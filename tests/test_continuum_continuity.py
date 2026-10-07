"""Saved work survives restart; only explicitly previewed safe reads continue."""
import threading
import time
from types import SimpleNamespace

import pytest

from jarvix.capabilities import continuity
from jarvix.capabilities.checkpoints import CheckpointStore
from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import check_cancelled, operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    if not hasattr(s, "continuity"):
        continuity.setup(s, s.registry)
    root = tmp_path / "project"
    root.mkdir()
    s.add_file_root(str(root))
    s.test_project = s.add_project("Jarvix", str(root))
    s.test_root = root
    s.settings.set("control.enabled", True)
    yield s
    s.close()


def test_checkpoint_restart_retains_links_and_never_executes_next_action(services, monkeypatch):
    file = services.test_root / "work.py"
    file.write_text("work")
    conversation = services.new_conversation()
    mission = services.missions.save("Release", project_id=services.test_project)
    checkpoint = services.continuity.save("Tests remain to finish", project_id=services.test_project,
        mission_id=mission["id"], conversation_id=conversation, files=[str(file)],
        next_action={"description": "Write release notes", "tool": "notes.create", "arguments": {"title": "Release", "body": "Pending"}})
    profile = services.data_dir
    services.close()
    restarted = Services(profile, vault=SimpleNamespace(get=lambda _: None))
    if not hasattr(restarted, "continuity"):
        continuity.setup(restarted, restarted.registry)
    monkeypatch.setattr(restarted, "execute_tool", lambda *_, **__: pytest.fail("Restart inspection cannot execute saved actions"))
    try:
        current = restarted.continuity.get(checkpoint["id"])
        assert current["available"] and current["summary"] == "Tests remain to finish"
        assert current["next_action"]["tool"] == "notes.create"
        assert not current["startup_execution"] and not current["actions_replayed"]
        assert not restarted.list_notes() and not restarted.operator.list()
        restarted.continuity.forget(checkpoint["id"])
        assert restarted.continuity.list()["items"] == []
        assert file.exists() and restarted.missions.get(mission["id"])["goal"] == "Release"
    finally:
        restarted.close()


def test_source_denial_and_removal_hide_summary_and_block_continuation(services):
    task = services.add_task("Linked task")
    mission = services.missions.save("Release", project_id=services.test_project, task_ids=[task])
    checkpoint = services.continuity.save("Private saved context", mission_id=mission["id"])
    services.permissions.set_grant("tasks.list", "deny")
    current = services.continuity.get(checkpoint["id"])
    assert not current["available"] and current["status"] == "permission_denied"
    assert current["summary"] is None and current["next_action"] is None
    assert not services.continuity.start_observations(checkpoint["id"]).ok
    services.permissions.set_grant("tasks.list", None)
    services.delete_task(task)
    current = services.continuity.get(checkpoint["id"])
    assert not current["available"] and current["status"] == "source_unavailable"
    assert not services.operator.list()


def test_prepare_reuses_project_plan_and_only_previews_relevant_reads(services, monkeypatch):
    saved = services.continuity.save("Continue Jarvix", project_id=services.test_project)
    calls, original = [], services.execute_tool
    def execute(name, arguments, **kwargs):
        calls.append(name)
        return original(name, arguments, **kwargs)
    monkeypatch.setattr(services, "execute_tool", execute)
    prepared = services.continuity.prepare()
    assert prepared["prepared"] and not prepared["executed"]
    assert prepared["checkpoint"]["id"] == saved["id"]
    assert "intelligence.prepare" in calls and "operator.preview" in calls
    assert not services.operator.list()
    assert {step["tool"] for step in prepared["observation_plan"]["steps"]} == {"projects.list", "tasks.list"}
    assert prepared["observation_plan"]["timeout_seconds"] == 120
    services.permissions.set_grant("intelligence.prepare", "deny")
    assert not services.continuity.prepare(saved["id"]).ok


