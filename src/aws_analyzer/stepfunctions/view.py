"""
StepFunctionsView: the notebook UI, which renders what StepFunctionsAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.deps import _require
from .._kit.errors import _Hint
from .._kit.fmt import _count, _duration, _fmt_dt, _plural, _tz_label, _utcnow, human_age, human_money
from .._kit.render import _call
from .._kit.text import _clip
from .._kit.view import _BaseView, _friendly_errors

from .helpers import STATUS_TONES, STATUS_WORDS, execution_ref, short_cause
from .models import ExecutionDetail, ExecutionPage, FailureReport, MachineNumbers, Overview, StateMachine, StepRun
from .analysis import (
    catch_label, execution_findings, failure_findings, logging_findings, machine_findings, mermaid, monthly_cost,
    numbers_findings, overview_findings, resource_label, retry_label, transitions_per_execution,
)
from .analysis.cost import free_tier_note
from .analysis.findings import lambda_logs_call
from .analysis.express import logging_gaps
from .analysis.layout import FlowMarks, failure_marks, flow_layout, path_marks
from .analysis.story import ExecutionStory, waterfall
from .analyzer import StepFunctionsAnalyzer
from .blocks import _FlowGraph, _Story, _Waterfall
from .style import _render_html


_STEP_TONES = {"ok": "ok", "failed": "bad", "running": "warn", "ended": ""}

_STEP_WORDS = {"ok": "ok", "failed": "failed", "running": "never finished", "ended": "end"}

_LOG_LEVELS = {"OFF": "off", "ALL": "everything", "ERROR": "errors only", "FATAL": "fatal errors only"}

_INPUT_CHARS = 4_000


def _ms(ms: float | None) -> str:
    return "-" if ms is None else _duration(ms / 1000)


def _seconds(seconds: float | None) -> str:
    return "-" if seconds is None else _duration(seconds)


def _logging_label(machine: StateMachine) -> str:
    label = _LOG_LEVELS.get(machine.log_level, machine.log_level.lower())
    return f"{label} (with data)" if machine.log_level != "OFF" and machine.log_data else label


def _logging_cell(machine: StateMachine) -> Any:
    if machine.is_express and machine.log_level in ("OFF", "ERROR", "FATAL"):
        return _Tone(_logging_label(machine), "warn" if machine.log_level == "OFF" else "")
    return _logging_label(machine)


def _unsuccessful_cell(numbers: MachineNumbers | None) -> Any:
    if numbers is None:
        return "-"
    if not numbers.unsuccessful:
        return "0"
    return _Tone(f"{numbers.unsuccessful:,} ({numbers.failure_rate:.1%})", "bad" if numbers.failed else "warn")


def _pretty_json(text: str) -> str:
    """
    JSON text indented, clipped to _INPUT_CHARS; other text as it is.
    """
    try:
        shown = json.dumps(json.loads(text), indent=2, default=str)
    except (TypeError, ValueError):
        shown = text or ""
    if len(shown) > _INPUT_CHARS:
        return shown[:_INPUT_CHARS] + f"\n… ({len(shown) - _INPUT_CHARS:,} more characters)"
    return shown


def _state_label(name: str, depth: int) -> str:
    return f"{'    ' * depth}{'↳ ' if depth else ''}{name}"


def _offset(step: StepRun, detail: ExecutionDetail) -> str:
    start = detail.execution.start
    if start is None:
        return _fmt_dt(step.entered, seconds=True)
    return f"+{(step.entered - start).total_seconds():.2f}s"


def _step_result(step: StepRun) -> Any:
    word = _STEP_WORDS.get(step.status, step.status)
    if step.status == "failed" and step.error:
        word = f"failed: {step.error}"
    tone = _STEP_TONES.get(step.status, "")
    return _Tone(word, tone) if tone else word


def _step_does(step: StepRun, machine: StateMachine | None) -> str:
    if step.function:
        return f"λ {step.function}"
    state = machine.state(step.name) if machine is not None else None
    label = resource_label(state) if state is not None else step.resource or step.type
    return "-" if label == step.type else label


def _inner_rows(steps: list[StepRun]) -> list[list[Any]]:
    """
    The states inside a Map or Parallel, one row per name: how many times they ran, how many failed, the longest.
    """
    rows: list[list[Any]] = []
    names = list(dict.fromkeys((s.depth, s.name) for s in steps))
    for depth, name in names:
        runs = [s for s in steps if s.name == name and s.depth == depth]
        failed = sum(1 for s in runs if s.status == "failed")
        longest = max((s.seconds or 0 for s in runs), default=0)
        result = _Tone(f"{failed:,} of {len(runs):,} failed", "bad") if failed else f"{len(runs):,} ok"
        rows.append([_state_label(name, depth), runs[0].type, f"λ {runs[0].function}" if runs[0].function else
                     runs[0].resource or runs[0].type, f"×{len(runs):,}", f"up to {_seconds(longest)}",
                     str(sum(s.retries for s in runs)), result])
    return rows


def _step_rows(detail: ExecutionDetail) -> list[list[Any]]:
    """
    A row per state the execution passed through at the top level, with the states inside each Map or Parallel
    summed up under it.
    """
    rows: list[list[Any]] = []
    top = [s for s in detail.steps if s.depth == 0]
    for i, step in enumerate(top):
        rows.append([step.name, step.type, _step_does(step, detail.machine), _offset(step, detail),
                     _seconds(step.seconds), str(len(step.attempts) or "-"), _step_result(step)])
        following = top[i + 1].entered if i + 1 < len(top) else None
        inner = [s for s in detail.steps if s.depth > 0 and s.entered >= step.entered
                 and (following is None or s.entered < following)]
        if inner:
            rows += _inner_rows(inner)
    return rows


def _missing_run_note(detail: ExecutionDetail) -> list[Any]:
    """
    Why the Lambda run behind the failed step isn't in the log, with the call that looks for it.
    """
    step = detail.failed_step
    if step is None or not step.function or not detail.lambda_note or detail.lambda_run is not None:
        return []
    call = lambda_logs_call(step.function, step.request_id) if step.request_id else (
        f"LambdaView().{_call('errors', step.function)}")
    return [_Note(f"The Lambda run behind {step.name} couldn't be shown: {detail.lambda_note}. Look for it with "
                  f"{call}.")]


def _story_blocks(story: ExecutionStory | None) -> list[Any]:
    """
    The execution's log, Step Functions' lines and every Lambda run's merged by time, with what wasn't read.
    """
    if story is None:
        return []
    missing = [f"{r.step.function} ({r.note})" for r in story.runs if r.run is None and r.note]
    notes = []
    if missing:
        notes.append(f"Not in the log: {', '.join(missing)}.")
    if story.skipped:
        notes.append(f"{_plural(story.skipped, 'more Lambda step')} not read (the failed ones and the first "
                     f"{len(story.runs)} were).")
    return [_Story(story.lines, story.sources, title="What happened, in order: Step Functions and every Lambda run "
                                                     "it called", note=" ".join(notes))]


def _with_story_run(detail: ExecutionDetail, story: ExecutionStory) -> None:
    """
    Puts the failed step's run from the story on the detail, so the findings say where its lines are.
    """
    failed = detail.failed_step
    found = next((r for r in story.runs if r.step is failed), None)
    if found is not None:
        detail.lambda_run, detail.lambda_note = found.run, found.note


class StepFunctionsView(_BaseView):
    """
    Notebook UI for Step Functions. Every public method renders a report and returns None; the data is on
    self.core (a StepFunctionsAnalyzer).
    """

    _render_html = staticmethod(_render_html)
    _GROUPS = {
        "🔀 State machines": ("overview", "state_machine", "graph"),
        "🏃 Executions": ("executions", "execution", "failures"),
        "🖱️ Explore by clicking": ("explore",),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "every state machine: executions, failures, cost and warnings"),
        ("failures('state-machine')", "where and why its executions fail, with the Lambda run to read"),
        ("execution('state-machine/execution-name')", "one execution step by step, and the Lambda lines that broke it"),
    )
    _PROGRESS = ("Reading", "executions")

    def __init__(
        self,
        core: StepFunctionsAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: StepFunctionsAnalyzer = core or StepFunctionsAnalyzer()

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code == "StateMachineDoesNotExist":
            return f"{text} State machines are regional (this is {self.core.region}): overview() lists them."
        if code == "ExecutionDoesNotExist":
            return (f"{text} Executions are kept 90 days after they end, and Express executions aren't kept at all: "
                    "executions('<state machine>') lists the ones there are.")
        if code == "InvalidArn":
            return f"{text} Pass a name, or an ARN copied from the console or from overview()."
        if code in ("AccessDeniedException", "AccessDenied") or "not authorized" in message.lower():
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    @_friendly_errors
    def overview(self, *, days: int = 7) -> None:
        """
        Every state machine in the region: how many executions it ran and how many didn't succeed over the last
        7 days, its average run, its estimated monthly cost and its logging, with what needs attention first.
        """
        with self._progress("Reading state machines", unit="state machines"):
            overview = self.core.overview(days=days)
        if not overview.machines:
            self._show([_Title(f"Step Functions in {overview.region}"),
                        _Note(f"No state machines in {overview.region}. They're regional: pass "
                              "StepFunctionsAnalyzer(region='...') to look in another region.")])
            return
        self._show(self._overview_blocks(overview))

    def _overview_blocks(self, overview: Overview) -> list[Any]:
        prices, days = self.core.prices, overview.days
        numbers = overview.numbers
        costs = {m.arn: monthly_cost(m, numbers.get(m.arn), prices) for m in overview.machines}
        findings = overview_findings(overview, prices)
        attention = [m for m in overview.machines if _needs_attention(m, numbers.get(m.arn))]
        started = sum(n.started for n in numbers.values())
        unsuccessful = sum(n.unsuccessful for n in numbers.values())
        known_costs = [c for c in costs.values() if c is not None]
        cards: list[tuple[str, ...]] = [
            ("State machines", _count(len(overview.machines))),
            (f"Executions · {days}d", _count(started) if numbers else "?"),
            (f"Didn't succeed · {days}d", _count(unsuccessful) if numbers else "?",
             "warn" if unsuccessful else ""),
            ("Est. $ / month", human_money(sum(known_costs)) if known_costs else "-"),
            ("Need attention", _count(len(attention)), "warn" if attention else ""),
        ]
        blocks: list[Any] = [
            _Title(f"Step Functions in {overview.region}",
                   f"{_plural(len(overview.machines), 'state machine')} · the last {_plural(days, 'day')} "
                   f"(CloudWatch's daily numbers, in UTC days) · {self._price_basis()}"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="Every state machine ran without failures, and logging is set up."),
        ]
        blocks += self._overview_notes(overview)
        blocks.append(self._machines_table(overview, costs))
        blocks.append(_Next(self._overview_next(overview)))
        return blocks

    def _overview_notes(self, overview: Overview) -> list[Any]:
        notes: list[Any] = []
        if "metrics" in overview.errors:
            notes.append(_Note(f"CloudWatch's numbers couldn't be read ({overview.errors['metrics']}), so executions "
                               "and costs are unknown: that needs cloudwatch:GetMetricData.", "warn"))
        unread = [name for name in overview.errors if name != "metrics"]
        if unread:
            notes.append(_Note(f"{', '.join(unread)} couldn't be described "
                               f"({overview.errors[unread[0]]}): that needs states:DescribeStateMachine.", "warn"))
        if overview.metrics_read:
            notes.append(_Note(f"Read {overview.metrics_read:,} CloudWatch metrics (about "
                               f"{human_money(overview.metrics_read * self.core.prices['metric_read'])}). "
                               + free_tier_note()))
        return notes

    def _machines_table(self, overview: Overview, costs: dict[str, float | None]) -> _Table:
        def order(machine: StateMachine) -> tuple[int, int]:
            numbers = overview.numbers.get(machine.arn)
            return (-(numbers.unsuccessful if numbers else 0), -(numbers.started if numbers else 0))

        rows = []
        for machine in sorted(overview.machines, key=order):
            numbers = overview.numbers.get(machine.arn)
            rows.append([
                machine.name,
                machine.type.title(),
                _count(numbers.started) if numbers else "-",
                _unsuccessful_cell(numbers),
                _ms(numbers.avg_ms) if numbers else "-",
                human_money(costs.get(machine.arn)),
                _logging_cell(machine),
                human_age(machine.created),
            ])
        days = overview.days
        return _Table(["State machine", "Type", f"Executions · {days}d", f"Didn't succeed · {days}d", "Avg run",
                       "$ / month", "Logging", "Created"], rows)

    def _overview_next(self, overview: Overview) -> list[tuple[str, str]]:
        failing = [m for m in overview.machines if (n := overview.numbers.get(m.arn)) and n.unsuccessful
                   and m.has_executions]
        failing.sort(key=lambda m: -overview.numbers[m.arn].unsuccessful)
        steps = []
        if failing:
            steps.append((_call("failures", failing[0].name), "where and why its executions fail"))
        busiest = max(overview.machines, key=lambda m: overview.numbers[m.arn].started
                      if m.arn in overview.numbers else 0)
        steps.append((_call("state_machine", busiest.name), "its states, findings and cost"))
        if busiest.has_executions:
            steps.append((_call("executions", busiest.name), "its latest executions"))
        return steps[:3]

    @_friendly_errors
    def state_machine(self, name: str, *, days: int = 7) -> None:
        """
        One state machine: its type, executions, failures, average run and estimated cost over the last 7 days,
        what its definition risks (Lambda tasks with no retry, waits with no timeout), every state with what it
        calls and how it handles errors, and the definition itself.
        """
        with self._progress("Reading the state machine", unit="calls"):
            machine = self.core.state_machine(name)
            numbers = self.core.numbers([machine], days=days)[0].get(machine.arn)
        self._show(self._machine_blocks(machine, numbers))

    def _machine_blocks(self, machine: StateMachine, numbers: MachineNumbers | None) -> list[Any]:
        cost = monthly_cost(machine, numbers, self.core.prices)
        findings = machine_findings(machine, numbers)
        days = numbers.days if numbers else 7
        logging_tone = "warn" if machine.is_express and machine.log_level == "OFF" else ""
        cards = [
            ("Type", f"{machine.type.title()}"),
            (f"Executions · {days}d", _count(numbers.started) if numbers else "?"),
            (f"Didn't succeed · {days}d", _count(numbers.unsuccessful) if numbers else "?",
             "warn" if numbers and numbers.unsuccessful else ""),
            ("Avg run", _ms(numbers.avg_ms) if numbers else "-"),
            ("Est. $ / month", human_money(cost)),
            ("Logging", _logging_label(machine), logging_tone),
        ]
        sub = " · ".join(filter(None, [
            f"{machine.type.title()} workflow",
            f"created {_fmt_dt(machine.created)}" if machine.created else "",
            f"{_plural(len(machine.states), 'state')}",
            f"role {machine.role.rsplit('/', 1)[-1]}" if machine.role else "",
            "X-Ray tracing on" if machine.tracing else "",
        ]))
        blocks: list[Any] = [_Title(machine.name, sub), _Cards([_card(c) for c in cards]),
                             _Findings(findings, empty="Nothing to fix: it runs without failures, and every Lambda "
                                                       "task retries Lambda's own errors.")]
        if machine.description:
            blocks.append(_Note(machine.description))
        blocks.append(self._states_table(machine, title="Its states, in the order the definition lists them"))
        if machine.functions:
            blocks.append(_Note(f"It calls {_plural(len(machine.functions), 'Lambda function')}: "
                                f"{', '.join(machine.functions)}. LambdaView().functions() shows how they're doing."))
        blocks.append(_Note(self._cost_basis(machine, numbers)))
        blocks.append(_Text(_pretty_json(machine.definition), title="Definition (Amazon States Language)",
                            collapsed=True))
        blocks.append(_Next(self._machine_next(machine, numbers)))
        return blocks

    def _cost_basis(self, machine: StateMachine, numbers: MachineNumbers | None) -> str:
        if numbers is None:
            return "The cost is unknown: CloudWatch's numbers couldn't be read."
        if machine.is_express:
            return (f"Cost: an estimate from {numbers.started:,} executions in {_plural(numbers.days, 'day')}, each "
                    f"billed as a request and {_ms(numbers.avg_ms)} at 64 MB (the least Express bills), "
                    f"{self._price_basis()}.")
        steps = transitions_per_execution(machine)
        return (f"Cost: an estimate from {numbers.started:,} executions in {_plural(numbers.days, 'day')}, each "
                f"counted as {_plural(steps, 'state transition')} (one per top-level state; Map iterations add "
                f"more), {self._price_basis()}. {free_tier_note()}")

    def _machine_next(self, machine: StateMachine, numbers: MachineNumbers | None) -> list[tuple[str, str]]:
        if not machine.has_executions:
            return [(_call("graph", machine.name), "how it flows, as a diagram to paste")]
        steps = []
        if numbers and numbers.unsuccessful:
            steps.append((_call("failures", machine.name), "where and why its executions fail"))
        steps.append((_call("executions", machine.name), "its latest executions"))
        steps.append((_call("graph", machine.name), "how it flows, as a diagram to paste"))
        return steps

    def _states_table(self, machine: StateMachine, *, title: str) -> _Table:
        rows = []
        for state in machine.states:
            then = ", ".join(t for t in state.next if t not in {c.get("Next") for c in state.catches})
            does = resource_label(state)
            rows.append([_state_label(state.name, state.depth), state.type, "-" if does == state.type else does,
                         then or ("end" if state.end else "-"), retry_label(state) or "-",
                         catch_label(state) or "-"])
        return _Table(["State", "Type", "Does", "Then", "Retries", "On error"], rows, title=title, max_rows=0,
                      sortable=False)

    @_friendly_errors
    def graph(self, name: str, *, execution: str | None = None, failures: Any = None) -> None:
        """
        How a state machine flows, drawn as a graph: a box per state (the states inside a Map or Parallel listed
        in it), an arrow per transition, dashed where an error goes. execution='ord-7f3a' colours the path that
        execution took (green ran, red failed, dashed not reached, each with its time and tries); failures='7d'
        shades the states where executions stopped. Then every state with where it goes next, and the flow as
        Mermaid to paste into a Markdown cell.
        """
        machine = self.core.state_machine(name)
        layout = flow_layout(machine)
        marks = FlowMarks()
        if execution is not None:
            with self._progress("Reading the execution", unit="events"):
                detail = self.core.execution(execution, machine=machine.arn, logs=False)
            marks = path_marks(layout, detail)
        elif failures is not None:
            with self._progress("Reading failed executions", unit="executions") as tick:
                report = self.core.failures(machine.arn, since=failures, progress=tick)
            marks = failure_marks(layout, report)
        self._show(self._graph_blocks(machine, marks))

    def _graph_blocks(self, machine: StateMachine, marks: FlowMarks | None = None) -> list[Any]:
        top = sum(1 for s in machine.states if s.depth == 0)
        nested = len(machine.states) - top
        sub = f"starts at {machine.start_at} · {_plural(top, 'state')}" + (
            f", and {_plural(nested, 'state')} inside a Map or Parallel" if nested else "")
        steps = [(_call("state_machine", machine.name), "its numbers, findings and cost")]
        if machine.has_executions:
            steps.append((_call("graph", machine.name, failures="7d"), "where its executions stop, in the last 7 "
                                                                       "days"))
        return [
            _Title(f"How {machine.name} flows", sub),
            _FlowGraph(flow_layout(machine), marks or FlowMarks()),
            self._states_table(machine, title="Each state, what it does, and where it goes next"),
            _Text(mermaid(machine), title="As a Mermaid flowchart: paste it into a Markdown cell (```mermaid) or "
                                          "mermaid.live", code=True, collapsed=True),
            _Next(steps),
        ]

    @_friendly_errors
    def executions(self, name: str, *, status: str | None = None, since: Any = "7d", n: Any = 25) -> None:
        """
        A Standard state machine's latest executions (25 from the last 7 days), newest first: when each started,
        how it ended and how long it took. status='failed' (or 'running', 'timed_out', ...) keeps one outcome.
        """
        with self._progress("Listing executions", unit="executions"):
            page = self.core.executions(name, status=status, since=since, limit=n)
        self._show(self._executions_blocks(page, status=status, since=since))

    def _executions_blocks(self, page: ExecutionPage, *, status: str | None, since: Any) -> list[Any]:
        machine, executions = page.machine, page.executions
        counts = {s: sum(1 for e in executions if e.status == s) for s in STATUS_WORDS}
        unsuccessful = sum(1 for e in executions if e.failed)
        durations = [e.seconds for e in executions if e.seconds is not None]
        what = f"{STATUS_WORDS.get(status.upper().replace(' ', '_'), status)} executions" if status else "executions"
        window = f"since {_fmt_dt(page.since, zone=True)}" if page.since else "all there are"
        cards = [
            ("Shown", _count(len(executions))),
            ("Succeeded", _count(counts["SUCCEEDED"])),
            ("Didn't succeed", _count(unsuccessful), "bad" if unsuccessful else ""),
            ("Running", _count(counts["RUNNING"])),
            ("Median run", _seconds(statistics.median(durations)) if durations else "-"),
            ("Longest", _seconds(max(durations)) if durations else "-"),
        ]
        where = f", from its log group ({machine.log_groups[0].split(':log-group:')[-1]})" if page.source == "logs" else ""
        blocks: list[Any] = [_Title(f"Executions of {machine.name}", f"{what}, {window}, newest first{where}"),
                             _Cards([_card(c) for c in cards])]
        if page.note:
            blocks.append(_Note(page.note))
        if page.covered_from is not None:
            blocks.append(_Note(f"Its log group has more lines than one read holds: these go back to "
                                f"{_fmt_dt(page.covered_from, zone=True)}. Pass since= for a shorter window."))
        elif page.truncated:
            blocks.append(_Note(f"Stopped at {_plural(len(executions), 'execution')}: these are the newest. Pass "
                                "n= for more, or since= for a shorter window."))
        if not executions:
            blocks.append(_Note(f"No {what} {window}. Pass since='30d' to look further back (executions are kept "
                                "90 days after they end)."))
        else:
            rows = [[_fmt_dt(e.start, seconds=True), e.name, _Tone(STATUS_WORDS.get(e.status, e.status),
                                                                    STATUS_TONES.get(e.status, "")),
                     _seconds(e.seconds), str(e.redrive_count or "")] for e in executions]
            blocks.append(_Table([f"Started ({_tz_label()})", "Execution", "Status", "Took", "Redriven"], rows))
        steps = []
        newest_failed = next((e for e in executions if e.failed), None)
        if newest_failed is not None:
            steps.append((_call("execution", execution_ref(machine.name, newest_failed.name, newest_failed.arn)),
                          "the newest failure, step by step"))
            steps.append((_call("failures", machine.name), "every failure, grouped by cause"))
        elif executions:
            newest = executions[0]
            steps.append((_call("execution", execution_ref(machine.name, newest.name, newest.arn)),
                          "the newest, step by step"))
        steps.append((_call("graph", machine.name), "how it flows"))
        blocks.append(_Next(steps[:3]))
        return blocks

    @_friendly_errors
    def execution(self, ref: str, *, machine: str | None = None, logs: bool = True, max_events: Any = 5_000) -> None:
        """
        One execution step by step: each state on a time axis with its tries and back-off, what it called, how
        long it took, where it failed and why, and one log of what happened, Step Functions' own events and the log
        lines of every Lambda run it called, merged by time. Name it by its ARN, a console link, or
        'state-machine/execution-name'; logs=False skips reading the Lambda logs.
        """
        with self._progress("Reading the execution", unit="events"):
            detail = self.core.execution(ref, machine=machine, logs=False, max_events=max_events)
            story = self.core.execution_story(detail) if logs else None
        if story is not None:
            _with_story_run(detail, story)
        self._show(self._execution_blocks(detail, story))

    def _execution_blocks(self, detail: ExecutionDetail, story: ExecutionStory | None = None) -> list[Any]:
        execution, step = detail.execution, detail.failed_step
        status_word = STATUS_WORDS.get(execution.status, execution.status)
        retries = sum(s.retries for s in detail.steps)
        cards = [
            ("Status", status_word, STATUS_TONES.get(execution.status, "")),
            ("Ran", _seconds(execution.seconds) if execution.seconds is not None else
             f"{human_age(execution.start).removesuffix(' ago')} so far"),
            ("States entered", _count(len(detail.steps))),
            ("Retries", _count(retries), "warn" if retries else ""),
        ]
        if step is not None:
            cards.append(("Stopped at", step.name, "bad"))
        sub = " · ".join(filter(None, [
            f"{execution.machine}",
            f"started {_fmt_dt(execution.start, seconds=True, zone=True)}",
            f"redriven {execution.redrive_count}×" if execution.redrive_count else "",
            "read from its log group" if detail.source == "logs" else "",
            _plural(detail.events, "history event"),
        ]))
        blocks: list[Any] = [
            _Title(f"Execution {execution.name}", sub),
            _Cards([_card(c) for c in cards]),
            _Findings(execution_findings(detail, _utcnow())),
            _Waterfall(waterfall(detail), title="When each state ran"),
            _Table(["State", "Type", "Does", "Entered", "Took", "Tries", "Result"], _step_rows(detail),
                   title="The states it passed through, in order", max_rows=0, sortable=False),
        ]
        blocks += _story_blocks(story) + _missing_run_note(detail)
        if step is not None and step.cause and detail.lambda_run is None:
            blocks.append(_Text(_pretty_json(step.cause), title=f"The error {step.name} returned", collapsed=True))
        if step is not None and step.input and step.input != execution.input:
            blocks.append(_Text(_pretty_json(step.input), title=f"What {step.name} was given", collapsed=True))
        if execution.input:
            blocks.append(_Text(_pretty_json(execution.input), title="The execution's input", collapsed=True))
        blocks.append(_Next(self._execution_next(detail)))
        return blocks

    def _execution_next(self, detail: ExecutionDetail) -> list[tuple[str, str]]:
        step, machine_name = detail.failed_step, detail.execution.machine
        steps = []
        if step is not None and step.function:
            steps.append((f"LambdaView().{_call('errors', step.function)}", f"every error {step.function} logged"))
        if detail.execution.failed and (detail.machine is None or detail.machine.has_executions):
            steps.append((_call("failures", machine_name), "how often it fails like this"))
        steps.append((_call("executions", machine_name), "its other executions"))
        return steps[:3]

    @_friendly_errors
    def failures(self, name: str, *, since: Any = "7d", limit: Any = 50) -> None:
        """
        Why a Standard state machine's executions fail: the ones that failed, timed out or were aborted in the last
        7 days, grouped by the state they stopped at and the error, each with the Lambda run or execution to read,
        and how many can still be redriven.
        """
        with self._progress("Reading failed executions", unit="executions") as tick:
            report = self.core.failures(name, since=since, limit=limit, progress=tick)
        self._show(self._failure_blocks(report))

    def _failure_blocks(self, report: FailureReport) -> list[Any]:
        machine, numbers = report.machine, report.numbers
        window = f"since {_fmt_dt(report.since, zone=True)}"
        unsuccessful = len(report.failed)
        redrivable = sum(g.redrivable for g in report.groups)
        cards = [
            ("Didn't succeed", _count(numbers.unsuccessful if numbers else unsuccessful),
             "bad" if unsuccessful else "ok"),
            ("Failure rate", f"{numbers.failure_rate:.1%}" if numbers else "-",
             "warn" if numbers and numbers.unsuccessful else ""),
            ("Causes", _count(len(report.groups))),
            ("Can be redriven", _count(redrivable)),
            ("Histories read", _count(report.read)),
        ]
        sub = f"{_plural(unsuccessful, 'execution')} that failed, timed out or were aborted {window}"
        if numbers is not None:
            sub += f" (CloudWatch counts {numbers.started:,} executions in {_plural(numbers.days, 'day')})"
        if report.source == "logs":
            sub += ", read from its log group"
        gaps = logging_gaps(machine) if report.source == "logs" else ""
        blocks: list[Any] = [
            _Title(f"Why {machine.name} fails", sub),
            _Cards([_card(c) for c in cards]),
            _Findings(failure_findings(report, _utcnow()),
                      empty=f"No execution failed, timed out or was aborted {window}."),
        ]
        if gaps:
            blocks.insert(1, _Note(gaps))
        if report.groups:
            rows = []
            for group in report.groups:
                newest = group.newest
                read = (lambda_logs_call(group.function, group.request_id) if group.function and group.request_id
                        else _call("execution", execution_ref(machine.name, newest.name, newest.arn))
                        if newest is not None else "-")
                rows.append([group.state + (f" · λ {group.function}" if group.function else ""), group.error,
                             _clip(short_cause(group.cause), 120) or "-", f"{group.count:,}",
                             human_age(newest.start) if newest is not None else "-", read])
            blocks.append(_Table(["Stopped at", "Error", "Cause", "Executions", "Newest", "To read next"], rows,
                                 title="Failures by where they stopped and why, the most common first",
                                 code_cols=(5,)))
        blocks.append(_Next(self._failure_next(report)))
        return blocks

    def _failure_next(self, report: FailureReport) -> list[tuple[str, str]]:
        steps = []
        top = report.groups[0] if report.groups else None
        if top is not None and top.newest is not None:
            steps.append((_call("execution", execution_ref(report.machine.name, top.newest.name, top.newest.arn)),
                          "the newest failure of the most common kind, step by step"))
        if top is not None and top.function:
            steps.append((f"LambdaView().{_call('errors', top.function)}", f"every error {top.function} logged"))
        steps.append((_call("graph", report.machine.name), "how it flows, and where errors go"))
        return steps[:3]

    @_friendly_errors
    def explore(self, name: str | None = None, *, execution: str | None = None, tab: str | None = None,
                since: str = "7d", height: int | str | None = None) -> None:
        """
        The explorer window: every state machine and how it's doing, and for the one you click its executions to
        click through, each with its waterfall and one log of what happened (Step Functions' events and every Lambda
        run's lines, merged by time), its failures by cause and its definition. Needs Jupyter and ipywidgets;
        view.explorer is the window.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, overview() lists every "
                "state machine, failures('name') says where and why one fails, and execution('name/execution') "
                "shows one step by step."
            )
        _require("ipywidgets", "The explorer window")
        from .explorer.window import StepFunctionsExplorer

        self.explorer = StepFunctionsExplorer(name, execution=execution, tab=tab, since=since, view=self,
                                              height=height, mode="widgets")


def _needs_attention(machine: StateMachine, numbers: MachineNumbers | None) -> bool:
    """
    Whether a state machine has a warning of its own in the overview: failures, throttles or logging.
    """
    found = numbers_findings(machine, numbers) + logging_findings(machine)
    return any(level == "warn" for level, _ in found)


def _card(card: tuple[str, ...]) -> tuple[str, ...]:
    """
    A card without an empty tone: (label, value) or (label, value, tone).
    """
    return card if len(card) == 2 or card[2] else card[:2]

