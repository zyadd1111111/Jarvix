"""Separate Nexus presentations preserve the original action and permission paths."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt

from jarvix.ui.nexus.pages import NexusFilesPage, NexusAutomationsPage, NexusAppsPage
from jarvix.ui.nexus.workspaces import NexusOperatorDialog, NexusAdaptiveDialog
from jarvix.ui.nexus.materials import GlassPanel
from test_ui import app as app, window as window, wait_until


def test_nexus_operator_keeps_verified_plan_and_existing_permission_route(window, app, monkeypatch):
    session = window.services.operator.run({"goal": "Inspect tasks", "steps": [
        {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    view = NexusOperatorDialog(window)
    view.setWindowFlags(Qt.WindowType.Widget)
    view.open_session(session["id"])
    view.show()
    app.processEvents()
    assert view.execution_split.count() == 3
    assert view.steps.count() == 1 and view.progress.value() == 100
    assert all(control.isVisible() for control in (view.pause_button, view.resume_button, view.cancel_button))
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    assert len(window.services.operator.list()) == 1
    assert not window.services.settings.get("control.enabled", False)
    view.save_progress()
    assert calls[-1] == ("continuity.save", {"summary": "Inspect tasks", "operator_session_id": session["id"]})
    view.close()


def test_nexus_knowledge_citations_clear_on_permission_revocation(window, app):
    note = window.services.save_note("OAuth", "Keep this local")
    space = window.services.knowledge_spaces.create("Local research")
    window.services.knowledge_spaces.add_source(space["id"], "note", note)
    view = NexusAdaptiveDialog(window, "Knowledge")
    wait_until(app, lambda: not window.jobs and view.sources.count() == 1)
    assert view.knowledge_split.count() == 4
    assert view.citation_inspector.topLevelItem(2).text(1) == note
    assert "Keep this local" not in view.source_details.toPlainText()
    assert "Keep this local" not in view.content_view.toPlainText()
    window.services.knowledge_spaces.refresh(space["id"])
    view.read_space_content()
    wait_until(app, lambda: not window.jobs)
    assert "Keep this local" in view.content_view.toPlainText() and "Source:" in view.content_view.toPlainText()
    window.services.permissions.set_grant("knowledge_spaces.summarize", "deny")
    view.read_space_content()
    wait_until(app, lambda: not window.jobs)
    assert "Could not read excerpts" in view.content_view.toPlainText()
    window.services.permissions.set_grant("knowledge_spaces.get", "deny")
    view.select_space()
    wait_until(app, lambda: not window.jobs)
    assert not view.sources.count() and not view.citation_inspector.topLevelItemCount()
    view.close()


def test_nexus_files_preview_uses_index_and_actions_keep_review(window, tmp_path, monkeypatch):
    folder = tmp_path / "files"
    folder.mkdir()
    document = folder / "Private.txt"
    document.write_text("Private contents are not automatically previewed", encoding="utf-8")
    window.services.add_file_root(str(folder))
    window.services.scan_files()
    page = NexusFilesPage(window)
    page.setParent(window)
    page.refresh()
    page.entries.selectRow(0)
    assert page.file_split.count() == 2
    assert page.preview_title.text() == "Private.txt"
    assert page.file_metadata.topLevelItem(0).text(1) == str(document)
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    page.file_action("recycle")
    assert calls == [("files.recycle", {"path": str(document)})] and document.exists()
    assert isinstance(page.preview, GlassPanel)
    assert not isinstance(window.pages["Files"], NexusFilesPage)


def test_nexus_workflow_preview_does_not_execute_and_app_launch_is_reviewed(window, monkeypatch):
    window.services.workflows.save("Reviewed task check", [
        {"kind": "delay", "seconds": 2},
        {"kind": "action", "tool": "tasks.list", "arguments": {}}], kind="routine", enabled=False)
    page = NexusAutomationsPage(window)
    page.setParent(window)
    page.refresh()
    page.workflow_entries.selectRow(0)
    assert page.workflow_heading.text() == "Reviewed task check"
    assert page.workflow_steps.count() == 3
    assert "Wait 2 seconds" in page.workflow_steps.item(1).text()
    assert not window.services.records.list("workflow_run")
    executable = Path(sys.executable)
    app_id = window.services.add_app("Registered Python", str(executable))
    apps = NexusAppsPage(window)
    apps.setParent(window)
    apps.refresh()
    apps.entries.selectRow(0)
    assert not apps.entries.item(0, 0).icon().isNull()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    apps.launch()
    assert calls == [("apps.open", {"id": app_id})]


def test_nexus_compact_inspectors_remain_available_without_clipping_controls(window, app):
    operator = NexusOperatorDialog(window)
    operator.setWindowFlags(Qt.WindowType.Widget)
    operator.resize(630, 480)
    operator.show()
    app.processEvents()
    assert operator.inspector_panel.isHidden()
    assert all(control.width() >= control.minimumSizeHint().width()
               for control in (operator.pause_button, operator.resume_button, operator.cancel_button))
    operator.toggle_inspector()
    assert operator.inspector_panel.isVisible() and operator.session_panel.isHidden()
    operator.toggle_inspector()
    assert operator.inspector_panel.isHidden() and operator.session_panel.isVisible()
    operator.close()
    knowledge = NexusAdaptiveDialog(window, "Knowledge")
    knowledge.setWindowFlags(Qt.WindowType.Widget)
    knowledge.resize(630, 480)
    knowledge.show()
    app.processEvents()
    assert knowledge.citation_panel.isHidden() and not knowledge.space_selector.isHidden()
    knowledge.toggle_citations()
    assert knowledge.citation_panel.isVisible()
    knowledge.close()
