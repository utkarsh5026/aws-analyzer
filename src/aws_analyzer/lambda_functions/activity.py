"""
What LambdaAnalyzer reads for the explorer's Live tab: each function's last minutes from CloudWatch, minute by minute.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code
from .._kit.fmt import _utcnow

from .models import Activity, Function
from .analysis.live import LIVE_WINDOW_MINUTES, minute_values


_MINUTE = 60

_ACTIVITY_METRICS = (("calls", "Invocations", "Sum"), ("errors", "Errors", "Sum"),
                     ("concurrency", "ConcurrentExecutions", "Maximum"))


def _activity_queries(functions: list[Function]) -> list[dict[str, Any]]:
    """
    GetMetricData's queries for each function's calls, failed calls and most runs at once, a value per minute: IDs
    a<function>_<metric>, in the order of `functions` and _ACTIVITY_METRICS.
    """
    queries = []
    for i, fn in enumerate(functions):
        dimensions = [{"Name": "FunctionName", "Value": fn.name}]
        for j, (_, metric, stat) in enumerate(_ACTIVITY_METRICS):
            queries.append({"Id": f"a{i}_{j}", "MetricStat": {
                "Metric": {"Namespace": "AWS/Lambda", "MetricName": metric, "Dimensions": dimensions},
                "Period": _MINUTE, "Stat": stat}})
    return queries


class _ActivityReads:
    """
    LambdaAnalyzer's reads for the Live tab (a mixin: it uses the analyzer's region, _metric_data and clients).
    """

    region: str | None
    _metric_data: Callable[..., dict[str, dict[datetime, float]]]

    def activity(
        self, functions: list[Function], *, minutes: int = LIVE_WINDOW_MINUTES, until: datetime | None = None
    ) -> tuple[dict[str, Activity], int, dict[str, str]]:
        """
        What CloudWatch counted for each function in its last `minutes` minutes, a value per minute (the minute
        under way included), by function ARN: calls, failed calls and the most running at once. Also returns how many
        metrics that read (GetMetricData bills $0.01 per 1,000) and region -> the error code of a region that
        couldn't be read.
        """
        end = (until or _utcnow()).replace(second=0, microsecond=0) + timedelta(minutes=1)
        since = end - timedelta(minutes=minutes)
        by_region: dict[str, list[Function]] = defaultdict(list)
        for fn in functions:
            by_region[fn.region or self.region or ""].append(fn)
        found: dict[str, Activity] = {}
        read = 0
        errors: dict[str, str] = {}
        for region, group in by_region.items():
            queries = _activity_queries(group)
            try:
                series = self._metric_data(region or None, queries, since, end)
            except ClientError as exc:
                errors[region] = _error_code(exc)
                continue
            except BotoCoreError as exc:
                errors[region] = type(exc).__name__
                continue
            read += len(queries)
            for i, fn in enumerate(group):
                values = [minute_values(series.get(f"a{i}_{j}", {}), since, minutes)
                          for j in range(len(_ACTIVITY_METRICS))]
                found[fn.arn] = Activity(*values)
        return found, read, errors
