import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jarvix.services import Services
from jarvix.storage import Database, SettingsRepository


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    yield service
    service.close()


def test_health_uses_real_local_checks_without_provider_or_network_probes(services, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("Local health must never probe the network"))
    monkeypatch.setattr(services, "discover_models", lambda *args: pytest.fail("No implicit paid or local model probe"))
    services.records.put("provider_health", {"available": True, "models": []}, "ollama")
    services.records.put("model_outcome", {"failures": 2}, "model")
    services.records.put("closed_schedule", {"enabled": True, "last_status": "Failed"}, "schedule")
    value = services.diagnostics.health()
    assert value["network_requests"] == 0 and not value["actions_performed"]
    assert value["checks"]["database"]["status"] == "Healthy"
    assert value["checks"]["providers"]["available"] == 1
    assert value["checks"]["providers"]["models_with_failures"] == 1
    assert value["checks"]["scheduler"]["needs_attention"] == 1
    assert not value["checks"]["browser"]["available"]
    services.permissions.set_grant("models.health", "deny")
    services.settings.set("tools.enabled", [name for name in services.enabled_tools() if name != "scheduler.list"])
    value = services.diagnostics.health()
    assert not value["checks"]["providers"]["available"]
    assert not value["checks"]["scheduler"]["available"]


def test_health_revalidates_knowledge_and_opt_ins_without_exposing_source_content(services, monkeypatch):
    note = services.save_note("Private title", "Never read this source body")
    space = services.knowledge_spaces.create("Local space")["id"]
    services.knowledge_spaces.add_source(space, "note", note)
    services.settings.set("context.enabled", True)
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("No live integration or Drive probes"))
    first = services.diagnostics.health()
    assert first["checks"]["knowledge"]["sources_checked"] == 1
    assert first["checks"]["knowledge"]["unavailable_sources"] == 0
    assert first["checks"]["permissions"]["app_opt_ins"]["context"]
    assert first["checks"]["permissions"]["status"] == "Unchecked"
    assert not first["checks"]["integrations"]["credential_contents_read"]
    assert "Private title" not in json.dumps(first) and "Never read this source body" not in json.dumps(first)
    services.permissions.set_grant("notes.search", "deny")
    assert services.diagnostics.health()["checks"]["knowledge"]["unavailable_sources"] == 1
    services.permissions.set_grant("knowledge_spaces.get", "deny")
    assert not services.diagnostics.health()["checks"]["knowledge"]["available"]
    services.permissions.set_grant("integrations.status", "deny")
    assert not services.diagnostics.health()["checks"]["integrations"]["available"]


def test_backup_uses_consistent_snapshot_manifest_and_bounded_storage(services):
    note = services.save_note("Saved before backup", "A local fact")
    assert not services.execute_tool("backup.create", {}).ok
    services.settings.set("control.enabled", True)
    result = services.execute_tool("backup.create", {})
    assert result.ok
    value = result.data
    manifest = json.loads(Path(value["manifest_path"]).read_text())
    assert manifest["version"] == 1 and not manifest["credentials_included"]
    assert hashlib.sha256(Path(value["path"]).read_bytes()).hexdigest() == value["sha256"]
    services.db.execute("UPDATE notes SET body=? WHERE id=?", ("Changed after snapshot", note))
    with closing(sqlite3.connect(Path(value["path"]).as_uri() + "?mode=ro&immutable=1", uri=True)) as snapshot:
        assert snapshot.execute("SELECT body FROM notes WHERE id=?", (note,)).fetchone()[0] == "A local fact"
    assert services.maintenance.preview(value["id"])["sha256"] == value["sha256"]
    directory = Path(value["path"]).parent
    for index in range(99):
        (directory / f"jarvix-backup-test-{index}.db").write_bytes(b"test")
    with pytest.raises(ValueError, match="Backup limit reached"):
        services.maintenance.create()


def test_corrupt_or_incompatible_backups_cannot_be_staged(services):
    value = services.maintenance.create()
    target = Path(value["path"])
    with target.open("ab") as output:
        output.write(b"corrupt")
    with pytest.raises(ValueError, match="hash or size"):
        services.maintenance.preview(value["id"])
    with pytest.raises(ValueError):
        services.maintenance.preview("../jarvix")
    value = services.maintenance.create()
    target = Path(value["path"])
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("CREATE TRIGGER unexpected AFTER DELETE ON grants BEGIN DELETE FROM notes; END")
    manifest = json.loads(Path(value["manifest_path"]).read_text())
    manifest["database"].update(size_bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    Path(value["manifest_path"]).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="incompatible"):
        services.maintenance.preview(value["id"])
    Path(value["manifest_path"]).write_text("[]")
    with pytest.raises(ValueError, match="Invalid backup manifest"):
        services.maintenance.preview(value["id"])


