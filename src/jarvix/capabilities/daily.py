"""Optional bounded briefs from permitted saved work and explicitly selected accounts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from jarvix.capabilities.schema import BOOL, array, enum, register, schema, string
from jarvix.domain import ToolResult
from jarvix.runtime import CURRENT, check_cancelled

LOCAL = ("tasks", "missions", "workflow_failures")
EXTERNAL = {"calendar": "google_calendar", "github": "github", "gmail": "gmail"}
OPTIONS = schema({"query": string(1000, 0), "owner": string(100), "repo": string(100)})


class DailyBriefService:
    def __init__(self, services):
        self.s = services

    def _permitted(self, *names):
        return all(name in self.s.enabled_tools() and not self.s.db.query(
            "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,)) for name in names)

    def _accounts(self):
        if not self._permitted("integrations.status"):
            return {}
        return {row["id"]: row for row in self.s.integrations.status()}

    @staticmethod
    def _identity(row):
        return hashlib.sha256(json.dumps([row["id"], row.get("account", "")]).encode()).hexdigest()

    def configure(self, enabled=None, sources=None, external=None):
        if (enabled is not None and type(enabled) is not bool
                or sources is not None and (not isinstance(sources, list) or len(sources) > 3
                    or any(source not in LOCAL for source in sources))
                or external is not None and (not isinstance(external, dict) or set(external) - set(EXTERNAL))):
            raise ValueError("Choose supported daily brief sources.")
        selected = None
        if external is not None:
            selected = {}
            accounts = self._accounts()
            for source, options in external.items():
                if (not isinstance(options, dict) or set(options) - set(OPTIONS["properties"])
                        or any(not isinstance(value, str) for value in options.values())
                        or any(len(value) > (1000 if key == "query" else 100) for key, value in options.items())
                        or source == "github" and not all(options.get(key) for key in ("owner", "repo"))
                        or source != "github" and set(options) - {"query"}):
                    raise ValueError("Set an explicit repository or a bounded account query.")
                row = accounts.get(EXTERNAL[source])
                if not row or row["status"] != "Connected":
                    raise PermissionError("Connect this account before opting it into a daily brief.")
                selected[source] = {"options": dict(options), "account_key": self._identity(row)}
        if enabled is not None:
            self.s.settings.set("daily.enabled", enabled)
        if sources is not None:
            self.s.settings.set("daily.sources", list(dict.fromkeys(sources)))
        if selected is not None:
            self.s.settings.set("daily.external", selected)
        return self.status()

    def status(self):
        return {"enabled": self.s.settings.get("daily.enabled", False),
                "sources": self.s.settings.get("daily.sources", []),
                "external": {key: value["options"] for key, value in self.s.settings.get("daily.external", {}).items()},
                "external_requires_foreground_request": True, "cloud_request": False,
                "actions_executed": 0, "automatic_upload": False}

    @staticmethod
    def _due(row, now):
        if row.get("status") != "open" or not row.get("due_at"):
            return False
        try:
            due = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
            return due.tzinfo is not None and due <= now + timedelta(days=1)
        except (TypeError, ValueError):
            return False

    def _local(self, source, now):
        tools = {"tasks": ("tasks.list", "tasks.search"),
                 "missions": ("missions.list", "missions.get", "missions.summary"),
                 "workflow_failures": ("workflows.list", "workflows.history")}[source]
        if not self._permitted(*tools):
            return {"source": source, "status": "permission_denied", "items": []}
        if source == "tasks":
            rows = self.s.list_tasks()
            selected = [row for row in rows if self._due(row, now)]
            selected.sort(key=lambda row: row["due_at"])
            items = [{key: row.get(key) for key in ("id", "title", "due_at", "project_id")}
                     for row in selected[:10]]
        elif source == "missions":
            items = []
            for row in self.s.records.list("mission")[:30]:
                if row.get("status") not in {"active", "paused"}:
                    continue
                summary = self.s.missions.summary(row["id"])
                items.append({key: summary.get(key) for key in ("id", "goal", "status", "project_id", "progress")})
                items[-1]["blocker_count"] = len(summary.get("current_blockers", []))
                if len(items) >= 10:
                    break
        else:
            items = []
            for row in self.s.records.list("workflow_run")[:100]:
                if row.get("test_mode") or row.get("status") not in {"failed", "partial", "timed_out", "interrupted"}:
                    continue
                try:
                    self.s.records.get("workflow", row["workflow_id"])
                except ValueError:
                    continue
                items.append({key: row.get(key) for key in ("id", "workflow_id", "name", "status", "ended_at")})
                if len(items) >= 10:
                    break
        return {"source": source, "status": "ok", "items": items,
                "coverage": "Bounded saved local metadata; no app observation or action."}

    def brief(self, include_external=False):
        if type(include_external) is not bool:
            raise ValueError("Choose whether to request opted-in account sources.")
        if not self._permitted("daily.brief"):
            raise PermissionError("Daily brief access is disabled or denied.")
        if not self.s.settings.get("daily.enabled", False):
            return {"enabled": False, "sections": [], "actions_executed": 0, "cloud_request": False}
        now = datetime.now(timezone.utc)
        sections = []
        for source in self.s.settings.get("daily.sources", []):
            check_cancelled()
            try:
                sections.append(self._local(source, now))
            except (ValueError, OSError, KeyError):
                sections.append({"source": source, "status": "source_unavailable", "items": []})
        configured = self.s.settings.get("daily.external", {})
        accounts = self._accounts() if include_external and configured else {}
        for source, choice in configured.items():
            check_cancelled()
            status = "not_requested"
            if include_external:
                row = accounts.get(EXTERNAL[source])
                current = CURRENT.get()
                if current and current.unattended:
                    status = "foreground_required"
                elif not self._permitted("integrations.status", "intelligence.briefing"):
                    status = "permission_denied"
                elif not row or row["status"] != "Connected":
                    status = "not_connected"
                elif self._identity(row) != choice["account_key"]:
                    status = "account_changed"
                else:
                    args = {"source": source, "limit": 5, **choice["options"]}
                    if source == "calendar":
                        args.update(start=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                                    end=(now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
                    value = self.s.execute_tool("intelligence.briefing", args)
                    if isinstance(value, ToolResult) and value.ok:
                        sections.append({"source": source, "status": "ok", "items": value.data.get("sections", []),
                                         "coverage": value.data.get("coverage"), "account_api_read": True})
                        continue
                    status = "read_failed"
            sections.append({"source": source, "status": status, "items": []})
        return {"enabled": True, "generated_at": now.isoformat(), "sections": sections,
                "partial": any(section["status"] not in {"ok", "not_requested"} for section in sections),
                "actions_executed": 0, "cloud_request": False, "automatic_upload": False}


def setup(s, registry):
    s.daily = service = DailyBriefService(s)
    register(registry, "daily.status", "Inspect daily brief opt-in sources; disabled by default.", {}, (), service.status)
    register(registry, "daily.configure", "Opt into explicit saved local or connected account brief sources. Does not run actions or upload local context.",
             {"enabled": BOOL, "sources": array(enum(*LOCAL), 3),
              "external": schema({source: OPTIONS for source in EXTERNAL})}, (), service.configure, 3)
    register(registry, "daily.brief", "Build a bounded brief. Accounts require explicit opt-in and a foreground request; no actions are performed.",
             {"include_external": BOOL}, (), service.brief)
