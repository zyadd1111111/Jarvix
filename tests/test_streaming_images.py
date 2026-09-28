"""Streaming/vision boundaries use real adapters and mocked HTTP only."""
import base64
import json
import threading

import httpx
import pytest

from jarvix.attachments import disclose_images, prepare_images, context_message
from jarvix.domain import ImageAttachment, Message, ProviderError, ToolSpec
from jarvix.providers import GeminiProvider, OpenAIProvider
from jarvix.runtime import operation
from jarvix.services import Services


class Vault:
    def get(self, _):
        return "local-test-key"


@pytest.fixture
def services(tmp_path):
    instance = Services(tmp_path / "profile", vault=Vault())
    yield instance
    instance.close()


def wire(events, done=False):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode() + (b"data: [DONE]\n\n" if done else b"")


def openai_event(text="", finish=None, calls=None):
    return {"choices": [{"index": 0, "delta": {"content": text, "tool_calls": calls}, "finish_reason": finish}]}


def test_openai_stream_assembles_arguments_before_returning_call():
    spec = ToolSpec("notes.create", "Create note", {"type": "object"})
    def endpoint(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        name = payload["tools"][0]["function"]["name"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire([
            openai_event("Preparing "), openai_event("note", calls=[{"index": 0, "id": "one",
                "function": {"name": name, "arguments": '{"title":'}}]),
            openai_event(calls=[{"index": 0, "function": {"arguments": '"Nexus"}'}}]),
            openai_event(finish="tool_calls"), {"choices": [], "usage": {"completion_tokens": 15}},
        ], done=True))
    fragments = []
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        answer = OpenAIProvider("test", client).stream([Message("user", "Note")], [spec], "model", fragments.append)
    assert fragments == ["Preparing ", "note"]
    assert answer.message.tool_calls[0].arguments == {"title": "Nexus"}
    assert answer.usage["completion_tokens"] == 15


@pytest.mark.parametrize("ending", [None, "length", "tool_calls"])
def test_truncated_or_malformed_stream_never_returns_tool(ending):
    payload = wire([openai_event("Partial", calls=[{"index": 0, "id": "one", "function": {
        "name": "bad", "arguments": '{"unfinished":',
    }}]), openai_event(finish=ending)], done=True)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        with pytest.raises(ProviderError):
            OpenAIProvider("test", client).stream([Message("user", "Hi")], [], "model", lambda _: None)


def test_gemini_stream_hides_thoughts_and_keeps_signatures():
    events = [
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "hidden", "thought": True}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Visible ", "thoughtSignature": "opaque"}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "answer"}]}, "finishReason": "STOP"}],
         "usageMetadata": {"candidatesTokenCount": 4}},
    ]
    fragments = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=wire(events)))) as client:
        result = GeminiProvider("test", client).stream([Message("user", "Hi")], [], "model", fragments.append)
    assert "".join(fragments) == result.message.content == "Visible answer"
    assert result.message.metadata["gemini_parts"][1]["thoughtSignature"] == "opaque"
    assert "hidden" not in json.dumps(context_message(result.message))


def test_stream_cancel_persists_one_partial_answer_without_executing_tools(services, monkeypatch):
    cancel = threading.Event()
    events = []
    def sink(kind, value):
        events.append(kind)
        if kind == "text_delta":
            cancel.set()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=wire([
        openai_event("First part"), openai_event("Not delivered", finish="stop"),
    ], done=True)))) as client:
        monkeypatch.setattr("jarvix.providers.create_provider", lambda *_: OpenAIProvider("test", client))
        conversation = services.new_conversation()
        answer = services.chat("Hello", conversation, "openai", "model", lambda _: False, sink, cancel)
    assert answer.startswith("First part")
    assert "Not delivered" not in answer
    rows = services.conversation_messages(conversation)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[-1]["content"] == answer
    assert "tool" not in events


