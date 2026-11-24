"""
Step Functions' reports as data: the tables behind overview(), state_machine(), executions(), execution(),
failures() and graph(timings=...), each column said in plain words with how to read it, and the charts the workbook
draws from them. An execution's tables join each step to the Lambda run behind it (the run's REPORT line: run time,
billed time, memory, cold start), so the time a step took can be set against the time its function ran. Pure: every
function takes what a command already read and returns a ReportData (_kit/datasets.py).
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any

from ..._kit.datasets import DataChart, DataColumn, DataTable, Recipe, ReportData
from ..helpers import execution_ref
from ..models import (
    Execution,
    ExecutionDetail,
    ExecutionPage,
    FailureReport,
    MachineNumbers,
    Overview,
    StateMachine,
    StepRun,
    TimingReport,
)
from .cost import MS_PER_SECOND, monthly_cost, transitions_per_execution
from .story import ExecutionStory

_STATUS_TONES = (("FAILED", "bad"), ("TIMED_OUT", "bad"), ("ABORTED", "warn"), ("RUNNING", "ok"))
_STEP_TONES = (("failed", "bad"), ("running", "warn"))
_OUTCOME_TONES = (("failed", "bad"), ("timed out", "bad"), ("running", "warn"))
_MB_PER_GB = 1024


def _share_of(part: float | None, whole: float | None) -> float | None:
    """
    part / whole as a share from 0 to 1, or None without a whole.
    """
    if part is None or not whole:
        return None
    return part / whole


def _stem(command: str, name: str) -> str:
    return f"stepfunctions-{command}-{name}"


def _execution_cost(machine: StateMachine, seconds: float | None, prices: dict[str, float]) -> float | None:
    """
    An estimate of what one execution costs: a request and its run time at the smallest memory for Express, its
    state transitions (one per top-level state) for Standard, before the free tier.
    """
    if machine.is_express:
        run = (seconds or 0.0) * prices["express_memory_gb"] * prices["express_gb_second"]
        return prices["express_request"] + run
    return transitions_per_execution(machine) * prices["transition"]


def _machine_row(machine: StateMachine, numbers: MachineNumbers | None, prices: dict[str, float]) -> dict[str, Any]:
    started = numbers.started if numbers else None
    unsuccessful = (numbers.failed + numbers.timed_out + numbers.aborted) if numbers else None
    return {
        "state_machine": machine.name, "type": machine.type, "status": machine.status,
        "started": started, "succeeded": numbers.succeeded if numbers else None,
        "failed": numbers.failed if numbers else None, "timed_out": numbers.timed_out if numbers else None,
        "aborted": numbers.aborted if numbers else None, "throttled": numbers.throttled if numbers else None,
        "unsuccessful_share": _share_of(unsuccessful, started),
        "avg_seconds": numbers.avg_ms / MS_PER_SECOND if numbers and numbers.avg_ms is not None else None,
        "est_monthly_usd": monthly_cost(machine, numbers, prices), "states": len(machine.states),
        "functions": ", ".join(machine.functions), "logging": machine.log_level, "tracing": machine.tracing,
        "created": machine.created, "arn": machine.arn,
    }


_MACHINE_COLUMNS = [
    DataColumn("state_machine", "the state machine's name", "id"),
    DataColumn("type", "STANDARD (kept history, billed per state transition) or EXPRESS (billed per run and second)"),
    DataColumn("status", "ACTIVE, or DELETING"),
    DataColumn("started", "executions started in the window (CloudWatch)", "count", bar=True),
    DataColumn("succeeded", "executions that succeeded", "count"),
    DataColumn("failed", "executions that failed", "count"),
    DataColumn("timed_out", "executions stopped by their timeout", "count"),
    DataColumn("aborted", "executions someone stopped", "count"),
    DataColumn("throttled", "starts refused because too many ran at once", "count"),
    DataColumn("unsuccessful_share", "(failed + timed_out + aborted) / started", "share",
               read="Over 0.05 (5%) is worth failures() on it."),
    DataColumn("avg_seconds", "the average execution's run time", "seconds"),
    DataColumn("est_monthly_usd", "an estimate of its monthly bill at this pace, at list prices", "usd", bar=True,
               read="Standard counts a transition per top-level state; Map and Parallel states make it more."),
    DataColumn("states", "states in its definition", "count"),
    DataColumn("functions", "the Lambda functions its states call"),
    DataColumn("logging", "its execution logging level: OFF, ERROR, FATAL or ALL"),
    DataColumn("tracing", "whether X-Ray tracing is on", "flag"),
    DataColumn("created", "when it was created", "time"),
    DataColumn("arn", "its ARN", "id"),
]


def _day_rows(machine: StateMachine, numbers: MachineNumbers | None) -> list[dict[str, Any]]:
    """
    A row per UTC day of a state machine's CloudWatch numbers: started, failed and not succeeded.
    """
    if numbers is None or numbers.first_day is None:
        return []
    rows = []
    for offset, started in enumerate(numbers.daily_started):
        day: date = numbers.first_day + timedelta(days=offset)
        failed = numbers.daily_failed[offset] if offset < len(numbers.daily_failed) else 0
        unsuccessful = numbers.daily_unsuccessful[offset] if offset < len(numbers.daily_unsuccessful) else 0
        rows.append({"state_machine": machine.name, "day": day.isoformat(), "started": started, "failed": failed,
                     "unsuccessful": unsuccessful, "unsuccessful_share": _share_of(unsuccessful, started)})
    return rows


_DAY_COLUMNS = [
    DataColumn("state_machine", "the state machine", "id"),
    DataColumn("day", "the UTC day (CloudWatch counts whole UTC days)"),
    DataColumn("started", "executions started that day", "count", bar=True),
    DataColumn("failed", "executions that failed that day", "count"),
    DataColumn("unsuccessful", "executions that failed, timed out or were aborted", "count"),
    DataColumn("unsuccessful_share", "unsuccessful / started", "share"),
]


def overview_data(overview: Overview, prices: dict[str, float]) -> ReportData:
    """
    overview()'s report as data: a row per state machine, the costliest first, and a row per machine per day.
    """
    rows = [_machine_row(machine, overview.numbers.get(machine.arn), prices) for machine in overview.machines]
    rows.sort(key=lambda row: -(row["est_monthly_usd"] or 0.0))
    days = [row for machine in overview.machines for row in _day_rows(machine, overview.numbers.get(machine.arn))]
    machines = DataTable(
        "machines", f"Every state machine in {overview.region}", "a state machine", _MACHINE_COLUMNS, rows,
        notes=[f"Counts cover the last {overview.days} days, from CloudWatch.",
               "The costliest first. The account's 4,000 free transitions a month aren't taken off."],
        recipes=[Recipe("df.sort_values('unsuccessful_share', ascending=False).head()", "the ones failing most"),
                 Recipe("df.groupby('type').est_monthly_usd.sum()", "the bill, Standard against Express")])
    by_day = DataTable(
        "days", "Executions a day, per state machine", "a state machine on a day", _DAY_COLUMNS, days,
        recipes=[Recipe("df.pivot(index='day', columns='state_machine', values='started')",
                        "a column per state machine, ready to plot")])
    charts = [
        DataChart("Executions and failures by state machine", "machines", "state_machine", ("started", "failed"),
                  "bar", f"Over the last {overview.days} days, in the order of the cost chart.", "executions"),
        DataChart("Estimated monthly cost by state machine", "machines", "state_machine", ("est_monthly_usd",),
                  "bar", "At this pace, at list prices.", "USD a month"),
    ]
    return ReportData(f"Step Functions in {overview.region}", [machines, by_day], charts,
                      stem=_stem("overview", overview.region))


_STATE_COLUMNS = [
    DataColumn("state", "the state's name", "id"),
    DataColumn("type", "Task, Choice, Map, Parallel, Wait, Pass, Succeed or Fail"),
    DataColumn("path", "where it sits: the Map or Parallel it's inside"),
    DataColumn("function", "the Lambda function it calls"),
    DataColumn("resource", "what a Task calls: a Lambda ARN or an AWS service action"),
    DataColumn("retries", "Retry rules it has", "count",
               read="A Lambda task with none fails the execution on its first throttle or timeout."),
    DataColumn("catches", "Catch rules it has: where errors go instead of failing", "count"),
    DataColumn("timeout_s", "its TimeoutSeconds, when set", "seconds"),
    DataColumn("next", "the states it can go to next"),
    DataColumn("end", "whether the execution can end here", "flag"),
]


def _state_rows(machine: StateMachine) -> list[dict[str, Any]]:
    return [{"state": s.name, "type": s.type, "path": s.path, "function": s.function, "resource": s.resource,
             "retries": len(s.retries), "catches": len(s.catches), "timeout_s": s.timeout,
             "next": ", ".join(s.next), "end": s.end} for s in machine.states]


def machine_data(machine: StateMachine, numbers: MachineNumbers | None) -> ReportData:
    """
    state_machine()'s report as data: every state of its definition, and its executions day by day.
    """
    states = DataTable("states", f"The states of {machine.name}", "a state of its definition", _STATE_COLUMNS,
                       _state_rows(machine),
                       recipes=[Recipe("df[(df.function != '') & (df.retries == 0)].state",
                                       "Lambda tasks with no retry")])
    days = DataTable("days", "Executions a day", "a UTC day", _DAY_COLUMNS, _day_rows(machine, numbers))
    charts = [DataChart("Executions a day", "days", "day", ("started", "unsuccessful"), "column",
                        "Started, and the ones that didn't succeed.", "executions")]
    tables = [states] + ([days] if days.rows else [])
    return ReportData(machine.name, tables, charts if days.rows else [], stem=_stem("machine", machine.name))


_EXECUTION_COLUMNS = [
    DataColumn("execution", "the execution's name", "id"),
    DataColumn("status", "SUCCEEDED, FAILED, TIMED_OUT, ABORTED or RUNNING", tones=_STATUS_TONES),
    DataColumn("started", "when it started", "time"),
    DataColumn("stopped", "when it ended (empty while running)", "time"),
    DataColumn("seconds", "how long it ran", "seconds", bar=True),
    DataColumn("est_cost_usd", "an estimate of what it cost, at list prices", "usd",
               read="Standard: a transition per top-level state; Express: a request and its run time."),
    DataColumn("redrive_count", "times it was redriven (restarted from where it failed)", "count"),
    DataColumn("error", "the error it failed with"),
    DataColumn("cause", "the cause it failed with, as Step Functions wrote it"),
    DataColumn("ref", "what execution('...') and compare('...') take to open it", "id"),
    DataColumn("arn", "its ARN", "id"),
]


def _execution_row(execution: Execution, machine: StateMachine, prices: dict[str, float]) -> dict[str, Any]:
    return {
        "execution": execution.name, "status": execution.status, "started": execution.start,
        "stopped": execution.stop, "seconds": execution.seconds,
        "est_cost_usd": _execution_cost(machine, execution.seconds, prices),
        "redrive_count": execution.redrive_count, "error": execution.error, "cause": execution.cause,
        "ref": execution_ref(machine.name, execution.name, execution.arn), "arn": execution.arn,
    }


def _execution_days(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    The executions summed up by the day they started: how many, how many didn't succeed, the median run, the cost.
    """
    days: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if isinstance(row["started"], datetime):
            days.setdefault(row["started"].date().isoformat(), []).append(row)
    summary = []
    for day, runs in sorted(days.items()):
        seconds = [run["seconds"] for run in runs if run["seconds"] is not None]
        unsuccessful = sum(1 for run in runs if run["status"] in ("FAILED", "TIMED_OUT", "ABORTED"))
        summary.append({"day": day, "executions": len(runs), "unsuccessful": unsuccessful,
                        "median_seconds": statistics.median(seconds) if seconds else None,
                        "est_cost_usd": sum(run["est_cost_usd"] or 0.0 for run in runs)})
    return summary


