"""Liquid OS tokens. Styles are attached to NEW windows only."""
from pathlib import Path

COLORS = {
    "canvas": "#e7e3da", "surface": "#f4f0e7", "raised": "#fcf9f1",
    "text": "#2b302d", "muted": "#696e64", "border": "#cfcfc3",
    "accent": "#356f65", "selected": "#dce8df", "success": "#3b7654",
    "warning": "#8a631e", "error": "#a7423c", "disabled": "#92998f",
}
# Every palette owns its canvas, glass tints, semantic ink and control surfaces.
# These values never modify the OLD theme or Qt's application-wide palette.
THEMES = {
    "cream": {
        **COLORS, "muted": "#5d6359", "canvas_light": "#f3efe5", "canvas_shade": "#e2dfd5",
        "highlight": "#fffdf5", "edge": "#969f8f", "shadow": "#2a392d",
        "primary_hover": "#2b5f57", "primary_text": "#f9faf4", "selection_ink": "#244b43",
        "code": "#e9eee4", "disabled_surface": "#e1e4da", "row_hover": "#e2ebe0",
        "header": "#e8ecde", "scrollbar": "#b5c1b4", "danger_border": "#d5b8ad",
        "glass_surface": "#f2eee4", "glass_panel": "#faf6ec", "glass_sidebar": "#faf7ef",
        "glass_toolbar": "#fcf9f1", "glass_command": "#fffcf5",
        "glass_popover": "#fcfbf5", "glass_dialog": "#fcfbf5",
    },
    "frost": {
        "canvas": "#e3e7eb", "surface": "#f1f4f6", "raised": "#fafcfd",
        "text": "#26313b", "muted": "#566470", "border": "#c6cfd7",
        "accent": "#436c90", "selected": "#dae6ef", "success": "#347254",
        "warning": "#826018", "error": "#a53e43", "disabled": "#929ba4",
        "canvas_light": "#f0f4f7", "canvas_shade": "#d9e0e6", "highlight": "#ffffff",
        "edge": "#899dac", "shadow": "#263646", "primary_hover": "#365a79",
        "primary_text": "#f8fbfd", "selection_ink": "#2a4c6c", "code": "#e4ebf1",
        "disabled_surface": "#dfe5ea", "row_hover": "#dde7ef", "header": "#e0e8ef",
        "scrollbar": "#b0beca", "danger_border": "#d0b3b8",
        "glass_surface": "#eaf0f4", "glass_panel": "#f4f8fb", "glass_sidebar": "#edf4f8",
        "glass_toolbar": "#f6fafc", "glass_command": "#fafcfe",
        "glass_popover": "#f5f9fc", "glass_dialog": "#f8fbfd",
    },
    "graphite": {
        "canvas": "#23282b", "surface": "#30373b", "raised": "#3b454a",
        "text": "#e7edeb", "muted": "#becbc7", "border": "#556361",
        "accent": "#79bfb0", "selected": "#34534b", "success": "#96c9a6",
        "warning": "#e4c383", "error": "#eea39e", "disabled": "#869391",
        "canvas_light": "#30383b", "canvas_shade": "#1d2225", "highlight": "#b6c8c5",
        "edge": "#6c827b", "shadow": "#071313", "primary_hover": "#9ccfc3",
        "primary_text": "#15332c", "selection_ink": "#e4f3ed", "code": "#293432",
        "disabled_surface": "#303938", "row_hover": "#354642", "header": "#34413e",
        "scrollbar": "#657c75", "danger_border": "#956761",
        "glass_surface": "#2b3436", "glass_panel": "#354043", "glass_sidebar": "#303a3e",
        "glass_toolbar": "#3b484a", "glass_command": "#414f51",
        "glass_popover": "#3b4749", "glass_dialog": "#3c474a",
    },
    "sand": {
        "canvas": "#e6dfd2", "surface": "#f1ebdf", "raised": "#faf5e9",
        "text": "#35332b", "muted": "#605a49", "border": "#ccc5b3",
        "accent": "#596f52", "selected": "#e0e7d7", "success": "#476f48",
        "warning": "#87611f", "error": "#a33f35", "disabled": "#9c9686",
        "canvas_light": "#f2ecde", "canvas_shade": "#dbd2bf", "highlight": "#fff8e6",
        "edge": "#a39d83", "shadow": "#3d3b27", "primary_hover": "#485c43",
        "primary_text": "#faf8ee", "selection_ink": "#3e5236", "code": "#e8e4d5",
        "disabled_surface": "#e1ddce", "row_hover": "#e5e8d8", "header": "#e8e5d5",
        "scrollbar": "#bebca5", "danger_border": "#d2b09e",
        "glass_surface": "#eee7d6", "glass_panel": "#f6efdd", "glass_sidebar": "#f3ecd9",
        "glass_toolbar": "#f9f2e2", "glass_command": "#fcf6e7",
        "glass_popover": "#f8f3e4", "glass_dialog": "#faf4e8",
    },
}
TOKENS = {"spacing": (4, 8, 12, 16, 24), "radius": 12, "control_radius": 7,
          "font_size": 10, "control_height": 28, "duration": 180, "blur": 18,
          "shadow": 18, "sidebar_width": 244, "sidebar_collapsed": 58,
          "conversation_width": 940, "composer_max_height": 180,
          "settings_categories_width": 152, "easing": "OutCubic",
          "duration_fast": 140, "duration_panel": 210, "slide": 6,
          "font_family": "Segoe UI Variable", "mono_family": "Cascadia Mono",
          "timeline_row_height": 70, "timeline_rail_width": 36,
          "workspace_library_width": 210, "workspace_inspector_width": 280,
          "workspace_breakpoint": 1000, "surface_inset": 12,
          "canvas_light": "#f3efe5", "canvas_shade": "#e2dfd5",
          "edge_light": "#fffdf5", "edge_shade": "#969f8f"}
