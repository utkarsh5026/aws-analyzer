"""
Run times and memory: percentiles, the numbers a performance report leads with, how they changed (since the window
before, between versions or around the function's last change), cold starts against warm runs, memory per container,
a memory size to suggest, and the findings about all of it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from ..._kit.fmt import _fmt_dt, _plural, human_money

from ..prices import DAYS_PER_MONTH, LAMBDA_PRICES
from ..helpers import MEMORY_STEPS, _pct, human_ms
from ..models import Function, Invocation, Performance
from .text import _cli
from .findings import _longer, _next_memory
from .causes import Verdict


TIMED_OUT_SHARE = 0.99
NEAR_TIMEOUT_SHARE = 0.8
NEAR_MEMORY_SHARE = 0.9
HUNG_SHARE = 1 / 3
MIN_FINISHED_TO_JUDGE = 5
MIN_COMPARED_RUNS = 10
MIN_GROUP_RUNS = 5
SLOWER_SHARE = 0.2
SLOWER_MS = 100.0
SAME_SHARE = 0.05
MIN_CONTAINER_RUNS = 9
CLIMB_SHARE = 0.2
CLIMB_MB = 16
WORRYING_MEMORY_SHARE = 0.7
MIN_COLD_RUNS = 20
COLD_SHARE = 0.05
SLOW_START_MS = 500
SAVING_WORTH_WARNING = 10.0
TIME_BAND_SLICES = 12
RUN_TIME_SLOTS = 10
NICE_STEPS = (1, 2, 5)


def percentile(values: Iterable[float], q: float) -> float | None:
    """
    The nearest-rank percentile (q from 0 to 100) of the values; None when there are none.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


def timed_out(run: Invocation, timeout_s: int) -> bool:
    """
    Whether a run hit the timeout: Lambda says so on newer runtimes (Status: timeout), and on older ones its run
    time is the timeout.
    """
    return run.status == "timeout" or (timeout_s > 0 and run.duration >= TIMED_OUT_SHARE * timeout_s * 1000)


@dataclass
class RunStats:
    """
    The numbers a set of runs comes down to. Run times are of the runs that finished, since a run that timed out
    only says how long the timeout is.
    """

    runs: int = 0
    finished: int = 0
    timeouts: int = 0
    median_ms: float | None = None
    p90_ms: float | None = None
    p99_ms: float | None = None
    longest_ms: float | None = None
    memory_peak: int = 0
    cold_starts: int = 0
    start_up_ms: float | None = None
    billed_ms: float = 0.0

    @property
    def timeout_share(self) -> float:
        return self.timeouts / self.runs if self.runs else 0.0

    @property
    def cold_share(self) -> float:
        return self.cold_starts / self.runs if self.runs else 0.0


def run_stats(runs: list[Invocation], timeout_s: int) -> RunStats:
    """
    The runs' count, timeouts, run times (median, 1 in 10, 1 in 100 and the longest, of the runs that finished),
    the most memory one used, cold starts with their average start-up, and the time billed.
    """
    finished = [run.duration for run in runs if not timed_out(run, timeout_s)]
    starts = [run.init for run in runs if run.init is not None]
    return RunStats(
        runs=len(runs), finished=len(finished), timeouts=len(runs) - len(finished),
        median_ms=percentile(finished, 50), p90_ms=percentile(finished, 90), p99_ms=percentile(finished, 99),
        longest_ms=max(finished, default=None), memory_peak=max((run.max_memory or 0 for run in runs), default=0),
        cold_starts=len(starts), start_up_ms=sum(starts) / len(starts) if starts else None,
        billed_ms=sum(run.billed for run in runs),
    )


def run_cost(stats: RunStats, fn: Function, prices: dict[str, float] | None = None) -> float:
    """
    What the runs cost in USD at list prices (or `prices`): the requests and the memory x billed time, before the
    free tier.
    """
    rates = {**LAMBDA_PRICES, **(prices or {})}
    gb_second = rates["gb_second_arm" if fn.arm else "gb_second"]
    compute = stats.billed_ms / 1000 * fn.memory / 1024 * gb_second
    return compute + stats.runs * rates["request"] / 1_000_000


def change_share(now: float | None, before: float | None) -> float | None:
    """
    How much `now` differs from `before`, as a share of it (0.23 is 23% more); None when either is missing or before
    is zero.
    """
    if now is None or not before:
        return None
    return (now - before) / before


