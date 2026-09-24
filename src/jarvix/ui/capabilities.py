"""Local tool forms and a cancellable worker with GUI-thread permission requests."""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .chat import PermissionDialog
from .widgets import ApprovalBridge, button, label


class ToolJob(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    activity = Signal(str, object)
    approval = Signal(object)

    def __init__(self, services, name, arguments, parent=None):
        super().__init__(parent)
        self.services, self.name, self.arguments = services, name, arguments
        self.cancel = threading.Event()

    def approve(self, request):
        bridge = ApprovalBridge(request)
        self.approval.emit(bridge)
        while not bridge.ready.wait(.1):
            if self.cancel.is_set():
                return False
        return bridge.answer and not self.cancel.is_set()

    def run(self):
        try:
            self.succeeded.emit(self.services.execute_tool(
                self.name, self.arguments, approve=self.approve, cancel=self.cancel,
                on_event=lambda kind, data: self.activity.emit(kind, data),
            ))
        except Exception:
            self.failed.emit("The local operation could not finish. Check Activity for its status.")


class ParameterForm(QWidget):
    """Omit untouched optional arguments so each service retains its own defaults."""

    def __init__(self, schema, initial=None, parent=None):
        super().__init__(parent)
        self.fields = {}
        initial = initial or {}
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        required = schema.get("required", [])
        for name, definition in schema.get("properties", {}).items():
            kind = definition.get("type")
            if "enum" in definition:
                editor = QComboBox()
                for option in definition["enum"]:
                    editor.addItem(str(option), option)
                if name in initial:
                    editor.setCurrentIndex(editor.findData(initial[name]))
            elif kind == "boolean":
                editor = QCheckBox("Enabled")
                editor.setChecked(bool(initial.get(name, definition.get("default", False))))
            elif kind == "integer" and (definition.get("maximum", 2147483647) > 2147483647
                                         or definition.get("minimum", -2147483647) < -2147483647):
                editor = QLineEdit(str(initial.get(name, definition.get("default", ""))))
                editor.setPlaceholderText("Whole number")
            elif kind == "integer":
                editor = QSpinBox()
                editor.setRange(max(-2147483647, definition.get("minimum", -2147483647)),
                                min(2147483647, definition.get("maximum", 2147483647)))
                editor.setValue(initial.get(name, definition.get("default", definition.get("minimum", 0))))
            elif kind == "number":
                editor = QDoubleSpinBox()
                editor.setRange(definition.get("minimum", -1e12), definition.get("maximum", 1e12))
                editor.setDecimals(4)
                editor.setValue(initial.get(name, definition.get("default", definition.get("minimum", 0))))
            elif kind == "string" and definition.get("maxLength", 0) <= 5000:
                editor = QLineEdit(str(initial.get(name, definition.get("default", ""))))
                editor.setMaxLength(definition.get("maxLength", 100000))
            else:
                editor = QPlainTextEdit()
                editor.setMaximumHeight(145)
                if kind == "string":
                    editor.setPlainText(str(initial.get(name, "")))
                else:
                    default = [] if kind == "array" else {}
                    editor.setPlainText(json.dumps(initial.get(name, default), indent=2))
                    editor.setPlaceholderText("JSON " + str(kind or "value"))
            editor.setAccessibleName(name)
            editor.setToolTip(definition.get("description", name.replace("_", " ")))
            enabled = None
            if name not in required:
                enabled = QCheckBox(name.replace("_", " "))
                enabled.setToolTip("Include this optional argument")
                enabled.setChecked(name in initial)
                editor.setEnabled(enabled.isChecked())
                enabled.toggled.connect(editor.setEnabled)
                layout.addRow(enabled, editor)
            else:
                layout.addRow(name.replace("_", " ") + " *", editor)
            self.fields[name] = (definition, editor, enabled)
        if not self.fields:
            layout.addRow(label("This action needs no arguments.", "Muted"))

    def arguments(self):
        values = {}
        for name, (definition, editor, enabled) in self.fields.items():
            if enabled is not None and not enabled.isChecked():
                continue
            if isinstance(editor, QComboBox):
                value = editor.currentData()
            elif isinstance(editor, QCheckBox):
                value = editor.isChecked()
            elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                value = editor.value()
            elif isinstance(editor, QLineEdit):
                value = editor.text()
                if definition.get("type") == "integer":
                    try:
                        value = int(value)
                    except ValueError:
                        raise ValueError(f"{name}: enter a whole number.") from None
            else:
                value = editor.toPlainText()
                if definition.get("type") != "string":
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{name}: enter valid JSON ({exc.msg}).") from None
            values[name] = value
        return values


class CapabilityDialog(QDialog):
    def __init__(self, window, tool_name=None, arguments=None):
        super().__init__(window)
        self.window, self.services = window, window.services
        self.worker = None
        self.pending_dialog = None
        self.selected_tool = None
        self.form = None
        self.setWindowTitle("Jarvix · Local actions")
        self.resize(850, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.addWidget(label("LOCAL ACTIONS", "Eyebrow"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search registered capabilities…")
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.tools = QComboBox()
        self.tools.setMinimumContentsLength(30)
        row.addWidget(self.tools, 1)
        self.favorite = QCheckBox("Favorite")
        self.favorite.toggled.connect(self.set_favorite)
        row.addWidget(self.favorite)
        layout.addLayout(row)
        self.description = label("", "Muted", True)
        layout.addWidget(self.description)
        self.permission = label("", "Accent", True)
        layout.addWidget(self.permission)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        layout.addWidget(self.form_scroll, 2)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("Results stay on this computer. Nothing here is sent to an AI provider.")
        layout.addWidget(self.result, 1)
        self.status = label("Ready", "Muted", True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.run_button = button("Run action", self.run_action, "Primary")
        self.cancel_button = button("Cancel action", self.cancel)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(button("Close", self.reject))
        layout.addLayout(buttons)
        self.search.textChanged.connect(self.filter_tools)
        self.tools.currentIndexChanged.connect(self.select_tool)
        self.filter_tools("")
        if tool_name:
            self.select(tool_name, arguments)

    def filter_tools(self, query):
        selected = self.selected_tool
        self.tools.blockSignals(True)
        self.tools.clear()
        for spec in self.services.registry.specs():
            if all(word in (spec.name + " " + spec.description).casefold() for word in query.casefold().split()):
                self.tools.addItem(spec.name, spec.name)
        found = self.tools.findData(selected)
        self.tools.setCurrentIndex(found if found >= 0 else 0)
        self.tools.blockSignals(False)
        self.select_tool()

    def select(self, tool_name, arguments=None):
        if self.worker:
            self.status.setText("Finish or cancel the current action before selecting another.")
            return
        self.search.clear()
        self.tools.setCurrentIndex(self.tools.findData(tool_name))
        self.select_tool(arguments=arguments)

    def select_tool(self, *_args, arguments=None):
        self.selected_tool = self.tools.currentData()
        if self.selected_tool is None:
            self.run_button.setEnabled(False)
            self.description.setText("No matching capability. Try a different search.")
            self.permission.clear()
            if self.form:
                self.form.setEnabled(False)
            return
        spec = self.services.registry.get(self.selected_tool)
        self.description.setText(spec.description)
        level = spec.permission_level or (1 if spec.risk == "read" else 2)
        descriptions = {1: "Read only", 2: "Reversible action · requires computer-control permission or one-time approval",
                        3: "Sensitive action · explicit confirmation required immediately before execution"}
        self.permission.setText(f"Level {level} · {descriptions[level]}")
        self.form = ParameterForm(spec.parameters, arguments)
        self.form_scroll.setWidget(self.form)
        self.result.clear()
        self.status.setText("Ready")
        self.run_button.setEnabled(True)
        self.favorite.blockSignals(True)
        self.favorite.setChecked(self.selected_tool in self.services.settings.get("commands.favorites", []))
        self.favorite.blockSignals(False)

    def set_favorite(self, enabled):
        if not self.selected_tool:
            return
        names = list(self.services.settings.get("commands.favorites", []))
        if enabled and self.selected_tool not in names:
            names.append(self.selected_tool)
        elif not enabled and self.selected_tool in names:
            names.remove(self.selected_tool)
        self.services.settings.set("commands.favorites", names[:100])

    def run_action(self):
        if self.worker or not self.selected_tool:
            return
        try:
            arguments = self.form.arguments()
            invalid = self.services.registry.validate(self.selected_tool, arguments)
            if invalid:
                self.status.setText(invalid.error)
                return
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.worker = ToolJob(self.services, self.selected_tool, arguments, self.window)
        self.window.jobs.add(self.worker)
        self.worker.succeeded.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.approval.connect(self.approve)
        self.worker.activity.connect(lambda _kind, data: self.status.setText(str(data.get("status", "Working…"))))
        self.worker.finished.connect(self.worker_finished)
        for widget in (self.search, self.tools, self.form, self.run_button):
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Checking permissions…")
        recent = self.services.settings.get("commands.recent", [])
        self.services.settings.set("commands.recent", [self.selected_tool] + [name for name in recent if name != self.selected_tool][:19])
        self.worker.start()

    def approve(self, bridge):
        try:
            if not self.worker or self.worker.cancel.is_set() or self.window.closing:
                return
            self.pending_dialog = PermissionDialog(bridge.request, self)
            bridge.answer = self.pending_dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.pending_dialog = None
            bridge.ready.set()

    def completed(self, result):
        self.result.setPlainText(json.dumps(result.as_dict(), indent=2, ensure_ascii=False, default=str))
        self.status.setText("Completed locally" if result.ok else (result.error or "Action failed"))

    def failed(self, message):
        self.status.setText(message)

    def worker_finished(self):
        worker, self.worker = self.worker, None
        for widget in (self.search, self.tools, self.form, self.run_button):
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.window.release_job(worker)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText("Stopping at the next safe cancellation point…")
        if self.pending_dialog:
            self.pending_dialog.reject()

    def reject(self):
        self.cancel()
        super().reject()

    def closeEvent(self, event):
        self.cancel()
        event.accept()
