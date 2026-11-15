"""
compare(): an execution beside another, by default the newest that succeeded before it (ListExecutions with
statusFilter SUCCEEDED, or an Express workflow's log group), both read as execution() reads them.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .._kit.errors import _Hint

from .models import Execution, ExecutionDetail
from .analysis.compare import ExecutionComparison, compare_executions

if TYPE_CHECKING:
    from .analyzer import StepFunctionsAnalyzer


SUCCESSES_READ = 50

SUCCESS_WINDOW = "30d"


def _newest_before(successes: list[Execution], start: datetime | None) -> Execution | None:
    """
    Of executions newest first, the newest that started before `start`, else the newest of all (None when there are
    none).
    """
    if not successes:
        return None
    if start is not None:
        before = [e for e in successes if e.start is not None and e.start < start]
        if before:
            return before[0]
    return successes[0]


class _CompareReads:
    """
    The reads behind compare(), a part of StepFunctionsAnalyzer.
    """

    def last_success(self: StepFunctionsAnalyzer, detail: ExecutionDetail, *, since: Any = SUCCESS_WINDOW
                     ) -> Execution | None:
        """
        The newest execution of the same state machine that succeeded before this one started (else the newest that
        succeeded at all), from the newest SUCCESSES_READ since `since`; None when none did.
        """
        machine = detail.machine.arn if detail.machine is not None else detail.execution.machine
        page = self.executions(machine, status="succeeded", since=since, limit=SUCCESSES_READ)
        return _newest_before(page.executions, detail.execution.start)

    def compare(self: StepFunctionsAnalyzer, ref: str, *, other: str | None = None, machine: str | None = None,
                since: Any = SUCCESS_WINDOW) -> ExecutionComparison:
        """
        An execution beside another (other=, an execution of the same state machine), by default the newest that
        succeeded before it in the last `since`: which states each went through, how they ran, and which of its
        input's fields differ. Both are read without their Lambda runs' lines.
        """
        this = self.execution(ref, machine=machine, logs=False)
        if other is not None:
            named = this.machine.arn if this.machine is not None else this.execution.machine
            return compare_executions(this, self.execution(other, machine=named, logs=False))
        success = self.last_success(this, since=since)
        if success is None:
            raise _Hint(f"No execution of {this.execution.machine} succeeded in the last {since}, so there's none to "
                        f"compare {this.execution.name} with. Name one: compare('{this.execution.name}', "
                        "other='...'), or look further back with since='90d'.")
        found = self.execution(success.arn, logs=False, started=success.start)
        return compare_executions(this, found)
