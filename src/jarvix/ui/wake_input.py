"""Visible local wake-mode controls; automatic chat submission is a session opt-in."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .widgets import button, label
from .icons import icon


class WakeWordPanel(QWidget):
    def __init__(self, parent_panel):
        super().__init__(parent_panel)
        self.panel = parent_panel
        self.window = parent_panel.window
        self.services = self.window.services
        self.wake = self.services.wake_word
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(label("Local wake word · Jarvix", "Heading"))
        layout.addWidget(label("Off at each launch. Uses your installed local keyword model; no audio is uploaded.", "Muted", True))
        requirements = label(
            "Install the optional voice extra and a compatible sherpa-onnx keyword model. "
            "Choose a folder with encoder/decoder/joiner ONNX, tokens.txt and tokenized keywords.txt labelled "
            "@jarvix, @stop, @cancel and @never_mind. Jarvix does not download a model automatically.", "Muted", True)
        requirements.hide()
        setup = button("Model requirements", style="Quiet")
        setup.setIcon(icon("chevron-right"))
        setup.setCheckable(True)
        setup.toggled.connect(requirements.setVisible)
        layout.addWidget(setup)
        layout.addWidget(requirements)
        row = QHBoxLayout()
        self.directory = QLineEdit(self.services.settings.get("wake.model_directory", ""))
        self.directory.setAccessibleName("Local wake-word model folder")
        self.directory.setPlaceholderText("Installed local keyword model folder")
        row.addWidget(self.directory, 1)
        row.addWidget(button("Choose model", self.choose_model))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label("Sensitivity", "Muted"))
        self.sensitivity = QDoubleSpinBox()
        self.sensitivity.setRange(.05, .95)
        self.sensitivity.setSingleStep(.05)
        self.sensitivity.setValue(float(self.services.settings.get("wake.sensitivity", .75)))
        self.sensitivity.setAccessibleName("Wake-word sensitivity")
        self.sensitivity.setToolTip("Higher sensitivity detects more easily and may cause false wakes.")
        row.addWidget(self.sensitivity)
        self.enable = button("Enable for this session", self.start, "Primary")
        row.addWidget(self.enable)
        row.addWidget(button("Disable now", self.stop))
        layout.addLayout(row)
        self.hands_free = QCheckBox("Hands-free commands · send each wake transcript to Chat")
        self.hands_free.setToolTip("Opt in for this session only. Tool permissions still apply; say Jarvix before each request.")
        self.hands_free.toggled.connect(self.wake.set_hands_free)
        layout.addWidget(self.hands_free)
        self.status = label("Wake word off", "Muted", True)
        self.status.setAccessibleName("Wake-word microphone activity")
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def choose_model(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose installed local keyword model")
        if directory:
            self.directory.setText(directory)

    def start(self):
        try:
            self.services.settings.set("wake.model_directory", self.directory.text().strip())
            self.services.settings.set("wake.sensitivity", self.sensitivity.value())
            self.services.settings.set("microphone.device", self.panel.device.currentData())
            self.wake.set_hands_free(self.hands_free.isChecked())
            self.wake.start(self.directory.text().strip(), self.panel.device.currentData(),
                            self.sensitivity.value(), self.panel.silence.value())
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def stop(self):
        self.hands_free.setChecked(False)
        self.wake.stop()
        self.refresh()

    def refresh(self):
        if self.window.closing:
            return
        state = self.wake.state()
        self.status.setText(("MICROPHONE ACTIVE · " if state["active"] else "") + state["status"])
        self.enable.setEnabled(not state["busy"])
        self.directory.setEnabled(not state["busy"])
        self.sensitivity.setEnabled(not state["busy"])
        if not state["hands_free"] and self.hands_free.isChecked():
            self.hands_free.setChecked(False)
        for event in self.wake.drain_events():
            if event["kind"] == "cancel":
                self.window.pages["Chat"].cancel()
                self.services.desktop.cancel()
                for session in self.services.operator.list():
                    if session["status"] in {"running", "paused", "awaiting_confirmation"}:
                        self.services.operator.cancel(session["id"])
                continue
            self.panel.transcript.appendPlainText(event["text"])
            chat = self.window.pages["Chat"]
            if (event.get("submit") and state["hands_free"] and not chat.busy
                    and not chat.composer.toPlainText().strip() and not chat.attachments):
                self.window.open_chat(event["text"], send=True)
            else:
                self.window.notify("Voice transcript ready in Voice · Review in Chat")

    def shutdown(self):
        self.timer.stop()
        self.wake.stop()
