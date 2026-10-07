"""Regressions for current context/privacy and saved state integration boundaries."""
import threading

import pytest

from jarvix.domain import Completion, Message, ToolCall, ToolResult
from jarvix.services import Services
from test_fusion_chat import Scripted, Vault, candidate, request


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=Vault())
    yield service
    service.close()


def test_custom_local_preference_requires_disclosure_when_only_cloud_available(services, monkeypatch):
    services.settings.set("routing.prefer_local", False)
    services.evaluation.configure_profile("custom", {"prefer_local": True, "prefer_cloud": False})
    cloud = Scripted(Message("assistant", "Sent without permission"))
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("openai", "cloud")])
    monkeypatch.setattr(services, "make_provider", lambda _: cloud)
    answer = request(services, lambda _: False)
    assert not cloud.requests
    assert "denied" in answer


def test_new_local_only_profile_blocks_next_cloud_round(services, monkeypatch):
    def change_profile(_):
        services.evaluation.configure_profile("local_only")
        return Completion(Message("assistant", tool_calls=[ToolCall("tasks", "tasks.list", {})]))
    cloud = Scripted(change_profile, Message("assistant", "Sent after local-only selection"))
    monkeypatch.setattr(services, "route_models", lambda *_, **__: [candidate("openai", "cloud")])
    monkeypatch.setattr(services, "make_provider", lambda _: cloud)
    services.settings.set("routing.prefer_local", False)
    id = services.new_conversation()
    services.chat("Work", id, "auto", "", lambda _: True, lambda *_: None, threading.Event())
    assert len(cloud.requests) == 1


def test_inspect_cannot_bypass_denied_temporary_session_read(services, monkeypatch):
    services.settings.set("context.enabled", True)
    services.context.remember_session("PRIVATE TEMPORARY CONTEXT")
    services.permissions.set_grant("context.session_inspect", "deny")
    real = services.execute_tool
    def no_window(name, args, **kwargs):
        return ToolResult(False) if name == "windows.foreground" else real(name, args, **kwargs)
    monkeypatch.setattr(services, "execute_tool", no_window)
    result = services.execute_tool("context.inspect", {"include_session": True})
    assert not result.ok or not result.data.get("session_context")


def test_continuity_reports_undone_verified_steps_as_unfinished(services):
    session = services.operator.run({"goal": "Observe tasks", "steps": [{"id": "tasks", "tool": "tasks.list", "arguments": {},
        "expected": {"tool": "tasks.list", "arguments": {}, "path": ["ok"], "equals": True}}]}, approve=lambda _: True)
    row = services.operator.get(session["id"])
    row["steps"][0]["undone"] = True
    services.records.put("operator_session", row, row["id"])
    saved = services.continuity.save("Review undone work", operator_session_id=row["id"])
    assert not saved["last_verified_state"]
    assert any(item.get("step_id") == "tasks" for item in saved["unfinished_work"])
