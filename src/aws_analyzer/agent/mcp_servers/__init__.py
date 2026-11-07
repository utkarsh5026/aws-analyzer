"""
The agent's MCP servers (AgentAnalyzer(mcp=...)): servers that speak the Model Context Protocol, whose tools the agent
can call beside its own AWS tools. A server is a command it runs on this machine (npx, uvx, docker, a script) or a URL
it calls, added in the chat window's 🔌 mcp panel, with add_mcp(), or from an mcp.json.

    config.py   McpServer, read forgivingly from a command line, a URL or an mcp.json entry, and written back in
                the shape VS Code, Claude Desktop, Cursor and Claude Code read (pure)
    files.py    the mcp.json the agent keeps its servers in, and the ones editors keep (editor_configs)
    client.py   McpHub: one connection per server on a loop of its own, their states and tools, and calls
    findings.py what's worth knowing about them: a server that won't connect, tools that run without asking (pure)

The tools themselves are toolkits/mcp_tools.py's McpToolkit. Unlike the AWS tools, they don't go through the agent's
read-only guard: they're the server's. So a tool that doesn't say it only reads asks the user before it runs, unless
the server's asks= says otherwise.
"""

from __future__ import annotations

from .client import CONNECTING, FAILED, OFF, READY, McpCallFailed, McpHub, McpTool, ServerState, asks_first
from .config import (
    ASK_LABELS,
    ASKS,
    ASKS_EVERY,
    ASKS_NEVER,
    ASKS_UNMARKED,
    HIDDEN,
    TRANSPORTS,
    McpServer,
    guess_name,
    parse_server_text,
    parse_settings_text,
    server_entry,
    servers_from_config,
    shown_settings,
    tool_name,
)
from .findings import mcp_findings
from .files import MCP_ENV, MCP_FILE, EditorConfig, editor_configs, mcp_path, read_servers, write_servers

__all__ = ["ASKS", "ASKS_EVERY", "ASKS_NEVER", "ASKS_UNMARKED", "ASK_LABELS", "CONNECTING", "FAILED", "HIDDEN",
           "MCP_ENV", "MCP_FILE", "OFF", "READY", "TRANSPORTS", "EditorConfig", "McpCallFailed", "McpHub",
           "McpServer", "McpTool", "ServerState", "asks_first", "editor_configs", "guess_name", "mcp_findings",
           "mcp_path", "parse_server_text", "parse_settings_text", "read_servers", "server_entry",
           "servers_from_config", "shown_settings", "tool_name", "write_servers"]
