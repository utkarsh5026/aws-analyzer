"""
What errors() says about each cause and about the window as a whole: whether a cause is new, still failing or has
stopped; what it means and what to do about it, in plain words; the columns its chart draws (calls and failed calls
per hour or day, each cause's lines in the same columns); the sentence that reads the chart; and the verdict at the
top of the report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ..._kit.fmt import _in_zone, _plural, human_age
from ..._kit.text import _clip

from ..helpers import human_ms
from ..models import ErrorGroup, ErrorReport, Function, FunctionMetrics
from .errors import _ACCESS_DENIED_RE, _CONNECT_RE, _DENIED_ACTION_RE, _LOAD_ERRORS, _THROTTLED_RE
from .fates import LOST
from .findings import _longer, _next_memory
from .text import _cli, _window

if TYPE_CHECKING:
    from .logs import LogRun


NEW, ONGOING, STOPPED = "new", "still failing", "stopped"

_QUIET_SHARE = 0.2

_QUIET_LEAST = timedelta(hours=3)

_NEW_SHARE = 0.2

_SLOTS_WITHOUT_METRICS = 48

_HOUR = 3600

_PEAK_SHARE = 0.5

_FEWEST_FOR_A_PATTERN = 4

_TREND_RATIO = 2

_VERDICT_MESSAGE = 80

_EXCEPTIONS = {
    "KeyError": ("The code read a key the data didn't have{detail}: some inputs don't carry it.",
                 "Read it with .get() and a default, or skip the inputs that don't have it."),
    "TypeError": ("A value had a type the code didn't expect, often None.",
                  "Check for None before using the value, or convert it first."),
    "AttributeError": ("The code used a field or method on something that doesn't have it, often None.",
                       "Check what the value is before using it."),
    "ValueError": ("A value had the right type but a form the code can't use.",
                   "Check the input before parsing it, and handle the ones that don't fit."),
    "IndexError": ("The code read past the end of a list.", "Check the list's length before reading from it."),
    "JSONDecodeError": ("Input that should be JSON isn't.",
                        "Log the raw input once, and handle what can't be parsed."),
    "ZeroDivisionError": ("The code divided by zero.", "Check the divisor before dividing."),
    "Logged error": ("Its code logged a line at ERROR level.", "Read one run to see what it was doing then."),
}


@dataclass
class CauseState:
    """
    Whether a cause is 'new' (it started in the last part of the window), 'still failing' or 'stopped' (nothing
    for a while), and that in a few words: 'stopped: none for 6h, about 290 calls since'.
    """

    state: str
    says: str


@dataclass
class CauseAdvice:
    """
    What a cause means, in plain words, and the steps to take, each a sentence that may hold a call or a command.
    """

    means: str
    steps: list[str] = field(default_factory=list)


@dataclass
class ChartColumns:
    """
    The columns of errors()' chart: each one's start, how long each is (`period`, seconds), the calls and failed
    calls CloudWatch counted in it (None without CloudWatch's numbers), and each cause's lines in it.
    """

    starts: list[datetime]
    period: int
    calls: list[float] | None = None
    failed: list[float] | None = None
    lanes: list[list[int]] = field(default_factory=list)

    @property
    def unit(self) -> str:
        return "hour" if self.period <= _HOUR else "day"


@dataclass
class Verdict:
    """
    The report's first sentence (`headline`), the numbers behind it (`detail`), and its tone: 'bad' while events are
    lost or a cause is still failing, 'ok' otherwise.
    """

    headline: str
    detail: str
    tone: str


def calls_after(metrics: FunctionMetrics | None, moment: datetime) -> int | None:
    """
    About how many calls CloudWatch counted after `moment`: the periods that start after it, so a little low. None
    without CloudWatch's numbers.
    """
    if metrics is None:
        return None
    return round(sum(row.invocations for row in metrics.daily if row.start >= moment))


def cause_state(group: ErrorGroup, *, since: datetime, until: datetime,
                metrics: FunctionMetrics | None = None) -> CauseState:
    """
    Whether a cause has stopped (no line for a fifth of the window, and at least three hours), is new (its first line
    in the last fifth of the window) or is still failing, with the words a card shows.
    """
    span = until - since
    quiet = until - group.last
    if quiet >= max(span * _QUIET_SHARE, _QUIET_LEAST):
        calls = calls_after(metrics, group.last)
        since_then = f", about {calls:,} calls since" if calls else ""
        return CauseState(STOPPED, f"stopped: none for {human_age(group.last, until).removesuffix(' ago')}"
                                   f"{since_then}")
    if group.first >= until - span * _NEW_SHARE:
        return CauseState(NEW, f"new: started {human_age(group.first, until)}")
    return CauseState(ONGOING, f"still failing, the last {human_age(group.last, until)}")


def _role_name(fn: Function) -> str:
    return (fn.role or "").split("/")[-1] or "its execution role"


def _allow_command(fn: Function, action: str, resource: str) -> str:
    """
    The command that lets the function's role do `action` on `resource`, as an inline policy.
    """
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": action, "Resource": resource}]}
    name = f"allow-{action.replace(':', '-').lower()}"
    return (f"aws iam put-role-policy --role-name {_role_name(fn)} --policy-name {name} --policy-document "
            f"'{json.dumps(policy, separators=(',', ':'))}'")


def _denied_advice(fn: Function, group: ErrorGroup) -> CauseAdvice:
    denied = _DENIED_ACTION_RE.search(group.example)
    if denied is None:
        return CauseAdvice(f"Its role {_role_name(fn)} wasn't allowed something its code does.",
                           ["Add the permission the message names to the role's policy in IAM."])
    action, resource = denied.group(1), (denied.group(2) or "*").rstrip(".")
    on = f" on {resource}" if resource != "*" else ""
    return CauseAdvice(f"Its role {_role_name(fn)} isn't allowed {action}{on}.",
                       [f"Allow it: {_allow_command(fn, action, resource)}."])


def _exception_advice(group: ErrorGroup) -> CauseAdvice:
    means, step = _EXCEPTIONS.get(group.kind, (
        f"Its code raised {group.kind} and nothing caught it.",
        "Fix the code where it fails, or catch the error and handle it."))
    detail = group.message.partition(":")[2].strip()
    return CauseAdvice(means.format(detail=f" ({detail})" if detail else ""), [step])


def cause_advice(group: ErrorGroup, fn: Function) -> CauseAdvice:
    """
    What a cause means and what to do about it: timeouts, running out of memory, code that can't load, a missing
    permission (with the command that grants it), throttling, a VPC that can't reach the service, and the common
    Python exceptions; any other error is its code's own.
    """
    name, cli = fn.name, _cli(fn)
    if group.kind == "Timeout":
        return CauseAdvice(f"Lambda stopped the run at its {fn.timeout} s limit, part way through its work.",
                           [f"If runs need longer: {_longer(fn)}.",
                            f"If they shouldn't take that long, performance('{name}') shows the slowest runs."])
    if group.kind == "Out of memory":
        return CauseAdvice(f"The run used all of its {fn.memory:,} MB and Lambda killed it.",
                           [f"Give it more: aws lambda update-function-configuration {cli} --memory-size "
                            f"{_next_memory(fn.memory)}.", f"performance('{name}') shows what the other runs use."])
    if group.kind in _LOAD_ERRORS:
        return CauseAdvice("Its code can't be loaded, so calls fail before any of it runs.",
                           [f"Check the package has the module and the handler setting ({fn.handler or '-'}) names a "
                            f"real file and function: code('{name}') shows what's in it."])
    if _ACCESS_DENIED_RE.search(group.example):
        return _denied_advice(fn, group)
    if _THROTTLED_RE.search(group.example):
        return CauseAdvice("A service it calls refused it for going too fast.",
                           ["Retry with backoff (boto3's Config(retries={'mode': 'adaptive'})), or ask that service "
                            "for a higher limit."])
    if fn.vpc_id and _CONNECT_RE.search(group.example):
        return CauseAdvice(f"It couldn't reach a service: it runs in VPC {fn.vpc_id}, which reaches the internet and "
                           "AWS APIs only through a NAT gateway or VPC endpoints.",
                           ["Add a VPC endpoint for the service it calls, or a NAT gateway."])
    return _exception_advice(group)


def _floor(moment: datetime, period: int) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % period, tz=timezone.utc)


def column_counts(times: list[datetime], starts: list[datetime], period: int) -> list[int]:
    """
    How many of `times` fall in each column (from its start, for `period` seconds).
    """
    counts = [0] * len(starts)
    if not starts:
        return counts
    first = starts[0]
    for moment in times:
        index = int((moment - first).total_seconds() // period)
        if 0 <= index < len(starts):
            counts[index] += 1
    return counts


def chart_columns(report: ErrorReport) -> ChartColumns:
    """
    The chart's columns for an ErrorReport: CloudWatch's own periods (an hour for a window of two days or less, else a
    day) with its calls and failed calls, or 48 equal slices without its numbers; and each cause's lines per column,
    in the report's order.
    """
    metrics = report.metrics
    if metrics is not None and metrics.period:
        period = metrics.period
        first = _floor(report.since, period)
        starts = []
        moment = first
        while moment < report.until:
            starts.append(moment)
            moment += timedelta(seconds=period)
        calls, failed = [0.0] * len(starts), [0.0] * len(starts)
        for row in metrics.daily:
            index = int((row.start - first).total_seconds() // period)
            if 0 <= index < len(starts):
                calls[index] += row.invocations
                failed[index] += row.errors
        columns = ChartColumns(starts, period, calls, failed)
    else:
        period = max(1, int((report.until - report.since).total_seconds() / _SLOTS_WITHOUT_METRICS))
        starts = [report.since + timedelta(seconds=period * i) for i in range(_SLOTS_WITHOUT_METRICS)]
        columns = ChartColumns(starts, period)
    columns.lanes = [column_counts(group.times, columns.starts, columns.period) for group in report.groups]
    return columns


def _clock(moment: datetime, unit: str) -> str:
    return _in_zone(moment).strftime("%H:%M" if unit == "hour" else "%b %d")


def failure_pattern(columns: ChartColumns) -> str:
    """
    What the failed calls per column say: most of them at once, rising, falling, or spread out (with the most in one
    column). '' when there are too few to say.
    """
    failed = columns.failed or [float(sum(lane[i] for lane in columns.lanes)) for i in range(len(columns.starts))]
    total = round(sum(failed))
    if total < _FEWEST_FOR_A_PATTERN:
        return ""
    unit = columns.unit
    peak = max(failed)
    if peak >= _PEAK_SHARE * total:
        when = columns.starts[failed.index(peak)]
        return f"Most failures came at once: {round(peak):,} of {total:,} in the {unit} from {_clock(when, unit)}."
    half = len(failed) // 2
    before, after = round(sum(failed[:half])), round(sum(failed[half:]))
    if after >= _TREND_RATIO * max(before, 1):
        return f"Failures are rising: {before:,} in the first half of the window, {after:,} in the second."
    if before >= _TREND_RATIO * max(after, 1):
        return f"Failures are falling: {before:,} in the first half of the window, {after:,} in the second."
    return f"Failures are spread across the window, at most {round(peak):,} in one {unit}, with no spike."


def _causes_phrase(count: int, state: str) -> str:
    verb = "is" if count == 1 else "are"
    return f"{_plural(count, 'cause')} {verb} {state}"


def errors_verdict(report: ErrorReport, states: list[CauseState]) -> Verdict:
    """
    The report's first sentence: events lost, then how many causes are still failing and how many stopped; the
    numbers behind it under it.
    """
    lost = sum(fate.count or 0 for fate in report.fates if fate.outcome == LOST)
    failing = sum(1 for state in states if state.state != STOPPED)
    stopped = len(states) - failing
    parts = [f"{_plural(lost, 'event')} {'was' if lost == 1 else 'were'} lost"] if lost else []
    if failing:
        parts.append(_causes_phrase(failing, "still failing"))
    if stopped:
        parts.append(f"{stopped} stopped" if failing else _causes_phrase(stopped, "stopped"))
    if not parts:
        parts.append("no cause found in its logs")
    headline = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    headline = headline[0].upper() + headline[1:] + "."
    metrics = report.metrics
    window = _window((report.until - report.since).total_seconds())
    detail = f"In the last {window}"
    if metrics is not None and metrics.invocations:
        detail += f", {round(metrics.errors):,} of {round(metrics.invocations):,} calls failed ({metrics.error_rate:.1%})"
    last = max((group.last for group in report.groups), default=None)
    if last is not None:
        detail += f"; the last error {human_age(last, report.until)}"
    tone = "bad" if lost or failing else "ok"
    return Verdict(headline, detail + ".", tone)


def _spread(durations: list[float], timeout: int) -> str:
    if not durations:
        return ""
    return f"Runs that worked took {human_ms(min(durations))} to {human_ms(max(durations))}, of a {timeout} s timeout."


def runs_verdict(runs: list[LogRun], fn: Function, causes: list[tuple[ErrorGroup, CauseState]], *,
                 until: datetime, matching: bool = False) -> Verdict:
    """
    runs()' first sentence: how many of the runs failed and from how many causes (each failed run's group and its
    state), then the cause still failing first (or that none is) and how long the runs that worked took.
    """
    noun = "runs that match" if matching else "runs"
    failed = [run for run in runs if run.status in ("failed", "timeout")]
    worked = [run.duration for run in runs if run.status == "ok" and run.duration is not None]
    spread = _spread(worked, fn.timeout)
    if not failed:
        return Verdict(f"All {len(runs):,} {noun} of {fn.name} worked." if len(runs) > 1
                       else f"The one run of {fn.name} worked.", spread, "ok")
    from_causes = f", from {_plural(len(causes), 'cause')}" if causes else ""
    headline = f"{len(failed):,} of {len(runs):,} {noun} failed ({len(failed) / len(runs):.0%}){from_causes}."
    still = [(group, state) for group, state in causes if state.state != STOPPED]
    if still:
        group, state = still[0]
        others = f" ({len(still) - 1} more still failing)" if len(still) > 1 else ""
        says = f"{_clip(group.message.splitlines()[0], _VERDICT_MESSAGE)}: {state.says}{others}."
    else:
        last = max((run.start for run in failed), default=None)
        says = f"None is failing any more: the last failed run started {human_age(last, until)}." if last else ""
    return Verdict(headline, " ".join(part for part in (says, spread) if part), "bad" if still else "ok")
