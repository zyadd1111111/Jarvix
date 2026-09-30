from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path

import pytest

from jarvix.services import Services
from jarvix.storage import Database, Repository, SettingsRepository


def test_schema_created_once_and_settings_persist(tmp_path):
    path = tmp_path / "jarvix.db"
    settings = SettingsRepository(Database(path))
    settings.set("provider", "gemini")
    settings.set("files.roots", ["a", "b"])
    reopened = SettingsRepository(Database(path))
    assert reopened.get("provider") == "gemini"
    assert reopened.get("files.roots") == ["a", "b"]
    assert reopened.get("missing", 5) == 5


@pytest.mark.parametrize("key", ["openai.api_key", "password", "oauth.token", "secret", "oauth.credential", "private_key"])
def test_settings_reject_credentials(tmp_path, key):
    with pytest.raises(ValueError, match="vault"):
        SettingsRepository(Database(tmp_path / "data.db")).set(key, "sensitive")


def test_settings_recover_from_malformed_values_and_reject_unsafe_json(tmp_path):
    db = Database(tmp_path / "data.db")
    settings = SettingsRepository(db)
    db.execute("INSERT INTO settings(key,value) VALUES (?,?)", ("broken", "{not-json"))

    assert settings.get("broken", "fallback") == "fallback"
    with pytest.raises(ValueError, match="JSON-compatible"):
        settings.set("safe", float("nan"))
    with pytest.raises(ValueError, match="size limit"):
        settings.set("safe", "x" * (256 * 1024))


def test_consistent_backup_is_self_contained_and_keeps_the_original_writable(tmp_path):
    profile = tmp_path / "profile"
    services = Services(profile)
    try:
        services.save_note("Before backup", "Snapshot content")
        backup = services.create_backup()
        target = Path(backup["path"])

        assert target.is_file()
        assert target.parent == profile / "backups"
        assert backup["credentials_included"] is False
        with sqlite3.connect(target) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT body FROM notes").fetchone()[0] == "Snapshot content"

        services.save_note("After backup", "Live database remains writable")
        with sqlite3.connect(target) as connection:
            assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    finally:
        services.close()


def test_transactions_rollback_and_foreign_keys(tmp_path):
    db = Database(tmp_path / "data.db")
    with pytest.raises(RuntimeError), db.connect() as conn:
        conn.execute("INSERT INTO settings VALUES ('test','123')")
        raise RuntimeError("rollback")
    assert db.query("SELECT * FROM settings") == []
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        Repository(db).append_message("missing", "user", "hello")


def test_concurrent_activity_writes(tmp_path):
    repo = Repository(Database(tmp_path / "data.db"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: repo.audit("test", str(n)), range(60)))
    assert len(repo.db.query("SELECT * FROM activity")) == 60


def test_repository_table_names_are_not_arbitrary_sql(tmp_path):
    repo = Repository(Database(tmp_path / "data.db"))
    with pytest.raises(ValueError):
        repo.list("settings; DROP TABLE notes")
    assert repo.list("notes") == []
