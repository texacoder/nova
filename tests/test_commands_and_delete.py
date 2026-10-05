"""Tests for running commands safely and deleting files to the Recycle Bin."""

import unittest
from pathlib import Path
from unittest import mock

from agent import Agent
from brain.base import BrainReply, ToolCall
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir
from tools import ToolContext, create_registry
from tools.system import is_cmd_style, is_delete_command, shell_command


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.config = make_config(self.tmp.name)
        self.store = MemoryStore(self.config.db_path)
        self.registry = create_registry(ToolContext(self.config, self.store, None))

    def test_failed_command_is_an_error(self):
        result = self.registry.execute("run_command", {"command": "exit 3"})
        self.assertTrue(result.startswith("Error: The command failed (exit code 3)"))
        self.assertIn("Exit code 0", self.registry.execute("run_command", {"command": "echo ok"}))

    def test_failed_command_is_flagged_not_done(self):
        """Replay of a real session: a failing delete command was shown as a success."""
        config = make_config(self.tmp.name, JARVIS_AUTO_APPROVE="run_command")
        brain = ScriptedBrain(BrainReply("", [ToolCall("run_command", {"command": "exit 1"})]),
                              BrainReply("Done."))
        reply = Agent(config, brain, self.store, "You are JARVIS.").handle("do it")
        self.assertEqual(reply.steps[0]["status"], "error")
        self.assertIn("⚠ Not done: run_command (it failed)", reply.text)

    def test_command_prompt_style_runs_in_cmd(self):
        for command in ["del /f /q C:\\x\\hello.py", "dir /s /b", "copy /y a.txt b.txt", "rd /s /q old",
                        "xcopy src dst /e /i"]:
            with self.subTest(command=command):
                self.assertTrue(is_cmd_style(command))
                self.assertEqual(shell_command(command, windows=True)[:3], ["cmd", "/d", "/c"])
        for command in ["Get-ChildItem C:/", "dir", "copy a.txt b.txt", "echo /f", "python -m http.server"]:
            with self.subTest(command=command):
                self.assertEqual(shell_command(command, windows=True)[0], "powershell")
        self.assertEqual(shell_command("ls -la", windows=False), ["/bin/sh", "-c", "ls -la"])

    def test_deleting_with_commands_is_redirected(self):
        for command in ["del /f /q C:\\x\\hello.py", "Remove-Item hello.py", "rm -rf projects"]:
            with self.subTest(command=command):
                self.assertTrue(is_delete_command(command))
                result = self.registry.execute("run_command", {"command": command})
                self.assertIn("Use the delete_file tool", result)
        self.assertFalse(is_delete_command("Get-ChildItem"))


class DeleteFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "Documents").mkdir(parents=True)
        patch = mock.patch.object(Path, "home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.config = make_config(self.tmp.name)
        self.config.workspace = self.home / "JARVIS_Workspace"
        self.registry = create_registry(ToolContext(self.config, MemoryStore(self.config.db_path), None))
        self.file = self.config.workspace / "projects" / "hello.py"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("print('hi')")

    def test_delete_goes_to_trash_and_can_be_restored(self):
        self.assertTrue(self.registry.needs_confirmation("delete_file", {"path": "projects/hello.py"}))
        self.assertIn(str(self.file), self.registry.describe("delete_file", {"path": "projects/hello.py"}))
        result = self.registry.execute("delete_file", {"path": "projects/hello.py"})
        self.assertIn("Recycle Bin", result)
        self.assertFalse(self.file.exists())
        trashed = self.home / ".local/share/Trash/files/hello.py"
        self.assertEqual(trashed.read_text(), "print('hi')")  # still recoverable

    def test_same_name_twice_keeps_both_in_trash(self):
        self.registry.execute("delete_file", {"path": "projects/hello.py"})
        self.file.write_text("second")
        self.registry.execute("delete_file", {"path": "projects/hello.py"})
        self.assertEqual((self.home / ".local/share/Trash/files/hello (1).py").read_text(), "second")

    def test_delete_folder(self):
        self.assertIn("folder with 1 file", self.registry.describe("delete_file", {"path": "projects"}))
        self.registry.execute("delete_file", {"path": "projects"})
        self.assertFalse((self.config.workspace / "projects").exists())

    def test_protected_places_are_refused(self):
        jarvis_file = Path(__file__).resolve().parents[1] / "agent.py"
        for path in [str(self.home), "Documents", str(self.config.workspace), str(jarvis_file),
                     str(Path(self.home.anchor))]:
            with self.subTest(path=path):
                result = self.registry.execute("delete_file", {"path": path})
                self.assertIn("Refused", result)
        self.assertTrue(jarvis_file.exists())

    def test_missing_file(self):
        self.assertIn("doesn't exist", self.registry.execute("delete_file", {"path": "nope.txt"}))

    def test_windows_uses_recycle_bin_with_path_passed_safely(self):
        import tools.files as files
        tricky = self.config.workspace / "it's; Remove-Item C.txt"
        tricky.write_text("x")

        def fake_run(command, **kwargs):
            Path(kwargs["env"]["JARVIS_DELETE_PATH"]).unlink()
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(files.platform, "system", return_value="Windows"), \
             mock.patch.object(files.subprocess, "run", side_effect=fake_run) as run:
            result = self.registry.execute("delete_file", {"path": str(tricky)})
        self.assertIn("Recycle Bin", result)
        command, kwargs = run.call_args[0][0], run.call_args[1]
        self.assertIn("SendToRecycleBin", command[-1])
        self.assertNotIn("Remove-Item C.txt", " ".join(command))  # the file name is never part of the command
        self.assertEqual(kwargs["env"]["JARVIS_DELETE_PATH"], str(tricky.resolve()))


if __name__ == "__main__":
    unittest.main()
