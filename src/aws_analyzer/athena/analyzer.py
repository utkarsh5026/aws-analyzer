"""
AthenaAnalyzer: talks to Athena (past queries, their saved results, workgroups), the Glue Data Catalog (databases,
tables, partitions, crawlers) and S3 (what a table's folder holds), and returns the dataclasses in models. Never
prints, and never runs a query: starting one writes its results to S3, so the reports show it as code to copy.
"""

from __future__ import annotations

import difflib
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _Hint
from .._kit.fmt import _as_count, _utcnow, parse_time

from .analysis import parse_crawler, parse_query, parse_results, parse_table, parse_workgroup, queries_reading
from .helpers import QUERY_ID_RE, parse_table_ref, split_s3
from .models import (
    Catalog, Crawler, Database, Location, Overview, QueryDetail, QueryHistory, QueryResults, Table, TableReport,
    Workgroup,
)
from .prices import ATHENA_PRICES


_IDS_PER_BATCH = 50

_RESULTS_PER_PAGE = 1_000

_CRAWLERS_PER_CALL = 100

_MOST_CHECKS = 200

_MARKER_KEYS = 10

_KEYS_PER_PAGE = 1_000

_FOLDER_MARKER = "_$folder$"

_NO_BUCKET = ("NoSuchBucket",)

_DENIED = ("AccessDenied", "AllAccessDisabled", "AccessDeniedException")

_NEWEST_PARTITIONS = 3


def _code(exc: ClientError | BotoCoreError) -> str:
    return _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__


def _is_data(item: dict[str, Any], prefix: str) -> bool:
    """
    Whether a listed key is a file rather than a folder marker ('folder/', 'folder_$folder$', a 0-byte key ending
    in '/').
    """
    key = item.get("Key", "")
    return key != prefix and not key.endswith("/") and not key.endswith(_FOLDER_MARKER)


