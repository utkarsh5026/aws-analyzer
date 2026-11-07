"""
AthenaExplorer, the Athena explorer window, and explore() to open it.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _plural, _utcnow, human_money, human_size
from ..._kit.text import _esc
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..helpers import QUERY_ID_RE, parse_table_ref
from ..models import Catalog, Overview, Query, QueryHistory, Workgroup
from ..analysis import catalog_findings, crawler_findings, did_not_finish, total_cost
from ..analysis.findings import _queries
from ..analyzer import AthenaAnalyzer
from ..style import _CSS, _render_html
from ..view import AthenaView
from .catalog import _CatalogTab, _CrawlersTab
from .costs import _CostsTab, _FailuresTab
from .focus import _FocusReads
from .frame import _EXPLORER_CSS, _EXPLORER_TABS, _LOGO, _SINCE_CHOICES, _SINCE_DAYS, _stats_html, _window_text
from .queries import _QueriesTab


_ALL_WORKGROUPS = ""

_QUERY_TABS = ("queries", "costs", "failures")


class AthenaExplorer(_QueriesTab, _CostsTab, _FailuresTab, _CatalogTab, _CrawlersTab, _FocusReads, _Explorer):
    """
    The Athena explorer: a window to look through your Athena queries and Glue tables by clicking. The menus at the
    top pick the time range and the workgroup; the cards beside them say what the queries cost and what to fix.

        Overview   what queries cost and which fail, each workgroup's limits, the costliest queries, and every
                   finding, warnings first
        Queries    every query, with chips (failed, costly, cancelled, running) and a search box; click one for its
                   SQL, why it failed, what it scanned and cost, and the rows it saved
        Costs      the same query's runs added up, costliest first; click one for its newest run
        Failures   failed queries by kind, with what each kind means and the fix; click one for its newest failure
        Catalog    every Glue table, with chips for what's wrong (empty or gone, no partitions loaded, read in full);
                   click one for its partitions, size, the queries that read it and the SQL to copy
        Crawlers   every crawler, failed or stopped ones first; click one for what's wrong with it

    what: a query ID or a table ('sales.events') to open first; without it, the Overview. tab: the tab to start on.
    since: the time range ('24h', '7d', '30d'). workgroup: one workgroup's queries only. view / core: an AthenaView
    or AthenaAnalyzer to use (else one is made from region / profile). height: the height of the tabs' pages
    (pixels, or CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.

    It only reads: nothing here runs a query, starts a crawler or changes a table. x.ui is an AthenaView for reports
    in other cells; x.workgroups, x.history, x.catalog, x.details and x.tables hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "atx", "athena-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        what: str | None = None,
        *,
        tab: str | None = None,
        since: str = "7d",
        workgroup: str | None = None,
        view: AthenaView | None = None,
        core: AthenaAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        _check_choices(mode, tab, since)
        super().__init__()
        if view is None:
            view = AthenaView(core or AthenaAnalyzer(region=region, profile=profile),
                              mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.height = height
        self.since = since
        self.workgroup = workgroup or _ALL_WORKGROUPS
        self.workgroups: list[Workgroup] = []
        self.history: QueryHistory | None = None
        self.catalog: Catalog | None = None
        self._errors: dict[str, str] = {}
        self._want = what
        self._tab = tab or _first_tab(what)
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(what, note, mode)
            return
        self._build()
        self._begin()
        self._display()

    def __repr__(self) -> str:
        return f"AthenaExplorer({self.core.region}) · help(AthenaExplorer) says what it shows"

    @_window_errors
    def query(self, query_id: str) -> None:
        """
        Opens a query in the Queries tab by its ID, as clicking it in the list does: its SQL, numbers and saved
        results.
        """
        found = QUERY_ID_RE.search(query_id or "")
        if found is None:
            raise ValueError(f"{query_id!r} isn't a query ID: they look like 'a1b2c3d4-...'")
        self._show_tab("queries")
        self._open_query(found.group(0), "queries")

    @_window_errors
    def table(self, table: str) -> None:
        """
        Opens a table in the Catalog tab ('sales.events'), as clicking it in the list does: its partitions, size,
        the queries that read it and the SQL to copy.
        """
        database, name = parse_table_ref(table)
        self._show_tab("catalog")
        self._open_table(f"{database}.{name}")

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the workgroups and queries, and the catalog with its crawlers (the ↻ button).
        """
        self.details.clear()
        self.tables.clear()
        self._begin()

    def _reports(self, what: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: expensive() shows what costs most, and catalog() the tables.")])
        if what and QUERY_ID_RE.search(what):
            self.ui.query(what)
        elif what:
            self.ui.table_info(what)
        else:
            self.ui.overview(days=_SINCE_DAYS[self.since])

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it).
        """
        return _render_html(_for_window(blocks, _window_text), self.ui.max_rows).replace(_CSS, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("atx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.since_pick = w.Dropdown(options=list(_SINCE_CHOICES), value=self.since, tooltip="How far back to read",
                                     layout=layout(width="150px", flex="0 0 auto"))
        self.since_pick.observe(self._safely(self._since_changed), names="value")
        self.workgroup_pick = w.Dropdown(options=[("All workgroups", _ALL_WORKGROUPS)], value=_ALL_WORKGROUPS,
                                         tooltip="Only one workgroup's queries", layout=layout(width="190px",
                                                                                               flex="0 0 auto"))
        self.workgroup_pick.observe(self._safely(self._workgroup_changed), names="value")
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the queries and the catalog again",
                                       layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("atx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.since_pick, self.workgroup_pick, self.refresh_button],
                     layout=layout(width="100%"))
        top.add_class("atx-top")
        self.stats = w.HTML(layout=layout(width="100%"))
        meta = w.HBox([self.stats], layout=layout(width="100%"))
        meta.add_class("atx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("atx-head")
        self.overview_view = w.HTML(layout=layout(width="100%"))
        self._frame(_CSS + _EXPLORER_CSS, head, self._tab_bar(_EXPLORER_TABS), {
            "overview": w.VBox([self.overview_view]),
            "queries": self._queries_page(),
            "costs": self._costs_page(),
            "failures": self._failures_page(),
            "catalog": self._catalog_page(),
            "crawlers": self._crawlers_page(),
        })
        self._show_tab(self._tab)
        self._draw_title()
        self._draw_stats()

    def _show_tab(self, key: str) -> None:
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "atx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        history, catalog = self.history, self.catalog
        if key in _QUERY_TABS and history is not None:
            return f"{_queries(len(self._queries_shown()))} in the {self._since_label()}: click one to see it."
        if key == "catalog" and catalog is not None:
            return f"{_plural(len(catalog.tables), 'table')}: click one for its partitions, size and queries."
        if key == "crawlers" and catalog is not None:
            return f"{_plural(len(catalog.crawlers), 'crawler')}: click one to see what's wrong with it."
        return ""

    def _since_label(self) -> str:
        return next(label.lower() for label, value in _SINCE_CHOICES if value == self.since)

    def _queries_shown(self) -> list[Query]:
        """
        The queries read, of the workgroup picked (every workgroup's when none is).
        """
        queries = self.history.queries if self.history else []
        return [q for q in queries if not self.workgroup or q.workgroup == self.workgroup]

    def _begin(self) -> None:
        """
        Reads the workgroups and the queries in the time picked, and the catalog with each table's folder checked,
        side by side in the background.
        """
        self._errors = {}
        for page in (self.query_list, self.cost_list, self.failure_list, self.table_list, self.crawler_list):
            page.selected = ""
        self._set(self.overview_view, _skeleton("Reading the queries, the workgroups and the Glue Data Catalog…"))
        self._status(f"Reading Athena in {self.core.region}…", busy=True, owner="history")
        self._load_history()
        self._later("catalog", self.core.catalog, self._got_catalog, self._catalog_failed)

    def _load_history(self) -> None:
        since = self.since
        self._later("history", lambda: (self.core.workgroups(), self.core.history(since)), self._got_history,
                    self._history_failed)

    def _history_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "athena:ListWorkGroups")
        self._errors["queries"] = str(reason)
        self._status(f"Couldn't read the queries ({reason}).", "warn")
        for page in (self.query_list, self.cost_list, self.failure_list):
            self._set(page.view, self._html([_Note(f"Couldn't read the queries: {reason}.", "warn")]))
        self._draw_overview()

    def _catalog_failed(self, exc: BaseException) -> None:
        reason = _why(_error_name(exc), "glue:GetDatabases") if not isinstance(exc, ValueError) else exc
        self._errors["catalog"] = str(reason)
        self._status(f"Couldn't read the Glue Data Catalog ({reason}).", "warn")
        self._draw_overview()

    def _got_history(self, read: tuple[list[Workgroup], QueryHistory]) -> None:
        self.workgroups, self.history = read
        self._fill_workgroups()
        self._draw_queries()
        if self.catalog is not None:
            self._draw_table_rows()
        failed = sum(1 for q in self._queries_shown() if did_not_finish(q))
        self._finish("history", f"{_queries(len(self.history.queries))} in the {self._since_label()}"
                                + (f" · {failed:,} failed" if failed else ""), tabs=_QUERY_TABS)
        self._open_wanted()

    def _got_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._draw_table_rows()
        self._draw_crawler_rows()
        self._draw_overview()
        self._draw_stats()
        self._finish("catalog", f"{_plural(len(catalog.tables), 'table')} and "
                                f"{_plural(len(catalog.crawlers), 'crawler')}", tabs=("catalog", "crawlers"))
        self._open_wanted()

    def _open_wanted(self) -> None:
        """
        Opens the query or table explore() was asked for, once what it needs is read.
        """
        wanted = self._want
        if not wanted:
            return
        found = QUERY_ID_RE.search(wanted)
        if found and self.history is not None:
            self._want = None
            self._open_query(found.group(0), "queries")
        elif not found and self.catalog is not None:
            self._want = None
            database, name = parse_table_ref(wanted)
            self._open_table(f"{database}.{name}")

    def _draw_queries(self) -> None:
        """
        The tabs drawn from the queries: Queries, Costs and Failures, the Overview and the cards.
        """
        self._draw_query_rows()
        self._draw_cost_rows()
        self._draw_failure_rows()
        self._draw_overview()
        self._draw_stats()

    def _fill_workgroups(self) -> None:
        names = sorted({g.name for g in self.workgroups} | {q.workgroup for q in self.history.queries if q.workgroup})
        options = [("All workgroups", _ALL_WORKGROUPS)] + [(f"Workgroup: {n}", n) for n in names]
        value = self.workgroup if self.workgroup in names else _ALL_WORKGROUPS
        self.workgroup = value
        self._quietly(self.workgroup_pick, options=options, value=value)

    def _since_changed(self, change: dict[str, Any]) -> None:
        if not change["new"] or change["new"] == self.since:
            return
        self.since = change["new"]
        for page in (self.query_list, self.cost_list, self.failure_list):
            page.selected = ""
        self._status(f"Reading the queries in the {self._since_label()}…", busy=True, owner="history")
        self._load_history()

    def _workgroup_changed(self, change: dict[str, Any]) -> None:
        if change["new"] == self.workgroup:
            return
        self.workgroup = change["new"] or _ALL_WORKGROUPS
        for page in (self.query_list, self.cost_list, self.failure_list):
            page.selected = ""
        if self.history is not None:
            self._draw_queries()
            if self.catalog is not None:
                self._draw_table_rows()
        self._status(self._tab_line(self._tab))

    def _overview(self) -> Overview | None:
        """
        What the Overview tab draws: the workgroups, the queries of the workgroup picked, and the catalog, once both
        are read (or failed).
        """
        has_queries = self.history is not None or "queries" in self._errors
        has_catalog = self.catalog is not None or "catalog" in self._errors
        if not (has_queries and has_catalog):
            return None
        history = None
        if self.history is not None:
            history = QueryHistory(self.history.since, self.history.until, self._queries_shown(),
                                   self.history.workgroups, self.history.truncated, dict(self.history.errors))
        groups = [g for g in self.workgroups if not self.workgroup or g.name == self.workgroup]
        return Overview(self.core.region, _SINCE_DAYS[self.since], groups, history, self.catalog,
                        errors=dict(self._errors))

    def _draw_overview(self) -> None:
        overview = self._overview()
        if overview is None:
            return
        self._draw("overview", self.overview_view, self.ui._overview_blocks(overview))

    def _draw_title(self) -> None:
        sub = "Your queries, what they cost and why they fail, and the Glue tables behind them · read-only"
        self._set(self.title, f'<div class="atx-brand"><span class="atx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="atx-name">Athena explorer<span>{_esc(self.core.region)}</span></div>'
                              f'<div class="atx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards under the title: the queries picked at a glance, and the tables and crawlers to fix.
        """
        if self.history is None:
            cards = [("Queries", "…", "sk-on"), ("Cost", "…", "sk-on"), ("Failed", "…", "sk-on")]
        else:
            queries = self._queries_shown()
            failed = sum(1 for q in queries if did_not_finish(q))
            cards = [("Queries", f"{len(queries):,}", ""),
                     ("Scanned", human_size(sum(q.scanned_bytes for q in queries)), ""),
                     ("Cost", human_money(total_cost(queries, self.core.prices)), ""),
                     ("Failed", f"{failed:,}", "bad" if failed else "")]
        cards += self._catalog_cards()
        self._set(self.stats, _stats_html(cards))

    def _catalog_cards(self) -> list[tuple[str, str, str]]:
        if self.catalog is None:
            return [("Tables to fix", "…", "sk-on")]
        history = self.history
        findings = catalog_findings(self.catalog, history, self.core.prices) if history else []
        tables = sum(1 for level, text in findings if level == "warn" and "Crawler" not in text)
        crawlers = sum(1 for level, _ in crawler_findings(self.catalog.crawlers, _utcnow()) if level == "warn")
        return [("Tables", f"{len(self.catalog.tables):,}", ""),
                ("Table warnings", f"{tables:,}", "warn" if tables else ""),
                ("Crawler warnings", f"{crawlers:,}", "warn" if crawlers else "")]


def _first_tab(what: str | None) -> str:
    """
    The tab to start on: the Queries tab for a query ID, the Catalog tab for a table, else the Overview.
    """
    if not what:
        return "overview"
    return "queries" if QUERY_ID_RE.search(what) else "catalog"


def _check_choices(mode: str, tab: str | None, since: str) -> None:
    """
    Refuses a mode, tab or time range the window doesn't have, saying which it does.
    """
    if mode not in ("auto", "widgets", "text"):
        raise ValueError("mode must be 'auto', 'widgets' or 'text'")
    tabs = [key for key, _, _, _ in _EXPLORER_TABS]
    if tab is not None and tab not in tabs:
        raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")
    if since not in _SINCE_DAYS:
        raise ValueError(f"since is one of {', '.join(_SINCE_DAYS)}, not {since!r}")


def explore(  # noqa: PLR0913
    what: str | None = None,
    *,
    tab: str | None = None,
    since: str = "7d",
    workgroup: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> AthenaExplorer:
    """
    Opens the Athena explorer window and returns it: what queries cost and why they fail, every query, the same
    query's runs added up, the failures by kind, every Glue table with what's wrong with it, and the crawlers.

        explore()                             # the Overview of the last 7 days
        explore(tab="costs", since="30d")     # the costliest queries of 30 days
        explore("sales.events")               # a table, on the Catalog tab
        explore("a1b2c3d4-...")               # a query, on the Queries tab

    It only reads: nothing here runs a query, starts a crawler or changes a table.
    """
    return AthenaExplorer(what, tab=tab, since=since, workgroup=workgroup, region=region, profile=profile,
                          height=height)
