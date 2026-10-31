"""
What's running now: CloudWatch's numbers minute by minute, which functions are busy, and which runs in their newest
log lines haven't ended yet (a START line and no REPORT line). No AWS calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Activity, Function, LogEvent
from .logs import LogRun, split_runs


LIVE_WINDOW_MINUTES = 10

LINES_KEPT = 2_000

TIGHT_SHARE = 0.7

_ENDED_GRACE_SECONDS = 10


def minute_values(series: dict[datetime, float], since: datetime, minutes: int) -> list[float]:
    """
    A CloudWatch series (period start -> value) as a value per minute from `since`, oldest first, 0 for a minute
    without one; values outside the minutes are left out.
    """
    values = [0.0] * minutes
    for moment, value in series.items():
        slot = int((moment - since).total_seconds() // 60)
        if 0 <= slot < minutes:
            values[slot] += value
    return values


def in_flight(runs: list[LogRun], now: datetime, timeout_seconds: int) -> list[LogRun]:
    """
    The runs still going: no REPORT line yet, and started no longer ago than the function's timeout (and a few
    seconds' grace). A run older than that has ended, and its REPORT line is late or outside the lines read.
    """
    longest = timedelta(seconds=timeout_seconds + _ENDED_GRACE_SECONDS)
    return [run for run in runs if run.request_id and run.report is None and now - run.start <= longest]


@dataclass
class LiveFunction:
    """
    One function as the Live tab follows it: CloudWatch's last minutes (activity), the newest lines read from its
    log group (events, oldest first, at most LINES_KEPT), and the error code of a read that failed.
    """

    function: Function
    activity: Activity | None = None
    events: list[LogEvent] = field(default_factory=list)
    read: bool = False
    error: str = ""

    def add(self, events: list[LogEvent], *, now: datetime) -> int:
        """
        Adds lines read, leaving out the ones already held (reads overlap by a moment) and the ones older than the
        live window and the function's timeout; returns how many were new.
        """
        known = {(e.stream, e.time, e.message) for e in self.events}
        new = [e for e in events if (e.stream, e.time, e.message) not in known]
        oldest = now - timedelta(minutes=LIVE_WINDOW_MINUTES, seconds=self.function.timeout)
        kept = [e for e in self.events + new if e.time >= oldest]
        self.events = sorted(kept, key=lambda e: e.time)[-LINES_KEPT:]
        self.read = True
        return len(new)

    @property
    def runs(self) -> list[LogRun]:
        """
        Its lines as runs, newest first, without the lines that belong to no run.
        """
        return [run for run in split_runs(self.events) if run.request_id]

    def running(self, now: datetime) -> list[LogRun]:
        return in_flight(self.runs, now, self.function.timeout)

    @property
    def last_line(self) -> datetime | None:
        return self.events[-1].time if self.events else None


def ran_lately(item: LiveFunction) -> bool:
    """
    Whether a function shows on the Live tab, and has its lines read: CloudWatch counted a call in the live window
    (its numbers come a minute or two late), or lines of it are held.
    """
    return bool(item.events) or (item.activity is not None and item.activity.total_calls > 0)


def live_rank(item: LiveFunction, now: datetime) -> tuple[int, float, int, str]:
    """
    The Live tab's order: the most runs going first, then the one that ran last, then the busiest, then by name.
    """
    minutes = item.activity.minutes_since_called if item.activity is not None else None
    last = item.last_line
    seconds_quiet = (now - last).total_seconds() if last is not None else float(LIVE_WINDOW_MINUTES * 60)
    if minutes is not None:
        seconds_quiet = min(seconds_quiet, minutes * 60.0)
    calls = item.activity.total_calls if item.activity is not None else 0
    return -len(item.running(now)), seconds_quiet, -calls, item.function.name.lower()


def running_for(run: LogRun, now: datetime) -> float:
    """
    How long a run has been going, in seconds.
    """
    return max(0.0, (now - run.start).total_seconds())


def timeout_warning(run: LogRun, fn: Function, now: datetime) -> str:
    """
    What to know about a run going for most of its function's timeout ('' while it's well inside it): Lambda stops it
    at the timeout and the call fails, and the command that gives it longer.
    """
    seconds = running_for(run, now)
    if not fn.timeout or seconds < TIGHT_SHARE * fn.timeout:
        return ""
    longer = min(900, fn.timeout * 2)
    return (f"Running for {seconds:.0f} s of its {fn.timeout} s timeout: Lambda stops it at {fn.timeout} s and the "
            "call fails. If its runs often take this long, give it longer: aws lambda update-function-configuration "
            f"--function-name {fn.name} --timeout {longer}")
