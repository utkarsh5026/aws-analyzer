"""
What APIGatewayAnalyzer returns: an API with its stages, routes, integrations and authorizers, its CloudWatch
numbers, the calls its access log recorded, and those calls' errors grouped by cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require

from .helpers import ACCOUNT_RATE_LIMIT, AUTH_LABELS, DEFAULT_STAGE, KIND_LABELS, MAX_TIMEOUT_MS, PREFERRED_STAGES, service_action


@dataclass
class Authorizer:
    """
    Who an authorizer lets in: its type (COGNITO_USER_POOLS, JWT, TOKEN / REQUEST for a Lambda authorizer), where
    it reads the caller's identity, and the Lambda function it runs or the issuer it trusts.
    """

    id: str
    name: str
    type: str
    identity_source: str = ""
    function: str = ""
    issuer: str = ""

    @property
    def label(self) -> str:
        """
        'Cognito acme-users', 'JWT partner-jwt', 'Lambda authorizer check-token'.
        """
        kind = AUTH_LABELS.get(self.type, self.type.title())
        return f"{kind} {self.name}" if self.name else kind


@dataclass
class Integration:
    """
    Where a route sends a request: a Lambda function (AWS_PROXY / AWS), an HTTP URL (HTTP_PROXY / HTTP), an AWS
    service call, or a mock answer, with how long API Gateway waits for it.
    """

    type: str
    uri: str = ""
    method: str = ""
    function: str = ""
    function_region: str = ""
    timeout_ms: int | None = None
    connection: str = "INTERNET"
    payload_version: str = ""
    subtype: str = ""

    @property
    def kind(self) -> str:
        """
        What sort of backend it is: 'Lambda', 'HTTP', 'AWS service', 'mock' or 'VPC link'.
        """
        if self.function:
            return "Lambda"
        if self.type == "MOCK":
            return "mock"
        if self.connection == "VPC_LINK":
            return "VPC link"
        return "HTTP" if self.type.startswith("HTTP") else "AWS service"

    @property
    def target(self) -> str:
        """
        What it calls, in a few words: 'λ orders-etl', 'https://hooks.acme.io/in', 'DynamoDB PutItem', 'mock'.
        """
        if self.function:
            return f"λ {self.function}"
        if self.type == "MOCK":
            return "mock"
        action = self.subtype.replace("-", " ") if self.subtype else service_action(self.uri)
        if action:
            return action
        if self.connection == "VPC_LINK":
            return f"VPC link · {self.uri or 'private'}"
        return self.uri or self.type.lower()


@dataclass
class Route:
    """
    A method and path (or a WebSocket route key like $connect), who may call it, and where it goes.
    """

    method: str
    path: str
    auth_type: str = "NONE"
    authorizer: Authorizer | None = None
    api_key_required: bool = False
    integration: Integration | None = None
    validator: str = ""
    route_id: str = ""
    scopes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """
        'GET /orders/{id}', or a WebSocket route key ('$connect') as it is.
        """
        return f"{self.method} {self.path}" if self.method else self.path

    @property
    def auth(self) -> str:
        """
        Who may call it, in a few words: 'Cognito acme-users', 'IAM', 'API key', 'none'.
        """
        if self.authorizer is not None:
            label = self.authorizer.label
        else:
            label = AUTH_LABELS.get(self.auth_type, self.auth_type.title())
        if self.api_key_required:
            return "API key" if label == "none" else f"{label} + API key"
        return label

    @property
    def is_open(self) -> bool:
        """
        Whether anyone with the URL can call it: no authorizer and no API key. A CORS preflight (OPTIONS) and a mock
        answer (a health check) don't count: they reach nothing behind the API.
        """
        is_mock = self.integration is not None and self.integration.type == "MOCK"
        return self.auth_type == "NONE" and not self.api_key_required and self.method != "OPTIONS" and not is_mock

    @property
    def auth_kind(self) -> str:
        """
        Who may call it, without the authorizer's name: 'Cognito', 'JWT', 'IAM', 'Lambda authorizer', 'API key',
        'none'.
        """
        if self.authorizer is not None:
            kind = AUTH_LABELS.get(self.authorizer.type, self.authorizer.type.title())
        else:
            kind = AUTH_LABELS.get(self.auth_type, self.auth_type.title())
        if self.api_key_required:
            return "API key" if kind == "none" else f"{kind} + API key"
        return kind


@dataclass
class Stage:
    """
    A deployed copy of an API with its own URL and settings: throttling (requests a second, and the burst),
    execution logging, the access log (one line per request, where its log group is and the line's format), X-Ray
    tracing, the cache, WAF, detailed (per-route) metrics. Stage variables keep their names only.
    """

    name: str
    deployment_id: str = ""
    created: datetime | None = None
    updated: datetime | None = None
    description: str = ""
    throttle_rate: float | None = None
    throttle_burst: int | None = None
    logging: str = "OFF"
    data_trace: bool = False
    access_log_group: str = ""
    access_log_format: str = ""
    tracing: bool = False
    cache_enabled: bool = False
    cache_size: str = ""
    waf: str = ""
    detailed_metrics: bool = False
    auto_deploy: bool = False
    variables: list[str] = field(default_factory=list)

    @property
    def has_access_log(self) -> bool:
        return bool(self.access_log_group)

    @property
    def is_throttled(self) -> bool:
        """
        Whether it has a rate of its own below the account's (a stage left at the default shares the account's
        10,000 requests a second with every API in the region).
        """
        return self.throttle_rate is not None and 0 < self.throttle_rate < ACCOUNT_RATE_LIMIT


@dataclass
class Api:
    """
    One API: REST, HTTP or WebSocket, with its stages, routes and authorizers once read (api() reads them; the list
    of APIs has the name, kind and dates only). errors: the parts that couldn't be read (part -> error code).
    """

    id: str
    name: str
    kind: str
    region: str = ""
    created: datetime | None = None
    description: str = ""
    endpoint_type: str = ""
    endpoint: str = ""
    api_key_source: str = ""
    default_endpoint_disabled: bool = False
    stages: list[Stage] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    authorizers: dict[str, Authorizer] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    read: bool = False

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def max_timeout_ms(self) -> int:
        """
        The longest API Gateway waits for an integration here: 29 s for REST and WebSocket, 30 s for HTTP.
        """
        return MAX_TIMEOUT_MS.get(self.kind, 29_000)

    @property
    def open_routes(self) -> list[Route]:
        """
        The routes anyone can call: for a WebSocket API only $connect matters, since the others need a connection.
        """
        if self.kind == "WEBSOCKET":
            return [r for r in self.routes if r.path == "$connect" and r.is_open]
        return [r for r in self.routes if r.is_open]

    @property
    def functions(self) -> list[str]:
        """
        The Lambda functions its routes call, each once, in route order.
        """
        names = [r.integration.function for r in self.routes if r.integration and r.integration.function]
        return list(dict.fromkeys(names))

    def stage(self, name: str | None) -> Stage | None:
        """
        A stage by name, or the main one when name is None: prod (or production, live, $default) when there is one,
        else the one deployed last.
        """
        if name:
            return next((s for s in self.stages if s.name == name), None)
        for wanted in PREFERRED_STAGES:
            found = next((s for s in self.stages if s.name == wanted), None)
            if found is not None:
                return found
        dated = [s for s in self.stages if (s.updated or s.created) is not None]
        if dated:
            return max(dated, key=lambda s: s.updated or s.created)
        return self.stages[0] if self.stages else None

    def route(self, key: str) -> Route | None:
        """
        A route by its key ('POST /orders'), its path alone when one method has it, or its route ID.
        """
        text = " ".join((key or "").split())
        found = next((r for r in self.routes if r.key == text or r.route_id == text), None)
        if found is not None:
            return found
        by_path = [r for r in self.routes if r.path == text]
        return by_path[0] if len(by_path) == 1 else None

    def url(self, stage: str | None = None) -> str:
        """
        Where a stage answers (the main stage's when none is named).
        """
        chosen = self.stage(stage)
        name = chosen.name if chosen is not None else (stage or DEFAULT_STAGE)
        scheme = "wss" if self.kind == "WEBSOCKET" else "https"
        base = self.endpoint or f"{scheme}://{self.id}.execute-api.{self.region}.amazonaws.com"
        return base if name == DEFAULT_STAGE else f"{base}/{name}"


@dataclass
class ApiNumbers:
    """
    An API's (or a stage's) CloudWatch numbers over `days` days: requests (WebSocket: messages), 4xx and 5xx
    errors, the average and 95th percentile of the time a request took, and the average time its integration took,
    with the requests and 5xx errors of each day.
    """

    days: int
    requests: int = 0
    errors_4xx: int = 0
    errors_5xx: int = 0
    latency_ms: float | None = None
    p95_ms: float | None = None
    integration_ms: float | None = None
    connections: int = 0
    daily_requests: list[int] = field(default_factory=list)
    daily_5xx: list[int] = field(default_factory=list)

    @property
    def rate_5xx(self) -> float:
        return self.errors_5xx / self.requests if self.requests else 0.0

    @property
    def rate_4xx(self) -> float:
        return self.errors_4xx / self.requests if self.requests else 0.0


@dataclass
class RouteNumbers:
    """
    One route's numbers, from detailed CloudWatch metrics or from the calls its access log recorded.
    """

    calls: int = 0
    errors_4xx: int = 0
    errors_5xx: int = 0
    avg_ms: float | None = None
    p95_ms: float | None = None
    max_ms: float | None = None

    @property
    def rate_5xx(self) -> float:
        return self.errors_5xx / self.calls if self.calls else 0.0


@dataclass
class Overview:
    """
    Every API in a region (each read with its stages and routes), their numbers over `days` days (API ID ->
    ApiNumbers), the parts that couldn't be read (an API's name, or 'metrics'), and how many metrics were read.
    """

    region: str
    days: int
    apis: list[Api] = field(default_factory=list)
    numbers: dict[str, ApiNumbers] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0


@dataclass
class ApiReport:
    """
    One API in detail: its stage's numbers over `days` days, each route's when the stage has detailed metrics, and
    what couldn't be read ('metrics', 'routes').
    """

    api: Api
    stage: Stage | None
    days: int
    numbers: ApiNumbers | None = None
    route_numbers: dict[str, RouteNumbers] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0


@dataclass
class Call:
    """
    One request, as the stage's access log recorded it: when, its request ID, its route, its status and error, how
    long it took and how much of that the authorizer and the integration took, and the Lambda request ID behind it
    when the format logs it. Fields the format doesn't log are empty.
    """

    time: datetime
    request_id: str = ""
    method: str = ""
    path: str = ""
    route: str = ""
    status: int | None = None
    latency_ms: float | None = None
    integration_ms: float | None = None
    authorizer_ms: float | None = None
    error: str = ""
    integration_error: str = ""
    integration_status: str = ""
    lambda_request: str = ""
    ip: str = ""
    user: str = ""
    message: str = ""

    @property
    def failed(self) -> bool:
        return self.status is not None and self.status >= 400

    @property
    def route_key(self) -> str:
        """
        The route it matched ('POST /orders'), else its method and path as sent.
        """
        if self.route:
            return self.route if " " in self.route or not self.method else f"{self.method} {self.route}"
        return f"{self.method} {self.path}".strip()

    @property
    def why(self) -> str:
        """
        The error it got, the integration's error first when there's one.
        """
        return self.integration_error or self.error


@dataclass
class CallPage:
    """
    The calls a stage's access log recorded from `since` to `until`, newest first: at most the newest `limit` lines
    were read (truncated says when there were more). fields: the $context variables its format logs. unread: lines
    that didn't parse. note: why there are none to read (no access log, or it couldn't be read).
    """

    api: Api
    stage: Stage | None
    since: datetime
    until: datetime
    calls: list[Call] = field(default_factory=list)
    truncated: bool = False
    fields: set[str] = field(default_factory=set)
    unread: int = 0
    note: str = ""
    log_group: str = ""

    @property
    def failed(self) -> list[Call]:
        return [c for c in self.calls if c.failed]

    def to_df(self) -> Any:
        """
        The calls as a pandas DataFrame, a row each.
        """
        pd = _require("pandas", "to_df()")
        columns = ("time", "request_id", "route_key", "status", "latency_ms", "integration_ms", "authorizer_ms",
                   "why", "lambda_request", "ip")
        return pd.DataFrame([{name: getattr(call, name) for name in columns} for call in self.calls])


@dataclass
class ErrorGroup:
    """
    Calls that failed the same way: the same status, error and route, how many, and the newest.
    """

    status: int | None
    error: str
    route: str
    count: int
    newest: Call


@dataclass
class ErrorReport:
    """
    What's failing in a stage since `since`: its numbers from CloudWatch, and when it has an access log, the failed
    calls it recorded, grouped by cause (the most common first).
    """

    api: Api
    stage: Stage | None
    since: datetime
    numbers: ApiNumbers | None = None
    page: CallPage | None = None
    groups: list[ErrorGroup] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class RunLine:
    """
    One line the Lambda run behind a call logged, readable (what Lambda writes before it taken off), marked when it
    reports an error.
    """

    time: datetime
    text: str
    is_error: bool = False


@dataclass
class LambdaRun:
    """
    The Lambda run behind a call: the function, its request ID, its lines (empty when it wasn't found), how long it
    ran and how it ended (from its REPORT line), and a note on how it was found or why it wasn't.
    """

    function: str
    request_id: str
    lines: list[RunLine] = field(default_factory=list)
    note: str = ""
    duration_ms: float | None = None
    status: str = ""

    @property
    def found(self) -> bool:
        return bool(self.lines)
