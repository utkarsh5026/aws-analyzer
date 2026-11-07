"""
What needs attention, as ("warn" | "info", message) pairs: queries that fail or cost the most, workgroups that let a
query scan without a limit, tables whose folder is empty or gone or whose queries read all of them, and crawlers
that failed or stopped running. Each message says what's wrong, what it costs when it can, and what to do.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from ..._kit.fmt import human_age, human_money, human_size
from ..._kit.render import _call
from ..helpers import is_columnar, one_line, schedule_period
from ..models import Catalog, Crawler, Overview, Query, QueryDetail, QueryHistory, Table, Workgroup
from .cost import billed_bytes, per_month, query_cost, query_shapes, scan_cost, total_cost
from .failures import did_not_finish, explain_failure, failure_kind, group_failures
from .sql import filters_on, selects_every_column, tables_in_sql


FAILED_SHARE = 0.05

_FAILED_AT_LEAST = 3

COSTLY = 1.0

_REPEATED = 3

BIG_TABLE = 1024**3

_SMALL_FILE = 8 * 1024**2

_MANY_FILES = 1_000

_LEAST_CUTOFF = 10 * 1024**3

_LONG_UNUSED = timedelta(days=30)

_NAMES_SHOWN = 3

_SQL_WIDTH = 70

_ENGINE_2 = "Athena engine version 2"


def _queries(count: int, adjective: str = "") -> str:
    """
    '1 query', '5 queries', '2 costly queries'.
    """
    word = "query" if count == 1 else "queries"
    return f"{count:,} {adjective + ' ' if adjective else ''}{word}"


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:_NAMES_SHOWN])
    more = len(items) - _NAMES_SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


def queries_reading(queries: list[Query], full_name: str) -> list[Query]:
    """
    The queries whose SQL names a table ('sales.orders'), reading names without a database in the query's own.
    """
    wanted = full_name.lower()
    return [q for q in queries if wanted in tables_in_sql(q.sql, q.database)]


def suggested_cutoff(largest: int) -> int:
    """
    A per-query scan limit that leaves room above the largest query seen: twice it, rounded up to a power of two
    GB, at least 10 GB.
    """
    wanted = max(2 * largest, _LEAST_CUTOFF)
    gigabytes = 2 ** math.ceil(math.log2(wanted / 1024**3))
    return int(gigabytes * 1024**3)


def _failure_finding(history: QueryHistory) -> list[tuple[str, str]]:
    stopped = [q for q in history.queries if did_not_finish(q)]
    if not stopped:
        return []
    total = len(history.queries)
    share = len(stopped) / total
    top = group_failures(stopped)[0]
    meaning, fix = explain_failure(top.kind)
    level = "warn" if share >= FAILED_SHARE and len(stopped) >= _FAILED_AT_LEAST else "info"
    return [(level, f"{len(stopped)} of {total} queries failed ({share:.0%}): the most common, {top.kind} "
                    f"({top.count}), means {meaning}. Fix: {fix} (see failures())")]


def _shape_findings(history: QueryHistory, prices: dict[str, Any]) -> list[tuple[str, str]]:
    shapes = query_shapes(history.queries, prices)
    total = sum(s.cost for s in shapes)
    findings: list[tuple[str, str]] = []
    repeated = next((s for s in shapes if s.runs >= _REPEATED and s.cost >= COSTLY), None)
    if repeated is not None:
        share = repeated.cost / total if total else 0
        findings.append(("warn", f"The same query ran {repeated.runs} times and cost {human_money(repeated.cost)}, "
                                 f"{share:.0%} of what queries cost: '{one_line(repeated.sql, _SQL_WIDTH)}'. Turn "
                                 "on query result reuse so a run within the hour reads the last result for free, "
                                 "or keep its answer in a table with CREATE TABLE AS "
                                 f"(see {_call('query', repeated.last_id)})"))
    star = [s for s in shapes if s.select_star and s.cost >= COSTLY]
    if star:
        cost = sum(s.cost for s in star)
        findings.append(("info", f"{_queries(len(star), 'costly')} read every column with SELECT * "
                                 f"({human_money(cost)} in all): on a Parquet or ORC table, naming only the columns "
                                 f"you need reads a fraction of the bytes (see {_call('query', star[0].last_id)})"))
    return findings


def query_findings(history: QueryHistory, prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    What the query history says: how many queries failed and why, a query that runs again and again and what it
    costs, and costly queries that read every column.
    """
    if not history.queries:
        return []
    return _failure_finding(history) + _shape_findings(history, prices)


def _largest_scan(queries: list[Query], workgroup: str) -> int:
    scans = [q.scanned_bytes for q in queries if q.workgroup == workgroup]
    return max(scans, default=0)


