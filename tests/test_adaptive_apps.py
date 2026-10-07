import sys
import threading
from types import SimpleNamespace

import pytest

from jarvix.capabilities import app_adapters, windows_integration
from jarvix.services import Services
from jarvix.runtime import operation
from jarvix.domain import ToolResult


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    s.add_file_root(str(tmp_path))
    if not hasattr(s, "windows_integration"):
        windows_integration.setup(s, s.registry)
    if not hasattr(s, "app_adapters"):
        app_adapters.setup(s, s.registry)
    yield s
    s.close()


def test_adapter_catalog_reuses_host_tools_and_permissions(services):
    groups = {group["id"]: group for group in services.app_adapters.capabilities()["items"]}
    assert next(a for a in groups["vscode"]["actions"] if a["name"] == "open_project")["tool"] == "apps.open_project"
    assert next(a for a in groups["terminal"]["actions"] if a["name"] == "open_directory")["tool"] == "workspaces.open_terminal"
    assert next(a for a in groups["spotify"]["actions"] if a["name"] == "pause")["permission_level"] == 2
    assert services.registry.get("adapters.vscode_command_palette").permission_level == 3
    assert services.registry.validate("adapters.explorer_navigate", {"handle": 1, "process_id": 9,
                                                                  "path": "C:/folder", "command": "evil"})


def test_adapter_dispatch_keeps_host_schema_and_sensitive_approval(services, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_adapters.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    assert services.app_adapters.resolve("explorer", "copy").name == "files.copy"
    source = tmp_path / "original.txt"
    source.write_text("original")
    destination = tmp_path / "copied.txt"
    denied = services.app_adapters.execute("explorer", "copy", {
        "source": str(source), "destination": str(destination)}, approve=lambda _: False)
    assert not denied.ok and not destination.exists()
    accepted = services.app_adapters.execute("explorer", "copy", {
        "source": str(source), "destination": str(destination)}, approve=lambda _: True)
    assert accepted.ok and destination.read_text() == "original"
    assert not services.app_adapters.execute("explorer", "copy", {
        "source": str(source), "destination": str(destination), "overwrite": True}, approve=lambda _: True).ok
    # A remembered normal-control grant must not authorize command execution.
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("developer.command_start", "allow")
    assert not services.app_adapters.execute("terminal", "command_start", {
        "argv": [sys.executable, "-c", "print('no')"], "cwd": str(tmp_path)}, approve=lambda _: False).ok
    assert not calls
    assert not services.app_adapters.execute("unknown", "copy", {}).ok


def test_adapter_browser_identity_and_catalog_unavailability(services, monkeypatch):
    groups = {group["id"]: group for group in services.app_adapters.capabilities()["items"]}
    downloads = next(a for a in groups["chrome"]["actions"] if a["name"] == "downloads")
    assert downloads["supported"] and not downloads["available"] and downloads["unavailable_reason"]
    assert {a["tool"] for a in groups["terminal"]["actions"]} >= {
        "developer.command_history", "developer.command_output"}
    calls = []
    monkeypatch.setattr(services.browser, "status", lambda: {"connected": True, "browser": "chrome"})
    marker = ToolResult(True, {"items": []})
    monkeypatch.setattr(services, "execute_tool", lambda name, args, **kw: calls.append((name, args, kw)) or marker)
    assert not services.app_adapters.execute("edge", "tabs", {}).ok
    assert calls == []
    assert services.app_adapters.execute("chrome", "downloads", {"limit": 5}) is marker
    assert calls[0][:2] == ("browser.downloads", {"limit": 5})
    services.registry.unregister("browser.downloads")
    downloads = next(a for group in services.app_adapters.capabilities()["items"] if group["id"] == "chrome"
                     for a in group["actions"] if a["name"] == "downloads")
    assert not downloads["supported"] and not downloads["available"]


def test_vscode_context_hints_are_redacted_and_git_requires_selected_project(services, tmp_path, monkeypatch):
    executable = tmp_path / "Code.exe"
    executable.write_bytes(b"test")
    app_id = services.add_app("VS Code", str(executable))
    project_id = services.add_project("Jarvix", str(tmp_path))
    title = "main.py - Jarvix - Visual Studio Code"
    monkeypatch.setattr(services.windows, "list", lambda: [{"handle": 123, "process_id": 9, "title": title}])
    monkeypatch.setattr(app_adapters.psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: str(executable)))
    services.settings.set("screenshots.enabled", True)
    elements = [{"runtime_id": str(i), "name": name, "automation_id": "", "control_type": kind,
                 "focused": i == 0, "password": False, "offscreen": False}
                for i, (name, kind) in enumerate([("main.py", "TabItem"), ("password=private", "TabItem"),
                                                ("Terminal", "TabItem"), ("secret.key", "Edit")])]
    services.desktop.backend = SimpleNamespace(call=lambda *a, **kw: {
        "root_id": "root", "process_started": "start", "application": "Code", "elements": elements, "bounded": True})
    calls = []
    original = services.execute_tool
    def execute(name, args, **kwargs):
        if name == "developer.git_status":
            calls.append((name, args))
            return ToolResult(True, {"branch": "main"})
        return original(name, args, **kwargs)
    monkeypatch.setattr(services, "execute_tool", execute)
    inspected = services.app_adapters.vscode_context(app_id, 123, 9)
    assert calls == [] and inspected["git"] is None
    assert inspected["active_file_hint"] == "main.py" and not inspected["file_paths_verified"]
    assert [hint["name"] for hint in inspected["editor_tab_hints"]] == ["main.py", "Terminal"]
    assert "private" not in str(inspected) and not inspected["diagnostics"]["available"]
    assert inspected["terminal"]["visible"] and not inspected["terminal"]["output_available"]
    inspected = services.app_adapters.vscode_context(app_id, 123, 9, project_id)
    assert inspected["git"]["available"] and inspected["project"]["id"] == project_id
    assert calls == [("developer.git_status", {"path": str(tmp_path)})]
    services.settings.set("screenshots.enabled", False)
    with pytest.raises(PermissionError):
        services.app_adapters.vscode_context(app_id, 123, 9, project_id)
    assert len(calls) == 1


