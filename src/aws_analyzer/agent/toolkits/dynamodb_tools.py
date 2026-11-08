"""
The DynamoDB toolkit: tools over DynamoDBAnalyzer for what each table holds, what it costs and what's wrong with it.
Which tables there are, with their items, size, billing, throttles and warnings (find_tables), one table's keys,
indexes (and how to query each), capacity, streams, TTL, backups, its last day from CloudWatch, its cost and
findings (table_details), and the shape of its items from a sample spread over the whole table: each attribute's
types, how many items have it, distinct values, ranges and the shapes of its keys (table_shape). A table's stream
is what feeds a Lambda function's DynamoDB trigger, and table_details names it, so the agent can follow a
function's failing trigger to the table and back with the Lambda tools.

table_shape reads items with a parallel Scan, the one read here that isn't a Describe or List call: the read-only
guard allows dynamodb:Scan by name (it reads and changes nothing), the budget's dynamodb_items caps how many items
it reads, and what it read is added to the question's AWS cost. Item values never go to the model: only attribute
names, types and counts, the smallest and largest number of a numeric attribute, string lengths, and key shapes
('USER#<number>'). Nothing here writes, deletes or changes a table: findings show the call or command that would.

The summarize_* functions are pure: they run on any list of TableReport, TableInfo, TableMetrics or TableProfile.
DYNAMODB_TOOL_READS is what each tool reads (its IAM permissions), for tools() and README. The analyzer's types are
imported for the type hints only, since its module loads when the first tool runs (_ddb_module). Tables are
regional: the toolkit reads the agent's region. find_tables lists at most _LISTED_TABLES tables, table_shape at most
_LISTED_ATTRIBUTES attributes and _KEY_SHAPES shapes per key.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, human_money, human_size
from ..budget import Budget
from ..results import _with_permissions, clamp_count, model_findings, share_pct
from .base import Toolkit

if TYPE_CHECKING:
    from ...dynamodb import (
        AttributeProfile, DynamoDBAnalyzer, IndexInfo, TableInfo, TableMetrics, TableProfile, TableReport,
    )

_DESCRIBE = ("dynamodb:ListTables", "dynamodb:DescribeTable")
_SETTINGS = ("dynamodb:DescribeTimeToLive", "dynamodb:DescribeContinuousBackups", "dynamodb:ListTagsOfResource")
_METRICS = "cloudwatch:GetMetricData"
DYNAMODB_TOOL_READS: dict[str, tuple[str, ...]] = {
    "find_tables": (*_DESCRIBE, *_SETTINGS, _METRICS),
    "table_details": ("dynamodb:DescribeTable", *_SETTINGS, _METRICS),
    "table_shape": ("dynamodb:DescribeTable", "dynamodb:Scan"),
}
_LISTED_TABLES = 60
_LISTED_ATTRIBUTES = 60
_KEY_SHAPES = 8
_DEFAULT_SAMPLE = 200
_METRIC_HOURS = 24
_SECTION_PERMISSIONS = {"describe": "dynamodb:DescribeTable", "ttl": "dynamodb:DescribeTimeToLive",
                        "pitr": "dynamodb:DescribeContinuousBackups", "tags": "dynamodb:ListTagsOfResource",
                        "metrics": _METRICS}


def _ddb_module() -> Any:
    """
    aws_analyzer.dynamodb, which the tools are built on. Loaded when a tool first needs it, so importing the agent
    loads no analyzer.
    """
    from ... import dynamodb

    return dynamodb


def _metrics_row(metrics: TableMetrics | None) -> dict[str, Any] | None:
    """
    A table's CloudWatch usage as the model reads it: capacity used in total and at its busiest, and throttles.
    """
    if metrics is None:
        return None
    return {"hours": metrics.hours, "has_data": metrics.has_data, "read_units": metrics.read_units,
            "write_units": metrics.write_units, "peak_reads_per_second": metrics.peak_reads,
            "peak_writes_per_second": metrics.peak_writes, "read_throttles": metrics.read_throttles,
            "write_throttles": metrics.write_throttles}


def _billing(info: TableInfo) -> str:
    """
    'on-demand', or 'provisioned 5 reads / 5 writes a second'.
    """
    if info.on_demand:
        return "on-demand"
    return f"provisioned {info.read_capacity or 0:,} reads / {info.write_capacity or 0:,} writes a second"


def _listed_table(report: TableReport, prices: dict[str, float]) -> dict[str, Any]:
    """
    One table in find_tables' list: its keys, items and size (DynamoDB's own estimate), billing, its last day's
    throttles, its estimated monthly cost and how many warnings its findings have.
    """
    ddb = _ddb_module()
    info, metrics = report.info, report.metrics
    if "describe" in info.errors:
        return {"name": info.name, "errors": _with_permissions(info.errors, _SECTION_PERMISSIONS)}
    cost = sum(ddb.table_monthly_cost(info, prices, metrics).values())
    found = ddb.table_findings(info, metrics, prices)
    row: dict[str, Any] = {"name": info.name, "status": info.status, "keys": info.keys,
                           "items": info.item_count, "size": human_size(info.size_bytes),
                           "billing": _billing(info), "indexes": [i.name for i in info.indexes],
                           "stream": info.stream, "estimated_monthly_cost": human_money(cost),
                           "throttles_last_day": (metrics.read_throttles + metrics.write_throttles) if metrics else None,
                           "warnings": sum(level == "warn" for level, _ in found)}
    errors = dict(info.errors)
    if report.metrics_error:
        errors["metrics"] = report.metrics_error
    if errors:
        row["errors"] = _with_permissions(errors, _SECTION_PERMISSIONS)
    return row


def summarize_tables(reports: list[TableReport], prices: dict[str, float], *,
                     limit: int = _LISTED_TABLES) -> dict[str, Any]:
    """
    find_tables()'s answer: every table in the region, the ones with the most warnings first, with their total
    estimated monthly cost and each one's share of it.
    """
    ddb = _ddb_module()
    rows = [_listed_table(r, prices) for r in reports]
    rows.sort(key=lambda r: (-(r.get("warnings") or 0), -(r.get("throttles_last_day") or 0), r["name"].lower()))
    costs = {r.info.name: sum(ddb.table_monthly_cost(r.info, prices, r.metrics).values()) for r in reports
             if "describe" not in r.info.errors}
    total = sum(costs.values())
    for row in rows:
        if row["name"] in costs:
            row["share_of_cost_pct"] = share_pct(costs[row["name"]], total)
    out: dict[str, Any] = {"count": len(rows), "estimated_monthly_cost": human_money(total), "tables": rows[:limit],
                           "note": "Item counts and sizes are DynamoDB's own estimates, refreshed about every 6 "
                                   "hours."}
    if len(rows) > limit:
        out["more"] = f"{len(rows) - limit:,} more not listed: narrow with match='prefix-*'"
    return out


def _query_call(info: TableInfo, index: IndexInfo | None) -> str:
    """
    The DynamoDBView query() that reads by a table's or index's keys, to give the user.
    """
    source = index or info
    sort = f", sort=<{source.sort_key}>" if source.sort_key else ""
    on_index = f", index={index.name!r}" if index else ""
    return f"query({info.name!r}, <{source.partition_key}>{sort}{on_index})"


def _secondary_index_row(info: TableInfo, index: IndexInfo) -> dict[str, Any]:
    """
    One secondary index: its kind, keys, what it projects, its status, size and capacity, and how to query it.
    """
    return {"name": index.name, "kind": index.kind, "keys": index.keys, "projection": index.projection,
            "projected": index.projected or None, "status": index.status, "backfilling": index.backfilling or None,
            "items": index.item_count, "size": human_size(index.size_bytes) if index.size_bytes is not None else None,
            "query": _query_call(info, index)}


def summarize_table(info: TableInfo, metrics: TableMetrics | None, prices: dict[str, float]) -> dict[str, Any]:
    """
    table_details()'s answer: a table's keys and their types, items and size, billing and capacity, its indexes
    (each with the query that reads it), its stream, TTL, point-in-time recovery, deletion protection, encryption
    and replicas, its last day from CloudWatch, its estimated monthly cost by part, and the findings with what to do.
    """
    ddb = _ddb_module()
    cost = ddb.table_monthly_cost(info, prices, metrics)
    out: dict[str, Any] = {
        "name": info.name, "status": info.status, "created": info.created, "partition_key": info.partition_key,
        "sort_key": info.sort_key, "key_types": info.attribute_types, "items": info.item_count,
        "size": human_size(info.size_bytes), "billing": _billing(info), "table_class": info.table_class,
        "indexes": [_secondary_index_row(info, i) for i in info.indexes], "query": _query_call(info, None),
        "stream": info.stream, "ttl_attribute": info.ttl_attribute, "ttl_status": info.ttl_status,
        "point_in_time_recovery": info.pitr, "deletion_protection": info.deletion_protection,
        "encryption": info.encryption, "replica_regions": info.replicas or None,
        "last_day": _metrics_row(metrics),
        "estimated_monthly_cost": {part: human_money(usd) for part, usd in cost.items()},
        "findings": model_findings(ddb.table_findings(info, metrics, prices)),
    }
    if info.errors:
        out["errors"] = _with_permissions(info.errors, _SECTION_PERMISSIONS)
    return out


def _attribute_row(attr: AttributeProfile, items: int) -> dict[str, Any]:
    """
    One attribute across the sample, without its values: its types and how many items have each, its fill rate,
    how many distinct values, a number's smallest and largest, a string's or list's shortest and longest.
    """
    ddb = _ddb_module()
    row: dict[str, Any] = {"attribute": attr.path,
                           "types": {ddb.TYPE_NAMES.get(kind, kind): n for kind, n in attr.types.most_common()},
                           "items": attr.count, "fill_pct": share_pct(attr.count, items),
                           "distinct": f"{attr.distinct:,}+" if attr.distinct_capped else attr.distinct}
    if attr.low is not None:
        row["number_range"] = [float(attr.low), float(attr.high)]
    if attr.min_len is not None:
        row["length_range"] = [attr.min_len, attr.max_len]
    if attr.empty:
        row["empty_values"] = attr.empty
    return row


def summarize_shape(profile: TableProfile, prices: dict[str, float]) -> dict[str, Any]:
    """
    table_shape()'s answer: how the items of a sample spread over the table look (how many were read of the
    table's estimate, their sizes), each attribute's types, fill rate and distinct values (no values), the shapes of
    the key values (an entity type of a single-table design each), what the read cost, and the findings.
    """
    ddb = _ddb_module()
    attributes = list(profile.attributes.values())
    stats = profile.stats
    out: dict[str, Any] = {
        "table": profile.table, "index": profile.index, "items_read": profile.items,
        "table_items_estimate": profile.approx_item_count, "average_item_size": human_size(round(profile.avg_size)),
        "largest_item_size": human_size(profile.max_size),
        "attributes_total": len(attributes),
        "attributes": [_attribute_row(a, profile.items) for a in attributes[:_LISTED_ATTRIBUTES]],
        "key_shapes": {key: dict(list(shapes.items())[:_KEY_SHAPES]) for key, shapes in profile.key_patterns.items()},
        "read": {"items_scanned": stats.scanned, "read_units": round(stats.read_units, 1),
                 "estimated_cost": human_money(ddb.request_cost(stats.read_units, 0.0, prices)),
                 "seconds": round(stats.seconds, 2)},
        "cut_short": stats.truncated,
        "findings": model_findings(ddb.profile_findings(profile)),
        "note": "A sample, not every item: counts and ranges are the sample's. Item values aren't shown.",
    }
    if len(attributes) > _LISTED_ATTRIBUTES:
        out["more"] = f"{len(attributes) - _LISTED_ATTRIBUTES:,} more attributes not listed"
    return out


class DynamoDBToolkit(Toolkit):
    """
    The agent's tools for Amazon DynamoDB, reading through DynamoDBAnalyzer: `analyzer` is one to use (else one is
    made on the agent's session, in its region). table_shape scans a sample of items (dynamodb:Scan), capped by the
    budget's dynamodb_items and billed to the question; no item's values reach the model.
    """

    name = "dynamodb"
    title = "DynamoDB"
    reads = DYNAMODB_TOOL_READS
    service = "dynamodb"
    services = {"GetMetricData": "cloudwatch"}
    view = "DynamoDBView"
    privacy = ("DynamoDB: never an item's values; table_shape sends only each attribute's types, counts, "
               "ranges and key shapes.")
    covers = ("Amazon DynamoDB: which tables exist and what they cost, each table's keys, indexes and how to query "
              "them, its capacity, throttles, stream, TTL and backups, and what its items look like (each "
              "attribute's types and how many items have it, the shapes of its keys).")

    def view_class(self) -> type:
        """
        DynamoDBView, whose commands the agent's findings can name.
        """
        return _ddb_module().DynamoDBView

    def make_analyzer(self, session: Any) -> DynamoDBAnalyzer:
        return _ddb_module().DynamoDBAnalyzer(session)

    @property
    def analyzer(self) -> DynamoDBAnalyzer:
        """
        The DynamoDBAnalyzer the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        return f"""\
Amazon DynamoDB
- Tables are regional: these tools read {self._region_text()}. Name a table exactly.
- For "which tables do I have" or "what does DynamoDB cost me", find_tables lists every table with its items,
  size, billing, throttles and estimated cost. table_details reads one: keys, indexes (with the query that reads
  each), capacity, stream, TTL, backups and its last day of use.
- For "what's in this table" or "why does my query miss items", table_shape samples up to {budget.dynamodb_items:,}
  items across the table: each attribute's types (an attribute with mixed types drops out of filters) and the
  shapes of its keys. It scans, which costs read capacity: use it once per table, and only when the shape matters.
- Findings name DynamoDBView calls (query(), scan(), schema()): give those to the user when they want the items
  themselves; you never see item values. Nothing here writes to a table."""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        find_tables, table_details and table_shape: methods whose type hints and docstrings tell the model how to
        call them, each reading through DynamoDBAnalyzer and returning a compact, JSON-ready summary.
        """
        return [self.find_tables, self.table_details, self.table_shape]

    def find_tables(self, match: str = "") -> dict[str, Any]:
        """
        Lists the DynamoDB tables in the region: each one's keys, items and size (DynamoDB's estimate), billing
        (on-demand or provisioned), indexes, stream, throttles in the last day, estimated monthly cost and its share
        of the total, and how many warnings it has (the ones with warnings first). match: a name glob like 'prod-*'.
        """
        core = self.analyzer
        reports = core.table_reports(match=str(match or "").strip() or None)
        self._count_cloudwatch(4 * sum(1 for r in reports if r.metrics is not None))
        out = summarize_tables(reports, core.prices)
        out["region"] = core.region
        warned = [r for r in out["tables"] if r.get("warnings")]
        out["_summary"] = (f"{_plural(len(reports), 'table')}, about {out['estimated_monthly_cost']}/month"
                           + (f", {len(warned):,} with warnings" if warned else ""))
        out["_next"] = [(self._view_call("tables", str(match or "").strip() or None),
                         "every table with its cost and warnings")]
        return out

    def table_details(self, table: str) -> dict[str, Any]:
        """
        Everything about one DynamoDB table: its partition and sort keys and their types, items and size, billing
        and capacity, each secondary index (keys, projection, size, and the query call that reads it), its stream
        (what a Lambda trigger reads), TTL, point-in-time recovery, deletion protection, encryption and replicas, its
        last 24 hours from CloudWatch (capacity used, peaks, throttles), its estimated monthly cost by part, and the
        findings with what to do. table: its exact name.
        """
        core = self.analyzer
        info = core.describe(table)
        metrics = None
        try:
            metrics = core.table_metrics(table, hours=_METRIC_HOURS)
            self._count_cloudwatch(4)
        except (ClientError, BotoCoreError) as exc:
            info.errors["metrics"] = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        out = summarize_table(info, metrics, core.prices)
        out["region"] = core.region
        warnings = sum(f["level"] == "warn" for f in out["findings"])
        out["_summary"] = (f"{_plural(info.item_count or 0, 'item')}, {human_size(info.size_bytes)}, {_billing(info)}"
                           + (f", {_plural(warnings, 'warning')}" if warnings else ""))
        out["_next"] = [(self._view_call("table_info", info.name), "keys, indexes and how to query each")]
        return out

    def table_shape(self, table: str, items: int = _DEFAULT_SAMPLE, index: str = "") -> dict[str, Any]:
        """
        What a DynamoDB table's items look like, from a sample spread over the whole table (a parallel Scan): each
        attribute's types and how many items have it (an attribute with several types silently drops out of
        filters), distinct values, number ranges and lengths, the shapes of the key values ('USER#<number>',
        'ORDER#<date>'), item sizes, what the scan read and cost, and the findings. Item values aren't returned.
        table: its exact name. items: how many to sample (capped by the budget). index: read a secondary index
        instead ('' for the table).
        """
        core = self.analyzer
        most = self.budget.dynamodb_items
        wanted = clamp_count(items, _DEFAULT_SAMPLE, max(most, 1))
        profile = core.profile(table, wanted, index=str(index or "").strip() or None)
        self._count_scan(profile.stats.read_units)
        out = summarize_shape(profile, core.prices)
        out["region"] = core.region
        if clamp_count(items, _DEFAULT_SAMPLE, 10**9) > wanted:
            out["capped_at"] = (f"{wanted:,} items, this question's limit (budget dynamodb_items): a scan costs read "
                                "capacity")
        mixed = [a["attribute"] for a in out["attributes"] if len(a["types"]) > 1]
        out["_summary"] = (f"{_plural(profile.items, 'item')} sampled, {_plural(len(profile.attributes), 'attribute')}"
                           + (f", mixed types in {', '.join(mixed[:3])}" if mixed else ""))
        out["_next"] = [(self._view_call("schema", profile.table, index=profile.index),
                         "the same shape with example values, in your notebook")]
        return out

    def _count_scan(self, read_units: float) -> None:
        """
        Adds what a scan read (on-demand read request units, at the analyzer's prices) to the AWS cost of the
        question being answered.
        """
        run = self.agent._run if self.agent is not None else None
        if run is None:
            return
        cost = _ddb_module().request_cost(read_units, 0.0, self.analyzer.prices)
        with run.lock:
            run.aws_cost += cost

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """
        A tool's answer when a table isn't there: the closest table names.
        """
        if _error_code(exc) != "ResourceNotFoundException":
            return None
        wanted = str(arguments.get("table") or "").strip()
        try:
            names = self.analyzer.list_table_names()
        except (ClientError, BotoCoreError):
            names = []
        return {"error": f"No table {wanted!r} in {self.analyzer.region}. Tables are regional and names are "
                         "case-sensitive.",
                "close_names": difflib.get_close_matches(wanted, names, n=3, cutoff=0.5) if wanted else names[:5],
                "hint": "Use one of close_names, or find_tables to list them."}
