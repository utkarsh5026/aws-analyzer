"""
What StepFunctionsAnalyzer returns: state machines and their states, their CloudWatch numbers, executions, the
steps an execution took (with each Lambda step's request ID), failures grouped by cause, and the overview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .._kit.deps import _require

from .helpers import FAILED_STATUSES


@dataclass
class StateDef:
    """
    One state of a definition: its type, what it calls (resource, and the Lambda function for a Lambda task), where
    it goes next (labels: what sends it to a target, a Choice rule's condition, 'Default' or a Catch's errors), and its
    error handling. path is where it sits ('ProcessItems > ResizeImage' inside a Map), branch which of its Parallel's
    branches, and starts the StartAt of each state machine nested in it (a Map's one, a Parallel's per branch).
    A Map's max_concurrency is its MaxConcurrency (None or 0: as many as Step Functions allows) and distributed says
    whether it runs its items as child executions (Mode DISTRIBUTED) rather than inline.
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
    labels: dict[str, str] = field(default_factory=dict)
    branch: int = 0
    starts: list[str] = field(default_factory=list)
    max_concurrency: int | None = None
    distributed: bool = False

    @property
    def waits_for_callback(self) -> bool:
        """
        Whether it waits for something outside to answer: a .waitForTaskToken task or an activity.
        """
        return self.resource.endswith(".waitForTaskToken") or ":activity:" in self.resource


@dataclass
class LambdaSettings:
    """
    The settings of a Lambda function a state machine calls that meet its states' own: its timeout in seconds and
    its reserved concurrency (None: it shares the region's pool, whose size is region_concurrency when known).
    """

    timeout: int
    reserved_concurrency: int | None = None
    region_concurrency: int | None = None


@dataclass
class StateMachine:
    """
    A state machine: STANDARD or EXPRESS, its states (parsed from the definition, in order), logging and tracing.
    definition is the raw ASL text; errors holds the sections that couldn't be read. lambda_settings are the
    settings of the Lambda functions it calls, by name, once read (read_lambda_settings()).
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
    lambda_settings: dict[str, LambdaSettings] = field(default_factory=dict)

    @property
    def is_express(self) -> bool:
        return self.type == "EXPRESS"

    @property
    def has_executions(self) -> bool:
        """
        Whether its executions can be read: a Standard workflow's history, or an Express workflow's log group when
        logging is on.
        """
        return not self.is_express or (self.log_level != "OFF" and bool(self.log_groups))

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
    average run time in ms. daily_* are per day, oldest first, from first_day (a UTC date): daily_unsuccessful counts
    the ones that failed, timed out or were aborted, daily_failed only the failed.
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
    daily_unsuccessful: list[int] = field(default_factory=list)
    first_day: date | None = None

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
    limit before `since`. with_failures says the ones that didn't succeed in the whole range were listed on their own
    and added after the newest (executions_with_failures), and failures_truncated that there were more of those than
    the limit. source is 'history' (ListExecutions) or 'logs' (an Express workflow's log group, read back to
    covered_from), and note says what the logs can't tell.
    """

    machine: StateMachine
    executions: list[Execution] = field(default_factory=list)
    since: datetime | None = None
    truncated: bool = False
    with_failures: bool = False
    failures_truncated: bool = False
    source: str = "history"
    covered_from: datetime | None = None
    note: str = ""

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
    source: str = "history"

    def step(self, name: str) -> StepRun | None:
        return next((s for s in reversed(self.steps) if s.name == name), None)


@dataclass
class FailureGroup:
    """
    Failed executions that stopped at the same state with the same error: how many, the newest one, every one read
    (executions, newest first), and for a Lambda step its function and the newest run's request ID.
    """

    state: str
    error: str
    cause: str = ""
    count: int = 0
    newest: Execution | None = None
    function: str = ""
    request_id: str = ""
    redrivable: int = 0
    executions: list[Execution] = field(default_factory=list)

    @property
    def first_seen(self) -> datetime | None:
        """
        When the oldest execution read in this group started: how long it has been failing this way, as far as the
        failures read go back.
        """
        starts = [execution.start for execution in self.executions if execution.start is not None]
        return min(starts) if starts else None


@dataclass
class Change:
    """
    Something that changed how a state machine runs, and when: a Lambda function it calls deployed (kind
    'function': its code or settings changed, or a version published, detail 'version 7'), or its own definition
    updated (kind 'definition'), as the definition an execution ran with says.
    """

    when: datetime
    kind: str
    name: str
    detail: str = ""


@dataclass
class FailureReport:
    """
    What failures() read: the state machine, the failed executions in the window (newest first), the groups they
    fall in, how many histories were read, and what changed around them (changes, oldest first; change_errors
    says what couldn't be read: 'lambda:<function>' or 'definition' -> error code).
    """

    machine: StateMachine
    since: datetime
    failed: list[Execution] = field(default_factory=list)
    groups: list[FailureGroup] = field(default_factory=list)
    read: int = 0
    truncated: bool = False
    numbers: MachineNumbers | None = None
    source: str = "history"
    changes: list[Change] = field(default_factory=list)
    change_errors: dict[str, str] = field(default_factory=dict)


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


@dataclass
class StateTiming:
    """
    One state's time over several executions: how many times it ran and in how many executions, the median and the
    longest of its finished runs (seconds), the time all its runs took, its share of the executions' time (top-level
    states only: a Map's inner runs can overlap), its tries (tasks only: tried_runs is how many runs had tries,
    retried_runs how many took more than one), the most common error a retry followed, and the executions behind its
    longest run and a retried one.
    """

    name: str
    type: str
    depth: int = 0
    runs: int = 0
    executions: int = 0
    median: float | None = None
    longest: float | None = None
    total: float = 0.0
    share: float | None = None
    tries: int = 0
    tried_runs: int = 0
    retried_runs: int = 0
    retry_error: str = ""
    function: str = ""
    slowest: Execution | None = None
    retried_in: Execution | None = None

    @property
    def retries(self) -> int:
        return self.tries - self.tried_runs

    @property
    def tries_per_run(self) -> float | None:
        return self.tries / self.tried_runs if self.tried_runs else None


@dataclass
class TimingReport:
    """
    What timings() read: the newest `asked` succeeded executions since `since` (executions, newest first; fewer when
    there weren't as many), each state's StateTiming in definition order, the executions' time added up, how many
    histories were cut at the event limit (partial), where they came from ('history' or 'logs') and what the logs
    can't tell (note), and the state machine's CloudWatch numbers for the cost of its retries.
    """

    machine: StateMachine
    since: datetime
    asked: int
    executions: list[Execution] = field(default_factory=list)
    states: list[StateTiming] = field(default_factory=list)
    total: float = 0.0
    partial: int = 0
    source: str = "history"
    note: str = ""
    numbers: MachineNumbers | None = None

    def state(self, name: str) -> StateTiming | None:
        return next((s for s in self.states if s.name == name), None)

    def to_df(self) -> Any:
        pd = _require("pandas", "to_df()")
        return pd.DataFrame([
            {"state": s.name, "type": s.type, "depth": s.depth, "runs": s.runs, "executions": s.executions,
             "median_seconds": s.median, "longest_seconds": s.longest, "total_seconds": s.total, "share": s.share,
             "tries_per_run": s.tries_per_run, "retried_runs": s.retried_runs}
            for s in self.states
        ])
