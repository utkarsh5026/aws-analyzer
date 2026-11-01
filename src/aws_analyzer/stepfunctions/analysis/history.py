"""
An execution's history (GetExecutionHistory's events) read into the steps it took: when each state was entered and
left, each try of a task, the Lambda run behind each Lambda try, and the step whose error ended the execution.
"""

from __future__ import annotations

from typing import Any, Callable

from ..helpers import arn_region, lambda_name, loads, short_cause
from ..models import Attempt, ExecutionDetail, StepRun


_ENTERED, _EXITED = "StateEntered", "StateExited"

_TASK_KINDS = ("Task", "LambdaFunction", "Activity")

_FAILED_ENDINGS = {"Failed": "failed", "TimedOut": "timed out", "StartFailed": "failed", "SubmitFailed": "failed",
                   "ScheduleFailed": "failed"}

_NESTING_EVENTS = ("MapIterationStarted", "ParallelStateStarted", "MapStateStarted")

_NESTING_KINDS = ("MapState", "MapIteration", "ParallelState")

_END_STATES = ("Fail", "Succeed")

_EXECUTION_ENDS = {"ExecutionFailed": "executionFailedEventDetails", "ExecutionTimedOut":
                   "executionTimedOutEventDetails", "ExecutionAborted": "executionAbortedEventDetails"}


def _details(event: dict[str, Any]) -> dict[str, Any]:
    """
    The one *EventDetails member an event carries ({} when it has none).
    """
    for key, value in event.items():
        if key.endswith("EventDetails") and isinstance(value, dict):
            return value
    return {}


def request_id_of_output(output: str) -> str:
    """
    The Lambda request ID in a lambda:invoke task's output (SdkResponseMetadata.RequestId), or ''.
    """
    payload = loads(output)
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("SdkResponseMetadata") or {}
    return str(metadata.get("RequestId") or "") if isinstance(metadata, dict) else ""


def request_id_of_cause(cause: str) -> str:
    """
    The Lambda request ID in a failed Lambda task's cause: the error payload Lambda returned ({"errorType": ...,
    "requestId": ...}), or ''. A throttle's cause names the API request, not a run, so it gives ''.
    """
    payload = loads(cause)
    return str(payload.get("requestId") or "") if isinstance(payload, dict) else ""


