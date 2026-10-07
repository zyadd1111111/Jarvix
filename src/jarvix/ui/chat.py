"""Conversation presentation and the explicit worker-to-UI permission boundary."""
from __future__ import annotations

import json
import base64
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QFrame,
    QScrollArea, QComboBox, QLineEdit, QDialog, QPlainTextEdit, QTextBrowser,
    QSplitter, QApplication, QInputDialog, QFileDialog, QLabel, QMenu, QSizePolicy,
)

from .pages import Page, pretty_date
from .widgets import label, button, Composer, ChatJob, clear_layout, TextPreview, evidence_text
from .icons import icon

MARKDOWN = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML


class ImageComposer(Composer):
    images_dropped = Signal(list)
    image_pasted = Signal(object)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            self.image_pasted.emit(source.imageData())
        elif source.hasUrls():
            paths = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
            if paths:
                self.images_dropped.emit(paths)
            else:
                super().insertFromMimeData(source)
        else:
            super().insertFromMimeData(source)


class MessageText(QTextBrowser):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("MessageText")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self.open_link)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from .theme import markdown_stylesheet
        self.document().setDefaultStyleSheet(markdown_stylesheet())
        self.document().setMarkdown(text, MARKDOWN)
        self.document().documentLayout().documentSizeChanged.connect(self.fit)
        self.fit()

    def loadResource(self, resource_type, name):
        # Model-authored markdown cannot fetch remote images or local resources.
        return None

    def open_link(self, url):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def fit(self, *_):
        height = int(self.document().size().height()) + 8
        self.setFixedHeight(max(36, min(height, 1800)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().setTextWidth(max(100, self.viewport().width()))
        self.fit()


class PermissionDialog(QDialog):
    """A closed dialog, Escape, or window close always denies the request."""

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Jarvix · Permission request")
        self.setMinimumSize(520, 400)
        self.resize(690, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        disclose = request.kind == "disclose"
        layout.addWidget(label("Share with provider" if disclose else "Action requires approval", "Heading", True))
        layout.addWidget(label(request.description, "Muted", True))
        layout.addWidget(label(f"{request.tool_name}  ·  {request.permission}", "Code", True))
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(request.preview or json.dumps(request.arguments, indent=2, ensure_ascii=False))
        layout.addWidget(preview, 1)
        layout.addWidget(label("This approval applies to this request only. You can deny and continue the conversation.", "Muted", True))
        row = QHBoxLayout()
        row.addStretch()
        self.deny_button = button("Deny", self.reject)
        self.deny_button.setDefault(True)
        self.allow_button = button("Share once" if disclose else "Allow once", self.accept, "Primary")
        self.allow_button.setAutoDefault(False)
        row.addWidget(self.deny_button)
        row.addWidget(self.allow_button)
        layout.addLayout(row)
        if request.images:
            pictures = QHBoxLayout()
            for image in request.images:
                thumbnail = QLabel()
                pixmap = QPixmap()
                pixmap.loadFromData(base64.b64decode(image.data_base64))
                thumbnail.setPixmap(pixmap.scaled(240, 160, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
                thumbnail.setToolTip(image.name)
                pictures.addWidget(thumbnail)
            layout.insertLayout(4, pictures)
            self.resize(860, 700)


class ChatPage(Page):
    title = "Chat"
    subtitle = "Conversations, tools and approved context."

    def __init__(self, window):
        super().__init__(window)
        self.conversation_id = None
        self.worker = None
        self.last_answer = ""
        self.pending_dialog = None
        self.operator_items = {}
        self.attachments = []
        self.stream_frame = None
        self.stream_widget = None
        self.stream_buffer = ""
        self.stream_timer = QTimer(self)
        self.stream_timer.setInterval(50)
        self.stream_timer.timeout.connect(self.flush_stream)
        split = QSplitter()
        split.setObjectName("ChatSplit")
        split.setChildrenCollapsible(False)
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(0, 0, 8, 0)
        history_layout.setSpacing(8)
        new = button("New conversation", self.new_conversation)
        new.setIcon(icon("plus"))
        history_layout.addWidget(new)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search conversations…")
        self.history_search.setAccessibleName("Search conversations")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self.refresh_history)
        history_layout.addWidget(self.history_search)
        self.history = QListWidget()
        self.history.setObjectName("ConversationList")
        self.history.setMinimumWidth(150)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.history_menu)
        self.history.currentItemChanged.connect(self.select_conversation)
        history_layout.addWidget(self.history)
        history_actions = QHBoxLayout()
        history_actions.addWidget(button("Conversation actions", self.history_menu, "Quiet"))
        history_layout.addLayout(history_actions)
        history_layout.addWidget(label("Stored on this device", "Muted"))
        split.addWidget(history_widget)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(8, 0, 0, 0)
        body.setSpacing(8)
        toolbar = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItem("OpenAI", "openai")
        self.provider.addItem("Gemini", "gemini")
        self.provider.addItem("Ollama · local", "ollama")
        self.provider.addItem("Local endpoint", "local")
        self.provider.addItem("Automatic routing", "auto")
        self.provider.currentIndexChanged.connect(self.change_provider)
        self.provider.setAccessibleName("AI provider")
        self.provider.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.model = QLineEdit()
        self.model.setPlaceholderText("Model identifier")
        self.model.setMinimumWidth(95)
        self.model.setAccessibleName("Model identifier")
        self.model.setObjectName("ModelIdentifier")
        toolbar.addWidget(self.provider)
        toolbar.addWidget(self.model)
        self.task_role = QComboBox()
        self.task_role.addItems(["chat", "planning", "coding", "document_analysis", "summarization"])
        self.task_role.setToolTip("Automatic routing uses this task role; an explicitly selected provider/model overrides routing.")
        self.task_role.setAccessibleName("Task routing role")
        self.task_role.setCurrentText(self.services.settings.get("chat.role", "chat"))
        self.task_role.currentTextChanged.connect(lambda value: self.services.settings.set("chat.role", value))
        toolbar.addWidget(self.task_role)
        toolbar.addStretch()
        self.context_button = button("Context", self.inspect_context, "Quiet")
        self.context_button.setToolTip("Inspect the exact context and tools for the next request")
        toolbar.addWidget(self.context_button)
        body.addLayout(toolbar)
        self.transcript = QScrollArea()
        self.transcript.setObjectName("ConversationTranscript")
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setWidgetResizable(True)
        self.messages_widget = QWidget()
        self.messages = QVBoxLayout(self.messages_widget)
        self.messages.setContentsMargins(0, 8, 8, 8)
        self.messages.setSpacing(8)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.transcript.setWidget(self.messages_widget)
        body.addWidget(self.transcript, 1)
        self.activity = label("Ready · conversation context only", "Muted", True)
        self.activity.setAccessibleName("Chat activity")
        body.addWidget(self.activity)
        self.timeline = QListWidget()
        self.timeline.setObjectName("ToolTimeline")
        self.timeline.setMaximumHeight(130)
        self.timeline.setAccessibleName("Tool execution activity")
        self.timeline.setVisible(False)
        self.timeline.itemActivated.connect(self.inspect_event)
        body.addWidget(self.timeline)
        self.attachment_label = label("", "Muted", True)
        self.attachment_label.hide()
        body.addWidget(self.attachment_label)
        self.composer = ImageComposer()
        self.composer.setObjectName("Composer")
        self.composer.setPlaceholderText("Ask Jarvix or tell it to do something…")
        self.composer.setAccessibleName("Message Jarvix")
        from .theme import TOKENS
        self.composer.setFixedHeight(TOKENS["control_height"] * 3 + TOKENS["spacing"][2])
        self.composer.submitted.connect(self.send)
        self.composer.images_dropped.connect(self.add_images)
        self.composer.image_pasted.connect(self.paste_image)
        body.addWidget(self.composer)
        actions = QHBoxLayout()
        input_tools = QHBoxLayout()
        self.attach_button = button("Attach image", self.choose_images, "Quiet")
        self.screen_button = button("Capture screen", self.screen_menu, "Quiet")
        input_tools.addWidget(self.attach_button)
        input_tools.addWidget(self.screen_button)
        voice = button("Voice", lambda: self.window.navigate("Voice"), "Quiet")
        voice.setIcon(icon("voice"))
        input_tools.addWidget(voice)
        self.clear_attachments_button = button("Clear images", self.clear_images, "Quiet")
        self.clear_attachments_button.hide()
        input_tools.addWidget(self.clear_attachments_button)
        input_tools.addStretch()
        body.insertLayout(body.indexOf(self.composer), input_tools)
        self.regenerate_button = button("Regenerate", self.regenerate, "Quiet")
        self.regenerate_button.setToolTip("Branch from the latest user message and request another response")
        actions.addWidget(self.regenerate_button)
        actions.addStretch()
        self.stop = button("Stop response", self.cancel)
        self.stop.setIcon(icon("square"))
        self.stop.setEnabled(False)
        self.send_button = button("Send message", self.send, "Primary")
        actions.addWidget(self.stop)
        actions.addWidget(self.send_button)
        body.addLayout(actions)
        footer = QHBoxLayout()
        self.connection = label("", "Muted")
        footer.addWidget(self.connection)
        footer.addStretch()
        footer.addWidget(label("Enter to send · Shift+Enter for a new line", "Caption"))
        body.addLayout(footer)
        split.addWidget(content)
        split.setSizes([215, 770])
        self.layout.addWidget(split, 1)
        self.reload_provider()
        self.render_messages()

    def history_menu(self, position=None):
        if position is not None:
            item = self.history.itemAt(position)
            if item and not self.busy:
                self.history.setCurrentItem(item)
        menu = QMenu(self)
        for text, callback, name in (("Rename conversation", self.rename_conversation, "file"),
                                     ("Pin / unpin conversation", self.pin_conversation, "pin"),
                                     ("Export conversation", self.export_conversation, "folder"),
                                     ("Delete conversation", self.delete_conversation, "trash")):
            action = menu.addAction(icon(name), text, callback)
            action.setEnabled(bool(self.conversation_id) and not self.busy)
        menu.exec(self.history.mapToGlobal(position) if position is not None else
                  self.history.mapToGlobal(self.history.rect().bottomLeft()))

    def export_conversation(self):
        if self.conversation_id and not self.busy:
            self.window.open_capabilities("conversations.export", {"id": self.conversation_id})

    @property
    def busy(self):
        # Keep the request active until its queued finished signal is handled.
        # A completed thread may still have result/approval signals in flight.
        return self.worker is not None

    def reload_provider(self):
        if self.busy:
            return
        current = self.services.settings.get("provider", "openai")
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(current)))
        self.provider.blockSignals(False)
        from jarvix.providers import DEFAULT_MODELS
        self.model.setText(self.services.settings.get("model." + current, DEFAULT_MODELS.get(current, "")))
        self.model.setPlaceholderText("Selected by task routing" if current == "auto" else "Model identifier")
        self.update_connection()

    def change_provider(self):
        provider_id = self.provider.currentData()
        self.services.settings.set("provider", provider_id)
        from jarvix.providers import DEFAULT_MODELS
        self.model.setText(self.services.settings.get("model." + provider_id, DEFAULT_MODELS.get(provider_id, "")))
        self.update_connection()
        self.window.update_status()

    def update_connection(self):
        ready = self.services.provider_status().get(self.provider.currentData(), False)
        provider_id = self.provider.currentData()
        local = provider_id in {"ollama", "local"}
        self.connection.setText(("Local endpoint configured" if ready else "Select a local model in Knowledge & Models")
                                if local else "Automatic · inspect task routing" if provider_id == "auto"
                                else "API key configured" if ready else "Add an API key in Integrations")

    def refresh(self):
        self.refresh_history()
        self.update_connection()

    def refresh_history(self):
        self.history.blockSignals(True)
        self.history.clear()
        for conversation in self.services.conversations.search(self.history_search.text()):
            item = QListWidgetItem(conversation.get("title", "Conversation") + "\n" + pretty_date(conversation.get("updated_at")))
            if conversation["pinned"]:
                item.setIcon(icon("pin"))
            item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
            self.history.addItem(item)
            if conversation["id"] == self.conversation_id:
                self.history.setCurrentItem(item)
        self.history.blockSignals(False)

    def rename_conversation(self):
        if self.busy or not self.conversation_id:
            return
        title, ok = QInputDialog.getText(self, "Rename conversation", "Title")
        if ok and title.strip():
            self.window.guard(lambda: self.services.conversations.configure(self.conversation_id, title=title))
            self.refresh_history()

    def pin_conversation(self):
        if self.busy or not self.conversation_id:
            return
        try:
            current = self.services.records.get("conversation_meta", self.conversation_id)
        except ValueError:
            current = {}
        self.window.guard(lambda: self.services.conversations.configure(
            self.conversation_id, pinned=not current.get("pinned", False)))
        self.refresh_history()

    def delete_conversation(self):
        if self.busy or not self.conversation_id:
            return
        if self.window.confirm_delete("Delete this conversation?", "All messages in this conversation will be removed from this device."):
            self.services.conversations.delete(self.conversation_id)
            self.new_conversation()

    def revise_message(self, index, send=False):
        if self.busy or not self.conversation_id:
            return
        branch = self.services.conversations.branch(self.conversation_id, index)
        self.load_conversation(branch["id"])
        self.set_draft(branch["draft"])
        if send:
            self.send()

    def regenerate(self):
        if self.busy or not self.conversation_id:
            return
        rows = self.services.conversation_messages(self.conversation_id)
        index = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["role"] == "user"), None)
        if index is not None:
            self.revise_message(index, send=True)

    def select_conversation(self, item, previous=None):
        if item and not self.busy:
            self.load_conversation(item.data(Qt.ItemDataRole.UserRole))

    def load_conversation(self, conversation_id):
        if self.busy:
            self.window.notify("Stop the active response before switching conversations.")
            return
        self.conversation_id = conversation_id
        self.window.select_context(conversation_id=conversation_id)
        self.timeline.clear()
        self.operator_items.clear()
        self.timeline.hide()
        self.render_messages()
        self.refresh_history()

    def new_conversation(self):
        if self.busy:
            self.window.notify("Stop the active response before creating a conversation.")
            return
        self.conversation_id = None
        self.window.select_context(conversation_id="")
        self.last_answer = ""
        self.timeline.clear()
        self.operator_items.clear()
        self.timeline.hide()
        self.composer.clear()
        self.clear_images()
        self.render_messages()
        self.refresh_history()
        self.composer.setFocus()

    def render_messages(self):
        self.stream_timer.stop()
        self.stream_frame = self.stream_widget = None
        self.stream_buffer = ""
        clear_layout(self.messages)
        self.last_answer = ""
        rows = self.services.conversation_messages(self.conversation_id) if self.conversation_id else []
        if not rows:
            empty = QFrame()
            empty.setObjectName("ChatEmptyState")
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(12, 16, 12, 16)
            layout.setSpacing(8)
            layout.addWidget(label("Start a conversation", "Heading"))
            layout.addWidget(label("Ask a question or describe a task. Jarvix requests approval before sensitive actions or sharing local results.", "Muted", True))
            for example in ("Show processes using the most RAM", "Find my unfinished tasks", "Search my notes"):
                layout.addWidget(button(example, lambda text=example: self.set_draft(text), "Quiet"))
            self.messages.addWidget(empty)
        for index, row in enumerate(rows):
            if row.get("role") in ("user", "assistant"):
                self.add_message(row["role"], row["content"], index)
        self.scroll_to_bottom()

    def add_message(self, role, content, index=None):
        frame = QFrame()
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        frame.setObjectName("UserMessage" if role == "user" else "Message")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(label("You" if role == "user" else "Jarvix", "MessageAuthor"))
        header.addStretch()
        header.addWidget(button("Copy", lambda: QApplication.clipboard().setText(content), "Quiet"))
        if role == "user" and index is not None:
            header.addWidget(button("Edit message", lambda: self.revise_message(index), "Quiet"))
        if role == "assistant":
            self.last_answer = content
            more = button("Response actions", style="Quiet")
            menu = QMenu(more)
            menu.addAction(icon("voice"), "Read aloud", lambda: self.window.run_job(
                lambda: self.services.speak(content), self.window.pages["Voice"].speech_started))
            menu.addAction(icon("tasks"), "Create task", lambda: self.window.guard(lambda: (
                self.services.add_task(content[:500]), self.window.notify("Task created"))))
            menu.addAction(icon("notes"), "Save as note", lambda: self.window.guard(lambda: (
                self.services.save_note("From Jarvix", content), self.window.notify("Note saved"))))
            more.setMenu(menu)
            header.addWidget(more)
        layout.addLayout(header)
        layout.addWidget(MessageText(content))
        self.messages.addWidget(frame)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        QTimer.singleShot(30, self, lambda: self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum()))

    def fit_timeline(self):
        height = sum(max(32, self.timeline.item(index).sizeHint().height())
                     for index in range(self.timeline.count()))
        self.timeline.setFixedHeight(min(130, height + 8))

    def set_draft(self, text):
        self.composer.setPlainText(text)
        self.composer.setFocus()

    def choose_images(self):
        if not self.busy:
            paths, _ = QFileDialog.getOpenFileNames(self, "Attach images", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
            self.add_images(paths)

    def add_images(self, paths):
        if self.busy:
            return
        selected = list(dict.fromkeys([*self.attachments, *paths]))
        if len(selected) > 4 or any(Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} for path in selected):
            self.window.notify("Attach up to four PNG, JPEG, WebP or BMP images.")
            return
        self.attachments = selected
        self.attachment_label.setText("Attached locally · " + ", ".join(Path(path).name for path in selected)
                                      + "\nCloud vision requires your approval." if selected else "")
        self.attachment_label.setVisible(bool(selected))
        self.clear_attachments_button.setVisible(bool(selected))

    def clear_images(self):
        self.attachments = []
        self.attachment_label.clear()
        self.attachment_label.hide()
        self.clear_attachments_button.hide()

    def paste_image(self, image):
        if self.busy:
            return
        if not self.services.settings.get("clipboard.enabled", False):
            self.window.notify("Enable clipboard permission in Settings before pasting an image.")
            return
        if len(self.attachments) >= 4:
            self.window.notify("Attach at most four images per request.")
            return
        from jarvix.capabilities.files import _linked
        directory = self.services.data_dir / "attachments"
        if any(_linked(part) for part in (directory, *directory.parents)):
            self.window.notify("The attachment directory must be local and cannot be a link.")
            return
        directory.mkdir(exist_ok=True)
        path = directory / f"clipboard-{uuid4().hex}.png"
        if image is None or image.isNull() or image.width() * image.height() > 32_000_000 or not image.save(str(path), "PNG"):
            self.window.notify("The clipboard image could not be saved within image limits.")
            return
        self.add_images([str(path)])

    def screen_menu(self):
        if self.busy:
            return
        menu = QMenu(self)
        menu.addAction("Attach active app screenshot", self.capture_active)
        menu.addAction("Attach selected monitor screenshot", self.choose_monitor)
        menu.addAction("Local OCR of recent screenshot", lambda: self.window.open_capabilities("vision.read_text", {}))
        menu.exec(self.mapToGlobal(self.rect().center()))

    def capture_active(self):
        if not self.services.settings.get("screenshots.enabled", False):
            self.window.notify("Enable screenshot permission in Settings first.")
            return
        self.window.hide()
        def done(result):
            self.window.show()
            self.window.activateWindow()
            self.add_images([result["path"]])
        def failed(error):
            self.window.show()
            self.window.notify(error)
        QTimer.singleShot(350, self, lambda: self.window.run_job(lambda: self.services.vision.capture("active"), done, failed))

    def choose_monitor(self):
        def choose(monitors):
            names = [f"{row['index']}: {row.get('device', 'Display')} · {row['width']}×{row['height']}" for row in monitors]
            name, ok = QInputDialog.getItem(self, "Capture monitor", "Choose a display; hide private content before capturing.", names, 0, False)
            if ok:
                monitor = monitors[names.index(name)]["index"]
                self.window.run_job(lambda: self.services.vision.capture("monitor", monitor=monitor),
                                    lambda result: self.add_images([result["path"]]))
        self.window.run_job(self.services.system.monitors, choose)

    def clear_stream(self):
        self.stream_timer.stop()
        if self.stream_frame:
            self.messages.removeWidget(self.stream_frame)
            self.stream_frame.deleteLater()
        self.stream_frame = self.stream_widget = None
        self.stream_buffer = ""

    def flush_stream(self):
        if self.stream_widget:
            self.stream_widget.document().setMarkdown(self.stream_buffer, MARKDOWN)
            self.stream_widget.fit()
            self.scroll_to_bottom()
        self.stream_timer.stop()

    def send(self):
        if self.busy:
            return
        text = self.composer.toPlainText().strip()
        model = self.model.text().strip()
        if not text and self.attachments:
            text = "Describe these attached images."
        if not text:
            return
        if not model and self.provider.currentData() != "auto":
            self.window.notify("Choose a model identifier before sending.")
            return
        if not self.conversation_id:
            self.conversation_id = self.services.new_conversation()
            self.window.select_context(conversation_id=self.conversation_id)
            clear_layout(self.messages)
        self.composer.clear()
        self.add_message("user", text)
        self.activity.setText("Preparing request…")
        self.timeline.clear()
        self.operator_items.clear()
        self.timeline.setVisible(False)
        self.send_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
        self.attach_button.setEnabled(False)
        self.screen_button.setEnabled(False)
        self.stop.setEnabled(True)
        self.provider.setEnabled(False)
        self.model.setEnabled(False)
        self.history.setEnabled(False)
        provider = self.provider.currentData()
        self.services.settings.set("model." + provider, model)
        selected_images = tuple(self.attachments)
        self.clear_images()
        self.worker = ChatJob(self.services, text, self.conversation_id, provider, model, self, attachments=selected_images)
        self.worker.succeeded.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.activity.connect(self.on_activity)
        self.worker.approval.connect(self.on_approval)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def on_activity(self, kind, data):
        if self.window.closing:
            return
        if kind == "model_selected":
            provider, model = data.get("provider", ""), data.get("model", "")
            title = f"Model · {provider} · {model}"
            self.connection.setText(f"Handled by {provider} · {model}")
            self.activity.setText(title)
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.timeline.addItem(item)
            self.fit_timeline()
            self.timeline.setVisible(True)
            self.timeline.scrollToBottom()
            return
        if kind == "stream_start":
            self.clear_stream()
            return
        if kind == "text_delta":
            if not self.stream_widget:
                self.stream_frame = QFrame()
                self.stream_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                self.stream_frame.setObjectName("Message")
                layout = QVBoxLayout(self.stream_frame)
                layout.setContentsMargins(12, 10, 12, 10)
                layout.addWidget(label("Jarvix · responding", "MessageAuthor"))
                self.stream_widget = MessageText("")
                layout.addWidget(self.stream_widget)
                self.messages.addWidget(self.stream_frame)
            self.stream_buffer += data["text"]
            if not self.stream_timer.isActive():
                self.stream_timer.start()
            self.activity.setText("Receiving response…")
            return
        if kind == "stream_end":
            self.flush_stream()
            if data.get("status") == "tools":
                self.clear_stream()
                self.activity.setText("Planning tool actions…")
            return
        if kind in {"tool", "tool_result", "usage", "operator_session"}:
            if kind == "operator_session":
                self.show_operator_card(data)
                return
            title = "Token usage" if kind == "usage" else data.get("name", "Tool") + " · " + data.get("status", "Result")
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.timeline.addItem(item)
            if kind == "tool_result":
                self.install_action_card(item, data)
            self.fit_timeline()
            self.timeline.setVisible(True)
            self.timeline.scrollToBottom()
        names = {"provider": "Waiting for provider…", "tool": "Running approved tool…", "provider_request": "Waiting for provider…", "tool_start": "Running approved tool…", "tool_result": "Action result", "permission": "Waiting for approval…", "complete": "Response complete"}
        description = names.get(kind, kind.replace("_", " ").capitalize())
        tool = data.get("tool") or data.get("tool_name") or data.get("name")
        self.activity.setText(description + (f" · {tool}" if tool else ""))

    def install_action_card(self, item, data):
        name = data.get("name", "Tool")
        result = data.get("result", data)
        value = result.get("data") or {}
        value = value if isinstance(value, dict) else {}
        title = {"files.move": "Moved file", "files.rename": "Renamed file", "files.copy": "Copied file",
                 "apps.open": "Opened application", "workflows.save": "Saved automation", "workspaces.launch": "Opened workspace",
                 "tasks.create": "Created task", "notes.create": "Created note"}.get(name, name.replace(".", " · "))
        card = QWidget()
        card.setObjectName("ToolRow")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 4, 8, 4)
        summary = QVBoxLayout()
        summary.setSpacing(3)
        summary.addWidget(label(title if result.get("ok", True) else name + " · failed", "SectionTitle"))
        detail = value.get("path") or value.get("name") or result.get("error")
        if value.get("source") and value.get("destination"):
            detail = f"{value['source']} → {value['destination']}"
        if detail:
            summary.addWidget(label(str(detail)[:220], "Muted", True))
        layout.addLayout(summary, 1)
        layout.addStretch()
        layout.addWidget(button("Details", lambda: self.inspect_event(item), "Quiet"))
        cited = evidence_text(value)
        if cited:
            layout.addWidget(button("Sources", lambda: TextPreview(
                "Cited source excerpts", "Local tool evidence; this view does not send it to a provider.",
                cited, self).exec(), "Quiet"))
        if result.get("ok", True) and value.get("undo_id"):
            layout.addWidget(button("Undo", lambda: self.window.open_capabilities(
                "actions.undo", {"id": value["undo_id"]}), "Quiet"))
        elif result.get("ok", True) and value.get("undo_available") and value.get("operation_id"):
            layout.addWidget(button("Undo", lambda: self.window.open_capabilities(
                "files.undo", {"operation_id": value["operation_id"]}), "Quiet"))
        if result.get("ok", True) and name in {"files.move", "files.rename", "files.copy"} and value.get("path"):
            layout.addWidget(button("Reveal", lambda: self.window.open_capabilities(
                "files.reveal", {"path": value["path"]}), "Quiet"))
        if name in {"operator.run", "operator.retry", "operator.replan"} and value.get("id"):
            layout.addWidget(button("Session", lambda: self.window.open_operator(value["id"]), "Quiet"))
        elif name == "notes.create" and value.get("id"):
            layout.addWidget(button("Open note", lambda: self.window.open_note(value["id"]), "Quiet"))
        elif name == "tasks.create" and value.get("id"):
            layout.addWidget(button("Open task", lambda: self.window.open_task(value["id"]), "Quiet"))
        self.timeline.setItemWidget(item, card)
        card.ensurePolished()
        item.setSizeHint(card.sizeHint())

    def show_operator_card(self, session):
        session_id = session.get("id")
        if not session_id:
            return
        item = self.operator_items.get(session_id)
        if item is None:
            item = QListWidgetItem()
            self.operator_items[session_id] = item
            self.timeline.addItem(item)
        steps = session.get("steps", [])
        done = sum(step.get("status") in {"complete", "completed"} for step in steps)
        state = session.get("status", "planning").replace("_", " ")
        item.setData(Qt.ItemDataRole.UserRole, session)
        item.setText(f"Operator · {done}/{len(steps)} steps · {state}")
        card = QWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(label(f"Operator · {done}/{len(steps)} steps · {state}", "SectionTitle"))
        row.addStretch()
        row.addWidget(button("View session", lambda: self.window.open_operator(session_id), "Quiet"))
        self.timeline.setItemWidget(item, card)
        card.ensurePolished()
        item.setSizeHint(card.sizeHint())
        self.fit_timeline()
        self.timeline.show()
        self.activity.setText("Operator · " + state)

    def inspect_event(self, item):
        TextPreview("Action details", "Local result; sharing with the provider requires separate approval.",
                    json.dumps(item.data(Qt.ItemDataRole.UserRole), indent=2, ensure_ascii=False, default=str), self).exec()

    def on_approval(self, bridge):
        try:
            if self.window.closing or not self.worker or self.worker.cancel.is_set():
                return
            self.activity.setText("Your decision is required before continuing")
            dialog = PermissionDialog(bridge.request, self)
            self.pending_dialog = dialog
            bridge.answer = dialog.exec() == QDialog.DialogCode.Accepted
            self.activity.setText("Approved for this request" if bridge.answer else "Request denied")
        finally:
            self.pending_dialog = None
            bridge.ready.set()

    def completed(self, answer):
        if self.window.closing:
            return
        self.render_messages()
        # Test/custom adapters may not persist their result; keep that result visible.
        if not self.services.conversation_messages(self.conversation_id):
            self.add_message("assistant", answer)
        self.activity.setText("Saved locally · tool results were shared only with your approval")
        self.refresh_history()
        self.window.update_status()
        if self.services.settings.get("voice.responses", False) and not self.window.closing and not (self.worker and self.worker.cancel.is_set()):
            self.window.run_job(lambda: self.services.speak(answer), self.window.pages["Voice"].speech_started)

    def failed(self, message):
        if self.window.closing:
            return
        partial = self.stream_buffer
        self.clear_stream()
        self.add_message("assistant", (partial + "\n\n" if partial else "") + "The request could not complete.\n\n" + message)
        self.activity.setText("Request failed · you can try again")
        self.refresh_history()

    def finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.send_button.setEnabled(True)
        self.regenerate_button.setEnabled(True)
        self.attach_button.setEnabled(True)
        self.screen_button.setEnabled(True)
        self.stop.setEnabled(False)
        self.provider.setEnabled(True)
        self.model.setEnabled(True)
        self.history.setEnabled(True)
        self.window.job_finished()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            if self.pending_dialog:
                self.pending_dialog.reject()
            self.activity.setText("Stopping after the current operation returns…")
            self.stop.setEnabled(False)

    def inspect_context(self):
        preview = self.services.context_preview(self.conversation_id, self.provider.currentData(), self.model.text().strip())
        TextPreview("AI context preview", "Conversation context and enabled tools for the next request. Your unsent draft is not included in this preview.", json.dumps(preview, indent=2, ensure_ascii=False, default=str), self).exec()
