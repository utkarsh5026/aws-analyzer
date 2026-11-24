"""
What a function costs a month: requests, compute, provisioned concurrency and its logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..._kit.fmt import HOURS_PER_MONTH, human_size

from ..prices import DAYS_PER_MONTH, LAMBDA_PRICES
from ..helpers import GB
from ..models import Function, FunctionMetrics, LogGroup, ProvisionedConcurrency

_CENT = 0.01
_WHOLE_NUMBERS_FROM = 100
_FREE_TMP_MB = 512


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


@dataclass(frozen=True)
class CostWorking:
    """
    How one part of a function's monthly cost was worked out: what was measured (scaled to a month), the price it
    was multiplied by, the sum written out, and what it came to.
    """

    part: str
    measured: str
    price: str
    sum: str
    cost: float


def _unit_price(usd: float) -> str:
    """
    A price as the price list writes it: 0.2 -> '$0.20', 0.0000166667 -> '$0.0000166667'.
    """
    if usd >= _CENT:
        return f"${usd:,.2f}"
    return f"${usd:.10f}".rstrip("0")


def _amount(value: float) -> str:
    """
    1234.5 -> '1,235', 3.25 -> '3.25', 0.004 -> '0.004': whole numbers once they're big, else a few digits.
    """
    if value >= _WHOLE_NUMBERS_FROM:
        return f"{value:,.0f}"
    return f"{value:.3g}"


def _gb_text(fn: Function) -> str:
    """
    The memory a run is billed for: '1 GB (1,024 MB)'.
    """
    return f"{_amount(fn.gb)} GB ({fn.memory:,} MB)"


def _usage_workings(fn: Function, metrics: FunctionMetrics, prices: dict[str, float]) -> list[CostWorking]:
    """
    The parts billed by use (requests, compute, /tmp, logs sent), each CloudWatch number over its window scaled to
    a month.
    """
    per_month = DAYS_PER_MONTH / metrics.days
    window = f"in {_amount(metrics.days)} days"
    calls = metrics.invocations * per_month
    seconds = metrics.duration_sum / 1000 * per_month
    gb_second_key = "gb_second_arm" if fn.arm else "gb_second"
    workings = [
        CostWorking("requests", f"{_amount(metrics.invocations)} calls {window} = {_amount(calls)} a month",
                    f"{_unit_price(prices['request'])} per million calls",
                    f"{_amount(calls)} / 1,000,000 x {_unit_price(prices['request'])}",
                    calls * prices["request"] / 1e6),
        CostWorking("compute", f"{_amount(metrics.duration_sum / 1000)} s of runs {window} = {_amount(seconds)} s a "
                               f"month, at {_gb_text(fn)}",
                    f"{_unit_price(prices[gb_second_key])} per GB-second ({fn.architecture})",
                    f"{_amount(seconds)} s x {_amount(fn.gb)} GB x {_unit_price(prices[gb_second_key])}",
                    seconds * fn.gb * prices[gb_second_key]),
    ]
    if fn.ephemeral_storage > _FREE_TMP_MB:
        extra_gb = (fn.ephemeral_storage - _FREE_TMP_MB) / 1024
        workings.append(CostWorking(
            "storage", f"{_amount(seconds)} s of runs a month, with {_amount(extra_gb)} GB of /tmp above the free "
                       f"{_FREE_TMP_MB} MB",
            f"{_unit_price(prices['ephemeral_storage'])} per GB-second",
            f"{_amount(seconds)} s x {_amount(extra_gb)} GB x {_unit_price(prices['ephemeral_storage'])}",
            seconds * extra_gb * prices["ephemeral_storage"]))
    if metrics.log_bytes is not None:
        logged_gb = metrics.log_bytes / GB * per_month
        workings.append(CostWorking(
            "logs", f"{human_size(metrics.log_bytes)} logged {window} = {_amount(logged_gb)} GB a month",
            f"{_unit_price(prices['log_ingestion'])} per GB sent",
            f"{_amount(logged_gb)} GB x {_unit_price(prices['log_ingestion'])}",
            logged_gb * prices["log_ingestion"]))
    return workings


def _log_storage_working(group: LogGroup, cost: float, prices: dict[str, float]) -> CostWorking:
    """
    What keeping the log group's logs costs, at their size now.
    """
    stored_gb = (group.stored_bytes or 0) / GB
    return CostWorking("log_storage", f"{human_size(group.stored_bytes)} stored in {group.name} now",
                       f"{_unit_price(prices['log_storage'])} per GB-month",
                       f"{_amount(stored_gb)} GB x {_unit_price(prices['log_storage'])}", cost)


def _provisioned_working(fn: Function, copies: int, prices: dict[str, float]) -> CostWorking:
    """
    What keeping copies ready costs, every second of the month whether they run or not.
    """
    key = "provisioned_arm" if fn.arm else "provisioned"
    seconds = HOURS_PER_MONTH * 3600
    return CostWorking("provisioned", f"{copies:,} copies kept ready, at {_gb_text(fn)}, all month "
                                      f"({HOURS_PER_MONTH:,.0f} hours)",
                       f"{_unit_price(prices[key])} per GB-second ({fn.architecture})",
                       f"{copies:,} x {_amount(fn.gb)} GB x {_amount(seconds)} s x {_unit_price(prices[key])}",
                       provisioned_monthly_cost(fn, copies, prices))


def cost_workings(
    fn: Function,
    metrics: FunctionMetrics | None = None,
    provisioned: Iterable[ProvisionedConcurrency] = (),
    log_group: LogGroup | None = None,
    *,
    prices: dict[str, float] | None = None,
) -> list[CostWorking]:
    """
    How function_monthly_cost() works out each part, as sentences a reader can check: what CloudWatch measured over
    its window scaled to a month (DAYS_PER_MONTH days), the price used and the sum, so the estimate isn't a number
    taken on trust. The same parts, in the same order, with the same costs.
    """
    prices = {**LAMBDA_PRICES, **(prices or {})}
    workings: list[CostWorking] = []
    if metrics is not None and metrics.days:
        workings += _usage_workings(fn, metrics, prices)
    stored = log_storage_monthly_cost(log_group, prices)
    if log_group is not None and stored is not None:
        workings.append(_log_storage_working(log_group, stored, prices))
    copies = sum(p.billed for p in provisioned)
    if copies:
        workings.append(_provisioned_working(fn, copies, prices))
    return workings


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
    billed a little less than this. cost_workings() shows how each part is worked out.
    """
    workings = cost_workings(fn, metrics, provisioned, log_group, prices=prices)
    return {working.part: working.cost for working in workings}
