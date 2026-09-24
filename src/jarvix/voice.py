"""Local OS speech without a Qt dependency or interpolation into shell commands."""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import threading

WINDOWS_SCRIPT = (
    "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding; "
    "Add-Type -AssemblyName System.Speech; "
    "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$speaker.Rate = [int]$env:JARVIX_SPEECH_RATE; "
    "if ($env:JARVIX_SPEECH_VOICE) { $speaker.SelectVoice($env:JARVIX_SPEECH_VOICE) }; "
    "try { $text = [Console]::In.ReadToEnd(); $speaker.Speak($text) } "
    "finally { $speaker.Dispose() }"
)


class VoiceService:
    """Read aloud on a worker; Stop terminates the owned speech process promptly.

    Microphone capture belongs to SpeechInputService. No audio recording or cloud
    audio transfer takes place in this playback service.
    """

    input_available = False
    input_status = "Microphone transcription is not configured. You can type requests and use local read-aloud."

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._generation = 0
        self._speaking = False
        self._closed = False
        self._command = self._discover_command()
        self._status = "Local speech is ready." if self._command else "No supported local speech engine was found."

    @staticmethod
    def _discover_command() -> list[str] | None:
        if sys.platform == "win32":
            executable = shutil.which("powershell.exe")
            if executable:
                return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_SCRIPT]
        if sys.platform == "darwin" and os.path.isfile("/usr/bin/say"):
            return ["/usr/bin/say", "-f", "-"]
        for name in ("espeak-ng", "espeak"):
            executable = shutil.which(name)
            if executable:
                return [executable, "--stdin"]
        return None

    @property
    def available(self) -> bool:
        return self._command is not None and not self._closed

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def voices(self) -> list[str]:
        """Return installed Windows voices; unsupported engines keep their default."""
        if sys.platform != "win32" or not self.available:
            return []
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "try { @($s.GetInstalledVoices() | Where-Object {$_.Enabled} | "
            "ForEach-Object {$_.VoiceInfo.Name}) | ConvertTo-Json -Compress } finally {$s.Dispose()}"
        )
        try:
            result = subprocess.run(
                [self._command[0], "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            value = json.loads(result.stdout.decode("utf-8-sig")) if result.returncode == 0 else []
            return value if isinstance(value, list) else [value] if value else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []

    def speak(self, text: str, rate: int = 175, voice: str = "") -> bool:
        if not isinstance(text, str) or not text.strip():
            return False
        try:
            # Words-per-minute engines receive this directly. System.Speech uses
            # an approximate pace scale, mapped in _run to its -10..10 range.
            rate = max(80, min(300, int(rate)))
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self._status = "Use a numeric speech rate between 80 and 300."
            return False
        self.stop()
        with self._lock:
            if not self.available:
                return False
            self._generation += 1
            generation = self._generation
            self._speaking = True
            self._status = "Speaking locally."
        threading.Thread(target=self._run, args=(text[:30000], generation, rate, voice), name="jarvix-speech", daemon=True).start()
        return True

    def _run(self, text: str, generation: int, rate: int, voice: str = "") -> None:
        try:
            with self._lock:
                if generation != self._generation or self._closed:
                    return
                command = list(self._command)
                environment = os.environ.copy()
                if sys.platform == "win32":
                    environment["JARVIX_SPEECH_RATE"] = str(max(-10, min(10, round((rate - 175) / 20))))
                    environment["JARVIX_SPEECH_VOICE"] = str(voice)[:200]
                elif sys.platform == "darwin":
                    command.extend(["-r", str(rate)])
                else:
                    command.extend(["-s", str(rate)])
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, shell=False,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
                )
                self._process = process
            process.communicate(input=text.encode("utf-8"))
            with self._lock:
                if generation == self._generation:
                    self._status = "Local speech is ready." if process.returncode == 0 else "The local speech engine could not speak this text."
        except (OSError, ValueError):
            with self._lock:
                if generation == self._generation:
                    self._status = "The local speech engine could not start."
        finally:
            with self._lock:
                if generation == self._generation:
                    self._process = None
                    self._speaking = False

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            process, self._process = self._process, None
            self._speaking = False
            if self._command:
                self._status = "Speech stopped."
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.stop()
