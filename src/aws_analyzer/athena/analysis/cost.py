"""
What past queries cost: each query's billed bytes (rounded up to the MB, at least 10 MB, nothing for a failed query,
DDL or a reused result) at the price per TB, and the queries grouped by shape so a query that runs again and again
shows what it costs in all.
"""

from __future__ import annotations

from typing import Any

from ..helpers import MB, TB
from ..models import Query, QueryShape
from .sql import normalize_sql, selects_every_column


_BILLED_STATES = ("SUCCEEDED", "CANCELLED")

_FREE_STATEMENTS = ("DDL", "UTILITY")

DAYS_PER_MONTH = 30


def billed_bytes(query: Query, prices: dict[str, Any]) -> int:
    """
    The bytes a query is billed for: what it scanned rounded up to the MB, at least prices['minimum_mb'] MB; 0 for
    a failed query, DDL (CREATE, ALTER, MSCK), SHOW / DESCRIBE, or a result reused from an earlier run.
    """
    is_free = (query.state not in _BILLED_STATES or query.statement_type in _FREE_STATEMENTS or query.reused)
    if is_free:
        return 0
    rounded = -(-query.scanned_bytes // MB) * MB
    return max(rounded, int(prices.get("minimum_mb", 10)) * MB)


def query_cost(query: Query, prices: dict[str, Any]) -> float:
    """
    What a query cost in USD: its billed bytes at prices['scanned_tb'] per TB.
    """
    return billed_bytes(query, prices) / TB * float(prices.get("scanned_tb", 5.0))


def total_cost(queries: list[Query], prices: dict[str, Any]) -> float:
    """
    What the queries cost together, in USD.
    """
    return sum(query_cost(q, prices) for q in queries)


def scan_cost(size: int, prices: dict[str, Any]) -> float:
    """
    What a query that reads `size` bytes would cost, in USD.
    """
    return max(size, int(prices.get("minimum_mb", 10)) * MB) / TB * float(prices.get("scanned_tb", 5.0))


def per_month(cost: float, days: float) -> float:
    """
    A cost over `days` days, scaled to a 30-day month.
    """
    return cost / days * DAYS_PER_MONTH if days > 0 else 0.0


def query_shapes(queries: list[Query], prices: dict[str, Any]) -> list[QueryShape]:
    """
    The queries grouped by shape (the same SQL but for its values), the most expensive first.
    """
    shapes: dict[str, QueryShape] = {}
    for query in queries:
        key = normalize_sql(query.sql)
        shape = shapes.setdefault(key, QueryShape(sql=query.sql, select_star=selects_every_column(query.sql)))
        shape.runs += 1
        shape.failed += query.failed
        shape.scanned_bytes += query.scanned_bytes
        shape.cost += query_cost(query, prices)
        shape.workgroups.add(query.workgroup)
        is_newer = shape.last is None or (query.submitted is not None and query.submitted > shape.last)
        if is_newer:
            shape.last, shape.last_id, shape.sql = query.submitted, query.id, query.sql
    return sorted(shapes.values(), key=lambda s: (-s.cost, -s.runs))


def costliest(queries: list[Query], prices: dict[str, Any], top: int = 10) -> list[Query]:
    """
    The `top` single runs that cost the most.
    """
    billed = [q for q in queries if billed_bytes(q, prices)]
    return sorted(billed, key=lambda q: -query_cost(q, prices))[:top]
