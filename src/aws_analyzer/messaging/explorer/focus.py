"""
What MessagingExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..._kit.focus import Focus, FocusPart

from ..helpers import arn_name
from .frame import _EXPLORER_TABS

if TYPE_CHECKING:
    from .window import MessagingExplorer


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


class _FocusReads:
    """
    The explorer's `focus`, a part of MessagingExplorer.
    """

    @property
    def focus(self: MessagingExplorer) -> Focus:
        """
        What the window is showing, as data: kind 'region' (the Everything or Dead letters tab, or nothing open yet),
        'queue', 'topic' or 'rule' (its Overview or Map), or 'live' (the queues Live watches), with the tab.
        """
        if self._tab in ("everything", "dead") or not self.item_key:
            return Focus("messaging", "region", self.core.region, parts=(_tab_part(self._tab),))
        if self._tab == "live":
            parts = [FocusPart("queue", q.arn, q.name) for q in self.watched]
            return Focus("messaging", "live", self.core.region, parts=(*parts, _tab_part("live")))
        kind, _, arn = self.item_key.partition(":")
        name = arn_name(arn)
        return Focus("messaging", kind, self.core.region,
                     parts=(FocusPart(kind, arn, f"{kind} {name}"), _tab_part(self._tab)))
