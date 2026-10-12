"""Execution and research workspaces; the original controllers keep every action boundary."""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt, QSize, QTimer, QRect
from PySide6.QtGui import QColor, QPen, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QListWidget, QTreeWidget,
    QTreeWidgetItem, QListWidgetItem, QProgressBar, QGraphicsView, QPlainTextEdit,
    QSplitter, QTabWidget, QHeaderView, QAbstractItemView, QStyledItemDelegate, QStyle, QWidget,
)

from ..adaptive import AdaptiveDialog
from ..continuum import SavedWorkView, LocalOperationsView
from ..operator import OperatorDialog
from ..widgets import label
from ..liquid.components import LiquidPanel, LiquidToolbar
from ..liquid.motion import owner, disclose, reveal, enabled, Transition
from ..liquid.theme import ink
from .design import TOKENS, NexusButton, NexusMenu, NexusInput, icon, pane


def tree(headers, parent=None):
    control = QTreeWidget(parent)
    control.setHeaderLabels(headers)
    control.setRootIsDecorated(False)
    control.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    control.setUniformRowHeights(True)
    control.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for index in range(1, len(headers)):
        control.header().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    control.setAccessibleName(" · ".join(headers))
    return control


def readout(parent, name):
    control = QPlainTextEdit(parent)
    control.setReadOnly(True)
    control.setAccessibleName(name)
    return control


def action_title(step):
    if step.get("title"):
        return str(step["title"])
    tool = str(step.get("tool") or "Action")
    if "." not in tool:
        return tool
    target, action = tool.rsplit(".", 1)
    return action.replace("_", " ").capitalize() + " · " + target.replace("_", " ").replace(".", " ")


