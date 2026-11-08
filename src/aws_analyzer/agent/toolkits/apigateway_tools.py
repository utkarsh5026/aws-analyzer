"""
The API Gateway toolkit: tools over APIGatewayAnalyzer for which APIs fail, why, and what's behind a failed call.
Which REST, HTTP and WebSocket APIs there are and how each did over the last days (find_apis), one API's stages,
routes (who may call each, where it sends a request) and findings (api_details), what's failing in a stage, from
CloudWatch's counts and the failed calls its access log recorded, grouped by status, error and route, with the
Lambda run behind the newest (api_errors), and the calls themselves, searched by any text (api_calls). A call's
access log line can name the Lambda request ID behind it ($context.integration.requestId), which is how api_errors
and api_calls read that run's log lines: the link that lets the agent follow a 502 into the function that caused it,
and on with the Lambda tools when it has them.

Nothing here sends a request to an API or changes one: findings show the command that would.

The summarize_* functions are pure: they run on any Overview, ApiReport, ErrorReport, CallPage or LambdaRun.
APIGATEWAY_TOOL_READS is what each tool reads (its IAM permissions), for tools() and README: IAM authorizes every
API Gateway read as apigateway:GET. The analyzer's types are imported for the type hints only, since its module
loads when the first tool runs (_apigw_module). APIs are regional: the toolkit reads the agent's region. A list shows
at most _LISTED_APIS APIs, _LISTED_ROUTES routes, _ERROR_GROUPS error groups and _MOST_CALLS calls; a Lambda run shows
its last _RUN_LINES lines, each cut to _LINE_CHARS.
"""

from __future__ import annotations

import difflib
import fnmatch
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, human_money
from ..._kit.text import _clip
from ..budget import Budget
from ..results import _window, _with_permissions, clamp_count, hide_secrets, model_findings, share_pct
from .base import Toolkit

if TYPE_CHECKING:
    from ...apigateway import (
        Api, ApiNumbers, ApiReport, APIGatewayAnalyzer, Call, CallPage, ErrorGroup, ErrorReport, LambdaRun, Overview,
        Route, RouteNumbers, RunLine, Stage,
    )

_GET = "apigateway:GET"
_METRICS = "cloudwatch:GetMetricData"
_ACCESS_LOG = "logs:FilterLogEvents"
_LAMBDA_RUN = ("lambda:GetFunction", "logs:DescribeLogGroups", "logs:FilterLogEvents")
APIGATEWAY_TOOL_READS: dict[str, tuple[str, ...]] = {
    "find_apis": (_GET, _METRICS),
    "api_details": (_GET, _METRICS),
    "api_errors": (_GET, _METRICS, _ACCESS_LOG, *_LAMBDA_RUN),
    "api_calls": (_GET, _ACCESS_LOG, *_LAMBDA_RUN),
}
_DAYS = 7
_MOST_DAYS = 90
_LISTED_APIS = 60
_LISTED_ROUTES = 80
_ERROR_GROUPS = 10
_DEFAULT_CALLS = 20
_MOST_CALLS = 100
_LINES_READ = 5_000
_RUN_LINES = 30
_LINE_CHARS = 400
_SECTION_PERMISSIONS = {"metrics": _METRICS, "routes": _GET, "stages": _GET, "authorizers": _GET}


def _apigw_module() -> Any:
    """
    aws_analyzer.apigateway, which the tools are built on. Loaded when a tool first needs it, so importing the
    agent loads no analyzer.
    """
    from ... import apigateway

    return apigateway


def _api_numbers(numbers: ApiNumbers | None) -> dict[str, Any] | None:
    """
    An API's CloudWatch numbers as the model reads them: requests, 4xx and 5xx errors with their shares, and how
    long requests and the integration took.
    """
    if numbers is None:
        return None
    row: dict[str, Any] = {"days": numbers.days, "requests": numbers.requests, "errors_4xx": numbers.errors_4xx,
                           "errors_5xx": numbers.errors_5xx,
                           "rate_4xx_pct": share_pct(numbers.errors_4xx, numbers.requests),
                           "rate_5xx_pct": share_pct(numbers.errors_5xx, numbers.requests),
                           "latency_ms": numbers.latency_ms, "p95_ms": numbers.p95_ms,
                           "integration_ms": numbers.integration_ms}
    if numbers.connections:
        row["connections"] = numbers.connections
    return row


