import asyncio
import html as html_lib
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError
from fake_apigateway import (
    BAD_ANSWER_RUN, ORDERS_FORMAT, SEARCH_FORMAT, TIMEOUT_RUN, cloudwatch, function_uri, now_utc, seed,
)
from moto import mock_aws

from aws_analyzer import apigateway as apimod
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.view import _commands
from aws_analyzer.apigateway import (
    APIGATEWAY_PRICES, APIGatewayAnalyzer, APIGatewayExplorer, APIGatewayView, Api, ApiNumbers, Call,
    ErrorGroup, ErrorReport, Integration, Route, RouteNumbers, Stage, curl_command, error_findings, group_errors,
    curl_lines, invoke_url, lambda_of_uri, latency_split, missing_fields, monthly_cost, numbers_findings, open_route_findings,
    parse_access_line, parse_api_ref, parse_rest_routes, parse_v2_routes, read_format, route_findings,
    route_numbers_from_calls, service_action, stage_findings,
)
from aws_analyzer.apigateway.explorer.frame import _window_text
from aws_analyzer.apigateway.explorer.routes import _route_tree
from aws_analyzer.lambda_functions import LambdaAnalyzer


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)



def test_api_references_are_read_however_they_come():
    assert parse_api_ref("orders-api") == ("orders-api", None)
    assert parse_api_ref("orders-api/prod") == ("orders-api", "prod")
    assert parse_api_ref("https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/prod/orders") == ("a1b2c3d4e5", "prod")
    assert parse_api_ref("wss://abc123.execute-api.eu-west-1.amazonaws.com/live") == ("abc123", "live")
    console = "https://us-east-1.console.aws.amazon.com/apigateway/main/apis/a1b2c3d4e5/resources?api=a1b2c3d4e5"
    assert parse_api_ref(console) == ("a1b2c3d4e5", None)
    with pytest.raises(ValueError, match="Name an API"):
        parse_api_ref(" ")


def test_integrations_say_what_they_call():
    assert lambda_of_uri(function_uri("orders-etl")) == ("orders-etl", "us-east-1")
    assert lambda_of_uri("arn:aws:lambda:eu-west-1:1:function:search-q:live") == ("search-q", "eu-west-1")
    assert lambda_of_uri("https://hooks.acme.io") == ("", "")
    assert service_action("arn:aws:apigateway:us-east-1:dynamodb:action/PutItem") == "DynamoDB PutItem"
    assert Integration("AWS_PROXY", function="orders-etl").target == "λ orders-etl"
    assert Integration("MOCK").target == "mock" and Integration("MOCK").kind == "mock"
    assert Integration("AWS_PROXY", subtype="SQS-SendMessage").target == "SQS SendMessage"
    assert Integration("HTTP_PROXY", uri="https://hooks.acme.io/in").kind == "HTTP"
    assert invoke_url("abc", "us-east-1", "prod") == "https://abc.execute-api.us-east-1.amazonaws.com/prod"
    assert invoke_url("abc", "us-east-1", "$default", "HTTP") == "https://abc.execute-api.us-east-1.amazonaws.com"
    assert invoke_url("abc", "us-east-1", "prod", "WEBSOCKET").startswith("wss://")


def test_who_can_call_a_route():
    assert Route("GET", "/orders").is_open
    assert not Route("OPTIONS", "/orders").is_open
    assert not Route("GET", "/health", integration=Integration("MOCK")).is_open
    assert not Route("GET", "/orders", api_key_required=True).is_open
    assert Route("GET", "/orders", api_key_required=True).auth == "API key"
    assert Route("GET", "/x", auth_type="AWS_IAM").auth_kind == "IAM"
    socket = Api("w", "chat", "WEBSOCKET", routes=[Route("", "$connect"), Route("", "sendMessage")])
    assert [r.key for r in socket.open_routes] == ["$connect"]


def test_the_main_stage_and_a_route_by_its_key():
    old, new = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)
    api = Api("a", "orders", "REST", region="us-east-1", stages=[Stage("dev", updated=new), Stage("beta", updated=old)],
              routes=[Route("GET", "/orders"), Route("POST", "/orders/{id}")])
    assert api.stage(None).name == "dev" and api.stage("beta").name == "beta" and api.stage("nope") is None
    api.stages.append(Stage("prod"))
    assert api.stage(None).name == "prod"
    assert api.route("GET  /orders").key == "GET /orders" and api.route("/orders/{id}").method == "POST"
    assert api.url() == "https://a.execute-api.us-east-1.amazonaws.com/prod"


