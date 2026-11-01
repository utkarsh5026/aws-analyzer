"""
APIGatewayExplorer, the API Gateway explorer window, and explore() to open it.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _plural, human_money
from ..._kit.text import _clip, _esc
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..helpers import parse_api_ref
from ..models import Api, ApiReport, CallPage, Overview, Stage
from ..analysis import monthly_cost
from ..analyzer import APIGatewayAnalyzer
from ..blocks import _ms
from ..style import _CSS, _render_html
from ..view import APIGatewayView
from .apis import _ApisTab
from .calls import _CallsTab
from .frame import _API_TABS, _EXPLORER_CSS, _EXPLORER_TABS, _LOGO, _SINCE_CHOICES, _api_tone, _window_text
from .routes import _RoutesTab
from .widgets import _ApiField, _Line


_OVERVIEW_DAYS = 7


class APIGatewayExplorer(_ApisTab, _RoutesTab, _CallsTab, _Explorer):
    """
    The API Gateway explorer: a window to look through your APIs by clicking. The API field at the top switches to
    another one, the stage menu beside the title to another stage; the cards beside the field say how it's doing.

        APIs         every API in the region: its requests, 5xx share and p95 (7 days), its cost and who can call it,
                     the failing ones first; click one to open it
        Overview     how the API is doing, what needs attention, its stages and its routes
        Routes       its routes as a tree of paths; click one to see where a request goes (the caller, the
                     authorizer, the validation, the integration), the step that fails marked, and curl to call it
        Calls        its calls from the stage's access log: a timeline, chips by status, a search box, and a click
                     on a call shows where its time went and the Lambda run behind it, its lines read by request ID
        Performance  how long its calls take, route by route, and the slowest calls with where their time went
        Stages       each stage's URL, throttling, logs, tracing and cache, with the command that fixes each

    api: the API to open (a name, ID, 'name/stage' or invoke URL); without it, the one failing most, else the first.
    tab: the tab to start on. stage: the stage to show (else prod, $default or the one deployed last). since: how far
    back the Calls tab reads ('1h', '24h', '7d'). view / core: an APIGatewayView or APIGatewayAnalyzer to use (else
    one is made from region / profile). height: the height of the tabs' pages (pixels, or CSS like '80vh'). mode:
    'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.

    It only reads: nothing here calls an API or changes one. x.ui is an APIGatewayView for reports in other cells;
    x.overview, x.api, x.stage, x.report, x.page and x.run hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "agx", "apigw-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        api: str | None = None,
        *,
        tab: str | None = None,
        stage: str | None = None,
        since: str = "24h",
        view: APIGatewayView | None = None,
        core: APIGatewayAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        _check_choices(mode, tab, since)
        super().__init__()
        if view is None:
            view = APIGatewayView(core or APIGatewayAnalyzer(region=region, profile=profile),
                                  mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.height = height
        self.overview: Overview | None = None
        self.numbers: dict[str, Any] = {}
        self.api: Api | None = None
        self.stage: Stage | None = None
        self.report: ApiReport | None = None
        self.page: CallPage | None = None
        self._since = since
        self._want_stage = stage
        self._want_tab = tab
        self._want_call: str | None = None
        self._api_rows: list[_Line] = []
        self._tab = "apis"
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(api, note, mode)
            return
        self._build()
        self._begin(api)
        self._display()

    def __repr__(self) -> str:
        name = self.api.name if self.api else "no API"
        return f"APIGatewayExplorer({name}) · help(APIGatewayExplorer) says what it shows"

    @_window_errors
    def open(self, api: str, *, tab: str | None = None) -> None:
        """
        Shows another API (a name, ID, 'name/stage' or invoke URL), as picking it in the API field does, on `tab`
        when one is named.
        """
        found = self.core.resolve(api)
        self._want_tab = tab
        self._open_api(found.id)

    @_window_errors
    def route(self, key: str) -> None:
        """
        Opens a route in the Routes tab ('POST /orders'), as clicking it in the tree does.
        """
        if self.api is None or self.api.route(key) is None:
            raise ValueError(f"No route {key!r} in {self.api.name if self.api else 'the API shown'}")
        self.route_key = self.api.route(key).key
        self._show_tab("routes")
        self._draw_routes()

    @_window_errors
    def call(self, request_id: str) -> None:
        """
        Opens a call in the Calls tab by its request ID, as clicking it in the list does: where its time went and the
        Lambda run behind it.
        """
        self._show_tab("calls")
        self._open_call(request_id)

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the APIs, and the one shown (the ↻ button).
        """
        keep = self.api.id if self.api else None
        self.core._listed = None
        self.core._read.clear()
        self._begin(keep)

    def _reports(self, api: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: api_info('name') shows one API, and errors('name') which calls fail.")])
        if api is not None:
            self.ui.api_info(api, stage=self._want_stage)
        else:
            self.ui.apis()

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it).
        """
        return _render_html(_for_window(blocks, _window_text), self.ui.max_rows).replace(_CSS, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("agx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.stage_pick = w.Dropdown(options=[("Stage: …", "")], value="", tooltip="The stage to look at",
                                     layout=layout(width="170px", flex="0 0 auto"))
        self.stage_pick.observe(self._safely(self._stage_changed), names="value")
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the APIs and the calls again",
                                       layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("agx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.stage_pick, self.refresh_button], layout=layout(width="100%"))
        top.add_class("agx-top")
        self.field = _ApiField(self, on_pick=self._open_api, on_text=self._typed_api)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.field, self.stats], layout=layout(width="100%"))
        meta.add_class("agx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("agx-head")
        tabs = self._tab_bar(_EXPLORER_TABS)
        self.overview_view = w.HTML(layout=layout(width="100%"))
        self.performance_view = w.HTML(layout=layout(width="100%"))
        self.stages_view = w.HTML(layout=layout(width="100%"))
        self._frame(_CSS + _EXPLORER_CSS, head, tabs, {
            "apis": self._apis_page(),
            "overview": w.VBox([self.overview_view]),
            "routes": self._routes_page(),
            "calls": self._calls_page(),
            "performance": w.VBox([self.performance_view]),
            "stages": w.VBox([self.stages_view]),
        })
        self._show_tab(self._tab)
        self._draw_title()

    def _show_tab(self, key: str) -> None:
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "agx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if key == "performance":
            self._draw_performance()
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        api, page = self.api, self.page
        if key == "apis" and self.overview is not None:
            return f"{_plural(len(self.overview.apis), 'API')} in {self.core.region}: click one to open it."
        if key in _API_TABS and api is None:
            return "Pick an API in the field above, or click one in the APIs tab."
        if key == "routes" and api is not None:
            return f"{_plural(len(api.routes), 'route')}: click one to see where a request goes."
        if key == "calls" and page is not None and not page.note:
            return f"{_plural(len(page.calls), 'call')} in the last {self._since_label()}: click one to see it."
        return ""

    def _since_label(self) -> str:
        return next(label.lower().removeprefix("last ") for label, value in _SINCE_CHOICES if value == self._since)

    def _begin(self, api: str | None) -> None:
        """
        Reads the region's APIs with their numbers, then opens the one asked for (else the one failing most, else
        the first).
        """
        self._status(f"Reading the APIs in {self.core.region}…", busy=True, owner="list")
        self._set(self.api_view, _skeleton("Reading the APIs and their CloudWatch numbers…"))
        self._later("list", lambda: self.core.overview(days=_OVERVIEW_DAYS), lambda ov: self._listed(ov, api),
                    self._list_failed)

    def _list_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "apigateway:GET")
        self.field.problem = f"Couldn't list the APIs ({reason}). Type one's ID and press Enter."
        self.field.set_choices([])
        self._status(f"Couldn't list the APIs ({reason}).", "warn")
        self._set(self.api_view, self._html([_Note(f"Couldn't list the APIs: {reason}.", "warn")]))

    def _listed(self, overview: Overview, wanted: str | None) -> None:
        self.overview = overview
        self.numbers = dict(overview.numbers)
        self.field.set_choices(overview.apis)
        self._draw_apis()
        self._draw_stats()
        if not overview.apis:
            self._status(f"No APIs in {self.core.region}.", "warn")
            return
        target = self._target(wanted)
        failing = sum(1 for n in self.numbers.values() if _api_tone(n) == "bad")
        self._status(f"{_plural(len(overview.apis), 'API')} in {self.core.region}"
                     + (f" · {failing:,} failing" if failing else ""), owner="list")
        if target is not None:
            self._open_api(target)

    def _target(self, wanted: str | None) -> str | None:
        """
        The API to open first: the one asked for (its stage too, when 'name/stage' names one), else the one with the
        highest share of server errors, else the busiest.
        """
        apis = self.overview.apis if self.overview else []
        if wanted:
            try:
                return self._wanted_api(wanted)
            except (ValueError, ClientError, BotoCoreError) as exc:
                self._status(self._error_text(exc), "warn")
                return None
        if not apis:
            return None
        ranked = sorted(apis, key=lambda a: (-(self.numbers[a.id].rate_5xx if a.id in self.numbers else 0),
                                             -(self.numbers[a.id].requests if a.id in self.numbers else 0)))
        return ranked[0].id

    def _wanted_api(self, wanted: str) -> str:
        _, stage = parse_api_ref(wanted)
        if stage and not self._want_stage:
            self._want_stage = stage
        return self.core.resolve(wanted).id

    def _typed_api(self, text: str) -> None:
        self._open_api(self._wanted_api(text))

    def _draw_title(self) -> None:
        api = self.api
        sub = "Every API, its routes, its calls one by one, and the Lambda runs behind them · read-only"
        if api is not None:
            sub = " · ".join(filter(None, [api.name, f"{api.kind_label} API", api.id,
                                           _plural(len(api.routes), "route"),
                                           _clip(api.description, 80) if api.description else ""]))
        self._set(self.title, f'<div class="agx-brand"><span class="agx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="agx-name">API Gateway explorer<span>{_esc(self.core.region)}</span>'
                              f'</div><div class="agx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the API field: the one shown at a glance, or the region's before one is.
        """
        report = self.report
        if report is None or self.api is None:
            cards = self._region_cards()
        else:
            numbers = report.numbers
            failing = numbers is not None and numbers.errors_5xx > 0 and numbers.rate_5xx >= 0.01
            slow = numbers is not None and numbers.p95_ms is not None and numbers.p95_ms >= 0.8 * self.api.max_timeout_ms
            cards = [
                (f"Requests · {_OVERVIEW_DAYS}d", f"{numbers.requests:,}" if numbers else "-", ""),
                ("5xx", f"{numbers.rate_5xx:.1%}" if numbers else "-", "bad" if failing else ""),
                ("p95", _ms(numbers.p95_ms) if numbers else "-", "warn" if slow else ""),
                ("Est. $ / month", human_money(monthly_cost(self.api, numbers, self.core.prices)), ""),
                ("Open routes", f"{len(self.api.open_routes):,}", "warn" if self.api.open_routes else ""),
            ]
        html_cards = "".join(f'<div class="agx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="agx-stats">{html_cards}</div>')

    def _region_cards(self) -> list[tuple[str, str, str]]:
        if self.overview is None:
            return [("APIs", "…", "sk-on"), ("Requests", "…", "sk-on"), ("5xx", "…", "sk-on")]
        numbers = list(self.numbers.values())
        requests = sum(n.requests for n in numbers)
        errors = sum(n.errors_5xx for n in numbers)
        rate = errors / requests if requests else 0.0
        return [("APIs", f"{len(self.overview.apis):,}", ""), (f"Requests · {_OVERVIEW_DAYS}d", f"{requests:,}", ""),
                ("5xx", f"{rate:.1%}", "bad" if rate >= 0.01 else "")]

    def _open_api(self, api_id: str) -> None:
        """
        Shows an API: its stages, routes and numbers first, then its calls, in the background; what's still being
        read for the one shown before is dropped when it comes back.
        """
        self._drop("api", "calls", "run")
        if self.api is not None and self.api.id != api_id:
            self._want_stage = None
        self.api = self.stage = self.report = self.page = None
        self.route_key, self.call_selected, self.call_query, self.call_chip = "", "", "", ""
        self._quietly(self.call_find, value="")
        self._quietly(self.route_find, value="")
        self.field.value = api_id
        self.field.draw()
        self._draw_api_rows()
        name = self.field._choice(api_id).name if self.field._choice(api_id) else api_id
        for widget, text in ((self.overview_view, f"Reading {name}…"), (self.route_view, "Reading its routes…"),
                             (self.stages_view, "Reading its stages…"), (self.call_view, "Waiting for its stages…"),
                             (self.performance_view, "Waiting for its calls…")):
            self._set(widget, _skeleton(text))
        self.route_rows.children, self.call_rows.children, self.call_chips.children = [], [], []
        self._set(self.timeline, "")
        self._status(f"Reading {name}…", busy=True, owner="api")
        stage = self._want_stage
        self._later("api", lambda: self.core.report(api_id, stage=stage, days=_OVERVIEW_DAYS), self._got_report,
                    self._api_failed)

    def _api_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview_view, self.route_view, self.stages_view, self.call_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _got_report(self, report: ApiReport) -> None:
        self.report, self.api, self.stage = report, report.api, report.stage
        self._fill_stages()
        self._draw_title()
        self._draw_stats()
        self._draw("overview", self.overview_view, self.ui._api_blocks(report))
        self._draw("stages", self.stages_view, self.ui._stages_blocks(report.api))
        self._draw_routes()
        want_tab, self._want_tab = self._want_tab, None
        if want_tab:
            self._show_tab(want_tab)
        self._finish("api", f"{report.api.name}: {_plural(len(report.api.routes), 'route')} · stage "
                            f"{self.stage.name if self.stage else 'none'}", tabs=("overview", "routes", "stages"))
        self._load_calls()

    def _fill_stages(self) -> None:
        api = self.api
        options = [(f"Stage: {s.name}", s.name) for s in api.stages] if api and api.stages else [("No stage", "")]
        value = self.stage.name if self.stage else ""
        self._quietly(self.stage_pick, options=options, value=value)

    def _stage_changed(self, change: dict[str, Any]) -> None:
        if not change["new"] or self.api is None or (self.stage and change["new"] == self.stage.name):
            return
        self._want_stage = change["new"]
        self._open_api(self.api.id)

    def _draw_performance(self) -> None:
        """
        The Performance tab: drawn from the calls read and the API's numbers, once both are in.
        """
        if self.report is None or self.page is None:
            return
        self._draw("performance", self.performance_view, self.ui._performance_blocks(self.page, self.report))


def _check_choices(mode: str, tab: str | None, since: str) -> None:
    """
    Refuses a mode, tab or time range the window doesn't have, saying which it does.
    """
    if mode not in ("auto", "widgets", "text"):
        raise ValueError("mode must be 'auto', 'widgets' or 'text'")
    tabs = [key for key, _, _, _ in _EXPLORER_TABS]
    if tab is not None and tab not in tabs:
        raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")
    if since not in [value for _, value in _SINCE_CHOICES]:
        raise ValueError(f"since is one of {', '.join(v for _, v in _SINCE_CHOICES)}, not {since!r}")


def explore(  # noqa: PLR0913
    api: str | None = None,
    *,
    tab: str | None = None,
    stage: str | None = None,
    since: str = "24h",
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> APIGatewayExplorer:
    """
    Opens the API Gateway explorer window and returns it: every API, its routes as a tree (where each request goes),
    its calls from the access log (where each one's time went, and the Lambda run behind it), how long calls take and
    its stages.

        explore()                                 # the API failing most, or the first
        explore("orders-api")                     # by name, ID or invoke URL
        explore("orders-api/prod", tab="calls")   # a stage, on the Calls tab
        explore("partner-api", tab="routes")

    It only reads: nothing here calls an API or changes one.
    """
    return APIGatewayExplorer(api, tab=tab, stage=stage, since=since, region=region, profile=profile, height=height)
