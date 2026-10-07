"""Jarvix desktop shell, command palette, and background-job lifetime management."""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QByteArray, QObject, Signal, QSize, QEvent
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QStackedWidget,
    QDialog, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QApplication,
    QSystemTrayIcon, QMenu, QScrollArea,
)

from .theme import STYLESHEET, configure_fonts
from .widgets import label, button, icon_button, Job, TextPreview, ApprovalBridge
from .icons import icon
from .pages import (
    HomePage, NotesPage, MemoryPage, TasksPage, FilesPage, AppsPage, SystemPage,
    ActivityPage, VoicePage, AutomationsPage, SettingsPage, ProjectsPage,
)
from .integrations import IntegrationsPage
from .chat import ChatPage, PermissionDialog
from .capabilities import CapabilityDialog, ToolJob
from .operator import DesktopIndicator, OperatorDialog, ServiceJob
from .overlay import CommandOverlay, GlobalHotkey, WorkflowHotkeys


NAVIGATION = [
    ("Home", "home"), ("Chat", "chat"), ("Voice", "voice"), ("Tasks", "tasks"),
    ("Memory", "memory"), ("Notes", "notes"), ("Files", "files"), ("Apps", "apps"),
    ("Automations", "automations"), ("Integrations", "integrations"), ("System", "system"),
    ("Activity", "activity"), ("Settings", "settings"),
]
SIDEBAR_GROUPS = (
    ("CORE", ("Home", "Chat", "Operator")),
    ("WORK", ("Missions", "Projects", "Tasks", "Notes", "Knowledge", "Memory")),
    ("AUTOMATE", ("Automations", "Skills")),
    ("SYSTEM", ("Files", "Apps", "Integrations", "System", "Voice")),
)


class RuntimeSignals(QObject):
    event = Signal(str, object)
    approval = Signal(object)
    dismiss_approval = Signal(object)


