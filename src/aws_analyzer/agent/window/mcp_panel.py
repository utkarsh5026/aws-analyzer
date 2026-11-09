"""
The chat window's MCP panel (🔌 mcp in its head): the agent's MCP servers, added, switched and watched in the window
instead of in code. A line per server says how it stands at a glance (a dot: connected, connecting, failed or off),
what it runs or where it is, its tools and how many ask before they run, and the calls it has answered; its buttons
open its tools (a box each, to turn one off, and when the server asks first), connect it again, turn it off or on,
and remove it (a second click). Under them, + add a server opens a form with one box for the command, the URL or a
pasted mcp.json, a name (guessed as you type), its environment variables or headers, and when it asks; connect & add
connects it before it's saved, and says why when it can't. The mcp.json files the editor keeps (VS Code's, Cursor's,
Claude Desktop's, Claude Code's) are offered to import, a button each.

Connecting takes a moment (a command that downloads its server takes longer the first time), so it runs on a worker
thread in a notebook, and the panel is drawn again on the loop when it's done; the head's 🔌 mcp says how many are
connected meanwhile. _MCP_CSS is its look, which the window adds to its stylesheet.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from ..._kit.fmt import _plural
from ..._kit.render import _prose
from ..._kit.text import _clip, _esc
from ..._kit.window import _class_if, _running_loop
from .model_panel import _text
from ..mcp_servers import (
    ASK_LABELS,
    ASKS,
    CONNECTING,
    FAILED,
    OFF,
    READY,
    EditorConfig,
    McpServer,
    McpTool,
    ServerState,
    asks_first,
    mcp_findings,
    parse_server_text,
)

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer
    from .chat import _AgentChat

_TARGET_CHARACTERS = 64
_ERROR_CHARACTERS = 300
_DESCRIPTION_CHARACTERS = 110
_IMPORTS_OFFERED = 3
_ADD = "connect & add"
_RETRY = "try again"
_MCP_OFF = ("MCP servers are off for this agent (mcp=False). AgentView(mcp=True) lets it use the tools of servers "
            "you add here, kept in ~/.cache/aws-analyzer/agent/mcp.json.")
_NO_SERVERS = ("No MCP servers yet. Add one below: a command it runs on this machine (npx, uvx, docker, a script), "
               "or a URL it calls. Its tools join the agent's own; one that may change something asks you first.")
_STATUS_DOTS = {READY: ("ok", "connected"), CONNECTING: ("run", "connecting"), FAILED: ("bad", "failed")}
_SERVER_PLACEHOLDER = "npx -y @modelcontextprotocol/server-memory · https://example.com/mcp · or paste its JSON"
_ENV_PLACEHOLDER = "NAME=value, a line each (${env:NAME} reads the kernel's)"
_HEADER_PLACEHOLDER = "Authorization: Bearer ${env:TOKEN}, a line each"
_LABEL_WIDTH = "104px"

_MCP_CSS = f"""\
.agc-app.agc-app .agc-new.agc-warn{{border-color:var(--ag-warn);color:var(--ag-warn)}}
.agc-app.agc-app .agc-mcp{{gap:4px;padding:10px 16px 12px;border-bottom:1px solid var(--ag-line)}}
.agc-app.agc-app .agc-mcp>*{{margin:0}}
.agc-app .agc-mcp .ag-mh{{font-size:12px;line-height:1.55;color:var(--ag-dim)}}
.agc-app .agc-mcp .ag-mh b{{color:inherit;font-weight:600}}
.agc-app .agc-mcp .ag-mh .warn,.agc-app .agc-mcp .ag-sl .warn{{color:var(--ag-warn)}}
.agc-app .agc-mcp .ag-mh .bad,.agc-app .agc-mcp .ag-sl .bad{{color:var(--ag-bad)}}
.agc-app.agc-app .agc-mcp .agc-servers{{gap:0;max-height:340px;overflow:hidden auto}}
.agc-app.agc-app .agc-mcp .agc-server{{gap:0;border-top:1px dashed var(--ag-line);padding:3px 0}}
.agc-app.agc-app .agc-mcp .agc-server:first-child{{border-top:0}}
.agc-app.agc-app .agc-mcp .agc-server>*,.agc-app.agc-app .agc-mcp .agc-sr>*{{margin:0}}
.agc-app.agc-app .agc-mcp .agc-sr{{align-items:center;gap:4px}}
.agc-app .agc-mcp .ag-sl{{display:flex;align-items:baseline;flex-wrap:wrap;gap:2px 10px;font-size:12.5px;line-height:1.5;min-width:0}}
.agc-app .agc-mcp .ag-sl b{{font-weight:600}}
.agc-app .agc-mcp .ag-sl code{{background:none;padding:0;font-size:12px;color:var(--ag-comment);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px}}
.agc-app .agc-mcp .ag-sl .ag-sm{{font-size:11.5px;color:var(--ag-comment);white-space:nowrap}}
.agc-app .agc-mcp .ag-dot{{display:inline-block;flex:0 0 auto;width:8px;height:8px;border-radius:50%;background:var(--ag-dim);opacity:.6;align-self:center}}
.agc-app .agc-mcp .ag-dot.ok{{background:var(--ag-ok);opacity:1}}
.agc-app .agc-mcp .ag-dot.bad{{background:var(--ag-bad);opacity:1}}
.agc-app .agc-mcp .ag-dot.run{{background:var(--ag-accent);opacity:1;animation:ag-shine 1.2s linear infinite}}
.agc-app .agc-mcp .ag-se{{flex:1 1 100%;font-size:12px;color:var(--ag-bad);overflow-wrap:anywhere}}
.agc-app .agc-mcp .ag-se .ag-k{{color:var(--ag-comment)}}
.agc-app.agc-app .agc-mcp .agc-x{{height:22px;min-width:22px;line-height:20px;padding:0 7px;margin:0;border-radius:5px;font-size:11.5px;background:transparent;border:1px solid transparent;box-shadow:none;color:var(--ag-comment)}}
.agc-app.agc-app .agc-mcp .agc-x:hover:enabled{{border-color:var(--ag-accent);color:var(--ag-accent)}}
.agc-app.agc-app .agc-mcp .agc-x.agc-rm:hover:enabled,.agc-app.agc-app .agc-mcp .agc-sure{{border-color:var(--ag-warn);color:var(--ag-warn)}}
.agc-app.agc-app .agc-mcp .agc-x.agc-on{{border-color:var(--ag-line);color:var(--ag-accent)}}
.agc-app.agc-app .agc-mcp .agc-tools{{gap:0;margin:2px 0 6px 18px;padding-left:12px;border-left:1px solid var(--ag-line)}}
.agc-app.agc-app .agc-mcp .agc-tools>*{{margin:0}}
.agc-app.agc-app .agc-mcp .agc-tool{{align-items:center;gap:6px}}
.agc-app.agc-app .agc-mcp .agc-tool>*{{margin:0}}
.agc-app.agc-app .agc-mcp .agc-tool .widget-checkbox,.agc-app.agc-app .agc-mcp .agc-tool .jupyter-widget-checkbox{{width:auto}}
.agc-app .agc-mcp .ag-tn{{font-size:12px;line-height:1.5;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.agc-app .agc-mcp .ag-tn .ag-tg{{display:inline-block;margin-right:8px;padding:0 6px;border-radius:9px;font-size:10.5px;border:1px solid var(--ag-line);color:var(--ag-comment)}}
.agc-app .agc-mcp .ag-tn .ag-tg.ro{{border-color:var(--ag-ok);color:var(--ag-ok)}}
.agc-app .agc-mcp .ag-tn .ag-tg.ask{{border-color:var(--ag-warn);color:var(--ag-warn)}}
.agc-app.agc-app .agc-mcp .agc-asks{{align-items:center;gap:10px;margin-bottom:4px}}
.agc-app.agc-app .agc-mcp .agc-asks>*{{margin:0}}
.agc-app.agc-app .agc-mcp select,.agc-app.agc-app .agc-mcp input,.agc-app.agc-app .agc-mcp textarea{{font-family:var(--ag-mono);font-size:12.5px;padding:0 8px;border:1px solid var(--ag-line);border-radius:6px;background-color:transparent;color:inherit;box-shadow:none}}
.agc-app.agc-app .agc-mcp select,.agc-app.agc-app .agc-mcp input{{height:28px}}
.agc-app.agc-app .agc-mcp textarea{{padding:4px 8px;min-height:44px;resize:vertical}}
.agc-app.agc-app .agc-mcp select option{{background:var(--ag-bg);color:inherit}}
.agc-app.agc-app .agc-mcp select:focus,.agc-app.agc-app .agc-mcp input:focus,.agc-app.agc-app .agc-mcp textarea:focus{{border-color:var(--ag-accent);outline:none}}
.agc-app.agc-app .agc-mcp .agc-form{{gap:6px;margin-top:8px;padding:10px 12px;border:1px solid var(--ag-line);border-radius:8px}}
.agc-app.agc-app .agc-mcp .agc-form>*{{margin:0}}
.agc-app.agc-app .agc-mcp .agc-field{{align-items:center;gap:10px}}
.agc-app.agc-app .agc-mcp .agc-field>*{{margin:0}}
.agc-app .agc-mcp .ag-lbl{{display:block;width:{_LABEL_WIDTH};color:var(--ag-comment)}}
.agc-app .agc-mcp .ag-fn{{margin:2px 0 0 calc({_LABEL_WIDTH} + 10px);font-size:12px;line-height:1.55;color:var(--ag-dim)}}
.agc-app .agc-mcp .ag-fn .warn{{color:var(--ag-warn)}}
.agc-app .agc-mcp .ag-fn .bad{{color:var(--ag-bad)}}
.agc-app.agc-app .agc-mcp .agc-form .agc-acts{{margin-left:calc({_LABEL_WIDTH} + 10px)}}
.agc-app.agc-app .agc-mcp .agc-acts{{gap:8px;margin-top:6px;flex-wrap:wrap}}"""


def mcp_button_label(states: list[ServerState]) -> str:
    """
    The head's button: '🔌 mcp' with no servers, '🔌 mcp 2/3' with three of which two are connected.
    """
    enabled = [state for state in states if state.server.enabled]
    if not enabled:
        return "🔌 mcp"
    ready = sum(1 for state in enabled if state.is_ready)
    return f"🔌 mcp {ready}/{len(enabled)}"


def panel_head_html(states: list[ServerState], *, path: Any, busy: str = "") -> str:
    """
    The line above the servers: how many are connected, failed and off, their tools, where they're saved, and what
    the panel is doing (busy).
    """
    if not states:
        parts = [f"<b>MCP servers</b> · {_esc(_NO_SERVERS)}"]
    else:
        ready = [state for state in states if state.is_ready]
        failed = sum(1 for state in states if state.status == FAILED)
        off = sum(1 for state in states if not state.server.enabled)
        tools = sum(len(state.usable_tools) for state in ready)
        parts = [f"<b>{_plural(len(states), 'MCP server')}</b>", f"{len(ready):,} connected", _plural(tools, "tool")]
        if failed:
            parts.insert(2, f'<span class="bad">{failed:,} failed</span>')
        if off:
            parts.insert(2, f"{off:,} off")
    where = f"saved in <code>{_esc(str(path))}</code>" if path is not None else "kept for this session only"
    parts.append(where)
    if busy:
        parts.append(f'<span class="warn">{_esc(busy)}</span>')
    return f'<div class="ag-mh">{" · ".join(parts)}</div>'


def _tools_text(state: ServerState) -> str:
    """
    A connected server's tools in a few words: '4 tools · 1 asks first', '3 of 5 tools on'.
    """
    usable, asking = state.usable_tools, state.asking_tools
    count = (f"{len(usable)} of {_plural(len(state.tools), 'tool')} on" if len(usable) != len(state.tools)
             else _plural(len(usable), "tool"))
    return f"{count} · {len(asking)} ask{'s' if len(asking) == 1 else ''} first" if asking else count


def server_line_html(state: ServerState) -> str:
    """
    One server's line: its dot, name, kind, what it runs or where it is, its tools (or what it's doing), the calls it
    has answered, and why it failed under it.
    """
    server = state.server
    tone, said = _STATUS_DOTS.get(state.status, ("off", "off"))
    if not server.enabled:
        tone, said = "off", "turned off"
    if state.is_ready:
        said = _tools_text(state)
    calls = f"{_plural(state.calls, 'call')}" + (f", {state.failed_calls} failed" if state.failed_calls else "")
    target = _clip(server.target, _TARGET_CHARACTERS)
    line = (f'<div class="ag-sl"><span class="ag-dot {tone}" title="{_esc(state.status)}"></span><b>{_esc(state.name)}'
            f'</b><span class="ag-sm">{_esc(server.transport)}</span><code title="{_esc(server.target)}">'
            f'{_esc(target)}</code><span class="ag-sm">{_esc(said)}</span>')
    if state.calls:
        line += f'<span class="ag-sm">{_esc(calls)}</span>'
    if state.status == FAILED and server.enabled:
        line += f'<span class="ag-se">✗ {_esc(_clip(state.error, _ERROR_CHARACTERS))}</span>'
    return line + "</div>"


def tool_line_html(state: ServerState, tool: McpTool) -> str:
    """
    One tool's line under its server: whether it says it only reads, whether it asks first, its name and what it
    does.
    """
    says = '<span class="ag-tg ro">reads only</span>' if tool.read_only else '<span class="ag-tg">may change</span>'
    if tool.destructive:
        says = '<span class="ag-tg ask">may delete</span>'
    asks = '<span class="ag-tg ask">asks first</span>' if asks_first(state.server, tool) else ""
    about = _clip(" ".join(tool.description.split()), _DESCRIPTION_CHARACTERS)
    return (f'<div class="ag-tn" title="{_esc(tool.description)}">{says}{asks}<b>{_esc(tool.name)}</b> '
            f'<span class="ag-k">{_esc(about)}</span></div>')


def form_note_html(text: str, *, error: str = "") -> str:
    """
    The note under the add form: what the server it would add does, or why it can't be added.
    """
    if error:
        return f'<div class="ag-fn"><span class="bad">✗ {_prose(error)}</span></div>'
    return f'<div class="ag-fn">{_prose(text)}</div>' if text else ""


def what_it_adds(servers: list[McpServer]) -> str:
    """
    What the add form would add, in a sentence: a command run here (and what it gets) or a URL called, or the
    servers a pasted mcp.json holds.
    """
    if len(servers) > 1:
        return f"Adds {_plural(len(servers), 'server')}: {', '.join(server.name for server in servers)}."
    server = servers[0]
    if server.is_remote:
        kind = "the older SSE transport" if server.transport == "sse" else "streamable HTTP"
        return f"Calls {server.url} over {kind}, as {server.name}."
    return (f"Runs {server.command} on this machine as {server.name}: it gets PATH and HOME and the variables you "
            "give it, not your AWS credentials.")


class _ServerRow:
    """
    One server in the panel: its line with its buttons (tools, ↻, on / off, ✕), and its tools, folded under it.

        is_open   its tools are shown
        sure      ✕ was clicked once, and the next click removes it
    """

    def __init__(self, panel: _McpPanel, state: ServerState):
        self.panel, self.state, self.w = panel, state, panel.w
        self.is_open = state.name in panel.opened
        self.sure = False
        self.root = self._build()

    def _build(self) -> Any:
        w, layout, state = self.w, self.w.Layout, self.state
        face = w.HTML(server_line_html(state), layout=layout(flex="1 1 auto", min_width="0"))
        buttons = []
        if state.tools:
            self.tools_button = self.panel._button("tools ▾" if self.is_open else "tools ▸",
                                                   "Its tools: turn one off, or say when it asks first",
                                                   self._toggle, "agc-x")
            _class_if(self.tools_button, "agc-on", self.is_open)
            buttons.append(self.tools_button)
        enabled = state.server.enabled
        buttons.append(self.panel._button("↻", "Connect it again", self._reconnect, "agc-x"))
        buttons.append(self.panel._button("off" if enabled else "on", "Turn it off: its tools aren't offered"
                                          if enabled else "Turn it on and connect it", self._switch, "agc-x"))
        self.remove_button = self.panel._button("✕", "Remove it (click twice)", self._remove, "agc-x")
        self.remove_button.add_class("agc-rm")
        buttons.append(self.remove_button)
        line = w.HBox([face, *buttons], layout=layout(width="100%"))
        line.add_class("agc-sr")
        children = [line, self._tools_box()] if self.is_open and state.tools else [line]
        root = w.VBox(children, layout=layout(width="100%"))
        root.add_class("agc-server")
        return root

    def _tools_box(self) -> Any:
        """
        Under an open server: when it asks first (a menu), then a line per tool with a box that turns it on or off.
        """
        w, layout, state = self.w, self.w.Layout, self.state
        asks = w.Dropdown(options=[(ASK_LABELS[key], key) for key in ASKS], value=state.server.asks,
                          layout=layout(width="auto"))
        asks.observe(self.panel.window._safely(lambda change: self._set_asks(change["new"])), names="value")
        label = w.HTML('<span class="ag-k">before a tool runs</span>', layout=layout(flex="0 0 auto"))
        rows = [w.HBox([label, asks], layout=layout(width="100%"))]
        rows[0].add_class("agc-asks")
        off = set(state.server.disabled_tools)
        for tool in state.tools:
            box = w.Checkbox(value=tool.name not in off, indent=False, layout=layout(width="auto", flex="0 0 auto"),
                             tooltip=f"Offer {tool.name} to the agent")
            box.observe(self.panel.window._safely(lambda change, name=tool.name: self._set_tool(name, change["new"])),
                        names="value")
            text = w.HTML(tool_line_html(state, tool), layout=layout(flex="1 1 auto", min_width="0"))
            row = w.HBox([box, text], layout=layout(width="100%"))
            row.add_class("agc-tool")
            rows.append(row)
        box = w.VBox(rows, layout=layout(width="auto"))
        box.add_class("agc-tools")
        return box

    def _toggle(self) -> None:
        self.panel.opened ^= {self.state.name}
        self.panel.refresh()

    def _set_asks(self, asks: str) -> None:
        self.panel.core.update_mcp(self.state.name, asks=asks)
        self.panel.changed(f"{self.state.name} now: {ASK_LABELS[asks]}.")

    def _set_tool(self, tool: str, is_on: bool) -> None:
        self.panel.core.update_mcp(self.state.name, tools={tool: is_on})
        self.panel.changed(f"{tool} is {'on' if is_on else 'off'}: from the next question on.")

    def _reconnect(self) -> None:
        name = self.state.name
        self.panel.in_background(lambda: self.panel.core.reconnect_mcp(name), f"connecting {name}…",
                                 lambda: self.panel.said_state(name))

    def _switch(self) -> None:
        name, enabled = self.state.name, not self.state.server.enabled
        if not enabled:
            self.panel.core.update_mcp(name, enabled=False)
            self.panel.changed(f"{name} is off: its tools aren't offered.")
            return
        self.panel.in_background(lambda: self.panel.core.update_mcp(name, enabled=True), f"connecting {name}…",
                                 lambda: self.panel.said_state(name))

    def _remove(self) -> None:
        if not self.sure:
            self.sure = True
            self.remove_button.description = "click again to remove"
            _class_if(self.remove_button, "agc-sure", True)
            return
        removed = self.panel.core.remove_mcp(self.state.name)
        self.panel.opened.discard(removed.name)
        self.panel.changed(f"Removed {removed.name}: its tools are gone from the next question on.")


class _McpPanel:
    """
    The agent's MCP servers, under the window's head while open: the line above them (head), a _ServerRow each
    (servers), the editors' mcp.json files to import (imports), the add form (form, shown by + add a server), and
    the buttons.

        opened   the servers whose tools are shown
        busy     what it's doing on a worker thread ('connecting github…'), '' when nothing
        retry    the server the add form added that didn't connect: trying again replaces it
        task     the asyncio task waiting for that work, in a notebook (tests await it)
    """

    def __init__(self, window: _AgentChat, widgets: Any):
        self.window, self.w = window, widgets
        self.opened: set[str] = set()
        self.busy = ""
        self.retry = ""
        self.task: Any = None
        self.editor_files: list[EditorConfig] | None = None
        self.root = self._build()

    @property
    def core(self) -> AgentAnalyzer:
        return self.window.view.core

    @property
    def is_open(self) -> bool:
        return self.root.layout.display != "none"

    def _button(self, label: str, tooltip: str, on_click: Callable[[], None], css: str) -> Any:
        button = self.w.Button(description=label, tooltip=tooltip,
                               layout=self.w.Layout(width="auto", flex="0 0 auto"))
        button.add_class(css)
        button.on_click(self.window._safely(lambda _button: on_click()))
        return button

    def _build(self) -> Any:
        w, layout = self.w, self.w.Layout
        self.head = w.HTML(layout=layout(width="100%"))
        self.servers = w.VBox(layout=layout(width="100%"))
        self.servers.add_class("agc-servers")
        self.findings = w.HTML(layout=layout(width="100%"))
        self.imports = w.HBox(layout=layout(width="100%"))
        self.imports.add_class("agc-acts")
        self.form = self._build_form()
        self.add_button = self._button("+ add a server", "Add an MCP server: a command, a URL or its JSON",
                                       self.show_form, "agc-new")
        self.reconnect_button = self._button("↻ reconnect all", "Connect every server that's on again",
                                             self._reconnect_all, "agc-new")
        close = self._button("close", "Close the MCP panel", self.close, "agc-new")
        actions = w.HBox([self.add_button, self.reconnect_button, close])
        actions.add_class("agc-acts")
        root = w.VBox([self.head, self.servers, self.findings, self.form, self.imports, actions],
                      layout=layout(display="none", width="100%"))
        root.add_class("agc-mcp")
        return root

    def _field(self, name: str, box: Any) -> tuple[Any, Any]:
        label = self.w.HTML(f'<span class="ag-lbl">{_esc(name)}</span>', layout=self.w.Layout(flex="0 0 auto"))
        row = self.w.HBox([label, box], layout=self.w.Layout(width="100%"))
        row.add_class("agc-field")
        return row, label

    def _build_form(self) -> Any:
        """
        The add form: the server (a command, a URL or JSON), its name, its settings (env or headers, by what the
        server box holds), when it asks, a note on what it would add, and connect & add.
        """
        w, layout = self.w, self.w.Layout
        grow = {"flex": "1 1 auto", "width": "auto"}
        self.server_box = w.Text(placeholder=_SERVER_PLACEHOLDER, continuous_update=True, layout=layout(**grow))
        self.server_box.observe(self.window._safely(lambda _change: self._typed()), names="value")
        self.server_box.on_msg(self.window._on_enter(self.add))
        self.name_box = w.Text(placeholder="named from what it runs", layout=layout(**grow))
        self.name_box.on_msg(self.window._on_enter(self.add))
        self.settings_box = w.Textarea(placeholder=_ENV_PLACEHOLDER, rows=2, layout=layout(**grow))
        self.asks_pick = w.Dropdown(options=[(ASK_LABELS[key], key) for key in ASKS], layout=layout(**grow))
        server_row, _ = self._field("server", self.server_box)
        name_row, _ = self._field("name", self.name_box)
        settings_row, self.settings_label = self._field("env", self.settings_box)
        asks_row, _ = self._field("asks", self.asks_pick)
        self.form_note = w.HTML(layout=layout(width="100%"))
        self.add_server_button = self._button(_ADD, "Connect to it, list its tools, and save it", self.add, "agc-use")
        cancel = self._button("cancel", "Close the form", self.hide_form, "agc-new")
        actions = w.HBox([self.add_server_button, cancel])
        actions.add_class("agc-acts")
        form = w.VBox([server_row, name_row, settings_row, asks_row, self.form_note, actions],
                      layout=layout(display="none", width="100%"))
        form.add_class("agc-form")
        return form

    def open(self) -> None:
        """
        Shows the servers as they stand, and connects the ones that are on and off in the background.
        """
        self.root.layout.display = ""
        _class_if(self.window.mcp_button, "agc-on", True)
        self.refresh()
        if self.core.mcp_kit is not None and any(s.status == OFF and s.server.enabled for s in self._states()):
            self.in_background(lambda: self.core.mcp_servers(), "connecting…", self.changed)

    def close(self) -> None:
        self.root.layout.display = "none"
        _class_if(self.window.mcp_button, "agc-on", False)

    def _states(self) -> list[ServerState]:
        kit = self.core.mcp_kit
        return kit.states if kit is not None else []

    def refresh(self) -> None:
        """
        Draws the head, the servers, their findings and the files to import again, as they stand now.
        """
        kit = self.core.mcp_kit
        self.window.mcp_button.description = mcp_button_label(self._states())
        if kit is None:
            self.window._set(self.head, f'<div class="ag-mh">{_prose(_MCP_OFF)}</div>')
            self.servers.children = ()
            self.add_button.layout.display = self.reconnect_button.layout.display = "none"
            return
        states = kit.states
        self.window._set(self.head, panel_head_html(states, path=kit.path, busy=self.busy))
        self.servers.children = tuple(_ServerRow(self, state).root for state in states)
        self.reconnect_button.layout.display = "" if states else "none"
        self.add_button.disabled = self.reconnect_button.disabled = bool(self.busy)
        self.add_server_button.disabled = bool(self.busy)
        warnings = [text for level, text in mcp_findings([], path=kit.path, problem=kit.problem) if level == "warn"]
        self.window._set(self.findings, "".join(f'<div class="ag-mh"><span class="warn">{_prose(text)}</span></div>'
                                                for text in warnings))
        self._draw_imports()

    def _draw_imports(self) -> None:
        """
        A button per mcp.json the editor keeps with servers the agent hasn't got, at most _IMPORTS_OFFERED.
        """
        if self.editor_files is None:
            self.editor_files = self.core.mcp_editor_files()
        mine = self.core.mcp_kit.servers if self.core.mcp_kit is not None else []
        buttons = []
        for config in self.editor_files:
            new = [s for s in config.servers if not any(s.same_place(m) for m in mine)]
            if not new:
                continue
            label = f"import {_plural(len(new), 'server')} from {config.label}"
            buttons.append(self._button(label, ", ".join(s.name for s in new),
                                        lambda path=config.path: self._import(path), "agc-new"))
        self.imports.children = tuple(buttons[:_IMPORTS_OFFERED])

    def changed(self, said: str = "") -> None:
        """
        The servers changed (from this panel, or from code): the panel, the window's head and every other window
        follow, and the status line says what happened.
        """
        self.window.view._tell_windows("mcp_changed", other_than=self.window)
        self.window.mcp_changed()
        if not self.is_open:
            self.refresh()
        if said:
            self.window._set_status(said, "ok")

    def said_state(self, name: str) -> None:
        """
        After a server was connected: says how it went on the status line.
        """
        state = self.core.mcp_kit.hub.state(name) if self.core.mcp_kit is not None else None
        if state is None:
            self.changed()
            return
        if state.is_ready:
            self.changed(f"{name} is connected: {_tools_text(state)}.")
            return
        self.changed()
        self.window._set_status(f"{name} didn't connect: {state.error}", "warn")

    def in_background(self, work: Callable[[], Any], busy: str, done: Callable[[], None],
                      failed: Callable[[BaseException], None] | None = None) -> None:
        """
        Runs `work` (connecting, which can take a while) on a worker thread in a notebook, so the window keeps
        answering clicks, then `done` on the loop (or `failed` with what it raised); right away without a loop.
        """
        self.busy = busy
        self.refresh()
        loop = _running_loop()
        if loop is None:
            try:
                work()
            except Exception as exc:  # noqa: BLE001
                self.busy = ""
                self._failed(exc, failed)
                return
            self.busy = ""
            done()
            return
        self.task = loop.create_task(self._later(work, done, failed))

    async def _later(self, work: Callable[[], Any], done: Callable[[], None],
                     failed: Callable[[BaseException], None] | None) -> None:
        job = asyncio.get_running_loop().run_in_executor(None, work)
        await asyncio.wait([job])
        self.busy = ""
        error = job.exception()
        if error is not None:
            self.window._safely(lambda: self._failed(error, failed))()
            return
        self.window._safely(done)()

    def _failed(self, exc: BaseException, failed: Callable[[BaseException], None] | None) -> None:
        self.refresh()
        if failed is not None:
            failed(exc)
            return
        self.window._warn(self.window._error_text(exc))

    def _reconnect_all(self) -> None:
        self.in_background(lambda: self.core.reconnect_mcp(), "connecting every server…", self._reconnected)

    def _reconnected(self) -> None:
        failed = [state.name for state in self._states() if state.status == FAILED]
        self.changed()
        if failed:
            self.window._set_status(f"{', '.join(failed)} didn't connect: open 🔌 mcp to see why.", "warn")
            return
        self.window._set_status("Every server that's on is connected.", "ok")

    def _import(self, path: Any) -> None:
        def done() -> None:
            self.editor_files = None
            self.changed("Imported: the agent offers their tools from the next question on.")

        self.in_background(lambda: self.core.import_mcp(path), "importing…", done)

    def show_form(self) -> None:
        self.form.layout.display = ""
        self.add_button.layout.display = "none"
        self._typed()

    def hide_form(self) -> None:
        self.form.layout.display = "none"
        self.add_button.layout.display = ""
        self.retry = ""
        self.add_server_button.description = _ADD
        for box in (self.server_box, self.name_box, self.settings_box):
            self.window._quietly(box, value="")
        self.window._set(self.form_note, "")

    def _parsed(self) -> list[McpServer]:
        return parse_server_text(_text(self.server_box), name=_text(self.name_box))

    def _typed(self) -> None:
        """
        What the server box holds now: the settings box asks for env or headers by it, the name box guesses a
        name, and the note says what it would add.
        """
        text = _text(self.server_box)
        is_url = text.lower().startswith(("http://", "https://"))
        self.settings_label.value = f'<span class="ag-lbl">{"headers" if is_url else "env"}</span>'
        self.settings_box.placeholder = _HEADER_PLACEHOLDER if is_url else _ENV_PLACEHOLDER
        if not text:
            self.name_box.placeholder = "named from what it runs"
            self.window._set(self.form_note, form_note_html("A command (npx, uvx, docker, a script), a URL, or an "
                                                           "mcp.json's JSON pasted whole."))
            return
        try:
            servers = parse_server_text(text)
        except ValueError as exc:
            self.window._set(self.form_note, form_note_html("", error=str(exc)))
            return
        self.name_box.placeholder = servers[0].name if len(servers) == 1 else "each named in its JSON"
        self.window._set(self.form_note, form_note_html(what_it_adds(servers)))

    def add(self) -> None:
        """
        connect & add (or Enter in a box): reads the form, adds the server (in place of the one the form added last
        time when it didn't connect), and connects it in the background; the form closes once it's connected, and
        stays open saying why when it isn't.
        """
        if self.busy:
            return
        try:
            servers = self._parsed()
        except ValueError as exc:
            self.window._set(self.form_note, form_note_html("", error=str(exc)))
            return
        settings = self.settings_box.value or None
        names = [server.name for server in servers]
        replace = bool(self.retry) and self.retry in names

        def work() -> list[ServerState]:
            return self.core.add_mcp(_text(self.server_box), name=_text(self.name_box),
                                     env=settings if not servers[0].is_remote else None,
                                     headers=settings if servers[0].is_remote else None,
                                     asks=self.asks_pick.value, replace=replace)

        self.in_background(work, f"connecting {', '.join(names)}…", lambda: self._added(names), self._not_added)

    def _added(self, names: list[str]) -> None:
        states = [state for state in self._states() if state.name in names]
        broken = [state for state in states if not state.is_ready]
        if not broken:
            summary = "; ".join(f"{state.name}: {_tools_text(state)}" for state in states)
            self.hide_form()
            self.opened.update(names)
            self.changed(f"Added {summary}. Its tools join the agent's from the next question on.")
            return
        self.retry = broken[0].name
        self.add_server_button.description = _RETRY
        self.changed()
        error = f"{broken[0].name} didn't connect: {broken[0].error} It's kept, so you can fix it and try again."
        self.window._set(self.form_note, form_note_html("", error=error))

    def _not_added(self, exc: BaseException) -> None:
        self.window._set(self.form_note, form_note_html("", error=self.window._error_text(exc)))
