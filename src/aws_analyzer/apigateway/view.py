"""
APIGatewayView: the notebook UI, which renders what APIGatewayAnalyzer returns as cards and tables.
"""

from __future__ import annotations

from typing import Any

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.deps import _require
from .._kit.errors import _Hint
from .._kit.fmt import _count, _fmt_dt, _plural, _tz_label, human_age, human_money
from .._kit.render import _call
from .._kit.text import _clip
from .._kit.view import _BaseView, _friendly_errors

from .helpers import status_tone
from .models import Api, ApiReport, Call, CallPage, ErrorReport, LambdaRun, Overview, Route, RouteNumbers, Stage
from .analysis import (
    access_log_command, api_findings, curl_command, curl_lines, error_findings, format_findings, latency_split,
    monthly_cost, open_route_findings, overview_findings, route_numbers_from_calls, stage_findings,
)
from .analysis.findings import SLOW_SHARE
from .analyzer import APIGatewayAnalyzer
from .blocks import _ms, _Path, _Step, _TimeSplit
from .style import _render_html


_SLOWEST = 10

_RUN_LINES = 40

_STATUS_WORDS = {"failed": (400, 600), "errors": (400, 600), "5xx": (500, 600), "4xx": (400, 500),
                 "2xx": (200, 300), "ok": (200, 400)}



def _share_cell(part: int, whole: int, tone: str) -> Any:
    if not whole:
        return "-"
    share = part / whole
    text = f"{share:.1%}"
    return _Tone(text, tone) if part and tone else text


def _card(card: tuple[str, ...]) -> tuple[str, ...]:
    """
    A card without an empty tone: (label, value) or (label, value, tone).
    """
    return card if len(card) == 2 or card[2] else card[:2]


def _status_range(status: Any) -> tuple[int, int] | None:
    """
    What calls(status=) keeps, as a range of codes: '5xx', '4xx', 'failed', '2xx', or one code (504, '504').
    """
    if status is None or status == "":
        return None
    text = str(status).strip().lower()
    if text in _STATUS_WORDS:
        return _STATUS_WORDS[text]
    if text.isdigit():
        return int(text), int(text) + 1
    raise ValueError(f"status= is '5xx', '4xx', 'failed', '2xx' or a code like 504, not {status!r}")


def _auth_cell(api: Api) -> Any:
    """
    Who may call an API, in a few words: its routes' authorizers, 'none ×2' (warned) when some answer anyone.
    """
    if not api.routes:
        return "-"
    open_routes = len(api.open_routes)
    if open_routes:
        return _Tone(f"none ×{open_routes}", "warn")
    asking = [r for r in api.routes if r.path == "$connect"] if api.kind == "WEBSOCKET" else api.routes
    labels = sorted({r.auth_kind for r in asking if r.method != "OPTIONS" and not (r.integration and r.integration.kind == "mock")})
    return ", ".join(labels) or "-"


def _route_tone(api: Api, route: Route, numbers: RouteNumbers | None) -> str:
    if numbers is None or numbers.p95_ms is None:
        return ""
    limit = route.integration.timeout_ms if route.integration and route.integration.timeout_ms else api.max_timeout_ms
    return "bad" if numbers.p95_ms >= SLOW_SHARE * min(limit, api.max_timeout_ms) else ""


def _stage_name(stage: Stage | None) -> str:
    return stage.name if stage is not None else "no stage"


def _throttle(stage: Stage) -> Any:
    if not stage.is_throttled:
        return _Tone("none", "warn")
    burst = f", burst {stage.throttle_burst:,}" if stage.throttle_burst else ""
    return f"{stage.throttle_rate:,.0f} rps{burst}"


def _access_cell(stage: Stage) -> Any:
    return stage.access_log_group if stage.has_access_log else _Tone("off", "warn")


def _call_status(call: Call) -> Any:
    tone = status_tone(call.status)
    text = str(call.status or "-")
    return _Tone(text, tone) if tone in ("bad", "warn") else text


