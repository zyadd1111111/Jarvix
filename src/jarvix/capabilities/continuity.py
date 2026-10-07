"""Explicit cross-session checkpoints; continuations observe before changing state."""
from __future__ import annotations

import copy
import json
import threading

from jarvix.capabilities.schema import ID, array, integer, register, schema, string
from jarvix.capabilities.context_graph import mission_references
from jarvix.capabilities.productivity import timestamp
from jarvix.domain import ToolResult
from jarvix.runtime import CURRENT, check_cancelled
from jarvix.services import required_text
from jarvix.storage import now_iso

LINKS = {"project_id": "project", "mission_id": "mission", "conversation_id": "conversation",
         "workspace_id": "workspace", "operator_session_id": "operator_session", "task_id": "task",
         "note_id": "note", "knowledge_space_id": "knowledge_space", "app_id": "app"}
NEXT_ACTION = schema({"description": string(500), "tool": string(100), "arguments": {"type": "object"}},
                     ("description",))


class ContinuityService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()

    def _permitted(self, name):
        return name in self.s.enabled_tools() and not self.s.db.query(
            "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,))

    def _read(self, name, arguments):
        check_cancelled()
        if not self._permitted(name):
            return ToolResult(False, {"failure_class": "permission_denied"}, "A continuation source is disabled or denied.")
        return self.s.execute_tool(name, arguments)

    def _references(self, value):
        for field, kind in LINKS.items():
            if value.get(field):
                yield {"kind": kind, "reference": value[field]}
        for path in value.get("files", []):
            yield {"kind": "file", "reference": path}

    def _reference(self, reference):
        if not self._permitted("context.graph"):
            raise PermissionError("Context references are disabled or denied.")
        item = self.s.context_graph.resolve(**reference)
        # A Space may remain inspectable while its contents are unavailable.
        # A continuation must not reuse its saved summary in that state.
        if item.get("unavailable_sources"):
            raise ValueError("A linked Knowledge Space source is unavailable.")
        return item

    def save(self, summary, id=None, files=None, next_action=None, timeout_seconds=120, **links):
        if set(links) - set(LINKS) or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError("Choose supported checkpoint links and a deadline of 1–300 seconds.")
        if not self._permitted("context.graph") or not self._permitted("continuity.get"):
            raise PermissionError("Context references or saved checkpoint access are disabled or denied.")
        with self._lock:
            value = self.s.records.get("continuity_checkpoint", id) if id else {}
            value.update(summary=required_text(summary, "Checkpoint summary", 3000), version=1,
                         timeout_seconds=timeout_seconds)
            for field, reference in links.items():
                if reference is not None and not isinstance(reference, str):
                    raise ValueError("Checkpoint references must contain an existing ID.")
                value[field] = self.s.context_graph.resolve(LINKS[field], reference)["reference"] if reference else None
            if files is not None:
                if not isinstance(files, list) or len(files) > 20:
                    raise ValueError("Link at most 20 existing files.")
                value["files"] = list(dict.fromkeys(self.s.context_graph.resolve("file", path)["reference"] for path in files))
            value.setdefault("files", [])
            if not list(self._references(value)):
                raise ValueError("Link a checkpoint to existing work before saving it.")
            # Revalidate unchanged references too; editing never restores revoked access.
            references = list(self._references(value))
            for reference in references:
                check_cancelled()
                self._reference(reference)
                if reference["kind"] == "mission":
                    references.extend(mission_references(self.s.records.get("mission", reference["reference"])))
            if next_action is not None:
                if (not isinstance(next_action, dict) or set(next_action) - {"description", "tool", "arguments"}
                        or "description" not in next_action):
                    raise ValueError("Use bounded next-action metadata with a description and optional registered tool.")
                action = copy.deepcopy(next_action)
                action["description"] = required_text(action["description"], "Next action", 500)
                if "tool" in action:
                    action["tool"] = required_text(action["tool"], "Tool", 100)
                    if not self._permitted(action["tool"]) or self.s.registry.validate(action["tool"], action.get("arguments", {})):
                        raise ValueError("Next-action metadata must name an enabled tool with valid concrete arguments.")
                elif "arguments" in action:
                    raise ValueError("Arguments require a registered tool.")
                try:
                    if len(json.dumps(action, allow_nan=False).encode("utf-8")) > 4000:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("Next-action metadata exceeds its bounded JSON size.") from None
                value["next_action"] = action
            value.setdefault("next_action", None)
            id = self.s.records.put("continuity_checkpoint", value, id)
        return self.get(id)

    def get(self, id):
        if not self._permitted("continuity.get"):
            return {"id": id, "summary": None, "next_action": None, "references": [], "available": False,
                    "status": "permission_denied", "actions_replayed": False,
                    "automatic_cloud_sharing": False, "startup_execution": False}
        value = self.s.records.get("continuity_checkpoint", id)
        references = []
        pending, seen = list(self._references(value)), set()
        for reference in pending:
            identity = (reference["kind"], reference["reference"])
            if identity in seen:
                continue
            seen.add(identity)
            check_cancelled()
            try:
                item = self._reference(reference)
                if reference["kind"] == "mission":
                    pending.extend(mission_references(self.s.records.get("mission", reference["reference"])))
            except PermissionError:
                item = {**reference, "available": False, "failure_class": "permission_denied"}
            except (ValueError, OSError):
                item = {**reference, "available": False, "failure_class": "source_unavailable"}
            references.append(item)
        available = bool(references) and all(item["available"] for item in references)
        failure = "permission_denied" if any(item.get("failure_class") == "permission_denied" for item in references) else "source_unavailable"
        if not available:
            value.update(summary=None, next_action=None)
        verified, unfinished, blockers = [], [], []
        for item in references:
            if not item["available"]:
                blockers.append({"kind": item["kind"], "reference": item["reference"],
                                 "reason": item.get("failure_class", "source_unavailable")})
            elif item["kind"] == "operator_session":
                session = self.s.operator.get(item["reference"])
                verified.extend({"session_id": session["id"], "step_id": step["id"], "tool": step["tool"]}
                    for step in session.get("steps", []) if step.get("status") == "complete"
                    and step.get("verified") and not step.get("undone"))
                unfinished.extend({"kind": "operator_step", "session_id": session["id"], "step_id": step["id"],
                    "status": step["status"], "undone": bool(step.get("undone")), "uncertain": step.get("retry_safe") is False}
                    for step in session.get("steps", []) if step.get("status") != "complete" or not step.get("verified") or step.get("undone"))
            elif item["kind"] == "task" and item.get("status") != "done":
                unfinished.append({"kind": "task", "id": item["reference"], "status": item.get("status")})
        return {**value, "references": references, "available": available, "status": "available" if available else failure,
                "last_verified_state": verified[:100], "unfinished_work": unfinished[:100], "blockers": blockers[:100],
                "recent_resources": references[:20], "actions_replayed": False,
                "automatic_cloud_sharing": False, "startup_execution": False}

    def list(self, project_id=None, limit=20, since=None, until=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("List at most 100 continuity checkpoints.")
        start, end = timestamp(since) if since else None, timestamp(until) if until else None
        if start and end and start >= end:
            raise ValueError("Use an increasing checkpoint date range.")
        rows = [row for row in self.s.records.list("continuity_checkpoint")
                if (project_id is None or row.get("project_id") == project_id)
                and (not start or timestamp(row["updated_at"]) >= start) and (not end or timestamp(row["updated_at"]) < end)]
        values = [self.get(row["id"]) for row in rows[:limit]]
        keys = ("id", "summary", "status", "available", "project_id", "mission_id", "updated_at")
        return {"items": [{key: value.get(key) for key in keys} for value in values[:limit]],
                "truncated": len(rows) > limit, "startup_execution": False}

    def forget(self, id):
        with self._lock:
            self.s.records.get("continuity_checkpoint", id)
            self.s.records.delete("continuity_checkpoint", id)
        return {"forgotten": True, "linked_work_preserved": True}

    def prepare(self, id=None):
        check_cancelled()
        if id is None:
            items = self.s.records.list("continuity_checkpoint")
            if not items:
                return ToolResult(False, {"failure_class": "source_unavailable"}, "No work checkpoint has been saved.")
            id = items[0]["id"]
        checkpoint = self.get(id)
        if not checkpoint["available"]:
            return ToolResult(False, checkpoint, "A saved work source is unavailable or its access was revoked.")
        result = {"checkpoint": checkpoint, "prepared": False, "executed": False, "actions_replayed": False,
                  "cloud_request": False, "plan": None, "preview": None, "observation_plan": None,
                  "recovery_preview": None, "source_session_id": None}
        project_id = checkpoint.get("project_id")
        mission_id = checkpoint.get("mission_id")
        if mission_id and not project_id:
            mission = self._read("missions.summary", {"id": mission_id})
            if not mission.ok:
                return mission
            project_id = mission.data.get("project_id")
        if project_id:
            prepared = self._read("intelligence.prepare", {"project_id": project_id,
                **({"mission_id": mission_id} if mission_id else {}), "goal": "Resume saved work: " + checkpoint["summary"][:475]})
            if not prepared.ok:
                return prepared
            result.update(plan=prepared.data["plan"], preview=prepared.data["preview"], project=prepared.data["project"])
        session_id = checkpoint.get("operator_session_id")
        session = self.s.operator.get(session_id) if session_id else None
        if session:
            result["operator_session"] = {"id": session_id, "status": session["status"],
                "uncertain_step_ids": [step["id"] for step in session.get("steps", [])
                                       if step.get("retry_safe") is False and step["status"] != "complete"],
                "uncertain_actions_resolved": False}
        steps = []
        def observe(name, arguments):
            if not self._permitted(name) or name not in self.s.UNATTENDED_TOOL_ALLOWLIST:
                return
            spec = self.s.registry.get(name)
            if (spec.risk == "read" and spec.permission_level == 1 and not self.s.registry.validate(name, arguments)
                    and not any(step["tool"] == name and step["arguments"] == arguments for step in steps)):
                steps.append({"id": f"observe{len(steps) + 1}", "tool": name, "arguments": arguments, "depends_on": []})
        action = checkpoint.get("next_action") or {}
        if action.get("tool"):
            observe(action["tool"], action.get("arguments", {}))
        if project_id:
            observe("projects.list", {})
        if mission_id or project_id:
            observe("tasks.list", {})
        for step in (session or {}).get("steps", [])[:24]:
            observe(step["tool"], {})
        if steps:
            plan = {"goal": "Observe saved work before continuing", "steps": steps[:4],
                    "timeout_seconds": checkpoint["timeout_seconds"], "max_parallel_reads": 2}
            self.s.operator.background_safe(plan)
            preview = self._read("operator.preview", {"plan": plan})
            if not preview.ok:
                return preview
            result.update(observation_plan=plan, observation_preview=preview.data)
            if session and session.get("checkpoint_available") and session["status"] in {"failed", "cancelled", "timed_out", "denied", "interrupted"} and not session.get("retry_session_id"):
                recovery = self._read("operator.preview_replan", {"id": session_id, "plan": plan})
                if not recovery.ok:
                    return recovery
                result.update(recovery_preview=recovery.data, source_session_id=session_id)
        result["prepared"] = result["plan"] is not None or result["observation_plan"] is not None
        if not result["prepared"]:
            return ToolResult(False, {**result, "failure_class": "no_safe_continuation"},
                              "Inspect the saved next action; no permitted continuation observations are available.")
        return result

    def start_observations(self, id=None, approve=None, on_event=None):
        check_cancelled()
        current = CURRENT.get()
        if current and current.unattended:
            raise PermissionError("Unattended work cannot start independent continuations.")
        prepared = self.prepare(id)
        if isinstance(prepared, ToolResult):
            return prepared
        if prepared["observation_plan"] is None:
            return ToolResult(False, {"failure_class": "no_safe_continuation"}, "The available reads require foreground control.")
        for name in ("execution.start", "operator.preview", *(["operator.preview_replan"] if prepared["source_session_id"] else [])):
            if not self._permitted(name):
                return ToolResult(False, {"failure_class": "permission_denied"}, "Continuation task preview is disabled or denied.")
        approval = approve or (current.approve if current else lambda _: False)
        invalidated = {}
        def review(request):
            if not approval(request):
                return False
            try:
                fresh = self.get(prepared["checkpoint"]["id"])
                if not fresh["available"]:
                    invalidated["failure_class"] = fresh["status"]
                elif fresh["updated_at"] != prepared["checkpoint"]["updated_at"]:
                    invalidated["failure_class"] = "checkpoint_changed"
            except ValueError:
                invalidated["failure_class"] = "source_unavailable"
            return not invalidated
        started = self.s.supervisor.start(prepared["observation_plan"], background=True, approve=review,
                                         on_event=on_event, _source_session_id=prepared["source_session_id"])
        if started["accepted"]:
            try:
                with self._lock:
                    value = self.s.records.get("continuity_checkpoint", prepared["checkpoint"]["id"])
                    value.update(last_observation_session_id=started.get("id"), last_observed_at=now_iso())
                    self.s.records.put("continuity_checkpoint", value, value["id"])
            except Exception:
                started["checkpoint_update"] = "unavailable"  # Keep the started task's receipt; never launch it again.
        return {**started, **invalidated, "checkpoint_id": prepared["checkpoint"]["id"], "source_session_id": prepared["source_session_id"],
                "actions_replayed": False, "read_only": True, "deadline_seconds": prepared["checkpoint"]["timeout_seconds"],
                "cancel_tool": "operator.cancel", "cancel_id": started.get("id"), "owned_cancellation": True,
                "uncertain_actions_resolved": False,
                "uncertain_step_ids": prepared.get("operator_session", {}).get("uncertain_step_ids", [])}


def setup(s, registry):
    s.continuity = service = ContinuityService(s)
    register(registry, "continuity.save", "Explicitly save a bounded work summary, linked sources and next-action metadata. Saved actions are never replayed on restart.",
             {"summary": string(3000), "id": ID, "files": array(string(4096), 20), "next_action": NEXT_ACTION,
              "timeout_seconds": integer(1, 300), **{field: string(160, 0) for field in LINKS}}, ("summary",), service.save, 2, "continuity.write")
    register(registry, "continuity.list", "List explicit work checkpoints; revoked or missing sources hide their saved summaries.",
             {"project_id": ID, "limit": integer(1, 100), "since": string(40), "until": string(40)}, (), service.list)
    register(registry, "continuity.get", "Inspect saved work with current source availability and permission checks; never executes its next action.",
             {"id": ID}, ("id",), service.get)
    register(registry, "continuity.forget", "Forget one saved checkpoint while preserving its linked project, Mission, files and sessions.",
             {"id": ID}, ("id",), service.forget, 3, "continuity.write")
    register(registry, "continuity.prepare", "Revalidate a saved checkpoint (latest by default), preview existing project observations and safe Operator recovery; never replay writes.",
             {"id": ID}, (), service.prepare)
    register(registry, "continuity.start_observations", "Review and start bounded, host-approved read-only observations for saved work, with owned cancellation. Sensitive actions still need normal foreground control.",
             {"id": ID}, (), service.start_observations, risk="write")