# tint, opacity, blur, radius: each level has a distinct physical weight.
MATERIALS = {
    "surface": ("#f2eee4", 104, 12, 7, 1),
    "panel": ("#faf6ec", 174, 18, 12, 2),
    "sidebar": ("#faf7ef", 152, 18, 14, 2),
    "toolbar": ("#fcf9f1", 208, 18, 10, 3),
    "command": ("#fffcf5", 222, 18, 14, 3),
    "popover": ("#fcfbf5", 241, 22, 12, 4),
    "dialog": ("#fcfbf5", 250, 22, 14, 5),
}


def palette(preferences=None):
    preferences = preferences or {}
    name = preferences.get("theme", "cream")
    values = THEMES.get(name if isinstance(name, str) else "cream", THEMES["cream"]).copy()
    dark = preferences.get("theme") == "graphite"
    if preferences.get("accent") in ("teal", "slate"):
        overrides = {
            (False, "teal"): ("#356f65", "#dce8df", "#2b5f57", "#244b43", "#f9faf4"),
            (False, "slate"): ("#486979", "#dce5e8", "#3b5867", "#334f5d", "#f9faf4"),
            (True, "teal"): ("#79bfb0", "#34534b", "#9ccfc3", "#e4f3ed", "#15332c"),
            (True, "slate"): ("#9cbace", "#384c5b", "#bad1df", "#e4eef5", "#1c303d"),
        }[(dark, preferences["accent"])]
        values.update(zip(("accent", "selected", "primary_hover", "selection_ink", "primary_text"), overrides, strict=True))
    return values


def ink(preferences, role):
    return palette(preferences)[role]


def material(preferences, name):
    return (ink(preferences, "glass_" + name), *MATERIALS[name][1:])


def rgba(color, opacity):
    channels = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    return f"rgba({channels[0]},{channels[1]},{channels[2]},{opacity})"


