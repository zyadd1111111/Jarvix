import json
import threading
from types import SimpleNamespace

import pytest

from jarvix.capabilities.checkpoints import CheckpointStore
from jarvix.capabilities.operator import OperatorService
from jarvix.domain import ToolResult
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    s.settings.set("control.enabled", True)
    # Reversible test codec exercises persistence on platforms without DPAPI.
    def codec(data, decrypt=False):
        return bytes(value ^ 77 for value in data)
    s.operator.checkpoints = CheckpointStore(s.records, codec)
    s.test_codec = codec
    yield s
    s.close()


def test_dependencies_parallel_reads_and_mutation_barrier(services, monkeypatch):
    barrier = threading.Barrier(2)
    original = services.execute_tool
    visits = []
    def execute(name, args, **kwargs):
        if name in {"tasks.list", "projects.list"}:
            barrier.wait(3)
            visits.append(name)
        return original(name, args, **kwargs)
    monkeypatch.setattr(services, "execute_tool", execute)
    plan = {"goal": "Read then write", "steps": [
        {"id": "save", "tool": "notes.create", "arguments": {"title": "Done", "body": ""}, "depends_on": ["tasks", "projects"]},
        {"id": "tasks", "tool": "tasks.list", "arguments": {}, "depends_on": []},
        {"id": "projects", "tool": "projects.list", "arguments": {}, "depends_on": []}]}
    result = services.operator.run(plan, approve=lambda _: True)
    assert result["ok"] and len(visits) == 2 and len(services.list_notes()) == 1
    assert [step["id"] for step in result["steps"]] == ["tasks", "projects", "save"]


def test_cyclic_dependencies_never_execute(services):
    plan = {"goal": "Invalid", "steps": [
        {"id": "a", "tool": "tasks.list", "arguments": {}, "depends_on": ["b"]},
        {"id": "b", "tool": "tasks.list", "arguments": {}, "depends_on": ["a"]}]}
    with pytest.raises(ValueError, match="cycle"):
        services.operator.run(plan, approve=lambda _: pytest.fail("Invalid plan requested approval"))


def test_restart_recovers_checkpoint_without_repeating_completed_write(services, monkeypatch):
    original = services.execute_tool
    def fail(name, args, **kwargs):
        if name == "tasks.list":
            return ToolResult(False, error="Temporary read failure")
        return original(name, args, **kwargs)
    monkeypatch.setattr(services, "execute_tool", fail)
    plan = {"goal": "Resume", "steps": [
        {"id": "note", "tool": "notes.create", "arguments": {"title": "Once", "body": "private-checkpoint-text"}},
        {"id": "read", "tool": "tasks.list", "arguments": {}}]}
    failed = services.operator.run(plan, approve=lambda _: True)
    assert failed["status"] == "failed" and failed["checkpoint_available"]
    encoded = json.dumps(services.records.list("operator_checkpoint"))
    assert "private-checkpoint-text" not in encoded
    assert "private-checkpoint-text" not in json.dumps(services.operator.list())
    restored = OperatorService(services)
    restored.checkpoints = CheckpointStore(services.records, services.test_codec)
    monkeypatch.setattr(services, "execute_tool", original)
    result = restored.retry(failed["id"], approve=lambda _: True)
    assert result["ok"] and len(services.list_notes()) == 1
    restored.forget_checkpoint(result["id"])
    assert not restored.get(result["id"])["checkpoint_available"]


def test_checkpoint_tampering_and_uncertain_mutation_cannot_replay(services, monkeypatch):
    original = services.execute_tool
    def uncertain(name, args, **kwargs):
        result = original(name, args, **kwargs)
        return ToolResult(False) if name == "notes.create" else result
    monkeypatch.setattr(services, "execute_tool", uncertain)
    result = services.operator.run({"goal": "Uncertain", "steps": [
        {"id": "note", "tool": "notes.create", "arguments": {"title": "Once", "body": ""}}]}, approve=lambda _: True)
    restored = OperatorService(services)
    restored.checkpoints = CheckpointStore(services.records, services.test_codec)
    with pytest.raises(ValueError, match="new plan"):
        restored.retry(result["id"], approve=lambda _: True)
    assert len(services.list_notes()) == 1

