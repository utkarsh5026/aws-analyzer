"""
What the notebook windows share: the S3 explorer, the chat, and the knowledge base and Lambda explorers.

The windows are ipywidgets laid out and styled by CSS, with no JavaScript. ipywidgets and IPython load only inside the
functions that need them (the window itself loads ipywidgets with `_require`), so this module imports with boto3 alone.

    helpers          _css_height, _running_loop, _cell_number, _class_if, _skeleton, _icon_url, _row_button,
                     _for_window
    _window_errors   a window's own commands say their errors in the window
    _Window          the chat's and the explorers' base: callbacks' errors said in the window, Enter, quiet updates
    _Explorer        the knowledge base and Lambda explorers' base: their status line, tabs and frame, AWS read off
                     the event loop (_later), the window shown once per cell
    _Picker          a field that opens a list to search and pick from
    _terminal_theme  the chat windows' look: a monospace font and a code editor's colours, light and dark, and the
                     marks beside a question (_HUMAN) and an answer (_MACHINE)
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from .blocks import _Findings, _Next, _Note, _Table, _Title
from .deps import _in_notebook, _require
from .errors import _Hint
from .render import _prose
from .text import _esc, search_rank
from .themes import _DARK_PAGES, _UNSET_PAGE


def _css_height(height: int | str | None) -> str | None:
    """
    height= as CSS: a number is pixels (720, '720'), other text is CSS as written ('80vh'); None is None.
    """
    text = "" if height is None else str(height).strip()
    return f"{text}px" if re.fullmatch(r"\d+(\.\d+)?", text) else text or None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """
    The kernel's event loop, which runs the window's clicks in a notebook; None elsewhere (a script, the tests),
    where the window's work runs right away while the click waits.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _cell_number() -> Any:
    """
    The running cell's execution count in IPython (None elsewhere): a cell that ends with a window (chat(),
    explore()) shows it once, not twice.
    """
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return None
    shell = get_ipython()
    return getattr(shell, "execution_count", None) if shell is not None else None


_HUMAN, _MACHINE = "\U0001F9D1", "\U0001F916"
_MONO_FONT = ('"JetBrains Mono","Cascadia Code","Fira Code",ui-monospace,SFMono-Regular,Menlo,Consolas,'
              '"Liberation Mono",monospace')
_TERMINAL_LIGHT = {
    "mono": _MONO_FONT, "bg": "var(--jp-cell-editor-background,var(--vscode-editor-background,#f6f8fa))",
    "line": "rgba(127,127,127,.22)", "dim": "rgba(127,127,127,.95)", "hover": "rgba(127,127,127,.1)",
    "accent": "#0969da", "prompt": "#8250df", "ok": "#1a7f37", "bad": "#cf222e", "warn": "#9a6700",
    "comment": "#6e7781", "k": "#cf222e", "f": "#8250df", "a": "#953800", "s": "#0a3069", "n": "#0550ae",
    "l": "#0550ae",
}
_TERMINAL_DARK = {
    "bg": "var(--jp-cell-editor-background,var(--vscode-editor-background,#0d1117))", "accent": "#58a6ff",
    "prompt": "#d2a8ff", "ok": "#3fb950", "bad": "#f85149", "warn": "#d29922", "comment": "#8b949e", "k": "#ff7b72",
    "f": "#d2a8ff", "a": "#ffa657", "s": "#a5d6ff", "n": "#79c0ff", "l": "#79c0ff",
}


def _theme_tokens(prefix: str, values: dict[str, str]) -> str:
    return ";".join(f"--{prefix}-{name}:{value}" for name, value in values.items())


