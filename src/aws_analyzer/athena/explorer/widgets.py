"""
The explorer window's widgets: a reusable line of a list, and the list page every tab is made of (a search box,
chips, the lines on the left, and a report on the right).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..._kit.text import _esc
from ..._kit.window import _class_if, _row_button

if TYPE_CHECKING:
    from .window import AthenaExplorer


_ROWS_SHOWN = 80


class _Line:
    """
    One reusable line of a list: a full-width button under its face, so the whole line is the click target. key is
    what the line stands for (a query ID, a table's 'database.table', a crawler's name), set when it's drawn.
    """

    def __init__(self, app: AthenaExplorer, on_click: Callable[[_Line], None]):
        self.key = ""
        self.box, self.button, self.face = _row_button(app._w, ("atx-row", "atx-row-b", "atx-row-t"),
                                                       app._safely(lambda _button: on_click(self)))


@dataclass(frozen=True)
class _Chip:
    """
    A chip over a list: its key ('' for every line), its label, its colour ('all', 'bad', 'warn', 'ok', 'none') and
    what it shows, for its tooltip.
    """

    key: str
    label: str
    tone: str
    tip: str


@dataclass
class _Shown:
    """
    One line a list page draws: what it stands for and its face.
    """

    key: str
    face: str


class _ListPage:
    """
    A tab's page: a search box and chips over the lines on the left, and the picked line's report on the right
    (view). query is the search box's text and chip the chip picked; redraw draws the lines again (the tab sets it),
    and on_click is what a click on a line does.
    """

    def __init__(self, app: AthenaExplorer, *, placeholder: str, chips: tuple[_Chip, ...],
                 on_click: Callable[[_Line], None]):
        w, layout = app._w, app._w.Layout
        self.app, self.chips, self.on_click = app, chips, on_click
        self.query, self.chip, self.selected = "", "", ""
        self.redraw: Callable[[], None] = lambda: None
        self.rows: list[_Line] = []
        self.keys: list[str] = []
        self.find = w.Text(placeholder=placeholder, continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.find.add_class("atx-find")
        self.find.observe(app._safely(self._typed), names="value")
        finder = w.HBox([self.find], layout=layout(width="100%", align_items="center"))
        finder.add_class("atx-filters")
        self.chips_box = w.HBox(layout=layout(width="100%"))
        self.chips_box.add_class("atx-chips")
        self.rows_box = w.VBox(layout=layout(width="100%"))
        self.rows_box.add_class("atx-rows")
        self.count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.count], layout=layout(width="100%"))
        count.add_class("atx-pager")
        left = w.VBox([finder, self.chips_box, self.rows_box, count])
        left.add_class("atx-left")
        self.view = w.HTML(layout=layout(width="100%"))
        self.right = w.VBox([self.view])
        self.right.add_class("atx-right")
        self.page = w.HBox([left, self.right])
        self.page.add_class("atx-split")

    def _typed(self, change: dict[str, Any]) -> None:
        self.query = str(change["new"] or "").strip()
        self.redraw()

    def _pick_chip(self, key: str) -> None:
        self.chip = "" if key == self.chip else key
        self.redraw()

    def draw_chips(self, counts: dict[str, int]) -> None:
        """
        A chip per key with lines under it, with how many; a click shows only those (again shows all).
        """
        w, layout = self.app._w, self.app._w.Layout
        buttons = []
        for chip in self.chips:
            count = counts.get(chip.key, 0)
            if chip.key and not count:
                continue
            tip = chip.tip + (" (click again for all)" if chip.key else "")
            button = w.Button(description=f"{chip.label} {count:,}", tooltip=tip, layout=layout(width="auto"))
            for name in ("atx-chip", f"atx-t-{chip.tone}"):
                button.add_class(name)
            _class_if(button, "atx-on", chip.key == self.chip)
            button.on_click(self.app._safely(lambda _button, key=chip.key: self._pick_chip(key)))
            buttons.append(button)
        self.chips_box.children = buttons

    def fill(self, lines: list[_Shown], *, total: int, empty: str, noun: str) -> None:
        """
        The lines, the first _ROWS_SHOWN of them (rows are pooled and reused), the one picked marked, and under them
        how many there are of the total; empty is what to say when there are none.
        """
        shown = lines[:_ROWS_SHOWN]
        while len(self.rows) < len(shown):
            self.rows.append(_Line(self.app, self.on_click))
        for row, line in zip(self.rows, shown):
            row.key = line.key
            self.app._set(row.face, line.face)
            _class_if(row.box, "atx-on", line.key == self.selected)
        self.keys = [line.key for line in shown]
        children: list[Any] = [row.box for row in self.rows[:len(shown)]]
        if not children:
            children = [self.app._w.HTML(f'<div class="atx-empty">{_esc(empty)}</div>')]
        self.rows_box.children = children
        self.app._set(self.count, _esc(_count_text(len(lines), len(shown), total, noun)) if total else "")

    def mark(self, key: str) -> None:
        """
        Marks the line picked (its report is on the right).
        """
        self.selected = key
        for row in self.rows:
            _class_if(row.box, "atx-on", bool(row.key) and row.key == key)


def _count_text(matching: int, shown: int, total: int, noun: str) -> str:
    """
    What the line under a list says: '412 queries', '12 of 412 queries', or '80 of 412 shown: search or pick a chip'.
    """
    if shown < matching:
        return f"{shown:,} of {matching:,} {noun} shown: search or pick a chip to narrow them"
    return f"{total:,} {noun}" if matching == total else f"{matching:,} of {total:,} {noun}"
