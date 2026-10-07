"""OS-style settings using the same controls and persistence callbacks as Legacy."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QFrame, QCheckBox, QComboBox, QLabel, QPushButton

from ..pages import SettingsPage
from ..widgets import label, button


class NexusSettingsPage(SettingsPage):
    subtitle = "Preferences and access"

    def __init__(self, window):
        super().__init__(window)
        old = {self.categories.item(i).text(): self.settings_stack.widget(i)
               for i in range(self.categories.count())}
        added = {}
        for name in ("General", "Permissions", "Integrations", "Extensions"):
            body = QWidget(self)
            content = QVBoxLayout(body)
            content.setContentsMargins(16, 0, 8, 0)
            content.setSpacing(12)
            content.addWidget(label(name, "Heading"))
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(body)
            added[name] = scroll
            self.settings_sections[name] = content
        general = self.settings_sections["General"]
        self.move_row(self.settings_sections["Appearance"], general, self.access_checks["tray.enabled"])
        general.addWidget(button("Review Windows startup", lambda: window.open_capabilities("windows.startup")))
        general.addStretch()
        permissions = self.settings_sections["Permissions"]
        self.move_row(self.settings_sections["Privacy & control"], permissions, self.access_checks["control.enabled"])
        permissions.addWidget(label("Sensitive actions always require a fresh confirmation. Neither interface changes your permissions.", "Muted", True))
        permissions.addWidget(button("Review action permissions", window.open_capabilities))
        permissions.addStretch()
        self.settings_sections["Integrations"].addWidget(button("Manage connected accounts", lambda: window.navigate("Integrations")))
        self.settings_sections["Integrations"].addWidget(label("Accounts, credentials and approved scopes are shared by both interfaces.", "Muted", True))
        self.settings_sections["Integrations"].addStretch()
        self.settings_sections["Extensions"].addWidget(button("Manage extensions", lambda: window.open_adaptive("Extensions")))
        self.settings_sections["Extensions"].addWidget(label("Extensions retain their existing approval and permission boundaries.", "Muted", True))
        self.settings_sections["Extensions"].addStretch()
        appearance = self.settings_sections["Appearance"]
        self.glass_intensity = QComboBox(self)
        self.glass_intensity.addItem("Frosted", "frosted")
        self.glass_intensity.addItem("Solid", "solid")
        self.glass_intensity.setCurrentIndex(max(0, self.glass_intensity.findData(self.services.settings.get("nexus.glass", "frosted"))))
        self.transparency = QCheckBox("Enabled", self)
        self.transparency.setChecked(self.services.settings.get("nexus.transparency", True))
        self.reduced_motion = QCheckBox("Enabled", self)
        self.reduced_motion.setChecked(not self.services.settings.get("nexus.motion", True))
        for caption, control, description in (
            ("Glass intensity", self.glass_intensity, "Solid surfaces reduce compositing work."),
            ("Transparency", self.transparency, "Turn off translucent surfaces when more contrast is needed."),
            ("Reduced motion", self.reduced_motion, "Disable transitions between sections."),
        ):
            # Add before the existing final stretch, so controls stay near the top.
            appearance.takeAt(appearance.count() - 1)
            self.setting_row(appearance, caption, control, description)
            appearance.addStretch()
        self.glass_intensity.currentIndexChanged.connect(self.save_materials)
        self.transparency.toggled.connect(self.save_materials)
        self.reduced_motion.toggled.connect(self.save_materials)
        ordered = (("General", added["General"]), ("Appearance", old["Appearance"]),
            ("Models", old["AI & models"]), ("Privacy", old["Privacy & control"]),
            ("Permissions", added["Permissions"]), ("Files", old["Files & storage"]),
            ("Browser", old["Browser"]), ("Voice", old["Voice"]), ("Automations", old["Automations"]),
            ("Integrations", added["Integrations"]), ("Extensions", added["Extensions"]), ("Advanced", old["Advanced"]))
        self.categories.blockSignals(True)
        self.categories.clear()
        for index, (name, scroll) in enumerate(ordered):
            self.settings_stack.removeWidget(scroll)
            self.settings_stack.insertWidget(index, scroll)
            self.categories.addItem(name)
        self.categories.blockSignals(False)
        self.categories.setCurrentRow(0)
        for text in self.findChildren(QLabel):
            replacements = {"AI & models": "Models", "AI capabilities": "Capabilities", "Dark": "Warm cream", "Local-only AI": "Local-only mode"}
            if text.text() in replacements:
                text.setText(replacements[text.text()])
        for control in self.findChildren(QPushButton):
            if control.text() == "Manage AI credentials":
                control.setText("Manage credentials")
                control.setAccessibleName("Manage credentials")

    @staticmethod
    def move_row(source, target, control):
        for index in range(source.count()):
            row = source.itemAt(index).layout()
            if row and any(row.itemAt(i).widget() is control for i in range(row.count())):
                source.takeAt(index)
                target.addLayout(row)
                return

    def save_materials(self, *_):
        def persist():
            self.services.settings.set("nexus.glass", self.glass_intensity.currentData())
            self.services.settings.set("nexus.transparency", self.transparency.isChecked())
            self.services.settings.set("nexus.motion", not self.reduced_motion.isChecked())
            self.window.configure_materials()
        self.guard(persist)
