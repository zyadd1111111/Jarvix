"""Cross-service permission, nested execution, migration and disclosure guarantees."""
import json
import sqlite3
import threading
import subprocess
import sys
from types import SimpleNamespace

import pytest

from jarvix.domain import Completion, Message, ToolCall, ToolResult, ToolSpec
from jarvix.runtime import operation
from jarvix.services import Services
from jarvix.storage import SCHEMA


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile")
    yield service
    service.close()


def test_levels_and_saved_grant_cannot_authorize_sensitive_action(services):
    approvals = []
    def approve(request):
        approvals.append(request)
        return True
    assert services.execute_tool("tasks.list", {}, approve=approve).ok
    assert not approvals
    assert not services.execute_tool("notes.create", {"title": "No", "body": "Denied"}).ok
    services.settings.set("control.enabled", True)
    assert services.execute_tool("notes.create", {"title": "Yes", "body": "Local"}, approve=approve).ok
    assert not approvals
    services.permissions.set_grant("memory.remember", "allow")
    assert not services.execute_tool("memory.remember", {"content": "secret"}).ok
    assert services.execute_tool("memory.remember", {"content": "explicit"}, approve=approve).ok
    assert [request.tool_name for request in approvals] == ["memory.remember"]
    assert not services.execute_tool("memory.remember", {"content": "again"}).ok


@pytest.mark.parametrize("permission,gate", [("clipboard.read", "clipboard.enabled"),
    ("screen.capture", "screenshots.enabled"), ("microphone.listen", "microphone.enabled")])
def test_opt_in_gate_cannot_be_bypassed_by_approval(services, permission, gate):
    reached = []
    services.registry.register(ToolSpec("test.gated", "Test gate", {"type": "object"}, permission,
                                        permission_level=1), lambda _: reached.append(True) or ToolResult(True))
    assert not services.execute_tool("test.gated", {}, approve=lambda _: True).ok
    assert reached == []
    services.settings.set(gate, True)
    assert services.execute_tool("test.gated", {}).ok


def test_explicit_deny_overrides_control_switch(services):
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("tasks.list", "deny")
    assert not services.execute_tool("tasks.list", {}, approve=lambda _: True).ok


def test_cancel_before_execution_and_nested_step_budget(services):
    cancel = threading.Event()
    cancel.set()
    assert not services.execute_tool("notes.create", {"title": "No", "body": ""},
                                     approve=lambda _: True, cancel=cancel).ok
    with operation(max_steps=1):
        assert services.execute_tool("tasks.list", {}).ok
        assert not services.execute_tool("tasks.list", {}).ok
    assert services.list_notes() == []


def test_unattended_routine_cannot_use_saved_sensitive_grant(services):
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("memory.remember", "allow")
    routine = services.automation.save("Sensitive", [{"tool": "memory.remember", "arguments": {"content": "private"}}])["id"]
    assert not services.automation.run(routine, unattended=True)["ok"]
    assert not services.list_memories()
    approvals = []
    result = services.execute_tool("automations.run", {"id": routine},
        approve=lambda request: approvals.append(request.tool_name) or True)
    assert result.ok and result.data["ok"]
    assert approvals == ["memory.remember"]
    assert "private" not in json.dumps(services.automation.history())
    assert "private" not in json.dumps(services.activity())


def test_unattended_routine_still_honors_individual_tool_deny(services):
    services.permissions.set_grant("tasks.list", "deny")
    routine = services.automation.save("Denied", [{"tool": "tasks.list", "arguments": {}}])["id"]
    assert not services.automation.run(routine, unattended=True)["ok"]


def test_file_trigger_establishes_baseline_then_fires_on_new_file(services, tmp_path):
    root = tmp_path / "watched"
    root.mkdir()
    services.add_file_root(str(root))
    services.automation.save("New file", [{"tool": "tasks.list", "arguments": {}}],
                             "file_created", {"path": str(root)})
    assert services.automation.tick() == []
    (root / "first.txt").write_text("new")
    assert services.automation.tick()[0]["ok"]
    assert services.automation.tick() == []


def test_notification_delivery_does_not_drop_more_than_five(services):
    for index in range(7):
        services.notifications.create(str(index))
    assert len(services.notifications.delivery()) == 5
    assert len(services.notifications.delivery()) == 2
    services.notifications.preferences(do_not_disturb=True)
    services.notifications.create("quiet")
    assert services.notifications.delivery() == []
    assert any(row["title"] == "quiet" for row in services.notifications.list())