def test_a_stage_left_at_the_accounts_rate_isnt_throttled():
    assert not Stage("prod").is_throttled
    assert not Stage("prod", throttle_rate=10_000).is_throttled
    assert Stage("prod", throttle_rate=500).is_throttled


def test_rest_resources_become_routes():
    resources = [{"id": "r1", "path": "/orders", "resourceMethods": {
        "POST": {"authorizationType": "COGNITO_USER_POOLS", "authorizerId": "au", "methodIntegration": {
            "type": "AWS_PROXY", "uri": function_uri("orders-etl"), "timeoutInMillis": 29000}},
        "OPTIONS": {"authorizationType": "NONE", "methodIntegration": {"type": "MOCK"}}}}, {"id": "root", "path": "/"}]
    from aws_analyzer.apigateway import Authorizer
    routes = parse_rest_routes(resources, {"au": Authorizer("au", "acme-users", "COGNITO_USER_POOLS")})
    post = next(r for r in routes if r.method == "POST")
    assert post.auth == "Cognito acme-users" and post.integration.function == "orders-etl"
    assert post.integration.timeout_ms == 29000 and len(routes) == 2


def test_http_routes_find_their_integration():
    integrations = {"i1": Integration("AWS_PROXY", function="search-q")}
    routes = parse_v2_routes([{"RouteKey": "GET /search", "Target": "integrations/i1", "RouteId": "r"},
                              {"RouteKey": "$default", "AuthorizationType": "AWS_IAM"}], integrations, {})
    assert routes[0].key == "GET /search" and routes[0].integration.function == "search-q"
    assert routes[1].key == "$default" and routes[1].integration is None and routes[1].auth == "IAM"


def test_routes_make_a_tree_of_their_paths():
    routes = [Route("GET", "/orders/{id}"), Route("POST", "/orders"), Route("GET", "/orders"), Route("GET", "/health")]
    tree = [(line.depth, line.folder or f"{line.route.method} {line.leaf}") for line in _route_tree(routes)]
    assert tree == [(0, "GET /health"), (0, "/orders"), (1, "GET "), (1, "POST "), (1, "GET /{id}")]
    sockets = [(line.depth, line.leaf) for line in _route_tree([Route("", "$connect"), Route("", "sendMessage")])]
    assert sockets == [(0, "$connect"), (0, "sendMessage")]


def test_curl_carries_what_the_route_asks_for():
    api = Api("a", "orders", "REST", region="us-east-1", stages=[Stage("prod")])
    assert curl_command(api, Route("GET", "/health")) == "curl -sS https://a.execute-api.us-east-1.amazonaws.com/prod/health"
    post = curl_command(api, Route("POST", "/orders", auth_type="COGNITO_USER_POOLS", api_key_required=True))
    assert '-X POST -H "Authorization: $TOKEN" -H "x-api-key: $API_KEY"' in post and "-d '{}'" in post
    iam = curl_command(api, Route("ANY", "/admin/{proxy+}", auth_type="AWS_IAM"))
    assert "--aws-sigv4" in iam and iam.endswith("/prod/admin/<proxy>") and "-X" not in iam
    assert curl_lines(api, Route("POST", "/orders")).splitlines()[0] == "curl -sS -X POST \\"
    socket = Api("w", "chat", "WEBSOCKET", region="us-east-1", stages=[Stage("prod")])
    assert curl_command(socket, Route("", "$connect", auth_type="CUSTOM")).startswith("wscat -c wss://")



