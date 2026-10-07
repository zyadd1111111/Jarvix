"""Saved outcome evidence, routing profiles and notification/brief opt-in boundaries."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from jarvix.capabilities import daily, evaluation
from jarvix.capabilities.schema import register
from jarvix.domain import ProviderError, ToolResult
from jarvix.model_router import ModelCandidate, ModelRouter
from jarvix.runtime import operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile")
    if not hasattr(service, "evaluation"):
        evaluation.setup(service, service.registry)
    if not hasattr(service, "daily"):
        daily.setup(service, service.registry)
    yield service
    service.close()


def test_evaluation_uses_real_outcomes_and_never_result_values(services):
    services.operator.run({"goal": "PRIVATE GOAL", "steps": [{"id": "tasks", "tool": "tasks.list", "arguments": {},
                          "expected": {"tool": "tasks.list", "arguments": {}, "path": ["ok"], "equals": True}}]},
                          approve=lambda _: True)
    services.record_model_outcome("ollama", "local", False, 10)
    services.record_model_outcome("ollama", "local", True, 30)
    services.records.put("operator_session", {"status": "interrupted", "goal": "PRIVATE GOAL",
        "steps": [{"status": "interrupted", "retry_safe": False, "failure_code": "process_interrupted", "attempts": 1}]})
    services.records.put("workflow_run", {"status": "complete", "test_mode": True,
        "steps": [{"status": "complete", "verified": True, "result": "PRIVATE RESULT"}]})
    report = services.evaluation.report()
    assert report["sources"]["operator"]["sample_count"] == 2
    assert report["sources"]["operator"]["uncertain_steps"] == 1
    assert report["sources"]["operator"]["verified_complete"] == 1
    assert report["sources"]["workflows"]["test_runs_excluded"] == 1
    assert report["sources"]["models"]["items"][0]["latency_ms"] == 16
    assert report["sources"]["tools"]["sample_count"] >= 1
    assert "PRIVATE" not in json.dumps(report)
    assert not report["automatic_tuning"] and not report["cloud_request"]


def test_evaluation_rechecks_denials_and_missing_samples_are_unknown(services):
    assert services.evaluation.report()["sources"]["operator"]["verified_completion_rate"] is None
    services.permissions.set_grant("operator.session", "deny")
    services.permissions.set_grant("models.health", "deny")
    report = services.evaluation.report()
    assert report["sources"]["operator"]["status"] == "permission_denied"
    assert report["sources"]["models"]["status"] == "permission_denied"
    services.permissions.set_grant("evaluation.report", "deny")
    with pytest.raises(PermissionError):
        services.evaluation.report()


def test_routing_profiles_preserve_capability_privacy_and_explicit_choice(services):
    candidates = [ModelCandidate("local", "slow", frozenset({"completion", "tools"}), 8000, True, latency_ms=500),
                  ModelCandidate("local", "fast", frozenset({"completion", "tools"}), 8000, True, latency_ms=20),
                  ModelCandidate("openai", "large", frozenset({"completion", "tools", "vision"}), 128000, latency_ms=900)]
    router = ModelRouter(candidates)
    assert router.select(profile="fast").model == "fast"
    assert router.select(profile="quality").model == "large"
    assert router.select(profile="local_only").is_local
    with pytest.raises(ProviderError):
        router.select(profile="local_only", require_vision=True)
    with pytest.raises(PermissionError):
        router.fallback(("local", "fast"), attempted=[("local", "slow")], profile="quality")
    assert ModelRouter(candidates, {"chat": ("local", "slow")}).select(profile="fast").model == "slow"
    services.evaluation.configure_profile("local_only")
    assert services.evaluation.routing_options()["local_only"]
    services.settings.set("ai.local_only", True)
    services.evaluation.configure_profile("custom", {"prefer_local": False, "prefer_cloud": True})
    assert services.evaluation.routing_options()["local_only"]
    with pytest.raises(ValueError):
        services.evaluation.configure_profile("custom", {"prefer_local": True, "prefer_cloud": True})
    assert not services.execute_tool("models.configure_profile", {"profile": "fast"}).ok


def test_notification_group_delivery_snooze_and_mute_are_persistent(services):
    first = services.notifications.create("First", category="work", group_key="project")
    second = services.notifications.create("Second", category="work", group_key="project")
    snoozed = services.notifications.create("Later", category="task")
    muted = services.notifications.create("Muted", category="automation")
    services.notifications.snooze(snoozed, 60)
    services.notifications.mute_category("automation")
    batch = services.notifications.delivery()
    assert len(batch) == 1 and batch[0]["id"] in {first, second}
    assert services.records.get("notification", first)["delivered"]
    assert services.records.get("notification", second)["delivered"]
    assert services.records.get("notification", muted)["delivered"]
    assert not services.records.get("notification", snoozed)["delivered"]
    assert services.notifications.groups()[0]["count"] >= 1
    profile = services.data_dir
    services.close()
    reopened = Services(profile)
    try:
        assert reopened.notifications.list(category="task")[0]["snoozed"]
        assert reopened.notifications.list(category="automation")[0]["muted"]
        assert reopened.notifications.delivery() == []
        reopened.notifications.snooze(snoozed, 0)
        assert reopened.notifications.delivery()[0]["id"] == snoozed
    finally:
        reopened.close()


def test_notification_shortcuts_are_previews_and_revalidate_permissions(services, monkeypatch):
    task = services.add_task("Related")
    id = services.notifications.create("Related", related=[{"kind": "task", "reference": task}],
        actions=[{"label": "Inspect", "tool": "tasks.search", "arguments": {"view": "open"}}])
    execute = Mock(side_effect=AssertionError("Shortcut executed a tool"))
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.notifications.action_preview(id)["actions_executed"] == 0
    execute.assert_not_called()
    with pytest.raises(ValueError):
        services.notifications.create("Unsafe", actions=[{"label": "Create", "tool": "tasks.create", "arguments": {"title": "No"}}])
    services.permissions.set_grant("tasks.search", "deny")
    with pytest.raises(PermissionError):
        services.notifications.action_preview(id)
    assert services.notifications.list()[0]["body"] == ""
    assert services.notifications.list()[0]["actions"] == []


def test_daily_is_off_and_only_selected_saved_metadata_is_read(services, monkeypatch):
    execute = Mock(side_effect=AssertionError("Unexpected account/tool read"))
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.daily.brief(include_external=True)["sections"] == []
    due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    services.add_task("Due today", due)
    services.missions.save("Mission", notes="PRIVATE NOTES")
    services.daily.configure(enabled=True, sources=["tasks", "missions"])
    report = services.daily.brief()
    assert [section["source"] for section in report["sections"]] == ["tasks", "missions"]
    assert report["sections"][0]["items"][0]["title"] == "Due today"
    assert report["sections"][1]["items"][0]["goal"] == "Mission"
    assert "PRIVATE" not in json.dumps(report)
    execute.assert_not_called()
    services.permissions.set_grant("tasks.search", "deny")
    assert services.daily.brief()["sections"][0]["status"] == "permission_denied"


def test_daily_workflow_failures_exclude_previews_and_deleted_sources(services):
    register(services.registry, "test.failure", "A failed read", {}, (), lambda: ToolResult(False, error="PRIVATE ERROR"))
    workflow = services.workflows.save("Failure", [{"kind": "action", "tool": "test.failure", "arguments": {}}])["id"]
    services.workflows.run(workflow)
    services.records.put("workflow_run", {"workflow_id": workflow, "name": "Preview", "status": "failed", "test_mode": True})
    services.records.put("workflow_run", {"workflow_id": "removed", "name": "Missing", "status": "failed"})
    services.daily.configure(enabled=True, sources=["workflow_failures"])
    report = services.daily.brief()
    assert len(report["sections"][0]["items"]) == 1
    assert report["sections"][0]["items"][0]["name"] == "Failure"
    assert "PRIVATE" not in json.dumps(report)


def test_daily_external_requires_optin_connection_current_account_and_foreground(services, monkeypatch):
    accounts = [{"id": "github", "status": "Connected", "account": "user"}]
    monkeypatch.setattr(services.integrations, "status", lambda: accounts)
    services.daily.configure(enabled=True, external={"github": {"owner": "owner", "repo": "repo"}})
    execute = Mock(return_value=ToolResult(False, error="PRIVATE TOKEN ERROR"))
    monkeypatch.setattr(services, "execute_tool", execute)
    assert services.daily.brief()["sections"][0]["status"] == "not_requested"
    execute.assert_not_called()
    with operation(unattended=True):
        assert services.daily.brief(True)["sections"][0]["status"] == "foreground_required"
    execute.assert_not_called()
    report = services.daily.brief(True)
    assert report["sections"][0]["status"] == "read_failed" and report["partial"]
    assert "PRIVATE" not in json.dumps(report)
    assert execute.call_args.args == ("intelligence.briefing", {"source": "github", "limit": 5, "owner": "owner", "repo": "repo"})
    execute.reset_mock()
    accounts[0]["account"] = "different"
    assert services.daily.brief(True)["sections"][0]["status"] == "account_changed"
    execute.assert_not_called()
    accounts[0]["status"] = "Not connected"
    with pytest.raises(PermissionError):
        services.daily.configure(external={"github": {"owner": "owner", "repo": "repo"}})
    assert services.daily.brief(True)["sections"][0]["status"] == "not_connected"
