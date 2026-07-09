from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
SUPPORTED_AI_PROVIDERS = {"openai", "gemini"}


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings loaded from environment and local project paths."""

    data_dir: Path
    database_path: Path
    ai_provider: str
    openai_api_key: str | None
    gemini_api_key: str | None
    model: str
    tts_enabled: bool = True

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_ai_key(self) -> bool:
        if self.ai_provider == "gemini":
            return self.has_gemini_key
        return self.has_openai_key

    @property
    def active_key_warning(self) -> str | None:
        if self.ai_provider == "gemini" and self.gemini_api_key:
            if not self.gemini_api_key.startswith(("AIza", "AQ")):
                return "Gemini key is set, but it does not look like a Google AI Studio API key."
        if self.ai_provider == "openai" and self.openai_api_key:
            if not self.openai_api_key.startswith("sk-"):
                return "OpenAI key is set, but it does not look like an OpenAI API key."
        return None

    @classmethod
    def from_environment(cls, data_dir: Path) -> "AppSettings":
        data_dir = data_dir.expanduser().resolve()
        openai_api_key = os.getenv("OPENAI_API_KEY") or None
        gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
        requested_provider = os.getenv("JARVIX_AI_PROVIDER", "").strip().lower()
        if requested_provider in SUPPORTED_AI_PROVIDERS:
            ai_provider = requested_provider
        elif gemini_api_key and not openai_api_key:
            ai_provider = "gemini"
        else:
            ai_provider = "openai"

        default_model = DEFAULT_GEMINI_MODEL if ai_provider == "gemini" else DEFAULT_OPENAI_MODEL
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "jarvix.db",
            ai_provider=ai_provider,
            openai_api_key=openai_api_key,
            gemini_api_key=gemini_api_key,
            model=os.getenv("JARVIX_MODEL", default_model),
            tts_enabled=os.getenv("JARVIX_TTS_ENABLED", "1") not in {"0", "false", "False"},
        )
