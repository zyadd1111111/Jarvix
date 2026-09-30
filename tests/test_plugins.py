import hashlib
import json
from pathlib import Path

from jarvix.capabilities.plugins import PluginService
from jarvix.services import Services


def install(s, target="tasks.list", permission=None):
    folder = s.data_dir / "plugins" / "school"
    folder.mkdir(parents=True)
    manifest = {"id": "school", "name": "School", "version": "1.0.0", "sdk_version": 1,
                "permissions": [permission or s.registry.get(target).permission], "capabilities": ["tools", "panels"],
                "tools": [{"name": "today", "target": target}],
                "panels": [{"name": "tasks", "target": target, "title": "School tasks"}]}
    raw = json.dumps(manifest).encode()
    (folder / "jarvix-plugin.json").write_bytes(raw)
    return folder, hashlib.sha256(raw).hexdigest()


def test_extension_explicit_approval_changed_manifest_and_failure_isolation(tmp_path):
    s = Services(tmp_path)
    s.plugins = PluginService(s)
    folder, fingerprint = install(s)
    assert not s.plugins.list()[0]["enabled"]
    s.plugins.enable("school", fingerprint)
    assert s.execute_tool("plugin.school.today", {}, approve=lambda _: True).ok
    (folder / "jarvix-plugin.json").write_text("{}")
    assert not s.execute_tool("plugin.school.today", {}).ok
    assert s.execute_tool("tasks.list", {}, approve=lambda _: True).ok
    s.close()


def test_plugin_cannot_lower_target_permission(tmp_path):
    s = Services(tmp_path)
    s.plugins = PluginService(s)
    target = s.registry.get("conversations.delete")
    _, fingerprint = install(s, target.name, target.permission)
    s.plugins.enable("school", fingerprint)
    s.permissions.set_grant("plugin.school.today", "allow")
    s.settings.set("control.enabled", True)
    note = s.new_conversation("Keep")
    asked = []
    result = s.execute_tool("plugin.school.today", {"id": note}, approve=lambda req: asked.append(req) or False)
    assert not result.ok and len(asked) == 1 and s.list_conversations()
    s.close()


def test_shipped_manifest_matches_host_permissions(tmp_path):
    s = Services(tmp_path)
    manifest = json.loads((Path(__file__).parents[1] / "examples/plugins/daily_focus/jarvix-plugin.json").read_text())
    s.plugins._validate(manifest, manifest["id"])
    s.close()
