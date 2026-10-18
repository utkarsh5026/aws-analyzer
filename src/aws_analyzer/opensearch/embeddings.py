"""
Turning a question into a vector with a Bedrock embedding model: each model family's request and
answer.
"""

from __future__ import annotations

import re
from typing import Any

from .prices import EMBEDDING_MODELS


def _as_vector(value: Any) -> list[float]:
    """
    A query vector from a list, tuple or numpy array -> list of floats.
    """
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]  # a batch of one, as embedding functions often return
    try:
        vector = [float(x) for x in value]
    except (TypeError, ValueError):
        raise ValueError("vector= takes a list of numbers (a list, tuple or numpy array)") from None
    if not vector:
        raise ValueError("The query vector is empty")
    return vector


def _base_model(model_id: str) -> str:
    """
    An inference profile's model: 'us.cohere.embed-v4:0' -> 'cohere.embed-v4:0'.
    """
    return re.sub(r"^(?:us|eu|apac|global|us-gov|ca|jp|au)\.", "", model_id)


def _embedding_style(model_id: str) -> str:
    """
    How to call a Bedrock embedding model, from its ID (inference profiles such as 'us.cohere.embed-v4:0' too).
    """
    known = EMBEDDING_MODELS.get(_base_model(model_id))
    if known:
        return known[0]
    for marker, style in (
        ("titan-embed-text-v2", "titan-v2"),
        ("titan-embed-text-v1", "titan"),
        ("titan-embed-image", "titan-image"),
        ("cohere.embed-v4", "cohere-v4"),
        ("cohere.embed", "cohere"),
    ):
        if marker in model_id:
            return style
    raise ValueError(
        f"Can't tell how to call {model_id!r}: this file knows Titan and Cohere embedding models. For another model, "
        "pass embed=your_function (text -> list of floats) or vector=[...]."
    )


def _embedding_request(style: str, text: str, dimension: int | None) -> dict[str, Any]:
    if style == "titan-v2":
        return {"inputText": text, **({"dimensions": dimension} if dimension else {})}
    if style == "titan-image":
        return {"inputText": text, **({"embeddingConfig": {"outputEmbeddingLength": dimension}} if dimension else {})}
    if style == "cohere-v4":
        extra = {"output_dimension": dimension} if dimension else {}
        return {"texts": [text], "input_type": "search_query", "embedding_types": ["float"], **extra}
    if style == "cohere":
        return {"texts": [text], "input_type": "search_query"}
    return {"inputText": text}


def _embedding_from(payload: dict[str, Any]) -> list[float]:
    """
    The vector in a Bedrock embedding answer (Titan's 'embedding', Cohere's 'embeddings', by type or not).
    """
    if isinstance(payload.get("embedding"), list):
        return payload["embedding"]
    found = payload.get("embeddings")
    if isinstance(found, dict):
        found = found.get("float")
    if isinstance(found, list) and found and isinstance(found[0], list):
        return found[0]
    by_type = payload.get("embeddingsByType")
    if isinstance(by_type, dict) and isinstance(by_type.get("float"), list):
        return by_type["float"]
    raise ValueError("The embedding model's answer holds no vector")
