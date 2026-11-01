"""
Failed executions grouped by the state they stopped at and the error, and which of them can still be redriven.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Execution, ExecutionDetail, FailureGroup
from .history import cause_of, failure_key


REDRIVE_DAYS = 14


def can_redrive(execution: Execution, now: datetime, *, express: bool = False) -> bool:
    """
    Whether an execution can still be redriven (started again from the state that failed): a Standard execution
    that failed, timed out or was aborted less than 14 days ago, unless DescribeExecution said it can't.
    """
    if express or not execution.failed or execution.stop is None:
        return False
    if execution.redrive_status and execution.redrive_status != "REDRIVABLE":
        return False
    return now - execution.stop < timedelta(days=REDRIVE_DAYS)


def group_failures(details: list[ExecutionDetail], now: datetime, *, express: bool = False) -> list[FailureGroup]:
    """
    Failed executions -> one FailureGroup per (state, error), the biggest first: how many, the newest one, and for
    a Lambda step its function and the newest run's request ID.
    """
    groups: dict[tuple[str, str], FailureGroup] = {}
    for detail in sorted(details, key=_started, reverse=True):
        state, error = failure_key(detail)
        group = groups.get((state, error))
        if group is None:
            step = detail.failed_step
            group = FailureGroup(state=state, error=error, cause=cause_of(detail), newest=detail.execution,
                                 function=step.function if step is not None else "",
                                 request_id=step.request_id if step is not None else "")
            groups[(state, error)] = group
        group.count += 1
        if can_redrive(detail.execution, now, express=express):
            group.redrivable += 1
    return sorted(groups.values(), key=lambda g: -g.count)


def _started(detail: ExecutionDetail) -> datetime:
    return detail.execution.start or datetime.min
