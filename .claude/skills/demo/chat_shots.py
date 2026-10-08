"""Remake the chat guide's screenshots: bedrock_chat.py's window, used in a real JupyterLab, light and dark.

    .venv/bin/python .claude/skills/demo/chat_shots.py                 # every figure
    .venv/bin/python .claude/skills/demo/chat_shots.py chat-request    # just these (image names, no -light/-dark)

shots.py renders reports, which are plain HTML. The chat window is ipywidgets, which only draw in a browser
connected to a kernel, so this starts JupyterLab on a notebook that opens the window on demo.py's fake Bedrock
(support-docs), then uses the window the way a person would: types a question and presses Enter, opens the knowledge
base list, adds settings, searches for a setting, opens the Request JSON tab and its Python view, edits the JSON, ticks
files, and asks the last question again on Retrieve only. Each figure is the window, 984 CSS px wide at 1.5x like the other
images, written to docs/images/<name>-{light,dark}.webp, and the height= of both its images is set in
docs/bedrock_chat.md, by shots.py's set_height.

Needs jupyterlab, playwright and Pillow (pip install jupyterlab playwright pillow), and Chromium: $CHROME, or the
one Playwright installs (playwright install chromium).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402  (the image size, where images go, and set_height)

WIDTH, SCALE, IMAGES = shots.WIDTH, shots.SCALE, shots.IMAGES
FIGURES = ("chat-window", "chat-pick", "chat-settings", "chat-add", "chat-request", "chat-python", "chat-edit",
           "chat-files", "chat-retrieve")

NOTEBOOK_CODE = f"""import os, sys
sys.path[:0] = [{str(ROOT / "analyzers")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}})
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo, bedrock_chat
core = bedrock_chat.BedrockChatAnalyzer(region="us-east-1", **demo.seed_bedrock_kb())
ui = bedrock_chat.BedrockChatView(core, kb="support-docs", model="sonnet", settings={{"n": 5, "search_type": "hybrid"}})
ui"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chrome() -> str | None:
    found = os.environ.get("CHROME") or next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))),
                                             None)
    return str(found) if found else None


