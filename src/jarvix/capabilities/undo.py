"""Conditional inverses for owned changes; never overwrite subsequent user edits."""
from __future__ import annotations

import hashlib
import json
import threading

from jarvix.capabilities.schema import ID, array, register
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
        if tool in {"workflows.save", "routines.save", "workflows.toggle"}:
            return {"kind": "workflow", "before": self.s.records.get("workflow", arguments["id"])
                    if arguments.get("id") else None}
        if tool in {"workflows.duplicate", "workflows.import"}:
            return {"kind": "workflow", "before": None}
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
        if kind in {"workspace", "workflow"}:
            return self.s.records.get(kind, record_id)
        if kind == "favorite":
            rows = self.s.db.query("SELECT data FROM records WHERE kind='app.meta' AND id=?", (record_id,))
            value = json.loads(rows[0]["data"]) if rows else {}
            return {key: value[key] for key in keys if key in value}
        raise ValueError("Unsupported inverse.")

    def undo(self, id):
        if id.startswith("file:"):
            return self.s.execute_tool("files.undo", {"operation_id": id[5:]})
        receipt = self.s.records.get("reversible_action", id)
        if receipt["kind"] == "workflow":
            return self._undo_workflow(id)
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

    def _undo_workflow(self, id):
        # Restore through the existing level-3 workflow tools, never by writing
        # an enabled rule or its approval fingerprint directly into storage.
        with self._lock, self.s.workflows._lock:
            item = self.s.records.get("reversible_action", id)
            current = self._value("workflow", item["target_id"])
            if item["undone"] or fingerprint(current) != item["fingerprint"]:
                return ToolResult(False, error="The automation changed or ran since this action. Undo is unavailable.")
            if any(run["workflow_id"] == item["target_id"] for run in self.s.workflows._active.values()):
                return ToolResult(False, error="Cancel the automation's active run before undoing its configuration.")
            if item["before"] is None:
                result = self.s.execute_tool("workflows.delete", {"id": item["target_id"]})
            else:
                from jarvix.capabilities.workflows import _definition_values
                previous = _definition_values(item["before"])
                result = self.s.execute_tool("workflows.save", {**previous, "id": item["target_id"]})
            if not result.ok:
                return result
            self.s.records.put("reversible_action", {**item, "undone": True}, id)
            self.s.repository.audit("undo", "Approved automation configuration restored")
            return {"undone": True, "id": id}

    def preview(self, id):
        """Recheck actual inverse preconditions. Availability is never a promise."""
        try:
            if id.startswith("file:"):
                item = self.s.records.get("file_operation", id[5:])
                destination = self.s.files.path(item["destination"], mutate=True)
                available = not item["undone"] and self.s.files._fingerprint(destination) == item["fingerprint"]
                if item["action"] == "move":
                    source = self.s.files.path(item["source"], existing=False, mutate=True)
                    available = available and not source.exists() and source.parent.is_dir()
                return {"id": id, "available": available, "action": item["action"],
                        "original_path": item.get("source"), "current_path": item["destination"],
                        "requires_confirmation": True, "rechecked_at_execution": True}
            item = self.s.records.get("reversible_action", id)
            available = not item["undone"] and fingerprint(self._value(
                item["kind"], item["target_id"], item.get("keys", []))) == item["fingerprint"]
            if available and item["kind"] in {"note", "task"}:
                links = self.s.db.query("SELECT id,data FROM records WHERE kind != 'reversible_action'")
                available = not any(item["target_id"] in row["data"] and row["id"] != item["target_id"] for row in links)
            if available and item["kind"] == "workflow":
                with self.s.workflows._lock:
                    available = not any(run["workflow_id"] == item["target_id"] for run in self.s.workflows._active.values())
            return {"id": id, "tool": item["tool"], "available": available,
                    "requires_confirmation": item["kind"] == "workflow", "rechecked_at_execution": True}
        except (ValueError, OSError):
            return {"id": id, "available": False, "reason": "Target changed, unavailable, or no longer permitted."}

    def rollback(self, ids):
        """Reverse explicit receipts newest-first; report partial work honestly."""
        if not 1 <= len(ids) <= 24 or len(set(ids)) != len(ids):
            raise ValueError("Choose 1–24 unique receipts in original execution order.")
        results = []
        for id in reversed(ids):
            try:
                check_cancelled()
            except InterruptedError:
                break
            result = self.s.execute_tool("actions.undo", {"id": id})
            results.append({"id": id, "ok": result.ok, "error": result.error})
            if not result.ok:
                break
        complete = len(results) == len(ids) and all(row["ok"] for row in results)
        return ToolResult(complete, {"complete": complete, "results": results,
            "remaining": [id for id in reversed(ids) if id not in {r["id"] for r in results if r["ok"]}],
            "atomic": False}, None if complete else "Rollback stopped; later edits or permissions prevented an inverse.")

    def list(self):
        return [{"id": item["id"], "tool": item["tool"], "undone": item["undone"],
                 "created_at": item["created_at"]} for item in self.s.records.list("reversible_action")[:100]]


def setup(s, registry):
    s.undo = service = ReversibleActionService(s)
    register(registry, "actions.undo", "Undo an owned file/note/task/favorite/workspace change only if unchanged.",
             {"id": ID}, ("id",), service.undo, 2, "computer.control")
    register(registry, "actions.undo_history", "List reversible local actions without their private values.",
             {}, (), service.list)
    register(registry, "actions.undo_preview", "Recheck whether a recorded inverse is currently available; execution rechecks later edits.",
             {"id": ID}, ("id",), service.preview)
    register(registry, "actions.rollback", "Reverse explicitly selected receipts in reverse execution order. Stops on failure; each inverse retains permissions. Not atomic.",
             {"ids": {**array(ID, 24), "minItems": 1, "uniqueItems": True}}, ("ids",), service.rollback, 3, "computer.control")
