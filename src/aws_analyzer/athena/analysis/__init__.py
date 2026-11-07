"""
Athena's and the Glue Data Catalog's pure analysis, no AWS calls: reading what the two clients return (parsing),
what queries cost (cost), reading SQL and writing SQL and Python to copy (sql), why queries fail (failures) and the
findings (findings). Public: run them on data you have.
"""

from __future__ import annotations

from .parsing import parse_crawler, parse_query, parse_results, parse_table, parse_workgroup
from .cost import billed_bytes, costliest, per_month, query_cost, query_shapes, scan_cost, total_cost
from .sql import (
    ctas_sql, date_column, filters_on, normalize_sql, pandas_code, repair_sql, run_code, sample_sql,
    selects_every_column, tables_in_sql,
)
from .failures import KNOWN_FAILURES, did_not_finish, explain_failure, failure_kind, group_failures
from .findings import (
    catalog_findings, code_findings, crawler_findings, detail_findings, monthly, overview_findings,
    queries_reading, query_findings, suggested_cutoff, table_findings, workgroup_findings,
)

__all__ = [
    "KNOWN_FAILURES",
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
    "failure_kind",
    "filters_on",
    "group_failures",
    "monthly",
    "normalize_sql",
    "overview_findings",
    "pandas_code",
    "parse_crawler",
    "parse_query",
    "parse_results",
    "parse_table",
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
    "selects_every_column",
    "suggested_cutoff",
    "table_findings",
    "tables_in_sql",
    "total_cost",
    "workgroup_findings",
]
