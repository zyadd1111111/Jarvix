"""Interface choice changes presentation, never services or live work."""
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from jarvix.domain import PermissionRequest
from jarvix.services import Services
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.interface import InterfaceMode, create_window, selected_interface
from jarvix.ui.theme import STYLESHEET
from jarvix.ui.window import MainWindow
from test_ui import NoVault, app as app, window as window, wait_until


@contextmanager
def opened_interface(app, monkeypatch, profile, choice=None):
    monkeypatch.setattr(MainWindow, "refresh_system", lambda self: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda self: None)
    services = Services(profile, vault=NoVault())
    if choice is not None:
        services.settings.set("ui.interface", choice)
    try:
        instance = create_window(services)
    except Exception:
        services.close()
        raise
    assert instance.services is services
    instance.show()
    app.processEvents()
    try:
        yield instance
    finally:
        instance.pages["Chat"].cancel()
        wait_until(app, lambda: not instance.jobs and not instance.pages["Chat"].busy)
        instance.close()
        wait_until(app, lambda: not instance.isVisible() and not services.background.running, timeout=10)
        instance.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        services.close()


@pytest.mark.parametrize("choice,expected", [(None, "legacy"), ("obsolete-interface", "legacy"),
                                            (37, "legacy"), ("nexus", "nexus")])
def test_factory_defaults_and_invalid_fallback_keep_the_supplied_services(app, tmp_path, monkeypatch, choice, expected):
    with opened_interface(app, monkeypatch, tmp_path / "profile", choice) as instance:
        assert instance.interface_mode == expected
        assert selected_interface(instance.services.settings) is InterfaceMode(expected)
        assert instance.pages["Chat"].services is instance.services
        assert instance.pages["Notes"].services is instance.services
        assert not instance.services.settings.get("control.enabled", False)
        assert not instance.services.settings.get("screenshots.enabled", False)
        if expected == "legacy":
            assert type(instance) is MainWindow
            assert instance.styleSheet() == STYLESHEET


def test_next_launch_selector_preserves_drafts_attachments_and_pending_confirmation(window, app, tmp_path):
    services = window.services
    chat, notes, settings = (window.pages[name] for name in ("Chat", "Notes", "Settings"))
    chat.composer.setPlainText("Keep this unsent message")
    attachment = tmp_path / "local-screen.png"
    chat.add_images([str(attachment)])
    notes.title_edit.setText("Unsaved work")
    notes.body.setPlainText("Keep this local draft")
    request = PermissionRequest("execute", "files.recycle", "filesystem.write", "Recycle one file",
                                {"path": str(tmp_path / "report.txt")})
    permission = PermissionDialog(request, window)
    permission.show()
    before = {key: services.settings.get(key, False) for key in settings.access_checks}
    pending_page = window.current_page
    settings.interface_selector.setCurrentIndex(settings.interface_selector.findData("nexus"))
    app.processEvents()
    assert services.settings.get("ui.interface") == "nexus"
    assert window.interface_mode == "legacy" and window.services is services
    assert window.pages["Chat"] is chat and window.pages["Notes"] is notes
    assert window.current_page == pending_page and not window.closing
    assert chat.composer.toPlainText() == "Keep this unsent message"
    assert chat.attachments == [str(attachment)] and not chat.busy
    assert notes.dirty and notes.body.toPlainText() == "Keep this local draft"
    assert {key: services.settings.get(key, False) for key in settings.access_checks} == before
    assert permission.isVisible() and permission.result() == QDialog.DialogCode.Rejected
    QTest.keyClick(permission, Qt.Key.Key_Escape)
    assert permission.result() == QDialog.DialogCode.Rejected and not permission.isVisible()
    permission.deleteLater()
    notes.dirty = False


def test_saved_interface_is_used_after_profile_reopens(app, tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    with opened_interface(app, monkeypatch, profile) as legacy:
        selector = legacy.pages["Settings"].interface_selector
        selector.setCurrentIndex(selector.findData("nexus"))
        assert legacy.interface_mode == "legacy"
    with opened_interface(app, monkeypatch, profile) as nexus:
        assert nexus.interface_mode == "nexus"
        selector = nexus.pages["Settings"].interface_selector
        selector.setCurrentIndex(selector.findData("legacy"))
        assert nexus.interface_mode == "nexus"
    with opened_interface(app, monkeypatch, profile) as legacy:
        assert type(legacy) is MainWindow and legacy.styleSheet() == STYLESHEET


def test_nexus_pages_keep_controller_widgets_after_deferred_gui_cleanup(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "nexus") as nexus:
        for name in ("Home", "Chat", "Operator", "Missions", "Knowledge", "Files",
                     "Automations", "Integrations", "System", "Settings"):
            nexus.navigate(name)
            wait_until(app, lambda: not nexus.jobs)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            nexus.pages[name].refresh()
            wait_until(app, lambda: not nexus.jobs)
            assert nexus.current_page == name and not nexus.grab().isNull()


def test_nexus_permission_sheet_keeps_deny_default_and_escape_denial(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "nexus") as nexus:
        request = PermissionRequest("disclose", "context.share", "context.disclose", "Share selected local context",
                                    {"text": "Selected content"}, preview="Only this selected content")
        dialog = PermissionDialog(request, nexus)
        dialog.show()
        app.processEvents()
        assert dialog.deny_button.isDefault() and not dialog.allow_button.isDefault()
        assert dialog.allow_button.text() == "Share once"
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == QDialog.DialogCode.Rejected and not dialog.isVisible()
        assert not nexus.services.settings.get("control.enabled", False)
        dialog.deleteLater()


def test_nexus_styles_are_window_scoped_and_leave_legacy_unchanged(app, tmp_path, monkeypatch):
    application_style = QApplication.instance().styleSheet()
    with opened_interface(app, monkeypatch, tmp_path / "legacy") as legacy:
        old_style = legacy.styleSheet()
        old_colors = legacy.palette()
        with opened_interface(app, monkeypatch, tmp_path / "nexus", "nexus") as nexus:
            assert nexus.styleSheet() != old_style
            assert QApplication.instance().styleSheet() == application_style
            assert legacy.styleSheet() == old_style == STYLESHEET
            assert legacy.palette() == old_colors
