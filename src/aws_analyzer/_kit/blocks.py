"""The blocks a report is made of, and what draws each kind of block.

A View command builds a list of blocks (a _Title, _Cards, a _Table, ...) and hands it to the renderer, which draws
each block by what's registered for its type: its HTML, or its lines of text. These are the blocks every service
uses (render.py draws them); a service's own blocks (_Passage, _Steps, _Wiring, ...) are defined in its module and
register how they're drawn with @_draws when it's imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[tuple[str, ...]]  # (label, value), or (label, value, tone) with tone 'warn' | 'bad' | 'ok'


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]  # cells are text, or _Tone for a coloured status (or another type registered as a cell)
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap
    code_cols: tuple[int, ...] = ()  # columns holding calls to copy, shown as code
    prose_cols: tuple[int, ...] = ()  # columns of sentences this tool wrote (findings): calls in them shown as code
    collapsed: bool = False  # a secondary view: folded under its title in HTML


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""
    wrap: bool = False  # prose: wrap long lines instead of scrolling sideways
    code: bool = False  # a snippet to copy: in HTML one click selects all of it
    collapsed: bool = False  # a secondary view (raw JSON): folded under its title in HTML


@dataclass
class _Findings:
    items: list[tuple[str, str]]  # (level, message) pairs from a *_findings function
    empty: str = ""  # said (as an ok note) when there are none; nothing when blank


@dataclass
class _Next:
    items: list[tuple[str, str]]  # (call, what it shows): the commands worth running next, arguments filled in
    title: str = "Next"


@dataclass
class _Tone:
    """A table cell with a status colour: a pill in HTML, plain text elsewhere."""

    text: str
    tone: str = "warn"  # 'warn' | 'bad' | 'ok'

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class _Report:
    """One report being drawn: what each block's drawing function gets besides the block."""

    max_rows: int  # the view's cap on table rows; 0 = no cap
    badge: str = ""  # the chip before each title (HTML), so reports from different services are easy to tell apart


_HTML: dict[type, Callable[[Any, _Report], str]] = {}  # block type -> its HTML
_TEXT: dict[type, Callable[[Any, _Report], list[str]]] = {}  # block type -> its lines in text mode
_CELLS: dict[type, Callable[[Any, str], str]] = {}  # table cell type -> its HTML, given the cell's text


def _draws(kind: type, mode: str) -> Callable[[Callable], Callable]:
    """@_draws(_Passage, "html") makes the function under it the way a _Passage block is drawn in HTML:
    f(block, report) -> str. "text" registers its lines in text mode, f(block, report) -> list[str], and "cell" how
    a table cell of that type shows in HTML, f(cell, text) -> str (text mode shows str(cell))."""
    registry: dict[type, Callable] = {"html": _HTML, "text": _TEXT, "cell": _CELLS}[mode]

    def register(draw: Callable) -> Callable:
        registry[kind] = draw
        return draw

    return register


def _drawer(registry: dict[type, Callable], value: Any) -> Callable | None:
    """What draws value: the function registered for its type, or for the closest type it's made from."""
    return next((registry[cls] for cls in type(value).__mro__ if cls in registry), None)
