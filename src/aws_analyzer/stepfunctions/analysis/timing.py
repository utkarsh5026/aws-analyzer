"""
Where a state machine's time goes over several executions: per state, how often it ran, the median and the longest
of its runs, its share of the executions' time and how many tries a run took, read from the steps of executions
already filled (fill_detail), and what's worth doing about it. Pure: run it on ExecutionDetails of your own.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from ..._kit.fmt import _duration, _plural, human_money
from ..._kit.render import _call

from ..helpers import execution_ref
from ..models import Execution, ExecutionDetail, StateMachine, StateTiming, StepRun, TimingReport
from .cost import DAYS_PER_MONTH


Finding = tuple[str, str]

SLOW_SHARE = 0.4

SLOW_SECONDS = 1.0

RETRY_RATE = 0.1

UNEVEN_RATIO = 3.0

UNEVEN_SECONDS = 5.0

_UNEVEN_RUNS = 3

_THROTTLED = "TooManyRequests"

_SHORTEST_IN_SECONDS = 0.1


def took_text(seconds: float | None) -> str:
    """
    A run's time in a few characters, always in seconds so a column lines up: '0.02s' under a tenth of a second,
    else as _duration writes it ('9.5s', '2m 05s'); '-' when it never finished.
    """
    if seconds is None:
        return "-"
    if seconds < _SHORTEST_IN_SECONDS:
        return f"{seconds:.2f}s"
    return _duration(seconds)


@dataclass
class _Runs:
    """
    What one state's runs add up to while the executions are read.
    """

    timing: StateTiming
    seconds: list[float] = field(default_factory=list)
    seen_in: set[str] = field(default_factory=set)
    retry_errors: Counter[str] = field(default_factory=Counter)


def _execution_seconds(detail: ExecutionDetail) -> float:
    """
    How long an execution ran: from its start to its stop, else from its first step's entry to its last step's end.
    """
    if detail.execution.seconds is not None:
        return detail.execution.seconds
    top = [step for step in detail.steps if step.depth == 0]
    ends = [step.entered.timestamp() + step.seconds for step in top if step.seconds is not None]
    return max(ends) - top[0].entered.timestamp() if top and ends else 0.0


def _definition_order(details: list[ExecutionDetail]) -> list[str]:
    machine = next((d.machine for d in details if d.machine is not None), None)
    return [state.name for state in machine.states] if machine is not None else []


def _add_tries(runs: _Runs, step: StepRun, execution: Execution) -> None:
    """
    A task run's tries: counted, and when it took more than one, the error the first retry followed and the
    execution it happened in (the newest one, since the executions come newest first).
    """
    timing = runs.timing
    timing.tries += len(step.attempts)
    timing.tried_runs += 1
    if len(step.attempts) < 2:
        return
    timing.retried_runs += 1
    runs.retry_errors.update(attempt.error for attempt in step.attempts[:-1] if attempt.error)
    if timing.retried_in is None:
        timing.retried_in = execution


def _add_run(runs: _Runs, step: StepRun, execution: Execution) -> None:
    timing = runs.timing
    timing.runs += 1
    runs.seen_in.add(execution.arn)
    timing.function = timing.function or step.function
    if step.attempts:
        _add_tries(runs, step, execution)
    if step.seconds is None:
        return
    runs.seconds.append(step.seconds)
    timing.total += step.seconds
    if timing.longest is None or step.seconds > timing.longest:
        timing.longest, timing.slowest = step.seconds, execution


def _settled(runs: _Runs, total: float) -> StateTiming:
    timing = runs.timing
    timing.executions = len(runs.seen_in)
    timing.median = statistics.median(runs.seconds) if runs.seconds else None
    if timing.depth == 0 and total > 0:
        timing.share = min(1.0, timing.total / total)
    timing.retry_error = runs.retry_errors.most_common(1)[0][0] if runs.retry_errors else ""
    return timing


def state_timings(details: list[ExecutionDetail]) -> list[StateTiming]:
    """
    Per state, over the executions given (newest first, as timings() reads them): how many times it ran and in how
    many executions, the median and the longest of its finished runs, the time its runs took and, for a top-level
    state, its share of the executions' time, and its tries: per run, how many runs retried, the most common error a
    retry followed, and the newest execution with a retry. In the definition's order, then any state it doesn't list.
    """
    by_name: dict[str, _Runs] = {}
    for name in _definition_order(details):
        by_name[name] = _Runs(StateTiming(name, ""))
    for detail in details:
        for step in detail.steps:
            runs = by_name.setdefault(step.name, _Runs(StateTiming(step.name, step.type)))
            runs.timing.type, runs.timing.depth = step.type, step.depth
            _add_run(runs, step, detail.execution)
    total = sum(_execution_seconds(detail) for detail in details)
    return [_settled(runs, total) for runs in by_name.values() if runs.timing.runs]


def timing_report(machine: StateMachine, details: list[ExecutionDetail], report: TimingReport) -> TimingReport:
    """
    Fills a TimingReport from the executions read: their list, each state's timing and their time added up.
    """
    report.executions = [detail.execution for detail in details]
    report.states = state_timings(details)
    report.total = sum(_execution_seconds(detail) for detail in details)
    report.partial = sum(1 for detail in details if detail.partial)
    return report


def _ref(report: TimingReport, execution: Execution) -> str:
    return execution_ref(report.machine.name, execution.name, execution.arn)


def _inside_slowest(report: TimingReport, timing: StateTiming) -> StateTiming | None:
    """
    The state inside a Map or Parallel whose runs take the most time in all.
    """
    inside = f"{timing.name} > "
    paths = {state.name for state in report.machine.states if state.path.startswith(inside)}
    found = [s for s in report.states if s.name in paths and s.total > 0]
    return max(found, key=lambda s: s.total) if found else None


def _where_to_look(report: TimingReport, timing: StateTiming) -> str:
    """
    What a slow state's time is, by what it does, and where to look next.
    """
    state = report.machine.state(timing.name)
    if timing.type == "Wait":
        return " It's a Wait state, so that's the wait its definition sets."
    if state is not None and state.waits_for_callback:
        return " It waits for a task token, so the time is spent waiting for whoever sends it back."
    if timing.type in ("Map", "Parallel"):
        inner = _inside_slowest(report, timing)
        if inner is None or inner.median is None:
            return ""
        per_execution = inner.runs / max(1, inner.executions)
        look = f" ({_lambda_call('performance', inner.function)})" if inner.function else ""
        return (f" Inside it, {inner.name} takes the most: {per_execution:.3g} runs an execution, a median of "
                f"{took_text(inner.median)} each{look}.")
    if timing.function:
        return f" Its Lambda function is where to look: {_lambda_call('performance', timing.function)}."
    return ""


def _lambda_call(command: str, function: str) -> str:
    return f"LambdaView().{_call(command, function)}"


def _slow_findings(report: TimingReport) -> list[Finding]:
    top = [s for s in report.states if s.share is not None and s.median is not None]
    if not top:
        return []
    slowest = max(top, key=lambda s: s.share or 0.0)
    is_slow = (slowest.share or 0.0) >= SLOW_SHARE and (slowest.median or 0.0) >= SLOW_SECONDS
    if not is_slow:
        return []
    return [("info", f"{slowest.name} takes {slowest.share:.0%} of the time: a median of "
                     f"{took_text(slowest.median)} a run, the longest {took_text(slowest.longest)}, over "
                     f"{_plural(len(report.executions), 'execution')}. Making it faster is what makes "
                     f"{report.machine.name} faster.{_where_to_look(report, slowest)}")]


def _retries_text(count: float) -> str:
    whole = round(count)
    return f"{whole:,} {'retry' if whole == 1 else 'retries'}"


def _retry_cost(report: TimingReport, timing: StateTiming, prices: dict[str, float]) -> str:
    """
    What a state's retries add to the bill a month, at the pace of the state machine's CloudWatch numbers: each one
    is a state transition (Standard), or run time (Express). '' without the numbers.
    """
    numbers = report.numbers
    if numbers is None or not numbers.days or not report.executions:
        return ""
    per_month = numbers.started / numbers.days * DAYS_PER_MONTH
    retries_a_month = per_month * timing.retries / len(report.executions)
    if report.machine.is_express:
        return (f" An Express workflow is billed by run time, so the {_retries_text(retries_a_month)} "
                "a month this pace makes are paid for in the time their back-off waits.")
    cost = retries_a_month * prices["transition"]
    return (f" Each retry is another state transition: about {_retries_text(retries_a_month)} a month at "
            f"this pace, {human_money(cost)}.")


def _retry_fix(report: TimingReport, timing: StateTiming) -> str:
    """
    What to do about a state's retries, then the execution that shows one.
    """
    look = f" {_call('execution', _ref(report, timing.retried_in))} shows one." if timing.retried_in else ""
    if _THROTTLED in timing.retry_error and timing.function:
        return (f" Lambda throttled it: give {timing.function} reserved concurrency, or start fewer at once (a Map's "
                f"MaxConcurrency).{look}")
    if timing.function:
        return f" {_lambda_call('errors', timing.function)} shows why it fails.{look}"
    return look


def _retry_findings(report: TimingReport, prices: dict[str, float]) -> list[Finding]:
    found = []
    for timing in report.states:
        if not timing.retried_runs or timing.retries / timing.tried_runs < RETRY_RATE:
            continue
        error = f", mostly after {timing.retry_error}" if timing.retry_error else ""
        found.append(("warn", f"{timing.name} retries in {timing.retried_runs:,} of its {timing.tried_runs:,} runs "
                              f"({timing.tries_per_run:.2f} tries a run{error}): each retry waits its back-off, "
                              f"so it's slower than it needs to be.{_retry_cost(report, timing, prices)}"
                              f"{_retry_fix(report, timing)}"))
    return found


def _uneven_findings(report: TimingReport) -> list[Finding]:
    found = []
    for timing in report.states:
        if timing.median is None or timing.longest is None or timing.runs < _UNEVEN_RUNS or timing.slowest is None:
            continue
        is_uneven = timing.longest >= UNEVEN_RATIO * timing.median and timing.longest - timing.median >= UNEVEN_SECONDS
        if is_uneven:
            found.append(("info", f"{timing.name} runs unevenly: a median of {took_text(timing.median)}, but one took "
                                  f"{took_text(timing.longest)}. One slow run isn't a habit: "
                                  f"{_call('execution', _ref(report, timing.slowest))} shows that one."))
    return found


def timing_findings(report: TimingReport, prices: dict[str, float]) -> list[Finding]:
    """
    What the timings say to do: the states that retry often (each retry a state transition on a Standard
    workflow's bill, and its back-off's wait), the state that takes most of the time (when its runs take a second or
    more) and where to look in it, and states whose runs vary a lot, with the execution that shows the slow one.
    """
    if not report.executions:
        return []
    return _retry_findings(report, prices) + _slow_findings(report) + _uneven_findings(report)
