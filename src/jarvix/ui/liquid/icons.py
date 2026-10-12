"""One SVG family; optional NEW palette ink never changes OLD icon caches."""
from functools import lru_cache

from PySide6.QtGui import QColor, QIcon, QPainter

from ..nexus.icons import icon as original_icon
from .theme import ink


@lru_cache(maxsize=128)
def tinted_icon(name, color):
    result = QIcon()
    source = original_icon(name)
    for size in (16, 24, 32, 48, 64):
        pixels = source.pixmap(size, size)
        painter = QPainter(pixels)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixels.rect(), QColor(color))
        painter.end()
        result.addPixmap(pixels)
    return result


def icon(name, preferences=None, role="muted"):
    return original_icon(name) if preferences is None else tinted_icon(name, ink(preferences, role))

__all__ = ["icon"]
