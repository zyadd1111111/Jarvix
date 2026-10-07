import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarvix.data_protection import PREFIX
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    yield service
    service.close()


def test_profile_persists_only_explicit_references_and_requires_reactivation(services, monkeypatch):
    services.settings.set("context.enabled", True)
    project = services.add_project("Code", str(services.test_root))
    workspace = services.workspaces.save("Coding", project_id=project)["id"]
    mission = services.missions.save("Release", project_id=project)["id"]
    conversation = services.new_conversation("Personal conversation")
    memory = services.productivity.memories.remember_sensitive("My editor is Vim", project_id=project)["id"]
    services.context.remember_session("temporary-private-value", conversation_id=conversation)
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: pytest.fail("Profiles must not inspect apps or replay actions"))
    saved = services.context.save_profile("Coding", project_id=project, workspace_id=workspace,
                                         mission_id=mission, conversation_id=conversation, memory_ids=[memory, memory])
    assert not saved["activated"]
    activated = services.context.activate_profile(saved["id"])
    assert activated["selection"]["memory_ids"] == [memory] and not activated["actions_replayed"]
    assert services.context.selection()["mission_id"] == mission
    assert "temporary-private-value" not in json.dumps(services.records.list("context.profile"))
    assert "My editor is Vim" not in json.dumps(services.context.get_profile(saved["id"]))
    assert "My editor is Vim" not in json.dumps(services.context_preview(conversation, "openai", "model"))
    data_dir = services.data_dir
    services.close()
    reopened = Services(data_dir, vault=SimpleNamespace(get=lambda _: None))
    try:
        assert reopened.context.list_profiles()["items"][0]["id"] == saved["id"]
        assert reopened.context.selection()["profile_id"] is None
        assert not reopened.context.session(conversation)["items"]
        assert reopened.context.activate_profile(saved["id"])["selection"]["project_id"] == project
    finally:
        reopened.close()


def test_save_and_delete_need_fresh_permission_and_do_not_mutate_after_denial(services):
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("context.profiles.save", "allow")
    assert not services.execute_tool("context.profiles.save", {"name": "Private"}).ok
    saved = services.execute_tool("context.profiles.save", {"name": "Private"}, approve=lambda _: True)
    assert saved.ok
    id = saved.data["id"]
    assert not services.execute_tool("context.profiles.save", {"name": "Changed", "id": id}).ok
    assert services.context.get_profile(id)["name"] == "Private"
    services.permissions.set_grant("context.profiles.delete", "allow")
    assert not services.execute_tool("context.profiles.delete", {"id": id}).ok
    assert services.execute_tool("context.profiles.delete", {"id": id}, approve=lambda _: True).ok
    assert not services.context.list_profiles()["items"]
    assert services.registry.get("context.profiles.save").permission_level == 3
    assert services.registry.get("context.profiles.delete").permission_level == 3


def test_profile_activation_is_opt_in_and_rechecks_roots_grants_and_memory_expiry(services):
    project = services.add_project("Code", str(services.test_root))
    memory = services.productivity.memories.remember_sensitive("Release style guide", project_id=project)["id"]
    id = services.context.save_profile("Code", project_id=project, memory_ids=[memory])["id"]
    with pytest.raises(PermissionError):
        services.context.activate_profile(id)
    services.settings.set("context.enabled", True)
    services.context.activate_profile(id)
    services.productivity.memories.edit(memory, expires_at="2000-01-01T00:00:00Z")
    services.remove_file_root(str(services.test_root))
    inspected = services.context.get_profile(id)
    assert inspected["unavailable"] == 2
    assert services.context.selection()["project_id"] is None
    assert services.context.selection()["memory_ids"] == []
    activated = services.context.activate_profile(id)
    assert activated["selection"]["project_id"] is None and not activated["selection"]["memory_ids"]
    services.add_file_root(str(services.test_root))
    services.permissions.set_grant("projects.list", "deny")
    assert not next(item for item in services.context.get_profile(id)["references"] if item["kind"] == "project")["available"]
    services.permissions.set_grant("context.profiles.get", "deny")
    with pytest.raises(PermissionError):
        services.context.get_profile(id)
    assert services.context.selection()["profile_id"] is None


def test_profile_validation_is_atomic_and_memory_scope_is_not_broadened(services):
    project = services.add_project("Code", str(services.test_root))
    memory = services.productivity.memories.remember_sensitive("Scoped coding convention", project_id=project)["id"]
    id = services.context.save_profile("Code", project_id=project, memory_ids=[memory])["id"]
    with pytest.raises(ValueError):
        services.context.save_profile("Changed", id=id, project_id="")
    assert services.context.get_profile(id)["name"] == "Code"
    with pytest.raises(ValueError):
        services.context.save_profile("Missing", memory_ids=["missing"])
    services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "memory.search"])
    assert not next(item for item in services.context.get_profile(id)["references"] if item["kind"] == "memory")["available"]
    with pytest.raises(ValueError):
        services.context.save_profile("Unavailable", project_id=project, memory_ids=[memory])


