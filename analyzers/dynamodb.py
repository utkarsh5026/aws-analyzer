"""
dynamodb.py - self-contained DynamoDB analysis toolkit for SageMaker / Jupyter notebooks.

Copy this one file into a notebook cell (or upload it next to your notebook and
``import dynamodb``). Nothing else from this repo is needed.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    DynamoDBAnalyzer   Pure logic. Talks to AWS and returns plain Python data
                       (dataclasses, dicts, lists, DataFrames). Never prints.
    DynamoDBView       Notebook UI. Calls DynamoDBAnalyzer and renders readable
                       cards and tables (HTML in Jupyter, plain text in a terminal).

Items come back as plain Python rather than DynamoDB JSON: numbers are int or
float (not Decimal), sets are sets, binary is bytes, and maps and lists are dicts
and lists. Nothing in this file writes to a table.

Quick start
-----------
    ui = DynamoDBView()                               # or DynamoDBView(DynamoDBAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.tables()                                       # every table: keys, items, size, cost, warnings
    ui.table_info("orders")                           # keys, indexes and how to query each, capacity, backups
    ui.scan("orders")                                 # the first 20 items as a table...
    ui.more()                                         # ...and the next 20
    ui.schema("orders")                               # every attribute: types, fill rate, examples, key patterns
    ui.get("orders", "USER#42", "ORDER#0017")         # one item, nested maps and lists expanded
    ui.query("orders", "USER#42", sort=("begins_with", "ORDER#"))
    ui.scan("orders", where={"status": "failed", "total": (">", 100)})
    ui.value_counts("orders", "status")

    ddb = ui.core                                     # same analyzer, raw data
    df = ddb.scan("orders", n=5000).to_df()           # nested maps become 'address.city' columns
    items = ddb.query("orders", "USER#42").items      # list of plain dicts
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import functools
import heapq
import html
import importlib
import inspect
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Iterator

import boto3
from boto3.dynamodb.conditions import Attr, ConditionBase, ConditionExpressionBuilder, Key
from boto3.dynamodb.types import Binary, TypeSerializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

# =============================================================================
# 1. Helpers: conversion and formatting
# =============================================================================

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4
ITEM_SIZE_LIMIT = 400 * KB  # DynamoDB rejects bigger items
HOURS_PER_MONTH = 730

TYPE_NAMES = {
    "S": "string", "N": "number", "B": "binary", "BOOL": "boolean", "NULL": "null",
    "M": "map", "L": "list", "SS": "string set", "NS": "number set", "BS": "binary set",
}

# USD, us-east-1 list prices for the standard table class, before the free tier. Other regions
# differ; pass DynamoDBAnalyzer(prices={...}) to use your own.
DYNAMODB_PRICES: dict[str, float] = {
    "read_request": 0.125,  # per million on-demand read request units
    "write_request": 0.625,  # per million on-demand write request units
    "read_capacity_hour": 0.00013,  # per provisioned read capacity unit, per hour
    "write_capacity_hour": 0.00065,  # per provisioned write capacity unit, per hour
    "storage": 0.25,  # per GB-month of table and index data
    "storage_ia": 0.10,  # per GB-month, standard-infrequent-access table class
    "pitr": 0.20,  # per GB-month of point-in-time recovery
}


def human_size(num_bytes: float | None) -> str:
    """1536 -> '1.5 KB' (binary units, like the AWS console)."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def human_age(when: datetime | None, now: datetime | None = None) -> str:
    """datetime -> '3d ago' / '5mo ago' / 'just now'."""
    if when is None:
        return "-"
    seconds = ((now or _utcnow()) - when).total_seconds()
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def human_money(usd: float | None) -> str:
    """12.345 -> '$12.35', 0.004 -> '<$0.01', 12345.6 -> '$12,346', -3 -> '-$3.00'."""
    if usd is None:
        return "-"
    sign, usd = ("-" if usd < 0 else ""), abs(usd)
    if 0 < usd < 0.01:
        return f"{sign}<$0.01"
    return f"{sign}${usd:,.0f}" if usd >= 1000 else f"{sign}${usd:,.2f}"


def _units(value: float | None) -> str:
    """Capacity units for display: 12.0 -> '12', 0.5 -> '0.5', 14457041.1 -> '14,457,041'."""
    if value is None:
        return "-"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}".removesuffix(".0")


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _require(module: str, purpose: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"{purpose} needs `{module.split('.')[0]}` (pip install {module.split('.')[0]})") from exc


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


def _error_name(exc: ClientError | BotoCoreError) -> str:
    return _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__


def _why(code: str, permission: str) -> str:
    """'AccessDeniedException' -> 'AccessDeniedException; needs dynamodb:Scan'. Other codes stay as they are."""
    return f"{code}; needs {permission}" if "denied" in code.lower() or code == "UnauthorizedOperation" else code


_COUNT_RE = re.compile(r"^\s*(\d[\d,_]*(?:\.\d+)?)\s*([km]?)\s*$", re.IGNORECASE)


def _as_int(value: Any, name: str, *, hint: str = "") -> int:
    """A number-of-items argument: 1000, '10,000', '10k' or '2m' -> int."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        return int(value)
    match = _COUNT_RE.match(value) if isinstance(value, str) else None
    if match:
        number = float(match.group(1).replace(",", "").replace("_", "")) * {"": 1, "k": 1000, "m": 10**6}[
            match.group(2).lower()]
        if number.is_integer():
            return int(number)
    raise ValueError(f"{name} takes a number of items, like 1000 or '10k'{hint}; got {value!r}")


def _as_count(value: Any, name: str) -> int | None:
    """Like _as_int, for limits where None means no limit."""
    return None if value is None else _as_int(value, name, hint=", or None for no limit")


def _clip(text: str, width: int = 90) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _number(text: str) -> int | float:
    """DynamoDB number string -> int when it's whole, else float: '42' -> 42, '1.50' -> 1.5."""
    value = Decimal(text)
    return int(value) if value == value.to_integral_value() else float(value)


def _binary(raw: Any) -> bytes:
    return bytes(raw) if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)  # exports hold base64 text


def _raw_bytes(value: Any) -> bytes:
    return bytes(value.value) if isinstance(value, Binary) else bytes(value)


def from_dynamo(value: dict[str, Any]) -> Any:
    """One DynamoDB-JSON value -> plain Python: {'N': '42'} -> 42, {'SS': ['a']} -> {'a'},
    {'M': {'city': {'S': 'Pune'}}} -> {'city': 'Pune'}."""
    [(kind, raw)] = value.items()
    if kind == "M":
        return {name: from_dynamo(child) for name, child in raw.items()}
    if kind == "L":
        return [from_dynamo(child) for child in raw]
    if kind == "N":
        return _number(raw)
    if kind == "NS":
        return {_number(n) for n in raw}
    if kind == "SS":
        return set(raw)
    if kind == "B":
        return _binary(raw)
    if kind == "BS":
        return {_binary(b) for b in raw}
    if kind == "NULL":
        return None
    return raw  # S, BOOL


def from_dynamo_item(item: dict[str, dict]) -> dict[str, Any]:
    """A whole item in DynamoDB JSON (the low-level API, or a line of an S3 export) -> plain dict."""
    return {name: from_dynamo(value) for name, value in item.items()}


_SERIALIZER = TypeSerializer()


def _decimals(value: Any) -> Any:
    """floats -> Decimal, recursively (boto3 refuses floats)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _decimals(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimals(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return {_decimals(v) for v in value}
    return value


def to_dynamo(value: Any) -> dict[str, Any]:
    """Plain Python -> DynamoDB-JSON value: 42 -> {'N': '42'}, 'x' -> {'S': 'x'}. Floats are fine."""
    try:
        return _SERIALIZER.serialize(_decimals(value))
    except TypeError as exc:
        raise ValueError(f"DynamoDB can't store a {type(value).__name__} ({exc}). Dates are usually stored as "
                         "ISO strings or epoch numbers: pass the form your table uses.") from exc


def dynamo_type(value: Any) -> str:
    """DynamoDB type code of a plain Python value: 'S', 'N', 'B', 'BOOL', 'NULL', 'M', 'L', 'SS', 'NS' or 'BS'."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, (int, float, Decimal)):
        return "N"
    if isinstance(value, str):
        return "S"
    if isinstance(value, (bytes, bytearray, Binary)):
        return "B"
    if isinstance(value, dict):
        return "M"
    if isinstance(value, (list, tuple)):
        return "L"
    if isinstance(value, (set, frozenset)):
        return dynamo_type(next(iter(value), "")) + "S"
    return type(value).__name__


def _number_size(value: Any) -> int:
    digits = Decimal(str(value)).normalize().as_tuple().digits  # significant digits, trailing zeros dropped
    return math.ceil(len(digits) / 2) + 1


