"""
NOVA's tools (abilities).

`create_registry(context)` builds the list of tools NOVA may use.
To add a new ability: write a Tool subclass in this package and add it
to ALL_TOOLS below.
"""

from tools.apps import OpenApplication, OpenPath
from tools.base import Tool, ToolContext, ToolError, ToolRegistry
from tools.email_tools import ReadEmails, SendEmail
from tools.files import ListDirectory, ReadFile, WriteFile
from tools.knowledge import LearnLesson, LearnTopic, Recall, Remember, SaveKnowledge
from tools.system import GetDateTime, RunCommand, SystemInfo
from tools.web import FetchWebpage, WebSearch

ALL_TOOLS = [
    # information
    GetDateTime, SystemInfo,
    # internet
    WebSearch, FetchWebpage,
    # memory & learning
    Remember, Recall, LearnTopic, SaveKnowledge, LearnLesson,
    # files
    ListDirectory, ReadFile, WriteFile,
    # PC control
    OpenApplication, OpenPath, RunCommand,
    # email
    SendEmail, ReadEmails,
]


def create_registry(context: ToolContext) -> ToolRegistry:
    registry = ToolRegistry(auto_approve=context.config.auto_approve)
    for tool_class in ALL_TOOLS:
        registry.register(tool_class(context))
    return registry


__all__ = ["Tool", "ToolContext", "ToolError", "ToolRegistry", "create_registry", "ALL_TOOLS"]
