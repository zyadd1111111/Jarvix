"""User-requested computer context snapshots; no passive app or clipboard tracking."""
from __future__ import annotations

import threading
import copy
import time
import uuid
from datetime import datetime, timedelta, timezone

from jarvix.capabilities.schema import BOOL, ID, array, integer, register, string
from jarvix.runtime import check_cancelled
from jarvix.services import required_text
from jarvix.storage import now_iso


class ContextService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._current = {"conversation_id": None, "project_id": None, "workspace_id": None, "selected_files": []}
        self._session_items = {}
        self._local_session = uuid.uuid4().hex

    def _enabled(self):
        if not self.s.settings.get("context.enabled", False):
            # Turning off context also invalidates temporary entries at next access.
            with self._lock:
                self._session_items.clear()
            raise PermissionError("Enable explicit context snapshots in Settings first.")
        check_cancelled()

    def _session_key(self, conversation_id):
        value = self._current["conversation_id"] if conversation_id is None else conversation_id
        if value:
            if not self.s.db.query("SELECT id FROM conversations WHERE id=?", (value,)):
                raise ValueError("Unknown conversation.")
            return value
        return self._local_session

    def _prune(self):
        current = time.monotonic()
        for key in list(self._session_items):
            self._session_items[key] = {id: item for id, item in self._session_items[key].items()
                                        if item["deadline"] > current}
            if not self._session_items[key]:
                del self._session_items[key]

    def remember_session(self, content, conversation_id=None, ttl_seconds=1800, source="Explicit user context"):
        """Keep user supplied context only in bounded memory, never in RecordStore or chat history."""
        self._enabled()
        content = required_text(content, "Session context", 4000)
        source = required_text(source, "Source", 200)
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 28800:
            raise ValueError("Session context expires after 30 seconds to 8 hours.")
        with self._lock:
            self._prune()
            key = self._session_key(conversation_id)
            if key not in self._session_items and len(self._session_items) >= 32:
                raise ValueError("Clear an existing session before creating more temporary context.")
            bucket = self._session_items.setdefault(key, {})
            if len(bucket) >= 100:
                raise ValueError("This session already has 100 temporary context entries; remove some first.")
            record_id = uuid.uuid4().hex
            stamp = datetime.now(timezone.utc)
            expires_at = (stamp + timedelta(seconds=ttl_seconds)).isoformat()
            bucket[record_id] = {"id": record_id, "content": content, "source": source,
                                 "created_at": stamp.isoformat(), "expires_at": expires_at,
                                 "deadline": time.monotonic() + ttl_seconds}
        return {"id": record_id, "stored": False, "expires_at": expires_at,
                "automatic_cloud_sharing": False}

    def session(self, conversation_id=None, cursor=0, limit=10):
        self._enabled()
        if (not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10):
            raise ValueError("Invalid session context page.")
        with self._lock:
            self._prune()
            key = self._session_key(conversation_id)
            items = [{k: v for k, v in item.items() if k != "deadline"}
                     for item in self._session_items.get(key, {}).values()]
        end = cursor + limit
        return {"items": items[cursor:end], "next_cursor": end if end < len(items) else None,
                "total": len(items), "stored": False, "automatic_cloud_sharing": False,
                "scope": "conversation" if key != self._local_session else "current local session"}

    def forget_session(self, id=None, conversation_id=None):
        # Removal remains available when the context setting is off.
        with self._lock:
            self._prune()
            key = self._session_key(conversation_id)
            if id is None:
                count = len(self._session_items.pop(key, {}))
            else:
                count = int(self._session_items.get(key, {}).pop(id, None) is not None)
        return {"removed": count, "stored": False}

    def clear(self):
        with self._lock:
            self._session_items.clear()
            self._current = {"conversation_id": None, "project_id": None, "workspace_id": None, "selected_files": []}
            self._local_session = uuid.uuid4().hex
        return {"cleared": True, "stored": False}

    def set_current(self, conversation_id=None, project_id=None, selected_files=None, workspace_id=None):
        with self._lock:
            updated = copy.deepcopy(self._current)
            if conversation_id is not None:
                if conversation_id and not self.s.db.query("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
                    raise ValueError("Unknown conversation.")
                if conversation_id != self._current["conversation_id"]:
                    # Selections from one conversation must not bleed into the next.
                    updated.update(project_id=None, workspace_id=None, selected_files=[])
                updated["conversation_id"] = conversation_id or None
            if project_id is not None:
                if project_id and not self.s.db.query("SELECT id FROM projects WHERE id=?", (project_id,)):
                    raise ValueError("Unknown project.")
                updated["project_id"] = project_id or None
            if workspace_id is not None:
                if workspace_id:
                    self.s.records.get("workspace", workspace_id)
                updated["workspace_id"] = workspace_id or None
            if selected_files is not None:
                if len(selected_files) > 20:
                    raise ValueError("Select at most 20 files.")
                updated["selected_files"] = [str(self.s.files.path(value)) for value in selected_files]
            self._current = updated
        return {"updated": True, "stored": False}

    def inspect(self, include_clipboard=False, include_selected_files=False, include_session=False):
        self._enabled()
        with self._lock:
            selected = copy.deepcopy(self._current)
        window_result = self.s.execute_tool("windows.foreground", {})
        window = window_result.data if window_result.ok else None
        workspace_id = selected["workspace_id"] or self.s.settings.get("workspace.active_id", "")
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
        session = self.session(selected["conversation_id"] or "", limit=5) if include_session else {"items": [], "next_cursor": None}
        return {"captured_at": now_iso(), "active_window": window, "workspace": workspace,
                "project": project[0] if project else None, "conversation_id": selected["conversation_id"],
                "selected_files": files, "selected_files_source": "Explicit selection inside Jarvix",
                "clipboard_type": clipboard, "session_context": session["items"],
                "session_context_next_cursor": session["next_cursor"],
                "stored": False, "automatic_cloud_sharing": False}


def setup(s, registry):
    s.context = service = ContextService(s)
    register(registry, "context.inspect", "Take one explicit, opt-in context snapshot. Contains no clipboard text and is never automatically shared.",
             {"include_clipboard": BOOL, "include_selected_files": BOOL, "include_session": BOOL}, (), service.inspect)
    register(registry, "context.select", "Explicitly select a known project/conversation/allowed files for this session's context.",
             {"conversation_id": string(160, 0), "project_id": string(160, 0), "workspace_id": string(160, 0),
              "selected_files": array(string(), 20)}, (), service.set_current, 2)
    register(registry, "context.session_remember", "Keep explicit text in this conversation's temporary local context. Fresh confirmation; expires automatically, never persists or shares automatically.",
             {"content": string(4000), "conversation_id": string(160, 0), "ttl_seconds": integer(30, 28800), "source": string(200)},
             ("content",), service.remember_session, 3, "context.write")
    register(registry, "context.session_inspect", "Inspect unexpired temporary context for one explicit conversation or the current session only.",
             {"conversation_id": string(160, 0), "cursor": integer(0, 100), "limit": integer(1, 10)}, (), service.session)
    register(registry, "context.session_forget", "Remove one temporary context entry, or clear the specified/current conversation's temporary context.",
             {"id": ID, "conversation_id": string(160, 0)}, (), service.forget_session, 2, "context.write")
    register(registry, "context.clear", "Clear all ephemeral session text and explicit file/project/conversation/workspace selections from memory.",
             {}, (), service.clear, 2, "context.write")
