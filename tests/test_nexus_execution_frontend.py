"""Fresh presentation surfaces retain permissioned controllers and verified local data."""
import pytest
import threading
from PySide6.QtCore import Qt, QCoreApplication, QEvent

from jarvix.ui.nexus_frontend.workspaces import NexusAdaptiveWorkspace, NexusOperatorWorkspace
from test_ui import app as app, window as window, wait_until


@pytest.fixture(autouse=True)
def material_host(window, monkeypatch):
    # Standalone presenters need the same material ancestor as the real Nexus shell.
    monkeypatch.setattr(window, "_liquid_preferences", {"theme": "cream", "motion": False}, raising=False)


def close_view(view, app, window):
    if isinstance(view, NexusOperatorWorkspace):
        view.shutdown()
    view.close()
    wait_until(app, lambda: not window.jobs)
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_blank_operator_console_keeps_original_pause_and_permission_methods(window, app, monkeypatch):
    session = window.services.operator.run({"goal": "Read saved tasks", "steps": [
        {"id": "tasks", "tool": "tasks.list", "arguments": {}}]}, approve=lambda _: True)
    view = NexusOperatorWorkspace(window)
    view.show()
    app.processEvents()
    assert view.session_picker.currentData() == session["id"]
    assert view.goal.text() == "Read saved tasks"
    assert view.progress.value() == 100
    assert view.steps.count() == 1 and view.steps.item(0).data(Qt.ItemDataRole.UserRole)["tool"] == "tasks.list"
    assert view.sessions.isHidden()
    assert view.inspector.isHidden()
    assert not view.pause_button.isEnabled() and not view.cancel_button.isEnabled()
    view.show_inspector()
    assert view.permissions_view.topLevelItem(0).text(0) == "tasks.list"
    assert view.permissions_view.topLevelItem(0).text(2) == "1"
    guarded = []
    original_guard = window.guard
    monkeypatch.setattr(window, "guard", lambda operation: guarded.append(operation))
    view.pause()
    assert len(guarded) == 1
    assert not window.services.settings.get("control.enabled", False)
    monkeypatch.setattr(window, "guard", original_guard)
    close_view(view, app, window)


def test_mission_workspace_uses_saved_progress_and_original_action_preview(window, app, monkeypatch):
    task = window.services.add_task("Verify package startup")
    mission = window.services.missions.save("Prepare release", task_ids=[task], milestones=[
        {"id": "package", "title": "Build package", "status": "pending"}], next_steps=["Review screenshots"])
    view = NexusAdaptiveWorkspace(window, "Missions")
    view.show()
    wait_until(app, lambda: not window.jobs and view.current_mission is not None)
    assert view.mission_picker.currentData() == mission["id"]
    assert view.missions.isHidden()
    assert view.mission_progress.value() == mission["progress"]["percent"] == 0
    assert [view.mission_sections.tabText(index) for index in range(view.mission_sections.count())] == [
        "Overview", "Milestones", "Tasks", "Resources", "History"]
    assert view.mission_milestones.topLevelItem(0).text(0) == "Build package"
    assert view.mission_tasks.topLevelItem(0).text(0) == "Verify package startup"
    assert "Review screenshots" in view.mission_objective.text()
    previews = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args: previews.append((name, args)))
    view.mission_actions["pause"].click()
    assert previews == [("missions.pause", {"id": mission["id"]})]
    assert window.services.missions.get(mission["id"])["status"] == "active"
    close_view(view, app, window)


def test_research_layout_shows_citations_without_auto_reading_source_content(window, app, monkeypatch):
    note = window.services.save_note("Browser restart", "OAuth reconnect should preserve existing permissions.")
    space = window.services.knowledge_spaces.create("Browser research")
    window.services.knowledge_spaces.add_source(space["id"], "note", note)
    view = NexusAdaptiveWorkspace(window, "Knowledge")
    view.show()
    wait_until(app, lambda: not window.jobs and view.sources.count() == 1)
    assert view.knowledge_split.count() == 3
    assert view.spaces.parentWidget() is view.sources.parentWidget()
    assert view.document_view.toPlainText() == ""
    assert view.source_properties.topLevelItem(2).text(1) == note
    previews = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args: previews.append((name, args)))
    view.ask_space_button.click()
    assert previews == [("knowledge_spaces.question", {"id": space["id"]})]
    assert not window.services.settings.get("context.enabled", False)
    window.services.permissions.set_grant("knowledge_spaces.search", "deny")
    view.space_query.setText("OAuth")
    view.search_space()
    wait_until(app, lambda: not window.jobs)
    assert "denied" in view.knowledge_status.text().casefold()
    assert view.space_results.count() == 0
    close_view(view, app, window)


