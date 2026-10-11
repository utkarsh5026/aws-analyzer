"""What every service's View shares: how a report is shown (HTML in Jupyter, text elsewhere), progress while a long
command runs, help(), and @_friendly_errors, which shows an error as a note instead of a traceback.

A service's <Service>View subclasses _BaseView, makes its analyzer in __init__ (self.core) and sets what's its own as
class attributes: _render_html (its look: stylesheet, root class and badge), _GROUPS and _START (how help() lists
its commands), and where they differ, _PROGRESS (what _progress() says without a label) and _DATA_ERRORS (what
else its commands raise that a note should show). It says what an AWS error means for it in _explain.
"""

from __future__ import annotations

import difflib
import functools
import inspect
import sys
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator

from botocore.exceptions import BotoCoreError, ClientError

from .blocks import _Next, _Note, _Table, _Text, _Title
from .deps import _in_notebook
from .errors import _Hint
from .progress import _progress_bar, _progress_bar_class, _progress_text
from .render import _render_text, _signature
from .text import _esc


def _commands(cls: type) -> dict[str, Callable]:
    """A View's commands, name -> function: the public methods of its class and of the classes it's made from (so
    _BaseView's help() too), the View's own first, in the order they're defined."""
    commands: dict[str, Callable] = {}
    for klass in cls.__mro__:
        for name, member in vars(klass).items():
            if not name.startswith("_") and callable(member) and name not in commands:
                commands[name] = inspect.unwrap(member)
    return commands


