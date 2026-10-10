"""
aws-analyzer - readable reports on your S3 buckets, DynamoDB tables, Bedrock knowledge bases, SageMaker notebooks,
OpenSearch vector indexes and Lambda functions, from a SageMaker / Jupyter notebook.

This is the pip-installed form of the files in the repository's analyzers/ folder: each module here is one of
those files, unchanged, so it works the same as a copy next to your notebook. Only the import line differs.

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

    from aws_analyzer.s3 import human_size   # anything else in a module: aws_analyzer.<module>

Modules: s3, s3_explorer, dynamodb, bedrock_kb, bedrock_chat, sagemaker_env, opensearch, lambda_functions. Importing
this package loads none of them; each loads the first time one of its names is used.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# For editors and type checkers; at run time __getattr__ imports them on first use. The modules are analyzers/*.py,
# which the build copies in here (pyproject.toml), so in the repository itself these imports don't resolve.
# pyright: reportMissingImports=false
if TYPE_CHECKING:
    from .bedrock_chat import BedrockChatAnalyzer, BedrockChatView, chat
    from .bedrock_kb import BedrockKBAnalyzer, BedrockKBView, KBExplorer
    from .dynamodb import DynamoDBAnalyzer, DynamoDBView
    from .lambda_functions import LambdaAnalyzer, LambdaExplorer, LambdaView
    from .opensearch import OpenSearchAnalyzer, OpenSearchView
    from .s3 import S3Analyzer, S3View
    from .s3_explorer import S3Explorer, S3Navigator
    from .sagemaker_env import SageMakerAnalyzer, SageMakerView

__version__ = "0.13.0"

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
]
_EXPORTS = {  # name -> the module it comes from
    "S3Analyzer": "s3",
    "S3View": "s3",
    "S3Explorer": "s3_explorer",
    "S3Navigator": "s3_explorer",
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
}
_MODULES = ("s3", "s3_explorer", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch",
            "lambda_functions")


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
