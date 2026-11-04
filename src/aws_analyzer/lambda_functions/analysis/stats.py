"""
A function's numbers over a range of time: cold starts and memory from its runs' REPORT lines, its busiest day, and
the findings about the range (failed and throttled calls, runs near the timeout, and what performance_findings says).
"""

from __future__ import annotations

from typing import Any, Iterable

from ..._kit.fmt import _plural
from ..._kit.render import _call

from ..helpers import _pct, human_ms
from ..models import DailyUsage, FunctionMetrics, FunctionStats, Invocation, RunSample
from .performance import percentile, performance_findings
from .findings import _longer


_FAILED_CALLS_TO_WARN = 10
_ERROR_RATE_TO_WARN = 0.01
_LONGEST_SHARE_TO_WARN = 0.9


def run_sample(invocations: Iterable[Invocation]) -> RunSample:
    """
    Cold starts and memory from runs' REPORT lines (Invocation): how many runs, how many started cold and their
    average start-up time, and the most and median memory used against what they had.
    """
    runs = list(invocations)
    if not runs:
        return RunSample()
    starts = [r.init for r in runs if r.init is not None]
    used = [float(r.max_memory) for r in runs if r.max_memory]
    return RunSample(
        runs=len(runs),
        cold_starts=len(starts),
        init_avg=sum(starts) / len(starts) if starts else None,
        memory=max((r.memory for r in runs if r.memory), default=0),
        memory_max=int(max(used, default=0)),
        memory_median=percentile(used, 50),
    )


def busiest_period(metrics: FunctionMetrics | None) -> DailyUsage | None:
    """
    The day (or hour) with the most calls; None when there were none.
    """
    rows = [row for row in (metrics.daily if metrics is not None else []) if row.invocations]
    return max(rows, key=lambda row: row.invocations, default=None)


def failed_calls_worth_warning(metrics: FunctionMetrics | None) -> bool:
    """
    Whether the range's failed calls are worth acting on: at least 10, and 1% of its calls or more.
    """
    if metrics is None or not metrics.invocations:
        return False
    return metrics.errors >= _FAILED_CALLS_TO_WARN and metrics.errors / metrics.invocations >= _ERROR_RATE_TO_WARN


def _range_findings(stats: FunctionStats, range_args: dict[str, Any]) -> list[tuple[str, str]]:
    """
    What CloudWatch's numbers for the range say: failed calls, throttled calls, and (without REPORT lines to say
    more) a longest run close to the timeout.
    """
    fn, m = stats.function, stats.metrics
    found: list[tuple[str, str]] = []
    if m is None or not m.invocations:
        return found
    if m.errors >= 1:
        level = "warn" if failed_calls_worth_warning(m) else "info"
        found.append((level, (
            f"{_plural(round(m.errors), 'call')} of {round(m.invocations):,} failed ({_pct(m.error_rate)}): each one "
            f"is work that didn't get done, or was retried. {_call('errors', fn.name, **range_args)} groups them by "
            "cause."
        )))
    if m.throttles >= 1:
        peak = f" (at most {m.concurrency_max:,.0f} ran at once)" if m.concurrency_max else ""
        found.append(("warn", (
            f"{_plural(round(m.throttles), 'call')} were throttled{peak}: Lambda turned them away because too many "
            "were running, and callers that don't retry lost them. Raise its reserved concurrency, or the account's "
            f"limit; {_call('function_info', fn.name)} shows both."
        )))
    has_report_lines = stats.perf is not None and bool(stats.perf.invocations)
    longest = m.duration_max or 0.0
    if not has_report_lines and fn.timeout and longest >= _LONGEST_SHARE_TO_WARN * fn.timeout * 1000:
        found.append(("warn", (
            f"The longest run took {human_ms(longest)}, close to its {fn.timeout} s timeout: a slightly slower day "
            f"and runs fail. Give them room: {_longer(fn)}; {_call('performance', fn.name, **range_args)} shows "
            "how many come close."
        )))
    return found


def stats_findings(stats: FunctionStats, *, prices: dict[str, float] | None = None,
                   range_args: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    """
    What a function's numbers over a range say: failed calls (with the errors() call for the same range), throttled
    calls, runs that come close to the timeout or the memory, memory it could do without, and slow cold starts.
    range_args are the since= / until= the next steps carry.
    """
    found = _range_findings(stats, range_args or {})
    if stats.perf is not None:
        found += performance_findings(stats.perf, prices=prices)
    return found
