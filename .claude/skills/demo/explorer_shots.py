"""Remake the S3 explorer's screenshots: S3Explorer is a live widget, so this runs it in a real JupyterLab.

    .venv/bin/python .claude/skills/demo/explorer_shots.py              # every explorer figure and the tour
    .venv/bin/python .claude/skills/demo/explorer_shots.py explorer     # just this one
    .venv/bin/python .claude/skills/demo/explorer_shots.py --list

shots.py renders reports as static HTML, which a widget isn't. This starts JupyterLab in a temporary folder, opens
a notebook that seeds shots.py's acme scene in moto and moves an S3Explorer to the figure's place, clicking its
buttons the way you would when the figure needs that. It screenshots the explorer 984 CSS px wide at 1.5x in
headless Chromium (light, then dark theme), writes docs/images/<name>-{light,dark}.webp and sets the figure's
height= in docs/s3_explorer.md, like shots.py.

The tour (explorer-tour, at the top of the guide and in README.md) is an animated WebP instead: a drawn pointer
moves to each button and clicks it, with a frame for every step of the way and a pause on each result.

Chromium draws the widgets' buttons and labels in the system UI font, which differs from machine to machine, so
the browser gets a fontconfig that maps it to Liberation Sans, the font the reports' Helvetica already gets on
Linux. The figures then come out the same wherever they're made.

Needs what the dev requirements have plus JupyterLab and Playwright:
    pip install jupyterlab playwright        # Playwright finds Chromium in $PLAYWRIGHT_BROWSERS_PATH
When that Chromium is another version than the Playwright installed expects, point $CHROME at it
(CHROME=/opt/pw-browsers/chromium-*/chrome-linux/chrome in a cloud session).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
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
class Step:
    click: str  # the button: "row:<name>" (a file, folder or bucket), "crumb:<name>", "act:<label>", "nav:<arrow>",
    # "chip:<label>" (a file type's chip, or Include subfolders), "check:<name>" (a row's checkbox) or "primary:<label>"
    ready: str  # text the right-hand pane shows once the click has done its work
    hold: float = 0.8  # in a tour, seconds the result stays on screen before the pointer moves on
    where: str = ""  # a selector to find `ready` in instead, for a click that only redraws the list (".s3x-status")


@dataclass
class Figure:
    name: str  # docs/images/<name>-light.webp
    code: str  # run in the notebook after the scene is seeded; `x` is the explorer the figure shows
    ready: str  # text the right-hand pane shows once the explorer has opened
    steps: tuple[Step, ...] = ()  # clicks made before the screenshot (in a tour, the clicks it shows)
    tour: bool = False  # an animated WebP of the clicks, starting with `hold` seconds on the opening view
    hold: float = 1.5


START = 'x = S3Explorer(core=core, height=500)'
FIGURES = [
    Figure("explorer", 'x = S3Explorer("s3://acme-ml-data/curated/features/churn/train.parquet", core=core, height=500)',
           "First 20 rows"),
    Figure("explorer-docx", 'x = S3Explorer("s3://acme-ml-data/docs/model-card-churn-xgb.docx", core=core, height=500)',
           "Training data"),
    Figure("explorer-pdf-text", 'x = S3Explorer("s3://acme-ml-data/docs/data-retention-policy.pdf", core=core, height=500)',
           "Text of page 1", (Step("act:Text", "Left out what repeats"),)),
    Figure("explorer-buckets", START, "Your buckets", (Step("act:Every bucket", "Buckets by size"),)),
    Figure("explorer-search", 'x = S3Explorer("s3://acme-ml-data/training/", core=core, height=500)\n'
           'x.filter(".tar", subfolders=True)', "Files below"),
    Figure("explorer-zip", 'x = S3Explorer("s3://acme-ml-data/raw/events/dt=2025-10-10/", core=core, height=500)',
           "Click a folder", (
               *(Step(f"check:part-{n:04d}.json.gz", f"{i} selected", where=".s3x-picks")
                 for i, n in enumerate((1, 2, 4, 5, 7), 1)),
               Step("primary:Download selected", "as one .zip"),
           )),
    Figure("explorer-tour", START, "Your buckets", tour=True, steps=(
        Step("row:acme-ml-data", "Click a folder to open it", 1.2),
        Step("row:curated", "Click a folder to open it", 0.5),
        Step("row:features", "Click a folder to open it", 0.5),
        Step("row:churn", "Click a folder to open it", 0.6),
        Step("row:train.parquet", "First 20 rows", 3.0),
        Step("crumb:acme-ml-data", "Click a folder to open it", 0.6),
        Step("row:docs", "Click a folder to open it", 0.6),
        Step("row:model-card-churn-xgb.docx", "Training data", 2.6),
        Step("crumb:acme-ml-data", "Click a folder to open it", 0.6),
        Step("row:training", "Click a folder to open it", 0.6),
        Step("chip:Include subfolders", "Files below", 1.0),
        Step("chip:.tar 13", "13 match", 3.4, where=".s3x-status"),
    )),
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
FONTS = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <alias binding="strong"><family>system-ui</family><prefer><family>Liberation Sans</family></prefer></alias>
  <alias binding="strong"><family>sans-serif</family><prefer><family>Liberation Sans</family></prefer></alias>
  <alias binding="strong"><family>sans</family><prefer><family>Liberation Sans</family></prefer></alias>
</fontconfig>
"""
BUTTONS = {"row": "button.s3x-row", "crumb": "button.s3x-crumb", "act": "button.s3x-act", "nav": "button.s3x-nav",
           "chip": "button.s3x-chip", "primary": "button.s3x-primary"}
