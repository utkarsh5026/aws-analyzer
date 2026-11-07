"""
What AthenaAnalyzer returns: past queries (Query, QueryHistory, QueryShape, FailureGroup, QueryDetail), workgroups,
and the Glue Data Catalog's databases, tables (with what their S3 folder holds) and crawlers. Each list with rows has
a to_df() that loads pandas only when it's called.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require


def _frame(rows: list[dict[str, Any]], purpose: str) -> Any:
    pandas = _require("pandas", purpose)
    return pandas.DataFrame(rows)


@dataclass
class Query:
    """
    One past Athena query: its SQL, how it ended, what it scanned and how long it took (times in ms).
    """

    id: str
    sql: str
    state: str
    workgroup: str = ""
    database: str = ""
    catalog: str = ""
    statement_type: str = ""
    submitted: datetime | None = None
    completed: datetime | None = None
    scanned_bytes: int = 0
    engine_ms: int = 0
    total_ms: int = 0
    queued_ms: int = 0
    error_message: str = ""
    error_category: int = 0
    error_type: int = 0
    output_location: str = ""
    reused: bool = False
    engine_version: str = ""

    @property
    def failed(self) -> bool:
        return self.state == "FAILED"

    @property
    def seconds(self) -> float:
        return self.total_ms / 1000


@dataclass
class QueryHistory:
    """
    The queries run from `since` to `until`, newest first, from the workgroups read. truncated: a workgroup had
    more than `limit` queries in that time; errors: workgroup -> the error code that stopped its list.
    """

    since: datetime
    until: datetime
    queries: list[Query] = field(default_factory=list)
    workgroups: list[str] = field(default_factory=list)
    truncated: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self) -> Any:
        """
        Every query as a DataFrame row.
        """
        return _frame([asdict(q) for q in self.queries], "QueryHistory.to_df()")


@dataclass
class QueryShape:
    """
    Queries that are the same but for their literal values ('... WHERE day = ?'): how often they ran, what they
    scanned and cost together, and the newest run's ID.
    """

    sql: str
    runs: int = 0
    failed: int = 0
    scanned_bytes: int = 0
    cost: float = 0.0
    last: datetime | None = None
    last_id: str = ""
    workgroups: set[str] = field(default_factory=set)
    select_star: bool = False


@dataclass
class FailureGroup:
    """
    Failed queries with the same kind of error: what it is (`kind`, Athena's code when the message has one), how
    many, the newest message and query ID, and what it means and how to fix it.
    """

    kind: str
    count: int = 0
    message: str = ""
    last: datetime | None = None
    last_id: str = ""
    meaning: str = ""
    fix: str = ""
    workgroups: set[str] = field(default_factory=set)


@dataclass
class QueryResults:
    """
    A past query's result rows as Athena saved them (every value as text), at most the `limit` asked for.
    """

    columns: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    rows: list[list[str | None]] = field(default_factory=list)
    truncated: bool = False

    def to_df(self) -> Any:
        """
        The rows as a DataFrame, the columns named; values stay text, as Athena returns them.
        """
        pandas = _require("pandas", "QueryResults.to_df()")
        return pandas.DataFrame(self.rows, columns=self.columns)


@dataclass
class QueryDetail:
    """
    One past query with its runtime statistics (rows read and written, when read) and its saved results, when it
    succeeded. errors: part -> the error code that kept it from being read ('statistics', 'results').
    """

    query: Query
    rows_in: int | None = None
    rows_out: int | None = None
    results: QueryResults | None = None
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Workgroup:
    """
    An Athena workgroup: where it saves results, the most a query may scan, and whether its settings override the
    caller's.
    """

    name: str
    state: str = "ENABLED"
    description: str = ""
    engine_version: str = ""
    output_location: str = ""
    enforced: bool = False
    bytes_cutoff: int | None = None
    publishes_metrics: bool = False
    created: datetime | None = None
    error: str = ""


@dataclass
class Column:
    """
    A table column: its name, type and comment.
    """

    name: str
    type: str
    comment: str = ""


@dataclass
class Location:
    """
    What a table's S3 folder holds: 'ok', 'empty', 'no bucket' or 'denied' (or 'unchecked'), and, when it was listed
    in full or up to a limit, its files and their size (`complete` is False when it stopped at the limit).
    """

    uri: str
    state: str = "unchecked"
    files: int = 0
    size: int = 0
    complete: bool = False
    newest: datetime | None = None
    requests: int = 0


@dataclass
class Table:
    """
    A Glue Data Catalog table: its columns and partition keys, where its files are and in what format, and what the
    reports found (`partitions`, counted up to a limit when `partitions_complete` is False; `location`).
    """

    database: str
    name: str
    kind: str = "EXTERNAL_TABLE"
    location_uri: str = ""
    format: str = ""
    compression: str = ""
    columns: list[Column] = field(default_factory=list)
    partition_keys: list[Column] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    created: datetime | None = None
    updated: datetime | None = None
    view_sql: str = ""
    projection: bool = False
    partitions: int | None = None
    partitions_complete: bool = True
    newest_partitions: list[list[str]] = field(default_factory=list)
    location: Location | None = None

    @property
    def full_name(self) -> str:
        return f"{self.database}.{self.name}"

    @property
    def is_view(self) -> bool:
        return self.kind == "VIRTUAL_VIEW"

    @property
    def is_partitioned(self) -> bool:
        return bool(self.partition_keys)


@dataclass
class Database:
    """
    A Glue database and its tables. error: the code that kept its tables from being listed.
    """

    name: str
    description: str = ""
    location_uri: str = ""
    created: datetime | None = None
    tables: list[Table] = field(default_factory=list)
    error: str = ""


@dataclass
class Crawler:
    """
    A Glue crawler: what it crawls and into which database, its schedule, and how its last crawl went (with the
    tables it changed, from GetCrawlerMetrics).
    """

    name: str
    state: str = "READY"
    database: str = ""
    targets: list[str] = field(default_factory=list)
    schedule: str = ""
    schedule_state: str = ""
    last_status: str = ""
    last_start: datetime | None = None
    last_error: str = ""
    created: datetime | None = None
    tables_created: int | None = None
    tables_updated: int | None = None
    tables_deleted: int | None = None
    last_runtime_s: float | None = None
    median_runtime_s: float | None = None


@dataclass
class Catalog:
    """
    The Glue Data Catalog in a region: its databases with their tables, its crawlers, and the S3 requests made to
    check where tables point (`checked` tables, at most the limit). errors: part -> code.
    """

    region: str
    databases: list[Database] = field(default_factory=list)
    crawlers: list[Crawler] = field(default_factory=list)
    checked: int = 0
    unchecked: int = 0
    s3_requests: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def tables(self) -> list[Table]:
        return [t for d in self.databases for t in d.tables]

    def to_df(self) -> Any:
        """
        Every table as a DataFrame row: database, name, format, partition keys, location and what it holds.
        """
        rows = [{"database": t.database, "table": t.name, "kind": t.kind, "format": t.format,
                 "partition_keys": ", ".join(k.name for k in t.partition_keys), "location": t.location_uri,
                 "location_state": t.location.state if t.location else "", "updated": t.updated}
                for t in self.tables]
        return _frame(rows, "Catalog.to_df()")


@dataclass
class TableReport:
    """
    One table in detail: the table (its partitions counted, its folder listed up to a limit) and the queries in
    the history that read it.
    """

    table: Table
    history: QueryHistory | None = None
    queries: list[Query] = field(default_factory=list)
    s3_requests: int = 0
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Overview:
    """
    Athena in a region: its workgroups, the queries of the last `days` days, the Data Catalog and its crawlers.
    errors: part ('workgroups', 'queries', 'catalog', 'crawlers') -> code.
    """

    region: str
    days: int
    workgroups: list[Workgroup] = field(default_factory=list)
    history: QueryHistory | None = None
    catalog: Catalog | None = None
    errors: dict[str, str] = field(default_factory=dict)