def _stage_row(stage: Stage) -> dict[str, Any]:
    """
    One stage's settings: its throttling, logging, access log, tracing, cache and detailed metrics.
    """
    return {"name": stage.name, "updated": stage.updated or stage.created,
            "throttle_rate": stage.throttle_rate, "throttle_burst": stage.throttle_burst,
            "execution_logging": stage.logging, "access_log_group": stage.access_log_group or None,
            "tracing": stage.tracing, "cache": stage.cache_size if stage.cache_enabled else None,
            "waf": stage.waf or None, "detailed_metrics": stage.detailed_metrics}


def _route_row(route: Route, numbers: RouteNumbers | None) -> dict[str, Any]:
    """
    One route: its key, who may call it, where it sends a request (the Lambda function, URL or AWS call) and how
    long API Gateway waits, and its numbers when the stage has per-route metrics.
    """
    integration = route.integration
    row: dict[str, Any] = {"route": route.key, "auth": route.auth, "open_to_anyone": route.is_open,
                           "target": integration.target if integration else None,
                           "timeout_ms": integration.timeout_ms if integration else None}
    if integration is not None and integration.function:
        row["lambda_function"] = integration.function
    if numbers is not None:
        row["numbers"] = {"calls": numbers.calls, "errors_4xx": numbers.errors_4xx, "errors_5xx": numbers.errors_5xx,
                          "rate_5xx_pct": share_pct(numbers.errors_5xx, numbers.calls), "p95_ms": numbers.p95_ms}
    return row


def _api_row(api: Api, numbers: ApiNumbers | None, prices: dict[str, float]) -> dict[str, Any]:
    """
    One API in find_apis' list: its kind, stages, how many routes (and open ones), the functions it calls, its
    numbers, estimated monthly cost and how many warnings its findings have.
    """
    apigw = _apigw_module()
    cost = apigw.monthly_cost(api, numbers, prices)
    found = (apigw.numbers_findings(api, numbers) + apigw.open_route_findings(api)
             + apigw.idle_findings(api, numbers))
    row: dict[str, Any] = {"name": api.name, "id": api.id, "kind": api.kind_label,
                           "stages": [s.name for s in api.stages], "routes": len(api.routes),
                           "open_routes": len(api.open_routes), "lambda_functions": api.functions,
                           "access_log": any(s.has_access_log for s in api.stages),
                           "numbers": _api_numbers(numbers),
                           "estimated_monthly_cost": human_money(cost) if cost is not None else None,
                           "warnings": sum(level == "warn" for level, _ in found)}
    if api.errors:
        row["errors"] = _with_permissions(api.errors, _SECTION_PERMISSIONS)
    return row


def _errors_5xx(row: dict[str, Any]) -> int:
    return int((row.get("numbers") or {}).get("errors_5xx") or 0)


def summarize_apis(overview: Overview, prices: dict[str, float], *, limit: int = _LISTED_APIS) -> dict[str, Any]:
    """
    find_apis()'s answer: every API in the region with its numbers over the window, the ones with the most 5xx
    errors first, each one's share of the region's 5xx errors, and the region's findings.
    """
    apigw = _apigw_module()
    rows = [_api_row(api, overview.numbers.get(api.id), prices) for api in overview.apis]
    rows.sort(key=lambda r: (-_errors_5xx(r), -r["warnings"], r["name"].lower()))
    total = sum(_errors_5xx(r) for r in rows)
    for row in rows:
        if row["numbers"] is not None:
            row["share_of_5xx_pct"] = share_pct(_errors_5xx(row), total)
    out: dict[str, Any] = {"region": overview.region, "days": overview.days, "count": len(rows),
                           "totals": {"requests": sum((r["numbers"] or {}).get("requests", 0) for r in rows),
                                      "errors_5xx": total},
                           "apis": rows[:limit], "findings": model_findings(apigw.overview_findings(overview, prices))}
    if len(rows) > limit:
        out["more"] = f"{len(rows) - limit:,} more not listed: narrow with match='prefix-*'"
    if overview.errors:
        out["errors"] = _with_permissions(overview.errors, _SECTION_PERMISSIONS)
    return out


