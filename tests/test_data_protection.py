import sqlite3

import pytest

from jarvix.data_protection import DataProtectionService, PREFIX, PLAIN_PREFIX
from jarvix.services import Services
from jarvix.storage import Database


def test_dpapi_migration_future_writes_and_selected_notes(tmp_path):
    s = Services(tmp_path)
    s.data_protection = DataProtectionService(s)
    note = s.save_note("Private title", "Private document")
    public = s.save_note("Public note", "Visible")
    chat = s.new_conversation("Private conversation")
    s.repository.append_message(chat, "user", "Private message")
    s.records.put("account.meta", {"account": "private@example.test"})
    s.data_protection.configure(True, [note])
    with sqlite3.connect(s.db.path) as raw:
        assert raw.execute("SELECT body FROM notes WHERE id=?", (note,)).fetchone()[0].startswith(PREFIX)
        assert raw.execute("SELECT body FROM notes WHERE id=?", (public,)).fetchone()[0] == "Visible"
        assert raw.execute("SELECT content FROM messages").fetchone()[0].startswith(PREFIX)
        assert "private@example.test" not in raw.execute("SELECT data FROM records").fetchone()[0]
    s.save_note("Updated", "Changed private document", note)
    s.repository.append_message(chat, "assistant", "Future protected message")
    assert s.conversation_messages(chat)[-1]["content"] == "Future protected message"
    assert next(n for n in s.list_notes() if n["id"] == note)["body"] == "Changed private document"
    s.data_protection.configure(False)
    with sqlite3.connect(s.db.path) as raw:
        assert raw.execute("SELECT content FROM messages ORDER BY id DESC").fetchone()[0] == "Future protected message"
    s.close()


def test_failed_migration_rolls_back(tmp_path):
    s = Services(tmp_path)
    note = s.save_note("Title", "original")
    s.data_protection = DataProtectionService(s)
    def fail(data, decrypt=False):
        raise RuntimeError("Unavailable vault")
    s.db.codec = fail
    with pytest.raises(RuntimeError):
        s.data_protection.configure(True)
    assert s.list_notes()[0]["id"] == note
    assert not s.settings.get("storage.encryption_enabled", False)
    s.close()


def test_protection_survives_restart_and_existing_cursor(tmp_path):
    s = Services(tmp_path)
    note = s.save_note("Private", "Original")
    with s.db.connect() as before_migration:
        s.data_protection.configure(True)
        before_migration.cursor().execute("BEGIN IMMEDIATE")
        before_migration.cursor().execute("UPDATE notes SET body=? WHERE id=?", ("Cursor write", note))
    s.close()
    reopened = Services(tmp_path)
    assert reopened.list_notes()[0]["body"] == "Cursor write"
    with reopened.db.connect() as conn:
        conn.cursor().executemany("INSERT INTO memories VALUES (?,?,?)", [("one", "secret", "2026")])
        with pytest.raises(ValueError):
            conn.cursor().executescript("UPDATE notes SET body='plaintext';")
    with sqlite3.connect(reopened.db.path) as raw:
        assert raw.execute("SELECT body FROM notes").fetchone()[0].startswith(PREFIX)
        assert raw.execute("SELECT content FROM memories").fetchone()[0].startswith(PREFIX)
    reopened.close()


def test_reserved_prefix_text_roundtrips_with_and_without_protection(tmp_path):
    s = Services(tmp_path)
    note = s.save_note(PREFIX + "example", PLAIN_PREFIX + "literal")
    task = s.add_task(PREFIX + "task")
    assert s.list_notes()[0]["title"] == PREFIX + "example"
    assert next(row for row in s.list_tasks() if row["id"] == task)["title"] == PREFIX + "task"
    s.data_protection.configure(True)
    assert s.list_notes()[0]["body"] == PLAIN_PREFIX + "literal"
    s.data_protection.configure(False)
    assert s.list_notes()[0]["title"] == PREFIX + "example"
    assert s.list_notes()[0]["id"] == note
    s.close()


def test_mid_migration_failure_is_atomic_and_corrupt_policy_preserves_data(tmp_path):
    s = Services(tmp_path)
    s.save_note("Private", "Original")
    calls = 0
    original = s.db.codec
    def fail_midway(data, decrypt=False):
        nonlocal calls
        calls += 1
        if calls > 3:
            raise RuntimeError("OS protection failed")
        return original(data, decrypt=decrypt)
    s.db.codec = fail_midway
    with pytest.raises(RuntimeError):
        s.data_protection.configure(True)
    s.db.codec = original
    assert s.list_notes()[0]["body"] == "Original"
    assert not s.settings.get("storage.encryption_enabled", False)
    s.close()
    with sqlite3.connect(s.db.path) as raw:
        raw.execute("INSERT INTO settings VALUES ('storage.encryption_enabled','damaged')")
    with pytest.raises(RuntimeError, match="policy is damaged"):
        Database(s.db.path)
    with sqlite3.connect(s.db.path) as raw:
        assert raw.execute("SELECT body FROM notes").fetchone()[0] == "Original"