class AthenaAnalyzer(_BaseAnalyzer):
    """
    Pure-logic Athena and Glue Data Catalog analysis: every method returns data; nothing is printed, run or changed.

    `client` is the athena client and `clients` other boto3 clients by service name ('athena', 'glue', 's3').
    `prices` overrides ATHENA_PRICES.
    """

    _LIST_PRICES = ATHENA_PRICES

    def __init__(  # noqa: PLR0913
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["athena"] = client
        self._lock = threading.Lock()
        self.max_workers = 8

    @property
    def client(self) -> Any:
        """
        The athena client, made on first use so a missing region shows up as a readable error.
        """
        return self._cached_client("athena", self._make_client)

    def _make_client(self) -> Any:
        try:
            return self.session.client("athena", config=self._config)
        except NoRegionError:
            raise ValueError(
                "No AWS region is set, and Athena is regional. Pass one: "
                "AthenaView(AthenaAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
            ) from None

    def _glue(self) -> Any:
        return self._cached_client(
            "glue", lambda: self.session.client("glue", region_name=self.region, config=self._config))

    def _s3(self) -> Any:
        return self._cached_client(
            "s3", lambda: self.session.client("s3", region_name=self.region, config=self._config))

    @staticmethod
    def _pages(call: Callable[..., dict[str, Any]], key: str, **params: Any) -> list[dict[str, Any]]:
        """
        Every item under `key` from every page of a list call, following NextToken.
        """
        items: list[dict[str, Any]] = []
        while True:
            answer = call(**params)
            items += answer.get(key, [])
            token = answer.get("NextToken")
            if not token:
                return items
            params["NextToken"] = token


    def workgroups(self) -> list[Workgroup]:
        """
        Every workgroup in the region with its settings (result location, scan limit, engine version). A workgroup
        whose settings couldn't be read has its error code in .error.
        """
        listed = self._pages(self.client.list_work_groups, "WorkGroups")
        return self._map(self._workgroup, listed)

    def _workgroup(self, listed: dict[str, Any]) -> Workgroup:
        try:
            item = self.client.get_work_group(WorkGroup=listed["Name"])["WorkGroup"]
        except ClientError as exc:
            group = parse_workgroup(listed)
            group.error = _error_code(exc)
            return group
        return parse_workgroup(item)

    def history(  # noqa: PLR0913
        self,
        since: Any = "7d",
        *,
        until: Any = None,
        workgroup: str | None = None,
        limit: Any = 2_000,
        progress: Callable[..., None] | None = None,
    ) -> QueryHistory:
        """
        The queries submitted from `since` to `until` in every workgroup (or the one named), newest first: at most
        `limit` per workgroup (history.truncated says when a workgroup had more). Athena keeps 45 days of history.
        """
        start = parse_time(since) or _utcnow() - timedelta(days=7)
        end = parse_time(until) or _utcnow()
        cap = _as_count(limit, "limit") or 10**9
        names = [workgroup] if workgroup else [g["Name"] for g in self._pages(self.client.list_work_groups,
                                                                               "WorkGroups")]
        history = QueryHistory(since=start, until=end, workgroups=names)
        for name in names:
            try:
                queries, truncated = self._workgroup_history(name, start, end, cap, progress, len(history.queries))
            except ClientError as exc:
                history.errors[name] = _error_code(exc)
                continue
            history.queries += queries
            history.truncated = history.truncated or truncated
        history.queries.sort(key=lambda q: q.submitted or start, reverse=True)
        return history

    def _workgroup_history(self, workgroup: str, start: datetime, end: datetime, cap: int,  # noqa: PLR0913
                           progress: Callable[..., None] | None, read_before: int) -> tuple[list[Any], bool]:
        """
        One workgroup's queries from start to end. ListQueryExecutions gives IDs newest first, so the listing stops
        at the first page that reaches back past `start`.
        """
        kept: list[Any] = []
        params: dict[str, Any] = {"WorkGroup": workgroup, "MaxResults": _IDS_PER_BATCH}
        while True:
            answer = self.client.list_query_executions(**params)
            ids = answer.get("QueryExecutionIds", [])
            queries = self._executions(ids)
            reached_start = any(q.submitted is not None and q.submitted < start for q in queries)
            kept += [q for q in queries if q.submitted is None or start <= q.submitted <= end]
            if progress is not None:
                progress(read_before + len(kept))
            if len(kept) >= cap:
                return kept[:cap], True
            if reached_start or not answer.get("NextToken"):
                return kept, False
            params["NextToken"] = answer["NextToken"]

    def _executions(self, ids: list[str]) -> list[Any]:
        if not ids:
            return []
        answer = self.client.batch_get_query_execution(QueryExecutionIds=ids)
        return [parse_query(item) for item in answer.get("QueryExecutions", [])]

    def query(self, query_id: str, *, rows: Any = 20) -> QueryDetail:
        """
        One past query: its SQL, how it ended, what it scanned, its runtime statistics, and the first `rows` rows of
        the results it saved in S3 (read back, never run again). Parts that couldn't be read are in detail.errors.
        """
        wanted = self._query_id(query_id)
        item = self.client.get_query_execution(QueryExecutionId=wanted)["QueryExecution"]
        detail = QueryDetail(parse_query(item))
        try:
            statistics = self.client.get_query_runtime_statistics(QueryExecutionId=wanted)
            counted = (statistics.get("QueryRuntimeStatistics") or {}).get("Rows") or {}
            detail.rows_in, detail.rows_out = counted.get("InputRows"), counted.get("OutputRows")
        except ClientError as exc:
            detail.errors["statistics"] = _error_code(exc)
        wanted_rows = _as_count(rows, "rows") or 0
        if detail.query.state == "SUCCEEDED" and wanted_rows:
            try:
                detail.results = self.results(wanted, limit=wanted_rows)
            except ClientError as exc:
                detail.errors["results"] = _error_code(exc)
        return detail

    def results(self, query_id: str, *, limit: Any = 1_000) -> QueryResults:
        """
        A past query's saved results, at most `limit` rows (results.to_df() for a DataFrame): read from where Athena
        wrote them, so a query whose result files were deleted can't be read.
        """
        wanted = self._query_id(query_id)
        cap = _as_count(limit, "limit") or 10**9
        pages: list[dict[str, Any]] = []
        params: dict[str, Any] = {"QueryExecutionId": wanted, "MaxResults": min(cap + 1, _RESULTS_PER_PAGE)}
        read = 0
        while True:
            answer = self.client.get_query_results(**params)
            pages.append(answer)
            read += len((answer.get("ResultSet") or {}).get("Rows", []))
            if not answer.get("NextToken") or read > cap:
                return parse_results(pages, cap)
            params["NextToken"] = answer["NextToken"]

    @staticmethod
    def _query_id(text: str) -> str:
        found = QUERY_ID_RE.search(text or "")
        if found is None:
            raise _Hint(f"{text!r} isn't a query ID: they look like 'a1b2c3d4-...' (queries() lists them).")
        return found.group(0)


    def databases(self) -> list[Database]:
        """
        Every Glue database in the region with its tables (their columns, partition keys, location and format). A
        database whose tables couldn't be listed has its error code in .error.
        """
        listed = self._pages(self._glue().get_databases, "DatabaseList")
        return self._map(self._database, listed)

    def _database(self, item: dict[str, Any]) -> Database:
        database = Database(name=item.get("Name", ""), description=item.get("Description", ""),
                            location_uri=item.get("LocationUri", ""), created=item.get("CreateTime"))
        try:
            tables = self._pages(self._glue().get_tables, "TableList", DatabaseName=database.name)
        except ClientError as exc:
            database.error = _error_code(exc)
            return database
        database.tables = sorted((parse_table(t) for t in tables), key=lambda t: t.name)
        return database

    def crawlers(self) -> list[Crawler]:
        """
        Every Glue crawler in the region: what it crawls, its schedule and how its last crawl went, with the tables
        it changed (GetCrawlerMetrics).
        """
        items = self._pages(self._glue().get_crawlers, "Crawlers")
        metrics: dict[str, dict[str, Any]] = {}
        names = [c["Name"] for c in items]
        for begin in range(0, len(names), _CRAWLERS_PER_CALL):
            answer = self._pages(self._glue().get_crawler_metrics, "CrawlerMetricsList",
                                 CrawlerNameList=names[begin: begin + _CRAWLERS_PER_CALL])
            metrics.update({m["CrawlerName"]: m for m in answer})
        crawlers = [parse_crawler(item, metrics.get(item["Name"])) for item in items]
        return sorted(crawlers, key=lambda c: c.name)

    def check_location(self, uri: str) -> Location:
        """
        Whether a table's S3 folder holds files: 'ok', 'empty', 'no bucket' or 'denied'. One small LIST request.
        """
        location = Location(uri)
        bucket, prefix = split_s3(uri)
        if not bucket:
            return location
        location.requests = 1
        try:
            answer = self._s3().list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=_MARKER_KEYS)
        except ClientError as exc:
            location.state = _location_error(_error_code(exc))
            return location
        files = [item for item in answer.get("Contents", []) if _is_data(item, prefix)]
        location.state = "ok" if files or answer.get("IsTruncated") else "empty"
        return location

    def list_location(self, uri: str, *, limit: Any = 10_000) -> Location:
        """
        What a table's S3 folder holds, listed up to `limit` keys: its files, their size and the newest one's time
        (location.complete is False when it stopped at the limit).
        """
        location = Location(uri)
        bucket, prefix = split_s3(uri)
        if not bucket:
            return location
        cap = _as_count(limit, "limit") or 10**9
        params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        seen = 0
        try:
            while True:
                params["MaxKeys"] = min(cap - seen, _KEYS_PER_PAGE)
                answer = self._s3().list_objects_v2(**params)
                location.requests += 1
                for item in answer.get("Contents", []):
                    seen += 1
                    _count_file(location, item, prefix)
                if not answer.get("IsTruncated") or seen >= cap:
                    location.complete = not answer.get("IsTruncated")
                    break
                params["ContinuationToken"] = answer["NextContinuationToken"]
        except ClientError as exc:
            location.state = _location_error(_error_code(exc))
            return location
        location.state = "ok" if location.files else "empty"
        return location

    def catalog(self, database: str | None = None, *, check_locations: bool = True,
                progress: Callable[..., None] | None = None) -> Catalog:
        """
        The Data Catalog (or one database of it) with its crawlers, each table's folder checked with one small S3
        request (at most 200 tables; catalog.unchecked counts the rest). Parts that couldn't be read are in errors.
        """
        catalog = Catalog(region=self.region)
        try:
            catalog.databases = self._named_databases(database)
        except (ClientError, BotoCoreError) as exc:
            catalog.errors["databases"] = _code(exc)
        try:
            crawlers = self.crawlers()
            catalog.crawlers = [c for c in crawlers if database is None or c.database == database.lower()]
        except (ClientError, BotoCoreError) as exc:
            catalog.errors["crawlers"] = _code(exc)
        if check_locations:
            self._check_tables(catalog, progress)
        return catalog

    def _named_databases(self, database: str | None) -> list[Database]:
        if database is None:
            return self.databases()
        try:
            item = self._glue().get_database(Name=database.lower())["Database"]
        except ClientError as exc:
            if _error_code(exc) != "EntityNotFoundException":
                raise
            names = [d["Name"] for d in self._pages(self._glue().get_databases, "DatabaseList")]
            close = difflib.get_close_matches(database.lower(), names, n=3, cutoff=0.5)
            hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
            raise _Hint(f"No database named {database!r} in {self.region}.{hint} catalog() lists them.") from None
        return [self._database(item)]

    def _check_tables(self, catalog: Catalog, progress: Callable[..., None] | None) -> None:
        tables = [t for t in catalog.tables if t.location_uri and not t.is_view]
        checking, catalog.unchecked = tables[:_MOST_CHECKS], max(0, len(tables) - _MOST_CHECKS)

        def check(table: Table) -> Location:
            if table.is_partitioned and not table.projection:
                self._any_partition(table)
            return self.check_location(table.location_uri)

        locations = self._map(check, checking)
        for table, location in zip(checking, locations):
            table.location = location
        catalog.checked = len(checking)
        catalog.s3_requests = sum(loc.requests for loc in locations)
        if progress is not None:
            progress(catalog.checked, catalog.checked)

    def _any_partition(self, table: Table) -> None:
        """
        Whether the catalog holds any partition of a table, with one small GetPartitions request: table.partitions
        is 0, or 1 with partitions_complete False (there may be more). A table that can't be read keeps None.
        """
        try:
            answer = self._glue().get_partitions(DatabaseName=table.database, TableName=table.name,
                                                 ExcludeColumnSchema=True, MaxResults=1)
        except ClientError:
            return
        found = len(answer.get("Partitions", []))
        table.partitions = found
        table.partitions_complete = not found

    def table(self, ref: str, *, partition_limit: Any = 10_000, file_limit: Any = 10_000) -> Table:
        """
        One table ('database.table'): its columns and partition keys, its partitions counted (up to
        `partition_limit`, with the newest few), and its S3 folder listed up to `file_limit` keys.
        """
        database, name = parse_table_ref(ref)
        try:
            item = self._glue().get_table(DatabaseName=database, Name=name)["Table"]
        except ClientError as exc:
            if _error_code(exc) != "EntityNotFoundException":
                raise
            raise self._unknown_table(database, name) from None
        table = parse_table(item)
        if table.is_partitioned:
            self._count_partitions(table, _as_count(partition_limit, "partition_limit") or 10**9)
        if table.location_uri:
            table.location = self.list_location(table.location_uri, limit=file_limit)
        return table

    def _unknown_table(self, database: str, name: str) -> _Hint:
        try:
            names = [t["Name"] for t in self._pages(self._glue().get_tables, "TableList", DatabaseName=database)]
        except ClientError:
            return _Hint(f"No database named {database!r} in {self.region}: catalog() lists them.")
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
        hint = f" Did you mean {', '.join(repr(f'{database}.{c}') for c in close)}?" if close else ""
        return _Hint(f"No table {name!r} in database {database!r}.{hint} catalog({database!r}) lists them.")

    def _count_partitions(self, table: Table, cap: int) -> None:
        params: dict[str, Any] = {"DatabaseName": table.database, "TableName": table.name,
                                  "ExcludeColumnSchema": True}
        values: list[list[str]] = []
        while True:
            answer = self._glue().get_partitions(**params)
            values += [p.get("Values", []) for p in answer.get("Partitions", [])]
            if len(values) >= cap:
                table.partitions_complete = False
                break
            if not answer.get("NextToken"):
                break
            params["NextToken"] = answer["NextToken"]
        table.partitions = min(len(values), cap)
        table.newest_partitions = sorted(values)[-_NEWEST_PARTITIONS:][::-1]

    def table_report(self, ref: str, *, since: Any = "7d", limit: Any = 2_000,
                     progress: Callable[..., None] | None = None) -> TableReport:
        """
        One table in detail (table()) and the queries since `since` that read it, from every workgroup's history.
        """
        report = TableReport(self.table(ref))
        report.s3_requests = report.table.location.requests if report.table.location else 0
        try:
            report.history = self.history(since, limit=limit, progress=progress)
        except (ClientError, BotoCoreError) as exc:
            report.errors["queries"] = _code(exc)
            return report
        report.queries = queries_reading(report.history.queries, report.table.full_name)
        return report

    def tables_named(self, names: set[str]) -> tuple[list[Table], dict[str, str]]:
        """
        The tables a query names ('database.table'), each read with table(); the ones that couldn't be read
        (name -> why).
        """
        found: list[Table] = []
        missing: dict[str, str] = {}
        for name in sorted(names):
            try:
                found.append(self.table(name, partition_limit=1_000))
            except _Hint as exc:
                missing[name] = str(exc)
            except ClientError as exc:
                missing[name] = _error_code(exc)
        return found, missing

    def overview(self, *, days: int = 7, progress: Callable[..., None] | None = None) -> Overview:
        """
        Athena in the region: the workgroups, the queries of the last `days` days, and the Data Catalog with each
        table's folder checked and the crawlers. Parts that couldn't be read are in errors.
        """
        overview = Overview(region=self.region, days=days)
        try:
            overview.workgroups = self.workgroups()
        except (ClientError, BotoCoreError) as exc:
            overview.errors["workgroups"] = _code(exc)
        try:
            overview.history = self.history(timedelta(days=days), progress=progress)
        except (ClientError, BotoCoreError) as exc:
            overview.errors["queries"] = _code(exc)
        overview.catalog = self.catalog()
        overview.errors.update(overview.catalog.errors)
        return overview


def _location_error(code: str) -> str:
    if code in _NO_BUCKET:
        return "no bucket"
    if code in _DENIED:
        return "denied"
    return f"error: {code}"


def _count_file(location: Location, item: dict[str, Any], prefix: str) -> None:
    if not _is_data(item, prefix):
        return
    location.files += 1
    location.size += int(item.get("Size", 0))
    modified = item.get("LastModified")
    if isinstance(modified, datetime):
        modified = modified if modified.tzinfo else modified.replace(tzinfo=timezone.utc)
        location.newest = modified if location.newest is None else max(location.newest, modified)
