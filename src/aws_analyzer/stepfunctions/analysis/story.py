"""
One execution told two ways: as a waterfall (each state a bar on one time axis, with its tries, the back-off between
them and a Map's iterations in place), and as one log, Step Functions' own events and every Lambda run's lines merged
by time, so what was happening at a moment reads in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..helpers import short_cause
from ..models import ExecutionDetail, StepRun


STEP_FUNCTIONS = "step functions"

_TRY_ENDINGS = {"failed": "failed", "timed out": "timed out"}

@dataclass
class LogLine:
    """
    A line of an execution's log: when, what wrote it (STEP_FUNCTIONS, or a Lambda function's name), the state it
    belongs to, its text, and its kind ('error', 'report', 'start', 'end' or '').
    """

    time: datetime
    source: str
    text: str
    state: str = ""
    kind: str = ""


@dataclass
class Segment:
    """
    A piece of a waterfall bar, as fractions of the execution's span: where it starts, how wide it is, and what it
    is ('ok', 'failed', 'retry', 'wait', 'running', 'sdk', 'choice').
    """

    start: float
    width: float
    kind: str
    label: str = ""


@dataclass
class WaterfallRow:
    """
    A row of the waterfall: the state, how deep it sits (inside a Map or Parallel), its type, what it called, and
    its bar's pieces. count is how many times it ran when a Map's iterations are drawn as one row.
    """

    name: str
    depth: int
    type: str
    does: str
    segments: list[Segment] = field(default_factory=list)
    count: int = 1
    failed: int = 0


@dataclass
class Waterfall:
    """
    An execution's waterfall: its rows in the order the states were entered, and its span in seconds.
    """

    rows: list[WaterfallRow]
    seconds: float
    start: datetime | None = None


def _span(detail: ExecutionDetail) -> tuple[datetime | None, float]:
    """
    When the execution started and how long its waterfall's axis is: until it stopped, else until the last thing
    the history says happened.
    """
    execution = detail.execution
    start = execution.start or (detail.steps[0].entered if detail.steps else None)
    if start is None:
        return None, 0.0
    ends = [execution.stop] + [s.exited for s in detail.steps] + [a.ended for s in detail.steps for a in s.attempts]
    known = [moment for moment in ends if moment is not None] or [start]
    return start, max(0.001, (max(known) - start).total_seconds())


def _fraction(moment: datetime, start: datetime, seconds: float) -> float:
    return min(1.0, max(0.0, (moment - start).total_seconds() / seconds))


def _try_kind(outcome: str, is_last: bool) -> str:
    if outcome == "ok":
        return "ok"
    if outcome == "running":
        return "running"
    return "failed" if is_last else "retry"


def _task_segments(step: StepRun, start: datetime, seconds: float, end: datetime) -> list[Segment]:
    """
    A task's bar: each try in its colour (a try that was retried amber, the last one green or red), and the back-off
    between tries hatched.
    """
    pieces: list[Segment] = []
    for i, attempt in enumerate(step.attempts):
        is_last = i == len(step.attempts) - 1
        if i:
            gap_from = step.attempts[i - 1].ended or step.attempts[i - 1].scheduled
            left = _fraction(gap_from, start, seconds)
            pieces.append(Segment(left, max(0.0, _fraction(attempt.scheduled, start, seconds) - left), "wait"))
        left = _fraction(attempt.scheduled, start, seconds)
        right = _fraction(attempt.ended or end, start, seconds)
        label = attempt.error if attempt.error and is_last else (f"try {i + 1}" if len(step.attempts) > 1 else "")
        pieces.append(Segment(left, max(0.0, right - left), _try_kind(attempt.outcome, is_last), label))
    return pieces


def _step_segments(step: StepRun, start: datetime, seconds: float, end: datetime) -> list[Segment]:
    if step.attempts and not step.function:
        kind = "failed" if step.status == "failed" else "sdk"
        left = _fraction(step.entered, start, seconds)
        right = _fraction(step.exited or step.attempts[-1].ended or end, start, seconds)
        return [Segment(left, max(0.0, right - left), kind, step.error if kind == "failed" else "")]
    if step.attempts:
        return _task_segments(step, start, seconds, end)
    left = _fraction(step.entered, start, seconds)
    right = _fraction(step.exited or end, start, seconds)
    kinds = {"failed": "failed", "running": "running", "ended": "failed" if step.type == "Fail" else "ok"}
    kind = "choice" if step.type in ("Choice", "Pass") else kinds.get(step.status, "ok")
    return [Segment(left, max(0.0, right - left), kind, step.error)]


def _does(step: StepRun) -> str:
    if step.function:
        return f"λ {step.function}"
    if step.resource:
        return step.resource
    return step.type


def waterfall(detail: ExecutionDetail) -> Waterfall:
    """
    An execution as a waterfall: a row per state entered at the top level, and under a Map or Parallel a row per
    state inside it, every iteration drawn on that one row (count says how many ran, failed how many failed).
    """
    start, seconds = _span(detail)
    if start is None:
        return Waterfall([], 0.0)
    end = detail.execution.stop or start
    rows: list[WaterfallRow] = []
    inner: dict[tuple[int, str], WaterfallRow] = {}
    for step in detail.steps:
        pieces = _step_segments(step, start, seconds, max(end, step.exited or step.entered))
        key = (step.depth, step.name)
        if step.depth and key in inner:
            row = inner[key]
            row.segments += pieces
            row.count += 1
            row.failed += step.status == "failed"
            continue
        row = WaterfallRow(step.name, step.depth, step.type, _does(step), pieces, failed=int(step.status == "failed"))
        rows.append(row)
        if step.depth:
            inner[key] = row
    return Waterfall(rows, seconds, start)


def _try_lines(step: StepRun) -> list[LogLine]:
    """
    What Step Functions recorded about a task's tries: when it was scheduled, each try that failed (and whether it
    was retried), and how the last one ended.
    """
    lines: list[LogLine] = []
    target = f" · {step.function}" if step.function else (f" · {step.resource}" if step.resource else "")
    for i, attempt in enumerate(step.attempts):
        is_last = i == len(step.attempts) - 1
        rid = f" · request {attempt.request_id[:8]}" if attempt.request_id else ""
        lines.append(LogLine(attempt.scheduled, STEP_FUNCTIONS, f"{step.name} scheduled{target}{rid}"
                             + (f" (try {i + 1} of {len(step.attempts)})" if len(step.attempts) > 1 else ""),
                             step.name, "start"))
        if attempt.outcome not in _TRY_ENDINGS or attempt.ended is None:
            continue
        why = (short_cause(attempt.cause) or attempt.error).rstrip(".")
        then = "" if is_last else ", retried"
        lines.append(LogLine(attempt.ended, STEP_FUNCTIONS, f"{step.name} {_TRY_ENDINGS[attempt.outcome]}: "
                                                            f"{why}{then}", step.name, "error"))
    return lines


def step_lines(detail: ExecutionDetail) -> list[LogLine]:
    """
    Step Functions' side of an execution's log: it started, each state entered (a task's tries as above), a state
    that failed, and how the execution ended.
    """
    execution = detail.execution
    lines: list[LogLine] = []
    if execution.start is not None:
        lines.append(LogLine(execution.start, STEP_FUNCTIONS, f"Execution {execution.name} started", "", "start"))
    for step in detail.steps:
        if step.attempts:
            lines += _try_lines(step)
        elif step.type in ("Fail", "Succeed") or step.status == "failed":
            lines.append(LogLine(step.entered, STEP_FUNCTIONS, f"Entered {step.name} ({step.type})", step.name,
                                 "error" if step.type == "Fail" or step.status == "failed" else ""))
    if execution.stop is not None:
        ending = execution.status.replace("_", " ").lower()
        why = f": {execution.error}" if execution.error else ""
        kind = "error" if execution.failed else "end"
        lines.append(LogLine(execution.stop, STEP_FUNCTIONS, f"Execution {ending}{why}", "", kind))
    return lines


def merge_log(*sources: list[LogLine]) -> list[LogLine]:
    """
    Lines from several sources as one log, oldest first; at the same moment, Step Functions' lines go first (they
    schedule what the functions then log).
    """
    merged = [line for lines in sources for line in lines]
    return sorted(merged, key=lambda line: (line.time, line.source != STEP_FUNCTIONS))


@dataclass
class StoryRun:
    """
    The Lambda run behind one step's last try, as the execution's log shows it: the step, the function, the run (a
    LogRun from aws_analyzer.lambda_functions, or None) and how it was found or why it wasn't.
    """

    step: StepRun
    run: Any
    note: str


@dataclass
class ExecutionStory:
    """
    An execution's whole log: Step Functions' lines and every Lambda run's merged by time, the runs read (one per
    Lambda step, the failed ones first), and how many Lambda steps weren't read because there were more than most.
    """

    lines: list[LogLine]
    runs: list[StoryRun] = field(default_factory=list)
    skipped: int = 0

    @property
    def sources(self) -> list[str]:
        """
        What wrote its lines, Step Functions first, then the functions in the order they first logged.
        """
        return list(dict.fromkeys([STEP_FUNCTIONS] + [line.source for line in self.lines]))


def lambda_steps(detail: ExecutionDetail, most: int) -> tuple[list[StepRun], int]:
    """
    The steps whose Lambda runs an execution's log reads: each step that called a function, the failed ones first,
    then in order, at most `most`; and how many were left out.
    """
    called = [s for s in detail.steps if s.function and s.attempts]
    ordered = sorted(called, key=lambda s: (s.status != "failed", s.entered))
    return ordered[:most], max(0, len(ordered) - most)
