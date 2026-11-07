"""
athena - Amazon Athena and the AWS Glue Data Catalog for SageMaker / Jupyter notebooks: what your SQL over S3 costs,
why queries fail, and the tables and crawlers to fix.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import AthenaView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich HTML output, ipywidgets for the
explorer window. All are preinstalled on SageMaker.

The package has two layers:

    AthenaAnalyzer  Pure logic. Talks to Athena (past queries, their saved results, workgroups), the Glue Data Catalog
                    (databases, tables, partitions, crawlers) and S3 (what a table's folder holds), and returns plain
                    Python data (dataclasses). Never prints.
    AthenaView      Notebook UI. Calls AthenaAnalyzer and renders readable cards and tables (HTML in Jupyter, plain
                    text in a terminal).

Read-only: running a query writes its results to S3, so nothing here runs one. Past queries and the results they
saved are read back; a new query is shown as the Python to run it yourself (query_code), with what it could cost.

Quick start
-----------
    athena = AthenaView()                          # or AthenaView(AthenaAnalyzer(region="eu-west-1"))
    athena.help()                                  # every command
    athena.overview()                              # what queries cost, which fail, and what to fix first
    athena.expensive()                             # the costliest queries, the same query's runs added up
    athena.failures()                              # why queries failed, and how to fix each kind
    athena.query("a1b2c3d4-...")                   # one query: its SQL, numbers and saved results
    athena.catalog()                               # every table, and whose S3 folder is empty or gone
    athena.table_info("sales.events")              # one table: partitions, size, its queries, SQL to copy
    athena.crawlers()                              # each crawler's schedule and last crawl
    athena.query_code("SELECT ...", database="sales")  # the Python to run a query yourself, and its most cost
    athena.explore()                               # a window to click through it all (Jupyter, ipywidgets)

    core = athena.core                             # the same analyzer, raw data
    history = core.history(since="7d")
    history.to_df()                                # every query as a DataFrame
    core.results("a1b2c3d4-...").to_df()           # a past query's saved results
"""

from __future__ import annotations

from .prices import ATHENA_PRICES
from .helpers import is_columnar, one_line, parse_table_ref, schedule_period, split_s3, storage_format
from .models import (
    Catalog, Column, Crawler, Database, FailureGroup, Location, Overview, Query, QueryDetail, QueryHistory,
    QueryResults, QueryShape, Table, TableReport, Workgroup,
)
from .analysis import (
    KNOWN_FAILURES, billed_bytes, catalog_findings, code_findings, costliest, crawler_findings, ctas_sql,
    date_column, detail_findings, did_not_finish, explain_failure, failure_kind, filters_on, group_failures,
    monthly, normalize_sql, overview_findings, pandas_code, parse_crawler, parse_query, parse_results, parse_table,
    parse_workgroup, per_month, queries_reading, query_cost, query_findings, query_shapes, repair_sql, run_code,
    sample_sql, scan_cost, selects_every_column, suggested_cutoff, table_findings, tables_in_sql, total_cost,
    workgroup_findings,
)
from .analyzer import AthenaAnalyzer
from .view import AthenaView
from .explorer.window import AthenaExplorer, explore

__all__ = [
    "ATHENA_PRICES",
    "AthenaAnalyzer",
    "AthenaExplorer",
    "AthenaView",
    "Catalog",
    "Column",
    "Crawler",
    "Database",
    "FailureGroup",
    "KNOWN_FAILURES",
    "Location",
    "Overview",
    "Query",
    "QueryDetail",
    "QueryHistory",
    "QueryResults",
    "QueryShape",
    "Table",
    "TableReport",
    "Workgroup",
    "billed_bytes",
    "catalog_findings",
    "code_findings",
    "costliest",
    "crawler_findings",
    "ctas_sql",
    "date_column",
    "detail_findings",
    "did_not_finish",
    "explain_failure",
    "explore",
    "failure_kind",
    "filters_on",
    "group_failures",
    "is_columnar",
    "monthly",
    "normalize_sql",
    "one_line",
    "overview_findings",
    "pandas_code",
    "parse_crawler",
    "parse_query",
    "parse_results",
    "parse_table",
    "parse_table_ref",
    "parse_workgroup",
    "per_month",
    "queries_reading",
    "query_cost",
    "query_findings",
    "query_shapes",
    "repair_sql",
    "run_code",
    "sample_sql",
    "scan_cost",
    "schedule_period",
    "selects_every_column",
    "split_s3",
    "storage_format",
    "suggested_cutoff",
    "table_findings",
    "tables_in_sql",
    "total_cost",
    "workgroup_findings",
]
