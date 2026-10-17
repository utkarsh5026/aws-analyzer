"""
What every service's analyzer shares: its boto3 session, the retry settings its clients use, its prices, and
helpers for clients made once, lists read page by page and work spread over threads.

A service's <Service>Analyzer subclasses _BaseAnalyzer: it sets _LIST_PRICES (its price table), passes session,
region, profile and prices to super().__init__() and makes its own clients, each with its service name spelled out
(self.session.client("s3", ...)) so rules.py can tell which AWS operations it calls.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import boto3
from botocore.config import Config


class _BaseAnalyzer:
    """
    What every <Service>Analyzer starts with: a boto3 session (session=, or one made from region= and profile=),
    the retry settings its clients use (self._config), and its prices, the service's list prices with prices= over
    them (a copy, so the module's price table never changes). Nothing here talks to AWS until the service asks.

    What the service sets:

        _LIST_PRICES  its price table (S3_PRICES, ...), which prices= overrides
        client        its main client
        _clients      the clients _cached_client made, by service name
        _lock         held while _cached_client makes a client
        max_workers   how many threads _map uses; 1 runs in order, as a Stubber answers
    """

    _LIST_PRICES: dict[str, Any] = {}
    client: Any
    _clients: dict[Any, Any]
    _lock: Any
    max_workers: int

    def __init__(self, session: Any = None, *, region: str | None = None, profile: str | None = None,
                 prices: dict[str, Any] | None = None):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        self.prices = {**self._LIST_PRICES, **(prices or {})}

    @property
    def region(self) -> str:
        return self.client.meta.region_name

    def _cached_client(self, service: str, make: Callable[[], Any]) -> Any:
        """
        The client for `service`, made by make() the first time it's needed (or the one passed in clients=). It's
        made under the lock: boto3 sessions aren't thread-safe, and the windows read from threads.
        """
        if service not in self._clients:
            with self._lock:
                if service not in self._clients:
                    self._clients[service] = make()
        return self._clients[service]

    def _paginate(self, operation: str, key: str, **params: Any) -> list[dict[str, Any]]:
        """
        Every item under `key`, from every page of one of the main client's list operations.
        """
        return [item for page in self.client.get_paginator(operation).paginate(**params) for item in page.get(key, [])]

    def _map(self, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        """
        fn over items, in parallel threads unless max_workers is 1 (a Stubber answers in order).
        """
        if self.max_workers <= 1 or len(items) <= 1:
            return [fn(item) for item in items]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(fn, items))
