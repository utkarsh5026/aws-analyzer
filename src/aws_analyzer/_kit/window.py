"""What the notebook windows share: the S3 explorer, the chat, and the knowledge base and Lambda explorers.

The windows are ipywidgets laid out and styled by CSS, with no JavaScript. ipywidgets and IPython load only inside the
functions that need them (the window itself loads ipywidgets with `_require`), so this module imports with boto3 alone.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from .blocks import _Findings, _Next, _Note, _Table, _Title
from .deps import _in_notebook, _require
from .errors import _Hint
from .render import _prose
from .text import _esc


def _css_height(height: int | str | None) -> str | None:
    """height= as CSS: a number is pixels (720, '720'), other text is CSS as written ('80vh'); None is None."""
    text = "" if height is None else str(height).strip()
    return f"{text}px" if re.fullmatch(r"\d+(\.\d+)?", text) else text or None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The kernel's event loop, which runs the window's clicks in a notebook; None elsewhere (a script, the tests),
    where the window's work runs right away while the click waits."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _cell_number() -> Any:
    """The running cell's execution count in IPython (None elsewhere): a cell that ends with a window (chat(),
    explore()) shows it once, not twice."""
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return None
    shell = get_ipython()
    return getattr(shell, "execution_count", None) if shell is not None else None


def _class_if(widget: Any, name: str, on: bool) -> None:
    """Adds or removes a widget's CSS class, sending nothing when it's already as wanted."""
    if on and name not in widget._dom_classes:
        widget.add_class(name)
    elif not on and name in widget._dom_classes:
        widget.remove_class(name)


def _skeleton(text: str) -> str:
    """A page still loading: what's being read, with a spinner, over grey bars where the report will be."""
    return (f'<div class="skw"><div class="skl"><span class="spin"></span>{_esc(text)}</div><div class="sk t"></div>'
            '<div class="skc"><div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div>'
            '</div><div class="sk"></div><div class="sk" style="width:86%"></div><div class="sk" style="width:64%">'
            "</div></div>")


