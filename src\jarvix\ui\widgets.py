"""Small reusable widgets and background jobs for the native UI."""
from __future__ import annotations

import threading
from typing import Any, Callable
import json

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QWidget, QLabel, QFrame, QPushButton, QVBoxLayout, QPlainTextEdit, QDialog, QDialogButtonBox, QTableWidget, QHeaderView,
    QAbstractItemView,
)


def label(text: str, style: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text: str, callback: Callable | None = None, style: str = "") -> QPushButton:
    widget = QPushButton(text)
    if style:
        widget.setObjectName(style)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(lambda _checked=False: callback())
    return widget


def panel(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(21, 18, 21, 20)
    layout.setSpacing(14)
    if title:
        layout.addWidget(label(title, "Heading"))
    return frame, layout


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def table(headers: list[str]) -> QTableWidget:
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    result.verticalHeader().hide()
    result.verticalHeader().setDefaultSectionSize(44)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.setShowGrid(False)
    result.setAlternatingRowColors(True)
    return result


def evidence_text(value):
    """Render only explicitly cited excerpts as plain text, never as active markup."""
    if not isinstance(value, dict):
        return ""
    excerpts = []
    for key in ("items", "excerpts", "evidence", "sections"):
        for item in value.get(key, []) if isinstance(value.get(key), list) else []:
            if isinstance(item, dict) and item.get("citation") and item.get("text"):
                excerpts.append(str(item["text"]) + "\nSource: " + json.dumps(item["citation"], ensure_ascii=False))
    for document in value.get("documents", []) if isinstance(value.get("documents"), list) else []:
        if isinstance(document, dict) and (text := evidence_text(document)):
            excerpts.append(text)
    if not excerpts:
        return ""
    method = value.get("method", "Source excerpts supplied by this tool. Review citations before relying on them.")
    return str(method) + "\n\n" + "\n\n".join(excerpts)


class SignalOrb(QWidget):
    """A restrained glassy identity mark used for Jarvix's ambient presence."""

    def __init__(self, size=70, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) * .44
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0, QColor(124, 231, 255, 92))
        gradient.setColorAt(.28, QColor(77, 166, 235, 48))
        gradient.setColorAt(.68, QColor(105, 105, 224, 23))
        gradient.setColorAt(1, QColor(14, 20, 32, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for factor, color, width in ((.78, "#426788", 1.0), (.56, "#6fc8ec", 1.1), (.31, "#c4f4ff", 1.35)):
            painter.setPen(QPen(QColor(color), width))
            painter.drawEllipse(center, radius * factor, radius * factor)
        painter.setPen(QPen(QColor("#d7fbff"), 1.2))
        painter.drawLine(center.x() - radius * .96, center.y(), center.x() - radius * .62, center.y())
        painter.drawLine(center.x() + radius * .62, center.y(), center.x() + radius * .96, center.y())
        painter.drawLine(center.x(), center.y() - radius * .96, center.x(), center.y() - radius * .62)
        painter.drawLine(center.x(), center.y() + radius * .62, center.x(), center.y() + radius * .96)


class Job(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], Any], parent=None):
        super().__init__(parent)
        self.work = work

    def run(self):
        try:
            self.succeeded.emit(self.work())
        except Exception as exc:
            self.failed.emit(str(exc))


class ApprovalBridge:
    def __init__(self, request):
        self.request = request
        self.answer = False
        self.ready = threading.Event()


class ChatJob(QThread):
    succeeded = Signal(str)
    failed = Signal(str)
    activity = Signal(str, object)
    approval = Signal(object)

    def __init__(self, services, text, conversation_id, provider_id, model, parent=None, attachments=()):
        super().__init__(parent)
        self.services = services
        self.text = text
        self.conversation_id = conversation_id
        self.provider_id = provider_id
        self.model = model
        self.attachments = tuple(attachments)
        self.cancel = threading.Event()

    def approve(self, request) -> bool:
        bridge = ApprovalBridge(request)
        self.approval.emit(bridge)
        while not bridge.ready.wait(.1):
            if self.cancel.is_set():
                return False
        return bridge.answer and not self.cancel.is_set()

    def run(self):
        try:
            extra = {"attachments": self.attachments} if self.attachments else {}
            answer = self.services.chat(
                self.text, self.conversation_id, self.provider_id, self.model,
                approve=self.approve, on_event=lambda kind, data: self.activity.emit(kind, data),
                cancel=self.cancel,
                **extra,
            )
            self.succeeded.emit(answer)
        except Exception as exc:
            self.failed.emit(str(exc))


class Composer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class TextPreview(QDialog):
    def __init__(self, title: str, description: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 530)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        layout.addWidget(label(title, "Heading"))
        layout.addWidget(label(description, "Muted", True))
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