def test_restore_requires_fresh_confirmation_stages_real_profile_and_disables_replay(services):
    note = services.save_note("Original", "Saved fact")
    services.settings.set("control.enabled", True)
    services.settings.set("automations.enabled", True)
    services.settings.set("proactive.enabled", True)
    services.settings.set("context.enabled", True)
    services.permissions.set_grant("notes.create", "allow")
    services.permissions.set_grant("files.delete", "deny")
    workflow = services.workflows.save("Regular status", [{"kind": "action", "tool": "system.status", "arguments": {}}],
                                        trigger="interval", config={"minutes": 60}, enabled=True)["id"]
    services.records.put("closed_schedule", {"enabled": True, "last_status": "Succeeded"}, "schedule")
    services.records.put("operator_session", {"goal": "Old work", "status": "running",
        "steps": [{"status": "running"}], "retry_available": True, "checkpoint_available": True}, "session")
    services.records.put("operator_checkpoint", {"payload": "old protected data"}, "session")
    services.records.put("device_peer", {"name": "Old peer"}, "peer")
    services.records.put("plugin_state", {"enabled": True}, "old_plugin")
    value = services.maintenance.create()
    services.db.execute("UPDATE notes SET body=? WHERE id=?", ("New live value", note))
    arguments = {"id": value["id"], "sha256": value["sha256"]}
    services.permissions.set_grant("backup.stage_restore", "allow")
    assert not services.execute_tool("backup.stage_restore", arguments).ok
    assert not services.execute_tool("backup.stage_restore", {**arguments, "sha256": "0" * 64}, approve=lambda _: True).ok
    approved = []
    result = services.execute_tool("backup.stage_restore", arguments, approve=lambda request: approved.append(request) or True)
    assert result.ok and approved[-1].arguments == arguments
    restored = Services(Path(result.data["staged_profile"]), vault=SimpleNamespace(get=lambda _: None))
    try:
        assert result.data["restart_required"] and not result.data["current_profile_restored"]
        assert result.data["launch_arguments"][-2:] == ["--data-dir", str(restored.data_dir)]
        assert restored.db.query("SELECT body FROM notes WHERE id=?", (note,))[0]["body"] == "Saved fact"
        assert services.db.query("SELECT body FROM notes WHERE id=?", (note,))[0]["body"] == "New live value"
        assert restored.permissions.db.query("SELECT decision FROM grants WHERE tool_name='files.delete'")[0]["decision"] == "deny"
        assert not restored.db.query("SELECT 1 FROM grants WHERE decision='allow'")
        assert not restored.settings.get("control.enabled") and not restored.settings.get("automations.enabled")
        assert not restored.settings.get("proactive.enabled") and not restored.settings.get("context.enabled")
        assert not restored.records.get("workflow", workflow)["enabled"]
        assert not restored.records.get("workflow", workflow)["approved_tools"]
        assert not restored.records.get("closed_schedule", "schedule")["enabled"]
        assert not restored.records.get("plugin_state", "old_plugin")["enabled"]
        assert not restored.records.get("operator_session", "session")["retry_available"]
        assert restored.records.get("operator_session", "session")["status"] == "interrupted"
        assert not restored.records.list("operator_checkpoint") and not restored.records.list("device_peer")
    finally:
        restored.close()


def test_restore_sanitization_covers_records_beyond_ui_page_limit(services):
    with services.db.connect() as conn:
        conn.executemany("INSERT INTO records VALUES (?,?,?,?,?)", [(f"{index:04}", "plugin_state", '{"enabled":true}',
                         "2026-01-01", "2026-01-01") for index in range(2001)])
    assert len(services.records.list("plugin_state")) == 2000
    rows = list(services.maintenance._restore_rows(services.db, services.records, "plugin_state"))
    assert len(rows) == 2001


def test_backup_schedule_reuses_exact_existing_workflow_permissions(services):
    services.settings.set("control.enabled", True)
    arguments = {"name": "Nightly backup", "trigger_config": {"trigger": "schedule", "time": "22:00", "weekdays": [0, 1]}}
    assert not services.execute_tool("backup.schedule", arguments).ok
    assert not services.records.list("workflow")
    approved = []
    result = services.execute_tool("backup.schedule", arguments, approve=lambda request: approved.append(request) or True)
    assert result.ok and [request.tool_name for request in approved] == ["workflows.save"]
    row = services.records.get("workflow", result.data["id"])
    assert not row["enabled"] and row["approved_tools"] == ["backup.create"]
    assert row["steps"] == [{"kind": "action", "tool": "backup.create", "arguments": {}, "retries": 0, "on_error": "stop"}]
    assert services.execute_tool("workflows.toggle", {"id": row["id"], "enabled": True}, approve=lambda _: True).ok
    run = services.workflows.run(row["id"], unattended=True)
    assert run["ok"] and run["actions_executed"] == 1
    assert len(services.maintenance.list()["items"]) == 1
    services.permissions.set_grant("backup.create", "deny")
    denied = services.workflows.run(row["id"], unattended=True)
    assert not denied["ok"] and len(services.maintenance.list()["items"]) == 1
    services.permissions.set_grant("backup.create", None)
    services.permissions.set_grant("workflows.save", "deny")
    assert not services.execute_tool("backup.schedule", arguments, approve=lambda _: True).ok