def _terminal_theme(roots: tuple[str, ...], prefix: str, *, dark_roots: tuple[str, ...] = ()) -> str:
    """
    The chat windows' look as CSS custom properties on `roots`: --<prefix>-mono (the font), -bg (a code editor's
    background, JupyterLab's or VS Code's when they say), -line, -dim, -hover, the -accent and -prompt colours, -ok,
    -bad, -warn, -comment, and the code colours (-k keywords, -f calls, -a arguments, -s text, -n numbers, -l
    literals). Dark when JupyterLab or VS Code is, or the browser is and neither says; dark_roots are more selectors
    that are always dark.
    """
    dark_selectors = [f"{page} {root}" for page in _DARK_PAGES for root in roots] + list(dark_roots)
    unset = ",".join(f"{_UNSET_PAGE} {root}" for root in roots)
    dark = _theme_tokens(prefix, _TERMINAL_DARK)
    return "\n".join([f"{','.join(roots)}{{{_theme_tokens(prefix, _TERMINAL_LIGHT)}}}",
                      f"{','.join(dark_selectors)}{{{dark}}}",
                      f"@media (prefers-color-scheme:dark){{{unset}{{{dark}}}}}"])


_ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' "
             "stroke-linecap='round' stroke-linejoin='round'>{}</svg>")


def _icon_url(paths: str) -> str:
    """
    A line drawing's paths (24 x 24) as a CSS url(): the windows draw their icons as masks of it, in the text's
    colour, so they follow the theme.
    """
    return f'url("data:image/svg+xml,{quote(_ICON_SVG.format(paths))}")'


def _row_button(w: Any, classes: tuple[str, str, str], on_click: Callable[[Any], None], *, tooltip: str | None = None,
                after: Iterable[Any] = ()) -> tuple[Any, Any, Any]:
    """
    A line the whole of which is the click target: a full-width button under its face, an HTML widget that shows
    what the line says (the window's CSS lets clicks through it). classes: the CSS classes of the box, the button and
    the face; after: widgets after them in the box (a line's own small button). Returns (box, button, face).
    """
    button = w.Button(tooltip=tooltip, layout=w.Layout(width="100%", height="100%"))
    button.add_class(classes[1])
    button.on_click(on_click)
    face = w.HTML(layout=w.Layout(width="100%"))
    face.add_class(classes[2])
    box = w.Box([button, face, *after], layout=w.Layout(width="100%"))
    box.add_class(classes[0])
    return box, button, face


def _class_if(widget: Any, name: str, on: bool) -> None:
    """
    Adds or removes a widget's CSS class, sending nothing when it's already as wanted.
    """
    if on and name not in widget._dom_classes:
        widget.add_class(name)
    elif not on and name in widget._dom_classes:
        widget.remove_class(name)


def _skeleton(text: str) -> str:
    """
    A page still loading: what's being read, with a spinner, over grey bars where the report will be.
    """
    return (f'<div class="skw"><div class="skl"><span class="spin"></span>{_esc(text)}</div><div class="sk t"></div>'
            '<div class="skc"><div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div>'
            '</div><div class="sk"></div><div class="sk" style="width:86%"></div><div class="sk" style="width:64%">'
            "</div></div>")


def _for_window(blocks: list[Any], words: Callable[[str], str]) -> list[Any]:
    """
    A report's blocks for an explorer window: no Next block (its calls are for a cell), and the commands its
    sentences name changed into the tabs that show the same (words: the window's _window_text).
    """
    out = []
    for block in blocks:
        if isinstance(block, _Next):
            continue
        if isinstance(block, _Findings):
            block = dataclasses.replace(block, items=[(level, words(m)) for level, m in block.items],
                                        empty=words(block.empty))
        elif isinstance(block, _Note):
            block = dataclasses.replace(block, text=words(block.text))
        elif isinstance(block, _Title):
            block = dataclasses.replace(block, sub=words(block.sub))
        elif isinstance(block, _Table):
            rows = [[words(cell) if j in block.prose_cols and isinstance(cell, str) else cell
                     for j, cell in enumerate(row)] for row in block.rows]
            block = dataclasses.replace(block, rows=rows, title=words(block.title))
        out.append(block)
    return out


