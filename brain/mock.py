"""
MockBrain: a tiny rule-based stand-in for a real model.

It lets you run and test NOVA with no AI model installed. It is NOT
intelligent: it greets you, and for questions it looks for a remembered
fact or an earlier message that shares words with your question.
Every reply is honest about being from the mock brain.
"""

import re

from brain.base import Brain, BrainStatus, validate_messages

# Common words ignored when matching a question against facts.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "called", "can", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "the", "to", "was", "what", "whats", "when", "where", "which", "who", "why",
    "you", "your", "name", "named", "tell", "about", "remember",
}
GREETINGS = {"hi", "hello", "hey", "yo", "greetings"}

# The agent puts saved memories in the system prompt as lines like "- [3] fact".
MEMORY_LINE = re.compile(r"^- \[\d+\] (.+)$", re.MULTILINE)


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOP_WORDS}


class MockBrain(Brain):
    name = "mock (no AI model)"

    def generate_response(self, messages: list[dict]) -> str:
        validate_messages(messages)

        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        if not user_messages:
            return "[mock] Nothing to respond to."
        question = user_messages[-1]

        if set(re.findall(r"[a-z]+", question.lower())) & GREETINGS:
            return "[mock] Hello. I'm NOVA. How can I help?"

        # Facts to search: saved memories first, then earlier things you said.
        system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
        facts = MEMORY_LINE.findall(system_text) + user_messages[:-1]

        best_fact, best_score = None, 0
        for fact in facts:
            score = len(keywords(question) & keywords(fact))
            if score > best_score:
                best_fact, best_score = fact, score

        if best_fact:
            return f"[mock] From what I know: {best_fact}"
        return (
            "[mock] I'm running on the mock brain, so I can't really think yet. "
            "Connect a local model (see README) for real answers."
        )

    def health_check(self) -> BrainStatus:
        return BrainStatus(True, "Mock brain active (for testing only; no AI model connected).")
