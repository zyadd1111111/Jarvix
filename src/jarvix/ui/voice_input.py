"""Microphone controls. Transcripts remain local drafts until the user sends them."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from .widgets import button, label


class VoiceInputPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.services = window.services
        self.microphone = self.services.microphone
        self._held = False
        self._starting = False
        self._revision = 0
        self._request = 0
        self._shutdown = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Local dictation", "Heading"))
        layout.addWidget(label(
            "Hold to talk, release to transcribe. Review the text before sending it to Chat. "
            "Requires Windows speech recognition and microphone access in Settings.", "Muted", True,
        ))
        row = QHBoxLayout()
        self.device = QComboBox()
        self.device.addItem("System default microphone", None)
        row.addWidget(self.device, 1)
        row.addWidget(button("Find microphones", self.load_devices))
        self.silence = QDoubleSpinBox()
        self.silence.setRange(0.5, 10)
        self.silence.setSingleStep(0.5)
        self.silence.setSuffix(" s silence")
        self.silence.setValue(float(self.services.settings.get("microphone.silence", 2)))
        self.silence.valueChanged.connect(lambda value: self.services.settings.set("microphone.silence", value))
        row.addWidget(self.silence)
        layout.addLayout(row)
        self.status = label("Microphone off", "Muted", True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.talk = QPushButton("Hold to talk")
        self.talk.setObjectName("Primary")
        self.talk.pressed.connect(self.press)
        self.talk.released.connect(self.release)
        controls.addWidget(self.talk)
        controls.addWidget(button("Finish transcription", self.finish))
        controls.addWidget(button("Cancel listening", self.cancel))
        self.continuous = QCheckBox("Continuous dictation")
        self.continuous.setToolTip("Opt in each session. Appends local transcripts for review; never sends automatically.")
        self.continuous.toggled.connect(self.continuous_changed)
        controls.addWidget(self.continuous)
        layout.addLayout(controls)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("Your editable transcript appears here. No audio is sent to a provider.")
        self.transcript.setMinimumHeight(85)
        layout.addWidget(self.transcript)
        layout.addWidget(button("Review in Chat  ↗", self.review, "Primary"))
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def load_devices(self):
        self.window.run_job(self.microphone.devices, self.populate_devices, lambda _: self.status.setText(
            "Microphone devices are unavailable. Check your audio driver and Windows privacy settings."
        ))

    def populate_devices(self, devices):
        selected = self.services.settings.get("microphone.device")
        self.device.clear()
        self.device.addItem("System default microphone", None)
        for device in devices:
            self.device.addItem(device["name"], device["id"])
        index = self.device.findData(selected)
        self.device.setCurrentIndex(max(0, index))

    def press(self):
        self._held = True
        self.start()

    def release(self):
        self._held = False
        self.microphone.finish()

    def start(self):
        if self._shutdown or self.window.closing:
            return
        if self._starting or self.microphone.state()["busy"]:
            return
        if not self.services.settings.get("microphone.enabled", False):
            self.status.setText("Microphone access is off. Enable it in Settings first.")
            self.continuous.blockSignals(True)
            self.continuous.setChecked(False)
            self.continuous.blockSignals(False)
            return
        self._starting = True
        self._request += 1
        request = self._request
        self.status.setText("Preparing local microphone…")
        device = self.device.currentData()
        silence = self.silence.value()
        self.services.settings.set("microphone.device", device)

        def ready(_):
            self._starting = False
            if request != self._request or self._shutdown or self.window.closing:
                self.microphone.cancel()
                return
            # A quick press may end before device/engine discovery finishes.
            if not self._held and not self.continuous.isChecked():
                self.microphone.finish()
            self.refresh()

        def failed(message):
            self._starting = False
            self.continuous.blockSignals(True)
            self.continuous.setChecked(False)
            self.continuous.blockSignals(False)
            self.status.setText(message)

        self.window.run_job(
            lambda: self.microphone.start(device, silence) if request == self._request else None,
            ready, failed,
        )

    def finish(self):
        self._held = False
        self.continuous.blockSignals(True)
        self.continuous.setChecked(False)
        self.continuous.blockSignals(False)
        self.microphone.finish()

    def cancel(self):
        self._request += 1
        self._held = False
        self.continuous.blockSignals(True)
        self.continuous.setChecked(False)
        self.continuous.blockSignals(False)
        self.microphone.cancel()
        self.refresh()

    def continuous_changed(self, enabled):
        if enabled:
            self.start()
        else:
            self.microphone.finish()

    def refresh(self):
        if self._shutdown or self.window.closing:
            return
        state = self.microphone.state()
        if not self._starting:
            self.status.setText(("● MICROPHONE ACTIVE · " if state["active"] else "") + state["status"])
        if state["revision"] != self._revision:
            self._revision = state["revision"]
            if state["transcript"]:
                self.transcript.appendPlainText(state["transcript"])
        if self.continuous.isChecked() and not self._starting and not state["busy"]:
            if not self.services.settings.get("microphone.enabled", False) or "unavailable" in state["status"]:
                self.continuous.setChecked(False)
            else:
                self.start()

    def review(self):
        text = self.transcript.toPlainText().strip()
        if text:
            self.cancel()
            self.window.open_chat(text, send=False)

    def hideEvent(self, event):
        self.cancel()
        super().hideEvent(event)

    def shutdown(self):
        """Stop acquisition before the main window waits for pending workers."""
        self._shutdown = True
        self.timer.stop()
        self.cancel()
