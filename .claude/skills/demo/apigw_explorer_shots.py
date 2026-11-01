"""
Remake the API Gateway explorer's screenshots: apigateway's explore() window, used in a real JupyterLab, light and
dark.

    .venv/bin/python .claude/skills/demo/apigw_explorer_shots.py                       # every figure
    .venv/bin/python .claude/skills/demo/apigw_explorer_shots.py apigw-explorer-call   # just these (no -light/-dark)
    .venv/bin/python .claude/skills/demo/apigw_explorer_shots.py --out /tmp/shots      # PNGs there, docs left alone

The explorer is ipywidgets, which only draw in a browser connected to a kernel, so this starts JupyterLab on a
notebook that opens the window on demo.py's APIs (acme's six, their access logs and the orders-etl runs behind the
failed calls, in moto), then uses it the way a person would: reads the APIs tab, opens the Routes tab (POST /orders,
the route that runs out of time, is picked first), then the Calls tab, picks the 5xx chip and clicks the newest 504
(where its time went, then the Lambda run behind it). Each figure is the window, 984 CSS px wide at 1.5x like the
other images, written to docs/images/<name>-{light,dark}.webp, and the height= of both its images is set in
docs/apigateway.md, by shots.py's set_height.

Needs jupyterlab, playwright and Pillow (pip install jupyterlab playwright pillow), and Chromium: $CHROME, or the one
Playwright installs (playwright install chromium).
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402
from chat_shots import api, chrome, free_port  # noqa: E402

WIDTH, SCALE, IMAGES = shots.WIDTH, shots.SCALE, shots.IMAGES
FIGURES = ("apigw-explorer", "apigw-explorer-routes", "apigw-explorer-call")

NOTEBOOK_CODE = f"""import os, sys
sys.path[:0] = [{str(ROOT / "src")!r}, {str(HERE)!r}]
os.environ.update({{"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1", "AWS_ANALYZER_HISTORY": "off"}})
os.environ.pop("AWS_PROFILE", None)
from moto import mock_aws
mock_aws().start()
import demo
from aws_analyzer import apigateway
core = apigateway.APIGatewayAnalyzer(region="us-east-1", **demo.seed_apigateway())
x = apigateway.APIGatewayExplorer(core=core, height=640)"""


@dataclass
class Lab:
    """
    The JupyterLab the figures are taken in: its address and token.
    """

    base: str
    token: str


def open_window(page: Any, lab: Lab, theme: str) -> Any:
    """
    Opens the notebook in a fresh kernel and theme, runs it, and returns the window, 984 CSS px wide.
    """
    for kernel in api(lab.base, lab.token, "/api/kernels"):
        api(lab.base, lab.token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(lab.base, lab.token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{lab.base}/lab/tree/explorer.ipynb?token={lab.token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".agx-app", timeout=90_000)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")
    time.sleep(1)
    app = page.locator(".agx-app").first
    width = app.bounding_box()["width"]
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + WIDTH - width), "height": viewport["height"]})
    return app


def shoot_theme(page: Any, lab: Lab, theme: str, wanted: list[str], out: Path) -> dict[str, Path]:
    """
    Uses the window the way a person would, and screenshots each wanted figure into `out`.
    """
    app = open_window(page, lab, theme)
    taken: dict[str, Path] = {}

    def settled() -> None:
        page.wait_for_function("![...document.querySelectorAll('.agx-app .skw')].some(el => el.offsetParent) && "
                               "!document.querySelector('.agx-app .agx-status .spin')", timeout=60_000)

    def shot(name: str) -> None:
        if name in wanted:
            page.mouse.move(1, 1)
            time.sleep(0.6)
            taken[name] = out / f"{name}-{theme}.png"
            app.screenshot(path=str(taken[name]))

    def tab(title: str) -> None:
        page.locator(f".agx-app button.agx-tab:has-text('{title}')").first.click()
        time.sleep(0.4)

    settled()
    page.evaluate("""() => {
        const pages = [...document.querySelectorAll('.agx-app .agx-page')].filter(el => el.offsetParent);
        const list = pages[0].querySelector('.agx-filters');
        pages[0].scrollTop += list.getBoundingClientRect().top - pages[0].getBoundingClientRect().top - 150;
    }""")
    shot("apigw-explorer")
    tab("Routes")
    settled()
    shot("apigw-explorer-routes")
    tab("Calls")
    settled()
    page.locator(".agx-app button.agx-chip", has_text="5xx").first.click()
    time.sleep(0.4)
    page.locator(".agx-app .agx-calls .agx-row", has_text="504").locator("button.agx-row-b").first.click()
    page.wait_for_selector(".agx-app .agx-right .ts", timeout=60_000)
    settled()
    shot("apigw-explorer-call")
    return taken


def notebook_file(folder: str) -> None:
    notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "explorer", "metadata": {},
                           "outputs": [], "source": NOTEBOOK_CODE}],
                "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                "nbformat": 4, "nbformat_minor": 5}
    Path(folder, "explorer.ipynb").write_text(json.dumps(notebook), encoding="utf-8")


def start_lab(folder: str, port: int, token: str) -> subprocess.Popen:
    """
    Starts JupyterLab on the folder, and waits until it answers.
    """
    server = subprocess.Popen(
        [str(Path(sys.executable).parent / "jupyter"), "lab", "--no-browser", "--allow-root", f"--port={port}",
         f"--IdentityProvider.token={token}", "--ServerApp.ip=127.0.0.1", f"--ServerApp.root_dir={folder}",
         "--LabApp.news_url=", "--LabApp.check_for_updates_class=jupyterlab.NeverCheckForUpdate"],
        cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "JUPYTER_CONFIG_DIR": folder})
    for _ in range(60):
        try:
            api(f"http://127.0.0.1:{port}", token, "/api")
            break
        except OSError:
            time.sleep(1)
    return server


def keep(taken: dict[str, Path], out: Path | None, heights: dict[str, set[int]]) -> None:
    """
    Copies the PNGs to `out`, or saves them as the docs' WebP images and notes their heights.
    """
    from PIL import Image

    for name, png in taken.items():
        if out:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy(png, out / png.name)
            continue
        image = Image.open(png).convert("RGB")
        image.save(IMAGES / f"{png.stem}.webp", "WEBP", quality=90, method=6)
        heights.setdefault(name, set()).add(round(image.height / SCALE))


def shoot(lab: Lab, themes: tuple[str, ...], names: list[str], folder: str, out: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    heights: dict[str, set[int]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome())
        for theme in themes:
            page = browser.new_page(viewport={"width": 1400, "height": 1200}, device_scale_factor=SCALE)
            try:
                taken = shoot_theme(page, lab, theme, names, Path(folder))
            except Exception:
                if out:
                    out.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out / f"stuck-{theme}.png"))
                raise
            page.close()
            keep(taken, out, heights)
        browser.close()
    for name, sizes in heights.items():
        shots.set_height(shots.GUIDES["apigateway"], name, max(sizes))
        print(f"  {name}: {WIDTH} x {max(sizes)}", file=sys.stderr)


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
        import playwright  # noqa: F401
    except ImportError as exc:
        sys.exit(f"{exc.name} isn't installed: pip install jupyterlab playwright pillow")
    if not shutil.which("jupyter", path=str(Path(sys.executable).parent)):
        sys.exit("jupyterlab isn't installed: pip install jupyterlab")
    port, token = free_port(), secrets.token_hex(8)
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    with tempfile.TemporaryDirectory() as folder:
        notebook_file(folder)
        server = start_lab(folder, port, token)
        try:
            shoot(Lab(f"http://127.0.0.1:{port}", token), themes, names, folder, args.out)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
