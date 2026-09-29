"""Real Qt flows for local evidence, recovery, scoped context and explicit tabs."""
from PySide6.QtWidgets import QDialog

from jarvix.ui.chat import PermissionDialog
from jarvix.ui.window import CommandPalette
from jarvix.ui.widgets import evidence_text
from test_ui import app as app, wait_until, window as window


def test_document_action_shows_citations_and_continues_same_revision(window, app, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    path = root / "lesson.md"
    path.write_text("\n".join(f"Fact {i} about gravity" for i in range(25)), encoding="utf-8")
    window.services.add_file_root(str(root))
    window.services.scan_files()
    window.navigate("Files")
    page = window.pages["Files"]
    page.entries.selectRow(0)
    page.inspect_document()
    dialog = window.capability_dialog
    assert dialog.selected_tool == "documents.extract" and dialog.worker is None
    dialog.run_action()
    wait_until(app, lambda: dialog.worker is None)
    assert "Fact 0" in dialog.sources.toPlainText() and "Source:" in dialog.sources.toPlainText()
    assert dialog.continuation[1]["expected_revision"]
    dialog.next_page()
    wait_until(app, lambda: dialog.worker is None)
    assert "Fact 24" in dialog.sources.toPlainText() and dialog.continuation is None
    assert not window.services.list_conversations()


def test_palette_reads_tabs_only_on_request_and_drafts_without_sending(window, app, monkeypatch):
    visits = []
    window.services.settings.set("browser.control_enabled", True)
    monkeypatch.setattr(window.services.browser.bridge, "tabs", lambda: visits.append(True) or [
        {"id": "9", "title": "Selected reference", "url": "https://example.com"}])
    palette = CommandPalette(window)
    assert not visits
    assert any(category == "Integration" for _, category, _ in palette.entries)
    palette.load_tabs()
    wait_until(app, lambda: palette.browser_worker is None)
    assert visits == [True]
    palette.search.setText("Selected reference")
    palette.execute()
    assert window.capability_dialog.selected_tool == "browser.tab_switch"
    assert window.capability_dialog.worker is None
    second = CommandPalette(window)
    second.search.setText("Summarize the lesson I selected")
    second.draft()
    assert window.pages["Chat"].composer.toPlainText() == "Summarize the lesson I selected"
    assert not window.services.list_conversations()
    palette.deleteLater()
    second.deleteLater()


def test_context_revoke_clears_temporary_entries_and_memory_explains_source(window):
    s = window.services
    s.settings.set("context.enabled", True)
    s.context.remember_session("temporary research")
    window.navigate("Settings")
    settings = window.pages["Settings"]
    settings.access_checks["context.enabled"].setChecked(False)
    settings.save()
    s.settings.set("context.enabled", True)
    assert not s.context.session()["items"]
    memory = s.add_memory("My chosen project is Jarvix")
    window.navigate("Memory")
    page = window.pages["Memory"]
    page.entries.selectRow(0)
    page.explain()
    assert window.capability_dialog.selected_tool == "memory.explain"
    assert window.capability_dialog.form.arguments() == {"id": memory}


def test_partial_session_exposes_recovery_and_owned_undo(window, app, monkeypatch):
    s = window.services
    s.settings.set("control.enabled", True)
    s.permissions.set_grant("tasks.list", "deny")
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    window.open_operator()
    dialog = window.operator_dialog
    plan = {"goal": "Write then inspect", "steps": [
        {"id": "note", "tool": "notes.create", "arguments": {"title": "Owned", "body": "Text"}},
        {"id": "read", "tool": "tasks.list", "arguments": {}}]}
    dialog.start(lambda **kwargs: s.operator.run(plan, **kwargs))
    wait_until(app, lambda: dialog.worker is None)
    assert dialog.replan_button.isEnabled()
    assert s.operator.get(dialog.session_id)["partial_completion"]
    dialog.steps.setCurrentRow(0)
    assert dialog.undo_button.isEnabled()
    rendered = evidence_text({"sections": [{"text": "<script>untrusted</script>", "citation": {"source": "mail"}}]})
    assert "<script>untrusted</script>" in rendered and '"source": "mail"' in rendered
