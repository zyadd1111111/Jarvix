"""Separate Nexus presentations retain the shared application routes and boundaries."""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QPushButton

from jarvix.services import Services
from jarvix.ui.chat import MessageText
from jarvix.ui.window import MainWindow
from jarvix.ui.nexus.window import NexusWindow
from jarvix.ui.nexus.theme import markdown_stylesheet

from test_ui import app as app, NoVault, wait_until


@pytest.fixture
def nexus(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "refresh_system", lambda self: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda self: None)
    services = Services(tmp_path / "profile", vault=NoVault())
    window = NexusWindow(services)
    window.show()
    app.processEvents()
    yield window
    window.pages["Chat"].cancel()
    wait_until(app, lambda: not window.jobs and not window.pages["Chat"].busy)
    window.close()
    wait_until(app, lambda: not window.isVisible() and not services.background.running, timeout=10)
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    services.close()


def test_nexus_home_shares_routes_and_does_not_imply_current_context(nexus, tmp_path, monkeypatch):
    home = nexus.pages["Home"]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    home.refresh()
    assert home.greeting.text() and home.date_label.text()
    calls = []
    monkeypatch.setattr(nexus, "open_chat", lambda text, send=True: calls.append((text, send)))
    home.command.setText("Continue my project")
    home.submit()
    assert calls == [("Continue my project", True)] and not home.command.text()
    root = tmp_path / "project"
    root.mkdir()
    nexus.services.add_project("Recently registered", str(root))
    home.refresh()
    assert "Recently registered" not in home.context_label.text()
    previews = []
    monkeypatch.setattr(nexus, "open_capabilities", lambda name, args: previews.append((name, args)))
    document = root / "notes.txt"
    home.open_dropped_files([str(document)])
    assert previews == [("files.inspect", {"path": str(document)})]
    home.open_calendar()
    assert previews[-1][0] == "google_calendar.events"
    assert set(previews[-1][1]) == {"start", "end"}
    assert not nexus.services.settings.get("control.enabled", False)


def test_nexus_chat_keeps_stream_styles_attachments_and_real_undo(nexus, app, tmp_path, monkeypatch):
    nexus.navigate("Chat")
    chat = nexus.pages["Chat"]
    assert chat.model_settings.isHidden()
    chat.model_toggle.click()
    assert not chat.model_settings.isHidden()
    chat.model_toggle.click()
    chat.add_message("assistant", "| Check | State |\n| --- | --- |\n| Tests | Pending |\n\n```python\nprint('local')\n```", 3)
    message = chat.messages_widget.findChildren(MessageText)[-1]
    assert message.document().defaultStyleSheet() == markdown_stylesheet()
    assert "<table" in message.document().toHtml()
    chat.on_activity("text_delta", {"text": "A streamed response"})
    chat.flush_stream()
    assert chat.stream_widget.document().defaultStyleSheet() == markdown_stylesheet()
    chat.clear_stream()
    image = tmp_path / "capture.png"
    nexus.pages["Home"].open_dropped_files([str(image)])
    assert chat.attachments == [str(image)] and "approval" in chat.attachment_label.text()
    assert not chat.busy
    chat.capture_active()
    assert nexus.isVisible(), "Screen access remains off without permission."
    calls = []
    monkeypatch.setattr(nexus, "open_capabilities", lambda name, args: calls.append((name, args)))
    chat.on_activity("tool_result", {"name": "files.move", "ok": True, "data": {"undo_id": "receipt-9"}})
    card = chat.timeline.itemWidget(chat.timeline.item(0))
    next(control for control in card.findChildren(QPushButton) if control.text() == "Undo").click()
    assert calls == [("actions.undo", {"id": "receipt-9"})]


@pytest.mark.parametrize("size", [(860, 600), (1280, 840)])
def test_nexus_home_and_chat_fit_small_windows(nexus, app, size):
    nexus.resize(*size)
    for name in ("Home", "Chat"):
        nexus.navigate(name)
        app.processEvents()
        page = nexus.pages[name]
        assert not nexus.grab().isNull()
        for control in (page.command_surface,):
            assert control.width() <= page.width()
            assert control.geometry().right() < page.width()
        if name == "Home":
            assert page.scroll_body.width() <= page.width()
        else:
            assert not page.history_pane.isVisible() if page.width() < 720 else page.history_pane.isVisible()
            assert page.transcript.viewport().width() > 180


def test_nexus_long_resource_names_remain_accessible_without_horizontal_overflow(nexus, app):
    goal = ("Verify " + "a very detailed release goal " * 12).strip()
    nexus.services.missions.save(goal)
    nexus.resize(860, 600)
    page = nexus.pages["Home"]
    page.refresh()
    app.processEvents()
    assert page.scroll_body.width() <= page.width()
    control = next(item for item in page.mission_panel.findChildren(QPushButton) if item.accessibleName() == goal)
    assert control.toolTip() == goal
    assert control.text() != goal
