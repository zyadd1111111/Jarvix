"""Compact local intelligence surfaces using the existing facade and permission forms."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget, QProgressBar,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu,
)

from .widgets import button, label
from .icons import icon


class AdaptiveDialog(QDialog):
    def __init__(self, window, tab="Search"):
        super().__init__(window)
        self.window, self.s = window, window.services
        self._closed = False
        self._read_versions = {}
        self.setWindowTitle("Jarvix · Work")
        self.resize(1040, 760)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.section_title = label("Search", "Title")
        header.addWidget(self.section_title)
        header.addStretch()
        self.section_selector = QComboBox(self)
        self.section_selector.setAccessibleName("Work and maintenance section")
        header.addWidget(self.section_selector)
        layout.addLayout(header)
        self.tabs = QTabWidget(self)
        self.tabs.tabBar().hide()
        layout.addWidget(self.tabs)
        self._search_tab()
        self._knowledge_tab()
        self._models_tab()
        self._plugins_tab()
        self._missions_tab()
        self._suggestions_tab()
        from .continuum import SavedWorkView, LocalOperationsView
        self.saved_work = {}
        for title, kind in (("Continue", "continuity"), ("Personal context", "context.profiles"), ("Skills", "skills")):
            view = SavedWorkView(self, kind)
            self.tabs.addTab(view, title)
            self.saved_work[title] = view
        for title, read, description, actions in (
            ("Daily brief", "daily.brief", "Off by default. Inspect approved sources; external account reads require an explicit foreground request.",
             (("Configure sources", "daily.configure", {}), ("Refresh connected sources", "daily.brief", {"include_external": True}))),
            ("Health", "diagnostics.health", "Database, runtime and saved connection health. Unknown or disconnected sources remain visible; nothing is reset.",
             (("Model health", "models.health", {}), ("Evaluation", "evaluation.report", {}))),
            ("Evaluation", "evaluation.report", "Measured saved outcomes distinguish verified tasks from tool returns. Routing never changes security rules.",
             (("Routing profile", "models.configure_profile", {}),)),
            ("Backups", "backup.list", "Consistent local snapshots exclude vault credentials. Restore opens an isolated profile; live data stays intact.",
             (("Create backup", "backup.create", {}), ("Schedule backup", "backup.schedule", {}), ("Verify snapshot", "backup.preview", {}),
              ("Restore snapshot", "backup.stage_restore", {}))),
            ("Devices", "devices.list", "Explicitly paired device identities and authenticated offline envelopes. This foundation has no remote-control listener.",
             (("Create identity", "devices.initialize", {}), ("Public identity", "devices.identity", {}),
              ("Pair device", "devices.pair", {}), ("Revoke device", "devices.revoke", {}), ("Review envelope", "devices.receive", {}))),
            ("Updates", "updates.status", "Manual release checks and local checksum verification. Executable code is never installed silently.",
             (("Trusted repository", "updates.configure", {}), ("Check releases", "updates.check", {}),
              ("Verify download", "updates.verify_artifact", {}))),
        ):
            view = LocalOperationsView(self, title, read, description, actions)
            self.tabs.addTab(view, title)
            self.saved_work[title] = view
        for index in range(self.tabs.count()):
            self.section_selector.addItem(self.tabs.tabText(index))
        self.section_selector.currentIndexChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.section_changed)
        self.tabs.currentChanged.connect(self.refresh)
        self.refresh()
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == tab:
                self.tabs.setCurrentIndex(index)

    def section_changed(self, index):
        self.section_title.setText(self.tabs.tabText(index))
        self.section_selector.blockSignals(True)
        self.section_selector.setCurrentIndex(index)
        self.section_selector.blockSignals(False)

    def page(self, title):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

    def tool(self, name, args=None):
        self.window.open_capabilities(name, args or {})

    def read_tool(self, name, args, done, failed):
        version = self._read_versions.get(name, 0) + 1
        self._read_versions[name] = version
        def work():
            if name not in self.s.enabled_tools():
                raise PermissionError("This source tool is disabled.")
            result = self.s.execute_tool(name, args)
            if not result.ok:
                raise ValueError(result.error or "This local read is unavailable.")
            return result.data
        def deliver(value):
            if not self._closed and self._read_versions.get(name) == version:
                done(value)
        def error(message):
            if not self._closed and self._read_versions.get(name) == version:
                failed(message)
        self.window.run_job(work, deliver, error)

    def showEvent(self, event):
        self._closed = False
        super().showEvent(event)

    def done(self, result):
        self._closed = True
        super().done(result)

    def _missions_tab(self):
        layout = self.page("Missions")
        row = QHBoxLayout()
        row.addWidget(button("New mission", lambda: self.tool("missions.save"), "Primary"))
        self.mission_filter = QComboBox(self)
        self.mission_filter.addItem("All missions", None)
        for status in ("active", "paused", "complete", "archived", "cancelled"):
            self.mission_filter.addItem(status.title(), status)
        self.mission_filter.currentIndexChanged.connect(self.refresh_missions)
        row.addWidget(self.mission_filter)
        row.addWidget(button("Refresh", self.refresh_missions, "Quiet"))
        row.addStretch()
        layout.addLayout(row)
        split = QSplitter(self)
        self.missions = QListWidget(self)
        self.missions.setAccessibleName("Saved Missions")
        self.missions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.missions.customContextMenuRequested.connect(self.mission_menu)
        self.missions.currentItemChanged.connect(self.select_mission)
        split.addWidget(self.missions)
        inspector = QWidget(self)
        content = QVBoxLayout(inspector)
        self.mission_goal = label("No Mission selected", "Heading", True)
        content.addWidget(self.mission_goal)
        self.mission_metadata = label("Create a Mission to track a larger goal across sessions.", "Muted", True)
        content.addWidget(self.mission_metadata)
        self.mission_progress = QProgressBar(self)
        self.mission_progress.setRange(0, 100)
        self.mission_progress.setTextVisible(False)
        self.mission_progress.setAccessibleName("Mission progress from linked state")
        content.addWidget(self.mission_progress)
        self.mission_sections = QTabWidget(self)
        content.addWidget(self.mission_sections, 1)
        self.mission_overview = QTreeWidget(self)
        self.mission_overview.setHeaderLabels(["Current work", "Status"])
        self.mission_overview.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mission_overview.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mission_overview.setRootIsDecorated(False)
        self.mission_sections.addTab(self.mission_overview, "Overview")
        self.mission_links = QTreeWidget(self)
        self.mission_links.setHeaderLabels(["Linked resource", "Type", "Status"])
        self.mission_links.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mission_links.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mission_links.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mission_links.setRootIsDecorated(False)
        self.mission_links.itemDoubleClicked.connect(self.inspect_mission_reference)
        self.mission_sections.addTab(self.mission_links, "Resources")
        self.mission_history = QTreeWidget(self)
        self.mission_history.setHeaderLabels(["Time", "Action"])
        self.mission_history.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.mission_history.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mission_history.setRootIsDecorated(False)
        self.mission_sections.addTab(self.mission_history, "History")
        self.mission_details = QPlainTextEdit(self)
        self.mission_details.setReadOnly(True)
        self.mission_sections.addTab(self.mission_details, "Details")
        split.addWidget(inspector)
        split.setSizes([250, 730])
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        self.mission_status = label("Select a mission to inspect its progress and blockers.", "Muted", True)
        layout.addWidget(self.mission_status)
        row = QHBoxLayout()
        self.mission_actions = {}
        for title, action in (("Edit Mission", "save"), ("Summarize", "summary"), ("Pause Mission", "pause"),
                              ("Resume Mission", "resume"), ("Archive", "archive"), ("Cancel Mission", "cancel")):
            control = button(title, lambda action=action: self.mission_tool("missions." + action),
                             "Danger" if action == "cancel" else "Quiet")
            control.setEnabled(False)
            self.mission_actions[action] = control
            if action in {"save", "pause", "resume"}:
                row.addWidget(control)
            else:
                control.setParent(self)
                control.hide()
        more = button("Mission actions", None, "Quiet")
        menu = QMenu(more)
        for action in ("summary", "archive", "cancel"):
            control = self.mission_actions[action]
            entry = menu.addAction(control.text())
            entry.triggered.connect(control.click)
            menu.aboutToShow.connect(lambda entry=entry, control=control: entry.setEnabled(control.isEnabled()))
        more.setMenu(menu)
        row.addWidget(more)
        self.continue_mission = button("Prepare next steps", lambda: self.mission_tool("intelligence.prepare"), "Primary")
        self.continue_mission.setEnabled(False)
        row.addWidget(self.continue_mission)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("Mission links", self.mission_graph, "Quiet"))
        row.addWidget(button("Continue a project", lambda: self.tool("intelligence.prepare"), "Quiet"))
        row.addWidget(button("Context graph", lambda: self.tool("context.graph"), "Quiet"))
        row.addStretch()
        layout.addLayout(row)

    def selected_mission(self):
        item = self.missions.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_missions(self, *_):
        selected = self.selected_mission()
        self.missions.blockSignals(True)
        self.missions.clear()
        self.missions.blockSignals(False)
        self.select_mission()
        args = {}
        if self.mission_filter.currentData():
            args["status"] = self.mission_filter.currentData()
        self.mission_status.setText("Reading saved mission metadata…")
        def done(value):
            self.missions.blockSignals(True)
            self.missions.clear()
            for mission in value.get("items", []):
                item = QListWidgetItem(f"{mission['goal']}\n{mission['status'].title()}")
                item.setData(Qt.ItemDataRole.UserRole, mission)
                self.missions.addItem(item)
                if selected and mission["id"] == selected["id"]:
                    self.missions.setCurrentItem(item)
            self.missions.blockSignals(False)
            self.mission_status.setText(f"{self.missions.count()} saved missions" if self.missions.count()
                                        else "No saved missions. Create a goal and link your existing work.")
            if self.missions.count() and not self.missions.currentItem():
                self.missions.setCurrentRow(0)
            else:
                self.select_mission()
        self.read_tool("missions.list", args, done, self.mission_status.setText)

    def select_mission(self, *_):
        selected = self.selected_mission()
        self.current_mission = None
        self.mission_details.clear()
        self.mission_overview.clear()
        self.mission_links.clear()
        self.mission_history.clear()
        self.mission_goal.setText("No Mission selected")
        self.mission_metadata.setText("Create a Mission to track a larger goal across sessions.")
        self.mission_progress.setValue(0)
        status = selected["status"] if selected else None
        allowed = {"save": status in {"active", "paused"}, "summary": bool(selected),
                   "pause": status == "active", "resume": status in {"paused", "complete", "archived"},
                   "archive": status in {"active", "paused", "complete"},
                   "cancel": bool(selected) and status != "cancelled"}
        for action, control in self.mission_actions.items():
            control.setEnabled(allowed[action])
        self.continue_mission.setEnabled(bool(selected and selected.get("project_id") and status in {"active", "paused"}))
        if not selected:
            return
        id = selected["id"]
        def done(mission):
            current = self.selected_mission()
            if not current or current["id"] != id:
                return
            self.current_mission = mission
            progress = mission["progress"]
            self.mission_goal.setText(mission["goal"])
            metadata = f"{mission['status'].title()} · {progress['completed']}/{progress['total']} linked items complete · {progress['percent']}%"
            if mission.get("deadline"):
                metadata += " · Due " + mission["deadline"]
            self.mission_metadata.setText(metadata)
            self.mission_progress.setValue(progress["percent"])
            for milestone in mission.get("milestones", []):
                item = QTreeWidgetItem([milestone["title"], milestone["status"].title()])
                item.setIcon(0, icon("check" if milestone["status"] == "complete" else "missions"))
                item.setToolTip(0, "Milestone" + (" · Due " + milestone["deadline"] if milestone.get("deadline") else ""))
                self.mission_overview.addTopLevelItem(item)
            milestone_titles = {item["title"].casefold() for item in mission.get("milestones", [])}
            for step in mission.get("next_step_details", []):
                if step.get("kind") == "milestone" and step["title"].casefold() in milestone_titles:
                    continue
                item = QTreeWidgetItem([step["title"], "Next step"])
                item.setToolTip(0, step.get("reason", ""))
                self.mission_overview.addTopLevelItem(item)
            for blocker in mission.get("current_blockers", []):
                item = QTreeWidgetItem([blocker, "Blocked"])
                item.setIcon(0, icon("alert"))
                self.mission_overview.addTopLevelItem(item)
            if mission.get("notes"):
                self.mission_overview.addTopLevelItem(QTreeWidgetItem([mission["notes"], "Notes"]))
            if not self.mission_overview.topLevelItemCount():
                self.mission_overview.addTopLevelItem(QTreeWidgetItem(["Add milestones or link tasks to define the next work.", ""]))
            for ref in mission.get("references", []):
                item = QTreeWidgetItem([ref.get("label", ref["reference"]), ref["kind"].replace("_", " ").title(),
                                       ref.get("status", "Available").title() if ref.get("available") else "Unavailable"])
                item.setToolTip(0, ref["reference"])
                item.setData(0, Qt.ItemDataRole.UserRole, ref)
                self.mission_links.addTopLevelItem(item)
            for event in reversed(mission.get("history", [])[-30:]):
                self.mission_history.addTopLevelItem(QTreeWidgetItem([event["at"], event["action"].replace("_", " ")]))
            lines = [mission["goal"], f"{mission['status'].title()} · {progress['completed']}/{progress['total']} linked tasks and sessions complete ({progress['percent']}%)"]
            if mission.get("current_blockers"):
                lines += ["", "Blockers", *["• " + text for text in mission["current_blockers"]]]
            if mission.get("notes"):
                lines += ["", "Notes", mission["notes"]]
            if mission.get("references"):
                lines += ["", "Linked work", *[f"{ref['kind']} · {ref.get('label', ref['reference'])}"
                           + (" · unavailable" if not ref.get("available") else "") for ref in mission["references"]]]
            if mission.get("history"):
                lines += ["", "Recent history", *[f"{event['at']} · {event['action']}" for event in mission["history"][-10:]]]
            self.mission_details.setPlainText("\n".join(lines))
        self.read_tool("missions.get", {"id": id}, done, self.mission_status.setText)

    def mission_tool(self, name):
        mission = self.selected_mission()
        if not mission:
            return
        if name == "intelligence.prepare":
            args = {"mission_id": mission["id"], "goal": ("Continue " + mission["goal"])[:500]}
        else:
            args = {"id": mission["id"]}
            if name == "missions.save":
                properties = self.s.registry.get(name).parameters["properties"]
                args = {key: value for key, value in (self.current_mission or mission).items()
                        if key in properties and value is not None}
        self.tool(name, args)

    def mission_graph(self):
        if mission := self.selected_mission():
            self.tool("context.graph", {"kind": "mission", "reference": mission["id"], "depth": 1})

    def inspect_mission_reference(self, item, _column=0):
        reference = item.data(0, Qt.ItemDataRole.UserRole)
        if reference:
            self.tool("context.graph", {"kind": reference["kind"], "reference": reference["reference"], "depth": 1})

    def mission_menu(self, position):
        item = self.missions.itemAt(position)
        if item is None:
            return
        self.missions.setCurrentItem(item)
        menu = QMenu(self.missions)
        menu.addAction("Open Mission", lambda: self.mission_sections.setCurrentIndex(0))
        for caption, action in (("Pause Mission", "pause"), ("Resume Mission", "resume"), ("Archive Mission", "archive")):
            entry = menu.addAction(caption, lambda action=action: self.mission_tool("missions." + action))
            entry.setEnabled(self.mission_actions[action].isEnabled())
        menu.addSeparator()
        menu.addAction("Inspect linked resources", self.mission_graph)
        menu.exec(self.missions.mapToGlobal(position))
        menu.deleteLater()

    def _suggestions_tab(self):
        layout = self.page("Suggestions")
        self.suggestion_status = label("Suggestions are off by default. They use permitted saved local records.", "Muted", True)
        layout.addWidget(self.suggestion_status)
        row = QHBoxLayout()
        row.addWidget(button("Configure suggestions", self.configure_suggestions))
        row.addWidget(button("Refresh", self.refresh_suggestions, "Quiet"))
        row.addStretch()
        layout.addLayout(row)
        split = QSplitter(self)
        self.suggestions = QListWidget(self)
        self.suggestions.currentItemChanged.connect(self.select_suggestion)
        split.addWidget(self.suggestions)
        self.suggestion_details = QPlainTextEdit(self)
        self.suggestion_details.setReadOnly(True)
        split.addWidget(self.suggestion_details)
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.review_suggestion = button("Review source", self.open_suggestion, "Primary")
        self.dismiss_suggestion = button("Dismiss", self.dismiss_selected_suggestion, "Quiet")
        self.mute_suggestion = button("Mute category", self.mute_selected_category, "Quiet")
        for control in (self.review_suggestion, self.dismiss_suggestion, self.mute_suggestion):
            control.setEnabled(False)
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("Review opens a local tool form. Suggestions never perform actions or share context automatically.", "Muted", True))

    def selected_suggestion(self):
        item = self.suggestions.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_suggestions(self):
        self.suggestions.clear()
        self.select_suggestion()
        self.suggestion_status.setText("Reading permitted local suggestions…")
        def done(value):
            self.suggestions.clear()
            self.suggestion_details.clear()
            for suggestion in value.get("items", []):
                item = QListWidgetItem(suggestion["title"] + "\n" + suggestion["category"].replace("_", " "))
                item.setData(Qt.ItemDataRole.UserRole, suggestion)
                self.suggestions.addItem(item)
            self.suggestion_status.setText((f"Suggestions on · {self.suggestions.count()} to review"
                                            if self.suggestions.count() else "Suggestions on · nothing needs attention")
                                           if value.get("enabled") else "Suggestions off · enable them explicitly in Configure suggestions")
            if self.suggestions.count():
                self.suggestions.setCurrentRow(0)
            self.select_suggestion()
        self.read_tool("suggestions.list", {}, done, self.suggestion_status.setText)

    def select_suggestion(self, *_):
        item = self.selected_suggestion()
        for control in (self.review_suggestion, self.dismiss_suggestion, self.mute_suggestion):
            control.setEnabled(bool(item))
        self.suggestion_details.setPlainText("" if not item else item["title"] + "\n\nWhy this appeared\n" + item["reason"]
                                             + "\n\nCategory: " + item["category"].replace("_", " "))

    def configure_suggestions(self):
        self.tool("suggestions.configure", {"enabled": self.s.settings.get("proactive.enabled", False),
                                             "muted": self.s.settings.get("proactive.muted", [])})

    def open_suggestion(self):
        if item := self.selected_suggestion():
            self.tool(item["action"], item["arguments"])

    def dismiss_selected_suggestion(self):
        if item := self.selected_suggestion():
            self.tool("suggestions.dismiss", {"id": item["id"]})

    def mute_selected_category(self):
        if item := self.selected_suggestion():
            muted = list(dict.fromkeys([*self.s.settings.get("proactive.muted", []), item["category"]]))
            self.tool("suggestions.configure", {"enabled": self.s.settings.get("proactive.enabled", False), "muted": muted})

    def _search_tab(self):
        layout = self.page("Search")
        row = QHBoxLayout()
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Find knowledge about OAuth, browser control, or a project…")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(button("Search", self.search, "Primary"))
        row.addWidget(button("Refresh index", lambda: self.tool("search.rebuild")))
        layout.addLayout(row)
        self.search_status = label("Rebuild explicitly to select the local sources to index.", "Muted", True)
        layout.addWidget(self.search_status)
        split = QSplitter(self)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Knowledge search results")
        self.results.currentItemChanged.connect(self.show_result)
        split.addWidget(self.results)
        self.evidence = QPlainTextEdit(self)
        self.evidence.setReadOnly(True)
        self.evidence.setAccessibleName("Selected result and citation")
        split.addWidget(self.evidence)
        layout.addWidget(split, 1)

    def search(self):
        text = self.query.text().strip()
        if not text:
            return
        self.search_status.setText("Searching permitted local sources…")
        def done(result):
            self.results.clear()
            self.evidence.clear()
            for row in result.get("items", []):
                item = QListWidgetItem(f"{row.get('title', row.get('kind', 'Source'))}\n{row.get('kind', 'knowledge')} · score {row.get('score', 0):.3f}")
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.results.addItem(item)
            self.search_status.setText(f"{result.get('method', result.get('mode', 'Local search'))} · {self.results.count()} results")
            if self.results.count():
                self.results.setCurrentRow(0)
        self.read_tool("search.query", {"query": text}, done, self.search_status.setText)

    def show_result(self, current, _previous=None):
        row = current.data(Qt.ItemDataRole.UserRole) if current else {}
        citation = row.get("citation", {})
        sources = [str(citation)] if not isinstance(citation, dict) else [
            key.replace("_", " ").title() + ": " + str(value) for key, value in citation.items() if value is not None]
        self.evidence.setPlainText(row.get("text", "") + ("\n\nSOURCE\n" + "\n".join(sources) if row else "Select a result to read its excerpt and source."))

    def _knowledge_tab(self):
        layout = self.page("Knowledge")
        row = QHBoxLayout()
        row.addWidget(button("Create space", lambda: self.tool("knowledge_spaces.create"), "Primary"))
        self.add_source_button = button("Add source", lambda: self.space_tool("knowledge_spaces.add_source"))
        self.ask_space_button = button("Ask a question", lambda: self.space_tool("knowledge_spaces.question"))
        row.addWidget(self.add_source_button)
        row.addWidget(self.ask_space_button)
        source_actions = self.source_actions_button = button("Source actions", None, "Quiet")
        source_menu = QMenu(source_actions)
        source_menu.addAction("Refresh source index", lambda: self.space_tool("knowledge_spaces.refresh"))
        source_menu.addAction("Summarize sources", lambda: self.space_tool("knowledge_spaces.summarize"))
        source_actions.setMenu(source_menu)
        row.addWidget(source_actions)
        row.addStretch()
        layout.addLayout(row)
        self.knowledge_status = label("No Knowledge Spaces yet. Create one to collect local documents, notes or repositories.", "Muted", True)
        layout.addWidget(self.knowledge_status)
        split = QSplitter(self)
        self.spaces = QListWidget(self)
        self.spaces.setAccessibleName("Knowledge Spaces")
        self.spaces.currentItemChanged.connect(self.select_space)
        split.addWidget(self.spaces)
        self.sources = QListWidget(self)
        self.sources.setAccessibleName("Knowledge source references")
        self.sources.currentItemChanged.connect(self.inspect_source)
        split.addWidget(self.sources)
        self.source_details = QPlainTextEdit(self)
        self.source_details.setReadOnly(True)
        self.source_details.setAccessibleName("Selected source metadata and index state")
        split.addWidget(self.source_details)
        split.setSizes([200, 280, 460])
        split.setStretchFactor(2, 2)
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.remove_source_button = button("Remove selected reference", self.remove_source, "Quiet")
        self.delete_space_button = button("Delete space", lambda: self.space_tool("knowledge_spaces.delete"), "Danger")
        row.addWidget(self.remove_source_button)
        row.addWidget(self.delete_space_button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("Original files remain local. Removing references does not delete sources. Drive entries contain explicitly imported metadata.", "Muted", True))

    def selected_space(self):
        item = self.spaces.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def space_tool(self, name):
        id = self.selected_space()
        if id:
            self.tool(name, {"id": id})

    def select_space(self, *_):
        self.sources.clear()
        self.source_details.setPlainText("Select a source to inspect its location and index state.")
        selected = bool(self.selected_space())
        for control in (self.add_source_button, self.ask_space_button, self.source_actions_button, self.delete_space_button):
            control.setEnabled(selected)
        self.remove_source_button.setEnabled(False)
        if id := self.selected_space():
            def loaded(value):
                if id != self.selected_space():
                    return
                for source in value.get("sources", []):
                    item = QListWidgetItem(f"{source.get('title') or source['reference']}\n{source['kind'].replace('_', ' ').title()}")
                    item.setIcon(icon("notes" if source["kind"] == "note" else "folder" if source["kind"] in {"folder", "repository"} else "file"))
                    item.setToolTip(source["reference"])
                    item.setData(Qt.ItemDataRole.UserRole, source["id"])
                    item.setData(Qt.ItemDataRole.UserRole + 1, {**source, "last_refresh": value.get("last_refresh")})
                    self.sources.addItem(item)
                if self.sources.count():
                    self.sources.setCurrentRow(0)
            def failed(_):
                self.sources.clear()
                self.source_details.clear()
            self.read_tool("knowledge_spaces.get", {"id": id}, loaded, failed)

    def inspect_source(self, current, _previous=None):
        source = current.data(Qt.ItemDataRole.UserRole + 1) if current else None
        if hasattr(self, "remove_source_button"):
            self.remove_source_button.setEnabled(bool(source))
        if not source:
            self.source_details.clear()
            return
        refreshed = source.get("last_refresh")
        lines = [source.get("title") or source["reference"], "", "Type: " + source["kind"].replace("_", " ").title(),
                 "Location: " + source["reference"], "", "INDEX"]
        if isinstance(refreshed, dict):
            lines += ["Last refreshed: " + str(refreshed.get("at", "Unknown")),
                      "Collection indexed: " + str(refreshed.get("indexed", 0)),
                      "Collection failures: " + str(refreshed.get("failed", 0))]
        else:
            lines += ["Refresh sources to build this collection's index."]
        if source["kind"] == "drive":
            lines += ["", "This reference contains imported metadata only."]
        self.source_details.setPlainText("\n".join(lines))

    def remove_source(self):
        source = self.sources.currentItem()
        if source and self.selected_space():
            self.tool("knowledge_spaces.remove_source", {"id": self.selected_space(),
                      "source_id": source.data(Qt.ItemDataRole.UserRole)})

    def _models_tab(self):
        layout = self.page("Models")
        layout.addWidget(label("Local endpoints stay on loopback. Discovery checks capabilities and never downloads models. Configure planning, chat, vision, coding, summaries, embeddings and document analysis roles.", "Muted", True))
        row = QHBoxLayout()
        self.provider = QComboBox(self)
        self.provider.addItems(["ollama", "local"])
        self.provider.currentTextChanged.connect(self.load_endpoint)
        row.addWidget(self.provider)
        self.endpoint = QLineEdit(self)
        self.endpoint.setPlaceholderText("http://127.0.0.1:11434")
        row.addWidget(self.endpoint, 1)
        row.addWidget(button("Save & discover", self.discover, "Primary"))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.models = QComboBox(self)
        self.models.setEditable(True)
        row.addWidget(self.models, 1)
        row.addWidget(button("Use for chat", self.use_model))
        row.addWidget(button("Configure task role", lambda: self.tool("models.configure_role")))
        row.addWidget(button("Routing cost", lambda: self.tool("models.configure_cost")))
        layout.addLayout(row)
        self.model_status = QPlainTextEdit(self)
        self.model_status.setReadOnly(True)
        self.model_status.setAccessibleName("Local endpoint health and reported model capabilities")
        self.model_status.setPlaceholderText("Discover installed models to inspect endpoint health and capabilities.")
        layout.addWidget(self.model_status, 1)
        self.load_endpoint()

    def load_endpoint(self, *_):
        from jarvix.providers import LOCAL_PROVIDER_URLS
        provider = self.provider.currentText()
        self.endpoint.setText(self.s.settings.get("endpoint." + provider, LOCAL_PROVIDER_URLS[provider]))
        self.models.clear()
        self.models.setCurrentText(self.s.settings.get("model." + provider, ""))

    def discover(self):
        from jarvix.providers.local import local_base_url
        provider = self.provider.currentText()
        try:
            endpoint = local_base_url(self.endpoint.text().strip())
            self.s.settings.set("endpoint." + provider, endpoint)
        except Exception as exc:
            self.model_status.setPlainText(str(exc))
            return
        self.model_status.setPlainText("Checking local endpoint…")
        def done(health):
            if self.provider.currentText() != provider or self.endpoint.text().strip() != endpoint:
                return
            self.models.clear()
            for model in health.get("models", []):
                self.models.addItem(model["id"])
            lines = ["Endpoint: " + endpoint, "Status: " + ("Available" if health.get("available") else "Unavailable"),
                     "", "INSTALLED MODELS"]
            for model in health.get("models", []):
                capabilities = model.get("capabilities", [])
                lines.append(model["id"] + " · " + (", ".join(capabilities) if capabilities else "Capabilities not reported"))
            if not health.get("models"):
                lines.append("No models discovered.")
            if health.get("error"):
                lines.extend(["", str(health["error"])])
            self.model_status.setPlainText("\n".join(lines))
        self.read_tool("models.discover", {"provider": provider}, done, self.model_status.setPlainText)

    def use_model(self):
        model = self.models.currentText().strip()
        if model:
            self.s.settings.set("model." + self.provider.currentText(), model)
            self.s.settings.set("provider", self.provider.currentText())
            self.window.pages["Chat"].reload_provider()
            self.window.update_status()
            self.window.notify("Local chat model configured; capabilities verified on each request.")

    def _plugins_tab(self):
        layout = self.page("Extensions")
        layout.addWidget(label(f"Manifest directory: {self.s.plugins.root}\nSDK v1 uses reviewed declarative host-tool contributions. No executable plugin code runs. Every action keeps its original permissions.", "Muted", True))
        self.plugins = QListWidget(self)
        layout.addWidget(self.plugins, 1)
        row = QHBoxLayout()
        row.addWidget(button("Import manifest", lambda: self.tool("plugins.install")))
        row.addWidget(button("Review manifest", lambda: self.plugin_tool("plugins.preview")))
        row.addWidget(button("Enable / disable", lambda: self.plugin_tool("plugins.enable")))
        row.addWidget(button("Open contributed panel", self.open_plugin_panel))
        row.addStretch()
        layout.addLayout(row)

    def plugin_tool(self, tool):
        item = self.plugins.currentItem()
        if item:
            row = item.data(Qt.ItemDataRole.UserRole)
            args = {"id": row["id"]}
            if tool == "plugins.enable" and row.get("fingerprint"):
                args.update(fingerprint=row["fingerprint"], enabled=not row["enabled"])
            self.tool(tool, args)

    def open_plugin_panel(self):
        item = self.plugins.currentItem()
        if item:
            id = item.data(Qt.ItemDataRole.UserRole)["id"]
            panels = [p for p in self.s.plugins.contributions("panels") if p["plugin_id"] == id]
            if panels:
                self.tool(panels[0]["target"])

    def refresh(self, *_):
        current_tab = self.tabs.tabText(self.tabs.currentIndex())
        if current_tab in self.saved_work:
            self.saved_work[current_tab].refresh()
        if current_tab == "Missions":
            self.refresh_missions()
        elif current_tab == "Suggestions":
            self.refresh_suggestions()
        if current_tab == "Knowledge":
            selected = self.selected_space()
            def loaded(value):
                self.spaces.blockSignals(True)
                self.spaces.clear()
                for row in value.get("items", []):
                    item = QListWidgetItem(f"{row['name']}\n{row.get('sources', 0)} sources")
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                    self.spaces.addItem(item)
                    if row["id"] == selected:
                        self.spaces.setCurrentItem(item)
                if self.spaces.count() and not self.spaces.currentItem():
                    self.spaces.setCurrentRow(0)
                self.spaces.blockSignals(False)
                self.knowledge_status.setText(f"{self.spaces.count()} Knowledge Spaces · select a collection to inspect its sources" if self.spaces.count()
                                              else "No Knowledge Spaces yet. Create one to collect local documents, notes or repositories.")
                self.select_space()
            def failed(message):
                self.spaces.clear()
                self.sources.clear()
                self.source_details.clear()
                self.knowledge_status.setText(message)
            self.read_tool("knowledge_spaces.list", {}, loaded, failed)
        elif current_tab == "Extensions":
            def loaded(rows):
                self.plugins.clear()
                for row in rows:
                    item = QListWidgetItem(f"{row.get('name', row['id'])} · {row.get('version', '')}\n{row['status']}")
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    self.plugins.addItem(item)
            self.read_tool("plugins.list", {}, loaded, lambda _: self.plugins.clear())
