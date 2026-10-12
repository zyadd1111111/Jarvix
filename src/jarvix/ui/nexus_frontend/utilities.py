"""Secondary workspaces retain controls, never the original page composition."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QSplitter, QTabWidget

from ..pages import VoicePage, MemoryPage, ActivityPage
from ..integrations import IntegrationsPage
from ..widgets import label
from .design import TOKENS, pane, title_row, discard_layout, NexusButton


def blank(page, controls):
    keeper = QWidget(page)
    for control in controls:
        control.setParent(keeper)
    discard_layout(page.layout, keeper)
    page.layout.setContentsMargins(TOKENS["page_inset"], 18, TOKENS["page_inset"], 18)
    page.layout.setSpacing(18)


class NexusVoicePage(VoicePage):
    def __init__(self, window):
        super().__init__(window)
        blank(self, (self.input_panel, self.text, self.status))
        self.layout.addWidget(title_row("Voice"))
        tabs = QTabWidget(self)
        dictation = pane(tabs)
        dictation.body.addWidget(self.input_panel)
        dictation.body.addStretch()
        tabs.addTab(dictation, "Dictation")
        playback = pane(tabs)
        playback.body.addWidget(label("Read text aloud locally.", "Muted"))
        playback.body.addWidget(self.text, 1)
        actions = QHBoxLayout()
        actions.addWidget(self.status, 1)
        actions.addWidget(NexusButton("Stop speaking", self.stop))
        actions.addWidget(NexusButton("Read aloud", self.speak, "Primary"))
        playback.body.addLayout(actions)
        tabs.addTab(playback, "Playback")
        self.layout.addWidget(tabs, 1)


class NexusMemoryPage(MemoryPage):
    def __init__(self, window):
        super().__init__(window)
        blank(self, (self.entries, self.content, self.search))
        self.layout.addWidget(title_row("Memory", NexusButton("Inspect selected fact", self.explain, "Quiet")))
        split = QSplitter(Qt.Orientation.Horizontal, self)
        library = pane(split)
        library.body.addWidget(self.search)
        library.body.addWidget(self.entries, 1)
        inspector = pane(split)
        inspector.setMaximumWidth(TOKENS["inspector_width"])
        inspector.body.addWidget(label("Save an explicit fact", "NexusSection"))
        inspector.body.addWidget(label("Keep temporary context in a session. Save here when you want a lasting fact.", "Muted", True))
        inspector.body.addWidget(self.content)
        inspector.body.addWidget(NexusButton("Save fact", self.add, "Primary"))
        inspector.body.addWidget(NexusButton("Review session context", lambda: window.open_capabilities("context.session_inspect"), "Quiet"))
        inspector.body.addStretch()
        inspector.body.addWidget(NexusButton("Forget selected fact", self.delete, "Danger"))
        split.setSizes([700, TOKENS["inspector_width"]])
        self.layout.addWidget(split, 1)


class NexusActivityPage(ActivityPage):
    def __init__(self, window):
        super().__init__(window)
        controls = (self.entries, self.search, self.kind, self.status)
        blank(self, controls)
        self.layout.addWidget(title_row("Activity"))
        filters = QHBoxLayout()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.kind)
        filters.addWidget(NexusButton("Filter activity", self.refresh, "Quiet"))
        self.layout.addLayout(filters)
        self.layout.addWidget(self.entries, 1)
        self.layout.addWidget(self.status)


class NexusIntegrationsPage(IntegrationsPage):
    def __init__(self, window):
        super().__init__(window)
        controls = [self.account_status, *self.keys.values(), *self.status_labels.values()]
        blank(self, controls)
        self.layout.addWidget(title_row("Integrations"))
        tabs = QTabWidget(self)
        accounts = pane(tabs)
        accounts.body.addWidget(label("Accounts stay local. Jarvix reads account data only when you request it.", "Muted", True))
        self.account_status.setMinimumHeight(0)
        self.account_status.verticalHeader().setDefaultSectionSize(52)
        accounts.body.addWidget(self.account_status, 1)
        tabs.addTab(accounts, "Accounts")
        models = pane(tabs)
        models.body.addWidget(label("Model credentials", "NexusSection"))
        for provider, name in (("openai", "OpenAI"), ("gemini", "Gemini")):
            row = QHBoxLayout()
            row.addWidget(label(name))
            row.addWidget(self.status_labels[provider])
            row.addWidget(self.keys[provider], 1)
            row.addWidget(NexusButton("Save key", lambda selected=provider: self.save(selected)))
            row.addWidget(NexusButton("Remove key", lambda selected=provider: self.remove(selected), "Quiet"))
            models.body.addLayout(row)
        models.body.addWidget(label("Keys use the OS credential vault. Never include them in a conversation.", "Muted", True))
        models.body.addStretch()
        tabs.addTab(models, "Models")
        self.layout.addWidget(tabs, 1)
