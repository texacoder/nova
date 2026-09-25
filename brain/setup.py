"""
Automatic brain setup.

NOVA's brain is a free, open AI model (e.g. Qwen 2.5) running on this PC
through Ollama. This module gets it ready with no manual steps:

  1. Find Ollama, or install it (Windows: via winget, or the official installer)
  2. Start the Ollama server if it isn't running
  3. Pick a model that fits this PC's memory (or one already installed)
  4. Download it, reporting progress
  5. Save the choice to .env so the next start is instant

SetupManager runs these steps in a background thread and exposes a status
dict the web UI and terminal can display.
"""

import ctypes
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from utils.logger import get_logger

log = get_logger("brain.setup")

IS_WINDOWS = platform.system() == "Windows"
OLLAMA_WINDOWS_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"

# Best model for each amount of RAM (GB). All support tool calling.
MODELS_BY_RAM = [(15, "qwen2.5:7b"), (7, "qwen2.5:3b"), (0, "qwen2.5:1.5b")]
# Already-installed models NOVA is happy to use, best first.
PREFERRED_FAMILIES = ["qwen2.5", "qwen3", "llama3.1", "llama3.2", "mistral-nemo", "mistral", "command-r"]


# Ollama keeps running after it's started; keep a reference so Python doesn't complain.
_background_processes: list = []


class SetupError(Exception):
    """A setup step failed; the message says what the user can do."""


# --- facts about this PC --------------------------------------------------------

