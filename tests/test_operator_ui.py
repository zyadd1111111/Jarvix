"""UI boundaries for the real operator, workflow builder and owned background work."""
import json
import threading
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton

from jarvix.domain import PermissionRequest
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.overlay import WorkflowHotkeys
from jarvix.ui.pages import pretty_date
from jarvix.ui.workflows import WorkflowBlockDialog, WorkflowBuilder, WorkflowHistory
from test_ui import app as app, wait_until, window as window


def test_execution_confirmation_displays_exact_action_preview(window):
    request = PermissionRequest("execute", "operator.run", "computer.control", "Review this plan",
                                {"plan": {}}, "Open folder\nMove three files\nDo not overwrite")
    dialog = PermissionDialog(request, window)
    assert dialog.findChild(QPlainTextEdit).toPlainText() == request.preview
    assert dialog.deny_button.isDefault()
    assert not dialog.allow_button.isDefault()
    dialog.reject()


def test_operator_ui_executes_and_displays_persisted_session(window, app, monkeypatch):
    previews = []

    def approve(dialog):
        previews.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PermissionDialog, "exec", approve)
    window.open_operator()
    dialog = window.operator_dialog
    plan = {"goal": "Review open tasks", "steps": [{"id": "tasks", "tool": "tasks.list", "arguments": {}}]}
    dialog.start(lambda **kwargs: window.services.operator.run(plan, **kwargs))
    wait_until(app, lambda: dialog.worker is None)
    session = window.services.operator.get(dialog.session_id)
    assert session["status"] == "complete"
    assert "Review open tasks" in previews[0]
    assert dialog.steps.count() == 1
    assert "1/1" in dialog.status.text()
    assert not dialog.undo_button.isEnabled()
    assert not dialog.cancel_button.isEnabled()


def test_control_hud_requires_real_display_and_cannot_take_keyboard_focus(window, app):
    before = app.activeWindow()
    acknowledged = window.desktop_indicator({"state": "running", "action": "Click Save", "handle": 123})
    hud = window.desktop_indicator.hud
    assert not acknowledged  # The offscreen backend cannot authorize native computer control.
    assert hud.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert hud.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert app.activeWindow() is before
    assert window.desktop_indicator({"state": "idle"})
    assert not hud.isVisible()


def test_overlay_snapshots_once_without_automatic_disclosure(window, app, monkeypatch):
    captured, sent = [], []
    snapshot = {"active_window": {"title": "Private editor", "handle": 12}, "automatic_cloud_sharing": False}
    window.services.settings.set("context.enabled", True)
    monkeypatch.setattr(window.services.context, "inspect", lambda: captured.append(True) or snapshot)
    monkeypatch.setattr(window, "open_chat", lambda text: sent.append(text))
    overlay = window.overlay
    overlay.toggle()
    wait_until(app, lambda: not window.jobs)
    assert captured == [True]
    assert overlay.context_snapshot == snapshot
    assert not overlay.share_context.isChecked()
    overlay.command.setText("Open my tasks")
    overlay.submit()
    assert sent == ["Open my tasks"]


def test_overlay_context_sharing_requires_fresh_confirmation(window, monkeypatch):
    sent, inspected = [], []
    overlay = window.overlay
    window.services.settings.set("context.enabled", True)
    overlay.context_snapshot = {"active_window": {"title": "Compiler error", "handle": 12}}
    overlay.share_context.setEnabled(True)
    overlay.share_context.setChecked(True)
    overlay.command.setText("Explain this")
    monkeypatch.setattr(window, "open_chat", lambda text: sent.append(text))
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    overlay.submit()
    assert not sent
    assert overlay.command.text() == "Explain this"

    def accept(dialog):
        inspected.append(json.loads(dialog.findChild(QPlainTextEdit).toPlainText()))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PermissionDialog, "exec", accept)
    overlay.submit()
    assert inspected == [overlay.context_snapshot]
    assert "Compiler error" in sent[0]


