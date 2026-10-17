"""
What every service's View shares: how a report is shown (HTML in Jupyter, text elsewhere), progress while a long
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
from .fmt import _tz_label
from .progress import _progress_bar, _progress_bar_class, _progress_text
from .render import _render_text, _signature
from .text import _esc


def _commands(cls: type) -> dict[str, Callable]:
    """
    A View's commands, name -> function: the public methods of its class and of the classes it's made from (so
    _BaseView's help() too), the View's own first, in the order they're defined.
    """
    commands: dict[str, Callable] = {}
    for klass in cls.__mro__:
        for name, member in vars(klass).items():
            if not name.startswith("_") and callable(member) and name not in commands:
                commands[name] = inspect.unwrap(member)
    return commands


def _about(command: Callable) -> str:
    """
    What help() says a command does: the first paragraph of its docstring, on one line.
    """
    return " ".join((inspect.getdoc(command) or "").split("\n\n")[0].split())


def _which_prices(prices: dict[str, Any], list_prices: dict[str, Any]) -> str:
    """
    How a report names the prices its costs come from: AWS's list prices, or the ones passed in prices=.
    """
    return "us-east-1 list prices" if prices == list_prices else "your prices"


def _friendly_errors(method: Callable) -> Callable:
    """
    Show AWS and input errors as a readable note instead of a traceback. The view says what an AWS error means
    (_explain_call, which is _explain unless the command's arguments help), which other errors are shown as they are
    (_DATA_ERRORS) and what to say about its own (_extra_note); anything else still raises. A _Hint is a question
    back to the user (which knowledge base?), not an error, so it's a plain note, and a missing optional package's
    ImportError already says what to pip install.
    """

    @functools.wraps(method)
    def wrapper(self: _BaseView, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            explained = self._explain_call(code, message, method.__name__, args, kwargs)
            self._show([_Note(f"{code}: {explained}  [{method.__name__}]", "warn")])
        except _Hint as exc:
            self._show([_Note(str(exc))])
        except ImportError as exc:
            self._show([_Note(f"{str(exc).rstrip('.')}.", "warn")])
        except (BotoCoreError, *self._DATA_ERRORS) as exc:
            self._show([_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")])
        except Exception as exc:
            note = self._extra_note(exc, method.__name__)
            if note is None:
                raise
            self._show([note])

    return wrapper


_LINE_EVERY = 0.5


class _ProgressLine:
    """
    The progress one _BaseView._progress block shows: a tqdm bar while tqdm works, else a line with the count, time,
    rate and time left, redrawn at most every _LINE_EVERY seconds, and only once the work has run that long. In a
    notebook the line is an HTML output that's replaced; elsewhere (or without IPython) it's rewritten in place on
    stderr.

    Only one shows at a time: the view's _progress_owner is the line showing now, and a line that starts showing
    clears the one before it. `stopped` is set when the work was interrupted, so a tqdm widget stays, red, where it
    stopped.
    """

    def __init__(self, view: _BaseView, label: str, unit: str):
        self.view, self.label, self.unit = view, label, unit
        self.notebook = view.use_html and _in_notebook()
        self.bar_class = _progress_bar_class(self.notebook) if view.progress == "auto" else None
        self.bar: Any = None
        self.handle: Any = None
        self.started: float = time.monotonic()
        self.total: int | None = None
        self.shown = 0.0
        self.width = 0
        self.stopped = False

    def tick(self, count: int, total: int | None = None) -> None:
        """
        tick(count) reports a running count; tick(done, total) a known total, and a new total starts a new bar.
        """
        if self.view.progress == "off":
            return
        if self.bar_class is not None and self._update_bar(count, total):
            return
        self._update_line(count, total)

    def _update_bar(self, count: int, total: int | None) -> bool:
        """
        Moves the tqdm bar to `count`. False when tqdm fails, whatever it raises (an old tqdm, or a broken widget
        front end): the plain line takes over from then on.
        """
        try:
            if self.bar is not None and total != self.bar.total:
                self._close_bar()
            if self.bar is None:
                self._take_over()
                self.bar = _progress_bar(self.bar_class, self.label, self.unit, total)
            self.bar.update(count - self.bar.n)
            return True
        except Exception:  # noqa: BLE001
            self.bar_class = None
            return False

    def _update_line(self, count: int, total: int | None) -> None:
        now = time.monotonic()
        if total != self.total:
            self.started, self.total = now, total
        if now - self.started < _LINE_EVERY or now - self.shown < _LINE_EVERY:
            return
        self.shown = now
        self._take_over()
        text = _progress_text(self.label, self.unit, count, total, now - self.started)
        if self.notebook:
            self._show_in_notebook(text)
        else:
            self.width = max(self.width, len(text))
            self.view._progress_to_terminal(text.ljust(self.width))

    def _show_in_notebook(self, text: str) -> None:
        """
        The line in the cell's output, replacing the last one. Outside IPython display() returns no handle, and
        nothing shows.
        """
        from IPython.display import HTML, display

        if self.handle is None:
            self.handle = display(HTML(""), display_id=True)
        if self.handle is not None:
            self.handle.update(HTML(f'<div style="opacity:.6">{_esc(text)}</div>'))

    def _take_over(self) -> None:
        """
        Makes this the line the view shows, clearing the one that showed before.
        """
        owner = self.view._progress_owner
        if owner is not self:
            if owner is not None:
                owner.clear()
            self.view._progress_owner = self

    def _close_bar(self) -> None:
        """
        Closes the tqdm bar. One that finished early (a file it couldn't read) is marked done, so it isn't left as a
        red "failed" widget; one that was interrupted is left as it is.
        """
        if self.bar is None:
            return
        if not self.stopped and self.bar.total and self.bar.n < self.bar.total:
            self.bar.total = self.bar.n
        self.bar.close()
        self.bar = None

    def clear(self) -> None:
        """
        Takes the bar or the line away.
        """
        self._close_bar()
        if self.handle is not None:
            from IPython.display import HTML

            self.handle.update(HTML(""))
        elif self.width:
            self.view._progress_to_terminal(" " * self.width + "\r")
            self.width = 0


class _BaseView:
    """
    What every <Service>View starts with: the mode, max_rows and progress options, how a report is shown,
    progress while a long command runs, help(), and the hooks @_friendly_errors asks. The only command here is
    help(); every other one is the service's, decorated with @_friendly_errors, and renders a report and returns
    nothing.

    What a service's View sets:

        core          its analyzer: the View's __init__ makes one when it isn't given one
        _render_html  its look: its stylesheet, root class and badge
        _GROUPS       help() lists the commands in these groups, in this order
        _START        help()'s "Start here": (call, what it shows)
        _PROGRESS     the label and unit _progress() shows when it's given none
        _DATA_ERRORS  errors shown as notes ("ValueError: ..."), besides AWS's

    _render_text, how a report reads as text, is the same for every service, and _progress_owner is the progress
    line showing now (see _ProgressLine).
    """

    core: Any
    _render_html: Callable[[list[Any], int], str]
    _render_text = staticmethod(_render_text)
    _GROUPS: dict[str, tuple[str, ...]] = {}
    _START: tuple[tuple[str, str], ...] = ()
    _PROGRESS = ("Reading", "items read")
    _DATA_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError)
    _progress_owner: _ProgressLine | None = None

    def __init__(self, *, mode: str = "auto", max_rows: int = 50, progress: str = "auto"):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.progress = progress

    def _show(self, blocks: list[Any]) -> None:
        """
        Shows a report: as HTML in Jupyter, else as text. With mode='html' outside Jupyter (no IPython), it's shown
        as text with a note saying why, and from then on as text.
        """
        if self.use_html:
            try:
                from IPython.display import HTML, display
            except ImportError:
                self.use_html = False
                note = "mode='html' only works in Jupyter (IPython isn't installed here), so this is shown as text."
                blocks = [*blocks, _Note(note, "warn")]
            else:
                display(HTML(self._render_html(blocks, self.max_rows)))
                return
        print(self._render_text(blocks, self.max_rows))

    @staticmethod
    def _progress_to_terminal(text: str) -> None:
        """
        Rewrites the progress line in place on stderr, outside a notebook.
        """
        print("\r" + text, end="", file=sys.stderr, flush=True)

    @contextmanager
    def _progress(self, label: str | None = None,
                  unit: str | None = None) -> Generator[Callable[..., None], None, None]:
        """
        Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away. Without a label or a
        unit, it shows the View's _PROGRESS.
        """
        line = _ProgressLine(self, self._PROGRESS[0] if label is None else label,
                             self._PROGRESS[1] if unit is None else unit)
        try:
            yield line.tick
        except BaseException:
            line.stopped = True
            raise
        finally:
            line.clear()
            if self._progress_owner is line:
                self._progress_owner = None

    def _explain(self, code: str, message: str) -> str:
        """
        What an AWS error means, and what to do about it. The service's View says more where it can; here, AWS's
        message as it is.
        """
        return message

    def _explain_call(self, code: str, message: str, command: str, args: tuple, kwargs: dict) -> str:
        """
        What a command's AWS error means: _explain's, unless the View needs the command's arguments to say more
        (DynamoDB names the table that wasn't found, and the closest names).
        """
        return self._explain(code, message)

    def _extra_note(self, exc: Exception, command: str) -> _Note | None:
        """
        The note for an error of the service's own (OpenSearch's failed requests), or None to let it raise.
        """
        return None

    def _price_basis(self) -> str:
        """
        The prices a report's costs are worked out with: AWS's list prices, or the analyzer's prices=.
        """
        return _which_prices(self.core.prices, self.core._LIST_PRICES)

    def help(self, command: Any = None) -> None:
        """
        Every command, grouped by task; help('name') shows one command in full.
        """
        view = type(self).__name__
        commands = _commands(type(self))
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
            rows = [[f"{name}{_signature(commands[name])}", _about(commands[name])] for name in names
                    if name in commands]
            if rows:
                blocks.append(_Table(["Command", "What it shows"], rows, title=group, max_rows=0, code_cols=(0,)))
        zone = _tz_label()
        change = ("set_timezone('Asia/Kolkata') (from aws_analyzer) shows them in your own zone, set_timezone('local') "
                  "in this machine's." if zone == "UTC" else
                  "set_timezone('UTC') (from aws_analyzer) shows them in UTC again.")
        blocks.append(_Note(f"Times are in {zone}: {change}"))
        self._show(blocks)
