"""
What LambdaAnalyzer reads for stats() and the explorer's range summary: a function's numbers over a range of time,
from CloudWatch, and its runs' REPORT lines for cold starts and memory.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code

from .models import Function, FunctionMetrics, FunctionStats, Performance
from .analysis.cost import function_range_cost


class _StatsReads:
    """
    LambdaAnalyzer's reads for a range of time (a mixin: it uses the analyzer's prices, function(), time_range(),
    metrics() and _performance()).
    """

    prices: dict[str, float]
    function: Callable[..., Function]
    time_range: Callable[..., tuple[datetime, datetime]]
    metrics: Callable[..., tuple[dict[str, FunctionMetrics], int]]
    _performance: Callable[..., Performance]

    def stats(self, ref: str, *, since: Any = "7d", until: Any = None, limit: int = 5_000,
              region: str | None = None) -> FunctionStats:
        """
        A function's numbers from `since` to `until` (FunctionStats): what CloudWatch counted (calls, failed and
        throttled calls, run time, the most running at once, a row a day), what that cost, and the cold starts and
        memory from its runs' REPORT lines (the newest `limit` runs; limit=0 skips reading its logs).
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._stats(fn, start, end, limit=limit)

    def _stats(self, fn: Function, start: datetime, end: datetime, *, limit: int = 5_000,
               progress: Callable[..., None] | None = None) -> FunctionStats:
        """
        stats() for a function already read (no GetFunction), telling `progress` how many REPORT lines it has read.
        """
        stats = FunctionStats(fn, start, end, prices=self.prices)
        self._read_range_metrics(stats)
        if limit > 0:
            stats.perf = self._performance(fn, start, end, limit=limit, progress=progress)
            stats.errors.update(stats.perf.errors)
        return stats

    def range_metrics(self, fn: Function, start: datetime, end: datetime) -> FunctionStats:
        """
        Only CloudWatch's numbers for the range, and their cost: stats() without the REPORT lines.
        """
        stats = FunctionStats(fn, start, end, prices=self.prices)
        self._read_range_metrics(stats)
        return stats

    def _read_range_metrics(self, stats: FunctionStats) -> None:
        """
        Fills in what CloudWatch counted over the range and what it cost, or the error code in stats.errors.
        """
        fn = stats.function
        try:
            found, stats.metrics_read = self.metrics([fn], since=stats.since, until=stats.until, concurrency=True)
        except ClientError as exc:
            stats.errors["metrics"] = _error_code(exc)
            return
        except BotoCoreError as exc:
            stats.errors["metrics"] = type(exc).__name__
            return
        stats.metrics = found.get(fn.arn)
        if stats.metrics is not None:
            stats.cost = function_range_cost(fn, stats.metrics, prices=self.prices)
