"""
API Gateway's reports as data: the tables behind apis(), api_info(), routes(), stages(), calls(), errors() and
performance(), each column said in plain words with how to read it, and the charts the workbook draws from them. A
call's row names the Lambda function behind its route and the Lambda request ID its access log recorded, so a failed
or slow call leads to the run that answered it. Pure: every function takes what a command already read and returns a
ReportData (_kit/datasets.py).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..._kit.datasets import DataChart, DataColumn, DataTable, Recipe, ReportData, by_hour, median_of
from ..models import Api, ApiNumbers, ApiReport, Call, CallPage, ErrorReport, Overview, Route, RouteNumbers, Stage
from .access_log import route_numbers_from_calls
from .cost import cache_monthly, monthly_cost
from .try_it import curl_command

_CLASS_TONES = (("5xx", "bad"), ("4xx", "warn"), ("2xx", "ok"))
_AUTH_TONES = (("none", "bad"),)
_NEAR_LIMIT = 0.8


def _share_of(part: float | None, whole: float | None) -> float | None:
    """
    part / whole as a share from 0 to 1, or None without a whole.
    """
    if part is None or not whole:
        return None
    return part / whole


def _stem(command: str, name: str) -> str:
    return f"apigateway-{command}-{name}"


def _status_class(status: int | None) -> str:
    """
    504 -> '5xx': the status code's class, which the workbook colours; '' when the log has no status.
    """
    return f"{status // 100}xx" if status else ""


def _route_function(api: Api, route_key: str) -> str:
    """
    The Lambda function behind a route, by its key ('POST /orders'), or '' when it calls something else.
    """
    route = api.route(route_key) if route_key else None
    if route is None or route.integration is None:
        return ""
    return route.integration.function


_DAY_COLUMNS = [
    DataColumn("api", "the API's name", "id"),
    DataColumn("day", "the UTC day (CloudWatch counts whole UTC days)"),
    DataColumn("requests", "requests that day (a WebSocket API: messages)", "count", bar=True),
    DataColumn("errors_5xx", "requests that got a 5xx: the API or its backend failed", "count"),
    DataColumn("share_5xx", "errors_5xx / requests", "share", read="Over 0.01 (1%) is worth errors() on it."),
]


def _day_rows(api: Api, numbers: ApiNumbers | None, today: date) -> list[dict[str, Any]]:
    """
    A row per UTC day of an API's CloudWatch numbers, the oldest first, the last one `today`.
    """
    if numbers is None:
        return []
    first = today - timedelta(days=numbers.days - 1)
    rows = []
    for offset, requests in enumerate(numbers.daily_requests):
        failed = numbers.daily_5xx[offset] if offset < len(numbers.daily_5xx) else 0
        rows.append({"api": api.name, "day": (first + timedelta(days=offset)).isoformat(), "requests": requests,
                     "errors_5xx": failed, "share_5xx": _share_of(failed, requests)})
    return rows


_API_COLUMNS = [
    DataColumn("api", "the API's name", "id"),
    DataColumn("type", "REST, HTTP or WEBSOCKET: each is priced and read differently"),
    DataColumn("stages", "its stages, the deployed copies with a URL each"),
    DataColumn("requests", "requests in the window (CloudWatch; a WebSocket API counts messages)", "count", bar=True),
    DataColumn("errors_4xx", "requests refused with a 4xx: the caller's request was wrong or not allowed", "count"),
    DataColumn("errors_5xx", "requests that got a 5xx: the API or its backend failed", "count"),
    DataColumn("share_4xx", "errors_4xx / requests", "share",
               read="Over 0.1 (10%) means callers are often refused: a wrong key, an expired token, a bad URL."),
    DataColumn("share_5xx", "errors_5xx / requests", "share", read="Over 0.01 (1%) is worth errors() on it."),
    DataColumn("avg_ms", "the average request's time, start to finish", "ms"),
    DataColumn("p95_ms", "95% of requests took this long or less", "ms",
               read="Close to max_timeout_ms means some calls get a 504."),
    DataColumn("integration_ms", "the average time its backend took", "ms"),
    DataColumn("max_timeout_ms", "the longest API Gateway waits for a backend: 29 s REST, 30 s HTTP", "ms"),
    DataColumn("routes", "routes it has", "count"),
    DataColumn("open_routes", "routes anyone with the URL can call: no authorizer, IAM or API key", "count",
               read="Above 0 is worth routes() on it."),
    DataColumn("functions", "the Lambda functions its routes call"),
    DataColumn("est_monthly_usd", "an estimate of its monthly bill at this pace, at list prices, caches included",
               "usd", bar=True),
    DataColumn("created", "when it was created", "time"),
    DataColumn("id", "its API ID", "id"),
]


def _api_row(api: Api, numbers: ApiNumbers | None, prices: dict[str, float]) -> dict[str, Any]:
    has_numbers = numbers is not None
    return {
        "api": api.name, "type": api.kind, "stages": ", ".join(s.name for s in api.stages),
        "requests": numbers.requests if has_numbers else None,
        "errors_4xx": numbers.errors_4xx if has_numbers else None,
        "errors_5xx": numbers.errors_5xx if has_numbers else None,
        "share_4xx": _share_of(numbers.errors_4xx, numbers.requests) if has_numbers else None,
        "share_5xx": _share_of(numbers.errors_5xx, numbers.requests) if has_numbers else None,
        "avg_ms": numbers.latency_ms if has_numbers else None, "p95_ms": numbers.p95_ms if has_numbers else None,
        "integration_ms": numbers.integration_ms if has_numbers else None, "max_timeout_ms": api.max_timeout_ms,
        "routes": len(api.routes), "open_routes": len(api.open_routes), "functions": ", ".join(api.functions),
        "est_monthly_usd": monthly_cost(api, numbers, prices), "created": api.created, "id": api.id,
    }


def apis_data(overview: Overview, prices: dict[str, float], *, today: date) -> ReportData:
    """
    apis()' report as data: a row per API, the costliest first, and a row per API per UTC day.
    """
    apis, numbers, region, days = overview.apis, overview.numbers, overview.region, overview.days
    rows = [_api_row(api, numbers.get(api.id), prices) for api in apis]
    rows.sort(key=lambda row: -(row["est_monthly_usd"] or 0.0))
    day_rows = [row for api in apis for row in _day_rows(api, numbers.get(api.id), today)]
    table = DataTable(
        "apis", f"Every API in {region}", "an API", _API_COLUMNS, rows,
        notes=[f"Counts cover the last {days} whole UTC days, from CloudWatch.",
               "The costliest first. Costs are estimates at the first price tier; the free tier isn't taken off."],
        recipes=[Recipe("df.sort_values('share_5xx', ascending=False).head()", "the APIs failing most"),
                 Recipe("df[df.open_routes > 0][['api', 'open_routes']]", "the APIs anyone can call"),
                 Recipe("df.groupby('type').est_monthly_usd.sum()", "the bill by kind of API")])
    by_day = DataTable(
        "days", "Requests a day, per API", "an API on a UTC day", _DAY_COLUMNS, day_rows,
        recipes=[Recipe("df.pivot(index='day', columns='api', values='requests')", "a column per API, ready to plot")])
    charts = [
        DataChart("Requests and server errors by API", "apis", "api", ("requests", "errors_5xx"), "bar",
                  f"Over the last {days} days, in the order of the cost chart.", "requests"),
        DataChart("Estimated monthly cost by API", "apis", "api", ("est_monthly_usd",), "bar",
                  "At this pace, at list prices.", "USD a month"),
    ]
    return ReportData(f"API Gateway in {region}", [table, by_day], charts, stem=_stem("apis", region))


_STAGE_COLUMNS = [
    DataColumn("stage", "the stage's name", "id"),
    DataColumn("url", "where it answers"),
    DataColumn("deployed", "when it was last deployed or changed", "time"),
    DataColumn("throttle_rate", "requests a second it allows (empty: the account's 10,000, shared by every API)",
               "number", read="Empty means one busy API can starve the others in the region."),
    DataColumn("throttle_burst", "requests it allows at once, above the rate", "count"),
    DataColumn("access_log", "the log group its access log writes to, a line per call (empty: none)",
               read="Empty means calls(), errors() and performance() have nothing to read."),
    DataColumn("execution_log", "its execution logging: OFF, ERROR or INFO (REST)"),
    DataColumn("tracing", "whether X-Ray tracing is on", "flag"),
    DataColumn("detailed_metrics", "whether CloudWatch counts each route on its own", "flag"),
    DataColumn("cache_gb", "its cache's size in GB, when it has one", "number"),
    DataColumn("cache_monthly_usd", "what its cache costs a month: billed every hour it's on, used or not", "usd"),
    DataColumn("waf", "the web ACL guarding it"),
    DataColumn("variables", "its stage variables (names only)"),
]


def _cache_gb(stage: Stage) -> float | None:
    if not stage.cache_enabled:
        return None
    try:
        return float(stage.cache_size)
    except ValueError:
        return None


def _stage_row(api: Api, stage: Stage, prices: dict[str, float]) -> dict[str, Any]:
    alone = Api(api.id, api.name, api.kind, stages=[stage])
    return {
        "stage": stage.name, "url": api.url(stage.name), "deployed": stage.updated or stage.created,
        "throttle_rate": stage.throttle_rate, "throttle_burst": stage.throttle_burst,
        "access_log": stage.access_log_group, "execution_log": stage.logging, "tracing": stage.tracing,
        "detailed_metrics": stage.detailed_metrics, "cache_gb": _cache_gb(stage),
        "cache_monthly_usd": cache_monthly(alone, prices) if stage.cache_enabled else None,
        "waf": stage.waf, "variables": ", ".join(stage.variables),
    }


def _stages_table(api: Api, prices: dict[str, float]) -> DataTable:
    return DataTable(
        "stages", f"The stages of {api.name}", "a stage", _STAGE_COLUMNS,
        [_stage_row(api, stage, prices) for stage in api.stages],
        recipes=[Recipe("df[df.access_log == ''].stage", "stages whose calls aren't recorded"),
                 Recipe("df[df.throttle_rate.isna()].stage", "stages sharing the account's limit")])


_ROUTE_COLUMNS = [
    DataColumn("route", "the method and path ('POST /orders'), or a WebSocket route key", "id"),
    DataColumn("method", "the HTTP method (ANY takes every one)"),
    DataColumn("path", "the path, {braces} marking its parameters"),
    DataColumn("auth", "who may call it: an authorizer, IAM, an API key, or 'none'", tones=_AUTH_TONES),
    DataColumn("is_open", "whether anyone with the URL can call it", "flag",
               read="True on a route that reaches data is worth an authorizer."),
    DataColumn("goes_to", "what it calls: 'λ orders-etl', a URL, an AWS service action or 'mock'"),
    DataColumn("backend", "the kind of backend: Lambda, HTTP, AWS service, mock or VPC link"),
    DataColumn("function", "the Lambda function it calls"),
    DataColumn("timeout_ms", "how long API Gateway waits for it (empty: the most it allows)", "ms"),
    DataColumn("calls", "calls it had (detailed metrics or the access log; empty when neither counts it)", "count",
               bar=True),
    DataColumn("errors_5xx", "calls that got a 5xx", "count"),
    DataColumn("share_5xx", "errors_5xx / calls", "share", read="Over 0.05 (5%) is worth errors() on it."),
    DataColumn("avg_ms", "the average call's time", "ms"),
    DataColumn("p95_ms", "95% of calls took this long or less", "ms"),
    DataColumn("max_ms", "the longest call", "ms"),
    DataColumn("p95_share_of_limit", "p95_ms / the time API Gateway waits", "share",
               read="Over 0.8 means some calls get a 504 when the backend is slow."),
    DataColumn("try_it", "the curl command that calls it"),
]


def _wait_limit(api: Api, route: Route | None) -> int:
    """
    How long API Gateway waits for a route's backend: its own timeout, at most the API's.
    """
    own = route.integration.timeout_ms if route is not None and route.integration is not None else None
    return min(own or api.max_timeout_ms, api.max_timeout_ms)


def _numbers_part(numbers: RouteNumbers | None, limit: int) -> dict[str, Any]:
    if numbers is None:
        return {"calls": None, "errors_5xx": None, "share_5xx": None, "avg_ms": None, "p95_ms": None,
                "max_ms": None, "p95_share_of_limit": None}
    return {"calls": numbers.calls, "errors_5xx": numbers.errors_5xx,
            "share_5xx": _share_of(numbers.errors_5xx, numbers.calls), "avg_ms": numbers.avg_ms,
            "p95_ms": numbers.p95_ms, "max_ms": numbers.max_ms, "p95_share_of_limit": _share_of(numbers.p95_ms, limit)}


def _route_row(api: Api, route: Route, numbers: RouteNumbers | None, stage: str | None) -> dict[str, Any]:
    integration = route.integration
    row = {
        "route": route.key, "method": route.method, "path": route.path, "auth": route.auth,
        "is_open": route in api.open_routes, "goes_to": integration.target if integration else "",
        "backend": integration.kind if integration else "", "function": integration.function if integration else "",
        "timeout_ms": integration.timeout_ms if integration else None,
    }
    row.update(_numbers_part(numbers, _wait_limit(api, route)))
    row["try_it"] = curl_command(api, route, stage)
    return row


def _routes_table(api: Api, numbers: dict[str, RouteNumbers], stage: str | None, *, source: str) -> DataTable:
    rows = [_route_row(api, route, numbers.get(route.key), stage) for route in api.routes]
    rows.sort(key=lambda row: (-(row["calls"] or 0), row["path"], row["method"]))
    notes = ["The busiest first." if numbers else "No route's calls were counted: the stage has neither detailed "
             "metrics nor an access log read here."]
    if source:
        notes.append(f"Calls and times come from {source}.")
    return DataTable(
        "routes", f"The routes of {api.name}", "a route", _ROUTE_COLUMNS, rows, notes=notes,
        recipes=[Recipe("df[df.is_open][['route', 'goes_to']]", "what anyone can call"),
                 Recipe("df.groupby('backend').size()", "the routes by kind of backend"),
                 Recipe("df.nlargest(5, 'p95_share_of_limit')[['route', 'p95_ms', 'timeout_ms']]",
                        "the routes closest to a 504")])


def api_data(report: ApiReport, prices: dict[str, float], *, today: date) -> ReportData:
    """
    api_info()'s report as data: its stages, its routes with their numbers when the stage counts them, and its
    requests day by day.
    """
    api = report.api
    stage = report.stage.name if report.stage else None
    source = "detailed metrics, 7 days" if report.route_numbers else ""
    routes = _routes_table(api, report.route_numbers, stage, source=source)
    days = DataTable("days", "Requests a day", "a UTC day", _DAY_COLUMNS, _day_rows(api, report.numbers, today))
    tables = [_stages_table(api, prices), routes] + ([days] if days.rows else [])
    charts = [DataChart("Requests a day", "days", "day", ("requests", "errors_5xx"), "column",
                        "Requests, and the ones that got a 5xx.", "requests")] if days.rows else []
    return ReportData(api.name, tables, charts, stem=_stem("api", api.name))


_AUTHORIZER_COLUMNS = [
    DataColumn("authorizer", "its name", "id"),
    DataColumn("type", "COGNITO_USER_POOLS, JWT, TOKEN or REQUEST (a Lambda authorizer)"),
    DataColumn("reads", "where it finds the caller's identity: a header, a query string"),
    DataColumn("function", "the Lambda function it runs"),
    DataColumn("issuer", "the token issuer it trusts"),
    DataColumn("routes", "routes it guards", "count"),
]


def routes_data(api: Api, stage: str | None) -> ReportData:
    """
    routes()' report as data: every route with who can call it, where it goes and the curl command that calls it,
    and every authorizer with the routes it guards.
    """
    routes = _routes_table(api, {}, stage, source="")
    routes.notes = ["In the order the API lists them; routes() and api_info() say which are busiest."]
    guarded = [route.authorizer.id for route in api.routes if route.authorizer is not None]
    authorizers = [{"authorizer": a.name, "type": a.type, "reads": a.identity_source, "function": a.function,
                    "issuer": a.issuer, "routes": guarded.count(a.id)} for a in api.authorizers.values()]
    tables = [routes, DataTable("authorizers", f"The authorizers of {api.name}", "an authorizer",
                                _AUTHORIZER_COLUMNS, authorizers)]
    return ReportData(f"Routes of {api.name}", tables, stem=_stem("routes", api.name))


def stages_data(api: Api, prices: dict[str, float]) -> ReportData:
    """
    stages()' report as data: every stage with its URL, throttling, logs, tracing, cache and WAF.
    """
    return ReportData(f"Stages of {api.name}", [_stages_table(api, prices)], stem=_stem("stages", api.name))


_CALL_COLUMNS = [
    DataColumn("time", "when the call came in", "time"),
    DataColumn("route", "the route it matched ('POST /orders'), else its method and path", "id"),
    DataColumn("status", "the status code it got", "count"),
    DataColumn("status_class", "2xx, 3xx, 4xx (the caller's fault) or 5xx (the API's or its backend's)",
               tones=_CLASS_TONES),
    DataColumn("latency_ms", "how long it took, start to finish", "ms", bar=True),
    DataColumn("integration_ms", "how much of it the backend took", "ms"),
    DataColumn("authorizer_ms", "how much of it the authorizer took", "ms"),
    DataColumn("gateway_ms", "the rest: API Gateway itself", "ms"),
    DataColumn("backend_share", "integration_ms / latency_ms", "share",
               read="Near 1 means the backend is what's slow; low means look at the authorizer or the gateway."),
    DataColumn("share_of_limit", "latency_ms / the time API Gateway waits", "share",
               read="Near 1 is a call that hit the limit and got a 504."),
    DataColumn("error", "the error it got, the integration's first"),
    DataColumn("integration_status", "the status its backend answered with"),
    DataColumn("function", "the Lambda function behind its route"),
    DataColumn("lambda_request", "the Lambda run's request ID: LambdaView().logs(function, request_id=...) opens it",
               "id"),
    DataColumn("request_id", "API Gateway's request ID: calls(api, search=...) finds it", "id"),
    DataColumn("ip", "the caller's IP address"),
    DataColumn("user", "the caller, when the authorizer names one"),
]


def _call_row(api: Api, call: Call) -> dict[str, Any]:
    route = api.route(call.route_key) if call.route_key else None
    known_parts = (call.integration_ms or 0.0) + (call.authorizer_ms or 0.0)
    gateway = max(0.0, call.latency_ms - known_parts) if call.latency_ms is not None else None
    return {
        "time": call.time, "route": call.route_key, "status": call.status, "status_class": _status_class(call.status),
        "latency_ms": call.latency_ms, "integration_ms": call.integration_ms, "authorizer_ms": call.authorizer_ms,
        "gateway_ms": gateway, "backend_share": _share_of(call.integration_ms, call.latency_ms),
        "share_of_limit": _share_of(call.latency_ms, _wait_limit(api, route)), "error": call.why,
        "integration_status": call.integration_status, "function": _route_function(api, call.route_key),
        "lambda_request": call.lambda_request, "request_id": call.request_id, "ip": call.ip, "user": call.user,
    }


def _calls_table(api: Api, calls: list[Call], *, name: str, title: str, notes: list[str]) -> DataTable:
    return DataTable(
        name, title, "a call the access log recorded", _CALL_COLUMNS, [_call_row(api, call) for call in calls],
        notes=notes,
        recipes=[Recipe("df.groupby('route').latency_ms.describe()", "each route's times"),
                 Recipe("df[df.status_class == '5xx'].groupby(['route', 'error']).size()", "what the failures were"),
                 Recipe("df.set_index('time').resample('h').size()", "calls an hour")])


_HOUR_COLUMNS = [
    DataColumn("hour", "the hour they came in", "time"),
    DataColumn("calls", "calls recorded that hour", "count", bar=True),
    DataColumn("failed", "the ones that got a 4xx or 5xx", "count"),
    DataColumn("errors_5xx", "the ones that got a 5xx", "count"),
    DataColumn("median_ms", "the typical call's time", "ms"),
    DataColumn("slowest_ms", "the slowest call's time", "ms"),
]


def _hour_rows(calls: list[Call]) -> list[dict[str, Any]]:
    rows = []
    for hour, members in by_hour(({"time": c.time, "call": c} for c in calls), time="time").items():
        made = [member["call"] for member in members]
        times = [c.latency_ms for c in made if c.latency_ms is not None]
        rows.append({"hour": hour, "calls": len(made), "failed": sum(1 for c in made if c.failed),
                     "errors_5xx": sum(1 for c in made if (c.status or 0) >= 500),
                     "median_ms": median_of(times), "slowest_ms": max(times) if times else None})
    return rows


def _page_notes(page: CallPage) -> list[str]:
    notes = ["Newest first."]
    if page.truncated:
        notes.append("Only the newest lines were read: pass since= for a shorter time.")
    if page.unread:
        notes.append(f"{page.unread:,} lines didn't match the stage's access log format and are left out.")
    return notes


def calls_data(page: CallPage, calls: list[Call]) -> ReportData:
    """
    calls()' report as data: every call read (the ones status= keeps), each route's numbers from them, and the
    calls an hour.
    """
    api = page.api
    numbers = route_numbers_from_calls(calls)
    stage = page.stage.name if page.stage else None
    routes = _routes_table(api, numbers, stage, source="the access log")
    routes.rows = [row for row in routes.rows if row["calls"]]
    hours = DataTable("hours", "Calls an hour", "an hour with calls", _HOUR_COLUMNS, _hour_rows(calls))
    tables = [_calls_table(api, calls, name="calls", title=f"Calls to {api.name}", notes=_page_notes(page)),
              routes, hours]
    charts = [DataChart("Every call's time", "calls", "time", ("latency_ms",), "scatter",
                        "A dot per call: the ones far above the rest are worth a look.", "ms"),
              DataChart("Calls an hour", "hours", "hour", ("calls", "failed"), "column",
                        "How many came in, and how many failed.", "calls")]
    return ReportData(f"Calls to {api.name}", tables, charts, stem=_stem("calls", api.name))


_CAUSE_COLUMNS = [
    DataColumn("status", "the status code they got", "count"),
    DataColumn("status_class", "4xx (the caller's fault) or 5xx (the API's or its backend's)", tones=_CLASS_TONES),
    DataColumn("error", "the error they got, the integration's first"),
    DataColumn("route", "the route they called", "id"),
    DataColumn("count", "calls that failed this way", "count", bar=True),
    DataColumn("share", "its part of the failed calls read", "share"),
    DataColumn("newest", "when the newest of them came in", "time"),
    DataColumn("function", "the Lambda function behind the route"),
    DataColumn("lambda_request", "the newest one's Lambda run: LambdaView().logs(function, request_id=...)", "id"),
    DataColumn("request_id", "the newest one's API Gateway request ID", "id"),
]


def _cause_rows(api: Api, report: ErrorReport) -> list[dict[str, Any]]:
    total = sum(group.count for group in report.groups)
    return [{"status": g.status, "status_class": _status_class(g.status), "error": g.error, "route": g.route,
             "count": g.count, "share": _share_of(g.count, total), "newest": g.newest.time,
             "function": _route_function(api, g.route), "lambda_request": g.newest.lambda_request,
             "request_id": g.newest.request_id} for g in report.groups]


def errors_data(report: ErrorReport) -> ReportData:
    """
    errors()' report as data: a row per way of failing (status, error and route), the most frequent first, and a
    row per failed call, each with the Lambda run behind it.
    """
    api = report.api
    failed = report.page.failed if report.page else []
    notes = ["The most frequent first; share adds up to 1."]
    if report.page is None or report.page.note:
        notes.append("The stage keeps no access log, so only CloudWatch's counts are known: both tables are empty.")
    causes = DataTable("causes", f"Why calls to {api.name} fail", "a status, error and route calls failed with",
                       _CAUSE_COLUMNS, _cause_rows(api, report), notes=notes,
                       recipes=[Recipe("df.groupby('route')['count'].sum()", "failures by route"),
                                Recipe("df.groupby('status_class')['count'].sum()", "the caller's fault or the API's")])
    calls = _calls_table(api, failed, name="failed", title="Every failed call read",
                         notes=["Newest first. lambda_request opens the run that answered."])
    charts = [DataChart("Failures by route", "causes", "route", ("count",), "bar",
                        "Calls that failed on each route: the longest bar is the fix that helps most.", "calls")]
    return ReportData(f"What's failing in {api.name}", [causes, calls], charts, stem=_stem("errors", api.name))


def performance_data(page: CallPage, report: ApiReport) -> ReportData:
    """
    performance()'s report as data: each route's times (from the access log, or detailed metrics), the slowest
    first, and every call read with where its time went.
    """
    api = page.api
    from_log = bool(page.calls)
    numbers = route_numbers_from_calls(page.calls) if from_log else report.route_numbers
    source = "the access log" if from_log else "detailed metrics, 7 days"
    stage = page.stage.name if page.stage else None
    routes = _routes_table(api, numbers, stage, source=source)
    routes.rows = sorted((row for row in routes.rows if row["calls"]), key=lambda row: -(row["p95_ms"] or 0.0))
    routes.notes = [f"The slowest first, from {source}.",
                    f"API Gateway gives up after {api.max_timeout_ms / 1000:,.0f} s with a 504: a p95_share_of_limit "
                    f"over {_NEAR_LIMIT} comes close."]
    ordered = sorted(page.calls, key=lambda call: -(call.latency_ms or 0.0))
    calls = _calls_table(api, ordered, name="calls", title=f"Every call to {api.name}, the slowest first",
                         notes=["The slowest first. gateway_ms is what's left once the backend and authorizer are "
                                "taken off."])
    charts = [DataChart("Each route's 95th percentile", "routes", "route", ("p95_ms",), "bar",
                        "95% of calls took this long or less.", "ms"),
              DataChart("Where the slowest calls' time went", "calls", "request_id",
                        ("integration_ms", "authorizer_ms", "gateway_ms"), "bar",
                        "The backend, the authorizer and API Gateway, call by call.", "ms", rows=10)]
    return ReportData(f"How long {api.name}'s calls take", [routes, calls], charts if from_log else charts[:1],
                      stem=_stem("performance", api.name))