_EXECUTION_DAY_COLUMNS = [
    DataColumn("day", "the day they started (UTC)"),
    DataColumn("executions", "executions listed that started that day", "count", bar=True),
    DataColumn("unsuccessful", "the ones that failed, timed out or were aborted", "count"),
    DataColumn("median_seconds", "the typical run", "seconds"),
    DataColumn("est_cost_usd", "what they cost, estimated", "usd"),
]


def executions_data(page: ExecutionPage, prices: dict[str, float]) -> ReportData:
    """
    executions()' report as data: a row per execution listed, newest first, and the same summed up by day.
    """
    machine = page.machine
    rows = [_execution_row(execution, machine, prices) for execution in page.executions]
    notes = ["Newest first. Only the executions listed: pass n= or since= to executions() for more."]
    if page.source == "logs":
        notes.append("An Express workflow keeps no history: these come from its log group.")
    executions = DataTable(
        "executions", f"Executions of {machine.name}", "an execution", _EXECUTION_COLUMNS, rows, notes=notes,
        recipes=[Recipe("df.groupby('status').seconds.describe()", "run times by how they ended"),
                 Recipe("df[df.status == 'FAILED'].groupby('error').size()", "what the failures were"),
                 Recipe("df.set_index('started').seconds.plot(style='.')", "run time over time"),
                 Recipe("ui.data('execution', df.ref[0])", "the newest one's steps, and its Lambda runs")])
    days = DataTable("days", "Executions by day", "a day with executions", _EXECUTION_DAY_COLUMNS,
                     _execution_days(rows))
    charts = [DataChart("Every execution's run time", "executions", "started", ("seconds",), "scatter",
                        "A dot per execution: dots far above the rest are runs worth execution('...').", "seconds"),
              DataChart("Executions a day", "days", "day", ("executions", "unsuccessful"), "column",
                        "How many ran, and how many didn't succeed.", "executions")]
    return ReportData(f"Executions of {machine.name}", [executions, days], charts,
                      stem=_stem("executions", machine.name))