def value_size(value: Any) -> int:
    """Bytes DynamoDB counts for one attribute value (without its name), following its sizing rules."""
    if value is None or isinstance(value, bool):
        return 1
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (int, float, Decimal)):
        return _number_size(value)
    if isinstance(value, (bytes, bytearray, Binary)):
        return len(_raw_bytes(value))
    if isinstance(value, dict):  # 3 bytes per map, 1 per element, plus each element's name and value
        return 3 + sum(len(str(k).encode("utf-8")) + value_size(v) + 1 for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return 3 + sum(value_size(v) + 1 for v in value)
    if isinstance(value, (set, frozenset)):
        return sum(value_size(v) for v in value)
    return len(str(value).encode("utf-8"))


def item_size(item: dict[str, Any]) -> int:
    """Approximate item size as DynamoDB counts it (attribute names + values). The limit is 400 KB;
    a read unit covers 4 KB (strongly consistent) and a write unit 1 KB."""
    return sum(len(name.encode("utf-8")) + value_size(value) for name, value in item.items())


def read_units(size: int, *, consistent: bool = True) -> float:
    """Capacity units to read one item of `size` bytes with GetItem (eventually consistent is half)."""
    units = max(1, math.ceil(size / (4 * KB)))
    return units if consistent else units / 2


def write_units(size: int) -> int:
    """Capacity units to write one item of `size` bytes."""
    return max(1, math.ceil(size / KB))


def _plain(value: Any) -> Any:
    """JSON-friendly copy: sets -> sorted lists, binary -> base64 text."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_plain(v) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=str)
    if isinstance(value, (bytes, bytearray, Binary)):
        return base64.b64encode(_raw_bytes(value)).decode()
    if isinstance(value, Decimal):
        return _number(str(value))
    return value


def to_json(value: Any, *, indent: int | None = None) -> str:
    """Item or value -> JSON text (sets become sorted lists, binary becomes base64)."""
    return json.dumps(_plain(value), indent=indent, ensure_ascii=False, default=str)


def format_value(value: Any, width: int = 80, *, oneline: bool = True) -> str:
    """Short display text for one attribute value: strings as they are ('' shows as ""), maps,
    lists and sets as compact JSON, binary as base64."""
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, str):
        text = value or '""'
    elif isinstance(value, (bytes, bytearray, Binary)):
        text = "b64:" + base64.b64encode(_raw_bytes(value)).decode()
    elif isinstance(value, (dict, list, tuple, set, frozenset)):
        text = to_json(value)
    else:
        text = str(value)
    if oneline:
        text = text.replace("\r\n", " ↵ ").replace("\n", " ↵ ")
    return _clip(text, width)


def flatten_item(item: dict[str, Any], *, max_depth: int | None = None) -> dict[str, Any]:
    """Nested maps -> dotted columns: {'address': {'city': 'Pune'}} -> {'address.city': 'Pune'}.
    Lists, sets and empty maps stay as values."""
    flat: dict[str, Any] = {}

    def walk(path: str, value: Any, depth: int) -> None:
        if isinstance(value, dict) and value and (max_depth is None or depth < max_depth):
            for name, child in value.items():
                walk(f"{path}.{name}", child, depth + 1)
        else:
            flat[path] = value

    for name, value in item.items():
        walk(name, value, 0)
    return flat


_MISSING = object()


def get_path(item: dict[str, Any], path: str, default: Any = None) -> Any:
    """item['address']['city'] for path 'address.city'. An attribute literally named 'address.city' wins."""
    if path in item:
        return item[path]
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


# =============================================================================
# 2. Data models (what DynamoDBAnalyzer returns)
# =============================================================================


@dataclass
class Stat:
    """Running item count + total bytes."""

    count: int = 0
    size: int = 0

    def add(self, size: int) -> None:
        self.count += 1
        self.size += size


@dataclass
class ReadStats:
    """What a read did and cost."""

    scanned: int = 0  # items DynamoDB read (and billed), before `where` filtered them
    matched: int = 0  # items that came back (= scanned when there is no filter)
    read_units: float = 0.0  # capacity consumed (read request units on on-demand tables)
    truncated: bool = False  # stopped before the end (limit, scan_limit or n reached)
    seconds: float = 0.0

    def add(self, other: ReadStats) -> None:
        self.scanned += other.scanned
        self.matched += other.matched
        self.read_units += other.read_units


@dataclass
class IndexInfo:
    """One global or local secondary index."""

    name: str
    kind: str  # 'global' | 'local'
    partition_key: str
    sort_key: str | None = None
    projection: str = "ALL"  # 'ALL' | 'KEYS_ONLY' | 'INCLUDE'
    projected: list[str] = field(default_factory=list)  # extra attributes of an INCLUDE projection
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
    """A table's settings. item_count and size_bytes are DynamoDB's own estimates, refreshed about
    every 6 hours. Parts that couldn't be read are listed in `errors` (section -> error code)."""

    name: str
    status: str = ""
    arn: str = ""
    partition_key: str = ""
    sort_key: str | None = None
    attribute_types: dict[str, str] = field(default_factory=dict)  # key attributes (table + indexes) -> S / N / B
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
        return self.size_bytes / self.item_count if self.item_count and self.size_bytes is not None else None

    def index(self, name: str) -> IndexInfo:
        for idx in self.indexes:
            if idx.name == name:
                return idx
        names = ", ".join(i.name for i in self.indexes) or "none"
        raise ValueError(f"Table {self.name} has no index {name!r} (indexes: {names})")


@dataclass
class TableMetrics:
    """CloudWatch usage of a table (not its indexes) over the last `hours`, in `period`-second buckets."""

    table: str
    hours: int
    period: int
    read_units: float | None = None  # capacity consumed in total; None = no data points
    write_units: float | None = None
    peak_reads: float | None = None  # units per second, averaged over the busiest period
    peak_writes: float | None = None
    read_throttles: int = 0
    write_throttles: int = 0

    @property
    def has_data(self) -> bool:
        return self.read_units is not None or self.write_units is not None


@dataclass
class TableReport:
    """One table in the all-tables overview."""

    info: TableInfo
    metrics: TableMetrics | None = None
    metrics_error: str | None = None


@dataclass
class ItemPage:
    """Items from one scan, query, sample or PartiQL call, and what reading them cost."""

    table: str
    items: list[dict[str, Any]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)  # key attributes (the index's, then the table's)
    index: str | None = None
    operation: str = "scan"  # 'scan' | 'query' | 'sample' | 'sql' | 'largest'
    stats: ReadStats = field(default_factory=ReadStats)
    last_key: Any = None  # pass back as start_key= (next_token= for sql) for the next page; None = no more

    @property
    def has_more(self) -> bool:
        return self.last_key is not None

    def to_df(self, *, flatten: bool = True):
        """The items as a pandas DataFrame, key attributes first (see items_to_df)."""
        return items_to_df(self.items, self.keys, flatten=flatten)


_SIZED_TYPES = {"S", "B", "M", "L", "SS", "NS", "BS"}
DISTINCT_CAP = 10_000  # distinct values tracked per attribute while profiling


def _fingerprint(kind: str, value: Any) -> Any:
    """Small hashable stand-in for a value, to count distinct values without keeping big ones."""
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
    """How one attribute (or a field inside a map, path 'parent.child') looks across profiled items."""

    path: str
    name: str
    depth: int = 0  # 0 = top-level attribute, 1 = field of a map, ...
    count: int = 0  # items that have it
    types: Counter = field(default_factory=Counter)  # type code -> items
    examples: list[Any] = field(default_factory=list)  # the first few distinct values
    low: Any = None  # numbers: smallest and largest value
    high: Any = None
    min_len: int | None = None  # strings, binary, maps, lists, sets: shortest and longest
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
    """What `profile_items` learned from a sample of items."""

    table: str
    index: str | None = None
    keys: list[str] = field(default_factory=list)
    items: int = 0
    total_size: int = 0
    max_size: int = 0
    attributes: dict[str, AttributeProfile] = field(default_factory=dict)  # keys first, map fields under their map
    key_patterns: dict[str, dict[str, int]] = field(default_factory=dict)  # key attribute -> shape -> items
    size_histogram: dict[str, Stat] = field(default_factory=dict)
    largest: list[tuple[int, dict[str, Any]]] = field(default_factory=list)  # (size, key values), biggest first
    approx_item_count: int | None = None  # the table's own estimate, for "profiled n of ~N"
    stats: ReadStats = field(default_factory=ReadStats)

    @property
    def avg_size(self) -> float:
        return self.total_size / self.items if self.items else 0.0

    def to_df(self):
        """One row per attribute: type(s), how many items have it, distinct values, range, examples."""
        pd = _require("pandas", "TableProfile.to_df")
        return pd.DataFrame([{
            "attribute": a.path, "depth": a.depth, "type": a.main_type, "types": dict(a.types), "items": a.count,
            "fill_rate": a.count / self.items if self.items else 0.0, "distinct": a.distinct,
            "distinct_capped": a.distinct_capped, "min": a.low, "max": a.high, "min_len": a.min_len,
            "max_len": a.max_len, "empty": a.empty, "examples": a.examples,
        } for a in self.attributes.values()])


@dataclass
class ValueCounts:
    """How often each value of one attribute occurs, and the total size of those items."""

    attribute: str
    table: str = ""
    items: int = 0  # items looked at
    counts: dict[Any, Stat] = field(default_factory=dict)  # value -> items with it, most common first
    missing: Stat = field(default_factory=Stat)  # items without the attribute
    stats: ReadStats = field(default_factory=ReadStats)


def items_table(items: list[dict[str, Any]], keys: Iterable[str] = (), *,
                flatten: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
    """(columns, rows) for showing items side by side: key attributes first, then the other attributes
    by how many items have them. With flatten=True nested maps become 'parent.child' columns."""
    counts: Counter = Counter()
    first_seen: dict[str, int] = {}
    for item in items:
        for name in item:
            counts[name] += 1
            first_seen.setdefault(name, len(first_seen))
    leading = [k for k in dict.fromkeys(keys) if k in counts]
    tops = leading + sorted((n for n in counts if n not in leading), key=lambda n: (-counts[n], first_seen[n]))
    if not flatten:
        return tops, list(items)
    columns: dict[str, dict[str, None]] = defaultdict(dict)  # top-level attribute -> its columns, in order
    rows = []
    for item in items:
        row: dict[str, Any] = {}
        for name, value in item.items():
            flat = flatten_item({name: value})
            columns[name].update(dict.fromkeys(flat))
            row.update(flat)
        rows.append(row)
    return [column for top in tops for column in columns[top]], rows


def items_to_df(items: list[dict[str, Any]], keys: Iterable[str] = (), *, flatten: bool = True):
    """Items -> pandas DataFrame, key attributes first. flatten=True turns nested maps into
    'parent.child' columns; lists and sets stay as Python objects in their cell."""
    pd = _require("pandas", "items_to_df")
    columns, rows = items_table(items, keys, flatten=flatten)
    return pd.DataFrame(rows, columns=columns)


# =============================================================================
# 3. Pure analysis (no AWS calls - works on any list of plain item dicts)
# =============================================================================

ITEM_SIZE_BANDS: list[tuple[str, int | None]] = [  # (label, exclusive upper bound in bytes)
    ("< 1 KB", KB),
    ("1 - 4 KB", 4 * KB),
    ("4 - 16 KB", 16 * KB),
    ("16 - 100 KB", 100 * KB),
    ("100 - 300 KB", 300 * KB),
    ("300 - 400 KB", None),
]


def _band(value: float, bands: list[tuple[str, int | None]]) -> str:
    for label, upper in bands:
        if upper is None or value < upper:
            return label
    return bands[-1][0]


_KEY_SEPARATORS = re.compile(r"([#|])")
_SEGMENT_SHAPES = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE), "<uuid>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}([T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?)?"), "<date>"),
    (re.compile(r"[+-]?\d+(\.\d+)?"), "<number>"),
    (re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"), "<email>"),
]
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{0,31}")


def _segment_shape(part: str, literal_ok: bool) -> str:
    if not part:
        return ""
    for regex, label in _SEGMENT_SHAPES:
        if regex.fullmatch(part):
            return label
    if _WORD_RE.fullmatch(part) and (literal_ok or part.isupper()):
        return part  # an entity name like USER or PROFILE
    return "<id>" if any(c.isdigit() for c in part) else "<text>"


def key_pattern(value: Any) -> str:
    """Shape of a key value, to spot the entity types of a single-table design:
    'USER#42' -> 'USER#<number>', 'ORDER#2024-05-01#a1b2' -> 'ORDER#<date>#<id>', 'PROFILE' -> 'PROFILE'."""
    if not isinstance(value, str):
        return {"N": "<number>", "B": "<binary>"}.get(dynamo_type(value), f"<{dynamo_type(value)}>")
    parts = _KEY_SEPARATORS.split(value)
    return "".join(part if part in ("#", "|") else _segment_shape(part, len(parts) > 1 and i == 0)
                   for i, part in enumerate(parts))


def profile_items(items: Iterable[dict[str, Any]], table: str = "", *, keys: Iterable[str] = (),
                  max_depth: int = 2, top_n: int = 10) -> TableProfile:
    """One pass over items -> TableProfile: every attribute (and map field down to max_depth) with its
    types, fill rate, distinct values, range and examples, plus key shapes and item sizes.
    Works on any plain dicts, e.g. from_dynamo_item() rows of a DynamoDB export in S3."""
    keys = list(dict.fromkeys(keys))
    profile = TableProfile(table=table, keys=keys)
    found: dict[str, AttributeProfile] = {}
    children: dict[str | None, list[str]] = defaultdict(list)  # parent path -> child paths, first seen first
    patterns: dict[str, Counter] = {k: Counter() for k in keys}
    sizes = {label: Stat() for label, _ in ITEM_SIZE_BANDS}
    largest: list[tuple[int, int, dict[str, Any]]] = []  # min-heap of the top_n biggest

    def visit(path: str, name: str, parent: str | None, value: Any, depth: int) -> None:
        attr = found.get(path)
        if attr is None:
            attr = found[path] = AttributeProfile(path, name, depth)
            children[parent].append(path)
        attr.observe(value)
        if isinstance(value, dict) and depth < max_depth:
            for child_name, child in value.items():
                visit(f"{path}.{child_name}", str(child_name), path, child, depth + 1)

    for i, item in enumerate(items):
        size = item_size(item)
        profile.items += 1
        profile.total_size += size
        profile.max_size = max(profile.max_size, size)
        sizes[_band(size, ITEM_SIZE_BANDS)].add(size)
        for name, value in item.items():
            visit(name, name, None, value, 0)
        for k in keys:
            if k in item:
                patterns[k][key_pattern(item[k])] += 1
        if top_n > 0:
            entry = (size, i, {k: item[k] for k in keys if k in item})
            if len(largest) < top_n:
                heapq.heappush(largest, entry)
            elif size > largest[0][0]:
                heapq.heapreplace(largest, entry)

    def ordered(parent: str | None) -> Iterator[str]:
        paths = children[parent]
        rank = {path: j for j, path in enumerate(paths)}
        lead = [k for k in keys if k in rank] if parent is None else []
        for path in lead + sorted((p for p in paths if p not in lead), key=lambda p: (-found[p].count, rank[p])):
            yield path
            yield from ordered(path)

    profile.attributes = {path: found[path] for path in ordered(None)}
    profile.key_patterns = {k: dict(c.most_common()) for k, c in patterns.items() if c}
    profile.size_histogram = sizes
    profile.largest = [(size, key) for size, _, key in sorted(largest, key=lambda e: (-e[0], e[1]))]
    return profile


def _hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    return to_json(value)


def count_values(items: Iterable[dict[str, Any]], attribute: str) -> ValueCounts:
    """How many items hold each value of `attribute` (a dotted path reaches into maps), and their
    total size. Maps, lists and sets are counted by their JSON text."""
    result = ValueCounts(attribute)
    counts: dict[Any, Stat] = defaultdict(Stat)
    for item in items:
        result.items += 1
        size = item_size(item)
        value = get_path(item, attribute, _MISSING)
        if value is _MISSING:
            result.missing.add(size)
        else:
            counts[_hashable(value)].add(size)
    result.counts = dict(sorted(counts.items(), key=lambda kv: (-kv[1].count, -kv[1].size)))
    return result


_OPERATORS = {
    "=": "eq", "==": "eq", "!=": "ne", "<>": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
    "between": "between", "begins_with": "begins_with", "contains": "contains", "in": "is_in",
    "exists": "exists", "not_exists": "not_exists", "type": "attribute_type",
}
_ARITY = {"between": 2, "exists": 0, "not_exists": 0}  # everything else takes one value
_SORT_KEY_OPERATORS = {"eq", "lt", "lte", "gt", "gte", "between", "begins_with"}


def _condition(attr: Attr | Key, spec: Any, *, key: bool = False) -> ConditionBase:
    """A plain value (equality) or an (operator, *values) tuple -> a boto3 condition on `attr`."""
    if not isinstance(spec, tuple):
        return attr.eq(spec)
    op = spec[0] if spec and isinstance(spec[0], str) else None
    if op is None or op.lower() not in _OPERATORS:
        raise ValueError(f"Can't read condition {spec!r}: use a value, or (operator, value) with one of "
                         + ", ".join(_OPERATORS))
    method, args = _OPERATORS[op.lower()], list(spec[1:])
    if key and method not in _SORT_KEY_OPERATORS:
        raise ValueError(f"Sort key conditions can be =, <, <=, >, >=, between or begins_with, not {op!r}")
    if method == "is_in":
        args = [list(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple, set, frozenset)) else args]
    if len(args) != _ARITY.get(method, 1):
        raise ValueError(f"{op!r} takes {_plural(_ARITY.get(method, 1), 'value')}, got {spec!r}")
    return getattr(attr, method)(*args)


