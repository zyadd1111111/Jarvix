import os
import threading
from types import SimpleNamespace

import psutil
import pytest

from jarvix.capabilities.computer import (
    ClipboardService,
    ComputerControlService,
    ScreenshotService,
    SystemService,
    WindowService,
    setup,
)
from jarvix.records import RecordStore
from jarvix.runtime import operation
from jarvix.storage import Database, SettingsRepository
from jarvix.tools.registry import ToolRegistry


class FakeNative:
    def __init__(self):
        self.text = "private clipboard text"
        self.calls = []

    def clipboard_read(self):
        self.calls.append("read")
        return self.text

    def clipboard_write(self, text):
        self.calls.append("write")
        self.text = text

    def windows(self):
        return [{"handle": 2**40, "process_id": 57, "title": "Visual Studio Code"}]

    def foreground(self):
        return self.windows()[0]

    def window_action(self, handle, process_id, action):
        self.calls.append((handle, process_id, action))
        return {"requested": True}

    def screenshot(self, *args):
        self.calls.append(args)
        return b"BM-test-fixture", {"x": 0, "y": 0, "width": 1, "height": 1}

    def media(self, action):
        self.calls.append(action)

    def lock(self):
        self.calls.append("lock")

    def sleep(self):
        self.calls.append("sleep")

    def system_directory(self):
        return "C:/Windows/System32"


@pytest.fixture
def services(tmp_path):
    db = Database(tmp_path / "jarvix.db")
    instance = SimpleNamespace(data_dir=tmp_path, records=RecordStore(db), settings=SettingsRepository(db))
    instance.save_note = lambda title, text: f"{title}:{text}"
    instance.add_task = lambda title, due_at=None: f"{title}:{due_at}"
    return instance


def test_clipboard_disabled_prevents_even_direct_reads_and_history(services):
    native = FakeNative()
    clipboard = ClipboardService(services, native)
    for method in (clipboard.read, clipboard.capture, clipboard.classify, clipboard.history, clipboard.clear):
        with pytest.raises(PermissionError):
            method()
    with pytest.raises(PermissionError):
        clipboard.write("test")
    assert native.calls == []


def test_clipboard_read_does_not_store_but_explicit_capture_does(services):
    services.settings.set("clipboard.enabled", True)
    native = FakeNative()
    clipboard = ClipboardService(services, native)
    assert clipboard.read() == {"text": native.text, "stored": False}
    assert services.records.list("clipboard") == []
    item = clipboard.capture()
    assert clipboard.item(item["id"])["text"] == native.text
    assert clipboard.capture()["already_saved"] is True
    assert len(services.records.list("clipboard")) == 1
    clipboard.pin(item["id"])
    assert clipboard.clear()["removed"] == 0
    assert clipboard.history("PRIVATE")[0]["pinned"] is True
    assert clipboard.clear(include_pinned=True)["removed"] == 1
    assert native.text == "private clipboard text"


@pytest.mark.parametrize(("text", "kind"), [
    ("https://example.org/a", "url"), ("javascript:alert(1)", "text"),
    ("C:\\Users\\person\\work.py", "file_path"), ("def hello():\n    return 1", "possible_code"),
    ("Remember to take a break", "text"),
])
def test_clipboard_classification(services, text, kind):
    services.settings.set("clipboard.enabled", True)
    native = FakeNative()
    native.text = text
    assert ClipboardService(services, native).classify()["kind"] == kind


def test_clipboard_bounds_and_null_safety(services):
    services.settings.set("clipboard.enabled", True)
    native = FakeNative()
    clipboard = ClipboardService(services, native)
    for text in ("a\0b", "a" * 12001):
        with pytest.raises(ValueError):
            clipboard.write(text)
    native.text = "a" * 12001
    with pytest.raises(ValueError):
        clipboard.read()
    assert "write" not in native.calls


def test_clipboard_to_task_retains_long_source_when_title_given(services):
    services.settings.set("clipboard.enabled", True)
    native = FakeNative()
    native.text = "source " * 100
    clipboard = ClipboardService(services, native)
    with pytest.raises(ValueError):
        clipboard.to_task()
    task = clipboard.to_task("Review source")
    assert services.records.get("task_clipboard_source", task["id"])["text"] == native.text


def test_screenshot_requires_explicit_access_and_valid_window_selection(services):
    native = FakeNative()
    screen = ScreenshotService(services, native)
    with pytest.raises(PermissionError):
        screen.capture()
    services.settings.set("screenshots.enabled", True)
    with pytest.raises(ValueError):
        screen.capture(target="window", handle=123)
    assert native.calls == []


