"""Liquid Glass OS visual language for the native Jarvix UI.

This module is intentionally presentation-only. The stylesheet targets the object
names already used by the desktop shell and pages, so application contracts and
service boundaries remain unchanged.
"""

import os
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QGuiApplication


def configure_fonts():
    """Make the Windows geometric sans available to Qt's offscreen backend."""
    if os.name != "nt" or QGuiApplication.platformName() not in ("offscreen", "minimal"):
        return
    families = QFontDatabase.families()
    if "Segoe UI" in families or "Segoe UI Variable" in families:
        return
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
        path = fonts / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


# Qt stylesheets do not expose browser-style backdrop-filter. Layered,
# translucent gradients, refractive borders and soft shadows provide the same
# depth while remaining native, reliable and inexpensive on Windows.
STYLESHEET = r"""
* { outline: none; }
QWidget {
    background: #080b12;
    color: #e7edf8;
    font-family: 'Segoe UI Variable', 'Segoe UI';
    font-size: 13px;
}
QMainWindow, QDialog {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #070a10, stop:.55 #0a0f18, stop:1 #080b13);
}
QDialog#ControlHud, QDialog#CommandOverlay {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(21, 30, 49, 250), stop:.55 rgba(12, 19, 34, 248), stop:1 rgba(8, 13, 24, 252));
    border: 1px solid rgba(142, 212, 255, 115);
    border-radius: 18px;
}
QLabel { background: transparent; }
QLabel#Brand {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 5px;
    color: #f7fbff;
}
QLabel#Eyebrow {
    font-size: 10px;
    font-weight: 700;
    color: #8394b4;
    letter-spacing: 2px;
}
QLabel#Title {
    font-size: 28px;
    font-weight: 600;
    color: #f5f8ff;
}
QLabel#Subtitle { font-size: 12px; color: #8797b0; }
QLabel#Heading { font-size: 15px; font-weight: 600; color: #eaf3ff; }
QLabel#Metric { font-size: 30px; font-weight: 500; color: #dff5ff; }
QLabel#Muted { color: #8b9ab2; }
QLabel#Accent { color: #7fe6ff; }
QLabel#Success { color: #83d5b5; }

QFrame#Sidebar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #080b12, stop:.7 #0b101a, stop:1 #0e1522);
    border-right: 1px solid rgba(174, 212, 255, 32);
}
QFrame#Topbar {
    background: rgba(8, 12, 20, 235);
    border-bottom: 1px solid rgba(174, 212, 255, 32);
}
QFrame#Panel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(21, 31, 47, 235), stop:.48 rgba(16, 24, 38, 222), stop:1 rgba(10, 16, 27, 238));
    border: 1px solid rgba(190, 224, 255, 35);
    border-top-color: rgba(225, 244, 255, 72);
    border-radius: 16px;
}
QFrame#Panel:hover {
    border-color: rgba(111, 224, 255, 82);
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(26, 39, 60, 242), stop:.48 rgba(17, 29, 46, 230), stop:1 rgba(11, 18, 31, 242));
}
QFrame#Hero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(21, 49, 72, 238), stop:.42 rgba(18, 35, 57, 232), stop:1 rgba(29, 25, 59, 238));
    border: 1px solid rgba(134, 225, 255, 105);
    border-top-color: rgba(234, 252, 255, 145);
    border-radius: 20px;
}
QFrame#Divider { background: rgba(164, 208, 245, 42); max-height: 1px; min-height: 1px; }
QFrame#Message, QFrame#UserMessage {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(18, 30, 45, 240), stop:1 rgba(12, 19, 31, 228));
    border: 1px solid rgba(184, 219, 255, 30);
    border-left: 2px solid #59dfff;
    border-radius: 14px;
}
QFrame#UserMessage {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(35, 29, 62, 238), stop:1 rgba(19, 22, 43, 228));
    border-left-color: #a990ff;
}

QPushButton {
    background: rgba(28, 41, 62, 215);
    border: 1px solid rgba(187, 222, 255, 48);
    border-top-color: rgba(232, 248, 255, 88);
    border-radius: 9px;
    padding: 8px 14px;
    color: #dfecfb;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(51, 82, 112, 230), stop:1 rgba(37, 51, 88, 230));
    border-color: rgba(116, 229, 255, 130);
    color: #f5fcff;
}
QPushButton:pressed { background: rgba(53, 84, 123, 245); }
QPushButton:disabled { color: #5d6a80; background: rgba(15, 22, 34, 180); border-color: rgba(137, 164, 196, 20); }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #3f9fc2, stop:.52 #4978c5, stop:1 #7654b8);
    color: #ffffff;
    border: 1px solid rgba(206, 249, 255, 150);
    font-weight: 600;
}
QPushButton#Primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #58c7e4, stop:.5 #608fe3, stop:1 #946bd4); }
QPushButton#Danger { color: #ffc1ca; background: rgba(69, 27, 42, 200); border-color: rgba(255, 123, 145, 76); }
QPushButton#Quiet { background: transparent; border-color: transparent; color: #97a9c3; }
QPushButton#Quiet:hover { background: rgba(117, 197, 239, 24); color: #effaff; border-color: rgba(116, 229, 255, 55); }
QPushButton#Navigation {
    text-align: left;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 10px 12px;
    color: #8f9db4;
}
QPushButton#Navigation:hover { background: rgba(106, 188, 227, 18); color: #dff7ff; }
QPushButton#Navigation:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(74, 184, 223, 50), stop:1 rgba(112, 95, 214, 42));
    color: #c8f5ff;
    border-color: rgba(119, 222, 255, 75);
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background: rgba(5, 11, 20, 190);
    border: 1px solid rgba(170, 211, 247, 54);
    border-top-color: rgba(220, 243, 255, 74);
    border-radius: 10px;
    padding: 9px 11px;
    color: #e8f3ff;
    selection-background-color: #2e789b;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: #62d8f5; background: rgba(9, 19, 32, 230); }
QLineEdit::placeholder { color: #667993; }
QComboBox {
    background: rgba(18, 31, 48, 225);
    border: 1px solid rgba(180, 220, 255, 50);
    border-radius: 9px;
    padding: 8px 11px;
    min-width: 95px;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #121f31; border: 1px solid #456381; selection-background-color: #285273; }
QListWidget {
    background: rgba(8, 15, 26, 205);
    border: 1px solid rgba(170, 211, 247, 48);
    border-radius: 11px;
    padding: 5px;
    outline: 0;
}
QListWidget::item { padding: 10px 9px; border-bottom: 1px solid rgba(176, 215, 248, 22); }
QListWidget::item:selected { background: rgba(75, 148, 193, 74); color: #e3f8ff; border-radius: 7px; }
QListWidget::item:hover { background: rgba(91, 190, 231, 30); }
QTableWidget {
    background: rgba(8, 15, 26, 205);
    alternate-background-color: rgba(18, 30, 47, 190);
    border: 1px solid rgba(170, 211, 247, 48);
    border-radius: 11px;
    gridline-color: rgba(160, 205, 243, 25);
    selection-background-color: rgba(67, 142, 190, 92);
}
QTableWidget::item { padding: 9px; }
QHeaderView::section {
    background: rgba(23, 39, 59, 230);
    border: none;
    border-bottom: 1px solid rgba(187, 226, 255, 55);
    padding: 9px;
    color: #91a8c3;
    font-size: 10px;
    font-weight: 700;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 3px; }
QScrollBar::handle:vertical { background: rgba(106, 157, 195, 105); border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: rgba(115, 222, 255, 150); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 9px; background: transparent; }
QScrollBar::handle:horizontal { background: rgba(106, 157, 195, 105); border-radius: 4px; }
QProgressBar { border: none; background: rgba(63, 91, 121, 100); border-radius: 4px; height: 6px; text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #56d5ed, stop:1 #9b7cf2); border-radius: 4px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #51708d; background: rgba(10, 18, 29, 210); border-radius: 5px; }
QCheckBox::indicator:checked { background: #55b9da; border-color: #b2f4ff; }
QSplitter::handle { background: transparent; width: 12px; }
QToolTip { background: #172b40; color: #edfbff; border: 1px solid #4c779a; padding: 7px; border-radius: 6px; }
QMenu { background: #101c2d; border: 1px solid #416180; padding: 5px; border-radius: 8px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: rgba(76, 162, 207, 95); }
QStatusBar { background: rgba(7, 12, 20, 245); border-top: 1px solid rgba(174, 212, 255, 32); color: #8299b4; padding: 4px 12px; font-size: 11px; }
"""