def summarize_api(report: ApiReport, prices: dict[str, float]) -> dict[str, Any]:
    """
    api_details()'s answer: one API's kind, URL and stages, the chosen stage's numbers over the window, each route
    (who may call it, where it goes, its numbers when the stage has per-route metrics), its estimated cost, and the
    findings with what to do.
    """
    apigw = _apigw_module()
    api, stage = report.api, report.stage
    cost = apigw.monthly_cost(api, report.numbers, prices)
    routes = [_route_row(r, report.route_numbers.get(r.key)) for r in api.routes[:_LISTED_ROUTES]]
    out: dict[str, Any] = {
        "name": api.name, "id": api.id, "kind": api.kind_label, "region": api.region or None,
        "url": api.url(stage.name if stage else None), "endpoint_type": api.endpoint_type or None,
        "stage": stage.name if stage else None, "stages": [_stage_row(s) for s in api.stages],
        "numbers": _api_numbers(report.numbers), "routes_total": len(api.routes), "routes": routes,
        "authorizers": [a.label for a in api.authorizers.values()], "lambda_functions": api.functions,
        "estimated_monthly_cost": human_money(cost) if cost is not None else None,
        "findings": model_findings(apigw.api_findings(report, prices)),
    }
    if len(api.routes) > _LISTED_ROUTES:
        out["more"] = f"{len(api.routes) - _LISTED_ROUTES:,} more routes not listed"
    errors = {**api.errors, **report.errors}
    if errors:
        out["errors"] = _with_permissions(errors, _SECTION_PERMISSIONS)
    return out


def _call_row(call: Call) -> dict[str, Any]:
    """
    One call as its access log recorded it: when, its route, status and error, how long it took (and how much of
    that the authorizer and integration took), and its request IDs.
    """
    row: dict[str, Any] = {"time": call.time, "route": call.route_key, "status": call.status,
                           "why": hide_secrets(call.why) or None, "latency_ms": call.latency_ms,
                           "integration_ms": call.integration_ms, "authorizer_ms": call.authorizer_ms,
                           "request_id": call.request_id or None, "lambda_request_id": call.lambda_request or None}
    return {key: value for key, value in row.items() if value is not None}


def _error_group_row(group: ErrorGroup, total: int) -> dict[str, Any]:
    """
    One kind of failed call: its status, error and route, how many and their share of the failed calls read, and
    the newest one.
    """
    return {"status": group.status, "error": hide_secrets(group.error) or None, "route": group.route,
            "count": group.count, "share_pct": share_pct(group.count, total), "newest": _call_row(group.newest)}


def _run_line(line: RunLine) -> str:
    """
    One line of a Lambda run as the model reads it: its time in UTC, ERROR when it reports one, and its text cut
    short with anything that looks like a secret hidden.
    """
    marker = "ERROR " if line.is_error else ""
    return f"{line.time.astimezone(timezone.utc):%H:%M:%SZ} {marker}{hide_secrets(_clip(line.text.strip(), _LINE_CHARS))}"


def summarize_lambda_run(run: LambdaRun) -> dict[str, Any]:
    """
    The Lambda run behind a call as the model reads it: the function, its request ID, how it ended and how long it
    ran, its last lines (secrets hidden, error lines marked), or why it couldn't be read.
    """
    out: dict[str, Any] = {"function": run.function or None, "request_id": run.request_id or None,
                           "found": run.found, "note": run.note or None, "status": run.status or None,
                           "duration_ms": run.duration_ms}
    if run.lines:
        out["lines"] = [_run_line(line) for line in run.lines[-_RUN_LINES:]]
    if len(run.lines) > _RUN_LINES:
        out["lines_left_out"] = len(run.lines) - _RUN_LINES
    return out


def _log_note(page: CallPage | None, stage: Stage | None) -> str | None:
    """
    Why a stage's calls can't be read, when they can't: no stage, no access log, or a log that couldn't be read.
    """
    if stage is None:
        return "the API has no stage"
    if not stage.has_access_log:
        return (f"stage {stage.name} keeps no access log, so its failed calls can't be listed: only CloudWatch's "
                "counts are known")
    return page.note if page is not None and page.note else None


