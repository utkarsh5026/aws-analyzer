"""
The plan dynamodb.py was split by (see split.py): each module, in layer order, and the statements it holds.
"""

MODULES = {
    "prices": ["DYNAMODB_PRICES"],
    "helpers": ["KB, MB, GB, TB", "ITEM_SIZE_LIMIT", "TYPE_NAMES", "_units", "_number", "_binary", "_raw_bytes",
                "from_dynamo", "from_dynamo_item", "_SERIALIZER", "_decimals", "to_dynamo", "dynamo_type",
                "_number_size", "value_size", "item_size", "read_units", "write_units", "_plain", "to_json",
                "format_value", "flatten_item", "_MISSING", "get_path"],
    "models": ["ReadStats", "IndexInfo", "TableInfo", "TableMetrics", "TableReport", "ItemPage", "_SIZED_TYPES",
               "DISTINCT_CAP", "_fingerprint", "AttributeProfile", "TableProfile", "ValueCounts", "items_table",
               "items_to_df"],
    "analysis": ["ITEM_SIZE_BANDS", "_KEY_SEPARATORS", "_SEGMENT_SHAPES", "_WORD_RE", "_segment_shape", "key_pattern",
                 "profile_items", "_hashable", "count_values", "_OPERATORS", "_ARITY", "_SORT_KEY_OPERATORS",
                 "_condition", "build_filter", "describe_condition", "describe_filter", "_PATH_PART_RE",
                 "_path_expression", "_expression_params", "parse_table", "table_monthly_cost", "request_cost",
                 "capacity_cost", "TARGET_USE", "_LARGE_ITEM_ADVICE", "_SECTIONS", "_table_ref", "profile_findings",
                 "_capacity_findings", "table_findings"],
    "analyzer": ["_FROM_RE", "DynamoDBAnalyzer"],
    "style": ["_ROOT", "_CSS", "_BADGE", "_render_html"],
    "view": ["_target", "_keys_label", "_capacity_label", "_billing_label", "_TABLE_CLASSES", "_STREAM_VIEWS",
             "_TTL_STATES", "_NO_MATCH", "_projection_label", "_query_hint", "_section", "_types_label",
             "_distinct_label", "_range_label", "_item_rows", "DynamoDBView"],
}

DOCS = {
    "prices": "DynamoDB's price list: on-demand requests, provisioned capacity per hour, storage per GB-month (both\n"
              "table classes) and point-in-time recovery. us-east-1 list prices; pass DynamoDBAnalyzer(prices={...})\n"
              "for your own.",
    "helpers": "Conversion and formatting: DynamoDB JSON to plain Python and back, the type names, item sizes and the\n"
               "capacity units they cost, JSON and one-line values, nested items flattened and read by path.",
    "models": "The dataclasses DynamoDBAnalyzer returns: a table, its indexes and metrics, a page of items, an\n"
              "attribute profile and value counts, and the items as a table or DataFrame.",
    "analysis": "Pure analysis of DynamoDB tables and items: no AWS calls, so it runs on any list of plain item dicts\n"
                "(a DynamoDB export, your own rows). Profiles and key patterns, filters and their expressions,\n"
                "parsing DescribeTable, costs and the findings.",
    "analyzer": "DynamoDBAnalyzer: talks to DynamoDB and CloudWatch, and returns the dataclasses in models.\n"
                "Never prints, and never writes to a table.",
    "style": "The look of DynamoDB reports: their CSS root class, stylesheet and badge.",
    "view": "DynamoDBView: the notebook UI, which renders what DynamoDBAnalyzer returns as cards and tables.",
}

REEXPORT_KIT = ["HOURS_PER_MONTH", "Stat", "human_age", "human_money", "human_size"]
