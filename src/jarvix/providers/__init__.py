"""Provider construction is centralized; adapters implement the shared AIProvider protocol."""
from __future__ import annotations

from jarvix.domain import AIProvider, ProviderError
from jarvix.providers.gemini import GeminiProvider
from jarvix.providers.openai import OpenAIProvider
from jarvix.providers.local import LocalOpenAIProvider, ModelInfo, OllamaProvider

PROVIDER_MODELS = {"openai": "gpt-4.1-mini", "gemini": "gemini-2.5-flash"}
_PROVIDERS = {"openai": OpenAIProvider, "gemini": GeminiProvider}
LOCAL_PROVIDER_URLS = {"ollama": "http://127.0.0.1:11434", "local": "http://127.0.0.1:1234/v1"}
# Local models are discovered, never guessed or downloaded implicitly.
DEFAULT_MODELS = {**PROVIDER_MODELS, "ollama": "", "local": ""}


def create_provider(provider_id: str, api_key: str = "", *, base_url: str | None = None,
                    local_only: bool = False) -> AIProvider:
    if provider_id in LOCAL_PROVIDER_URLS:
        provider_type = OllamaProvider if provider_id == "ollama" else LocalOpenAIProvider
        return provider_type(base_url or LOCAL_PROVIDER_URLS[provider_id], api_key)
    if local_only:
        raise ProviderError("Local-only mode blocks cloud AI providers. Select a local endpoint.")
    provider_type = _PROVIDERS.get(provider_id)
    if provider_type is None:
        raise ProviderError("Choose a supported AI provider in Settings.")
    return provider_type(api_key)


__all__ = ["GeminiProvider", "OpenAIProvider", "OllamaProvider", "LocalOpenAIProvider", "ModelInfo",
           "PROVIDER_MODELS", "DEFAULT_MODELS", "LOCAL_PROVIDER_URLS", "create_provider"]