# The tour's pointer and the ring a click leaves; the browser draws no pointer in screenshots.
POINTER = """() => {
  const pointer = document.createElement('div');
  pointer.id = 'tour-pointer';
  pointer.style.cssText = 'position:fixed;left:-2px;top:-2px;z-index:100001;pointer-events:none;'
    + 'filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.35))';
  pointer.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="26" viewBox="0 0 20 26">'
    + '<path d="M2 2v18.6l4.7-4.3 3.3 7.4 3.3-1.5-3.2-7.2h6.6z" fill="#111" stroke="#fff" stroke-width="1.6"'
    + ' stroke-linejoin="round"/></svg>';
  const ring = document.createElement('div');
  ring.id = 'tour-ring';
  ring.style.cssText = 'position:fixed;left:-18px;top:-18px;width:36px;height:36px;border-radius:50%;'
    + 'z-index:100000;pointer-events:none;opacity:0;background:rgba(59,130,246,.25);'
    + 'box-shadow:0 0 0 2px rgba(59,130,246,.6)';
  document.body.append(ring, pointer);
}"""
MOVE = """([x, y, ring]) => {
  document.getElementById('tour-pointer').style.transform = `translate(${x}px, ${y}px)`;
  const r = document.getElementById('tour-ring');
  r.style.transform = `translate(${x}px, ${y}px) scale(${ring ? ring : 0.4})`;
  r.style.opacity = ring ? String(1.6 - ring) : '0';
}"""


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


def open_explorer(page, base: str, figure: Figure, theme: str, settings: Path) -> None:
    """Run the figure's notebook in the given JupyterLab theme and wait until the explorer has opened."""
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
    wait_ready(page, figure.ready)
    page.add_style_tag(content=f".s3x{{width:{shots.WIDTH}px !important}} .jp-toast-container{{display:none}}")
    page.mouse.move(0, 0)
    time.sleep(1.5)


def wait_ready(page, text: str) -> None:
    """Wait for a right-hand pane, drawn since the last click, that shows `text` (each report gets a new pane)."""
    page.wait_for_function("text => [...document.querySelectorAll('.s3x-right:not([data-old])')]"
                           ".some(pane => pane.textContent.includes(text))", arg=text, timeout=60_000)


def button(page, click: str):
    kind, _, label = click.partition(":")
    if kind == "check":  # the checkbox in the row of that name
        name = page.locator("button.s3x-row").filter(has_text=re.compile(rf"(^|\s){re.escape(label)}\s*$"))
        found = page.locator(".s3x .s3x-r").filter(has=name).locator("button.s3x-check")
        if found.count() != 1:
            raise SystemExit(f"{click!r}: {found.count()} checkboxes match, not one")
        return found
    found = page.locator(f".s3x {BUTTONS[kind]}").filter(has_text=re.compile(rf"(^|\s){re.escape(label)}\s*$"))
    if found.count() != 1:
        raise SystemExit(f"{click!r}: {found.count()} buttons match, not one")
    return found


def click(page, step: Step, at: tuple[float, float] | None = None) -> None:
    """Click the step's button (at a point, for the tour) and wait until its report is on the right."""
    time.sleep(0.5)  # the explorer drops clicks that come right after the list changed (_CLICK_GRACE)
    page.evaluate("() => document.querySelectorAll('.s3x-right').forEach(pane => pane.dataset.old = '1')")
    if at:
        page.mouse.click(*at)
    else:
        button(page, step.click).click()
    if step.where:
        page.wait_for_function("([where, text]) => [...document.querySelectorAll(where)]"
                               ".some(el => el.textContent.includes(text))", arg=[step.where, step.ready], timeout=60_000)
    else:
        wait_ready(page, step.ready)
    time.sleep(0.6)  # pictures and fonts in the new report


