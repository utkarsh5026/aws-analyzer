"""
acme's API Gateway, for the tests, the demo and the guide's figures: six APIs made in moto (REST in apigateway, HTTP
and WebSocket in apigatewayv2), their stages' access logs in moto's CloudWatch Logs, the Lambda run behind the newest
timeout in orders-etl's log, and FakeCloudWatch, which answers GetMetricData from `traffic` (moto keeps no
API Gateway metrics, and has no percentiles).

    orders-api   REST, Cognito; POST /orders runs out of time (504s), a 502 and refused callers; prod logs every
                 call in JSON, dev keeps no log and has no throttle
    search-api   HTTP, JWT, busy and fine; its access log is HTTP APIs' default format, without the backend's time
    partner-api  HTTP, two routes anyone can call, many 4xx, no access log
    chat-ws      WebSocket, a Lambda authorizer on $connect
    reports-api  REST, IAM, a 0.5 GB cache, no access log
    legacy-v1    REST, an API key, two stages and no calls in 30 days
"""

from __future__ import annotations

import io
import json
import random
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import boto3
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator


REGION = "us-east-1"

ACCOUNT = "123456789012"

DAYS = 7

ORDERS_LOG = "/aws/apigateway/orders-api"

SEARCH_LOG = "/aws/apigateway/search-api"

ORDERS_FORMAT = json.dumps({
    "requestId": "$context.requestId", "ip": "$context.identity.sourceIp", "user": "$context.authorizer.claims.sub",
    "httpMethod": "$context.httpMethod", "resourcePath": "$context.resourcePath", "status": "$context.status",
    "responseLatency": "$context.responseLatency", "integrationLatency": "$context.integration.latency",
    "authorizerLatency": "$context.authorizer.latency", "integrationRequestId": "$context.integration.requestId",
    "error": "$context.error.message", "integrationError": "$context.integrationErrorMessage",
}, separators=(",", ":"))

SEARCH_FORMAT = json.dumps({
    "requestId": "$context.requestId", "ip": "$context.identity.sourceIp", "requestTime": "$context.requestTime",
    "httpMethod": "$context.httpMethod", "routeKey": "$context.routeKey", "status": "$context.status",
    "protocol": "$context.protocol", "responseLength": "$context.responseLength",
}, separators=(",", ":"))

TIMEOUT_RUN = "8f5ce35b-6a1d-4f0e-9a8b-2c7d1e0f4a3b"

BAD_ANSWER_RUN = "2a90bb41-77c3-4e21-8d0b-5f6e7a8b9c0d"

ROLE = f"arn:aws:iam::{ACCOUNT}:role/apigw"

CODE = b"def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n"


def function_uri(name: str) -> str:
    """
    A REST integration's URI for a Lambda function.
    """
    return (f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:{REGION}:{ACCOUNT}:"
            f"function:{name}/invocations")


def function_arn(name: str) -> str:
    return f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}"


@dataclass
class Traffic:
    """
    One API's (or stage's) numbers for FakeCloudWatch: requests and 5xx per day (oldest first), 4xx over the week,
    and its times in ms.
    """

    daily: list[int]
    daily_5xx: list[int]
    errors_4xx: int = 0
    latency: float = 0.0
    p95: float = 0.0
    integration: float = 0.0
    connects: int = 0
    routes: dict[str, dict[str, float]] = field(default_factory=dict)


def _spread(total: int, days: int = DAYS, seed: int = 1) -> list[int]:
    rng = random.Random(seed)
    weights = [rng.uniform(0.8, 1.2) for _ in range(days)]
    scale = total / sum(weights)
    return [round(w * scale) for w in weights]