def build_filter(where: Any) -> ConditionBase | None:
    """`where` -> a boto3 condition. Takes None, a boto3 condition (Attr('a').gt(1) | Attr('b').exists()),
    or a dict of attribute -> value or (operator, *values), which must all match:

        {'status': 'failed'}                    status = 'failed'
        {'total': ('>', 100)}                   also '=', '!=', '<', '<=', '>=', ('between', low, high)
        {'sk': ('begins_with', 'ORDER#')}       also ('contains', x), ('in', [a, b, c]), ('type', 'N')
        {'deleted_at': ('not_exists',)}         or ('exists',)
        {'address.city': 'Pune'}                dots reach into maps
    """
    if where is None or isinstance(where, ConditionBase):
        return where
    if not isinstance(where, dict):
        raise ValueError("where= takes a dict like {'status': 'failed'} or a boto3 condition")
    conditions = [_condition(Attr(name), spec) for name, spec in where.items()]
    return functools.reduce(lambda a, b: a & b, conditions) if conditions else None


def describe_condition(name: str, spec: Any) -> str:
    """('total', ('>', 100)) -> 'total > 100', ('sk', ('between', 'a', 'c')) -> "sk between 'a' and 'c'"."""
    if not isinstance(spec, tuple):
        return f"{name} = {spec!r}"
    if not spec:
        return f"{name} {spec!r}"
    op, *args = spec
    joiner = " and " if op == "between" else ", "
    return f"{name} {op}" + (" " + joiner.join(map(repr, args)) if args else "")


def describe_filter(where: Any) -> str:
    """`where` as text: {'status': 'failed', 'total': ('>', 100)} -> "status = 'failed', total > 100"."""
    if where is None:
        return ""
    if isinstance(where, ConditionBase):
        return "a boto3 condition"
    return ", ".join(describe_condition(name, spec) for name, spec in where.items())


_PATH_PART_RE = re.compile(r"^([^\[\]]+)((?:\[\d+\])*)$")


def _path_expression(path: str, names: dict[str, str]) -> str:
    """'address.city' -> '#p0.#p1' (placeholders avoid clashes with DynamoDB's reserved words)."""
    parts = []
    for part in path.split("."):
        match = _PATH_PART_RE.match(part)
        if not match:
            raise ValueError(f"Can't read attribute path {path!r}")
        placeholder = f"#p{len(names)}"
        names[placeholder] = match.group(1)
        parts.append(placeholder + match.group(2))
    return ".".join(parts)


def _expression_params(*, key: ConditionBase | None = None, where: ConditionBase | None = None,
                       attributes: Iterable[str] | None = None) -> dict[str, Any]:
    """KeyConditionExpression / FilterExpression / ProjectionExpression with shared placeholders."""
    builder = ConditionExpressionBuilder()
    params: dict[str, Any] = {}
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for name, condition, is_key in (("KeyConditionExpression", key, True), ("FilterExpression", where, False)):
        if condition is not None:
            built = builder.build_expression(condition, is_key_condition=is_key)
            params[name] = built.condition_expression
            names.update(built.attribute_name_placeholders)
            values.update({k: to_dynamo(v) for k, v in built.attribute_value_placeholders.items()})
    if attributes:
        params["ProjectionExpression"] = ", ".join(_path_expression(path, names) for path in attributes)
    if names:
        params["ExpressionAttributeNames"] = names
    if values:
        params["ExpressionAttributeValues"] = values
    return params


def parse_table(desc: dict[str, Any]) -> TableInfo:
    """A DescribeTable 'Table' dict -> TableInfo (TTL, backups and tags need their own calls: see describe)."""
    keys = {k["KeyType"]: k["AttributeName"] for k in desc.get("KeySchema", [])}
    info = TableInfo(
        name=desc["TableName"],
        status=desc.get("TableStatus", ""),
        arn=desc.get("TableArn", ""),
        partition_key=keys.get("HASH", ""),
        sort_key=keys.get("RANGE"),
        attribute_types={a["AttributeName"]: a["AttributeType"] for a in desc.get("AttributeDefinitions", [])},
        item_count=desc.get("ItemCount"),
        size_bytes=desc.get("TableSizeBytes"),
        created=desc.get("CreationDateTime"),
        billing_mode=desc.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED"),
        table_class=desc.get("TableClassSummary", {}).get("TableClass", "STANDARD"),
        deletion_protection=desc.get("DeletionProtectionEnabled"),
        replicas=[r["RegionName"] for r in desc.get("Replicas", [])],
    )
    if not info.on_demand:
        throughput = desc.get("ProvisionedThroughput", {})
        info.read_capacity, info.write_capacity = throughput.get("ReadCapacityUnits"), throughput.get("WriteCapacityUnits")
    stream = desc.get("StreamSpecification", {})
    if stream.get("StreamEnabled"):
        info.stream = stream.get("StreamViewType", "on")
    sse = desc.get("SSEDescription") or {}
    if sse.get("Status") in ("ENABLED", "UPDATING") and sse.get("SSEType") == "KMS":
        info.encryption, info.kms_key = "KMS", sse.get("KMSMasterKeyArn")
    for kind, section in (("global", "GlobalSecondaryIndexes"), ("local", "LocalSecondaryIndexes")):
        for idx in desc.get(section) or []:
            index_keys = {k["KeyType"]: k["AttributeName"] for k in idx.get("KeySchema", [])}
            projection = idx.get("Projection", {})
            throughput = idx.get("ProvisionedThroughput", {}) if kind == "global" and not info.on_demand else {}
            info.indexes.append(IndexInfo(
                name=idx["IndexName"], kind=kind, partition_key=index_keys.get("HASH", ""),
                sort_key=index_keys.get("RANGE"), projection=projection.get("ProjectionType", "ALL"),
                projected=projection.get("NonKeyAttributes", []), status=idx.get("IndexStatus"),
                backfilling=bool(idx.get("Backfilling")), item_count=idx.get("ItemCount"),
                size_bytes=idx.get("IndexSizeBytes"), read_capacity=throughput.get("ReadCapacityUnits"),
                write_capacity=throughput.get("WriteCapacityUnits")))
    return info


def table_monthly_cost(info: TableInfo, prices: dict[str, float] | None = None,
                       metrics: TableMetrics | None = None) -> dict[str, float]:
    """Estimated USD per month: 'storage' (table + indexes), 'capacity' (provisioned tables: table and
    global indexes at today's settings), 'requests' (on-demand tables, given `metrics`: the reads and
    writes of that window, extended to a month) and 'backup' (point-in-time recovery, when on)."""
    prices = DYNAMODB_PRICES if prices is None else prices
    stored = ((info.size_bytes or 0) + sum(i.size_bytes or 0 for i in info.indexes)) / GB
    cost = {"storage": stored * prices["storage_ia" if info.table_class == "STANDARD_INFREQUENT_ACCESS" else "storage"]}
    if not info.on_demand and info.read_capacity is not None:
        global_indexes = [i for i in info.indexes if i.kind == "global"]
        reads = info.read_capacity + sum(i.read_capacity or 0 for i in global_indexes)
        writes = (info.write_capacity or 0) + sum(i.write_capacity or 0 for i in global_indexes)
        cost["capacity"] = capacity_cost(reads, writes, prices)
    if info.on_demand and metrics is not None and metrics.has_data:
        spent = request_cost(metrics.read_units or 0.0, metrics.write_units or 0.0, prices)
        cost["requests"] = spent * HOURS_PER_MONTH / metrics.hours
    if info.pitr:
        cost["backup"] = (info.size_bytes or 0) / GB * prices["pitr"]
    return cost


def request_cost(read_units: float = 0.0, write_units: float = 0.0, prices: dict[str, float] | None = None) -> float:
    """USD for on-demand reads and writes (request units, e.g. ReadStats.read_units)."""
    prices = DYNAMODB_PRICES if prices is None else prices
    return (read_units * prices["read_request"] + write_units * prices["write_request"]) / 1e6


def capacity_cost(read_capacity: float = 0, write_capacity: float = 0, prices: dict[str, float] | None = None) -> float:
    """USD per month for provisioned read and write capacity units."""
    prices = DYNAMODB_PRICES if prices is None else prices
    return HOURS_PER_MONTH * (read_capacity * prices["read_capacity_hour"] + write_capacity * prices["write_capacity_hour"])


TARGET_USE = 0.7  # suggested capacity leaves the busiest period at 70% of it (auto scaling's default target)

_LARGE_ITEM_ADVICE = ("Keeping large attributes (documents, blobs, long text) in S3 with a pointer in the item, or "
                      "compressing them, cuts the cost of every read.")

# describe() section -> (what it is, the permission that reads it)
_SECTIONS = {
    "describe": ("the table", "dynamodb:DescribeTable"),
    "ttl": ("time to live", "dynamodb:DescribeTimeToLive"),
    "pitr": ("point-in-time recovery", "dynamodb:DescribeContinuousBackups"),
    "tags": ("tags", "dynamodb:ListTagsOfResource"),
}


def _table_ref(table: str) -> str:
    return repr(table) if table else "<table>"


def profile_findings(profile: TableProfile) -> list[tuple[str, str]]:
    """Plain-language data-quality and cost notes about profiled items -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    if not profile.items:
        return found
    table = _table_ref(profile.table)
    for attr in profile.attributes.values():
        kinds = [(kind, n) for kind, n in attr.types.most_common() if kind != "NULL"]
        if len(kinds) > 1:
            mix = ", ".join(f"{TYPE_NAMES.get(kind, kind)} in {n:,}" for kind, n in kinds)
            odd = kinds[-1][0]
            found.append(("warn", f"'{attr.path}' holds different types ({mix} items). A filter or key condition "
                                  "matches one type only, so the other items silently drop out. Find the "
                                  f"{TYPE_NAMES.get(odd, odd)} ones with "
                                  f"scan({table}, where={{{attr.path!r}: ('type', {odd!r})}})."))
    empties = [a for a in profile.attributes.values() if a.empty]
    if empties:
        listed = ", ".join(f"'{a.path}' ({_plural(a.empty, 'item')})" for a in empties[:8])
        found.append(("info", f"Empty strings or binary values in {listed}. They count as present, so "
                              f"where={{{empties[0].path!r}: ('exists',)}} matches them too; if they mean "
                              "\"unknown\", add ('!=', '') to filters."))
    big = profile.size_histogram.get("300 - 400 KB", Stat()).count
    if big:
        found.append(("warn", f"{_plural(big, 'item')} over 300 KB. DynamoDB rejects items over 400 KB, and one "
                              "strongly consistent read of an item that size costs up to 100 read units. "
                              f"largest({table}) lists them. {_LARGE_ITEM_ADVICE}"))
    if profile.avg_size > 4 * KB:
        units = read_units(math.ceil(profile.avg_size))
        found.append(("info", f"The average item is {human_size(profile.avg_size)}, so reading one costs "
                              f"{_units(units)} read units (half that eventually consistent). Reading fewer "
                              f"attributes doesn't lower this: the whole item is billed. {_LARGE_ITEM_ADVICE}"))
    singles = sum(1 for a in profile.attributes.values() if a.depth == 0 and a.count == 1)
    if profile.items >= 20 and singles >= 20:
        found.append(("info", f"{singles:,} attributes appear in only one item. Attribute names built from data "
                              "(dates, IDs) can't be indexed and are hard to query. Keeping that data in a map, "
                              "or as separate items with the date or ID in the sort key, makes it queryable."))
    return found


def _capacity_findings(info: TableInfo, metrics: TableMetrics, prices: dict[str, float]) -> list[tuple[str, str]]:
    """Provisioned capacity against CloudWatch's busiest period: near the limit, or paying for unused units."""
    found: list[tuple[str, str]] = []
    minutes = metrics.period // 60
    low: list[str] = []
    suggested = {"Reads": info.read_capacity or 0, "Writes": info.write_capacity or 0}
    for label, peak, capacity in (("Reads", metrics.peak_reads, info.read_capacity),
                                  ("Writes", metrics.peak_writes, info.write_capacity)):
        if not capacity:
            continue
        share = (peak or 0.0) / capacity
        if share >= 0.8:
            found.append(("warn", f"{label} averaged {_units(peak)} units/s in the busiest {minutes} minutes, "
                                  f"{share:.0%} of the {capacity:,} provisioned. Short bursts above it get throttled: "
                                  "raise the capacity or turn on auto scaling."))
        elif share < 0.2:
            shown = "under 1%" if 0 < share < 0.01 else f"{share:.0%}"
            low.append(f"{label.lower()} peaked at {shown} of the {capacity:,} provisioned units")
            suggested[label] = max(1, math.ceil((peak or 0.0) / TARGET_USE))
    if not low:
        return found
    now = capacity_cost(info.read_capacity or 0, info.write_capacity or 0, prices)
    lowered = capacity_cost(suggested["Reads"], suggested["Writes"], prices)
    on_demand = request_cost(metrics.read_units or 0.0, metrics.write_units or 0.0, prices) * HOURS_PER_MONTH / metrics.hours
    options = []
    if lowered < now:
        options.append(f"{suggested['Reads']:,} read / {suggested['Writes']:,} write units (the busiest period at "
                       f"{TARGET_USE:.0%} use) would cost {human_money(lowered)}")
    if on_demand < now:
        options.append(f"on-demand at this traffic about {human_money(on_demand)}")
    if not options:
        return found
    saving = now - min(lowered, on_demand)
    text = "; ".join(low)
    found.append(("warn" if saving >= 10 else "info",
                  f"{text[0].upper()}{text[1:]} in the last {metrics.hours}h. The table's own capacity costs "
                  f"{human_money(now)}/month; {' and '.join(options)}. If the last {metrics.hours}h were typical, "
                  f"that saves up to {human_money(saving)}/month. Auto scaling can also adjust the capacity for you."))
    return found