def workgroup_findings(workgroups: list[Workgroup], history: QueryHistory | None,
                       prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Workgroups without a per-query scan limit that queries spend money in, on Athena engine version 2, or with no
    result location of their own.
    """
    queries = history.queries if history else []
    findings: list[tuple[str, str]] = []
    for group in workgroups:
        spent = total_cost([q for q in queries if q.workgroup == group.name], prices)
        largest = _largest_scan(queries, group.name)
        if group.bytes_cutoff is None and spent >= COSTLY:
            limit = suggested_cutoff(largest)
            findings.append(("warn", f"Workgroup {group.name} lets one query scan any amount: its queries cost "
                                     f"{human_money(spent)} and the largest read {human_size(largest)}. A per-query "
                                     f"limit of {human_size(limit)} stops a runaway scan: aws athena "
                                     f"update-work-group --work-group {group.name} --configuration-updates "
                                     f"BytesScannedCutoffPerQuery={limit}"))
        if group.engine_version == _ENGINE_2:
            findings.append(("info", f"Workgroup {group.name} still runs {_ENGINE_2}, which AWS is retiring: "
                                     "version 3 is faster and has more functions, and AUTO keeps it on the newest. aws athena update-work-group "
                                     f"--work-group {group.name} --configuration-updates "
                                     "EngineVersion={SelectedEngineVersion=AUTO}"))
        if group.state == "ENABLED" and not group.output_location:
            findings.append(("info", f"Workgroup {group.name} has no query result location: a caller that doesn't "
                                     "name one gets 'No output location provided'. Set one: aws athena "
                                     f"update-work-group --work-group {group.name} --configuration-updates "
                                     "'ResultConfigurationUpdates={OutputLocation=s3://<bucket>/athena-results/}'"))
    return findings


def _location_finding(table: Table) -> tuple[str, str] | None:
    location = table.location
    if location is None:
        return None
    info = _call("table_info", table.full_name)
    if location.state == "no bucket":
        return ("warn", f"{table.full_name} points at a bucket that doesn't exist ({location.uri}): its queries "
                        f"find nothing. Point it at its data with ALTER TABLE SET LOCATION, or drop it "
                        f"(see {info})")
    if location.state == "empty":
        return ("warn", f"{table.full_name} points at an empty S3 folder ({location.uri}): its queries return no "
                        f"rows. Check the path, or whether the job that writes it still runs (see {info})")
    if location.state == "denied":
        return ("info", f"{table.full_name}'s folder couldn't be checked ({location.uri}): this role may not list "
                        "it (s3:ListBucket)")
    return None


def _partition_finding(table: Table) -> tuple[str, str] | None:
    has_no_partitions = (table.is_partitioned and not table.projection and table.partitions == 0)
    has_data = table.location is None or table.location.state in ("ok", "unchecked")
    if not (has_no_partitions and has_data):
        return None
    keys = ", ".join(k.name for k in table.partition_keys)
    return ("warn", f"{table.full_name} is partitioned by {keys} but the catalog has no partitions: its queries "
                    f"return no rows. Load them with MSCK REPAIR TABLE {table.database}.{table.name}, or let a "
                    "crawler or partition projection add them")


def _full_scan_finding(table: Table, queries: list[Query], prices: dict[str, Any]) -> tuple[str, str] | None:
    size = table.location.size if table.location and table.location.state == "ok" else 0
    if table.is_view or table.is_partitioned:
        return None
    reading = [q for q in queries if billed_bytes(q, prices)]
    cost = total_cost(reading, prices)
    if reading and cost >= COSTLY:
        scanned = sum(q.scanned_bytes for q in reading)
        return ("warn", f"{table.full_name} has no partitions, so each of its {len(reading)} queries read all of it "
                        f"({human_size(scanned)} in all, {human_money(cost)}): partition it by date with CREATE "
                        f"TABLE AS, so a query for one day reads one day (the SQL is in "
                        f"{_call('table_info', table.full_name)})")
    if size >= BIG_TABLE:
        return ("info", f"{table.full_name} has no partitions: every query reads all {human_size(size)} "
                        f"({human_money(scan_cost(size, prices))} a query). Partition it by date with CREATE "
                        f"TABLE AS (see {_call('table_info', table.full_name)})")
    return None


def _unfiltered_finding(table: Table, queries: list[Query], prices: dict[str, Any]) -> tuple[str, str] | None:
    if not table.is_partitioned:
        return None
    keys = [k.name for k in table.partition_keys]
    unfiltered = [q for q in queries if billed_bytes(q, prices) and not filters_on(q.sql, keys)]
    cost = total_cost(unfiltered, prices)
    if not unfiltered or cost < COSTLY:
        return None
    return ("warn", f"{_queries(len(unfiltered))} of {table.full_name} didn't filter on {', '.join(keys)}, "
                    f"so each read every partition ({human_money(cost)} in all): add WHERE {keys[0]} = ... "
                    f"(see {_call('query', unfiltered[0].id)})")


def _format_finding(table: Table, queries: list[Query], prices: dict[str, Any]) -> tuple[str, str] | None:
    size = table.location.size if table.location and table.location.state == "ok" else 0
    spent = total_cost(queries, prices)
    is_row_format = bool(table.format) and not is_columnar(table.format)
    if not is_row_format or (size < BIG_TABLE and spent < COSTLY):
        return None
    return ("info", f"{table.full_name} is stored as {table.format}, so every query reads whole rows: as Parquet it "
                    "would read only the columns a query names, often a tenth of the bytes. Convert it with CREATE "
                    f"TABLE AS (see {_call('table_info', table.full_name)})")


def _files_finding(table: Table) -> tuple[str, str] | None:
    location = table.location
    if location is None or location.files < _MANY_FILES:
        return None
    average = location.size / location.files
    if average >= _SMALL_FILE:
        return None
    return ("info", f"{table.full_name} holds {location.files:,}{'' if location.complete else '+'} files of "
                    f"{human_size(average)} on average: Athena opens each one, so its queries are slow. Compact them "
                    "into files of 128 MB or more (CREATE TABLE AS writes them)")


def table_findings(table: Table, queries: list[Query], prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    One table's findings: its folder is gone, empty or unreadable; it's partitioned with no partitions loaded;
    its queries read all of it (no partitions, or no filter on them); it's stored row by row; or it's many small
    files. `queries` are the ones that read it (queries_reading).
    """
    checks = (_location_finding(table), _partition_finding(table), _full_scan_finding(table, queries, prices),
              _unfiltered_finding(table, queries, prices), _format_finding(table, queries, prices),
              _files_finding(table))
    return [f for f in checks if f is not None]


def _grouped(tables: list[Table], one: str, many: str, fix: str) -> tuple[str, str]:
    """
    One warning about several tables: 'sales.x points at ...' / '3 tables point at ...', naming them, then the fix.
    """
    names = [t.full_name for t in tables]
    first = _call("table_info", names[0])
    if len(names) == 1:
        return ("warn", f"{names[0]} {one}: its queries return no rows. {fix} (see {first})")
    return ("warn", f"{len(names)} tables {many} ({_names(names)}): their queries return no rows. {fix} "
                    f"(see {first})")


def catalog_findings(catalog: Catalog, history: QueryHistory | None,
                     prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    The catalog's findings: tables that point at a missing bucket or an empty folder, partitioned tables with no
    partitions, and the tables whose queries read all of them, each kind said once with the tables it's about.
    """
    queries = history.queries if history else []
    tables = [t for t in catalog.tables if not t.is_view]
    gone = [t for t in tables if t.location and t.location.state == "no bucket"]
    empty = [t for t in tables if t.location and t.location.state == "empty"]
    unloaded = [t for t in tables if _partition_finding(t) is not None]
    findings: list[tuple[str, str]] = []
    if gone:
        findings.append(_grouped(gone, f"points at a bucket that doesn't exist ({gone[0].location_uri})",
                                 "point at a bucket that doesn't exist",
                                 "Point it at its data with ALTER TABLE SET LOCATION, or drop it"))
    if empty:
        findings.append(_grouped(empty, f"points at an empty S3 folder ({empty[0].location_uri})",
                                 "point at an empty S3 folder",
                                 "Check the path, and whether the job that writes it still runs"))
    if unloaded:
        keys = ", ".join(k.name for k in unloaded[0].partition_keys)
        findings.append(_grouped(unloaded, f"is partitioned by {keys} but the catalog has no partitions",
                                 "are partitioned but have no partitions in the catalog",
                                 f"Load them with MSCK REPAIR TABLE {unloaded[0].full_name}"))
    for table in tables:
        reading = queries_reading(queries, table.full_name)
        scans = (_full_scan_finding(table, reading, prices), _unfiltered_finding(table, reading, prices))
        findings += [f for f in scans if f is not None and f[0] == "warn"]
    return findings


def _is_stalled(crawler: Crawler, now: datetime) -> bool:
    period = schedule_period(crawler.schedule)
    if period is None or crawler.schedule_state != "SCHEDULED" or crawler.last_start is None:
        return False
    return now - crawler.last_start > 2 * period + timedelta(days=1)


def _one_crawler(crawler: Crawler, now: datetime) -> tuple[str, str] | None:
    start = f"aws glue start-crawler --name {crawler.name}"
    if crawler.last_status == "FAILED":
        error = f": {crawler.last_error}" if crawler.last_error else ""
        return ("warn", f"Crawler {crawler.name}'s last crawl failed {human_age(crawler.last_start, now)}{error}. "
                        f"Its tables miss new folders and columns until it runs again: fix that, then {start}")
    if crawler.schedule_state == "PAUSED":
        return ("warn", f"Crawler {crawler.name}'s schedule is paused, so new folders aren't added as partitions "
                        f"(last ran {human_age(crawler.last_start, now)}): aws glue start-crawler-schedule "
                        f"--crawler-name {crawler.name}")
    if _is_stalled(crawler, now):
        return ("warn", f"Crawler {crawler.name} is scheduled ({crawler.schedule}) but last ran "
                        f"{human_age(crawler.last_start, now)}: it has stopped running, so new data isn't in its "
                        f"tables. Run it with {start}, and check its schedule")
    if crawler.last_start is None:
        return ("info", f"Crawler {crawler.name} has never run, so it has made no tables: {start}")
    is_long_unused = not crawler.schedule and now - crawler.last_start > _LONG_UNUSED
    if is_long_unused:
        return ("info", f"Crawler {crawler.name} runs only when started and last ran "
                        f"{human_age(crawler.last_start, now)}: partitions added since then aren't in the catalog. "
                        f"{start}")
    return None


def crawler_findings(crawlers: list[Crawler], now: datetime) -> list[tuple[str, str]]:
    """
    Crawlers whose last crawl failed, whose schedule is paused, that are scheduled but stopped running, that never
    ran, or that run only by hand and haven't in a month.
    """
    found = [_one_crawler(c, now) for c in crawlers]
    return [f for f in found if f is not None]


def _sorted(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(findings, key=lambda f: f[0] != "warn")


def overview_findings(overview: Overview, prices: dict[str, Any], now: datetime) -> list[tuple[str, str]]:
    """
    Every finding about the region, warnings first: queries, workgroups, the catalog's tables and its crawlers.
    """
    findings: list[tuple[str, str]] = []
    if overview.history is not None:
        findings += query_findings(overview.history, prices)
    findings += workgroup_findings(overview.workgroups, overview.history, prices)
    if overview.catalog is not None:
        findings += catalog_findings(overview.catalog, overview.history, prices)
        findings += crawler_findings(overview.catalog.crawlers, now)
    return _sorted(findings)


def detail_findings(detail: QueryDetail, prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    One past query's findings: why it failed and how to fix it, that it read every column, or that it was free
    because it reused an earlier result.
    """
    query = detail.query
    findings: list[tuple[str, str]] = []
    if did_not_finish(query):
        kind = failure_kind(query.error_message)
        meaning, fix = explain_failure(kind)
        findings.append(("warn", f"It failed with {kind}, which means {meaning}. Fix: {fix}"))
    cost = query_cost(query, prices)
    if selects_every_column(query.sql) and cost >= COSTLY / 10:
        findings.append(("info", f"It read every column with SELECT * ({human_money(cost)}): naming the columns "
                                 "you need reads less from a Parquet or ORC table"))
    if query.reused:
        findings.append(("info", "It reused an earlier run's result, so it scanned nothing and cost nothing"))
    return findings


def code_findings(sql: str, tables: list[Table], prices: dict[str, Any]) -> list[tuple[str, str]]:
    """
    What to know before running a query: the most it could scan and cost (its tables' folders, when they were
    listed), a partitioned table it doesn't filter on, and SELECT * on a big table.
    """
    findings: list[tuple[str, str]] = []
    for table in tables:
        keys = [k.name for k in table.partition_keys]
        if keys and not filters_on(sql, keys):
            findings.append(("warn", f"It doesn't filter on the partition keys of {table.full_name} "
                                     f"({', '.join(keys)}), so it reads every partition: add WHERE {keys[0]} = ..."))
        size = table.location.size if table.location and table.location.state == "ok" else 0
        if not keys and size >= BIG_TABLE:
            findings.append(("info", f"{table.full_name} has no partitions, so the query reads all "
                                     f"{human_size(size)} ({human_money(scan_cost(size, prices))}) whatever it "
                                     "filters on"))
    if selects_every_column(sql):
        findings.append(("info", "SELECT * reads every column: name the ones you need, and a Parquet or ORC table "
                                 "reads only those"))
    return _sorted(findings)


def monthly(history: QueryHistory, prices: dict[str, Any]) -> float:
    """
    What queries would cost in a 30-day month at the rate of the history's window.
    """
    days = max((history.until - history.since).total_seconds() / 86_400, 1 / 24)
    return per_month(total_cost(history.queries, prices), days)
