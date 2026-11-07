import asyncio
import json
import os
import shlex
import stat
import sys
import threading
from pathlib import Path

import pytest

from aws_analyzer._kit.errors import _Hint
from aws_analyzer.agent import AgentAnalyzer, AgentView, BigRead
from aws_analyzer.agent.harness import _langchain_tools
from aws_analyzer.agent.mcp_servers import (
    ASKS_EVERY,
    ASKS_NEVER,
    FAILED,
    OFF,
    READY,
    McpHub,
    McpServer,
    ServerState,
    asks_first,
    editor_configs,
    guess_name,
    mcp_findings,
    mcp_path,
    parse_server_text,
    parse_settings_text,
    read_servers,
    server_entry,
    servers_from_config,
    shown_settings,
    tool_name,
    write_servers,
)
from aws_analyzer.agent.mcp_servers.client import McpCallFailed, McpTool, describe_error, tool_from
from aws_analyzer.agent.mcp_servers.config import environment_names, expand_references, split_tool_name
from aws_analyzer.agent.toolkits.mcp_tools import McpToolkit, mcp_toolkit, summarize_mcp_result, tool_signature
from aws_analyzer.agent.window.mcp_panel import mcp_button_label, panel_head_html, server_line_html, what_it_adds
from test_agent import calls, enter, page, run, says, scripted, window

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")
SERVER = shlex.join([sys.executable, str(FAKE_SERVER)])
REGION = "us-east-1"


def needs_mcp():
    pytest.importorskip("mcp")


@pytest.fixture
def hub():
    made = McpHub(connect_seconds=30, call_seconds=30)
    yield made
    made.close()


def agent(model=None, *, mcp=None, **settings):
    """
    An agent with the Lambda toolkit (no AWS read unless a Lambda tool runs) and these MCP servers, kept in memory.
    """
    return AgentAnalyzer(region=REGION, toolkits=["lambda"], chat_model=model, mcp=[SERVER] if mcp is None else mcp,
                         findings=False, verify=False, **settings)



def test_a_command_line_becomes_a_stdio_server_named_from_its_package():
    [server] = parse_server_text("GITHUB_TOKEN=${env:GH} npx -y @modelcontextprotocol/server-github")
    assert (server.name, server.transport, server.command, server.args) == (
        "github", "stdio", "npx", ["-y", "@modelcontextprotocol/server-github"])
    assert server.env == {"GITHUB_TOKEN": "${env:GH}"} and server.target == "npx -y @modelcontextprotocol/server-github"
    assert guess_name(parse_server_text("uvx awslabs.aws-documentation-mcp-server@latest")[0]) == "aws-documentation"
    assert parse_server_text("docker run -i --rm ghcr.io/github/github-mcp-server")[0].name == "github"
    assert parse_server_text("python /opt/tools/notes_server.py")[0].name == "notes"
    assert parse_server_text("npx -y @modelcontextprotocol/server-memory", name="My Memory")[0].name == "my-memory"
    with pytest.raises(ValueError, match="check its quotes"):
        parse_server_text("npx 'unclosed")
    with pytest.raises(ValueError, match="Type the command"):
        parse_server_text("  ")


def test_a_url_is_a_remote_server_and_sse_is_told_by_its_path():
    [notion] = parse_server_text("https://mcp.notion.com/mcp")
    assert (notion.name, notion.transport, notion.is_remote, notion.target) == (
        "notion", "http", True, "https://mcp.notion.com/mcp")
    assert parse_server_text("https://example.com/sse")[0].transport == "sse"
    assert parse_server_text("http://localhost:8000/mcp")[0].name == "localhost"


