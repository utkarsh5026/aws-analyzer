"""
A state machine's executions day by day (CloudWatch's daily numbers, whole UTC days), and whether its failures jumped
on one of them: the difference between three failures a day all week and thirty since yesterday. Pure: run it on
MachineNumbers of your own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..._kit.fmt import _plural
from ..._kit.render import _call

from ..models import MachineNumbers, StateMachine


Finding = tuple[str, str]

JUMP_MIN_FAILURES = 5

JUMP_SHARE = 0.6

JUMP_RATIO = 3.0

JUMP_FLOOR = 0.5


@dataclass
class DayCount:
    """
    One UTC day of a state machine's executions: how many started, and how many failed, timed out or were aborted.
    """

    day: date
    started: int
    unsuccessful: int


@dataclass
class FailureJump:
    """
    The day failures jumped: how many came on it and after, out of how many in the window, and how many a day came
    before and after it.
    """

    day: date
    after: int
    total: int
    days_after: int
    per_day_before: float
    per_day_after: float


def day_label(day: date) -> str:
    """
    date(2026, 11, 10) -> '10 Nov'.
    """
    return f"{day.day} {day:%b}"


def daily_counts(numbers: MachineNumbers | None) -> list[DayCount]:
    """
    Each UTC day's executions, oldest first, from MachineNumbers read with first_day (an empty list when they weren't).
    """
    if numbers is None or numbers.first_day is None or not numbers.daily_started:
        return []
    unsuccessful = numbers.daily_unsuccessful or numbers.daily_failed or [0] * len(numbers.daily_started)
    return [DayCount(numbers.first_day + timedelta(days=i), started, failed)
            for i, (started, failed) in enumerate(zip(numbers.daily_started, unsuccessful))]


def _jump_from(days: list[DayCount], first_after: int) -> FailureJump | None:
    """
    The jump if failures from days[first_after] on make one: at least JUMP_MIN_FAILURES, at least JUMP_SHARE of the
    window's, and JUMP_RATIO times as many a day as before (at least JUMP_FLOOR a day before, so one failure in a
    quiet week isn't a jump).
    """
    before, after = days[:first_after], days[first_after:]
    after_count = sum(d.unsuccessful for d in after)
    total = after_count + sum(d.unsuccessful for d in before)
    per_day_before = sum(d.unsuccessful for d in before) / len(before)
    per_day_after = after_count / len(after)
    is_jump = (days[first_after].unsuccessful > 0 and after_count >= JUMP_MIN_FAILURES
               and after_count >= JUMP_SHARE * total
               and per_day_after >= JUMP_RATIO * max(per_day_before, JUMP_FLOOR))
    if not is_jump:
        return None
    return FailureJump(days[first_after].day, after_count, total, len(after), per_day_before, per_day_after)


def failure_jump(days: list[DayCount]) -> FailureJump | None:
    """
    The day failures jumped, if they did: of the days that start a jump (_jump_from), the one whose failures a day
    rose the most against the days before it. None for a window of fewer than three days.
    """
    if len(days) < 3:
        return None
    jumps = [jump for i in range(1, len(days)) if (jump := _jump_from(days, i)) is not None]
    if not jumps:
        return None
    return max(jumps, key=lambda jump: jump.per_day_after / max(jump.per_day_before, JUMP_FLOOR))


def trend_findings(machine: StateMachine, numbers: MachineNumbers | None) -> list[Finding]:
    """
    A warning when a state machine's failures jumped on one day of the window: when, how many since, against how
    many a day before, and the call that reads just those failures.
    """
    days = daily_counts(numbers)
    jump = failure_jump(days)
    if jump is None or numbers is None:
        return []
    since = _call("failures", machine.name, since=f"{jump.days_after}d")
    return [("warn", f"Failures jumped on {day_label(jump.day)}: {jump.after:,} of the {jump.total:,} in the last "
                     f"{_plural(numbers.days, 'day')} came since then, about {jump.per_day_after:.0f} a day against "
                     f"{jump.per_day_before:.1f} before (UTC days). See what changed and which executions: {since}")]
