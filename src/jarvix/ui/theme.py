"""Shared technical desktop tokens; dark today, light palette prepared for later."""
import os
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QGuiApplication

DARK = {
    "background": "#191b1f", "surface": "#202328", "raised": "#292d33",
    "border": "#383d44", "muted": "#a5abb4", "text": "#e7e9ed",
    "accent": "#5db6a6", "selected": "#343a42", "warning": "#d6ad64",
    "error": "#e48989", "success": "#81be99", "disabled": "#747b85", "on_accent": "#142421",
}
LIGHT = {
    "background": "#f5f5f4", "surface": "#ffffff", "raised": "#eeefef",
    "border": "#c8ccd0", "muted": "#555d67", "text": "#22272e",
    "accent": "#167565", "selected": "#dce1e5", "warning": "#936517",
    "error": "#b32935", "success": "#287943", "disabled": "#838990", "on_accent": "#ffffff",
}
COLORS = DARK
MONO_FONT = "Cascadia Mono"
TOKENS = {"spacing": (4, 8, 12, 16, 24), "radius": 5, "font_size": 10,
          "control_height": 24, "animation_ms": 140}


def configure_fonts():
    if os.name != "nt" or QGuiApplication.platformName() not in ("offscreen", "minimal"):
        return
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("SegUIVar.ttf", "segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "CascadiaMono.ttf", "consola.ttf"):
        path = fonts / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def stylesheet(mode="dark"):
    palette = LIGHT if mode == "light" else DARK
    style = r"""
QWidget { background: @background; color: @text; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 10pt; }
QMainWindow, QDialog { background: @background; }
QLabel { background: transparent; }
QLabel#Brand { font-size: 12pt; font-weight: 600; }
QLabel#Title { font-size: 16pt; font-weight: 600; }
QLabel#Heading, QLabel#SectionHeader { font-size: 10.5pt; font-weight: 600; }
QLabel#MessageAuthor, QLabel#SectionTitle { font-size: 10pt; font-weight: 600; }
QLabel#Eyebrow, QLabel#SidebarGroup { font-size: 8pt; color: @muted; }
QLabel#Subtitle, QLabel#Muted, QLabel#Caption { color: @muted; font-size: 9pt; }
QLabel#Metric { font-size: 14pt; font-weight: 600; }
QLabel#Accent { color: @accent; }
QLabel#Success { color: @success; }
QLabel#Warning { color: @warning; }
QLabel#Error { color: @error; }
QLabel#Code, QPlainTextEdit, QTextEdit#Code, QTextBrowser { font-family: 'Cascadia Mono', 'Consolas', monospace; }
QPlainTextEdit#Composer { font-family: 'Segoe UI Variable', 'Segoe UI'; }
QTextBrowser#MessageText { background: transparent; border: none; padding: 0; font-family: 'Segoe UI Variable', 'Segoe UI'; }
QWidget#ToolRow { background: @surface; border: 1px solid @border; border-radius: 4px; }
QFrame#Sidebar { background: @surface; border-right: 1px solid @border; }
QWidget#SidebarContent, QScrollArea#SidebarScroll { background: @surface; border: none; }
QFrame#Topbar { border-bottom: 1px solid @border; background: @background; }
QFrame#Panel, QFrame#InspectorPanel { background: @surface; border: 1px solid @border; border-radius: 6px; }
QFrame#Section, QFrame#Message, QFrame#ToolRow { background: transparent; border: none; border-bottom: 1px solid @border; }
QFrame#UserMessage { background: @surface; border: none; border-left: 2px solid @border; }
QFrame#Divider { background: @border; border: none; max-height: 1px; }
QListWidget#ToolTimeline::item { padding: 0; }
QListWidget#ToolTimeline { border: none; background: transparent; }
QFrame#CommandBar { background: @surface; border: 1px solid @border; border-radius: 6px; }
QDialog#ControlHud, QDialog#CommandOverlay { background: @raised; border: 1px solid @border; border-radius: 8px; }
QPushButton, QToolButton { background: @raised; color: @text; border: 1px solid @border; border-radius: 4px; padding: 4px 10px; min-height: @control_height; }
QPushButton:hover, QToolButton:hover { background: @selected; border-color: @muted; }
QPushButton:pressed, QToolButton:pressed { background: @surface; }
QPushButton:focus, QToolButton:focus { border: 1px solid @accent; }
QPushButton:disabled, QToolButton:disabled { color: @disabled; border-color: @border; background: @surface; }
QPushButton#Primary { background: @accent; color: @on_accent; border-color: @accent; font-weight: 600; }
QPushButton#Primary:hover { background: @success; border-color: @success; }
QPushButton#Primary:pressed { background: @accent; border-color: @text; }
QPushButton#Primary:disabled { background: @raised; color: @disabled; border-color: @border; }
QPushButton#Danger { color: @error; }
QPushButton#Danger:hover { border-color: @error; }
QPushButton#Quiet { background: transparent; border-color: transparent; }
QPushButton#Quiet:hover { background: @raised; border-color: @border; }
QPushButton#Quiet:focus { border-color: @accent; }
QPushButton#Navigation { text-align: left; padding: 3px 8px; border: 1px solid transparent; border-radius: 4px; background: transparent; color: @muted; }
QPushButton#Navigation:hover { color: @text; background: @raised; }
QPushButton#Navigation:checked { color: @text; background: @selected; border-color: @border; }
QPushButton#Navigation:focus { border-color: @accent; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: @surface; border: 1px solid @border; border-radius: 4px; padding: 5px 8px; selection-background-color: @selected; color: @text; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: @accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled { color: @disabled; }
QComboBox { background: @surface; border: 1px solid @border; border-radius: 4px; padding: 5px 8px; min-height: @control_height; }
QComboBox:focus { border-color: @accent; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: @raised; border: 1px solid @border; selection-background-color: @selected; }
QListWidget, QTreeWidget, QTableWidget { background: @surface; alternate-background-color: @background; border: 1px solid @border; border-radius: 4px; outline: none; selection-background-color: @selected; selection-color: @text; }
QListWidget::item { padding: 6px 8px; border-bottom: 1px solid @border; }
QListWidget::item:selected, QTreeWidget::item:selected { background: @selected; color: @text; }
QListWidget::item:hover { background: @raised; }
QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid @border; }
QTableWidget::item:focus { border: 1px solid @accent; }
QHeaderView::section { background: @raised; color: @muted; padding: 6px 8px; border: none; border-bottom: 1px solid @border; font-weight: 400; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: @background; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @border; border-radius: 3px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: @muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 10px; background: @background; }
QScrollBar::handle:horizontal { background: @border; border-radius: 3px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar { border: none; background: @border; border-radius: 2px; min-height: 5px; max-height: 16px; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 2px; }
QCheckBox { spacing: 7px; background: transparent; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid @muted; background: @surface; border-radius: 3px; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url(@check_icon); }
QCheckBox:focus { color: @accent; }
QSplitter::handle { background: @background; width: 6px; height: 6px; }
QSplitter::handle:hover { background: @border; }
QTabWidget::pane { border: 1px solid @border; }
QTabBar::tab { background: @background; color: @muted; padding: 6px 12px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @text; border-bottom-color: @accent; }
QTabBar::tab:focus { border-color: @accent; }
QToolTip { background: @raised; color: @text; border: 1px solid @border; padding: 6px; }
QMenu { background: @raised; color: @text; border: 1px solid @border; padding: 4px; }
QMenu::item { padding: 6px 22px; }
QMenu::item:selected { background: @selected; }
QMenu::separator { height: 1px; background: @border; margin: 4px 6px; }
QStatusBar { background: @surface; color: @muted; border-top: 1px solid @border; padding: 2px 8px; font-size: 9pt; }
QGroupBox { border: 1px solid @border; border-radius: 6px; margin-top: 12px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; padding: 0 6px; }
"""
    values = {**palette, "control_height": f"{TOKENS['control_height']}px",
              "check_icon": (Path(__file__).parent.parent / "assets" / "icons" / "check.svg").as_posix()}
    for key, value in values.items():
        style = style.replace("@" + key, value)
    return style


def markdown_stylesheet():
    return ("body { font-family: 'Segoe UI Variable', 'Segoe UI'; } "
            f"pre, code {{ font-family: '{MONO_FONT}', 'Consolas', monospace; background: {COLORS['surface']}; }} "
            f"a {{ color: {COLORS['accent']}; }} th {{ background: {COLORS['raised']}; }} "
            f"td, th {{ padding: 5px; border: 1px solid {COLORS['border']}; }} "
            "h1 { font-size: 15pt; } h2 { font-size: 12pt; } h3 { font-size: 10pt; }")


STYLESHEET = stylesheet()
