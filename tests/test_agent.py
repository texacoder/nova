"""Tests for the agent: commands, context, the tool loop and approvals."""

import unittest
from pathlib import Path
from unittest import mock

from agent import Agent
from brain.base import Brain, BrainReply, BrainStatus, BrainUnavailableError, ToolCall
from brain.mock import MockBrain
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir


class BrokenBrain(Brain):
    name = "broken"

    def chat(self, messages, tools=None):
        raise BrainUnavailableError("Cannot reach Ollama.")

    def health_check(self):
        return BrainStatus(False, "Cannot reach Ollama.")


def call(name, **arguments):
    return BrainReply("", [ToolCall(name, arguments)])


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config = make_config(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def new_agent(self, brain, config=None):
        config = config or self.config
        return Agent(config, brain, MemoryStore(config.db_path), "You are NOVA.")


class ConversationTests(AgentTestCase):
    def test_empty_input_is_ignored(self):
        brain = ScriptedBrain()
        self.assertEqual(self.new_agent(brain).handle("   ").text, "")
        self.assertEqual(brain.calls, [])

    def test_conversation_context_is_sent_to_brain(self):
        brain = ScriptedBrain(BrainReply("ok"), BrainReply("NOVA"))
        agent = self.new_agent(brain)
        agent.handle("My project is called NOVA.")
        agent.handle("What is my project called?")
        messages, tools = brain.calls[-1]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(
            [(m["role"], m["content"]) for m in messages[1:]],
            [("user", "My project is called NOVA."), ("assistant", "ok"),
             ("user", "What is my project called?")],
        )
        self.assertTrue(tools)  # the tool list is offered to the brain

    def test_conversation_context_with_mock_brain(self):
        agent = self.new_agent(MockBrain())
        agent.handle("My project is called NOVA.")
        self.assertIn("NOVA", agent.handle("What is my project called?").text)

    def test_memories_and_knowledge_in_system_prompt(self):
        brain = ScriptedBrain()
        agent = self.new_agent(brain)
        agent.handle("/remember My store is called EXORASTORE.")
        agent.memory_store.add_knowledge("solar panels", "Panels convert sunlight to power.", "https://x.org")
        agent.handle("How do solar panels work?")
        system = brain.calls[-1][0][0]["content"]
        self.assertIn("EXORASTORE", system)
        self.assertIn("Panels convert sunlight", system)
        self.assertIn("workspace", system)

    def test_memory_survives_restart(self):
        agent = self.new_agent(MockBrain())
        self.assertIn("I'll remember that", agent.handle("/remember My store is called EXORASTORE.").text)
        restarted = self.new_agent(MockBrain())  # new agent + new DB connection
        self.assertIn("EXORASTORE", restarted.handle("What is my store called?").text)

    def test_unavailable_brain_is_handled(self):
        agent = self.new_agent(BrokenBrain())
        reply = agent.handle("hello")
        self.assertIn("unavailable", reply.text)
        self.assertEqual(len(agent.conversation), 0)  # unanswered message not kept
        self.assertIn("I'll remember", agent.handle("/remember still works").text)

    def test_brain_without_tool_support_gets_no_tools(self):
        brain = ScriptedBrain()
        brain.supports_tools = False
        self.new_agent(brain).handle("hi")
        self.assertIsNone(brain.calls[0][1])


class CommandTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.agent = self.new_agent(ScriptedBrain())

    def test_memory_commands(self):
        self.agent.handle("/remember fact one")
        self.agent.handle("/remember fact two")
        listing = self.agent.handle("/memories").text
        self.assertIn("[1] fact one", listing)
        self.assertIn("[2] fact two", listing)
        self.assertIn("Forgotten memory #1", self.agent.handle("/forget 1").text)
        self.assertIn("no memory #1", self.agent.handle("/forget 1").text)
        self.assertIn("Usage", self.agent.handle("/forget abc").text)
        self.assertIn("Usage", self.agent.handle("/remember").text)

    def test_knowledge_commands(self):
        entry = self.agent.memory_store.add_knowledge("tea", "Tea has caffeine.")
        self.assertIn("tea", self.agent.handle("/knowledge").text)
        self.assertIn("Forgotten knowledge", self.agent.handle(f"/forget K{entry.id}").text)
        self.assertIn("haven't learned", self.agent.handle("/knowledge").text)

    def test_learn_command(self):
        fake_results = [{"title": "Tea", "url": "https://tea.example", "snippet": "about tea"}]
        with mock.patch("tools.knowledge.search_web", return_value=fake_results), \
             mock.patch("tools.knowledge.fetch_page", return_value=("Tea", "Tea is a drink. " * 30)):
            reply = self.agent.handle("/learn tea")
        self.assertIn("Learned about 'tea'", reply.text)
        self.assertEqual(self.agent.memory_store.list_knowledge()[0].source, "https://tea.example")

    def test_clear_memory_needs_confirmation(self):
        self.agent.handle("/remember something")
        self.assertIn("Type 'yes'", self.agent.handle("/clear_memory").text)
        self.assertIn("Cancelled", self.agent.handle("no").text)
        self.assertIn("[1] something", self.agent.handle("/memories").text)
        self.agent.handle("/clear_memory")
        self.assertIn("cleared", self.agent.handle("yes").text)
        self.assertIn("no saved memories", self.agent.handle("/memories").text)

    def test_other_commands(self):
        self.assertIn("Unknown command", self.agent.handle("/dance").text)
        self.assertIn("web_search", self.agent.handle("/tools").text)
        self.assertIn("Brain", self.agent.handle("/status").text)
        self.agent.handle("hi")
        self.assertIn("fresh conversation", self.agent.handle("/new").text)
        self.assertEqual(len(self.agent.conversation), 0)
        reply = self.agent.handle("/exit")
        self.assertTrue(reply.exit)
        self.assertTrue(self.agent.should_exit)


class ToolLoopTests(AgentTestCase):
    def test_tool_result_goes_back_to_brain(self):
        brain = ScriptedBrain(call("get_datetime"), BrainReply("It is Friday."))
        agent = self.new_agent(brain)
        reply = agent.handle("what day is it?")
        self.assertEqual(reply.text, "It is Friday.")
        self.assertEqual([s["tool"] for s in reply.steps], ["get_datetime"])
        self.assertEqual(reply.steps[0]["status"], "ok")
        second_call_messages = brain.calls[1][0]
        self.assertEqual([m["role"] for m in second_call_messages[1:]], ["user", "assistant", "tool"])
        self.assertEqual(second_call_messages[-1]["tool_name"], "get_datetime")

    def test_write_in_workspace_needs_no_approval(self):
        brain = ScriptedBrain(call("write_file", path="code/hello.py", content="print('hi')"), BrainReply("Done."))
        reply = self.new_agent(brain).handle("write hello world")
        self.assertIsNone(reply.pending)
        self.assertEqual((self.config.workspace / "code" / "hello.py").read_text(), "print('hi')")

    def test_risky_action_waits_for_approval_and_can_be_declined(self):
        outside = Path(self.tmp.name) / "outside.txt"
        brain = ScriptedBrain(call("write_file", path=str(outside), content="x"), BrainReply("Okay, I didn't."))
        agent = self.new_agent(brain)
        reply = agent.handle("write outside")
        self.assertIsNotNone(reply.pending)
        self.assertIn(str(outside), reply.pending.description)
        self.assertEqual(len(brain.calls), 1)  # paused: brain not called again yet

        reply = agent.resolve_pending(False)
        self.assertFalse(outside.exists())
        self.assertEqual(reply.text, "Okay, I didn't.")
        self.assertEqual(reply.steps[0]["status"], "declined")
        self.assertIn("declined", brain.calls[-1][0][-1]["content"])

    def test_approval_by_typing_yes(self):
        outside = Path(self.tmp.name) / "outside.txt"
        brain = ScriptedBrain(call("write_file", path=str(outside), content="x"), BrainReply("Written."))
        agent = self.new_agent(brain)
        agent.handle("write outside")
        self.assertIn("yes or no", agent.handle("something else").text)
        reply = agent.handle("yes")
        self.assertEqual(outside.read_text(), "x")
        self.assertEqual(reply.text, "Written.")

    def test_auto_approve_skips_confirmation(self):
        config = make_config(self.tmp.name, NOVA_AUTO_APPROVE="run_command")
        brain = ScriptedBrain(call("run_command", command="echo nova-test"), BrainReply("ran"))
        reply = self.new_agent(brain, config).handle("run it")
        self.assertIsNone(reply.pending)
        self.assertIn("nova-test", reply.steps[0]["result"])

    def test_unknown_tool_is_reported_not_crashed(self):
        brain = ScriptedBrain(call("format_disk"), BrainReply("Sorry."))
        reply = self.new_agent(brain).handle("do it")
        self.assertEqual(reply.steps[0]["status"], "error")
        self.assertIn("no tool called", reply.steps[0]["result"])

    def test_step_limit(self):
        brain = ScriptedBrain(*[call("get_datetime") for _ in range(20)])
        config = make_config(self.tmp.name, NOVA_MAX_TOOL_STEPS="3")
        reply = self.new_agent(brain, config).handle("loop forever")
        self.assertIn("stopped after 3 steps", reply.text)
        self.assertEqual(len(brain.calls), 3)

    def test_mock_brain_end_to_end(self):
        agent = self.new_agent(MockBrain())
        reply = agent.handle("list files")
        self.assertEqual(reply.steps[0]["tool"], "list_directory")
        self.assertIn("Contents of", reply.text)


if __name__ == "__main__":
    unittest.main()