def test_screenshot_saves_only_in_profile_with_random_filename(services):
    services.settings.set("screenshots.enabled", True)
    screen = ScreenshotService(services, FakeNative())
    first = screen.capture()
    second = screen.capture(target="window", handle=123, process_id=456)
    from pathlib import Path
    path = Path(first["path"])
    assert path.parent == services.data_dir / "screenshots"
    assert path.read_bytes() == b"BM-test-fixture"
    assert first["path"] != second["path"]
    assert len(screen.history()) == 2
    services.settings.set("screenshots.enabled", False)
    with pytest.raises(PermissionError):
        screen.history()


def test_screen_gate_rechecked_after_native_capture(services):
    services.settings.set("screenshots.enabled", True)
    native = FakeNative()

    def revoke(*args):
        services.settings.set("screenshots.enabled", False)
        return b"BM", {"width": 1, "height": 1}

    native.screenshot = revoke
    with pytest.raises(PermissionError):
        ScreenshotService(services, native).capture()
    assert list((services.data_dir / "screenshots").iterdir()) == []


def test_windows_actions_preserve_64bit_handles_and_process_identity(services):
    native = FakeNative()
    windows = WindowService(services, native)
    row = windows.list("CODE")[0]
    windows.action(row["handle"], row["process_id"], "focus")
    assert native.calls == [(2**40, 57, "focus")]


def test_cancelled_operation_never_calls_native_mutations(services):
    native = FakeNative()
    stop = threading.Event()
    with operation(cancel=stop):
        stop.set()
        with pytest.raises(InterruptedError):
            ComputerControlService(services, native).media("play_pause")
    assert native.calls == []


def test_process_termination_protects_system_and_checks_identity(services, monkeypatch):
    terminated = []
    selected = SimpleNamespace(pid=456, name=lambda: "example.exe", create_time=lambda: 200,
                               terminate=lambda: terminated.append(456))

    def process(pid=None):
        return selected if pid is not None else SimpleNamespace(parents=lambda: [])

    monkeypatch.setattr(psutil, "Process", process)
    system = SystemService(services)
    with pytest.raises(ValueError):
        system.terminate(456, 100)
    selected.name = lambda: "lsass.exe"
    with pytest.raises(PermissionError):
        system.terminate(456, 200)
    selected.name = lambda: "example.exe"
    with pytest.raises(PermissionError):
        system.terminate(os.getpid(), 200)
    assert terminated == []
    assert system.terminate(456, 200)["termination_requested"]
    assert terminated == [456]


def test_power_uses_native_sleep_and_fixed_shutdown_arguments(services, monkeypatch):
    native = FakeNative()
    calls = []
    monkeypatch.setattr(subprocess := __import__("subprocess"), "run", lambda command, **kwargs: calls.append((command, kwargs)))
    computer = ComputerControlService(services, native)
    computer.power("sleep")
    assert native.calls == ["sleep"]
    computer.power("restart")
    assert calls[0][0][1:] == ["/r", "/t", "0"]
    assert calls[0][1]["check"] is True
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    with pytest.raises(ValueError):
        computer.power("restart & whoami")


def test_battery_absence_and_network_are_honest(services, monkeypatch):
    system = SystemService(services)
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
    assert system.battery() == {"present": False}
    assert system.network()["internet_reachability_tested"] is False
    assert system.uptime()["uptime_seconds"] >= 0
    assert system.device()["logical_cpus"] >= 1


def test_tool_schema_safety_and_failure_sanitization(services):
    registry = ToolRegistry()
    setup(services, registry)
    for name in ("computer.power", "processes.terminate", "clipboard.clear"):
        assert registry.get(name).permission_level == 3
    assert registry.get("screen.capture").permission == "screen.capture"
    assert registry.get("windows.close").permission_level == 2
    assert registry.validate("processes.terminate", {"pid": 123}) is not None
    assert registry.validate("screen.capture", {"path": "../../outside.bmp"}) is not None
    assert registry.validate("computer.power", {"action": "format"}) is not None
    native = FakeNative()

    def fail():
        raise RuntimeError("token=super-secret-value")

    native.clipboard_read = fail
    services.clipboard._native = native
    services.settings.set("clipboard.enabled", True)
    result = registry.execute("clipboard.read", {})
    assert not result.ok
    assert "super-secret" not in str(result.as_dict())


@pytest.mark.skipif(os.name != "nt", reason="Native Win32 inspection")
def test_win32_handle_bindings_and_monitor_inspection():
    import ctypes as ct
    from jarvix.capabilities.native_windows import Win32
    native = Win32()
    assert ct.sizeof(native.user.GetForegroundWindow.restype) == ct.sizeof(ct.c_void_p)
    for monitor in native.monitors():
        assert monitor["width"] > 0
        assert monitor["height"] > 0