class APIGatewayView(_BaseView):
    """
    Notebook UI for API Gateway. Every public method renders a report and returns None; the data is on self.core
    (an APIGatewayAnalyzer).
    """

    _render_html = staticmethod(_render_html)
    _GROUPS = {
        "🚪 APIs": ("apis", "api_info", "routes", "stages"),
        "📞 Calls": ("calls", "errors", "performance"),
        "🖱️ Explore by clicking": ("explore",),
        "❓ Help": ("help",),
    }
    _START = (
        ("apis()", "every API: requests, errors, latency, who can call it and its cost"),
        ("api_info('api-name')", "one API's stages, routes and what's failing"),
        ("errors('api-name')", "which calls fail and why, with the Lambda run behind each"),
    )
    _PROGRESS = ("Reading", "log lines")

    def __init__(
        self,
        core: APIGatewayAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: APIGatewayAnalyzer = core or APIGatewayAnalyzer()

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code == "NotFoundException":
            return f"{text} APIs are regional (this is {self.core.region}): apis() lists them."
        if code == "ResourceNotFoundException":
            return f"{text} The stage's access log group may have been deleted: stages('<api>') shows where it points."
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in message:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message


    @_friendly_errors
    def apis(self, *, days: int = 7) -> None:
        """
        Every API in the region, REST, HTTP and WebSocket: its requests, its 4xx and 5xx share, how long calls take,
        who can call it and its estimated monthly cost over the last 7 days, with what needs attention first.
        """
        with self._progress("Reading APIs", unit="APIs"):
            overview = self.core.overview(days=days)
        if not overview.apis:
            self._show([_Title(f"API Gateway in {overview.region}"),
                        _Note(f"No APIs in {overview.region}. They're regional: pass "
                              "APIGatewayAnalyzer(region='...') to look in another region.")])
            return
        self._show(self._overview_blocks(overview))

    def _overview_blocks(self, overview: Overview) -> list[Any]:
        prices, days, numbers = self.core.prices, overview.days, overview.numbers
        costs = {a.id: monthly_cost(a, numbers.get(a.id), prices) for a in overview.apis}
        findings = overview_findings(overview, prices)
        requests = sum(n.requests for n in numbers.values())
        server_errors = sum(n.errors_5xx for n in numbers.values())
        open_apis = [a for a in overview.apis if a.open_routes]
        known = [c for c in costs.values() if c is not None]
        rate = server_errors / requests if requests else 0.0
        warned_errors = any(level == "warn" and "5xx" in text for level, text in findings)
        kinds = {kind: sum(1 for a in overview.apis if a.kind == kind) for kind in ("REST", "HTTP", "WEBSOCKET")}
        sub = ", ".join(f"{n} {label}" for label, n in (("REST", kinds["REST"]), ("HTTP", kinds["HTTP"]),
                                                          ("WebSocket", kinds["WEBSOCKET"])) if n)
        cards: list[tuple[str, ...]] = [
            ("APIs", _count(len(overview.apis))),
            (f"Requests · {days}d", _count(requests) if numbers else "?"),
            (f"5xx · {days}d", f"{rate:.1%}" if numbers else "?", "warn" if warned_errors else ""),
            ("Open to anyone", _count(len(open_apis)), "warn" if open_apis else ""),
            ("Est. $ / month", human_money(sum(known)) if known else "-"),
        ]
        blocks: list[Any] = [
            _Title(f"API Gateway in {overview.region}",
                   f"{_plural(len(overview.apis), 'API')} · {sub} · the last {_plural(days, 'day')} (CloudWatch's "
                   f"daily numbers, in UTC days) · {self._price_basis()}"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="No API fails often, and every route asks who's calling."),
            *self._overview_notes(overview),
            self._apis_table(overview, costs),
            _Next(self._overview_next(overview)),
        ]
        return blocks

    def _overview_notes(self, overview: Overview) -> list[Any]:
        notes: list[Any] = []
        if "metrics" in overview.errors:
            notes.append(_Note(f"CloudWatch's numbers couldn't be read ({overview.errors['metrics']}), so requests, "
                               "errors and costs are unknown: that needs cloudwatch:GetMetricData.", "warn"))
        unread = [name for name in overview.errors if name != "metrics"]
        if unread:
            notes.append(_Note(f"{', '.join(unread)} couldn't be read whole ({overview.errors[unread[0]]}): "
                               "README lists the apigateway:GET permission it needs.", "warn"))
        if overview.metrics_read:
            notes.append(_Note(f"Read {overview.metrics_read:,} CloudWatch metrics (about "
                               f"{human_money(overview.metrics_read * self.core.prices['metric_read'])}). Costs are "
                               "estimates at the first price tier: REST $3.50, HTTP $1.00 per million requests, "
                               "WebSocket $1.00 per million messages, plus any cache."))
        return notes

    def _apis_table(self, overview: Overview, costs: dict[str, float | None]) -> _Table:
        def order(api: Api) -> tuple[int, int]:
            numbers = overview.numbers.get(api.id)
            return (-(numbers.errors_5xx if numbers else 0), -(numbers.requests if numbers else 0))

        rows = []
        for api in sorted(overview.apis, key=order):
            numbers = overview.numbers.get(api.id)
            rows.append([
                api.name, api.kind_label, ", ".join(s.name for s in api.stages) or "-",
                _count(numbers.requests) if numbers else "-",
                _share_cell(numbers.errors_4xx, numbers.requests, "") if numbers else "-",
                _share_cell(numbers.errors_5xx, numbers.requests, "bad" if numbers.rate_5xx >= 0.01 else "")
                if numbers else "-",
                _ms(numbers.p95_ms) if numbers else "-", _auth_cell(api), human_money(costs.get(api.id)),
                human_age(api.created),
            ])
        days = overview.days
        return _Table(["API", "Type", "Stages", f"Requests · {days}d", "4xx", "5xx", "p95", "Auth", "$ / month",
                       "Created"], rows, title="APIs, the ones failing most first")

    def _overview_next(self, overview: Overview) -> list[tuple[str, str]]:
        numbers = overview.numbers
        failing = sorted((a for a in overview.apis if numbers.get(a.id) and numbers[a.id].errors_5xx),
                         key=lambda a: -numbers[a.id].errors_5xx)
        busiest = max(overview.apis, key=lambda a: numbers[a.id].requests if a.id in numbers else 0)
        steps = []
        if failing:
            steps.append((_call("errors", failing[0].name), "which calls fail and why"))
        steps.append((_call("api_info", busiest.name), "its stages, routes and what's failing"))
        opened = next((a for a in overview.apis if a.open_routes), None)
        if opened is not None:
            steps.append((_call("routes", opened.name), "every route and who can call it"))
        return steps[:3]


    @_friendly_errors
    def api_info(self, name: str, *, stage: str | None = None, days: int = 7) -> None:
        """
        One API: its requests, errors, latency and cost over the last 7 days, what needs attention, its stages
        (URL, throttling, logging, tracing) and its routes with where each goes and who can call it. name: its name,
        ID, 'name/stage' or invoke URL; stage= picks a stage (else prod, $default or the one deployed last).
        """
        with self._progress(f"Reading {name}", unit="parts"):
            report = self.core.report(name, stage=stage, days=days)
        self._show(self._api_blocks(report))

    def _api_cards(self, report: ApiReport, findings: list[tuple[str, str]]) -> list[tuple[str, ...]]:
        numbers, api = report.numbers, report.api
        warned = " ".join(text for level, text in findings if level == "warn")
        cost = monthly_cost(api, numbers, self.core.prices)
        return [
            (f"Requests · {report.days}d", _count(numbers.requests) if numbers else "?"),
            (f"5xx · {report.days}d", f"{numbers.rate_5xx:.1%}" if numbers else "?",
             "bad" if numbers and "5xx" in warned else ""),
            ("p95", _ms(numbers.p95_ms) if numbers else "?", "warn" if "time limit" in warned else ""),
            ("Routes", _count(len(api.routes)), "warn" if api.open_routes else ""),
            ("Stages", _count(len(api.stages))),
            ("Est. $ / month", human_money(cost)),
        ]

    def _api_blocks(self, report: ApiReport) -> list[Any]:
        api, stage = report.api, report.stage
        findings = api_findings(report, self.core.prices)
        sub = " · ".join(filter(None, [f"{api.kind_label} API", api.id, f"{api.endpoint_type.lower()} endpoint"
                                       if api.endpoint_type else "", f"stage {_stage_name(stage)}",
                                       f"created {_fmt_dt(api.created)}" if api.created else ""]))
        blocks: list[Any] = [
            _Title(api.name, sub),
            _Cards([_card(c) for c in self._api_cards(report, findings)]),
            _Findings(findings, empty="Nothing to fix: it doesn't fail often, every route asks who's calling, and "
                                      "its stage logs each call."),
        ]
        if api.description:
            blocks.append(_Note(api.description))
        blocks += self._read_notes(api, report.errors, report.metrics_read)
        blocks.append(self._stages_table(api))
        blocks.append(self._routes_table(api, report.route_numbers, title="Routes, the busiest first"
                                         if report.route_numbers else "Routes"))
        blocks.append(_Next(self._api_next(report)))
        return blocks

    def _read_notes(self, api: Api, errors: dict[str, str], metrics_read: int) -> list[Any]:
        notes: list[Any] = []
        for part, code in {**api.errors, **errors}.items():
            notes.append(_Note(f"Its {part} couldn't be read ({code}).", "warn"))
        if metrics_read:
            notes.append(_Note(f"Read {metrics_read:,} CloudWatch metrics (about "
                               f"{human_money(metrics_read * self.core.prices['metric_read'])})."))
        return notes

    def _api_next(self, report: ApiReport) -> list[tuple[str, str]]:
        api = report.api
        steps = [(_call("errors", api.name), "which calls fail and why")]
        if api.open_routes:
            steps.append((_call("routes", api.name), "every route and who can call it"))
        steps.append((_call("performance", api.name), "which routes are slow, and where the time goes"))
        return steps[:3]

    def _stages_table(self, api: Api) -> _Table:
        rows = []
        for stage in api.stages:
            rows.append([stage.name, human_age(stage.updated or stage.created), api.url(stage.name), _throttle(stage),
                         _access_cell(stage), stage.logging.lower(), "X-Ray" if stage.tracing else "off",
                         f"{stage.cache_size} GB" if stage.cache_enabled else "off", stage.waf or "-"])
        return _Table(["Stage", "Deployed", "URL", "Throttle", "Access log", "Execution log", "Tracing", "Cache",
                       "WAF"], rows, title="Stages", max_rows=0)

    def _routes_table(self, api: Api, numbers: dict[str, RouteNumbers], *, title: str) -> _Table:
        routes = sorted(api.routes, key=lambda r: (-(numbers[r.key].calls if r.key in numbers else 0), r.path, r.method))
        rows = []
        for route in routes:
            found = numbers.get(route.key)
            target = route.integration.target if route.integration else "-"
            auth: Any = _Tone("none", "warn") if route in api.open_routes else route.auth
            tone = _route_tone(api, route, found)
            p95: Any = _Tone(_ms(found.p95_ms), tone) if found and tone else _ms(found.p95_ms if found else None)
            rows.append([route.key, target, auth, _count(found.calls) if found else "-",
                         _share_cell(found.errors_5xx, found.calls, "bad" if found.rate_5xx >= 0.05 else "")
                         if found else "-", p95])
        return _Table(["Route", "Goes to", "Auth", "Calls", "5xx", "p95"], rows, title=title)


    @_friendly_errors
    def routes(self, name: str, *, stage: str | None = None) -> None:
        """
        Every route of an API: who can call it (its authorizer, IAM, an API key, or anyone), where it goes (a Lambda
        function, a URL, an AWS service or a mock), how long API Gateway waits for it, and the curl command that
        calls it.
        """
        api = self.core.api(name)
        self._show(self._routes_blocks(api, stage))

    def _routes_blocks(self, api: Api, stage: str | None = None) -> list[Any]:
        open_routes = api.open_routes
        targets: dict[str, int] = {}
        for route in api.routes:
            kind = route.integration.kind if route.integration else "none"
            targets[kind] = targets.get(kind, 0) + 1
        findings = open_route_findings(api)
        chosen = api.stage(stage)
        rows = []
        for route in sorted(api.routes, key=lambda r: (r.path, r.method)):
            integration = route.integration
            auth: Any = _Tone("none", "warn") if route in open_routes else route.auth
            rows.append([route.key, auth, integration.target if integration else "-",
                         _ms(integration.timeout_ms) if integration and integration.timeout_ms else "-",
                         integration.payload_version or "-" if integration else "-",
                         curl_command(api, route, chosen.name if chosen else None)])
        blocks: list[Any] = [
            _Title(f"Routes of {api.name}", f"{api.kind_label} API · stage {_stage_name(chosen)} · "
                                           f"{_plural(len(api.routes), 'route')}"),
            _Cards([_card(("Routes", _count(len(api.routes)))),
                    _card(("Open to anyone", _count(len(open_routes)), "warn" if open_routes else "")),
                    _card(("Integrations", " · ".join(f"{k} {n}" for k, n in sorted(targets.items())) or "-"))]),
            _Findings(findings, empty="Every route asks who's calling (an authorizer, IAM or an API key)."),
            _Table(["Route", "Auth", "Goes to", "Timeout", "Payload", "Try it"], rows, title="Routes", code_cols=(5,)),
        ]
        if api.authorizers:
            blocks.append(_Table(["Authorizer", "Type", "Reads", "Runs or trusts"],
                                 [[a.name, a.label.removesuffix(f" {a.name}"), a.identity_source or "-",
                                   a.function or a.issuer or "-"] for a in api.authorizers.values()], title="Authorizers", max_rows=0))
        blocks.append(_Next([(_call("errors", api.name), "which calls fail and why"),
                             (_call("stages", api.name), "each stage's throttling, logging and URL")]))
        return blocks

    def _route_blocks(self, api: Api, route: Route, numbers: RouteNumbers | None, stage: str | None) -> list[Any]:
        """
        One route, as the explorer's Routes tab shows it: where a request goes, its numbers, what's wrong, its
        settings and the command that calls it.
        """
        integration = route.integration
        limit = min(integration.timeout_ms or api.max_timeout_ms, api.max_timeout_ms) if integration else api.max_timeout_ms
        tone = _route_tone(api, route, numbers)
        steps = [_Step("caller", route.key)]
        auth_tone = "warn" if route in api.open_routes else ""
        steps.append(_Step("auth", route.auth, route.authorizer.function or route.authorizer.issuer
                           if route.authorizer else "", auth_tone))
        if route.validator:
            steps.append(_Step("validates", "the request", route.validator))
        if integration is not None:
            detail = f"waits up to {_ms(limit)}"
            if numbers and numbers.p95_ms is not None:
                detail = f"p95 {_ms(numbers.p95_ms)} of {_ms(limit)}"
            steps.append(_Step(f"integration · {integration.type.lower()}", integration.target, detail, tone))
        findings = [f for f in open_route_findings(api) if route.key in f[1]]
        if tone:
            findings.insert(0, ("warn", f"{route.key} runs out of time: 5% of its calls take {_ms(numbers.p95_ms)} "
                                        f"or more, and API Gateway gives up at {_ms(limit)} with a 504. Make it answer "
                                        "first and finish the work later (a queue), or speed it up."))
        settings = [["Integration", f"{integration.type} · {integration.uri or '-'}" if integration else "none"],
                    ["Timeout", _ms(integration.timeout_ms) if integration and integration.timeout_ms else "default"],
                    ["Who may call", route.auth], ["API key", "required" if route.api_key_required else "not required"]]
        if numbers is not None:
            settings += [["Calls", _count(numbers.calls)], ["5xx", f"{numbers.errors_5xx:,} ({numbers.rate_5xx:.1%})"]]
        chosen = api.stage(stage)
        return [
            _Title(route.key, f"{api.name} · stage {_stage_name(chosen)}"),
            _Path(steps),
            _Findings(findings),
            _Table(["Setting", "Value"], settings, sortable=False, max_rows=0),
            _Text(curl_lines(api, route, chosen.name if chosen else None), title="Call it yourself", code=True),
        ]


    @_friendly_errors
    def stages(self, name: str) -> None:
        """
        Every stage of an API: when it was deployed, its URL, its throttling, its access and execution logs, tracing,
        cache and WAF, with what each risks and the command that fixes it.
        """
        api = self.core.api(name)
        self._show(self._stages_blocks(api))

    def _stages_blocks(self, api: Api) -> list[Any]:
        findings: list[tuple[str, str]] = []
        for stage in api.stages:
            findings += stage_findings(api, stage) + format_findings(api, stage)
        blocks: list[Any] = [
            _Title(f"Stages of {api.name}", f"{api.kind_label} API · {_plural(len(api.stages), 'stage')}"),
            _Findings(findings, empty="Every stage is throttled and keeps an access log."),
            self._stages_table(api),
        ]
        logged = [s for s in api.stages if s.has_access_log]
        for stage in logged:
            blocks.append(_Text(stage.access_log_format or "(none)", title=f"{stage.name}'s access log format",
                                collapsed=True, code=True))
        if not api.stages:
            blocks.append(_Note(f"{api.name} isn't deployed: it has no stage, so it has no URL yet."))
        if api.stages and not logged:
            blocks.append(_Next([(access_log_command(api, api.stage(None)), "turn the main stage's access log on")]))
        return blocks


    @_friendly_errors
    def calls(self, name: str, *, stage: str | None = None, since: Any = "24h", status: Any = None,  # noqa: PLR0913
              search: str | None = None, n: Any = 50) -> None:
        """
        The calls a stage's access log recorded, newest first: when, the route, the status and error, how long each
        took and how much of it the backend took. status= keeps some ('5xx', '4xx', 'failed', 504); search= finds a
        request ID or any text in the line. Needs the stage's access log.
        """
        with self._progress(f"Reading {name}'s access log", unit="lines") as tick:
            page = self.core.calls(name, stage=stage, since=since, pattern=f'"{search}"' if search else None,
                                   progress=tick)
        self._show(self._calls_blocks(page, status=status, n=n))

    def _calls_blocks(self, page: CallPage, *, status: Any = None, n: Any = 50) -> list[Any]:
        api, stage = page.api, page.stage
        title = _Title(f"Calls to {api.name}", f"stage {_stage_name(stage)} · from {_fmt_dt(page.since, zone=True)}")
        if page.note:
            return [title, *self._no_log_blocks(api, stage)]
        wanted = _status_range(status)
        calls = [c for c in page.calls if wanted is None or (c.status is not None and wanted[0] <= c.status < wanted[1])]
        failed = page.failed
        times = sorted(c.latency_ms for c in page.calls if c.latency_ms is not None)
        cards = [("Calls", _count(len(page.calls))), ("Failed", _count(len(failed)), "warn" if failed else ""),
                 ("Median", _ms(times[len(times) // 2]) if times else "-"), ("Slowest", _ms(times[-1]) if times else "-")]
        limit = int(n) if str(n).isdigit() else 50
        rows = [[_fmt_dt(c.time, seconds=True), c.route_key or "-", _call_status(c), _ms(c.latency_ms),
                 _ms(c.integration_ms), _clip(c.why, 80) or "-", c.request_id or "-"] for c in calls[:limit]]
        blocks: list[Any] = [title, _Cards([_card(c) for c in cards]), *self._page_notes(page)]
        blocks += [_Note(text) for _, text in format_findings(api, stage)]
        shown = f"{min(limit, len(calls)):,} of {len(calls):,}" if len(calls) > limit else f"{len(calls):,}"
        blocks.append(_Table([f"Time ({_tz_label()})", "Route", "Status", "Took", "Backend", "Error", "Request ID"],
                             rows, title=f"Calls, newest first ({shown})", max_rows=0))
        if failed:
            blocks.append(_Next([(_call("errors", api.name), "the failures grouped by cause")]))
        return blocks

    def _page_notes(self, page: CallPage) -> list[Any]:
        notes = []
        if page.truncated:
            notes.append(_Note(f"Read the newest {len(page.calls) + page.unread:,} lines of {page.log_group}: there "
                               "are more. Pass since= to look at a shorter time."))
        if page.unread:
            notes.append(_Note(f"{_plural(page.unread, 'line')} didn't match the stage's access log format and were "
                               "skipped.", "warn"))
        return notes

    def _no_log_blocks(self, api: Api, stage: Stage | None) -> list[Any]:
        if stage is None:
            return [_Note(f"{api.name} has no stage: it isn't deployed, so no one can call it yet.")]
        return [_Note(f"Stage {stage.name} keeps no access log, so its calls aren't recorded anywhere: CloudWatch "
                      "only counts them. Turn the access log on to see each call:", "warn"),
                _Text(access_log_command(api, stage), code=True)]


    @_friendly_errors
    def errors(self, name: str, *, stage: str | None = None, since: Any = "24h") -> None:
        """
        What's failing in an API: CloudWatch's 4xx and 5xx counts, and from the stage's access log the failed calls
        grouped by status, error and route, the most common first, each with what it means and the newest call's
        request ID (and the Lambda run to read).
        """
        with self._progress(f"Reading {name}'s failures", unit="lines") as tick:
            report = self.core.errors(name, stage=stage, since=since, progress=tick)
        self._show(self._error_blocks(report))

    def _error_blocks(self, report: ErrorReport) -> list[Any]:
        api, stage, page, numbers = report.api, report.stage, report.page, report.numbers
        failed = page.failed if page else []
        server = sum(1 for c in failed if (c.status or 0) >= 500)
        worst = report.groups[0].route if report.groups else "-"
        cards = [
            ("5xx", _count(numbers.errors_5xx) if numbers else "?", "bad" if numbers and numbers.errors_5xx else ""),
            ("4xx", _count(numbers.errors_4xx) if numbers else "?"),
            ("Requests", _count(numbers.requests) if numbers else "?"),
            ("Worst route", worst),
        ]
        sub = f"stage {_stage_name(stage)} · since {_fmt_dt(report.since, zone=True)}"
        if page is not None:
            sub += f" · {_plural(len(failed), 'failed call')} in the access log ({server:,} 5xx)"
        blocks: list[Any] = [
            _Title(f"What's failing in {api.name}", sub),
            _Cards([_card(c) for c in cards]),
            _Findings(error_findings(report), empty="No call failed in this time."),
        ]
        if "metrics" in report.errors:
            blocks.append(_Note(f"CloudWatch's counts couldn't be read ({report.errors['metrics']}).", "warn"))
        if page is not None:
            blocks += self._page_notes(page)
        if report.groups:
            blocks.append(self._groups_table(api, report))
        if failed:
            blocks.append(self._failed_table(failed[:_SLOWEST]))
        blocks.append(_Next(self._error_next(report)))
        return blocks

    def _groups_table(self, api: Api, report: ErrorReport) -> _Table:
        rows = []
        for group in report.groups:
            route = api.route(group.route)
            function = route.integration.function if route and route.integration else ""
            newest = group.newest
            read = (f"LambdaView().{_call('logs', function, request_id=newest.lambda_request)}"
                    if function and newest.lambda_request else newest.request_id or "-")
            rows.append([_Tone(str(group.status or "-"), status_tone(group.status)), _clip(group.error, 80) or "-",
                         group.route or "-", f"{group.count:,}", human_age(newest.time), read])
        return _Table(["Status", "Error", "Route", "Calls", "Newest", "To read next"], rows,
                      title="Failures by cause, the most common first", code_cols=(5,))

    def _failed_table(self, calls: list[Call]) -> _Table:
        rows = [[_fmt_dt(c.time, seconds=True), c.route_key or "-", _call_status(c), _ms(c.latency_ms),
                 c.request_id or "-", c.lambda_request or "-"] for c in calls]
        return _Table([f"Time ({_tz_label()})", "Route", "Status", "Took", "Request ID", "Lambda request"], rows,
                      title="The latest failed calls", max_rows=0)

    def _error_next(self, report: ErrorReport) -> list[tuple[str, str]]:
        api = report.api
        steps = []
        if report.stage is not None and report.stage.has_access_log:
            steps.append((_call("calls", api.name, status="5xx"), "every server error, newest first"))
        steps.append((_call("performance", api.name), "which routes are slow"))
        steps.append((_call("routes", api.name), "where each route goes"))
        return steps[:3]


    @_friendly_errors
    def performance(self, name: str, *, stage: str | None = None, since: Any = "24h") -> None:
        """
        How long an API's calls take: the typical and slowest call, each route's average, 95th percentile and
        longest (from the access log, or per-route metrics), how close they come to the time limit, and the slowest
        calls with where their time went.
        """
        with self._progress(f"Reading {name}'s access log", unit="lines") as tick:
            page = self.core.calls(name, stage=stage, since=since, progress=tick)
            report = self.core.report(name, stage=page.stage.name if page.stage else None, days=7)
        self._show(self._performance_blocks(page, report))

    def _performance_blocks(self, page: CallPage, report: ApiReport) -> list[Any]:
        api, stage, numbers = page.api, page.stage, report.numbers
        by_route = route_numbers_from_calls(page.calls) if page.calls else report.route_numbers
        source = "its access log" if page.calls else ("detailed metrics, 7 days" if by_route else "")
        times = sorted(c.latency_ms for c in page.calls if c.latency_ms is not None)
        share = self._backend_share(page.calls)
        cards = [
            ("p95 · 7d", _ms(numbers.p95_ms) if numbers else "?"),
            ("Average · 7d", _ms(numbers.latency_ms) if numbers else "?"),
            ("Slowest call", _ms(times[-1]) if times else "-",
             "bad" if times and times[-1] >= api.max_timeout_ms * SLOW_SHARE else ""),
            ("In the backend", f"{share:.0%}" if share is not None else "-"),
            ("Time limit", _ms(api.max_timeout_ms)),
        ]
        blocks: list[Any] = [
            _Title(f"How long {api.name}'s calls take", f"stage {_stage_name(stage)}"
                   + (f" · since {_fmt_dt(page.since, zone=True)}" if page.calls else "")),
            _Cards([_card(c) for c in cards]),
        ]
        slow = [r for r in api.routes if _route_tone(api, r, by_route.get(r.key))]
        blocks.append(_Findings([("warn", f"{r.key} comes close to the {_ms(api.max_timeout_ms)} limit: its p95 is "
                                          f"{_ms(by_route[r.key].p95_ms)}, so some calls get a 504.") for r in slow],
                                empty="No route comes close to the time limit."))
        if page.note:
            blocks += self._no_log_blocks(api, stage)
        if by_route:
            blocks.append(self._latency_table(api, by_route, source))
        if page.calls:
            blocks += self._slowest_blocks(api, page.calls)
        return blocks

    def _backend_share(self, calls: list[Call]) -> float | None:
        timed = [c for c in calls if c.latency_ms and c.integration_ms is not None]
        total = sum(c.latency_ms for c in timed)
        return sum(c.integration_ms for c in timed) / total if total else None

    def _latency_table(self, api: Api, by_route: dict[str, RouteNumbers], source: str) -> _Table:
        rows = []
        for key, found in sorted(by_route.items(), key=lambda item: -(item[1].p95_ms or 0)):
            route = api.route(key)
            tone = _route_tone(api, route, found) if route else ""
            rows.append([key, _count(found.calls), _ms(found.avg_ms),
                         _Tone(_ms(found.p95_ms), tone) if tone else _ms(found.p95_ms), _ms(found.max_ms),
                         route.integration.target if route and route.integration else "-"])
        return _Table(["Route", "Calls", "Average", "p95", "Longest", "Goes to"], rows,
                      title=f"Routes, the slowest first (from {source})")

    def _slowest_blocks(self, api: Api, calls: list[Call]) -> list[Any]:
        slowest = sorted((c for c in calls if c.latency_ms is not None), key=lambda c: -c.latency_ms)[:_SLOWEST]
        if not slowest:
            return []
        rows = [[_fmt_dt(c.time, seconds=True), c.route_key, _call_status(c), _ms(c.latency_ms),
                 _ms(c.integration_ms), _ms(c.authorizer_ms), c.request_id or "-"] for c in slowest]
        blocks: list[Any] = [_Table([f"Time ({_tz_label()})", "Route", "Status", "Took", "Backend", "Authorizer",
                                     "Request ID"], rows, title="The slowest calls", max_rows=0)]
        top = slowest[0]
        parts = latency_split(top)
        if len(parts) > 1:
            blocks.append(self._split_block(api, top, title=f"Where the slowest call's {_ms(top.latency_ms)} went"))
        return blocks

    def _split_block(self, api: Api, call: Call, *, title: str) -> _TimeSplit:
        parts = latency_split(call)
        tone = "bad" if call.status in (502, 504) else ""
        notes = []
        if call.latency_ms is not None and call.latency_ms >= api.max_timeout_ms * 0.99:
            notes.append(f"It hit API Gateway's {_ms(api.max_timeout_ms)} limit: the caller got a {call.status}, "
                         "while the backend may still have been working.")
        return _TimeSplit(parts, call.latency_ms or sum(ms for _, ms in parts), tone=tone, title=title, notes=notes)

    def _call_blocks(self, api: Api, call: Call, run: LambdaRun | None) -> list[Any]:
        """
        One call, as the explorer's Calls tab shows it: where its time went, what it was, and the Lambda run behind
        it with its lines.
        """
        details = [["Request ID", call.request_id or "-"], ["Route", call.route_key or "-"],
                   ["Status", str(call.status or "-")], ["Error", call.why or "-"],
                   ["Caller", " · ".join(filter(None, [call.ip, call.user])) or "-"],
                   ["Lambda request", call.lambda_request or "-"]]
        blocks: list[Any] = []
        if len(latency_split(call)) > 1:
            blocks.append(self._split_block(api, call, title=f"Where the {_ms(call.latency_ms)} went"))
        blocks.append(_Table(["", ""], details, sortable=False, max_rows=0))
        if run is not None:
            blocks += self._run_blocks(run)
        return blocks

    def _run_blocks(self, run: LambdaRun) -> list[Any]:
        if not run.found:
            return [_Note(f"The Lambda run behind it couldn't be shown: {run.note}.")] if run.function else []
        start = run.lines[0].time
        rows = [[f"+{(line.time - start).total_seconds():.3f}s",
                 _Tone(_clip(line.text, 200), "bad") if line.is_error else _clip(line.text, 200)]
                for line in run.lines[:_RUN_LINES]]
        ran = f" · ran {_ms(run.duration_ms)}" if run.duration_ms is not None else ""
        ended = f" · {run.status}" if run.status else ""
        return [_Table(["", "Line"], rows, title=f"λ {run.function} · run {run.request_id[:8]}{ran}{ended}",
                       sortable=False, max_rows=0, code_cols=(0,)),
                _Next([(f"LambdaView().{_call('logs', run.function, request_id=run.request_id)}",
                        "the whole run, in the Lambda reports")])]


    @_friendly_errors
    def explore(self, name: str | None = None, *, tab: str | None = None, stage: str | None = None,
                since: str = "24h", height: int | str | None = None) -> None:
        """
        The explorer window: every API and how it's doing, and for the one you click its routes as a tree (where
        each request goes, the step that fails marked), its calls from the access log (where each one's time went,
        and the Lambda run behind it), how long calls take, and its stages. Needs Jupyter and ipywidgets;
        view.explorer is the window.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, apis() lists every API, "
                "api_info('name') shows one, and errors('name') says which calls fail and why."
            )
        _require("ipywidgets", "The explorer window")
        from .explorer.window import APIGatewayExplorer

        self.explorer = APIGatewayExplorer(name, tab=tab, stage=stage, since=since, view=self, height=height,
                                           mode="widgets")

