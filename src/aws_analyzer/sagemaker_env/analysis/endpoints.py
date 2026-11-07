"""
Endpoints and their traffic: which are idle and what they cost a month, which are barely used, and which answer
with errors; the findings of endpoints().
"""

from __future__ import annotations

from datetime import datetime

from ..._kit.fmt import HOURS_PER_MONTH, _plural, _utcnow, human_age, human_money

from ..prices import SAGEMAKER_PRICES
from ..models import EndpointReport, EndpointUse
from .common import _couldnt_read
from .notebooks import stop_command
from .running import _unused_endpoint


STALE_DAYS = 7

LOW_TRAFFIC_PER_DAY = 50

SERVER_ERROR_SHARE = 0.01

_SHOWN_IDLE = 6


def endpoint_monthly(use: EndpointUse, prices: dict[str, float] | None = None) -> float | None:
    """
    What an endpoint costs a month if left on: its instances' hourly price × 730 (0 for a serverless one, which
    bills per request; None when the price table lacks a type).
    """
    if use.endpoint.serverless:
        return 0.0
    hourly = use.endpoint.hourly(prices)
    return None if hourly is None else hourly * HOURS_PER_MONTH


def is_idle(use: EndpointUse, now: datetime | None = None) -> bool:
    """
    An endpoint on instances, at least a day old, that got no requests in the window.
    """
    return _unused_endpoint(use.endpoint, now)


def is_stale(use: EndpointUse, now: datetime | None = None) -> bool:
    """
    An endpoint that got requests in the window, but none in the last STALE_DAYS days.
    """
    last = use.last_called
    return last is not None and ((now or _utcnow()) - last).days >= STALE_DAYS


def _bring_back(use: EndpointUse) -> str:
    b = use.endpoint
    if not b.config:
        return ""
    return (f" (its endpoint configuration {b.config} stays, so aws sagemaker create-endpoint --endpoint-name "
            f"{b.name} --endpoint-config-name {b.config} brings it back)")


def _idle_finding(use: EndpointUse, report: EndpointReport, prices: dict[str, float],
                  now: datetime) -> tuple[str, str]:
    b = use.endpoint
    monthly = endpoint_monthly(use, prices)
    cost = f"costing {human_money(monthly)}/month" if monthly is not None else "billing every hour"
    return ("warn", (f"Endpoint {b.name} ({b.instance_label}) got no requests in the last {report.days} days (made "
                     f"{human_age(b.since, now)}) but is {cost}. If nothing uses it, delete it{_bring_back(use)}: "
                     f"{stop_command(b)}"))


def _stale_finding(use: EndpointUse, prices: dict[str, float], now: datetime) -> tuple[str, str]:
    b = use.endpoint
    monthly = endpoint_monthly(use, prices)
    cost = f" ({human_money(monthly)}/month)" if monthly else ""
    return ("warn", (f"Endpoint {b.name}'s last request was {human_age(use.last_called, now)}, but it still runs "
                     f"{b.instance_label}{cost}. If its users have moved on, delete it{_bring_back(use)}: "
                     f"{stop_command(b)}"))


def _quiet_finding(use: EndpointUse, report: EndpointReport, prices: dict[str, float]) -> tuple[str, str] | None:
    b = use.endpoint
    monthly = endpoint_monthly(use, prices)
    per_day = (use.requests or 0) / max(report.days, 1)
    if b.serverless or not monthly or not use.requests or per_day >= LOW_TRAFFIC_PER_DAY:
        return None
    return ("info", (f"Endpoint {b.name} averages {per_day:,.1f} requests a day on {b.instance_label}, "
                     f"{human_money(monthly)}/month. Serverless inference bills per request and scales to zero, which "
                     "is usually cheaper at this traffic (ServerlessConfig in a new endpoint configuration)."))


def _error_finding(use: EndpointUse) -> tuple[str, str] | None:
    if not use.requests or not use.server_errors:
        return None
    share = use.server_errors / use.requests
    if share < SERVER_ERROR_SHARE:
        return None
    name = use.endpoint.name
    return ("warn", (f"Endpoint {name}: {share:.1%} of its requests ({use.server_errors:,.0f}) failed with a 5xx "
                     "error, so its model container is raising errors or timing out. Its container log says why: "
                     f"aws logs tail /aws/sagemaker/Endpoints/{name} --since 1d"))


def endpoint_findings(report: EndpointReport, prices: dict[str, float] | None = None,
                      now: datetime | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about the region's endpoints: the idle ones and what each costs a month, the ones nobody
    has called lately, the barely used ones serverless would be cheaper for, and the ones failing requests, each
    with the command to run -> [(level, message)].
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    now = now or _utcnow()
    found: list[tuple[str, str]] = []
    idle = [use for use in report.endpoints if is_idle(use, now)]
    idle.sort(key=lambda use: -(endpoint_monthly(use, prices) or 0))
    found += [_idle_finding(use, report, prices, now) for use in idle[:_SHOWN_IDLE]]
    if len(idle) > 1:
        total = sum(endpoint_monthly(use, prices) or 0 for use in idle)
        found.append(("warn", (f"{_plural(len(idle), 'idle endpoint')} cost {human_money(total)}/month together; "
                               "deleting an endpoint keeps its model and configuration.")))
    for use in report.endpoints:
        if is_stale(use, now):
            found.append(_stale_finding(use, prices, now))
    for use in report.endpoints:
        if is_idle(use, now) or is_stale(use, now):
            continue
        for note in (_error_finding(use), _quiet_finding(use, report, prices)):
            if note:
                found.append(note)
    if report.errors:
        found.append(("info", _couldnt_read(report.errors, {"metrics": "every endpoint's traffic is unknown"})))
    return found
