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


# Everyday action and time words: sharing these says nothing about the topic.
COMMON_WORDS = {
    "send", "open", "show", "make", "write", "create", "delete", "find", "give", "get", "set", "put",
    "want", "need", "like", "know", "now", "today", "tonight", "tomorrow", "yesterday", "time",
    "minute", "minutes", "hour", "hours", "day", "days", "week", "am", "pm", "new", "all", "some",
    "just", "also", "then", "there", "here", "them", "they", "their", "have", "has", "not", "yes",
    "okay", "ok", "thanks", "again", "use", "used", "using", "one", "more", "many", "much", "very",
}


def clearly_related(query: str, topic: str, content: str) -> bool:
    """
    Strict check used before attaching learned knowledge to a question: the topic
    name matches, or at least 2 meaningful words are shared. Numbers and everyday
    words like "today" or "send" don't count.
    """
    words = {w for w in keywords(query) if not w.isdigit() and len(w) > 2 and w not in COMMON_WORDS}
    return bool(words & keywords(topic)) or len(words & keywords(content)) >= 2


def truncate(text: str, limit: int) -> str:
    """Shorten `text` to at most `limit` characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text) - limit} more characters]"