def _which_prices(prices: dict[str, Any], list_prices: dict[str, Any]) -> str:
    """How a report names the prices its costs come from: AWS's list prices, or the ones passed in prices=."""
    return "us-east-1 list prices" if prices == list_prices else "your prices"


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS and input errors as a readable note instead of a traceback. The view says what an AWS error means
    (_explain_call, which is _explain unless the command's arguments help), which other errors are shown as they are
    (_DATA_ERRORS) and what to say about its own (_extra_note); anything else still raises."""

    @functools.wraps(method)
    def wrapper(self: _BaseView, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            explained = self._explain_call(code, message, method.__name__, args, kwargs)
            self._show([_Note(f"{code}: {explained}  [{method.__name__}]", "warn")])
        except _Hint as exc:  # a question back to the user (which knowledge base?), not an error
            self._show([_Note(str(exc))])
        except ImportError as exc:  # a missing optional package: the message says what to pip install
            self._show([_Note(f"{str(exc).rstrip('.')}.", "warn")])
        except (BotoCoreError, *self._DATA_ERRORS) as exc:
            self._show([_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")])
        except Exception as exc:
            note = self._extra_note(exc, method.__name__)
            if note is None:
                raise
            self._show([note])

    return wrapper


class _BaseView:
    """What every <Service>View starts with: the mode, max_rows and progress options, how a report is shown,
    progress while a long command runs, help(), and the hooks @_friendly_errors asks. The only command here is
    help(); every other one is the service's, decorated with @_friendly_errors, and renders a report and returns
    nothing."""

    core: Any  # the service's analyzer: the View's __init__ makes one when it isn't given one
    _render_html: Callable[[list[Any], int], str]  # the service's look: its stylesheet, root class and badge
    _render_text = staticmethod(_render_text)  # how a report reads as text, the same for every service
    _TABLE: Callable[..., Any] = _Table  # the block help() lists commands in (S3 draws its own until Phase 6)
    _GROUPS: dict[str, tuple[str, ...]] = {}  # help() lists the commands in these groups, in this order
    _START: tuple[tuple[str, str], ...] = ()  # help()'s "Start here": (call, what it shows)
    _PROGRESS = ("Reading", "items read")  # the label and unit _progress() shows when it's given none
    _DATA_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError)  # shown as notes: "ValueError: ..."
    _progress_owner: Callable[[], None] | None = None  # clears the progress bar showing now

    def __init__(self, *, mode: str = "auto", max_rows: int = 50, progress: str = "auto"):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.progress = progress

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            try:
                from IPython.display import HTML, display
            except ImportError:  # mode='html' outside Jupyter: show text, and say why (once)
                self.use_html = False
                note = "mode='html' only works in Jupyter (IPython isn't installed here), so this is shown as text."
                blocks = [*blocks, _Note(note, "warn")]
            else:
                display(HTML(self._render_html(blocks, self.max_rows)))
                return
        print(self._render_text(blocks, self.max_rows))

    @contextmanager
    def _progress(self, label: str | None = None,
                  unit: str | None = None) -> Generator[Callable[..., None], None, None]:
        """Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away. Without a label or a
        unit, it shows the View's _PROGRESS."""
        label = self._PROGRESS[0] if label is None else label
        unit = self._PROGRESS[1] if unit is None else unit
        notebook = self.use_html and _in_notebook()  # elsewhere (or without IPython) the plain line goes to stderr
        bar_class = [_progress_bar_class(notebook) if self.progress == "auto" else None]
        bar: list[Any] = [None]
        handle: list[Any] = [None]
        started: list[Any] = [time.monotonic(), None]  # when the current total started, and that total
        shown, width, stopped = [0.0], [0], [False]

        def close_bar() -> None:
            if bar[0] is not None:
                if not stopped[0] and bar[0].total and bar[0].n < bar[0].total:
                    bar[0].total = bar[0].n  # done early (a file it couldn't read): no red "failed" widget
                bar[0].close()
                bar[0] = None

        def clear() -> None:
            close_bar()
            if handle[0] is not None:
                from IPython.display import HTML

                handle[0].update(HTML(""))
            elif width[0]:
                print("\r" + " " * width[0] + "\r", end="", file=sys.stderr, flush=True)
                width[0] = 0

        def take_over() -> None:
            owner = self._progress_owner
            if owner is not clear:
                if owner is not None:
                    owner()
                self._progress_owner = clear

        def tick(count: int, total: int | None = None) -> None:
            if self.progress == "off":
                return
            if bar_class[0] is not None:
                try:
                    if bar[0] is not None and total != bar[0].total:
                        close_bar()
                    if bar[0] is None:
                        take_over()
                        bar[0] = _progress_bar(bar_class[0], label, unit, total)
                    bar[0].update(count - bar[0].n)
                    return
                except Exception:  # an old tqdm or a broken widget front end: use the plain line instead
                    bar_class[0] = None
            now = time.monotonic()
            if total != started[1]:
                started[:] = [now, total]
            if now - started[0] < 0.5 or now - shown[0] < 0.5:
                return
            shown[0] = now
            take_over()
            text = _progress_text(label, unit, count, total, now - started[0])
            if notebook:
                from IPython.display import HTML, display

                if handle[0] is None:
                    handle[0] = display(HTML(""), display_id=True)
                if handle[0] is not None:  # display() returns None outside IPython
                    handle[0].update(HTML(f'<div style="opacity:.6">{_esc(text)}</div>'))
            else:
                width[0] = max(width[0], len(text))
                print("\r" + text.ljust(width[0]), end="", file=sys.stderr, flush=True)

        try:
            yield tick
        except BaseException:
            stopped[0] = True  # interrupted: a tqdm widget stays, red, where it stopped
            raise
        finally:
            clear()
            if self._progress_owner is clear:
                self._progress_owner = None

    # ------------------------------------------------------------------ what @_friendly_errors asks

    def _explain(self, code: str, message: str) -> str:
        """What an AWS error means, and what to do about it. The service's View says more where it can; here, AWS's
        message as it is."""
        return message

    def _explain_call(self, code: str, message: str, command: str, args: tuple, kwargs: dict) -> str:
        """What a command's AWS error means: _explain's, unless the View needs the command's arguments to say more
        (DynamoDB names the table that wasn't found, and the closest names)."""
        return self._explain(code, message)

    def _extra_note(self, exc: Exception, command: str) -> _Note | None:
        """The note for an error of the service's own (OpenSearch's failed requests), or None to let it raise."""
        return None

    def _price_basis(self) -> str:
        """The prices a report's costs are worked out with: AWS's list prices, or the analyzer's prices=."""
        return _which_prices(self.core.prices, self.core._LIST_PRICES)

    # ------------------------------------------------------------------ help

    def help(self, command: Any = None) -> None:
        """Every command, grouped by task; help('name') shows one command in full."""
        view = type(self).__name__
        commands = _commands(type(self))

        def about(name: str) -> str:  # the docstring's first paragraph, on one line
            return " ".join((inspect.getdoc(commands[name]) or "").split("\n\n")[0].split())

        if command is not None:
            name = getattr(command, "__name__", str(command))
            if name not in commands:
                close = difflib.get_close_matches(name, list(commands), n=3)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                self._show([_Note(f"{view} has no command {name!r}.{hint} help() lists them all.", "warn")])
                return
            self._show([_Title(f"{name}{_signature(commands[name])}", f"{view} command · help() lists them all"),
                        _Text(inspect.getdoc(commands[name]) or "(no description)", wrap=True)])
            return
        grouped = {name for names in self._GROUPS.values() for name in names}
        groups = {**self._GROUPS, "Other": tuple(name for name in commands if name not in grouped)}
        blocks: list[Any] = [
            _Title(f"{view} commands", "help('name') shows one in full · the data behind each report "
                                       f"comes from .core ({type(self.core).__name__})"),
            _Next(list(self._START), title="Start here"),
        ]
        for group, names in groups.items():
            rows = [[f"{name}{_signature(commands[name])}", about(name)] for name in names if name in commands]
            if rows:
                blocks.append(self._TABLE(["Command", "What it shows"], rows, title=group, max_rows=0, code_cols=(0,)))
        self._show(blocks)
