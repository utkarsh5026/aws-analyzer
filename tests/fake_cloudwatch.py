"""
A fake CloudWatch for the agent's CloudWatch toolkit: acme's numbers over the last weeks and its alarms, every request
and answer checked against the service model (fake_stepfunctions' _Checked). moto's CloudWatch has no percentiles,
so p95 latency comes from here.

scene(now) is a night with something wrong, `now` being the end of the hour asked about:
- churn-endpoint (SageMaker, variant AllTraffic): p95 ModelLatency about 400 ms every day, 1,840 ms from 02:10 tonight
  (INCIDENT, 50 minutes before now); its traffic is as usual; no alarm on it.
- orders-etl (Lambda): 1 or 2 errors an hour as usual, 7 every 5 minutes from INCIDENT; alarm orders-etl-errors
  (Errors Sum > 10 over 5 minutes) is in ALARM since then and tells an SNS topic.
- email-jobs (SQS): 20 messages waiting, as always.
- checkout-5xx: a REST API's alarm in ALARM with no actions; legacy-batch-cpu: an EC2 alarm with no data for a month.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fake_stepfunctions import REGION, _Checked

ACCOUNT = "123456789012"
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
INCIDENT = timedelta(minutes=50)
FINE = 300


def _key(namespace: str, name: str, dimensions: list[dict[str, str]] | tuple[tuple[str, str], ...]) -> tuple:
    pairs = [(d["Name"], d["Value"]) for d in dimensions] if dimensions and isinstance(dimensions[0], dict) \
        else list(dimensions)
    return namespace, name, tuple(sorted(pairs))


@dataclass
class Scene:
    """
    The numbers (by namespace, metric name and dimensions: a value for each 5-minute point, or None for none) and the
    alarms (as DescribeAlarms returns them).
    """

    numbers: dict[tuple, Callable[[datetime], float | None]] = field(default_factory=dict)
    alarms: list[dict[str, Any]] = field(default_factory=list)

    def add(self, namespace: str, name: str, dimensions: tuple[tuple[str, str], ...],
            values: Callable[[datetime], float | None]) -> None:
        self.numbers[_key(namespace, name, dimensions)] = values


def _percentile(values: list[float], stat: str) -> float:
    ordered = sorted(values)
    wanted = float(stat[1:]) / 100
    return ordered[min(len(ordered) - 1, max(0, math.ceil(wanted * len(ordered)) - 1))]


def _aggregate(values: list[float], stat: str) -> float:
    if stat == "Sum":
        return sum(values)
    if stat == "Average":
        return sum(values) / len(values)
    if stat == "Minimum":
        return min(values)
    if stat == "SampleCount":
        return float(len(values))
    if stat.startswith("p"):
        return _percentile(values, stat)
    return max(values)


def _alarm(name: str, namespace: str, metric: str, dimensions: tuple[tuple[str, str], ...], *, state: str,
           since: datetime, threshold: float, stat: str = "Sum", actions: bool = True, enabled: bool = True,
           operator: str = "GreaterThanThreshold", reason: str = "") -> dict[str, Any]:
    stat_key = "ExtendedStatistic" if stat.startswith("p") else "Statistic"
    return {"AlarmName": name, "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{name}",
            "StateValue": state, "StateUpdatedTimestamp": since, "StateReason": reason or f"{name} is {state}",
            "Namespace": namespace, "MetricName": metric,
            "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions], stat_key: stat, "Period": FINE,
            "EvaluationPeriods": 1, "DatapointsToAlarm": 1, "Threshold": threshold,
            "ComparisonOperator": operator, "ActionsEnabled": enabled,
            "AlarmActions": [f"arn:aws:sns:{REGION}:{ACCOUNT}:on-call"] if actions else []}


ENDPOINT = (("EndpointName", "churn-endpoint"), ("VariantName", "AllTraffic"))
FUNCTION = (("FunctionName", "orders-etl"),)
QUEUE = (("QueueName", "email-jobs"),)


def scene(now: datetime = NOW) -> Scene:
    """
    acme's night (see the module's docstring), `now` being the end of the hour asked about.
    """
    started = now - INCIDENT

    def tonight(t: datetime) -> bool:
        return started <= t < now + timedelta(minutes=5)

    def latency(t: datetime) -> float:
        if tonight(t):
            return 1_840_000.0
        return 410_000.0 if (now - t) > timedelta(days=3) else 395_000.0

    def errors(t: datetime) -> float:
        if tonight(t):
            return 7.0
        return 1.0 if t.minute in (0, 30) else 0.0

    world = Scene()
    world.add("AWS/SageMaker", "ModelLatency", ENDPOINT, latency)
    world.add("AWS/SageMaker", "Invocations", ENDPOINT, lambda t: 100.0)
    world.add("AWS/SageMaker", "Invocation5XXErrors", ENDPOINT, lambda t: None)
    world.add("AWS/SageMaker", "Invocation4XXErrors", ENDPOINT, lambda t: None)
    world.add("AWS/Lambda", "Invocations", FUNCTION, lambda t: 100.0)
    world.add("AWS/Lambda", "Errors", FUNCTION, errors)
    world.add("AWS/Lambda", "Duration", FUNCTION, lambda t: 2_100.0)
    world.add("AWS/Lambda", "Throttles", FUNCTION, lambda t: None)
    world.add("AWS/Lambda", "Invocations", (("FunctionName", "orders-api"),), lambda t: 40.0)
    world.add("AWS/SQS", "ApproximateNumberOfMessagesVisible", QUEUE, lambda t: 20.0)
    world.add("AWS/SQS", "ApproximateAgeOfOldestMessage", QUEUE, lambda t: 30.0)
    world.add("AWS/SQS", "NumberOfMessagesSent", QUEUE, lambda t: 50.0)
    world.alarms = [
        _alarm("orders-etl-errors", "AWS/Lambda", "Errors", FUNCTION, state="ALARM", since=started + timedelta(
            minutes=5), threshold=10),
        _alarm("checkout-5xx", "AWS/ApiGateway", "5XXError", (("ApiName", "checkout-api"),), state="ALARM",
               since=now - timedelta(hours=3), threshold=5, actions=False),
        _alarm("email-jobs-backlog", "AWS/SQS", "ApproximateNumberOfMessagesVisible", QUEUE, state="OK",
               since=now - timedelta(days=12), threshold=1000, stat="Maximum"),
        _alarm("legacy-batch-cpu", "AWS/EC2", "CPUUtilization", (("InstanceId", "i-0old"),),
               state="INSUFFICIENT_DATA", since=now - timedelta(days=30), threshold=90, stat="Average"),
    ]
    return world


class FakeCloudWatch(_Checked):
    """
    GetMetricData, ListMetrics, DescribeAlarmsForMetric and DescribeAlarms over a Scene.
    """

    service = "cloudwatch"

    def __init__(self, world: Scene | None = None, region: str = REGION):
        super().__init__(region)
        self.world = world or scene()

    def _values(self, key: tuple, start: datetime, period: int, stat: str) -> float | None:
        values = self.world.numbers.get(key)
        if values is None:
            return None
        points = [values(start + timedelta(seconds=FINE * i)) for i in range(max(1, period // FINE))]
        found = [v for v in points if v is not None]
        return _aggregate(found, stat) if found else None

    def _series(self, query: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        stat = query["MetricStat"]
        metric = stat["Metric"]
        key = _key(metric["Namespace"], metric["MetricName"], metric.get("Dimensions", []))
        times, values = [], []
        moment = start
        while moment < end:
            value = self._values(key, moment, stat["Period"], stat["Stat"])
            if value is not None:
                times.append(moment)
                values.append(value)
            moment += timedelta(seconds=stat["Period"])
        return {"Id": query["Id"], "Label": metric["MetricName"], "Timestamps": times, "Values": values,
                "StatusCode": "Complete"}

    def _get_metric_data(self, MetricDataQueries: list[dict[str, Any]], StartTime: datetime, EndTime: datetime,
                         **params: Any) -> dict[str, Any]:
        return {"MetricDataResults": [self._series(q, StartTime, EndTime) for q in MetricDataQueries]}

    def _list_metrics(self, Namespace: str = "", MetricName: str = "", Dimensions: Any = None,
                      **params: Any) -> dict[str, Any]:
        wanted = {(d["Name"], d["Value"]) for d in Dimensions or []}
        found = []
        for namespace, name, dims in self.world.numbers:
            if (Namespace and namespace != Namespace) or (MetricName and name != MetricName):
                continue
            if wanted <= set(dims):
                found.append({"Namespace": namespace, "MetricName": name,
                              "Dimensions": [{"Name": k, "Value": v} for k, v in dims]})
        return {"Metrics": found}

    def _describe_alarms_for_metric(self, MetricName: str, Namespace: str, Dimensions: Any = None,
                                    **params: Any) -> dict[str, Any]:
        key = _key(Namespace, MetricName, Dimensions or [])
        return {"MetricAlarms": [a for a in self.world.alarms
                                 if _key(a["Namespace"], a["MetricName"], a["Dimensions"]) == key]}

    def _describe_alarms(self, StateValue: str = "", AlarmNamePrefix: str = "", **params: Any) -> dict[str, Any]:
        found = [a for a in self.world.alarms
                 if (not StateValue or a["StateValue"] == StateValue) and a["AlarmName"].startswith(AlarmNamePrefix)]
        return {"MetricAlarms": found, "CompositeAlarms": []}