def test_a_json_access_log_is_read_by_its_format():
    fmt = read_format(ORDERS_FORMAT)
    assert fmt.is_json and fmt.keys["lambda_request"] == "integrationRequestId" and missing_fields(fmt) == []
    line = ('{"requestId":"r1","httpMethod":"POST","resourcePath":"/orders","status":"504","responseLatency":"29001",'
            '"integrationLatency":"28987","authorizerLatency":"12","integrationRequestId":"l1",'
            '"error":"Endpoint request timed out","integrationError":"-"}')
    call = parse_access_line(line, NOW, fmt)
    assert call.route_key == "POST /orders" and call.status == 504 and call.failed
    assert call.integration_ms == 28987 and call.lambda_request == "l1" and call.why == "Endpoint request timed out"
    assert [part for part, _ in latency_split(call)] == ["authorizer", "integration", "API Gateway"]


def test_a_json_format_with_its_own_key_names():
    fmt = read_format('{"id":"$context.requestId","code":"$context.status","took":"$context.responseLatency",'
                      '"ctx":{"rk":"$context.routeKey"}}')
    call = parse_access_line('{"id":"r9","code":"404","took":"7","ctx":{"rk":"GET /nope"}}', NOW, fmt)
    assert (call.request_id, call.status, call.latency_ms, call.route_key) == ("r9", 404, 7, "GET /nope")
    default = read_format(SEARCH_FORMAT)
    assert "$context.integration.latency" in missing_fields(default)


def test_a_text_access_log_is_read_by_its_format():
    clf = ('$context.identity.sourceIp $context.identity.caller $context.identity.user [$context.requestTime] '
           '"$context.httpMethod $context.resourcePath $context.protocol" $context.status $context.responseLength '
           '$context.requestId')
    fmt = read_format(clf)
    assert not fmt.is_json
    call = parse_access_line('203.0.113.9 - - [01/Nov/2026:12:00:00 +0000] "GET /orders HTTP/1.1" 502 36 abc-123',
                             NOW, fmt)
    assert (call.ip, call.method, call.route_key, call.status, call.request_id) == (
        "203.0.113.9", "GET", "GET /orders", 502, "abc-123")
    assert parse_access_line("something else", NOW, fmt) is None


def test_failed_calls_group_by_cause_and_routes_get_numbers():
    calls = [Call(NOW - timedelta(minutes=i), f"r{i}", "POST", route="/orders", status=504, latency_ms=29_000,
                  error="Endpoint request timed out") for i in range(3)]
    calls += [Call(NOW, "ok", "GET", route="/orders", status=200, latency_ms=50),
              Call(NOW, "bad", "GET", route="/orders", status=401, latency_ms=4, error="Unauthorized")]
    groups = group_errors(calls)
    assert [(g.status, g.count) for g in groups] == [(504, 3), (401, 1)] and groups[0].newest.request_id == "r0"
    numbers = route_numbers_from_calls(calls)
    assert numbers["POST /orders"].errors_5xx == 3 and numbers["POST /orders"].p95_ms == 29_000
    assert numbers["GET /orders"].calls == 2 and numbers["GET /orders"].errors_4xx == 1



def test_numbers_findings_name_the_problem_and_the_next_step():
    api = Api("a", "orders-api", "REST")
    numbers = ApiNumbers(days=7, requests=10_000, errors_5xx=300, errors_4xx=2_000, p95_ms=26_000)
    text = " ".join(message for _, message in numbers_findings(api, numbers))
    assert "5xx on 3.0% of calls" in text and "errors('orders-api')" in text
    assert "refuses 20.0% of calls" in text and "close to its time limit" in text
    assert numbers_findings(api, ApiNumbers(days=7, requests=10_000, errors_5xx=5)) == []


def test_routes_that_run_out_of_time_and_routes_open_to_anyone():
    route = Route("POST", "/orders", auth_type="COGNITO_USER_POOLS",
                  integration=Integration("AWS_PROXY", function="orders-etl", timeout_ms=29_000))
    api = Api("a", "orders-api", "REST", routes=[route, Route("GET", "/partners")])
    found = route_findings(api, {"POST /orders": RouteNumbers(calls=1_000, errors_5xx=100, p95_ms=29_000)})
    assert [level for level, _ in found] == ["warn", "warn"] and "runs out of time" in found[0][1]
    assert "λ orders-etl may still be working" in found[0][1]
    assert "GET /partners" in open_route_findings(api)[0][1]


