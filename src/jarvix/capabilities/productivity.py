"""Productivity extensions backed by existing rows and versioned local metadata."""
from __future__ import annotations

import calendar
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from jarvix.capabilities.schema import BOOL, ID, array, enum, integer, register, string
from jarvix.services import required_text
from jarvix.storage import now_iso


def row(s, table, record_id):
    if table not in {"tasks", "notes", "memories", "projects", "apps"}:
        raise ValueError("Unsupported record type.")
    rows = s.db.query(f"SELECT * FROM {table} WHERE id=?", (record_id,))
    if not rows:
        raise ValueError("Record not found.")
    return rows[0]


def metadata(s, kind, record_id):
    try:
        return {key: value for key, value in s.records.get(kind, record_id).items()
                if key not in {"id", "created_at", "updated_at"}}
    except ValueError:
        return {}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Include a timezone in dates.")
    return parsed.astimezone(timezone.utc)


def tags(values):
    return list(dict.fromkeys(required_text(value, "Tag", 60) for value in values))[:30]


class TaskService:
    def __init__(self, s):
        self.s = s

    def update(self, id, title=None, due_at=None, priority=None, tags=None, project_id=None,
               parent_id=None, recurrence=None, interval=None):
        task = row(self.s, "tasks", id)
        meta = metadata(self.s, "task.meta", id)
        if title is not None:
            task["title"] = required_text(title, "Task title", 500)
        if due_at is not None:
            task["due_at"] = timestamp(due_at).isoformat(timespec="seconds") if due_at else None
        if priority is not None:
            if priority not in {"low", "normal", "high", "urgent"}:
                raise ValueError("Invalid priority.")
            meta["priority"] = priority
        if tags is not None:
            meta["tags"] = globals()["tags"](tags)
        if project_id is not None:
            if project_id:
                row(self.s, "projects", project_id)
            meta["project_id"] = project_id
        if parent_id is not None:
            seen, parent = {id}, parent_id
            while parent:
                if parent in seen:
                    raise ValueError("Subtasks cannot form cycles.")
                seen.add(parent)
                row(self.s, "tasks", parent)
                parent = metadata(self.s, "task.meta", parent).get("parent_id")
            meta["parent_id"] = parent_id
        if recurrence is not None:
            if recurrence not in {"none", "daily", "weekly", "monthly"}:
                raise ValueError("Invalid recurrence.")
            meta["recurrence"] = recurrence
        if interval is not None:
            if not isinstance(interval, int) or isinstance(interval, bool) or not 1 <= interval <= 365:
                raise ValueError("Recurrence interval must be between 1 and 365.")
            meta["interval"] = interval
        self.s.db.execute("UPDATE tasks SET title=?,due_at=?,reminded_at=CASE WHEN due_at IS ? THEN reminded_at ELSE NULL END WHERE id=?",
                          (task["title"], task["due_at"], task["due_at"], id))
        self.s.records.put("task.meta", meta, id)
        self.s.repository.audit("task", "Task details updated")
        return {"id": id, **task, **meta}

    def search(self, query="", view="all", project_id=None, tag=None, priority=None):
        current, matches = datetime.now(timezone.utc), []
        for task in self.s.list_tasks():
            item = {**task, **metadata(self.s, "task.meta", task["id"])}
            due = timestamp(item["due_at"]) if item.get("due_at") else None
            if query.casefold() not in (item["title"] + " " + " ".join(item.get("tags", []))).casefold():
                continue
            if project_id is not None and item.get("project_id") != project_id:
                continue
            if tag is not None and tag not in item.get("tags", []):
                continue
            if priority is not None and item.get("priority", "normal") != priority:
                continue
            if view == "completed" and item["status"] != "done":
                continue
            if view in {"open", "today", "upcoming", "overdue"} and item["status"] != "open":
                continue
            if view == "today" and (not due or due.astimezone().date() != current.astimezone().date()):
                continue
            if view == "upcoming" and (not due or not current < due <= current + timedelta(days=7)):
                continue
            if view == "overdue" and (not due or due >= current):
                continue
            matches.append(item)
        matches.sort(key=lambda item: (item.get("due_at") or "9999", item["title"].casefold()))
        return {"items": matches[:100], "truncated": len(matches) > 100}

    def subtask(self, parent_id, title, due_at=None):
        row(self.s, "tasks", parent_id)
        task_id = self.s.add_task(title, due_at)
        return self.update(task_id, parent_id=parent_id)

    def complete(self, id):
        meta = metadata(self.s, "task.meta", id)
        next_id = None
        stamp = now_iso()
        with self.s.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
            if not task:
                raise ValueError("Task not found.")
            if task["status"] == "done":
                return {"id": id, "already_completed": True}
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (id,))
            recurrence = meta.get("recurrence", "none")
            if recurrence != "none":
                base = timestamp(task["due_at"] or stamp)
                amount = int(meta.get("interval", 1))
                if recurrence == "monthly":
                    month = base.year * 12 + base.month - 1 + amount
                    year, month = divmod(month, 12)
                    base = base.replace(year=year, month=month + 1,
                                        day=min(base.day, calendar.monthrange(year, month + 1)[1]))
                else:
                    base += timedelta(days=amount * (7 if recurrence == "weekly" else 1))
                next_id = self.s.repository.new_id()
                conn.execute("INSERT INTO tasks(id,title,due_at,created_at) VALUES (?,?,?,?)",
                             (next_id, task["title"], base.isoformat(timespec="seconds"), stamp))
                next_meta = {k: v for k, v in meta.items() if k != "completed_at"}
                next_meta["previous_occurrence_id"] = id
                conn.execute("INSERT INTO records VALUES (?,?,?,?,?)",
                             (next_id, "task.meta", json.dumps(next_meta), stamp, stamp))
            meta["completed_at"] = stamp
            conn.execute("INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(id,kind) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                         (id, "task.meta", json.dumps(meta), stamp, stamp))
        self.s.repository.audit("task", "Task completed")
        return {"id": id, "next_occurrence_id": next_id}

    def reopen(self, id):
        row(self.s, "tasks", id)
        meta = metadata(self.s, "task.meta", id)
        # Reopening an occurrence must not create a second recurring chain.
        if any(item.get("previous_occurrence_id") == id for item in self.s.records.list("task.meta")):
            meta["recurrence"] = "none"
        meta.pop("completed_at", None)
        self.s.records.put("task.meta", meta, id)
        self.s.db.execute("UPDATE tasks SET status='open',reminded_at=NULL WHERE id=?", (id,))
        return {"id": id, "status": "open"}

    def from_note(self, note_id, title=None, due_at=None):
        note = row(self.s, "notes", note_id)
        task_id = self.s.add_task(title or note["title"], due_at)
        self.s.records.put("task.meta", {"source_note_id": note_id}, task_id)
        return {"id": task_id, "source_note_id": note_id}


class NoteService:
    def __init__(self, s):
        self.s = s

    def snapshot(self, note_id):
        if not note_id:
            return
        try:
            note = row(self.s, "notes", note_id)
        except ValueError:
            return
        self.s.records.put("note.version", {"note_id": note_id, "title": note["title"], "body": note["body"]})
        snapshots = [v for v in self.s.records.list("note.version") if v["note_id"] == note_id]
        for old in snapshots[30:]:
            self.s.records.delete("note.version", old["id"])

    def edit(self, id, title, body):
        row(self.s, "notes", id)
        self.s.save_note(title, body, id)
        return {"id": id}

    def organize(self, id, folder=None, tags=None, pinned=None, favorite=None, project_id=None, task_id=None):
        row(self.s, "notes", id)
        meta = metadata(self.s, "note.meta", id)
        for key, value in (("folder", folder), ("pinned", pinned), ("favorite", favorite)):
            if value is not None:
                meta[key] = value
        if tags is not None:
            meta["tags"] = globals()["tags"](tags)
        for key, value, table in (("project_id", project_id, "projects"), ("task_id", task_id, "tasks")):
            if value is not None:
                if value:
                    row(self.s, table, value)
                meta[key] = value
        self.s.records.put("note.meta", meta, id)
        return {"id": id, **meta}

    def list(self, query="", folder=None, tag=None, pinned=None, favorite=None, project_id=None):
        matches = []
        for note in self.s.list_notes():
            item = {**note, **metadata(self.s, "note.meta", note["id"])}
            if query.casefold() not in (note["title"] + " " + note["body"]).casefold():
                continue
            if any(value is not None and item.get(key) != value
                   for key, value in (("folder", folder), ("pinned", pinned), ("favorite", favorite), ("project_id", project_id))):
                continue
            if tag is not None and tag not in item.get("tags", []):
                continue
            matches.append({**item, "body": item["body"][:500]})
        matches.sort(key=lambda item: not item.get("pinned", False))
        return {"items": matches[:50], "truncated": len(matches) > 50}

    def history(self, id):
        row(self.s, "notes", id)
        return {"items": [{**item, "body": item["body"][:1000]} for item in self.s.records.list("note.version")
                          if item["note_id"] == id][:30]}

    def restore_version(self, version_id):
        version = self.s.records.get("note.version", version_id)
        row(self.s, "notes", version["note_id"])
        self.s.save_note(version["title"], version["body"], version["note_id"])
        return {"id": version["note_id"]}

    def checklist(self, id):
        note = row(self.s, "notes", id)
        body = "\n".join(line if line.lstrip().startswith(("- [ ]", "- [x]", "- [X]")) else
                         "- [ ] " + line.strip().removeprefix("- ").removeprefix("* ")
                         for line in note["body"].splitlines() if line.strip())
        return {"id": self.s.save_note(note["title"] + " — checklist", body)}

    def export(self, id, path):
        note = row(self.s, "notes", id)
        target = self.s.files.path(path, existing=False)
        if target.suffix.lower() not in {".md", ".txt"}:
            raise ValueError("Use a Markdown or text filename.")
        with target.open("x", encoding="utf-8") as handle:
            handle.write(f"# {note['title']}\n\n{note['body']}\n")
        return {"path": str(target)}


class MemoryService:
    def __init__(self, s):
        self.s = s

    def edit(self, id, content=None, category=None, source=None, importance=None, expires_at=None):
        memory = row(self.s, "memories", id)
        meta = metadata(self.s, "memory.meta", id)
        if content is not None:
            memory["content"] = required_text(content, "Memory", 5000)
        if expires_at is not None:
            meta["expires_at"] = timestamp(expires_at).isoformat() if expires_at else None
        for key, value in (("category", category), ("source", source), ("importance", importance)):
            if value is not None:
                meta[key] = value
        self.s.db.execute("UPDATE memories SET content=? WHERE id=?", (memory["content"], id))
        self.s.records.put("memory.meta", meta, id)
        return {"id": id, **meta}

    def remember_sensitive(self, content, category="personal", source="user", importance=3, expires_at=None):
        if expires_at:
            timestamp(expires_at)
        record_id = self.s.add_memory(content)
        self.edit(record_id, category=category, source=source, importance=importance, expires_at=expires_at)
        return {"id": record_id}

    def list(self, query="", category=None, include_expired=False):
        items, current = [], datetime.now(timezone.utc)
        for memory in self.s.list_memories():
            item = {**memory, **metadata(self.s, "memory.meta", memory["id"])}
            if query.casefold() not in memory["content"].casefold():
                continue
            if category is not None and item.get("category") != category:
                continue
            item["expired"] = bool(item.get("expires_at") and timestamp(item["expires_at"]) <= current)
            if item["expired"] and not include_expired:
                continue
            items.append(item)
        items.sort(key=lambda value: -value.get("importance", 3))
        return {"items": items[:50], "truncated": len(items) > 50}

    def delete(self, id):
        row(self.s, "memories", id)
        self.s.delete_memory(id)
        self.s.records.delete("memory.meta", id)
        return {"deleted": True}


class ProjectService:
    def __init__(self, s):
        self.s = s

    def create(self, name, path):
        target = self.s.files.path(path)
        return {"id": self.s.add_project(name, str(target))}

    def update(self, id, name=None, archived=None, workspace_id=None):
        project = row(self.s, "projects", id)
        meta = metadata(self.s, "project.meta", id)
        if name is not None:
            self.s.db.execute("UPDATE projects SET name=? WHERE id=?", (required_text(name, "Project", 200), id))
        if archived is not None:
            meta["archived"] = archived
        if workspace_id is not None:
            if workspace_id:
                self.s.records.get("workspace", workspace_id)
            meta["workspace_id"] = workspace_id
        self.s.records.put("project.meta", meta, id)
        return {**project, **meta, "name": name or project["name"]}

    def summary(self, id):
        project = row(self.s, "projects", id)
        tasks = self.s.productivity.tasks.search(project_id=id)["items"]
        notes = self.s.productivity.notes.list(project_id=id)["items"]
        from pathlib import Path
        root = Path(project["path"])
        files = [item for item in self.s.list_files() if Path(item["path"]).is_relative_to(root)]
        return {"project": {**project, **metadata(self.s, "project.meta", id)}, "tasks": tasks[:30],
                "notes": notes[:20], "files": files[:30], "open_tasks": sum(t["status"] == "open" for t in tasks)}


def setup(s, registry):
    tasks, notes, memories, projects = TaskService(s), NoteService(s), MemoryService(s), ProjectService(s)
    s.productivity = SimpleNamespace(tasks=tasks, notes=notes, memories=memories, projects=projects,
                                     complete_task=tasks.complete, snapshot_note=notes.snapshot)
    def add(name, description, props, required, handler, level=1):
        register(registry, name, description, props, required, handler, level,
                 "local.read" if level == 1 else name.split(".")[0] + ".write")
    task_props = {"id": ID, "title": string(500), "due_at": string(40, 0), "priority": enum("low", "normal", "high", "urgent"),
                  "tags": array(string(60), 30), "project_id": string(160, 0), "parent_id": string(160, 0),
                  "recurrence": enum("none", "daily", "weekly", "monthly"), "interval": integer(1, 365)}
    add("tasks.update", "Edit task title, due time, priority, tags, project, parent or recurrence. Empty due_at clears it.", task_props, ["id"], tasks.update, 2)
    add("tasks.search", "Search tasks with today, next-seven-days upcoming, overdue, completed or open views.",
        {"query": string(200, 0), "view": enum("all", "today", "upcoming", "overdue", "completed", "open"),
         "project_id": ID, "tag": string(60), "priority": task_props["priority"]}, [], tasks.search)
    add("tasks.add_subtask", "Create a task linked to a parent task.", {"parent_id": ID, "title": string(500), "due_at": string(40)}, ["parent_id", "title"], tasks.subtask, 2)
    add("tasks.complete", "Complete a task; generate its next occurrence exactly once when recurring.", {"id": ID}, ["id"], lambda id: s.complete_task(id), 2)
    add("tasks.reopen", "Reopen a completed task without duplicating its recurring chain.", {"id": ID}, ["id"], tasks.reopen, 2)
    add("tasks.from_note", "Create a task linked to a saved note.", {"note_id": ID, "title": string(500), "due_at": string(40)}, ["note_id"], tasks.from_note, 2)
    add("notes.edit", "Edit a Markdown note, preserving its previous version locally.", {"id": ID, "title": string(200), "body": string(16000, 0)}, ["id", "title", "body"], notes.edit, 2)
    add("notes.organize", "Set a note's folder, tags, pin, favorite, project and task links.",
        {"id": ID, "folder": string(160, 0), "tags": array(string(60), 30), "pinned": BOOL, "favorite": BOOL,
         "project_id": string(160, 0), "task_id": string(160, 0)}, ["id"], notes.organize, 2)
    add("notes.filter", "Search notes with folder, tag, pin, favorite and project filters.",
        {"query": string(200, 0), "folder": string(160), "tag": string(60), "pinned": BOOL, "favorite": BOOL, "project_id": ID}, [], notes.list)
    add("notes.history", "Inspect the last 30 note version snapshots with bounded previews.", {"id": ID}, ["id"], notes.history)
    add("notes.restore_version", "Restore a note version; preserve current text in a new snapshot.", {"version_id": ID}, ["version_id"], notes.restore_version, 2)
    add("notes.to_checklist", "Create a Markdown checklist copy, one checkbox per nonempty line.", {"id": ID}, ["id"], notes.checklist, 2)
    add("notes.export", "Export note to a new .md or .txt file in an allowed root. Never overwrite.", {"id": ID, "path": string()}, ["id", "path"], notes.export, 2)
    memory_props = {"content": string(5000), "category": string(100), "source": string(200), "importance": integer(1, 5), "expires_at": string(40, 0)}
    add("memory.edit", "Edit explicit memory content or metadata. Always asks confirmation for potentially sensitive facts.", {"id": ID, **memory_props}, ["id"], memories.edit, 3)
    add("memory.remember_sensitive", "Save user-requested sensitive memory only after immediate confirmation.", memory_props, ["content"], memories.remember_sensitive, 3)
    add("memory.filter", "Find explicit memories by category or text, excluding expired entries by default.",
        {"query": string(200, 0), "category": string(100), "include_expired": BOOL}, [], memories.list)
    add("memory.delete", "Permanently remove an explicit memory after confirmation.", {"id": ID}, ["id"], memories.delete, 3)
    add("projects.create", "Register an existing coding/project directory inside allowed roots.", {"name": string(200), "path": string()}, ["name", "path"], projects.create, 2)
    add("projects.update", "Rename, archive/unarchive or link a workspace to a project.",
        {"id": ID, "name": string(200), "archived": BOOL, "workspace_id": string(160, 0)}, ["id"], projects.update, 2)
    add("projects.summary", "Retrieve a project's linked notes, tasks, indexed files and workspace metadata.", {"id": ID}, ["id"], projects.summary)
