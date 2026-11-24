"""
performance()'s report: a verdict, the numbers that matter as cards, the findings, every run over time with the
timeouts in a lane of their own, how the runs compare with the window before and between versions, how long they take,
cold starts against warm runs, memory (what it has, and per container), the slowest runs and the percentiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone, _Verdict
from ..._kit.charts import _Meter, _Part
from ..._kit.fmt import _count, _fmt_dt, _plural, _tz_label, human_money

from ..helpers import _mb, _ms, _pct, human_ms
from ..models import Invocation, Performance
from ..prices import DAYS_PER_MONTH
from ..analysis.performance import (
    MIN_CONTAINER_RUNS, MIN_FINISHED_TO_JUDGE, NEAR_MEMORY_SHARE, NEAR_TIMEOUT_SHARE, SAME_SHARE, TIME_BAND_SLICES, RunSpan, RunStats, change_share,
    cold_vs_warm, hung_timeouts, is_slower, memory_by_container, memory_to_try, percentile, performance_findings,
    performance_verdict, run_cost, run_groups, run_stats, run_time_bins, short_stream, slice_counts, slowdown, time_slices,
    timed_out,
)
from .run_charts import _ColdWarm, _RunHistogram, _RunMark, _RunTimeline, _Spark

_SLOWEST_SHOWN = 10
_TIMEOUTS_NAMED = 5
_CONTAINERS_SHOWN = 6
_CLEAR_CHANGE = 0.1


@dataclass
class _PerformanceSetup:
    """
    What the report needs from the View that drew it: the window's length in words ('24 hours'), the read's limit,
    how to write a call about the function (`call(command, **kwargs)`), since= for the next steps, the prices and
    how they're described, and whether it's for the explorer (which lists the slowest runs itself).
    """

    window: str
    call: Callable[..., str]
    limit: int | None = None
    since: dict[str, Any] = field(default_factory=dict)
    prices: dict[str, float] | None = None
    price_basis: str = "list prices"
    for_window: bool = False


def _run_kind(run: Invocation, timeout_s: int) -> str:
    if timed_out(run, timeout_s):
        return "timeout"
    if run.status:
        return "failed"
    return "cold" if run.init is not None else "run"


def _run_label(run: Invocation, timeout_s: int) -> str:
    """
    What pointing at a run says: when, how long, how it ended, and its request ID.
    """
    parts = [_fmt_dt(run.time, seconds=True), human_ms(run.duration)]
    if timed_out(run, timeout_s):
        parts.append("timed out")
    elif run.status:
        parts.append(run.status + (f" ({run.error_type})" if run.error_type else ""))
    if run.init is not None:
        parts.append(f"cold start {human_ms(run.init)}")
    return " · ".join(parts + [run.request_id])


def _timeline(perf: Performance) -> _RunTimeline | None:
    """
    Every run by when it ran, over the part of the window the read covered.
    """
    fn = perf.function
    timed = sorted((run for run in perf.invocations if run.time is not None), key=lambda run: run.time)
    if len(timed) < 2:
        return None
    start = perf.covered_from if perf.truncated and perf.covered_from else perf.since
    if perf.until <= start:
        return None
    slices = time_slices(timed, fn.timeout, start, perf.until, slices=TIME_BAND_SLICES)
    counts = slice_counts([run.time for run in timed if run.time is not None], start, perf.until)
    marks = [_RunMark(run.time, run.duration, _run_kind(run, fn.timeout), _run_label(run, fn.timeout)) for run in timed]
    note = f"These are the newest {len(timed):,} runs: the reading stopped there." if perf.truncated else ""
    return _RunTimeline(marks, slices, counts, start, perf.until, fn.timeout,
                        title=f"Every run, by when it ran ({_tz_label()})", note=note)


def _change_cell(now: float | None, before: float | None, *, worse_when_up: bool = True) -> Any:
    """
    How a number changed since the window before: '+23%' red when it got worse by a tenth or more, green when it got
    better, 'same' within SAME_SHARE.
    """
    share = change_share(now, before)
    if share is None:
        return "-"
    if abs(share) < SAME_SHARE:
        return "same"
    text = f"{'+' if share > 0 else '−'}{abs(share):.0%}"
    if abs(share) < _CLEAR_CHANGE:
        return text
    got_worse = share > 0 if worse_when_up else share < 0
    return _Tone(text, "bad" if got_worse else "ok")


def _count_change(now: int, before: int) -> Any:
    if now == before:
        return "same"
    return _Tone(f"{now - before:+,}", "bad" if now > before else "ok")


def _comparison_blocks(perf: Performance, stats: RunStats, setup: _PerformanceSetup, daily_cost: float) -> list[Any]:
    """
    The window against the one before it, number by number, or a note saying why there's nothing to compare.
    """
    before = perf.before
    if before is None:
        return []
    if before.errors:
        return [_Note(f"Couldn't read the {setup.window} before, so nothing is compared with it.", "warn")]
    if not before.invocations:
        return [_Note(f"No runs in the {setup.window} before, so there's nothing to compare with.")]
    fn = perf.function
    was = run_stats(before.invocations, fn.timeout)
    was_cost = run_cost(was, fn, setup.prices) / before.window_days
    rows = [
        ["Runs", _count(was.runs), _count(stats.runs), _change_cell(stats.runs, was.runs, worse_when_up=False)],
        ["Typical run (median)", human_ms(was.median_ms), human_ms(stats.median_ms),
         _change_cell(stats.median_ms, was.median_ms)],
        ["1 in 10 takes (p90)", human_ms(was.p90_ms), human_ms(stats.p90_ms), _change_cell(stats.p90_ms, was.p90_ms)],
        ["Timeouts", _count(was.timeouts), _count(stats.timeouts), _count_change(stats.timeouts, was.timeouts)],
        ["Cold starts", _pct(was.cold_share), _pct(stats.cold_share), _change_cell(stats.cold_share, was.cold_share)],
        ["Memory peak", _mb(was.memory_peak), _mb(stats.memory_peak),
         _change_cell(stats.memory_peak, was.memory_peak)],
        ["Cost per month", human_money(was_cost * DAYS_PER_MONTH), human_money(daily_cost * DAYS_PER_MONTH),
         _change_cell(daily_cost, was_cost)],
    ]
    title = (f"Compared with the {setup.window} before ({_fmt_dt(before.since)} to {_fmt_dt(before.until)}, "
             f"{_plural(was.runs, 'run')})")
    return [_Table(["", "Before", "Now", "Change"], rows, title=title, max_rows=0, sortable=False)]


def _group_blocks(perf: Performance) -> list[Any]:
    """
    The runs by version, or before and since the function's last change, when the window holds both.
    """
    groups = run_groups(perf)
    if len(groups) < 2:
        return []
    slower = slowdown(groups)
    newest = groups[-1]
    rows = []
    for group in groups:
        typical: Any = human_ms(group.stats.median_ms)
        if slower and group is newest:
            typical = _Tone(f"{typical} (+{slower[2]:.0%})", "bad")
        rows.append([group.label, _fmt_dt(group.first), _fmt_dt(group.last), _count(group.stats.runs), typical,
                     human_ms(group.stats.p90_ms), _count(group.stats.timeouts), _mb(group.stats.memory_peak)])
    by_version = all(group.version for group in groups)
    title = "By version" if by_version else "Before and since its last change"
    return [_Table(["Runs of", f"First ({_tz_label()})", "Last", "Runs", "Typical run", "1 in 10", "Timeouts",
                    "Memory peak"], rows, title=title, max_rows=0)]


def _histogram(perf: Performance, stats: RunStats) -> _RunHistogram | None:
    fn = perf.function
    if stats.finished < MIN_FINISHED_TO_JUDGE and not stats.timeouts:
        return None
    bins = run_time_bins(perf.invocations, fn.timeout)
    note = ""
    if hung_timeouts(stats, fn.timeout):
        note = (f"No run took between {human_ms(stats.longest_ms)} and the {fn.timeout} s timeout: the runs that "
                "timed out were stuck, not slow.")
    return _RunHistogram(bins, stats.timeouts, fn.timeout, title="How long the runs that finished take", note=note)


def _cold_warm_block(perf: Performance) -> _ColdWarm | None:
    numbers = cold_vs_warm(perf.invocations, perf.function.timeout)
    if numbers is None:
        return None
    note = (f"{_plural(numbers.cold, 'cold start')} spent {_pct(numbers.start_up_share)} of all the time runs took "
            "starting up.")
    if numbers.cold_median_ms + numbers.start_up_median_ms <= numbers.warm_median_ms:
        note += " A cold run still finishes as soon as a warm one, so start-up isn't what makes runs slow."
    return _ColdWarm(numbers, title="Cold starts against warm runs", note=note)


def _memory_meter(perf: Performance, memory: int) -> _Meter | None:
    """
    The most memory a run used against what the function has, with the size worth trying marked when it's far
    more than runs use.
    """
    used = max((run.max_memory or 0 for run in perf.invocations), default=0)
    if not used or not memory:
        return None
    suggested = memory_to_try(used, memory)
    meter = _Meter([_Part("most a run used", float(used), f"{used:,} MB")], float(memory), f"{memory:,} MB",
                   f"{used:,} MB", title="Memory: the most a run used, of what it has")
    if suggested:
        meter.mark, meter.mark_text = float(suggested), f"{suggested:,} MB would do"
    return meter


def _container_table(perf: Performance) -> _Table | None:
    """
    Memory run after run in each container (log stream), a line each: open when one climbs, folded otherwise.
    """
    containers = [box for box in memory_by_container(perf.invocations) if box.runs >= 3]
    if not containers or max(box.runs for box in containers) < MIN_CONTAINER_RUNS:
        return None
    climbing = any(box.is_climbing for box in containers)
    rows = [[short_stream(box.stream), _count(box.runs), _Spark([float(v) for v in box.used],
                                                                "warn" if box.is_climbing else ""),
             f"{box.first:,} → {box.last:,} MB", _Tone("climbing", "warn") if box.is_climbing else "steady"]
            for box in containers[:_CONTAINERS_SHOWN]]
    more = f", the {_CONTAINERS_SHOWN} busiest of {len(containers):,}" if len(containers) > _CONTAINERS_SHOWN else ""
    return _Table(["Container", "Runs", "Memory, run after run", "First → last", ""], rows,
                  title=f"Memory per container (log stream){more}", max_rows=0, collapsed=not climbing,
                  sortable=False)


def _slowest_blocks(perf: Performance, stats: RunStats) -> list[Any]:
    """
    The slowest runs that finished, against the typical one, then the runs that timed out by request ID.
    """
    fn = perf.function
    finished = [run for run in perf.invocations if not timed_out(run, fn.timeout)]
    slowest = sorted(finished, key=lambda run: run.duration, reverse=True)[:_SLOWEST_SHOWN]
    blocks: list[Any] = []
    if slowest:
        typical = stats.median_ms or 0
        blocks.append(_Table(
            [f"Time ({_tz_label()})", "Request ID", "Run time", "× typical", "Memory used", "Cold start", "Status"],
            [[_fmt_dt(run.time, seconds=True), run.request_id, _ms(run.duration),
              f"{run.duration / typical:.1f}×" if typical else "-", _mb(run.max_memory),
              _ms(run.init) if run.init is not None else "-",
              _Tone(run.status + (f" ({run.error_type})" if run.error_type else ""), "bad") if run.status else "ok"]
             for run in slowest],
            title="The slowest runs that finished", max_rows=0,
        ))
    timeouts = [run for run in perf.invocations if timed_out(run, fn.timeout)]
    if timeouts:
        named = ", ".join(f"{run.request_id} at {_fmt_dt(run.time)}" for run in timeouts[:_TIMEOUTS_NAMED])
        more = f", and {len(timeouts) - _TIMEOUTS_NAMED:,} more" if len(timeouts) > _TIMEOUTS_NAMED else ""
        blocks.append(_Note(f"{_plural(len(timeouts), 'run')} timed out, newest first: {named}{more}."))
    return blocks


def _percentile_table(perf: Performance) -> _Table:
    fn, runs = perf.function, perf.invocations
    durations = [run.duration for run in runs if not timed_out(run, fn.timeout)]
    used = [float(run.max_memory) for run in runs if run.max_memory]
    starts = [run.init for run in runs if run.init is not None]

    def spread(values: list[float], show: Callable[[float | None], str]) -> list[str]:
        return [show(percentile(values, q)) for q in (50, 90, 99)] + [show(max(values) if values else None)]

    rows = [["Run time (runs that finished)", *spread(durations, _ms)], ["Memory used", *spread(used, _mb)]]
    if starts:
        rows.append([f"Start-up of cold starts ({_plural(len(starts), 'run')})", *spread(starts, _ms)])
    return _Table(["Measure", "Median", "p90", "p99", "Most"], rows, max_rows=0, collapsed=True,
                  title="Percentiles (billed time matches run time to the millisecond, so it's left out)")


def _perf_next(perf: Performance, stats: RunStats, setup: _PerformanceSetup) -> _Next:
    fn = perf.function
    timeouts = [run for run in perf.invocations if timed_out(run, fn.timeout)]
    steps: list[tuple[str, str]] = []
    if timeouts:
        steps.append((setup.call("logs", request_id=timeouts[0].request_id, **setup.since),
                      "where a run that timed out stopped"))
    else:
        slowest = max(perf.invocations, key=lambda run: run.duration)
        steps.append((setup.call("logs", request_id=slowest.request_id, **setup.since),
                      "everything the slowest run logged"))
    slower = slowdown(run_groups(perf))
    if hung_timeouts(stats, fn.timeout):
        steps.append((setup.call("how_it_works"), "the calls it makes without a timeout"))
    elif slower and slower[1].version.isdigit() and slower[0].version.isdigit():
        steps.append((setup.call("diff", slower[0].version, slower[1].version), "what changed in the slower version"))
    elif timeouts:
        steps.append((setup.call("errors", **setup.since), "every error, grouped by cause"))
    steps.append((setup.call("function_info"), "its settings, triggers and last 30 days"))
    return _Next(steps[:3])


def _perf_cards(perf: Performance, stats: RunStats, daily_cost: float, groups: list[RunSpan]) -> _Cards:
    """
    The numbers that matter, each card amber only when a finding in the report is about it.
    """
    fn, runs = perf.function, perf.invocations
    memory = max((run.memory for run in runs if run.memory), default=fn.memory)
    before = run_stats(perf.before.invocations, fn.timeout) if perf.before and perf.before.invocations else None
    slower = (before is not None and is_slower(stats, before)) or slowdown(groups) is not None
    share = change_share(stats.median_ms, before.median_ms) if before else None
    typical = human_ms(stats.median_ms) + (f" ({'+' if share > 0 else '−'}{abs(share):.0%})"
                                           if share is not None and abs(share) >= SAME_SHARE else "")
    out_of_memory = any((run.error_type or "").endswith("OutOfMemory") for run in runs)
    memory_warning = out_of_memory or stats.memory_peak >= NEAR_MEMORY_SHARE * memory
    cards: list[tuple[str, ...]] = [("Runs", _count(stats.runs)), ("Typical run", typical, "warn" if slower else "")]
    if stats.timeouts:
        cards.append(("Timeouts", f"{stats.timeouts:,} · {_pct(stats.timeout_share)}", "warn"))
    else:
        near = (stats.p99_ms or 0) >= NEAR_TIMEOUT_SHARE * fn.timeout * 1000
        cards.append(("Slowest", f"{human_ms(stats.longest_ms)} of {fn.timeout} s", "warn" if near else ""))
    cards.append(("Memory peak", f"{stats.memory_peak:,} of {memory:,} MB", "warn" if memory_warning else ""))
    cold = _pct(stats.cold_share) + (f" · +{human_ms(stats.start_up_ms)}" if stats.start_up_ms else "")
    cards.append(("Cold starts", cold))
    cards.append(("Cost", f"{human_money(daily_cost * DAYS_PER_MONTH)}/month"))
    return _Cards(cards)


def _title(perf: Performance, setup: _PerformanceSetup) -> _Title:
    fn = perf.function
    compared = f", compared with the {setup.window} before" if perf.before and perf.before.invocations else ""
    return _Title(
        f"Performance of {fn.name}",
        f"from {_plural(len(perf.invocations), 'REPORT line')} (one per run) in the last {setup.window}{compared} · "
        f"log group {perf.log_group} · {fn.memory:,} MB, {fn.timeout} s timeout · cost at {setup.price_basis}, before "
        "the free tier",
    )


def _empty_blocks(perf: Performance, setup: _PerformanceSetup, notes: list[Any]) -> list[Any]:
    blocks: list[Any] = [_title(perf, setup), *notes]
    if "logs" not in perf.errors:
        blocks.append(_Note(
            f"No REPORT lines in the last {setup.window}: it wasn't called, or its logs go somewhere else. "
            f"{setup.call('function_info')} shows when it was last called."
        ))
    return blocks


def _verdict(perf: Performance, window: str) -> _Verdict | None:
    verdict = performance_verdict(perf, window)
    return _Verdict(verdict.headline, verdict.detail, verdict.tone) if verdict else None


def performance_blocks(perf: Performance, setup: _PerformanceSetup, notes: list[Any]) -> list[Any]:
    """
    performance()'s report, as blocks; `notes` say what couldn't be read. With setup.for_window, the slowest runs are
    left out (the explorer lists them as runs to click).
    """
    if not perf.invocations:
        return _empty_blocks(perf, setup, notes)
    fn = perf.function
    stats = run_stats(perf.invocations, fn.timeout)
    daily_cost = run_cost(stats, fn, setup.prices) / perf.window_days
    groups = run_groups(perf)
    found = performance_findings(perf, prices=setup.prices, window=setup.window)
    blocks: list[Any] = [_title(perf, setup), _verdict(perf, setup.window), _perf_cards(perf, stats, daily_cost, groups),
                         *notes]
    if perf.truncated:
        blocks.append(_Note(
            f"Reading stopped at the newest {_count(setup.limit)} runs, back to {_fmt_dt(perf.covered_from, zone=True)}: "
            "pass limit= to read more."
        ))
    memory = max((run.memory for run in perf.invocations if run.memory), default=fn.memory)
    blocks.append(_Findings(found, empty=(
        f"Runs stay well inside the {fn.timeout} s timeout and the {memory:,} MB of memory, and the memory isn't "
        "far more than they use.")))
    blocks += [_timeline(perf), *_comparison_blocks(perf, stats, setup, daily_cost), *_group_blocks(perf),
               _histogram(perf, stats), _cold_warm_block(perf), _memory_meter(perf, memory), _container_table(perf)]
    if not setup.for_window:
        blocks += _slowest_blocks(perf, stats)
    blocks += [_percentile_table(perf), _perf_next(perf, stats, setup)]
    return [block for block in blocks if block is not None]
