"""Shared helpers for the tests."""

import tempfile
from pathlib import Path

from brain.base import Brain, BrainReply, BrainStatus
from config import load_config


def make_config(tmp_dir: str, **overrides):
    """A Config that stores everything (data + workspace) inside a temporary folder."""
    environ = {
        "NOVA_DATA_DIR": str(Path(tmp_dir) / "data"),
        "NOVA_WORKSPACE": str(Path(tmp_dir) / "workspace"),
        "NOVA_APPS_FILE": str(Path(tmp_dir) / "apps.json"),
    }
    environ.update(overrides)
    return load_config(env_file=Path(tmp_dir) / "missing.env", environ=environ)


def temp_dir():
    return tempfile.TemporaryDirectory()


class ScriptedBrain(Brain):
    """A fake brain that returns pre-written replies, and records what it was sent."""

    name = "scripted"

    def __init__(self, *replies: BrainReply):
        self.replies = list(replies)
        self.calls = []  # (messages, tools) for each chat() call

    def chat(self, messages, tools=None):
        self.calls.append((messages, tools))
        if self.replies:
            return self.replies.pop(0)
        return BrainReply("done")

    def health_check(self):
        return BrainStatus(True, "scripted brain")
