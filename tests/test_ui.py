"""Offscreen verification of real desktop flows and worker/permission boundaries."""
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

from jarvix.domain import PermissionRequest
from jarvix.services import Services
from jarvix.ui.chat import PermissionDialog
from jarvix.ui.window import CommandPalette, MainWindow, NAVIGATION


class NoVault:
    def get(self, _):
        return None


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    yield instance
    instance.closeAllWindows()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    instance.processEvents()


def wait_until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
        time.sleep(.005)
    app.processEvents()
    assert predicate(), "Desktop operation did not finish before timeout"


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # Periodic OS work is tested separately; keep UI scenarios deterministic.
    monkeypatch.setattr(MainWindow, "refresh_system", lambda self: None)
    monkeypatch.setattr(MainWindow, "run_routines", lambda self: None)
    services = Services(tmp_path / "profile", vault=NoVault())
    instance = MainWindow(services)
    instance.show()
    app.processEvents()
    yield instance
    instance.pages["Chat"].cancel()
    wait_until(app, lambda: not instance.jobs and not instance.pages["Chat"].busy)
    instance.close()
    instance.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    services.close()


def test_every_workspace_page_navigates_and_renders(window, app):
    assert len(NAVIGATION) == 13
    for name, _ in NAVIGATION:
        window.navigate(name)
        app.processEvents()
        assert window.current_page == name
        assert window.stack.currentWidget() is window.pages[name]
        assert window.nav_buttons[name].isChecked()
        assert not window.grab().isNull()


def test_note_task_and_memory_flows_persist(window):
    window.navigate("Notes")
    notes = window.pages["Notes"]
    notes.title_edit.setText("Architecture")
    notes.body.setPlainText("Tools use structured arguments.")
    notes.save()
    assert window.services.list_notes()[0]["body"] == "Tools use structured arguments."
    notes.body.setPlainText("Keep data local.")
    window.navigate("Tasks")
    assert window.services.list_notes()[0]["body"] == "Keep data local."
    tasks = window.pages["Tasks"]
    tasks.entry.setText("Verify Jarvix")
    tasks.add()
    tasks.entries.selectRow(0)
    tasks.complete()
    assert window.services.list_tasks()[0]["status"] == "done"
    window.navigate("Memory")
    memory = window.pages["Memory"]
    memory.content.setText("Jarvix is my main project.")
    memory.add()
    assert window.services.list_memories()[0]["content"] == "Jarvix is my main project."


def test_failed_note_save_preserves_editor_and_navigation(window, monkeypatch):
    window.navigate("Notes")
    notes = window.pages["Notes"]
    notes.title_edit.setText("Unwritten")
    notes.body.setPlainText("Must survive a failed save")
    def unavailable(*args, **kwargs):
        raise OSError("Storage unavailable")
    monkeypatch.setattr(window.services, "save_note", unavailable)
    notes.new()
    assert notes.body.toPlainText() == "Must survive a failed save"
    window.navigate("Home")
    assert window.current_page == "Notes"
    assert notes.dirty
    # Teardown may close after this intentionally failed save.
    notes.dirty = False


