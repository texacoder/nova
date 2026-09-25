"""
The Brain interface.

A "brain" is anything that can turn a conversation into a reply.
NOVA's agent only talks to this interface, so the model behind it can be
swapped (Ollama today, something else tomorrow) without touching the rest
of the code.

Messages use the common chat format understood by Ollama:

    {"role": "system",    "content": "You are NOVA..."}
    {"role": "user",      "content": "What time is it?"}
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_datetime", "arguments": {}}}]}
    {"role": "tool",      "content": "2026-09-25 17:03", "tool_name": "get_datetime"}
    {"role": "assistant", "content": "It's 17:03."}

"Tools" are actions NOVA can take (search the web, open an app...).
The brain doesn't run tools itself: it *asks* for them by returning
tool calls, and the agent decides whether and how to run them.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

VALID_ROLES = ("system", "user", "assistant", "tool")


class BrainError(Exception):
    """Something went wrong while generating a response."""


class BrainUnavailableError(BrainError):
    """The brain cannot be reached or is not configured."""


@dataclass
class BrainStatus:
    """Result of a health check."""

    ok: bool
    message: str


@dataclass
class ToolCall:
    """The brain asking to run one tool."""

    name: str
    arguments: dict = field(default_factory=dict)

    def to_message_format(self) -> dict:
        return {"function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class BrainReply:
    """What the brain answered: text, and possibly tool calls."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class Brain(ABC):
    """Base class every brain must follow."""

    #: Short name shown in /status, e.g. "ollama (qwen2.5:7b)".
    name = "brain"
    #: Whether this brain can request tools. Brains may switch this off.
    supports_tools = True

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> BrainReply:
        """
        Reply to `messages`. `tools` lists the tools the brain may request
        (in Ollama's JSON-schema format).

        Raises BrainError (or BrainUnavailableError) on failure.
        """

    @abstractmethod
    def health_check(self) -> BrainStatus:
        """Report whether the brain is ready to use. Must never raise."""

    def generate_response(self, messages: list[dict]) -> str:
        """Plain text reply with no tools (handy for summaries)."""
        return self.chat(messages).content


def validate_messages(messages: list[dict]) -> None:
    """Raise BrainError if `messages` is not in the expected chat format."""
    if not isinstance(messages, list) or not messages:
        raise BrainError("messages must be a non-empty list")
    for message in messages:
        if not isinstance(message, dict):
            raise BrainError(f"each message must be a dict, got {type(message).__name__}")
        if message.get("role") not in VALID_ROLES:
            raise BrainError(f"invalid message role: {message.get('role')!r}")
        if not isinstance(message.get("content"), str):
            raise BrainError("message content must be a string")
