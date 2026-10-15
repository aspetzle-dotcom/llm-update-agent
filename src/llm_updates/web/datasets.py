"""Local dataset storage, isolated from the collector database and static exports."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

MAX_CSV_BYTES = 2_000_000
MAX_REQUEST_BYTES = 2_500_000
DATASET_ID = re.compile(r"^dataset-[a-f0-9-]{36}$")


def validate_csv(text: str) -> tuple[int, int]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("Choose a CSV under 2 MB.")
    try:
        rows = csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True)
        headers = next(rows, [])
        headers = [h.strip() for h in headers]
        if (
            not headers
            or len(headers) > 50
            or not all(headers)
            or len(set(headers)) != len(headers)
        ):
            raise ValueError("Use up to 50 unique, nonempty column names.")
        count = 0
        for row in rows:
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError("Every row must match the header column count.")
            count += 1
            if count > 10_000:
                raise ValueError("Use up to 10,000 rows.")
        if not count:
            raise ValueError("Include at least one data row.")
        return count, len(headers)
    except csv.Error as exc:
        raise ValueError("Invalid CSV format or oversized field.") from exc


class DatasetStore:
    def __init__(self, database: str):
        self.path = Path(str(database) + ".workspace.sqlite3")

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            updated_at TEXT NOT NULL, csv TEXT NOT NULL)""")
        return db

    @staticmethod
    def _item(row):
        return {
            "id": row["id"],
            "kind": "dataset",
            "title": row["title"],
            "content": row["content"],
            "updatedAt": row["updated_at"],
        }

    def list(self):
        if not self.path.exists():
            return []
        db = self._connect()
        try:
            return [
                self._item(r)
                for r in db.execute(
                    "SELECT id,title,content,updated_at FROM datasets ORDER BY updated_at DESC"
                )
            ]
        finally:
            db.close()

    def upload(self, name: str, text: str):
        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            raise ValueError("Use a filename between 1 and 300 characters.")
        rows, columns = validate_csv(text)
        item = {
            "id": "dataset-" + str(uuid.uuid4()),
            "title": name,
            "content": json.dumps(
                {"rows": rows, "columns": columns, "bytes": len(text.encode("utf-8"))}
            ),
            "updated_at": datetime.now(UTC).isoformat(),
            "csv": text,
        }
        db = self._connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO datasets VALUES (:id,:title,:content,:updated_at,:csv)", item
                )
        finally:
            db.close()
        return self._item(item)

    def get(self, dataset_id: str):
        if not DATASET_ID.fullmatch(dataset_id):
            raise ValueError("Invalid dataset ID.")
        if not self.path.exists():
            return None
        db = self._connect()
        try:
            row = db.execute("SELECT csv FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            return row["csv"] if row else None
        finally:
            db.close()

    def delete(self, dataset_id: str):
        if not DATASET_ID.fullmatch(dataset_id):
            raise ValueError("Invalid dataset ID.")
        if not self.path.exists():
            return
        db = self._connect()
        try:
            with db:
                db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        finally:
            db.close()