def test_vscode_adapter_does_not_bypass_denied_observation_tools(services, tmp_path, monkeypatch):
    executable = tmp_path / "Code.exe"
    executable.write_bytes(b"test")
    app_id = services.add_app("VS Code", str(executable))
    services.settings.set("screenshots.enabled", True)
    monkeypatch.setattr(services.windows, "list", lambda: [{"handle": 123, "process_id": 9, "title": "private.py - Code"}])
    monkeypatch.setattr(app_adapters.psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: str(executable)))
    observed = []
    services.desktop.backend = SimpleNamespace(call=lambda *a, **kw: observed.append(a) or {
        "root_id": "root", "process_started": "start", "application": "Code", "elements": []})
    services.permissions.set_grant("desktop.inspect_ui", "deny")
    args = {"id": app_id, "handle": 123, "process_id": 9}
    denied = services.execute_tool("adapters.vscode_context", args)
    assert not denied.ok and "private.py" not in str(denied.data) and not observed
    services.permissions.set_grant("desktop.inspect_ui", None)
    services.permissions.set_grant("projects.list", "deny")
    denied = services.execute_tool("adapters.vscode_context", args)
    assert not denied.ok and "private.py" not in str(denied.data) and len(observed) == 1


def test_vscode_file_arguments_are_fixed_and_root_guarded(services, tmp_path, monkeypatch):
    executable = tmp_path / "Code.exe"
    executable.write_bytes(b"test")
    id = services.add_app("VS Code", str(executable))
    source = tmp_path / "file.py"
    source.write_text("print('hello')")
    launches = []
    monkeypatch.setattr(app_adapters.subprocess, "Popen", lambda args, **kwargs:
                        launches.append((args, kwargs)) or SimpleNamespace(pid=42))
    result = services.app_adapters.vscode_open_file(id, str(source), 4, 2)
    assert result["requested"] and not result["verified"]
    assert launches[0][0] == [str(executable), "--reuse-window", "--goto", f"{source}:4:2"]
    assert launches[0][1]["shell"] is False
    with pytest.raises(ValueError):
        services.app_adapters.vscode_open_file(id, str(source), -1)
    cancelled = threading.Event()
    with operation(cancelled):
        cancelled.set()
        with pytest.raises(InterruptedError):
            services.app_adapters.vscode_open_file(id, str(source))
    services.remove_file_root(str(tmp_path))
    with pytest.raises(ValueError):
        services.app_adapters.vscode_open_file(id, str(source))
    assert len(launches) == 1


def test_shortcut_rechecks_registered_executable_after_visible_hud(services, tmp_path, monkeypatch):
    executable = tmp_path / "Code.exe"
    executable.write_bytes(b"test")
    id = services.add_app("VS Code", str(executable))
    services.settings.set("screenshots.enabled", True)
    monkeypatch.setattr(services.windows, "list", lambda: [{"handle": 123, "process_id": 9, "title": "VS Code"}])
    monkeypatch.setattr(app_adapters.psutil, "Process", lambda pid: SimpleNamespace(exe=lambda: str(executable)))
    calls = []
    services.desktop.backend = SimpleNamespace(call=lambda operation, **kwargs:
        calls.append((operation, kwargs)) or {"root_id": "root", "process_started": "start", "requested": True, "verified": False})
    services.desktop.set_indicator(lambda state: True)
    assert not services.app_adapters.vscode_shortcut(id, 123, 9, "command_palette")["verified"]
    assert calls[-1][1]["codes"] == [0x11, 0x10, 0x50]
    assert calls[-1][1]["root_id"] == "root" and calls[-1][1]["process_started"] == "start"
    def revoke(state):
        if state.get("state") == "running":
            services.settings.set("screenshots.enabled", False)
        return True
    services.desktop.set_indicator(revoke)
    before = len(calls)
    with pytest.raises(PermissionError):
        services.app_adapters.vscode_shortcut(id, 123, 9, "focus_terminal")
    assert len(calls) == before + 1


