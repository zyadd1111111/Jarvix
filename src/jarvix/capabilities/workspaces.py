"""Configured applications and workspaces; each launch step uses normal tool permissions."""
from __future__ import annotations

import os
from pathlib import Path

from jarvix.capabilities.browser import public_url
from jarvix.capabilities.productivity import metadata, row
from jarvix.capabilities.schema import BOOL, ID, array, register, string
from jarvix.runtime import check_cancelled
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


class WorkspaceService:
    def __init__(self, s):
        self.s = s

    def save(self, name, id=None, app_ids=None, folders=None, urls=None, note_ids=None, automation_ids=None,
             description="", kind="workspace"):
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
        if sum(len(value[key]) for key in ("app_ids", "folders", "urls", "automation_ids")) > 24:
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
                                    ("urls", "web.open", "url"), ("automation_ids", "automations.run", "id")):
            actions.extend({"tool": tool, "arguments": {argument: value}} for value in workspace.get(key, []))
        if len(actions) > 24:
            raise ValueError("Workspace exceeds the bounded action limit.")
        return {"id": id, "name": workspace["name"], "actions": actions, "note_ids": workspace.get("note_ids", [])}

    def launch(self, id):
        preview = self.preview(id)
        outcomes = []
        for action in preview["actions"]:
            check_cancelled()
            result = self.s.execute_tool(action["tool"], action["arguments"])
            outcomes.append({"tool": action["tool"], "result": result.as_dict()})
            if not result.ok:
                return {"completed": False, "results": outcomes, "note_ids": preview["note_ids"]}
        self.s.repository.audit("workspace", "Configured workspace launched")
        return {"completed": True, "results": outcomes, "note_ids": preview["note_ids"]}


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
    workspace_props = {"name": string(160), "id": ID, "app_ids": array(ID, 20), "folders": array(string(), 20),
                       "urls": array(string(), 20), "note_ids": array(ID, 20), "automation_ids": array(ID, 20),
                       "description": string(2000, 0), "kind": {"enum": ["workspace", "app_group"], "type": "string"}}
    add("workspaces.save", "Create or edit reusable workspace/app group with apps, allowed folders, public URLs, notes and automations.",
        workspace_props, ["name"], workspaces.save, 2)
    add("workspaces.list", "Search reusable workspaces and application groups.",
        {"query": string(200, 0), "kind": workspace_props["kind"]}, [], workspaces.list)
    add("workspaces.preview", "Preview all workspace actions before launch.", {"id": ID}, ["id"], workspaces.preview)
    add("workspaces.launch", "Launch a bounded workspace, checking every nested action's permissions and stopping on denial/failure.",
        {"id": ID}, ["id"], launch_tool, 2)
    add("workspaces.delete", "Delete a saved workspace or app group after confirmation.", {"id": ID}, ["id"], workspaces.delete, 3)
