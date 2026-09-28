"""Background ownership, reminder delivery, and shutdown cancellation."""
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from jarvix.background import BackgroundRuntime
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile")
    yield service
    service.close()


def test_runtime_is_opt_in_and_repeated_start_does_not_duplicate_worker(services, monkeypatch):
    assert not services.background.running
    entered = threading.Event()
    runtime = BackgroundRuntime(services, interval=.1)
    monkeypatch.setattr(runtime, "tick_once", lambda: entered.set())
    assert runtime.start()
    assert entered.wait(2)
    assert not runtime.start()
    assert runtime.stop() and not runtime.running
    assert runtime.start()
    assert runtime.stop()


def test_background_runs_workflows_and_reminds_only_once(services):
    events = []
    runtime = BackgroundRuntime(services, lambda kind, data: events.append((kind, data)))
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    services.add_task("Finish homework", due)
    services.workflows.save("Start", [{"kind": "action", "tool": "tasks.list", "arguments": {}}],
                            "jarvix_start", enabled=True)
    first = runtime.tick_once()
    assert first["workflows"][0]["ok"]
    assert len(first["reminders"]) == 1
    assert runtime.tick_once()["reminders"] == []
    assert len(services.notifications.list(category="task")) == 1
    assert any(kind == "reminder" for kind, _ in events)
    notifications = [data for kind, data in events if kind == "notification"]
    assert len(notifications) == 1 and notifications[0]["title"] == "Task reminder"


def test_stop_cancels_workflow_delay_and_joins_owned_worker(services):
    runtime = BackgroundRuntime(services, interval=.1)
    entered = threading.Event()
    runtime.set_callback(lambda kind, data: entered.set() if kind == "workflow" and data["steps"] else None)
    services.workflows.save("Wait", [{"kind": "delay", "seconds": 30}], "jarvix_start", enabled=True)
    runtime.start()
    assert entered.wait(3)
    assert runtime.stop(timeout=3)
    assert services.workflows.history()[0]["status"] == "cancelled"


def test_cancelling_one_workflow_does_not_stop_the_background_runtime(services):
    cancelled, delivered = threading.Event(), threading.Event()

    def event(kind, data):
        if kind == "workflow" and data["status"] == "running" and data["steps"]:
            services.workflows.cancel(data["run_id"])
        if kind == "workflow" and data["status"] == "cancelled":
            cancelled.set()
        if kind == "notification":
            delivered.set()

    runtime = BackgroundRuntime(services, event, interval=.1)
    services.workflows.save("Cancel just this run", [{"kind": "delay", "seconds": 30}],
                            "jarvix_start", enabled=True)
    services.notifications.create("Background delivery must continue")
    try:
        assert runtime.start()
        assert cancelled.wait(3) and delivered.wait(3)
        assert runtime.running
    finally:
        assert runtime.stop(timeout=3)


def test_scheduler_failure_is_sanitized_and_subscriber_failure_isolated(services, monkeypatch):
    entered = threading.Event()
    runtime = BackgroundRuntime(services, interval=.1)
    fail = Mock(side_effect=RuntimeError("super-secret-private-value"))
    monkeypatch.setattr(services, "run_due_automations", fail)
    events = []

    def callback(kind, data):
        events.append((kind, data))
        if data.get("status") == "error":
            entered.set()
            raise RuntimeError("UI closed")

    runtime.set_callback(callback)
    runtime.start()
    assert entered.wait(3)
    assert runtime.stop()
    assert "super-secret" not in str(events)


def test_stop_makes_manual_tick_inert(services, monkeypatch):
    runtime = BackgroundRuntime(services)
    timer = Mock()
    monkeypatch.setattr(services, "run_due_automations", timer)
    runtime.stop()
    assert runtime.tick_once() == {"workflows": [], "reminders": []}
    timer.assert_not_called()


def test_shutdown_does_not_report_stopped_or_start_duplicate_while_tool_is_running(services, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    events = []
    runtime = BackgroundRuntime(services, lambda kind, data: events.append((kind, data)))

    def blocked_tool():
        entered.set()
        release.wait(3)

    monkeypatch.setattr(services, "run_due_automations", blocked_tool)
    try:
        assert runtime.start() and entered.wait(2)
        assert not runtime.stop(timeout=.01)
        assert runtime.running and not runtime.start()
        assert any(data["status"] == "stopping" for kind, data in events if kind == "background")
        assert not any(data["status"] == "stopped" for kind, data in events if kind == "background")
    finally:
        release.set()
        assert runtime.stop(timeout=3)
    assert not runtime.running
    assert events[-1] == ("background", {"status": "stopped"})


def test_native_delivery_is_bounded_and_respects_quiet_and_do_not_disturb(services):
    events = []
    runtime = BackgroundRuntime(services, lambda kind, data: events.append((kind, data)))
    for index in range(7):
        services.notifications.create(f"Notice {index}")
    runtime.tick_once()
    assert len([event for event in events if event[0] == "notification"]) == 5
    runtime.tick_once()
    assert len([event for event in events if event[0] == "notification"]) == 7
    for preference in ("notifications.quiet", "notifications.dnd"):
        services.settings.set(preference, True)
        services.notifications.create("Muted")
        runtime.tick_once()
        services.settings.set(preference, False)
    runtime.tick_once()
    assert len([event for event in events if event[0] == "notification"]) == 7

