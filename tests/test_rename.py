"""Tests for the rename to JARVIS: upgrading old installs and renaming the assistant."""

import json
import unittest
from pathlib import Path
from unittest import mock

from agent import Agent
from config import load_config
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir
from utils.migrate import migrate_env_file, migrate_files

OLD_ENV = """# Copy this file to .env
NOVA_NAME=NOVA
NOVA_VERSION=1.0
NOVA_BRAIN=ollama
OLLAMA_MODEL=qwen2.5:7b
NOVA_WORKSPACE=~/NOVA_Workspace
NOVA_PERSONALITY_FILE=personality/nova.txt
EMAIL_ADDRESS=me@example.com
"""


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"

    def test_old_setting_names_still_work(self):
        self.env.write_text("NOVA_NAME=Friday\nNOVA_MAX_HISTORY=12\n")
        config = load_config(env_file=self.env, environ={"NOVA_LOG_LEVEL": "DEBUG"})
        self.assertEqual((config.name, config.max_history, config.log_level), ("Friday", 12, "DEBUG"))

    def test_env_file_is_upgraded_once(self):
        self.env.write_text(OLD_ENV)
        notes = migrate_env_file(self.env)
        self.assertTrue(notes)
        text = self.env.read_text()
        for line in ["JARVIS_NAME=JARVIS", "JARVIS_WORKSPACE=~/JARVIS_Workspace",
                     "JARVIS_PERSONALITY_FILE=personality/jarvis.txt", "OLLAMA_MODEL=qwen2.5:7b",
                     "EMAIL_ADDRESS=me@example.com", "# Copy this file to .env"]:
            self.assertIn(line, text)
        self.assertNotIn("NOVA_", text)
        self.assertEqual(migrate_env_file(self.env), [])  # second run: nothing to do

        config = load_config(env_file=self.env, environ={})
        self.assertEqual(config.name, "JARVIS")
        self.assertTrue(config.personality_file.exists())  # the renamed personality file

    def test_custom_name_is_kept(self):
        self.env.write_text("NOVA_NAME=Friday\n")
        migrate_env_file(self.env)
        self.assertEqual(self.env.read_text(), "JARVIS_NAME=Friday\n")

    def test_memories_move_to_new_database(self):
        config = make_config(self.tmp.name)
        config.data_dir.mkdir(parents=True)
        MemoryStore(config.data_dir / "nova.db").add("My store is called EXORASTORE.")
        notes = migrate_files(config)
        self.assertTrue(any("memories" in n for n in notes))
        self.assertFalse((config.data_dir / "nova.db").exists())
        self.assertEqual(MemoryStore(config.db_path).list()[0].content, "My store is called EXORASTORE.")

    def test_existing_new_database_is_never_overwritten(self):
        config = make_config(self.tmp.name)
        config.data_dir.mkdir(parents=True)
        MemoryStore(config.data_dir / "nova.db").add("old")
        MemoryStore(config.db_path).add("new")
        migrate_files(config)
        self.assertEqual([m.content for m in MemoryStore(config.db_path).list()], ["new"])

    def test_workspace_folder_is_renamed_with_its_files(self):
        home = self.root / "home"
        (home / "NOVA_Workspace" / "python_practice").mkdir(parents=True)
        (home / "NOVA_Workspace" / "python_practice" / "hello.py").write_text("print('hi')")
        config = make_config(self.tmp.name)
        config.workspace = home / "JARVIS_Workspace"
        with mock.patch.object(Path, "home", return_value=home):
            notes = migrate_files(config)
        self.assertTrue(any("workspace" in n for n in notes))
        self.assertEqual((home / "JARVIS_Workspace" / "python_practice" / "hello.py").read_text(), "print('hi')")
        self.assertFalse((home / "NOVA_Workspace").exists())


class RenameYourselfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.config = make_config(self.tmp.name)
        self.config.env_file = Path(self.tmp.name) / ".env"
        self.config.env_file.write_text("JARVIS_NAME=JARVIS\nOLLAMA_MODEL=qwen2.5:7b\n")

    def test_set_my_name_via_the_agent(self):
        from brain.base import BrainReply, ToolCall
        brain = ScriptedBrain(BrainReply("", [ToolCall("set_my_name", {"new_name": "FRIDAY"})]),
                              BrainReply("I'm FRIDAY now."), BrainReply("Hello."))
        agent = Agent(self.config, brain, MemoryStore(self.config.db_path), "You are JARVIS.")
        reply = agent.handle("from now on your name is FRIDAY")
        self.assertIn("FRIDAY", reply.pending.description)  # renaming always asks first
        agent.resolve_pending(True)
        self.assertEqual(self.config.name, "FRIDAY")
        self.assertIn("JARVIS_NAME=FRIDAY", self.config.env_file.read_text())
        self.assertIn("OLLAMA_MODEL=qwen2.5:7b", self.config.env_file.read_text())  # nothing else touched
        agent.handle("hi")
        self.assertIn("You are FRIDAY", brain.calls[-1][0][0]["content"])  # personality uses the new name
        self.assertIn("Quit FRIDAY", agent.handle("/help").text)

    def test_invalid_names_are_refused(self):
        from tools import ToolContext, create_registry
        registry = create_registry(ToolContext(self.config, MemoryStore(self.config.db_path), None))
        for bad in ["", "x" * 40, "rm -rf /;", "\nJARVIS_BRAIN=evil"]:
            with self.subTest(bad=bad):
                self.assertTrue(registry.execute("set_my_name", {"new_name": bad}).startswith("Error"))
        self.assertEqual(self.config.env_file.read_text(), "JARVIS_NAME=JARVIS\nOLLAMA_MODEL=qwen2.5:7b\n")

    def test_web_page_follows_the_new_name(self):
        import http.client
        import threading
        from ui.server import JarvisWebServer
        self.config.web_port = 0
        agent = Agent(self.config, ScriptedBrain(), MemoryStore(self.config.db_path), "You are JARVIS.")
        server = JarvisWebServer(agent, self.config)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.config.name = "FRIDAY"

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
            conn.request("GET", path, headers={"X-Jarvis-Token": server.token})
            body = conn.getresponse().read().decode()
            conn.close()
            return body

        self.assertIn("<title>FRIDAY - Personal AI</title>", get("/"))
        self.assertEqual(json.loads(get("/api/status"))["name"], "FRIDAY")


if __name__ == "__main__":
    unittest.main()