_STEP_COLUMNS = [
    DataColumn("order", "the order it was entered in", "count"),
    DataColumn("step", "the state's name", "id"),
    DataColumn("type", "Task, Choice, Map, Parallel, Wait, Pass, ..."),
    DataColumn("status", "ok, failed, running (never left) or ended (a Succeed or Fail state)", tones=_STEP_TONES),
    DataColumn("entered", "when the execution entered it", "time"),
    DataColumn("exited", "when it left", "time"),
    DataColumn("seconds", "how long it took, every try and wait between them included", "seconds", bar=True),
    DataColumn("share_of_execution", "seconds / the execution's whole run", "share",
               read="The biggest shares are where making it faster pays."),
    DataColumn("tries", "how many times it was tried", "count"),
    DataColumn("retries", "tries after the first: each one is a state transition billed again", "count"),
    DataColumn("transitions", "state transitions it was billed for (Standard: 1 + its retries)", "count"),
    DataColumn("est_cost_usd", "what its transitions cost, at list prices (Standard only)", "usd"),
    DataColumn("function", "the Lambda function it called"),
    DataColumn("request_id", "the Lambda request ID of its last try: logs(function, request_id=...) opens it", "id"),
    DataColumn("error", "the error it failed with"),
    DataColumn("cause", "the cause, as Step Functions wrote it"),
    DataColumn("items", "a Map's item count", "count"),
    DataColumn("depth", "0 at the top, 1 inside a Map or Parallel, ...", "count"),
]


