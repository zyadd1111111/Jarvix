import json
import subprocess
import sys
import time
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvix.speech_input import RECOGNIZE, SpeechInputService
from jarvix.voice import VoiceService


class Settings(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture
def microphone(tmp_path, monkeypatch):
    services = SimpleNamespace(data_dir=tmp_path, settings=Settings(), stop_speaking=Mock())
    result = SpeechInputService(services)
    monkeypatch.setattr(result, "engines", lambda: ["Test recognizer"])
    yield result
    result.close()


def wait_finished(microphone):
    deadline = time.monotonic() + 3
    while microphone.state()["busy"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not microphone.state()["busy"]


def test_permission_and_timeout_validation(microphone):
    with pytest.raises(PermissionError):
        microphone.start()
    microphone.s.settings.set("microphone.enabled", True)
    with pytest.raises(ValueError):
        microphone.start(silence_timeout=0)
    assert not microphone.state()["active"]


def test_capture_transcribes_locally_then_removes_temporary_audio(microphone, monkeypatch):
    microphone.s.settings.set("microphone.enabled", True)
    observed = []

    class Stream:
        def __init__(self, **kwargs):
            observed.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, block):
            microphone.finish()
            return b"\xff\x07" * block, False

    def transcribe(path):
        with wave.open(str(path)) as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getnframes() == 1600
        return "Open my notes"

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda *_: {"default_samplerate": 16000}, RawInputStream=Stream,
    ))
    monkeypatch.setattr(microphone, "transcribe", transcribe)
    microphone.start(device=4)
    wait_finished(microphone)
    assert microphone.state()["transcript"] == "Open my notes"
    assert microphone.state()["revision"] == 1
    assert observed[0]["device"] == 4
    assert list((microphone.s.data_dir / "voice-tmp").iterdir()) == []
    microphone.s.stop_speaking.assert_called_once()


def test_cancel_during_engine_probe_cannot_restart_recording(microphone, monkeypatch):
    microphone.s.settings.set("microphone.enabled", True)

    def engines():
        microphone.cancel()
        return ["Test"]

    monkeypatch.setattr(microphone, "engines", engines)
    assert microphone.start() == {"listening": False}
    assert not microphone.state()["busy"]


def test_access_revoked_during_probe_never_opens_capture(microphone, monkeypatch):
    microphone.s.settings.set("microphone.enabled", True)

    def engines():
        microphone.s.settings.set("microphone.enabled", False)
        return ["Test"]

    monkeypatch.setattr(microphone, "engines", engines)
    capture = Mock()
    monkeypatch.setattr(microphone, "_capture", capture)
    with pytest.raises(PermissionError):
        microphone.start()
    capture.assert_not_called()
    assert not microphone.state()["busy"]


def test_permission_revocation_stops_capture_without_transcription(microphone, monkeypatch):
    microphone.s.settings.set("microphone.enabled", True)

    class Stream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            microphone.s.settings.set("microphone.enabled", False)
            return self

        def __exit__(self, *args):
            pass

        def read(self, block):
            pytest.fail("Permission was revoked before capture")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda *_: {"default_samplerate": 16000}, RawInputStream=Stream,
    ))
    transcribe = Mock()
    monkeypatch.setattr(microphone, "transcribe", transcribe)
    microphone.start()
    wait_finished(microphone)
    transcribe.assert_not_called()
    assert microphone.state()["transcript"] == ""
    assert list((microphone.s.data_dir / "voice-tmp").iterdir()) == []


def test_device_failure_is_local_and_sanitized(microphone, monkeypatch):
    microphone.s.settings.set("microphone.enabled", True)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=Mock(side_effect=RuntimeError("private device details")),
    ))
    microphone.start()
    wait_finished(microphone)
    assert "unavailable" in microphone.state()["status"]
    assert "private" not in microphone.state()["status"]


def test_recognition_command_receives_path_as_data(microphone, monkeypatch, tmp_path):
    process = Mock(returncode=0)
    process.communicate.return_value = (b'{"text":"Create a task"}', b"")
    popen = Mock(return_value=process)
    monkeypatch.setattr("jarvix.speech_input.subprocess.Popen", popen)
    path = tmp_path / "recording $(ignored).wav"
    assert microphone.transcribe(path) == "Create a task"
    arguments = popen.call_args.args[0]
    assert str(path) not in arguments[-1]
    assert RECOGNIZE.startswith("$ErrorActionPreference")
    assert json.loads(process.communicate.call_args.args[0]) == {"path": str(path)}


def test_recognition_timeout_kills_owned_process(microphone, monkeypatch, tmp_path):
    process = Mock()
    process.communicate.side_effect = [subprocess.TimeoutExpired("recognizer", 70), (b"", b"")]
    monkeypatch.setattr("jarvix.speech_input.subprocess.Popen", lambda *_, **__: process)
    with pytest.raises(RuntimeError, match="timed out"):
        microphone.transcribe(tmp_path / "audio.wav")
    process.kill.assert_called_once()
    assert microphone._process is None


def test_cancelled_transcription_discards_output_and_releases_process(microphone, monkeypatch, tmp_path):
    process = Mock(returncode=0)

    def communicate(*args, **kwargs):
        microphone.cancel()
        return b'{"text":"Discard this speech"}', b""

    process.communicate.side_effect = communicate
    monkeypatch.setattr("jarvix.speech_input.subprocess.Popen", lambda *_, **__: process)
    assert microphone.transcribe(tmp_path / "audio.wav") == ""
    assert microphone._process is None


def test_recognizer_malformed_output_is_not_accepted_as_transcript(microphone, monkeypatch, tmp_path):
    process = Mock(returncode=0)
    process.communicate.return_value = (b'{"text":{"unexpected":"object"}}', b"")
    monkeypatch.setattr("jarvix.speech_input.subprocess.Popen", lambda *_, **__: process)
    with pytest.raises(RuntimeError, match="invalid text"):
        microphone.transcribe(tmp_path / "audio.wav")
    assert microphone._process is None


def test_cancel_and_close_do_not_leave_active_owned_process(microphone):
    process = Mock()
    process.poll.return_value = None
    microphone._process = process
    microphone._transcript = "discard me"
    microphone.cancel()
    process.terminate.assert_called_once()
    assert microphone.state()["transcript"] == ""
    microphone.s.settings.set("microphone.enabled", True)
    microphone.close()
    with pytest.raises(RuntimeError, match="closed"):
        microphone.start()


def test_voice_name_is_environment_data_not_shell_code(monkeypatch):
    monkeypatch.setattr("jarvix.voice.sys.platform", "win32")
    monkeypatch.setattr(VoiceService, "_discover_command", lambda _: ["powershell.exe", "-Command", "constant"])
    process = Mock(returncode=0)
    popen = Mock(return_value=process)
    monkeypatch.setattr("jarvix.voice.subprocess.Popen", popen)
    service = VoiceService()
    service._generation = 1
    voice = "Voice $(untrusted)"
    service._run("hello", 1, 175, voice)
    assert popen.call_args.kwargs["env"]["JARVIX_SPEECH_VOICE"] == voice
    assert voice not in popen.call_args.args[0]
    assert popen.call_args.kwargs["shell"] is False