class SourceFields(QWidget):
    """Stacked provenance stays legible when an inspector is narrow."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)

    def clear(self):
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def set_fields(self, fields):
        self.clear()
        for caption, text in fields:
            field = pane(self)
            field.body.setSpacing(4)
            field.body.addWidget(label(caption, "Muted"))
            value = label(str(text), wrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(caption)
            if caption == "Location":
                value.setFont(QFont(TOKENS["mono_family"], TOKENS["font_size"] - 1))
            field.body.addWidget(value)
            self.body.addWidget(field)
        self.body.addStretch()


class SkillInspector(QWidget):
    """A capability sheet instead of the old general-purpose record tree."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.title = label("Select a Skill", "NexusSection", True)
        self.metadata = label("", "Muted", True)
        self.body.addWidget(self.title)
        self.body.addWidget(self.metadata)
        self.properties = SourceFields(self)
        self.body.addWidget(self.properties, 1)
        self.raw = readout(self, "Exact reviewed Skill recipe")
        self.raw.hide()
        self.body.addWidget(self.raw, 1)
        self.toggle = NexusButton("Show recipe details", self.toggle_details, "Quiet", self)
        self.toggle.setEnabled(False)
        self.body.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

    def clear(self):
        self.title.setText("Select a Skill")
        self.metadata.clear()
        self.properties.clear()
        self.raw.clear()
        self.toggle.setEnabled(False)

    def set_record(self, value, _kind=None):
        self.clear()
        self.title.setText(value.get("name", "Skill"))
        self.metadata.setText(("Enabled" if value.get("enabled") else "Disabled") +
                              " · Version " + str(value.get("version", 1)))
        fields = [("Purpose", value.get("description") or "A reviewed local routine.")]
        if value.get("instructions"):
            fields.append(("Instructions", value["instructions"]))
        permissions = value.get("required_permissions", [])
        requirements = []
        for entry in permissions:
            if isinstance(entry, dict):
                level = entry.get("level") or (1 if entry.get("risk") == "read" else 2)
                requirements.append(entry["tool"] + " · Level " + str(level))
            else:
                requirements.append(entry)
        fields.append(("Required tools", "\n".join(requirements) or "None"))
        fields.append(("Integrations", "\n".join(value.get("required_integrations", [])) or "None"))
        usage = value.get("usage", {})
        fields.append(("Recorded runs", str(usage.get("recorded_runs", 0))))
        self.properties.set_fields(fields)
        self.raw.setPlainText(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        self.toggle.setEnabled(True)

    def toggle_details(self):
        shown = self.raw.isHidden()
        self.raw.setVisible(shown)
        self.properties.setVisible(not shown)
        self.toggle.setText("Hide recipe details" if shown else "Show recipe details")
        disclose(self)


class ExecutionRail(QStyledItemDelegate):
    """Connected, compact events rather than a list of chatbot bubbles."""
    def sizeHint(self, option, index):
        return QSize(0, TOKENS["row_height"] + 24)

    def paint(self, painter, option, index):
        preferences = getattr(owner(self.parent()), "_liquid_preferences", {})
        step = index.data(Qt.ItemDataRole.UserRole) or {}
        status = step.get("status", "pending")
        completed = status in {"complete", "completed", "succeeded"}
        active = status == "running"
        paused = active and bool(self.parent().property("operatorPaused"))
        failed = status in {"blocked", "failed", "denied"}
        painter.save()
        if active or option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect.adjusted(0, 2, 0, -2), QColor(ink(preferences, "selected")))
        center = option.rect.top() + 19
        painter.setPen(QPen(QColor(ink(preferences, "border")), 1))
        painter.drawLine(option.rect.left() + 19, option.rect.top(), option.rect.left() + 19, option.rect.bottom())
        glyph = "check" if completed else "alert" if failed else "pause" if paused else "play" if active else "operator"
        role = "success" if completed else "error" if failed else "accent" if active else "muted"
        painter.fillRect(QRect(option.rect.left() + 8, center - 10, 22, 22), QColor(ink(preferences, "surface")))
        icon(glyph, preferences, role).paint(painter, option.rect.left() + 10, center - 8, 18, 18)
        title = action_title(step)
        text_rect = option.rect.adjusted(40, 7, -12, -28)
        painter.setPen(QColor(ink(preferences, "text")))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()))
        detail = "Paused at a checkpoint" if paused else status.replace("_", " ").capitalize()
        if completed:
            detail += " · verified" if step.get("verified") else " · verification not recorded"
        elif step.get("error"):
            detail += " · " + str(step["error"])
        dependencies = step.get("depends_on") or []
        if dependencies:
            detail += " · after " + ", ".join(dependencies)
        painter.setPen(QColor(ink(preferences, "muted")))
        text_rect = option.rect.adjusted(40, 31, -12, -6)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(detail, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()


class NexusOperatorWorkspace(OperatorDialog):
    """A blank run console using OperatorDialog's original execution methods."""
    def __init__(self, window):
        QDialog.__init__(self, window)
        self.window, self.services = window, window.services
        self.worker = self.pending_dialog = self.session_id = self._revision = None
        self._closed = False
        self.setObjectName("NexusWorkspace")
        self.setWindowTitle("Jarvix · Operator")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(TOKENS["page_inset"], 16, TOKENS["page_inset"], 16)
        layout.setSpacing(16)
        header = pane(self, horizontal=True)
        header.body.addWidget(label("OPERATOR", "Muted"))
        self.session_picker = QComboBox(self)
        self.session_picker.setAccessibleName("Choose an Operator run")
        self.session_picker.setMaximumWidth(TOKENS["content_width"] // 2)
        self.session_picker.currentIndexChanged.connect(self.choose_run)
        header.body.addWidget(self.session_picker, 1)
        header.body.addStretch()
        header.body.addWidget(NexusButton("Plan a task", self.ask, "Primary", self))
        options = NexusButton("Run options", parent=self)
        menu = NexusMenu("Run options", options)
        for caption, callback in (("Enter structured plan", self.edit_plan),
                                  ("Inspect current context", self.inspect_context),
                                  ("Save progress", self.save_progress),
                                  ("Create Skill from verified run", self.learn_session),
                                  ("Send to background", self.handoff)):
            menu.addAction(caption, callback)
        options.setMenu(menu)
        header.body.addWidget(options)
        layout.addWidget(header)
        self.goal = label("No run selected", "NexusTitle", True)
        layout.addWidget(self.goal)
        metadata = pane(self, horizontal=True)
        self.status = label("Plan a task to start an execution timeline.", "Muted", True)
        self.elapsed = label("", "Muted")
        metadata.body.addWidget(self.status, 1)
        metadata.body.addWidget(self.elapsed)
        layout.addWidget(metadata)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(4)
        self.progress.setAccessibleName("Verified run progress")
        layout.addWidget(self.progress)
        self.run_split = QSplitter(self)
        execution = pane(self)
        self.current_action = label("No action running", "NexusSection", True)
        execution.body.addWidget(self.current_action)
        self.steps = QListWidget(self)
        self.steps.setAccessibleName("Operator execution timeline")
        self.steps.setItemDelegate(ExecutionRail(self.steps))
        self.steps.currentItemChanged.connect(self.selection_changed)
        self.steps.itemDoubleClicked.connect(lambda _item: self.show_inspector())
        execution.body.addWidget(self.steps, 1)
        self.graph_view = QGraphicsView(self)
        self.graph_view.setAccessibleName("Run dependency graph")
        self.graph_view.hide()
        execution.body.addWidget(self.graph_view, 1)
        self.run_split.addWidget(execution)
        self.inspector = LiquidPanel(parent=self)
        self.inspector.setMinimumWidth(0)
        self.inspector.setProperty("liquidExpandedSize", TOKENS["inspector_width"])
        inspector_header = QHBoxLayout()
        inspector_header.addWidget(label("Run details", "NexusSection"), 1)
        inspector_header.addWidget(NexusButton("Close", lambda: reveal(self.inspector, False), "Quiet", self))
        self.inspector.body.addLayout(inspector_header)
        self.inspector_tabs = QTabWidget(self)
        self.inspector_tabs.tabBar().hide()
        self.inspector_tabs.currentChanged.connect(lambda index: disclose(self.inspector_tabs.widget(index)))
        self.inspector_section = QComboBox(self)
        self.inspector_section.setAccessibleName("Run detail section")
        self.inspector_section.currentIndexChanged.connect(self.inspector_tabs.setCurrentIndex)
        self.inspector.body.addWidget(self.inspector_section)
        self.files_view = tree(["Resource", "Location"], self)
        self.step_inspector = tree(["Property", "Value"], self)
        self.permissions_view = tree(["Tool", "Permission", "Level"], self)
        self.output_view = readout(self, "Selected action output")
        self.changes_view = readout(self, "Recorded reversible changes")
        for caption, control in (("Tools", self.step_inspector), ("Files", self.files_view),
                                 ("Permissions", self.permissions_view), ("Outputs", self.output_view),
                                 ("Changes", self.changes_view)):
            self.inspector_tabs.addTab(control, caption)
            self.inspector_section.addItem(caption)
        self.inspector.body.addWidget(self.inspector_tabs, 1)
        self.run_split.addWidget(self.inspector)
        self.run_split.setSizes([850, TOKENS["inspector_width"]])
        self.run_split.setStretchFactor(0, 1)
        self.inspector.hide()
        layout.addWidget(self.run_split, 1)
        controls = LiquidToolbar(self)
        actions = QHBoxLayout()
        self.pause_button = NexusButton("Pause", self.pause, parent=self)
        self.cancel_button = NexusButton("Stop", self.cancel_session, "Danger", self)
        self.resume_button = NexusButton("Resume", self.resume, parent=self)
        for control, glyph in ((self.pause_button, "pause"), (self.cancel_button, "square"), (self.resume_button, "play")):
            control.setIcon(icon(glyph))
            actions.addWidget(control)
        actions.addStretch()
        self.graph_button = NexusButton("Dependencies", self.toggle_graph, "Quiet", self)
        self.details_button = NexusButton("Inspect action", self.show_inspector, "Quiet", self)
        actions.addWidget(self.graph_button)
        actions.addWidget(self.details_button)
        self.recovery_button = NexusButton("Recovery", parent=self)
        self.recovery_menu = NexusMenu("Recovery", self.recovery_button)
        self.recovery_actions = []
        self.retry_button = NexusButton("Retry failed step", self.retry, parent=self)
        self.replan_button = NexusButton("Edit recovery plan", self.replan, parent=self)
        self.undo_button = NexusButton("Undo selected action", self.undo, parent=self)
        for control in (self.retry_button, self.replan_button, self.undo_button):
            control.hide()
            action = self.recovery_menu.addAction(control.text(), control.click)
            self.recovery_actions.append((action, control))
        self.recovery_menu.aboutToShow.connect(self.update_recovery_menu)
        self.recovery_button.setMenu(self.recovery_menu)
        actions.addWidget(self.recovery_button)
        controls.body.addLayout(actions)
        layout.addWidget(controls)
        # These controller targets remain alive without exposing the OLD action rows.
        self.permissions_button = NexusButton("View permissions", self.permissions, parent=self)
        self.handoff_button = NexusButton("Send to background", self.handoff, parent=self)
        self.save_progress_button = NexusButton("Save progress", self.save_progress, parent=self)
        self.learn_button = NexusButton("Create Skill", self.learn_session, parent=self)
        self.sessions = QListWidget(self)
        self.sessions.currentItemChanged.connect(self.selected)
        for hidden in (self.permissions_button, self.handoff_button, self.save_progress_button, self.learn_button, self.sessions):
            hidden.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def choose_run(self, index):
        if id := self.session_picker.itemData(index):
            self.open_session(id)

    def showEvent(self, event):
        self._closed = False
        super().showEvent(event)

    def refresh(self):
        if self._closed:
            return
        super().refresh()
        current = self.session_id
        records = [self.sessions.item(index) for index in range(self.sessions.count())]
        self.session_picker.blockSignals(True)
        self.session_picker.clear()
        for item in records:
            self.session_picker.addItem(item.text().split("\n", 1)[0], item.data(Qt.ItemDataRole.UserRole))
        index = self.session_picker.findData(current)
        if index >= 0:
            self.session_picker.setCurrentIndex(index)
        self.session_picker.blockSignals(False)

    def render_session(self):
        row = self.record() or {}
        signature = (row.get("status"), tuple((step.get("id"), step.get("status"), step.get("verified"))
                                             for step in row.get("steps", [])))
        previous = getattr(self, "_timeline_signature", None)
        viewport = self.steps.viewport()
        snapshot = viewport.grab() if previous is not None and previous != signature and viewport.isVisible() and enabled(self) else None
        super().render_session()
        self._timeline_signature = signature
        if snapshot is not None:
            for old in viewport.findChildren(Transition, options=Qt.FindChildOption.FindDirectChildrenOnly):
                old.hide()
                old.deleteLater()
            Transition(viewport, snapshot)
        if not hasattr(self, "elapsed"):
            return
        row = self.record() or {}
        steps = row.get("steps", [])
        running = next((step for step in steps if step.get("status") == "running"), None)
        state = row.get("status")
        self.steps.setProperty("operatorPaused", state == "paused")
        if state == "paused":
            action = "Paused · " + action_title(running) if running else "Paused at a checkpoint"
        elif state == "cancelled":
            action = "Execution stopped"
        elif state in {"failed", "partial", "partial_failure", "timed_out", "denied", "interrupted"}:
            action = "Review the outcome before continuing"
        elif state in {"complete", "completed"}:
            action = "Execution finished"
        else:
            action = "Now · " + action_title(running) if running else "Waiting for the next action" if steps else "Plan a task to see its actions here"
        self.current_action.setText(action)
        starts = [step["started_at"] for step in steps if isinstance(step.get("started_at"), (int, float))]
        ends = [step["started_at"] + max(0, float(step.get("elapsed_seconds", 0))) for step in steps
                if isinstance(step.get("started_at"), (int, float))]
        if running:
            ends.append(time.time())
        elapsed = max(0, max(ends) - min(starts)) if starts and ends else None
        self.elapsed.setText(f"{elapsed:.1f}s elapsed" if elapsed is not None else "")
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            step = item.data(Qt.ItemDataRole.UserRole)
            display_state = "paused at a checkpoint" if state == "paused" and step.get("status") == "running" else step.get("status", "pending").replace("_", " ")
            item.setText(action_title(step) + " · " + display_state)
        self.steps.viewport().update()

    def selection_changed(self, *_):
        super().selection_changed()
        if not hasattr(self, "permissions_view"):
            return
        selected = self.steps.currentItem()
        step = selected.data(Qt.ItemDataRole.UserRole) if selected else {}
        self.files_view.clear()
        self.permissions_view.clear()
        if step.get("tool"):
            try:
                spec = self.services.registry.get(step["tool"])
            except ValueError:
                self.permissions_view.addTopLevelItem(QTreeWidgetItem([step["tool"], "Tool unavailable", ""]))
            else:
                level = spec.permission_level or (1 if spec.risk == "read" else 2)
                self.permissions_view.addTopLevelItem(QTreeWidgetItem([spec.name, spec.permission, str(level)]))
        for group in (step.get("arguments", {}), step.get("result", {})):
            if isinstance(group, dict):
                for key in ("path", "folder", "working_directory", "destination", "source"):
                    if isinstance(group.get(key), str):
                        self.files_view.addTopLevelItem(QTreeWidgetItem([key.replace("_", " ").title(), group[key]]))
        if not self.files_view.topLevelItemCount():
            self.files_view.addTopLevelItem(QTreeWidgetItem(["No file reference", "This action recorded no file path."]))
        result = step.get("result")
        self.output_view.setPlainText(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:32000]
                                     if result is not None else str(step.get("error") or "No output recorded for this action."))
        self.changes_view.setPlainText(("Undo reference: " + str(step["undo_id"]) +
                                      ("\nAlready undone." if step.get("undone") else "\nUndo is available through Recovery after the run stops."))
                                     if step.get("undo_id") else "No reversible change recorded for this action.")

    def show_inspector(self):
        reveal(self.inspector, True)
        self.selection_changed()

    def update_recovery_menu(self):
        for action, control in self.recovery_actions:
            action.setEnabled(control.isEnabled())

    def refresh_materials(self):
        preferences = getattr(owner(self), "_liquid_preferences", {})
        for control, glyph in ((self.pause_button, "pause"), (self.cancel_button, "square"), (self.resume_button, "play")):
            control.setIcon(icon(glyph, preferences))
        self.steps.viewport().update()

    def closeEvent(self, event):
        self._closed = True
        self.shutdown()
        super().closeEvent(event)


class SkillRows(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(0, TOKENS["row_height"] + 28)

    def paint(self, painter, option, index):
        value = index.data(Qt.ItemDataRole.UserRole) or {}
        preferences = getattr(owner(self.parent()), "_liquid_preferences", {})
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect.adjusted(0, 2, 0, -2), QColor(ink(preferences, "selected")))
        icon("skills", preferences).paint(painter, option.rect.left() + 12, option.rect.top() + 16, 20, 20)
        rect = option.rect.adjusted(44, 7, -100, -31)
        painter.setPen(QColor(ink(preferences, "text")))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(value.get("name", "Skill"), Qt.TextElideMode.ElideRight, rect.width()))
        painter.setPen(QColor(ink(preferences, "muted")))
        rect = option.rect.adjusted(44, 32, -100, -7)
        description = value.get("description") or " · ".join(
            entry.get("tool", "") if isinstance(entry, dict) else str(entry)
            for entry in value.get("required_permissions", [])) or "Reviewed local recipe"
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(description, Qt.TextElideMode.ElideRight, rect.width()))
        painter.drawText(option.rect.adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "Enabled" if value.get("enabled", True) else "Disabled")
        painter.restore()


