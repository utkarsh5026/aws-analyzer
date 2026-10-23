"""
LambdaAnalyzer: talks to Lambda, CloudWatch and CloudWatch Logs in one region or all of them, and
returns the dataclasses in models. Never prints, and never invokes or changes a function.
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import hashlib
import io
import json
import math
import os
import re
import threading
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, NamedTuple

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _Hint, _error_code
from .._kit.fmt import _as_int, _utcnow, human_size, parse_size, parse_time

from .prices import LAMBDA_PRICES
from .helpers import MB, _midnight, parse_function_ref, parse_qualifier, runtime_family
from .models import (
    AccountLimits, Alias, AsyncConfig, CodePackage, CodeSearch, DailyUsage, ErrorReport, Function, FunctionDetail,
    FunctionMetrics, FunctionUrl, LogEvent, LogGroup, PackageDownload, PackageSources, Performance, ProvisionedConcurrency,
    SavedPage, SymbolReport, Trigger, Version, VersionDiff,
)
from .analysis.functions import (
    _unqualified, parse_event_source_mapping, parse_function, parse_policy, parse_version,
)
from .analysis.overview import Overview
from .analysis.errors import _filter_pattern, _phrase, classify_error, group_errors, request_id_of
from .analysis.logs import LogPage, LogRun, parse_report, split_runs
from .analysis.package import DOWNLOADS, _DOWNLOADS_MARK, package_file_name, page_file_name, read_package
from .analysis.source import find_symbol_in, read_sources, search_package
from .analysis.versions import diff_packages, nothing_before, version_before
from .analysis.codemap.model import CodeMap
from .analysis.codemap.mapping import map_package


# Error codes from a region the account hasn't turned on (opt-in regions): skipped, not reported as a failure.
_NOT_ENABLED = {"UnrecognizedClientException", "InvalidClientTokenId", "AuthFailure", "OptInRequired",
                "InvalidSignatureException"}

# CloudWatch Logs filter patterns. The service narrows the lines down; classify_error() and parse_report() then
# decide, so a pattern that lets a few extra lines through costs nothing but reading them.
_ERROR_PATTERN = (
    '?ERROR ?Error ?error ?Exception ?exception ?Traceback ?FATAL ?CRITICAL ?panic ?errorType ?"Task timed out" '
    '?timeout ?"Runtime exited" ?"signal: killed"'
)

_REPORT_PATTERN = '?"REPORT RequestId" ?"platform.report"'

_PACKAGES_KEPT = 2

_KEPT_PACKAGE_BYTES = 50 * MB

_SEARCH_HITS = 500  # log_runs(search=...) reads at most this many matching lines (the newest)...

_SEARCH_WINDOWS = 30  # ...and the whole runs around the newest this many of them (a read each)

_LOG_READERS = 4

_FIRST_SLICE_MS = 60_000

_SLICE_GROWTH = 4


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _from_ms(value: Any) -> datetime | None:
    return None if value in (None, "") else datetime.fromtimestamp(int(value) / 1000, timezone.utc)


def _log_slices(low: int, high: int, readers: int) -> list[tuple[int, int]]:
    """
    The (first, last) milliseconds a log read asks CloudWatch for, newest first: the newest minute, then slices
    _SLICE_GROWTH times longer each, so a read that fills its limit early has asked for little. With several readers
    no slice is longer than their even share of the range, so the slices read side by side finish about together.
    """
    longest = high - low + 1 if readers <= 1 else max(_FIRST_SLICE_MS, (high - low + 1) // readers)
    slices: list[tuple[int, int]] = []
    size = _FIRST_SLICE_MS
    while high >= low:
        first = max(low, high - size + 1)
        slices.append((first, high))
        high = first - 1
        size = min(size * _SLICE_GROWTH, longest)
    return slices


class _LogSlice(NamedTuple):
    """
    What one slice of a log read found: where it starts, its events oldest first, and whether CloudWatch had more
    than the limit let it read.
    """

    first: int
    events: list[LogEvent]
    has_more: bool


def _join_slices(
    parts: list[_LogSlice], low: int, limit: int | None
) -> tuple[list[LogEvent], bool, datetime | None]:
    """
    The newest-first slices of a log read as one list, oldest first, cut to the newest `limit` events: with whether
    it was cut (or older slices weren't read), and then the time of its oldest event.
    """
    events: list[LogEvent] = []
    truncated = False
    for part in parts:
        batch = part.events
        total = len(events) + len(batch)
        reached_limit = limit is not None and total >= limit
        if reached_limit and (part.has_more or part.first > low or total > limit):
            batch = batch[total - limit:]
            truncated = True
        events = batch + events
    return events, truncated, (events[0].time if truncated and events else None)


def _log_group(entry: dict[str, Any]) -> LogGroup:
    return LogGroup(
        entry.get("logGroupName", ""),
        entry.get("retentionInDays"),
        entry.get("storedBytes"),
        _from_ms(entry.get("creationTime")),
        entry.get("logGroupClass"),
    )


def _pages(client: Any, operation: str, key: str, **params: Any) -> list[dict[str, Any]]:
    """
    Every item under `key` across the pages of a List / Describe call (Lambda's Marker, or nextToken).
    """
    call = getattr(client, operation)
    items: list[dict[str, Any]] = []
    token: dict[str, str] = {}
    for _ in range(100_000):
        resp = call(**params, **token)
        items += resp.get(key) or []
        if resp.get("NextMarker"):
            token = {"Marker": resp["NextMarker"]}
        elif resp.get("nextToken"):
            token = {"nextToken": resp["nextToken"]}
        elif resp.get("NextToken"):
            token = {"NextToken": resp["NextToken"]}
        else:
            break
    return items


def _qualifier(arn: str | None) -> str:
    """
    'arn:aws:lambda:r:a:function:etl:live' -> 'live'.
    """
    parts = (arn or "").split(":")
    return parts[7] if len(parts) > 7 else "$LATEST"


class LambdaAnalyzer(_BaseAnalyzer):
    """
    Pure-logic Lambda analysis: every method returns data; nothing is printed, invoked or changed.

    Functions are read in the analyzer's region, or in several: overview(regions=['us-east-1', 'eu-west-1']) or
    regions='all'. Methods about one function take its name, 'name:alias', an ARN or a console link, and region=
    when it's in another region (an ARN or a link says so itself, and a name overview() found in one other region
    is looked for there). `prices` overrides LAMBDA_PRICES. `client` is the Lambda client and `clients` other boto3
    clients by service name ('lambda', 'cloudwatch', 'logs', 'ec2'): a client, used in every region, or a function
    that makes one for a region (e.g. for tests). `downloads` is the folder download() saves packages in without a
    path= (DOWNLOADS, 'lambda-downloads' next to the notebook).
    """

    _LIST_PRICES = LAMBDA_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        downloads: str | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self.downloads = downloads or DOWNLOADS  # where download() saves without a path (see downloads_folder())
        self._given: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._given["lambda"] = client
        self._clients: dict[tuple[str, str | None], Any] = {}
        self._lock = threading.Lock()  # boto3 sessions aren't thread-safe, so clients are made one at a time
        self.max_workers = 8  # functions and regions read in parallel
        self._seen: dict[str, set[str]] = defaultdict(set)  # function name -> regions overview() found it in
        self._packages: dict[tuple[str, str], bytes] = {}

    # ------------------------------------------------------------------ clients

    def _service(self, name: str, region: str | None = None) -> Any:
        """
        A boto3 client for `region` (default: the analyzer's), made on first use, or the one passed in.
        """
        if region is not None and region == self.session.region_name:
            region = None
        with self._lock:
            if (name, region) not in self._clients:
                given = self._given.get(name)
                if given is not None:
                    maker = callable(given) and not hasattr(given, "meta")  # a function that makes clients
                    self._clients[(name, region)] = given(region or self.session.region_name) if maker else given
                else:
                    makers = {  # named one by one so the project's read-only check can see which services are used
                        "lambda": lambda: self.session.client("lambda", region_name=region, config=self._config),
                        "cloudwatch": lambda: self.session.client(
                            "cloudwatch", region_name=region, config=self._config
                        ),
                        "logs": lambda: self.session.client("logs", region_name=region, config=self._config),
                        "ec2": lambda: self.session.client("ec2", region_name=region, config=self._config),
                    }
                    try:
                        self._clients[(name, region)] = makers[name]()
                    except NoRegionError:
                        raise ValueError(
                            "No AWS region is set, and Lambda functions are regional. Pass one: "
                            "LambdaView(LambdaAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                        ) from None
            return self._clients[(name, region)]

    @property
    def client(self) -> Any:
        """
        The Lambda client in the analyzer's region.
        """
        return self._service("lambda")

    def regions(self, which: str | Iterable[str] | None = None) -> list[str]:
        """
        The regions a report covers: None -> the analyzer's; 'all' -> every region turned on for the account
        (EC2 DescribeRegions; without that permission, every region Lambda is offered in); a name, 'us-east-1,
        eu-west-1' or a list -> those.
        """
        if which is None:
            return [self.region]
        if isinstance(which, str) and which.strip().lower() == "all":
            try:
                found = self._service("ec2").describe_regions().get("Regions") or []
                names = [r["RegionName"] for r in found if r.get("OptInStatus") != "not-opted-in"]
                if names:
                    return sorted(names)
            except (ClientError, BotoCoreError):
                pass
            return sorted(self.session.get_available_regions("lambda"))
        names = re.split(r"[,\s]+", which.strip()) if isinstance(which, str) else [str(r).strip() for r in which]
        names = [n for n in dict.fromkeys(names) if n]
        for name in names:
            if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d+", name):
                close = difflib.get_close_matches(name, self.session.get_available_regions("lambda"), n=2)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                raise ValueError(f"{name!r} isn't a region name, like 'us-east-1'.{hint} regions='all' covers every one")
        if not names:
            raise ValueError("regions= is empty: pass 'all', a region like 'us-east-1', or a list of them")
        return names

    def locate(self, ref: str, region: str | None = None) -> tuple[str, str | None, str]:
        """
        (name, qualifier, region) for a function: its name, 'name:alias', an ARN or a console link. The region is
        region= when given, else the one in the ARN or link, else the one other region overview() found the name
        in, else the analyzer's.
        """
        name, qualifier, found = parse_function_ref(ref)
        if region:
            return name, qualifier, region
        if found:
            return name, qualifier, found
        seen = self._seen.get(name, set())
        if len(seen) == 1 and self.region not in seen:
            return name, qualifier, next(iter(seen))
        return name, qualifier, self.region

    # ------------------------------------------------------------------ functions

    def list_functions(self, region: str | None = None, *, match: str | None = None) -> list[Function]:
        """
        Every function in the region (its $LATEST configuration), or those whose name matches the glob.
        """
        where = region or self.region
        found = [parse_function(c, where) for c in _pages(self._service("lambda", region), "list_functions",
                                                          "Functions")]
        return [f for f in found if match is None or fnmatch.fnmatchcase(f.name, match)]

    def function(self, ref: str, *, region: str | None = None) -> Function:
        """
        One function's configuration, tags, reserved concurrency and the link to its code (GetFunction).
        """
        name, qualifier, where = self.locate(ref, region)
        resp = self._service("lambda", where).get_function(
            FunctionName=name, **({"Qualifier": qualifier} if qualifier else {})
        )
        fn = parse_function(resp.get("Configuration") or {}, where)
        fn.tags = dict(resp.get("Tags") or {})
        fn.reserved_concurrency = (resp.get("Concurrency") or {}).get("ReservedConcurrentExecutions")
        code = resp.get("Code") or {}
        fn.code_location = code.get("Location")
        fn.image_uri = code.get("ImageUri") or code.get("ResolvedImageUri")
        return fn

    def versions(self, ref: str, region: str | None = None) -> list[Version]:
        """
        A function's published versions, oldest first (ListVersionsByFunction): each one's number, code size and
        SHA-256, when it was published and its description. $LATEST, the code that runs when no version is named,
        isn't one of them.
        """
        name, _, where = self.locate(ref, region)
        found = _pages(self._service("lambda", where), "list_versions_by_function", "Versions", FunctionName=name)
        return [parse_version(v) for v in found if v.get("Version") != "$LATEST"]

    def event_sources(self, region: str | None = None, *, function: str | None = None) -> dict[str, list[Trigger]]:
        """
        Event source mappings (the queues and streams Lambda polls for functions), by function ARN.
        """
        params = {"FunctionName": function} if function else {}
        found: dict[str, list[Trigger]] = defaultdict(list)
        for mapping in _pages(self._service("lambda", region), "list_event_source_mappings", "EventSourceMappings",
                              **params):
            found[_unqualified(mapping.get("FunctionArn") or "")].append(parse_event_source_mapping(mapping))
        return dict(found)

    def policy(self, name: str, region: str | None = None, *, qualifier: str | None = None) -> dict[str, Any] | None:
        """
        The function's resource-based policy document (who may invoke it), or None when it has none.
        """
        try:
            resp = self._service("lambda", region).get_policy(
                FunctionName=name, **({"Qualifier": qualifier} if qualifier else {})
            )
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return None
            raise
        return json.loads(resp.get("Policy") or "{}")

    def provisioned(self, name: str, region: str | None = None) -> list[ProvisionedConcurrency]:
        """
        Provisioned concurrency on the function's aliases and versions: copies kept ready, billed used or not.
        """
        configs = _pages(self._service("lambda", region), "list_provisioned_concurrency_configs",
                         "ProvisionedConcurrencyConfigs", FunctionName=name)
        return [
            ProvisionedConcurrency(
                _qualifier(c.get("FunctionArn")),
                int(c.get("RequestedProvisionedConcurrentExecutions") or 0),
                int(c.get("AllocatedProvisionedConcurrentExecutions") or 0),
                int(c.get("AvailableProvisionedConcurrentExecutions") or 0),
                c.get("Status") or "",
                c.get("StatusReason"),
            )
            for c in configs
        ]

    def account(self, region: str | None = None) -> AccountLimits:
        """
        The region's limits and usage: concurrent runs allowed and unreserved, code storage used and allowed.
        """
        resp = self._service("lambda", region).get_account_settings()
        limit, usage = resp.get("AccountLimit") or {}, resp.get("AccountUsage") or {}
        return AccountLimits(
            region or self.region,
            limit.get("ConcurrentExecutions"),
            limit.get("UnreservedConcurrentExecutions"),
            usage.get("TotalCodeSize"),
            limit.get("TotalCodeSize"),
            usage.get("FunctionCount"),
        )

    def log_groups(self, region: str | None = None, *, prefix: str = "/aws/lambda/") -> dict[str, LogGroup]:
        """
        Log groups whose names start with prefix (by default the one each function gets), by name.
        """
        entries = _pages(self._service("logs", region), "describe_log_groups", "logGroups", logGroupNamePrefix=prefix)
        return {entry["logGroupName"]: _log_group(entry) for entry in entries if entry.get("logGroupName")}

    def log_group(self, name: str, region: str | None = None) -> LogGroup | None:
        """
        One log group: how long it keeps logs and how much it holds; None when it doesn't exist yet.
        """
        return self.log_groups(region, prefix=name).get(name)

    # ------------------------------------------------------------------ CloudWatch

    def metrics(
        self,
        functions: list[Function],
        *,
        since: datetime,
        until: datetime | None = None,
        period: int | None = None,
        logs: bool = True,
        concurrency: bool = False,
    ) -> tuple[dict[str, FunctionMetrics], int]:
        """
        What CloudWatch counted for each function from `since` to `until`, by function ARN: calls, errors,
        throttles, total and longest run time, with logs=True the bytes it logged, and with concurrency=True the
        most running at once. One row per day (per hour for windows of two days or less). Also returns how many
        metrics were read (GetMetricData bills $0.01 per 1,000). Functions may be in different regions.
        """
        until = until or _utcnow()
        period = period or (3600 if until - since <= timedelta(days=2) else 86400)
        specs = [
            ("invocations", "Invocations", "Sum"),
            ("errors", "Errors", "Sum"),
            ("throttles", "Throttles", "Sum"),
            ("duration_sum", "Duration", "Sum"),
            ("duration_max", "Duration", "Maximum"),
        ]
        if concurrency:
            specs.append(("concurrency", "ConcurrentExecutions", "Maximum"))
        found: dict[str, FunctionMetrics] = {}
        read = 0
        by_region: dict[str, list[Function]] = defaultdict(list)
        for fn in functions:
            by_region[fn.region or self.region].append(fn)
        for region, group in by_region.items():
            queries: list[dict[str, Any]] = []
            for i, fn in enumerate(group):
                dimensions = [{"Name": "FunctionName", "Value": fn.name}]
                for j, (_, metric, stat) in enumerate(specs):
                    queries.append({"Id": f"m{i}_{j}", "MetricStat": {
                        "Metric": {"Namespace": "AWS/Lambda", "MetricName": metric, "Dimensions": dimensions},
                        "Period": period, "Stat": stat}})
                if logs:
                    queries.append({"Id": f"m{i}_logs", "MetricStat": {
                        "Metric": {"Namespace": "AWS/Logs", "MetricName": "IncomingBytes",
                                   "Dimensions": [{"Name": "LogGroupName", "Value": fn.log_group}]},
                        "Period": period, "Stat": "Sum"}})
            series = self._metric_data(region, queries, since, until)
            read += len(queries)
            for i, fn in enumerate(group):
                rows: dict[datetime, DailyUsage] = {}
                for j, (attribute, _, _) in enumerate(specs):
                    for moment, value in series.get(f"m{i}_{j}", {}).items():
                        row = rows.setdefault(moment, DailyUsage(moment))
                        if attribute in ("duration_max", "concurrency"):
                            setattr(row, attribute, max(getattr(row, attribute) or 0.0, value))
                        else:
                            setattr(row, attribute, getattr(row, attribute) + value)
                daily = sorted(rows.values(), key=lambda r: r.start)
                m = FunctionMetrics(fn.name, (until - since).total_seconds() / 86400, period, daily=daily)
                m.invocations = sum(r.invocations for r in daily)
                m.errors = sum(r.errors for r in daily)
                m.throttles = sum(r.throttles for r in daily)
                m.duration_sum = sum(r.duration_sum for r in daily)
                m.duration_max = max((r.duration_max for r in daily if r.duration_max is not None), default=None)
                m.concurrency_max = max((r.concurrency for r in daily if r.concurrency is not None), default=None)
                if logs:
                    m.log_bytes = sum(series.get(f"m{i}_logs", {}).values())
                found[fn.arn] = m
        return found, read

    def _metric_data(
        self, region: str | None, queries: list[dict[str, Any]], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, float]]:
        """
        GetMetricData for any number of queries (500 a call): query ID -> {period start: value}.
        """
        series: dict[str, dict[datetime, float]] = defaultdict(dict)
        client = self._service("cloudwatch", region)
        for start in range(0, len(queries), 500):
            params: dict[str, Any] = {"MetricDataQueries": queries[start : start + 500], "StartTime": since,
                                      "EndTime": until}
            while True:
                resp = client.get_metric_data(**params)
                for result in resp.get("MetricDataResults") or []:
                    for moment, value in zip(result.get("Timestamps") or [], result.get("Values") or []):
                        series[result["Id"]][moment.astimezone(timezone.utc)] = float(value)
                if not resp.get("NextToken"):
                    break
                params["NextToken"] = resp["NextToken"]
        return series

    def peak_concurrency(self, region: str | None, *, since: datetime, until: datetime | None = None) -> float | None:
        """
        The most runs in progress at once across the region (every function together) in the window.
        """
        until = until or _utcnow()
        query = {"Id": "peak", "MetricStat": {
            "Metric": {"Namespace": "AWS/Lambda", "MetricName": "ConcurrentExecutions"},
            "Period": 3600 if until - since <= timedelta(days=2) else 86400, "Stat": "Maximum"}}
        values = self._metric_data(region, [query], since, until).get("peak", {})
        return max(values.values()) if values else None

    # ------------------------------------------------------------------ overview

    def overview(
        self,
        match: str | None = None,
        *,
        regions: str | Iterable[str] | None = None,
        days: int = 30,
        metrics: bool = True,
        details: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> Overview:
        """
        Every function in the region (or regions; 'all' for every region turned on for the account) with what
        invokes it, CloudWatch's numbers for the last `days` days, each region's limits and its functions' log
        groups. details=False skips the reads made per function (resource policy, provisioned concurrency);
        metrics=False skips CloudWatch. match='etl-*' keeps matching names. What can't be read is in .errors, as
        'region:section'.
        """
        days = _as_int(days, "days")
        if not 1 <= days <= 455:
            raise ValueError(f"days= must be between 1 and 455 (what CloudWatch keeps of hourly numbers); got {days}")
        names = self.regions(regions)
        until = _utcnow()
        since = _midnight(until) - timedelta(days=days - 1)  # whole UTC days, today included
        ov = Overview(names, days, prices=self.prices, details=details)
        found: dict[str, list[Function]] = {}

        def scan(region: str) -> tuple[str, dict[str, Any]]:
            out: dict[str, Any] = {"functions": [], "errors": {}}
            try:
                out["functions"] = self.list_functions(region, match=match)
            except ClientError as exc:
                code = _error_code(exc)
                if code in _NOT_ENABLED and len(names) > 1:  # an opt-in region the account hasn't turned on
                    out["skipped"] = code
                else:
                    out["errors"]["list"] = code
                return region, out
            except BotoCoreError as exc:
                out["errors"] = {"list": type(exc).__name__}
                return region, out
            if not out["functions"]:
                return region, out
            for section, call in (
                ("triggers", lambda: self.event_sources(region)),
                ("account", lambda: self.account(region)),
                ("logs", lambda: self.log_groups(region)),
            ):
                try:
                    out[section] = call()
                except ClientError as exc:
                    out["errors"][section] = _error_code(exc)
                except BotoCoreError as exc:
                    out["errors"][section] = type(exc).__name__
            return region, out

        for done, (region, out) in enumerate(self._map(scan, names), 1):
            if progress and len(names) > 1:
                progress(done, len(names))
            if "skipped" in out:
                ov.skipped[region] = out["skipped"]
                continue
            ov.errors.update({f"{region}:{section}": code for section, code in out["errors"].items()})
            found[region] = out["functions"]
            ov.functions += out["functions"]
            for fn in out["functions"]:
                self._seen[fn.name].add(region)
                ov.triggers[fn.arn] = list(out.get("triggers", {}).get(fn.arn, []))
                group = out.get("logs", {}).get(fn.log_group)
                if group is not None:
                    ov.log_groups[fn.arn] = group
            if out.get("account") is not None:
                ov.accounts[region] = out["account"]
        if details and ov.functions:
            ov.add_extras(self._all_extras(ov.functions, progress=progress))
        if metrics:
            ov.add_numbers(*self.overview_numbers(ov.functions, since=since, until=until, limits=set(ov.accounts)))
        return ov

    def _extras(self, fn: Function) -> tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]:
        """
        The reads overview() makes per function: the callers its resource policy allows, and its provisioned
        concurrency, with section -> error code for what couldn't be read.
        """
        triggers: list[Trigger] = []
        provisioned: list[ProvisionedConcurrency] = []
        errors: dict[str, str] = {}
        try:
            triggers = parse_policy(self.policy(fn.name, fn.region))
        except ClientError as exc:
            errors["policy"] = _error_code(exc)
        except BotoCoreError as exc:
            errors["policy"] = type(exc).__name__
        try:
            provisioned = self.provisioned(fn.name, fn.region)
        except ClientError as exc:
            errors["provisioned"] = _error_code(exc)
        except BotoCoreError as exc:
            errors["provisioned"] = type(exc).__name__
        return triggers, provisioned, errors

    def _all_extras(
        self, functions: list[Function], *, progress: Callable[..., None] | None = None
    ) -> dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]:
        """
        _extras() for each function, in parallel, by function ARN (Overview.add_extras adds them).
        """
        for region in dict.fromkeys(fn.region for fn in functions):  # each region's client before the threads use it
            self._service("lambda", region or None)
        found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]] = {}
        for start in range(0, len(functions), 50):  # in batches, so progress moves as they finish
            batch = functions[start : start + 50]
            found.update({fn.arn: extras for fn, extras in zip(batch, self._map(self._extras, batch))})
            if progress:
                progress(min(start + 50, len(functions)), len(functions))
        return found

    def overview_numbers(
        self, functions: list[Function], *, since: datetime, until: datetime, limits: set[str] | None = None
    ) -> tuple[dict[str, FunctionMetrics], int, dict[str, float | None], dict[str, str]]:
        """
        CloudWatch's numbers for overview() (Overview.add_numbers adds them): each function's from `since` to `until`
        by ARN, concurrency included (what provisioned concurrency is weighed against); how many metrics that read;
        the most runs at once in each region of `limits`; and 'region:metrics' -> the error code of a region that
        couldn't be read.
        """
        found: dict[str, FunctionMetrics] = {}
        read = 0
        peaks: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        by_region: dict[str, list[Function]] = defaultdict(list)
        for fn in functions:
            by_region[fn.region or self.region].append(fn)
        for region, group in by_region.items():
            try:
                numbers, count = self.metrics(group, since=since, until=until, concurrency=True)
                found.update(numbers)
                read += count
                if region in (limits or ()):
                    peaks[region] = self.peak_concurrency(region, since=since, until=until)
                    read += 1
            except ClientError as exc:
                errors[f"{region}:metrics"] = _error_code(exc)
            except BotoCoreError as exc:
                errors[f"{region}:metrics"] = type(exc).__name__
        return found, read, peaks, errors

    # ------------------------------------------------------------------ one function

    def describe(
        self,
        ref: str,
        *,
        region: str | None = None,
        days: int = 30,
        metrics: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> FunctionDetail:
        """
        Everything about one function: its configuration (GetFunction), what triggers it (event source mappings
        and its resource policy), its function URL, what happens to failed asynchronous events, versions, aliases,
        provisioned concurrency, runtime updates, its log group, and CloudWatch's numbers for the last `days` days.
        Sections that can't be read are named in .errors.
        """
        days = _as_int(days, "days")
        if not 1 <= days <= 455:
            raise ValueError(f"days= must be between 1 and 455; got {days}")
        name, qualifier, where = self.locate(ref, region)
        fn = self.function(ref, region=where)  # a missing function raises ResourceNotFoundException here
        detail = FunctionDetail(fn)
        client = self._service("lambda", where)
        qualified = {"Qualifier": qualifier} if qualifier else {}
        steps = 9 + (fn.package_type == "Zip") + bool(metrics)  # GetFunction, then one read per section
        done = [1]

        def read(section: str, call: Callable[[], Any], missing: Any = None) -> Any:
            try:
                return call()
            except ClientError as exc:
                if _error_code(exc) == "ResourceNotFoundException":
                    return missing
                detail.errors[section] = _error_code(exc)
            except BotoCoreError as exc:
                detail.errors[section] = type(exc).__name__
            finally:
                done[0] += 1
                if progress:
                    progress(done[0], steps)
            return None

        detail.policy = read("policy", lambda: self.policy(name, where, qualifier=qualifier))
        policy_triggers = parse_policy(detail.policy)
        sources = read("triggers", lambda: self.event_sources(where, function=name)) or {}
        detail.triggers = [t for found in sources.values() for t in found]
        url = read("url", lambda: client.get_function_url_config(FunctionName=name, **qualified))
        if url:
            detail.url = FunctionUrl(
                url.get("FunctionUrl", ""),
                url.get("AuthType", ""),
                list((url.get("Cors") or {}).get("AllowOrigins") or []),
                url.get("InvokeMode"),
            )
        if "url" not in detail.errors:  # keep the policy's URL statements only when there is a URL to call
            public = any(t.public for t in policy_triggers if t.kind == "Function URL")
            policy_triggers = [t for t in policy_triggers if t.kind != "Function URL"]
            if detail.url is not None:
                if detail.url.auth_type != "NONE":
                    how = "callers sign their requests with IAM (auth type AWS_IAM)"
                elif public:
                    how = "no sign-in (auth type NONE)"
                elif "policy" in detail.errors:
                    how = "auth type NONE (couldn't read the resource policy to see who may call it)"
                else:
                    how = "auth type NONE, but no policy statement lets the public call it, so calls are refused"
                policy_triggers.append(Trigger("Function URL", detail.url.url, via="function URL",
                                               public=detail.url.auth_type == "NONE" and public, detail=how))
        detail.triggers += policy_triggers
        config = read("async", lambda: client.get_function_event_invoke_config(FunctionName=name, **qualified),
                      missing={})
        if config is not None:
            destinations = config.get("DestinationConfig") or {}
            detail.async_config = AsyncConfig(
                int(config.get("MaximumRetryAttempts", 2)),
                int(config.get("MaximumEventAgeInSeconds", 6 * 3600)),
                (destinations.get("OnSuccess") or {}).get("Destination"),
                (destinations.get("OnFailure") or {}).get("Destination"),
            )
        detail.versions = read("versions", lambda: self.versions(name, where)) or []
        aliases = read("aliases", lambda: _pages(client, "list_aliases", "Aliases", FunctionName=name))
        detail.aliases = [
            Alias(a.get("Name", ""), a.get("FunctionVersion", ""),
                  dict((a.get("RoutingConfig") or {}).get("AdditionalVersionWeights") or {}), a.get("Description") or "")
            for a in aliases or []
        ]
        detail.provisioned = read("provisioned", lambda: self.provisioned(name, where)) or []
        if fn.package_type == "Zip":
            runtime = read("runtime_updates", lambda: client.get_runtime_management_config(FunctionName=name,
                                                                                         **qualified))
            if runtime:
                detail.runtime_updates = runtime.get("UpdateRuntimeOn")
                detail.runtime_version = runtime.get("RuntimeVersionArn")
        detail.log_group = read("log_group", lambda: self.log_group(fn.log_group, where))
        if metrics:
            until = _utcnow()
            since = _midnight(until) - timedelta(days=days - 1)
            numbers = read("metrics", lambda: self.metrics([fn], since=since, until=until, concurrency=True))
            detail.metrics = numbers[0].get(fn.arn) if numbers else None
        return detail

    # ------------------------------------------------------------------ logs

    @staticmethod
    def time_range(since: Any, until: Any = None) -> tuple[datetime, datetime]:
        """
        (start, end) from since= / until=: '1h', '24h', '7d', '2026-10-01' or a datetime.
        """
        now = _utcnow()
        try:  # '24h' and '7d' are measured back from now, for since= and until= alike
            end = parse_time(until, now=now) if until is not None else now
            start = parse_time(since, now=now)
        except ValueError:
            raise ValueError(
                f"since= and until= take a time like '1h', '24h', '7d', '2026-10-01' or a datetime; got "
                f"{since!r}" + (f" and {until!r}" if until is not None else "")
            ) from None
        if start is None or end is None or start >= end:
            raise ValueError(f"since= ({since!r}) must be before until=; try since='24h'")
        return start, end

    def _filter(
        self,
        group: str,
        region: str | None,
        start: datetime,
        end: datetime,
        pattern: str | None = None,
        limit: int | None = 10_000,
        *,
        stream: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> tuple[list[LogEvent], bool, datetime | None]:
        """
        The newest `limit` events from start to end that match the filter pattern, oldest first; whether reading
        stopped at the limit, and then how far back it reached. Reads backwards in growing slices (a minute, then
        4, 16, ...), so the newest events are the ones kept. A log group's slices are read _LOG_READERS at a time,
        newest first, so a search over days takes a fraction of the time one slice after another would; a stream's
        window (one run at a time) is small and read in order.
        """
        client = self._service("logs", region)
        params: dict[str, Any] = {"logGroupName": group}
        if pattern:
            params["filterPattern"] = pattern
        if stream:
            params["logStreamNames"] = [stream]
        low, high = _to_ms(start), _to_ms(end)

        def read(window: tuple[int, int], budget: int | None, stop: threading.Event | None = None) -> _LogSlice:
            return self._read_slice(client, {**params, "startTime": window[0], "endTime": window[1]}, start,
                                    budget, stop)

        readers = 1 if stream or self.max_workers <= 1 else _LOG_READERS
        slices = _log_slices(low, high, readers)
        if readers > 1 and len(slices) > 1:
            parts = self._read_side_by_side(read, slices, limit, readers, progress)
        else:
            parts = self._read_in_order(read, slices, limit, progress)
        return _join_slices(parts, low, limit)

    @staticmethod
    def _read_slice(
        client: Any, params: dict[str, Any], start: datetime, budget: int | None, stop: threading.Event | None
    ) -> _LogSlice:
        """
        One slice of a log read, page by page, until CloudWatch has no more, `budget` events are in, or `stop` is
        set (a newer slice already holds the limit).
        """
        events: list[LogEvent] = []
        token = None
        while stop is None or not stop.is_set():
            resp = client.filter_log_events(**params)  # read-only: FilterLogEvents only reads log events
            for e in resp.get("events") or []:
                message = e.get("message") or ""
                moment = _from_ms(e.get("timestamp")) or start
                events.append(LogEvent(moment, message, e.get("logStreamName") or "", request_id_of(message)))
            token = resp.get("nextToken")
            if not token or (budget is not None and len(events) >= budget):
                break
            params["nextToken"] = token
        events.sort(key=lambda e: e.time)
        return _LogSlice(params["startTime"], events, bool(token))

    @staticmethod
    def _read_in_order(
        read: Callable[..., _LogSlice],
        slices: list[tuple[int, int]],
        limit: int | None,
        progress: Callable[..., None] | None,
    ) -> list[_LogSlice]:
        """
        The slices read one after another, newest first, each asking only for what the limit still leaves.
        """
        parts: list[_LogSlice] = []
        count = 0
        for window in slices:
            if limit is not None and count >= limit:
                break
            part = read(window, None if limit is None else limit - count)
            parts.append(part)
            count += len(part.events)
            if progress:
                progress(count)
        return parts

    @staticmethod
    def _read_side_by_side(
        read: Callable[..., _LogSlice],
        slices: list[tuple[int, int]],
        limit: int | None,
        readers: int,
        progress: Callable[..., None] | None,
    ) -> list[_LogSlice]:
        """
        The slices read `readers` at a time, newest first, taken in order until they hold the limit; the older ones
        still waiting are dropped, and the ones being read stop after their page. Progress is reported from the
        calling thread.
        """
        parts: list[_LogSlice] = []
        count = 0
        stop = threading.Event()
        with ThreadPoolExecutor(max_workers=readers, thread_name_prefix="lambda-logs") as pool:
            futures = [pool.submit(read, window, limit, stop) for window in slices]
            try:
                for future in futures:
                    part = future.result()
                    parts.append(part)
                    count += len(part.events)
                    if progress:
                        progress(count)
                    if limit is not None and count >= limit:
                        break
            finally:
                stop.set()
                for future in futures:
                    future.cancel()
        return parts

    def _run_of(self, fn: Function, event: LogEvent) -> str | None:
        """
        The run a log line without a request ID belongs to: the next REPORT line in its stream (a stream runs one
        call at a time, and every run ends with one). None when there's none within the timeout.
        """
        if not event.stream:
            return None
        start = _to_ms(event.time)
        try:
            resp = self._service("logs", fn.region).filter_log_events(  # read-only: FilterLogEvents only reads
                logGroupName=fn.log_group, logStreamNames=[event.stream], startTime=start,
                endTime=start + (fn.timeout + 5) * 1000, filterPattern='"REPORT RequestId"', limit=1)
        except (ClientError, BotoCoreError):
            return None
        found = [request_id_of(e.get("message") or "") for e in resp.get("events") or []]
        return next((rid for rid in found if rid), None)

    def _latest(self, group: str, region: str | None) -> datetime | None:
        """
        When the log group last got an event (its busiest stream's last one), or None.
        """
        try:
            streams = self._service("logs", region).describe_log_streams(
                logGroupName=group, orderBy="LastEventTime", descending=True, limit=1
            ).get("logStreams") or []
        except (ClientError, BotoCoreError):
            return None
        return _from_ms(streams[0].get("lastEventTimestamp")) if streams else None

    def log_events(
        self,
        ref: str,
        *,
        since: Any = "1h",
        until: Any = None,
        pattern: str | None = None,
        request_id: str | None = None,
        limit: int | None = 10_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """
        What a function logged from `since` to `until` (the newest `limit` lines when there are more), matching a
        CloudWatch Logs filter pattern ('"KeyError"', '?ERROR ?WARN'). request_id= gives every line of that one
        run, the lines it printed without the ID included.
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._log_page(fn, start, end, pattern=pattern, request_id=request_id, limit=limit, progress=progress)

    def _log_page(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        pattern: str | None = None,
        request_id: str | None = None,
        limit: int | None = 10_000,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """
        log_events() for a function already read (no GetFunction).
        """
        page = LogPage(fn, fn.log_group, start, end, pattern=pattern)
        try:
            if request_id:
                page.pattern = f'"{request_id}"'
                hits, _, _ = self._filter(fn.log_group, fn.region, start, end, page.pattern, 1_000)
                if hits:  # one run's lines are everything its stream logged between its first and last line
                    stream = hits[-1].stream
                    first = min(e.time for e in hits if e.stream == stream)
                    last = max(e.time for e in hits if e.stream == stream)
                    page.events, page.truncated, page.covered_from = self._filter(
                        fn.log_group, fn.region, first, last, None, limit, stream=stream, progress=progress)
            else:
                page.events, page.truncated, page.covered_from = self._filter(
                    fn.log_group, fn.region, start, end, pattern, limit, progress=progress)
        except ClientError as exc:
            page.errors["logs"] = _error_code(exc)
            return page
        except BotoCoreError as exc:
            page.errors["logs"] = type(exc).__name__
            return page
        if not page.events:
            page.latest = self._latest(fn.log_group, fn.region)
        return page

    def log_runs(
        self,
        ref: str,
        *,
        since: Any = "1h",
        until: Any = None,
        search: str | None = None,
        limit: int | None = 3_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """
        What a function logged from `since` to `until`, as runs: .runs is a LogRun per call, newest first, each with
        every line it logged, its status (ok, failed, timeout...), run time, memory and cold start. Reads the newest
        `limit` lines. search= ('KeyError', an order ID, a request ID, or a CloudWatch Logs filter pattern) keeps the
        runs with a matching line, each with all of its lines (the matching ones are marked).
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._log_runs(fn, start, end, search=search, limit=limit, progress=progress)

    def _log_runs(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        search: str | None = None,
        limit: int | None = 3_000,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """
        log_runs() for a function already read (no GetFunction).
        """
        pattern = _filter_pattern(search)
        if not pattern:
            return self._log_page(fn, start, end, limit=limit, progress=progress)
        page = LogPage(fn, fn.log_group, start, end, pattern=pattern)
        try:
            hits, page.truncated, page.covered_from = self._filter(
                fn.log_group, fn.region, start, end, pattern, _SEARCH_HITS, progress=progress)
            phrase = _phrase(search)
            if phrase:  # the same check here, for log stores that only roughly apply patterns
                hits = [e for e in hits if phrase in e.message]
            page.events = self._around(fn, hits, limit)
        except ClientError as exc:
            page.errors["logs"] = _error_code(exc)
            return page
        except BotoCoreError as exc:
            page.errors["logs"] = type(exc).__name__
            return page
        if not page.events:
            page.latest = self._latest(fn.log_group, fn.region)
        return page

    def _around(self, fn: Function, hits: list[LogEvent], limit: int | None = 3_000) -> list[LogEvent]:
        """
        Every line of the runs that `hits` (lines a search found) belong to, oldest first, the hits marked: each
        stream's lines from a timeout before a hit to a timeout after it (no run lasts longer), windows that overlap
        read once, the newest _SEARCH_WINDOWS of them.
        """
        pad = timedelta(seconds=fn.timeout + 10)
        spans: dict[str, list[list[datetime]]] = {}
        for hit in sorted(hits, key=lambda e: e.time):
            hit.matched = True
            windows = spans.setdefault(hit.stream, [])
            if windows and hit.time - pad <= windows[-1][1]:
                windows[-1][1] = hit.time + pad
            else:
                windows.append([hit.time - pad, hit.time + pad])
        reads = sorted(((stream, a, b) for stream, windows in spans.items() for a, b in windows if stream),
                       key=lambda job: job[2], reverse=True)[:_SEARCH_WINDOWS]
        wanted = {(e.stream, e.time, e.message) for e in hits}

        def read(job: tuple[str, datetime, datetime]) -> list[LogEvent]:
            stream, a, b = job
            return self._filter(fn.log_group, fn.region, a, b, None, limit, stream=stream)[0]

        found: dict[tuple[str, datetime, str], LogEvent] = {}
        for events in self._map(read, reads):
            for event in events:
                key = (event.stream, event.time, event.message)
                event.matched = key in wanted
                found.setdefault(key, event)
        for hit in hits:  # a hit whose window wasn't read still shows, on its own
            found.setdefault((hit.stream, hit.time, hit.message), hit)
        return sorted(found.values(), key=lambda e: e.time)

    def _run_around(
        self, fn: Function, moment: datetime, *, stream: str = "", request_id: str | None = None
    ) -> LogRun | None:
        """
        The whole run that logged a line at `moment` in `stream` (the one with request_id, when given): its stream's
        lines from a timeout before to a timeout after. Without a stream, a search for the request ID finds it.
        """
        pad = timedelta(seconds=fn.timeout + 10)
        if stream:
            events = self._filter(fn.log_group, fn.region, moment - pad, moment + pad, None, 10_000, stream=stream)[0]
        elif request_id:
            events = self._around(fn, self._filter(fn.log_group, fn.region, moment - pad, moment + pad,
                                                   f'"{request_id}"', _SEARCH_HITS)[0], 10_000)
        else:
            return None
        runs = [run for run in split_runs(events) if run.request_id]
        if request_id:
            return next((run for run in runs if run.request_id == request_id), None)
        return next((run for run in runs if run.start <= moment <= run.end), None)

    def errors(
        self,
        ref: str,
        *,
        since: Any = "24h",
        until: Any = None,
        limit: int | None = 10_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> ErrorReport:
        """
        A function's errors from `since` to `until`, from its logs and grouped by cause (group_errors), with
        CloudWatch's count of failed and throttled calls over the same window.
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._errors(fn, start, end, limit=limit, progress=progress)

    def _errors(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = 10_000,
        progress: Callable[..., None] | None = None,
    ) -> ErrorReport:
        """
        errors() for a function already read (no GetFunction).
        """
        report = ErrorReport(fn, fn.log_group, start, end)
        events: list[LogEvent] = []
        try:
            events, report.truncated, report.covered_from = self._filter(
                fn.log_group, fn.region, start, end, _ERROR_PATTERN, limit, progress=progress)
        except ClientError as exc:
            report.errors["logs"] = _error_code(exc)
        except BotoCoreError as exc:
            report.errors["logs"] = type(exc).__name__
        failures = [e for e in events if classify_error(e.message)]
        unnamed = [e for e in sorted(failures, key=lambda e: e.time, reverse=True) if not e.request_id]
        for event in unnamed[:10]:  # the newest lines that don't say which run they're from (Python's tracebacks)
            event.request_id = self._run_of(fn, event)
        report.errors_found = len(failures)
        report.groups = group_errors(failures)
        report.newest = sorted(failures, key=lambda e: e.time, reverse=True)[:10]
        try:
            report.metrics = self.metrics([fn], since=start, until=end, logs=False)[0].get(fn.arn)
        except ClientError as exc:
            report.errors["metrics"] = _error_code(exc)
        except BotoCoreError as exc:
            report.errors["metrics"] = type(exc).__name__
        return report

    def performance(
        self,
        ref: str,
        *,
        since: Any = "24h",
        until: Any = None,
        limit: int | None = 5_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """
        Every run from `since` to `until` (the newest `limit` when there are more), from the REPORT line Lambda
        logs after each: run time, billed time, memory used, and the start-up time of cold starts.
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._performance(fn, start, end, limit=limit, progress=progress)

    def _performance(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = 5_000,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """
        performance() for a function already read (no GetFunction).
        """
        perf = Performance(fn, fn.log_group, start, end)
        try:
            events, perf.truncated, perf.covered_from = self._filter(
                fn.log_group, fn.region, start, end, _REPORT_PATTERN, limit, progress=progress)
        except ClientError as exc:
            perf.errors["logs"] = _error_code(exc)
            return perf
        except BotoCoreError as exc:
            perf.errors["logs"] = type(exc).__name__
            return perf
        runs = []
        for event in events:
            run = parse_report(event.message, event.time)
            if run is not None:
                run.stream = event.stream
                runs.append(run)
        perf.invocations = sorted(runs, key=lambda r: r.time or start, reverse=True)
        return perf

    # ------------------------------------------------------------------ code

    def _download(self, url: str, max_bytes: int | None) -> bytes:
        """
        The deployment package, from the short-lived link GetFunction gives (an HTTPS download from S3).
        """
        if not url.lower().startswith("https://"):
            raise ValueError("Lambda's link to the code isn't an https:// address, so it isn't downloaded")
        too_big = (
            "The package is {size}, more than max_size ({most}): pass max_size='{more}' to read it anyway."
        )
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                length = int(resp.headers.get("Content-Length") or 0)
                if max_bytes is not None and length > max_bytes:
                    raise ValueError(too_big.format(size=human_size(length), most=human_size(max_bytes),
                                                    more=f"{math.ceil(length / MB) + 1}MB"))
                data = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
        except OSError as exc:  # no route to S3, a timeout, an expired link
            raise ValueError(
                f"Couldn't download the code ({getattr(exc, 'reason', exc)}): the notebook must reach S3 over HTTPS. "
                "In a VPC without internet access, that takes an S3 gateway endpoint."
            ) from None
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(too_big.format(size=f"over {human_size(max_bytes)}", most=human_size(max_bytes),
                                            more=f"{math.ceil(max_bytes * 2 / MB)}MB"))
        return data

    def _kept_package(self, key: tuple[str, str] | None, max_bytes: int | None) -> bytes | None:
        """
        The package kept under `key`, when there is one no bigger than max_bytes.
        """
        if key is None:
            return None
        with self._lock:
            data = self._packages.get(key)
        if data is None or (max_bytes is not None and len(data) > max_bytes):
            return None
        return data

    def _keep_package(self, key: tuple[str, str] | None, data: bytes) -> None:
        """
        Keeps a downloaded package for the next read of the same code, dropping the oldest past _PACKAGES_KEPT: only
        one whose SHA-256 is the CodeSha256 in `key`, so a download that came out wrong is never kept.
        """
        if key is None or len(data) > _KEPT_PACKAGE_BYTES:
            return
        if base64.b64encode(hashlib.sha256(data).digest()).decode() != key[1]:
            return
        with self._lock:
            self._packages.pop(key, None)
            self._packages[key] = data
            while len(self._packages) > _PACKAGES_KEPT:
                del self._packages[next(iter(self._packages))]

    def _package(self, fn: Function, max_size: Any = "50MB") -> bytes | None:
        """
        A function's deployment package (fn as function() returns it, with the link GetFunction gives), downloaded up
        to max_size; None for a container image, or when Lambda gave no link. The last _PACKAGES_KEPT, up to
        _KEPT_PACKAGE_BYTES each, are kept in memory by ARN and CodeSha256, newest last, so reading another file of the same code (code(), code_map(), the agent's function_source)
        doesn't download it again, and new code, which has a new hash, always is.
        """
        if fn.package_type == "Image" or not fn.code_location:
            return None
        sha = fn.raw.get("CodeSha256")
        key = (fn.arn, sha) if sha else None
        max_bytes = parse_size(max_size)
        data = self._kept_package(key, max_bytes)
        if data is None:
            data = self._download(fn.code_location, max_bytes)
            self._keep_package(key, data)
        return data

    def code(
        self, ref: str, *, file: str | None = None, max_size: Any = "50MB", region: str | None = None
    ) -> CodePackage:
        """
        A function's deployment package: every file in the .zip with its size, the file that holds the
        handler, and the text of that file (or of file=, a path, a file name or a glob like '*.py'). Downloads the
        package (up to max_size) from the link Lambda gives; nothing in it is run. A secrets file (.env, keys,
        credentials) is named but its text isn't read, and a container image is named, not pulled.
        """
        fn = self.function(ref, region=region)
        return read_package(fn, self._package(fn, max_size), file=file)

    def sources(
        self, ref: str, *, files: str | None = None, max_size: Any = "50MB", region: str | None = None
    ) -> PackageSources:
        """
        The text of a function's own source files, or with files= of every file that matches (see read_sources()),
        by path. Downloads the package like code(); nothing in it is run, and secrets files aren't read.
        """
        fn = self.function(ref, region=region)
        return read_sources(fn, self._package(fn, max_size), files=files)

    def search_code(
        self, ref: str, pattern: str, *, regex: bool = False, files: str | None = None, limit: int = 50,
        max_size: Any = "50MB", region: str | None = None,
    ) -> CodeSearch:
        """
        The lines of a function's code that contain `pattern`, or match it as a regular expression with regex=True,
        across its package's own source files (or the files files= names, libraries too), like grep -rn (see
        search_package()): the first `limit` matches, and how many there are in all. Downloads the package like code(); nothing in it is run, and secrets files aren't read.
        """
        fn = self.function(ref, region=region)
        search = search_package(fn, self._package(fn, max_size), pattern, regex=regex, files=files)
        search.matches = search.matches[:limit]
        return search

    def find_symbol(
        self, ref: str, name: str, *, limit: int = 40, max_size: Any = "50MB", region: str | None = None
    ) -> SymbolReport:
        """
        Where a Python name ('load_orders', 'Orders.load') is defined in a function's code and the lines that use it,
        like go to definition and find references (see find_symbol_in()). Downloads the package like code() and
        reads its .py files with Python's ast module; nothing in it is run.
        """
        fn = self.function(ref, region=region)
        data = self._package(fn, max_size) if runtime_family(fn.runtime) == "python" else None
        return find_symbol_in(fn, data, name, limit=limit)

    def code_map(self, ref: str, *, max_size: Any = "50MB", region: str | None = None) -> CodeMap:
        """
        How a Python function's code works (see map_code()): downloads its package, up to max_size, like code(),
        and reads its own .py files with Python's ast module, starting at the handler. Nothing in it is run. A function
        on another runtime, or a container image, gives a CodeMap with only a note saying so.
        """
        fn = self.function(ref, region=region)
        data = self._package(fn, max_size) if runtime_family(fn.runtime) == "python" else None
        return map_package(fn, data)

    def diff(
        self, ref: str, old: Any = None, new: Any = None, *, max_size: Any = "50MB", region: str | None = None
    ) -> VersionDiff:
        """
        What changed between two versions of a function (see diff_packages()): its own files line by line, the
        libraries packed with it, its settings and environment variable names. new= is the version or alias to look
        at (by default the one ref names, 'etl:prod', else $LATEST) and old= the one to compare it with (by default
        the newest published version before it whose code is different). Downloads both packages, up to max_size,
        like code(); nothing in them is run. Lambda keeps the code of published versions only, so with none before
        it the VersionDiff has only a note saying so.
        """
        name, qualifier, where = self.locate(ref, region)
        versions = self.versions(name, where)
        new_fn = self.function(f"{name}:{parse_qualifier(new if new is not None else qualifier or '$LATEST')}",
                               region=where)
        passed: list[str] = []
        if old is None:
            before, passed = version_before(versions, new_fn)
            if before is None:
                return VersionDiff(new_fn, versions=versions, note=nothing_before(new_fn))
            old = before.version
        old_fn = self.function(f"{name}:{parse_qualifier(old)}", region=where)
        old_data, new_data = self._map(lambda fn: self._package(fn, max_size), [old_fn, new_fn])
        diff = diff_packages(old_fn, old_data, new_fn, new_data)
        diff.versions, diff.skipped = versions, passed
        return diff

    def download(self, ref: str, path: str | None = None, *, region: str | None = None) -> PackageDownload:
        """
        Saves a function's deployment package on the notebook's disk, the .zip as Lambda stores it: at `path` (a
        file, or a folder to put <name>.zip in), else in the downloads folder (see downloads_folder()) as
        'etl.zip', or 'etl-v3.zip' for a published version. A file of that name is replaced. Nothing in it is
        unzipped or run, and its SHA-256 is checked against the one Lambda reports. A container image is named, not
        pulled.
        """
        fn = self.function(ref, region=region)
        if fn.package_type == "Image":
            raise _Hint(f"{fn.name} is a container image ({fn.image_uri or 'image URI not shown'}): its code is in the "
                        "image, which this doesn't pull. docker pull it from ECR to look inside.")
        data = self._package(fn, None)
        if data is None:
            raise ValueError(f"Lambda gave no link to download {fn.name}'s code.")
        return self._save_package(fn, data, path)

    def downloads_folder(self) -> str:
        """
        The folder download() saves in when it's given no path (absolute): `self.downloads`, 'lambda-downloads'
        next to the notebook unless LambdaAnalyzer(downloads=...) says otherwise.
        """
        return os.path.abspath(os.path.expanduser(str(self.downloads).strip() or "."))

    def _write_download(self, name: str, data: bytes, path: str | None) -> tuple[str, bool, bool]:
        """
        Writes data where download() saves: path= (a file, or a folder to put `name` in), else `name` in the
        downloads folder; to <target>.part, then renamed over a file of that name. A downloads folder it makes gets a
        .gitignore that keeps it out of git, since a package can hold secrets. Returns (where, whether it replaced a
        file, whether it made the downloads folder); an OSError becomes a ValueError that says why.
        """
        target = os.path.abspath(os.path.expanduser(path)) if path else os.path.join(self.downloads_folder(), name)
        if os.path.isdir(target) or (path and path.endswith(("/", os.sep))):
            target = os.path.join(target, name)
        folder = os.path.dirname(target)
        replaced, marked = os.path.exists(target), False
        part = target + ".part"
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
                if not path:
                    with open(os.path.join(folder, ".gitignore"), "w", encoding="utf-8") as mark:
                        mark.write(_DOWNLOADS_MARK)
                    marked = True
            with open(part, "wb") as handle:
                handle.write(data)
            os.replace(part, target)
        except OSError as exc:
            if os.path.exists(part):
                os.remove(part)
            raise ValueError(f"Couldn't save {name} in {folder}: {exc.strerror or exc}") from None
        return target, replaced, marked

    def _save_package(self, fn: Function, data: bytes, path: str | None = None) -> PackageDownload:
        """
        Writes a package already downloaded (data, from _package) where download() would: no AWS call.
        """
        target, replaced, marked = self._write_download(package_file_name(fn), data, path)
        saved = PackageDownload(fn, target, len(data), replaced=replaced, marked=marked)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                saved.files = sum(1 for info in archive.infolist() if not info.is_dir())
        except zipfile.BadZipFile:
            pass
        expected = fn.raw.get("CodeSha256")
        if expected:
            saved.sha256_matches = base64.b64encode(hashlib.sha256(data).digest()).decode() == expected
        return saved

    def _save_page(self, fn: Function, page: str, path: str | None = None) -> SavedPage:
        """
        Writes a report drawn as a page (a whole HTML document) where download() saves, as
        page_file_name(fn) unless path= names a file: no AWS call.
        """
        data = page.encode("utf-8")
        target, replaced, marked = self._write_download(page_file_name(fn), data, path)
        return SavedPage(fn, target, len(data), replaced=replaced, marked=marked)
