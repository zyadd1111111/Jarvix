"""Nexus workspaces present shared local data and retain existing action boundaries."""
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QPushButton

from test_interface_modes import opened_interface
from test_ui import app as app, wait_until


def test_nexus_tasks_views_use_real_dates_links_and_completion(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        s = window.services
        folder = tmp_path / "project"
        folder.mkdir()
        project = s.add_project("Release", folder)
        now = datetime.now().astimezone()
        today = s.add_task("Review release", now.isoformat())
        upcoming = s.add_task("Publish release", (now + timedelta(days=3)).isoformat())
        s.productivity.tasks.update(today, priority="high", project_id=project)
        window.navigate("Tasks")
        view = window.pages["Tasks"]
        view.view.setCurrentIndex(view.view.findData("today"))
        assert view.entries.rowCount() == 1
        assert view.entries.item(0, 1).text() == "Review release"
        assert view.entries.item(0, 2).text() == "Release"
        assert view.entries.item(0, 4).text() == "High"
        view.entries.item(0, 0).setCheckState(Qt.CheckState.Checked)
        assert next(row for row in s.list_tasks() if row["id"] == today)["status"] == "done"
        assert not view.entries.rowCount()
        view.view.setCurrentIndex(view.view.findData("upcoming"))
        assert view.entries.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"] == upcoming
        view.view.setCurrentIndex(view.view.findData("completed"))
        previews = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args: previews.append((name, args)))
        view.entries.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        assert previews == [("tasks.reopen", {"id": today})]
        assert view.entries.item(0, 0).checkState() == Qt.CheckState.Checked


def test_nexus_notes_folder_and_metadata_never_truncate_editable_content(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        body = "Keep the complete Markdown document.\n" * 40
        note = window.services.save_note("Browser research", body)
        window.services.save_note("Unfiled", "Different document")
        window.services.productivity.notes.organize(note, folder="Development", tags=["browser"])
        window.navigate("Notes")
        view = window.pages["Notes"]
        assert view.note_split.count() == 3
        view.note_folders.setCurrentIndex(view.note_folders.findData("Development"))
        assert view.list.count() == 1
        view.list.setCurrentRow(0)
        assert view.note_id == note and view.body.toPlainText() == body
        assert view.note_metadata.topLevelItem(0).text(1) == "Development"
        previews = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args: previews.append((name, args)))
        view.note_action("notes.organize")
        assert previews == [("notes.organize", {"id": note})]
        view.body.setPlainText(body + "New local decision.")
        view.save()
        assert next(row for row in window.services.list_notes() if row["id"] == note)["body"] == body + "New local decision."


def test_nexus_project_workspace_reads_links_and_routes_actions_to_existing_tools(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        s = window.services
        folder = tmp_path / "project"
        folder.mkdir()
        s.add_file_root(folder)
        project = s.add_project("Jarvix", folder)
        task = s.add_task("Check tests")
        s.productivity.tasks.update(task, project_id=project)
        note = s.save_note("Release notes", "Local only")
        s.productivity.notes.organize(note, project_id=project)
        space = s.knowledge_spaces.create("Release sources", project_id=project)
        conversation = s.new_conversation("Release planning")
        s.conversations.configure(conversation, project_id=project)
        window.navigate("Projects")
        view = window.pages["Projects"]
        view.entries.selectRow(0)
        wait_until(app, lambda: not window.jobs)
        assert [view.project_tabs.tabText(index) for index in range(view.project_tabs.count())] == [
            "Overview", "Files", "Tasks", "Knowledge", "Conversations", "Activity"]
        assert view.project_name.text() == "Jarvix"
        assert view.project_views["Tasks"].item(0, 0).text() == "Check tests"
        assert any(view.project_views["Overview"].item(i, 0).text() == "Linked notes" and
                   view.project_views["Overview"].item(i, 1).text() == "1" for i in range(view.project_views["Overview"].rowCount()))
        assert view.project_views["Knowledge"].item(0, 0).data(Qt.ItemDataRole.UserRole)["id"] == space["id"]
        assert view.project_views["Conversations"].item(0, 0).data(Qt.ItemDataRole.UserRole)["id"] == conversation
        previews = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args: previews.append((name, args)))
        view.open_terminal()
        assert previews == [("workspaces.open_terminal", {"path": str(folder)})]
        view.ask_project()
        assert "Jarvix" in window.pages["Chat"].composer.toPlainText()
        assert not window.pages["Chat"].busy
        assert not s.settings.get("context.enabled", False)
        s.permissions.set_grant("projects.summary", "deny")
        window.navigate("Projects")
        wait_until(app, lambda: not window.jobs)
        assert not view.project_views["Overview"].rowCount()
        assert "denied" in view.project_state.text().casefold()


def test_nexus_mission_tabs_are_structured_and_never_invent_progress(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        s = window.services
        task = s.add_task("Verify startup")
        mission = s.missions.save("Release Nexus", task_ids=[task], milestones=[
            {"id": "package", "title": "Package x64", "status": "pending"}], next_steps=["Review screenshots"])
        window.navigate("Missions")
        view = window.pages["Missions"]
        wait_until(app, lambda: not window.jobs and view.current_mission is not None)
        assert [view.mission_sections.tabText(index) for index in range(view.mission_sections.count())] == [
            "Overview", "Milestones", "Tasks", "Resources", "History"]
        assert view.mission_progress.value() == mission["progress"]["percent"] == 0
        assert view.mission_milestones.topLevelItem(0).text(0) == "Package x64"
        assert view.mission_tasks.topLevelItem(0).text(0) == "Verify startup"
        assert "Review screenshots" in view.mission_objective.text()
        view.mission_links.setCurrentItem(view.mission_links.topLevelItem(0))
        reference = view.mission_links.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole)
        assert reference["kind"] == "task" and reference["reference"] == task


def test_nexus_home_command_and_suggestions_use_current_workspace_actions(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        view = window.pages["Home"]
        assert view.content.maximumWidth() <= 1100
        assert isinstance(view.command, QPlainTextEdit)
        assert view.command_surface.isAncestorOf(view.command)
        next(control for control in view.findChildren(QPushButton) if control.text() == "Review today's tasks").click()
        assert window.current_page == "Tasks"
        assert window.pages["Tasks"].view.currentData() == "today"
        window.navigate("Home")
        calls = []
        monkeypatch.setattr(window.controller, "open_chat", lambda text, send=True: calls.append((text, send)))
        view.command.setPlainText("Continue my work")
        view.submit()
        assert calls == [("Continue my work", True)]


def test_nexus_source_icons_follow_palette_without_changing_selected_identity(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        s = window.services
        note = s.save_note("Local source", "Keep this selected document local.")
        space = s.knowledge_spaces.create("Research")
        s.knowledge_spaces.add_source(space["id"], "note", note)
        window.navigate("Knowledge")
        view = window.pages["Knowledge"]
        wait_until(app, lambda: not window.jobs and view.sources.count() == 1)
        view.sources.setCurrentRow(0)
        source = view.sources.currentItem()
        identity = source.data(Qt.ItemDataRole.UserRole + 1)
        before = source.icon().pixmap(16, 16).toImage().constBits().tobytes()
        s.settings.set("liquid.theme", "graphite")
        window.configure_materials()
        view.refresh_materials()
        after = source.icon().pixmap(16, 16).toImage().constBits().tobytes()
        assert before != after
        assert view.sources.currentItem() is source
        assert source.data(Qt.ItemDataRole.UserRole + 1) == identity
        assert not s.settings.get("context.enabled", False)