class CommandPalette(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.browser_worker = None
        self.pending_dialog = None
        self.setWindowTitle("Jarvix · Command palette")
        self.resize(700, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(label("Command palette", "Heading"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search locally or describe what you want to do…")
        self.search.setMinimumHeight(34)
        self.search.setAccessibleName("Search commands and local resources")
        self.search.textChanged.connect(self.search_entries)
        self.search.returnPressed.connect(self.execute)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(self.execute)
        layout.addWidget(self.results)
        browser_row = QHBoxLayout()
        self.browser_button = button("Include connected browser tabs", self.load_tabs, "Quiet")
        browser_row.addWidget(self.browser_button)
        browser_row.addStretch()
        browser_row.addWidget(button("Draft in Chat", self.draft, "Quiet"))
        layout.addLayout(browser_row)
        self.status = label("↑ ↓ to explore    Enter to open    Esc to dismiss    ·    Search stays local", "Muted", True)
        layout.addWidget(self.status)
        self.entries = self.build_entries()
        self.search_entries("")

    def build_entries(self):
        entries = []
        window = self.window
        services = window.services
        for name in window.nav_buttons:
            entries.append(("Open " + name, "Navigate", lambda dest=name: window.navigate(dest)))
        entries.extend([
            ("New conversation", "Command", lambda: (window.navigate("Chat"), window.pages["Chat"].new_conversation())),
            ("Create a note", "Command", lambda: (window.navigate("Notes"), window.pages["Notes"].new())),
            ("Create a task or reminder", "Command", lambda: (window.navigate("Tasks"), window.pages["Tasks"].entry.setFocus())),
            ("Stop speaking", "Voice", services.stop_speaking),
            ("Configure API keys", "Settings", lambda: window.navigate("Integrations")),
            ("Provider and model settings", "Settings", lambda: window.navigate("Settings")),
            ("Tool permissions and capabilities", "Settings", lambda: window.navigate("Settings")),
            ("Local actions", "Command", window.open_capabilities),
            ("Operator sessions", "Command", window.open_operator),
            ("Knowledge spaces", "Knowledge", lambda: window.open_adaptive("Knowledge")),
            ("Semantic search", "Search", window.open_adaptive),
            ("Local models and routing", "Settings", lambda: window.open_adaptive("Models")),
            ("Extension manager", "Settings", lambda: window.open_adaptive("Extensions")),
            ("Missions", "Command", lambda: window.open_adaptive("Missions")),
            ("Continue saved work", "Command", lambda: window.open_adaptive("Continue")),
            ("Review latest checkpoint", "Command", lambda: window.open_capabilities("continuity.prepare")),
            ("Personal context profiles", "Settings", lambda: window.open_adaptive("Personal context")),
            ("Learned skills", "Command", lambda: window.open_adaptive("Skills")),
            ("Daily brief", "Command", lambda: window.open_adaptive("Daily brief")),
            ("Jarvix health", "Command", lambda: window.open_adaptive("Health")),
            ("Execution evaluation", "Command", lambda: window.open_adaptive("Evaluation")),
            ("Backup and restore", "Command", lambda: window.open_adaptive("Backups")),
            ("Trusted devices", "Command", lambda: window.open_adaptive("Devices")),
            ("Release updates", "Command", lambda: window.open_adaptive("Updates")),
            ("Prepare project release", "Command", lambda: window.open_capabilities("intelligence.plan", {"recipe": "release_review"})),
            ("Continue current project", "Project", lambda: window.open_capabilities("intelligence.prepare")),
            ("Local suggestions", "Command", lambda: window.open_adaptive("Suggestions")),
            ("Context graph", "Command", lambda: window.open_capabilities("context.graph")),
            ("Quick command overlay", "Command", window.open_overlay),
            ("Build a workflow", "Automation", window.open_workflow_builder),
            ("Manage workspaces", "Command", window.open_workspaces),
            ("Notifications inbox", "Command", window.open_notifications),
            ("Update file index", "Command", lambda: (window.navigate("Files"), window.pages["Files"].scan())),
        ])
        for note in services.list_notes():
            entries.append((note["title"], "Note", lambda record=note: window.open_note(record["id"])))
        for conversation in services.list_conversations():
            entries.append((conversation["title"], "Conversation",
                            lambda record=conversation: window.open_conversation(record["id"])))
        for integration in services.integrations.status():
            entries.append((integration["name"] + " · " + integration["status"], "Integration",
                            lambda: window.navigate("Integrations")))
        for app in services.list_apps():
            entries.append((app["name"], "Application", lambda record=app: window.open_capabilities("apps.open", {"id": record["id"]})))
        for project in services.list_projects():
            entries.append((project["name"], "Project", lambda record=project: window.open_folder(record["path"])))
            entries.append(("Continue " + project["name"], "Project",
                            lambda record=project: window.open_capabilities("intelligence.prepare", {
                                "project_id": record["id"], "goal": ("Continue " + record["name"])[:500]})))
        missions = services.execute_tool("missions.list", {})
        if missions.ok:
            for mission in missions.data.get("items", []):
                entries.append((mission["goal"], "Mission", lambda record=mission:
                                window.open_capabilities("missions.summary", {"id": record["id"]})))
        for file in services.list_files():
            entries.append((file.get("name", file["path"]), "File", lambda record=file: TextPreview(record.get("name", "File"), "Indexed metadata; file contents remain unread.", json.dumps(record, indent=2, default=str), window).exec()))
        for task in services.list_tasks():
            entries.append((task["title"], "Task", lambda record=task: window.open_task(record["id"])))
        for routine in services.records.list("routine"):
            entries.append((routine["name"], "Automation", lambda record=routine: window.open_capabilities("automations.preview", {"id": record["id"]})))
        for workspace in services.records.list("workspace"):
            entries.append((workspace["name"], "Workspace", lambda record=workspace: window.open_capabilities("workspaces.launch", {"id": record["id"]})))
        if hasattr(services, "workflows"):
            for workflow in services.workflows.list():
                entries.append((workflow["name"], "Routine" if workflow.get("kind") == "routine" else "Workflow",
                                lambda record=workflow: window.open_capabilities("workflows.run", {"id": record["id"]})))
        specs = services.registry.specs()
        tool_names = {spec.name for spec in specs}
        for name in services.settings.get("commands.favorites", []):
            if name in tool_names:
                entries.append((name, "Favorite command", lambda tool=name: window.open_capabilities(tool)))
        for name in services.settings.get("commands.recent", []):
            if name in tool_names:
                entries.append((name, "Recent command", lambda tool=name: window.open_capabilities(tool)))
        for spec in specs:
            entries.append((spec.name + " · " + spec.description, "Tool", lambda tool=spec: window.open_capabilities(tool.name)))
        for activity in services.activity(10):
            entries.append((activity.get("summary", "Recent action"), "Recent action", lambda: window.navigate("Activity")))
        for command in services.plugins.contributions("commands"):
            entries.append((command.get("title", command["name"]), "Extension command",
                            lambda item=command: window.open_capabilities(item["target"])))
        return entries

    def draft(self):
        text = self.search.text().strip()
        if text:
            self.accept()
            self.window.open_chat(text, send=False)

    def load_tabs(self):
        if self.browser_worker:
            return
        self.browser_worker = ToolJob(self.window.services, "browser.tabs", {}, self.window)
        self.window.jobs.add(self.browser_worker)
        self.browser_worker.approval.connect(self.approve_tabs)
        self.browser_worker.succeeded.connect(self.tabs_loaded)
        self.browser_worker.failed.connect(self.status.setText)
        self.browser_worker.finished.connect(self.tabs_finished)
        self.browser_button.setEnabled(False)
        self.status.setText("Reading explicitly connected tabs…")
        self.browser_worker.start()

    def approve_tabs(self, bridge):
        try:
            if not self.browser_worker or self.browser_worker.cancel.is_set() or self.window.closing:
                return
            self.pending_dialog = PermissionDialog(bridge.request, self)
            bridge.answer = self.pending_dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.pending_dialog = None
            bridge.ready.set()

    def tabs_loaded(self, result):
        if not result.ok:
            self.status.setText(result.error or "Browser is not connected.")
            return
        self.entries = [item for item in self.entries if item[1] != "Browser tab"]
        for tab in result.data.get("items", []):
            self.entries.append((tab["title"] + " · " + tab["url"], "Browser tab",
                                 lambda record=tab: self.window.open_capabilities("browser.tab_switch", {"tab_id": record["id"]})))
        self.search_entries(self.search.text())
        self.status.setText("Tab titles and sanitized URLs loaded locally for this search only.")

    def tabs_finished(self):
        worker, self.browser_worker = self.browser_worker, None
        self.window.release_job(worker)
        self.browser_button.setEnabled(True)

    def done(self, result):
        if self.browser_worker:
            self.browser_worker.cancel.set()
        if self.pending_dialog:
            self.pending_dialog.reject()
        super().done(result)

    def search_entries(self, text):
        self.results.clear()
        query = text.casefold().split()
        scored = []
        for title, category, action in self.entries:
            candidate = (title + " " + category).casefold()
            score = 2 if all(word in candidate for word in query) else 0
            if query and " ".join(query) == title.casefold():
                score = 5
            elif query and title.casefold().startswith(" ".join(query)):
                score = 4
            elif query and all(word in title.casefold() for word in query):
                score = 3
            if not score and query:
                words = candidate.replace(".", " ").replace("_", " ").split()
                if all(len(word) > 2 and any(SequenceMatcher(None, word, other).ratio() >= .8
                                             for other in words) for word in query):
                    score = 1
            if score:
                scored.append((score, title, category, action))
        for _, title, category, action in sorted(scored, key=lambda row: -row[0]):
            item = QListWidgetItem(title + "\n" + category)
            item.setData(Qt.ItemDataRole.UserRole, action)
            self.results.addItem(item)
            if self.results.count() >= 80:
                break
        if self.results.count():
            self.results.setCurrentRow(0)
        elif text.strip():
            item = QListWidgetItem("Ask Jarvix: " + text + "\nDraft in Chat · review before sending")
            item.setData(Qt.ItemDataRole.UserRole, self.draft)
            self.results.addItem(item)
            self.results.setCurrentRow(0)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Down and self.search.hasFocus():
            self.results.setFocus()
            if self.results.count():
                self.results.setCurrentRow(0)
            return
        super().keyPressEvent(event)

    def execute(self, *_):
        item = self.results.currentItem()
        if item:
            action = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
            self.window.guard(action)


class MainWindow(QMainWindow):
    # Interface subclasses replace presentation only; all runtime ownership stays here.
    interface_mode = "legacy"
    interface_stylesheet = STYLESHEET
    page_constructors = {}
    operator_type = OperatorDialog
    adaptive_type = None

    def __init__(self, services):
        super().__init__()
        configure_fonts()
        self.services = services
        self.jobs = set()
        self.snapshot = {}
        self.closing = False
        self.system_busy = False
        self.routines_busy = False
        self.reminder_dialogs = []
        self.capability_dialog = None
        self.operator_dialog = None
        self.adaptive_dialog = None
        self.workflow_dialogs = []
        self.supervised_dialogs = []
        self.workspace_dialog = None
        self.hotkey_jobs = {}
        self.tray = None
        self._exit_requested = False
        self.navigation_history = []
        self.navigation_position = -1
        self.current_page = "Home"
        self.setWindowTitle("Jarvix")
        self.setWindowIcon(QIcon(str(Path(__file__).parent.parent / "assets" / "jarvix.svg")))
        self.setMinimumSize(860, 600)
        self.resize(1280, 840)
        self.setStyleSheet(self.interface_stylesheet)
        root = QWidget()
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = self.sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(174)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 10, 8, 8)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(5)
        self.brand_label = label("Jarvix", "Brand")
        brand.addWidget(self.brand_label)
        brand.addStretch()
        self.sidebar_toggle = icon_button("menu", "Collapse navigation", self.toggle_sidebar)
        brand.addWidget(self.sidebar_toggle)
        side.addLayout(brand)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_scroll.setObjectName("SidebarScroll")
        nav_content = QWidget()
        nav_content.setObjectName("SidebarContent")
        nav_layout = QVBoxLayout(nav_content)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(2)
        self.nav_buttons = {}
        self.sidebar_labels = []
        for group, names in SIDEBAR_GROUPS:
            heading = label(group, "SidebarGroup")
            heading.setContentsMargins(8, 12, 0, 4)
            self.sidebar_labels.append(heading)
            nav_layout.addWidget(heading)
            for name in names:
                nav_layout.addWidget(self.navigation_button(name))
        nav_layout.addStretch()
        nav_scroll.setWidget(nav_content)
        side.addWidget(nav_scroll, 1)
        divider = QFrame()
        divider.setObjectName("Divider")
        side.addWidget(divider)
        for name in ("Activity", "Settings"):
            side.addWidget(self.navigation_button(name))
        self.sidebar_collapsed = False
        self.toggle_sidebar(self.services.settings.get("ui.sidebar_collapsed", False), persist=False)
        main.addWidget(sidebar)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(48)
        top = QHBoxLayout(topbar)
        top.setContentsMargins(12, 0, 12, 0)
        top.setSpacing(4)
        self.back_button = icon_button("chevron-left", "Back · Alt+Left", lambda: self.go_history(-1))
        self.forward_button = icon_button("chevron-right", "Forward · Alt+Right", lambda: self.go_history(1))
        top.addWidget(self.back_button)
        top.addWidget(self.forward_button)
        self.breadcrumb = label("Home", "Muted")
        top.addWidget(self.breadcrumb)
        top.addStretch()
        self.clock_label = label("", "Muted")
        top.addWidget(self.clock_label)
        top.addSpacing(8)
        top.addWidget(button("Tools", self.open_capabilities, "Quiet"))
        top.addWidget(button("Notifications", self.open_notifications, "Quiet"))
        top.addWidget(button("Search · Ctrl+K", self.open_palette))
        workspace_layout.addWidget(topbar)
        self.stack = QStackedWidget()
        workspace_layout.addWidget(self.stack, 1)
        main.addWidget(workspace, 1)
        constructors = {
            "Home": HomePage, "Chat": ChatPage, "Voice": VoicePage, "Tasks": TasksPage,
            "Memory": MemoryPage, "Notes": NotesPage, "Files": FilesPage, "Apps": AppsPage,
            "Automations": AutomationsPage, "Integrations": IntegrationsPage, "System": SystemPage,
            "Activity": ActivityPage, "Settings": SettingsPage,
        }
        constructors.update(self.page_constructors)
        self.pages = {}
        for name, _ in NAVIGATION:
            page = constructors[name](self)
            self.pages[name] = page
            self.stack.addWidget(page)
        self.pages["Projects"] = constructors.get("Projects", ProjectsPage)(self)
        self.stack.addWidget(self.pages["Projects"])
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.provider_label = label("", "Muted")
        status.addPermanentWidget(self.provider_label)
        self.palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.palette_shortcutcut_mac = QShortcut(QKeySequence("Meta+K"), self)
        self.palette_shortcut.activated.connect(self.open_palette)
        self.palette_shortcutcut_mac.activated.connect(self.open_palette)
        self.new_chat_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_chat_shortcut.activated.connect(lambda: (self.navigate("Chat"), self.pages["Chat"].new_conversation()))
        self.back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.back_shortcut.activated.connect(lambda: self.go_history(-1))
        self.forward_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        self.forward_shortcut.activated.connect(lambda: self.go_history(1))
        self.action_shortcut = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
        self.action_shortcut.activated.connect(self.open_capabilities)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(30000)
        self.system_timer = QTimer(self)
        self.system_timer.timeout.connect(self.refresh_system)
        self.system_timer.start(15000)
        self.routine_timer = QTimer(self)
        self.routine_timer.timeout.connect(self.run_routines)
        if not hasattr(self.services, "background"):
            self.routine_timer.start(30000)
        self.desktop_indicator = DesktopIndicator(self)
        if hasattr(self.services, "desktop"):
            self.services.desktop.set_indicator(self.desktop_indicator)
        self.overlay = CommandOverlay(self)
        self.global_hotkey = GlobalHotkey(self.open_overlay)
        self.workflow_hotkeys = WorkflowHotkeys(self.run_workflow_hotkey)
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(100)
        self.shutdown_timer.timeout.connect(self.close)
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.setInterval(2000)
        self.hotkey_timer.timeout.connect(self.configure_workflow_hotkeys)
        self.hotkey_timer.start()
        self.runtime_signals = RuntimeSignals(self)
        self.runtime_signals.event.connect(self.runtime_event)
        self.runtime_signals.approval.connect(self.supervised_permission)
        self.runtime_signals.dismiss_approval.connect(self.dismiss_supervised_permission)
        if hasattr(self.services, "supervisor"):
            self.services.supervisor.set_approval_handler(self.supervised_approval)
        if hasattr(self.services, "operator"):
            self.services.operator.set_callback(self.runtime_signals.event.emit)
        self.update_clock()
        self.update_status()
        geometry = self.services.settings.get("ui.geometry", "")
        if isinstance(geometry, str) and len(geometry) < 4096:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
        last_page = self.services.settings.get("ui.last_page", "Home")
        self.navigate(last_page if last_page in self.nav_buttons else "Home")
        self.setup_tray()
        self.configure_overlay()
        self.configure_workflow_hotkeys()
        if hasattr(self.services, "background"):
            self.services.background.set_callback(self.runtime_signals.event.emit)
            self.services.background.start()
        QTimer.singleShot(100, self.refresh_system)
        if not hasattr(self.services, "background"):
            QTimer.singleShot(700, self.run_routines)

    def navigation_button(self, name):
        nav = button(name, lambda target=name: self.navigate(target), "Navigation")
        nav.setIcon(icon(name.lower()))
        nav.setIconSize(QSize(18, 18))
        nav.setCheckable(True)
        nav.setMinimumHeight(28)
        self.nav_buttons[name] = nav
        return nav

    def toggle_sidebar(self, collapsed=None, *, persist=True):
        # Qt's clicked(bool) is not a requested collapse state.
        if collapsed is None or self.sender() is self.sidebar_toggle:
            collapsed = not self.sidebar_collapsed
        self.sidebar_collapsed = bool(collapsed)
        self.sidebar.setFixedWidth(52 if collapsed else 174)
        self.brand_label.setVisible(not collapsed)
        for heading in self.sidebar_labels:
            heading.setVisible(not collapsed)
        for name, nav in self.nav_buttons.items():
            nav.setText("" if collapsed else name)
        description = "Expand navigation" if collapsed else "Collapse navigation"
        self.sidebar_toggle.setAccessibleName(description)
        self.sidebar_toggle.setToolTip(description)
        if persist:
            self.services.settings.set("ui.sidebar_collapsed", self.sidebar_collapsed)

    def update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%a, %b %d   %I:%M %p"))

    def supervised_approval(self, request, cancel):
        if self.closing or cancel.is_set():
            return False
        bridge = ApprovalBridge(request)
        self.runtime_signals.approval.emit(bridge)
        try:
            from jarvix.runtime import check_cancelled
            while not bridge.ready.wait(.1):
                check_cancelled()
                if self.closing or cancel.is_set():
                    return False
            check_cancelled()
            return bridge.answer and not self.closing and not cancel.is_set()
        finally:
            if not bridge.ready.is_set():
                self.runtime_signals.dismiss_approval.emit(bridge)

    def supervised_permission(self, bridge):
        dialog = None
        try:
            if self.closing:
                return
            dialog = PermissionDialog(bridge.request, self)
            dialog.supervised_bridge = bridge
            self.supervised_dialogs.append(dialog)
            bridge.answer = dialog.exec() == QDialog.DialogCode.Accepted and not self.closing
        finally:
            if dialog:
                self.supervised_dialogs.remove(dialog)
                dialog.deleteLater()
            bridge.ready.set()

    def dismiss_supervised_permission(self, bridge):
        for dialog in self.supervised_dialogs:
            if dialog.supervised_bridge is bridge:
                dialog.reject()

    def update_status(self):
        if not hasattr(self, "provider_label"):
            return
        provider = self.services.settings.get("provider", "openai")
        configured = self.services.provider_status().get(provider, False)
        names = {"openai": "OpenAI · cloud", "gemini": "Gemini · cloud", "ollama": "Ollama · local",
                 "local": "Local endpoint", "auto": "Automatic routing"}
        self.provider_label.setText(f"{names.get(provider, provider)} · {'Configured' if configured else 'Setup required'}")

    def open_adaptive(self, tab="Search"):
        if self.closing:
            return
        destination = tab if tab in ("Missions", "Skills") else "Knowledge"
        self.navigate(destination)
        if self.current_page != destination:
            return
        dialog = self.adaptive_dialog
        index = next((i for i in range(dialog.tabs.count()) if dialog.tabs.tabText(i) == tab), 0)
        dialog.tabs.setCurrentIndex(index)
        self.breadcrumb.setText(destination if tab == destination else f"{destination} / {tab}")

    def ensure_page(self, name):
        if name == "Operator" and self.operator_dialog is None:
            self.operator_dialog = self.operator_type(self)
            self.operator_dialog.setWindowFlags(Qt.WindowType.Widget)
            self.operator_dialog.installEventFilter(self)
            self.stack.addWidget(self.operator_dialog)
            self.pages[name] = self.operator_dialog
        elif name in ("Missions", "Knowledge", "Skills") and self.adaptive_dialog is None:
            from .adaptive import AdaptiveDialog
            self.adaptive_dialog = (self.adaptive_type or AdaptiveDialog)(self, name)
            self.adaptive_dialog.setWindowFlags(Qt.WindowType.Widget)
            self.adaptive_dialog.installEventFilter(self)
            self.stack.addWidget(self.adaptive_dialog)
            for destination in ("Missions", "Knowledge", "Skills"):
                self.pages[destination] = self.adaptive_dialog

    def eventFilter(self, watched, event):
        if (watched in (self.operator_dialog, self.adaptive_dialog)
                and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape):
            # Embedded workspaces are pages; Escape still denies real dialogs.
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def navigate(self, name, record_history=True):
        if self.closing:
            return
        if name not in self.nav_buttons:
            raise ValueError(f"Unknown Jarvix section: {name}")
        if self.current_page == "Notes" and name != "Notes":
            if not self.guard(self.pages["Notes"].preserve):
                return
        self.ensure_page(name)
        if name in ("Missions", "Knowledge", "Skills"):
            tabs = self.adaptive_dialog.tabs
            index = next(i for i in range(tabs.count()) if tabs.tabText(i) == name)
            tabs.setCurrentIndex(index)
        self.current_page = name
        self.services.settings.set("ui.last_page", name)
        if record_history and (self.navigation_position < 0 or self.navigation_history[self.navigation_position] != name):
            self.navigation_history = self.navigation_history[:self.navigation_position + 1] + [name]
            self.navigation_history = self.navigation_history[-100:]
            self.navigation_position = len(self.navigation_history) - 1
        self.back_button.setEnabled(self.navigation_position > 0)
        self.forward_button.setEnabled(self.navigation_position < len(self.navigation_history) - 1)
        self.stack.setCurrentWidget(self.pages[name])
        self.pages[name].show()
        for key, nav in self.nav_buttons.items():
            nav.setChecked(key == name)
        self.breadcrumb.setText(name)
        self.guard(self.pages[name].refresh)

    def go_history(self, offset):
        position = self.navigation_position + offset
        if 0 <= position < len(self.navigation_history):
            target = self.navigation_history[position]
            self.navigate(target, record_history=False)
            if self.current_page == target:
                self.navigation_position = position
                self.back_button.setEnabled(position > 0)
                self.forward_button.setEnabled(position < len(self.navigation_history) - 1)

    def open_chat(self, text, send=True):
        self.navigate("Chat")
        chat = self.pages["Chat"]
        if chat.busy:
            self.notify("A response is already running. Your draft is ready to send afterward.")
            chat.set_draft(text)
            return
        chat.set_draft(text)
        if send:
            chat.send()

    def select_context(self, **selection):
        if hasattr(self.services, "context") and self.services.settings.get("context.enabled", False):
            self.guard(lambda: self.services.context.set_current(**selection))

    def open_conversation(self, conversation_id):
        self.navigate("Chat")
        self.pages["Chat"].load_conversation(conversation_id)

    def open_note(self, note_id):
        self.navigate("Notes")
        self.pages["Notes"].load_note(note_id)

    def open_folder(self, path):
        self.open_capabilities("files.open_folder", {"path": str(path)})

    def open_task(self, record_id):
        self.navigate("Tasks")
        table = self.pages["Tasks"].entries
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            data = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(data, dict) and data.get("id") == record_id:
                table.selectRow(row)
                table.scrollToItem(item)
                break

    def open_capabilities(self, tool_name=None, arguments=None):
        if self.closing:
            return
        if self.capability_dialog is None:
            self.capability_dialog = CapabilityDialog(self, tool_name, arguments)
        elif tool_name:
            self.capability_dialog.select(tool_name, arguments)
        self.capability_dialog.show()
        self.capability_dialog.raise_()
        self.capability_dialog.activateWindow()

    def open_notifications(self):
        self.open_capabilities("notifications.list")
        if self.capability_dialog and self.capability_dialog.selected_tool == "notifications.list":
            self.capability_dialog.run_action()

    def open_operator(self, session_id=None):
        if self.closing:
            return
        self.navigate("Operator")
        if self.current_page != "Operator":
            return
        if session_id:
            self.operator_dialog.open_session(session_id)

    def open_workflow_builder(self, definition=None):
        from .workflows import WorkflowBuilder
        dialog = WorkflowBuilder(self, definition)
        self.workflow_dialogs.append(dialog)
        dialog.finished.connect(lambda _result: self.workflow_dialogs.remove(dialog)
                                if dialog in self.workflow_dialogs else None)
        dialog.show()

    def open_overlay(self):
        if not self.closing:
            self.overlay.toggle()

    def open_workspaces(self):
        from .workspaces import WorkspaceDialog
        if self.workspace_dialog is None:
            self.workspace_dialog = WorkspaceDialog(self)
        self.workspace_dialog.refresh()
        self.workspace_dialog.show()
        self.workspace_dialog.raise_()
        self.workspace_dialog.activateWindow()

    def configure_overlay(self):
        message = self.global_hotkey.configure(self.services.settings.get("overlay.enabled", False),
                                               self.services.settings.get("overlay.hotkey", "Ctrl+Alt+Space"))
        if self.services.settings.get("overlay.enabled", False):
            self.notify(message)

    def configure_workflow_hotkeys(self):
        if self.closing or not hasattr(self.services, "workflows"):
            return
        errors = self.workflow_hotkeys.configure(self.services.workflows.list(),
                                                 self.services.settings.get("automations.enabled", True))
        if errors:
            self.notify("Workflow hotkeys · " + " · ".join(errors))

    def run_workflow_hotkey(self, shortcut):
        if self.closing or shortcut in self.hotkey_jobs:
            return
        worker = ServiceJob(lambda cancel, on_event, **_: self.services.workflows.trigger_hotkey(
            shortcut, cancel=cancel, on_event=on_event), self)
        self.hotkey_jobs[shortcut] = worker
        self.jobs.add(worker)
        worker.activity.connect(self.runtime_event)
        worker.failed.connect(self.notify)
        def completed(results):
            failed = sum(not row.get("ok", False) for row in results)
            self.notify(f"Workflow shortcut · {failed} run(s) need attention" if failed else
                        f"Workflow shortcut · {len(results)} run(s) completed")
        worker.succeeded.connect(completed)

        def finished():
            self.hotkey_jobs.pop(shortcut, None)
            self.release_job(worker)
        worker.finished.connect(finished)
        worker.start()

    def runtime_event(self, kind, data):
        if self.closing:
            return
        if kind == "operator_session":
            if self.operator_dialog:
                self.operator_dialog.refresh()
        elif kind == "notification":
            if self.tray:
                self.tray.showMessage(data.get("title", "Jarvix"), data.get("body", ""),
                                      QSystemTrayIcon.MessageIcon.Information, 8000)
            else:
                self.notify(data.get("title", "Jarvix notification"))
        elif kind == "error" or (kind == "background" and data.get("status") == "error"):
            self.notify(data.get("error", "Background task needs attention."))
        if self.current_page in {"Home", "Automations", "Activity", "Tasks"}:
            self.pages[self.current_page].refresh()

    def setup_tray(self):
        if QApplication.platformName() == "offscreen" or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Jarvix")
        menu = QMenu(self)
        menu.addAction("Open Jarvix", self.restore_window)
        menu.addAction("Local actions", lambda: (self.restore_window(), self.open_capabilities()))
        menu.addAction("Operator sessions", lambda: (self.restore_window(), self.open_operator()))
        menu.addAction("Knowledge & local models", lambda: (self.restore_window(), self.open_adaptive()))
        menu.addAction("Quick command", self.open_overlay)
        menu.addAction("Tasks", lambda: (self.restore_window(), self.navigate("Tasks")))
        menu.addAction("Notifications", lambda: (self.restore_window(), self.open_notifications()))
        menu.addAction("Stop speaking", self.services.stop_speaking)
        menu.addSeparator()
        menu.addAction("Quit Jarvix", self.quit_application)
        self.tray.setContextMenu(menu)
        self.tray.messageClicked.connect(lambda: (self.restore_window(), self.open_notifications()))
        self.tray.activated.connect(lambda reason: self.restore_window()
                                   if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        self._exit_requested = True
        self.close()

    def open_palette(self):
        dialog = CommandPalette(self)
        dialog.search.setFocus()
        dialog.exec()

    def notify(self, message):
        self.statusBar().showMessage(str(message), 10000)

    def guard(self, action):
        try:
            action()
            return True
        except Exception as exc:
            self.notify(str(exc))
            return False

    def confirm_delete(self, title, explanation):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(title)
        dialog.setInformativeText(explanation)
        dialog.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def run_job(self, work, done=None, failed=None):
        if self.closing:
            return None
        job = Job(work, self)
        self.jobs.add(job)
        if done:
            job.succeeded.connect(lambda result: done(result) if not self.closing else None)
        job.failed.connect(lambda message: (failed or self.notify)(message) if not self.closing else None)
        job.finished.connect(lambda: self.release_job(job))
        job.start()
        return job

    def release_job(self, job):
        self.jobs.discard(job)
        job.deleteLater()
        self.job_finished()

    def job_finished(self):
        if self.closing and not self.jobs and not self.pages["Chat"].busy:
            QTimer.singleShot(0, self.close)

    def refresh_system(self):
        if self.system_busy or self.closing:
            return
        self.system_busy = True
        def done(snapshot):
            self.system_busy = False
            self.snapshot = snapshot
            self.pages["System"].refresh()
            if self.current_page == "Home":
                self.pages["Home"].refresh()
        def failed(error):
            self.system_busy = False
            self.notify(error)
        self.run_job(self.services.system_snapshot, done, failed)

    def run_routines(self):
        if self.routines_busy or self.closing:
            return
        self.routines_busy = True
        def run():
            automations = self.services.run_due_automations()
            reminders = self.services.due_reminders()
            for reminder in reminders:
                self.services.notifications.create("Task reminder", reminder["title"], "task", "high")
            return {"automations": automations, "reminders": reminders,
                    "notifications": self.services.notifications.delivery()}
        def done(result):
            self.routines_busy = False
            reminders = result["reminders"]
            if self.tray:
                count = sum(task["status"] != "done" for task in self.services.list_tasks())
                self.tray.setToolTip(f"Jarvix · {count} open task(s)")
            for notification in result["notifications"]:
                if self.tray:
                    self.tray.showMessage(notification["title"], notification["body"],
                                          QSystemTrayIcon.MessageIcon.Information, 8000)
                else:
                    self.notify(notification["title"])
            quiet = self.services.settings.get("notifications.quiet", False) or self.services.settings.get("notifications.dnd", False)
            if reminders and not quiet and not self.tray:
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Jarvix · Reminder")
                dialog.setText("Your reminders are due")
                dialog.setInformativeText("\n".join(item["title"] for item in reminders))
                dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
                dialog.finished.connect(lambda _: self.reminder_dialogs.remove(dialog) if dialog in self.reminder_dialogs else None)
                self.reminder_dialogs.append(dialog)
                dialog.open()
                self.notify(f"{len(reminders)} reminder(s) due")
            if self.current_page in ("Automations", "Activity", "Tasks"):
                self.pages[self.current_page].refresh()
        def failed(error):
            self.routines_busy = False
            self.notify(error)
        self.run_job(run, done, failed)

    def closeEvent(self, event):
        if not self.guard(self.pages["Notes"].preserve):
            event.ignore()
            return
        self.services.settings.set("ui.geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        if self.tray and not self._exit_requested and not self.closing and self.services.settings.get("tray.enabled", False):
            self.hide()
            event.ignore()
            return
        self.closing = True
        for dialog in list(self.supervised_dialogs):
            dialog.reject()
        self.global_hotkey.close()
        self.workflow_hotkeys.close()
        self.hotkey_timer.stop()
        for worker in self.hotkey_jobs.values():
            worker.cancel.set()
        self.overlay.hide()
        self.desktop_indicator.hud.hide()
        if hasattr(self.services, "desktop"):
            self.services.desktop.cancel()
        if hasattr(self.services, "background"):
            self.services.background.stop(timeout=0)
        if self.operator_dialog:
            self.operator_dialog.shutdown()
        if self.adaptive_dialog:
            self.adaptive_dialog._closed = True
        for dialog in list(self.workflow_dialogs):
            dialog.cancel()
            dialog.hide()
        if self.capability_dialog:
            self.capability_dialog.cancel()
            self.capability_dialog.hide()
        for timer in (self.clock_timer, self.system_timer, self.routine_timer, self.pages["Voice"].status_timer):
            timer.stop()
        self.pages["Chat"].cancel()
        self.pages["Voice"].input_panel.shutdown()
        self.pages["Integrations"].cancel_connections()
        self.guard(self.services.stop_speaking)
        background_running = hasattr(self.services, "background") and self.services.background.running
        if self.jobs or self.pages["Chat"].busy or background_running:
            self.notify("Finishing active local work before closing…")
            if not self.shutdown_timer.isActive():
                self.shutdown_timer.start()
            event.ignore()
            return
        self.shutdown_timer.stop()
        if self.tray:
            self.tray.hide()
        event.accept()
