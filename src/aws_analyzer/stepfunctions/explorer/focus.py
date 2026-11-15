"""
What StepFunctionsExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property, which its ✦ Ask
drawer asks every question about.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from ..helpers import STATUS_WORDS, execution_name
from .frame import _EXPLORER_TABS, _SINCE_CHOICES, _STATUS_CHIPS

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_SERVICE = "stepfunctions"

_CHIP_CHARS = 80

_RANGED_TABS = ("executions", "failures")

_TAB_KINDS = {"overview": "machine", "executions": "executions", "graph": "graph", "failures": "failures",
              "definition": "definition"}


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


def _range_part(since: str) -> FocusPart:
    label = next(text for text, value in _SINCE_CHOICES if value == since)
    return FocusPart("range", since, label.lower())


def _execution_parts(x: StepFunctionsExplorer) -> list[FocusPart]:
    """
    The execution open in the Executions tab: its name, how it ended once its history is read, and for one that
    failed the error and the state it stopped at.
    """
    arn = x._selected
    parts = [FocusPart("execution", arn, f"execution {execution_name(arn)}")]
    detail = x.detail if x.detail is not None and x.detail.execution.arn == arn else None
    if detail is None:
        return parts
    execution = detail.execution
    parts.append(FocusPart("status", execution.status, STATUS_WORDS.get(execution.status, execution.status)))
    failed_step = detail.failed_step
    error = execution.error or (failed_step.error if failed_step else "")
    if execution.failed and error:
        parts.append(FocusPart("error", error, _clip(error, _CHIP_CHARS)))
    if failed_step is not None:
        step_error = f" ({_clip(failed_step.error, _CHIP_CHARS)})" if failed_step.error not in ("", error) else ""
        parts.append(FocusPart("failed_state", failed_step.name, f"stopped at {failed_step.name}{step_error}"))
    return parts


def _list_parts(x: StepFunctionsExplorer) -> list[FocusPart]:
    """
    How the Executions tab's list is narrowed: an outcome chip and the search box.
    """
    parts = []
    if x._status_filter:
        label = next(text for status, text, _ in _STATUS_CHIPS if status == x._status_filter)
        parts.append(FocusPart("status_filter", x._status_filter, f"{label.lower()} only"))
    if x._query:
        parts.append(FocusPart("search", x._query, _clip(f"search: {x._query}", _CHIP_CHARS)))
    return parts


def _graph_parts(x: StepFunctionsExplorer) -> list[FocusPart]:
    """
    What the Graph tab colours (the execution open, or where executions stop in the time range) and the state picked.
    """
    parts = []
    if x.graph_mode == "execution" and x._selected:
        parts.append(FocusPart("execution", x._selected, f"execution {execution_name(x._selected)}"))
    if x.graph_mode == "failures":
        parts.append(_range_part(x._since))
    if x.picked_state:
        parts.append(FocusPart("state", x.picked_state, f"{x.picked_state} state"))
    return parts


class _FocusReads:
    """
    The explorer's `focus`, a part of StepFunctionsExplorer.
    """

    def _shows_failures(self: StepFunctionsExplorer) -> bool:
        return self._tab == "failures" or (self._tab == "graph" and self.graph_mode == "failures")

    def _focus_since(self: StepFunctionsExplorer) -> datetime | None:
        """
        Where the time range shown starts: the failures report's on the Failures tab (or the Graph tab coloured by
        failures), else the executions list's; None until it's read.
        """
        if self._shows_failures():
            return self.report.since if self.report is not None else None
        return self.page.since if self.page is not None else None

    @property
    def focus(self: StepFunctionsExplorer) -> Focus:
        """
        What the window is showing, as data: kind 'machines' (the State machines tab, or nothing open yet), 'machine'
        (a state machine's Overview), 'executions', 'execution' (one open in the Executions tab), 'graph', 'state' (a
        state picked in the Graph tab), 'failures' or 'definition', with the state machine, the tab, the time range,
        and the execution, its outcome and error, or the state picked.
        """
        machine = self.machine
        region = self.core.region
        if machine is None or self._tab == "machines":
            return Focus(_SERVICE, "machines", region, parts=(_tab_part("machines"),))
        parts = [FocusPart("state_machine", machine.arn, machine.name), _tab_part(self._tab)]
        kind = _TAB_KINDS[self._tab]
        if self._tab in _RANGED_TABS:
            parts.append(_range_part(self._since))
        if self._tab == "executions" and self._selected:
            parts.extend(_execution_parts(self))
            kind = "execution"
        elif self._tab == "executions":
            parts.extend(_list_parts(self))
        if self._tab == "graph":
            parts.extend(_graph_parts(self))
            kind = "state" if self.picked_state else "graph"
        ranged = self._tab == "executions" or self._shows_failures()
        since = self._focus_since() if ranged else None
        return Focus(_SERVICE, kind, region, since=since, until=None, parts=tuple(parts))
