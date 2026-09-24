"""Provider construction is centralized; adapters implement the shared AIProvider protocol."""
from __future__ import annotations

from jarvix.domain import AIProvider, ProviderError
from jarvix.providers.gemini import GeminiProvider
from jarvix.providers.openai import OpenAIProvider

PROVIDER_MODELS = {"openai": "gpt-4.1-mini", "gemini": "gemini-2.5-flash"}
_PROVIDERS = {"openai": OpenAIProvider, "gemini": GeminiProvider}


def create_provider(provider_id: str, api_key: str) -> AIProvider:
    provider_type = _PROVIDERS.get(provider_id)
    if provider_type is None:
        raise ProviderError("Choose a supported AI provider in Settings.")
    return provider_type(api_key)


__all__ = ["GeminiProvider", "OpenAIProvider", "PROVIDER_MODELS", "create_provider"]
