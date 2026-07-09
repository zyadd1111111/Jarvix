from __future__ import annotations

import threading


class SpeechService:
    """Non-blocking pyttsx3 speech wrapper."""

    def __init__(self) -> None:
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def speak(self, text: str) -> None:
        if not self._enabled or not text.strip():
            return
        thread = threading.Thread(target=self._speak_blocking, args=(text,), daemon=True)
        thread.start()

    @staticmethod
    def _speak_blocking(text: str) -> None:
        try:
            import pyttsx3
        except ImportError:
            return
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()

