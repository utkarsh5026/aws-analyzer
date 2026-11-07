"""
Where the agent's MCP servers are kept: one mcp.json (MCP_FILE in aws-analyzer's cache folder, AWS_ANALYZER_MCP's
path, or the file AgentAnalyzer(mcp=...) names), in the shape VS Code, Claude Desktop, Cursor and Claude Code read,
so the same file works in the editor too. Writing keeps everything else in it (VS Code's "inputs", other keys) and
the key it already uses for its servers; it goes to <name>.part first and is then renamed, readable by its owner
only, since it can hold keys. Nothing here deletes a file.

editor_configs() finds the mcp.json files editors and apps keep beside the notebook and in the home folder, so the
chat window can offer to import their servers.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import SERVER_KEYS, McpServer, server_entry, servers_from_config

MCP_FILE = Path("~/.cache/aws-analyzer/agent/mcp.json")
MCP_ENV = "AWS_ANALYZER_MCP"
_OFF_WORDS = frozenset({"off", "0", "false", "no", "none"})
_OWNER_ONLY = 0o600
_PROJECT_FILES = ((".vscode/mcp.json", "VS Code"), (".mcp.json", "Claude Code"), (".cursor/mcp.json", "Cursor"))
_FOLDERS_UP = 6


@dataclass
class EditorConfig:
    """
    An mcp.json an editor or app keeps: where, whose ('VS Code'), the servers it holds, and why it couldn't be read.
    """

    path: Path
    app: str
    servers: list[McpServer] = field(default_factory=list)
    problem: str = ""

    @property
    def label(self) -> str:
        """
        How the window names it: "VS Code's .vscode/mcp.json".
        """
        return f"{self.app}'s {_nearby_path(self.path)}"


def _nearby_path(path: Path) -> str:
    """
    A path from the notebook's folder when it's under it, else from the home folder (~/...).
    """
    for base, prefix in ((Path.cwd(), ""), (Path.home(), "~/")):
        try:
            return prefix + str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def mcp_path(mcp: Any) -> Path | None:
    """
    The file the agent keeps its MCP servers in: MCP_FILE for mcp=True (or the path in AWS_ANALYZER_MCP, which 'off'
    turns off), the path mcp= gives, or None when mcp=False, or when mcp= gives the servers themselves (a dict or a
    list), which are then kept for the session only.
    """
    if mcp is True:
        chosen = os.environ.get(MCP_ENV, "").strip()
        if chosen.lower() in _OFF_WORDS:
            return None
        return Path(chosen or MCP_FILE).expanduser()
    if isinstance(mcp, (str, os.PathLike)):
        return Path(mcp).expanduser()
    return None


def read_servers(path: Path, *, source: str = "saved") -> tuple[list[McpServer], str]:
    """
    The servers an mcp.json holds, and '' or what kept it from being read ("it isn't JSON: ..."). A file that isn't
    there holds none, which isn't a problem: it's made when the first server is added.
    """
    if not path.exists():
        return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"{path} can't be read: {exc}"
    except json.JSONDecodeError as exc:
        return [], f"{path} isn't JSON ({exc.msg} at line {exc.lineno}): fix it, or move it away to start over"
    if not data:
        return [], ""
    try:
        return servers_from_config(data, source=source), ""
    except ValueError as exc:
        return [], f"{path}: {exc}"


def _existing(path: Path) -> dict[str, Any]:
    """
    What the file holds now, to keep: {} when it isn't there or isn't a JSON object.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_servers(path: Path, servers: list[McpServer]) -> None:
    """
    Writes the servers into the mcp.json at `path` under the key it already uses ("mcpServers" when it has none),
    keeping the rest of what it holds: to <name>.part first, then renamed over it, readable by its owner only.
    """
    data = _existing(path)
    key = next((key for key in SERVER_KEYS if isinstance(data.get(key), dict)), SERVER_KEYS[0])
    data[key] = {server.name: server_entry(server) for server in servers}
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    part.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(part, _OWNER_ONLY)
    os.replace(part, path)


def _app_files(home: Path) -> list[tuple[Path, str]]:
    """
    The mcp.json files apps keep in the home folder: Cursor's and Claude Desktop's (where each system keeps it).
    """
    files = [(home / ".cursor" / "mcp.json", "Cursor")]
    if sys.platform == "darwin":
        files.append((home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
                      "Claude Desktop"))
    elif sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        files.append((appdata / "Claude" / "claude_desktop_config.json", "Claude Desktop"))
    else:
        files.append((home / ".config" / "Claude" / "claude_desktop_config.json", "Claude Desktop"))
    return files


def _project_files(start: Path, home: Path) -> list[tuple[Path, str]]:
    """
    The mcp.json files editors keep in a project (_PROJECT_FILES), in the notebook's folder and up to _FOLDERS_UP
    folders above it, stopping at the home folder.
    """
    files: list[tuple[Path, str]] = []
    folder = start.resolve()
    for _ in range(_FOLDERS_UP):
        files.extend((folder / name, app) for name, app in _PROJECT_FILES)
        if folder == home or folder.parent == folder:
            break
        folder = folder.parent
    return files


def editor_configs(start: Path | None = None, home: Path | None = None,
                   skip: Path | None = None) -> list[EditorConfig]:
    """
    The mcp.json files there are, the nearest first: VS Code's .vscode/mcp.json, Claude Code's .mcp.json and
    Cursor's .cursor/mcp.json from the notebook's folder up, then Cursor's and Claude Desktop's in the home folder.
    `skip` is the agent's own file, never offered. Each holds the servers it could read, or says why not.
    """
    home = (home or Path.home()).resolve()
    found: list[EditorConfig] = []
    seen: set[Path] = {skip.resolve()} if skip is not None else set()
    for path, app in [*_project_files(start or Path.cwd(), home), *_app_files(home)]:
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        servers, problem = read_servers(path, source=f"{app}'s {_nearby_path(path)}")
        found.append(EditorConfig(path, app, servers, problem))
    return found
