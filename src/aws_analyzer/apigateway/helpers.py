"""
Small helpers for API Gateway: the references commands take (a name, 'name/stage', an API ID, an invoke URL or a
console link), the Lambda function an integration calls, an API's invoke URL, and the words reports use for an
API's kind and a route's authorizer.
"""

from __future__ import annotations

import re


KIND_LABELS = {"REST": "REST", "HTTP": "HTTP", "WEBSOCKET": "WebSocket"}

AUTH_LABELS = {"NONE": "none", "AWS_IAM": "IAM", "COGNITO_USER_POOLS": "Cognito", "JWT": "JWT",
               "CUSTOM": "Lambda authorizer", "REQUEST": "Lambda authorizer", "TOKEN": "Lambda authorizer"}

MAX_TIMEOUT_MS = {"REST": 29_000, "HTTP": 30_000, "WEBSOCKET": 29_000}

INVOKE_URL_RE = re.compile(r"^(?:https|wss)://(?P<id>[a-z0-9]+)\.execute-api\.(?P<region>[\w-]+)\.amazonaws\.com"
                           r"(?:/(?P<stage>[^/?#]+))?")

CONSOLE_API_RE = re.compile(r"console\.aws\.amazon\.com/apigateway/.*?(?:apis/|[?&]api=)(?P<id>[a-z0-9]+)")

LAMBDA_ARN_RE = re.compile(r"arn:aws[\w-]*:lambda:(?P<region>[\w-]+):\d+:function:(?P<name>[^:/]+)")

SERVICE_URI_RE = re.compile(r"^arn:aws[\w-]*:apigateway:[\w-]+:(?P<service>[\w.-]+):(?:action|path)/(?P<action>[^/?]+)")

DEFAULT_STAGE = "$default"

ACCOUNT_RATE_LIMIT = 10_000

PREFERRED_STAGES = ("prod", "production", "live", DEFAULT_STAGE)


def parse_api_ref(ref: str) -> tuple[str, str | None]:
    """
    What an API reference names, as (API, stage): 'orders-api/prod' gives both, an invoke URL its ID and stage, a
    console link its ID, and a name or ID alone (it, None).
    """
    text = (ref or "").strip()
    if not text:
        raise ValueError("Name an API: its name, its ID, 'name/stage', or its invoke URL")
    url = INVOKE_URL_RE.match(text)
    if url:
        return url.group("id"), url.group("stage")
    console = CONSOLE_API_RE.search(text)
    if console:
        return console.group("id"), None
    if "/" in text:
        api, _, stage = text.partition("/")
        return api.strip(), stage.strip() or None
    return text, None


def lambda_of_uri(uri: str) -> tuple[str, str]:
    """
    The Lambda function an integration's URI calls, as (name, region), or ('', '') when it calls none:
    'arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123:function:orders-etl/
    invocations' -> ('orders-etl', 'us-east-1').
    """
    found = LAMBDA_ARN_RE.search(uri or "")
    if not found:
        return "", ""
    return found.group("name"), found.group("region")


def service_action(uri: str) -> str:
    """
    The AWS service call an integration makes directly, or '': 'arn:aws:apigateway:us-east-1:dynamodb:action/
    PutItem' -> 'DynamoDB PutItem'.
    """
    found = SERVICE_URI_RE.match(uri or "")
    if not found or found.group("service") == "lambda":
        return ""
    service = {"dynamodb": "DynamoDB", "sqs": "SQS", "sns": "SNS", "s3": "S3", "states": "Step Functions",
               "kinesis": "Kinesis", "events": "EventBridge"}.get(found.group("service"), found.group("service"))
    return f"{service} {found.group('action')}"


def invoke_url(api_id: str, region: str, stage: str, kind: str = "REST") -> str:
    """
    Where a stage answers: https://<id>.execute-api.<region>.amazonaws.com/<stage> (wss:// for a WebSocket API; the
    $default stage has no path).
    """
    scheme = "wss" if kind == "WEBSOCKET" else "https"
    base = f"{scheme}://{api_id}.execute-api.{region}.amazonaws.com"
    return base if stage == DEFAULT_STAGE or not stage else f"{base}/{stage}"


def status_tone(status: int | None) -> str:
    """
    A status code's colour: 'bad' for 5xx, 'warn' for 4xx, 'ok' for the rest, '' when unknown.
    """
    if not status:
        return ""
    if status >= 500:
        return "bad"
    return "warn" if status >= 400 else "ok"


def route_sort_key(key: str) -> tuple[str, int, str]:
    """
    How routes are listed: by path, then by method in the usual order (GET, POST, PUT, PATCH, DELETE, the rest).
    """
    method, _, path = key.partition(" ") if " " in key else ("", "", key)
    order = ("GET", "POST", "PUT", "PATCH", "DELETE", "ANY", "OPTIONS", "HEAD")
    rank = order.index(method) if method in order else len(order)
    return (path, rank, method)
