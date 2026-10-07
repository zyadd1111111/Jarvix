"""User-requested computer context snapshots; no passive app or clipboard tracking."""
from __future__ import annotations

import threading
import copy
import time
import uuid
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvix.capabilities.productivity import metadata, timestamp
from jarvix.capabilities.schema import BOOL, ID, array, integer, register, string, schema, enum
from jarvix.runtime import CURRENT, check_cancelled, operation
from jarvix.services import required_text
from jarvix.storage import now_iso

PROFILE_FIELDS = {"project_id": "project", "workspace_id": "workspace", "mission_id": "mission",
                  "conversation_id": "conversation"}
SESSION_STATE = schema({"goal": string(500), "project_id": ID, "active_files": array(string(4096), 20),
    "unfinished_steps": array(string(300), 20), "temporary_decisions": array(string(300), 10),
    "recent_results": array(schema({"tool": string(100), "ok": BOOL, "summary": string(500)}, ("tool", "ok")), 10)})


def empty_selection():
    return {**dict.fromkeys(PROFILE_FIELDS), "selected_files": [], "memory_ids": [], "profile_id": None}


class ContextService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._current = empty_selection()
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

    def _session_access(self, conversation_id):
        self._enabled()
        self._profile_access("context.session_inspect")
        key = self._session_key(conversation_id)
        if key != self._local_session:
            self._profile_access("conversations.search")
        return key

    def _state_access(self, state):
        if state is None:
            return
        if state.get("project_id"):
            self.s.context_graph.resolve("project", state["project_id"])
        for path in state.get("active_files", []):
            self.s.context_graph.resolve("file", path)
        for result in state.get("recent_results", []):
            self._profile_access(result["tool"])
            self.s.registry.get(result["tool"])

    def _live_session_entry(self, id, conversation_id):
        with self._lock:
            key = self._session_access(conversation_id)
            self._prune()
            item = copy.deepcopy(self._session_items.get(key, {}).get(id))
        if not item:
            raise ValueError("Temporary context expired or is unavailable.")
        self._state_access(item.get("state"))
        return key, item

    def remember_session(self, content, conversation_id=None, ttl_seconds=1800, source="Explicit user context",
                         state=None, expires_at=None):
        """Keep user supplied context only in bounded memory, never in RecordStore or chat history."""
        self._enabled()
        content = required_text(content, "Session context", 4000)
        source = required_text(source, "Source", 200)
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 28800:
            raise ValueError("Session context expires after 30 seconds to 8 hours.")
        stamp = datetime.now(timezone.utc)
        if expires_at is not None:
            ttl_seconds = (timestamp(expires_at) - stamp).total_seconds()
            if not 30 <= ttl_seconds <= 86400:
                raise ValueError("Choose an explicit expiration within 24 hours.")
        if state is not None:
            from jsonschema import Draft202012Validator
            if list(Draft202012Validator(SESSION_STATE).iter_errors(state)) or len(json.dumps(state)) > 8000:
                raise ValueError("Use bounded structured session state.")
            state = copy.deepcopy(state)
            if state.get("project_id"):
                self.s.context_graph.resolve("project", state["project_id"])
            state["active_files"] = [self.s.context_graph.resolve("file", path)["reference"]
                                     for path in state.get("active_files", [])]
            for result in state.get("recent_results", []):
                self._profile_access(result["tool"])
                self.s.registry.get(result["tool"])
        with self._lock:
            self._prune()
            key = self._session_key(conversation_id)
            if key not in self._session_items and len(self._session_items) >= 32:
                raise ValueError("Clear an existing session before creating more temporary context.")
            bucket = self._session_items.setdefault(key, {})
            if len(bucket) >= 100:
                raise ValueError("This session already has 100 temporary context entries; remove some first.")
            record_id = uuid.uuid4().hex
            expires_at = (stamp + timedelta(seconds=ttl_seconds)).isoformat()
            bucket[record_id] = {"id": record_id, "content": content, "source": source,
                                 "created_at": stamp.isoformat(), "expires_at": expires_at,
                                 "deadline": time.monotonic() + ttl_seconds}
            if state is not None:
                bucket[record_id]["state"] = state
        return {"id": record_id, "stored": False, "expires_at": expires_at,
                "automatic_cloud_sharing": False}

    def session(self, conversation_id=None, cursor=0, limit=10):
        if (not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10):
            raise ValueError("Invalid session context page.")
        with self._lock:
            key = self._session_access(conversation_id)
            self._prune()
            items = [{k: v for k, v in item.items() if k != "deadline"}
                     for item in self._session_items.get(key, {}).values()]
        items = copy.deepcopy(items)
        for item in items:
            state = item.get("state")
            if state is None:
                continue
            try:
                self._state_access(state)
            except (ValueError, OSError):
                item.pop("state")
                item["state_unavailable"] = True
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

    def promote_session(self, id, conversation_id=None, scope="permanent", project_id=None, content=None):
        self._profile_access("memory.remember_sensitive")
        key, item = self._live_session_entry(id, conversation_id)
        # Bind selection to the original bucket even when the active conversation changes.
        source_conversation = key if key != self._local_session else ""
        if scope not in {"permanent", "project"} or (scope == "project" and not project_id):
            raise ValueError("Choose permanent memory or an explicit project scope.")
        if scope == "project":
            self.s.context_graph.resolve("project", project_id)
        arguments = {"content": required_text(content if content is not None else item["content"], "Memory", 5000),
                     "source": "Explicit session promotion", "source_kind": "conversation" if source_conversation else "user",
                     "source_detail": "Temporary entry " + id}
        if scope == "project":
            arguments["project_id"] = project_id
        current = CURRENT.get()
        prior_checkpoint = current.checkpoint if current else None
        validating = False
        def checkpoint():
            nonlocal validating
            if validating:
                return  # Shared source checks call check_cancelled themselves.
            validating = True
            try:
                if prior_checkpoint:
                    prior_checkpoint()
                self._profile_access("memory.remember_sensitive")
                live_key, live = self._live_session_entry(id, source_conversation)
                if live_key != key or live != item:
                    raise PermissionError("Temporary context changed; inspect it before promotion.")
                if scope == "project":
                    self.s.context_graph.resolve("project", project_id)
            finally:
                validating = False
        # The host and original memory writer both checkpoint after approval and before persistence.
        with operation(checkpoint=checkpoint):
            result = self.s.execute_tool("memory.remember_sensitive", arguments)
        if result.ok and isinstance(result.data, dict) and result.data.get("saved"):
            try:
                result.data["temporary_entry_removed"] = bool(self.forget_session(id, source_conversation)["removed"])
            except (ValueError, OSError):
                result.data["temporary_entry_removed"] = False  # Preserve the committed memory receipt.
        return result

    def clear(self):
        with self._lock:
            self._session_items.clear()
            self._current = empty_selection()
            self._local_session = uuid.uuid4().hex
        return {"cleared": True, "stored": False}

    def set_current(self, conversation_id=None, project_id=None, selected_files=None, workspace_id=None, mission_id=None):
        with self._lock:
            updated = copy.deepcopy(self._current)
            if conversation_id is not None:
                if conversation_id and not self.s.db.query("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
                    raise ValueError("Unknown conversation.")
                if conversation_id != self._current["conversation_id"]:
                    # Selections from one conversation must not bleed into the next.
                    updated.update(project_id=None, workspace_id=None, mission_id=None, selected_files=[], memory_ids=[])
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
            if mission_id is not None:
                if mission_id:
                    self.s.context_graph.resolve("mission", mission_id)
                updated["mission_id"] = mission_id or None
            if any(value is not None for value in (conversation_id, project_id, selected_files, workspace_id, mission_id)):
                updated.update(profile_id=None, memory_ids=[])
            self._current = updated
        return {"updated": True, "stored": False}

    def _profile_access(self, name):
        if name not in self.s.enabled_tools() or self.s.db.query(
                "SELECT 1 FROM grants WHERE tool_name=? AND decision='deny'", (name,)):
            raise PermissionError("This context profile tool is disabled or denied.")
        check_cancelled()

    def _profile_references(self, profile):
        references = []
        for field, kind in PROFILE_FIELDS.items():
            if profile.get(field):
                references.append({"field": field, **self.s.context_graph.inspect_reference(kind, profile[field])})
        for id in profile.get("memory_ids", []):
            item = {"field": "memory_ids", "kind": "memory", "reference": id, "available": False}
            try:
                for name in ("memory.search", "memory.explain"):
                    self._profile_access(name)
                if not self.s.db.query("SELECT id FROM memories WHERE id=?", (id,)):
                    raise ValueError("Pinned memory no longer exists.")
                meta = metadata(self.s, "memory.meta", id)
                if meta.get("expires_at") and timestamp(meta["expires_at"]) <= datetime.now(timezone.utc):
                    raise ValueError("Pinned memory has expired.")
                for field in ("project_id", "workspace_id"):
                    if meta.get(field) and meta[field] != profile.get(field):
                        raise ValueError("Pinned memory belongs to another context scope.")
                    if meta.get(field):
                        self.s.context_graph.resolve(PROFILE_FIELDS[field], meta[field])
                item.update(available=True, expires_at=meta.get("expires_at"))
            except (ValueError, OSError):
                item["reason"] = "Memory unavailable, expired, outside this scope or denied."
            references.append(item)
        return references

    def save_profile(self, name, id=None, project_id=None, workspace_id=None, mission_id=None,
                     conversation_id=None, memory_ids=None):
        """Persist explicit references only; temporary session text is never copied."""
        with self._lock:
            current = self.s.records.get("context.profile", id) if id else {}
            if not id and len(self.s.records.list("context.profile")) >= 100:
                raise ValueError("Remove an existing profile before adding more than 100 profiles.")
            value = {"name": required_text(name, "Profile name", 120)}
            for field, incoming in (("project_id", project_id), ("workspace_id", workspace_id),
                                    ("mission_id", mission_id), ("conversation_id", conversation_id)):
                if incoming is not None and not isinstance(incoming, str):
                    raise ValueError("Profile references must be existing record IDs.")
                value[field] = (required_text(incoming, "Reference", 160) if incoming else None) if incoming is not None else current.get(field)
            pins = current.get("memory_ids", []) if memory_ids is None else memory_ids
            if not isinstance(pins, list) or len(pins) > 20:
                raise ValueError("Pin at most 20 existing memories.")
            value["memory_ids"] = list(dict.fromkeys(required_text(pin, "Memory ID", 160) for pin in pins))
            if any(not item["available"] for item in self._profile_references(value)):
                raise ValueError("Choose available references and unexpired memories in this profile's scope.")
            id = self.s.records.put("context.profile", value, id)
        return {"id": id, "saved": True, "stored": True, "activated": False, "automatic_cloud_sharing": False}

    def get_profile(self, id):
        self._profile_access("context.profiles.get")
        profile = self.s.records.get("context.profile", id)
        references = self._profile_references(profile)
        return {**profile, "references": references, "active": self._current.get("profile_id") == id,
                "unavailable": sum(not item["available"] for item in references), "stored": True,
                "automatic_cloud_sharing": False, "automatic_activation": False}

    def list_profiles(self):
        self._profile_access("context.profiles.list")
        return {"items": [{"id": profile["id"], "name": profile["name"], "updated_at": profile["updated_at"],
                            "active": self._current.get("profile_id") == profile["id"],
                            "unavailable": sum(not item["available"] for item in self._profile_references(profile))}
                           for profile in self.s.records.list("context.profile")[:100]],
                "automatic_cloud_sharing": False, "automatic_activation": False}

    def activate_profile(self, id):
        self._enabled()
        with self._lock:
            profile = self.get_profile(id)
            updated = empty_selection()
            updated["profile_id"] = id
            for item in profile["references"]:
                if item["available"]:
                    if item["field"] == "memory_ids":
                        updated["memory_ids"].append(item["reference"])
                    else:
                        updated[item["field"]] = item["reference"]
            self._current = updated
        return {"id": id, "selection": copy.deepcopy(updated), "unavailable": profile["unavailable"], "stored": False,
                "actions_replayed": False, "automatic_cloud_sharing": False}

    def unset_profile(self):
        with self._lock:
            self._current = empty_selection()
        return {"unset": True, "profiles_preserved": True, "stored": False}

    def delete_profile(self, id):
        with self._lock:
            self.s.records.get("context.profile", id)
            self.s.records.delete("context.profile", id)
            if self._current.get("profile_id") == id:
                self._current = empty_selection()
        return {"deleted": True, "linked_records_preserved": True}

    def selection(self):
        """Inspect current metadata only, rechecking revoked sources and expiring pins."""
        self._enabled()
        with self._lock:
            selected = copy.deepcopy(self._current)
        if selected["profile_id"]:
            try:
                self._profile_access("context.profiles.get")
                self.s.records.get("context.profile", selected["profile_id"])
            except (ValueError, OSError):
                return empty_selection()
        references = self._profile_references(selected)
        for item in references:
            if not item["available"]:
                if item["field"] == "memory_ids":
                    selected["memory_ids"].remove(item["reference"])
                else:
                    selected[item["field"]] = None
        selected["selected_files"] = [item["reference"] for path in selected["selected_files"]
                                      if (item := self.s.context_graph.inspect_reference("file", path))["available"]]
        return selected

    def inspect(self, include_clipboard=False, include_selected_files=False, include_session=False):
        self._enabled()
        selected = self.selection()
        window_result = self.s.execute_tool("windows.foreground", {})
        window = window_result.data if window_result.ok else None
        workspace_id = selected["workspace_id"] or self.s.settings.get("workspace.active_id", "")
        workspace = None
        if workspace_id:
            try:
                self.s.context_graph.resolve("workspace", workspace_id)
                saved = self.s.records.get("workspace", workspace_id)
                workspace = {"id": workspace_id, "name": saved["name"], "project_id": saved.get("project_id")}
            except (ValueError, OSError):
                pass
        project_id = selected["project_id"] or (workspace or {}).get("project_id")
        project = self.s.db.query("SELECT id,name,path FROM projects WHERE id=?", (project_id,)) if project_id and self.s.context_graph.inspect_reference("project", project_id)["available"] else []
        clipboard = None
        if include_clipboard:
            result = self.s.execute_tool("clipboard.classify", {})
            clipboard = result.data if result.ok else {"available": False}
        files = []
        explorer = None
        desktop = None
        selection_source = "Explicit selection inside Jarvix"
        if include_selected_files:
            for path in selected["selected_files"]:
                try:
                    files.append(str(self.s.files.path(path)))
                except (OSError, ValueError):
                    continue
            if (isinstance(window, dict) and window.get("handle") and window.get("process_id")
                    and self.s.settings.get("screenshots.enabled", False)
                    and hasattr(self.s, "app_adapters")):
                identity = {"handle": window["handle"], "process_id": window["process_id"]}
                process = self.s.execute_tool("processes.details", {"pid": window["process_id"]})
                if (process.ok and isinstance(process.data, dict)
                        and Path(process.data.get("executable", "")).name.casefold() == "explorer.exe"):
                    result = self.s.execute_tool("adapters.explorer_context", identity)
                    if result.ok and isinstance(result.data, dict):
                        current_folder = None
                        try:
                            folder = self.s.files.path(result.data.get("folder") or "")
                            if folder.is_dir():
                                current_folder = str(folder)
                        except (OSError, ValueError):
                            pass
                        selected_paths = []
                        for path in result.data.get("selected_files", [])[:20]:
                            try:
                                selected_paths.append(str(self.s.files.path(path)))
                            except (OSError, ValueError):
                                continue
                        explorer = {"folder": current_folder, "selected_files": selected_paths, **identity}
                        files = list(dict.fromkeys([*files, *selected_paths]))[:20]
                        selection_source += " + approved Explorer Shell COM selection"
                        result = self.s.execute_tool("windows.virtual_desktop", identity)
                        if result.ok:
                            desktop = result.data
        session = self.session(selected["conversation_id"] or "", limit=5) if include_session else {"items": [], "next_cursor": None}
        self._enabled()
        return {"captured_at": now_iso(), "active_window": window, "workspace": workspace,
                "project": project[0] if project else None, "conversation_id": selected["conversation_id"],
                "profile_id": selected["profile_id"], "mission_id": selected["mission_id"], "memory_ids": selected["memory_ids"],
                "selected_files": files, "selected_files_source": selection_source,
                "explorer": explorer, "virtual_desktop": desktop,
                "clipboard_type": clipboard, "session_context": session["items"],
                "session_context_next_cursor": session["next_cursor"],
                "stored": False, "automatic_cloud_sharing": False}


def setup(s, registry):
    s.context = service = ContextService(s)
    register(registry, "context.inspect", "Take one explicit, opt-in context snapshot. Contains no clipboard text and is never automatically shared.",
             {"include_clipboard": BOOL, "include_selected_files": BOOL, "include_session": BOOL}, (), service.inspect)
    register(registry, "context.select", "Explicitly select a known project/conversation/allowed files for this session's context.",
             {"conversation_id": string(160, 0), "project_id": string(160, 0), "workspace_id": string(160, 0),
              "mission_id": string(160, 0),
              "selected_files": array(string(), 20)}, (), service.set_current, 2)
    register(registry, "context.session_remember", "Keep explicit text in this conversation's temporary local context. Fresh confirmation; expires automatically, never persists or shares automatically.",
             {"content": string(4000), "conversation_id": string(160, 0), "ttl_seconds": integer(30, 28800), "source": string(200),
              "state": SESSION_STATE, "expires_at": string(40)},
             ("content",), service.remember_session, 3, "context.write")
    register(registry, "context.session_promote", "Explicitly promote selected temporary text to permanent or project memory. Fresh original memory confirmation, duplicate checks and source access remain required; never auto-promotes.",
             {"id": ID, "conversation_id": string(160, 0), "scope": enum("permanent", "project"),
              "project_id": ID, "content": string(5000)}, ("id",), service.promote_session, 3, "context.write")
    register(registry, "context.session_inspect", "Inspect unexpired temporary context for one explicit conversation or the current session only.",
             {"conversation_id": string(160, 0), "cursor": integer(0, 100), "limit": integer(1, 10)}, (), service.session)
    register(registry, "context.session_forget", "Remove one temporary context entry, or clear the specified/current conversation's temporary context.",
             {"id": ID, "conversation_id": string(160, 0)}, (), service.forget_session, 2, "context.write")
    register(registry, "context.clear", "Clear all ephemeral session text and explicit file/project/conversation/workspace selections from memory.",
             {}, (), service.clear, 2, "context.write")
    register(registry, "context.profiles.save", "Explicitly save/update a named local profile referencing existing project/workspace/Mission/conversation and up to 20 memories. Never copies temporary context or memory content; fresh confirmation.",
             {"name": string(120), "id": ID, **{field: string(160, 0) for field in PROFILE_FIELDS},
              "memory_ids": array(ID, 20)}, ("name",), service.save_profile, 3, "context.write")
    register(registry, "context.profiles.get", "Inspect an explicit saved context profile; linked permissions, roots, memory scope and expiry are revalidated without reading source content.",
             {"id": ID}, ("id",), service.get_profile)
    register(registry, "context.profiles.list", "List saved local context profiles and current reference availability. Profiles never activate automatically after restart.",
             {}, (), service.list_profiles)
    register(registry, "context.profiles.activate", "Explicitly apply only currently allowed profile metadata to this running session. Requires context opt-in; never captures apps, resumes actions or adds facts to AI messages.",
             {"id": ID}, ("id",), service.activate_profile, 2, "context.write")
    register(registry, "context.profiles.unset", "Clear current profile metadata selections, retaining saved profiles, linked records and temporary conversation context.",
             {}, (), service.unset_profile, 2, "context.write")
    register(registry, "context.profiles.delete", "Permanently forget one local context profile after fresh confirmation. Its original memories, conversations, project and Mission remain intact.",
             {"id": ID}, ("id",), service.delete_profile, 3, "context.write")
