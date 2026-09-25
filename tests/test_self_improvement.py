"""Tests for self-improvement: skills NOVA writes, and changes to its own code."""

import unittest
from pathlib import Path

from agent import Agent
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir
from tools import ToolContext, create_registry
from tools.self_modify import load_changes, rollback_last_change

GOOD_SKILL = '''from tools.base import Tool, ToolError


class Shout(Tool):
    name = "shout"
    description = "Return the text in capital letters."
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def run(self, text: str) -> str:
        return text.upper() + "!"
'''


def make_project(root: Path) -> None:
    """A tiny stand-in for NOVA's project: one module and one test."""
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_calc.py").write_text(
        "import unittest\nimport calc\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(calc.add(2, 2), 4)\n")
    (root / "personality").mkdir()
    (root / "personality" / "nova.txt").write_text("You are NOVA.\n")


class SelfImprovementTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        make_project(self.project)
        self.config = make_config(self.tmp.name)
        self.config.project_root = self.project
        self.store = MemoryStore(self.config.db_path)
        self.registry = self.new_registry()

    def new_registry(self):
        return create_registry(ToolContext(self.config, self.store, ScriptedBrain()))

    def run_tool(self, tool_name, /, **arguments):
        return self.registry.execute(tool_name, arguments)


class SkillTests(SelfImprovementTestCase):
    def test_create_skill_and_use_it_immediately(self):
        self.assertTrue(self.registry.needs_confirmation("create_skill", {"name": "shout", "code": GOOD_SKILL}))
        self.assertIn("class Shout", self.registry.describe("create_skill", {"name": "shout", "code": GOOD_SKILL}))
        result = self.run_tool("create_skill", name="shout", code=GOOD_SKILL)
        self.assertIn("installed", result)
        self.assertTrue((self.config.skills_dir / "shout.py").exists())
        self.assertEqual(self.run_tool("shout", text="hello"), "HELLO!")
        self.assertIn("shout", self.run_tool("list_skills"))

    def test_skills_load_again_after_restart(self):
        self.run_tool("create_skill", name="shout", code=GOOD_SKILL)
        restarted = self.new_registry()
        self.assertIn("shout", restarted.skill_names)
        self.assertEqual(restarted.execute("shout", {"text": "hi"}), "HI!")

    def test_fixing_a_skill_replaces_it(self):
        self.run_tool("create_skill", name="shout", code=GOOD_SKILL)
        self.run_tool("create_skill", name="shout", code=GOOD_SKILL.replace('+ "!"', '+ "!!!"'))
        self.assertEqual(self.run_tool("shout", text="a"), "A!!!")

    def test_bad_skills_are_rejected_with_a_reason(self):
        cases = {
            "syntax": ("shout", "def broken(:\n"),
            "no tool": ("shout", "x = 1\n"),
            "wrong name": ("yell", GOOD_SKILL),
            "crash on import": ("shout", "raise RuntimeError('boom')\n" + GOOD_SKILL),
            "bad name": ("Bad Name", GOOD_SKILL),
            "built-in": ("web_search", GOOD_SKILL.replace('"shout"', '"web_search"')),
        }
        for label, (name, code) in cases.items():
            with self.subTest(label):
                result = self.run_tool("create_skill", name=name, code=code)
                self.assertTrue(result.startswith("Error:"), result)
        self.assertFalse((self.config.skills_dir / "shout.py").exists())
        self.assertEqual(self.registry.skill_names, set())

    def test_skill_file_cannot_replace_builtin_tool(self):
        self.config.skills_dir.mkdir(parents=True)
        (self.config.skills_dir / "evil.py").write_text(GOOD_SKILL.replace('"shout"', '"send_email"'))
        registry = self.new_registry()
        self.assertNotIn("send_email", registry.skill_names)
        self.assertEqual(type(registry.get("send_email")).__name__, "SendEmail")

    def test_broken_skill_file_does_not_stop_nova(self):
        self.config.skills_dir.mkdir(parents=True)
        (self.config.skills_dir / "broken.py").write_text("raise RuntimeError('oops')\n")
        (self.config.skills_dir / "shout.py").write_text(GOOD_SKILL)
        registry = self.new_registry()
        self.assertIn("shout", registry.skill_names)
        self.assertTrue(any("broken.py" in error for error in registry.skill_errors))

    def test_remove_skill(self):
        self.run_tool("create_skill", name="shout", code=GOOD_SKILL)
        self.assertTrue(self.registry.needs_confirmation("remove_skill", {"name": "shout"}))
        self.assertIn("removed", self.run_tool("remove_skill", name="shout"))
        self.assertIsNone(self.registry.get("shout"))
        self.assertFalse((self.config.skills_dir / "shout.py").exists())
        self.assertIn("Error", self.run_tool("remove_skill", name="web_search"))

    def test_example_skill_in_real_project_loads(self):
        registry = create_registry(ToolContext(make_config(self.tmp.name), self.store, None))
        self.assertIn("word_count", registry.skill_names)
        self.assertEqual(registry.execute("word_count", {"text": "one two three"}),
                         "3 words, 1 lines, 13 characters")


