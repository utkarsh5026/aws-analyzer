"""
The blocks a report is made of, and what draws each kind of block.

A View command builds a list of blocks (a _Title, _Cards, a _Table, ...) and hands it to the renderer, which draws
each block by what's registered for its type: its HTML, or its lines of text. These are the blocks every service
uses (render.py draws them); a service's own blocks (_Passage, _Steps, _Wiring, ...) are defined in its module and
register how they're drawn with @_draws when it's imported.

The registries @_draws fills: _HTML (block type -> its HTML), _TEXT (block type -> its lines in text mode) and
_CELLS (table cell type -> its HTML, given the cell's text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _Title:
    """
    The report's title, with a line under it (sub).
    """

    text: str
    sub: str = ""


@dataclass
class _Cards:
    """
    The few numbers that matter, as cards. Each item is (label, value), or (label, value, tone) with tone 'warn',
    'bad' or 'ok'.
    """

    items: list[tuple[str, ...]]


@dataclass
class _Table:
    """
    A table of rows under its headers.

        rows        cells are text, or _Tone for a coloured status (or another type registered as a cell)
        bars        0..1 per row, drawn as an extra column headed bar_label
        tree        the first column holds indented tree labels
        max_rows    None for the view's default, 0 for no cap
        code_cols   columns holding calls to copy, shown as code
        prose_cols  columns of sentences this tool wrote (findings): the calls in them shown as code
        collapsed   a secondary view: folded under its title in HTML
        path_cols   columns of keys and paths: the file name always shows, a long folder is shortened
        sortable    in HTML a click on a column's header sorts by it (tree tables never sort)
    """

    headers: list[str]
    rows: list[list[Any]]
    title: str = ""
    bars: list[float] | None = None
    bar_label: str = "Share"
    tree: bool = False
    max_rows: int | None = None
    code_cols: tuple[int, ...] = ()
    prose_cols: tuple[int, ...] = ()
    collapsed: bool = False
    path_cols: tuple[int, ...] = ()
    sortable: bool = True


@dataclass
class _Note:
    """
    A sentence on its own, at a level: 'info', 'warn' or 'ok'.
    """

    text: str
    level: str = "info"


@dataclass
class _Text:
    """
    Text shown as it is.

        wrap       prose: wrap long lines instead of scrolling sideways
        code       a snippet to copy: in HTML one click selects all of it
        collapsed  a secondary view (raw JSON): folded under its title in HTML
    """

    text: str
    title: str = ""
    wrap: bool = False
    code: bool = False
    collapsed: bool = False


@dataclass
class _Findings:
    """
    The (level, message) pairs from a *_findings function, and what to say (as an ok note) when there are none;
    nothing when `empty` is blank.
    """

    items: list[tuple[str, str]]
    empty: str = ""


@dataclass
class _Next:
    """
    The commands worth running next, as (call, what it shows), with their arguments filled in.
    """

    items: list[tuple[str, str]]
    title: str = "Next"


@dataclass
class _Tone:
    """
    A table cell with a status colour (tone: 'warn', 'bad' or 'ok'): a pill in HTML, plain text elsewhere.
    """

    text: str
    tone: str = "warn"

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class _Report:
    """
    One report being drawn: what each block's drawing function gets besides the block.

        max_rows  the view's cap on table rows; 0 for no cap
        badge     the chip before each title (HTML), so reports from different services are easy to tell apart
        root      the service's root class, which scopes the CSS rules
        blocks    the whole report, for a block that needs to know what else is in it
        rules     CSS rules the blocks need beyond the stylesheet (a table's filters and columns): a <style> after it
        parts     what blocks share while the report is drawn (see shared)
    """

    max_rows: int
    badge: str = ""
    root: str = ""
    blocks: tuple[Any, ...] = ()
    rules: set[str] = field(default_factory=set)
    parts: dict[Any, Any] = field(default_factory=dict)

    def shared(self, key: Any, make: Callable[[], Any]) -> Any:
        """
        What the report's blocks share while it's drawn, made by make() the first time one asks for it: S3's PDF
        pages share one full-size view, so its ‹ › step through every page in the report.
        """
        if key not in self.parts:
            self.parts[key] = make()
        return self.parts[key]


_HTML: dict[type, Callable[[Any, _Report], str]] = {}
_TEXT: dict[type, Callable[[Any, _Report], list[str]]] = {}
_CELLS: dict[type, Callable[[Any, str], str]] = {}


def _draws(kind: type, mode: str) -> Callable[[Callable], Callable]:
    """
    @_draws(_Passage, "html") makes the function under it the way a _Passage block is drawn in HTML:
    f(block, report) -> str. "text" registers its lines in text mode, f(block, report) -> list[str], and "cell" how
    a table cell of that type shows in HTML, f(cell, text) -> str (text mode shows str(cell)).
    """
    registry: dict[type, Callable] = {"html": _HTML, "text": _TEXT, "cell": _CELLS}[mode]

    def register(draw: Callable) -> Callable:
        registry[kind] = draw
        return draw

    return register


def _drawer(registry: dict[type, Callable], value: Any) -> Callable | None:
    """
    What draws value: the function registered for its type, or for the closest type it's made from.
    """
    return next((registry[cls] for cls in type(value).__mro__ if cls in registry), None)
