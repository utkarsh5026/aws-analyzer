"""
The explorer's Catalog and Crawlers tabs. Catalog: every Glue table with a dot for what's wrong with it (its folder
empty or gone, partitions never loaded, read in full), chips for each problem and a search box, and on the right the
table picked as table_info() shows it, its partitions counted and its folder listed in the background. Crawlers:
every crawler, failed or stopped ones first, and on the right what's wrong with the one picked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Cards, _Findings, _Note, _Table, _Text, _Title
from ..._kit.fmt import _duration, _fmt_dt, _utcnow, human_age
from ..._kit.text import search_rank
from ..._kit.window import _skeleton

from ..helpers import is_columnar
from ..models import Crawler, Table, TableReport
from ..analysis import crawler_findings, queries_reading, table_findings
from .frame import _crawler_face, _schedule_words, _table_face
from .widgets import _Chip, _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import AthenaExplorer


_TABLE_CHIPS = (
    _Chip("", "All", "all", "Every table and view"),
    _Chip("fix", "To fix", "warn", "The tables with a warning"),
    _Chip("gone", "Empty or gone", "bad", "The tables whose S3 folder is empty, or whose bucket doesn't exist"),
    _Chip("unloaded", "No partitions loaded", "warn", "Partitioned tables with no partitions in the catalog"),
    _Chip("unpartitioned", "No partitions", "none", "Tables every query reads in full"),
    _Chip("rows", "CSV / JSON", "none", "Tables stored row by row, not as Parquet or ORC"),
    _Chip("views", "Views", "none", "Views"),
)

_CRAWLER_CHIPS = (_Chip("", "All", "all", "Every crawler"),)

_LEVEL_ORDER = {"warn": 0, "info": 1, "": 2}


def _in_table_chip(table: Table, findings: list[tuple[str, str]], chip: str) -> bool:
    """
    Whether a table belongs under a chip ('' is every one).
    """
    location = table.location
    tests = {
        "fix": any(level == "warn" for level, _ in findings),
        "gone": location is not None and location.state in ("empty", "no bucket"),
        "unloaded": table.is_partitioned and not table.projection and table.partitions == 0,
        "unpartitioned": not table.is_partitioned and not table.is_view,
        "rows": bool(table.format) and not is_columnar(table.format),
        "views": table.is_view,
    }
    return tests.get(chip, True)


def _table_text(table: Table) -> tuple[str, ...]:
    keys = " ".join(k.name for k in table.partition_keys)
    columns = " ".join(c.name for c in table.columns)
    return (table.full_name, table.location_uri, table.format, keys, columns)


def _crawler_level(findings: list[tuple[str, str]]) -> str:
    levels = [level for level, _ in findings]
    return "warn" if "warn" in levels else ("info" if levels else "")


def _crawler_blocks(crawler: Crawler) -> list[Any]:
    """
    One crawler as the Crawlers tab shows it: what's wrong with it and what to run, then where it crawls from and
    into, its schedule, and its last crawl.
    """
    findings = crawler_findings([crawler], _utcnow())
    status = (crawler.last_status or "never ran").lower()
    cards = [("Runs", _schedule_words(crawler)), ("Last crawl", human_age(crawler.last_start)),
             ("Status", status, "bad" if crawler.last_status == "FAILED" else ""),
             ("Took", _duration(crawler.last_runtime_s) if crawler.last_runtime_s else "-")]
    changes = [("Tables added", crawler.tables_created), ("Tables changed", crawler.tables_updated),
               ("Tables removed", crawler.tables_deleted)]
    rows = [[label, "-" if value is None else f"{value:,}"] for label, value in changes]
    rows += [["Database", crawler.database or "-"], ["Schedule", crawler.schedule or "none: it runs when started"],
             ["Last started", _fmt_dt(crawler.last_start)]]
    blocks: list[Any] = [
        _Title(f"Crawler {crawler.name}", " · ".join(crawler.targets) or "no targets"),
        _Cards([card if len(card) == 2 or card[2] else card[:2] for card in cards]),
        _Findings(findings, empty="Its last crawl worked, and it runs on time."),
    ]
    if crawler.last_error:
        blocks.append(_Text(crawler.last_error, title="Its last crawl's error", wrap=True))
    blocks.append(_Table(["", ""], rows, title="Its last crawl", sortable=False))
    return blocks


class _CatalogTab:
    """
    The Catalog tab, a part of AthenaExplorer: table_list is its page; table_name the table shown on the right, and
    tables the ones read so far, by name.
    """

    def _catalog_page(self: AthenaExplorer) -> Any:
        self.table_name = ""
        self.tables: dict[str, Table] = {}
        self.table_list = _ListPage(self, placeholder="Search: table, location, format, column…",
                                    chips=_TABLE_CHIPS, on_click=self._clicked_table)
        self.table_list.redraw = self._draw_table_rows
        return self.table_list.page

    def _table_findings(self: AthenaExplorer, table: Table) -> list[tuple[str, str]]:
        """
        A table's findings, with the queries in the history read that name it.
        """
        queries = queries_reading(self._queries_shown(), table.full_name)
        return table_findings(table, queries, self.core.prices)

    def _draw_table_rows(self: AthenaExplorer) -> None:
        page = self.table_list
        if self.catalog is None:
            return
        findings = {t.full_name: self._table_findings(t) for t in self.catalog.tables}
        tables = sorted(self.catalog.tables, key=lambda t: (not _in_table_chip(t, findings[t.full_name], "fix"),
                                                            t.full_name))
        page.draw_chips({chip.key: sum(1 for t in tables if _in_table_chip(t, findings[t.full_name], chip.key))
                         for chip in _TABLE_CHIPS})
        matching = [t for t in tables if _in_table_chip(t, findings[t.full_name], page.chip)]
        if page.query:
            matching = [t for t in matching if search_rank(page.query, _table_text(t)) is not None]
        words = page.query.split()
        lines = [_Shown(t.full_name, _table_face(t, findings=findings[t.full_name], words=words)) for t in matching]
        empty = "No table matches the search." if page.query or page.chip else (
            f"No tables in the Glue Data Catalog in {self.core.region}.")
        page.fill(lines, total=len(tables), empty=empty, noun="tables")
        if not page.selected and page.keys:
            self._open_table(page.keys[0])

    def _clicked_table(self: AthenaExplorer, line: _Line) -> None:
        if line.key:
            self._open_table(line.key)

    def _open_table(self: AthenaExplorer, name: str) -> None:
        """
        Shows a table on the Catalog tab's right: at once when it was read before, else its partitions counted and
        its folder listed in the background.
        """
        self.table_list.mark(name)
        self.table_name = name
        if name in self.tables:
            self._draw_table(self.tables[name])
            return
        self._set(self.table_list.view, _skeleton(f"Reading {name}: its partitions and its S3 folder…"))
        if self._tab == "catalog":
            self._status(f"Reading {name}…", busy=True, owner="table")
        self._later("table", lambda: self.core.table(name), self._got_table, self._table_failed)

    def _got_table(self: AthenaExplorer, table: Table) -> None:
        self.tables[table.full_name] = table
        if table.full_name == self.table_name:
            self._draw_table(table)
        self._finish("table", f"{table.full_name}: {table.format or 'view'}", tabs=("catalog",))

    def _draw_table(self: AthenaExplorer, table: Table) -> None:
        queries = queries_reading(self._queries_shown(), table.full_name)
        report = TableReport(table, self.history, queries, table.location.requests if table.location else 0)
        self._draw("catalog", self.table_list.view, self.ui._table_blocks(report))

    def _table_failed(self: AthenaExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._draw("catalog", self.table_list.view, [_Note(text, "warn")])


class _CrawlersTab:
    """
    The Crawlers tab, a part of AthenaExplorer: crawler_list is its page.
    """

    def _crawlers_page(self: AthenaExplorer) -> Any:
        self.crawler_list = _ListPage(self, placeholder="Search: crawler, database, path…", chips=_CRAWLER_CHIPS,
                                      on_click=self._clicked_crawler)
        self.crawler_list.redraw = self._draw_crawler_rows
        return self.crawler_list.page

    def _crawlers_ranked(self: AthenaExplorer) -> list[tuple[Crawler, str]]:
        """
        The crawlers with how bad their findings are, the ones with warnings first, then by name.
        """
        crawlers = self.catalog.crawlers if self.catalog else []
        now = _utcnow()
        ranked = [(c, _crawler_level(crawler_findings([c], now))) for c in crawlers]
        return sorted(ranked, key=lambda pair: (_LEVEL_ORDER[pair[1]], pair[0].name))

    def _draw_crawler_rows(self: AthenaExplorer) -> None:
        page = self.crawler_list
        if self.catalog is None:
            return
        ranked = self._crawlers_ranked()
        page.draw_chips({"": len(ranked)})
        matching = ranked
        if page.query:
            matching = [pair for pair in ranked
                        if search_rank(page.query, (pair[0].name, pair[0].database, *pair[0].targets)) is not None]
        words = page.query.split()
        lines = [_Shown(c.name, _crawler_face(c, level=level if level == "warn" else "", words=words))
                 for c, level in matching]
        empty = "No crawler matches the search." if page.query else f"No Glue crawlers in {self.core.region}."
        page.fill(lines, total=len(ranked), empty=empty, noun="crawlers")
        if not page.selected and page.keys:
            self._show_crawler(page.keys[0])

    def _clicked_crawler(self: AthenaExplorer, line: _Line) -> None:
        if line.key:
            self._show_crawler(line.key)

    def _show_crawler(self: AthenaExplorer, name: str) -> None:
        crawler = next((c for c in (self.catalog.crawlers if self.catalog else []) if c.name == name), None)
        if crawler is None:
            return
        self.crawler_list.mark(name)
        self._draw("crawlers", self.crawler_list.view, _crawler_blocks(crawler))
