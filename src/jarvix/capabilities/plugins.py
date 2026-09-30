"""Versioned declarative extensions; manifests compose host tools, never execute Python."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from jsonschema import Draft202012Validator

from jarvix.capabilities.files import _linked
from jarvix.capabilities.schema import BOOL, ID, register, string
from jarvix.domain import ToolResult

MANIFEST_SCHEMA = {
    "type": "object", "required": ["id", "name", "version", "sdk_version", "permissions", "capabilities"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,39}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"},
        "sdk_version": {"const": 1},
        "permissions": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 30},
        "capabilities": {"type": "array", "uniqueItems": True, "maxItems": 7,
                         "items": {"enum": ["tools", "services", "integrations", "panels", "commands", "triggers", "actions"]}},
        **{kind: {"type": "array", "maxItems": 30, "items": {
            "type": "object", "required": ["name", "target"], "additionalProperties": False,
            "properties": {"name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,39}$"},
                           "target": {"type": "string", "minLength": 1, "maxLength": 160},
                           "title": {"type": "string", "minLength": 1, "maxLength": 160}}}}
           for kind in ("tools", "services", "integrations", "panels", "commands", "triggers", "actions")},
    },
}


class PluginService:
    def __init__(self, services):
        self.s = services
        self.root = services.data_dir / "plugins"
        self.loaded = {}
        self.errors = {}

    def _manifest(self, id):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", id):
            raise ValueError("Invalid extension ID.")
        path = self.root / id / "jarvix-plugin.json"
        if any(_linked(p) for p in (self.root, path.parent, path)):
            raise PermissionError("Extension manifests cannot use linked paths.")
        if path.stat().st_size > 48000:
            raise ValueError("Extension manifest is too large.")
        with path.open("rb") as source:
            raw = source.read(48001)
        if len(raw) > 48000:
            raise ValueError("Extension manifest is too large.")
        value = json.loads(raw)
        self._validate(value, id)
        return value, hashlib.sha256(raw).hexdigest()

    def _validate(self, value, id):
        Draft202012Validator(MANIFEST_SCHEMA).validate(value)
        if value["id"] != id:
            raise ValueError("Manifest ID must match its directory.")
        for kind in ("tools", "services", "integrations", "panels", "commands", "triggers", "actions"):
            if value.get(kind) and kind not in value["capabilities"]:
                raise ValueError("An extension contribution lacks a declared capability.")
            names = [item["name"] for item in value.get(kind, [])]
            if len(names) != len(set(names)):
                raise ValueError("Extension contribution names must be unique.")
            for item in value.get(kind, []):
                if kind == "triggers":
                    from jarvix.capabilities.workflows import TRIGGERS
                    if item["target"] not in TRIGGERS:
                        raise ValueError("An extension trigger must reference a built-in workflow event.")
                    continue
                spec = self.s.registry.get(item["target"])
                if spec.name.startswith(("plugin.", "plugins.")):
                    raise ValueError("Extensions cannot load extensions or call each other.")
                if spec.permission not in value["permissions"]:
                    raise PermissionError("The target tool's permission must be declared.")

    def install(self, path):
        target = self.s.files.path(path)
        if not target.is_file() or target.stat().st_size > 48000:
            raise ValueError("Choose a bounded JSON manifest.")
        with target.open("rb") as source:
            raw = source.read(48001)
        if len(raw) > 48000:
            raise ValueError("Choose a bounded JSON manifest.")
        value = json.loads(raw)
        id = value.get("id") if isinstance(value, dict) else None
        self._validate(value, id)
        destination = self.root / id
        if _linked(self.root) or destination.exists():
            raise ValueError("Extension already installed or directory unavailable.")
        destination.mkdir(parents=True)
        try:
            with (destination / "jarvix-plugin.json").open("xb") as output:
                output.write(raw)
        except Exception:
            destination.rmdir()
            raise
        return {"id": id, "enabled": False, "fingerprint": hashlib.sha256(raw).hexdigest()}

    def list(self):
        if not self.root.exists():
            return []
        if _linked(self.root):
            raise PermissionError("Extension directory cannot be linked.")
        result = []
        for directory in sorted(self.root.iterdir())[:100]:
            try:
                manifest, fingerprint = self._manifest(directory.name)
                loaded = self.loaded.get(directory.name)
                result.append({"id": manifest["id"], "name": manifest["name"], "version": manifest["version"],
                               "permissions": manifest["permissions"], "capabilities": manifest["capabilities"],
                               "fingerprint": fingerprint, "enabled": bool(loaded and loaded[1] == fingerprint),
                               "status": "Enabled" if loaded and loaded[1] == fingerprint else "Review required"})
            except Exception:
                result.append({"id": directory.name, "status": "Invalid manifest", "enabled": False})
        return result

    def preview(self, id):
        manifest, fingerprint = self._manifest(id)
        return {"manifest": manifest, "fingerprint": fingerprint,
                "execution": "Declarative composition only. No plugin code, shell or automatic external requests."}

    def enable(self, id, fingerprint, enabled=True):
        if not enabled:
            self._unload(id)
            self.s.records.put("plugin_state", {"enabled": False}, id)
            return {"id": id, "enabled": False}
        manifest, current = self._manifest(id)
        if fingerprint != current:
            raise PermissionError("Manifest changed. Review its permissions again.")
        self._unload(id)
        registered = []
        try:
            for item in manifest.get("tools", []):
                target = self.s.registry.get(item["target"])
                name = f"plugin.{id}.{item['name']}"
                spec = replace(target, name=name, description=f"{manifest['name']}: {item.get('title', target.description)}")
                def handler(arguments, target_name=target.name, plugin_id=id, approved=current):
                    if self._manifest(plugin_id)[1] != approved or plugin_id not in self.loaded:
                        return ToolResult(False, error="Extension changed or disabled; review required.")
                    return self.s.execute_tool(target_name, arguments)
                self.s.registry.register(spec, handler)
                registered.append(name)
            self.loaded[id] = (manifest, current, registered)
            self.s.records.put("plugin_state", {"enabled": True, "fingerprint": current}, id)
        except Exception:
            for name in registered:
                self.s.registry.unregister(name)
            raise
        return {"id": id, "enabled": True, "registered_tools": registered}

    def _unload(self, id):
        item = self.loaded.pop(id, None)
        if item:
            for name in item[2]:
                self.s.registry.unregister(name)

    def restore(self):
        for state in self.s.records.list("plugin_state"):
            if state.get("enabled"):
                try:
                    self.enable(state["id"], state["fingerprint"])
                except Exception:
                    self.errors[state["id"]] = "Manifest unavailable or changed; review required."

    def contributions(self, kind):
        if kind not in {"services", "integrations", "panels", "commands", "triggers", "actions"}:
            raise ValueError("Unknown extension capability.")
        result = []
        for id, (manifest, fingerprint, _) in list(self.loaded.items()):
            try:
                if self._manifest(id)[1] == fingerprint:
                    result.extend({**item, "plugin_id": id} for item in manifest.get(kind, []))
            except Exception:
                self.errors[id] = "Manifest unavailable or changed; review required."
        return result

    def invoke(self, id, kind, name, arguments):
        candidates = [item for item in self.contributions(kind) if item["plugin_id"] == id and item["name"] == name]
        if len(candidates) != 1:
            raise ValueError("Enabled contribution not found.")
        return self.s.execute_tool(candidates[0]["target"], arguments)


def setup(s, registry):
    s.plugins = service = PluginService(s)
    register(registry, "plugins.list", "Inspect local declarative extensions and their approval status.", {}, (), service.list)
    register(registry, "plugins.preview", "Review an extension's exact manifest, fingerprint and declared permissions.",
             {"id": ID}, ("id",), service.preview)
    register(registry, "plugins.install", "Import one declarative JSON manifest from an approved file root, disabled until explicitly reviewed.",
             {"path": string()}, ("path",), service.install, 3)
    register(registry, "plugins.invoke", "Call an enabled declared service, integration endpoint or action through its original host-tool permission checks.",
             {"id": ID, "kind": {"enum": ["services", "integrations", "actions"]}, "name": ID,
              "arguments": {"type": "object", "maxProperties": 30}}, ("id", "kind", "name", "arguments"), service.invoke)
    register(registry, "plugins.enable", "Enable or disable this exact reviewed declarative extension. No executable plugin code is loaded.",
             {"id": ID, "fingerprint": string(64), "enabled": BOOL}, ("id", "fingerprint"), service.enable, 3)
    service.restore()
