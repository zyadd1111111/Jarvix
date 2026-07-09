from __future__ import annotations

from pathlib import Path
import tempfile


class VoiceTranscriberError(RuntimeError):
    """Raised when voice input cannot be captured or transcribed."""


class VoiceTranscriber:
    """Push-to-talk transcription using sounddevice and faster-whisper when installed."""

    def __init__(self, model_size: str = "base", seconds: int = 5, sample_rate: int = 16_000) -> None:
        self.model_size = model_size
        self.seconds = seconds
        self.sample_rate = sample_rate

    def transcribe_once(self) -> str:
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VoiceTranscriberError(
                "Voice input requires faster-whisper, sounddevice, soundfile, and numpy."
            ) from exc

        try:
            audio = sd.rec(
                int(self.seconds * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
            )
            sd.wait()
        except Exception as exc:
            raise VoiceTranscriberError(f"Microphone capture failed: {exc}") from exc

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            audio_path = Path(handle.name)

        try:
            sf.write(audio_path, np.squeeze(audio), self.sample_rate)
            model = WhisperModel(self.model_size, device="auto", compute_type="auto")
            segments, _info = model.transcribe(str(audio_path))
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise VoiceTranscriberError(f"Transcription failed: {exc}") from exc
        finally:
            audio_path.unlink(missing_ok=True)

        if not text:
            raise VoiceTranscriberError("No speech was detected.")
        return text

