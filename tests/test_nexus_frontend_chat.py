"""Fresh Nexus layout retains the shared conversation and security boundary."""
import threading
from contextlib import contextmanager

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton, QSplitter

from jarvix.services import Services
from jarvix.ui.chat import MessageText
from jarvix.ui.nexus_frontend.chat import NexusChatPage, NexusToolEvent
from jarvix.ui.nexus_frontend.design import TOKENS, stylesheet
from jarvix.ui.liquid.theme import markdown_stylesheet
from jarvix.ui.window import MainWindow
from test_ui import NoVault, app as app, wait_until


@contextmanager
def opened_chat(app, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "refresh_system", lambda self: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda self: None)
    class Host(MainWindow):
        page_constructors = {"Chat": NexusChatPage}
        interface_mode = "new"
        _liquid_preferences = {"theme": "cream", "motion": False}
        interface_stylesheet = stylesheet(_liquid_preferences)
    services = Services(tmp_path / "profile", vault=NoVault())
    host = Host(services)
    host.show()
    host.navigate("Chat")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    try:
        yield host, host.pages["Chat"]
    finally:
        host.pages["Chat"].cancel()
        wait_until(app, lambda: not host.jobs and not host.pages["Chat"].busy)
        host.close()
        wait_until(app, lambda: not host.isVisible() and not services.background.running, timeout=10)
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        services.close()


def test_empty_chat_centers_single_composer_then_moves_it_to_document_bottom(app, tmp_path, monkeypatch):
    with opened_chat(app, tmp_path, monkeypatch) as (window, chat):
        assert chat.state_stack.currentWidget() is chat.empty
        assert chat.empty_composer_slot.indexOf(chat.command_surface) == 0
        assert chat.active_composer_slot.indexOf(chat.command_surface) == -1
        assert chat._keeper.isHidden() and not chat.history.isVisible()
        assert not chat.findChild(QSplitter, "ChatSplit")
        assert not chat.findChild(QSplitter, "LiquidChatSplit")
        assert chat.inspector.isHidden() and chat.model_toggle.menu() is chat.model_menu
        assert chat.empty_heading.text() == "What can I help with?"
        chat.add_message("user", "Inspect the current project")
        chat.add_message("assistant", "Three changed files are ready to review.")
        assert chat.state_stack.currentWidget() is chat.active
        assert chat.active_composer_slot.indexOf(chat.command_surface) == 0
        assert chat.empty_composer_slot.indexOf(chat.command_surface) == -1
        for width, height in ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440)):
            window.resize(width, height)
            app.processEvents()
            assert chat.command_surface.width() <= TOKENS["conversation_width"]
            assert chat.send_button.isVisible() and chat.composer.isVisible()
            assert chat.messages_widget.width() <= TOKENS["conversation_width"]
        chat.new_conversation()
        assert chat.state_stack.currentWidget() is chat.empty and chat.empty_composer_slot.indexOf(chat.command_surface) == 0


def test_shared_streaming_worker_stop_and_attachment_disclosure_inputs(app, tmp_path, monkeypatch):
    with opened_chat(app, tmp_path, monkeypatch) as (window, chat):
        started, observed = threading.Event(), []
        def respond(text, conversation, provider, model, *, approve, on_event, cancel, attachments):
            observed.append((text, attachments))
            on_event("stream_start", {})
            on_event("text_delta", {"text": "Inspecting local image"})
            started.set()
            assert cancel.wait(4)
            return "Stopped"
        monkeypatch.setattr(window.services, "chat", respond)
        path = str(tmp_path / "local.png")
        chat.add_images([path])
        chat.composer.setPlainText("Inspect this image")
        chat.send()
        wait_until(app, lambda: started.is_set() and chat.stream_widget is not None)
        chat.flush_stream()
        assert chat.stream_widget.toPlainText() == "Inspecting local image"
        assert chat.stop.isVisible() and not chat.send_button.isVisible()
        assert not chat.add_button.isEnabled() and not chat.model_toggle.isEnabled()
        worker = chat.worker
        chat.stop.click()
        assert worker.cancel.is_set()
        wait_until(app, lambda: not chat.busy)
        assert observed == [("Inspect this image", (path,))]
        assert chat.send_button.isVisible() and not chat.stop.isVisible()
        assert [text.toPlainText() for text in chat.messages_widget.findChildren(MessageText)] == ["Stopped"]
        assert chat.stream_frame is None and not chat.stream_timer.isActive()