def _step_row(order: int, step: StepRun, total: float | None, *, machine: StateMachine | None,
              prices: dict[str, float]) -> dict[str, Any]:
    is_standard = machine is None or not machine.is_express
    transitions = 1 + step.retries if is_standard else None
    return {
        "order": order, "step": step.name, "type": step.type, "status": step.status, "entered": step.entered,
        "exited": step.exited, "seconds": step.seconds, "share_of_execution": _share_of(step.seconds, total),
        "tries": len(step.attempts) or None, "retries": step.retries, "transitions": transitions,
        "est_cost_usd": transitions * prices["transition"] if transitions else None,
        "function": step.function, "request_id": step.request_id, "error": step.error, "cause": step.cause,
        "items": step.items, "depth": step.depth,
    }


_TRY_COLUMNS = [
    DataColumn("step", "the state it was a try of", "id"),
    DataColumn("try", "which try: 1 is the first", "count"),
    DataColumn("scheduled", "when it was scheduled", "time"),
    DataColumn("ended", "when it ended", "time"),
    DataColumn("seconds", "how long the try took", "seconds", bar=True),
    DataColumn("outcome", "ok, failed, timed out or running", tones=_OUTCOME_TONES),
    DataColumn("error", "the error it failed with"),
    DataColumn("function", "the Lambda function it called"),
    DataColumn("request_id", "its Lambda request ID", "id"),
]


