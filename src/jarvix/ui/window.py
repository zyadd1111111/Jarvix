"""Jarvix desktop shell, command palette, and background-job lifetime management."""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QByteArray
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QStackedWidget,
    QDialog, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QApplication,
    QSystemTrayIcon, QMenu,
)

from .theme import STYLESHEET, configure_fonts
from .widgets import label, button, Job, SignalOrb, TextPreview
from .pages import (
    HomePage, NotesPage, MemoryPage, TasksPage, FilesPage, AppsPage, SystemPage,
    ActivityPage, IntegrationsPage, VoicePage, AutomationsPage, SettingsPage,
)
from .chat import ChatPage
from .capabilities import CapabilityDialog


NAVIGATION = [
    ("Home", "⌂"), ("Chat", "◈"), ("Voice", "≋"), ("Tasks", "✓"),
    ("Memory", "◎"), ("Notes", "≡"), ("Files", "▱"), ("Apps", "⊞"),
    ("Automations", "↻"), ("Integrations", "◇"), ("System", "⌁"),
    ("Activity", "⋮"), ("Settings", "⚙"),
]


class CommandPalette(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Jarvix · Command palette")
        self.resize(700, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(label("COMMAND CENTER", "Eyebrow"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands, notes, tasks, files, applications, and tools…")
        self.search.setMinimumHeight(43)
        self.search.textChanged.connect(self.search_entries)
        self.search.returnPressed.connect(self.execute)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(self.execute)
        layout.addWidget(self.results)
        layout.addWidget(label("↑ ↓ to explore    Enter to open    Esc to dismiss    ·    Search stays local", "Muted"))
        self.entries = self.build_entries()
        self.search_entries("")

    def build_entries(self):
        entries = []
        window = self.window
        services = window.services
        for name, _ in NAVIGATION:
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
            ("Notifications inbox", "Command", window.open_notifications),
            ("Update file index", "Command", lambda: (window.navigate("Files"), window.pages["Files"].scan())),
        ])
        for note in services.list_notes():
            entries.append((note["title"], "Note", lambda record=note: window.open_note(record["id"])))
        for app in services.list_apps():
            entries.append((app["name"], "Application", lambda record=app: window.open_capabilities("apps.open", {"id": record["id"]})))
        for project in services.list_projects():
            entries.append((project["name"], "Project", lambda record=project: window.open_folder(record["path"])))
        for file in services.list_files():
            entries.append((file.get("name", file["path"]), "File", lambda record=file: TextPreview(record.get("name", "File"), "Indexed metadata; file contents remain unread.", json.dumps(record, indent=2, default=str), window).exec()))
        for task in services.list_tasks():
            entries.append((task["title"], "Task", lambda record=task: window.open_task(record["id"])))
        for routine in services.records.list("routine"):
            entries.append((routine["name"], "Automation", lambda record=routine: window.open_capabilities("automations.preview", {"id": record["id"]})))
        for workspace in services.records.list("workspace"):
            entries.append((workspace["name"], "Workspace", lambda record=workspace: window.open_capabilities("workspaces.launch", {"id": record["id"]})))
        tool_names = {spec.name for spec in services.registry.specs()}
        for name in services.settings.get("commands.favorites", []):
            if name in tool_names:
                entries.append((name, "Favorite command", lambda tool=name: window.open_capabilities(tool)))
        for name in services.settings.get("commands.recent", []):
            if name in tool_names:
                entries.append((name, "Recent command", lambda tool=name: window.open_capabilities(tool)))
        for spec in services.registry.specs():
            entries.append((spec.name + " · " + spec.description, "Tool", lambda tool=spec: window.open_capabilities(tool.name)))
        for activity in services.activity(10):
            entries.append((activity.get("summary", "Recent action"), "Recent action", lambda: window.navigate("Activity")))
        return entries

    def search_entries(self, text):
        self.results.clear()
        query = text.casefold().split()
        scored = []
        for title, category, action in self.entries:
            candidate = (title + " " + category).casefold()
            score = 2 if all(word in candidate for word in query) else 0
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
        self.tray = None
        self._exit_requested = False
        self.navigation_history = []
        self.navigation_position = -1
        self.current_page = "Home"
        self.setWindowTitle("Jarvix · Personal AI workspace")
        self.setWindowIcon(QIcon(str(Path(__file__).parent.parent / "assets" / "jarvix.svg")))
        self.setMinimumSize(1080, 720)
        self.resize(1440, 940)
        self.setStyleSheet(STYLESHEET)
        root = QWidget()
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(193)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 23, 14, 16)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(5)
        brand.addWidget(SignalOrb(36))
        brand.addWidget(label("JARVIX", "Brand"))
        brand.addStretch()
        side.addLayout(brand)
        side.addWidget(label("     AI WORKSPACE", "Eyebrow"))
        side.addSpacing(21)
        self.nav_buttons = {}
        for name, glyph in NAVIGATION:
            if name in ("Tasks", "Automations", "Settings"):
                side.addSpacing(9)
            nav = button(glyph + "    " + name, lambda target=name: self.navigate(target), "Navigation")
            nav.setCheckable(True)
            nav.setMinimumHeight(34)
            nav.setAccessibleName(name)
            side.addWidget(nav)
            self.nav_buttons[name] = nav
        side.addStretch()
        divider = QFrame()
        divider.setObjectName("Divider")
        side.addWidget(divider)
        side.addSpacing(10)
        side.addWidget(label("●  LOCAL-FIRST", "Success"))
        side.addWidget(label("Your data. Your decisions.", "Muted"))
        side.addWidget(label("OPERATOR  /  0.2", "Eyebrow"))
        main.addWidget(sidebar)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(59)
        top = QHBoxLayout(topbar)
        top.setContentsMargins(30, 0, 26, 0)
        self.back_button = button("‹", lambda: self.go_history(-1), "Quiet")
        self.back_button.setToolTip("Back · Alt+Left")
        self.forward_button = button("›", lambda: self.go_history(1), "Quiet")
        self.forward_button.setToolTip("Forward · Alt+Right")
        top.addWidget(self.back_button)
        top.addWidget(self.forward_button)
        self.breadcrumb = label("WORKSPACE  /  HOME", "Eyebrow")
        top.addWidget(self.breadcrumb)
        top.addStretch()
        self.clock_label = label("", "Muted")
        top.addWidget(self.clock_label)
        top.addSpacing(19)
        top.addWidget(button("Actions", self.open_capabilities))
        top.addWidget(button("Inbox", self.open_notifications, "Quiet"))
        top.addWidget(button("Search anything    Ctrl K", self.open_palette))
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
        self.pages = {}
        for name, _ in NAVIGATION:
            page = constructors[name](self)
            self.pages[name] = page
            self.stack.addWidget(page)
        status = self.statusBar()
        status.setStyleSheet("QStatusBar { background:#0b0f15; border-top:1px solid #222a37; color:#8291aa; padding:3px 12px; font-size:11px; }")
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
        self.routine_timer.start(30000)
        self.update_clock()
        self.update_status()
        geometry = self.services.settings.get("ui.geometry", "")
        if isinstance(geometry, str) and len(geometry) < 4096:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
        last_page = self.services.settings.get("ui.last_page", "Home")
        self.navigate(last_page if last_page in self.pages else "Home")
        self.setup_tray()
        QTimer.singleShot(100, self.refresh_system)
        QTimer.singleShot(700, self.run_routines)

    def update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%a, %b %d   %I:%M %p"))

    def update_status(self):
        if not hasattr(self, "provider_label"):
            return
        provider = self.services.settings.get("provider", "openai")
        configured = self.services.provider_status().get(provider, False)
        self.provider_label.setText(f"{'OpenAI' if provider == 'openai' else 'Gemini'}  ·  {'Key configured' if configured else 'Setup required'}")

    def navigate(self, name, record_history=True):
        if self.closing:
            return
        if name not in self.pages:
            raise ValueError(f"Unknown Jarvix section: {name}")
        if self.current_page == "Notes" and name != "Notes":
            if not self.guard(self.pages["Notes"].preserve):
                return
        self.current_page = name
        self.services.settings.set("ui.last_page", name)
        if record_history and (self.navigation_position < 0 or self.navigation_history[self.navigation_position] != name):
            self.navigation_history = self.navigation_history[:self.navigation_position + 1] + [name]
            self.navigation_history = self.navigation_history[-100:]
            self.navigation_position = len(self.navigation_history) - 1
        self.back_button.setEnabled(self.navigation_position > 0)
        self.forward_button.setEnabled(self.navigation_position < len(self.navigation_history) - 1)
        self.stack.setCurrentWidget(self.pages[name])
        for key, nav in self.nav_buttons.items():
            nav.setChecked(key == name)
        self.breadcrumb.setText("WORKSPACE  /  " + name.upper())
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

    def setup_tray(self):
        if QApplication.platformName() == "offscreen" or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Jarvix")
        menu = QMenu(self)
        menu.addAction("Open Jarvix", self.restore_window)
        menu.addAction("Local actions", lambda: (self.restore_window(), self.open_capabilities()))
        menu.addAction("Tasks", lambda: (self.restore_window(), self.navigate("Tasks")))
        menu.addAction("Notifications", lambda: (self.restore_window(), self.open_notifications()))
        menu.addAction("Stop speaking", self.services.stop_speaking)
        menu.addSeparator()
        menu.addAction("Quit Jarvix", self.quit_application)
        self.tray.setContextMenu(menu)
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
        if self.capability_dialog:
            self.capability_dialog.cancel()
            self.capability_dialog.hide()
        for timer in (self.clock_timer, self.system_timer, self.routine_timer, self.pages["Voice"].status_timer):
            timer.stop()
        self.pages["Chat"].cancel()
        self.pages["Voice"].input_panel.shutdown()
        self.guard(self.services.stop_speaking)
        if self.jobs or self.pages["Chat"].busy:
            self.notify("Finishing active local work before closing…")
            event.ignore()
            return
        if self.tray:
            self.tray.hide()
        event.accept()
