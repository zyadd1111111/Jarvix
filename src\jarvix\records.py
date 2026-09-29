"""Versioned extension records stored in the existing local SQLite database."""
import json
from datetime import datetime, timezone

from jarvix.storage import Repository


class RecordStore:
    def __init__(self, db):
        self.db = db

    def list(self, kind):
        return [self._decode(row) for row in self.db.query(
            "SELECT * FROM records WHERE kind=? ORDER BY updated_at DESC,rowid DESC LIMIT 2000", (kind,))]

    def get(self, kind, record_id):
        rows = self.db.query("SELECT * FROM records WHERE kind=? AND id=?", (kind, record_id))
        if not rows:
            raise ValueError("Record not found.")
        return self._decode(rows[0])

    @staticmethod
    def _decode(row):
        return {**json.loads(row["data"]), "id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def put(self, kind, data, record_id=None):
        record_id = record_id or Repository.new_id()
        data = {key: value for key, value in data.items() if key not in {"id", "created_at", "updated_at"}}
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 250000:
            raise ValueError("Record exceeds the local size limit.")
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self.db.execute("INSERT INTO records(id,kind,data,created_at,updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(id,kind) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                        (record_id, kind, encoded, stamp, stamp))
        return record_id

    def delete(self, kind, record_id):
        self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id))