def test_update_status_is_offline_and_artifact_verification_rechecks_roots_and_grants(services, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("Status and hash verification must stay offline"))
    assert services.maintenance.update_status()["download_status"] == "not_downloaded"
    target = services.test_root / "Jarvix-installer.exe"
    target.write_bytes(b"a verified local download")
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        services.maintenance.verify_artifact(str(target))
    with pytest.raises(ValueError, match="verification failed"):
        services.maintenance.verify_artifact(str(target), "0" * 64)
    value = services.maintenance.verify_artifact(str(target), sha)
    assert value["verified"] and not value["executed"] and not value["installed"]
    assert "does not establish publisher authenticity" in value["authenticity"]
    assert services.maintenance.update_status()["download_status"] == "verified_local_artifact"
    services.permissions.set_grant("files.inspect", "deny")
    assert services.maintenance.update_status()["artifact"] is None
    with pytest.raises(PermissionError):
        services.maintenance.verify_artifact(str(target), sha)
    services.permissions.set_grant("files.inspect", None)
    services.remove_file_root(str(services.test_root))
    assert services.maintenance.update_status()["artifact"] is None
    services.add_file_root(str(services.test_root))
    target.write_bytes(b"modified after verification")
    assert services.maintenance.update_status()["artifact"] is None


def test_update_check_reads_only_configured_public_github_metadata(services, monkeypatch):
    calls = []
    artifact = services.test_root / "Jarvix.exe"
    artifact.write_bytes(b"release artifact")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    release = {"draft": False, "prerelease": False, "tag_name": "v0.8.1", "published_at": "2026-10-02T00:00:00Z",
        "body": "Release improvements\n" + "x" * 9000,
        "html_url": "https://github.com/example/jarvix/releases/tag/v0.8.1",
        "assets": [{"name": artifact.name, "size": artifact.stat().st_size, "digest": "sha256:" + sha,
                    "browser_download_url": "https://github.com/example/jarvix/releases/download/v0.8.1/Jarvix.exe"},
                   {"name": "bad.exe", "digest": "sha256:" + sha, "browser_download_url": "https://evil.example/installer"}]}
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=release)
    client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    with pytest.raises(ValueError):
        services.maintenance.check_update()
    for value in ("http://localhost/repo", "../repo", "owner/repo/extra", "owner/.."):
        with pytest.raises(ValueError):
            services.maintenance.configure_updates(value)
    services.maintenance.configure_updates("example/jarvix")
    value = services.maintenance.check_update()
    assert len(calls) == 1 and str(calls[0].url) == "https://api.github.com/repos/example/jarvix/releases/latest"
    assert not calls[0].content and "authorization" not in calls[0].headers
    assert not value["installed"] and len(value["assets"]) == 1
    assert value["changelog"].startswith("Release improvements\n") and value["changelog_truncated"]
    assert len(value["changelog"].encode()) == 8000 and value["content_trust"] == "untrusted_release_metadata"
    checked = services.maintenance.verify_artifact(str(artifact), asset_name=artifact.name)
    assert checked["checksum_source"] == "configured_github_release_digest"
    assert services.maintenance.update_status()["cached_release"]["cached"]
    services.permissions.set_grant("updates.check", "deny")
    assert services.maintenance.update_status()["cached_release"] is None
    assert services.maintenance.update_status()["artifact"] is None
    assert len(calls) == 1


def test_revocation_during_restore_confirmation_stops_staging(services):
    value = services.maintenance.create()
    def approval(_):
        services.permissions.set_grant("backup.preview", "deny")
        return True
    result = services.execute_tool("backup.stage_restore", {"id": value["id"], "sha256": value["sha256"]}, approve=approval)
    assert not result.ok and not (services.data_dir / "restores").exists()


def test_dpapi_protected_records_remain_protected_in_backup_and_restored_profile(services):
    services.data_protection.configure(True)
    saved = services.context.save_profile("Private saved profile")["id"]
    value = services.maintenance.create()
    assert b"Private saved profile" not in Path(value["path"]).read_bytes()
    staged = services.maintenance.stage_restore(value["id"], value["sha256"])
    restored = Database(Path(staged["database_path"]), codec=services.db.codec)
    assert SettingsRepository(restored).get("storage.encryption_enabled")
    assert json.loads(restored.query("SELECT data FROM records WHERE kind='context.profile' AND id=?", (saved,))[0]["data"])["name"] == "Private saved profile"
