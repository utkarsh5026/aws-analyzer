"""
The explorer's Queries tab: every query run in the time picked (and the workgroup, when one is), with a search box
and chips (failed, costly, cancelled, running), and on the right the query picked as query() shows it: its SQL, how
it ended, what it scanned and cost, and the results it saved. Opening a query is shared with the Costs and Failures
tabs, which show their picked query's newest run the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import search_rank
from ..._kit.window import _skeleton

from ..models import Query, QueryDetail
from ..analysis import did_not_finish, query_cost
from .frame import _query_face
from .widgets import _Chip, _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import AthenaExplorer


COSTLY_RUN = 0.25

_QUERY_CHIPS = (
    _Chip("", "All", "all", "Every query"),
    _Chip("failed", "Failed", "bad", "The queries that failed, or that the scan limit stopped"),
    _Chip("costly", "Costly", "warn", f"The queries that cost ${COSTLY_RUN:.2f} or more"),
    _Chip("cancelled", "Cancelled", "warn", "The queries someone or something cancelled"),
    _Chip("running", "Running", "none", "The queries still queued or running"),
)

_RESULT_ROWS = 20


def _in_query_chip(query: Query, chip: str, prices: dict[str, Any]) -> bool:
    """
    Whether a query belongs under a chip ('' is every one).
    """
    if chip == "failed":
        return did_not_finish(query)
    if chip == "costly":
        return query_cost(query, prices) >= COSTLY_RUN
    if chip == "cancelled":
        return query.state == "CANCELLED"
    if chip == "running":
        return query.state in ("QUEUED", "RUNNING")
    return True


def _query_text(query: Query) -> tuple[str, ...]:
    """
    What the Queries tab's search box looks through: the SQL first, then the ID, workgroup, database and error.
    """
    return (query.sql, query.id, query.workgroup, query.database, query.error_message)


class _QueriesTab:
    """
    The Queries tab, a part of AthenaExplorer: query_list is its page; query_id is the query last opened on the tab
    shown (query_tab says which), and details the queries read so far, by ID.
    """

    def _queries_page(self: AthenaExplorer) -> Any:
        self.query_id, self.query_tab = "", ""
        self.details: dict[str, QueryDetail] = {}
        self.query_list = _ListPage(self, placeholder="Search: SQL, query ID, workgroup, database, error…",
                                    chips=_QUERY_CHIPS, on_click=self._clicked_query)
        self.query_list.redraw = self._draw_query_rows
        return self.query_list.page

    def _draw_query_rows(self: AthenaExplorer) -> None:
        page, prices = self.query_list, self.core.prices
        queries = self._queries_shown()
        page.draw_chips({chip.key: sum(1 for q in queries if _in_query_chip(q, chip.key, prices))
                         for chip in _QUERY_CHIPS})
        matching = [q for q in queries if _in_query_chip(q, page.chip, prices)]
        if page.query:
            matching = [q for q in matching if search_rank(page.query, _query_text(q)) is not None]
        words = page.query.split()
        lines = [_Shown(q.id, _query_face(q, cost=query_cost(q, prices), words=words)) for q in matching]
        empty = "No query matches the search." if page.query or page.chip else "No queries in this time."
        page.fill(lines, total=len(queries), empty=empty, noun="queries")

    def _clicked_query(self: AthenaExplorer, line: _Line) -> None:
        if line.key:
            self._open_query(line.key, "queries")

    def _query_page(self: AthenaExplorer, tab: str) -> _ListPage:
        return {"queries": self.query_list, "costs": self.cost_list, "failures": self.failure_list}[tab]

    def _open_query(self: AthenaExplorer, query_id: str, tab: str) -> None:
        """
        Shows a query on the right of a tab: at once when it was read before, else read in the background (its
        statistics and the first rows of its saved results too).
        """
        page = self._query_page(tab)
        page.mark(query_id)
        if tab == self._tab or not self.query_id:
            self.query_id, self.query_tab = query_id, tab
        if query_id in self.details:
            self._draw_query(self.details[query_id], tab)
            return
        job = f"query-{tab}"
        self._set(page.view, _skeleton(f"Reading query {query_id[:8]}…"))
        if tab == self._tab:
            self._status(f"Reading query {query_id[:8]}…", busy=True, owner=job)
        self._later(job, lambda: self.core.query(query_id, rows=_RESULT_ROWS),
                    lambda detail: self._got_query(detail, tab), lambda exc: self._query_failed(exc, tab))

    def _got_query(self: AthenaExplorer, detail: QueryDetail, tab: str) -> None:
        """
        A query read for a tab: drawn there while it's still the one picked on that tab (each tab reads its own,
        so the Costs and Failures tabs' first queries don't drop each other).
        """
        self.details[detail.query.id] = detail
        if detail.query.id == self._query_page(tab).selected:
            self._draw_query(detail, tab)
        self._finish(f"query-{tab}", f"Query {detail.query.id[:8]}: {detail.query.state.lower()}", tabs=(tab,))

    def _draw_query(self: AthenaExplorer, detail: QueryDetail, tab: str) -> None:
        self._draw(tab, self._query_page(tab).view, self.ui._query_blocks(detail))

    def _query_failed(self: AthenaExplorer, exc: BaseException, tab: str) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._draw(tab, self._query_page(tab).view, [_Note(text, "warn")])
