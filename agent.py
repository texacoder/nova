"""
The Agent Engine: NOVA's decision loop, independent of any interface.

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
from dataclasses import dataclass, field
from datetime import datetime

from brain.base import Brain, BrainError, BrainUnavailableError, ToolCall
from brain.local import LocalBrain
from brain.setup import SetupManager
from config import Config
from memory.conversation import ConversationMemory
from memory.database import MemoryStore, MemoryStoreError
from tools import Tool, ToolContext, ToolError, create_registry
from personality import load_personality
from tools.knowledge import learn_topic
from utils.logger import get_logger
from utils.text import truncate

log = get_logger("agent")

YES = {"yes", "y", "approve", "ok", "sure"}
NO = {"no", "n", "deny", "cancel"}

HELP_TEXT = """Commands:
  /help               Show this help
  /remember <fact>    Save a fact to long-term memory
  /memories           List saved memories
  /forget <id>        Delete a memory (K<id> = knowledge, L<id> = lesson)
  /clear_memory       Delete ALL memories (asks for confirmation)
  /learn <topic>      Research a topic online and save what NOVA learns
  /knowledge          List what NOVA has learned
  /lessons            List lessons NOVA learned about how to work for you
  /tools              List NOVA's abilities
  /new                Start a fresh conversation (memories are kept)
  /status             Show NOVA's current status
  /setup              Install/repair NOVA's brain automatically
  /exit               Quit NOVA

Anything else is sent to NOVA. Examples:
  search the latest Python release
  write a hello world Python script and open it in Geany
  learn about solar panels
  check my latest emails
  from now on, always answer in short bullet points   (NOVA learns this lesson)"""


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

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "steps": self.steps,
            "pending": vars(self.pending) if self.pending else None,
            "exit": self.exit,
        }


class Agent:
    def __init__(self, config: Config, brain: Brain, memory_store: MemoryStore, personality: str):
        self.config = config
        self.brain = brain
        self.memory_store = memory_store
        self.personality = personality
        self.conversation = ConversationMemory(config.max_history)
        self.tools = create_registry(ToolContext(config, memory_store, brain))
        self.should_exit = False
        self.pending: PendingAction | None = None
        self._queue: list[ToolCall] = []     # tool calls still to run this turn
        self._steps: list[dict] = []         # tools that ran this turn
        self._rounds = 0                     # brain calls this turn
        self._awaiting_clear_confirmation = False
        # Automatic brain installer (only for the real local brain).
        self.setup = SetupManager(brain, config.env_file) if isinstance(brain, LocalBrain) else None
        # Remember the personality file's timestamp so edits are picked up live.
        self._personality_mtime = self._mtime(config.personality_file)

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
            "/new": self._cmd_new,
            "/status": self._cmd_status,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
        }

    # --- entry points -----------------------------------------------------

    def handle(self, user_input: str) -> AgentReply:
        """Process one message from the user."""
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
                return AgentReply(handler(argument.strip()), exit=self.should_exit)
            except (MemoryStoreError, ToolError) as error:
                return AgentReply(str(error))

        self.conversation.add("user", text)
        self._queue, self._steps, self._rounds = [], [], 0
        return self._continue()

    def resolve_pending(self, approved: bool) -> AgentReply:
        """Approve or decline the action NOVA is waiting on, then carry on."""
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

            while self._rounds < self.config.max_tool_steps:
                self._rounds += 1
                reply = self.brain.chat(self.build_messages(), self._tool_schemas())
                if not reply.tool_calls:
                    text = reply.content or "(The model returned an empty reply.)"
                    self.conversation.add("assistant", text)
                    return AgentReply(text, self._steps)

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
        self.conversation.add("tool", result, tool_name=call.name)
        self._steps.append({
            "tool": call.name,
            "arguments": call.arguments,
            "status": status,
            "result": truncate(result, 800),
        })

    def _pending_reply(self) -> AgentReply:
        return AgentReply(f"I need your approval to:\n{self.pending.description}",
                          self._steps, pending=self.pending)

    def _tool_schemas(self) -> list[dict] | None:
        return self.tools.schemas() if self.brain.supports_tools else None

    # --- context ----------------------------------------------------------

    def build_messages(self) -> list[dict]:
        """The full context sent to the brain: system prompt + recent conversation."""
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
                sections.append("Things you remember about the user:\n" +
                                "\n".join(f"- [{m.id}] {m.content}" for m in memories))

        knowledge = self.memory_store.search_knowledge(latest_question, limit=3)
        if knowledge:
            sections.append("Relevant things you learned earlier (from the internet):\n" + "\n".join(
                f"- [K{k.id}] {k.topic}: {truncate(k.content, 1200)} (sources: {k.source})"
                for k in knowledge))

        return [{"role": "system", "content": "\n\n".join(sections)}] + history

    @staticmethod
    def _mtime(path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_personality_if_changed(self) -> None:
        mtime = self._mtime(self.config.personality_file)
        if mtime and mtime != self._personality_mtime:
            self._personality_mtime = mtime
            self.personality = load_personality(self.config.personality_file, self.config.name, self.config.version)
            log.info("Personality file changed; reloaded")

    def _situation(self) -> str:
        tools_note = ("You can act using your tools." if self.brain.supports_tools
                      else "Tools are NOT available with the current model, so you cannot act.")
        return "\n".join([
            "Current situation:",
            f"- Date and time: {datetime.now().strftime('%A %d %B %Y, %H:%M')}",
            f"- Operating system: {platform.system()} {platform.release()}",
            f"- Your workspace folder: {self.config.workspace}",
            f"- Email: {'configured for ' + self.config.email_address if self.config.email_configured else 'not configured'}",
            f"- {tools_note}",
        ])

    # --- commands ---------------------------------------------------------

    def _cmd_help(self, _argument: str) -> str:
        return HELP_TEXT

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
            lines.append(f"  {name:<17} {gate:<14} {truncate(tool.description, 70).splitlines()[0]}")
        return "NOVA's abilities:\n" + "\n".join(lines)

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
            "Tools": f"{len(self.tools)}" + ("" if self.brain.supports_tools else " (model can't use tools)"),
            "Memories": f"{self.memory_store.count()} saved",
            "Knowledge": f"{self.memory_store.count_knowledge()} topics learned",
            "Lessons": f"{len(self.memory_store.list_lessons())} learned",
            "Conversation": f"{len(self.conversation)} messages this session",
            "Email": self.config.email_address if self.config.email_configured else "not configured",
            "Workspace": str(self.config.workspace),
            "Log file": str(self.config.log_file),
        }

    def _cmd_exit(self, _argument: str) -> str:
        self.should_exit = True
        return "Goodbye."
