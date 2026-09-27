"""User-requested computer context snapshots; no passive app or clipboard tracking."""
from __future__ import annotations

import threading

from jarvix.capabilities.schema import BOOL, ID, array, register, string
from jarvix.runtime import check_cancelled
from jarvix.storage import now_iso


class ContextService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._current = {"conversation_id": None, "project_id": None, "selected_files": []}

    def set_current(self, conversation_id=None, project_id=None, selected_files=None):
        with self._lock:
            if conversation_id is not None:
                if conversation_id and not self.s.db.query("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
                    raise ValueError("Unknown conversation.")
                self._current["conversation_id"] = conversation_id or None
            if project_id is not None:
                if project_id and not self.s.db.query("SELECT id FROM projects WHERE id=?", (project_id,)):
                    raise ValueError("Unknown project.")
                self._current["project_id"] = project_id or None
            if selected_files is not None:
                if len(selected_files) > 20:
                    raise ValueError("Select at most 20 files.")
                self._current["selected_files"] = [str(self.s.files.path(value)) for value in selected_files]
        return {"updated": True, "stored": False}

    def inspect(self, include_clipboard=False, include_selected_files=False):
        if not self.s.settings.get("context.enabled", False):
            raise PermissionError("Enable explicit context snapshots in Settings first.")
        check_cancelled()
        with self._lock:
            selected = dict(self._current)
        window_result = self.s.execute_tool("windows.foreground", {})
        window = window_result.data if window_result.ok else None
        workspace_id = self.s.settings.get("workspace.active_id", "")
        workspace = None
        if workspace_id:
            try:
                saved = self.s.records.get("workspace", workspace_id)
                workspace = {"id": workspace_id, "name": saved["name"], "project_id": saved.get("project_id")}
            except ValueError:
                pass
        project_id = selected["project_id"] or (workspace or {}).get("project_id")
        project = self.s.db.query("SELECT id,name,path FROM projects WHERE id=?", (project_id,)) if project_id else []
        clipboard = None
        if include_clipboard:
            result = self.s.execute_tool("clipboard.classify", {})
            clipboard = result.data if result.ok else {"available": False}
        files = []
        if include_selected_files:
            for path in selected["selected_files"]:
                try:
                    files.append(str(self.s.files.path(path)))
                except (OSError, ValueError):
                    continue
        return {"captured_at": now_iso(), "active_window": window, "workspace": workspace,
                "project": project[0] if project else None, "conversation_id": selected["conversation_id"],
                "selected_files": files, "selected_files_source": "Explicit selection inside Jarvix",
                "clipboard_type": clipboard, "stored": False, "automatic_cloud_sharing": False}


def setup(s, registry):
    s.context = service = ContextService(s)
    register(registry, "context.inspect", "Take one explicit, opt-in context snapshot. Contains no clipboard text and is never automatically shared.",
             {"include_clipboard": BOOL, "include_selected_files": BOOL}, (), service.inspect)
    register(registry, "context.select", "Explicitly select a known project/conversation/allowed files for this session's context.",
             {"conversation_id": ID, "project_id": ID, "selected_files": array(string(), 20)}, (), service.set_current, 2)
