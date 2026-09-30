"""Small, bounded HTTP boundary; provider payloads and credentials are never logged."""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx

from jarvix.domain import ProviderError
from jarvix.runtime import CURRENT, cancel_response, check_cancelled

MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_TOOL_CALLS = 16
MAX_OUTPUT_TOKENS = 4096
MAX_IMAGE_REQUEST_BYTES = 18 * 1024 * 1024
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

    def request(self, url: str, payload: dict[str, Any] | None, headers: dict[str, str],
                *, method: str = "POST", timeout: httpx.Timeout = TIMEOUT) -> dict[str, Any]:
        check_cancelled()
        body = dump_json(payload).encode("utf-8") if payload is not None else None
        if body is not None and len(body) > self._request_limit(payload):
            raise ProviderError("This conversation exceeds the local request limit. Start a new conversation or shorten it.")
        # A fresh owned client is closed even on error. HTTPX's default transport
        # has zero retries; redirects are disabled even on injected test clients.
        client = self._client or httpx.Client(timeout=TIMEOUT, trust_env=False)
        try:
            with client.stream(
                method, url, content=body,
                headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
                timeout=timeout, follow_redirects=False,
            ) as response, cancel_response(response):
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
            check_cancelled()
            raise ProviderError("The AI provider timed out. Check the connection and try again; Jarvix did not retry automatically.") from None
        except (httpx.HTTPError, OSError):
            check_cancelled()
            raise ProviderError("Could not reach the AI provider securely. Check your network connection and try again.") from None
        finally:
            if self._client is None:
                client.close()

    @staticmethod
    def _request_limit(payload):
        # Images are independently bounded and approved before encoding. Ordinary
        # text keeps the much smaller existing context limit.
        messages = payload.get("messages", [])
        contents = payload.get("contents", [])
        has_images = any(isinstance(m.get("content"), list) and any(
            p.get("type") == "image_url" for p in m["content"]) for m in messages)
        has_images |= any(any("inlineData" in p for p in c.get("parts", [])) for c in contents)
        return MAX_IMAGE_REQUEST_BYTES if has_images else MAX_REQUEST_BYTES

    def events(self, url: str, payload: dict, headers: dict) -> Iterator[dict]:
        """Bounded SSE decoding, with cancellation even while waiting for bytes.

        A JSON response is accepted for compatible gateways; it remains subject
        to the adapter's full completion validation and is never blindly retried.
        """
        check_cancelled()
        body = dump_json(payload).encode("utf-8")
        if len(body) > self._request_limit(payload):
            raise ProviderError("This conversation exceeds the local request limit. Shorten it or remove images.")
        client = self._client or httpx.Client(timeout=TIMEOUT, trust_env=False)
        done = threading.Event()
        watcher = None
        context = CURRENT.get()
        try:
            with client.stream("POST", url, content=body, headers={
                "Content-Type": "application/json", "Accept": "text/event-stream", **headers,
            }, timeout=TIMEOUT, follow_redirects=False) as response:
                self._check_status(response.status_code)
                if context:
                    def watch():
                        while not done.wait(.1):
                            if context.cancel.is_set() or time.monotonic() > context.deadline:
                                try:
                                    response.close()
                                except Exception:
                                    pass
                                return
                    watcher = threading.Thread(target=watch, name="jarvix-stream-cancel", daemon=True)
                    watcher.start()
                total = 0
                buffer = b""
                event_lines = []
                json_response = "application/json" in response.headers.get("content-type", "").lower()
                for chunk in response.iter_bytes():
                    check_cancelled()
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ProviderError("The AI response exceeded the local size limit. No tool was executed.")
                    buffer += chunk
                    if json_response:
                        continue
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        try:
                            line = raw.rstrip(b"\r").decode("utf-8")
                        except UnicodeError:
                            raise ProviderError("The provider returned invalid stream encoding.") from None
                        if line.startswith("data:"):
                            event_lines.append(line[5:].lstrip(" "))
                        elif not line and event_lines:
                            value = "\n".join(event_lines)
                            event_lines = []
                            if value == "[DONE]":
                                check_cancelled()
                                return
                            parsed = load_json(value)
                            if not isinstance(parsed, dict):
                                raise ProviderError("The provider returned a malformed stream event.")
                            yield parsed
                check_cancelled()
                if json_response:
                    parsed = load_json(buffer)
                    if not isinstance(parsed, dict):
                        raise ProviderError("The provider returned a malformed response.")
                    yield parsed
                elif buffer.strip() or event_lines:
                    raise ProviderError("The provider stream ended before its final event. No tool was executed.")
        except httpx.TimeoutException:
            check_cancelled()
            raise ProviderError("The AI provider timed out. The partial response was kept locally.") from None
        except (httpx.HTTPError, OSError):
            check_cancelled()
            raise ProviderError("The AI provider connection was interrupted. The partial response was kept locally.") from None
        finally:
            done.set()
            if watcher:
                watcher.join(timeout=.3)
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
