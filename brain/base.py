"""
The Brain interface.

A "brain" is anything that can turn a conversation into a reply.
NOVA's agent only talks to this interface, so the model behind it can be
swapped (Ollama today, something else tomorrow) without touching the rest
of the code.

Messages use the common chat format understood by Ollama and most other
local model servers:

    [
        {"role": "system",    "content": "You are NOVA..."},
        {"role": "user",      "content": "Hello"},
        {"role": "assistant", "content": "Hello. How can I help?"},
    ]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

VALID_ROLES = ("system", "user", "assistant")


class BrainError(Exception):
    """Something went wrong while generating a response."""


class BrainUnavailableError(BrainError):
    """The brain cannot be reached or is not configured."""


@dataclass
class BrainStatus:
    """Result of a health check."""

    ok: bool
    message: str


class Brain(ABC):
    """Base class every brain must follow."""

    #: Short name shown in /status, e.g. "ollama (llama3.2)".
    name = "brain"

    @abstractmethod
    def generate_response(self, messages: list[dict]) -> str:
        """
        Return the assistant's reply to `messages`.

        Raises BrainError (or BrainUnavailableError) on failure.
        """

    @abstractmethod
    def health_check(self) -> BrainStatus:
        """Report whether the brain is ready to use. Must never raise."""


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
