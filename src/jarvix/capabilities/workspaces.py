"""Configured applications and workspaces; each launch step uses normal tool permissions."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psutil

from jarvix.capabilities.browser import public_url
from jarvix.capabilities.productivity import metadata, row
from jarvix.capabilities.schema import BOOL, ID, array, integer, register, schema, string
from jarvix.runtime import CURRENT, check_cancelled
from jarvix.services import required_text
from jarvix.storage import now_iso


class AppService:
    def __init__(self, s):
        self.s = s

    def list(self, query="", favorites_only=False, recent_only=False):
        items = []
        for app in self.s.list_apps():
            item = {**app, **metadata(self.s, "app.meta", app["id"])}
            if query.casefold() not in (app["name"] + " " + " ".join(item.get("aliases", []))).casefold():
                continue
            if favorites_only and not item.get("favorite"):
                continue
            if recent_only and not item.get("last_launched_at"):
                continue
            # Keep configured argv out of casual app queries and model context.
            item.pop("arguments", None)
            items.append(item)
        items.sort(key=lambda value: value.get("last_launched_at", ""), reverse=True)
        return {"items": items[:100], "truncated": len(items) > 100}

    def configure(self, id, favorite=None, aliases=None):
        row(self.s, "apps", id)
        meta = metadata(self.s, "app.meta", id)
        if aliases is not None:
            values = list(dict.fromkeys(required_text(value, "Alias", 80).casefold() for value in aliases))
            if len(values) > 20:
                raise ValueError("At most 20 aliases per app.")
            for other in self.s.list_apps():
                if other["id"] == id:
                    continue
                existing = [other["name"].casefold(), *metadata(self.s, "app.meta", other["id"]).get("aliases", [])]
                if set(existing) & set(values):
                    raise ValueError("This alias already identifies another app.")
            meta["aliases"] = values
        if favorite is not None:
            meta["favorite"] = favorite
        self.s.records.put("app.meta", meta, id)
        return {"id": id, "favorite": meta.get("favorite", False), "aliases": meta.get("aliases", [])}

    def configure_arguments(self, id, arguments):
        row(self.s, "apps", id)
        if len(arguments) > 30 or any(not isinstance(value, str) or len(value) > 1000
                                      or any(c in value for c in "\0\r\n") for value in arguments):
            raise ValueError("Use at most 30 bounded arguments without control characters.")
        meta = metadata(self.s, "app.meta", id)
        meta["arguments"] = arguments
        self.s.records.put("app.meta", meta, id)
        self.s.repository.audit("app", "Application launch arguments explicitly configured")
        return {"id": id, "argument_count": len(arguments)}

    def arguments_for(self, record_id):
        return metadata(self.s, "app.meta", record_id).get("arguments", [])

    def record_launch(self, record_id):
        meta = metadata(self.s, "app.meta", record_id)
        meta["last_launched_at"] = now_iso()
        meta["launch_count"] = int(meta.get("launch_count", 0)) + 1
        self.s.records.put("app.meta", meta, record_id)

    def open_alias(self, alias):
        needle = required_text(alias, "Alias", 120).casefold()
        matches = [item for item in self.list()["items"] if needle == item["name"].casefold() or needle in item.get("aliases", [])]
        if len(matches) != 1:
            raise ValueError("Use a unique registered app name or alias.")
        return self.s.execute_tool("apps.open", {"id": matches[0]["id"]})

    def installation_folder(self, id):
        app = row(self.s, "apps", id)
        # Normal folder tool enforces roots, including installation directories.
        return self.s.execute_tool("files.open_folder", {"path": str(Path(app["path"]).parent)})

    def discover(self, query=""):
        if os.name != "nt":
            return {"items": [], "supported": False, "reason": "App Paths discovery is Windows-only."}
        import winreg
        discovered = {}
        subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                        for index in range(min(winreg.QueryInfoKey(key)[0], 500)):
                            check_cancelled()
                            try:
                                name = winreg.EnumKey(key, index)
                                with winreg.OpenKey(key, name) as entry:
                                    raw = winreg.QueryValue(entry, None)
                                path = Path(os.path.expandvars(raw.strip().strip('"')))
                                if not path.is_absolute() or path.suffix.casefold() != ".exe" or not path.is_file():
                                    continue
                                path = path.resolve(strict=True)
                                label = path.stem
                                if query.casefold() in (label + " " + name).casefold():
                                    discovered[str(path).casefold()] = {"name": label, "path": str(path), "source": "Windows App Paths"}
                            except (OSError, ValueError, TypeError):
                                continue
                except OSError:
                    continue
        values = list(discovered.values())
        return {"items": sorted(values, key=lambda item: item["name"].casefold())[:100], "supported": True,
                "truncated": len(values) > 100}

    def register_discovered(self, path, name=None):
        resolved = Path(path).resolve(strict=True)
        matches = [item for item in self.discover(resolved.stem)["items"] if Path(item["path"]) == resolved]
        if len(matches) != 1:
            raise ValueError("The executable is not a discovered Windows App Paths entry.")
        return {"id": self.s.add_app(name or matches[0]["name"], str(resolved))}

    def open_project(self, id, project_id):
        app = row(self.s, "apps", id)
        executable = Path(app["path"]).resolve(strict=True)
        if executable.name.casefold() not in {"code.exe", "code-insiders.exe"} or os.name != "nt":
            raise ValueError("This project adapter supports registered Visual Studio Code executables on Windows.")
        project = row(self.s, "projects", project_id)
        folder = self.s.files.path(project["path"])
        if not folder.is_dir():
            raise ValueError("The project's allowed folder is unavailable.")
        check_cancelled()
        process = subprocess.Popen([str(executable), "--new-window", str(folder)], cwd=folder, shell=False,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.record_launch(id)
        return {"pid": process.pid, "project_id": project_id, "requested": True, "verified": False,
                "verification": "Wait for and inspect the project window."}


class WorkspaceService:
    def __init__(self, s):
        self.s = s

    def save(self, name, id=None, app_ids=None, folders=None, urls=None, note_ids=None, automation_ids=None,
             description="", kind="workspace", project_id=None, terminal_directory=None,
             window_layouts=None, workflow_ids=None):
        if kind not in {"workspace", "app_group"}:
            raise ValueError("Unsupported workspace type.")
        current = self.s.records.get("workspace", id) if id else {}
        value = {"name": required_text(name, "Workspace name", 160), "description": description[:2000], "kind": kind}
        for key, incoming, table in (("app_ids", app_ids, "apps"), ("note_ids", note_ids, "notes")):
            values = incoming if incoming is not None else current.get(key, [])
            if len(values) > 20:
                raise ValueError("At most 20 items in each workspace section.")
            for record_id in values:
                row(self.s, table, record_id)
            value[key] = list(dict.fromkeys(values))
        value["folders"] = folders if folders is not None else current.get("folders", [])
        value["urls"] = urls if urls is not None else current.get("urls", [])
        value["automation_ids"] = automation_ids if automation_ids is not None else current.get("automation_ids", [])
        value["project_id"] = project_id if project_id is not None else current.get("project_id")
        if value["project_id"]:
            project = row(self.s, "projects", value["project_id"])
            self.s.files.path(project["path"])
        value["terminal_directory"] = terminal_directory if terminal_directory is not None else current.get("terminal_directory", "")
        if value["terminal_directory"] and not self.s.files.path(value["terminal_directory"]).is_dir():
            raise ValueError("Terminal working directory must be an allowed folder.")
        value["workflow_ids"] = workflow_ids if workflow_ids is not None else current.get("workflow_ids", [])
        if len(value["workflow_ids"]) > 12:
            raise ValueError("Use at most twelve workflows.")
        for workflow_id in value["workflow_ids"]:
            self.s.records.get("workflow", workflow_id)
        value["window_layouts"] = window_layouts if window_layouts is not None else current.get("window_layouts", [])
        if len(value["window_layouts"]) > 12:
            raise ValueError("Use at most twelve window layouts.")
        for layout in value["window_layouts"]:
            from jsonschema import Draft202012Validator
            if not Draft202012Validator(LAYOUT).is_valid(layout):
                raise ValueError("Invalid saved window geometry.")
            row(self.s, "apps", layout["app_id"])
        if any(len(value[key]) > 20 for key in ("folders", "urls", "automation_ids")):
            raise ValueError("At most 20 items in each workspace section.")
        for path in value["folders"]:
            if not self.s.files.path(path).is_dir():
                raise ValueError("Workspace folders must be allowed directories.")
        value["urls"] = [public_url(url) for url in value["urls"]]
        # Only extended routines have an automations.run dispatcher. Legacy
        # timers continue running independently through their existing scheduler.
        known = {item["id"] for item in self.s.records.list("routine")}
        if any(record_id not in known for record_id in value["automation_ids"]):
            raise ValueError("Workspace automation not found.")
        actions = sum(len(value[key]) for key in ("app_ids", "folders", "urls", "automation_ids", "workflow_ids"))
        if actions + bool(value["project_id"]) + bool(value["terminal_directory"]) + bool(value["window_layouts"]) > 24:
            raise ValueError("A workspace may perform at most 24 direct actions; nested routines share the agent step limit.")
        return {"id": self.s.records.put("workspace", value, id)}

    def list(self, query="", kind=None):
        items = [item for item in self.s.records.list("workspace") if query.casefold() in item["name"].casefold()
                 and (kind is None or item.get("kind", "workspace") == kind)]
        return {"items": items[:100], "truncated": len(items) > 100}

    def delete(self, id):
        self.s.records.get("workspace", id)
        self.s.records.delete("workspace", id)
        return {"deleted": True}

    def preview(self, id):
        workspace = self.s.records.get("workspace", id)
        actions = []
        for key, tool, argument in (("app_ids", "apps.open", "id"), ("folders", "files.open_folder", "path"),
                                    ("urls", "web.open", "url"), ("automation_ids", "automations.run", "id"),
                                    ("workflow_ids", "workflows.run", "id")):
            actions.extend({"tool": tool, "arguments": {argument: value}} for value in workspace.get(key, []))
        if workspace.get("project_id"):
            project = row(self.s, "projects", workspace["project_id"])
            for action in actions:
                if action["tool"] == "apps.open":
                    app = row(self.s, "apps", action["arguments"]["id"])
                    if Path(app["path"]).name.casefold() in {"code.exe", "code-insiders.exe"}:
                        action["tool"] = "apps.open_project"
                        action["arguments"]["project_id"] = workspace["project_id"]
            actions.append({"tool": "files.open_folder", "arguments": {"path": project["path"]}})
        if workspace.get("terminal_directory"):
            actions.append({"tool": "workspaces.open_terminal", "arguments": {"path": workspace["terminal_directory"]}})
        if workspace.get("window_layouts"):
            actions.append({"tool": "workspaces.restore_layout", "arguments": {"id": id}})
        if len(actions) > 24:
            raise ValueError("Workspace exceeds the bounded action limit.")
        return {"id": id, "name": workspace["name"], "actions": actions, "note_ids": workspace.get("note_ids", [])}

    def launch(self, id):
        preview = self.preview(id)
        context = CURRENT.get()
        if context and context.checkpoint is None and hasattr(self.s, "operator") and preview["actions"]:
            planned = {"goal": "Start workspace: " + preview["name"], "steps": [
                {"id": f"step{i + 1}", **action} for i, action in enumerate(preview["actions"])]}
            outcome = self.s.operator.run(planned)
            results = [{"tool": step["tool"], "result": outcome.get("results", {}).get(step["id"], {"ok": False})}
                       for step in planned["steps"] if step["id"] in outcome.get("results", {})]
            self._record_launch(id, results, outcome["ok"])
            return {"completed": outcome["ok"], "session_id": outcome["id"], "results": results,
                    "note_ids": preview["note_ids"], "unverified_steps": outcome.get("unverified_steps", [])}
        outcomes = []
        for action in preview["actions"]:
            check_cancelled()
            result = self.s.execute_tool(action["tool"], action["arguments"])
            outcomes.append({"tool": action["tool"], "result": result.as_dict()})
            if not self.s.operator._result_ok(result):
                self._record_launch(id, outcomes, False)
                return {"completed": False, "results": outcomes, "note_ids": preview["note_ids"]}
        self._record_launch(id, outcomes, True)
        self.s.repository.audit("workspace", "Configured workspace launched")
        return {"completed": True, "results": outcomes, "note_ids": preview["note_ids"],
                "unverified_steps": [item["tool"] for item in outcomes
                                     if isinstance(item["result"].get("data"), dict)
                                     and item["result"]["data"].get("verified") is False]}

    def _record_launch(self, id, outcomes, completed):
        owned = []
        for outcome in outcomes:
            data = outcome.get("result", {}).get("data")
            if isinstance(data, dict) and data.get("pid"):
                try:
                    process = psutil.Process(data["pid"])
                    owned.append({"pid": process.pid, "started_at": process.create_time()})
                except psutil.Error:
                    continue
        self.s.records.put("workspace_session", {"workspace_id": id, "owned_processes": owned,
                                                  "completed": completed, "closed": False})
        if completed:
            self.s.settings.set("workspace.active_id", id)
            saved = self.s.records.get("workspace", id)
            if saved.get("project_id") and hasattr(self.s, "context"):
                self.s.context.set_current(project_id=saved["project_id"])

    def open_terminal(self, path):
        if os.name != "nt":
            raise OSError("The native terminal adapter requires Windows.")
        folder = self.s.files.path(path)
        if not folder.is_dir():
            raise ValueError("Choose an allowed terminal directory.")
        from jarvix.capabilities.native_windows import Win32
        executable = Path(Win32().system_directory()) / "WindowsPowerShell/v1.0/powershell.exe"
        check_cancelled()
        process = subprocess.Popen([str(executable), "-NoLogo", "-NoProfile", "-NoExit"], cwd=folder, shell=False,
                                   creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return {"pid": process.pid, "requested": True, "verified": False}

    def _app_windows(self):
        apps = {str(Path(app["path"]).resolve()).casefold(): app["id"] for app in self.s.list_apps()}
        result = self.s.execute_tool("windows.list", {})
        if not result.ok:
            raise PermissionError("Window inspection is unavailable.")
        items = []
        for window in result.data:
            check_cancelled()
            try:
                path = str(Path(psutil.Process(window["process_id"]).exe()).resolve()).casefold()
                if path in apps:
                    items.append({**window, "app_id": apps[path]})
            except (psutil.Error, OSError):
                continue
        return items

    def capture(self, name, id=None):
        layouts = []
        app_ids = []
        windows = self._app_windows()
        for window in windows[:12]:
            target = {key: window[key] for key in ("handle", "process_id")}
            result = self.s.execute_tool("desktop.window_bounds", target)
            if result.ok:
                layouts.append({"app_id": window["app_id"], "title": window["title"][:256], **result.data})
                app_ids.append(window["app_id"])
        if not layouts:
            raise ValueError("No windows belong to registered apps. Register the applications first.")
        result = self.save(name, id=id, app_ids=list(dict.fromkeys(app_ids)), window_layouts=layouts)
        return {**result, "captured_windows": len(layouts), "scope": "Registered application windows only; browser tabs are not collected."}

    def restore_layout(self, id):
        workspace = self.s.records.get("workspace", id)
        windows = self._app_windows()
        outcomes = []
        for layout in workspace.get("window_layouts", []):
            matches = [window for window in windows if window["app_id"] == layout["app_id"]
                       and (not layout.get("title") or window["title"] == layout["title"])]
            if len(matches) != 1:
                outcomes.append({"app_id": layout["app_id"], "verified": False,
                                 "error": "No unique matching window is ready. Open the app, then restore again."})
                continue
            args = {key: matches[0][key] for key in ("handle", "process_id")}
            args.update({key: layout[key] for key in ("x", "y", "width", "height")})
            result = self.s.execute_tool("desktop.move_resize_window", args)
            outcomes.append({"app_id": layout["app_id"], "verified": result.ok and result.data.get("verified", False)})
        return {"verified": bool(outcomes) and all(item["verified"] for item in outcomes), "windows": outcomes}

    def close(self, id):
        sessions = [record for record in self.s.records.list("workspace_session")
                    if record["workspace_id"] == id and not record["closed"]]
        if not sessions:
            raise ValueError("No owned launch session exists for this workspace.")
        owned = {item["pid"]: item["started_at"] for session in sessions for item in session["owned_processes"]}
        windows = self.s.execute_tool("windows.list", {})
        if not windows.ok:
            return {"completed": False, "error": "Window inspection was denied."}
        closed = []
        for window in windows.data:
            check_cancelled()
            pid = window["process_id"]
            try:
                if pid not in owned or abs(psutil.Process(pid).create_time() - owned[pid]) > .001:
                    continue
                result = self.s.execute_tool("windows.close", {"handle": window["handle"], "process_id": pid})
                closed.append({"handle": window["handle"], "requested": result.ok})
            except psutil.Error:
                continue
        remaining = self.s.execute_tool("windows.list", {})
        observed = {window["handle"] for window in remaining.data} if remaining.ok else {entry["handle"] for entry in closed}
        verified = bool(closed) and all(entry["requested"] and entry["handle"] not in observed for entry in closed)
        if verified:
            for session in sessions:
                self.s.records.put("workspace_session", {**session, "closed": True}, session["id"])
            if self.s.settings.get("workspace.active_id") == id:
                self.s.settings.set("workspace.active_id", "")
        return {"requested": bool(closed), "verified": verified, "windows": closed,
                "scope": "Only processes launched by this workspace; pre-existing apps and save dialogs remain under your control."}


LAYOUT = schema({"app_id": ID, "title": string(256, 0), "x": integer(-32768, 32768),
                 "y": integer(-32768, 32768), "width": integer(100, 16384), "height": integer(100, 16384)},
                ("app_id", "x", "y", "width", "height"))


def setup(s, registry):
    apps, workspaces = AppService(s), WorkspaceService(s)
    s.apps, s.workspaces = apps, workspaces
    def launch_tool(id):
        from jarvix.domain import ToolResult
        result = workspaces.launch(id)
        return ToolResult(result["completed"], result, None if result["completed"] else "Workspace stopped before every action completed.")
    def add(name, desc, props, required, handler, level=1):
        register(registry, name, desc, props, required, handler, level,
                 "apps.read" if level == 1 else "apps.write")
    add("apps.search", "Search registered applications by names or aliases, favorites and recent launches.",
        {"query": string(200, 0), "favorites_only": BOOL, "recent_only": BOOL}, [], apps.list)
    add("apps.configure", "Set application favorites and unique custom aliases.",
        {"id": ID, "favorite": BOOL, "aliases": array(string(80), 20)}, ["id"], apps.configure, 2)
    add("apps.configure_arguments", "Configure default app launch arguments. Arguments can execute code; always requires confirmation.",
        {"id": ID, "arguments": array(string(1000, 0), 30)}, ["id", "arguments"], apps.configure_arguments, 3)
    add("apps.open_alias", "Resolve a configured exact app name or alias and launch through permission checks.",
        {"alias": string(120)}, ["alias"], apps.open_alias, 2)
    add("apps.installation_folder", "Open an application's installation folder if it is inside allowed file roots.", {"id": ID}, ["id"], apps.installation_folder, 2)
    add("apps.discover", "Inspect Windows App Paths registry for installed .exe applications; never executes discovery results.",
        {"query": string(200, 0)}, [], apps.discover)
    add("apps.register_discovered", "Explicitly trust and register a discovered application executable for future launches.",
        {"path": string(), "name": string(120)}, ["path"], apps.register_discovered, 3)
    add("apps.open_project", "Open an allowed registered project in a registered Visual Studio Code executable, using fixed project arguments.",
        {"id": ID, "project_id": ID}, ["id", "project_id"], apps.open_project, 2)
    workspace_props = {"name": string(160), "id": ID, "app_ids": array(ID, 20), "folders": array(string(), 20),
                       "urls": array(string(), 20), "note_ids": array(ID, 20), "automation_ids": array(ID, 20),
                       "description": string(2000, 0), "kind": {"enum": ["workspace", "app_group"], "type": "string"},
                       "project_id": ID, "terminal_directory": string(4096, 0),
                       "window_layouts": array(LAYOUT, 12), "workflow_ids": array(ID, 12)}
    add("workspaces.save", "Create or edit reusable workspace/app group with apps, allowed folders, public URLs, notes and automations.",
        workspace_props, ["name"], workspaces.save, 2)
    add("workspaces.list", "Search reusable workspaces and application groups.",
        {"query": string(200, 0), "kind": workspace_props["kind"]}, [], workspaces.list)
    add("workspaces.preview", "Preview all workspace actions before launch.", {"id": ID}, ["id"], workspaces.preview)
    add("workspaces.launch", "Launch a bounded workspace, checking every nested action's permissions and stopping on denial/failure.",
        {"id": ID}, ["id"], launch_tool, 2)
    add("workspaces.delete", "Delete a saved workspace or app group after confirmation.", {"id": ID}, ["id"], workspaces.delete, 3)
    add("workspaces.capture", "Explicitly save open registered apps and their current window layouts as a workspace. Browser tabs are not inspected.",
        {"name": string(160), "id": ID}, ["name"], workspaces.capture, 2)
    add("workspaces.restore_layout", "Restore saved geometry only for uniquely identified registered application windows.",
        {"id": ID}, ["id"], workspaces.restore_layout, 2)
    add("workspaces.close", "Gracefully close only verified processes launched by this workspace; never dismiss save confirmations.",
        {"id": ID}, ["id"], workspaces.close, 3)
    add("workspaces.open_terminal", "Open a Windows PowerShell terminal with profiles disabled in an allowed directory. Does not execute a command.",
        {"path": string()}, ["path"], workspaces.open_terminal, 2)