def _window_errors(method: Callable) -> Callable:
    """
    For a window's own commands (x.open(...), x.refresh()): an error is said in the window (or, without the window,
    as a note) instead of a traceback. The window says when a command can't run at all (_ready) and how an error is
    said (_command_failed, which returns False for one that should still raise).
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        if not self._ready():
            return None
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            if not self._command_failed(exc, method.__name__):
                raise
        return None

    return wrapper


class _Window:
    """
    What the windows' classes share: widget callbacks that say their errors in the window, Enter in a text box,
    and widgets set quietly or only when their HTML changed.

    A window says where an error goes (_warn: its status line) and what an AWS error means (_explain: its View's),
    in its own words (_reword: an explorer turns a report's commands into its tabs). Its pickers (_Picker) go in
    pickers, and its backdrop, the invisible button under an open list, calls _close_lists. `quiet` is set while
    the code, not a person, changes a widget (_quietly), so the widget's observer does nothing.
    """

    quiet = False
    pickers: list[_Picker]
    backdrop: Any

    def _warn(self, text: str) -> None:
        """
        Says what went wrong where the window says things.
        """
        raise NotImplementedError

    def _explain(self, code: str, message: str) -> str:
        """
        An AWS error's message, plus what to do about it.
        """
        return message

    @staticmethod
    def _reword(text: str) -> str:
        """
        A sentence from a report, as the window says it.
        """
        return text

    def _ready(self) -> bool:
        """
        Whether a command can run (see _window_errors).
        """
        return True

    def _command_failed(self, exc: Exception, command: str) -> bool:
        """
        Says a command's error in the window (see _window_errors); False when it isn't one to say.
        """
        return False

    def _safely(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """
        A widget callback that says any error it raises in the window (_warn): raised, it would only reach the
        browser's log, and the click would seem to do nothing.
        """

        @functools.wraps(handler)
        def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return handler(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self._warn(self._error_text(exc))
                return None

        return run

    def _on_enter(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """
        A text box's Enter: the 'submit' message the box sends (on_submit is deprecated), so leaving the box
        doesn't trigger it.
        """
        safe = self._safely(handler)
        return lambda _widget, content, _buffers: safe() if content.get("event") == "submit" else None

    def _error_text(self, exc: BaseException) -> str:
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            return f"{code}: {self._reword(self._explain(code, message))}"
        if isinstance(exc, _Hint):
            return self._reword(str(exc))
        return f"{type(exc).__name__}: {exc}"

    def _quietly(self, widget: Any, **values: Any) -> None:
        self.quiet = True
        try:
            for name, value in values.items():
                setattr(widget, name, value)
        finally:
            self.quiet = False

    @staticmethod
    def _set(widget: Any, value: str) -> None:
        """
        An HTML widget's new content, sent only when it changed: sending the same HTML again would fold up every
        part the user opened (a chunk, a run, a JSON object, a table).
        """
        if widget.value != value:
            widget.value = value

    def _close_lists(self) -> None:
        """
        A click on the backdrop, anywhere in the window but the open list: closes it.
        """
        for picker in self.pickers:
            picker.close()


@dataclasses.dataclass
class _Job:
    """
    A job _Explorer._later runs in the background: its key and number (a newer job of the same key makes its
    result go unused), what to do with its result or its error, and the status line it shows while it counts, as
    long as the line is still owner's.
    """

    key: str
    number: int
    done: Callable[[Any], None]
    failed: Callable[[BaseException], None]
    counting: str = ""
    owner: str = ""


class _Explorer(_Window):
    """
    What the knowledge base and Lambda explorers share on top of _Window: the status line under the tabs, AWS read
    off the notebook's event loop (_later), report blocks drawn into a page (_draw), their commands' errors said in
    the window, and the window shown once per cell.

    A subclass calls this __init__ first and sets ui (its View) and height; its _build makes the head, the tab bar
    (_tab_bar) and the pages, and _frame puts them in the window. _show_tab(key) opens a tab, and _html(blocks) draws
    a report the way the window shows them. _PREFIX starts its CSS class names (.kbx-app, .kbx-tab, ...), and
    _THREADS names its worker threads.

    What it keeps:

        shown      page -> the blocks drawn there last (for tests, and the curious)
        _w         ipywidgets, when the window shows (else the explorer shows reports)
        _jobs      background job -> its latest number: an older one's result is dropped
        _tasks     background job -> its asyncio task (tests wait for them)
        _counted   how much the background read has read so far
        _said      the status line: its text, level, whether it's busy, and whose it is
        _shown_at  the cell the window was last shown in
        pickers    the fields that open a list, of which one is open at a time
    """

    _PREFIX = "explorer"
    _THREADS = "explorer"
    _WORKERS = 4
    _COUNT_EVERY = 0.4

    def __init__(self) -> None:
        self.shown: dict[str, list[Any]] = {}
        self.quiet = False
        self._w: Any = None
        self._jobs: dict[str, int] = {}
        self._tasks: dict[str, Any] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._counted = 0
        self._said: tuple[str, str, bool, str] = ("", "", False, "")
        self._shown_at: Any = object()
        self.pickers: list[_Picker] = []

    def _load_widgets(self, mode: str) -> str:
        """
        Loads ipywidgets into _w when the window can show (mode 'widgets', or 'auto' in a notebook). Returns what
        to say instead when it isn't installed ('' otherwise).
        """
        if mode != "text" and (mode == "widgets" or _in_notebook()):
            try:
                self._w = _require("ipywidgets", "The explorer window")
            except ImportError as exc:
                return (f"{exc}, which SageMaker notebooks normally have: install it and restart the kernel. Until "
                        "then, the same in reports:")
        return ""

    def _ipython_display_(self) -> None:
        """
        A cell ending with the explorer shows its window, unless the same cell already did (without the window,
        its reports were shown when it was made).
        """
        if self._w is not None and _cell_number() != self._shown_at:
            self._display()

    def _display(self) -> None:
        """
        Shows the window in the running cell's output; outside IPython there's nowhere to show widgets.
        """
        self._shown_at = _cell_number()
        if self._shown_at is None:
            return
        from IPython.display import display

        display(self.root)

    def _tab_bar(self, tabs: Iterable[tuple[str, str, str, str]]) -> Any:
        """
        The tab buttons in a row, one per (key, title, what it holds, its icon's paths), in tab_buttons: a click
        opens its tab. The CSS draws each one's icon from its <prefix>-i-<key> class.
        """
        w, layout, prefix = self._w, self._w.Layout, self._PREFIX
        self.tab_buttons: dict[str, Any] = {}
        for key, title, tip, _ in tabs:
            button = w.Button(description=title, tooltip=tip, layout=layout(width="auto"))
            for name in (f"{prefix}-tab", f"{prefix}-i-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._show_tab(key)))
            self.tab_buttons[key] = button
        bar = w.HBox(list(self.tab_buttons.values()), layout=layout(width="100%"))
        bar.add_class(f"{prefix}-tabs")
        return bar

    def _frame(self, css: str, head: Any, tabs: Any, pages: dict[str, Any]) -> None:
        """
        The window, in root: its stylesheet (in a hidden HTML widget), the head and the tab bar over the tabs'
        pages (only the open one shows; each is height= tall when that's set), the status line and the backdrop.
        """
        w, layout, prefix = self._w, self._w.Layout, self._PREFIX
        style = w.HTML(css, layout=layout(display="none"))
        height = _css_height(self.height)
        self.pages = pages
        for key, page in pages.items():
            page.add_class(f"{prefix}-page")
            page.layout.width = "100%"
            if height:
                page.layout.height = height
            if key != self._tab:
                page.layout.display = "none"
        self.status = w.HTML(layout=layout(width="100%"))
        self.status.add_class(f"{prefix}-status")
        self.root = w.VBox([style, head, tabs, *pages.values(), self.status, self.backdrop],
                           layout=layout(width="100%"))
        self.root.add_class(f"{prefix}-app")

    def _show_tab(self, key: str) -> None:
        raise NotImplementedError

    def _explain(self, code: str, message: str) -> str:
        return self.ui._explain(code, message)

    def _warn(self, text: str) -> None:
        self._status(text, "warn")

    def _command_failed(self, exc: Exception, command: str) -> bool:
        if not isinstance(exc, (ClientError, BotoCoreError, ValueError, TypeError)):
            return False
        if self._w is None:
            self.ui._show([_Note(self._error_text(exc), "warn")])
        else:
            self._status(self._error_text(exc), "warn")
        return True

    def _status(self, text: str, level: str = "", busy: bool = False, owner: str = "") -> None:
        """
        The line under the tabs. owner: the background job a busy line is about (it says the job's last word).
        """
        self._said = (text, level, busy, owner)
        spin = '<span class="spin"></span>' if busy else ""
        self._set(self.status, f'<div class="st {level}">{spin}{_prose(text)}</div>' if text else "")

    def _finish(self, owner: str, text: str, level: str = "", *, tabs: Iterable[str] = (), busy: bool = False) -> None:
        """
        A background job's word in the status line (its last, unless busy= says it goes on): said while the line is
        still that job's (busy), or while one of its tabs is open; otherwise the line stays as it is.
        """
        if (self._said[2] and self._said[3] == owner) or self._tab in tabs:
            self._status(text, level, busy=busy, owner=owner if busy else "")

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them (each explorer draws them in its own service's look).
        """
        raise NotImplementedError

    def _draw(self, page: str, widget: Any, blocks: list[Any]) -> None:
        self.shown[page] = blocks
        self._set(widget, self._html(blocks))

    def _workers(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._WORKERS, thread_name_prefix=self._THREADS)
        return self._pool

    def _later(self, key: str, work: Callable[[], Any], done: Callable[[Any], None],  # noqa: PLR0913
               failed: Callable[[BaseException], None] | None = None, counting: str = "", owner: str = "") -> None:
        """
        Runs work() off the notebook's event loop, then done(result) on it, so a click returns at once and the
        window keeps answering while AWS is read. Without a running loop (a script, the tests) it runs here and now.
        A newer job of the same key makes this one's result go unused. Whatever work() raises goes to failed (by
        default said in the status line). counting: the status line to show while it runs and the line is still
        owner's, {n} standing for the count read so far.
        """
        self._jobs[key] = number = self._jobs.get(key, 0) + 1
        job = _Job(key, number, done, failed or self._say_error, counting, owner)
        loop = _running_loop()
        if loop is None:
            self._run_now(job, work)
        else:
            self._tasks[key] = loop.create_task(self._run_in_background(job, loop, work))

    def _say_error(self, exc: BaseException) -> None:
        self._status(self._error_text(exc), "warn")

    def _is_latest(self, job: _Job) -> bool:
        return self._jobs.get(job.key) == job.number

    def _hand_back(self, job: _Job, handler: Callable[[Any], None], value: Any) -> None:
        """
        Gives a job's result (or its error) to its handler, unless a newer job of its key started since. A bug in
        drawing it is said in the window, like a click's.
        """
        if not self._is_latest(job):
            return
        try:
            handler(value)
        except Exception as exc:  # noqa: BLE001
            self._say_error(exc)

    def _run_now(self, job: _Job, work: Callable[[], Any]) -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            self._hand_back(job, job.failed, exc)
        else:
            self._hand_back(job, job.done, result)

    async def _run_in_background(self, job: _Job, loop: asyncio.AbstractEventLoop, work: Callable[[], Any]) -> None:
        future = loop.run_in_executor(self._workers(), work)
        await self._count_while_waiting(job, future)
        try:
            result = await future
        except Exception as exc:  # noqa: BLE001
            self._hand_back(job, job.failed, exc)
        else:
            self._hand_back(job, job.done, result)
        finally:
            if self._tasks.get(job.key) is asyncio.current_task():
                del self._tasks[job.key]

    async def _count_while_waiting(self, job: _Job, future: asyncio.Future) -> None:
        """
        While a job that counts runs, says how much it has read every _COUNT_EVERY seconds, as long as the status
        line is still its owner's and no newer job of its key started.
        """
        while job.counting and not future.done():
            await asyncio.wait([future], timeout=self._COUNT_EVERY)
            still_counting = self._is_latest(job) and not future.done() and self._said[3] == job.owner
            if still_counting and self._counted:
                self._status(job.counting.replace("{n}", f"{self._counted:,}"), busy=True, owner=job.owner)

    def _drop(self, *keys: str) -> None:
        """
        Makes what's still being read for these jobs go unused when it comes back.
        """
        for key in keys:
            self._jobs[key] = self._jobs.get(key, 0) + 1


class _Picker:
    """
    A window's field that opens a list to pick from, with a search box over it. Plain widgets and CSS: the field is
    a button under its face (_row_button), the list opens under it over the window (its panel, <prefix>-pop), and
    each line of the list is a button under its text, made the first time it's shown and kept (rows, _line), so the
    whole line is the click target. One list is open at a time (the window's pickers), and while one is, the window's
    backdrop (an invisible button over the rest of the window, under the open field) takes a click anywhere else and
    closes it, as a dropdown would. Enter picks the first line the search box finds, or hands what's typed to on_text
    when there's none (an ID or ARN the list doesn't hold).

    A subclass says what the search box finds a choice by (_fields; _query rewrites what's typed first, an ARN into
    its name), a choice's value (_key), and draws the field's face, the list (_draw_list) and its lines, and what a
    click on one does (_clicked). prefix starts the CSS classes (kbx: .kbx-field, .kbx-trig, .kbx-opt, ...).

    What it keeps: `problem`, why there's nothing to list (it couldn't be read), shown under the list; `message`,
    what went wrong with the last Enter; `shown`, the values of the lines the list shows, top to bottom; and `rows`,
    value -> (its line, the line's button, its text).
    """

    def __init__(self, app: _Window, w: Any, prefix: str, *, on_pick: Callable[[Any], None],  # noqa: PLR0913
                 on_text: Callable[[str], None] | None, placeholder: str, tooltip: str | None = None,
                 close_width: str | None = None, list_width: str | None = None,
                 field_layout: dict[str, str] | None = None):
        layout = w.Layout
        self.app, self.w, self.prefix, self.on_pick, self.on_text = app, w, prefix, on_pick, on_text
        self.choices: list[Any] = []
        self.problem = ""
        self.message = ""
        self.shown: list[str] = []
        self.rows: dict[str, tuple[Any, Any, Any]] = {}
        trigger, self.button, self.face = _row_button(w, (f"{prefix}-trig", f"{prefix}-trig-b", f"{prefix}-face"),
                                                      app._safely(lambda _button: self.toggle()), tooltip=tooltip)
        self.search = w.Text(placeholder=placeholder, continuous_update=True,
                             layout=layout(flex="1 1 auto", width="auto"))
        self.search.add_class(f"{prefix}-find")
        self.search.observe(app._safely(self._typed), names="value")
        self.search.on_msg(app._on_enter(self._entered))
        close = w.Button(description="✕", tooltip="Close the list", layout=layout(width=close_width, flex="0 0 auto"))
        close.add_class(f"{prefix}-x")
        close.on_click(app._safely(lambda _button: self.close()))
        self.list = w.VBox(layout=layout(width="100%"))
        self.list.add_class(f"{prefix}-opts")
        self.foot = w.HTML(layout=layout(width="100%"))
        self.panel = w.VBox([w.HBox([self.search, close], layout=layout(width="100%", align_items="center")),
                             self.list, self.foot], layout=layout(display="none", min_width=list_width))
        self.panel.add_class(f"{prefix}-pop")
        self.field = w.VBox([trigger, self.panel], layout=layout(**(field_layout or {})))
        self.field.add_class(f"{prefix}-field")
        app.pickers.append(self)

    @property
    def is_open(self) -> bool:
        return self.panel.layout.display != "none"

    def _can_open(self) -> bool:
        return True

    def _opening(self) -> None:
        """
        What to do as the list opens, before it's drawn.
        """

    def open(self) -> None:
        """
        Opens the list, closing any other open one: one list is open at a time. The search box takes the keys
        (ipywidgets 8 can focus it).
        """
        if not self._can_open() or self.is_open:
            return
        for other in self.app.pickers:
            if other is not self:
                other.close()
        self._opening()
        self.message = ""
        self.panel.layout.display = ""
        self.app.backdrop.layout.display = ""
        _class_if(self.field, f"{self.prefix}-open", True)
        self._draw_list()
        if hasattr(self.search, "focus"):
            self.search.focus()

    def close(self) -> None:
        if not self.is_open:
            return
        self.panel.layout.display = "none"
        if not any(p.is_open for p in self.app.pickers):
            self.app.backdrop.layout.display = "none"
        _class_if(self.field, f"{self.prefix}-open", False)
        self.app._quietly(self.search, value="")
        self.message = ""

    def toggle(self) -> None:
        if self.is_open:
            self.close()
        else:
            self.open()

    def _query(self, text: str) -> str:
        """
        What's typed, as the search looks for it.
        """
        return text

    def _fields(self, choice: Any) -> Iterable[Any]:
        """
        What the search box finds a choice by (its name, ID, description...).
        """
        raise NotImplementedError

    def _key(self, choice: Any) -> str:
        """
        The value a choice's line stands for.
        """
        raise NotImplementedError

    def matches(self) -> list[Any]:
        """
        The choices the search box finds, best first (search_rank); every one, in order, while it's empty.
        """
        text = self._query(str(self.search.value or ""))
        ranked = [(rank, i, c) for i, c in enumerate(self.choices)
                  if (rank := search_rank(text, self._fields(c))) is not None]
        return [c for _, _, c in sorted(ranked, key=lambda r: r[:2])]

    def _line(self, value: str) -> tuple[Any, Any, Any]:
        """
        The widgets of value's line in the list (its line, the line's button, its text), made once.
        """
        if value not in self.rows:
            prefix = self.prefix
            self.rows[value] = _row_button(self.w, (f"{prefix}-opt", f"{prefix}-opt-b", f"{prefix}-opt-t"),
                                           self.app._safely(lambda _button: self._clicked(value)))
        return self.rows[value]

    def _draw_list(self) -> None:
        raise NotImplementedError

    def _clicked(self, value: str) -> None:
        raise NotImplementedError

    def _typed(self, change: dict[str, Any]) -> None:
        self._draw_list()

    def _entered(self) -> None:
        """
        Enter in the search box: picks the first line found, or hands the text to on_text when nothing is found
        (a picker without on_text has an _entered of its own). What goes wrong is said under the list, where the eyes
        are.
        """
        text = str(self.search.value or "").strip()
        if not text:
            self.close()
            return
        found = self.matches()
        try:
            if found:
                self._clicked(self._key(found[0]))
            else:
                self.on_text(text)  # type: ignore[misc]
                self.close()
        except (ValueError, ClientError, BotoCoreError) as exc:
            self.message = str(exc) if isinstance(exc, ValueError) else self.app._error_text(exc)
            self._draw_list()
