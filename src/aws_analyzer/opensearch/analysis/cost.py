"""
What a domain or a Serverless collection costs a month, at the prices given.
"""

from __future__ import annotations

from typing import Iterable

from ..._kit.fmt import HOURS_PER_MONTH

from ..prices import OPENSEARCH_PRICES, SERVERLESS_MIN_OCUS
from ..models import Collection, Domain


def domain_monthly_cost(domain: Domain, prices: dict[str, float] | None = None) -> dict[str, float | None]:
    """
    Estimated USD per month: 'data nodes', 'master nodes' and 'UltraWarm nodes' (instance-hours at list price) and
    'storage' (every data node's EBS volume). None where a price isn't known (pass it in prices). IOPS and throughput
    above gp3's baseline, UltraWarm storage and data transfer aren't included.
    """
    prices = OPENSEARCH_PRICES if prices is None else prices

    def nodes(kind: str | None, count: int) -> float | None:
        price = prices.get(kind or "")
        return None if price is None else price * count * HOURS_PER_MONTH

    cost: dict[str, float | None] = {"data nodes": nodes(domain.instance_type, domain.instance_count)}
    if domain.master_type and domain.master_count:
        cost["master nodes"] = nodes(domain.master_type, domain.master_count)
    if domain.warm_type and domain.warm_count:
        cost["UltraWarm nodes"] = nodes(domain.warm_type, domain.warm_count)
    if domain.volume_type and domain.volume_gb:
        rate = prices.get(domain.volume_type)
        cost["storage"] = None if rate is None else rate * domain.volume_gb * domain.instance_count
    return cost


def _key_label(key: str | None) -> str:
    return "AWS owned key" if not key or key == "AWS owned key" else f"key {key.rsplit('/', 1)[-1]}"


def serverless_minimum(collections: Iterable[Collection]) -> list[tuple[str, float, list[str]]]:
    """
    The OCUs OpenSearch Serverless bills even when idle -> [(group, OCUs, collection names)], one entry for each
    group of collections that share OCUs: same encryption key, same kind (vector search or not) and same standby
    setting. Collections being deleted or that failed to create aren't counted.
    """
    groups: dict[tuple[bool, str, bool], list[str]] = {}
    for c in collections:
        if c.status in ("DELETING", "FAILED"):
            continue
        groups.setdefault((c.vector, c.kms_key or "AWS owned key", c.standby), []).append(c.name)
    return [
        (
            f"{'vector search' if vector else 'search / time series'} · {_key_label(key)} · "
            f"standby replicas {'on' if standby else 'off'}",
            SERVERLESS_MIN_OCUS[standby],
            sorted(names),
        )
        for (vector, key, standby), names in sorted(groups.items())
    ]


def ocu_monthly_cost(ocus: float, prices: dict[str, float] | None = None) -> float:
    """
    USD per month for `ocus` OpenSearch Compute Units running all month.
    """
    prices = OPENSEARCH_PRICES if prices is None else prices
    return ocus * prices["ocu_hour"] * HOURS_PER_MONTH
