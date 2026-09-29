import copy
import threading
from types import SimpleNamespace

import pytest

from jarvix.capabilities.desktop import DesktopOperatorService, setup
from jarvix.records import RecordStore
from jarvix.runtime import operation
from jarvix.storage import Database, SettingsRepository
from jarvix.tools.registry import ToolRegistry


class FakeUIA:
    def __init__(self):
        self.calls = []
        self.changed = False
        self.rows = [
            {"runtime_id": "1.2.3", "name": "Save", "automation_id": "save", "control_type": "Button",
             "enabled": True, "password": False, "offscreen": False, "focused": False},
            {"runtime_id": "1.2.4", "name": "Document", "automation_id": "editor", "control_type": "Edit",
             "enabled": True, "password": False, "offscreen": False, "focused": True},
            {"runtime_id": "1.2.5", "name": "Password: private", "automation_id": "password", "control_type": "Edit",
             "enabled": True, "password": True, "offscreen": False, "focused": False, "value": "secret value"},
        ]

    def call(self, operation, **arguments):
        self.calls.append((operation, arguments))
        if operation == "inspect":
            return {"root_id": "1.2", "process_started": "123456789", "application": "Example",
                    "elements": copy.deepcopy(self.rows[:arguments.get("limit", 120)]), "bounded": False}
        if self.changed:
            raise ValueError("Control changed")
        return {"requested": True, "verified": operation in {"type", "focus", "move", "scroll"}}


@pytest.fixture
def operator(tmp_path):
    database = Database(tmp_path / "test.db")
    rows = [{"handle": 2**40, "process_id": 42, "title": "Editor"}]
    services = SimpleNamespace(settings=SettingsRepository(database), records=RecordStore(database),
                               data_dir=tmp_path, windows=SimpleNamespace(list=lambda query="": [r for r in rows if query in r["title"]],
                               foreground=lambda: rows[0], action=lambda *args: {"requested": True}))
    services.settings.set("screenshots.enabled", True)
    backend = FakeUIA()
    value = DesktopOperatorService(services, backend)
    value.set_indicator(lambda state: True)
    services.desktop = value
    return value


def reference(operator, name="Save"):
    return operator.find_element(2**40, 42, name, exact=True)["matches"][0]["element_ref"]


def test_tree_redacts_protected_values_and_never_issues_a_target_reference(operator):
    tree = operator.inspect_ui(2**40, 42)
    protected = tree["elements"][2]
    assert protected["name"] == "[protected]"
    assert protected["automation_id"] == ""
    assert "value" not in protected and "element_ref" not in protected
    assert "private" not in str(tree) and "secret value" not in str(tree)


def test_read_gate_and_wrong_pid_fail_before_native_call(operator):
    with pytest.raises(ValueError):
        operator.inspect_ui(2**40, 99)
    operator.services.settings.set("screenshots.enabled", False)
    with pytest.raises(PermissionError):
        operator.inspect_ui(2**40, 42)
    assert operator.backend.calls == []


def test_target_reference_carries_all_identity_guards_and_does_not_claim_success(operator):
    ref = reference(operator)
    result = operator.click_element(ref)
    assert result["requested"] and not result["verified"]
    name, arguments = operator.backend.calls[-1]
    assert name == "click"
    assert arguments["handle"] == 2**40 and arguments["process_id"] == 42
    assert arguments["process_started"] == "123456789" and arguments["root_id"] == "1.2"
    assert arguments["runtime_id"] == "1.2.3" and arguments["expected_name"] == "Save"
    assert not arguments["pointer_fallback"]


def test_expired_and_invented_refs_never_execute(operator):
    ref = reference(operator)
    operator._refs[ref]["expires"] = 0
    for bad in (ref, "invented"):
        with pytest.raises(ValueError):
            operator.click_element(bad)
    assert all(name == "inspect" for name, _ in operator.backend.calls)


def test_mutations_require_acknowledged_visible_hud_and_idle_is_guaranteed(operator):
    ref = reference(operator)
    states = []
    operator.set_indicator(lambda state: states.append(state) or False)
    with pytest.raises(PermissionError):
        operator.click_element(ref)
    assert [s["state"] for s in states] == ["running", "idle"]
    assert not operator.status()["active"]
    assert all(name == "inspect" for name, _ in operator.backend.calls)
    operator.set_indicator(lambda state: states.append(state) or True)
    operator.backend.changed = True
    with pytest.raises(ValueError):
        operator.click_element(ref)
    assert states[-1]["state"] == "idle"


def test_cancellation_after_hud_ack_stops_before_mutation(operator):
    ref = reference(operator)

    def indicator(state):
        if state["state"] == "running":
            operator.cancel()
        return True

    operator.set_indicator(indicator)
    with pytest.raises(InterruptedError):
        operator.click_element(ref)
    assert all(name == "inspect" for name, _ in operator.backend.calls)


