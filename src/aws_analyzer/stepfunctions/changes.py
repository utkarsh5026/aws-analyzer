"""
What changed around a state machine's failures, for failures(): when each Lambda function its failure groups stopped
in was deployed (GetFunction, and ListVersionsByFunction for the versions published), and when its definition was
last updated as of each group's first failure (DescribeStateMachineForExecution). Only reads: a part it can't read is
noted in report.change_errors, and the rest still comes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_name

from .models import Change, Execution, FailureReport

if TYPE_CHECKING:
    from .analyzer import StepFunctionsAnalyzer


SAME_DEPLOY = timedelta(minutes=1)


def _first_failures(report: FailureReport) -> list[Execution]:
    """
    Each failure group's oldest execution read: the definition it ran with dates the definition that failure began on.
    """
    firsts = []
    for group in report.groups:
        dated = [e for e in group.executions if e.start is not None]
        if dated:
            firsts.append(min(dated, key=lambda e: e.start or datetime.max))
    return firsts


class _ChangeReads:
    """
    The reads behind FailureReport.changes, a part of StepFunctionsAnalyzer.
    """

    def _function_changes(self: StepFunctionsAnalyzer, name: str) -> list[Change]:
        """
        When a Lambda function was deployed: each published version, and its last change when no version was published
        with it (its code or settings changed on $LATEST).
        """
        lambda_core = self.lambda_core
        function = lambda_core.function(name)
        changes = [Change(v.last_modified, "function", name, f"version {v.version}")
                   for v in lambda_core.versions(name) if v.last_modified is not None]
        last = function.last_modified
        is_published = any(abs(change.when - last) <= SAME_DEPLOY for change in changes) if last else True
        if last is not None and not is_published:
            changes.append(Change(last, "function", name))
        return changes

    def _definition_changes(self: StepFunctionsAnalyzer, report: FailureReport) -> list[Change]:
        """
        When the definition each failure group's first execution ran with was last updated, once per date.
        """
        dates = set()
        for execution in _first_failures(report):
            answer = self.client.describe_state_machine_for_execution(executionArn=execution.arn)
            if answer.get("updateDate"):
                dates.add(answer["updateDate"])
        return [Change(when, "definition", report.machine.name) for when in sorted(dates)]

    def _read_changes(self: StepFunctionsAnalyzer, report: FailureReport) -> None:
        """
        report.changes, oldest first: the deploys of the functions its groups stopped in, and (Standard only: Express
        executions can't be described) the definition's updates.
        """
        changes: list[Change] = []
        for name in sorted({group.function for group in report.groups if group.function}):
            try:
                changes += self._function_changes(name)
            except (ClientError, BotoCoreError) as exc:
                report.change_errors[f"lambda:{name}"] = _error_name(exc)
        if report.groups and not report.machine.is_express:
            try:
                changes += self._definition_changes(report)
            except (ClientError, BotoCoreError) as exc:
                report.change_errors["definition"] = _error_name(exc)
        report.changes = sorted(changes, key=lambda change: change.when)
