"""App-specific actions composed from native interfaces and existing host tools."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psutil

from jarvix.capabilities.productivity import row
from jarvix.capabilities.schema import ID, integer, register, string
from jarvix.capabilities.desktop import PROTECTED, SECRET, redact
from jarvix.domain import ToolResult
from jarvix.runtime import check_cancelled

VSCODE_NAMES = {"code.exe", "code-insiders.exe"}
ADAPTERS = {
    "vscode": {"open_project": "apps.open_project", "open_file": "adapters.vscode_open_file",
               "command_palette": "adapters.vscode_command_palette", "focus_terminal": "adapters.vscode_focus_terminal",
               "window_context": "adapters.vscode_context", "git_status": "developer.git_status",
               "git_diff": "developer.git_diff", "find_files": "developer.find_files"},
    "explorer": {"open_folder": "files.open_folder", "inspect": "adapters.explorer_context",
                 "navigate": "adapters.explorer_navigate", "reveal_file": "files.reveal", "list_files": "files.list",
                 "copy": "files.copy", "move": "files.move", "rename": "files.rename",
                 "create_folder": "files.create_folder", "recycle": "files.recycle"},
    "terminal": {"open_directory": "workspaces.open_terminal", "command_preview": "developer.command_preview",
                 "command_start": "developer.command_start", "command_output": "developer.command_output",
                 "command_cancel": "developer.command_cancel", "command_history": "developer.command_history"},
    "chrome": {"active_tab": "browser.active_tab", "tabs": "browser.tabs", "open_tab": "browser.tab_open",
               "find_element": "browser.find_element", "inspect_page": "browser.inspect_page",
               "selected_text": "browser.selected_text", "downloads": "browser.downloads"},
    "edge": {"active_tab": "browser.active_tab", "tabs": "browser.tabs", "open_tab": "browser.tab_open",
             "find_element": "browser.find_element", "inspect_page": "browser.inspect_page",
             "selected_text": "browser.selected_text", "downloads": "browser.downloads"},
    "spotify": {"playback": "spotify.playback", "devices": "spotify.devices", "play": "spotify.play",
                "pause": "spotify.pause", "next": "spotify.next", "previous": "spotify.previous"},
}


class AppAdapterService:
    def __init__(self, services):
        self.s = services

    def capabilities(self):
        known = {tool.name: tool for tool in self.s.registry.specs()}
        items = []
        for adapter, actions in ADAPTERS.items():
            descriptors = []
            for action, name in actions.items():
                spec = known.get(name)
                reason = self._unavailable(adapter, name) if spec else "Tool is not registered."
                descriptors.append({"name": action, "tool": name, "supported": spec is not None,
                                    "available": not reason, "unavailable_reason": reason,
                                    "schema": spec.parameters if spec else None,
                                    "permission": spec.permission if spec else None,
                                    "permission_level": spec.permission_level if spec else None,
                                    "confirmation_required": bool(spec and spec.permission_level == 3)})
            items.append({"id": adapter, "actions": descriptors})
        return {"items": items,
            "resolution_order": ["native_api", "app_adapter", "accessibility", "vision", "coordinates"],
            "browser_requirement": "Explicitly connected Chrome/Edge native extension; actions target that connection.",
            "downloads_requirement": "The extension also requires a separate Downloads permission; checked when requested.",
            "spotify_requirement": "Authenticated Spotify account with permitted playback device.",
            "workspace_detection": "VS Code title matches and editor labels are hints, not verified workspace/file paths.",
            "diagnostics": {"available": False, "reason": "No VS Code diagnostics API is connected."},
            "terminal_scope": "Only Jarvix-owned command sessions expose working directory, state and output. External shells are not inspected."}

    def _unavailable(self, adapter, tool):
        if tool.startswith("adapters.") or tool in {"apps.open_project", "files.open_folder", "files.reveal", "files.recycle", "workspaces.open_terminal"}:
            if os.name != "nt":
                return "This action requires Windows."
        if tool.startswith("adapters.") and tool != "adapters.vscode_open_file":
            if not self.s.settings.get("screenshots.enabled", False):
                return "Enable screen access to inspect or control this app window."
        if adapter in {"chrome", "edge"}:
            status = self.s.browser.status()
            if not status["connected"] or status["browser"] != adapter:
                return "Explicitly connect this browser before using its adapter."
        return None

    def resolve(self, adapter, action):
        """Return the existing tool contract; adding an app means extending ADAPTERS."""
        if not isinstance(adapter, str) or not isinstance(action, str):
            raise ValueError("Choose a known app adapter and action.")
        tool = ADAPTERS.get(adapter, {}).get(action)
        if tool is None:
            raise ValueError("Unknown app adapter action.")
        return self.s.registry.get(tool)

    def execute(self, adapter, action, arguments, approve=None, cancel=None, on_event=None):
        """Dispatch through the host boundary, retaining schema, approval and cancellation checks."""
        try:
            spec = self.resolve(adapter, action)
        except ValueError:
            return ToolResult(False, error="Unknown or unavailable app adapter action.")
        reason = self._unavailable(adapter, spec.name)
        if reason:
            return ToolResult(False, error=reason)
        return self.s.execute_tool(spec.name, arguments, approve=approve, cancel=cancel, on_event=on_event)

    def _vscode(self, id):
        app = row(self.s, "apps", id)
        target = Path(app["path"]).resolve(strict=True)
        if os.name != "nt" or not target.is_file() or target.name.casefold() not in VSCODE_NAMES:
            raise ValueError("Select a registered Visual Studio Code executable on Windows.")
        return target

    def _window(self, handle, process_id, executable):
        self.s.desktop._screen_gate()
        self.s.desktop._window(handle, process_id)
        try:
            running = Path(psutil.Process(process_id).exe()).resolve(strict=True)
        except (psutil.Error, OSError) as exc:
            raise ValueError("The application's executable is unavailable. Inspect the window again.") from exc
        if running != executable:
            raise ValueError("The window belongs to a different application.")

    def vscode_open_file(self, id, path, line=1, column=1):
        executable = self._vscode(id)
        target = self.s.files.path(path)
        if not target.is_file() or type(line) is not int or type(column) is not int or not (1 <= line <= 1000000 and 1 <= column <= 10000):
            raise ValueError("Choose an approved regular file and bounded line/column.")
        check_cancelled()
        process = subprocess.Popen([str(executable), "--reuse-window", "--goto", f"{target}:{line}:{column}"],
                                   cwd=target.parent, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.s.apps.record_launch(id)
        self.s.repository.audit("app", "VS Code file-open requested")
        return {"pid": process.pid, "path": str(target), "line": line, "column": column,
                "requested": True, "verified": False, "verification": "Inspect the resulting editor window."}

    def vscode_shortcut(self, id, handle, process_id, action):
        codes = {"command_palette": [0x11, 0x10, 0x50], "focus_terminal": [0x11, 0xC0]}
        if action not in codes:
            raise ValueError("Only the named VS Code navigation shortcuts are supported.")
        executable = self._vscode(id)
        self._window(handle, process_id, executable)
        desktop = self.s.desktop
        snapshot = desktop.backend.call("inspect", handle=handle, process_id=process_id, limit=1)
        with desktop._action("VS Code " + action, handle, process_id):
            self._window(handle, process_id, executable)
            # Fixed keys only. Native UIA rechecks process lifetime, window and protected focus.
            return desktop.backend.call("shortcut", handle=handle, process_id=process_id, codes=codes[action],
                                        root_id=snapshot["root_id"], process_started=snapshot["process_started"])

    def vscode_context(self, id, handle, process_id, project_id=None):
        executable = self._vscode(id)
        self._window(handle, process_id, executable)
        inspected = self.s.execute_tool("desktop.inspect_ui", {"handle": handle, "process_id": process_id, "limit": 80})
        if not inspected.ok:
            return inspected
        tree = inspected.data
        self._window(handle, process_id, executable)
        title = self.s.desktop._window(handle, process_id)["title"]
        candidates = []
        projects = self.s.execute_tool("projects.list", {})
        if not projects.ok:
            return projects
        for project in projects.data["items"]:
            try:
                root = self.s.files.path(project["path"])
                if root.is_dir() and (title.startswith(root.name + " - ") or title.startswith(project["name"] + " - ")):
                    candidates.append({"id": project["id"], "name": project["name"], "path": str(root)})
            except (OSError, ValueError):
                continue
        # ponytail: bounded UIA labels are hints; verified editor paths/diagnostics need a VS Code API.
        tabs = [{"name": element["name"], "focused": bool(element.get("focused")),
                 "element_ref": element.get("element_ref")}
                for element in tree["elements"]
                if element.get("control_type") == "TabItem" and not element.get("password")
                and not element.get("offscreen") and element.get("name")]
        file_label = title.split(" - ", 1)[0]
        active_file = None if (" - " not in title or PROTECTED.search(file_label) or SECRET.search(file_label)) else redact(file_label)
        project, git = None, None
        if project_id is not None:
            selected = row(self.s, "projects", project_id)
            root = self.s.files.path(selected["path"])
            if not root.is_dir():
                raise ValueError("Choose an approved registered project folder.")
            project = {"id": project_id, "name": selected["name"], "path": str(root), "window_workspace_verified": False}
            result = self.s.execute_tool("developer.git_status", {"path": str(root)})
            git = {"available": result.ok, "result": result.data if result.ok else None, "error": result.error}
        self._window(handle, process_id, executable)
        return {"handle": handle, "process_id": process_id, "application": tree["application"],
                "elements": tree["elements"], "workspace_candidates": candidates[:20],
                "workspace_verified": False, "editor_tab_hints": tabs[:40], "active_file_hint": active_file,
                "file_paths_verified": False, "bounded": tree.get("bounded", False), "project": project, "git": git,
                "terminal": {"visible": any("terminal" in element.get("name", "").casefold()
                                               for element in tree["elements"] if not element.get("password")),
                             "output_available": False, "reason": "Use owned command sessions for terminal state/output."},
                "diagnostics": {"available": False, "reason": "No VS Code diagnostics API is connected."},
                "source": "UI Automation and approved registered project title matches"}

    def _explorer(self, handle, process_id):
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
        self._window(handle, process_id, executable.resolve(strict=True))

    def explorer_context(self, handle, process_id):
        self._explorer(handle, process_id)
        result = self.s.windows_integration.explorer_snapshot(handle, process_id)
        self._explorer(handle, process_id)
        return result

    def explorer_navigate(self, handle, process_id, path):
        target = self.s.files.path(path)
        if not target.is_dir():
            raise ValueError("Navigate to an approved folder.")
        self._explorer(handle, process_id)
        with self.s.desktop._action("Explorer navigate", handle, process_id):
            self._explorer(handle, process_id)
            result = self.s.windows_integration._call("navigate", handle=handle, path=str(target))
            self._explorer(handle, process_id)
            observed = self.s.windows_integration.explorer_snapshot(handle, process_id)
            # Shell navigation can be asynchronous. Never report a request as verified completion.
            return {**result, "path": str(target), "verified": observed["folder"] == str(target),
                    "observed_folder": observed["folder"], "verification": "Reinspect if navigation is still pending."}


def setup(s, registry):
    s.app_adapters = service = AppAdapterService(s)
    register(registry, "adapters.capabilities", "List app-specific actions backed by registered host tools and native interfaces.",
             {}, [], service.capabilities)
    register(registry, "adapters.vscode_open_file", "Open an approved file at a line/column in registered VS Code. No arbitrary launch arguments.",
             {"id": ID, "path": string(), "line": integer(1, 1000000), "column": integer(1, 10000)},
             ["id", "path"], service.vscode_open_file, 2, "computer.control")
    window = {"handle": integer(1, 2**53), "process_id": integer(1, 2**31)}
    for action in ("command_palette", "focus_terminal"):
        register(registry, "adapters.vscode_" + action, "Request VS Code " + action.replace("_", " ") + " in an inspected registered app window. No command is entered or executed.",
                 {"id": ID, **window}, ["id", "handle", "process_id"],
                 lambda id, handle, process_id, action=action: service.vscode_shortcut(id, handle, process_id, action),
                 3, "computer.control")
    register(registry, "adapters.vscode_context", "Inspect a registered VS Code window for editor/workspace hints; optionally read Git status for an explicitly selected project. No editor values or external terminal output.",
             {"id": ID, **window, "project_id": ID}, ["id", "handle", "process_id"], service.vscode_context)
    register(registry, "adapters.explorer_context", "Read the current folder and selected files from a known Explorer window; approved paths only.",
             window, ["handle", "process_id"], service.explorer_context)
    register(registry, "adapters.explorer_navigate", "Navigate a known Explorer window to an approved folder through Shell COM; visible HUD required.",
             {**window, "path": string()}, ["handle", "process_id", "path"], service.explorer_navigate, 2, "computer.control")
