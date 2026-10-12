"""A conversation-first frontend; the original ChatPage owns requests and approval."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QListWidgetItem, QPlainTextEdit, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget, QWidgetAction,
)

from ..chat import ChatPage, ImageComposer, MARKDOWN, MessageText
from ..widgets import clear_layout, evidence_text, label
from ..liquid import motion
from ..liquid.components import (
    LiquidButton, LiquidCommandBar, LiquidIconButton, LiquidInspector,
    LiquidMenu, LiquidSurface,
)
from ..liquid.icons import icon
from ..liquid.theme import markdown_stylesheet
from .design import TOKENS


class ConversationComposer(LiquidCommandBar):
    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(TOKENS["conversation_width"])
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size


class NexusToolEvent(QWidget):
    """One expandable execution row, rather than another conversation message."""

    def __init__(self, page, data, state="running"):
        super().__init__()
        self.page, self.data = page, data
        self.setObjectName("NexusToolEvent")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(TOKENS["spacing"][0])
        self.heading = LiquidButton("", self.toggle, "Quiet")
        self.heading.setProperty("nexusEvent", True)
        self.heading.setProperty("nexusSemanticIcon", True)
        self.heading.setProperty("liquidRow", True)
        self.body.addWidget(self.heading)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(TOKENS["spacing"][3], 0, 0, TOKENS["spacing"][1])
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Local action arguments and result")
        self.preview.setMaximumHeight(TOKENS["composer_max_height"])
        details.addWidget(self.preview)
        self.actions = QHBoxLayout()
        details.addLayout(self.actions)
        self.body.addWidget(self.details)
        self.details.hide()
        self.update_result(data, state)

    def toggle(self):
        motion.reveal(self.details, self.details.isHidden())

    def update_result(self, data, state="complete"):
        self.data = data
        name = data.get("name", "Action")
        result = data.get("result", data)
        state = "failed" if result.get("ok") is False else state
        titles = {
            "apps.open": "Opened application", "files.search": "Searched files",
            "files.inspect": "Read file", "files.move": "Moved file",
            "files.copy": "Copied file", "files.rename": "Renamed file",
            "developer.git_status": "Read Git status", "tasks.list": "Read tasks",
            "tasks.create": "Created task", "notes.create": "Created note",
            "workspaces.launch": "Opened workspace", "notes.search": "Searched notes",
            "developer.command_output": "Read terminal output", "developer.find_files": "Found project files",
            "developer.git_changed": "Read changed files", "developer.git_diff": "Read working diff",
        }
        title = titles.get(name, name.replace(".", " · ").replace("_", " "))
        if state == "running":
            title = {"files.search": "Searching files…", "developer.git_status": "Reading Git status…",
                     "developer.command_output": "Reading terminal output…", "files.move": "Moving file…",
                     "apps.open": "Opening application…"}.get(name, "Running · " + title)
        elif state == "failed":
            title = "Failed · " + title
        elif state == "cancelled":
            title = "Cancelled · " + title
        value = result.get("data", {})
        if isinstance(value, dict):
            detail = value.get("name") or value.get("path")
            if detail:
                title += " · " + Path(str(detail)).name
        glyph, role = {"complete": ("check", "success"), "failed": ("alert", "error"),
                       "cancelled": ("square", "muted")}.get(state, ("activity", "muted"))
        self.state = state
        self.heading.setText(title)
        self.heading.setAccessibleName(title + ". Expand action details")
        self.heading.setToolTip(title + " · Click to inspect")
        self.heading.setIcon(icon(glyph, self.page.preferences, role))
        self.preview.setPlainText(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        while self.actions.count():
            control = self.actions.takeAt(0).widget()
            if control:
                control.deleteLater()
        if isinstance(value, dict) and result.get("ok", True):
            if value.get("undo_id"):
                self.actions.addWidget(LiquidButton("Undo", lambda: self.page.window.open_capabilities(
                    "actions.undo", {"id": value["undo_id"]}), "Quiet"))
            elif value.get("undo_available") and value.get("operation_id"):
                self.actions.addWidget(LiquidButton("Undo", lambda: self.page.window.open_capabilities(
                    "files.undo", {"operation_id": value["operation_id"]}), "Quiet"))
            if name in {"files.move", "files.copy", "files.rename"} and value.get("path"):
                self.actions.addWidget(LiquidButton("Reveal file", lambda: self.page.window.open_capabilities(
                    "files.reveal", {"path": value["path"]}), "Quiet"))
            cited = evidence_text(value)
            if cited:
                self.actions.addWidget(LiquidButton("View sources", lambda: self.page.show_details("Sources", cited), "Quiet"))
        self.actions.addStretch()


class NexusChatPage(ChatPage):
    conversation_changed = Signal()

    def __init__(self, window):
        self._ready = False
        self._follow_stream, self._scrolling, self._rendering = True, False, False
        self._pending_selection = None
        super().__init__(window)
        keeper = QWidget(self)
        keeper.setObjectName("NexusChatControllerFields")
        self._keeper = keeper
        retained = (
            self.history, self.history_search, self.provider, self.model, self.task_role,
            self.activity, self.timeline, self.attachment_label, self.attach_button,
            self.screen_button, self.clear_attachments_button, self.regenerate_button,
            self.stop, self.send_button, self.connection,
        )
        for control in retained:
            control.setParent(keeper)
        clear_layout(self.layout)
        keeper.hide()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setObjectName("NexusConversationWorkspace")
        self.split.setChildrenCollapsible(False)
        self.document = QWidget()
        body = QVBoxLayout(self.document)
        body.setContentsMargins(TOKENS["spacing"][3], TOKENS["spacing"][1], TOKENS["spacing"][3], TOKENS["spacing"][3])
        body.setSpacing(TOKENS["spacing"][1])
        header = QHBoxLayout()
        self.conversation_heading = label("New chat", "SectionTitle")
        self.conversation_heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        header.addWidget(self.conversation_heading, 1)
        self.project_button = LiquidButton("Project", self.choose_project, "Quiet")
        self.project_button.setIcon(icon("projects"))
        header.addWidget(self.project_button)
        self.model_toggle = LiquidButton("Model", style="Quiet")
        self.model_menu = LiquidMenu("Model", self.model_toggle)
        model_action = QWidgetAction(self.model_menu)
        self.model_settings = QWidget()
        self.model_settings.setMinimumWidth(TOKENS["workspace_inspector_width"])
        form = QFormLayout(self.model_settings)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.provider.setAccessibleName("Provider")
        form.addRow("Provider", self.provider)
        form.addRow("Model", self.model)
        form.addRow("Task", self.task_role)
        form.addRow(self.connection)
        model_action.setDefaultWidget(self.model_settings)
        self.model_menu.addAction(model_action)
        self.model_toggle.setMenu(self.model_menu)
        header.addWidget(self.model_toggle)
        self.context_button = LiquidButton("Context", self.inspect_context, "Quiet")
        header.addWidget(self.context_button)
        self.more_button = LiquidIconButton("menu", "Conversation actions")
        self.more_menu = LiquidMenu("Conversation", self.more_button)
        for caption, action in (("Rename conversation", self.rename_conversation),
                                ("Pin / unpin conversation", self.pin_conversation),
                                ("Export conversation", self.export_conversation),
                                ("Delete conversation", self.delete_conversation),
                                ("Retry response", self.regenerate),
                                ("Link mission…", self.choose_mission)):
            self.more_menu.addAction(caption, action)
        self.more_menu.aboutToShow.connect(self.update_more_menu)
        self.more_button.setMenu(self.more_menu)
        header.addWidget(self.more_button)
        body.addLayout(header)

        self.state_stack = QStackedWidget()
        self.empty = QWidget()
        self.empty.setObjectName("NexusEmptyConversation")
        empty = QVBoxLayout(self.empty)
        empty.setContentsMargins(0, 0, 0, 0)
        empty.addStretch(5)
        self.mark = label("Jarvix", "NexusTitle")
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.mark)
        self.empty_heading = label("What can I help with?", "Heading")
        self.empty_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.empty_heading)
        self.empty_composer_slot = QVBoxLayout()
        empty.addLayout(self.empty_composer_slot)
        suggestions = QHBoxLayout()
        suggestions.addStretch()
        for title, text in (("Review tasks", "Show today's tasks"), ("Explore a project", "Help me inspect my current project"),
                            ("Find a file", "Help me find a file")):
            suggestions.addWidget(LiquidButton(title, lambda draft=text: self.set_draft(draft), "Quiet"))
        suggestions.addStretch()
        empty.addLayout(suggestions)
        empty.addStretch(7)
        self.state_stack.addWidget(self.empty)

        self.active = QWidget()
        active = QVBoxLayout(self.active)
        active.setContentsMargins(0, 0, 0, 0)
        active.setSpacing(TOKENS["spacing"][1])
        self.transcript = QScrollArea()
        self.transcript.setObjectName("NexusConversationTranscript")
        self.transcript.setFrameShape(QScrollArea.Shape.NoFrame)
        self.transcript.setWidgetResizable(True)
        flow = QWidget()
        flow_body = QHBoxLayout(flow)
        flow_body.setContentsMargins(TOKENS["spacing"][1], 0, TOKENS["spacing"][1], TOKENS["spacing"][3])
        self.messages_widget = QWidget()
        self.messages_widget.setMaximumWidth(TOKENS["conversation_width"])
        self.messages = QVBoxLayout(self.messages_widget)
        self.messages.setContentsMargins(0, TOKENS["spacing"][3], 0, TOKENS["spacing"][3])
        self.messages.setSpacing(TOKENS["spacing"][4])
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        flow_body.addWidget(self.messages_widget, 1, Qt.AlignmentFlag.AlignHCenter)
        self.transcript.setWidget(flow)
        self.transcript.viewport().installEventFilter(self)
        scrollbar = self.transcript.verticalScrollBar()
        scrollbar.valueChanged.connect(self.track_scroll)
        scrollbar.rangeChanged.connect(lambda *_: self.scroll_to_bottom())
        active.addWidget(self.transcript, 1)
        self.active_composer_slot = QVBoxLayout()
        active.addLayout(self.active_composer_slot)
        self.state_stack.addWidget(self.active)
        body.addWidget(self.state_stack, 1)

        self.command_surface = ConversationComposer()
        self.command_surface.setObjectName("NexusFloatingComposer")
        self.command_surface.setMaximumWidth(TOKENS["conversation_width"])
        self.command_surface.body.setContentsMargins(TOKENS["spacing"][2], TOKENS["spacing"][1], TOKENS["spacing"][2], TOKENS["spacing"][1])
        self.command_surface.body.addWidget(self.attachment_label)
        self.composer = ImageComposer()
        self.composer.setObjectName("Composer")
        self.composer.setPlaceholderText("Ask Jarvix anything…")
        self.composer.setAccessibleName("Message Jarvix")
        self.composer.setProperty("nexusReadable", True)
        input_font = self.composer.font()
        input_font.setPointSizeF(TOKENS["font_size"] + 1)
        self.composer.setFont(input_font)
        self.composer.setFixedHeight(TOKENS["control_height"] * 2)
        self.composer.submitted.connect(self.send)
        self.composer.images_dropped.connect(self.add_dropped_files)
        self.composer.image_pasted.connect(self.paste_image)
        self.composer.textChanged.connect(self.fit_composer)
        self.composer.installEventFilter(self)
        self.command_surface.body.addWidget(self.composer)
        controls = QHBoxLayout()
        self.add_button = LiquidIconButton("plus", "Attach a file, image, screenshot or context")
        self.attachment_menu = LiquidMenu("Attach or add context", self.add_button)
        for caption, action in (("Attach file context…", self.choose_files), ("Attach image…", self.choose_images),
                                ("Take screenshot…", self.screen_menu), ("Add project…", self.choose_project),
                                ("Add mission…", self.choose_mission),
                                ("Inspect screen context", lambda: self.window.open_capabilities("context.inspect", {}))):
            self.attachment_menu.addAction(caption, action)
        self.attachment_menu.addSeparator()
        self.attachment_menu.addAction("Remove images", self.clear_images)
        self.add_button.setMenu(self.attachment_menu)
        controls.addWidget(self.add_button)
        self.context_summary = LiquidButton("Conversation only", self.inspect_context, "Quiet")
        controls.addWidget(self.context_summary)
        self.activity.setParent(self.command_surface)
        self.activity.setVisible(False)
        controls.addWidget(self.activity, 1)
        controls.addStretch()
        self.voice_button = LiquidIconButton("voice", "Open voice input", lambda: self.window.navigate("Voice"))
        controls.addWidget(self.voice_button)
        self.send_button.setText("")
        self.send_button.setIcon(icon("send"))
        self.send_button.setAccessibleName("Send message")
        self.send_button.setToolTip("Send message · Enter")
        self.send_button.setFixedWidth(TOKENS["control_height"] + TOKENS["spacing"][1])
        self.stop.setText("Stop")
        controls.addWidget(self.stop)
        controls.addWidget(self.send_button)
        self.stop.hide()
        self.command_surface.body.addLayout(controls)

        self.inspector = LiquidInspector()
        self.inspector.setObjectName("NexusConversationInspector")
        self.inspector.setMinimumWidth(TOKENS["workspace_inspector_width"])
        inspector_header = QHBoxLayout()
        inspector_header.addWidget(label("Context", "SectionTitle"), 1)
        inspector_header.addWidget(LiquidIconButton("x", "Close context panel", self.close_inspector))
        self.inspector.body.addLayout(inspector_header)
        self.inspector_tabs = QTabWidget()
        self.context_pane = QWidget()
        self.context_body = QVBoxLayout(self.context_pane)
        self.inspector_tabs.addTab(self.context_pane, "Context")
        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setAccessibleName("Local context or action details")
        self.inspector_tabs.addTab(self.detail_view, "Details")
        self.inspector.body.addWidget(self.inspector_tabs, 1)
        self.split.addWidget(self.document)
        self.split.addWidget(self.inspector)
        self.split.setStretchFactor(0, 1)
        self.split.setSizes([800, TOKENS["workspace_inspector_width"]])
        self.inspector.hide()
        self.layout.addWidget(self.split, 1)
        self._ready = True
        self.model.textChanged.connect(self.update_model_summary)
        self.provider.currentIndexChanged.connect(self.update_model_summary)
        self.timeline.hide()
        self._event_rows = []
        self._event_group = None
        self.render_messages()
        self.refresh_context_summary()
        self.update_model_summary()
        self.fit_composer()

    @property
    def preferences(self):
        return getattr(motion.owner(self), "_liquid_preferences", getattr(self.window, "_liquid_preferences", {}))

    def set_active(self, active):
        self.state_stack.setCurrentWidget(self.active if active else self.empty)
        slot = self.active_composer_slot if active else self.empty_composer_slot
        if slot.indexOf(self.command_surface) < 0:
            self.active_composer_slot.removeWidget(self.command_surface)
            self.empty_composer_slot.removeWidget(self.command_surface)
            slot.addWidget(self.command_surface, 0, Qt.AlignmentFlag.AlignHCenter)
        self.command_surface.show()

    def render_messages(self):
        if not self._ready:
            return super().render_messages()
        self.clear_stream()
        self._rendering = True
        position = self.transcript.verticalScrollBar().value()
        try:
            clear_layout(self.messages)
            self.last_answer = ""
            self._event_rows = []
            self._event_group = None
            self.operator_items.clear()
            rows = self.services.conversation_messages(self.conversation_id) if self.conversation_id else []
            for index, row in enumerate(rows):
                if row.get("role") in {"user", "assistant"}:
                    self.add_message(row["role"], row["content"], index)
            self.set_active(bool(rows))
            last_reply = next((index for index in range(self.messages.count() - 1, -1, -1)
                               if self.messages.itemAt(index).widget().objectName() == "NexusResponse"), None)
            for index in range(self.timeline.count()):
                data = self.timeline.item(index).data(Qt.ItemDataRole.UserRole)
                state = "running" if data.get("status") == "Running" else "cancelled" if data.get("status") == "Cancelled" else "complete"
                self.append_event(data, state)
                if last_reply is not None and index == 0:
                    self.messages.removeWidget(self._event_group)
                    self.messages.insertWidget(last_reply, self._event_group)
            self.update_conversation_header()
        finally:
            self._rendering = False
        if not self._follow_stream:
            QTimer.singleShot(0, self, lambda: self.restore_scroll(position))
        self.scroll_to_bottom()
        self.conversation_changed.emit()

    def message(self, role, content, index=None, streaming=False):
        frame = LiquidSurface() if role == "user" else QWidget()
        frame.setObjectName("NexusUserMessage" if role == "user" else "NexusResponse")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body = frame.body if isinstance(frame, LiquidSurface) else QVBoxLayout(frame)
        body.setContentsMargins(TOKENS["spacing"][2] if role == "user" else 0, TOKENS["spacing"][1], TOKENS["spacing"][2] if role == "user" else 0, TOKENS["spacing"][1])
        body.setSpacing(TOKENS["spacing"][1])
        heading = QHBoxLayout()
        heading.addWidget(label("You" if role == "user" else "Jarvix", "Caption"))
        heading.addStretch()
        if not streaming:
            more = LiquidIconButton("menu", "Message actions")
            menu = LiquidMenu("Message actions", more)
            menu.addAction("Copy text", lambda: QApplication.clipboard().setText(content))
            if role == "user" and index is not None:
                menu.addAction("Edit and resend", lambda: self.revise_message(index))
            if role == "assistant":
                menu.addAction("Read aloud", lambda: self.window.run_job(lambda: self.services.speak(content), self.window.pages["Voice"].speech_started))
                menu.addAction("Create task", lambda: self.window.guard(lambda: (self.services.add_task(content[:500]), self.window.notify("Task created"))))
                menu.addAction("Save as note", lambda: self.window.guard(lambda: (self.services.save_note("From Jarvix", content), self.window.notify("Note saved"))))
            more.setMenu(menu)
            heading.addWidget(more)
        body.addLayout(heading)
        text = MessageText(content)
        text.setProperty("nexusReadable", True)
        message_font = text.font()
        message_font.setPointSizeF(TOKENS["font_size"] + 1)
        text.setFont(message_font)
        text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        text.document().setDefaultStyleSheet(markdown_stylesheet(self.preferences))
        text.document().setMarkdown(content, MARKDOWN)
        text.fit()
        body.addWidget(text)
        if role == "user":
            frame.setMaximumWidth(TOKENS["conversation_width"] * 4 // 5)
            self.messages.addWidget(frame, 0, Qt.AlignmentFlag.AlignRight)
        else:
            self.messages.addWidget(frame)
        return frame, text

    def add_message(self, role, content, index=None):
        if not self._ready:
            return super().add_message(role, content, index)
        if role == "user" and self._pending_selection is not None:
            selected, self._pending_selection = self._pending_selection, None
            self.window.select_context(conversation_id=self.conversation_id, **{key: selected[key] for key in (
                "project_id", "mission_id", "workspace_id", "selected_files")})
            if selected["project_id"]:
                self.services.conversations.configure(self.conversation_id, project_id=selected["project_id"])
        self.set_active(True)
        self.message(role, content, index)
        if role == "assistant":
            self.last_answer = content
        self.scroll_to_bottom()

    def on_activity(self, kind, data):
        if not self._ready or self.window.closing:
            return
        if kind == "text_delta":
            if not self.stream_widget:
                self.set_active(True)
                self.stream_frame, self.stream_widget = self.message("assistant", "", streaming=True)
            self.stream_buffer += data["text"]
            if not self.stream_timer.isActive():
                self.stream_timer.start()
            self.activity.setText("Responding…")
        elif kind == "stream_start":
            self.clear_stream()
        elif kind == "stream_end":
            self.flush_stream()
            if data.get("status") == "tools":
                self.clear_stream()
        elif kind in {"tool", "tool_result"}:
            state = "complete" if kind == "tool_result" else "cancelled" if data.get("status") == "Cancelled" else "running"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.timeline.addItem(item)
            previous = next((row for row in reversed(self._event_rows) if row.data.get("name") == data.get("name") and row.state == "running"), None)
            if previous and (kind == "tool_result" or state == "cancelled"):
                previous.update_result(data, state)
                for index in range(self.timeline.count() - 2, -1, -1):
                    old = self.timeline.item(index).data(Qt.ItemDataRole.UserRole)
                    if old.get("name") == data.get("name") and old.get("status") == "Running":
                        self.timeline.takeItem(index)
                        break
            else:
                self.append_event(data, state)
            self.activity.setText("Working…" if kind == "tool" else "Action complete" if data.get("result", data).get("ok", True) else "Action failed")
        elif kind == "operator_session":
            self.show_operator_card(data)
        elif kind == "model_selected":
            self.connection.setText(f"Handled by {data.get('provider', '')} · {data.get('model', '')}")
        elif kind == "usage":
            self.model_toggle.setToolTip("Model usage\n" + json.dumps(data, ensure_ascii=False))
        else:
            self.activity.setText({"permission": "Waiting for approval", "provider": "Preparing response…", "complete": "Complete"}.get(kind, "Working…"))
        self.timeline.hide()
        self.activity.setVisible(self.busy)
        self.scroll_to_bottom()

    def append_event(self, data, state):
        if not isinstance(data, dict):
            return
        self.set_active(True)
        if self._event_group is None:
            self._event_group = QWidget()
            self._event_group.setObjectName("NexusExecutionTimeline")
            self._event_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._event_group.body = QVBoxLayout(self._event_group)
            self._event_group.body.setContentsMargins(0, 0, 0, 0)
            self._event_group.body.setSpacing(TOKENS["spacing"][0])
            self.messages.addWidget(self._event_group)
        row = NexusToolEvent(self, data, state)
        self._event_rows.append(row)
        self._event_group.body.addWidget(row)

    def show_operator_card(self, session):
        session_id = session.get("id")
        if not session_id:
            return
        completed = sum(step.get("status") in {"complete", "completed"} for step in session.get("steps", []))
        row = self.operator_items.get(session_id)
        if row is None:
            row = LiquidButton("", lambda: self.window.open_operator(session_id), "Quiet")
            row.setIcon(icon("operator", self.preferences))
            self.operator_items[session_id] = row
            self.messages.addWidget(row)
        row.setText(f"Operator · {completed}/{len(session.get('steps', []))} steps · {session.get('status', 'planning')}")

    def send(self):
        if not self.busy:
            self._follow_stream = True
            self._pending_selection = self.services.context.selection() if self.services.settings.get("context.enabled", False) else None
        try:
            super().send()
        finally:
            self._pending_selection = None
        if self.busy:
            self.send_button.hide()
            self.stop.show()
            self.add_button.setEnabled(False)
            self.project_button.setEnabled(False)
            self.voice_button.setEnabled(False)
            self.model_toggle.setEnabled(False)
            self.activity.show()
            self.timeline.hide()
            if self._event_group is not None:
                self.messages.removeWidget(self._event_group)
                self._event_group.hide()
                self._event_group.deleteLater()
            self._event_rows = []
            self._event_group = None
        self.refresh_context_summary()
        self.update_conversation_header()
        self.conversation_changed.emit()

    def finished(self):
        super().finished()
        self.send_button.show()
        self.stop.hide()
        for control in (self.add_button, self.project_button, self.voice_button, self.model_toggle):
            control.setEnabled(True)
        self.activity.hide()
        self.conversation_changed.emit()

    def new_conversation(self):
        busy = self.busy
        super().new_conversation()
        if self._ready and not busy:
            self._follow_stream = True
            self.activity.hide()
            self.close_inspector()
            self.refresh_context_summary()

    def load_conversation(self, conversation_id):
        if not self.busy:
            self._follow_stream = True
        super().load_conversation(conversation_id)
        if self._ready:
            self.refresh_context_summary()

    def refresh_history(self):
        super().refresh_history()
        if self._ready:
            self.update_conversation_header()
            self.conversation_changed.emit()

    def refresh(self):
        super().refresh()
        if self._ready:
            self.refresh_context_summary()

    def update_conversation_header(self):
        record = next((row for row in self.services.list_conversations() if row["id"] == self.conversation_id), None)
        self.conversation_heading.setText(record.get("title", "Conversation") if record else "New chat")
        self.conversation_heading.setToolTip(self.conversation_heading.text())

    def update_more_menu(self):
        for action in self.more_menu.actions():
            action.setEnabled(not self.busy and (bool(self.conversation_id) or action.text() == "Link mission…"))

    def update_connection(self):
        super().update_connection()
        if self._ready:
            self.update_model_summary()

    def update_model_summary(self, *_):
        model = self.model.text().strip()
        friendly = model.rsplit("/", 1)[-1].replace("-", " ") if model else "Automatic"
        self.model_toggle.setText(friendly if len(friendly) <= 25 else friendly[:23] + "…")
        self.model_toggle.setToolTip(f"{self.provider.currentText()} · {model}\n{self.connection.text()}")

    def choose_files(self):
        if self.busy:
            return
        if not self.services.settings.get("context.enabled", False):
            self.window.notify("Enable context permission in Settings before adding files.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add file context")
        if paths:
            self.window.guard(lambda: self.services.context.set_current(selected_files=paths))
            self.refresh_context_summary()
            self.inspect_context()

    def add_dropped_files(self, paths):
        if self.busy:
            return
        images = [path for path in paths if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}]
        if images:
            self.add_images(images)
        documents = [path for path in paths if path not in images]
        if documents:
            if not self.services.settings.get("context.enabled", False):
                self.window.notify("Enable context permission in Settings before adding files.")
                return
            self.window.guard(lambda: self.services.context.set_current(selected_files=documents))
            self.refresh_context_summary()

    def choose_project(self):
        self.choose_resource("project", self.services.list_projects())

    def choose_mission(self):
        self.choose_resource("mission", self.services.missions.list()["items"])

    def choose_resource(self, kind, rows):
        if self.busy:
            return
        names = [row.get("name", row.get("goal", "Untitled")) for row in rows]
        if not names:
            self.window.notify(f"Create a {kind} before adding its context.")
            return
        name, accepted = QInputDialog.getItem(self, f"Choose {kind}", kind.title(), names, 0, False)
        if not accepted:
            return
        resource = rows[names.index(name)]
        if self.conversation_id and kind == "project":
            self.window.guard(lambda: self.services.conversations.configure(self.conversation_id, project_id=resource["id"]))
        elif self.conversation_id:
            self.window.open_capabilities("context.link", {"source": {"kind": "conversation", "reference": self.conversation_id},
                "target": {"kind": "mission", "reference": resource["id"]}, "relation": "related"})
        if self.services.settings.get("context.enabled", False):
            self.window.select_context(**{kind + "_id": resource["id"]})
        else:
            self.window.notify("Enable context in Settings to include this source in a request.")
        self.refresh_context_summary()
        self.conversation_changed.emit()

    def refresh_context_summary(self):
        if not self._ready:
            return
        pieces = []
        if self.services.settings.get("context.enabled", False):
            selected = self.services.context.selection()
            project = next((row for row in self.services.list_projects() if row["id"] == selected["project_id"]), None)
            if project:
                pieces.append(project["name"])
                self.project_button.setText(project["name"])
            else:
                self.project_button.setText("Project")
            if selected["selected_files"]:
                count = len(selected["selected_files"])
                pieces.append(f"{count} local file{'s' if count != 1 else ''}")
            if selected["mission_id"]:
                pieces.append("Mission")
        self.context_summary.setText(" · ".join(pieces) or "Conversation only")

    def inspect_context(self):
        clear_layout(self.context_body)
        self.refresh_context_summary()
        self.context_body.addWidget(label(self.context_summary.text(), "SectionTitle", True))
        selected = {}
        if self.services.settings.get("context.enabled", False):
            selected = self.services.context.selection()
            project = next((row for row in self.services.list_projects() if row["id"] == selected["project_id"]), None)
            if project:
                self.context_body.addWidget(label("Project", "Caption"))
                for caption, tool in (("Project files", "developer.find_files"), ("Changed files", "developer.git_changed"),
                                      ("Working diff", "developer.git_diff")):
                    control = LiquidButton(caption, lambda name=caption, command=tool, root=project["path"]:
                        self.read_context_tool(name, command, {"path": root}), "Quiet")
                    control.setProperty("liquidRow", True)
                    self.context_body.addWidget(control)
                terminal = LiquidButton("Terminal output", self.choose_terminal_output, "Quiet")
                terminal.setProperty("liquidRow", True)
                self.context_body.addWidget(terminal)
            for path in selected["selected_files"]:
                row = LiquidButton(Path(path).name, lambda selected=path: self.window.open_capabilities("files.inspect", {"path": selected}), "Quiet")
                row.setToolTip(path)
                self.context_body.addWidget(row)
        self.context_body.addWidget(label("Local resources stay local until you approve sharing.", "Muted", True))
        self.context_body.addWidget(LiquidButton("Inspect exact request", self.exact_context, "Quiet"))
        if self.services.settings.get("context.enabled", False) and any(
                selected.get(key) for key in ("project_id", "mission_id", "workspace_id", "selected_files")):
            self.context_body.addWidget(LiquidButton("Remove selected context", self.remove_context, "Quiet"))
        self.context_body.addStretch()
        self.inspector_tabs.setCurrentIndex(0)
        motion.reveal(self.inspector, True)

    def remove_context(self):
        self.window.select_context(project_id="", mission_id="", workspace_id="", selected_files=[])
        self.inspect_context()

    def read_context_tool(self, title, tool, arguments):
        self.show_details(title, "Loading…")
        def done(result):
            if not result.ok:
                self.detail_view.setPlainText(result.error or "The requested context is unavailable.")
                return
            value = result.data
            if isinstance(value, dict):
                if "text" in value or "output" in value:
                    text = value.get("text", value.get("output", "")) or "No output."
                elif "working" in value or "staged" in value:
                    text = "Working changes\n" + ("\n".join(value.get("working", [])) or "None") + "\n\nStaged changes\n" + ("\n".join(value.get("staged", [])) or "None")
                elif "items" in value:
                    text = "\n".join(str(item.get("path", item.get("name", ""))) for item in value["items"]) or "No files found."
                else:
                    text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
                if value.get("truncated"):
                    text += "\n\nMore content is available. Narrow the selection."
            else:
                text = str(value)
            self.detail_view.setPlainText(text)
        self.window.run_job(lambda: self.services.execute_tool(tool, arguments), done,
                            lambda error: self.detail_view.setPlainText(error))

    def choose_terminal_output(self):
        def choose(result):
            if not result.ok:
                self.show_details("Terminal output", result.error or "Command history is unavailable.")
                return
            rows = [row for row in result.data["items"] if row.get("status") != "previous_session"]
            if not rows:
                self.show_details("Terminal output", "No command output is available in this Jarvix session.")
                return
            names = [f"{row.get('executable', 'Command')} · {row.get('cwd', '')} · {row['id']}" for row in rows]
            selected, accepted = QInputDialog.getItem(self, "Terminal output", "Owned command", names, 0, False)
            if accepted:
                self.read_context_tool("Terminal output", "developer.command_output", {"session_id": rows[names.index(selected)]["id"]})
        self.window.run_job(lambda: self.services.execute_tool("developer.command_history", {"limit": 30}), choose)

    def exact_context(self):
        preview = self.services.context_preview(self.conversation_id, self.provider.currentData(), self.model.text().strip())
        self.show_details("Request context", json.dumps(preview, indent=2, ensure_ascii=False, default=str))

    def show_details(self, title, text):
        self.inspector_tabs.setTabText(1, title)
        self.detail_view.setPlainText(text)
        self.inspector_tabs.setCurrentIndex(1)
        motion.reveal(self.inspector, True)

    def close_inspector(self):
        motion.reveal(self.inspector, False)

    def fit_composer(self):
        height = self.composer.document().blockCount() * self.composer.fontMetrics().lineSpacing() + TOKENS["spacing"][3]
        self.composer.setFixedHeight(max(TOKENS["control_height"] * 2, min(height, TOKENS["composer_max_height"])))

    def track_scroll(self, value):
        if not self._scrolling and not self._rendering:
            self._follow_stream = self.transcript.verticalScrollBar().maximum() - value <= TOKENS["control_height"]

    def restore_scroll(self, position):
        if not self._follow_stream:
            self._scrolling = True
            self.transcript.verticalScrollBar().setValue(position)
            self._scrolling = False

    def scroll_to_bottom(self):
        if self._follow_stream:
            QTimer.singleShot(30, self, self.follow_stream)

    def follow_stream(self):
        if self._follow_stream:
            self._scrolling = True
            self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
            self._scrolling = False

    def refresh_materials(self):
        for text in self.messages_widget.findChildren(MessageText):
            content = text.document().toMarkdown(MARKDOWN)
            text.document().setDefaultStyleSheet(markdown_stylesheet(self.preferences))
            text.document().setMarkdown(content, MARKDOWN)
        for row in self._event_rows:
            row.update_result(row.data, row.state)

    def eventFilter(self, watched, event):
        if watched is self.composer and event.type() in {QEvent.Type.FocusIn, QEvent.Type.FocusOut}:
            self.command_surface.focus_material(event.type() == QEvent.Type.FocusIn)
        elif watched is self.transcript.viewport() and event.type() == QEvent.Type.Resize:
            width = min(TOKENS["conversation_width"], max(1, self.transcript.viewport().width() - TOKENS["spacing"][3]))
            self.messages_widget.setFixedWidth(width)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._ready and self.width() < TOKENS["workspace_breakpoint"] and not self.inspector.isHidden():
            self.close_inspector()