def traffic() -> dict[str, Traffic]:
    """
    Each API's numbers by its CloudWatch name (a REST API's name, an HTTP or WebSocket API's name too: FakeCloudWatch
    maps IDs to names), the same for its main stage.
    """
    orders_routes = {
        "GET /orders/{id}": {"Count": 1_283_776, "4XX": 9_102, "5XX": 41, "Average": 96, "p95": 120, "Maximum": 2_100},
        "POST /orders": {"Count": 612_400, "4XX": 2_210, "5XX": 58_900, "Average": 9_800, "p95": 29_000,
                         "Maximum": 29_010},
        "GET /orders": {"Count": 190_210, "4XX": 1_402, "5XX": 190, "Average": 210, "p95": 340, "Maximum": 4_200},
        "DELETE /orders/{id}": {"Count": 12_005, "4XX": 30, "5XX": 0, "Average": 31, "p95": 45, "Maximum": 210},
        "GET /health": {"Count": 6_160, "4XX": 0, "5XX": 0, "Average": 4, "p95": 8, "Maximum": 20},
    }
    return {
        "orders-api": Traffic(_spread(2_104_551, seed=2), _spread(61_204, seed=3), errors_4xx=12_744, latency=2_380,
                              p95=24_400, integration=2_310, routes=orders_routes),
        "search-api": Traffic(_spread(1_980_002, seed=4), _spread(120, seed=5), errors_4xx=23_760, latency=96,
                              p95=180, integration=82),
        "partner-api": Traffic(_spread(180_312, seed=6), _spread(180, seed=7), errors_4xx=17_670, latency=61, p95=95,
                               integration=48),
        "chat-ws": Traffic(_spread(512_830, seed=8), _spread(1_025, seed=9), errors_4xx=510, integration=35,
                           connects=18_204),
        "reports-api": Traffic(_spread(34_635, seed=10), _spread(139, seed=11), errors_4xx=692, latency=640,
                               p95=1_100, integration=610),
        "legacy-v1": Traffic([0] * DAYS, [0] * DAYS),
    }


_COUNT_METRICS = {"Count": "daily", "MessageCount": "daily", "5XXError": "daily_5xx", "5xx": "daily_5xx",
                  "ExecutionError": "daily_5xx"}

_ROUTE_METRICS = {"Count": "Count", "4XXError": "4XX", "4xx": "4XX", "5XXError": "5XX", "5xx": "5XX"}


class _Checked:
    """
    A boto3 client stand-in answering from methods of its own, every request and answer checked against the service
    model. holds: an Event per operation that its calls wait for (the background tests hold one back); denied:
    operations refused as AccessDenied.
    """

    service = ""

    def __init__(self, region: str = REGION):
        model = boto3.client(self.service, region_name=region).meta.service_model
        self.meta = SimpleNamespace(region_name=region, service_model=model)
        self._ops = {xform_name(op): model.operation_model(op) for op in model.operation_names}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.denied: set[str] = set()
        self.holds: dict[str, threading.Event] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or not hasattr(type(self), f"_{name}"):
            raise AttributeError(name)
        return lambda **params: self._call(name, params)

    def _call(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        operation = self._ops[name]
        report = ParamValidator().validate(params, operation.input_shape)
        assert not report.has_errors(), report.generate_report()
        self.calls.append((name, params))
        hold = self.holds.get(name)
        if hold is not None:
            hold.wait(5)
        if name in self.denied:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, operation.name)
        answer = getattr(self, f"_{name}")(**params)
        report = ParamValidator().validate(answer, operation.output_shape)
        assert not report.has_errors(), report.generate_report()
        return answer