def _tries_of(detail: ExecutionDetail) -> list[dict[str, Any]]:
    rows = []
    for step in detail.steps:
        for number, attempt in enumerate(step.attempts, 1):
            rows.append({"step": step.name, "try": number, "scheduled": attempt.scheduled, "ended": attempt.ended,
                         "seconds": attempt.seconds, "outcome": attempt.outcome, "error": attempt.error,
                         "function": step.function, "request_id": attempt.request_id})
    return rows


_LAMBDA_COLUMNS = [
    DataColumn("step", "the state that called the function", "id"),
    DataColumn("function", "the Lambda function", "id"),
    DataColumn("request_id", "the run's request ID", "id"),
    DataColumn("step_seconds", "how long Step Functions says the step took (its last try)", "seconds"),
    DataColumn("lambda_ms", "how long the function's code ran, from its REPORT line", "ms", bar=True),
    DataColumn("outside_ms", "step time the function's code didn't account for: start-up, queueing, Step Functions",
               "ms", read="Large next to lambda_ms points at cold starts or throttling, not slow code."),
    DataColumn("billed_ms", "the run time Lambda billed", "ms"),
    DataColumn("memory_mb", "the memory the function has", "mb"),
    DataColumn("max_memory_mb", "the most memory the run used", "mb"),
    DataColumn("memory_share", "max_memory_mb / memory_mb", "share", read="Over 0.9 risks running out of memory."),
    DataColumn("cold_start", "whether a new container started for it", "flag"),
    DataColumn("init_ms", "start-up time of a cold start", "ms"),
    DataColumn("lambda_cost_usd", "what the run cost in Lambda: billed GB-seconds and the request, x86_64 list "
               "prices", "usd"),
    DataColumn("failed", "whether the run logged an error", "flag"),
    DataColumn("lines", "lines the run logged", "count"),
    DataColumn("note", "how the run was found, or why it wasn't"),
]


