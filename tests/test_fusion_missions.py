from types import SimpleNamespace

import pytest

from jarvix.capabilities import context_graph, missions
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    if not hasattr(service, "context_graph"):
        context_graph.setup(service, service.registry)
    if not hasattr(service, "missions"):
        missions.setup(service, service.registry)
    yield service
    service.close()


def test_mission_progress_uses_existing_work_and_revalidates_sources(services):
    project = services.add_project("Jarvix", str(services.test_root))
    note = services.save_note("Plan", "Existing note")
    task = services.add_task("Finish Fusion")
    path = services.test_root / "README.md"
    path.write_text("existing contents", encoding="utf-8")
    space = services.knowledge_spaces.create("Code", project_id=project)["id"]
    services.knowledge_spaces.add_source(space, "document", str(path))
    mission = services.missions.save("Finish Jarvix 0.7", project_id=project, task_ids=[task, task],
                                     note_ids=[note], files=[str(path)], knowledge_space_ids=[space])
    id = mission["id"]
    assert mission["task_ids"] == [task] and mission["progress"]["percent"] == 0
    assert len(services.list_tasks()) == 1
    services.productivity.tasks.complete(task)
    assert services.missions.summary(id)["progress"]["percent"] == 100
    services.remove_file_root(str(services.test_root))
    result = services.missions.get(id)
    assert result["current_blockers"]
    assert not next(item for item in result["references"] if item["kind"] == "file")["available"]
    assert next(item for item in result["references"] if item["kind"] == "knowledge_space")["unavailable_sources"] == 1
    graph = services.context_graph.graph(kind="project", reference=project)
    assert not next(node for node in graph["nodes"] if node["kind"] == "project")["available"]
    with pytest.raises(ValueError):
        services.missions.complete(id)
    assert path.read_text(encoding="utf-8") == "existing contents"


def test_restart_resume_never_replays_uncertain_operator_actions(services, monkeypatch):
    session = services.records.put("operator_session", {
        "goal": "Change files", "status": "running", "steps": [
            {"id": "change", "status": "running", "retry_safe": False, "verified": False}]})
    mission = services.missions.save("Release", operator_session_ids=[session])
    services.missions.pause(mission["id"])
    data_dir = services.data_dir
    services.close()
    reopened = Services(data_dir, vault=SimpleNamespace(get=lambda _: None))
    if not hasattr(reopened, "context_graph"):
        context_graph.setup(reopened, reopened.registry)
    if not hasattr(reopened, "missions"):
        missions.setup(reopened, reopened.registry)
    calls = []
    monkeypatch.setattr(reopened, "execute_tool", lambda *args, **kwargs: calls.append(args))
    try:
        result = reopened.missions.resume(mission["id"])
        assert result["status"] == "active" and not result["actions_replayed"] and not calls
        assert result["operator_sessions"][0]["uncertain_steps"] == ["change"]
        assert result["operator_sessions"][0]["resume_requires_new_plan"]
        assert result["progress"]["percent"] == 0 and result["current_blockers"]
        assert [entry["action"] for entry in reopened.missions.get(mission["id"])["history"]] == ["created", "paused", "active"]
    finally:
        reopened.close()


def test_graph_links_metadata_and_derived_missions_without_reading_content(services, monkeypatch):
    task = services.add_task("Code")
    note = services.save_note("Research", "secret note contents should not appear")
    mission = services.missions.save("Fusion", task_ids=[task], note_ids=[note])
    source = {"kind": "note", "reference": note}
    target = {"kind": "github_issue", "reference": "https://github.com/example/jarvix/issues/7"}
    edge = services.context_graph.link(source, target, "research")
    assert services.context_graph.link(source, target, "research")["id"] == edge["id"]
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: pytest.fail("Graph must not call external tools"))
    graph = services.context_graph.graph(kind="mission", reference=mission["id"], depth=2)
    assert {node["kind"] for node in graph["nodes"]} == {"mission", "task", "note", "github_issue"}
    assert "secret note contents" not in str(graph) and not graph["automatic_cloud_sharing"]
    assert len(services.context_graph.graph(limit=1)["edges"]) == 1
    assert services.context_graph.graph(limit=1)["truncated"]
    services.delete_note(note)
    graph = services.context_graph.graph(kind="mission", reference=mission["id"], depth=2)
    assert not next(node for node in graph["nodes"] if node["kind"] == "note")["available"]
    services.context_graph.unlink(edge["id"])
    assert not services.records.list("context.link")


def test_mission_edits_are_atomic_and_deletion_preserves_linked_work(services):
    task = services.add_task("Existing")
    id = services.missions.save("Fusion", task_ids=[task])["id"]
    with pytest.raises(ValueError):
        services.missions.save("Changed", id=id, task_ids=["missing"])
    assert services.missions.get(id)["goal"] == "Fusion"
    services.missions.archive(id)
    assert services.missions.list(status="archived")["items"][0]["id"] == id
    services.missions.resume(id)
    denied = services.execute_tool("missions.cancel", {"id": id}, approve=lambda _: False)
    assert not denied.ok and services.missions.get(id)["status"] == "active"
    assert services.execute_tool("missions.cancel", {"id": id}, approve=lambda _: True).ok
    with pytest.raises(ValueError, match="cancelled"):
        services.missions.resume(id)
    services.missions.delete(id)
    assert services.list_tasks()[0]["id"] == task
    assert services.registry.get("missions.delete").permission_level == 3
    assert services.registry.get("context.unlink").permission_level == 3
    with pytest.raises(ValueError):
        services.context_graph.link({"kind": "task", "reference": task},
                                    {"kind": "github_issue", "reference": "http://127.0.0.1/issues/7"})


