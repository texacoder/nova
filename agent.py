"""
The Agent Engine: JARVIS's decision loop, independent of any interface.

For each message you send, the agent:
  1. Runs slash commands (/remember, /learn, ...) directly, or
  2. Builds the context (personality + relevant memories + knowledge +
     recent conversation) and asks the brain what to do.
  3. If the brain asks for tools (search, open app, send email...), the
     agent runs them, gives the results back to the brain, and repeats
     until the brain gives a final answer.
  4. Risky tools pause the loop and wait for your approval
     (see `pending` and `resolve_pending`).

The terminal (main.py) and the web UI (ui/server.py) both use this class.
"""

import platform
import re
from dataclasses import dataclass, field
from datetime import datetime

from brain.base import Brain, BrainError, BrainUnavailableError, ToolCall
from brain.local import LocalBrain
from brain.setup import SetupManager
from config import PROJECT_ROOT, Config
from memory.conversation import ConversationMemory
from memory.database import MemoryStore, MemoryStoreError
from tools import Tool, ToolContext, ToolError, create_registry
from personality import load_personality
from scheduler import Scheduler
from tools.scheduling import format_task_list
from tools.knowledge import learn_topic
from tools.routing import select_tool_names
from tools.self_modify import load_changes, rollback_last_change
from utils import autostart
from utils.logger import get_logger
from utils.text import truncate

log = get_logger("agent")

MAX_NUDGES = 2  # times per message JARVIS may ask the model to correct itself

NUDGE_EMPTY = ("(Automatic note from the system: your last reply was empty. Answer the user now, "
               "or call a tool if an action is needed.)")
NUDGE_SKILL = ("(Automatic note from the system: you wrote skill code as text, but nothing was "
               "installed. Call the create_skill tool now with the complete, corrected code. Don't "
               "show code to the user instead of calling the tool.)")

VOICE_NOTE = ("(The user is talking to you by voice and will hear your reply spoken aloud: answer in "
              "1-3 short, natural sentences. No lists, tables, links or code unless they ask for them.)")

FAILED_NOTE = ("(This step FAILED. You have no results from it - do not make any up. Tell the user it "
               "failed and why, or try a different way.)")

# A reply claiming success, and words that admit failure (used by the made-up-results guard).
CLAIMS_SUCCESS = re.compile(
    r"\b(i found|found (these|the|it|them|your)|here (is|are)|done|deleted|removed|opened|created|saved|"
    r"sent|moved|successfully|i've|i have|completed|it's open|is open)\b", re.IGNORECASE)
ADMITS_FAILURE = re.compile(
    r"\b(fail|failed|couldn't|could not|can't|cannot|unable|not able|didn't|did not|denied|error|"
    r"not found|no (file|files|match|results)|doesn't exist|does not exist|wasn't|was not)\b", re.IGNORECASE)

# Questions about the assistant's memory, answered straight from the database (no model involved).
MEMORY_QUESTION = re.compile(
    r"^\s*(?:(?:show|list|display|give|tell)(?: me)?\s+(?:all\s+)?(?:of\s+)?(?:your|my|the)?\s*(?:saved\s+)?"
    r"memor(?:y|ies)|(?:what are|what's in)\s+(?:your|my)\s+(?:saved\s+)?memor(?:y|ies)|"
    r"(?:your|my)?\s*saved\s+memor(?:y|ies)|your memor(?:y|ies)|"
    r"what do you (?:remember|know) about me|what have you (?:remembered|saved) about me)\s*[?.!]*\s*$",
    re.IGNORECASE)

# Commands whose output stays in the conversation, so "delete that skill" or
# "cancel the second one" right after them makes sense to the model.
FOLLOW_UP_COMMANDS = {"/memories", "/knowledge", "/lessons", "/skills", "/tools", "/scheduled"}
FOLLOW_UP_CHARS = 1500

YES = {"yes", "y", "approve", "ok", "sure"}
NO = {"no", "n", "deny", "cancel"}