def still(page, figure: Figure, png: Path) -> None:
    for step in figure.steps:
        click(page, step)
    page.mouse.move(0, 0)
    time.sleep(0.5)
    page.locator(".s3x").first.screenshot(path=str(png))


def tour(page, figure: Figure) -> list:
    """The frames of the tour, as (image, milliseconds) pairs."""
    from PIL import Image

    box = page.locator(".s3x").first.bounding_box()
    clip = {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}
    frames = []

    def frame(ms: float, x: float, y: float, ring: float = 0) -> None:
        page.evaluate(MOVE, [x, y, ring])
        frames.append((Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB"), round(ms)))

    page.evaluate(POINTER)
    x, y = box["x"] + box["width"] * 0.74, box["y"] + box["height"] * 0.66
    frame(figure.hold * 1000, x, y)
    for step in figure.steps:
        # Past the end of the button's text (which turns bold, so wider, once clicked), so the pointer doesn't hide
        # the name it clicked.
        text = button(page, step.click).evaluate("b => { const r = document.createRange(); r.selectNodeContents(b); "
                                                 "const box = r.getBoundingClientRect(), all = b.getBoundingClientRect(); "
                                                 "return [Math.min(box.right + 24, all.right - 6), all.y, all.height]; }")
        to_x, to_y = text[0], text[1] + text[2] * 0.38
        moves = max(8, min(16, round(((to_x - x) ** 2 + (to_y - y) ** 2) ** 0.5 / 30)))
        for i in range(1, moves + 1):
            t = i / moves
            ease = t * t * (3 - 2 * t)
            at = (x + (to_x - x) * ease, y + (to_y - y) * ease)
            page.mouse.move(*at)  # so the button under the pointer shows its hover colour
            frame(33, *at)
        x, y = to_x, to_y
        frame(90, x, y, 0.8)
        click(page, step, (x, y))
        frame(90, x, y, 1.2)
        frame(step.hold * 1000, x, y)
    return frames


def main() -> int:
    intro, _, rest = (__doc__ or "").partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="figures to make; default: all")
    parser.add_argument("--list", action="store_true", help="list the figures and exit")
    args = parser.parse_args()
    if args.list:
        for figure in FIGURES:
            clicks = " → ".join(step.click for step in figure.steps)
            print(f"{figure.name:17} {figure.code}" + (f"\n{'':17} then {clicks}" if clicks else ""))
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
        (work / "fonts.conf").write_text(FONTS)
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
                browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None,
                                            env={**os.environ, "FONTCONFIG_FILE": str(work / "fonts.conf")})
                page = browser.new_page(viewport={"width": 1500, "height": 1400}, device_scale_factor=shots.SCALE)
                for figure in chosen:
                    heights = set()
                    for theme in ("light", "dark"):
                        for kernel in json.load(urllib.request.urlopen(f"{base}/api/kernels?token=shots")):
                            urllib.request.urlopen(urllib.request.Request(
                                f"{base}/api/kernels/{kernel['id']}?token=shots", method="DELETE"))
                        open_explorer(page, base, figure, theme, settings)
                        out = shots.IMAGES / f"{figure.name}-{theme}.webp"
                        if figure.tour:
                            frames = tour(page, figure)
                            first, *others = [image for image, _ in frames]
                            first.save(out, "WEBP", save_all=True, append_images=others, loop=0, lossless=True,
                                       quality=100, method=6, duration=[ms for _, ms in frames], minimize_size=True)
                            image = first
                            print(f"  {figure.name}-{theme}: {len(frames)} frames, "
                                  f"{sum(ms for _, ms in frames) / 1000:.1f} s, {out.stat().st_size / 1e6:.1f} MB",
                                  file=sys.stderr)
                        else:
                            png = work / f"{figure.name}-{theme}.png"
                            still(page, figure, png)
                            image = Image.open(png).convert("RGB")
                            image.save(out, "WEBP", quality=90, method=6)
                        heights.add(round(image.height / shots.SCALE))
                    shots.set_height("s3_explorer", figure.name, max(heights))
                    print(f"  {figure.name}: {shots.WIDTH} x {max(heights)}", file=sys.stderr)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