def test_shell_results_are_filtered_against_live_allowed_roots(services, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    good = root / "report.txt"
    good.write_text("hello")
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    services.remove_file_root(str(tmp_path))
    services.add_file_root(str(root))
    requests = []
    services.windows_integration.backend = lambda script, arguments, **kw: requests.append(arguments) or {
        "paths": [str(good), str(outside)], "items": [{"path": str(outside)}, {"path": str(good)}]}
    assert [r["path"] for r in services.windows_integration.search("report")["items"]] == [str(good)]
    assert requests[0]["roots"] == [str(root)]
    with pytest.raises(PermissionError):
        services.windows_integration.recent()
    services.settings.set("windows.recent.enabled", True)
    assert [r["path"] for r in services.windows_integration.recent()["items"]] == [str(good)]
    with pytest.raises(ValueError):
        services.windows_integration.search("% OR 1=1")
    with pytest.raises(ValueError):
        services.windows_integration.open_with(str(outside))


def test_startup_only_owns_exact_jarvix_entry_and_needs_fresh_confirmation(services, tmp_path, monkeypatch):
    executable = tmp_path / "Jarvix.exe"
    executable.write_bytes(b"packaged")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    values = {}
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def query(key, name):
        if name not in values:
            raise FileNotFoundError()
        return values[name], 1
    fake_registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, KEY_QUERY_VALUE=4, REG_SZ=1,
        OpenKey=lambda *a: Key(), CreateKeyEx=lambda *a: Key(), QueryValueEx=query,
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
        DeleteValue=lambda key, name: values.pop(name))
    monkeypatch.setitem(sys.modules, "winreg", fake_registry)
    services.settings.set("control.enabled", True)
    assert not services.execute_tool("windows.set_startup", {"enabled": True}, approve=lambda _: False).ok
    assert not values
    assert services.execute_tool("windows.set_startup", {"enabled": True}, approve=lambda _: True).ok
    assert values["Jarvix"] == services.windows_integration.startup_preview()["command"]
    values["Jarvix"] = "unrelated command"
    with pytest.raises(PermissionError):
        services.windows_integration.set_startup(False)
    assert values["Jarvix"] == "unrelated command"


def test_explorer_context_is_explicit_root_filtered_and_ephemeral(services, tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    good = root / "selected.txt"
    good.write_text("selected")
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    services.remove_file_root(str(tmp_path))
    services.add_file_root(str(root))
    services.settings.set("context.enabled", True)
    services.settings.set("screenshots.enabled", True)
    calls = []
    def execute(name, args):
        calls.append((name, args))
        if name == "windows.foreground":
            return ToolResult(True, {"handle": 123, "process_id": 9, "title": "Explorer"})
        if name == "processes.details":
            return ToolResult(True, {"executable": r"C:\Windows\explorer.exe"})
        if name == "adapters.explorer_context":
            return ToolResult(True, {"folder": str(root), "selected_files": [str(good), str(outside)]})
        if name == "windows.virtual_desktop":
            return ToolResult(True, {"desktop_id": "test", "on_active_desktop": True})
        raise AssertionError(name)
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.context.inspect()["selected_files"] == []
    assert len(calls) == 1
    result = services.context.inspect(include_selected_files=True)
    assert result["selected_files"] == [str(good)]
    assert result["explorer"]["folder"] == str(root) and result["virtual_desktop"]["desktop_id"] == "test"
    assert not result["stored"] and not result["automatic_cloud_sharing"]
    assert services.context._current["selected_files"] == []
    assert [args for name, args in calls if name == "adapters.explorer_context"] == [{"handle": 123, "process_id": 9}]
    services.settings.set("screenshots.enabled", False)
    calls.clear()
    assert services.context.inspect(include_selected_files=True)["explorer"] is None
    assert [name for name, args in calls] == ["windows.foreground"]
    services.settings.set("context.enabled", False)
    calls.clear()
    with pytest.raises(PermissionError):
        services.context.inspect(include_selected_files=True)
    assert calls == []
