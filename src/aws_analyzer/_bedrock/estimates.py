"""
Token and cost estimates: a text's tokens, what searches cost (embedding and reranking) and what an
answer costs at its model's price.
"""

from __future__ import annotations

import math

from .prices import BEDROCK_PRICES, _RERANK_ALIASES, model_price


def estimate_tokens(text: str | None) -> int:
    """
    Roughly how many tokens `text` is: one per 4 characters. An estimate: label it as one wherever it's shown.
    """
    return math.ceil(len(text or "") / 4)


def query_cost(
    n_queries: int,
    rerank: bool | str = False,
    prices: dict[str, float] | None = None,
    *,
    question_tokens: int = 20,
) -> float:
    """
    Estimated USD for n searches: embedding each question (about question_tokens tokens) and, with `rerank`, the
    reranking model: True or 'cohere' for Cohere Rerank 3.5, 'amazon' or 'amazon.rerank-v1:0' (or its ARN) for
    Amazon Rerank. The vector store's own charges aren't included.
    """
    prices = BEDROCK_PRICES if prices is None else prices
    per_query = question_tokens * prices["embedding_per_million_tokens"] / 1e6
    if rerank:
        model = _RERANK_ALIASES.get(str(rerank).lower(), str(rerank))
        key = (
            "amazon_rerank_per_1k_queries"
            if "amazon.rerank" in model
            else "rerank_per_1k_queries"
        )
        per_query += prices[key] / 1000
    return n_queries * per_query


def generation_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """
    Estimated USD for one answer; None when the model isn't in the price table (pass model_prices=...).
    """
    price = model_price(model, model_prices)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1e6