def total_ram_gb() -> float:
    """Total physical memory in GB (0 if unknown)."""
    try:
        if IS_WINDOWS:
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 1024 ** 3
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()) / 1024 ** 3
        with open("/proc/meminfo", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 ** 2
    except Exception:  # never let a hardware query break setup
        log.warning("Could not detect RAM size", exc_info=True)
    return 0.0


def recommended_model(ram_gb: float | None = None) -> str:
    ram = total_ram_gb() if ram_gb is None else ram_gb
    for minimum, model in MODELS_BY_RAM:
        if ram >= minimum:
            return model
    return MODELS_BY_RAM[-1][1]


def pick_installed_model(installed: list[str]) -> str | None:
    """Choose the best already-installed model for NOVA, if any."""
    for family in PREFERRED_FAMILIES:
        matches = [name for name in installed if name.split(":")[0] == family]
        if matches:
            return sorted(matches)[0]
    return None


def find_ollama() -> str | None:
    """Path to the ollama program, or None if it isn't installed."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = []
    if IS_WINDOWS:
        candidates = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
                      os.path.expandvars(r"%ProgramFiles%\Ollama\ollama.exe")]
    elif platform.system() == "Darwin":
        candidates = ["/Applications/Ollama.app/Contents/Resources/ollama", "/usr/local/bin/ollama"]
    else:
        candidates = ["/usr/local/bin/ollama", "/usr/bin/ollama"]
    return next((c for c in candidates if os.path.isfile(c)), None)


# --- talking to the Ollama server ----------------------------------------------

def _get_json(url: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def server_running(host: str) -> bool:
    try:
        _get_json(f"{host}/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def installed_models(host: str) -> list[str]:
    return [m.get("name", "") for m in _get_json(f"{host}/api/tags").get("models", [])]


def model_installed(model: str, installed: list[str]) -> bool:
    wanted = model if ":" in model else f"{model}:latest"
    return model in installed or wanted in installed


def start_server(ollama_path: str, host: str, wait_seconds: int = 30) -> None:
    """Start `ollama serve` in the background and wait until it answers."""
    log.info("Starting Ollama server: %s serve", ollama_path)
    flags = {}
    if IS_WINDOWS:
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    _background_processes.append(subprocess.Popen(
        [ollama_path, "serve"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags))
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if server_running(host):
            return
        time.sleep(1)
    raise SetupError("Ollama was started but didn't respond. Try starting the Ollama app from the Start menu.")


def pull_model(host: str, model: str, on_progress=None) -> None:
    """Download `model`, calling on_progress(status_text, fraction_or_None)."""
    request = urllib.request.Request(
        f"{host}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise SetupError(f"Downloading {model} failed: {event['error']}")
                total, done = event.get("total"), event.get("completed")
                fraction = done / total if total and done is not None else None
                if on_progress:
                    on_progress(event.get("status", ""), fraction)
                if event.get("status") == "success":
                    return
    except (urllib.error.URLError, OSError) as error:
        raise SetupError(f"Could not download {model}: {error}. Check your internet connection.") from error
    raise SetupError(f"The download of {model} ended unexpectedly. Try again.")


# --- installing Ollama ---------------------------------------------------------

WINGET_TIME_LIMIT = 8 * 60      # seconds before giving up on winget
INSTALLER_TIME_LIMIT = 10 * 60  # seconds before giving up on the official installer


def winget_command(winget: str) -> list[str]:
    return [winget, "install", "--id", "Ollama.Ollama", "-e", "--source", "winget", "--silent",
            "--disable-interactivity", "--accept-source-agreements", "--accept-package-agreements"]


def installer_command(installer: Path) -> list[str]:
    return [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


def _run_until_installed(command: list[str], label: str, notify, time_limit: float, host: str | None) -> str | None:
    """
    Run an installer, but don't wait on it blindly: finish as soon as Ollama is
    installed (or its server answers), and stop the installer if it takes longer
    than `time_limit` seconds. Returns the path to ollama, or None.
    """
    output_log = Path(tempfile.gettempdir()) / "nova_ollama_install.log"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
    try:
        with open(output_log, "w", encoding="utf-8", errors="replace") as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, **flags)
    except OSError as error:
        log.warning("Could not start %s: %s", command[0], error)
        return None

    started = time.time()
    while True:
        elapsed = time.time() - started
        finished = process.poll() is not None
        path = find_ollama()
        if path and (finished or (host and server_running(host))):
            break  # installed; a still-running installer can finish on its own
        if finished:
            break
        if elapsed > time_limit:
            log.warning("%s took over %s seconds; stopping it", label, time_limit)
            process.kill()
            break
        minutes, seconds = divmod(int(elapsed), 60)
        notify(f"{label} ({minutes}:{seconds:02d})... If Windows asks for permission, click Yes "
               "(the prompt may be hidden behind other windows; check the taskbar).", None)
        time.sleep(2)

    try:
        log.info("%s exit code %s. Output:\n%s", label, process.poll(),
                 output_log.read_text(encoding="utf-8", errors="replace")[-3000:])
    except OSError:
        pass
    return find_ollama()


def install_ollama(on_progress=None, host: str | None = None) -> str:
    """Install Ollama and return the path to the program."""
    if not IS_WINDOWS:
        raise SetupError(
            "Automatic install is available on Windows. On this system install Ollama from "
            "https://ollama.com/download (Linux: curl -fsSL https://ollama.com/install.sh | sh), then try again."
        )

    notify = on_progress or (lambda text, fraction: None)
    winget = shutil.which("winget")
    if winget:
        path = _run_until_installed(winget_command(winget), "Installing Ollama with winget",
                                    notify, WINGET_TIME_LIMIT, host)
        if path:
            return path
        log.info("winget didn't install Ollama; trying the official installer")

    notify("Downloading the Ollama installer from ollama.com...", None)
    installer = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    try:
        with urllib.request.urlopen(OLLAMA_WINDOWS_INSTALLER, timeout=60) as response, open(installer, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                done += len(chunk)
                notify("Downloading the Ollama installer...", done / total if total else None)
    except (urllib.error.URLError, OSError) as error:
        raise SetupError(
            f"Could not download Ollama ({error}). Please install it yourself from "
            "https://ollama.com/download, then click Try again."
        ) from error

    path = _run_until_installed(installer_command(installer), "Running the Ollama installer",
                                notify, INSTALLER_TIME_LIMIT, host)
    if not path:
        raise SetupError(
            "Ollama didn't finish installing. Please install it yourself from "
            "https://ollama.com/download (run OllamaSetup.exe), then click Try again."
        )
    return path


# --- saving the choice ----------------------------------------------------------

def save_env_value(env_file: Path, key: str, value: str) -> None:
    """Set KEY=value in the .env file (updating the line or adding it)."""
    lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
    new_line = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key:
            lines[index] = new_line
            break
    else:
        lines.append(new_line)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the whole process ----------------------------------------------------------

class SetupManager:
    """Runs the setup steps in a background thread and reports progress."""

    def __init__(self, brain, env_file: Path):
        self.brain = brain
        self.env_file = env_file
        self.lock = threading.Lock()
        self.thread = None
        self.status = {"state": "idle", "message": "", "progress": None, "model": brain.model}

    def _update(self, **changes) -> None:
        with self.lock:
            self.status.update(changes)
        log.info("Setup: %s", changes.get("message", changes))

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.status)

    def needs_install(self) -> bool:
        return find_ollama() is None and not server_running(self.brain.host)

    def quick_start(self) -> bool:
        """
        Everything that needs no permission and no download: start an installed
        Ollama and pick an installed model. Returns True if the brain is ready.
        """
        try:
            if not server_running(self.brain.host):
                path = find_ollama()
                if not path:
                    return False
                start_server(path, self.brain.host)
            if not self.brain.model:
                chosen = pick_installed_model(installed_models(self.brain.host))
                if chosen:
                    self._use_model(chosen)
            return self.brain.health_check().ok
        except (SetupError, OSError, ValueError) as error:
            log.warning("Quick start failed: %s", error)
            return False

    def start(self) -> bool:
        """Begin full setup in the background. Returns False if already running."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.status = {"state": "running", "message": "Starting setup...", "progress": None,
                           "model": self.brain.model}
            self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def run_blocking(self) -> dict:
        """Run full setup in this thread (used by the terminal)."""
        self.status["state"] = "running"
        self._run()
        return self.snapshot()

    def _progress(self, text: str, fraction) -> None:
        self._update(message=text, progress=fraction)

    def _use_model(self, model: str) -> None:
        self.brain.model = model
        self.brain.supports_tools = True
        save_env_value(self.env_file, "OLLAMA_MODEL", model)
        self._update(model=model)

    def _run(self) -> None:
        host = self.brain.host
        try:
            if not server_running(host):
                path = find_ollama()
                if not path:
                    self._update(message="Installing Ollama (the free engine that runs NOVA's brain)...")
                    path = install_ollama(self._progress, host)
                self._update(message="Starting the AI engine...", progress=None)
                if not server_running(host):
                    start_server(path, host)

            installed = installed_models(host)
            model = self.brain.model or pick_installed_model(installed) or recommended_model()
            if not model_installed(model, installed):
                self._update(message=f"Downloading NOVA's brain ({model}). This is a one-time download of a few GB...")
                pull_model(host, model, lambda text, fraction: self._progress(
                    f"Downloading {model}: {text}", fraction))
            self._use_model(model)

            health = self.brain.health_check()
            if not health.ok:
                raise SetupError(health.message)
            self._update(state="done", message=f"NOVA's brain is ready ({model}).", progress=1.0)
        except SetupError as error:
            self._update(state="error", message=str(error), progress=None)
        except Exception as error:  # unexpected: log details, show a friendly message
            log.exception("Brain setup crashed")
            self._update(state="error", message=f"Setup failed unexpectedly: {error}. See the log file.", progress=None)