def table_findings(info: TableInfo, metrics: TableMetrics | None = None,
                   prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """Plain-language risks and cost notes for a table, each with what to do about it -> [(level, message)]."""
    prices = DYNAMODB_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    pk = info.partition_key or "<partition key>"
    if info.status and info.status != "ACTIVE":
        found.append(("info", f"The table is {info.status}."))
    for idx in info.indexes:
        if idx.backfilling or (idx.status and idx.status != "ACTIVE"):
            state = "backfilling" if idx.backfilling else idx.status
            found.append(("info", f"Index {idx.name} is {state}; queries on it may miss items until it's done."))
    if "pitr" not in info.errors and info.pitr is False:
        price = (f", for about {human_money(info.size_bytes / GB * prices['pitr'])}/month at this table's size"
                 if info.size_bytes else "")
        found.append(("warn", "Point-in-time recovery is off: an accidental delete or bad write can't be rolled "
                              f"back. Turning it on keeps continuous backups for up to 35 days{price}: "
                              f"aws dynamodb update-continuous-backups --table-name {info.name} "
                              "--point-in-time-recovery-specification PointInTimeRecoveryEnabled=true"))
    if info.deletion_protection is False:
        found.append(("info", "Deletion protection is off, so one DeleteTable call removes the table. To turn it on: "
                              f"aws dynamodb update-table --table-name {info.name} --deletion-protection-enabled"))
    if metrics and (metrics.read_throttles or metrics.write_throttles):
        if info.on_demand:
            fix = ("On an on-demand table this usually means one partition key gets most of the traffic, or traffic "
                   "more than doubled suddenly")
        else:
            fix = "Raise the capacity or turn on auto scaling; if that doesn't help, one partition key may be hot"
        found.append(("warn", f"{metrics.read_throttles:,} read and {metrics.write_throttles:,} write throttle "
                              f"events in the last {metrics.hours}h: some requests were rejected and retried, which "
                              f"slows the application. {fix}. value_counts({info.name!r}, {pk!r}) shows whether a "
                              "few partition keys hold most of the items."))
    if metrics and metrics.has_data and not info.on_demand:
        found += _capacity_findings(info, metrics, prices)
    if info.avg_item_size and info.avg_item_size > 4 * KB:
        found.append(("info", f"Items average {human_size(info.avg_item_size)}, so each read of one item costs "
                              f"{_units(read_units(math.ceil(info.avg_item_size)))} read units. {_LARGE_ITEM_ADVICE} "
                              f"largest({info.name!r}) shows the biggest items."))
    if info.errors:
        parts = [f"{_SECTIONS.get(k, (k, ''))[0]} ({_why(v, _SECTIONS[k][1]) if k in _SECTIONS else v})"
                 for k, v in info.errors.items()]
        found.append(("info", "Couldn't read " + ", ".join(parts) + "."))
    return found


# =============================================================================
# 4. DynamoDBAnalyzer - pure logic layer (talks to AWS, returns data)
# =============================================================================

_FROM_RE = re.compile(r'\bFROM\s+(?:"([^"]+)"|([\w.-]+))(?:\."([^"]+)")?', re.IGNORECASE)


class DynamoDBAnalyzer:
    """Pure-logic DynamoDB analysis: every method returns data; nothing is printed or written.

    Items are plain dicts of Python values (see from_dynamo). Methods that read items take `where`
    (a filter, see build_filter), `index` (read a secondary index instead of the table) and
    `progress` (called with the running count of items read). Scans are billed per item read and
    share the table's capacity with your application, so the whole-table analyses stop after
    `limit` items unless you pass limit=None.
    `prices` overrides DYNAMODB_PRICES for cost estimates.
    """

    def __init__(self, session: Any = None, *, region: str | None = None, profile: str | None = None,
                 client: Any = None, prices: dict[str, float] | None = None):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        self._client = client
        self._cloudwatch_client: Any = None
        self.prices = {**DYNAMODB_PRICES, **(prices or {})}
        self._tables: dict[str, TableInfo] = {}

    @property
    def client(self) -> Any:
        """The DynamoDB client, made on first use so a missing region shows up as a readable error."""
        if self._client is None:
            try:
                self._client = self.session.client("dynamodb", config=self._config)
            except NoRegionError:
                raise ValueError("No AWS region is set, and DynamoDB tables are regional. Pass one: "
                                 "DynamoDBView(DynamoDBAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION.") from None
        return self._client

    @property
    def region(self) -> str:
        return self.client.meta.region_name

    def _cloudwatch(self) -> Any:
        if self._cloudwatch_client is None:
            self._cloudwatch_client = self.session.client("cloudwatch", region_name=self.region)
        return self._cloudwatch_client

    # ------------------------------------------------------------------ tables

    def list_table_names(self) -> list[str]:
        return [name for page in self.client.get_paginator("list_tables").paginate() for name in page.get("TableNames", [])]

    def list_tables(self, *, details: bool = True) -> list[TableInfo]:
        """Every table in the region. details=True describes each one (in parallel) for keys, counts and billing."""
        names = self.list_table_names()
        if not details:
            return [TableInfo(name) for name in names]
        with ThreadPoolExecutor(max_workers=16) as pool:
            return list(pool.map(self._safe_table, names))

    def _safe_table(self, name: str) -> TableInfo:
        try:
            return self.table(name, refresh=True)
        except (ClientError, BotoCoreError) as exc:
            return TableInfo(name, errors={"describe": _error_name(exc)})

    def table_reports(self, *, match: str | None = None, metrics: bool = True, max_workers: int = 8,
                      progress: Callable[[int], None] | None = None) -> list[TableReport]:
        """describe() (and CloudWatch usage unless metrics=False) for every table, checked in parallel.
        match: only tables whose name matches this glob, e.g. 'prod-*'."""
        names = [n for n in self.list_table_names() if match is None or fnmatch.fnmatchcase(n, match)]
        if metrics and names:
            self._cloudwatch()  # boto3 sessions aren't thread-safe: make the client before the threads start

        def check(name: str) -> TableReport:
            try:
                report = TableReport(self.describe(name))
            except (ClientError, BotoCoreError) as exc:
                return TableReport(TableInfo(name, errors={"describe": _error_name(exc)}))
            if metrics:
                try:
                    report.metrics = self.table_metrics(name)
                except (ClientError, BotoCoreError) as exc:
                    report.metrics_error = _error_name(exc)
            return report

        reports: list[TableReport] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for report in pool.map(check, names):
                reports.append(report)
                if progress:
                    progress(len(reports))
        return reports

    def table(self, table: str, *, refresh: bool = False) -> TableInfo:
        """Keys, indexes, billing and DynamoDB's item count / size estimate (one DescribeTable call, cached)."""
        if refresh or table not in self._tables:
            self._tables[table] = parse_table(self.client.describe_table(TableName=table)["Table"])
        return self._tables[table]

    def describe(self, table: str) -> TableInfo:
        """Everything about a table: table() plus TTL, point-in-time recovery and tags."""
        info = self.table(table, refresh=True)

        def get(section: str, call: Callable[[], Any]) -> Any:
            try:
                return call()
            except (ClientError, BotoCoreError) as exc:
                info.errors[section] = _error_name(exc)
            return None

        if resp := get("ttl", lambda: self.client.describe_time_to_live(TableName=table)):
            ttl = resp.get("TimeToLiveDescription", {})
            info.ttl_status, info.ttl_attribute = ttl.get("TimeToLiveStatus"), ttl.get("AttributeName")
        if resp := get("pitr", lambda: self.client.describe_continuous_backups(TableName=table)):
            pitr = resp.get("ContinuousBackupsDescription", {}).get("PointInTimeRecoveryDescription", {})
            info.pitr = pitr.get("PointInTimeRecoveryStatus") == "ENABLED"
            info.pitr_days = pitr.get("RecoveryPeriodInDays")
            info.pitr_earliest = pitr.get("EarliestRestorableDateTime")
        if info.arn and (tags := get("tags", lambda: self._tags(info.arn))) is not None:
            info.tags = tags
        return info

    def _tags(self, arn: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        token = None
        while True:
            resp = self.client.list_tags_of_resource(ResourceArn=arn, **({"NextToken": token} if token else {}))
            tags.update({t["Key"]: t["Value"] for t in resp.get("Tags", [])})
            token = resp.get("NextToken")
            if not token:
                return tags

    def table_metrics(self, table: str, *, hours: int = 24) -> TableMetrics:
        """Read / write units consumed (in total and in the busiest period) and throttle events from
        CloudWatch, for the table itself (not its indexes). Needs cloudwatch:GetMetricData."""
        period = max(300, math.ceil(hours * 3600 / 1440 / 60) * 60)  # at most ~1,440 points per metric
        names = ["ConsumedReadCapacityUnits", "ConsumedWriteCapacityUnits", "ReadThrottleEvents", "WriteThrottleEvents"]
        queries = [{"Id": f"m{i}", "MetricStat": {
            "Metric": {"Namespace": "AWS/DynamoDB", "MetricName": name, "Dimensions": [{"Name": "TableName", "Value": table}]},
            "Period": period, "Stat": "Sum"}} for i, name in enumerate(names)]
        now = _utcnow()
        values: dict[str, list[float]] = {name: [] for name in names}
        for page in self._cloudwatch().get_paginator("get_metric_data").paginate(
                MetricDataQueries=queries, StartTime=now - timedelta(hours=hours), EndTime=now):
            for series in page["MetricDataResults"]:
                values[names[int(series["Id"][1:])]] += series.get("Values", [])
        reads, writes = values["ConsumedReadCapacityUnits"], values["ConsumedWriteCapacityUnits"]
        return TableMetrics(
            table, hours, period,
            read_units=sum(reads) if reads else None, write_units=sum(writes) if writes else None,
            peak_reads=max(reads) / period if reads else None, peak_writes=max(writes) / period if writes else None,
            read_throttles=int(sum(values["ReadThrottleEvents"])), write_throttles=int(sum(values["WriteThrottleEvents"])))

    # -------------------------------------------------------------------- keys

    def keys(self, table: str, index: str | None = None) -> list[str]:
        """[partition key, sort key] of the table, or of `index`."""
        info = self.table(table)
        return info.index(index).keys if index else info.keys

    def key_attributes(self, table: str, index: str | None = None) -> list[str]:
        """Every key attribute an item read from the table / index carries, the index's first. Tables of
        items show these first, and they make up the start key of the next page."""
        info = self.table(table)
        return list(dict.fromkeys((info.index(index).keys if index else []) + info.keys))

    def _key_value(self, table: str, name: str, value: Any) -> Any:
        """Match a key value to its declared type, so '42' works for a number key and 42 for a string key."""
        kind = self.table(table).attribute_types.get(name)
        if kind == "N" and isinstance(value, str):
            try:
                return _number(value.strip())
            except InvalidOperation:
                raise ValueError(f"{name} is a number key, and {value!r} isn't a number") from None
        if kind == "S" and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return str(value)
        return value

    def primary_key(self, table: str, key: tuple | list | dict) -> dict[str, Any]:
        """('USER#1', 'ORDER#7') or {'pk': 'USER#1', 'sk': 'ORDER#7'} -> the key dict GetItem needs."""
        names = self.table(table).keys
        if isinstance(key, dict):
            given = dict(key)
        elif len(key) == 1 and isinstance(key[0], dict):
            given = dict(key[0])
        elif len(key) == len(names):
            given = dict(zip(names, key))
        else:
            raise ValueError(f"{table}'s key is ({', '.join(names)}): pass {_plural(len(names), 'value')} or a dict")
        missing = [name for name in names if name not in given]
        if missing:
            raise ValueError(f"Missing key attribute {', '.join(missing)} for {table}")
        return {name: self._key_value(table, name, given[name]) for name in names}

    # ------------------------------------------------------------------- items

    def get(self, table: str, *key: Any, consistent: bool = False) -> dict[str, Any] | None:
        """One item by primary key: get('orders', 'USER#1', 'ORDER#7') or get('orders', {'pk': ..., 'sk': ...}).
        None when there's no such item."""
        primary = self.primary_key(table, key)
        resp = self.client.get_item(TableName=table, Key={k: to_dynamo(v) for k, v in primary.items()},
                                    ConsistentRead=consistent)
        return from_dynamo_item(resp["Item"]) if "Item" in resp else None

    def _projection(self, table: str, index: str | None, attributes: str | Iterable[str] | None) -> list[str] | None:
        """Attributes to read, always with the key attributes (tables need them, and so does the next page)."""
        if attributes is None:
            return None
        attributes = [attributes] if isinstance(attributes, str) else list(attributes)
        return list(dict.fromkeys(self.key_attributes(table, index) + attributes))

    def _scan_params(self, table: str, index: str | None, where: Any, attributes: Any,
                     consistent: bool = False) -> dict[str, Any]:
        params = {"TableName": table,
                  **_expression_params(where=build_filter(where), attributes=self._projection(table, index, attributes))}
        if index:
            params["IndexName"] = index
        if consistent:
            params["ConsistentRead"] = True
        return params

    def scan(self, table: str, n: int | None = 100, *, where: Any = None, index: str | None = None,
             attributes: str | Iterable[str] | None = None, start_key: Any = None, scan_limit: int | None = None,
             consistent: bool = False, progress: Callable[[int], None] | None = None) -> ItemPage:
        """Up to n items from the start of the table (or index), or after `start_key` (a page's last_key).
        With `where`, keeps reading until n items match; scan_limit caps how many items are read."""
        params = self._scan_params(table, index, where, attributes, consistent)
        return self._read("scan", table, index, params, n=n, start_key=start_key, scan_limit=scan_limit,
                          progress=progress)

    def query(self, table: str, partition: Any, sort: Any = None, *, n: int | None = 100, where: Any = None,
              index: str | None = None, attributes: str | Iterable[str] | None = None, descending: bool = False,
              start_key: Any = None, scan_limit: int | None = None, consistent: bool = False,
              progress: Callable[[int], None] | None = None) -> ItemPage:
        """Items whose partition key is `partition`, in sort-key order (on the table, or on `index`).
        sort narrows the sort key: a value, or ('begins_with', 'ORDER#'), ('between', a, b), ('>=', x) ...
        descending=True starts from the highest sort key."""
        keys = self.keys(table, index)
        condition = Key(keys[0]).eq(self._key_value(table, keys[0], partition))
        if sort is not None:
            if len(keys) < 2:
                raise ValueError(f"{index or table} has no sort key, so sort= can't be used")
            op, *args = sort if isinstance(sort, tuple) else ("=", sort)
            condition &= _condition(Key(keys[1]), (op, *(self._key_value(table, keys[1], a) for a in args)), key=True)
        params = {"TableName": table, "ScanIndexForward": not descending,
                  **_expression_params(key=condition, where=build_filter(where),
                                       attributes=self._projection(table, index, attributes))}
        if index:
            params["IndexName"] = index
        if consistent:
            params["ConsistentRead"] = True
        return self._read("query", table, index, params, n=n, start_key=start_key, scan_limit=scan_limit,
                          progress=progress)

    def _read(self, operation: str, table: str, index: str | None, params: dict[str, Any], *, n: int | None,
              start_key: Any = None, scan_limit: int | None = None,
              progress: Callable[[int], None] | None = None) -> ItemPage:
        """Page through a scan or query until n items came back, the data ran out, or scan_limit items were
        read. Stopping inside a page sets last_key to the last item returned, so passing it back as
        start_key resumes right after that item."""
        n, scan_limit = _as_count(n, "n"), _as_count(scan_limit, "scan_limit")
        if n is not None and n < 1:
            raise ValueError("n must be at least 1 (or None for everything)")
        page = ItemPage(table=table, index=index, operation=operation, keys=self.key_attributes(table, index))
        params = dict(params, ReturnConsumedCapacity="TOTAL")
        if start_key:
            params["ExclusiveStartKey"] = start_key
        filtered = "FilterExpression" in params
        call = getattr(self.client, operation)
        started = time.monotonic()
        while True:
            caps = [n - len(page.items)] if n is not None and not filtered else []  # a filter drops items after the read
            if scan_limit is not None:
                caps.append(scan_limit - page.stats.scanned)
            if caps:
                params["Limit"] = max(1, min(caps))
            resp = call(**params)
            page.stats.scanned += resp.get("ScannedCount", 0)
            page.stats.read_units += resp.get("ConsumedCapacity", {}).get("CapacityUnits", 0.0)
            raw = resp.get("Items", [])
            page.last_key = resp.get("LastEvaluatedKey")
            if n is not None and len(raw) > n - len(page.items):
                raw = raw[: n - len(page.items)]
                page.last_key = {name: raw[-1][name] for name in page.keys if name in raw[-1]}
            page.items += [from_dynamo_item(item) for item in raw]
            if progress:
                progress(page.stats.scanned)
            if (page.last_key is None or (n is not None and len(page.items) >= n)
                    or (scan_limit is not None and page.stats.scanned >= scan_limit)):
                break
            params["ExclusiveStartKey"] = page.last_key
        page.stats.matched = len(page.items)
        page.stats.truncated = page.last_key is not None
        page.stats.seconds = time.monotonic() - started
        return page

    def sample(self, table: str, n: int = 100, *, where: Any = None, index: str | None = None,
               attributes: str | Iterable[str] | None = None, segments: int | None = None,
               scan_limit: int | None = None, progress: Callable[[int], None] | None = None) -> ItemPage:
        """About n items spread over the whole table. scan() starts at the beginning, where the first
        items can all share a few partition keys; this reads a few items from each of `segments` slices
        of the key space (parallel-scan segments, fetched in parallel; default one per item, up to 100).
        scan_limit caps the items read."""
        n, scan_limit = _as_int(n, "n"), _as_count(scan_limit, "scan_limit")
        if n < 1:
            raise ValueError("n must be at least 1")
        segments = segments or min(n, 100)
        params = dict(self._scan_params(table, index, where, attributes), TotalSegments=segments)
        page = ItemPage(table=table, index=index, operation="sample", keys=self.key_attributes(table, index))
        budget = None if scan_limit is None else math.ceil(scan_limit / segments)  # items each segment may read
        cursors: dict[int, Any] = dict.fromkeys(range(segments))  # open segment -> where to resume it
        spent: dict[int, int] = dict.fromkeys(range(segments), 0)
        started = time.monotonic()

        def read(job: tuple[int, Any, int | None], share: int) -> ItemPage:
            segment, start, limit = job
            return self._read("scan", table, index, dict(params, Segment=segment), n=share, start_key=start,
                              scan_limit=limit)

        with ThreadPoolExecutor(max_workers=min(segments, 16)) as pool:
            while cursors and len(page.items) < n:
                jobs = [(s, cursors[s], None if budget is None else budget - spent[s]) for s in cursors]
                share = math.ceil((n - len(page.items)) / len(jobs))
                for (segment, _, _), part in zip(jobs, pool.map(read, jobs, [share] * len(jobs))):
                    page.items += part.items
                    page.stats.add(part.stats)
                    spent[segment] += part.stats.scanned
                    if part.last_key is None or (budget is not None and spent[segment] >= budget):
                        del cursors[segment]
                    else:
                        cursors[segment] = part.last_key
                if progress:
                    progress(page.stats.scanned)
        page.items = page.items[:n]
        page.stats.matched = len(page.items)
        page.stats.truncated = bool(cursors)
        page.stats.seconds = time.monotonic() - started
        return page

    def sql(self, statement: str, *parameters: Any, n: int | None = 100, next_token: str | None = None,
            consistent: bool = False, progress: Callable[[int], None] | None = None) -> ItemPage:
        """Run a PartiQL statement: sql('SELECT * FROM "orders" WHERE pk = ?', 'USER#1'). Parameters fill
        the ? placeholders in order. Pages are kept whole (so you may get a few more than n) and last_key
        continues exactly, as next_token=. Without the partition key in WHERE, a SELECT scans the table."""
        n = _as_count(n, "n")
        params: dict[str, Any] = {"Statement": statement, "ReturnConsumedCapacity": "TOTAL"}
        if parameters:
            params["Parameters"] = [to_dynamo(p) for p in parameters]
        if consistent:
            params["ConsistentRead"] = True
        if next_token:
            params["NextToken"] = next_token
        match = _FROM_RE.search(statement)
        table = (match.group(1) or match.group(2)) if match else ""
        index = match.group(3) if match else None
        try:
            keys = self.key_attributes(table, index) if table else []
        except (ClientError, BotoCoreError, ValueError):
            keys = []  # the statement's own error is more useful; let execute_statement raise it
        page = ItemPage(table=table, index=index, operation="sql", keys=keys)
        started = time.monotonic()
        while True:
            resp = self.client.execute_statement(**params)
            page.stats.read_units += resp.get("ConsumedCapacity", {}).get("CapacityUnits", 0.0)
            page.items += [from_dynamo_item(item) for item in resp.get("Items", [])]
            page.last_key = resp.get("NextToken")
            if progress:
                progress(len(page.items))
            if not page.last_key or (n is not None and len(page.items) >= n):
                break
            params["NextToken"] = page.last_key
        page.stats.matched = len(page.items)
        page.stats.truncated = page.last_key is not None
        page.stats.seconds = time.monotonic() - started
        return page

    # ---------------------------------------------------------------- analysis

    def _pages(self, params: dict[str, Any], stats: ReadStats, *, limit: int | None = None,
               progress: Callable[[int], None] | None = None) -> Iterator[dict[str, Any]]:
        """Scan responses (up to 1 MB each) until the end of the table or until `limit` items were read."""
        limit = _as_count(limit, "limit")
        params = dict(params, ReturnConsumedCapacity="TOTAL")
        started = time.monotonic()
        try:
            while True:
                if limit is not None:
                    params["Limit"] = max(1, limit - stats.scanned)
                resp = self.client.scan(**params)
                stats.scanned += resp.get("ScannedCount", 0)
                stats.read_units += resp.get("ConsumedCapacity", {}).get("CapacityUnits", 0.0)
                yield resp
                if progress:
                    progress(stats.scanned)
                last = resp.get("LastEvaluatedKey")
                if not last:
                    return
                if limit is not None and stats.scanned >= limit:
                    stats.truncated = True
                    return
                params["ExclusiveStartKey"] = last
        finally:
            stats.seconds += time.monotonic() - started

    def iter_items(self, table: str, *, where: Any = None, index: str | None = None,
                   attributes: str | Iterable[str] | None = None, limit: int | None = None,
                   progress: Callable[[int], None] | None = None,
                   stats: ReadStats | None = None) -> Iterator[dict[str, Any]]:
        """Stream a scan's items without keeping them in memory. `limit` caps the items read; pass
        stats=ReadStats() to see afterwards what the scan read and cost."""
        stats = ReadStats() if stats is None else stats
        params = self._scan_params(table, index, where, attributes)
        for resp in self._pages(params, stats, limit=limit, progress=progress):
            for raw in resp.get("Items", []):
                stats.matched += 1
                yield from_dynamo_item(raw)

    def count(self, table: str, *, where: Any = None, index: str | None = None,
              progress: Callable[[int], None] | None = None) -> ReadStats:
        """Exact item count (of items matching `where`) as ReadStats.matched: a full scan with Select=COUNT.
        No items come back, but every item is still read and billed. table(t).item_count is an instant
        estimate, refreshed about every 6 hours."""
        stats = ReadStats()
        params = dict(self._scan_params(table, index, where, None), Select="COUNT")
        for resp in self._pages(params, stats, progress=progress):
            stats.matched += resp.get("Count", 0)
        return stats

    def value_counts(self, table: str, attribute: str, *, limit: int | None = 10_000, where: Any = None,
                     index: str | None = None, progress: Callable[[int], None] | None = None) -> ValueCounts:
        """How often each value of `attribute` (a dotted path reaches into maps) occurs among the first
        `limit` items read (None = the whole table). On the partition key: the size of each item collection."""
        stats = ReadStats()
        items = self.iter_items(table, where=where, index=index, limit=limit, progress=progress, stats=stats)
        result = count_values(items, attribute)
        result.table, result.stats = table, stats
        return result

    def largest(self, table: str, n: int = 10, *, limit: int | None = 10_000, where: Any = None,
                index: str | None = None, progress: Callable[[int], None] | None = None) -> ItemPage:
        """The n biggest items (by DynamoDB's sizing rules), biggest first, among the first `limit` items
        read (None = the whole table)."""
        n = _as_int(n, "n")
        page = ItemPage(table=table, index=index, operation="largest", keys=self.key_attributes(table, index))
        items = self.iter_items(table, where=where, index=index, limit=limit, progress=progress, stats=page.stats)
        page.items = [item for _, _, item in heapq.nlargest(n, ((item_size(it), i, it) for i, it in enumerate(items)))]
        return page

    def profile(self, table: str, n: int = 1000, *, where: Any = None, index: str | None = None,
                spread: bool = True, max_depth: int = 2,
                progress: Callable[[int], None] | None = None) -> TableProfile:
        """Profile about n items: attribute names, types, fill rate, distinct values, ranges, examples,
        key shapes and item sizes. spread=True samples across the whole table (see sample);
        spread=False reads from the start of it."""
        if spread:
            page = self.sample(table, n, where=where, index=index, progress=progress)
        else:
            page = self.scan(table, n, where=where, index=index, progress=progress)
        info = self.table(table)
        result = profile_items(page.items, table, keys=page.keys, max_depth=max_depth)
        result.index, result.stats = index, page.stats
        result.approx_item_count = info.index(index).item_count if index else info.item_count
        return result


# =============================================================================
# 5. DynamoDBView - notebook UI layer (renders what DynamoDBAnalyzer returns)
# =============================================================================


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[tuple[str, str]]


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""


_CSS = """<style>
.ddb{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.ddb h3{margin:10px 0 2px;font-size:16px}
.ddb h4{margin:14px 0 4px;font-size:13px}
.ddb .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.ddb .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.ddb .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.ddb .card .l{font-size:11px;opacity:.65}
.ddb .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.ddb .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.ddb table.t{border-collapse:collapse;width:auto;font-size:inherit}
.ddb table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.ddb table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.ddb table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.ddb table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ddb table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.ddb table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.ddb .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.ddb .track{vertical-align:middle;margin-right:6px}
.ddb .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.ddb .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.ddb .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.ddb .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.ddb .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.ddb pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
</style>"""

_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _render_html(blocks: list[Any], max_rows: int) -> str:
    out = [_CSS, '<div class="ddb">']
    for block in blocks:
        if isinstance(block, _Title):
            out.append(f"<h3>{_esc(block.text)}</h3>")
            if block.sub:
                out.append(f'<div class="sub">{_esc(block.sub)}</div>')
        elif isinstance(block, _Cards):
            cards = "".join(f'<div class="card"><div class="l">{_esc(label)}</div><div class="v">{_esc(value)}</div></div>'
                            for label, value in block.items)
            out.append(f'<div class="cards">{cards}</div>')
        elif isinstance(block, _Note):
            out.append(f'<div class="note {block.level}">{_esc(block.text)}</div>')
        elif isinstance(block, _Table):
            if block.title:
                out.append(f"<h4>{_esc(block.title)}</h4>")
            if not block.rows:
                out.append('<div class="more">(none)</div>')
                continue
            rows, hidden = _visible_rows(block, max_rows)
            head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
            head += f"<th>{_esc(block.bar_label)}</th>" if block.bars is not None else ""
            body = []
            for i, row in enumerate(rows):
                cells = []
                for j, cell in enumerate(row):
                    text = "" if cell is None else str(cell)
                    css = "tree" if block.tree and j == 0 else ("n" if _NUMERIC_RE.match(text) else "")
                    cells.append(f'<td class="{css}">{_esc(text)}</td>' if css else f"<td>{_esc(text)}</td>")
                if block.bars is not None:
                    pct = max(0.0, min(1.0, block.bars[i])) * 100
                    cells.append(f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                                 f"</span></span>{pct:.1f}%</td>")
                body.append(f"<tr>{''.join(cells)}</tr>")
            out.append(f'<div class="tw"><table class="t"><thead><tr>{head}</tr></thead>'
                       f'<tbody>{"".join(body)}</tbody></table></div>')
            if hidden:
                out.append(f'<div class="more">... {hidden:,} more rows not shown</div>')
        elif isinstance(block, _Text):
            if block.title:
                out.append(f"<h4>{_esc(block.title)}</h4>")
            out.append(f"<pre>{_esc(block.text)}</pre>")
    out.append("</div>")
    return "".join(out)


def _text_bar(fraction: float, width: int = 20) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled) + f" {fraction * 100:5.1f}%"


def _render_text(blocks: list[Any], max_rows: int) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Title):
            out += ["", block.text, "=" * min(len(block.text), 100)] + ([block.sub] if block.sub else [])
        elif isinstance(block, _Cards):
            line = ""
            for label, value in block.items:
                item = f"{label}: {value}"
                if line and len(line) + len(item) > 100:
                    out.append(line)
                    line = ""
                line += ("   " if line else "") + item
            out.append(line)
        elif isinstance(block, _Note):
            out.append({"warn": "[!] ", "ok": "[ok] "}.get(block.level, "[i] ") + block.text)
        elif isinstance(block, _Table):
            out.append("")
            if block.title:
                out.append(f"-- {block.title} --")
            if not block.rows:
                out.append("(none)")
                continue
            rows, hidden = _visible_rows(block, max_rows)
            headers = list(block.headers) + ([block.bar_label] if block.bars is not None else [])
            cells = [[_clip(("" if c is None else str(c)).replace("\n", ", ")) for c in row]
                     + ([_text_bar(block.bars[i])] if block.bars is not None else []) for i, row in enumerate(rows)]
            widths = [max([len(h)] + [len(r[j]) for r in cells]) for j, h in enumerate(headers)]

            def line_of(values: list[str], widths: list[int] = widths) -> str:
                return "  ".join(v.rjust(w) if _NUMERIC_RE.match(v) else v.ljust(w)
                                 for v, w in zip(values, widths)).rstrip()

            out += [line_of(headers), "  ".join("-" * w for w in widths)] + [line_of(r) for r in cells]
            if hidden:
                out.append(f"... {hidden:,} more rows not shown")
        elif isinstance(block, _Text):
            if block.title:
                out += ["", f"-- {block.title} --"]
            out.append(block.text)
    return "\n".join(out)


