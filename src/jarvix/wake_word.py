"""Opt-in local keyword spotting. Audio never enters network, history, or logs.

Uses sherpa-onnx's documented KeywordSpotter API with user-installed models:
https://k2-fsa.github.io/sherpa/onnx/kws/index.html
The bounded audio queue is discarded on stop. Only speech following a wake
event is written to a temporary WAV for the existing local Windows recognizer.
"""
from __future__ import annotations

import array
import math
import queue
import re
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path

from jarvix.speech_input import SpeechInputService

KEYWORDS = {"jarvix", "stop", "cancel", "never_mind"}
RATE = 16000
BLOCK = 1600


def model_files(directory):
    """Resolve only ordinary local files; ambiguous model variants need selection."""
    from jarvix.capabilities.files import _linked
    root = Path(directory)
    if (not root.is_absolute() or not root.is_dir()
            or any(_linked(part) for part in (root, *root.parents))):
        raise ValueError("Select a local wake-model folder without symbolic links.")
    result = {}
    for kind in ("encoder", "decoder", "joiner"):
        candidates = sorted(root.glob(f"{kind}*.onnx"))
        # Quantized files supplied alongside float models are preferred together.
        quantized = [path for path in candidates if "int8" in path.name]
        candidates = quantized or candidates
        if len(candidates) != 1:
            raise ValueError(f"The wake-model folder must contain one {kind} ONNX variant.")
        result[kind] = candidates[0]
    result.update(tokens=root / "tokens.txt", keywords_file=root / "keywords.txt")
    if any(not path.is_file() or _linked(path) for path in result.values()):
        raise ValueError("The model requires encoder, decoder, joiner, tokens.txt and keywords.txt.")
    keywords = result["keywords_file"]
    if keywords.stat().st_size > 65536:
        raise ValueError("Wake keywords.txt exceeds the local limit.")
    text = keywords.read_text(encoding="utf-8")
    labels = set(re.findall(r"@([A-Za-z_]+)\s*(?:#.*)?$", text, re.MULTILINE))
    if labels != KEYWORDS:
        raise ValueError("Tokenize four keywords for this model with labels @jarvix, @stop, @cancel and @never_mind in keywords.txt.")
    return {key: str(path) for key, path in result.items()}


class SherpaWakeEngine:
    def __init__(self, directory, sensitivity):
        files = model_files(directory)
        try:
            import sherpa_onnx
        except ImportError:
            raise RuntimeError("Install Jarvix's optional voice extra (sherpa-onnx) to enable local wake detection.") from None
        self.spotter = sherpa_onnx.KeywordSpotter(
            **files, num_threads=2, sample_rate=RATE, provider="cpu",
            keywords_threshold=1.0 - sensitivity, keywords_score=1.0,
        )
        self.stream = self.spotter.create_stream()

    def accept(self, raw):
        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        self.stream.accept_waveform(RATE, samples)
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result = self.spotter.get_result(self.stream).lower()
            if result:
                self.spotter.reset_stream(self.stream)
                return result if result in KEYWORDS else ""
        return ""


