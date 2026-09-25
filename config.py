"""
Configuration for NOVA.

Settings are read from (highest priority first):
  1. Real environment variables  (e.g. `set OLLAMA_MODEL=qwen2.5:7b`)
  2. A `.env` file in the project folder
  3. The defaults defined below

Nothing here is machine-specific: relative paths are resolved against the
project folder and "~" means your home folder, so NOVA works on any PC.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# The folder that contains this file (the project root).
PROJECT_ROOT = Path(__file__).resolve().parent

# Brains NOVA knows how to create. See brain/__init__.py.
SUPPORTED_BRAINS = ("ollama",)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULTS = {
    # Identity
    "NOVA_NAME": "NOVA",
    "NOVA_VERSION": "1.0",
    # Brain
    "NOVA_BRAIN": "ollama",
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_MODEL": "",
    "OLLAMA_TIMEOUT": "300",
    "OLLAMA_NUM_CTX": "8192",
    # Storage, personality, logging
    "NOVA_DATA_DIR": "data",
    "NOVA_PERSONALITY_FILE": "personality/nova.txt",
    "NOVA_LOG_LEVEL": "INFO",
    # Context
    "NOVA_MAX_HISTORY": "40",
    "NOVA_MAX_MEMORIES_IN_PROMPT": "50",
    "NOVA_MAX_TOOL_STEPS": "8",
    # PC control
    "NOVA_WORKSPACE": "~/NOVA_Workspace",
    "NOVA_APPS_FILE": "apps.json",
    "NOVA_COMMAND_TIMEOUT": "60",
    "NOVA_AUTO_APPROVE": "",
    # Web UI
    "NOVA_WEB_HOST": "127.0.0.1",
    "NOVA_WEB_PORT": "8765",
    # Email (optional)
    "EMAIL_ADDRESS": "",
    "EMAIL_PASSWORD": "",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "IMAP_HOST": "imap.gmail.com",
}


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Config:
    """All of NOVA's settings in one place."""

    name: str
    version: str
    brain: str
    ollama_host: str
    ollama_model: str
    ollama_timeout: int
    ollama_num_ctx: int
    data_dir: Path
    personality_file: Path
    log_level: str
    max_history: int
    max_memories_in_prompt: int
    max_tool_steps: int
    workspace: Path
    apps_file: Path
    command_timeout: int
    auto_approve: frozenset
    web_host: str
    web_port: int
    email_address: str
    email_password: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    # The .env file these settings came from (brain setup saves the chosen model there).
    env_file: Path = PROJECT_ROOT / ".env"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nova.db"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "logs" / "nova.log"

    @property
    def email_configured(self) -> bool:
        return bool(self.email_address and self.email_password)


def read_env_file(path: Path) -> dict:
    """
    Read a simple .env file of KEY=VALUE lines.

    Blank lines and lines starting with # are ignored.
    Surrounding quotes around values are removed.
    """
    values = {}
    if not path.exists():
        return values

    try:
        text = path.read_text(encoding="utf-8-sig")  # -sig: tolerate Notepad's BOM
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Could not read {path}: {error}") from error

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            raise ConfigError(
                f"{path.name} line {line_number} is not in KEY=VALUE format: {raw_line!r}"
            )
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _to_int(settings: dict, key: str, minimum: int) -> int:
    raw = settings[key]
    try:
        number = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from None
    if number < minimum:
        raise ConfigError(f"{key} must be at least {minimum}, got {number}")
    return number


def _to_path(value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_config(env_file: Path | None = None, environ: dict | None = None) -> Config:
    """
    Build a Config from defaults, the .env file and environment variables.

    `env_file` and `environ` can be passed in by tests; normally they are
    the project's .env file and the real environment.
    """
    if env_file is None:
        env_file = PROJECT_ROOT / ".env"
    if environ is None:
        environ = dict(os.environ)

    settings = dict(DEFAULTS)
    settings.update(read_env_file(env_file))
    # Real environment variables win, but only for keys NOVA knows about.
    settings.update({k: v for k, v in environ.items() if k in DEFAULTS})

    brain = settings["NOVA_BRAIN"].strip().lower()
    if brain not in SUPPORTED_BRAINS:
        raise ConfigError(
            f"NOVA_BRAIN must be one of {', '.join(SUPPORTED_BRAINS)}; got {brain!r}"
        )

    log_level = settings["NOVA_LOG_LEVEL"].strip().upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(
            f"NOVA_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}; got {log_level!r}"
        )

    ollama_host = settings["OLLAMA_HOST"].strip().rstrip("/")
    if not ollama_host.startswith(("http://", "https://")):
        raise ConfigError(f"OLLAMA_HOST must start with http:// or https://; got {ollama_host!r}")

    name = settings["NOVA_NAME"].strip()
    if not name:
        raise ConfigError("NOVA_NAME cannot be empty")

    auto_approve = frozenset(
        item.strip() for item in settings["NOVA_AUTO_APPROVE"].split(",") if item.strip()
    )

    return Config(
        name=name,
        version=settings["NOVA_VERSION"].strip() or DEFAULTS["NOVA_VERSION"],
        brain=brain,
        ollama_host=ollama_host,
        ollama_model=settings["OLLAMA_MODEL"].strip(),
        ollama_timeout=_to_int(settings, "OLLAMA_TIMEOUT", minimum=1),
        ollama_num_ctx=_to_int(settings, "OLLAMA_NUM_CTX", minimum=512),
        data_dir=_to_path(settings["NOVA_DATA_DIR"]),
        personality_file=_to_path(settings["NOVA_PERSONALITY_FILE"]),
        log_level=log_level,
        max_history=_to_int(settings, "NOVA_MAX_HISTORY", minimum=2),
        max_memories_in_prompt=_to_int(settings, "NOVA_MAX_MEMORIES_IN_PROMPT", minimum=0),
        max_tool_steps=_to_int(settings, "NOVA_MAX_TOOL_STEPS", minimum=1),
        workspace=_to_path(settings["NOVA_WORKSPACE"]),
        apps_file=_to_path(settings["NOVA_APPS_FILE"]),
        command_timeout=_to_int(settings, "NOVA_COMMAND_TIMEOUT", minimum=1),
        auto_approve=auto_approve,
        web_host=settings["NOVA_WEB_HOST"].strip() or "127.0.0.1",
        web_port=_to_int(settings, "NOVA_WEB_PORT", minimum=1),
        email_address=settings["EMAIL_ADDRESS"].strip(),
        email_password=settings["EMAIL_PASSWORD"].strip(),
        smtp_host=settings["SMTP_HOST"].strip(),
        smtp_port=_to_int(settings, "SMTP_PORT", minimum=1),
        imap_host=settings["IMAP_HOST"].strip(),
        env_file=env_file,
    )