def test_stage_findings_show_the_command_that_fixes_them():
    api = Api("abc", "orders-api", "HTTP", region="us-east-1")
    found = dict((m.split(":")[0], m) for _, m in stage_findings(api, Stage("prod", data_trace=True)))
    assert "aws apigatewayv2 update-stage --api-id abc --stage-name prod" in found["Stage prod keeps no access log"]
    assert "Stage prod has no throttle of its own" in found
    assert any(level == "warn" for level, _ in stage_findings(api, Stage("prod", data_trace=True)))


def test_error_findings_explain_what_a_status_means():
    route = Route("POST", "/orders", integration=Integration("AWS_PROXY", function="orders-etl"))
    api = Api("a", "orders-api", "REST", routes=[route])
    stage = Stage("prod", access_log_group="/g", access_log_format=ORDERS_FORMAT)
    timeout = Call(NOW, "r1", "POST", route="/orders", status=504, lambda_request="l1")
    report = ErrorReport(api, stage, NOW, page=apimod.CallPage(api, stage, NOW, NOW, calls=[timeout]),
                         groups=[ErrorGroup(504, "Endpoint request timed out", "POST /orders", 1, timeout)])
    text = error_findings(report)[0][1]
    assert "times out" in text and "LambdaView().logs('orders-etl', request_id='l1')" in text
    missing = ErrorReport(api, Stage("dev"), NOW, numbers=ApiNumbers(days=1, errors_5xx=4, errors_4xx=2))
    assert "keeps no access log" in error_findings(missing)[0][1] and "4 5xx and 2 4xx" in error_findings(missing)[0][1]


def test_monthly_cost_is_requests_at_the_kinds_price_and_the_cache():
    numbers = ApiNumbers(days=7, requests=7_000_000)
    rest = Api("a", "r", "REST", stages=[Stage("prod", cache_enabled=True, cache_size="0.5")])
    assert monthly_cost(rest, numbers, APIGATEWAY_PRICES) == pytest.approx(30_000_000 * 0.0000035 + 0.02 * 730)
    assert monthly_cost(Api("b", "h", "HTTP"), numbers, APIGATEWAY_PRICES) == pytest.approx(30.0)
    assert monthly_cost(Api("b", "h", "HTTP"), None, APIGATEWAY_PRICES) is None
    assert APIGATEWAY_PRICES["rest_request"] == 0.0000035



@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def world(aws):
    now = now_utc()
    ids = seed(now)
    return ids, cloudwatch(ids)


@pytest.fixture
def core(world):
    ids, metrics = world
    analyzer = APIGatewayAnalyzer(region="us-east-1", clients={"cloudwatch": metrics},
                                  lambda_core=LambdaAnalyzer(region="us-east-1"))
    analyzer.max_workers = 1
    return analyzer


def test_every_kind_of_api_is_listed_and_found(core, world):
    ids, _ = world
    kinds = {a.name: a.kind for a in core.list_apis()}
    assert kinds == {"orders-api": "REST", "search-api": "HTTP", "partner-api": "HTTP", "chat-ws": "WEBSOCKET",
                     "reports-api": "REST", "legacy-v1": "REST"}
    assert core.resolve("orders-api").id == ids["orders-api"] == core.resolve(ids["orders-api"]).id
    url = f"https://{ids['search-api']}.execute-api.us-east-1.amazonaws.com"
    assert core.resolve(url).name == "search-api"
    with pytest.raises(_Hint, match="Did you mean 'orders-api'"):
        core.resolve("order-api")


def test_an_api_is_read_with_its_routes_stages_and_authorizers(core):
    orders = core.api("orders-api")
    assert {r.key for r in orders.routes} == {"GET /orders", "POST /orders", "GET /orders/{id}",
                                              "DELETE /orders/{id}", "GET /health"}
    assert orders.route("POST /orders").auth == "Cognito acme-users"
    assert orders.route("DELETE /orders/{id}").integration.target == "DynamoDB DeleteItem"
    prod = orders.stage("prod")
    assert prod.has_access_log and prod.is_throttled and prod.tracing and prod.detailed_metrics
    assert prod.access_log_group == "/aws/apigateway/orders-api" and not orders.stage("dev").has_access_log
    partner = core.api("partner-api")
    assert {r.key for r in partner.open_routes} == {"GET /partners", "POST /webhook"}
    assert partner.route("GET /status").integration.target == "https://status.acme.io"
    chat = core.api("chat-ws")
    assert chat.route("$connect").auth == "Lambda authorizer chat-token" and chat.open_routes == []
    assert core.api("reports-api").stage("prod").cache_enabled


