"""
Whether a change explains a kind of failure: for each failure group, the change just before it began (a deploy of the
function it stopped in, or the definition updated, within SUSPECT_WINDOW of its first failure), a change after it
began that it went on failing past, or nothing that changed nearby. Timing, not proof, and the findings say so.
Pure: run it on a FailureReport whose changes you filled yourself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..._kit.fmt import _fmt_dt, _plural, human_age
from ..._kit.render import _call

from ..helpers import execution_ref
from ..models import Change, FailureGroup, FailureReport


Finding = tuple[str, str]

SUSPECT_WINDOW = timedelta(hours=48)

WINDOW_EDGE = timedelta(hours=6)

GROUPS_JUDGED = 3

_MINUTE = 60

_HOUR = 3_600

_DAY = 86_400


@dataclass
class ChangeVerdict:
    """
    What changed around one kind of failure: the change just before its first failure (before), one after it that
    the failures went on past (after), the latest known change of each thing it depends on (known), and whether its
    first failure is the real start (exact: False when the failures read may not go back far enough).
    """

    group: FailureGroup
    first: datetime
    before: Change | None = None
    after: Change | None = None
    known: list[Change] = field(default_factory=list)
    exact: bool = True


def gap_text(gap: timedelta) -> str:
    """
    timedelta(minutes=4) -> '4 minutes', 26 hours -> '26 hours', 3 days -> '3 days'.
    """
    seconds = int(abs(gap.total_seconds()))
    if seconds < _HOUR:
        return _plural(max(1, round(seconds / _MINUTE)), "minute")
    if seconds < 2 * _DAY:
        return _plural(round(seconds / _HOUR), "hour")
    return _plural(round(seconds / _DAY), "day")


def change_text(change: Change, machine: str) -> str:
    """
    A change as a sentence's subject: 'orders-etl version 7 was published', 'orders-etl was deployed',
    "order-pipeline's definition was updated".
    """
    if change.kind == "definition":
        return f"{machine}'s definition was updated"
    if change.detail:
        return f"{change.name} {change.detail} was published"
    return f"{change.name} was deployed"


def relevant_changes(group: FailureGroup, changes: list[Change]) -> list[Change]:
    """
    The changes a kind of failure could come from: the definition's, and the deploys of the function it stopped in.
    """
    return [c for c in changes if c.kind == "definition" or (c.kind == "function" and c.name == group.function)]


def _latest_of_each(changes: list[Change]) -> list[Change]:
    latest: dict[tuple[str, str], Change] = {}
    for change in changes:
        key = (change.kind, change.name)
        if key not in latest or change.when > latest[key].when:
            latest[key] = change
    return sorted(latest.values(), key=lambda c: c.when)


def change_verdict(report: FailureReport, group: FailureGroup) -> ChangeVerdict | None:
    """
    What changed around a kind of failure (None when it has no dated execution). Its first failure is exact when the
    read wasn't cut short and it started more than WINDOW_EDGE after the window did.
    """
    first = group.first_seen
    if first is None:
        return None
    changes = relevant_changes(group, report.changes)
    exact = not report.truncated and first - report.since > WINDOW_EDGE
    verdict = ChangeVerdict(group, first, known=_latest_of_each([c for c in changes if c.when <= first]), exact=exact)
    before = [c for c in changes if first - SUSPECT_WINDOW <= c.when <= first]
    if before:
        verdict.before = before[-1]
        return verdict
    newest = group.newest.start if group.newest is not None else None
    after = [c for c in changes if c.when > first and newest is not None and c.when < newest]
    verdict.after = after[-1] if after else None
    return verdict


def _next_call(change: Change, report: FailureReport) -> str:
    if change.kind == "function":
        return f"LambdaView().{_call('function_info', change.name)}"
    return _call("state_machine", report.machine.name)


def _began(verdict: ChangeVerdict) -> str:
    group = verdict.group
    return f"{group.state} began failing with {group.error}"


def _before_finding(verdict: ChangeVerdict, report: FailureReport) -> Finding:
    change, group = verdict.before, verdict.group
    gap = gap_text(verdict.first - change.when)
    earlier = "" if verdict.exact else (f" It may have begun earlier: the failures read go back to "
                                        f"{_fmt_dt(report.since, zone=True)}.")
    return ("warn", f"{_began(verdict)} {gap} after {change_text(change, report.machine.name)}: the first of these "
                    f"{_plural(group.count, 'failure')} started {_fmt_dt(verdict.first, zone=True)}, and the change "
                    f"was at {_fmt_dt(change.when, zone=True)}.{earlier} Timing, not proof, but it's the first thing "
                    f"to check: {_next_call(change, report)}")


def _after_finding(verdict: ChangeVerdict, report: FailureReport) -> Finding:
    change = verdict.after
    return ("info", f"{change_text(change, report.machine.name)} {human_age(change.when)}, after "
                    f"{verdict.group.state} began failing with {verdict.group.error}, and it went on failing: that "
                    "change didn't fix it.")


def _quiet_finding(verdict: ChangeVerdict, report: FailureReport) -> Finding:
    group = verdict.group
    last = "; ".join(f"{change_text(c, report.machine.name)} {gap_text(verdict.first - c.when)} before"
                     for c in verdict.known)
    look = (f" See what a failing execution was given: "
            f"{_call('execution', execution_ref(report.machine.name, group.newest.name, group.newest.arn))}"
            if group.newest is not None else "")
    return ("info", f"Nothing changed in the {gap_text(SUSPECT_WINDOW)} before {_began(verdict)}: {last}. The cause "
                    f"is more likely in what it was given, or in a service it calls.{look}")


def group_change_findings(report: FailureReport, group: FailureGroup) -> list[Finding]:
    """
    What changed around one kind of failure, as a finding: the change just before it began (a warning), one after it
    that it went on failing past, or nothing nearby (said only when its start is exact and something is known).
    """
    verdict = change_verdict(report, group)
    if verdict is None:
        return []
    if verdict.before is not None:
        return [_before_finding(verdict, report)]
    if verdict.after is not None:
        return [_after_finding(verdict, report)]
    if verdict.exact and verdict.known:
        return [_quiet_finding(verdict, report)]
    return []


def change_findings(report: FailureReport) -> list[Finding]:
    """
    What changed around the GROUPS_JUDGED most common kinds of failure, and a note when what changed couldn't be read.
    """
    found: list[Finding] = []
    for group in report.groups[:GROUPS_JUDGED]:
        found += group_change_findings(report, group)
    if report.change_errors:
        missing = ", ".join(f"{part} ({code})" for part, code in sorted(report.change_errors.items()))
        found.append(("info", f"Couldn't read what changed before these failures: {missing}. Reading deploys needs "
                              "lambda:GetFunction and lambda:ListVersionsByFunction; the definition's updates, "
                              "states:DescribeStateMachineForExecution."))
    return found
