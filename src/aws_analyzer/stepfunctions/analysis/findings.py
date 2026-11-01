"""
What the reports warn about, as ("warn" | "info", message) pairs: failures and throttles in a state machine's
numbers, logging that hides Express executions, Lambda tasks with no retry, waits with no timeout, and for one
execution or a window of failures, what broke, what to read next and how to redrive.
"""

from __future__ import annotations

from datetime import datetime

from ..._kit.fmt import _duration, _plural, human_money
from ..._kit.render import _call

from ..helpers import STATUS_WORDS, short_cause
from ..models import ExecutionDetail, FailureReport, MachineNumbers, Overview, StateMachine
from .cost import monthly_cost
from .definition import lambda_retry_block, unbounded_waits, unretried_lambda_states
from .failures import REDRIVE_DAYS, can_redrive


Finding = tuple[str, str]

FAILURE_RATE_WARN = 0.01

_MAX_NAMED = 3

_LOGGING_COMMAND = ("aws stepfunctions update-state-machine --state-machine-arn {arn} --logging-configuration "
                    "file://logging.json, with logging.json holding "
                    '{{"level": "ALL", "includeExecutionData": true, "destinations": [{{"cloudWatchLogsLogGroup": '
                    '{{"logGroupArn": "{group}"}}}}]}}')

_REDRIVE_COMMAND = "aws stepfunctions redrive-execution --execution-arn {arn}"


def _names(names: list[str]) -> str:
    """
    'a', 'a and b', 'a, b and c', 'a, b, c and 2 more'.
    """
    if len(names) <= 1:
        return "".join(names)
    shown = names[:_MAX_NAMED]
    rest = len(names) - len(shown)
    if rest:
        return f"{', '.join(shown)} and {rest} more"
    return f"{', '.join(shown[:-1])} and {shown[-1]}"


def lambda_logs_call(function: str, request_id: str) -> str:
    """
    The Lambda report that shows one run, to copy.
    """
    return f"LambdaView().{_call('logs', function, request_id=request_id)}"


def logging_findings(machine: StateMachine) -> list[Finding]:
    """
    Express workflows keep no history: without logging there's no record of an execution at all, and at ERROR or
    FATAL only failures leave one.
    """
    if not machine.is_express:
        return []
    group = machine.log_groups[0] if machine.log_groups else (
        machine.arn.replace(":states:", ":logs:", 1).split(":stateMachine:")[0]
        + f":log-group:/aws/vendedlogs/states/{machine.name}:*")
    command = _LOGGING_COMMAND.format(arn=machine.arn, group=group)
    if machine.log_level == "OFF":
        return [("warn", f"{machine.name} is an Express workflow with logging off: none of its executions leave a "
                         f"record anywhere, so a failure can't be looked into. Turn logging on: {command}")]
    if machine.log_level in ("ERROR", "FATAL"):
        return [("info", f"{machine.name} logs at {machine.log_level} only: successful executions leave no record, "
                         f"so you can't compare a failure with a good run. Set level ALL to keep them: {command}")]
    return []


def definition_findings(machine: StateMachine) -> list[Finding]:
    """
    What the definition itself risks: Lambda tasks with no retry for Lambda's own errors, and waits that never time
    out.
    """
    found: list[Finding] = []
    unretried = unretried_lambda_states(machine)
    if unretried:
        names = _names([s.name for s in unretried])
        found.append(("info", f"{names} {'calls' if len(unretried) == 1 else 'call'} Lambda with no Retry for its "
                              "own errors: one throttle or a brief Lambda outage fails the whole execution. AWS "
                              f"recommends this rule on every Lambda task: {lambda_retry_block()}"))
    waits = unbounded_waits(machine)
    if waits:
        names = _names([s.name for s in waits])
        found.append(("warn", f"{names} {'waits' if len(waits) == 1 else 'wait'} for a callback with no "
                              "TimeoutSeconds or HeartbeatSeconds: if the answer never comes, the execution waits up "
                              "to a year, billed for nothing. Add \"HeartbeatSeconds\": 3600 (or a TimeoutSeconds)."))
    return found