def test_chat_without_key_finishes_and_persists(window, app):
    window.open_chat("Help me organize this project")
    chat = window.pages["Chat"]
    wait_until(app, lambda: chat.worker is None)
    rows = window.services.conversation_messages(chat.conversation_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert "API key" in rows[-1]["content"]
    assert "Integrations" in rows[-1]["content"]
    assert chat.send_button.isEnabled()
    assert not chat.stop.isEnabled()


@pytest.mark.parametrize("accepted", [False, True])
def test_permission_bridge_runs_dialog_on_ui_thread(window, app, monkeypatch, accepted):
    decisions = []
    dialog_threads = []
    request = PermissionRequest("execute", "notes.create", "local.write", "Create one note", {"title": "Example"})
    def fake_chat(*args, approve, on_event, cancel):
        decisions.append(approve(request))
        return "Permission resolved"
    def decide(self):
        dialog_threads.append(QThread.currentThread())
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(window.services, "chat", fake_chat)
    monkeypatch.setattr(PermissionDialog, "exec", decide)
    window.open_chat("Create one note")
    wait_until(app, lambda: window.pages["Chat"].worker is None)
    assert decisions == [accepted]
    assert dialog_threads == [app.thread()]


def test_close_waits_for_worker_without_running_completion_ui(window, app):
    release = threading.Event()
    started = threading.Event()
    delivered = []
    def work():
        started.set()
        release.wait(3)
        return "done"
    window.run_job(work, delivered.append)
    wait_until(app, started.is_set)
    assert window.close() is False
    assert window.isVisible()
    release.set()
    wait_until(app, lambda: not window.jobs and not window.isVisible())
    assert delivered == []


def test_command_palette_searches_and_opens_note(window):
    note_id = window.services.save_note("Gemini experiments", "A private note")
    palette = CommandPalette(window)
    palette.search.setText("Gemini experiments")
    assert palette.results.count() == 1
    palette.execute()
    assert window.current_page == "Notes"
    assert window.pages["Notes"].note_id == note_id
    palette = CommandPalette(window)
    palette.search.setText("system.status")
    assert palette.results.count() >= 1
    palette.execute()
    assert window.capability_dialog.selected_tool == "system.status"
    window.capability_dialog.close()
    assert not window.pages["Chat"].busy


def test_context_preview_excludes_disabled_tools_and_unrelated_notes(window, monkeypatch):
    previews = []
    window.services.save_note("Private", "Never automatically included")
    window.services.settings.set("tools.enabled", ["system.status"])
    class Preview:
        def __init__(self, title, description, text, parent):
            previews.append(json.loads(text))
        def exec(self):
            return 0
    monkeypatch.setattr("jarvix.ui.chat.TextPreview", Preview)
    window.pages["Chat"].inspect_context()
    assert [tool["name"] for tool in previews[0]["tools"]] == ["system.status"]
    assert "Never automatically included" not in json.dumps(previews[0])


def test_file_root_revocation_removes_indexed_files(window, tmp_path):
    folder = tmp_path / "approved"
    folder.mkdir()
    (folder / "project.txt").write_text("local", encoding="utf-8")
    window.services.add_file_root(str(folder))
    window.services.scan_files()
    window.navigate("Files")
    page = window.pages["Files"]
    assert page.entries.rowCount() == 1
    page.remove_root()
    assert window.services.file_roots() == []
    assert page.entries.rowCount() == 0
    assert (folder / "project.txt").exists()


def test_voice_reports_engine_unavailable_and_polls_completion(window, app, monkeypatch):
    class Voice:
        status = "No supported local speech engine was found."
        is_speaking = False
        def stop(self):
            self.is_speaking = False
        def close(self):
            pass
    monkeypatch.setattr(window.services, "voice", Voice())
    monkeypatch.setattr(window.services, "speak", lambda text: False)
    page = window.pages["Voice"]
    page.text.setPlainText("Example text")
    page.speak()
    wait_until(app, lambda: not window.jobs)
    assert "No supported" in page.status.text()
    assert not page.status_timer.isActive()
    window.services.voice.status = "Speaking locally."
    window.services.voice.is_speaking = True
    page.speech_started(True)
    assert page.status_timer.isActive()
    window.services.voice.status = "Local speech is ready."
    window.services.voice.is_speaking = False
    wait_until(app, lambda: not page.status_timer.isActive())
    assert page.status.text() == "Local speech is ready."
    assert window.pages["Settings"].rate.maximum() == 300


def test_automation_latest_result_is_inspectable(window, monkeypatch):
    routine_id = window.services.add_automation("My tasks", "tasks.list", {}, 60)
    window.services.run_due_automations()
    previews = []
    class Preview:
        def __init__(self, title, description, text, parent):
            previews.append(json.loads(text))
        def exec(self):
            return 0
    monkeypatch.setattr("jarvix.ui.pages.TextPreview", Preview)
    window.navigate("Automations")
    page = window.pages["Automations"]
    page.entries.selectRow(0)
    page.view_result()
    assert previews == [window.services.settings.get("automation.result." + routine_id)]
    assert previews[0]["ok"] is True


def conversation_with_two_turns(services):
    record_id = services.new_conversation("Coding session")
    for role, text in (("user", "Explain functions"), ("assistant", "Functions name a reusable operation."),
                       ("user", "Give a Python example"), ("assistant", "def greet(): return 'Hello'")):
        services.repository.append_message(record_id, role, text)
    return record_id


def test_edit_branches_prior_context_without_mutating_original_or_sending(window):
    original = conversation_with_two_turns(window.services)
    chat = window.pages["Chat"]
    chat.load_conversation(original)
    chat.revise_message(2)
    assert chat.conversation_id != original
    assert chat.composer.toPlainText() == "Give a Python example"
    assert len(window.services.conversation_messages(original)) == 4
    assert [row["role"] for row in window.services.conversation_messages(chat.conversation_id)] == ["user", "assistant"]
    assert not chat.busy


def test_regenerate_keeps_original_and_reuses_only_prior_context(window, app):
    original = conversation_with_two_turns(window.services)
    chat = window.pages["Chat"]
    chat.load_conversation(original)
    chat.regenerate()
    wait_until(app, lambda: not chat.busy)
    revised = window.services.conversation_messages(chat.conversation_id)
    assert chat.conversation_id != original
    assert len(revised) == 4
    assert revised[-2]["content"] == "Give a Python example"
    assert "API key" in revised[-1]["content"]
    assert window.services.conversation_messages(original)[-1]["content"] == "def greet(): return 'Hello'"


def test_conversation_rename_pin_search_and_context_switch(window, monkeypatch):
    record_id = conversation_with_two_turns(window.services)
    chat = window.pages["Chat"]
    chat.load_conversation(record_id)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Python reference", True))
    chat.rename_conversation()
    chat.pin_conversation()
    chat.history_search.setText("reusable operation")
    assert chat.history.count() == 1
    assert chat.history.item(0).text().startswith("★ Python reference")
    chat.on_activity("tool_result", {"name": "tasks.list", "data": []})
    assert chat.timeline.count() == 1
    chat.new_conversation()
    assert chat.timeline.count() == 0
    assert chat.last_answer == ""
    assert window.services.records.get("conversation_meta", record_id)["pinned"]


class LocalMicrophone:
    def __init__(self):
        self.values = {"active": False, "busy": False, "status": "Microphone off", "transcript": "", "revision": 0}
        self.starts = 0
        self.cancellations = 0

    def state(self):
        return dict(self.values)

    def start(self, device=None, silence_timeout=2):
        self.starts += 1
        self.values.update(active=True, busy=True, status="Listening locally", transcript="")
        return {"listening": True}

    def finish(self):
        self.values.update(active=False, busy=True, status="Transcribing locally")

    def cancel(self):
        self.cancellations += 1
        self.values.update(active=False, busy=False, status="Microphone off", transcript="")

    def close(self):
        self.cancel()


def install_microphone(window, monkeypatch):
    microphone = LocalMicrophone()
    panel = window.pages["Voice"].input_panel
    monkeypatch.setattr(window.services, "microphone", microphone)
    monkeypatch.setattr(panel, "microphone", microphone)
    window.navigate("Voice")
    return microphone, panel


def test_microphone_transcript_is_reviewed_locally_and_never_auto_sent(window, monkeypatch):
    microphone, panel = install_microphone(window, monkeypatch)
    microphone.values.update(transcript="Delete my notes", revision=1, status="Ready to review")
    panel.refresh()
    panel.refresh()
    assert panel.transcript.toPlainText() == "Delete my notes"
    assert not window.services.list_conversations()
    monkeypatch.setattr(window.pages["Chat"], "send", lambda: pytest.fail("Voice transcript was sent without review"))
    panel.review()
    assert window.current_page == "Chat"
    assert window.pages["Chat"].composer.toPlainText() == "Delete my notes"
    assert not window.services.list_conversations()
    assert microphone.cancellations


def test_microphone_opt_in_and_revocation_cancel_continuous_mode(window, app, monkeypatch):
    microphone, panel = install_microphone(window, monkeypatch)
    panel.press()
    assert microphone.starts == 0
    assert "access is off" in panel.status.text()
    window.services.settings.set("microphone.enabled", True)
    panel.continuous.setChecked(True)
    wait_until(app, lambda: not window.jobs)
    assert microphone.starts == 1
    panel.refresh()
    assert "MICROPHONE ACTIVE" in panel.status.text()
    settings = window.pages["Settings"]
    settings.refresh()
    settings.access_checks["microphone.enabled"].setChecked(False)
    settings.save()
    panel.refresh()
    assert not panel.continuous.isChecked()
    assert not microphone.state()["active"]
    assert microphone.starts == 1


def test_close_stops_microphone_polling_and_acquisition(window, monkeypatch):
    microphone, panel = install_microphone(window, monkeypatch)
    microphone.values.update(active=True, busy=True)
    window.close()
    assert not panel.timer.isActive()
    assert microphone.cancellations
    assert not microphone.state()["active"]


def test_integration_status_is_real_and_data_folder_does_not_expand_file_roots(window, app, monkeypatch):
    window.navigate("Integrations")
    integrations = window.pages["Integrations"].account_status
    assert integrations.rowCount() == 6
    assert all(integrations.item(row, 1).text() == "Not connected" for row in range(6))
    opened = []
    monkeypatch.setattr(window.services, "open_data_folder", lambda: opened.append(window.services.data_dir))
    window.pages["Settings"].open_data_folder()
    wait_until(app, lambda: not window.jobs)
    assert opened == [window.services.data_dir]
    assert not window.services.file_roots()


def test_existing_tool_selection_changes_only_when_settings_are_saved(window):
    window.services.settings.set("tools.enabled", ["system.status"])
    window.navigate("Settings")
    settings = window.pages["Settings"]
    assert sum(check.isChecked() for check in settings.tool_checks.values()) == 1
    settings.tool_search.setText("clipboard")
    assert all(check.isHidden() == ("clipboard" not in name) for name, check in settings.tool_checks.items())
    settings.select_tools(True)
    assert window.services.settings.get("tools.enabled") == ["system.status"]
    settings.save()
    assert set(window.services.settings.get("tools.enabled")) == set(settings.tool_checks)
    settings.select_tools(False)
    settings.save()
    assert window.services.settings.get("tools.enabled") == []


def test_app_launch_uses_permission_checked_tool_form(window, tmp_path, monkeypatch):
    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"test application fixture")
    record_id = window.services.add_app("Fixture app", str(executable))
    monkeypatch.setattr(window.services, "launch_app", lambda _: pytest.fail("UI bypassed permission dispatch"))
    window.navigate("Apps")
    page = window.pages["Apps"]
    page.entries.selectRow(0)
    page.launch()
    assert window.capability_dialog.selected_tool == "apps.open"
    assert window.capability_dialog.form.arguments() == {"id": record_id}
    assert window.capability_dialog.worker is None
