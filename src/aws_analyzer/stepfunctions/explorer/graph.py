"""
The explorer's Graph tab: the state machine drawn from its definition, coloured by the execution open in the
Executions tab or by where executions stop, with a line per state to pick one, and what's known about the state
picked beside it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note, _Table, _Text, _Title
from ..._kit.fmt import _plural
from ..._kit.text import _esc
from ..._kit.window import _class_if

from ..models import ExecutionDetail, FailureReport, StateDef, StepRun
from ..analysis.definition import catch_label, resource_label, retry_label
from ..analysis.layout import FlowLayout, FlowMarks, failure_marks, flow_layout, path_marks
from ..analysis.story import ExecutionStory
from ..blocks import _FlowGraph, _Story
from .widgets import _Line

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_MODES = (("This execution", "execution"), ("Where failures stop", "failures"), ("Just the flow", "plain"))

_JSON_CHARS = 3_000

_TRY_OUTCOMES = {"ok": "ok", "failed": "failed", "timed out": "timed out", "running": "never finished"}


def _json_text(text: str) -> str:
    try:
        shown = json.dumps(json.loads(text), indent=2, default=str)
    except (TypeError, ValueError):
        shown = text or ""
    return shown if len(shown) <= _JSON_CHARS else f"{shown[:_JSON_CHARS]}\n…"


def _definition_rows(state: StateDef) -> list[list[str]]:
    does = resource_label(state)
    rows = [["Type", state.type], ["Does", "-" if does == state.type else does]]
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


def _execution_blocks(name: str, detail: ExecutionDetail, story: ExecutionStory | None) -> list[Any]:
    """
    What the execution open did in a state: each try, its Lambda run's lines when they're read, and what the state
    was given and gave back.
    """
    runs = [s for s in detail.steps if s.name == name]
    if not runs:
        return [_Note(f"{detail.execution.name} never reached {name}.")]
    step = runs[-1]
    blocks: list[Any] = []
    if len(runs) > 1:
        failed = sum(1 for s in runs if s.status == "failed")
        blocks.append(_Note(f"It ran {_plural(len(runs), 'time')} in {detail.execution.name}"
                            + (f", {failed:,} of them failed." if failed else ".") + " The last one is shown."))
    if step.attempts:
        blocks.append(_Table(["Try", "Scheduled", "Took", "Ended", "Error", "Request"], _try_rows(step, detail),
                             title=f"Its tries in {detail.execution.name}", max_rows=0, sortable=False))
    lines = [line for line in story.lines if line.state == name and line.source != "step functions"] if story else []
    if lines:
        blocks.append(_Story(lines, title=f"What {step.function} logged"))
    elif step.function and story is None:
        blocks.append(_Note("Its Lambda run's lines come once the execution's log is read."))
    if step.input:
        blocks.append(_Text(_json_text(step.input), title="What it was given", collapsed=True))
    if step.output:
        blocks.append(_Text(_json_text(step.output), title="What it gave back", collapsed=True))
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
        if self.graph_mode == "failures" and self.report is None and self.machine and not self.machine.is_express:
            self._load_failures()
        self._draw_graph()

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
            say = ("Express workflows keep no history of their failures." if self.machine and self.machine.is_express
                   else "Reading where executions stop…")
            return FlowMarks(selected=self.picked_state), say
        else:
            marks = failure_marks(layout, self.report)
        marks.selected = self.picked_state
        return marks, ""

    def _draw_graph(self: StepFunctionsExplorer) -> None:
        layout = self.graph_layout
        if layout is None:
            return
        marks, note = self._graph_marks()
        blocks: list[Any] = [_Note(note)] if note else []
        blocks.append(_FlowGraph(layout, marks))
        self._draw("graph", self.graph_view, blocks)
        for line in self._state_lines:
            _class_if(line.box, "sfx-on", line.key == self.picked_state)
            mark = marks.states.get(line.key, "")
            note_text = marks.notes.get(line.key, "")
            self._set(line.face, f'<div class="er"><span class="ei m-{_esc(mark)}"></span><div class="em">'
                                 f'<div class="en">{_esc(line.key)}</div><div class="ef">{_esc(note_text or "")}'
                                 "</div></div></div>")
        self._draw_state()

    def _draw_state_lines(self: StepFunctionsExplorer) -> None:
        layout = self.graph_layout
        names = [n.name for n in sorted(layout.nodes, key=lambda n: (n.layer, n.column))] if layout else []
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
        blocks: list[Any] = [_Title(name, state.comment), _Table(["", ""], _definition_rows(state),
                                                                 title="What the definition says", max_rows=0,
                                                                 sortable=False)]
        if self.graph_mode == "execution" and self.detail is not None:
            blocks += _execution_blocks(name, self.detail, self.story)
        elif self.graph_mode == "failures" and self.report is not None:
            inner = {s.name for s in machine.states if s.depth and s.path.startswith(f"{state.path} > ")}
            blocks += _failure_blocks(name, inner, self.report)
        self._draw("state", self.state_view, blocks)
