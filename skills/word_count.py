"""Example skill: counts words, lines and characters in a text or a text file."""

from pathlib import Path

from tools.base import Tool, ToolError


class WordCount(Tool):
    name = "word_count"
    description = "Count the words, lines and characters in a piece of text or a text file."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to count (or leave empty and give a path)"},
            "path": {"type": "string", "description": "Optional path to a text file"},
        },
        "required": [],
    }

    def run(self, text: str = "", path: str = "") -> str:
        if path:
            file = Path(path).expanduser()
            if not file.is_absolute():
                file = self.config.workspace / file
            if not file.is_file():
                raise ToolError(f"{file} is not a file")
            text = file.read_text(encoding="utf-8", errors="replace")
        if not text:
            raise ToolError("Give some text or a file path")
        return f"{len(text.split())} words, {len(text.splitlines())} lines, {len(text)} characters"
