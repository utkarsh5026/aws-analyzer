"""
S3's price list: storage per class, lifecycle transitions, and the minimum sizes and days S3 bills.
us-east-1 list prices; pass S3Analyzer(prices={...}) for your own.
"""

from __future__ import annotations

from .helpers import KB


ARCHIVE_CLASSES = {"GLACIER", "DEEP_ARCHIVE"}  # need a restore before GetObject works


# Storage price in USD per GB-month (GB = 2**30 bytes): us-east-1 list prices for the first 50 TB, checked
# against the AWS Price List API on 2026-09-27. Other regions and volume tiers differ; pass
# S3Analyzer(prices={...}) to use your own.
S3_PRICES: dict[str, float] = {
    "STANDARD": 0.023,
    "INTELLIGENT_TIERING": 0.023,  # frequent-access tier; listings don't say which tier an object is in
    "STANDARD_IA": 0.0125,
    "ONEZONE_IA": 0.01,
    "GLACIER_IR": 0.004,
    "GLACIER": 0.0036,
    "DEEP_ARCHIVE": 0.00099,
    "REDUCED_REDUNDANCY": 0.024,
    "EXPRESS_ONEZONE": 0.11,
    "STAGING": 0.021,  # not a class: multipart uploads to GLACIER / DEEP_ARCHIVE still in progress (flat rate)
}

# USD per 1,000 lifecycle transition requests into each class (us-east-1).
S3_TRANSITION_PRICES: dict[str, float] = {
    "INTELLIGENT_TIERING": 0.01,
    "STANDARD_IA": 0.01,
    "ONEZONE_IA": 0.01,
    "GLACIER_IR": 0.02,
    "GLACIER": 0.03,
    "DEEP_ARCHIVE": 0.05,
}

MIN_BILLABLE_SIZE = {
    "STANDARD_IA": 128 * KB,
    "ONEZONE_IA": 128 * KB,
    "GLACIER_IR": 128 * KB,
}

MIN_STORAGE_DAYS = {
    "STANDARD_IA": 30,
    "ONEZONE_IA": 30,
    "GLACIER_IR": 90,
    "GLACIER": 90,
    "DEEP_ARCHIVE": 180,
}

ARCHIVE_INDEX_BYTES = (
    32 * KB,
    8 * KB,
)  # per GLACIER / DEEP_ARCHIVE object: billed at its class's rate, at STANDARD's

MIN_TRANSITION_SIZE = (
    128 * KB
)  # lifecycle rules don't move smaller objects (S3 default since September 2024)

# Lifecycle transitions only go "down" this list (S3's waterfall; STANDARD_IA can move to
# INTELLIGENT_TIERING but not the other way round).
TRANSITION_ORDER = {
    "STANDARD": 0,
    "REDUCED_REDUNDANCY": 0,
    "STANDARD_IA": 1,
    "INTELLIGENT_TIERING": 2,
    "ONEZONE_IA": 3,
    "GLACIER_IR": 4,
    "GLACIER": 5,
    "DEEP_ARCHIVE": 6,
}
