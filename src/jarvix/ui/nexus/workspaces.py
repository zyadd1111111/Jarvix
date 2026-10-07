"""Nexus workspace composition over the existing permissioned controllers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QPlainTextEdit, QLineEdit, QListView, QComboBox,
)

from ..adaptive import AdaptiveDialog
from ..operator import OperatorDialog
from ..widgets import label, button
from .materials import GlassPanel, GlassToolbar
from .icons import icon


def glass_pane(splitter, index, title):
    """Keep the original widget, signals and ownership while framing its pane."""
    pane = GlassPanel(parent=splitter.parentWidget())
    pane.body.addWidget(label(title, "SectionTitle"))
    content = splitter.replaceWidget(index, pane)
    content.setMinimumWidth(0)
    pane.body.addWidget(content, 1)
    pane.setMinimumWidth(0)
    return pane


def drain_layout(layout, owner):
    """Transfer presentation widgets; retire detached Qt layouts on the GUI thread."""
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.hide()
            widget.setParent(owner)
        elif child := item.layout():
            child.setParent(layout)
            drain_layout(child, owner)
            child.deleteLater()


class NexusOperatorDialog(OperatorDialog):
    """One live console, with session history and a selected-step inspector."""

    def __init__(self, window):
        super().__init__(window)
        toolbar_controls = {control.text(): control for control in self.findChildren(QPushButton)}
        layout = self.layout()
        drain_layout(layout, self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        toolbar = GlassToolbar(parent=self)
        row = QHBoxLayout()
        for text in ("Plan a task", "Enter a structured plan"):
            control = toolbar_controls[text]
            control.show()
            row.addWidget(control)
        row.addStretch()
        self.inspector_toggle = button("Step inspector", self.toggle_inspector, "Quiet")
        row.addWidget(self.inspector_toggle)
        control = toolbar_controls["Inspect current context"]
        control.show()
        row.addWidget(control)
        toolbar.body.addLayout(row)
        layout.addWidget(toolbar)
        self.execution_split = QSplitter(self)
        history = self.session_panel = GlassPanel(parent=self.execution_split)
        history.body.addWidget(label("Sessions", "SectionTitle"))
        history.body.addWidget(self.sessions, 1)
        self.sessions.show()
        self.execution_split.addWidget(history)
        console = GlassPanel(parent=self.execution_split)
        self.goal.setObjectName("NexusGoal")
        for widget in (self.goal, self.status, self.progress):
            console.body.addWidget(widget)
            widget.show()
        controls = QHBoxLayout()
        for control in (self.pause_button, self.resume_button, self.cancel_button):
            controls.addWidget(control)
            control.show()
        for control, text in ((self.pause_button, "Pause"), (self.resume_button, "Resume"), (self.cancel_button, "Stop")):
            control.setText(text)
        controls.addStretch()
        console.body.addLayout(controls)
        console.body.addWidget(self.steps, 1)
        self.steps.show()
        console.body.addWidget(self.graph_view, 1)
        row = QHBoxLayout()
        for control in (self.graph_button, self.handoff_button):
            row.addWidget(control)
            control.show()
        row.addStretch()
        console.body.addLayout(row)
        self.execution_split.addWidget(console)
        self.inspector_panel = GlassPanel(parent=self.execution_split)
        self.inspector_panel.body.addWidget(label("Selected step", "SectionTitle"))
        self.step_inspector.setMaximumHeight(16777215)
        self.step_inspector.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.step_inspector.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.inspector_panel.body.addWidget(self.step_inspector, 1)
        self.step_inspector.show()
        for control in (self.details_button, self.permissions_button, self.recovery_button,
                        self.save_progress_button, self.learn_button):
            self.inspector_panel.body.addWidget(control)
            control.show()
        self.execution_split.addWidget(self.inspector_panel)
        self.execution_split.setSizes([165, 560, 250])
        self.execution_split.setStretchFactor(1, 3)
        layout.addWidget(self.execution_split, 1)
        for listing in (self.steps, self.sessions):
            listing.setWordWrap(True)
            listing.setResizeMode(QListView.ResizeMode.Adjust)
            listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "inspector_panel"):
            self.inspector_panel.setVisible(self.width() >= 900)
            self.session_panel.show()

    def toggle_inspector(self):
        visible = self.inspector_panel.isHidden()
        self.inspector_panel.setVisible(visible)
        if self.width() < 900:
            self.session_panel.setVisible(not visible)

    def render_session(self):
        super().render_session()
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            step = item.data(Qt.ItemDataRole.UserRole)
            status = step.get("status", "pending")
            item.setIcon(icon("check" if status in {"completed", "complete", "succeeded"}
                              else "alert" if status in {"failed", "blocked"}
                              else "play" if status == "running" else "operator"))


class NexusAdaptiveDialog(AdaptiveDialog):
    """Research, Mission and Skill panes retain their existing local controllers."""

    def __init__(self, window, tab="Search"):
        super().__init__(window, tab)
        self.setWindowTitle("Jarvix · Nexus workspace")
        self.layout().setContentsMargins(12, 12, 12, 12)
        for title, labels in (("Missions", ("Missions", "Mission workspace")),
                              ("Knowledge", ("Spaces", "Sources", "Content")),
                              ("Search", ("Results", "Document excerpt"))):
            page = self.tab_page(title)
            split = page.findChild(QSplitter)
            for index, caption in enumerate(labels):
                glass_pane(split, index, caption)
            if title == "Knowledge":
                self.knowledge_split = split
                self.citation_panel = GlassPanel(parent=split)
                self.citation_panel.body.addWidget(label("Source inspector", "SectionTitle"))
                self.citation_inspector = QTreeWidget(self.citation_panel)
                self.citation_inspector.setHeaderLabels(["Property", "Value"])
                self.citation_inspector.setRootIsDecorated(False)
                self.citation_inspector.setAccessibleName("Knowledge citation and source identity")
                self.citation_inspector.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
                self.citation_inspector.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                self.citation_panel.body.addWidget(self.citation_inspector, 1)
                self.source_details.setParent(self.citation_panel)
                self.source_details.hide()
                self.content_view = QPlainTextEdit(split.widget(2))
                self.content_view.setReadOnly(True)
                self.content_view.setAccessibleName("Explicitly requested local Knowledge Space excerpts")
                self.content_view.setObjectName("KnowledgeContent")
                self.content_view.setPlainText("Read indexed excerpts, or search this space. Source contents are read only when requested.")
                self.knowledge_query = QLineEdit(split.widget(2))
                self.knowledge_query.setPlaceholderText("Search this space…")
                self.knowledge_query.setAccessibleName("Search selected Knowledge Space")
                self.knowledge_query.returnPressed.connect(self.read_space_content)
                content = split.widget(2).body
                content.addWidget(self.knowledge_query)
                content.addWidget(button("Read indexed excerpts", self.read_space_content, "Quiet"))
                content.addWidget(self.content_view, 1)
                split.addWidget(self.citation_panel)
                split.setSizes([150, 230, 340, 220])
                self.inspect_source(self.sources.currentItem())
            else:
                split.setSizes([240, 700])
        for title, view in self.saved_work.items():
            if split := view.findChild(QSplitter):
                glass_pane(split, 0, "Skills" if title == "Skills" else "Saved entries")
                glass_pane(split, 1, "Capability details" if title == "Skills" else "Details")
        for listing in (self.spaces, self.sources, self.results, self.missions):
            listing.setWordWrap(True)
            listing.setResizeMode(QListView.ResizeMode.Adjust)
            listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.citation_toggle = button("Source inspector", self.toggle_citations, "Quiet")
        knowledge_toolbar = self.tab_page("Knowledge").layout().itemAt(0).layout()
        knowledge_toolbar.insertWidget(knowledge_toolbar.count() - 1, self.citation_toggle)
        self.space_selector = QComboBox(self)
        self.space_selector.setAccessibleName("Knowledge Space")
        self.space_selector.setMaximumWidth(250)
        self.space_selector.currentIndexChanged.connect(self.spaces.setCurrentRow)
        knowledge_layout = self.tab_page("Knowledge").layout()
        status_index = knowledge_layout.indexOf(self.knowledge_status)
        knowledge_layout.removeWidget(self.knowledge_status)
        status_row = QHBoxLayout()
        status_row.addWidget(self.space_selector)
        status_row.addWidget(self.knowledge_status, 1)
        knowledge_layout.insertLayout(status_index, status_row)
        self.sync_space_selector()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "citation_panel"):
            self.citation_panel.setVisible(self.width() >= 900)
            self.knowledge_split.widget(0).setVisible(self.width() >= 700)
            self.knowledge_split.widget(1).show()
            self.space_selector.setVisible(self.width() < 700)

    def toggle_citations(self):
        visible = self.citation_panel.isHidden()
        self.citation_panel.setVisible(visible)
        if self.width() < 900:
            self.knowledge_split.widget(0).hide()
            self.knowledge_split.widget(1).setVisible(not visible)

    def tab_page(self, title):
        return next(self.tabs.widget(index) for index in range(self.tabs.count())
                    if self.tabs.tabText(index) == title)

    def inspect_source(self, current, _previous=None):
        super().inspect_source(current, _previous)
        if current:
            source = current.data(Qt.ItemDataRole.UserRole + 1)
            current.setIcon(icon("notes" if source["kind"] == "note" else
                                 "folder" if source["kind"] in {"folder", "repository"} else "file"))
        if not hasattr(self, "citation_inspector"):
            return
        self.citation_inspector.clear()
        source = current.data(Qt.ItemDataRole.UserRole + 1) if current else None
        if source:
            for caption, key in (("Source", "title"), ("Type", "kind"), ("Location", "reference"),
                                 ("Index refreshed", "last_refresh")):
                value = source.get(key)
                if key == "last_refresh":
                    value = value.get("at", "Not indexed") if isinstance(value, dict) else "Not indexed"
                elif key == "kind":
                    value = str(value).replace("_", " ").title()
                value = str(value or "Not recorded")
                item = QTreeWidgetItem([caption, value])
                item.setToolTip(1, value)
                self.citation_inspector.addTopLevelItem(item)

    def select_space(self, *_):
        if hasattr(self, "citation_inspector"):
            self._content_version = getattr(self, "_content_version", 0) + 1
            self.citation_inspector.clear()
            self.content_view.setPlainText("Read indexed excerpts, or search this space. Source contents are read only when requested.")
            self.knowledge_query.clear()
        super().select_space()
        if hasattr(self, "space_selector"):
            self.sync_space_selector()

    def sync_space_selector(self):
        self.space_selector.blockSignals(True)
        self.space_selector.clear()
        for index in range(self.spaces.count()):
            item = self.spaces.item(index)
            self.space_selector.addItem(item.text().split("\n", 1)[0], item.data(Qt.ItemDataRole.UserRole))
        self.space_selector.setCurrentIndex(self.spaces.currentRow())
        self.space_selector.setEnabled(bool(self.spaces.count()))
        self.space_selector.blockSignals(False)

    def read_space_content(self):
        space_id = self.selected_space()
        if not space_id:
            return
        query = self.knowledge_query.text().strip()
        self._content_version = getattr(self, "_content_version", 0) + 1
        version = self._content_version
        tool = "knowledge_spaces.search" if query else "knowledge_spaces.summarize"
        args = {"id": space_id, "query": query, "limit": 8} if query else {"id": space_id, "limit": 8}
        self.content_view.setPlainText("Reading permitted indexed excerpts…")
        def done(value):
            if space_id != self.selected_space() or version != self._content_version:
                return
            parts = []
            for index, item in enumerate(value.get("items", []), 1):
                citation = item.get("citation", {})
                location = citation.get("path") or citation.get("reference") or citation.get("source_id") or item.get("source_id", "Local index")
                parts.append(f"[{index}] {item.get('title', 'Source')}\n{item.get('text', '')}\nSource: {location}")
            self.content_view.setPlainText("\n\n".join(parts) or "No indexed excerpts. Use Source actions to refresh this space.")
        def failed(message):
            if space_id == self.selected_space() and version == self._content_version:
                self.content_view.setPlainText("Could not read excerpts: " + message)
        self.read_tool(tool, args, done, failed)

    def read_tool(self, name, args, done, failed):
        def deliver(value):
            done(value)
            if name == "missions.get":
                for index in range(self.mission_overview.topLevelItemCount()):
                    item = self.mission_overview.topLevelItem(index)
                    item.setIcon(0, icon("check" if item.text(1).casefold() == "complete" else
                                         "alert" if item.text(1).casefold() == "blocked" else "missions"))
            elif name == "knowledge_spaces.get":
                for index in range(self.sources.count()):
                    item = self.sources.item(index)
                    source = item.data(Qt.ItemDataRole.UserRole + 1)
                    item.setIcon(icon("notes" if source["kind"] == "note" else
                                      "folder" if source["kind"] in {"folder", "repository"} else "file"))
            elif name == "knowledge_spaces.list" and hasattr(self, "space_selector"):
                self.sync_space_selector()
        super().read_tool(name, args, deliver, failed)
