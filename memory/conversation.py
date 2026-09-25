"""
Short-term conversation memory.

Keeps the messages of the current session in RAM so NOVA can follow the
conversation. It is NOT saved to disk: it disappears when NOVA exits.
Only the most recent `max_messages` are kept so prompts stay small.
"""


class ConversationMemory:
    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages
        self.messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        # Drop the oldest messages once we are over the limit.
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def remove_last(self) -> None:
        if self.messages:
            self.messages.pop()

    def get_messages(self) -> list[dict]:
        # Return a copy so callers cannot accidentally change our history.
        return [dict(m) for m in self.messages]

    def clear(self) -> None:
        self.messages = []

    def __len__(self) -> int:
        return len(self.messages)
