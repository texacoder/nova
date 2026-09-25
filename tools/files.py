"""
File tools.

NOVA has its own folder, the *workspace* (NOVA_WORKSPACE, default
~/NOVA_Workspace). Inside it NOVA can read and write freely, e.g. to
create code files. Reading or writing anywhere else needs your approval.
"""

from pathlib import Path

from tools.base import Tool, ToolError

MAX_READ_CHARS = 20000
MAX_LIST_ENTRIES = 200


def resolve_path(config, path: str) -> Path:
    """Relative paths are inside the workspace; '~' is your home folder."""
    candidate = Path(str(path).strip()).expanduser()
    if not candidate.is_absolute():
        candidate = config.workspace / candidate
    return candidate.resolve()


def inside_workspace(config, path: Path) -> bool:
    return path.resolve().is_relative_to(config.workspace.resolve())


class _PathTool(Tool):
    """Shared logic: confirm when the path is outside the workspace."""

    def needs_confirmation(self, arguments: dict) -> bool:
        path = arguments.get("path", ".")
        return not inside_workspace(self.config, resolve_path(self.config, path))

    def describe(self, arguments: dict) -> str:
        path = resolve_path(self.config, arguments.get("path", "."))
        return f"{self.name}: {path}"


class ListDirectory(_PathTool):
    name = "list_directory"
    description = (
        "List files and folders. Relative paths are inside NOVA's workspace folder; "
        "use '.' for the workspace itself or an absolute path like C:/Users/me/Documents."
    )
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Folder to list"}},
        "required": [],
    }

    def run(self, path: str = ".") -> str:
        folder = resolve_path(self.config, path)
        if folder == self.config.workspace.resolve():
            folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder")
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"{p.name}/" if p.is_dir() else p.name for p in entries[:MAX_LIST_ENTRIES]]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"... and {len(entries) - MAX_LIST_ENTRIES} more")
        return f"Contents of {folder}:\n" + ("\n".join(lines) if lines else "(empty)")


class ReadFile(_PathTool):
    name = "read_file"
    description = "Read a text file (code, notes, etc.). Relative paths are inside the workspace."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File to read"}},
        "required": ["path"],
    }

    def run(self, path: str) -> str:
        file = resolve_path(self.config, path)
        if not file.is_file():
            raise ToolError(f"{file} does not exist or is not a file")
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{file.name} is not a text file")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + f"\n...[file continues, {len(text)} characters total]"
        return f"Contents of {file}:\n{text}"


class WriteFile(_PathTool):
    name = "write_file"
    description = (
        "Create or overwrite a text file (for example a Python script). Relative paths are "
        "inside NOVA's workspace. Folders are created automatically. Set append=true to add "
        "to the end instead of overwriting."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to write, e.g. projects/hello.py"},
            "content": {"type": "string", "description": "The full text to write"},
            "append": {"type": "boolean", "description": "Append instead of overwrite"},
        },
        "required": ["path", "content"],
    }

    def describe(self, arguments: dict) -> str:
        path = resolve_path(self.config, arguments.get("path", ""))
        action = "Append to" if arguments.get("append") else "Write"
        size = len(str(arguments.get("content", "")))
        return f"{action} file {path} ({size} characters)"

    def run(self, path: str, content: str, append: bool = False) -> str:
        file = resolve_path(self.config, path)
        if file.is_dir():
            raise ToolError(f"{file} is a folder, not a file")
        file.parent.mkdir(parents=True, exist_ok=True)
        existed = file.exists()
        with open(file, "a" if append else "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        verb = "Appended" if append else ("Overwrote" if existed else "Created")
        return f"{verb} {file} ({len(content)} characters)."