HELP_TEXT = """Commands:
  /help               Show this help
  /remember <fact>    Save a fact to long-term memory
  /memories           List saved memories
  /forget <id>        Delete a memory (K<id> = knowledge, L<id> = lesson)
  /clear_memory       Delete ALL memories (asks for confirmation)
  /learn <topic>      Research a topic online and save what {name} learns
  /knowledge          List what {name} has learned
  /lessons            List lessons {name} learned about how to work for you
  /tools              List {name}'s abilities
  /skills             List abilities {name} wrote for itself
  /scheduled          List scheduled emails and reminders
  /cancel <id>        Cancel a scheduled email or reminder
  /autostart on|off   Start {name} automatically when Windows starts
  /rollback           Undo {name}'s most recent change to its own code
  /restart            Restart {name} (activates changes to its own code)
  /new                Start a fresh conversation (memories are kept)
  /status             Show {name}'s current status
  /setup              Install/repair {name}'s brain automatically
  /exit               Quit {name}

Anything else is sent to {name}. Examples:
  search the latest Python release
  write a hello world Python script and open it in Geany
  learn about solar panels
  check my latest emails
  email bob@example.com tomorrow at 6 am saying the report is ready
  remind me in 20 minutes to check the oven
  from now on, always answer in short bullet points   ({name} learns this lesson)
  make yourself a skill that converts CSV files to JSON ({name} writes new code for itself)"""


@dataclass
class PendingAction:
    """A tool call waiting for the user's approval."""

    tool: str
    arguments: dict
    description: str


@dataclass
class AgentReply:
    """Everything an interface needs to show after one step."""

    text: str
    steps: list[dict] = field(default_factory=list)  # tools that ran this turn
    pending: PendingAction | None = None
    exit: bool = False
    restart: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "steps": self.steps,
            "pending": vars(self.pending) if self.pending else None,
            "exit": self.exit,
            "restart": self.restart,
        }


