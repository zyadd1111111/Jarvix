"""Rasterize the original vector mark for Windows executable resources."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

app = QGuiApplication([])
assets = Path(__file__).resolve().parents[1] / "src" / "jarvix" / "assets"
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
QSvgRenderer(str(assets / "jarvix.svg")).render(painter)
painter.end()
if not image.save(str(assets / "jarvix.ico")):
    raise SystemExit("ICO generation failed")
