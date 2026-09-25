"""
Persistent long-term memory stored in SQLite.

SQLite is built into Python and keeps everything in one local file
(data/nova.db by default). Tables are created automatically.

Three tables:
  memories  - facts about you ("My store is called EXORASTORE.")
  knowledge - things NOVA learned from the internet, with their sources
  lessons   - how NOVA should behave, learned from your corrections

The MemoryStore class is the only place that touches the database, so a
smarter search (e.g. semantic/vector search) can be added later behind the
same methods.
"""

from __future__ import annotations  # lets us name a method "list"

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger
from utils.text import relevance

log = get_logger("memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
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


@dataclass
class Knowledge:
    id: int
    topic: str
    content: str
    source: str  # where it was learned, e.g. URLs
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rank(query: str, items: list, text_of, limit: int) -> list:
    """The `limit` items most related to `query` (unrelated ones are dropped)."""
    scored = [(relevance(query, text_of(item)), item) for item in items]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda pair: (-pair[0], -pair[1].id))  # best first, newest wins ties
    return [item for _, item in scored[:limit]]


class MemoryStore:
    """Save, search and delete long-term memories and learned knowledge."""

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

    # --- memories (facts about the user) -----------------------------------

    def add(self, content: str) -> Memory:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty")
        created_at = _now()
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

    def search(self, query: str, limit: int = 10) -> list[Memory]:
        """Memories that share words with `query`, most relevant first."""
        return _rank(query, self.list(), lambda m: m.content, limit)

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

    # --- knowledge (learned from the internet) ------------------------------

    def add_knowledge(self, topic: str, content: str, source: str = "") -> Knowledge:
        topic, content = topic.strip(), content.strip()
        if not topic or not content:
            raise ValueError("Knowledge needs a topic and content")
        created_at = _now()
        self._run(
            "INSERT INTO knowledge (topic, content, source, created_at) VALUES (?, ?, ?, ?)",
            (topic, content, source.strip(), created_at),
        )
        log.info("Saved knowledge #%s about %r", self._last_id, topic)
        return Knowledge(self._last_id, topic, content, source.strip(), created_at)

    def list_knowledge(self) -> list[Knowledge]:
        rows = self._run("SELECT id, topic, content, source, created_at FROM knowledge ORDER BY id")
        return [Knowledge(**dict(row)) for row in rows]

    def search_knowledge(self, query: str, limit: int = 3) -> list[Knowledge]:
        return _rank(query, self.list_knowledge(), lambda k: f"{k.topic} {k.content}", limit)

    def delete_knowledge(self, knowledge_id: int) -> bool:
        self._run("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
        return self._last_rowcount > 0

    def count_knowledge(self) -> int:
        return self._run("SELECT COUNT(*) AS n FROM knowledge")[0]["n"]

    # --- lessons (how NOVA should behave) -----------------------------------

    def add_lesson(self, content: str) -> Memory:
        content = content.strip()
        if not content:
            raise ValueError("A lesson cannot be empty")
        created_at = _now()
        self._run("INSERT INTO lessons (content, created_at) VALUES (?, ?)", (content, created_at))
        log.info("Saved lesson #%s", self._last_id)
        return Memory(self._last_id, content, created_at)

    def list_lessons(self) -> list[Memory]:
        rows = self._run("SELECT id, content, created_at FROM lessons ORDER BY id")
        return [Memory(**dict(row)) for row in rows]

    def delete_lesson(self, lesson_id: int) -> bool:
        self._run("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        return self._last_rowcount > 0