def summarize_api_errors(report: ErrorReport, run: LambdaRun | None = None) -> dict[str, Any]:
    """
    api_errors()'s answer: CloudWatch's counts over the window, the failed calls the stage's access log recorded,
    grouped by status, error and route (the biggest first, each with its share and newest call), the Lambda run
    behind the newest failed call that called Lambda, and the findings.
    """
    apigw = _apigw_module()
    page = report.page
    failed = page.failed if page is not None else []
    grouped = sum(g.count for g in report.groups)
    out: dict[str, Any] = {
        "api": report.api.name, "stage": report.stage.name if report.stage else None, "since": report.since,
        "cloudwatch": _api_numbers(report.numbers), "calls_read": len(page.calls) if page else 0,
        "failed_calls": len(failed), "groups": [_error_group_row(g, grouped) for g in report.groups[:_ERROR_GROUPS]],
        "cut_short": bool(page and page.truncated), "findings": model_findings(apigw.error_findings(report)),
        "window": _window(report.since, page.until if page is not None else datetime.now(timezone.utc)),
    }
    note = _log_note(page, report.stage)
    if note:
        out["note"] = note
    if page is not None and page.truncated:
        out["stopped_at"] = (f"the newest {len(page.calls):,} access log lines: the groups cover those only; "
                             "CloudWatch's counts cover the whole window")
    if run is not None:
        out["lambda_run"] = summarize_lambda_run(run)
    if report.errors:
        out["errors"] = _with_permissions(report.errors, _SECTION_PERMISSIONS)
    return out


def summarize_calls(page: CallPage, *, limit: int = _DEFAULT_CALLS, status: str = "") -> dict[str, Any]:
    """
    api_calls()'s answer: the calls a stage's access log recorded in the window, newest first (only the ones
    whose status starts with `status` when it's given), with how many got each status.
    """
    counts: dict[str, int] = {}
    for call in page.calls:
        key = str(call.status) if call.status is not None else "unknown"
        counts[key] = counts.get(key, 0) + 1
    picked = [c for c in page.calls if not status or str(c.status or "").startswith(status)]
    out: dict[str, Any] = {"api": page.api.name, "stage": page.stage.name if page.stage else None,
                           "since": page.since, "until": page.until, "calls_read": len(page.calls),
                           "statuses": counts, "matching": len(picked),
                           "calls": [_call_row(c) for c in picked[:limit]], "cut_short": page.truncated,
                           "window": _window(page.since, page.until)}
    if len(picked) > limit:
        out["more"] = f"{len(picked) - limit:,} more not listed: narrow with search=, status= or since="
    note = _log_note(page, page.stage)
    if note:
        out["note"] = note
    if page.unread:
        out["unread_lines"] = page.unread
    if page.fields and "integrationRequestId" not in " ".join(page.fields):
        out["no_lambda_request_ids"] = ("the stage's access log format doesn't record "
                                        "$context.integration.requestId, so a call's Lambda run can't be found")
    return out


def _filter_pattern(search: str) -> str | None:
    """
    search= as a CloudWatch Logs filter pattern: text in double quotes, so a request ID or path matches as it is.
    A pattern the model wrote itself ({ $.status = "504" }) is kept.
    """
    text = str(search or "").strip()
    if not text:
        return None
    if text.startswith(("{", '"')):
        return text
    return '"' + text.replace('"', '') + '"'


class APIGatewayToolkit(Toolkit):
    """
    The agent's tools for Amazon API Gateway (REST, HTTP and WebSocket APIs), reading through APIGatewayAnalyzer:
    `analyzer` is one to use (else one is made on the agent's session, in its region). When the agent has the
    Lambda toolkit too, a call's Lambda run is read through that toolkit's LambdaAnalyzer.
    """

    name = "apigateway"
    title = "API Gateway"
    reads = APIGATEWAY_TOOL_READS
    service = "apigateway"
    services = {"GetMetricData": "cloudwatch", "FilterLogEvents": "logs", "DescribeLogGroups": "logs",
                "GetFunction": "lambda"}
    view = "APIGatewayView"
    privacy = ("API Gateway: access log lines (never request bodies) and the Lambda run behind a call, "
               "secrets hidden.")
    covers = ("Amazon API Gateway: which REST, HTTP and WebSocket APIs exist and how they did (requests, 4xx and "
              "5xx errors, latency, cost), each API's stages and routes (who may call each, what it calls), which "
              "calls fail and why, grouped by status, error and route from the stage's access log, and the Lambda "
              "run behind a failed call.")

    def view_class(self) -> type:
        """
        APIGatewayView, whose commands the agent's findings can name.
        """
        return _apigw_module().APIGatewayView

    def make_analyzer(self, session: Any) -> APIGatewayAnalyzer:
        lambda_kit = self.peer("lambda")
        lambda_core = lambda_kit.analyzer if lambda_kit is not None else None
        return _apigw_module().APIGatewayAnalyzer(session, lambda_core=lambda_core)

    @property
    def analyzer(self) -> APIGatewayAnalyzer:
        """
        The APIGatewayAnalyzer the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def permission_for(self, exc: ClientError) -> str:
        """
        The IAM permission a refused call needed: apigateway:GET for every API Gateway read, else the operation's.
        """
        op = getattr(exc, "operation_name", "") or ""
        if op.startswith("Get") and op not in self.services:
            return _GET
        return super().permission_for(exc)

    def instructions(self, budget: Budget) -> str:
        return f"""\
