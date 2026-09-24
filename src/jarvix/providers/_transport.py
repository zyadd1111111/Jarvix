"""Small, bounded HTTP boundary; provider payloads and credentials are never logged."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from jarvix.domain import ProviderError
from jarvix.runtime import check_cancelled

MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_TOOL_CALLS = 16
MAX_OUTPUT_TOKENS = 4096
TIMEOUT = httpx.Timeout(connect=10, read=60, write=15, pool=10)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("Nonfinite JSON number")


def load_json(value: str | bytes) -> Any:
    """Reject ambiguous objects and nonstandard numeric values from the model."""
    try:
        return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("The AI provider returned invalid JSON. No tool was executed.") from None


def dump_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        raise ProviderError("The AI request contains unsupported data. Check the conversation and tools.") from None


def arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError("The AI provider returned invalid tool arguments. No tool was executed.")
    if len(dump_json(value).encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ProviderError("The AI provider returned oversized tool arguments. No tool was executed.")
    return value


def validate_model(model: str) -> str:
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
        raise ProviderError("Enter a valid model identifier in Settings.")
    return model


class Transport:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("Add this provider's API key in Settings before sending a message.")
        if len(api_key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in api_key.strip()):
            raise ProviderError("The API key has an invalid format. Check Settings.")
        self._api_key = api_key.strip()
        self._client = client

    def request(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        check_cancelled()
        body = dump_json(payload).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise ProviderError("This conversation exceeds the local request limit. Start a new conversation or shorten it.")
        # A fresh owned client is closed even on error. HTTPX's default transport
        # has zero retries; redirects are disabled even on injected test clients.
        client = self._client or httpx.Client(timeout=TIMEOUT, trust_env=False)
        try:
            with client.stream(
                "POST", url, content=body,
                headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
                timeout=TIMEOUT, follow_redirects=False,
            ) as response:
                self._check_status(response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=16 * 1024):
                    check_cancelled()
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ProviderError("The AI response exceeded the local size limit. No tool was executed.")
                parsed = load_json(bytes(data))
                check_cancelled()
                if not isinstance(parsed, dict):
                    raise ProviderError("The AI provider returned an unexpected response. No tool was executed.")
                return parsed
        except httpx.TimeoutException:
            raise ProviderError("The AI provider timed out. Check the connection and try again; Jarvix did not retry automatically.") from None
        except httpx.HTTPError:
            raise ProviderError("Could not reach the AI provider securely. Check your network connection and try again.") from None
        finally:
            if self._client is None:
                client.close()

    @staticmethod
    def _check_status(status: int) -> None:
        if 200 <= status < 300:
            return
        if status in (401, 403):
            message = "AI provider authentication failed. Check the API key and account access in Settings."
        elif status == 429:
            message = "AI provider rate or quota limit reached. Check your account quota and try again later."
        elif status in (400, 404, 422):
            message = "The AI provider rejected this request. Check the model identifier and tool support in Settings."
        elif status >= 500:
            message = "The AI provider is temporarily unavailable. Try again later."
        elif 300 <= status < 400:
            message = "The AI provider returned an unexpected redirect. The request was not forwarded."
        else:
            message = "The AI provider could not complete this request. Check your provider configuration."
        raise ProviderError(message)