def _lambda_row(step: StepRun, run: Any, note: str, prices: dict[str, float]) -> dict[str, Any]:
    """
    One Lambda step beside the run behind it: Step Functions' time for the step, and the run's REPORT line (run
    time, billed time, memory, cold start) and its cost; the run's columns stay empty when it wasn't found.
    """
    last_try = step.attempts[-1] if step.attempts else None
    step_seconds = last_try.seconds if last_try else step.seconds
    report = getattr(run, "report", None)
    row: dict[str, Any] = {"step": step.name, "function": step.function, "request_id": step.request_id,
                           "step_seconds": step_seconds, "note": note}
    if run is not None:
        row.update({"failed": bool(getattr(run, "failures", [])), "lines": len(getattr(run, "events", []))})
    if report is None:
        return row
    outside = step_seconds * MS_PER_SECOND - report.duration if step_seconds is not None else None
    gb_seconds = report.billed / MS_PER_SECOND * report.memory / _MB_PER_GB
    row.update({
        "lambda_ms": report.duration, "outside_ms": max(outside, 0.0) if outside is not None else None,
        "billed_ms": report.billed, "memory_mb": report.memory, "max_memory_mb": report.max_memory,
        "memory_share": _share_of(report.max_memory, report.memory), "cold_start": report.init is not None,
        "init_ms": report.init,
        "lambda_cost_usd": gb_seconds * prices["lambda_gb_second"] + prices["lambda_request"],
    })
    return row


def _lambda_table(story: ExecutionStory | None, prices: dict[str, float]) -> DataTable:
    rows = [_lambda_row(item.step, item.run, item.note, prices) for item in (story.runs if story else [])]
    notes = ["Each Lambda step beside the run behind it, matched by the request ID Step Functions recorded.",
             "Lambda's cost is billed on top of Step Functions': lambda_cost_usd plus the steps' est_cost_usd is "
             "the execution's cost."]
    if story is not None and story.skipped:
        notes.append(f"{story.skipped} more Lambda steps weren't read: execution() reads the failed ones first.")
    if story is None:
        notes.append("execution(..., logs=False) reads no Lambda run, so this table is empty.")
    return DataTable(
        "lambda_runs", "Its Lambda runs, step by step", "a Lambda step and the run behind it", _LAMBDA_COLUMNS,
        rows, notes=notes,
        recipes=[Recipe("df[['step', 'step_seconds', 'lambda_ms', 'outside_ms']]",
                        "where each step's time went: the code, or around it"),
                 Recipe("df.lambda_cost_usd.sum()", "what the execution's Lambda runs cost")])


def execution_data(detail: ExecutionDetail, story: ExecutionStory | None, prices: dict[str, float]) -> ReportData:
    """
    execution()'s report as data: its steps with their time and transitions, every try, and each Lambda step
    joined to the run behind it.
    """
    execution = detail.execution
    total = execution.seconds
    steps = DataTable(
        "steps", f"The steps of {execution.name}", "a state the execution entered", _STEP_COLUMNS,
        [_step_row(order, step, total, machine=detail.machine, prices=prices)
         for order, step in enumerate(detail.steps, 1)],
        notes=[f"It {execution.status.lower().replace('_', ' ')} after {total or 0:,.1f} s."
               + (" Its history was cut at the event limit, so later steps may be missing." if detail.partial
                  else "")],
        recipes=[Recipe("df.nlargest(5, 'seconds')[['step', 'seconds', 'share_of_execution']]", "the slowest steps"),
                 Recipe("df.est_cost_usd.sum()", "what its state transitions cost")])
    tries = DataTable("tries", "Every try of its tasks", "a try of a task", _TRY_COLUMNS, _tries_of(detail),
                      recipes=[Recipe("df[df['try'] > 1]", "the retries, and what they retried")])
    charts = [DataChart("Where the time went", "steps", "step", ("seconds",), "bar",
                        "Each step's time, in the order they ran.", "seconds"),
              DataChart("Step time against the function's run time", "lambda_runs", "step",
                        ("lambda_ms", "outside_ms"), "bar",
                        "The code's run time, and the time around it (cold start, queueing, Step Functions).", "ms")]
    return ReportData(f"Execution {execution.name}", [steps, tries, _lambda_table(story, prices)], charts,
                      stem=_stem("execution", execution.name))


