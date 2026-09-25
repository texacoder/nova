"""
MockBrain: a tiny rule-based stand-in for a real model, used ONLY by the tests.

NOVA itself always uses a real local model. This fake brain lets the test
suite exercise the agent, tools and UI without a model. It understands a few
fixed phrases:

    hello                       -> greeting
    search <something>          -> web_search tool
    learn about <topic>         -> learn_topic tool
    open <app>                  -> open_application tool
    what time is it / date      -> get_datetime tool
    list files                  -> list_directory tool
    any other question          -> looks for a matching memory or earlier message
"""

import re

from brain.base import Brain, BrainReply, BrainStatus, ToolCall, validate_messages
from utils.text import keywords, relevance, truncate

GREETINGS = {"hi", "hello", "hey", "yo", "greetings"}

# The agent lists saved memories/knowledge in the system prompt as "- [id] text".
FACT_LINE = re.compile(r"^- \[K?\d+\] (.+)$", re.MULTILINE)

# (pattern, tool name, function that turns the regex match into arguments)
INTENTS = [
    (r"^(?:search|google|look up)\s+(?:for\s+|the web for\s+)?(.+)", "web_search",
     lambda m: {"query": m.group(1)}),
    (r"^(?:learn|research)\s+(?:about\s+)?(.+)", "learn_topic",
     lambda m: {"topic": m.group(1)}),
    (r"^(?:open|launch|start)\s+(.+)", "open_application",
     lambda m: {"name": m.group(1)}),
    (r"\b(?:time|date|day)\b.*\?|^what time|^what(?:'s| is) the date", "get_datetime",
     lambda m: {}),
    (r"^(?:list|show)\s+(?:my\s+)?(?:files|folder|workspace)", "list_directory",
     lambda m: {"path": "."}),
]


class MockBrain(Brain):
    name = "mock (no AI model)"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> BrainReply:
        validate_messages(messages)
        last = messages[-1]

        # A tool just ran: report its result.
        if last["role"] == "tool":
            if last["content"].startswith("The user declined"):
                return BrainReply("[mock] Understood. I did not do that.")
            return BrainReply("[mock] Result:\n" + truncate(last["content"], 1500))

        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        if not user_messages:
            return BrainReply("[mock] Nothing to respond to.")
        question = user_messages[-1].strip()

        # Used by learn_topic to summarise web pages.
        if question.startswith("Summarize the key facts"):
            sources = question.split("SOURCES:", 1)[-1].strip()
            return BrainReply("[mock summary] " + truncate(sources, 600))

        tool_names = {t["function"]["name"] for t in tools or []}
        for pattern, tool_name, make_args in INTENTS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match and tool_name in tool_names:
                return BrainReply("", [ToolCall(tool_name, make_args(match))])

        if set(re.findall(r"[a-z]+", question.lower())) & GREETINGS:
            return BrainReply("[mock] Hello. I'm NOVA. How can I help?")

        # Facts to search: saved memories/knowledge, then earlier things you said.
        system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
        facts = FACT_LINE.findall(system_text) + user_messages[:-1]
        if keywords(question):
            best = max(facts, key=lambda fact: relevance(question, fact), default=None)
            if best and relevance(question, best) > 0:
                return BrainReply(f"[mock] From what I know: {best}")

        return BrainReply(
            "[mock] I'm running on the mock brain, so I can't really think yet. "
            "Connect a local model (see README) for real answers."
        )

    def health_check(self) -> BrainStatus:
        return BrainStatus(True, "Mock brain active (for testing only; no AI model connected).")