def test_the_overview_has_every_api_with_its_numbers(core):
    overview = core.overview()
    assert len(overview.apis) == 6 and overview.metrics_read > 0 and not overview.errors
    orders = next(a for a in overview.apis if a.name == "orders-api")
    numbers = overview.numbers[orders.id]
    assert numbers.requests > 2_000_000 and numbers.errors_5xx > 60_000 and len(numbers.daily_requests) == 7
    assert numbers.p95_ms == 24_400


def test_a_report_has_the_stages_numbers_and_each_routes(core):
    report = core.report("orders-api")
    assert report.stage.name == "prod" and report.route_numbers["POST /orders"].p95_ms == 29_000
    assert core.report("orders-api/dev").route_numbers == {}
    with pytest.raises(_Hint, match="has no stage 'qa'"):
        core.report("orders-api", stage="qa")


def test_calls_are_read_from_the_access_log_newest_first(core):
    page = core.calls("orders-api")
    assert len(page.calls) > 250 and page.calls[0].time >= page.calls[-1].time and page.unread == 0
    timeouts = [c for c in page.calls if c.status == 504]
    assert len(timeouts) == 36 and timeouts[0].lambda_request == TIMEOUT_RUN
    assert core.calls("orders-api", pattern=f'"{TIMEOUT_RUN}"').calls[0].status == 504
    assert core.calls("partner-api").note == "no access log"


def test_errors_group_the_failed_calls(core):
    report = core.errors("orders-api")
    assert report.groups[0].status == 504 and report.groups[0].count == 36
    assert report.numbers.errors_5xx > 0 and report.page is not None
    assert core.errors("partner-api").page is None


def test_the_lambda_run_behind_a_call_is_read_by_its_request_id(core):
    api = core.api("orders-api")
    page = core.calls("orders-api")
    timeout = next(c for c in page.calls if c.lambda_request == TIMEOUT_RUN)
    run = core.lambda_run(api, timeout)
    assert run.found and run.function == "orders-etl" and run.duration_ms == pytest.approx(41002.31)
    assert "done: 4,812 rows" in " ".join(line.text for line in run.lines)
    bad = core.lambda_run(api, next(c for c in page.calls if c.lambda_request == BAD_ANSWER_RUN))
    assert any(line.is_error for line in bad.lines)
    refused = next(c for c in page.calls if c.status == 401)
    assert "doesn't record the Lambda request ID" in core.lambda_run(api, refused).note
    health = Call(NOW, "h", "GET", route="/health", status=200)
    assert "doesn't call a Lambda function" in core.lambda_run(api, health).note


def test_numbers_that_cant_be_read_become_a_note(core, world):
    _, metrics = world
    metrics.denied.add("get_metric_data")
    assert core.overview().errors["metrics"] == "AccessDeniedException"
    assert core.report("orders-api").errors["metrics"] == "AccessDeniedException"



def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


@pytest.fixture
def ui(core):
    return APIGatewayView(core, mode="text", progress="off")


def test_help_lists_every_command(ui, capsys):
    assert set(_commands(APIGatewayView)) == {n for names in APIGatewayView._GROUPS.values() for n in names}
    out = run(capsys, ui.help)
    assert "apis()" in out and "Every API in the region" in out


def test_apis_puts_the_answer_first(ui, capsys):
    out = run(capsys, ui.apis)
    assert "API Gateway in us-east-1" in out and "6 APIs · 3 REST, 2 HTTP, 1 WebSocket" in out
    assert "orders-api returns 5xx" in out and "partner-api has 2 routes anyone can call" in out
    assert "legacy-v1 had no calls" in out and "aws apigateway delete-rest-api" in out
    assert "errors('orders-api')" in out and "none ×2" in out


def test_api_info_shows_stages_routes_and_findings(ui, capsys):
    out = run(capsys, ui.api_info, "orders-api")
    assert "REST API" in out and "POST /orders runs out of time" in out and "Routes, the busiest first" in out
    assert "/aws/apigateway/orders-api" in out and "Stage: " not in out
    assert "Stage dev" not in out
    dev = run(capsys, ui.api_info, "orders-api/dev")
    assert "Stage dev keeps no access log" in dev and "Stage dev has no throttle of its own" in dev