_GROUP_COLUMNS = [
    DataColumn("state", "the state the executions stopped at", "id"),
    DataColumn("error", "the error they failed with"),
    DataColumn("cause", "the cause, as Step Functions wrote it"),
    DataColumn("count", "executions that failed this way", "count", bar=True),
    DataColumn("share", "its part of the failures read", "share"),
    DataColumn("first_seen", "the oldest of them read", "time"),
    DataColumn("newest", "the newest: execution('...') opens it", "id"),
    DataColumn("function", "the Lambda function behind the failing state"),
    DataColumn("request_id", "the newest one's Lambda run: logs(function, request_id=...) opens it", "id"),
    DataColumn("redrivable", "executions that can still be redriven from where they failed", "count"),
]


def failures_data(report: FailureReport, prices: dict[str, float]) -> ReportData:
    """
    failures()' report as data: a row per way of failing, the most frequent first, and a row per failed execution.
    """
    machine = report.machine
    total = sum(group.count for group in report.groups)
    groups = [{"state": g.state, "error": g.error, "cause": g.cause, "count": g.count,
               "share": _share_of(g.count, total), "first_seen": g.first_seen,
               "newest": execution_ref(machine.name, g.newest.name, g.newest.arn) if g.newest else None,
               "function": g.function, "request_id": g.request_id, "redrivable": g.redrivable}
              for g in report.groups]
    by_cause = DataTable("causes", f"Why {machine.name} fails", "a state and error executions failed at",
                         _GROUP_COLUMNS, groups,
                         notes=["The most frequent first; share adds up to 1."],
                         recipes=[Recipe("df.groupby('state')['count'].sum()", "failures by state")])
    failed = DataTable("failed", "Every failed execution read", "an execution that didn't succeed",
                       _EXECUTION_COLUMNS, [_execution_row(e, machine, prices) for e in report.failed],
                       recipes=[Recipe("df.set_index('started').resample('D').size()", "failures a day")])
    charts = [DataChart("Failures by cause", "causes", "state", ("count",), "bar",
                        "Executions that stopped at each state: the longest bar is the fix that helps most.",
                        "executions")]
    return ReportData(f"Failures of {machine.name}", [by_cause, failed], charts, stem=_stem("failures", machine.name))


_TIMING_COLUMNS = [
    DataColumn("state", "the state", "id"),
    DataColumn("type", "its type"),
    DataColumn("function", "the Lambda function it calls"),
    DataColumn("runs", "times it ran across the executions read", "count"),
    DataColumn("executions", "executions it ran in", "count"),
    DataColumn("median_seconds", "its typical run", "seconds"),
    DataColumn("longest_seconds", "its longest run", "seconds"),
    DataColumn("total_seconds", "all its runs added up", "seconds", bar=True),
    DataColumn("share", "its part of all the executions' time", "share",
               read="The states with the biggest share are where the time goes."),
    DataColumn("tries_per_run", "tries per run that was tried: over 1 means it retries", "number"),
    DataColumn("retried_runs", "runs that needed more than one try", "count"),
    DataColumn("retry_error", "the error it retries on most"),
]


def timings_data(report: TimingReport) -> ReportData:
    """
    graph(timings=...)'s data: each state's time over the succeeded executions read.
    """
    rows = [{"state": s.name, "type": s.type, "function": s.function, "runs": s.runs, "executions": s.executions,
             "median_seconds": s.median, "longest_seconds": s.longest, "total_seconds": s.total, "share": s.share,
             "tries_per_run": s.tries_per_run, "retried_runs": s.retried_runs, "retry_error": s.retry_error}
            for s in report.states]
    machine = report.machine.name
    table = DataTable("timings", f"Where {machine}'s time goes", "a state", _TIMING_COLUMNS, rows,
                      notes=[f"Over the newest {len(report.executions):,} executions that succeeded."],
                      recipes=[Recipe("df.nlargest(3, 'share')[['state', 'share', 'median_seconds']]",
                                      "the states that take the time")])
    charts = [DataChart("Each state's part of the time", "timings", "state", ("share",), "bar", "",
                        "share of the time")]
    return ReportData(f"Timings of {machine}", [table], charts, stem=_stem("timings", machine))
