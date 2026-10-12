"""Command-led opening workspace. Local rows, not a telemetry dashboard."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QScrollArea, QFrame, QHBoxLayout, QSizePolicy

from ..widgets import label, clear_layout
from .design import TOKENS, pane, NexusComposer, NexusButton, NexusIconButton


class NexusHomePage(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window, self.services = window, window.services
        self.setObjectName("NexusWorkspace")
        from PySide6.QtWidgets import QVBoxLayout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(TOKENS["page_inset"], 20, TOKENS["page_inset"], 12)
        self.layout.setSpacing(TOKENS["section_gap"])
        self.content = pane(self)
        self.content.body.setSpacing(TOKENS["section_gap"])
        header = QHBoxLayout()
        self.greeting = label("", "NexusTitle")
        self.date_label = label("", "Muted")
        header.addWidget(self.greeting, 1)
        header.addWidget(self.date_label)
        self.content.body.addLayout(header)
        self.command_surface = NexusComposer(self.content)
        self.command_surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.command = QPlainTextEdit()
        self.command.setObjectName("NexusCommandInput")
        self.command.setPlaceholderText("Ask Jarvix or tell it to do something…")
        self.command.setAccessibleName("Ask Jarvix or tell it to do something")
        self.command.setFixedHeight(58)
        self.command.setFrameShape(QFrame.Shape.NoFrame)
        self.command_surface.body.addWidget(self.command)
        commands = QHBoxLayout()
        commands.addWidget(NexusIconButton("plus", "Add files or context", self.attach))
        commands.addWidget(NexusButton("Voice", lambda: window.navigate("Voice"), "Quiet"))
        commands.addWidget(NexusButton("Screen", self.screen, "Quiet"))
        commands.addStretch()
        commands.addWidget(NexusButton("Ask Jarvix", self.submit, "Primary"))
        self.command_surface.body.addLayout(commands)
        self.content.body.addWidget(self.command_surface)
        suggestions = QHBoxLayout()
        for text, callback in (
            ("Continue Jarvix", lambda: window.open_adaptive("Continue")),
            ("Review today's tasks", lambda: window.navigate("Tasks")),
            ("Open coding workspace", window.open_workspaces),
            ("Search files", lambda: window.navigate("Files")),
        ):
            suggestions.addWidget(NexusButton(text, callback, "Quiet"))
        suggestions.addStretch()
        self.content.body.addLayout(suggestions)
        split = pane(self.content, horizontal=True)
        split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        split.body.setSpacing(36)
        self.main = pane(split)
        self.side = pane(split)
        self.side.setMaximumWidth(TOKENS["inspector_width"])
        split.body.addWidget(self.main, 2)
        split.body.addWidget(self.side, 1)
        self.content.body.addWidget(split)
        self.recent = pane(self.content)
        self.content.body.addWidget(self.recent)
        scroll = QScrollArea(self)
        scroll.setObjectName("NexusHomeScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.content)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.content.setMaximumWidth(TOKENS["content_width"])
        self.layout.addWidget(scroll, 1)

    def submit(self):
        text = self.command.toPlainText().strip()
        if text:
            self.window.navigate("Chat")
            if self.window.current_page != "Chat":
                return
            self.window.open_chat(text)
            if self.window.current_page == "Chat":
                self.command.clear()

    def attach(self):
        self.window.navigate("Chat")
        if self.window.current_page != "Chat":
            return
        self.window.open_chat(self.command.toPlainText(), send=False)
        chat = self.window.pages["Chat"]
        if self.window.current_page == "Chat":
            chat.attachment_menu.popup(chat.add_button.mapToGlobal(chat.add_button.rect().bottomLeft()))

    def screen(self):
        self.window.navigate("Chat")
        if self.window.current_page != "Chat":
            return
        self.window.open_chat(self.command.toPlainText(), send=False)
        if self.window.current_page == "Chat":
            self.window.pages["Chat"].screen_menu()

    def row(self, destination, kind, title, metadata, callback):
        row = pane(destination, horizontal=True)
        category = label(kind, "Muted")
        category.setMinimumWidth(70)
        row.body.addWidget(category)
        name = label(title, wrap=True)
        name.setTextFormat(Qt.TextFormat.PlainText)
        row.body.addWidget(name, 1)
        row.body.addWidget(label(metadata, "Muted"))
        row.body.addWidget(NexusButton("Open", callback, "Quiet"))
        destination.body.addWidget(row)

    def refresh(self):
        now = datetime.now()
        self.greeting.setText("Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening")
        self.date_label.setText(now.strftime("%A, %b %d"))
        for part in (self.main, self.side, self.recent):
            clear_layout(part.body)
        self.main.body.addWidget(label("Continue", "NexusSection"))
        missions = [row for row in self.services.records.list("mission") if row.get("status") not in ("archived", "cancelled", "complete")]
        projects = self.services.list_projects()
        sessions = self.services.operator.list()
        if missions:
            row = missions[0]
            self.row(self.main, "Mission", row.get("goal", "Mission"), row.get("status", ""), lambda: self.window.navigate("Missions"))
        if projects:
            row = projects[0]
            self.row(self.main, "Project", row["name"], "Local", lambda: self.window.navigate("Projects"))
        if sessions:
            row = sessions[0]
            self.row(self.main, "Operator", row.get("goal", "Saved session"), row.get("status", ""), self.window.open_operator)
        if not missions and not projects and not sessions:
            self.main.body.addWidget(label("No saved work yet. Start a conversation or add a project.", "Muted", True))
        self.main.body.addSpacing(TOKENS["section_gap"])
        self.main.body.addWidget(label("Today", "NexusSection"))
        tasks = [task for task in self.services.productivity.tasks.search(view="today")["items"] if task.get("status") != "done"]
        for task in tasks[:4]:
            self.row(self.main, "Task", task["title"], str(task.get("due_at") or "No date")[:10],
                     lambda selected=task: self.window.open_task(selected["id"]))
        if not tasks:
            self.main.body.addWidget(label("No tasks due today.", "Muted"))
        self.side.body.addWidget(label("Calendar & notifications", "NexusSection"))
        calendar = next((item for item in self.services.integrations.status() if item["id"] == "google_calendar"), {})
        self.side.body.addWidget(label("Calendar · " + calendar.get("status", "Not connected"), "Muted", True))
        self.side.body.addWidget(NexusButton("Calendar connection", lambda: self.window.navigate("Integrations"), "Quiet"))
        unread = self.services.notifications.list(unread_only=True)
        self.side.body.addWidget(label(f"{len(unread)} notifications", "Muted"))
        self.side.body.addWidget(NexusButton("Review notifications", self.window.open_notifications, "Quiet"))
        self.side.body.addSpacing(TOKENS["section_gap"])
        self.side.body.addWidget(label("On this PC", "NexusSection"))
        snapshot = self.window.snapshot
        system_text = (f"CPU {snapshot['cpu_percent']}% · Memory {snapshot['memory_percent']}%"
                       if "cpu_percent" in snapshot and "memory_percent" in snapshot else "Checking system state…")
        self.side.body.addWidget(label(system_text, "Muted"))
        self.side.body.addStretch()
        self.recent.body.addWidget(label("Recent", "NexusSection"))
        for conversation in self.services.conversations.search("")[:3]:
            self.row(self.recent, "Chat", conversation["title"], "",
                     lambda selected=conversation: self.window.open_conversation(selected["id"]))
        for record in self.services.list_files()[:2]:
            self.row(self.recent, "File", record["name"], "Local",
                     lambda selected=record: self.window.open_capabilities("files.open", {"path": selected["path"]}))
        for workspace in self.services.workspaces.list()["items"][:2]:
            self.row(self.recent, "Workspace", workspace["name"], "Saved",
                     lambda selected=workspace: self.window.open_capabilities("workspaces.launch", {"id": selected["id"]}))
