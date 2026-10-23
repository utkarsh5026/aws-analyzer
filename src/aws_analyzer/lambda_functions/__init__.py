"""
lambda_functions.py - AWS Lambda toolkit for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import LambdaView``. It isn't called
lambda.py because `lambda` is a Python keyword, so `from aws_analyzer import lambda` can't work.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    LambdaAnalyzer   Pure logic. Talks to AWS and returns plain Python data
                     (dataclasses, dicts, lists, DataFrames). Never prints.
    LambdaView       Notebook UI. Calls LambdaAnalyzer and renders readable
                     cards and tables (HTML in Jupyter, plain text in a terminal).

and a window on top of them, LambdaExplorer (explore()): every function to click
through, and for the one you pick its logs run by run, errors, run times, code and
settings (ipywidgets, which SageMaker notebooks have).

Functions and their settings come from Lambda, how often they ran, failed and were
throttled from CloudWatch, and what they logged from CloudWatch Logs. Nothing in this
file invokes, changes or deletes a function: where a change would help, the report
shows the command to run instead. Environment variable values are never shown.

Quick start
-----------
    ui = LambdaView()                                 # or LambdaView(LambdaAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.functions()                                    # every function: runtime, triggers, use, errors, cost, warnings
    ui.functions(regions="all")                       # ...in every region your account has turned on
    ui.function_info("etl-nightly")                   # one function in plain English: triggers, access, cost, activity
    ui.errors("etl-nightly")                          # its errors in the last 24 hours, grouped by cause
    ui.logs("etl-nightly", search="KeyError")         # the newest lines it logged
    ui.performance("etl-nightly")                     # run times, memory used, cold starts, and the memory it needs
    ui.code("etl-nightly")                            # the files in its package, and the handler's source
    ui.diff("etl-nightly")                            # what changed since its last published version: files, lines, settings
    ui.how_it_works("etl-nightly")                    # how its Python code works, step by step from the handler
    ui.how_it_works_page("etl-nightly")               # the same as a page of its own, to open in a browser tab
    ui.download("etl-nightly")                        # its package (.zip) saved in lambda-downloads/, next to the notebook
    ui.explore()                                      # all of it in a window, by clicking: functions, logs, errors

    from aws_analyzer.lambda_functions import explore
    explore("etl-nightly", tab="logs")                # the window, straight to one function's logs

    lam = ui.core                                     # same analyzer, raw data
    lam.search_code("etl-nightly", "customer_id")     # CodeSearch: the lines of its code that contain it, like grep
    lam.find_symbol("etl-nightly", "load_orders")     # SymbolReport: where it's defined and used, like a language server
    df = lam.overview(regions="all").to_df()          # one row per function
    detail = lam.describe("etl-nightly")              # FunctionDetail: .function, .triggers, .aliases, .metrics
    runs = lam.log_runs("etl-nightly").runs           # LogRun per call: its lines, status, run time, memory
"""

from __future__ import annotations

