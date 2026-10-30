"""
The Logs tab's timeline: a bar for each slice of the time range read, as tall as the runs that started in it and red
when some failed; a click shows only that slice's runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.fmt import _in_zone, _plural, _utcnow
from ..._kit.text import _esc
from ..._kit.window import _class_if

from ..analysis.logs import TIMELINE_BUCKETS, TimeBucket, run_timeline
from ..view import _stamp
from .frame import _run_clock


_SHORTEST_BAR = 12


def _bar_height(bucket: TimeBucket, busiest: int) -> str:
    """
    A bar's height, as a share of the strip: its runs against the busiest slice's, at least _SHORTEST_BAR percent
    when it holds any, so one run is still a bar to click.
    """
    if not bucket.runs:
        return "3px"
    return f"{max(_SHORTEST_BAR, round(100 * bucket.runs / busiest))}%"


def _bar_tip(bucket: TimeBucket) -> str:
    """
    What a bar's tooltip says: its slice of time, its runs and its failed ones, and what a click does.
    """
    if not bucket.runs:
        return f"{_stamp(bucket.start)} to {_stamp(bucket.end)}: no runs"
    failed = f", {bucket.failed:,} failed" if bucket.failed else ""
    return (f"{_stamp(bucket.start)} to {_stamp(bucket.end)}: {_plural(bucket.runs, 'run')}{failed} · click to show "
            "only these (again for every run)")


class _Timeline:
    """
    The strip of bars over the Logs tab's runs, one per TIMELINE_BUCKETS-th of the time range read, and the slice
    picked by a click (`_run_window`), which the run list then keeps to.
    """

    def _timeline_strip(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.timeline_bars: list[Any] = []
        for index in range(TIMELINE_BUCKETS):
            bar = w.Button(layout=layout(flex="1 1 0", width="auto", height="3px"))
            bar.add_class("lmx-tbar")
            bar.on_click(self._safely(lambda _button, index=index: self._clicked_bucket(index)))
            self.timeline_bars.append(bar)
        strip = w.HBox(self.timeline_bars, layout=layout(width="100%"))
        strip.add_class("lmx-timeline")
        self.timeline_axis = w.HTML(layout=layout(width="100%"))
        self.timeline_box = w.VBox([strip, self.timeline_axis], layout=layout(width="100%", display="none"))
        return self.timeline_box

    def _timeline_range(self) -> tuple[datetime, datetime]:
        """
        The stretch of time the runs were read from: the time range, from where reading stopped when it stopped
        at the limit.
        """
        start, end = self._range_window()
        page = self.logs_page
        if self.found is None and page is not None and page.truncated and page.covered_from is not None:
            start = max(start, page.covered_from)
        return start, min(end, _utcnow())

    def _draw_timeline(self) -> None:
        """
        Counts the runs read into the strip's bars. While a bar is picked the slices stay where they were, so the
        picked one doesn't drift with the clock.
        """
        runs = self._source_runs()
        if not runs:
            self.timeline_box.layout.display = "none"
            self._buckets = []
            return
        if self._run_window is not None and self._buckets:
            start, end = self._buckets[0].start, self._buckets[-1].end
        else:
            start, end = self._timeline_range()
        self._buckets = run_timeline(runs, start, end)
        busiest = max((bucket.runs for bucket in self._buckets), default=0) or 1
        for bar, bucket in zip(self.timeline_bars, self._buckets):
            height = _bar_height(bucket, busiest)
            if bar.layout.height != height:
                bar.layout.height = height
            _class_if(bar, "lmx-bad", bucket.failed > 0)
            _class_if(bar, "lmx-on", self._run_window == (bucket.start, bucket.end))
            bar.disabled = not bucket.runs
            tip = _bar_tip(bucket)
            if bar.tooltip != tip:
                bar.tooltip = tip
        today = _in_zone(_utcnow()).date()
        middle = start + (end - start) / 2
        labels = "".join(f"<span>{_esc(_run_clock(moment, today))}</span>" for moment in (start, middle, end))
        self._set(self.timeline_axis, f'<div class="lmx-taxis">{labels}</div>')
        self.timeline_box.layout.display = ""

    def _clicked_bucket(self, index: int) -> None:
        """
        A bar clicked: only the runs that started in its slice, or every run again when it was the one picked.
        """
        if index >= len(self._buckets):
            return
        bucket = self._buckets[index]
        picked = (bucket.start, bucket.end)
        self._run_window = None if self._run_window == picked else picked
        self._run_offset = 0
        self._draw_timeline()
        self._draw_runs(renew=True)
        if self._run_window is None:
            self._status(self._logs_line())
            return
        shown = len(self._visible_runs())
        self._status(f"{_plural(shown, 'run')} from {_stamp(bucket.start)} to {_stamp(bucket.end)} · click the bar "
                     "again for every run")
