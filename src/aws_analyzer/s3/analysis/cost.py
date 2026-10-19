"""
What objects cost a month to store, from their size and storage class or from CloudWatch's
numbers.
"""

from __future__ import annotations

from ..helpers import GB
from ..prices import ARCHIVE_CLASSES, ARCHIVE_INDEX_BYTES, MIN_BILLABLE_SIZE, S3_PRICES


def object_monthly_cost(
    size: int, storage_class: str, prices: dict[str, float] | None = None
) -> float | None:
    """
    Estimated USD per month to store one object, with S3's billing minimums: STANDARD_IA,
    ONEZONE_IA and GLACIER_IR bill at least 128 KB per object; GLACIER and DEEP_ARCHIVE add 40 KB
    of index data per object (32 KB at the archive rate, 8 KB at the STANDARD rate).
    None when `prices` has no price for the class. Storage only: no requests or data transfer.
    """
    prices = S3_PRICES if prices is None else prices
    price = prices.get(storage_class)
    if price is None:
        return None
    billed = max(size, MIN_BILLABLE_SIZE.get(storage_class, 0))
    if storage_class in ARCHIVE_CLASSES:
        at_class, at_standard = ARCHIVE_INDEX_BYTES
        return (
            (billed + at_class) * price + at_standard * prices.get("STANDARD", 0.0)
        ) / GB
    return billed * price / GB


_STORAGE_TYPE_PREFIXES = [  # CloudWatch StorageType prefix -> class whose price applies; longest match first
    ("IntelligentTieringFA", "INTELLIGENT_TIERING"),
    (
        "IntelligentTieringIA",
        "STANDARD_IA",
    ),  # each Intelligent-Tiering tier costs the same as this class
    ("IntelligentTieringAIA", "GLACIER_IR"),
    ("IntelligentTieringAA", "GLACIER"),
    ("IntelligentTieringDAA", "DEEP_ARCHIVE"),
    ("StandardIA", "STANDARD_IA"),
    ("Standard", "STANDARD"),
    ("OneZoneIA", "ONEZONE_IA"),
    ("ReducedRedundancy", "REDUCED_REDUNDANCY"),
    ("GlacierInstantRetrieval", "GLACIER_IR"),
    ("Glacier", "GLACIER"),
    ("DeepArchive", "DEEP_ARCHIVE"),
    ("ExpressOneZone", "EXPRESS_ONEZONE"),
]


def storage_type_class(storage_type: str) -> str | None:
    """
    CloudWatch StorageType ('StandardIAStorage', 'GlacierObjectOverhead', ...) -> the storage
    class whose price applies to it ('STAGING' for archive uploads in progress), or None if unknown.
    """
    if "S3ObjectOverhead" in storage_type:
        return "STANDARD"  # the 8 KB of archive index data per object is billed as STANDARD
    if "Staging" in storage_type:
        return "STAGING"
    return next(
        (
            cls
            for prefix, cls in _STORAGE_TYPE_PREFIXES
            if storage_type.startswith(prefix)
        ),
        None,
    )


def cloudwatch_cost(
    size_by_storage_type: dict[str, int], prices: dict[str, float] | None = None
) -> dict[str, float | None]:
    """
    BucketMetrics.size_by_storage_type -> estimated USD per month per storage type (None = no price).
    CloudWatch already reports minimum-size and archive overheads as their own storage types.
    """
    prices = S3_PRICES if prices is None else prices
    costs: dict[str, float | None] = {}
    for storage_type, size in size_by_storage_type.items():
        price = prices.get(storage_type_class(storage_type) or "")
        costs[storage_type] = None if price is None else size * price / GB
    return costs
