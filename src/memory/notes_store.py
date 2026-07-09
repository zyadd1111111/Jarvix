from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Note:
    id: int
    title: str
    body: str
    created_at: str
    updated_at: str


class NotesStore:
    """SQLite-backed local notes store."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_note(self, title: str, body: str) -> Note:
        clean_title = title.strip() or "Untitled"
        clean_body = body.strip()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO notes (title, body, created_at, updated_at)
                VALUES (?, ?, datetime('now'), datetime('now'))
                """,
                (clean_title, clean_body),
            )
            note_id = int(cursor.lastrowid)
        return self.get_note(note_id)

    def search_notes(self, query: str, limit: int = 25) -> list[Note]:
        pattern = f"%{query.strip()}%"
        if not query.strip():
            return self.list_recent(limit=limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, body, created_at, updated_at
                FROM notes
                WHERE title LIKE ? OR body LIKE ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()
        return [self._row_to_note(row) for row in rows]

    def list_recent(self, limit: int = 25) -> list[Note]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, body, created_at, updated_at
                FROM notes
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_note(row) for row in rows]

    def get_note(self, note_id: int) -> Note:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, body, created_at, updated_at
                FROM notes
                WHERE id = ?
                """,
                (note_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Note not found: {note_id}")
        return self._row_to_note(row)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        return Note(
            id=int(row["id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

