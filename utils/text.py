"""Small text helpers used by memory and knowledge search."""

import re

# Common words ignored when comparing a question with stored facts.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "please",
    "the", "to", "was", "what", "whats", "when", "where", "which", "who", "why", "will",
    "you", "your", "tell", "about", "remember", "that", "this", "with",
}


def keywords(text: str) -> set[str]:
    """The meaningful lowercase words in `text`."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 1}


def relevance(query: str, text: str) -> int:
    """How many meaningful words `query` and `text` share (0 = unrelated)."""
    return len(keywords(query) & keywords(text))


def truncate(text: str, limit: int) -> str:
    """Shorten `text` to at most `limit` characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text) - limit} more characters]"
