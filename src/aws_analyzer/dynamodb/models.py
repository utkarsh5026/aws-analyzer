"""
The dataclasses DynamoDBAnalyzer returns: a table, its indexes and metrics, a page of items, an
attribute profile and value counts, and the items as a table or DataFrame.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .._kit.deps import _require
from .._kit.fmt import Stat

from .helpers import _raw_bytes, dynamo_type, flatten_item, to_json


@dataclass
class ReadStats:
    """
    What a read did and cost.
    """

    scanned: int = 0  # items DynamoDB read (and billed), before `where` filtered them
    matched: int = 0  # items that came back (= scanned when there is no filter)
    read_units: float = (
        0.0  # capacity consumed (read request units on on-demand tables)
    )
    truncated: bool = False  # stopped before the end (limit, scan_limit or n reached)
    seconds: float = 0.0

    def add(self, other: ReadStats) -> None:
        self.scanned += other.scanned
        self.matched += other.matched
        self.read_units += other.read_units


@dataclass
class IndexInfo:
    """
    One global or local secondary index.
    """

    name: str
    kind: str  # 'global' | 'local'
    partition_key: str
    sort_key: str | None = None
    projection: str = "ALL"  # 'ALL' | 'KEYS_ONLY' | 'INCLUDE'
    projected: list[str] = field(
        default_factory=list
    )  # extra attributes of an INCLUDE projection
    status: str | None = None  # global indexes only
    backfilling: bool = False
    item_count: int | None = None
    size_bytes: int | None = None
    read_capacity: int | None = None  # provisioned tables' global indexes only
    write_capacity: int | None = None

    @property
    def keys(self) -> list[str]:
        return [self.partition_key] + ([self.sort_key] if self.sort_key else [])


@dataclass
class TableInfo:
    """
    A table's settings. item_count and size_bytes are DynamoDB's own estimates, refreshed about
    every 6 hours. Parts that couldn't be read are listed in `errors` (section -> error code).
    """

    name: str
    status: str = ""
    arn: str = ""
    partition_key: str = ""
    sort_key: str | None = None
    attribute_types: dict[str, str] = field(
        default_factory=dict
    )  # key attributes (table + indexes) -> S / N / B
    item_count: int | None = None
    size_bytes: int | None = None
    created: datetime | None = None
    billing_mode: str = "PROVISIONED"  # or 'PAY_PER_REQUEST' (on-demand)
    read_capacity: int | None = None  # provisioned tables only
    write_capacity: int | None = None
    table_class: str = "STANDARD"  # or 'STANDARD_INFREQUENT_ACCESS'
    indexes: list[IndexInfo] = field(default_factory=list)
    stream: str | None = None  # stream view type when a stream is on
    replicas: list[str] = field(default_factory=list)  # regions of a global table
    encryption: str = "AWS owned key"  # or 'KMS'
    kms_key: str | None = None
    deletion_protection: bool | None = None
    ttl_attribute: str | None = None  # filled by describe()
    ttl_status: str | None = None
    pitr: bool | None = None  # point-in-time recovery
    pitr_days: int | None = None
    pitr_earliest: datetime | None = None
    tags: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        return [self.partition_key] + ([self.sort_key] if self.sort_key else [])

    @property
    def on_demand(self) -> bool:
        return self.billing_mode == "PAY_PER_REQUEST"

    @property
    def avg_item_size(self) -> float | None:
        return (
            self.size_bytes / self.item_count
            if self.item_count and self.size_bytes is not None
            else None
        )

    def index(self, name: str) -> IndexInfo:
        for idx in self.indexes:
            if idx.name == name:
                return idx
        names = ", ".join(i.name for i in self.indexes) or "none"
        raise ValueError(f"Table {self.name} has no index {name!r} (indexes: {names})")


@dataclass
class TableMetrics:
    """
    CloudWatch usage of a table (not its indexes) over the last `hours`, in `period`-second buckets.
    """

    table: str
    hours: int
    period: int
    read_units: float | None = None  # capacity consumed in total; None = no data points
    write_units: float | None = None
    peak_reads: float | None = (
        None  # units per second, averaged over the busiest period
    )
    peak_writes: float | None = None
    read_throttles: int = 0
    write_throttles: int = 0

    @property
    def has_data(self) -> bool:
        return self.read_units is not None or self.write_units is not None


@dataclass
class TableReport:
    """
    One table in the all-tables overview.
    """

    info: TableInfo
    metrics: TableMetrics | None = None
    metrics_error: str | None = None


@dataclass
class ItemPage:
    """
    Items from one scan, query, sample or PartiQL call, and what reading them cost.
    """

    table: str
    items: list[dict[str, Any]] = field(default_factory=list)
    keys: list[str] = field(
        default_factory=list
    )  # key attributes (the index's, then the table's)
    index: str | None = None
    operation: str = "scan"  # 'scan' | 'query' | 'sample' | 'sql' | 'largest'
    stats: ReadStats = field(default_factory=ReadStats)
    last_key: Any = None  # pass back as start_key= (next_token= for sql) for the next page; None = no more

    @property
    def has_more(self) -> bool:
        return self.last_key is not None

    def to_df(self, *, flatten: bool = True):
        """
        The items as a pandas DataFrame, key attributes first (see items_to_df).
        """
        return items_to_df(self.items, self.keys, flatten=flatten)


_SIZED_TYPES = {"S", "B", "M", "L", "SS", "NS", "BS"}

DISTINCT_CAP = 10_000  # distinct values tracked per attribute while profiling


def _fingerprint(kind: str, value: Any) -> Any:
    """
    Small hashable stand-in for a value, to count distinct values without keeping big ones.
    """
    if kind == "S":
        return (kind, value if len(value) <= 64 else hash(value))
    if kind == "B":
        raw = _raw_bytes(value)
        return (kind, raw if len(raw) <= 64 else hash(raw))
    if kind in ("N", "BOOL", "NULL"):
        return (kind, value)
    return (kind, hash(to_json(value)))


@dataclass
class AttributeProfile:
    """
    How one attribute (or a field inside a map, path 'parent.child') looks across profiled items.
    """

    path: str
    name: str
    depth: int = 0  # 0 = top-level attribute, 1 = field of a map, ...
    count: int = 0  # items that have it
    types: Counter = field(default_factory=Counter)  # type code -> items
    examples: list[Any] = field(default_factory=list)  # the first few distinct values
    low: Any = None  # numbers: smallest and largest value
    high: Any = None
    min_len: int | None = (
        None  # strings, binary, maps, lists, sets: shortest and longest
    )
    max_len: int | None = None
    empty: int = 0  # '' and empty binary values
    distinct_capped: bool = False  # more than DISTINCT_CAP distinct values
    _seen: set = field(default_factory=set, repr=False, compare=False)

    @property
    def distinct(self) -> int:
        return len(self._seen)

    @property
    def main_type(self) -> str:
        return self.types.most_common(1)[0][0] if self.types else "-"

    def observe(self, value: Any, *, examples: int = 3) -> None:
        kind = dynamo_type(value)
        self.count += 1
        self.types[kind] += 1
        if kind == "N":
            self.low = value if self.low is None or value < self.low else self.low
            self.high = value if self.high is None or value > self.high else self.high
        elif kind in _SIZED_TYPES:
            length = len(_raw_bytes(value)) if kind == "B" else len(value)
            self.min_len = length if self.min_len is None else min(self.min_len, length)
            self.max_len = length if self.max_len is None else max(self.max_len, length)
            self.empty += kind in ("S", "B") and length == 0
        marker = _fingerprint(kind, value)
        if marker in self._seen:
            return
        if len(self._seen) >= DISTINCT_CAP:
            self.distinct_capped = True
            return
        self._seen.add(marker)
        if len(self.examples) < examples:
            self.examples.append(value)


@dataclass
class TableProfile:
    """
    What `profile_items` learned from a sample of items.
    """

    table: str
    index: str | None = None
    keys: list[str] = field(default_factory=list)
    items: int = 0
    total_size: int = 0
    max_size: int = 0
    attributes: dict[str, AttributeProfile] = field(
        default_factory=dict
    )  # keys first, map fields under their map
    key_patterns: dict[str, dict[str, int]] = field(
        default_factory=dict
    )  # key attribute -> shape -> items
    size_histogram: dict[str, Stat] = field(default_factory=dict)
    largest: list[tuple[int, dict[str, Any]]] = field(
        default_factory=list
    )  # (size, key values), biggest first
    approx_item_count: int | None = (
        None  # the table's own estimate, for "profiled n of ~N"
    )
    stats: ReadStats = field(default_factory=ReadStats)

    @property
    def avg_size(self) -> float:
        return self.total_size / self.items if self.items else 0.0

    def to_df(self):
        """
        One row per attribute: type(s), how many items have it, distinct values, range, examples.
        """
        pd = _require("pandas", "TableProfile.to_df")
        return pd.DataFrame(
            [
                {
                    "attribute": a.path,
                    "depth": a.depth,
                    "type": a.main_type,
                    "types": dict(a.types),
                    "items": a.count,
                    "fill_rate": a.count / self.items if self.items else 0.0,
                    "distinct": a.distinct,
                    "distinct_capped": a.distinct_capped,
                    "min": a.low,
                    "max": a.high,
                    "min_len": a.min_len,
                    "max_len": a.max_len,
                    "empty": a.empty,
                    "examples": a.examples,
                }
                for a in self.attributes.values()
            ]
        )


@dataclass
class ValueCounts:
    """
    How often each value of one attribute occurs, and the total size of those items.
    """

    attribute: str
    table: str = ""
    items: int = 0  # items looked at
    counts: dict[Any, Stat] = field(
        default_factory=dict
    )  # value -> items with it, most common first
    missing: Stat = field(default_factory=Stat)  # items without the attribute
    stats: ReadStats = field(default_factory=ReadStats)


def items_table(
    items: list[dict[str, Any]], keys: Iterable[str] = (), *, flatten: bool = True
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    (columns, rows) for showing items side by side: key attributes first, then the other attributes
    by how many items have them. With flatten=True nested maps become 'parent.child' columns.
    """
    counts: Counter = Counter()
    first_seen: dict[str, int] = {}
    for item in items:
        for name in item:
            counts[name] += 1
            first_seen.setdefault(name, len(first_seen))
    leading = [k for k in dict.fromkeys(keys) if k in counts]
    tops = leading + sorted(
        (n for n in counts if n not in leading),
        key=lambda n: (-counts[n], first_seen[n]),
    )
    if not flatten:
        return tops, list(items)
    columns: dict[str, dict[str, None]] = defaultdict(
        dict
    )  # top-level attribute -> its columns, in order
    rows = []
    for item in items:
        row: dict[str, Any] = {}
        for name, value in item.items():
            flat = flatten_item({name: value})
            columns[name].update(dict.fromkeys(flat))
            row.update(flat)
        rows.append(row)
    return [column for top in tops for column in columns[top]], rows


def items_to_df(
    items: list[dict[str, Any]], keys: Iterable[str] = (), *, flatten: bool = True
):
    """
    Items -> pandas DataFrame, key attributes first. flatten=True turns nested maps into
    'parent.child' columns; lists and sets stay as Python objects in their cell.
    """
    pd = _require("pandas", "items_to_df")
    columns, rows = items_table(items, keys, flatten=flatten)
    return pd.DataFrame(rows, columns=columns)
