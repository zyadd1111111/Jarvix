"""Daily context, checkpoint and skill views over the existing local tool forms."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
    QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem,
)

from .widgets import button, label
from .icons import icon


class RecordInspector(QWidget):
    """Readable local records with an optional exact technical inspection."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = label("Select an entry", "Heading", True)
        self.metadata = label("", "Muted", True)
        layout.addWidget(self.title)
        layout.addWidget(self.metadata)
        self.fields = QTreeWidget(self)
        self.fields.setHeaderLabels(["Item", "Value"])
        self.fields.setColumnWidth(0, 190)
        self.fields.setAccessibleName("Saved record fields and resources")
        layout.addWidget(self.fields, 1)
        self.toggle = button("Show technical details", self.toggle_details, "Quiet")
        self.toggle.setEnabled(False)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.raw = QPlainTextEdit(self)
        self.raw.setReadOnly(True)
        self.raw.setAccessibleName("Exact local record details")
        self.raw.hide()
        layout.addWidget(self.raw, 1)

    def toggle_details(self):
        visible = self.raw.isHidden()
        self.raw.setVisible(visible)
        self.fields.setVisible(not visible)
        self.toggle.setText("Hide technical details" if visible else "Show technical details")

    def clear(self):
        self.title.setText("Select an entry")
        self.metadata.clear()
        self.fields.clear()
        self.raw.clear()
        self.toggle.setEnabled(False)

    def set_record(self, value, kind=None):
        self.clear()
        self._remaining = 100
        self.raw.setPlainText(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        self.toggle.setEnabled(True)
        if not isinstance(value, dict):
            value = {"Entries": value}
        self.title.setText(str(value.get("name") or value.get("summary") or value.get("goal") or "Local inspection"))
        metadata = [str(value[key]) for key in ("description", "status", "version", "updated_at") if value.get(key) is not None]
        self.metadata.setText(" · ".join(metadata))
        if kind == "skills":
            usage = value.get("usage", {})
            self.metadata.setText(("Enabled" if value.get("enabled") else "Disabled") + " · Version " + str(value.get("version", "1"))
                                  + f" · {usage.get('recorded_runs', 0)} recorded runs")
            self._add("Description", value.get("description", ""))
            self._add("Instructions", value.get("instructions", ""))
            self._add("Actions", [{"tool": step.get("tool", ""), "on_error": step.get("on_error", "stop")}
                                   for step in value.get("routine", {}).get("steps", [])])
            self._add("Required permissions", value.get("required_permissions", []))
            self._add("Required integrations", value.get("required_integrations", []))
            self._add("Recorded outcomes", usage)
            self._add("Input policy", value.get("input_policy", ""))
        else:
            # ponytail: inspect at most 100 visible fields; exact records remain available in Details.
            self._remaining = 100
            for key, item in value.items():
                if key not in {"name", "summary", "goal", "description", "status", "updated_at"}:
                    self._add(key.replace("_", " ").title(), item)
        self.fields.expandToDepth(0)

    def _add(self, caption, value, parent=None, depth=0):
        if not hasattr(self, "_remaining"):
            self._remaining = 100
        if self._remaining <= 0:
            return
        self._remaining -= 1
        if isinstance(value, dict):
            text = str(value.get("title") or value.get("name") or value.get("tool") or "")
        elif isinstance(value, list):
            text = f"{len(value)} items"
        elif value is None:
            text = "Not recorded"
        elif isinstance(value, bool):
            text = "Yes" if value else "No"
        else:
            text = str(value)
        node = QTreeWidgetItem([caption, text[:500]])
        node.setToolTip(1, text)
        parent.addChild(node) if parent is not None else self.fields.addTopLevelItem(node)
        if depth < 3:
            items = value.items() if isinstance(value, dict) else enumerate(value, 1) if isinstance(value, list) else []
            for key, item in items:
                self._add(str(key).replace("_", " ").title(), item, node, depth + 1)


class SavedWorkView(QWidget):
    def __init__(self, dialog, kind):
        super().__init__(dialog)
        self.dialog, self.kind = dialog, kind
        self._rows, self._selection = [], None
        layout = QVBoxLayout(self)
        descriptions = {
            "continuity": "Save your progress and next step. Continue reviews current access and never replays uncertain actions.",
            "context.profiles": "Named personal context pins existing memories and references. Activate explicitly; nothing is added to AI prompts automatically.",
            "skills": "Learn a reviewed recipe from verified work or a manual routine. Each run keeps the original permissions.",
        }
        layout.addWidget(label(descriptions[kind], "Muted", True))
        row = QHBoxLayout()
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Filter saved work…")
        self.query.textChanged.connect(self.filter_rows)
        row.addWidget(self.query, 1)
        create = {"continuity": ("Save progress", self.save_progress),
                  "context.profiles": ("New profile", lambda: self.dialog.tool("context.profiles.save")),
                  "skills": ("Create Skill", lambda: self.dialog.tool("skills.learn_preview"))}
        row.addWidget(button(create[kind][0], create[kind][1], "Primary"))
        row.addWidget(button("Refresh", self.refresh, "Quiet"))
        layout.addLayout(row)
        split = QSplitter(self)
        self.items = QListWidget(self)
        self.items.setAccessibleName("Saved " + kind.replace(".", " "))
        self.items.currentItemChanged.connect(self.inspect)
        split.addWidget(self.items)
        self.inspector = RecordInspector(self)
        self.details = self.inspector.raw
        split.addWidget(self.inspector)
        split.setSizes([250, 650])
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        self.status = label("No work selected.", "Muted", True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        actions = {
            "continuity": (("Review next steps", "prepare"), ("Run safe observations", "start_observations"), ("Forget", "forget")),
            "context.profiles": (("Edit profile", "save"), ("Activate profile", "activate"), ("Delete profile", "delete")),
            "skills": (("Preview Skill", "preview"), ("Run Skill", "run"), ("Edit recipe", "learn_preview"),
                       ("Enable / disable Skill", "set_enabled"), ("Delete Skill", "delete")),
        }
        self.controls = []
        for title, action in actions[kind]:
            control = button(title, lambda action=action: self.action(action), "Danger" if action in {"forget", "delete"} else "Quiet")
            control.setEnabled(False)
            row.addWidget(control)
            self.controls.append(control)
        if kind == "context.profiles":
            row.addWidget(button("Unset active profile", lambda: self.dialog.tool("context.profiles.unset"), "Quiet"))
        row.addStretch()
        layout.addLayout(row)

    def save_progress(self):
        chat = self.dialog.window.pages["Chat"]
        args = {"conversation_id": chat.conversation_id} if chat.conversation_id else {}
        self.dialog.tool("continuity.save", args)

    def selected(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def title(row):
        return row.get("name") or row.get("summary") or row["id"]

    def refresh(self):
        self.status.setText("Reading saved local work…")
        def loaded(value):
            self._rows = value if isinstance(value, list) else value.get("items", [])
            self.filter_rows()
            self.status.setText(f"{len(self._rows)} saved entries" if self._rows else "Nothing saved yet. Start with the action above.")
        def failed(message):
            self._rows = []
            self.filter_rows()
            self.status.setText(message)
        self.dialog.read_tool(self.kind + ".list", {}, loaded, failed)

    def filter_rows(self, *_):
        selected = self.selected()
        query = self.query.text().strip().casefold()
        self.items.blockSignals(True)
        self.items.clear()
        for row in self._rows:
            if query and query not in self.title(row).casefold():
                continue
            item = QListWidgetItem(self.title(row)[:200])
            item.setIcon(icon("skills" if self.kind == "skills" else "memory" if self.kind == "context.profiles" else "activity"))
            item.setToolTip(self.title(row))
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.items.addItem(item)
            if selected and selected["id"] == row["id"]:
                self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        if self.items.count() and not self.items.currentItem():
            self.items.setCurrentRow(0)
        else:
            self.inspect()

    def inspect(self, *_):
        row = self.selected()
        self._selection = row["id"] if row else None
        self.inspector.clear()
        for control in self.controls:
            control.setEnabled(bool(row))
        if not row:
            return
        id = row["id"]
        self.status.setText("Inspecting saved references…")
        def loaded(value):
            if self._selection != id:
                return
            self.inspector.set_record(value, self.kind)
            self.status.setText("Local inspection. Review actions before execution.")
        def failed(message):
            if self._selection == id:
                self.status.setText(message)
        self.dialog.read_tool(self.kind + ".get", {"id": id}, loaded, failed)

    def action(self, action):
        row = self.selected()
        if not row:
            return
        args = {"id": row["id"]}
        if action == "set_enabled":
            args["enabled"] = not row.get("enabled", True)
        if action == "save":
            args["name"] = row["name"]
        if action == "learn_preview":
            args.update(name=row["name"], skill_id=row["id"])
        self.dialog.tool(self.kind + "." + action, args)


class LocalOperationsView(QWidget):
    """Small review surfaces; all mutations open the existing permissioned form."""
    def __init__(self, dialog, title, read_tool, description, actions):
        super().__init__(dialog)
        self.dialog, self.read_name = dialog, read_tool
        layout = QVBoxLayout(self)
        layout.addWidget(label(title, "Heading"))
        layout.addWidget(label(description, "Muted", True))
        row = QHBoxLayout()
        for text, tool, arguments in actions:
            row.addWidget(button(text, lambda name=tool, args=arguments: self.dialog.tool(name, args), "Quiet"))
        row.addStretch()
        row.addWidget(button("Refresh", self.refresh, "Quiet"))
        layout.addLayout(row)
        self.inspector = RecordInspector(self)
        self.result = self.inspector.raw
        self.result.setAccessibleName(title + " · local inspection")
        layout.addWidget(self.inspector, 1)
        self.status = label("Ready for local inspection.", "Muted", True)
        layout.addWidget(self.status)

    def refresh(self):
        self.status.setText("Inspecting permitted sources…")
        def loaded(value):
            self.inspector.set_record(value)
            self.status.setText("Local review. No actions were executed.")
        def failed(message):
            self.inspector.clear()
            self.status.setText(message)
        self.dialog.read_tool(self.read_name, {}, loaded, failed)