def test_compact_tool_event_updates_in_place_with_expandable_real_undo(app, tmp_path, monkeypatch):
    with opened_chat(app, tmp_path, monkeypatch) as (window, chat):
        calls = []
        monkeypatch.setattr(window, "open_capabilities", lambda name, arguments: calls.append((name, arguments)))
        chat.on_activity("tool", {"name": "files.move", "status": "Running"})
        result = {"name": "files.move", "result": {"ok": True, "data": {
            "source": "Downloads/a.png", "destination": "Pictures/a.png", "undo_id": "receipt"}}}
        chat.on_activity("tool_result", result)
        assert len(chat._event_rows) == 1 and chat.timeline.count() == 1
        event = chat._event_rows[0]
        assert isinstance(event, NexusToolEvent) and event.details.isHidden()
        assert "Moved file" in event.heading.text()
        event.heading.click()
        assert not event.details.isHidden() and "receipt" in event.preview.toPlainText()
        next(control for control in event.findChildren(QPushButton) if control.text() == "Undo").click()
        assert calls == [("actions.undo", {"id": "receipt"})]
        chat.on_activity("tool", {"name": "files.inspect", "status": "Running"})
        chat.on_activity("tool_result", {"name": "files.inspect", "result": {"ok": False, "error": "Access denied"}})
        assert "Failed" in chat._event_rows[-1].heading.text()
        chat.on_activity("tool", {"name": "files.search", "status": "Running"})
        chat.on_activity("tool", {"name": "files.search", "status": "Cancelled"})
        assert len(chat._event_rows) == 3 and chat._event_rows[-1].state == "cancelled"
        assert "Cancelled" in chat._event_rows[-1].heading.text()
        chat.show_operator_card({"id": "operator", "status": "running", "steps": [{"status": "running"}]})
        row = chat.operator_items["operator"]
        chat.show_operator_card({"id": "operator", "status": "complete", "steps": [{"status": "complete"}]})
        assert chat.operator_items["operator"] is row and "1/1" in row.text()


def test_reader_scroll_is_preserved_while_streaming(app, tmp_path, monkeypatch):
    with opened_chat(app, tmp_path, monkeypatch) as (_, chat):
        for index in range(16):
            chat.add_message("assistant", f"Section {index}\n\n" + "Verified result\n\n" * 4)
        QTest.qWait(100)
        app.processEvents()
        scrollbar = chat.transcript.verticalScrollBar()
        assert scrollbar.maximum() > 0
        scrollbar.setValue(0)
        assert not chat._follow_stream
        chat.on_activity("text_delta", {"text": "New response\n\n" * 15})
        chat.flush_stream()
        QTest.qWait(100)
        assert scrollbar.value() == 0
        scrollbar.setValue(scrollbar.maximum())
        chat.on_activity("text_delta", {"text": "Additional result\n\n" * 10})
        chat.flush_stream()
        QTest.qWait(100)
        assert scrollbar.value() == scrollbar.maximum()


def test_file_context_requires_permission_and_allowed_root_and_uses_full_palette(app, tmp_path, monkeypatch):
    with opened_chat(app, tmp_path, monkeypatch) as (window, chat):
        permitted = tmp_path / "documents"
        permitted.mkdir()
        path = permitted / "private.txt"
        path.write_text("LOCAL PRIVATE TEXT", encoding="utf-8")
        opened = []
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (opened.append(True) or [str(path)], ""))
        chat.choose_files()
        assert not opened and not window.services.settings.get("context.enabled", False)
        window.services.settings.set("context.enabled", True)
        chat.choose_files()
        assert window.services.context.selection()["selected_files"] == []
        window.services.add_file_root(str(permitted))
        chat.choose_files()
        assert window.services.context.selection()["selected_files"] == [str(path)]
        assert chat.context_summary.text() == "1 local file"
        assert not chat.inspector.isHidden()
        assert "LOCAL PRIVATE TEXT" not in chat.detail_view.toPlainText()
        chat.read_context_tool("Project files", "developer.find_files", {"path": str(permitted)})
        wait_until(app, lambda: not window.jobs)
        assert "private.txt" in chat.detail_view.toPlainText()
        assert "LOCAL PRIVATE TEXT" not in chat.detail_view.toPlainText()
        window.services.remove_file_root(str(permitted))
        chat.read_context_tool("Project files", "developer.find_files", {"path": str(permitted)})
        wait_until(app, lambda: not window.jobs)
        assert "outside allowed access" in chat.detail_view.toPlainText()
        chat.add_message("assistant", "```python\nprint('local')\n```")
        for theme in ("cream", "frost", "graphite", "sand"):
            window._liquid_preferences["theme"] = theme
            chat.refresh_materials()
            text = chat.messages_widget.findChildren(MessageText)[-1]
            assert text.document().defaultStyleSheet() == markdown_stylesheet(window._liquid_preferences)
            assert "print('local')" in text.toPlainText()
