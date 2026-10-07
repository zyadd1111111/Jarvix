"""On-demand local health observations; never probes a paid provider or starts services."""
from __future__ import annotations

import platform
import sqlite3

from jarvix import __version__
from jarvix.capabilities.schema import register
from jarvix.runtime import check_cancelled
from jarvix.storage import now_iso


def permitted(s, name):
    return name in s.enabled_tools() and not s.db.query(
        "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,))


def access(s, name):
    check_cancelled()
    if not permitted(s, name):
        raise PermissionError("This tool is disabled or denied.")


class DiagnosticsService:
    def __init__(self, services):
        self.s = services

    def _source(self, tool, arguments=None):
        if not permitted(self.s, tool):
            return None
        try:
            result = self.s.execute_tool(tool, arguments or {})
        except (OSError, RuntimeError, sqlite3.Error):
            return None
        return result.data if result.ok else None

    def _knowledge(self):
        spaces = self._source("knowledge_spaces.list")
        if spaces is None or not permitted(self.s, "knowledge_spaces.get"):
            return None
        selected = spaces["items"][:20]
        unavailable, sources = 0, 0
        for row in selected:
            space = self._source("knowledge_spaces.get", {"id": row["id"]})
            if space is None:
                unavailable += 1
                continue
            for source in space.get("sources", []):
                check_cancelled()
                sources += 1
                try:
                    kind, reference = source["kind"], source["reference"]
                    if kind == "folder":
                        access(self.s, "files.inspect")
                        if not self.s.files.path(reference).is_dir():
                            raise ValueError("Folder unavailable.")
                    elif kind == "drive":
                        access(self.s, "integrations.status")
                        access(self.s, "google_drive.metadata")
                        from jarvix.capabilities.search import drive_identity
                        if self.s.records.get("knowledge.drive", reference).get("account_key") != drive_identity(self.s):
                            raise ValueError("Knowledge account changed.")
                    else:
                        self.s.context_graph.resolve(kind, reference)
                except (ValueError, OSError, KeyError):
                    unavailable += 1
        needs_refresh = sum(not row.get("last_refresh") for row in selected)
        return {"status": "Warning" if unavailable or needs_refresh else "Healthy" if selected else "Unchecked",
                "spaces_checked": len(selected), "sources_checked": sources, "unavailable_sources": unavailable,
                "spaces_needing_refresh": needs_refresh, "truncated": bool(spaces.get("truncated") or len(spaces["items"]) > 20),
                "contents_read": False, "live_cloud_probe": False}

    def health(self):
        access(self.s, "diagnostics.health")
        checks = {}
        try:
            rows = self.s.db.query("PRAGMA quick_check(1)")
            checks["database"] = {"status": "Healthy" if rows and next(iter(rows[0].values())) == "ok" else "Error",
                                  "schema_version": self.s.db.query("PRAGMA user_version")[0]["user_version"]}
        except (sqlite3.Error, RuntimeError):
            checks["database"] = {"status": "Error"}
        models = self._source("models.health")
        if models is not None:
            providers = models.get("providers", [])
            checks["providers"] = {"status": "Warning" if any(row.get("available") is False for row in providers) else "Unchecked",
                "observed": len(providers), "available": sum(row.get("available") is True for row in providers),
                "models_with_failures": sum(bool(row.get("failures")) for row in models.get("models", [])),
                "live_probe": False, "basis": "Saved observations only; current provider health is unchecked."}
        index = self._source("search.status")
        if index is not None:
            checks["index"] = {"status": "Healthy" if index.get("sources") else "Unchecked", "sources": index.get("sources", 0),
                               "chunks": index.get("chunks", 0), "local_only": True, "automatic_indexing": False}
        plugins = self._source("plugins.list")
        if plugins is not None:
            invalid = sum(row.get("status") == "Invalid manifest" for row in plugins)
            checks["plugins"] = {"status": "Error" if invalid else "Healthy" if plugins else "Unchecked",
                                 "installed": len(plugins), "enabled": sum(bool(row.get("enabled")) for row in plugins), "invalid": invalid}
        browser = self._source("browser.status")
        if browser is not None:
            checks["browser"] = {key: browser.get(key) for key in ("connected", "listening", "browser")}
            checks["browser"]["status"] = "Healthy" if browser.get("connected") else "Unchecked"
        schedules = self._source("scheduler.list")
        if schedules is not None:
            failed = sum(any(word in str(row.get("last_status", "")).casefold()
                             for word in ("failed", "interrupted", "denied")) for row in schedules)
            checks["scheduler"] = {"status": "Warning" if failed else "Unchecked", "saved_schedules": len(schedules),
                "enabled": sum(bool(row.get("enabled")) for row in schedules), "live_probe": False, "needs_attention": failed}
        integrations = self._source("integrations.status")
        if integrations is not None:
            failed = sum(row.get("status") in {"Error", "Needs attention"} for row in integrations)
            connected = sum(row.get("status") == "Connected" for row in integrations)
            checks["integrations"] = {"status": "Warning" if failed else "Healthy" if connected else "Unchecked",
                "connected": connected, "needs_attention": failed, "live_probe": False, "credential_contents_read": False}
        knowledge = self._knowledge()
        if knowledge is not None:
            checks["knowledge"] = knowledge
        startup = self._source("windows.startup")
        if startup is not None:
            checks["windows_startup"] = {"status": "Warning" if startup.get("needs_attention") else "Healthy",
                "registered_for_this_profile": bool(startup.get("enabled")), "scope": "Current Windows user"}
        checks["permissions"] = {"status": "Unchecked", "basis": "Application opt-ins only; OS permissions checked during explicit capability use.",
            "app_opt_ins": {name: self.s.settings.get(key, False) is True for name, key in {
                "control": "control.enabled", "clipboard": "clipboard.enabled", "screen": "screenshots.enabled",
                "microphone": "microphone.enabled", "browser": "browser.control_enabled", "context": "context.enabled",
                "proactive": "proactive.enabled"}.items()}, "sensitive_actions": "Fresh confirmation required"}
        background = getattr(self.s, "background", None)
        checks["runtime"] = {"status": "Warning" if background and background._last_error else "Healthy",
                             "jarvix_version": __version__, "python_version": platform.python_version(),
                             "background_running": bool(background and background.running),
                             "background_error": bool(background and background._last_error)}
        for key in ("providers", "index", "plugins", "browser", "scheduler", "integrations", "knowledge", "windows_startup"):
            checks.setdefault(key, {"status": "Unchecked", "available": False, "reason": "Source disabled, denied, or inaccessible."})
        return {"checked_at": now_iso(), "checks": checks, "network_requests": 0, "actions_performed": False}


def setup(s, registry):
    s.diagnostics = DiagnosticsService(s)
    register(registry, "diagnostics.health", "Check local DB integrity and permitted provider, index, knowledge-reference, integration, plugin, scheduler and runtime observations; inspect application opt-ins and Jarvix-only startup. No network or credential probes.",
             {}, (), s.diagnostics.health)
