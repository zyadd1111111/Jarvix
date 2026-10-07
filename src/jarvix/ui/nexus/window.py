"""Nexus shell over the existing runtime, navigation and permission controllers."""
from PySide6.QtCore import QEvent, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QGraphicsOpacityEffect, QMenu, QApplication, QDialog, QWidget, QVBoxLayout

from ..window import MainWindow
from .theme import stylesheet, TOKENS
from .materials import DesktopCanvas, GlassSidebar, GlassToolbar, GlassPanel, GlassSurface, GlassDialog
from .composition import apply_backdrop
from .home import NexusHomePage
from .chat import NexusChatPage
from .settings import NexusSettingsPage
from .workspaces import NexusOperatorDialog, NexusAdaptiveDialog
from .pages import NexusFilesPage, NexusAppsPage, NexusAutomationsPage, NexusIntegrationsPage, NexusSystemPage


class NexusWindow(MainWindow):
    interface_mode = "nexus"
    interface_stylesheet = stylesheet()
    page_constructors = {"Home": NexusHomePage, "Chat": NexusChatPage, "Settings": NexusSettingsPage,
        "Files": NexusFilesPage, "Apps": NexusAppsPage, "Automations": NexusAutomationsPage,
        "Integrations": NexusIntegrationsPage, "System": NexusSystemPage}
    operator_type = NexusOperatorDialog
    adaptive_type = NexusAdaptiveDialog

    def __init__(self, services):
        self._nexus_preferences = {"transparency": services.settings.get("nexus.transparency", True),
            "glass": services.settings.get("nexus.glass", "frosted"),
            "motion": services.settings.get("nexus.motion", True)}
        self._nexus_sidebar_frame = None
        self._transition = None
        super().__init__(services)
        self.setWindowTitle("Jarvix · Nexus")
        old_root = self.takeCentralWidget()
        canvas = DesktopCanvas(self)
        self.setCentralWidget(canvas)
        old_root.setParent(canvas)
        old_layout = old_root.layout()
        old_layout.takeAt(0)  # sidebar widget stays Qt-owned until adopted below
        workspace = old_layout.takeAt(0).widget()
        self._nexus_sidebar_frame = GlassSidebar(canvas)
        self._nexus_sidebar_frame.body.setContentsMargins(3, 4, 3, 4)
        self._nexus_sidebar_frame.body.addWidget(self.sidebar)
        self._nexus_sidebar_frame.setFixedWidth(self.sidebar.width() + 6)
        self.sidebar.setObjectName("NexusNavigation")
        layout = QHBoxLayout(canvas)
        layout.setContentsMargins(14, 14, 14, 4)
        layout.setSpacing(12)
        layout.addWidget(self._nexus_sidebar_frame)
        layout.addWidget(workspace, 1)
        workspace_layout = workspace.layout()
        workspace_layout.setSpacing(10)
        topbar = workspace_layout.takeAt(0).widget()
        toolbar = GlassToolbar(workspace)
        toolbar.body.setContentsMargins(2, 0, 2, 0)
        toolbar.body.addWidget(topbar)
        workspace_layout.insertWidget(0, toolbar)
        self.clock_label.hide()
        for control in topbar.findChildren(QPushButton):
            if control.text() == "Tools":
                control.hide()
            elif control.text().startswith("Search"):
                control.setText("Search")
                control.setToolTip("Search · Ctrl+K")
        workspace_layout.removeWidget(self.stack)
        pane = GlassPanel(workspace)
        pane.body.setContentsMargins(0, 0, 0, 0)
        pane.body.addWidget(self.stack)
        workspace_layout.addWidget(pane, 1)
        old_root.deleteLater()
        self.stack.currentChanged.connect(self.animate_navigation)
        self._recolor_icons()
        self.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.composition_status = "Qt material"
        QTimer.singleShot(0, self.configure_materials)

    def configure_materials(self):
        self._nexus_preferences.update({"transparency": self.services.settings.get("nexus.transparency", True),
            "glass": self.services.settings.get("nexus.glass", "frosted"),
            "motion": self.services.settings.get("nexus.motion", True)})
        self.composition_status = apply_backdrop(self, enabled=self._nexus_preferences["transparency"])
        if not self._nexus_preferences["motion"] and self._transition:
            self._transition.stop()
            self._transition.deleteLater()
            self._transition = None
            self.stack.setGraphicsEffect(None)
        for surface in self.findChildren(GlassSurface):
            surface.update()

    def toggle_sidebar(self, collapsed=None, persist=True):
        super().toggle_sidebar(collapsed, persist=persist)
        if self._nexus_sidebar_frame:
            self._nexus_sidebar_frame.setFixedWidth(self.sidebar.width() + 6)

    def _recolor_icons(self):
        # The exact same SVG family; no global QIcon or Legacy asset is mutated.
        for control in self.findChildren(QPushButton):
            if control.icon().isNull():
                continue
            if control.property("nexusTinted"):
                continue
            ratio = self.devicePixelRatioF()
            pixels = control.icon().pixmap(int(24 * ratio), int(24 * ratio))
            painter = QPainter(pixels)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixels.rect(), QColor("#606b61"))
            painter.end()
            pixels.setDevicePixelRatio(ratio)
            control.setIcon(QIcon(pixels))
            control.setProperty("nexusTinted", True)

    def animate_navigation(self, _index):
        if not self._nexus_preferences["motion"] or not self.isVisible():
            return
        if self._transition:
            self._transition.stop()
            self._transition.deleteLater()
            self.stack.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(TOKENS["duration"])
        animation.setStartValue(.8)
        animation.setEndValue(1.)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        def finished():
            if self._transition is animation:
                self.stack.setGraphicsEffect(None)
                self._transition = None
        animation.finished.connect(finished)
        animation.finished.connect(animation.deleteLater)
        self._transition = animation
        animation.start()

    def ensure_page(self, name):
        super().ensure_page(name)
        if hasattr(self, "_nexus_sidebar_frame"):
            self._recolor_icons()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, (QMenu, QDialog)):
            parent = watched.parentWidget()
            while parent is not None and parent is not self:
                parent = parent.parentWidget()
            if parent is self:
                if isinstance(watched, QMenu):
                    watched.setAccessibleName(watched.title() or "Actions")
                    apply_backdrop(watched, transient=True, enabled=self._nexus_preferences["transparency"])
                elif watched.isWindow() and watched.layout() and not watched.property("nexusSheet"):
                    watched.setProperty("nexusSheet", True)
                    content = QWidget(watched)
                    content.setLayout(watched.layout())
                    sheet = GlassDialog(watched)
                    sheet.body.setContentsMargins(0, 0, 0, 0)
                    sheet.body.addWidget(content)
                    outer = QVBoxLayout(watched)
                    outer.setContentsMargins(6, 6, 6, 6)
                    outer.addWidget(sheet)
        return super().eventFilter(watched, event)
