"""Real service/orchestrator/permission/tool/SQLite loops with mocked cloud HTTP only."""
from __future__ import annotations

import json
import socket
import threading

import httpx
import pytest

import jarvix.providers
from jarvix.providers import GeminiProvider, OpenAIProvider, PROVIDER_MODELS
from jarvix.services import Services


TEST_KEY = "integration-test-key-not-a-real-credential"
FINAL_ANSWER = "Finished the approved request."


class TestVault:
    __test__ = False

    def get(self, _):
        return TEST_KEY


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Any accidental live HTTP or socket connection must fail this test suite."""
    def forbidden(*_args, **_kwargs):
        pytest.fail("Provider integration tests must never connect to a live network.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
def services(tmp_path):
    instance = Services(tmp_path / "isolated-profile", vault=TestVault())
    yield instance
    instance.close()


class ScriptedEndpoint:
    """Simulate each vendor's wire protocol, not the AIProvider domain contract."""

    def __init__(self, provider_id: str, arguments: list[dict]):
        self.provider_id = provider_id
        self.arguments = arguments
        self.payloads: list[dict] = []
        self.gemini_parts: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert TEST_KEY not in request.content.decode()
        assert TEST_KEY not in str(request.url)
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if self.provider_id == "openai":
            assert request.url.host == "api.openai.com"
            assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
            if len(self.payloads) == 1:
                assert len(payload["tools"]) == 1
                alias = payload["tools"][0]["function"]["name"]
                calls = [{"id": f"call-{index}", "type": "function", "function": {
                    "name": alias, "arguments": json.dumps(arguments),
                }} for index, arguments in enumerate(self.arguments)]
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": calls,
                }}]})
            assert len(self.payloads) == 2
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": FINAL_ANSWER,
            }}]})
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == TEST_KEY
        if len(self.payloads) == 1:
            declarations = payload["tools"][0]["functionDeclarations"]
            assert len(declarations) == 1
            alias = declarations[0]["name"]
            self.gemini_parts = [{"text": "Internal provider thought", "thought": True}]
            for index, arguments in enumerate(self.arguments):
                part = {"functionCall": {"name": alias, "args": arguments, "id": f"call-{index}"}}
                if index == 0:
                    part["thoughtSignature"] = "opaque-signature-must-survive-the-real-orchestrator"
                self.gemini_parts.append(part)
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "role": "model", "parts": self.gemini_parts,
            }}]})
        assert len(self.payloads) == 2
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
            "role": "model", "parts": [{"text": FINAL_ANSWER}],
        }}]})

    def results(self) -> list[dict]:
        if self.provider_id == "openai":
            return [json.loads(message["content"]) for message in self.payloads[-1]["messages"]
                    if message["role"] == "tool"]
        return [part["functionResponse"]["response"] for content in self.payloads[-1]["contents"]
                for part in content["parts"] if "functionResponse" in part]


