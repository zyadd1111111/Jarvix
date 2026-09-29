"""Explicit event-driven routines using the same permission boundary as manual tools."""
import hashlib
import json
import math
import threading
from datetime import datetime, timezone

import psutil

from jarvix.capabilities.schema import BOOL, ID, array, enum, register, schema, string
from jarvix.runtime import check_cancelled, operation
from jarvix.domain import ToolResult
from jarvix.storage import now_iso

TRIGGERS = ("manual", "interval", "at_time", "jarvix_start", "app_start", "file_created",
            "folder_change", "battery_below", "cpu_above", "memory_above", "task_due")
ACTION = schema({"tool": string(80), "arguments": {"type": "object"}}, ("tool", "arguments"))


class AutomationService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.Lock()
        self._started = set()
        self._runs_lock = threading.Lock()
        self._running = set()

    def list(self):
        return self.s.records.list("routine")

    def _validate_actions(self, actions, *, unattended=False):
        if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
            raise ValueError("Use between 1 and 12 actions.")
        for action in actions:
            if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
                raise ValueError("Each action needs a tool and structured arguments.")
            spec = self.s.registry.get(action["tool"])
            if spec.name.startswith(("automations.", "workspaces.launch", "apps.group_launch")):
                raise ValueError("Recursive routine actions are not allowed.")
            if unattended and spec.name not in self.s.UNATTENDED_TOOL_ALLOWLIST:
                raise ValueError("Scheduled routines may only use safe local summaries and notifications.")
            if self.s.registry.validate(spec.name, action["arguments"]):
                raise ValueError("An action has invalid arguments.")

    def _config(self, trigger, config):
        if trigger not in TRIGGERS or not isinstance(config, dict):
            raise ValueError("Invalid trigger configuration.")
        allowed = {"interval": {"minutes"}, "at_time": {"at"}, "app_start": {"name"},
                   "folder_change": {"path"}, "file_created": {"path"},
                   "battery_below": {"threshold"}, "cpu_above": {"threshold"},
                   "memory_above": {"threshold"}}
        if set(config) - allowed.get(trigger, set()):
            raise ValueError("Unknown trigger configuration field.")
        config = dict(config)
        if trigger in {"folder_change", "file_created"}:
            value = config.get("path")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Choose an allowed trigger directory.")
            path = self.s.files.path(value)
            if not path.is_dir():
                raise ValueError("Choose an allowed trigger directory.")
            config["path"] = str(path)
        if trigger == "at_time":
            value = config.get("at")
            if not isinstance(value, str):
                raise ValueError("Provide an ISO-8601 scheduled time.")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Scheduled times need a timezone.")
            config["at"] = parsed.astimezone(timezone.utc).isoformat()
        if trigger == "interval":
            interval = config.get("minutes", 60)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 1 <= interval <= 10080:
                raise ValueError("Interval must be between 1 and 10080 minutes.")
            config["minutes"] = interval
        if trigger in {"battery_below", "cpu_above", "memory_above"}:
            threshold = config.get("threshold", 20 if trigger == "battery_below" else 80)
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
                raise ValueError("Threshold must be between 0 and 100 percent.")
            config["threshold"] = threshold
        if trigger == "app_start":
            name = config.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(c in name for c in "\0\r\n/\\"):
                raise ValueError("Provide an exact process filename, such as Code.exe.")
            config["name"] = name.strip()
        return config

    def save(self, name, actions, trigger="manual", config=None, id=None, enabled=True):
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("Routine name must contain 1 to 160 characters.")
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be a boolean.")
        if id:
            self.s.records.get("routine", id)
        config = self._config(trigger, {} if config is None else config)
        self._validate_actions(actions, unattended=trigger != "manual")
        record = {"name": name.strip(), "actions": actions, "trigger": trigger, "config": config,
                  "enabled": enabled, "last_run_at": None, "last_state": None}
        record_id = self.s.records.put("routine", record, id)
        return {"id": record_id, "action_count": len(actions)}

    def preview(self, id):
        row = self.s.records.get("routine", id)
        return {"name": row["name"], "trigger": row["trigger"], "actions": [
            {**action, "permission_level": self.s.registry.get(action["tool"]).permission_level}
            for action in row["actions"]]}

    def run(self, id, unattended=False):
        with self._runs_lock:
            if id in self._running:
                raise RuntimeError("This routine is already running.")
            self._running.add(id)
        try:
            with operation(unattended=unattended):
                return self._run(id, unattended)
        finally:
            with self._runs_lock:
                self._running.discard(id)

    def _run(self, id, unattended):
        if not self.s.settings.get("automations.enabled", True):
            raise PermissionError("Automations are disabled.")
        row = self.s.records.get("routine", id)
        self._validate_actions(row["actions"])
        outcomes = []
        for action in row["actions"]:
            try:
                check_cancelled()
            except InterruptedError:
                outcomes.append({"tool": action["tool"], "ok": False, "error": "Operation stopped or timed out."})
                break
            spec = self.s.registry.get(action["tool"])
            if unattended and spec.name not in self.s.UNATTENDED_TOOL_ALLOWLIST:
                outcomes.append({"tool": spec.name, "ok": False,
                                 "error": "Unattended routines are limited to safe local summaries and notifications."})
                break
            result = self.s.execute_tool(spec.name, action["arguments"], approve=(lambda _: False) if unattended else None)
            outcomes.append({"tool": spec.name, "ok": result.ok, "error": result.error})
            if not result.ok:
                break
        ok = len(outcomes) == len(row["actions"]) and all(item["ok"] for item in outcomes)
        row["last_run_at"] = now_iso()
        self.s.records.put("routine", row, id)
        self.s.records.put("routine_run", {"routine_id": id, "ok": ok, "actions": outcomes})
        self.s.repository.audit("automation", f"Routine {'completed' if ok else 'needs attention'}; {len(outcomes)} action(s)")
        if not ok:
            self.s.notifications.create("Automation needs attention", "Open local routine history for details.", "automation", "high")
        return {"ok": ok, "actions": outcomes}

    def toggle(self, id, enabled):
        row = self.s.records.get("routine", id)
        row["enabled"] = enabled
        self.s.records.put("routine", row, id)
        return {"enabled": enabled}

    def duplicate(self, id, name):
        row = self.s.records.get("routine", id)
        return self.save(name, row["actions"], row["trigger"], row["config"], enabled=False)

    def delete(self, id):
        self.s.records.delete("routine", id)
        return {"deleted": True}

    def history(self, id=""):
        return [row for row in self.s.records.list("routine_run") if not id or row["routine_id"] == id][:100]

    def templates(self):
        return [
            {"name": "Today's tasks at launch", "trigger": "jarvix_start", "actions": [{"tool": "tasks.list", "arguments": {}}]},
            {"name": "Low battery alert", "trigger": "battery_below", "config": {"threshold": 20},
             "actions": [{"tool": "notifications.create", "arguments": {"title": "Battery is below 20%", "category": "system"}}]},
            {"name": "Memory pressure alert", "trigger": "memory_above", "config": {"threshold": 90},
             "actions": [{"tool": "notifications.create", "arguments": {"title": "Memory use is high", "category": "system"}}]},
        ]

    def _state(self, row):
        trigger, config = row["trigger"], row["config"]
        if trigger == "app_start":
            name = config.get("name", "").casefold()
            for process in psutil.process_iter(["name"]):
                check_cancelled()
                try:
                    if name == (process.info["name"] or "").casefold():
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        if trigger in {"file_created", "folder_change"}:
            folder = self.s.files.path(config["path"])
            if not folder.is_dir():
                raise ValueError("Trigger folder unavailable.")
            items = []
            for entry in sorted(folder.iterdir(), key=lambda p: p.name)[:1000]:
                check_cancelled()
                try:
                    path = self.s.files.path(str(entry))
                    stat = path.stat()
                    items.append((path.name, stat.st_size, stat.st_mtime_ns))
                except (OSError, ValueError, PermissionError):
                    continue
            if trigger == "file_created":
                return [item[0] for item in items]
            return hashlib.sha256(json.dumps(items).encode()).hexdigest()
        threshold = config.get("threshold", 80)
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 100:
            raise ValueError("Invalid threshold.")
        if trigger == "battery_below":
            battery = psutil.sensors_battery()
            return bool(battery and not battery.power_plugged and battery.percent < threshold)
        if trigger == "cpu_above":
            return psutil.cpu_percent(interval=.1) > threshold
        if trigger == "memory_above":
            return psutil.virtual_memory().percent > threshold
        if trigger == "task_due":
            now = datetime.now(timezone.utc)
            due = []
            for task in self.s.list_tasks():
                if task["status"] != "open" or not task.get("due_at"):
                    continue
                parsed = datetime.fromisoformat(task["due_at"].replace("Z", "+00:00"))
                if parsed.tzinfo is not None and parsed <= now:
                    due.append(task["id"])
            return sorted(due)
        return None

    def tick(self):
        if not self.s.settings.get("automations.enabled", True) or not self._lock.acquire(blocking=False):
            return []
        results = []
        try:
            for row in self.list():
                if not row["enabled"] or row["trigger"] == "manual":
                    continue
                try:
                    check_cancelled()
                    trigger = row["trigger"]
                    row["config"] = self._config(trigger, row["config"])
                    last = datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None
                    now = datetime.now(timezone.utc)
                    previous = row.get("last_state")
                    if trigger == "interval":
                        fire = not last or (now - last).total_seconds() >= row["config"].get("minutes", 60) * 60
                    elif trigger == "jarvix_start":
                        fire = row["id"] not in self._started
                        self._started.add(row["id"])
                    elif trigger == "at_time":
                        fire = not last and now >= datetime.fromisoformat(row["config"]["at"].replace("Z", "+00:00"))
                    else:
                        state = self._state(row)
                        if trigger == "task_due":
                            fire = bool(set(state) - set(previous or []))
                        elif trigger == "file_created":
                            fire = previous is not None and bool(set(state) - set(previous))
                        elif trigger == "folder_change":
                            fire = previous is not None and state != previous
                        else:
                            fire = bool(state) and state != previous
                        row["last_state"] = state
                        self.s.records.put("routine", row, row["id"])
                    if fire:
                        results.append(self.run(row["id"], unattended=True))
                except InterruptedError:
                    raise
                except Exception:
                    self.s.repository.audit("error", "Automation trigger could not be evaluated")
                    self.s.records.put("routine_run", {"routine_id": row["id"], "ok": False,
                        "actions": [], "error": "Trigger evaluation failed; check configuration and allowed roots."})
            return results
        finally:
            self._lock.release()