class Agent:
    def __init__(self, config: Config, brain: Brain, memory_store: MemoryStore, personality: str):
        self.config = config
        self.brain = brain
        self.memory_store = memory_store
        self.personality = personality
        self.conversation = ConversationMemory(config.max_history)
        self.tools = create_registry(ToolContext(config, memory_store, brain))
        # Carries out scheduled emails/reminders; main.py starts its thread.
        self.scheduler = Scheduler(memory_store, self.tools, config.name)
        self.should_exit = False
        self.restart_requested = False
        self.pending: PendingAction | None = None
        self._queue: list[ToolCall] = []     # tool calls still to run this turn
        self._steps: list[dict] = []         # tools that ran this turn
        self._rounds = 0                     # brain calls this turn
        self._nudges = 0                     # self-corrections requested this turn
        self._on_event = None                # where to send live updates (streaming)
        self._voice = False                  # this turn came from voice chat
        self._awaiting_clear_confirmation = False
        # Automatic brain installer (only for the real local brain).
        self.setup = SetupManager(brain, config.env_file) if isinstance(brain, LocalBrain) else None
        # Remember the personality file's timestamp so edits are picked up live.
        self._personality_mtime = self._mtime(config.personality_file)
        self._personality_name = config.name

        self.commands = {
            "/help": self._cmd_help,
            "/remember": self._cmd_remember,
            "/memories": self._cmd_memories,
            "/forget": self._cmd_forget,
            "/clear_memory": self._cmd_clear_memory,
            "/learn": self._cmd_learn,
            "/knowledge": self._cmd_knowledge,
            "/lessons": self._cmd_lessons,
            "/setup": self._cmd_setup,
            "/tools": self._cmd_tools,
            "/skills": self._cmd_skills,
            "/scheduled": self._cmd_scheduled,
            "/cancel": self._cmd_cancel,
            "/autostart": self._cmd_autostart,
            "/rollback": self._cmd_rollback,
            "/restart": self._cmd_restart,
            "/new": self._cmd_new,
            "/status": self._cmd_status,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
        }

    # --- entry points -----------------------------------------------------

    def handle(self, user_input: str, on_event=None, voice: bool = False) -> AgentReply:
        """
        Process one message from the user.

        on_event(dict) receives live updates while JARVIS works:
          {"type": "token", "text": ...}  a new piece of the reply as it's written
          {"type": "reset"}               discard the text streamed so far
          {"type": "step", ...}           a tool finished
        voice=True asks for short, speakable answers (voice chat).
        """
        self._on_event, self._voice = on_event, voice
        text = user_input.strip()
        if not text:
            return AgentReply("")

        if self.pending:
            if text.lower() in YES | NO:
                return self.resolve_pending(text.lower() in YES)
            return AgentReply("Please answer yes or no to the pending action first.", pending=self.pending)

        if self._awaiting_clear_confirmation:
            return AgentReply(self._finish_clear_memory(text))

        if text.startswith("/"):
            command, _, argument = text.partition(" ")
            handler = self.commands.get(command.lower())
            if handler is None:
                return AgentReply(f"Unknown command: {command}. Type /help for the list of commands.")
            try:
                answer = handler(argument.strip())
            except (MemoryStoreError, ToolError, autostart.AutostartError) as error:
                return AgentReply(str(error))
            if command.lower() in FOLLOW_UP_COMMANDS:
                self.conversation.add("user", text)
                self.conversation.add("assistant", truncate(answer, FOLLOW_UP_CHARS))
            return AgentReply(answer, exit=self.should_exit, restart=self.restart_requested)

        if MEMORY_QUESTION.match(text):
            answer = self._describe_memories()
            self.conversation.add("user", text)
            self.conversation.add("assistant", answer)
            return AgentReply(answer)

        self.conversation.add("user", text)
        self._queue, self._steps, self._rounds, self._nudges = [], [], 0, 0
        return self._continue()

    def resolve_pending(self, approved: bool, on_event=None) -> AgentReply:
        """Approve or decline the action JARVIS is waiting on, then carry on."""
        self._on_event = on_event
        if not self.pending:
            return AgentReply("There is nothing waiting for approval.")
        call = self._queue.pop(0)
        self.pending = None
        if approved:
            self._execute(call)
        else:
            log.info("User declined %s", call.name)
            self._record(call, "The user declined this action. Do not try it again unless they ask.", "declined")
        return self._continue()

    # --- the tool loop ----------------------------------------------------

    def _continue(self) -> AgentReply:
        try:
            if self._run_queue():
                return self._pending_reply()

            correction: list[dict] = []  # temporary messages asking the model to fix a slip
            while self._rounds < self.config.max_tool_steps:
                self._rounds += 1
                reply = self._ask_brain(self.build_messages() + correction)
                correction = []
                if not reply.tool_calls:
                    nudge = self._needs_nudge(reply.content)
                    if nudge and self._nudges < MAX_NUDGES:
                        self._emit({"type": "reset"})
                        self._nudges += 1
                        log.info("Asking the model to correct itself: %s", nudge[:60])
                        if reply.content:
                            correction.append({"role": "assistant", "content": reply.content})
                        correction.append({"role": "user", "content": nudge})
                        continue
                    text = reply.content or "I couldn't come up with an answer to that. Could you rephrase it?"
                    text = self._replace_made_up_results(text)
                    self.conversation.add("assistant", text)
                    return AgentReply(text + self._not_done_note(), self._steps)

                self._emit({"type": "reset"})  # text before tool calls isn't the final answer
                self.conversation.add(
                    "assistant", reply.content,
                    tool_calls=[call.to_message_format() for call in reply.tool_calls],
                )
                self._queue = list(reply.tool_calls)
                if self._run_queue():
                    return self._pending_reply()

            text = (f"I stopped after {self.config.max_tool_steps} steps without finishing. "
                    "Tell me how you'd like to continue.")
            self.conversation.add("assistant", text)
            return AgentReply(text, self._steps)

        except BrainError as error:
            log.warning("Brain error: %s", error)
            self._queue, self.pending = [], None
            if not self._steps and self.conversation.messages[-1:] and \
                    self.conversation.messages[-1]["role"] == "user":
                self.conversation.remove_last()  # nothing happened; forget the message
            if isinstance(error, BrainUnavailableError) and self.setup:
                return AgentReply(
                    "My brain (the local AI model) isn't ready yet. Type /setup and I'll install and "
                    f"start it automatically.\n\nDetails: {error.args[0].splitlines()[0]}", self._steps)
            if isinstance(error, BrainUnavailableError):
                return AgentReply(f"My brain is unavailable right now.\n{error}", self._steps)
            return AgentReply(f"My brain ran into a problem: {error}", self._steps)
        except MemoryStoreError as error:
            return AgentReply(str(error), self._steps)

    def _emit(self, event: dict) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:  # a disconnected browser must not break the agent
                log.debug("Could not deliver live update", exc_info=True)

    def _ask_brain(self, messages: list[dict]):
        """Call the brain, streaming its words to the interface when possible."""
        if self._on_event and self.brain.supports_streaming:
            return self.brain.chat(messages, self._tool_schemas(),
                                   on_token=lambda text: self._emit({"type": "token", "text": text}))
        return self.brain.chat(messages, self._tool_schemas())

    def warm_up(self) -> None:
        """Load the model and pre-read the instructions so the first reply is fast."""
        if hasattr(self.brain, "warm_up") and self.brain.health_check().ok:
            self.brain.warm_up(self.build_messages() + [{"role": "user", "content": "Hello"}],
                               self._tool_schemas())

    def _run_queue(self) -> bool:
        """Run queued tool calls. Returns True if one is waiting for approval."""
        while self._queue:
            call = self._queue[0]
            if self.tools.needs_confirmation(call.name, call.arguments):
                self.pending = PendingAction(call.name, call.arguments,
                                             self.tools.describe(call.name, call.arguments))
                log.info("Waiting for approval: %s", call.name)
                return True
            self._queue.pop(0)
            self._execute(call)
        return False

    def _execute(self, call: ToolCall) -> None:
        result = self.tools.execute(call.name, call.arguments)
        self._record(call, result, "error" if result.startswith("Error:") else "ok")

    def _record(self, call: ToolCall, result: str, status: str) -> None:
        # The model gets an explicit reminder after a failure, so it doesn't invent results.
        for_model = f"{result}\n{FAILED_NOTE}" if status == "error" else result
        self.conversation.add("tool", for_model, tool_name=call.name)
        step = {
            "tool": call.name,
            "arguments": call.arguments,
            "status": status,
            "result": truncate(result, 800),
        }
        self._steps.append(step)
        self._emit({"type": "step", **step})

    def _describe_memories(self) -> str:
        """A plain listing of what the assistant remembers (used for 'show your memories')."""
        memories = self.memory_store.list()
        lessons = self.memory_store.list_lessons()
        topics = [k.topic for k in self.memory_store.list_knowledge()]
        if not (memories or lessons or topics):
            return ("I don't have any saved memories yet. Tell me something about yourself and I'll "
                    "remember it, or use /remember <fact>.")
        parts = []
        if memories:
            parts.append("Here's what I remember about you:\n" +
                         "\n".join(f"- [{m.id}] {m.content}" for m in memories))
        if lessons:
            parts.append("How you like me to work:\n" + "\n".join(f"- [L{x.id}] {x.content}" for x in lessons))
        if topics:
            parts.append("Topics I've learned about: " + ", ".join(topics))
        parts.append("To remove something, say \"forget memory 3\" (or /forget 3).")
        return "\n\n".join(parts)

    def _replace_made_up_results(self, text: str) -> str:
        """
        If every action this turn failed but the reply claims success ("I found...",
        "Done"), the model made the results up. Replace it with the truth.
        """
        if not self._steps or any(step["status"] == "ok" for step in self._steps):
            return text
        if not CLAIMS_SUCCESS.search(text) or ADMITS_FAILURE.search(text):
            return text
        log.warning("Replaced a reply that claimed success after failed actions: %r", text[:200])
        problems = []
        for step in self._steps:
            if step["status"] == "declined":
                problems.append(f"{step['tool']} was not done because you denied it")
            else:
                reason = step["result"].removeprefix("Error:").strip().splitlines()[0][:200]
                problems.append(f"{step['tool']} failed: {reason}")
        return ("Sorry - that didn't work, so I have no results to show you.\n" +
                "\n".join(f"- {p}" for p in problems) +
                "\nTell me more (for example the exact name or folder) and I'll try again.")

    def _not_done_note(self) -> str:
        """
        Honesty guard that doesn't rely on the model: list actions this turn that were
        denied or failed (and not later done successfully), so a reply can never pretend.
        """
        succeeded_later = set()
        problems = []
        for step in reversed(self._steps):
            if step["status"] == "ok":
                succeeded_later.add(step["tool"])
            elif step["tool"] not in succeeded_later:
                reason = "you denied it" if step["status"] == "declined" else "it failed"
                problems.append(f"{step['tool']} ({reason})")
        if not problems:
            return ""
        return "\n\n⚠ Not done: " + ", ".join(reversed(problems))

    def _needs_nudge(self, text: str) -> str | None:
        """Spot common model slips that it can fix itself if asked."""
        if not text.strip():
            return NUDGE_EMPTY
        wrote_skill_as_text = "```" in text and re.search(r"class\s+\w+\s*\(\s*Tool\s*\)", text)
        installed = any(s["tool"] == "create_skill" and s["status"] == "ok" for s in self._steps)
        if wrote_skill_as_text and not installed and self.brain.supports_tools \
                and self.tools.get("create_skill"):
            return NUDGE_SKILL
        return None

    def _pending_reply(self) -> AgentReply:
        return AgentReply(f"I need your approval to:\n{self.pending.description}",
                          self._steps, pending=self.pending)

    def _tool_schemas(self) -> list[dict] | None:
        """The tools offered for this message: everyday ones plus any the message is about."""
        if not self.brain.supports_tools:
            return None
        history = self.conversation.get_messages()
        questions = [m["content"] for m in history if m["role"] == "user"]
        latest = questions[-1] if questions else ""
        if len(questions) > 1 and questions[-2].startswith("/"):
            latest += " " + questions[-2]  # "delete that one" right after /skills is about skills
        recent = {m.get("tool_name", "") for m in history[-12:] if m["role"] == "tool"}
        names = select_tool_names(self.tools.names(), latest, recent, self.tools.skill_names)
        return [self.tools.get(name).schema() for name in names]

    # --- context ----------------------------------------------------------

    def build_messages(self) -> list[dict]:
        """
        The full context sent to the brain: system prompt + recent conversation.

        For speed, the system prompt only contains things that rarely change, so
        Ollama can reuse its work from the previous message instead of re-reading
        thousands of words. Per-question context (relevant knowledge, voice mode)
        is attached to the latest user message instead.
        """
        history = self.conversation.get_messages()
        latest_question = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

        self._reload_personality_if_changed()
        sections = [self.personality, self._situation()]

        lessons = self.memory_store.list_lessons()
        if lessons:
            sections.append("Lessons you have learned about working for this user (always follow them):\n" +
                            "\n".join(f"- [L{lesson.id}] {lesson.content}" for lesson in lessons[-40:]))

        limit = self.config.max_memories_in_prompt
        if limit > 0:
            if self.memory_store.count() <= limit:
                memories = self.memory_store.list()
            else:
                memories = self.memory_store.search(latest_question, limit)
            if memories:
                sections.append(
                    "Facts about the user (the one person you work for). The user told you these, so "
                    "\"I\", \"me\" and \"my\" in them mean the USER, not you. Use them to answer questions "
                    "about the user, such as \"who am I?\" or \"what's my name?\":\n" +
                    "\n".join(f"- [{m.id}] {m.content}" for m in memories))

        extra = []
        knowledge = self.memory_store.search_knowledge(latest_question, limit=3)
        if knowledge:
            extra.append("(Relevant things you learned earlier from the internet:\n" + "\n".join(
                f"- [K{k.id}] {k.topic}: {truncate(k.content, 1200)} (sources: {k.source})"
                for k in knowledge) + ")")
        if self._voice:
            extra.append(VOICE_NOTE)
        last_user = max((i for i, m in enumerate(history) if m["role"] == "user"), default=None)
        if extra and last_user is not None:
            history[last_user]["content"] += "\n\n" + "\n\n".join(extra)

        return [{"role": "system", "content": "\n\n".join(sections)}] + history

    @staticmethod
    def _mtime(path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_personality_if_changed(self) -> None:
        mtime = self._mtime(self.config.personality_file)
        renamed = self.config.name != self._personality_name
        if (mtime and mtime != self._personality_mtime) or renamed:
            self._personality_mtime = mtime
            self._personality_name = self.config.name
            self.personality = load_personality(self.config.personality_file, self.config.name, self.config.version)
            log.info("Personality file changed; reloaded")

    def _situation(self) -> str:
        tools_note = ("You can act using your tools." if self.brain.supports_tools
                      else "Tools are NOT available with the current model, so you cannot act.")
        return "\n".join([
            "Current situation:",
            f"- Today's date: {datetime.now().strftime('%A %d %B %Y')} (use get_datetime for the exact time)",
            f"- Operating system: {platform.system()} {platform.release()}",
            f"- Your workspace folder: {self.config.workspace}",
            f"- Email: {'configured for ' + self.config.email_address if self.config.email_configured else 'not configured'}",
            f"- {tools_note}",
        ])

    # --- commands ---------------------------------------------------------

    def _cmd_help(self, _argument: str) -> str:
        return HELP_TEXT.replace("{name}", self.config.name)

    def _cmd_remember(self, argument: str) -> str:
        if not argument:
            return "Usage: /remember <fact>   e.g. /remember My store is called EXORASTORE."
        memory = self.memory_store.add(argument)
        return f"I'll remember that. (memory #{memory.id})"

    def _cmd_memories(self, _argument: str) -> str:
        memories = self.memory_store.list()
        if not memories:
            return "I have no saved memories yet. Use /remember <fact> to add one."
        lines = [f"  [{m.id}] {m.content}   ({m.created_at[:10]})" for m in memories]
        return f"Saved memories ({len(memories)}):\n" + "\n".join(lines)

    def _cmd_forget(self, argument: str) -> str:
        arg = argument.strip().upper()
        if arg.startswith("L") and arg[1:].isdigit():
            if self.memory_store.delete_lesson(int(arg[1:])):
                return f"Forgotten lesson #{arg}."
            return f"There is no lesson #{arg}."
        if arg.startswith("K") and arg[1:].isdigit():
            if self.memory_store.delete_knowledge(int(arg[1:])):
                return f"Forgotten knowledge #{arg}."
            return f"There is no knowledge #{arg}."
        if not arg.isdigit():
            return "Usage: /forget <id>, /forget K<id> or /forget L<id>   (see /memories, /knowledge, /lessons)"
        if self.memory_store.delete(int(arg)):
            return f"Forgotten memory #{arg}."
        return f"There is no memory #{arg}."

    def _cmd_clear_memory(self, _argument: str) -> str:
        count = self.memory_store.count()
        if count == 0:
            return "There are no memories to clear."
        self._awaiting_clear_confirmation = True
        word = "memory" if count == 1 else "memories"
        return f"This will permanently delete {count} saved {word}. Type 'yes' to confirm."

    def _finish_clear_memory(self, text: str) -> str:
        self._awaiting_clear_confirmation = False
        if text.lower() in YES:
            removed = self.memory_store.clear()
            return f"All memories cleared ({removed} removed)."
        return "Cancelled. Your memories were not changed."

    def _cmd_learn(self, argument: str) -> str:
        if not argument:
            return "Usage: /learn <topic>   e.g. /learn how solar panels work"
        entry = learn_topic(argument, self.brain, self.memory_store)
        return f"Learned about '{entry.topic}' (knowledge #K{entry.id}):\n{entry.content}\n\nSources: {entry.source}"

    def _cmd_knowledge(self, _argument: str) -> str:
        items = self.memory_store.list_knowledge()
        if not items:
            return "I haven't learned anything yet. Try /learn <topic>."
        lines = [f"  [K{k.id}] {k.topic}: {truncate(' '.join(k.content.split()), 120)}" for k in items]
        return f"Knowledge base ({len(items)}):\n" + "\n".join(lines)

    def _cmd_lessons(self, _argument: str) -> str:
        lessons = self.memory_store.list_lessons()
        if not lessons:
            return ("No lessons yet. Correct me or tell me how you like things done "
                    "(e.g. 'from now on, keep answers short') and I'll learn it.")
        return f"Lessons ({len(lessons)}):\n" + "\n".join(f"  [L{x.id}] {x.content}" for x in lessons)

    def _cmd_setup(self, _argument: str) -> str:
        if not self.setup:
            return "Automatic setup is only available for the local Ollama brain."
        if self.setup.start():
            return "Setting up my brain in the background. Progress is shown in the setup panel."
        return "Setup is already running."

    def _cmd_tools(self, _argument: str) -> str:
        lines = []
        for name in self.tools.names():
            tool = self.tools.get(name)
            if name in self.config.auto_approve:
                gate = "auto-approved"
            elif tool.requires_confirmation:
                gate = "always asks"
            elif type(tool).needs_confirmation is not Tool.needs_confirmation:
                gate = "asks if risky"
            else:
                gate = "free"
            if name in self.tools.skill_names:
                gate += " (skill)"
            lines.append(f"  {name:<17} {gate:<14} {truncate(tool.description, 70).splitlines()[0]}")
        return f"{self.config.name}'s abilities:\n" + "\n".join(lines)

    def _cmd_skills(self, _argument: str) -> str:
        text = self.tools.execute("list_skills", {})
        if self.tools.skill_errors:
            text += "\n\nSkills that failed to load (see the log):\n" + "\n".join(f"  {e}" for e in self.tools.skill_errors)
        return text

    def _cmd_scheduled(self, _argument: str) -> str:
        text = format_task_list(self.memory_store.list_tasks())
        if text.startswith("Nothing"):
            return (text + " Try: \"remind me in 10 minutes to stretch\" or "
                    "\"email bob@example.com tomorrow at 6 am saying hello\".")
        return text + "\n\nCancel one with /cancel <number> (or just ask me)."

    def _cmd_cancel(self, argument: str) -> str:
        number = argument.strip().lstrip("#")
        if not number.isdigit():
            return "Usage: /cancel <number>   (see /scheduled)"
        result = self.tools.execute("cancel_scheduled", {"id": int(number)})
        return result.removeprefix("Error: ")

    def _cmd_autostart(self, argument: str) -> str:
        choice = argument.strip().lower()
        if choice in ("on", "yes", "enable"):
            path = autostart.enable(PROJECT_ROOT)
            return (f"Done - {self.config.name} will start automatically (minimized, no browser tab) when you "
                    f"log in to Windows, so scheduled emails and reminders work.\n"
                    f"Startup file: {path}\nTurn it off with /autostart off.")
        if choice in ("off", "no", "disable"):
            if autostart.disable():
                return f"Done - {self.config.name} will no longer start with Windows."
            return f"{self.config.name} wasn't set to start with Windows."
        state = "ON" if autostart.is_enabled() else "OFF"
        return f"Start with Windows is {state}. Use /autostart on or /autostart off."

    def _cmd_rollback(self, _argument: str) -> str:
        return rollback_last_change(self.config)

    def _cmd_restart(self, _argument: str) -> str:
        self.should_exit = True
        self.restart_requested = True
        return f"Restarting {self.config.name}..."

    def _cmd_new(self, _argument: str) -> str:
        self.conversation.clear()
        return "Started a fresh conversation. Long-term memories are kept."

    def _cmd_status(self, _argument: str) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.status().items())

    def status(self) -> dict:
        health = self.brain.health_check()
        return {
            "Version": f"{self.config.name} v{self.config.version}",
            "Brain": self.brain.name,
            "Brain status": ("ready" if health.ok else "NOT ready") + " - " + health.message.splitlines()[0],
            "Tools": f"{len(self.tools)}, {len(self.tools.skill_names)} self-written"
                     + ("" if self.brain.supports_tools else " (model can't use tools)"),
            "Self-edits": f"{len(load_changes(self.config))} changes to its own code",
            "Memories": f"{self.memory_store.count()} saved",
            "Knowledge": f"{self.memory_store.count_knowledge()} topics learned",
            "Lessons": f"{len(self.memory_store.list_lessons())} learned",
            "Scheduled": f"{sum(t.status == 'pending' for t in self.memory_store.list_tasks())} waiting",
            "Start with Windows": "on" if autostart.is_enabled() else "off",
            "Conversation": f"{len(self.conversation)} messages this session",
            "Email": self.config.email_address if self.config.email_configured else "not configured",
            "Workspace": str(self.config.workspace),
            "Log file": str(self.config.log_file),
        }

    def _cmd_exit(self, _argument: str) -> str:
        self.should_exit = True
        return "Goodbye."