def test_unset_clear_and_forget_preserve_original_records_and_temporary_context(services):
    services.settings.set("context.enabled", True)
    project = services.add_project("Code", str(services.test_root))
    memory = services.productivity.memories.remember_sensitive("Default editor preference")["id"]
    conversation = services.new_conversation("Conversation")
    id = services.context.save_profile("Code", project_id=project, conversation_id=conversation, memory_ids=[memory])["id"]
    services.context.activate_profile(id)
    services.context.remember_session("Ephemeral text", conversation_id=conversation)
    services.context.unset_profile()
    assert services.context.selection()["profile_id"] is None
    assert services.context.session(conversation)["items"]
    services.context.activate_profile(id)
    services.context.clear()
    assert services.context.selection()["profile_id"] is None and not services.context.session(conversation)["items"]
    services.context.activate_profile(id)
    services.context.delete_profile(id)
    assert services.context.selection()["profile_id"] is None
    assert services.list_projects()[0]["id"] == project and services.list_memories()[0]["id"] == memory
    assert services.list_conversations()[0]["id"] == conversation


def test_profiles_use_existing_record_encryption(services):
    services.data_protection.configure(True)
    id = services.context.save_profile("Private profile name")["id"]
    with sqlite3.connect(services.db.path) as raw:
        value = raw.execute("SELECT data FROM records WHERE kind='context.profile' AND id=?", (id,)).fetchone()[0]
    assert value.startswith(PREFIX) and "Private profile name" not in value
    assert services.context.get_profile(id)["name"] == "Private profile name"


@pytest.mark.parametrize("source", ["context.session_inspect", "conversations.search", "projects.list", "files.inspect", "tasks.list"])
@pytest.mark.parametrize("during_approval", [False, True])
def test_session_promotion_rechecks_original_sources_before_and_after_memory_approval(services, source, during_approval):
    services.settings.set("context.enabled", True)
    conversation = services.new_conversation("Source conversation")
    project = services.add_project("Source project", str(services.test_root))
    file = services.test_root / "source.txt"
    file.write_text("Local")
    saved = services.context.remember_session("Selected user text", conversation_id=conversation,
        state={"project_id": project, "active_files": [str(file)],
               "recent_results": [{"tool": "tasks.list", "ok": True}]})
    requested = []
    if not during_approval:
        services.permissions.set_grant(source, "deny")
    def approve(request):
        requested.append(request.tool_name)
        if during_approval and request.tool_name == "memory.remember_sensitive":
            services.permissions.set_grant(source, "deny")
        return True
    result = services.execute_tool("context.session_promote", {"id": saved["id"], "conversation_id": conversation}, approve=approve)
    assert not result.ok and not services.list_memories()
    assert (("memory.remember_sensitive" in requested) == during_approval)
    services.permissions.set_grant(source, None)
    assert services.context.session(conversation)["items"][0]["id"] == saved["id"]


@pytest.mark.parametrize("change", ["expire", "forget", "remove_root", "disable_inspect", "turn_off_context"])
def test_promotion_invalidated_while_confirming_never_creates_permanent_memory(services, monkeypatch, change):
    from jarvix.capabilities import context
    clock = [0.0]
    monkeypatch.setattr(context, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    services.settings.set("context.enabled", True)
    project = services.add_project("Source", str(services.test_root))
    saved = services.context.remember_session("Temporary text", ttl_seconds=30, state={"project_id": project})
    def approve(request):
        if request.tool_name == "memory.remember_sensitive":
            if change == "expire":
                clock[0] = 31.0
            elif change == "forget":
                services.context.forget_session(saved["id"])
            elif change == "remove_root":
                services.remove_file_root(str(services.test_root))
            elif change == "disable_inspect":
                services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "context.session_inspect"])
            else:
                services.settings.set("context.enabled", False)
        return True
    assert not services.execute_tool("context.session_promote", {"id": saved["id"]}, approve=approve).ok
    assert not services.list_memories()


def test_explicit_promotion_keeps_selected_text_and_original_conversation_when_selection_changes(services):
    services.settings.set("context.enabled", True)
    original = services.new_conversation("Original")
    other = services.new_conversation("Other")
    services.context.set_current(conversation_id=original)
    source = services.context.remember_session("Keep only the selected sentence. Omit this sentence.")
    other_source = services.context.remember_session("Other conversation text", conversation_id=other)
    def approve(request):
        if request.tool_name == "memory.remember_sensitive":
            assert request.arguments["content"] == "Keep only the selected sentence."
            services.context.set_current(conversation_id=other)
        return True
    promoted = services.execute_tool("context.session_promote", {"id": source["id"], "content": "Keep only the selected sentence."},
                                    approve=approve)
    assert promoted.ok and promoted.data["saved"] and promoted.data["temporary_entry_removed"]
    assert services.list_memories()[0]["content"] == "Keep only the selected sentence."
    assert services.productivity.memories.explain(promoted.data["id"])["source_kind"] == "conversation"
    assert not services.context.session(original)["items"]
    assert services.context.session(other)["items"][0]["id"] == other_source["id"]
