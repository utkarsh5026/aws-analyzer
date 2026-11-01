"""
Make the windows' animated tours: a short video of each explorer and the chat window being used, as an animated WebP.

    .venv/bin/python .claude/skills/demo/tour_shots.py                        # every tour, light and dark
    .venv/bin/python .claude/skills/demo/tour_shots.py lambda-explorer-tour   # just these
    .venv/bin/python .claude/skills/demo/tour_shots.py --out /tmp/tours       # WebPs there, docs left alone
    .venv/bin/python .claude/skills/demo/tour_shots.py --list

Each window is ipywidgets, which only draw in a browser connected to a kernel, so this starts JupyterLab on the
notebook its screenshot script opens (the same demo data), then plays a scripted visit: a drawn pointer glides to each
button, clicks it with a ring, types into search boxes a letter at a time, and stays on each result long enough to read
it. A frame is taken at every step of the way, and while the window is loading, so the tour shows what a user waits
for. The frames become docs/images/<name>-{light,dark}.webp (lossless, looping), and the height= of both images is set
in the window's guide, by shots.py's set_height. The S3 explorer's tour (explorer-tour) is made by explorer_shots.py.

Needs jupyterlab, playwright and Pillow (pip install jupyterlab playwright pillow), and Chromium: $CHROME, or the one
Playwright installs (playwright install chromium). In a cloud session, point $FONTCONFIG_FILE at a fontconfig without
56-prefer-inter.conf, as for the other figures.
"""

from __future__ import annotations

import argparse
import io
import json
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import apigw_explorer_shots  # noqa: E402
import chat_shots  # noqa: E402
import kb_explorer_shots  # noqa: E402
import lambda_explorer_shots  # noqa: E402
import sfn_explorer_shots  # noqa: E402
import shots  # noqa: E402
from chat_shots import api, chrome, free_port  # noqa: E402
from sfn_explorer_shots import start_lab  # noqa: E402

