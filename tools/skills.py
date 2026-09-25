"""
Skills: new abilities NOVA writes for itself.

When NOVA needs an ability it doesn't have (e.g. "convert this CSV to
Excel" or "resize these photos"), it can write a new Tool in Python with
create_skill. The code is:

  1. shown to you for approval (it is real code that will run on your PC),
  2. checked in a separate Python process (syntax, structure, can be loaded),
  3. saved to the skills/ folder, and
  4. loaded immediately, so NOVA can use it in the same conversation.

Skills are loaded again every time NOVA starts. Skills can't replace
NOVA's built-in tools. Delete a file in skills/ (or ask NOVA to use
remove_skill) to remove an ability.
"""

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.base import Tool, ToolError
from utils.logger import get_logger

log = get_logger("tools.skills")

# NOVA's own code folder: skills always import NOVA's real tools.base from here.
CODE_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

SKILL_TEMPLATE = '''from tools.base import Tool, ToolError


class WordCount(Tool):
    name = "word_count"                      # must equal the skill name
    description = "Count the words in a text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text"}},
        "required": ["text"],
    }
    requires_confirmation = False            # True if every use should ask the user first

    def run(self, text: str) -> str:
        return f"{len(text.split())} words"
'''

# Runs in a separate process to check a skill file without affecting NOVA.
VALIDATOR = r"""
import importlib.util, json, sys
sys.path.insert(0, sys.argv[3])
from tools.base import Tool, ToolContext
spec = importlib.util.spec_from_file_location("skill_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tools = [obj for obj in vars(module).values()
         if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool
         and obj.__module__ == module.__name__]
matching = [t for t in tools if t.name == sys.argv[2]]
if not matching:
    raise SystemExit(f"No Tool class with name = {sys.argv[2]!r} found (found: {[t.name for t in tools]})")
tool = matching[0](ToolContext(config=None))
schema = tool.schema()
if not tool.description:
    raise SystemExit("The tool needs a description")
if schema["function"]["parameters"].get("type") != "object":
    raise SystemExit("parameters must be a JSON schema with type 'object'")
json.dumps(schema)
print("OK")
"""


SKILL_RULES = (
    "Nothing was installed. Fix the code and call create_skill again right away. Do NOT just show "
    "code to the user and do NOT claim the skill exists. Rules: the code starts with "
    "`from tools.base import Tool, ToolError`; it has one class inheriting from Tool; that class sets "
    "name = the skill name, description, and parameters (JSON schema with type 'object'); run(self, ...) "
    "returns a string; only import modules and functions that really exist in the standard library."
)


def prepare_skill_code(code: str) -> str:
    """Tidy up code from the model: remove ``` fences and add the Tool import if it's missing."""
    text = code.strip()
    fenced = re.match(r"^```(?:python|py)?[ \t]*\n(.*?)\n?```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    if "Tool" in text and not re.search(r"^\s*from\s+tools\.base\s+import\b", text, re.MULTILINE):
        text = "from tools.base import Tool, ToolError\n\n" + text
    return text.rstrip() + "\n"


def _module_tools(path: Path) -> list[type]:
    """Import a skill file and return the Tool classes it defines."""
    module_name = f"nova_skill_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return [obj for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool
            and obj.__module__ == module_name and obj.name]


def load_skill(path: Path, context, registry) -> list[str]:
    """Load one skill file into the registry. Returns the tool names added."""
    added = []
    for tool_class in _module_tools(path):
        if tool_class.name in registry.names() and tool_class.name not in registry.skill_names:
            log.warning("Skill %s tried to replace built-in tool %s; skipped", path.name, tool_class.name)
            continue
        registry.register(tool_class(context), replace=True)
        registry.skill_names.add(tool_class.name)
        added.append(tool_class.name)
    return added


def load_all_skills(context, registry) -> list[str]:
    """Load every skill in the skills/ folder. Returns error messages (if any)."""
    errors = []
    folder = context.config.skills_dir
    if not folder.is_dir():
        return errors
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            names = load_skill(path, context, registry)
            log.info("Loaded skill %s: %s", path.name, names)
        except Exception as error:  # a broken skill must not stop NOVA
            log.exception("Could not load skill %s", path.name)
            errors.append(f"{path.name}: {error}")
    return errors