class WakeWordService:
    """One microphone owner, bounded capture, explicit per-session activation."""
    def __init__(self, services, engine_factory=SherpaWakeEngine):
        self.s = services
        self._engine_factory = engine_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = self._recognition_thread = self._stream = self._recognizer = None
        self._closed = False
        self._active = False
        self._status = "Wake word off"
        self._generation = 0
        self._events = deque(maxlen=16)
        self._hands_free = False
        self._recording = False
        self._transcribing = False

    def state(self):
        with self._lock:
            return {"active": self._active, "status": self._status,
                    "busy": bool(self._thread and self._thread.is_alive()),
                    "hands_free": self._hands_free, "recording": self._recording,
                    "transcribing": self._transcribing}

    def drain_events(self):
        with self._lock:
            result = list(self._events)
            self._events.clear()
            return result

    def set_hands_free(self, enabled):
        with self._lock:
            self._hands_free = bool(enabled)

    def start(self, directory, device=None, sensitivity=.75, silence_timeout=2.0):
        if not self.s.settings.get("microphone.enabled", False):
            raise PermissionError("Enable microphone permission in Settings first.")
        if not .05 <= float(sensitivity) <= .95 or not .5 <= float(silence_timeout) <= 10:
            raise ValueError("Sensitivity must be 0.05–0.95 and silence timeout 0.5–10 seconds.")
        with self._lock:
            if self._closed:
                raise RuntimeError("Wake-word service is closed.")
            if self.state()["busy"] or self._transcribing or self.s.microphone.state()["busy"]:
                raise RuntimeError("Stop active microphone work before enabling wake mode.")
            self._events.clear()
            self._stop.clear()
            self._generation += 1
            self._status = "Loading local wake model…"
            self._thread = threading.Thread(target=self._listen,
                args=(directory, device, float(sensitivity), float(silence_timeout)),
                name="jarvix-wake-local", daemon=True)
            self._thread.start()
        return {"starting": True}

    def stop(self):
        self._stop.set()
        with self._lock:
            self._generation += 1
            self._events.clear()
            self._hands_free = self._recording = self._active = False
            self._status = "Wake word off"
            stream, recognizer = self._stream, self._recognizer
        if recognizer:
            recognizer.cancel()
        if stream:
            try:
                stream.abort()
            except Exception:
                pass
        return {"stopped": True}

    def _permitted(self):
        return not self._stop.is_set() and self.s.settings.get("microphone.enabled", False)

    def _interrupt(self):
        self.s.stop_speaking()
        with self._lock:
            self._generation += 1
            self._recording = False
            recognizer = self._recognizer
            self._events.clear()
            self._events.append({"kind": "cancel"})
            self._status = "Interrupted · listening for Jarvix"
        if recognizer:
            recognizer.cancel()

    def _listen(self, directory, device, sensitivity, silence):
        buffers = queue.Queue(maxsize=20)
        overflow = threading.Event()
        frames = []
        started = last_voice = 0
        heard = False
        try:
            engine = self._engine_factory(directory, sensitivity)
            if not self._permitted():
                return
            probe = SpeechInputService(self.s)
            try:
                if not probe.engines():
                    raise RuntimeError("Install a Windows speech recognition language for local command transcription.")
            finally:
                probe.close()
            if not self._permitted():
                return
            import sounddevice

            def capture(raw, _frames, _time, status):
                if not self._permitted():
                    return
                if status:
                    overflow.set()
                try:
                    buffers.put_nowait(bytes(raw))
                except queue.Full:
                    overflow.set()

            with sounddevice.RawInputStream(device=device, samplerate=RATE, channels=1,
                                           dtype="int16", blocksize=BLOCK, callback=capture) as stream:
                with self._lock:
                    self._stream = stream
                    self._active = self._permitted()
                    if self._active:
                        self._status = "Listening locally for Jarvix"
                while self._permitted():
                    if overflow.is_set():
                        raise RuntimeError("Audio overflow. Select another microphone or reduce local CPU load.")
                    try:
                        raw = buffers.get(timeout=.1)
                    except queue.Empty:
                        continue
                    keyword = engine.accept(raw)
                    if not self._permitted():
                        break
                    if keyword in KEYWORDS - {"jarvix"}:
                        frames.clear()
                        self._interrupt()
                        continue
                    if keyword == "jarvix" and not self._transcribing:
                        self.s.stop_speaking()
                        frames.clear()
                        started = last_voice = time.monotonic()
                        heard = False
                        with self._lock:
                            self._recording = True
                            self._status = "Jarvix heard · listening to your command"
                        continue
                    if not self._recording:
                        continue
                    frames.append(raw)
                    values = array.array("h", raw)
                    energy = math.sqrt(sum(int(value) ** 2 for value in values) / max(1, len(values)))
                    now = time.monotonic()
                    if energy > 450:
                        heard, last_voice = True, now
                    if now - started >= 30 or (heard and now - last_voice >= silence) or (not heard and now - started >= 8):
                        self._begin_transcription(b"".join(frames) if heard else b"")
                        frames.clear()
        except Exception as exc:
            with self._lock:
                if not self._stop.is_set():
                    self._status = (str(exc) if isinstance(exc, (ValueError, RuntimeError, PermissionError))
                                    else "Local wake engine unavailable. Check the model, audio device and voice extra.")
        finally:
            with self._lock:
                self._active = self._recording = False
                self._stream = None
                if not self.s.settings.get("microphone.enabled", False):
                    self._status = "Microphone permission off"
            if not self._permitted():
                self.stop()

    def _begin_transcription(self, audio):
        with self._lock:
            self._recording = False
            if not audio or not self._permitted():
                self._status = "No speech heard · listening for Jarvix"
                return
            self._transcribing = True
            self._status = "Transcribing locally · say Cancel to interrupt"
            self._recognizer = SpeechInputService(self.s)
            generation = self._generation
            self._recognition_thread = threading.Thread(target=self._transcribe,
                args=(audio, generation, self._recognizer), name="jarvix-wake-transcription", daemon=True)
            self._recognition_thread.start()

    def _transcribe(self, audio, generation, recognizer):
        path = None
        try:
            from jarvix.capabilities.files import _linked
            directory = self.s.data_dir / "voice-tmp"
            if any(_linked(part) for part in (directory, *directory.parents)):
                raise RuntimeError("Temporary voice folder cannot be a symbolic link.")
            directory.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".wav", delete=False) as output:
                path = Path(output.name)
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                output.writeframes(audio)
            text = recognizer.transcribe(path).strip()
            with self._lock:
                if self._permitted() and generation == self._generation:
                    # Stop commands are deterministic local controls, never provider prompts.
                    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
                    if normalized in {"stop", "jarvix stop", "cancel", "never mind", "jarvix cancel"}:
                        self._interrupt()
                    elif text:
                        self._events.append({"kind": "transcript", "text": text,
                                             "submit": self._hands_free})
                    self._status = "Listening locally for Jarvix"
        except Exception:
            with self._lock:
                if self._permitted() and generation == self._generation:
                    self._status = "Local transcription failed · try again after Jarvix"
        finally:
            recognizer.close()
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._transcribing = False
                if self._recognizer is recognizer:
                    self._recognizer = None

    def close(self):
        self._closed = True
        self.stop()
        for thread in (self._thread, self._recognition_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)
