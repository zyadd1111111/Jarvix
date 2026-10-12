"""NEW utility pages preserve real controllers, disclosures and local preferences."""
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from jarvix.domain import ToolResult
from jarvix.ui.integrations import ConnectionDialog
from jarvix.ui.nexus_frontend.pages import (
    NexusAppsPage, NexusAutomationsPage, NexusFilesPage, NexusSettingsPage, NexusSystemPage,
)
from jarvix.ui.nexus_frontend.utilities import NexusIntegrationsPage
from test_interface_modes import opened_interface
from test_ui import app as app, wait_until


def test_liquid_files_keep_index_preview_and_permissioned_actions(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        folder = tmp_path / "documents"
        folder.mkdir()
        document = folder / "report.md"
        document.write_text("Private local report", encoding="utf-8")
        window.services.add_file_root(str(folder))
        window.services.scan_files()
        page = window.pages["Files"]
        assert isinstance(page, NexusFilesPage)
        window.navigate("Files")
        wait_until(app, lambda: not window.jobs)
        page.entries.selectRow(0)
        assert page.preview_title.text() == "report.md"
        assert page.file_metadata.topLevelItem(0).text(1) == str(document)
        page.file_action("recycle")
        dialog = window.capability_dialog
        assert dialog.selected_tool == "files.recycle" and dialog.worker is None
        assert dialog.form.arguments() == {"path": str(document)}
        assert window.services.registry.get("files.recycle").permission_level == 3
        dialog.reject()
        assert document.exists()
        page.search.setText("no-such-file")
        wait_until(app, lambda: page.entries.rowCount() == 0)
        assert page.preview_title.text() == "File details"
        assert [page.entries.horizontalHeaderItem(i).text() for i in range(4)] == ["Name", "Type", "Modified", "Size"]


def test_liquid_file_navigation_never_restores_revoked_folder_access(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        services = window.services
        folders = [tmp_path / "School", tmp_path / "Work"]
        for folder in folders:
            folder.mkdir()
            services.add_file_root(folder)
        page = window.pages["Files"]
        page.refresh()
        page.navigate_location(str(folders[1]))
        page.go_location(-1)
        assert page.location == str(folders[0])
        page.go_location(1)
        assert page.location == str(folders[1])
        services.remove_file_root(str(folders[0]))
        page.refresh()
        notices = []
        monkeypatch.setattr(window, "notify", notices.append)
        page.go_location(-1)
        assert page.location == str(folders[1])
        assert services.file_roots() == [str(folders[1])]
        assert notices and "approved" in notices[0]


def test_liquid_app_library_filters_existing_aliases_favorites_and_launch_history(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        services = window.services
        executable = tmp_path / "editor.exe"
        executable.write_bytes(b"UI fixture only")
        favorite = services.add_app("Editor", str(executable))
        other = tmp_path / "other.exe"
        other.write_bytes(b"Another UI fixture")
        services.add_app("Other application", str(other))
        services.apps.configure(favorite, favorite=True, aliases=["coding"])
        services.apps.record_launch(favorite)
        page = window.pages["Apps"]
        page.refresh()
        assert page.entries.rowCount() == 2
        for mode in (1, 2):
            page.launcher_view.setCurrentIndex(mode)
            assert page.entries.rowCount() == 1
            assert page.entries.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"] == favorite
        page.search.setText("coding")
        assert page.entries.rowCount() == 1
        assert not services.settings.get("control.enabled", False)


def test_liquid_system_detail_views_use_read_only_jobs_and_recover_after_failure(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        page = window.pages["System"]
        page._details_busy = True
        window.navigate("System")
        page._details_busy = False
        healthy = False
        calls = []
        def execute(name, arguments):
            calls.append((name, arguments))
            return ToolResult(True, {"adapters": [{"name": "Local adapter", "connected": True}]}) if healthy else ToolResult(False, error="Inspection unavailable")
        monkeypatch.setattr(window.services, "execute_tool", execute)
        page.refresh()
        wait_until(app, lambda: not window.jobs)
        assert page.device_status["Network"].text() == "Unavailable · Inspection unavailable"
        healthy = True
        page._details_updated = None
        page.refresh()
        wait_until(app, lambda: not window.jobs)
        assert calls == [(name, {}) for _ in range(2) for name in ("system.gpu", "system.battery", "system.storage", "system.network")]
        assert all(window.services.registry.get(name).permission_level == 1 for name, _ in calls)
        assert page.device_status["Network"].text() == "Local adapter" and not page._details_busy


def test_liquid_location_rail_and_contextual_panels_keep_shared_state(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        for name in ("School", "Work"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / (name + ".txt")).write_text(name, encoding="utf-8")
            window.services.add_file_root(str(folder))
        window.services.scan_files()
        window.resize(1920, 1080)
        window.navigate("Files")
        page = window.pages["Files"]
        page.refresh()
        wait_until(app, lambda: not window.jobs)
        assert page.location_list.count() == 2
        assert page.root_picker.isHidden()
        page.location_list.setCurrentRow(1)
        wait_until(app, lambda: not window.jobs)
        assert page.location == str(tmp_path / "Work")
        assert page.entries.item(0, 0).text() == "Work.txt"
        assert page.preview.isHidden()
        page.entries.selectRow(0)
        page.toggle_preview()
        wait_until(app, lambda: not getattr(page.preview, "_liquid_animations", {}))
        assert not page.preview.isHidden() and not page.file_metadata.isHidden()
        window.resize(1280, 720)
        app.processEvents()
        page.navigate_location(str(tmp_path / "School"))
        wait_until(app, lambda: not window.jobs)
        assert page.entries.item(0, 0).text() == "School.txt"
        page.entries.selectRow(0)
        assert page.file_metadata.topLevelItem(0).text(1) == str(tmp_path / "School" / "School.txt")
        page.entries.clearSelection()
        assert page.file_metadata.topLevelItemCount() == 0
        window.navigate("Integrations")
        accounts = window.pages["Integrations"]
        assert accounts.account_status.rowCount() == 6
        assert all(accounts.account_status.item(i, 1).text() == "Not connected" for i in range(6))
        assert not accounts.connections


def test_liquid_apps_and_workflows_route_existing_reviewed_operations(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        executable = tmp_path / "fixture.exe"
        executable.write_bytes(b"not executable; launcher UI fixture")
        app_id = window.services.add_app("Fixture editor", str(executable))
        apps = window.pages["Apps"]
        assert isinstance(apps, NexusAppsPage)
        window.navigate("Apps")
        apps.entries.selectRow(0)
        assert apps.app_name.text() == "Fixture editor"
        assert apps.app_path.text() == str(executable)
        assert not apps.entries.item(0, 0).icon().isNull()
        apps.launch()
        assert window.capability_dialog.selected_tool == "apps.open"
        assert window.capability_dialog.form.arguments() == {"id": app_id}
        assert window.capability_dialog.worker is None
        window.capability_dialog.reject()
        workflow = window.services.workflows.save("Review today", [
            {"kind": "action", "tool": "tasks.list", "arguments": {}},
        ])
        page = window.pages["Automations"]
        assert isinstance(page, NexusAutomationsPage)
        window.navigate("Automations")
        page.workflow_entries.selectRow(0)
        assert page.flow_title.text() == "Review today"
        assert page.flow_steps.steps()[0]["tool"] == "tasks.list"
        page.workflow_action("run")
        assert window.capability_dialog.selected_tool == "workflows.run"
        assert window.capability_dialog.form.arguments() == {"id": workflow["id"]}
        assert window.capability_dialog.worker is None
        window.capability_dialog.reject()


def test_liquid_account_cards_show_verified_state_without_connecting(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        page = window.pages["Integrations"]
        assert isinstance(page, NexusIntegrationsPage)
        window.navigate("Integrations")
        page.refresh()
        statuses = {row["id"]: row for row in window.services.integrations.status()}
        assert set(page.cards) == set(statuses)
        for key, (state, account, connect, manage, disconnect, cancel) in page.cards.items():
            assert state.text() == statuses[key]["status"] == "Not connected"
            assert account.text() == "No verified account"
            assert page.account_status.item(page.account_rows[key], 3).text() == "Not granted"
            assert not connect.isHidden() and manage.isHidden() and disconnect.isHidden() and cancel.isHidden()
        monkeypatch.setattr(ConnectionDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
        page.configure("github")
        assert page.connections == {}
        assert window.services.integrations.status() == list(statuses.values())


def test_liquid_settings_preferences_are_isolated_from_access_and_nexus(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        page = window.pages["Settings"]
        assert isinstance(page, NexusSettingsPage)
        window.navigate("Settings")
        settings = window.services.settings
        permissions = {key: settings.get(key, default) for key, default in page.access_defaults.items()}
        assert [page.glass_intensity.itemData(index) for index in range(page.glass_intensity.count())] == ["frosted", "subtle", "solid"]
        page.glass_intensity.setCurrentIndex(page.glass_intensity.findData("subtle"))
        assert settings.get("liquid.glass") == "subtle"
        page.glass_intensity.setCurrentIndex(page.glass_intensity.findData("solid"))
        page.transparency.setChecked(False)
        page.reduced_motion.setChecked(True)
        page.density.setCurrentIndex(page.density.findData("compact"))
        page.accent.setCurrentIndex(page.accent.findData("slate"))
        assert settings.get("liquid.glass") == "solid"
        assert settings.get("liquid.transparency") is False and settings.get("liquid.reduced_motion") is True
        assert not window._liquid_preferences["motion"]
        assert settings.get("liquid.density") == "compact" and settings.get("liquid.accent") == "slate"
        assert settings.get("nexus.glass") is None and settings.get("nexus.motion") is None
        assert {key: settings.get(key, default) for key, default in page.access_defaults.items()} == permissions
        assert [page.interface_selector.itemText(i) for i in range(page.interface_selector.count())] == ["OLD", "NEXUS"]
        appearance_index = next(index for index in range(page.categories.count())
                                if page.categories.item(index).text() == "Appearance")
        assert page.settings_stack.widget(appearance_index).isAncestorOf(page.interface_selector)
        assert page.settings_split.widget(0).isAncestorOf(page.categories)
        assert page.settings_split.widget(1) is page.settings_stack
        page.category_search.setText("privacy")
        assert page.categories.currentItem().text() == "Privacy"
        page.category_search.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        page.save()
        assert settings.get("provider") == page.provider.currentText()


def test_liquid_small_utility_layouts_and_process_inspection(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as window:
        window.resize(860, 600)
        page = window.pages["System"]
        assert isinstance(page, NexusSystemPage)
        window.snapshot = {"cpu_percent": 12.5, "memory_percent": 48.0,
                           "processes": [{"name": "FixtureProcess", "pid": 4512, "memory_mb": 24.5}]}
        page.refresh_processes()
        page.processes.selectRow(0)
        assert page.processes.item(0, 0).text() == "FixtureProcess"
        assert page.processes.item(0, 1).text() == "4512"
        page.inspect_process()
        assert window.capability_dialog.selected_tool == "processes.details"
        assert window.capability_dialog.form.arguments() == {"pid": 4512}
        window.capability_dialog.reject()
        for name in ("Files", "Apps", "Automations", "Integrations", "Settings"):
            window.navigate(name)
            wait_until(app, lambda: not window.jobs)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            assert not window.pages[name].grab().isNull()
            assert window.width() <= 860
        assert window.pages["Files"].preview.isHidden()
        assert window.pages["Apps"].app_inspector.isHidden()
        settings = window.pages["Settings"]
        assert settings.categories.horizontalScrollBar().maximum() == 0
        for index in range(settings.categories.count()):
            item = settings.categories.item(index)
            assert settings.categories.visualItemRect(item).width() >= (
                settings.categories.fontMetrics().horizontalAdvance(item.text())
                + settings.categories.iconSize().width() + 16
            )
