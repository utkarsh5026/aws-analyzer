"""
DynamoDBAnalyzer: talks to DynamoDB and CloudWatch, and returns the dataclasses in models.
Never prints, and never writes to a table.
"""

from __future__ import annotations

import fnmatch
import heapq
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Iterator

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_name
from .._kit.fmt import _as_count, _as_int, _plural, _utcnow

from .prices import DYNAMODB_PRICES
from .helpers import _number, from_dynamo_item, item_size, to_dynamo
from .models import ItemPage, ReadStats, TableInfo, TableMetrics, TableProfile, TableReport, ValueCounts
from .analysis import _condition, _expression_params, build_filter, count_values, parse_table, profile_items


_FROM_RE = re.compile(
    r'\bFROM\s+(?:"([^"]+)"|([\w.-]+))(?:\."([^"]+)")?', re.IGNORECASE
)


class DynamoDBAnalyzer(_BaseAnalyzer):
    """
    Pure-logic DynamoDB analysis: every method returns data; nothing is printed or written.

    Items are plain dicts of Python values (see from_dynamo). Methods that read items take `where`
    (a filter, see build_filter), `index` (read a secondary index instead of the table) and
    `progress` (called with the running count of items read). Scans are billed per item read and
    share the table's capacity with your application, so the whole-table analyses stop after
    `limit` items unless you pass limit=None.
    `prices` overrides DYNAMODB_PRICES for cost estimates.
    """

    _LIST_PRICES = DYNAMODB_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        prices: dict[str, float] | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._client = client
        self._cloudwatch_client: Any = None
        self._tables: dict[str, TableInfo] = {}

    @property
    def client(self) -> Any:
        """
        The DynamoDB client, made on first use so a missing region shows up as a readable error.
        """
        if self._client is None:
            try:
                self._client = self.session.client("dynamodb", config=self._config)
            except NoRegionError:
                raise ValueError(
                    "No AWS region is set, and DynamoDB tables are regional. Pass one: "
                    "DynamoDBView(DynamoDBAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                ) from None
        return self._client

    def _cloudwatch(self) -> Any:
        if self._cloudwatch_client is None:
            self._cloudwatch_client = self.session.client(
                "cloudwatch", region_name=self.region
            )
        return self._cloudwatch_client

    # ------------------------------------------------------------------ tables

    def list_table_names(self) -> list[str]:
        return [
            name
            for page in self.client.get_paginator("list_tables").paginate()
            for name in page.get("TableNames", [])
        ]

    def list_tables(self, *, details: bool = True) -> list[TableInfo]:
        """
        Every table in the region. details=True describes each one (in parallel) for keys, counts and billing.
        """
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

    def table_reports(
        self,
        *,
        match: str | None = None,
        metrics: bool = True,
        max_workers: int = 8,
        progress: Callable[[int], None] | None = None,
    ) -> list[TableReport]:
        """
        describe() (and CloudWatch usage unless metrics=False) for every table, checked in parallel.
        match: only tables whose name matches this glob, e.g. 'prod-*'.
        """
        names = [
            n
            for n in self.list_table_names()
            if match is None or fnmatch.fnmatchcase(n, match)
        ]
        if metrics and names:
            self._cloudwatch()  # boto3 sessions aren't thread-safe: make the client before the threads start

        def check(name: str) -> TableReport:
            try:
                report = TableReport(self.describe(name))
            except (ClientError, BotoCoreError) as exc:
                return TableReport(
                    TableInfo(name, errors={"describe": _error_name(exc)})
                )
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
        """
        Keys, indexes, billing and DynamoDB's item count / size estimate (one DescribeTable call, cached).
        """
        if refresh or table not in self._tables:
            self._tables[table] = parse_table(
                self.client.describe_table(TableName=table)["Table"]
            )
        return self._tables[table]

    def describe(self, table: str) -> TableInfo:
        """
        Everything about a table: table() plus TTL, point-in-time recovery and tags.
        """
        info = self.table(table, refresh=True)

        def get(section: str, call: Callable[[], Any]) -> Any:
            try:
                return call()
            except (ClientError, BotoCoreError) as exc:
                info.errors[section] = _error_name(exc)
            return None

        if resp := get(
            "ttl", lambda: self.client.describe_time_to_live(TableName=table)
        ):
            ttl = resp.get("TimeToLiveDescription", {})
            info.ttl_status, info.ttl_attribute = (
                ttl.get("TimeToLiveStatus"),
                ttl.get("AttributeName"),
            )
        if resp := get(
            "pitr", lambda: self.client.describe_continuous_backups(TableName=table)
        ):
            pitr = resp.get("ContinuousBackupsDescription", {}).get(
                "PointInTimeRecoveryDescription", {}
            )
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
            resp = self.client.list_tags_of_resource(
                ResourceArn=arn, **({"NextToken": token} if token else {})
            )
            tags.update({t["Key"]: t["Value"] for t in resp.get("Tags", [])})
            token = resp.get("NextToken")
            if not token:
                return tags

    def table_metrics(self, table: str, *, hours: int = 24) -> TableMetrics:
        """
        Read / write units consumed (in total and in the busiest period) and throttle events from
        CloudWatch, for the table itself (not its indexes). Needs cloudwatch:GetMetricData.
        """
        period = max(
            300, math.ceil(hours * 3600 / 1440 / 60) * 60
        )  # at most ~1,440 points per metric
        names = [
            "ConsumedReadCapacityUnits",
            "ConsumedWriteCapacityUnits",
            "ReadThrottleEvents",
            "WriteThrottleEvents",
        ]
        queries = [
            {
                "Id": f"m{i}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/DynamoDB",
                        "MetricName": name,
                        "Dimensions": [{"Name": "TableName", "Value": table}],
                    },
                    "Period": period,
                    "Stat": "Sum",
                },
            }
            for i, name in enumerate(names)
        ]
        now = _utcnow()
        values: dict[str, list[float]] = {name: [] for name in names}
        for page in (
            self._cloudwatch()
            .get_paginator("get_metric_data")
            .paginate(
                MetricDataQueries=queries,
                StartTime=now - timedelta(hours=hours),
                EndTime=now,
            )
        ):
            for series in page["MetricDataResults"]:
                values[names[int(series["Id"][1:])]] += series.get("Values", [])
        reads, writes = (
            values["ConsumedReadCapacityUnits"],
            values["ConsumedWriteCapacityUnits"],
        )
        return TableMetrics(
            table,
            hours,
            period,
            read_units=sum(reads) if reads else None,
            write_units=sum(writes) if writes else None,
            peak_reads=max(reads) / period if reads else None,
            peak_writes=max(writes) / period if writes else None,
            read_throttles=int(sum(values["ReadThrottleEvents"])),
            write_throttles=int(sum(values["WriteThrottleEvents"])),
        )

    # -------------------------------------------------------------------- keys

    def keys(self, table: str, index: str | None = None) -> list[str]:
        """
        [partition key, sort key] of the table, or of `index`.
        """
        info = self.table(table)
        return info.index(index).keys if index else info.keys

    def key_attributes(self, table: str, index: str | None = None) -> list[str]:
        """
        Every key attribute an item read from the table / index carries, the index's first. Tables of
        items show these first, and they make up the start key of the next page.
        """
        info = self.table(table)
        return list(
            dict.fromkeys((info.index(index).keys if index else []) + info.keys)
        )

    def _key_value(self, table: str, name: str, value: Any) -> Any:
        """
        Match a key value to its declared type, so '42' works for a number key and 42 for a string key.
        """
        kind = self.table(table).attribute_types.get(name)
        if kind == "N" and isinstance(value, str):
            try:
                return _number(value.strip())
            except InvalidOperation:
                raise ValueError(
                    f"{name} is a number key, and {value!r} isn't a number"
                ) from None
        if (
            kind == "S"
            and isinstance(value, (int, float, Decimal))
            and not isinstance(value, bool)
        ):
            return str(value)
        return value

    def primary_key(self, table: str, key: tuple | list | dict) -> dict[str, Any]:
        """
        ('USER#1', 'ORDER#7') or {'pk': 'USER#1', 'sk': 'ORDER#7'} -> the key dict GetItem needs.
        """
        names = self.table(table).keys
        if isinstance(key, dict):
            given = dict(key)
        elif len(key) == 1 and isinstance(key[0], dict):
            given = dict(key[0])
        elif len(key) == len(names):
            given = dict(zip(names, key))
        else:
            raise ValueError(
                f"{table}'s key is ({', '.join(names)}): pass {_plural(len(names), 'value')} or a dict"
            )
        missing = [name for name in names if name not in given]
        if missing:
            raise ValueError(f"Missing key attribute {', '.join(missing)} for {table}")
        return {name: self._key_value(table, name, given[name]) for name in names}

    # ------------------------------------------------------------------- items

    def get(
        self, table: str, *key: Any, consistent: bool = False
    ) -> dict[str, Any] | None:
        """
        One item by primary key: get('orders', 'USER#1', 'ORDER#7') or get('orders', {'pk': ..., 'sk': ...}).
        None when there's no such item.
        """
        primary = self.primary_key(table, key)
        resp = self.client.get_item(
            TableName=table,
            Key={k: to_dynamo(v) for k, v in primary.items()},
            ConsistentRead=consistent,
        )
        return from_dynamo_item(resp["Item"]) if "Item" in resp else None

    def _projection(
        self, table: str, index: str | None, attributes: str | Iterable[str] | None
    ) -> list[str] | None:
        """
        Attributes to read, always with the key attributes (tables need them, and so does the next page).
        """
        if attributes is None:
            return None
        attributes = [attributes] if isinstance(attributes, str) else list(attributes)
        return list(dict.fromkeys(self.key_attributes(table, index) + attributes))

    def _scan_params(
        self,
        table: str,
        index: str | None,
        where: Any,
        attributes: Any,
        consistent: bool = False,
    ) -> dict[str, Any]:
        params = {
            "TableName": table,
            **_expression_params(
                where=build_filter(where),
                attributes=self._projection(table, index, attributes),
            ),
        }
        if index:
            params["IndexName"] = index
        if consistent:
            params["ConsistentRead"] = True
        return params

    def scan(
        self,
        table: str,
        n: int | None = 100,
        *,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        start_key: Any = None,
        scan_limit: int | None = None,
        consistent: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        Up to n items from the start of the table (or index), or after `start_key` (a page's last_key).
        With `where`, keeps reading until n items match; scan_limit caps how many items are read.
        """
        params = self._scan_params(table, index, where, attributes, consistent)
        return self._read(
            "scan",
            table,
            index,
            params,
            n=n,
            start_key=start_key,
            scan_limit=scan_limit,
            progress=progress,
        )

    def query(
        self,
        table: str,
        partition: Any,
        sort: Any = None,
        *,
        n: int | None = 100,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        descending: bool = False,
        start_key: Any = None,
        scan_limit: int | None = None,
        consistent: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        Items whose partition key is `partition`, in sort-key order (on the table, or on `index`).
        sort narrows the sort key: a value, or ('begins_with', 'ORDER#'), ('between', a, b), ('>=', x) ...
        descending=True starts from the highest sort key.
        """
        keys = self.keys(table, index)
        condition = Key(keys[0]).eq(self._key_value(table, keys[0], partition))
        if sort is not None:
            if len(keys) < 2:
                raise ValueError(
                    f"{index or table} has no sort key, so sort= can't be used"
                )
            op, *args = sort if isinstance(sort, tuple) else ("=", sort)
            condition &= _condition(
                Key(keys[1]),
                (op, *(self._key_value(table, keys[1], a) for a in args)),
                key=True,
            )
        params = {
            "TableName": table,
            "ScanIndexForward": not descending,
            **_expression_params(
                key=condition,
                where=build_filter(where),
                attributes=self._projection(table, index, attributes),
            ),
        }
        if index:
            params["IndexName"] = index
        if consistent:
            params["ConsistentRead"] = True
        return self._read(
            "query",
            table,
            index,
            params,
            n=n,
            start_key=start_key,
            scan_limit=scan_limit,
            progress=progress,
        )

    def _read(
        self,
        operation: str,
        table: str,
        index: str | None,
        params: dict[str, Any],
        *,
        n: int | None,
        start_key: Any = None,
        scan_limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        Page through a scan or query until n items came back, the data ran out, or scan_limit items were
        read. Stopping inside a page sets last_key to the last item returned, so passing it back as
        start_key resumes right after that item.
        """
        n, scan_limit = _as_count(n, "n"), _as_count(scan_limit, "scan_limit")
        if n is not None and n < 1:
            raise ValueError("n must be at least 1 (or None for everything)")
        page = ItemPage(
            table=table,
            index=index,
            operation=operation,
            keys=self.key_attributes(table, index),
        )
        params = dict(params, ReturnConsumedCapacity="TOTAL")
        if start_key:
            params["ExclusiveStartKey"] = start_key
        filtered = "FilterExpression" in params
        call = getattr(self.client, operation)
        started = time.monotonic()
        while True:
            caps = (
                [n - len(page.items)] if n is not None and not filtered else []
            )  # a filter drops items after the read
            if scan_limit is not None:
                caps.append(scan_limit - page.stats.scanned)
            if caps:
                params["Limit"] = max(1, min(caps))
            resp = call(**params)
            page.stats.scanned += resp.get("ScannedCount", 0)
            page.stats.read_units += resp.get("ConsumedCapacity", {}).get(
                "CapacityUnits", 0.0
            )
            raw = resp.get("Items", [])
            page.last_key = resp.get("LastEvaluatedKey")
            if n is not None and len(raw) > n - len(page.items):
                raw = raw[: n - len(page.items)]
                page.last_key = {
                    name: raw[-1][name] for name in page.keys if name in raw[-1]
                }
            page.items += [from_dynamo_item(item) for item in raw]
            if progress:
                progress(page.stats.scanned)
            if (
                page.last_key is None
                or (n is not None and len(page.items) >= n)
                or (scan_limit is not None and page.stats.scanned >= scan_limit)
            ):
                break
            params["ExclusiveStartKey"] = page.last_key
        page.stats.matched = len(page.items)
        page.stats.truncated = page.last_key is not None
        page.stats.seconds = time.monotonic() - started
        return page

    def sample(
        self,
        table: str,
        n: int = 100,
        *,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        segments: int | None = None,
        scan_limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        About n items spread over the whole table. scan() starts at the beginning, where the first
        items can all share a few partition keys; this reads a few items from each of `segments` slices
        of the key space (parallel-scan segments, fetched in parallel; default one per item, up to 100).
        scan_limit caps the items read.
        """
        n, scan_limit = _as_int(n, "n"), _as_count(scan_limit, "scan_limit")
        if n < 1:
            raise ValueError("n must be at least 1")
        segments = segments or min(n, 100)
        params = dict(
            self._scan_params(table, index, where, attributes), TotalSegments=segments
        )
        page = ItemPage(
            table=table,
            index=index,
            operation="sample",
            keys=self.key_attributes(table, index),
        )
        budget = (
            None if scan_limit is None else math.ceil(scan_limit / segments)
        )  # items each segment may read
        cursors: dict[int, Any] = dict.fromkeys(
            range(segments)
        )  # open segment -> where to resume it
        spent: dict[int, int] = dict.fromkeys(range(segments), 0)
        started = time.monotonic()

        def read(job: tuple[int, Any, int | None], share: int) -> ItemPage:
            segment, start, limit = job
            return self._read(
                "scan",
                table,
                index,
                dict(params, Segment=segment),
                n=share,
                start_key=start,
                scan_limit=limit,
            )

        with ThreadPoolExecutor(max_workers=min(segments, 16)) as pool:
            while cursors and len(page.items) < n:
                jobs = [
                    (s, cursors[s], None if budget is None else budget - spent[s])
                    for s in cursors
                ]
                share = math.ceil((n - len(page.items)) / len(jobs))
                for (segment, _, _), part in zip(
                    jobs, pool.map(read, jobs, [share] * len(jobs))
                ):
                    page.items += part.items
                    page.stats.add(part.stats)
                    spent[segment] += part.stats.scanned
                    if part.last_key is None or (
                        budget is not None and spent[segment] >= budget
                    ):
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

    def sql(
        self,
        statement: str,
        *parameters: Any,
        n: int | None = 100,
        next_token: str | None = None,
        consistent: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        Run a PartiQL statement: sql('SELECT * FROM "orders" WHERE pk = ?', 'USER#1'). Parameters fill
        the ? placeholders in order. Pages are kept whole (so you may get a few more than n) and last_key
        continues exactly, as next_token=. Without the partition key in WHERE, a SELECT scans the table.
        """
        n = _as_count(n, "n")
        params: dict[str, Any] = {
            "Statement": statement,
            "ReturnConsumedCapacity": "TOTAL",
        }
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
            page.stats.read_units += resp.get("ConsumedCapacity", {}).get(
                "CapacityUnits", 0.0
            )
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

    def _pages(
        self,
        params: dict[str, Any],
        stats: ReadStats,
        *,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Scan responses (up to 1 MB each) until the end of the table or until `limit` items were read.
        """
        limit = _as_count(limit, "limit")
        params = dict(params, ReturnConsumedCapacity="TOTAL")
        started = time.monotonic()
        try:
            while True:
                if limit is not None:
                    params["Limit"] = max(1, limit - stats.scanned)
                resp = self.client.scan(**params)
                stats.scanned += resp.get("ScannedCount", 0)
                stats.read_units += resp.get("ConsumedCapacity", {}).get(
                    "CapacityUnits", 0.0
                )
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

    def iter_items(
        self,
        table: str,
        *,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
        stats: ReadStats | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Stream a scan's items without keeping them in memory. `limit` caps the items read; pass
        stats=ReadStats() to see afterwards what the scan read and cost.
        """
        stats = ReadStats() if stats is None else stats
        params = self._scan_params(table, index, where, attributes)
        for resp in self._pages(params, stats, limit=limit, progress=progress):
            for raw in resp.get("Items", []):
                stats.matched += 1
                yield from_dynamo_item(raw)

    def count(
        self,
        table: str,
        *,
        where: Any = None,
        index: str | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> ReadStats:
        """
        Exact item count (of items matching `where`) as ReadStats.matched: a full scan with Select=COUNT.
        No items come back, but every item is still read and billed. table(t).item_count is an instant
        estimate, refreshed about every 6 hours.
        """
        stats = ReadStats()
        params = dict(self._scan_params(table, index, where, None), Select="COUNT")
        for resp in self._pages(params, stats, progress=progress):
            stats.matched += resp.get("Count", 0)
        return stats

    def value_counts(
        self,
        table: str,
        attribute: str,
        *,
        limit: int | None = 10_000,
        where: Any = None,
        index: str | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> ValueCounts:
        """
        How often each value of `attribute` (a dotted path reaches into maps) occurs among the first
        `limit` items read (None = the whole table). On the partition key: the size of each item collection.
        """
        stats = ReadStats()
        items = self.iter_items(
            table, where=where, index=index, limit=limit, progress=progress, stats=stats
        )
        result = count_values(items, attribute)
        result.table, result.stats = table, stats
        return result

    def largest(
        self,
        table: str,
        n: int = 10,
        *,
        limit: int | None = 10_000,
        where: Any = None,
        index: str | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> ItemPage:
        """
        The n biggest items (by DynamoDB's sizing rules), biggest first, among the first `limit` items
        read (None = the whole table).
        """
        n = _as_int(n, "n")
        page = ItemPage(
            table=table,
            index=index,
            operation="largest",
            keys=self.key_attributes(table, index),
        )
        items = self.iter_items(
            table,
            where=where,
            index=index,
            limit=limit,
            progress=progress,
            stats=page.stats,
        )
        page.items = [
            item
            for _, _, item in heapq.nlargest(
                n, ((item_size(it), i, it) for i, it in enumerate(items))
            )
        ]
        return page

    def profile(
        self,
        table: str,
        n: int = 1000,
        *,
        where: Any = None,
        index: str | None = None,
        spread: bool = True,
        max_depth: int = 2,
        progress: Callable[[int], None] | None = None,
    ) -> TableProfile:
        """
        Profile about n items: attribute names, types, fill rate, distinct values, ranges, examples,
        key shapes and item sizes. spread=True samples across the whole table (see sample);
        spread=False reads from the start of it.
        """
        if spread:
            page = self.sample(table, n, where=where, index=index, progress=progress)
        else:
            page = self.scan(table, n, where=where, index=index, progress=progress)
        info = self.table(table)
        result = profile_items(page.items, table, keys=page.keys, max_depth=max_depth)
        result.index, result.stats = index, page.stats
        result.approx_item_count = (
            info.index(index).item_count if index else info.item_count
        )
        return result
