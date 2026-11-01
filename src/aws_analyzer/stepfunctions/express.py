"""
_ExpressReads, the part of StepFunctionsAnalyzer that reads Express executions from their state machine's log group:
Step Functions keeps no list or history of them, but logs each event as a JSON line when logging is on.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .._kit.errors import _Hint
from .._kit.fmt import _utcnow

from .helpers import FAILED_STATUSES, arn_region
from .models import Execution, ExecutionDetail, ExecutionPage, StateMachine
from .analysis import fill_detail
from .analysis.express import (
    LoggedRun, arn_pattern, endings_pattern, execution_of, group_log_lines, log_group_name, logging_gaps,
)

if TYPE_CHECKING:
    from .analyzer import StepFunctionsAnalyzer


EXPRESS_LONGEST = timedelta(minutes=5)

_LINE_BUDGET = 20_000

_FIRST_SLICE = timedelta(hours=1)

_PAD = timedelta(minutes=1)

_SEARCH_BACK = timedelta(days=14)

_SLICE_READS = 4


class _ExpressReads:
    """
    Express executions, read from the log group a state machine's logging configuration names (CloudWatch Logs
    FilterLogEvents, which only reads).
    """

    def _logs_client(self: StepFunctionsAnalyzer, region: str | None) -> Any:
        key = "logs" if region in (None, self.region) else f"logs:{region}"
        return self._cached_client(key, lambda: self.session.client("logs", region_name=region or self.region,
                                                                    config=self._config))

    def _express_group(self: StepFunctionsAnalyzer, machine: StateMachine) -> tuple[str, str | None]:
        """
        The log group an Express workflow's executions are in, and its region; a _Hint that says how to turn logging
        on when it's off.
        """
        if machine.log_level == "OFF" or not machine.log_groups:
            raise _Hint(f"{machine.name} is an Express workflow with logging off: Step Functions keeps no list or "
                        "history of its executions, so there's nothing to read. state_machine() shows the command "
                        "that turns logging on; executions after that show up here.")
        arn = machine.log_groups[0]
        return log_group_name(arn), arn_region(arn)

    def _read_lines(self: StepFunctionsAnalyzer, group: tuple[str, str | None], start: datetime, end: datetime, *,
                    pattern: str | None = None, budget: int | None = None) -> tuple[list[str], datetime | None]:
        """
        The messages of a log group between start and end, the newest first: read back from end in slices that
        double, so when there are more than `budget` lines the newest are kept. Returns the messages and how far back
        they cover when the budget (_LINE_BUDGET lines unless given) ran out (None when they cover the whole window).
        """
        budget = budget or _LINE_BUDGET
        messages: list[str] = []
        slice_end, size = end, _FIRST_SLICE
        while slice_end > start:
            slice_start = max(start, slice_end - size)
            messages += self._read_slice(group, slice_start, slice_end, pattern, budget - len(messages))
            if len(messages) >= budget:
                return messages, slice_start
            slice_end, size = slice_start, size * 2
        return messages, None

    def _read_slice(self: StepFunctionsAnalyzer, group: tuple[str, str | None], start: datetime, end: datetime,
                    pattern: str | None, budget: int) -> list[str]:
        """
        A slice's messages, keeping its newest `budget`: FilterLogEvents answers oldest first, so it reads on (up to
        _SLICE_READS times the budget) and keeps the tail.
        """
        name, region = group
        params: dict[str, Any] = {"logGroupName": name, "startTime": int(start.timestamp() * 1000),
                                  "endTime": int(end.timestamp() * 1000)}
        if pattern:
            params["filterPattern"] = pattern
        messages: list[str] = []
        client = self._logs_client(region)
        while len(messages) < budget * _SLICE_READS:
            answer = client.filter_log_events(**params)  # read-only: FilterLogEvents only reads log events
            messages += [event.get("message", "") for event in answer.get("events", [])]
            if not answer.get("nextToken"):
                break
            params["nextToken"] = answer["nextToken"]
        return messages[-budget:] if budget else []

    def _express_page(self: StepFunctionsAnalyzer, machine: StateMachine, status: str | None,
                      start: datetime | None, cap: int | None) -> ExecutionPage:
        """
        An Express workflow's executions since `start`, newest first, from its log group: every line in the window
        (status= only keeps the ones that ended that way).
        """
        group = self._express_group(machine)
        end = _utcnow()
        since = start or end - _SEARCH_BACK
        messages, covered_from = self._read_lines(group, since, end)
        everything = machine.log_level == "ALL"
        executions = [execution_of(run, logs_everything=everything) for run in group_log_lines(messages)]
        if status:
            executions = [e for e in executions if e.status == status]
        truncated = covered_from is not None or (cap is not None and len(executions) > cap)
        page = ExecutionPage(machine, executions[:cap] if cap is not None else executions, since=since,
                             truncated=truncated, source="logs", covered_from=covered_from)
        page.note = logging_gaps(machine)
        return page

    def _express_lines(self: StepFunctionsAnalyzer, machine: StateMachine, arn: str,
                       started: datetime | None) -> LoggedRun | None:
        """
        One Express execution's lines: from a minute before it started to a minute past the longest an Express
        execution can run, when its start is known; else searched for over the last 14 days.
        """
        group = self._express_group(machine)
        end = _utcnow()
        if started is not None:
            window = (started - _PAD, min(end, started + EXPRESS_LONGEST + _PAD))
        else:
            window = (end - _SEARCH_BACK, end)
        messages, _ = self._read_lines(group, *window, pattern=arn_pattern(arn))
        runs = [run for run in group_log_lines(messages) if run.arn == arn]
        return runs[0] if runs else None

    def _express_detail(self: StepFunctionsAnalyzer, machine: StateMachine, arn: str,
                        started: datetime | None) -> ExecutionDetail:
        run = self._express_lines(machine, arn, started)
        if run is None:
            raise _Hint(f"No lines for {arn.rsplit(':', 2)[-2]} in {machine.name}'s log group. Express executions "
                        "are only there while the log group keeps them (its retention), and at ERROR level only the "
                        "ones that failed are logged.")
        execution = execution_of(run, logs_everything=machine.log_level == "ALL")
        detail = ExecutionDetail(execution, machine=machine, source="logs")
        return fill_detail(detail, run.events)

    def _express_failures(self: StepFunctionsAnalyzer, machine: StateMachine, start: datetime,
                          cap: int | None) -> tuple[list[ExecutionDetail], bool]:
        """
        The Express executions that failed, timed out or were aborted since `start` (the newest `cap`), each read
        from its lines: their ending lines first, then each one's lines around its ending.
        """
        group = self._express_group(machine)
        endings, covered_from = self._read_lines(group, start, _utcnow(), pattern=endings_pattern(FAILED_STATUSES))
        ended = group_log_lines(endings)
        cut = covered_from is not None or (cap is not None and len(ended) > cap)
        chosen = ended[:cap] if cap is not None else ended

        def read(run: LoggedRun) -> ExecutionDetail:
            moment = run.events[-1]["timestamp"]
            messages, _ = self._read_lines(group, moment - EXPRESS_LONGEST - _PAD, moment + _PAD,
                                           pattern=arn_pattern(run.arn))
            whole = next((r for r in group_log_lines(messages) if r.arn == run.arn), run)
            execution: Execution = execution_of(whole, logs_everything=machine.log_level == "ALL")
            return fill_detail(ExecutionDetail(execution, machine=machine, source="logs"), whole.events)

        return self._map(read, chosen), cut
