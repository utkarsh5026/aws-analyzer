"""
The explorer's Items tab: the open table's items, read a page at a time. Without a key it reads a sample from across
the table (or the table from its start); with a partition key value it queries that item collection, its sort key
narrowed to a prefix when one is typed, on the table or one of its indexes. The search box filters what was read,
More reads the next page, and a click on an item shows all of it, as get() does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import search_rank
from ..._kit.window import _skeleton

from ..helpers import format_value
from ..models import ItemPage
from ..view import _target
from .frame import _item_face
from .widgets import _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import DynamoDBExplorer


ITEMS_PER_READ = 50

SCAN_LIMIT = 10_000

_HOW_CHOICES = (("Sample across it", "sample"), ("From the start", "scan"))

_SEARCH_CHARS = 200


@dataclass(frozen=True)
class ItemRead:
    """
    What the Items tab reads: the table, the index ('' for the table itself), how ('sample', 'scan', or 'query'
    when a partition key value is given), and the sort key prefix a query narrows to.
    """

    table: str
    index: str = ""
    how: str = "sample"
    partition: str = ""
    starts_with: str = ""

    def describe(self, keys: list[str]) -> tuple[str, str]:
        """
        The title and subtitle of the page this read shows, as scan(), query() and sample() title theirs.
        """
        target = _target(self.table, self.index or None)
        if self.how == "query":
            condition = f"{keys[0]} = {self.partition!r}"
            if self.starts_with and len(keys) > 1:
                condition += f", {keys[1]} begins with {self.starts_with!r}"
            return f"Query {target}: {condition}", ""
        if self.how == "scan":
            return f"Scan {target}", "from the start of the table"
        return f"Sample of {target}", "spread across the table"


def _item_text(item: dict[str, Any], keys: list[str]) -> tuple[str, ...]:
    """
    What the Items tab's search box looks through: the key values first, then every other value as shown.
    """
    first = tuple(format_value(item[key], _SEARCH_CHARS) for key in keys if key in item)
    return first + tuple(format_value(value, _SEARCH_CHARS) for name, value in item.items() if name not in keys)


class _ItemsTab:
    """
    The Items tab, a part of DynamoDBExplorer: item_list is its page, item_read what it read last (None before),
    item_page the last page read and items_read every item read since the read began (More adds to them).
    """

    def _items_page(self: DynamoDBExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.item_read: ItemRead | None = None
        self.item_page: ItemPage | None = None
        self.items_read: list[dict[str, Any]] = []
        self._page_number = 0
        self.index_pick = w.Dropdown(options=[("The table", "")], value="", tooltip="Read the table or an index",
                                     layout=layout(flex="1 1 0", width="auto", min_width="0"))
        self.index_pick.observe(self._safely(lambda _change: self._fit_sort_box()), names="value")
        self.how_pick = w.Dropdown(options=list(_HOW_CHOICES), value="sample",
                                   tooltip="Without a key: a sample from across the table, or its first items",
                                   layout=layout(flex="1 1 0", width="auto", min_width="0"))
        self.partition_box = w.Text(placeholder="Partition key value (Enter reads its items)",
                                    layout=layout(flex="1 1 0", width="auto", min_width="0"))
        self.partition_box.on_msg(self._on_enter(lambda: self._read_items()))
        self.sort_box = w.Text(placeholder="Sort key starts with…", layout=layout(flex="1 1 0", width="auto",
                                                                                   min_width="0"))
        self.sort_box.on_msg(self._on_enter(lambda: self._read_items()))
        self.read_button = w.Button(description="Read", tooltip="Read the items again with these settings",
                                    layout=layout(width="auto", flex="0 0 auto"))
        self.read_button.add_class("ddx-small")
        self.read_button.on_click(self._safely(lambda _button: self._read_items()))
        self.more_button = w.Button(description="More", tooltip="Read the next page", disabled=True,
                                    layout=layout(width="auto", flex="0 0 auto"))
        self.more_button.add_class("ddx-small")
        self.more_button.on_click(self._safely(lambda _button: self._read_items(more=True)))
        source = w.HBox([self.index_pick, self.how_pick], layout=layout(width="100%"))
        key = w.HBox([self.partition_box, self.sort_box, self.read_button, self.more_button],
                     layout=layout(width="100%"))
        tools = w.VBox([source, key], layout=layout(width="100%"))
        tools.add_class("ddx-search")
        self.item_list = _ListPage(self, placeholder="Filter the items read: any value…", chips=(),
                                   on_click=self._clicked_item, tools=tools)
        self.item_list.redraw = self._draw_item_rows
        return self.item_list.page

    def _item_keys(self: DynamoDBExplorer) -> list[str]:
        """
        The key attributes of what's read: the index's, then the table's (an index's items carry both).
        """
        read = self.item_read
        if read is None:
            return []
        return self.core.key_attributes(read.table, read.index or None)

    def _fill_item_tools(self: DynamoDBExplorer) -> None:
        """
        The Items tab's controls for the open table: its indexes to read from, and the sort key box only when what's
        read has a sort key.
        """
        info = self.reports[self.table_name].info
        options = [("The table", "")] + [(f"Index {i.name} ({i.kind})", i.name) for i in info.indexes]
        self._quietly(self.index_pick, options=options, value="")
        for box in (self.partition_box, self.sort_box):
            self._quietly(box, value="")
        self._fit_sort_box()

    def _fit_sort_box(self: DynamoDBExplorer) -> None:
        table = self.table_name
        keys = self.core.keys(table, self.index_pick.value or None) if table else []
        self.sort_box.disabled = len(keys) < 2
        self.sort_box.placeholder = f"{keys[1]} starts with…" if len(keys) > 1 else "(no sort key)"
        if keys:
            self.partition_box.placeholder = f"{keys[0]} = … (Enter reads its items)"

    def _wanted_read(self: DynamoDBExplorer) -> ItemRead:
        partition = self.partition_box.value.strip()
        starts_with = self.sort_box.value.strip() if not self.sort_box.disabled else ""
        how = "query" if partition else self.how_pick.value
        return ItemRead(self.table_name, self.index_pick.value or "", how, partition, starts_with if partition else "")

    def _read_items(self: DynamoDBExplorer, *, more: bool = False) -> None:
        """
        Reads a page of the open table's items in the background, as the controls say: the next page of the last
        read with more=True.
        """
        if not self.table_name:
            raise ValueError("Pick a table first, in the Tables tab or the menu at the top.")
        read = self.item_read if more and self.item_read is not None else self._wanted_read()
        start = self.item_page.last_key if more and self.item_page is not None else None
        if not more:
            self.item_list.selected = ""
            self._set(self.item_list.view, _skeleton(f"Reading {read.table}'s items…"))
        self.more_button.disabled = True
        self._status(f"Reading {read.table}'s items…", busy=True, owner="items")
        self._later("items", lambda: self._fetch_items(read, start),
                    lambda page: self._got_items(page, read, more), self._items_failed)

    def _fetch_items(self: DynamoDBExplorer, read: ItemRead, start: Any) -> ItemPage:
        index = read.index or None
        if read.how == "query":
            sort = ("begins_with", read.starts_with) if read.starts_with else None
            return self.core.query(read.table, read.partition, sort, n=ITEMS_PER_READ, index=index, start_key=start)
        if read.how == "scan":
            return self.core.scan(read.table, ITEMS_PER_READ, index=index, start_key=start, scan_limit=SCAN_LIMIT)
        return self.core.sample(read.table, ITEMS_PER_READ, index=index, scan_limit=SCAN_LIMIT)

    def _got_items(self: DynamoDBExplorer, page: ItemPage, read: ItemRead, more: bool) -> None:
        number = self._page_number + 1 if more else 1
        self._page_number = number
        self.item_read, self.item_page = read, page
        self.items_read = self.items_read + page.items if more else list(page.items)
        self.more_button.disabled = not page.has_more
        self._draw_item_rows()
        if not self.item_list.selected:
            self._draw_item_page(page, read, number)
        total = f"{len(self.items_read):,} item{'' if len(self.items_read) == 1 else 's'} read"
        self._finish("items", total + (": More reads the next page." if page.has_more else "."), tabs=("items",))

    def _draw_item_page(self: DynamoDBExplorer, page: ItemPage, read: ItemRead, number: int) -> None:
        """
        A page of items on the right of the Items tab, as scan(), query() and sample() show it.
        """
        title, sub = read.describe(self.core.keys(read.table, read.index or None))
        empty = "The table is empty."
        if read.how == "query":
            empty = (f"No items with that partition key{' and sort key prefix' if read.starts_with else ''}. "
                     "Keys are case-sensitive: copy one from an item read without a key.")
        blocks = self.ui._page_blocks(page, title, sub, empty=empty, number=number)
        self._draw("items", self.item_list.view, blocks)

    def _items_failed(self: DynamoDBExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._draw("items", self.item_list.view, [_Note(text, "warn")])
        self.more_button.disabled = self.item_page is None or not self.item_page.has_more

    def _naming_keys(self: DynamoDBExplorer, keys: list[str]) -> list[str]:
        """
        The keys an item's line is named by: every key attribute, but a query's partition key, which every item read
        shares (its sort key then tells them apart).
        """
        read = self.item_read
        if read is None or read.how != "query" or len(keys) < 2:
            return keys
        return keys[1:]

    def _draw_item_rows(self: DynamoDBExplorer) -> None:
        page = self.item_list
        keys = self._item_keys()
        named_by = self._naming_keys(keys)
        page.draw_chips({})
        numbered = list(enumerate(self.items_read))
        if page.query:
            numbered = [(i, item) for i, item in numbered if search_rank(page.query, _item_text(item, keys)) is not None]
        words = page.query.split()
        lines = [_Shown(str(i), _item_face(item, keys, named_by=named_by, words=words)) for i, item in numbered]
        empty = "No item read matches the filter." if page.query else "No items read yet."
        page.fill(lines, total=len(self.items_read), empty=empty, noun="items")

    def _clicked_item(self: DynamoDBExplorer, line: _Line) -> None:
        if line.key:
            self._open_item(int(line.key))

    def _open_item(self: DynamoDBExplorer, number: int) -> None:
        """
        An item on the right of the Items tab, as get() shows it: at once when it was read from the table (it's whole),
        read again by its key in the background when it came from an index, which may hold only some attributes.
        """
        read, item = self.item_read, self.items_read[number]
        self.item_list.mark(str(number))
        table_keys = self.core.keys(read.table)
        primary = {key: item[key] for key in table_keys if key in item}
        if not read.index:
            self._draw("items", self.item_list.view, self.ui._get_blocks(read.table, primary, item))
            self._status(f"Item {number + 1:,} of {len(self.items_read):,}.")
            return
        self._set(self.item_list.view, _skeleton("Reading the whole item from the table…"))
        self._status("Reading the whole item from the table…", busy=True, owner="item")
        self._later("item", lambda: self.core.get(read.table, primary),
                    lambda whole: self._got_item(read.table, primary, whole, number), self._items_failed)

    def _got_item(self: DynamoDBExplorer, table: str, primary: dict[str, Any], item: dict[str, Any] | None,
                  number: int) -> None:
        if self.item_list.selected == str(number):
            self._draw("items", self.item_list.view, self.ui._get_blocks(table, primary, item))
        self._finish("item", f"Item {number + 1:,} of {len(self.items_read):,}.", tabs=("items",))
