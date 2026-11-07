"""
AthenaView: the notebook UI, which renders what AthenaAnalyzer returns as cards and tables.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.deps import _require
from .._kit.errors import _Hint, _why
from .._kit.fmt import _count, _duration, _fmt_dt, _plural, _tz_label, _utcnow, human_age, human_money, human_size
from .._kit.render import _call
from .._kit.view import _BaseView, _friendly_errors

from .analysis import (
    catalog_findings, code_findings, costliest, crawler_findings, ctas_sql, detail_findings, did_not_finish,
    group_failures, monthly, overview_findings, pandas_code, query_cost, query_findings, query_shapes,
    repair_sql, run_code, sample_sql, scan_cost, table_findings, tables_in_sql, total_cost,
)
from .analysis.findings import COSTLY
from .analyzer import AthenaAnalyzer
from .helpers import STATE_TONES, is_columnar, one_line, schedule_period
from .models import Catalog, Crawler, Overview, Query, QueryDetail, QueryHistory, Table, TableReport, Workgroup
from .style import _render_html


_SQL_WIDTH = 80

_TOP_SHAPES = 5

_COLUMNS_SHOWN = 40

_PERMISSIONS = {
    "workgroups": "athena:ListWorkGroups", "queries": "athena:ListQueryExecutions",
    "databases": "glue:GetDatabases", "crawlers": "glue:GetCrawlers", "statistics": "athena:GetQueryRuntimeStatistics",
    "results": "athena:GetQueryResults and s3:GetObject on the result location",
}

_STATUS_WORDS = {"failed": ("FAILED",), "succeeded": ("SUCCEEDED",), "ok": ("SUCCEEDED",),
                 "cancelled": ("CANCELLED",), "canceled": ("CANCELLED",), "running": ("RUNNING", "QUEUED")}

_LOCATION_TONES = {"ok": "", "empty": "warn", "no bucket": "bad", "denied": "warn"}


def _card(card: tuple[str, ...]) -> tuple[str, ...]:
    """
    A card without an empty tone: (label, value) or (label, value, tone).
    """
    return card if len(card) == 2 or card[2] else card[:2]


def _state_cell(query: Query) -> Any:
    tone = STATE_TONES.get(query.state, "")
    text = query.state.lower() or "-"
    return _Tone(text, tone) if tone in ("bad", "warn") else text


def _took(query: Query) -> str:
    return _duration(query.seconds) if query.total_ms else "-"


def _location_cell(table: Table) -> Any:
    if table.location is None:
        return "-" if not table.is_view else "view"
    state = table.location.state
    tone = _LOCATION_TONES.get(state, "warn")
    return _Tone(state, tone) if tone else state


def _partition_cell(table: Table) -> str:
    if not table.is_partitioned:
        return "-"
    keys = ", ".join(k.name for k in table.partition_keys)
    return f"{keys} (projected)" if table.projection else keys


def _schedule_text(crawler: Crawler) -> str:
    if not crawler.schedule:
        return "on demand"
    period = schedule_period(crawler.schedule)
    words = {1: "hourly", 24: "daily", 168: "weekly", 744: "monthly"}
    word = words.get(int(period.total_seconds() // 3600), "") if period else ""
    paused = " · paused" if crawler.schedule_state == "PAUSED" else ""
    return f"{word or crawler.schedule}{paused}"


def _crawl_status(crawler: Crawler) -> Any:
    if crawler.state == "RUNNING":
        return "running"
    status = (crawler.last_status or "never ran").lower()
    tone = {"failed": "bad", "cancelled": "warn", "never ran": "warn"}.get(status, "")
    return _Tone(status, tone) if tone else status


def _changes(crawler: Crawler) -> str:
    counts = (crawler.tables_created, crawler.tables_updated, crawler.tables_deleted)
    if all(c is None for c in counts):
        return "-"
    return f"+{counts[0] or 0} ~{counts[1] or 0} -{counts[2] or 0}"


def _has_warning(findings: list[tuple[str, str]], *words: str) -> bool:
    return any(level == "warn" and any(w in text for w in words) for level, text in findings)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _status_states(status: Any) -> tuple[str, ...] | None:
    if status is None or status == "":
        return None
    text = str(status).strip().lower()
    if text in _STATUS_WORDS:
        return _STATUS_WORDS[text]
    raise ValueError(f"status= is 'failed', 'succeeded', 'cancelled' or 'running', not {status!r}")


def _pointing_here(findings: list[tuple[str, str]], table: Table) -> list[tuple[str, str]]:
    """
    A table's findings as its own report shows them: where they point at table_info() for the SQL, the SQL is below.
    """
    call = _call("table_info", table.full_name)
    here = []
    for level, text in findings:
        text = text.replace(f"(the SQL is in {call})", "(the SQL is below)").replace(f"(see {call})",
                                                                                     "(the SQL is below)")
        here.append((level, text))
    return here


class AthenaView(_BaseView):
    """
    Notebook UI for Athena and the Glue Data Catalog. Every public method renders a report and returns None; the
    data is on self.core (an AthenaAnalyzer).
    """

    _render_html = staticmethod(_render_html)
    _GROUPS = {
        "🏛️ Athena": ("overview",),
        "🧾 Past queries": ("queries", "expensive", "failures", "query"),
        "📚 Data Catalog": ("catalog", "table_info", "crawlers"),
        "▶️ Run a query yourself": ("query_code",),
        "🖱️ Explore by clicking": ("explore",),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "what queries cost, which fail, and the tables and crawlers to fix"),
        ("expensive()", "the queries that cost the most, and how to make them cheaper"),
        ("catalog()", "every table, and the ones whose S3 folder is empty or gone"),
    )
    _PROGRESS = ("Reading", "queries")

    def __init__(
        self,
        core: AthenaAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: AthenaAnalyzer = core or AthenaAnalyzer()

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code == "InvalidRequestException" and "not found" in message.lower():
            return f"{text} Athena keeps 45 days of query history: queries() lists the recent ones."
        if code == "EntityNotFoundException":
            return f"{text} The catalog is regional (this is {self.core.region}): catalog() lists its tables."
        if code in ("AccessDeniedException", "AccessDenied") or "not authorized" in message:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    def _error_notes(self, errors: dict[str, str]) -> list[Any]:
        notes = []
        for part, code in errors.items():
            permission = _PERMISSIONS.get(part, "")
            why = _why(code, permission) if permission else code
            notes.append(_Note(f"The {part} couldn't be read ({why}), so the report leaves them out.", "warn"))
        return notes

    def _history_note(self, history: QueryHistory) -> list[Any]:
        notes = self._error_notes({f"queries of workgroup {k}": v for k, v in history.errors.items()})
        if history.truncated:
            notes.append(_Note("Stopped at the limit: a workgroup had more queries in this time than were read. "
                               "Pass limit= for more, or a shorter since=.", "warn"))
        return notes


    @_friendly_errors
    def overview(self, *, days: int = 7) -> None:
        """
        Athena in the region at a glance: what the last 7 days of queries scanned and cost, how many failed, each
        workgroup's limits, and the tables and crawlers that need fixing, with what to do first.
        """
        with self._progress("Reading queries", unit="queries") as tick:
            overview = self.core.overview(days=days, progress=tick)
        self._show(self._overview_blocks(overview))

    def _overview_blocks(self, overview: Overview) -> list[Any]:
        prices = self.core.prices
        history = overview.history or QueryHistory(_utcnow(), _utcnow())
        catalog = overview.catalog or Catalog(overview.region)
        findings = overview_findings(overview, prices, _utcnow())
        cost = total_cost(history.queries, prices)
        failed = [q for q in history.queries if did_not_finish(q)]
        share = len(failed) / len(history.queries) if history.queries else 0.0
        scanned = sum(q.scanned_bytes for q in history.queries)
        table_warned = _has_warning(findings, "table", "partition")
        crawler_warned = _has_warning(findings, "Crawler")
        cards = [
            (f"Queries · {overview.days}d", _count(len(history.queries))),
            (f"Scanned · {overview.days}d", human_size(scanned)),
            (f"Cost · {overview.days}d", human_money(cost)),
            ("Failed", f"{len(failed):,} ({share:.0%})", "warn" if _has_warning(findings, "queries failed") else ""),
            ("Tables", _count(len(catalog.tables)), "warn" if table_warned else ""),
            ("Crawlers", _count(len(catalog.crawlers)), "warn" if crawler_warned else ""),
        ]
        blocks: list[Any] = [
            _Title(f"Athena in {overview.region}",
                   f"{_plural(len(overview.workgroups), 'workgroup')} · the last {_plural(overview.days, 'day')}"
                   f" · about {human_money(monthly(history, prices))} a month at this rate · "
                   f"{len(catalog.databases)} Glue databases · {self._price_basis()}"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="No query fails often, no table points at an empty folder, and every "
                                      "crawler ran on time."),
            *self._error_notes(overview.errors),
            *self._history_note(history),
        ]
        if overview.workgroups:
            blocks.append(self._workgroups_table(overview.workgroups, history))
        blocks += self._shapes_tables(history, top=_TOP_SHAPES, title="The queries that cost the most")
        if catalog.unchecked:
            blocks.append(_Note(f"{catalog.unchecked:,} more tables' folders weren't checked (the first "
                                f"{catalog.checked:,} were): catalog('<database>') checks one database's."))
        blocks.append(_Next(self._overview_next(history, catalog)))
        return blocks

    def _workgroups_table(self, workgroups: list[Workgroup], history: QueryHistory) -> _Table:
        prices = self.core.prices
        warned = {g.name for g in workgroups
                  if g.bytes_cutoff is None and total_cost([q for q in history.queries if q.workgroup == g.name],
                                                           prices) >= COSTLY}
        rows = []
        for group in workgroups:
            mine = [q for q in history.queries if q.workgroup == group.name]
            failed = sum(did_not_finish(q) for q in mine)
            limit: Any = human_size(group.bytes_cutoff) if group.bytes_cutoff else "none"
            if group.name in warned:
                limit = _Tone("none", "warn")
            state = group.state.lower() if group.state != "ENABLED" else "on"
            rows.append([group.name, state, _count(len(mine)), _count(failed),
                         human_size(sum(q.scanned_bytes for q in mine)), human_money(total_cost(mine, prices)),
                         limit, group.output_location or "-", group.engine_version.replace("Athena engine ", "")
                         or "-"])
        return _Table(["Workgroup", "State", "Queries", "Failed", "Scanned", "Cost", "Scan limit", "Results go to",
                       "Engine"], rows, title="Workgroups", path_cols=(7,))

    def _shapes_tables(self, history: QueryHistory, *, top: int, title: str) -> list[Any]:
        prices = self.core.prices
        shapes = [s for s in query_shapes(history.queries, prices) if s.cost > 0][:top]
        if not shapes:
            return []
        total = total_cost(history.queries, prices)
        rows = [[_count(s.runs), human_money(s.cost), human_size(s.scanned_bytes), human_age(s.last),
                 one_line(s.sql, _SQL_WIDTH), s.last_id] for s in shapes]
        bars = [s.cost / total if total else 0.0 for s in shapes]
        return [_Table(["Runs", "Cost", "Scanned", "Last run", "SQL (the newest run)", "Query ID"], rows,
                       title=title, bars=bars, bar_label="Share of cost")]

    def _overview_next(self, history: QueryHistory, catalog: Catalog) -> list[tuple[str, str]]:
        steps: list[tuple[str, str]] = [("expensive()", "the costliest queries, and how to make them cheaper")]
        if any(did_not_finish(q) for q in history.queries):
            steps.append(("failures()", "why queries failed, and how to fix each kind"))
        flagged = [t for t in catalog.tables if t.location and t.location.state in ("empty", "no bucket")]
        if flagged:
            steps.append((_call("table_info", flagged[0].full_name), "the table whose folder is empty or gone"))
        else:
            steps.append(("catalog()", "every table, its format, partitions and folder"))
        return steps[:3]


    def _read_history(self, since: Any, workgroup: str | None, limit: Any = 2_000) -> QueryHistory:
        with self._progress("Reading queries", unit="queries") as tick:
            return self.core.history(since, workgroup=workgroup, limit=limit, progress=tick)

    def _window(self, history: QueryHistory) -> str:
        where = f"workgroup {history.workgroups[0]}" if len(history.workgroups) == 1 else (
            f"{_plural(len(history.workgroups), 'workgroup')}")
        return f"{where} · since {_fmt_dt(history.since, zone=True)} ({human_age(history.since)})"

    @_friendly_errors
    def queries(self, since: Any = "7d", *, workgroup: str | None = None, status: Any = None,  # noqa: PLR0913
                database: str | None = None, limit: Any = 2_000) -> None:
        """
        The queries run since `since` ('7d', '24h', '2026-05-01'), newest first: when, how they ended, what each
        scanned and cost and how long it took, with its SQL and ID. status= keeps 'failed', 'succeeded',
        'cancelled' or 'running'; database= keeps one database's.
        """
        states = _status_states(status)
        history = self._read_history(since, workgroup, limit)
        prices = self.core.prices
        shown = [q for q in history.queries
                 if (states is None or q.state in states) and (database is None or q.database == database.lower())]
        cost = total_cost(shown, prices)
        failed = [q for q in shown if did_not_finish(q)]
        took = sorted(q.seconds for q in shown if q.total_ms)
        findings = query_findings(QueryHistory(history.since, history.until, shown), prices)
        cards = [
            ("Queries", _count(len(shown))),
            ("Scanned", human_size(sum(q.scanned_bytes for q in shown))),
            ("Cost", human_money(cost)),
            ("Failed", _count(len(failed)), "warn" if _has_warning(findings, "queries failed") else ""),
            ("Median time", _duration(took[len(took) // 2]) if took else "-"),
        ]
        rows = [[_fmt_dt(q.submitted), _state_cell(q), q.workgroup, q.database or "-", human_size(q.scanned_bytes),
                 human_money(query_cost(q, prices)), _took(q), one_line(q.sql, _SQL_WIDTH), q.id] for q in shown]
        blocks: list[Any] = [
            _Title("Athena queries", f"{self._window(history)} · {self._price_basis()}"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="" if not shown else "No query failed, and none ran again and again at a "
                                                            "cost."),
            *self._history_note(history),
        ]
        if not shown:
            blocks.append(_Note("No queries in that time. Athena keeps 45 days of history: try since='30d'."))
        else:
            blocks.append(_Table([f"Submitted ({_tz_label()})", "State", "Workgroup", "Database", "Scanned", "Cost",
                                  "Took", "SQL", "Query ID"], rows))
        blocks.append(_Next(self._queries_next(shown)))
        self._show(blocks)

    def _queries_next(self, shown: list[Query]) -> list[tuple[str, str]]:
        prices = self.core.prices
        steps: list[tuple[str, str]] = []
        top = costliest(shown, prices, top=1)
        if top:
            steps.append((_call("query", top[0].id), "the costliest one: its SQL, numbers and saved results"))
        if any(did_not_finish(q) for q in shown):
            steps.append(("failures()", "why queries failed, and how to fix each kind"))
        steps.append(("expensive()", "the same queries grouped, by what they cost together"))
        return steps[:3]

    @_friendly_errors
    def expensive(self, since: Any = "7d", *, top: int = 10, workgroup: str | None = None) -> None:
        """
        The queries that cost the most since `since`: the same query's runs added up (a query run 40 times shows
        once, with its total), then the single costliest runs, with what would make them cheaper.
        """
        history = self._read_history(since, workgroup)
        prices = self.core.prices
        cost = total_cost(history.queries, prices)
        shapes = [s for s in query_shapes(history.queries, prices) if s.cost > 0]
        findings = query_findings(history, prices)
        top_share = shapes[0].cost / cost if shapes and cost else 0.0
        cards = [
            ("Cost", human_money(cost)),
            ("≈ $ / month", human_money(monthly(history, prices))),
            ("Kinds of query", _count(len(shapes))),
            ("Costliest's share", f"{top_share:.0%}" if shapes else "-"),
        ]
        blocks: list[Any] = [
            _Title("The costliest Athena queries", f"{self._window(history)} · {self._price_basis()}"),
            _Cards(cards),
            _Findings([f for f in findings if "failed" not in f[1]], empty="" if not shapes else
                      "No query runs again and again at a cost, and no costly one reads every column."),
            *self._history_note(history),
        ]
        if not shapes:
            blocks.append(_Note("No query in that time cost anything: failed queries and DDL are free."))
            self._show(blocks)
            return
        blocks += self._shapes_tables(history, top=top, title="By kind of query (the same SQL with other values)")
        runs = costliest(history.queries, prices, top=top)
        rows = [[_fmt_dt(q.submitted), q.workgroup, human_size(q.scanned_bytes), human_money(query_cost(q, prices)),
                 _took(q), one_line(q.sql, _SQL_WIDTH), q.id] for q in runs]
        blocks.append(_Table([f"Submitted ({_tz_label()})", "Workgroup", "Scanned", "Cost", "Took", "SQL",
                              "Query ID"], rows, title="The costliest single runs"))
        blocks.append(_Note(f"Athena bills {human_money(prices.get('scanned_tb', 5.0))} per TB a query scans, "
                            f"at least {prices.get('minimum_mb', 10):g} MB each; failed queries, DDL and reused "
                            "results are free. A workgroup on provisioned capacity pays by the hour instead."))
        blocks.append(_Next(self._expensive_next(runs, history)))
        self._show(blocks)

    def _expensive_next(self, runs: list[Query], history: QueryHistory) -> list[tuple[str, str]]:
        steps: list[tuple[str, str]] = []
        if runs:
            steps.append((_call("query", runs[0].id), "the costliest run: its SQL and what it read"))
            tables = sorted(tables_in_sql(runs[0].sql, runs[0].database))
            if tables:
                steps.append((_call("table_info", tables[0]), "the table it reads: partitions, format, size"))
        steps.append(("queries(status='failed')", "the queries that failed"))
        return steps[:3]

    @_friendly_errors
    def failures(self, since: Any = "7d", *, workgroup: str | None = None) -> None:
        """
        Why queries failed since `since`: the failures grouped by kind (TABLE_NOT_FOUND, HIVE_BAD_DATA, an S3
        permission, ...), each with what it means in plain words, how to fix it, and the newest one's message.
        """
        history = self._read_history(since, workgroup)
        failed = [q for q in history.queries if did_not_finish(q)]
        groups = group_failures(failed)
        share = len(failed) / len(history.queries) if history.queries else 0.0
        times = [q.submitted for q in failed if q.submitted is not None]
        cards = [
            ("Failed", _count(len(failed))),
            ("Of all queries", f"{share:.1%}"),
            ("Kinds", _count(len(groups))),
            ("Newest", human_age(max(times)) if times else "-"),
        ]
        blocks: list[Any] = [
            _Title("Why Athena queries failed", self._window(history)),
            _Cards(cards),
            *self._history_note(history),
        ]
        if not groups:
            count = len(history.queries)
            blocks.append(_Note(f"None of the {count:,} {'query' if count == 1 else 'queries'} in that time failed.",
                                "ok"))
            self._show(blocks)
            return
        rows = [[g.kind, _count(g.count), human_age(g.last), f"{_capital(g.meaning)}. Fix: {g.fix}.",
                 one_line(g.message, 160), g.last_id] for g in groups]
        blocks.append(_Table(["Kind", "Queries", "Newest", "What it means, and the fix", "Newest message",
                              "Query ID"], rows, title="Failures by kind, the most common first", prose_cols=(3,)))
        steps = [(_call("query", groups[0].last_id), f"the newest {groups[0].kind}: its SQL and whole message")]
        steps.append(("queries(status='failed')", "every failed query, one by one"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def query(self, query_id: str, *, rows: int = 20) -> None:
        """
        One past query: its SQL, how it ended (and why it failed), what it scanned and cost, how long it queued and
        ran, and the first rows of the results it saved, read back from S3 without running it again.
        """
        detail = self.core.query(query_id, rows=rows)
        self._show(self._query_blocks(detail))

    def _query_blocks(self, detail: QueryDetail) -> list[Any]:
        query, prices = detail.query, self.core.prices
        findings = detail_findings(detail, prices)
        state_tone = STATE_TONES.get(query.state, "")
        cards = [
            ("State", query.state.lower(), state_tone if state_tone != "ok" or not findings else ""),
            ("Scanned", human_size(query.scanned_bytes)),
            ("Cost", human_money(query_cost(query, prices))),
            ("Took", _took(query)),
            ("Queued", _duration(query.queued_ms / 1000) if query.queued_ms else "-"),
            ("Rows out", _count(detail.rows_out)),
        ]
        sub = (f"{query.workgroup} · {query.database or 'no database'} · {_fmt_dt(query.submitted, zone=True)} "
               f"({human_age(query.submitted)}) · {query.engine_version or 'engine ?'} · {self._price_basis()}")
        blocks: list[Any] = [
            _Title(f"Query {query.id[:8]}", sub),
            _Cards([_card(c) for c in cards if c[1] != "-" or c[0] in ("State", "Took")]),
            _Findings(findings),
        ]
        if query.error_message:
            blocks.append(_Text(query.error_message, title="Athena's message", wrap=True))
        blocks.append(_Text(query.sql.strip(), title="SQL", code=True))
        blocks += self._error_notes(detail.errors)
        blocks += self._results_blocks(detail)
        steps = [(_call("query_code", one_line(query.sql), database=query.database or None,
                        workgroup=query.workgroup or None), "the Python to run it again yourself")]
        tables = sorted(tables_in_sql(query.sql, query.database))
        if tables:
            steps.append((_call("table_info", tables[0]), "the table it reads"))
        blocks.append(_Next(steps))
        return blocks

    def _results_blocks(self, detail: QueryDetail) -> list[Any]:
        results = detail.results
        if results is None:
            return []
        where = f" (saved in {detail.query.output_location})" if detail.query.output_location else ""
        if not results.columns:
            return [_Note(f"It saved no result rows{where}.")]
        more = ", more in core.results(...)" if results.truncated else ""
        return [_Table(results.columns, [[v if v is not None else "" for v in r] for r in results.rows],
                       title=f"Its saved results: the first {_plural(len(results.rows), 'row')}{more}{where}",
                       max_rows=0)]


    @_friendly_errors
    def catalog(self, database: str | None = None) -> None:
        """
        The Glue Data Catalog: every database and table with its format and partition keys, and whether its S3
        folder holds files, with the tables that point at an empty or missing folder, have no partitions loaded,
        or are read in full, and the crawlers to fix.
        """
        with self._progress("Checking table folders", unit="tables") as tick:
            catalog = self.core.catalog(database, progress=tick)
        history = self._quiet_history()
        self._show(self._catalog_blocks(catalog, history, database))

    def _quiet_history(self) -> QueryHistory | None:
        """
        The last 7 days of queries, for what a table's queries cost; None when they can't be read, so the catalog
        still shows without them.
        """
        try:
            return self.core.history("7d")
        except (ClientError, BotoCoreError):
            return None

    def _catalog_blocks(self, catalog: Catalog, history: QueryHistory | None, database: str | None) -> list[Any]:
        prices = self.core.prices
        findings = catalog_findings(catalog, history, prices) + crawler_findings(catalog.crawlers, _utcnow())
        findings.sort(key=lambda f: f[0] != "warn")
        tables = catalog.tables
        empty = [t for t in tables if t.location and t.location.state in ("empty", "no bucket")]
        partitioned = [t for t in tables if t.is_partitioned]
        row_format = [t for t in tables if t.format and not is_columnar(t.format)]
        cards = [
            ("Databases", _count(len(catalog.databases))),
            ("Tables", _count(len(tables))),
            ("Partitioned", _count(len(partitioned))),
            ("Row formats", _count(len(row_format))),
            ("Empty or gone", _count(len(empty)), "warn" if empty else ""),
            ("Crawlers", _count(len(catalog.crawlers)), "warn" if _has_warning(findings, "Crawler") else ""),
        ]
        where = f"database {database}" if database else catalog.region
        checks = (f"{_plural(catalog.checked, 'folder')} checked with {_plural(catalog.s3_requests, 'S3 request')} "
                  f"({human_money(catalog.s3_requests * prices.get('s3_list_request', 0))})")
        blocks: list[Any] = [
            _Title(f"Glue Data Catalog in {where}", checks),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="Every table's folder holds files, partitions are loaded, and the crawlers "
                                      "ran on time."),
            *self._error_notes(catalog.errors),
        ]
        if not tables:
            blocks.append(_Note(f"No tables in {where}. The catalog is regional: pass "
                                "AthenaAnalyzer(region='...') to look in another region."))
        else:
            rows = [[t.database, t.name, t.format or ("view" if t.is_view else "-"), _partition_cell(t),
                     t.location_uri or "-", _location_cell(t), human_age(t.updated)] for t in tables]
            blocks.append(_Table(["Database", "Table", "Format", "Partitioned by", "Location", "Folder", "Updated"],
                                 rows, title="Tables", path_cols=(4,)))
        if catalog.crawlers:
            blocks.append(self._crawlers_table(catalog.crawlers))
        for db in catalog.databases:
            if db.error:
                why = _why(db.error, "glue:GetTables")
                blocks.append(_Note(f"Database {db.name}'s tables couldn't be listed ({why}).", "warn"))
        first = next(iter(empty), None) or next(iter(tables), None)
        steps = []
        if first is not None:
            steps.append((_call("table_info", first.full_name), "a table in detail: columns, partitions, size"))
        steps.append(("crawlers()", "each crawler's schedule and last crawl"))
        blocks.append(_Next(steps))
        return blocks

    def _crawlers_table(self, crawlers: list[Crawler]) -> _Table:
        rows = [[c.name, c.database or "-", _schedule_text(c), human_age(c.last_start), _crawl_status(c),
                 _changes(c), _duration(c.last_runtime_s) if c.last_runtime_s else "-", ", ".join(c.targets) or "-"]
                for c in crawlers]
        return _Table(["Crawler", "Database", "Runs", "Last crawl", "Status", "Tables +/~/-", "Took", "Crawls"],
                      rows, title="Crawlers", path_cols=(7,))

    @_friendly_errors
    def crawlers(self) -> None:
        """
        Every Glue crawler: what it crawls into which database, how often it runs, when it last ran and how that
        went, and the tables it added, changed or removed, with the ones that failed or stopped running first.
        """
        crawlers = self.core.crawlers()
        findings = crawler_findings(crawlers, _utcnow())
        failed = [c for c in crawlers if c.last_status == "FAILED"]
        cards = [
            ("Crawlers", _count(len(crawlers))),
            ("Running now", _count(sum(c.state == "RUNNING" for c in crawlers))),
            ("Last crawl failed", _count(len(failed)), "warn" if failed else ""),
            ("Scheduled", _count(sum(bool(c.schedule) for c in crawlers))),
        ]
        blocks: list[Any] = [
            _Title(f"Glue crawlers in {self.core.region}"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="Every crawler's last crawl worked, and the scheduled ones run on time."),
        ]
        if not crawlers:
            blocks.append(_Note("No crawlers in this region. Tables can also be made with CREATE EXTERNAL TABLE."))
        else:
            blocks.append(self._crawlers_table(crawlers))
        blocks.append(_Next([("catalog()", "the tables they make, and whose folders are empty")]))
        self._show(blocks)

    @_friendly_errors
    def table_info(self, table: str, *, since: Any = "7d") -> None:
        """
        One table ('database.table'): its format, partitions, size and files in S3, its columns, the queries since
        `since` that read it and what they cost, with what would make it cheaper to query and the SQL to copy (a
        first query, CREATE TABLE AS to Parquet, MSCK REPAIR TABLE).
        """
        with self._progress("Reading the table", unit="queries") as tick:
            report = self.core.table_report(table, since=since, progress=tick)
        self._show(self._table_blocks(report))

    def _table_cards(self, table: Table, report: TableReport, findings: list[tuple[str, str]]) -> list[Any]:
        prices = self.core.prices
        location = table.location
        partitions = "none"
        if table.is_partitioned:
            partitions = "projected" if table.projection else (
                f"{table.partitions:,}{'' if table.partitions_complete else '+'}" if table.partitions is not None
                else "?")
        size = "-"
        if location is not None and location.state == "ok":
            size = f"{'' if location.complete else '≥ '}{human_size(location.size)}"
        return [
            ("Format", table.format or ("view" if table.is_view else "?"),
             "warn" if _has_warning(findings, "stored as") else ""),
            ("Partitions", partitions, "warn" if _has_warning(findings, "no partitions", "didn't filter") else ""),
            ("Size", size, "warn" if location and location.state in ("empty", "no bucket") else ""),
            ("Files", f"{location.files:,}{'' if location.complete else '+'}" if location and location.files else "-"),
            ("Queries", _count(len(report.queries))),
            ("Their cost", human_money(total_cost(report.queries, prices))),
        ]

    def _table_blocks(self, report: TableReport) -> list[Any]:
        table, prices = report.table, self.core.prices
        findings = _pointing_here(table_findings(table, report.queries, prices), table)
        location = table.location
        sub = [table.location_uri or "a view", f"updated {human_age(table.updated)}"]
        if location is not None and location.requests:
            sub.append(f"{_plural(location.requests, 'S3 request')} to list its folder")
        blocks: list[Any] = [
            _Title(f"Table {table.full_name}", " · ".join(sub)),
            _Cards([_card(c) for c in self._table_cards(table, report, findings)]),
            _Findings(findings, empty="Its folder holds data, its queries filter on its partitions, and it's stored "
                                      "in a columnar format."),
            *self._error_notes(report.errors),
        ]
        if location is not None and location.state == "ok" and not location.complete:
            blocks.append(_Note(f"Its folder holds more than the {location.files:,} files listed: the size is at "
                                "least what's shown. Pass file_limit= to core.table() for a full count."))
        blocks += self._table_detail(table, report)
        blocks += self._table_sql(table, findings)
        blocks.append(_Next(self._table_next(table)))
        return blocks

    def _table_detail(self, table: Table, report: TableReport) -> list[Any]:
        prices = self.core.prices
        blocks: list[Any] = []
        if table.is_view and table.view_sql:
            blocks.append(_Text(table.view_sql, title="The view's SQL", code=True))
        keys = {k.name for k in table.partition_keys}
        columns = [[c.name, c.type, "partition key" if c.name in keys else "-", c.comment or "-"]
                   for c in table.columns + table.partition_keys]
        blocks.append(_Table(["Column", "Type", "Role", "Comment"], columns, title=f"{_plural(len(columns), 'column')}",
                             collapsed=len(columns) > _COLUMNS_SHOWN))
        if table.newest_partitions:
            names = [k.name for k in table.partition_keys]
            blocks.append(_Table(names, table.newest_partitions, title="Its newest partitions", sortable=False))
        if report.queries:
            rows = [[_fmt_dt(q.submitted), _state_cell(q), human_size(q.scanned_bytes),
                     human_money(query_cost(q, prices)), _took(q), one_line(q.sql, _SQL_WIDTH), q.id]
                    for q in report.queries]
            blocks.append(_Table([f"Submitted ({_tz_label()})", "State", "Scanned", "Cost", "Took", "SQL",
                                  "Query ID"], rows, title="The queries that read it"))
        elif report.history is not None:
            blocks.append(_Note(f"No query read it since {_fmt_dt(report.history.since, zone=True)} (read from "
                                "the SQL, so a query through a view isn't counted)."))
        return blocks

    def _table_sql(self, table: Table, findings: list[tuple[str, str]]) -> list[Any]:
        if table.is_view:
            return []
        blocks: list[Any] = [_Text(sample_sql(table, table.newest_partitions[0] if table.newest_partitions else
                                              None), title="A first query (reads one partition)" if
                                   table.is_partitioned else "A first query", code=True)]
        if _has_warning(findings, "the catalog has no partitions"):
            blocks.append(_Text(repair_sql(table), title="Load its partitions from its folder", code=True))
        wants_copy = any(w in text for _, text in findings for w in ("CREATE TABLE AS", "stored as"))
        if wants_copy:
            blocks.append(_Text(ctas_sql(table), title="Copy it to Parquet, partitioned (run it in Athena)",
                                code=True))
        return blocks

    def _table_next(self, table: Table) -> list[tuple[str, str]]:
        sample = one_line(sample_sql(table, table.newest_partitions[0] if table.newest_partitions else None))
        return [(_call("query_code", sample, database=table.database), "the Python to run a first query, and its "
                                                                         "most cost"),
                (_call("queries", database=table.database), f"every query run in {table.database}")]


    @_friendly_errors
    def query_code(self, sql: str, *, database: str | None = None, workgroup: str | None = None) -> None:
        """
        The Python to run a new query yourself (boto3, or pandas with awswrangler), and what it could cost: the
        tables it names, their size, and whether it filters on their partitions. aws-analyzer only reads, and
        running a query writes its results to S3, so it never runs one.
        """
        names = tables_in_sql(sql, database or "")
        tables, missing = self.core.tables_named(names)
        prices = self.core.prices
        findings = code_findings(sql, tables, prices)
        listed = [t for t in tables if t.location and t.location.state == "ok"]
        most = sum(t.location.size for t in listed if t.location)
        some_partial = any(t.location and not t.location.complete for t in listed)
        cards = [
            ("Tables", _count(len(names))),
            ("Reads at most", f"{'≥ ' if some_partial else ''}{human_size(most)}" if listed else "?"),
            ("Costs at most", human_money(scan_cost(most, prices)) if listed else "?"),
        ]
        blocks: list[Any] = [
            _Title("Run this query yourself", "aws-analyzer never runs a query: Athena writes every query's "
                                              f"results to S3 · {self._price_basis()}"),
            _Cards(cards),
            _Findings(findings),
            _Text(run_code(sql, database=database or "", workgroup=workgroup or "", region=self.core.region),
                  title="With boto3", code=True),
            _Text(pandas_code(sql, database=database or "", workgroup=workgroup or ""),
                  title="Or into a DataFrame (pip install awswrangler)", code=True, collapsed=True),
        ]
        for name, why in missing.items():
            blocks.append(_Note(f"{name} couldn't be read: {why}", "warn"))
        if listed:
            blocks.append(_Note("The most it could read is the size of its tables' folders: partition filters, and "
                                "naming only the columns you need on a Parquet or ORC table, read less."))
        blocks.append(_Next([("queries(since='1h')", "the query once it ran: what it scanned and cost")]))
        self._show(blocks)

    @_friendly_errors
    def explore(self, what: str | None = None, *, tab: str | None = None, since: str = "7d",
                workgroup: str | None = None, height: int | str | None = None) -> None:
        """
        The explorer window: what queries cost and why they fail, every query (click one for its SQL and the rows it
        saved), the same query's runs added up, the failures by kind, every Glue table with what's wrong with it, and
        the crawlers. Needs Jupyter and ipywidgets; view.explorer is the window.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, overview() shows what "
                "queries cost and what to fix, expensive() the costliest queries, and catalog() the tables."
            )
        _require("ipywidgets", "The explorer window")
        from .explorer.window import AthenaExplorer

        self.explorer = AthenaExplorer(what, tab=tab, since=since, workgroup=workgroup, view=self, height=height,
                                       mode="widgets")
