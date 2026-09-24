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
    QScrollArea, QFrame,
)

from .widgets import label, button, panel, clear_layout, table, SignalOrb, TextPreview


def pretty_date(value) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%b %d · %H:%M")
    except (ValueError, TypeError):
        return str(value)


def fill_table(widget, rows, columns):
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for col_index, column in enumerate(columns):
            value = column(row) if callable(column) else row.get(column, "")
            item = QTableWidgetItem(str(value if value is not None else "—"))
            item.setData(Qt.ItemDataRole.UserRole, row)
            widget.setItem(row_index, col_index, item)


def selected_record(widget):
    selected = widget.selectedItems()
    return selected[0].data(Qt.ItemDataRole.UserRole) if selected else None


class Page(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.services = window.services
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(30, 25, 30, 25)
        self.layout.setSpacing(20)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(label(self.title.upper(), "Eyebrow"))
        heading.addWidget(label(self.title, "Title"))
        heading.addWidget(label(self.subtitle, "Subtitle", True))
        self.layout.addLayout(heading)

    def refresh(self):
        pass

    def guard(self, action):
        return self.window.guard(action)


class HomePage(Page):
    title = "Home"
    subtitle = "Your workspace, in focus."

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(18)
        scroll.setWidget(body)
        self.layout.addWidget(scroll)
        hero = QFrame()
        hero.setObjectName("Hero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(24, 22, 24, 24)
        hl.setSpacing(13)
        row = QHBoxLayout()
        words = QVBoxLayout()
        self.greeting = label("", "Heading")
        self.date_label = label("", "Muted")
        words.addWidget(self.greeting)
        words.addWidget(self.date_label)
        row.addLayout(words)
        row.addStretch()
        row.addWidget(SignalOrb(62))
        hl.addLayout(row)
        hl.addWidget(label("What would you like to move forward?", "Title", True))
        entry = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("Ask Jarvix, find a project, or create something…")
        self.command.setMinimumHeight(43)
        self.command.returnPressed.connect(self.submit)
        entry.addWidget(self.command)
        entry.addWidget(button("Start conversation  ↗", self.submit, "Primary"))
        hl.addLayout(entry)
        hl.addWidget(label("Local workspace ready  ·  You control every external tool result", "Muted"))
        self.body_layout.addWidget(hero)
        self.metrics = QHBoxLayout()
        self.body_layout.addLayout(self.metrics)
        split = QHBoxLayout()
        split.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(18)
        right = QVBoxLayout()
        right.setSpacing(18)
        split.addLayout(left, 3)
        split.addLayout(right, 2)
        self.body_layout.addLayout(split)
        self.tasks_panel, self.tasks_layout = panel()
        left.addWidget(self.tasks_panel)
        self.history_panel, self.history_layout = panel()
        left.addWidget(self.history_panel)
        self.recent_panel, self.recent_layout = panel()
        left.addWidget(self.recent_panel)
        self.health_panel, self.health_layout = panel()
        right.addWidget(self.health_panel)
        self.connections_panel, self.connections_layout = panel()
        right.addWidget(self.connections_panel)
        self.activity_panel, self.activity_layout = panel()
        right.addWidget(self.activity_panel)
        left.addStretch()
        right.addStretch()
        self.body_layout.addStretch()

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
            row.addWidget(button("View all  ↗", lambda: self.window.navigate(link), "Quiet"))
        layout.addLayout(row)

    def refresh(self):
        now = datetime.now()
        greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
        self.greeting.setText(greeting + ". Your workspace is ready.")
        self.date_label.setText(now.strftime("%A, %B %d  ·  %I:%M %p"))
        tasks = self.services.list_tasks()
        pending = [item for item in tasks if item.get("status") not in ("done", "completed")]
        notes = self.services.list_notes()
        memories = self.services.list_memories()
        clear_layout(self.metrics)
        for value, title, destination in ((len(pending), "OPEN TASKS", "Tasks"), (len(notes), "SAVED NOTES", "Notes"), (len(memories), "EXPLICIT MEMORIES", "Memory")):
            frame, layout = panel()
            row = QHBoxLayout()
            row.addWidget(label(str(value), "Metric"))
            row.addStretch()
            row.addWidget(button("↗", lambda dest=destination: self.window.navigate(dest), "Quiet"))
            layout.addLayout(row)
            layout.addWidget(label(title, "Eyebrow"))
            self.metrics.addWidget(frame)
        self.section(self.tasks_layout, "Next up", "Tasks")
        for task in pending[:4]:
            self.tasks_layout.addWidget(label("○  " + task["title"], wrap=True))
            if task.get("due_at"):
                self.tasks_layout.addWidget(label("     Reminder · " + pretty_date(task["due_at"]), "Muted"))
        if not pending:
            self.tasks_layout.addWidget(label("A clear slate. Add a task to keep your next step close.", "Muted", True))
            self.tasks_layout.addWidget(button("Create a task", lambda: self.window.navigate("Tasks"), "Quiet"))
        self.section(self.history_layout, "Conversations", "Chat")
        for conversation in self.services.list_conversations()[:3]:
            self.history_layout.addWidget(button(conversation.get("title", "Conversation"), lambda c=conversation: self.window.open_conversation(c["id"]), "Quiet"))
        if not self.services.list_conversations():
            self.history_layout.addWidget(label("Your first conversation starts here. Configure an AI provider in Integrations.", "Muted", True))
        self.section(self.recent_layout, "Files & projects", "Files")
        recent_files = self.services.list_files()[:3]
        projects = self.services.list_projects()[:2]
        for project in projects:
            self.recent_layout.addWidget(label("▱  " + project["name"], wrap=True))
        for file in recent_files:
            self.recent_layout.addWidget(label("↳  " + file.get("name", Path(file["path"]).name), "Muted", True))
        if not projects and not recent_files:
            self.recent_layout.addWidget(label("Add a project or choose a folder to index. Jarvix only sees the locations you choose.", "Muted", True))
        self.section(self.health_layout, "System health", "System")
        snapshot = self.window.snapshot
        if snapshot:
            for name, value in (("CPU", snapshot.get("cpu_percent", 0)), ("Memory", snapshot.get("memory_percent", 0))):
                row = QHBoxLayout()
                row.addWidget(label(name, "Muted"))
                row.addStretch()
                row.addWidget(label(f"{value:.0f}%", "Accent"))
                self.health_layout.addLayout(row)
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(round(value))
                bar.setTextVisible(False)
                bar.setFixedHeight(5)
                self.health_layout.addWidget(bar)
        else:
            self.health_layout.addWidget(label("Reading local system metrics…", "Muted"))
        self.health_layout.addWidget(label("●  Local storage active", "Success"))
        self.section(self.connections_layout, "AI connections", "Integrations")
        status = self.services.provider_status()
        provider = self.services.settings.get("provider", "openai")
        self.connections_layout.addWidget(label("Selected · " + ("OpenAI" if provider == "openai" else "Gemini"), "Accent"))
        for name in ("openai", "gemini"):
            self.connections_layout.addWidget(label(f"{'●' if status.get(name) else '○'}  {'OpenAI' if name == 'openai' else 'Gemini'}  ·  {'Key configured' if status.get(name) else 'Not configured'}", "Muted"))
        self.section(self.activity_layout, "Recent activity", "Activity")
        activity = self.services.activity(4)
        for item in activity:
            self.activity_layout.addWidget(label(item.get("summary", "Action recorded"), wrap=True))
            self.activity_layout.addWidget(label(pretty_date(item.get("created_at")), "Muted"))
        if not activity:
            self.activity_layout.addWidget(label("Your actions and AI tool decisions will appear here.", "Muted", True))


class NotesPage(Page):
    title = "Notes"
    subtitle = "Ideas and working notes, stored on this computer."

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
        ed.setContentsMargins(8, 0, 0, 0)
        ed.setSpacing(13)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Note title")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("Capture a thought. Your notes stay local until you choose to share a tool result.")
        self.title_edit.textChanged.connect(self.mark_dirty)
        self.body.textChanged.connect(self.mark_dirty)
        ed.addWidget(self.title_edit)
        ed.addWidget(self.body)
        row = QHBoxLayout()
        self.state = label("New note", "Muted")
        row.addWidget(self.state)
        row.addStretch()
        row.addWidget(button("Delete", self.delete, "Danger"))
        row.addWidget(button("Save note", self.save, "Primary"))
        ed.addLayout(row)
        splitter.addWidget(editor)
        splitter.setSizes([260, 650])
        self.layout.addWidget(splitter)

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
    subtitle = "What Jarvix should remember. Explicit, inspectable, and yours to remove."

    def __init__(self, window):
        super().__init__(window)
        intro, layout = panel()
        layout.addWidget(label("Memory is intentional", "Heading"))
        layout.addWidget(label("Nothing is remembered automatically. Saved facts stay local and are only accessed through approved tools.", "Muted", True))
        row = QHBoxLayout()
        self.content = QLineEdit()
        self.content.setPlaceholderText("For example: Jarvix development is my main project.")
        self.content.returnPressed.connect(self.add)
        row.addWidget(self.content)
        row.addWidget(button("Remember", self.add, "Primary"))
        layout.addLayout(row)
        self.layout.addWidget(intro)
        self.entries = table(["Remembered fact", "Created"])
        self.layout.addWidget(self.entries)
        self.layout.addWidget(button("Forget selected", self.delete, "Danger"), alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self):
        fill_table(self.entries, self.services.list_memories(), ["content", lambda row: pretty_date(row.get("created_at"))])

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
    subtitle = "Keep a clear next step. Reminders run while Jarvix is open."

    def __init__(self, window):
        super().__init__(window)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("What needs doing?")
        self.entry.returnPressed.connect(self.add)
        self.due = QLineEdit()
        self.due.setPlaceholderText("Optional: YYYY-MM-DD HH:MM")
        self.due.setMaximumWidth(245)
        self.due.setToolTip("Local time, for example 2026-09-20 09:30")
        row.addWidget(self.entry, 2)
        row.addWidget(self.due, 1)
        row.addWidget(button("Add task", self.add, "Primary"))
        self.layout.addLayout(row)
        self.entries = table(["Task", "Status", "Reminder"])
        self.layout.addWidget(self.entries)
        actions = QHBoxLayout()
        self.count = label("", "Muted")
        actions.addWidget(self.count)
        actions.addStretch()
        actions.addWidget(button("Delete", self.delete, "Danger"))
        actions.addWidget(button("Mark complete", self.complete, "Primary"))
        self.layout.addLayout(actions)

    def refresh(self):
        rows = self.services.list_tasks()
        fill_table(self.entries, rows, ["title", "status", lambda row: pretty_date(row.get("due_at"))])
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
    subtitle = "A search index scoped to folders you choose. File contents are never bulk uploaded."

    def __init__(self, window):
        super().__init__(window)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find an indexed file…")
        self.search.textChanged.connect(self.refresh_files)
        row.addWidget(self.search)
        row.addWidget(button("Choose folder", self.choose_root))
        self.scan_button = button("Update index", self.scan, "Primary")
        row.addWidget(self.scan_button)
        self.layout.addLayout(row)
        self.roots = label("", "Muted", True)
        self.layout.addWidget(self.roots)
        scope = QHBoxLayout()
        self.root_picker = QComboBox()
        self.root_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.root_picker.setMinimumContentsLength(28)
        scope.addWidget(self.root_picker, 1)
        self.remove_root_button = button("Remove folder access", self.remove_root, "Quiet")
        scope.addWidget(self.remove_root_button)
        self.layout.addLayout(scope)
        self.entries = table(["Name", "Location", "Size", "Modified"])
        self.entries.cellDoubleClicked.connect(self.show_file)
        self.layout.addWidget(self.entries, 2)
        project_row = QHBoxLayout()
        project_row.addWidget(label("PROJECTS", "Eyebrow"))
        project_row.addStretch()
        project_row.addWidget(button("Register project", self.add_project, "Quiet"))
        self.layout.addLayout(project_row)
        self.projects = table(["Project", "Folder"])
        self.projects.setMaximumHeight(190)
        self.projects.cellDoubleClicked.connect(self.open_project)
        self.layout.addWidget(self.projects, 1)
        self.status = label("Double-click a file to inspect its indexed metadata; a project to open its folder.", "Muted", True)
        self.layout.addWidget(self.status)

    def refresh(self):
        roots = self.services.file_roots()
        self.roots.setText("Search scope · Only approved folders are indexed." if roots else "No folders selected. Choose a folder to get started.")
        selected = self.root_picker.currentText()
        self.root_picker.clear()
        self.root_picker.addItems(roots)
        if selected in roots:
            self.root_picker.setCurrentText(selected)
        self.remove_root_button.setEnabled(bool(roots))
        self.refresh_files()
        fill_table(self.projects, self.services.list_projects(), ["name", "path"])

    def refresh_files(self):
        fill_table(self.entries, self.services.list_files(self.search.text()), ["name", "path", lambda r: f"{r.get('size', 0) / 1024:.1f} KB", lambda r: pretty_date(r.get("modified_at"))])

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

    def add_project(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a project folder")
        if path:
            name, accepted = QInputDialog.getText(self, "Register project", "Project name", text=Path(path).name)
            if accepted and name.strip():
                self.guard(lambda: self.services.add_project(name.strip(), path))
                self.refresh()

    def open_project(self, row, column=0):
        record = self.projects.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.window.open_folder(record["path"])


class AppsPage(Page):
    title = "Apps"
    subtitle = "Register trusted applications. Jarvix launches explicit paths, never arbitrary shell commands."

    def __init__(self, window):
        super().__init__(window)
        row = QHBoxLayout()
        row.addWidget(label("Your application library", "Heading"))
        row.addStretch()
        row.addWidget(button("Register application", self.add, "Primary"))
        self.layout.addLayout(row)
        self.entries = table(["Application", "Executable"])
        self.entries.cellDoubleClicked.connect(lambda *_: self.launch())
        self.layout.addWidget(self.entries)
        self.empty = label("", "Muted")
        self.layout.addWidget(self.empty)
        self.layout.addWidget(button("Launch selected  ↗", self.launch, "Primary"), alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self):
        apps = self.services.list_apps()
        fill_table(self.entries, apps, ["name", "path"])
        self.empty.setText("Choose an executable to add your first application." if not apps else f"{len(apps)} registered applications")

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
    subtitle = "Live local resource usage. Metrics stay on this device."

    def __init__(self, window):
        super().__init__(window)
        top = QHBoxLayout()
        self.cpu, cpul = panel("CPU utilization")
        self.cpu_value = label("—", "Metric")
        cpul.addWidget(self.cpu_value)
        self.memory, meml = panel("Memory pressure")
        self.memory_value = label("—", "Metric")
        self.memory_detail = label("", "Muted")
        meml.addWidget(self.memory_value)
        meml.addWidget(self.memory_detail)
        top.addWidget(self.cpu)
        top.addWidget(self.memory)
        self.layout.addLayout(top)
        row = QHBoxLayout()
        row.addWidget(label("Highest memory usage", "Heading"))
        row.addStretch()
        row.addWidget(button("Refresh", self.window.refresh_system))
        self.layout.addLayout(row)
        self.processes = table(["Process", "PID", "Memory"])
        self.layout.addWidget(self.processes)
        self.layout.addWidget(label("Updated every 15 seconds while Jarvix is open. Process names may require operating-system access.", "Muted", True))

    def refresh(self):
        snapshot = self.window.snapshot
        if not snapshot:
            return
        self.cpu_value.setText(f"{snapshot.get('cpu_percent', 0):.1f}%")
        self.memory_value.setText(f"{snapshot.get('memory_percent', 0):.1f}%")
        self.memory_detail.setText(f"{snapshot.get('memory_used_gb', 0):.1f} GB used / {snapshot.get('memory_total_gb', 0):.1f} GB installed")
        fill_table(self.processes, snapshot.get("processes", []), ["name", "pid", lambda r: f"{r.get('memory_mb', 0):,.1f} MB"])


class ActivityPage(Page):
    title = "Activity"
    subtitle = "An inspectable history of Jarvix actions, tool decisions, and local changes."

    def __init__(self, window):
        super().__init__(window)
        self.entries = table(["When", "Type", "Action"])
        self.layout.addWidget(self.entries)
        self.layout.addWidget(button("Refresh activity", self.refresh), alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self):
        fill_table(self.entries, self.services.activity(200), [lambda r: pretty_date(r.get("created_at")), "kind", "summary"])


class IntegrationsPage(Page):
    title = "Integrations"
    subtitle = "Connect an AI provider. Credentials are stored in your operating system's credential vault."

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 6, 0)
        content.setSpacing(18)
        scroll.setWidget(body)
        self.layout.addWidget(scroll)
        self.status_labels = {}
        self.keys = {}
        for provider, name, description in (("openai", "OpenAI", "Tool-capable models through the OpenAI API."), ("gemini", "Gemini", "Google Gemini models with structured function calling.")):
            frame, layout = panel()
            row = QHBoxLayout()
            row.addWidget(label(name, "Heading"))
            row.addStretch()
            status = label("", "Muted")
            self.status_labels[provider] = status
            row.addWidget(status)
            layout.addLayout(row)
            layout.addWidget(label(description, "Muted"))
            entry = QHBoxLayout()
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText(f"Enter {name} API key")
            self.keys[provider] = key
            entry.addWidget(key)
            entry.addWidget(button("Save key", lambda p=provider: self.save(p), "Primary"))
            entry.addWidget(button("Remove", lambda p=provider: self.remove(p), "Quiet"))
            layout.addLayout(entry)
            content.addWidget(frame)
        future, layout = panel("Account integrations")
        self.account_status = table(["Service", "Connection", "Adapter"])
        self.account_status.setMinimumHeight(255)
        layout.addWidget(self.account_status)
        layout.addWidget(label("Account adapters remain disconnected until an implementation and authorized credentials are configured. No account data is fetched automatically.", "Muted", True))
        content.addWidget(future)
        content.addWidget(label("A configured key has not necessarily been verified. The first AI request validates it with the selected provider.", "Muted", True))
        content.addStretch()

    def refresh(self):
        statuses = self.services.provider_status()
        for provider, widget in self.status_labels.items():
            widget.setText("● Key configured" if statuses.get(provider) else "○ Not configured")
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
    subtitle = "A local voice for your assistant, with explicit playback controls."

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 6, 0)
        content.setSpacing(18)
        scroll.setWidget(body)
        self.layout.addWidget(scroll)
        frame, layout = panel()
        top = QHBoxLayout()
        top.addWidget(SignalOrb(90))
        words = QVBoxLayout()
        words.addWidget(label("Read aloud", "Heading"))
        words.addWidget(label("Uses the operating system's speech engine. No voice recording is uploaded.", "Muted", True))
        top.addLayout(words)
        top.addStretch()
        layout.addLayout(top)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Paste text here, or use Read aloud on a chat answer.")
        self.text.setMinimumHeight(170)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        self.status = label("Ready for playback", "Muted")
        row.addWidget(self.status)
        row.addStretch()
        row.addWidget(button("Stop speaking", self.stop))
        row.addWidget(button("Read aloud", self.speak, "Primary"))
        layout.addLayout(row)
        content.addWidget(frame)
        frame, layout = panel("Voice input")
        from .voice_input import VoiceInputPanel
        self.input_panel = VoiceInputPanel(window)
        layout.addWidget(self.input_panel)
        content.addWidget(frame)
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
    subtitle = "Explicit local routines. Scheduled work runs only while Jarvix is open."

    def __init__(self, window):
        super().__init__(window)
        frame, layout = panel("Create a routine")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Routine name")
        layout.addWidget(self.name)
        row = QHBoxLayout()
        self.tool = QComboBox()
        self.tool.addItems(["system.status", "system.processes", "tasks.list", "projects.list"])
        row.addWidget(self.tool, 2)
        row.addWidget(label("Every", "Muted"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 10080)
        self.interval.setValue(60)
        self.interval.setSuffix(" minutes")
        row.addWidget(self.interval)
        row.addWidget(button("Create routine", self.add, "Primary"))
        layout.addLayout(row)
        layout.addWidget(label("Only safe, read-only local tools are allowed for unattended runs. Activity records each outcome; the latest result is available below.", "Muted", True))
        self.layout.addWidget(frame)
        self.entries = table(["Routine", "Tool", "Interval", "State", "Last run"])
        self.layout.addWidget(self.entries)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("View last result", self.view_result))
        row.addWidget(button("Delete", self.delete, "Danger"))
        row.addWidget(button("Pause / resume", self.toggle))
        self.layout.addLayout(row)

    def refresh(self):
        fill_table(self.entries, self.services.list_automations(), ["name", "tool_name", lambda r: f"{r['interval_minutes']} min", lambda r: "Active" if r.get("enabled") else "Paused", lambda r: pretty_date(r.get("last_run_at"))])

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
    subtitle = "Configure your assistant and inspect its local boundaries."

    def __init__(self, window):
        super().__init__(window)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(18)
        scroll.setWidget(body)
        self.layout.addWidget(scroll)
        frame, form = panel("AI defaults")
        self.provider = QComboBox()
        self.provider.addItems(["openai", "gemini"])
        self.openai_model = QLineEdit()
        self.gemini_model = QLineEdit()
        for name, widget in (("Default provider", self.provider), ("OpenAI model", self.openai_model), ("Gemini model", self.gemini_model)):
            row = QHBoxLayout()
            caption = label(name, "Muted")
            caption.setMinimumWidth(140)
            row.addWidget(caption)
            row.addWidget(widget)
            form.addLayout(row)
        self.rate = QSpinBox()
        self.rate.setRange(80, 300)
        self.rate.setSuffix(" words / min")
        self.rate.setToolTip("80–300 words per minute; Windows speech uses an approximate pace mapping.")
        row = QHBoxLayout()
        row.addWidget(label("Speech rate", "Muted"))
        row.addStretch()
        row.addWidget(self.rate)
        form.addLayout(row)
        layout.addWidget(frame)
        frame, controls = panel("Computer access & notifications")
        controls.addWidget(label("Sensitive actions always need confirmation immediately before execution. Microphone, screen, and clipboard access are opt-in.", "Muted", True))
        self.access_checks = {}
        self.access_defaults = {
            "control.enabled": False, "clipboard.enabled": False,
            "screenshots.enabled": False, "microphone.enabled": False,
            "automations.enabled": True, "notifications.quiet": False,
            "notifications.dnd": False, "tray.enabled": False,
            "voice.responses": False,
        }
        for key, caption in (
            ("control.enabled", "Allow reversible computer actions without individual prompts"),
            ("clipboard.enabled", "Allow clipboard tools (manual access only)"),
            ("screenshots.enabled", "Allow screenshots on request"),
            ("microphone.enabled", "Allow local microphone dictation"),
            ("automations.enabled", "Enable configured automations while Jarvix is running"),
            ("notifications.quiet", "Quiet notifications"),
            ("notifications.dnd", "Do not disturb"),
            ("tray.enabled", "Minimize to system tray when closing"),
            ("voice.responses", "Read AI responses aloud automatically"),
        ):
            check = QCheckBox(caption)
            controls.addWidget(check)
            self.access_checks[key] = check
        voice_row = QHBoxLayout()
        voice_row.addWidget(label("Speech voice", "Muted"))
        self.speech_voice = QComboBox()
        self.speech_voice.addItem("System default", "")
        voice_row.addWidget(self.speech_voice, 1)
        voice_row.addWidget(button("Find voices", self.load_voices))
        controls.addLayout(voice_row)
        layout.addWidget(frame)
        frame, tools_layout = panel("Enabled AI capabilities")
        tools_layout.addWidget(label("Disabled tools are omitted from model requests. Enabling a tool does not bypass execution or disclosure approval.", "Muted", True))
        self.tool_checks = {}
        self.tool_search = QLineEdit()
        self.tool_search.setPlaceholderText("Filter capabilities by name…")
        self.tool_search.textChanged.connect(self.filter_tools)
        tools_layout.addWidget(self.tool_search)
        selection = QHBoxLayout()
        selection.addWidget(button("Enable all", lambda: self.select_tools(True), "Quiet"))
        selection.addWidget(button("Disable all", lambda: self.select_tools(False), "Quiet"))
        selection.addStretch()
        tools_layout.addLayout(selection)
        for spec in self.services.registry.specs():
            checkbox = QCheckBox(spec.name)
            checkbox.setToolTip(f"{spec.description}\nPermission: {spec.permission}\nLevel: {spec.permission_level or spec.risk}")
            tools_layout.addWidget(checkbox)
            self.tool_checks[spec.name] = checkbox
        layout.addWidget(frame)
        frame, storage = panel("Local storage")
        storage.addWidget(label(str(self.services.data_dir), "Muted", True))
        storage.addWidget(label("Notes, tasks, memories, settings, and conversation history are stored here. API credentials are kept separately in the operating-system vault.", "Muted", True))
        storage_actions = QHBoxLayout()
        storage_actions.addWidget(button("Create consistent backup", self.create_backup, "Quiet"))
        storage_actions.addWidget(button("Open data folder  ↗", self.open_data_folder, "Quiet"))
        storage_actions.addStretch()
        storage.addLayout(storage_actions)
        self.backup_status = label("Backups are stored locally in this profile and exclude API credentials.", "Muted", True)
        storage.addWidget(self.backup_status)
        layout.addWidget(frame)
        layout.addStretch()
        self.layout.addWidget(button("Save settings", self.save, "Primary"), alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self):
        settings = self.services.settings
        self.provider.setCurrentText(settings.get("provider", "openai"))
        self.openai_model.setText(settings.get("model.openai", "gpt-4.1-mini"))
        self.gemini_model.setText(settings.get("model.gemini", "gemini-2.5-flash"))
        self.rate.setValue(int(settings.get("speech.rate", 175)))
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
        self.backup_status.setText("Creating a consistent local backup…")

        def done(result):
            size_kib = result["size_bytes"] / 1024
            self.backup_status.setText(f"Backup created · {size_kib:,.1f} KiB · API credentials excluded.")
            self.window.notify("Local backup created. Open the data folder to copy it elsewhere.")

        def failed(message):
            self.backup_status.setText(message)

        self.window.run_job(self.services.create_backup, done, failed)

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
            for key, value in (("provider", self.provider.currentText()), ("model.openai", self.openai_model.text().strip()), ("model.gemini", self.gemini_model.text().strip()), ("speech.rate", self.rate.value()), ("tools.enabled", [name for name, check in self.tool_checks.items() if check.isChecked()])):
                self.services.settings.set(key, value)
            for key, check in self.access_checks.items():
                self.services.settings.set(key, check.isChecked())
            self.services.settings.set("speech.voice", self.speech_voice.currentData() or "")
            if not self.access_checks["microphone.enabled"].isChecked():
                self.window.pages["Voice"].input_panel.cancel()
        if self.guard(persist):
            self.window.update_status()
            self.window.pages["Chat"].reload_provider()
            self.window.notify("Settings saved locally.")
