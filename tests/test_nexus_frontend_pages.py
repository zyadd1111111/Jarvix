"""Fresh Nexus workspaces keep original service and permission contracts."""
from contextlib import contextmanager

from PySide6.QtCore import QCoreApplication, QEvent, Qt

from jarvix.ui.nexus_frontend.pages import (
    NexusAppsPage, NexusAutomationsPage, NexusFilesPage, NexusNotesPage,
    NexusProjectsPage, NexusSettingsPage, NexusSystemPage, NexusTasksPage,
)
from test_ui import app as app, window as window, wait_until


@contextmanager
def workspace(window, app, factory):
    page = factory(window)
    window.stack.addWidget(page)
    window.stack.setCurrentWidget(page)
    page.refresh()
    app.processEvents()
    wait_until(app, lambda: not window.jobs)
    try:
        yield page
    finally:
        wait_until(app, lambda: not window.jobs)
        window.stack.setCurrentWidget(window.pages["Home"])
        window.stack.removeWidget(page)
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_fresh_file_browser_lists_real_folders_and_preserves_destructive_preview(window, app, tmp_path):
    folder = tmp_path / "documents"
    folder.mkdir()
    nested = folder / "Research"
    nested.mkdir()
    document = folder / "report.md"
    document.write_text("Private local report", encoding="utf-8")
    window.services.add_file_root(str(folder))
    with workspace(window, app, NexusFilesPage) as page:
        assert [page.entries.horizontalHeaderItem(i).text() for i in range(4)] == ["Name", "Type", "Modified", "Size"]
        assert page.entries.rowCount() == 2
        assert page.preview.isHidden()
        row = next(i for i in range(2) if page.entries.item(i, 0).text() == "Research")
        page.open_entry(row)
        wait_until(app, lambda: not window.jobs)
        assert page.location == str(nested) and page.entries.rowCount() == 0
        page.go_location(-1)
        wait_until(app, lambda: not window.jobs)
        assert page.location == str(folder)
        row = next(i for i in range(2) if page.entries.item(i, 0).text() == "report.md")
        page.entries.selectRow(row)
        page.file_action("recycle")
        preview = window.capability_dialog
        assert preview.selected_tool == "files.recycle" and preview.worker is None
        assert preview.form.arguments() == {"path": str(document)}
        preview.reject()
        assert document.exists()
        assert window.services.list_files() == []  # Browsing does not index or upload content.


def test_file_navigation_refuses_outside_roots_and_revoked_history(window, app, tmp_path, monkeypatch):
    folders = [tmp_path / name for name in ("School", "Work", "Private")]
    for folder in folders:
        folder.mkdir()
    for folder in folders[:2]:
        window.services.add_file_root(str(folder))
    notices = []
    monkeypatch.setattr(window, "notify", notices.append)
    with workspace(window, app, NexusFilesPage) as page:
        page.navigate_location(str(folders[1]))
        wait_until(app, lambda: not window.jobs)
        assert not page.navigate_location(str(folders[2]))
        assert page.location == str(folders[1])
        window.services.remove_file_root(str(folders[0]))
        page.go_location(-1)
        assert page.location == str(folders[1]) and page.location_position == 1
        assert notices and "approved" in notices[-1]


def test_workflow_workspace_uses_real_steps_and_confirmed_save(window, app):
    workflow = window.services.workflows.save("Morning review", [{"kind": "action", "tool": "tasks.list", "arguments": {}}])
    with workspace(window, app, NexusAutomationsPage) as page:
        assert page.flow_title.text() == "Morning review"
        assert page.flow_steps.blocks.count() == 1
        assert page.flow_steps.steps()[0]["tool"] == "tasks.list"
        page.flow_steps.append({"kind": "action", "tool": "projects.list", "arguments": {}})
        page.refresh()
        assert page.flow_steps.blocks.count() == 2
        assert len(window.services.workflows.get(workflow["id"])["steps"]) == 1
        calls = []
        original = window.open_capabilities
        window.open_capabilities = lambda tool, args: calls.append((tool, args))
        try:
            page.save_sequence()
        finally:
            window.open_capabilities = original
        assert calls[0][0] == "workflows.save"
        assert calls[0][1]["id"] == workflow["id"]
        assert len(calls[0][1]["steps"]) == 2
        assert window.services.registry.get("workflows.save").permission_level == 3
        assert not window.services.workflows.get(workflow["id"])["enabled"]


