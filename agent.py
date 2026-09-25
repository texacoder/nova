"""
The Agent Engine: NOVA's "main loop" logic, independent of the terminal.

For each thing the user types, the agent either:
  - runs a slash command (/remember, /memories, ...), or
  - builds the context (personality + memories + recent conversation)
    and asks the brain for a reply.

Future versions will add tool calling here (see tools/). The CLI in
main.py only handles input/output, so another interface (voice, web UI)
could reuse this class unchanged.
"""

from brain.base import Brain, BrainError
from config import Config
from memory.conversation import ConversationMemory
from memory.database import MemoryStore, MemoryStoreError
from tools.base import ToolRegistry
from utils.logger import get_logger

log = get_logger("agent")

HELP_TEXT = """Commands:
  /help               Show this help
  /remember <fact>    Save a fact to long-term memory
  /memories           List saved memories
  /forget <id>        Delete one memory by its id
  /clear_memory       Delete ALL memories (asks for confirmation)
  /status             Show NOVA's current status
  /exit               Quit NOVA

Anything else is sent to NOVA as a message."""


class Agent:
    def __init__(self, config: Config, brain: Brain, memory_store: MemoryStore, personality: str):
        self.config = config
        self.brain = brain
        self.memory_store = memory_store
        self.personality = personality
        self.conversation = ConversationMemory(config.max_history)
        self.tools = ToolRegistry()  # empty in v0.1
        self.should_exit = False
        self._awaiting_clear_confirmation = False

        self.commands = {
            "/help": self._cmd_help,
            "/remember": self._cmd_remember,
            "/memories": self._cmd_memories,
            "/forget": self._cmd_forget,
            "/clear_memory": self._cmd_clear_memory,
            "/status": self._cmd_status,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
        }

    # --- entry point ------------------------------------------------------

    def handle(self, user_input: str) -> str:
        """Process one line of user input and return NOVA's reply."""
        text = user_input.strip()
        if not text:
            return ""

        if self._awaiting_clear_confirmation:
            return self._finish_clear_memory(text)

        if text.startswith("/"):
            command, _, argument = text.partition(" ")
            handler = self.commands.get(command.lower())
            if handler is None:
                return f"Unknown command: {command}. Type /help for the list of commands."
            try:
                return handler(argument.strip())
            except MemoryStoreError as error:
                return str(error)

        return self._chat(text)

    # --- conversation -----------------------------------------------------

    def build_messages(self) -> list[dict]:
        """The full context sent to the brain: system prompt + recent conversation."""
        system_prompt = self.personality

        memories = self.memory_store.list(limit=self.config.max_memories_in_prompt)
        if memories and self.config.max_memories_in_prompt > 0:
            lines = "\n".join(f"- [{m.id}] {m.content}" for m in memories)
            system_prompt += f"\n\nThings you remember about the user:\n{lines}"

        return [{"role": "system", "content": system_prompt}] + self.conversation.get_messages()

    def _chat(self, text: str) -> str:
        self.conversation.add("user", text)
        try:
            reply = self.brain.generate_response(self.build_messages())
        except BrainError as error:
            # Forget the unanswered message so the history stays consistent.
            self.conversation.remove_last()
            log.warning("Brain error: %s", error)
            return f"My brain is unavailable right now.\n{error}"
        except MemoryStoreError as error:
            self.conversation.remove_last()
            return str(error)

        if not reply:
            reply = "(The model returned an empty reply.)"
        self.conversation.add("assistant", reply)
        return reply

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
        if not argument.isdigit():
            return "Usage: /forget <id>   (see ids with /memories)"
        memory_id = int(argument)
        if self.memory_store.delete(memory_id):
            return f"Forgotten memory #{memory_id}."
        return f"There is no memory #{memory_id}."

    def _cmd_clear_memory(self, _argument: str) -> str:
        count = self.memory_store.count()
        if count == 0:
            return "There are no memories to clear."
        self._awaiting_clear_confirmation = True
        word = "memory" if count == 1 else "memories"
        return f"This will permanently delete {count} saved {word}. Type 'yes' to confirm."

    def _finish_clear_memory(self, text: str) -> str:
        self._awaiting_clear_confirmation = False
        if text.lower() in ("yes", "y"):
            removed = self.memory_store.clear()
            return f"All memories cleared ({removed} removed)."
        return "Cancelled. Your memories were not changed."

    def _cmd_status(self, _argument: str) -> str:
        health = self.brain.health_check()
        return "\n".join([
            f"{self.config.name} v{self.config.version}",
            f"  Brain:        {self.brain.name}",
            f"  Brain status: {'ready' if health.ok else 'NOT ready'} - {health.message.splitlines()[0]}",
            f"  Memories:     {self.memory_store.count()} saved",
            f"  Conversation: {len(self.conversation)} messages this session",
            f"  Tools:        {len(self.tools)} (none in v0.1)",
            f"  Data folder:  {self.config.data_dir}",
            f"  Log file:     {self.config.log_file}",
        ])

    def _cmd_exit(self, _argument: str) -> str:
        self.should_exit = True
        return "Goodbye."
