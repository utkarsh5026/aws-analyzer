"""
The agent's chat window (AgentView.chat()), a module per part: chat.py is the window itself (_AgentChat: the
conversation, each tool call as it runs, the answer), tool_words.py what each tool call says it does,
model_panel.py its model settings (_ModelPanel, ⚙ model in its head: the provider and model the agent answers with,
picked by clicking), and model_commands.py the View's commands that do the same from a cell (_ModelCommands: switch,
name, rename and forget saved models). _AgentChat is loaded when it's first asked for, so the View can import
model_commands.py without the window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .chat import _AgentChat

__all__ = ["_AgentChat"]


def __getattr__(name: str) -> Any:
    """
    _AgentChat, imported the first time it's asked for.
    """
    if name == "_AgentChat":
        from .chat import _AgentChat

        return _AgentChat
    raise AttributeError(name)
