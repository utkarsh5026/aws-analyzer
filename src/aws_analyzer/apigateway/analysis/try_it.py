"""
How to call a route yourself: a curl command (wscat for a WebSocket API) with the URL filled in and the credentials
the route asks for left as placeholders. Shown to copy; nothing here sends a request.
"""

from __future__ import annotations

import re

from ..models import Api, Route


GREEDY_RE = re.compile(r"\{(\w+)\+\}")

SAMPLE_BODY = "'{}'"


def route_path(route: Route) -> str:
    """
    A route's path as a caller would send it: {proxy+} becomes <path>, the other {params} stay for the caller to
    fill in. A $default route stands for any path.
    """
    if route.path in ("$default", "/"):
        return "/"
    return GREEDY_RE.sub(lambda m: f"<{m.group(1)}>", route.path)


def _auth_options(api: Api, route: Route) -> list[str]:
    """
    The curl options that carry the credentials a route asks for.
    """
    options = []
    if route.auth_type == "AWS_IAM":
        options.append(f'--aws-sigv4 "aws:amz:{api.region}:execute-api" '
                       '--user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" -H "x-amz-security-token: $AWS_SESSION_TOKEN"')
    elif route.auth_type != "NONE":
        options.append('-H "Authorization: $TOKEN"')
    if route.api_key_required:
        options.append('-H "x-api-key: $API_KEY"')
    return options


def _command_parts(api: Api, route: Route, stage: str | None) -> list[str]:
    """
    The pieces of the command that calls a route: the program and its method, each credential, the body, the URL.
    """
    base = api.url(stage)
    if api.kind == "WEBSOCKET":
        parts = ["wscat", f"-c {base}"]
        if route.path == "$connect" and route.auth_type != "NONE":
            parts.append('-H "Authorization: $TOKEN"')
        return parts
    method = "GET" if route.method in ("ANY", "") else route.method
    parts = ["curl -sS" + (f" -X {method}" if method != "GET" else "")]
    parts += _auth_options(api, route)
    if method in ("POST", "PUT", "PATCH"):
        parts.append(f'-H "Content-Type: application/json" -d {SAMPLE_BODY}')
    parts.append(f"{base}{route_path(route)}")
    return parts


def curl_command(api: Api, route: Route, stage: str | None = None) -> str:
    """
    The command that calls a route on a stage (its main stage when none is named), on one line: curl with its
    method, the credentials it needs as $PLACEHOLDERS and an empty JSON body for a method that sends one; wscat for a
    WebSocket API.
    """
    return " ".join(_command_parts(api, route, stage))


def curl_lines(api: Api, route: Route, stage: str | None = None) -> str:
    """
    The same command with each option on a line of its own (a shell continues it with a backslash), easier to read
    and to change.
    """
    return " \\\n  ".join(_command_parts(api, route, stage))
