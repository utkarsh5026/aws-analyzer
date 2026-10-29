"""
The Performance tab's mixin.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _in_zone, _utcnow
from ..._kit.text import _esc
from ..._kit.window import _skeleton

from ..helpers import human_ms
from ..models import Invocation, Performance
from ..analyzer import LambdaAnalyzer
from .frame import _run_clock
from .widgets import _Row


class _PerformanceTab:
    """
    The Performance tab of LambdaExplorer: run times, memory and cold starts, and the slowest runs to open.
    """

    def _performance_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar, (self.perf_pick, again) = self._range_bar("its runs")
        again.on_click(self._safely(lambda _button: self._load_perf()))
        self.perf_view = w.HTML(layout=layout(width="100%"))
        self.slow_head = w.HTML(layout=layout(width="100%"))
        self.slow_rows_box = w.VBox(layout=layout(width="100%"))
        return self._scroll_page([self.perf_view, self.slow_head, self.slow_rows_box], above=[bar])

    def _load_perf(self) -> None:
        if self.function is None:
            return
        self._asked.add("performance")
        fn = self._full()
        start, end = LambdaAnalyzer.time_range(self._range)
        self.perf = None
        self._set(self.perf_view, _skeleton(f"Reading {fn.name}'s runs from {self._range_text()}…"))
        self._set(self.slow_head, "")
        self.slow_rows_box.children = []
        self._counted = 0
        self._status(f"Reading {fn.name}'s REPORT lines…", busy=True, owner="performance")
        self._later("perf", lambda: self.core._performance(fn, start, end,
                                                           progress=lambda n: setattr(self, "_counted", n)),
                    self._got_perf, self._perf_failed,
                    counting=f"Reading {fn.name}'s REPORT lines… {{n}} read so far", owner="performance")

    def _perf_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.perf_view, self._html([_Note(text, "warn")]))
        self._status(text, "warn")

    def _got_perf(self, perf: Performance) -> None:
        self.perf = perf
        fn = perf.function
        self._draw("performance", self.perf_view, self.ui._performance_blocks(perf, since=self._range,
                                                                              for_window=True))
        slowest = sorted(perf.invocations, key=lambda r: r.duration, reverse=True)[:10]
        today = _in_zone(_utcnow()).date()
        while len(self._slow_rows) < len(slowest):
            self._slow_rows.append(_Row(self, self._clicked_slow, short=True))
        for row, inv in zip(self._slow_rows, slowest):
            row.item = inv
            share = min(1.0, inv.duration / (fn.timeout * 1000)) if fn.timeout else 0.0
            fill = "bad" if share >= 0.9 else "warn" if share >= 0.7 else ""
            bits = [f"{inv.max_memory:,} of {inv.memory:,} MB" if inv.memory else "",
                    f"cold start {human_ms(inv.init)}" if inv.init is not None else "",
                    f"status {inv.status}" + (f" ({inv.error_type})" if inv.error_type else "") if inv.status else ""]
            icon = "✕" if inv.status else "⏱"
            self._set(row.face, f'<div class="er slow"><span class="ri">{icon}</span><span class="ew">'
                                f"{_esc(_run_clock(inv.time, today) if inv.time else '-')}</span><span class=\"ek\">"
                                f'<span class="rr" style="display:inline;height:auto;padding:0"><span class="rb">'
                                f'<i class="{fill}" style="width:{max(share * 100, 2):.1f}%"></i></span></span> '
                                f"{_esc(human_ms(inv.duration))}</span><span class=\"es\">"
                                f"{_esc(' · '.join(b for b in bits if b))}</span>"
                                '<span class="eo">Read the run ›</span></div>')
            tip = f"request {inv.request_id} · click to read every line it logged"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        self.slow_rows_box.children = [row.box for row in self._slow_rows[:len(slowest)]]
        self._set(self.slow_head, '<div class="lmx-h">The slowest runs<span>click one to read every line of it in the '
                                  "Logs tab</span></div>" if slowest else "")
        self._finish("performance", self._tab_line("performance"), tabs=("performance",))

    def _clicked_slow(self, inv: Invocation) -> None:
        if inv.time is not None:
            self._show_run(inv.time, inv.stream, inv.request_id or None)
