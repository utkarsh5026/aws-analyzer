"""
The explorer's Calls tab: the calls the stage's access log recorded in the time picked, as a timeline of how many
and how many failed, chips by status, a search box (typing filters the calls read; Enter asks CloudWatch Logs for
the text over the whole time), and a line per call. A click shows on the right where its time went and, for a call
that reached a Lambda function, that run's lines, read by the request ID the access log recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _plural
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if, _skeleton

from ..models import Call, CallPage, LambdaRun
from ..analysis.findings import SLOW_SHARE
from .frame import _CALL_CHIPS, _CALL_PAGE, _SINCE_CHOICES, _call_face, _failures_line, _timeline_html
from .widgets import _Line

if TYPE_CHECKING:
    from .window import APIGatewayExplorer


_CALLS_READ = 5_000

_CHIP_TIPS = {
    "": "Show every call",
    "5xx": "Show only the server errors (5xx)",
    "4xx": "Show only the calls refused (4xx)",
    "slow": "Show only the calls that came close to the time limit",
    "429": "Show only the throttled calls (429)",
    "2xx": "Show only the calls that went well",
}


def _call_in_chip(call: Call, chip: str, limit_ms: int) -> bool:
    """
    Whether a call belongs under a chip ('' is every one).
    """
    status = call.status or 0
    if chip == "5xx":
        return status >= 500
    if chip == "4xx":
        return 400 <= status < 500
    if chip == "429":
        return status == 429
    if chip == "2xx":
        return 200 <= status < 400
    if chip == "slow":
        return call.latency_ms is not None and call.latency_ms >= SLOW_SHARE * limit_ms
    return True


def _call_text(call: Call) -> tuple[str, ...]:
    return (call.request_id, call.route_key, call.why, str(call.status or ""), call.lambda_request, call.ip)


class _CallsTab:
    """
    The Calls tab, a part of APIGatewayExplorer: what it keeps is `call_query` (the search box's text), `call_chip`,
    `call_offset` (the first line of the page shown), `call_selected` (the request ID open on the right),
    `calls_shown` (the calls the search and the chip leave) and `run`, the Lambda run read for the call open.
    """

    def _calls_page(self: APIGatewayExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.call_query, self.call_chip, self.call_offset, self.call_selected = "", "", 0, ""
        self.calls_shown: list[Call] = []
        self.run: LambdaRun | None = None
        self._call_rows: list[_Line] = []
        self.since_pick = w.Dropdown(options=list(_SINCE_CHOICES), value=self._since,
                                     layout=layout(flex="0 0 auto", width="150px"))
        self.since_pick.observe(self._safely(self._since_changed), names="value")
        self.call_find = w.Text(placeholder="Find calls: a request ID, a path, an error… (Enter searches the whole log)",
                                continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.call_find.add_class("agx-find")
        self.call_find.observe(self._safely(self._call_typed), names="value")
        self.call_find.on_msg(self._on_enter(self._call_entered))
        finder = w.HBox([self.since_pick, self.call_find], layout=layout(width="100%", align_items="center"))
        finder.add_class("agx-filters")
        self.call_chips = w.HBox(layout=layout(width="100%"))
        self.call_chips.add_class("agx-chips")
        self.timeline = w.HTML(layout=layout(width="100%"))
        self.call_rows = w.VBox(layout=layout(width="100%"))
        self.call_rows.add_class("agx-rows")
        self.call_pager = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.call_buttons = {}
        for key, glyph, tip in (("first", "«", "The newest calls"), ("previous", "‹", "Newer calls"),
                                ("next", "›", "Older calls"), ("last", "»", "The oldest calls")):
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("agx-pg")
            button.on_click(self._safely(lambda _button, key=key: self._call_page_to(key)))
            self.call_buttons[key] = button
        pager = w.HBox([self.call_pager, *self.call_buttons.values()], layout=layout(width="100%"))
        pager.add_class("agx-pager")
        left = w.VBox([finder, self.call_chips, self.timeline, self.call_rows, pager])
        for name in ("agx-left", "agx-calls"):
            left.add_class(name)
        self.call_view = w.HTML(layout=layout(width="100%"))
        self.call_right = w.VBox([self.call_view])
        self.call_right.add_class("agx-right")
        self.call_split = w.HBox([left, self.call_right])
        self.call_split.add_class("agx-split")
        self.call_left = left
        return self.call_split

    def _limit_ms(self: APIGatewayExplorer) -> int:
        return self.api.max_timeout_ms if self.api else 29_000

    def _load_calls(self: APIGatewayExplorer, pattern: str | None = None) -> None:
        """
        Reads the stage's access log for the time picked, in the background (the text searched for over the whole
        time when `pattern` is given).
        """
        api = self.api
        if api is None:
            return
        stage = self.stage.name if self.stage else None
        self.page = None
        self._set(self.timeline, "")
        self.call_rows.children = []
        self._set(self.call_view, _skeleton("Reading the stage's access log…"))
        self._status(f"Reading {api.name}'s access log for the last {self._since_label()}…", busy=True, owner="calls")
        since, api_id = self._since, api.id
        self._later("calls", lambda: self.core.calls(api_id, stage=stage, since=since, limit=_CALLS_READ,
                                                     pattern=pattern), self._got_calls, self._calls_failed)

    def _calls_failed(self: APIGatewayExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.call_view, self._html([_Note(f"Couldn't read the access log: {text}", "warn")]))

    def _got_calls(self: APIGatewayExplorer, page: CallPage) -> None:
        self.page = page
        self.call_offset = 0
        if page.note:
            self._draw("calls", self.call_view, self.ui._calls_blocks(page))
            self._draw_call_chips()
            self._draw_call_rows()
            self._finish("calls", f"Stage {self.stage.name if self.stage else ''} keeps no access log: its calls "
                                  "aren't recorded.", "warn", tabs=("calls",))
            self._after_calls()
            return
        self._set(self.timeline, _timeline_html(page.calls, page.since, page.until) + _failures_line(page.calls))
        self._draw_call_chips()
        self._refilter_calls()
        failed = len(page.failed)
        _class_if(self.tab_buttons["calls"], "agx-alarm", any((c.status or 0) >= 500 for c in page.calls))
        self._draw("calls", self.call_view, [_Note("Click a call on the left to see where its time went and the "
                                                   "Lambda run behind it.")])
        cut = f" (the newest {len(page.calls):,})" if page.truncated else ""
        self._finish("calls", f"{_plural(len(page.calls), 'call')} in the last {self._since_label()}{cut}"
                              + (f" · {failed:,} failed" if failed else ""), tabs=("calls",))
        self._after_calls()

    def _after_calls(self: APIGatewayExplorer) -> None:
        """
        What the calls feed besides the Calls tab: the routes' numbers (when the stage has no detailed metrics) and
        the Performance tab.
        """
        if self.report is not None and not self.report.route_numbers:
            self._draw_routes()
        if self._tab == "performance":
            self._draw_performance()
        wanted, self._want_call = self._want_call, None
        if wanted:
            self._open_call(wanted)

    def _since_changed(self: APIGatewayExplorer, change: dict[str, Any]) -> None:
        self._since = change["new"]
        self._load_calls()

    def _call_typed(self: APIGatewayExplorer, change: dict[str, Any]) -> None:
        self.call_query = str(change["new"] or "").strip()
        self.call_offset = 0
        self._refilter_calls()

    def _call_entered(self: APIGatewayExplorer) -> None:
        """
        Enter in the search box: CloudWatch Logs looks for the text in every line of the time picked, so a request ID
        older than the lines read is found too.
        """
        text = self.call_query
        self._load_calls(f'"{text}"' if text else None)

    def _draw_call_chips(self: APIGatewayExplorer) -> None:
        """
        A chip per kind of call there is, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        calls = self.page.calls if self.page else []
        chips = []
        for key, label, tone in _CALL_CHIPS if calls else ():
            count = sum(1 for call in calls if _call_in_chip(call, key, self._limit_ms()))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=_CHIP_TIPS[key]
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("agx-chip", f"agx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "agx-on", key == self.call_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_call_chip(key)))
            chips.append(chip)
        self.call_chips.children = chips

    def _pick_call_chip(self: APIGatewayExplorer, chip: str) -> None:
        self.call_chip = "" if chip == self.call_chip else chip
        self.call_offset = 0
        self._draw_call_chips()
        self._refilter_calls()

    def _refilter_calls(self: APIGatewayExplorer) -> None:
        calls = self.page.calls if self.page else []
        shown = [c for c in calls if _call_in_chip(c, self.call_chip, self._limit_ms())]
        if self.call_query:
            shown = [c for c in shown if search_rank(self.call_query, _call_text(c)) is not None]
        self.calls_shown = shown
        self._draw_call_rows()

    def _draw_call_rows(self: APIGatewayExplorer) -> None:
        page = self.calls_shown[self.call_offset:self.call_offset + _CALL_PAGE]
        while len(self._call_rows) < len(page):
            self._call_rows.append(_Line(self, self._clicked_call))
        words = self.call_query.split()
        for line, call in zip(self._call_rows, page):
            line.key = call.request_id
            self._set(line.face, _call_face(call, words))
            _class_if(line.box, "agx-on", call.request_id == self.call_selected)
        children: list[Any] = [line.box for line in self._call_rows[:len(page)]]
        if not page:
            children = [self._w.HTML(f'<div class="agx-empty">{_esc(self._no_calls_text())}</div>')]
        self.call_rows.children = children
        self._draw_call_pager()

    def _no_calls_text(self: APIGatewayExplorer) -> str:
        if self.page is None:
            return "Reading the access log…"
        if self.page.note:
            return "This stage keeps no access log, so there are no calls to show."
        if self.call_query or self.call_chip:
            return "No call matches. Clear the search, pick All above, or press Enter to search the whole log."
        return "No calls in this time: pick a longer one above."

    def _draw_call_pager(self: APIGatewayExplorer) -> None:
        total = len(self.calls_shown)
        first, last = self.call_offset + 1, min(self.call_offset + _CALL_PAGE, total)
        self._set(self.call_pager, f"<b>{first:,}–{last:,}</b> of {total:,}" if total else "0 calls")
        more = total > _CALL_PAGE
        for key, button in self.call_buttons.items():
            button.layout.display = "" if more else "none"
            button.disabled = (self.call_offset == 0) if key in ("first", "previous") else (last >= total)

    def _call_page_to(self: APIGatewayExplorer, where: str) -> None:
        end = max(0, (len(self.calls_shown) - 1) // _CALL_PAGE * _CALL_PAGE)
        self.call_offset = {"first": 0, "previous": max(0, self.call_offset - _CALL_PAGE),
                            "next": min(end, self.call_offset + _CALL_PAGE), "last": end}[where]
        self._draw_call_rows()

    def _clicked_call(self: APIGatewayExplorer, line: _Line) -> None:
        if line.key:
            self._open_call(line.key)

    def _renew_call_pane(self: APIGatewayExplorer) -> None:
        """
        A new box for the right side of the Calls tab, which starts at the top (widgets can't be scrolled from Python).
        """
        w = self._w
        self.call_view = w.HTML(layout=w.Layout(width="100%"))
        self.call_right = w.VBox([self.call_view])
        self.call_right.add_class("agx-right")
        self.call_split.children = [self.call_left, self.call_right]

    def _open_call(self: APIGatewayExplorer, request_id: str) -> None:
        """
        A call on the right: where its time went at once, then the Lambda run behind it, read in the background.
        """
        calls = self.page.calls if self.page else []
        call = next((c for c in calls if c.request_id == request_id), None)
        if call is None or self.api is None:
            self._status(f"No call with request ID {request_id} in the calls read: press Enter in the search box to "
                         "look through the whole log.", "warn")
            return
        self.call_selected, self.run = request_id, None
        self._drop("run")
        for line in self._call_rows:
            _class_if(line.box, "agx-on", line.key == request_id)
        self._renew_call_pane()
        api = self.api
        reading = [_Note("Reading the Lambda run behind it…")] if call.lambda_request else []
        self._draw("calls", self.call_view, self.ui._call_blocks(api, call, None) + reading)
        if not call.lambda_request:
            self._status(f"{call.route_key}: {call.status} · {call.why or 'no error'}")
            return
        self._status(f"{call.route_key}: {call.status} · reading the Lambda run behind it…", busy=True, owner="run")
        self._later("run", lambda: self.core.lambda_run(api, call), lambda run: self._got_run(call, run))

    def _got_run(self: APIGatewayExplorer, call: Call, run: LambdaRun) -> None:
        if self.call_selected != call.request_id or self.api is None:
            return
        self.run = run
        self._draw("calls", self.call_view, self.ui._call_blocks(self.api, call, run))
        said = f"its Lambda run, {len(run.lines)} lines" if run.found else f"its Lambda run: {run.note}"
        self._finish("run", f"{call.route_key}: {call.status} · {said}", tabs=("calls",))
