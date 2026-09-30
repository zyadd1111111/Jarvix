"""Compact local intelligence surfaces using the existing facade and permission forms."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .widgets import button, label


class AdaptiveDialog(QDialog):
    def __init__(self, window, tab="Search"):
        super().__init__(window)
        self.window, self.s = window, window.services
        self.setWindowTitle("Jarvix · Knowledge & local intelligence")
        self.resize(1040, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(label("ADAPTIVE", "Eyebrow"))
        layout.addWidget(label("Your knowledge. Your models. Your control.", "Heading"))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._search_tab()
        self._knowledge_tab()
        self._models_tab()
        self._plugins_tab()
        self.tabs.currentChanged.connect(self.refresh)
        self.refresh()
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == tab:
                self.tabs.setCurrentIndex(index)

    def page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

    def tool(self, name, args=None):
        self.window.open_capabilities(name, args or {})
        self.refresh()

    def _search_tab(self):
        layout = self.page("Search")
        layout.addWidget(label("Hybrid keyword + semantic ranking uses configured local embeddings. Unavailable embeddings fall back to labeled keyword results.", "Muted", True))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find knowledge about OAuth, browser control, or a project…")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(button("Search", self.search, "Primary"))
        row.addWidget(button("Refresh index", lambda: self.tool("search.rebuild")))
        layout.addLayout(row)
        self.search_status = label("Rebuild explicitly to select the local sources to index.", "Muted", True)
        layout.addWidget(self.search_status)
        split = QSplitter()
        self.results = QListWidget()
        self.results.currentItemChanged.connect(self.show_result)
        split.addWidget(self.results)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
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
        self.window.run_job(lambda: self.s.search.query(text), done, self.search_status.setText)

    def show_result(self, current, _previous=None):
        row = current.data(Qt.ItemDataRole.UserRole) if current else {}
        self.evidence.setPlainText(row.get("text", "") + "\n\nSource: "
                                  + json.dumps(row.get("citation", {}), indent=2, ensure_ascii=False))

    def _knowledge_tab(self):
        layout = self.page("Knowledge")
        row = QHBoxLayout()
        row.addWidget(button("New space", lambda: self.tool("knowledge_spaces.create"), "Primary"))
        row.addWidget(button("Add source", lambda: self.space_tool("knowledge_spaces.add_source")))
        row.addWidget(button("Refresh sources", lambda: self.space_tool("knowledge_spaces.refresh")))
        row.addWidget(button("Ask a question", lambda: self.space_tool("knowledge_spaces.question")))
        row.addWidget(button("Source overview", lambda: self.space_tool("knowledge_spaces.summarize")))
        layout.addLayout(row)
        split = QSplitter()
        self.spaces = QListWidget()
        self.spaces.currentItemChanged.connect(self.select_space)
        split.addWidget(self.spaces)
        self.sources = QListWidget()
        split.addWidget(self.sources)
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        row.addWidget(button("Remove selected reference", self.remove_source, "Quiet"))
        row.addWidget(button("Delete space", lambda: self.space_tool("knowledge_spaces.delete"), "Danger"))
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
        if id := self.selected_space():
            for source in self.s.knowledge_spaces.get(id).get("sources", []):
                item = QListWidgetItem(f"{source['kind']}\n{source['reference']}")
                item.setData(Qt.ItemDataRole.UserRole, source["id"])
                self.sources.addItem(item)

    def remove_source(self):
        source = self.sources.currentItem()
        if source and self.selected_space():
            self.tool("knowledge_spaces.remove_source", {"id": self.selected_space(),
                      "source_id": source.data(Qt.ItemDataRole.UserRole)})

    def _models_tab(self):
        layout = self.page("Models")
        layout.addWidget(label("Local endpoints stay on loopback. Discovery checks exposed model capabilities and never downloads a model. Configure role overrides for planning, summaries, embeddings and vision.", "Muted", True))
        row = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItems(["ollama", "local"])
        self.provider.currentTextChanged.connect(self.load_endpoint)
        row.addWidget(self.provider)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("http://127.0.0.1:11434")
        row.addWidget(self.endpoint, 1)
        row.addWidget(button("Save & discover", self.discover, "Primary"))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.models = QComboBox()
        self.models.setEditable(True)
        row.addWidget(self.models, 1)
        row.addWidget(button("Use for chat", self.use_model))
        row.addWidget(button("Configure task role", lambda: self.tool("models.configure_role")))
        row.addWidget(button("Routing cost", lambda: self.tool("models.configure_cost")))
        layout.addLayout(row)
        self.model_status = QPlainTextEdit()
        self.model_status.setReadOnly(True)
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
            self.model_status.setPlainText(json.dumps(health, indent=2, ensure_ascii=False))
        self.window.run_job(lambda: self.s.discover_models(provider), done, self.model_status.setPlainText)

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
        self.plugins = QListWidget()
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
        selected = self.selected_space()
        self.spaces.blockSignals(True)
        self.spaces.clear()
        for row in self.s.knowledge_spaces.list()["items"]:
            item = QListWidgetItem(f"{row['name']}\n{row.get('sources', 0)} sources")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.spaces.addItem(item)
            if row["id"] == selected:
                self.spaces.setCurrentItem(item)
        self.spaces.blockSignals(False)
        self.select_space()
        self.plugins.clear()
        for row in self.s.plugins.list():
            item = QListWidgetItem(f"{row.get('name', row['id'])} · {row.get('version', '')}\n{row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.plugins.addItem(item)
