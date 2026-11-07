"""
CloudWatch's numbers for queues, topics and rules: what each sent, received, delivered or failed over a window of
whole UTC days (CloudWatch's daily numbers), a line per day for one thing's report, and the age of a queue's oldest
message now. A mixin of MessagingAnalyzer; reads only (GetMetricData).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .._kit.fmt import _utcnow

from .models import Queue, QueueNumbers, Rule, RuleNumbers, Topic, TopicNumbers


_DAY_SECONDS = 86_400

_QUERIES_PER_CALL = 500

_NOW_WINDOW = timedelta(hours=1)

_NOW_PERIOD = 300

_QUEUE_COUNTS = (("sent", "NumberOfMessagesSent"), ("received", "NumberOfMessagesReceived"),
                 ("deleted", "NumberOfMessagesDeleted"), ("empty_receives", "NumberOfEmptyReceives"))

_OLDEST = "ApproximateAgeOfOldestMessage"

_TOPIC_COUNTS = (("published", "NumberOfMessagesPublished"), ("delivered", "NumberOfNotificationsDelivered"),
                 ("failed", "NumberOfNotificationsFailed"), ("filtered", "NumberOfNotificationsFilteredOut"),
                 ("redriven", "NumberOfNotificationsRedrivenToDlq"),
                 ("redrive_failed", "NumberOfNotificationsFailedToRedriveToDlq"))

_RULE_COUNTS = (("matched", "TriggeredRules"), ("invocations", "Invocations"), ("failed", "FailedInvocations"),
                ("to_dlq", "InvocationsSentToDlq"), ("dlq_failed", "InvocationsFailedToBeSentToDlq"),
                ("throttled", "ThrottledRules"))

_DAILY_QUEUE = ("sent", "deleted")

_DAILY_TOPIC = ("published", "failed")

_DAILY_RULE = ("matched", "failed")


@dataclass
class _Metric:
    """
    One metric to read: who it's for (owner), which field of its numbers it fills, and where CloudWatch keeps it.
    """

    owner: str
    field: str
    namespace: str
    name: str
    dimensions: list[dict[str, str]]
    stat: str = "Sum"


def _midnight_after(now: datetime) -> datetime:
    """
    The UTC midnight that ends today: CloudWatch's daily numbers cover whole UTC days.
    """
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


def _queue_metrics(queue: Queue) -> list[_Metric]:
    dims = [{"Name": "QueueName", "Value": queue.name}]
    metrics = [_Metric(queue.arn, field, "AWS/SQS", name, dims) for field, name in _QUEUE_COUNTS]
    return [*metrics, _Metric(queue.arn, "oldest_max_s", "AWS/SQS", _OLDEST, dims, "Maximum")]


def _topic_metrics(topic: Topic) -> list[_Metric]:
    dims = [{"Name": "TopicName", "Value": topic.name}]
    return [_Metric(topic.arn, field, "AWS/SNS", name, dims) for field, name in _TOPIC_COUNTS]


def _rule_metrics(rule: Rule) -> list[_Metric]:
    dims = [{"Name": "RuleName", "Value": rule.name}]
    if rule.bus != "default":
        dims = [{"Name": "EventBusName", "Value": rule.bus}, *dims]
    return [_Metric(rule.arn, field, "AWS/Events", name, dims) for field, name in _RULE_COUNTS]


def _combined(points: list[tuple[datetime, float]], stat: str) -> float | None:
    if not points:
        return None
    values = [value for _, value in points]
    return max(values) if stat == "Maximum" else sum(values)


def _fill(numbers: Any, metric: _Metric, points: list[tuple[datetime, float]]) -> None:
    total = _combined(points, metric.stat)
    if total is None:
        return
    setattr(numbers, metric.field, total if metric.stat == "Maximum" else int(total))


def _days(points: dict[str, list[tuple[datetime, float]]], fields: tuple[str, ...]) -> list[tuple[Any, ...]]:
    """
    A line per day, oldest first: (day, field 1's sum, field 2's sum, ...), from each field's daily points.
    """
    by_day: dict[datetime, dict[str, float]] = {}
    for field, series in points.items():
        for moment, value in series:
            by_day.setdefault(moment, {})[field] = value
    return [(day, *(int(by_day[day].get(f, 0)) for f in fields)) for day in sorted(by_day)]


class _MetricReads:
    """
    The CloudWatch reads of MessagingAnalyzer: totals over a window for many things at once, a line per day for one,
    and how old a queue's oldest message is now. metrics_read counts what was asked for (each costs $0.00001).
    """

    metrics_read: int

    def _cloudwatch(self) -> Any:
        raise NotImplementedError

    def _query_batch(self, metrics: list[_Metric], start: datetime, end: datetime,
                     period: int) -> dict[int, list[tuple[datetime, float]]]:
        queries = [{"Id": f"m{i}", "MetricStat": {"Metric": {"Namespace": m.namespace, "MetricName": m.name,
                                                             "Dimensions": m.dimensions},
                                                  "Period": period, "Stat": m.stat}}
                   for i, m in enumerate(metrics)]
        found: dict[int, list[tuple[datetime, float]]] = {}
        for page in self._cloudwatch().get_paginator("get_metric_data").paginate(
                MetricDataQueries=queries, StartTime=start, EndTime=end):
            for result in page.get("MetricDataResults", []):
                index = int(result["Id"][1:])
                found.setdefault(index, []).extend(zip(result.get("Timestamps", []), result.get("Values", [])))
        return found

    def _read_metrics(self, metrics: list[_Metric], start: datetime, end: datetime,
                      period: int) -> list[list[tuple[datetime, float]]]:
        """
        Each metric's points (time, value) between start and end, in periods of `period` seconds, read 500 at a time.
        """
        points: list[list[tuple[datetime, float]]] = []
        for first in range(0, len(metrics), _QUERIES_PER_CALL):
            batch = metrics[first:first + _QUERIES_PER_CALL]
            found = self._query_batch(batch, start, end, period)
            points += [found.get(i, []) for i in range(len(batch))]
            self.metrics_read += len(batch)
        return points

    def _totals(self, metrics: list[_Metric], days: int, make: type) -> dict[str, Any]:
        """
        Each owner's numbers (a QueueNumbers, TopicNumbers or RuleNumbers made by `make`) over the last `days` whole
        UTC days.
        """
        end = _midnight_after(_utcnow())
        start = end - timedelta(days=days)
        numbers: dict[str, Any] = {}
        for metric, points in zip(metrics, self._read_metrics(metrics, start, end, days * _DAY_SECONDS)):
            _fill(numbers.setdefault(metric.owner, make()), metric, points)
        return numbers

    def _daily(self, metrics: list[_Metric], days: int, fields: tuple[str, ...]) -> list[tuple[Any, ...]]:
        end = _midnight_after(_utcnow())
        chosen = [m for m in metrics if m.field in fields]
        series = self._read_metrics(chosen, end - timedelta(days=days), end, _DAY_SECONDS)
        return _days({m.field: points for m, points in zip(chosen, series)}, fields)

    def _oldest_now(self, queues: list[Queue]) -> dict[str, float]:
        """
        How old each queue's oldest message is now (its latest five-minute reading in the last hour), in seconds.
        """
        metrics = [_Metric(q.arn, "oldest_now_s", "AWS/SQS", _OLDEST, [{"Name": "QueueName", "Value": q.name}],
                           "Maximum") for q in queues]
        now = _utcnow()
        found = {}
        for metric, points in zip(metrics, self._read_metrics(metrics, now - _NOW_WINDOW, now, _NOW_PERIOD)):
            if points:
                found[metric.owner] = max(points)[1]
        return found

    def queue_numbers(self, queues: list[Queue], *, days: int = 7, daily: bool = False) -> dict[str, QueueNumbers]:
        """
        Each queue's numbers over the last `days` whole UTC days (by ARN), with the age of its oldest message now,
        and a line per day when daily=True.
        """
        if not queues:
            return {}
        metrics = [m for q in queues for m in _queue_metrics(q)]
        numbers = self._totals(metrics, days, QueueNumbers)
        for arn, oldest in self._oldest_now(queues).items():
            numbers.setdefault(arn, QueueNumbers()).oldest_now_s = oldest
        if daily:
            for queue in queues:
                numbers.setdefault(queue.arn, QueueNumbers()).daily = self._daily(
                    _queue_metrics(queue), days, _DAILY_QUEUE)
        return {q.arn: numbers.get(q.arn, QueueNumbers()) for q in queues}

    def topic_numbers(self, topics: list[Topic], *, days: int = 7, daily: bool = False) -> dict[str, TopicNumbers]:
        """
        Each topic's numbers over the last `days` whole UTC days (by ARN), and a line per day when daily=True.
        """
        if not topics:
            return {}
        numbers = self._totals([m for t in topics for m in _topic_metrics(t)], days, TopicNumbers)
        if daily:
            for topic in topics:
                numbers.setdefault(topic.arn, TopicNumbers()).daily = self._daily(
                    _topic_metrics(topic), days, _DAILY_TOPIC)
        return {t.arn: numbers.get(t.arn, TopicNumbers()) for t in topics}

    def rule_numbers(self, rules: list[Rule], *, days: int = 7, daily: bool = False) -> dict[str, RuleNumbers]:
        """
        Each rule's numbers over the last `days` whole UTC days (by ARN), and a line per day when daily=True.
        """
        if not rules:
            return {}
        numbers = self._totals([m for r in rules for m in _rule_metrics(r)], days, RuleNumbers)
        if daily:
            for rule in rules:
                numbers.setdefault(rule.arn, RuleNumbers()).daily = self._daily(_rule_metrics(rule), days, _DAILY_RULE)
        return {r.arn: numbers.get(r.arn, RuleNumbers()) for r in rules}
