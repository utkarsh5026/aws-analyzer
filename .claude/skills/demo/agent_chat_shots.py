"""
Remake the agent's chat window screenshots: AgentView.chat(), used in a real JupyterLab, light and dark.

    agentenv/bin/python .claude/skills/demo/agent_chat_shots.py                     # every figure
    agentenv/bin/python .claude/skills/demo/agent_chat_shots.py agent-chat          # just these (no -light/-dark)
    agentenv/bin/python .claude/skills/demo/agent_chat_shots.py --out /tmp/shots    # PNGs there, docs left alone

The window is ipywidgets, which only draw in a browser connected to a kernel, so this starts JupyterLab on a notebook
that opens the window on demo.py's Lambda functions (moto, us-east-1), with a scripted model standing in for Claude
Haiku 4.5 on Amazon Bedrock: it writes a three-step plan (write_todos) and says what it means to do, calls
function_health and function_errors (which really run, on the demo data), ticking the plan off as it goes, and answers
from what they found, pausing before each reply the way a model does. It takes
the window empty (agent-chat-start: the greeting and the questions to start from), with its model settings open on
Amazon Bedrock (agent-chat-model: the provider list, the model box filled from demo.py's fake Bedrock model list, and
where questions go and what they cost), then asks the question the way a person would, and takes it while the agent works: its plan half done
(agent-chat-plan: the checklist under the question, two calls done, the third step under way), then (agent-chat-working: two tool calls done, Thinking…, Stop, and a follow-up typed meanwhile,
queued under it, which it then drops with its ✕ so the answer stays the last thing in the conversation), once
it has answered (agent-chat: the steps folded above the answer), and with the steps and a tool call open
(agent-chat-details: the first call's operations, window and data open). Each figure is the window, 984 CSS px wide at 1.5x like the other images, written to
docs/images/<name>-{light,dark}.webp, and the height= of both its images is set in docs/agent.md, by shots.py's
set_height.

Needs Python 3.11 or newer with the agent's packages (pip install "aws-analyzer[agent]"), jupyterlab, playwright and
Pillow, and Chromium: $CHROME, or the one Playwright installs (playwright install chromium).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402
from chat_shots import api, chrome, free_port  # noqa: E402

WIDTH, SCALE, IMAGES = shots.WIDTH, shots.SCALE, shots.IMAGES
FIGURES = ("agent-chat-start", "agent-chat-model", "agent-chat-plan", "agent-chat-working", "agent-chat",
           "agent-chat-details")
QUESTION = "Which Lambda failed in the last 24 hours, and why?"
FOLLOW_UP = "How do I fix the KeyError in orders-etl?"
MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
PAUSE_SECONDS = 1.5
SHOT_PAUSE_SECONDS = 8.0
PLAN = ("Check every function's failures in the last 24 hours", "Read the logs of the ones that failed",
        "Say what to fix first")
ANSWER = """\
**orders-etl** is the one that failed: 66 of its 2,226 calls in the last 24 hours (3.0%). Its logs show three causes:

1. **`KeyError: 'customer_id'`** (8 times): `etl.py` line 23 reads `row["customer_id"]`, and some input rows don't \
have it.
2. **Timeouts** (3 times): runs hit the 60 s limit, and its slowest run took 58.2 s, so it's close even when it works.
3. **`AccessDeniedException` on `dynamodb:PutItem`** (2 times): its role, `acme-lambda-role`, can't write to the \
`orders` table.

**report-api** also had 20 errors and 61 throttled calls, out of 9,878 (0.2%).

Start with the KeyError, the most frequent: `LambdaView().errors('orders-etl', since='24h')` shows each cause with a \
run to open."""

NOTEBOOK_CODE = f"""import os, sys
sys.path[:0] = [{str(ROOT / "src")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}})
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo, agent_chat_shots
from aws_analyzer import AgentView, lambda_functions
lambda_core = lambda_functions.LambdaAnalyzer(region="us-east-1", **demo.seed_lambda_functions())
agent = AgentView(region="us-east-1", lambda_core=lambda_core, chat_model=agent_chat_shots.scripted_model())
agent.core.provider = "bedrock"
agent.core._clients["bedrock"] = demo.seed_bedrock_kb()["clients"]["bedrock"]
agent.chat(height=600)"""


def scripted_model():
    """
    A LangChain chat model that plays a short investigation: it writes its plan (PLAN) and says what it will check,
    calls function_health, then function_errors for orders-etl, each beside the plan ticked off so far, marks the
    plan done, then answers with ANSWER. It waits PAUSE_SECONDS before each reply, and SHOT_PAUSE_SECONDS before
    the plan's last tick, so the window can be taken while it works, with the plan two steps in.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    def plan(done: int) -> tuple[str, dict]:
        statuses = ["completed"] * done + ["in_progress"] + ["pending"] * len(PLAN)
        return "write_todos", {"todos": [{"content": text, "status": status} for text, status in zip(PLAN, statuses)]}

    def reply(text: str, *tools: tuple[str, dict], tokens: tuple[int, int] = (2400, 90),
              pause: float = PAUSE_SECONDS) -> tuple[AIMessage, float]:
        calls = [{"name": name, "args": args, "id": f"call-{i}-{name}", "type": "tool_call"}
                 for i, (name, args) in enumerate(tools)]
        usage = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)}
        message = AIMessage(content=text, tool_calls=calls, usage_metadata=usage,
                            response_metadata={"model_name": MODEL})
        return message, pause

    class Scripted(BaseChatModel):
        replies: list
        model_name: str = MODEL

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            message, pause = self.replies.pop(0)
            time.sleep(pause)
            return ChatResult(generations=[ChatGeneration(message=message)])

    return Scripted(replies=[
        reply("I'll check every function's failures over the last 24 hours first.", plan(0),
              ("function_health", {"since": "24h"})),
        reply("**orders-etl** failed the most. Let me read its logs to see why.", plan(1),
              ("function_errors", {"function": "orders-etl", "since": "24h"}), tokens=(3900, 110)),
        reply("", plan(2), tokens=(5100, 60)),
        reply("", plan(3), tokens=(5300, 40), pause=SHOT_PAUSE_SECONDS),
        reply(ANSWER, tokens=(6200, 320)),
    ])