def numbers_findings(machine: StateMachine, numbers: MachineNumbers | None) -> list[Finding]:
    """
    What a state machine's CloudWatch numbers say: executions that didn't succeed, starts that were throttled, and
    no executions at all.
    """
    if numbers is None:
        return []
    found: list[Finding] = []
    window = f"the last {_plural(numbers.days, 'day')}"
    if numbers.unsuccessful:
        level = "warn" if numbers.failure_rate >= FAILURE_RATE_WARN or numbers.timed_out else "info"
        parts = [f"{numbers.failed:,} failed" if numbers.failed else "",
                 f"{numbers.timed_out:,} timed out" if numbers.timed_out else "",
                 f"{numbers.aborted:,} aborted" if numbers.aborted else ""]
        detail = ", ".join(p for p in parts if p)
        where = (_call("failures", machine.name) if not machine.is_express
                 else "its log group (Express workflows have no history to read)")
        found.append((level, f"{numbers.unsuccessful:,} of {numbers.started:,} {machine.name} executions didn't "
                             f"succeed in {window} ({numbers.failure_rate:.1%}, {detail}). See where and why: "
                             f"{where}"))
    if numbers.throttled:
        found.append(("warn", f"Starting {machine.name} was throttled {numbers.throttled:,} times in {window}: "
                              "those executions never started unless the caller retried. Ask for a higher quota in "
                              "Service Quotas, or spread the starts out."))
    return found


def idle_findings(machine: StateMachine, numbers: MachineNumbers | None) -> list[Finding]:
    """
    A state machine that wasn't started in the window.
    """
    if numbers is None or numbers.started:
        return []
    return [("info", f"{machine.name} wasn't started in the last {_plural(numbers.days, 'day')}: if it's no longer "
                     "used, its definition and role can go (it costs nothing while idle).")]


def machine_findings(machine: StateMachine, numbers: MachineNumbers | None) -> list[Finding]:
    """
    Every finding about one state machine: its numbers, its logging and its definition.
    """
    return (numbers_findings(machine, numbers) + idle_findings(machine, numbers) + logging_findings(machine)
            + definition_findings(machine))


def overview_findings(overview: Overview, prices: dict[str, float]) -> list[Finding]:
    """
    The region's findings: each state machine's numbers and logging (definitions are checked in state_machine()),
    the warnings first, and the one that costs the most when it's most of the bill.
    """
    found: list[Finding] = []
    for machine in overview.machines:
        numbers = overview.numbers.get(machine.arn)
        found += numbers_findings(machine, numbers) + logging_findings(machine)
    idle = [m.name for m in overview.machines if idle_findings(m, overview.numbers.get(m.arn))]
    if idle:
        found.append(("info", f"{_names(idle)} {'was' if len(idle) == 1 else 'were'} not started in the last "
                              f"{_plural(overview.days, 'day')}."))
    costs = {m.name: monthly_cost(m, overview.numbers.get(m.arn), prices) or 0.0 for m in overview.machines}
    total = sum(costs.values())
    if total and len(costs) > 1:
        top, top_cost = max(costs.items(), key=lambda item: item[1])
        if top_cost / total >= 0.5:
            found.append(("info", f"{top} is most of the estimated bill: {human_money(top_cost)} of "
                                  f"{human_money(total)} a month. state_machine('{top}') shows where it comes from."))
    return sorted(found, key=lambda f: f[0] != "warn")


def _step_findings(detail: ExecutionDetail) -> list[Finding]:
    """
    The steps that needed more than one try, and what the retries cost in time.
    """
    found: list[Finding] = []
    for step in detail.steps:
        if not step.retries:
            continue
        errors = sorted({a.error for a in step.attempts[:-1] if a.error})
        waited = (step.seconds or 0) - sum(a.seconds or 0 for a in step.attempts)
        found.append(("info", f"{step.name} took {len(step.attempts)} tries ({', '.join(errors) or 'retried'}), "
                              f"adding {_duration(max(waited, 0))} of back-off."))
    return found


