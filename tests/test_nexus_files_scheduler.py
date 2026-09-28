import shutil
from types import SimpleNamespace

import pytest

from jarvix.capabilities.recycle import RecycleService
from jarvix.capabilities.scheduler import SchedulerService, task_xml
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    s = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    s.add_file_root(str(tmp_path))
    s.settings.set("control.enabled", True)
    yield s
    s.close()


class Tasks:
    def __init__(self):
        self.items = {}

    def install(self, name, xml):
        self.items[name] = xml

    def remove(self, name):
        del self.items[name]


def schedule(s):
    backend = Tasks()
    service = s.scheduler = SchedulerService(s, backend, lambda: b"isolated-test-key")
    workflow = s.workflows.save("Morning", [{"kind": "action", "tool": "tasks.create",
                                            "arguments": {"title": "Once"}}],
                                 enabled=True, approved_tools=["tasks.create"])
    return service, workflow["id"], backend


def test_signed_schedule_enforces_grants_and_exact_workflow(services):
    scheduler, id, backend = schedule(services)
    saved = scheduler.install(id, "07:00", [0, 1, 2, 3, 4])
    assert len(backend.items) == 1
    xml = next(iter(backend.items.values()))
    assert "InteractiveToken" in xml and "LeastPrivilege" in xml and "--scheduled-run" in xml
    assert "-Command" not in xml
    assert scheduler.run(saved["id"])["ok"]
    assert scheduler.run(saved["id"])["skipped"]
    assert len(services.list_tasks()) == 1
    row = services.records.get("closed_schedule", saved["id"])
    row["time"] = "08:00"
    services.records.put("closed_schedule", row, row["id"])
    with pytest.raises(PermissionError, match="signed"):
        scheduler.run(row["id"])
    scheduler.remove(row["id"])
    assert not backend.items and not scheduler.list()


def test_closed_schedule_fresh_approval_and_configuration_change(services):
    scheduler, id, backend = schedule(services)
    args = {"workflow_id": id, "time": "07:00", "weekdays": [0]}
    assert not services.execute_tool("scheduler.install", args, approve=lambda _: False).ok
    assert not backend.items
    saved = scheduler.install(**args)
    services.workflows.toggle(id, False)
    with pytest.raises((ValueError, PermissionError)):
        scheduler.run(saved["id"])
    services.workflows.toggle(id, True)
    services.settings.set("automations.enabled", False)
    with pytest.raises(PermissionError):
        scheduler.run(saved["id"])


def test_schedule_cannot_approve_dangerous_workflow_or_modified_app(services):
    scheduler, id, _ = schedule(services)
    dangerous = services.workflows.save("No", [{"kind": "action", "tool": "memory.remember",
                                                "arguments": {"content": "value"}}], enabled=True)
    with pytest.raises(PermissionError):
        scheduler.install(dangerous["id"], "07:00", [0])
    row = {"time": "07:00", "weekdays": [0], "command": 'C:/App & Test/Jarvix.exe', "arguments": '--data-dir "C:/a & b"'}
    assert "&amp;" in task_xml(row)


class Bin:
    def __init__(self, target, stash):
        self.target, self.stash = target, stash
        self.present = True
        self.calls = 0

    def items(self):
        return [{"identity": "owned-recycle-id", "original": str(self.target)}] if self.present else []

    def restore(self, item):
        self.calls += 1
        assert item["identity"] == "owned-recycle-id"
        shutil.move(self.stash, self.target)
        self.present = False


def recycled(s, tmp_path):
    target = tmp_path / "original.txt"
    target.write_text("unchanged", encoding="utf-8")
    digest = s.files._fingerprint(target)
    stash = tmp_path / "stash.txt"
    target.rename(stash)
    backend = Bin(target, stash)
    recycle = RecycleService(s, backend)
    id = recycle.record(target, digest, set())["recycle_id"]
    return recycle, id, backend


def test_restore_preview_conflict_missing_and_verified_success(services, tmp_path):
    recycle, id, backend = recycled(services, tmp_path)
    assert recycle.preview([id])["can_restore"]
    backend.target.write_text("new file")
    assert not recycle.preview([id])["can_restore"]
    with pytest.raises(ValueError):
        recycle.restore([id])
    assert backend.calls == 0
    backend.target.unlink()
    assert recycle.restore([id])["completed"]
    assert backend.target.read_text() == "unchanged"
    assert not recycle.preview([id])["can_restore"]


def test_restore_rechecks_allowed_root_and_native_identity(services, tmp_path):
    recycle, id, backend = recycled(services, tmp_path)
    backend.present = False
    assert not recycle.list()["items"][0]["available"]
    with pytest.raises(ValueError):
        recycle.restore([id])
    backend.present = True
    services.remove_file_root(str(tmp_path))
    with pytest.raises(ValueError):
        recycle.restore([id])
    assert backend.calls == 0

