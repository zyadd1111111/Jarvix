"""Workspace management through the existing permission-checked tool forms."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QMenu

from .pages import fill_table, selected_record
from .widgets import button, label, table


class WorkspaceDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.services = window, window.services
        self.setWindowTitle("Jarvix · Workspaces")
        self.resize(900, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Workspaces", "Title"))
        row = QHBoxLayout()
        row.addWidget(button("Create workspace", lambda: window.open_capabilities("workspaces.save"), "Primary"))
        row.addWidget(button("Save current setup", lambda: window.open_capabilities("workspaces.capture")))
        row.addStretch()
        row.addWidget(button("Refresh", self.refresh, "Quiet"))
        layout.addLayout(row)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search saved workspaces…")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.entries = table(["Workspace", "Apps", "Folders", "Websites", "Layouts"])
        self.entries.setAccessibleName("Saved workspaces")
        self.entries.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.entries.customContextMenuRequested.connect(self.context_menu)
        self.entries.itemDoubleClicked.connect(lambda _: self.action("preview"))
        layout.addWidget(self.entries, 1)
        self.actions = []
        row = QHBoxLayout()
        for caption, name in (("Preview workspace", "preview"), ("Launch workspace", "launch"), ("Edit workspace", "save"),
                              ("Restore layout", "restore_layout"), ("Close workspace", "close"),
                              ("Delete workspace", "delete")):
            control = button(caption, lambda action=name: self.action(action), "Danger" if name == "delete" else "Quiet")
            self.actions.append(control)
            row.addWidget(control)
        layout.addLayout(row)
        self.status = label("No workspaces yet. Save an app and folder setup to launch it together.", "Muted", True)
        layout.addWidget(self.status)
        self.entries.itemSelectionChanged.connect(self.selection_changed)
        self.refresh()

    def refresh(self, *_):
        previous = selected_record(self.entries)
        query = self.search.text().casefold()
        rows = [row for row in self.services.records.list("workspace") if query in row["name"].casefold()]
        fill_table(self.entries, rows, ["name", lambda r: len(r.get("app_ids", [])),
                                      lambda r: len(r.get("folders", [])), lambda r: len(r.get("urls", [])),
                                      lambda r: len(r.get("window_layouts", []))])
        if previous:
            for index, row in enumerate(rows):
                if row["id"] == previous["id"]:
                    self.entries.selectRow(index)
        self.status.setText(f"{len(rows)} workspaces · select a row to inspect or launch" if rows
                            else "No matching workspaces." if query else "No workspaces yet. Save an app and folder setup to launch it together.")
        self.selection_changed()

    def selection_changed(self):
        record = selected_record(self.entries)
        for control in self.actions:
            control.setEnabled(record is not None)

    def action(self, action):
        record = selected_record(self.entries)
        if not record:
            return
        name = "workspaces." + action
        arguments = {"id": record["id"]}
        if action == "save":
            properties = self.services.registry.get(name).parameters["properties"]
            arguments = {key: value for key, value in record.items() if key in properties and value is not None}
        self.window.open_capabilities(name, arguments)

    def context_menu(self, position):
        item = self.entries.itemAt(position)
        if item is None:
            return
        self.entries.selectRow(item.row())
        menu = QMenu(self.entries)
        for caption, action in (("Preview workspace", "preview"), ("Launch workspace", "launch"),
                                ("Edit workspace", "save"), ("Restore window layout", "restore_layout"),
                                ("Close workspace apps", "close"), ("Delete workspace", "delete")):
            menu.addAction(caption, lambda action=action: self.action(action))
        menu.exec(self.entries.mapToGlobal(position))
        menu.deleteLater()
