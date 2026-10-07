"""Liquid panes for desktop utilities; actions remain in the existing controllers."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, Qt
from PySide6.QtWidgets import (
    QFileIconProvider, QHeaderView, QSplitter, QTreeWidget, QTreeWidgetItem,
    QListWidget, QListWidgetItem, QHBoxLayout, QPushButton, QMenu,
)

from ..pages import FilesPage, AppsPage, AutomationsPage, SystemPage, selected_record
from ..integrations import IntegrationsPage
from ..widgets import label, button
from .materials import GlassPanel
from .icons import icon
from .workspaces import drain_layout


def frame_widget(layout, widget, title=""):
    index = layout.indexOf(widget)
    stretch = layout.stretch(index)
    layout.removeWidget(widget)
    panel = GlassPanel(parent=widget.parentWidget())
    if title:
        panel.body.addWidget(label(title, "SectionTitle"))
    panel.body.addWidget(widget, 1)
    layout.insertWidget(index, panel, stretch)
    return panel


class NexusFilesPage(FilesPage):
    """Indexed file manager with a metadata-only preview, never extra file reads."""

    def __init__(self, window):
        super().__init__(window)
        index = self.layout.indexOf(self.entries)
        self.layout.removeWidget(self.entries)
        self.file_split = QSplitter(self)
        files = GlassPanel(parent=self.file_split)
        files.body.addWidget(self.entries, 1)
        self.file_split.addWidget(files)
        self.preview = GlassPanel(parent=self.file_split)
        self.preview_title = label("File preview", "SectionTitle")
        self.preview.body.addWidget(self.preview_title)
        self.file_metadata = QTreeWidget(self.preview)
        self.file_metadata.setHeaderLabels(["Property", "Value"])
        self.file_metadata.setRootIsDecorated(False)
        self.file_metadata.setAccessibleName("Indexed file metadata preview")
        self.file_metadata.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.file_metadata.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.preview.body.addWidget(self.file_metadata, 1)
        self.preview.body.addWidget(label("Metadata from the local index. Inspect document to read permitted contents.", "Muted", True))
        self.file_split.addWidget(self.preview)
        self.file_split.setSizes([720, 270])
        self.file_split.setStretchFactor(0, 3)
        self.layout.insertWidget(index, self.file_split, 3)
        self.entries.itemSelectionChanged.connect(self.preview_selection)
        self.layout.setSpacing(8)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.roots.hide()
        self.layout.removeWidget(self.projects)
        self.projects.hide()
        actions = next(self.layout.itemAt(index).layout() for index in range(self.layout.count())
                       if self.layout.itemAt(index).layout()
                       and self.layout.itemAt(index).layout().indexOf(self.document_action) >= 0)
        actions_index = self.layout.indexOf(actions)
        controls = [actions.itemAt(index).widget() for index in range(actions.count())
                    if isinstance(actions.itemAt(index).widget(), QPushButton)]
        self.layout.removeItem(actions)
        actions.setParent(self.layout)
        drain_layout(actions, self)
        actions.deleteLater()
        row = QHBoxLayout()
        row.addWidget(self.document_action)
        self.document_action.show()
        more = button("File actions", None, "Quiet")
        menu = QMenu(more)
        for control in controls:
            if control.text() == "Inspect document":
                row.addWidget(control)
                control.show()
            elif control.menu():
                control.menu().setTitle(control.text())
                menu.addMenu(control.menu())
            else:
                menu.addAction(control.text(), control.click)
        menu.addSeparator()
        menu.addAction("Show / hide file preview", lambda: self.preview.setVisible(self.preview.isHidden()))
        menu.addAction("View projects", lambda: self.window.navigate("Projects"))
        menu.addAction("Register project", self.add_project)
        more.setMenu(menu)
        row.addStretch()
        row.addWidget(more)
        self.layout.insertLayout(actions_index, row)
        project_row = next(self.layout.itemAt(index).layout() for index in range(self.layout.count())
                           if self.layout.itemAt(index).layout() and any(
                               self.layout.itemAt(index).layout().itemAt(child).widget()
                               and getattr(self.layout.itemAt(index).layout().itemAt(child).widget(), "text", lambda: "")() == "PROJECTS"
                               for child in range(self.layout.itemAt(index).layout().count())))
        self.layout.removeItem(project_row)
        project_row.setParent(self.layout)
        drain_layout(project_row, self)
        project_row.deleteLater()
        self.preview_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            self.preview.setVisible(self.width() >= 900)

    def refresh_files(self):
        super().refresh_files()
        if hasattr(self, "entries"):
            for index in range(self.entries.rowCount()):
                self.entries.item(index, 0).setIcon(icon("file"))
            scope = self.root_picker.currentText()
            self.breadcrumb.setText("Indexed files / " + (Path(scope).name if scope else "No approved folder"))
            self.breadcrumb.setToolTip(scope)

    def refresh(self):
        super().refresh()
        for index in range(self.projects.rowCount()):
            self.projects.item(index, 0).setIcon(icon("projects"))

    def preview_selection(self):
        self.file_metadata.clear()
        row = selected_record(self.entries)
        self.preview_title.setText(row["name"] if row else "Select a file")
        if row:
            for caption, value in (("Location", row["path"]), ("Type", Path(row["path"]).suffix or "No extension"),
                                   ("Size", f"{row.get('size', 0):,} bytes"), ("Modified", row.get("modified_at") or "Not recorded")):
                item = QTreeWidgetItem([caption, str(value)])
                item.setToolTip(1, str(value))
                self.file_metadata.addTopLevelItem(item)


class NexusAppsPage(AppsPage):
    """Registered launcher with native executable icons where available."""

    def __init__(self, window):
        super().__init__(window)
        self.icon_provider = QFileIconProvider()
        self.icon_provider.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)
        frame_widget(self.layout, self.entries)

    def refresh(self):
        super().refresh()
        if not hasattr(self, "icon_provider"):
            return
        for index in range(self.entries.rowCount()):
            item = self.entries.item(index, 0)
            row = item.data(Qt.ItemDataRole.UserRole)
            path = Path(row["path"])
            if path.is_file():
                native_icon = self.icon_provider.icon(QFileInfo(str(path)))
                if not native_icon.isNull():
                    item.setIcon(native_icon)
            else:
                item.setIcon(icon("apps"))


class NexusAutomationsPage(AutomationsPage):
    """Reviewed workflow library beside its ordered trigger/action surface."""

    def __init__(self, window):
        super().__init__(window)
        page = self.tabs.widget(0)
        layout = page.layout()
        index = layout.indexOf(self.workflow_entries)
        layout.removeWidget(self.workflow_entries)
        self.workflow_split = QSplitter(page)
        library = GlassPanel(parent=self.workflow_split)
        library.body.addWidget(self.workflow_entries, 1)
        self.workflow_split.addWidget(library)
        self.workflow_preview = GlassPanel(parent=self.workflow_split)
        self.workflow_heading = label("Workflow steps", "SectionTitle")
        self.workflow_preview.body.addWidget(self.workflow_heading)
        self.workflow_steps = QListWidget(self.workflow_preview)
        self.workflow_steps.setWordWrap(True)
        self.workflow_steps.setAccessibleName("Selected workflow trigger and ordered actions")
        self.workflow_preview.body.addWidget(self.workflow_steps, 1)
        self.workflow_split.addWidget(self.workflow_preview)
        self.workflow_split.setSizes([620, 330])
        self.workflow_split.setStretchFactor(0, 2)
        layout.insertWidget(index, self.workflow_split, 1)
        self.workflow_entries.itemSelectionChanged.connect(self.preview_workflow)
        self.preview_workflow()

    def preview_workflow(self):
        self.workflow_steps.clear()
        row = selected_record(self.workflow_entries)
        self.workflow_heading.setText(row["name"] if row else "Select a workflow")
        if not row:
            self.workflow_steps.addItem("Build or select a workflow.")
            return
        trigger = QListWidgetItem("Trigger · " + str(row.get("trigger", "manual")).replace("_", " "))
        trigger.setIcon(icon("automations"))
        self.workflow_steps.addItem(trigger)
        for index, step in enumerate(row.get("steps", []), 1):
            kind = step.get("kind", "action")
            text = (step.get("tool", "Registered action") if kind == "action" else
                    f"Wait {step.get('seconds', 0)} seconds" if kind == "delay" else
                    str(step.get("title") or kind.replace("_", " ").title()))
            item = QListWidgetItem(f"{index:02d}  {text}")
            item.setIcon(icon("activity" if kind == "delay" else "operator"))
            item.setToolTip(kind.replace("_", " ").title())
            self.workflow_steps.addItem(item)


class NexusIntegrationsPage(IntegrationsPage):
    subtitle = "Credentials and connected accounts"

    def __init__(self, window):
        super().__init__(window)
        account_layout = self.account_status.parentWidget().layout()
        frame_widget(account_layout, self.account_status)
        for index in range(self.account_status.rowCount()):
            self.account_status.item(index, 0).setIcon(icon("integrations"))


class NexusSystemPage(SystemPage):
    """Resource monitoring on one utility pane, with the real process table."""

    def __init__(self, window):
        super().__init__(window)
        frame_widget(self.layout, self.processes, "Running processes")
