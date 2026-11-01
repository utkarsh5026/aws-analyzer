"""
Express executions read from the state machine's log group: each line Step Functions writes there is one history
event as JSON ({"id", "type", "details", "previous_event_id", "event_timestamp", "execution_arn"}), so the lines of an
execution, turned back into GetExecutionHistory's shape, read like a Standard execution's history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..helpers import EXECUTION_ARN_RE, execution_name
from ..models import Execution, StateMachine


ENDINGS = {"ExecutionSucceeded": "SUCCEEDED", "ExecutionFailed": "FAILED", "ExecutionTimedOut": "TIMED_OUT",
           "ExecutionAborted": "ABORTED"}

_STATE_DETAILS = {"StateEntered": "stateEnteredEventDetails", "StateExited": "stateExitedEventDetails"}

_MS_PER_SECOND = 1000


@dataclass
class LoggedRun:
    """
    One Express execution's lines from the log group: its ARN and its events, each in GetExecutionHistory's shape.
    """

    arn: str
    events: list[dict[str, Any]] = field(default_factory=list)


def _detail_key(kind: str) -> str:
    """
    Where GetExecutionHistory keeps an event's details: 'TaskFailed' -> 'taskFailedEventDetails', any
    '...StateEntered' -> 'stateEnteredEventDetails'.
    """
    for ending, key in _STATE_DETAILS.items():
        if kind.endswith(ending):
            return key
    return f"{kind[:1].lower()}{kind[1:]}EventDetails"


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / _MS_PER_SECOND, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_log_line(message: str) -> tuple[str, dict[str, Any]] | None:
    """
    One line of a state machine's log group -> (its execution's ARN, the event as GetExecutionHistory gives it), or
    None when it isn't an execution event.
    """
    try:
        line = json.loads(message)
    except ValueError:
        return None
    if not isinstance(line, dict) or not line.get("execution_arn") or not line.get("type"):
        return None
    moment = _moment(line.get("event_timestamp"))
    if moment is None:
        return None
    kind = str(line["type"])
    event: dict[str, Any] = {"timestamp": moment, "type": kind, "id": int(line.get("id") or 0)}
    previous = int(line.get("previous_event_id") or 0)
    if previous:
        event["previousEventId"] = previous
    details = line.get("details")
    if isinstance(details, dict) and details:
        event[_detail_key(kind)] = details
    return str(line["execution_arn"]), event


def group_log_lines(messages: list[str]) -> list[LoggedRun]:
    """
    Log lines -> one LoggedRun per execution, its events in order, the newest execution first.
    """
    runs: dict[str, LoggedRun] = {}
    for message in messages:
        parsed = parse_log_line(message)
        if parsed is None:
            continue
        arn, event = parsed
        runs.setdefault(arn, LoggedRun(arn)).events.append(event)
    for run in runs.values():
        run.events.sort(key=lambda e: (e["timestamp"], e["id"]))
    return sorted(runs.values(), key=lambda r: r.events[0]["timestamp"], reverse=True)


def _machine_of(arn: str) -> str:
    found = EXECUTION_ARN_RE.match(arn)
    return found.group("machine") if found else ""


def execution_of(run: LoggedRun, *, logs_everything: bool) -> Execution:
    """
    An Express execution as its lines tell it: its name, when it started (its ExecutionStarted line, else its first
    line logged) and stopped, and how it ended. Without an ending line it's RUNNING when the log group gets every
    event, else unknown ('' status): at ERROR level only failures are logged.
    """
    events = run.events
    started = next((e for e in events if e["type"] == "ExecutionStarted"), events[0])
    ending = next((e for e in reversed(events) if e["type"] in ENDINGS), None)
    status = ENDINGS[ending["type"]] if ending else ("RUNNING" if logs_everything else "")
    details = (ending or {}).get(_detail_key(ending["type"]), {}) if ending else {}
    start_details = started.get("executionStartedEventDetails") or {}
    return Execution(
        arn=run.arn,
        name=execution_name(run.arn),
        machine=_machine_of(run.arn),
        status=status,
        start=started["timestamp"],
        stop=ending["timestamp"] if ending else None,
        error=str(details.get("error") or ""),
        cause=str(details.get("cause") or ""),
        input=str(start_details.get("input") or ""),
    )


def log_group_name(arn: str) -> str:
    """
    A log group's name from its ARN ('arn:aws:logs:us-east-1:1:log-group:/aws/states/x:*' -> '/aws/states/x').
    """
    if ":log-group:" not in arn:
        return arn
    name = arn.split(":log-group:", 1)[1]
    return name[:-2] if name.endswith(":*") else name


def arn_pattern(arn: str) -> str:
    """
    The CloudWatch Logs filter pattern that finds one execution's lines.
    """
    return f'{{ $.execution_arn = "{arn}" }}'


def endings_pattern(statuses: tuple[str, ...]) -> str:
    """
    The filter pattern that finds the lines ending executions with these statuses ('FAILED', ...).
    """
    kinds = [kind for kind, status in ENDINGS.items() if status in statuses]
    return "{ " + " || ".join(f'$.type = "{kind}"' for kind in kinds) + " }"


def logging_gaps(machine: StateMachine) -> str:
    """
    What an Express workflow's log group can't tell at its logging settings: at ERROR or FATAL only failed executions
    are logged, and without execution data no inputs, outputs or Lambda request IDs.
    """
    gaps = []
    if machine.log_level in ("ERROR", "FATAL"):
        gaps.append(f"{machine.name} logs at {machine.log_level} level, so only executions that failed are in its "
                    "log group: successful ones leave no line, and a failed one shows only its error events.")
    if not machine.log_data:
        gaps.append("Its logging leaves out execution data, so inputs, outputs and Lambda request IDs aren't there.")
    return " ".join(gaps)
