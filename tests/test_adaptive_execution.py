"""Adaptive supervision and dynamic workflows retain the host permission boundary."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from jarvix.capabilities.schema import schema, string
from jarvix.capabilities.supervisor import ExecutionSupervisor
from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import CURRENT, check_cancelled, operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    service.settings.set("control.enabled", True)
    yield service
    service.close()


def action(tool="tasks.list", arguments=None, **kwargs):
    return {"kind": "action", "tool": tool, "arguments": arguments or {}, **kwargs}


def test_step_deadline_progress_and_public_dependency_graph(services):
    def slow(_):
        while True:
            check_cancelled()
            time.sleep(.01)
    services.registry.register(ToolSpec("test.slow", "Slow read", schema(), permission_level=1), slow)
    result = services.operator.run({"goal": "Bound slow read", "timeout_seconds": 4, "steps": [
        {"id": "slow", "tool": "test.slow", "arguments": {}, "timeout_seconds": 1},
        {"id": "later", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    assert result["status"] == "failed" and result["steps"][0]["failure_code"] == "step_deadline"
    inspected = ExecutionSupervisor(services).inspect(result["id"])
    assert inspected["progress_percent"] == 0 and inspected["evaluated_percent"] == 50
    assert inspected["steps"][0]["failure_class"] == "timeout"
    assert inspected["graph"]["edges"] == [{"from": "slow", "to": "later"}]
    assert "arguments" not in json.dumps(inspected["graph"])


def test_long_supervised_deadline_does_not_relax_step_or_parent_limits(services):
    with operation(timeout=1800):
        assert 1700 < CURRENT.get().deadline - time.monotonic() <= 1800.001
        with operation(timeout=3600):
            assert CURRENT.get().deadline - time.monotonic() <= 1800.001
    services.registry.register(ToolSpec("test.deadline", "Observe remaining time", schema(), permission_level=1),
        lambda _: ToolResult(True, {"remaining": CURRENT.get().deadline - time.monotonic()}))
    result = services.operator.run({"goal": "Long bounded plan", "timeout_seconds": 3600, "steps": [
        {"id": "read", "tool": "test.deadline", "arguments": {}, "timeout_seconds": 600}]}, approve=lambda _: True)
    assert result["ok"] and 590 < result["results"]["read"]["data"]["remaining"] <= 600.001
    with pytest.raises(ValueError, match="bounded"):
        services.operator.preview({"goal": "Over limit", "timeout_seconds": 3601, "steps": [
            {"id": "read", "tool": "tasks.list", "arguments": {}}]})


def test_supervised_background_is_bounded_and_cancelled_cleanly(services, monkeypatch):
    supervisor = ExecutionSupervisor(services)
    terminal = threading.Event()
    entered = threading.Event()
    original = services.execute_tool
    def execute(name, arguments, **kwargs):
        if name == "tasks.list":
            entered.set()
            while True:
                check_cancelled()
                time.sleep(.01)
        return original(name, arguments, **kwargs)
    monkeypatch.setattr(services, "execute_tool", execute)
    def event(kind, value):
        if kind == "operator_session" and value["status"] == "cancelled":
            terminal.set()
    try:
        started = supervisor.start({"goal": "Supervised read", "steps": [
            {"id": "read", "tool": "tasks.list", "arguments": {}}]}, True, approve=lambda _: True, on_event=event)
        assert entered.wait(2)
        services.operator.cancel(started["id"])
        assert terminal.wait(2)
        assert supervisor.inspect(started["id"])["execution_mode"] == "background"
        with pytest.raises(PermissionError, match="read-only"):
            supervisor.start({"goal": "Unsafe", "steps": [
                {"id": "write", "tool": "notes.create", "arguments": {"title": "No", "body": ""}}]},
                True, approve=lambda _: pytest.fail("An unsafe handoff must not request approval"))
        assert not services.list_notes()
    finally:
        supervisor.close()


def test_supervisor_shutdown_reports_native_read_still_stopping(services, monkeypatch):
    supervisor = ExecutionSupervisor(services)
    entered, release, terminal = threading.Event(), threading.Event(), threading.Event()
    def execute(*args, **kwargs):
        entered.set()
        release.wait(3)
        return ToolResult(True, [])
    monkeypatch.setattr(services, "execute_tool", execute)
    def event(kind, value):
        if kind == "operator_session" and value["status"] == "cancelled":
            terminal.set()
    supervisor.start({"goal": "Native read", "steps": [
        {"id": "read", "tool": "tasks.list", "arguments": {}}]}, True, approve=lambda _: True, on_event=event)
    try:
        assert entered.wait(2)
        assert supervisor.close(timeout=.02) is False
        with pytest.raises(ValueError, match="stopping"):
            supervisor.start({"goal": "No new work", "steps": [
                {"id": "read", "tool": "tasks.list", "arguments": {}}]}, True, approve=lambda _: True)
    finally:
        release.set()
        assert terminal.wait(2)
        assert supervisor.close(timeout=2)


def test_supervisor_does_not_reuse_preview_for_nested_plan(services):
    supervisor = ExecutionSupervisor(services)
    nested = {"goal": "Unreviewed nested plan", "steps": [
        {"id": "read", "tool": "tasks.list", "arguments": {}}]}
    routine = services.automation.save("Nested", [{"tool": "operator.run", "arguments": {"plan": nested}}])["id"]
    requests, finished = [], threading.Event()
    def approve(request):
        requests.append(request.tool_name)
        return request.tool_name == "execution.start"
    def event(kind, value):
        if kind == "operator_session" and value.get("parent_id") is None and value["status"] in {"failed", "complete"}:
            finished.set()
    try:
        supervisor.start({"goal": "Review routine", "steps": [
            {"id": "routine", "tool": "automations.run", "arguments": {"id": routine}}]}, approve=approve, on_event=event)
        assert finished.wait(3)
        assert requests == ["execution.start", "operator.run"]
        assert any(row["goal"] == nested["goal"] and row["status"] == "denied" for row in services.operator.list())
    finally:
        assert supervisor.close()


def test_detached_observer_failure_does_not_interrupt_safe_tool_work(services):
    supervisor = ExecutionSupervisor(services)
    def deleted_observer(*args):
        raise RuntimeError("Underlying Qt signal object was deleted")
    try:
        started = supervisor.start({"goal": "Continue reads", "steps": [
            {"id": "tasks", "tool": "tasks.list", "arguments": {}}]},
            True, approve=lambda _: True, on_event=deleted_observer)
        deadline = time.monotonic() + 3
        while supervisor.inspect(started["id"])["status"] not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(.01)
        assert supervisor.inspect(started["id"])["status"] == "complete"
    finally:
        assert supervisor.close()


@pytest.mark.parametrize("broker_available", [False, True])
def test_detached_sensitive_confirmation_uses_persistent_broker_or_denies(services, broker_available):
    supervisor = ExecutionSupervisor(services)
    executed, broker_requests = [], []
    services.registry.register(ToolSpec("test.sensitive", "Sensitive", schema(), permission_level=3),
        lambda _: executed.append(True) or ToolResult(True))
    def short_lived_approval(request):
        if request.tool_name == "execution.start":
            return True
        raise RuntimeError("Underlying Qt launch worker was deleted")
    def broker(request, cancel):
        broker_requests.append(request.tool_name)
        return not cancel.is_set()
    if broker_available:
        supervisor.set_approval_handler(broker)
    try:
        started = supervisor.start({"goal": "Fresh confirmation", "steps": [
            {"id": "action", "tool": "test.sensitive", "arguments": {}}]}, approve=short_lived_approval)
        deadline = time.monotonic() + 3
        while supervisor.inspect(started["id"])["status"] not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(.01)
        result = supervisor.inspect(started["id"])
        assert (result["status"] == "complete") == broker_available
        assert executed == ([True] if broker_available else [])
        assert broker_requests == (["test.sensitive"] if broker_available else [])
        if not broker_available:
            assert result["steps"][0]["failure_class"] == "permission"
    finally:
        assert supervisor.close()


def test_workflow_variables_previous_outputs_and_read_only_collections(services):
    calls = []
    services.registry.register(ToolSpec("test.source", "Local source", schema(), permission_level=1),
        lambda _: ToolResult(True, {"records": [{"name": "OAuth"}, {"name": "Jarvix"}], "private": "not-in-history"}))
    services.registry.register(ToolSpec("test.read", "Read known value", schema({"name": string()}, ("name",)), permission_level=1),
        lambda args: calls.append(args["name"]) or ToolResult(True, {"name": args["name"]}))
    steps = [action("test.source", id="source"),
        {"kind": "set", "name": "records", "value": {"$ref": "results.source.data.records"}},
        {"kind": "branch", "condition": {"kind": "result", "ref": "results.source.ok", "op": "eq", "value": True},
         "then": [{"kind": "foreach", "items": {"$ref": "variables.records"}, "limit": 2,
                   "steps": [action("test.read", {"name": {"$ref": "item.name"}})]}], "else": []}]
    saved = services.workflows.save("Read collection", steps)["id"]
    result = services.workflows.run(saved)
    assert result["ok"] and calls == ["OAuth", "Jarvix"] and result["actions_executed"] == 3
    assert all("started_at" in step and "ended_at" in step for step in result["steps"])
    assert "not-in-history" not in json.dumps(services.workflows.history(saved))
    with pytest.raises(ValueError, match="read-only"):
        services.workflows.save("Unsafe loop", [{"kind": "foreach", "items": [1],
            "steps": [action("notes.create", {"title": "No", "body": ""})]}])
    with pytest.raises(ValueError, match="reference"):
        services.workflows.save("Forward ref", [action("test.read", {"name": {"$ref": "results.future.data"}})])


def test_dynamic_background_mutations_cannot_reuse_literal_approval(services):
    with pytest.raises(PermissionError, match="literal"):
        services.workflows.save("Dynamic write", [action("notes.create", {"title": {"$ref": "variables.title"}, "body": ""})],
            trigger="jarvix_start", enabled=True, variables={"title": "Private"}, approved_tools=["notes.create"])
    assert not services.list_notes()


def test_subflows_are_pinned_and_test_mode_never_runs_sensitive_actions(services):
    child = services.workflows.save("Child", [action("notes.create", {"title": "Preview", "body": ""})])["id"]
    parent = services.workflows.save("Parent", [action(), {"kind": "subflow", "workflow_id": child}])["id"]
    debug = services.workflows.debug(parent)
    assert debug["status"] == "tested" and debug["actions_executed"] == 1 and debug["actions_previewed"] == 1
    assert not services.list_notes()
    row = services.records.get("workflow", parent)
    row["steps"][1]["steps"][0]["arguments"]["title"] = "Tampered"
    services.records.put("workflow", row, parent)
    with pytest.raises(PermissionError, match="snapshot"):
        services.workflows.run(parent)
    services.workflows.save("Child changed", [action()], id=child)
    with pytest.raises(PermissionError, match="edited"):
        services.workflows.run(parent)


def test_manual_references_still_require_fresh_sensitive_confirmation(services):
    called = []
    services.registry.register(ToolSpec("test.sensitive", "Sensitive", schema({"text": string()}, ("text",)), permission_level=3),
        lambda args: called.append(args) or ToolResult(True))
    saved = services.workflows.save("Confirm dynamic values", [action("test.sensitive", {"text": {"$ref": "variables.text"}})],
                                    variables={"text": "Private"})["id"]
    requests = []
    with operation(approve=lambda request: requests.append(request) or False):
        result = services.workflows.run(saved)
    assert not result["ok"] and not called
    assert requests[0].arguments == {"text": "Private"}


def test_collection_limit_is_enforced_after_resolving_outputs(services):
    services.registry.register(ToolSpec("test.source", "Read", schema(), permission_level=1),
        lambda _: ToolResult(True, [1, 2, 3]))
    saved = services.workflows.save("Bound collection", [action("test.source", id="source"),
        {"kind": "foreach", "items": {"$ref": "results.source.data"}, "limit": 2, "steps": [action()]}])["id"]
    result = services.workflows.run(saved)
    assert not result["ok"] and result["actions_executed"] == 1
    assert result["steps"][1]["failure_code"] == "collection_limit"
