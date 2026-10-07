"""Opt-in suggestions from existing local records; no passive app or cloud reads."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from jarvix.capabilities.schema import BOOL, ID, array, enum, register
from jarvix.storage import now_iso
from jarvix.runtime import check_cancelled

CATEGORIES = ("unfinished_session", "workflow_failure", "task_due")
SOURCES = {"unfinished_session": ("operator.sessions", "operator.session"),
           "workflow_failure": ("workflows.history", "workflows.history"),
           "task_due": ("tasks.list", "tasks.search")}


class ProactiveService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._last_refresh = 0

    def configure(self, enabled=None, muted=None):
        if ((enabled is not None and type(enabled) is not bool)
                or (muted is not None and (not isinstance(muted, list) or len(muted) > 3
                    or any(not isinstance(category, str) or category not in CATEGORIES for category in muted)))):
            raise ValueError("Choose supported suggestion categories.")
        with self._lock:
            if enabled is not None:
                self.s.settings.set("proactive.enabled", enabled)
            if muted is not None:
                self.s.settings.set("proactive.muted", list(dict.fromkeys(muted)))
            self._last_refresh = 0
        return {"enabled": self.s.settings.get("proactive.enabled", False),
                "muted": self.s.settings.get("proactive.muted", []), "cloud_request": False}

    def dismiss(self, id):
        with self._lock:
            item = self.s.records.get("suggestion", id)
            self.s.records.put("suggestion.dismissal", {"category": item["category"], "reference": item["reference"],
                                                       "dismissed_at": now_iso()}, id)
        return {"dismissed": True}

    def _permitted(self, category):
        for name in SOURCES[category]:
            if name not in self.s.enabled_tools() or self.s.db.query(
                    "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,)):
                return False
            spec = self.s.registry.get(name)
            if spec.risk != "read" or spec.permission_level != 1:
                return False
        return True

    def _source_version(self, category, reference):
        """Validate only saved source metadata, including current per-tool denials."""
        if category not in SOURCES or not self._permitted(category):
            return None
        try:
            if category == "unfinished_session":
                row = self.s.records.get("operator_session", reference)
                if row.get("status") not in {"paused", "interrupted", "failed", "timed_out"}:
                    return None
                parts = [row["status"], row["updated_at"]]
            elif category == "workflow_failure":
                row = self.s.records.get("workflow_run", reference)
                self.s.records.get("workflow", row["workflow_id"])
                if row.get("status") not in {"failed", "partial", "timed_out", "interrupted"}:
                    return None
                parts = [row["status"], row["updated_at"], row["workflow_id"]]
            else:
                rows = self.s.db.query("SELECT title,status,due_at FROM tasks WHERE id=?", (reference,))
                if not rows or rows[0]["status"] != "open" or not rows[0]["due_at"]:
                    return None
                row = rows[0]
                due = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
                current = datetime.now(timezone.utc)
                if due.tzinfo is None or not current - timedelta(days=1) <= due <= current + timedelta(hours=1):
                    return None
                parts = [row["title"], row["due_at"], row["status"]]
            return hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        except (ValueError, KeyError):
            return None

    def refresh(self, force=False):
        with self._lock:
            if not self.s.settings.get("proactive.enabled", False):
                return []
            check_cancelled()
            if not force and time.monotonic() - self._last_refresh < 60:
                return self.list()
            muted = set(self.s.settings.get("proactive.muted", []))
            active = set()
            def suggest(category, reference, title, reason, action, arguments):
                if category in muted:
                    return
                version = self._source_version(category, reference)
                if version is None:
                    return
                id = hashlib.sha256((category + ":" + reference).encode()).hexdigest()
                active.add(id)
                try:
                    item = self.s.records.get("suggestion", id)
                except ValueError:
                    item = {"first_seen": now_iso()}
                item.update(category=category, reference=reference, title=title[:300], reason=reason,
                            action=action, arguments=arguments, source_version=version,
                            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
                self.s.records.put("suggestion", item, id)

            if "unfinished_session" not in muted and self._permitted("unfinished_session"):
                for row in self.s.operator.list()[:30]:
                    if row.get("status") in {"paused", "interrupted", "failed", "timed_out"}:
                        suggest("unfinished_session", row["id"], "An Operator session needs attention",
                                "A saved local session has unfinished work. Review uncertain actions before continuing.",
                                "operator.session", {"id": row["id"]})
            if "workflow_failure" not in muted and self._permitted("workflow_failure"):
                for row in self.s.records.list("workflow_run")[:30]:
                    if row.get("status") in {"failed", "partial", "timed_out", "interrupted"}:
                        suggest("workflow_failure", row["id"], "A workflow needs review",
                                "A saved local workflow run reports a failure or partial completion.",
                                "workflows.history", {"id": row["workflow_id"]})
            if "task_due" not in muted and self._permitted("task_due"):
                current = datetime.now(timezone.utc)
                for row in self.s.list_tasks():
                    if row.get("status") != "open" or not row.get("due_at"):
                        continue
                    try:
                        due = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
                        if due.tzinfo is None:
                            continue
                    except ValueError:
                        continue
                    if current - timedelta(days=1) <= due <= current + timedelta(hours=1):
                        suggest("task_due", row["id"], "Due soon: " + row["title"],
                                "An unfinished task has a saved due time within the last day or next hour.",
                                "tasks.search", {"query": row["title"][:200]})
            for item in self.s.records.list("suggestion"):
                if item["id"] not in active:
                    self.s.records.delete("suggestion", item["id"])
            self._last_refresh = time.monotonic()
            return self.list()

    def list(self):
        if not self.s.settings.get("proactive.enabled", False):
            return []
        muted = self.s.settings.get("proactive.muted", [])
        current = datetime.now(timezone.utc).isoformat()
        return [row for row in self.s.records.list("suggestion") if not row.get("dismissed")
                and row["category"] not in muted and row.get("expires_at", "") > current
                and not self.s.db.query("SELECT 1 FROM records WHERE kind='suggestion.dismissal' AND id=?", (row["id"],))
                and row.get("source_version")
                and row["source_version"] == self._source_version(row["category"], row["reference"])][:20]


def setup(s, registry):
    s.proactive = service = ProactiveService(s)
    register(registry, "suggestions.list", "Inspect optional local suggestions and why each appeared. Never reads apps or calls cloud providers.",
             {}, (), lambda: {"items": service.refresh(), "enabled": s.settings.get("proactive.enabled", False)})
    register(registry, "suggestions.configure", "Explicitly opt in/out of local suggestions and mute categories. Suggestions never execute actions automatically.",
             {"enabled": BOOL, "muted": array(enum(*CATEGORIES), 3)}, (), service.configure, 3)
    register(registry, "suggestions.dismiss", "Dismiss a suggestion without changing its task, session or workflow.",
             {"id": ID}, ("id",), service.dismiss, 2)