def test_settings_has_fresh_categories_and_theme_persistence_without_new_permissions(window, app, monkeypatch):
    changes = []
    monkeypatch.setattr(window, "configure_materials", lambda: changes.append(True), raising=False)
    with workspace(window, app, NexusSettingsPage) as page:
        assert page.categories.count() == 12
        assert page.interface_selector.itemText(page.interface_selector.findData("new")) == "NEXUS"
        page.nexus_theme.setCurrentIndex(page.nexus_theme.findData("graphite"))
        assert window.services.settings.get("liquid.theme") == "graphite" and changes
        page.motion_enabled.setChecked(False)
        page.reduced_motion.setChecked(True)
        assert not window.services.settings.get("liquid.motion")
        assert window.services.settings.get("liquid.reduced_motion")
        assert not window.services.settings.get("control.enabled", False)
        assert not window.services.settings.get("screenshots.enabled", False)
        page.filter_categories("Appearance")
        assert page.categories.currentItem().text() == "Appearance"
        assert all(page.categories.item(i).isHidden() for i in range(12) if page.categories.item(i).text() != "Appearance")
        page.refresh()
        assert page.nexus_theme.currentData() == "graphite"


def test_project_workspace_reads_registered_resources_and_previews_terminal(window, app, tmp_path, monkeypatch):
    folder = tmp_path / "project"
    folder.mkdir()
    window.services.add_file_root(str(folder))
    project_id = window.services.add_project("Jarvix", str(folder))
    with workspace(window, app, NexusProjectsPage) as page:
        wait_until(app, lambda: not window.jobs)
        assert page.project_name.text() == "Jarvix"
        assert [page.project_tabs.tabText(i) for i in range(6)] == ["Overview", "Files", "Tasks", "Knowledge", "Conversations", "Activity"]
        assert page.project_views["Overview"].rowCount() == 6
        calls = []
        monkeypatch.setattr(window, "open_capabilities", lambda tool, args: calls.append((tool, args)))
        page.open_terminal()
        assert calls == [("workspaces.open_terminal", {"path": str(folder)})]
        assert window.services.list_projects()[0]["id"] == project_id
        assert not window.services.settings.get("control.enabled", False)


def test_task_views_and_checkboxes_keep_original_completion_flow(window, app):
    window.services.add_task("Review repository")
    with workspace(window, app, NexusTasksPage) as page:
        page.view.setCurrentIndex(page.view.findData("all"))
        assert page.entries.rowCount() == 1
        assert page.entries.item(0, 1).text() == "Review repository"
        page.entries.item(0, 0).setCheckState(Qt.CheckState.Checked)
        assert window.services.list_tasks()[0]["status"] == "done"
        page.view.setCurrentIndex(page.view.findData("completed"))
        assert page.entries.rowCount() == 1


def test_system_composition_keeps_sortable_process_rows(window, app):
    window.snapshot = {"cpu_percent": 12, "memory_percent": 42, "processes": [{"name": "editor", "pid": 19, "memory_mb": 140}]}
    with workspace(window, app, NexusSystemPage) as page:
        assert page.cpu_value.text() == "12.0%"
        assert page.processes.item(0, 0).text() == "editor"
        assert page.processes.isSortingEnabled()
        assert [page.system_tabs.tabText(i) for i in range(4)] == ["Processes", "Storage", "Network", "Devices"]


def test_note_library_preserves_full_document_and_folder_filter(window, app):
    body = "Keep the entire document.\n" * 80
    note_id = window.services.save_note("Release notes", body)
    window.services.productivity.notes.organize(note_id, folder="Jarvix", tags=["release"])
    window.services.save_note("School notes", "Other work")
    with workspace(window, app, NexusNotesPage) as page:
        page.note_folders.setCurrentIndex(page.note_folders.findData("Jarvix"))
        assert page.list.count() == 1
        page.list.setCurrentRow(0)
        assert page.body.toPlainText() == body
        page.title_edit.setText("Updated release notes")
        page.save()
        assert next(note for note in window.services.list_notes() if note["id"] == note_id)["body"] == body
        assert page.note_metadata.topLevelItem(1).text(1) == "release"


def test_app_launcher_reads_aliases_favorites_and_recent_launches(window, app, tmp_path):
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"Test fixture only")
    app_id = window.services.add_app("Editor", str(executable))
    window.services.apps.configure(app_id, favorite=True, aliases=["code"])
    window.services.apps.record_launch(app_id)
    with workspace(window, app, NexusAppsPage) as page:
        for mode in (0, 1, 2):
            page.launcher_view.setCurrentIndex(mode)
            assert page.entries.rowCount() == 1
            assert page.entries.item(0, 1).text() == "code"
        page.entries.selectRow(0)
        page.launch()
        assert window.capability_dialog.selected_tool == "apps.open"
        assert window.capability_dialog.worker is None
        window.capability_dialog.reject()
