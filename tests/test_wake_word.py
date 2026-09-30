"""Local wake state, microphone ownership and cancellation without real audio."""
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvix.speech_input import SpeechInputService
from jarvix.wake_word import SherpaWakeEngine, WakeWordService, model_files


@pytest.fixture
def model(tmp_path):
    directory = tmp_path / "model"
    directory.mkdir()
    for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"):
        (directory / name).write_text("test-only model")
    (directory / "keywords.txt").write_text("J AR V IX @jarvix\nS T OP @stop\nC AN CEL @cancel\nNE V ER M I ND @never_mind\n")
    return directory


@pytest.fixture
def wake(tmp_path, monkeypatch):
    services = SimpleNamespace(data_dir=tmp_path, settings={"microphone.enabled": True},
                               stop_speaking=Mock(), microphone=Mock())
    services.microphone.state.return_value = {"busy": False}
    instance = WakeWordService(services, engine_factory=Mock())
    services.wake_word = instance
    monkeypatch.setattr(SpeechInputService, "engines", lambda _: ["Local test recognizer"])
    yield instance
    instance.close()


def wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def test_wake_off_by_default_and_denied_without_microphone(wake, model):
    assert wake.state() == {"active": False, "status": "Wake word off", "busy": False,
                            "hands_free": False, "recording": False, "transcribing": False}
    wake.s.settings["microphone.enabled"] = False
    with pytest.raises(PermissionError):
        wake.start(model)
    wake._engine_factory.assert_not_called()


def test_model_selection_validates_labels_and_ambiguous_variants(model):
    assert model_files(model)["tokens"].endswith("tokens.txt")
    (model / "encoder-other.onnx").write_text("ambiguous")
    with pytest.raises(ValueError, match="one encoder"):
        model_files(model)
    (model / "encoder.int8.onnx").write_text("preferred")
    assert model_files(model)["encoder"].endswith("encoder.int8.onnx")
    (model / "keywords.txt").write_text("J AR V IX @jarvix")
    with pytest.raises(ValueError, match="four keywords"):
        model_files(model)


def test_sherpa_uses_cpu_local_models_and_explicit_threshold(model, monkeypatch):
    spotter = Mock()
    factory = Mock(return_value=spotter)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(KeywordSpotter=factory))
    engine = SherpaWakeEngine(model, .8)
    options = factory.call_args.kwargs
    assert options["provider"] == "cpu"
    assert options["keywords_threshold"] == pytest.approx(.2)
    assert options["sample_rate"] == 16000
    assert engine.stream is spotter.create_stream.return_value


def test_stop_during_model_load_prevents_microphone_capture(wake, model, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def load(*_):
        entered.set()
        release.wait(2)
        return Mock()
    wake._engine_factory = load
    stream = Mock()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=stream))
    wake.start(model)
    assert entered.wait(2)
    wake.stop()
    release.set()
    wait(lambda: not wake.state()["busy"])
    stream.assert_not_called()
    assert not wake.state()["active"]


def test_stop_and_permission_revocation_discard_queued_audio(wake, model, monkeypatch):
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock()
    factory = Mock(return_value=stream)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=factory))
    wake.start(model, device=7)
    wait(lambda: wake.state()["active"])
    assert factory.call_args.kwargs["device"] == 7
    wake.s.settings["microphone.enabled"] = False
    wait(lambda: not wake.state()["busy"])
    assert not wake.state()["active"]
    assert wake.drain_events() == []


def test_wake_and_cancel_are_local_and_leave_session_listening(wake, model, monkeypatch):
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock()
    factory = Mock(return_value=stream)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=factory))
    engine = Mock()
    engine.accept.side_effect = ["jarvix", "cancel"]
    wake._engine_factory = lambda *_: engine
    wake.start(model)
    wait(lambda: wake.state()["active"])
    capture = factory.call_args.kwargs["callback"]
    capture(b"\0" * 3200, 1600, None, None)
    wait(lambda: wake.state()["recording"])
    capture(b"\0" * 3200, 1600, None, None)
    wait(lambda: not wake.state()["recording"])
    assert wake.drain_events() == [{"kind": "cancel"}]
    assert wake.state()["active"]
    assert wake.s.stop_speaking.call_count == 2
    wake.stop()
    stream.abort.assert_called_once()


@pytest.mark.parametrize("hands_free", [False, True])
def test_transcript_stays_local_unless_session_explicitly_enables_submission(wake, monkeypatch, hands_free):
    monkeypatch.setattr(SpeechInputService, "transcribe", lambda *_: "Open my notes")
    wake.set_hands_free(hands_free)
    wake._begin_transcription(b"\x00\x08" * 1600)
    wait(lambda: not wake.state()["transcribing"])
    assert wake.drain_events() == [{"kind": "transcript", "text": "Open my notes", "submit": hands_free}]
    assert list((wake.s.data_dir / "voice-tmp").iterdir()) == []


def test_stop_discards_inflight_transcription_and_resets_hands_free(wake, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def recognize(*_):
        started.set()
        release.wait(2)
        return "Must not execute"
    monkeypatch.setattr(SpeechInputService, "transcribe", recognize)
    wake.set_hands_free(True)
    wake._begin_transcription(b"\x00\x08" * 1600)
    assert started.wait(2)
    wake.stop()
    release.set()
    wait(lambda: not wake.state()["transcribing"])
    assert wake.drain_events() == []
    assert wake.state()["hands_free"] is False


def test_manual_microphone_cannot_compete_with_wake_owner(wake):
    wake._thread = Mock()
    wake._thread.is_alive.return_value = True
    microphone = SpeechInputService(wake.s)
    with pytest.raises(RuntimeError, match="Disable wake"):
        microphone.start()
    assert not microphone.state()["active"]
    wake._thread = None


def test_bad_device_cannot_emit_private_exception_details(wake, model, monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Mock(
        side_effect=OSError("private hardware secret"))))
    wake.start(model)
    wait(lambda: not wake.state()["busy"])
    assert "private hardware secret" not in wake.state()["status"]
    assert not wake.state()["active"]
