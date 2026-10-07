"""Verified local profile snapshots and explicit GitHub release inspection; no installer."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import closing
from pathlib import Path
from itertools import islice

import httpx

from jarvix import __version__
from jarvix.capabilities.diagnostics import access, permitted
from jarvix.capabilities.files import _linked
from jarvix.capabilities.schema import ID, array, enum, integer, register, schema, string
from jarvix.capabilities.productivity import timestamp
from jarvix.providers._transport import load_json
from jarvix.runtime import cancel_response, check_cancelled
from jarvix.storage import Database, SettingsRepository, now_iso
from jarvix.records import RecordStore

MAX_DATABASE_BYTES = 512 * 1024**2
MAX_BACKUP_TOTAL = 2 * 1024**3
TABLES = {"settings", "notes", "memories", "tasks", "projects", "apps", "conversations", "messages", "activity",
          "grants", "files", "automations", "records", "integrations", "scheduled_tasks"}
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}")
BACKUP = re.compile(r"jarvix-backup-[A-Za-z0-9_-]{1,100}")
SHA256 = re.compile(r"[a-fA-F0-9]{64}")


def digest(path, maximum=MAX_DATABASE_BYTES):
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("Choose a regular file within the bounded size limit.")
    result, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            check_cancelled()
            size += len(chunk)
            if size > maximum:
                raise ValueError("File exceeds the bounded size limit.")
            result.update(chunk)
    return result.hexdigest(), size


class MaintenanceService:
    def __init__(self, services):
        self.s = services
        self._lock = threading.RLock()

    def _directory(self, name):
        profile = self.s.data_dir.resolve(strict=True)
        target = profile / name
        if any(_linked(part) for part in (self.s.data_dir, target, *self.s.data_dir.parents)):
            raise PermissionError("Profile maintenance cannot use linked paths.")
        target.mkdir(exist_ok=True)
        if target.resolve(strict=True) != target or not target.is_relative_to(profile):
            raise PermissionError("Profile directory changed.")
        return target

    def create(self):
        with self._lock:
            return self._create()

    def _create(self):
        access(self.s, "backup.create")
        directory = self._directory("backups")
        previous = list(islice(directory.glob("jarvix-backup-*.db"), 101))
        pages = self.s.db.query("PRAGMA page_count")[0]["page_count"]
        page_size = self.s.db.query("PRAGMA page_size")[0]["page_size"]
        expected_size = pages * page_size
        if (len(previous) >= 100 or expected_size > MAX_DATABASE_BYTES
                or sum(path.stat().st_size for path in previous if not _linked(path) and path.is_file()) + expected_size > MAX_BACKUP_TOTAL):
            raise ValueError("Backup limit reached (100 snapshots, 2 GiB total, 512 MiB per DB). Review and remove old local backups before continuing.")
        result = self.s.create_backup()
        target = Path(result["path"])
        if target.parent != directory or _linked(target):
            raise PermissionError("Backup destination changed.")
        sha256, size = digest(target)
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        manifest = {"format": "jarvix-profile-backup", "version": 1, "created_at": now_iso(),
                    "jarvix_version": __version__, "database": {"filename": target.name, "size_bytes": size,
                    "sha256": sha256, "schema_version": version}, "credentials_included": False,
                    "external_files_included": False, "protected_data": "Current-user Windows DPAPI where enabled"}
        with target.with_suffix(".json").open("x", encoding="utf-8") as output:
            json.dump(manifest, output, allow_nan=False, indent=2)
        return {"id": target.stem, "path": str(target), "manifest_path": str(target.with_suffix(".json")),
                "size_bytes": size, "sha256": sha256, "credentials_included": False}

    def schedule(self, name, trigger_config):
        access(self.s, "backup.schedule")
        access(self.s, "backup.create")
        access(self.s, "workflows.save")
        if not isinstance(trigger_config, dict) or trigger_config.get("trigger") not in {"interval", "schedule", "at_time"}:
            raise ValueError("Choose an existing interval, daily schedule, or exact-time workflow trigger.")
        config = {key: value for key, value in trigger_config.items() if key != "trigger"}
        if trigger_config["trigger"] == "interval" and not 60 <= config.get("minutes", 60) <= 10080:
            raise ValueError("Scheduled backups support intervals from one hour to one week.")
        return self.s.execute_tool("workflows.save", {"name": name, "trigger": trigger_config["trigger"], "config": config,
            "steps": [{"kind": "action", "tool": "backup.create", "arguments": {}}],
            "approved_tools": ["backup.create"], "enabled": False})

    def list(self):
        access(self.s, "backup.list")
        directory = self._directory("backups")
        items = []
        for path in sorted(directory.glob("jarvix-backup-*.db"), reverse=True)[:100]:
            check_cancelled()
            if not _linked(path) and path.is_file():
                items.append({"id": path.stem, "size_bytes": path.stat().st_size,
                              "manifest_available": path.with_suffix(".json").is_file() and not _linked(path.with_suffix(".json"))})
        return {"items": items}

    def _verified(self, id):
        if not isinstance(id, str) or not BACKUP.fullmatch(id):
            raise ValueError("Choose an existing Jarvix backup ID.")
        directory = self._directory("backups")
        target, manifest_path = directory / (id + ".db"), directory / (id + ".json")
        if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Backup is in use or has uncheckpointed changes; close it before verification.")
        if _linked(target) or _linked(manifest_path) or not manifest_path.is_file() or manifest_path.stat().st_size > 8000:
            raise ValueError("A bounded manifest is required; legacy backups need manual recovery.")
        with manifest_path.open("rb") as source:
            raw = source.read(8001)
        if len(raw) > 8000:
            raise ValueError("Backup manifest exceeds its size limit.")
        manifest = load_json(raw)
        if not isinstance(manifest, dict):
            raise ValueError("Invalid backup manifest.")
        database = manifest.get("database", {})
        if (not isinstance(database, dict) or manifest.get("format") != "jarvix-profile-backup"
                or type(manifest.get("version")) is not int or manifest["version"] != 1
                or database.get("filename") != target.name or type(database.get("size_bytes")) is not int
                or not isinstance(database.get("sha256"), str) or not SHA256.fullmatch(database["sha256"])):
            raise ValueError("Invalid backup manifest.")
        sha256, size = digest(target)
        if size != database["size_bytes"] or sha256 != database["sha256"].lower():
            raise ValueError("Backup hash or size verification failed.")
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            conn.execute("PRAGMA trusted_schema=OFF")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if (version not in (2, 3, 4) or version != database.get("schema_version") or not TABLES <= tables
                    or tables - TABLES - {"sqlite_sequence"}
                    or conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger')").fetchone()
                    or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
                raise ValueError("Backup database is damaged or incompatible.")
        return target, manifest

    def preview(self, id):
        access(self.s, "backup.preview")
        target, manifest = self._verified(id)
        return {"id": id, "verified": True, "path": str(target), "created_at": manifest.get("created_at"),
                "size_bytes": manifest["database"]["size_bytes"], "sha256": manifest["database"]["sha256"],
                "credentials_included": False, "external_files_included": False,
                "restore_behavior": "Creates an isolated profile; current profile is unchanged. Review permissions and workflows after restart.",
                "protected_data": "DPAPI-protected data can only be opened by the original Windows account.",
                "restart_required": True}

    def stage_restore(self, id, sha256):
        access(self.s, "backup.stage_restore")
        access(self.s, "backup.preview")
        source, manifest = self._verified(id)
        if not isinstance(sha256, str) or sha256 != manifest["database"]["sha256"]:
            raise ValueError("Review this exact backup hash before staging a restore.")
        directory = self._directory("restores") / uuid.uuid4().hex
        directory.mkdir()
        target = directory / "jarvix.db"
        # The source is already a consistent SQLite backup. Copy only its verified bytes;
        # opening it as a writable Database would change the original snapshot's header.
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            copied = 0
            while chunk := incoming.read(1024 * 1024):
                check_cancelled()
                copied += len(chunk)
                if copied > manifest["database"]["size_bytes"]:
                    raise ValueError("Backup changed during staging; do not open this staged profile.")
                outgoing.write(chunk)
        if digest(target)[0] != sha256:
            raise ValueError("Backup changed during staging; do not open this staged profile.")
        restored = Database(target, codec=self.s.db.codec)
        settings, records = SettingsRepository(restored), RecordStore(restored)
        with restored.connect() as conn:
            conn.execute("DELETE FROM grants WHERE decision='allow'")
            conn.execute("UPDATE automations SET enabled=0")
            conn.execute("UPDATE scheduled_tasks SET enabled=0")
            conn.execute("DELETE FROM records WHERE kind IN ('operator_checkpoint','device_peer','device_receipt')")
        for key in ("control.enabled", "automations.enabled", "proactive.enabled", "context.enabled", "browser.control_enabled",
                    "clipboard.enabled", "screenshots.enabled", "microphone.enabled", "wake.enabled", "windows.recent.enabled"):
            settings.set(key, False)
        for kind in ("workflow", "closed_schedule", "plugin_state"):
            for row in self._restore_rows(restored, records, kind):
                row.update(enabled=False)
                if kind == "workflow":
                    row["approved_tools"] = []
                if kind == "closed_schedule":
                    row["last_status"] = "Restored; explicit review and reinstall required"
                records.put(kind, row, row["id"])
        for row in self._restore_rows(restored, records, "operator_session"):
            row.update(checkpoint_available=False, retry_available=False)
            if row.get("status") not in {"complete", "failed", "cancelled", "timed_out", "denied", "interrupted"}:
                row["status"] = "interrupted"
                for step in row.get("steps", []):
                    if step.get("status") == "running":
                        step.update(status="interrupted", retry_safe=False)
            records.put("operator_session", row, row["id"])
        launch = [sys.executable, *([] if getattr(sys, "frozen", False) else ["-m", "jarvix"]), "--data-dir", str(directory)]
        self.s.repository.audit("backup", "Verified backup staged as a separate profile; approvals and unattended work disabled")
        return {"staged_profile": str(directory), "database_path": str(target), "restart_required": True,
                "current_profile_restored": False, "unattended_work_enabled": False, "credentials_included": False,
                "launch_command": subprocess.list2cmdline(launch), "launch_arguments": launch,
                "protected_data": "Open with the original Windows account. Vault credentials are not copied; reconnect integrations explicitly."}

    @staticmethod
    def _restore_rows(db, records, kind):
        # RecordStore.list intentionally caps UI pages; sanitization must cover every row.
        cursor = ""
        while rows := db.query("SELECT id FROM records WHERE kind=? AND id>? ORDER BY id LIMIT 100", (kind, cursor)):
            for row in rows:
                check_cancelled()
                yield records.get(kind, row["id"])
            cursor = rows[-1]["id"]

    def configure_updates(self, repository):
        access(self.s, "updates.configure")
        if not isinstance(repository, str) or not REPO.fullmatch(repository) or repository.split("/")[1] in {".", ".."}:
            raise ValueError("Enter the trusted GitHub owner/repository explicitly.")
        self.s.settings.set("updates.repository", repository)
        # A previously checked release belongs to the previous configuration.
        self.s.records.delete("update_release", "latest")
        return {"repository": repository, "automatic_check": False, "automatic_install": False}

    def update_status(self):
        access(self.s, "updates.status")
        repository = self.s.settings.get("updates.repository", "")
        release, artifact = None, None
        if permitted(self.s, "updates.check"):
            try:
                value = self.s.records.get("update_release", "latest")
                if value.get("repository") == repository:
                    release = {key: value.get(key) for key in ("repository", "tag", "url", "published_at", "checked_at", "assets",
                                                              "changelog", "changelog_truncated", "content_trust")}
                    from datetime import datetime, timezone
                    release["cached"] = True
                    release["stale"] = (datetime.now(timezone.utc) - timestamp(value["checked_at"])).total_seconds() > 86400
            except (ValueError, KeyError, TypeError):
                pass
        if permitted(self.s, "updates.verify_artifact") and permitted(self.s, "files.inspect"):
            try:
                value = self.s.records.get("update_artifact", "latest")
                if (value.get("checksum_source") == "user_supplied_checksum"
                        or (release is not None and value.get("repository") == repository)):
                    target = self.s.files.path(value["path"])
                    actual, size = digest(target)
                    if actual == value["sha256"] and size == value["size_bytes"]:
                        artifact = {key: value.get(key) for key in ("path", "sha256", "size_bytes", "checksum_source", "verified_at")}
            except (ValueError, OSError, KeyError):
                pass
        return {"current_version": __version__, "repository": repository or None, "cached_release": release,
                "download_status": "verified_local_artifact" if artifact else "not_downloaded", "artifact": artifact,
                "network_requests": 0, "automatic_check": False, "automatic_install": False}

    def check_update(self):
        access(self.s, "updates.check")
        repository = self.s.settings.get("updates.repository", "")
        if (not isinstance(repository, str) or not REPO.fullmatch(repository)
                or repository.split("/")[1] in {".", ".."}):
            raise ValueError("Configure the official GitHub repository before checking releases.")
        url = "https://api.github.com/repos/" + repository + "/releases/latest"
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False) as client:
            with client.stream("GET", url, follow_redirects=False,
                    headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}) as response, cancel_response(response):
                if response.status_code == 404:
                    return {"repository": repository, "available": False, "reason": "No public release found", "installed": False}
                if response.status_code != 200:
                    raise RuntimeError("GitHub release check failed; no artifact was downloaded or installed.")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    check_cancelled()
                    raw.extend(chunk)
                    if len(raw) > 256000:
                        raise ValueError("Release metadata exceeds its size limit.")
        value = load_json(bytes(raw))
        expected_prefix = "https://github.com/" + repository + "/releases/"
        if (not isinstance(value, dict) or value.get("draft") is not False or value.get("prerelease") is not False
                or not isinstance(value.get("html_url"), str) or not value["html_url"].startswith(expected_prefix)
                or not isinstance(value.get("tag_name"), str) or not 1 <= len(value["tag_name"]) <= 120):
            raise ValueError("Release metadata does not match the configured repository.")
        assets = []
        if not isinstance(value.get("assets", []), list):
            raise ValueError("Invalid GitHub release asset metadata.")
        for asset in value.get("assets", [])[:30]:
            if not isinstance(asset, dict):
                continue
            checksum, address, name = asset.get("digest"), asset.get("browser_download_url"), asset.get("name")
            if (not isinstance(address, str) or not address.startswith(expected_prefix + "download/")
                    or len(address) > 4096 or not isinstance(name, str) or not 1 <= len(name) <= 160
                    or "/" in name or "\\" in name or any(ord(char) < 32 for char in name)):
                continue
            assets.append({"name": name, "url": address, "size_bytes": asset.get("size"),
                           "sha256": checksum[7:].lower() if isinstance(checksum, str) and checksum.startswith("sha256:")
                           and SHA256.fullmatch(checksum[7:]) else None})
        changelog = value.get("body") or ""
        if not isinstance(changelog, str):
            raise ValueError("Invalid GitHub release changelog.")
        raw_changelog = changelog.encode("utf-8")
        release = {"repository": repository, "tag": value["tag_name"], "url": value["html_url"],
                   "published_at": value.get("published_at"), "checked_at": now_iso(), "assets": assets,
                   "changelog": raw_changelog[:8000].decode("utf-8", errors="ignore"),
                   "changelog_truncated": len(raw_changelog) > 8000, "content_trust": "untrusted_release_metadata"}
        access(self.s, "updates.check")
        if repository != self.s.settings.get("updates.repository", ""):
            raise ValueError("Release repository changed during the check.")
        self.s.records.put("update_release", release, "latest")
        return {**release, "current_version": __version__, "different_version": value["tag_name"].lstrip("v") != __version__,
                "installed": False, "automatic_install": False}

    def verify_artifact(self, path, sha256=None, asset_name=None):
        access(self.s, "updates.verify_artifact")
        access(self.s, "files.inspect")
        target = self.s.files.path(path)
        source = "user_supplied_checksum"
        if asset_name is not None:
            access(self.s, "updates.check")
            release = self.s.records.get("update_release", "latest")
            if release["repository"] != self.s.settings.get("updates.repository", ""):
                raise ValueError("Check the currently configured repository first.")
            matches = [row for row in release["assets"] if row["name"] == asset_name and row.get("sha256")]
            if len(matches) != 1 or target.name != asset_name:
                raise ValueError("This artifact needs a checksum from the checked release, or an explicit expected SHA256.")
            expected = matches[0]["sha256"]
            if sha256 is not None and sha256.lower() != expected:
                raise ValueError("User checksum differs from checked release metadata.")
            sha256, source = expected, "configured_github_release_digest"
        if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
            raise ValueError("Supply the trusted expected SHA256; filenames alone do not verify artifacts.")
        actual, size = digest(target)
        if actual != sha256.lower():
            raise ValueError("Artifact SHA256 verification failed; do not run it.")
        value = {"path": str(target), "sha256": actual, "size_bytes": size, "checksum_source": source, "verified_at": now_iso()}
        if asset_name is not None:
            value["repository"] = release["repository"]
        access(self.s, "updates.verify_artifact")
        access(self.s, "files.inspect")
        if self.s.files.path(path) != target:
            raise ValueError("Artifact root or path changed during verification.")
        self.s.records.put("update_artifact", value, "latest")
        return {**value, "verified": True,
                "authenticity": "Matches the explicitly trusted repository digest" if asset_name is not None
                else "Checksum integrity only; a user-supplied checksum does not establish publisher authenticity.",
                "publisher_signature_verified": False, "installed": False, "executed": False}


def setup(s, registry):
    service = s.maintenance = MaintenanceService(s)
    register(registry, "backup.create", "Create a consistent local DB backup with a versioned SHA256 manifest; excludes vault credentials and external files.",
             {}, (), service.create, 2, "storage.backup")
    register(registry, "backup.schedule", "Prepare a disabled workflow whose sole action is a local backup. Review its exact schedule and enable through existing workflow approval; never creates a second timer.",
             {"name": string(160), "trigger_config": schema({"trigger": enum("interval", "schedule", "at_time"),
              "minutes": integer(60, 10080), "time": string(5), "weekdays": array(integer(0, 6), 7), "at": string(40)}, ("trigger",))},
             ("name", "trigger_config"), service.schedule, 2, "storage.backup")
    register(registry, "backup.list", "List this profile's local DB snapshots and manifest availability.", {}, (), service.list)
    register(registry, "backup.preview", "Verify backup size, SHA256 and SQLite integrity before reviewing an isolated restore.",
             {"id": ID}, ("id",), service.preview)
    register(registry, "backup.stage_restore", "Stage the exact reviewed backup as a separate profile for restart; remove stored allows, disable unattended work, and never overwrite live data.",
             {"id": ID, "sha256": string(64)}, ("id", "sha256"), service.stage_restore, 3, "storage.restore")
    register(registry, "updates.configure", "Explicitly choose the trusted GitHub owner/repository for manual release checks; never enables automatic requests or installation.",
             {"repository": string(140)}, ("repository",), service.configure_updates, 3, "updates.configure")
    register(registry, "updates.check", "Request only public latest-release metadata from the configured official GitHub repository. No local content or API credentials are sent.",
             {}, (), service.check_update, 2, "network.read")
    register(registry, "updates.status", "Inspect current version, explicitly configured repository, cached release and revalidated local artifact; never checks the network automatically.",
             {}, (), service.update_status)
    register(registry, "updates.verify_artifact", "Verify an approved local artifact against a user-supplied SHA256 or checked GitHub release digest; never opens or installs it.",
             {"path": string(4096), "sha256": string(64), "asset_name": string(160)}, ("path",), service.verify_artifact)
