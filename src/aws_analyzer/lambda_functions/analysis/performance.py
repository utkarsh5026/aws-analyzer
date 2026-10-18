"""
Run times and memory: percentiles, a memory size to suggest, and the findings about both.
"""

from __future__ import annotations

import math
from typing import Iterable

from ..._kit.fmt import _plural, human_money

from ..prices import DAYS_PER_MONTH, LAMBDA_PRICES
from ..helpers import MEMORY_STEPS, _pct, human_ms
from ..models import Performance
from .text import _cli
from .findings import _longer, _next_memory


def percentile(values: Iterable[float], q: float) -> float | None:
    """
    The nearest-rank percentile (q from 0 to 100) of the values; None when there are none.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


def suggest_memory(max_used: float, memory: int, headroom: float = 1.3) -> int | None:
    """
    The smallest of MEMORY_STEPS that leaves 30% room above the most memory a run used, when that's less than
    the function has; None when what it has is about right.
    """
    need = max_used * headroom
    step = next((s for s in MEMORY_STEPS if s >= need), None)
    return step if step is not None and step < memory else None


def performance_findings(perf: Performance, prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """
    What the run times and memory say: timeouts, runs close to the timeout or out of memory, memory that's
    much more than the runs use (and what less would save), and slow cold starts.
    """
    runs = perf.invocations
    if not runs:
        return []
    prices = {**LAMBDA_PRICES, **(prices or {})}
    fn = perf.function
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    durations = [r.duration for r in runs]
    memory = max((r.memory for r in runs if r.memory), default=fn.memory)
    max_used = max((r.max_memory for r in runs), default=0)
    timeouts = [r for r in runs if r.status == "timeout"]
    out_of_memory = [r for r in runs if (r.error_type or "").endswith("OutOfMemory")]
    p99 = percentile(durations, 99) or 0.0
    if timeouts:
        found.append(("warn", (
            f"{_plural(len(timeouts), 'run')} of {len(runs):,} timed out at the {fn.timeout} s limit: Lambda stopped "
            f"{'it' if len(timeouts) == 1 else 'them'} part way. If runs need longer: {_longer(fn)}; "
            f"logs('{name}', request_id='{timeouts[0].request_id}') shows one of them."
        )))
    elif fn.timeout and p99 >= 0.8 * fn.timeout * 1000:
        found.append(("warn", (
            f"1 run in 100 takes {human_ms(p99)} or more, close to its {fn.timeout} s timeout: on a slightly slower "
            f"day they fail. Give them room: {_longer(fn)}."
        )))
    if out_of_memory:
        found.append(("warn", (
            f"{_plural(len(out_of_memory), 'run')} ran out of memory at {memory:,} MB: Lambda killed "
            f"{'it' if len(out_of_memory) == 1 else 'them'}. Give it "
            f"more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(memory)}."
        )))
    elif max_used and memory and max_used >= 0.9 * memory:
        found.append(("warn", (
            f"A run used {max_used:,} of its {memory:,} MB: close to the limit, where Lambda kills the run. Give it "
            f"more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(memory)}."
        )))
    elif max_used and memory:
        suggested = suggest_memory(max_used, memory)
        if suggested:  # a quarter of what it has at most per step: less memory is also less CPU
            suggested = max(suggested, next((s for s in MEMORY_STEPS if s >= memory / 4), suggested))
        if suggested and suggested < memory:
            rate = prices["gb_second_arm" if fn.arm else "gb_second"]
            monthly = sum(r.billed for r in runs) / 1000 * memory / 1024 * rate * DAYS_PER_MONTH / perf.window_days
            saving = monthly * (1 - suggested / memory)
            found.append(("warn" if saving >= 10 else "info", (
                f"Its runs used at most {max_used:,} of the {memory:,} MB it has: {suggested:,} MB still leaves room, "
                f"and would cut its compute cost by up to {_pct(1 - suggested / memory)} (about "
                f"{human_money(saving)}/month), if runs don't get slower: memory also sets the CPU. Try it and "
                f"compare: aws lambda update-function-configuration {cli} --memory-size {suggested}, then "
                f"performance('{name}') a day later."
            )))
    cold = perf.cold_starts
    if cold and len(runs) >= 20:
        share = len(cold) / len(runs)
        average = sum(r.init or 0 for r in cold) / len(cold)
        if share >= 0.05 and average >= 500:
            found.append(("info", (
                f"{_pct(share)} of runs were cold starts, each spending about {human_ms(average)} starting up first. "
                "SnapStart (Java 11+, Python 3.12+, .NET 8+), provisioned concurrency, or a smaller package "
                f"(code('{name}') shows what's in it) make them faster."
            )))
    return found