class SelfModifyTests(SelfImprovementTestCase):
    def test_good_change_is_applied_and_can_be_rolled_back(self):
        new_code = "def add(a, b):\n    # improved by NOVA\n    return a + b\n"
        description = self.registry.describe("modify_nova_source", {"path": "calc.py", "content": new_code})
        self.assertIn("+    # improved by NOVA", description)  # the approval shows a diff
        self.assertTrue(self.registry.needs_confirmation("modify_nova_source", {"path": "calc.py", "content": ""}))

        result = self.run_tool("modify_nova_source", path="calc.py", content=new_code, reason="comment")
        self.assertIn("All tests passed", result)
        self.assertIn("/restart", result)
        self.assertEqual((self.project / "calc.py").read_text(), new_code)
        self.assertEqual(load_changes(self.config)[0]["reason"], "comment")

        self.assertIn("Undid the change to calc.py", rollback_last_change(self.config))
        self.assertEqual((self.project / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")
        self.assertIn("no self-made changes", rollback_last_change(self.config))

    def test_change_that_breaks_tests_is_rolled_back(self):
        result = self.run_tool("modify_nova_source", path="calc.py", content="def add(a, b):\n    return a - b\n")
        self.assertIn("REJECTED", result)
        self.assertIn("AssertionError", result)
        self.assertEqual((self.project / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")
        self.assertEqual(load_changes(self.config), [])

    def test_syntax_error_changes_nothing(self):
        result = self.run_tool("modify_nova_source", path="calc.py", content="def add(:\n")
        self.assertIn("Syntax error", result)
        self.assertEqual((self.project / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")

    def test_new_file_and_rollback_removes_it(self):
        self.assertIn("All tests passed", self.run_tool("modify_nova_source", path="helpers/extra.py", content="X = 1\n"))
        self.assertTrue((self.project / "helpers" / "extra.py").exists())
        rollback_last_change(self.config)
        self.assertFalse((self.project / "helpers" / "extra.py").exists())

    def test_personality_change_is_active_immediately(self):
        result = self.run_tool("modify_nova_source", path="personality/nova.txt", content="You are NOVA. Be witty.\n")
        self.assertIn("active from the next message", result)

    def test_locked_and_private_files_are_refused(self):
        for path in ["tests/test_calc.py", "tools/base.py", "tools/self_modify.py", "tools/skills.py",
                     "ui/server.py", ".env", "data/nova.db", "../outside.py", "calc.exe"]:
            with self.subTest(path=path):
                result = self.run_tool("modify_nova_source", path=path, content="x = 1\n")
                self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual((self.project / "tests" / "test_calc.py").read_text().count("assertEqual"), 1)

    def test_read_own_source(self):
        listing = self.run_tool("read_nova_source", path=".")
        self.assertIn("calc.py", listing)
        self.assertIn("tests/test_calc.py  (locked)", listing)
        self.assertIn("return a + b", self.run_tool("read_nova_source", path="calc.py"))
        self.assertIn("Error", self.run_tool("read_nova_source", path=".env"))


class AgentCommandTests(SelfImprovementTestCase):
    def test_restart_skills_and_rollback_commands(self):
        agent = Agent(self.config, ScriptedBrain(), self.store, "You are NOVA.")
        self.assertIn("No skills yet", agent.handle("/skills").text)
        self.assertIn("no self-made changes", agent.handle("/rollback").text)
        reply = agent.handle("/restart")
        self.assertTrue(reply.restart)
        self.assertTrue(reply.exit)
        self.assertTrue(reply.to_dict()["restart"])


if __name__ == "__main__":
    unittest.main()
