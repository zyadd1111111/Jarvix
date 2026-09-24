"""The restrained Jarvix desktop design system."""

import os
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QGuiApplication


def configure_fonts():
    """Qt's offscreen Windows backend does not discover system fonts itself."""
    if os.name != "nt" or QGuiApplication.platformName() not in ("offscreen", "minimal"):
        return
    if "Segoe UI" in QFontDatabase.families():
        return
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
        path = fonts / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))

STYLESHEET = """
QWidget { background: #0d1016; color: #dce2ef; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #0d1016; }
QLabel { background: transparent; }
QLabel#Brand { font-size: 20px; font-weight: 700; letter-spacing: 4px; color: #f3f5ff; }
QLabel#Eyebrow { font-size: 10px; font-weight: 700; color: #7f8bab; letter-spacing: 2px; }
QLabel#Title { font-size: 28px; font-weight: 600; color: #f4f6fc; }
QLabel#Subtitle { font-size: 12px; color: #8793aa; }
QLabel#Heading { font-size: 15px; font-weight: 600; color: #edf1fb; }
QLabel#Metric { font-size: 30px; font-weight: 500; color: #e3eafd; }
QLabel#Muted { color: #8b98ad; }
QLabel#Accent { color: #92b9ff; }
QLabel#Success { color: #8bbfb2; }
QFrame#Sidebar { background: #090c11; border-right: 1px solid #202633; }
QFrame#Topbar { background: #0d1016; border-bottom: 1px solid #202633; }
QFrame#Panel { background: #121720; border: 1px solid #252d3c; border-radius: 8px; }
QFrame#Hero { background: #151d2d; border: 1px solid #303c58; border-radius: 9px; }
QFrame#Divider { background: #272f3d; max-height: 1px; min-height: 1px; }
QFrame#Message { background: #111720; border-left: 2px solid #607cb7; }
QFrame#UserMessage { background: #151b28; border-left: 2px solid #7568aa; }
QPushButton { background: #1b2331; border: 1px solid #303b4e; border-radius: 5px; padding: 8px 13px; color: #dce4f4; }
QPushButton:hover { background: #253149; border-color: #586d98; }
QPushButton:pressed { background: #304165; }
QPushButton:disabled { color: #596479; background: #151b25; border-color: #242c3a; }
QPushButton#Primary { background: #5374be; color: #ffffff; border: 1px solid #7294dd; font-weight: 600; }
QPushButton#Primary:hover { background: #6587ce; }
QPushButton#Danger { color: #e8a0ad; background: #211720; border-color: #513341; }
QPushButton#Quiet { background: transparent; border-color: transparent; color: #8f9db5; }
QPushButton#Quiet:hover { background: #1b2331; color: #e3eaff; }
QPushButton#Navigation { text-align: left; background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 9px 12px; color: #8a95aa; }
QPushButton#Navigation:hover { background: #121a27; color: #dfe8ff; }
QPushButton#Navigation:checked { background: #1b263c; color: #becffc; border-color: #293957; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox { background: #0d121b; border: 1px solid #303a4e; border-radius: 5px; padding: 9px; color: #e2e8f5; selection-background-color: #435e93; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #6788cc; }
QLineEdit::placeholder { color: #65738b; }
QComboBox { background: #151e2c; border: 1px solid #303b4e; border-radius: 5px; padding: 7px 10px; min-width: 95px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #192232; selection-background-color: #2e4268; }
QListWidget { background: #10151e; border: 1px solid #263044; border-radius: 6px; padding: 5px; outline: 0; }
QListWidget::item { padding: 10px 8px; border-bottom: 1px solid #1e2736; }
QListWidget::item:selected { background: #23324f; color: #dfebff; border-radius: 4px; }
QListWidget::item:hover { background: #1b2638; }
QTableWidget { background: #10151e; alternate-background-color: #141b26; border: 1px solid #283244; border-radius: 6px; gridline-color: #202b3c; selection-background-color: #233554; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #171f2c; border: none; border-bottom: 1px solid #2d394f; padding: 9px; color: #8f9fb9; font-size: 11px; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #344159; border-radius: 3px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 8px; background: transparent; }
QScrollBar::handle:horizontal { background: #344159; border-radius: 3px; }
QProgressBar { border: none; background: #252f41; border-radius: 3px; height: 5px; text-align: center; }
QProgressBar::chunk { background: #728fd0; border-radius: 3px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #465675; background: #111824; border-radius: 3px; }
QCheckBox::indicator:checked { background: #6586cf; border-color: #87a8ed; }
QSplitter::handle { background: #0d1016; width: 12px; }
QToolTip { background: #202c41; color: #e5edff; border: 1px solid #465b80; padding: 7px; }
QMenu { background: #152032; border: 1px solid #34435d; padding: 5px; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #293f65; }
"""