class FakeCloudWatch(_Checked):
    """
    GetMetricData over `numbers` (API name -> Traffic), whatever the stage: a daily count gives a value a day, the
    rest one value for the window. ids: an HTTP or WebSocket API's ID -> its name.
    """

    service = "cloudwatch"

    def __init__(self, numbers: dict[str, Traffic], ids: dict[str, str], region: str = REGION):
        super().__init__(region)
        self.numbers = numbers
        self.ids = ids

    def _get_metric_data(self, MetricDataQueries: list[dict[str, Any]], StartTime: datetime, EndTime: datetime,
                         **params: Any) -> dict[str, Any]:
        results = []
        for query in MetricDataQueries:
            stat = query["MetricStat"]
            metric = stat["Metric"]
            dims = {d["Name"]: d["Value"] for d in metric["Dimensions"]}
            name = dims.get("ApiName") or self.ids.get(dims.get("ApiId", ""), "")
            found = self.numbers.get(name)
            stamps, values = self._values(found, dims, metric["MetricName"], stat, StartTime) if found else ([], [])
            results.append({"Id": query["Id"], "Label": metric["MetricName"], "Timestamps": stamps, "Values": values,
                            "StatusCode": "Complete"})
        return {"MetricDataResults": results}

    def _values(self, found: Traffic, dims: dict[str, str], metric: str, stat: dict[str, Any],
                start: datetime) -> tuple[list[datetime], list[float]]:
        if "Route" in dims or "Resource" in dims:
            route = dims.get("Route") or f"{dims.get('Method')} {dims.get('Resource')}"
            numbers = found.routes.get(route)
            key = _ROUTE_METRICS.get(metric, stat["Stat"])
            return ([start], [float(numbers[key])]) if numbers and key in numbers else ([], [])
        period = stat["Period"]
        if metric in _COUNT_METRICS:
            daily = getattr(found, _COUNT_METRICS[metric])
            if period == 86_400:
                return [start + timedelta(days=i) for i in range(len(daily))], [float(v) for v in daily]
            share = min(1.0, period / (DAYS * 86_400))
            return [start], [float(round(sum(daily) * share))]
        value = self._single(found, metric, stat["Stat"], period)
        return ([start], [value]) if value is not None else ([], [])

    def _single(self, found: Traffic, metric: str, stat: str, period: int) -> float | None:
        share = min(1.0, period / (DAYS * 86_400))
        if metric in ("4XXError", "4xx", "ClientError"):
            return float(round(found.errors_4xx * share))
        if metric == "ConnectCount":
            return float(round(found.connects * share))
        if metric == "IntegrationLatency":
            return found.integration or None
        if metric == "Latency":
            return (found.p95 if stat == "p95" else found.latency) or None
        return None


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.py", CODE)
    return buffer.getvalue()


def _rest_method(ag: Any, api: str, resource: str, verb: str, integration: dict[str, Any], **method: Any) -> None:
    ag.put_method(restApiId=api, resourceId=resource, httpMethod=verb, **{"authorizationType": "NONE", **method})
    ag.put_integration(restApiId=api, resourceId=resource, httpMethod=verb, **integration)


def _lambda_proxy(name: str, timeout: int = 29_000) -> dict[str, Any]:
    return {"type": "AWS_PROXY", "integrationHttpMethod": "POST", "uri": function_uri(name), "timeoutInMillis": timeout}


