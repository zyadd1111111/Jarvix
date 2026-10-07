"""The redesigned shell preserves navigation, drafts and permission boundaries."""
from PySide6.QtWidgets import QDialog
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jarvix.ui.theme import STYLESHEET, stylesheet
from jarvix.ui.window import CommandPalette, SIDEBAR_GROUPS
from test_ui import app as app, window as window, wait_until


def test_grouped_navigation_collapses_without_losing_accessible_actions(window):
    assert [name for name, _ in SIDEBAR_GROUPS] == ["CORE", "WORK", "AUTOMATE", "SYSTEM"]
    window.sidebar_toggle.click()
    assert window.sidebar_collapsed and window.sidebar.width() == 52
    assert window.services.settings.get("ui.sidebar_collapsed") is True
    for name, control in window.nav_buttons.items():
        assert not control.text() and control.accessibleName() == name
        assert control.toolTip() == name and not control.icon().isNull()
    window.sidebar_toggle.click()
    assert not window.sidebar_collapsed and window.sidebar.width() == 174
    assert window.nav_buttons["Projects"].text() == "Projects"
    palette = CommandPalette(window)
    assert "Open Missions" in [title for title, _, _ in palette.entries]
    palette.close()


def test_operator_and_work_surfaces_navigate_inside_one_window(window, app):
    for name in ("Operator", "Missions", "Knowledge", "Skills", "Projects"):
        window.navigate(name)
        wait_until(app, lambda: not window.jobs)
        assert window.stack.currentWidget() is window.pages[name]
        assert not window.pages[name].isWindow()
        assert window.nav_buttons[name].isChecked()
        if isinstance(window.pages[name], QDialog):
            QTest.keyClick(window.pages[name], Qt.Key.Key_Escape)
            assert window.pages[name].isVisible()
            window.pages[name].reject()
            window.navigate(name)
            assert window.pages[name].isVisible()
    window.go_history(-1)
    assert window.current_page == "Skills"
    window.go_history(1)
    assert window.current_page == "Projects"


def test_unsaved_note_failure_also_blocks_embedded_workspace_navigation(window, monkeypatch):
    window.navigate("Notes")
    notes = window.pages["Notes"]
    notes.title_edit.setText("Unwritten draft")
    notes.body.setPlainText("Keep this text")
    monkeypatch.setattr(window.services, "save_note", lambda *a, **kw: (_ for _ in ()).throw(OSError("Storage offline")))
    window.open_adaptive("Missions")
    window.open_operator()
    assert window.current_page == "Notes" and notes.body.toPlainText() == "Keep this text"
    assert window.adaptive_dialog is None and window.operator_dialog is None
    notes.dirty = False


def test_neutral_theme_and_small_laptop_shell(window, app):
    assert all(word not in STYLESHEET.lower() for word in ("gradient", "glow", "roboto", "arial", "inter'"))
    assert "Segoe UI Variable" in STYLESHEET and "Cascadia Mono" in STYLESHEET
    assert "#f5f5f4" in stylesheet("light")
    window.resize(1024, 700)
    window.navigate("Files")
    app.processEvents()
    assert window.width() == 1024 and window.height() == 700
    assert window.pages["Files"].geometry().right() < window.width()
    assert not window.grab().isNull()
