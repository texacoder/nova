"""
Doing things later: scheduled emails, reminders and timers.

The model passes the user's own words for the time ("tomorrow at 6 am",
"in 10 minutes"); utils/when.py turns them into an exact date and time, so
the model never has to do date arithmetic.

Tasks are saved in the database (table `scheduled`) and carried out by the
scheduler (scheduler.py) while JARVIS is running. A task is deleted from the
database as soon as it has run.
"""

from tools.base import Tool, ToolError
from tools.email_tools import NOT_CONFIGURED
from utils.text import truncate
from utils.when import WhenError, describe_time, parse_when

WHEN_PARAM = {
    "type": "string",
    "description": "When, in the user's own words, e.g. 'tomorrow at 6 am', 'in 10 minutes', 'friday 5 pm'",
}


def _parse(when: str):
    try:
        return parse_when(str(when))
    except WhenError as error:
        raise ToolError(str(error))
    except ValueError as error:  # e.g. 31/02
        raise ToolError(f"'{when}' isn't a real date ({error}).")


def describe_task(task) -> str:
    """One line describing a scheduled task, e.g. for /scheduled."""
    when = describe_time(task.run_at)
    if task.kind == "email":
        what = f"email to {task.payload.get('to', '?')}: \"{truncate(task.payload.get('subject', ''), 60)}\""
    else:
        what = f"reminder: \"{truncate(task.payload.get('message', ''), 80)}\""
    status = "" if task.status == "pending" else f"  [{task.status.upper()}: {truncate(task.result, 120)}]"
    return f"#{task.id}  {when}  {what}{status}"


def format_task_list(tasks) -> str:
    if not tasks:
        return "Nothing is scheduled."
    return "Scheduled:\n" + "\n".join(describe_task(task) for task in tasks)


class ScheduleEmail(Tool):
    name = "schedule_email"
    description = ("Send an email LATER, at a given time (e.g. 'tomorrow at 6 am'). The user approves it now; "
                   "it is sent automatically at that time while JARVIS is running.")
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address(es), comma-separated"},
            "subject": {"type": "string", "description": "Subject line"},
            "body": {"type": "string", "description": "Plain-text message"},
            "when": WHEN_PARAM,
        },
        "required": ["to", "subject", "body", "when"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        try:
            when = describe_time(parse_when(str(arguments.get("when", ""))))
        except ValueError:
            when = f"{arguments.get('when', '')!r} (not understood - this will fail)"
        return (
            f"Schedule email\nSend at: {when}\n"
            f"From: {self.config.email_address or '(not configured)'}\n"
            f"To: {arguments.get('to', '')}\nSubject: {arguments.get('subject', '')}\n\n"
            f"{truncate(str(arguments.get('body', '')), 1500)}"
        )

    def run(self, to: str, subject: str, body: str, when: str) -> str:
        if not self.config.email_configured:
            raise ToolError(NOT_CONFIGURED)
        if "@" not in to:
            raise ToolError(f"'{to}' is not an email address.")
        run_at = _parse(when)
        task = self.context.memory_store.add_task("email", run_at, {"to": to, "subject": subject, "body": body})
        return (f"Scheduled email #{task.id} to {to} for {describe_time(run_at)}. "
                "It will be sent automatically if JARVIS is running then.")


class SetReminder(Tool):
    name = "set_reminder"
    description = ("Remind the user of something later, or set a timer (e.g. 'in 10 minutes', 'tomorrow at 9 am'). "
                   "JARVIS shows and speaks the reminder at that time.")
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "What to remind the user about, e.g. 'call mom' or 'Timer done'"},
            "when": WHEN_PARAM,
        },
        "required": ["message", "when"],
    }

    def run(self, message: str, when: str) -> str:
        message = message.strip() or "Reminder"
        run_at = _parse(when)
        task = self.context.memory_store.add_task("reminder", run_at, {"message": message})
        return f"Reminder #{task.id} set for {describe_time(run_at)}: \"{message}\"."


class ListScheduled(Tool):
    name = "list_scheduled"
    description = "List scheduled emails and reminders (with their numbers, for cancelling)."
    parameters = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        return format_task_list(self.context.memory_store.list_tasks())


class CancelScheduled(Tool):
    name = "cancel_scheduled"
    description = "Cancel a scheduled email or reminder by its number (see list_scheduled)."
    parameters = {
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "The task number, e.g. 3"}},
        "required": ["id"],
    }

    def run(self, id: int) -> str:
        store = self.context.memory_store
        task = store.get_task(int(id))
        if task is None:
            raise ToolError(f"There is no scheduled task #{id}. Use list_scheduled to see them.")
        store.delete_task(task.id)
        return f"Cancelled: {describe_task(task)}"
