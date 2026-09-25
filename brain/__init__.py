"""
NOVA's brain package.

`create_brain(config)` picks the right brain based on NOVA_BRAIN.
To add a new kind of brain later: write a class that inherits from Brain,
then add it here.
"""

from brain.base import Brain, BrainError, BrainStatus, BrainUnavailableError
from brain.local import LocalBrain
from brain.mock import MockBrain


def create_brain(config) -> Brain:
    if config.brain == "ollama":
        return LocalBrain(config.ollama_host, config.ollama_model, config.ollama_timeout)
    if config.brain == "mock":
        return MockBrain()
    raise ValueError(f"Unknown brain: {config.brain!r}")


__all__ = [
    "Brain", "BrainError", "BrainStatus", "BrainUnavailableError",
    "LocalBrain", "MockBrain", "create_brain",
]