def shoot_theme(page, base: str, token: str, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:  # noqa: PLR0913
    """
    Open the notebook, run it, ask the question, and screenshot each wanted figure into `out`.
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
    page.wait_for_selector(".agc-app", timeout=90_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")
    time.sleep(1)
    app = page.locator(".agc-app").first
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - app.bounding_box()["width"]),
                            "height": viewport["height"]})
    time.sleep(1)
    taken: dict[str, Path] = {}

    def shot(name: str) -> None:
        if name in wanted:
            page.mouse.move(1, 1)
            time.sleep(0.6)
            taken[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(taken[name]))

    shot("agent-chat-start")
    if "agent-chat-model" in wanted:
        page.locator(".agc-app .agc-head .agc-new").first.click()
        page.locator(".agc-app .agc-set select").select_option(label="Amazon Bedrock")
        page.wait_for_function("!document.querySelector('.agc-app .ag-sn').textContent.includes('Listing')",
                               timeout=30_000)
        shot("agent-chat-model")
        page.locator(".agc-app .agc-acts .agc-new").click()
    box = page.locator(".agc-app .agc-composer input")
    box.fill(QUESTION)
    box.press("Enter")
    page.wait_for_function("document.querySelectorAll('.agc-app .ag-step:not(.ag-run)').length === 2 && "
                           "document.querySelector('.agc-app .ag-pulse') && "
                           "document.querySelectorAll('.agc-app .ag-plan .pln-done').length === 2", timeout=60_000)
    shot("agent-chat-plan")
    box.fill(FOLLOW_UP)
    box.press("Enter")
    page.wait_for_selector(".agc-app .agc-queued", timeout=10_000)
    shot("agent-chat-working")
    page.locator(".agc-app .agc-drop").click()
    page.wait_for_selector(".agc-app .agc-queued", state="detached", timeout=10_000)
    page.wait_for_selector(".agc-app .ag-ans", timeout=60_000)
    shot("agent-chat")
    page.locator(".agc-app .ag-trail > summary").click()
    page.locator(".agc-app .ag-trail .ag-step > details > summary").nth(0).click()
    page.locator(".agc-app .ag-trail .ag-data > summary").nth(0).click()
    time.sleep(0.4)
    page.evaluate("() => document.querySelector('.agc-app .ag-trail').scrollIntoView({block: 'start'})")
    shot("agent-chat-details")
    return taken


def save(taken: dict[str, Path], theme: str, out: Path | None, heights: dict[str, set[int]]) -> None:
    """
    The PNGs as the docs' WebP images (their heights kept for set_height), or copied to --out.
    """
    from PIL import Image

    for name, png in taken.items():
        if out:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy(png, out / png.name)
            continue
        image = Image.open(png).convert("RGB")
        image.save(IMAGES / f"{name}-{theme}.webp", "WEBP", quality=90, method=6)
        heights.setdefault(name, set()).add(round(image.height / SCALE))


def start_lab(folder: str, port: int, token: str) -> subprocess.Popen:
    """
    JupyterLab on `folder`, without its news and update checks, once it answers.
    """
    server = subprocess.Popen(
        [str(Path(sys.executable).parent / "jupyter"), "lab", "--no-browser", "--allow-root", f"--port={port}",
         f"--IdentityProvider.token={token}", "--ServerApp.ip=127.0.0.1", f"--ServerApp.root_dir={folder}",
         "--LabApp.news_url=", "--LabApp.check_for_updates_class=jupyterlab.NeverCheckForUpdate"],
        cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "JUPYTER_CONFIG_DIR": folder})
    for _ in range(60):
        try:
            api(f"http://127.0.0.1:{port}", token, "/api")
            return server
        except OSError:
            time.sleep(1)
    return server


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
    if not shutil.which("jupyter", path=str(Path(sys.executable).parent)):
        sys.exit("jupyterlab isn't installed: pip install jupyterlab")
    port, token = free_port(), secrets.token_hex(8)
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    heights: dict[str, set[int]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "agent", "metadata": {},
                               "outputs": [], "source": NOTEBOOK_CODE}],
                    "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                    "nbformat": 4, "nbformat_minor": 5}
        Path(tmp, "agent.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        server = start_lab(tmp, port, token)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=chrome())
                for theme in themes:
                    page = browser.new_page(viewport={"width": 1400, "height": 1200}, device_scale_factor=SCALE)
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
