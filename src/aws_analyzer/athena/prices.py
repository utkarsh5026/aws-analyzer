"""
Athena's price list: SQL queries are billed by the data they scan, at least 10 MB each, and the reports' own S3
listings (a table's folder) by the request. us-east-1 list prices, read 2026-11-07 from the AWS Price List API
(AmazonAthena, published 2026-09-11, and AmazonS3); pass AthenaAnalyzer(prices={...}) for your own.
"""

from __future__ import annotations


ATHENA_PRICES: dict[str, float] = {
    "scanned_tb": 5.00,
    "minimum_mb": 10,
    "s3_list_request": 0.000005,
}

PRICE_NOTES = {
    "scanned_tb": "SQL queries: per TB scanned, rounded up to the MB (successful and cancelled queries; failed ones "
                  "and DDL are free)",
    "minimum_mb": "SQL queries: the least a query is billed for, in MB",
    "s3_list_request": "S3 LIST requests the reports make to read a table's folder ($0.005 per 1,000)",
}