class NexusSkillLibrary(SavedWorkView):
    def __init__(self, dialog):
        # Only SavedWorkView's reads/actions are inherited; its original split layout is not built.
        QWidget.__init__(self, dialog)
        self.dialog, self.kind = dialog, "skills"
        self._rows, self._selection = [], None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = pane(self, horizontal=True)
        self.query = NexusInput(parent=self)
        self.query.setPlaceholderText("Find a capability…")
        self.query.setAccessibleName("Search installed Skills")
        self.query.textChanged.connect(self.filter_rows)
        header.body.addWidget(self.query, 1)
        header.body.addWidget(NexusButton("Create Skill", lambda: self.dialog.tool("skills.learn_preview"), "Primary", self))
        layout.addWidget(header)
        self.split = QSplitter(self)
        self.items = QListWidget(self)
        self.items.setAccessibleName("Installed Skills")
        self.items.setItemDelegate(SkillRows(self.items))
        self.items.currentItemChanged.connect(self.inspect)
        self.split.addWidget(self.items)
        detail = self.detail_panel = LiquidPanel(parent=self)
        self.inspector = SkillInspector(self)
        self.details = self.inspector.raw
        detail.body.addWidget(self.inspector, 1)
        self.split.addWidget(detail)
        self.split.setSizes([700, TOKENS["inspector_width"]])
        self.split.setStretchFactor(0, 1)
        layout.addWidget(self.split, 1)
        self.status = label("No Skills yet. Save a reviewed routine to reuse it here.", "Muted", True)
        layout.addWidget(self.status)
        controls = pane(self, horizontal=True)
        self.controls = []
        for caption, action in (("Run Skill", "run"), ("Edit recipe", "learn_preview"),
                                ("Enable / disable", "set_enabled"), ("Test Skill", "preview")):
            control = NexusButton(caption, lambda action=action: self.action(action),
                                  "Primary" if action == "run" else "Quiet", self)
            control.setEnabled(False)
            self.controls.append(control)
            controls.body.addWidget(control)
        controls.body.addStretch()
        layout.addWidget(controls)

    def inspect(self, *_):
        super().inspect()
        self.detail_panel.setVisible(bool(self.selected()))

    def refresh_materials(self):
        self.items.viewport().update()