def validate_skill(code: str, name: str) -> None:
    """Check the code in a separate process. Raises ToolError describing the problem."""
    try:
        compile(code, f"{name}.py", "exec")
    except SyntaxError as error:
        raise ToolError(f"Syntax error on line {error.lineno}: {error.msg}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-c", VALIDATOR, str(path), name, str(CODE_ROOT)],
                capture_output=True, text=True, errors="replace", timeout=60, cwd=str(CODE_ROOT),
            )
        except subprocess.TimeoutExpired:
            raise ToolError("Loading the skill took over 60 seconds. Don't do slow work when the file is imported.")
    if result.returncode != 0 or "OK" not in result.stdout:
        details = (result.stderr or result.stdout).strip().splitlines()[-8:]
        raise ToolError("The skill failed its check:\n" + "\n".join(details))


class CreateSkill(Tool):
    name = "create_skill"
    description = (
        "Give yourself a NEW ability by writing a Python tool, when no existing tool can do what the "
        "user needs. The code must define one class inheriting from Tool (`from tools.base import Tool, "
        "ToolError`) with class attributes name (equal to the skill name), description, parameters "
        "(JSON schema) and a run(self, ...) method returning a string. self.config.workspace is your "
        "workspace folder. Use the Python standard library unless the user installed a package. Raise "
        "ToolError('message') for expected problems. Calling it again with the same name replaces the "
        "skill (use that to fix bugs). Example:\n" + SKILL_TEMPLATE
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "snake_case name, e.g. csv_to_json"},
            "code": {"type": "string", "description": "The complete Python source of the skill"},
        },
        "required": ["name", "code"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        name = str(arguments.get("name", ""))
        verb = "Replace skill" if (self.config.skills_dir / f"{name}.py").exists() else "Create new skill"
        code = prepare_skill_code(str(arguments.get("code", "")))
        return f"{verb} '{name}' (Python code that will run on your PC):\n\n{code}"

    def run(self, name: str, code: str) -> str:
        name = name.strip()
        if not SKILL_NAME.match(name):
            raise ToolError("Skill names must be snake_case letters/numbers, 3-41 characters, e.g. csv_to_json")
        registry = self.context.registry
        if name in registry.names() and name not in registry.skill_names:
            raise ToolError(f"'{name}' is a built-in tool; choose another name")

        code = prepare_skill_code(code)
        try:
            validate_skill(code, name)
        except ToolError as error:
            raise ToolError(f"{error}\n\n{SKILL_RULES}")

        folder = self.config.skills_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.py"
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(code, encoding="utf-8")
        try:
            added = load_skill(path, self.context, registry)
            if name not in added:
                raise ToolError(f"the file does not define a tool called '{name}'")
        except Exception as error:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous, encoding="utf-8")
            raise ToolError(f"The skill passed its check but failed to load: {error}\n\n{SKILL_RULES}")
        log.info("Skill %s installed", name)
        return f"Skill '{name}' is installed ({path}) and ready to use right now. Call it to do the task."


class RemoveSkill(Tool):
    name = "remove_skill"
    description = "Delete a skill you created earlier (only skills, not built-in tools)."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill's name"}},
        "required": ["name"],
    }
    requires_confirmation = True

    def run(self, name: str) -> str:
        registry = self.context.registry
        if name not in registry.skill_names:
            names = ", ".join(sorted(registry.skill_names)) or "none"
            raise ToolError(f"There is no skill called '{name}'. Skills: {names}")
        registry.unregister(name)
        (self.config.skills_dir / f"{name}.py").unlink(missing_ok=True)
        return f"Skill '{name}' removed."


class ListSkills(Tool):
    name = "list_skills"
    description = "List the skills (abilities you wrote for yourself) that you currently have."

    def run(self) -> str:
        registry = self.context.registry
        if not registry.skill_names:
            return "No skills yet. Use create_skill to give yourself a new ability."
        return "\n".join(f"- {n}: {registry.get(n).description}" for n in sorted(registry.skill_names))
