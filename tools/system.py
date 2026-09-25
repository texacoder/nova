"""System tools: date/time, PC information and running commands."""

import os
import platform
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


def shell_command(command: str) -> list[str]:
    """How to run a command line on this operating system."""
    if platform.system() == "Windows":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


class RunCommand(Tool):
    name = "run_command"
    description = (
        "Run a command line on the user's PC (PowerShell on Windows, sh on Linux/macOS) "
        "and return its output. Use for tasks no other tool covers. The user must approve it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run"},
            "working_directory": {
                "type": "string",
                "description": "Folder to run in (default: NOVA's workspace)",
            },
        },
        "required": ["command"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        where = arguments.get("working_directory") or str(self.config.workspace)
        return f"Run command in {where}:\n{arguments.get('command', '')}"

    def run(self, command: str, working_directory: str = "") -> str:
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
        output = (completed.stdout or "") + (completed.stderr or "")
        return f"Exit code {completed.returncode}\n{output.strip() or '(no output)'}"
