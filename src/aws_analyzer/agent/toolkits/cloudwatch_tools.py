"""
The CloudWatch toolkit: what normal looks like for a number. compare_metric reads a resource's numbers (a Lambda
function's errors and p95 duration, a SageMaker endpoint's p95 latency, a queue's backlog, ...) over the hour asked
about and the same hour yesterday and last week, says whether that's usual, since when it has looked off, and reads
the alarms set on each number; find_alarms lists the region's alarms with what each watches and since when it's in
its state. The tools work the comparison out, ratios and times included, and hand the model a sentence to copy
(`says`), so it never does the maths itself.

It reads through MetricReader (cloudwatch_reads.py), not one of the package's analyzers, since no service owns
CloudWatch's numbers; baselines.py has the catalogue of numbers and the maths, pure. Nothing here creates, changes
or deletes an alarm: a finding shows the `aws cloudwatch put-metric-alarm` command that would set one.

summarize_comparisons and summarize_alarms are pure: they run on any list of Comparison or Alarm.
CLOUDWATCH_TOOL_READS is what each tool reads (its IAM permissions), for tools() and README. A list shows at most
_LISTED rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..._kit.fmt import _plural, parse_time
from ..budget import Budget
from ..results import _window, model_findings
from .base import Toolkit
from .baselines import (
    DAY, NO_BASELINE, NO_DATA, OFF, OFF_RATIO, WEEK, Alarm, Comparison, Window, alarm_list_findings, comparison_findings,
    compare, make_window, parse_resource, pick_metrics, ratio, resource_of, says,
)
from .cloudwatch_reads import MetricRead, MetricReader, NoMetrics, Resource

_METRICS = "cloudwatch:GetMetricData"
CLOUDWATCH_TOOL_READS: dict[str, tuple[str, ...]] = {
    "compare_metric": (_METRICS, "cloudwatch:ListMetrics", "cloudwatch:DescribeAlarmsForMetric"),
    "find_alarms": ("cloudwatch:DescribeAlarms",),
}
_LISTED = 50
_QUERIES_PER_NUMBER = 6
_STATES = {"alarm": "ALARM", "alarms": "ALARM", "firing": "ALARM", "ok": "OK", "insufficient_data": "INSUFFICIENT_DATA",
           "insufficient": "INSUFFICIENT_DATA", "no_data": "INSUFFICIENT_DATA", "all": "", "any": "", "": ""}


def _alarm_row(alarm: Alarm) -> dict[str, Any]:
    """
    One alarm as the model reads it: its name, state and since when, its rule in words, what it watches (as
    compare_metric's resource=), whether it tells anyone, and why it's in its state.
    """
    row: dict[str, Any] = {"name": alarm.name, "state": alarm.state, "since": alarm.since, "rule": alarm.rule,
                           "watches": resource_of(alarm) or None, "metric": alarm.metric or None,
                           "notifies": alarm.notifies, "actions_enabled": alarm.actions_enabled,
                           "reason": alarm.reason or None}
    return {key: value for key, value in row.items() if value is not None}


def _metric_row(found: Comparison, name: str, window: Window) -> dict[str, Any]:
    """
    One number compared, as the model reads it: its value over the window and the same window yesterday and last
    week, the ratios, how it looks, since when, the sentence to copy, the alarms on it, and for one that's off (or has
    nothing to compare with) the periods before it.
    """
    row: dict[str, Any] = {
        "metric": found.label, "cloudwatch": f"{found.spec.name} {found.spec.stat}", "unit": found.spec.unit or None,
        "dimensions": dict(found.dimensions), "now": found.now, "yesterday": found.yesterday,
        "last_week": found.last_week, "vs_yesterday_x": ratio(found.now, found.yesterday),
        "vs_last_week_x": ratio(found.now, found.last_week), "looks": found.looks, "off_since": found.off_since,
        "off_since_before": found.off_before, "says": says(found, name, window),
        "alarms": [_alarm_row(a) for a in found.alarms],
        "earlier_periods": found.trend if found.is_off or found.looks == NO_BASELINE else None,
    }
    return {key: value for key, value in row.items() if value is not None}


def summarize_comparisons(found: list[Comparison], *, name: str, namespace: str, window: Window,
                          region: str = "") -> dict[str, Any]:
    """
    compare_metric's answer: each number over the window against the same window yesterday and last week, the ones
    that look off first, with the sentence to copy for each (`says`), the alarms on it, the periods before it, and
    the findings (a number that's off with no alarm to catch it, and the command that would set one).
    """
    ordered = sorted(found, key=lambda item: (not item.is_off, item.looks in (NO_DATA, NO_BASELINE)))
    return {
        "resource": name, "namespace": namespace, "region": region or None,
        "window": _window(window.start, window.end),
        "compared_with": {"yesterday": _window(window.start - DAY, window.end - DAY),
                          "last_week": _window(window.start - WEEK, window.end - WEEK)},
        "off": [item.label for item in ordered if item.is_off],
        "metrics": [_metric_row(item, name, window) for item in ordered],
        "findings": model_findings(comparison_findings(ordered, namespace=namespace, name=name, region=region)),
        "note": (f"Off means over {OFF_RATIO:g}× (or under 1/{OFF_RATIO:g} of) both the same time yesterday and last week, by "
                 "a margin that matters for that number. Alarms on a metric math expression aren't matched."),
    }


def summarize_alarms(alarms: list[Alarm], *, state: str, region: str, cut_short: bool = False,
                     now: datetime | None = None) -> dict[str, Any]:
    """
    find_alarms' answer: the region's alarms, the ones in ALARM first (newest change first), how many are in each
    state, and the findings (alarms firing, ones that tell no one, ones without data for over a week).
    """
    now = now or datetime.now(timezone.utc)
    order = {"ALARM": 0, "INSUFFICIENT_DATA": 1, "OK": 2}
    ordered = sorted(alarms, key=lambda a: (order.get(a.state, 3), -(a.since.timestamp() if a.since else 0)))
    counts: dict[str, int] = {}
    for alarm in alarms:
        counts[alarm.state] = counts.get(alarm.state, 0) + 1
    out: dict[str, Any] = {"region": region, "state": state or "any", "found": len(alarms), "states": counts,
                           "alarms": [_alarm_row(a) for a in ordered[:_LISTED]], "cut_short": cut_short,
                           "findings": model_findings(alarm_list_findings(ordered, now))}
    if len(alarms) > _LISTED:
        out["more"] = f"{len(alarms) - _LISTED:,} more not listed: narrow with state= or prefix="
    return out


def _state_of(state: str) -> str:
    """
    state= as DescribeAlarms takes it: 'ALARM', 'OK', 'INSUFFICIENT_DATA' or '' for any, forgiving about case and
    words ('firing', 'all').
    """
    text = str(state or "").strip().lower().replace(" ", "_")
    if text in _STATES:
        return _STATES[text]
    if text.upper() in ("ALARM", "OK", "INSUFFICIENT_DATA"):
        return text.upper()
    raise ValueError(f"state={state!r}: use 'ALARM', 'OK', 'INSUFFICIENT_DATA' or '' for every alarm")


def _metric_reads(resource: Resource, metric: str) -> list[MetricRead]:
    """
    The numbers to read for metric= ('' for the kind's own), once per dimension set (a SageMaker endpoint's
    variants), each labelled for its sentence.
    """
    reads = []
    for spec in pick_metrics(resource.kind, metric):
        for i, dims in enumerate(resource.dimension_sets):
            label = spec.label + (f" ({resource.set_labels[i]})" if resource.set_labels else "")
            reads.append(MetricRead(spec, dims, label))
    return reads


def _end_of(at: str) -> datetime:
    """
    at= as the end of the window: now when it's '', else an ISO UTC time or a relative one ('3h': three hours ago).
    """
    text = str(at or "").strip()
    moment = parse_time(text) if text else None
    return moment or datetime.now(timezone.utc)


class CloudWatchToolkit(Toolkit):
    """
    The agent's tools for what normal looks like in Amazon CloudWatch, reading through MetricReader: `analyzer` is one
    to use (else one is made on the agent's session, in its region). The reader makes a client per region through
    cloudwatch(), which the guard hooks.
    """

    name = "cloudwatch"
    title = "CloudWatch"
    reads = CLOUDWATCH_TOOL_READS
    service = "cloudwatch"
    client_factory = "cloudwatch"
    privacy = "CloudWatch: numbers and alarm settings only; no log lines."
    covers = ("Amazon CloudWatch: whether a number is normal (a Lambda function's errors or run time, a SageMaker "
              "endpoint's latency, a queue's backlog, a state machine's failures, an API's 5xx, a table's "
              "throttles, or any metric) against the same hour yesterday and last week, since when it has looked "
              "off, and the alarms set on it.")

    def make_analyzer(self, session: Any) -> MetricReader:
        return MetricReader(session)

    @property
    def analyzer(self) -> MetricReader:
        """
        The MetricReader the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        return f"""\
Amazon CloudWatch: what normal looks like
- A number alone doesn't say whether it's unusual. Before you call a number high, low, slow, a spike or a drop, and
  whenever the user asks whether something is normal or since when it's been off, call compare_metric: it compares
  the last hour with the same hour yesterday and last week, says whether that's usual and since when it has looked
  off, and reads the alarms set on it. It reads {self._region_text()} unless region= names another.
- Copy each metric's `says` sentence, its numbers, ratios (vs_last_week_x, vs_yesterday_x) and off_since rather
  than comparing numbers yourself; give its times in the user's time zone. "No alarm is set on it" is a finding too.
- resource= is kind:name: lambda:orders-etl, sagemaker:churn-endpoint, sqs:email-jobs, states:order-pipeline,
  dynamodb:orders, apigateway:orders-api (a REST API by name), http:a1b2c3d4e5 (an HTTP or WebSocket API by ID),
  or an ARN. Any other metric: its namespace and dimensions ('AWS/EC2:InstanceId=i-0abc') with metric=
  ('CPUUtilization'). metric= picks one number ('p99 latency', 'errors'); '' compares the kind's main ones.
- at= is the end of the window for a question about earlier ('2026-11-09T03:00Z'); minutes= its length.
- For "is anything alarming" or "what alarms do I have", find_alarms lists them with what each watches, which
  compare_metric takes as resource=.
- Nothing here sets or changes an alarm: give the user the put-metric-alarm command the findings show."""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        compare_metric and find_alarms: methods whose type hints and docstrings tell the model how to call them, each
        reading CloudWatch and returning a compact, JSON-ready summary.
        """
        return [self.compare_metric, self.find_alarms]

    def _missing(self, exc: NoMetrics, resource: str, region: str) -> dict[str, Any]:
        return {"resource": resource, "regions": [region], "error": str(exc), "close_names": exc.close,
                "hint": "Use one of close_names, check the kind (lambda:, sagemaker:, ...) or try another region."}

    def compare_metric(self, resource: str, metric: str = "", at: str = "", minutes: int = 60,
                       region: str = "") -> dict[str, Any]:
        """
        Whether a resource's numbers are normal: each one over the window (the last hour by default) against the
        same window yesterday and last week, with the ratios (vs_last_week_x, vs_yesterday_x), how it looks
        ('higher than usual', 'lower than usual', 'usual', 'no data'), since when it has looked off (off_since, from
        5-minute points), the alarms set on it and their state, the hours before it, a sentence to copy (`says`) and
        findings (a number that's off with no alarm on it, and the command that would set one).
        resource: kind:name ('lambda:orders-etl', 'sagemaker:churn-endpoint', 'sqs:email-jobs',
        'states:order-pipeline', 'dynamodb:orders', 'apigateway:orders-api', 'http:<api id>'), an ARN, or a raw
        metric's namespace and dimensions ('AWS/EC2:InstanceId=i-0abc'). metric: '' for the kind's main numbers, or
        one ('p99 latency', 'errors', 'CPUUtilization'). at: the end of the window, ISO UTC or relative ('3h' ago);
        '' for now. minutes: the window's length, 15 to 360. region: '' for the agent's (or the ARN's).
        """
        core = self.analyzer
        ref = parse_resource(resource)
        where = str(region or "").strip() or ref.region or core.region
        window = make_window(_end_of(at), minutes)
        try:
            found = core.resolve(ref, where)
            reads = _metric_reads(found, metric)
            series = core.read(reads, found.namespace, window, where)
            if not any(part.points for trio in series for part in trio):
                core.has_metrics(found, where)
        except NoMetrics as exc:
            return self._missing(exc, ref.text, where)
        self._count_cloudwatch(len(reads) * _QUERIES_PER_NUMBER)
        compared = []
        for read, trio in zip(reads, series):
            item = compare(read.spec, window, trio, label=read.label, dimensions=read.dimensions)
            item.alarms = core.alarms_for(read, found.namespace, where)
            compared.append(item)
        out = summarize_comparisons(compared, name=found.name, namespace=found.namespace, window=window,
                                    region=where)
        out["resource"] = ref.text
        out["regions"] = [where]
        off = [item for item in compared if item.looks in OFF]
        out["_summary"] = (f"{found.name}: " + (", ".join(f"{item.label} {item.looks}" for item in off[:2])
                                                if off else f"{_plural(len(compared), 'number')} as usual"))
        return out

    def find_alarms(self, state: str = "ALARM", prefix: str = "", region: str = "") -> dict[str, Any]:
        """
        The region's CloudWatch alarms, the ones firing first: each one's state and since when, its rule in words
        ('p95 > 1,000 ms for 3 of 3 periods of 5 minutes'), what it watches (as compare_metric's resource=), whether
        it tells anyone (notifies, actions_enabled) and why it's in its state; with how many are in each state and
        findings (alarms firing, ones that tell no one). state: 'ALARM' (default), 'OK', 'INSUFFICIENT_DATA' or ''
        for all. prefix: only alarms whose names start with it. region: '' for the agent's.
        """
        core = self.analyzer
        where = str(region or "").strip() or core.region
        wanted = _state_of(state)
        alarms, cut_short = core.alarms(state=wanted, prefix=str(prefix or "").strip(), region=where)
        out = summarize_alarms(alarms, state=wanted, region=where, cut_short=cut_short)
        out["regions"] = [where]
        firing = out["states"].get("ALARM", 0)
        out["_summary"] = (f"{_plural(len(alarms), 'alarm')}" + (f", {firing:,} in ALARM" if firing and wanted != "ALARM"
                                                                 else ""))
        return out
