"""
One-time upgrade of an installation from its old name (NOVA) to JARVIS.

Runs automatically at startup and does nothing once everything is upgraded:
  - .env: NOVA_* settings become JARVIS_*, and the old default name,
    personality file and workspace folder are switched to the new ones
  - data/nova.db (memories, knowledge, lessons) becomes data/jarvis.db
  - ~/NOVA_Workspace becomes ~/JARVIS_Workspace (with all its files)

If anything can't be renamed, the old file or folder keeps being used,
so nothing is ever lost.
"""

import shutil
from pathlib import Path

from utils.logger import get_logger

log = get_logger("migrate")

OLD_PREFIX, NEW_PREFIX = "NOVA_", "JARVIS_"
VALUE_UPGRADES = {
    "JARVIS_NAME": {"NOVA": "JARVIS"},
    "JARVIS_PERSONALITY_FILE": {"personality/nova.txt": "personality/jarvis.txt"},
    "JARVIS_WORKSPACE": {"~/NOVA_Workspace": "~/JARVIS_Workspace"},
}


def migrate_env_file(env_file: Path) -> list[str]:
    """Rewrite old setting names and old default values in .env. Returns notes for the user."""
    if not env_file.exists():
        return []
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        log.warning("Could not read %s for upgrading: %s", env_file, error)
        return []

    changed = False
    upgraded = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            upgraded.append(line)
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        new_key = NEW_PREFIX + key[len(OLD_PREFIX):] if key.startswith(OLD_PREFIX) else key
        bare = value.strip("'\"").replace("\\", "/")
        new_value = VALUE_UPGRADES.get(new_key, {}).get(bare, value)
        if (new_key, new_value) != (key, value):
            changed = True
            upgraded.append(f"{new_key}={new_value}")
        else:
            upgraded.append(line)

    if not changed:
        return []
    try:
        env_file.write_text("\n".join(upgraded) + "\n", encoding="utf-8")
    except OSError as error:
        log.warning("Could not upgrade %s: %s", env_file, error)
        return [f"Couldn't update {env_file.name} ({error}); the old settings still work."]
    log.info("Upgraded settings in %s for the new name", env_file)
    return [f"Updated your settings ({env_file.name}) for the new name."]


def migrate_files(config) -> list[str]:
    """Rename the old memory database and workspace folder. Returns notes for the user."""
    notes = []

    old_db = config.data_dir / "nova.db"
    if old_db.exists() and not config.db_path.exists():
        try:
            old_db.rename(config.db_path)
            notes.append(f"Moved your memories to {config.db_path.name}.")
        except OSError:
            try:
                shutil.copy2(old_db, config.db_path)
                notes.append(f"Copied your memories to {config.db_path.name}.")
            except OSError as error:
                log.warning("Could not move %s: %s", old_db, error)
                notes.append(f"Couldn't move your memories ({error}). They are safe in {old_db}.")

    old_workspace = Path.home() / "NOVA_Workspace"
    default_workspace = Path.home() / "JARVIS_Workspace"
    if config.workspace == default_workspace and old_workspace.is_dir() and not default_workspace.exists():
        try:
            shutil.move(str(old_workspace), str(default_workspace))
            notes.append(f"Renamed your workspace folder to {default_workspace}.")
        except OSError as error:
            log.warning("Could not rename %s: %s", old_workspace, error)
            config.workspace = old_workspace  # keep using the old folder
            notes.append(f"Kept using your workspace at {old_workspace} (couldn't rename it: {error}).")

    for note in notes:
        log.info("Upgrade: %s", note)
    return notes
