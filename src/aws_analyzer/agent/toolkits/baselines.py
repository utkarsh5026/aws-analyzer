"""
What normal looks like for a CloudWatch number, worked out so the model copies it rather than computes it: the hour
asked about against the same hour yesterday and the same hour last week, whether that's usual, since when it has
looked off, and the alarms set on it, in words.

Everything here is pure: no AWS calls. cloudwatch_reads.py reads the numbers and alarms, cloudwatch_tools.py hands
them to the model.

RESOURCE_KINDS is what the agent knows about each kind of resource: the namespace and dimension CloudWatch files its numbers
under, and the few numbers worth comparing (MetricSpec: the statistic, the unit people know, which way is worse and
the smallest change that counts). parse_resource() reads 'lambda:orders-etl', an ARN, an SQS queue URL or a raw
'AWS/EC2:InstanceId=i-0abc'; pick_metrics() reads 'p99 latency', 'errors' or a CloudWatch metric name.

compare() judges one number: higher than usual when it's over OFF_RATIO times the larger of yesterday's and last
week's and by at least the metric's floor, lower than usual the other way round (for numbers where a drop matters,
like traffic), else usual. off_since() walks the 5-minute points back from the end of the window for the run of
points that were off against the same points yesterday and last week, allowing GAP_POINTS quiet points inside it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..._kit.text import _clip
from ..results import _iso

OFF_RATIO = 1.5
FINE_SECONDS = 300
TREND_MINUTES = 360
GAP_POINTS = 1
MIN_RUN_POINTS = 2
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
UP, DOWN, BOTH = "up", "down", "both"
HIGHER, LOWER, USUAL, NO_DATA, NO_BASELINE = ("higher than usual", "lower than usual", "usual", "no data",
                                              "no baseline")
OFF = (HIGHER, LOWER)
_PERCENTILE_RE = re.compile(r"^p(\d{1,2}(?:\.\d+)?)$", re.I)
_STAT_WORDS = {"avg": "Average", "average": "Average", "mean": "Average", "max": "Maximum", "maximum": "Maximum",
               "peak": "Maximum", "min": "Minimum", "minimum": "Minimum", "sum": "Sum", "total": "Sum",
               "count": "SampleCount", "samplecount": "SampleCount"}
_OPERATORS = {"GreaterThanThreshold": ">", "GreaterThanOrEqualToThreshold": ">=", "LessThanThreshold": "<",
              "LessThanOrEqualToThreshold": "<=", "LessThanLowerOrGreaterThanUpperThreshold": "outside the band",
              "LessThanLowerThreshold": "under the band", "GreaterThanUpperThreshold": "over the band"}
_ALARM_NAME_CHARS = 120
_RAW_RE = re.compile(r"^(?P<namespace>[A-Za-z0-9_./#-]+?)[:\s]+(?P<dims>[^=\s]+=.+)$")


@dataclass(frozen=True)
class MetricSpec:
    """
    One number worth comparing: its short name (key), how a sentence names it (label), the CloudWatch metric (name)
    and statistic (stat: 'Sum', 'Maximum', 'p95'), the unit people know (unit) and what turns CloudWatch's value into
    it (scale: SageMaker's ModelLatency is in microseconds, 0.001 makes it ms), which way is worse (UP, DOWN or BOTH),
    the smallest change over the window that counts (floor, in unit), whether no data means none happened
    (missing_is_zero: Lambda publishes Errors only when it runs), and other words for it (aliases).
    """

    key: str
    label: str
    name: str
    stat: str
    unit: str = ""
    scale: float = 1.0
    worse: str = UP
    floor: float = 0.0
    missing_is_zero: bool = False
    aliases: tuple[str, ...] = ()

    @property
    def is_sum(self) -> bool:
        return self.stat in ("Sum", "SampleCount")

    def with_stat(self, stat: str) -> MetricSpec:
        """
        The same number under another statistic ('p99' for 'p95'), its label saying so.
        """
        if not stat or stat == self.stat:
            return self
        words = self.label.split(" ", 1)
        rest = words[1] if len(words) > 1 and _stat_of(words[0]) else self.label
        label = f"{stat} {rest}" if _PERCENTILE_RE.match(stat) else f"{stat.lower()} {rest}"
        return MetricSpec(self.key, label, self.name, stat, self.unit, self.scale, self.worse, self.floor,
                          self.missing_is_zero and stat in ("Sum", "SampleCount"), self.aliases)


@dataclass(frozen=True)
class ResourceKind:
    """
    A kind of resource the agent compares numbers for: its key ('lambda'), what it's called (title), the namespace
    CloudWatch files its numbers in, the dimension that names one (dimension), the dimensions found with ListMetrics
    beside it (found: SageMaker's VariantName), and its numbers, the first ones read when no metric is named.
    """

    key: str
    title: str
    namespace: str
    dimension: str
    metrics: tuple[MetricSpec, ...]
    found: tuple[str, ...] = ()


def _calls(key: str, name: str, *, floor: float = 20, aliases: tuple[str, ...] = ()) -> MetricSpec:
    return MetricSpec(key, key.replace("_", " "), name, "Sum", worse=BOTH, floor=floor, missing_is_zero=True,
                      aliases=aliases)


def _failures(key: str, name: str, *, floor: float = 3, aliases: tuple[str, ...] = ()) -> MetricSpec:
    return MetricSpec(key, key.replace("_", " "), name, "Sum", floor=floor, missing_is_zero=True, aliases=aliases)


def _timing(key: str, name: str, *, scale: float = 1.0, floor: float = 100,
            aliases: tuple[str, ...] = ()) -> MetricSpec:
    return MetricSpec(key, f"p95 {key.replace('_', ' ')}", name, "p95", "ms", scale, floor=floor, aliases=aliases)


_LATENCY_WORDS = ("latency", "duration", "response time", "slow", "time", "run time")
_REST_API = (_calls("requests", "Count", aliases=("calls", "count", "traffic")),
             _failures("server_errors", "5XXError", aliases=("5xx", "errors", "5xx errors")),
             _failures("client_errors", "4XXError", floor=10, aliases=("4xx", "4xx errors")),
             _timing("latency", "Latency", aliases=_LATENCY_WORDS))
_HTTP_API = (_calls("requests", "Count", aliases=("calls", "count", "traffic")),
             _failures("server_errors", "5xx", aliases=("5xx", "errors", "5xx errors")),
             _failures("client_errors", "4xx", floor=10, aliases=("4xx", "4xx errors")),
             _timing("latency", "Latency", aliases=_LATENCY_WORDS))
RESOURCE_KINDS: dict[str, ResourceKind] = {
    "lambda": ResourceKind("lambda", "Lambda function", "AWS/Lambda", "FunctionName", (
        _calls("invocations", "Invocations", aliases=("calls", "runs", "traffic", "requests")),
        _failures("errors", "Errors", aliases=("failures", "failed")),
        _timing("duration", "Duration", aliases=_LATENCY_WORDS),
        _failures("throttles", "Throttles", aliases=("throttled", "throttling")))),
    "sagemaker": ResourceKind("sagemaker", "SageMaker endpoint", "AWS/SageMaker", "EndpointName", (
        _calls("invocations", "Invocations", aliases=("calls", "requests", "traffic")),
        _timing("latency", "ModelLatency", scale=0.001, floor=50, aliases=(*_LATENCY_WORDS, "model latency")),
        _failures("server_errors", "Invocation5XXErrors", aliases=("5xx", "errors", "5xx errors")),
        _failures("client_errors", "Invocation4XXErrors", floor=10, aliases=("4xx", "4xx errors"))),
        found=("VariantName",)),
    "sqs": ResourceKind("sqs", "SQS queue", "AWS/SQS", "QueueName", (
        MetricSpec("waiting", "messages waiting", "ApproximateNumberOfMessagesVisible", "Maximum", floor=50,
                   aliases=("backlog", "visible", "depth", "queue depth", "messages")),
        MetricSpec("oldest_message", "age of the oldest message", "ApproximateAgeOfOldestMessage", "Maximum", "s",
                   floor=60, aliases=("age", "oldest", "lag", "delay")),
        _calls("sent", "NumberOfMessagesSent", aliases=("arrivals", "incoming", "traffic")))),
    "states": ResourceKind("states", "Step Functions state machine", "AWS/States", "StateMachineArn", (
        _calls("started", "ExecutionsStarted", floor=10, aliases=("executions", "runs", "traffic")),
        _failures("failed", "ExecutionsFailed", floor=2, aliases=("failures", "errors")),
        _failures("timed_out", "ExecutionsTimedOut", floor=1, aliases=("timeouts", "timed out")),
        _timing("execution_time", "ExecutionTime", floor=1000, aliases=_LATENCY_WORDS))),
    "dynamodb": ResourceKind("dynamodb", "DynamoDB table", "AWS/DynamoDB", "TableName", (
        _calls("read_units", "ConsumedReadCapacityUnits", floor=100, aliases=("reads", "read capacity", "rcu")),
        _calls("write_units", "ConsumedWriteCapacityUnits", floor=100, aliases=("writes", "write capacity", "wcu")),
        _failures("read_throttles", "ReadThrottleEvents", aliases=("throttles", "throttled reads")),
        _failures("write_throttles", "WriteThrottleEvents", aliases=("throttled writes",)))),
    "apigateway": ResourceKind("apigateway", "REST API", "AWS/ApiGateway", "ApiName", _REST_API),
    "apigateway_http": ResourceKind("apigateway_http", "HTTP API", "AWS/ApiGateway", "ApiId", _HTTP_API,
                                    found=("Stage",)),
}
_KIND_WORDS = {"lambda": "lambda", "function": "lambda", "fn": "lambda", "sagemaker": "sagemaker",
               "endpoint": "sagemaker", "sqs": "sqs", "queue": "sqs", "states": "states", "stepfunctions": "states",
               "step_functions": "states", "statemachine": "states", "state_machine": "states", "sfn": "states",
               "dynamodb": "dynamodb", "table": "dynamodb", "ddb": "dynamodb", "apigateway": "apigateway",
               "api": "apigateway", "apigw": "apigateway", "rest": "apigateway", "http": "apigateway_http",
               "httpapi": "apigateway_http", "websocket": "apigateway_http"}


def _stat_of(word: str) -> str:
    """
    A statistic from a word: 'p99' -> 'p99', 'avg' -> 'Average', 'max' -> 'Maximum'; '' for any other word.
    """
    text = word.strip().lower()
    if _PERCENTILE_RE.match(text):
        return text
    return _STAT_WORDS.get(text, "")


@dataclass(frozen=True)
class ResourceRef:
    """
    What a resource= names, before CloudWatch is asked: its kind ('lambda', or '' for a raw metric), its name
    (a function, endpoint, queue, state machine ARN, table or API), the region an ARN or URL names ('' for the
    agent's), and for a raw metric its namespace and dimensions.
    """

    kind: str
    name: str
    region: str = ""
    namespace: str = ""
    dimensions: tuple[tuple[str, str], ...] = ()

    @property
    def text(self) -> str:
        """
        How the tools write it back: 'lambda:orders-etl', or 'AWS/EC2:InstanceId=i-0abc'.
        """
        if self.kind:
            return f"{self.kind}:{self.name}"
        return f"{self.namespace}:" + ",".join(f"{k}={v}" for k, v in self.dimensions)


def _from_arn(text: str) -> ResourceRef | None:
    """
    A ResourceRef from an ARN of a kind RESOURCE_KINDS knows (a Lambda function, SageMaker endpoint, SQS queue, state machine
    or DynamoDB table), else None.
    """
    parts = text.split(":", 6)
    if len(parts) < 6 or parts[0] != "arn":
        return None
    service, region, rest = parts[2], parts[3], ":".join(parts[5:])
    if service == "lambda" and rest.startswith("function:"):
        return ResourceRef("lambda", rest.split(":", 1)[1], region)
    if service == "sagemaker" and rest.startswith("endpoint/"):
        return ResourceRef("sagemaker", rest.split("/", 1)[1], region)
    if service == "sqs":
        return ResourceRef("sqs", rest, region)
    if service == "states" and rest.startswith("stateMachine:"):
        return ResourceRef("states", text, region)
    if service == "dynamodb" and rest.startswith("table/"):
        return ResourceRef("dynamodb", rest.split("/")[1], region)
    return None


def _from_queue_url(text: str) -> ResourceRef | None:
    """
    A ResourceRef from an SQS queue URL ('https://sqs.us-east-1.amazonaws.com/123456789012/email-jobs'), else None.
    """
    found = re.match(r"^https://sqs\.([a-z0-9-]+)\.amazonaws\.com/\d+/([^/?#]+)", text)
    return ResourceRef("sqs", found.group(2), found.group(1)) if found else None


def _from_raw(text: str) -> ResourceRef | None:
    """
    A raw metric's resource: 'AWS/EC2:InstanceId=i-0abc' or 'AWS/EC2 InstanceId=i-0abc,AutoScalingGroupName=web',
    else None.
    """
    found = _RAW_RE.match(text)
    if not found or "/" not in found.group("namespace"):
        return None
    pairs = []
    for part in re.split(r"[,\s]+", found.group("dims").strip()):
        name, _, value = part.partition("=")
        if not name or not value:
            return None
        pairs.append((name, value))
    return ResourceRef("", "", namespace=found.group("namespace").rstrip(":"), dimensions=tuple(pairs))


def parse_resource(text: str) -> ResourceRef:
    """
    resource= as the tools take it: 'lambda:orders-etl' (or function:, endpoint:, queue:, table:, state_machine:,
    api: for a REST API by name, http: for an HTTP or WebSocket API by ID), an ARN, an SQS queue URL, or a raw
    'AWS/EC2:InstanceId=i-0abc'. A ValueError says what it takes.
    """
    value = str(text or "").strip()
    found = _from_arn(value) or _from_queue_url(value) or _from_raw(value)
    if found is not None:
        return found
    kind, sep, name = value.partition(":")
    if not sep:
        kind, sep, name = value.partition("/")
    key = _KIND_WORDS.get(kind.strip().lower().replace("-", "_").replace(" ", "_"))
    if not sep or key is None or not name.strip():
        raise ValueError(f"resource={value!r} isn't one I can read: write it as kind:name ('lambda:orders-etl', "
                         "'sagemaker:churn-endpoint', 'sqs:email-jobs', 'states:order-pipeline', 'dynamodb:orders', "
                         "'apigateway:orders-api', 'http:a1b2c3d4e5'), an ARN, or a raw metric's namespace and "
                         "dimensions ('AWS/EC2:InstanceId=i-0abc').")
    return ResourceRef(key, name.strip())


def _words(text: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", text).lower().split())


def _matches(spec: MetricSpec, wanted: str) -> bool:
    names = (spec.key, spec.label, spec.name, *spec.aliases)
    return wanted in {_words(name) for name in names} or wanted == _words(spec.label.split(" ", 1)[-1])


def pick_metrics(kind: ResourceKind | None, text: str) -> list[MetricSpec]:
    """
    The numbers metric= names: '' for the kind's own (RESOURCE_KINDS), else one read from words like 'p99 latency',
    'errors', 'max backlog' or a CloudWatch metric name ('ConcurrentExecutions', 'CPUUtilization'), its statistic
    taken from the words (a percentile, avg, max, min, sum) or the metric's own (Average for one RESOURCE_KINDS doesn't
    know). A raw metric (kind None) needs metric=.
    """
    words = str(text or "").strip().split()
    stats = [_stat_of(word) for word in words]
    stat = next((s for s in stats if s), "")
    rest = " ".join(word for word, s in zip(words, stats) if not s)
    if not rest:
        if kind is None:
            raise ValueError("A raw metric needs metric= too: its CloudWatch name, like 'CPUUtilization'.")
        return [spec.with_stat(stat) for spec in kind.metrics]
    wanted = _words(rest)
    known = [spec for spec in (kind.metrics if kind else ()) if _matches(spec, wanted)]
    if known:
        return [known[0].with_stat(stat)]
    name = rest.replace(" ", "")
    return [MetricSpec(_words(name).replace(" ", "_"), f"{(stat or 'average').lower()} {name}", name,
                       stat or "Average")]


@dataclass(frozen=True)
class Series:
    """
    One number as CloudWatch gave it for one window: its value over each period (period_values, oldest first, the
    last one the window asked about) and its 5-minute points (points), both by start time, already in the spec's
    unit.
    """

    period_values: dict[datetime, float] = field(default_factory=dict)
    points: dict[datetime, float] = field(default_factory=dict)

    def value_at(self, start: datetime, spec: MetricSpec) -> float | None:
        found = self.period_values.get(start)
        if found is None and spec.missing_is_zero:
            return 0.0
        return found

    def point_at(self, start: datetime, spec: MetricSpec) -> float | None:
        found = self.points.get(start)
        if found is None and spec.missing_is_zero:
            return 0.0
        return found


@dataclass(frozen=True)
class Window:
    """
    The time a comparison covers: the period asked about (`start` to `end`, `minutes` long), and the earlier
    periods read before it for the trend (`trend_start` on), all in UTC; yesterday's and last week's are the same
    times a day and a week back.
    """

    end: datetime
    minutes: int
    trend_start: datetime

    @property
    def start(self) -> datetime:
        return self.end - timedelta(minutes=self.minutes)

    def period_starts(self) -> list[datetime]:
        count = round((self.end - self.trend_start).total_seconds() / (self.minutes * 60))
        return [self.trend_start + timedelta(minutes=self.minutes * i) for i in range(count)]

    def point_starts(self) -> list[datetime]:
        count = round((self.end - self.trend_start).total_seconds() / FINE_SECONDS)
        return [self.trend_start + timedelta(seconds=FINE_SECONDS * i) for i in range(count)]


def make_window(end: datetime, minutes: int) -> Window:
    """
    The window ending at `end` (rounded down to 5 minutes, in UTC) that's `minutes` long (rounded to 5, between 15
    and 360), with whole periods before it back to TREND_MINUTES for the trend and off_since().
    """
    minutes = max(15, min(360, 5 * round(int(minutes or 60) / 5)))
    end = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    periods = max(1, math.ceil(TREND_MINUTES / minutes))
    return Window(end, minutes, end - timedelta(minutes=minutes * periods))


@dataclass(frozen=True)
class Alarm:
    """
    A CloudWatch alarm, in the terms people read: its name, state ('ALARM', 'OK', 'INSUFFICIENT_DATA') and since
    when, what it watches (namespace, metric, dimensions), its rule in words (rule), whether its actions are on and
    whether anyone is told when it fires (notifies), and its threshold in the unit the comparison uses (threshold).
    """

    name: str
    state: str
    since: datetime | None
    namespace: str
    metric: str
    dimensions: tuple[tuple[str, str], ...]
    rule: str
    actions_enabled: bool
    notifies: bool
    threshold: float | None = None
    reason: str = ""


def _number(value: float | None) -> str:
    """
    A value as a sentence writes it: 1,840 / 4.5 / 0.25.
    """
    if value is None:
        return "-"
    rounded = rounded_value(value)
    if float(rounded).is_integer():
        return f"{int(rounded):,}"
    return f"{rounded:,}"


def rounded_value(value: float) -> float:
    """
    A value rounded the way the tools hand it over: whole above 100, one decimal above 1, three significant digits
    below; the sentence and the numbers agree.
    """
    size = abs(value)
    if size >= 100:
        return float(round(value))
    if size >= 1:
        return round(value, 1)
    if size == 0:
        return 0.0
    return round(value, 2 - int(math.floor(math.log10(size))))


def amount(value: float | None, unit: str) -> str:
    """
    A value with its unit: '1,840 ms', '35', '12 s'.
    """
    return f"{_number(value)} {unit}".strip()


def _period_words(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "an hour" if hours == 1 else f"{hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return "a minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


def alarm_rule(raw: dict[str, Any], *, unit: str = "", scale: float = 1.0) -> str:
    """
    An alarm's rule as a sentence says it: 'p95 > 1,000 ms for 3 of 3 periods of 5 minutes'. scale and unit turn its
    threshold into the comparison's unit; a metric math alarm says so.
    """
    stat = raw.get("ExtendedStatistic") or raw.get("Statistic") or ""
    operator = _OPERATORS.get(raw.get("ComparisonOperator", ""), raw.get("ComparisonOperator", "?"))
    if raw.get("Metrics"):
        return f"a metric math expression {operator} {_number(raw.get('Threshold'))}"
    threshold = raw.get("Threshold")
    shown = amount(threshold * scale, unit) if threshold is not None else (raw.get("ThresholdMetricId") or "a band")
    periods = raw.get("EvaluationPeriods") or 1
    breaching = raw.get("DatapointsToAlarm") or periods
    period = _period_words(int(raw.get("Period") or 60))
    return f"{stat} {operator} {shown} for {breaching} of {periods} periods of {period}".strip()


def parse_alarm(raw: dict[str, Any], *, unit: str = "", scale: float = 1.0) -> Alarm:
    """
    An Alarm from what DescribeAlarms / DescribeAlarmsForMetric return (a MetricAlarm, or a CompositeAlarm, which
    watches other alarms), its threshold put in the comparison's unit with `scale`.
    """
    dims = tuple((d.get("Name", ""), d.get("Value", "")) for d in raw.get("Dimensions") or [])
    actions = bool(raw.get("AlarmActions"))
    is_composite = "AlarmRule" in raw
    rule = f"composite: {_clip(raw.get('AlarmRule', ''), 200)}" if is_composite else alarm_rule(raw, unit=unit,
                                                                                                scale=scale)
    threshold = raw.get("Threshold")
    return Alarm(name=_clip(raw.get("AlarmName", ""), _ALARM_NAME_CHARS), state=raw.get("StateValue", ""),
                 since=raw.get("StateUpdatedTimestamp"), namespace=raw.get("Namespace", ""),
                 metric=raw.get("MetricName", ""), dimensions=dims, rule=rule,
                 actions_enabled=bool(raw.get("ActionsEnabled", True)), notifies=actions,
                 threshold=threshold * scale if threshold is not None else None,
                 reason=_clip(raw.get("StateReason", ""), 300))


def resource_of(alarm: Alarm) -> str:
    """
    The resource= compare_metric takes for what an alarm watches: 'lambda:orders-etl' when RESOURCE_KINDS knows its
    namespace and dimension, else the raw 'AWS/EC2:InstanceId=i-0abc'; '' for a composite or metric math alarm.
    """
    if not alarm.metric:
        return ""
    dims = dict(alarm.dimensions)
    for kind in RESOURCE_KINDS.values():
        if kind.namespace == alarm.namespace and kind.dimension in dims:
            return f"{kind.key}:{dims[kind.dimension]}"
    if not dims:
        return ""
    return f"{alarm.namespace}:" + ",".join(f"{k}={v}" for k, v in alarm.dimensions)


@dataclass
class Comparison:
    """
    One number compared: the metric (spec), the label a sentence uses, the dimensions it was read with, the value
    over the window (now) and the same window yesterday and last week, how it looks (HIGHER, LOWER, USUAL, NO_DATA or
    NO_BASELINE), since when it has looked off (off_since, or off_before: it already was at the start of the trend),
    each earlier period beside its baselines (trend), and the alarms set on it.
    """

    spec: MetricSpec
    label: str
    dimensions: tuple[tuple[str, str], ...]
    now: float | None
    yesterday: float | None
    last_week: float | None
    looks: str
    off_since: datetime | None = None
    off_before: datetime | None = None
    trend: list[dict[str, Any]] = field(default_factory=list)
    alarms: list[Alarm] = field(default_factory=list)

    @property
    def is_off(self) -> bool:
        return self.looks in OFF


def ratio(now: float | None, base: float | None) -> float | None:
    """
    now / base rounded to one decimal (whole from 10 up), so a sentence says '4.5×'; None without a base above 0.
    """
    if now is None or not base:
        return None
    times = now / base
    return float(round(times)) if times >= 10 else round(times, 1)


def judge(spec: MetricSpec, now: float | None, bases: list[float | None], *, floor: float | None = None) -> str:
    """
    How `now` looks against its baselines (yesterday's and last week's): HIGHER when it's over OFF_RATIO times the
    larger and by at least the floor, LOWER when it's under the smaller divided by OFF_RATIO by at least the floor
    (for numbers where a drop matters), USUAL otherwise; NO_DATA without `now`, NO_BASELINE without either base.
    """
    known = [b for b in bases if b is not None]
    if now is None:
        return NO_DATA
    if not known:
        return NO_BASELINE
    least = spec.floor if floor is None else floor
    high, low = max(known), min(known)
    if spec.worse in (UP, BOTH) and now > high * OFF_RATIO and now - high >= least:
        return HIGHER
    if spec.worse in (DOWN, BOTH) and now < low / OFF_RATIO and low - now >= least:
        return LOWER
    return USUAL


def _point_floor(spec: MetricSpec, minutes: int) -> float:
    """
    The floor for one 5-minute point: a sum's is its share of the window's, any other statistic's is the same.
    """
    if spec.is_sum:
        return spec.floor * FINE_SECONDS / (minutes * 60)
    return spec.floor


def _point_states(spec: MetricSpec, window: Window, reads: tuple[Series, Series, Series], looks: str) -> list[str]:
    """
    Each 5-minute point of the trend, oldest first: 'off' when it looks the way the window does (higher or lower)
    against the same point yesterday and last week, 'ok' when it doesn't, '' when there's nothing to tell.
    """
    now, yesterday, last_week = reads
    least = _point_floor(spec, window.minutes)
    states = []
    for start in window.point_starts():
        value = now.point_at(start, spec)
        bases = [yesterday.point_at(start - DAY, spec), last_week.point_at(start - WEEK, spec)]
        verdict = judge(spec, value, bases, floor=least)
        states.append("off" if verdict == looks else ("ok" if verdict in (USUAL, *OFF) else ""))
    return states


def off_since(spec: MetricSpec, window: Window, reads: tuple[Series, Series, Series],
              looks: str) -> tuple[datetime | None, bool]:
    """
    When the number started looking the way it does (`looks`, HIGHER or LOWER), from its 5-minute points: the start
    of the run of points that were off, allowing GAP_POINTS quiet points inside it, that reaches into the window
    asked about, at least MIN_RUN_POINTS long. (start, True) when the run goes back to the trend's start, so it was
    already off then; (None, False) when there's no such run (the number is off as a whole, not since a moment).
    """
    states = _point_states(spec, window, reads, looks)
    starts = window.point_starts()
    first_in_window = len(starts) - round(window.minutes * 60 / FINE_SECONDS)
    last_off = max((i for i, state in enumerate(states) if state == "off"), default=-1)
    if last_off < first_in_window:
        return None, False
    begin, gap = last_off, 0
    for i in range(last_off - 1, -1, -1):
        if states[i] == "off":
            begin, gap = i, 0
        else:
            gap += 1
            if gap > GAP_POINTS:
                break
    off_points = sum(1 for state in states[begin:last_off + 1] if state == "off")
    if off_points < MIN_RUN_POINTS:
        return None, False
    return starts[begin], begin == 0


def _trend(spec: MetricSpec, window: Window, reads: tuple[Series, Series, Series]) -> list[dict[str, Any]]:
    """
    Each period of the trend before the one asked about, oldest first: when it started, its value and the same
    period yesterday and last week.
    """
    now, yesterday, last_week = reads
    rows = []
    for start in window.period_starts()[:-1]:
        rows.append({"from": start, "now": _kept(now.value_at(start, spec)),
                     "yesterday": _kept(yesterday.value_at(start - DAY, spec)),
                     "last_week": _kept(last_week.value_at(start - WEEK, spec))})
    return rows


def _kept(value: float | None) -> float | None:
    return rounded_value(value) if value is not None else None


def compare(spec: MetricSpec, window: Window, reads: tuple[Series, Series, Series], *, label: str = "",
            dimensions: tuple[tuple[str, str], ...] = ()) -> Comparison:
    """
    One number over the window against the same window yesterday and last week (reads: now's, yesterday's and last
    week's Series), how that looks, since when it has looked off, and the trend before it.
    """
    now, yesterday, last_week = reads
    start = window.start
    values = (now.value_at(start, spec), yesterday.value_at(start - DAY, spec), last_week.value_at(start - WEEK, spec))
    kept = [_kept(v) for v in values]
    looks = judge(spec, kept[0], kept[1:])
    found = Comparison(spec, label or spec.label, dimensions, kept[0], kept[1], kept[2], looks,
                       trend=_trend(spec, window, reads))
    if found.is_off:
        since, before = off_since(spec, window, reads, looks)
        found.off_since = None if before else since
        found.off_before = since if before else None
    return found


def _times(value: float | None, base: float | None) -> str:
    times = ratio(value, base)
    if times is not None:
        return f"{times:g}×"
    return "from 0" if base == 0 and value else ""


def _against(found: Comparison) -> str:
    """
    'against 410 ms the same hour last week (4.5×) and 395 ms yesterday (4.7×)'; the bases there are, last week
    first, since a week back is the same day of the week.
    """
    unit = found.spec.unit
    parts = []
    for base, when in ((found.last_week, "last week"), (found.yesterday, "yesterday")):
        if base is None:
            continue
        times = _times(found.now, base)
        parts.append(f"{amount(base, unit)} {when}" + (f" ({times})" if times else ""))
    return "against " + " and ".join(parts) if parts else ""


def _alarm_words(alarms: list[Alarm]) -> str:
    """
    What a sentence says about the alarms on a number: none, the one firing, or how many there are and their state.
    """
    if not alarms:
        return "No alarm is set on it."
    firing = [a for a in alarms if a.state == "ALARM"]
    if firing:
        alarm = firing[0]
        since = f" since {_iso(alarm.since)}" if alarm.since else ""
        told = "" if alarm.notifies and alarm.actions_enabled else ", but it notifies no one"
        return f"Alarm '{alarm.name}' ({alarm.rule}) is in ALARM{since}{told}."
    if len(alarms) == 1:
        alarm = alarms[0]
        return f"Alarm '{alarm.name}' ({alarm.rule}) is set on it and is {alarm.state or 'unknown'}."
    states = sorted({a.state for a in alarms})
    return f"{len(alarms)} alarms are set on it, all {' or '.join(states)}."


def _window_words(window: Window) -> str:
    span = "hour" if window.minutes == 60 else f"{window.minutes} minutes"
    return f"in the {span} to {_iso(window.end)}"


def says(found: Comparison, name: str, window: Window) -> str:
    """
    The comparison as one sentence the model can copy: 'p95 latency for churn-endpoint in the hour to
    2026-11-09T03:00Z: 1,840 ms against 410 ms last week (4.5×) and 395 ms yesterday (4.7×), higher than usual
    since 2026-11-09T02:10Z. No alarm is set on it.'
    """
    head = f"{found.label} for {name} {_window_words(window)}"
    if found.looks == NO_DATA:
        return f"{head}: CloudWatch has no data. {_alarm_words(found.alarms)}"
    value = amount(found.now, found.spec.unit)
    if found.looks == NO_BASELINE:
        return (f"{head}: {value}, with no data for the same time yesterday or last week to compare it with. "
                f"{_alarm_words(found.alarms)}")
    verdict = "about usual" if found.looks == USUAL else found.looks
    if found.off_since:
        verdict += f" since {_iso(found.off_since)}"
    elif found.off_before:
        verdict += f" since before {_iso(found.off_before)}, at least"
    return f"{head}: {value} {_against(found)}, {verdict}. {_alarm_words(found.alarms)}"


def _dimension_flags(dimensions: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"Name={name},Value={value}" for name, value in dimensions)


def _suggested_threshold(found: Comparison) -> float:
    """
    A threshold that would have caught this, in CloudWatch's unit: twice the usual (half for a drop), or the floor
    when the usual is 0.
    """
    bases = [b for b in (found.yesterday, found.last_week) if b is not None]
    spec = found.spec
    if found.looks == LOWER:
        usual = min(bases) if bases else 0.0
        return rounded_value(usual / 2 / spec.scale)
    usual = max(bases) if bases else 0.0
    wanted = usual * 2 if usual else spec.floor
    return rounded_value(max(wanted, spec.floor) / spec.scale)


def alarm_command(found: Comparison, *, namespace: str, name: str, region: str = "") -> str:
    """
    The `aws cloudwatch put-metric-alarm` command a finding shows for a number that's off with no alarm on it: the
    metric as the comparison read it, over 5-minute periods, 3 of 3 breaching, at a threshold that would have caught
    this (_suggested_threshold), missing data not breaching. It's shown, never run.
    """
    spec = found.spec
    stat_flag = f"--extended-statistic {spec.stat}" if _PERCENTILE_RE.match(spec.stat) else f"--statistic {spec.stat}"
    operator = "LessThanThreshold" if found.looks == LOWER else "GreaterThanThreshold"
    alarm_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{name}-{spec.key}")[:_ALARM_NAME_CHARS]
    command = (f"aws cloudwatch put-metric-alarm --alarm-name {alarm_name} --namespace {namespace} "
               f"--metric-name {spec.name} --dimensions {_dimension_flags(found.dimensions)} {stat_flag} "
               f"--period {FINE_SECONDS} --evaluation-periods 3 --datapoints-to-alarm 3 "
               f"--threshold {_number(_suggested_threshold(found)).replace(',', '')} "
               f"--comparison-operator {operator} --treat-missing-data notBreaching")
    return f"{command} --region {region}" if region else command


def _off_finding(found: Comparison, *, namespace: str, name: str, region: str) -> tuple[str, str] | None:
    """
    What a number that's off says needs doing: an alarm, when none is set on it; a lower threshold, when the one set
    on it stays OK through this; nothing when an alarm caught it and tells someone.
    """
    if not found.alarms:
        return ("warn", f"{found.label} for {name} is {found.looks} with no alarm on it: nobody is told when it "
                        "does this. An alarm that would have caught it (add --alarm-actions with an SNS topic to be "
                        f"told): {alarm_command(found, namespace=namespace, name=name, region=region)}")
    if any(a.state == "ALARM" for a in found.alarms):
        return None
    quiet = found.alarms[0]
    return ("warn", f"{found.label} for {name} is {found.looks} but alarm '{quiet.name}' stays {quiet.state}: its "
                    f"rule ({quiet.rule}) doesn't catch this. Check its threshold and statistic against "
                    f"{amount(found.now, found.spec.unit)} now.")


def _alarm_findings(alarms: list[Alarm], name: str) -> list[tuple[str, str]]:
    """
    Alarms that fire but tell no one: in ALARM with no actions, or with their actions turned off.
    """
    found = []
    for alarm in alarms:
        if alarm.state != "ALARM":
            continue
        since = f" since {_iso(alarm.since)}" if alarm.since else ""
        if not alarm.notifies:
            found.append(("warn", f"Alarm '{alarm.name}' on {name} is in ALARM{since} but has no alarm actions: "
                                  "it fires and nobody hears. Add an SNS topic to it (--alarm-actions)."))
        elif not alarm.actions_enabled:
            found.append(("warn", f"Alarm '{alarm.name}' on {name} is in ALARM{since} with its actions turned "
                                  f"off: aws cloudwatch enable-alarm-actions --alarm-names {alarm.name}"))
    return found


def comparison_findings(found: list[Comparison], *, namespace: str, name: str,
                        region: str = "") -> list[tuple[str, str]]:
    """
    What the comparisons say needs attention, as ('warn' | 'info', message) pairs: each number that's off with no
    alarm to catch it (and the put-metric-alarm command that would), or an alarm that stays quiet through it; alarms
    that fire and tell no one; and an info when every number is usual.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in found:
        if item.is_off:
            finding = _off_finding(item, namespace=namespace, name=name, region=region)
            if finding:
                out.append(finding)
        for alarm_finding in _alarm_findings([a for a in item.alarms if a.name not in seen], name):
            out.append(alarm_finding)
        seen |= {a.name for a in item.alarms}
    judged = [item for item in found if item.looks not in (NO_DATA, NO_BASELINE)]
    if judged and not any(item.is_off for item in found):
        out.append(("info", f"Every number read for {name} is within its usual range: under {OFF_RATIO:g}× the "
                            "same time yesterday and last week."))
    return out


def alarm_list_findings(alarms: list[Alarm], now: datetime) -> list[tuple[str, str]]:
    """
    What a list of alarms says needs attention: each one in ALARM (with what it watches, so compare_metric can say
    how far off it is), the ones in ALARM that tell no one, and the ones without data for over a week, which may
    watch something that's gone.
    """
    out: list[tuple[str, str]] = []
    for alarm in alarms:
        if alarm.state != "ALARM":
            continue
        since = f" since {_iso(alarm.since)}" if alarm.since else ""
        watched = resource_of(alarm) or "its metrics"
        out.append(("warn", f"Alarm '{alarm.name}' is in ALARM{since}: {alarm.rule}, on {watched}. See how far "
                            f"off it is with compare_metric('{watched}')."))
        out.extend(_alarm_findings([alarm], watched))
    stale = [a for a in alarms if a.state == "INSUFFICIENT_DATA" and a.since and now - a.since > WEEK]
    if stale:
        names = " ".join(a.name for a in stale[:5])
        have = "alarms have" if len(stale) > 1 else "alarm has"
        out.append(("info", f"{len(stale)} {have} had no data for over a week ({names}): what they watch may be "
                            f"gone. Delete them if so: aws cloudwatch delete-alarms --alarm-names {names}"))
    return out
