"""
stats()'s report, and the explorer's range summary: a function's calls, failed and throttled calls, run time, the most
running at once, cold starts, memory and cost over a range of time, then a row per day (or hour) and the cost by part.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from ..._kit.history import _SECONDS_PER_DAY
from ..._kit.fmt import _count, _fmt_dt, _plural, _share, _total, _tz_label, human_money

from ..helpers import _pct, human_ms
from ..models import DailyUsage, FunctionStats, RunSample
from ..analysis.cost import COST_PARTS, function_range_cost
from ..analysis.performance import memory_tight, near_timeout
from ..analysis.stats import busiest_period, failed_calls_worth_warning, run_sample, stats_findings
from ..analysis.text import _window

_LONGEST_SHARE_TO_WARN = 0.9
_UNREAD = "-"
_SAMPLE_SHARE_TO_SAY = 0.9


def _range_text(stats: FunctionStats) -> str:
    """
    'from 2026-10-01 00:00 to 2026-10-15 00:00 UTC (14 days)'.
    """
    window = _window((stats.until - stats.since).total_seconds())
    return f"from {_fmt_dt(stats.since)} to {_fmt_dt(stats.until, zone=True)} ({window})"


def _longest_is_close(stats: FunctionStats) -> bool:
    """
    Whether the Longest card is amber: runs near the timeout by their REPORT lines, or else by CloudWatch's longest.
    """
    perf, fn, m = stats.perf, stats.function, stats.metrics
    if perf is not None and perf.invocations:
        return near_timeout(perf)
    longest = (m.duration_max if m is not None else None) or 0.0
    return bool(fn.timeout) and longest >= _LONGEST_SHARE_TO_WARN * fn.timeout * 1000


def _metric_cards(stats: FunctionStats) -> list[tuple[str, ...]]:
    """
    The cards CloudWatch's numbers give: calls, error rate, throttled calls, average and longest run time, the most
    running at once and the cost.
    """
    m, fn = stats.metrics, stats.function
    if m is None:
        return [("Calls", _UNREAD), ("Est. cost", _UNREAD)]
    has_calls = bool(m.invocations)
    failed = f"{_pct(m.error_rate)} · {_count(round(m.errors))} failed" if has_calls else _UNREAD
    longest = f"{human_ms(m.duration_max)} of {fn.timeout} s" if m.duration_max is not None else _UNREAD
    peak = f"{m.concurrency_max:,.0f}" if m.concurrency_max is not None else _UNREAD
    return [
        ("Calls", _count(round(m.invocations))),
        ("Error rate", failed, "warn" if failed_calls_worth_warning(m) else ""),
        ("Throttled", _count(round(m.throttles)), "warn" if m.throttles >= 1 else ""),
        ("Avg run time", human_ms(m.avg_duration) if has_calls else _UNREAD),
        ("Longest", longest, "warn" if _longest_is_close(stats) else ""),
        ("Most at once", peak),
        ("Est. cost", human_money(_total(stats.cost)) if stats.cost else _UNREAD),
    ]


def _sample_cards(sample: RunSample, *, memory_warning: bool = False, read: str = "") -> list[tuple[str, ...]]:
    """
    The cold starts and memory cards from runs' REPORT lines; `read` labels them when they come from part of the
    range ('of 120 runs read').
    """
    if not sample.runs:
        return []
    cold = _pct(sample.cold_share) + (f" · {human_ms(sample.init_avg)}" if sample.init_avg is not None else "")
    label = f" ({read})" if read else ""
    return [
        (f"Cold starts{label}", cold),
        (f"Memory used{label}", f"{sample.memory_max:,} of {sample.memory:,} MB", "warn" if memory_warning else ""),
    ]


def _stats_cards(stats: FunctionStats) -> _Cards:
    """
    Every card of the range: CloudWatch's numbers and cost, then cold starts and memory from the REPORT lines read.
    """
    perf = stats.perf
    sample = run_sample(perf.invocations if perf is not None else [])
    tight = perf is not None and bool(perf.invocations) and memory_tight(perf)
    return _Cards(_metric_cards(stats) + _sample_cards(sample, memory_warning=tight))


def _period_label(start: datetime, period: int) -> str:
    """
    A row's day ('2026-10-03', in UTC, as CloudWatch counts days) or hour (in the zone reports show times in).
    """
    if period < _SECONDS_PER_DAY:
        return _fmt_dt(start)
    in_utc = start.astimezone(timezone.utc)
    return f"{in_utc:%Y-%m-%d}" if in_utc.hour == in_utc.minute == 0 else f"{in_utc:%Y-%m-%d %H:%M}"


def _period_row(stats: FunctionStats, row: DailyUsage) -> list[Any]:
    period = stats.metrics.period if stats.metrics is not None else _SECONDS_PER_DAY
    cost = _total(function_range_cost(stats.function, row, prices=stats.prices))
    return [
        _period_label(row.start, period),
        _count(round(row.invocations)),
        _count(round(row.errors)),
        _pct(row.errors / row.invocations) if row.invocations else _UNREAD,
        _count(round(row.throttles)),
        human_ms(row.avg_duration) if row.invocations else _UNREAD,
        human_ms(row.duration_max) if row.duration_max is not None else _UNREAD,
        f"{row.concurrency:,.0f}" if row.concurrency is not None else _UNREAD,
        human_money(cost) if cost is not None else _UNREAD,
    ]


def _periods_table(stats: FunctionStats) -> _Table | None:
    """
    A row per day (or hour) with calls in it, oldest first, with a bar of its share of the calls.
    """
    m = stats.metrics
    if m is None or not m.daily:
        return None
    is_hourly = m.period < _SECONDS_PER_DAY
    starts_at_midnight = all(row.start.astimezone(timezone.utc).hour == 0 for row in m.daily)
    when = "Day (UTC)" if starts_at_midnight else "24 h from (UTC)"
    if is_hourly:
        when = f"Hour ({_tz_label()})"
    headers = [when, "Calls", "Failed", "Error rate", "Throttled", "Avg run time", "Longest", "Most at once", "Cost"]
    busiest = busiest_period(m)
    title = "Hour by hour" if is_hourly else "Day by day"
    if busiest is not None:
        title += (f" (the busiest: {_period_label(busiest.start, m.period)}, {_count(round(busiest.invocations))} "
                  "calls; cost is requests and compute)")
    return _Table(headers, [_period_row(stats, row) for row in m.daily],
                  bars=[_share(row.invocations, m.invocations) for row in m.daily], bar_label="Share of calls",
                  title=title)


def _cost_table(stats: FunctionStats) -> _Table | None:
    if not stats.cost:
        return None
    rows = [[COST_PARTS.get(part, part), human_money(value)] for part, value in stats.cost.items()]
    total = _total(stats.cost) or 0.0
    return _Table(["Part", "Est. cost"], rows, bars=[_share(v, total) for v in stats.cost.values()],
                  bar_label="Share", title="What it cost, by part", max_rows=0)


def _sample_note(stats: FunctionStats, limit: int | None) -> _Note | None:
    """
    Where the cold starts and memory come from, when that's less than the whole range.
    """
    perf, m = stats.perf, stats.metrics
    if perf is None or not perf.invocations:
        return None
    read = len(perf.invocations)
    if perf.truncated:
        return _Note(
            f"Cold starts and memory are from the newest {_plural(read, 'run')} (limit={limit:,}), back to "
            f"{_fmt_dt(perf.covered_from, zone=True)}; CloudWatch's numbers cover the whole range. Pass limit= to "
            "read more."
        )
    calls = round(m.invocations) if m is not None else 0
    if read < _SAMPLE_SHARE_TO_SAY * calls:
        return _Note(
            f"Cold starts and memory are from the {_plural(read, 'run')} whose REPORT lines its logs hold for the "
            f"range, of {_count(calls)} calls CloudWatch counted: its log group keeps fewer days, or some runs log "
            "elsewhere."
        )
    return None


def _stats_blocks(stats: FunctionStats, *, call: Callable[..., str], range_args: dict[str, Any],
                  price_basis: str, limit: int | None = 5_000) -> list[Any]:
    """
    stats()'s report, as blocks: the cards, the findings, a row per day (or hour), the cost by part and the next
    steps over the same range.
    """
    fn, m = stats.function, stats.metrics
    has_calls = m is not None and m.invocations > 0
    title = _Title(f"Stats for {fn.name}", " · ".join(filter(None, [_range_text(stats), fn.region])))
    blocks: list[Any] = [title, _stats_cards(stats)]
    if m is not None and not has_calls:
        blocks.append(_Note(f"CloudWatch counted no calls {_range_text(stats)}. {call('function_info')} shows when it "
                            "was last called."))
    sample_note = _sample_note(stats, limit)
    if sample_note is not None:
        blocks.append(sample_note)
    found = stats_findings(stats, prices=stats.prices, range_args=range_args)
    blocks.append(_Findings(found, empty="No failed or throttled calls, and runs stay inside the timeout and memory."))
    for table in (_periods_table(stats), _cost_table(stats) if has_calls else None):
        if table is not None:
            blocks.append(table)
    if has_calls and stats.cost:
        blocks.append(_Note(
            f"Costs are estimates at {price_basis}, before the free tier, without provisioned concurrency or the "
            f"logs CloudWatch keeps. This report read {_plural(stats.metrics_read, 'metric')} from CloudWatch."
        ))
    blocks.append(_Next([
        (call("errors", **range_args), "every failed call in the range, grouped by cause"),
        (call("performance", **range_args), "run times and memory, and the slowest runs"),
        (call("runs", **range_args, sort="-duration"), "the runs themselves, longest first"),
    ]))
    return blocks
