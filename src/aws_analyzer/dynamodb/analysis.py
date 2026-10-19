"""
Pure analysis of DynamoDB tables and items: no AWS calls, so it runs on any list of plain item dicts
(a DynamoDB export, your own rows). Profiles and key patterns, filters and their expressions,
parsing DescribeTable, costs and the findings.
"""

from __future__ import annotations

import functools
import heapq
import math
import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Iterator

from boto3.dynamodb.conditions import Attr, ConditionBase, ConditionExpressionBuilder, Key

from .._kit.errors import _why
from .._kit.fmt import HOURS_PER_MONTH, Stat, _band, _plural, human_money, human_size

from .prices import DYNAMODB_PRICES
from .helpers import (
    GB, KB, TYPE_NAMES, _MISSING, _units, dynamo_type, get_path, item_size, read_units, to_dynamo, to_json,
)
from .models import AttributeProfile, IndexInfo, TableInfo, TableMetrics, TableProfile, ValueCounts


ITEM_SIZE_BANDS: list[
    tuple[str, int | None]
] = [  # (label, exclusive upper bound in bytes)
    ("< 1 KB", KB),
    ("1 - 4 KB", 4 * KB),
    ("4 - 16 KB", 16 * KB),
    ("16 - 100 KB", 100 * KB),
    ("100 - 300 KB", 300 * KB),
    ("300 - 400 KB", None),
]


_KEY_SEPARATORS = re.compile(r"([#|])")

_SEGMENT_SHAPES = [
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "<uuid>",
    ),
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
    """
    Shape of a key value, to spot the entity types of a single-table design:
    'USER#42' -> 'USER#<number>', 'ORDER#2024-05-01#a1b2' -> 'ORDER#<date>#<id>', 'PROFILE' -> 'PROFILE'.
    """
    if not isinstance(value, str):
        return {"N": "<number>", "B": "<binary>"}.get(
            dynamo_type(value), f"<{dynamo_type(value)}>"
        )
    parts = _KEY_SEPARATORS.split(value)
    return "".join(
        part if part in ("#", "|") else _segment_shape(part, len(parts) > 1 and i == 0)
        for i, part in enumerate(parts)
    )


def profile_items(
    items: Iterable[dict[str, Any]],
    table: str = "",
    *,
    keys: Iterable[str] = (),
    max_depth: int = 2,
    top_n: int = 10,
) -> TableProfile:
    """
    One pass over items -> TableProfile: every attribute (and map field down to max_depth) with its
    types, fill rate, distinct values, range and examples, plus key shapes and item sizes.
    Works on any plain dicts, e.g. from_dynamo_item() rows of a DynamoDB export in S3.
    """
    keys = list(dict.fromkeys(keys))
    profile = TableProfile(table=table, keys=keys)
    found: dict[str, AttributeProfile] = {}
    children: dict[str | None, list[str]] = defaultdict(
        list
    )  # parent path -> child paths, first seen first
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
        for path in lead + sorted(
            (p for p in paths if p not in lead),
            key=lambda p: (-found[p].count, rank[p]),
        ):
            yield path
            yield from ordered(path)

    profile.attributes = {path: found[path] for path in ordered(None)}
    profile.key_patterns = {k: dict(c.most_common()) for k, c in patterns.items() if c}
    profile.size_histogram = sizes
    profile.largest = [
        (size, key) for size, _, key in sorted(largest, key=lambda e: (-e[0], e[1]))
    ]
    return profile


def _hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    return to_json(value)


def count_values(items: Iterable[dict[str, Any]], attribute: str) -> ValueCounts:
    """
    How many items hold each value of `attribute` (a dotted path reaches into maps), and their
    total size. Maps, lists and sets are counted by their JSON text.
    """
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
    result.counts = dict(
        sorted(counts.items(), key=lambda kv: (-kv[1].count, -kv[1].size))
    )
    return result


_OPERATORS = {
    "=": "eq",
    "==": "eq",
    "!=": "ne",
    "<>": "ne",
    "<": "lt",
    "<=": "lte",
    ">": "gt",
    ">=": "gte",
    "between": "between",
    "begins_with": "begins_with",
    "contains": "contains",
    "in": "is_in",
    "exists": "exists",
    "not_exists": "not_exists",
    "type": "attribute_type",
}

_ARITY = {"between": 2, "exists": 0, "not_exists": 0}  # everything else takes one value

_SORT_KEY_OPERATORS = {"eq", "lt", "lte", "gt", "gte", "between", "begins_with"}


