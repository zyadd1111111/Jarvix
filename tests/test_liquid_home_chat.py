"""Home and Chat use the fresh shell without changing request/privacy boundaries."""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton

from jarvix.services import Services
from jarvix.ui.chat import MessageText
from jarvix.ui.interface import create_window
from jarvix.ui.nexus_frontend.chat import NexusChatPage
from jarvix.ui.nexus_frontend.home import NexusHomePage
from jarvix.ui.liquid.theme import markdown_stylesheet
from test_interface_modes import opened_interface
from test_ui import app as app, NoVault, wait_until


def test_home_commands_use_shared_worker_and_refresh_does_not_select_private_context(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        home = window.pages["Home"]
        assert isinstance(home, NexusHomePage) and home.window is window.controller
        project = tmp_path / "project"
        project.mkdir()
        window.services.add_project("Private project", str(project))
        observed = []
        monkeypatch.setattr(window.services, "chat", lambda text, *args, **kwargs: observed.append(text) or "Reviewed")
        home.refresh()
        assert not observed
        with pytest.raises(PermissionError):
            window.services.context.selection()
        assert not window.services.settings.get("context.enabled", False)
        home.command.setPlainText("Continue my project")
        home.submit()
        wait_until(app, lambda: not window.pages["Chat"].busy)
        assert observed == ["Continue my project"] and not home.command.toPlainText()
        assert window.current_page == "Chat" and home.services is window.pages["Chat"].services
        window.navigate("Home")
        chat = window.pages["Chat"]
        selected = tmp_path / "local.png"
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(selected)], "Images"))
        home.command.setPlainText("Inspect this image")
        home.attach()
        assert window.current_page == "Chat" and chat.composer.toPlainText() == "Inspect this image"
        assert chat.attachment_menu.isVisible() and not chat.attachments
        next(action for action in chat.attachment_menu.actions() if action.text() == "Attach image…").trigger()
        chat.attachment_menu.close()
        assert chat.attachments == [str(selected)] and "approval" in chat.attachment_label.text()
        assert not window.services.settings.get("control.enabled", False)


def test_chat_models_markdown_streaming_undo_and_reduced_motion_keep_handlers(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.navigate("Chat")
        chat = window.pages["Chat"]
        assert isinstance(chat, NexusChatPage) and not chat.model_menu.isVisible()
        assert chat.model_toggle.menu() is chat.model_menu
        chat.provider.setCurrentIndex(chat.provider.findData("ollama"))
        assert window.services.settings.get("provider") == "ollama"
        chat.add_message("assistant", "| Test | Result |\n| --- | --- |\n| Local | Passed |", 2)
        message = chat.messages_widget.findChildren(MessageText)[-1]
        assert message.document().defaultStyleSheet() == markdown_stylesheet(window._liquid_preferences)
        assert "<table" in message.document().toHtml()
        app.processEvents()
        assert not message.verticalScrollBar().isVisible()
        chat.on_activity("text_delta", {"text": "Partial text"})
        chat.flush_stream()
        assert chat.stream_widget.toPlainText() == "Partial text"
        assert chat.stream_widget.document().defaultStyleSheet() == markdown_stylesheet(window._liquid_preferences)
        chat.clear_stream()
        calls = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, args: calls.append((name, args)))
        chat.on_activity("tool_result", {"name": "files.move", "ok": True, "data": {"undo_id": "receipt-1"}})
        assert not chat.timeline.isVisible()
        event = chat._event_rows[-1]
        assert event.details.isHidden()
        event.heading.click()
        wait_until(app, lambda: not getattr(event.details, "_liquid_animations", {}))
        next(control for control in event.findChildren(QPushButton) if control.text() == "Undo").click()
        assert calls == [("actions.undo", {"id": "receipt-1"})]
        window.services.settings.set("liquid.reduced_motion", True)
        window.configure_materials()
        chat.inspect_context()
        assert chat.inspector.isVisible() and not getattr(chat.inspector, "_liquid_animations", {})
        chat.close_inspector()
        assert chat.inspector.isHidden()


@pytest.mark.parametrize("size", [(860, 600), (1280, 840), (2560, 1440)])
def test_home_chat_survive_deferred_cleanup_and_fit_laptop_windows(app, tmp_path, monkeypatch, size):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.resize(*size)
        for name in ("Home", "Chat"):
            window.navigate(name)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            page = window.pages[name]
            page.refresh()
            app.processEvents()
            assert not window.grab().isNull()
            position = page.command_surface.mapTo(page, QPoint())
            assert position.x() >= 0 and position.x() + page.command_surface.width() <= page.width()
            if name == "Chat":
                assert page.command_surface.isVisible() and page.composer.isVisible()
            else:
                assert page.content.width() <= page.width()


def test_home_initial_real_runtime_refresh_shows_real_sections(app, tmp_path):
    services = Services(tmp_path / "startup-profile", vault=NoVault())
    services.settings.set("ui.interface", "new")
    window = create_window(services)
    window.navigate("Home")
    window.show()
    try:
        wait_until(app, lambda: "cpu_percent" in window.snapshot, timeout=20)
        home = window.pages["Home"]
        home.refresh()
        app.processEvents()
        visible = [control.text() for control in home.findChildren(QLabel) if control.isVisible()]
        assert {"Continue", "Today", "Calendar & notifications", "On this PC", "Recent"}.issubset(visible)
        assert any(text.startswith("CPU ") and "Memory" in text for text in visible)
        assert any("Not connected" in text for text in visible)
        assert not services.settings.get("context.enabled", False)
    finally:
        window.close()
        wait_until(app, lambda: not window.isVisible() and not services.background.running, timeout=20)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        services.close()
