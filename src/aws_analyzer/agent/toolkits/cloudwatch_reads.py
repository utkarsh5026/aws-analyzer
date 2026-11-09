"""
The CloudWatch reads behind the agent's CloudWatch toolkit: a resource's numbers over three windows (now, a day back
and a week back) with GetMetricData, the dimensions a resource's numbers are filed under with ListMetrics (a
SageMaker endpoint's variants, a state machine's ARN, an HTTP API's stage), the alarms on one metric with
DescribeAlarmsForMetric, and a region's alarms with DescribeAlarms. Every one of them only reads.

MetricReader is the toolkit's analyzer: the agent's read-only guard hooks its clients (cloudwatch(), the clients it
makes per region). The maths is baselines.py's, pure.
"""

from __future__ import annotations

import difflib
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..._kit.analyzer import _BaseAnalyzer
from .baselines import DAY, FINE_SECONDS, RESOURCE_KINDS, WEEK, Alarm, MetricSpec, ResourceKind, ResourceRef, Series, Window
from .baselines import parse_alarm

_QUERIES_PER_CALL = 500
_VARIANTS = 3
_CLOSE_NAMES = 5
_LISTED_ALARMS = 500
_SHIFTS = (timedelta(0), DAY, WEEK)


class NoMetrics(LookupError):
    """
    CloudWatch has no numbers for the resource asked about in the region: `close` are the names it has that look
    like it.
    """

    def __init__(self, message: str, close: list[str]):
        super().__init__(message)
        self.close = close


@dataclass(frozen=True)
class Resource:
    """
    A resource as CloudWatch files its numbers: its kind (RESOURCE_KINDS', or None for a raw metric), the name a sentence
    calls it, its namespace, and the dimensions each of its numbers is read with (one set, or one per SageMaker
    variant), with a label for each set when there are several.
    """

    kind: ResourceKind | None
    name: str
    namespace: str
    dimension_sets: tuple[tuple[tuple[str, str], ...], ...]
    set_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricRead:
    """
    One number to read: its spec, the dimensions to read it with, and the label a sentence uses for it.
    """

    spec: MetricSpec
    dimensions: tuple[tuple[str, str], ...]
    label: str


def _dimension_list(dimensions: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"Name": name, "Value": value} for name, value in dimensions]


def _queries(reads: list[MetricRead], namespace: str, window: Window) -> list[dict[str, Any]]:
    """
    GetMetricData's queries for the reads: each number twice, once per period of the window (p<i>) and once per 5
    minutes (f<i>), so the window's value is CloudWatch's own (a p95 over the hour, not a guess from 5-minute ones).
    """
    queries = []
    for i, read in enumerate(reads):
        metric = {"Namespace": namespace, "MetricName": read.spec.name,
                  "Dimensions": _dimension_list(read.dimensions)}
        queries.append({"Id": f"p{i}", "ReturnData": True,
                        "MetricStat": {"Metric": metric, "Period": window.minutes * 60, "Stat": read.spec.stat}})
        queries.append({"Id": f"f{i}", "ReturnData": True,
                        "MetricStat": {"Metric": metric, "Period": FINE_SECONDS, "Stat": read.spec.stat}})
    return queries


def _points(result: dict[str, Any]) -> dict[datetime, float]:
    return dict(zip(result.get("Timestamps", []), result.get("Values", [])))


