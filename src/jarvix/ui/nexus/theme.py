"""Warm desktop tokens; scoped to the Nexus window, never QApplication."""
from pathlib import Path

COLORS = {
    "canvas": "#eae7df", "surface": "#f4f2ec", "raised": "#faf9f4",
    "text": "#292e2c", "muted": "#646963", "border": "#c9cbc2",
    "accent": "#346f67", "selected": "#dde7df", "success": "#3a7554",
    "warning": "#95651e", "error": "#aa443c", "disabled": "#94978f",
}
TOKENS = {"spacing": (4, 8, 12, 16, 24), "radius": 12, "control_radius": 7,
          "duration": 180, "blur": 18, "shadow": 10, "control_height": 26}
# strength, blur, elevation: denser layers float above quieter embedded surfaces.
MATERIALS = {
    "surface": (174, 12, 0), "panel": (198, 18, 1), "sidebar": (168, 18, 2),
    "toolbar": (210, 18, 2), "command": (224, 18, 3),
    "popover": (238, 22, 4), "dialog": (248, 22, 5),
}


def stylesheet():
    style = """
QWidget { color: @text; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 10pt; background: transparent; }
QMainWindow { background: @canvas; }
QDialog { background: @raised; border: 1px solid @border; }
QLabel { background: transparent; }
QLabel#Brand { font-size: 12pt; font-weight: 600; }
QLabel#Title { font-size: 18pt; font-weight: 600; }
QLabel#Heading, QLabel#SectionHeader, QLabel#SectionTitle { font-size: 11pt; font-weight: 600; }
QLabel#NexusGoal { font-size: 16pt; font-weight: 600; }
QLabel#NexusGreeting { font-size: 16pt; font-weight: 600; }
QLabel#MessageAuthor { font-weight: 600; }
QLabel#Muted, QLabel#Subtitle, QLabel#Caption { color: @muted; font-size: 9pt; }
QLabel#Eyebrow, QLabel#SidebarGroup { color: @muted; font-size: 8pt; }
QLabel#Metric { font-size: 16pt; font-weight: 600; }
QLabel#Code, QPlainTextEdit, QLabel#Technical, QLineEdit#ModelIdentifier { font-family: 'Cascadia Mono', 'Consolas'; }
QPlainTextEdit#Composer, QPlainTextEdit#KnowledgeContent, QTextBrowser#MessageText { font-family: 'Segoe UI Variable', 'Segoe UI'; }
QLabel#Success { color: @success; } QLabel#Error { color: @error; }
QFrame#Panel { background: rgba(250,249,244,190); border: 1px solid @border; border-radius: 12px; }
QFrame#Message, QFrame#Section { background: transparent; border: none; }
QFrame#UserMessage { background: rgba(248,247,241,200); border: 1px solid @border; border-radius: 10px; }
QFrame#ToolRow { background: rgba(240,242,234,150); border: none; border-bottom: 1px solid @border; }
QPushButton, QToolButton { background: rgba(251,250,245,190); border: 1px solid @border; border-radius: 7px; padding: 4px 10px; min-height: 26px; }
QPushButton:hover, QToolButton:hover { background: @raised; border-color: @muted; }
QPushButton:pressed, QToolButton:pressed { background: @selected; }
QPushButton:focus, QToolButton:focus { border-color: @accent; }
QPushButton:disabled, QToolButton:disabled { color: @disabled; background: @surface; border-color: @border; }
QPushButton#Primary { background: @accent; color: #f9faf5; border-color: @accent; }
QPushButton#Primary:hover { background: #285b54; }
QPushButton#Primary:disabled { background: @surface; color: @disabled; border-color: @border; }
QPushButton#Quiet { background: transparent; border-color: transparent; }
QPushButton#Quiet:hover { background: rgba(252,251,246,190); border-color: @border; }
QPushButton#Quiet:focus { border-color: @accent; }
QPushButton#Danger { color: @error; }
QPushButton[nexusRow="true"] { text-align: left; min-height: 18px; padding: 3px 6px; }
QPushButton#Navigation { background: transparent; border: 1px solid transparent; text-align: left; padding: 2px 8px; min-height: 23px; }
QPushButton#Navigation:hover { background: rgba(253,252,247,160); }
QPushButton#Navigation:checked { background: rgba(233,239,231,220); border-color: rgba(255,255,250,230); }
QPushButton#Navigation:focus { border-color: @accent; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: rgba(252,251,246,190); border: 1px solid @border; border-radius: 7px; padding: 6px 8px; selection-background-color: @selected; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: @accent; }
QLineEdit:disabled { color: @disabled; }
QLineEdit#CommandInput { background: transparent; border: none; font-size: 13pt; padding: 8px; min-height: 36px; }
QPlainTextEdit#Composer { background: transparent; border: none; }
QTextBrowser#MessageText { background: transparent; border: none; padding: 0; }
QComboBox { background: rgba(251,250,245,200); border: 1px solid @border; border-radius: 7px; padding: 5px 8px; min-height: 26px; }
QComboBox:focus { border-color: @accent; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: url(@chevron_icon); width: 14px; height: 14px; }
QComboBox QAbstractItemView, QMenu { background: @raised; border: 1px solid @border; border-radius: 8px; padding: 4px; selection-background-color: @selected; }
QMenu::item { padding: 6px 20px; } QMenu::item:selected { background: @selected; border-radius: 4px; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }
QListWidget, QTreeWidget, QTableWidget { background: rgba(250,249,244,100); alternate-background-color: rgba(224,226,216,50); border: 1px solid @border; border-radius: 8px; selection-background-color: @selected; selection-color: @text; outline: none; }
QListWidget::item { padding: 6px 8px; border-bottom: 1px solid rgba(190,195,184,80); }
QListWidget::item:hover { background: rgba(254,253,248,200); }
QListWidget::item:selected, QTreeWidget::item:selected { background: @selected; color: @text; }
QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid rgba(190,195,184,80); }
QTableWidget::item:focus { border: 1px solid @accent; }
QHeaderView::section { background: rgba(235,237,226,180); color: @muted; padding: 7px 8px; border: none; border-bottom: 1px solid @border; }
QScrollArea, QStackedWidget, QFrame#Sidebar, QWidget#SidebarContent, QScrollArea#SidebarScroll { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #b4b9ad; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: @muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #b4b9ad; border-radius: 4px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar { background: #d5ddcf; border: none; border-radius: 3px; min-height: 5px; max-height: 12px; }
QProgressBar::chunk { background: @accent; border-radius: 3px; }
QCheckBox { spacing: 7px; } QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid @muted; border-radius: 4px; background: @raised; }
QCheckBox::indicator:checked { background: @accent; image: url(@check_icon); }
QCheckBox:focus { color: @accent; }
QSplitter::handle { background: transparent; width: 8px; height: 8px; }
QSplitter::handle:hover { background: rgba(173,189,174,80); }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 6px 12px; color: @muted; background: transparent; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @text; border-color: @accent; }
QToolTip { background: @raised; color: @text; border: 1px solid @border; padding: 6px; }
QStatusBar { color: @muted; background: transparent; border: none; font-size: 9pt; }
QGroupBox { border: 1px solid @border; border-radius: 10px; margin-top: 12px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; padding: 0 8px; }
"""
    assets = Path(__file__).parent.parent.parent / "assets" / "nexus"
    for key, value in {**COLORS, "chevron_icon": (assets / "chevron-down.svg").as_posix(),
                      "check_icon": (assets / "check.svg").as_posix()}.items():
        style = style.replace("@" + key, value)
    return style


def markdown_stylesheet():
    return ("body { color: #292e2c; font-family: 'Segoe UI Variable', 'Segoe UI'; } "
            "pre, code { color: #292e2c; font-family: 'Cascadia Mono', 'Consolas'; background: #e9ece3; } "
            "a { color: #346f67; } th { background: #e5e9de; } "
            "td, th { padding: 5px; border: 1px solid #c9cbc2; }")