def setup(s, registry):
    service = s.automation = AutomationService(s)
    def run_tool(id):
        result = service.run(id)
        return ToolResult(result["ok"], result, None if result["ok"] else "Routine stopped before every action completed.")
    register(registry, "automations.list", "List configured event routines.", {}, (), service.list)
    register(registry, "automations.save", "Create or edit a routine; sensitive actions still require interactive confirmation.",
             {"name": string(160), "actions": array(ACTION, 12), "trigger": enum(*TRIGGERS),
              "config": {"type": "object"}, "id": ID, "enabled": BOOL}, ("name", "actions"), service.save, 2)
    register(registry, "automations.preview", "Preview every routine action and its permission level.", {"id": ID}, ("id",), service.preview)
    register(registry, "automations.run", "Run a configured routine with permission checks on every action.", {"id": ID}, ("id",), run_tool, 2)
    register(registry, "automations.toggle", "Enable or pause a routine.", {"id": ID, "enabled": BOOL}, ("id", "enabled"), service.toggle, 2)
    register(registry, "automations.duplicate", "Copy a routine, initially paused.", {"id": ID, "name": string(160)}, ("id", "name"), service.duplicate, 2)
    register(registry, "automations.delete", "Delete a local routine.", {"id": ID}, ("id",), service.delete, 3)
    register(registry, "automations.history", "Read routine outcomes without private action values.", {"id": string(160, 0)}, (), service.history)
    register(registry, "automations.templates", "List usable routine templates.", {}, (), service.templates)
