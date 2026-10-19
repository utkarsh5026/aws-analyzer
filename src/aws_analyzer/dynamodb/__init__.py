"""
dynamodb.py - DynamoDB analysis toolkit for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import DynamoDBView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    DynamoDBAnalyzer   Pure logic. Talks to AWS and returns plain Python data
                       (dataclasses, dicts, lists, DataFrames). Never prints.
    DynamoDBView       Notebook UI. Calls DynamoDBAnalyzer and renders readable
                       cards and tables (HTML in Jupyter, plain text in a terminal).

Items come back as plain Python rather than DynamoDB JSON: numbers are int or
float (not Decimal), sets are sets, binary is bytes, and maps and lists are dicts
and lists. Nothing in this file writes to a table.

Quick start
-----------
    ui = DynamoDBView()                               # or DynamoDBView(DynamoDBAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.tables()                                       # every table: keys, items, size, cost, warnings
    ui.table_info("orders")                           # keys, indexes and how to query each, capacity, backups
    ui.scan("orders")                                 # the first 20 items as a table...
    ui.more()                                         # ...and the next 20
    ui.schema("orders")                               # every attribute: types, fill rate, examples, key patterns
    ui.get("orders", "USER#42", "ORDER#0017")         # one item, nested maps and lists expanded
    ui.query("orders", "USER#42", sort=("begins_with", "ORDER#"))
    ui.scan("orders", where={"status": "failed", "total": (">", 100)})
    ui.value_counts("orders", "status")

    ddb = ui.core                                     # same analyzer, raw data
    df = ddb.scan("orders", n=5000).to_df()           # nested maps become 'address.city' columns
    items = ddb.query("orders", "USER#42").items      # list of plain dicts
"""

from __future__ import annotations

from .._kit.fmt import HOURS_PER_MONTH, Stat, human_age, human_money, human_size
from .prices import DYNAMODB_PRICES
from .helpers import (
    GB, ITEM_SIZE_LIMIT, KB, MB, TB, TYPE_NAMES, dynamo_type, flatten_item, format_value, from_dynamo,
    from_dynamo_item, get_path, item_size, read_units, to_dynamo, to_json, value_size, write_units,
)
from .models import (
    DISTINCT_CAP, AttributeProfile, IndexInfo, ItemPage, ReadStats, TableInfo, TableMetrics, TableProfile,
    TableReport, ValueCounts, items_table, items_to_df,
)
from .analysis import (
    ITEM_SIZE_BANDS, TARGET_USE, build_filter, capacity_cost, count_values, describe_condition, describe_filter,
    key_pattern, parse_table, profile_findings, profile_items, request_cost, table_findings, table_monthly_cost,
)
from .analyzer import DynamoDBAnalyzer
from .view import DynamoDBView

__all__ = [
    "DISTINCT_CAP",
    "DYNAMODB_PRICES",
    "GB",
    "HOURS_PER_MONTH",
    "ITEM_SIZE_BANDS",
    "ITEM_SIZE_LIMIT",
    "KB",
    "MB",
    "TARGET_USE",
    "TB",
    "TYPE_NAMES",
    "AttributeProfile",
    "DynamoDBAnalyzer",
    "DynamoDBView",
    "IndexInfo",
    "ItemPage",
    "ReadStats",
    "Stat",
    "TableInfo",
    "TableMetrics",
    "TableProfile",
    "TableReport",
    "ValueCounts",
    "build_filter",
    "capacity_cost",
    "count_values",
    "describe_condition",
    "describe_filter",
    "dynamo_type",
    "flatten_item",
    "format_value",
    "from_dynamo",
    "from_dynamo_item",
    "get_path",
    "human_age",
    "human_money",
    "human_size",
    "item_size",
    "items_table",
    "items_to_df",
    "key_pattern",
    "parse_table",
    "profile_findings",
    "profile_items",
    "read_units",
    "request_cost",
    "table_findings",
    "table_monthly_cost",
    "to_dynamo",
    "to_json",
    "value_size",
    "write_units",
]
