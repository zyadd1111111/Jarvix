"""App-specific actions composed from native interfaces and existing host tools."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psutil

from jarvix.capabilities.productivity import row
from jarvix.capabilities.schema import ID, integer, register, string
from jarvix.runtime import check_cancelled

VSCODE_NAMES = {"code.exe", "code-insiders.exe"}
ADAPTERS = {
    "vscode": {"open_project": "apps.open_project", "open_file": "adapters.vscode_open_file",
               "command_palette": "adapters.vscode_command_palette", "focus_terminal": "adapters.vscode_focus_terminal",
               "window_context": "adapters.vscode_context"},
    "explorer": {"open_folder": "files.open_folder", "inspect": "adapters.explorer_context",
                 "navigate": "adapters.explorer_navigate", "reveal_file": "files.reveal"},
    "terminal": {"open_directory": "workspaces.open_terminal"},
    "chrome": {"active_tab": "browser.active_tab", "tabs": "browser.tabs", "open_tab": "browser.tab_open",
               "find_element": "browser.find_element", "inspect_page": "browser.inspect_page"},
    "edge": {"active_tab": "browser.active_tab", "tabs": "browser.tabs", "open_tab": "browser.tab_open",
             "find_element": "browser.find_element", "inspect_page": "browser.inspect_page"},
    "spotify": {"playback": "spotify.playback", "devices": "spotify.devices", "play": "spotify.play",
                "pause": "spotify.pause", "next": "spotify.next", "previous": "spotify.previous"},
}


class AppAdapterService:
    def __init__(self, services):
        self.s = services

    def capabilities(self):
        known = {tool.name: tool for tool in self.s.registry.specs()}
        return {"items": [{"id": adapter, "actions": [
            {"name": action, "tool": tool, "schema": known[tool].parameters,
             "permission_level": known[tool].permission_level}
            for action, tool in actions.items() if tool in known]
            } for adapter, actions in ADAPTERS.items()],
            "browser_requirement": "Explicitly connected Chrome/Edge native extension; actions target that connection.",
            "spotify_requirement": "Authenticated Spotify account with permitted playback device.",
            "workspace_detection": "VS Code title matches are candidates, not verified workspace paths."}

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

    def vscode_context(self, id, handle, process_id):
        executable = self._vscode(id)
        self._window(handle, process_id, executable)
        tree = self.s.desktop.inspect_ui(handle, process_id, limit=80)
        self._window(handle, process_id, executable)
        title = self.s.desktop._window(handle, process_id)["title"]
        candidates = []
        for project in self.s.list_projects():
            try:
                root = self.s.files.path(project["path"])
                if root.is_dir() and (title.startswith(root.name + " - ") or title.startswith(project["name"] + " - ")):
                    candidates.append({"id": project["id"], "name": project["name"], "path": str(root)})
            except (OSError, ValueError):
                continue
        return {"handle": handle, "process_id": process_id, "application": tree["application"],
                "elements": tree["elements"], "workspace_candidates": candidates[:20],
                "workspace_verified": False, "source": "UI Automation and approved registered project title matches"}

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
    register(registry, "adapters.vscode_context", "Inspect a known registered VS Code window and show unverified registered workspace candidates.",
             {"id": ID, **window}, ["id", "handle", "process_id"], service.vscode_context)
    register(registry, "adapters.explorer_context", "Read the current folder and selected files from a known Explorer window; approved paths only.",
             window, ["handle", "process_id"], service.explorer_context)
    register(registry, "adapters.explorer_navigate", "Navigate a known Explorer window to an approved folder through Shell COM; visible HUD required.",
             {**window, "path": string()}, ["handle", "process_id", "path"], service.explorer_navigate, 2, "computer.control")