def test_json_from_any_apps_mcp_json_reads_the_same():
    vscode = {"servers": {"Docs": {"type": "http", "url": "https://docs.example.com/mcp",
                                   "headers": {"Authorization": "Bearer ${input:token}"}},
                          "files": {"type": "stdio", "command": "npx", "args": ["-y", "server-filesystem", "/data"]}},
              "inputs": [{"id": "token", "type": "promptString"}]}
    docs, files = servers_from_config(vscode, source="VS Code")
    assert (docs.name, docs.transport, docs.headers) == ("docs", "http", {"Authorization": "Bearer ${input:token}"})
    assert (files.name, files.command, files.args, files.source) == ("files", "npx", ["-y", "server-filesystem",
                                                                                      "/data"], "VS Code")
    claude = parse_server_text(json.dumps({"mcpServers": {"memory": {"command": "npx -y server-memory",
                                                                     "disabled": True, "asks": "every"}}}))
    assert (claude[0].command, claude[0].args, claude[0].enabled, claude[0].asks) == (
        "npx", ["-y", "server-memory"], False, "every")
    [lone] = parse_server_text('{"command": "uvx", "args": ["mcp-server-time"]}', name="clock")
    assert (lone.name, lone.command) == ("clock", "uvx")
    assert parse_server_text('{"serverUrl": "https://x.example.com/sse"}')[0].transport == "sse"
    with pytest.raises(ValueError, match="doesn't read"):
        parse_server_text("{not json")
    with pytest.raises(ValueError, match="neither a command"):
        servers_from_config({"mcpServers": {"x": {"args": ["a"]}}})
    with pytest.raises(ValueError, match="No MCP servers"):
        servers_from_config({"something": 1})


def test_a_server_is_written_back_in_the_shape_every_app_reads():
    server = McpServer("files", command="npx", args=["-y", "server-filesystem"], env={"ROOT": "/data"},
                       enabled=False, asks=ASKS_EVERY, disabled_tools=["write_file"])
    entry = server_entry(server)
    assert entry == {"type": "stdio", "command": "npx", "args": ["-y", "server-filesystem"], "env": {"ROOT": "/data"},
                     "disabled": True, "asks": "every", "disabledTools": ["write_file"]}
    [back] = servers_from_config({"mcpServers": {"files": entry}})
    assert back.same_place(server) and back.disabled_tools == ["write_file"] and not back.enabled
    assert server_entry(McpServer("docs", transport="http", url="https://d/mcp")) == {"type": "http",
                                                                                      "url": "https://d/mcp"}


def test_settings_lines_references_and_what_shows():
    assert parse_settings_text("A=1\n# a comment\n\nAuthorization: Bearer x=y\nB=a:b") == {
        "A": "1", "Authorization": "Bearer x=y", "B": "a:b"}
    with pytest.raises(ValueError, match="has neither"):
        parse_settings_text("just words")
    assert expand_references("Bearer ${env:TOKEN} ${HOME} ${input:x}", {"TOKEN": "t", "HOME": "/h"}) == (
        "Bearer t /h ", ["input:x"])
    assert shown_settings({"A": "secret", "B": "${env:B}", "C": "Bearer ${TOKEN}"}) == {
        "A": "(hidden)", "B": "${env:B}", "C": "Bearer ${TOKEN}"}
    server = McpServer("gh", command="npx", env={"GITHUB_TOKEN": "${env:GH_TOKEN}"})
    assert environment_names(server, {}) == ["GH_TOKEN"] and environment_names(server, {"GH_TOKEN": "x"}) == []


def test_tool_names_are_what_every_provider_takes():
    assert tool_name("github", "create_issue") == "github__create_issue"
    assert tool_name("my server", "do.it/now") == "my_server__do_it_now"
    assert len(tool_name("s" * 40, "t" * 40)) == 64
    assert split_tool_name("github__create_issue") == ("github", "create_issue") and split_tool_name("ls") is None



