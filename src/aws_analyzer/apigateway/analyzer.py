"""
APIGatewayAnalyzer: talks to API Gateway (apigateway for REST APIs, apigatewayv2 for HTTP and WebSocket APIs),
CloudWatch and CloudWatch Logs, and returns the dataclasses in models. For a call that reached a Lambda function it
reads that run's log lines through the Lambda analyzer (aws_analyzer.lambda_functions, loaded the first time it's
needed). Never prints, never sends a request to an API, and never changes one.
"""

from __future__ import annotations

import difflib
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _Hint
from .._kit.fmt import _as_count, _utcnow, parse_time

from .helpers import parse_api_ref
from .models import (
    Api, ApiNumbers, ApiReport, Call, CallPage, ErrorReport, LambdaRun, Overview, RouteNumbers, RunLine, Stage,
)
from .prices import APIGATEWAY_PRICES
from .analysis import (
    group_errors, parse_access_line, parse_rest_api, parse_rest_authorizer, parse_rest_routes, parse_rest_stage,
    parse_v2_api, parse_v2_authorizer, parse_v2_integration, parse_v2_routes, parse_v2_stage, read_format,
)


_DAY_SECONDS = 86_400

_HOUR_SECONDS = 3_600

_QUERIES_PER_CALL = 500

_MOST_ROUTES = 100

_FIRST_SLICE = timedelta(minutes=15)

_SLICE_CAP = 20_000

_LOG_PAD = timedelta(minutes=2)

_COUNTS = {
    "REST": {"requests": "Count", "errors_4xx": "4XXError", "errors_5xx": "5XXError"},
    "HTTP": {"requests": "Count", "errors_4xx": "4xx", "errors_5xx": "5xx"},
    "WEBSOCKET": {"requests": "MessageCount", "errors_4xx": "ClientError", "errors_5xx": "ExecutionError",
                  "connections": "ConnectCount"},
}

_TIMES = (("latency_ms", "Latency", "Average"), ("p95_ms", "Latency", "p95"),
          ("integration_ms", "IntegrationLatency", "Average"))

_ROUTE_METRICS = (("calls", "Count", "Sum"), ("errors_4xx", "4XX", "Sum"), ("errors_5xx", "5XX", "Sum"),
                  ("avg_ms", "Latency", "Average"), ("p95_ms", "Latency", "p95"), ("max_ms", "Latency", "Maximum"))

_DAILY = ("requests", "errors_5xx")

_ERROR_WORDS = ("[ERROR]", "Traceback", "Task timed out", "\"level\":\"ERROR\"", "Error:", "errorMessage")


def _lambda_module() -> Any:
    """
    aws_analyzer.lambda_functions, whose log reading a call's Lambda run reuses. Loaded when it's first needed, so
    importing this module loads no other analyzer.
    """
    from .. import lambda_functions

    return lambda_functions


def _midnight_after(now: datetime) -> datetime:
    """
    The UTC midnight that ends today: CloudWatch's daily numbers cover whole UTC days.
    """
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


def _dimensions(api: Api, stage: str | None) -> list[dict[str, str]]:
    """
    How CloudWatch names an API's metrics: a REST API by its name, an HTTP or WebSocket API by its ID, with the stage
    when one is asked for.
    """
    dims = [{"Name": "ApiName", "Value": api.name}] if api.kind == "REST" else [{"Name": "ApiId", "Value": api.id}]
    if stage:
        dims.append({"Name": "Stage", "Value": stage})
    return dims


def _route_dimensions(api: Api, stage: str, route: Any) -> list[dict[str, str]]:
    """
    How CloudWatch names one route's detailed metrics: method and resource for REST, the route key for HTTP.
    """
    if api.kind == "REST":
        return [{"Name": "ApiName", "Value": api.name}, {"Name": "Method", "Value": route.method},
                {"Name": "Resource", "Value": route.path}, {"Name": "Stage", "Value": stage}]
    return [{"Name": "ApiId", "Value": api.id}, {"Name": "Stage", "Value": stage}, {"Name": "Route", "Value": route.key}]


