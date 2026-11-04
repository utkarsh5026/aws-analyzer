"""
What a function costs a month, or over a range of time: requests, compute, provisioned concurrency and its logs.
"""

from __future__ import annotations

from typing import Iterable

from ..._kit.fmt import HOURS_PER_MONTH

from ..prices import DAYS_PER_MONTH, LAMBDA_PRICES
from ..helpers import GB
from ..models import DailyUsage, Function, FunctionMetrics, LogGroup, ProvisionedConcurrency


COST_PARTS = {
    "requests": "Requests",
    "compute": "Compute (memory x run time)",
    "storage": "/tmp above 512 MB",
    "logs": "Logs sent to CloudWatch",
    "log_storage": "Logs kept in CloudWatch",
    "provisioned": "Provisioned concurrency",
}


def provisioned_monthly_cost(fn: Function, copies: int, prices: dict[str, float] | None = None) -> float:
    """
    What keeping `copies` copies of the function ready (provisioned concurrency) costs a month, used or not.
    """
    prices = {**LAMBDA_PRICES, **(prices or {})}
    return copies * fn.gb * HOURS_PER_MONTH * 3600 * prices["provisioned_arm" if fn.arm else "provisioned"]


def log_storage_monthly_cost(group: LogGroup | None, prices: dict[str, float] | None = None) -> float | None:
    """
    What keeping a log group's logs costs a month (CloudWatch Logs storage), at its size now.
    """
    if group is None or group.stored_bytes is None:
        return None
    prices = {**LAMBDA_PRICES, **(prices or {})}
    return group.stored_bytes / GB * prices["log_storage"]


def _usage_cost(fn: Function, usage: FunctionMetrics | DailyUsage, *, scale: float,
                prices: dict[str, float]) -> dict[str, float]:
    """
    USD by part for what CloudWatch counted, times `scale` (1 for the window itself, a month over its days for a
    monthly estimate): 'requests', 'compute', 'storage' (/tmp above 512 MB) and, for a whole window with log bytes
    read, 'logs'.
    """
    seconds = usage.duration_sum / 1000 * scale
    cost = {
        "requests": usage.invocations * scale * prices["request"] / 1e6,
        "compute": seconds * fn.gb * prices["gb_second_arm" if fn.arm else "gb_second"],
    }
    if fn.ephemeral_storage > 512:
        cost["storage"] = seconds * (fn.ephemeral_storage - 512) / 1024 * prices["ephemeral_storage"]
    log_bytes = getattr(usage, "log_bytes", None)
    if log_bytes is not None:
        cost["logs"] = log_bytes / GB * scale * prices["log_ingestion"]
    return cost


def function_range_cost(fn: Function, usage: FunctionMetrics | DailyUsage, *,
                        prices: dict[str, float] | None = None) -> dict[str, float]:
    """
    Estimated USD, by part, for what CloudWatch counted over its window as it is, not scaled to a month: 'requests',
    'compute' (memory x run time), 'storage' (/tmp above 512 MB) and 'logs' (what it sent to CloudWatch Logs, when
    read). Works on a whole window (FunctionMetrics) or one of its days (DailyUsage, without logs). Before the free
    tier, and without provisioned concurrency or the logs CloudWatch keeps, which are billed by the month.
    """
    return _usage_cost(fn, usage, scale=1.0, prices={**LAMBDA_PRICES, **(prices or {})})


def function_monthly_cost(
    fn: Function,
    metrics: FunctionMetrics | None = None,
    provisioned: Iterable[ProvisionedConcurrency] = (),
    log_group: LogGroup | None = None,
    *,
    prices: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Estimated USD a month, by part: 'requests', 'compute' (memory x run time), 'storage' (/tmp above 512 MB),
    'logs' (what it sends to CloudWatch Logs), 'log_storage' (what the log group keeps) and 'provisioned'
    (copies kept ready). Usage parts scale what CloudWatch counted over its window (metrics) to a month; a part
    without the numbers it needs is left out. Before the free tier; compute that runs on provisioned concurrency is
    billed a little less than this.
    """
    prices = {**LAMBDA_PRICES, **(prices or {})}
    cost: dict[str, float] = {}
    if metrics is not None and metrics.days:
        cost = _usage_cost(fn, metrics, scale=DAYS_PER_MONTH / metrics.days, prices=prices)
    stored = log_storage_monthly_cost(log_group, prices)
    if stored is not None:
        cost["log_storage"] = stored
    copies = sum(p.billed for p in provisioned)
    if copies:
        cost["provisioned"] = provisioned_monthly_cost(fn, copies, prices)
    return cost
