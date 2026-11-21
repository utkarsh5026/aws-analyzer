"""
DynamoDBExplorer, the DynamoDB explorer window, and explore() to open it.
"""

from __future__ import annotations

import difflib
from typing import Any

from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _plural, human_money, human_size
from ..._kit.text import _esc
from ..._kit.themes import _theme_css, _theme_name
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..models import TableReport
from ..analyzer import DynamoDBAnalyzer
from ..style import _CSS, _ROOT, _render_html
from ..view import DynamoDBView
from .focus import _FocusReads
from .frame import _EXPLORER_CSS, _EXPLORER_TABS, _LOGO, _stats_html, _window_text
from .items import _ItemsTab
from .schema import _SchemaTab
from .tables import _TablesTab, _warnings


_NO_TABLE = ""

_TABLE_TABS = ("items", "schema")


class DynamoDBExplorer(_TablesTab, _ItemsTab, _SchemaTab, _FocusReads, _Explorer):
    """
    The DynamoDB explorer: a window to look through your DynamoDB tables and their items by clicking. The menu at
    the top opens a table (so does a click in the Tables tab); the cards beside it say what the tables cost and how
    many need fixing.

        Overview   every table at a glance: items, size, billing, cost a month, and every warning
        Tables     every table, with chips (to fix, on-demand, provisioned, no backups, idle) and a search box;
                   click one for its keys, indexes and how to query each, usage, cost and what to fix
        Items      the open table's items: a sample from across it, its first items, or one partition key's
                   (on the table or an index, the sort key narrowed to a prefix); More reads the next page, and a
                   click shows the whole item
        Schema     what the open table's items hold: every attribute's types, fill rate and examples, with chips
                   for the ones not on every item and the ones of mixed types; click one to count its values

    what: a table to open first. tab: the tab to start on. view / core: a DynamoDBView or DynamoDBAnalyzer to use
    (else one is made from region / profile). height: the height of the tabs' pages (pixels, or CSS like '80vh').
    mode: 'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.

    It only reads: nothing here writes, deletes or changes an item or a table. Reading items costs read units, as in
    a cell: each page says what it read and cost. x.ui is a DynamoDBView for reports in other cells; x.reports,
    x.items_read, x.profile and x.counts hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "ddx", "dynamodb-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        what: str | None = None,
        *,
        tab: str | None = None,
        view: DynamoDBView | None = None,
        core: DynamoDBAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        _check_choices(mode, tab)
        super().__init__()
        if view is None:
            view = DynamoDBView(core or DynamoDBAnalyzer(region=region, profile=profile),
                                mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.height = height
        self.table_name = _NO_TABLE
        self.loaded: set[str] = set()
        self._error = ""
        self._want = what
        self._tab = tab or ("tables" if what else "overview")
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(what, note, mode)
            return
        self._build()
        self._begin()
        self._display()

    def __repr__(self) -> str:
        return f"DynamoDBExplorer({self.core.region}) · help(DynamoDBExplorer) says what it shows"

    @_window_errors
    def table(self, name: str) -> None:
        """
        Opens a table in the Tables tab, as clicking it in the list does: its keys, indexes and how to query each,
        usage, cost and what to fix.
        """
        self._open_table(self._known_table(name))
        self._show_tab("tables")

    @_window_errors
    def items(self, partition: Any = None, starts_with: str | None = None, *, table: str | None = None,
              index: str | None = None) -> None:
        """
        Opens the Items tab and reads: a partition key's items (the sort key narrowed to starts_with when given), or
        without one a sample from across the table. table= opens another table first, index= reads an index.
        """
        if table:
            self._open_table(self._known_table(table))
        if not self.table_name:
            raise ValueError("Pick a table first: x.items(table='orders'), or click one in the Tables tab.")
        if index is not None:
            if index:
                self.reports[self.table_name].info.index(index)
            self.index_pick.value = index
        self._quietly(self.partition_box, value="" if partition is None else str(partition))
        self._quietly(self.sort_box, value=starts_with or "")
        self._show_tab("items", load=False)
        self.loaded.add("items")
        self._read_items()

    @_window_errors
    def schema(self, table: str | None = None) -> None:
        """
        Opens the Schema tab: every attribute of the open table's items (or table='...''s), with its types, fill rate
        and examples.
        """
        if table:
            self._open_table(self._known_table(table))
        if not self.table_name:
            raise ValueError("Pick a table first: x.schema('orders'), or click one in the Tables tab.")
        self._show_tab("schema")

    @_window_errors
    def refresh(self) -> None:
        """
        Reads every table again (the ↻ button), and the open table's items and schema when they're next shown.
        """
        self.loaded.clear()
        self.counts.clear()
        self._begin()

    def _reports(self, what: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: table_info(name) shows a table, and sample(name) its items.")])
        if what:
            self.ui.table_info(what)
        else:
            self.ui.tables()

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, drawn in the report
        theme the window opened with, and without the report and theme CSS each one carries (the window's style holds
        them).
        """
        html = _render_html(_for_window(blocks, _window_text), self.ui.max_rows, theme=self._theme)
        return html.replace(_CSS, "", 1).replace(self._theme_style, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("ddx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.table_pick = w.Dropdown(options=[("Pick a table…", _NO_TABLE)], value=_NO_TABLE,
                                     tooltip="The table the Items and Schema tabs show",
                                     layout=layout(width="240px", flex="0 0 auto"))
        self.table_pick.observe(self._safely(self._table_picked), names="value")
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read every table again",
                                       layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("ddx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.table_pick, self.refresh_button], layout=layout(width="100%"))
        top.add_class("ddx-top")
        self.stats = w.HTML(layout=layout(width="100%"))
        meta = w.HBox([self.stats], layout=layout(width="100%"))
        meta.add_class("ddx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("ddx-head")
        self.overview_view = w.HTML(layout=layout(width="100%"))
        self._theme = _theme_name()
        self._theme_style = _theme_css(_ROOT, self._theme)
        self._frame(_CSS + _EXPLORER_CSS + self._theme_style, head, self._tab_bar(_EXPLORER_TABS), {
            "overview": w.VBox([self.overview_view]),
            "tables": self._tables_page(),
            "items": self._items_page(),
            "schema": self._schema_page(),
        })
        self._show_tab(self._tab, load=False)
        self._draw_title()
        self._draw_stats()

    def _show_tab(self, key: str, *, load: bool = True) -> None:
        """
        Opens a tab; the Items and Schema tabs read the open table the first time they're shown for it.
        """
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "ddx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))
        if load and key in _TABLE_TABS:
            self._load_tab(key)

    def _load_tab(self, key: str) -> None:
        page = self.item_list if key == "items" else self.attribute_list
        if not self.table_name:
            self._set(page.view, self._html([_Note("Pick a table in the Tables tab, or in the menu at the top, to see "
                                                   "its items and what they hold.")]))
            return
        if key in self.loaded:
            return
        self.loaded.add(key)
        if key == "items":
            self._read_items()
        else:
            self._read_schema()

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        if key == "tables" and self.reports:
            return f"{_plural(len(self.reports), 'table')}: click one for its keys, indexes, usage and cost."
        if key == "items" and self.table_name:
            return "Type a partition key value and press Enter for its items; click an item for all of it."
        if key == "schema" and self.profile is not None:
            return "Click an attribute to count its values."
        return ""

    def _begin(self) -> None:
        """
        Reads every table in the region in the background: each one described, with its CloudWatch usage.
        """
        self._error = ""
        self.table_list.selected = ""
        self._set(self.overview_view, _skeleton("Reading every DynamoDB table and its usage…"))
        self._status(f"Reading DynamoDB in {self.core.region}…", busy=True, owner="tables")
        self._later("tables", lambda: self.core.table_reports(), self._got_tables, self._tables_failed)

    def _tables_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "dynamodb:ListTables")
        self._error = str(reason)
        self._status(f"Couldn't read the tables ({reason}).", "warn")
        note = [_Note(f"Couldn't read the tables: {reason}.", "warn")]
        self._draw("overview", self.overview_view, note)
        self._draw("tables", self.table_list.view, note)

    def _got_tables(self, reports: list[TableReport]) -> None:
        self.reports = {report.info.name: report for report in reports}
        self._fill_table_pick()
        self._draw_table_rows()
        self._draw("overview", self.overview_view, self.ui._tables_blocks(reports, match=None, metrics=True))
        self._draw_stats()
        to_fix = sum(1 for report in reports if _warnings(report, self.core.prices))
        self._finish("tables", f"{_plural(len(reports), 'table')}" + (f" · {to_fix:,} to fix" if to_fix else ""),
                     tabs=("overview", "tables"))
        self._open_wanted()

    def _open_wanted(self) -> None:
        """
        Opens the table explore() was asked for (or keeps the one open), once the tables are read.
        """
        wanted, self._want = self._want or self.table_name, None
        if not wanted:
            return
        if wanted not in self.reports:
            self._status(self._not_found_text(wanted), "warn")
            return
        self._open_table(wanted, reopen=True)
        if self._tab in _TABLE_TABS:
            self._load_tab(self._tab)

    def _known_table(self, name: str) -> str:
        """
        A table the window read, by its name; a name it doesn't know raises ValueError with the closest ones.
        """
        if name in self.reports:
            return name
        raise ValueError(self._not_found_text(name))

    def _not_found_text(self, name: str) -> str:
        names = list(self.reports)
        close = [n for n in names if n.lower() == name.lower()] or difflib.get_close_matches(name, names, n=3)
        text = f"No table {name!r} in {self.core.region}"
        if close:
            return f"{text}. Did you mean {' or '.join(map(repr, close))}? Names are case-sensitive."
        return f"{text} (names are case-sensitive, and tables are regional)."

    def _open_table(self, name: str, *, reopen: bool = False) -> None:
        """
        Makes a table the open one: drawn on the right of the Tables tab, picked in the menu, and read by the Items
        and Schema tabs when they're next shown (another table's items and schema are dropped).
        """
        self.table_list.mark(name)
        self._draw_table(name)
        self._quietly(self.table_pick, value=name)
        if name == self.table_name and not reopen:
            return
        self.table_name = name
        self._drop("items", "item", "schema", "counts")
        self.loaded.clear()
        self.item_read, self.item_page, self.items_read = None, None, []
        self.profile, self.counts = None, {}
        self.more_button.disabled = True
        self._draw_item_rows()
        self._draw_attribute_rows()
        if "describe" not in self.reports[name].info.errors:
            self._fill_item_tools()
        self._draw_stats()
        self._focus_moved()

    def _table_picked(self, change: dict[str, Any]) -> None:
        name = change["new"]
        if not name or name == self.table_name:
            return
        self._open_table(name)
        if self._tab in _TABLE_TABS:
            self._load_tab(self._tab)

    def _fill_table_pick(self) -> None:
        names = sorted(self.reports)
        value = self.table_name if self.table_name in self.reports else _NO_TABLE
        self._quietly(self.table_pick, options=[("Pick a table…", _NO_TABLE)] + [(n, n) for n in names], value=value)

    def _draw_title(self) -> None:
        sub = "Your tables, their items and what they hold, and what they cost · read-only"
        self._set(self.title, f'<div class="ddx-brand"><span class="ddx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="ddx-name">DynamoDB explorer<span>{_esc(self.core.region)}</span></div>'
                              f'<div class="ddx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards under the title: the tables at a glance, how many to fix, and the table open.
        """
        if not self.reports and not self._error:
            cards = [("Tables", "…", "sk-on"), ("Size", "…", "sk-on"), ("Cost / month", "…", "sk-on")]
        else:
            reports = list(self.reports.values())
            to_fix = sum(1 for report in reports if _warnings(report, self.core.prices))
            cards = [("Tables", f"{len(reports):,}", ""),
                     ("Size", human_size(sum(r.info.size_bytes or 0 for r in reports)), ""),
                     ("Cost / month", human_money(sum(self._table_cost(r) for r in reports)), ""),
                     ("To fix", f"{to_fix:,}", "warn" if to_fix else "")]
        if self.table_name:
            cards.append(("Open table", self.table_name, ""))
        self._set(self.stats, _stats_html(cards))


def _check_choices(mode: str, tab: str | None) -> None:
    """
    Refuses a mode or tab the window doesn't have, saying which it does.
    """
    if mode not in ("auto", "widgets", "text"):
        raise ValueError("mode must be 'auto', 'widgets' or 'text'")
    tabs = [key for key, _, _, _ in _EXPLORER_TABS]
    if tab is not None and tab not in tabs:
        raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")


def explore(
    what: str | None = None,
    *,
    tab: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> DynamoDBExplorer:
    """
    Opens the DynamoDB explorer window and returns it: every table with what it costs and what to fix, a table's
    keys, indexes and usage, its items (a sample, or one partition key's), and what its items hold.

        explore()                          # the Overview: every table at a glance
        explore("orders")                  # a table, on the Tables tab
        explore("orders", tab="items")     # its items, a sample from across it

    It only reads: nothing here writes, deletes or changes an item or a table.
    """
    return DynamoDBExplorer(what, tab=tab, region=region, profile=profile, height=height)