def _in_notebook() -> bool:
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and type(shell).__name__ != "TerminalInteractiveShell"


def _progress_bar_class(notebook: bool) -> Any:
    """tqdm's widget bar in a notebook (it needs ipywidgets) or its text bar elsewhere; None without tqdm."""
    try:
        if notebook:
            importlib.import_module("ipywidgets")
            return importlib.import_module("tqdm.notebook").tqdm
        return importlib.import_module("tqdm").tqdm
    except Exception:  # not installed, or too old to import cleanly: the plain progress line takes over
        return None


def _progress_bar(bar_class: Any, label: str, unit: str, total: int | None) -> Any:
    """A tqdm bar that shows up after half a second and disappears when closed. unit='B' counts bytes."""
    options: dict[str, Any] = {"desc": label, "total": total, "leave": False, "delay": 0.5, "mininterval": 0.25,
                               "dynamic_ncols": True, "disable": False, "unit_scale": True}
    if unit == "B":
        options.update(unit="B", unit_divisor=1024)
    else:
        known = total is not None
        counts = "{percentage:3.0f}%|{bar}| {n:,}/{total:,}" if known else "{n:,}"
        timing = "{elapsed}<{remaining}, {rate_fmt}" if known else "{elapsed}, {rate_fmt}"
        options.update(unit=f" {unit}", bar_format=f"{{desc}}: {counts} {unit} [{timing}]")
    return bar_class(**options)