def execution_findings(detail: ExecutionDetail, now: datetime) -> list[Finding]:
    """
    What broke an execution and what to read next (the Lambda run behind a failed Lambda step), whether a Catch
    moved on, how to redrive it, and the steps that were retried.
    """
    execution, step = detail.execution, detail.failed_step
    found: list[Finding] = []
    if execution.failed and step is not None:
        error = short_cause(step.cause) if step.error and step.cause.lstrip().startswith("{") else ""
        error = error or step.error or execution.error or STATUS_WORDS.get(execution.status, execution.status)
        where = f" ({step.function})" if step.function else ""
        nxt = ""
        if step.function and step.request_id:
            nxt = (" Its Lambda run's lines are below." if detail.lambda_run is not None
                   else f" Read the Lambda run: {lambda_logs_call(step.function, step.request_id)}")
        found.append(("warn", f"{step.name}{where} failed and ended the execution. It returned {error}.{nxt}"))
        later = [s for s in detail.steps if s.entered > step.entered and s.depth <= step.depth]
        if later and step.status == "failed":
            found.append(("info", f"A Catch on {step.name} sent it to {later[0].name}, so the error was handled the "
                                  "way the definition says, and the execution still ended without succeeding."))
    elif execution.failed:
        found.append(("warn", f"It {STATUS_WORDS.get(execution.status, execution.status)}: {execution.error or ''} "
                              f"{execution.cause or ''}".rstrip()))
    express = detail.machine is not None and detail.machine.is_express
    if can_redrive(execution, now, express=express):
        found.append(("info", f"It can still be redriven (for {REDRIVE_DAYS} days after it stopped): that starts it "
                              "again from the state that failed, keeping the steps that succeeded. When the cause "
                              f"is fixed: {_REDRIVE_COMMAND.format(arn=execution.arn)}"))
    if detail.partial:
        found.append(("info", f"The history was read up to {_plural(detail.events, 'event')}, so later steps may be "
                              "missing. Pass max_events= for more."))
    return found + _step_findings(detail)


def failure_findings(report: FailureReport, now: datetime) -> list[Finding]:
    """
    Why a state machine fails: the biggest groups of failures with the run to read for each, how many can still be
    redriven, and how much of the window was read.
    """
    found: list[Finding] = []
    total = sum(g.count for g in report.groups)
    for group in report.groups[:_MAX_NAMED]:
        share = f" ({group.count / total:.0%} of the failures read)" if total and len(report.groups) > 1 else ""
        where = f" in {group.function}" if group.function else ""
        read = ""
        if group.function and group.request_id:
            read = f" Read the newest run: {lambda_logs_call(group.function, group.request_id)}"
        elif group.newest is not None:
            read = f" See one: {_call('execution', f'{report.machine.name}/{group.newest.name}')}"
        cause = f" {group.cause.rstrip('.')}." if group.cause else ""
        what = (f"{group.state}{where} was still running when {_plural(group.count, 'execution')} timed out"
                if group.error == "States.Timeout" else
                f"{group.state} fails with {group.error}{where}: {_plural(group.count, 'execution')}")
        found.append(("warn", f"{what}{share}.{cause}{read}"))
    redrivable = sum(g.redrivable for g in report.groups)
    if redrivable:
        newest = next((e for e in report.failed if can_redrive(e, now, express=report.machine.is_express)), None)
        command = _REDRIVE_COMMAND.format(arn=newest.arn) if newest is not None else ""
        found.append(("info", f"{_plural(redrivable, 'failed execution')} can still be redriven: once the cause is "
                              "fixed, each starts again from the state that failed, without repeating the steps that "
                              f"succeeded. For the newest: {command}"))
    if report.truncated:
        found.append(("info", f"Only the newest {_plural(report.read, 'failed execution')} were read. Pass limit= "
                              "to read more."))
    return found