def test_workflow_builder_reorders_validates_tests_and_saves_real_definition(window, app, monkeypatch):
    builder = WorkflowBuilder(window)
    builder.name.setText("After school")
    builder.trigger.setCurrentText("hotkey")
    builder.hotkey.setCurrentText("Ctrl+Shift+F8")
    builder.steps.append({"kind": "action", "tool": "tasks.list", "arguments": {}})
    builder.steps.append({"kind": "delay", "seconds": 0})
    builder.steps.blocks.setCurrentRow(1)
    builder.steps.move(-1)
    assert builder.values()["steps"][0]["kind"] == "delay"
    assert builder.values()["config"] == {"shortcut": "Ctrl+Shift+F8"}
    preview = window.services.workflows.preview_definition(**builder.values())
    assert preview["actions"] == [{"tool": "tasks.list", "arguments": {}, "permission_level": 1}]
    builder.test()
    wait_until(app, lambda: not window.jobs)
    assert "no actions executed" in builder.status.text()
    assert not window.services.workflows.list()
    previews = []

    def accept(dialog):
        previews.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PermissionDialog, "exec", accept)
    builder.save()
    wait_until(app, lambda: builder.worker is None)
    saved = window.services.workflows.get(builder.definition["id"])
    assert saved["config"] == {"shortcut": "Ctrl+Shift+F8"}
    assert saved["steps"][0]["kind"] == "delay"
    assert "After school" in previews[0]
    assert builder.save_button.isEnabled()
    exported = window.services.execute_tool("workflows.export", {"id": saved["id"]})
    window.open_capabilities("workflows.import", {"definition": exported.data})
    window.capability_dialog.run_action()
    wait_until(app, lambda: window.capability_dialog.worker is None)
    imported = json.loads(window.capability_dialog.result.toPlainText())
    assert imported["ok"]
    assert len(window.services.workflows.list()) == 2
    builder.reject()


def test_workflow_builder_denial_and_cancel_never_save(window, app, monkeypatch):
    builder = WorkflowBuilder(window, {"name": "Unapproved", "steps": [
        {"kind": "action", "tool": "tasks.list", "arguments": {}}]})
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    builder.save()
    wait_until(app, lambda: builder.worker is None)
    assert not window.services.workflows.list()
    assert not builder.definition.get("id")
    builder.reject()


def test_workflow_controls_remain_available_while_action_runner_is_busy(window, app, monkeypatch):
    definition = window.services.workflows.save("Controlled delay", [
        {"kind": "delay", "seconds": 20}, {"kind": "action", "tool": "tasks.list", "arguments": {}}])
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    window.open_capabilities("workflows.run", {"id": definition["id"]})
    runner = window.capability_dialog
    runner.run_action()
    wait_until(app, lambda: bool(window.services.workflows.history(definition["id"])))
    history = WorkflowHistory(window, definition["id"])
    history.show()
    run_id = history.runs.currentItem().data(Qt.ItemDataRole.UserRole)["id"]

    def state():
        return next(row for row in window.services.workflows.history(definition["id"]) if row["id"] == run_id)["status"]

    try:
        history.controls["pause"].click()
        wait_until(app, lambda: state() == "paused")
        history.refresh()
        assert history.controls["resume"].isEnabled()
        assert runner.worker is not None
        assert runner.selected_tool == "workflows.run"
        history.controls["resume"].click()
        wait_until(app, lambda: state() == "running")
        history.refresh()
        history.controls["cancel"].click()
        wait_until(app, lambda: runner.worker is None)
        history.refresh()
        assert state() == "cancelled"
        assert not history.controls["pause"].isEnabled()
        assert not history.controls["cancel"].isEnabled()
    finally:
        runner.cancel()
        history.reject()


def test_workflow_block_picker_excludes_unsupported_actions_and_unsafe_retries(window):
    dialog = WorkflowBlockDialog(window.services, {"kind": "action", "tool": "tasks.list", "arguments": {}})
    choices = [dialog.tool.itemData(index) for index in range(dialog.tool.count())]
    assert not any(name.startswith("operator.") for name in choices)
    assert "workflows.save" not in choices and "routines.save" not in choices
    assert "workflows.run" in choices and "routines.run" in choices
    assert dialog.retries.isEnabled()
    dialog.retries.setValue(1)
    dialog.tool.setCurrentIndex(dialog.tool.findData("notes.create"))
    assert not dialog.retries.isEnabled()
    assert dialog.retries.value() == 0
    dialog.reject()


def test_automation_page_displays_calculated_next_run(window):
    saved = window.services.workflows.save("Weekday tasks", [
        {"kind": "action", "tool": "tasks.list", "arguments": {}}],
        trigger="schedule", config={"time": "16:00", "weekdays": [0, 1, 2, 3, 4]}, enabled=True)
    page = window.pages["Automations"]
    page.refresh_workflows()
    next_run = window.services.workflows.get(saved["id"])["next_run"]
    assert next_run
    assert page.workflow_entries.item(0, 3).text() == pretty_date(next_run)


