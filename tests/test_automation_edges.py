"""Routine configuration, event edges and nested unattended permissions."""
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvix.domain import ToolResult, ToolSpec
from jarvix.runtime import operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile")
    yield service
    service.close()


READ_TASKS = [{"tool": "tasks.list", "arguments": {}}]


@pytest.mark.parametrize("trigger,config", [
    ("interval", {"minutes": True}), ("interval", {"minutes": float("inf")}),
    ("interval", {"minutes": 0}), ("battery_below", {"threshold": float("nan")}),
    ("memory_above", {"threshold": -1}), ("cpu_above", {"threshold": 101}),
    ("cpu_above", {"threshold": True}), ("app_start", {"name": ""}),
    ("app_start", {"name": "folder/app.exe"}), ("at_time", {"at": "2030-01-01T10:00:00"}),
    ("at_time", {"at": None}), ("file_created", {"path": ""}),
    ("manual", {"unrecognized": "ignored before"}),
])
def test_invalid_trigger_configuration_never_persists(services, trigger, config):
    with pytest.raises(ValueError):
        services.automation.save("Invalid", READ_TASKS, trigger, config)
    assert services.automation.list() == []


def test_watched_path_must_be_allowed_directory(services, tmp_path):
    target = tmp_path / "document.txt"
    target.write_text("Local")
    services.add_file_root(str(tmp_path))
    with pytest.raises(ValueError, match="directory"):
        services.automation.save("Folder", READ_TASKS, "folder_change", {"path": str(target)})


def test_battery_trigger_fires_only_on_threshold_crossing(services, monkeypatch):
    sensor = SimpleNamespace(percent=19, power_plugged=False)
    monkeypatch.setattr("jarvix.capabilities.automations.psutil.sensors_battery", lambda: sensor)
    services.automation.save("Battery", READ_TASKS, "battery_below", {"threshold": 20})
    assert services.automation.tick()[0]["ok"]
    assert services.automation.tick() == []
    sensor.power_plugged = True
    assert services.automation.tick() == []
    sensor.power_plugged = False
    assert services.automation.tick()[0]["ok"]


def test_task_due_uses_timezone_and_does_not_refire_for_removed_tasks(services, monkeypatch):
    now = datetime.now(timezone.utc)
    past = (now - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=14)))
    future = (now + timedelta(hours=2)).astimezone(timezone(timedelta(hours=-12)))
    tasks = [{"id": "past", "status": "open", "due_at": past.isoformat()},
             {"id": "future", "status": "open", "due_at": future.isoformat()}]
    monkeypatch.setattr(services, "list_tasks", lambda: tasks)
    services.automation.save("Due", READ_TASKS, "task_due")
    assert len(services.automation.tick()) == 1
    assert services.automation.list()[0]["last_state"] == ["past"]
    assert services.automation.tick() == []
    tasks.clear()
    assert services.automation.tick() == []
    tasks.append({"id": "another", "status": "open", "due_at": past.isoformat()})
    assert len(services.automation.tick()) == 1


@pytest.mark.parametrize("permission,level", [("clipboard.read", 1), ("screen.capture", 2),
    ("microphone.listen", 1), ("local.write", 3)])
def test_nested_unattended_actions_cannot_inherit_interactive_approval(services, permission, level):
    services.settings.set("control.enabled", True)
    for setting in ("clipboard.enabled", "screenshots.enabled", "microphone.enabled"):
        services.settings.set(setting, True)
    reached = Mock(return_value=ToolResult(True))
    services.registry.register(ToolSpec("test.target", "Target", {"type": "object"}, permission,
        permission_level=level), reached)
    services.registry.register(ToolSpec("test.wrapper", "Nested action", {"type": "object"},
        permission_level=1), lambda _: services.execute_tool("test.target", {}, approve=lambda _: True))
    routine = services.automation.save("Nested", [{"tool": "test.wrapper", "arguments": {}}])["id"]
    with operation(approve=lambda _: True):
        assert not services.automation.run(routine, unattended=True)["ok"]
        assert services.execute_tool("test.target", {}).ok
    reached.assert_called_once()


def test_cancellation_stops_remaining_actions_and_records_local_outcome(services):
    cancel = threading.Event()
    services.registry.register(ToolSpec("test.cancel", "Cancel", {"type": "object"}, permission_level=1),
        lambda _: cancel.set() or ToolResult(True))
    routine = services.automation.save("Cancel", [{"tool": "test.cancel", "arguments": {}}, *READ_TASKS])["id"]
    with operation(cancel=cancel):
        result = services.automation.run(routine)
    assert not result["ok"]
    assert "stopped" in result["actions"][1]["error"]
    assert services.automation.history(routine)[0]["ok"] is False
    assert not services.automation._running


def test_scheduled_routines_reject_external_actions_before_persisting(services):
    with pytest.raises(ValueError, match="safe local summaries"):
        services.automation.save("Open site", [{"tool": "web.open", "arguments": {"url": "https://example.com"}}],
                                 "interval", {"minutes": 5})
    assert services.automation.list() == []


def test_unattended_host_boundary_blocks_legacy_external_routines(services, monkeypatch):
    opened = []
    monkeypatch.setattr(services.browser, "open_url", lambda url: opened.append(url) or {"opened": True})
    routine = services.automation.save(
        "Legacy external", [{"tool": "web.open", "arguments": {"url": "https://example.com"}}]
    )["id"]

    result = services.automation.run(routine, unattended=True)

    assert not result["ok"]
    assert "safe local summaries" in result["actions"][0]["error"]
    assert opened == []


def test_scheduled_local_notification_requires_control_access(services):
    routine = services.automation.save(
        "Local alert",
        [{"tool": "notifications.create", "arguments": {"title": "Heads up", "category": "system"}}],
        "interval",
        {"minutes": 5},
    )["id"]

    result = services.automation.run(routine, unattended=True)
    assert not result["ok"]
    services.settings.set("control.enabled", True)
    result = services.automation.run(routine, unattended=True)
    assert result["ok"]
    assert any(row["title"] == "Heads up" for row in services.notifications.list())


def test_unattended_runner_revalidates_persisted_recursive_actions(services):
    routine = services.automation.save("Original", READ_TASKS)["id"]
    row = services.records.get("routine", routine)
    row["actions"] = [{"tool": "automations.run", "arguments": {"id": routine}}]
    services.records.put("routine", row, routine)
    with pytest.raises(ValueError, match="Recursive"):
        services.automation.run(routine, unattended=True)
    assert not services.automation._running


def test_notification_delivery_prioritizes_high_without_dropping_low(services):
    for index in range(6):
        services.notifications.create(f"Low {index}", priority="low")
    urgent = services.notifications.create("Urgent", priority="high")
    first = services.notifications.delivery()
    second = services.notifications.delivery()
    assert first[0]["id"] == urgent
    assert len(first + second) == 7
    assert len({row["id"] for row in first + second}) == 7
