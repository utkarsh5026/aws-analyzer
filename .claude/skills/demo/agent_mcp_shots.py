"""
Remake the screenshots of the agent chat window's 🔌 mcp panel: AgentView.chat() with two MCP servers, in a real
JupyterLab, light and dark.

    agentenv/bin/python .claude/skills/demo/agent_mcp_shots.py                  # every figure
    agentenv/bin/python .claude/skills/demo/agent_mcp_shots.py agent-mcp        # just this one (no -light/-dark)
    agentenv/bin/python .claude/skills/demo/agent_mcp_shots.py --out /tmp/shots # PNGs there, docs left alone

The notebook opens the window on demo.py's Lambda functions (moto, us-east-1) with two MCP servers: tickets, the
tests' ticket desk (tests/fake_mcp_server.py, copied beside the notebook as ticket_desk.py and run over stdio), and
jira, a command that isn't installed, so the panel shows a failed server and why. A .vscode/mcp.json beside the
notebook holds a GitHub server, which the panel offers to import. A scripted model stands in for Claude Haiku 4.5: it
looks for a ticket (tickets__find_tickets, which only reads), then closes it (tickets__close_ticket, which may change
something, so the window asks run or skip), and answers once it's run.

It takes the panel open (agent-mcp: one server connected, one failed, the import), its tools open (agent-mcp-tools:
when it asks first, a box per tool), the add form with a URL typed in (agent-mcp-add), the question waiting for run or
skip (agent-mcp-ask), and the answer once it ran (agent-mcp-answer, the steps unfolded). Each figure is the window,
984 CSS px wide at 1.5x, written to docs/images/<name>-{light,dark}.webp, and the height= of both its images is set in
docs/agent.md by shots.py's set_height.

Needs aws-analyzer's requirements (pip install -r requirements-dev.txt), jupyterlab, playwright and
Pillow, and Chromium: $CHROME, or the one Playwright installs (playwright install chromium).
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402
from agent_chat_shots import MODEL, save, start_lab  # noqa: E402
from chat_shots import api, chrome, free_port  # noqa: E402

WIDTH, SCALE = shots.WIDTH, shots.SCALE
FIGURES = ("agent-mcp", "agent-mcp-tools", "agent-mcp-add", "agent-mcp-ask", "agent-mcp-answer")
QUESTION = "Is there a ticket about orders-etl? Close it, I fixed the KeyError."
REMOTE = "https://mcp.example.com/mcp"
HEADER = "Authorization: Bearer ${env:EXAMPLE_TOKEN}"
PAUSE_SECONDS = 1.0
VSCODE_CONFIG = {"servers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}}}

NOTEBOOK_CODE = f"""import os, sys
from pathlib import Path
sys.path[:0] = [{str(ROOT / "src")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1", "AWS_ANALYZER_MCP": "off"}})
os.environ["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]])
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo, agent_mcp_shots
from aws_analyzer import AgentView, lambda_functions
from aws_analyzer.agent import McpServer
from aws_analyzer.agent.mcp_servers import editor_configs
lambda_core = lambda_functions.LambdaAnalyzer(region="us-east-1", **demo.seed_lambda_functions())
agent = AgentView(region="us-east-1", lambda_core=lambda_core, chat_model=agent_mcp_shots.scripted_model(),
                  findings=False, verify=False,
                  mcp=[McpServer("tickets", command="python", args=["ticket_desk.py"]),
                       McpServer("jira", command="jira-mcp", args=["--stdio"])])
agent.core.provider = "bedrock"
agent.core.mcp_editor_files = lambda: editor_configs(Path.cwd(), home=Path.cwd())
agent.chat(height=520)"""


def scripted_model():
    """
    A LangChain chat model that finds the orders-etl ticket, closes it (which asks first) and answers, waiting
    PAUSE_SECONDS before each reply.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    def reply(text: str, *tools: tuple[str, dict]) -> AIMessage:
        calls = [{"name": name, "args": args, "id": f"call-{i}-{name}", "type": "tool_call"}
                 for i, (name, args) in enumerate(tools)]
        usage = {"input_tokens": 2400, "output_tokens": 80, "total_tokens": 2480}
        return AIMessage(content=text, tool_calls=calls, usage_metadata=usage, response_metadata={"model_name": MODEL})

    class Scripted(BaseChatModel):
        replies: list
        model_name: str = MODEL

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            time.sleep(PAUSE_SECONDS)
            return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    return Scripted(replies=[
        reply("I'll look for a ticket about orders-etl first.", ("tickets__find_tickets", {"text": "orders-etl"})),
        reply("T-1 is the one. Closing it, as you asked.",
              ("tickets__close_ticket", {"ticket": "T-1", "note": "fixed the KeyError"})),
        reply("I closed **T-1** (*orders-etl fails at 02:00*) [1], with your note that the KeyError is fixed [2]."),
    ])


def _open_notebook(page, base: str, token: str, theme: str):
    """
    Opens the notebook in `theme`, runs it, and returns the window, WIDTH wide.
    """
    for kernel in api(base, token, "/api/kernels"):
        api(base, token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(base, token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{base}/lab/tree/agent.ipynb?token={token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".agc-app", timeout=120_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")
    time.sleep(1)
    app = page.locator(".agc-app").first
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - app.bounding_box()["width"]),
                            "height": viewport["height"]})
    time.sleep(1)
    return app


def shoot_theme(page, base: str, token: str, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:  # noqa: PLR0913
    """
    Open the notebook, run it, click through the panel and ask the question, screenshotting each wanted figure.
    """
    app = _open_notebook(page, base, token, theme)
    taken: dict[str, Path] = {}

    def shot(name: str) -> None:
        if name in wanted:
            page.mouse.move(1, 1)
            time.sleep(0.6)
            taken[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(taken[name]))

    mcp_button = page.locator(".agc-app .agc-head .agc-new", has_text="mcp")
    mcp_button.click()
    page.wait_for_function("(document.querySelector('.agc-app .agc-mcp .ag-mh') || {textContent: ''})"
                           ".textContent.includes('1 connected')", timeout=90_000)
    time.sleep(1)
    shot("agent-mcp")
    page.locator(".agc-app .agc-server").first.locator("button", has_text="tools").click()
    time.sleep(1)
    shot("agent-mcp-tools")
    page.locator(".agc-app .agc-server").first.locator("button", has_text="tools").click()
    page.locator(".agc-app .agc-mcp button", has_text="add a server").click()
    page.locator(".agc-app .agc-form input").first.fill(REMOTE)
    time.sleep(0.6)
    page.locator(".agc-app .agc-form textarea").fill(HEADER)
    time.sleep(0.6)
    shot("agent-mcp-add")
    page.locator(".agc-app .agc-form button", has_text="cancel").click()
    mcp_button.click()
    box = page.locator(".agc-app .agc-composer input")
    box.fill(QUESTION)
    box.press("Enter")
    page.wait_for_selector(".agc-app .agc-ask", timeout=60_000)
    time.sleep(1)
    shot("agent-mcp-ask")
    page.locator(".agc-app .agc-ask button", has_text="run").click()
    page.wait_for_selector(".agc-app .ag-ans", timeout=60_000)
    page.locator(".agc-app .ag-trail > summary").click()
    time.sleep(0.5)
    shot("agent-mcp-answer")
    return taken


def _write_notebook(folder: str) -> None:
    """
    The notebook, the ticket desk it runs and the .vscode/mcp.json it offers to import, in `folder`.
    """
    notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "agent", "metadata": {},
                           "outputs": [], "source": NOTEBOOK_CODE}],
                "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                "nbformat": 4, "nbformat_minor": 5}
    Path(folder, "agent.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    shutil.copy(ROOT / "tests" / "fake_mcp_server.py", Path(folder, "ticket_desk.py"))
    Path(folder, ".vscode").mkdir()
    Path(folder, ".vscode", "mcp.json").write_text(json.dumps(VSCODE_CONFIG), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n")[0])
    parser.add_argument("names", nargs="*", help=f"figures to make (default all: {', '.join(FIGURES)})")
    parser.add_argument("--out", type=Path, help="write PNGs to this folder instead of the docs' WebP images")
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    args = parser.parse_args()
    names = args.names or list(FIGURES)
    unknown = set(names) - set(FIGURES)
    if unknown:
        sys.exit(f"no figure {', '.join(sorted(unknown))}; the figures are {', '.join(FIGURES)}")
    try:
        import PIL  # noqa: F401
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        sys.exit(f"{exc.name} isn't installed: pip install jupyterlab playwright pillow")
    port, token = free_port(), secrets.token_hex(8)
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    heights: dict[str, set[int]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        _write_notebook(tmp)
        server = start_lab(tmp, port, token)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=chrome())
                for theme in themes:
                    page = browser.new_page(viewport={"width": 1400, "height": 1300}, device_scale_factor=SCALE)
                    try:
                        taken = shoot_theme(page, f"http://127.0.0.1:{port}", token, theme, names, Path(tmp))
                    except Exception:
                        if args.out:
                            args.out.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(args.out / f"stuck-{theme}.png"))
                        raise
                    page.close()
                    save(taken, theme, args.out, heights)
                browser.close()
            for name, sizes in heights.items():
                shots.set_height(shots.GUIDES["agent"], name, max(sizes))
                print(f"  {name}: {WIDTH} x {max(sizes)}", file=sys.stderr)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
