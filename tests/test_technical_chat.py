"""Presentation checks retain the actual account and action boundaries."""
from PySide6.QtWidgets import QLabel, QPushButton
from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest

from jarvix.ui.chat import MessageText

from test_ui import app as app, window as window


def test_chat_is_a_document_workspace_and_keeps_attachment_disclosure(window, app, tmp_path):
    window.navigate("Chat")
    chat = window.pages["Chat"]
    texts = [widget.text() for widget in chat.messages_widget.findChildren(QLabel)]
    assert "Start a conversation" in texts
    assert not any("clearer way" in text or "A clearer" in text for text in texts)
    assert chat.composer.accessibleName() == "Message Jarvix"
    assert chat.provider.accessibleName() and chat.model.accessibleName()
    assert chat.attachment_label.isHidden()
    image = tmp_path / "error.png"
    chat.add_images([str(image)])
    assert chat.attachments == [str(image)]
    assert not chat.attachment_label.isHidden()
    assert "approval" in chat.attachment_label.text()
    chat.clear_images()
    assert chat.attachment_label.isHidden() and not chat.attachments
    assert not chat.busy and not window.services.list_conversations()
    conversation = window.services.new_conversation("Notes from testing")
    chat.load_conversation(conversation)
    chat.export_conversation()
    assert window.capability_dialog.selected_tool == "conversations.export"
    assert window.capability_dialog.form.arguments()["id"] == conversation
    assert not window.capability_dialog.worker


def test_chat_result_details_stay_collapsed_and_keep_real_action_routes(window):
    chat = window.pages["Chat"]
    result = {"name": "files.move", "ok": True, "data": {
        "path": "C:/Approved/report.txt", "undo_id": "receipt-17", "private_detail": "large technical result"}}
    chat.on_activity("tool_result", result)
    item = chat.timeline.item(0)
    assert item.data(256) == result
    row = chat.timeline.itemWidget(item)
    texts = [widget.text() for widget in row.findChildren(QLabel)]
    assert "Moved file" in texts
    assert "large technical result" not in texts
    buttons = {widget.text(): widget for widget in row.findChildren(QPushButton)}
    assert "Details" in buttons
    buttons["Undo"].click()
    assert window.capability_dialog.selected_tool == "actions.undo"
    assert window.capability_dialog.form.arguments() == {"id": "receipt-17"}
    assert not window.capability_dialog.worker


def test_integrations_are_account_rows_and_not_claimed_connected(window, app):
    window.navigate("Integrations")
    page = window.pages["Integrations"]
    assert page.account_status.columnCount() == 6
    assert page.account_status.rowCount() == len(page.cards) == 6
    for key, index in page.account_rows.items():
        assert page.account_status.item(index, 1).text() == "Not connected"
        assert page.account_status.item(index, 3).text() == "Not granted"
        state, detail, connect, manage, disconnect, cancel = page.cards[key]
        assert state.text() == "Not connected" and "verified" in detail.text()
        assert connect.accessibleName() and connect.toolTip()
        assert not connect.isHidden() and manage.isHidden() and disconnect.isHidden() and cancel.isHidden()
    assert all(entry.echoMode() == entry.EchoMode.Password for entry in page.keys.values())
    window.resize(860, 600)
    app.processEvents()
    assert not page.account_status.isColumnHidden(0)
    assert not page.account_status.isColumnHidden(1)
    assert not page.account_status.isColumnHidden(5)
    assert page.account_status.isColumnHidden(4)
    assert "Permissions:" in page.account_status.item(0, 0).toolTip()
    assert page.account_status.horizontalScrollBar().maximum() == 0


def test_quick_command_keeps_context_off_and_routes_to_chat(window, monkeypatch):
    overlay = window.overlay
    assert overlay.command.accessibleName() == "Quick command"
    assert not overlay.share_context.isEnabled()
    texts = [widget.text() for widget in overlay.findChildren(QPushButton)]
    assert "Send to Chat" in texts and "↑" not in texts
    submitted = []
    monkeypatch.setattr(window, "open_chat", submitted.append)
    overlay.command.setText("Explain this error")
    overlay.submit()
    assert submitted == ["Explain this error"]
    assert not window.pages["Chat"].busy


def test_markdown_tables_render_without_fetching_embedded_resources(app, window):
    widget = MessageText("| Check | Status |\n| --- | --- |\n| Tests | Not run |\n\n<script>unsafe()</script>")
    assert "<table" in widget.document().toHtml()
    assert "<script>" not in widget.document().toHtml()
    assert widget.loadResource(2, QUrl("https://example.test/private.png")) is None
    assert widget.loadResource(2, QUrl("file:///C:/Users/private.png")) is None
    widget.deleteLater()
    window.navigate("Chat")
    chat = window.pages["Chat"]
    chat.add_message("assistant", "A verified read is required.\n\n```python\nassert peer.connected\n```\n\n| Check | Expected |\n| --- | --- |\n| Read | Available |\n| Writes | Review |")
    QTest.qWait(100)
    message = chat.messages_widget.findChildren(MessageText)[-1]
    assert message.height() >= message.document().size().height()
    assert message.verticalScrollBar().maximum() == 0