def test_revoke_screen_access_after_preview_stops_mutation(operator):
    ref = reference(operator)

    def indicator(state):
        operator.services.settings.set("screenshots.enabled", False)
        return True

    operator.set_indicator(indicator)
    with pytest.raises(PermissionError):
        operator.type_text(ref, "hello")
    assert all(name == "inspect" for name, _ in operator.backend.calls)


def test_credentials_and_security_confirmation_are_never_automated(operator):
    ref = reference(operator, "Document")
    with pytest.raises(PermissionError):
        operator.type_text(ref, "password=secret-value")
    operator.backend.rows[0]["name"] = "Confirm"
    confirm = reference(operator, "Confirm")
    with pytest.raises(PermissionError):
        operator.click_element(confirm)
    for shortcut in ("ENTER", "CTRL+ENTER", "ALT+F4", "WIN+R"):
        with pytest.raises(PermissionError):
            operator.send_shortcut(2**40, 42, shortcut)
    assert all(name == "inspect" for name, _ in operator.backend.calls)


def test_verified_type_does_not_return_entered_text(operator):
    result = operator.type_text(reference(operator, "Document"), "My draft document")
    assert result["verified"]
    assert "My draft document" not in str(result)


def test_ambiguous_search_requires_choosing_ref_and_incomplete_absence_is_not_verified(operator):
    operator.backend.rows.append({**operator.backend.rows[0], "runtime_id": "1.2.6"})
    assert operator.find_element(2**40, 42, "Save")["ambiguous"]
    original = operator.backend.call

    def bounded(*args, **kwargs):
        return {**original(*args, **kwargs), "bounded": True}

    operator.backend.call = bounded
    assert not operator.verify_state(2**40, 42, "Download complete", expected="absent")["verified"]


def test_wait_reports_timeout_and_supports_local_cancellation(operator):
    assert operator.wait_for_window("Missing", timeout=0)["timed_out"]
    operator.services.windows.list = lambda query="": operator.cancel() and []
    with pytest.raises(InterruptedError):
        operator.wait_for_window("Missing", timeout=2)
    assert not operator.status()["active"]


def test_runtime_cancel_is_checked_before_hud_and_native_writes(operator):
    ref = reference(operator)
    event = threading.Event()
    states = []
    operator.set_indicator(lambda state: states.append(state) or True)
    with operation(cancel=event):
        event.set()
        with pytest.raises(InterruptedError):
            operator.click_element(ref)
    assert states == []


def test_desktop_schemas_constrain_targets_and_require_fresh_confirmation(operator):
    registry = ToolRegistry()
    setup(operator.services, registry)
    assert registry.get("desktop.click_element").permission_level == 3
    assert registry.get("desktop.type_text").permission_level == 3
    assert registry.get("desktop.focus_window").permission_level == 2
    assert registry.get("desktop.inspect_ui").permission == "screen.capture"
    assert registry.validate("desktop.click_element", {"x": 100, "y": 100}) is not None
    assert registry.validate("desktop.send_shortcut", {"handle": 1, "process_id": 2, "shortcut": "ENTER"}) is not None
    assert registry.validate("desktop.wait_for_window", {"query": "Editor", "timeout": 600}) is not None
    assert registry.get("vision.read_text").permission == "screen.capture"


def test_wait_deadline_interrupts_inspection_before_long_native_timeout(operator, monkeypatch):
    current_time = [100.0]
    monkeypatch.setattr("jarvix.capabilities.desktop.time.monotonic", lambda: current_time[0])

    def slow_worker(*args, **kwargs):
        current_time[0] += 2
        operator._checkpoint()
        pytest.fail("The per-wait deadline must stop the still-running native worker")

    operator.backend.call = slow_worker
    result = operator.wait_for_element(2**40, 42, "Save", timeout=1)
    assert result["timed_out"] and not result["matched"]
    assert operator._wait_deadline is None and not operator.status()["active"]


def test_paused_wait_still_obeys_its_deadline(operator, monkeypatch):
    ticks = iter([100.0, 100.0, 100.5, 102.0])
    monkeypatch.setattr("jarvix.capabilities.desktop.time.monotonic", lambda: next(ticks))

    def inspection(query=""):
        operator.pause()
        operator._checkpoint()
        pytest.fail("A paused wait must still reach its timeout")

    operator.services.windows.list = inspection
    result = operator.wait_for_window("Save", timeout=1)
    assert result["timed_out"] and not result["matched"]
    assert not operator.status()["active"] and not operator.status()["paused"]
    assert operator._wait_deadline is None
