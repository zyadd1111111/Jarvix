from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from config.settings import AppSettings


GEMINI_KEY_HELP = (
    "Gemini API key looks wrong. Jarvix needs a Google AI Studio API key, "
    "starting with `AQ` for newer auth keys or `AIza` for older standard keys. "
    "Create/copy a key from https://aistudio.google.com/app/apikey, copy only "
    "the key, then restart Jarvix."
)


SYSTEM_PROMPT = """You are Jarvix, a local-first desktop assistant.
You can chat and suggest safe next steps, but you cannot access accounts,
inspect credentials, or use private data. The desktop host handles simple
local actions like opening websites, opening apps, and arithmetic before
messages reach you; never claim a local action happened unless the host says so.
Keep responses concise, practical, and calm."""


class AIClientError(RuntimeError):
    """Raised when AI chat cannot complete."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class AIClient:
    """Thin provider wrapper for OpenAI and Gemini.

    SDKs are imported lazily so the rest of the app and tests can run without
    provider packages installed.
    """

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def generate_reply(self, messages: Iterable[ChatMessage | dict[str, str]]) -> str:
        normalized_messages = self._normalize_messages(messages)
        if self.settings.ai_provider == "gemini":
            return self._generate_gemini_reply(normalized_messages)
        return self._generate_openai_reply(normalized_messages)

    def _generate_openai_reply(self, messages: list[ChatMessage]) -> str:
        if not self.settings.openai_api_key:
            raise AIClientError(
                "OPENAI_API_KEY is not set. Add it to your shell environment to enable AI chat."
            )

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIClientError(
                'The OpenAI SDK is not installed. Run: python -m pip install -e ".[dev]"'
            ) from exc

        client = OpenAI(api_key=self.settings.openai_api_key)
        input_items = [{"role": "system", "content": SYSTEM_PROMPT}]
        for message in messages:
            input_items.append({"role": message.role, "content": message.content})

        try:
            response = client.responses.create(
                model=self.settings.model,
                input=input_items,
                text={"verbosity": "low"},
                reasoning={"effort": "low"},
            )
        except Exception as exc:  # pragma: no cover - SDK/network specific
            raise AIClientError(f"AI request failed: {exc}") from exc

        output_text = getattr(response, "output_text", None)
        if output_text:
            return str(output_text).strip()

        return self._extract_text_from_response(response)

    def _generate_gemini_reply(self, messages: list[ChatMessage]) -> str:
        if not self.settings.gemini_api_key:
            raise AIClientError(
                "GEMINI_API_KEY is not set. Add it to your shell environment to enable Gemini chat."
            )
        if not self._looks_like_gemini_api_key(self.settings.gemini_api_key):
            raise AIClientError(GEMINI_KEY_HELP)

        try:
            from google import genai
        except ImportError as exc:
            raise AIClientError(
                'The Gemini SDK is not installed. Run: python -m pip install -e ".[dev]"'
            ) from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        prompt = self._build_gemini_prompt(messages)
        try:
            response = client.interactions.create(model=self.settings.model, input=prompt)
        except Exception as exc:  # pragma: no cover - SDK/network specific
            if self._is_gemini_auth_error(str(exc)):
                raise AIClientError(GEMINI_KEY_HELP) from exc
            raise AIClientError(f"Gemini request failed: {exc}") from exc

        output_text = getattr(response, "output_text", None) or getattr(response, "text", None)
        if output_text:
            return str(output_text).strip()
        raise AIClientError("Gemini response did not include text output.")

    @staticmethod
    def _looks_like_gemini_api_key(api_key: str) -> bool:
        return api_key.strip().startswith(("AIza", "AQ"))

    @staticmethod
    def _is_gemini_auth_error(message: str) -> bool:
        return any(
            marker in message
            for marker in (
                "401 UNAUTHENTICATED",
                "invalid authentication credentials",
                "ACCESS_TOKEN_TYPE_UNSUPPORTED",
            )
        )

    @staticmethod
    def _normalize_messages(messages: Iterable[ChatMessage | dict[str, str]]) -> list[ChatMessage]:
        normalized: list[ChatMessage] = []
        for message in messages:
            if isinstance(message, ChatMessage):
                normalized.append(message)
            else:
                normalized.append(
                    ChatMessage(
                        role=str(message.get("role", "user")),
                        content=str(message.get("content", "")),
                    )
                )
        return normalized

    @staticmethod
    def _build_gemini_prompt(messages: list[ChatMessage]) -> str:
        lines = [f"System: {SYSTEM_PROMPT}", ""]
        for message in messages:
            role = "Assistant" if message.role == "assistant" else "User"
            lines.append(f"{role}: {message.content}")
        lines.append("Assistant:")
        return "\n".join(lines)

    @staticmethod
    def _extract_text_from_response(response: object) -> str:
        output = getattr(response, "output", None) or []
        chunks: list[str] = []
        for item in output:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    chunks.append(str(text))
        if not chunks:
            raise AIClientError("AI response did not include text output.")
        return "\n".join(chunks).strip()
