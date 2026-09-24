"""Explicit, bounded desktop operations behind first-party tool services."""
from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from jarvix.capabilities.native_windows import Win32, windows_only
from jarvix.capabilities.files import _linked
from jarvix.capabilities.schema import BOOL, ID, enum, integer, register, string
from jarvix.runtime import check_cancelled


class NativeService:
    def __init__(self, services, native=None):
        self.services = services
        self._native = native

    @property
    def native(self):
        if self._native is None:
            self._native = Win32()
        return self._native


class WindowService(NativeService):
    def list(self, query=""):
        check_cancelled()
        return [row for row in self.native.windows() if query.casefold() in row["title"].casefold()]

    def foreground(self):
        check_cancelled()
        return self.native.foreground()

    def action(self, handle, process_id, action):
        check_cancelled()
        return self.native.window_action(handle, process_id, action)


class ClipboardService(NativeService):
    """Clipboard history is captured only by explicit action; never by a timer."""
    def _gate(self):
        check_cancelled()
        if not self.services.settings.get("clipboard.enabled", False):
            raise PermissionError("Enable clipboard access in Settings first.")

    def read(self):
        self._gate()
        text = self.native.clipboard_read()
        if len(text) > 12000:
            raise ValueError("Clipboard text is too large; select at most 12,000 characters.")
        return {"text": text, "stored": False}

    def write(self, text):
        self._gate()
        if "\0" in text or len(text) > 12000:
            raise ValueError("Clipboard text must contain at most 12,000 characters and no NUL bytes.")
        self.native.clipboard_write(text)
        return {"written": True, "characters": len(text)}

    def capture(self):
        text = self.read()["text"]
        if not text:
            raise ValueError("The clipboard contains no text.")
        rows = self.services.records.list("clipboard")
        existing = next((row for row in rows if row["text"] == text), None)
        if existing:
            return {"id": existing["id"], "captured": False, "already_saved": True}
        if len(rows) >= 200:
            removable = next((row for row in reversed(rows) if not row.get("pinned")), None)
            if not removable:
                raise ValueError("Clipboard history is full. Unpin or clear items first.")
            self.services.records.delete("clipboard", removable["id"])
        record_id = self.services.records.put("clipboard", {"text": text, "pinned": False})
        return {"id": record_id, "captured": True}

    def history(self, query="", limit=20):
        self._gate()
        rows = self.services.records.list("clipboard")
        return [{**row, "text": row["text"][:1000], "truncated": len(row["text"]) > 1000}
                for row in rows if query.casefold() in row["text"].casefold()][:limit]

    def item(self, item_id):
        self._gate()
        return self.services.records.get("clipboard", item_id)

    def pin(self, item_id, pinned=True):
        row = self.item(item_id)
        self.services.records.put("clipboard", {**row, "pinned": pinned}, item_id)
        return {"id": item_id, "pinned": pinned}

    def clear(self, include_pinned=False):
        self._gate()
        count = 0
        for row in self.services.records.list("clipboard"):
            check_cancelled()
            if include_pinned or not row.get("pinned"):
                self.services.records.delete("clipboard", row["id"])
                count += 1
        return {"removed": count, "system_clipboard_unchanged": True}

    def classify(self):
        text = self.read()["text"].strip()
        parsed = urlsplit(text) if not any(c.isspace() for c in text) else None
        if parsed and parsed.scheme in {"https", "http"} and parsed.netloc:
            kind = "url"
        elif re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", text) and "\n" not in text:
            kind = "file_path"
        elif re.search(r"(?m)^\s*(?:def |class |import |from .+ import |function |const |let |local |#include|SELECT )", text):
            kind = "possible_code"
        else:
            kind = "text"
        return {"kind": kind, "characters": len(text), "heuristic": True}

    def to_note(self, title):
        return {"id": self.services.save_note(title, self.read()["text"])}

    def to_task(self, title=None, due_at=None):
        text = self.read()["text"]
        if not text.strip():
            raise ValueError("The clipboard contains no text.")
        if title is None and len(text) > 500:
            raise ValueError("Provide a short task title for long clipboard content.")
        task_id = self.services.add_task(title or text, due_at)
        if title and text != title:
            self.services.records.put("task_clipboard_source", {"task_id": task_id, "text": text}, task_id)
        return {"id": task_id}


