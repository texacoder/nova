"""
File tools: find, list, read and write files.

Who may do what without asking:
  - Your workspace (JARVIS_WORKSPACE, default ~/JARVIS_Workspace): read and write freely.
  - Your own folders (Documents, Desktop, Downloads, Pictures, ...): find, list and
    read freely - it's your PC and only you use JARVIS.
  - Private places (AppData, .ssh, password/key files, JARVIS's .env): always ask.
  - Writing anywhere outside the workspace: always ask.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from tools.base import Tool, ToolError

MAX_READ_CHARS = 20000
MAX_LIST_ENTRIES = 200

# Everyday names for the user's folders. OneDrive often moves them on Windows.
KNOWN_FOLDERS = {
    "documents": ["Documents", "OneDrive/Documents"], "document": ["Documents", "OneDrive/Documents"],
    "my documents": ["Documents", "OneDrive/Documents"], "docs": ["Documents", "OneDrive/Documents"],
    "desktop": ["Desktop", "OneDrive/Desktop"],
    "downloads": ["Downloads"], "download": ["Downloads"],
    "pictures": ["Pictures", "OneDrive/Pictures"], "photos": ["Pictures", "OneDrive/Pictures"],
    "music": ["Music"], "videos": ["Videos"], "home": [""],
}
SEARCH_EVERYWHERE = ["documents", "desktop", "downloads", "pictures", "music", "videos"]

# Places with passwords, keys and app data: reading them always needs approval.
PRIVATE_PARTS = {"appdata", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "$recycle.bin"}
PRIVATE_NAMES = {".env", ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "credentials"}
PRIVATE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}

# Folders not worth searching (huge, or full of program internals).
SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", "$recycle.bin", "windows",
             "program files", "program files (x86)", "programdata", ".cache", "venv", ".venv"}


def known_folder(name: str) -> Path | None:
    """'Documents' -> C:/Users/you/Documents (or the OneDrive one if that's where it lives)."""
    candidates = KNOWN_FOLDERS.get(name.strip().lower())
    if candidates is None:
        return None
    paths = [Path.home() / c for c in candidates]
    return next((p for p in paths if p.is_dir()), paths[0])


def resolve_path(config, path: str) -> Path:
    """
    Turn what the user or model wrote into a real path:
    absolute paths stay; '~' is home; 'Documents/x.txt' means your Documents folder;
    anything else relative is inside the workspace.
    """
    text = str(path).strip().strip("'\"")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        first, _, rest = text.replace("\\", "/").partition("/")
        folder = known_folder(first)
        if folder is not None and not (config.workspace / first).exists():
            candidate = folder / rest if rest else folder
        else:
            candidate = config.workspace / candidate
    return candidate.resolve()


def inside_workspace(config, path: Path) -> bool:
    return path.resolve().is_relative_to(config.workspace.resolve())


def is_private(config, path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts & PRIVATE_PARTS or path.name.lower() in PRIVATE_NAMES or path.suffix.lower() in PRIVATE_SUFFIXES:
        return True
    return path.resolve() == config.env_file.resolve()


def free_to_read(config, path: Path) -> bool:
    """Reading/listing without approval: the workspace, or the user's own (non-private) files."""
    if is_private(config, path):
        return False
    return inside_workspace(config, path) or path.resolve().is_relative_to(Path.home().resolve())


class _ReadTool(Tool):
    """Shared logic for tools that only look at files."""

    def needs_confirmation(self, arguments: dict) -> bool:
        return not free_to_read(self.config, resolve_path(self.config, arguments.get("path", ".")))

    def describe(self, arguments: dict) -> str:
        path = resolve_path(self.config, arguments.get("path", "."))
        return f"{self.name}: {path}"


class FindFiles(Tool):
    name = "find_files"
    description = (
        "Search the user's PC for files or folders by name. Partial names are fine: 'budget' finds "
        "'Budget 2025.xlsx'. Without a folder it searches Documents, Desktop, Downloads, Pictures, "
        "Music, Videos and your workspace. Use this whenever the user asks to find, check for or "
        "locate a file, then offer to open what you found."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Part of the file or folder name, e.g. 'budget'"},
            "folder": {"type": "string",
                       "description": "Optional: where to look, e.g. 'Documents', 'Downloads' or a full path"},
        },
        "required": ["name"],
    }
    MAX_RESULTS = 25
    MAX_ENTRIES = 300_000
    MAX_SECONDS = 20

    def roots(self, folder: str) -> list[Path]:
        if folder.strip():
            return [resolve_path(self.config, folder)]
        roots = [known_folder(name) for name in SEARCH_EVERYWHERE] + [self.config.workspace]
        unique = []
        for root in roots:
            if root and root.is_dir() and root.resolve() not in [u.resolve() for u in unique]:
                unique.append(root)
        return unique

    def needs_confirmation(self, arguments: dict) -> bool:
        return any(is_private(self.config, root) for root in self.roots(str(arguments.get("folder", ""))))

    def describe(self, arguments: dict) -> str:
        where = ", ".join(str(r) for r in self.roots(str(arguments.get("folder", "")))) or "your folders"
        return f"Search for '{arguments.get('name', '')}' in {where}"

    @staticmethod
    def _words(text: str) -> list[str]:
        return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]

    def run(self, name: str, folder: str = "") -> str:
        wanted = self._words(name)
        if not wanted:
            raise ToolError("Give part of the file name to look for")
        roots = self.roots(folder)
        missing = [str(r) for r in roots if not r.is_dir()]
        if folder.strip() and missing:
            raise ToolError(f"The folder {missing[0]} doesn't exist")

        matches, seen, started = [], 0, time.time()
        for root in roots:
            for current, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
                for entry in dirs + files:
                    seen += 1
                    flat = "".join(self._words(entry))
                    if all(word in flat for word in wanted):
                        matches.append(Path(current) / entry)
                if seen > self.MAX_ENTRIES or time.time() - started > self.MAX_SECONDS:
                    break

        where = folder or "Documents, Desktop, Downloads, Pictures, Music, Videos and the workspace"
        if not matches:
            return (f"No file or folder matching '{name}' was found in {where} ({seen} items checked). "
                    "Tell the user, and suggest another name or folder.")

        stem = "".join(wanted)
        matches.sort(key=lambda p: ("".join(self._words(p.stem)) != stem, -self._mtime(p)))
        lines = [self._describe_match(p) for p in matches[: self.MAX_RESULTS]]
        more = f"\n...and {len(matches) - self.MAX_RESULTS} more" if len(matches) > self.MAX_RESULTS else ""
        return (f"Found {len(matches)} match(es) for '{name}':\n" + "\n".join(lines) + more +
                "\nTell the user what you found and ask if they want it opened (use open_path).")

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _describe_match(self, path: Path) -> str:
        try:
            info = path.stat()
        except OSError:
            return f"- {path}"
        when = datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y")
        if path.is_dir():
            return f"- {path}  (folder, modified {when})"
        size = info.st_size
        size_text = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
        return f"- {path}  ({size_text}, modified {when})"


class ListDirectory(_ReadTool):
    name = "list_directory"
    description = (
        "List the files and folders in a folder. Accepts 'Documents', 'Desktop', 'Downloads', "
        "'Pictures' etc., a full path like C:/Users/me/Projects, or '.' for your workspace."
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


class ReadFile(_ReadTool):
    name = "read_file"
    description = (
        "Read a text file (code, notes, .txt, .csv, .md...). Accepts a full path, "
        "'Documents/notes.txt', or a path inside your workspace."
    )
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
            raise ToolError(f"{file.name} is not a plain text file, so it can't be read this way. "
                            "Offer to open it with its program instead (open_path).")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + f"\n...[file continues, {len(text)} characters total]"
        return f"Contents of {file}:\n{text}"


class WriteFile(Tool):
    name = "write_file"
    description = (
        "Create or overwrite a text file (for example a Python script). Relative paths are "
        "inside your workspace. Folders are created automatically. Set append=true to add "
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

    def needs_confirmation(self, arguments: dict) -> bool:
        # Changing files outside the workspace always asks first.
        return not inside_workspace(self.config, resolve_path(self.config, arguments.get("path", "")))

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
