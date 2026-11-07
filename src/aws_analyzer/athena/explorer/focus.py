"""
What AthenaExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..._kit.focus import Focus, FocusPart

from .frame import _EXPLORER_TABS, _SINCE_CHOICES

if TYPE_CHECKING:
    from .window import AthenaExplorer


_QUERY_TABS = ("queries", "costs", "failures")


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


class _FocusReads:
    """
    The explorer's `focus`, a part of AthenaExplorer.
    """

    @property
    def focus(self: AthenaExplorer) -> Focus:
        """
        What the window is showing, as data: kind 'overview', 'queries', 'costs', 'failures', 'catalog' or
        'crawlers' (a tab with nothing picked), 'query' (a query on the right of the Queries, Costs or Failures tab),
        'table' or 'crawler', with the tab, the time range, the workgroup and what's picked.
        """
        label = next(text for text, value in _SINCE_CHOICES if value == self.since).lower()
        parts = [_tab_part(self._tab), FocusPart("range", self.since, label)]
        if self.workgroup:
            parts.append(FocusPart("workgroup", self.workgroup, f"workgroup {self.workgroup}"))
        kind = self._tab
        picked = self._query_page(self._tab).selected if self._tab in _QUERY_TABS else ""
        if picked:
            parts.append(FocusPart("query_id", picked, f"query {picked[:8]}"))
            kind = "query"
        if self._tab == "catalog" and self.table_name:
            parts.append(FocusPart("table", self.table_name, self.table_name))
            kind = "table"
        if self._tab == "crawlers" and self.crawler_list.selected:
            parts.append(FocusPart("crawler", self.crawler_list.selected, f"crawler {self.crawler_list.selected}"))
            kind = "crawler"
        history = self.history
        return Focus("athena", kind, self.core.region, since=history.since if history else None,
                     until=history.until if history else None, parts=tuple(parts))
