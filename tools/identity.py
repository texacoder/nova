"""Changing the assistant's own name (e.g. "from now on your name is FRIDAY")."""

import re

from brain.setup import save_env_value
from tools.base import Tool, ToolError

VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,29}$")


class SetMyName(Tool):
    name = "set_my_name"
    description = (
        "Change your own name when the user asks you to rename yourself. The new name is used "
        "everywhere: the title of the app, the chat, and how you introduce yourself."
    )
    parameters = {
        "type": "object",
        "properties": {"new_name": {"type": "string", "description": "The new name, e.g. FRIDAY"}},
        "required": ["new_name"],
    }
    requires_confirmation = True

    def describe(self, arguments: dict) -> str:
        return f"Change my name from {self.config.name} to {str(arguments.get('new_name', '')).strip()}"

    def run(self, new_name: str) -> str:
        new_name = new_name.strip().strip("'\"")
        if not VALID_NAME.match(new_name):
            raise ToolError("A name must be 1-30 characters: letters, numbers, spaces, dots, dashes or underscores")
        old_name = self.config.name
        save_env_value(self.config.env_file, "JARVIS_NAME", new_name)
        self.config.name = new_name
        return (f"Renamed from {old_name} to {new_name}. The new name is active now and saved "
                f"for future starts. Introduce yourself as {new_name} from now on.")
