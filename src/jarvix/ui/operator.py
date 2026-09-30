"""Operator sessions and the visible, acknowledged desktop-control boundary."""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QVBoxLayout, QWidget, QProgressBar, QGraphicsScene, QGraphicsView,
)

from .chat import PermissionDialog
from .widgets import ApprovalBridge, TextPreview, button, label


class ServiceJob(QThread):
    """Run service work without moving confirmations onto the worker thread."""

    succeeded = Signal(object)
    failed = Signal(str)
    activity = Signal(str, object)
    approval = Signal(object)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work
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
            self.succeeded.emit(self.work(
                approve=self.approve, cancel=self.cancel,
                on_event=lambda kind, data: self.activity.emit(kind, data),
            ))
        except Exception as exc:
            self.failed.emit(str(exc))


class OperatorDialog(QDialog):
    """Persistent session history with controls for the selected session."""

    def __init__(self, window):
        super().__init__(window)
        self.window, self.services = window, window.services
        self.worker = None
        self.pending_dialog = None
        self.session_id = None
        self._revision = None
        self.setWindowTitle("Jarvix · Operator sessions")
        self.resize(980, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(label("OPERATOR", "Eyebrow"))
        layout.addWidget(label("A visible plan. A controlled execution.", "Heading"))
        row = QHBoxLayout()
        row.addWidget(button("Ask Jarvix to plan a task", self.ask, "Primary"))
        row.addWidget(button("Run a structured plan", self.edit_plan))
        row.addStretch()
        row.addWidget(button("Inspect current context", self.inspect_context, "Quiet"))
        layout.addLayout(row)
        split = QSplitter()
        self.sessions = QListWidget()
        self.sessions.setMinimumWidth(240)
        self.sessions.currentItemChanged.connect(self.selected)
        split.addWidget(self.sessions)
        detail = QWidget()
        body = QVBoxLayout(detail)
        self.goal = label("No operator session yet", "Heading", True)
        body.addWidget(self.goal)
        self.status = label("Ask Jarvix to plan a task or open a saved session.", "Muted", True)
        body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Operator progress")
        body.addWidget(self.progress)
        self.steps = QListWidget()
        self.steps.itemDoubleClicked.connect(lambda _item: self.details())
        body.addWidget(self.steps, 1)
        self.graph_view = QGraphicsView()
        self.graph_view.setAccessibleName("Operator dependency graph")
        self.graph_view.setMinimumHeight(180)
        self.graph_view.hide()
        body.addWidget(self.graph_view, 1)
        controls = QHBoxLayout()
        self.pause_button = button("Pause", self.pause)
        self.resume_button = button("Resume", self.resume)
        self.cancel_button = button("Cancel", self.cancel_session, "Danger")
        self.retry_button = button("Retry failed step", self.retry)
        self.replan_button = button("Edit recovery plan", self.replan)
        self.undo_button = button("Undo selected", self.undo)
        for control in (self.pause_button, self.resume_button, self.cancel_button,
                        self.retry_button, self.undo_button):
            controls.addWidget(control)
        body.addLayout(controls)
        self.steps.currentItemChanged.connect(self.selection_changed)
        row = QHBoxLayout()
        row.addWidget(button("View details", self.details, "Quiet"))
        row.addWidget(button("View permissions", self.permissions, "Quiet"))
        row.addWidget(self.replan_button)
        row.addWidget(button("Dependency graph", self.toggle_graph, "Quiet"))
        row.addWidget(button("Send to background", self.handoff, "Quiet"))
        row.addStretch()
        body.addLayout(row)
        split.addWidget(detail)
        split.setSizes([280, 650])
        layout.addWidget(split, 1)
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

    def ask(self):
        self.hide()
        self.window.navigate("Chat")
        self.window.pages["Chat"].set_draft("Plan and carry out this task: ")

    def refresh(self):
        if self.window.closing or not hasattr(self.services, "operator"):
            return
        records = self.services.operator.list()
        revision = [(row.get("id"), row.get("updated_at"), row.get("status")) for row in records]
        if revision != self._revision:
            self._revision = revision
            selected = self.session_id
            self.sessions.blockSignals(True)
            self.sessions.clear()
            for row in records:
                item = QListWidgetItem(f"{row.get('goal', 'Operator task')}\n{row.get('status', 'ready')}")
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.sessions.addItem(item)
                if row["id"] == selected:
                    self.sessions.setCurrentItem(item)
            self.sessions.blockSignals(False)
            if not selected and records:
                self.session_id = records[0]["id"]
                self.sessions.setCurrentRow(0)
        self.render_session()

    def selected(self, item, _previous=None):
        if item:
            self.session_id = item.data(Qt.ItemDataRole.UserRole)
            self.render_session()

    def open_session(self, session_id):
        self.session_id = session_id
        self._revision = None
        self.refresh()

    def record(self):
        if self.session_id:
            try:
                return self.services.operator.get(self.session_id)
            except ValueError:
                self.session_id = None
        return None

    def render_session(self):
        row = self.record()
        selected = self.steps.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole).get("id") if selected else None
        self.steps.blockSignals(True)
        self.steps.clear()
        state = row.get("status", "ready") if row else "ready"
        if row:
            self.goal.setText(row.get("goal", "Operator task"))
            steps = row.get("steps", [])
            done = sum(step.get("status") in {"completed", "complete", "succeeded"} for step in steps)
            self.status.setText(f"{state.replace('_', ' ').capitalize()} · {done}/{len(steps)} steps completed")
            self.progress.setValue(int(row.get("progress_percent", 100 * done / max(1, len(steps)))))
            if self.graph_view.isVisible():
                self.render_graph(row)
            if row.get("partial_completion"):
                self.status.setText(self.status.text() + " · partial results retained")
            for index, step in enumerate(steps, 1):
                status = step.get("status", "pending")
                symbol = "✓" if status in {"completed", "complete", "succeeded"} else "!" if status == "failed" else "○"
                text = f"{symbol}  {index}. {step.get('title', step.get('tool', 'Action'))}\n     {status}"
                if status in {"completed", "complete", "succeeded"}:
                    text += " · verified" if step.get("verified") else " · outcome not independently verified"
                if step.get("error"):
                    text += " · " + str(step["error"])
                if step.get("recovery"):
                    text += "\n     " + str(step["recovery"])
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, step)
                self.steps.addItem(item)
                if step.get("id") == selected_id:
                    self.steps.setCurrentItem(item)
        self.steps.blockSignals(False)
        active = {"running", "planning", "awaiting_approval", "awaiting_confirmation"}
        self.pause_button.setEnabled(state in active)
        self.resume_button.setEnabled(state == "paused")
        self.cancel_button.setEnabled(state in active | {"paused"})
        self.retry_button.setEnabled(state in {"failed", "partial", "partial_failure", "cancelled", "timed_out", "interrupted"}
                                     and self.worker is None and bool(row.get("retry_available", True) if row else False)
                                     and not any(step.get("retry_safe") is False
                                                 for step in (row.get("steps", []) if row else [])
                                                 if step.get("status") not in {"complete", "completed", "succeeded"}))
        self.replan_button.setEnabled(state in {"failed", "partial", "cancelled", "timed_out", "interrupted"}
                                      and self.worker is None and not row.get("retry_session_id"))
        self.selection_changed()

    def toggle_graph(self):
        show = self.graph_view.isHidden()
        self.graph_view.setVisible(show)
        self.steps.setVisible(not show)
        if show and (row := self.record()):
            self.render_graph(row)

    def render_graph(self, row):
        from jarvix.capabilities.operator_graph import graph
        value = graph({"steps": row.get("steps", [])}, row.get("retained_step_ids", []))
        scene = QGraphicsScene(self.graph_view)
        positions, counts = {}, {}
        pen = QPen(QColor("#60869f"))
        for node in value["nodes"]:
            rank = node["rank"]
            offset = counts.get(rank, 0)
            counts[rank] = offset + 1
            positions[node["id"]] = (rank * 210, offset * 90)
        for edge in value["edges"]:
            if edge["from"] in positions and edge["to"] in positions:
                x1, y1 = positions[edge["from"]]
                x2, y2 = positions[edge["to"]]
                scene.addLine(x1 + 185, y1 + 28, x2, y2 + 28, pen)
                scene.addLine(x2 - 7, y2 + 23, x2, y2 + 28, pen)
                scene.addLine(x2 - 7, y2 + 33, x2, y2 + 28, pen)
        for node in value["nodes"]:
            x, y = positions[node["id"]]
            scene.addRect(x, y, 185, 57, pen, QBrush(QColor("#142231")))
            text = scene.addText(node["id"][:24] + "\n" + node["tool"][:24])
            text.setDefaultTextColor(QColor("#d7eaf6"))
            text.setPos(x + 5, y + 3)
            text.setToolTip(node["id"] + " · " + node["tool"])
        old = self.graph_view.scene()
        self.graph_view.setScene(scene)
        if old:
            old.deleteLater()

    def handoff(self):
        if self.session_id:
            self.window.open_capabilities("execution.handoff", {"id": self.session_id, "background": True})
            self.refresh()

    def selection_changed(self, *_):
        item = self.steps.currentItem()
        step = item.data(Qt.ItemDataRole.UserRole) if item else {}
        row = self.record()
        stopped = row and row.get("status") in {"complete", "completed", "partial", "partial_failure", "failed", "cancelled", "timed_out", "denied", "interrupted"}
        self.undo_button.setEnabled(bool(stopped and step.get("undo_id")) and not step.get("undone") and self.worker is None)

    def pause(self):
        if self.session_id:
            self.window.guard(lambda: self.services.operator.pause(self.session_id))
            self.refresh()

    def resume(self):
        if self.session_id:
            self.window.guard(lambda: self.services.operator.resume(self.session_id))
            self.refresh()

    def cancel_session(self):
        if self.session_id:
            self.window.guard(lambda: self.services.operator.cancel(self.session_id))
        if self.worker:
            self.worker.cancel.set()
        if self.pending_dialog:
            self.pending_dialog.reject()
        self.refresh()

    def retry(self):
        if self.session_id:
            session_id = self.session_id
            self.start(lambda **kwargs: self.services.operator.retry(session_id, **kwargs))

    def undo(self):
        item = self.steps.currentItem()
        if self.session_id and item:
            session_id, step_id = self.session_id, item.data(Qt.ItemDataRole.UserRole)["id"]
            self.start(lambda **kwargs: self.services.operator.undo(session_id, step_id, **kwargs))

    def start(self, work):
        if self.worker:
            return
        self.worker = ServiceJob(work, self.window)
        self.window.jobs.add(self.worker)
        self.worker.approval.connect(self.approve)
        self.worker.activity.connect(self.activity)
        self.worker.succeeded.connect(self.completed)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def activity(self, kind, data):
        if kind != "operator_session":
            return
        session_id = data.get("session_id") or data.get("id")
        if session_id:
            self.session_id = session_id
        self.refresh()

    def approve(self, bridge):
        try:
            if self.window.closing or not self.worker or self.worker.cancel.is_set():
                return
            self.pending_dialog = PermissionDialog(bridge.request, self)
            bridge.answer = self.pending_dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.pending_dialog = None
            bridge.ready.set()

    def completed(self, result):
        if isinstance(result, dict) and result.get("id"):
            self.session_id = result["id"]
        self.refresh()

    def worker_finished(self):
        worker, self.worker = self.worker, None
        self.window.release_job(worker)
        self.refresh()

    def replan(self):
        if self.session_id:
            self.edit_plan(recovery_id=self.session_id)

    def edit_plan(self, recovery_id=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit recovery plan" if recovery_id else "Run a structured operator plan")
        dialog.resize(710, 530)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Review the ordered tools and arguments before execution.", "Muted", True))
        editor = QPlainTextEdit()
        plan = {"goal": "Review today's tasks", "steps": [
            {"id": "tasks", "tool": "tasks.list", "arguments": {}}], "timeout_seconds": 120}
        if recovery_id:
            session = self.services.operator.get(recovery_id)
            retained = [step["id"] for step in session.get("steps", [])
                        if step.get("status") == "complete" and not step.get("undone")]
            layout.addWidget(label("Completed steps retained: " + (", ".join(retained) or "none")
                                   + ". Add only new steps; inspect uncertain targets before changing them.", "Muted", True))
            plan.update(goal=session["goal"], steps=[])
        editor.setPlainText(json.dumps(plan, indent=2))
        layout.addWidget(editor)
        error = label("", "Muted", True)
        layout.addWidget(error)

        def run():
            try:
                plan = json.loads(editor.toPlainText())
                if not isinstance(plan, dict):
                    raise ValueError("A plan must be a JSON object.")
                if recovery_id:
                    self.services.operator.preview_replan(recovery_id, plan)
                else:
                    self.services.operator.preview(plan)
            except (ValueError, TypeError) as exc:
                error.setText(str(exc))
                return
            dialog.accept()
            self.start(lambda **kwargs: self.services.operator.replan(recovery_id, plan, **kwargs)
                       if recovery_id else self.services.operator.run(plan, **kwargs))

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", dialog.reject))
        row.addWidget(button("Preview and run", run, "Primary"))
        layout.addLayout(row)
        dialog.exec()

    def details(self):
        row = self.record()
        if row:
            item = self.steps.currentItem()
            data = item.data(Qt.ItemDataRole.UserRole) if item else row
            TextPreview("Operator details", "Saved locally. Verification and errors are recorded per step.",
                        json.dumps(data, indent=2, ensure_ascii=False, default=str), self).exec()

    def permissions(self):
        row = self.record()
        if not row:
            return
        permissions = []
        for step in row.get("steps", []):
            spec = self.services.registry.get(step["tool"])
            permissions.append({"tool": spec.name, "permission": spec.permission,
                                "level": spec.permission_level or (1 if spec.risk == "read" else 2)})
        TextPreview("Session permissions", "Sensitive actions require fresh approval when executed.",
                    json.dumps(permissions, indent=2), self).exec()

    def inspect_context(self):
        self.window.open_capabilities("context.inspect")

    def shutdown(self):
        self.timer.stop()
        if self.worker:
            self.worker.cancel.set()
        if self.pending_dialog:
            self.pending_dialog.reject()
        self.hide()


class ControlHud(QDialog):
    def __init__(self, window):
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("ControlHud")
        self.setFixedWidth(490)
        body = QVBoxLayout(self)
        self.title = label("Jarvix is controlling this window", "Heading")
        body.addWidget(self.title)
        self.action = label("", "Muted", True)
        body.addWidget(self.action)
        row = QHBoxLayout()
        self.pause_button = button("Pause", self.pause)
        row.addWidget(self.pause_button)
        row.addWidget(button("Cancel operation", self.cancel, "Danger"))
        row.addStretch()
        row.addWidget(button("View session", window.open_operator, "Quiet"))
        body.addLayout(row)
        self.paused = False

    def update_state(self, data):
        if data.get("state") == "idle":
            self.hide()
            return
        self.action.setText(f"{data.get('action', 'Desktop action')} · Window {data.get('handle', 'active')}")
        self.paused = data.get("state") == "paused"
        self.pause_button.setText("Resume" if self.paused else "Pause")
        if not self.isVisible():
            self.adjustSize()
            screen = QApplication.primaryScreen()
            if screen:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.top() + 24)
            self.show()
        self.raise_()
        self.repaint()

    def pause(self):
        desktop = self.window.services.desktop
        desktop.resume() if self.paused else desktop.pause()
        self.paused = not self.paused
        self.pause_button.setText("Resume" if self.paused else "Pause")

    def cancel(self):
        self.window.services.desktop.cancel()
        self.window.pages["Chat"].cancel()
        if self.window.capability_dialog:
            self.window.capability_dialog.cancel()
        if self.window.operator_dialog:
            self.window.operator_dialog.cancel_session()
        self.action.setText("Stopping at the next safe point…")


class DesktopIndicator(QObject):
    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.hud = ControlHud(window)
        self.requested.connect(self.display)

    def __call__(self, data):
        bridge = {"data": data, "ready": threading.Event(), "allowed": False}
        if QThread.currentThread() == self.thread():
            self.display(bridge)
        else:
            self.requested.emit(bridge)
            if not bridge["ready"].wait(3):
                return False
        return bridge["allowed"]

    @Slot(object)
    def display(self, bridge):
        try:
            if self.window.closing:
                return
            self.hud.update_state(bridge["data"])
            bridge["allowed"] = (bridge["data"].get("state") == "idle" or
                                 (self.hud.isVisible() and QApplication.platformName() not in {"offscreen", "minimal"}))
        finally:
            bridge["ready"].set()
