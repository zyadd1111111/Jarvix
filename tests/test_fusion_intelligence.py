from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jarvix.capabilities import context_graph, missions, proactive
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    for name, module in (("context_graph", context_graph), ("missions", missions), ("proactive", proactive)):
        if not hasattr(service, name):
            module.setup(service, service.registry)
    yield service
    service.close()


def test_prepare_resolves_mission_sessions_and_only_previews_existing_tools(services, monkeypatch):
    project = services.add_project("Jarvix", str(services.test_root))
    (services.test_root / ".git").mkdir()
    session = services.records.put("operator_session", {"goal": "Prior change", "status": "interrupted", "steps": [
        {"id": "change", "status": "interrupted", "retry_safe": False, "verified": False}]})
    mission = services.missions.save("Finish Fusion", project_id=project, operator_session_ids=[session])
    original, calls = services.execute_tool, []

    def execute(name, arguments, **kwargs):
        calls.append(name)
        return original(name, arguments, **kwargs)

    monkeypatch.setattr(services, "execute_tool", execute)
    result = services.intelligence.prepare(mission_id=mission["id"])
    assert not result["executed"] and not result["cloud_request"]
    assert result["recent_sessions"][0]["id"] == session
    assert "operator.preview" in calls and "operator.run" not in calls
    assert len(services.records.list("operator_session")) == 1
    for step in result["plan"]["steps"]:
        assert services.registry.get(step["tool"]).risk == "read"
        assert services.registry.validate(step["tool"], step["arguments"]) is None
    services.permissions.set_grant("operator.preview", "deny")
    assert not services.execute_tool("intelligence.prepare", {"project_id": project}).ok


def test_prepare_honors_source_denials_and_disabled_reads(services):
    project = services.add_project("Jarvix", str(services.test_root))
    services.permissions.set_grant("tasks.search", "deny")
    plan = services.intelligence.prepare(project_id=project)["plan"]
    assert "tasks.search" not in {step["tool"] for step in plan["steps"]}
    services.permissions.set_grant("projects.list", "deny")
    assert not services.execute_tool("intelligence.prepare", {"project_id": project}).ok
    services.permissions.set_grant("projects.list", None)
    services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "context.graph"])
    assert not services.execute_tool("intelligence.prepare", {"project_id": project}).ok


def test_handoff_uses_bounded_structured_refs_and_host_permissions(services, monkeypatch):
    task = services.add_task("Existing task")
    original, calls = services.execute_tool, []

    def execute(name, arguments, **kwargs):
        calls.append((name, arguments))
        return original(name, arguments, **kwargs)

    monkeypatch.setattr(services, "execute_tool", execute)
    result = services.intelligence.handoff("planner", [
        {"id": "tasks", "tool": "tasks.list", "arguments": {}},
        {"id": "search", "tool": "tasks.search", "arguments": {"query": {"$ref": "tasks.data.items.0.title"}}}])
    assert result["agents_started"] == 0 and not result["cloud_request"]
    assert result["state"]["tasks"]["data"]["items"][0]["id"] == task
    assert calls[1] == ("tasks.search", {"query": "Existing task"})
    calls.clear()
    with pytest.raises(ValueError):
        services.intelligence.handoff("planner", [
            {"id": "first", "tool": "tasks.list", "arguments": {}},
            {"id": "bad", "tool": "tasks.search", "arguments": {"query": {"$ref": "later.data.title"}}}])
    assert not calls
    services.permissions.set_grant("tasks.search", "deny")
    with pytest.raises(PermissionError):
        services.intelligence.handoff("planner", [
            {"id": "first", "tool": "tasks.list", "arguments": {}},
            {"id": "denied", "tool": "tasks.search", "arguments": {}}])
    assert not calls
    with pytest.raises(PermissionError):
        services.intelligence.handoff("planner", [{"id": "write", "tool": "tasks.create", "arguments": {"title": "No"}}])
    assert len(services.list_tasks()) == 1


def test_handoff_rejects_invalid_state_and_missing_result_fields(services):
    with pytest.raises(ValueError):
        services.intelligence.handoff("planner", [{"id": "tasks", "tool": "tasks.list", "arguments": {}}],
                                      state={"prior": "x" * 33000})
    result = services.intelligence.handoff("planner", [
        {"id": "search", "tool": "tasks.search", "arguments": {"query": {"$ref": "prior.data.missing"}}}],
        state={"prior": {"data": {"title": "Task"}}})
    assert not result.ok and not result.data["completed"]


def test_proactive_is_opt_in_local_and_cached_results_recheck_denials(services, monkeypatch):
    due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    task = services.add_task("Due task", due)
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: pytest.fail("Suggestions must use saved local metadata only"))
    assert services.proactive.refresh() == [] and not services.records.list("suggestion")
    services.proactive.configure(enabled=True)
    items = services.proactive.refresh(force=True)
    assert len(items) == 1 and items[0]["reason"] and items[0]["reference"] == task
    services.permissions.set_grant("tasks.list", "deny")
    assert services.proactive.list() == []
    assert services.proactive.refresh() == []
    services.permissions.set_grant("tasks.list", None)
    services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "tasks.search"])
    assert services.proactive.list() == []


def test_proactive_dismissal_and_mutes_survive_refresh_and_restart(services):
    due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    services.add_task("Due task", due)
    services.proactive.configure(enabled=True)
    id = services.proactive.refresh(force=True)[0]["id"]
    services.proactive.dismiss(id)
    services.proactive.configure(muted=["task_due"])
    assert services.settings.get("proactive.enabled")
    assert services.proactive.refresh(force=True) == []
    services.proactive.configure(muted=[])
    assert services.proactive.refresh(force=True) == []
    restarted = proactive.ProactiveService(services)
    assert restarted.refresh(force=True) == []
    assert services.records.get("suggestion.dismissal", id)["reference"]


def test_proactive_hides_stale_source_metadata_and_orphaned_workflows(services):
    due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    task = services.add_task("Due task", due)
    workflow = services.records.put("workflow", {"name": "Existing workflow"})
    run = services.records.put("workflow_run", {"workflow_id": workflow, "status": "failed"})
    services.proactive.configure(enabled=True)
    assert len(services.proactive.refresh(force=True)) == 2
    services.productivity.tasks.update(task, title="Changed task")
    assert {item["reference"] for item in services.proactive.list()} == {run}
    services.records.delete("workflow", workflow)
    assert services.proactive.list() == []
    services.productivity.tasks.complete(task)
    assert services.proactive.refresh(force=True) == []
