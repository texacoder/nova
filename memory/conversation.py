"""
Short-term conversation memory.

Keeps the messages of the current session in RAM so NOVA can follow the
conversation. It is NOT saved to disk: it disappears when NOVA exits.
Only the most recent `max_messages` are kept so prompts stay small.
"""


class ConversationMemory:
    def __init__(self, max_messages: int = 40):
        self.max_messages = max_messages
        self.messages: list[dict] = []

    def add(self, role: str, content: str, **extra) -> None:
        """Add a message. `extra` holds fields like tool_calls or tool_name."""
        self.messages.append({"role": role, "content": content, **extra})
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
            # Never start the history in the middle of a tool exchange:
            # drop messages until the first remaining one is from the user.
            while self.messages and self.messages[0]["role"] != "user":
                self.messages.pop(0)

    def remove_last(self) -> None:
        if self.messages:
            self.messages.pop()

    def get_messages(self) -> list[dict]:
        # Return copies so callers cannot accidentally change our history.
        return [dict(m) for m in self.messages]

    def clear(self) -> None:
        self.messages = []

    def __len__(self) -> int:
        return len(self.messages)
