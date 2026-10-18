"""
What a function costs a month: requests, compute, provisioned concurrency and its logs.
"""

from __future__ import annotations

from typing import Iterable

from ..._kit.fmt import HOURS_PER_MONTH

from ..prices import DAYS_PER_MONTH, LAMBDA_PRICES
from ..helpers import GB
from ..models import Function, FunctionMetrics, LogGroup, ProvisionedConcurrency


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
        per_month = DAYS_PER_MONTH / metrics.days
        seconds = metrics.duration_sum / 1000 * per_month
        cost["requests"] = metrics.invocations * per_month * prices["request"] / 1e6
        cost["compute"] = seconds * fn.gb * prices["gb_second_arm" if fn.arm else "gb_second"]
        if fn.ephemeral_storage > 512:
            cost["storage"] = seconds * (fn.ephemeral_storage - 512) / 1024 * prices["ephemeral_storage"]
        if metrics.log_bytes is not None:
            cost["logs"] = metrics.log_bytes / GB * per_month * prices["log_ingestion"]
    stored = log_storage_monthly_cost(log_group, prices)
    if stored is not None:
        cost["log_storage"] = stored
    copies = sum(p.billed for p in provisioned)
    if copies:
        cost["provisioned"] = provisioned_monthly_cost(fn, copies, prices)
    return cost
