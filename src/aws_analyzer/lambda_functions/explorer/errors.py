"""
The Errors tab's mixin.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Findings, _Note
from ..._kit.fmt import _count, _in_zone, _utcnow, human_age
from ..._kit.text import _clip, _esc
from ..._kit.window import _skeleton

from ..models import ErrorGroup, ErrorReport, LogEvent
from ..analysis.errors import cause_search, classify_error, failing_frame
from ..view import _stamp
from .frame import _run_clock
from .widgets import _Row


class _ErrorsTab:
    """
    The Errors tab of LambdaExplorer: the function's errors grouped by cause, and its failed runs to open.
    """

    def _errors_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar, (self.errors_pick, again) = self._range_bar("its errors")
        again.on_click(self._safely(lambda _button: self._load_errors()))
        self.errors_view = w.HTML(layout=layout(width="100%"))
        self.cause_head = w.HTML(layout=layout(width="100%"))
        self.cause_rows_box = w.VBox(layout=layout(width="100%"))
        self.errors_more = w.HTML(layout=layout(width="100%"))
        self.error_head = w.HTML(layout=layout(width="100%"))
        self.error_rows_box = w.VBox(layout=layout(width="100%"))
        return self._scroll_page([self.errors_view, self.cause_head, self.cause_rows_box, self.errors_more,
                                  self.error_head, self.error_rows_box], above=[bar])

    # ------------------------------------------------------------------ errors and performance

    def _load_errors(self) -> None:
        if self.function is None:
            return
        self._asked.add("errors")
        fn = self._full()
        start, end = self._tab_window()
        self.error_report = None
        self._set(self.errors_view, _skeleton(f"Reading {fn.name}'s errors from {self._tab_range_text()}…"))
        for widget in (self.cause_head, self.errors_more, self.error_head):
            self._set(widget, "")
        self.cause_rows_box.children = self.error_rows_box.children = []
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
        blocks = self.ui._errors_blocks(report, since=self._tab_range()[0], for_window=True)
        split = next((i for i, block in enumerate(blocks) if isinstance(block, _Findings)), len(blocks))
        self._draw("errors", self.errors_view, blocks[:split])
        self._set(self.errors_more, self._html(blocks[split:]))
        self._draw_causes(report)
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

    def _draw_causes(self, report: ErrorReport) -> None:
        """
        The errors by cause, the most frequent first, each a line to click for the runs it failed in (in the Logs
        tab), with Code on it when its traceback names a line of the function's own code.
        """
        groups = report.groups
        lines = sum(g.count for g in groups) or 1
        while len(self._cause_rows) < len(groups):
            self._cause_rows.append(_Row(self, self._clicked_cause, short=True,
                                         action=("Code ›", "Open the line it failed on in the Code tab",
                                                 self._clicked_cause_code)))
        for row, group in zip(self._cause_rows, groups):
            row.item = group
            share = group.count / lines
            self._set(row.face, f'<div class="er cause"><span class="ri">✕</span><span class="ec">×{_count(group.count)}'
                                f'</span><span class="ek">{_esc(group.kind)}</span><span class="es">'
                                f"{_esc(_clip(group.message, 300))}</span><span class=\"ew\">"
                                f"{_esc(human_age(group.last, report.until))}</span><span class=\"esh\"><i style=\""
                                f'width:{max(share * 100, 2):.1f}%"></i></span><span class="eo">Its runs ›</span></div>')
            frame = failing_frame(group.example)
            row.action.layout.display = "" if frame is not None else "none"
            tip = (f"{group.kind}: {group.count:,} of {lines:,} error lines ({share:.0%}), the last "
                   f"{human_age(group.last, report.until)} ago · click for the runs it failed in")
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        self.cause_rows_box.children = [row.box for row in self._cause_rows[:len(groups)]]
        self._set(self.cause_head, '<div class="lmx-h">Errors by cause<span>click one for the runs it failed in, '
                                   "in the Logs tab</span></div>" if groups else "")

    def _clicked_cause(self, group: ErrorGroup) -> None:
        """
        A cause clicked: the Logs tab, searched over the Errors tab's time range for the runs it failed in.
        """
        text = cause_search(group)
        self._set_range(*self._tab_range())
        self._show_tab("logs", load=False)
        self._quietly(self.log_find, value=text)
        self._log_query = text
        self._asked.add("logs")
        self._log_search()

    def _clicked_cause_code(self, group: ErrorGroup) -> None:
        frame = failing_frame(group.example)
        if frame is not None:
            self._open_code_at(frame)

    def _clicked_failure(self, event: LogEvent) -> None:
        self._show_run(event.time, event.stream, event.request_id)
