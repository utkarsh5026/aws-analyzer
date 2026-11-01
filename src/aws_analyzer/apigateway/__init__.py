"""
apigateway - Amazon API Gateway analysis for SageMaker / Jupyter notebooks: REST, HTTP and WebSocket APIs.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import APIGatewayView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich HTML output, ipywidgets for the
explorer window. All are preinstalled on SageMaker.

The package has two layers:

    APIGatewayAnalyzer  Pure logic. Talks to API Gateway (apigateway for REST APIs, apigatewayv2 for HTTP and
                        WebSocket ones), CloudWatch and CloudWatch Logs, and returns plain Python data (dataclasses).
                        Never prints.
    APIGatewayView      Notebook UI. Calls APIGatewayAnalyzer and renders readable cards and tables (HTML in Jupyter,
                        plain text in a terminal).

A call's own record is in its stage's access log (CloudWatch keeps only counts), read by the stage's own log format.
For a call that reached a Lambda function, its run's lines are read through aws_analyzer.lambda_functions, found by
the Lambda request ID the access log recorded. Nothing here sends a request to an API or changes one: findings show
the command instead.

Quick start
-----------
    apigw = APIGatewayView()                       # or APIGatewayView(APIGatewayAnalyzer(region="eu-west-1"))
    apigw.help()                                   # every command
    apigw.apis()                                   # every API: requests, errors, latency, who can call it, cost
    apigw.api_info("orders-api")                   # its stages and routes, and what needs attention
    apigw.routes("partner-api")                    # who can call each route, where it goes, and curl to try it
    apigw.errors("orders-api")                     # which calls fail and why, with the Lambda run to read
    apigw.calls("orders-api", status="5xx")        # the access log, call by call
    apigw.performance("orders-api")                # which routes are slow, and where the time goes
    apigw.explore("orders-api")                    # a window to click through it all (Jupyter, ipywidgets)

    core = apigw.core                              # the same analyzer, raw data
    page = core.calls("orders-api/prod", since="1h")
    page.to_df()                                   # every call as a DataFrame
"""

from __future__ import annotations

from .prices import APIGATEWAY_PRICES
from .helpers import invoke_url, lambda_of_uri, parse_api_ref, service_action
from .models import (
    Api, ApiNumbers, ApiReport, Authorizer, Call, CallPage, ErrorGroup, ErrorReport, Integration, LambdaRun, Overview,
    Route, RouteNumbers, RunLine, Stage,
)
from .analysis import (
    LogFormat, access_log_command, api_findings, cache_monthly, curl_command, curl_lines, delete_command, error_findings,
    format_findings, group_errors, idle_findings, latency_split, missing_fields, monthly_cost, monthly_requests,
    numbers_findings, open_route_findings, overview_findings, parse_access_line, parse_rest_api,
    parse_rest_authorizer, parse_rest_integration, parse_rest_routes, parse_rest_stage, parse_v2_api,
    parse_v2_authorizer, parse_v2_integration, parse_v2_routes, parse_v2_stage, read_format, request_price,
    route_findings, route_numbers_from_calls, route_path, stage_findings, suggested_format,
)
from .analyzer import APIGatewayAnalyzer
from .view import APIGatewayView
from .explorer.window import APIGatewayExplorer, explore

__all__ = [
    "APIGATEWAY_PRICES",
    "APIGatewayAnalyzer",
    "APIGatewayExplorer",
    "APIGatewayView",
    "Api",
    "ApiNumbers",
    "ApiReport",
    "Authorizer",
    "Call",
    "CallPage",
    "ErrorGroup",
    "ErrorReport",
    "Integration",
    "LambdaRun",
    "LogFormat",
    "Overview",
    "Route",
    "RouteNumbers",
    "RunLine",
    "Stage",
    "access_log_command",
    "api_findings",
    "cache_monthly",
    "curl_command",
    "curl_lines",
    "delete_command",
    "error_findings",
    "explore",
    "format_findings",
    "group_errors",
    "idle_findings",
    "invoke_url",
    "lambda_of_uri",
    "latency_split",
    "missing_fields",
    "monthly_cost",
    "monthly_requests",
    "numbers_findings",
    "open_route_findings",
    "overview_findings",
    "parse_access_line",
    "parse_api_ref",
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
    "service_action",
    "stage_findings",
    "suggested_format",
]
