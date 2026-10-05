"""Tests for the 'smarter assistant' changes: file search, own-folder access, forget,
tool selection, the honesty guard and clearer memories."""

import json
import unittest
from pathlib import Path
from unittest import mock

from agent import Agent
from brain.base import BrainReply, ToolCall
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir
from tools import ToolContext, create_registry
from tools.files import known_folder, resolve_path
from tools.routing import select_tool_names


class HomeTestCase(unittest.TestCase):
    """A fake home folder with Documents, Downloads, AppData... for each test."""

    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        files = {
            "Documents/Budget 2025.xlsx": "x" * 3000,
            "Documents/work/notes.txt": "meeting at 5",
            "Downloads/budget_old.pdf": "pdf",
            "Downloads/setup.exe": "exe",
            "Pictures/holiday.jpg": "jpg",
            "AppData/Roaming/budget_secret.txt": "secret",
            ".ssh/id_rsa": "key",
        }
        for relative, content in files.items():
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        patch = mock.patch.object(Path, "home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.config = make_config(self.tmp.name)
        self.store = MemoryStore(self.config.db_path)
        self.registry = create_registry(ToolContext(self.config, self.store, ScriptedBrain()))

    def run_tool(self, tool_name, /, **arguments):
        return self.registry.execute(tool_name, arguments)


class FindFilesTests(HomeTestCase):
    def test_find_in_documents(self):
        result = self.run_tool("find_files", name="budget", folder="Documents")
        self.assertIn(str(self.home / "Documents" / "Budget 2025.xlsx"), result)
        self.assertNotIn("budget_old.pdf", result)
        self.assertIn("ask if they want it opened", result)

    def test_find_everywhere_skips_private_folders(self):
        result = self.run_tool("find_files", name="budget")
        self.assertIn("Budget 2025.xlsx", result)
        self.assertIn("budget_old.pdf", result)
        self.assertNotIn("budget_secret", result)  # AppData is never searched
        self.assertFalse(self.registry.needs_confirmation("find_files", {"name": "budget"}))

    def test_partial_and_multi_word_names(self):
        self.assertIn("Budget 2025.xlsx", self.run_tool("find_files", name="budget 2025"))
        self.assertIn("notes.txt", self.run_tool("find_files", name="NOTES"))
        self.assertIn("work", self.run_tool("find_files", name="work", folder="documents"))

    def test_nothing_found_and_bad_folder(self):
        self.assertIn("No file or folder matching", self.run_tool("find_files", name="passport"))
        self.assertIn("doesn't exist", self.run_tool("find_files", name="x", folder="C:/no/such/folder"))


class OwnFoldersTests(HomeTestCase):
    def test_known_folder_names(self):
        self.assertEqual(resolve_path(self.config, "Documents/work/notes.txt"),
                         (self.home / "Documents" / "work" / "notes.txt").resolve())
        self.assertEqual(resolve_path(self.config, "downloads"), (self.home / "Downloads").resolve())
        self.assertEqual(resolve_path(self.config, "projects/a.py"),
                         (self.config.workspace / "projects" / "a.py").resolve())

    def test_onedrive_documents(self):
        (self.home / "Documents").rename(self.home / "Docs_old")
        (self.home / "OneDrive" / "Documents").mkdir(parents=True)
        self.assertEqual(known_folder("documents"), self.home / "OneDrive" / "Documents")

    def test_reading_own_files_needs_no_approval_but_private_ones_do(self):
        needs = self.registry.needs_confirmation
        self.assertFalse(needs("list_directory", {"path": "Documents"}))
        self.assertFalse(needs("read_file", {"path": "Documents/work/notes.txt"}))
        self.assertIn("meeting at 5", self.run_tool("read_file", path="Documents/work/notes.txt"))
        self.assertTrue(needs("read_file", {"path": str(self.home / ".ssh" / "id_rsa")}))
        self.assertTrue(needs("read_file", {"path": str(self.home / "AppData" / "Roaming" / "budget_secret.txt")}))
        self.assertTrue(needs("read_file", {"path": str(self.config.env_file)}))
        self.assertTrue(needs("read_file", {"path": "/etc/hosts"}))  # outside the user's home

    def test_changing_or_running_things_still_asks(self):
        needs = self.registry.needs_confirmation
        self.assertTrue(needs("write_file", {"path": "Documents/new.txt", "content": "x"}))
        self.assertFalse(needs("write_file", {"path": "projects/new.txt", "content": "x"}))
        self.assertFalse(needs("open_path", {"target": str(self.home / "Documents" / "Budget 2025.xlsx")}))
        self.assertTrue(needs("open_path", {"target": str(self.home / "Downloads" / "setup.exe")}))


class ForgetTests(HomeTestCase):
    def test_forget_one_or_all(self):
        first = self.store.add("The user's name is Jishnu Raj.")
        self.store.add("The user likes tea.")
        self.assertTrue(self.registry.needs_confirmation("forget", {"what": "all_memories"}))
        description = self.registry.describe("forget", {"what": "memory", "id": first.id})
        self.assertIn("Jishnu Raj", description)  # the approval shows exactly what will be forgotten
        self.assertIn("Deleted memory", self.run_tool("forget", what="memory", id=first.id))
        self.assertEqual([m.content for m in self.store.list()], ["The user likes tea."])
        self.assertIn("Deleted all 1", self.run_tool("forget", what="all_memories"))
        self.assertEqual(self.store.count(), 0)

    def test_forget_lessons_and_knowledge(self):
        lesson = self.store.add_lesson("Answer briefly.")
        knowledge = self.store.add_knowledge("tea", "Tea has caffeine.")
        self.run_tool("forget", what="lesson", id=lesson.id)
        self.run_tool("forget", what="knowledge", id=knowledge.id)
        self.assertEqual((self.store.list_lessons(), self.store.list_knowledge()), ([], []))
        self.assertIn("Error", self.run_tool("forget", what="memory", id=999))
        self.assertIn("Error", self.run_tool("forget", what="memory"))


class ToolSelectionTests(unittest.TestCase):
    ALL = ["get_datetime", "web_search", "remember", "forget", "find_files", "open_path", "run_command",
           "send_email", "read_emails", "create_skill", "list_skills", "modify_jarvis_source",
           "set_my_name", "learn_topic", "system_info", "my_custom_skill"]

    def pick(self, text, recent=(), skills=("my_custom_skill",)):
        return set(select_tool_names(self.ALL, text, set(recent), set(skills)))

    def test_everyday_message_gets_a_short_list(self):
        tools = self.pick("hello, how are you?")
        self.assertIn("find_files", tools)
        self.assertIn("my_custom_skill", tools)  # the user's own skills are always available
        for hidden in ("send_email", "create_skill", "modify_jarvis_source", "set_my_name", "learn_topic"):
            self.assertNotIn(hidden, tools)

    def test_groups_switch_on_by_topic(self):
        self.assertIn("send_email", self.pick("send an email to my boss"))
        self.assertIn("create_skill", self.pick("make yourself a skill that converts csv"))
        self.assertIn("modify_jarvis_source", self.pick("improve yourself"))
        self.assertIn("set_my_name", self.pick("from now on your name is FRIDAY"))
        self.assertIn("system_info", self.pick("how much disk space is left?"))
        self.assertIn("learn_topic", self.pick("learn about black holes"))

    def test_telling_my_name_does_not_offer_renaming_jarvis(self):
        self.assertNotIn("set_my_name", self.pick("my name is Jishnu"))

    def test_recently_used_groups_stay_available(self):
        self.assertIn("send_email", self.pick("yes, send it", recent={"read_emails"}))

    def test_agent_offers_selected_tools(self):
        with temp_dir() as tmp:
            config = make_config(tmp)
            brain = ScriptedBrain()
            agent = Agent(config, brain, MemoryStore(config.db_path), "You are JARVIS.")
            agent.handle("hi there")
            offered = {t["function"]["name"] for t in brain.calls[-1][1]}
            self.assertIn("find_files", offered)
            self.assertNotIn("send_email", offered)
            self.assertLess(len(offered), len(agent.tools))


class HonestyAndMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.config = make_config(self.tmp.name)
        self.store = MemoryStore(self.config.db_path)

    def test_denied_action_cannot_be_reported_as_done(self):
        """Replay of a real session: denied action, then the model claimed it was done."""
        brain = ScriptedBrain(BrainReply("", [ToolCall("remove_skill", {"name": "who_knows"})]),
                              BrainReply("All facts about your identity have been removed."))
        agent = Agent(self.config, brain, self.store, "You are JARVIS.")
        agent.handle("delete every fact you know")
        reply = agent.resolve_pending(False)
        self.assertIn("⚠ Not done: remove_skill (you denied it)", reply.text)

    def test_failed_then_fixed_is_not_flagged(self):
        brain = ScriptedBrain(BrainReply("", [ToolCall("read_file", {"path": "missing.txt"})]),
                              BrainReply("", [ToolCall("get_datetime", {})]),
                              BrainReply("", [ToolCall("read_file", {"path": "."})]),
                              BrainReply("Here it is."))
        reply = Agent(self.config, brain, self.store, "You are JARVIS.").handle("read it")
        self.assertIn("⚠ Not done: read_file (it failed)", reply.text)  # the retry also failed
        brain = ScriptedBrain(BrainReply("", [ToolCall("read_file", {"path": "missing.txt"})]),
                              BrainReply("", [ToolCall("get_datetime", {})]), BrainReply("Done."))
        reply = Agent(self.config, brain, self.store, "You are JARVIS.").handle("x")
        self.assertIn("read_file (it failed)", reply.text)
        brain = ScriptedBrain(BrainReply("", [ToolCall("get_datetime", {"x": 1})]), BrainReply("Monday."))
        reply = Agent(self.config, brain, self.store, "You are JARVIS.").handle("day?")
        self.assertEqual(reply.text, "Monday.")

    def test_memories_are_explained_as_facts_about_the_user(self):
        self.store.add("I am Jishnu Raj and I created you. Always call me Boss.")
        brain = ScriptedBrain()
        Agent(self.config, brain, self.store, "You are JARVIS.").handle("who am I?")
        system = brain.calls[-1][0][0]["content"]
        self.assertIn("mean the USER, not you", system)
        self.assertIn("who am I?", system)
        self.assertIn("I am Jishnu Raj", system)

    def test_temperature_setting(self):
        from brain.local import LocalBrain
        from config import ConfigError, load_config
        env = Path(self.tmp.name) / "none.env"
        self.assertEqual(load_config(env_file=env, environ={}).ollama_temperature, 0.3)
        with self.assertRaises(ConfigError):
            load_config(env_file=env, environ={"OLLAMA_TEMPERATURE": "hot"})
        brain = LocalBrain("http://127.0.0.1:9", "m", temperature=0.2)
        with mock.patch.object(brain, "_request", return_value={"message": {"content": "ok"}}) as request:
            brain.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(request.call_args[0][1]["options"]["temperature"], 0.2)


class FindAndOpenFlowTests(HomeTestCase):
    def test_find_then_offer_then_open(self):
        found = str(self.home / "Documents" / "Budget 2025.xlsx")
        brain = ScriptedBrain(
            BrainReply("", [ToolCall("find_files", {"name": "budget", "folder": "Documents"})]),
            BrainReply("I found Budget 2025.xlsx in your Documents folder. Want me to open it?"),
            BrainReply("", [ToolCall("open_path", {"target": found})]),
            BrainReply("Done - it's open."),
        )
        agent = Agent(self.config, brain, self.store, "You are JARVIS.")
        reply = agent.handle("check my Documents folder for a file named budget")
        self.assertIsNone(reply.pending)  # looking needs no approval
        self.assertIn("Want me to open it?", reply.text)
        with mock.patch("tools.apps.open_with_default_program") as opener:
            reply = agent.handle("yes")
        self.assertIsNone(reply.pending)  # opening your own document needs no approval either
        opener.assert_called_once_with(str(Path(found).resolve()))
        self.assertEqual(reply.text, "Done - it's open.")


if __name__ == "__main__":
    unittest.main()
