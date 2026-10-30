"""
aws-analyzer - readable reports on your S3 buckets, DynamoDB tables, Bedrock knowledge bases, SageMaker notebooks,
OpenSearch vector indexes and Lambda functions, from a SageMaker / Jupyter notebook, and an agent that answers
questions about them in plain English.

Each service is one module (aws_analyzer.s3, aws_analyzer.dynamodb, ...), and aws_analyzer._kit holds the code they
share (aws_analyzer._bedrock what the two Bedrock modules share), so install the package rather than copying a file.

Quick start
-----------
    %pip install aws-analyzer               # only boto3 is required; aws-analyzer[all] adds every optional package

    from aws_analyzer import S3View          # or DynamoDBView, BedrockKBView, SageMakerView, OpenSearchView, LambdaView,
                                             # S3Explorer
    ui = S3View()
    ui.help()                                # every command, grouped by task

    from aws_analyzer import chat            # a chat window on a Bedrock knowledge base
    chat("support-docs", model="sonnet")

    from aws_analyzer import KBExplorer      # a window to look through a knowledge base: every file, how it was
    KBExplorer("support-docs")               # indexed, its syncs, search and settings, by clicking

    from aws_analyzer import LambdaExplorer  # a window to look through your Lambda functions: their logs run by
    LambdaExplorer("orders-etl", tab="logs") # run, errors, run times, code and settings, by clicking

    from aws_analyzer import set_timezone    # times in your own zone instead of UTC, in every report and window
    set_timezone("Asia/Kolkata")             # or "local", "+05:30"; AWS_ANALYZER_TZ=Asia/Kolkata does it for good

    from aws_analyzer import set_theme       # how reports look: "console" (the default), "soft", "editorial",
    set_theme("soft")                        # "brand" or "classic"; AWS_ANALYZER_THEME=soft does it for good

    from aws_analyzer import AgentView       # ask about your account in plain English (aws-analyzer[agent])
    AgentView().ask("Which Lambda failed last night?")  # "last night" in set_timezone()'s zone

    from aws_analyzer import clean_up        # free the disk: remove the downloads and the agent sandbox's cache
    clean_up(dry_run=True)                   # aws-analyzer saved here (local_files() lists them); dry_run only says

    from aws_analyzer.s3 import human_size   # anything else in a module: aws_analyzer.<module>

Modules: s3, s3_explorer, dynamodb, bedrock_kb, bedrock_chat, sagemaker_env, opensearch, lambda_functions, agent.
Importing this package loads none of them; each loads the first time one of its names is used.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# For editors and type checkers; at run time __getattr__ imports them on first use.
if TYPE_CHECKING:
    from ._kit.fmt import set_timezone
    from ._kit.housekeeping import clean_up, local_files
    from ._kit.themes import set_theme
    from .agent import AgentAnalyzer, AgentView
    from .bedrock_chat import BedrockChatAnalyzer, BedrockChatView, chat
    from .bedrock_kb import BedrockKBAnalyzer, BedrockKBView, KBExplorer
    from .dynamodb import DynamoDBAnalyzer, DynamoDBView
    from .lambda_functions import LambdaAnalyzer, LambdaExplorer, LambdaView
    from .opensearch import OpenSearchAnalyzer, OpenSearchView
    from .s3 import S3Analyzer, S3Explorer, S3Navigator, S3View
    from .sagemaker_env import SageMakerAnalyzer, SageMakerView

__version__ = "0.40.0"

__all__ = [
    "__version__",
    "S3Analyzer",
    "S3View",
    "S3Explorer",
    "S3Navigator",
    "DynamoDBAnalyzer",
    "DynamoDBView",
    "BedrockKBAnalyzer",
    "BedrockKBView",
    "KBExplorer",
    "BedrockChatAnalyzer",
    "BedrockChatView",
    "chat",
    "SageMakerAnalyzer",
    "SageMakerView",
    "OpenSearchAnalyzer",
    "OpenSearchView",
    "LambdaAnalyzer",
    "LambdaView",
    "LambdaExplorer",
    "AgentAnalyzer",
    "AgentView",
    "set_timezone",
    "set_theme",
    "local_files",
    "clean_up",
]
_EXPORTS = {  # name -> the module it comes from
    "S3Analyzer": "s3",
    "S3View": "s3",
    "S3Explorer": "s3",
    "S3Navigator": "s3",
    "DynamoDBAnalyzer": "dynamodb",
    "DynamoDBView": "dynamodb",
    "BedrockKBAnalyzer": "bedrock_kb",
    "BedrockKBView": "bedrock_kb",
    "KBExplorer": "bedrock_kb",
    "BedrockChatAnalyzer": "bedrock_chat",
    "BedrockChatView": "bedrock_chat",
    "chat": "bedrock_chat",
    "SageMakerAnalyzer": "sagemaker_env",
    "SageMakerView": "sagemaker_env",
    "OpenSearchAnalyzer": "opensearch",
    "OpenSearchView": "opensearch",
    "LambdaAnalyzer": "lambda_functions",
    "LambdaView": "lambda_functions",
    "LambdaExplorer": "lambda_functions",
    "AgentAnalyzer": "agent",
    "AgentView": "agent",
    "set_timezone": "_kit.fmt",
    "set_theme": "_kit.themes",
    "local_files": "_kit.housekeeping",
    "clean_up": "_kit.housekeeping",
}
_MODULES = ("s3", "s3_explorer", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch",
            "lambda_functions", "agent")


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        value = getattr(importlib.import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    elif name in _MODULES:
        value = importlib.import_module(f"{__name__}.{name}")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value  # later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS, *_MODULES})
