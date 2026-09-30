import sys
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


def test_recurring_completion_is_idempotent_and_subtasks_reject_cycles(services):
    tasks = services.productivity.tasks
    parent = services.add_task("Monthly review", "2026-01-31T10:00:00+00:00")
    tasks.update(parent, recurrence="monthly", priority="high", tags=["work", "work"])
    child = tasks.subtask(parent, "Prepare report")["id"]
    with pytest.raises(ValueError, match="cycles"):
        tasks.update(parent, parent_id=child)
    completed = services.execute_tool("tasks.complete", {"id": parent}, approve=lambda _: True)
    next_id = completed.data["next_occurrence_id"]
    next_task = next(item for item in services.list_tasks() if item["id"] == next_id)
    assert next_task["due_at"] == "2026-02-28T10:00:00+00:00"
    assert tasks.complete(parent)["already_completed"]
    assert len(services.list_tasks()) == 3
    tasks.reopen(parent)
    assert tasks.complete(parent)["next_occurrence_id"] is None
    assert len(services.list_tasks()) == 3
    assert len(services.notifications.list(category="task")) == 1


def test_note_snapshots_restore_and_export_never_overwrite(services):
    notes = services.productivity.notes
    note_id = services.save_note("Plan", "old contents")
    notes.edit(note_id, "Plan", "new contents")
    version_id = notes.history(note_id)["items"][0]["id"]
    notes.restore_version(version_id)
    assert next(row for row in services.list_notes() if row["id"] == note_id)["body"] == "old contents"
    assert any(row["body"] == "new contents" for row in notes.history(note_id)["items"])
    target = services.test_root / "plan.md"
    notes.export(note_id, str(target))
    before = target.read_text()
    with pytest.raises(FileExistsError):
        notes.export(note_id, str(target))
    assert target.read_text() == before


def test_workspace_routines_dispatch_real_tools_and_stop_after_denial(services, monkeypatch):
    routine_id = services.automation.save("Today", [{"tool": "tasks.search", "arguments": {"view": "today"}}])["id"]
    workspace_id = services.workspaces.save("Work", folders=[str(services.test_root)],
                                             urls=["https://example.org"], automation_ids=[routine_id])["id"]
    preview = services.workspaces.preview(workspace_id)
    assert [a["tool"] for a in preview["actions"]] == ["files.open_folder", "web.open", "automations.run"]
    for action in preview["actions"]:
        assert services.registry.validate(action["tool"], action["arguments"]) is None
    calls = []
    def execute(name, arguments):
        calls.append((name, arguments))
        return ToolResult(name != "web.open", {})
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.workspaces.launch(workspace_id)["completed"] is False
    assert [name for name, _ in calls] == ["files.open_folder", "web.open"]
    with pytest.raises(ValueError):
        services.workspaces.save("Invalid", automation_ids=["missing-routine"])


def test_app_arguments_always_confirmed_and_hidden_from_search(services, monkeypatch):
    app_id = services.add_app("Fixture Python", sys.executable)
    services.settings.set("control.enabled", True)
    arguments = {"id": app_id, "arguments": ["-c", "print('fixture-secret')"]}
    declined = services.execute_tool("apps.configure_arguments", arguments, approve=lambda _: False)
    assert not declined.ok and services.apps.arguments_for(app_id) == []
    accepted = services.execute_tool("apps.configure_arguments", arguments, approve=lambda _: True)
    assert accepted.ok and services.apps.arguments_for(app_id) == arguments["arguments"]
    assert "fixture-secret" not in str(services.apps.list())
    services.apps.configure(app_id, aliases=["fixture-code"], favorite=True)
    calls = []
    monkeypatch.setattr(services, "execute_tool", lambda name, args: calls.append((name, args)) or ToolResult(True, {}))
    services.apps.open_alias("FIXTURE-CODE")
    assert calls == [("apps.open", {"id": app_id})]


def test_browser_import_is_all_or_nothing_and_groups_check_every_action(services, monkeypatch):
    browser = services.browser
    with pytest.raises(ValueError):
        browser.import_history([{"title": "Public", "url": "https://example.org"},
                                {"title": "Private", "url": "http://127.0.0.1/admin"}])
    assert browser.history()["items"] == []
    group_id = browser.save_group("School", ["https://example.org", "https://example.com"])["id"]
    calls = []
    monkeypatch.setattr(services, "execute_tool", lambda name, args: calls.append((name, args)) or ToolResult(False, {}))
    assert browser.open_group(group_id)["completed"] is False
    assert calls == [("web.open", {"url": "https://example.org"})]
