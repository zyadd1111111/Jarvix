import json
from types import SimpleNamespace

import pytest

from jarvix.domain import ToolResult
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    yield service
    service.close()


def test_memory_relevance_is_scoped_explained_and_excludes_expired(services):
    memories = services.productivity.memories
    project = services.add_project("Jarvix", str(services.test_root))
    other_root = services.test_root / "school"
    other_root.mkdir()
    other = services.add_project("School", str(other_root))
    workspace = services.workspaces.save("Coding")["id"]
    scoped = memories.remember_sensitive("Python coding conventions", project_id=project,
                                        workspace_id=workspace, importance=5)["id"]
    global_id = memories.remember_sensitive("Python documentation", importance=2)["id"]
    memories.remember_sensitive("Python homework", project_id=other)
    memories.remember_sensitive("Python old preference", expires_at="2000-01-01T00:00:00Z")
    results = memories.relevant("python", project, workspace)
    assert [item["id"] for item in results["items"]] == [scoped, global_id]
    assert results["items"][0]["relevance_score"] > results["items"][1]["relevance_score"]
    assert "Same project" in results["items"][0]["relevance_reasons"]
    assert [item["id"] for item in memories.relevant("python")["items"]] == [global_id]
    assert memories.relevant("unrelated")["items"] == []
    assert not results["automatic_cloud_sharing"]


def test_duplicates_and_conflicts_require_current_explicit_review(services):
    memories = services.productivity.memories
    first = memories.remember_sensitive("My editor is VS Code", subject_key="preferred editor")["id"]
    duplicate = memories.remember_sensitive("My EDITOR is VS Code!")
    assert isinstance(duplicate, ToolResult) and not duplicate.ok
    assert duplicate.data["duplicates"][0]["id"] == first
    proposed = "My editor is Vim"
    conflict = memories.remember_sensitive(proposed, subject_key="preferred editor")
    assert not conflict.ok and conflict.data["conflicts"][0]["id"] == first
    assert len(services.list_memories()) == 1
    old_token = conflict.data["review_token"]
    memories.edit(first, source="Changed source")
    stale = memories.remember_sensitive(proposed, subject_key="preferred editor", conflict_policy="keep_separate",
                                        review_token=old_token)
    assert not stale.ok
    kept = memories.remember_sensitive(proposed, subject_key="preferred editor", conflict_policy="keep_separate",
                                       review_token=stale.data["review_token"])
    assert kept["saved"] and len(services.list_memories()) == 2
    assert next(item for item in services.list_memories() if item["id"] == first)["content"] == "My editor is VS Code"


def test_memory_edits_validate_scope_before_mutation_and_explain_provenance(services):
    memories = services.productivity.memories
    id = memories.remember_sensitive("A source fact", source="User supplied document", source_kind="document",
                                     source_detail="Project overview, section 2")["id"]
    with pytest.raises(ValueError):
        memories.edit(id, content="Must not be saved", project_id="missing")
    assert memories.explain(id)["content"] == "A source fact"
    memories.edit(id, source="Corrected document", expires_at="2000-01-01T00:00:00Z")
    explanation = memories.explain(id)
    assert explanation["source"] == "Corrected document" and explanation["expired"]
    assert explanation["source_detail"] == "Project overview, section 2"
    assert "not independently verified" in explanation["why_known"]
    assert memories.list()["items"] == []
    assert memories.list(include_expired=True)["items"][0]["id"] == id


def test_session_context_isolated_and_never_persisted_or_automatically_shared(services):
    services.settings.set("context.enabled", True)
    first = services.new_conversation("First")
    second = services.new_conversation("Second")
    services.context.set_current(conversation_id=first)
    saved = services.context.remember_session("temporary-private-value", source="Explicit message")
    assert not saved["stored"]
    services.context.set_current(conversation_id=second)
    assert services.context.session()["items"] == []
    assert services.context.session(first)["items"][0]["content"] == "temporary-private-value"
    assert "temporary-private-value" not in json.dumps(services.context_preview(first, "openai", "model"))
    assert services.db.query("SELECT COUNT(*) AS n FROM memories")[0]["n"] == 0
    assert services.db.query("SELECT COUNT(*) AS n FROM records WHERE data LIKE '%temporary-private-value%'")[0]["n"] == 0
    assert services.context.forget_session(saved["id"], second)["removed"] == 0
    assert services.context.forget_session(saved["id"], first)["removed"] == 1


