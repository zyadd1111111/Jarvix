"""A vertical workflow editor that uses registered tools and their real schemas."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .capabilities import ParameterForm
from .chat import PermissionDialog
from .operator import ServiceJob
from .widgets import TextPreview, button, label, clear_layout


TRIGGERS = ["manual", "schedule", "interval", "at_time", "jarvix_start", "windows_start",
            "app_start", "app_closed", "file_created", "file_modified", "folder_change",
            "clipboard_changed", "battery_below", "cpu_above", "memory_above",
            "network_connected", "network_disconnected", "task_due", "hotkey"]


def decode(editor, expected, caption):
    try:
        value = json.loads(editor.toPlainText())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{caption}: {exc.msg}") from None
    if not isinstance(value, expected):
        raise ValueError(f"{caption} must be a JSON {'object' if expected is dict else 'array'}.")
    return value


class WorkflowSteps(QWidget):
    changed = Signal()

    def __init__(self, services, steps=None, parent=None):
        super().__init__(parent)
        self.services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for kind in ("action", "delay", "branch", "notification", "set", "foreach", "subflow"):
            row.addWidget(button("+ " + kind.capitalize(), lambda k=kind: self.add(k), "Quiet"))
        row.addStretch()
        layout.addLayout(row)
        self.blocks = QListWidget()
        self.blocks.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.blocks.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.blocks.setAccessibleName("Workflow blocks, drag to reorder")
        self.blocks.itemDoubleClicked.connect(lambda _item: self.edit())
        self.blocks.model().rowsMoved.connect(lambda *_: self.changed.emit())
        layout.addWidget(self.blocks, 1)
        row = QHBoxLayout()
        row.addWidget(button("Edit block", self.edit))
        row.addWidget(button("↑", lambda: self.move(-1), "Quiet"))
        row.addWidget(button("↓", lambda: self.move(1), "Quiet"))
        row.addWidget(button("Remove block", self.remove, "Quiet"))
        row.addStretch()
        layout.addLayout(row)
        for step in steps or []:
            self.append(step)

    def append(self, step, item=None):
        kind = step.get("kind", "action")
        if kind == "action":
            text = step.get("tool", "Choose an action")
            detail = "Retry once" if step.get("retries") else "Stop on failure" if step.get("on_error", "stop") == "stop" else "Continue on failure"
        elif kind == "delay":
            text, detail = f"Wait {step.get('seconds', 1)} seconds", "Cancellation remains available"
        elif kind in {"set", "foreach", "subflow"}:
            text = {"set": "Set variable", "foreach": "Read-only collection loop", "subflow": "Pinned subflow"}[kind]
            detail = step.get("name", step.get("workflow_id", "Bounded structured values"))
        else:
            text = "Branch · " + step.get("condition", {}).get("kind", "condition")
            detail = f"Then: {len(step.get('then', []))} action(s) · Otherwise: {len(step.get('else', []))} action(s)"
        created = item is None
        item = item or QListWidgetItem()
        item.setText(text + "\n" + detail)
        item.setData(Qt.ItemDataRole.UserRole, step)
        if created:
            self.blocks.addItem(item)
        self.changed.emit()

    def steps(self):
        return [self.blocks.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.blocks.count())]

    def add(self, kind):
        step = {"kind": kind}
        if kind == "notification":
            step = {"kind": "action", "tool": "notifications.create", "arguments": {}}
        dialog = WorkflowBlockDialog(self.services, step, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.append(dialog.value)

    def edit(self):
        item = self.blocks.currentItem()
        if item:
            dialog = WorkflowBlockDialog(self.services, item.data(Qt.ItemDataRole.UserRole), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.append(dialog.value, item)

    def move(self, offset):
        index = self.blocks.currentRow()
        target = index + offset
        if index >= 0 and 0 <= target < self.blocks.count():
            self.blocks.insertItem(target, self.blocks.takeItem(index))
            self.blocks.setCurrentRow(target)
            self.changed.emit()

    def remove(self):
        index = self.blocks.currentRow()
        if index >= 0:
            self.blocks.takeItem(index)
            self.changed.emit()


class WorkflowBlockDialog(QDialog):
    def __init__(self, services, step, parent=None):
        super().__init__(parent)
        self.services, self.step = services, step
        self.kind = step.get("kind", "action")
        self.setWindowTitle("Workflow · " + self.kind.capitalize())
        self.resize(780, 620)
        layout = QVBoxLayout(self)
        self.value = None
        if self.kind == "action":
            self.tool = QComboBox()
            for spec in services.registry.specs():
                if spec.name.startswith("operator."):
                    continue
                if spec.name.startswith(("workflows.", "routines.")) and spec.name not in {"workflows.run", "routines.run"}:
                    continue
                self.tool.addItem(spec.name, spec.name)
            self.tool.setCurrentIndex(max(0, self.tool.findData(step.get("tool", "tasks.list"))))
            layout.addWidget(self.tool)
            self.permission = label("", "Muted", True)
            layout.addWidget(self.permission)
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            layout.addWidget(self.scroll, 1)
            self.retries = QSpinBox()
            self.retries.setRange(0, 1)
            self.retries.setValue(step.get("retries", 0))
            self.on_error = QComboBox()
            self.on_error.addItems(["stop", "continue"])
            self.on_error.setCurrentText(step.get("on_error", "stop"))
            form = QFormLayout()
            form.addRow("Controlled retries", self.retries)
            form.addRow("If the action fails", self.on_error)
            layout.addLayout(form)
            self.tool.currentIndexChanged.connect(self.select_tool)
            self.select_tool(initial=step.get("arguments", {}))
            self.output_id = QLineEdit(step.get("id", ""))
            self.output_id.setPlaceholderText("Optional output ID for later results.ID.data references")
            layout.addWidget(self.output_id)
            self.structured = QCheckBox("Use structured JSON arguments / references")
            self.raw_arguments = QPlainTextEdit(json.dumps(step.get("arguments", {}), indent=2))
            self.raw_arguments.setMaximumHeight(155)
            self.raw_arguments.hide()
            self.structured.toggled.connect(self.raw_arguments.setVisible)
            self.structured.toggled.connect(lambda checked: self.scroll.setVisible(not checked))
            layout.addWidget(self.structured)
            layout.addWidget(self.raw_arguments)
            from jarvix.capabilities.operator_graph import references
            self.structured.setChecked(bool(references(step.get("arguments", {}))))
        elif self.kind == "delay":
            self.seconds = QSpinBox()
            self.seconds.setRange(0, 60)
            self.seconds.setSuffix(" seconds")
            self.seconds.setValue(step.get("seconds", 1))
            layout.addWidget(self.seconds)
            layout.addStretch()
        elif self.kind in {"set", "foreach", "subflow"}:
            defaults = {"set": {"kind": "set", "name": "topic", "value": "Jarvix"},
                        "foreach": {"kind": "foreach", "items": [], "limit": 20, "steps": [
                            {"kind": "action", "tool": "tasks.list", "arguments": {}}]},
                        "subflow": {"kind": "subflow", "workflow_id": ""}}
            layout.addWidget(label("Structured block. The full workflow validates references, limits, immutable subflows and permissions before saving.", "Muted", True))
            self.raw_block = QPlainTextEdit(json.dumps(step if len(step) > 1 else defaults[self.kind], indent=2))
            layout.addWidget(self.raw_block, 1)
        else:
            layout.addWidget(label("Safe condition", "Heading"))
            self.condition = QPlainTextEdit()
            self.condition.setMaximumHeight(130)
            self.condition.setPlainText(json.dumps(step.get("condition", {"kind": "weekday", "days": [0, 1, 2, 3, 4]}), indent=2))
            layout.addWidget(self.condition)
            tabs = QTabWidget()
            self.then_steps = WorkflowSteps(services, step.get("then", []))
            self.else_steps = WorkflowSteps(services, step.get("else", []))
            tabs.addTab(self.then_steps, "Condition met")
            tabs.addTab(self.else_steps, "Otherwise")
            layout.addWidget(tabs, 1)
        self.error = label("", "Muted", True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", self.reject))
        row.addWidget(button("Apply block", self.save, "Primary"))
        layout.addLayout(row)

    def select_tool(self, *_args, initial=None):
        from jarvix.capabilities.workflows import RETRY_SAFE
        from jarvix.capabilities.operator_graph import references
        spec = self.services.registry.get(self.tool.currentData())
        literal = {key: value for key, value in (initial or {}).items() if not references(value)}
        self.form = ParameterForm(spec.parameters, literal)
        self.scroll.setWidget(self.form)
        self.permission.setText(f"{spec.description}\nPermission: {spec.permission} · Level {spec.permission_level or (1 if spec.risk == 'read' else 2)}")
        can_retry = spec.permission_level == 1 and spec.name in RETRY_SAFE
        self.retries.setEnabled(can_retry)
        if not can_retry:
            self.retries.setValue(0)

    def save(self):
        try:
            if self.kind == "action":
                arguments = decode(self.raw_arguments, dict, "Arguments") if self.structured.isChecked() else self.form.arguments()
                tool = self.tool.currentData()
                invalid = self.services.registry.validate(tool, arguments)
                from jarvix.capabilities.operator_graph import references
                if invalid and not references(arguments):
                    raise ValueError(invalid.error)
                self.value = {"kind": "action", "tool": tool, "arguments": arguments,
                              "retries": self.retries.value(), "on_error": self.on_error.currentText()}
                if self.output_id.text().strip():
                    self.value["id"] = self.output_id.text().strip()
            elif self.kind == "delay":
                self.value = {"kind": "delay", "seconds": self.seconds.value()}
            elif self.kind in {"set", "foreach", "subflow"}:
                self.value = decode(self.raw_block, dict, "Block")
                if self.value.get("kind") != self.kind:
                    raise ValueError("Keep the selected block kind.")
            else:
                self.value = {"kind": "branch", "condition": decode(self.condition, dict, "Condition"),
                              "then": self.then_steps.steps(), "else": self.else_steps.steps()}
            self.accept()
        except (ValueError, KeyError) as exc:
            self.error.setText(str(exc))


class WorkflowBuilder(QDialog):
    def __init__(self, window, definition=None):
        super().__init__(window)
        self.window, self.services = window, window.services
        self.definition = definition or {}
        self.worker = None
        self.pending_dialog = None
        self.pending_definition = None
        self.setWindowTitle("Jarvix · Workflow builder")
        self.resize(1040, 770)
        layout = QVBoxLayout(self)
        layout.addWidget(label("WORKFLOW BUILDER", "Eyebrow"))
        layout.addWidget(label("Trigger → conditions → ordered actions", "Heading"))
        self.name = QLineEdit(self.definition.get("name", ""))
        self.name.setPlaceholderText("Name this workflow or routine")
        layout.addWidget(self.name)
        split = self.editor = QSplitter()
        config = QWidget()
        form = QVBoxLayout(config)
        self.kind = QComboBox()
        self.kind.addItems(["workflow", "routine"])
        self.kind.setCurrentText(self.definition.get("kind", "workflow"))
        self.kind.currentTextChanged.connect(self.kind_changed)
        form.addWidget(label("TYPE", "Eyebrow"))
        form.addWidget(self.kind)
        self.trigger = QComboBox()
        self.trigger.addItems(TRIGGERS)
        for item in self.services.plugins.contributions("triggers"):
            self.trigger.addItem(item.get("title", item["name"]) + " · extension", item["target"])
        self.trigger.setCurrentText(self.definition.get("trigger", "manual"))
        form.addWidget(label("TRIGGER", "Eyebrow"))
        form.addWidget(self.trigger)
        self.config = QPlainTextEdit()
        self.config.setPlainText(json.dumps(self.definition.get("config", {}), indent=2))
        self.config.setMaximumHeight(170)
        form.addWidget(self.config)
        self.trigger.currentTextChanged.connect(self.trigger_changed)
        from jarvix.capabilities.workflows import WORKFLOW_HOTKEYS
        self.hotkey = QComboBox()
        self.hotkey.addItems(list(WORKFLOW_HOTKEYS))
        self.hotkey.setCurrentText(self.definition.get("config", {}).get("shortcut", "Ctrl+Alt+F1"))
        self.hotkey.setVisible(self.trigger.currentText() == "hotkey")
        self.hotkey.setToolTip("The shortcut works while Jarvix is running, including in the tray.")
        form.addWidget(self.hotkey)
        form.addWidget(label("CONDITIONS", "Eyebrow"))
        self.conditions = QPlainTextEdit()
        self.conditions.setPlainText(json.dumps(self.definition.get("conditions", []), indent=2))
        self.conditions.setMaximumHeight(170)
        form.addWidget(self.conditions)
        form.addWidget(label("VARIABLES", "Eyebrow"))
        self.variables = QPlainTextEdit(json.dumps(self.definition.get("variables", {}), indent=2))
        self.variables.setMaximumHeight(90)
        self.variables.setToolTip("JSON values. Reference with {\"$ref\": \"variables.name\"} in structured arguments.")
        form.addWidget(self.variables)
        self.enabled = QCheckBox("Enable this workflow")
        self.enabled.setChecked(self.definition.get("enabled", False))
        form.addWidget(self.enabled)
        form.addWidget(label("Scheduled changes need explicit tool approval. Every run retains permission checks.", "Muted", True))
        self.grant_widgets = {}
        self.grants = QVBoxLayout()
        form.addLayout(self.grants)
        form.addStretch()
        split.addWidget(config)
        self.steps = WorkflowSteps(self.services, self.definition.get("steps", []))
        self.steps.changed.connect(self.refresh_grants)
        split.addWidget(self.steps)
        split.setSizes([310, 700])
        layout.addWidget(split, 1)
        self.status = label("Drag blocks to reorder. Double-click a block to edit it.", "Muted", True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(button("Preview / validate", self.preview))
        self.test_button = button("Test conditions", self.test)
        row.addWidget(self.test_button)
        row.addWidget(button("Copy definition", self.copy_definition, "Quiet"))
        row.addWidget(button("Debug saved workflow", self.debug, "Quiet"))
        row.addWidget(button("Templates", lambda: self.window.open_capabilities("workflows.templates"), "Quiet"))
        row.addStretch()
        self.cancel_button = button("Cancel active save", self.cancel)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        self.save_button = button("Review and save", self.save, "Primary")
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.refresh_grants()
        self.kind_changed(self.kind.currentText())

    def kind_changed(self, kind):
        if not hasattr(self, "trigger"):
            return
        if kind == "routine":
            self.trigger.setCurrentText("manual")
        self.trigger.setEnabled(kind != "routine")

    def refresh_grants(self):
        from jarvix.capabilities.workflows import BACKGROUND_OPT_IN

        def tools(steps):
            for step in steps:
                if step.get("kind") == "action":
                    yield step.get("tool")
                elif step.get("kind") == "branch":
                    yield from tools(step.get("then", []))
                    yield from tools(step.get("else", []))

        checked = {name for name, widget in self.grant_widgets.items() if widget.isChecked()}
        if not self.grant_widgets:
            checked = set(self.definition.get("approved_tools", []))
        clear_layout(self.grants)
        self.grant_widgets = {}
        names = sorted(set(tools(self.steps.steps())) & BACKGROUND_OPT_IN)
        if names:
            self.grants.addWidget(label("BACKGROUND APPROVALS", "Eyebrow"))
            self.grants.addWidget(label("Optional: allow these exact actions and arguments when triggered. Review them before saving.", "Muted", True))
        for name in names:
            check = QCheckBox(name)
            check.setChecked(name in checked)
            self.grant_widgets[name] = check
            self.grants.addWidget(check)

    def trigger_changed(self, trigger):
        trigger = self.trigger.currentData() or trigger
        defaults = {"schedule": {"time": "16:00", "weekdays": [0, 1, 2, 3, 4]},
                    "interval": {"minutes": 60}, "at_time": {"at": ""},
                    "app_start": {"name": ""}, "app_closed": {"name": ""},
                    "file_created": {"path": ""}, "file_modified": {"path": ""},
                    "folder_change": {"path": ""}, "battery_below": {"threshold": 20},
                    "cpu_above": {"threshold": 90}, "memory_above": {"threshold": 90},
                    "hotkey": {"shortcut": "Ctrl+Alt+F1"}, "clipboard_changed": {"opt_in": False}}
        self.config.setPlainText(json.dumps(defaults.get(trigger, {}), indent=2))
        self.hotkey.setVisible(trigger == "hotkey")

    def values(self):
        value = {"name": self.name.text().strip(), "kind": self.kind.currentText(),
                 "trigger": self.trigger.currentData() or self.trigger.currentText(), "config": decode(self.config, dict, "Trigger settings"),
                 "conditions": decode(self.conditions, list, "Conditions"),
                 "steps": self.steps.steps(), "enabled": self.enabled.isChecked(),
                 "approved_tools": [name for name, widget in self.grant_widgets.items() if widget.isChecked()]}
        variables = decode(self.variables, dict, "Variables")
        if variables:
            value["variables"] = variables
        if value["trigger"] == "hotkey":
            value["config"] = {"shortcut": self.hotkey.currentText()}
        if not value["name"]:
            raise ValueError("Name the workflow before saving.")
        if self.definition.get("id"):
            value["id"] = self.definition["id"]
        return value

    def preview(self):
        try:
            preview = self.services.workflows.preview_definition(**self.values())
            TextPreview("Workflow preview", "Validation only. No action has executed.",
                        json.dumps(preview, indent=2, ensure_ascii=False, default=str), self).exec()
            self.status.setText("Definition validated. Review and save when ready.")
        except (ValueError, TypeError, PermissionError, KeyError) as exc:
            self.status.setText(str(exc))

    def debug(self):
        if self.definition.get("id"):
            self.window.open_capabilities("workflows.debug", {"id": self.definition["id"]})
        else:
            self.status.setText("Save and review the workflow before debugging. Only read-only steps execute in debug mode.")

    def test(self):
        if self.worker:
            return
        try:
            definition = self.values()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.test_button.setEnabled(False)
        self.status.setText("Testing conditions; no action will execute…")

        def done(result):
            self.test_button.setEnabled(True)
            if not result.ok:
                self.status.setText(result.error)
            else:
                self.status.setText("Conditions met · no actions executed." if result.data["would_run"] else
                                    "Conditions are not currently met · no actions executed.")

        def failed(error):
            self.test_button.setEnabled(True)
            self.status.setText(error)
        self.window.run_job(lambda: self.services.execute_tool("workflows.test_definition", definition), done, failed)

    def copy_definition(self):
        try:
            QApplication.clipboard().setText(json.dumps(self.values(), indent=2, ensure_ascii=False))
            self.status.setText("Definition copied to clipboard.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def save(self):
        if self.worker:
            return
        try:
            definition = self.values()
            self.services.workflows.preview_definition(**definition)
        except (ValueError, TypeError, PermissionError, KeyError) as exc:
            self.status.setText(str(exc))
            return
        self.pending_definition = definition
        self.worker = ServiceJob(lambda **kwargs: self.services.execute_tool("workflows.save", definition, **kwargs), self.window)
        self.window.jobs.add(self.worker)
        self.worker.approval.connect(self.approve)
        self.worker.succeeded.connect(self.saved)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.worker_finished)
        self.save_button.setEnabled(False)
        self.editor.setEnabled(False)
        self.name.setEnabled(False)
        self.test_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("Waiting for your review…")
        self.worker.start()

    def approve(self, bridge):
        try:
            if self.window.closing or not self.worker or self.worker.cancel.is_set():
                return
            self.pending_dialog = PermissionDialog(bridge.request, self)
            bridge.answer = self.pending_dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.pending_dialog = None
            bridge.ready.set()

    def saved(self, result):
        if result.ok:
            self.definition.update(self.pending_definition)
            self.definition["id"] = result.data["id"]
            self.status.setText("Saved locally. Scheduled runs respect all permissions.")
            self.window.pages["Automations"].refresh()
            self.window.configure_workflow_hotkeys()
        else:
            self.status.setText(result.error or "The workflow was not saved.")

    def worker_finished(self):
        worker, self.worker = self.worker, None
        self.window.release_job(worker)
        self.save_button.setEnabled(True)
        self.editor.setEnabled(True)
        self.name.setEnabled(True)
        self.test_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
        if self.pending_dialog:
            self.pending_dialog.reject()

    def reject(self):
        self.cancel()
        super().reject()


class WorkflowHistory(QDialog):
    def __init__(self, window, workflow_id):
        super().__init__(window)
        self.window, self.workflow_id = window, workflow_id
        self._revision = None
        self.setWindowTitle("Workflow · Run history")
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        self.runs = QListWidget()
        layout.addWidget(self.runs)
        self.runs.currentItemChanged.connect(self.update_controls)
        self.status = label("", "Muted", True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.controls = {}
        for action in ("details", "pause", "resume", "cancel"):
            control = button(action.capitalize(), lambda name=action: self.selected(name))
            self.controls[action] = control
            row.addWidget(control)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        if self.window.closing:
            self.timer.stop()
            return
        records = self.window.services.workflows.history(self.workflow_id)
        revision = [(run["id"], run.get("updated_at"), run.get("status")) for run in records]
        if revision == self._revision:
            return
        self._revision = revision
        item = self.runs.currentItem()
        selected_id = item.data(Qt.ItemDataRole.UserRole)["id"] if item else None
        self.runs.blockSignals(True)
        self.runs.clear()
        for run in records:
            item = QListWidgetItem(f"{run.get('status', 'unknown')} · {run.get('created_at', '')}")
            item.setData(Qt.ItemDataRole.UserRole, run)
            self.runs.addItem(item)
            if run["id"] == selected_id:
                self.runs.setCurrentItem(item)
        if self.runs.currentRow() < 0 and records:
            self.runs.setCurrentRow(0)
        self.runs.blockSignals(False)
        self.status.setText("" if records else "No runs yet. Run this workflow manually or enable its trigger.")
        self.update_controls()

    def update_controls(self, *_):
        item = self.runs.currentItem()
        state = item.data(Qt.ItemDataRole.UserRole).get("status") if item else None
        self.controls["details"].setEnabled(item is not None)
        self.controls["pause"].setEnabled(state == "running")
        self.controls["resume"].setEnabled(state == "paused")
        self.controls["cancel"].setEnabled(state in {"running", "paused"})

    def selected(self, action):
        item = self.runs.currentItem()
        if not item:
            return
        run = item.data(Qt.ItemDataRole.UserRole)
        if action == "details":
            TextPreview("Run details", "Stored locally", json.dumps(run, indent=2, default=str), self).exec()
            return
        # Explicit session controls must remain reachable while the action runner is occupied.
        # They only alter the current run's cooperative state, never execute its actions.
        if self.window.guard(lambda: getattr(self.window.services.workflows, action)(run["id"])):
            self.status.setText(f"{action.capitalize()} requested; waiting for the next safe checkpoint…")
        self.refresh()


def import_definition(window):
    dialog = QDialog(window)
    dialog.setWindowTitle("Import workflow")
    dialog.resize(720, 560)
    layout = QVBoxLayout(dialog)
    layout.addWidget(label("Paste an exported workflow. Imported workflows start disabled.", "Muted", True))
    editor = QPlainTextEdit()
    layout.addWidget(editor)
    error = label("", "Muted", True)
    layout.addWidget(error)

    def review():
        try:
            definition = decode(editor, dict, "Workflow")
        except ValueError as exc:
            error.setText(str(exc))
            return
        dialog.accept()
        window.open_capabilities("workflows.import", {"definition": definition})

    layout.addWidget(button("Review import", review, "Primary"))
    dialog.exec()
