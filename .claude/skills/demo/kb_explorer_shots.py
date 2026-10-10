"""Remake the knowledge base explorer's screenshots: bedrock_kb.py's explore() window, used in a real JupyterLab,
light and dark.

    .venv/bin/python .claude/skills/demo/kb_explorer_shots.py                  # every figure
    .venv/bin/python .claude/skills/demo/kb_explorer_shots.py kb-explorer-file # just these (image names, no -light/-dark)
    .venv/bin/python .claude/skills/demo/kb_explorer_shots.py --out /tmp/shots # PNGs there, docs left alone (to look)

shots.py renders reports, which are plain HTML. The explorer is ipywidgets, which only draw in a browser connected to
a kernel, so this starts JupyterLab on a notebook that opens the window on demo.py's fake Bedrock (support-docs), then
uses it the way a person would: reads the Overview, opens the Files tab and clicks warranty.pdf, opens one of its
chunks, asks it a question, filters the list to the failed files and opens one, searches the knowledge base, and opens
the Syncs tab. Each figure is the window, 984 CSS px wide at 1.5x like the other images, written to
docs/images/<name>-{light,dark}.webp, and the height= of both its images is set in docs/bedrock_kb.md, by shots.py's
set_height.

Needs jupyterlab, playwright and Pillow (pip install jupyterlab playwright pillow), and Chromium: $CHROME, or the
one Playwright installs (playwright install chromium).
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

import shots  # noqa: E402  (the image size, where images go, and set_height)
from chat_shots import api, chrome, free_port  # noqa: E402  (the same JupyterLab plumbing)

WIDTH, SCALE, IMAGES = shots.WIDTH, shots.SCALE, shots.IMAGES
FIGURES = ("kb-explorer", "kb-explorer-file", "kb-explorer-chunks", "kb-explorer-ask", "kb-explorer-failed",
           "kb-explorer-search", "kb-explorer-syncs")

NOTEBOOK_CODE = f"""import os, sys
sys.path[:0] = [{str(ROOT / "src")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}})
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo
from aws_analyzer import bedrock_kb
core = bedrock_kb.BedrockKBAnalyzer(region="us-east-1", **demo.seed_bedrock_kb())
# height=620: the figures don't grow with the browser's height
x = bedrock_kb.KBExplorer("support-docs", core=core, height=620)"""


def shoot_theme(page, base: str, token: str, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:
    """Open the notebook, run it, use the window, and screenshot each wanted figure into `out`."""
    for kernel in api(base, token, "/api/kernels"):  # a fresh kernel, so the newest bedrock_kb.py is imported
        api(base, token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(base, token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{base}/lab/tree/explorer.ipynb?token={token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")  # news prompts
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".kbx-app", timeout=60_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")  # fold the file browser away
    time.sleep(1)
    app = page.locator(".kbx-app").first
    width = app.bounding_box()["width"]
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - width), "height": viewport["height"]})
    page.wait_for_function("document.querySelector('.kbx-app .kbx-status').innerText.includes('listed')",
                           timeout=60_000)
    page.mouse.move(1, 1)
    time.sleep(1)
    taken: dict[str, Path] = {}

    def shot(name: str) -> None:
        if name in wanted:
            time.sleep(0.6)
            taken[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(taken[name]))

    def tab(title: str) -> None:
        page.locator(f".kbx-app button.kbx-tab:has-text('{title}')").first.click()
        time.sleep(0.4)

    def settled() -> None:
        """Waits until nothing in the window is loading."""
        page.wait_for_function("![...document.querySelectorAll('.kbx-app .skw')].some(el => el.offsetParent) && "
                               "!document.querySelector('.kbx-app .kbx-status .spin')", timeout=30_000)

    settled()
    shot("kb-explorer")
    tab("Files")
    find = page.locator(".kbx-app input[placeholder^='Search files']")
    find.fill("warranty")
    page.locator(".kbx-app .kbx-row", has_text="warranty.pdf").locator("button.kbx-row-b").first.click()
    settled()
    page.mouse.move(1, 1)
    shot("kb-explorer-file")
    page.locator(".kbx-app details.ck summary").nth(1).click()  # the second chunk, with what it shares marked
    page.evaluate("""() => {  // its chunks' heading at the top of the pane
        const pane = document.querySelector('.kbx-app .kbx-right');
        const bar = pane.querySelector('.ckbar');
        const heading = bar.previousElementSibling || bar;
        pane.scrollTop += heading.getBoundingClientRect().top - pane.getBoundingClientRect().top - 64;
    }""")
    page.mouse.move(1, 1)
    shot("kb-explorer-chunks")
    ask = page.locator(".kbx-app input[placeholder^='Ask a question to see whether this file']")
    ask.fill("How long is the warranty?")
    ask.press("Enter")
    settled()
    shot("kb-explorer-ask")
    find.fill("")
    page.locator(".kbx-app button.kbx-chip:has-text('Failed')").first.click()
    page.locator(".kbx-app .kbx-row", has_text="scanned-invoice.pdf").locator("button.kbx-row-b").first.click()
    settled()
    page.mouse.move(1, 1)
    shot("kb-explorer-failed")
    tab("Search")
    box = page.locator(".kbx-app input[placeholder^='Ask the knowledge base']")
    box.fill("What does error E1234 mean?")
    box.press("Enter")
    settled()
    page.mouse.move(1, 1)
    shot("kb-explorer-search")
    tab("Syncs")
    settled()
    shot("kb-explorer-syncs")
    return taken


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("names", nargs="*", help=f"figures to make (default all: {', '.join(FIGURES)})")
    parser.add_argument("--out", type=Path, help="write PNGs to this folder instead of the docs' WebP images")
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    args = parser.parse_args()
    names = args.names or list(FIGURES)
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
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    with tempfile.TemporaryDirectory() as tmp:
        notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "explorer", "metadata": {},
                               "outputs": [], "source": NOTEBOOK_CODE}],
                    "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                    "nbformat": 4, "nbformat_minor": 5}
        Path(tmp, "explorer.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
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
                for theme in themes:
                    page = browser.new_page(viewport={"width": 1400, "height": 1200}, device_scale_factor=SCALE)
                    try:
                        taken = shoot_theme(page, base, token, theme, names, Path(tmp))
                    except Exception:
                        if args.out:  # what the page looked like when it got stuck
                            args.out.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(args.out / f"stuck-{theme}.png"))
                        raise
                    page.close()
                    for name, png in taken.items():
                        if args.out:
                            args.out.mkdir(parents=True, exist_ok=True)
                            shutil.copy(png, args.out / png.name)
                            continue
                        image = Image.open(png).convert("RGB")
                        image.save(IMAGES / f"{name}-{theme}.webp", "WEBP", quality=90, method=6)
                        heights.setdefault(name, set()).add(round(image.height / SCALE))
                browser.close()
            for name, sizes in heights.items():
                shots.set_height("bedrock_kb", name, max(sizes))
                print(f"  {name}: {WIDTH} x {max(sizes)}", file=sys.stderr)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
