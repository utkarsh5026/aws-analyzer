"""
What the web page offers: each service, the View it runs, and the View's commands a browser tab can run, with their
parameters, read from the View's own signatures, docstrings and help() groups (nothing is listed twice).

A command is offered when it draws a report. The ones that write files or change something (a View's _CHANGES:
download, invoke, new_env, ...) stay in the notebook, where you see the code before it runs, and so do the ones that
open a widget window (explore, chat's app), hand back data (data) or put code in a cell (next).
"""

from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from .._kit.render import _signature
from .._kit.view import _commands

NOT_SERVED = frozenset({"explore", "app", "next", "data", "help"})
OTHER_GROUP = "Other"


@dataclass(frozen=True)
class Service:
    """
    A service the page lists: its key (the module, 'lambda_functions'), the name shown, its View and Analyzer
    classes' names, and its icon (one of AWS's architecture icons in static/icons/).
    """

    key: str
    title: str
    prefix: str
    icon: str

    @property
    def view_name(self) -> str:
        return f"{self.prefix}View"

    @property
    def analyzer_name(self) -> str:
        return f"{self.prefix}Analyzer"

    def module(self) -> Any:
        """
        The service's module, imported the first time it's needed, so the page loads only what it shows.
        """
        return importlib.import_module(f"aws_analyzer.{self.key}")


SERVICES = (
    Service("s3", "S3", "S3", "s3"),
    Service("lambda_functions", "Lambda", "Lambda", "lambda"),
    Service("dynamodb", "DynamoDB", "DynamoDB", "dynamodb"),
    Service("stepfunctions", "Step Functions", "StepFunctions", "stepfunctions"),
    Service("apigateway", "API Gateway", "APIGateway", "apigateway"),
    Service("messaging", "SQS, SNS & EventBridge", "Messaging", "messaging"),
    Service("athena", "Athena & Glue", "Athena", "athena"),
    Service("bedrock_kb", "Bedrock knowledge bases", "BedrockKB", "bedrock"),
    Service("bedrock_chat", "Bedrock chat", "BedrockChat", "bedrock"),
    Service("opensearch", "OpenSearch", "OpenSearch", "opensearch"),
    Service("sagemaker_env", "SageMaker", "SageMaker", "sagemaker"),
)
BY_KEY = {service.key: service for service in SERVICES}


@dataclass(frozen=True)
class Parameter:
    """
    One of a command's parameters as the page's form shows it: its name, its default written as Python ('' when it
    has none, so it's required), how it's entered ('flag', a checkbox, for a True / False default, else 'text') and
    whether it can be given in order (False after a bare * in the signature: by name only).
    """

    name: str
    default: str
    required: bool
    kind: str
    positional: bool = True


@dataclass(frozen=True)
class Command:
    """
    A command the page can run: its name, its signature without type hints, what it shows (the docstring's first
    paragraph, as help() says it), its whole docstring, the help() group it's in, and its parameters.
    """

    name: str
    signature: str
    about: str
    doc: str
    group: str
    parameters: tuple[Parameter, ...]


def _first_paragraph(doc: str) -> str:
    return " ".join(doc.split("\n\n")[0].split())


def _parameter(param: inspect.Parameter) -> Parameter:
    has_default = param.default is not inspect.Parameter.empty
    is_flag = isinstance(param.default, bool)
    default = repr(param.default) if has_default else ""
    positional = param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    return Parameter(param.name, default, not has_default, "flag" if is_flag else "text", positional)


def _form_parameters(function: Callable) -> tuple[Parameter, ...]:
    """
    The parameters a form can fill: every named one after self (a command's *args and **kwargs have no names to
    show; the call box takes them).
    """
    named = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    params = list(inspect.signature(function).parameters.values())[1:]
    return tuple(_parameter(param) for param in params if param.kind in named)


def _group_of(view_class: type) -> dict[str, str]:
    groups = getattr(view_class, "_GROUPS", {})
    return {name: group for group, names in groups.items() for name in names}


def served_names(view_class: type) -> list[str]:
    """
    The names of a View's commands the page offers, in help()'s order: every public command but the ones that
    change something (_CHANGES) or don't draw a report in a page (NOT_SERVED).
    """
    held_back = NOT_SERVED | set(getattr(view_class, "_CHANGES", ()))
    return [name for name in _commands(view_class) if name not in held_back]


def view_commands(view_class: type) -> list[Command]:
    """
    A View's commands the page offers, grouped as help() groups them, the groups in help()'s order.
    """
    functions = _commands(view_class)
    group_of = _group_of(view_class)
    order = [*getattr(view_class, "_GROUPS", {}), OTHER_GROUP]
    commands = []
    for name in served_names(view_class):
        function = functions[name]
        doc = inspect.getdoc(function) or ""
        commands.append(Command(name, _signature(function), _first_paragraph(doc), doc,
                                group_of.get(name, OTHER_GROUP), _form_parameters(function)))
    return sorted(commands, key=lambda command: order.index(command.group))


def _literal_or_text(text: str) -> Any:
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return text


def _is_number_text(text: str) -> bool:
    return text.replace(",", "").replace(".", "", 1).lstrip("-").isdigit()


def _looks_like_literal(text: str) -> bool:
    return text[:1] in "[{('\"" or text in ("True", "False", "None")


def argument_value(text: str, parameter: Parameter) -> Any:
    """
    What a form's box holds, as the value the command gets. The Views are forgiving ('7d', '10MB', 's3://b/x'), so
    text stays text unless it plainly means something else: 'true' / 'false' for a flag, a number for a parameter
    whose default is a number or None (limit=, n=), and anything written as Python ([...], {...}, a quoted string,
    True / False / None) for the others. A name ('orders-etl', '42' for a function) stays a string.
    """
    text = text.strip()
    if parameter.kind == "flag":
        return text.lower() in ("true", "1", "yes", "on")
    default = _literal_or_text(parameter.default) if parameter.default else ""
    if parameter.default and not isinstance(default, str):
        return _literal_or_text(text.replace(",", "") if _is_number_text(text) else text)
    if _looks_like_literal(text):
        return _literal_or_text(text)
    return text
