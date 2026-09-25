"""
NOVA's brain package.

`create_brain(config)` creates NOVA's brain: a real AI model running on
this PC through Ollama. To support another model server later, write a
class that inherits from Brain and add it here.
"""

from brain.base import Brain, BrainError, BrainReply, BrainStatus, BrainUnavailableError, ToolCall
from brain.local import LocalBrain


def create_brain(config) -> Brain:
    if config.brain == "ollama":
        return LocalBrain(
            config.ollama_host, config.ollama_model, config.ollama_timeout, config.ollama_num_ctx
        )
    raise ValueError(f"Unknown brain: {config.brain!r}")


__all__ = [
    "Brain", "BrainError", "BrainReply", "BrainStatus", "BrainUnavailableError", "ToolCall",
    "LocalBrain", "create_brain",
]