def test_the_file_keeps_what_else_it_holds_and_is_readable_by_its_owner_only(tmp_path):
    path = tmp_path / ".vscode" / "mcp.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"inputs": [{"id": "token"}], "servers": {"old": {"command": "x"}}}))
    write_servers(path, [McpServer("memory", command="npx", args=["-y", "server-memory"])])
    data = json.loads(path.read_text())
    assert data == {"inputs": [{"id": "token"}],
                    "servers": {"memory": {"type": "stdio", "command": "npx", "args": ["-y", "server-memory"]}}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and not path.with_name("mcp.json.part").exists()
    servers, problem = read_servers(path)
    assert [s.name for s in servers] == ["memory"] and problem == ""
    assert read_servers(tmp_path / "missing.json") == ([], "")
    (tmp_path / "bad.json").write_text("{nope")
    assert "isn't JSON" in read_servers(tmp_path / "bad.json")[1]


def test_where_the_agents_servers_are_kept(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ANALYZER_MCP")
    assert mcp_path(True) == Path("~/.cache/aws-analyzer/agent/mcp.json").expanduser()
    monkeypatch.setenv("AWS_ANALYZER_MCP", str(tmp_path / "m.json"))
    assert mcp_path(True) == tmp_path / "m.json" and mcp_path("~/x.json") == Path("~/x.json").expanduser()
    monkeypatch.setenv("AWS_ANALYZER_MCP", "off")
    assert mcp_path(True) is None and mcp_path(False) is None and mcp_path({"mcpServers": {}}) is None


def test_editor_files_are_found_from_the_notebooks_folder_up(tmp_path):
    project = tmp_path / "home" / "work" / "project"
    (project / "notebooks").mkdir(parents=True)
    (project / ".vscode").mkdir()
    (project / ".vscode" / "mcp.json").write_text(json.dumps({"servers": {"docs": {"url": "https://d/mcp"}}}))
    (project / ".mcp.json").write_text("{broken")
    found = editor_configs(project / "notebooks", home=tmp_path / "home")
    assert [(config.app, [s.name for s in config.servers]) for config in found] == [("VS Code", ["docs"]),
                                                                                   ("Claude Code", [])]
    assert "isn't JSON" in found[1].problem
    assert editor_configs(project / "notebooks", home=tmp_path / "home", skip=project / ".mcp.json")[1:] == []



def test_the_hub_connects_lists_tools_and_calls_them(hub):
    needs_mcp()
    hub.set_servers(parse_server_text(SERVER, name="desk"))
    [state] = hub.connect()
    assert state.status == READY and state.info.startswith("ticket-desk") and "acme team" in state.instructions
    tools = {tool.name: tool for tool in state.tools}
    assert sorted(tools) == ["broken", "close_ticket", "find_tickets", "ticket_counts"]
    assert tools["find_tickets"].read_only and not tools["close_ticket"].read_only
    assert tools["find_tickets"].title == "Find tickets" and tools["find_tickets"].qualified == "desk__find_tickets"
    assert tools["find_tickets"].schema["properties"]["limit"]["type"] == "integer"
    answer = hub.call("desk", "find_tickets", {"text": "etl"})
    assert answer["content"][0]["text"] == "T-1: orders-etl fails at 02:00" and not answer["isError"]
    assert hub.call("desk", "broken", {})["isError"] and (state.calls, state.failed_calls) == (2, 0)
    hub.disconnect("desk")
    assert state.status == OFF
    assert hub.call("desk", "find_tickets", {"text": "slow"})["content"][0]["text"].startswith("T-2")


def test_a_server_that_wont_start_says_why(hub):
    needs_mcp()
    quits = parse_server_text(f"FAKE_MCP_SAY='no licence key' FAKE_MCP_QUIT=1 {SERVER}", name="quits")
    missing = parse_server_text("npx-that-isnt-here -y server", name="missing")
    hub.set_servers([*quits, *missing])
    states = {state.name: state for state in hub.connect()}
    assert states["quits"].status == FAILED and "It said: no licence key" in states["quits"].error
    assert states["missing"].status == FAILED and "isn't installed here" in states["missing"].error
    assert hub.connect() and states["quits"].status == FAILED
    with pytest.raises(McpCallFailed, match="isn't connected"):
        hub.call("quits", "find_tickets", {})
    with pytest.raises(McpCallFailed, match="no MCP server named"):
        hub.call("nope", "x", {})


def test_errors_read_as_what_to_do():
    npx = McpServer("x", command="npx")
    assert "comes with Node.js" in describe_error(FileNotFoundError("npx"), npx)
    remote = McpServer("r", transport="http", url="https://r.example.com/mcp")

    class Refused(Exception):
        response = type("R", (), {"status_code": 401})()

    assert "can't sign in with OAuth" in describe_error(Refused("401"), remote)
    group = type("Group", (Exception,), {})("wrapped")
    group.exceptions = [ConnectionError("down")]
    assert describe_error(group, remote) == "ConnectionError: down"


def test_a_tool_says_whether_it_asks_first():
    reads = tool_from("desk", {"name": "find", "annotations": {"readOnlyHint": True}})
    writes = tool_from("desk", {"name": "drop", "annotations": {"destructiveHint": True}, "inputSchema": None})
    assert reads.read_only and not writes.read_only and writes.destructive and writes.schema["type"] == "object"
    assert not asks_first(McpServer("d"), reads) and asks_first(McpServer("d"), writes)
    assert asks_first(McpServer("d", asks=ASKS_EVERY), reads) and not asks_first(McpServer("d", asks=ASKS_NEVER),
                                                                                writes)



def test_what_the_model_reads_of_an_answer():
    text = summarize_mcp_result({"content": [{"type": "text", "text": "line one\npassword=hunter2"},
                                             {"type": "image", "mimeType": "image/png", "data": "..."}]})
    assert text == {"text": "line one\npassword=[hidden]", "also": ["a picture (image/png), not sent"],
                    "_summary": "line one"}
    structured = summarize_mcp_result({"content": [{"type": "text", "text": '{"open": 3, "api_key": "k"}'}],
                                       "structuredContent": {"open": 3, "api_key": "k"}})
    assert structured["data"] == {"open": 3, "api_key": "[hidden]"} and "text" not in structured
    fastmcp = summarize_mcp_result({"content": [{"type": "text", "text": "T-1"}], "structuredContent": {"result": "T-1"}})
    assert fastmcp["text"] == "T-1" and "data" not in fastmcp
    failed = summarize_mcp_result({"content": [{"type": "text", "text": "the desk is down"}], "isError": True})
    assert failed["error"] == "the tool said it failed" and failed["_summary"] == "failed: the desk is down"
    long = summarize_mcp_result({"content": [{"type": "text", "text": "x" * 50}]}, characters=10)
    assert long["text"] == "x" * 10 and long["cut_short"] and "of its 50 characters" in long["note"]


def test_a_tools_schema_becomes_its_signature():
    signature = tool_signature({"properties": {"text": {"type": "string"}, "limit": {"type": ["integer", "null"],
                                                                                      "default": 5}},
                                "required": ["text"]})
    assert str(signature) == "(*, text: str, limit: int = 5)"
    assert str(tool_signature({"properties": {"file-path": {"type": "string"}}})) == "(**arguments)"



def test_mcp_tools_join_the_agents_own_with_their_schema_and_prompt():
    needs_mcp()
    pytest.importorskip("deepagents")
    core = agent()
    names = [fn.__name__ for fn in core.tools()]
    assert {"fake__find_tickets", "fake__close_ticket"} <= set(names) and "function_health" in names
    assert core.reads["fake__find_tickets"] == ("mcp:CallTool",)
    text = core.instructions()
    assert "\nMCP servers\n- The user connected these MCP servers" in text and "fake__find_tickets" in text
    assert "It says: Tickets about the acme team's AWS jobs." in text and "tools of the MCP servers" in text
    made = {getattr(t, "name", getattr(t, "__name__", "")): t for t in _langchain_tools(core.tools())}
    assert made["fake__find_tickets"].args_schema["properties"]["limit"] == {"default": 10, "title": "Limit",
                                                                            "type": "integer"}
    assert callable(made["function_health"])
    core.mcp_kit.hub.close()


def test_a_tool_that_only_reads_runs_and_one_that_may_change_something_asks_first():
    needs_mcp()
    model = scripted(calls(("fake__find_tickets", {"text": "etl"})), calls(("fake__close_ticket", {"ticket": "T-1"})),
                     says("I closed T-1."), calls(("fake__close_ticket", {"ticket": "T-2"})), says("Left T-2 open."))
    core = agent(model)
    answer = core.ask("Close the etl ticket")
    found, closing = answer.calls
    assert found.summary == "T-1: orders-etl fails at 02:00" and found.services == ("mcp",) and not found.waiting
    assert closing.paused and "doesn't say it only reads" in closing.waiting.what and closing.waiting.smaller == ""
    assert closing.waiting.question.endswith("Go: run it, this once. Skip: answer without it.")
    done = core.resume("go")
    assert done.text == "I closed T-1." and done.calls[1].summary == "closed T-1"
    skipped = core.ask("Close T-2 as well")
    assert skipped.waiting
    left = core.resume("skip")
    assert left.calls[0].refused == "skipped" and "chose not to run fake__close_ticket" in left.calls[0].result["message"]
    assert core.mcp_kit.hub.state("fake").calls == 2
    core.mcp_kit.hub.close()


def test_asks_never_runs_without_asking_and_a_failing_tool_is_an_error():
    needs_mcp()
    model = scripted(calls(("fake__close_ticket", {"ticket": "T-3"}), ("fake__broken", {})), says("done"))
    core = agent(model, mcp={"mcpServers": {"fake": {"command": sys.executable, "args": [str(FAKE_SERVER)],
                                                     "asks": "never"}}})
    answer = core.ask("Close T-3")
    closed, broken = answer.calls
    assert closed.summary == "closed T-3" and not closed.waiting
    assert broken.result["error"] == "the tool said it failed" and broken.summary == "failed: Error executing tool " \
        "broken: the desk is down"
    core.mcp_kit.hub.close()


def test_add_switch_and_remove_servers_saved_in_the_file(tmp_path):
    needs_mcp()
    path = tmp_path / "mcp.json"
    core = AgentAnalyzer(region=REGION, toolkits=["lambda"], mcp=path)
    [state] = core.add_mcp(SERVER, name="desk", env="EXTRA=1")
    assert state.status == READY and json.loads(path.read_text())["mcpServers"]["desk"]["env"] == {"EXTRA": "1"}
    with pytest.raises(ValueError, match="already an MCP server named 'desk'.*'desk-2'"):
        core.add_mcp(SERVER, name="desk", connect=False)
    core.update_mcp("desk", tools={"close_ticket": False}, asks="every")
    saved = json.loads(path.read_text())["mcpServers"]["desk"]
    assert saved["disabledTools"] == ["close_ticket"] and saved["asks"] == "every"
    assert "desk__close_ticket" not in [fn.__name__ for fn in core.tools()]
    core.update_mcp("desk", enabled=False)
    assert core.mcp_kit.hub.state("desk").status == OFF and not [fn for fn in core.tools() if "desk__" in fn.__name__]
    again = AgentAnalyzer(region=REGION, toolkits=["lambda"], mcp=path)
    assert [s.name for s in again.mcp_kit.servers] == ["desk"] and not again.mcp_kit.servers[0].enabled
    core.remove_mcp("Desk")
    assert json.loads(path.read_text()) == {"mcpServers": {}}
    with pytest.raises(_Hint, match="no MCP server named 'desk'"):
        core.remove_mcp("desk")
    core.mcp_kit.hub.close()


def test_importing_an_editors_file_skips_what_the_agent_has(tmp_path):
    vscode = tmp_path / "mcp.json"
    vscode.write_text(json.dumps({"servers": {"memory": {"command": "npx", "args": ["-y", "server-memory"]},
                                              "fake": {"command": "elsewhere"}}}))
    core = agent(mcp=[McpServer("memory", command="npx", args=["-y", "server-memory"]),
                      McpServer("fake", command="other")])
    states = core.import_mcp(vscode, connect=False)
    assert [state.name for state in states] == ["fake-2"]
    with pytest.raises(_Hint, match="isn't JSON"):
        (tmp_path / "bad.json").write_text("{")
        core.import_mcp(tmp_path / "bad.json")


def test_mcp_off_says_how_to_turn_it_on(capsys):
    core = AgentAnalyzer(region=REGION, toolkits=["lambda"], mcp=False)
    assert core.mcp_kit is None and [t.name for t in core.toolkits] == ["lambda", "runbooks"]
    with pytest.raises(_Hint, match=r"mcp=False"):
        core.mcp_servers()
    assert "MCP servers are off" in run(capsys, AgentView(core, mode="text").mcp)


def test_given_servers_are_named_once():
    kit = mcp_toolkit(["npx -y server-memory", "npx -y server-memory", {"command": "uvx", "args": ["x-mcp"]}])
    assert [server.name for server in kit.servers] == ["memory", "memory-2", "x"] and kit.path is None
    assert mcp_toolkit(False) is None and mcp_toolkit(kit) is kit
    assert isinstance(mcp_toolkit(True), McpToolkit)



def test_findings_say_what_to_fix():
    failed = ServerState(McpServer("gh", command="npx", env={"GITHUB_TOKEN": "${env:NOT_SET_HERE}"}), status=FAILED,
                         error="Connection closed.")
    loose = ServerState(McpServer("files", command="npx", env={"KEY": "abc"}, asks=ASKS_NEVER), status=READY,
                        tools=[McpTool("files", "write_file"), McpTool("files", "drop", destructive=True)])
    findings = mcp_findings([failed, loose], path=Path("/x/mcp.json"))
    texts = [text for _level, text in findings]
    assert findings[0] == ("warn", "gh isn't connected: Connection closed. Its tools are left out until it is: fix "
                                   "it, then reconnect_mcp('gh'), or remove_mcp('gh').")
    assert any("NOT_SET_HERE" in text and "os.environ['NOT_SET_HERE']" in text for text in texts)
    assert any("never asks first, and 2 tools of it" in text for text in texts)
    assert any("drop say they may delete" in text for text in texts)
    assert any("keeps KEY in /x/mcp.json" in text and "${env:KEY}" in text for text in texts)
    assert [level for level, _ in findings] == ["warn", "warn", "warn", "info", "info"]
    assert "No MCP servers yet" in mcp_findings([], path=None)[0][1]


def test_the_mcp_report_and_the_tools_report(capsys):
    needs_mcp()
    view = AgentView(agent(mcp=[SERVER, "npx-that-isnt-here server"]), mode="text", progress="off")
    out = run(capsys, view.mcp)
    assert "MCP servers\n===========\n2 servers · kept for this session only" in out
    assert "Connected: 1 of 2 (!)" in out and "Tools: 4" in out and "Ask first: 1" in out
    assert "[!] npx-that-isnt-here isn't connected: npx-that-isnt-here isn't installed here" in out
    assert "fake__close_ticket   may change  yes" in out and "reconnect_mcp('npx-that-isnt-here')" in out
    tools = run(capsys, view.tools)
    assert "Lambda, read-only on AWS, with 1 MCP server (fake)" in tools
    assert "MCP server fake (not an AWS read)" in tools and "Your MCP servers get the arguments" in tools
    view.core.mcp_kit.hub.close()


def test_the_view_adds_and_removes(capsys, tmp_path):
    needs_mcp()
    view = AgentView(region=REGION, toolkits=["lambda"], mcp=tmp_path / "mcp.json", mode="text", progress="off")
    out = run(capsys, view.add_mcp, SERVER, name="desk")
    assert "Added desk" in out and "Connected: 1 of 1" in out
    assert "Removed desk" in run(capsys, view.remove_mcp, "desk")
    assert "There's no MCP server named 'desk'" in run(capsys, view.remove_mcp, "desk")
    view.core.mcp_kit.hub.close()



def test_the_panel_lines_say_how_each_server_stands():
    ready = ServerState(McpServer("desk", command="python", args=["s.py"]), status=READY, calls=3, failed_calls=1,
                        tools=[McpTool("desk", "find", read_only=True), McpTool("desk", "close")])
    failed = ServerState(McpServer("gh", transport="http", url="https://gh/mcp"), status=FAILED, error="refused")
    off = ServerState(McpServer("old", command="x", enabled=False))
    assert mcp_button_label([ready, failed, off]) == "🔌 mcp 1/2" and mcp_button_label([]) == "🔌 mcp"
    line = server_line_html(ready)
    assert 'ag-dot ok' in line and "2 tools · 1 asks first" in line and "3 calls, 1 failed" in line
    assert "✗ refused" in server_line_html(failed) and "turned off" in server_line_html(off)
    head = panel_head_html([ready, failed, off], path=Path("/x/mcp.json"), busy="connecting gh…")
    assert "<b>3 MCP servers</b> · 1 connected · 1 off · " in head and "1 failed" in head and "connecting gh…" in head
    assert "not your AWS credentials" in what_it_adds(parse_server_text("npx -y server-memory"))
    assert "over the older SSE transport" in what_it_adds(parse_server_text("https://x/sse"))


def test_adding_a_server_in_the_window_and_running_its_tool():
    needs_mcp()
    model = scripted(calls(("desk__close_ticket", {"ticket": "T-1"})), says("Closed it."))
    x = window(model, toolkits=["lambda"], findings=False, verify=False)
    assert x.mcp_button.description == "🔌 mcp"
    x.mcp_button.click()
    panel = x.mcp_panel
    assert panel.is_open and "No MCP servers yet" in panel.head.value
    panel.add_button.click()
    panel.server_box.value = "https://example.com/mcp"
    assert "headers" in panel.settings_label.value and panel.name_box.placeholder == "example"
    panel.server_box.value = "npx 'broken"
    assert "check its quotes" in panel.form_note.value
    panel.server_box.value = SERVER
    panel.name_box.value = "desk"
    panel.add_server_button.click()
    assert "Added desk: 4 tools · 1 asks first" in x.status.value and panel.form.layout.display == "none"
    assert x.mcp_button.description == "🔌 mcp 1/1" and "desk" in panel.opened
    rows = panel.servers.children
    assert len(rows) == 1 and len(rows[0].children) == 2
    enter(x, "Close T-1")
    assert "✋ Run this MCP tool?" in page(x) and "# close ticket, on desk" in page(x)
    labels = [button.description for button in x.turn.ask.children[1:]]
    assert labels == ["run ▸", "skip"]
    x.turn.ask.children[1].click()
    assert "Closed it." in page(x) and "closed T-1" in page(x)
    x.view.core.mcp_kit.hub.close()


def test_a_server_that_doesnt_connect_stays_to_fix_and_try_again():
    needs_mcp()
    x = window(scripted(says("ok")), toolkits=["lambda"])
    x.mcp_button.click()
    panel = x.mcp_panel
    panel.add_button.click()
    panel.server_box.value = f"FAKE_MCP_QUIT=1 FAKE_MCP_SAY=bye {SERVER}"
    panel.name_box.value = "desk"
    panel.add_server_button.click()
    assert "desk didn&#x27;t connect" in panel.form_note.value and "It said: bye" in panel.form_note.value
    assert panel.add_server_button.description == "try again" and panel.retry == "desk"
    panel.server_box.value = SERVER
    panel.add_server_button.click()
    assert [s.name for s in x.view.core.mcp_kit.servers] == ["desk"] and "Added desk" in x.status.value
    row = panel.servers.children[0]
    remove = row.children[0].children[-1]
    remove.click()
    assert remove.description == "click again to remove"
    remove.click()
    assert x.view.core.mcp_kit.servers == [] and "Removed desk" in x.status.value
    x.view.core.mcp_kit.hub.close()


def test_the_panel_connects_off_the_loop_in_a_notebook():
    needs_mcp()
    x = window(scripted(says("ok")), toolkits=["lambda"], mcp=[SERVER])
    gate = threading.Event()
    hub = x.view.core.mcp_kit.hub
    connect = hub.connect

    def held(*args, **kwargs):
        gate.wait(10)
        return connect(*args, **kwargs)

    hub.connect = held

    async def go():
        x.mcp_button.click()
        assert "connecting…" in x.mcp_panel.head.value and x.mcp_panel.add_button.disabled
        gate.set()
        await x.mcp_panel.task

    asyncio.run(go())
    assert "1 connected" in x.mcp_panel.head.value and x.mcp_button.description == "🔌 mcp 1/1"
    hub.close()


def test_switching_a_tool_off_in_the_panel():
    needs_mcp()
    x = window(scripted(says("ok")), toolkits=["lambda"], mcp=[SERVER])
    x.mcp_button.click()
    row = x.mcp_panel.servers.children[0]
    row.children[0].children[1].click()
    tools = x.mcp_panel.servers.children[0].children[1]
    asks, *lines = tools.children
    close = next(line for line in lines if "close_ticket" in line.children[1].value)
    close.children[0].value = False
    assert x.view.core.mcp_kit.servers[0].disabled_tools == ["close_ticket"]
    assert "close_ticket is off" in x.status.value
    asks.children[1].value = "never"
    assert x.view.core.mcp_kit.servers[0].asks == "never" and "never ask" in x.status.value
    x.view.core.mcp_kit.hub.close()


def test_big_reads_without_a_smaller_one_offer_go_and_skip(capsys):
    read = BigRead("desk__close", what="It would close T-1.", go="run it, this once", smaller="")
    assert read.question == "It would close T-1. Go: run it, this once. Skip: answer without it."
    assert os.environ["AWS_ANALYZER_MCP"] == "off"


def test_the_line_of_a_servers_stderr_that_says_why():
    from aws_analyzer.agent.mcp_servers.client import _telling_line

    npm = ("npm error code E404\nnpm error 404 Not Found - GET https://registry.npmjs.org/@acme%2fjira - Not found\n"
           "npm error 404\nnpm error A complete log of this run can be found in: /x.log")
    assert _telling_line(npm) == "npm error 404 Not Found - GET https://registry.npmjs.org/@acme%2fjira - Not found"
    assert _telling_line("Traceback\n  File x\nModuleNotFoundError: No module named 'mcp'") == (
        "ModuleNotFoundError: No module named 'mcp'")
    assert _telling_line("boom") == "boom" and _telling_line("") == ""
