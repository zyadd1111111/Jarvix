"""A blank Nexus window around the existing application controller.

Only presentation hooks cross this boundary. Services, workers, approvals and
shutdown still belong to the original controller.
"""
from datetime import datetime
from weakref import ref

from PySide6.QtCore import Qt, QByteArray, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QShortcut, QIcon, QPainter, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QListWidget, QListWidgetItem, QDialog, QMenu, QPushButton, QApplication,
    QLineEdit, QPlainTextEdit,
    QSizePolicy,
)

from ..window import MainWindow
from ..widgets import label
from ..nexus.composition import apply_backdrop
from ..liquid import motion
from ..liquid.components import LiquidSheet, LiquidSurface, LiquidPopover
from ..liquid.theme import ink
from .design import (TOKENS, stylesheet, NexusCanvas, NexusRail, NexusInput,
                     NexusButton, NexusIconButton, NexusMenu, icon)
from .home import NexusHomePage


PRIMARY = ("Home", "Operator", "Projects", "Missions", "Knowledge", "Files", "Automations")
SECONDARY = ("Tasks", "Notes", "Apps", "Skills", "Integrations", "System", "Activity", "Voice", "Memory")


def preferences(settings):
    values = {name: settings.get("liquid." + name, default) for name, default in (
        ("theme", "cream"), ("transparency", True), ("glass", "frosted"),
        ("motion", True), ("density", "comfortable"), ("accent", "theme"))}
    values["motion"] = values["motion"] and not settings.get("liquid.reduced_motion", False)
    return values


class NexusController(MainWindow):
    """Reuse application actions without using their window as the frontend."""
    _frontend_ref = None
    interface_mode = "new"

    @property
    def frontend(self):
        return self._frontend_ref() if self._frontend_ref else None

    @frontend.setter
    def frontend(self, window):
        self._frontend_ref = ref(window)

    def navigate(self, name, record_history=True):
        if self.frontend is not None:
            return self.frontend.navigate(name, record_history)
        return super().navigate(name, record_history)

    def notify(self, message):
        if self.frontend is not None:
            return self.frontend.notify(message)
        return super().notify(message)

    def restore_window(self):
        if self.frontend is not None:
            return self.frontend.restore_window()
        return super().restore_window()

    def close(self):
        if self.frontend is not None:
            return self.frontend.close()
        return super().close()

    def hide(self):
        if self.frontend is not None:
            return self.frontend.hide()
        return super().hide()

    def show(self):
        if self.frontend is not None:
            return self.frontend.show()
        return super().show()

    def activateWindow(self):
        if self.frontend is not None:
            return self.frontend.activateWindow()
        return super().activateWindow()

    def configure_materials(self):
        if self.frontend is not None:
            return self.frontend.configure_materials()

    def ensure_page(self, name):
        if name not in ("Missions", "Knowledge", "Skills"):
            return super().ensure_page(name)
        if name not in self.pages:
            page = self.adaptive_type(self, name)
            page.setWindowFlags(Qt.WindowType.Widget)
            page.installEventFilter(self)
            self.stack.addWidget(page)
            self.pages[name] = page
        self.adaptive_dialog = self.pages[name]

    def open_adaptive(self, tab="Search"):
        if tab in ("Missions", "Knowledge", "Skills"):
            return super().open_adaptive(tab)
        # Existing auxiliary controllers remain available without reinstating their page layout.
        from ..adaptive import AdaptiveDialog
        dialog = getattr(self, "_auxiliary_dialog", None)
        if dialog is None:
            dialog = self._auxiliary_dialog = AdaptiveDialog(self, tab)
        index = next((index for index in range(dialog.tabs.count()) if dialog.tabs.tabText(index) == tab), 0)
        dialog.tabs.setCurrentIndex(index)
        dialog.refresh()
        dialog.show()
        dialog.raise_()

    def isVisible(self):
        if self.frontend is not None:
            return self.frontend.isVisible()
        return super().isVisible()


