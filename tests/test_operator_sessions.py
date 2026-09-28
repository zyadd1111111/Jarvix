"""Plan/session invariants across real storage and the existing execution boundary."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from jarvix.domain import Completion, Message, ToolCall, ToolResult, ToolSpec
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    service.settings.set("control.enabled", True)
    yield service
    service.close()


def step(id, tool="tasks.list", arguments=None, **extra):
    return {"id": id, "tool": tool, "arguments": arguments or {}, **extra}


def plan(*steps, **extra):
    return {"goal": "Complete local work", "steps": list(steps), **extra}


def test_preview_then_fresh_sensitive_confirmation_and_private_history(services):
    requests = []
    result = services.operator.run(plan(
        step("note", "notes.create", {"title": "Plan", "body": "private-value-123"}),
        step("memory", "memory.remember", {"content": "private-value-123"})),
        approve=lambda request: requests.append(request) or request.tool_name != "memory.remember")
    assert result["status"] == "failed"
    assert [request.tool_name for request in requests] == ["operator.run", "memory.remember"]
    assert "notes.create" in requests[0].preview
    assert len(services.list_notes()) == 1 and not services.list_memories()
    assert "private-value-123" not in json.dumps(services.operator.list())
    assert "private-value-123" not in json.dumps(services.activity())
    assert result["steps"][0]["undo_id"]


def test_denied_plan_has_no_side_effects(services):
    outcome = services.operator.run(plan(step("note", "notes.create", {"title": "Never", "body": ""})))
    assert outcome["status"] == "denied"
    assert not services.list_notes()


def test_result_references_and_observed_verification(services):
    outcome = services.operator.run(plan(
        step("note", "notes.create", {"title": "Draft", "body": "text"}),
        step("task", "tasks.create", {"title": {"$ref": "note.data.id"}},
             expected={"tool": "tasks.list", "arguments": {}, "path": ["data", "items", 0, "status"], "equals": "open"})),
        approve=lambda _: True)
    assert outcome["ok"]
    assert outcome["steps"][1]["verified"]
    assert services.list_tasks()[0]["title"] == services.list_notes()[0]["id"]


@pytest.mark.parametrize("steps", [
    [step("one", "tasks.create", {"title": {"$ref": "later.data.id"}})],
    [step("one"), step("one")],
    [step("one", "notes.create", {"title": "x", "body": ""}, retries=1)],
    [step("one", "notes.create", {"title": "x", "body": ""}), step("two", "notes.create", {"title": "x", "body": ""})],
    [step("one", expected={"tool": "notes.create", "arguments": {"title": "x", "body": ""}})],
])
def test_invalid_or_repeating_plans_rejected_before_preview(services, steps):
    with pytest.raises(ValueError):
        services.operator.run(plan(*steps), approve=lambda _: pytest.fail("Invalid plan requested approval"))


def test_retry_is_bounded_and_never_replays_completed_mutations(services):
    attempts = []
    def read(_):
        attempts.append(1)
        return ToolResult(len(attempts) >= 3, {"ready": len(attempts) >= 3})
    services.registry.register(ToolSpec("test.read", "Read", {"type": "object"}, permission_level=1), read)
    failed = services.operator.run(plan(
        step("note", "notes.create", {"title": "Once", "body": ""}),
        step("read", "test.read", retries=1)), approve=lambda _: True)
    assert not failed["ok"] and len(attempts) == 2
    recovered = services.operator.retry(failed["id"], approve=lambda _: True)
    assert recovered["ok"] and len(attempts) == 3 and len(services.list_notes()) == 1
    assert recovered["parent_id"] == failed["id"]


def test_false_verification_stops_following_step(services):
    services.registry.register(ToolSpec("test.observe", "Observe", {"type": "object"}, permission_level=1),
                               lambda _: ToolResult(True, {"verified": False}))
    result = services.operator.run(plan(step("read", expected={"tool": "test.observe", "arguments": {}}),
        step("note", "notes.create", {"title": "Never", "body": ""})), approve=lambda _: True)
    assert result["status"] == "failed" and not services.list_notes()


def test_retry_never_duplicates_mutation_after_failed_verification(services):
    services.registry.register(ToolSpec("test.observe", "Observe", {"type": "object"}, permission_level=1),
                               lambda _: ToolResult(True, {"matched": False}))
    outcome = services.operator.run(plan(step("note", "notes.create", {"title": "Once", "body": ""},
        expected={"tool": "test.observe", "arguments": {}})), approve=lambda _: True)
    assert outcome["status"] == "failed" and not outcome["steps"][0]["retry_safe"]
    with pytest.raises(ValueError, match="new plan"):
        services.operator.retry(outcome["id"], approve=lambda _: pytest.fail("Unsafe replay was previewed"))
    assert len(services.list_notes()) == 1


def test_old_session_cannot_repeat_writes_completed_by_recovery(services):
    attempts = []
    def read(_):
        attempts.append(1)
        return ToolResult(len(attempts) > 1, {})
    services.registry.register(ToolSpec("test.read", "Read", {"type": "object"}, permission_level=1), read)
    failed = services.operator.run(plan(step("read", "test.read"),
        step("note", "notes.create", {"title": "Recovered once", "body": ""})), approve=lambda _: True)
    recovered = services.operator.retry(failed["id"], approve=lambda _: True)
    assert recovered["ok"]
    assert services.operator.get(failed["id"])["retry_session_id"] == recovered["id"]
    with pytest.raises(ValueError, match="recovery session"):
        services.operator.retry(failed["id"], approve=lambda _: True)
    assert len(services.list_notes()) == 1


def test_result_references_cannot_hide_repeated_mutations(services):
    services.registry.register(ToolSpec("test.name", "Observe", {"type": "object"}, permission_level=1),
                               lambda _: ToolResult(True, {"name": "Same title"}))
    outcome = services.operator.run(plan(
        step("name", "test.name"),
        step("first", "notes.create", {"title": "Same title", "body": ""}),
        step("again", "notes.create", {"title": {"$ref": "name.data.name"}, "body": ""})), approve=lambda _: True)
    assert outcome["status"] == "failed"
    assert len(services.list_notes()) == 1
    recovered = services.operator.retry(outcome["id"], approve=lambda _: True)
    assert recovered["status"] == "failed"
    assert len(services.list_notes()) == 1


def test_workspace_nested_execution_stops_on_observed_failure(services, tmp_path, monkeypatch):
    services.add_file_root(str(tmp_path))
    saved = services.workspaces.save("Observed", folders=[str(tmp_path)], urls=["https://example.org"])
    calls = []
    def execute(name, arguments):
        calls.append(name)
        return ToolResult(True, {"completed": False})
    monkeypatch.setattr(services, "execute_tool", execute)
    assert not services.workspaces.launch(saved["id"])["completed"]
    assert calls == ["files.open_folder"]


def test_cancellation_and_deadline_stop_nested_work(services):
    cancelled = threading.Event()
    def approve(_):
        cancelled.set()
        return True
    outcome = services.operator.run(plan(step("note", "notes.create", {"title": "No", "body": ""})),
                                    approve=approve, cancel=cancelled)
    assert outcome["status"] == "cancelled" and not services.list_notes()
    services.registry.register(ToolSpec("test.slow", "Slow read", {"type": "object"}, permission_level=1),
                               lambda _: time.sleep(1.05) or ToolResult(True))
    timed = services.operator.run(plan(step("slow", "test.slow"), step("next"), timeout_seconds=1), approve=lambda _: True)
    assert timed["status"] == "timed_out" and timed["steps"][1]["status"] == "pending"


def test_pause_and_resume_are_cooperative(services):
    paused = threading.Event()
    session_ids = []
    results = []
    def events(kind, value):
        if kind == "operator_session" and value["status"] == "running" and not session_ids:
            session_ids.append(value["id"])
            services.operator.pause(value["id"])
        if kind == "operator_session" and value["status"] == "paused":
            paused.set()
    thread = threading.Thread(target=lambda: results.append(services.operator.run(plan(step("read")),
                                approve=lambda _: True, on_event=events)))
    thread.start()
    try:
        assert paused.wait(3)
        assert services.operator.get(session_ids[0])["status"] == "paused"
        services.operator.resume(session_ids[0])
        thread.join(3)
        assert results[0]["ok"]
    finally:
        if session_ids:
            services.operator.cancel(session_ids[0])
        thread.join(3)


def test_session_persistence_does_not_replay_on_restart(services):
    result = services.operator.run(plan(step("read")), approve=lambda _: True)
    services.records.put("operator_session", {"goal": "Interrupted", "status": "running",
        "steps": [{"id": "one", "status": "running", "tool": "tasks.list"}]}, "interrupted")
    from jarvix.capabilities.operator import OperatorService
    restored = OperatorService(services)
    assert restored.get(result["id"])["status"] == "complete"
    assert restored.get("interrupted")["status"] == "interrupted"
    with pytest.raises(ValueError, match="Re-plan"):
        restored.retry("interrupted")


def test_undo_only_unchanged_owned_items(services):
    created = services.execute_tool("notes.create", {"title": "Owned", "body": "original"})
    assert services.execute_tool("actions.undo", {"id": created.data["undo_id"]}).ok
    assert not services.list_notes()
    changed = services.execute_tool("tasks.create", {"title": "Owned task"})
    services.complete_task(changed.data["id"])
    blocked = services.execute_tool("actions.undo", {"id": changed.data["undo_id"]})
    assert not blocked.ok and len(services.list_tasks()) == 1


def test_workspace_changes_have_guarded_undo_and_terminal_roots(services, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    services.add_file_root(str(folder))
    project_id = services.add_project("Jarvix", str(folder))
    saved = services.execute_tool("workspaces.save", {"name": "Coding", "project_id": project_id,
                                                     "terminal_directory": str(folder)})
    assert saved.ok and saved.data["undo_id"]
    preview = services.workspaces.preview(saved.data["id"])
    assert [item["tool"] for item in preview["actions"]] == ["files.open_folder", "workspaces.open_terminal"]
    assert services.execute_tool("actions.undo", {"id": saved.data["undo_id"]}).ok
    assert not services.workspaces.list()["items"]
    with pytest.raises(ValueError):
        services.workspaces.save("Invalid", terminal_directory=str(tmp_path))


def test_context_opt_in_and_selection_are_explicit(services, tmp_path, monkeypatch):
    with pytest.raises(PermissionError):
        services.context.inspect()
    services.settings.set("context.enabled", True)
    monkeypatch.setattr(services.windows, "_native", SimpleNamespace(foreground=lambda:
        {"handle": 3, "process_id": 4, "title": "Editor"}))
    assert services.context.inspect()["active_window"]["title"] == "Editor"
    assert services.context.inspect()["clipboard_type"] is None
    (tmp_path / "outside.txt").write_text("outside roots", encoding="utf-8")
    with pytest.raises(ValueError):
        services.context.set_current(selected_files=[str(tmp_path / "outside.txt")])


@pytest.mark.parametrize("allow_save", [False, True])
def test_chat_schedule_is_structured_and_requires_confirmation(services, monkeypatch, allow_save):
    workflow = {"name": "Weekday homework", "trigger": "schedule", "config": {"time": "16:00", "weekdays": [0, 1, 2, 3, 4]},
                "enabled": True, "steps": [{"kind": "action", "tool": "tasks.list", "arguments": {}}]}
    replies = iter([
        Message("assistant", tool_calls=[ToolCall("load", "capabilities.load", {"groups": ["workflows"]})]),
        Message("assistant", tool_calls=[ToolCall("save", "workflows.save", workflow)]),
        Message("assistant", "Finished reviewing the schedule."),
    ])
    provider = SimpleNamespace(id="openai", complete=lambda *args: Completion(next(replies)))
    monkeypatch.setattr("jarvix.providers.create_provider", lambda *_: provider)
    monkeypatch.setattr(services.vault, "get", lambda _: "test-only-key")
    confirmations = []
    def approve(request):
        if request.tool_name == "workflows.save" and request.kind == "execute":
            confirmations.append(request)
            return allow_save
        return request.kind == "disclose"
    conversation = services.new_conversation()
    services.chat("Every weekday at four show unfinished tasks", conversation, "openai", "test-model",
                  approve, lambda *_: None, threading.Event())
    assert len(confirmations) == 1
    assert confirmations[0].arguments["config"]["time"] == "16:00"
    assert len(services.workflows.list()) == int(allow_save)

