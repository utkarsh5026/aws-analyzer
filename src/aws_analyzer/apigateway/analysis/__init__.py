"""
API Gateway's pure analysis, no AWS calls: reading what the two clients return (parsing), reading access log lines
by their stage's format (access_log), the monthly cost (cost) and the findings (findings). Public: run them on data
you have.
"""

from __future__ import annotations

from .parsing import (
    parse_rest_api, parse_rest_authorizer, parse_rest_integration, parse_rest_routes, parse_rest_stage, parse_v2_api,
    parse_v2_authorizer, parse_v2_integration, parse_v2_routes, parse_v2_stage,
)
from .access_log import (
    LogFormat, group_errors, latency_split, missing_fields, parse_access_line, read_format, route_numbers_from_calls,
    suggested_format,
)
from .cost import cache_monthly, monthly_cost, monthly_requests, request_price
from .try_it import curl_command, curl_lines, route_path
from .findings import (
    access_log_command, api_findings, delete_command, error_findings, format_findings, idle_findings,
    numbers_findings, open_route_findings, overview_findings, route_findings, stage_findings,
)

__all__ = [
    "LogFormat",
    "access_log_command",
    "api_findings",
    "cache_monthly",
    "curl_command",
    "curl_lines",
    "delete_command",
    "error_findings",
    "format_findings",
    "group_errors",
    "idle_findings",
    "latency_split",
    "missing_fields",
    "monthly_cost",
    "monthly_requests",
    "numbers_findings",
    "open_route_findings",
    "overview_findings",
    "parse_access_line",
    "parse_rest_api",
    "parse_rest_authorizer",
    "parse_rest_integration",
    "parse_rest_routes",
    "parse_rest_stage",
    "parse_v2_api",
    "parse_v2_authorizer",
    "parse_v2_integration",
    "parse_v2_routes",
    "parse_v2_stage",
    "read_format",
    "request_price",
    "route_findings",
    "route_numbers_from_calls",
    "route_path",
    "stage_findings",
    "suggested_format",
]
