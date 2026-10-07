"""Persistent goals link existing work; resuming never replays Operator actions."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from jarvix.capabilities.context_graph import MISSION_LINKS, mission_references
from jarvix.capabilities.schema import ID, array, enum, register, schema, string
from jarvix.services import required_text
from jarvix.storage import now_iso

STATUSES = ("active", "paused", "complete", "archived", "cancelled")
MILESTONE = schema({"id": string(80), "title": string(300), "status": enum("pending", "complete"),
                    "deadline": string(50, 0), "task_ids": array(ID, 50)}, ("id", "title"))


def deadline_value(value):
    if not value:
        return None
    value = required_text(value, "Deadline", 50)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Use an ISO deadline with an explicit timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


class MissionService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()

    @staticmethod
    def _history(mission, action, **details):
        history = mission.get("history", [])
        mission["history"] = [*history, {"at": now_iso(), "action": action, **details}][-200:]
        mission["history_count"] = mission.get("history_count", 0) + 1

    def save(self, goal, id=None, project_id=None, notes=None, blockers=None,
             milestones=None, deadline=None, next_steps=None, **links):
        with self._lock:
            current = self.s.records.get("mission", id) if id else {"status": "active"}
            if current["status"] in {"complete", "archived", "cancelled"}:
                raise ValueError("Resume completed or archived missions before editing; cancelled missions are final.")
            value = {**current, "goal": required_text(goal, "Mission goal", 2000)}
            if project_id is not None:
                value["project_id"] = project_id or None
            value.setdefault("project_id", None)
            if notes is not None:
                if not isinstance(notes, str) or len(notes) > 12000:
                    raise ValueError("Mission notes must contain at most 12,000 characters.")
                value["notes"] = notes
            value.setdefault("notes", "")
            if blockers is not None:
                if not isinstance(blockers, list) or len(blockers) > 30:
                    raise ValueError("Use at most 30 mission blockers.")
                value["blockers"] = list(dict.fromkeys(required_text(item, "Blocker", 500) for item in blockers))
            value.setdefault("blockers", [])
            if deadline is not None:
                value["deadline"] = deadline_value(deadline)
            value.setdefault("deadline", None)
            if next_steps is not None:
                if not isinstance(next_steps, list) or len(next_steps) > 20:
                    raise ValueError("Use at most 20 explicitly recorded next steps.")
                value["next_steps"] = list(dict.fromkeys(required_text(step, "Next step", 500) for step in next_steps))
            value.setdefault("next_steps", [])
            if milestones is not None:
                from jsonschema import Draft202012Validator
                if not isinstance(milestones, list) or len(milestones) > 30 or any(
                        not Draft202012Validator(MILESTONE).is_valid(item) for item in milestones):
                    raise ValueError("Use at most 30 structured milestones with unique IDs.")
                if len({item["id"] for item in milestones}) != len(milestones):
                    raise ValueError("Milestone IDs must be unique.")
                value["milestones"] = [{**item, "title": required_text(item["title"], "Milestone", 300),
                                        "status": item.get("status", "pending"),
                                        "deadline": deadline_value(item.get("deadline")),
                                        "task_ids": list(dict.fromkeys(item.get("task_ids", [])))} for item in milestones]
            value.setdefault("milestones", [])
            for milestone in value["milestones"]:
                tasks = [self.s.context_graph.resolve("task", task) for task in milestone["task_ids"]]
                if milestone["status"] == "complete" and any(task["status"] != "done" for task in tasks):
                    raise ValueError("Finish linked tasks before declaring their milestone complete.")
            if set(links) - set(MISSION_LINKS):
                raise ValueError("Unsupported mission link field.")
            for field, kind in MISSION_LINKS.items():
                incoming = links.get(field, current.get(field, []))
                if not isinstance(incoming, list) or len(incoming) > 100:
                    raise ValueError("Use at most 100 references per mission section.")
                value[field] = list(dict.fromkeys(self.s.context_graph.resolve(kind, item)["reference"] for item in incoming))
            if value["project_id"]:
                self.s.context_graph.resolve("project", value["project_id"])
            if sum(len(value[field]) for field in MISSION_LINKS) + sum(len(item["task_ids"]) for item in value["milestones"]) > 200:
                raise ValueError("A mission supports at most 200 linked references.")
            changed_fields = [key for key in value if key not in {"history", "history_count", "id", "created_at", "updated_at"}
                              and value.get(key) != current.get(key)]
            self._history(value, "updated" if id else "created", fields=changed_fields)
            id = self.s.records.put("mission", value, id)
        return self.get(id)

    def get(self, id):
        mission = self.s.records.get("mission", id)
        references = [self.s.context_graph.inspect_reference(**item) for item in mission_references(mission)]
        blockers = list(mission.get("blockers", []))
        tracked_tasks = {item["reference"] for item in mission_references(mission) if item["kind"] == "task"}
        manual_milestones = [item for item in mission.get("milestones", []) if not item.get("task_ids")]
        complete = sum(item.get("status") == "complete" for item in manual_milestones)
        total = len(tracked_tasks) + len(mission.get("operator_session_ids", [])) + len(manual_milestones)
        sessions = []
        next_details = [{"kind": "recorded", "title": step, "reason": "Explicitly recorded in this mission."}
                        for step in mission.get("next_steps", [])]
        now = datetime.now(timezone.utc)
        for item in references:
            if not item["available"]:
                blockers.append(f"Linked {item['kind']} is unavailable: {item['reference']}")
            elif item.get("unavailable_sources"):
                blockers.append(f"Knowledge Space has {item['unavailable_sources']} unavailable source(s): {item['reference']}")
            elif item["kind"] == "task":
                complete += item["status"] == "done"
                if item["status"] != "done":
                    next_details.append({"kind": "task", "reference": item["reference"], "title": item["label"],
                                         "reason": "Linked task is unfinished."})
            elif item["kind"] == "operator_session":
                session = self.s.records.get("operator_session", item["reference"])
                steps = session.get("steps", [])
                uncertain = [step["id"] for step in steps if step.get("retry_safe") is False
                             and step.get("status") != "complete"]
                unverified = [step["id"] for step in steps if step.get("status") == "complete"
                              and (not step.get("verified") or step.get("undone"))]
                complete += session["status"] == "complete" and bool(steps) and all(
                    step.get("status") == "complete" and step.get("verified") and not step.get("undone") for step in steps)
                sessions.append({"id": session["id"], "status": session["status"], "uncertain_steps": uncertain,
                                 "unverified_steps": unverified, "resume_requires_new_plan": bool(uncertain or unverified)})
                if session["status"] in {"failed", "interrupted", "timed_out", "denied", "cancelled"} or uncertain or unverified:
                    blockers.append(f"Inspect unfinished or uncertain Operator work: {session['id']}")
                    next_details.append({"kind": "operator_session", "reference": session["id"],
                                         "title": "Review unfinished Operator work", "reason": "Resume requires inspecting uncertain outcomes; actions are never replayed."})
        milestones = []
        for milestone in mission.get("milestones", []):
            tasks = [self.s.context_graph.inspect_reference("task", task) for task in milestone.get("task_ids", [])]
            unavailable = [task["reference"] for task in tasks if not task["available"]]
            completed = (all(task.get("status") == "done" for task in tasks) if tasks
                         else milestone.get("status") == "complete")
            if unavailable:
                blockers.append(f"Milestone has unavailable task references: {milestone['id']}")
            due = milestone.get("deadline")
            milestones.append({**milestone, "status": "complete" if completed else "pending",
                               "progress": {"completed": sum(task.get("status") == "done" for task in tasks),
                                            "total": len(tasks)}, "unavailable_task_ids": unavailable,
                               "overdue": bool(due and not completed and datetime.fromisoformat(due) < now)})
            if not completed:
                next_details.append({"kind": "milestone", "reference": milestone["id"], "title": milestone["title"],
                                     "reason": "Milestone is unfinished.", "deadline": due})
        due = mission.get("deadline")
        return {**mission, "milestones": milestones, "references": references, "operator_sessions": sessions,
                "next_step_details": next_details[:50], "next_steps_truncated": len(next_details) > 50,
                "overdue": bool(due and mission["status"] == "active" and datetime.fromisoformat(due) < now),
                "progress": {"completed": complete, "total": total,
                             "percent": round(100 * complete / total) if total else (100 if mission["status"] == "complete" else 0),
                             "milestones_completed": sum(item["status"] == "complete" for item in milestones),
                             "milestones_total": len(milestones)},
                "current_blockers": list(dict.fromkeys(blockers)), "history_truncated": mission.get("history_count", 0) > 200,
                "actions_replayed": False, "automatic_cloud_sharing": False}

    def list(self, query="", status=None, project_id=None):
        if status is not None and status not in STATUSES:
            raise ValueError("Unsupported mission status.")
        query = required_text(query, "Query", 200) if query else ""
        items = [{key: mission.get(key) for key in ("id", "goal", "status", "project_id", "updated_at")}
                 for mission in self.s.records.list("mission")
                 if query.casefold() in mission["goal"].casefold() and (status is None or mission["status"] == status)
                 and (project_id is None or mission.get("project_id") == project_id)]
        return {"items": items[:100], "truncated": len(items) > 100, "automatic_cloud_sharing": False}

    def summary(self, id):
        mission = self.get(id)
        return {**{key: mission.get(key) for key in ("id", "goal", "status", "project_id", "progress", "current_blockers",
                "operator_sessions", "deadline", "overdue", "milestones", "skill_ids", "next_steps", "next_step_details",
                "updated_at", "actions_replayed", "automatic_cloud_sharing")}, "recent_history": mission["history"][-10:]}

    def set_status(self, id, status):
        if status not in STATUSES:
            raise ValueError("Unsupported mission status.")
        with self._lock:
            value = self.s.records.get("mission", id)
            previous = value["status"]
            if previous == "cancelled" and status != "cancelled":
                raise ValueError("A cancelled mission cannot be resumed.")
            if status == "paused" and previous not in {"active", "paused"}:
                raise ValueError("Only active missions can be paused.")
            if status == "complete":
                snapshot = self.get(id)
                if (snapshot["current_blockers"] or snapshot["progress"]["completed"] != snapshot["progress"]["total"]
                        or snapshot["progress"]["milestones_completed"] != snapshot["progress"]["milestones_total"]):
                    raise ValueError("Resolve blockers and finish linked work before completing the mission.")
            if status != previous:
                value["status"] = status
                self._history(value, status, previous_status=previous)
                self.s.records.put("mission", value, id)
        # Resume changes tracking only. Operator's own recovery checks remain authoritative.
        return self.summary(id)

    def pause(self, id):
        return self.set_status(id, "paused")

    def resume(self, id):
        return self.set_status(id, "active")

    def archive(self, id):
        return self.set_status(id, "archived")

    def cancel(self, id):
        return self.set_status(id, "cancelled")

    def complete(self, id):
        return self.set_status(id, "complete")

    def delete(self, id):
        with self._lock:
            self.s.records.get("mission", id)
            self.s.records.delete("mission", id)
        return {"deleted": True, "linked_work_preserved": True}


def setup(s, registry):
    s.missions = service = MissionService(s)
    props = {"goal": string(2000), "id": ID, "project_id": string(160, 0), "notes": string(12000, 0),
             "milestones": array(MILESTONE, 30), "deadline": string(50, 0), "next_steps": array(string(500), 20),
             "blockers": array(string(500), 30), **{field: array(string(4096) if field == "files" else ID, 100) for field in MISSION_LINKS}}
    register(registry, "missions.save", "Create or edit a persistent local goal linked to existing tasks, project, knowledge, notes, files and Operator sessions.",
             props, ("goal",), service.save, 2, "missions.write")
    register(registry, "missions.list", "Find saved missions across restarts without replaying their actions.",
             {"query": string(200, 0), "status": enum(*STATUSES), "project_id": ID}, (), service.list)
    for name in ("get", "summary"):
        register(registry, "missions." + name, "Inspect a saved mission's current progress, links and blockers; source access is revalidated.",
                 {"id": ID}, ("id",), getattr(service, name))
    for name in ("pause", "resume", "archive", "cancel", "complete", "delete"):
        register(registry, "missions." + name, "Change mission tracking only; linked tasks, files and Operator actions are preserved and never replayed.",
                 {"id": ID}, ("id",), getattr(service, name), 3 if name in {"cancel", "delete"} else 2, "missions.write")
