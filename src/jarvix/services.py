"""Application use cases composed behind one Qt-independent facade."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import copy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import psutil
from platformdirs import user_data_path

from jarvix.domain import ProviderError, ToolResult
from jarvix.records import RecordStore
from jarvix.runtime import check_cancelled, consume_step, operation
from jarvix.orchestrator import Orchestrator, bounded_history
from jarvix.security import CredentialVault, PermissionService
from jarvix.storage import Database, Repository, SettingsRepository, now_iso


def required_text(value: str, label: str, limit: int = 20000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must contain text.")
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError(f"{label} must contain 1–{limit:,} characters.")
    return value


class Services:
    # Background work must stay narrow even if a future capability is accidentally
    # registered with a permissive level. The host boundary—not just the routine
    # editor—enforces this for legacy routines and nested calls alike.
    SAFE_AUTOMATIONS = frozenset({"system.status", "system.processes", "tasks.list", "projects.list"})
    UNATTENDED_TOOL_ALLOWLIST = SAFE_AUTOMATIONS | frozenset({"notifications.create"})

    def __init__(self, data_dir: Path | None = None, vault=None):
        self.data_dir = Path(data_dir or os.environ.get("JARVIX_DATA_DIR") or user_data_path("Jarvix", appauthor=False))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "jarvix.db")
        self.repository = Repository(self.db)
        self.settings = SettingsRepository(self.db)
        self.records = RecordStore(self.db)
        self.vault = vault or CredentialVault()
        self.permissions = PermissionService(self.db, self.repository)
        from jarvix.tools import build_registry
        from jarvix.voice import VoiceService
        self.registry = build_registry(self)
        self.voice = VoiceService()
        self.orchestrator = Orchestrator(self.registry, self.permissions, self.repository, self.execute_tool)
        self.latest_context: dict = {}
        self._chat_lock = threading.Lock()
        self._automation_lock = threading.Lock()
        self._file_scan_lock = threading.Lock()
        self._roots_lock = threading.RLock()
        from jarvix.capabilities import files, developer, computer, productivity, browser, workspaces
        from jarvix.capabilities import notifications, automations, chat_management, integration, catalog
        from jarvix.speech_input import SpeechInputService
        for module in (files, developer, computer, productivity, browser, workspaces,
                       notifications, automations, chat_management, integration, catalog):
            module.setup(self, self.registry)
        self.microphone = SpeechInputService(self)

    def execute_tool(self, name, arguments, approve=None, cancel=None, on_event=None):
        """Single host enforcement boundary for UI, workspaces and automation actions."""
        arguments = copy.deepcopy(arguments)
        invalid = self.registry.validate(name, arguments)
        if invalid:
            return invalid
        spec = self.registry.get(name)
        try:
            with operation(cancel, approve, timeout=self.settings.get("agent.timeout_seconds", 120)) as context:
                consume_step()
                if context.unattended and name not in self.UNATTENDED_TOOL_ALLOWLIST:
                    self.repository.audit("permission", f"{name}: unavailable to unattended routine")
                    return ToolResult(False,
                                      error="Unattended routines are limited to safe local summaries and notifications.",
                                      sensitivity="public")
                preview_arguments = arguments
                app_preview = None
                if name == "apps.open":
                    configured = self.apps.arguments_for(arguments["id"])
                    if configured:
                        rows = self.db.query("SELECT path FROM apps WHERE id=?", (arguments["id"],))
                        if not rows:
                            return ToolResult(False, error="Registered application is unavailable.")
                        app_preview = (arguments["id"], rows[0]["path"], tuple(configured))
                        spec = replace(spec, permission_level=3, description=
                            "Launch this exact executable and configured arguments. Arguments may execute commands.")
                        preview_arguments = {**arguments, "executable": rows[0]["path"], "arguments": configured}
                approval = approve if approve is not None else context.approve
                if not self.permissions.authorize(spec, preview_arguments, approval):
                    return ToolResult(False, error="Permission denied or the required access switch is off.", sensitivity="public")
                check_cancelled()
                if on_event:
                    on_event("tool", {"name": name, "status": "Running"})
                previous_app = context.approved_app
                context.approved_app = app_preview
                try:
                    result = self.registry.execute(name, arguments)
                finally:
                    context.approved_app = previous_app
                self.repository.audit("tool", f"{name}: {'completed' if result.ok else 'failed'}")
                self.records.put("action_history", {"tool": name, "ok": result.ok,
                                                   "permission_level": spec.permission_level})
                return result
        except InterruptedError:
            return ToolResult(False, error="Operation stopped or timed out.")

    def list_notes(self):
        return self.repository.list("notes")

    def save_note(self, title: str, body: str, note_id: str | None = None):
        title = required_text(title, "Title", 200)
        if len(body) > 100000:
            raise ValueError("Notes are limited to 100,000 characters.")
        record_id, stamp = note_id or self.repository.new_id(), now_iso()
        if note_id and hasattr(self, "productivity"):
            self.productivity.snapshot_note(note_id)
        self.db.execute("INSERT INTO notes VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,updated_at=excluded.updated_at",
                        (record_id, title, body, stamp, stamp))
        self.repository.audit("note", "Note saved locally")
        return record_id

    def delete_note(self, record_id):
        self.repository.delete("notes", record_id)
        self.repository.audit("note", "Note deleted")

    def list_memories(self):
        return self.repository.list("memories")

    def add_memory(self, content: str):
        record_id = self.repository.new_id()
        self.db.execute("INSERT INTO memories VALUES (?,?,?)", (record_id, required_text(content, "Memory", 5000), now_iso()))
        self.repository.audit("memory", "Explicit memory saved locally")
        return record_id

    def delete_memory(self, record_id):
        self.repository.delete("memories", record_id)
        self.repository.audit("memory", "Memory deleted")

    def list_tasks(self):
        return self.repository.list("tasks")

    def add_task(self, title: str, due_at: str | None = None):
        if due_at:
            try:
                parsed = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Use an ISO-8601 reminder date and time.") from exc
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            due_at = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        record_id = self.repository.new_id()
        self.db.execute("INSERT INTO tasks(id,title,due_at,created_at) VALUES (?,?,?,?)",
                        (record_id, required_text(title, "Task", 500), due_at or None, now_iso()))
        self.repository.audit("task", "Task created locally")
        return record_id

    def complete_task(self, record_id):
        if hasattr(self, "productivity"):
            result = self.productivity.complete_task(record_id)
            if hasattr(self, "notifications"):
                self.notifications.create("Task completed", "Your completed-task history has been updated.", "task")
            return result
        self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (record_id,))
        self.repository.audit("task", "Task completed")

    def delete_task(self, record_id):
        self.repository.delete("tasks", record_id)

    def due_reminders(self):
        stamp = now_iso()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = [dict(row) for row in conn.execute("SELECT * FROM tasks WHERE status='open' AND due_at<=? AND reminded_at IS NULL", (stamp,))]
            for row in rows:
                conn.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (stamp, row["id"]))
        return rows

    def list_projects(self):
        return self.repository.list("projects")

    def add_project(self, name, path):
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_dir():
            raise ValueError("Select a project directory.")
        record_id = self.repository.new_id()
        self.db.execute("INSERT INTO projects VALUES (?,?,?,?)", (record_id, required_text(name, "Project name", 200), str(target), now_iso()))
        return record_id

    def list_apps(self):
        return self.repository.list("apps")

    def add_app(self, name, path):
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_file() or (os.name == "nt" and target.suffix.lower() != ".exe"):
            raise ValueError("Select an executable application (.exe on Windows).")
        if os.name != "nt" and not os.access(target, os.X_OK):
            raise ValueError("The application must be executable.")
        record_id = self.repository.new_id()
        self.db.execute("INSERT INTO apps VALUES (?,?,?,?)", (record_id, required_text(name, "App name", 120), str(target), now_iso()))
        return record_id

    def launch_app(self, record_id):
        rows = self.db.query("SELECT * FROM apps WHERE id=?", (record_id,))
        if not rows:
            raise ValueError("Register this application in Apps first.")
        path = Path(rows[0]["path"]).resolve(strict=True)
        if not path.is_file() or (os.name == "nt" and path.suffix.lower() != ".exe"):
            raise ValueError("The registered executable is unavailable.")
        arguments = self.apps.arguments_for(record_id) if hasattr(self, "apps") else []
        if arguments:
            from jarvix.runtime import CURRENT
            context = CURRENT.get()
            if context is None or context.approved_app != (record_id, rows[0]["path"], tuple(arguments)):
                raise PermissionError("Launch arguments require a fresh matching confirmation.")
        process = subprocess.Popen([str(path), *arguments], shell=False, cwd=path.parent,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.repository.audit("app", "Registered application launched")
        if hasattr(self, "apps"):
            self.apps.record_launch(record_id)
        return {"launched": rows[0]["name"], "pid": process.pid}

    def file_roots(self):
        return self.settings.get("files.roots", [])

    def add_file_root(self, path):
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_dir():
            raise ValueError("Select a directory to index.")
        with self._roots_lock:
            roots = self.file_roots()
            if str(target) not in roots:
                self.settings.set("files.roots", [*roots, str(target)])

    def remove_file_root(self, path):
        with self._roots_lock:
            self.settings.set("files.roots", [root for root in self.file_roots() if root != path])
            self.db.execute("DELETE FROM files WHERE root=?", (path,))

    def _valid_roots(self):
        from jarvix.capabilities.files import _linked
        roots = []
        for saved in self.file_roots():
            try:
                original = Path(saved)
                if (original.is_dir() and original.resolve(strict=True) == original
                        and not any(_linked(part) for part in (original, *original.parents))):
                    roots.append(original)
            except (OSError, RuntimeError):
                continue
        return roots

    def scan_files(self):
        from jarvix.capabilities.files import _linked
        from jarvix.tools.builtin import _is_sensitive
        if not self._file_scan_lock.acquire(blocking=False):
            return {"count": 0, "busy": True}
        try:
            records = []
            excluded = {"node_modules", ".git", ".venv", "__pycache__", "venv", "dist", "build"}
            for root in self._valid_roots():
                root_str = str(root)
                visited = {root}
                for directory, dirs, filenames in os.walk(root, followlinks=False):
                    check_cancelled()
                    permitted_dirs = []
                    for name in dirs:
                        candidate = Path(directory, name)
                        try:
                            resolved = candidate.resolve(strict=True)
                            if (name not in excluded and not name.startswith(".") and not _linked(candidate)
                                    and not _is_sensitive(candidate)
                                    and resolved.is_relative_to(root) and resolved not in visited):
                                permitted_dirs.append(name)
                                visited.add(resolved)
                        except (OSError, RuntimeError):
                            continue
                    dirs[:] = permitted_dirs
                    for name in filenames:
                        try:
                            candidate = Path(directory, name)
                            if _linked(candidate) or _is_sensitive(candidate):
                                continue
                            path = candidate.resolve(strict=True)
                            if not path.is_relative_to(root) or not path.is_file():
                                continue
                            stat = path.stat()
                            records.append((str(path), name, root_str, datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"), stat.st_size))
                        except (OSError, RuntimeError):
                            continue
                        if len(records) >= 20000:
                            break
                    if len(records) >= 20000:
                        break
                if len(records) >= 20000:
                    break
            with self._roots_lock:
                valid = {str(root) for root in self._valid_roots()}
                records = [row for row in records if row[2] in valid]
                with self.db.connect() as conn:
                    conn.execute("DELETE FROM files")
                    conn.executemany("INSERT OR IGNORE INTO files VALUES (?,?,?,?,?)", records)
            self.repository.audit("files", f"Local file index refreshed: {len(records)} entries")
            return {"count": len(records), "limited": len(records) >= 20000}
        finally:
            self._file_scan_lock.release()

    def list_files(self, query=""):
        from jarvix.tools.builtin import _approved_path
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.db.query("SELECT * FROM files WHERE name LIKE ? ESCAPE '\\' ORDER BY modified_at DESC LIMIT 300", (f"%{escaped}%",))
        roots = [str(root) for root in self._valid_roots()]
        return [row for row in rows if _approved_path(row["path"], roots) is not None]

    def list_conversations(self):
        return self.repository.list("conversations")

    def new_conversation(self, title="New conversation"):
        record_id, stamp = self.repository.new_id(), now_iso()
        self.db.execute("INSERT INTO conversations VALUES (?,?,?,?)", (record_id, required_text(title, "Conversation title", 200), stamp, stamp))
        return record_id

    def conversation_messages(self, record_id):
        return self.db.query("SELECT role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id", (record_id,))

    def provider_status(self):
        return {provider: bool(self.vault.get(provider)) for provider in ("openai", "gemini")}

    def set_api_key(self, provider_id, key):
        self.vault.set(provider_id, key)
        self.repository.audit("settings", f"{provider_id}: credential saved in OS vault")

    def delete_api_key(self, provider_id):
        self.vault.delete(provider_id)

    def enabled_tools(self):
        return self.settings.get("tools.enabled", [spec.name for spec in self.registry.specs()])

    def context_preview(self, conversation_id, provider_id, model):
        from dataclasses import asdict
        from jarvix.capabilities.catalog import initial_tools
        return {"provider": provider_id, "model": model,
                "messages": [asdict(message) for message in bounded_history(self.conversation_messages(conversation_id))],
                "tools": [asdict(spec) for spec in initial_tools([spec for spec in self.registry.specs()
                                                                if spec.name in self.enabled_tools()])],
                "policy": "The next submitted message is added to this bounded history. Tool results require disclosure approval. No unrelated local records are attached."}

    def chat(self, text, conversation_id, provider_id, model, approve, on_event, cancel):
        text = required_text(text, "Message", 16000)
        if not self._chat_lock.acquire(blocking=False):
            raise RuntimeError("A request is already running.")
        try:
            from jarvix.providers import create_provider
            self.repository.append_message(conversation_id, "user", text)
            self.db.execute("UPDATE conversations SET title=? WHERE id=? AND title='New conversation'", (text[:65], conversation_id))
            key = self.vault.get(provider_id)
            if not key:
                answer = f"Configure your {provider_id.title()} API key in Integrations to use AI chat. Notes, tasks, memory, files and system tools are available locally now."
            else:
                provider = create_provider(provider_id, key)
                try:
                    with operation(cancel, approve, timeout=self.settings.get("agent.timeout_seconds", 120)):
                        answer = self.orchestrator.run(provider, required_text(model, "Model", 150),
                                bounded_history(self.conversation_messages(conversation_id)), self.enabled_tools(),
                                approve, on_event, cancel, lambda value: setattr(self, "latest_context", value),
                                max_rounds=self.settings.get("agent.max_rounds", 6))
                except ProviderError as exc:
                    self.repository.audit("provider", f"{provider_id}: request failed")
                    answer = str(exc)
                except InterruptedError:
                    answer = "Request stopped or timed out. Completed actions remain in Activity."
                finally:
                    if hasattr(provider, "close"):
                        provider.close()
            self.repository.append_message(conversation_id, "assistant", answer)
            self.repository.audit("chat", f"{provider_id}: conversation updated")
            return answer
        finally:
            self._chat_lock.release()

    def system_snapshot(self):
        memory = psutil.virtual_memory()
        processes = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                if proc.info["memory_info"]:
                    processes.append({"pid": proc.info["pid"], "name": proc.info["name"] or "Unknown",
                                      "memory_mb": round(proc.info["memory_info"].rss / 1048576, 1)})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda item: item["memory_mb"], reverse=True)
        return {"cpu_percent": psutil.cpu_percent(interval=0.1), "memory_percent": memory.percent,
                "memory_used_gb": round(memory.used / 1073741824, 1),
                "memory_total_gb": round(memory.total / 1073741824, 1), "processes": processes[:20]}

    def activity(self, limit=50):
        return self.db.query("SELECT kind,summary,created_at FROM activity ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),))

    def speak(self, text):
        return self.voice.speak(text, rate=int(self.settings.get("speech.rate", 175)),
                                voice=self.settings.get("speech.voice", ""))

    def stop_speaking(self):
        self.voice.stop()

    def open_data_folder(self):
        """Explicit Settings action; this never grants AI access to the profile."""
        path = self.data_dir.resolve(strict=True)
        if os.name == "nt":
            os.startfile(str(path))
        else:
            import sys
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"opened": True}

    def create_backup(self):
        """Create a self-contained, verified local database snapshot in the profile."""
        from jarvix.capabilities.files import _linked

        profile = self.data_dir.resolve(strict=True)
        directory = profile / "backups"
        if _linked(directory):
            raise PermissionError("The backup directory must remain inside the Jarvix profile.")
        directory.mkdir(exist_ok=True)
        resolved = directory.resolve(strict=True)
        if _linked(directory) or not resolved.is_relative_to(profile):
            raise PermissionError("The backup directory must remain inside the Jarvix profile.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = resolved / f"jarvix-backup-{stamp}-{self.repository.new_id()[:8]}.db"
        self.db.backup(target)
        self.repository.audit("backup", "Consistent local database backup created")
        return {"path": str(target), "size_bytes": target.stat().st_size, "credentials_included": False}

    def list_automations(self):
        rows = self.repository.list("automations")
        for row in rows:
            row["arguments"] = json.loads(row["arguments"])
            row["enabled"] = bool(row["enabled"])
        return rows

    def add_automation(self, name, tool_name, arguments, interval_minutes):
        if tool_name not in self.SAFE_AUTOMATIONS:
            raise ValueError("Automations support only local system, task and project summaries.")
        if not isinstance(interval_minutes, int) or isinstance(interval_minutes, bool) or not 1 <= interval_minutes <= 10080:
            raise ValueError("Interval must be between 1 minute and 7 days.")
        invalid = self.registry.validate(tool_name, arguments)
        if invalid:
            raise ValueError(invalid.error)
        record_id = self.repository.new_id()
        self.db.execute("INSERT INTO automations(id,name,tool_name,arguments,interval_minutes,created_at) VALUES (?,?,?,?,?,?)",
                        (record_id, required_text(name, "Automation name", 160), tool_name, json.dumps(arguments), interval_minutes, now_iso()))
        self.repository.audit("automation", "Local automation explicitly registered")
        return record_id

    def toggle_automation(self, record_id, enabled):
        self.db.execute("UPDATE automations SET enabled=? WHERE id=?", (int(bool(enabled)), record_id))

    def delete_automation(self, record_id):
        self.repository.delete("automations", record_id)

    def run_due_automations(self):
        if not self.settings.get("automations.enabled", True):
            return []
        if not self._automation_lock.acquire(blocking=False):
            return []
        completed = []
        try:
            current = datetime.now(timezone.utc)
            for row in self.list_automations():
                if not row["enabled"] or row["tool_name"] not in self.SAFE_AUTOMATIONS:
                    continue
                last = datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None
                if last and (current - last).total_seconds() < row["interval_minutes"] * 60:
                    continue
                result = self.execute_tool(row["tool_name"], row["arguments"], approve=lambda _: False)
                self.db.execute("UPDATE automations SET last_run_at=? WHERE id=?", (now_iso(), row["id"]))
                self.settings.set(f"automation.result.{row['id']}", result.as_dict())
                self.repository.audit("automation", f"{row['tool_name']}: {'completed locally' if result.ok else 'failed'}")
                completed.append({"id": row["id"], "name": row["name"], "ok": result.ok})
            if hasattr(self, "automation"):
                self.automation.tick()
            return completed
        finally:
            self._automation_lock.release()

    def close(self):
        self.voice.close()
        if hasattr(self, "integrations"):
            self.integrations.close()
        if hasattr(self, "microphone"):
            self.microphone.close()
        if hasattr(self, "developer") and hasattr(self.developer, "close"):
            self.developer.close()