def run_chat(monkeypatch, services, provider_id, tool_name, arguments, approve):
    endpoint = ScriptedEndpoint(provider_id, arguments)
    provider_type = OpenAIProvider if provider_id == "openai" else GeminiProvider
    services.settings.set("tools.enabled", [tool_name])
    conversation_id = services.new_conversation()
    events = []
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        provider = provider_type(TEST_KEY, client)

        def factory(selected_id, key):
            assert selected_id == provider_id and key == TEST_KEY
            return provider

        monkeypatch.setattr(jarvix.providers, "create_provider", factory)
        answer = services.chat(
            "Perform this request using the selected tool.", conversation_id,
            provider_id, PROVIDER_MODELS[provider_id], approve,
            lambda name, value: events.append((name, value)), threading.Event(),
        )
    assert answer == FINAL_ANSWER
    assert len(endpoint.payloads) == 2
    assert [message["role"] for message in services.conversation_messages(conversation_id)] == ["user", "assistant"]
    assert services.conversation_messages(conversation_id)[-1]["content"] == FINAL_ANSWER
    assert services.latest_context["provider"] == provider_id
    if provider_id == "gemini":
        returned_model_content = endpoint.payloads[1]["contents"][-2]
        assert returned_model_content == {"role": "model", "parts": endpoint.gemini_parts}
        # Internal thought text is retained for protocol state, never an answer.
        assert "Internal provider thought" not in answer
    return endpoint, events


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_real_adapter_creates_note_after_execution_and_disclosure_approval(monkeypatch, services, provider_id):
    approvals = []

    def approve(request):
        approvals.append(request)
        return True

    endpoint, events = run_chat(monkeypatch, services, provider_id, "notes.create", [
        {"title": "Jarvix architecture", "body": "Tools use structured arguments."},
    ], approve)
    notes = services.list_notes()
    assert len(notes) == 1
    assert notes[0]["title"] == "Jarvix architecture"
    assert notes[0]["body"] == "Tools use structured arguments."
    assert [request.kind for request in approvals] == ["execute", "disclose"]
    assert approvals[0].tool_name == "notes.create"
    assert approvals[0].permission == "notes.write"
    assert endpoint.results() == [json.loads(approvals[1].preview)]
    assert endpoint.results()[0]["data"]["id"] == notes[0]["id"]
    assert [value["status"] for name, value in events if name == "tool"] == ["Running", "Complete"]


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_only_explicitly_approved_search_result_reaches_provider(monkeypatch, services, provider_id):
    services.save_note("Jarvix", "approved-local-detail")
    services.save_note("Unrelated", "unrelated-private-note")
    services.add_memory("unrelated-private-memory")
    approvals = []
    endpoint, _ = run_chat(monkeypatch, services, provider_id, "notes.search", [{"query": "Jarvix"}],
                          lambda request: approvals.append(request) or True)
    first_payload, second_payload = map(json.dumps, endpoint.payloads)
    assert "approved-local-detail" not in first_payload
    assert "approved-local-detail" in second_payload
    assert "unrelated-private-note" not in second_payload
    assert "unrelated-private-memory" not in second_payload
    assert [request.kind for request in approvals] == ["disclose"]
    assert endpoint.results() == [json.loads(approvals[0].preview)]
    assert "approved-local-detail" not in json.dumps(services.activity())


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_execution_denial_blocks_sqlite_write_before_tool_result(monkeypatch, services, provider_id):
    approvals = []
    endpoint, events = run_chat(monkeypatch, services, provider_id, "notes.create", [
        {"title": "Should not exist", "body": "Denied"},
    ], lambda request: approvals.append(request) or False)
    assert services.list_notes() == []
    assert [request.kind for request in approvals] == ["execute"]
    assert endpoint.results()[0]["ok"] is False
    assert "denied" in endpoint.results()[0]["error"].lower()
    assert not any(name == "tool" for name, _ in events)


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_disclosure_denial_withholds_real_note_body_from_http(monkeypatch, services, provider_id):
    services.save_note("Jarvix", "withheld-private-note-body")
    approvals = []

    def approve(request):
        approvals.append(request)
        return request.kind == "execute"

    endpoint, events = run_chat(monkeypatch, services, provider_id, "notes.search", [{"query": "Jarvix"}], approve)
    assert [request.kind for request in approvals] == ["disclose"]
    assert "withheld-private-note-body" in approvals[0].preview
    assert "withheld-private-note-body" not in json.dumps(endpoint.payloads)
    assert endpoint.results()[0]["disclosure_denied"] is True
    assert endpoint.results()[0]["data"] is None
    assert any(name == "tool" and value["status"] == "Complete" for name, value in events)


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_real_registry_schema_rejection_precedes_permission_and_execution(monkeypatch, services, provider_id):
    approvals = []
    endpoint, events = run_chat(monkeypatch, services, provider_id, "notes.create", [
        {"title": 42, "body": "Wrong type"},
    ], lambda request: approvals.append(request) or True)
    assert approvals == []
    assert services.list_notes() == []
    assert endpoint.results()[0]["ok"] is False
    assert "schema" in endpoint.results()[0]["error"]
    assert not any(name == "tool" for name, _ in events)


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_saved_execution_grant_never_implicitly_grants_cloud_disclosure(monkeypatch, services, provider_id):
    services.save_note("Jarvix", "still-private-after-saved-grant")
    services.permissions.set_grant("notes.search", "allow")
    approvals = []
    endpoint, _ = run_chat(monkeypatch, services, provider_id, "notes.search", [{"query": "Jarvix"}],
                          lambda request: approvals.append(request) or False)
    assert [request.kind for request in approvals] == ["disclose"]
    assert "still-private-after-saved-grant" not in json.dumps(endpoint.payloads)
    assert endpoint.results()[0]["disclosure_denied"] is True


@pytest.mark.parametrize("provider_id", ["openai", "gemini"])
def test_multiple_calls_keep_independent_approvals_and_result_correlation(monkeypatch, services, provider_id):
    approvals = []
    endpoint, _ = run_chat(monkeypatch, services, provider_id, "notes.create", [
        {"title": "First", "body": "One"}, {"title": "Second", "body": "Two"},
    ], lambda request: approvals.append(request) or True)
    notes = services.list_notes()
    assert {note["title"] for note in notes} == {"First", "Second"}
    assert [request.kind for request in approvals] == ["execute", "disclose", "execute", "disclose"]
    assert {result["data"]["id"] for result in endpoint.results()} == {note["id"] for note in notes}
    if provider_id == "gemini":
        responses = endpoint.payloads[1]["contents"][-1]["parts"]
        assert [part["functionResponse"]["id"] for part in responses] == ["call-0", "call-1"]
    else:
        results = [message for message in endpoint.payloads[1]["messages"] if message["role"] == "tool"]
        assert [result["tool_call_id"] for result in results] == ["call-0", "call-1"]