from .._kit.fmt import HOURS_PER_MONTH, human_age, human_money, human_size, parse_size, parse_time
from .._kit.text import search_rank
from .prices import DAYS_PER_MONTH, LAMBDA_PRICES
from .helpers import (
    CODE_STORAGE_LIMIT, ENDING_SOON_DAYS, GB, KB, LATEST_RUNTIMES, MAX_TIMEOUT, MB, MEMORY_PER_VCPU, MEMORY_STEPS,
    RUNTIMES, TB, UNZIPPED_LIMIT, human_ms, parse_function_ref, parse_qualifier, runtime_family, stream_version,
)
from .models import (
    AccountLimits, Alias, AsyncConfig, CodeFile, CodeMatch, CodePackage, CodeSearch, CodeSymbol, DailyUsage, ErrorGroup,
    ErrorReport, FileChange, Function, FunctionDetail, FunctionMetrics, FunctionUrl, Invocation, Layer, LibraryChange,
    LogEvent, LogGroup, PackageDownload, Performance, ProvisionedConcurrency, RuntimeStatus, SavedPage, SettingChange,
    SymbolReport, SymbolUse, Trigger, Version, VersionDiff,
)
from .analysis.functions import (
    masked_config, parse_event_source_mapping, parse_function, parse_policy, parse_version, runtime_status,
)
from .analysis.cost import function_monthly_cost, log_storage_monthly_cost, provisioned_monthly_cost
from .analysis.overview import Overview
from .analysis.findings import RECENT_DAYS, account_findings, function_findings, secret_like
from .analysis.errors import classify_error, error_findings, group_errors, request_id_of
from .analysis.logs import LogPage, LogRun, line_level, parse_report, split_runs
from .analysis.performance import percentile, performance_findings, suggest_memory
from .analysis.package import (
    DOWNLOADS, handler_file, package_file_name, package_findings, page_file_name, read_package,
)
from .analysis.source import find_symbol_in, search_package, source_files
from .analysis.versions import diff_findings, diff_packages, diff_settings, version_before
from .analysis.codemap.model import AwsCall, CodeFunction, CodeMap, CodeSpot, CodeStep, EnvRead, EventField, ImportUse
from .analysis.codemap.mapping import map_code, map_package
from .analysis.codemap.findings import code_map_findings
from .analysis.codemap.graph import CodeGraph, GraphEdge, GraphNode, code_graph
from .analyzer import LambdaAnalyzer
from .view import LambdaView
from .explorer.window import LambdaExplorer, explore

__all__ = [
    "CODE_STORAGE_LIMIT",
    "DAYS_PER_MONTH",
    "DOWNLOADS",
    "ENDING_SOON_DAYS",
    "GB",
    "HOURS_PER_MONTH",
    "KB",
    "LAMBDA_PRICES",
    "LATEST_RUNTIMES",
    "MAX_TIMEOUT",
    "MB",
    "MEMORY_PER_VCPU",
    "MEMORY_STEPS",
    "RECENT_DAYS",
    "RUNTIMES",
    "TB",
    "UNZIPPED_LIMIT",
    "AccountLimits",
    "Alias",
    "AsyncConfig",
    "AwsCall",
    "CodeFile",
    "CodeMatch",
    "CodeSearch",
    "CodeSymbol",
    "CodeFunction",
    "CodeGraph",
    "CodeMap",
    "CodePackage",
    "CodeSpot",
    "CodeStep",
    "DailyUsage",
    "EnvRead",
    "ErrorGroup",
    "ErrorReport",
    "EventField",
    "FileChange",
    "Function",
    "FunctionDetail",
    "FunctionMetrics",
    "FunctionUrl",
    "GraphEdge",
    "GraphNode",
    "ImportUse",
    "Invocation",
    "LambdaAnalyzer",
    "LambdaExplorer",
    "LambdaView",
    "Layer",
    "LibraryChange",
    "LogEvent",
    "LogGroup",
    "LogPage",
    "LogRun",
    "Overview",
    "PackageDownload",
    "Performance",
    "ProvisionedConcurrency",
    "RuntimeStatus",
    "SavedPage",
    "SettingChange",
    "SymbolReport",
    "SymbolUse",
    "Trigger",
    "Version",
    "VersionDiff",
    "account_findings",
    "classify_error",
    "code_graph",
    "code_map_findings",
    "diff_findings",
    "diff_packages",
    "diff_settings",
    "error_findings",
    "explore",
    "function_findings",
    "function_monthly_cost",
    "group_errors",
    "handler_file",
    "human_age",
    "human_money",
    "human_ms",
    "human_size",
    "line_level",
    "log_storage_monthly_cost",
    "map_code",
    "map_package",
    "find_symbol_in",
    "search_package",
    "source_files",
    "masked_config",
    "package_file_name",
    "package_findings",
    "page_file_name",
    "parse_event_source_mapping",
    "parse_function",
    "parse_function_ref",
    "parse_policy",
    "parse_qualifier",
    "parse_report",
    "parse_size",
    "parse_time",
    "parse_version",
    "percentile",
    "performance_findings",
    "provisioned_monthly_cost",
    "read_package",
    "request_id_of",
    "runtime_family",
    "runtime_status",
    "search_rank",
    "secret_like",
    "split_runs",
    "stream_version",
    "suggest_memory",
    "version_before",
]