def test_closed_research_workspace_discards_pending_deliveries(window, app, monkeypatch):
    callbacks = []
    monkeypatch.setattr(window, "run_job", lambda work, done, failed: callbacks.append(done))
    view = NexusAdaptiveWorkspace(window, "Knowledge")
    view.close()
    assert view._closed
    callbacks[0]({"items": [{"id": "late", "name": "Late result", "sources": 0}]})
    assert view.spaces.count() == 0
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_skill_library_keeps_reviewed_recipe_and_routes_run_to_permission_form(window, app, monkeypatch):
    routine = window.services.workflows.save("Review local tasks", [
        {"kind": "action", "tool": "tasks.list", "arguments": {}}], kind="routine", enabled=False)
    args = {"name": "Release review", "routine_id": routine["id"], "description": "Inspect tasks before packaging."}
    preview = window.services.execute_tool("skills.learn_preview", args)
    assert preview.ok, preview.error
    saved = window.services.execute_tool("skills.save", {**args, "review_fingerprint": preview.data["review_fingerprint"]},
                                         approve=lambda request: request.tool_name == "skills.save")
    assert saved.ok, saved.error
    view = NexusAdaptiveWorkspace(window, "Skills")
    view.show()
    library = view.saved_work["Skills"]
    wait_until(app, lambda: not window.jobs and library.items.count() == 1)
    assert library.selected()["description"] == args["description"]
    assert library.inspector.title.text() == "Release review"
    assert "tasks.list" in library.inspector.raw.toPlainText()
    previews = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args: previews.append((name, args)))
    library.controls[0].click()
    assert previews == [("skills.run", {"id": saved.data["id"]})]
    assert window.services.skills.get(saved.data["id"])["usage"]["recorded_runs"] == 0
    close_view(view, app, window)


def test_live_console_motion_and_stop_follow_real_operator_state(window, app, monkeypatch):
    from jarvix.domain import ToolSpec, ToolResult
    from jarvix.runtime import check_cancelled
    from jarvix.ui.chat import PermissionDialog
    from jarvix.ui.liquid.motion import Transition
    entered, released = threading.Event(), threading.Event()
    def read(_args):
        entered.set()
        while not released.wait(.01):
            check_cancelled()
        return ToolResult(True, {"read": True})
    window.services.registry.register(ToolSpec("test.nexus_read", "Controlled read", {
        "type": "object", "properties": {}, "additionalProperties": False},
        risk="read", permission_level=1), read)
    monkeypatch.setattr(window, "interface_mode", "new")
    window._liquid_preferences["motion"] = True
    monkeypatch.setattr(PermissionDialog, "exec", lambda _dialog: PermissionDialog.DialogCode.Accepted)
    view = NexusOperatorWorkspace(window)
    view.show()
    app.processEvents()
    plan = {"goal": "Observe a cancellable local read", "steps": [
        {"id": "read", "tool": "test.nexus_read", "arguments": {}}]}
    try:
        view.start(lambda **kwargs: window.services.operator.run(plan, **kwargs))
        wait_until(app, lambda: entered.is_set() and view.session_id is not None)
        view.refresh()
        assert view.steps.item(0).data(Qt.ItemDataRole.UserRole)["status"] == "running"
        assert view.steps.viewport().findChildren(Transition)
        view.pause_button.click()
        wait_until(app, lambda: window.services.operator.get(view.session_id)["status"] == "paused")
        view.refresh()
        assert view.resume_button.isEnabled()
        assert view.current_action.text().startswith("Paused")
        assert view.steps.property("operatorPaused")
        view.resume_button.click()
        view.cancel_button.click()
        released.set()
        wait_until(app, lambda: view.worker is None)
        assert window.services.operator.get(view.session_id)["status"] == "cancelled"
        assert not view.cancel_button.isEnabled()
        assert view.current_action.text() == "Execution stopped"
    finally:
        released.set()
        close_view(view, app, window)
