"""Optional current-user DPAPI field protection, including transactional migration."""
from __future__ import annotations

import base64
import json
import re
import sqlite3

from jarvix.runtime import check_cancelled


PREFIX = "jvx-dpapi-v1:"
PLAIN_PREFIX = "jvx-text-v1:"
FIELDS = {"notes": {"title", "body"}, "memories": {"content"},
          "messages": {"content", "attachments"}, "conversations": {"title"},
          "integrations": {"account"}, "records": {"data"}}
COLUMNS = {"notes": ["id", "title", "body", "created_at", "updated_at"],
           "memories": ["id", "content", "created_at"],
           "conversations": ["id", "title", "created_at", "updated_at"],
           "records": ["id", "kind", "data", "created_at", "updated_at"]}


class ProtectedConnection(sqlite3.Connection):
    """Protect bound writes before SQLite receives plaintext, including owned transactions."""

    def initialize(self, codec):
        self.codec = codec
        settings = dict(super().execute("SELECT key,value FROM settings WHERE key LIKE 'storage.%'").fetchall())
        try:
            policy = json.loads(settings.get("storage.encryption_enabled", "false"))
            notes = json.loads(settings.get("storage.protected_notes", "null"))
            if type(policy) is not bool or (notes is not None and (not isinstance(notes, list)
                    or any(not isinstance(id, str) for id in notes))):
                raise ValueError
        except ValueError as exc:
            raise RuntimeError("Storage protection policy is damaged. Restore a compatible backup.") from exc
        self.protection_enabled, self.protected_notes = policy, notes
        self.create_function("jarvix_plain", 1, self.decode)
        self.row_factory = lambda cursor, values: sqlite3.Row(cursor, tuple(self.decode(v) for v in values))

    def encode(self, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Protected fields must contain text.")
        return PREFIX + base64.b64encode(self.codec(value.encode("utf-8"))).decode("ascii")

    def decode(self, value):
        if isinstance(value, str) and value.startswith(PLAIN_PREFIX):
            return value[len(PLAIN_PREFIX):]
        if isinstance(value, str) and value.startswith(PREFIX):
            try:
                return self.codec(base64.b64decode(value[len(PREFIX):], validate=True), decrypt=True).decode("utf-8")
            except Exception as exc:
                raise RuntimeError("Protected data cannot be opened by this Windows account. Restore a compatible backup.") from exc
        return value

    @staticmethod
    def escape(value):
        return PLAIN_PREFIX + value if isinstance(value, str) and value.startswith((PREFIX, PLAIN_PREFIX)) else value

    def _parameters(self, sql, parameters):
        if hasattr(self, "codec") and not self.in_transaction and re.match(
                r"\s*(?:INSERT|UPDATE|REPLACE|DELETE)\b", sql, re.I):
            # Reload policy under the writer lock: a connection opened before a
            # migration must never write using the previous plaintext policy.
            super().execute("BEGIN IMMEDIATE")
            self.initialize(self.codec)
        if not getattr(self, "protection_enabled", False):
            return tuple(self.escape(value) for value in parameters)
        if not isinstance(parameters, (list, tuple)):
            raise ValueError("Protected database writes require positional parameters.")
        values = list(parameters)
        insert = re.match(r"\s*INSERT(?:\s+OR\s+\w+)?\s+INTO\s+(\w+)\s*(?:\(([^)]+)\))?\s*VALUES\s*\(([^)]+)\)", sql, re.I)
        update = re.match(r"\s*UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)", sql, re.I | re.S)
        edits = []
        table = (insert or update).group(1).lower() if insert or update else ""
        if not table and re.match(r"\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE)\s+(?:"
                                 + "|".join(FIELDS) + r")\b", sql, re.I):
            raise ValueError("Unsupported protected write shape.")
        if table not in FIELDS:
            return tuple(self.escape(value) for value in parameters)
        if insert:
            columns = [v.strip().lower() for v in insert.group(2).split(",")] if insert.group(2) else COLUMNS.get(table, [])
            expressions = [v.strip() for v in insert.group(3).split(",")]
            if len(columns) != len(expressions):
                raise ValueError("Unsupported protected insert shape.")
            index = 0
            identity = None
            for column, expression in zip(columns, expressions, strict=True):
                if column == "id" and expression == "?":
                    identity = values[index]
                if column in FIELDS[table]:
                    if expression != "?":
                        raise ValueError("Protected values must use bound parameters.")
                    edits.append(index)
                index += expression.count("?")
        else:
            identity_match = re.search(r"\bid\s*=\s*\?", update.group(3), re.I)
            identity = values[sql[:update.start(3) + identity_match.start()].count("?")] if identity_match else None
            index = 0
            for assignment in update.group(2).split(","):
                column, expression = (part.strip() for part in assignment.split("=", 1))
                if column.lower() in FIELDS[table]:
                    if expression != "?":
                        raise ValueError("Protected values must use bound parameters.")
                    edits.append(index)
                index += expression.count("?")
        if table == "notes" and self.protected_notes is not None and identity not in self.protected_notes:
            if identity is None and edits:
                raise ValueError("Selected protected notes require an explicit note ID.")
            return tuple(self.escape(value) for value in parameters)
        for index in edits:
            values[index] = self.encode(values[index])
        return tuple(value if index in edits else self.escape(value) for index, value in enumerate(values))

    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, self._parameters(sql, parameters))
        if hasattr(self, "codec") and re.match(r"\s*BEGIN\b", sql, re.I):
            self.initialize(self.codec)
        return cursor

    def executemany(self, sql, seq_of_parameters):
        return super().executemany(sql, (self._parameters(sql, values) for values in seq_of_parameters))

    def cursor(self, factory=None):
        return super().cursor(factory or ProtectedCursor)

    def executescript(self, sql_script):
        if getattr(self, "protection_enabled", False) and re.search(
                r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE)\s+(?:"
                + "|".join(FIELDS) + r")\b", sql_script, re.I):
            raise ValueError("Protected writes require bound parameters, not scripts.")
        return super().executescript(sql_script)


class ProtectedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, self.connection._parameters(sql, parameters))
        if hasattr(self.connection, "codec") and re.match(r"\s*BEGIN\b", sql, re.I):
            self.connection.initialize(self.connection.codec)
        return cursor

    def executemany(self, sql, seq_of_parameters):
        return super().executemany(sql, (self.connection._parameters(sql, values) for values in seq_of_parameters))

    def executescript(self, sql_script):
        self.connection.executescript(sql_script)
        return self


class DataProtectionService:
    def __init__(self, services):
        self.s = services

    def status(self):
        return {"enabled": self.s.settings.get("storage.encryption_enabled", False),
                "protected_notes": self.s.settings.get("storage.protected_notes"),
                "method": "Windows current-user DPAPI", "database_encrypted": False,
                "scope": "Memory, conversation content/titles, account metadata, selected notes and extension records.",
                "backups": "Existing backups remain unchanged; new backups preserve protected fields."}

    def configure(self, enabled, note_ids=None):
        if type(enabled) is not bool or (note_ids is not None and (not isinstance(note_ids, list)
                or any(not isinstance(value, str) for value in note_ids) or len(note_ids) > 1000)):
            raise ValueError("Choose protection and bounded note IDs.")
        if note_ids is not None and not set(note_ids) <= {row["id"] for row in self.s.list_notes()}:
            raise ValueError("Select existing note IDs.")
        if enabled:
            probe = self.s.db.codec(b"Jarvix storage check")
            if self.s.db.codec(probe, decrypt=True) != b"Jarvix storage check":
                raise RuntimeError("OS protection verification failed.")
        with self.s.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table, columns in FIELDS.items():
                check_cancelled()
                keys = ["id", "kind"] if table == "records" else ["id"]
                rows = conn.execute(f"SELECT {','.join(keys + sorted(columns))} FROM {table}").fetchall()
                for row in rows:
                    check_cancelled()
                    encrypt = enabled and (table != "notes" or note_ids is None or row["id"] in note_ids)
                    updates = {column: conn.encode(row[column]) if encrypt else conn.escape(row[column]) for column in columns}
                    # Migration bypasses bound-write protection exactly once: rows already decode on read.
                    sqlite3.Connection.execute(conn, f"UPDATE {table} SET " + ",".join(f"{c}=?" for c in updates)
                        + " WHERE " + " AND ".join(f"{key}=?" for key in keys),
                        (*updates.values(), *(row[key] for key in keys)))
            for key, value in (("storage.encryption_enabled", enabled), ("storage.protected_notes", note_ids)):
                conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, json.dumps(value)))
        # Remove obsolete free-page plaintext and checkpoint old WAL frames after the committed migration.
        with self.s.db.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        self.s.repository.audit("storage", "Local data protection policy changed")
        return self.status()


def setup(s, registry):
    from jarvix.capabilities.schema import BOOL, ID, array, register
    s.data_protection = DataProtectionService(s)
    register(registry, "storage.protection_status", "Inspect the local field encryption policy.", {}, (),
             s.data_protection.status)
    register(registry, "storage.configure_protection", "Migrate sensitive local data using Windows DPAPI. Existing backups retain their original policy; account-bound encrypted data needs this Windows profile.",
             {"enabled": BOOL, "note_ids": array(ID, 1000)}, ("enabled",), s.data_protection.configure, 3)
