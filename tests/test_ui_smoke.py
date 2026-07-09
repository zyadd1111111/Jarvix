from __future__ import annotations

import os

import pytest


def _make_window(tmp_path, launcher=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")

    from PySide6.QtWidgets import QApplication

    from ai.client import AIClient
    from config.settings import AppSettings
    from memory.notes_store import NotesStore
    from tools.launcher import Launcher
    from tools.system_info import SystemInfoService
    from ui.main_window import JarvixWindow
    from voice.speech_service import SpeechService
    from voice.transcriber import VoiceTranscriber

    app = QApplication.instance() or QApplication([])
    settings = AppSettings.from_environment(tmp_path)
    speech_service = SpeechService()
    speech_service.set_enabled(False)
    window = JarvixWindow(
        settings=settings,
        ai_client=AIClient(settings),
        notes_store=NotesStore(settings.database_path),
        transcriber=VoiceTranscriber(),
        speech_service=speech_service,
        launcher=launcher or Launcher(),
        system_info=SystemInfoService(),
    )
    window.speech_service.set_enabled(False)
    return app, window


def test_main_window_constructs_offscreen(tmp_path) -> None:
    app, window = _make_window(tmp_path)

    window.show()
    app.processEvents()

    assert window.windowTitle() == "Jarvix V.01"
    assert window.stack.count() == 6
    assert window.status_label.text() == "status: idle"

    window.close()


def test_voice_text_submits_to_chat_workflow(tmp_path) -> None:
    app, window = _make_window(tmp_path)

    window._handle_voice_text("what is 2 plus 2")
    app.processEvents()

    chat_text = window._chat_plain_text()
    assert "You: what is 2 plus 2" in chat_text
    assert "Jarvix: 2 + 2 = 4" in chat_text

    window.close()


def test_chat_website_action_uses_launcher_after_confirmation(tmp_path, monkeypatch) -> None:
    from tools.launcher import LaunchResult, Launcher

    class FakeLauncher(Launcher):
        def __init__(self) -> None:
            self.opened: list[str] = []

        def open_website(self, url: str) -> LaunchResult:
            self.opened.append(url)
            return LaunchResult(True, f"Opened {url}.")

    fake_launcher = FakeLauncher()
    app, window = _make_window(tmp_path, launcher=fake_launcher)
    monkeypatch.setattr(window, "_confirm", lambda _text: True)

    window._submit_chat_text("can you open youtube")
    app.processEvents()

    assert fake_launcher.opened == ["https://www.youtube.com"]
    assert "Jarvix: Opened https://www.youtube.com." in window._chat_plain_text()

    window.close()