GLIDE_FRAME_MS = 33
RING_FRAME_MS = 90
LOADING_FRAME_MS = 160
MAX_LOADING_FRAMES = 12
KEY_FRAME_MS = 55
LOADING_TIMEOUT_MS = 90_000
POINTER_HTML = """() => {
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
MOVE_POINTER = """([x, y, ring]) => {
  document.getElementById('tour-pointer').style.transform = `translate(${x}px, ${y}px)`;
  const r = document.getElementById('tour-ring');
  r.style.transform = `translate(${x}px, ${y}px) scale(${ring ? ring : 0.4})`;
  r.style.opacity = ring ? String(1.6 - ring) : '0';
}"""


class Tour:
    """
    Records a visit to one window: each method moves the drawn pointer, acts, and adds frames of the window.
    """

    def __init__(self, page: Any, app: Any, busy_js: str) -> None:
        """
        `busy_js` is a JavaScript expression that's true while the window is loading something.
        """
        self.page = page
        self.app = app
        self.busy_js = busy_js
        box = app.bounding_box()
        self.clip = {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}
        self.x = box["x"] + box["width"] * 0.72
        self.y = box["y"] + box["height"] * 0.62
        self.frames: list[tuple[Any, int]] = []
        page.evaluate(POINTER_HTML)

    def frame(self, ms: float, ring: float = 0) -> None:
        """
        Adds the window as it is now, shown for `ms` milliseconds.
        """
        from PIL import Image

        self.page.evaluate(MOVE_POINTER, [self.x, self.y, ring])
        picture = Image.open(io.BytesIO(self.page.screenshot(clip=self.clip))).convert("RGB")
        self.frames.append((picture, round(ms)))

    def hold(self, seconds: float) -> None:
        """
        Stays on what the window shows, once it has finished loading.
        """
        self.wait()
        self.frame(seconds * 1000)

    def wait(self) -> None:
        """
        Adds a frame now and then while the window loads, and returns once it has finished.
        """
        time.sleep(0.25)
        for _ in range(MAX_LOADING_FRAMES):
            if not self.page.evaluate(f"() => {self.busy_js}"):
                break
            self.frame(LOADING_FRAME_MS)
            time.sleep(0.1)
        self.page.wait_for_function(f"() => !({self.busy_js})", timeout=LOADING_TIMEOUT_MS)
        time.sleep(0.5)

    def glide(self, target: Any, *, at: float = 0.5) -> tuple[float, float]:
        """
        Moves the pointer to the target, `at` of the way across it (at most 220 px in), and returns where it stopped
        relative to the target.
        """
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        inside = (min(box["width"] * at, 220), box["height"] * 0.5)
        to_x, to_y = box["x"] + inside[0], box["y"] + inside[1]
        distance = ((to_x - self.x) ** 2 + (to_y - self.y) ** 2) ** 0.5
        moves = max(8, min(18, round(distance / 30)))
        start_x, start_y = self.x, self.y
        for i in range(1, moves + 1):
            ease = (i / moves) ** 2 * (3 - 2 * i / moves)
            self.x = start_x + (to_x - start_x) * ease
            self.y = start_y + (to_y - start_y) * ease
            self.page.mouse.move(self.x, self.y)
            self.frame(GLIDE_FRAME_MS)
        return inside

    def click(self, target: Any, *, hold: float = 1.2, at: float = 0.5) -> None:
        """
        Glides to the target and clicks it, then stays on the result.
        """
        inside = self.glide(target, at=at)
        self.frame(RING_FRAME_MS, 0.8)
        target.click(position={"x": inside[0], "y": inside[1]})
        self.frame(RING_FRAME_MS, 1.2)
        self.hold(hold)

    def type(self, box: Any, text: str, *, enter: bool = True, hold: float = 1.5) -> None:
        """
        Clicks into a text box and types `text` a letter at a time, then presses Enter when asked.
        """
        inside = self.glide(box, at=0.3)
        box.click(position={"x": inside[0], "y": inside[1]})
        self.frame(RING_FRAME_MS, 1.0)
        for letter in text:
            self.page.keyboard.type(letter)
            self.frame(KEY_FRAME_MS)
        if enter:
            time.sleep(0.2)
            box.press("Enter")
        self.hold(hold)

    def paste(self, box: Any, text: str, *, hold: float = 1.0) -> None:
        """
        Clicks into a text box and fills it at once, as a paste does: for text too long to type.
        """
        inside = self.glide(box, at=0.3)
        box.click(position={"x": inside[0], "y": inside[1]})
        self.frame(RING_FRAME_MS, 1.0)
        box.fill(text)
        self.hold(hold)

    def choose(self, select: Any, label: str, *, hold: float = 1.2) -> None:
        """
        Glides to a drop-down and picks the option with this label.
        """
        self.glide(select, at=0.4)
        self.frame(RING_FRAME_MS, 0.8)
        select.select_option(label=label)
        self.frame(RING_FRAME_MS, 1.2)
        self.hold(hold)

    def save(self, path: Path) -> Any:
        """
        Writes the frames as a looping, lossless animated WebP and returns its first frame.
        """
        first, *others = [picture for picture, _ in self.frames]
        durations = [ms for _, ms in self.frames]
        first.save(path, "WEBP", save_all=True, append_images=others, loop=0, lossless=True, quality=100, method=6,
                   duration=durations, minimize_size=True)
        seconds = sum(durations) / 1000
        print(f"  {path.name}: {len(self.frames)} frames, {seconds:.1f} s, {path.stat().st_size / 1e6:.1f} MB",
              file=sys.stderr)
        return first


def busy_in(root: str, status: str) -> str:
    """
    The JavaScript test for an explorer that's loading: a skeleton shown, or a spinner on its status line.
    """
    return (f"[...document.querySelectorAll('{root} .skw')].some(el => el.offsetParent) || "
            f"!!document.querySelector('{root} {status} .spin')")


def row(page: Any, prefix: str, text: str, within: str = "") -> Any:
    """
    The full-width button of the explorer's row that shows `text`.
    """
    rows = page.locator(f".{prefix}-app {within} .{prefix}-row".replace("  ", " "), has_text=text)
    return rows.locator(f"button.{prefix}-row-b").first


def tab(page: Any, prefix: str, title: str) -> Any:
    """
    The explorer's tab button with this title.
    """
    return page.locator(f".{prefix}-app button.{prefix}-tab:has-text('{title}')").first


def play_lambda(page: Any, tour: Tour) -> None:
    """
    The Functions list, orders-etl's Overview, its failed runs in the Logs, a search for AccessDenied, its Errors and
    its Code.
    """
    tour.hold(2.0)
    tour.click(row(page, "lmx", "orders-etl"), hold=2.4, at=0.12)
    tour.click(tab(page, "lmx", "Logs"), hold=0.8)
    tour.choose(page.locator(".lmx-app .lmx-logpage .lmx-range select"), "Last 24 hours", hold=1.0)
    failed_run = page.locator(".lmx-app .lmx-run:has(.rr.bad)").first.locator("button.lmx-run-b")
    tour.click(failed_run, hold=2.6, at=0.3)
    tour.type(page.locator(".lmx-app input[placeholder^='Find runs']"), "AccessDenied", hold=2.6)
    tour.click(tab(page, "lmx", "Errors"), hold=2.8)
    tour.click(tab(page, "lmx", "Code"), hold=3.0)


def play_kb(page: Any, tour: Tour) -> None:
    """
    support-docs' Overview, warranty.pdf in the Files tab and a question about it, a search of the whole knowledge
    base, then the Syncs.
    """
    tour.hold(2.2)
    tour.click(tab(page, "kbx", "Files"), hold=0.8)
    tour.type(page.locator(".kbx-app input[placeholder^='Search files']"), "warranty", enter=False, hold=0.6)
    tour.click(row(page, "kbx", "warranty.pdf"), hold=2.4, at=0.2)
    ask = page.locator(".kbx-app input[placeholder^='Ask a question to see whether this file']")
    tour.type(ask, "How long is the warranty?", hold=2.8)
    tour.click(tab(page, "kbx", "Search"), hold=0.6)
    tour.type(page.locator(".kbx-app input[placeholder^='Ask the knowledge base']"), "What does error E1234 mean?",
              hold=2.8)
    tour.click(tab(page, "kbx", "Syncs"), hold=3.0)


def play_stepfunctions(page: Any, tour: Tour) -> None:
    """
    The State machines list, order-pipeline's executions, the failed execution ord-7f3a's waterfall and log, then the
    Graph with LoadOrders picked.
    """
    tour.hold(2.2)
    tour.click(tab(page, "sfx", "Executions"), hold=1.2)
    tour.click(row(page, "sfx", "ord-7f3a"), hold=3.0, at=0.15)
    tour.click(tab(page, "sfx", "Graph"), hold=1.2)
    tour.click(row(page, "sfx", "LoadOrders", within=".sfx-states"), hold=3.2, at=0.2)


def play_apigateway(page: Any, tour: Tour) -> None:
    """
    The APIs list, the Routes tab, then the Calls tab narrowed to 5xx and the newest 504: where its time went and the
    Lambda run behind it.
    """
    tour.hold(2.2)
    tour.click(tab(page, "agx", "Routes"), hold=2.4)
    tour.click(tab(page, "agx", "Calls"), hold=1.4)
    tour.click(page.locator(".agx-app button.agx-chip", has_text="5xx").first, hold=1.0)
    tour.click(row(page, "agx", "504", within=".agx-calls"), hold=1.0, at=0.2)
    page.wait_for_selector(".agx-app .agx-right .ts", timeout=LOADING_TIMEOUT_MS)
    tour.hold(3.4)


def play_chat(page: Any, tour: Tour) -> None:
    """
    Two questions answered with their sources, the cited passages opened, six test questions run in the Test tab, then
    the Code tab's script.
    """
    tour.hold(1.6)
    box = page.locator(".kbc-app input[placeholder='Ask a question, then press Enter']")
    tour.type(box, "Can digital goods be refunded?", hold=2.6)
    tour.type(box, "Summarize how long refunds take, as a list", hold=2.6)
    tour.click(page.locator(".kbc-app details.src summary").last, hold=2.4, at=0.15)
    tests_tab = ".kbc-app .lm-TabBar-tab:has-text('{0}'), .kbc-app .p-TabBar-tab:has-text('{0}')"
    tour.click(page.locator(tests_tab.format("Test")).first, hold=0.6)
    tour.paste(page.locator(".kbc-app textarea[placeholder^='How long do refunds take?']"), chat_shots.TEST_QUESTIONS)
    tour.click(page.locator(".kbc-app button:has-text('Run 6 questions')"), hold=0.4)
    page.wait_for_function("document.querySelector('.kbc-app .tests') && !document.querySelector('.kbc-app .bq.wait')",
                           timeout=LOADING_TIMEOUT_MS)
    tour.hold(3.2)
    tour.click(page.locator(tests_tab.format("Code")).first, hold=3.0)


def chat_busy() -> str:
    """
    The JavaScript test for the chat window waiting on an answer.
    """
    return ("!!document.querySelector('.kbc-app .kbc-log .wait') || !!document.querySelector('.kbc-app .caret') || "
            "!!document.querySelector('.kbc-app .bq.wait')")


@dataclass
class Window:
    """
    A window with a tour: the notebook that opens it, how to find it, when it's ready, and the visit.
    """

    name: str
    guide: str
    code: str
    root: str
    busy_js: str
    play: Callable[[Any, Tour], None]


TOURS = [
    Window("lambda-explorer-tour", "lambda_functions", lambda_explorer_shots.NOTEBOOK_CODE, ".lmx-app",
           busy_in(".lmx-app", ".lmx-status"), play_lambda),
    Window("kb-explorer-tour", "bedrock_kb", kb_explorer_shots.NOTEBOOK_CODE, ".kbx-app",
           busy_in(".kbx-app", ".kbx-status"), play_kb),
    Window("sfn-explorer-tour", "stepfunctions", sfn_explorer_shots.NOTEBOOK_CODE, ".sfx-app",
           busy_in(".sfx-app", ".sfx-status"), play_stepfunctions),
    Window("apigw-explorer-tour", "apigateway", apigw_explorer_shots.NOTEBOOK_CODE, ".agx-app",
           busy_in(".agx-app", ".agx-status"), play_apigateway),
    Window("chat-tour", "bedrock_chat", chat_shots.NOTEBOOK_CODE, ".kbc-app", chat_busy(), play_chat),
]


@dataclass
class Lab:
    """
    The JupyterLab the tours are recorded in: its address, token and folder.
    """

    base: str
    token: str
    folder: Path


def write_notebook(folder: Path, window: Window) -> None:
    notebook = {"cells": [{"cell_type": "code", "execution_count": None, "id": "tour", "metadata": {}, "outputs": [],
                           "source": window.code}],
                "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                "nbformat": 4, "nbformat_minor": 5}
    (folder / f"{window.name}.ipynb").write_text(json.dumps(notebook), encoding="utf-8")


def open_window(page: Any, lab: Lab, window: Window, theme: str) -> Any:
    """
    Runs the window's notebook in a fresh kernel and theme, makes the window 984 CSS px wide, waits until it has
    loaded, and returns it.
    """
    for kernel in api(lab.base, lab.token, "/api/kernels"):
        api(lab.base, lab.token, f"/api/kernels/{kernel['id']}", "DELETE")
    api(lab.base, lab.token, "/lab/api/settings/@jupyterlab/apputils-extension:themes", "PUT",
        {"raw": json.dumps({"theme": "JupyterLab Dark" if theme == "dark" else "JupyterLab Light"})})
    page.goto(f"{lab.base}/lab/tree/{window.name}.ipynb?token={lab.token}&reset")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    time.sleep(3)
    page.add_style_tag(content=".Toastify, .jp-toast-container { display: none !important; }")
    page.click(".jp-Cell .jp-InputArea-editor")
    page.keyboard.press("Shift+Enter")
    page.wait_for_selector(window.root, timeout=LOADING_TIMEOUT_MS)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+b")
    time.sleep(1)
    app = page.locator(window.root).first
    viewport = page.viewport_size
    page.set_viewport_size({"width": round(viewport["width"] + shots.WIDTH - app.bounding_box()["width"]),
                            "height": viewport["height"]})
    time.sleep(1)
    app.scroll_into_view_if_needed()
    page.wait_for_function(f"() => !({window.busy_js})", timeout=LOADING_TIMEOUT_MS)
    page.mouse.move(1, 1)
    time.sleep(1.5)
    return app


def record(page: Any, lab: Lab, window: Window, theme: str, out: Path) -> int:
    """
    Records the window's tour in one theme into `out`, and returns its height in CSS px.
    """
    app = open_window(page, lab, window, theme)
    tour = Tour(page, app, window.busy_js)
    window.play(page, tour)
    first = tour.save(out / f"{window.name}-{theme}.webp")
    return round(first.height / shots.SCALE)


def record_all(lab: Lab, chosen: list[Window], themes: tuple[str, ...], out: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome())
        for window in chosen:
            heights = set()
            for theme in themes:
                page = browser.new_page(viewport={"width": 1400, "height": 1300}, device_scale_factor=shots.SCALE)
                heights.add(record(page, lab, window, theme, out or shots.IMAGES))
                page.close()
            if not out:
                shots.set_height(shots.GUIDES[window.guide], window.name, max(heights))
        browser.close()


def main() -> int:
    intro, _, rest = (__doc__ or "").strip().partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="tours to make; default: all")
    parser.add_argument("--list", action="store_true", help="list the tours and exit")
    parser.add_argument("--out", type=Path, help="write the WebPs to this folder instead of the docs' images")
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    args = parser.parse_args()
    if args.list:
        for window in TOURS:
            print(f"{window.name:22} {(window.play.__doc__ or '').split(chr(10))[1].strip()}")
        return 0
    unknown = set(args.names) - {window.name for window in TOURS}
    if unknown:
        parser.error(f"no tour {', '.join(sorted(unknown))} (--list shows them)")
    if not shutil.which("jupyter", path=str(Path(sys.executable).parent)):
        sys.exit("jupyterlab isn't installed: pip install jupyterlab playwright pillow")
    chosen = [window for window in TOURS if not args.names or window.name in args.names]
    themes = ("light", "dark") if args.theme == "both" else (args.theme,)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    port, token = free_port(), secrets.token_hex(8)
    with tempfile.TemporaryDirectory() as folder:
        for window in chosen:
            write_notebook(Path(folder), window)
        server = start_lab(folder, port, token)
        try:
            record_all(Lab(f"http://127.0.0.1:{port}", token, Path(folder)), chosen, themes, args.out)
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
