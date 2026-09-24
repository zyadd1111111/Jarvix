"""Provider contract tests use local HTTPX transports; no keys or network needed."""
from __future__ import annotations

import json
import re
import threading

import httpx
import pytest

from jarvix.domain import Message, ProviderError, ToolCall, ToolSpec
from jarvix.providers import GeminiProvider, OpenAIProvider, PROVIDER_MODELS, create_provider
from jarvix.providers import _transport
from jarvix.runtime import operation


SPEC = ToolSpec("memory.create", "Remember a user-selected fact", {
    "type": "object", "properties": {"content": {"type": "string"}},
    "required": ["content"], "additionalProperties": False,
})
SECRET = "test-secret-never-log"


def test_provider_cancellation_after_network_response_discards_completion():
    cancel = threading.Event()
    def handler(request):
        cancel.set()
        return httpx.Response(200, json=openai_response("Must not be used"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, operation(cancel=cancel):
        with pytest.raises(InterruptedError):
            OpenAIProvider(SECRET, client).complete([Message("user", "Hello")], [], "test-model")


def openai_response(text="Hello", calls=None, finish=None):
    return {"choices": [{"finish_reason": finish or ("tool_calls" if calls else "stop"),
                         "message": {"role": "assistant", "content": text, "tool_calls": calls}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def gemini_response(parts=None, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {
        "role": "model", "parts": parts if parts is not None else [{"text": "Hello"}],
    }}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}}


@pytest.mark.parametrize("provider_type,model,response", [
    (OpenAIProvider, "gpt-4.1-mini", openai_response()),
    (GeminiProvider, "gemini-2.5-flash", gemini_response()),
])
def test_text_completion_uses_only_explicit_context(provider_type, model, response):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = provider_type(SECRET, client).complete([
            Message("system", "Be helpful"), Message("user", "Hello", metadata={"private": "DO NOT SEND"}),
        ], [], model)
    assert result.message.content == "Hello"
    assert result.message.role == "assistant"
    assert result.usage
    request = requests[0]
    assert "DO NOT SEND" not in request.content.decode()
    assert SECRET not in str(request.url)
    assert request.url.scheme == "https"
    assert request.extensions["timeout"]["read"] == 60
    assert len(requests) == 1


def test_openai_tool_cycle_and_dotted_registry_names():
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            function = payload["tools"][0]["function"]
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", function["name"])
            assert function["parameters"] == SPEC.parameters
            assert payload["store"] is False
            assert payload["parallel_tool_calls"] is False
            assert request.headers["Authorization"] == f"Bearer {SECRET}"
            return httpx.Response(200, json=openai_response(None, [{
                "id": "call-1", "type": "function", "function": {
                    "name": function["name"], "arguments": '{"content":"Jarvix is my main project"}',
                },
            }]))
        return httpx.Response(200, json=openai_response("Remembered."))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIProvider(SECRET, client)
        messages = [Message("user", "Remember my project")]
        call_message = provider.complete(messages, [SPEC], "gpt-4.1-mini").message
        assert call_message.tool_calls == [ToolCall("call-1", "memory.create", {"content": "Jarvix is my main project"})]
        messages.extend([call_message, Message("tool", '{"ok":true}', tool_call_id="call-1", name=SPEC.name)])
        assert provider.complete(messages, [SPEC], "gpt-4.1-mini").message.content == "Remembered."
    second = payloads[1]["messages"]
    assert second[-1] == {"role": "tool", "content": '{"ok":true}', "tool_call_id": "call-1"}
    assert second[-2]["tool_calls"][0]["function"]["name"] == payloads[0]["tools"][0]["function"]["name"]


def test_gemini_parallel_cycle_retains_thought_signatures_and_call_ids():
    payloads = []
    raw_parts = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            function = payload["tools"][0]["functionDeclarations"][0]
            assert function["parametersJsonSchema"] == SPEC.parameters
            assert request.headers["x-goog-api-key"] == SECRET
            assert payload["systemInstruction"] == {"parts": [{"text": "Be helpful"}]}
            raw_parts.extend([
                {"text": "Internal thought", "thought": True},
                {"functionCall": {"name": function["name"], "args": {"content": "one"}, "id": "provider-id"},
                 "thoughtSignature": "opaque-signature"},
                {"functionCall": {"name": function["name"], "args": {"content": "two"}}},
            ])
            return httpx.Response(200, json=gemini_response(raw_parts))
        return httpx.Response(200, json=gemini_response([{"text": "Remembered."}, {"thoughtSignature": "text-signature"}]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider(SECRET, client)
        messages = [Message("system", "Be helpful"), Message("user", "Remember both")]
        assistant = provider.complete(messages, [SPEC], "gemini-2.5-flash").message
        assert assistant.content == ""
        assert assistant.tool_calls[0].name == SPEC.name
        assert assistant.tool_calls[0].id == "provider-id"
        messages.append(assistant)
        for call in assistant.tool_calls:
            messages.append(Message("tool", '{"ok":true}', tool_call_id=call.id, name=call.name))
        assert provider.complete(messages, [SPEC], "gemini-2.5-flash").message.content == "Remembered."
    contents = payloads[1]["contents"]
    assert contents[-2] == {"role": "model", "parts": raw_parts}
    results = contents[-1]["parts"]
    assert len(results) == 2
    assert results[0]["functionResponse"]["id"] == "provider-id"
    assert "id" not in results[1]["functionResponse"]
    assert results[0]["functionResponse"]["response"] == {"ok": True}


@pytest.mark.parametrize("provider_type", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("status,match", [
    (401, "authentication"), (403, "authentication"), (429, "quota"),
    (400, "model"), (404, "model"), (500, "unavailable"), (302, "redirect"),
])
def test_errors_are_safe_and_requests_are_never_retried(provider_type, status, match):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=f"server echoed {SECRET} private content", headers={"Location": "https://evil.example"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ProviderError, match=match) as caught:
            provider_type(SECRET, client).complete([Message("user", "private content")], [], "test-model")
    assert SECRET not in str(caught.value)
    assert "private content" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(calls) == 1


@pytest.mark.parametrize("error,match", [(httpx.ReadTimeout, "timed out"), (httpx.ConnectError, "network")])
def test_network_errors_are_sanitized(error, match):
    def handler(request):
        raise error(f"internal detail {SECRET}", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match=match) as caught:
            OpenAIProvider(SECRET, client).complete([Message("user", "hello")], [], "test-model")
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("arguments", ['[]', 'null', '{"x":NaN}', '{"x":1,"x":2}', '{"x":'])
def test_openai_rejects_ambiguous_or_invalid_arguments(arguments):
    def handler(request):
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        return httpx.Response(200, json=openai_response(None, [{
            "id": "call-1", "type": "function", "function": {"name": name, "arguments": arguments},
        }]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            OpenAIProvider(SECRET, client).complete([Message("user", "remember")], [SPEC], "test-model")


@pytest.mark.parametrize("provider_type,response", [
    (OpenAIProvider, {"choices": []}),
    (OpenAIProvider, openai_response(text={"invalid": "content"})),
    (OpenAIProvider, openai_response("partial", finish="length")),
    (OpenAIProvider, openai_response(None, [{"id": "a", "type": "function", "function": {"name": "unknown", "arguments": "{}"}}])),
    (GeminiProvider, {"candidates": []}),
    (GeminiProvider, gemini_response([{"text": "partial"}], "MAX_TOKENS")),
    (GeminiProvider, {"promptFeedback": {"blockReason": "SAFETY"}}),
    (GeminiProvider, gemini_response([{"inlineData": {"data": "unsupported"}}])),
    (GeminiProvider, gemini_response([{"functionCall": {"name": "unknown", "args": {}}}])),
])
def test_invalid_and_incomplete_responses_do_not_produce_tool_calls(provider_type, response):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as client:
        with pytest.raises(ProviderError):
            provider_type(SECRET, client).complete([Message("user", "hi")], [SPEC], "test-model")


@pytest.mark.parametrize("response_body", [b"not json", b'{"secret":NaN}', b'{"a":1,"a":2}', b"[]"])
def test_transport_rejects_malformed_json(response_body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=response_body))) as client:
        with pytest.raises(ProviderError):
            OpenAIProvider(SECRET, client).complete([Message("user", "hi")], [], "test-model")


def test_request_and_response_limits_fail_explicitly(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 100)

    monkeypatch.setattr(_transport, "MAX_REQUEST_BYTES", 1000)
    monkeypatch.setattr(_transport, "MAX_RESPONSE_BYTES", 50)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIProvider(SECRET, client)
        with pytest.raises(ProviderError, match="request limit"):
            provider.complete([Message("user", "x" * 2000)], [], "test-model")
        assert not calls
        with pytest.raises(ProviderError, match="size limit"):
            provider.complete([Message("user", "hi")], [], "test-model")
    assert len(calls) == 1


@pytest.mark.parametrize("model", ["../other", "https://evil.example", "foo?key=x", "", "a/b", "x\r\ny"])
def test_model_identifier_cannot_change_endpoint(model):
    with pytest.raises(ProviderError, match="model identifier"):
        GeminiProvider(SECRET).complete([Message("user", "hi")], [], model)


def test_factory_and_configuration_errors():
    assert create_provider("openai", SECRET).id == "openai"
    assert create_provider("gemini", SECRET).id == "gemini"
    assert set(PROVIDER_MODELS) == {"openai", "gemini"}
    with pytest.raises(ProviderError, match="supported"):
        create_provider("unknown", SECRET)
    with pytest.raises(ProviderError, match="API key"):
        create_provider("openai", "   ")


def test_missing_gemini_tool_result_fails_before_network():
    messages = [Message("assistant", tool_calls=[ToolCall("call-1", SPEC.name, {})])]
    with pytest.raises(ProviderError, match="unfinished"):
        GeminiProvider(SECRET).complete(messages, [SPEC], "test-model")


def test_names_that_look_like_aliases_do_not_collide():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=openai_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIProvider(SECRET, client)
        provider.complete([Message("user", "hi")], [SPEC], "test-model")
        alias = payloads[0]["tools"][0]["function"]["name"]
        provider.complete([Message("user", "hi")], [SPEC, ToolSpec(alias, "another tool", {})], "test-model")
    names = [tool["function"]["name"] for tool in payloads[1]["tools"]]
    assert len(set(names)) == 2


@pytest.mark.parametrize("provider_type", [OpenAIProvider, GeminiProvider])
def test_duplicate_call_ids_are_rejected_before_any_execution(provider_type):
    def handler(request):
        payload = json.loads(request.content)
        if provider_type is OpenAIProvider:
            name = payload["tools"][0]["function"]["name"]
            call = {"id": "duplicate", "type": "function", "function": {"name": name, "arguments": "{}"}}
            return httpx.Response(200, json=openai_response(None, [call, call]))
        name = payload["tools"][0]["functionDeclarations"][0]["name"]
        part = {"functionCall": {"id": "duplicate", "name": name, "args": {}}}
        return httpx.Response(200, json=gemini_response([part, part]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            provider_type(SECRET, client).complete([Message("user", "hi")], [SPEC], "test-model")


@pytest.mark.parametrize("extra", [{"text": "mixed"}, {"thought": True}, {"thought": "true"}])
def test_gemini_does_not_execute_calls_in_invalid_parts(extra):
    def handler(request):
        name = json.loads(request.content)["tools"][0]["functionDeclarations"][0]["name"]
        return httpx.Response(200, json=gemini_response([{"functionCall": {"name": name, "args": {}}, **extra}]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            GeminiProvider(SECRET, client).complete([Message("user", "hi")], [SPEC], "test-model")