def test_explicit_read_continuation_retains_uncertain_write_across_restart(services):
    # Test codec exercises the protected checkpoint format without requiring DPAPI on CI.
    def codec(data, decrypt=False):
        return data
    services.operator.checkpoints = CheckpointStore(services.records, codec)
    writes = []
    services.registry.register(ToolSpec("test.uncertain", "Write receipt", {"type": "object"},
                                      permission_level=2, risk="write"),
                               lambda _: writes.append(True) or ToolResult(False, error="State uncertain"))
    original = services.operator.run({"goal": "Original work", "steps": [
        {"id": "change", "tool": "test.uncertain", "arguments": {}},
        {"id": "read", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    assert original["status"] == "failed" and writes == [True]
    saved = services.continuity.save("Inspect unfinished work", operator_session_id=original["id"], timeout_seconds=30)
    from jarvix.capabilities.operator import OperatorService
    services.operator = OperatorService(services)
    services.operator.checkpoints = CheckpointStore(services.records, codec)
    prepared = services.continuity.prepare(saved["id"])
    assert prepared["recovery_preview"]["recovery"]["uncertain_step_ids"] == ["change"]
    assert not prepared["operator_session"]["uncertain_actions_resolved"]
    terminal, approvals = threading.Event(), []
    def event(kind, value):
        if kind == "operator_session" and value["parent_id"] == original["id"] and value["status"] in {"complete", "failed", "cancelled"}:
            terminal.set()
    started = services.continuity.start_observations(saved["id"],
        approve=lambda request: approvals.append(request) or True, on_event=event)
    assert started["accepted"] and terminal.wait(3)
    assert started["read_only"] and started["owned_cancellation"] and started["deadline_seconds"] == 30
    resumed = services.operator.get(started["id"])
    assert resumed["parent_id"] == original["id"] and resumed["status"] == "complete"
    assert resumed["recovery_plan"]["uncertain_step_ids"] == ["change"]
    assert all(step["tool"] == "tasks.list" for step in resumed["steps"])
    assert writes == [True] and len(approvals) == 1 and approvals[0].tool_name == "execution.start"
    assert not started["uncertain_actions_resolved"] and started["uncertain_step_ids"] == ["change"]


def test_cancel_owned_continuation_and_prevent_unattended_launch(services, monkeypatch):
    saved = services.continuity.save("Observe tasks", project_id=services.test_project)
    entered, stopped = threading.Event(), threading.Event()
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
            stopped.set()
    started = services.continuity.start_observations(saved["id"], approve=lambda _: True, on_event=event)
    assert entered.wait(3)
    services.operator.cancel(started["cancel_id"])
    assert stopped.wait(3)
    assert services.operator.get(started["id"])["execution_mode"] == "background"
    with operation(unattended=True):
        with pytest.raises(PermissionError, match="Unattended"):
            services.continuity.start_observations(saved["id"], approve=lambda _: True)


def test_start_requires_one_fresh_preview_and_respects_denials(services):
    saved = services.continuity.save("Continue", project_id=services.test_project)
    requests = []
    services.settings.set("control.enabled", False)
    result = services.execute_tool("continuity.start_observations", {"id": saved["id"]},
                                   approve=lambda request: requests.append(request) or False)
    assert result.ok and not result.data["accepted"]
    assert len(requests) == 1 and requests[0].tool_name == "execution.start"
    assert not services.operator.list()
    services.permissions.set_grant("execution.start", "deny")
    result = services.continuity.start_observations(saved["id"], approve=lambda _: pytest.fail("Denied execution cannot request confirmation"))
    assert not result.ok and result.data["failure_class"] == "permission_denied"


def test_changed_or_revoked_checkpoint_after_confirmation_does_not_start(services):
    saved = services.continuity.save("Continue", project_id=services.test_project)
    def changed(request):
        services.continuity.save("Updated goal", id=saved["id"])
        return True
    result = services.continuity.start_observations(saved["id"], approve=changed)
    assert not result["accepted"] and result["failure_class"] == "checkpoint_changed"
    assert not services.operator.list()
    def revoked(request):
        services.permissions.set_grant("projects.list", "deny")
        return True
    result = services.continuity.start_observations(saved["id"], approve=revoked)
    assert not result["accepted"] and result["failure_class"] == "permission_denied"
    assert not services.operator.list()


@pytest.mark.parametrize("disabled", [False, True])
def test_saved_summary_never_bypasses_checkpoint_get_denial(services, disabled):
    saved = services.continuity.save("Private checkpoint summary", project_id=services.test_project)
    if disabled:
        services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "continuity.get"])
    else:
        services.permissions.set_grant("continuity.get", "deny")
    assert services.continuity.list()["items"][0]["summary"] is None
    result = services.continuity.prepare(saved["id"])
    assert not result.ok and result.data["status"] == "permission_denied"
    assert result.data["summary"] is None
    assert not services.continuity.start_observations(saved["id"], approve=lambda _: True).ok
    assert not services.operator.list()
    with pytest.raises(PermissionError):
        services.continuity.save("Changed private summary", id=saved["id"])
    assert services.records.get("continuity_checkpoint", saved["id"])["summary"] == "Private checkpoint summary"


@pytest.mark.parametrize("tool", ["execution.start", "operator.preview", "operator.run"])
def test_supervisor_rechecks_task_permissions_after_confirmation(services, tool):
    saved = services.continuity.save("Observe", project_id=services.test_project)
    def revoke(request):
        services.permissions.set_grant(tool, "deny")
        return True
    result = services.continuity.start_observations(saved["id"], approve=revoke)
    assert not result["accepted"] and result["failure_class"] == "permission_denied"
    assert not services.operator.list()


@pytest.mark.parametrize("cause", ["get_denied", "removed_file"])
def test_confirmation_remains_bound_to_accessible_saved_sources(services, cause):
    file = services.test_root / "linked.py"
    file.write_text("local")
    saved = services.continuity.save("Observe", project_id=services.test_project, files=[str(file)])
    def invalidate(request):
        if cause == "get_denied":
            services.permissions.set_grant("continuity.get", "deny")
        else:
            file.unlink()
        return True
    result = services.continuity.start_observations(saved["id"], approve=invalidate)
    assert not result["accepted"] and result["failure_class"] == ("permission_denied" if cause == "get_denied" else "source_unavailable")
    assert not services.operator.list()


def test_checkpoint_bounds_and_mutation_metadata_never_widen_background_policy(services):
    saved = services.continuity.save("Do later", project_id=services.test_project,
        next_action={"description": "Create later", "tool": "notes.create", "arguments": {"title": "No replay", "body": ""}})
    plan = services.continuity.prepare(saved["id"])["observation_plan"]
    assert "notes.create" not in {step["tool"] for step in plan["steps"]}
    assert services.operator.background_safe(plan)
    with pytest.raises(ValueError):
        services.continuity.save("No link")
    for arguments in ({"timeout_seconds": True}, {"project_id": False}, {"summary": "x" * 3001},
                      {"next_action": {"description": "Invalid", "arguments": {}}}):
        with pytest.raises(ValueError):
            services.continuity.save(**{"summary": "Saved", "project_id": services.test_project, **arguments})
    assert not services.list_notes()


@pytest.mark.parametrize("via_mission", [False, True])
@pytest.mark.parametrize("cause", ["removed_root", "denied_source", "deleted_file"])
def test_continuity_fails_closed_when_linked_space_contents_are_unavailable(services, via_mission, cause):
    document = services.test_root / "knowledge.txt"
    document.write_text("Local knowledge")
    space = services.knowledge_spaces.create("Space")["id"]
    services.knowledge_spaces.add_source(space, "document", str(document))
    if via_mission:
        mission = services.missions.save("Mission", knowledge_space_ids=[space])
        links = {"mission_id": mission["id"]}
    else:
        links = {"knowledge_space_id": space}
    saved = services.continuity.save("Private saved knowledge summary", **links,
        next_action={"description": "Observe current tasks", "tool": "tasks.list", "arguments": {}})
    assert services.continuity.prepare(saved["id"])["prepared"]
    if cause == "removed_root":
        services.remove_file_root(str(services.test_root))
    elif cause == "denied_source":
        services.permissions.set_grant("files.inspect", "deny")
    else:
        document.unlink()
    # The Space itself remains inspectable; continuation summaries require its sources too.
    inspected = services.context_graph.resolve("knowledge_space", space)
    assert inspected["available"] and inspected["unavailable_sources"] == 1
    current = services.continuity.get(saved["id"])
    assert not current["available"] and current["summary"] is None and current["next_action"] is None
    assert services.continuity.list()["items"][0]["summary"] is None
    assert not services.continuity.prepare(saved["id"]).ok
    assert not services.continuity.start_observations(saved["id"], approve=lambda _: True).ok
    with pytest.raises(ValueError):
        services.continuity.save("New saved summary", **links)
    with pytest.raises(ValueError):
        services.continuity.save("Changed summary", id=saved["id"])
    assert services.records.get("continuity_checkpoint", saved["id"])["summary"] == "Private saved knowledge summary"
    assert not services.operator.list()


def test_space_source_revocation_after_observation_confirmation_prevents_launch(services):
    document = services.test_root / "knowledge.txt"
    document.write_text("Local knowledge")
    space = services.knowledge_spaces.create("Space")["id"]
    services.knowledge_spaces.add_source(space, "document", str(document))
    saved = services.continuity.save("Private saved knowledge summary", knowledge_space_id=space,
        next_action={"description": "Observe tasks", "tool": "tasks.list", "arguments": {}})
    def revoke(request):
        services.permissions.set_grant("files.inspect", "deny")
        return True
    result = services.continuity.start_observations(saved["id"], approve=revoke)
    assert not result["accepted"] and result["failure_class"] == "source_unavailable"
    assert not services.operator.list()