def is_slower(now: RunStats, before: RunStats) -> bool:
    """
    Whether the typical run got slower by enough to act on: a fifth or more, and at least SLOWER_MS, with enough
    finished runs on both sides to trust their medians.
    """
    enough_runs = min(now.finished, before.finished) >= MIN_COMPARED_RUNS
    share = change_share(now.median_ms, before.median_ms)
    if not enough_runs or share is None:
        return False
    return share >= SLOWER_SHARE and (now.median_ms or 0) - (before.median_ms or 0) >= SLOWER_MS


def hung_timeouts(stats: RunStats, timeout_s: int) -> bool:
    """
    Whether the runs that timed out were stuck rather than slow: every run that finished took under a third of the
    timeout, so nothing ran close to it.
    """
    if not stats.timeouts or stats.finished < MIN_FINISHED_TO_JUDGE or stats.longest_ms is None:
        return False
    return stats.longest_ms < HUNG_SHARE * timeout_s * 1000


@dataclass
class TimeSlice:
    """
    A slice of the window: when it starts and ends, and the median and 1-in-10 run time of the runs that finished
    in it.
    """

    start: datetime
    end: datetime
    runs: int
    median_ms: float
    p90_ms: float


def time_slices(runs: list[Invocation], timeout_s: int, start: datetime, end: datetime,
                *, slices: int = TIME_BAND_SLICES) -> list[TimeSlice]:
    """
    The window cut into `slices` equal parts, each with the median and 1-in-10 run time of the runs that finished in
    it; parts with no finished run are left out.
    """
    span = (end - start) / slices
    if span.total_seconds() <= 0:
        return []
    buckets: dict[int, list[float]] = defaultdict(list)
    for run in runs:
        if run.time is None or timed_out(run, timeout_s):
            continue
        index = min(max(int((run.time - start) / span), 0), slices - 1)
        buckets[index].append(run.duration)
    return [TimeSlice(start + span * index, start + span * (index + 1), len(values),
                      percentile(values, 50) or 0.0, percentile(values, 90) or 0.0)
            for index, values in sorted(buckets.items())]


def slice_counts(times: list[datetime], start: datetime, end: datetime, *,
                 slices: int = TIME_BAND_SLICES) -> list[int]:
    """
    How many of `times` fall in each of the window's `slices` equal parts.
    """
    counts = [0] * slices
    span = (end - start) / slices
    if span.total_seconds() <= 0:
        return counts
    for moment in times:
        counts[min(max(int((moment - start) / span), 0), slices - 1)] += 1
    return counts


def _nice_step(longest_ms: float, slots: int) -> float:
    """
    The smallest of 1, 2 or 5 times a power of ten that fits the longest run time in `slots` bins.
    """
    rough = max(longest_ms / slots, 1.0)
    power = 10 ** math.floor(math.log10(rough))
    return next((step * power for step in NICE_STEPS if step * power >= rough), 10 * power)


@dataclass
class RunTimeBin:
    """
    How many finished runs took from `low` up to `high` ms.
    """

    low: float
    high: float
    runs: int


