"""
The range summary the Logs and Performance tabs open with: every call CloudWatch counted in the tab's time range, the
failed and throttled ones, run time, the most running at once and what it cost; on the Logs tab, also the cold starts
and memory of the runs read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.blocks import _Cards, _Note
from ..._kit.errors import _why
from ..._kit.fmt import _plural
from ..._kit.text import _esc

from ..models import FunctionStats
from ..analysis.stats import run_sample
from ..analysis.text import _span_said
from ..blocks.stats import _metric_cards, _sample_cards

_RANGE_TABS = ("logs", "performance")


def _job_key(tab: str) -> str:
    return f"range-{tab}"


class _RangeSummary:
    """
    The summary cards over the Logs and Performance tabs (a mixin of LambdaExplorer): read with
    LambdaAnalyzer.range_metrics, one GetMetricData call, each time the tab reads its range.
    """

    range_views: dict[str, Any]
    range_stats: dict[str, FunctionStats]

    def _range_summary_view(self, tab: str) -> Any:
        """
        The tab's summary widget, empty until its range is read.
        """
        view = self._w.HTML(layout=self._w.Layout(width="100%"))
        view.add_class("lmx-rangesum")
        self.range_views[tab] = view
        return view

    def _clear_range_summaries(self) -> None:
        self.range_stats.clear()
        for view in self.range_views.values():
            self._set(view, "")

    def _drop_range_summaries(self) -> None:
        self._drop(*(_job_key(tab) for tab in _RANGE_TABS))

    def _load_range_summary(self, tab: str, start: datetime, end: datetime) -> None:
        """
        Reads CloudWatch's numbers for the tab's range in the background, and draws them when they're in.
        """
        fn = self.function
        if fn is None:
            return
        self.range_stats.pop(tab, None)
        self._set(self.range_views[tab], "")
        self._later(_job_key(tab), lambda: self.core.range_metrics(fn, start, end),
                    lambda stats: self._got_range_summary(tab, stats),
                    lambda exc: self._range_summary_failed(tab, exc))

    def _got_range_summary(self, tab: str, stats: FunctionStats) -> None:
        self.range_stats[tab] = stats
        self._draw_range_summary(tab)

    def _range_summary_failed(self, tab: str, exc: BaseException) -> None:
        self._set(self.range_views[tab], self._html([_Note(f"Couldn't read the range's numbers: "
                                                           f"{self._error_text(exc)}", "warn")]))

    def _read_runs_cards(self) -> list[tuple[str, ...]]:
        """
        Cold starts and memory of the runs the Logs tab read (its newest lines in the range, not every run).
        """
        reports = [run.report for run in self._runs if run.report is not None]
        sample = run_sample(reports)
        return _sample_cards(sample, read=f"{_plural(sample.runs, 'run')} read")

    def _draw_range_summary(self, tab: str) -> None:
        """
        The tab's summary: a heading naming the range, then the cards, or why CloudWatch couldn't be read.
        """
        stats = self.range_stats.get(tab)
        if stats is None:
            return
        if "metrics" in stats.errors:
            why = _why(stats.errors["metrics"], "cloudwatch:GetMetricData")
            self._set(self.range_views[tab], self._html([_Note(f"Couldn't read CloudWatch's numbers ({why}).",
                                                               "warn")]))
            return
        cards = _metric_cards(stats)
        if tab == "logs":
            cards += self._read_runs_cards()
        span = _span_said(stats.since, stats.until)
        basis = self.ui._price_basis()
        head = (f'<div class="lmx-h">In {_esc(span)}<span>every call CloudWatch counted · the cost estimated at '
                f"{_esc(basis)}, before the free tier</span></div>")
        self._set(self.range_views[tab], head + self._html([_Cards(cards)]))
