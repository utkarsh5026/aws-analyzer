"""Remake the S3 explorer's screenshots: S3Explorer is a live widget, so this runs it in a real JupyterLab.

    .venv/bin/python .claude/skills/demo/explorer_shots.py              # every explorer figure
    .venv/bin/python .claude/skills/demo/explorer_shots.py explorer     # just this one
    .venv/bin/python .claude/skills/demo/explorer_shots.py --list

shots.py renders reports as static HTML, which a widget isn't. This starts JupyterLab in a temporary folder, opens
a notebook that seeds shots.py's acme scene in moto and moves an S3Explorer to the figure's place, screenshots the
explorer 984 CSS px wide at 1.5x in headless Chromium (light, then dark theme), writes
docs/images/<name>-{light,dark}.webp and sets the <img height=> in docs/s3.html, like shots.py.

Needs what the dev requirements have plus JupyterLab and Playwright:
    pip install jupyterlab playwright        # Playwright finds Chromium in $PLAYWRIGHT_BROWSERS_PATH
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402  (the scene, the image size and set_height)


@dataclass
class Figure:
    name: str  # docs/images/<name>-light.webp
    code: str  # run in the notebook after the scene is seeded; `x` is the explorer the figure shows
    ready: str  # text the right-hand pane shows once the figure is ready


FIGURES = [
    Figure("explorer", 'x = S3Explorer("s3://acme-ml-data/curated/features/churn/train.parquet", core=core, height=500)',
           "First 20 rows"),
    Figure("explorer-docx", 'x = S3Explorer("s3://acme-ml-data/docs/model-card-churn-xgb.docx", core=core, height=500)',
           "Training data"),
]
SEED = f"""import os, sys
sys.path[:0] = [{str(ROOT / "analyzers")!r}, {str(HERE)!r}]
os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_DEFAULT_REGION="us-east-1")
from moto import mock_aws
mock_aws().start()
import shots
from s3 import S3Analyzer
from s3_explorer import S3Explorer
core = S3Analyzer(**shots.seed_s3_docs())"""


def notebook(code: str) -> dict:
    cells = [SEED, code]
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "language": "python",
                                                                           "display_name": "Python 3"}},
            "cells": [{"cell_type": "code", "id": f"c{i}", "metadata": {}, "source": source, "outputs": [],
                       "execution_count": None} for i, source in enumerate(cells)]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shoot(page, base: str, figure: Figure, theme: str, settings: Path, png: Path) -> None:
    (settings / "@jupyterlab" / "apputils-extension").mkdir(parents=True, exist_ok=True)
    (settings / "@jupyterlab" / "apputils-extension" / "themes.jupyterlab-settings").write_text(
        json.dumps({"theme": f"JupyterLab {theme.title()}"}))
    page.goto(f"{base}/lab/tree/{figure.name}.ipynb?token=shots&reset", wait_until="networkidle")
    page.wait_for_selector(".jp-Notebook .jp-Cell", timeout=60_000)
    time.sleep(1)
    page.locator(".jp-Cell").first.click()
    page.keyboard.press("Shift+Enter")
    page.wait_for_function("() => /\\[\\d+\\]/.test(document.querySelector('.jp-InputPrompt').textContent)",
                           timeout=180_000)
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(".s3x .s3x-row", timeout=60_000)
    page.wait_for_function(f"() => document.querySelector('.s3x-right').textContent.includes({figure.ready!r})",
                           timeout=60_000)
    page.add_style_tag(content=f".s3x{{width:{shots.WIDTH}px !important}} .jp-toast-container{{display:none}}")
    page.mouse.move(0, 0)
    time.sleep(1.5)
    page.locator(".s3x").first.screenshot(path=str(png))


def main() -> int:
    intro, _, rest = (__doc__ or "").partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="figures to make; default: all")
    parser.add_argument("--list", action="store_true", help="list the figures and exit")
    args = parser.parse_args()
    if args.list:
        for figure in FIGURES:
            print(f"{figure.name:16} {figure.code}")
        return 0
    chosen = [f for f in FIGURES if not args.names or f.name in args.names]
    if len(chosen) < len(set(args.names)):
        parser.error(f"no such figure (--list shows them): {sorted(set(args.names) - {f.name for f in FIGURES})}")
    from PIL import Image
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        work, settings = Path(tmp), Path(tmp) / "settings"
        for figure in chosen:
            (work / f"{figure.name}.ipynb").write_text(json.dumps(notebook(figure.code)))
        port = free_port()
        env = {**os.environ, "JUPYTERLAB_SETTINGS_DIR": str(settings), "JUPYTER_RUNTIME_DIR": str(work / "runtime")}
        server = subprocess.Popen(
            [sys.executable, "-m", "jupyter", "lab", "--no-browser", "--allow-root", f"--port={port}",
             "--IdentityProvider.token=shots", f"--ServerApp.root_dir={work}"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(120):
                try:
                    urllib.request.urlopen(f"{base}/api/status?token=shots", timeout=1)
                    break
                except OSError:
                    time.sleep(0.5)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1500, "height": 1400}, device_scale_factor=shots.SCALE)
                for figure in chosen:
                    heights = set()
                    for theme in ("light", "dark"):
                        for kernel in json.load(urllib.request.urlopen(f"{base}/api/kernels?token=shots")):
                            urllib.request.urlopen(urllib.request.Request(
                                f"{base}/api/kernels/{kernel['id']}?token=shots", method="DELETE"))
                        png = work / f"{figure.name}-{theme}.png"
                        shoot(page, base, figure, theme, settings, png)
                        image = Image.open(png).convert("RGB")
                        image.save(shots.IMAGES / f"{figure.name}-{theme}.webp", "WEBP", quality=90, method=6)
                        heights.add(round(image.height / shots.SCALE))
                    shots.set_height("s3", figure.name, max(heights))
                    print(f"  {figure.name}: {shots.WIDTH} x {max(heights)}", file=sys.stderr)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
