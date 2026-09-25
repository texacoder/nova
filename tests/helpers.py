"""Shared helpers for the tests."""

import tempfile
from pathlib import Path

from config import load_config


def make_config(tmp_dir: str, **overrides):
    """A Config that stores everything inside a temporary folder."""
    environ = {"NOVA_DATA_DIR": tmp_dir, "NOVA_BRAIN": "mock"}
    environ.update(overrides)
    return load_config(env_file=Path(tmp_dir) / "missing.env", environ=environ)


def temp_dir():
    return tempfile.TemporaryDirectory()
