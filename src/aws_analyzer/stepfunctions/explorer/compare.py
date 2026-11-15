"""
The Executions tab's ⇄ Compare: on an execution that didn't succeed, a button beside ↗ Console that reads the newest
execution that succeeded before it and draws the two side by side above its page (compare()'s report), the likely
cause first; a second click hides it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.window import _class_if, _skeleton

from ..analysis.compare import ExecutionComparison
from ..helpers import execution_name
from ..models import ExecutionDetail

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_COMPARE_LABEL = "⇄ Compare with the last success"

_HIDE_LABEL = "✕ Hide the comparison"


class _CompareSlot:
    """
    ⇄ Compare in the Executions tab, a part of StepFunctionsExplorer: what it keeps is `comparison`, the
    ExecutionComparison drawn (None while none is).
    """

    def _compare_widgets(self: StepFunctionsExplorer) -> None:
        w, layout = self._w, self._w.Layout
        self.comparison: ExecutionComparison | None = None
        self.compare_button = w.Button(description=_COMPARE_LABEL, layout=layout(width="auto", display="none"),
                                       tooltip="Read the newest execution that succeeded before this one, and show "
                                               "the two side by side: the states and input fields that differ")
        self.compare_button.add_class("sfx-small")
        self.compare_button.on_click(self._safely(lambda _button: self._toggle_compare()))
        self.compare_view = w.HTML(layout=layout(width="100%"))

    def _reset_compare(self: StepFunctionsExplorer) -> None:
        """
        No comparison drawn or being read, and no button, as when another execution opens.
        """
        self._drop("compare")
        self.comparison = None
        self._set(self.compare_view, "")
        self.compare_button.description = _COMPARE_LABEL
        _class_if(self.compare_button, "sfx-on", False)
        self.compare_button.layout.display = "none"

    def _show_compare_button(self: StepFunctionsExplorer, detail: ExecutionDetail) -> None:
        """
        ⇄ Compare for an execution that didn't succeed, once its history is read (and its state machine records its
        executions, so there's a success to find).
        """
        can_compare = detail.execution.failed and (detail.machine is None or detail.machine.has_executions)
        self.compare_button.layout.display = "" if can_compare else "none"

    def _toggle_compare(self: StepFunctionsExplorer) -> None:
        if self.comparison is not None or self.compare_button.description == _HIDE_LABEL:
            self._reset_compare()
            if self.detail is not None:
                self._show_compare_button(self.detail)
            return
        self._start_compare(self._selected, None)

    def _start_compare(self: StepFunctionsExplorer, arn: str, other: str | None) -> None:
        name = execution_name(arn)
        self.compare_button.description = _HIDE_LABEL
        _class_if(self.compare_button, "sfx-on", True)
        self._set(self.compare_view, _skeleton(f"Finding the newest execution that succeeded before {name}…"))
        self._status(f"Comparing {name} with the last success…", busy=True, owner="compare")
        machine = self.machine.arn if self.machine is not None else None
        self._later("compare", lambda: self.core.compare(arn, other=other, machine=machine), self._got_comparison,
                    self._comparison_failed)

    def _got_comparison(self: StepFunctionsExplorer, comparison: ExecutionComparison) -> None:
        if comparison.this.execution.arn != self._selected:
            return
        self.comparison = comparison
        self._draw("compare", self.compare_view, self.ui._compare_blocks(comparison))
        this, other = comparison.this.execution.name, comparison.other.execution.name
        differ = len(comparison.differing_steps) + len(comparison.inputs)
        self._finish("compare", f"{this} beside {other}: {differ} difference{'' if differ == 1 else 's'}",
                     tabs=("executions",))

    def _comparison_failed(self: StepFunctionsExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.compare_view, self._html([_Note(text, "warn")]))

    def _compare_row(self: StepFunctionsExplorer) -> Any:
        """
        The line above an execution's page: ↗ Console and ⇄ Compare.
        """
        row = self._w.HBox([self.execution_link, self.compare_button])
        row.add_class("sfx-exec-row")
        return row