class NexusAdaptiveWorkspace(AdaptiveDialog):
    """Separate research/mission/Skill compositions, backed by AdaptiveDialog handlers."""
    def __init__(self, window, tab="Knowledge"):
        QDialog.__init__(self, window)
        self.window, self.s = window, window.services
        self._closed, self._read_versions = False, {}
        self.saved_work = {}
        self.setObjectName("NexusWorkspace")
        self.setWindowTitle("Jarvix · " + tab)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(TOKENS["page_inset"], 16, TOKENS["page_inset"], 16)
        layout.setSpacing(16)
        self.section_title = label(tab, "NexusTitle")
        self.section_selector = QComboBox(self)
        self.section_selector.hide()
        self.tabs = QTabWidget(self)
        self.tabs.tabBar().hide()
        layout.addWidget(self.tabs, 1)
        if tab == "Missions":
            self._build_missions()
        elif tab == "Knowledge":
            self._build_knowledge()
        elif tab == "Skills":
            page = pane(self)
            view = NexusSkillLibrary(self)
            page.body.addWidget(label("Skills", "NexusTitle"))
            page.body.addWidget(view, 1)
            self.tabs.addTab(page, "Skills")
            self.saved_work["Skills"] = view
        else:
            self._build_maintenance(tab)
        for index in range(self.tabs.count()):
            self.section_selector.addItem(self.tabs.tabText(index))
        self.section_selector.currentIndexChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.section_changed)
        self.tabs.currentChanged.connect(self.refresh)
        self.refresh()

    def read_tool(self, name, args, done, failed):
        def loaded(value):
            done(value)
            if name == "missions.get" and getattr(self, "current_mission", None) is value:
                self.present_mission(value)
            elif name == "missions.list" and hasattr(self, "mission_picker"):
                self.sync_mission_picker()
            elif name == "knowledge_spaces.get" and hasattr(self, "sources"):
                self.refresh_materials()
            elif name == "skills.get" and "Skills" in self.saved_work:
                view = self.saved_work["Skills"]
                selected = view.items.currentItem()
                if selected and selected.data(Qt.ItemDataRole.UserRole)["id"] == value.get("id"):
                    selected.setData(Qt.ItemDataRole.UserRole, value)
                    view.controls[0].setEnabled(bool(value.get("enabled")))
                    view.controls[2].setText("Disable Skill" if value.get("enabled") else "Enable Skill")
                    view.items.viewport().update()
        super().read_tool(name, args, loaded, failed)

    def _build_missions(self):
        layout = self.page("Missions")
        layout.setContentsMargins(0, 0, 0, 0)
        header = pane(self, horizontal=True)
        self.mission_picker = QComboBox(self)
        self.mission_picker.setAccessibleName("Choose a Mission")
        self.mission_picker.setMaximumWidth(TOKENS["content_width"] // 2)
        self.mission_picker.currentIndexChanged.connect(self.choose_mission)
        header.body.addWidget(self.mission_picker, 1)
        self.mission_filter = QComboBox(self)
        self.mission_filter.addItem("All Missions", None)
        for state in ("active", "paused", "complete", "archived", "cancelled"):
            self.mission_filter.addItem(state.title(), state)
        self.mission_filter.currentIndexChanged.connect(self.refresh_missions)
        header.body.addWidget(self.mission_filter)
        header.body.addStretch()
        header.body.addWidget(NexusButton("New Mission", lambda: self.tool("missions.save"), "Primary", self))
        layout.addWidget(header)
        self.missions = QListWidget(self)
        self.missions.currentItemChanged.connect(self.select_mission)
        self.missions.hide()
        self.mission_goal = label("Choose a Mission", "NexusTitle", True)
        self.mission_metadata = label("Track a larger goal across conversations and work sessions.", "Muted", True)
        layout.addWidget(self.mission_goal)
        layout.addWidget(self.mission_metadata)
        self.mission_progress = QProgressBar(self)
        self.mission_progress.setRange(0, 100)
        self.mission_progress.setTextVisible(False)
        self.mission_progress.setMaximumHeight(5)
        self.mission_progress.setAccessibleName("Mission progress from linked tasks and sessions")
        layout.addWidget(self.mission_progress)
        self.mission_sections = QTabWidget(self)
        self.mission_sections.currentChanged.connect(lambda index: disclose(self.mission_sections.widget(index)))
        overview = pane(self)
        current = LiquidToolbar(self)
        self.mission_objective = label("No next step recorded.", "NexusSection", True)
        self.mission_blockers = label("", "Muted", True)
        current.body.addWidget(self.mission_objective)
        current.body.addWidget(self.mission_blockers)
        overview.body.addWidget(current)
        self.mission_overview = tree(["Current work", "State"], self)
        self.mission_overview.hide()
        work = pane(self, horizontal=True)
        objective = pane(self)
        objective.body.addWidget(label("UP NEXT", "Muted"))
        self.mission_next_work = tree(["Work", "State"], self)
        objective.body.addWidget(self.mission_next_work, 1)
        resources = pane(self)
        resources.body.addWidget(label("LINKED WORK", "Muted"))
        self.mission_resource_summary = tree(["Resource", "Type"], self)
        self.mission_resource_summary.itemDoubleClicked.connect(self.inspect_mission_reference)
        resources.body.addWidget(self.mission_resource_summary, 1)
        work.body.addWidget(objective, 1)
        work.body.addWidget(resources, 1)
        overview.body.addWidget(work, 1)
        self.mission_sections.addTab(overview, "Overview")
        self.mission_milestones = tree(["Milestone", "State", "Deadline"], self)
        self.mission_sections.addTab(self.mission_milestones, "Milestones")
        self.mission_tasks = tree(["Task", "State"], self)
        self.mission_tasks.itemDoubleClicked.connect(self.inspect_mission_reference)
        self.mission_sections.addTab(self.mission_tasks, "Tasks")
        self.mission_links = tree(["Resource", "Type", "State"], self)
        self.mission_links.itemDoubleClicked.connect(self.inspect_mission_reference)
        self.mission_sections.addTab(self.mission_links, "Resources")
        self.mission_history = tree(["Time", "Action"], self)
        self.mission_sections.addTab(self.mission_history, "History")
        self.mission_details = readout(self, "Exact Mission metadata")
        self.mission_details.hide()
        layout.addWidget(self.mission_sections, 1)
        self.mission_status = label("Choose a Mission to inspect saved work.", "Muted", True)
        layout.addWidget(self.mission_status)
        self.mission_actions = {}
        actions = pane(self, horizontal=True)
        for caption, action in (("Edit Mission", "save"), ("Pause", "pause"), ("Resume", "resume"),
                                ("Archive", "archive"), ("Cancel Mission", "cancel"), ("Summarize", "summary")):
            control = NexusButton(caption, lambda action=action: self.mission_tool("missions." + action), "Quiet", self)
            control.setEnabled(False)
            self.mission_actions[action] = control
            if action in {"save", "pause", "resume"}:
                actions.body.addWidget(control)
            else:
                control.hide()
        menu_button = NexusButton("More", parent=self)
        menu = NexusMenu("Mission actions", menu_button)
        for action in ("summary", "archive", "cancel"):
            control = self.mission_actions[action]
            entry = menu.addAction(control.text(), control.click)
            menu.aboutToShow.connect(lambda entry=entry, control=control: entry.setEnabled(control.isEnabled()))
        menu.addAction("Inspect relationships", self.mission_graph)
        menu_button.setMenu(menu)
        actions.body.addWidget(menu_button)
        actions.body.addStretch()
        self.continue_mission = NexusButton("Prepare next steps", lambda: self.mission_tool("intelligence.prepare"), "Primary", self)
        self.continue_mission.setEnabled(False)
        actions.body.addWidget(self.continue_mission)
        layout.addWidget(actions)

    def sync_mission_picker(self):
        self.mission_picker.blockSignals(True)
        self.mission_picker.clear()
        selected = self.selected_mission()
        for index in range(self.missions.count()):
            value = self.missions.item(index).data(Qt.ItemDataRole.UserRole)
            self.mission_picker.addItem(value["goal"], value["id"])
        if selected:
            self.mission_picker.setCurrentIndex(self.mission_picker.findData(selected["id"]))
        self.mission_picker.blockSignals(False)

    def choose_mission(self, index):
        id = self.mission_picker.itemData(index)
        for row in range(self.missions.count()):
            if self.missions.item(row).data(Qt.ItemDataRole.UserRole)["id"] == id:
                self.missions.setCurrentRow(row)
                break

    def select_mission(self, *_):
        super().select_mission()
        if hasattr(self, "mission_milestones"):
            self.mission_milestones.clear()
            self.mission_tasks.clear()
            self.mission_objective.setText("No next step recorded.")
            self.mission_blockers.clear()
            self.mission_next_work.clear()
            self.mission_resource_summary.clear()

    def present_mission(self, value):
        self.mission_milestones.clear()
        self.mission_tasks.clear()
        for milestone in value.get("milestones", []):
            self.mission_milestones.addTopLevelItem(QTreeWidgetItem([
                milestone["title"], milestone["status"].title(), milestone.get("deadline") or "Not set"]))
        for reference in value.get("references", []):
            if reference["kind"] == "task":
                row = QTreeWidgetItem([reference.get("label", reference["reference"]),
                                      reference.get("status", "Unavailable").title()])
                row.setData(0, Qt.ItemDataRole.UserRole, reference)
                self.mission_tasks.addTopLevelItem(row)
        next_steps = value.get("next_step_details", [])
        self.mission_objective.setText("Next · " + next_steps[0]["title"] if next_steps else
                                      "No unfinished action recorded.")
        blockers = value.get("current_blockers", [])
        self.mission_blockers.setText("Needs attention · " + " · ".join(blockers) if blockers else "No blockers recorded")
        self.mission_next_work.clear()
        listed = set()
        for step in next_steps:
            self.mission_next_work.addTopLevelItem(QTreeWidgetItem([step["title"], "Next step"]))
            listed.add(step["title"].casefold())
        for milestone in value.get("milestones", []):
            if milestone["status"] != "complete" and milestone["title"].casefold() not in listed:
                self.mission_next_work.addTopLevelItem(QTreeWidgetItem([milestone["title"], milestone["status"].title()]))
        self.mission_resource_summary.clear()
        for reference in value.get("references", [])[:30]:
            row = QTreeWidgetItem([reference.get("label", reference["reference"]), reference["kind"].replace("_", " ").title()])
            row.setData(0, Qt.ItemDataRole.UserRole, reference)
            self.mission_resource_summary.addTopLevelItem(row)

    def _build_knowledge(self):
        layout = self.page("Knowledge")
        layout.setContentsMargins(0, 0, 0, 0)
        header = pane(self, horizontal=True)
        header.body.addWidget(label("Knowledge", "NexusTitle"), 1)
        header.body.addWidget(NexusButton("Create space", lambda: self.tool("knowledge_spaces.create"), "Primary", self))
        self.add_source_button = NexusButton("Add source", lambda: self.space_tool("knowledge_spaces.add_source"), parent=self)
        header.body.addWidget(self.add_source_button)
        self.source_actions_button = NexusButton("Source actions", parent=self)
        source_menu = NexusMenu("Source actions", self.source_actions_button)
        source_menu.addAction("Refresh source index", lambda: self.space_tool("knowledge_spaces.refresh"))
        source_menu.addAction("Summarize sources", lambda: self.space_tool("knowledge_spaces.summarize"))
        self.remove_source_button = NexusButton("Remove reference", self.remove_source, parent=self)
        self.delete_space_button = NexusButton("Delete space", lambda: self.space_tool("knowledge_spaces.delete"), "Danger", self)
        for control in (self.remove_source_button, self.delete_space_button):
            control.hide()
            action = source_menu.addAction(control.text(), control.click)
            source_menu.aboutToShow.connect(lambda action=action, control=control: action.setEnabled(control.isEnabled()))
        self.source_actions_button.setMenu(source_menu)
        header.body.addWidget(self.source_actions_button)
        self.citation_button = NexusButton("Source details", self.toggle_citations, "Quiet", self)
        self.citation_button.setEnabled(False)
        header.body.addWidget(self.citation_button)
        layout.addWidget(header)
        self.knowledge_status = label("Select sources to research locally.", "Muted", True)
        layout.addWidget(self.knowledge_status)
        self.knowledge_split = QSplitter(self)
        library = pane(self)
        self.source_library = library
        library.setMaximumWidth(TOKENS["library_width"] + 60)
        library.body.addWidget(label("SPACES", "Muted"))
        self.spaces = QListWidget(self)
        self.spaces.setAccessibleName("Knowledge Spaces")
        self.spaces.currentItemChanged.connect(self.select_space)
        library.body.addWidget(self.spaces, 1)
        library.body.addWidget(label("SOURCES", "Muted"))
        self.sources = QListWidget(self)
        self.sources.setWordWrap(True)
        self.sources.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sources.setAccessibleName("Sources in the selected Space")
        self.sources.currentItemChanged.connect(self.inspect_source)
        library.body.addWidget(self.sources, 2)
        self.knowledge_split.addWidget(library)
        document = pane(self)
        search = pane(self, horizontal=True)
        self.space_query = NexusInput(parent=self)
        self.space_query.setPlaceholderText("Search this space…")
        self.space_query.setAccessibleName("Search selected Knowledge Space")
        self.space_query.returnPressed.connect(self.search_space)
        search.body.addWidget(self.space_query, 1)
        search.body.addWidget(NexusButton("Search", self.search_space, "Primary", self))
        self.ask_space_button = NexusButton("Ask", lambda: self.space_tool("knowledge_spaces.question"), parent=self)
        search.body.addWidget(self.ask_space_button)
        document.body.addWidget(search)
        self.research_tabs = QTabWidget(self)
        self.research_tabs.currentChanged.connect(lambda index: disclose(self.research_tabs.widget(index)))
        self.document_title = label("Select a source", "NexusSection", True)
        excerpts = pane(self)
        excerpts.body.addWidget(self.document_title)
        self.document_view = readout(self, "Local source excerpts")
        self.document_view.setPlaceholderText("Read indexed excerpts to inspect this collection. Source contents are never uploaded automatically.")
        excerpts.body.addWidget(self.document_view, 1)
        excerpts.body.addWidget(NexusButton("Read indexed excerpts", self.read_excerpts, "Quiet", self))
        self.research_tabs.addTab(excerpts, "Document")
        results = pane(self)
        self.space_results = QListWidget(self)
        self.space_results.setAccessibleName("Cited local search results")
        self.space_results.currentItemChanged.connect(self.inspect_search_result)
        results.body.addWidget(self.space_results, 1)
        self.research_tabs.addTab(results, "Results")
        document.body.addWidget(self.research_tabs, 1)
        self.knowledge_split.addWidget(document)
        self.citation_panel = LiquidPanel(parent=self)
        self.citation_panel.setMinimumWidth(0)
        source_header = pane(self, horizontal=True)
        source_header.body.addWidget(label("Source", "NexusSection"), 1)
        source_header.body.addWidget(NexusButton("Close", self.toggle_citations, "Quiet", self))
        self.citation_panel.body.addWidget(source_header)
        self.source_properties = tree(["Field", "Value"], self)
        self.source_properties.setHeaderHidden(True)
        self.source_properties.hide()
        self.citation_fields = SourceFields(self)
        self.citation_panel.body.addWidget(self.citation_fields, 1)
        self.source_details = readout(self, "Exact source metadata and index state")
        self.source_details.hide()
        self.citation_panel.body.addWidget(label("Only permitted local evidence is read. Original sources remain in place.", "Muted", True))
        self.knowledge_split.addWidget(self.citation_panel)
        self.knowledge_split.setSizes([TOKENS["library_width"], 600, TOKENS["inspector_width"]])
        self.knowledge_split.setStretchFactor(1, 1)
        layout.addWidget(self.knowledge_split, 1)
        self.citation_panel.hide()

    def select_space(self, *_):
        super().select_space()
        self.space_results.clear()
        self.document_view.clear()
        self.document_title.setText("Select a source")
        self.source_properties.clear()
        self.citation_fields.clear()

    def inspect_source(self, current, _previous=None):
        super().inspect_source(current, _previous)
        if not hasattr(self, "source_properties"):
            return
        self.source_properties.clear()
        self.citation_fields.clear()
        source = current.data(Qt.ItemDataRole.UserRole + 1) if current else {}
        if not source:
            self.document_title.setText("Select a source")
            self.citation_button.setEnabled(False)
            self.citation_panel.hide()
            return
        self.citation_button.setEnabled(True)
        self.citation_panel.setVisible(self.width() >= TOKENS["workspace_breakpoint"])
        self.document_view.clear()
        self.document_title.setText(source.get("title") or source["reference"])
        refreshed = source.get("last_refresh") or {}
        fields = [("Title", source.get("title") or source["reference"]),
                  ("Type", source["kind"].replace("_", " ").title()),
                  ("Location", source["reference"]),
                  ("Collection refreshed", refreshed.get("at", "Not indexed"))]
        for caption, value in fields:
            row = QTreeWidgetItem([caption, str(value)])
            row.setToolTip(1, str(value))
            self.source_properties.addTopLevelItem(row)
        self.citation_fields.set_fields(fields)

    def toggle_citations(self):
        shown = self.citation_panel.isHidden()
        disclose(self.knowledge_split)
        if self.width() < TOKENS["workspace_breakpoint"]:
            self.source_library.setVisible(not shown)
        reveal(self.citation_panel, shown)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "citation_panel"):
            compact = self.width() < TOKENS["workspace_breakpoint"]
            self.source_library.setVisible(True)
            self.citation_panel.setVisible(not compact and self.sources.currentItem() is not None)

    def search_space(self):
        id, query = self.selected_space(), self.space_query.text().strip()
        if not id or not query:
            return
        self.knowledge_status.setText("Searching permitted local evidence…")
        def loaded(value):
            if self.selected_space() != id:
                return
            self.space_results.clear()
            for row in value.get("items", []):
                item = QListWidgetItem(row.get("title", "Source") + "\n" + row.get("text", "")[:150])
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.space_results.addItem(item)
            self.research_tabs.setCurrentIndex(1)
            self.knowledge_status.setText(f"{self.space_results.count()} local results" +
                                          (" · refresh this collection's index" if value.get("needs_refresh") else ""))
            if self.space_results.count():
                self.space_results.setCurrentRow(0)
        self.read_tool("knowledge_spaces.search", {"id": id, "query": query, "limit": 20}, loaded,
                       self.knowledge_status.setText)

    def read_excerpts(self):
        id = self.selected_space()
        if not id:
            return
        def loaded(value):
            if self.selected_space() != id:
                return
            self.document_title.setText("Indexed excerpts")
            self.document_view.setPlainText(value.get("text", "") or "No indexed excerpts. Refresh this collection first.")
            self.research_tabs.setCurrentIndex(0)
            self.knowledge_status.setText("Local indexed excerpts · original sources stay in place")
        def failed(message):
            if self.selected_space() == id:
                self.document_view.setPlainText("Could not read excerpts.\n" + message)
                self.knowledge_status.setText(message)
        self.read_tool("knowledge_spaces.summarize", {"id": id, "limit": 8}, loaded, failed)

    def inspect_search_result(self, current, _previous=None):
        value = current.data(Qt.ItemDataRole.UserRole) if current else {}
        if not value:
            return
        self.document_title.setText(value.get("title", "Source"))
        self.document_view.setPlainText(value.get("text", ""))
        citation = value.get("citation") or {}
        self.source_properties.clear()
        fields = [(str(key).replace("_", " ").title(), str(item))
                  for key, item in (citation.items() if isinstance(citation, dict) else [("Source", citation)])]
        for key, item in fields:
            row = QTreeWidgetItem([str(key).replace("_", " ").title(), str(item)])
            row.setToolTip(1, str(item))
            self.source_properties.addTopLevelItem(row)
        self.citation_fields.set_fields(fields)

    def _build_maintenance(self, tab):
        # Secondary maintenance tools retain their existing functional forms.
        builders = {"Search": self._search_tab, "Models": self._models_tab,
                    "Extensions": self._plugins_tab, "Suggestions": self._suggestions_tab}
        if tab in builders:
            builders[tab]()
            return
        if tab in {"Continue", "Personal context"}:
            kind = "continuity" if tab == "Continue" else "context.profiles"
            view = SavedWorkView(self, kind)
        else:
            definitions = {
                "Daily brief": ("daily.brief", (("Configure sources", "daily.configure", {}),)),
                "Health": ("diagnostics.health", (("Model health", "models.health", {}),)),
                "Evaluation": ("evaluation.report", (("Routing profile", "models.configure_profile", {}),)),
                "Backups": ("backup.list", (("Create backup", "backup.create", {}), ("Restore snapshot", "backup.stage_restore", {}))),
                "Devices": ("devices.list", (("Pair device", "devices.pair", {}), ("Revoke device", "devices.revoke", {}))),
                "Updates": ("updates.status", (("Check releases", "updates.check", {}), ("Verify download", "updates.verify_artifact", {}))),
            }
            read, actions = definitions.get(tab, definitions["Health"])
            view = LocalOperationsView(self, tab, read, "Inspect saved local state before making changes.", actions)
        self.tabs.addTab(view, tab)
        self.saved_work[tab] = view

    def refresh_materials(self):
        preferences = getattr(owner(self), "_liquid_preferences", {})
        if hasattr(self, "sources"):
            for index in range(self.sources.count()):
                item = self.sources.item(index)
                source = item.data(Qt.ItemDataRole.UserRole + 1)
                name = "notes" if source["kind"] == "note" else "folder" if source["kind"] in {"folder", "repository"} else "file"
                item.setIcon(icon(name, preferences))
        if "Skills" in self.saved_work:
            self.saved_work["Skills"].refresh_materials()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        disclose(self)

    def closeEvent(self, event):
        self._closed = True
        super().closeEvent(event)
