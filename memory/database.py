"""
Persistent long-term memory stored in SQLite.

SQLite is built into Python and keeps everything in one local file
(data/nova.db by default). The file and table are created automatically.

The MemoryStore class is the only place that touches the database, so a
smarter memory (e.g. semantic/vector search) can be added later behind the
same methods.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

log = get_logger("memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
"""


class MemoryStoreError(Exception):
    """The memory database could not be opened or used."""


@dataclass
class Memory:
    id: int
    content: str
    created_at: str  # ISO 8601 timestamp in UTC


class MemoryStore:
    """Save, list and delete long-term memories."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn, conn:
                conn.executescript(SCHEMA)
        except (sqlite3.Error, OSError) as error:
            log.exception("Could not open memory database at %s", self.db_path)
            raise MemoryStoreError(
                f"Could not open the memory database at {self.db_path}. "
                "If the file is corrupted, move it aside and NOVA will create a new one."
            ) from error

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Run one SQL statement and return any rows. Wraps errors nicely."""
        try:
            # closing() closes the connection; `with conn` commits the change.
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
                self._last_rowcount = cursor.rowcount
                self._last_id = cursor.lastrowid
                return rows
        except sqlite3.Error as error:
            log.exception("Memory database error running: %s", sql)
            raise MemoryStoreError("The memory database had a problem. See the log for details.") from error

    # --- public methods ---------------------------------------------------

    def add(self, content: str) -> Memory:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty")
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._run("INSERT INTO memories (content, created_at) VALUES (?, ?)", (content, created_at))
        log.info("Saved memory #%s", self._last_id)
        return Memory(self._last_id, content, created_at)

    def get(self, memory_id: int) -> Memory | None:
        rows = self._run("SELECT id, content, created_at FROM memories WHERE id = ?", (memory_id,))
        return Memory(**dict(rows[0])) if rows else None

    def list(self, limit: int | None = None) -> list[Memory]:
        """All memories, oldest first. With `limit`, only the most recent ones."""
        if limit is None:
            rows = self._run("SELECT id, content, created_at FROM memories ORDER BY id")
        else:
            rows = self._run(
                "SELECT * FROM (SELECT id, content, created_at FROM memories "
                "ORDER BY id DESC LIMIT ?) ORDER BY id",
                (limit,),
            )
        return [Memory(**dict(row)) for row in rows]

    def delete(self, memory_id: int) -> bool:
        """Delete one memory. Returns False if it did not exist."""
        self._run("DELETE FROM memories WHERE id = ?", (memory_id,))
        deleted = self._last_rowcount > 0
        if deleted:
            log.info("Deleted memory #%s", memory_id)
        return deleted

    def clear(self) -> int:
        """Delete every memory. Returns how many were removed."""
        self._run("DELETE FROM memories")
        log.info("Cleared %s memories", self._last_rowcount)
        return self._last_rowcount

    def count(self) -> int:
        return self._run("SELECT COUNT(*) AS n FROM memories")[0]["n"]
