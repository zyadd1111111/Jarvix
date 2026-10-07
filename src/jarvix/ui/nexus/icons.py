"""The shared SVG family in Nexus ink; originals and Legacy caches are untouched."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

from ..icons import DIRECTORY


@lru_cache(maxsize=48)
def icon(name):
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz-" for char in name):
        raise ValueError("Unknown interface icon")
    renderer = QSvgRenderer(QByteArray((DIRECTORY / (name + ".svg")).read_bytes().replace(b"#b8bfc8", b"#606b61")))
    result = QIcon()
    for size in (16, 24, 32, 48, 64):
        pixels = QPixmap(size, size)
        pixels.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixels)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pixels)
    return result