def test_graph_and_mission_links_cannot_bypass_source_denials(services):
    note = services.save_note("Private title", "Private contents")
    task = services.add_task("Private task")
    conversation = services.new_conversation("Private conversation")
    session = services.records.put("operator_session", {"goal": "Private session", "status": "interrupted", "steps": [
        {"id": "change", "status": "interrupted", "retry_safe": False, "verified": False}]})
    id = services.missions.save("Mission", note_ids=[note], task_ids=[task], conversation_ids=[conversation],
                                operator_session_ids=[session])["id"]
    for name in ("notes.search", "tasks.search", "conversations.search", "operator.session"):
        services.permissions.set_grant(name, "deny")
    graph = services.context_graph.graph(kind="mission", reference=id)
    assert "Private" not in str(graph)
    assert not any(node["available"] for node in graph["nodes"] if node["kind"] != "mission")
    mission = services.missions.get(id)
    assert not mission["operator_sessions"] and "Private" not in str(mission)
    services.permissions.set_grant("notes.search", None)
    services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "notes.search"])
    assert not services.context_graph.inspect_reference("note", note)["available"]


def test_milestones_deadlines_skills_and_structured_next_steps_preserve_actions(services):
    task = services.add_task("Run approved checks")
    args = {"name": "Review tasks", "recipe": {"steps": [{"kind": "action", "tool": "tasks.list", "arguments": {}}]}}
    review = services.skills.learn_preview(**args)
    skill = services.execute_tool("skills.save", {**args, "review_fingerprint": review["review_fingerprint"]}, approve=lambda _: True).data
    mission = services.missions.save("Release Continuum", skill_ids=[skill["id"]], deadline="2020-01-01T12:00:00Z",
        next_steps=["Review release notes"], milestones=[{"id": "checks", "title": "Release checks", "task_ids": [task],
                                                        "deadline": "2020-01-01T12:00:00Z"}])
    assert mission["progress"]["total"] == 1 and mission["progress"]["milestones_total"] == 1
    assert mission["overdue"] and mission["milestones"][0]["overdue"]
    assert {item["kind"] for item in mission["next_step_details"]} == {"recorded", "task", "milestone"}
    assert not mission["actions_replayed"] and services.workflows.history(skill["routine_id"]) == []
    with pytest.raises(ValueError):
        services.missions.complete(mission["id"])
    with pytest.raises(ValueError):
        services.missions.save("Wrong", id=mission["id"], milestones=[{"id": "checks", "title": "Checks", "status": "complete", "task_ids": [task]}])
    assert services.missions.get(mission["id"])["goal"] == "Release Continuum"
    services.productivity.tasks.complete(task)
    summary = services.missions.summary(mission["id"])
    assert summary["progress"]["milestones_completed"] == 1 and summary["progress"]["percent"] == 100
    assert summary["skill_ids"] == [skill["id"]] and summary["recent_history"][0]["fields"]
    assert services.missions.complete(mission["id"])["status"] == "complete"
    assert services.workflows.history(skill["routine_id"]) == []


def test_milestone_source_denials_and_invalid_deadlines_are_revalidated(services):
    task = services.add_task("Private task")
    mission = services.missions.save("Goal", milestones=[{"id": "m", "title": "Milestone", "task_ids": [task]}])
    services.permissions.set_grant("tasks.search", "deny")
    result = services.missions.summary(mission["id"])
    assert "Private task" not in str(result) and result["current_blockers"]
    assert result["milestones"][0]["unavailable_task_ids"] == [task]
    with pytest.raises(ValueError):
        services.missions.save("Invalid", deadline="2026-10-04")
    with pytest.raises(ValueError):
        services.missions.save("Invalid", milestones=[{"id": "same", "title": "One"}, {"id": "same", "title": "Two"}])


def test_graph_skill_and_registered_app_relationships_are_local_metadata_only(services, monkeypatch):
    import sys
    app = services.add_app("Python", sys.executable)
    args = {"name": "List tasks", "recipe": {"steps": [{"kind": "action", "tool": "tasks.list", "arguments": {}}]}}
    review = services.skills.learn_preview(**args)
    skill = services.execute_tool("skills.save", {**args, "review_fingerprint": review["review_fingerprint"]}, approve=lambda _: True).data
    mission = services.missions.save("Mission", skill_ids=[skill["id"]])
    edge = services.context_graph.link({"kind": "skill", "reference": skill["id"]}, {"kind": "app", "reference": app},
                                      "uses_app", metadata={"source": "user", "confidence": .8})
    monkeypatch.setattr(services, "execute_tool", lambda *args, **kwargs: pytest.fail("Graph must not execute source tools"))
    graph = services.context_graph.graph(kind="mission", reference=mission["id"], depth=3)
    assert {node["kind"] for node in graph["nodes"]} == {"mission", "skill", "routine", "app"}
    explicit = next(item for item in graph["edges"] if item["id"] == edge["id"])
    assert explicit["metadata"] == {"source": "user", "type": "explicit", "confidence": .8, "scope": "local"}
    assert all(item["metadata"]["scope"] == "local" for item in graph["edges"])
    assert next(node for node in graph["nodes"] if node["kind"] == "app")["registered"]
    assert "steps" not in str(graph) and "arguments" not in str(graph)
    services.permissions.set_grant("apps.list", "deny")
    services.permissions.set_grant("skills.get", "deny")
    hidden = services.context_graph.graph(kind="mission", reference=mission["id"], depth=3)
    assert not any(node["available"] for node in hidden["nodes"] if node["kind"] in {"skill", "app"})
    assert all("label" not in node for node in hidden["nodes"] if node["kind"] in {"skill", "app"})
    assert "Python" not in str(hidden)