def _orders_api(ag: Any) -> str:
    api = ag.create_rest_api(name="orders-api", description="Orders for the shop and the warehouse",
                             endpointConfiguration={"types": ["REGIONAL"]})["id"]
    root = ag.get_resources(restApiId=api)["items"][0]["id"]
    users = ag.create_authorizer(restApiId=api, name="acme-users", type="COGNITO_USER_POOLS",
                                 providerARNs=[f"arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/acme-users"],
                                 identitySource="method.request.header.Authorization")["id"]
    cognito = {"authorizationType": "COGNITO_USER_POOLS", "authorizerId": users}
    orders = ag.create_resource(restApiId=api, parentId=root, pathPart="orders")["id"]
    one = ag.create_resource(restApiId=api, parentId=orders, pathPart="{id}")["id"]
    health = ag.create_resource(restApiId=api, parentId=root, pathPart="health")["id"]
    _rest_method(ag, api, orders, "GET", _lambda_proxy("orders-read"), **cognito)
    _rest_method(ag, api, orders, "POST", _lambda_proxy("orders-etl"), **cognito)
    _rest_method(ag, api, one, "GET", _lambda_proxy("orders-read"), **cognito)
    _rest_method(ag, api, one, "DELETE", {"type": "AWS", "integrationHttpMethod": "POST", "credentials": ROLE,
                                          "uri": f"arn:aws:apigateway:{REGION}:dynamodb:action/DeleteItem"},
                 authorizationType="AWS_IAM")
    _rest_method(ag, api, health, "GET", {"type": "MOCK"})
    ag.create_deployment(restApiId=api, stageName="prod")
    ag.create_deployment(restApiId=api, stageName="dev")
    ag.update_stage(restApiId=api, stageName="prod", patchOperations=[
        {"op": "replace", "path": "/accessLogSettings/destinationArn",
         "value": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{ORDERS_LOG}"},
        {"op": "replace", "path": "/accessLogSettings/format", "value": ORDERS_FORMAT},
        {"op": "replace", "path": "/*/*/throttling/rateLimit", "value": "500"},
        {"op": "replace", "path": "/*/*/throttling/burstLimit", "value": "1000"},
        {"op": "replace", "path": "/*/*/logging/loglevel", "value": "ERROR"},
        {"op": "replace", "path": "/*/*/metrics/enabled", "value": "true"},
        {"op": "replace", "path": "/tracingEnabled", "value": "true"},
    ])
    return api


def _simple_rest(ag: Any, name: str, path: str, method: dict[str, Any], integration: dict[str, Any],
                 stages: tuple[str, ...]) -> str:
    api = ag.create_rest_api(name=name, endpointConfiguration={"types": ["REGIONAL"]})["id"]
    root = ag.get_resources(restApiId=api)["items"][0]["id"]
    resource = ag.create_resource(restApiId=api, parentId=root, pathPart=path)["id"]
    _rest_method(ag, api, resource, "GET", integration, **method)
    for stage in stages:
        ag.create_deployment(restApiId=api, stageName=stage)
    return api


def _http_api(v2: Any, name: str, routes: list[tuple[str, str, dict[str, Any]]],
              stage: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """
    An HTTP API with its routes ((route key, target, route settings): a target that starts with https:// is an HTTP
    proxy, else a Lambda function) and one stage.
    """
    api = v2.create_api(Name=name, ProtocolType="HTTP")["ApiId"]
    jwt = v2.create_authorizer(ApiId=api, AuthorizerType="JWT", Name=f"{name.split('-')[0]}-jwt",
                               IdentitySource=["$request.header.Authorization"],
                               JwtConfiguration={"Audience": ["acme"], "Issuer": "https://auth.acme.io"})["AuthorizerId"]
    integrations: dict[str, str] = {}
    for key, target, settings in routes:
        if target not in integrations:
            if target.startswith("https://"):
                made = v2.create_integration(ApiId=api, IntegrationType="HTTP_PROXY", IntegrationUri=target,
                                             IntegrationMethod="ANY", PayloadFormatVersion="1.0", TimeoutInMillis=10_000)
            else:
                made = v2.create_integration(ApiId=api, IntegrationType="AWS_PROXY", IntegrationUri=function_arn(target),
                                             PayloadFormatVersion="2.0", TimeoutInMillis=30_000)
            integrations[target] = made["IntegrationId"]
        auth = {"AuthorizationType": "JWT", "AuthorizerId": jwt} if settings.get("jwt") else {}
        v2.create_route(ApiId=api, RouteKey=key, Target=f"integrations/{integrations[target]}", **auth)
    v2.create_stage(ApiId=api, **stage)
    return api, integrations


def _chat_ws(v2: Any) -> str:
    api = v2.create_api(Name="chat-ws", ProtocolType="WEBSOCKET", RouteSelectionExpression="$request.body.action")["ApiId"]
    checker = v2.create_authorizer(ApiId=api, AuthorizerType="REQUEST", Name="chat-token",
                                   IdentitySource=["route.request.querystring.token"],
                                   AuthorizerUri=function_uri("chat-auth"))["AuthorizerId"]
    for key, function in (("$connect", "chat-connect"), ("$disconnect", "chat-connect"), ("sendMessage", "chat-msg")):
        made = v2.create_integration(ApiId=api, IntegrationType="AWS_PROXY", IntegrationUri=function_arn(function))
        auth = {"AuthorizationType": "CUSTOM", "AuthorizerId": checker} if key == "$connect" else {}
        v2.create_route(ApiId=api, RouteKey=key, Target=f"integrations/{made['IntegrationId']}", **auth)
    v2.create_stage(ApiId=api, StageName="prod", DefaultRouteSettings={"ThrottlingRateLimit": 200,
                                                                         "ThrottlingBurstLimit": 400})
    return api


def seed(now: datetime) -> dict[str, str]:
    """
    acme's six APIs in moto (call it inside mock_aws), their access logs, and the Lambda functions behind the
    orders API with the runs its failed calls name. Returns each API's ID by name.
    """
    ag = boto3.client("apigateway", region_name=REGION)
    v2 = boto3.client("apigatewayv2", region_name=REGION)
    ids = {"orders-api": _orders_api(ag)}
    ids["search-api"], _ = _http_api(v2, "search-api", [("GET /search", "search-q", {"jwt": True}),
                                                        ("GET /suggest", "search-q", {"jwt": True})], {
        "StageName": "$default", "AutoDeploy": True, "DefaultRouteSettings": {
            "ThrottlingRateLimit": 2_000, "ThrottlingBurstLimit": 4_000},
        "AccessLogSettings": {"DestinationArn": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{SEARCH_LOG}",
                              "Format": SEARCH_FORMAT}})
    ids["partner-api"], _ = _http_api(v2, "partner-api", [
        ("GET /partners", "partners-list", {}), ("POST /webhook", "https://hooks.acme.io/in", {}),
        ("GET /partners/{id}", "partners-get", {"jwt": True}), ("PUT /partners/{id}", "partners-put", {"jwt": True}),
        ("ANY /admin/{proxy+}", "partners-admin", {"jwt": True}), ("GET /status", "https://status.acme.io", {"jwt": True}),
    ], {"StageName": "prod", "DefaultRouteSettings": {"ThrottlingRateLimit": 100, "ThrottlingBurstLimit": 200}})
    ids["chat-ws"] = _chat_ws(v2)
    ids["reports-api"] = _simple_rest(ag, "reports-api", "reports", {"authorizationType": "AWS_IAM"},
                                      _lambda_proxy("reports"), ("prod",))
    ag.update_stage(restApiId=ids["reports-api"], stageName="prod", patchOperations=[
        {"op": "replace", "path": "/cacheClusterEnabled", "value": "true"},
        {"op": "replace", "path": "/cacheClusterSize", "value": "0.5"}])
    ids["legacy-v1"] = _simple_rest(ag, "legacy-v1", "items", {"apiKeyRequired": True},
                                    {"type": "HTTP_PROXY", "integrationHttpMethod": "GET",
                                     "uri": "https://legacy.acme.io/items"}, ("prod", "v1"))
    _put(ORDERS_LOG, orders_log_lines(now))
    _put(SEARCH_LOG, search_log_lines(now))
    _functions(now)
    return ids


def _put(group: str, lines: list[tuple[datetime, str]], stream: str = "access") -> None:
    logs = boto3.client("logs", region_name=REGION)
    try:
        logs.create_log_group(logGroupName=group)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.create_log_stream(logGroupName=group, logStreamName=stream)
    events = [{"timestamp": int(moment.timestamp() * 1000), "message": text} for moment, text in sorted(lines)]
    for begin in range(0, len(events), 5_000):
        logs.put_log_events(logGroupName=group, logStreamName=stream, logEvents=events[begin: begin + 5_000])


def _functions(now: datetime) -> None:
    """
    The orders API's Lambda functions, and in orders-etl's log the runs behind the newest 504 and 502.
    """
    iam = boto3.client("iam", region_name=REGION)
    try:
        role = iam.create_role(RoleName="apigw-fns", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName="apigw-fns")["Role"]["Arn"]
    lam = boto3.client("lambda", region_name=REGION)
    for name, timeout in (("orders-etl", 60), ("orders-read", 10)):
        lam.create_function(FunctionName=name, Runtime="python3.12", Role=role, Handler="app.handler",
                            Code={"ZipFile": _zip()}, Timeout=timeout)
    newest_timeout, newest_bad = _newest(now)
    _put("/aws/lambda/orders-etl", timeout_run(newest_timeout) + bad_answer_run(newest_bad),
         stream="2026/11/01/[$LATEST]7f3a")


def _newest(now: datetime) -> tuple[datetime, datetime]:
    """
    When the newest timed-out call and the newest 502 ended, as orders_log_lines writes them.
    """
    return now - timedelta(minutes=4), now - timedelta(minutes=63)


def _stamp(moment: datetime) -> str:
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"


def timeout_run(ended: datetime) -> list[tuple[datetime, str]]:
    """
    The orders-etl run behind the newest 504: it started 29 s before API Gateway gave up and ran 41 s.
    """
    start = ended - timedelta(seconds=29)
    at = lambda seconds: start + timedelta(seconds=seconds)  # noqa: E731
    rid = TIMEOUT_RUN
    return [
        (at(0), f"START RequestId: {rid} Version: $LATEST"),
        (at(0.04), f"{_stamp(at(0.04))}\t{rid}\tloading 4,812 rows from s3://acme-uploads/orders/batch-118.json"),
        (at(12.6), f"{_stamp(at(12.6))}\t{rid}\twrote 2,000 rows to orders"),
        (at(27.9), f"{_stamp(at(27.9))}\t{rid}\twrote 4,000 rows to orders"),
        (at(41.0), f"{_stamp(at(41.0))}\t{rid}\tdone: 4,812 rows"),
        (at(41.0), f"END RequestId: {rid}"),
        (at(41.0), f"REPORT RequestId: {rid}\tDuration: 41002.31 ms\tBilled Duration: 41003 ms\tMemory Size: 1024 MB"
                   "\tMax Memory Used: 412 MB"),
    ]


def bad_answer_run(ended: datetime) -> list[tuple[datetime, str]]:
    """
    The orders-etl run behind the newest 502: it raised, so API Gateway had no answer to pass on.
    """
    start = ended - timedelta(seconds=1.2)
    at = lambda seconds: start + timedelta(seconds=seconds)  # noqa: E731
    rid = BAD_ANSWER_RUN
    return [
        (at(0), f"START RequestId: {rid} Version: $LATEST"),
        (at(1.1), f"[ERROR]\t{_stamp(at(1.1))}\t{rid}\tKeyError: 'customer_id'\nTraceback (most recent call last):\n"
                  "  File \"/var/task/etl.py\", line 22, in handler\n    row[\"customer_id\"]"),
        (at(1.2), f"END RequestId: {rid}"),
        (at(1.2), f"REPORT RequestId: {rid}\tDuration: 1190.00 ms\tBilled Duration: 1191 ms\tMemory Size: 1024 MB"
                  "\tMax Memory Used: 151 MB"),
    ]


def _orders_line(when: datetime, n: int, route: tuple[str, str], status: int, took: float,
                 **extra: Any) -> tuple[datetime, str]:
    method, path = route
    record = {"requestId": f"{n:08x}-1d2e-4f3a-9b8c-7d6e5f4a{n % 10_000:04d}", "ip": f"203.0.113.{n % 200 + 1}",
              "user": f"u-{n % 97:04x}", "httpMethod": method, "resourcePath": path, "status": str(status),
              "responseLatency": str(round(took)), "integrationLatency": str(round(max(0.0, took - 14))),
              "authorizerLatency": "12", "integrationRequestId": f"{n:08x}-aaaa-4bbb-8ccc-{n:012x}",
              "error": "-", "integrationError": "-"}
    record.update({key: str(value) for key, value in extra.items()})
    return when, json.dumps(record, separators=(",", ":"))


def orders_log_lines(now: datetime) -> list[tuple[datetime, str]]:
    """
    orders-api's prod access log over the last 24 hours: calls that went fine, 504s on POST /orders (the newest names
    TIMEOUT_RUN), a 502 (BAD_ANSWER_RUN), expired tokens and throttled callers.
    """
    rng = random.Random(7)
    lines = []
    routes = [("GET", "/orders/{id}"), ("GET", "/orders/{id}"), ("GET", "/orders"), ("POST", "/orders"),
              ("DELETE", "/orders/{id}"), ("GET", "/health")]
    for n in range(240):
        when = now - timedelta(minutes=rng.uniform(5, 1_440))
        route = rng.choice(routes)
        took = {"POST": rng.uniform(2_000, 9_000), "/health": rng.uniform(2, 9)}.get(route[0], rng.uniform(15, 400))
        took = rng.uniform(2, 9) if route[1] == "/health" else took
        lines.append(_orders_line(when, n, route, 200, took))
    newest_timeout, newest_bad = _newest(now)
    for n in range(36):
        when = newest_timeout - timedelta(minutes=n * 37)
        extra = {"error": "Endpoint request timed out"}
        if n == 0:
            extra["integrationRequestId"] = TIMEOUT_RUN
        lines.append(_orders_line(when, 1_000 + n, ("POST", "/orders"), 504, 29_000, **extra))
    lines.append(_orders_line(newest_bad, 2_000, ("POST", "/orders"), 502, 1_204, integrationRequestId=BAD_ANSWER_RUN,
                              error="Internal server error", integrationError="Malformed Lambda proxy response"))
    for n in range(14):
        lines.append(_orders_line(now - timedelta(minutes=12 + n * 90), 3_000 + n, ("GET", "/orders"), 401, 4,
                                  integrationLatency="-", integrationRequestId="-", error="Unauthorized"))
    for n in range(6):
        lines.append(_orders_line(now - timedelta(hours=6, minutes=n), 4_000 + n, ("GET", "/orders/{id}"), 429, 2,
                                  integrationLatency="-", integrationRequestId="-", error="Too Many Requests"))
    return lines


def search_log_lines(now: datetime) -> list[tuple[datetime, str]]:
    """
    search-api's access log in HTTP APIs' default format: no backend time and no Lambda request ID.
    """
    rng = random.Random(11)
    lines = []
    for n in range(120):
        when = now - timedelta(minutes=rng.uniform(1, 1_440))
        status = 404 if n % 25 == 0 else 200
        record = {"requestId": f"S{n:07d}=", "ip": f"198.51.100.{n % 200 + 1}", "requestTime": when.strftime(
            "%d/%b/%Y:%H:%M:%S +0000"), "httpMethod": "GET", "routeKey": rng.choice(["GET /search", "GET /suggest"]),
            "status": str(status), "protocol": "HTTP/1.1", "responseLength": str(rng.randint(200, 9000))}
        lines.append((when, json.dumps(record, separators=(",", ":"))))
    return lines


def cloudwatch(ids: dict[str, str]) -> FakeCloudWatch:
    """
    FakeCloudWatch over traffic(), knowing the HTTP and WebSocket APIs by their IDs.
    """
    return FakeCloudWatch(traffic(), {api_id: name for name, api_id in ids.items()})


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