def test_session_context_ttl_opt_in_and_explicit_clear(services, monkeypatch):
    with pytest.raises(PermissionError):
        services.context.remember_session("No implicit collection")
    services.settings.set("context.enabled", True)
    clock = [100.0]
    monkeypatch.setattr("jarvix.capabilities.context.time.monotonic", lambda: clock[0])
    with pytest.raises(ValueError):
        services.context.remember_session("Invalid expiry", ttl_seconds=28801)
    services.context.remember_session("Expires soon", ttl_seconds=30)
    assert len(services.context.session()["items"]) == 1
    clock[0] += 30
    assert services.context.session()["items"] == []
    services.context.remember_session("User can remove it")
    assert services.context.clear()["cleared"]
    assert services.context.session()["items"] == []
    services.context.remember_session("Switch off clears it on access")
    services.settings.set("context.enabled", False)
    with pytest.raises(PermissionError):
        services.context.session()
    services.settings.set("context.enabled", True)
    assert services.context.session()["items"] == []


def test_selection_is_atomic_and_does_not_leak_between_conversations(services, monkeypatch):
    services.settings.set("context.enabled", True)
    monkeypatch.setattr(services, "execute_tool", lambda *_: ToolResult(True, {"title": "Fixture"}))
    first, second = services.new_conversation("A"), services.new_conversation("B")
    file = services.test_root / "selected.txt"
    file.write_text("Selected", encoding="utf-8")
    project = services.add_project("Project", str(services.test_root))
    workspace = services.workspaces.save("Workspace")["id"]
    services.context.set_current(first, project, [str(file)], workspace)
    with pytest.raises(ValueError):
        services.context.set_current(second, project_id="missing")
    before = services.context.inspect(include_selected_files=True)
    assert before["conversation_id"] == first and before["selected_files"] == [str(file)]
    services.context.set_current(second)
    after = services.context.inspect(include_selected_files=True)
    assert after["conversation_id"] == second and not after["selected_files"] and after["project"] is None
    assert after["workspace"] is None


def test_sensitive_memory_and_session_writes_need_fresh_confirmation_without_logging_text(services):
    services.settings.set("control.enabled", True)
    services.settings.set("context.enabled", True)
    for name in ("memory.remember_sensitive", "context.session_remember"):
        services.permissions.set_grant(name, "allow")
        args = {"content": "private-value-738"}
        assert not services.execute_tool(name, args).ok
        assert services.execute_tool(name, args, approve=lambda _: True).ok
        assert not services.execute_tool(name, {"content": "private-value-739"}).ok
    assert "private-value" not in json.dumps(services.activity())
    assert "private-value" not in json.dumps(services.records.list("action_history"))
    assert services.registry.get("memory.edit").permission_level == 3
    assert services.registry.get("memory.delete").permission_level == 3


def test_memory_matching_retains_scope_and_duplicates_beyond_display_limits(services):
    project = services.add_project("Scoped project", str(services.test_root))
    with services.db.connect() as conn:
        conn.execute("INSERT INTO memories VALUES (?,?,?)", ("old", "old reserved fact", "2000-01-01T00:00:00Z"))
        conn.execute("INSERT INTO records VALUES (?,?,?,?,?)", (
            "old", "memory.meta", json.dumps({"project_id": project}), "2000", "2000"))
        conn.executemany("INSERT INTO memories VALUES (?,?,?)", [
            (f"new-{number}", f"Recent fact {number}", "2026-01-01T00:00:00Z") for number in range(2100)])
        conn.executemany("INSERT INTO records VALUES (?,?,?,?,?)", [
            (f"new-{number}", "memory.meta", "{}", "2026", "2026") for number in range(2100)])
    memories = services.productivity.memories
    assert memories.relevant("reserved")["items"] == []
    assert memories.relevant("reserved", project_id=project)["items"][0]["id"] == "old"
    assert memories.check("old reserved fact", project_id=project)["duplicates"][0]["id"] == "old"


def test_failed_memory_metadata_write_rolls_back_content_and_new_fact(services):
    import sqlite3

    memories = services.productivity.memories
    id = memories.remember_sensitive("Original approved content")["id"]
    with services.db.connect() as conn:
        conn.execute("CREATE TRIGGER fail_memory_metadata BEFORE INSERT ON records WHEN NEW.kind='memory.meta' "
                     "BEGIN SELECT RAISE(ABORT, 'metadata unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        memories.edit(id, content="Must not replace approved content")
    with pytest.raises(sqlite3.IntegrityError):
        memories.remember_sensitive("Must not become an unscoped fact")
    assert [item["content"] for item in services.list_memories()] == ["Original approved content"]


def test_session_context_pages_long_entries_within_tool_result_limit(services):
    services.settings.set("context.enabled", True)
    for number in range(12):
        services.context.remember_session(f"{number}:" + "private text " * 300)
    first = services.execute_tool("context.session_inspect", {})
    assert first.ok and len(first.data["items"]) == 10 and first.data["next_cursor"] == 10
    last = services.execute_tool("context.session_inspect", {"cursor": 10})
    assert last.ok and len(last.data["items"]) == 2 and last.data["next_cursor"] is None
