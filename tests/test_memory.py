"""Tests for the SQLite long-term memory and the short-term conversation memory."""

import sqlite3
import unittest
from pathlib import Path

from memory import ConversationMemory, MemoryStore, MemoryStoreError
from tests.helpers import temp_dir


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.db_path = Path(self.tmp.name) / "sub" / "nova.db"
        self.store = MemoryStore(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_database_is_created(self):
        self.assertTrue(self.db_path.exists())
        with sqlite3.connect(self.db_path) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)")]
        self.assertEqual(columns, ["id", "content", "created_at"])

    def test_save_memory(self):
        memory = self.store.add("  My store is called EXORASTORE.  ")
        self.assertEqual(memory.content, "My store is called EXORASTORE.")
        self.assertIsInstance(memory.id, int)
        self.assertTrue(memory.created_at)

    def test_empty_memory_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add("   ")

    def test_retrieve_memories(self):
        self.store.add("first")
        self.store.add("second")
        self.store.add("third")
        self.assertEqual([m.content for m in self.store.list()], ["first", "second", "third"])
        self.assertEqual([m.content for m in self.store.list(limit=2)], ["second", "third"])
        self.assertEqual(self.store.count(), 3)

    def test_memories_survive_reopening(self):
        self.store.add("My store is called EXORASTORE.")
        reopened = MemoryStore(self.db_path)  # simulates restarting NOVA
        self.assertEqual(reopened.list()[0].content, "My store is called EXORASTORE.")

    def test_delete_memory(self):
        keep = self.store.add("keep me")
        remove = self.store.add("delete me")
        self.assertTrue(self.store.delete(remove.id))
        self.assertFalse(self.store.delete(remove.id))
        self.assertIsNone(self.store.get(remove.id))
        self.assertEqual([m.id for m in self.store.list()], [keep.id])

    def test_clear(self):
        self.store.add("a")
        self.store.add("b")
        self.assertEqual(self.store.clear(), 2)
        self.assertEqual(self.store.count(), 0)

    def test_corrupted_database_gives_friendly_error(self):
        bad = Path(self.tmp.name) / "corrupt.db"
        bad.write_bytes(b"this is not a sqlite database" * 100)
        with self.assertRaises(MemoryStoreError):
            MemoryStore(bad)


class ConversationMemoryTests(unittest.TestCase):
    def test_keeps_order_and_limit(self):
        convo = ConversationMemory(max_messages=3)
        for i in range(5):
            convo.add("user", f"message {i}")
        self.assertEqual([m["content"] for m in convo.get_messages()],
                         ["message 2", "message 3", "message 4"])

    def test_get_messages_returns_copy(self):
        convo = ConversationMemory()
        convo.add("user", "hi")
        convo.get_messages()[0]["content"] = "changed"
        self.assertEqual(convo.get_messages()[0]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