def stylesheet(preferences=None):
    preferences = preferences or {}
    compact = preferences.get("density") == "compact"
    values = {**palette(preferences), "height": str(24 if compact else TOKENS["control_height"]),
              "pad": "4" if compact else "6"}
    for name, role, opacity in (
        ("row_border", "border", 110), ("user_surface", "selected", 160),
        ("panel_surface", "glass_panel", 150), ("control_surface", "raised", 210),
        ("disabled_fill", "disabled_surface", 160), ("hover_fill", "row_hover", 210),
        ("input_surface", "glass_command", 205), ("table_surface", "glass_surface", 120),
        ("alternate_surface", "selected", 90), ("header_surface", "header", 190),
    ):
        values[name] = rgba(values[role], opacity)
    assets = Path(__file__).parent.parent.parent / "assets"
    dark = preferences.get("theme") == "graphite"
    values.update(
        chevron=(assets / ("liquid/chevron-down-dark.svg" if dark else "nexus/chevron-down.svg")).as_posix(),
        check=(assets / ("liquid/check-dark.svg" if dark else "nexus/check.svg")).as_posix(),
    )
    qss = """
QWidget { background: transparent; color: @text; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 10pt; }
QMainWindow { background: @canvas; }
QDialog { background: @surface; }
QLabel { background: transparent; }
QLabel#Title { font-size: 17pt; font-weight: 600; }
QLabel#Heading, QLabel#SectionTitle { font-size: 11pt; font-weight: 600; }
QLabel#LiquidGoal, QLabel#LiquidGreeting { font-size: 18pt; font-weight: 600; }
QLabel#Muted, QLabel#Subtitle { color: @muted; font-size: 9pt; }
QLabel#Eyebrow, QLabel#SidebarGroup { color: @muted; font-size: 8pt; font-weight: 600; }
QLabel#Brand { font-size: 14pt; font-weight: 600; }
QLabel#Code, QTextEdit#Code, QPlainTextEdit#Code { font-family: 'Cascadia Mono', Consolas; font-size: 9pt; }
QLabel#Warning { color: @warning; } QLabel#Success { color: @success; } QLabel#Error { color: @error; }
QFrame#Divider { background: @border; max-height: 1px; }
QFrame#LiquidDivider { background: @border; border: none; max-height: 1px; }
QFrame#LiquidIntegrationRow, QFrame#LiquidSettingRow { background: transparent; border: none; border-bottom: 1px solid @row_border; }
QLabel#LiquidStatus { color: @muted; font-size: 9pt; }
QFrame[liquidMessage="user"] { background: @user_surface; border: 1px solid @row_border; border-radius: 10px; }
QFrame[liquidMessage="response"] { background: transparent; border: none; }
QFrame#Panel { background: @panel_surface; border: 1px solid @row_border; border-radius: 10px; }
QFrame#Sidebar, QWidget#SidebarContent, QScrollArea#SidebarScroll, QFrame#Topbar { background: transparent; border: none; }
QPushButton, QToolButton { background: @control_surface; border: 1px solid @border; border-radius: 7px; min-height: @heightpx; padding: @padpx 10px; }
QPushButton:hover, QToolButton:hover { background: @raised; border-color: @edge; }
QPushButton:pressed, QToolButton:pressed { background: @selected; }
QPushButton:focus, QToolButton:focus { border: 1px solid @accent; }
QPushButton:disabled, QToolButton:disabled { color: @disabled; background: @disabled_fill; border-color: @border; }
QPushButton#Primary { background: @accent; color: @primary_text; border-color: @accent; }
QPushButton#Primary:hover { background: @primary_hover; }
QPushButton#Primary:disabled { color: @disabled; background: @disabled_fill; border-color: @border; }
QPushButton#Danger { color: @error; border-color: @danger_border; }
QPushButton#Danger:disabled { color: @disabled; border-color: @border; }
QPushButton#Quiet { background: transparent; border-color: transparent; }
QPushButton#Quiet:hover { background: @hover_fill; }
QPushButton#Quiet:focus { border-color: @accent; }
QPushButton[liquidRow="true"] { background: transparent; border: 1px solid transparent; text-align: left; padding: 5px 2px; min-height: 20px; }
QPushButton[liquidRow="true"]:hover { background: @hover_fill; }
QPushButton[liquidRow="true"]:focus { border-color: @accent; }
QPushButton[liquidCompact="true"] { min-height: 20px; padding: 3px 7px; font-size: 9pt; }
QPushButton#Navigation { background: transparent; border: 1px solid transparent; text-align: left; padding: 4px 8px; min-height: 23px; }
QPushButton#Navigation:hover { background: @hover_fill; }
QPushButton#Navigation:checked { background: transparent; color: @selection_ink; border-color: transparent; }
QPushButton#Navigation:focus { border-color: @accent; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: @input_surface; border: 1px solid @border; border-radius: 7px; padding: 7px; selection-background-color: @selected; selection-color: @text; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: @accent; }
QLineEdit:disabled { color: @disabled; }
QLineEdit#CommandInput { background: transparent; border: none; font-size: 12pt; padding: 6px; min-height: 26px; }
QPlainTextEdit#Composer, QTextBrowser#MessageText { background: transparent; border: none; }
QTextBrowser#MessageText { padding: 0; }
QComboBox { background: @input_surface; border: 1px solid @border; border-radius: 7px; padding: 5px 8px; min-height: @heightpx; }
QComboBox:focus { border-color: @accent; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: url(@chevron); width: 14px; height: 14px; }
QComboBox QAbstractItemView, QMenu { background: @raised; border: 1px solid @border; border-radius: 8px; padding: 4px; selection-background-color: @selected; }
QMenu::item { padding: 6px 24px 6px 10px; border-radius: 4px; }
QMenu::item:selected { background: @selected; }
QMenu::item:disabled { color: @disabled; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }
QListWidget, QTreeWidget, QTableWidget { background: @table_surface; border: none; outline: 0; selection-background-color: @selected; selection-color: @text; alternate-background-color: @alternate_surface; }
QListWidget::item { padding: 7px 8px; border-bottom: 1px solid @row_border; }
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover { background: @hover_fill; }
QListWidget::item:focus, QTreeWidget::item:focus, QTableWidget::item:focus { border: 1px solid @accent; }
QTableWidget::item, QTreeWidget::item { padding: 5px 7px; border-bottom: 1px solid @row_border; }
QHeaderView::section { background: @header_surface; color: @muted; border: none; border-bottom: 1px solid @border; padding: 6px 8px; font-size: 9pt; }
QScrollArea, QStackedWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 9px; }
QScrollBar::handle:vertical { background: @scrollbar; border-radius: 4px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: @muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: @scrollbar; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar { background: @selected; border: none; border-radius: 3px; min-height: 6px; max-height: 12px; }
QProgressBar::chunk { background: @accent; border-radius: 3px; }
QCheckBox { spacing: 7px; } QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid @muted; border-radius: 4px; background: @raised; }
QCheckBox::indicator:checked { background: @accent; image: url(@check); }
QCheckBox:focus { color: @accent; }
QSplitter::handle { background: transparent; width: 10px; height: 10px; }
QSplitter::handle:hover { background: @hover_fill; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 7px 12px; color: @muted; background: transparent; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @text; border-color: @accent; }
QTabBar::tab:focus { color: @accent; }
QToolTip { background: @raised; color: @text; border: 1px solid @border; padding: 6px; }
QStatusBar { color: @muted; background: transparent; border: none; font-size: 9pt; }
QGroupBox { border: 1px solid @border; border-radius: 8px; margin-top: 12px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; padding: 0 8px; }
"""
    for key in sorted(values, key=len, reverse=True):
        qss = qss.replace("@" + key, values[key])
    return qss


def markdown_stylesheet(preferences=None):
    values = palette(preferences)
    return (f"body {{color: {values['text']}; font-family: 'Segoe UI Variable', 'Segoe UI';}} "
            f"pre,code {{color: {values['text']}; font-family: 'Cascadia Mono', Consolas; background: {values['code']};}} "
            f"a {{color: {values['accent']};}} th {{background: {values['header']};}} "
            f"td,th {{padding: 5px; border: 1px solid {values['border']};}}")
