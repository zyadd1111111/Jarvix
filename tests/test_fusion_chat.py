"""Fallbacks retain tool receipts, respect disclosure, and never duplicate partials."""
import threading

import pytest

from jarvix.domain import Completion, Message, ProviderError, ToolCall
from jarvix.services import Services


class Vault:
    def get(self, _):
        return None


class Scripted:
    def __init__(self, *replies):
        self.replies, self.requests, self.closed = iter(replies), [], False

    def stream(self, messages, tools, model, on_delta):
        self.requests.append((list(messages), model))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(on_delta)
        return Completion(value)

    def close(self):
        self.closed = True


def candidate(provider, model):
    return {"provider": provider, "model": model, "is_local": provider in {"local", "ollama"},
            "capabilities": ["completion", "tools"], "context_length": 128000}


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=Vault())
    yield s
    s.close()


def request(s, approve, sink=lambda *_: None, cancel=None):
    id = s.new_conversation()
    answer = s.chat("Continue my work", id, "auto", "", approve, sink, cancel or threading.Event())
    assert [row["role"] for row in s.conversation_messages(id)] == ["user", "assistant"]
    return answer


@pytest.mark.parametrize("approved", [False, True])
def test_local_fallback_cloud_needs_exact_fresh_disclosure(services, monkeypatch, approved):
    local, cloud = Scripted(ProviderError("Local unavailable")), Scripted(Message("assistant", "Done"))
    choices = [candidate("local", "local"), candidate("openai", "cloud")]
    monkeypatch.setattr(services, "route_models", lambda *_, **__: choices)
    monkeypatch.setattr(services, "make_provider", lambda provider: local if provider == "local" else cloud)
    permissions, events = [], []
    def approve(value):
        permissions.append(value)
        return approved
    answer = request(services, approve, lambda kind, value: events.append((kind, value)))
    assert len(permissions) == 1 and permissions[0].tool_name == "models.fallback"
    assert permissions[0].arguments["messages"][-1]["content"] == "Continue my work"
    assert len(cloud.requests) == int(approved)
    assert local.closed
    if approved:
        assert answer == "Done" and cloud.closed
        assert services.latest_context["provider"] == "openai"
        assert services.latest_context["model"] == "cloud"
        assert any(kind == "model_selected" and value["fallback"] for kind, value in events)
    else:
        assert "remains local" in answer
    assert len(services.records.list("model_outcome")) == 1 + int(approved)


def test_unavailable_local_primary_still_requires_cloud_approval(services, monkeypatch):
    services.configure_model_role("chat", "local", "offline")
    cloud = Scripted(Message("assistant", "Never sent"))
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("openai", "cloud")])
    monkeypatch.setattr(services, "make_provider", lambda _: cloud)
    assert "denied" in request(services, lambda _: False)
    assert not cloud.requests


def test_unavailable_discovered_local_model_still_requires_cloud_approval(services, monkeypatch):
    # Local discovery need not save a model setting or a pinned task role.
    assert not services.settings.get("model.local", "")
    assert not services.settings.get("routing.roles", {})
    services.settings.set("routing.prefer_local", True)
    cloud = Scripted(Message("assistant", "Never sent"))
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("openai", "cloud")])
    monkeypatch.setattr(services, "make_provider", lambda _: cloud)
    assert "denied" in request(services, lambda _: False)
    assert not cloud.requests


def test_local_alternative_precedes_cloud_and_cancellation_stops_fallback(services, monkeypatch):
    first, second, cloud = Scripted(ProviderError("Offline")), Scripted(Message("assistant", "Local done")), Scripted()
    choices = [candidate("ollama", "first"), candidate("openai", "cloud"), candidate("local", "second")]
    monkeypatch.setattr(services, "route_models", lambda *_, **__: choices)
    monkeypatch.setattr(services, "make_provider", lambda provider: {"ollama": first, "local": second, "openai": cloud}[provider])
    assert request(services, lambda _: pytest.fail("No disclosure for local requests")) == "Local done"
    assert not cloud.requests
    cancel = threading.Event()
    def interrupted(_):
        cancel.set()
        raise ProviderError("Endpoint failed")
    first.replies = iter([interrupted])
    answer = request(services, lambda _: pytest.fail("Cancellation must precede approval"), cancel=cancel)
    assert "stopped" in answer
    assert len(second.requests) == 1


def test_partial_failure_is_persisted_once_without_fallback(services, monkeypatch):
    def interrupted(delta):
        delta("Kept partial")
        raise ProviderError("Disconnected")
    first, second = Scripted(interrupted), Scripted()
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("local", "first"), candidate("ollama", "next")])
    monkeypatch.setattr(services, "make_provider", lambda provider: first if provider == "local" else second)
    answer = request(services, lambda _: False)
    assert answer.startswith("Kept partial\n\nResponse interrupted:")
    assert not second.requests
    assert services.records.list("model_outcome")[0]["failures"] == 1


def test_fallback_after_tool_result_never_replays_write(services, monkeypatch):
    first = Scripted(Message("assistant", tool_calls=[ToolCall("note", "notes.create", {"title": "Fusion", "body": "One"})]),
                     ProviderError("Disconnected"))
    next_provider = Scripted(Message("assistant", "Saved once"))
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("local", "first"), candidate("ollama", "next")])
    monkeypatch.setattr(services, "make_provider", lambda provider: first if provider == "local" else next_provider)
    assert request(services, lambda _: True) == "Saved once"
    assert len(services.list_notes()) == 1
    assert next_provider.requests[0][0][-1].role == "tool"
    assert '"ok": true' in next_provider.requests[0][0][-1].content


def test_growing_tool_context_skips_small_fallback_without_replaying_tools(services, monkeypatch):
    from jarvix.domain import ToolResult, ToolSpec
    services.registry.register(ToolSpec("test.context", "Long result", {"type": "object"}, permission_level=1),
                               lambda _: ToolResult(True, {"text": "x" * 9000}))
    services.settings.set("tools.enabled", ["test.context"])
    first = Scripted(Message("assistant", tool_calls=[ToolCall("context", "test.context", {})]),
                     ProviderError("Disconnected"))
    small, sufficient = Scripted(), Scripted(Message("assistant", "Kept context"))
    choices = [candidate("local", "first"), {**candidate("ollama", "small"), "context_length": 6500},
               candidate("local", "sufficient")]
    monkeypatch.setattr(services, "route_models", lambda *_, **__: choices)
    monkeypatch.setattr(services, "make_provider", lambda provider: first if provider == "local" and not first.closed
                        else sufficient if provider == "local" else small)
    assert request(services, lambda _: True) == "Kept context"
    assert not small.requests
    assert sufficient.requests[0][0][-1].role == "tool"
    outcomes = services.records.list("model_outcome")
    assert {row["model"] for row in outcomes} == {"first", "sufficient"}
