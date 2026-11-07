"""
The explorer's Costs and Failures tabs. Costs: the same query's runs added up (QueryShape), costliest first, with
chips for queries that run again and again and those that read every column; Failures: the failed queries grouped by
kind, the most common first, each with what it means. A click on either shows the newest run on the right, as the
Queries tab shows a query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import search_rank

from ..models import FailureGroup, QueryShape
from ..analysis import group_failures, query_shapes, total_cost
from .frame import _failure_face, _shape_face
from .widgets import _Chip, _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import AthenaExplorer


_REPEATED = 3

_COST_CHIPS = (
    _Chip("", "All", "all", "Every kind of query that cost something"),
    _Chip("repeated", "Run again and again", "warn", f"Queries that ran {_REPEATED} times or more"),
    _Chip("star", "SELECT *", "warn", "Queries that read every column"),
)

_FAILURE_CHIPS = (_Chip("", "All", "all", "Every kind of failure"),)


def _in_cost_chip(shape: QueryShape, chip: str) -> bool:
    if chip == "repeated":
        return shape.runs >= _REPEATED
    if chip == "star":
        return shape.select_star
    return True


def _failure_text(group: FailureGroup) -> tuple[str, ...]:
    return (group.kind, group.meaning, group.message, group.fix)


class _CostsTab:
    """
    The Costs tab, a part of AthenaExplorer: cost_list is its page.
    """

    def _costs_page(self: AthenaExplorer) -> Any:
        self.cost_list = _ListPage(self, placeholder="Search the SQL…", chips=_COST_CHIPS,
                                   on_click=self._clicked_shape)
        self.cost_list.redraw = self._draw_cost_rows
        return self.cost_list.page

    def _shapes(self: AthenaExplorer) -> list[QueryShape]:
        return [s for s in query_shapes(self._queries_shown(), self.core.prices) if s.cost > 0]

    def _draw_cost_rows(self: AthenaExplorer) -> None:
        page = self.cost_list
        shapes = self._shapes()
        total = total_cost(self._queries_shown(), self.core.prices)
        page.draw_chips({chip.key: sum(1 for s in shapes if _in_cost_chip(s, chip.key)) for chip in _COST_CHIPS})
        matching = [s for s in shapes if _in_cost_chip(s, page.chip)]
        if page.query:
            matching = [s for s in matching if search_rank(page.query, (s.sql,)) is not None]
        words = page.query.split()
        lines = [_Shown(s.last_id, _shape_face(s, share=s.cost / total if total else 0.0, words=words))
                 for s in matching]
        empty = "No query matches the search." if page.query or page.chip else (
            "No query in this time cost anything: failed queries and DDL are free.")
        page.fill(lines, total=len(shapes), empty=empty, noun="kinds of query")
        self._open_first("costs", page)

    def _clicked_shape(self: AthenaExplorer, line: _Line) -> None:
        if line.key:
            self._open_query(line.key, "costs")

    def _open_first(self: AthenaExplorer, tab: str, page: _ListPage) -> None:
        """
        Shows the first line's query on a tab's right when nothing there is picked yet (typing in the search box
        leaves the one picked), or says there's nothing to show.
        """
        if page.selected:
            return
        if page.keys:
            self._open_query(page.keys[0], tab)
            return
        self._draw(tab, page.view, [_Note("Nothing to show here for the time and workgroup picked.")])


class _FailuresTab:
    """
    The Failures tab, a part of AthenaExplorer: failure_list is its page.
    """

    def _failures_page(self: AthenaExplorer) -> Any:
        self.failure_list = _ListPage(self, placeholder="Search: kind, message…", chips=_FAILURE_CHIPS,
                                      on_click=self._clicked_failure)
        self.failure_list.redraw = self._draw_failure_rows
        return self.failure_list.page

    def _draw_failure_rows(self: AthenaExplorer) -> None:
        page = self.failure_list
        groups = group_failures(self._queries_shown())
        page.draw_chips({"": len(groups)})
        matching = groups
        if page.query:
            matching = [g for g in groups if search_rank(page.query, _failure_text(g)) is not None]
        words = page.query.split()
        lines = [_Shown(g.last_id, _failure_face(g, words=words)) for g in matching]
        empty = "No failure matches the search." if page.query else "No query failed in this time."
        page.fill(lines, total=len(groups), empty=empty, noun="kinds of failure")
        self._open_first("failures", page)

    def _clicked_failure(self: AthenaExplorer, line: _Line) -> None:
        if line.key:
            self._open_query(line.key, "failures")
