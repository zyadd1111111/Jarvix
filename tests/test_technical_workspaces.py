"""Technical workspaces preserve real actions while presenting structured state."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QDialog, QPlainTextEdit

from jarvix.ui.adaptive import AdaptiveDialog
from jarvix.ui.operator import OperatorDialog
from jarvix.ui.workflows import WorkflowSteps, import_definition
from test_ui import app as app, window as window, wait_until


def test_operator_console_targets_saved_steps_and_keeps_stop_visible(window, app, monkeypatch):
    services = window.services
    session = services.operator.run({"goal": "Inspect tasks", "steps": [
        {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    view = OperatorDialog(window)
    view.setWindowFlags(Qt.WindowType.Widget)
    view.open_session(session["id"])
    view.show()
    app.processEvents()
    assert view.steps.count() == 1 and not view.steps.item(0).icon().isNull()
    assert view.progress.value() == 100
    assert not view.progress.isTextVisible() and "100%" in view.status.text()
    assert view.step_inspector.topLevelItemCount() >= 3
    assert view.pause_button.isVisible() and view.resume_button.isVisible() and view.cancel_button.isVisible()
    assert view.cancel_button.text() == "Stop task" and not view.cancel_button.isEnabled()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    view.save_progress()
    assert calls == [("continuity.save", {"summary": "Inspect tasks", "operator_session_id": session["id"]})]
    assert len(services.operator.list()) == 1
    view.close()


def test_mission_workspace_uses_verified_counts_and_keeps_links_inspectable(window, app):
    services = window.services
    task = services.add_task("Verify package")
    mission = services.missions.save("Release Jarvix", task_ids=[task], milestones=[
        {"id": "review", "title": "Review release", "status": "complete"}])
    view = AdaptiveDialog(window, "Missions")
    wait_until(app, lambda: not window.jobs and view.current_mission is not None)
    assert view.mission_goal.text() == "Release Jarvix"
    assert view.mission_progress.value() == 50
    assert not view.mission_progress.isTextVisible() and "50%" in view.mission_metadata.text()
    assert "1/2" in view.mission_metadata.text()
    assert view.mission_links.topLevelItemCount() == 1
    assert view.mission_sections.tabText(0) == "Overview"
    assert view.mission_sections.tabText(1) == "Resources"
    assert view.section_selector.currentText() == "Missions"
    assert view.tabs.tabBar().isHidden()
    assert view.current_mission["id"] == mission["id"]
    services.missions.save("Release Jarvix", id=mission["id"], milestones=[
        {"id": "review", "title": "Review release", "status": "complete"},
        {"id": "package", "title": "Package release", "status": "pending"}])
    view.refresh_missions()
    wait_until(app, lambda: not window.jobs and view.mission_progress.value() == 33)
    titles = [view.mission_overview.topLevelItem(index).text(0)
              for index in range(view.mission_overview.topLevelItemCount())]
    assert titles.count("Package release") == 1
    view.close()


def test_knowledge_source_inspector_clears_when_access_is_revoked(window, app):
    services = window.services
    note = services.save_note("Browser OAuth", "Local source remains private")
    space = services.knowledge_spaces.create("Browser research")
    services.knowledge_spaces.add_source(space["id"], "note", note)
    view = AdaptiveDialog(window, "Knowledge")
    wait_until(app, lambda: not window.jobs and view.sources.count() == 1)
    assert view.sources.currentItem().data(Qt.ItemDataRole.UserRole)
    assert "Location: " + note in view.source_details.toPlainText()
    assert "Refresh sources" in view.source_details.toPlainText()
    services.permissions.set_grant("knowledge_spaces.get", "deny")
    view.select_space()
    wait_until(app, lambda: not window.jobs)
    assert not view.sources.count() and not view.source_details.toPlainText()
    view.close()


def test_skill_inspector_keeps_exact_recipe_collapsed_and_runs_only_existing_form(window, app, monkeypatch):
    services = window.services
    routine = services.workflows.save("Create project note", [{"kind": "action", "tool": "notes.create",
        "arguments": {"title": "Reviewed note", "body": "Original literal arguments"}}], kind="routine", enabled=False)
    reviewed = services.skills.learn_preview("Project note", routine_id=routine["id"])
    skill = services.skills.save("Project note", reviewed["review_fingerprint"], routine_id=routine["id"])
    view = AdaptiveDialog(window, "Skills")
    skills = view.saved_work["Skills"]
    wait_until(app, lambda: not window.jobs and "Original literal arguments" in skills.details.toPlainText())
    assert skills.inspector.raw.isHidden()
    assert skills.inspector.title.text() == "Project note"
    assert "0 recorded runs" in skills.inspector.metadata.text()
    assert skills.inspector.fields.topLevelItemCount() >= 4
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    skills.action("run")
    assert calls == [("skills.run", {"id": skill["id"]})] and not services.list_notes()
    view.close()


def test_workflow_reordering_preserves_exact_arguments_and_labels_controls(window):
    original = [{"kind": "action", "tool": "tasks.list", "arguments": {}},
                {"kind": "delay", "seconds": 2}]
    view = WorkflowSteps(window.services, original, window)
    view.blocks.setCurrentRow(1)
    view.move(-1)
    assert view.steps() == list(reversed(original))
    assert "Delay" in view.blocks.item(0).text()
    labels = {control.text() for control in view.findChildren(QPushButton)}
    assert {"Add block", "Move earlier", "Move later", "Edit block", "Remove block"} <= labels
    assert not window.services.records.list("workflow_run")
    view.close()


def test_workflow_import_parents_editor_and_requires_review_before_saving(window, monkeypatch):
    calls = []
    definition = {"name": "Reviewed import", "steps": [{"kind": "action", "tool": "tasks.list", "arguments": {}}]}
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))

    def review(dialog):
        import json
        editor = dialog.findChild(QPlainTextEdit)
        assert editor.parentWidget() is dialog
        editor.setPlainText(json.dumps(definition))
        next(control for control in dialog.findChildren(QPushButton) if control.text() == "Review import").click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", review)
    import_definition(window)
    assert calls == [("workflows.import", {"definition": definition})]
    assert not window.services.workflows.list()
