"""
What an API costs a month, estimated from its CloudWatch numbers at the list prices: its requests (WebSocket: its
messages) at the price of its kind, plus an hourly cache on each stage that has one.
"""

from __future__ import annotations

from ..._kit.fmt import HOURS_PER_MONTH
from ..models import Api, ApiNumbers
from ..prices import DAYS_PER_MONTH


REQUEST_PRICE_KEYS = {"REST": "rest_request", "HTTP": "http_request", "WEBSOCKET": "websocket_message"}

CACHE_SIZES = ("0.5", "1.6", "6.1", "13.5", "28.4", "58.2", "118", "237")


def request_price(kind: str, prices: dict[str, float]) -> float:
    """
    The price of one request (WebSocket: one message) for an API of this kind.
    """
    return prices[REQUEST_PRICE_KEYS.get(kind, "rest_request")]


def cache_hourly(size: str, prices: dict[str, float]) -> float | None:
    """
    The hourly price of a cache of `size` GB ('0.5', '1.6', ... '237'), or None for a size not in the table.
    """
    key = f"cache_{size}"
    return prices.get(key)


def cache_monthly(api: Api, prices: dict[str, float]) -> float:
    """
    What the caches on an API's stages cost a month (a cache bills every hour it's on, used or not).
    """
    total = 0.0
    for stage in api.stages:
        hourly = cache_hourly(stage.cache_size, prices) if stage.cache_enabled else None
        total += (hourly or 0.0) * HOURS_PER_MONTH
    return total


def monthly_requests(numbers: ApiNumbers) -> float:
    """
    Requests a month at the rate of the days read.
    """
    return numbers.requests / max(1, numbers.days) * DAYS_PER_MONTH


def monthly_cost(api: Api, numbers: ApiNumbers | None, prices: dict[str, float]) -> float | None:
    """
    An API's estimated monthly bill: its requests at the rate of the days read, at its kind's price (the first tier),
    plus its caches; None when its numbers weren't read and it has no cache.
    """
    caches = cache_monthly(api, prices)
    if numbers is None:
        return caches or None
    return monthly_requests(numbers) * request_price(api.kind, prices) + caches