def api(base: str, token: str, path: str, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(f"{base}{path}?token={token}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read() or b"null")


def shoot_theme(page, base: str, token: str, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:
    """Open the notebook, run it, use the window, and screenshot each wanted figure into `out`."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for kernel in api(base, token, "/api/kernels"):  # a fresh kernel, so the newest bedrock_chat.py is imported
        api(base, token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(base, token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{base}/lab/tree/chat.ipynb?token={token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")  # news prompts
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".kbc-app", timeout=60_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")  # fold the file browser away
    time.sleep(1)
    app = page.locator(".kbc-app").first
    width = app.bounding_box()["width"]
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - width), "height": viewport["height"]})
    time.sleep(1)
    shots: dict[str, Path] = {}

    def shot(name: str) -> None:
        if name in wanted:
            time.sleep(0.6)
            shots[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(shots[name]))

    def answered(count: int) -> None:
        page.wait_for_function(
            f"document.querySelectorAll('.kbc-app .msg.bot').length >= {count} && "
            "!document.querySelector('.kbc-app .wait') && !document.querySelector('.kbc-app .caret')", timeout=30_000)

    def scroll_to(element: str) -> None:
        """Scrolls the open tab so the element (a JS expression) is at its top: each tab scrolls on its own, in the
        box inside the tab's frame."""
        page.evaluate(f"""() => {{ const el = {element};
            const box = el.closest('.widget-tab-contents > .widget-box, .jupyter-widget-tab-contents > .jupyter-widget-box');
            box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 4; }}""")

    box = page.locator(".kbc-app input[placeholder='Ask a question, then press Enter']")
    box.fill("Can digital goods be refunded?")
    box.press("Enter")
    answered(1)
    box.fill("Summarize how long refunds take, as a list")  # demo.py answers this one in markdown
    box.press("Enter")
    answered(2)
    page.locator(".kbc-app details.src summary").last.click()
    shot("chat-window")

    def field(label: str):
        """The header field (a _Picker's button) with this label."""
        return page.locator(".kbc-app .kbc-field", has=page.locator(f".fxl:text-is('{label}')")).locator(
            "button.kbc-trig-b").first

    if "chat-pick" in wanted:
        field("Knowledge base").click()
        page.wait_for_selector(".kbc-app .kbc-pop .kbc-opt", timeout=10_000)
        shot("chat-pick")
        field("Knowledge base").click()  # closes it again
    page.locator(".kbc-app button.kbc-add").click()  # opens Add a setting
    for label in ("+ Temperature", "+ Metadata filter", "+ Reranker"):
        page.locator(f".kbc-app button:has-text('{label}')").first.click()
        time.sleep(0.4)
    area = page.locator(".kbc-app textarea[placeholder*='team']").first
    area.fill('{"team": "billing"}')
    area.evaluate("el => el.blur()")  # sends the value, as leaving the box does
    try:
        page.wait_for_function("document.querySelector('.kbc-app').innerText.includes('Only documents where')",
                               timeout=10_000)
    except PlaywrightTimeout:
        pass
    page.locator(".kbc-app .kbc-card .kbc-x").first.click()  # folds it away again
    scroll_to("document.querySelector('.kbc-app .kbc-row')")
    shot("chat-settings")
    page.locator(".kbc-app button.kbc-add").click()
    search = page.locator(".kbc-app input[placeholder^='Search:']")
    search.fill("rerank")
    page.wait_for_selector(".kbc-app .kbc-pick", timeout=10_000)
    scroll_to("document.querySelector('.kbc-app .kbc-card')")
    shot("chat-add")
    search.fill("")
    tab = ".kbc-app .lm-TabBar-tab:has-text('{0}'), .kbc-app .p-TabBar-tab:has-text('{0}')"
    page.locator(tab.format("Request JSON")).first.click()
    shot("chat-request")
    page.locator(".kbc-app .widget-toggle-button:has-text('Python')").first.click()
    shot("chat-python")
    page.locator(".kbc-app .widget-toggle-button:has-text('Tree')").first.click()
    page.locator(".kbc-app button:has-text('Edit JSON')").click()
    editor = page.locator(".kbc-app .kbc-mono textarea").last
    for _ in range(50):  # the request arrives from the kernel a moment after the click
        if '"input"' in editor.input_value():
            break
        time.sleep(0.2)
    editor.fill(editor.input_value().replace('"temperature": 0.2', '"temperature": 0.2,\n            "topK": 50'))
    editor.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    page.locator(".kbc-app button:has-text('Apply')").click()
    page.wait_for_selector(".kbc-app .note.warn", timeout=10_000)
    scroll_to("[...document.querySelectorAll('.kbc-app .note.warn')].find(el => el.offsetParent)")
    shot("chat-edit")
    if "chat-files" in wanted or "chat-retrieve" in wanted:
        page.locator(".kbc-app button:has-text('Cancel')").click()
        page.locator(tab.format("Settings")).first.click()
        for name in ("Temperature", "Reranker", "Metadata filter"):  # back to the settings the window opened with
            page.locator(".kbc-app .kbc-row", has=page.locator(f".rh b:text-is('{name}')")).locator(
                "button.kbc-x").first.click()
            time.sleep(0.4)
        field("Files").click()
        files = page.locator(".kbc-app input[placeholder='Search by file name or folder']")
        files.wait_for(state="visible", timeout=10_000)
        for label in ("policies/refund-policy.pdf", "policies/eu-returns.pdf"):
            files.fill(label)
            files.press("Enter")  # ticks the only file it finds
            page.wait_for_function(f"document.querySelector('.kbc-app').innerText.includes('{label.split('/')[-1]} ✕')",
                                   timeout=10_000)
        field("Files").click()  # closes the list
        page.mouse.move(1, 1)  # off the chips, which turn red under the pointer
        box.fill("How long do refunds take?")
        box.press("Enter")
        answered(3)
        shot("chat-files")
    if "chat-retrieve" in wanted:
        if page.locator(".kbc-app button.kbc-add").is_visible():  # Add a setting may still be open from before
            page.locator(".kbc-app button.kbc-add").click()
        page.locator(".kbc-app button:has-text('+ Temperature')").first.click()  # for the answer: shown as not sent
        time.sleep(0.4)
        page.locator(".kbc-app .kbc-card .kbc-x").first.click()
        page.locator(".kbc-app button:has-text('All files')").click()
        page.locator(".kbc-app .widget-toggle-button:has-text('Retrieve only')").first.click()
        search = page.locator(".kbc-app input[placeholder='Type a question to search for']")
        for _ in range(50):  # the last question comes back into the box from the kernel
            if search.input_value():
                break
            time.sleep(0.2)
        search.press("Enter")
        answered(4)
        shot("chat-retrieve")
    return shots


def main() -> int:
    names = sys.argv[1:] or list(FIGURES)
    unknown = set(names) - set(FIGURES)
    if unknown:
        sys.exit(f"no figure {', '.join(sorted(unknown))}; the figures are {', '.join(FIGURES)}")
    try:
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        sys.exit(f"{exc.name} isn't installed: pip install jupyterlab playwright pillow")
    if not shutil.which("jupyter", path=str(Path(sys.executable).parent)):
        sys.exit("jupyterlab isn't installed: pip install jupyterlab")
    port, token = free_port(), secrets.token_hex(8)
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "chat", "metadata": {},
                               "outputs": [], "source": NOTEBOOK_CODE}],
                    "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                    "nbformat": 4, "nbformat_minor": 5}
        Path(tmp, "chat.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        server = subprocess.Popen(
            [str(Path(sys.executable).parent / "jupyter"), "lab", "--no-browser", "--allow-root", f"--port={port}",
             f"--IdentityProvider.token={token}", "--ServerApp.ip=127.0.0.1", f"--ServerApp.root_dir={tmp}",
             "--LabApp.news_url=", "--LabApp.check_for_updates_class=jupyterlab.NeverCheckForUpdate"],
            cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "JUPYTER_CONFIG_DIR": tmp})
        try:
            for _ in range(60):
                try:
                    api(base, token, "/api")
                    break
                except OSError:
                    time.sleep(1)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=chrome())
                heights: dict[str, set[int]] = {}
                for theme in ("light", "dark"):
                    page = browser.new_page(viewport={"width": 1400, "height": 1200}, device_scale_factor=SCALE)
                    taken = shoot_theme(page, base, token, theme, names, Path(tmp))
                    page.close()
                    for name, png in taken.items():
                        image = Image.open(png).convert("RGB")
                        image.save(IMAGES / f"{name}-{theme}.webp", "WEBP", quality=90, method=6)
                        heights.setdefault(name, set()).add(round(image.height / SCALE))
                browser.close()
            for name, sizes in heights.items():
                shots.set_height("bedrock_chat", name, max(sizes))
                print(f"  {name}: {WIDTH} x {max(sizes)}", file=sys.stderr)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
