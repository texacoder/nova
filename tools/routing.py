"""
Choosing which tools to show the model for each message.

Small local models pick the right tool much more reliably when they see a
short list. So everyday tools are always offered, and specialised groups
(email, skills, self-editing, ...) only when the message is about them, or
when they were used recently in the conversation.
"""

CORE_TOOLS = {
    "get_datetime", "web_search", "fetch_webpage", "remember", "recall", "forget", "learn_lesson",
    "find_files", "list_directory", "read_file", "write_file", "delete_file", "open_application", "open_path",
    "run_command",
}

# group -> (tools, words in the user's message that switch the group on)
TOOL_GROUPS = {
    "learning": ({"learn_topic", "save_knowledge"},
                 ["learn", "research", "study", "knowledge", "find out about"]),
    "email": ({"send_email", "read_emails"},
              ["email", "e-mail", "mail", "inbox", "gmail", "outlook"]),
    "system": ({"system_info"},
               ["disk", "storage", "space", "ram", "cpu", "processor", "battery", "system", "specs",
                "my pc", "my computer", "laptop"]),
    "skills": ({"create_skill", "remove_skill", "list_skills"},
               ["skill", "abilit", "new tool", "yourself a", "make yourself", "teach yourself", "can you do"]),
    "self": ({"read_jarvis_source", "modify_jarvis_source"},
             ["source", "your code", "your own code", "your personality", "improve yourself",
              "modify yourself", "change yourself", "update yourself", "upgrade yourself", "fix yourself"]),
    "identity": ({"set_my_name"},
                 ["your name", "call you", "rename you", "rename yourself", "name you"]),
}


def select_tool_names(all_names: list[str], user_text: str, recent_tools: set[str],
                      skill_names: set[str]) -> list[str]:
    """The tools to offer for this message."""
    text = user_text.lower()
    wanted = set(CORE_TOOLS) | set(skill_names)
    for tools, keywords in TOOL_GROUPS.values():
        if any(word in text for word in keywords) or tools & recent_tools:
            wanted |= tools
    grouped = set().union(*(tools for tools, _ in TOOL_GROUPS.values()))
    # Any tool not in a group (e.g. added later) is always offered.
    return [name for name in all_names if name in wanted or name not in grouped]
