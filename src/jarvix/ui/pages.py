"""Local workspace pages. All persistence and operating-system work uses services."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QComboBox, QListWidget, QListWidgetItem, QTableWidgetItem, QSplitter,
    QFileDialog, QInputDialog, QProgressBar, QCheckBox, QSpinBox,
    QScrollArea, QFrame, QMenu, QStackedWidget, QHeaderView, QTabWidget, QLayout,
)

from .widgets import label, button, clear_layout, table, TextPreview
from .icons import icon
from .theme import TOKENS

SPACE = TOKENS["spacing"]


class RecordItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(Qt.ItemDataRole.UserRole + 1)
        right = other.data(Qt.ItemDataRole.UserRole + 1)
        if left is not None and right is not None and type(left) is type(right):
            return left < right
        return self.text().casefold() < other.text().casefold()


def pretty_date(value) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%b %d · %H:%M")
    except (ValueError, TypeError):
        return str(value)


def fill_table(widget, rows, columns):
    sorting = widget.isSortingEnabled()
    widget.setSortingEnabled(False)
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for col_index, column in enumerate(columns):
            value = column(row) if callable(column) else row.get(column, "")
            item = RecordItem(str(value if value is not None else "—"))
            item.setToolTip(item.text())
            item.setData(Qt.ItemDataRole.UserRole, row)
            sort_keys = widget.property("sort_keys") or []
            if col_index < len(sort_keys):
                item.setData(Qt.ItemDataRole.UserRole + 1, row.get(sort_keys[col_index]))
            widget.setItem(row_index, col_index, item)
    widget.setSortingEnabled(sorting)


def selected_record(widget):
    selected = widget.selectedItems()
    return selected[0].data(Qt.ItemDataRole.UserRole) if selected else None


def context_menu(widget, actions):
    """Native menu actions use the same callbacks as their visible controls."""
    widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    def show(position):
        index = widget.indexAt(position)
        if not index.isValid():
            return
        widget.selectRow(index.row())
        menu = QMenu(widget)
        for caption, callback, symbol in actions:
            action = menu.addAction(icon(symbol), caption)
            action.triggered.connect(lambda _checked=False, action=callback: action())
        menu.exec(widget.viewport().mapToGlobal(position))
    widget.customContextMenuRequested.connect(show)


class Page(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.services = window.services
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(SPACE[3], SPACE[3], SPACE[3], SPACE[3])
        self.layout.setSpacing(SPACE[2])
        heading = QHBoxLayout()
        heading.addWidget(label(self.title, "Title"))
        heading.addStretch()
        self.page_description = label(self.subtitle, "Muted", True)
        self.page_description.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(self.page_description, 1)
        self.layout.addLayout(heading)

    def refresh(self):
        pass

    def guard(self, action):
        return self.window.guard(action)


class HomePage(Page):
    title = "Home"
    subtitle = "Current work and local activity"

    def __init__(self, window):
        super().__init__(window)
        self.greeting = label("", "Muted")
        self.date_label = label("", "Muted")
        date_row = QHBoxLayout()
        date_row.addWidget(self.greeting)
        date_row.addStretch()
        date_row.addWidget(self.date_label)
        self.layout.addLayout(date_row)
        command_row = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setObjectName("CommandInput")
        self.command.setPlaceholderText("Ask Jarvix or tell it to do something…")
        self.command.setAccessibleName("Universal Jarvix command")
        self.command.returnPressed.connect(self.submit)
        command_row.addWidget(self.command, 1)
        command_row.addWidget(button("Ask Jarvix", self.submit, "Primary"))
        self.layout.addLayout(command_row)
        shortcuts = QHBoxLayout()
        shortcuts.addWidget(button("Continue saved work", lambda: window.open_adaptive("Continue"), "Quiet"))
        shortcuts.addWidget(button("Manage routines", lambda: window.navigate("Automations"), "Quiet"))
        shortcuts.addWidget(button("Inspect context", lambda: window.open_adaptive("Personal context"), "Quiet"))
        shortcuts.addStretch()
        self.layout.addLayout(shortcuts)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.scroll_body = body
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(SPACE[3])
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        self.sections = QWidget()
        self.section_columns = QHBoxLayout(self.sections)
        self.section_columns.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.section_columns.setContentsMargins(0, 0, 0, 0)
        self.section_columns.setSpacing(SPACE[3])
        self.body_layout.addWidget(self.sections)
        current = self.column("CURRENT")
        today = self.column("TODAY")
        jarvix = self.column("JARVIX")
        self.operator_panel, self.operator_layout = self.group(current)
        self.mission_panel, self.mission_layout = self.group(current)
        self.workspace_panel, self.workspace_layout = self.group(current)
        self.tasks_panel, self.tasks_layout = self.group(today)
        self.daily_panel, self.daily_layout = self.group(today)
        self.daily_panel.hide()
        self._daily_revision = 0
        self.connections_panel, self.connections_layout = self.group(jarvix)
        self.automation_panel, self.automation_layout = self.group(jarvix)
        self.favorites_panel, self.favorites_layout = self.group(jarvix)
        for column in (current, today, jarvix):
            column.addStretch()
        self.health_panel, self.health_layout = self.group(self.body_layout)
        recent = QWidget()
        recent_columns = QHBoxLayout(recent)
        recent_columns.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        recent_columns.setContentsMargins(0, 0, 0, 0)
        recent_columns.setSpacing(SPACE[3])
        self.body_layout.addWidget(label("RECENT", "Eyebrow"))
        self.body_layout.addWidget(recent)
        for name in ("recent", "history", "activity"):
            container = QWidget()
            content = QVBoxLayout(container)
            content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            content.setContentsMargins(0, 0, SPACE[1], 0)
            content.setSpacing(SPACE[0])
            recent_columns.addWidget(container, 1)
            setattr(self, name + "_panel", container)
            setattr(self, name + "_layout", content)
        self.body_layout.addStretch()

    def column(self, title):
        container = QWidget()
        content = QVBoxLayout(container)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        content.setContentsMargins(0, 0, SPACE[2], 0)
        content.setSpacing(SPACE[2])
        content.addWidget(label(title, "Eyebrow"))
        self.section_columns.addWidget(container, 1)
        return content

    @staticmethod
    def group(parent):
        frame = QWidget()
        content = QVBoxLayout(frame)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(SPACE[0])
        parent.addWidget(frame)
        return frame, content

    def submit(self):
        text = self.command.text().strip()
        if text:
            self.command.clear()
            self.window.open_chat(text)

    def section(self, layout, title, link=None):
        clear_layout(layout)
        row = QHBoxLayout()
        row.addWidget(label(title, "Heading"))
        row.addStretch()
        if link:
            row.addWidget(button("Open " + link.lower(), lambda: self.window.navigate(link), "Quiet"))
        layout.addLayout(row)

    def refresh(self):
        now = datetime.now()
        self.greeting.setText("Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening")
        self.date_label.setText(now.strftime("%a, %b %d · %I:%M %p"))
        self.section(self.operator_layout, "Operator")
        sessions = self.services.operator.list() if hasattr(self.services, "operator") else []
        current = next((row for row in sessions if row.get("status") in {"running", "paused", "planning"}), None)
        if current:
            steps = current.get("steps", [])
            done = sum(step.get("status") in {"completed", "succeeded", "complete"} for step in steps)
            self.operator_layout.addWidget(label(current.get("goal", "Operator task"), wrap=True))
            self.operator_layout.addWidget(label(f"{current.get('status', 'ready').capitalize()} · {done}/{len(steps)} steps", "Muted"))
            progress = QProgressBar()
            progress.setValue(int(current.get("progress_percent", 100 * done / max(1, len(steps)))))
            self.operator_layout.addWidget(progress)
            self.operator_layout.addWidget(button("Open session controls", lambda r=current: self.window.open_operator(r["id"]), "Quiet"))
        else:
            self.operator_layout.addWidget(label("No task running.", "Muted"))
            self.operator_layout.addWidget(button("View operator sessions", self.window.open_operator, "Quiet"))
        self.section(self.mission_layout, "Mission")
        missions = self.services.execute_tool("missions.list", {})
        active_missions = [mission for mission in missions.data.get("items", [])
                           if mission["status"] in {"active", "paused"}] if missions.ok else []
        for mission in active_missions[:2]:
            self.mission_layout.addWidget(button(mission["goal"], lambda record=mission:
                self.window.open_capabilities("missions.summary", {"id": record["id"]}), "Quiet"))
            self.mission_layout.addWidget(label(mission["status"].capitalize(), "Muted"))
        if not active_missions:
            self.mission_layout.addWidget(label("No active mission." if missions.ok else "Mission list unavailable.", "Muted"))
        self.mission_layout.addWidget(button("Manage missions", lambda: self.window.open_adaptive("Missions"), "Quiet"))
        self.section(self.workspace_layout, "Project & workspace")
        projects = self.services.list_projects()
        for project in projects[:2]:
            self.workspace_layout.addWidget(button(project["name"], lambda record=project:
                self.window.open_capabilities("intelligence.prepare", {"project_id": record["id"]}), "Quiet"))
        if not projects:
            self.workspace_layout.addWidget(label("No project registered. Add a folder in Projects.", "Muted", True))
        for workspace in self.services.records.list("workspace")[:2]:
            self.workspace_layout.addWidget(button("Launch " + workspace["name"], lambda record=workspace:
                self.window.open_capabilities("workspaces.launch", {"id": record["id"]}), "Quiet"))
        self.workspace_layout.addWidget(button("Manage workspaces", self.window.open_workspaces, "Quiet"))
        self.section(self.tasks_layout, "Tasks", "Tasks")
        today_tasks = self.services.productivity.tasks.search(view="today")["items"]
        for task in today_tasks[:5]:
            row = QHBoxLayout()
            row.addWidget(label(task["title"], wrap=True), 1)
            if task.get("due_at"):
                row.addWidget(label(pretty_date(task["due_at"]), "Muted"))
            self.tasks_layout.addLayout(row)
        if not today_tasks:
            self.tasks_layout.addWidget(label("No open tasks due today.", "Muted"))
        self.tasks_layout.addWidget(button("Create a task", lambda: self.window.navigate("Tasks"), "Quiet"))
        self._daily_revision += 1
        revision = self._daily_revision
        enabled = self.services.settings.get("daily.enabled", False)
        self.daily_panel.setVisible(enabled)
        if enabled:
            self.section(self.daily_layout, "Daily brief")
            self.daily_layout.addWidget(label("Reading approved local sources…", "Muted"))
            def work():
                result = self.services.execute_tool("daily.brief", {})
                if not result.ok:
                    raise ValueError(result.error or "Daily brief unavailable.")
                return result.data
            def loaded(value):
                if revision != self._daily_revision or self.window.closing:
                    return
                self.section(self.daily_layout, "Daily brief")
                for section in value.get("sections", [])[:6]:
                    self.daily_layout.addWidget(label(f"{section['source'].replace('_', ' ').title()} · "
                        f"{len(section.get('items', []))} items · {section['status'].replace('_', ' ')}", "Muted", True))
                self.daily_layout.addWidget(button("Read daily brief", lambda: self.window.open_adaptive("Daily brief"), "Quiet"))
            def failed(message):
                if revision == self._daily_revision and not self.window.closing:
                    self.section(self.daily_layout, "Daily brief")
                    self.daily_layout.addWidget(label(message, "Muted", True))
            self.window.run_job(work, loaded, failed)
        self.section(self.connections_layout, "AI model", "Integrations")
        from jarvix.providers import DEFAULT_MODELS
        provider = self.services.settings.get("provider", "openai")
        names = {"openai": "OpenAI", "gemini": "Gemini", "ollama": "Ollama · local", "local": "Local endpoint", "auto": "Automatic routing"}
        model = ("Configured role models" if provider == "auto" else
                 self.services.settings.get("model." + provider, DEFAULT_MODELS.get(provider, "")) or "Choose a model")
        self.connections_layout.addWidget(label(names.get(provider, provider)))
        self.connections_layout.addWidget(label(model, "Code", True))
        status = self.services.provider_status()
        configured = status.get(provider) if provider != "auto" else any(status.values())
        self.connections_layout.addWidget(label("Configured · health checked on request" if configured else "Not configured", "Muted", True))
        self.connections_layout.addWidget(button("Inspect provider health", lambda: self.window.open_adaptive("Health"), "Quiet"))
        self.section(self.automation_layout, "Background activity", "Automations")
        workflows = self.services.workflows.list() if hasattr(self.services, "workflows") else []
        active = sum(bool(row.get("enabled")) for row in workflows)
        background = getattr(self.services, "background", None)
        self.automation_layout.addWidget(label(f"{active} workflows enabled · " + ("Runtime active" if background and background.running else "Runtime idle"), "Muted", True))
        unread = self.services.notifications.list(unread_only=True, limit=3)
        for notification in unread:
            self.automation_layout.addWidget(button(notification["title"], self.window.open_notifications, "Quiet"))
        if not unread:
            self.automation_layout.addWidget(label("No unread notifications.", "Muted"))
        self.section(self.favorites_layout, "Applications", "Apps")
        favorites = self.services.apps.list(favorites_only=True)["items"]
        for app in favorites[:3]:
            self.favorites_layout.addWidget(button("Open " + app["name"], lambda record=app:
                self.window.open_capabilities("apps.open", {"id": record["id"]}), "Quiet"))
        if not favorites:
            self.favorites_layout.addWidget(label("Pin applications in Apps for quick access.", "Muted", True))
        self.section(self.health_layout, "SYSTEM", "System")
        telemetry = QHBoxLayout()
        snapshot = self.window.snapshot
        for name, value in (("CPU", f"{snapshot.get('cpu_percent', 0):.0f}%"),
                            ("RAM", f"{snapshot.get('memory_percent', 0):.0f}%"),
                            ("Memory", f"{snapshot.get('memory_used_gb', 0):.1f} / {snapshot.get('memory_total_gb', 0):.1f} GB")):
            telemetry.addWidget(label(name, "Muted"))
            telemetry.addWidget(label(value if snapshot else "Pending", "Code"))
            telemetry.addSpacing(12)
        telemetry.addStretch()
        self.health_layout.addLayout(telemetry)
        self.section(self.recent_layout, "Files & projects", "Files")
        recent_files = self.services.list_files()[:3]
        for record in recent_files:
            self.recent_layout.addWidget(button(record.get("name", Path(record["path"]).name), lambda record=record:
                self.window.open_capabilities("files.inspect", {"path": record["path"]}), "Quiet"))
        if not recent_files:
            self.recent_layout.addWidget(label("Choose a folder in Files to build an index.", "Muted", True))
        self.recent_layout.addStretch()
        self.section(self.history_layout, "Conversations", "Chat")
        conversations = self.services.list_conversations()
        for conversation in conversations[:3]:
            self.history_layout.addWidget(button(conversation.get("title", "Conversation"), lambda record=conversation:
                self.window.open_conversation(record["id"]), "Quiet"))
        if not conversations:
            self.history_layout.addWidget(label("No conversations yet.", "Muted"))
        self.history_layout.addStretch()
        self.section(self.activity_layout, "Actions", "Activity")
        activity = self.services.activity(3)
        for item in activity:
            self.activity_layout.addWidget(label(item.get("summary", "Action recorded"), wrap=True))
            self.activity_layout.addWidget(label(pretty_date(item.get("created_at")), "Muted"))
        if not activity:
            self.activity_layout.addWidget(label("Jarvix actions will appear here.", "Muted"))
        self.activity_layout.addStretch()


class NotesPage(Page):
    title = "Notes"
    subtitle = "Local notes and working documents"

    def __init__(self, window):
        super().__init__(window)
        self.note_id = None
        self.dirty = False
        self.loading = False
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search notes…")
        self.search.textChanged.connect(self.refresh_list)
        toolbar.addWidget(self.search)
        toolbar.addWidget(button("New note", self.new, "Primary"))
        self.layout.addLayout(toolbar)
        splitter = QSplitter()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.select)
        splitter.addWidget(self.list)
        editor = QWidget()
        ed = QVBoxLayout(editor)
        ed.setContentsMargins(SPACE[1], 0, 0, 0)
        ed.setSpacing(SPACE[1])
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Note title")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("Write a note…")
        self.title_edit.textChanged.connect(self.mark_dirty)
        self.body.textChanged.connect(self.mark_dirty)
        ed.addWidget(self.title_edit)
        ed.addWidget(self.body)
        row = QHBoxLayout()
        self.state = label("New note", "Muted")
        row.addWidget(self.state)
        row.addStretch()
        row.addWidget(button("Delete note", self.delete, "Danger"))
        row.addWidget(button("Save note", self.save, "Primary"))
        ed.addLayout(row)
        splitter.addWidget(editor)
        splitter.setSizes([260, 650])
        self.layout.addWidget(splitter, 1)

    def mark_dirty(self):
        if not self.loading:
            self.dirty = True
            self.state.setText("Unsaved changes")

    def preserve(self):
        if self.dirty and (self.title_edit.text().strip() or self.body.toPlainText().strip()):
            self.note_id = self.services.save_note(self.title_edit.text().strip() or "Untitled note", self.body.toPlainText(), self.note_id)
            self.dirty = False

    def refresh(self):
        self.refresh_list()

    def refresh_list(self):
        query = self.search.text().casefold()
        self.list.blockSignals(True)
        self.list.clear()
        for note in self.services.list_notes():
            if query in (note["title"] + " " + note["body"]).casefold():
                item = QListWidgetItem(note["title"] + "\n" + pretty_date(note.get("updated_at")))
                item.setData(Qt.ItemDataRole.UserRole, note)
                self.list.addItem(item)
                if note["id"] == self.note_id:
                    self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def load_note(self, note_id):
        for note in self.services.list_notes():
            if note["id"] == note_id:
                if self.guard(self.preserve):
                    self.set_note(note)
                return

    def set_note(self, note):
        self.loading = True
        self.note_id = note.get("id")
        self.title_edit.setText(note.get("title", ""))
        self.body.setPlainText(note.get("body", ""))
        self.loading = False
        self.dirty = False
        self.state.setText("Saved locally" if self.note_id else "New note")

    def select(self, item, previous):
        if item:
            if self.guard(self.preserve):
                self.set_note(item.data(Qt.ItemDataRole.UserRole))
            else:
                self.list.blockSignals(True)
                self.list.setCurrentItem(previous)
                self.list.blockSignals(False)

    def new(self):
        if not self.guard(self.preserve):
            return
        self.set_note({})
        self.list.clearSelection()
        self.title_edit.setFocus()

    def save(self):
        if not self.title_edit.text().strip():
            self.window.notify("Give your note a title first.")
            return
        if self.guard(self.preserve):
            self.state.setText("Saved locally")
            self.refresh_list()

    def delete(self):
        if self.note_id and self.window.confirm_delete("Delete this note?", "This removes the selected note from local storage."):
            if not self.guard(lambda: self.services.delete_note(self.note_id)):
                return
            self.dirty = False
            self.set_note({})
            self.refresh_list()


class MemoryPage(Page):
    title = "Memory"
    subtitle = "Explicit facts, sources and retention"

    def __init__(self, window):
        super().__init__(window)
        self.layout.addWidget(label("Save a fact only when you want Jarvix to retain it. Temporary context is separate.", "Muted", True))
        row = QHBoxLayout()
        self.content = QLineEdit()
        self.content.setPlaceholderText("For example: Jarvix development is my main project.")
        self.content.returnPressed.connect(self.add)
        row.addWidget(self.content)
        row.addWidget(button("Save memory", self.add, "Primary"))
        self.layout.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search saved memories…")
        self.search.textChanged.connect(self.refresh)
        self.layout.addWidget(self.search)
        self.entries = table(["Remembered fact", "Created"])
        self.layout.addWidget(self.entries)
        actions = QHBoxLayout()
        actions.addWidget(button("Why is this remembered?", self.explain, "Quiet"))
        actions.addWidget(button("Review temporary context", lambda: self.window.open_capabilities("context.session_inspect"), "Quiet"))
        actions.addStretch()
        actions.addWidget(button("Forget selected", self.delete, "Danger"))
        self.layout.addLayout(actions)
        context_menu(self.entries, [("Inspect source and retention", self.explain, "search"),
                                    ("Forget memory", self.delete, "trash")])

    def explain(self):
        row = selected_record(self.entries)
        if row:
            self.window.open_capabilities("memory.explain", {"id": row["id"]})
        else:
            self.window.notify("Select a memory to inspect its source, scope and retention.")

    def refresh(self):
        rows = self.services.list_memories()
        query = self.search.text().casefold()
        fill_table(self.entries, [row for row in rows if query in row["content"].casefold()],
                   ["content", lambda row: pretty_date(row.get("created_at"))])

    def add(self):
        value = self.content.text().strip()
        if value and self.guard(lambda: self.services.add_memory(value)):
            self.content.clear()
            self.refresh()

    def delete(self):
        row = selected_record(self.entries)
        if row and self.window.confirm_delete("Forget this memory?", row["content"]):
            self.guard(lambda: self.services.delete_memory(row["id"]))
            self.refresh()


class TasksPage(Page):
    title = "Tasks"
    subtitle = "Tasks and reminders"

    def __init__(self, window):
        super().__init__(window)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Task title")
        self.entry.returnPressed.connect(self.add)
        self.due = QLineEdit()
        self.due.setPlaceholderText("Optional: YYYY-MM-DD HH:MM")
        self.due.setMaximumWidth(245)
        self.due.setToolTip("Local time, for example 2026-09-20 09:30")
        row.addWidget(self.entry, 2)
        row.addWidget(self.due, 1)
        row.addWidget(button("Add task", self.add, "Primary"))
        self.layout.addLayout(row)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks…")
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.view = QComboBox()
        self.view.addItems(["All tasks", "Open tasks", "Completed tasks"])
        self.view.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.view)
        self.layout.addLayout(filters)
        self.entries = table(["Task", "Status", "Reminder"])
        self.layout.addWidget(self.entries)
        actions = QHBoxLayout()
        self.count = label("", "Muted")
        actions.addWidget(self.count)
        actions.addStretch()
        actions.addWidget(button("Delete task", self.delete, "Danger"))
        actions.addWidget(button("Mark complete", self.complete, "Primary"))
        self.layout.addLayout(actions)
        context_menu(self.entries, [("Mark complete", self.complete, "check"), ("Delete task", self.delete, "trash")])

    def refresh(self):
        rows = self.services.list_tasks()
        query = self.search.text().casefold()
        displayed = [row for row in rows if query in row["title"].casefold()
                     and (self.view.currentIndex() == 0
                     or (row.get("status") in {"done", "completed"}) == (self.view.currentIndex() == 2))]
        fill_table(self.entries, displayed, ["title", "status", lambda row: pretty_date(row.get("due_at"))])
        active = sum(row.get("status") not in ("done", "completed") for row in rows)
        self.count.setText(f"{active} open · {len(rows) - active} completed")

    def add(self):
        title = self.entry.text().strip()
        if not title:
            return
        due_at = None
        if self.due.text().strip():
            try:
                parsed = datetime.fromisoformat(self.due.text().strip())
                due_at = parsed.astimezone().isoformat()
            except ValueError:
                self.window.notify("Use YYYY-MM-DD HH:MM for the reminder, for example 2026-09-20 09:30.")
                return
        if self.guard(lambda: self.services.add_task(title, due_at)):
            self.entry.clear()
            self.due.clear()
            self.refresh()

    def complete(self):
        row = selected_record(self.entries)
        if row:
            self.guard(lambda: self.services.complete_task(row["id"]))
            self.refresh()

    def delete(self):
        row = selected_record(self.entries)
        if row and self.window.confirm_delete("Delete this task?", row["title"]):
            self.guard(lambda: self.services.delete_task(row["id"]))
            self.refresh()


class FilesPage(Page):
    title = "Files"
    subtitle = "Indexed files inside approved folders"

    def __init__(self, window):
        super().__init__(window)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search indexed filenames…")
        self.search.textChanged.connect(self.refresh_files)
        toolbar.addWidget(self.search, 1)
        self.file_type = QComboBox()
        self.file_type.addItems(["All file types", "Documents", "Images", "Source code"])
        self.file_type.currentIndexChanged.connect(self.refresh_files)
        toolbar.addWidget(self.file_type)
        toolbar.addWidget(button("Add folder access", self.choose_root))
        self.scan_button = button("Update index", self.scan, "Primary")
        toolbar.addWidget(self.scan_button)
        self.layout.addLayout(toolbar)
        self.roots = label("", "Muted", True)
        self.layout.addWidget(self.roots)
        scope = QHBoxLayout()
        scope.addWidget(label("Folder", "Muted"))
        self.root_picker = QComboBox()
        self.root_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.root_picker.setMinimumContentsLength(20)
        self.root_picker.currentIndexChanged.connect(self.refresh_files)
        scope.addWidget(self.root_picker, 1)
        scope.addWidget(button("Browse folder", self.browse_folder, "Quiet"))
        self.remove_root_button = button("Remove access", self.remove_root, "Quiet")
        scope.addWidget(self.remove_root_button)
        self.layout.addLayout(scope)
        self.breadcrumb = label("Indexed files", "Code", True)
        self.layout.addWidget(self.breadcrumb)
        self.entries = table(["Name", "Folder", "Size", "Modified"])
        self.entries.setProperty("sort_keys", ["name", "path", "size", "modified_at"])
        self.entries.setSortingEnabled(True)
        self.entries.cellDoubleClicked.connect(self.show_file)
        self.entries.itemSelectionChanged.connect(self.update_selection)
        self.layout.addWidget(self.entries, 3)
        actions = QHBoxLayout()
        self.document_action = QComboBox()
        for caption, tool in (("Read with citations", "extract"), ("Search document", "search"),
                              ("Ask document locally", "question"), ("Summarize excerpts", "summarize"),
                              ("Headings", "sections"), ("Tables and cells", "tables")):
            self.document_action.addItem(caption, tool)
        actions.addWidget(self.document_action)
        actions.addWidget(button("Inspect document", self.inspect_document))
        actions.addWidget(button("Open file", lambda: self.file_action("open"), "Quiet"))
        actions.addWidget(button("Use as context", self.use_file_context, "Quiet"))
        actions.addStretch()
        knowledge = button("Knowledge collections", None, "Quiet")
        menu = QMenu(knowledge)
        for caption, callback in (("Browse collections", lambda: self.window.open_capabilities("knowledge.list")),
                                  ("Create collection…", self.create_collection),
                                  ("Compare documents…", self.compare_documents)):
            menu.addAction(caption).triggered.connect(lambda _checked=False, action=callback: action())
        knowledge.setMenu(menu)
        actions.addWidget(knowledge)
        self.layout.addLayout(actions)
        context_menu(self.entries, [("Open file", lambda: self.file_action("open"), "file"),
            ("Reveal in Explorer", lambda: self.file_action("reveal"), "folder"),
            ("Copy path", lambda: self.file_action("copy_path"), "copy"),
            ("Move file…", lambda: self.file_action("move"), "folder"),
            ("Copy file…", lambda: self.file_action("copy"), "copy"),
            ("Rename file…", lambda: self.file_action("rename"), "file"),
            ("Send to Recycle Bin…", lambda: self.file_action("recycle"), "trash")])
        project_row = QHBoxLayout()
        project_row.addWidget(label("PROJECTS", "Eyebrow"))
        project_row.addStretch()
        project_row.addWidget(button("Register project", self.add_project, "Quiet"))
        self.layout.addLayout(project_row)
        self.projects = table(["Project", "Folder"])
        self.projects.setMaximumHeight(150)
        self.projects.cellDoubleClicked.connect(self.open_project)
        context_menu(self.projects, [("Open project folder", self.open_selected_project, "folder"),
                                    ("Inspect project", self.inspect_selected_project, "search")])
        self.layout.addWidget(self.projects, 1)
        self.status = label("Add folder access to index files. Right-click a file for actions.", "Muted", True)
        self.layout.addWidget(self.status)

    def refresh(self):
        roots = self.services.file_roots()
        self.roots.setText("Approved folders only · Contents stay local until disclosure is approved." if roots
                           else "No folder access yet. Add a folder to build a local file index.")
        selected = self.root_picker.currentText()
        self.root_picker.blockSignals(True)
        self.root_picker.clear()
        self.root_picker.addItems(roots)
        if selected in roots:
            self.root_picker.setCurrentText(selected)
        self.root_picker.blockSignals(False)
        self.remove_root_button.setEnabled(bool(roots))
        self.refresh_files()
        fill_table(self.projects, self.services.list_projects(), ["name", "path"])
        for index in range(self.projects.rowCount()):
            self.projects.item(index, 0).setIcon(icon("projects"))

    def refresh_files(self):
        if not hasattr(self, "entries"):
            return
        rows = self.services.list_files(self.search.text())
        scope = self.root_picker.currentText()
        if scope:
            rows = [row for row in rows if Path(row["path"]).is_relative_to(Path(scope))]
        groups = {1: {".txt", ".md", ".markdown", ".pdf", ".csv", ".json", ".docx", ".xlsx", ".pptx"},
                  2: {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"},
                  3: {".py", ".js", ".ts", ".tsx", ".jsx", ".lua", ".luau", ".rs", ".go", ".cpp", ".h"}}
        extensions = groups.get(self.file_type.currentIndex())
        if extensions:
            rows = [row for row in rows if Path(row["path"]).suffix.casefold() in extensions]
        def folder_label(row):
            folder = Path(row["path"]).parent
            relative = str(folder.relative_to(Path(scope))) if scope else str(folder)
            return Path(scope).name if relative == "." else relative
        fill_table(self.entries, rows, ["name", folder_label,
                   lambda row: f"{row.get('size', 0) / 1024:,.1f} KB", lambda row: pretty_date(row.get("modified_at"))])
        for index in range(self.entries.rowCount()):
            self.entries.item(index, 0).setIcon(icon("file"))
            record = self.entries.item(index, 0).data(Qt.ItemDataRole.UserRole)
            self.entries.item(index, 1).setToolTip(str(Path(record["path"]).parent))
        self.breadcrumb.setText("Indexed files / " + (scope or "No approved folder"))
        self.status.setText(f"{len(rows):,} indexed files · Right-click a file for actions." if rows
                            else "No matching files. Change the filter or update the index.")

    def update_selection(self):
        row = selected_record(self.entries)
        if row:
            self.status.setText(row["path"])

    def file_action(self, action):
        row = selected_record(self.entries)
        if not row:
            self.window.notify("Select an indexed file first.")
            return
        arguments = {"source" if action in {"move", "copy"} else "path": row["path"]}
        self.window.open_capabilities("files." + action, arguments)

    def open_selected_project(self):
        row = self.projects.currentRow()
        if row >= 0:
            self.open_project(row)

    def inspect_selected_project(self):
        row = selected_record(self.projects)
        if row:
            self.window.open_capabilities("intelligence.prepare", {"project_id": row["id"]})

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder Jarvix may index")
        if path and self.guard(lambda: self.services.add_file_root(path)):
            self.refresh()
            self.scan()

    def remove_root(self):
        path = self.root_picker.currentText()
        if path and self.guard(lambda: self.services.remove_file_root(path)):
            self.refresh()
            self.status.setText("Folder access removed and its indexed metadata cleared. Your files are unchanged.")

    def browse_folder(self):
        path = self.root_picker.currentText()
        if path:
            self.window.open_capabilities("files.list", {"path": path, "recursive": False})
        else:
            self.window.notify("Add an approved folder first.")

    def scan(self):
        self.scan_button.setEnabled(False)
        self.status.setText("Updating the index in the background…")
        def done(result):
            self.scan_button.setEnabled(True)
            if result.get("busy"):
                self.status.setText("An index update is already running.")
            elif result.get("limited"):
                self.status.setText("Index updated with the first 20,000 files. Choose smaller folders for complete coverage.")
            else:
                self.status.setText(f"Index updated · {result.get('count', 0):,} files. Search runs locally.")
            self.refresh()
        def failed(message):
            self.scan_button.setEnabled(True)
            self.status.setText(message)
        self.window.run_job(self.services.scan_files, done, failed)

    def show_file(self, row, column=0):
        item = self.entries.item(row, 0)
        if item:
            record = item.data(Qt.ItemDataRole.UserRole)
            TextPreview(record.get("name", "File"), "Indexed metadata. This does not read or send the file contents.", json.dumps(record, indent=2, default=str), self).exec()

    def use_file_context(self):
        row = selected_record(self.entries)
        if not row:
            self.window.notify("Select an indexed file first.")
        elif not self.services.settings.get("context.enabled", False):
            self.window.notify("Enable explicit context snapshots in Settings first.")
        else:
            self.window.select_context(selected_files=[row["path"]])
            self.status.setText("Selected for local context. File contents are shared only after approval.")

    def inspect_document(self):
        row = selected_record(self.entries)
        if not row:
            self.window.notify("Select an indexed document first.")
            return
        action = self.document_action.currentData()
        arguments = {"path": row["path"]}
        if action in {"search", "question"}:
            query, accepted = QInputDialog.getText(self, "Local document evidence", "Search words or question")
            if not accepted or not query.strip():
                return
            arguments["query" if action == "search" else "question"] = query.strip()
        self.window.open_capabilities("documents." + action, arguments)

    def create_collection(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose documents inside approved folders")
        if not paths:
            return
        name, accepted = QInputDialog.getText(self, "Knowledge collection", "Collection name")
        if accepted and name.strip():
            arguments = {"name": name.strip(), "paths": paths}
            project = selected_record(self.projects)
            if project:
                arguments["project_id"] = project["id"]
            self.window.open_capabilities("knowledge.create", arguments)

    def compare_documents(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose 2–5 documents inside approved folders")
        if paths:
            self.window.open_capabilities("documents.compare", {"paths": paths})

    def add_project(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a project folder")
        if path:
            name, accepted = QInputDialog.getText(self, "Register project", "Project name", text=Path(path).name)
            if accepted and name.strip():
                self.guard(lambda: self.services.add_project(name.strip(), path))
                self.refresh()

    def open_project(self, row, column=0):
        record = self.projects.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.window.select_context(project_id=record["id"])
        self.window.open_folder(record["path"])


class ProjectsPage(Page):
    title = "Projects"
    subtitle = "Registered local projects and working folders"

    def __init__(self, window):
        super().__init__(window)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search project name or path…")
        self.search.textChanged.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(button("Register project", lambda: window.open_capabilities("projects.create"), "Primary"))
        self.layout.addLayout(toolbar)
        self.entries = table(["Project", "Folder"])
        self.entries.setProperty("sort_keys", ["name", "path"])
        self.entries.setSortingEnabled(True)
        self.entries.itemDoubleClicked.connect(lambda _: self.continue_project())
        self.layout.addWidget(self.entries, 1)
        context_menu(self.entries, [("Continue project", self.continue_project, "play"),
                                    ("Inspect project", self.inspect, "search"),
                                    ("Open project folder", self.open_folder, "folder")])
        actions = QHBoxLayout()
        self.status = label("", "Muted", True)
        actions.addWidget(self.status, 1)
        actions.addWidget(button("Open folder", self.open_folder, "Quiet"))
        actions.addWidget(button("Inspect project", self.inspect, "Quiet"))
        actions.addWidget(button("Continue project", self.continue_project, "Primary"))
        self.layout.addLayout(actions)

    def refresh(self):
        query = self.search.text().casefold()
        rows = [row for row in self.services.list_projects() if query in (row["name"] + " " + row["path"]).casefold()]
        fill_table(self.entries, rows, ["name", "path"])
        for index in range(self.entries.rowCount()):
            self.entries.item(index, 0).setIcon(icon("projects"))
        self.status.setText(f"{len(rows)} projects" if rows else "No projects yet. Register an approved folder to track its work.")

    def inspect(self):
        row = selected_record(self.entries)
        if row:
            self.window.open_capabilities("developer.project_inspect", {"path": row["path"]})

    def open_folder(self):
        row = selected_record(self.entries)
        if row:
            self.window.open_folder(row["path"])

    def continue_project(self):
        row = selected_record(self.entries)
        if row:
            self.window.open_capabilities("intelligence.prepare", {"project_id": row["id"]})


class AppsPage(Page):
    title = "Apps"
    subtitle = "Registered applications and trusted executable paths"

    def __init__(self, window):
        super().__init__(window)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search applications…")
        self.search.textChanged.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(button("Discover installed apps", lambda: window.open_capabilities("apps.discover"), "Quiet"))
        toolbar.addWidget(button("Register application", self.add, "Primary"))
        self.layout.addLayout(toolbar)
        self.entries = table(["Application", "Executable"])
        self.entries.setSortingEnabled(True)
        self.entries.cellDoubleClicked.connect(lambda *_: self.launch())
        context_menu(self.entries, [("Launch application", self.launch, "play")])
        self.layout.addWidget(self.entries, 1)
        actions = QHBoxLayout()
        self.empty = label("", "Muted")
        actions.addWidget(self.empty, 1)
        actions.addWidget(button("Launch application", self.launch, "Primary"))
        self.layout.addLayout(actions)

    def refresh(self):
        query = self.search.text().casefold()
        apps = [row for row in self.services.list_apps() if query in (row["name"] + " " + row["path"]).casefold()]
        fill_table(self.entries, apps, ["name", "path"])
        for index in range(self.entries.rowCount()):
            self.entries.item(index, 0).setIcon(icon("apps"))
        self.empty.setText("Register an executable or discover installed apps." if not apps else f"{len(apps)} registered applications")

    def add(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a trusted application", "", "Applications (*.exe);;All files (*)")
        if path:
            name, accepted = QInputDialog.getText(self, "Register application", "Application name", text=Path(path).stem)
            if accepted and name.strip():
                self.guard(lambda: self.services.add_app(name.strip(), path))
                self.refresh()

    def launch(self):
        row = selected_record(self.entries)
        if row:
            self.window.open_capabilities("apps.open", {"id": row["id"]})


class SystemPage(Page):
    title = "System"
    subtitle = "Local resource usage · updates every 15 seconds"

    def __init__(self, window):
        super().__init__(window)
        telemetry = QHBoxLayout()
        telemetry.setSpacing(SPACE[3])
        self.cpu = QWidget()
        cpu_layout = QVBoxLayout(self.cpu)
        cpu_layout.setContentsMargins(0, 0, 0, 0)
        cpu_row = QHBoxLayout()
        cpu_row.addWidget(label("CPU", "Heading"))
        cpu_row.addStretch()
        self.cpu_value = label("Pending", "Code")
        cpu_row.addWidget(self.cpu_value)
        cpu_layout.addLayout(cpu_row)
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setTextVisible(False)
        cpu_layout.addWidget(self.cpu_bar)
        self.memory = QWidget()
        memory_layout = QVBoxLayout(self.memory)
        memory_layout.setContentsMargins(0, 0, 0, 0)
        memory_row = QHBoxLayout()
        memory_row.addWidget(label("RAM", "Heading"))
        memory_row.addStretch()
        self.memory_value = label("Pending", "Code")
        memory_row.addWidget(self.memory_value)
        memory_layout.addLayout(memory_row)
        self.memory_bar = QProgressBar()
        self.memory_bar.setTextVisible(False)
        memory_layout.addWidget(self.memory_bar)
        self.memory_detail = label("", "Muted")
        memory_layout.addWidget(self.memory_detail)
        telemetry.addWidget(self.cpu, 1)
        telemetry.addWidget(self.memory, 1)
        self.layout.addLayout(telemetry)
        self.device_status = {}
        details = QHBoxLayout()
        for caption in ("GPU", "Battery", "Storage", "Network"):
            column = QVBoxLayout()
            column.addWidget(label(caption, "Heading"))
            self.device_status[caption] = label("Not inspected", "Muted", True)
            column.addWidget(self.device_status[caption])
            column.addStretch()
            details.addLayout(column, 1)
        self.layout.addLayout(details)
        toolbar = QHBoxLayout()
        toolbar.addWidget(label("PROCESSES", "Eyebrow"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter the 20 highest-memory processes…")
        self.search.textChanged.connect(self.refresh_processes)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(button("Inspect CPU processes", lambda: window.open_capabilities("processes.search", {"sort": "cpu"})))
        toolbar.addWidget(button("Refresh metrics", window.refresh_system, "Quiet"))
        self.layout.addLayout(toolbar)
        self.processes = table(["Process", "PID", "Memory"])
        self.processes.setProperty("sort_keys", ["name", "pid", "memory_mb"])
        self.processes.setSortingEnabled(True)
        self.processes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.processes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.processes.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.layout.addWidget(self.processes, 1)
        context_menu(self.processes, [("Inspect process", self.inspect_process, "search")])
        self.status = label("Memory ranking includes accessible running processes. Command arguments are excluded.", "Muted", True)
        self.layout.addWidget(self.status)
        self._details_busy = False
        self._details_updated = None

    def refresh(self):
        snapshot = self.window.snapshot
        if snapshot:
            self.cpu_value.setText(f"{snapshot.get('cpu_percent', 0):.1f}%")
            self.memory_value.setText(f"{snapshot.get('memory_percent', 0):.1f}%")
            self.cpu_bar.setValue(round(snapshot.get("cpu_percent", 0)))
            self.memory_bar.setValue(round(snapshot.get("memory_percent", 0)))
            self.memory_detail.setText(f"{snapshot.get('memory_used_gb', 0):.1f} GB used / {snapshot.get('memory_total_gb', 0):.1f} GB installed")
            self.refresh_processes()
        if (not self.isVisible() or self._details_busy or self.window.closing
                or self._details_updated and (datetime.now() - self._details_updated).total_seconds() < 30):
            return
        self._details_busy = True
        for widget in self.device_status.values():
            widget.setText("Inspecting…")
        def work():
            return {name: self.services.execute_tool("system." + tool, {})
                    for name, tool in (("GPU", "gpu"), ("Battery", "battery"), ("Storage", "storage"), ("Network", "network"))}
        def loaded(values):
            self._details_busy = False
            self._details_updated = datetime.now()
            if self.window.closing:
                return
            for name, result in values.items():
                if not result.ok:
                    self.device_status[name].setText("Unavailable · " + (result.error or "Inspection failed"))
                    continue
                data = result.data
                if name == "Battery":
                    text = (f"{data.get('percent', 0):.0f}% · " + ("Charging" if data.get("charging") else "On battery")) if data.get("present") else "No battery reported"
                elif name == "Storage":
                    drives = data if isinstance(data, list) else data.get("items", [])
                    text = "\n".join(f"{row['mountpoint']} · {row['free'] / 1073741824:.1f} GB free" for row in drives[:3]) or "No accessible drives"
                elif name == "Network":
                    adapters = [row["name"] for row in data.get("adapters", []) if row.get("connected")]
                    text = ", ".join(adapters[:3]) or "No connected adapter"
                else:
                    adapters = data if isinstance(data, list) else data.get("adapters", data.get("items", []))
                    text = ", ".join(dict.fromkeys(str(row.get("name", row.get("Name", "Unknown adapter")))
                                                   for row in adapters[:3])) or "No adapter reported"
                self.device_status[name].setText(text)
        def failed(message):
            self._details_busy = False
            if not self.window.closing:
                for widget in self.device_status.values():
                    widget.setText("Unavailable · " + message)
        self.window.run_job(work, loaded, failed)

    def refresh_processes(self):
        query = self.search.text().casefold()
        rows = [row for row in self.window.snapshot.get("processes", []) if query in row.get("name", "").casefold()]
        fill_table(self.processes, rows, ["name", "pid", lambda row: f"{row.get('memory_mb', 0):,.1f} MB"])

    def inspect_process(self):
        row = selected_record(self.processes)
        if row:
            self.window.open_capabilities("processes.details", {"pid": row["pid"]})


class ActivityPage(Page):
    title = "Activity"
    subtitle = "Tools, permissions and local changes"

    def __init__(self, window):
        super().__init__(window)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search recorded actions…")
        self.search.textChanged.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        self.kind = QComboBox()
        self.kind.addItem("All action types", "")
        self.kind.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.kind)
        toolbar.addWidget(button("Refresh activity", self.refresh, "Quiet"))
        self.layout.addLayout(toolbar)
        self.entries = table(["When", "Type", "Action"])
        self.entries.setSortingEnabled(True)
        self.layout.addWidget(self.entries, 1)
        self.status = label("", "Muted")
        self.layout.addWidget(self.status)

    def refresh(self):
        rows = self.services.activity(200)
        current = self.kind.currentData()
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItem("All action types", "")
        for kind in sorted({row.get("kind", "") for row in rows}):
            self.kind.addItem(kind.replace("_", " ").title(), kind)
        self.kind.setCurrentIndex(max(0, self.kind.findData(current)))
        self.kind.blockSignals(False)
        query = self.search.text().casefold()
        kind = self.kind.currentData()
        shown = [row for row in rows if (not kind or row.get("kind") == kind)
                 and query in row.get("summary", "").casefold()]
        fill_table(self.entries, shown, [lambda row: pretty_date(row.get("created_at")), "kind", "summary"])
        self.status.setText(f"{len(shown)} actions · Showing the latest 200 local records" if shown
                            else "No matching activity. Jarvix records tools, confirmations and local changes here.")


class IntegrationsPage(Page):
    title = "Integrations"
    subtitle = "AI credentials and account connections"

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(SPACE[2])
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        content.addWidget(label("AI PROVIDERS", "Eyebrow"))
        self.status_labels = {}
        self.keys = {}
        for provider, name in (("openai", "OpenAI"), ("gemini", "Gemini")):
            row = QHBoxLayout()
            row.addWidget(label(name, "Heading"))
            state = label("", "Muted")
            self.status_labels[provider] = state
            row.addWidget(state)
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText(f"{name} API key · stored in OS vault")
            key.setAccessibleName(name + " API key")
            self.keys[provider] = key
            row.addWidget(key, 1)
            row.addWidget(button("Save key", lambda name=provider: self.save(name), "Primary"))
            row.addWidget(button("Remove key", lambda name=provider: self.remove(name), "Quiet"))
            content.addLayout(row)
        content.addWidget(label("Configured keys are verified by the provider on the next request.", "Muted", True))
        content.addWidget(label("ACCOUNTS", "Eyebrow"))
        accounts = QWidget()
        layout = QVBoxLayout(accounts)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE[1])
        self.account_status = table(["Service", "Connection", "Adapter"])
        self.account_status.setMinimumHeight(180)
        layout.addWidget(self.account_status)
        layout.addWidget(label("Account adapters remain disconnected until an account is verified. No account data is fetched automatically.", "Muted", True))
        content.addWidget(accounts)
        content.addStretch()

    def refresh(self):
        statuses = self.services.provider_status()
        for provider, widget in self.status_labels.items():
            widget.setText("Key configured" if statuses.get(provider) else "Not configured")
        fill_table(self.account_status, self.services.integrations.status(),
                   ["name", "status", lambda row: "Available" if row["adapter_available"] else "Not installed"])

    def save(self, provider):
        key = self.keys[provider].text().strip()
        if key and self.guard(lambda: self.services.set_api_key(provider, key)):
            self.keys[provider].clear()
            self.refresh()
            self.window.update_status()

    def remove(self, provider):
        if self.window.confirm_delete("Remove saved API key?", f"Jarvix will disconnect the saved {provider} credential."):
            self.guard(lambda: self.services.delete_api_key(provider))
            self.refresh()
            self.window.update_status()


class VoicePage(Page):
    title = "Voice"
    subtitle = "Local dictation and speech playback"

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(SPACE[2])
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        content.addWidget(label("SPEECH OUTPUT", "Eyebrow"))
        content.addWidget(label("The operating system reads this text locally. No recording is uploaded.", "Muted", True))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Text to read aloud…")
        self.text.setMinimumHeight(130)
        content.addWidget(self.text)
        row = QHBoxLayout()
        self.status = label("Ready for playback", "Muted")
        row.addWidget(self.status, 1)
        row.addWidget(button("Stop speaking", self.stop))
        row.addWidget(button("Read aloud", self.speak, "Primary"))
        content.addLayout(row)
        content.addWidget(label("SPEECH INPUT", "Eyebrow"))
        from .voice_input import VoiceInputPanel
        self.input_panel = VoiceInputPanel(window)
        content.addWidget(self.input_panel)
        content.addStretch()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(250)
        self.status_timer.timeout.connect(self.refresh)

    def refresh(self):
        self.status.setText(self.services.voice.status)
        if self.services.voice.is_speaking:
            self.status_timer.start()
        else:
            self.status_timer.stop()

    def speak(self):
        text = self.text.toPlainText().strip()
        if text:
            self.status.setText("Starting local speech…")
            self.window.run_job(lambda: self.services.speak(text), self.speech_started, lambda error: self.status.setText(error))

    def speech_started(self, started):
        self.refresh()
        if not started:
            self.window.notify(self.services.voice.status)

    def stop(self):
        self.guard(self.services.stop_speaking)
        self.refresh()


class AutomationsPage(Page):
    title = "Automations"
    subtitle = "Workflows, routines and recurring checks"

    def __init__(self, window):
        super().__init__(window)
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs, 1)
        workflows = QWidget()
        workflow_layout = QVBoxLayout(workflows)
        workflow_layout.setContentsMargins(0, SPACE[2], 0, 0)
        toolbar = QHBoxLayout()
        self.workflow_search = QLineEdit()
        self.workflow_search.setPlaceholderText("Search workflows and routines…")
        self.workflow_search.textChanged.connect(self.refresh_workflows)
        toolbar.addWidget(self.workflow_search, 1)
        toolbar.addWidget(button("Build workflow", self.window.open_workflow_builder, "Primary"))
        more = button("Workflow actions", None, "Quiet")
        menu = QMenu(more)
        for caption, callback in (("Create workflow through chat", lambda: self.window.open_chat(
                "Help me create an automation. Show its trigger, conditions and actions before saving.", send=False)),
                ("Import workflow", self.import_workflow),
                ("Duplicate selected workflow", lambda: self.workflow_action("duplicate")),
                ("Export selected workflow", lambda: self.workflow_action("export"))):
            menu.addAction(caption).triggered.connect(lambda _checked=False, action=callback: action())
        more.setMenu(menu)
        toolbar.addWidget(more)
        workflow_layout.addLayout(toolbar)
        self.workflow_entries = table(["Workflow", "Trigger", "State", "Next run"])
        self.workflow_entries.itemDoubleClicked.connect(lambda _item: self.edit_workflow())
        workflow_layout.addWidget(self.workflow_entries, 1)
        actions = QHBoxLayout()
        self.workflow_count = label("", "Muted", True)
        actions.addWidget(self.workflow_count, 1)
        for caption, callback in (("Edit workflow", self.edit_workflow),
                                  ("Test workflow", lambda: self.workflow_action("test")),
                                  ("Run history", self.workflow_history),
                                  ("Enable / disable", self.toggle_workflow),
                                  ("Run workflow", lambda: self.workflow_action("run"))):
            actions.addWidget(button(caption, callback, "Primary" if caption == "Run workflow" else "Quiet"))
        workflow_layout.addLayout(actions)
        context_menu(self.workflow_entries, [("Edit workflow", self.edit_workflow, "automations"),
            ("Run workflow", lambda: self.workflow_action("run"), "play"),
            ("Test workflow", lambda: self.workflow_action("test"), "check"),
            ("View run history", self.workflow_history, "activity"),
            ("Enable / disable workflow", self.toggle_workflow, "pause")])
        self.tabs.addTab(workflows, "Workflows && routines")
        recurring = QWidget()
        recurring_layout = QVBoxLayout(recurring)
        recurring_layout.setContentsMargins(0, SPACE[2], 0, 0)
        recurring_layout.addWidget(label("Recurring read-only checks", "Heading"))
        recurring_layout.addWidget(label("Local tools only. Permissions remain enforced on each run.", "Muted", True))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Check name")
        row.addWidget(self.name, 1)
        self.tool = QComboBox()
        self.tool.addItems(["system.status", "system.processes", "tasks.list", "projects.list"])
        row.addWidget(self.tool, 1)
        row.addWidget(label("Every", "Muted"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 10080)
        self.interval.setValue(60)
        self.interval.setSuffix(" minutes")
        row.addWidget(self.interval)
        row.addWidget(button("Create check", self.add, "Primary"))
        recurring_layout.addLayout(row)
        self.entries = table(["Check", "Tool", "Interval", "State", "Last run"])
        recurring_layout.addWidget(self.entries, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("View last result", self.view_result))
        actions.addWidget(button("Delete check", self.delete, "Danger"))
        actions.addWidget(button("Pause / resume check", self.toggle))
        recurring_layout.addLayout(actions)
        context_menu(self.entries, [("View last result", self.view_result, "search"),
                                    ("Pause / resume check", self.toggle, "pause"),
                                    ("Delete check", self.delete, "trash")])
        self.tabs.addTab(recurring, "Recurring checks")

    def refresh(self):
        self.refresh_workflows()
        fill_table(self.entries, self.services.list_automations(), ["name", "tool_name", lambda r: f"{r['interval_minutes']} min", lambda r: "Active" if r.get("enabled") else "Paused", lambda r: pretty_date(r.get("last_run_at"))])

    def refresh_workflows(self, *_):
        rows = self.services.workflows.list() if hasattr(self.services, "workflows") else []
        query = self.workflow_search.text().casefold()
        rows = [row for row in rows if query in (row["name"] + " " + row["trigger"]).casefold()]
        current = selected_record(self.workflow_entries)
        self.workflow_count.setText(f"{len(rows)} workflows" if rows else "No workflows yet. Build a reviewed sequence of actions.")
        fill_table(self.workflow_entries, rows, ["name", "trigger", lambda row: "Enabled" if row["enabled"] else "Disabled",
                                                lambda row: pretty_date(row.get("next_run"))])
        if current:
            for index, row in enumerate(rows):
                if row["id"] == current["id"]:
                    self.workflow_entries.selectRow(index)

    def edit_workflow(self):
        row = selected_record(self.workflow_entries)
        if row:
            self.window.open_workflow_builder(row)

    def workflow_action(self, action):
        row = selected_record(self.workflow_entries)
        if row:
            self.window.open_capabilities("workflows." + action, {"id": row["id"]})

    def toggle_workflow(self):
        row = selected_record(self.workflow_entries)
        if row:
            self.window.open_capabilities("workflows.toggle", {"id": row["id"], "enabled": not row["enabled"]})

    def workflow_history(self):
        from .workflows import WorkflowHistory
        row = selected_record(self.workflow_entries)
        if row:
            WorkflowHistory(self.window, row["id"]).exec()

    def import_workflow(self):
        from .workflows import import_definition
        import_definition(self.window)

    def add(self):
        name = self.name.text().strip()
        if name and self.guard(lambda: self.services.add_automation(name, self.tool.currentText(), {}, self.interval.value())):
            self.name.clear()
            self.refresh()

    def toggle(self):
        row = selected_record(self.entries)
        if row:
            self.guard(lambda: self.services.toggle_automation(row["id"], not row.get("enabled")))
            self.refresh()

    def view_result(self):
        row = selected_record(self.entries)
        if not row:
            self.window.notify("Choose a routine to inspect its latest result.")
            return
        result = self.services.settings.get("automation.result." + row["id"])
        if result is None:
            self.window.notify("This routine has not run yet.")
            return
        TextPreview(row["name"] + " · Latest result", "Stored locally · " + pretty_date(row.get("last_run_at")), json.dumps(result, indent=2, ensure_ascii=False), self).exec()

    def delete(self):
        row = selected_record(self.entries)
        if row and self.window.confirm_delete("Delete this routine?", row["name"]):
            self.guard(lambda: self.services.delete_automation(row["id"]))
            self.refresh()


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Preferences and local access"

    def __init__(self, window):
        super().__init__(window)
        splitter = QSplitter()
        self.categories = QListWidget()
        self.settings_stack = QStackedWidget()
        self.settings_sections = {}
        definitions = (("AI & models", "chat", "Provider defaults and model routing"),
                       ("Privacy & control", "settings", "Explicit access. Sensitive actions always require fresh confirmation."),
                       ("Files & storage", "files", "Approved folders, local profile and backups"),
                       ("Voice", "voice", "Local microphone and speech playback"),
                       ("Browser", "link", "Explicit browser bridge connection"),
                       ("Automations", "automations", "Configured workflows and notification behavior"),
                       ("Appearance", "settings", "Native desktop navigation and window behavior"),
                       ("Advanced", "terminal", "Capability availability, storage protection and diagnostics"))
        for title, symbol, description in definitions:
            self.categories.addItem(QListWidgetItem(icon(symbol), title))
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            body = QWidget()
            content = QVBoxLayout(body)
            content.setContentsMargins(SPACE[3], 0, SPACE[1], 0)
            content.setSpacing(SPACE[2])
            content.addWidget(label(title, "Heading"))
            content.addWidget(label(description, "Muted", True))
            self.settings_sections[title] = content
            scroll.setWidget(body)
            self.settings_stack.addWidget(scroll)
        self.categories.ensurePolished()
        self.categories.setMinimumWidth(self.categories.sizeHintForColumn(0) + SPACE[2])
        self.categories.currentRowChanged.connect(self.settings_stack.setCurrentIndex)
        self.categories.setCurrentRow(0)
        splitter.addWidget(self.categories)
        splitter.addWidget(self.settings_stack)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([170, 800])
        self.layout.addWidget(splitter, 1)
        ai = self.settings_sections["AI & models"]
        self.provider = QComboBox()
        self.provider.addItems(["openai", "gemini", "ollama", "local", "auto"])
        self.openai_model = QLineEdit()
        self.gemini_model = QLineEdit()
        self.local_only = QCheckBox("Enabled")
        self.prefer_local = QCheckBox("Enabled")
        self.routing_cost = QComboBox()
        self.routing_cost.addItems(["balanced", "low"])
        self.routing_cost.setToolTip("Unknown provider pricing is never treated as free.")
        self.setting_row(ai, "Default provider", self.provider)
        self.setting_row(ai, "OpenAI model", self.openai_model)
        self.setting_row(ai, "Gemini model", self.gemini_model)
        self.setting_row(ai, "Local-only AI", self.local_only, "Block requests to cloud models.")
        self.setting_row(ai, "Prefer local models", self.prefer_local, "Used when routing automatically.")
        self.setting_row(ai, "Routing cost", self.routing_cost)
        ai.addWidget(button("Configure local models and routing", lambda: window.open_adaptive("Models"), "Quiet"))
        ai.addWidget(button("Manage AI credentials", lambda: window.navigate("Integrations"), "Quiet"))
        self.access_checks = {}
        self.access_defaults = {
            "control.enabled": False, "clipboard.enabled": False,
            "screenshots.enabled": False, "microphone.enabled": False,
            "automations.enabled": True, "notifications.quiet": False,
            "notifications.dnd": False, "tray.enabled": False,
            "voice.responses": False, "context.enabled": False,
            "overlay.enabled": False, "windows.recent.enabled": False,
        }
        for category, key, caption, description in (
            ("Privacy & control", "control.enabled", "Reversible computer actions", "Allow normal control actions without individual prompts."),
            ("Privacy & control", "clipboard.enabled", "Clipboard tools", "Manual requests only. No clipboard monitoring."),
            ("Privacy & control", "screenshots.enabled", "Screenshots", "Capture only when requested."),
            ("Privacy & control", "context.enabled", "Active-window context", "Allow explicit context snapshots."),
            ("Voice", "microphone.enabled", "Local microphone", "Enable reviewed dictation and local listening."),
            ("Voice", "voice.responses", "Spoken responses", "Read AI responses aloud automatically."),
            ("Automations", "automations.enabled", "Background workflows", "Run configured automations while Jarvix is running."),
            ("Automations", "notifications.quiet", "Quiet notifications", "Suppress normal notification popups."),
            ("Automations", "notifications.dnd", "Do not disturb", "Pause notification interruptions."),
            ("Appearance", "tray.enabled", "Close to system tray", "Keep approved background work running after closing the window."),
            ("Appearance", "overlay.enabled", "Global command overlay", "Open a command field over the current application."),
            ("Files & storage", "windows.recent.enabled", "Windows Recent items", "Explicit requests remain limited to approved folders."),
        ):
            check = QCheckBox("Enabled")
            check.setAccessibleName(caption)
            self.access_checks[key] = check
            self.setting_row(self.settings_sections[category], caption, check, description)
        voice = self.settings_sections["Voice"]
        self.rate = QSpinBox()
        self.rate.setRange(80, 300)
        self.rate.setSuffix(" words / min")
        self.rate.setToolTip("Windows speech uses an approximate mapping to this pace.")
        self.setting_row(voice, "Speech rate", self.rate)
        self.speech_voice = QComboBox()
        self.speech_voice.addItem("System default", "")
        self.setting_row(voice, "Speech voice", self.speech_voice)
        voice.addWidget(button("Find installed voices", self.load_voices, "Quiet"))
        voice.addWidget(button("Open voice controls", lambda: window.navigate("Voice"), "Quiet"))
        appearance = self.settings_sections["Appearance"]
        from .interface import add_interface_setting
        add_interface_setting(self, appearance)
        from .overlay import HOTKEYS
        self.overlay_hotkey = QComboBox()
        self.overlay_hotkey.addItems(list(HOTKEYS))
        self.setting_row(appearance, "Overlay shortcut", self.overlay_hotkey)
        self.setting_row(appearance, "Theme", label("Dark", "Muted"), "Native Windows text scaling is respected.")
        browser = self.settings_sections["Browser"]
        browser.addWidget(label("Browser access uses an explicitly connected Chrome or Edge bridge. Login and protected fields remain restricted.", "Muted", True))
        browser.addWidget(button("Inspect browser connection", lambda: window.open_capabilities("browser.status"), "Quiet"))
        automations = self.settings_sections["Automations"]
        automations.addWidget(button("Manage workflows", lambda: window.navigate("Automations"), "Quiet"))
        automations.addWidget(button("Review closed-app scheduling", lambda: window.open_capabilities("scheduler.list"), "Quiet"))
        files = self.settings_sections["Files & storage"]
        files.addWidget(button("Manage allowed folders", lambda: window.navigate("Files"), "Quiet"))
        self.setting_row(files, "Profile location", label(str(self.services.data_dir), "Code", True))
        self.setting_row(files, "Database location", label(str(self.services.data_dir / "jarvix.db"), "Code", True))
        files.addWidget(label("Credentials remain in the operating-system vault and are excluded from ordinary backups.", "Muted", True))
        storage_actions = QHBoxLayout()
        storage_actions.addWidget(button("Create backup", self.create_backup, "Quiet"))
        storage_actions.addWidget(button("Open data folder", self.open_data_folder, "Quiet"))
        storage_actions.addStretch()
        files.addLayout(storage_actions)
        self.backup_status = label("Backup snapshots stay inside this profile.", "Muted", True)
        files.addWidget(self.backup_status)
        advanced = self.settings_sections["Advanced"]
        actions = QHBoxLayout()
        actions.addWidget(button("Inspect diagnostics", lambda: window.open_adaptive("Health"), "Quiet"))
        actions.addWidget(button("Review storage protection", lambda: window.open_capabilities("storage.configure_protection"), "Quiet"))
        actions.addWidget(button("Inspect Windows startup", lambda: window.open_capabilities("windows.startup"), "Quiet"))
        actions.addStretch()
        advanced.addLayout(actions)
        advanced.addWidget(label("AI capabilities", "Heading"))
        advanced.addWidget(label("Disabled capabilities are omitted from model requests. Enabling one does not grant execution or disclosure permission.", "Muted", True))
        self.tool_checks = {}
        self.tool_search = QLineEdit()
        self.tool_search.setPlaceholderText("Filter capabilities…")
        self.tool_search.textChanged.connect(self.filter_tools)
        advanced.addWidget(self.tool_search)
        selection = QHBoxLayout()
        selection.addWidget(button("Enable all capabilities", lambda: self.select_tools(True), "Quiet"))
        selection.addWidget(button("Disable all capabilities", lambda: self.select_tools(False), "Quiet"))
        selection.addStretch()
        advanced.addLayout(selection)
        for spec in self.services.registry.specs():
            checkbox = QCheckBox(spec.name)
            checkbox.setToolTip(f"{spec.description}\nPermission: {spec.permission}\nLevel: {spec.permission_level or spec.risk}")
            advanced.addWidget(checkbox)
            self.tool_checks[spec.name] = checkbox
        for section in self.settings_sections.values():
            section.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(label("Changes apply after saving.", "Muted"))
        footer.addStretch()
        footer.addWidget(button("Save settings", self.save, "Primary"))
        self.layout.addLayout(footer)

    @staticmethod
    def setting_row(layout, caption, control, description=""):
        row = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(SPACE[0])
        words.addWidget(label(caption))
        if description:
            words.addWidget(label(description, "Muted", True))
        row.addLayout(words, 3)
        row.addWidget(control, 2)
        control.setAccessibleName(caption)
        layout.addLayout(row)

    def refresh(self):
        settings = self.services.settings
        self.local_only.setChecked(settings.get("ai.local_only", False))
        self.prefer_local.setChecked(settings.get("routing.prefer_local", True))
        self.routing_cost.setCurrentText(settings.get("routing.cost_preference", "balanced"))
        self.provider.setCurrentText(settings.get("provider", "openai"))
        self.openai_model.setText(settings.get("model.openai", "gpt-4.1-mini"))
        self.gemini_model.setText(settings.get("model.gemini", "gemini-2.5-flash"))
        self.rate.setValue(int(settings.get("speech.rate", 175)))
        self.overlay_hotkey.setCurrentText(settings.get("overlay.hotkey", "Ctrl+Alt+Space"))
        for key, check in self.access_checks.items():
            check.setChecked(settings.get(key, self.access_defaults[key]))
        voice = settings.get("speech.voice", "")
        if self.speech_voice.findData(voice) < 0:
            self.speech_voice.addItem(voice, voice)
        self.speech_voice.setCurrentIndex(max(0, self.speech_voice.findData(voice)))
        enabled = settings.get("tools.enabled", list(self.tool_checks))
        for name, checkbox in self.tool_checks.items():
            checkbox.setChecked(name in enabled)

    def load_voices(self):
        def populate(voices):
            selected = self.services.settings.get("speech.voice", "")
            self.speech_voice.clear()
            self.speech_voice.addItem("System default", "")
            for voice in voices:
                self.speech_voice.addItem(voice, voice)
            self.speech_voice.setCurrentIndex(max(0, self.speech_voice.findData(selected)))
        self.window.run_job(self.services.voice.voices, populate)

    def open_data_folder(self):
        self.window.run_job(self.services.open_data_folder)

    def create_backup(self):
        self.backup_status.setText("Review the bounded snapshot action; its result includes a restore-verification checksum.")
        self.window.open_capabilities("backup.create")

    def filter_tools(self, text):
        for name, checkbox in self.tool_checks.items():
            checkbox.setVisible(all(word in name.casefold() for word in text.casefold().split()))

    def select_tools(self, enabled):
        for checkbox in self.tool_checks.values():
            checkbox.setChecked(enabled)

    def save(self):
        if not self.openai_model.text().strip() or not self.gemini_model.text().strip():
            self.window.notify("Both model identifiers are required.")
            return
        def persist():
            self.services.settings.set("ai.local_only", self.local_only.isChecked())
            self.services.settings.set("routing.prefer_local", self.prefer_local.isChecked())
            self.services.settings.set("routing.cost_preference", self.routing_cost.currentText())
            for key, value in (("provider", self.provider.currentText()), ("model.openai", self.openai_model.text().strip()), ("model.gemini", self.gemini_model.text().strip()), ("speech.rate", self.rate.value()), ("tools.enabled", [name for name, check in self.tool_checks.items() if check.isChecked()])):
                self.services.settings.set(key, value)
            for key, check in self.access_checks.items():
                self.services.settings.set(key, check.isChecked())
            self.services.settings.set("speech.voice", self.speech_voice.currentData() or "")
            self.services.settings.set("overlay.hotkey", self.overlay_hotkey.currentText())
            if not self.access_checks["microphone.enabled"].isChecked():
                self.window.pages["Voice"].input_panel.cancel()
            if not self.access_checks["context.enabled"].isChecked():
                self.services.context.clear()
                overlay = self.window.overlay
                overlay.context_generation += 1
                overlay.context_snapshot = None
                overlay.share_context.setChecked(False)
                overlay.share_context.setEnabled(False)
                overlay.context.setText("Context access is disabled.")
        if self.guard(persist):
            self.window.update_status()
            self.window.configure_overlay()
            self.window.pages["Chat"].reload_provider()
            self.window.notify("Settings saved locally.")
