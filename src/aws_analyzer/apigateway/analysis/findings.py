"""
What needs attention in an API, as ("warn" | "info", message) pairs: each says what's wrong, why it matters and the
next step (a command to run, a setting to change or the AWS CLI command to copy). Pure: they read what the analyzer
returned, so they run on any Api / ApiNumbers you build.
"""

from __future__ import annotations

from ..._kit.fmt import _duration, _plural, human_money
from ..._kit.render import _call

from ..helpers import ACCOUNT_RATE_LIMIT
from ..models import Api, ApiNumbers, ApiReport, Call, ErrorGroup, ErrorReport, Overview, Route, RouteNumbers, Stage
from .access_log import missing_fields, read_format, suggested_format
from .cost import monthly_cost


SERVER_ERROR_RATE = 0.01

CLIENT_ERROR_RATE = 0.10

FEWEST_ERRORS = 10

SLOW_SHARE = 0.8

ROUTE_ERROR_RATE = 0.05

ROUTES_NAMED = 3

GROUPS_EXPLAINED = 5


def _seconds(ms: float) -> str:
    return _duration(ms / 1000)


def _names(routes: list[str]) -> str:
    shown = ", ".join(routes[:ROUTES_NAMED])
    more = len(routes) - ROUTES_NAMED
    return f"{shown} and {more:,} more" if more > 0 else shown


def delete_command(api: Api) -> str:
    """
    The AWS CLI command that deletes an API (shown, never run).
    """
    if api.kind == "REST":
        return f"aws apigateway delete-rest-api --rest-api-id {api.id} --region {api.region}"
    return f"aws apigatewayv2 delete-api --api-id {api.id} --region {api.region}"


def access_log_command(api: Api, stage: Stage) -> str:
    """
    The AWS CLI command that turns a stage's access log on, in a format the Calls reports read fully (shown, never
    run; the log group must exist).
    """
    group = f"arn:aws:logs:{api.region}:<account>:log-group:/aws/apigateway/{api.name}"
    fmt = suggested_format(api.kind).replace("'", "")
    if api.kind == "REST":
        return (f"aws apigateway update-stage --rest-api-id {api.id} --stage-name {stage.name} --patch-operations "
                f"op=replace,path=/accessLogSettings/destinationArn,value={group} "
                f"'op=replace,path=/accessLogSettings/format,value={fmt}'")
    return (f"aws apigatewayv2 update-stage --api-id {api.id} --stage-name {stage.name} --access-log-settings "
            f"'DestinationArn={group},Format={fmt}'")


def numbers_findings(api: Api, numbers: ApiNumbers | None) -> list[tuple[str, str]]:
    """
    What an API's CloudWatch numbers say: too many 5xx (its backend fails), many 4xx (callers are refused), or
    requests close to the time limit.
    """
    if numbers is None or not numbers.requests:
        return []
    findings: list[tuple[str, str]] = []
    window = f"{_plural(numbers.days, 'day')}"
    if numbers.errors_5xx >= FEWEST_ERRORS and numbers.rate_5xx >= SERVER_ERROR_RATE:
        findings.append(("warn", f"{api.name} returns 5xx on {numbers.rate_5xx:.1%} of calls: {numbers.errors_5xx:,} "
                                 f"server errors in {window}, so its callers got errors instead of answers. See "
                                 f"which routes and why with {_call('errors', api.name)}."))
    if numbers.errors_4xx >= FEWEST_ERRORS and numbers.rate_4xx >= CLIENT_ERROR_RATE:
        findings.append(("info", f"{api.name} refuses {numbers.rate_4xx:.1%} of calls with a 4xx: "
                                 f"{numbers.errors_4xx:,} in {window}, often expired tokens, a missing API key or a "
                                 f"path that doesn't exist. See which with {_call('errors', api.name)}."))
    limit = api.max_timeout_ms
    if numbers.p95_ms is not None and numbers.p95_ms >= SLOW_SHARE * limit:
        findings.append(("warn", f"{api.name} is close to its time limit: 5% of calls take {_seconds(numbers.p95_ms)} "
                                 f"or more, and API Gateway gives up at {_seconds(limit)} with a 504. Find the slow "
                                 f"routes with {_call('performance', api.name)}."))
    return findings


def open_route_findings(api: Api) -> list[tuple[str, str]]:
    """
    Routes anyone with the URL can call: no authorizer, no IAM and no API key.
    """
    routes = [r.key for r in api.open_routes]
    if not routes:
        return []
    how = ("an authorizer on $connect" if api.kind == "WEBSOCKET" else
           "an IAM, Cognito, JWT or Lambda authorizer, or an API key with a usage plan")
    return [("warn", f"{api.name} has {_plural(len(routes), 'route')} anyone can call: {_names(routes)} "
                     f"{'has' if len(routes) == 1 else 'have'} no authorizer, so anyone with the URL gets an "
                     f"answer. Add {how} unless it's meant to be public (see {_call('routes', api.name)}).")]


