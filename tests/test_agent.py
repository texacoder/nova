"""Tests for the agent: commands, conversation context and error handling."""

import unittest

from agent import Agent
from brain.base import Brain, BrainStatus, BrainUnavailableError
from brain.mock import MockBrain
from memory import MemoryStore
from tests.helpers import make_config, temp_dir


class RecordingBrain(Brain):
    """Remembers what it was sent so tests can inspect the context."""
    name = "recording"

    def __init__(self):
        self.calls = []

    def generate_response(self, messages):
        self.calls.append(messages)
        return "ok"

    def health_check(self):
        return BrainStatus(True, "fine")


class BrokenBrain(Brain):
    name = "broken"

    def generate_response(self, messages):
        raise BrainUnavailableError("Cannot reach Ollama.")

    def health_check(self):
        return BrainStatus(False, "Cannot reach Ollama.")


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config = make_config(self.tmp.name)
        self.brain = RecordingBrain()
        self.agent = self._new_agent(self.brain)

    def tearDown(self):
        self.tmp.cleanup()

    def _new_agent(self, brain):
        return Agent(self.config, brain, MemoryStore(self.config.db_path), "You are NOVA.")

    def test_empty_input_is_ignored(self):
        self.assertEqual(self.agent.handle("   "), "")
        self.assertEqual(self.brain.calls, [])

    def test_conversation_context_is_sent_to_brain(self):
        self.agent.handle("My project is called NOVA.")
        self.agent.handle("What is my project called?")
        messages = self.brain.calls[-1]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(
            [(m["role"], m["content"]) for m in messages[1:]],
            [("user", "My project is called NOVA."), ("assistant", "ok"),
             ("user", "What is my project called?")],
        )

    def test_conversation_context_with_mock_brain(self):
        agent = self._new_agent(MockBrain())
        agent.handle("My project is called NOVA.")
        self.assertIn("NOVA", agent.handle("What is my project called?"))

    def test_memories_are_in_system_prompt(self):
        self.agent.handle("/remember My store is called EXORASTORE.")
        self.agent.handle("What is my store called?")
        self.assertIn("EXORASTORE", self.brain.calls[-1][0]["content"])

    def test_memory_survives_restart(self):
        self.assertIn("I'll remember that", self.agent.handle("/remember My store is called EXORASTORE."))
        restarted = self._new_agent(MockBrain())  # new agent + new DB connection
        self.assertIn("EXORASTORE", restarted.handle("What is my store called?"))

    def test_memory_commands(self):
        self.agent.handle("/remember fact one")
        self.agent.handle("/remember fact two")
        listing = self.agent.handle("/memories")
        self.assertIn("[1] fact one", listing)
        self.assertIn("[2] fact two", listing)
        self.assertIn("Forgotten memory #1", self.agent.handle("/forget 1"))
        self.assertIn("no memory #1", self.agent.handle("/forget 1"))
        self.assertIn("Usage", self.agent.handle("/forget abc"))
        self.assertIn("Usage", self.agent.handle("/remember"))

    def test_clear_memory_needs_confirmation(self):
        self.agent.handle("/remember something")
        self.assertIn("Type 'yes'", self.agent.handle("/clear_memory"))
        self.assertIn("Cancelled", self.agent.handle("no"))
        self.assertIn("[1] something", self.agent.handle("/memories"))
        self.agent.handle("/clear_memory")
        self.assertIn("cleared", self.agent.handle("yes"))
        self.assertIn("no saved memories", self.agent.handle("/memories"))

    def test_unknown_command(self):
        self.assertIn("Unknown command", self.agent.handle("/dance"))

    def test_status_and_exit(self):
        self.assertIn("Brain:", self.agent.handle("/status"))
        self.assertFalse(self.agent.should_exit)
        self.agent.handle("/exit")
        self.assertTrue(self.agent.should_exit)

    def test_unavailable_brain_is_handled(self):
        agent = self._new_agent(BrokenBrain())
        reply = agent.handle("hello")
        self.assertIn("unavailable", reply)
        self.assertEqual(len(agent.conversation), 0)  # unanswered message not kept
        self.assertIn("I'll remember", agent.handle("/remember still works"))


if __name__ == "__main__":
    unittest.main()
