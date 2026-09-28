"""Conditional inverses for owned changes; never overwrite subsequent user edits."""
from __future__ import annotations

import hashlib
import json
import threading

from jarvix.capabilities.schema import ID, register
from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReversibleActionService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()

    def prepare(self, tool, arguments):
        if tool == "apps.configure":
            existing = self.s.db.query("SELECT data FROM records WHERE kind='app.meta' AND id=?", (arguments["id"],))
            data = json.loads(existing[0]["data"]) if existing else {}
            keys = [key for key in ("favorite", "aliases") if key in arguments]
            return {"kind": "favorite", "keys": keys,
                    "before": {key: data[key] for key in keys if key in data}}
        if tool == "workspaces.save":
            return {"kind": "workspace", "before": self.s.records.get("workspace", arguments["id"])
                    if arguments.get("id") else None}
        if tool in {"notes.create", "tasks.create"}:
            return {"kind": "note" if tool == "notes.create" else "task", "before": None}
        return None

    def record(self, tool, arguments, result, prepared):
        if not result.ok or not isinstance(result.data, dict):
            return None
        if result.data.get("undo_available") and result.data.get("operation_id"):
            return "file:" + result.data["operation_id"]
        if prepared is None or not result.data.get("id"):
            return None
        record_id = result.data["id"]
        kind = prepared["kind"]
        after = self._value(kind, record_id, prepared.get("keys", []))
        return self.s.records.put("reversible_action", {**prepared, "target_id": record_id,
            "tool": tool, "fingerprint": fingerprint(after), "undone": False})

    def _value(self, kind, record_id, keys=()):
        if kind in {"note", "task"}:
            table = "notes" if kind == "note" else "tasks"
            rows = self.s.db.query(f"SELECT * FROM {table} WHERE id=?", (record_id,))
            if not rows:
                raise ValueError("Created record is no longer available.")
            extras = self.s.db.query("SELECT kind,data FROM records WHERE id=? ORDER BY kind", (record_id,))
            return {"row": rows[0], "metadata": extras}
        if kind == "workspace":
            return self.s.records.get("workspace", record_id)
        if kind == "favorite":
            rows = self.s.db.query("SELECT data FROM records WHERE kind='app.meta' AND id=?", (record_id,))
            value = json.loads(rows[0]["data"]) if rows else {}
            return {key: value[key] for key in keys if key in value}
        raise ValueError("Unsupported inverse.")

    def undo(self, id):
        if id.startswith("file:"):
            return self.s.execute_tool("files.undo", {"operation_id": id[5:]})
        with self._lock, self.s.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            entries = conn.execute("SELECT data FROM records WHERE kind='reversible_action' AND id=?", (id,)).fetchall()
            if not entries:
                raise ValueError("Unknown undo action.")
            item = json.loads(entries[0]["data"])
            if item["undone"]:
                raise ValueError("This action has already been undone.")
            check_cancelled()
            value = self._value(item["kind"], item["target_id"], item.get("keys", []))
            if fingerprint(value) != item["fingerprint"]:
                return ToolResult(False, error="This item changed after the action. Undo would affect later edits.")
            target = item["target_id"]
            if item["kind"] in {"note", "task"}:
                table = "notes" if item["kind"] == "note" else "tasks"
                # Metadata references from other objects mean this is no longer an isolated creation.
                links = conn.execute("SELECT kind,id,data FROM records WHERE kind != 'reversible_action'").fetchall()
                if any(target in row["data"] and row["id"] != target for row in links):
                    return ToolResult(False, error="Other local items now reference this record. Undo is unavailable.")
                conn.execute(f"DELETE FROM {table} WHERE id=?", (target,))
            elif item["kind"] == "workspace":
                if item["before"] is None:
                    conn.execute("DELETE FROM records WHERE kind='workspace' AND id=?", (target,))
                else:
                    previous = {k: v for k, v in item["before"].items() if k not in {"id", "created_at", "updated_at"}}
                    conn.execute("UPDATE records SET data=?,updated_at=? WHERE kind='workspace' AND id=?",
                                 (json.dumps(previous), item["before"]["updated_at"], target))
            elif item["kind"] == "favorite":
                row = conn.execute("SELECT data FROM records WHERE kind='app.meta' AND id=?", (target,)).fetchone()
                current = json.loads(row["data"]) if row else {}
                for key in item["keys"]:
                    current.pop(key, None)
                current.update(item["before"])
                conn.execute("UPDATE records SET data=? WHERE kind='app.meta' AND id=?", (json.dumps(current), target))
            item["undone"] = True
            conn.execute("UPDATE records SET data=? WHERE kind='reversible_action' AND id=?", (json.dumps(item), id))
        self.s.repository.audit("undo", "Unchanged owned action reversed")
        return {"undone": True, "id": id}

    def list(self):
        return [{"id": item["id"], "tool": item["tool"], "undone": item["undone"],
                 "created_at": item["created_at"]} for item in self.s.records.list("reversible_action")[:100]]


def setup(s, registry):
    s.undo = service = ReversibleActionService(s)
    register(registry, "actions.undo", "Undo an owned file/note/task/favorite/workspace change only if unchanged.",
             {"id": ID}, ("id",), service.undo, 2, "computer.control")
    register(registry, "actions.undo_history", "List reversible local actions without their private values.",
             {}, (), service.list)

