"""Small transactional repositories; each operation owns its SQLite connection."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


MAX_SETTING_BYTES = 256 * 1024
SENSITIVE_SETTING_PARTS = ("secret", "api_key", "token", "password", "credential", "private_key")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    due_at TEXT, reminded_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX tasks_due_reminders ON tasks(due_at)
    WHERE status='open' AND due_at IS NOT NULL AND reminded_at IS NULL;
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE apps (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX conversations_recent ON conversations(updated_at DESC, id DESC);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL
    REFERENCES conversations(id) ON DELETE CASCADE, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX messages_conversation ON messages(conversation_id, id);
CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE grants (tool_name TEXT PRIMARY KEY, decision TEXT NOT NULL CHECK(decision IN ('allow','deny')), updated_at TEXT NOT NULL);
CREATE TABLE files (path TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL, modified_at TEXT NOT NULL, size INTEGER NOT NULL);
CREATE INDEX files_name ON files(name);
CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT NOT NULL, tool_name TEXT NOT NULL, arguments TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, interval_minutes INTEGER NOT NULL, last_run_at TEXT, created_at TEXT NOT NULL);
PRAGMA user_version = 1;
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("This data directory requires a newer Jarvix version.")
            if version == 0:
                conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            if version < 2:
                conn.executescript("""BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS records (id TEXT NOT NULL, kind TEXT NOT NULL,
                        data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        PRIMARY KEY(id,kind));
                    CREATE INDEX IF NOT EXISTS records_kind ON records(kind,updated_at);
                    PRAGMA user_version = 2;
                    COMMIT;""")
            if version < 3:
                conn.executescript("""BEGIN IMMEDIATE;
                    CREATE INDEX IF NOT EXISTS tasks_due_reminders ON tasks(due_at)
                        WHERE status='open' AND due_at IS NOT NULL AND reminded_at IS NULL;
                    CREATE INDEX IF NOT EXISTS conversations_recent ON conversations(updated_at DESC, id DESC);
                    PRAGMA user_version = 3;
                    COMMIT;""")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass  # Windows permissions are inherited from the user's app-data directory.

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, args)

    def backup(self, target: Path) -> None:
        """Write and verify a consistent SQLite snapshot without pausing the application."""
        target = Path(target)
        if target.exists() or not target.parent.is_dir():
            raise ValueError("Backup destination is unavailable.")
        try:
            # Reserve the path first so a concurrent backup can never overwrite
            # an existing user file. SQLite then writes through its backup API.
            with target.open("xb"):
                pass
            with self.connect() as source:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                    integrity = destination.execute("PRAGMA integrity_check").fetchone()
                    if integrity is None or integrity[0] != "ok":
                        raise RuntimeError("Backup integrity verification failed.")
                finally:
                    destination.close()
        except BaseException:
            # The target was reserved exclusively by this method, so removing a
            # failed partial snapshot cannot affect a pre-existing user file.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        rows = self.db.query("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError))
        except (TypeError, ValueError, json.JSONDecodeError):
            # SQLite writes are atomic, but a hand-edited or legacy malformed
            # setting must not prevent the rest of the local workspace opening.
            return default

    def set(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key or any(word in key.lower() for word in SENSITIVE_SETTING_PARTS):
            raise ValueError("Credentials must be stored in the OS credential vault.")
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Settings must contain bounded JSON-compatible values.") from exc
        if len(encoded.encode("utf-8")) > MAX_SETTING_BYTES:
            raise ValueError("Setting exceeds the local size limit.")
        self.db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, encoded))


class Repository:
    """Only explicit application-owned tables can be addressed through this repository."""
    _tables = {"notes", "memories", "tasks", "projects", "apps", "automations", "conversations"}

    def __init__(self, db: Database):
        self.db = db

    def list(self, table: str) -> list[dict]:
        if table not in self._tables:
            raise ValueError("Unsupported record type")
        order = "updated_at" if table in {"notes", "conversations"} else "created_at"
        return self.db.query(f"SELECT * FROM {table} ORDER BY {order} DESC, id DESC LIMIT 1000")

    def delete(self, table: str, record_id: str) -> None:
        if table not in self._tables:
            raise ValueError("Unsupported record type")
        self.db.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))

    def audit(self, kind: str, summary: str) -> None:
        # Only caller-supplied fixed summaries belong here; never provider payloads or tool arguments.
        self.db.execute("INSERT INTO activity(kind,summary,created_at) VALUES (?,?,?)",
                        (kind[:64], summary[:500], now_iso()))

    def append_message(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Only user-visible messages are persisted")
        with self.db.connect() as conn:
            conn.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES (?,?,?,?)",
                         (conversation_id, role, content, now_iso()))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_iso(), conversation_id))

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex
