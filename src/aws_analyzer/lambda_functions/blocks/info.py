"""
The pictures function_info() and the explorer's Overview tab share: how a function is wired, its calls and run time
a day, and where its monthly cost goes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..._kit.charts import _Part, _Split
from ..._kit.fmt import _count, _plural, _total, human_money, human_size

from ..helpers import MB, _midnight, _pct, human_ms
from ..models import FunctionDetail
from ..analysis.functions import _source_name
from .logs import _short_day
from .wiring import _Columns, _Wiring

_DESTINATION_KINDS = {"sqs": "SQS queue", "sns": "SNS topic", "lambda": "Lambda function",
                      "events": "EventBridge bus", "s3": "S3 bucket"}
_BIG_LOGS_KEPT_FOREVER = 100 * MB
_ONE_DAY = 86400


def _retries(count: int) -> str:
    return "no retries" if not count else "1 retry" if count == 1 else f"{count} retries"


def _destination(arn: str | None) -> tuple[str, str]:
    """
    (what it is, its name) for an on-success / on-failure destination or a dead-letter queue: an SQS queue, an SNS
    topic, another function...
    """
    parts = (arn or "").split(":")
    kind = _DESTINATION_KINDS.get(parts[2], parts[2]) if len(parts) > 5 else ""
    return kind or "destination", _source_name(arn)[0] or (arn or "-")


def _input_nodes(detail: FunctionDetail) -> list[tuple[str, str, str, str]]:
    """
    What calls the function, a node each, marked when it's public, disabled or its last read had a problem.
    """
    nodes = []
    for trigger in detail.triggers:
        is_disabled = (trigger.state or "").lower() == "disabled"
        has_problem = bool(trigger.last_result and trigger.last_result.upper().startswith("PROBLEM"))
        detail_text = "; ".join(filter(None, [
            "anyone on the internet can call it" if trigger.public else "", "disabled" if is_disabled else "",
            trigger.detail, trigger.last_result]))
        tone = "warn" if trigger.public or is_disabled or has_problem else ""
        nodes.append((trigger.kind, trigger.source or ("anyone" if trigger.public else "-"), detail_text, tone))
    return nodes


def _log_node(detail: FunctionDetail) -> tuple[str, str, str, str]:
    """
    Where its logs go, how long they're kept and how much is stored, marked when big logs are kept forever.
    """
    fn, group = detail.function, detail.log_group
    if group is not None and group.retention_days is None:
        kept = "kept forever"
    elif group is not None and group.retention_days:
        kept = f"kept {_plural(group.retention_days, 'day')}"
    elif group is None and "log_group" not in detail.errors:
        kept = "no log group yet"
    else:
        kept = ""
    stored = f", {human_size(group.stored_bytes)} stored" if group is not None and group.stored_bytes else ""
    is_kept_forever = group is not None and group.retention_days is None
    is_big_forever = is_kept_forever and (group.stored_bytes or 0) >= _BIG_LOGS_KEPT_FOREVER
    return ("Logs", fn.log_group, (kept + stored).strip(", "), "warn" if is_big_forever else "")


def _output_nodes(detail: FunctionDetail) -> list[tuple[str, str, str, str]]:
    """
    Where its results, failed events and logs go, and the role it runs as.
    """
    fn, config = detail.function, detail.async_config
    nodes = []
    if config is not None and config.on_success:
        kind, name = _destination(config.on_success)
        nodes.append(("On success", name, f"{kind}: each asynchronous call's result", ""))
    failure = (config.on_failure if config is not None else None) or fn.dead_letter
    asynchronous = list(dict.fromkeys(t.kind for t in detail.triggers if t.asynchronous))
    if failure:
        kind, name = _destination(failure)
        retries = f"after {_retries(config.retries)}" if config is not None else ""
        nodes.append(("On failure", name, f"{kind}, {retries}".rstrip(", "), ""))
    elif asynchronous and config is not None:
        nodes.append(("On failure", "nowhere: dropped",
                      f"events from {', '.join(asynchronous)} that still fail after {_retries(config.retries)} "
                      "are lost", "warn"))
    nodes.append(_log_node(detail))
    if fn.role:
        nodes.append(("Allowed to use", fn.role.split("/")[-1],
                      f"its execution role{' · in VPC ' + fn.vpc_id if fn.vpc_id else ''}", ""))
    return nodes


def wiring_block(detail: FunctionDetail) -> _Wiring:
    """
    What calls the function, and where its results, failed events and logs go, as a diagram.
    """
    fn = detail.function
    center = (fn.name, " · ".join(filter(None, [fn.runtime or "container image", f"{fn.memory:,} MB",
                                                f"{fn.timeout} s timeout", fn.architecture])))
    return _Wiring(_input_nodes(detail), center, _output_nodes(detail),
                   title="How it's wired: what calls it, and where its results and logs go",
                   empty="Nothing calls it on its own: it's called directly (an SDK, Step Functions...)")


def _day_columns(detail: FunctionDetail, days: list[datetime]) -> tuple[list, list]:
    """
    For each day, its calls (the failed part second) and its run time (the average under the longest), with a
    tooltip saying the day's numbers; a day without calls is an empty column.
    """
    by_day = {d.start.astimezone(timezone.utc).date(): d for d in detail.metrics.daily}
    calls, times = [], []
    for day in days:
        usage = by_day.get(day.date())
        label = _short_day(day)
        if usage is None or not usage.invocations:
            calls.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
            times.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
            continue
        failed = f", {_count(round(usage.errors))} failed ({_pct(usage.errors / usage.invocations)})" if (
            usage.errors) else ""
        throttled = f", {_count(round(usage.throttles))} throttled" if usage.throttles else ""
        calls.append((label, usage.invocations, usage.errors,
                      f"{day:%a} {label}: {_count(round(usage.invocations))} calls{failed}{throttled}"))
        times.append((label, usage.duration_max or usage.avg_duration or 0.0, usage.avg_duration or 0.0,
                      f"{day:%a} {label}: average {human_ms(usage.avg_duration)}, longest "
                      f"{human_ms(usage.duration_max)}"))
    return calls, times


def day_charts(detail: FunctionDetail, *, days: int = 30, now: datetime) -> list[_Columns]:
    """
    Calls a day (the failed part in red), failed calls a day on their own scale (when some failed) and run time a day (the average under the longest, against the timeout),
    a dashed line through the day it was last changed (its code or settings) when that's one of them. Nothing when
    CloudWatch counted no calls or the numbers aren't by day.
    """
    m, fn = detail.metrics, detail.function
    if m is None or not m.invocations or m.period < _ONE_DAY:
        return []
    today = _midnight(now)
    shown_days = [today - timedelta(days=days - 1 - i) for i in range(days)]
    calls, times = _day_columns(detail, shown_days)
    changed = fn.last_modified
    mark = shown_days.index(_midnight(changed)) if changed is not None and _midnight(changed) in shown_days else None
    label = f"changed {_short_day(changed)}" if mark is not None else ""
    charts = [_Columns(calls, title=f"Calls a day, the failed part in red (CloudWatch, the last {days} days, UTC "
                                    "days)", mark=mark, mark_label=label)]
    if m.errors:
        failed = [(day_label, part, part, tip) for day_label, _, part, tip in calls]
        charts.append(_Columns(failed, title="Failed calls a day, on their own scale, so a rise shows even when "
                                             "they're a small share of the calls", mark=mark, mark_label=label))
    return charts + [
        _Columns(times, title=f"Run time a day: the average (dark) under the longest (light), against its "
                              f"{fn.timeout} s timeout", part="dim", limit=fn.timeout * 1000,
                 limit_label=f"timeout {fn.timeout} s", unit="ms", mark=mark, mark_label=label),
    ]


def cost_split(cost: dict[str, float | None], names: dict[str, str], *, basis: str) -> _Split:
    """
    The estimated monthly cost as one bar of its parts, largest first, so "logs are most of it" is seen at once. A
    part whose price isn't known is left out of the bar and named in the note.
    """
    known = {part: value for part, value in cost.items() if value is not None}
    parts = [_Part(names.get(part, part), value, human_money(value))
             for part, value in sorted(known.items(), key=lambda item: -item[1])]
    total = human_money(_total(known) or 0.0)
    unknown = [names.get(part, part) for part, value in cost.items() if value is None]
    left_out = f" Not priced, so left out: {', '.join(unknown)}." if unknown else ""
    return _Split(parts, total, title=f"Estimated monthly cost: {total}",
                  note=f"From {basis}, before the free tier.{left_out}")
