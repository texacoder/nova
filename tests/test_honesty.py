"""Tests for answering memory questions directly and never showing made-up results."""

import unittest

from agent import MEMORY_QUESTION, Agent
from brain.base import BrainReply, ToolCall
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir


class HonestyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.config = make_config(self.tmp.name)
        self.store = MemoryStore(self.config.db_path)

    def agent(self, *replies):
        self.brain = ScriptedBrain(*replies)
        return Agent(self.config, self.brain, self.store, "You are JARVIS.")


class MemoryQuestionTests(HonestyTestCase):
    def test_recognised_questions(self):
        for text in ["show your saved memories", "your saved memories", "show me all your memories",
                     "What do you remember about me?", "list my memories", "what are your memories",
                     "your memories", "display memories"]:
            with self.subTest(text=text):
                self.assertTrue(MEMORY_QUESTION.match(text))
        for text in ["remember that I like tea", "forget my memories", "delete your memories",
                     "how does memory work in computers?", "my memory is bad", "show your skills"]:
            with self.subTest(text=text):
                self.assertFalse(MEMORY_QUESTION.match(text))

    def test_answered_from_real_memory_without_the_model(self):
        self.store.add("The user's name is Jishnu Raj.")
        self.store.add_lesson("Call the user Boss.")
        self.store.add_knowledge("solar panels", "Panels turn light into power.")
        agent = self.agent()
        reply = agent.handle("your saved memories")
        self.assertEqual(self.brain.calls, [])  # no model, so nothing can be made up
        self.assertIn("[1] The user's name is Jishnu Raj.", reply.text)
        self.assertIn("Call the user Boss.", reply.text)
        self.assertIn("solar panels", reply.text)
        self.assertEqual(reply.steps, [])
        # The answer is part of the conversation, so follow-up questions make sense.
        self.assertEqual(agent.conversation.get_messages()[-1]["content"], reply.text)

    def test_no_memories_yet(self):
        self.assertIn("don't have any saved memories", self.agent().handle("show your memories").text)

    def test_list_memories_tool(self):
        from tools import ToolContext, create_registry
        self.store.add("The user likes tea.")
        registry = create_registry(ToolContext(self.config, self.store, None))
        self.assertIn("[1] The user likes tea.", registry.execute("list_memories", {}))
        self.assertFalse(registry.needs_confirmation("list_memories", {}))


class MadeUpResultsTests(HonestyTestCase):
    def test_replay_of_invented_files(self):
        """Real session: find_files failed twice, the model said it found 12345.txt and 67890.md."""
        agent = self.agent(
            BrainReply("", [ToolCall("find_files", {"folder": "saved_memories"})]),
            BrainReply("", [ToolCall("find_files", {"folder": "saved_memories"})]),
            BrainReply("I found these saved memories in your workspace:\n- saved_memories/12345.txt\n"
                       "- saved_memories/67890.md\nDo you need more information on any of these?"),
        )
        reply = agent.handle("find my saved memories files")
        self.assertNotIn("12345", reply.text)
        self.assertIn("that didn't work", reply.text)
        self.assertIn("find_files failed", reply.text)
        self.assertIn("⚠ Not done: find_files (it failed)", reply.text)
        self.assertNotIn("12345", str(agent.conversation.get_messages()))  # the lie isn't remembered either

    def test_model_is_told_a_step_failed(self):
        agent = self.agent(BrainReply("", [ToolCall("read_file", {"path": "missing.txt"})]),
                           BrainReply("I couldn't read it."))
        reply = agent.handle("read missing.txt")
        tool_message = [m for m in self.brain.calls[-1][0] if m["role"] == "tool"][-1]["content"]
        self.assertIn("This step FAILED", tool_message)
        self.assertNotIn("This step FAILED", reply.steps[0]["result"])  # the user sees the plain error

    def test_honest_failure_replies_are_kept(self):
        agent = self.agent(BrainReply("", [ToolCall("read_file", {"path": "missing.txt"})]),
                           BrainReply("I couldn't find that file. Could you give me the exact name?"))
        reply = agent.handle("read missing.txt")
        self.assertTrue(reply.text.startswith("I couldn't find that file."))

    def test_success_after_partial_failure_is_kept(self):
        agent = self.agent(BrainReply("", [ToolCall("read_file", {"path": "missing.txt"})]),
                           BrainReply("", [ToolCall("get_datetime", {})]),
                           BrainReply("Here is today's date: Monday."))
        reply = agent.handle("x")
        self.assertTrue(reply.text.startswith("Here is today's date"))

    def test_denied_and_claimed_done(self):
        agent = self.agent(BrainReply("", [ToolCall("run_command", {"command": "echo hi"})]),
                           BrainReply("Done! The command ran successfully."))
        agent.handle("run echo hi")
        reply = agent.resolve_pending(False)
        self.assertIn("run_command was not done because you denied it", reply.text)
        self.assertNotIn("successfully", reply.text)


if __name__ == "__main__":
    unittest.main()
