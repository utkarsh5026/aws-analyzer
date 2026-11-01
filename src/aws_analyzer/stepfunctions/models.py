"""
What StepFunctionsAnalyzer returns: state machines and their states, their CloudWatch numbers, executions, the
steps an execution took (with each Lambda step's request ID), failures grouped by cause, and the overview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require

from .helpers import FAILED_STATUSES


@dataclass
class StateDef:
    """
    One state of a definition: its type, what it calls (resource, and the Lambda function for a Lambda task), where
    it goes next, and its error handling. path is where it sits ('ProcessItems > ResizeImage' inside a Map).
    """

    name: str
    type: str
    path: str = ""
    resource: str = ""
    function: str = ""
    next: list[str] = field(default_factory=list)
    end: bool = False
    retries: list[dict[str, Any]] = field(default_factory=list)
    catches: list[dict[str, Any]] = field(default_factory=list)
    timeout: int | None = None
    heartbeat: int | None = None
    comment: str = ""
    depth: int = 0

    @property
    def waits_for_callback(self) -> bool:
        """
        Whether it waits for something outside to answer: a .waitForTaskToken task or an activity.
        """
        return self.resource.endswith(".waitForTaskToken") or ":activity:" in self.resource


@dataclass
class StateMachine:
    """
    A state machine: STANDARD or EXPRESS, its states (parsed from the definition, in order), logging and tracing.
    definition is the raw ASL text; errors holds the sections that couldn't be read.
    """

    name: str
    arn: str
    type: str = "STANDARD"
    created: datetime | None = None
    status: str = ""
    description: str = ""
    role: str = ""
    definition: str = ""
    states: list[StateDef] = field(default_factory=list)
    start_at: str = ""
    log_level: str = "OFF"
    log_data: bool = False
    log_groups: list[str] = field(default_factory=list)
    tracing: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def is_express(self) -> bool:
        return self.type == "EXPRESS"

    @property
    def functions(self) -> list[str]:
        """
        The Lambda functions its states call, each once, in order.
        """
        return list(dict.fromkeys(s.function for s in self.states if s.function))

    def state(self, name: str) -> StateDef | None:
        return next((s for s in self.states if s.name == name), None)


@dataclass
class MachineNumbers:
    """
    A state machine's CloudWatch numbers over whole UTC days: executions by outcome, throttled starts, and the
    average run time in ms. daily_* are per day, oldest first.
    """

    days: int
    started: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    aborted: int = 0
    throttled: int = 0
    avg_ms: float | None = None
    daily_started: list[int] = field(default_factory=list)
    daily_failed: list[int] = field(default_factory=list)

    @property
    def unsuccessful(self) -> int:
        return self.failed + self.timed_out + self.aborted

    @property
    def failure_rate(self) -> float:
        finished = self.succeeded + self.unsuccessful
        return self.unsuccessful / finished if finished else 0.0


@dataclass
class Execution:
    """
    One execution as ListExecutions or DescribeExecution returns it. error, cause, input and output come only from
    DescribeExecution.
    """

    arn: str
    name: str
    machine: str
    status: str
    start: datetime | None = None
    stop: datetime | None = None
    redrive_count: int = 0
    redrive_status: str = ""
    error: str = ""
    cause: str = ""
    input: str = ""
    output: str = ""

    @property
    def seconds(self) -> float | None:
        if self.start is None or self.stop is None:
            return None
        return (self.stop - self.start).total_seconds()

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


@dataclass
class ExecutionPage:
    """
    The executions of one state machine that list_executions read, newest first; truncated when it stopped at the
    limit before `since`.
    """

    machine: StateMachine
    executions: list[Execution] = field(default_factory=list)
    since: datetime | None = None
    truncated: bool = False

    def to_df(self) -> Any:
        pd = _require("pandas", "to_df()")
        return pd.DataFrame([
            {"name": e.name, "status": e.status, "start": e.start, "stop": e.stop, "seconds": e.seconds,
             "redrive_count": e.redrive_count, "arn": e.arn}
            for e in self.executions
        ])


@dataclass
class Attempt:
    """
    One try of a task: when it was scheduled and ended, and how (ok, failed, timed out). request_id is the Lambda
    run's, when the task called Lambda and the history says which run it was.
    """

    scheduled: datetime
    ended: datetime | None = None
    outcome: str = "running"
    error: str = ""
    cause: str = ""
    request_id: str = ""
    output: str = ""

    @property
    def seconds(self) -> float | None:
        return (self.ended - self.scheduled).total_seconds() if self.ended else None


@dataclass
class StepRun:
    """
    One state an execution entered: its type, when it was entered and left, its attempts (tasks only), the Lambda
    function it called, its input and output, and how it went: 'ok', 'failed', 'running' (never left), or 'ended'
    (a Fail or Succeed state). items is a Map's item count.
    """

    name: str
    type: str
    entered: datetime
    exited: datetime | None = None
    status: str = "running"
    attempts: list[Attempt] = field(default_factory=list)
    resource: str = ""
    function: str = ""
    function_region: str = ""
    input: str = ""
    output: str = ""
    error: str = ""
    cause: str = ""
    items: int | None = None
    depth: int = 0

    @property
    def retries(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def seconds(self) -> float | None:
        """
        How long it ran: until it was left, else until its last try ended (a failed task isn't left).
        """
        ended = self.exited or (self.attempts[-1].ended if self.attempts else None)
        return (ended - self.entered).total_seconds() if ended else None

    @property
    def request_id(self) -> str:
        """
        The Lambda request ID of its last attempt that has one.
        """
        return next((a.request_id for a in reversed(self.attempts) if a.request_id), "")


@dataclass
class ExecutionDetail:
    """
    An execution with the steps it took, oldest first, read from its history. failed_step is the step whose error
    ended it (None when it succeeded or the history doesn't say), and partial is set when the history was cut at
    the limit.
    """

    execution: Execution
    machine: StateMachine | None = None
    steps: list[StepRun] = field(default_factory=list)
    events: int = 0
    partial: bool = False
    failed_step: StepRun | None = None
    lambda_run: Any = None
    lambda_note: str = ""

    def step(self, name: str) -> StepRun | None:
        return next((s for s in reversed(self.steps) if s.name == name), None)


@dataclass
class FailureGroup:
    """
    Failed executions that stopped at the same state with the same error: how many, the newest one, and for a
    Lambda step its function and the newest run's request ID.
    """

    state: str
    error: str
    cause: str = ""
    count: int = 0
    newest: Execution | None = None
    function: str = ""
    request_id: str = ""
    redrivable: int = 0


@dataclass
class FailureReport:
    """
    What failures() read: the state machine, the failed executions in the window (newest first), the groups they
    fall in, and how many histories were read.
    """

    machine: StateMachine
    since: datetime
    failed: list[Execution] = field(default_factory=list)
    groups: list[FailureGroup] = field(default_factory=list)
    read: int = 0
    truncated: bool = False
    numbers: MachineNumbers | None = None


@dataclass
class Overview:
    """
    Every state machine in the region with its numbers over `days` days. errors holds what couldn't be read
    ('metrics', or a machine's name).
    """

    region: str
    days: int
    machines: list[StateMachine] = field(default_factory=list)
    numbers: dict[str, MachineNumbers] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0
