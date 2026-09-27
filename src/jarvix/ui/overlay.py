"""Optional command overlay and a scoped Windows global hotkey registration."""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from jarvix.domain import PermissionRequest

from .chat import PermissionDialog
from .widgets import TextPreview, button, label


HOTKEYS = {"Alt+Space": (0x0001, 0x20), "Ctrl+Alt+Space": (0x0003, 0x20),
           "Alt+J": (0x0001, 0x4A), "Ctrl+Shift+Space": (0x0006, 0x20)}


class GlobalHotkey(QAbstractNativeEventFilter):
    identifier = 0x4A56

    def __init__(self, callback, identifier=0x4A56, choices=None):
        super().__init__()
        self.callback = callback
        self.identifier = identifier
        self.choices = HOTKEYS if choices is None else choices
        self.registered = False
        self.api = None

    def configure(self, enabled, sequence):
        self.close()
        if not enabled:
            return "Overlay hotkey is off."
        if os.name != "nt" or QApplication.platformName() in {"offscreen", "minimal"}:
            return "Global overlay hotkey is available in the Windows desktop application."
        if sequence not in self.choices:
            return "Choose a supported shortcut."
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.api.RegisterHotKey.restype = wintypes.BOOL
        self.api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.UnregisterHotKey.restype = wintypes.BOOL
        modifiers, key = self.choices[sequence]
        self.registered = bool(self.api.RegisterHotKey(None, self.identifier, modifiers | 0x4000, key))
        if not self.registered:
            return "That hotkey is already in use. Choose another shortcut in Settings."
        QApplication.instance().installNativeEventFilter(self)
        return f"Overlay ready · {sequence}"

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.identifier:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            QApplication.instance().removeNativeEventFilter(self)
            self.api.UnregisterHotKey(None, self.identifier)
            self.registered = False


class WorkflowHotkeys:
    """Only enabled, explicitly configured workflow shortcuts own native keys."""

    def __init__(self, callback):
        self.callback = callback
        self.bindings = {}
        self.signature = None

    def configure(self, workflows, enabled=True):
        from jarvix.capabilities.workflows import WORKFLOW_HOTKEYS
        shortcuts = tuple(sorted({row["config"]["shortcut"] for row in workflows
                                  if enabled and row.get("enabled") and row.get("trigger") == "hotkey"}))
        if shortcuts == self.signature:
            return []
        self.close()
        self.signature = shortcuts
        errors = []
        for index, shortcut in enumerate(shortcuts):
            binding = GlobalHotkey(lambda key=shortcut: self.callback(key),
                                   identifier=0x4B00 + index, choices=WORKFLOW_HOTKEYS)
            message = binding.configure(True, shortcut)
            if binding.registered:
                self.bindings[shortcut] = binding
            else:
                errors.append(f"{shortcut}: {message}")
        return errors

    def close(self):
        for binding in self.bindings.values():
            binding.close()
        self.bindings.clear()
        self.signature = None


class CommandOverlay(QDialog):
    def __init__(self, window):
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.window = window
        self.setWindowTitle("Jarvix quick command")
        self.setObjectName("CommandOverlay")
        self.setFixedWidth(720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        row = QHBoxLayout()
        row.addWidget(label("JARVIX", "Brand"))
        row.addStretch()
        row.addWidget(button("Esc", self.hide, "Quiet"))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("Ask Jarvix or tell it to do something…")
        self.command.setMinimumHeight(48)
        self.command.returnPressed.connect(self.submit)
        row.addWidget(self.command, 1)
        row.addWidget(button("Voice", self.voice))
        row.addWidget(button("↑", self.submit, "Primary"))
        layout.addLayout(row)
        self.context = label("Current application context is off.", "Muted", True)
        layout.addWidget(self.context)
        context_row = QHBoxLayout()
        self.share_context = QCheckBox("Include current context with this message")
        self.share_context.setEnabled(False)
        context_row.addWidget(self.share_context)
        self.inspect_button = button("Inspect context", self.inspect_context, "Quiet")
        self.inspect_button.setEnabled(False)
        context_row.addWidget(self.inspect_button)
        layout.addLayout(context_row)
        layout.addWidget(label("Commands open a visible session. Screen access happens only on request.", "Muted"))
        self.context_generation = 0
        self.pending_context = False
        self.context_snapshot = None

    def toggle(self):
        if self.isVisible() or self.pending_context:
            self.context_generation += 1
            self.pending_context = False
            self.hide()
            return
        self.context_generation += 1
        generation = self.context_generation
        self.context.setText("Context off · enable explicit context in Settings" if not
                             self.window.services.settings.get("context.enabled", False) else "Reading current app context…")
        self.context_snapshot = None
        self.share_context.setChecked(False)
        self.share_context.setEnabled(False)
        self.inspect_button.setEnabled(False)
        # Finish the snapshot before showing a focusable overlay, preserving the target app.
        if self.window.services.settings.get("context.enabled", False):
            self.pending_context = True
            self.window.run_job(self.window.services.context.inspect,
                                lambda result: self.update_context(result, generation),
                                lambda error: self.context_failed(error, generation))
            return
        self.reveal()

    def reveal(self):
        if self.window.closing:
            return
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2, area.top() + area.height() // 4)
        self.show()
        self.raise_()
        self.activateWindow()
        self.command.setFocus()

    def update_context(self, result, generation):
        if generation != self.context_generation:
            return
        self.pending_context = False
        self.context_snapshot = result
        self.share_context.setEnabled(True)
        self.inspect_button.setEnabled(True)
        active = result.get("active_window") or result.get("window") or {}
        if isinstance(active, dict):
            text = active.get("title") or active.get("name") or "No active application"
        else:
            text = str(active)
        self.context.setText("Current app · " + text)
        self.reveal()

    def context_failed(self, error, generation):
        if generation == self.context_generation:
            self.pending_context = False
            self.context.setText(error)
            self.reveal()

    def submit(self):
        text = self.command.text().strip()
        if not text:
            return
        if (self.share_context.isChecked() and self.context_snapshot and
                self.window.services.settings.get("context.enabled", False)):
            preview = json.dumps(self.context_snapshot, indent=2, ensure_ascii=False)
            request = PermissionRequest("disclose", "context.inspect", "context.read",
                                        "Include this captured context with your message to the selected AI provider?",
                                        {}, preview)
            if PermissionDialog(request, self).exec() != QDialog.DialogCode.Accepted:
                return
            text += "\n\nUser-approved context snapshot (captured before opening Jarvix):\n" + preview
        self.command.clear()
        self.hide()
        self.window.restore_window()
        self.window.open_chat(text)

    def inspect_context(self):
        if self.context_snapshot:
            TextPreview("Current context", "This snapshot stays local until you explicitly approve sharing it.",
                        json.dumps(self.context_snapshot, indent=2, ensure_ascii=False), self).exec()

    def voice(self):
        self.hide()
        self.window.restore_window()
        self.window.navigate("Voice")
        self.window.pages["Voice"].input_panel.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        QTimer.singleShot(0, self.dismiss_if_inactive)
        super().focusOutEvent(event)

    def dismiss_if_inactive(self):
        if not self.isActiveWindow():
            self.hide()
