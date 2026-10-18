"""
AWS operations named from botocore's own service models (no AWS call), the IAM
action each needs, and the event a handler takes.
"""

from __future__ import annotations

import base64
import functools
import json
import re
from typing import TYPE_CHECKING, Any, Iterable

from .terms import _IAM_ACTIONS, _IAM_PREFIXES, _READS_PREFIXES, _RUNS_PREFIXES, _SENDS_PREFIXES, _SERVICE_NAMES

if TYPE_CHECKING:
    from .model import EventField


def _service_name(service: str) -> str:
    """
    's3' -> 'S3', 'dynamodb' -> 'DynamoDB', 'stepfunctions' -> 'Step Functions': the name people use.
    """
    if service in _SERVICE_NAMES:
        return _SERVICE_NAMES[service]
    model = _aws_model(service)
    return model["name"] if model else service


@functools.lru_cache(maxsize=1)
def _botocore_loader() -> Any:
    from botocore.loaders import create_loader

    return create_loader()


def _tokens(value: Any) -> tuple[str, ...]:
    """
    A paginator's token setting as names: 'NextMarker || Contents[-1].Key' -> ('NextMarker',).
    """
    values = value if isinstance(value, list) else [value] if value else []
    return tuple(m.group(0) for v in values if (m := re.match(r"\w+", str(v))))


@functools.lru_cache(maxsize=256)
def _aws_model(service: str) -> dict[str, Any] | None:
    """
    What botocore's own copy of a service's API says, read from the files boto3 installs (no AWS call): its
    operations by method name, which of them come in pages, its name and its IAM prefix. None for a service this
    boto3 doesn't know.
    """
    if not service or not re.fullmatch(r"[a-z0-9-]+", service):
        return None
    from botocore import xform_name
    from botocore.exceptions import BotoCoreError

    loader = _botocore_loader()
    try:
        model = loader.load_service_model(service, "service-2")
    except (BotoCoreError, OSError, ValueError):
        return None
    try:
        pages = loader.load_service_model(service, "paginators-1").get("pagination", {})
    except (BotoCoreError, OSError, ValueError):
        pages = {}
    meta = model.get("metadata", {})
    return {
        "ops": {xform_name(op): op for op in model.get("operations", {})},
        "pages": {op: (frozenset(_tokens(spec.get("input_token"))),
                       _tokens(spec.get("output_token")) + _tokens(spec.get("more_results")),
                       str(spec.get("limit_key") or ""))
                  for op, spec in pages.items()},
        "name": meta.get("serviceId") or service,
        "iam": meta.get("signingName") or meta.get("endpointPrefix") or service,
    }


def _access(operation: str) -> str:
    """
    What an AWS operation does to what it touches: 'reads', 'writes', 'sends' (a message or an event) or 'runs'
    (invokes or starts something).
    """
    if operation.startswith(_SENDS_PREFIXES):
        return "sends"
    if operation.startswith(_RUNS_PREFIXES):
        return "runs"
    return "reads" if operation.startswith(_READS_PREFIXES) else "writes"


def _iam_action(service: str, operation: str) -> str:
    """
    The IAM action an operation needs: ('s3', 'ListObjectsV2') -> 's3:ListBucket'.
    """
    if (service, operation) in _IAM_ACTIONS:
        return _IAM_ACTIONS[(service, operation)]
    model = _aws_model(service)
    prefix = _IAM_PREFIXES.get(service) or (model["iam"] if model else service)
    return f"{prefix}:{operation}"


def _event_label(path: tuple[str, ...]) -> str:
    """
    ('Records', '[]', 'body', '{json}', 'bucket') -> 'Records[].body (JSON).bucket'.
    """
    out = ""
    for part in path:
        if part == "[]":
            out += "[]"
        elif part in ("{json}", "{base64}"):
            out += " (JSON)" if part == "{json}" else " (base64)"
        else:
            out += ("." if out else "") + part
    return out


def _event_kind(fields: Iterable[EventField]) -> str | None:
    """
    What sends the event, from the fields the code reads: 'an SQS batch', 'an HTTP request', ... None when it
    can't tell.
    """
    paths = [tuple(p.lower() for p in f.path) for f in fields]

    def has(*parts: str) -> bool:
        return any(path[:len(parts)] == parts for path in paths)

    for parts, kind in ((("records", "[]", "s3"), "an S3 event"), (("records", "[]", "sns"), "an SNS message"),
                        (("records", "[]", "dynamodb"), "a DynamoDB stream batch"),
                        (("records", "[]", "kinesis"), "a Kinesis batch"), (("records", "[]", "body"), "an SQS batch"),
                        (("records", "[]", "receipthandle"), "an SQS batch"),
                        (("records", "[]", "messageid"), "an SQS batch"), (("awslogs",), "CloudWatch Logs"),
                        (("detail-type",), "an EventBridge event"), (("actiongroup",), "a Bedrock agent action"),
                        (("apipath",), "a Bedrock agent action")):
        if has(*parts):
            return kind
    if any(has(key) for key in ("httpmethod", "pathparameters", "querystringparameters", "requestcontext", "headers",
                                "rawpath", "routekey", "multivalueheaders", "rawquerystring")):
        return "an HTTP request"
    if has("detail") and has("source"):
        return "an EventBridge event"
    if has("records"):
        return "a batch of records"
    return None


def _sample_event(fields: Iterable[EventField]) -> Any:
    """
    A test event with every field the code reads, its values '...': {'Records': [{'body': '{"bucket": "..."}'}]},
    for the console's Test tab. JSON read from a text field is written as text, the way it arrives.
    """
    root: dict[str, Any] = {}
    for f in fields:
        if "*" in f.path:
            continue
        node = root
        for part in f.path:
            node = node.setdefault(part, {})

    def finish(node: dict[str, Any]) -> Any:
        if not node:
            return "..."
        if "{json}" in node:
            return json.dumps(finish(node["{json}"]))
        if "{base64}" in node:
            inner = finish(node["{base64}"])
            text = inner if isinstance(inner, str) else json.dumps(inner)
            return base64.b64encode(text.encode()).decode() if text != "..." else "..."
        keys = [k for k in node if k != "[]"]
        if not keys and "[]" in node:
            return [finish(node["[]"])]
        return {k: finish(node[k]) for k in keys}

    return finish(root)
