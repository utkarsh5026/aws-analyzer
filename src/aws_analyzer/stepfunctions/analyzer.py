"""
StepFunctionsAnalyzer: talks to Step Functions and CloudWatch, and returns the dataclasses in models. For a
Lambda step it reads that run's log lines through the Lambda analyzer (aws_analyzer.lambda_functions, loaded the
first time it's needed). Never prints, and never starts, stops or redrives an execution.
"""

from __future__ import annotations

import difflib
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _Hint
from .._kit.fmt import _as_count, _utcnow, parse_time

from .helpers import EXECUTION_ARN_RE, FAILED_STATUSES, STATE_MACHINE_ARN_RE, execution_arn, parse_execution_ref
from .models import (
    Attempt, Execution, ExecutionDetail, ExecutionPage, FailureReport, MachineNumbers, Overview, StateMachine,
    StepRun,
)
from .prices import STEPFUNCTIONS_PRICES
from .analysis import fill_detail, group_failures, parse_execution, parse_state_machine
from .analysis.story import ExecutionStory, LogLine, StoryRun, lambda_steps, merge_log, step_lines


_METRIC_STAT_AND_FIELD = {
    "ExecutionsStarted": ("Sum", "started"),
    "ExecutionsSucceeded": ("Sum", "succeeded"),
    "ExecutionsFailed": ("Sum", "failed"),
    "ExecutionsTimedOut": ("Sum", "timed_out"),
    "ExecutionsAborted": ("Sum", "aborted"),
    "ExecutionThrottled": ("Sum", "throttled"),
    "ExecutionTime": ("Average", "avg_ms"),
}

_DAY_SECONDS = 86_400

_QUERIES_PER_CALL = 500

_TAIL_EVENTS = 200

_LIST_PAGE = 100

_LOG_PAD = timedelta(minutes=2)

_TIME_MATCH_LINES = 2_000

_STORY_STEPS = 12


def _lambda_module() -> Any:
    """
    aws_analyzer.lambda_functions, whose log reading a Lambda step reuses. Loaded when it's first needed, so
    importing this module loads no other analyzer.
    """
    from .. import lambda_functions

    return lambda_functions


def _run_lines(story_run: StoryRun) -> list[LogLine]:
    """
    A Lambda run's lines for an execution's log, as the Lambda reports show them (readable_line): the lines that
    report an error marked, and the REPORT line summed up.
    """
    lambda_functions = _lambda_module()
    run, step = story_run.run, story_run.step
    failing = {id(event) for event, _, _ in run.failures}
    lines = []
    for event in run.events:
        is_report = lambda_functions.parse_report(event.message) is not None
        kind = "error" if id(event) in failing else ("report" if is_report else "")
        lines.append(LogLine(event.time, step.function, lambda_functions.readable_line(event.message), step.name, kind))
    return lines


def _midnight_after(now: datetime) -> datetime:
    """
    The UTC midnight that ends today: CloudWatch's daily numbers cover whole UTC days.
    """
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


