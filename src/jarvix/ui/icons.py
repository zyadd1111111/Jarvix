"""One consistent 24-unit stroke SVG family, rendered by Qt at the device scale."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

DIRECTORY = Path(__file__).parent.parent / "assets" / "icons"


@lru_cache(maxsize=48)
def icon(name):
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz-" for char in name):
        raise ValueError("Unknown interface icon.")
    source = DIRECTORY / (name + ".svg")
    if not source.is_file():
        raise ValueError("Unknown interface icon: " + name)
    return QIcon(str(source))
