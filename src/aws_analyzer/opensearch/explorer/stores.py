"""
The explorer's Domains tab: the region's cards, then a line per OpenSearch domain and Serverless collection with a
search box, a chip per kind (with warnings, domains, collections, vector collections, not active) and columns that
sort by a click, then Serverless capacity and the warnings, as the API Gateway explorer's APIs tab does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Next, _Note, _Table
from ..._kit.fmt import _total
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..models import Collection, Domain
from ..analysis.cost import domain_monthly_cost
from ..analysis.findings import collection_findings, domain_findings
from ..analysis.vectors import domain_knn_memory
from ..view import _state_tone
from .frame import _store_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_STORE_COLUMNS = (
    ("name", "Domain or collection", "Sort by name"),
    ("status", "Status", "Sort by status: the ones not active first"),
    ("memory", "k-NN memory", "Sort by the memory a domain's data nodes have for vector graphs"),
    ("cost", "$ / month", "Sort by a domain's estimated monthly cost"),
    ("warnings", "Warnings", "Sort by how many warnings it has"),
)

_STORE_CHIPS = (
    ("", "All", "all"),
    ("warn", "With warnings", "warn"),
    ("domain", "Domains", "none"),
    ("collection", "Collections", "none"),
    ("vector", "Vector collections", "none"),
    ("changing", "Not active", "bad"),
)

_CHIP_TIPS = {
    "": "Show every domain and collection",
    "warn": "Show only the ones with a warning",
    "domain": "Show only the OpenSearch Service domains",
    "collection": "Show only the Serverless collections",
    "vector": "Show only the Serverless collections made for vector search",
    "changing": "Show only the ones that aren't active: creating, changing, failed or being deleted",
}

_ASCENDING_FIRST = ("name",)

_DEFAULT_SORT = "warnings"

_NO_VALUE = -1.0

_STORE_TABLES = ("Domains (", "Serverless collections")


@dataclass
class _StoreLine:
    """
    What a domain's or collection's line is drawn and sorted from: the store, its warnings, the memory its data
    nodes have for vectors (domains only) and its estimated monthly cost (domains only; a collection's is shared
    with the others of its group).
    """

    store: Domain | Collection
    warnings: int
    knn_memory: int | None
    cost: float | None


def _in_chip(line: _StoreLine, chip: str) -> bool:
    """
    Whether a store's line belongs under a chip ('' is every one).
    """
    store = line.store
    if chip == "warn":
        return line.warnings > 0
    if chip == "domain":
        return isinstance(store, Domain)
    if chip == "collection":
        return isinstance(store, Collection)
    if chip == "vector":
        return isinstance(store, Collection) and store.vector
    if chip == "changing":
        return bool(_state_tone(store.status))
    return True


def _column_value(line: _StoreLine, column: str) -> tuple[Any, ...]:
    """
    What a column sorts a line by; a value a store hasn't got sorts below every one that has it.
    """
    if column == "name":
        return (line.store.name.lower(),)
    if column == "status":
        return (1 if _state_tone(line.store.status) else 0, line.store.status.lower())
    if column == "memory":
        return (float(line.knn_memory) if line.knn_memory else _NO_VALUE,)
    if column == "cost":
        return (line.cost if line.cost is not None else _NO_VALUE,)
    return (float(line.warnings), float(line.knn_memory or 0))


def _searched_text(store: Domain | Collection) -> tuple[str, ...]:
    """
    What the search box looks through: the name first, then the engine and instances, or the collection's ID,
    kind and description.
    """
    if isinstance(store, Domain):
        return (store.name, f"{store.engine} {store.version}", store.instance_type, store.vpc or "")
    return (store.name, store.id, store.kind, store.description)


def _split_overview(blocks: list[Any]) -> tuple[list[Any], list[Any]]:
    """
    overview()'s blocks split around the list the tab draws in place of its tables of domains and collections: what
    goes above it (the title, cards and notes) and below it (Serverless capacity and the warnings). The next steps
    are left out: the list is what to click next.
    """
    head: list[Any] = []
    tail: list[Any] = []
    for block in blocks:
        if isinstance(block, _Next):
            continue
        is_store_table = isinstance(block, _Table) and str(block.title or "").startswith(_STORE_TABLES)
        if is_store_table:
            continue
        if tail or isinstance(block, _Table):
            tail.append(block)
        else:
            head.append(block)
    return head, tail


class _StoresTab:
    """
    The Domains tab, a part of OpenSearchExplorer: what it keeps is `store_query` (the search box's text),
    `store_chip`, `store_sort` and `store_descending`, and `stores_shown`, the lines in the order drawn.
    """

    def _stores_page(self: OpenSearchExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.store_query, self.store_chip = "", ""
        self.store_sort, self.store_descending = _DEFAULT_SORT, True
        self.stores_shown: list[_StoreLine] = []
        self._store_rows: list[_Line] = []
        self.stores_view = w.HTML(layout=layout(width="100%"))
        self.store_find = w.Text(placeholder="Search: name, collection ID, engine, instance type…",
                                 continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.store_find.add_class("osx-find")
        self.store_find.observe(self._safely(self._store_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("osx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.store_find, "value", "")))
        finder = w.HBox([self.store_find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("osx-filters")
        self.store_chips = w.HBox(layout=layout(width="100%"))
        self.store_chips.add_class("osx-chips")
        self.store_head = w.HBox([w.HTML(), *self._column_buttons()], layout=layout(width="100%"))
        self.store_head.add_class("osx-lhead")
        self.stores_box = w.VBox([self.store_head], layout=layout(width="100%"))
        self.stores_box.add_class("osx-machines")
        self.store_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.store_count], layout=layout(width="100%"))
        count.add_class("osx-pager")
        self.stores_tail = w.HTML(layout=layout(width="100%"))
        return w.VBox([self.stores_view, finder, self.store_chips, self.stores_box, count, self.stores_tail])

    def _column_buttons(self: OpenSearchExplorer) -> list[Any]:
        w, layout = self._w, self._w.Layout
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip in _STORE_COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            for name in ("osx-col", f"osx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_stores_by(key)))
            self.column_buttons[key] = button
        return list(self.column_buttons.values())

    def _warning_count(self: OpenSearchExplorer, store: Domain | Collection) -> int:
        """
        How many warnings overview() gives a domain or collection (the Warnings column, the dots).
        """
        if isinstance(store, Domain):
            if "describe" in store.errors:
                return 0
            found = domain_findings(store, self.core.prices)
        else:
            found = collection_findings(store)
        return sum(1 for level, _ in found if level == "warn")

    def _store_lines(self: OpenSearchExplorer) -> list[_StoreLine]:
        overview = self.overview
        if overview is None:
            return []
        lines = []
        for store in [*overview.domains, *overview.collections]:
            is_domain = isinstance(store, Domain) and "describe" not in store.errors
            memory = domain_knn_memory(store) if is_domain else None
            cost = _total(domain_monthly_cost(store, self.core.prices)) if is_domain else None
            lines.append(_StoreLine(store, self._warning_count(store), memory, cost))
        return lines

    def _store_typed(self: OpenSearchExplorer, change: dict[str, Any]) -> None:
        self.store_query = str(change["new"] or "").strip()
        self._draw_store_rows()

    def _pick_store_chip(self: OpenSearchExplorer, chip: str) -> None:
        self.store_chip = "" if chip == self.store_chip else chip
        self._draw_store_chips()
        self._draw_store_rows()

    def _sort_stores_by(self: OpenSearchExplorer, column: str) -> None:
        if column == self.store_sort:
            self.store_descending = not self.store_descending
        else:
            self.store_sort, self.store_descending = column, column not in _ASCENDING_FIRST
        self._draw_store_rows()

    def _draw_stores(self: OpenSearchExplorer) -> None:
        """
        The Domains tab: the region's cards, then the chips and the lines, searched, filtered and sorted, then
        Serverless capacity and the warnings.
        """
        overview = self.overview
        if overview is None:
            return
        blocks = self.ui._overview_blocks(overview)
        head, tail = _split_overview(blocks)
        self.shown["stores"] = head + tail
        self._set(self.stores_view, self._html(head))
        self._set(self.stores_tail, self._html(tail) if tail else "")
        self._draw_store_chips()
        self._draw_store_rows()

    def _draw_store_chips(self: OpenSearchExplorer) -> None:
        """
        A chip per kind of store there is, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        lines = self._store_lines()
        chips = []
        for key, label, tone in _STORE_CHIPS if lines else ():
            count = sum(1 for line in lines if _in_chip(line, key))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=f"{_CHIP_TIPS[key]}"
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("osx-chip", f"osx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "osx-on", key == self.store_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_store_chip(key)))
            chips.append(chip)
        self.store_chips.children = chips

    def _shown_store_lines(self: OpenSearchExplorer) -> list[_StoreLine]:
        """
        The lines the search and the chip leave, in the column's order (by name first, so equal values stay A to Z).
        """
        lines = [line for line in self._store_lines() if _in_chip(line, self.store_chip)]
        if self.store_query:
            lines = [line for line in lines if search_rank(self.store_query, _searched_text(line.store)) is not None]
        lines.sort(key=lambda line: line.store.name.lower())
        lines.sort(key=lambda line: _column_value(line, self.store_sort), reverse=self.store_descending)
        return lines

    def _draw_store_rows(self: OpenSearchExplorer) -> None:
        lines = self._shown_store_lines()
        self.stores_shown = lines
        self._draw_column_heads()
        while len(self._store_rows) < len(lines):
            self._store_rows.append(_Line(self, self._clicked_store))
        words = self.store_query.split()
        shown_name = self.store.name if self.store is not None else ""
        for row, line in zip(self._store_rows, lines):
            row.key = line.store.name
            self._set(row.face, _store_face(line.store, warnings=line.warnings, knn_memory=line.knn_memory,
                                            cost=line.cost, words=words))
            _class_if(row.box, "osx-on", line.store.name == shown_name)
        rows = [row.box for row in self._store_rows[:len(lines)]]
        self.stores_box.children = [self.store_head, *rows, *self._nothing_shown(lines)]
        self._draw_store_count(lines)

    def _draw_column_heads(self: OpenSearchExplorer) -> None:
        for key, button in self.column_buttons.items():
            _class_if(button, "osx-on", key == self.store_sort)
            label = next(label for column, label, _ in _STORE_COLUMNS if column == key)
            arrow = (" ▾" if self.store_descending else " ▴") if key == self.store_sort else ""
            if button.description != label + arrow:
                button.description = label + arrow

    def _nothing_shown(self: OpenSearchExplorer, lines: list[_StoreLine]) -> list[Any]:
        """
        The line under the header when the search and the chip leave nothing (none when something is shown).
        """
        if lines:
            return []
        if not self._store_lines():
            region = self.core.region
            return [self._w.HTML(self._html([_Note(
                f"No OpenSearch domains or Serverless collections in {region}. They're regional: "
                "explore(region='us-west-2') looks in another region.")]))]
        what = f"matches {self.store_query!r}" if self.store_query else "is under this chip"
        return [self._w.HTML(f'<div class="osx-empty">Nothing {_esc(what)}.</div>')]

    def _draw_store_count(self: OpenSearchExplorer, lines: list[_StoreLine]) -> None:
        total = len(self._store_lines())
        text = f"{total:,} domains and collections" if len(lines) == total else f"{len(lines):,} of {total:,}"
        self._set(self.store_count, _esc(text) if total else "")

    def _clicked_store(self: OpenSearchExplorer, line: _Line) -> None:
        if line.key:
            self._want_tab = "indexes"
            self._open_store(line.key)