def _for_window(blocks: list[Any], words: Callable[[str], str]) -> list[Any]:
    """A report's blocks for an explorer window: no Next block (its calls are for a cell), and the commands its
    sentences name changed into the tabs that show the same (words: the window's _window_text)."""
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
    """For a window's own commands (x.open(...), x.refresh()): an error is said in the window (or, without the window,
    as a note) instead of a traceback. The window says when a command can't run at all (_ready) and how an error is
    said (_command_failed, which returns False for one that should still raise)."""

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
    """What the windows' classes share: widget callbacks that say their errors in the window, Enter in a text box,
    and widgets set quietly or only when their HTML changed.

    A window says where an error goes (_warn: its status line) and what an AWS error means (_explain: its View's),
    in its own words (_reword: an explorer turns a report's commands into its tabs)."""

    quiet = False  # set while the code (not a person) changes a widget, so its observer does nothing

    def _warn(self, text: str) -> None:
        """Says what went wrong where the window says things."""
        raise NotImplementedError

    def _explain(self, code: str, message: str) -> str:
        """An AWS error's message, plus what to do about it."""
        return message

    @staticmethod
    def _reword(text: str) -> str:
        """A sentence from a report, as the window says it."""
        return text

    def _ready(self) -> bool:
        """Whether a command can run (see _window_errors)."""
        return True

    def _command_failed(self, exc: Exception, command: str) -> bool:
        """Says a command's error in the window (see _window_errors); False when it isn't one to say."""
        return False

    def _safely(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(handler)
        def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return handler(*args, **kwargs)
            except Exception as exc:  # shown in the window: a widget callback's error would go to the browser log
                self._warn(self._error_text(exc))
                return None

        return run

    def _on_enter(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """A text box's Enter: the 'submit' message the box sends (on_submit is deprecated), so leaving the box
        doesn't trigger it."""
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
        """An HTML widget's new content, sent only when it changed: sending the same HTML again would fold up every
        part the user opened (a chunk, a run, a JSON object, a table)."""
        if widget.value != value:
            widget.value = value


class _Explorer(_Window):
    """What the knowledge base and Lambda explorers share on top of _Window: the status line under the tabs, AWS read
    off the notebook's event loop (_later), report blocks drawn into a page (_draw), their commands' errors said in
    the window, and the window shown once per cell.

    A subclass calls this __init__ first, sets ui (its View), and builds root (the window) and status (the line);
    _html(blocks) draws a report the way it shows them. _THREADS names its worker threads."""

    _THREADS = "explorer"

    def __init__(self) -> None:
        self.shown: dict[str, list[Any]] = {}  # page -> the blocks drawn there last (for tests, and the curious)
        self.quiet = False
        self._w: Any = None  # ipywidgets, when the window shows (else the explorer shows reports)
        self._jobs: dict[str, int] = {}  # background job -> its latest number: an older one's result is dropped
        self._tasks: dict[str, Any] = {}  # background job -> its asyncio task (tests wait for them)
        self._pool: ThreadPoolExecutor | None = None
        self._counted = 0  # what the background read has read so far
        self._said: tuple[str, str, bool, str] = ("", "", False, "")  # the status line: text, level, busy, whose
        self._shown_at: Any = object()

    def _load_widgets(self, mode: str) -> str:
        """Loads ipywidgets into _w when the window can show (mode 'widgets', or 'auto' in a notebook). Returns what
        to say instead when it isn't installed ('' otherwise)."""
        if mode != "text" and (mode == "widgets" or _in_notebook()):
            try:
                self._w = _require("ipywidgets", "The explorer window")
            except ImportError as exc:
                return (f"{exc}, which SageMaker notebooks normally have: install it and restart the kernel. Until "
                        "then, the same in reports:")
        return ""

    def _ipython_display_(self) -> None:
        """A cell ending with the explorer shows its window, unless the same cell already did (without the window,
        its reports were shown when it was made)."""
        if self._w is not None and _cell_number() != self._shown_at:
            self._display()

    def _display(self) -> None:
        self._shown_at = _cell_number()
        if self._shown_at is None:
            return  # outside IPython there's nowhere to show widgets
        from IPython.display import display

        display(self.root)

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
        """The line under the tabs. owner: the background job a busy line is about (it says the job's last word)."""
        self._said = (text, level, busy, owner)
        spin = '<span class="spin"></span>' if busy else ""
        self._set(self.status, f'<div class="st {level}">{spin}{_prose(text)}</div>' if text else "")

    def _finish(self, owner: str, text: str, level: str = "", *, tabs: Iterable[str] = (), busy: bool = False) -> None:
        """A background job's word in the status line (its last, unless busy= says it goes on): said while the line is
        still that job's (busy), or while one of its tabs is open; otherwise the line stays as it is."""
        if (self._said[2] and self._said[3] == owner) or self._tab in tabs:
            self._status(text, level, busy=busy, owner=owner if busy else "")

    def _html(self, blocks: list[Any]) -> str:
        """Report blocks as the window shows them (each explorer draws them in its own service's look)."""
        raise NotImplementedError

    def _draw(self, page: str, widget: Any, blocks: list[Any]) -> None:
        self.shown[page] = blocks
        self._set(widget, self._html(blocks))

    def _workers(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix=self._THREADS)
        return self._pool

    def _later(self, key: str, work: Callable[[], Any], done: Callable[[Any], None],
               failed: Callable[[BaseException], None] | None = None, counting: str = "", owner: str = "") -> None:
        """Runs work() off the notebook's event loop, then done(result) on it, so a click returns at once and the
        window keeps answering while AWS is read. Without a running loop (a script, the tests) it runs here and now.
        A newer job of the same key makes this one's result go unused. counting: the status line to show while it runs
        and the line is still owner's, {n} standing for the count read so far."""
        self._jobs[key] = job = self._jobs.get(key, 0) + 1
        failed = failed or (lambda exc: self._status(self._error_text(exc), "warn"))

        def finish(handler: Callable[[Any], None], value: Any) -> None:
            if self._jobs.get(key) != job:
                return
            try:
                handler(value)
            except Exception as exc:  # a bug in drawing it: said in the window, like a click's
                self._status(self._error_text(exc), "warn")

        loop = _running_loop()
        if loop is None:
            try:
                result = work()
            except Exception as exc:
                finish(failed, exc)
            else:
                finish(done, result)
            return

        async def run() -> None:
            future = loop.run_in_executor(self._workers(), work)
            while counting and not future.done():
                await asyncio.wait([future], timeout=0.4)
                if self._jobs.get(key) == job and not future.done() and self._counted and self._said[3] == owner:
                    self._status(counting.replace("{n}", f"{self._counted:,}"), busy=True, owner=owner)
            try:
                result = await future
            except Exception as exc:
                finish(failed, exc)
            else:
                finish(done, result)
            finally:
                if self._tasks.get(key) is task:
                    del self._tasks[key]

        task = loop.create_task(run())
        self._tasks[key] = task

    def _drop(self, *keys: str) -> None:
        """Makes what's still being read for these jobs go unused when it comes back."""
        for key in keys:
            self._jobs[key] = self._jobs.get(key, 0) + 1