def idle_findings(api: Api, numbers: ApiNumbers | None) -> list[tuple[str, str]]:
    """
    An API no one called in the days read: it may be left over.
    """
    if numbers is None or numbers.requests or not api.stages:
        return []
    return [("info", f"{api.name} had no calls in {_plural(numbers.days, 'day')}: it still has "
                     f"{_plural(len(api.stages), 'stage')} deployed. Delete it if nothing uses it "
                     f"({delete_command(api)}).")]


def stage_findings(api: Api, stage: Stage | None) -> list[tuple[str, str]]:
    """
    What a stage's settings risk: no throttle of its own, no access log, or request bodies written to the
    execution log.
    """
    if stage is None:
        return []
    findings: list[tuple[str, str]] = []
    if not stage.is_throttled:
        findings.append(("info", f"Stage {stage.name} has no throttle of its own: one busy caller can use the "
                                 f"account's {ACCOUNT_RATE_LIMIT:,} requests a second, which every API in the region "
                                 "shares. Set a default rate and burst in the stage's settings."))
    if not stage.has_access_log:
        findings.append(("info", f"Stage {stage.name} keeps no access log: a failed call can't be traced, and "
                                 f"{_call('calls', api.name)} has nothing to read. Turn it on "
                                 f"({access_log_command(api, stage)})."))
    if stage.data_trace:
        findings.append(("warn", f"Stage {stage.name} writes whole requests and responses to its execution log: "
                                 "tokens and personal data in them end up in CloudWatch, and it costs per GB. Turn "
                                 "off full request and response logging in the stage's Logs settings."))
    return findings


def format_findings(api: Api, stage: Stage | None) -> list[tuple[str, str]]:
    """
    What a stage's access log format leaves out that the Calls reports would use.
    """
    if stage is None or not stage.has_access_log:
        return []
    missing = missing_fields(read_format(stage.access_log_format))
    if not missing:
        return []
    return [("info", f"Stage {stage.name}'s access log doesn't log {', '.join(missing)}: without them a failed "
                     "call can't say how long its backend took or which Lambda run it was. Add them to the "
                     f"format (this one logs all of it: {suggested_format(api.kind)}).")]


def route_findings(api: Api, numbers: dict[str, RouteNumbers]) -> list[tuple[str, str]]:
    """
    Routes that fail or run close to the time limit, by their own numbers.
    """
    findings: list[tuple[str, str]] = []
    for route in api.routes:
        found = numbers.get(route.key)
        if found is None or not found.calls:
            continue
        findings += _slow_route(api, route, found) + _failing_route(api, route, found)
    return findings


def _slow_route(api: Api, route: Route, numbers: RouteNumbers) -> list[tuple[str, str]]:
    limit = (route.integration.timeout_ms if route.integration and route.integration.timeout_ms else None)
    limit = min(limit or api.max_timeout_ms, api.max_timeout_ms)
    if numbers.p95_ms is None or numbers.p95_ms < SLOW_SHARE * limit:
        return []
    target = route.integration.target if route.integration else "its integration"
    return [("warn", f"{route.key} runs out of time: 5% of its calls take {_seconds(numbers.p95_ms)} or more, and "
                     f"API Gateway gives up at {_seconds(limit)} with a 504, while {target} may still be working. "
                     "Make it answer first and finish the work later (a queue), or speed it up "
                     f"(see {_call('performance', api.name)}).")]


def _failing_route(api: Api, route: Route, numbers: RouteNumbers) -> list[tuple[str, str]]:
    if numbers.errors_5xx < FEWEST_ERRORS or numbers.rate_5xx < ROUTE_ERROR_RATE:
        return []
    return [("warn", f"{route.key} fails {numbers.rate_5xx:.1%} of its calls: {numbers.errors_5xx:,} server errors. "
                     f"See why with {_call('errors', api.name)}.")]


def overview_findings(overview: Overview, prices: dict[str, float]) -> list[tuple[str, str]]:
    """
    The region's findings: each API's errors, slowness, open routes and idleness, then the APIs that keep no access
    log, and the one that costs most when it's most of the bill.
    """
    findings: list[tuple[str, str]] = []
    for api in overview.apis:
        numbers = overview.numbers.get(api.id)
        findings += numbers_findings(api, numbers) + open_route_findings(api) + idle_findings(api, numbers)
    unlogged = [a.name for a in overview.apis if a.stages and not any(s.has_access_log for s in a.stages)]
    if unlogged:
        findings.append(("info", f"{_names(unlogged)} {'keeps' if len(unlogged) == 1 else 'keep'} no access log on "
                                 "any stage: a failed call can't be traced back to who sent it or why it failed. "
                                 f"{_call('stages', unlogged[0])} shows the command that turns it on."))
    findings += _bill_findings(overview, prices)
    return findings


