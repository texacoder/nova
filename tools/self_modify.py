"""
Self-modification: NOVA can read and change its own source code.

Safety net for every change:
  1. You approve it after seeing exactly what changes (a diff).
  2. The old version is backed up to data/backups/.
  3. NOVA's full test suite runs. If ANY test fails, the change is rolled
     back automatically and NOVA is told why, so it can try again.
  4. /rollback undoes the most recent successful change.

Some files are locked so NOVA can't weaken its own safeguards: the tests,
the approval system, this file, the skill installer and the web security.
You can still edit those yourself.

Code changes take effect after /restart. Personality changes
(personality/nova.txt) take effect on the next message.
"""

import difflib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.base import Tool, ToolError
from utils.logger import get_logger
from utils.text import truncate

log = get_logger("tools.self_modify")

EDITABLE_SUFFIXES = {".py", ".txt", ".md", ".json", ".js", ".css", ".html", ".bat"}
HIDDEN_PARTS = {"data", ".git", "__pycache__", ".venv", "venv", ".pytest_cache"}
PRIVATE_NAMES = {".env", "apps.json"}
# Safeguards NOVA may read but never change by itself.
LOCKED = {"tests", "tools/base.py", "tools/self_modify.py", "tools/skills.py", "ui/server.py"}
TEST_TIMEOUT = 600


def _relative(root: Path, path: str) -> tuple[Path, str]:
    candidate = (root / path.strip().lstrip("/\\")).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ToolError("Only files inside NOVA's own project folder can be used")
    relative = candidate.relative_to(root.resolve())
    if HIDDEN_PARTS & set(relative.parts) or candidate.name in PRIVATE_NAMES:
        raise ToolError(f"{relative.as_posix()} is private data or configuration")
    return candidate, relative.as_posix()


def is_locked(relative: str) -> bool:
    return any(relative == item or relative.startswith(item + "/") for item in LOCKED)


def editable_path(root: Path, path: str) -> tuple[Path, str]:
    """Resolve a project-relative path and make sure NOVA may change it."""
    file, relative = _relative(root, path)
    if is_locked(relative):
        raise ToolError(f"{relative} is a locked safeguard; only the user can edit it by hand")
    if file.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ToolError(f"Only these file types can be edited: {', '.join(sorted(EDITABLE_SUFFIXES))}")
    return file, relative


def list_source_files(root: Path) -> list[str]:
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not (HIDDEN_PARTS & set(relative.parts)) \
                and path.name not in PRIVATE_NAMES and path.suffix.lower() in EDITABLE_SUFFIXES:
            mark = "  (locked)" if is_locked(relative.as_posix()) else ""
            files.append(relative.as_posix() + mark)
    return files


def run_test_suite(root: Path) -> tuple[bool, str]:
    """Run NOVA's tests in a separate process. Returns (passed, output)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            cwd=str(root), capture_output=True, text=True, errors="replace", timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"The tests took longer than {TEST_TIMEOUT} seconds."
    return result.returncode == 0, (result.stdout + result.stderr).strip()


# --- change history (for /rollback) ---------------------------------------------

def _manifest(config) -> Path:
    return config.backups_dir / "changes.json"


def load_changes(config) -> list[dict]:
    try:
        return json.loads(_manifest(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_changes(config, changes: list[dict]) -> None:
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    _manifest(config).write_text(json.dumps(changes, indent=2), encoding="utf-8")


def rollback_last_change(config) -> str:
    """Undo the most recent change NOVA made to its own files."""
    changes = load_changes(config)
    if not changes:
        return "There are no self-made changes to undo."
    change = changes.pop()
    target = config.project_root / change["path"]
    if change["backup"]:
        shutil.copy2(config.backups_dir / change["backup"], target)
    else:
        target.unlink(missing_ok=True)  # the file was new, so undoing means removing it
    _save_changes(config, changes)
    log.info("Rolled back change to %s", change["path"])
    return f"Undid the change to {change['path']} (made {change['time'][:15]}). Type /restart if it was code."


# --- tools -------------------------------------------------------------------------

class ReadNovaSource(Tool):
    name = "read_nova_source"
    description = (
        "Read your own source code (NOVA's project files) to understand or improve yourself. "
        "Use path '.' to list all files."
    )
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Project-relative path, e.g. agent.py, or '.'"}},
        "required": ["path"],
    }

    def run(self, path: str) -> str:
        root = self.config.project_root
        if path.strip() in ("", ".", "/"):
            return "NOVA's source files:\n" + "\n".join(list_source_files(root))
        file, relative = _relative(root, path)
        if not file.is_file():
            raise ToolError(f"{relative} does not exist")
        return f"--- {relative} ---\n{file.read_text(encoding='utf-8')}"


class ModifyNovaSource(Tool):
    name = "modify_nova_source"
    description = (
        "Change your own source code or personality to improve yourself (fix a bug, add a feature, change "
        "behaviour). First read the file with read_nova_source, then provide its COMPLETE new content. "
        "The change is backed up and NOVA's tests run automatically; if any test fails it is rolled back. "
        "For a new ability prefer create_skill. Tests and safety files are locked."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative path, e.g. personality/nova.txt"},
            "content": {"type": "string", "description": "The complete new file content"},
            "reason": {"type": "string", "description": "Why this change improves NOVA"},
        },
        "required": ["path", "content"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        path = str(arguments.get("path", ""))
        try:
            file, relative = editable_path(self.config.project_root, path)
            old = file.read_text(encoding="utf-8").splitlines(keepends=True) if file.exists() else []
        except (ToolError, OSError, UnicodeDecodeError) as error:
            return f"Modify NOVA's file {path} (this will be refused: {error})"
        new = str(arguments.get("content", "")).splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old, new, f"{relative} (now)", f"{relative} (new)"))
        reason = arguments.get("reason") or "(no reason given)"
        return f"Modify NOVA's own file: {relative}\nReason: {reason}\n\n{truncate(diff or '(no changes)', 12000)}"

    def run(self, path: str, content: str, reason: str = "") -> str:
        root = self.config.project_root
        file, relative = editable_path(root, path)
        existed = file.exists()
        if existed and file.read_text(encoding="utf-8") == content:
            return f"{relative} already has exactly this content; nothing changed."
        if file.suffix == ".py":
            try:
                compile(content, relative, "exec")
            except SyntaxError as error:
                raise ToolError(f"Syntax error on line {error.lineno}: {error.msg}. Nothing was changed.")

        # 1. back up
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = f"{stamp}/{relative}" if existed else ""
        if existed:
            (self.config.backups_dir / backup).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, self.config.backups_dir / backup)

        # 2. write
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8", newline="")

        # 3. test, and roll back on failure
        passed, output = run_test_suite(root)
        if not passed:
            if existed:
                shutil.copy2(self.config.backups_dir / backup, file)
            else:
                file.unlink(missing_ok=True)
            log.warning("Change to %s rolled back: tests failed", relative)
            return ("Change REJECTED and rolled back: NOVA's tests failed with it. Nothing changed. "
                    "Test output (end):\n" + "\n".join(output.splitlines()[-25:]))

        changes = load_changes(self.config)
        changes.append({"time": stamp, "path": relative, "backup": backup, "reason": reason})
        _save_changes(self.config, changes)
        log.info("Self-modification applied to %s: %s", relative, reason)

        if file.suffix == ".py":
            when = "It takes effect after a restart: tell the user to type /restart."
        elif relative.startswith("personality/"):
            when = "It is active from the next message."
        else:
            when = "It may need /restart to take effect."
        return f"Changed {relative}. All tests passed. {when} (The user can undo it with /rollback.)"
