from __future__ import annotations

import builtins

import pytest

from voice.transcriber import VoiceTranscriber, VoiceTranscriberError


def test_transcriber_reports_missing_voice_dependencies(monkeypatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("sounddevice unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(VoiceTranscriberError, match="Voice input requires"):
        VoiceTranscriber().transcribe_once()
