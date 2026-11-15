"""
The explorer's Graph tab: the state machine drawn from its definition, coloured by the execution open in the
Executions tab or by where executions stop, with a line per state to pick one (the states inside a Map or Parallel
under it), and what's known about the state picked beside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note, _Table, _Title
from ..._kit.fmt import _plural
from ..._kit.text import _esc
from ..._kit.window import _class_if

from ..models import ExecutionDetail, FailureReport, StateDef, StateMachine, StepRun
from ..analysis.definition import catch_label, resource_label, retry_label
from ..analysis.layout import (
    FlowLayout, FlowMarks, failure_marks, flow_layout, inner_failure_marks, inner_path_marks, inner_states, owner_of,
    path_marks,
)
from ..analysis.story import ExecutionStory
from ..analyzer import _run_lines
from ..blocks import _FlowGraph, _payload_block, _Story
from .widgets import _Line

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_MODES = (("This execution", "execution"), ("Where failures stop", "failures"), ("Just the flow", "plain"))

_TRY_OUTCOMES = {"ok": "ok", "failed": "failed", "timed out": "timed out", "running": "never finished"}

_DEEPEST_INDENT = 3

_RUNS_INSIDE = {"Map": "it runs once for each item", "Parallel": "it runs in one of its branches"}


def _inside_text(state: StateDef, machine: StateMachine) -> str:
    """
    Where an inner state sits, for its definition: 'ProcessItems, a Map: it runs once for each item'.
    """
    parent_name = state.path.split(" > ")[-2]
    parent = machine.state(parent_name)
    kind = parent.type if parent is not None else ""
    return f"{parent_name}, a {kind}: {_RUNS_INSIDE[kind]}" if kind in _RUNS_INSIDE else parent_name


def _definition_rows(state: StateDef, machine: StateMachine) -> list[list[str]]:
    does = resource_label(state)
    rows = [["Type", state.type], ["Does", "-" if does == state.type else does]]
    if state.depth:
        rows.append(["Inside", _inside_text(state, machine)])
    caught = {c.get("Next") for c in state.catches}
    then = [t for t in state.next if t not in caught]
    rows.append(["Then", ", ".join(then) or ("end" if state.end else "-")])
    rows.append(["Retries", retry_label(state) or "none"])
    rows.append(["On error", catch_label(state) or "the execution fails"])
    if state.timeout is not None:
        rows.append(["Times out after", f"{state.timeout:,}s"])
    return rows


def _try_rows(step: StepRun, detail: ExecutionDetail) -> list[list[str]]:
    start = detail.execution.start or step.entered
    rows = []
    for i, attempt in enumerate(step.attempts, start=1):
        took = f"{attempt.seconds:.2f}s" if attempt.seconds is not None else "-"
        rid = attempt.request_id[:8] if attempt.request_id else "-"
        rows.append([str(i), f"+{(attempt.scheduled - start).total_seconds():.2f}s", took,
                     _TRY_OUTCOMES.get(attempt.outcome, attempt.outcome), attempt.error or "-", rid])
    return rows


def _runs_note(runs: list[StepRun], shown: StepRun, execution: str) -> _Note:
    """
    How often a state ran in one execution (a Map's inner state once per item, a state in a loop once per lap), how
    many of those runs failed, and which one is shown.
    """
    failed = sum(1 for s in runs if s.status == "failed")
    which = "the last failed one" if shown.status == "failed" else "the last one"
    counted = f", {failed:,} of them failed" if failed else ""
    return _Note(f"It ran {_plural(len(runs), 'time')} in {execution}{counted}: {which} is shown.",
                 "warn" if failed else "info")


def _lambda_blocks(step: StepRun, story: ExecutionStory | None) -> list[Any]:
    """
    The lines of the Lambda run behind the run of a state shown (one item's, for a state inside a Map), once the
    execution's log is read, or why they aren't there.
    """
    if not step.function:
        return []
    if story is None:
        return [_Note("Its Lambda run's lines come once the execution's log is read.")]
    found = next((r for r in story.runs if r.step is step), None)
    if found is not None and found.run is not None:
        return [_Story(_run_lines(found), title=f"What {step.function} logged")]
    why = found.note if found is not None and found.note else "the log reads the failed steps and the first ones only"
    return [_Note(f"This run's Lambda lines aren't in the execution's log ({why}).")]


def _execution_blocks(name: str, detail: ExecutionDetail, story: ExecutionStory | None) -> list[Any]:
    """
    What the execution open did in a state: each try (of its last failed run, when it ran more than once and one
    failed), its Lambda run's lines when they're read, and what the state was given and gave back.
    """
    runs = [s for s in detail.steps if s.name == name]
    if not runs:
        return [_Note(f"{detail.execution.name} never reached {name}.")]
    failed_runs = [s for s in runs if s.status == "failed"]
    step = failed_runs[-1] if failed_runs else runs[-1]
    blocks: list[Any] = []
    if len(runs) > 1:
        blocks.append(_runs_note(runs, step, detail.execution.name))
    if step.attempts:
        blocks.append(_Table(["Try", "Scheduled", "Took", "Ended", "Error", "Request"], _try_rows(step, detail),
                             title=f"Its tries in {detail.execution.name}", max_rows=0, sortable=False))
    blocks += _lambda_blocks(step, story)
    if step.input:
        blocks.append(_payload_block(step.input, "What it was given", name="state_input"))
    if step.output:
        blocks.append(_payload_block(step.output, "What it gave back", name="state_output"))
    return blocks


def _failure_blocks(name: str, inner: set[str], report: FailureReport) -> list[Any]:
    """
    The failure groups that stopped at a state (or at a state inside it).
    """
    groups = [g for g in report.groups if g.state == name or g.state in inner]
    if not groups:
        return [_Note(f"No execution stopped at {name} in the window read.")]
    rows = [[g.state, g.error, g.cause or "-", f"{g.count:,}", g.newest.name if g.newest else "-"] for g in groups]
    return [_Table(["Stopped at", "Error", "Cause", "Executions", "Newest"], rows,
                   title="The executions that stopped here", max_rows=0, sortable=False)]


def _state_face(name: str, depth: int, mark: str, note: str) -> str:
    """
    A state's line: its mark, its name (indented under the Map or Parallel it sits inside) and a short note.
    """
    nested = f" d{min(depth, _DEEPEST_INDENT)}" if depth else ""
    arrow = "↳ " if depth else ""
    return (f'<div class="er{nested}"><span class="ei m-{_esc(mark)}"></span><div class="em">'
            f'<div class="en">{arrow}{_esc(name)}</div><div class="ef">{_esc(note)}</div></div></div>')


class _GraphTab:
    """
    The Graph tab, a part of StepFunctionsExplorer: what it keeps is `layout` (the state machine drawn), `graph_mode`
    ('execution', 'failures' or 'plain') and `picked_state`.
    """

    def _graph_page(self: StepFunctionsExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.graph_layout: FlowLayout | None = None
        self.graph_mode = "execution"
        self.picked_state = ""
        self._state_lines: list[_Line] = []
        self.mode_pick = w.ToggleButtons(options=list(_MODES), value="execution", style={"button_width": "auto"},
                                         tooltips=["The path the execution open in the Executions tab took",
                                                   "Where executions stopped in the time range picked",
                                                   "The definition, uncoloured"])
        self.mode_pick.observe(self._safely(self._mode_changed), names="value")
        self.graph_view = w.HTML(layout=layout(width="100%"))
        graph_side = w.VBox([self.mode_pick, self.graph_view], layout=layout(flex="1 1 auto", min_width="0"))
        graph_side.add_class("sfx-graph")
        self.state_box = w.VBox(layout=layout(width="100%"))
        self.state_box.add_class("sfx-rows")
        self.state_view = w.HTML(layout=layout(width="100%"))
        panel = w.VBox([self.state_box, self.state_view])
        panel.add_class("sfx-left")
        panel.add_class("sfx-states")
        page = w.HBox([graph_side, panel])
        page.add_class("sfx-split")
        return page

    def _graph_machine(self: StepFunctionsExplorer) -> None:
        """
        A new state machine: its graph laid out once, the first state picked.
        """
        machine = self.machine
        if machine is None:
            return
        self.graph_layout = flow_layout(machine)
        self.picked_state = machine.start_at
        self._draw_state_lines()
        self._draw_graph()

    def _mode_changed(self: StepFunctionsExplorer, change: dict[str, Any]) -> None:
        self.graph_mode = change["new"]
        if self.graph_mode == "failures" and self.report is None and self.machine and self.machine.has_executions:
            self._load_failures()
        self._draw_graph()
        self._focus_moved()

    def _graph_marks(self: StepFunctionsExplorer) -> tuple[FlowMarks, str]:
        """
        What the graph is coloured by in the mode picked, and what to say when it can't be.
        """
        layout = self.graph_layout
        if layout is None or self.graph_mode == "plain":
            return FlowMarks(selected=self.picked_state), ""
        if self.graph_mode == "execution":
            if self.detail is None:
                return FlowMarks(selected=self.picked_state), ("Open an execution in the Executions tab to see the "
                                                               "path it took.")
            marks = path_marks(layout, self.detail)
        elif self.report is None:
            unreadable = self.machine is not None and not self.machine.has_executions
            say = ("An Express workflow with logging off leaves no record of its failures." if unreadable
                   else "Reading where executions stop…")
            return FlowMarks(selected=self.picked_state), say
        else:
            marks = failure_marks(layout, self.report)
        marks.selected = self._picked_box()
        return marks, ""

    def _picked_box(self: StepFunctionsExplorer) -> str:
        """
        The box the graph rings for the state picked: its own, or the Map's or Parallel's it sits inside.
        """
        return owner_of(self.machine, self.picked_state) if self.machine else self.picked_state

    def _inner_marks(self: StepFunctionsExplorer) -> FlowMarks:
        """
        How the states inside a Map or Parallel went, for their lines: in the execution open, or in the failures.
        """
        machine = self.machine
        if machine is None:
            return FlowMarks()
        if self.graph_mode == "execution" and self.detail is not None:
            return inner_path_marks(machine, self.detail)
        if self.graph_mode == "failures" and self.report is not None:
            return inner_failure_marks(machine, self.report)
        return FlowMarks()

    def _draw_graph(self: StepFunctionsExplorer) -> None:
        layout = self.graph_layout
        if layout is None:
            return
        marks, note = self._graph_marks()
        blocks: list[Any] = [_Note(note)] if note else []
        blocks.append(_FlowGraph(layout, marks))
        self._draw("graph", self.graph_view, blocks)
        inner = self._inner_marks()
        for line in self._state_lines:
            _class_if(line.box, "sfx-on", line.key == self.picked_state)
            mark = marks.states.get(line.key) or inner.states.get(line.key, "")
            note_text = marks.notes.get(line.key) or inner.notes.get(line.key, "")
            self._set(line.face, _state_face(line.key, self._state_depth(line.key), mark, note_text))
        self._draw_state()

    def _state_depth(self: StepFunctionsExplorer, name: str) -> int:
        state = self.machine.state(name) if self.machine else None
        return state.depth if state is not None else 0

    def _state_names(self: StepFunctionsExplorer) -> list[str]:
        """
        The states to pick, as the graph reads top down: each box, then the states inside it (a Map's or
        Parallel's), which the graph only lists in its box.
        """
        layout, machine = self.graph_layout, self.machine
        if layout is None or machine is None:
            return []
        names = []
        for node in sorted(layout.nodes, key=lambda n: (n.layer, n.column)):
            names.append(node.name)
            names += [state.name for state in inner_states(machine, node.name)]
        return names

    def _draw_state_lines(self: StepFunctionsExplorer) -> None:
        names = self._state_names()
        while len(self._state_lines) < len(names):
            self._state_lines.append(_Line(self, self._clicked_state))
        for line, name in zip(self._state_lines, names):
            line.key = name
            line.button.tooltip = f"{name}: what it does, and what happened there"
        self.state_box.children = [line.box for line in self._state_lines[:len(names)]]

    def _clicked_state(self: StepFunctionsExplorer, line: _Line) -> None:
        if line.key:
            self.picked_state = line.key
            self._draw_graph()
            self._focus_moved()

    def _draw_state(self: StepFunctionsExplorer) -> None:
        """
        The state picked: what its definition says, then what the execution open did there, or which failures
        stopped there.
        """
        machine, name = self.machine, self.picked_state
        state = machine.state(name) if machine else None
        if state is None:
            self._set(self.state_view, "")
            return
        blocks: list[Any] = [_Title(name, state.comment), _Table(["", ""], _definition_rows(state, machine),
                                                                 title="What the definition says", max_rows=0,
                                                                 sortable=False)]
        if self.graph_mode == "execution" and self.detail is not None:
            blocks += _execution_blocks(name, self.detail, self.story)
        elif self.graph_mode == "failures" and self.report is not None:
            inner = {s.name for s in inner_states(machine, name)}
            blocks += _failure_blocks(name, inner, self.report)
        self._draw("state", self.state_view, blocks)