Amazon API Gateway
- APIs are regional: these tools read {self._region_text()}. Name an API by its name or ID; 'name/stage' or stage=
  picks a stage (else its main one: prod, $default, or the one deployed last).
- For "which API is failing" or "are my APIs OK", start with find_apis: every API's requests and 4xx / 5xx errors
  over the last days from CloudWatch, the ones with the most 5xx first. api_details shows one API's routes (who may
  call each, what it calls) and stages.
- For "why does it return 502 / 504" or "why do calls fail", api_errors groups the failed calls in the stage's
  access log by status, error and route, and reads the Lambda run behind the newest one. api_calls lists calls
  and finds one by a request ID, a path or any text (search=).
- A stage without an access log has only CloudWatch's counts: say so, and give the command the findings name
  that turns the log on.{self._lambda_rule()}
- Findings name APIGatewayView commands (errors(), stages(), performance()): api_errors, api_details and api_calls
  are your tools for those. Nothing here calls an API or changes one."""

    def _lambda_rule(self) -> str:
        """
        The prompt's line on following a call into Lambda, when the agent has the Lambda tools.
        """
        if self.peer("lambda") is None:
            return ""
        return """
- A call's lambda_request_id is the request ID of the Lambda run behind it: function_logs(function,
  search=lambda_request_id) reads that run, and function_errors, function_performance and function_source go on
  from there. A 504 means the integration took longer than API Gateway waits (29 s for REST): compare it with the
  function's timeout and run times."""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        find_apis, api_details, api_errors and api_calls: methods whose type hints and docstrings tell the model how
        to call them, each reading through APIGatewayAnalyzer and returning a compact, JSON-ready summary.
        """
        return [self.find_apis, self.api_details, self.api_errors, self.api_calls]

    def find_apis(self, match: str = "", days: int = _DAYS) -> dict[str, Any]:
        """
        Lists the API Gateway APIs in the region (REST, HTTP and WebSocket) with how they did over the last few
        whole days (days=), from CloudWatch: requests, 4xx and 5xx errors and their rates, latency, each API's stages,
        routes (and how many anyone can call), the Lambda functions it calls, whether it keeps an access log, its
        estimated monthly cost, and the region's findings. The ones with the most 5xx errors come first. match: a
        name glob like 'orders-*'. days: 1 to 90.
        """
        core = self.analyzer
        overview = core.overview(days=clamp_count(days, _DAYS, _MOST_DAYS))
        pattern = str(match or "").strip().lower()
        if pattern:
            overview.apis = [a for a in overview.apis if fnmatch.fnmatch(a.name.lower(), pattern)]
        self._count_cloudwatch(overview.metrics_read)
        out = summarize_apis(overview, core.prices)
        failing = [r for r in out["apis"] if _errors_5xx(r)]
        out["_summary"] = (f"{_plural(len(overview.apis), 'API')} in {overview.region}"
                           + (", 5xx errors in " + ", ".join(f"{r['name']} ({_errors_5xx(r):,})" for r in failing[:3])
                              if failing else ""))
        out["_next"] = [(self._view_call("apis"), "every API with its numbers and warnings")]
        out["_next"] += [(self._view_call("errors", r["name"]), "which calls fail and why") for r in failing[:2]]
        return out

    def api_details(self, api: str, stage: str = "", days: int = _DAYS) -> dict[str, Any]:
        """
        One API in plain words: its kind, URL and stages (throttling, logging, access log, cache, tracing), the
        stage's numbers over the last days (days=), each route (who may call it, where it sends a request: the
        Lambda function, URL or AWS call, and how long API Gateway waits), its estimated cost and the findings.
        api: its name or ID ('name/stage' picks a stage). stage: '' for its main one. days: 1 to 90.
        """
        core = self.analyzer
        report = core.report(api, stage=str(stage or "").strip() or None, days=clamp_count(days, _DAYS, _MOST_DAYS))
        self._count_cloudwatch(report.metrics_read)
        out = summarize_api(report, core.prices)
        out["region"] = core.region
        numbers = out["numbers"] or {}
        out["_summary"] = (f"{report.api.kind_label}, {_plural(len(report.api.routes), 'route')}"
                           + (f", {numbers['requests']:,} requests, {numbers['errors_5xx']:,} 5xx"
                              if numbers else ""))
        out["_next"] = [(self._view_call("api_info", report.api.name), "its stages, routes and findings"),
                        (self._view_call("routes", report.api.name), "every route and what it calls")]
        return out

    def api_errors(self, api: str, stage: str = "", since: str = "24h") -> dict[str, Any]:
        """
        What's failing in an API's stage in a window: CloudWatch's request, 4xx and 5xx counts over the window,
        and when the stage keeps an access log, its failed calls grouped by status, error and route (the biggest
        group first, each with its share and newest call), plus the log lines of the Lambda run behind the newest
        failed call that called Lambda (found by the request ID the access log recorded), and the findings. api:
        its name or ID ('name/stage' picks a stage). since: ISO UTC ('2026-10-15T18:00Z') or relative ('24h').
        """
        core = self.analyzer
        report = core.errors(api, stage=str(stage or "").strip() or None, since=since or "24h", limit=_LINES_READ)
        run = self._newest_run(report)
        out = summarize_api_errors(report, run)
        out["region"] = core.region
        groups = out["groups"]
        out["_summary"] = (f"{_plural(out['failed_calls'], 'failed call')}"
                           + (", " + "; ".join(f"{g['status']} {g['route']} ×{g['count']}" for g in groups[:2])
                              if groups else "") + (", with a Lambda run" if run is not None and run.found else ""))
        out["_next"] = [(self._view_call("errors", report.api.name, since=since or None),
                         "the same failures as a report")]
        return out

    def _newest_run(self, report: ErrorReport) -> LambdaRun | None:
        """
        The Lambda run behind the newest failed call that names one; None when none does.
        """
        page = report.page
        if page is None:
            return None
        call = next((c for c in page.failed if c.lambda_request), None)
        return self.analyzer.lambda_run(report.api, call) if call is not None else None

    def api_calls(self, api: str, stage: str = "", since: str = "1h", until: str = "",  # noqa: PLR0913
                  search: str = "", status: str = "", limit: int = _DEFAULT_CALLS) -> dict[str, Any]:
        """
        The calls an API's stage recorded in its access log in a window, newest first: each one's
        time, route, status, error, latency (and how much of it the authorizer and integration took) and request
        IDs, with how many got each status. search: text a line must contain, like a request ID, a path or an
        error ('' for all). status: keep calls whose status starts with it ('5', '502'). When exactly one call
        matches and it called Lambda, its Lambda run's log lines come too. since/until: ISO UTC or relative ('1h');
        until defaults to now. limit: at most 100.
        """
        core = self.analyzer
        page = core.calls(api, stage=str(stage or "").strip() or None, since=since or "1h", until=until or None,
                          limit=_LINES_READ, pattern=_filter_pattern(search))
        wanted = str(status or "").strip()
        out = summarize_calls(page, limit=clamp_count(limit, _DEFAULT_CALLS, _MOST_CALLS), status=wanted)
        out["region"] = core.region
        picked = [c for c in page.calls if not wanted or str(c.status or "").startswith(wanted)]
        if len(picked) == 1 and picked[0].lambda_request:
            out["lambda_run"] = summarize_lambda_run(core.lambda_run(page.api, picked[0]))
        out["_summary"] = f"{_plural(out['matching'], 'call')}" + (f" of {len(page.calls):,} read"
                                                                     if wanted else "")
        out["_next"] = [(self._view_call("calls", page.api.name, since=since or None,
                                         search=str(search or "").strip() or None), "the same calls as a report")]
        return out

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """
        A tool's answer when an API isn't there: the closest API names.
        """
        if _error_code(exc) != "NotFoundException":
            return None
        wanted = str(arguments.get("api") or "").strip()
        try:
            names = [a.name for a in self.analyzer.list_apis()]
        except (ClientError, BotoCoreError):
            names = []
        return {"error": f"No API {wanted!r} in {self.analyzer.region}. APIs are regional.",
                "close_names": difflib.get_close_matches(wanted.split("/")[0], names, n=3, cutoff=0.5),
                "hint": "Use one of close_names, or find_apis to list them."}
