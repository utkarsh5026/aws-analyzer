"""
What DynamoDBExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..._kit.focus import Focus, FocusPart

from .frame import _EXPLORER_TABS

if TYPE_CHECKING:
    from .window import DynamoDBExplorer


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


class _FocusReads:
    """
    The explorer's `focus`, a part of DynamoDBExplorer.
    """

    @property
    def focus(self: DynamoDBExplorer) -> Focus:
        """
        What the window is showing, as data: kind 'overview', 'tables', 'items' or 'schema' (a tab with nothing
        picked), 'table' (a table on the Tables tab), 'item' (an item on the Items tab) or 'attribute' (an
        attribute's values on the Schema tab), with the table open, the tab, the index and key read, and what's
        picked.
        """
        parts = [FocusPart("table", self.table_name, self.table_name)] if self.table_name else []
        parts.append(_tab_part(self._tab))
        kind = self._tab
        if self._tab == "tables" and self.table_list.selected:
            kind = "table"
        read = self.item_read
        if self._tab == "items" and read is not None:
            if read.index:
                parts.append(FocusPart("index", read.index, f"index {read.index}"))
            if read.partition:
                parts.append(FocusPart("partition", read.partition, f"partition key {read.partition}"))
            if self.item_list.selected:
                kind = "item"
                parts.append(FocusPart("item", self.item_list.selected, f"item {int(self.item_list.selected) + 1}"))
        if self._tab == "schema" and self.attribute_list.selected:
            kind = "attribute"
            parts.append(FocusPart("attribute", self.attribute_list.selected, self.attribute_list.selected))
        return Focus("dynamodb", kind, self.core.region, parts=tuple(parts))