class NexusWindow(QMainWindow):
    interface_mode = "new"

    def __init__(self, services):
        super().__init__()
        from .chat import NexusChatPage
        from .pages import (NexusProjectsPage, NexusFilesPage, NexusAutomationsPage,
                            NexusSettingsPage, NexusSystemPage, NexusTasksPage,
                            NexusNotesPage, NexusAppsPage)
        from .workspaces import NexusOperatorWorkspace, NexusAdaptiveWorkspace
        from .utilities import NexusVoicePage, NexusMemoryPage, NexusActivityPage, NexusIntegrationsPage
        self.services = services
        self._liquid_preferences = preferences(services.settings)
        # One controller/runtime. Its former chrome is never part of this shell.
        class Controller(NexusController):
            page_constructors = {"Home": NexusHomePage, "Chat": NexusChatPage,
                "Projects": NexusProjectsPage, "Files": NexusFilesPage,
                "Automations": NexusAutomationsPage, "Settings": NexusSettingsPage,
                "System": NexusSystemPage, "Tasks": NexusTasksPage,
                "Notes": NexusNotesPage, "Apps": NexusAppsPage,
                "Voice": NexusVoicePage, "Memory": NexusMemoryPage,
                "Activity": NexusActivityPage, "Integrations": NexusIntegrationsPage}
            operator_type = NexusOperatorWorkspace
            adaptive_type = NexusAdaptiveWorkspace
        self.controller = Controller(services)
        self.controller.setParent(self, Qt.WindowType.Widget)
        self.controller.hide()
        self.controller.setStyleSheet("")
        self.setWindowTitle("Jarvix · Nexus")
        self.setWindowIcon(self.controller.windowIcon())
        self.setMinimumSize(820, 540)
        self.resize(1366, 768)
        canvas = NexusCanvas(self)
        self.setCentralWidget(canvas)
        shell = QHBoxLayout(canvas)
        shell.setContentsMargins(10, 10, 10, 10)
        shell.setSpacing(0)
        self.sidebar = NexusRail(canvas)
        self.sidebar.body.setContentsMargins(10, 12, 10, 12)
        self.sidebar.body.setSpacing(6)
        shell.addWidget(self.sidebar)
        self.stack = QStackedWidget(canvas)
        self.stack.setObjectName("NexusWorkspace")
        shell.addWidget(self.stack, 1)
        for page in set(self.controller.pages.values()):
            self.stack.addWidget(page)
        self.controller.stack = self.stack
        self.controller.frontend = self
        self.controller.saveGeometry = self.saveGeometry
        self.nav_buttons = {}
        self.build_rail()
        self.controller.nav_buttons = self.nav_buttons
        self.pages["Chat"].conversation_changed.connect(self.refresh_history)
        self._toast = None
        self._shortcuts = []
        for key, callback in (("Ctrl+N", self.new_chat), ("Ctrl+K", self.controller.open_palette),
                ("Meta+K", self.controller.open_palette), ("Ctrl+,", lambda: self.navigate("Settings")),
                ("Alt+Left", lambda: self.controller.go_history(-1)),
                ("Alt+Right", lambda: self.controller.go_history(1)),
                ("Ctrl+Shift+K", self.controller.open_capabilities)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self.controller.shutdown_timer.timeout.disconnect()
        self.controller.shutdown_timer.timeout.connect(self.close)
        geometry = services.settings.get("ui.geometry", "")
        if isinstance(geometry, str) and len(geometry) < 4096:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
        self.sidebar_collapsed = services.settings.get("nexus.sidebar_collapsed", False)
        self.toggle_sidebar(self.sidebar_collapsed, persist=False)
        self.configure_materials()
        QApplication.instance().installEventFilter(self)
        self.navigate(self.controller.current_page, record_history=False)

    def __getattr__(self, name):
        controller = self.__dict__.get("controller")
        if controller is not None:
            return getattr(controller, name)
        raise AttributeError(name)

    @property
    def snapshot(self):
        return self.controller.snapshot

    @snapshot.setter
    def snapshot(self, value):
        self.controller.snapshot = value

    def statusBar(self):
        controller = self.__dict__.get("controller")
        return controller.statusBar() if controller is not None else super().statusBar()

    def build_rail(self):
        brand = QHBoxLayout()
        self.brand_label = label("Jarvix", "Brand")
        brand.addWidget(self.brand_label, 1)
        self.sidebar_toggle = NexusIconButton("menu", "Collapse sidebar", self.toggle_sidebar)
        brand.addWidget(self.sidebar_toggle)
        self.sidebar.body.addLayout(brand)
        self.new_chat_button = NexusButton("New chat", self.new_chat, "Primary")
        self.new_chat_button.setIcon(icon("plus", self._liquid_preferences))
        self.new_chat_button.setToolTip("New chat · Ctrl+N")
        self.sidebar.body.addWidget(self.new_chat_button)
        self.search = NexusInput("Search…", self.sidebar)
        self.search.setAccessibleName("Search conversations")
        self.search.setClearButtonEnabled(True)
        self.sidebar.body.addWidget(self.search)
        self.history = QListWidget(self.sidebar)
        self.history.setAccessibleName("Conversation history")
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.itemActivated.connect(self.open_history)
        self.history.itemClicked.connect(self.open_history)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.history_menu)
        self.sidebar.body.addWidget(self.history, 1)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(140)
        self.search_timer.timeout.connect(self.refresh_history)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        for name in (*PRIMARY, *SECONDARY, "Chat", "Settings"):
            control = NexusButton(name, lambda target=name: self.navigate(target), "NexusNav", self.sidebar)
            control.setIcon(icon(name.lower(), self._liquid_preferences))
            control.setCheckable(True)
            control.setAccessibleName(name)
            self.nav_buttons[name] = control
            if name in PRIMARY:
                self.sidebar.body.addWidget(control)
            else:
                control.hide()
        self.more = NexusButton("More", style="NexusNav", parent=self.sidebar)
        self.more.setIcon(icon("menu", self._liquid_preferences))
        self.more_menu = NexusMenu("More Jarvix tools", self.more)
        for name in SECONDARY:
            self.more_menu.addAction(icon(name.lower(), self._liquid_preferences), name,
                lambda target=name: self.navigate(target))
        self.more.setMenu(self.more_menu)
        self.sidebar.body.addWidget(self.more)
        footer = QHBoxLayout()
        self.nav_buttons["Settings"].setVisible(True)
        footer.addWidget(self.nav_buttons["Settings"], 1)
        footer.addWidget(NexusIconButton("bell", "Notifications", self.controller.open_notifications))
        self.sidebar.body.addLayout(footer)
        self.local_status = self.controller.provider_label
        self.local_status.setWordWrap(True)
        self.local_status.setMinimumWidth(0)
        self.local_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.local_status.setObjectName("Caption")
        self.sidebar.body.addWidget(self.local_status)
        self.refresh_history()

    def refresh_history(self):
        rows = self.services.conversations.search(self.search.text().strip())
        self.history.blockSignals(True)
        self.history.clear()
        groups = {name: [] for name in ("Pinned", "Today", "Yesterday", "Previous 7 days", "Older")}
        today = datetime.now().astimezone().date()
        for row in rows:
            try:
                days = (today - datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")).astimezone().date()).days
            except (KeyError, ValueError, TypeError):
                days = 99
            group = "Pinned" if row.get("pinned") else "Today" if days <= 0 else "Yesterday" if days == 1 else "Previous 7 days" if days <= 7 else "Older"
            groups[group].append(row)
        for group, records in groups.items():
            if not records:
                continue
            heading = QListWidgetItem(group)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            self.history.addItem(heading)
            for record in records:
                item = QListWidgetItem(record["title"])
                item.setData(Qt.ItemDataRole.UserRole, record["id"])
                item.setToolTip(record["title"])
                self.history.addItem(item)
                if record["id"] == self.pages["Chat"].conversation_id:
                    self.history.setCurrentItem(item)
        self.history.blockSignals(False)

    def open_history(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if identifier:
            self.controller.open_conversation(identifier)

    def history_menu(self, position):
        item = self.history.itemAt(position)
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        chat = self.pages["Chat"]
        if chat.busy:
            return self.notify("Stop the active response before changing conversations.")
        self.open_history(item)
        if self.current_page != "Chat":
            return
        menu = NexusMenu("Conversation", self.history)
        for text, callback in (("Rename", chat.rename_conversation), ("Pin / unpin", chat.pin_conversation),
                               ("Export…", chat.export_conversation), ("Delete…", chat.delete_conversation)):
            menu.addAction(text, callback)
        menu.exec(self.history.mapToGlobal(position))
        self.refresh_history()

    def new_chat(self):
        self.navigate("Chat")
        if self.current_page == "Chat":
            self.pages["Chat"].new_conversation()

    def navigate(self, name, record_history=True):
        if self.closing:
            return
        prior = self.current_page
        snapshot = self.stack.grab() if self.isVisible() and prior != name and motion.enabled(self) else None
        MainWindow.navigate(self.controller, name, record_history)
        if self.current_page != name:
            return
        self.tint_controls()
        self.refresh_history()
        if snapshot is not None:
            motion.Transition(self.stack, snapshot)

    def toggle_sidebar(self, collapsed=None, *, persist=True):
        if collapsed is None or self.sender() is self.sidebar_toggle:
            collapsed = not self.sidebar_collapsed
        self.sidebar_collapsed = bool(collapsed)
        self.brand_label.setVisible(not collapsed)
        for control in (self.search, self.history, self.local_status):
            control.setVisible(not collapsed)
        for name, control in self.nav_buttons.items():
            control.setText("" if collapsed else name)
        self.new_chat_button.setText("" if collapsed else "New chat")
        self.more.setText("" if collapsed else "More")
        description = "Expand sidebar" if collapsed else "Collapse sidebar"
        self.sidebar_toggle.setAccessibleName(description)
        self.sidebar_toggle.setToolTip(description)
        end = TOKENS["rail_collapsed"] if collapsed else TOKENS["rail_width"]
        motion.animate(self.sidebar, "width", self.sidebar.width(), end, self.sidebar.setFixedWidth,
                       duration=TOKENS["duration_panel"])
        if persist:
            self.services.settings.set("nexus.sidebar_collapsed", collapsed)

    def configure_materials(self):
        self._liquid_preferences = preferences(self.services.settings)
        self.setStyleSheet(stylesheet(self._liquid_preferences))
        self.composition_status = apply_backdrop(self, enabled=self._liquid_preferences["transparency"])
        if not self._liquid_preferences["motion"]:
            motion.settle(self)
        self.centralWidget().update()
        for surface in self.findChildren(LiquidSurface):
            surface.update()
        self.tint_controls()
        for page in set(self.pages.values()):
            if refresh := getattr(page, "refresh_materials", None):
                refresh()

    def tint_controls(self):
        for control in self.findChildren(QPushButton):
            if control.icon().isNull() or control.property("nativeAppIcon") or control.property("nexusSemanticIcon"):
                continue
            color = ink(self._liquid_preferences, "primary_text" if control.objectName() == "Primary" else "muted")
            if control.property("nexusTinted") == color:
                continue
            if not hasattr(control, "_nexus_original_icon"):
                control._nexus_original_icon = control.icon()
            pixels = control._nexus_original_icon.pixmap(24, 24)
            painter = QPainter(pixels)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixels.rect(), QColor(color))
            painter.end()
            control.setIcon(QIcon(pixels))
            control.setProperty("nexusTinted", color)

    def notify(self, message):
        MainWindow.notify(self.controller, message)
        if not self.isVisible():
            return
        if self._toast is None:
            self._toast = LiquidPopover(parent=self.centralWidget())
            self._toast_label = label("", wrap=True)
            self._toast.body.addWidget(self._toast_label)
            self._toast.body.addWidget(NexusButton("Dismiss", self._toast.hide, "Quiet"))
            self._toast_timer = QTimer(self._toast)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._toast.hide)
        self._toast_label.setText(str(message))
        self._toast.setFixedWidth(min(340, self.centralWidget().width() // 2))
        self._toast.adjustSize()
        self.position_toast()
        self._toast.show()
        self._toast.raise_()
        motion.disclose(self._toast)
        self._toast_timer.start(7000)

    def position_toast(self):
        if self._toast is not None:
            self._toast.move(max(0, self.centralWidget().width() - self._toast.width() - 18),
                             max(0, self.centralWidget().height() - self._toast.height() - 18))

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        motion.settle(self)
        MainWindow.closeEvent(self.controller, event)
        if self.closing:
            for page in set(self.pages.values()):
                if hasattr(page, "_closed"):
                    page._closed = True
            if auxiliary := getattr(self.controller, "_auxiliary_dialog", None):
                auxiliary._closed = True
                auxiliary.hide()
        if not event.isAccepted() and not self.closing and self.controller.tray and self.services.settings.get("tray.enabled", False):
            self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if "sidebar_collapsed" in self.__dict__ and self.width() < 1080 and not self.sidebar_collapsed:
            self.toggle_sidebar(True, persist=False)
        if "_toast" in self.__dict__:
            self.position_toast()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut) and isinstance(watched, (QLineEdit, QPlainTextEdit)):
            surface = watched.parentWidget()
            while surface is not None and not isinstance(surface, LiquidSurface):
                surface = surface.parentWidget()
            if surface is not None and motion.owner(surface) is self:
                surface.focus_material(event.type() == QEvent.Type.FocusIn)
        if event.type() == QEvent.Type.Show and isinstance(watched, (QDialog, QMenu)):
            ancestor = watched.parentWidget()
            while ancestor is not None and ancestor is not self:
                ancestor = ancestor.parentWidget()
            if ancestor is self and watched.isWindow():
                if isinstance(watched, QDialog) and watched.layout() and not watched.property("nexusSheet"):
                    watched.setProperty("nexusSheet", True)
                    content = QWidget(watched)
                    content.setLayout(watched.layout())
                    sheet = LiquidSheet(watched)
                    sheet.body.addWidget(content)
                    layout = QVBoxLayout(watched)
                    layout.addWidget(sheet)
                motion.disclose(watched)
        return super().eventFilter(watched, event)
