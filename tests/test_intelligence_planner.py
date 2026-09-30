"""Recovery plans preserve successful work and stop safely across parallel branches."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from jarvix.capabilities.checkpoints import CheckpointStore
from jarvix.capabilities.operator import OperatorService
from jarvix.domain import Completion, Message, ToolCall, ToolResult, ToolSpec

from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    value = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    value.settings.set("control.enabled", True)
    value.operator.checkpoints = CheckpointStore(value.records, lambda data, **_: data)
    yield value
    value.close()


def step(id, tool="tasks.list", arguments=None, **extra):
    return {"id": id, "tool": tool, "arguments": arguments or {}, **extra}


def plan(*steps, **extra):
    return {"goal": "Complete connected work", "steps": list(steps), **extra}


def failure_after_note(services):
    services.registry.register(ToolSpec("test.unavailable", "Unavailable source", {"type": "object"},
                                        permission_level=1), lambda _: ToolResult(False))
    return services.operator.run(plan(
        step("note", "notes.create", {"title": "Preserve this", "body": ""}),
        step("source", "test.unavailable")), approve=lambda _: True)


def test_replan_after_restart_reuses_successful_outputs_and_previews_changes(services):
    failed = failure_after_note(services)
    restored = OperatorService(services)
    restored.checkpoints = CheckpointStore(services.records, lambda data, **_: data)
    recovery = plan(step("task", "tasks.create", {"title": {"$ref": "note.data.id"}}, depends_on=["note"]))
    preview = restored.preview_replan(failed["id"], recovery)
    assert preview["recovery"]["retained_step_ids"] == ["note"]
    assert preview["recovery"]["replaced_step_ids"] == ["source"]
    assert not services.list_tasks()
    requests = []
    result = restored.replan(failed["id"], recovery, approve=lambda request: requests.append(request) or True)
    assert result["ok"] and result["parent_id"] == failed["id"]
    assert len(services.list_notes()) == 1
    assert services.list_tasks()[0]["title"] == services.list_notes()[0]["id"]
    assert json.loads(requests[0].preview)["recovery"]["kind"] == "changed_plan"
    with pytest.raises(ValueError, match="recovery session"):
        restored.replan(failed["id"], recovery, approve=lambda _: True)


def test_replan_denied_preview_and_sensitive_step_never_bypass_permissions(services):
    failed = failure_after_note(services)
    requests = []
    result = services.operator.replan(failed["id"], plan(step("save", "memory.remember", {"content": "private"})),
        approve=lambda request: requests.append(request.tool_name) or request.tool_name == "operator.run")
    assert result["status"] == "failed" and not services.list_memories()
    assert requests == ["operator.run", "memory.remember"]
    denied = services.operator.replan(result["id"], plan(step("new", "tasks.create", {"title": "Not approved"})))
    assert denied["status"] == "denied" and not services.list_tasks()


def test_replan_rejects_completed_mutation_replay_and_seed_shadowing(services):
    failed = failure_after_note(services)
    with pytest.raises(ValueError, match="replay"):
        services.operator.preview_replan(failed["id"], plan(
            step("again", "notes.create", {"title": "Preserve this", "body": ""})))
    with pytest.raises(ValueError, match="retained results"):
        services.operator.preview_replan(failed["id"], plan(step("note")))
    assert len(services.list_notes()) == 1


def test_uncertain_mutation_requires_fresh_observation_before_changed_actions(services, monkeypatch):
    original = services.execute_tool
    def uncertain(name, args, **kwargs):
        result = original(name, args, **kwargs)
        return ToolResult(False) if name == "notes.create" else result
    monkeypatch.setattr(services, "execute_tool", uncertain)
    failed = services.operator.run(plan(step("note", "notes.create", {"title": "Uncertain", "body": ""})),
                                    approve=lambda _: True)
    with pytest.raises(ValueError, match="fresh read"):
        services.operator.preview_replan(failed["id"], plan(step("task", "tasks.create", {"title": "Check"})))
    monkeypatch.setattr(services, "execute_tool", original)
    recovery = plan(step("inspect", "notes.search", {"query": "Uncertain"}),
                    step("task", "tasks.create", {"title": "Review retained note"}))
    assert services.operator.preview_replan(failed["id"], recovery)["recovery"]["uncertain_step_ids"] == ["note"]
    assert services.operator.replan(failed["id"], recovery, approve=lambda _: True)["ok"]
    assert len(services.list_notes()) == 1


def test_denied_recovery_preserves_uncertainty_for_following_replans(services, monkeypatch):
    original = services.execute_tool
    def uncertain(name, args, **kwargs):
        original(name, args, **kwargs)
        return ToolResult(False)
    monkeypatch.setattr(services, "execute_tool", uncertain)
    failed = services.operator.run(plan(step("note", "notes.create", {"title": "Uncertain", "body": ""})),
                                   approve=lambda _: True)
    monkeypatch.setattr(services, "execute_tool", original)
    denied = services.operator.replan(failed["id"], plan(step("inspect", "notes.search", {"query": "Uncertain"})))
    assert denied["status"] == "denied"
    with pytest.raises(ValueError, match="fresh read"):
        services.operator.preview_replan(denied["id"], plan(step("task", "tasks.create", {"title": "Review"})))
    preview = services.operator.preview_replan(denied["id"], plan(
        step("inspect", "notes.search", {"query": "Uncertain"}),
        step("task", "tasks.create", {"title": "Review"})))
    assert preview["recovery"]["uncertain_step_ids"] == ["note"]
    assert len(services.list_notes()) == 1 and not services.list_tasks()


def test_failure_policy_skips_dependencies_and_mutations_but_continues_independent_reads(services, monkeypatch):
    original, calls = services.execute_tool, []
    def execute(name, args, **kwargs):
        calls.append(name)
        if name == "tasks.list":
            return ToolResult(False, error="Unavailable")
        return original(name, args, **kwargs)
    monkeypatch.setattr(services, "execute_tool", execute)
    result = services.operator.run(plan(
        step("failed", depends_on=[]),
        step("blocked", "notes.search", {"query": "work"}, depends_on=["failed"]),
        step("independent", "projects.list", depends_on=[]),
        step("write", "notes.create", {"title": "Must wait", "body": ""}, depends_on=[]),
        on_failure="continue_independent_reads", max_parallel_reads=1), approve=lambda _: True)
    assert result["status"] == "failed" and result["partial_completion"]
    assert result["failed_step_ids"] == ["failed"]
    assert result["skipped_step_ids"] == ["blocked", "write"]
    assert calls == ["tasks.list", "projects.list"] and not services.list_notes()
    assert result["steps"][1]["blocked_by"] == ["failed"]


def test_default_failure_policy_stops_independent_reads(services, monkeypatch):
    calls = []
    monkeypatch.setattr(services, "execute_tool", lambda name, args, **_: calls.append(name) or ToolResult(False))
    result = services.operator.run(plan(step("failed", depends_on=[]), step("next", "projects.list", depends_on=[]),
                                        max_parallel_reads=1), approve=lambda _: True)
    assert calls == ["tasks.list"] and result["steps"][1]["status"] == "pending"


def test_incomplete_batch_and_failed_retry_are_reported_as_failures(services):
    services.registry.register(ToolSpec("test.batch", "Partly completed batch", {"type": "object"},
                                        permission_level=1), lambda _: ToolResult(True, {"complete": False}))
    failed = services.operator.run(plan(step("batch", "test.batch")), approve=lambda _: True)
    assert failed["status"] == "failed" and not failed["ok"]
    retried = services.execute_tool("operator.retry", {"id": failed["id"]}, approve=lambda _: True)
    assert not retried.ok and retried.data["status"] == "failed"


@pytest.mark.parametrize("invalid", [None, ToolResult(True, {"value": float("nan")}),
                                    ToolResult("yes", {}), ToolResult(True, {"value": "wrong type"})])
def test_invalid_results_are_not_seeded_or_retried(services, monkeypatch, invalid):
    services.registry.register(ToolSpec("test.typed", "Typed read", {"type": "object"}, permission_level=1,
        result_schema={"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}),
        lambda _: ToolResult(True, {"value": 1}))
    calls = []
    monkeypatch.setattr(services, "execute_tool", lambda name, args, **_: calls.append(name) or invalid)
    result = services.operator.run(plan(step("bad", "test.typed", retries=1)), approve=lambda _: True)
    assert result["steps"][0]["failure_code"] == "invalid_result" and len(calls) == 1
    assert result["results"]["bad"]["data"] is None


def test_semantic_failure_read_retries_once_and_validates_resolved_arguments(services, monkeypatch):
    calls = []
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs:
                        calls.append(1) or ToolResult(True, {"completed": len(calls) > 1, "title": 10}))
    result = services.operator.run(plan(step("read", retries=1),
        step("write", "tasks.create", {"title": {"$ref": "read.data.title"}})), approve=lambda _: True)
    assert len(calls) == 2 and result["steps"][0]["status"] == "complete"
    assert result["steps"][1]["failure_code"] == "invalid_arguments"
    assert result["steps"][1]["retry_safe"]


def test_cancel_parallel_reads_returns_before_uncooperative_read_and_freezes_checkpoint(services, monkeypatch):
    started, release, cancel = threading.Barrier(3), threading.Event(), threading.Event()
    returned = [threading.Event(), threading.Event()]
    calls = {"tasks.list": 0, "projects.list": 1}
    def execute(name, args, **kwargs):
        started.wait(3)
        release.wait(3)
        returned[calls[name]].set()
        return ToolResult(True, {"late": "must never overwrite terminal results"})
    monkeypatch.setattr(services, "execute_tool", execute)
    result = []
    thread = threading.Thread(target=lambda: result.append(services.operator.run(plan(
        step("tasks", depends_on=[]), step("projects", "projects.list", depends_on=[])),
        approve=lambda _: True, cancel=cancel)))
    thread.start()
    try:
        started.wait(3)
        cancel.set()
        thread.join(1)
        assert not thread.is_alive() and result[0]["status"] == "cancelled"
        session = copy.deepcopy(services.operator.get(result[0]["id"]))
        checkpoint = services.operator.checkpoints.load(result[0]["id"])
        release.set()
        assert all(event.wait(2) for event in returned)
        assert services.operator.get(result[0]["id"]) == session
        assert services.operator.checkpoints.load(result[0]["id"]) == checkpoint
        assert result[0]["results"] == {}
    finally:
        release.set()
        thread.join(3)


def test_stop_after_tool_return_never_prompts_for_disclosure_or_calls_provider_again(services, monkeypatch):
    cancel, requests = threading.Event(), []
    def execute(*args, **kwargs):
        cancel.set()
        return ToolResult(True, {"private": "keep local"})
    services.orchestrator.executor = execute
    provider = SimpleNamespace(id="test", complete=lambda *args: Completion(Message("assistant",
        tool_calls=[ToolCall("search", "notes.search", {"query": "project"})])))
    result = services.orchestrator.run(provider, "test", [Message("user", "Search notes")], services.enabled_tools(),
        lambda request: requests.append(request) or True, lambda *_: None, cancel)
    assert "stopped" in result and requests == []
