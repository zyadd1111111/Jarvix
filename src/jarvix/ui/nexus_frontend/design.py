"""Shared Nexus composition metrics and material primitives, without page layouts."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLayout

from ..liquid.theme import TOKENS as MATERIAL_TOKENS, stylesheet as material_stylesheet, palette
from ..liquid.components import (
    LiquidCanvas as NexusCanvas, LiquidSurface as NexusSurface,
    LiquidSidebar as NexusRail, LiquidCommandBar as NexusComposer,
    LiquidButton as NexusButton, LiquidIconButton as NexusIconButton,
    LiquidInput as NexusInput, LiquidMenu as NexusMenu,
)
from ..liquid.icons import icon
from ..widgets import label

__all__ = ["TOKENS", "stylesheet", "discard_layout", "pane", "title_row", "centered", "icon",
           "NexusCanvas", "NexusSurface", "NexusRail", "NexusComposer", "NexusButton",
           "NexusIconButton", "NexusInput", "NexusMenu"]

TOKENS = {**MATERIAL_TOKENS, "rail_width": 228, "rail_collapsed": 56,
          "conversation_width": 800, "content_width": 1040, "page_inset": 28,
          "library_width": 220, "inspector_width": 300, "section_gap": 24,
          "header_height": 48, "row_height": 36}


def stylesheet(preferences):
    colors = palette(preferences)
    return material_stylesheet(preferences) + """
        QWidget#NexusWorkspace { background: transparent; border: none; }
        QLabel#NexusTitle { font-size: 20pt; font-weight: 500; }
        QLabel#NexusSection { font-size: 11pt; font-weight: 600; }
        QPushButton#NexusNav { text-align: left; padding: 5px 10px; border: none; background: transparent; }
        QPlainTextEdit#NexusCommandInput { background: transparent; border: none; padding: 0px; }
        QScrollArea#NexusDocument, QScrollArea#NexusHomeScroll { border: none; background: transparent; }
    """ + f"""
        QTextBrowser[nexusReadable="true"], QPlainTextEdit[nexusReadable="true"] {{ font-size: {TOKENS['font_size'] + 1}pt; }}
        QPushButton#NexusNav:hover {{ background: {colors['row_hover']}; }}
        QPushButton#NexusNav:checked {{ background: {colors['selected']}; color: {colors['selection_ink']}; }}
    """


def discard_layout(layout, keeper):
    """Keep bound controls alive while discarding their presentation containers."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().setParent(keeper)
        elif item.layout():
            discard_layout(item.layout(), keeper)
            item.layout().deleteLater()


def pane(parent=None, *, horizontal=False, margins=None):
    widget = QWidget(parent)
    widget.setMinimumWidth(0)
    widget.setObjectName("NexusWorkspace")
    widget.body = QHBoxLayout(widget) if horizontal else QVBoxLayout(widget)
    widget.body.setContentsMargins(*(margins or (0, 0, 0, 0)))
    widget.body.setSpacing(8)
    if not horizontal:
        widget.body.setAlignment(Qt.AlignmentFlag.AlignTop)
    widget.body.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
    return widget


def title_row(text, *actions, parent=None):
    widget = pane(parent, horizontal=True)
    widget.body.addWidget(label(text, "NexusTitle"), 1)
    for action in actions:
        widget.body.addWidget(action)
    return widget


def centered(widget, width=None):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch()
    widget.setMaximumWidth(width or TOKENS["content_width"])
    row.addWidget(widget, 1, Qt.AlignmentFlag.AlignTop)
    row.addStretch()
    return row
