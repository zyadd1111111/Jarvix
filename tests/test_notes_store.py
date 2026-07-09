from __future__ import annotations

from memory.notes_store import NotesStore


def test_create_and_search_notes(tmp_path) -> None:
    store = NotesStore(tmp_path / "jarvix.db")

    note = store.create_note("Launch plan", "Build the local assistant.")
    assert note.id > 0
    assert note.title == "Launch plan"

    matches = store.search_notes("assistant")
    assert len(matches) == 1
    assert matches[0].id == note.id


def test_notes_persist_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "jarvix.db"
    first = NotesStore(database_path)
    first.create_note("Memory", "SQLite keeps local notes.")

    second = NotesStore(database_path)
    matches = second.search_notes("SQLite")
    assert [note.title for note in matches] == ["Memory"]

