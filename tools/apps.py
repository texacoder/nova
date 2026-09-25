"""
Tools to open applications, files, folders and websites.

Apps are found in this order:
  1. apps.json in the project folder (your own list, see apps.example.json)
  2. Programs on your PATH (e.g. "notepad", "geany" if installed that way)
  3. Common install locations on Windows (Geany, VS Code, Chrome, ...)

Apps listed in apps.json, plus a few everyday apps, open without asking.
Anything else asks for your approval first.
"""

import json
import os
import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path

from tools.base import Tool, ToolError
from tools.files import inside_workspace, resolve_path
from utils.logger import get_logger

log = get_logger("tools.apps")

IS_WINDOWS = platform.system() == "Windows"

# Everyday apps that are safe to open without asking.
TRUSTED_APPS = {
    "notepad", "calc", "calculator", "mspaint", "paint", "explorer", "geany", "code", "vscode",
    "chrome", "firefox", "msedge", "edge", "notepad++", "gedit", "wordpad", "write",
}

# Where common apps usually live on Windows (%VARS% are expanded).
WINDOWS_LOCATIONS = {
    "geany": [r"%ProgramFiles%\Geany\bin\geany.exe", r"%ProgramFiles(x86)%\Geany\bin\geany.exe"],
    "code": [r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe", r"%ProgramFiles%\Microsoft VS Code\Code.exe"],
    "vscode": [r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe", r"%ProgramFiles%\Microsoft VS Code\Code.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"],
    "firefox": [r"%ProgramFiles%\Mozilla Firefox\firefox.exe"],
    "edge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"],
    "notepad++": [r"%ProgramFiles%\Notepad++\notepad++.exe"],
    "calculator": [r"%SystemRoot%\System32\calc.exe"],
    "paint": [r"%SystemRoot%\System32\mspaint.exe"],
}

# Apps NOVA opened keep running on their own; keeping a reference stops Python warning about them.
_launched: list = []

# Opening these with the default program would *run* them, so always ask.
EXECUTABLE_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi",
    ".scr", ".lnk", ".reg", ".hta", ".cpl", ".jar", ".sh", ".py", ".pyw", ".app",
}


def load_app_aliases(path: Path) -> dict[str, str]:
    """Read apps.json: {"geany": "C:/Program Files/Geany/bin/geany.exe", ...}."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        log.warning("Could not read %s: %s", path, error)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): os.path.expandvars(str(v)) for k, v in data.items()}


def normalise_app_name(name: str) -> str:
    name = name.strip().strip("'\"").lower()
    return name[:-4] if name.endswith(".exe") else name


def open_with_default_program(target: str) -> None:
    """Open a file, folder or URL the way double-clicking it would."""
    if IS_WINDOWS:
        os.startfile(target)  # noqa: only exists on Windows
    elif platform.system() == "Darwin":
        _launched.append(subprocess.Popen(["open", target]))
    else:
        _launched.append(subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))


class OpenApplication(Tool):
    name = "open_application"
    description = (
        "Open an application on the user's PC, optionally with arguments such as a file to open. "
        "Example: name='geany', arguments=['C:/Users/me/NOVA_Workspace/hello.py']."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "App name, e.g. geany, notepad, chrome"},
            "arguments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arguments, e.g. a file path to open",
            },
        },
        "required": ["name"],
    }

    def aliases(self) -> dict[str, str]:
        return load_app_aliases(self.config.apps_file)

    def find_app(self, name: str) -> str | None:
        key = normalise_app_name(name)
        aliases = self.aliases()
        if key in aliases:
            return aliases[key]
        found = shutil.which(key) or shutil.which(name.strip())
        if found:
            return found
        if IS_WINDOWS:
            for location in WINDOWS_LOCATIONS.get(key, []):
                expanded = os.path.expandvars(location)
                if os.path.isfile(expanded):
                    return expanded
        return None

    def needs_confirmation(self, arguments: dict) -> bool:
        key = normalise_app_name(str(arguments.get("name", "")))
        return key not in TRUSTED_APPS and key not in self.aliases()

    def describe(self, arguments: dict) -> str:
        args = " ".join(str(a) for a in arguments.get("arguments") or [])
        found = self.find_app(str(arguments.get("name", ""))) or arguments.get("name")
        return f"Open application: {found} {args}".strip()

    def run(self, name: str, arguments: list | None = None) -> str:
        args = [str(a) for a in (arguments or [])]
        program = self.find_app(name)
        if program:
            flags = {}
            if IS_WINDOWS:
                flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                flags["start_new_session"] = True
            try:
                _launched.append(subprocess.Popen([program, *args], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags))
            except OSError as error:
                raise ToolError(f"Could not start {program}: {error}")
            return f"Started {program} {' '.join(args)}".strip()

        if IS_WINDOWS and not args:
            # Windows can often find installed apps by name (App Paths registry).
            try:
                os.startfile(name.strip())
                return f"Asked Windows to open '{name}'."
            except OSError:
                pass
        raise ToolError(
            f"Could not find an app called '{name}'. Add its full path to apps.json "
            "(see apps.example.json)."
        )


class OpenPath(Tool):
    name = "open_path"
    description = (
        "Open a file, folder or website (http/https URL) with its default program, "
        "e.g. open a document, show a folder in Explorer, or open a web page in the browser."
    )
    parameters = {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "File path, folder or URL"}},
        "required": ["target"],
    }

    @staticmethod
    def _is_url(target: str) -> bool:
        return target.strip().lower().startswith(("http://", "https://"))

    def needs_confirmation(self, arguments: dict) -> bool:
        target = str(arguments.get("target", ""))
        if self._is_url(target):
            return False
        path = resolve_path(self.config, target)
        if path.is_dir():
            return False
        return path.suffix.lower() in EXECUTABLE_EXTENSIONS or not inside_workspace(self.config, path)

    def describe(self, arguments: dict) -> str:
        target = str(arguments.get("target", ""))
        shown = target if self._is_url(target) else str(resolve_path(self.config, target))
        return f"Open: {shown}"

    def run(self, target: str) -> str:
        if self._is_url(target):
            webbrowser.open(target.strip())
            return f"Opened {target.strip()} in the web browser."
        path = resolve_path(self.config, target)
        if not path.exists():
            raise ToolError(f"{path} does not exist")
        try:
            open_with_default_program(str(path))
        except OSError as error:
            raise ToolError(f"Could not open {path}: {error}")
        return f"Opened {path}."
