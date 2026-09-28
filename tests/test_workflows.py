"""Workflow boundaries, persisted runs, schedules, and controlled recovery."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from jarvix.capabilities.workflows import WORKFLOW_HOTKEYS, background_allowed
from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import CURRENT, operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile")
    yield service
    service.close()


def action(tool="tasks.list", arguments=None, **extras):
    return {"kind": "action", "tool": tool, "arguments": arguments or {}, **extras}


def test_save_requires_fresh_confirmation_even_with_control_enabled(services):
    services.settings.set("control.enabled", True)
    arguments = {"name": "Homework", "steps": [action()], "trigger": "schedule",
                 "config": {"time": "16:00", "weekdays": [0, 1, 2, 3, 4]}, "enabled": True}
    denied = services.execute_tool("workflows.save", arguments, approve=lambda _: False)
    assert not denied.ok
    assert services.workflows.list() == []
    previews = []
    saved = services.execute_tool("workflows.save", arguments, approve=lambda request: previews.append(request) or True)
    assert saved.ok
    assert previews[0].arguments["steps"][0]["tool"] == "tasks.list"
    assert services.workflows.get(saved.data["id"])["next_run"]
    assert not services.execute_tool("workflows.toggle", {"id": saved.data["id"], "enabled": True}, approve=lambda _: False).ok


def test_manual_workflow_checks_sensitive_action_immediately(services):
    reached = Mock(return_value=ToolResult(True))
    services.registry.register(ToolSpec("test.sensitive", "Sensitive", {"type": "object"}, permission_level=3), reached)
    saved = services.workflows.save("Sensitive", [action("test.sensitive")])["id"]
    services.settings.set("control.enabled", True)
    requested = []

    def approve(request):
        requested.append(request.tool_name)
        return request.tool_name == "workflows.run"

    result = services.execute_tool("workflows.run", {"id": saved}, approve=approve)
    assert not result.ok
    reached.assert_not_called()
    assert services.workflows.history(saved)[0]["status"] == "failed"
    assert requested == ["workflows.run", "test.sensitive"]


@pytest.mark.parametrize("step", [
    action("tasks.list", retries=2), action("notes.create", {"title": "A", "body": "B"}, retries=1),
    {"kind": "delay", "seconds": float("inf")}, {"kind": "delay", "seconds": True},
    {"kind": "code", "expression": "__import__('os')"},
])
def test_bad_blocks_never_persist(services, step):
    with pytest.raises((ValueError, KeyError)):
        services.workflows.save("Invalid", [step])
    assert services.workflows.list() == []


def test_branch_and_continue_failure_produce_truthful_partial_status(services):
    services.registry.register(ToolSpec("test.failure", "Failure", {"type": "object"}, permission_level=1),
                               lambda _: ToolResult(False, error="private-token-do-not-log"))
    today = datetime.now().weekday()
    steps = [{"kind": "branch", "condition": {"kind": "weekday", "days": [today]},
              "then": [action("test.failure", on_error="continue"), action()], "else": []}]
    saved = services.workflows.save("Branch", steps)["id"]
    result = services.workflows.run(saved)
    assert not result["ok"] and result["status"] == "partial"
    assert result["steps"][-1]["ok"]
    assert "private-token" not in json.dumps(services.workflows.history())


def test_safe_retry_retries_once_and_denial_is_never_retried(services, monkeypatch):
    saved = services.workflows.save("Retry", [action(retries=1)])["id"]
    execute = Mock(side_effect=[ToolResult(False, error="Temporary failure"), ToolResult(True)])
    monkeypatch.setattr(services, "execute_tool", execute)
    result = services.workflows.run(saved)
    assert result["ok"] and result["steps"][0]["attempts"] == 2
    execute.reset_mock(side_effect=True)
    execute.return_value = ToolResult(False, error="Permission denied")
    assert not services.workflows.run(saved)["ok"]
    assert execute.call_count == 1


@pytest.mark.parametrize("data", [{"completed": False}, {"matched": False}, {"verified": False}, {"ok": False}])
def test_failed_observed_state_stops_later_actions(services, monkeypatch, data):
    saved = services.workflows.save("Verify actual outcome", [action(), action()])["id"]
    execute = Mock(return_value=ToolResult(True, data))
    monkeypatch.setattr(services, "execute_tool", execute)
    result = services.workflows.run(saved)
    assert not result["ok"] and result["status"] == "failed"
    assert result["steps"][0]["status"] == "failed"
    assert execute.call_count == 1


def test_unverified_launch_is_recorded_as_requested(services, monkeypatch):
    saved = services.workflows.save("Request launch", [action()])["id"]
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: ToolResult(
        True, {"requested": True, "verified": False, "private": "do-not-store"}))
    result = services.workflows.run(saved)
    assert result["ok"] and result["steps"][0]["status"] == "requested"
    assert not result["steps"][0]["verified"]
    assert "do-not-store" not in json.dumps(services.workflows.history(saved))


def test_unattended_exact_grant_never_broadens_default_allowlist(services, tmp_path):
    services.add_file_root(str(tmp_path))
    services.settings.set("control.enabled", True)
    destination = str(tmp_path / "created")
    step = action("files.create_folder", {"path": destination})
    with pytest.raises(PermissionError):
        services.workflows.save("Unapproved", [step], "jarvix_start", enabled=True)
    saved = services.workflows.save("Approved", [step], "jarvix_start", enabled=True,
                                    approved_tools=["files.create_folder"])["id"]
    assert services.workflows.tick()[0]["ok"]
    assert (tmp_path / "created").is_dir()
    assert not background_allowed("files.create_folder", {"path": destination})
    with operation(unattended=True):
        denied = services.execute_tool("files.create_folder", {"path": str(tmp_path / "other")}, approve=lambda _: True)
    assert not denied.ok and not (tmp_path / "other").exists()
    assert services.workflows.history(saved)[0]["unattended"]


def test_opt_in_grant_cannot_bypass_disabled_control_or_tampered_definition(services, tmp_path):
    services.add_file_root(str(tmp_path))
    path = str(tmp_path / "denied")
    saved = services.workflows.save("Review", [action("files.create_folder", {"path": path})],
            "manual", enabled=True, approved_tools=["files.create_folder"])["id"]
    assert not services.workflows.run(saved, unattended=True)["ok"]
    assert not (tmp_path / "denied").exists()
    row = services.records.get("workflow", saved)
    row["steps"][0]["arguments"]["path"] = str(tmp_path / "changed")
    services.records.put("workflow", row, saved)
    services.settings.set("control.enabled", True)
    with pytest.raises(PermissionError, match="changed"):
        services.workflows.run(saved, unattended=True)


def test_sensitive_and_ui_tools_never_allowed_in_background(services):
    services.registry.register(ToolSpec("test.destructive", "Destructive", {"type": "object"}, permission_level=3),
                               lambda _: ToolResult(True))
    with pytest.raises(PermissionError):
        services.workflows.save("Bad", [action("test.destructive")], "jarvix_start", enabled=True)
    with pytest.raises(ValueError):
        services.workflows.save("Bad", [action()], approved_tools=["desktop.click_element"])


def test_cancel_and_pause_are_persisted_and_prevent_later_actions(services):
    reached = Mock(return_value=ToolResult(True))
    services.registry.register(ToolSpec("test.after", "After", {"type": "object"}, permission_level=1), reached)
    saved = services.workflows.save("Pause", [{"kind": "delay", "seconds": 2}, action("test.after")])["id"]
    events = []
    outcome = []
    started = threading.Event()

    def event(kind, data):
        events.append(data)
        if data["status"] == "running" and len(data["steps"]) == 1:
            services.workflows.pause(data["run_id"])
            started.set()

    thread = threading.Thread(target=lambda: outcome.append(services.workflows.run(saved, on_event=event)))
    thread.start()
    assert started.wait(3)
    services.workflows.cancel(events[0]["run_id"])
    thread.join(3)
    assert not thread.is_alive()
    assert outcome[0]["status"] == "cancelled"
    assert outcome[0]["steps"][0]["status"] == "cancelled"
    reached.assert_not_called()
    assert services.workflows.history(saved)[0]["status"] == "cancelled"


def test_timeout_and_run_persistence(services):
    saved = services.workflows.save("Timeout", [{"kind": "delay", "seconds": 1}])["id"]
    with operation():
        CURRENT.get().deadline = time.monotonic() + .02
        result = services.workflows.run(saved)
    assert result["status"] == "timed_out"
    assert services.records.get("workflow_run", result["run_id"])["ended_at"]


def test_schedule_fires_once_per_local_day_and_reports_next_run(services):
    now = datetime.now().astimezone()
    saved = services.workflows.save("Weekdays", [action()], "schedule", {"time": "00:00", "weekdays": [now.weekday()]}, enabled=True)["id"]
    assert len(services.workflows.tick()) == 1
    assert services.workflows.tick() == []
    next_time = datetime.fromisoformat(services.workflows.get(saved)["next_run"])
    assert next_time.date() > now.date()


@pytest.mark.parametrize("fields", [
    {"trigger": "schedule", "config": {"time": "1600Z"}},
    {"conditions": [{"kind": "time_range", "start": "1600Z", "end": "17:00"}]},
])
def test_local_clock_values_cannot_silently_become_utc(services, fields):
    with pytest.raises(ValueError, match="HH:MM"):
        services.workflows.save("Local time only", [action()], **fields)
    assert services.workflows.list() == []


def test_task_due_edges_and_disabled_automation_gate(services, monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    tasks = [{"id": "one", "title": "Due", "status": "open", "due_at": past}]
    monkeypatch.setattr(services, "list_tasks", lambda: tasks)
    services.workflows.save("Due", [action()], "task_due", enabled=True)
    assert len(services.workflows.tick()) == 1
    assert services.workflows.tick() == []
    services.settings.set("automations.enabled", False)
    tasks.append({"id": "two", "title": "Due", "status": "open", "due_at": past})
    assert services.workflows.tick() == []


def test_clipboard_trigger_opt_in_is_separate_and_never_persists_content(services, monkeypatch):
    read = Mock(return_value={"text": "password-private"})
    monkeypatch.setattr(services.clipboard, "read", read)
    with pytest.raises(ValueError, match="opt-in"):
        services.workflows.save("Clipboard", [action()], "clipboard_changed")
    services.workflows.save("Clipboard", [action()], "clipboard_changed", {"opt_in": True}, enabled=True)
    services.workflows.tick()
    read.assert_not_called()
    services.settings.set("clipboard.enabled", True)
    assert services.workflows.tick() == []
    read.return_value = {"text": "changed-private"}
    assert len(services.workflows.tick()) == 1
    assert "private" not in json.dumps(services.workflows.list())
    assert "password" not in json.dumps(services.workflows.history())


def test_export_import_strips_grants_and_external_triggers(services, tmp_path):
    services.add_file_root(str(tmp_path))
    saved = services.workflows.save("Folders", [action("files.create_folder", {"path": str(tmp_path / "new")})],
        "jarvix_start", enabled=True, approved_tools=["files.create_folder"])["id"]
    imported = services.workflows.import_workflow(services.workflows.export(saved))["id"]
    row = services.workflows.get(imported)
    assert not row["enabled"] and not row["approved_tools"] and row["trigger"] == "manual"
    assert row["steps"] == services.workflows.get(saved)["steps"]


def test_nested_workflow_cycle_is_bounded(services):
    saved = services.workflows.save("Self", [action()])["id"]
    services.workflows.save("Self", [action("workflows.run", {"id": saved})], id=saved)
    services.settings.set("control.enabled", True)
    result = services.workflows.run(saved)
    assert result["status"] == "failed" and len(result["steps"]) == 1


def test_workflow_test_never_executes_actions_and_bad_condition_is_rejected(services, monkeypatch):
    saved = services.workflows.save("Test", [action()])["id"]
    execute = Mock()
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.workflows.test(saved)["actions_executed"] == 0
    execute.assert_not_called()
    with pytest.raises(ValueError):
        services.workflows.save("Unsafe condition", [action()], conditions=[{"kind": "safe", "key": "eval", "value": 1, "op": "eq"}])


def test_restarted_service_marks_old_running_sessions_interrupted(services):
    from jarvix.capabilities.workflows import WorkflowService
    run_id = services.records.put("workflow_run", {"workflow_id": "old", "status": "running", "steps": []})
    WorkflowService(services)
    assert services.records.get("workflow_run", run_id)["status"] == "interrupted"


def test_nested_run_preserves_parent_pause_and_cancellation(services):
    reached = Mock(return_value=ToolResult(True))
    services.registry.register(ToolSpec("test.after", "After", {"type": "object"}, permission_level=1), reached)
    child = services.workflows.save("Child", [{"kind": "delay", "seconds": .1}, action("test.after")])["id"]
    parent = services.workflows.save("Parent", [action("workflows.run", {"id": child})])["id"]
    services.settings.set("control.enabled", True)
    paused = threading.Event()
    parent_run = []
    outcome = []

    def event(kind, data):
        if kind != "workflow":
            return
        if data["workflow_id"] == parent and not parent_run:
            parent_run.append(data["run_id"])
        if data["workflow_id"] == child and data["steps"]:
            services.workflows.pause(parent_run[0])
        if data["workflow_id"] == parent and data["status"] == "paused":
            paused.set()

    def run():
        with operation(approve=lambda _: True):
            outcome.append(services.workflows.run(parent, on_event=event))

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert paused.wait(3)
        reached.assert_not_called()
        services.workflows.cancel(parent_run[0])
        worker.join(3)
        assert not worker.is_alive()
        assert outcome[0]["status"] == "cancelled"
        assert services.workflows.history(child)[0]["status"] == "cancelled"
        reached.assert_not_called()
    finally:
        services.workflows.close()
        worker.join(3)


def test_supported_hotkey_is_canonical_and_routes_only_enabled_workflows(services):
    assert WORKFLOW_HOTKEYS["Ctrl+Alt+F12"] == (0x0003, 0x7B)
    saved = services.workflows.save("Shortcut", [action()], "hotkey",
                                   {"shortcut": "ctrl+alt+f12"}, enabled=True)["id"]
    services.workflows.save("Disabled shortcut", [action()], "hotkey", {"shortcut": "Ctrl+Alt+F12"})
    assert services.workflows.get(saved)["config"]["shortcut"] == "Ctrl+Alt+F12"
    assert services.workflows.trigger_hotkey("Ctrl+Alt+F11") == []
    results = services.workflows.trigger_hotkey("Ctrl+Alt+F12")
    assert len(results) == 1 and results[0]["ok"]
    with pytest.raises(ValueError, match="Ctrl"):
        services.workflows.save("Unsupported", [action()], "hotkey", {"shortcut": "Ctrl+Alt+Delete"})


def test_unchanged_trigger_does_not_rewrite_definition(services):
    saved = services.workflows.save("Once", [action()], "jarvix_start", enabled=True)["id"]
    services.workflows.tick()
    stamp = services.workflows.get(saved)["updated_at"]
    services.workflows.tick()
    assert services.workflows.get(saved)["updated_at"] == stamp


def test_condition_file_paths_revalidate_allowed_roots(services, tmp_path):
    allowed = tmp_path / "watched"
    allowed.mkdir()
    services.add_file_root(str(allowed))
    target = allowed / "ready.txt"
    condition = {"kind": "file_exists", "path": str(target)}
    saved = services.workflows.save("Allowed file", [action()], conditions=[condition])["id"]
    assert not services.workflows.test(saved)["would_run"]
    target.touch()
    assert services.workflows.test(saved)["would_run"]
    with pytest.raises((ValueError, PermissionError)):
        services.workflows.save("Outside", [action()], conditions=[{"kind": "file_exists", "path": str(tmp_path / "other")}])


@pytest.mark.parametrize("fields", [{"conditions": {}}, {"conditions": False}, {"config": []}])
def test_falsey_invalid_definition_fields_are_rejected(services, fields):
    with pytest.raises(ValueError):
        services.workflows.save("Malformed", [action()], **fields)


def test_workflow_cannot_nest_operator_session_even_in_branch(services):
    steps = [{"kind": "branch", "condition": {"kind": "weekday", "days": [0]},
              "then": [action("operator.sessions")], "else": []}]
    with pytest.raises(ValueError, match="Operator sessions cannot be nested"):
        services.workflows.save("Recursive operator", steps)
    assert services.workflows.list() == []


def test_background_app_arguments_are_rechecked_before_run(services, monkeypatch):
    monkeypatch.setattr(services.apps, "arguments_for", lambda _: [])
    saved = services.workflows.save("Launch", [action("apps.open", {"id": "configured-app"})],
        "jarvix_start", enabled=True, approved_tools=["apps.open"])["id"]
    monkeypatch.setattr(services.apps, "arguments_for", lambda _: ["--run", "private-command"])
    execute = Mock()
    monkeypatch.setattr(services, "execute_tool", execute)
    with pytest.raises(PermissionError, match="command arguments"):
        services.workflows.run(saved, unattended=True)
    execute.assert_not_called()


def test_file_watch_protects_sensitive_entries_and_reports_removed_root(services, tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    services.add_file_root(str(folder))
    saved = services.workflows.save("Watch", [action()], "file_created", {"path": str(folder)}, enabled=True)["id"]
    assert services.workflows.tick() == []
    (folder / ".env").write_text("private-value")
    assert services.workflows.tick() == []
    (folder / "ordinary.txt").write_text("file body")
    assert services.workflows.tick()[0]["ok"]
    persisted = json.dumps(services.workflows.list()) + json.dumps(services.workflows.history())
    assert "private-value" not in persisted and "ordinary.txt" not in persisted
    services.remove_file_root(str(folder))
    assert services.workflows.tick() == []
    history = services.workflows.history(saved)
    assert history[0]["status"] == "failed"
    assert "private-value" not in json.dumps(history)

