"""Nexus composition reuses real conversation, worker and disclosure boundaries."""
import threading

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QPushButton

from jarvix.ui.chat import MessageText
from jarvix.ui.liquid.theme import markdown_stylesheet
from jarvix.ui.nexus_frontend.design import TOKENS
from test_interface_modes import opened_interface
from test_ui import app as app, wait_until


def test_nexus_chat_uses_shell_history_and_readable_document_with_contextual_inspector(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.resize(1920, 1080)
        window.navigate("Chat")
        chat = window.pages["Chat"]
        app.processEvents()
        assert not chat.history.isVisible() and window.history.isVisible()
        assert not chat.model_menu.isVisible()
        assert not chat.regenerate_button.isVisible()
        assert chat.inspector.isHidden()
        assert chat.state_stack.currentWidget() is chat.empty
        chat.add_message("assistant", "Ready to inspect the project.")
        app.processEvents()
        assert chat.messages_widget.width() <= TOKENS.get("conversation_width", 940)
        assert chat.messages_widget.width() >= 700
        assert chat.command_surface.width() <= TOKENS["conversation_width"] + TOKENS["surface_inset"] * 2
        assert not chat.attach_button.isVisible() and not chat.screen_button.isVisible()
        assert chat.add_button.menu() is chat.attachment_menu
        actions = [action.text() for action in chat.attachment_menu.actions()]
        assert {"Attach image…", "Take screenshot…", "Attach file context…", "Add project…"} <= set(actions)
        original_height = chat.composer.height()
        chat.composer.setPlainText("one\ntwo\nthree\nfour\nfive")
        assert chat.composer.height() > original_height
        assert chat.composer.height() <= TOKENS.get("composer_max_height", 180)
        chat.inspect_context()
        wait_until(app, lambda: not getattr(chat.inspector, "_liquid_animations", {}))
        assert chat.inspector_tabs.currentIndex() == 0
        chat.close_inspector()
        wait_until(app, chat.inspector.isHidden)
        assert chat.model_toggle.menu() is chat.model_menu
        window.resize(3840, 2160)
        app.processEvents()
        assert chat.command_surface.width() <= TOKENS["conversation_width"] + TOKENS["surface_inset"] * 2
        assert chat.messages_widget.width() <= TOKENS["conversation_width"]


def test_nexus_streaming_worker_stop_retains_selected_images_and_original_cancel_boundary(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.navigate("Chat")
        chat = window.pages["Chat"]
        observed = []
        started = threading.Event()
        def respond(text, conversation_id, provider, model, *, approve, on_event, cancel, attachments):
            observed.append((text, conversation_id, provider, model, attachments))
            on_event("stream_start", {})
            on_event("text_delta", {"text": "Reviewing the attachment"})
            started.set()
            assert cancel.wait(3), "Stop did not reach the original worker cancellation event"
            return "Stopped after reviewing the attachment"
        monkeypatch.setattr(window.services, "chat", respond)
        path = str(tmp_path / "local.png")
        chat.add_images([path])
        chat.composer.setPlainText("Inspect this image")
        chat.send()
        wait_until(app, lambda: started.is_set() and chat.stream_widget is not None)
        chat.flush_stream()
        assert chat.stream_widget.toPlainText() == "Reviewing the attachment"
        assert chat.stop.isVisible() and not chat.send_button.isVisible()
        assert not chat.add_button.isEnabled() and not chat.provider.isEnabled()
        worker = chat.worker
        chat.stop.click()
        assert worker.cancel.is_set()
        wait_until(app, lambda: not chat.busy)
        assert observed[0][0] == "Inspect this image" and observed[0][-1] == (path,)
        assert chat.send_button.isVisible() and not chat.stop.isVisible()
        assert chat.add_button.isEnabled() and chat.provider.isEnabled()
        texts = [message.toPlainText() for message in chat.messages_widget.findChildren(MessageText)]
        assert texts == ["Stopped after reviewing the attachment"]
        assert chat.stream_frame is None and not chat.stream_timer.isActive()
        chat.new_conversation()
        assert not chat.activity.isVisible() and chat.state_stack.currentWidget() is chat.empty


def test_nexus_stream_does_not_drag_reader_back_to_bottom(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.navigate("Chat")
        chat = window.pages["Chat"]
        for index in range(15):
            chat.add_message("assistant", f"Section {index}\n\n" + "Verified output\n\n" * 5)
        QTest.qWait(80)
        app.processEvents()
        bar = chat.transcript.verticalScrollBar()
        assert bar.maximum() > 0
        chat.follow_stream()
        bar.setValue(0)
        assert not chat._follow_stream
        chat.on_activity("text_delta", {"text": "A new response\n\n" * 15})
        chat.flush_stream()
        QTest.qWait(80)
        app.processEvents()
        assert bar.value() == 0
        bar.setValue(bar.maximum())
        assert chat._follow_stream
        chat.on_activity("text_delta", {"text": "More verified results\n\n" * 10})
        chat.flush_stream()
        QTest.qWait(80)
        app.processEvents()
        assert bar.value() == bar.maximum()


def test_nexus_file_context_respects_permission_roots_and_stays_local(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        chat = window.pages["Chat"]
        private = tmp_path / "permitted"
        private.mkdir()
        document = private / "notes.txt"
        document.write_text("PRIVATE LOCAL CONTENT", encoding="utf-8")
        opened = []
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (opened.append(True) or [str(document)], ""))
        chat.choose_files()
        assert not opened and not window.services.settings.get("context.enabled", False)
        window.services.settings.set("context.enabled", True)
        chat.choose_files()
        assert window.services.context.selection()["selected_files"] == []
        window.services.add_file_root(str(private))
        chat.choose_files()
        assert window.services.context.selection()["selected_files"] == [str(document)]
        assert chat.context_summary.text() == "1 local file"
        assert chat.inspector_tabs.currentIndex() == 0 and not chat.inspector.isHidden()
        assert chat.composer.toPlainText() == ""
        assert not window.services.list_conversations()
        assert "PRIVATE LOCAL CONTENT" not in " ".join(label.text() for label in chat.context_pane.findChildren(type(chat.context_summary)))
        assert not window.services.settings.get("screenshots.enabled", False)
        observed = []
        def respond(*args, **kwargs):
            observed.append(window.services.context.selection())
            return "Local context selected"
        monkeypatch.setattr(window.services, "chat", respond)
        chat.composer.setPlainText("Review the selected local file")
        chat.send()
        wait_until(app, lambda: not chat.busy)
        assert observed[0]["selected_files"] == [str(document)]
        assert observed[0]["conversation_id"] == chat.conversation_id


def test_nexus_project_mission_links_tool_results_and_theme_keep_real_handlers(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        chat = window.pages["Chat"]
        project_path = tmp_path / "project"
        project_path.mkdir()
        project = window.services.add_project("Jarvix", str(project_path))
        window.services.add_file_root(str(project_path))
        mission = window.services.missions.save("Review the Jarvix release")
        conversation = window.services.new_conversation("Release review")
        chat.load_conversation(conversation)
        window.services.settings.set("context.enabled", True)
        monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][0], True))
        chat.choose_project()
        chat.choose_mission()
        assert window.services.records.get("conversation_meta", conversation)["project_id"] == project
        selected = window.services.context.selection()
        assert selected["project_id"] == project and selected["mission_id"] == mission["id"]
        assert window.controller.capability_dialog.selected_tool == "context.link"
        assert window.controller.capability_dialog.form.arguments() == {
            "source": {"kind": "conversation", "reference": conversation},
            "target": {"kind": "mission", "reference": mission["id"]}, "relation": "related",
        }
        assert window.controller.capability_dialog.worker is None and not window.services.records.list("context.link")
        assert "Jarvix" in chat.context_summary.text() and "Mission" in chat.context_summary.text()
        calls = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, arguments: calls.append((name, arguments)))
        result = {"name": "files.move", "ok": True, "data": {"source": "one", "destination": "two", "undo_id": "receipt"}}
        chat.on_activity("tool_result", result)
        item = chat.timeline.item(0)
        assert item.data(Qt.ItemDataRole.UserRole) == result
        card = chat._event_rows[-1]
        assert card.heading.accessibleName() and card.details.isHidden()
        card.heading.click()
        wait_until(app, lambda: not getattr(card.details, "_liquid_animations", {}))
        next(button for button in card.findChildren(QPushButton) if button.text() == "Undo").click()
        assert calls == [("actions.undo", {"id": "receipt"})]
        chat.add_message("assistant", "```python\nprint('local')\n```")
        window._liquid_preferences["theme"] = "graphite"
        chat.refresh_materials()
        message = chat.messages_widget.findChildren(MessageText)[-1]
        assert message.document().defaultStyleSheet() == markdown_stylesheet(window._liquid_preferences)
        assert "print('local')" in message.toPlainText()
