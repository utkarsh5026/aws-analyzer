"""
The explorer's Failures tab: why the state machine's executions fail, as a list to click. On the left, every
failure at once and then each kind of failure (the state it stopped at and the error), the most common first; on the
right, the one picked: all of them is the report's summary, findings and executions a day, a kind is its numbers,
the error it returned, what to read next, the commands that redrive it and its executions, each a line that opens in
the Executions tab.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _plural
from ..._kit.text import _esc
from ..._kit.window import _class_if, _skeleton

from ..models import FailureGroup, FailureReport
from .frame import _all_failures_face, _execution_face, _failure_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


ALL_FAILURES = ""


def _group_key(group: FailureGroup) -> str:
    """
    What a kind of failure is called in the list and in picked_failure: its state and error.
    """
    return f"{group.state} · {group.error}"


class _FailuresTab:
    """
    The Failures tab, a part of StepFunctionsExplorer: what it keeps is `report` (the FailureReport read) and
    `picked_failure`, the line picked: ALL_FAILURES ('') or a kind's 'State · Error'.
    """

    def _failures_page(self: StepFunctionsExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.picked_failure = ALL_FAILURES
        self._failure_lines: list[_Line] = []
        self._failure_runs: list[_Line] = []
        self.failure_list = w.VBox(layout=layout(width="100%"))
        self.failure_list.add_class("sfx-rows")
        self.failures_left = w.VBox([self.failure_list])
        self.failures_left.add_class("sfx-left")
        self.failures_view = w.HTML(layout=layout(width="100%"))
        self.failure_runs_head = w.HTML(layout=layout(width="100%"))
        self.failure_runs_box = w.VBox(layout=layout(width="100%"))
        self.failures_right = self._failure_pane()
        self.failures_split = w.HBox([self.failures_left, self.failures_right])
        self.failures_split.add_class("sfx-split")
        return self.failures_split

    def _failure_pane(self: StepFunctionsExplorer) -> Any:
        """
        The right side of the Failures tab in a new box, which starts at the top (widgets can't be scrolled from
        Python).
        """
        pane = self._w.VBox([self.failures_view, self.failure_runs_head, self.failure_runs_box])
        pane.add_class("sfx-right")
        return pane

    def _clear_failures(self: StepFunctionsExplorer, text: str) -> None:
        """
        The tab before a report is read: a line saying what's coming, no list, no executions.
        """
        self.picked_failure = ALL_FAILURES
        self._set(self.failures_view, _skeleton(text))
        self._set(self.failure_runs_head, "")
        self.failure_list.children = self.failure_runs_box.children = []

    def _load_failures(self: StepFunctionsExplorer) -> None:
        machine = self.machine
        if machine is None or not machine.has_executions:
            return
        self._clear_failures(f"Reading why {machine.name}'s executions fail…")
        self._status(f"Reading {machine.name}'s failed executions…", busy=True, owner="failures")
        arn, since = machine.arn, self._since
        self._later("failures", lambda: self.core.failures(arn, since=since), self._got_failures,
                    self._failures_failed)

    def _failures_failed(self: StepFunctionsExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.failures_view, self._html([_Note(f"Couldn't read the failures: {text}", "warn")]))

    def _got_failures(self: StepFunctionsExplorer, report: FailureReport) -> None:
        self.report = report
        self._draw_graph()
        keys = {_group_key(group) for group in report.groups}
        picked = self.picked_failure if self.picked_failure in keys else ALL_FAILURES
        self._draw_failure_lines()
        self._show_failure(picked)
        _class_if(self.tab_buttons["failures"], "sfx-alarm", bool(report.groups))
        self._finish("failures", f"{_plural(len(report.failed), 'execution')} didn't succeed in the last "
                                 f"{self._since_label()}, {_plural(len(report.groups), 'kind')} of failure",
                     tabs=("failures",))

    def _draw_failure_lines(self: StepFunctionsExplorer) -> None:
        """
        The list on the left: every failure at once, then a line per kind of failure, the most common first.
        """
        report = self.report
        if report is None:
            return
        faces = [(ALL_FAILURES, _all_failures_face(report), "Every failure: the summary, findings and days")]
        for group in report.groups:
            tip = f"{_group_key(group)}: its error, what to read next and its executions"
            faces.append((_group_key(group), _failure_face(group, len(report.failed)), tip))
        while len(self._failure_lines) < len(faces):
            self._failure_lines.append(_Line(self, self._clicked_failure))
        for line, (key, face, tip) in zip(self._failure_lines, faces):
            line.key = key
            self._set(line.face, face)
            if line.button.tooltip != tip:
                line.button.tooltip = tip
        self.failure_list.children = [line.box for line in self._failure_lines[:len(faces)]]

    def _clicked_failure(self: StepFunctionsExplorer, line: _Line) -> None:
        self._show_failure(line.key)

    def _show_failure(self: StepFunctionsExplorer, key: str) -> None:
        """
        The line picked, on the right: every failure (the report's summary, findings and days), or one kind of
        failure with its executions to open.
        """
        report = self.report
        if report is None:
            return
        self.picked_failure = key
        for line in self._failure_lines:
            _class_if(line.box, "sfx-on", line.key == key)
        self.failures_right = self._failure_pane()
        self.failures_split.children = [self.failures_left, self.failures_right]
        group = next((g for g in report.groups if _group_key(g) == key), None)
        if group is None:
            self._draw("failures", self.failures_view, self.ui._failure_blocks(report, for_window=True))
            self._draw_failure_runs(None)
        else:
            self._draw("failures", self.failures_view, self.ui._failure_group_blocks(report, group))
            self._draw_failure_runs(group)
        self._focus_moved()

    def _draw_failure_runs(self: StepFunctionsExplorer, group: FailureGroup | None) -> None:
        """
        A kind of failure's executions under its report, newest first, each a line that opens it in the Executions
        tab (none for every failure at once).
        """
        executions = group.executions if group is not None else []
        while len(self._failure_runs) < len(executions):
            self._failure_runs.append(_Line(self, self._clicked_failure_run))
        for line, execution in zip(self._failure_runs, executions):
            line.key = execution.arn
            self._set(line.face, _execution_face(execution, []))
            tip = f"{execution.name}: open its waterfall and log in the Executions tab"
            if line.button.tooltip != tip:
                line.button.tooltip = tip
        head = (f'<div class="sfx-subh">{_esc(_plural(len(executions), "execution"))} that failed this way'
                "<span>click one for its waterfall and log in the Executions tab</span></div>") if executions else ""
        self._set(self.failure_runs_head, head)
        self.failure_runs_box.children = [line.box for line in self._failure_runs[:len(executions)]]

    def _clicked_failure_run(self: StepFunctionsExplorer, line: _Line) -> None:
        if line.key:
            self._open_from_failures(line.key)

    def _open_from_failures(self: StepFunctionsExplorer, arn: str) -> None:
        self._show_tab("executions")
        self._open_execution(arn)