class MetricReader(_BaseAnalyzer):
    """
    Reads CloudWatch for the agent's CloudWatch toolkit: numbers over a window and the same window a day and a week
    back, the dimensions a resource's numbers are filed under, and alarms. `client` is a CloudWatch client to read
    through (else one is made on `session`, in `region`); cloudwatch(region) makes one per other region.
    """

    def __init__(self, session: Any = None, *, region: str | None = None, profile: str | None = None,
                 client: Any = None):
        super().__init__(session, region=region, profile=profile)
        self.client = client or self.session.client("cloudwatch", region_name=region, config=self._config)
        self._clients: dict[str, Any] = {self.client.meta.region_name: self.client}
        self._lock = threading.Lock()
        self.max_workers = 3

    def cloudwatch(self, region: str | None = None) -> Any:
        """
        The CloudWatch client for `region` (None: the reader's own), made once.
        """
        wanted = region or self.region
        return self._cached_client(wanted, lambda: self.session.client("cloudwatch", region_name=wanted,
                                                                       config=self._config))

    def _list_metrics(self, client: Any, namespace: str, metric: str,
                      dimensions: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"Namespace": namespace, "MetricName": metric}
        if dimensions:
            params["Dimensions"] = dimensions
        return [m for page in client.get_paginator("list_metrics").paginate(**params) for m in page.get("Metrics", [])]

    def _close_names(self, client: Any, kind: ResourceKind, wanted: str) -> list[str]:
        """
        The names CloudWatch has numbers for, of the kind asked about, that look like `wanted`.
        """
        names = set()
        for metric in self._list_metrics(client, kind.namespace, kind.metrics[0].name):
            for dimension in metric.get("Dimensions", []):
                if dimension.get("Name") == kind.dimension:
                    names.add(_short_name(kind, dimension.get("Value", "")))
        return difflib.get_close_matches(_short_name(kind, wanted), sorted(names), n=_CLOSE_NAMES, cutoff=0.4)

    def _missing(self, client: Any, kind: ResourceKind, name: str, region: str) -> NoMetrics:
        close = self._close_names(client, kind, name)
        hint = f" Close names: {', '.join(close)}." if close else ""
        return NoMetrics(f"CloudWatch has no numbers for {kind.title} {name!r} in {region} over the last two weeks."
                         f"{hint}", close)

    def _state_machine_arn(self, client: Any, kind: ResourceKind, name: str, region: str) -> str:
        """
        A state machine's ARN, from its name: the StateMachineArn dimension CloudWatch files its numbers under.
        """
        if name.startswith("arn:"):
            return name
        for metric in self._list_metrics(client, kind.namespace, kind.metrics[0].name):
            for dimension in metric.get("Dimensions", []):
                if dimension.get("Value", "").endswith(f":stateMachine:{name}"):
                    return dimension["Value"]
        raise self._missing(client, kind, name, region)

    def _found_sets(self, client: Any, kind: ResourceKind, name: str, region: str) -> list[dict[str, str]]:
        """
        The dimension sets a resource's first number is filed under beside its own dimension (a SageMaker endpoint's
        variants, an HTTP API's stages), each as {name: value}, sorted.
        """
        wanted = {kind.dimension, *kind.found}
        found = []
        for metric in self._list_metrics(client, kind.namespace, kind.metrics[0].name,
                                         [{"Name": kind.dimension, "Value": name}]):
            dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
            if set(dims) == wanted:
                found.append(dims)
        if not found:
            raise self._missing(client, kind, name, region)
        return sorted(found, key=lambda dims: tuple(sorted(dims.items())))

    def resolve(self, ref: ResourceRef, region: str | None = None) -> Resource:
        """
        Where CloudWatch files the numbers of the resource `ref` names, in `region`: its namespace and dimension
        sets, read with ListMetrics where they can't be told from the name (a state machine's ARN, a SageMaker
        endpoint's variants, an HTTP API's stage). NoMetrics, with close names, when it has none.
        """
        if not ref.kind:
            return Resource(None, ",".join(value for _, value in ref.dimensions), ref.namespace, (ref.dimensions,))
        kind = RESOURCE_KINDS[ref.kind]
        client = self.cloudwatch(region)
        where = region or self.region
        name = ref.name
        if kind.key == "states":
            arn = self._state_machine_arn(client, kind, name, where)
            return Resource(kind, arn.rsplit(":", 1)[-1], kind.namespace, (((kind.dimension, arn),),))
        if kind.key == "lambda" and ":" in name:
            function = name.split(":", 1)[0]
            return Resource(kind, name, kind.namespace, (((kind.dimension, function), ("Resource", name)),))
        if not kind.found:
            return Resource(kind, name, kind.namespace, (((kind.dimension, name),),))
        sets = self._found_sets(client, kind, name, where)[:_VARIANTS]
        dims = tuple(tuple(sorted(found.items(), key=lambda kv: kv[0] != kind.dimension)) for found in sets)
        labels = tuple(", ".join(f"{k} {v}" for k, v in found.items() if k != kind.dimension) for found in sets)
        return Resource(kind, name, kind.namespace, dims, labels if len(sets) > 1 else ())

    def _read_window(self, client: Any, reads: list[MetricRead], namespace: str, window: Window,
                     shift: timedelta) -> list[Series]:
        """
        Every read's Series over the window moved back by `shift` (none, a day or a week), from GetMetricData,
        page by page.
        """
        queries = _queries(reads, namespace, window)
        found: dict[str, dict[datetime, float]] = {q["Id"]: {} for q in queries}
        for start in range(0, len(queries), _QUERIES_PER_CALL):
            params: dict[str, Any] = {"MetricDataQueries": queries[start:start + _QUERIES_PER_CALL],
                                      "StartTime": window.trend_start - shift, "EndTime": window.end - shift,
                                      "ScanBy": "TimestampAscending"}
            while True:
                page = client.get_metric_data(**params)
                for result in page.get("MetricDataResults", []):
                    found[result["Id"]].update(_points(result))
                token = page.get("NextToken")
                if not token:
                    break
                params["NextToken"] = token
        return [Series({t: v * read.spec.scale for t, v in found[f"p{i}"].items()},
                       {t: v * read.spec.scale for t, v in found[f"f{i}"].items()})
                for i, read in enumerate(reads)]

    def read(self, reads: list[MetricRead], namespace: str, window: Window,
             region: str | None = None) -> list[tuple[Series, Series, Series]]:
        """
        Each read's numbers over the window, the same window a day back and a week back (now's, yesterday's and last
        week's Series), read side by side: one GetMetricData call per window for every number.
        """
        client = self.cloudwatch(region)
        windows = self._map(lambda shift: self._read_window(client, reads, namespace, window, shift), list(_SHIFTS))
        return [(windows[0][i], windows[1][i], windows[2][i]) for i in range(len(reads))]

    def has_metrics(self, resource: Resource, region: str | None = None) -> None:
        """
        Raises NoMetrics, with close names, when CloudWatch files no numbers of the resource's kind under its name
        (a typo: Lambda publishes nothing for a function that doesn't exist).
        """
        kind = resource.kind
        if kind is None:
            return
        client = self.cloudwatch(region)
        dimension = resource.dimension_sets[0][0]
        if not self._list_metrics(client, kind.namespace, kind.metrics[0].name, _dimension_list((dimension,))):
            raise self._missing(client, kind, resource.name, region or self.region)

    def alarms_for(self, read: MetricRead, namespace: str, region: str | None = None) -> list[Alarm]:
        """
        The alarms set on one number (any statistic or period), from DescribeAlarmsForMetric, with their thresholds
        in the number's unit.
        """
        client = self.cloudwatch(region)
        answer = client.describe_alarms_for_metric(MetricName=read.spec.name, Namespace=namespace,
                                                   Dimensions=_dimension_list(read.dimensions))
        return [parse_alarm(raw, unit=read.spec.unit, scale=read.spec.scale) for raw in answer.get("MetricAlarms", [])]

    def alarms(self, *, state: str = "", prefix: str = "", region: str | None = None) -> tuple[list[Alarm], bool]:
        """
        The region's alarms (metric and composite), in `state` ('ALARM', 'OK', 'INSUFFICIENT_DATA' or '' for all),
        whose names start with `prefix`, from DescribeAlarms page by page; and whether it stopped at _LISTED_ALARMS.
        """
        params: dict[str, Any] = {"AlarmTypes": ["MetricAlarm", "CompositeAlarm"]}
        if state:
            params["StateValue"] = state
        if prefix:
            params["AlarmNamePrefix"] = prefix
        found: list[Alarm] = []
        for page in self.cloudwatch(region).get_paginator("describe_alarms").paginate(**params):
            for raw in [*page.get("MetricAlarms", []), *page.get("CompositeAlarms", [])]:
                found.append(_alarm_in_its_unit(raw))
            if len(found) >= _LISTED_ALARMS:
                return found[:_LISTED_ALARMS], True
        return found, False


def _short_name(kind: ResourceKind, value: str) -> str:
    """
    How a resource's dimension value reads as a name: a state machine's ARN as its name, anything else as it is.
    """
    return value.rsplit(":", 1)[-1] if kind.key == "states" else value


def _alarm_in_its_unit(raw: dict[str, Any]) -> Alarm:
    """
    An alarm from DescribeAlarms, its threshold in the unit the comparison uses when RESOURCE_KINDS knows its metric
    (SageMaker's ModelLatency in ms, not microseconds).
    """
    for kind in RESOURCE_KINDS.values():
        if kind.namespace != raw.get("Namespace"):
            continue
        for spec in kind.metrics:
            if spec.name == raw.get("MetricName"):
                return parse_alarm(raw, unit=spec.unit, scale=spec.scale)
    return parse_alarm(raw)
