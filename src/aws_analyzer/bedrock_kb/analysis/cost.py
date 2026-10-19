"""
What a knowledge base's vector store costs when idle.
"""

from __future__ import annotations

from ..._bedrock.prices import BEDROCK_PRICES
from ..._kit.fmt import HOURS_PER_MONTH, human_money

from ..models import KnowledgeBaseInfo
from .settings import _STORE_BILLED_BY


# ----------------------------------------------------------------------- cost


def vector_store_monthly_cost(
    kb: KnowledgeBaseInfo, prices: dict[str, float] | None = None
) -> float | None:
    """
    Estimated USD per month the vector store costs even with no traffic. Only OpenSearch Serverless is estimated
    (its minimum OCUs, around the clock); other stores are billed by their own service, and this returns None.
    """
    prices = BEDROCK_PRICES if prices is None else prices
    if kb.vector_store != "OPENSEARCH_SERVERLESS":
        return None
    return (
        prices["opensearch_ocu_hour"] * prices["opensearch_min_ocus"] * HOURS_PER_MONTH
    )


def idle_cost_label(
    kb: KnowledgeBaseInfo, prices: dict[str, float] | None = None
) -> str:
    """
    '$350.40' for OpenSearch Serverless, 'billed by Pinecone, not estimated' for the others.
    """
    cost = vector_store_monthly_cost(kb, prices)
    if cost is not None:
        return human_money(cost)
    return (
        f"billed by {_STORE_BILLED_BY[kb.vector_store]}, not estimated"
        if kb.vector_store in _STORE_BILLED_BY
        else "-"
    )
