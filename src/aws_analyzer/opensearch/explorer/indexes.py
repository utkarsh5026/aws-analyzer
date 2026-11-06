"""
The explorer's Indexes tab: the domain's or collection's indexes on the left (vector indexes first, each with its
first vector field, its documents and size, and a dot for its health or warnings), and on the right the index
picked as index_info() shows it: its vector fields, the memory they need, findings and the query to copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if, _skeleton

from ..models import Domain, IndexInfo
from ..analysis.findings import index_findings
from .frame import _index_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_INDEX_CHIPS = (
    ("", "All", "all"),
    ("vector", "Vector", "none"),
    ("warn", "With warnings", "warn"),
    ("unhealthy", "Not green", "bad"),
)

_CHIP_TIPS = {
    "": "Show every index",
    "vector": "Show only the indexes with a vector (knn_vector) field",
    "warn": "Show only the indexes with a warning",
    "unhealthy": "Show only the indexes whose health is yellow or red",
}


def _index_text(info: IndexInfo) -> tuple[str, ...]:
    """
    What the Indexes tab's search box looks through: the name first, then its aliases and vector fields.
    """
    vectors = " ".join(f"{vf.path} {vf.engine or ''} {vf.space}" for vf in info.vectors)
    return (info.name, " ".join(info.aliases), vectors)


class _IndexesTab:
    """
    The Indexes tab, a part of OpenSearchExplorer: what it keeps is `index_query` (the search box's text),
    `index_chip`, and `indexes_shown`, the indexes in the order drawn.
    """

    def _indexes_page(self: OpenSearchExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.index_query, self.index_chip = "", ""
        self.indexes_shown: list[IndexInfo] = []
        self._index_rows: list[_Line] = []
        self.index_find = w.Text(placeholder="Filter indexes: name, alias, vector field…", continuous_update=True,
                                 layout=layout(flex="1 1 auto", width="auto"))
        self.index_find.add_class("osx-find")
        self.index_find.observe(self._safely(self._index_typed), names="value")
        finder = w.HBox([self.index_find], layout=layout(width="100%", align_items="center"))
        finder.add_class("osx-filters")
        self.index_chips = w.HBox(layout=layout(width="100%"))
        self.index_chips.add_class("osx-chips")
        self.index_rows = w.VBox(layout=layout(width="100%"))
        self.index_rows.add_class("osx-rows")
        self.index_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.index_count], layout=layout(width="100%"))
        count.add_class("osx-pager")
        left = w.VBox([finder, self.index_chips, self.index_rows, count])
        for name in ("osx-left", "osx-indexes"):
            left.add_class(name)
        self.index_view = w.HTML(layout=layout(width="100%"))
        right = w.VBox([self.index_view])
        right.add_class("osx-right")
        split = w.HBox([left, right])
        split.add_class("osx-split")
        return split

    def _index_warnings(self: OpenSearchExplorer, info: IndexInfo) -> int:
        """
        How many warnings indexes() gives an index (its line's dot and count).
        """
        store = self.store
        is_domain = isinstance(store, Domain)
        data_nodes = store.instance_count if is_domain else (
            self.report.health.data_nodes if self.report and self.report.health else None)
        found = index_findings(info, store, version=store.version if is_domain else None, data_nodes=data_nodes)
        return sum(1 for level, _ in found if level == "warn")

    def _in_index_chip(self: OpenSearchExplorer, info: IndexInfo, chip: str) -> bool:
        if chip == "vector":
            return bool(info.vectors)
        if chip == "warn":
            return self._index_warnings(info) > 0
        if chip == "unhealthy":
            return (info.health or "green") != "green"
        return True

    def _all_indexes(self: OpenSearchExplorer) -> list[IndexInfo]:
        """
        The store's indexes, vector indexes first, then A to Z.
        """
        indexes = self.report.indexes if self.report else []
        return sorted(indexes, key=lambda i: (not i.vectors, i.name))

    def _first_index(self: OpenSearchExplorer) -> str:
        """
        The index to show first: the vector index with the most warnings, else the biggest vector index, else the
        first index.
        """
        indexes = self._all_indexes()
        vectors = [i for i in indexes if i.vectors]
        if not vectors:
            return indexes[0].name if indexes else ""
        flagged = max(vectors, key=lambda i: (self._index_warnings(i), i.documents or 0))
        return flagged.name

    def _index_typed(self: OpenSearchExplorer, change: dict[str, Any]) -> None:
        self.index_query = str(change["new"] or "").strip()
        self._draw_index_rows()

    def _pick_index_chip(self: OpenSearchExplorer, chip: str) -> None:
        self.index_chip = "" if chip == self.index_chip else chip
        self._draw_index_chips()
        self._draw_index_rows()

    def _draw_index_chips(self: OpenSearchExplorer) -> None:
        w, layout = self._w, self._w.Layout
        indexes = self._all_indexes()
        chips = []
        for key, label, tone in _INDEX_CHIPS if indexes else ():
            count = sum(1 for info in indexes if self._in_index_chip(info, key))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=_CHIP_TIPS[key]
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("osx-chip", f"osx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "osx-on", key == self.index_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_index_chip(key)))
            chips.append(chip)
        self.index_chips.children = chips

    def _draw_index_rows(self: OpenSearchExplorer) -> None:
        indexes = [i for i in self._all_indexes() if self._in_index_chip(i, self.index_chip)]
        if self.index_query:
            indexes = [i for i in indexes if search_rank(self.index_query, _index_text(i)) is not None]
        self.indexes_shown = indexes
        while len(self._index_rows) < len(indexes):
            self._index_rows.append(_Line(self, self._clicked_index))
        words = self.index_query.split()
        for row, info in zip(self._index_rows, indexes):
            row.key = info.name
            self._set(row.face, _index_face(info, warnings=self._index_warnings(info), words=words))
            _class_if(row.box, "osx-on", info.name == self.index_name)
        children: list[Any] = [row.box for row in self._index_rows[:len(indexes)]]
        if not children:
            text = ("No index matches the filter." if self.index_query or self.index_chip
                    else "No indexes here yet.")
            children = [self._w.HTML(f'<div class="osx-empty">{_esc(text)}</div>')]
        self.index_rows.children = children
        total = len(self._all_indexes())
        shown = f"{len(indexes):,} of {total:,} indexes" if len(indexes) != total else f"{total:,} indexes"
        self._set(self.index_count, _esc(shown) if total else "")

    def _draw_indexes(self: OpenSearchExplorer) -> None:
        """
        The Indexes tab, drawn anew for the store shown: the chips, the lines, and the index picked.
        """
        self._draw_index_chips()
        self._draw_index_rows()
        if not self._all_indexes():
            self._draw("indexes", self.index_view, [_Note(
                f"{self.store.label if self.store else 'This'} has no indexes yet, so there's nothing to look at.")])

    def _draw_index(self: OpenSearchExplorer) -> None:
        if self.info is None:
            self._set(self.index_view, _skeleton(f"Reading {self.index_name}…"))
            return
        self._draw("indexes", self.index_view, self.ui._index_info_blocks(self.store, self.info, self.knn,
                                                                         self.knn_problem))

    def _clicked_index(self: OpenSearchExplorer, line: _Line) -> None:
        if line.key and line.key != self.index_name:
            self._open_index(line.key)
