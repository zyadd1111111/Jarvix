"""Fresh Nexus navigation uses the controller's conversations, state and guards."""
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog

from jarvix.services import Services
from jarvix.ui.liquid.navigation import conversation_group
from jarvix.ui.nexus_frontend.window import PRIMARY, NexusWindow
from test_interface_modes import opened_interface
from test_ui import NoVault, app as app, wait_until


def test_history_grouping_handles_pinned_and_local_calendar_dates():
    today = date(2026, 10, 10)
    for days, expected in ((0, "Today"), (1, "Yesterday"), (4, "Previous 7 days"), (8, "Older")):
        value = datetime.combine(today - timedelta(days=days), datetime.min.time()).astimezone().isoformat()
        assert conversation_group({"updated_at": value}, today) == expected
    assert conversation_group({"pinned": True}, today) == "Pinned"
    assert conversation_group({"updated_at": "unknown"}, today) == "Older"


def test_nexus_shell_history_search_links_and_keyboard_use_shared_state(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        assert isinstance(window, NexusWindow) and window.controller.services is window.services
        services = window.services
        one = services.new_conversation("Release notes")
        two = services.new_conversation("Browser research")
        services.repository.append_message(two, "user", "OAuth browser context")
        services.conversations.configure(one, pinned=True)
        window.refresh_history()
        assert [window.history.item(i).text() for i in range(window.history.count())][:2] == ["Pinned", "Release notes"]
        window.search.setText("OAuth")
        wait_until(app, lambda: not window.search_timer.isActive())
        ids = [window.history.item(i).data(Qt.ItemDataRole.UserRole) for i in range(window.history.count())]
        assert two in ids and one not in ids
        item = next(window.history.item(i) for i in range(window.history.count()) if window.history.item(i).data(Qt.ItemDataRole.UserRole) == two)
        window.open_history(item)
        chat = window.pages["Chat"]
        assert window.current_page == "Chat" and chat.conversation_id == two
        folder = tmp_path / "repository"
        folder.mkdir()
        project = services.add_project("Jarvix", str(folder))
        mission = services.missions.save("Prepare the release")
        monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][0], True))
        chat.choose_project()
        assert next(row for row in services.conversations.search() if row["id"] == two)["project_id"] == project
        previews = []
        monkeypatch.setattr(window.controller, "open_capabilities", lambda name, arguments: previews.append((name, arguments)))
        chat.choose_mission()
        assert previews[0][0] == "context.link" and previews[0][1]["target"]["reference"] == mission["id"]
        assert not services.records.list("context.link")
        window.activateWindow()
        app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.pages["Chat"].conversation_id is None
        QTest.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.current_page == "Settings"
        window.toggle_sidebar(True)
        wait_until(app, lambda: not getattr(window.sidebar, "_liquid_animations", {}))
        assert window.history.isHidden() and window.search.isHidden()
        assert window.nav_buttons["Voice"].isHidden() and window.nav_buttons["Memory"].isHidden()
        assert {action.text() for action in window.more_menu.actions()} >= {"Voice", "Memory", "Tasks", "Notes"}
        assert not services.settings.get("control.enabled", False)


def test_navigation_selection_and_note_guard_survive_startup_theme_and_sidebar_motion(app, tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    services = Services(profile, vault=NoVault())
    services.settings.set("ui.last_page", "Settings")
    services.close()
    with opened_interface(app, monkeypatch, profile, "new") as window:
        def assert_selection():
            assert window.nav_buttons[window.current_page].isChecked()
            assert sum(control.isChecked() for control in window.nav_buttons.values()) == 1
            assert window.stack.currentWidget() is window.pages[window.current_page]

        assert window.current_page == "Settings"
        assert_selection()
        window.resize(1280, 720)
        app.processEvents()
        assert_selection()
        window.services.settings.set("liquid.theme", "graphite")
        window.configure_materials()
        app.processEvents()
        assert_selection()
        window.navigate("Notes")
        assert_selection()
        with monkeypatch.context() as denied:
            denied.setattr(window.controller, "guard", lambda callback: False)
            window.navigate("Home")
            assert window.current_page == "Notes"
        for index in range(8):
            window.services.new_conversation(f"Conversation {index}")
        window.refresh_history()
        app.processEvents()
        assert_selection()
        for collapsed in (True, False):
            window.toggle_sidebar(collapsed)
            wait_until(app, lambda: not getattr(window.sidebar, "_liquid_animations", {}))
            assert window.services.settings.get("nexus.sidebar_collapsed") is collapsed
            assert_selection()
        for page in ("Home", "Projects"):
            window.navigate(page)
            assert_selection()
        assert [name for name in PRIMARY if window.nav_buttons[name].isVisible()] == list(PRIMARY)