def test_v1_profile_migrates_without_losing_records_or_grants(tmp_path):
    profile = tmp_path / "old-profile"
    profile.mkdir()
    with sqlite3.connect(profile / "jarvix.db") as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO notes VALUES ('n','Existing','Private','2026-01-01','2026-01-01')")
        connection.execute("INSERT INTO grants VALUES ('memory.remember','allow','2026-01-01')")
    upgraded = Services(profile)
    try:
        assert upgraded.list_notes()[0]["body"] == "Private"
        assert upgraded.db.query("PRAGMA user_version")[0]["user_version"] == 3
        assert {row["name"] for row in upgraded.db.query("PRAGMA index_list(tasks)")} >= {"tasks_due_reminders"}
        assert {row["name"] for row in upgraded.db.query("PRAGMA index_list(conversations)")} >= {"conversations_recent"}
        assert not upgraded.execute_tool("memory.remember", {"content": "Cannot bypass"}).ok
    finally:
        upgraded.close()


def test_capability_loading_exposes_only_enabled_tools_next_round(services):
    visible = []
    class Provider:
        id = "test"
        def complete(self, messages, tools, model):
            visible.append([spec.name for spec in tools])
            if len(visible) == 1:
                return Completion(Message("assistant", tool_calls=[ToolCall("load", "capabilities.load", {"groups": ["notes"]})]))
            return Completion(Message("assistant", "Ready"))
    enabled = [spec.name for spec in services.registry.specs() if spec.name != "notes.delete"]
    services.settings.set("tools.enabled", enabled)
    with operation():
        result = services.orchestrator.run(Provider(), "test", [Message("user", "Load notes")], enabled,
            lambda _: pytest.fail("Tool catalog contains no private data"), lambda *_: None, threading.Event())
    assert result == "Ready"
    assert "notes.filter" not in visible[0]
    assert "notes.filter" in visible[1]
    assert "notes.delete" not in visible[1]
    assert all(len(names) < 128 for names in visible)


def test_conversation_branch_and_export_are_non_destructive(services, tmp_path):
    conversation = services.new_conversation("Original")
    for role, text in [("user", "First"), ("assistant", "Reply"), ("user", "Revise")]:
        services.repository.append_message(conversation, role, text)
    branch = services.conversations.branch(conversation, 2)
    assert branch["draft"] == "Revise"
    assert len(services.conversation_messages(conversation)) == 3
    assert len(services.conversation_messages(branch["id"])) == 2
    services.add_file_root(str(tmp_path))
    target = tmp_path / "conversation.md"
    services.conversations.export(conversation, str(target))
    with pytest.raises(FileExistsError):
        services.conversations.export(conversation, str(target))


def test_configured_app_arguments_need_fresh_exact_confirmation(services, monkeypatch):
    app_id = services.add_app("Test Python", sys.executable)
    services.apps.configure_arguments(app_id, ["--version"])
    services.settings.set("control.enabled", True)
    services.permissions.set_grant("apps.open", "allow")
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **_: launches.append(argv) or SimpleNamespace(pid=123))
    assert not services.execute_tool("apps.open", {"id": app_id}).ok
    with pytest.raises(PermissionError):
        services.launch_app(app_id)
    approvals = []
    assert services.execute_tool("apps.open", {"id": app_id},
        approve=lambda request: approvals.append(request) or True).ok
    assert approvals[0].arguments["arguments"] == ["--version"]
    assert len(launches) == 1
    assert not services.execute_tool("apps.open", {"id": app_id}).ok
    def changed_during_approval(_):
        services.apps.configure_arguments(app_id, ["-c", "print('changed')"])
        return True
    assert not services.execute_tool("apps.open", {"id": app_id}, approve=changed_during_approval).ok
    assert len(launches) == 1


def test_workspace_stops_after_nested_routine_permission_denial(services):
    services.settings.set("control.enabled", True)
    blocked = services.automation.save("Must confirm", [{"tool": "memory.remember", "arguments": {"content": "Private"}}])["id"]
    following = services.automation.save("Must not run", [{"tool": "notes.create", "arguments": {"title": "No", "body": ""}}])["id"]
    workspace = services.workspaces.save("Nested", automation_ids=[blocked, following])["id"]
    result = services.execute_tool("workspaces.launch", {"id": workspace}, approve=lambda _: False)
    assert not result.ok
    assert not result.data["completed"]
    assert not services.list_notes()
    assert not services.list_memories()
    assert services.automation.history(following) == []