class _HistoryReader:
    """
    Reads events oldest first. Each event belongs to the step whose StateEntered event its chain of
    previousEventId leads back to, which keeps a Map's iterations and a Parallel's branches apart; a Map's or
    Parallel's own events (an iteration ending, the state succeeding) belong to the innermost one still open, since
    their chain runs back through the last iteration's states.
    """

    def __init__(self, events: list[dict[str, Any]]):
        self.events = sorted(events, key=lambda e: e.get("id", 0))
        self.by_id = {e.get("id"): e for e in self.events}
        self.steps: dict[int, StepRun] = {}
        self.owners: dict[int, int | None] = {}
        self.ending: tuple[str, str, str] | None = None
        self.open_nesting: list[int] = []

    def owner_id(self, event: dict[str, Any]) -> int | None:
        """
        The id of the StateEntered event behind `event`, or None when the chain leaves the events read.
        """
        chain, current, found = [], event, None
        while current is not None:
            event_id = current.get("id")
            if event_id in self.owners:
                found = self.owners[event_id]
                break
            chain.append(event_id)
            if str(current.get("type", "")).endswith(_ENTERED):
                found = event_id
                break
            current = self.by_id.get(current.get("previousEventId"))
        for event_id in chain:
            self.owners[event_id] = found
        return found

    def depth_of(self, event: dict[str, Any]) -> int:
        """
        How deep a newly entered state sits: one below the Map or Parallel whose start led to it.
        """
        previous = self.by_id.get(event.get("previousEventId"))
        if previous is None:
            return 0
        parent = self.steps.get(self.owner_id(previous) or -1)
        if parent is None:
            return 0
        return parent.depth + (1 if previous.get("type") in _NESTING_EVENTS else 0)

    def read(self) -> list[StepRun]:
        for event in self.events:
            self.take(event)
        for step in self.steps.values():
            _settle(step)
        return sorted(self.steps.values(), key=lambda s: s.entered)

    def take(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        if kind in _EXECUTION_ENDS:
            details = event.get(_EXECUTION_ENDS[kind]) or {}
            self.ending = (kind, details.get("error", ""), details.get("cause", ""))
            return
        if kind.endswith(_ENTERED):
            self.enter(event, kind[: -len(_ENTERED)])
            return
        if kind.startswith(_NESTING_KINDS) and self.open_nesting:
            self.owners[event["id"]] = self.open_nesting[-1]
            if kind.endswith(_EXITED):
                self.open_nesting.pop()
        step = self.steps.get(self.owner_id(event) or -1)
        if step is None:
            return
        handler = _handler(kind)
        if handler is not None:
            handler(step, event, _details(event))

    def enter(self, event: dict[str, Any], state_type: str) -> None:
        details = _details(event)
        step = StepRun(name=details.get("name", "?"), type=state_type, entered=event["timestamp"],
                       input=details.get("input", ""), depth=self.depth_of(event))
        if state_type in _END_STATES:
            step.status = "ended"
            step.exited = event["timestamp"]
        if state_type in ("Map", "Parallel"):
            self.open_nesting.append(event["id"])
        self.steps[event["id"]] = step


def _exited(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
    step.exited = event["timestamp"]
    step.output = details.get("output", "")


def _scheduled(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
    """
    A try begins. A lambda:invoke task names its function in its parameters, an older direct-ARN one in its
    resource.
    """
    resource = details.get("resource", "")
    resource_type = details.get("resourceType")
    if resource_type:
        step.resource = f"{resource_type}:{resource}"
        parameters = loads(details.get("parameters")) or {}
        if resource_type == "lambda" and isinstance(parameters, dict) and isinstance(parameters.get("FunctionName"),
                                                                                     str):
            step.function = lambda_name(parameters["FunctionName"])
            step.function_region = arn_region(parameters["FunctionName"]) or details.get("region", "")
    elif resource.startswith("arn:aws:lambda:"):
        step.resource = resource
        step.function = lambda_name(resource)
        step.function_region = arn_region(resource) or ""
    else:
        step.resource = resource
    step.attempts.append(Attempt(scheduled=event["timestamp"]))


def _succeeded(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
    attempt = _open_attempt(step, event)
    attempt.ended, attempt.outcome = event["timestamp"], "ok"
    attempt.output = details.get("output", "")
    attempt.request_id = request_id_of_output(attempt.output)


def _ended_badly(outcome: str) -> Callable[[StepRun, dict[str, Any], dict[str, Any]], None]:
    def record(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
        attempt = _open_attempt(step, event)
        attempt.ended, attempt.outcome = event["timestamp"], outcome
        attempt.error, attempt.cause = details.get("error", ""), details.get("cause", "")
        attempt.request_id = request_id_of_cause(attempt.cause)
    return record


def _map_started(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
    step.items = details.get("length")


def _branch_failed(step: StepRun, event: dict[str, Any], details: dict[str, Any]) -> None:
    step.status = "failed"
    step.error = step.error or "States.BranchFailed"


def _open_attempt(step: StepRun, event: dict[str, Any]) -> Attempt:
    """
    The try an ending belongs to: the last one still open, or a new one when the start wasn't read.
    """
    if step.attempts and step.attempts[-1].ended is None:
        return step.attempts[-1]
    step.attempts.append(Attempt(scheduled=event["timestamp"]))
    return step.attempts[-1]


def _handler(kind: str) -> Callable[[StepRun, dict[str, Any], dict[str, Any]], None] | None:
    """
    What an event of `kind` does to the step it belongs to, or None when it changes nothing shown.
    """
    if kind.endswith(_EXITED):
        return _exited
    if kind in ("MapStateFailed", "ParallelStateFailed"):
        return _branch_failed
    if kind == "MapStateStarted":
        return _map_started
    for prefix in _TASK_KINDS:
        if not kind.startswith(prefix):
            continue
        ending = kind[len(prefix):]
        if ending == "Scheduled":
            return _scheduled
        if ending == "Succeeded":
            return _succeeded
        if ending in _FAILED_ENDINGS:
            return _ended_badly(_FAILED_ENDINGS[ending])
    return None


def _settle(step: StepRun) -> None:
    """
    A step's status once every event is read: failed when its last try failed (even if a Catch then moved on), ok
    when it was left, else as it was.
    """
    last = step.attempts[-1] if step.attempts else None
    if last is not None and last.outcome in ("failed", "timed out"):
        step.status = "failed"
        step.error, step.cause = last.error or ("States.Timeout" if last.outcome == "timed out" else ""), last.cause
    elif step.status == "running" and step.exited is not None:
        step.status = "ok"


def _last_try_end(step: StepRun) -> Any:
    return step.attempts[-1].ended or step.entered


def _failed_step(steps: list[StepRun], status: str) -> StepRun | None:
    """
    The step whose error ended an execution: the newest failed step that isn't a Fail state, preferring one with an
    error of its own (an iteration over the Map around it); for a timed-out execution, the step still running.
    """
    failed = [s for s in steps if s.status == "failed"]
    own = [s for s in failed if s.attempts]
    if own:
        return max(own, key=_last_try_end)
    if failed:
        return failed[-1]
    if status == "TIMED_OUT":
        running = [s for s in steps if s.status == "running"]
        return running[-1] if running else None
    return None


def fill_detail(detail: ExecutionDetail, events: list[dict[str, Any]]) -> ExecutionDetail:
    """
    Fills an ExecutionDetail from its history's events (any order, all or the newest part): its steps, the step
    that failed, and the execution's error and cause when DescribeExecution didn't give them.
    """
    reader = _HistoryReader(events)
    detail.steps = reader.read()
    detail.events = len(events)
    execution = detail.execution
    if reader.ending is not None:
        _, error, cause = reader.ending
        execution.error = execution.error or error
        execution.cause = execution.cause or cause
    if execution.failed:
        detail.failed_step = _failed_step(detail.steps, execution.status)
    return detail


def failure_key(detail: ExecutionDetail) -> tuple[str, str]:
    """
    (state, error) a failed execution is grouped by: the step that failed and its error, else what the execution
    says.
    """
    step, execution = detail.failed_step, detail.execution
    state = step.name if step is not None else "(unknown state)"
    error = (step.error if step is not None else "") or execution.error or execution.status.replace("_", " ").lower()
    if execution.status == "TIMED_OUT" and step is not None and step.status == "running":
        error = "States.Timeout"
    return state, error


def cause_of(detail: ExecutionDetail) -> str:
    """
    The failure's cause in one line.
    """
    step = detail.failed_step
    return short_cause((step.cause if step is not None else "") or detail.execution.cause)