class SystemService(NativeService):
    CRITICAL = {"system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
                "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe"}

    def battery(self):
        value = psutil.sensors_battery()
        if value is None:
            return {"present": False}
        seconds = value.secsleft
        return {"present": True, "percent": value.percent, "charging": value.power_plugged,
                "seconds_remaining": seconds if seconds >= 0 else None}

    def storage(self):
        rows = []
        for item in psutil.disk_partitions(all=False):
            check_cancelled()
            try:
                usage = psutil.disk_usage(item.mountpoint)
                rows.append({"device": item.device, "mountpoint": item.mountpoint, "filesystem": item.fstype,
                             "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent})
            except (PermissionError, OSError):
                continue
        return rows

    def network(self):
        statistics = psutil.net_if_stats()
        result = []
        for name, addresses in psutil.net_if_addrs().items():
            check_cancelled()
            status = statistics.get(name)
            result.append({"name": name, "connected": bool(status and status.isup),
                           "speed_mbps": status.speed if status else None,
                           "addresses": [{"address": item.address, "netmask": item.netmask,
                                          "family": "IPv4" if item.family == socket.AF_INET else "IPv6"}
                                         for item in addresses if item.family in {socket.AF_INET, socket.AF_INET6}]})
        return {"adapters": result, "internet_reachability_tested": False}

    def uptime(self):
        return {"boot_timestamp": psutil.boot_time(), "uptime_seconds": int(max(0, time.time() - psutil.boot_time()))}

    def device(self):
        return {"hostname": platform.node(), "os": platform.system(), "release": platform.release(),
                "version": platform.version(), "architecture": platform.machine(), "processor": platform.processor(),
                "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
                "memory_bytes": psutil.virtual_memory().total}

    def processes(self, query="", sort="memory", limit=20):
        items = []
        processes = list(psutil.process_iter())
        if sort == "cpu":
            for process in processes:
                try:
                    process.cpu_percent(None)
                except psutil.Error:
                    pass
            time.sleep(0.15)
        for process in processes:
            check_cancelled()
            try:
                with process.oneshot():
                    name = process.name()
                    if query.casefold() not in name.casefold():
                        continue
                    items.append({"pid": process.pid, "name": name, "memory_mb": round(process.memory_info().rss / 1048576, 2),
                                  "cpu_percent": process.cpu_percent(None), "started_at": process.create_time()})
            except psutil.Error:
                continue
        key = "cpu_percent" if sort == "cpu" else "memory_mb"
        return sorted(items, key=lambda row: row[key], reverse=True)[:limit]

    def process_details(self, pid):
        process = psutil.Process(pid)
        with process.oneshot():
            return {"pid": process.pid, "name": process.name(), "started_at": process.create_time(),
                    "status": process.status(), "parent_pid": process.ppid(), "threads": process.num_threads(),
                    "memory_mb": round(process.memory_info().rss / 1048576, 2),
                    "executable": process.exe()}

    def terminate(self, pid, started_at):
        check_cancelled()
        process = psutil.Process(pid)
        protected_ids = {os.getpid(), 0, 4}
        protected_ids.update(parent.pid for parent in psutil.Process().parents())
        if pid in protected_ids or process.name().casefold() in self.CRITICAL:
            raise PermissionError("Jarvix will not terminate this protected process.")
        if abs(process.create_time() - started_at) > 0.001:
            raise ValueError("The process has changed. Inspect it and confirm again.")
        check_cancelled()
        process.terminate()
        return {"pid": pid, "termination_requested": True}

    def services_list(self, query=""):
        windows_only()
        items = []
        for service in psutil.win_service_iter():
            check_cancelled()
            try:
                row = service.as_dict()
                if query.casefold() in (row["name"] + row["display_name"]).casefold():
                    items.append({key: row[key] for key in ("name", "display_name", "status", "start_type", "pid")})
                if len(items) >= 150:
                    break
            except psutil.Error:
                continue
        return items

    def startup(self):
        windows_only()
        import winreg
        rows = []
        for hive, scope in ((winreg.HKEY_CURRENT_USER, "user"), (winreg.HKEY_LOCAL_MACHINE, "machine")):
            for branch in (r"Software\Microsoft\Windows\CurrentVersion\Run",
                           r"Software\Microsoft\Windows\CurrentVersion\RunOnce"):
                try:
                    with winreg.OpenKey(hive, branch, 0, winreg.KEY_READ) as key:
                        for index in range(winreg.QueryInfoKey(key)[1]):
                            name, _command, _kind = winreg.EnumValue(key, index)
                            # Startup command arguments may contain passwords or tokens.
                            rows.append({"name": name, "scope": scope, "source": branch})
                except OSError:
                    continue
        for variable, scope in (("APPDATA", "user"), ("PROGRAMDATA", "machine")):
            directory = Path(os.environ.get(variable, "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
            if directory.is_dir():
                rows.extend({"name": item.name, "scope": scope, "source": "Startup folder"}
                            for item in directory.iterdir() if item.is_file())
        return {"items": rows[:200], "scope": "Run/RunOnce registry keys and Startup folders"}

    def monitors(self):
        return self.native.monitors()

    def gpu(self):
        return self.native.graphics()

    def audio_devices(self):
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtMultimedia import QMediaDevices
        if QCoreApplication.instance() is None:
            raise RuntimeError("Audio devices are available while the desktop application is running.")
        return {"outputs": [{"id": bytes(item.id()).hex(), "name": item.description(), "default": item.isDefault()}
                            for item in QMediaDevices.audioOutputs()],
                "inputs": [{"id": bytes(item.id()).hex(), "name": item.description(), "default": item.isDefault()}
                           for item in QMediaDevices.audioInputs()]}

    def volume(self, action="status", percent=None):
        check_cancelled()
        if action == "set" and (percent is None or not 0 <= percent <= 100):
            raise ValueError("Volume must be between 0 and 100 percent.")
        return self.native.volume(action, percent)


class ComputerControlService(NativeService):
    def media(self, action):
        check_cancelled()
        self.native.media(action)
        return {"action": action, "sent": True, "note": "The active media player receives the system media key."}

    def lock(self):
        check_cancelled()
        self.native.lock()
        return {"lock_requested": True}

    def power(self, action):
        check_cancelled()
        if action == "sleep":
            self.native.sleep()
        elif action in {"restart", "shutdown"}:
            command = str(Path(self.native.system_directory()) / "shutdown.exe")
            subprocess.run([command, "/r" if action == "restart" else "/s", "/t", "0"],
                           check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            raise ValueError("Unknown power operation.")
        return {"action": action, "requested": True}


class ScreenshotService(NativeService):
    def _gate(self):
        check_cancelled()
        if not self.services.settings.get("screenshots.enabled", False):
            raise PermissionError("Enable screen access in Settings first.")

    def capture(self, target="monitor", monitor=0, handle=None, process_id=None):
        self._gate()
        if target == "window" and (not handle or not process_id):
            raise ValueError("Select a window handle and its process ID.")
        directory = self.services.data_dir / "screenshots"
        if _linked(directory):
            raise PermissionError("The screenshot directory must be local to the Jarvix data directory.")
        directory.mkdir(exist_ok=True)
        if not directory.resolve().is_relative_to(self.services.data_dir.resolve()):
            raise PermissionError("The screenshot directory is outside the Jarvix data directory.")
        data, bounds = self.native.screenshot(target, monitor, handle, process_id)
        self._gate()
        record_id = uuid.uuid4().hex
        path = directory / f"{record_id}.bmp"
        with path.open("xb") as output:
            output.write(data)
        self.services.records.put("screenshot", {"path": str(path), "target": target, **bounds}, record_id)
        return {"id": record_id, "path": str(path), "target": target, **bounds,
                "note": "Captures visible pixels; overlapping windows may appear."}

    def history(self, limit=20):
        self._gate()
        return self.services.records.list("screenshot")[:limit]


def setup(services, registry):
    services.windows = windows = WindowService(services)
    services.computer = computer = ComputerControlService(services)
    services.clipboard = clipboard = ClipboardService(services)
    services.system = system = SystemService(services)
    services.screenshots = screenshots = ScreenshotService(services)

    def tool(name, description, handler, properties=None, required=(), level=1, permission="local.read"):
        register(registry, name, description, properties or {}, required, handler, level, permission)

    tool("windows.list", "List visible windows, handles and owning process IDs.", windows.list, {"query": string(200, 0)})
    tool("windows.foreground", "Inspect the active foreground window.", windows.foreground)
    window_args = {"handle": integer(1, 2**64 - 1), "process_id": integer(1, 2**32 - 1)}
    for action in ("focus", "minimize", "maximize", "restore", "close"):
        def handler(handle, process_id, _action=action):
            return windows.action(handle, process_id, _action)
        tool(f"windows.{action}", f"{action.title()} a window after checking its owning process. Close requests are graceful.",
             handler, window_args, tuple(window_args), 2, "computer.control")

    tool("clipboard.read", "Read clipboard text once without storing it; requires clipboard access enabled.", clipboard.read,
         permission="clipboard.read")
    tool("clipboard.write", "Replace clipboard text with explicit text.", clipboard.write,
         {"text": string(12000, 0)}, ("text",), 2, "clipboard.write")
    tool("clipboard.capture", "Explicitly save current clipboard text in local history; no passive monitoring.", clipboard.capture,
         level=2, permission="clipboard.write")
    tool("clipboard.history", "Search explicitly captured clipboard history (bounded previews).", clipboard.history,
         {"query": string(200, 0), "limit": integer(1, 30)}, permission="clipboard.read")
    tool("clipboard.item", "Read the full text of one explicitly captured clipboard item.", clipboard.item,
         {"item_id": ID}, ("item_id",), permission="clipboard.read")
    tool("clipboard.pin", "Pin or unpin a captured clipboard item.", clipboard.pin,
         {"item_id": ID, "pinned": BOOL}, ("item_id",), 2, "clipboard.write")
    tool("clipboard.clear", "Permanently clear Jarvix clipboard history; system clipboard remains unchanged.", clipboard.clear,
         {"include_pinned": BOOL}, level=3, permission="clipboard.write")
    tool("clipboard.classify", "Classify copied text as URL, possible code, path or plain text without storing it.", clipboard.classify,
         permission="clipboard.read")
    tool("clipboard.to_note", "Create a local note from current clipboard text.", clipboard.to_note,
         {"title": string(200)}, ("title",), 2, "clipboard.read")
    tool("clipboard.to_task", "Create a local task from clipboard text; provide a title for long content.", clipboard.to_task,
         {"title": string(500), "due_at": string(80)}, level=2, permission="clipboard.read")

    for name, description, handler in (
        ("battery", "Inspect battery level, charging state and estimated remaining time.", system.battery),
        ("storage", "Inspect storage capacity and free space for mounted drives.", system.storage),
        ("network", "Inspect network adapters and local IPs; does not test Internet connectivity.", system.network),
        ("uptime", "Inspect system uptime and boot timestamp.", system.uptime),
        ("device", "Inspect OS version, CPU architecture and hardware memory.", system.device),
        ("startup", "Inspect configured startup entries without disclosing their command arguments.", system.startup),
        ("monitors", "Inspect connected monitors, screen resolution and physical coordinates.", system.monitors),
        ("gpu", "Inspect Windows display adapters.", system.gpu),
        ("audio_devices", "List audio input/output devices and current default devices.", system.audio_devices),
        ("volume", "Inspect default playback volume and mute state.", system.volume),
    ):
        tool(f"system.{name}", description, handler)
    tool("processes.search", "Search running process names sorted by CPU or RAM, including process identity timestamp.", system.processes,
         {"query": string(200, 0), "sort": enum("cpu", "memory"), "limit": integer(1, 100)})
    tool("processes.details", "Inspect a process without disclosing command-line arguments or environment variables.", system.process_details,
         {"pid": integer(0, 2**32 - 1)}, ("pid",))
    tool("processes.terminate", "Terminate a noncritical process; unsaved data may be lost. ALWAYS confirm immediately first.", system.terminate,
         {"pid": integer(1, 2**32 - 1), "started_at": {"type": "number", "minimum": 0}},
         ("pid", "started_at"), 3, "system.terminate")
    tool("system.services", "Search running Windows services and their start modes.", system.services_list, {"query": string(200, 0)})
    for action in ("up", "down", "mute", "unmute"):
        def audio_handler(_action=action):
            return system.volume(_action)
        tool(f"audio.{action}", f"{action.title()} the default system playback volume.", audio_handler,
             level=2, permission="computer.control")
    tool("audio.set_volume", "Set default playback volume to a percentage.", lambda percent: system.volume("set", percent),
         {"percent": integer(0, 100)}, ("percent",), 2, "computer.control")
    tool("media.control", "Send a native play/pause, next, previous or stop command to the active media player.", computer.media,
         {"action": enum("play_pause", "next", "previous", "stop")}, ("action",), 2, "computer.control")
    tool("computer.lock", "Lock the Windows session.", computer.lock, level=2, permission="computer.control")
    tool("computer.power", "Sleep, restart or shut down Windows. ALWAYS confirm immediately first; unsaved work may be lost.", computer.power,
         {"action": enum("sleep", "restart", "shutdown")}, ("action",), 3, "system.power")
    tool("screen.capture", "Capture a selected monitor, visible window bounds or all displays once into local screenshot history. Requires screen access enabled.",
         screenshots.capture, {"target": enum("monitor", "window", "all"), "monitor": integer(0, 63), **window_args},
         level=2, permission="screen.capture")
    tool("screen.history", "List saved local screenshots; no new capture.", screenshots.history,
         {"limit": integer(1, 100)}, permission="screen.capture")
