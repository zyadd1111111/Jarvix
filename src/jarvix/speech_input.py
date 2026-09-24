"""Opt-in local microphone capture and Windows dictation; no audio leaves the device.

Uses sounddevice RawInputStream and System.Speech SetInputToWaveFile/Recognize.
https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html
https://learn.microsoft.com/dotnet/api/system.speech.recognition.speechrecognitionengine
"""
from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

RECOGNIZE = r"""$ErrorActionPreference='Stop'; [Console]::InputEncoding=[Text.Encoding]::UTF8;
[Console]::OutputEncoding=[Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech;
$config=([Console]::In.ReadToEnd() | ConvertFrom-Json);
$engine=New-Object System.Speech.Recognition.SpeechRecognitionEngine;
try {
 $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar));
 $engine.SetInputToWaveFile([string]$config.path);
 $texts=New-Object System.Collections.Generic.List[string];
 while ($null -ne ($result=$engine.Recognize([TimeSpan]::FromSeconds(3)))) { $texts.Add($result.Text) }
 @{text=($texts -join ' ')} | ConvertTo-Json -Compress
} finally { $engine.Dispose() }
"""
ENGINES = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object { $_.Name }) | ConvertTo-Json -Compress"


class SpeechInputService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._process = None
        self._status = "Microphone off"
        self._transcript = ""
        self._revision = 0
        self._active = False
        self._engine_cache = None
        self._closed = False
        self._request_generation = 0

    def devices(self):
        import sounddevice
        return [{"id": index, "name": row["name"], "channels": row["max_input_channels"],
                 "sample_rate": row["default_samplerate"]}
                for index, row in enumerate(sounddevice.query_devices()) if row["max_input_channels"] > 0]

    def engines(self):
        if self._engine_cache is not None:
            return self._engine_cache
        if sys.platform != "win32" or not shutil.which("powershell.exe"):
            return []
        try:
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ENGINES],
                capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value = json.loads(proc.stdout.decode("utf-8-sig")) if proc.returncode == 0 else []
            self._engine_cache = value if isinstance(value, list) else ([value] if value else [])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._engine_cache = []
        return self._engine_cache

    def state(self):
        with self._lock:
            return {"active": self._active, "status": self._status,
                    "transcript": self._transcript, "revision": self._revision,
                    "busy": bool(self._thread and self._thread.is_alive())}

    def start(self, device=None, silence_timeout=2.0):
        with self._lock:
            generation = self._request_generation
        if not self.s.settings.get("microphone.enabled", False):
            raise PermissionError("Enable microphone access in Settings first.")
        if not self.engines():
            raise RuntimeError("No Windows speech recognition language is installed.")
        if not .5 <= float(silence_timeout) <= 10:
            raise ValueError("Silence timeout must be between 0.5 and 10 seconds.")
        with self._lock:
            if self._closed:
                raise RuntimeError("Microphone service is closed.")
            if generation != self._request_generation:
                return {"listening": False}
            if not self.s.settings.get("microphone.enabled", False):
                raise PermissionError("Microphone access was turned off.")
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Microphone or transcription is already active.")
            self.s.stop_speaking()
            self._stop.clear()
            self._cancel.clear()
            self._active = True
            self._status = "Listening locally"
            self._transcript = ""
            self._thread = threading.Thread(target=self._capture, args=(device, float(silence_timeout)), daemon=True)
            self._thread.start()
        return {"listening": True}

    def finish(self):
        self._stop.set()
        return {"status": "Finishing local transcription"}

    def cancel(self):
        self._cancel.set()
        self._stop.set()
        with self._lock:
            self._request_generation += 1
            process = self._process
            self._transcript = ""
            self._status = "Microphone off"
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        return {"cancelled": True}

    def _capture(self, device, silence):
        capture_path = None
        try:
            import sounddevice
            if self._cancel.is_set() or not self.s.settings.get("microphone.enabled", False):
                return
            descriptor = sounddevice.query_devices(device, "input")
            rate = int(descriptor["default_samplerate"])
            block = max(160, rate // 10)
            directory = self.s.data_dir / "voice-tmp"
            directory.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".wav", dir=directory, delete=False) as temp:
                capture_path = Path(temp.name)
            started = last_voice = time.monotonic()
            heard = False
            with wave.open(str(capture_path), "wb") as output:
                output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                if self._cancel.is_set() or not self.s.settings.get("microphone.enabled", False):
                    return
                with sounddevice.RawInputStream(device=device, samplerate=rate, channels=1, dtype="int16", blocksize=block) as stream:
                    while not self._stop.is_set():
                        if not self.s.settings.get("microphone.enabled", False):
                            self._cancel.set()
                            break
                        raw, overflow = stream.read(block)
                        if overflow:
                            raise RuntimeError("Microphone audio overflow")
                        output.writeframes(bytes(raw))
                        samples = array.array("h", raw)
                        rms = math.sqrt(sum(int(value) ** 2 for value in samples) / max(1, len(samples)))
                        now = time.monotonic()
                        if rms > 450:
                            heard = True
                            last_voice = now
                        if now - started > 60 or (heard and now - last_voice >= silence) or (not heard and now - started > 10):
                            break
            with self._lock:
                self._active = False
                self._status = "Transcribing locally" if not self._cancel.is_set() else "Microphone off"
            if not self._cancel.is_set():
                text = self.transcribe(capture_path)
                with self._lock:
                    if not self._cancel.is_set():
                        self._transcript = text
                        self._revision += 1
                        self._status = "Ready to review" if text else "No speech recognized"
        except Exception:
            with self._lock:
                if not self._cancel.is_set():
                    self._status = "Microphone or local recognition unavailable. Check device and Windows speech settings."
        finally:
            if capture_path:
                try:
                    capture_path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._active = False
                self._process = None

    def transcribe(self, path):
        command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", RECOGNIZE]
        with self._lock:
            if self._closed or self._cancel.is_set():
                return ""
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
            self._process = process
        try:
            try:
                output, _ = process.communicate(json.dumps({"path": str(path)}).encode("utf-8"), timeout=70)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RuntimeError("Local recognition timed out") from None
            if self._cancel.is_set():
                return ""
            if process.returncode != 0 or len(output) > 64000:
                raise RuntimeError("Local recognition failed")
            decoded = json.loads(output.decode("utf-8-sig"))
            if not isinstance(decoded, dict) or not isinstance(decoded.get("text"), str):
                raise RuntimeError("Local recognition returned invalid text")
            return decoded["text"][:16000]
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

    def close(self):
        with self._lock:
            self._closed = True
        self.cancel()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
