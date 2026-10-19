"""
The agent's chat window (AgentView.chat()), a module per part: chat.py is the window itself (_AgentChat: the
conversation, each tool call as it runs, the answer), and model_panel.py its model settings (_ModelPanel, ⚙ model in
its head: the provider and model the agent answers with, picked by clicking).
"""

from __future__ import annotations

from .chat import _AgentChat

__all__ = ["_AgentChat"]
