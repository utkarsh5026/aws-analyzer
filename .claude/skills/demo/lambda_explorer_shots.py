"""Remake the Lambda explorer's screenshots: lambda_functions.py's explore() window, used in a real JupyterLab, light
and dark.

    .venv/bin/python .claude/skills/demo/lambda_explorer_shots.py                       # every figure
    .venv/bin/python .claude/skills/demo/lambda_explorer_shots.py lambda-explorer-logs  # just these (no -light/-dark)
    .venv/bin/python .claude/skills/demo/lambda_explorer_shots.py --out /tmp/shots      # PNGs there, docs left alone

shots.py renders reports, which are plain HTML. The explorer is ipywidgets, which only draw in a browser connected to
a kernel, so this starts JupyterLab on a notebook that opens the window on demo.py's Lambda functions (moto, us-east-1),
then uses it the way a person would: reads the Functions tab, opens orders-etl's Overview (scrolled to how it's wired),
its Logs over the last 24 hours with a failed run open, searches them for AccessDenied, opens the Errors and Code tabs,
then picks report-api in the function field for its JSON logs, a failed run and a good one open. Each figure is the
window, 984 CSS px wide at 1.5x like the other images, written to docs/images/<name>-{light,dark}.webp, and the height=
of both its images is set in docs/lambda_functions.md, by shots.py's set_height.

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
FIGURES = ("lambda-explorer", "lambda-explorer-overview", "lambda-explorer-logs", "lambda-explorer-search",
           "lambda-explorer-json", "lambda-explorer-errors", "lambda-explorer-code")

NOTEBOOK_CODE = f"""import os, sys
sys.path[:0] = [{str(ROOT / "analyzers")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}})
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo, lambda_functions
core = lambda_functions.LambdaAnalyzer(region="us-east-1", **demo.seed_lambda_functions())
# height=620: the figures don't grow with the browser's height
x = lambda_functions.LambdaExplorer(core=core, height=620)"""


def shoot_theme(page, base: str, token: str, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:
    """Open the notebook, run it, use the window, and screenshot each wanted figure into `out`."""
    for kernel in api(base, token, "/api/kernels"):  # a fresh kernel, so the newest lambda_functions.py is imported
        api(base, token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(base, token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{base}/lab/tree/explorer.ipynb?token={token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")  # news prompts
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".lmx-app", timeout=90_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")  # fold the file browser away
    time.sleep(1)
    app = page.locator(".lmx-app").first
    width = app.bounding_box()["width"]
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - width), "height": viewport["height"]})
    page.wait_for_function("document.querySelector('.lmx-app .lmx-status').innerText.includes('read in')",
                           timeout=90_000)
    page.mouse.move(1, 1)
    time.sleep(1)
    taken: dict[str, Path] = {}

    def shot(name: str) -> None:
        if name in wanted:
            time.sleep(0.6)
            taken[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(taken[name]))

    def tab(title: str) -> None:
        page.locator(f".lmx-app button.lmx-tab:has-text('{title}')").first.click()
        time.sleep(0.4)

    def settled() -> None:
        """Waits until nothing in the window is loading."""
        page.wait_for_function("![...document.querySelectorAll('.lmx-app .skw')].some(el => el.offsetParent) && "
                               "!document.querySelector('.lmx-app .lmx-status .spin')", timeout=60_000)

    settled()
    shot("lambda-explorer")
    page.locator(".lmx-app .lmx-row", has_text="orders-etl").locator("button.lmx-row-b").first.click()
    settled()
    page.wait_for_selector(".lmx-app .lmx-scroll .wire", state="visible", timeout=30_000)
    time.sleep(0.5)
    page.evaluate("""() => {  // past the findings (function_info's figure has them): how it's wired, its 30 days
        const wire = [...document.querySelectorAll('.lmx-app .lmx-scroll .wire')].find(el => el.offsetParent);
        const pane = wire.closest('.lmx-scroll');
        pane.scrollTop += wire.getBoundingClientRect().top - pane.getBoundingClientRect().top - 40;
    }""")
    page.mouse.move(1, 1)
    shot("lambda-explorer-overview")
    tab("Logs")
    settled()
    page.locator(".lmx-app .lmx-logpage .lmx-range select").select_option(label="Last 24 hours")
    time.sleep(0.5)
    settled()
    page.locator(".lmx-app .lmx-run:has(.rr.bad)").first.locator("button.lmx-run-b").click()
    time.sleep(0.4)
    page.mouse.move(1, 1)
    shot("lambda-explorer-logs")
    search = page.locator(".lmx-app input[placeholder^='Find runs with']")
    search.fill("AccessDenied")
    search.press("Enter")
    time.sleep(0.5)
    settled()
    page.mouse.move(1, 1)
    shot("lambda-explorer-search")
    tab("Errors")
    settled()
    page.mouse.move(1, 1)
    shot("lambda-explorer-errors")
    tab("Code")
    settled()
    page.mouse.move(1, 1)
    shot("lambda-explorer-code")
    if "lambda-explorer-json" in wanted:  # report-api, which logs JSON
        page.locator(".lmx-app .lmx-trig-b").first.click()
        page.locator(".lmx-app .lmx-pop input").fill("report")
        page.locator(".lmx-app .lmx-pop input").press("Enter")
        tab("Logs")
        settled()
        page.locator(".lmx-app .lmx-logpage .lmx-range select").select_option(label="Last 3 hours")
        time.sleep(0.5)
        settled()
        page.locator(".lmx-app .lmx-run:has(.rr.bad)").first.locator("button.lmx-run-b").click()
        page.locator(".lmx-app .lmx-run:has(.rr.ok)").first.locator("button.lmx-run-b").click()
        time.sleep(0.4)
        page.mouse.move(1, 1)
        shot("lambda-explorer-json")
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
                shots.set_height("lambda_functions", name, max(sizes))
                print(f"  {name}: {WIDTH} x {max(sizes)}", file=sys.stderr)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
