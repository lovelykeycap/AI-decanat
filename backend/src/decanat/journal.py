from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from decanat.contracts import Evidence


class Journal:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "journal.sqlite3"
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                question TEXT NOT NULL, answer TEXT NOT NULL, status TEXT NOT NULL,
                provider TEXT NOT NULL, kb_id TEXT NOT NULL, release_id TEXT NOT NULL,
                sources TEXT NOT NULL, interrupted INTEGER NOT NULL DEFAULT 0,
                spoken_text TEXT NOT NULL DEFAULT '')""")
        self.purge()

    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        return db

    def record(
        self,
        *,
        turn_id: str,
        session_id: str,
        question: str,
        answer: str,
        status: str,
        provider: str,
        kb_id: str,
        release_id: str,
        sources: list[Evidence],
        created_at: datetime | None = None,
    ) -> None:
        timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._db() as db:
            db.execute(
                """INSERT OR REPLACE INTO turns
                (turn_id,session_id,created_at,question,answer,status,provider,kb_id,release_id,sources)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    turn_id,
                    session_id,
                    timestamp,
                    question,
                    answer,
                    status,
                    provider,
                    kb_id,
                    release_id,
                    json.dumps([s.model_dump() for s in sources], ensure_ascii=False),
                ),
            )

    def playback(self, turn_id: str, spoken_text: str, *, interrupted: bool = False) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE turns SET spoken_text=?,interrupted=? WHERE turn_id=?",
                (spoken_text, int(interrupted), turn_id),
            )

    def list(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM turns ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [{**dict(row), "sources": json.loads(row["sources"])} for row in rows]

    def purge(self, now: datetime | None = None) -> int:
        cutoff = ((now or datetime.now(UTC)) - timedelta(days=30)).astimezone(UTC).isoformat()
        with self._db() as db:
            count = db.execute("DELETE FROM turns WHERE created_at < ?", (cutoff,)).rowcount
        if count:
            with self._db() as db:
                db.execute("VACUUM")
        return count
