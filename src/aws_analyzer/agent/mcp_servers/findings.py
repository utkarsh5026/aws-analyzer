"""
What's worth knowing about the agent's MCP servers, as findings ("warn" | "info", message) the mcp() report and the
chat window's 🔌 mcp panel show: a server that isn't connected and why, a variable it names that the kernel doesn't
set, tools that can run without asking, tools that say they may delete something, and keys kept in the file. Pure.
"""

from __future__ import annotations

from pathlib import Path

from ..._kit.fmt import _plural
from .client import FAILED, ServerState
from .config import ASKS_NEVER, environment_names, is_reference

_NAMED_TOOLS = 3


def _names(tools: list[str]) -> str:
    shown = ", ".join(tools[:_NAMED_TOOLS])
    return f"{shown} and {len(tools) - _NAMED_TOOLS} more" if len(tools) > _NAMED_TOOLS else shown


def _server_findings(state: ServerState, path: Path | None) -> list[tuple[str, str]]:
    """
    One server's findings: why it isn't connected, what it names that isn't set, what runs without asking, what may
    delete something, and a key written into the file.
    """
    name, server = state.name, state.server
    findings: list[tuple[str, str]] = []
    if state.status == FAILED:
        findings.append(("warn", f"{name} isn't connected: {state.error.rstrip('.')}. Its tools are left out until "
                                 f"it is: fix it, then reconnect_mcp('{name}'), or remove_mcp('{name}')."))
    missing = environment_names(server)
    if missing and server.enabled:
        variable = missing[0]
        findings.append(("warn", f"{name} reads {_plural(len(missing), 'variable')} this kernel doesn't set "
                                 f"({', '.join(missing)}): it starts without {'it' if len(missing) == 1 else 'them'}. "
                                 f"Set it (os.environ['{variable}'] = ...), then reconnect_mcp('{name}')."))
    unasked = [tool.name for tool in state.usable_tools if not tool.read_only]
    if server.asks == ASKS_NEVER and unasked:
        findings.append(("warn", f"{name} never asks first, and {_plural(len(unasked), 'tool')} of it don't say they "
                                 f"only read ({_names(unasked)}): the agent can run them without asking you. Pick "
                                 "'ask unless the tool is marked read-only' in the chat window's 🔌 mcp panel."))
    destructive = [tool.name for tool in state.usable_tools if tool.destructive]
    if destructive:
        findings.append(("info", f"{name}'s {_names(destructive)} say they may delete or overwrite something: the "
                                 "agent asks before each runs. Untick them in the 🔌 mcp panel if it should never "
                                 "use them."))
    kept = [key for key, value in server.settings.items() if value and not is_reference(value)]
    if kept and path is not None:
        findings.append(("info", f"{name} keeps {_names(kept)} in {path} (readable only by you): write it as "
                                 f"${{env:{kept[0]}}} there to read it from the kernel's environment instead."))
    return findings


def mcp_findings(states: list[ServerState], *, path: Path | None, problem: str = "",
                 missing_package: str = "") -> list[tuple[str, str]]:
    """
    The findings about every server, warnings first: the file that couldn't be read, the mcp package that isn't
    installed, then each server's (_server_findings); and how to add one when there are none.
    """
    findings: list[tuple[str, str]] = []
    if problem:
        findings.append(("warn", f"The MCP servers' file can't be used: {problem}"))
    if missing_package:
        findings.append(("warn", f"{missing_package}: it comes with aws-analyzer[agent]."))
    for state in states:
        findings.extend(_server_findings(state, path))
    if not states and not problem:
        findings.append(("info", "No MCP servers yet: add one with add_mcp('npx -y @modelcontextprotocol/"
                                 "server-memory'), a URL, or an editor's mcp.json (import_mcp()), or in the chat "
                                 "window's 🔌 mcp panel."))
    return sorted(findings, key=lambda finding: finding[0] != "warn")