def _condition(attr: Attr | Key, spec: Any, *, key: bool = False) -> ConditionBase:
    """
    A plain value (equality) or an (operator, *values) tuple -> a boto3 condition on `attr`.
    """
    if not isinstance(spec, tuple):
        return attr.eq(spec)
    op = spec[0] if spec and isinstance(spec[0], str) else None
    if op is None or op.lower() not in _OPERATORS:
        raise ValueError(
            f"Can't read condition {spec!r}: use a value, or (operator, value) with one of "
            + ", ".join(_OPERATORS)
        )
    method, args = _OPERATORS[op.lower()], list(spec[1:])
    if key and method not in _SORT_KEY_OPERATORS:
        raise ValueError(
            f"Sort key conditions can be =, <, <=, >, >=, between or begins_with, not {op!r}"
        )
    if method == "is_in":
        args = [
            list(args[0])
            if len(args) == 1 and isinstance(args[0], (list, tuple, set, frozenset))
            else args
        ]
    if len(args) != _ARITY.get(method, 1):
        raise ValueError(
            f"{op!r} takes {_plural(_ARITY.get(method, 1), 'value')}, got {spec!r}"
        )
    return getattr(attr, method)(*args)


def build_filter(where: Any) -> ConditionBase | None:
    """
    `where` -> a boto3 condition. Takes None, a boto3 condition (Attr('a').gt(1) | Attr('b').exists()),
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
        raise ValueError(
            "where= takes a dict like {'status': 'failed'} or a boto3 condition"
        )
    conditions = [_condition(Attr(name), spec) for name, spec in where.items()]
    return functools.reduce(lambda a, b: a & b, conditions) if conditions else None


def describe_condition(name: str, spec: Any) -> str:
    """
    ('total', ('>', 100)) -> 'total > 100', ('sk', ('between', 'a', 'c')) -> "sk between 'a' and 'c'".
    """
    if not isinstance(spec, tuple):
        return f"{name} = {spec!r}"
    if not spec:
        return f"{name} {spec!r}"
    op, *args = spec
    joiner = " and " if op == "between" else ", "
    return f"{name} {op}" + (" " + joiner.join(map(repr, args)) if args else "")


def describe_filter(where: Any) -> str:
    """
    `where` as text: {'status': 'failed', 'total': ('>', 100)} -> "status = 'failed', total > 100".
    """
    if where is None:
        return ""
    if isinstance(where, ConditionBase):
        return "a boto3 condition"
    return ", ".join(describe_condition(name, spec) for name, spec in where.items())


_PATH_PART_RE = re.compile(r"^([^\[\]]+)((?:\[\d+\])*)$")


def _path_expression(path: str, names: dict[str, str]) -> str:
    """
    'address.city' -> '#p0.#p1' (placeholders avoid clashes with DynamoDB's reserved words).
    """
    parts = []
    for part in path.split("."):
        match = _PATH_PART_RE.match(part)
        if not match:
            raise ValueError(f"Can't read attribute path {path!r}")
        placeholder = f"#p{len(names)}"
        names[placeholder] = match.group(1)
        parts.append(placeholder + match.group(2))
    return ".".join(parts)


def _expression_params(
    *,
    key: ConditionBase | None = None,
    where: ConditionBase | None = None,
    attributes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    KeyConditionExpression / FilterExpression / ProjectionExpression with shared placeholders.
    """
    builder = ConditionExpressionBuilder()
    params: dict[str, Any] = {}
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for name, condition, is_key in (
        ("KeyConditionExpression", key, True),
        ("FilterExpression", where, False),
    ):
        if condition is not None:
            built = builder.build_expression(condition, is_key_condition=is_key)
            params[name] = built.condition_expression
            names.update(built.attribute_name_placeholders)
            values.update(
                {k: to_dynamo(v) for k, v in built.attribute_value_placeholders.items()}
            )
    if attributes:
        params["ProjectionExpression"] = ", ".join(
            _path_expression(path, names) for path in attributes
        )
    if names:
        params["ExpressionAttributeNames"] = names
    if values:
        params["ExpressionAttributeValues"] = values
    return params


def parse_table(desc: dict[str, Any]) -> TableInfo:
    """
    A DescribeTable 'Table' dict -> TableInfo (TTL, backups and tags need their own calls: see describe).
    """
    keys = {k["KeyType"]: k["AttributeName"] for k in desc.get("KeySchema", [])}
    info = TableInfo(
        name=desc["TableName"],
        status=desc.get("TableStatus", ""),
        arn=desc.get("TableArn", ""),
        partition_key=keys.get("HASH", ""),
        sort_key=keys.get("RANGE"),
        attribute_types={
            a["AttributeName"]: a["AttributeType"]
            for a in desc.get("AttributeDefinitions", [])
        },
        item_count=desc.get("ItemCount"),
        size_bytes=desc.get("TableSizeBytes"),
        created=desc.get("CreationDateTime"),
        billing_mode=desc.get("BillingModeSummary", {}).get(
            "BillingMode", "PROVISIONED"
        ),
        table_class=desc.get("TableClassSummary", {}).get("TableClass", "STANDARD"),
        deletion_protection=desc.get("DeletionProtectionEnabled"),
        replicas=[r["RegionName"] for r in desc.get("Replicas", [])],
    )
    if not info.on_demand:
        throughput = desc.get("ProvisionedThroughput", {})
        info.read_capacity, info.write_capacity = (
            throughput.get("ReadCapacityUnits"),
            throughput.get("WriteCapacityUnits"),
        )
    stream = desc.get("StreamSpecification", {})
    if stream.get("StreamEnabled"):
        info.stream = stream.get("StreamViewType", "on")
    sse = desc.get("SSEDescription") or {}
    if sse.get("Status") in ("ENABLED", "UPDATING") and sse.get("SSEType") == "KMS":
        info.encryption, info.kms_key = "KMS", sse.get("KMSMasterKeyArn")
    for kind, section in (
        ("global", "GlobalSecondaryIndexes"),
        ("local", "LocalSecondaryIndexes"),
    ):
        for idx in desc.get(section) or []:
            index_keys = {
                k["KeyType"]: k["AttributeName"] for k in idx.get("KeySchema", [])
            }
            projection = idx.get("Projection", {})
            throughput = (
                idx.get("ProvisionedThroughput", {})
                if kind == "global" and not info.on_demand
                else {}
            )
            info.indexes.append(
                IndexInfo(
                    name=idx["IndexName"],
                    kind=kind,
                    partition_key=index_keys.get("HASH", ""),
                    sort_key=index_keys.get("RANGE"),
                    projection=projection.get("ProjectionType", "ALL"),
                    projected=projection.get("NonKeyAttributes", []),
                    status=idx.get("IndexStatus"),
                    backfilling=bool(idx.get("Backfilling")),
                    item_count=idx.get("ItemCount"),
                    size_bytes=idx.get("IndexSizeBytes"),
                    read_capacity=throughput.get("ReadCapacityUnits"),
                    write_capacity=throughput.get("WriteCapacityUnits"),
                )
            )
    return info


def table_monthly_cost(
    info: TableInfo,
    prices: dict[str, float] | None = None,
    metrics: TableMetrics | None = None,
) -> dict[str, float]:
    """
    Estimated USD per month: 'storage' (table + indexes), 'capacity' (provisioned tables: table and
    global indexes at today's settings), 'requests' (on-demand tables, given `metrics`: the reads and
    writes of that window, extended to a month) and 'backup' (point-in-time recovery, when on).
    """
    prices = DYNAMODB_PRICES if prices is None else prices
    stored = (
        (info.size_bytes or 0) + sum(i.size_bytes or 0 for i in info.indexes)
    ) / GB
    cost = {
        "storage": stored
        * prices[
            "storage_ia"
            if info.table_class == "STANDARD_INFREQUENT_ACCESS"
            else "storage"
        ]
    }
    if not info.on_demand and info.read_capacity is not None:
        global_indexes = [i for i in info.indexes if i.kind == "global"]
        reads = info.read_capacity + sum(i.read_capacity or 0 for i in global_indexes)
        writes = (info.write_capacity or 0) + sum(
            i.write_capacity or 0 for i in global_indexes
        )
        cost["capacity"] = capacity_cost(reads, writes, prices)
    if info.on_demand and metrics is not None and metrics.has_data:
        spent = request_cost(
            metrics.read_units or 0.0, metrics.write_units or 0.0, prices
        )
        cost["requests"] = spent * HOURS_PER_MONTH / metrics.hours
    if info.pitr:
        cost["backup"] = (info.size_bytes or 0) / GB * prices["pitr"]
    return cost


def request_cost(
    read_units: float = 0.0,
    write_units: float = 0.0,
    prices: dict[str, float] | None = None,
) -> float:
    """
    USD for on-demand reads and writes (request units, e.g. ReadStats.read_units).
    """
    prices = DYNAMODB_PRICES if prices is None else prices
    return (
        read_units * prices["read_request"] + write_units * prices["write_request"]
    ) / 1e6


def capacity_cost(
    read_capacity: float = 0,
    write_capacity: float = 0,
    prices: dict[str, float] | None = None,
) -> float:
    """
    USD per month for provisioned read and write capacity units.
    """
    prices = DYNAMODB_PRICES if prices is None else prices
    return HOURS_PER_MONTH * (
        read_capacity * prices["read_capacity_hour"]
        + write_capacity * prices["write_capacity_hour"]
    )


TARGET_USE = 0.7  # suggested capacity leaves the busiest period at 70% of it (auto scaling's default target)

_LARGE_ITEM_ADVICE = (
    "Keeping large attributes (documents, blobs, long text) in S3 with a pointer in the item, or "
    "compressing them, cuts the cost of every read."
)

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
    """
    Plain-language data-quality and cost notes about profiled items -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    if not profile.items:
        return found
    table = _table_ref(profile.table)
    for attr in profile.attributes.values():
        kinds = [(kind, n) for kind, n in attr.types.most_common() if kind != "NULL"]
        if len(kinds) > 1:
            mix = ", ".join(
                f"{TYPE_NAMES.get(kind, kind)} in {n:,}" for kind, n in kinds
            )
            odd = kinds[-1][0]
            found.append(
                (
                    "warn",
                    f"'{attr.path}' holds different types ({mix} items). A filter or key condition "
                    "matches one type only, so the other items silently drop out. Find the "
                    f"{TYPE_NAMES.get(odd, odd)} ones with "
                    f"scan({table}, where={{{attr.path!r}: ('type', {odd!r})}}).",
                )
            )
    empties = [a for a in profile.attributes.values() if a.empty]
    if empties:
        listed = ", ".join(
            f"'{a.path}' ({_plural(a.empty, 'item')})" for a in empties[:8]
        )
        found.append(
            (
                "info",
                f"Empty strings or binary values in {listed}. They count as present, so "
                f"where={{{empties[0].path!r}: ('exists',)}} matches them too; if they mean "
                "\"unknown\", add ('!=', '') to filters.",
            )
        )
    big = profile.size_histogram.get("300 - 400 KB", Stat()).count
    if big:
        found.append(
            (
                "warn",
                f"{_plural(big, 'item')} over 300 KB. DynamoDB rejects items over 400 KB, and one "
                "strongly consistent read of an item that size costs up to 100 read units. "
                f"largest({table}) lists them. {_LARGE_ITEM_ADVICE}",
            )
        )
    if profile.avg_size > 4 * KB:
        units = read_units(math.ceil(profile.avg_size))
        found.append(
            (
                "info",
                f"The average item is {human_size(profile.avg_size)}, so reading one costs "
                f"{_units(units)} read units (half that eventually consistent). Reading fewer "
                f"attributes doesn't lower this: the whole item is billed. {_LARGE_ITEM_ADVICE}",
            )
        )
    singles = sum(
        1 for a in profile.attributes.values() if a.depth == 0 and a.count == 1
    )
    if profile.items >= 20 and singles >= 20:
        found.append(
            (
                "info",
                f"{singles:,} attributes appear in only one item. Attribute names built from data "
                "(dates, IDs) can't be indexed and are hard to query. Keeping that data in a map, "
                "or as separate items with the date or ID in the sort key, makes it queryable.",
            )
        )
    return found


def _capacity_findings(
    info: TableInfo, metrics: TableMetrics, prices: dict[str, float]
) -> list[tuple[str, str]]:
    """
    Provisioned capacity against CloudWatch's busiest period: near the limit, or paying for unused units.
    """
    found: list[tuple[str, str]] = []
    minutes = metrics.period // 60
    low: list[str] = []
    suggested = {"Reads": info.read_capacity or 0, "Writes": info.write_capacity or 0}
    for label, peak, capacity in (
        ("Reads", metrics.peak_reads, info.read_capacity),
        ("Writes", metrics.peak_writes, info.write_capacity),
    ):
        if not capacity:
            continue
        share = (peak or 0.0) / capacity
        if share >= 0.8:
            found.append(
                (
                    "warn",
                    f"{label} averaged {_units(peak)} units/s in the busiest {minutes} minutes, "
                    f"{share:.0%} of the {capacity:,} provisioned. Short bursts above it get throttled: "
                    "raise the capacity or turn on auto scaling.",
                )
            )
        elif share < 0.2:
            shown = "under 1%" if 0 < share < 0.01 else f"{share:.0%}"
            low.append(
                f"{label.lower()} peaked at {shown} of the {capacity:,} provisioned units"
            )
            suggested[label] = max(1, math.ceil((peak or 0.0) / TARGET_USE))
    if not low:
        return found
    now = capacity_cost(info.read_capacity or 0, info.write_capacity or 0, prices)
    lowered = capacity_cost(suggested["Reads"], suggested["Writes"], prices)
    on_demand = (
        request_cost(metrics.read_units or 0.0, metrics.write_units or 0.0, prices)
        * HOURS_PER_MONTH
        / metrics.hours
    )
    options = []
    if lowered < now:
        options.append(
            f"{suggested['Reads']:,} read / {suggested['Writes']:,} write units (the busiest period at "
            f"{TARGET_USE:.0%} use) would cost {human_money(lowered)}"
        )
    if on_demand < now:
        options.append(f"on-demand at this traffic about {human_money(on_demand)}")
    if not options:
        return found
    saving = now - min(lowered, on_demand)
    text = "; ".join(low)
    found.append(
        (
            "warn" if saving >= 10 else "info",
            f"Most of the provisioned capacity goes unused: {text} in the last {metrics.hours}h. The table's own "
            f"capacity costs {human_money(now)}/month; {' and '.join(options)}. If the last {metrics.hours}h were "
            f"typical, that saves up to {human_money(saving)}/month. Auto scaling can also adjust the capacity for "
            "you.",
        )
    )
    return found


def table_findings(
    info: TableInfo,
    metrics: TableMetrics | None = None,
    prices: dict[str, float] | None = None,
) -> list[tuple[str, str]]:
    """
    Plain-language risks and cost notes for a table, each with what to do about it -> [(level, message)].
    """
    prices = DYNAMODB_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    pk = info.partition_key or "<partition key>"
    if info.status and info.status != "ACTIVE":
        found.append(("info", f"The table is {info.status}."))
    for idx in info.indexes:
        if idx.backfilling or (idx.status and idx.status != "ACTIVE"):
            state = "backfilling" if idx.backfilling else idx.status
            found.append(
                (
                    "info",
                    f"Index {idx.name} is {state}; queries on it may miss items until it's done.",
                )
            )
    if "pitr" not in info.errors and info.pitr is False:
        price = (
            f", for about {human_money(info.size_bytes / GB * prices['pitr'])}/month at this table's size"
            if info.size_bytes
            else ""
        )
        found.append(
            (
                "warn",
                "Point-in-time recovery is off: an accidental delete or bad write can't be rolled "
                f"back. Turning it on keeps continuous backups for up to 35 days{price}: "
                f"aws dynamodb update-continuous-backups --table-name {info.name} "
                "--point-in-time-recovery-specification PointInTimeRecoveryEnabled=true",
            )
        )
    if info.deletion_protection is False:
        found.append(
            (
                "info",
                "Deletion protection is off, so one DeleteTable call removes the table. To turn it on: "
                f"aws dynamodb update-table --table-name {info.name} --deletion-protection-enabled",
            )
        )
    if metrics and (metrics.read_throttles or metrics.write_throttles):
        if info.on_demand:
            fix = (
                "On an on-demand table this usually means one partition key gets most of the traffic, or traffic "
                "more than doubled suddenly"
            )
        else:
            fix = "Raise the capacity or turn on auto scaling; if that doesn't help, one partition key may be hot"
        found.append(
            (
                "warn",
                f"{metrics.read_throttles:,} read and {metrics.write_throttles:,} write throttle "
                f"events in the last {metrics.hours}h: some requests were rejected and retried, which "
                f"slows the application. {fix}. value_counts({info.name!r}, {pk!r}) shows whether a "
                "few partition keys hold most of the items.",
            )
        )
    if metrics and metrics.has_data and not info.on_demand:
        found += _capacity_findings(info, metrics, prices)
    if info.avg_item_size and info.avg_item_size > 4 * KB:
        found.append(
            (
                "info",
                f"Items average {human_size(info.avg_item_size)}, so each read of one item costs "
                f"{_units(read_units(math.ceil(info.avg_item_size)))} read units. {_LARGE_ITEM_ADVICE} "
                f"largest({info.name!r}) shows the biggest items.",
            )
        )
    if info.errors:
        parts = [
            f"{_SECTIONS.get(k, (k, ''))[0]} ({_why(v, _SECTIONS[k][1]) if k in _SECTIONS else v})"
            for k, v in info.errors.items()
        ]
        found.append(("info", "Couldn't read " + ", ".join(parts) + "."))
    return found
