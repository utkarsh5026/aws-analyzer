"""
What LambdaAnalyzer reads for performance(): each run's REPORT line in a window, and with compare=True the same
length of time just before it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code

from .models import Function, Performance
from .analysis.logs import parse_report


_REPORT_PATTERN = '?"REPORT RequestId" ?"platform.report"'


class _PerformanceReads:
    """
    LambdaAnalyzer's performance reads: the REPORT lines Lambda logs after each run, through its CloudWatch Logs
    filter (`_filter`), for a function `function()` finds.
    """

    def performance(  # noqa: PLR0913
        self,
        ref: str,
        *,
        since: Any = "24h",
        until: Any = None,
        limit: int | None = 5_000,
        compare: bool = False,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """
        Every run from `since` to `until` (the newest `limit` when there are more), from the REPORT line Lambda
        logs after each: run time, billed time, memory used, and the start-up time of cold starts. compare=True also
        reads the same length of time just before into `.before`, so you can see what changed.
        """
        fn = self.function(ref, region=region)
        start, end = self.time_range(since, until)
        return self._performance(fn, start, end, limit=limit, compare=compare, progress=progress)

    def _report_lines(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None,
        progress: Callable[..., None] | None,
    ) -> Performance:
        """
        The runs from start to end, from their REPORT lines, newest first; a read that fails is in `errors`.
        """
        perf = Performance(fn, fn.log_group, start, end)
        try:
            events, perf.truncated, perf.covered_from = self._filter(
                fn.log_group, fn.region, start, end, _REPORT_PATTERN, limit, progress=progress)
        except ClientError as exc:
            perf.errors["logs"] = _error_code(exc)
            return perf
        except BotoCoreError as exc:
            perf.errors["logs"] = type(exc).__name__
            return perf
        runs = []
        for event in events:
            run = parse_report(event.message, event.time)
            if run is not None:
                run.stream = event.stream
                runs.append(run)
        perf.invocations = sorted(runs, key=lambda r: r.time or start, reverse=True)
        return perf

    def _performance(  # noqa: PLR0913
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = 5_000,
        compare: bool = False,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """
        performance() for a function already read (no GetFunction). With compare=True and runs to compare, the same
        length of time before `start` goes in `.before`, read with the same limit.
        """
        perf = self._report_lines(fn, start, end, limit=limit, progress=progress)
        if compare and perf.invocations and not perf.errors:
            already = len(perf.invocations)
            counting = (lambda n: progress(already + n)) if progress else None
            perf.before = self._report_lines(fn, start - (end - start), start, limit=limit, progress=counting)
        return perf
