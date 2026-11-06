"""
What OpenSearchExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from ..models import Collection, Domain
from .frame import _EXPLORER_TABS

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_CHIP_CHARS = 80


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


class _FocusReads:
    """
    The explorer's `focus`, a part of OpenSearchExplorer.
    """

    @property
    def focus(self: OpenSearchExplorer) -> Focus:
        """
        What the window is showing, as data: kind 'stores' (the Domains tab, or nothing open yet), 'store' (a
        domain's or collection's Overview), 'index', 'document' or 'search', with the domain or collection, the
        index, the tab, and the document or the search shown.
        """
        store = self.store
        if store is None or self._tab == "stores":
            return Focus("opensearch", "stores", self.core.region, parts=(_tab_part("stores"),))
        what = "domain" if isinstance(store, Domain) else "collection" if isinstance(store, Collection) else "endpoint"
        parts = [FocusPart(what, store.name, store.name)]
        kind = "store"
        if self.index_name and self._tab != "overview":
            parts.append(FocusPart("index", self.index_name, f"index {self.index_name}"))
            kind = "index"
        parts.append(_tab_part(self._tab))
        if self._tab == "documents" and self.doc is not None:
            doc_id = str(self.doc.get("_id", ""))
            parts.append(FocusPart("document", doc_id, f"document {_clip(doc_id, _CHIP_CHARS)}"))
            kind = "document"
        if self._tab == "search" and self.result is not None:
            asked = self.result.like or self.result.text or ""
            text = f"nearest to {asked}" if self.result.like else f"search: {asked}"
            parts.append(FocusPart("search", asked, _clip(text, _CHIP_CHARS)))
            kind = "search"
        return Focus("opensearch", kind, self.core.region, parts=tuple(parts))