def _bill_findings(overview: Overview, prices: dict[str, float]) -> list[tuple[str, str]]:
    costs = {a.name: monthly_cost(a, overview.numbers.get(a.id), prices) for a in overview.apis}
    known = {name: cost for name, cost in costs.items() if cost}
    total = sum(known.values())
    if len(known) < 2 or not total:
        return []
    name, cost = max(known.items(), key=lambda item: item[1])
    if cost < total / 2:
        return []
    return [("info", f"{name} is most of the estimated bill: {human_money(cost)} of {human_money(total)} a month. "
                     f"{_call('api_info', name)} shows what it's made of.")]


def api_findings(report: ApiReport, prices: dict[str, float]) -> list[tuple[str, str]]:
    """
    One API's findings: its numbers, its routes, who can call it, its stage's settings and log format.
    """
    api, stage = report.api, report.stage
    findings = numbers_findings(api, report.numbers) + route_findings(api, report.route_numbers)
    findings += open_route_findings(api) + idle_findings(api, report.numbers)
    findings += stage_findings(api, stage) + format_findings(api, stage)
    if stage is not None and report.numbers and report.numbers.requests and not stage.detailed_metrics:
        findings.append(("info", f"Stage {stage.name} has no per-route metrics: CloudWatch counts the whole API, so "
                                 f"the routes table has no numbers. {_call('performance', api.name)} works them out "
                                 "from its access log instead, or turn on detailed metrics in the stage's settings."))
    return findings


def _lambda_logs_call(call: Call, function: str) -> str:
    if call.lambda_request:
        return f"LambdaView().{_call('logs', function, request_id=call.lambda_request)}"
    return f"LambdaView().{_call('errors', function)}"


def _group_finding(api: Api, group: ErrorGroup, total: int) -> tuple[str, str] | None:
    """
    What the most common kind of failure means, by its status: a timeout, a bad answer from the integration,
    throttling, a path that doesn't exist, or a refused caller.
    """
    route = api.route(group.route)
    function = route.integration.function if route and route.integration else ""
    share = f"{group.count:,} of {total:,} failed calls"
    if group.status == 504:
        read = f" Read the run behind the newest: {_lambda_logs_call(group.newest, function)}." if function else ""
        return ("warn", f"{group.route} times out: {share} got a 504, because API Gateway waits "
                        f"{_seconds(api.max_timeout_ms)} at most. Make its backend answer sooner, or reply first and "
                        f"finish the work later (a queue).{read}")
    if group.status == 502:
        read = f" {_lambda_logs_call(group.newest, function)} shows what it did." if function else ""
        said = group.error or "bad gateway"
        return ("warn", f"{group.route} gets bad answers from its backend: {share} got a 502 ({said}). A Lambda function behind a proxy integration must return statusCode and a body "
                        f"that's a string, and not raise.{read}")
    if group.status == 429:
        return ("warn", f"{group.route} is throttled: {share} got a 429. Raise the stage's rate and burst, ask for a "
                        "higher account limit, or have callers retry with back-off.")
    if group.status == 403 and "Missing Authentication Token" in group.error:
        return ("info", f"{share} asked for a route that doesn't exist: API Gateway answers a wrong path or method "
                        f"with 403 Missing Authentication Token. Check the URL against {_call('routes', api.name)}.")
    if group.status in (401, 403):
        return ("info", f"{group.route} refuses callers: {share} got a {group.status} ({group.error or 'refused'}). "
                        "Usually an expired or missing token, or a key without a usage plan.")
    return None


def error_findings(report: ErrorReport) -> list[tuple[str, str]]:
    """
    What a stage's failures say: the meaning of the most common kinds, or, without an access log, what CloudWatch
    counted and how to see more.
    """
    api, stage, page = report.api, report.stage, report.page
    if stage is not None and not stage.has_access_log:
        counted = report.numbers
        errors = f"{counted.errors_5xx:,} 5xx and {counted.errors_4xx:,} 4xx" if counted else "its errors"
        return [("info", f"Stage {stage.name} keeps no access log, so only CloudWatch's counts ({errors}) are "
                         "known, not which calls failed or why. Turn the access log on "
                         f"({access_log_command(api, stage)}).")]
    failed = len(page.failed) if page else 0
    findings = []
    for group in report.groups[:GROUPS_EXPLAINED]:
        found = _group_finding(api, group, failed)
        if found is not None and found not in findings:
            findings.append(found)
    return findings + format_findings(api, stage)
