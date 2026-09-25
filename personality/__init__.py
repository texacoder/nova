"""
Loads NOVA's personality from a plain text file (personality/nova.txt).

Edit that file to change how NOVA behaves; no Python changes needed.
The placeholders {name} and {version} are filled in from the config.
"""

from pathlib import Path

from utils.logger import get_logger

log = get_logger("personality")

FALLBACK_PERSONALITY = (
    "You are {name}, a concise, calm and helpful personal AI assistant (version {version}). "
    "Never pretend to have performed an action you did not perform."
)


def load_personality(path: Path, name: str, version: str) -> str:
    try:
        template = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        log.warning("Could not read personality file %s; using built-in fallback", path)
        template = ""
    if not template:
        template = FALLBACK_PERSONALITY
    # Plain replace (not str.format) so other { } characters in the file are safe.
    return template.replace("{name}", name).replace("{version}", version)
