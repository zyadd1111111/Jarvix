"""Purpose-built Nexus workspaces; existing controllers own every mutation."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileIconProvider, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QStyledItemDelegate, QStyle, QVBoxLayout,
)

from ..pages import (
    AppsPage, AutomationsPage, FilesPage, NotesPage, ProjectsPage, SettingsPage, SystemPage,
    TasksPage, fill_table, pretty_date, selected_record,
)
from ..widgets import button, label, table
from ..liquid.components import LiquidPanel, LiquidToolbar
from ..liquid.motion import Transition, enabled, owner, reveal
from ..liquid.theme import ink
from .design import TOKENS, discard_layout, icon, pane, title_row


def fresh(page):
    """Discard controller composition; retain its controls and signal contracts."""
    discard_layout(page.layout, page)
    page.layout.setContentsMargins(TOKENS["page_inset"], TOKENS["spacing"][3], TOKENS["page_inset"], TOKENS["spacing"][3])
    page.layout.setSpacing(TOKENS["spacing"][2])
    page.setMinimumWidth(0)


def place(layout, widget, stretch=0):
    widget.setMinimumWidth(0)
    layout.addWidget(widget, stretch)
    widget.show()


def properties(parent=None):
    view = QTreeWidget(parent)
    view.setColumnCount(2)
    view.setHeaderHidden(True)
    view.setRootIsDecorated(False)
    view.setMinimumWidth(0)
    view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    view.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    return view


def describe(view, rows):
    view.clear()
    for caption, value in rows:
        item = QTreeWidgetItem([caption, str(value)])
        item.setToolTip(1, str(value))
        view.addTopLevelItem(item)


def menu_button(caption, actions, parent):
    from .design import NexusMenu
    control = button(caption, style="Quiet")
    menu = NexusMenu(caption, control)
    for text, callback in actions:
        menu.addAction(text).triggered.connect(lambda _checked=False, call=callback: call())
    control.setMenu(menu)
    control.setParent(parent)
    return control


class WorkflowSequenceDelegate(QStyledItemDelegate):
    """A connected action sequence; item data and editing stay with WorkflowSteps."""

    def sizeHint(self, option, index):
        return QSize(0, TOKENS["timeline_row_height"] + TOKENS["spacing"][4])

    def paint(self, painter, option, index):
        preferences = getattr(owner(self.parent()), "_liquid_preferences", {})
        step = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = step.get("kind", "action")
        caption = {"action": "DO", "delay": "WAIT", "branch": "BRANCH", "set": "SET", "foreach": "FOR EACH", "subflow": "SUBFLOW"}.get(kind, "STEP")
        text = step.get("tool") or step.get("name") or step.get("workflow_id") or (f"{step.get('seconds', 1)} seconds" if kind == "delay" else "Condition")
        rect = option.rect.adjusted(0, 3, -1, -3)
        rail = rect.left() + 14
        body = rect.adjusted(32, 3, -2, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(ink(preferences, "border")), 1))
        if index.row():
            painter.drawLine(rail, rect.top(), rail, rect.center().y() - 8)
        if index.row() + 1 < index.model().rowCount():
            painter.drawLine(rail, rect.center().y() + 8, rail, rect.bottom())
        painter.setBrush(QColor(ink(preferences, "selected")))
        painter.drawEllipse(rail - 6, rect.center().y() - 6, 12, 12)
        fill = QColor(ink(preferences, "selected" if selected else "glass_panel"))
        fill.setAlpha(220 if selected else 150)
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(ink(preferences, "accent" if selected else "border")), 1))
        painter.drawRoundedRect(QRectF(body), TOKENS["control_radius"], TOKENS["control_radius"])
        painter.setFont(option.font)
        painter.setPen(QColor(ink(preferences, "muted")))
        painter.drawText(QRect(body.left() + 12, body.top() + 9, body.width() - 24, 20), Qt.AlignmentFlag.AlignLeft, caption)
        painter.setPen(QColor(ink(preferences, "text")))
        title = painter.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight, body.width() - 24)
        painter.drawText(QRect(body.left() + 12, body.top() + 30, body.width() - 24, 22), Qt.AlignmentFlag.AlignLeft, title)
        painter.setPen(QColor(ink(preferences, "muted")))
        detail = "Retry once" if step.get("retries") else "Stop on failure" if step.get("on_error", "stop") == "stop" else "Continue on failure"
        painter.drawText(QRect(body.left() + 12, body.bottom() - 24, body.width() - 24, 20), Qt.AlignmentFlag.AlignLeft, detail)
        painter.restore()


class NexusProjectsPage(ProjectsPage):
    """Library beside a project document, with contextual resource tabs."""

    def __init__(self, window):
        super().__init__(window)
        fresh(self)
        self.project_split = QSplitter(self)
        self.project_split.setChildrenCollapsible(False)
        library = pane(self.project_split)
        library.setMaximumWidth(TOKENS["library_width"] + 40)
        library.body.addWidget(title_row("Projects", parent=library))
        self.search.setPlaceholderText("Find project…")
        place(library.body, self.search)
        self.entries.setColumnHidden(1, True)
        self.entries.horizontalHeader().hide()
        self.entries.verticalHeader().setDefaultSectionSize(TOKENS["row_height"])
        place(library.body, self.entries, 1)
        place(library.body, self.status)
        library.body.addWidget(button("Register project", lambda: window.open_capabilities("projects.create")))
        self.project_split.addWidget(library)
        document = pane(self.project_split, margins=(20, 0, 0, 0))
        self.project_name = label("Choose a project", "NexusTitle", True)
        self.project_path = label("Select a project to see its work.", "Code", True)
        self.repository = label("Repository not inspected", "Muted", True)
        document.body.addWidget(self.project_name)
        document.body.addWidget(self.project_path)
        document.body.addWidget(self.repository)
        quick = QHBoxLayout()
        self.project_actions = [button("Open", self.open_folder), button("VS Code", self.open_editor),
                                button("Terminal", self.open_terminal), button("Ask Jarvix", self.ask_project, "Primary")]
        for control in self.project_actions:
            quick.addWidget(control)
        quick.addStretch()
        quick.addWidget(menu_button("Project actions", [("Inspect project", self.inspect),
            ("Continue project", self.continue_project)], self))
        document.body.addLayout(quick)
        self.project_tabs = QTabWidget(document)
        self.project_tabs.setAccessibleName("Project sections")
        self.project_views = {}
        for name, columns in (("Overview", ["Property", "Value"]), ("Files", ["Name", "Modified", "Size"]),
            ("Tasks", ["Task", "Status", "Due"]), ("Knowledge", ["Space", "Sources"]),
            ("Conversations", ["Conversation", "Updated"]), ("Activity", ["Time", "Action"])):
            view = table(columns)
            self.project_tabs.addTab(view, name)
            self.project_views[name] = view
            if name in {"Files", "Tasks", "Knowledge", "Conversations"}:
                view.cellDoubleClicked.connect(lambda *_args, section=name: self.open_resource(section))
        document.body.addWidget(self.project_tabs, 1)
        self.project_state = label("Linked resources stay local.", "Muted", True)
        document.body.addWidget(self.project_state)
        self.project_split.addWidget(document)
        self.project_split.setSizes([TOKENS["library_width"], 820])
        self.project_split.setStretchFactor(1, 1)
        self.layout.addWidget(self.project_split, 1)
        self.entries.itemSelectionChanged.connect(self.load_project)
        self._project_revision = 0
        self.load_project()

    def refresh(self):
        selected = selected_record(self.entries)
        self.entries.blockSignals(True)
        super().refresh()
        if self.entries.rowCount():
            row = next((i for i in range(self.entries.rowCount()) if selected and
                self.entries.item(i, 0).data(Qt.ItemDataRole.UserRole)["id"] == selected["id"]), 0)
            self.entries.selectRow(row)
        self.entries.blockSignals(False)
        if hasattr(self, "project_views"):
            self.load_project()

    def load_project(self):
        self._project_revision += 1
        revision = self._project_revision
        project = selected_record(self.entries)
        for control in self.project_actions:
            control.setEnabled(bool(project))
        for view in self.project_views.values():
            view.setRowCount(0)
        self.project_name.setText(project["name"] if project else "Choose a project")
        self.project_path.setText(project["path"] if project else "Register a local folder to begin.")
        self.repository.setText("Inspecting repository…" if project else "")
        self.project_tabs.setEnabled(bool(project))
        if not project:
            return
        self.project_state.setText("Reading project resources…")
        def read():
            sources = (("summary", "projects.summary", {"id": project["id"]}),
                ("knowledge", "knowledge_spaces.list", {"project_id": project["id"]}),
                ("conversations", "conversations.search", {"project_id": project["id"]}))
            results = {}
            for name, tool, arguments in sources:
                if tool not in self.services.enabled_tools():
                    raise PermissionError(tool + " is disabled.")
                result = self.services.execute_tool(tool, arguments)
                if not result.ok:
                    raise ValueError(result.error or "Project source unavailable.")
                results[name] = result.data
            if "developer.git_status" in self.services.enabled_tools():
                results["git"] = self.services.execute_tool("developer.git_status", {"path": project["path"]})
            results["activity"] = [{"at": entry["at"], "action": entry["action"].replace("_", " ")}
                for mission in self.services.records.list("mission") if mission.get("project_id") == project["id"]
                for entry in reversed(mission.get("history", []))][:100]
            return results
        def loaded(data):
            if revision != self._project_revision or self.window.closing:
                return
            summary = data["summary"]
            git = data.get("git")
            text = git.data.get("text", "") if git and git.ok else ""
            self.repository.setText(text.splitlines()[0].removeprefix("## ") if text else "Git unavailable or not a repository")
            fill_table(self.project_views["Overview"], [{"name": name, "value": value} for name, value in (
                ("Folder", project["path"]), ("Open tasks", summary["open_tasks"]),
                ("Indexed files", len(summary["files"])), ("Linked notes", len(summary["notes"])),
                ("Workspace", summary["project"].get("workspace_id") or "Not linked"),
                ("State", "Archived" if summary["project"].get("archived") else "Active"))], ["name", "value"])
            fill_table(self.project_views["Files"], summary["files"], ["name", lambda r: pretty_date(r.get("modified_at")), "size"])
            fill_table(self.project_views["Tasks"], summary["tasks"], ["title", "status", lambda r: pretty_date(r.get("due_at"))])
            fill_table(self.project_views["Knowledge"], data["knowledge"]["items"], ["name", "sources"])
            fill_table(self.project_views["Conversations"], data["conversations"], ["title", lambda r: pretty_date(r.get("updated_at"))])
            fill_table(self.project_views["Activity"], data["activity"], [lambda r: pretty_date(r["at"]), "action"])
            self.project_state.setText("Local resources · Double-click a row to open it.")
        def failed(message):
            if revision == self._project_revision and not self.window.closing:
                self.project_state.setText(message)
                self.repository.setText("Repository not inspected")
        self.window.run_job(read, loaded, failed)

    def open_editor(self):
        project = selected_record(self.entries)
        if not project:
            return
        app = next((row for row in self.services.list_apps() if Path(row["path"]).name.casefold()
                    in {"code.exe", "code-insiders.exe"}), None)
        if app:
            self.window.open_capabilities("apps.open_project", {"id": app["id"], "project_id": project["id"]})
        else:
            self.window.notify("Register Visual Studio Code in Apps first.")

    def open_terminal(self):
        if project := selected_record(self.entries):
            self.window.open_capabilities("workspaces.open_terminal", {"path": project["path"]})

    def ask_project(self):
        if project := selected_record(self.entries):
            self.window.select_context(project_id=project["id"])
            self.window.open_chat("Continue " + project["name"] + ".", send=False)

    def open_resource(self, section):
        if row := selected_record(self.project_views[section]):
            if section == "Files":
                self.window.open_capabilities("files.inspect", {"path": row["path"]})
            elif section == "Knowledge":
                self.window.open_capabilities("knowledge_spaces.get", {"id": row["id"]})
            else:
                {"Tasks": self.window.open_task, "Conversations": self.window.open_conversation}[section](row["id"])


class NexusFilesPage(FilesPage):
    """Approved-folder browser with real directories and a contextual inspector."""

    def __init__(self, window):
        self.location = None
        self.location_history = []
        self.location_position = -1
        self._file_revision = 0
        super().__init__(window)
        fresh(self)
        self.file_icons = QFileIconProvider()
        self.file_icons.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)
        self.file_split = QSplitter(self)
        self.file_split.setChildrenCollapsible(False)
        locations = pane(self.file_split)
        locations.setMaximumWidth(TOKENS["library_width"])
        locations.body.addWidget(label("Locations", "NexusSection"))
        self.location_list = QListWidget(locations)
        self.location_list.setAccessibleName("Approved folders")
        self.location_list.itemActivated.connect(lambda item: self.navigate_location(item.data(Qt.ItemDataRole.UserRole)))
        self.location_list.currentItemChanged.connect(lambda item, _previous: self.navigate_location(item.data(Qt.ItemDataRole.UserRole)) if item else None)
        locations.body.addWidget(self.location_list, 1)
        locations.body.addWidget(button("Add folder access", self.choose_root))
        locations.body.addWidget(menu_button("Folder actions", [("Remove access", self.remove_root),
            ("Update index", self.scan), ("Recycled items", lambda: window.open_capabilities("files.recycled"))], self))
        self.file_split.addWidget(locations)
        browser = pane(self.file_split, margins=(16, 0, 0, 0))
        toolbar = LiquidToolbar(browser)
        row = QHBoxLayout()
        self.location_back = button("Back", lambda: self.go_location(-1), "Quiet")
        self.location_forward = button("Forward", lambda: self.go_location(1), "Quiet")
        self.location_back.setIcon(icon("chevron-left"))
        self.location_forward.setIcon(icon("chevron-right"))
        row.addWidget(self.location_back)
        row.addWidget(self.location_forward)
        self.path_input = QLineEdit(toolbar)
        self.path_input.setAccessibleName("Approved folder path")
        self.path_input.returnPressed.connect(lambda: self.navigate_location(self.path_input.text()))
        row.addWidget(self.path_input, 1)
        row.addWidget(menu_button("View", [("Compact rows", lambda: self.entries.verticalHeader().setDefaultSectionSize(28)),
            ("Comfortable rows", lambda: self.entries.verticalHeader().setDefaultSectionSize(36)),
            ("Show / hide preview", self.toggle_preview)], self))
        toolbar.body.addLayout(row)
        browser.body.addWidget(toolbar)
        filters = QHBoxLayout()
        self.search.setPlaceholderText("Search this folder…")
        self.file_type.setMinimumContentsLength(10)
        place(filters, self.search, 1)
        place(filters, self.file_type)
        filters.addWidget(menu_button("Sort", [("Name", lambda: self.entries.sortItems(0)),
            ("Newest first", lambda: self.entries.sortItems(2, Qt.SortOrder.DescendingOrder)),
            ("Largest first", lambda: self.entries.sortItems(3, Qt.SortOrder.DescendingOrder))], self))
        filters.addWidget(button("Up", self.go_parent, "Quiet"))
        filters.addWidget(button("Refresh", self.refresh_files, "Quiet"))
        browser.body.addLayout(filters)
        self.entries.setColumnCount(4)
        self.entries.setHorizontalHeaderLabels(["Name", "Type", "Modified", "Size"])
        self.entries.setProperty("sort_keys", ["name", "extension", "modified_at", "size"])
        self.entries.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 4):
            self.entries.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.entries.cellDoubleClicked.disconnect()
        self.entries.cellDoubleClicked.connect(self.open_entry)
        place(browser.body, self.entries, 1)
        place(browser.body, self.status)
        self.file_split.addWidget(browser)
        self.preview = LiquidPanel(parent=self.file_split)
        self.preview.setMaximumWidth(TOKENS["inspector_width"])
        self.preview_title = label("File details", "NexusSection", True)
        self.preview.body.addWidget(self.preview_title)
        self.file_metadata = properties(self.preview)
        self.preview.body.addWidget(self.file_metadata, 1)
        self.preview.body.addWidget(button("Open", lambda: self.file_action("open")))
        self.preview.body.addWidget(button("Ask Jarvix", self.ask_file, "Primary"))
        self.preview.body.addWidget(button("Inspect document", self.inspect_document))
        place(self.preview.body, self.document_action)
        self.file_split.addWidget(self.preview)
        self.preview.hide()
        self._preview_shown = False
        self.file_split.setSizes([180, 760, TOKENS["inspector_width"]])
        self.file_split.setStretchFactor(1, 1)
        self.layout.addWidget(self.file_split, 1)
        self.entries.itemSelectionChanged.connect(self.preview_selection)
        self.search_delay = QTimer(self)
        self.search_delay.setSingleShot(True)
        self.search_delay.setInterval(160)
        self.search.textChanged.disconnect()
        self.search.textChanged.connect(lambda _text: self.search_delay.start())
        self.search_delay.timeout.connect(self.refresh_files)

    def refresh(self):
        roots = self.services.file_roots()
        self.root_picker.blockSignals(True)
        self.root_picker.clear()
        self.root_picker.addItems(roots)
        self.root_picker.blockSignals(False)
        self.location_list.blockSignals(True)
        self.location_list.clear()
        for path in roots:
            item = QListWidgetItem(icon("folder"), Path(path).name or path)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.location_list.addItem(item)
        self.location_list.blockSignals(False)
        if self.location:
            try:
                self.services.files.path(self.location)
            except (ValueError, OSError):
                self.location = None
        if not self.location and roots:
            self.navigate_location(roots[0])
        else:
            self.refresh_files()

    def navigate_location(self, raw, remember=True):
        try:
            path = self.services.files.path(raw)
            if not path.is_dir():
                raise ValueError("Choose an approved folder.")
        except (ValueError, OSError) as exc:
            self.window.notify(str(exc))
            return False
        self.location = str(path)
        self.path_input.setText(self.location)
        self.path_input.setCursorPosition(0)
        for index in range(self.root_picker.count()):
            if path.is_relative_to(Path(self.root_picker.itemText(index))):
                self.root_picker.blockSignals(True)
                self.root_picker.setCurrentIndex(index)
                self.root_picker.blockSignals(False)
                break
        if remember and (self.location_position < 0 or self.location_history[self.location_position] != self.location):
            self.location_history = self.location_history[:self.location_position + 1] + [self.location]
            self.location_position += 1
        self.location_back.setEnabled(self.location_position > 0)
        self.location_forward.setEnabled(self.location_position + 1 < len(self.location_history))
        self.refresh_files()
        return True

    def go_location(self, offset):
        target = self.location_position + offset
        if 0 <= target < len(self.location_history) and self.navigate_location(self.location_history[target], False):
            self.location_position = target
            self.location_back.setEnabled(target > 0)
            self.location_forward.setEnabled(target + 1 < len(self.location_history))

    def go_parent(self):
        if self.location:
            self.navigate_location(str(Path(self.location).parent))

    def refresh_files(self):
        if not hasattr(self, "path_input"):
            return
        self._file_revision += 1
        revision = self._file_revision
        if not self.location:
            self.entries.setRowCount(0)
            self.preview_selection()
            self.path_input.clear()
            self.status.setText("Add folder access to browse local files.")
            return
        self.status.setText("Reading folder…")
        path, query = self.location, self.search.text()
        def read():
            if "files.list" not in self.services.enabled_tools():
                raise PermissionError("Folder browsing is disabled in Settings.")
            result = self.services.execute_tool("files.list", {"path": path, "recursive": bool(query), "query": query, "limit": 100, "max_bytes": 2**53})
            if not result.ok:
                raise ValueError(result.error or "Folder unavailable.")
            return result.data
        def loaded(data):
            if revision != self._file_revision or self.window.closing:
                return
            rows = data["items"]
            groups = {1: {".txt", ".md", ".pdf", ".csv", ".json", ".docx", ".xlsx", ".pptx"},
                2: {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"},
                3: {".py", ".js", ".ts", ".tsx", ".jsx", ".lua", ".rs", ".go", ".cpp", ".h"}}
            if extensions := groups.get(self.file_type.currentIndex()):
                rows = [row for row in rows if row.get("kind") == "folder" or row.get("extension") in extensions]
            fill_table(self.entries, rows, ["name", lambda r: "Folder" if r.get("kind") == "folder" else r.get("extension", "").lstrip(".").upper() or "File",
                lambda r: pretty_date(r.get("modified_at")), lambda r: "—" if r.get("size") is None else f"{r['size'] / 1024:,.1f} KB"])
            for index in range(self.entries.rowCount()):
                item = self.entries.item(index, 0)
                record = item.data(Qt.ItemDataRole.UserRole)
                native = self.file_icons.icon(QFileInfo(record["path"])) if not record["path"].startswith(("\\\\", "//")) else icon("folder" if record.get("kind") == "folder" else "file")
                item.setIcon(native)
                item.setToolTip(record["path"])
            self.preview_selection()
            self.status.setText(f"{len(rows)} items" + (" · first 100 matches" if data.get("truncated") else " · Local files"))
        def failed(message):
            if revision == self._file_revision and not self.window.closing:
                self.entries.setRowCount(0)
                self.preview_selection()
                self.status.setText(message + " · Refresh to retry.")
        self.window.run_job(read, loaded, failed)

    def open_entry(self, row, _column=0):
        item = self.entries.item(row, 0)
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if record.get("kind") == "folder":
            self.navigate_location(record["path"])
        else:
            self.file_action("open")

    def file_action(self, action):
        row = selected_record(self.entries)
        if row and action == "open" and row.get("kind") == "folder":
            self.window.open_capabilities("files.open_folder", {"path": row["path"]})
        else:
            super().file_action(action)

    def toggle_preview(self):
        self._preview_shown = not self._preview_shown
        reveal(self.preview, self._preview_shown)

    def preview_selection(self):
        row = selected_record(self.entries)
        self.preview_title.setText(row["name"] if row else "File details")
        describe(self.file_metadata, [] if not row else [("Path", row["path"]),
            ("Type", row.get("kind", "file")), ("Modified", pretty_date(row.get("modified_at"))),
            ("Size", f"{row.get('size') or 0:,} bytes")])

    def ask_file(self):
        if row := selected_record(self.entries):
            self.use_file_context()
            if self.services.settings.get("context.enabled", False):
                self.window.open_chat("Help me understand " + row["name"] + ".", send=False)


class NexusAutomationsPage(AutomationsPage):
    """Workflow library, editable sequence and properties on one workspace."""

    def __init__(self, window):
        self._workflow_drafts = {}
        super().__init__(window)
        fresh(self)
        self.layout.addWidget(title_row("Automations", button("Create workflow", window.open_workflow_builder), parent=self))
        self.workflow_split = QSplitter(self)
        self.workflow_split.setChildrenCollapsible(False)
        library = pane(self.workflow_split)
        library.setMaximumWidth(TOKENS["library_width"] + 40)
        place(library.body, self.workflow_search)
        for index in range(1, 4):
            self.workflow_entries.setColumnHidden(index, True)
        self.workflow_entries.horizontalHeader().hide()
        place(library.body, self.workflow_entries, 1)
        place(library.body, self.workflow_count)
        library.body.addWidget(menu_button("Library actions", [("Import workflow", self.import_workflow),
            ("Duplicate workflow", lambda: self.workflow_action("duplicate")),
            ("Export workflow", lambda: self.workflow_action("export")),
            ("Recurring checks", self.show_checks)], self))
        self.workflow_split.addWidget(library)
        self.flow_document = pane(self.workflow_split, margins=(20, 0, 20, 0))
        self.flow_title = label("Choose a workflow", "NexusTitle", True)
        self.flow_document.body.addWidget(self.flow_title)
        self.trigger_description = label("Reviewed actions run with the same permissions as Chat.", "Muted", True)
        self.flow_document.body.addWidget(self.trigger_description)
        self.condition_description = label("", "Muted", True)
        self.flow_document.body.addWidget(self.condition_description)
        from ..workflows import WorkflowSteps
        self.flow_steps = WorkflowSteps(self.services, parent=self.flow_document)
        self.flow_steps.blocks.setObjectName("NexusWorkflowSequence")
        self.flow_steps.blocks.setItemDelegate(WorkflowSequenceDelegate(self.flow_steps.blocks))
        self.flow_steps.blocks.setWordWrap(True)
        self.flow_steps.changed.connect(self.sequence_changed)
        self.flow_document.body.addWidget(self.flow_steps, 1)
        self.flow_status = label("", "Muted", True)
        self.flow_document.body.addWidget(self.flow_status)
        self.flow_document.body.addWidget(button("Review and save sequence", self.save_sequence, "Primary"))
        self.workflow_split.addWidget(self.flow_document)
        self.flow_inspector = LiquidPanel(parent=self.workflow_split)
        self.flow_inspector.setMaximumWidth(TOKENS["inspector_width"])
        self.flow_inspector.body.addWidget(label("Properties", "NexusSection"))
        self.flow_properties = properties(self.flow_inspector)
        self.flow_inspector.body.addWidget(self.flow_properties, 1)
        self.flow_controls = []
        for caption, callback in (("Edit configuration", self.edit_workflow),
            ("Test workflow", lambda: self.workflow_action("test")), ("Run history", self.workflow_history),
            ("Enable / disable", self.toggle_workflow), ("Run workflow", lambda: self.workflow_action("run"))):
            control = button(caption, callback, "Primary" if caption == "Run workflow" else "Quiet")
            self.flow_inspector.body.addWidget(control)
            self.flow_controls.append(control)
        self.workflow_split.addWidget(self.flow_inspector)
        self.workflow_split.setSizes([TOKENS["library_width"], 590, TOKENS["inspector_width"]])
        self.workflow_split.setStretchFactor(1, 1)
        self.layout.addWidget(self.workflow_split, 1)
        self._loading_flow = False
        self.workflow_entries.itemSelectionChanged.connect(self.select_workflow)
        self.select_workflow()

    def refresh_workflows(self, *_):
        super().refresh_workflows()
        if hasattr(self, "flow_document"):
            if self.workflow_entries.rowCount() and not selected_record(self.workflow_entries):
                self.workflow_entries.selectRow(0)
            self.select_workflow()

    def select_workflow(self):
        row = selected_record(self.workflow_entries)
        self._loading_flow = True
        self.flow_steps.blocks.clear()
        self.flow_title.setText(row["name"] if row else "Choose a workflow")
        self.flow_document.setEnabled(bool(row))
        for control in self.flow_controls:
            control.setEnabled(bool(row))
        if row:
            definition = self.services.workflows.get(row["id"])
            config = definition.get("config", {})
            trigger = definition["trigger"].replace("_", " ").capitalize()
            self.trigger_description.setText("WHEN  " + trigger + (" · " + ", ".join(str(v) for v in config.values()) if config else ""))
            self.condition_description.setText("IF  " + (", ".join(item.get("kind", "condition").replace("_", " ")
                for item in definition.get("conditions", [])) or "No additional conditions"))
            draft = self._workflow_drafts.get(row["id"])
            if draft == definition.get("steps", []):
                self._workflow_drafts.pop(row["id"], None)
                draft = None
            for step in draft if draft is not None else definition.get("steps", []):
                self.flow_steps.append(step)
            describe(self.flow_properties, [("Status", "Enabled" if definition.get("enabled") else "Disabled"),
                ("Type", definition.get("kind", "workflow")), ("Next run", pretty_date(definition.get("next_run"))),
                ("Background grants", ", ".join(definition.get("approved_tools", [])) or "None"),
                ("Steps", len(definition.get("steps", [])))])
            self.flow_status.setText("Unsaved sequence · Review changes before saving." if draft is not None else "Drag steps to reorder. Double-click to edit.")
        else:
            describe(self.flow_properties, [])
            self.trigger_description.setText("Create a reviewed sequence of existing Jarvix actions.")
            self.condition_description.clear()
            self.flow_status.clear()
        self._loading_flow = False

    def sequence_changed(self):
        if self._loading_flow:
            return
        if row := selected_record(self.workflow_entries):
            self._workflow_drafts[row["id"]] = self.flow_steps.steps()
            self.flow_status.setText("Unsaved sequence · Review changes before saving.")

    def save_sequence(self):
        if row := selected_record(self.workflow_entries):
            saved = self.services.workflows.get(row["id"])
            arguments = {key: saved[key] for key in ("id", "name", "kind", "trigger", "config", "conditions", "enabled", "approved_tools", "variables") if key in saved}
            arguments["steps"] = self.flow_steps.steps()
            self.window.open_capabilities("workflows.save", arguments)

    def show_checks(self):
        # Keep the original recurring-check controller, without making it the workspace.
        from PySide6.QtWidgets import QDialog
        dialog = QDialog(self.window.frontend if hasattr(self.window, "frontend") else self.window)
        dialog.setWindowTitle("Recurring checks")
        dialog.resize(800, 480)
        content = QVBoxLayout(dialog)
        original = self.tabs.widget(1)
        self.tabs.removeTab(1)
        original.setParent(dialog)
        content.addWidget(original)
        original.show()
        dialog.exec()
        self.tabs.addTab(original, "Recurring checks")
        original.hide()
        dialog.deleteLater()


class NexusSettingsPage(SettingsPage):
    """Fresh OS settings architecture using the existing preference controls."""

    def setting_row(self, layout, caption, control, description=""):
        self.rows.append((layout, caption, control, description))
        frame = QFrame(self)
        frame.setObjectName("LiquidSettingRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 10, 0, 10)
        words = QVBoxLayout()
        words.setSpacing(3)
        words.addWidget(label(caption, wrap=True))
        if description:
            words.addWidget(label(description, "Muted", True))
        row.addLayout(words, 3)
        control.setAccessibleName(caption)
        control.setMinimumWidth(0)
        control.setMaximumWidth(340)
        row.addWidget(control, 2)
        layout.addWidget(frame)
        self.row_widgets[control] = frame

    def __init__(self, window):
        self.rows, self.row_widgets = [], {}
        super().__init__(window)
        old_sections = dict(self.settings_sections)
        controls = {control.text(): control for control in self.findChildren(QPushButton)}
        old_categories = self.categories
        old_stack = self.settings_stack
        for _layout, _caption, control, _description in self.rows:
            self.row_widgets[control].setParent(self)
        for control in controls.values():
            control.setParent(self)
        for checkbox in self.tool_checks.values():
            checkbox.setParent(self)
        self.tool_search.setParent(self)
        fresh(self)
        self.layout.addWidget(title_row("Settings", parent=self))
        self.settings_split = QSplitter(self)
        self.settings_split.setChildrenCollapsible(False)
        category_pane = pane(self.settings_split)
        category_pane.setMaximumWidth(TOKENS["library_width"])
        self.category_search = QLineEdit(category_pane)
        self.category_search.setPlaceholderText("Find settings…")
        self.category_search.setAccessibleName("Find settings category")
        category_pane.body.addWidget(self.category_search)
        self.categories = QListWidget(category_pane)
        self.categories.setAccessibleName("Settings categories")
        category_pane.body.addWidget(self.categories, 1)
        self.settings_stack = QStackedWidget(self.settings_split)
        self.settings_sections = {}
        self.category_names = ("General", "Appearance", "Models", "Privacy", "Permissions", "Files", "Browser", "Voice", "Automations", "Integrations", "Extensions", "Advanced")
        self.category_icons = {"General": "settings", "Appearance": "home", "Models": "chat", "Privacy": "memory",
            "Permissions": "check", "Files": "files", "Browser": "link", "Voice": "voice", "Automations": "automations",
            "Integrations": "integrations", "Extensions": "apps", "Advanced": "terminal"}
        for name in self.category_names:
            self.categories.addItem(QListWidgetItem(icon(self.category_icons[name]), name))
            scroll = QScrollArea(self.settings_stack)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            body = pane(scroll, margins=(28, 2, 22, 8))
            body.setMaximumWidth(TOKENS["content_width"] - TOKENS["library_width"])
            body.body.addWidget(label(name, "NexusTitle"))
            self.settings_sections[name] = body.body
            scroll.setWidget(body)
            self.settings_stack.addWidget(scroll)
        self.categories.currentRowChanged.connect(self.select_settings)
        self.categories.setCurrentRow(0)
        self.category_search.textChanged.connect(self.filter_categories)
        self.settings_split.addWidget(category_pane)
        self.settings_split.addWidget(self.settings_stack)
        self.settings_split.setSizes([TOKENS["library_width"], 850])
        self.settings_split.setStretchFactor(1, 1)
        self.layout.addWidget(self.settings_split, 1)
        mapped = {"AI & models": "Models", "Privacy & control": "Privacy", "Files & storage": "Files", "Appearance": "General"}
        for source, _caption, control, _description in self.rows:
            if isinstance(control, QLabel) and control.text() == "Dark":
                self.row_widgets[control].hide()
                continue
            old_name = next(name for name, layout in old_sections.items() if layout is source)
            target = mapped.get(old_name, old_name)
            if control is self.interface_selector:
                target = "Appearance"
            elif control is self.access_checks.get("control.enabled"):
                target = "Permissions"
            destination = self.settings_sections[target]
            place(destination, self.row_widgets[control])
        self.interface_selector.setItemText(self.interface_selector.findData("new"), "NEXUS")
        self.material_controls = {}
        for caption, key, choices in (
            ("Theme", "liquid.theme", [("Cream", "cream"), ("Frost", "frost"), ("Graphite", "graphite"), ("Sand", "sand")]),
            ("Glass intensity", "liquid.glass", [("Frosted", "frosted"), ("Subtle", "subtle"), ("Solid", "solid")]),
            ("Density", "liquid.density", [("Comfortable", "comfortable"), ("Compact", "compact")]),
            ("Accent", "liquid.accent", [("Theme default", "theme"), ("Teal", "teal"), ("Slate", "slate")]),
        ):
            control = QComboBox(self)
            for text, value in choices:
                control.addItem(text, value)
            self.material_controls[key] = control
            self.setting_row(self.settings_sections["Appearance"], caption, control)
        for caption, key in (("Transparency", "liquid.transparency"), ("Motion", "liquid.motion"), ("Reduce motion", "liquid.reduced_motion")):
            control = QCheckBox("Enabled", self)
            self.material_controls[key] = control
            self.setting_row(self.settings_sections["Appearance"], caption, control)
        self.nexus_theme = self.material_controls["liquid.theme"]
        self.glass_intensity = self.material_controls["liquid.glass"]
        self.transparency = self.material_controls["liquid.transparency"]
        self.motion_enabled = self.material_controls["liquid.motion"]
        self.reduced_motion = self.material_controls["liquid.reduced_motion"]
        self.density = self.material_controls["liquid.density"]
        self.accent = self.material_controls["liquid.accent"]
        destinations = {
            "Models": ["Configure local models and routing", "Manage AI credentials"],
            "Voice": ["Find installed voices", "Open voice controls"],
            "Browser": ["Inspect browser connection"],
            "Automations": ["Manage workflows", "Review closed-app scheduling"],
            "Files": ["Manage allowed folders", "Create backup", "Open data folder"],
            "Advanced": ["Inspect diagnostics", "Review storage protection", "Inspect Windows startup"],
        }
        for name, captions in destinations.items():
            for caption in captions:
                if caption in controls:
                    control = controls[caption]
                    if "AI" in caption:
                        control.setText("Manage model credentials")
                    place(self.settings_sections[name], control)
        self.settings_sections["Permissions"].addWidget(label("Sensitive actions always need fresh confirmation.", "Muted", True))
        self.settings_sections["Integrations"].addWidget(button("Manage connected accounts", lambda: window.navigate("Integrations")))
        self.settings_sections["Extensions"].addWidget(button("Manage extensions", lambda: window.open_adaptive("Extensions")))
        self.settings_sections["Advanced"].addWidget(label("Capabilities", "NexusSection"))
        place(self.settings_sections["Advanced"], self.tool_search)
        selection = QHBoxLayout()
        for caption in ("Enable all capabilities", "Disable all capabilities"):
            place(selection, controls[caption])
        self.settings_sections["Advanced"].addLayout(selection)
        for checkbox in self.tool_checks.values():
            place(self.settings_sections["Advanced"], checkbox)
        place(self.settings_sections["Files"], self.backup_status)
        for content in self.settings_sections.values():
            content.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(label("Models and access apply after saving. Appearance applies immediately.", "Muted", True), 1)
        footer.addWidget(button("Save settings", self.save, "Primary"))
        self.layout.addLayout(footer)
        old_categories.hide()
        old_stack.hide()
        self.load_materials()
        for control in self.material_controls.values():
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self.save_materials)
            else:
                control.toggled.connect(self.save_materials)

    def filter_categories(self, query):
        visible = []
        for index in range(self.categories.count()):
            item = self.categories.item(index)
            item.setHidden(query.casefold() not in item.text().casefold())
            if not item.isHidden():
                visible.append(index)
        if visible and self.categories.currentRow() not in visible:
            self.categories.setCurrentRow(visible[0])

    def select_settings(self, index):
        snapshot = self.settings_stack.grab() if self.settings_stack.isVisible() and enabled(self.settings_stack) else None
        self.settings_stack.setCurrentIndex(index)
        if snapshot is not None:
            Transition(self.settings_stack, snapshot)

    def load_materials(self):
        defaults = {"liquid.theme": "cream", "liquid.glass": "frosted", "liquid.density": "comfortable", "liquid.accent": "theme",
            "liquid.transparency": True, "liquid.motion": True, "liquid.reduced_motion": False}
        for key, control in self.material_controls.items():
            value = self.services.settings.get(key, defaults[key])
            control.blockSignals(True)
            if isinstance(control, QComboBox):
                control.setCurrentIndex(max(0, control.findData(value)))
            else:
                control.setChecked(bool(value))
            control.blockSignals(False)

    def save_materials(self, *_args):
        def save():
            for key, control in self.material_controls.items():
                self.services.settings.set(key, control.currentData() if isinstance(control, QComboBox) else control.isChecked())
            self.window.configure_materials()
        self.guard(save)

    def refresh(self):
        super().refresh()
        if hasattr(self, "material_controls"):
            self.load_materials()

    def refresh_materials(self):
        preferences = getattr(owner(self), "_liquid_preferences", {})
        for index in range(self.categories.count()):
            item = self.categories.item(index)
            item.setIcon(icon(self.category_icons[item.text()], preferences))


class NexusSystemPage(SystemPage):
    """Utility-style metrics strip and sortable process workspace."""

    def __init__(self, window):
        super().__init__(window)
        fresh(self)
        self.layout.addWidget(title_row("System", button("Refresh", window.refresh_system), parent=self))
        metrics = LiquidToolbar(self)
        strip = QHBoxLayout()
        place(strip, self.cpu, 1)
        place(strip, self.memory, 1)
        strip.setAlignment(self.cpu, Qt.AlignmentFlag.AlignTop)
        strip.setAlignment(self.memory, Qt.AlignmentFlag.AlignTop)
        for name, value in self.device_status.items():
            column = pane(metrics)
            column.body.addWidget(label(name, "Muted"))
            place(column.body, value)
            strip.addWidget(column, 1)
        metrics.body.addLayout(strip)
        self.layout.addWidget(metrics)
        self.system_tabs = QTabWidget(self)
        processes = pane(self.system_tabs)
        toolbar = QHBoxLayout()
        place(toolbar, self.search, 1)
        toolbar.addWidget(button("Inspect CPU usage", lambda: window.open_capabilities("processes.search", {"sort": "cpu"}), "Quiet"))
        processes.body.addLayout(toolbar)
        place(processes.body, self.processes, 1)
        place(processes.body, self.status)
        self.system_tabs.addTab(processes, "Processes")
        for name, tool in (("Storage", "system.storage"), ("Network", "system.network"), ("Devices", "system.device")):
            view = pane(self.system_tabs, margins=(8, 12, 8, 8))
            view.body.addWidget(label(name, "NexusSection"))
            view.body.addWidget(label("Inspect an explicit local snapshot. No private activity is recorded.", "Muted", True))
            view.body.addWidget(button("Inspect " + name.lower(), lambda tool=tool: window.open_capabilities(tool)))
            view.body.addStretch()
            self.system_tabs.addTab(view, name)
        self.layout.addWidget(self.system_tabs, 1)


class NexusTasksPage(TasksPage):
    """Fast task rows, with views separate from creation and completion."""

    def __init__(self, window):
        super().__init__(window)
        fresh(self)
        self.layout.addWidget(title_row("Tasks", parent=self))
        toolbar = QHBoxLayout()
        self.view.blockSignals(True)
        self.view.clear()
        for caption, value in (("Today", "today"), ("Upcoming", "upcoming"), ("All", "all"), ("Completed", "completed")):
            self.view.addItem(caption, value)
        self.view.blockSignals(False)
        place(toolbar, self.view)
        place(toolbar, self.search, 1)
        self.layout.addLayout(toolbar)
        self.entries.setColumnCount(5)
        self.entries.setHorizontalHeaderLabels(["Done", "Task", "Project", "Due", "Priority"])
        self.entries.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.entries.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        place(self.layout, self.entries, 1)
        quick = LiquidToolbar(self)
        row = QHBoxLayout()
        place(row, self.entry, 2)
        place(row, self.due, 1)
        row.addWidget(button("Add task", self.add, "Primary"))
        quick.body.addLayout(row)
        self.layout.addWidget(quick)
        footer = QHBoxLayout()
        place(footer, self.count, 1)
        footer.addWidget(menu_button("Task actions", [("Mark complete", self.complete), ("Delete task", self.delete)], self))
        self.layout.addLayout(footer)
        self.entries.itemChanged.connect(self.check_task)

    def refresh(self):
        if self.view.currentData() is None:
            return super().refresh()
        result = self.services.productivity.tasks.search(query=self.search.text(), view=self.view.currentData())
        projects = {row["id"]: row["name"] for row in self.services.list_projects()}
        self.entries.blockSignals(True)
        fill_table(self.entries, result["items"], [lambda _: "", "title", lambda r: projects.get(r.get("project_id"), "—"),
            lambda r: pretty_date(r.get("due_at")), lambda r: r.get("priority", "normal").title()])
        for index in range(self.entries.rowCount()):
            item = self.entries.item(index, 0)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole)["status"] in {"done", "completed"} else Qt.CheckState.Unchecked)
        self.entries.blockSignals(False)
        self.count.setText(f"{len(result['items'])} tasks" + (" · first 100 matches" if result["truncated"] else ""))

    def check_task(self, item):
        if item.column() != 0:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if not row:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        if checked == (row["status"] in {"done", "completed"}):
            return
        self.entries.selectRow(item.row())
        if checked:
            self.complete()
        else:
            self.window.open_capabilities("tasks.reopen", {"id": row["id"]})
            self.refresh()


class NexusNotesPage(NotesPage):
    """A local document editor, with its library and metadata kept contextual."""

    def __init__(self, window):
        super().__init__(window)
        for control in (self.search, self.list, self.title_edit, self.body, self.state):
            control.setParent(self)
        fresh(self)
        self.layout.addWidget(title_row("Notes", button("New note", self.new),
            button("Details", self.toggle_details, "Quiet"), parent=self))
        self.note_split = QSplitter(self)
        self.note_split.setChildrenCollapsible(False)
        library = pane(self.note_split)
        library.setMaximumWidth(TOKENS["library_width"] + 40)
        self.note_folders = QComboBox(library)
        self.note_folders.addItem("All folders", None)
        self.note_folders.setAccessibleName("Note folders")
        self.note_folders.currentIndexChanged.connect(self.refresh_list)
        library.body.addWidget(self.note_folders)
        place(library.body, self.search)
        place(library.body, self.list, 1)
        self.list.setWordWrap(True)
        self.note_split.addWidget(library)
        document = pane(self.note_split, margins=(20, 0, 12, 0))
        self.title_edit.setObjectName("NexusNoteTitle")
        self.title_edit.setPlaceholderText("Untitled note")
        place(document.body, self.title_edit)
        self.body.setObjectName("NexusNoteDocument")
        place(document.body, self.body, 1)
        footer = QHBoxLayout()
        place(footer, self.state, 1)
        footer.addWidget(menu_button("Note actions", [("Delete note", self.delete),
            ("Version history", lambda: self.note_action("notes.history")),
            ("Convert to task", lambda: self.note_action("tasks.from_note")),
            ("Export note", lambda: self.note_action("notes.export"))], self))
        footer.addWidget(button("Save note", self.save, "Primary"))
        document.body.addLayout(footer)
        self.note_split.addWidget(document)
        self.note_details = LiquidPanel(parent=self.note_split)
        self.note_details.setMaximumWidth(TOKENS["inspector_width"])
        self.note_details.body.addWidget(label("Details", "NexusSection"))
        self.note_metadata = properties(self.note_details)
        self.note_details.body.addWidget(self.note_metadata, 1)
        self.note_details.body.addWidget(button("Edit links and tags", lambda: self.note_action("notes.organize")))
        self.note_split.addWidget(self.note_details)
        self.note_details.hide()
        self._note_details_shown = False
        self.note_split.setSizes([TOKENS["library_width"], 700, TOKENS["inspector_width"]])
        self.note_split.setStretchFactor(1, 1)
        self.layout.addWidget(self.note_split, 1)

    def refresh_list(self):
        if not hasattr(self, "note_folders"):
            return super().refresh_list()
        metadata = {item["id"]: item for item in self.services.records.list("note.meta")}
        records = [{**note, **{key: value for key, value in metadata.get(note["id"], {}).items()
                    if key not in {"id", "created_at", "updated_at"}}} for note in self.services.list_notes()]
        selected = self.note_folders.currentData()
        self.note_folders.blockSignals(True)
        self.note_folders.clear()
        self.note_folders.addItem("All folders", None)
        for folder in sorted({record.get("folder") for record in records if record.get("folder")}):
            self.note_folders.addItem(folder, folder)
        self.note_folders.setCurrentIndex(max(0, self.note_folders.findData(selected)))
        self.note_folders.blockSignals(False)
        folder = self.note_folders.currentData()
        query = self.search.text().casefold()
        self.list.blockSignals(True)
        self.list.clear()
        for record in records:
            if (folder and record.get("folder") != folder) or query not in (record["title"] + " " + record["body"]).casefold():
                continue
            item = QListWidgetItem(icon("notes"), record["title"] + "\n" + pretty_date(record.get("updated_at")))
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.list.addItem(item)
            if record["id"] == self.note_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def set_note(self, note):
        super().set_note(note)
        if hasattr(self, "note_metadata"):
            try:
                metadata = self.services.records.get("note.meta", self.note_id) if self.note_id else {}
            except ValueError:
                metadata = {}
            describe(self.note_metadata, [("Folder", metadata.get("folder") or "None"),
                ("Tags", ", ".join(metadata.get("tags", [])) or "None"), ("Updated", pretty_date(note.get("updated_at"))),
                ("Project", metadata.get("project_id") or "Not linked"), ("Task", metadata.get("task_id") or "Not linked")])

    def toggle_details(self):
        self._note_details_shown = not self._note_details_shown
        reveal(self.note_details, self._note_details_shown)

    def note_action(self, tool):
        if self.note_id:
            self.window.open_capabilities(tool, {"note_id" if tool == "tasks.from_note" else "id": self.note_id})
        else:
            self.window.notify("Save the note first.")


class NexusAppsPage(AppsPage):
    """Compact native-icon library, with launch actions kept beside selection."""

    def __init__(self, window):
        super().__init__(window)
        fresh(self)
        self.layout.addWidget(title_row("Applications", button("Register app", self.add), parent=self))
        toolbar = QHBoxLayout()
        self.launcher_view = QComboBox(self)
        self.launcher_view.addItems(["All applications", "Favorites", "Recently launched"])
        self.launcher_view.setAccessibleName("Application view")
        self.launcher_view.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.launcher_view)
        place(toolbar, self.search, 1)
        toolbar.addWidget(button("Discover applications", lambda: window.open_capabilities("apps.discover"), "Quiet"))
        self.layout.addLayout(toolbar)
        self.app_split = QSplitter(self)
        self.app_split.setChildrenCollapsible(False)
        library = pane(self.app_split)
        self.entries.setColumnCount(3)
        self.entries.setHorizontalHeaderLabels(["Application", "Aliases", "Last launched"])
        self.entries.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.entries.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.entries.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.entries.verticalHeader().setDefaultSectionSize(44)
        place(library.body, self.entries, 1)
        place(library.body, self.empty)
        self.app_split.addWidget(library)
        inspector = self.app_inspector = LiquidPanel(parent=self.app_split)
        inspector.setMaximumWidth(TOKENS["inspector_width"])
        self.app_name = label("Select an application", "NexusSection", True)
        self.app_path = label("", "Code", True)
        inspector.body.addWidget(self.app_name)
        inspector.body.addWidget(self.app_path)
        inspector.body.addStretch()
        self.app_controls = [button("Launch application", self.launch, "Primary"),
            button("Aliases and favorite", lambda: self.app_action("apps.configure")),
            button("Open installation folder", lambda: self.app_action("apps.installation_folder"), "Quiet")]
        for control in self.app_controls:
            inspector.body.addWidget(control)
        self.app_split.addWidget(inspector)
        inspector.hide()
        self._app_details_shown = False
        self.app_split.setStretchFactor(0, 1)
        self.app_split.setSizes([760, TOKENS["inspector_width"]])
        self.layout.addWidget(self.app_split, 1)
        self.file_icons = QFileIconProvider()
        self.entries.itemSelectionChanged.connect(self.app_selection)
        self.app_selection()

    def refresh(self):
        if not hasattr(self, "launcher_view"):
            return super().refresh()
        selected = selected_record(self.entries)
        rows = self.services.apps.list(self.search.text(), favorites_only=self.launcher_view.currentIndex() == 1,
            recent_only=self.launcher_view.currentIndex() == 2)["items"]
        self.entries.blockSignals(True)
        fill_table(self.entries, rows, ["name", lambda row: ", ".join(row.get("aliases", [])), lambda row: pretty_date(row.get("last_launched_at"))])
        for index in range(self.entries.rowCount()):
            item = self.entries.item(index, 0)
            path = item.data(Qt.ItemDataRole.UserRole)["path"]
            # Network shell icon handlers can block; retrieve icons only for local files.
            native = self.file_icons.icon(QFileInfo(path)) if not path.startswith(("\\\\", "//")) else icon("apps")
            item.setIcon(native if not native.isNull() else icon("apps"))
            item.setToolTip(path)
        self.empty.setText(f"{len(rows)} applications" if rows else "Register an application or discover installed apps.")
        if selected:
            for index in range(self.entries.rowCount()):
                if self.entries.item(index, 0).data(Qt.ItemDataRole.UserRole)["id"] == selected["id"]:
                    self.entries.selectRow(index)
                    break
        self.entries.blockSignals(False)
        self.app_selection()

    def app_selection(self):
        row = selected_record(self.entries)
        self.app_name.setText(row["name"] if row else "Select an application")
        self.app_path.setText(row["path"] if row else "")
        if self._app_details_shown != bool(row):
            self._app_details_shown = bool(row)
            if self.isVisible():
                reveal(self.app_inspector, bool(row))
            else:
                self.app_inspector.setVisible(bool(row))
        for control in self.app_controls:
            control.setEnabled(bool(row))

    def app_action(self, tool):
        if row := selected_record(self.entries):
            self.window.open_capabilities(tool, {"id": row["id"]})
