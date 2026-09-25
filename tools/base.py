"""
Foundation for future tools (web search, file access, app launching...).

v0.1 ships with NO tools on purpose. This file only defines the shape a
tool will have, so later versions can plug tools in without redesigning
the agent. Each tool will be registered explicitly, so NOVA can never use
a capability you did not deliberately add.
"""

from abc import ABC, abstractmethod


class Tool(ABC):
    #: Unique name the model will use to call the tool, e.g. "web_search".
    name: str = ""
    #: One-line description shown to the model.
    description: str = ""

    @abstractmethod
    def run(self, **kwargs) -> str:
        """Perform the action and return a text result."""


class ToolRegistry:
    """Keeps track of which tools NOVA is allowed to use."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

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

    def __len__(self) -> int:
        return len(self._tools)
