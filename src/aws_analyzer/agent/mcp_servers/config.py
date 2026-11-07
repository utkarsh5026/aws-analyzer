"""
The MCP servers the agent can use, as settings: McpServer says how to start or reach one (a command it runs on this
machine, or a URL it calls) and what the agent may do with it (whether it asks before a tool runs, which tools are
off). Read forgivingly from what people paste (a command line, a URL, an entry or a whole mcp.json from another app)
and written back in the shape those apps share, so a file stays readable by VS Code, Claude Desktop, Cursor and
Claude Code. Pure: nothing here starts a server or touches a file (files.py does that).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

STDIO, HTTP, SSE = "stdio", "http", "sse"
TRANSPORTS = (STDIO, HTTP, SSE)
ASKS_UNMARKED, ASKS_EVERY, ASKS_NEVER = "unmarked", "every", "never"
ASKS = (ASKS_UNMARKED, ASKS_EVERY, ASKS_NEVER)
ASK_LABELS = {ASKS_UNMARKED: "ask unless the tool is marked read-only", ASKS_EVERY: "ask before every call",
              ASKS_NEVER: "never ask"}
SERVER_KEYS = ("mcpServers", "servers")
HIDDEN = "(hidden)"
TOOL_SEPARATOR = "__"
MAX_TOOL_NAME = 64
_NAME_CHARACTERS = 32
_DEFAULT_NAME = "server"
_TRANSPORT_WORDS = {"stdio": STDIO, "local": STDIO, "http": HTTP, "streamable-http": HTTP, "streamablehttp": HTTP,
                    "streamable_http": HTTP, "remote": HTTP, "sse": SSE}
_REFERENCE = re.compile(r"\$\{(?:(env|input):)?([A-Za-z_][A-Za-z0-9_.-]*)\}")
_ONLY_REFERENCES = re.compile(r"^(?:\s*(?:Bearer\s+)?\$\{[^}]+\}\s*)+$")
_SETTING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_UNSAFE_NAME = re.compile(r"[^a-z0-9_-]+")
_UNSAFE_TOOL = re.compile(r"[^A-Za-z0-9_-]+")
_RUNNERS = frozenset({"npx", "uvx", "uv", "pipx", "node", "deno", "bun", "bunx", "pnpm", "yarn", "dlx", "run", "tool",
                      "python", "python3", "py", "docker", "podman", "exec", "--from", "-m", "x"})
_NAME_PREFIXES = ("mcp-server-", "server-", "mcp-", "mcp_")
_NAME_SUFFIXES = ("-mcp-server", "_mcp_server", "-server-mcp", "-mcp", "_mcp", "-server", "_server")
_HOST_WORDS = frozenset({"www", "mcp", "api", "app", "server", "remote", "com", "net", "org", "io", "dev", "ai", "co",
                         "cloud", "run"})


@dataclass
class McpServer:
    """
    One MCP server, as the agent keeps it.

        name            what the agent and its tools call it: 'github' (its tools are github__<tool>)
        transport       'stdio' (a command run on this machine), 'http' (streamable HTTP) or 'sse' (the older one)
        command, args   what a stdio server runs: 'npx', ['-y', '@modelcontextprotocol/server-filesystem', '/data']
        env             environment variables a stdio server gets, besides PATH and HOME ('${env:NAME}' reads one of
                        this kernel's)
        cwd             the folder a stdio server runs in ('' for the notebook's)
        url, headers    where a remote server is, and the HTTP headers sent to it (an Authorization key, ...)
        enabled         whether the agent uses it
        asks            when the agent asks before one of its tools runs: ASKS_UNMARKED (a tool not marked read-only),
                        ASKS_EVERY, ASKS_NEVER
        disabled_tools  its tools the agent leaves out, by their own names
        source          where it was read from ('saved', '.vscode/mcp.json', 'AgentView(mcp=...)')
    """

    name: str
    transport: str = STDIO
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    asks: str = ASKS_UNMARKED
    disabled_tools: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def is_remote(self) -> bool:
        return self.transport != STDIO

    @property
    def target(self) -> str:
        """
        What it runs ('npx -y @modelcontextprotocol/server-filesystem /data') or where it is (its URL).
        """
        if self.is_remote:
            return self.url
        return shlex.join([self.command, *self.args])

    @property
    def settings(self) -> dict[str, str]:
        """
        Its environment variables (stdio) or HTTP headers (remote): what the add form's settings box holds.
        """
        return self.headers if self.is_remote else self.env

    def same_place(self, other: McpServer) -> bool:
        """
        Whether two servers are started or reached the same way, so a connection to one is a connection to the other.
        """
        return (self.transport, self.command, list(self.args), dict(self.env), self.cwd, self.url,
                dict(self.headers)) == (other.transport, other.command, list(other.args), dict(other.env), other.cwd,
                                        other.url, dict(other.headers))


def clean_name(text: str) -> str:
    """
    A server's name as the agent keeps it: lower case, letters, digits, '-' and '_', at most _NAME_CHARACTERS.
    'GitHub Copilot' -> 'github-copilot'.
    """
    name = _UNSAFE_NAME.sub("-", str(text or "").strip().lower()).strip("-_")
    return name[:_NAME_CHARACTERS].strip("-_") or _DEFAULT_NAME


def unique_name(name: str, taken: Iterable[str]) -> str:
    """
    `name`, or name-2, name-3, ... when another server has it.
    """
    used = set(taken)
    if name not in used:
        return name
    number = 2
    while f"{name}-{number}" in used:
        number += 1
    return f"{name}-{number}"


def _package_word(token: str) -> str:
    """
    The word a package or path names: '@modelcontextprotocol/server-filesystem@latest' -> 'filesystem',
    'awslabs.aws-documentation-mcp-server@latest' -> 'aws-documentation', 'ghcr.io/github/github-mcp-server' ->
    'github', '/opt/tools/notes_server.py' -> 'notes'.
    """
    word = token.rsplit("/", 1)[-1] if "/" in token else token
    word = word.split("@", 1)[0] if not word.startswith("@") else word.lstrip("@")
    word = word.split(":", 1)[0]
    word = re.sub(r"\.(py|js|mjs|ts|exe)$", "", word)
    if word.startswith("awslabs."):
        word = word[len("awslabs."):]
    lowered = word.lower()
    for prefix in _NAME_PREFIXES:
        if lowered.startswith(prefix) and len(lowered) > len(prefix):
            lowered = lowered[len(prefix):]
    for suffix in _NAME_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            lowered = lowered[: -len(suffix)]
    return lowered


def _host_word(url: str) -> str:
    """
    The word a URL's host names: 'https://mcp.notion.com/mcp' -> 'notion', 'https://api.githubcopilot.com/mcp/' ->
    'githubcopilot', 'http://localhost:8000/mcp' -> 'localhost'.
    """
    host = (urlparse(url).hostname or "").lower()
    labels = [label for label in host.split(".") if label and label not in _HOST_WORDS]
    return labels[0] if labels else host


def guess_name(server: McpServer) -> str:
    """
    A name for a server nobody named, from what it runs or where it is (see _package_word and _host_word).
    """
    if server.is_remote:
        return clean_name(_host_word(server.url))
    tokens = [server.command, *server.args]
    for token in tokens:
        bare = token.rsplit("/", 1)[-1].lower()
        if not token or token.startswith("-") or bare in _RUNNERS or "=" in token:
            continue
        return clean_name(_package_word(token))
    return clean_name(_package_word(server.command))


def is_reference(value: str) -> bool:
    """
    Whether a setting's value only names other values ('${env:GITHUB_TOKEN}', 'Bearer ${GITHUB_TOKEN}'), and so
    can be shown as it is: it holds no secret itself.
    """
    return bool(_ONLY_REFERENCES.match(str(value or "")))


def shown_settings(settings: Mapping[str, str]) -> dict[str, str]:
    """
    Environment variables or headers as a panel or report shows them: each value HIDDEN unless it only names other
    values (is_reference).
    """
    return {key: value if is_reference(value) else HIDDEN for key, value in settings.items()}


def expand_references(value: str, environ: Mapping[str, str]) -> tuple[str, list[str]]:
    """
    A setting with '${env:NAME}' and '${NAME}' replaced by this kernel's variable, and what it named that isn't set
    (VS Code's '${input:...}' prompts, which nothing here can ask, count as not set).
    """
    missing: list[str] = []

    def swap(found: re.Match[str]) -> str:
        kind, name = found.group(1), found.group(2)
        if kind == "input" or name not in environ:
            missing.append(f"{kind}:{name}" if kind == "input" else name)
            return ""
        return environ[name]

    return _REFERENCE.sub(swap, str(value)), missing


def parse_settings_text(text: str) -> dict[str, str]:
    """
    The add form's settings box: one 'NAME=value' or 'Header: value' per line (whichever of = and : comes first
    splits it), blank lines and lines starting with # left out. ValueError naming a line that has neither.
    """
    settings: dict[str, str] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cuts = [at for at in (stripped.find("="), stripped.find(":")) if at > 0]
        if not cuts:
            raise ValueError(f"Each line is NAME=value (or Header: value): {stripped!r} has neither")
        at = min(cuts)
        key, value = stripped[:at].strip(), stripped[at + 1:].strip()
        if not _SETTING_NAME.fullmatch(key):
            raise ValueError(f"{key!r} isn't a name an environment variable or header can have")
        settings[key] = value
    return settings


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    return [str(item) for item in value]


def _text_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): "" if item is None else str(item) for key, item in value.items()}


def _transport_of(entry: Mapping[str, Any], url: str) -> str:
    """
    The transport an entry names ('type', or 'transport'), else what its URL suggests: an /sse path is the older
    SSE transport, any other URL streamable HTTP, and no URL a command.
    """
    named = str(entry.get("type") or entry.get("transport") or "").strip().lower()
    if named in _TRANSPORT_WORDS:
        transport = _TRANSPORT_WORDS[named]
        if transport != STDIO or not url:
            return transport
    if not url:
        return STDIO
    return SSE if urlparse(url).path.rstrip("/").endswith("/sse") else HTTP


def server_from_entry(name: str, entry: Mapping[str, Any], *, source: str = "") -> McpServer:
    """
    One server from an mcp.json entry, in any of the shapes apps write: Claude Desktop's and Cursor's
    {"command", "args", "env"}, VS Code's and Claude Code's with "type", a remote one's {"url", "headers"} (or
    Windsurf's "serverUrl"), a command given whole as one string, and this agent's own "disabled", "asks" and
    "disabledTools". ValueError for an entry with neither a command nor a URL.
    """
    if not isinstance(entry, Mapping):
        raise ValueError(f"The server {name!r} should be a JSON object with a command or a url")
    url = str(entry.get("url") or entry.get("serverUrl") or "").strip()
    transport = _transport_of(entry, url)
    command, args = "", _text_list(entry.get("args"))
    if transport == STDIO:
        words = _text_list(entry.get("command"))
        if not words:
            raise ValueError(f"The server {name!r} has neither a command to run nor a url to call")
        command, args = words[0], [*words[1:], *args]
    asks = str(entry.get("asks") or ASKS_UNMARKED).strip().lower()
    server = McpServer(
        name="", transport=transport, command=command, args=args if transport == STDIO else [],
        env=_text_map(entry.get("env")), cwd=str(entry.get("cwd") or ""), url=url,
        headers=_text_map(entry.get("headers")),
        enabled=not entry.get("disabled", False) and entry.get("enabled", True) is not False,
        asks=asks if asks in ASKS else ASKS_UNMARKED,
        disabled_tools=[str(tool) for tool in entry.get("disabledTools") or []], source=source)
    server.name = clean_name(name) if name else guess_name(server)
    return server


def _is_entry(value: Any) -> bool:
    return isinstance(value, Mapping) and any(key in value for key in ("command", "url", "serverUrl"))


def servers_from_config(data: Any, *, source: str = "") -> list[McpServer]:
    """
    Every server in an mcp.json's data: its "mcpServers" (Claude Desktop, Cursor, Claude Code) or "servers"
    (VS Code), a {name: entry} mapping, or one entry on its own (named from what it runs). ValueError when it holds
    none.
    """
    if not isinstance(data, Mapping):
        raise ValueError("An mcp.json holds a JSON object: {\"mcpServers\": {\"name\": {\"command\": ...}}}")
    if _is_entry(data):
        return [server_from_entry("", data, source=source)]
    mapping = next((data[key] for key in SERVER_KEYS if isinstance(data.get(key), Mapping)), None)
    if mapping is None and data and all(_is_entry(value) for value in data.values()):
        mapping = data
    if mapping is None:
        raise ValueError("No MCP servers in it: it needs \"mcpServers\" (or VS Code's \"servers\"), each with a "
                         "command or a url")
    return [server_from_entry(str(name), entry, source=source) for name, entry in mapping.items()]


def _from_command_line(text: str) -> McpServer:
    """
    A stdio server from a command line: 'GITHUB_TOKEN=... npx -y @modelcontextprotocol/server-github', its leading
    NAME=value words its environment.
    """
    try:
        words = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"The command can't be read ({exc}): check its quotes") from exc
    env: dict[str, str] = {}
    while words and "=" in words[0] and _SETTING_NAME.fullmatch(words[0].split("=", 1)[0]):
        key, value = words.pop(0).split("=", 1)
        env[key] = value
    if not words:
        raise ValueError("Type the command that starts the server, like npx -y @modelcontextprotocol/server-memory")
    return McpServer(name="", command=words[0], args=words[1:], env=env)


def parse_server_text(text: str, *, name: str = "", source: str = "") -> list[McpServer]:
    """
    The servers in what someone typed or pasted: a URL (a remote server), a command line (one run on this machine),
    or JSON (an entry, or a whole mcp.json from another app). `name` names a lone server; otherwise each is named
    from what it runs or where it is. ValueError saying what's wrong with it.
    """
    stripped = str(text or "").strip()
    if not stripped:
        raise ValueError("Type the command that starts the server (npx -y @modelcontextprotocol/server-memory), its "
                         "URL (https://.../mcp), or paste its JSON from an mcp.json")
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"That JSON doesn't read: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
        servers = servers_from_config(data, source=source)
    elif re.match(r"https?://", stripped, re.IGNORECASE):
        servers = [McpServer(name="", transport=_transport_of({}, stripped), url=stripped)]
    else:
        servers = [_from_command_line(stripped)]
    for server in servers:
        server.source = server.source or source
        if not server.name:
            server.name = guess_name(server)
    if name and len(servers) == 1:
        servers[0].name = clean_name(name)
    return servers


def server_entry(server: McpServer) -> dict[str, Any]:
    """
    A server as an mcp.json entry, in the shape VS Code, Claude Desktop, Cursor and Claude Code all read, with this
    agent's own settings ("disabled", "asks", "disabledTools") only when they aren't the defaults.
    """
    entry: dict[str, Any] = {"type": server.transport}
    if server.is_remote:
        entry["url"] = server.url
        if server.headers:
            entry["headers"] = dict(server.headers)
    else:
        entry["command"] = server.command
        if server.args:
            entry["args"] = list(server.args)
        if server.env:
            entry["env"] = dict(server.env)
        if server.cwd:
            entry["cwd"] = server.cwd
    if not server.enabled:
        entry["disabled"] = True
    if server.asks != ASKS_UNMARKED:
        entry["asks"] = server.asks
    if server.disabled_tools:
        entry["disabledTools"] = list(server.disabled_tools)
    return entry


def tool_name(server: str, tool: str) -> str:
    """
    What the model calls a server's tool: '<server>__<tool>' ('github__create_issue'), in the letters, digits, '-'
    and '_' every provider takes, at most MAX_TOOL_NAME characters.
    """
    joined = f"{server}{TOOL_SEPARATOR}{tool}"
    return _UNSAFE_TOOL.sub("_", joined)[:MAX_TOOL_NAME]


def split_tool_name(name: str) -> tuple[str, str] | None:
    """
    ('github', 'create_issue') from 'github__create_issue', or None for a name that isn't an MCP tool's.
    """
    if TOOL_SEPARATOR not in name:
        return None
    server, tool = name.split(TOOL_SEPARATOR, 1)
    return (server, tool) if server and tool else None


def environment_names(server: McpServer, environ: Mapping[str, str] | None = None) -> list[str]:
    """
    The kernel's variables a server's settings name that aren't set ('GITHUB_TOKEN'), which it would start without.
    """
    environ = os.environ if environ is None else environ
    values = [*server.settings.values(), *server.args, server.url]
    missing: list[str] = []
    for value in values:
        missing.extend(name for name in expand_references(value, environ)[1] if name not in missing)
    return missing
