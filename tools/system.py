"""System tools: date/time, PC information and running commands."""

import os
import platform
import re
import shutil
import subprocess
from datetime import datetime

from tools.base import Tool, ToolError


class GetDateTime(Tool):
    name = "get_datetime"
    description = "Get the current local date, time and day of the week."

    def run(self) -> str:
        return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S (local time)")


class SystemInfo(Tool):
    name = "system_info"
    description = "Get information about this PC: operating system, CPU cores, disk space."

    def run(self) -> str:
        home = os.path.expanduser("~")
        disk = shutil.disk_usage(home)
        gb = 1024 ** 3
        return "\n".join([
            f"Operating system: {platform.system()} {platform.release()} ({platform.version()})",
            f"Machine: {platform.machine()}, CPU cores: {os.cpu_count()}",
            f"Python: {platform.python_version()}",
            f"Disk ({home}): {disk.free / gb:.1f} GB free of {disk.total / gb:.1f} GB",
        ])


# Commands from the old Windows Command Prompt. Models often write these (e.g. "del /f /q x"),
# but PowerShell doesn't understand their /switches, so such commands are run in cmd instead.
CMD_COMMANDS = {"del", "erase", "copy", "move", "dir", "rd", "rmdir", "md", "mkdir", "ren", "rename",
                "type", "xcopy", "attrib", "tree", "where"}


def is_cmd_style(command: str) -> bool:
    """True for Command Prompt commands with /switches, like 'del /f /q file' or 'dir /s'."""
    words = command.strip().split()
    if not words or words[0].lower() not in CMD_COMMANDS:
        return False
    return any(re.fullmatch(r"/[a-zA-Z](:\S*)?", word) for word in words[1:])


DELETE_COMMANDS = {"del", "erase", "rm", "rmdir", "rd", "remove-item", "ri", "rni"}


def is_delete_command(command: str) -> bool:
    """Commands that delete files - these go through delete_file (Recycle Bin) instead."""
    words = command.strip().split()
    return bool(words) and words[0].lower() in DELETE_COMMANDS


def shell_command(command: str, windows: bool | None = None) -> list[str]:
    """How to run a command line on this operating system."""
    if windows is None:
        windows = platform.system() == "Windows"
    if windows:
        if is_cmd_style(command):
            return ["cmd", "/d", "/c", command]
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


def shell_name(command: str) -> str:
    if platform.system() != "Windows":
        return "the shell"
    return "Command Prompt" if is_cmd_style(command) else "PowerShell"


class RunCommand(Tool):
    name = "run_command"
    description = (
        "Run a command on the user's PC and return its output. On Windows it runs in PowerShell, "
        "so use PowerShell syntax (e.g. Get-ChildItem, Copy-Item, New-Item). Use it only when no "
        "other tool fits; to delete files use delete_file instead. The user must approve it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run"},
            "working_directory": {
                "type": "string",
                "description": "Folder to run in (default: your workspace)",
            },
        },
        "required": ["command"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        where = arguments.get("working_directory") or str(self.config.workspace)
        command = str(arguments.get("command", ""))
        return f"Run in {shell_name(command)} (folder: {where}):\n{command}"

    def run(self, command: str, working_directory: str = "") -> str:
        if is_delete_command(command):
            raise ToolError("Nothing was deleted: don't delete with run_command. Use the delete_file tool "
                            "for each file or folder instead - it moves them to the Recycle Bin.")
        folder = os.path.expanduser(working_directory) if working_directory else str(self.config.workspace)
        os.makedirs(self.config.workspace, exist_ok=True)
        if not os.path.isdir(folder):
            raise ToolError(f"Folder does not exist: {folder}")
        try:
            completed = subprocess.run(
                shell_command(command),
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.command_timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"The command took longer than {self.config.command_timeout} seconds and was stopped")
        except FileNotFoundError as error:
            raise ToolError(f"Could not start the shell: {error}")
        output = ((completed.stdout or "") + (completed.stderr or "")).strip() or "(no output)"
        if completed.returncode != 0:
            # A failed command is a failure: shown with a cross, and flagged as "not done".
            raise ToolError(f"The command failed (exit code {completed.returncode}). Nothing may have "
                            f"been changed. Output:\n{output}")
        return f"Exit code 0\n{output}"