def test_routes_and_stages(ui, capsys):
    out = run(capsys, ui.routes, "partner-api")
    assert "Open to anyone: 2 (!)" in out and "curl -sS" in out and "partner-jwt" in out
    assert "Lambda 4" in out and "HTTP 2" in out
    stages = run(capsys, ui.stages, "orders-api")
    assert "prod's access log format" in stages and "$context.requestId" in stages


def test_calls_errors_and_performance(ui, capsys):
    out = run(capsys, ui.calls, "orders-api", status="5xx", n=5)
    assert "Calls, newest first (5 of 37)" in out and "Endpoint request timed out" in out
    errors = run(capsys, ui.errors, "orders-api")
    assert "POST /orders times out" in errors and f"request_id='{TIMEOUT_RUN}'" in errors
    assert "Malformed Lambda proxy response" in errors
    perf = run(capsys, ui.performance, "orders-api")
    assert "How long orders-api's calls take" in perf and "Where the slowest call's" in perf
    assert "hit API Gateway's 29s limit" in perf
    search = run(capsys, ui.calls, "search-api")
    assert "doesn't log $context.integration.latency" in search
    with pytest.raises(ValueError, match="status= is"):
        ui._calls_blocks(ui.core.calls("orders-api"), status="bad")


def test_a_stage_without_an_access_log_says_how_to_turn_it_on(ui, capsys):
    out = run(capsys, ui.calls, "partner-api")
    assert "keeps no access log" in out and "aws apigatewayv2 update-stage" in out
    errors = run(capsys, ui.errors, "partner-api")
    assert "only CloudWatch's counts" in errors


def test_mistakes_become_notes_not_tracebacks(ui, capsys):
    out = run(capsys, ui.api_info, "order-api")
    assert "No API named 'order-api'" in out and "Traceback" not in out
    assert "needs Jupyter" in run(capsys, ui.explore)
    assert "has no stage 'qa'" in run(capsys, ui.calls, "orders-api", stage="qa")


def test_an_empty_region(aws, capsys):
    view = APIGatewayView(APIGatewayAnalyzer(region="eu-west-3"), mode="text", progress="off")
    assert "No APIs in eu-west-3" in run(capsys, view.apis)



def explorer(core, api=None, **kwargs):
    return APIGatewayExplorer(api, core=core, mode="widgets", **kwargs)


def plain(value):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def test_window_wording():
    assert _window_text("See why with errors('orders-api').") == "See why with the Calls tab."
    assert _window_text("the Routes tab shows them") == "The Routes tab shows them"
    lambda_call = "LambdaView().logs('orders-etl', request_id='x')"
    assert _window_text(f"Read {lambda_call}.") == f"Read {lambda_call}."


def test_explorer_opens_the_api_failing_most(core):
    x = explorer(core)
    assert x.api.name == "orders-api" and x.stage.name == "prod" and x._tab == "apis"
    assert [line.api.name for line in x.apis_shown][0] == "orders-api"
    assert "API Gateway explorer" in x.title.value and "Requests" in x.stats.value
    assert plain(x.status.value) == "297 calls in the last 24 hours · 57 failed"


def test_explorer_apis_tab_searches_filters_and_sorts(core):
    x = explorer(core)
    x.api_find.value = "partner"
    assert [line.api.name for line in x.apis_shown] == ["partner-api"]
    x.api_find.value = ""
    open_chip = next(c for c in x.api_chips.children if c.description.startswith("Open routes"))
    open_chip.click()
    assert [line.api.name for line in x.apis_shown] == ["partner-api"]
    open_chip.click()
    x.column_buttons["name"].click()
    assert [line.api.name for line in x.apis_shown][0] == "chat-ws"
    x._api_rows[0].button.click()
    assert x.api.name == "chat-ws" and x._tab == "overview"


