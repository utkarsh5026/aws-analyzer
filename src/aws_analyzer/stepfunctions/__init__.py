"""
stepfunctions - AWS Step Functions analysis for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import StepFunctionsView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich HTML output. All are preinstalled
on SageMaker.

The package has two layers:

    StepFunctionsAnalyzer  Pure logic. Talks to Step Functions and CloudWatch and returns plain Python data
                           (dataclasses). Never prints.
    StepFunctionsView      Notebook UI. Calls StepFunctionsAnalyzer and renders readable cards and tables (HTML in
                           Jupyter, plain text in a terminal).

For a step that called Lambda, an execution's report shows that run's own log lines, found by the request ID the
execution's history records, read through aws_analyzer.lambda_functions. Nothing here starts, stops or redrives an
execution: findings show the command instead.

Quick start
-----------
    sfn = StepFunctionsView()                      # or StepFunctionsView(StepFunctionsAnalyzer(region="eu-west-1"))
    sfn.help()                                     # every command
    sfn.overview()                                 # every state machine: executions, failures, cost, warnings
    sfn.state_machine("order-pipeline")            # its numbers, what its definition risks, every state
    sfn.failures("order-pipeline")                 # where and why it fails, with the Lambda run to read
    sfn.execution("order-pipeline/ord-7f3a")       # one execution step by step, and the Lambda lines that broke it
    sfn.executions("order-pipeline", status="failed")
    sfn.graph("order-pipeline", execution="ord-7f3a")  # the flow drawn, coloured by the path one execution took
    sfn.explore("order-pipeline")                  # a window to click through it all (Jupyter, ipywidgets)

    core = sfn.core                                # the same analyzer, raw data
    detail = core.execution("order-pipeline/ord-7f3a")
    detail.failed_step.request_id                  # the Lambda run behind the failure
"""

from __future__ import annotations

from .prices import STEPFUNCTIONS_PRICES
from .helpers import execution_name, lambda_name, machine_name, parse_execution_ref, short_cause
from .models import (
    Attempt, Execution, ExecutionDetail, ExecutionPage, FailureGroup, FailureReport, MachineNumbers, Overview,
    StateDef, StateMachine, StepRun,
)
from .analysis import (
    LAMBDA_SERVICE_ERRORS, REDRIVE_DAYS, can_redrive, catch_label, definition_findings, execution_findings,
    failure_findings, fill_detail, group_failures, idle_findings, logging_findings, machine_findings, mermaid,
    monthly_cost, numbers_findings, overview_findings, parse_definition, parse_execution, parse_state_machine,
    request_id_of_cause, request_id_of_output, resource_label, retry_label, transitions_per_execution,
    unbounded_waits, unretried_lambda_states, ExecutionStory, LogLine, Waterfall, WaterfallRow, merge_log, step_lines,
    waterfall, FlowLayout, FlowMarks, FlowNode, failure_marks, flow_layout, path_marks,
)
from .analyzer import StepFunctionsAnalyzer
from .view import StepFunctionsView
from .explorer.window import StepFunctionsExplorer, explore

__all__ = [
    "LAMBDA_SERVICE_ERRORS",
    "REDRIVE_DAYS",
    "STEPFUNCTIONS_PRICES",
    "Attempt",
    "Execution",
    "ExecutionDetail",
    "ExecutionPage",
    "ExecutionStory",
    "FailureGroup",
    "FailureReport",
    "FlowLayout",
    "FlowMarks",
    "FlowNode",
    "LogLine",
    "MachineNumbers",
    "Overview",
    "StateDef",
    "StateMachine",
    "StepFunctionsAnalyzer",
    "StepFunctionsExplorer",
    "StepFunctionsView",
    "StepRun",
    "Waterfall",
    "WaterfallRow",
    "can_redrive",
    "catch_label",
    "definition_findings",
    "execution_findings",
    "execution_name",
    "explore",
    "failure_findings",
    "failure_marks",
    "fill_detail",
    "flow_layout",
    "group_failures",
    "idle_findings",
    "lambda_name",
    "logging_findings",
    "machine_findings",
    "machine_name",
    "merge_log",
    "mermaid",
    "monthly_cost",
    "numbers_findings",
    "overview_findings",
    "parse_definition",
    "parse_execution",
    "parse_execution_ref",
    "parse_state_machine",
    "path_marks",
    "request_id_of_cause",
    "request_id_of_output",
    "resource_label",
    "retry_label",
    "short_cause",
    "step_lines",
    "transitions_per_execution",
    "unbounded_waits",
    "unretried_lambda_states",
    "waterfall",
]
