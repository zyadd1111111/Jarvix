import json
import threading

import httpx
import pytest

from jarvix.domain import ImageAttachment, Message, ProviderError, ToolSpec
from jarvix.providers import GeminiProvider, OpenAIProvider, OllamaProvider, LocalOpenAIProvider, create_provider
from jarvix.providers.local import local_base_url
from jarvix.runtime import operation

MODEL = "org/local-model:latest"
SPEC = ToolSpec("notes.search", "Search notes", {"type": "object", "properties": {}})


def completion(text="Hello"):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "http://192.168.1.2/v1", "http://127.0.0.1.evil/v1",
    "http://user:secret@127.0.0.1/v1", "http://127.0.0.1/v1?secret=x", "http://127.0.0.1/v1#x",
    "http://127.0.0.1/../v1", "http://[::1%25x]/v1", "file:///tmp/model", "http://localhost:0/v1",
])
def test_local_endpoint_cannot_route_private_prompts_off_machine(url):
    with pytest.raises(ProviderError, match="loopback"):
        LocalOpenAIProvider(url)


def test_local_url_normalizes_localhost_without_dns_and_supports_ipv6():
    assert local_base_url("http://localhost:1234/v1/") == "http://127.0.0.1:1234/v1"
    assert local_base_url("http://[::1]:11434") == "http://[::1]:11434"
    assert create_provider("ollama").is_local is True
    with pytest.raises(ProviderError, match="Local-only"):
        create_provider("openai", "secret", local_only=True)


def test_compatible_endpoint_uses_chat_transport_without_inventing_capabilities():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        assert payload["messages"] == [{"role": "user", "content": "Hello"}]
        assert "tools" not in payload and "store" not in payload and "max_completion_tokens" not in payload
        assert "max_tokens" in payload
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=completion())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = LocalOpenAIProvider(client=client)
        result = provider.complete([Message("user", "Hello")], [SPEC], MODEL)
        assert result.message.metadata == {"provider": "local", "model": MODEL}
        assert not provider.model_info(MODEL).capabilities
        assert provider.supports_vision is False and provider.supports_tools is False
    assert len(calls) == 2


def test_ollama_capabilities_gate_tools_and_vision_and_chat_is_schema_validated():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools", "vision"],
                "model_info": {"llama.context_length": 32768}})
        payload = json.loads(request.content)
        name = payload["tools"][0]["function"]["name"]
        assert payload["messages"][0]["content"][1]["type"] == "image_url"
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {
                "name": name, "arguments": "{}"}}]}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(client=client)
        result = provider.complete([Message("user", "Explain", images=[ImageAttachment("x", "image/png", "eA==")])],
                                   [SPEC], MODEL)
        assert result.message.tool_calls[0].name == SPEC.name
        assert provider._models[MODEL].context_length == 32768
    assert [request.url.path for request in calls] == ["/api/show", "/v1/chat/completions"]


@pytest.mark.parametrize("data,model", [
    ({"capabilities": ["completion"], "remote_host": "https://ollama.com"}, MODEL),
    ({"capabilities": ["completion"], "remote_model": "remote"}, MODEL),
    ({"capabilities": ["completion"]}, "model:cloud"),
])
def test_ollama_cloud_relay_is_not_a_local_provider(data, model):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json=data))) as client:
        with pytest.raises(ProviderError, match="cloud|remotely"):
            OllamaProvider(client=client).complete([Message("user", "private")], [], model)
    assert all(request.url.path == "/api/show" for request in calls)


def test_local_models_and_health_do_not_follow_redirects_or_claim_online():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        status = LocalOpenAIProvider(client=client).health()
    assert status["available"] is False
    assert status["models"] == [] and "redirect" in status["error"]


def test_ollama_discovery_omits_cloud_models_and_health_reports_actual_endpoint():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [
        {"name": "model:local"}, {"name": "model:cloud"}, {"name": "alias", "remote_model": "remote"}]}))) as client:
        status = OllamaProvider(client=client).health()
    assert status["available"] is True
    assert [item["id"] for item in status["models"]] == ["model:local"]
    assert status["models"][0]["capabilities"] == []


def test_unsupported_local_vision_stops_before_image_disclosure():
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or
                     httpx.Response(200, json={"data": [{"id": MODEL}]}))) as client:
        with pytest.raises(ProviderError, match="image support"):
            LocalOpenAIProvider(client=client).complete([Message("user", "Explain",
                images=[ImageAttachment("x", "image/png", "eA==")])], [], MODEL)
    assert len(requests) == 1 and requests[0].method == "GET"


def test_ollama_embeddings_use_verified_local_model_and_validate_vectors():
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["embedding"]})
        assert request.url.path == "/api/embed"
        assert json.loads(request.content) == {"model": MODEL, "input": ["first", "second"], "truncate": False}
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert OllamaProvider(client=client).embed(["first", "second"], MODEL) == [[1.0, 0.0], [0.0, 1.0]]


def test_compatible_embedding_order_is_validated_and_restored():
    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": MODEL, "capabilities": {"embeddings": True}}]})
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert LocalOpenAIProvider(client=client).embed(["first", "second"], MODEL) == [[1, 0], [0, 1]]


@pytest.mark.parametrize("vectors", [[], [[0, 0]], [[True, 1]], [[1], [1, 2]], [[10**1000, 1]], [[float("nan"), 1]]])
def test_embedding_vector_validation_rejects_invalid_or_unsafe_results(vectors):
    with pytest.raises(ProviderError, match="embedding"):
        LocalOpenAIProvider._validate_vectors(vectors, max(1, len(vectors)))


def test_local_streaming_reuses_sse_validation_and_cancellation():
    cancel = threading.Event()
    deltas = []

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        event = {"choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    def delta(text):
        deltas.append(text)
        cancel.set()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, operation(cancel=cancel):
        with pytest.raises(InterruptedError):
            LocalOpenAIProvider(client=client).stream([Message("user", "Hello")], [], MODEL, delta)
    assert deltas == ["Hello"]


@pytest.mark.parametrize("provider", [OpenAIProvider, GeminiProvider, LocalOpenAIProvider, OllamaProvider])
def test_provider_close_preserves_injected_transport_ownership(provider):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        instance = provider(client=client) if provider in {LocalOpenAIProvider, OllamaProvider} else provider("test-key", client=client)
        instance.close()
        instance.close()
        assert not client.is_closed
