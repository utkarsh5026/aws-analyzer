"""
Reading a stage's access log. Each line is written in the format the stage's owner chose, made of $context
variables: JSON ({"requestId":"$context.requestId", "status":"$context.status", ...}) or text (CLF, CSV, ...). The
format is in the stage's settings, so a line is read by its own format: a JSON key is known by the variable it holds,
and a text format becomes a pattern with a group per variable. A line from another format falls back on the usual
key names.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import Call, ErrorGroup, RouteNumbers


CONTEXT_RE = re.compile(r"\$context\.[A-Za-z0-9_.]+")

FIELD_VARIABLES = {
    "request_id": ("$context.requestId", "$context.extendedRequestId"),
    "status": ("$context.status",),
    "method": ("$context.httpMethod",),
    "path": ("$context.path",),
    "route": ("$context.routeKey", "$context.resourcePath"),
    "latency_ms": ("$context.responseLatency",),
    "integration_ms": ("$context.integration.latency", "$context.integrationLatency"),
    "authorizer_ms": ("$context.authorizer.latency", "$context.authorize.latency"),
    "error": ("$context.error.message", "$context.error.messageString"),
    "integration_error": ("$context.integrationErrorMessage", "$context.integration.error"),
    "integration_status": ("$context.integration.status", "$context.integrationStatus",
                           "$context.integration.integrationStatus"),
    "lambda_request": ("$context.integration.requestId", "$context.awsEndpointRequestId"),
    "ip": ("$context.identity.sourceIp",),
    "user": ("$context.authorizer.claims.sub", "$context.authorizer.principalId", "$context.identity.user",
             "$context.identity.caller"),
}

FALLBACK_KEYS = {
    "request_id": ("requestId", "request_id", "requestid"),
    "status": ("status", "statusCode", "status_code"),
    "method": ("httpMethod", "method", "http_method"),
    "path": ("path",),
    "route": ("routeKey", "resourcePath", "route", "resource"),
    "latency_ms": ("responseLatency", "latency", "responseLatencyMs"),
    "integration_ms": ("integrationLatency", "integration_latency"),
    "authorizer_ms": ("authorizerLatency", "authorizer_latency"),
    "error": ("errorMessage", "error", "error_message"),
    "integration_error": ("integrationErrorMessage", "integrationError", "integration_error"),
    "integration_status": ("integrationStatus", "integration_status"),
    "lambda_request": ("integrationRequestId", "awsEndpointRequestId", "lambdaRequestId"),
    "ip": ("ip", "sourceIp", "source_ip"),
    "user": ("user", "caller", "principalId"),
}

NUMBER_FIELDS = ("latency_ms", "integration_ms", "authorizer_ms")

EMPTY_VALUES = ("", "-", "null", "None")

WANTED_VARIABLES = ("$context.integration.latency", "$context.integration.requestId", "$context.error.message")


@dataclass
class LogFormat:
    """
    A stage's access log format, read: JSON (keys: field -> the JSON key holding it) or text (pattern: a regex with
    a group per field). fields: the $context variables it logs.
    """

    is_json: bool
    keys: dict[str, str]
    pattern: re.Pattern[str] | None
    fields: set[str]


def _field_of(variable: str) -> str:
    """
    The Call field a $context variable fills, or ''.
    """
    return next((name for name, variables in FIELD_VARIABLES.items() if variable in variables), "")


def _json_keys(template: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """
    Field -> the (dotted) JSON key whose value is a $context variable, in a JSON format.
    """
    keys: dict[str, str] = {}
    for key, value in template.items():
        if isinstance(value, dict):
            keys.update({k: v for k, v in _json_keys(value, f"{prefix}{key}.").items() if k not in keys})
            continue
        found = CONTEXT_RE.fullmatch(str(value).strip())
        name = _field_of(found.group(0)) if found else ""
        if name and name not in keys:
            keys[name] = f"{prefix}{key}"
    return keys


def _text_pattern(text: str) -> tuple[re.Pattern[str], set[str]]:
    """
    A text format as a regex: its literal parts as they are, each $context variable a group (named by its field when
    it fills one).
    """
    parts, used, named = [], set(), set()
    position = 0
    for found in CONTEXT_RE.finditer(text):
        parts.append(re.escape(text[position:found.start()]))
        name = _field_of(found.group(0))
        if name and name not in named:
            named.add(name)
            parts.append(f"(?P<{name}>.*?)")
        else:
            parts.append(".*?")
        used.add(found.group(0))
        position = found.end()
    parts.append(re.escape(text[position:]))
    return re.compile("^" + "".join(parts) + "$"), used


def read_format(text: str) -> LogFormat:
    """
    A stage's access log format (its accessLogSettings' format), ready to read lines with. An empty one reads JSON
    lines by the usual key names.
    """
    variables = set(CONTEXT_RE.findall(text or ""))
    try:
        template = json.loads(text) if text else {}
    except ValueError:
        template = None
    if isinstance(template, dict):
        return LogFormat(True, _json_keys(template), None, variables)
    pattern, used = _text_pattern(text)
    return LogFormat(False, {}, pattern, used)


def _dig(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _json_values(record: dict[str, Any], keys: dict[str, str]) -> dict[str, Any]:
    """
    Each field's value from a JSON line: by the format's key, else by the usual key names.
    """
    values = {}
    for name, fallbacks in FALLBACK_KEYS.items():
        value = _dig(record, keys[name]) if name in keys else None
        if value is None:
            value = next((record[key] for key in fallbacks if key in record), None)
        values[name] = value
    return values


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _to_call(values: dict[str, Any], when: datetime, message: str) -> Call:
    clean = {name: ("" if str(value) in EMPTY_VALUES or value is None else value) for name, value in values.items()}
    call = Call(time=when, message=message, status=_status(clean.get("status")))
    for name in NUMBER_FIELDS:
        setattr(call, name, _number(clean.get(name)))
    for name in ("request_id", "method", "path", "route", "error", "integration_error", "integration_status",
                 "lambda_request", "ip", "user"):
        setattr(call, name, str(clean.get(name) or ""))
    return call


def parse_access_line(message: str, when: datetime, log_format: LogFormat) -> Call | None:
    """
    One access log line as a Call, read by the stage's format; None when it isn't a line of that format.
    """
    text = (message or "").strip()
    if text.startswith("{"):
        try:
            record = json.loads(text)
        except ValueError:
            return None
        if not isinstance(record, dict):
            return None
        return _to_call(_json_values(record, log_format.keys), when, text)
    if log_format.pattern is None:
        return None
    found = log_format.pattern.match(text)
    if not found:
        return None
    return _to_call(found.groupdict(), when, text)


def missing_fields(log_format: LogFormat) -> list[str]:
    """
    The $context variables worth adding to a format that doesn't log them: how long the integration took, the
    Lambda request ID, and the error message.
    """
    return [variable for variable in WANTED_VARIABLES if variable not in log_format.fields]


def suggested_format(kind: str) -> str:
    """
    An access log format that logs what the Calls reports read, as JSON to paste into the stage's settings.
    """
    route = "$context.routeKey" if kind != "REST" else "$context.resourcePath"
    fields = {
        "requestId": "$context.requestId", "ip": "$context.identity.sourceIp", "httpMethod": "$context.httpMethod",
        "route": route, "status": "$context.status", "responseLatency": "$context.responseLatency",
        "integrationLatency": "$context.integration.latency", "integrationStatus": "$context.integration.status",
        "integrationRequestId": "$context.integration.requestId", "error": "$context.error.message",
        "integrationError": "$context.integrationErrorMessage", "authorizerLatency": "$context.authorizer.latency",
    }
    return json.dumps(fields, separators=(",", ":"))


def group_errors(calls: list[Call]) -> list[ErrorGroup]:
    """
    Failed calls grouped by status, error and route, the most common first, each with its newest call.
    """
    groups: dict[tuple[Any, str, str], list[Call]] = {}
    for call in calls:
        if call.failed:
            groups.setdefault((call.status, call.why, call.route_key), []).append(call)
    found = [ErrorGroup(status, error, route, len(members), max(members, key=lambda c: c.time))
             for (status, error, route), members in groups.items()]
    found.sort(key=lambda g: (-g.count, -(g.status or 0), g.route))
    return found


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def route_numbers_from_calls(calls: list[Call]) -> dict[str, RouteNumbers]:
    """
    Each route's numbers from the calls a log recorded: calls, 4xx and 5xx, and how long they took (average, 95th
    percentile, longest).
    """
    by_route: dict[str, list[Call]] = {}
    for call in calls:
        by_route.setdefault(call.route_key, []).append(call)
    numbers = {}
    for route, members in by_route.items():
        statuses = Counter((c.status or 0) // 100 for c in members)
        times = [c.latency_ms for c in members if c.latency_ms is not None]
        numbers[route] = RouteNumbers(calls=len(members), errors_4xx=statuses[4], errors_5xx=statuses[5],
                                      avg_ms=sum(times) / len(times) if times else None,
                                      p95_ms=_percentile(times, 0.95), max_ms=max(times) if times else None)
    return numbers


def latency_split(call: Call) -> list[tuple[str, float]]:
    """
    Where a call's time went, as (part, milliseconds): the authorizer, the integration, and API Gateway itself
    (the rest), each only when the log has it.
    """
    parts: list[tuple[str, float]] = []
    if call.authorizer_ms:
        parts.append(("authorizer", call.authorizer_ms))
    if call.integration_ms is not None:
        parts.append(("integration", call.integration_ms))
    if call.latency_ms is not None:
        rest = call.latency_ms - sum(ms for _, ms in parts)
        parts.append(("API Gateway", max(0.0, rest)))
    return parts
