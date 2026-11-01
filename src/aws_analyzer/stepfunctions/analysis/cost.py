"""
What a state machine costs a month, from its CloudWatch numbers and the prices in use: state transitions for a
Standard workflow, requests and run time for an Express one.
"""

from __future__ import annotations

from ..models import MachineNumbers, StateMachine
from ..prices import FREE_TRANSITIONS


DAYS_PER_MONTH = 30

MS_PER_SECOND = 1000


def transitions_per_execution(machine: StateMachine) -> int:
    """
    An estimate of the state transitions one execution makes: one per top-level state, as if each execution passed
    through every one of them once (a Map's iterations and a Parallel's branches add more; branches not taken,
    fewer).
    """
    return max(1, sum(1 for s in machine.states if s.depth == 0))


def monthly_cost(machine: StateMachine, numbers: MachineNumbers | None, prices: dict[str, float],
                 *, transitions: float | None = None) -> float | None:
    """
    The estimated USD a month at the pace of the last numbers.days days: transitions x price for Standard (after
    the free 4,000 a month, which the whole account shares, so it's left out here), requests plus GB-seconds at the
    smallest memory for Express. None without numbers.
    """
    if numbers is None or not numbers.days:
        return None
    per_month = numbers.started / numbers.days * DAYS_PER_MONTH
    if machine.is_express:
        seconds = (numbers.avg_ms or 0) / MS_PER_SECOND
        per_execution = prices["express_request"] + seconds * prices["express_memory_gb"] * prices["express_gb_second"]
        return per_month * per_execution
    steps = transitions if transitions is not None else transitions_per_execution(machine)
    return per_month * steps * prices["transition"]


def free_tier_note() -> str:
    return (f"Standard workflows get {FREE_TRANSITIONS:,} state transitions a month free, shared by the account, "
            "which the estimate leaves out.")