def test_explorer_routes_tab(core):
    x = explorer(core, "orders-api", tab="routes")
    assert x._tab == "routes" and x.route_key == "POST /orders"
    kinds = [type(b).__name__ for b in x.shown["routes"]]
    assert kinds[:2] == ["_Title", "_Path"] and "runs out of time" in x.route_view.value
    line = next(row for row in x._route_rows if row.key == "GET /health")
    line.button.click()
    assert x.route_key == "GET /health" and "curl -sS" in x.route_view.value
    x.route_find.value = "delete"
    assert [row.key for row in x._route_rows if row.box in x.route_rows.children] == ["DELETE /orders/{id}"]
    x.route("GET /orders")
    assert x.route_key == "GET /orders"


def test_explorer_calls_tab_opens_a_call_and_its_lambda_run(core):
    x = explorer(core, "orders-api", tab="calls")
    assert x.page is not None and "504 ×36" in plain(x.timeline.value)
    chip = next(c for c in x.call_chips.children if c.description.startswith("5xx"))
    chip.click()
    assert all(c.status >= 500 for c in x.calls_shown) and len(x.calls_shown) == 37
    x._call_rows[0].button.click()
    assert x.run is not None and x.run.found and "Where the" in x.call_view.value
    assert "done: 4,812 rows" in x.call_view.value
    chip.click()
    x.call_find.value = "Unauthorized"
    assert len(x.calls_shown) == 14 and all(c.status == 401 for c in x.calls_shown)
    x.call("nope")
    assert "No call with request ID nope" in plain(x.status.value)


def test_explorer_performance_and_stages_tabs(core):
    x = explorer(core, "orders-api")
    x._show_tab("performance")
    assert "How long orders-api" in x.performance_view.value and "_TimeSplit" in [
        type(b).__name__ for b in x.shown["performance"]]
    x._show_tab("stages")
    assert "Stage dev keeps no access log" in plain(x.stages_view.value)


def test_explorer_switches_stage_and_api(core):
    x = explorer(core, "orders-api")
    x.stage_pick.value = "dev"
    assert x.stage.name == "dev" and x.page.note == "no access log"
    assert "keeps no access log" in plain(x.status.value)
    x.open("partner-api", tab="routes")
    assert x.api.name == "partner-api" and x._tab == "routes" and x.route_key == "GET /partners"
    x.field._clicked(core.resolve("search-api").id)
    assert x.api.name == "search-api" and x.stage.name == "$default"


def test_explorer_starts_where_asked_and_refuses_what_it_hasnt(core):
    with pytest.raises(ValueError, match="tab is one of"):
        explorer(core, tab="logs")
    with pytest.raises(ValueError, match="since is one of"):
        explorer(core, since="3d")
    x = explorer(core, "orders-api/dev")
    assert x.stage.name == "dev"
    x.refresh()
    assert x.api.name == "orders-api"


def test_explorer_says_when_the_apis_cant_be_listed(world):
    _, metrics = world

    class Refusing(APIGatewayAnalyzer):
        def list_apis(self):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetRestApis")

    x = explorer(Refusing(region="us-east-1", clients={"cloudwatch": metrics}))
    assert "Couldn't list the APIs" in plain(x.status.value) and x.api is None


def test_explorer_without_widgets_shows_reports(core, capsys):
    APIGatewayExplorer("orders-api", core=core, mode="text")
    assert "orders-api" in capsys.readouterr().out
    html_view = APIGatewayView(core, mode="html")
    html_view._show = lambda blocks: None
    html_view.explore("partner-api")
    assert html_view.explorer.api.name == "partner-api"


def test_explorer_reads_in_the_background(core, world):
    _, metrics = world
    hold = metrics.holds["get_metric_data"] = threading.Event()

    async def settle(x):
        while x._tasks:
            await asyncio.gather(*list(x._tasks.values()))

    async def main():
        x = explorer(core, "orders-api")
        assert list(x._tasks) == ["list"] and x.api is None
        assert "Reading the APIs" in plain(x.status.value)
        hold.set()
        await settle(x)
        assert x.api.name == "orders-api" and x.page is not None
        timeout = next(c for c in x.page.calls if c.lambda_request == TIMEOUT_RUN)
        x._show_tab("calls")
        x.call(timeout.request_id)
        assert "run" in x._tasks
        await settle(x)
        assert x.run.found

    asyncio.run(main())