def run_time_bins(runs: list[Invocation], timeout_s: int, *, slots: int = RUN_TIME_SLOTS) -> list[RunTimeBin]:
    """
    The finished runs counted in equal bins of a round width (100 ms, 2 s, ...) from the bin of the fastest to the
    bin of the longest; the runs that timed out aren't in them.
    """
    finished = [run.duration for run in runs if not timed_out(run, timeout_s)]
    if not finished:
        return []
    step = _nice_step(max(finished), slots)
    first, last = int(min(finished) // step), int(max(finished) // step)
    bins = [RunTimeBin(i * step, (i + 1) * step, 0) for i in range(first, last + 1)]
    for duration in finished:
        bins[int(duration // step) - first].runs += 1
    return bins


@dataclass
class RunSpan:
    """
    The runs of one version, or of the time before or after the function last changed, with their numbers.
    """

    label: str
    first: datetime | None
    last: datetime | None
    stats: RunStats
    version: str = ""


def _group(label: str, runs: list[Invocation], timeout_s: int, version: str = "") -> RunSpan:
    times = [run.time for run in runs if run.time is not None]
    return RunSpan(label, min(times, default=None), max(times, default=None), run_stats(runs, timeout_s), version)


def _version_order(version: str) -> tuple[int, int, str]:
    return (0, int(version), "") if version.isdigit() else (1, 0, version)


def run_groups(perf: Performance) -> list[RunSpan]:
    """
    The runs split by what ran them, oldest first: by version when the log streams name two or more (a function
    called through a version or alias), else at the function's last change when that fell inside the window with
    MIN_GROUP_RUNS on each side; [] when neither splits them.
    """
    fn, runs = perf.function, perf.invocations
    by_version: dict[str, list[Invocation]] = defaultdict(list)
    for run in runs:
        if run.version:
            by_version[run.version].append(run)
    if len(by_version) >= 2:
        return [_group(f"version {version}", by_version[version], fn.timeout, version)
                for version in sorted(by_version, key=_version_order)]
    changed = fn.last_modified
    if changed is None or not perf.since < changed < perf.until:
        return []
    before = [run for run in runs if run.time is not None and run.time < changed]
    after = [run for run in runs if run.time is not None and run.time >= changed]
    if min(len(before), len(after)) < MIN_GROUP_RUNS:
        return []
    when = _fmt_dt(changed)
    return [_group(f"before its change at {when}", before, fn.timeout),
            _group(f"since its change at {when}", after, fn.timeout)]


@dataclass
class ColdWarm:
    """
    Cold starts against warm runs: how many of each, their median run time, the median start-up a cold one spends
    first, and the share of all the time runs took that went on starting up.
    """

    cold: int
    warm: int
    warm_median_ms: float
    cold_median_ms: float
    start_up_median_ms: float
    start_up_share: float


def cold_vs_warm(runs: list[Invocation], timeout_s: int) -> ColdWarm | None:
    """
    Cold starts against warm runs, among the runs that finished; None without both.
    """
    finished = [run for run in runs if not timed_out(run, timeout_s)]
    cold = [run for run in finished if run.init is not None]
    warm = [run for run in finished if run.init is None]
    if not cold or not warm:
        return None
    start_ups = [run.init or 0.0 for run in cold]
    total = sum(run.duration for run in finished) + sum(start_ups)
    return ColdWarm(len(cold), len(warm), percentile([run.duration for run in warm], 50) or 0.0,
                    percentile([run.duration for run in cold], 50) or 0.0, percentile(start_ups, 50) or 0.0,
                    sum(start_ups) / total if total else 0.0)


@dataclass
class ContainerMemory:
    """
    The memory each run used in one container (log stream), oldest first.
    """

    stream: str
    used: list[int] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return len(self.used)

    @property
    def first(self) -> int:
        return self.used[0] if self.used else 0

    @property
    def last(self) -> int:
        return self.used[-1] if self.used else 0

    @property
    def is_climbing(self) -> bool:
        """
        Whether its memory grows run after run: each third of its runs used more than the one before, and the last
        third at least a fifth (and CLIMB_MB) more than the first.
        """
        if self.runs < MIN_CONTAINER_RUNS:
            return False
        third = self.runs // 3
        early, middle, late = (percentile(part, 50) or 0 for part in (
            self.used[:third], self.used[third:-third], self.used[-third:]))
        grew_enough = late - early >= max(CLIMB_MB, CLIMB_SHARE * early)
        return early < middle < late and grew_enough

    @property
    def mb_per_run(self) -> float:
        return (self.last - self.first) / (self.runs - 1) if self.runs > 1 else 0.0


def memory_by_container(runs: list[Invocation]) -> list[ContainerMemory]:
    """
    Each container's runs' memory, oldest first: the containers whose memory climbs first, then the busiest.
    """
    by_stream: dict[str, list[Invocation]] = defaultdict(list)
    for run in runs:
        if run.stream and run.max_memory and run.time is not None:
            by_stream[run.stream].append(run)
    containers = [ContainerMemory(stream, [run.max_memory for run in sorted(group, key=lambda run: run.time)])
                  for stream, group in by_stream.items()]
    return sorted(containers, key=lambda box: (not box.is_climbing, -box.runs, box.stream))


def short_stream(stream: str) -> str:
    """
    A log stream's name as a container is told apart: its version and the last 6 characters of its ID
    ('[$LATEST]…0000a1').
    """
    head, _, tail = stream.rpartition("]")
    version = head[head.find("["):] + "]" if "[" in head else ""
    return f"{version}…{tail[-6:]}" if len(tail) > 6 else stream


def suggest_memory(max_used: float, memory: int, headroom: float = 1.3) -> int | None:
    """
    The smallest of MEMORY_STEPS that leaves 30% room above the most memory a run used, when that's less than
    the function has; None when what it has is about right.
    """
    need = max_used * headroom
    step = next((s for s in MEMORY_STEPS if s >= need), None)
    return step if step is not None and step < memory else None


def memory_to_try(max_used: float, memory: int) -> int | None:
    """
    The memory size worth trying when runs use much less than the function has: suggest_memory's, but at least a
    quarter of what it has, since less memory is also less CPU; None when what it has is about right.
    """
    suggested = suggest_memory(max_used, memory)
    if not suggested:
        return None
    at_least_a_quarter = next((s for s in MEMORY_STEPS if s >= memory / 4), suggested)
    suggested = max(suggested, at_least_a_quarter)
    return suggested if suggested < memory else None


def _verdict_headline(stats: RunStats, timeout_s: int) -> tuple[str, str]:
    """
    The verdict's first sentence and tone, from the timeouts and how close the slow runs come to the timeout.
    """
    typical = f"Typical run {human_ms(stats.median_ms)}" if stats.median_ms is not None else "No run finished"
    longest = human_ms(stats.longest_ms)
    if stats.timeouts and hung_timeouts(stats, timeout_s):
        return (f"{typical}. {_plural(stats.timeouts, 'run')} hung until the {timeout_s} s timeout; every other run "
                f"finished within {longest}.", "warn")
    if stats.timeouts:
        tone = "bad" if stats.timeout_share >= 0.05 else "warn"
        return (f"{typical}, but {_plural(stats.timeouts, 'run')} ({_pct(stats.timeout_share)}) hit the "
                f"{timeout_s} s timeout.", tone)
    if (stats.p99_ms or 0) >= NEAR_TIMEOUT_SHARE * timeout_s * 1000:
        return f"{typical}, but 1 run in 100 takes {human_ms(stats.p99_ms)}, close to the {timeout_s} s timeout.", "warn"
    return f"{typical}; the slowest took {longest}, well inside the {timeout_s} s timeout.", "ok"


def _since_before(now: RunStats, before: RunStats | None, window: str) -> str:
    """
    The verdict's second sentence: how the typical run compares with the window before.
    """
    if before is None or min(now.finished, before.finished) < MIN_COMPARED_RUNS:
        return ""
    share = change_share(now.median_ms, before.median_ms)
    if share is None:
        return ""
    medians = f"median {human_ms(before.median_ms)} → {human_ms(now.median_ms)}"
    if abs(share) < SAME_SHARE:
        return f"About as fast as the {window} before ({medians})."
    word = "Slower" if share > 0 else "Faster"
    return f"{word} than the {window} before: {medians} ({'+' if share > 0 else '−'}{abs(share):.0%})."


def performance_verdict(perf: Performance, window: str) -> Verdict | None:
    """
    The report's answer: the typical run time and what's wrong at the edges (runs that hung or hit the timeout, or
    slow runs near it), then how it compares with the window before. `window` names the length of time ('24
    hours'). None without runs.
    """
    if not perf.invocations:
        return None
    timeout_s = perf.function.timeout
    stats = run_stats(perf.invocations, timeout_s)
    before = run_stats(perf.before.invocations, timeout_s) if perf.before and perf.before.invocations else None
    headline, tone = _verdict_headline(stats, timeout_s)
    detail = _since_before(stats, before, window)
    if tone == "ok" and before is not None and is_slower(stats, before):
        tone = "warn"
    return Verdict(headline, detail, tone)


def _hang_or_timeout_finding(perf: Performance, stats: RunStats) -> tuple[str, str] | None:
    """
    Runs that hung until the timeout (a longer timeout won't help), runs that hit it, or slow runs close to it.
    """
    fn, name = perf.function, perf.function.name
    timeouts = [run for run in perf.invocations if timed_out(run, fn.timeout)]
    one, who = ("it", "it") if len(timeouts) == 1 else ("them", "they")
    if timeouts and hung_timeouts(stats, fn.timeout):
        return ("warn", (
            f"{_plural(len(timeouts), 'run')} hung until the {fn.timeout} s timeout: every other run finished within "
            f"{human_ms(stats.longest_ms)}, so {'it was' if len(timeouts) == 1 else 'they were'} waiting on something "
            "that never answered rather than slow, and a longer timeout would only cost more. Find what "
            f"{who} waited on: how_it_works('{name}') shows calls made without a timeout, and "
            f"logs('{name}', request_id='{timeouts[0].request_id}') shows where one stopped."
        ))
    if timeouts:
        return ("warn", (
            f"{_plural(len(timeouts), 'run')} of {len(perf.invocations):,} timed out at the {fn.timeout} s limit: "
            f"Lambda stopped {one} part way. If runs need longer: {_longer(fn)}; "
            f"logs('{name}', request_id='{timeouts[0].request_id}') shows one of them."
        ))
    if fn.timeout and (stats.p99_ms or 0) >= NEAR_TIMEOUT_SHARE * fn.timeout * 1000:
        return ("warn", (
            f"1 run in 100 takes {human_ms(stats.p99_ms)} or more, close to its {fn.timeout} s timeout: on a slightly "
            f"slower day they fail. Give them room: {_longer(fn)}."
        ))
    return None


def _monthly_compute(perf: Performance, memory: int, prices: dict[str, float]) -> float:
    rate = prices["gb_second_arm" if perf.function.arm else "gb_second"]
    billed_seconds = sum(run.billed for run in perf.invocations) / 1000
    return billed_seconds * memory / 1024 * rate * DAYS_PER_MONTH / perf.window_days


def _memory_size_finding(perf: Performance, prices: dict[str, float]) -> tuple[str, str] | None:
    """
    Runs killed for memory, runs close to the limit, or memory far more than the runs use (and what less saves).
    """
    fn, runs, cli = perf.function, perf.invocations, _cli(perf.function)
    memory = max((run.memory for run in runs if run.memory), default=fn.memory)
    max_used = max((run.max_memory for run in runs), default=0)
    out_of_memory = [run for run in runs if (run.error_type or "").endswith("OutOfMemory")]
    more = f"aws lambda update-function-configuration {cli} --memory-size {_next_memory(memory)}"
    if out_of_memory:
        return ("warn", (
            f"{_plural(len(out_of_memory), 'run')} ran out of memory at {memory:,} MB: Lambda killed "
            f"{'it' if len(out_of_memory) == 1 else 'them'}. Give it more: {more}."
        ))
    if not max_used or not memory:
        return None
    if max_used >= NEAR_MEMORY_SHARE * memory:
        return ("warn", (
            f"A run used {max_used:,} of its {memory:,} MB: close to the limit, where Lambda kills the run. Give it "
            f"more: {more}."
        ))
    suggested = memory_to_try(max_used, memory)
    if not suggested:
        return None
    saving = _monthly_compute(perf, memory, prices) * (1 - suggested / memory)
    return ("warn" if saving >= SAVING_WORTH_WARNING else "info", (
        f"Its runs used at most {max_used:,} of the {memory:,} MB it has: {suggested:,} MB still leaves room, "
        f"and would cut its compute cost by up to {_pct(1 - suggested / memory)} (about "
        f"{human_money(saving)}/month), if runs don't get slower: memory also sets the CPU. Try it and "
        f"compare: aws lambda update-function-configuration {cli} --memory-size {suggested}, then "
        f"performance('{fn.name}') a day later."
    ))


def slowdown(groups: list[RunSpan]) -> tuple[RunSpan, RunSpan, float] | None:
    """
    The newest group of runs and the one before it, with how much slower its typical run is, when that's a fifth or
    more (and SLOWER_MS) with MIN_GROUP_RUNS finished runs on each side; None otherwise.
    """
    if len(groups) < 2:
        return None
    older, newer = groups[-2], groups[-1]
    enough = min(older.stats.finished, newer.stats.finished) >= MIN_GROUP_RUNS
    share = change_share(newer.stats.median_ms, older.stats.median_ms)
    gap = (newer.stats.median_ms or 0) - (older.stats.median_ms or 0)
    if not enough or share is None or share < SLOWER_SHARE or gap < SLOWER_MS:
        return None
    return older, newer, share


def _group_finding(perf: Performance) -> tuple[str, str] | None:
    """
    Runs that got slower with the newest version, or since the function last changed.
    """
    slower = slowdown(run_groups(perf))
    if slower is None:
        return None
    older, newer, share = slower
    name = perf.function.name
    medians = f"median {human_ms(older.stats.median_ms)} → {human_ms(newer.stats.median_ms)}"
    if newer.version and older.version.isdigit() and newer.version.isdigit():
        what, look = (f"with version {newer.version}",
                      f"diff('{name}', '{older.version}', '{newer.version}') shows what changed between them.")
    else:
        what, look = (newer.label.replace("since its", "after its"),
                      f"function_info('{name}') shows its settings now, and diff('{name}') what changed in its code.")
    return ("warn", f"Runs got {share:.0%} slower {what} ({medians}): {look}")


def _before_finding(perf: Performance, stats: RunStats, window: str) -> tuple[str, str] | None:
    """
    The typical run slower than in the window before.
    """
    if perf.before is None or not perf.before.invocations:
        return None
    before = run_stats(perf.before.invocations, perf.function.timeout)
    if not is_slower(stats, before):
        return None
    share = change_share(stats.median_ms, before.median_ms) or 0.0
    slow = ""
    if before.p90_ms and stats.p90_ms:
        slow = f", and 1 run in 10 from {human_ms(before.p90_ms)} to {human_ms(stats.p90_ms)}"
    return ("warn", (
        f"Runs are {share:.0%} slower than the {window} before: the median went from {human_ms(before.median_ms)} to "
        f"{human_ms(stats.median_ms)}{slow}. Nothing in the function changed in that time, so look at what it calls "
        f"or reads: logs('{perf.function.name}') shows what its runs did."
    ))


def _container_finding(perf: Performance) -> tuple[str, str] | None:
    """
    Memory that climbs run after run in one container: something kept between runs keeps growing.
    """
    climbing = [box for box in memory_by_container(perf.invocations) if box.is_climbing]
    if not climbing:
        return None
    box, fn = climbing[0], perf.function
    memory = fn.memory or 1
    level = "warn" if box.last >= WORRYING_MEMORY_SHARE * memory else "info"
    left = ""
    if box.mb_per_run > 0:
        runs_left = math.ceil((memory - box.last) / box.mb_per_run)
        left = (f" At about {box.mb_per_run:,.1f} MB a run it reaches the {memory:,} MB limit in about "
                f"{runs_left:,} more runs, and Lambda kills that run.")
    others = f" ({_plural(len(climbing) - 1, 'other container')} too)" if len(climbing) > 1 else ""
    return (level, (
        f"Memory climbs run after run in container {short_stream(box.stream)}: from {box.first:,} to {box.last:,} MB "
        f"over {box.runs:,} runs{others}. Something kept between runs grows, usually a list, cache or client made at "
        f"module level that each run adds to.{left} how_it_works('{fn.name}') shows what its code keeps at module "
        "level."
    ))


def _cold_finding(perf: Performance) -> tuple[str, str] | None:
    """
    Cold starts frequent and slow enough to be worth making faster.
    """
    runs, cold = perf.invocations, perf.cold_starts
    if not cold or len(runs) < MIN_COLD_RUNS:
        return None
    share = len(cold) / len(runs)
    average = sum(run.init or 0 for run in cold) / len(cold)
    if share < COLD_SHARE or average < SLOW_START_MS:
        return None
    return ("info", (
        f"{_pct(share)} of runs were cold starts, each spending about {human_ms(average)} starting up first. "
        "SnapStart (Java 11+, Python 3.12+, .NET 8+), provisioned concurrency, or a smaller package "
        f"(code('{perf.function.name}') shows what's in it) make them faster."
    ))


def performance_findings(perf: Performance, prices: dict[str, float] | None = None, *,
                         window: str = "24 hours") -> list[tuple[str, str]]:
    """
    What the run times and memory say: runs that hung until the timeout, timed out or came close to it, runs out of
    memory or close to it, memory that's much more than the runs use (and what less would save), runs slower than
    in the window before or since a new version or change, memory that climbs in a container, and slow cold starts.
    `window` names the length of time read ('24 hours'), for the comparison with the window before.
    """
    if not perf.invocations:
        return []
    rates = {**LAMBDA_PRICES, **(prices or {})}
    stats = run_stats(perf.invocations, perf.function.timeout)
    group = _group_finding(perf)
    found = [
        _hang_or_timeout_finding(perf, stats),
        _memory_size_finding(perf, rates),
        group,
        None if group else _before_finding(perf, stats, window),
        _container_finding(perf),
        _cold_finding(perf),
    ]
    return [finding for finding in found if finding is not None]