class StepFunctionsAnalyzer(_BaseAnalyzer):
    """
    Pure-logic Step Functions analysis: every method returns data; nothing is printed, started or changed.

    Methods about one state machine take its name or ARN. Executions are named by their ARN, 'machine/name', or a
    name with machine=. `prices` overrides STEPFUNCTIONS_PRICES. `client` is the Step Functions client and
    `clients` other boto3 clients by service name ('stepfunctions', 'cloudwatch'). `lambda_core` is the
    LambdaAnalyzer that reads a Lambda step's logs (one is made on this session when it's first needed).
    """

    _LIST_PRICES = STEPFUNCTIONS_PRICES

    def __init__(  # noqa: PLR0913
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        lambda_core: Any = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["stepfunctions"] = client
        self._lock = threading.Lock()
        self.max_workers = 8
        self._lambda_core = lambda_core
        self._machines: dict[str, StateMachine] = {}
        self._names: dict[str, str] | None = None

    @property
    def client(self) -> Any:
        """
        The Step Functions client, made on first use so a missing region shows up as a readable error.
        """
        return self._cached_client("stepfunctions", self._make_client)

    def _make_client(self) -> Any:
        try:
            return self.session.client("stepfunctions", config=self._config)
        except NoRegionError:
            raise ValueError(
                "No AWS region is set, and state machines are regional. Pass one: "
                "StepFunctionsView(StepFunctionsAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
            ) from None

    def _cloudwatch(self) -> Any:
        return self._cached_client(
            "cloudwatch", lambda: self.session.client("cloudwatch", region_name=self.region, config=self._config))

    @property
    def lambda_core(self) -> Any:
        """
        The LambdaAnalyzer that reads Lambda steps' logs, on this analyzer's session.
        """
        if self._lambda_core is None:
            with self._lock:
                if self._lambda_core is None:
                    self._lambda_core = _lambda_module().LambdaAnalyzer(self.session)
        return self._lambda_core


    def list_state_machines(self) -> list[dict[str, Any]]:
        """
        Every state machine in the region as ListStateMachines returns it (name, ARN, type, creation date).
        """
        machines = self._paginate("list_state_machines", "stateMachines")
        self._names = {m["name"]: m["stateMachineArn"] for m in machines}
        return machines

    def resolve(self, ref: str) -> str:
        """
        A state machine's ARN from its name or ARN; an unknown name raises a _Hint with the closest names.
        """
        text = (ref or "").strip()
        if STATE_MACHINE_ARN_RE.match(text):
            return text
        names = self._names if self._names is not None and text in self._names else None
        if names is None:
            self.list_state_machines()
            names = self._names or {}
        if text in names:
            return names[text]
        close = difflib.get_close_matches(text, list(names), n=3, cutoff=0.5)
        hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
        raise _Hint(f"No state machine named {text!r} in {self.region}.{hint} overview() lists them.")

    def state_machine(self, ref: str) -> StateMachine:
        """
        One state machine: its type, definition (read into states), logging and tracing.
        """
        arn = self.resolve(ref)
        if arn not in self._machines:
            described = self.client.describe_state_machine(stateMachineArn=arn)
            self._machines[arn] = parse_state_machine(described)
        return self._machines[arn]

    def state_machines(self) -> tuple[list[StateMachine], dict[str, str]]:
        """
        Every state machine in the region, described, and the ones that couldn't be (name -> error code).
        """
        errors: dict[str, str] = {}

        def describe(item: dict[str, Any]) -> StateMachine | None:
            try:
                return self.state_machine(item["stateMachineArn"])
            except ClientError as exc:
                errors[item["name"]] = _error_code(exc)
                return StateMachine(name=item["name"], arn=item["stateMachineArn"], type=item.get("type", ""),
                                    created=item.get("creationDate"), errors={"describe": _error_code(exc)})

        machines = [m for m in self._map(describe, self.list_state_machines()) if m is not None]
        return machines, errors


    def numbers(self, machines: list[StateMachine], *, days: int = 7) -> tuple[dict[str, MachineNumbers], int]:
        """
        Each state machine's CloudWatch numbers over the last `days` whole UTC days (ARN -> MachineNumbers), and how
        many metrics were read (GetMetricData bills per metric).
        """
        end = _midnight_after(_utcnow())
        start = end - timedelta(days=days)
        queries, slots = [], {}
        for i, machine in enumerate(machines):
            for j, (metric, (stat, field_name)) in enumerate(_METRIC_STAT_AND_FIELD.items()):
                query_id = f"m{i}_{j}"
                slots[query_id] = (machine.arn, field_name)
                queries.append({"Id": query_id, "MetricStat": {
                    "Metric": {"Namespace": "AWS/States", "MetricName": metric,
                               "Dimensions": [{"Name": "StateMachineArn", "Value": machine.arn}]},
                    "Period": _DAY_SECONDS, "Stat": stat}})
        numbers = {m.arn: MachineNumbers(days=days, daily_started=[0] * days, daily_failed=[0] * days)
                   for m in machines}
        for begin in range(0, len(queries), _QUERIES_PER_CALL):
            for result in self._metric_results(queries[begin: begin + _QUERIES_PER_CALL], start, end):
                arn, field_name = slots[result["Id"]]
                _add_metric(numbers[arn], field_name, result, start)
        return numbers, len(queries)

    def _metric_results(self, queries: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end}
        while True:
            answer = self._cloudwatch().get_metric_data(**params)
            results += answer.get("MetricDataResults", [])
            if not answer.get("NextToken"):
                return results
            params["NextToken"] = answer["NextToken"]

    def overview(self, *, days: int = 7) -> Overview:
        """
        Every state machine in the region with its numbers over the last `days` days. Sections that couldn't be read
        are in errors ('metrics', or a state machine's name).
        """
        machines, errors = self.state_machines()
        overview = Overview(region=self.region, days=days, machines=machines, errors=dict(errors))
        if not machines:
            return overview
        try:
            overview.numbers, overview.metrics_read = self.numbers(machines, days=days)
        except (ClientError, BotoCoreError) as exc:
            overview.errors["metrics"] = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        return overview


    def executions(
        self,
        ref: str,
        *,
        status: str | None = None,
        since: Any = "7d",
        limit: Any = 200,
    ) -> ExecutionPage:
        """
        A Standard state machine's executions started since `since`, newest first, at most `limit` (truncated says
        when it stopped there). status= keeps one outcome: 'failed', 'running', 'succeeded', 'timed_out', 'aborted'.
        """
        machine = self.state_machine(ref)
        _refuse_express(machine, "executions()")
        start = parse_time(since) if since is not None else None
        cap = _as_count(limit, "limit")
        page = ExecutionPage(machine, since=start)
        page.executions, page.truncated = self._list_executions(machine, _status_filter(status), start, cap)
        return page

    def _list_executions(self, machine: StateMachine, status: str | None, start: datetime | None,
                         cap: int | None) -> tuple[list[Execution], bool]:
        """
        ListExecutions newest first, page by page, until one is older than `start` or `cap` are read.
        """
        found: list[Execution] = []
        params: dict[str, Any] = {"stateMachineArn": machine.arn, "maxResults": _LIST_PAGE}
        if status:
            params["statusFilter"] = status
        while True:
            answer = self.client.list_executions(**params)
            for item in answer.get("executions", []):
                execution = parse_execution(item, machine.name)
                if start is not None and execution.start is not None and execution.start < start:
                    return found, False
                if cap is not None and len(found) >= cap:
                    return found, True
                found.append(execution)
            if not answer.get("nextToken"):
                return found, False
            params["nextToken"] = answer["nextToken"]

    def execution_arn(self, ref: str, *, machine: str | None = None) -> str:
        """
        An execution's ARN from its ARN, a console link, 'machine/name', or a name with machine=.
        """
        named_machine, name = parse_execution_ref(ref)
        if EXECUTION_ARN_RE.match(name):
            return name
        machine = named_machine or machine
        if not machine:
            raise _Hint(f"Which state machine is {name!r} from? Name it too: execution('<state machine>/{name}'), "
                        "or pass the execution's ARN (executions('<state machine>') lists them).")
        return execution_arn(self.resolve(machine), name)

    def execution(self, ref: str, *, machine: str | None = None, max_events: Any = 5_000,
                  logs: bool = True) -> ExecutionDetail:
        """
        One execution with every step it took, read from its history (up to max_events events), and for a failed
        Lambda step that run's log lines (logs=False skips them).
        """
        arn = self.execution_arn(ref, machine=machine)
        described = self.client.describe_execution(executionArn=arn)
        state_machine = self.state_machine(described["stateMachineArn"])
        detail = ExecutionDetail(parse_execution(described, state_machine.name), machine=state_machine)
        events, detail.partial = self._history(arn, _as_count(max_events, "max_events"))
        fill_detail(detail, events)
        step = detail.failed_step
        if logs and step is not None and step.function:
            detail.lambda_run, detail.lambda_note = self.lambda_run(step)
        return detail

    def _history(self, arn: str, cap: int | None) -> tuple[list[dict[str, Any]], bool]:
        events: list[dict[str, Any]] = []
        params: dict[str, Any] = {"executionArn": arn, "maxResults": 1000, "includeExecutionData": True}
        while True:
            answer = self.client.get_execution_history(**params)
            events += answer.get("events", [])
            if cap is not None and len(events) >= cap:
                return events[:cap], bool(answer.get("nextToken")) or len(events) > cap
            if not answer.get("nextToken"):
                return events, False
            params["nextToken"] = answer["nextToken"]

    def _history_tail(self, arn: str) -> list[dict[str, Any]]:
        """
        The newest events of an execution's history, where a failure is: one request whatever its length.
        """
        answer = self.client.get_execution_history(executionArn=arn, reverseOrder=True, maxResults=_TAIL_EVENTS,
                                                   includeExecutionData=True)
        return answer.get("events", [])


    def lambda_run(self, step: StepRun) -> tuple[Any, str]:
        """
        The Lambda run behind a step's last try (a LogRun from aws_analyzer.lambda_functions) and a note on how it
        was found: by its request ID when the history has one, else by time (the run that started just after the
        try was scheduled, which may be another call's when the function runs often). (None, why) when it can't be
        read.
        """
        attempt = step.attempts[-1] if step.attempts else None
        if attempt is None or not step.function:
            return None, ""
        region = step.function_region or None
        try:
            if step.request_id:
                run = self._run_by_request(step, attempt, region)
                return run, "found by its request ID" if run else "no run with that request ID is in its logs"
            run = self._run_by_time(step, attempt, region)
            return run, ("matched by time: the history names no request ID" if run
                         else "no run started in its logs while the step ran")
        except ClientError as exc:
            return None, _log_failure(exc, step.function)
        except BotoCoreError as exc:
            return None, f"its logs couldn't be read ({type(exc).__name__})"

    def execution_story(self, detail: ExecutionDetail, *, most: int = _STORY_STEPS) -> ExecutionStory:
        """
        An execution's whole log: Step Functions' own lines and the log lines of the Lambda run behind each Lambda
        step (at most `most` steps, the failed ones first, read side by side), merged by time.
        """
        steps, skipped = lambda_steps(detail, most)
        found = self._map(self.lambda_run, steps)
        runs = [StoryRun(step, run, note) for step, (run, note) in zip(steps, found)]
        lambda_lines = [_run_lines(story_run) for story_run in runs if story_run.run is not None]
        return ExecutionStory(merge_log(step_lines(detail), *lambda_lines), runs, skipped)

    def _run_by_request(self, step: StepRun, attempt: Attempt, region: str | None) -> Any:
        ended = attempt.ended or attempt.scheduled
        page = self.lambda_core.log_events(step.function, since=attempt.scheduled - _LOG_PAD, until=ended + _LOG_PAD,
                                           request_id=step.request_id, region=region)
        _raise_log_error(page)
        runs = _lambda_module().split_runs(page.events)
        return next((run for run in runs if run.request_id == step.request_id), None)

    def _run_by_time(self, step: StepRun, attempt: Attempt, region: str | None) -> Any:
        ended = attempt.ended or attempt.scheduled
        page = self.lambda_core.log_events(step.function, since=attempt.scheduled, until=ended + _LOG_PAD,
                                           limit=_TIME_MATCH_LINES, region=region)
        _raise_log_error(page)
        runs = [run for run in _lambda_module().split_runs(page.events) if run.request_id]
        started = [run for run in runs if run.start >= attempt.scheduled - timedelta(seconds=1)]
        return min(started, key=lambda run: run.start) if started else None


    def failures(
        self,
        ref: str,
        *,
        since: Any = "7d",
        limit: Any = 50,
        progress: Callable[..., None] | None = None,
    ) -> FailureReport:
        """
        Why a Standard state machine's executions failed since `since`: the newest `limit` that failed, timed out or
        were aborted, each history's end read (one request each), grouped by the state they stopped at and the error.
        """
        machine = self.state_machine(ref)
        _refuse_express(machine, "failures()")
        start = parse_time(since) or _utcnow() - timedelta(days=7)
        cap = _as_count(limit, "limit")
        failed, truncated = self._failed_executions(machine, start, cap)
        report = FailureReport(machine, start, failed=failed, truncated=truncated)

        def read(execution: Execution) -> ExecutionDetail:
            return fill_detail(ExecutionDetail(execution, machine=machine), self._history_tail(execution.arn))

        details = []
        for detail in self._map(read, failed):
            details.append(detail)
            if progress is not None:
                progress(len(details), len(failed))
        report.read = len(details)
        report.groups = group_failures(details, _utcnow(), express=machine.is_express)
        try:
            days = max(1, (_utcnow() - start).days)
            report.numbers = self.numbers([machine], days=days)[0].get(machine.arn)
        except (ClientError, BotoCoreError):
            report.numbers = None
        return report

    def _failed_executions(self, machine: StateMachine, start: datetime,
                           cap: int | None) -> tuple[list[Execution], bool]:
        found: list[Execution] = []
        truncated = False
        for status in FAILED_STATUSES:
            executions, cut = self._list_executions(machine, status, start, cap)
            found += executions
            truncated = truncated or cut
        found.sort(key=lambda e: e.start or start, reverse=True)
        if cap is not None and len(found) > cap:
            return found[:cap], True
        return found, truncated


def _add_metric(numbers: MachineNumbers, field_name: str, result: dict[str, Any], start: datetime) -> None:
    """
    One metric's daily values into MachineNumbers: summed (an average is weighted by the day's executions, which
    are read first), with the daily started and failed counts kept per day.
    """
    values = list(zip(result.get("Timestamps", []), result.get("Values", [])))
    if field_name == "avg_ms":
        weights = {_day(ts, start): numbers.daily_started[_day(ts, start)] for ts, _ in values
                   if 0 <= _day(ts, start) < numbers.days}
        total_weight = sum(weights.values())
        if values and total_weight:
            numbers.avg_ms = sum(v * weights.get(_day(ts, start), 0) for ts, v in values) / total_weight
        elif values:
            numbers.avg_ms = sum(v for _, v in values) / len(values)
        return
    setattr(numbers, field_name, getattr(numbers, field_name) + int(sum(v for _, v in values)))
    daily = {"started": numbers.daily_started, "failed": numbers.daily_failed}.get(field_name)
    if daily is None:
        return
    for ts, value in values:
        day = _day(ts, start)
        if 0 <= day < numbers.days:
            daily[day] += int(value)


def _day(moment: datetime, start: datetime) -> int:
    return int((moment - start).total_seconds() // _DAY_SECONDS)


def _status_filter(status: str | None) -> str | None:
    """
    'failed' / 'timed out' / 'TIMED_OUT' -> ListExecutions' statusFilter; None for every execution.
    """
    if not status:
        return None
    value = status.strip().upper().replace(" ", "_").replace("-", "_")
    allowed = ("RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED", "PENDING_REDRIVE")
    if value not in allowed:
        raise ValueError(f"status= is one of {', '.join(a.lower() for a in allowed)}, not {status!r}")
    return value


def _refuse_express(machine: StateMachine, command: str) -> None:
    if machine.is_express:
        where = (f"its log group ({machine.log_groups[0]})" if machine.log_groups
                 else "its logs, once logging is on (state_machine() shows how)")
        raise _Hint(f"{machine.name} is an Express workflow: Step Functions keeps no list or history of its "
                    f"executions, so {command} can't read them. They're in {where}.")


def _raise_log_error(page: Any) -> None:
    """
    A Lambda log page that couldn't be read raises its error, so lambda_run() says why.
    """
    code = (page.errors or {}).get("logs")
    if code:
        raise ClientError({"Error": {"Code": code, "Message": code}}, "FilterLogEvents")


def _log_failure(exc: ClientError, function: str) -> str:
    """
    Why a Lambda step's run couldn't be read, in a few words: the function is gone, it has no log group, or the
    error's code.
    """
    code = _error_code(exc)
    if code == "ResourceNotFoundException" and exc.operation_name == "GetFunction":
        return f"{function} no longer exists in this region"
    if code == "ResourceNotFoundException":
        return f"{function} has no log group (/aws/lambda/{function}): it hasn't logged, or it logs elsewhere"
    return f"its logs couldn't be read ({code})"
