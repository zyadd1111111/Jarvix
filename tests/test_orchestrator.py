import json
import threading

import pytest

from jarvix.domain import Completion, Message, ProviderError, ToolCall, ToolResult, ToolSpec
from jarvix.orchestrator import Orchestrator, bounded_history
from jarvix.security import PermissionService
from jarvix.storage import Database, Repository
from jarvix.tools import ToolRegistry


class ScriptedProvider:
    id = "scripted"

    def __init__(self, replies):
        self.replies, self.requests = iter(replies), []

    def complete(self, messages, tools, model):
        self.requests.append(list(messages))
        return Completion(next(self.replies))


@pytest.fixture
def setup_loop(tmp_path):
    db = Database(tmp_path / "data.db")
    repo = Repository(db)
    registry = ToolRegistry()
    executed = []
    def handler(args):
        executed.append(args)
        return ToolResult(True, {"content": "private-local-record"})
    registry.register(ToolSpec("notes.search", "Search local notes", {
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"], "additionalProperties": False}), handler)
    return Orchestrator(registry, PermissionService(db, repo), repo), executed


def plan(name="notes.search", args=None):
    return Message("assistant", tool_calls=[ToolCall("call-1", name, args if args is not None else {"query": "work"})])


def run(loop, provider, approve, cancel=None, max_rounds=6):
    return loop.run(provider, "test-model", [Message("user", "Find my note")], ["notes.search"],
                    approve, lambda *_: None, cancel or threading.Event(), max_rounds=max_rounds)


def test_execute_then_exact_disclosure_then_response(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan(), Message("assistant", "Found your note")])
    requests = []
    def approve(request):
        requests.append(request)
        return True
    assert run(loop, provider, approve) == "Found your note"
    assert executed == [{"query": "work"}]
    assert [request.kind for request in requests] == ["execute", "disclose"]
    sent = provider.requests[1][-1]
    assert sent.content == requests[1].preview
    assert "private-local-record" in sent.content


def test_execution_denial_prevents_side_effect(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan(), Message("assistant", "Permission denied")])
    run(loop, provider, lambda _: False)
    assert not executed
    assert "denied execution" in provider.requests[1][-1].content


def test_disclosure_denial_never_sends_local_result(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan(), Message("assistant", "Kept locally")])
    run(loop, provider, lambda req: req.kind == "execute")
    assert executed
    assert "private-local-record" not in str(provider.requests)
    assert json.loads(provider.requests[1][-1].content)["disclosure_denied"] is True
    assert "private-local-record" not in str(loop.repository.db.query("SELECT * FROM activity"))


def test_schema_invalid_call_never_requests_permission(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan(args={"unexpected": 4}), Message("assistant", "Invalid")])
    requests = []
    run(loop, provider, lambda request: requests.append(request))
    assert not requests and not executed
    assert json.loads(provider.requests[1][-1].content)["ok"] is False


def test_disabled_tool_never_executes(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan("shell.execute"), Message("assistant", "Unavailable")])
    run(loop, provider, lambda _: True)
    assert not executed


def test_cancellation_before_execution(setup_loop):
    loop, executed = setup_loop
    cancel = threading.Event()
    def approve(_):
        cancel.set()
        return True
    provider = ScriptedProvider([plan()])
    assert "stopped" in run(loop, provider, approve, cancel)
    assert not executed


def test_round_limit_stops_runaway_provider(setup_loop):
    loop, executed = setup_loop
    second = Message("assistant", tool_calls=[ToolCall("call-2", "notes.search", {"query": "work"})])
    provider = ScriptedProvider([plan(), second])
    with pytest.raises(ProviderError, match="planning limit"):
        run(loop, provider, lambda _: True, max_rounds=2)
    assert len(executed) == 2


def test_reused_call_id_cannot_repeat_action(setup_loop):
    loop, executed = setup_loop
    provider = ScriptedProvider([plan(), plan()])
    assert "repeated a tool-call ID" in run(loop, provider, lambda _: True)
    assert len(executed) == 1


def test_identical_write_with_new_id_runs_only_once(setup_loop):
    loop, executed = setup_loop
    def write(args):
        executed.append(args)
        return ToolResult(True, {"saved": True})
    loop.registry.register(ToolSpec("notes.create", "Save note", {"type": "object"}, risk="write"), write)
    provider = ScriptedProvider([
        Message("assistant", tool_calls=[ToolCall("a", "notes.create", {"body": "one"})]),
        Message("assistant", tool_calls=[ToolCall("b", "notes.create", {"body": "one"})]),
        Message("assistant", "Saved")])
    loop.run(provider, "test", [Message("user", "Save note")], ["notes.create"], lambda _: True,
             lambda *_: None, threading.Event())
    assert executed == [{"body": "one"}]


def test_stored_execution_grant_never_grants_disclosure(setup_loop):
    loop, _ = setup_loop
    loop.permissions.set_grant("notes.search", "allow")
    requests = []
    provider = ScriptedProvider([plan(), Message("assistant", "Done")])
    run(loop, provider, lambda request: requests.append(request) or False)
    assert [request.kind for request in requests] == ["disclose"]


def test_context_is_bounded_and_system_instructions_retained():
    messages = bounded_history([{"role": "user", "content": "x" * 1000} for _ in range(100)])
    assert messages[0].role == "system"
    assert len(messages) <= 29
