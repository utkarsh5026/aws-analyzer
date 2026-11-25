"""
What LambdaAnalyzer reads to say what happened to a function's failed events: what calls it, its asynchronous
settings, CloudWatch's counts of the events Lambda gave up on, and each SQS trigger's queue and dead-letter queue as
they are now. Nothing here receives a message: that would hide it from the function and count as one of its tries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code
from .._kit.fmt import _utcnow

from .models import AsyncConfig, AsyncCounts, EventFate, Function, QueueState, Trigger
from .analysis.fates import event_fates, parse_async_config, parse_redrive, queue_name
from .analysis.functions import parse_policy


_ASYNC_METRICS = (("received", "AsyncEventsReceived"), ("dropped", "AsyncEventsDropped"),
                  ("dead_letter_errors", "DeadLetterErrors"), ("destination_failures", "DestinationDeliveryFailures"))

_QUEUE_ATTRIBUTES = ["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "MessageRetentionPeriod",
                     "RedrivePolicy", "VisibilityTimeout"]

_OLDEST_LOOKBACK = timedelta(hours=1)

_FIVE_MINUTES = 300


def _queue_parts(arn: str) -> tuple[str, str, str]:
    """
    'arn:aws:sqs:us-east-1:123456789012:orders' -> ('us-east-1', '123456789012', 'orders').
    """
    parts = arn.split(":")
    if len(parts) < 6:
        raise ValueError(f"not an SQS queue ARN: {arn}")
    return parts[3], parts[4], parts[5]


def _queue_state(arn: str, attributes: dict[str, str]) -> QueueState:
    """
    GetQueueAttributes' answer -> QueueState (without its dead-letter queue or the oldest message's age).
    """
    dead_letter, receives = parse_redrive(attributes.get("RedrivePolicy"))
    return QueueState(
        queue_name(arn), arn,
        visible=int(attributes.get("ApproximateNumberOfMessages") or 0),
        in_flight=int(attributes.get("ApproximateNumberOfMessagesNotVisible") or 0),
        retention=int(attributes.get("MessageRetentionPeriod") or 4 * 86400),
        dead_letter_arn=dead_letter, max_receives=receives,
        visibility=int(attributes.get("VisibilityTimeout") or 30),
    )


def _reading(errors: dict[str, str], section: str, call: Callable[[], Any]) -> Any:
    """
    call(), or None with the error code in errors[section] when AWS refuses it.
    """
    try:
        return call()
    except ClientError as exc:
        errors[section] = _error_code(exc)
    except BotoCoreError as exc:
        errors[section] = type(exc).__name__
    return None


class _FateReads:
    """
    LambdaAnalyzer's reads for event_fates() (a mixin: it uses the analyzer's clients, policy(), event_sources() and
    _metric_data).
    """

    _service: Callable[..., Any]
    policy: Callable[..., dict[str, Any] | None]
    event_sources: Callable[..., dict[str, list[Trigger]]]
    _metric_data: Callable[..., dict[str, dict[datetime, float]]]

    def _callers(self, fn: Function, errors: dict[str, str]) -> list[Trigger]:
        """
        What calls the function: the queues and streams Lambda reads for it, and the callers its policy allows.
        """
        sources = _reading(errors, "triggers", lambda: self.event_sources(fn.region or None, function=fn.name)) or {}
        allowed = _reading(errors, "policy", lambda: parse_policy(self.policy(fn.name, fn.region or None))) or []
        return [t for found in sources.values() for t in found] + allowed

    def _async_config(self, fn: Function, errors: dict[str, str]) -> AsyncConfig | None:
        """
        The function's asynchronous settings (Lambda's defaults when it has none), or None when they couldn't be read.
        """
        client = self._service("lambda", fn.region or None)

        def read() -> dict[str, Any]:
            try:
                return client.get_function_event_invoke_config(FunctionName=fn.name)
            except ClientError as exc:
                if _error_code(exc) == "ResourceNotFoundException":
                    return {}
                raise

        return parse_async_config(_reading(errors, "async", read))

    def async_counts(self, fn: Function, *, since: datetime, until: datetime) -> AsyncCounts:
        """
        What CloudWatch counted of the function's asynchronous events from `since` to `until`: received, given up
        on, and not delivered to its dead-letter queue or on-failure destination.
        """
        period = 3600 if until - since <= timedelta(days=2) else 86400
        dimensions = [{"Name": "FunctionName", "Value": fn.name}]
        queries = [{"Id": f"q{i}", "MetricStat": {
            "Metric": {"Namespace": "AWS/Lambda", "MetricName": metric, "Dimensions": dimensions},
            "Period": period, "Stat": "Sum"}} for i, (_, metric) in enumerate(_ASYNC_METRICS)]
        series = self._metric_data(fn.region or None, queries, since, until)
        counts = AsyncCounts()
        for i, (attribute, _) in enumerate(_ASYNC_METRICS):
            setattr(counts, attribute, sum(series.get(f"q{i}", {}).values()))
        return counts

    def queue(self, arn: str) -> QueueState:
        """
        An SQS queue as it is now (GetQueueAttributes), with its dead-letter queue when it has one, and the age of
        each one's oldest message (CloudWatch's ApproximateAgeOfOldestMessage, the last value in the past hour).
        A queue that can't be read comes back with its error code in .error.
        """
        state = self._one_queue(arn)
        if state.dead_letter_arn and not state.error:
            state.dead_letter = self._one_queue(state.dead_letter_arn)
        self._add_oldest_ages([q for q in (state, state.dead_letter) if q is not None and not q.error])
        return state

    def _one_queue(self, arn: str) -> QueueState:
        region, account, name = _queue_parts(arn)
        sqs = self._service("sqs", region)
        try:
            url = sqs.get_queue_url(QueueName=name, QueueOwnerAWSAccountId=account)["QueueUrl"]
            attributes = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=_QUEUE_ATTRIBUTES).get("Attributes")
        except ClientError as exc:
            return QueueState(name, arn, error=_error_code(exc))
        except BotoCoreError as exc:
            return QueueState(name, arn, error=type(exc).__name__)
        return _queue_state(arn, attributes or {})

    def _add_oldest_ages(self, queues: list[QueueState]) -> None:
        """
        Sets each queue's oldest_age from CloudWatch, leaving it None when there's no value or it can't be read.
        """
        if not queues:
            return
        region = _queue_parts(queues[0].arn)[0]
        until = _utcnow()
        queries = [{"Id": f"o{i}", "MetricStat": {
            "Metric": {"Namespace": "AWS/SQS", "MetricName": "ApproximateAgeOfOldestMessage",
                       "Dimensions": [{"Name": "QueueName", "Value": q.name}]},
            "Period": _FIVE_MINUTES, "Stat": "Maximum"}} for i, q in enumerate(queues)]
        try:
            series = self._metric_data(region, queries, until - _OLDEST_LOOKBACK, until)
        except (ClientError, BotoCoreError):
            return
        for i, q in enumerate(queues):
            values = series.get(f"o{i}") or {}
            if values:
                q.oldest_age = values[max(values)]

    def _fates(self, fn: Function, since: datetime, until: datetime) -> tuple[list[EventFate], dict[str, str]]:
        """
        event_fates() for a function already read: what happened to its failed events from `since` to `until`, with
        section -> error code for what couldn't be read.
        """
        errors: dict[str, str] = {}
        triggers = self._callers(fn, errors)
        config = self._async_config(fn, errors)
        counts = _reading(errors, "async_metrics", lambda: self.async_counts(fn, since=since, until=until))
        queues = {t.arn: self.queue(t.arn) for t in triggers if t.kind == "SQS queue" and t.arn}
        fates = event_fates(fn, triggers, async_config=config, counts=counts, queues=queues)
        return fates, errors