def test_home_favorite_app_launch_uses_registered_permission_runner(window, tmp_path):
    executable = tmp_path / "favorite.exe"
    executable.write_bytes(b"test-only executable fixture")
    executable.chmod(0o755)
    app_id = window.services.add_app("Favorite editor", str(executable))
    window.services.apps.configure(app_id, favorite=True)
    home = window.pages["Home"]
    home.refresh()
    launch = next(control for control in home.favorites_panel.findChildren(QPushButton)
                  if control.text() == "Favorite editor")
    launch.click()
    assert window.capability_dialog.selected_tool == "apps.open"
    assert window.capability_dialog.form.arguments() == {"id": app_id}
    assert window.capability_dialog.worker is None


def test_home_today_tasks_exclude_other_days_and_completed_tasks(window):
    today = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    services = window.services
    services.add_task("Due today", today.isoformat())
    services.add_task("Tomorrow", (today + timedelta(days=1)).isoformat())
    services.add_task("Yesterday", (today - timedelta(days=1)).isoformat())
    services.add_task("No due date")
    completed = services.add_task("Already done", today.isoformat())
    services.complete_task(completed)
    home = window.pages["Home"]
    home.refresh()
    texts = [item.text() for index in range(home.tasks_layout.count())
             if isinstance(item := home.tasks_layout.itemAt(index).widget(), QLabel)]
    assert "○  Due today" in texts
    assert not any(title in "\n".join(texts) for title in ("Tomorrow", "Yesterday", "No due date", "Already done"))


def test_workflow_hotkey_uses_supported_shortcut_and_releases_disabled_bindings(monkeypatch):
    calls = []

    class Binding:
        def __init__(self, callback, identifier, choices):
            self.callback, self.choices, self.registered = callback, choices, False

        def configure(self, enabled, sequence):
            assert sequence in self.choices
            self.registered = enabled
            calls.append(sequence)
            return "ready"

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("jarvix.ui.overlay.GlobalHotkey", Binding)
    pressed = []
    manager = WorkflowHotkeys(pressed.append)
    records = [{"trigger": "hotkey", "enabled": True, "config": {"shortcut": "Ctrl+Alt+F4"}}]
    assert not manager.configure(records)
    manager.bindings["Ctrl+Alt+F4"].callback()
    assert pressed == ["Ctrl+Alt+F4"]
    manager.configure(records)
    assert calls == ["Ctrl+Alt+F4"]
    manager.configure(records, enabled=False)
    assert calls == ["Ctrl+Alt+F4", "closed"]
    assert not manager.bindings


def test_workspace_editor_preserves_members_and_routes_actions_through_permissions(window):
    saved = window.services.workspaces.save("Coding")
    window.open_workspaces()
    dialog = window.workspace_dialog
    dialog.entries.selectRow(0)
    dialog.action("save")
    args = window.capability_dialog.form.arguments()
    assert args["id"] == saved["id"]
    assert args["name"] == "Coding"
    assert "created_at" not in args
    assert args["window_layouts"] == []
    dialog.action("close")
    assert window.capability_dialog.selected_tool == "workspaces.close"
    assert window.capability_dialog.worker is None
    assert window.capability_dialog.form.arguments() == {"id": saved["id"]}


def test_action_card_only_offers_actual_undo_and_reveal(window):
    chat = window.pages["Chat"]
    chat.on_activity("tool_result", {"name": "files.move", "ok": True, "data": {
        "path": "C:/Approved/Reports/report.txt", "operation_id": "saved-operation", "undo_available": True}})
    card = chat.timeline.itemWidget(chat.timeline.item(0))
    controls = {control.text(): control for control in card.findChildren(QPushButton)}
    controls["Undo"].click()
    assert window.capability_dialog.selected_tool == "files.undo"
    assert window.capability_dialog.form.arguments() == {"operation_id": "saved-operation"}
    controls["Reveal"].click()
    assert window.capability_dialog.selected_tool == "files.reveal"


def test_background_lives_while_hidden_and_shutdown_waits_without_blocking(window, app, monkeypatch):
    background = window.services.background
    assert background.running
    window.hide()
    assert background.running
    window.show()
    assert background.stop()
    entered, release = threading.Event(), threading.Event()

    def tick():
        entered.set()
        release.wait(3)

    monkeypatch.setattr(background, "tick_once", tick)
    background.start()
    wait_until(app, entered.is_set)
    try:
        assert window.close() is False
        assert window.closing and window.shutdown_timer.isActive()
        assert background.running
    finally:
        release.set()
    wait_until(app, lambda: not window.isVisible() and not background.running)
    assert not window.shutdown_timer.isActive()

