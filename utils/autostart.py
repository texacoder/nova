"""
Start JARVIS automatically when you log in to Windows (/autostart on|off).

It puts a tiny script, JARVIS.vbs, in your Windows "Startup" folder. At
login it runs start_jarvis.bat --no-browser in a minimized window, so
JARVIS is running in the background (scheduled emails and reminders work)
without opening a browser tab. Remove it with /autostart off, or by
deleting the file (press Win+R, type shell:startup).
"""

import os
import sys
from pathlib import Path

SCRIPT_NAME = "JARVIS.vbs"


class AutostartError(Exception):
    """Autostart could not be changed."""


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        raise AutostartError("Starting automatically is only available on Windows.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def script_path() -> Path:
    return startup_folder() / SCRIPT_NAME


def startup_script(project_root: Path) -> str:
    """The VBScript that starts JARVIS minimized (window style 7) without a browser tab."""
    root = str(Path(project_root).resolve())
    bat = str(Path(root) / "start_jarvis.bat")
    quote = lambda text: text.replace('"', '""')  # VBScript string escaping
    return (
        "' Starts JARVIS when you log in. Delete this file (or type /autostart off) to stop that.\r\n"
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{quote(root)}"\r\n'
        f'shell.Run """{quote(bat)}"" --no-browser", 7, False\r\n'
    )


def is_enabled() -> bool:
    try:
        return script_path().exists()
    except AutostartError:
        return False


def enable(project_root: Path) -> Path:
    path = script_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(startup_script(project_root), encoding="utf-8")
    except OSError as error:
        raise AutostartError(f"Could not write {path}: {error}")
    return path


def disable() -> bool:
    """Returns False if autostart wasn't on."""
    path = script_path()
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as error:
        raise AutostartError(f"Could not remove {path}: {error}")
    return True
