"""
Talking to MCP servers. McpHub keeps one open connection per server, on an asyncio loop of its own on a thread of its
own (aws-analyzer-mcp), so the agent's tools, which run on LangGraph's threads, and the chat window, on the kernel's
loop, all reach it with plain blocking calls (connect, call, disconnect) that wait at most so long.

A connection is a task on that loop that opens the transport (a stdio server's process, or an HTTP session), starts
the MCP session, lists the server's tools, then waits until it's told to close: the MCP SDK's transports must be
opened and closed in the same task. A stdio server gets PATH, HOME and the like from this kernel and the env it was
given, not the kernel's AWS credentials, and its stderr goes to a temporary file whose last lines say why it stopped.

ServerState is what the window and the reports show of each server, McpTool one of its tools, with its JSON schema and
whether it says it only reads (its readOnlyHint). Whether a tool may run without asking is asks_first()'s to say; the
MCP toolkit (toolkits/mcp_tools.py) asks. The mcp package (the [agent] extra) is imported only when a server is first
connected.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import os
import re
import tempfile
import threading
import weakref
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..._kit.deps import _require
from ..._kit.text import _clip
from .config import ASKS_EVERY, ASKS_NEVER, SSE, STDIO, McpServer, expand_references, tool_name

OFF, CONNECTING, READY, FAILED = "off", "connecting", "ready", "failed"
CONNECT_SECONDS = 60.0
CALL_SECONDS = 120.0
_CLOSE_SECONDS = 5.0
_SPARE_SECONDS = 5.0
_LOG_CHARACTERS = 1200
_ERROR_CHARACTERS = 400
_INSTRUCTION_CHARACTERS = 600
_MAX_TOOL_PAGES = 20
_THREAD_NAME = "aws-analyzer-mcp"
_INSTALLED_WITH = {"npx": "Node.js (nodejs.org)", "node": "Node.js (nodejs.org)", "uvx": "uv (pip install uv)",
                   "uv": "uv (pip install uv)", "docker": "Docker", "bunx": "Bun (bun.sh)", "deno": "Deno (deno.com)"}
_LOST_CONNECTION = ("ClosedResourceError", "BrokenResourceError", "EndOfStream", "BrokenPipeError")
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_POINTER = re.compile(r"(?i)complete log of this run|for more information|^\s*at\s")
_MISSING = re.compile(r"(?i)not found|no such|no module|cannot find|could not|can't|denied|refused|missing|invalid|E404")
_ERROR = re.compile(r"(?i)error|exception|fatal")


def _mcp(module: str) -> Any:
    """
    The mcp package's module `mcp<module>` ('' for mcp itself, '.client.stdio'), or ImportError saying what to install.
    """
    return _require(f"mcp{module}", "Connecting to MCP servers", package="mcp")


class McpCallFailed(Exception):
    """
    A server or one of its tools couldn't answer: it isn't connected, it timed out, or it said it failed. The message
    says which, in a sentence the model and the user can read.
    """


@dataclass
class McpTool:
    """
    One of a server's tools, as it described itself: its name, what it does, the JSON schema of its arguments, and
    whether its annotations say it only reads (readOnlyHint) or may destroy something (destructiveHint).
    """

    server: str
    name: str
    description: str = ""
    title: str = ""
    schema: dict[str, Any] = field(default_factory=lambda: dict(_EMPTY_SCHEMA))
    read_only: bool = False
    destructive: bool = False

    @property
    def qualified(self) -> str:
        """
        What the model calls it: 'github__create_issue'.
        """
        return tool_name(self.server, self.name)


def tool_from(server: str, data: Mapping[str, Any]) -> McpTool:
    """
    An McpTool from a tool as the server listed it (MCP's Tool, as plain data: name, title, description,
    inputSchema, annotations).
    """
    annotations = data.get("annotations") or {}
    given = data.get("inputSchema")
    schema = given if isinstance(given, dict) else dict(_EMPTY_SCHEMA)
    return McpTool(server=server, name=str(data.get("name") or ""), description=str(data.get("description") or ""),
                   title=str(data.get("title") or annotations.get("title") or ""), schema=schema,
                   read_only=annotations.get("readOnlyHint") is True,
                   destructive=annotations.get("destructiveHint") is True and annotations.get("readOnlyHint") is not True)


def asks_first(server: McpServer, tool: McpTool) -> bool:
    """
    Whether the agent asks the user before it runs a tool: never with asks='never', always with 'every', and with
    the default ('unmarked') unless the tool says it only reads.
    """
    if server.asks == ASKS_NEVER:
        return False
    if server.asks == ASKS_EVERY:
        return True
    return not tool.read_only


@dataclass
class ServerState:
    """
    A server as it stands: its settings, whether it's connected (OFF, CONNECTING, READY or FAILED) and why it
    failed, its tools and what it calls itself ('github-mcp-server 0.5.1'), the instructions it gives its clients,
    when it was connected, how many calls it has answered and failed in this kernel, and its stderr's last lines.
    """

    server: McpServer
    status: str = OFF
    error: str = ""
    tools: list[McpTool] = field(default_factory=list)
    info: str = ""
    instructions: str = ""
    connected_at: datetime | None = None
    calls: int = 0
    failed_calls: int = 0
    last_call: datetime | None = None
    log: str = ""

    @property
    def name(self) -> str:
        return self.server.name

    @property
    def is_ready(self) -> bool:
        return self.status == READY

    @property
    def usable_tools(self) -> list[McpTool]:
        """
        Its tools the agent may use: all but the ones turned off (disabled_tools).
        """
        off = set(self.server.disabled_tools)
        return [tool for tool in self.tools if tool.name not in off]

    @property
    def asking_tools(self) -> list[McpTool]:
        """
        The usable tools the agent asks about before it runs them (asks_first).
        """
        return [tool for tool in self.usable_tools if asks_first(self.server, tool)]


def _innermost(exc: BaseException) -> BaseException:
    """
    The first error inside an exception group (anyio's task groups wrap what a transport raised), else the error.
    """
    while True:
        inner = getattr(exc, "exceptions", None)
        if not isinstance(inner, (list, tuple)) or not inner:
            return exc
        exc = inner[0]


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def describe_error(exc: BaseException, server: McpServer) -> str:
    """
    Why a server couldn't be reached or a call failed, in a sentence: a command that isn't installed (and what
    brings it), a URL that can't be reached or refuses the key, or what the server said.
    """
    inner = _innermost(exc)
    kind = type(inner).__name__
    if isinstance(inner, FileNotFoundError) and not server.is_remote:
        brings = _INSTALLED_WITH.get(os.path.basename(server.command))
        tail = f" It comes with {brings}." if brings else ""
        return f"{server.command} isn't installed here, or isn't on the kernel's PATH.{tail}"
    status = _http_status(inner)
    if status in (401, 403):
        return (f"{server.url} refused the request ({status}): it needs a key or token, sent as a header "
                "(Authorization: Bearer ...). This agent sends the headers you give; it can't sign in with OAuth.")
    if status == 404:
        return f"There's no MCP server at {server.url} (404): check the URL, which often ends in /mcp or /sse."
    if status is not None:
        return f"{server.url} answered {status}: {_clip(str(inner), _ERROR_CHARACTERS)}"
    if kind in ("ConnectError", "ConnectTimeout", "gaierror"):
        return f"Couldn't reach {server.url}: {_clip(str(inner) or kind, _ERROR_CHARACTERS)}"
    error = getattr(inner, "error", None)
    message = getattr(error, "message", None) or str(inner) or kind
    return _clip(f"{message}" if kind == "McpError" else f"{kind}: {message}", _ERROR_CHARACTERS)


def _telling_line(log: str) -> str:
    """
    The line of a server's last stderr lines that says why it stopped: one naming what's missing or refused (npm's
    'npm error 404 Not Found - GET ...'), else the last that mentions an error, else the last line. Lines pointing at
    a log file ('A complete log of this run can be found in ...') say nothing.
    """
    lines = [line.strip() for line in log.strip().splitlines() if line.strip() and not _POINTER.search(line)]
    for pattern in (_MISSING, _ERROR):
        found = [line for line in lines if pattern.search(line)]
        if found:
            return found[-1]
    return lines[-1] if lines else ""


@dataclass
class _Connection:
    """
    One open connection, on the hub's loop: set `closing` to close it, `ready` is set once it's connected or has
    failed, and `session` is its MCP session while it's open.
    """

    ready: asyncio.Event
    closing: asyncio.Event
    errlog: Any = None
    session: Any = None
    task: Any = None


_HUBS: weakref.WeakSet[McpHub] = weakref.WeakSet()


def _close_hubs() -> None:
    for hub in list(_HUBS):
        hub.close()


atexit.register(_close_hubs)


class McpHub:
    """
    The agent's MCP servers, connected on a loop of their own: states (name -> ServerState, in the order the agent
    lists them) is what the window shows. set_servers() follows the agent's list, connect() opens the enabled ones
    that aren't open, call() runs one of a server's tools and disconnect() closes one. Every call blocks at most
    connect_seconds or call_seconds. `environ` is where ${env:NAME} in a server's settings is read (os.environ).
    """

    def __init__(self, *, connect_seconds: float = CONNECT_SECONDS, call_seconds: float = CALL_SECONDS,
                 environ: Mapping[str, str] | None = None):
        self.connect_seconds = connect_seconds
        self.call_seconds = call_seconds
        self.environ = environ
        self.states: dict[str, ServerState] = {}
        self._connections: dict[str, _Connection] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        _HUBS.add(self)

    def state(self, name: str) -> ServerState | None:
        return self.states.get(name)

    def set_servers(self, servers: Iterable[McpServer]) -> None:
        """
        Follows the agent's list of servers: a new one starts OFF, one that's gone or turned off is closed, and one
        started or reached another way now (another command, URL or setting) is closed, to connect anew.
        """
        wanted = {server.name: server for server in servers}
        for name in [name for name in self.states if name not in wanted]:
            self.disconnect(name)
            self.states.pop(name, None)
        ordered: dict[str, ServerState] = {}
        for name, server in wanted.items():
            state = self.states.get(name)
            if state is None:
                ordered[name] = ServerState(server)
                continue
            moved = not state.server.same_place(server)
            state.server = server
            if moved or not server.enabled:
                self.disconnect(name)
                state.status, state.error = OFF, ""
            ordered[name] = state
        self.states = ordered

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """
        The hub's own event loop, running on its thread from the first connection on.
        """
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name=_THREAD_NAME, daemon=True).start()
                self._loop = loop
            return self._loop

    def connect(self, names: Iterable[str] | None = None, *, again: bool = False) -> list[ServerState]:
        """
        Connects the enabled servers (`names` picks some) that are off, or all of them again with again=True, side
        by side, and returns their states once each is connected or has failed (at most connect_seconds). One that
        failed stays failed until it's connected again, so a server that's down isn't waited for at every question.
        ImportError when the mcp package isn't installed.
        """
        wanted = None if names is None else set(names)
        picked = [state for state in self.states.values()
                  if state.server.enabled and (wanted is None or state.name in wanted)]
        todo = [state for state in picked if again or state.status == OFF]
        if not todo:
            return picked
        _mcp("")
        for state in todo:
            if again:
                self.disconnect(state.name)
            state.status, state.error = CONNECTING, ""
        loop = self._ensure_loop()
        opening = [asyncio.run_coroutine_threadsafe(self._open(state), loop) for state in todo]
        concurrent.futures.wait(opening, timeout=self.connect_seconds + _SPARE_SECONDS)
        for state in todo:
            if state.status == CONNECTING:
                state.status, state.error = FAILED, f"It didn't answer within {self.connect_seconds:g}s."
        return picked

    async def _open(self, state: ServerState) -> None:
        """
        Starts a connection's task and waits until it's connected or has failed, or connect_seconds have passed.
        """
        errlog = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") if not state.server.is_remote else None
        connection = _Connection(asyncio.Event(), asyncio.Event(), errlog=errlog)
        with self._lock:
            self._connections[state.name] = connection
        connection.task = asyncio.get_running_loop().create_task(self._serve(state, connection))
        try:
            await asyncio.wait_for(connection.ready.wait(), self.connect_seconds)
        except asyncio.TimeoutError:
            state.log = self._log_tail(connection)
            state.status = FAILED
            state.error = (f"It didn't answer within {self.connect_seconds:g}s: a command that downloads the server "
                           "first (npx, uvx) can take longer the first time, so try again.")
            connection.closing.set()
            connection.task.cancel()

    async def _serve(self, state: ServerState, connection: _Connection) -> None:
        """
        A connection's whole life, in one task: open the transport and the session, list the tools, wait to close.
        Whatever opening or the transport raises (a command that isn't there, a refused key, a server that quits)
        becomes the state's error.
        """
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(state.server, connection, stack)
                session = await stack.enter_async_context(self._session(read, write))
                started = await session.initialize()
                tools = await self._list_tools(session, state.name)
                self._now_ready(state, started, tools)
                connection.session = session
                connection.ready.set()
                await connection.closing.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.log = self._log_tail(connection)
            if state.status != FAILED:
                state.status, state.error = FAILED, self._with_log(describe_error(exc, state.server), state.log)
        finally:
            connection.session = None
            connection.ready.set()
            if state.status in (READY, CONNECTING) and connection.closing.is_set():
                state.status = OFF
            if connection.errlog is not None:
                connection.errlog.close()

    @staticmethod
    def _with_log(error: str, log: str) -> str:
        """
        The error with the line of the server's stderr that says most (_telling_line) after it.
        """
        last = _telling_line(log)
        if not last or last in error:
            return error
        return f"{error.rstrip('.')}. It said: {_clip(last, _ERROR_CHARACTERS)}"

    async def _open_transport(self, server: McpServer, connection: _Connection, stack: AsyncExitStack) -> tuple[Any, Any]:
        """
        The transport's read and write streams: a stdio server's process, or an HTTP session (streamable HTTP, or
        the older SSE), with ${env:NAME} in its settings read from the kernel's environment.
        """
        environ = os.environ if self.environ is None else self.environ
        settings = {key: expand_references(value, environ)[0] for key, value in server.settings.items()}
        if server.transport == STDIO:
            params = _mcp("").StdioServerParameters(command=server.command,
                                                     args=[expand_references(arg, environ)[0] for arg in server.args],
                                                     env=settings or None, cwd=server.cwd or None)
            opened = _mcp(".client.stdio").stdio_client(params, errlog=connection.errlog)
            streams = await stack.enter_async_context(opened)
            return streams[0], streams[1]
        url = expand_references(server.url, environ)[0]
        if server.transport == SSE:
            streams = await stack.enter_async_context(_mcp(".client.sse").sse_client(url, headers=settings or None))
            return streams[0], streams[1]
        return await self._open_http(url, settings, stack)

    @staticmethod
    async def _open_http(url: str, headers: dict[str, str], stack: AsyncExitStack) -> tuple[Any, Any]:
        """
        A streamable HTTP session: streamable_http_client on an httpx client carrying the headers, or on an older
        mcp package, streamablehttp_client.
        """
        streamable_http = _mcp(".client.streamable_http")
        if hasattr(streamable_http, "streamable_http_client"):
            make_client = _mcp(".shared._httpx_utils").create_mcp_http_client
            client = await stack.enter_async_context(make_client(headers=headers or None))
            streams = await stack.enter_async_context(streamable_http.streamable_http_client(url, http_client=client))
        else:
            streams = await stack.enter_async_context(streamable_http.streamablehttp_client(url, headers=headers or None))
        return streams[0], streams[1]

    @staticmethod
    def _session(read: Any, write: Any) -> Any:
        from ... import __version__

        about = _mcp(".types").Implementation(name="aws-analyzer", version=__version__)
        return _mcp("").ClientSession(read, write, client_info=about)

    @staticmethod
    async def _list_tools(session: Any, server: str) -> list[McpTool]:
        """
        Every tool the server lists, page by page (at most _MAX_TOOL_PAGES).
        """
        paging = _mcp(".types").PaginatedRequestParams
        tools: list[McpTool] = []
        cursor = None
        for _ in range(_MAX_TOOL_PAGES):
            page = await (session.list_tools(params=paging(cursor=cursor)) if cursor else session.list_tools())
            tools.extend(tool_from(server, tool.model_dump(mode="json", by_alias=True, exclude_none=True))
                         for tool in page.tools)
            cursor = page.nextCursor
            if not cursor:
                break
        return tools

    @staticmethod
    def _now_ready(state: ServerState, started: Any, tools: list[McpTool]) -> None:
        info = getattr(started, "serverInfo", None)
        name, version = getattr(info, "name", "") or "", getattr(info, "version", "") or ""
        state.info = f"{name} {version}".strip()
        state.instructions = _clip(str(getattr(started, "instructions", "") or "").strip(), _INSTRUCTION_CHARACTERS)
        state.tools = tools
        state.status, state.error = READY, ""
        state.connected_at = datetime.now(timezone.utc)

    @staticmethod
    def _log_tail(connection: _Connection) -> str:
        """
        The last lines a stdio server wrote to its stderr.
        """
        errlog = connection.errlog
        if errlog is None or errlog.closed:
            return ""
        try:
            errlog.flush()
            errlog.seek(0)
            text = errlog.read()
        except (OSError, ValueError):
            return ""
        return text[-_LOG_CHARACTERS:].strip()

    def call(self, name: str, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Runs one of a server's tools and returns what it answered (MCP's CallToolResult as plain data: content,
        structuredContent, isError), connecting the server first when it isn't. McpCallFailed when it can't: the
        server isn't there or connected, the call took longer than call_seconds, or the connection broke (then the
        server is marked failed).
        """
        state = self.states.get(name)
        if state is None:
            raise McpCallFailed(f"There's no MCP server named {name!r}.")
        if not state.is_ready:
            self.connect([name])
        connection = self._connections.get(name)
        session = connection.session if connection is not None else None
        if not state.is_ready or session is None or self._loop is None:
            raise McpCallFailed(f"The MCP server {name!r} isn't connected: {state.error or 'it is off'}")
        running = asyncio.run_coroutine_threadsafe(
            session.call_tool(tool, arguments or {}, read_timeout_seconds=timedelta(seconds=self.call_seconds)),
            self._loop)
        try:
            result = running.result(self.call_seconds + _SPARE_SECONDS)
        except concurrent.futures.TimeoutError as exc:
            running.cancel()
            self._counted(state, failed=True)
            raise McpCallFailed(f"{tool} on {name} didn't answer within {self.call_seconds:g}s.") from exc
        except Exception as exc:  # noqa: BLE001
            self._counted(state, failed=True)
            text = describe_error(exc, state.server)
            if type(_innermost(exc)).__name__ in _LOST_CONNECTION or "Connection closed" in text:
                self._lost(state, connection, text)
            raise McpCallFailed(f"{tool} on {name} failed: {text}") from exc
        self._counted(state)
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _counted(self, state: ServerState, *, failed: bool = False) -> None:
        with self._lock:
            state.calls += 1
            state.failed_calls += 1 if failed else 0
            state.last_call = datetime.now(timezone.utc)

    def _lost(self, state: ServerState, connection: _Connection | None, why: str) -> None:
        """
        Marks a server whose connection broke as failed, with its stderr's last lines, and closes what's left of it.
        """
        state.log = self._log_tail(connection) if connection is not None else state.log
        self.disconnect(state.name)
        state.status, state.error = FAILED, self._with_log(f"The connection broke: {why}", state.log)

    def disconnect(self, name: str) -> None:
        """
        Closes a server's connection (a stdio server's process stops), waiting at most _CLOSE_SECONDS.
        """
        with self._lock:
            connection = self._connections.pop(name, None)
        state = self.states.get(name)
        if state is not None and state.status in (READY, CONNECTING):
            state.status = OFF
        loop = self._loop
        if connection is None or loop is None or loop.is_closed():
            return
        closing = asyncio.run_coroutine_threadsafe(self._close(connection), loop)
        try:
            closing.result(_CLOSE_SECONDS + _SPARE_SECONDS)
        except (concurrent.futures.TimeoutError, concurrent.futures.CancelledError):
            closing.cancel()

    async def _close(self, connection: _Connection) -> None:
        connection.closing.set()
        task = connection.task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), _CLOSE_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001
            return

    def close(self) -> None:
        """
        Closes every connection and stops the hub's loop (at the kernel's exit, or when the agent is done with it).
        """
        for name in list(self._connections):
            self.disconnect(name)
        loop, self._loop = self._loop, None
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
