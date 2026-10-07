import sys
from datetime import datetime, timedelta, timezone

import pytest

from test_continuum_context import services as services


def test_structured_session_state_expires_and_promotes_only_after_confirmation(services, monkeypatch):
    services.settings.set("context.enabled", True)
    project = services.add_project("Code", str(services.test_root))
    file = services.test_root / "main.py"
    file.write_text("print('local')")
    saved = services.context.remember_session("Use small functions", state={"goal": "Review code",
        "project_id": project, "active_files": [str(file)], "temporary_decisions": ["Keep it local"],
        "unfinished_steps": ["Run tests"], "recent_results": [{"tool": "tasks.list", "ok": True}]},
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=10)).isoformat())
    assert services.context.session()["items"][0]["state"]["goal"] == "Review code"
    assert not services.execute_tool("context.session_promote", {"id": saved["id"], "scope": "project", "project_id": project}).ok
    assert not services.list_memories()
    result = services.execute_tool("context.session_promote", {"id": saved["id"], "scope": "project", "project_id": project},
                                   approve=lambda _: True)
    assert result.ok and result.data["saved"]
    assert not services.context.session()["items"]
    assert services.productivity.memories.explain(result.data["id"])["project_id"] == project
    expired = services.context.remember_session("Temporary", ttl_seconds=30)
    monkeypatch.setattr("jarvix.capabilities.context.time.monotonic", lambda: 10**15)
    assert not services.context.session()["items"]
    with pytest.raises(ValueError):
        services.context.promote_session(expired["id"])


def test_saved_state_rechecks_sources_and_continuity_date_queries(services):
    services.settings.set("context.enabled", True)
    project = services.add_project("Code", str(services.test_root))
    services.context.remember_session("Explicit temporary decision", state={"project_id": project})
    task = services.add_task("Finish checks")
    note = services.save_note("Checkpoint", "Local sources")
    saved = services.continuity.save("Continue checks", task_id=task, note_id=note)
    assert saved["unfinished_work"][0]["id"] == task
    assert services.continuity.list(since="2000-01-01T00:00:00Z")["items"][0]["id"] == saved["id"]
    assert not services.continuity.list(until="2000-01-01T00:00:00Z")["items"]
    services.remove_file_root(str(services.test_root))
    assert services.context.session()["items"][0]["state_unavailable"]
    services.permissions.set_grant("notes.search", "deny")
    assert services.continuity.get(saved["id"])["summary"] is None


def test_release_checklist_and_owned_command_verification_do_not_invent_success(services):
    root = services.test_root
    (root / "pyproject.toml").write_text('[project]\nversion = "0.8.0"\n')
    (root / "README.md").write_text("Install instructions")
    before = services.developer.release_checklist(str(root))
    assert before["version"] == "0.8.0" and not before["ready_to_publish"]
    assert next(check for check in before["checks"] if check["item"] == "Tests")["status"] == "needs_verification"
    session = services.execute_tool("developer.command_start", {"argv": [sys.executable, "-c", "print('passed')"],
        "cwd": str(root)}, approve=lambda _: True)
    verified = services.execute_tool("developer.verify_command", {"session_id": session.data["session_id"]}, approve=lambda _: True)
    assert verified.ok and verified.data["verified"]
    after = services.developer.release_checklist(str(root), test_session_id=session.data["session_id"])
    assert next(check for check in after["checks"] if check["item"] == "Tests")["status"] == "command_exit_verified"
    assert not after["ready_to_publish"]
    failed = services.execute_tool("developer.command_start", {"argv": [sys.executable, "-c", "raise SystemExit(2)"],
        "cwd": str(root)}, approve=lambda _: True)
    assert not services.execute_tool("developer.verify_command", {"session_id": failed.data["session_id"]}, approve=lambda _: True).ok