def _duration(seconds: float) -> str:
    """0.42 -> '0.4s', 42.4 -> '42s', 125 -> '2m 05s', 7500 -> '2h 05m'."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m"


def _progress_text(label: str, unit: str, count: int, total: int | None, elapsed: float) -> str:
    """The progress line shown without tqdm: 'Reading... 1.2 GB of 3.0 GB (40%) · 12s · 98.0 MB/s · about 18s left'."""
    amount = human_size if unit == "B" else (lambda n: f"{n:,}")
    text = f"{label}... {amount(count)}"
    if total:
        text += f" of {amount(total)}"
    text += "" if unit == "B" else f" {unit}"
    if total:
        text += f" ({min(count / total, 1):.0%})"
    text += f" · {_duration(elapsed)}"
    if elapsed >= 1 and count:
        rate = count / elapsed
        text += f" · {human_size(rate)}/s" if unit == "B" else f" · {rate:,.0f}/s" if rate >= 10 else f" · {rate:.1f}/s"
        if total and total > count:
            text += f" · about {_duration((total - count) / rate)} left"
    return text


def _fmt_dt(moment: datetime | None) -> str:
    return "-" if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _count(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _target(table: str, index: str | None) -> str:
    return f"{table} (index {index})" if index else table


def _keys_label(info: TableInfo | IndexInfo, types: dict[str, str]) -> str:
    """'pk (string) + sk (number)'."""
    return " + ".join(f"{k} ({TYPE_NAMES.get(types.get(k, ''), '?')})" for k in info.keys if k) or "-"


def _capacity_label(read: int | None, write: int | None) -> str:
    return f"{_count(read)} read / {_count(write)} write units"


def _billing_label(info: TableInfo) -> str:
    if info.on_demand:
        return "on-demand"
    return "provisioned: " + _capacity_label(info.read_capacity, info.write_capacity)


_TABLE_CLASSES = {"STANDARD": "standard", "STANDARD_INFREQUENT_ACCESS": "standard-infrequent access (cheaper storage)"}
_STREAM_VIEWS = {"KEYS_ONLY": "on: keys only", "NEW_IMAGE": "on: new item", "OLD_IMAGE": "on: old item",
                 "NEW_AND_OLD_IMAGES": "on: old and new item"}
_TTL_STATES = {"DISABLED": "off", "ENABLING": "turning on", "DISABLING": "turning off"}
_NO_MATCH = ("No items matched. Values are typed: '100' (text) doesn't match 100 (a number); "
             "schema() shows each attribute's type.")


def _projection_label(idx: IndexInfo) -> str:
    if idx.projection == "INCLUDE":
        return "keys + " + ", ".join(idx.projected)
    return {"ALL": "all attributes", "KEYS_ONLY": "keys only"}.get(idx.projection, idx.projection)


def _query_hint(info: TableInfo, idx: IndexInfo | None) -> str:
    source = idx or info
    hint = f"query({info.name!r}, <{source.partition_key}>"
    hint += f", sort=<{source.sort_key}>" if source.sort_key else ""
    return hint + (f", index={idx.name!r})" if idx else ")")


def _section(info: TableInfo, section: str, text: str) -> str:
    return f"? ({info.errors[section]})" if section in info.errors else text


def _types_label(attr: AttributeProfile) -> str:
    if len(attr.types) == 1:
        return TYPE_NAMES.get(attr.main_type, attr.main_type)
    return " · ".join(f"{TYPE_NAMES.get(kind, kind)} {_share(n, attr.count):.0%}" for kind, n in attr.types.most_common())


def _distinct_label(attr: AttributeProfile) -> str:
    if attr.distinct_capped:
        return f"{attr.distinct:,}+"
    if attr.count > 1 and attr.distinct == attr.count:
        return "all unique"
    return f"{attr.distinct:,}"


def _range_label(attr: AttributeProfile) -> str:
    if attr.low is not None:
        low, high = format_value(attr.low, 24), format_value(attr.high, 24)
        return low if low == high else f"{low} … {high}"
    if attr.min_len is not None:
        unit = {"S": "char", "B": "byte", "M": "field"}.get(attr.main_type, "element")
        if attr.min_len == attr.max_len:
            return _plural(attr.min_len, unit)
        return f"{attr.min_len:,} - {attr.max_len:,} {unit}s"
    return ""


def _item_rows(item: dict[str, Any], keys: list[str], *, max_depth: int = 6,
               max_elements: int = 100) -> list[list[str]]:
    """Attribute / type / value rows for one item, with maps (and lists holding maps or lists) expanded."""
    rows: list[list[str]] = []
    roles = dict(zip(keys, ("partition key", "sort key")))

    def walk(label: str, value: Any, depth: int) -> None:
        kind = dynamo_type(value)
        indent = "    " * depth
        nested = isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value))
        if not (nested and value and depth < max_depth):
            rows.append([indent + label, TYPE_NAMES.get(kind, kind), format_value(value, 2000, oneline=False)])
            return
        rows.append([indent + label, TYPE_NAMES.get(kind, kind), _plural(len(value), "field" if kind == "M" else "element")])
        children = value.items() if isinstance(value, dict) else ((f"[{i}]", v) for i, v in enumerate(value))
        for i, (name, child) in enumerate(children):
            if i == max_elements:
                rows.append([indent + "    …", "", f"{len(value) - max_elements:,} more"])
                break
            walk(str(name), child, depth + 1)

    for name in [k for k in keys if k in item] + [k for k in item if k not in keys]:
        walk(name + (f"  ({roles[name]})" if name in roles else ""), item[name], 0)
    return rows


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS / input errors as a readable note instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: DynamoDBView, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            if code == "ResourceNotFoundException":
                message = self._not_found(method.__name__, args, kwargs)
            self._show([_Note(f"{code}: {message}  [{method.__name__}]", "warn")])
        except (BotoCoreError, ValueError, TypeError, ImportError) as exc:
            self._show([_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")])

    return wrapper


class DynamoDBView:
    """Notebook UI over DynamoDBAnalyzer. Each method renders a report and returns nothing;
    for the underlying data call the same-named method on `view.core` (a DynamoDBAnalyzer).

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    max_columns: most attributes shown side by side in a table of items (0 = all).
    """

    def __init__(self, core: DynamoDBAnalyzer | None = None, *, mode: str = "auto", max_rows: int = 50,
                 max_columns: int = 30, progress: str = "auto"):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.core = core or DynamoDBAnalyzer()
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.progress = progress
        self.max_columns = max_columns
        self._pager: tuple[str, str, Callable, str, Any, int] | None = None  # what more() continues

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            from IPython.display import HTML, display

            display(HTML(_render_html(blocks, self.max_rows)))
        else:
            print(_render_text(blocks, self.max_rows))

    @contextmanager
    def _progress(self, label: str = "Reading", unit: str = "items read") -> Iterator[Callable[..., None]]:
        """Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away."""
        bar_class = [_progress_bar_class(self.use_html and _in_notebook()) if self.progress == "auto" else None]
        bar: list[Any] = [None]
        handle: list[Any] = [None]
        started: list[Any] = [time.monotonic(), None]  # when the current total started, and that total
        shown, width, stopped = [0.0], [0], [False]

        def close_bar() -> None:
            if bar[0] is not None:
                if not stopped[0] and bar[0].total and bar[0].n < bar[0].total:
                    bar[0].total = bar[0].n  # done early (a file it couldn't read): no red "failed" widget
                bar[0].close()
                bar[0] = None

        def clear() -> None:
            close_bar()
            if handle[0] is not None:
                from IPython.display import HTML

                handle[0].update(HTML(""))
            elif width[0]:
                print("\r" + " " * width[0] + "\r", end="", file=sys.stderr, flush=True)
                width[0] = 0

        def take_over() -> None:
            owner = getattr(self, "_progress_owner", None)
            if owner is not clear:
                if owner is not None:
                    owner()
                self._progress_owner = clear

        def tick(count: int, total: int | None = None) -> None:
            if self.progress == "off":
                return
            if bar_class[0] is not None:
                try:
                    if bar[0] is not None and total != bar[0].total:
                        close_bar()
                    if bar[0] is None:
                        take_over()
                        bar[0] = _progress_bar(bar_class[0], label, unit, total)
                    bar[0].update(count - bar[0].n)
                    return
                except Exception:  # an old tqdm or a broken widget front end: use the plain line instead
                    bar_class[0] = None
            now = time.monotonic()
            if total != started[1]:
                started[:] = [now, total]
            if now - started[0] < 0.5 or now - shown[0] < 0.5:
                return
            shown[0] = now
            take_over()
            text = _progress_text(label, unit, count, total, now - started[0])
            if self.use_html:
                from IPython.display import HTML, display

                if handle[0] is None:
                    handle[0] = display(HTML(""), display_id=True)
                if handle[0] is not None:  # display() returns None outside IPython
                    handle[0].update(HTML(f'<div style="opacity:.6">{_esc(text)}</div>'))
            else:
                width[0] = max(width[0], len(text))
                print("\r" + text.ljust(width[0]), end="", file=sys.stderr, flush=True)

        try:
            yield tick
        except BaseException:
            stopped[0] = True  # interrupted: a tqdm widget stays, red, where it stopped
            raise
        finally:
            clear()
            if getattr(self, "_progress_owner", None) is clear:
                self._progress_owner = None

    def help(self) -> None:
        """This list."""
        rows = []
        for name, member in vars(type(self)).items():
            if name.startswith("_") or not callable(member):
                continue
            target = inspect.unwrap(member)
            params = str(inspect.signature(target)).replace("(self, ", "(").replace("(self)", "()")
            rows.append([f"{name}{params}", (inspect.getdoc(target) or "").split("\n")[0]])
        self._show([_Title("DynamoDBView commands", "Data versions of each live on .core (DynamoDBAnalyzer)"),
                    _Table(["Command", "What it shows"], rows, max_rows=0)])

    def _not_found(self, command: str, args: tuple, kwargs: dict) -> str:
        """Why a table wasn't found, with the closest names in the region ('Orders' -> 'orders')."""
        name = kwargs.get("statement" if command == "sql" else "table", args[0] if args else "")
        if command == "sql":
            match = _FROM_RE.search(str(name))
            name = (match.group(1) or match.group(2)) if match else ""
        text = f"table {name!r} not found in {self.core.region}" if name else f"table not found in {self.core.region}"
        try:
            names = self.core.list_table_names()
        except (ClientError, BotoCoreError):
            names = []
        close = [n for n in names if n.lower() == str(name).lower()] or difflib.get_close_matches(str(name), names, n=3)
        if close:
            return text + f". Did you mean {' or '.join(map(repr, close))}? Names are case-sensitive."
        return text + " (names are case-sensitive, and tables are regional). tables() lists every table in the region."

    def _price_basis(self) -> str:
        return "us-east-1 list prices" if self.core.prices == DYNAMODB_PRICES else "your prices"

    def _read_cost(self, stats: ReadStats) -> str:
        return human_money(request_cost(stats.read_units, prices=self.core.prices))

    def _items_blocks(self, items: list[dict[str, Any]], keys: list[str], title: str = "") -> list[Any]:
        """A table of items (one row each, one column per attribute) plus a note about hidden columns."""
        columns, rows = items_table(items, keys)
        shown = columns[: self.max_columns] if self.max_columns else columns
        blocks: list[Any] = [_Table(shown, [[format_value(row[c]) if c in row else "" for c in shown] for row in rows],
                                    title=title, max_rows=0)]
        if len(shown) < len(columns):
            rest = columns[len(shown):]
            blocks.append(_Note(f"{_plural(len(rest), 'more attribute')} not shown ({', '.join(rest[:6])}"
                                f"{', …' if len(rest) > 6 else ''}): pass attributes=[...] to choose, "
                                "or get a DataFrame of everything from .core (page.to_df())."))
        return blocks

    def _page(self, title: str, sub: str, fetch: Callable[[Any, Callable[[int], None]], ItemPage], *,
              empty: str, start: Any = None, number: int = 1) -> None:
        """Fetch one page of items, show it, and remember how to continue for more()."""
        with self._progress() as tick:
            page = fetch(start, tick)
        self._pager = (title, sub, fetch, empty, page.last_key, number + 1) if page.has_more else None
        st = page.stats
        cards = [("Items", f"{len(page.items):,}")]
        if st.scanned > st.matched:
            cards.append(("Items read", f"{st.scanned:,}"))
        cards += [("Read units", _units(st.read_units)), ("Read cost (on-demand)", self._read_cost(st)),
                  ("Time", f"{st.seconds:.1f}s")]
        blocks: list[Any] = [_Title(title + (f"  (page {number})" if number > 1 else ""), sub), _Cards(cards)]
        if not page.items:
            blocks.append(_Note(empty))
        elif page.operation == "scan" and st.scanned >= max(1000, 10 * st.matched):
            blocks.append(_Note(f"Read {st.scanned:,} items to return {st.matched:,}: a filter runs after the read, "
                                "so every item read is billed. If you search by this attribute often, a query on a "
                                "key or an index is far cheaper."))
        if page.items:
            blocks += self._items_blocks(page.items, page.keys)
        if page.has_more:
            blocks.append(_Note("There's more: call .more() for the next page."))
        self._show(blocks)

    # ------------------------------------------------------------------ tables

    @_friendly_errors
    def tables(self, match: str | None = None, *, metrics: bool = True) -> None:
        """Every table in the region: key, items, size, billing, estimated monthly cost and warnings.
        match='prod-*' checks only matching table names."""
        with self._progress("Checking tables", unit="tables") as tick:
            reports = sorted(self.core.table_reports(match=match, metrics=metrics, progress=tick),
                             key=lambda r: r.info.name)
        rows: list[list[str]] = []
        warnings: list[list[str]] = []
        unreadable: list[str] = []
        no_usage: list[str] = []
        total = 0.0
        for report in reports:
            t, usage = report.info, report.metrics
            if "describe" in t.errors:
                unreadable.append(f"{t.name} ({_why(t.errors['describe'], 'dynamodb:DescribeTable')})")
                rows.append([t.name, "?", "-", "-", "-", "-", "-", "-", "-", "-"])
                continue
            cost = sum(table_monthly_cost(t, self.core.prices, usage).values())
            total += cost
            found = [message for level, message in table_findings(t, usage, self.core.prices) if level == "warn"]
            warnings += [[t.name, message] for message in found]
            if report.metrics_error:
                no_usage.append(f"{t.name} ({_why(report.metrics_error, 'cloudwatch:GetMetricData')})")
            rows.append([t.name, t.status or "?", _count(t.item_count), human_size(t.size_bytes),
                         _keys_label(t, t.attribute_types), _billing_label(t), str(len(t.indexes)), human_money(cost),
                         str(len(found)), human_age(t.created)])
        if metrics:
            basis = f"storage, capacity, backups and on-demand requests at the last 24h's rate, at {self._price_basis()}"
        else:
            basis = f"storage, capacity and backups at {self._price_basis()} (on-demand requests not included)"
        blocks: list[Any] = [
            _Title(f"DynamoDB tables in {self.core.region} ({len(reports)})",
                   (f"names matching {match!r} · " if match else "") + "item counts and sizes are DynamoDB's "
                   f"estimates, refreshed about every 6 hours · cost is {basis}"),
            _Cards([("Tables", f"{len(reports):,}"),
                    ("Total size", human_size(sum(r.info.size_bytes or 0 for r in reports))),
                    ("Est. cost / month", human_money(total)),
                    ("Tables with warnings", f"{len({name for name, _ in warnings}):,}")]),
        ]
        if not reports:
            where = f"matching {match!r} " if match else ""
            blocks.append(_Note(f"No tables {where}in {self.core.region}. Tables are regional: try "
                                "DynamoDBView(DynamoDBAnalyzer(region='eu-west-1'))."))
            self._show(blocks)
            return
        if unreadable:
            blocks.append(_Note(f"Couldn't describe {', '.join(unreadable)}.", "warn"))
        if no_usage:
            blocks.append(_Note(f"No CloudWatch usage for {', '.join(no_usage[:10])}{' …' if len(no_usage) > 10 else ''}: "
                                "their on-demand request cost and capacity checks are missing."))
        blocks.append(_Table(["Table", "Status", "Items", "Size", "Key", "Billing", "Indexes", "Est. $/month",
                              "Warnings", "Created"], rows, max_rows=0))
        if warnings:
            blocks.append(_Table(["Table", "Warning"], warnings,
                                 title="Warnings (table_info(name) shows every finding for one table)", max_rows=0))
        else:
            blocks.append(_Note("table_info(name) shows one table's indexes and how to query each, usage, cost "
                                "and every finding."))
        self._show(blocks)

    @_friendly_errors
    def table_info(self, table: str, *, metrics: bool = True, hours: int = 24) -> None:
        """Keys, indexes and how to query each, capacity, usage, backups, cost and risks.
        Also TTL, stream, encryption and tags; usage is CloudWatch's last `hours`."""
        info = self.core.describe(table)
        blocks: list[Any] = [_Title(f"Table {info.name}", info.arn or self.core.region)]
        usage = None
        if metrics:
            try:
                usage = self.core.table_metrics(table, hours=hours)
            except (ClientError, BotoCoreError) as exc:
                blocks.append(_Note(f"No CloudWatch usage ({_why(_error_name(exc), 'cloudwatch:GetMetricData')}), so "
                                    "the capacity checks and the on-demand request cost are missing.", "warn"))
        types = info.attribute_types
        cost = table_monthly_cost(info, self.core.prices, usage)
        ttl = (f"on ({info.ttl_attribute})" if info.ttl_status == "ENABLED"
               else _TTL_STATES.get(info.ttl_status or "DISABLED", str(info.ttl_status).lower()))
        pitr = "off" if not info.pitr else "on" + (f", {info.pitr_days} days" if info.pitr_days else "")
        encryption = info.encryption + (f" ({info.kms_key.rsplit('/', 1)[-1]})" if info.kms_key else "")
        cards = [
            ("Status", info.status or "?"),
            ("Items (estimate)", _count(info.item_count)),
            ("Size (estimate)", human_size(info.size_bytes)),
            ("Average item", human_size(info.avg_item_size)),
            ("Partition key", f"{info.partition_key} ({TYPE_NAMES.get(types.get(info.partition_key, ''), '?')})"),
            ("Sort key", f"{info.sort_key} ({TYPE_NAMES.get(types.get(info.sort_key, ''), '?')})" if info.sort_key else "none"),
            ("Billing", _billing_label(info)),
            ("Est. cost / month", human_money(sum(cost.values()))),
            ("Table class", _TABLE_CLASSES.get(info.table_class, info.table_class)),
            ("Stream", _STREAM_VIEWS.get(info.stream, "on") if info.stream else "off"),
            ("TTL", _section(info, "ttl", ttl)),
            ("Point-in-time recovery", _section(info, "pitr", pitr)),
            ("Deletion protection", "on" if info.deletion_protection else "off"),
            ("Encryption", encryption),
            ("Created", _fmt_dt(info.created)),
        ]
        if info.replicas:
            cards.append(("Replicas", ", ".join(info.replicas)))
        blocks.append(_Cards(cards))
        blocks += [_Note(message, level) for level, message in table_findings(info, usage, self.core.prices)]

        def capacity(idx: IndexInfo | None) -> list[str]:
            if info.on_demand:
                return []  # every row would say on-demand
            if idx is not None and idx.kind == "local":
                return ["the table's"]
            source = idx or info
            return [_capacity_label(source.read_capacity, source.write_capacity)]

        rows = [["(table)", "table", _keys_label(info, types), "all attributes", _count(info.item_count),
                 human_size(info.size_bytes), *capacity(None), _query_hint(info, None)]]
        rows += [[idx.name, f"{idx.kind} index", _keys_label(idx, types), _projection_label(idx),
                  _count(idx.item_count), human_size(idx.size_bytes), *capacity(idx), _query_hint(info, idx)]
                 for idx in info.indexes]
        headers = ["Read from", "Kind", "Key", "Projection", "Items", "Size"] + ([] if info.on_demand else ["Capacity"])
        blocks.append(_Table(headers + ["Query with"], rows, title="Table and indexes", max_rows=0))
        labels = {"storage": "Storage (table + indexes)", "capacity": "Provisioned capacity",
                  "requests": f"On-demand reads and writes (at the last {hours}h's rate)",
                  "backup": "Point-in-time recovery"}
        blocks.append(_Table(["Cost", "Est. $/month"], [[labels[k], human_money(v)] for k, v in cost.items()],
                             title=f"Estimated monthly cost ({self._price_basis()})"))
        if usage and usage.has_data:
            minutes = usage.period // 60
            rows = [["Read units", _units(usage.read_units), _units(usage.peak_reads),
                     "on-demand" if info.on_demand else _count(info.read_capacity)],
                    ["Write units", _units(usage.write_units), _units(usage.peak_writes),
                     "on-demand" if info.on_demand else _count(info.write_capacity)],
                    ["Read throttle events", f"{usage.read_throttles:,}", "", ""],
                    ["Write throttle events", f"{usage.write_throttles:,}", "", ""]]
            blocks.append(_Table(["Metric", f"Last {hours}h", f"Busiest {minutes} min, per second", "Provisioned"],
                                 rows, title="Usage (CloudWatch, table only)"))
        elif usage:
            blocks.append(_Note(f"No reads or writes recorded in CloudWatch in the last {hours}h."))
        if info.tags:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(info.tags.items())], title="Tags"))
        self._show(blocks)

    # ------------------------------------------------------------------- items

    @_friendly_errors
    def scan(self, table: str, n: int = 20, *, where: Any = None, index: str | None = None,
             attributes: str | Iterable[str] | None = None, scan_limit: int | None = 100_000) -> None:
        """Items from the start of the table (or an index) as a table; more() shows the next page.
        where= filters, e.g. where={'status': 'failed', 'total': ('>', 100)}."""
        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.scan(table, n, where=where, index=index, attributes=attributes, start_key=start,
                                  scan_limit=scan_limit, progress=tick)

        scan_limit = _as_count(scan_limit, "scan_limit")
        empty = "The table is empty." if where is None else _NO_MATCH
        if where is not None and scan_limit:
            empty += f" Each page reads at most {scan_limit:,} items (scan_limit); pass scan_limit=None to read on."
        self._page(f"Scan {_target(table, index)}", f"where {describe_filter(where)}" if where else
                   "from the start of the table", fetch, empty=empty)

    @_friendly_errors
    def query(self, table: str, partition: Any, sort: Any = None, *, n: int = 20, where: Any = None,
              index: str | None = None, attributes: str | Iterable[str] | None = None,
              descending: bool = False) -> None:
        """Items sharing one partition key, in sort-key order: query('orders', 'USER#42', sort=('begins_with', 'ORDER#')).
        index= queries a secondary index; descending=True starts from the highest sort key."""
        keys = self.core.keys(table, index)

        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.query(table, partition, sort, n=n, where=where, index=index, attributes=attributes,
                                   descending=descending, start_key=start, progress=tick)

        condition = f"{keys[0]} = {partition!r}"
        if sort is not None and len(keys) > 1:
            condition += ", " + describe_condition(keys[1], sort)
        sub = " · ".join(filter(None, [f"where {describe_filter(where)}" if where else "",
                                       "highest sort key first" if descending else ""]))
        empty = (f"No items with {keys[0]} = {partition!r}" + (" and that sort key" if sort is not None else "")
                 + (" matching the filter" if where else "") + ". Keys are case-sensitive and typed: "
                 "'42' (a string) and 42 (a number) are different keys.")
        self._page(f"Query {_target(table, index)}: {condition}", sub, fetch, empty=empty)

    @_friendly_errors
    def sample(self, table: str, n: int = 20, *, where: Any = None, index: str | None = None,
               attributes: str | Iterable[str] | None = None, scan_limit: int | None = 100_000) -> None:
        """About n items spread across the whole table (scan() only shows its start): a better first look."""
        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.sample(table, n, where=where, index=index, attributes=attributes, scan_limit=scan_limit,
                                    progress=tick)

        sub = "spread across the table" + (f" · where {describe_filter(where)}" if where else "")
        self._page(f"Sample of {_target(table, index)}", sub, fetch,
                   empty="The table is empty." if where is None else _NO_MATCH)

    @_friendly_errors
    def more(self) -> None:
        """Next page of the last scan / query / sql."""
        if self._pager is None:
            self._show([_Note("Nothing to continue: the last scan, query or sql returned everything "
                              "(or none has run yet).")])
            return
        title, sub, fetch, empty, start, number = self._pager
        self._page(title, sub, fetch, empty=empty, start=start, number=number)

    @_friendly_errors
    def get(self, table: str, *key: Any, as_json: bool = False) -> None:
        """One item by primary key with nested maps and lists expanded: get('orders', 'USER#42', 'ORDER#0017').
        The key can also be a dict: get('orders', {'pk': ..., 'sk': ...}). as_json=True adds a JSON copy."""
        primary = self.core.primary_key(table, key)
        item = self.core.get(table, primary)
        blocks: list[Any] = [_Title(f"Item in {table}", ", ".join(f"{k} = {format_value(v, 60)}"
                                                                   for k, v in primary.items()))]
        if item is None:
            blocks.append(_Note("No item with this key. Keys are case-sensitive and typed: '42' (a string) and "
                                "42 (a number) are different keys.", "warn"))
            self._show(blocks)
            return
        size = item_size(item)
        blocks.append(_Cards([
            ("Attributes", f"{len(item):,}"),
            ("Size (estimate)", human_size(size)),
            ("Read cost", f"{_units(read_units(size))} read unit{'' if read_units(size) == 1 else 's'} "
                          f"({_units(read_units(size, consistent=False))} if eventually consistent)"),
            ("Write cost", _plural(write_units(size), "write unit")),
        ]))
        if size > 300 * KB:
            blocks.append(_Note(f"This item is {human_size(size)}; DynamoDB rejects items over 400 KB. {_LARGE_ITEM_ADVICE}",
                                "warn"))
        blocks.append(_Table(["Attribute", "Type", "Value"], _item_rows(item, list(primary)), tree=True, max_rows=0))
        if as_json:
            blocks.append(_Text(to_json(item, indent=2), title="JSON"))
        self._show(blocks)

    @_friendly_errors
    def sql(self, statement: str, *parameters: Any, n: int = 50) -> None:
        """Run a SQL-like (PartiQL) statement: sql('SELECT * FROM "orders" WHERE pk = ?', 'USER#42'); more() continues.
        Without the partition key in WHERE, a SELECT scans the whole table."""
        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.sql(statement, *parameters, n=n, next_token=start, progress=tick)

        sub = _clip(" ".join(statement.split()), 200) + (f"  with {list(parameters)!r}" if parameters else "")
        self._page("PartiQL", sub, fetch, empty="No items came back.")

    # ---------------------------------------------------------------- analysis

    @_friendly_errors
    def schema(self, table: str, n: int = 1000, *, where: Any = None, index: str | None = None,
               spread: bool = True, max_depth: int = 2) -> None:
        """What the items look like: every attribute's types, fill rate, examples and range, key patterns.
        Map fields are nested under their map. Reads about n items spread across the table."""
        with self._progress("Sampling") as tick:
            p = self.core.profile(table, n, where=where, index=index, spread=spread, max_depth=max_depth,
                                  progress=tick)
        of = f" of ~{p.approx_item_count:,}" if p.approx_item_count else ""
        how = "spread across the table" if spread else "from the start of the table"
        blocks: list[Any] = [_Title(f"Schema of {_target(table, index)}",
                                    f"{p.items:,} items{of} profiled, {how}"
                                    + (f" · where {describe_filter(where)}" if where else ""))]
        if not p.items:
            blocks.append(_Note("No items to profile."))
            self._show(blocks)
            return
        top = sum(1 for a in p.attributes.values() if a.depth == 0)
        blocks.append(_Cards([
            ("Items profiled", f"{p.items:,}"),
            ("Attributes", f"{top:,}"),
            ("Map fields", f"{len(p.attributes) - top:,}"),
            ("Average item", human_size(p.avg_size)),
            ("Largest item", human_size(p.max_size)),
            ("Read units", _units(p.stats.read_units)),
        ]))
        blocks += [_Note(message, level) for level, message in profile_findings(p)]
        info = self.core.table(table)
        roles = {info.partition_key: "partition key", **({info.sort_key: "sort key"} if info.sort_key else {})}
        if index:
            idx = info.index(index)
            roles.update({idx.partition_key: f"{index} partition key",
                          **({idx.sort_key: f"{index} sort key"} if idx.sort_key else {})})
        rows = [["    " * a.depth + a.name + (f"  ({roles[a.path]})" if a.depth == 0 and a.path in roles else ""),
                 _types_label(a), _distinct_label(a), ", ".join(format_value(v, 30) for v in a.examples),
                 _range_label(a)] for a in p.attributes.values()]
        blocks.append(_Table(["Attribute", "Type", "Distinct", "Examples", "Range"], rows, title="Attributes",
                             bars=[_share(a.count, p.items) for a in p.attributes.values()], bar_label="Present in",
                             tree=True, max_rows=300))
        for key, patterns in p.key_patterns.items():
            total = sum(patterns.values())
            shown = list(patterns.items())[:15]
            blocks.append(_Table(["Pattern", "Items"], [[pattern, f"{count:,}"] for pattern, count in shown],
                                 title=f"Key patterns: {key}" + (f" ({roles[key]})" if key in roles else ""),
                                 bars=[_share(count, total) for _, count in shown], bar_label="% of items"))
        blocks.append(_Table(["Item size", "Items"], [[label, f"{st.count:,}"] for label, st in p.size_histogram.items()],
                             title="Item sizes", bars=[_share(st.count, p.items) for st in p.size_histogram.values()],
                             bar_label="% of items"))
        blocks.append(_Table([*p.keys, "Size", "Read units"],
                             [[format_value(key.get(k), 60) for k in p.keys] + [human_size(size), _units(read_units(size))]
                              for size, key in p.largest], title=f"Largest {len(p.largest)} items"))
        self._show(blocks)

    @_friendly_errors
    def value_counts(self, table: str, attribute: str, *, limit: int | None = 10_000, where: Any = None,
                     index: str | None = None, top: int = 30) -> None:
        """How often each value of an attribute occurs ('address.city' reaches into maps).
        On the partition key this is each item collection's size: hot or oversized partitions stand out."""
        limit, top = _as_count(limit, "limit"), _as_int(top, "top")
        with self._progress() as tick:
            vc = self.core.value_counts(table, attribute, limit=limit, where=where, index=index, progress=tick)
        st = vc.stats
        sub = f"{st.scanned:,} items read" + (f"; stopped at limit={limit:,}, pass limit=None to read the whole "
                                               "table" if st.truncated else " (the whole table)")
        blocks: list[Any] = [_Title(f"Values of {attribute} in {_target(table, index)}",
                                    sub + (f" · where {describe_filter(where)}" if where else "")),
                             _Cards([("Items", f"{vc.items:,}"), ("Distinct values", f"{len(vc.counts):,}"),
                                     ("Without it", f"{vc.missing.count:,}"), ("Read units", _units(st.read_units)),
                                     ("Read cost (on-demand)", self._read_cost(st))])]
        if attribute == self.core.keys(table, index)[0]:
            blocks.append(_Note("This is the partition key, so each count is the size of one item collection. "
                                "A few values holding most of the items (or data) can mean hot partitions."))
        shown = list(vc.counts.items())[:top]
        rows = [[format_value(value), f"{s.count:,}", human_size(s.size)] for value, s in shown]
        bars = [_share(s.count, vc.items) for _, s in shown]
        if vc.missing.count:
            rows.append(["(not set)", f"{vc.missing.count:,}", human_size(vc.missing.size)])
            bars.append(_share(vc.missing.count, vc.items))
        blocks.append(_Table(["Value", "Items", "Size"], rows, bars=bars, bar_label="% of items", max_rows=0,
                             title=f"Top {len(shown)} values" if len(vc.counts) > top else "Values"))
        if len(vc.counts) > top:
            blocks.append(_Note(f"{len(vc.counts) - top:,} more values not shown; pass top= for more."))
        self._show(blocks)

    @_friendly_errors
    def largest(self, table: str, n: int = 10, *, limit: int | None = 10_000, where: Any = None,
                index: str | None = None) -> None:
        """The biggest items (DynamoDB's size rules; the limit is 400 KB) among the first `limit` items read."""
        n, limit = _as_int(n, "n"), _as_count(limit, "limit")
        with self._progress() as tick:
            page = self.core.largest(table, n, limit=limit, where=where, index=index, progress=tick)
        st = page.stats
        sizes = [item_size(item) for item in page.items]
        sub = f"{st.scanned:,} items read" + (f"; stopped at limit={limit:,}, pass limit=None to read the whole "
                                               "table" if st.truncated else " (the whole table)")
        blocks: list[Any] = [
            _Title(f"Largest items in {_target(table, index)}", sub),
            _Cards([("Largest", human_size(max(sizes, default=None))), ("Read units", _units(st.read_units)),
                    ("Read cost (on-demand)", self._read_cost(st)), ("Time", f"{st.seconds:.1f}s")]),
        ]
        if any(size > 300 * KB for size in sizes):
            blocks.append(_Note(f"Some items are over 300 KB, close to DynamoDB's 400 KB item limit. {_LARGE_ITEM_ADVICE}",
                                "warn"))
        blocks.append(_Table([*page.keys, "Size", "Read units", "Attributes"],
                             [[format_value(item.get(k), 60) for k in page.keys]
                              + [human_size(size), _units(read_units(size)), f"{len(item):,}"]
                              for item, size in zip(page.items, sizes)]))
        blocks.append(_Note("Sizes are estimates using DynamoDB's sizing rules; get(table, key...) shows one item."))
        self._show(blocks)

    @_friendly_errors
    def count(self, table: str, *, where: Any = None, index: str | None = None) -> None:
        """Exact item count (optionally of items matching where=): reads, and pays for, every item."""
        with self._progress("Counting") as tick:
            st = self.core.count(table, where=where, index=index, progress=tick)
        info = self.core.table(table)
        estimate = info.index(index).item_count if index else info.item_count
        self._show([
            _Title(f"Count of {_target(table, index)}", f"where {describe_filter(where)}" if where else "full scan"),
            _Cards([("Items" + (" matching" if where else ""), f"{st.matched:,}"), ("Items read", f"{st.scanned:,}"),
                    ("Read units", _units(st.read_units)), ("Read cost (on-demand)", self._read_cost(st)),
                    ("Time", f"{st.seconds:.1f}s"), ("DynamoDB's estimate", _count(estimate))]),
        ])
