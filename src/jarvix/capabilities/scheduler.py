"""Opt-in, signed workflow schedules; Windows runs only this fixed Jarvix runner.

Task schema: https://learn.microsoft.com/windows/win32/taskschd/task-scheduler-schema
Tasks use the current interactive user, least privilege, and no stored password.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from jarvix.capabilities.schema import ID, array, integer, register, string
from jarvix.runtime import check_cancelled
from jarvix.security import CredentialVault
from jarvix.storage import now_iso

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
FIELDS = ("workflow_id", "fingerprint", "time", "weekdays", "profile", "command", "arguments", "dependencies")


def task_xml(row):
    ET.register_namespace("", NS)
    def add(parent, tag, text=None):
        node = ET.SubElement(parent, "{" + NS + "}" + tag)
        node.text = text
        return node
    root = ET.Element("{" + NS + "}Task", {"version": "1.2"})
    info = add(root, "RegistrationInfo")
    add(info, "Description", "Jarvix: one explicitly approved workflow. Remove in Jarvix or Task Scheduler.")
    trigger = add(add(root, "Triggers"), "CalendarTrigger")
    today = datetime.now().replace(hour=int(row["time"][:2]), minute=int(row["time"][3:]), second=0, microsecond=0)
    add(trigger, "StartBoundary", (today - timedelta(days=1)).isoformat())
    add(trigger, "Enabled", "true")
    week = add(trigger, "ScheduleByWeek")
    add(week, "WeeksInterval", "1")
    days = add(week, "DaysOfWeek")
    for day in row["weekdays"]:
        add(days, DAYS[day])
    principal = add(add(root, "Principals"), "Principal")
    principal.set("id", "JarvixUser")
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(root, "Settings")
    for key, value in {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
                       "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "true",
                       "ExecutionTimeLimit": "PT10M", "Enabled": "true"}.items():
        add(settings, key, value)
    actions = add(root, "Actions")
    actions.set("Context", "JarvixUser")
    action = add(actions, "Exec")
    add(action, "Command", row["command"])
    add(action, "Arguments", row["arguments"])
    add(action, "WorkingDirectory", str(Path(row["command"]).parent))
    return ET.tostring(root, encoding="unicode")


class WindowsScheduler:
    def _run(self, args):
        if sys.platform != "win32":
            raise RuntimeError("Closed-app scheduling requires Windows Task Scheduler.")
        check_cancelled()
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "schtasks.exe"), *args],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Windows Task Scheduler rejected the request. Check task permissions.")
        return result.stdout

    def install(self, name, xml):
        with tempfile.TemporaryDirectory(prefix="jarvix-task-") as directory:
            path = Path(directory) / "task.xml"
            path.write_text(xml, encoding="utf-16")
            self._run(["/Create", "/TN", name, "/XML", str(path)])
        self._run(["/Query", "/TN", name, "/XML"])

    def remove(self, name):
        self._run(["/Delete", "/TN", name, "/F"])


class SchedulerService:
    def __init__(self, services, backend=None, key_provider=None):
        self.s = services
        self.backend = backend or WindowsScheduler()
        self._key_provider = key_provider or self._key
        self._lock = threading.Lock()

    def _key(self):
        vault = CredentialVault()._backend()
        account = hashlib.sha256(str(self.s.data_dir.resolve()).casefold().encode()).hexdigest()
        key = vault.get_password("Jarvix.Scheduler", account)
        if not key:
            key = secrets.token_hex(32)
            vault.set_password("Jarvix.Scheduler", account, key)
        return key.encode()

    def _signature(self, row):
        payload = json.dumps({key: row[key] for key in FIELDS}, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._key_provider(), payload.encode(), hashlib.sha256).hexdigest()

    def _workflow(self, id):
        if not self.s.settings.get("automations.enabled", True):
            raise PermissionError("Automations are disabled in Settings.")
        row = self.s.workflows._verified(id)
        if row["trigger"] != "manual" or not row["enabled"]:
            raise ValueError("Use an enabled manual workflow or routine. Windows owns this schedule.")
        self.s.workflows._background_calls(row)
        return row

    def preview(self, workflow_id, time, weekdays):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            raise ValueError("Use a local 24-hour time such as 07:00.")
        if not weekdays or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays):
            raise ValueError("Choose weekdays from Monday=0 to Sunday=6.")
        self._workflow(workflow_id)
        return {"workflow": self.s.workflows.preview(workflow_id), "time": time,
                "weekdays": sorted(set(weekdays)), "scope": "Runs while signed into Windows, even if Jarvix is closed",
                "privilege": "Current user, no elevation", "missed_runs": "Run when available; computer must be awake"}

    def approval_snapshot(self, arguments):
        """Bind approvals to workflow contents and external registered-app configuration."""
        from jarvix.capabilities.workflows import _actions, fingerprint
        row = self._workflow(arguments["workflow_id"])
        apps = []
        for action in _actions(row["steps"]):
            if action["tool"] == "apps.open":
                id = action["arguments"]["id"]
                apps.append({"id": id, "rows": self.s.db.query("SELECT * FROM apps WHERE id=?", (id,)),
                             "arguments": self.s.apps.arguments_for(id)})
        return fingerprint({"workflow": row["approval_fingerprint"], "apps": apps})

    def install(self, workflow_id, time, weekdays):
        preview = self.preview(workflow_id, time, weekdays)
        workflow = self._workflow(workflow_id)
        id = self.s.repository.new_id()
        profile = str(self.s.data_dir.resolve())
        argv = [] if getattr(sys, "frozen", False) else ["-m", "jarvix"]
        argv += ["--data-dir", profile, "--scheduled-run", id]
        row = {"workflow_id": workflow_id, "fingerprint": workflow["approval_fingerprint"],
               "time": time, "weekdays": preview["weekdays"], "profile": profile,
               "command": str(Path(sys.executable).resolve()), "arguments": subprocess.list2cmdline(argv),
               "task_name": "Jarvix-" + id, "enabled": False, "last_status": "Installing"}
        row["dependencies"] = self.approval_snapshot({"workflow_id": workflow_id})
        row["signature"] = self._signature(row)
        self.s.records.put("closed_schedule", row, id)
        try:
            self.backend.install(row["task_name"], task_xml(row))
        except Exception:
            row["last_status"] = "Installation failed; inspect Task Scheduler before retrying"
            self.s.records.put("closed_schedule", row, id)
            raise
        row.update(enabled=True, last_status="Scheduled")
        self.s.records.put("closed_schedule", row, id)
        self.s.repository.audit("schedule", "Approved Windows workflow schedule installed")
        return {"id": id, "task_name": row["task_name"], **preview}

    def list(self):
        return [{key: row.get(key) for key in ("id", "workflow_id", "time", "weekdays", "enabled", "task_name", "last_status", "last_run_at")}
                for row in self.s.records.list("closed_schedule")]

    def remove(self, id):
        row = self.s.records.get("closed_schedule", id)
        if row["task_name"] != "Jarvix-" + id:
            raise ValueError("Invalid Jarvix task identity.")
        # Revoke first: a task racing this removal can no longer execute.
        row["enabled"] = False
        self.s.records.put("closed_schedule", row, id)
        self.backend.remove(row["task_name"])
        self.s.records.delete("closed_schedule", id)
        self.s.repository.audit("schedule", "Windows workflow schedule removed")
        return {"removed": True}

    def run(self, id, cancel=None):
        with self._lock:
            row = self.s.records.get("closed_schedule", id)
            if not row["enabled"] or not hmac.compare_digest(row["signature"], self._signature(row)):
                raise PermissionError("Schedule is disabled or its signed definition changed.")
            if str(self.s.data_dir.resolve()) != row["profile"]:
                raise PermissionError("Schedule belongs to a different profile.")
            workflow = self._workflow(row["workflow_id"])
            if workflow["approval_fingerprint"] != row["fingerprint"]:
                raise PermissionError("Workflow changed. Remove and explicitly approve a new schedule.")
            if row.get("dependencies") != self.approval_snapshot({"workflow_id": row["workflow_id"]}):
                raise PermissionError("A scheduled application's configuration changed. Review this schedule again.")
            now = datetime.now()
            # At most one execution per local calendar day, even if the task is retriggered.
            if row.get("last_day") == now.date().isoformat():
                return {"skipped": True, "reason": "Already executed today"}
            row.update(last_day=now.date().isoformat(), last_status="Running", last_run_at=now_iso())
            self.s.records.put("closed_schedule", row, id)
            result = self.s.workflows.run(row["workflow_id"], unattended=True, cancel=cancel)
            row["last_status"] = result["status"]
            self.s.records.put("closed_schedule", row, id)
            self.s.notifications.create("Background schedule " + result["status"],
                "Review the workflow run history for details.", "schedule", "normal" if result["ok"] else "high")
            self.s.repository.audit("schedule", "Signed scheduled workflow " + result["status"])
            return result

    def drain(self, cancel=None):
        directory = self.s.data_dir / "schedule-inbox"
        if not directory.exists():
            return
        for path in list(directory.glob("*.request"))[:10]:
            check_cancelled()
            if path.is_symlink() or not re.fullmatch(r"[a-f0-9-]{32,36}", path.stem):
                continue
            path.unlink(missing_ok=True)
            try:
                self.run(path.stem, cancel)
            except Exception:
                self.s.repository.audit("schedule", "Scheduled workflow blocked; recheck approval and configuration")
                self.s.notifications.create("Background schedule needs attention",
                    "The workflow or its approval changed. Review the schedule before retrying.", "schedule", "high")


def setup(s, registry):
    service = s.scheduler = SchedulerService(s)
    props = {"workflow_id": ID, "time": string(5), "weekdays": array(integer(0, 6), 7)}
    register(registry, "scheduler.preview", "Preview a signed Windows schedule for an enabled, approved manual workflow.",
             props, tuple(props), service.preview)
    register(registry, "scheduler.install", "Install this approved workflow in Windows Task Scheduler. Runs when signed in even with Jarvix closed; no elevated commands.",
             props, tuple(props), service.install, 3, "scheduler.manage")
    register(registry, "scheduler.list", "List Jarvix's opted-in Windows schedules and last execution status.", {}, (), service.list)
    register(registry, "scheduler.remove", "Revoke and remove a Jarvix Windows scheduled task.",
             {"id": ID}, ("id",), service.remove, 3, "scheduler.manage")
