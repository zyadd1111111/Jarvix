"""Loopback-only model adapters; capabilities come from endpoint metadata."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from jarvix.domain import ProviderError
from jarvix.providers.openai import OpenAIProvider

LOCAL_TIMEOUT = httpx.Timeout(connect=3, read=10, write=10, pool=3)


def local_base_url(value: str) -> str:
    """Confine endpoints to numeric loopback; no DNS, proxies or redirects."""
    try:
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 for c in value):
            raise ValueError
        parts = urlsplit(value)
        host = parts.hostname
        if host == "localhost":
            host = "127.0.0.1"
        address = ipaddress.ip_address(host)
        if (parts.scheme not in {"http", "https"} or not address.is_loopback
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or "%" in parts.netloc
                or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", parts.path)):
            raise ValueError
        port = parts.port
        netloc = f"[{host}]" if address.version == 6 else host
        if port is not None:
            if not 1 <= port <= 65535:
                raise ValueError
            netloc += f":{port}"
        return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("Local AI base URL must use http(s) with localhost or a loopback IP, without credentials or query parameters.") from None


def validate_local_model(model: str) -> str:
    if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model)
            or ".." in model or "//" in model or model.endswith("/")):
        raise ProviderError("Enter a valid local model identifier in Settings.")
    return model


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    capabilities: frozenset[str] = frozenset()
    context_length: int | None = None
    is_local: bool = True

    def as_dict(self):
        value = asdict(self)
        value["capabilities"] = sorted(self.capabilities)
        return value


def _capabilities(raw):
    if isinstance(raw, dict):
        raw = [key for key, value in raw.items() if value is True]
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        return frozenset()
    aliases = {"chat": "completion", "tool_calling": "tools", "embedding": "embeddings"}
    return frozenset(aliases.get(value, value) for value in raw
                     if value in {"chat", "completion", "tools", "tool_calling", "vision", "embedding", "embeddings"})


def _context_length(raw):
    return raw if type(raw) is int and 0 < raw <= 100_000_000 else None


class LocalOpenAIProvider(OpenAIProvider):
    """OpenAI-compatible chat/embeddings using the existing bounded transport.

    A plain /models response does not advertise tools, vision or embeddings.
    Those features stay disabled unless its model entry exposes capabilities.
    """
    id = "local"
    is_local = True
    supports_vision = False
    supports_tools = False
    _validate_model = staticmethod(validate_local_model)

    def __init__(self, base_url="http://127.0.0.1:1234/v1", api_key="", client=None):
        self.base_url = local_base_url(base_url)
        super().__init__(api_key or "local", client)
        self._headers = {"Authorization": f"Bearer {self._transport._api_key}"} if api_key else {}
        self._completion_url = self.base_url + "/chat/completions"
        self._models: dict[str, ModelInfo] = {}

    def discover_models(self) -> list[ModelInfo]:
        data = self._transport.request(self.base_url + "/models", None, self._headers,
                                       method="GET", timeout=LOCAL_TIMEOUT)
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) > 512:
            raise ProviderError("The local endpoint returned an invalid model list.")
        models = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("The local endpoint returned invalid model metadata.")
            model = validate_local_model(row.get("id"))
            models[model] = ModelInfo(model, self.id, _capabilities(row.get("capabilities")),
                                      _context_length(row.get("context_length")))
        self._models = models
        return list(models.values())

    def model_info(self, model: str) -> ModelInfo:
        model = validate_local_model(model)
        if model not in self._models:
            self.discover_models()
        if model not in self._models:
            raise ProviderError("The selected local model is not installed or exposed by the endpoint.")
        info = self._models[model]
        self.supports_vision = "vision" in info.capabilities
        self.supports_tools = "tools" in info.capabilities
        return info

    def health(self) -> dict:
        try:
            models = self.discover_models()
            return {"provider": self.id, "available": True, "is_local": True,
                    "base_url": self.base_url, "models": [model.as_dict() for model in models]}
        except ProviderError as exc:
            return {"provider": self.id, "available": False, "is_local": True,
                    "base_url": self.base_url, "models": [], "error": str(exc)}

    def _effective_tools(self, messages, tools, model):
        info = self.model_info(model)
        if any(message.images for message in messages) and "vision" not in info.capabilities:
            raise ProviderError("The selected local model does not advertise image support.")
        if any(message.tool_calls or message.role == "tool" for message in messages) and "tools" not in info.capabilities:
            raise ProviderError("The selected local model does not advertise tool support for this conversation.")
        return tools if "tools" in info.capabilities else []

    def complete(self, messages, tools, model):
        return super().complete(messages, self._effective_tools(messages, tools, model), model)

    def stream(self, messages, tools, model, on_delta):
        return super().stream(messages, self._effective_tools(messages, tools, model), model, on_delta)

    def _payload(self, messages, tools, model):
        payload = super()._payload(messages, tools, model)
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        payload.pop("store")
        # Some compatible local runtimes reject this optional OpenAI flag.
        payload.pop("parallel_tool_calls", None)
        return payload

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self._validate_embeddings_input(texts)
        if "embeddings" not in self.model_info(model).capabilities:
            raise ProviderError("The selected local model does not advertise embeddings support.")
        result = self._transport.request(self.base_url + "/embeddings",
            {"model": model, "input": texts, "encoding_format": "float"}, self._headers)
        rows = result.get("data")
        if (not isinstance(rows, list) or len(rows) != len(texts)
                or any(not isinstance(row, dict) or type(row.get("index")) is not int for row in rows)
                or sorted(row["index"] for row in rows) != list(range(len(texts)))):
            raise ProviderError("The local endpoint returned invalid embeddings.")
        return self._validate_vectors([row.get("embedding") for row in sorted(rows, key=lambda row: row["index"])], len(texts))

    @staticmethod
    def _validate_embeddings_input(texts):
        if (not isinstance(texts, list) or not 1 <= len(texts) <= 64
                or any(not isinstance(text, str) or not text.strip() or len(text) > 32_000 for text in texts)):
            raise ProviderError("Embed between one and 64 nonempty texts, each no longer than 32,000 characters.")

    @staticmethod
    def _validate_vectors(vectors, count):
        if not isinstance(vectors, list) or len(vectors) != count:
            raise ProviderError("The local endpoint returned invalid embeddings.")
        dimensions = None
        for vector in vectors:
            if (not isinstance(vector, list) or not 1 <= len(vector) <= 16384
                    or any(type(number) not in {float, int} or not abs(number) <= 1e100 for number in vector)
                    or not any(vector) or dimensions not in {None, len(vector)}):
                raise ProviderError("The local endpoint returned invalid embedding vectors.")
            dimensions = len(vector)
        return vectors


class OllamaProvider(LocalOpenAIProvider):
    id = "ollama"

    def __init__(self, base_url="http://127.0.0.1:11434", api_key="", client=None):
        endpoint = local_base_url(base_url)
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        self._native_url = endpoint
        super().__init__(endpoint + "/v1", api_key, client)

    def discover_models(self) -> list[ModelInfo]:
        data = self._transport.request(self._native_url + "/api/tags", None, self._headers,
                                       method="GET", timeout=LOCAL_TIMEOUT)
        rows = data.get("models")
        if not isinstance(rows, list) or len(rows) > 512:
            raise ProviderError("Ollama returned an invalid model list.")
        models = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("Ollama returned invalid model metadata.")
            model = validate_local_model(row.get("name"))
            if not self._remote_model(model, row):
                models.append(ModelInfo(model, self.id))
        return models

    @staticmethod
    def _remote_model(model, data):
        return "cloud" in model.lower().split(":")[-1] or bool(data.get("remote_host") or data.get("remote_model"))

    def model_info(self, model):
        model = validate_local_model(model)
        if self._remote_model(model, {}):
            raise ProviderError("Ollama cloud models are disabled for local AI. Select a downloaded local model.")
        data = self._transport.request(self._native_url + "/api/show", {"model": model},
                                       self._headers, timeout=LOCAL_TIMEOUT)
        if self._remote_model(model, data):
            raise ProviderError("Ollama reports a remotely hosted model. It cannot be used for local AI.")
        capabilities = _capabilities(data.get("capabilities"))
        raw_info = data.get("model_info") or {}
        lengths = [_context_length(value) for key, value in raw_info.items()
                   if isinstance(key, str) and key.endswith(".context_length")] if isinstance(raw_info, dict) else []
        info = ModelInfo(model, self.id, capabilities, min((value for value in lengths if value), default=None))
        self._models[model] = info
        self.supports_vision = "vision" in capabilities
        self.supports_tools = "tools" in capabilities
        return info

    def embed(self, texts, model):
        self._validate_embeddings_input(texts)
        if "embeddings" not in self.model_info(model).capabilities:
            raise ProviderError("The selected Ollama model does not advertise embeddings support.")
        result = self._transport.request(self._native_url + "/api/embed",
            {"model": model, "input": texts, "truncate": False}, self._headers)
        return self._validate_vectors(result.get("embeddings"), len(texts))
