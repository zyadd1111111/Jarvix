"""Nexus conversation presentation with the existing permissioned Chat controller."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QFormLayout

from ..chat import ChatPage, MessageText, MARKDOWN
from ..widgets import button, clear_layout
from .icons import icon
from .materials import GlassPanel, GlassCommandSurface
from .theme import markdown_stylesheet


class NexusChatPage(ChatPage):
    def __init__(self, window):
        super().__init__(window)
        old_split = self.findChild(QSplitter, "ChatSplit")
        history = old_split.widget(0)
        old_content = old_split.widget(1)
        history.setParent(self)
        keep = (self.provider, self.model, self.task_role, self.context_button, self.transcript,
                self.activity, self.timeline, self.attachment_label, self.composer, self.attach_button,
                self.screen_button, self.clear_attachments_button, self.regenerate_button,
                self.stop, self.send_button, self.connection)
        for widget in keep:
            widget.setParent(self)
        voice = next(control for control in old_content.findChildren(type(self.attach_button)) if control.text() == "Voice")
        voice.setParent(self)
        clear_layout(self.layout)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.split = QSplitter(self)
        self.split.setObjectName("NexusChatSplit")
        self.split.setChildrenCollapsible(False)
        self._history_user_hidden = False
        self.history_pane = GlassPanel(self.split)
        self.history_pane.body.addWidget(history, 1)
        self.split.addWidget(self.history_pane)
        content = QWidget(self.split)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.conversation_pane = GlassPanel(content)
        heading = QHBoxLayout()
        self.history_toggle = button("Conversations", self.toggle_history, "Quiet")
        self.history_toggle.setIcon(icon("chat"))
        heading.addWidget(self.history_toggle)
        heading.addStretch()
        self.model_toggle = button("Model", style="Quiet")
        self.model_toggle.setIcon(icon("settings"))
        self.model_toggle.setCheckable(True)
        self.model_toggle.setAccessibleName("Show model and routing settings")
        heading.addWidget(self.model_toggle)
        self.conversation_pane.body.addLayout(heading)
        self.conversation_pane.body.addWidget(self.transcript, 1)
        self.conversation_pane.body.addWidget(self.activity)
        self.conversation_pane.body.addWidget(self.timeline)
        body.addWidget(self.conversation_pane, 1)
        self.command_surface = GlassCommandSurface(content)
        self.model_settings = QWidget(self.command_surface)
        model_form = QFormLayout(self.model_settings)
        model_form.setContentsMargins(0, 0, 0, 0)
        model_form.setSpacing(6)
        model_form.addRow("Provider", self.provider)
        model_form.addRow("Model", self.model)
        model_form.addRow("Routing role", self.task_role)
        model_form.addRow(self.connection)
        self.command_surface.body.addWidget(self.model_settings)
        self.model_toggle.toggled.connect(self.model_settings.setVisible)
        self.model_settings.hide()
        self.command_surface.body.addWidget(self.attachment_label)
        self.composer.setFixedHeight(74)
        self.command_surface.body.addWidget(self.composer)
        controls = QHBoxLayout()
        self.attach_button.setText("Images")
        self.attach_button.setIcon(icon("plus"))
        self.screen_button.setText("Screen")
        self.screen_button.setIcon(icon("system"))
        self.context_button.setText("Context")
        for control in (self.attach_button, self.screen_button, voice, self.context_button):
            controls.addWidget(control)
        controls.addStretch()
        self.command_surface.body.addLayout(controls)
        actions = QHBoxLayout()
        actions.addWidget(self.regenerate_button)
        actions.addWidget(self.clear_attachments_button)
        actions.addStretch()
        actions.addWidget(self.stop)
        self.send_button.setText("Send")
        self.send_button.setAccessibleName("Send message")
        actions.addWidget(self.send_button)
        self.command_surface.body.addLayout(actions)
        body.addWidget(self.command_surface)
        self.split.addWidget(content)
        self.split.setSizes([205, 795])
        self.layout.addWidget(self.split, 1)
        for widget in (*keep, voice, history):
            widget.show()
        self.timeline.setVisible(self.timeline.count() > 0)
        self.attachment_label.setVisible(bool(self.attachments))
        self.clear_attachments_button.setVisible(bool(self.attachments))
        self.model_settings.hide()
        self.update_model_summary()
        self.provider.currentIndexChanged.connect(self.update_model_summary)
        self.model.textChanged.connect(self.update_model_summary)
        self.composer.setFocus()

    def toggle_history(self):
        show = not self.history_pane.isVisible()
        self._history_user_hidden = not show
        self.history_pane.setVisible(show)

    def update_model_summary(self, *_):
        if hasattr(self, "model_toggle"):
            provider = self.provider.currentText().replace(" · local", "")
            self.model_toggle.setText(provider)
            self.model_toggle.setToolTip(f"{provider} · {self.model.text() or 'Choose a model'}\nShow model and routing settings")

    @staticmethod
    def style_message(widget, content):
        widget.document().setDefaultStyleSheet(markdown_stylesheet())
        widget.document().setMarkdown(content, MARKDOWN)
        widget.fit()

    def add_message(self, role, content, index=None):
        super().add_message(role, content, index)
        frame = self.messages.itemAt(self.messages.count() - 1).widget()
        self.style_message(frame.findChild(MessageText), content)

    def on_activity(self, kind, data):
        super().on_activity(kind, data)
        if kind == "text_delta" and self.stream_widget:
            self.stream_widget.document().setDefaultStyleSheet(markdown_stylesheet())

    def update_connection(self):
        super().update_connection()
        self.update_model_summary()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "history_pane"):
            self.history_pane.setVisible(self.width() >= 720 and not self._history_user_hidden)

