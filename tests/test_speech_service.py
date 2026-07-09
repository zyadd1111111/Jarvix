from __future__ import annotations

from voice.speech_service import SpeechService


def test_speech_service_disabled_does_not_start_thread(monkeypatch) -> None:
    service = SpeechService()
    service.set_enabled(False)
    called = False

    def fake_speak(_text: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(service, "_speak_blocking", fake_speak)
    service.speak("hello")

    assert called is False

