"""
DynamoDB's price list: on-demand requests, provisioned capacity per hour, storage per GB-month (both
table classes) and point-in-time recovery. us-east-1 list prices; pass DynamoDBAnalyzer(prices={...})
for your own.
"""

from __future__ import annotations


# USD, us-east-1 list prices for the standard table class, before the free tier. Other regions
# differ; pass DynamoDBAnalyzer(prices={...}) to use your own.
DYNAMODB_PRICES: dict[str, float] = {
    "read_request": 0.125,  # per million on-demand read request units
    "write_request": 0.625,  # per million on-demand write request units
    "read_capacity_hour": 0.00013,  # per provisioned read capacity unit, per hour
    "write_capacity_hour": 0.00065,  # per provisioned write capacity unit, per hour
    "storage": 0.25,  # per GB-month of table and index data
    "storage_ia": 0.10,  # per GB-month, standard-infrequent-access table class
    "pitr": 0.20,  # per GB-month of point-in-time recovery
}