def _query(query_id: str, metric: str, dims: list[dict[str, str]], period: int, stat: str) -> dict[str, Any]:
    return {"Id": query_id, "MetricStat": {"Metric": {"Namespace": "AWS/ApiGateway", "MetricName": metric,
                                                      "Dimensions": dims}, "Period": period, "Stat": stat}}


def _route_metric(api: Api, metric: str) -> str:
    """
    A route metric's name for the API's kind: REST counts errors as 4XXError / 5XXError, HTTP as 4xx / 5xx.
    """
    if metric in ("4XX", "5XX"):
        return f"{metric}Error" if api.kind == "REST" else metric.lower()
    return metric


class APIGatewayAnalyzer(_BaseAnalyzer):
    """
    Pure-logic API Gateway analysis: every method returns data; nothing is printed, called or changed.

    Methods about one API take its name, its ID, 'name/stage', its invoke URL or a console link. `prices` overrides
    APIGATEWAY_PRICES. `client` is the apigateway (REST) client and `clients` other boto3 clients by service name
    ('apigateway', 'apigatewayv2', 'cloudwatch', 'logs'). `lambda_core` is the LambdaAnalyzer that reads a call's
    Lambda run (one is made on this session when it's first needed).
    """

    _LIST_PRICES = APIGATEWAY_PRICES

    def __init__(  # noqa: PLR0913
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        lambda_core: Any = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["apigateway"] = client
        self._lock = threading.Lock()
        self.max_workers = 8
        self._lambda_core = lambda_core
        self._listed: list[Api] | None = None
        self._read: dict[str, Api] = {}

    @property
    def client(self) -> Any:
        """
        The apigateway client (REST APIs), made on first use so a missing region shows up as a readable error.
        """
        return self._cached_client("apigateway", self._make_client)

    def _make_client(self) -> Any:
        try:
            return self.session.client("apigateway", config=self._config)
        except NoRegionError:
            raise ValueError(
                "No AWS region is set, and APIs are regional. Pass one: "
                "APIGatewayView(APIGatewayAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
            ) from None

    def _v2(self) -> Any:
        return self._cached_client(
            "apigatewayv2", lambda: self.session.client("apigatewayv2", region_name=self.region, config=self._config))

    def _cloudwatch(self) -> Any:
        return self._cached_client(
            "cloudwatch", lambda: self.session.client("cloudwatch", region_name=self.region, config=self._config))

    def _logs(self) -> Any:
        return self._cached_client(
            "logs", lambda: self.session.client("logs", region_name=self.region, config=self._config))

    @property
    def lambda_core(self) -> Any:
        """
        The LambdaAnalyzer that reads a call's Lambda run, on this analyzer's session.
        """
        if self._lambda_core is None:
            with self._lock:
                if self._lambda_core is None:
                    self._lambda_core = _lambda_module().LambdaAnalyzer(self.session)
        return self._lambda_core


    def _pages(self, client: Any, operation: str, key: str, **params: Any) -> list[dict[str, Any]]:
        """
        Every item under `key` from every page of a list operation, following position / NextToken.
        """
        items: list[dict[str, Any]] = []
        token_in, token_out = ("position", "position") if client is self.client else ("NextToken", "NextToken")
        while True:
            answer = getattr(client, operation)(**params)
            items += answer.get(key, [])
            token = answer.get(token_out)
            if not token:
                return items
            params[token_in] = token

    def list_apis(self) -> list[Api]:
        """
        Every API in the region, REST (apigateway) and HTTP / WebSocket (apigatewayv2), with its name, kind and
        dates only (api() reads the rest).
        """
        region = self.region
        rest = [parse_rest_api(item, region) for item in self._pages(self.client, "get_rest_apis", "items",
                                                                    limit=500)]
        others = [parse_v2_api(item, region) for item in self._pages(self._v2(), "get_apis", "Items",
                                                                    MaxResults="500")]
        self._listed = sorted(rest + others, key=lambda a: a.name.lower())
        return list(self._listed)

    def resolve(self, ref: str) -> Api:
        """
        An API (as listed) from its name, ID, 'name/stage', invoke URL or console link; an unknown one raises a _Hint
        with the closest names.
        """
        wanted, _ = parse_api_ref(ref)
        listed = self._listed if self._listed is not None else self.list_apis()
        found = _find(listed, wanted)
        if found is None and self._listed is not None:
            found = _find(self.list_apis(), wanted)
        if found is not None:
            return found
        names = [a.name for a in self._listed or []]
        close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.5)
        hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
        raise _Hint(f"No API named {wanted!r} in {self.region}.{hint} apis() lists them.")

    def api(self, ref: str | Api) -> Api:
        """
        One API with its stages, routes (each with its integration and authorizer) and authorizers. Parts that
        couldn't be read are in api.errors.
        """
        listed = ref if isinstance(ref, Api) else self.resolve(ref)
        if listed.id in self._read:
            return self._read[listed.id]
        api = Api(**{**listed.__dict__, "stages": [], "routes": [], "authorizers": {}, "errors": {}})
        reader = self._read_rest if api.kind == "REST" else self._read_v2
        for part, read in reader(api):
            try:
                read()
            except ClientError as exc:
                api.errors[part] = _error_code(exc)
        api.read = True
        self._read[api.id] = api
        return api

    def _read_rest(self, api: Api) -> list[tuple[str, Callable[[], None]]]:
        def authorizers() -> None:
            items = self._pages(self.client, "get_authorizers", "items", restApiId=api.id)
            api.authorizers = {item["id"]: parse_rest_authorizer(item) for item in items}

        def routes() -> None:
            resources = self._pages(self.client, "get_resources", "items", restApiId=api.id, embed=["methods"],
                                    limit=500)
            api.routes = parse_rest_routes(resources, api.authorizers)

        def stages() -> None:
            items = self.client.get_stages(restApiId=api.id).get("item", [])
            api.stages = [parse_rest_stage(item) for item in items]

        return [("authorizers", authorizers), ("routes", routes), ("stages", stages)]

    def _read_v2(self, api: Api) -> list[tuple[str, Callable[[], None]]]:
        v2 = self._v2()

        def routes() -> None:
            integrations = {item["IntegrationId"]: parse_v2_integration(item)
                            for item in self._pages(v2, "get_integrations", "Items", ApiId=api.id)}
            items = self._pages(v2, "get_routes", "Items", ApiId=api.id)
            ids = sorted({item["AuthorizerId"] for item in items if item.get("AuthorizerId")})
            api.authorizers = {i: parse_v2_authorizer(v2.get_authorizer(ApiId=api.id, AuthorizerId=i)) for i in ids}
            api.routes = parse_v2_routes(items, integrations, api.authorizers)

        def stages() -> None:
            api.stages = [parse_v2_stage(item) for item in self._pages(v2, "get_stages", "Items", ApiId=api.id)]

        return [("routes", routes), ("stages", stages)]

    def apis(self) -> tuple[list[Api], dict[str, str]]:
        """
        Every API in the region, each read with its stages and routes, and the ones that couldn't be listed whole
        (name -> error code).
        """
        apis = self._map(self.api, self.list_apis())
        errors = {a.name: next(iter(a.errors.values())) for a in apis if a.errors}
        return apis, errors


    def numbers(self, apis: list[Api], *, days: int = 7, stage: str | None = None) -> tuple[dict[str, ApiNumbers], int]:
        """
        Each API's CloudWatch numbers over the last `days` whole UTC days (API ID -> ApiNumbers), for one stage when
        stage= names it, and how many metrics were read (GetMetricData bills per metric).
        """
        end = _midnight_after(_utcnow())
        start = end - timedelta(days=days)
        queries, slots = [], {}
        for i, api in enumerate(apis):
            dims = _dimensions(api, stage)
            for field_name, metric in _COUNTS.get(api.kind, _COUNTS["REST"]).items():
                period = _DAY_SECONDS if field_name in _DAILY else days * _DAY_SECONDS
                slots[f"c{i}_{field_name}"] = (api.id, field_name)
                queries.append(_query(f"c{i}_{field_name}", metric, dims, period, "Sum"))
            for field_name, metric, stat in _TIMES:
                if api.kind == "WEBSOCKET" and metric == "Latency":
                    continue
                slots[f"t{i}_{field_name}"] = (api.id, field_name)
                queries.append(_query(f"t{i}_{field_name}", metric, dims, days * _DAY_SECONDS, stat))
        numbers = {a.id: ApiNumbers(days=days, daily_requests=[0] * days, daily_5xx=[0] * days) for a in apis}
        for result in self._metric_results(queries, start, end):
            api_id, field_name = slots[result["Id"]]
            _add_metric(numbers[api_id], field_name, result, start)
        return numbers, len(queries)

    def _metric_results(self, queries: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for begin in range(0, len(queries), _QUERIES_PER_CALL):
            params: dict[str, Any] = {"MetricDataQueries": queries[begin: begin + _QUERIES_PER_CALL],
                                      "StartTime": start, "EndTime": end}
            while True:
                answer = self._cloudwatch().get_metric_data(**params)
                results += answer.get("MetricDataResults", [])
                if not answer.get("NextToken"):
                    break
                params["NextToken"] = answer["NextToken"]
        return results

    def route_numbers(self, api: Api, stage: Stage, *, days: int = 7) -> tuple[dict[str, RouteNumbers], int]:
        """
        Each route's numbers over `days` days from the stage's detailed metrics (route key -> RouteNumbers; empty
        when the stage has none, or for a WebSocket API), and how many metrics were read.
        """
        if not stage.detailed_metrics or api.kind == "WEBSOCKET":
            return {}, 0
        end = _midnight_after(_utcnow())
        start = end - timedelta(days=days)
        period = days * _DAY_SECONDS
        queries, slots = [], {}
        for i, route in enumerate(api.routes[:_MOST_ROUTES]):
            dims = _route_dimensions(api, stage.name, route)
            for field_name, metric, stat in _ROUTE_METRICS:
                slots[f"r{i}_{field_name}"] = (route.key, field_name)
                queries.append(_query(f"r{i}_{field_name}", _route_metric(api, metric), dims, period, stat))
        numbers: dict[str, RouteNumbers] = {}
        for result in self._metric_results(queries, start, end):
            key, field_name = slots[result["Id"]]
            values = result.get("Values") or []
            if values:
                route_numbers = numbers.setdefault(key, RouteNumbers())
                value = values[0]
                setattr(route_numbers, field_name, int(value) if field_name in ("calls", "errors_4xx", "errors_5xx")
                        else float(value))
        return numbers, len(queries)

    def overview(self, *, days: int = 7) -> Overview:
        """
        Every API in the region with its numbers over the last `days` days. Parts that couldn't be read are in
        errors ('metrics', or an API's name).
        """
        apis, errors = self.apis()
        overview = Overview(region=self.region, days=days, apis=apis, errors=dict(errors))
        if not apis:
            return overview
        try:
            overview.numbers, overview.metrics_read = self.numbers(apis, days=days)
        except (ClientError, BotoCoreError) as exc:
            overview.errors["metrics"] = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        return overview

    def report(self, ref: str, *, stage: str | None = None, days: int = 7) -> ApiReport:
        """
        One API in detail: its stage (the one named in ref or stage=, else its main one) with its numbers over `days`
        days, and each route's when the stage has detailed metrics.
        """
        _, named = parse_api_ref(ref)
        api = self.api(ref)
        chosen = api.stage(stage or named)
        if (stage or named) and chosen is None:
            raise _Hint(f"{api.name} has no stage {stage or named!r}: its stages are "
                        f"{', '.join(s.name for s in api.stages) or 'none'}.")
        report = ApiReport(api, chosen, days)
        try:
            numbers, read = self.numbers([api], days=days, stage=chosen.name if chosen else None)
            report.numbers, report.metrics_read = numbers.get(api.id), read
            if chosen is not None:
                report.route_numbers, routes_read = self.route_numbers(api, chosen, days=days)
                report.metrics_read += routes_read
        except (ClientError, BotoCoreError) as exc:
            report.errors["metrics"] = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        return report


    def calls(  # noqa: PLR0913
        self,
        ref: str,
        *,
        stage: str | None = None,
        since: Any = "24h",
        until: Any = None,
        limit: Any = 5_000,
        pattern: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> CallPage:
        """
        The calls a stage's access log recorded from `since` to `until`, newest first: at most the newest `limit`
        lines (page.truncated says when there were more), matching a CloudWatch Logs filter pattern when one is
        given ('"c1f08a2e"', '{ $.status = "504" }'). A stage without an access log gives an empty page with a note.
        """
        api, chosen = self._api_stage(ref, stage)
        start = parse_time(since) or _utcnow() - timedelta(days=1)
        end = parse_time(until) or _utcnow()
        page = CallPage(api, chosen, since=start, until=end)
        if chosen is None or not chosen.has_access_log:
            page.note = "no access log" if chosen is not None else "no stage"
            return page
        page.log_group = chosen.access_log_group
        log_format = read_format(chosen.access_log_format)
        page.fields = set(log_format.fields)
        events, page.truncated = self._read_lines(chosen.access_log_group, start, end,
                                                  _as_count(limit, "limit") or _SLICE_CAP, pattern, progress)
        for event in events:
            when = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
            call = parse_access_line(event.get("message", ""), when, log_format)
            if call is None:
                page.unread += 1
            else:
                page.calls.append(call)
        page.calls.sort(key=lambda c: c.time, reverse=True)
        return page

    def _api_stage(self, ref: str, stage: str | None) -> tuple[Api, Stage | None]:
        _, named = parse_api_ref(ref)
        api = self.api(ref)
        chosen = api.stage(stage or named)
        if (stage or named) and chosen is None:
            raise _Hint(f"{api.name} has no stage {stage or named!r}: its stages are "
                        f"{', '.join(s.name for s in api.stages) or 'none'}.")
        return api, chosen

    def _read_lines(self, group: str, start: datetime, end: datetime, limit: int, pattern: str | None,  # noqa: PLR0913
                    progress: Callable[..., None] | None) -> tuple[list[dict[str, Any]], bool]:
        """
        A log group's newest `limit` lines from start to end: read backwards in slices that double, so a busy log's
        newest lines are the ones kept.
        """
        kept: list[dict[str, Any]] = []
        slice_end, width = end, _FIRST_SLICE
        while slice_end > start and len(kept) < limit:
            slice_start = max(start, slice_end - width)
            lines = self._filter(group, slice_start, slice_end, pattern)
            kept = lines[-(limit - len(kept)):] + kept if lines else kept
            if progress is not None:
                progress(len(kept))
            if len(lines) >= _SLICE_CAP:
                return kept, True
            slice_end, width = slice_start, width * 2
        return kept, slice_end > start

    def _filter(self, group: str, start: datetime, end: datetime, pattern: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"logGroupName": group, "startTime": int(start.timestamp() * 1000),
                                  "endTime": int(end.timestamp() * 1000)}
        if pattern:
            params["filterPattern"] = pattern
        lines: list[dict[str, Any]] = []
        while len(lines) < _SLICE_CAP:
            answer = self._logs().filter_log_events(**params)  # read-only: FilterLogEvents reads log lines
            lines += answer.get("events", [])
            if not answer.get("nextToken"):
                break
            params["nextToken"] = answer["nextToken"]
        lines.sort(key=lambda e: e.get("timestamp", 0))
        return lines

    def errors(self, ref: str, *, stage: str | None = None, since: Any = "24h", limit: Any = 5_000,
               progress: Callable[..., None] | None = None) -> ErrorReport:
        """
        What's failing in a stage since `since`: CloudWatch's counts over that time, and when the stage has an access
        log, the failed calls in its newest `limit` lines, grouped by status, error and route.
        """
        api, chosen = self._api_stage(ref, stage)
        start = parse_time(since) or _utcnow() - timedelta(days=1)
        report = ErrorReport(api, chosen, since=start)
        try:
            report.numbers = self._window_numbers(api, chosen, start)
        except (ClientError, BotoCoreError) as exc:
            report.errors["metrics"] = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        if chosen is not None and chosen.has_access_log:
            report.page = self.calls(api.id, stage=chosen.name, since=start, limit=limit, progress=progress)
            report.groups = group_errors(report.page.calls)
        return report

    def _window_numbers(self, api: Api, stage: Stage | None, start: datetime) -> ApiNumbers:
        """
        An API's counts from `start` to now, hour by hour (requests, 4xx, 5xx, and its latency).
        """
        now = _utcnow()
        first = start.replace(minute=0, second=0, microsecond=0)
        hours = max(1, int((now - first).total_seconds() // _HOUR_SECONDS) + 1)
        dims = _dimensions(api, stage.name if stage else None)
        queries, slots = [], {}
        for field_name, metric in _COUNTS.get(api.kind, _COUNTS["REST"]).items():
            slots[f"w_{field_name}"] = field_name
            queries.append(_query(f"w_{field_name}", metric, dims, hours * _HOUR_SECONDS, "Sum"))
        numbers = ApiNumbers(days=max(1, hours // 24))
        for result in self._metric_results(queries, first, first + timedelta(hours=hours)):
            values = result.get("Values") or []
            setattr(numbers, slots[result["Id"]], int(sum(values)))
        return numbers


    def lambda_run(self, api: Api, call: Call) -> LambdaRun:
        """
        The Lambda run behind a call, found by the Lambda request ID its access log recorded: its lines, how long it
        ran and how it ended, or a note saying why it couldn't be read.
        """
        route = api.route(call.route_key)
        function = route.integration.function if route and route.integration else ""
        found = LambdaRun(function, call.lambda_request)
        if not function:
            found.note = "its route doesn't call a Lambda function"
            return found
        if not call.lambda_request:
            found.note = "the access log doesn't record the Lambda request ID ($context.integration.requestId)"
            return found
        try:
            run = self._lambda_log_run(route.integration, call)
        except ClientError as exc:
            found.note = f"{function}'s logs couldn't be read ({_error_code(exc)})"
            return found
        except BotoCoreError as exc:
            found.note = f"{function}'s logs couldn't be read ({type(exc).__name__})"
            return found
        if run is None:
            found.note = "no run with that request ID is in its logs"
            return found
        return _with_run(found, run)

    def _lambda_log_run(self, integration: Any, call: Call) -> Any:
        took = timedelta(milliseconds=call.latency_ms or 0)
        page = self.lambda_core.log_events(integration.function, since=call.time - took - _LOG_PAD,
                                           until=call.time + _LOG_PAD, request_id=call.lambda_request,
                                           region=integration.function_region or None)
        code = (page.errors or {}).get("logs")
        if code:
            raise ClientError({"Error": {"Code": code, "Message": code}}, "FilterLogEvents")
        runs = _lambda_module().split_runs(page.events)
        return next((r for r in runs if r.request_id == call.lambda_request), None)


def _with_run(found: LambdaRun, run: Any) -> LambdaRun:
    """
    A LambdaRun filled from aws_analyzer.lambda_functions' LogRun: each line made readable, error lines marked.
    """
    lambda_functions = _lambda_module()
    for event in run.events:
        text = lambda_functions.readable_line(event.message)
        found.lines.append(RunLine(event.time, text, is_error=_is_error_line(event.message)))
    if run.report is not None:
        found.duration_ms = run.report.duration
        found.status = run.report.status or ""
    found.note = "found by its request ID"
    return found


def _is_error_line(message: str) -> bool:
    text = message or ""
    return any(word in text for word in _ERROR_WORDS)


def _find(apis: list[Api], wanted: str) -> Api | None:
    by_id = next((a for a in apis if a.id == wanted), None)
    if by_id is not None:
        return by_id
    named = [a for a in apis if a.name == wanted]
    return named[0] if named else None


def _add_metric(numbers: ApiNumbers, field_name: str, result: dict[str, Any], start: datetime) -> None:
    """
    One metric's values into ApiNumbers: counts summed (and kept per day for requests and 5xx), times as they are.
    """
    values = list(zip(result.get("Timestamps", []), result.get("Values", [])))
    if not values:
        return
    if field_name in ("latency_ms", "p95_ms", "integration_ms"):
        setattr(numbers, field_name, float(values[0][1]))
        return
    setattr(numbers, field_name, getattr(numbers, field_name) + int(sum(v for _, v in values)))
    daily = {"requests": numbers.daily_requests, "errors_5xx": numbers.daily_5xx}.get(field_name)
    if daily is None:
        return
    for moment, value in values:
        day = int((moment - start).total_seconds() // _DAY_SECONDS)
        if 0 <= day < numbers.days:
            daily[day] += int(value)
