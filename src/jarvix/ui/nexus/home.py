"""Nexus desktop composition; all working Home controllers remain shared."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QVBoxLayout, QLayout, QLineEdit, QScrollArea,
    QFrame, QWidget, QMenu, QPushButton, QSizePolicy,
)

from ..pages import HomePage
from ..widgets import button, label, clear_layout
from .icons import icon
from .materials import GlassPanel, GlassCommandSurface


class CommandInput(QLineEdit):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class NexusHomePage(HomePage):
    """Adopt inherited section widgets rather than fork local data or actions."""

    def __init__(self, window):
        super().__init__(window)
        names = ("greeting", "date_label", "operator_panel", "mission_panel", "workspace_panel",
                 "tasks_panel", "daily_panel", "connections_panel", "automation_panel",
                 "favorites_panel", "health_panel", "recent_panel", "history_panel", "activity_panel")
        keeper = QWidget(self)
        for name in names:
            # A different parent also removes direct Home children from old layouts.
            getattr(self, name).setParent(keeper)
        clear_layout(self.layout)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(12)
        heading = QHBoxLayout()
        self.greeting.setObjectName("NexusGreeting")
        heading.addWidget(self.greeting)
        heading.addStretch()
        heading.addWidget(self.date_label)
        self.layout.addLayout(heading)
        self.context_label = label("Local workspace", "Muted", True)
        self.layout.addWidget(self.context_label)
        self.command_surface = GlassCommandSurface(self)
        self.command = CommandInput(self.command_surface)
        self.command.setObjectName("CommandInput")
        self.command.setPlaceholderText("Ask Jarvix or tell it to do something…")
        self.command.setAccessibleName("Universal Jarvix command")
        self.command.setClearButtonEnabled(True)
        self.command.returnPressed.connect(self.submit)
        self.command.files_dropped.connect(self.open_dropped_files)
        self.command_surface.body.addWidget(self.command)
        inputs = QHBoxLayout()
        for caption, symbol, action in (("Files", "file", self.file_menu),
                                        ("Voice", "voice", lambda: window.navigate("Voice")),
                                        ("Screen", "system", self.screen_context),
                                        ("Context", "knowledge", lambda: window.open_adaptive("Personal context"))):
            control = button(caption, action, "Quiet")
            control.setIcon(icon(symbol))
            control.setAccessibleName({"Files": "Choose a file or attach an image", "Screen": "Choose screen context"}.get(caption, caption))
            inputs.addWidget(control)
        inputs.addStretch()
        inputs.addWidget(button("Ask Jarvix", self.submit, "Primary"))
        self.command_surface.body.addLayout(inputs)
        self.layout.addWidget(self.command_surface)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_body = QWidget(scroll)
        self.body_layout = QVBoxLayout(self.scroll_body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.desktop_grid = QGridLayout()
        self.desktop_grid.setContentsMargins(0, 0, 0, 0)
        self.desktop_grid.setSpacing(12)
        self.body_layout.addLayout(self.desktop_grid)
        self.current_pane = GlassPanel(self.scroll_body)
        self.current_pane.body.addWidget(label("CURRENT WORK", "Eyebrow"))
        for name in ("mission_panel", "workspace_panel", "operator_panel", "tasks_panel"):
            self.current_pane.body.addWidget(getattr(self, name))
        self.context_pane = GlassPanel(self.scroll_body)
        self.context_pane.body.addWidget(label("CONTEXT", "Eyebrow"))
        self.calendar_panel = QWidget(self.context_pane)
        self.calendar_layout = QVBoxLayout(self.calendar_panel)
        self.calendar_layout.setContentsMargins(0, 0, 0, 0)
        self.context_pane.body.addWidget(self.calendar_panel)
        for name in ("daily_panel", "automation_panel", "connections_panel", "favorites_panel"):
            self.context_pane.body.addWidget(getattr(self, name))
        self.recent_pane = GlassPanel(self.scroll_body)
        self.recent_pane.body.addWidget(label("RECENT WORK", "Eyebrow"))
        self.recent_grid = QGridLayout()
        self.recent_grid.setContentsMargins(0, 0, 0, 0)
        self.recent_grid.setSpacing(12)
        self.recent_pane.body.addLayout(self.recent_grid)
        self.body_layout.addWidget(self.recent_pane)
        self.body_layout.addWidget(self.health_panel)
        self.body_layout.addStretch()
        scroll.setWidget(self.scroll_body)
        self.layout.addWidget(scroll, 1)
        for name in names:
            getattr(self, name).show()
            if name.endswith("_panel"):
                getattr(self, name).setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.daily_panel.hide()
        keeper.deleteLater()
        self._compact = None
        self.arrange_panes()

    def arrange_panes(self):
        compact = self.width() < 710
        if compact == self._compact:
            return
        self._compact = compact
        self.desktop_grid.removeWidget(self.current_pane)
        self.desktop_grid.removeWidget(self.context_pane)
        self.desktop_grid.addWidget(self.current_pane, 0, 0)
        self.desktop_grid.addWidget(self.context_pane, 1 if compact else 0, 0 if compact else 1)
        self.desktop_grid.setColumnStretch(0, 2)
        self.desktop_grid.setColumnStretch(1, 0 if compact else 1)
        for index, name in enumerate(("recent_panel", "history_panel", "activity_panel")):
            panel = getattr(self, name)
            self.recent_grid.removeWidget(panel)
            self.recent_grid.addWidget(panel, index if compact else 0, 0 if compact else index)
            self.recent_grid.setColumnStretch(index, 1 if not compact or index == 0 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "desktop_grid"):
            self.arrange_panes()
            self.fit_resource_labels()

    def fit_resource_labels(self):
        for pane in (self.current_pane, self.context_pane, self.recent_pane):
            for control in pane.findChildren(QPushButton):
                text = control.property("nexusFullLabel")
                if text and control.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored:
                    control.setText(control.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(80, control.width() - 20)))

    def section(self, layout, title, link=None):
        super().section(layout, "Model" if title == "AI model" else title, link)

    def refresh(self):
        super().refresh()
        projects = self.services.list_projects()
        selected = None
        if self.services.settings.get("context.enabled", False):
            try:
                selected = self.services.context.selection().get("project_id")
            except (PermissionError, ValueError):
                pass
        project = next((item for item in projects if item["id"] == selected), None)
        self.context_label.setText("Current project · " + project["name"] if project else "Local workspace · Choose a project in Projects")
        self.section(self.calendar_layout, "Calendar")
        status = next((row for row in self.services.integrations.status() if row["id"] == "google_calendar"), {})
        connected = status.get("status") == "Connected"
        self.calendar_layout.addWidget(label(status.get("account") or status.get("status", "Not connected"), "Muted", True))
        self.calendar_layout.addWidget(button("View today's events" if connected else "Connect Calendar",
            self.open_calendar if connected else lambda: self.window.navigate("Integrations"), "Quiet"))
        for pane in (self.current_pane, self.context_pane, self.recent_pane):
            for control in pane.findChildren(QPushButton):
                control.setProperty("nexusRow", True)
                control.setProperty("nexusFullLabel", control.text())
                if control.parentWidget().layout().indexOf(control) >= 0:
                    control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                control.style().unpolish(control)
                control.style().polish(control)
        QTimer.singleShot(0, self, self.fit_resource_labels)

    def open_calendar(self):
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        self.window.open_capabilities("google_calendar.events", {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()})

    def draft_in_chat(self):
        text = self.command.text().strip()
        self.command.clear()
        if text:
            self.window.open_chat(text, send=False)
        else:
            self.window.navigate("Chat")
        return self.window.pages["Chat"]

    def file_menu(self):
        menu = QMenu(self)
        menu.addAction(icon("plus"), "Attach images to a message", lambda: self.draft_in_chat().choose_images())
        menu.addAction(icon("files"), "Browse local files", lambda: self.window.navigate("Files"))
        menu.exec(self.command_surface.mapToGlobal(self.command_surface.rect().bottomLeft()))
        menu.deleteLater()

    def screen_context(self):
        self.draft_in_chat().screen_menu()

    def open_dropped_files(self, paths):
        images = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        if paths and all(Path(path).suffix.lower() in images for path in paths):
            self.draft_in_chat().add_images(paths)
        elif paths:
            self.window.open_capabilities("files.inspect", {"path": paths[0]})

