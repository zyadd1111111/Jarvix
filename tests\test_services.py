import threading
from datetime import datetime, timedelta, timezone

import pytest

from jarvix.services import Services


class NoVault:
    def get(self, _):
        return None


@pytest.fixture
def services(tmp_path):
    instance = Services(tmp_path / "profile", vault=NoVault())
    yield instance
    instance.close()


def test_local_data_survives_restart(services):
    note_id = services.save_note("Architecture", "Local first")
    services.save_note("Architecture", "SQLite", note_id)
    services.add_memory("Jarvix is my main project")
    task_id = services.add_task("Build Jarvix")
    services.complete_task(task_id)
    reopened = Services(services.data_dir, vault=NoVault())
    try:
        assert reopened.list_notes()[0]["body"] == "SQLite"
        assert reopened.list_memories()[0]["content"] == "Jarvix is my main project"
        assert reopened.list_tasks()[0]["status"] == "done"
    finally:
        reopened.close()


def test_reminders_once_and_completed_excluded(services):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    services.add_task("Due", past)
    completed = services.add_task("Already done", past)
    services.complete_task(completed)
    assert [row["title"] for row in services.due_reminders()] == ["Due"]
    assert services.due_reminders() == []


def test_file_index_opt_in_and_metadata_only(services, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    (root / "project.txt").write_text("private contents", encoding="utf-8")
    assert services.list_files() == []
    services.add_file_root(str(root))
    assert services.scan_files()["count"] == 1
    assert services.list_files("project")[0]["name"] == "project.txt"
    assert "private contents" not in str(services.db.query("SELECT * FROM files"))
    assert services.list_files("%") == []
    services.remove_file_root(str(root))
    assert services.list_files() == []


def test_no_key_chat_is_honest_and_persistent(services):
    cid = services.new_conversation()
    result = services.chat("Hello", cid, "openai", "gpt-4.1-mini", lambda _: False, lambda *_: None, threading.Event())
    assert "API key" in result
    assert len(services.conversation_messages(cid)) == 2
    assert services.list_conversations()[0]["title"] == "Hello"


def test_shared_text_validation_returns_safe_errors_for_non_text_input(services):
    with pytest.raises(ValueError, match="Title must contain text"):
        services.save_note(None, "Body")
    with pytest.raises(ValueError, match="Task must contain text"):
        services.add_task(42)


def test_context_does_not_include_unrelated_local_records(services):
    services.save_note("Secret", "never implicitly upload this")
    services.add_memory("other unrelated private record")
    cid = services.new_conversation()
    preview = str(services.context_preview(cid, "openai", "test"))
    assert "never implicitly upload this" not in preview
    assert "other unrelated private record" not in preview


def test_automations_reject_side_effects_and_run_once_per_interval(services):
    with pytest.raises(ValueError):
        services.add_automation("Bad", "apps.open", {}, 10)
    services.add_automation("Task summary", "tasks.list", {}, 10)
    assert len(services.run_due_automations()) == 1
    assert services.run_due_automations() == []
    row = services.list_automations()[0]
    services.toggle_automation(row["id"], False)
    assert services.list_automations()[0]["enabled"] is False


def test_app_registration_rejects_documents(services, tmp_path):
    path = tmp_path / "unsafe.cmd"
    path.write_text("echo unsafe")
    with pytest.raises(ValueError):
        services.add_app("Unsafe", str(path))


def test_system_metrics_are_real_bounded_values(services):
    snapshot = services.system_snapshot()
    assert 0 <= snapshot["memory_percent"] <= 100
    assert snapshot["memory_total_gb"] > 0
    assert len(snapshot["processes"]) <= 20


def test_revoking_root_during_scan_cannot_reinsert_metadata(services, tmp_path, monkeypatch):
    import os
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("hello")
    services.add_file_root(str(root))
    original_walk = os.walk
    def revoking_walk(*args, **kwargs):
        for record in original_walk(*args, **kwargs):
            yield record
            services.remove_file_root(str(root.resolve()))
    monkeypatch.setattr(os, "walk", revoking_walk)
    services.scan_files()
    assert services.db.query("SELECT * FROM files") == []


def test_replaced_root_cannot_expand_access(services, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    services.add_file_root(str(root))
    root.rmdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is not permitted on this host")
    services.scan_files()
    assert services.list_files() == []
    assert services.db.query("SELECT * FROM files") == []


def test_windows_junctions_are_pruned_before_traversal(services, tmp_path, monkeypatch):
    import os
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows junction behavior")
    import _winapi
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "allowed.txt").write_text("allowed")
    (outside / "private.txt").write_text("private")
    escape, loop = root / "escape", root / "cycle"
    _winapi.CreateJunction(str(outside), str(escape))
    _winapi.CreateJunction(str(root), str(loop))
    real_scandir = os.scandir
    visited = []
    def observing_scandir(path):
        visited.append(str(path))
        assert Path(path).resolve().is_relative_to(root)
        assert len(visited) < 4, "Cycle was traversed"
        return real_scandir(path)
    from pathlib import Path
    try:
        services.add_file_root(str(root))
        monkeypatch.setattr(os, "scandir", observing_scandir)
        assert services.scan_files()["count"] == 1
        assert len(visited) == 1
    finally:
        monkeypatch.setattr(os, "scandir", real_scandir)
        escape.rmdir()
        loop.rmdir()
