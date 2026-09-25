"""
The tool system: actions NOVA can perform on your PC and the internet.

Each tool is a small class with:
  - name / description / parameters   -> shown to the AI model
  - run(**arguments)                   -> does the work, returns text
  - needs_confirmation(arguments)      -> True if YOU must approve it first

Only tools registered in the ToolRegistry exist for NOVA. Anything risky
(sending email, running commands, writing outside the workspace...) asks
for your approval, because a web page or email could contain text that
tries to trick the AI into doing something you didn't want.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger
from utils.text import truncate

log = get_logger("tools")

# Tool output longer than this is cut, so it fits in the model's context.
MAX_RESULT_CHARS = 6000


class ToolError(Exception):
    """A friendly error message to hand back to the model."""


@dataclass
class ToolContext:
    """Things tools may need: settings, memory and the brain."""

    config: Any
    memory_store: Any = None
    brain: Any = None


class Tool(ABC):
    #: Unique name the model uses to call the tool, e.g. "web_search".
    name: str = ""
    #: One-line description shown to the model.
    description: str = ""
    #: JSON schema of the arguments (Ollama/OpenAI format).
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    #: Whether the user must approve each use (can be refined per call).
    requires_confirmation: bool = False

    def __init__(self, context: ToolContext):
        self.context = context
        self.config = context.config

    @abstractmethod
    def run(self, **arguments) -> str:
        """Perform the action and return a text result."""

    def needs_confirmation(self, arguments: dict) -> bool:
        return self.requires_confirmation

    def describe(self, arguments: dict) -> str:
        """Human-readable summary shown when asking for approval."""
        details = ", ".join(f"{k}={truncate(str(v), 200)!r}" for k, v in arguments.items())
        return f"{self.name}({details})"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Keeps track of which tools NOVA is allowed to use."""

    def __init__(self, auto_approve: frozenset = frozenset()):
        self._tools: dict[str, Tool] = {}
        self.auto_approve = auto_approve

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("A tool must have a name")
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def needs_confirmation(self, name: str, arguments: dict) -> bool:
        tool = self.get(name)
        if tool is None or name in self.auto_approve:
            return False
        return tool.needs_confirmation(arguments)

    def describe(self, name: str, arguments: dict) -> str:
        tool = self.get(name)
        return tool.describe(arguments) if tool else f"{name}({arguments})"

    def execute(self, name: str, arguments: dict) -> str:
        """Run a tool and always return text (errors become 'Error: ...')."""
        tool = self.get(name)
        if tool is None:
            return f"Error: there is no tool called {name!r}. Available: {', '.join(self.names())}"

        # Ignore arguments the tool doesn't define (models sometimes invent extras).
        known = tool.parameters.get("properties", {})
        clean_args = {k: v for k, v in arguments.items() if k in known}
        missing = [k for k in tool.parameters.get("required", []) if k not in clean_args]
        if missing:
            return f"Error: {name} needs the argument(s): {', '.join(missing)}"

        log.info("Running tool %s with %s", name, truncate(repr(clean_args), 500))
        try:
            result = tool.run(**clean_args)
        except ToolError as error:
            log.info("Tool %s failed: %s", name, error)
            return f"Error: {error}"
        except Exception as error:  # a tool bug must never crash NOVA
            log.exception("Tool %s crashed", name)
            return f"Error: {name} failed unexpectedly ({type(error).__name__}: {error})"
        return truncate(str(result), MAX_RESULT_CHARS)

    def __len__(self) -> int:
        return len(self._tools)
