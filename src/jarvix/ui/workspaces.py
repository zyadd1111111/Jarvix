"""Workspace management through the existing permission-checked tool forms."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from .pages import fill_table, selected_record
from .widgets import button, label, table


class WorkspaceDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.services = window, window.services
        self.setWindowTitle("Jarvix · Workspaces")
        self.resize(900, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(label("WORKSPACES", "Eyebrow"))
        layout.addWidget(label("Your applications, project and layout together.", "Heading"))
        row = QHBoxLayout()
        row.addWidget(button("Create workspace", lambda: window.open_capabilities("workspaces.save"), "Primary"))
        row.addWidget(button("Save current setup", lambda: window.open_capabilities("workspaces.capture")))
        row.addStretch()
        row.addWidget(button("Refresh", self.refresh, "Quiet"))
        layout.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search saved workspaces…")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.entries = table(["Workspace", "Apps", "Folders", "Websites", "Layouts"])
        self.entries.itemDoubleClicked.connect(lambda _: self.action("preview"))
        layout.addWidget(self.entries, 1)
        self.actions = []
        row = QHBoxLayout()
        for caption, name in (("Preview", "preview"), ("Launch", "launch"), ("Edit", "save"),
                              ("Restore layout", "restore_layout"), ("Close workspace", "close"),
                              ("Delete", "delete")):
            control = button(caption, lambda action=name: self.action(action), "Danger" if name == "delete" else "Quiet")
            self.actions.append(control)
            row.addWidget(control)
        layout.addLayout(row)
        layout.addWidget(label("Launch and close use per-action permissions. Layout restoration only targets matching registered applications.", "Muted", True))
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