def test_stream_failure_preserves_partial_and_does_not_retry(services, monkeypatch):
    calls = []
    def endpoint(request):
        calls.append(request)
        return httpx.Response(200, content=wire([openai_event("Kept locally")]))
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        monkeypatch.setattr("jarvix.providers.create_provider", lambda *_: OpenAIProvider("test", client))
        conversation = services.new_conversation()
        answer = services.chat("Hello", conversation, "openai", "model", lambda _: False,
                               lambda *_: None, threading.Event())
    assert len(calls) == 1
    assert answer.startswith("Kept locally\n\nResponse interrupted:")
    assert len(services.conversation_messages(conversation)) == 2


def test_sse_accepts_fragmented_utf8_and_crlf():
    class Bytes(httpx.SyncByteStream):
        def __iter__(self):
            payload = wire([openai_event("🌍", finish="stop")], done=True).replace(b"\n", b"\r\n")
            yield from (payload[i:i+1] for i in range(len(payload)))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Bytes()))) as client:
        result = OpenAIProvider("test", client).stream([Message("user", "Hi")], [], "model", lambda _: None)
    assert result.message.content == "🌍"


def test_image_preparation_removes_metadata_and_approval_snapshot_is_immutable(tmp_path, services):
    from PySide6.QtGui import QImage
    path = tmp_path / "selected.png"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xff003399)
    image.setText("GPS", "private location")
    assert image.save(str(path), "PNG")
    observed = []
    def approve(request):
        observed.append(request)
        path.write_bytes(b"changed after review")
        return True
    with operation():
        prepared = disclose_images([path], OpenAIProvider("test"), "model", approve, services.repository)
    assert observed[0].images[0] is prepared[0]
    assert b"private location" not in base64.b64decode(prepared[0].data_base64)
    assert prepared[0].width == 8
    assert "data_base64" not in json.dumps(context_message(Message("user", "Inspect", images=prepared)))
    with pytest.raises(ValueError, match="valid"):
        prepare_images([path])


@pytest.mark.parametrize("approved", [False, True])
def test_image_disclosure_is_once_and_never_auto_included_in_next_request(services, tmp_path, monkeypatch, approved):
    from PySide6.QtGui import QImage
    path = tmp_path / "image.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xff123456)
    image.save(str(path), "PNG")
    calls, permissions = [], []
    def endpoint(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=wire([openai_event("Answer", finish="stop")], done=True))
    def approve(request):
        permissions.append(request)
        return approved
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        monkeypatch.setattr("jarvix.providers.create_provider", lambda *_: OpenAIProvider("test", client))
        conversation = services.new_conversation()
        services.chat("What is this?", conversation, "openai", "model", approve,
                      lambda *_: None, threading.Event(), attachments=[path])
        assert len(calls) == int(approved)
        services.chat("Next request", conversation, "openai", "model", approve,
                      lambda *_: None, threading.Event())
    assert len(permissions) == 1 and permissions[0].permission == "cloud.vision"
    assert "image_url" not in json.dumps(calls[-1]["messages"])
    assert "data_base64" not in json.dumps(services.conversation_messages(conversation))


@pytest.mark.parametrize("provider_type", [OpenAIProvider, GeminiProvider])
def test_both_providers_encode_only_explicit_image(provider_type):
    captured = []
    def endpoint(request):
        captured.append(json.loads(request.content))
        response = ({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}]}
                    if provider_type is OpenAIProvider else {"candidates": [{"finishReason": "STOP", "content": {
                        "role": "model", "parts": [{"text": "OK"}]}}]})
        return httpx.Response(200, json=response)
    image = ImageAttachment("private-source.png", "image/png", "cGl4ZWxz")
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        provider_type("test", client).complete([Message("user", "Inspect", images=[image])], [], "model")
    encoded = json.dumps(captured)
    assert "cGl4ZWxz" in encoded
    assert "private-source.png" not in encoded

