"""Continuum review flows use the real facade and existing permission workers."""
from PySide6.QtWidgets import QDialog, QPlainTextEdit
import json

from jarvix.ui.chat import PermissionDialog
from jarvix.ui.window import CommandPalette
from test_ui import app as app, window as window, wait_until


def test_saved_work_views_inspect_local_records_and_open_existing_actions(window, app, monkeypatch):
    s = window.services
    conversation = s.new_conversation("Release work")
    checkpoint = s.continuity.save("Prepare the next release", conversation_id=conversation,
                                   next_action={"description": "Check tasks", "tool": "tasks.list", "arguments": {}})
    profile = s.context.save_profile("Daily work", conversation_id=conversation)
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    window.open_adaptive("Continue")
    dialog = window.adaptive_dialog
    view = dialog.saved_work["Continue"]
    wait_until(app, lambda: not window.jobs and "Prepare the next release" in view.details.toPlainText())
    view.action("prepare")
    view.action("start_observations")
    assert calls == [("continuity.prepare", {"id": checkpoint["id"]}),
                     ("continuity.start_observations", {"id": checkpoint["id"]})]
    assert not s.operator.list()  # Opening a review never starts work.
    view.query.setText("No match")
    assert not view.items.count() and all(not control.isEnabled() for control in view.controls)
    window.open_adaptive("Personal context")
    view = dialog.saved_work["Personal context"]
    wait_until(app, lambda: not window.jobs and "Daily work" in view.details.toPlainText())
    view.action("activate")
    assert calls[-1] == ("context.profiles.activate", {"id": profile["id"]})
    assert s.context._current["profile_id"] is None
    names = {entry[0] for entry in CommandPalette(window).entries}
    assert {"Continue saved work", "Personal context profiles", "Learned skills", "Review latest checkpoint"} <= names
    dialog.close()


def test_declined_continuation_preview_reports_not_started(window, app, monkeypatch):
    s = window.services
    checkpoint = s.continuity.save("Check pending tasks", conversation_id=s.new_conversation(),
        next_action={"description": "Review", "tool": "tasks.list", "arguments": {}})
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    window.open_capabilities("continuity.start_observations", {"id": checkpoint["id"]})
    dialog = window.capability_dialog
    dialog.run_action()
    wait_until(app, lambda: dialog.worker is None)
    assert "not approved" in dialog.status.text().casefold() and not s.operator.list()
    dialog.close()


def test_skill_review_carries_exact_source_to_fresh_confirmation_without_running(window, app, monkeypatch):
    s = window.services
    routine = s.workflows.save("Daily note", [{"kind": "action", "tool": "notes.create",
        "arguments": {"title": "Daily", "body": "Reviewed literal text"}}], kind="routine", enabled=False)
    window.open_capabilities("skills.learn_preview", {"name": "Daily note", "routine_id": routine["id"]})
    dialog = window.capability_dialog
    dialog.run_action()
    wait_until(app, lambda: dialog.worker is None)
    assert dialog.reviewed_skill["routine_id"] == routine["id"]
    assert not dialog.save_skill_button.isHidden()
    dialog.review_skill()
    assert dialog.selected_tool == "skills.save" and not dialog.worker
    arguments = dialog.form.arguments()
    assert arguments["review_fingerprint"] and not s.skills.list()["items"]
    previews = []
    def deny(confirmation):
        previews.append(confirmation.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(PermissionDialog, "exec", deny)
    dialog.run_action()
    wait_until(app, lambda: dialog.worker is None)
    assert "Reviewed literal text" in previews[0] and "notes.create" in previews[0]
    assert not s.skills.list()["items"] and not s.list_notes()
    monkeypatch.setattr(PermissionDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    dialog.run_action()
    wait_until(app, lambda: dialog.worker is None)
    assert len(s.skills.list()["items"]) == 1 and not s.list_notes()
    window.open_adaptive("Skills")
    view = window.adaptive_dialog.saved_work["Skills"]
    wait_until(app, lambda: not window.jobs and "recorded_runs" in view.details.toPlainText())
    assert view.items.count() == 1 and '"recorded_runs": 0' in view.details.toPlainText()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    view.action("learn_preview")
    assert calls == [("skills.learn_preview", {"id": s.skills.list()["items"][0]["id"],
        "name": "Daily note", "skill_id": s.skills.list()["items"][0]["id"]})]
    dialog.close()
    window.adaptive_dialog.close()


def test_operator_checkpoint_and_learning_link_selected_verified_session(window, monkeypatch):
    s = window.services
    completed = s.operator.run({"goal": "Create a note", "steps": [{"id": "note", "tool": "notes.create",
        "arguments": {"title": "Result", "body": "Saved once"}}]}, approve=lambda _: True)
    window.open_operator()
    dialog = window.operator_dialog
    dialog.open_session(completed["id"])
    assert dialog.learn_button.isEnabled() and dialog.save_progress_button.isEnabled()
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    dialog.save_progress()
    dialog.learn_session()
    assert calls == [("continuity.save", {"summary": "Create a note", "operator_session_id": completed["id"]}),
                     ("skills.learn_preview", {"name": "Create a note", "session_id": completed["id"]})]
    assert len(s.list_notes()) == 1 and not s.skills.list()["items"]
    dialog.close()


def test_maintenance_views_read_real_local_status_without_side_effects(window, app, monkeypatch):
    s = window.services
    calls = []
    monkeypatch.setattr(window, "open_capabilities", lambda name, args=None: calls.append((name, args)))
    window.open_adaptive("Daily brief")
    dialog = window.adaptive_dialog
    for title, expected in (("Daily brief", "enabled"), ("Devices", "listener_started"),
                            ("Updates", "automatic_install"), ("Health", "actions_performed")):
        window.open_adaptive(title)
        view = dialog.saved_work[title]
        wait_until(app, lambda view=view: not window.jobs and bool(view.result.toPlainText()))
        value = json.loads(view.result.toPlainText())
        assert value[expected] is False
    assert not calls and not s.settings.get("daily.enabled", False)
    assert not s.records.list("device_peer") and not s.records.list("update_release")
    window.pages["Settings"].create_backup()
    assert calls == [("backup.create", None)]
    window.open_adaptive("Backups")
    view = dialog.saved_work["Backups"]
    wait_until(app, lambda: not window.jobs and bool(view.result.toPlainText()))
    assert json.loads(view.result.toPlainText())["items"] == []
    s.permissions.set_grant("backup.list", "deny")
    view.refresh()
    wait_until(app, lambda: not window.jobs)
    assert not view.result.toPlainText() and "denied" in view.status.text().casefold()
    dialog.close()


def test_knowledge_ui_rechecks_source_permission_and_clears_revoked_data(window, app):
    s = window.services
    note = s.save_note("Private knowledge", "Should disappear when source access is revoked")
    space = s.knowledge_spaces.create("Private collection")
    s.knowledge_spaces.add_source(space["id"], "note", note)
    window.open_adaptive("Knowledge")
    dialog = window.adaptive_dialog
    wait_until(app, lambda: not window.jobs and dialog.sources.count() == 1)
    s.permissions.set_grant("knowledge_spaces.get", "deny")
    dialog.select_space()
    wait_until(app, lambda: not window.jobs)
    assert not dialog.sources.count()
    s.permissions.set_grant("knowledge_spaces.list", "deny")
    dialog.refresh()
    wait_until(app, lambda: not window.jobs)
    assert not dialog.spaces.count() and not dialog.sources.count()
    dialog.close()
