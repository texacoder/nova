"""
The scheduler: carries out scheduled emails and reminders at their time.

A background thread checks the `scheduled` table every few seconds while
JARVIS is running. When a task is due:
  - email    -> sent with the send_email tool (you approved it when it was
                scheduled). If JARVIS was off and it's more than 12 hours
                late, it is NOT sent (it may be out of date); you're told.
  - reminder -> shown in the web UI (spoken aloud and with a beep), in the
                terminal, and as a Windows notification.
After it has run, the task is deleted from the database, so finished tasks
don't pile up. Failed or missed tasks are kept for 7 days, then removed.

Interfaces get notifications with `notifications_since(id)` (the web UI
polls it) or by adding a listener function (the terminal does that).
"""

import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta

from utils.logger import get_logger
from utils.text import truncate
from utils.when import describe_time

log = get_logger("scheduler")

CHECK_EVERY_SECONDS = 15
MAX_LATE = timedelta(hours=12)    # older than this when JARVIS starts = missed, not sent
LATE_NOTE_AFTER = timedelta(minutes=5)
KEEP_NOTIFICATIONS = 50

TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:JARVIS_TOAST_TITLE)) > $null
$texts.Item(1).AppendChild($template.CreateTextNode($env:JARVIS_TOAST_TEXT)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def windows_toast(title: str, text: str) -> None:
    """Show a Windows notification (best effort; does nothing elsewhere)."""
    if sys.platform != "win32":
        return
    # The text goes in environment variables, never inside the script itself.
    env = {**os.environ, "JARVIS_TOAST_TITLE": title, "JARVIS_TOAST_TEXT": text}
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_SCRIPT],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        log.info("Could not show a Windows notification: %s", error)


class Scheduler:
    def __init__(self, memory_store, registry, name: str = "JARVIS", clock=datetime.now,
                 toast=windows_toast):
        self.memory_store = memory_store
        self.registry = registry
        self.name = name
        self.clock = clock
        self.toast = toast
        self.listeners = []          # functions called with each new notification
        self._notifications = []     # the most recent ones, for the web UI
        self._next_id = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_purge = None

    # --- notifications ------------------------------------------------------------

    def notify(self, kind: str, title: str, text: str) -> dict:
        with self._lock:
            note = {"id": self._next_id, "kind": kind, "title": title, "text": text,
                    "time": self.clock().strftime("%I:%M %p").lstrip("0")}
            self._next_id += 1
            self._notifications = (self._notifications + [note])[-KEEP_NOTIFICATIONS:]
        log.info("Notification: %s - %s", title, truncate(text, 200))
        for listener in list(self.listeners):
            try:
                listener(note)
            except Exception:
                log.exception("A notification listener failed")
        if self.toast:
            self.toast(title, truncate(text, 250))
        return note

    def notifications_since(self, after_id: int = 0) -> list[dict]:
        with self._lock:
            return [note for note in self._notifications if note["id"] > after_id]

    @property
    def last_notification_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    # --- running tasks --------------------------------------------------------------

    def run_due(self) -> int:
        """Carry out every task whose time has come. Returns how many ran."""
        now = self.clock()
        tasks = self.memory_store.due_tasks(now)
        for task in tasks:
            try:
                self._run_task(task, now)
            except Exception as error:  # one bad task must not stop the others
                log.exception("Scheduled task #%s crashed", task.id)
                self.memory_store.mark_task(task.id, "failed", f"{type(error).__name__}: {error}")
        if self._last_purge is None or now - self._last_purge > timedelta(hours=1):
            self._last_purge = now
            removed = self.memory_store.purge_old_tasks(now)
            if removed:
                log.info("Removed %s old failed/missed scheduled task(s)", removed)
        return len(tasks)

    def _run_task(self, task, now: datetime) -> None:
        late = now - task.run_at
        if task.kind == "reminder":
            message = task.payload.get("message", "Reminder")
            if late > LATE_NOTE_AFTER:
                message += f"  (this was due {describe_time(task.run_at)}; {self.name} wasn't running then)"
            self.memory_store.delete_task(task.id)
            log.info("Reminder #%s done", task.id)
            self.notify("reminder", "Reminder", message)
            return

        if task.kind != "email":
            self.memory_store.mark_task(task.id, "failed", f"Unknown task type {task.kind!r}")
            return

        to, subject = task.payload.get("to", "?"), task.payload.get("subject", "")
        if late > MAX_LATE:
            reason = (f"Not sent: it was due {describe_time(task.run_at)} but {self.name} wasn't running, "
                      "and it's now too late to be sure it should still go out.")
            self.memory_store.mark_task(task.id, "missed", reason)
            self.notify("error", f"Scheduled email #{task.id} missed", f"Email to {to} (\"{subject}\"). {reason}")
            return

        result = self.registry.execute("send_email", dict(task.payload))
        if result.startswith("Error"):
            self.memory_store.mark_task(task.id, "failed", result)
            self.notify("error", f"Scheduled email #{task.id} FAILED", f"Email to {to} (\"{subject}\"): {result}")
            return
        self.memory_store.delete_task(task.id)
        log.info("Scheduled email #%s sent to %s", task.id, to)
        note = f" ({int(late.total_seconds() // 60)} min late - {self.name} wasn't running)" if late > LATE_NOTE_AFTER else ""
        self.notify("email", "Scheduled email sent", f"Sent to {to}: \"{subject}\"{note}")

    # --- background thread -----------------------------------------------------------

    def start(self, interval: float = CHECK_EVERY_SECONDS) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.run_due()
                except Exception:
                    log.exception("Scheduler check failed")
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self._thread.start()
        pending = [t for t in self.memory_store.list_tasks() if t.status == "pending"]
        log.info("Scheduler started (%s task(s) waiting)", len(pending))

    def stop(self) -> None:
        self._stop.set()
