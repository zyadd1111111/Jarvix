"""Real local action dispatch, schema forms and worker lifetime boundaries."""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import check_cancelled
from jarvix.services import Services
from jarvix.ui.capabilities import CapabilityDialog, ParameterForm, PermissionDialog
from jarvix.ui.window import CommandPalette, MainWindow


class NoVault:
    def get(self, _):
        return None


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert predicate()


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


@pytest.fixture
def host(app, tmp_path):
    class Host(QMainWindow):
        closing = False

        def release_job(self, worker):
            self.jobs.discard(worker)
            worker.deleteLater()

    window = Host()
    window.services = Services(tmp_path / "profile", vault=NoVault())
    window.jobs = set()
    yield window
    for child in window.findChildren(CapabilityDialog):
        child.cancel()
    wait(app, lambda: not window.jobs)
    window.services.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_form_preserves_optional_defaults_and_64_bit_window_handles(app):
    form = ParameterForm({"type": "object", "properties": {
        "handle": {"type": "integer", "minimum": 1, "maximum": 2**64 - 1},
        "overwrite": {"type": "boolean"},
        "actions": {"type": "array", "items": {"type": "object"}},
    }, "required": ["handle"]}, {"handle": 2**40})
    assert form.arguments() == {"handle": 2**40}
    _, editor, include = form.fields["actions"]
    include.setChecked(True)
    editor.setPlainText('[{"tool": "tasks.list", "arguments": {}}]')
    assert form.arguments()["actions"][0]["tool"] == "tasks.list"
    editor.setPlainText("{invalid")
    with pytest.raises(ValueError, match="valid JSON"):
        form.arguments()
    form.deleteLater()


def test_local_read_runs_without_model_and_records_recent_command(host, app, monkeypatch):
    host.services.add_task("Check the local result")
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: pytest.fail("Read action requested confirmation"))
    dialog = CapabilityDialog(host, "tasks.list")
    dialog.run_action()
    wait(app, lambda: dialog.worker is None)
    assert "Check the local result" in dialog.result.toPlainText()
    assert host.services.settings.get("commands.recent") == ["tasks.list"]
    assert not host.services.db.query("SELECT * FROM conversations")


@pytest.mark.parametrize("accepted", [False, True])
def test_sensitive_action_confirmation_stays_on_ui_thread(host, app, monkeypatch, accepted):
    mutations, threads = [], []
    host.services.registry.register(ToolSpec("test.sensitive", "Preview this test mutation",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "local.write", "write", 3), lambda args: (mutations.append(args) or ToolResult(True)))
    host.services.settings.set("control.enabled", True)
    host.services.permissions.set_grant("test.sensitive", "allow")

    def decide(dialog):
        threads.append(QThread.currentThread())
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(PermissionDialog, "exec", decide)
    dialog = CapabilityDialog(host, "test.sensitive")
    dialog.run_action()
    wait(app, lambda: dialog.worker is None)
    assert threads == [app.thread()]
    assert mutations == ([{}] if accepted else [])


def test_cancel_stops_worker_and_close_keeps_it_owned_until_finished(host, app):
    started = threading.Event()

    def operation(_):
        started.set()
        while True:
            check_cancelled()
            time.sleep(.01)

    host.services.registry.register(ToolSpec("test.slow", "Cancellable test read",
        {"type": "object", "properties": {}, "additionalProperties": False}, permission_level=1), operation)
    dialog = CapabilityDialog(host, "test.slow")
    dialog.show()
    dialog.run_action()
    wait(app, started.is_set)
    assert host.jobs
    dialog.reject()
    wait(app, lambda: dialog.worker is None)
    assert not host.jobs
    assert "stopped" in dialog.result.toPlainText().lower()


def test_cancel_dismisses_pending_confirmation_without_executing(host, app, monkeypatch):
    mutations = []
    host.services.registry.register(ToolSpec("test.sensitive", "Test cancellation at confirmation",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "local.write", "write", 3), lambda _: (mutations.append(True) or ToolResult(True)))
    original_exec = PermissionDialog.exec
    dialog = CapabilityDialog(host, "test.sensitive")

    def cancel_when_visible(confirmation):
        QTimer.singleShot(0, dialog.cancel)
        return original_exec(confirmation)

    monkeypatch.setattr(PermissionDialog, "exec", cancel_when_visible)
    dialog.run_action()
    wait(app, lambda: dialog.worker is None)
    assert not mutations
    assert dialog.pending_dialog is None
    assert not host.jobs


def test_shell_history_palette_actions_and_persistent_state(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "refresh_system", lambda _: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda _: None)
    services = Services(tmp_path / "profile", vault=NoVault())
    first = MainWindow(services)
    first.navigate("Tasks")
    first.navigate("Files")
    first.go_history(-1)
    assert first.current_page == "Tasks"
    first.go_history(1)
    assert first.current_page == "Files"
    palette = CommandPalette(first)
    palette.search.setText("system.uptime")
    palette.execute()
    assert first.capability_dialog.selected_tool == "system.uptime"
    assert first.capability_dialog.worker is None
    first.navigate("Settings")
    first.close()
    second = MainWindow(services)
    assert second.current_page == "Settings"
    assert services.settings.get("ui.geometry")
    second.close()
    first.deleteLater()
    second.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    services.close()
