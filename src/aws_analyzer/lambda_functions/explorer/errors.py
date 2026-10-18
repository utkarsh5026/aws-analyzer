"""
The Errors tab's mixin.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _in_zone, _utcnow
from ..._kit.text import _clip, _esc
from ..._kit.window import _skeleton

from ..models import ErrorReport, LogEvent
from ..analysis.errors import classify_error
from ..analyzer import LambdaAnalyzer
from ..view import _stamp
from .frame import _run_clock
from .widgets import _Row


class _ErrorsTab:
    """
    The Errors tab of LambdaExplorer: the function's errors grouped by cause, and its failed runs to open.
    """

    def _errors_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar, (self.errors_pick, again) = self._range_bar("24h", self._errors_range_changed, "its errors")
        again.on_click(self._safely(lambda _button: self._load_errors()))
        self.errors_view = w.HTML(layout=layout(width="100%"))
        self.error_head = w.HTML(layout=layout(width="100%"))
        self.error_rows_box = w.VBox(layout=layout(width="100%"))
        return self._scroll_page([self.errors_view, self.error_head, self.error_rows_box], above=[bar])

    # ------------------------------------------------------------------ errors and performance

    def _errors_range_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self._errors_range = str(change["new"])
        self._load_errors()

    def _load_errors(self) -> None:
        if self.function is None:
            return
        self._asked.add("errors")
        fn = self._full()
        start, end = LambdaAnalyzer.time_range(self._errors_range)
        self.error_report = None
        self._set(self.errors_view, _skeleton(f"Reading {fn.name}'s errors from {self._range_text(self._errors_range)}"
                                              "…"))
        self._set(self.error_head, "")
        self.error_rows_box.children = []
        self._counted = 0
        self._status(f"Reading {fn.name}'s error lines…", busy=True, owner="errors")
        self._later("errors", lambda: self.core._errors(fn, start, end, progress=lambda n: setattr(self, "_counted",
                                                                                                  n)),
                    self._got_errors, self._errors_failed,
                    counting=f"Reading {fn.name}'s error lines… {{n}} read so far", owner="errors")

    def _errors_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.errors_view, self._html([_Note(text, "warn")]))
        self._status(text, "warn")

    def _got_errors(self, report: ErrorReport) -> None:
        self.error_report = report
        self._draw("errors", self.errors_view, self.ui._errors_blocks(report, since=self._errors_range, for_window=True))
        failed = [e for e in report.newest]
        today = _in_zone(_utcnow()).date()
        while len(self._error_rows) < len(failed):
            self._error_rows.append(_Row(self, self._clicked_failure, short=True))
        for row, event in zip(self._error_rows, failed):
            row.item = event
            kind, summary = classify_error(event.message) or ("Error", _clip(event.message, 200))
            self._set(row.face, f'<div class="er"><span class="ri">✕</span><span class="ew">'
                                f"{_esc(_run_clock(event.time, today))}</span><span class=\"ek\">{_esc(kind)}</span>"
                                f'<span class="es">{_esc(summary.splitlines()[0][:300])}</span>'
                                '<span class="eo">Read the run ›</span></div>')
            tip = (f"{_stamp(event.time, zone=True)} · request {event.request_id or 'unknown'} · click to read every "
                   "line")
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        self.error_rows_box.children = [row.box for row in self._error_rows[:len(failed)]]
        self._set(self.error_head, '<div class="lmx-h">The newest failed runs<span>click one to read every line of '
                                   "it in the Logs tab</span></div>" if failed else "")
        self._finish("errors", self._tab_line("errors"), tabs=("errors",))

    def _clicked_failure(self, event: LogEvent) -> None:
        self._show_run(event.time, event.stream, event.request_id)
