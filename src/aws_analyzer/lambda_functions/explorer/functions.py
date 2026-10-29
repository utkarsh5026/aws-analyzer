"""
The Functions tab: each function's line, and the tab's mixin.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..._kit.blocks import _Note
from ..._kit.errors import _why
from ..._kit.fmt import _count, _plural, _total, _utcnow, human_money
from ..._kit.render import _split_lead
from ..._kit.text import _esc, _marked, search_rank
from ..._kit.window import _class_if

from ..helpers import _midnight, _pct, human_ms
from ..models import Function, FunctionMetrics, ProvisionedConcurrency, RuntimeStatus, Trigger
from ..analysis.functions import runtime_status
from ..analysis.cost import function_monthly_cost
from ..analysis.overview import Overview
from ..analysis.findings import (
    RECENT_DAYS, _day_age, _errors_level, _recent, _runtime_findings, account_findings, function_findings,
)
from ..view import _unread
from .frame import _FUNCTION_PAGE, _FUNCTION_TABS, _window_text
from .widgets import _Row


_COLUMNS = (  # the function list's columns: (sort key, header, tooltip, width); a header sorts, again reverses
    ("name", "Function", "Sort by name", ""),
    ("trend", "Last 14 days", f"Calls a day, the failed part in red. Sort by the share of calls that failed in the last "
                              f"{RECENT_DAYS} days", "96px"),
    ("calls", "Calls · 30d", "Sort by calls in the last 30 days", "86px"),
    ("errors", "Errors", "Sort by the share of calls that failed", "64px"),
    ("duration", "Avg run", "Sort by the average run time", "72px"),
    ("cost", "$ / month", "Sort by the estimated monthly cost", "76px"),
    ("called", "Last called", "Sort by when it was last called", "92px"),
    ("problems", "⚠", "Problems first: the most warnings, then the most failed calls", "38px"),
)

_FUNCTION_CHIPS = (  # (key, label, tone): a chip shows only the functions it names; again shows every function
    ("", "All", "all"), ("attention", "Needs attention", "warn"), ("errors", "With errors", "bad"),
    ("runtime", "Old runtime", "warn"), ("public", "Public", "warn"), ("idle", "Not called", "idle"),
)

_TREND_DAYS = 14

_SHORTEST_BAR = 12


@dataclass
class _Facts:
    """
    What the explorer's list says about one function, worked out once each time more is read. Its tone is its dot:
    'bad' (failing), 'warn' (a warning), 'ok', 'idle' (not called) or '' (not read yet); its headline is what its
    line says is wrong, in place of its description.
    """

    fn: Function
    metrics: FunctionMetrics | None
    status: RuntimeStatus
    findings: list[tuple[str, str]]
    triggers: list[Trigger]
    cost: float | None
    tone: str
    headline: str = ""

    @property
    def warnings(self) -> list[str]:
        return [message for level, message in self.findings if level == "warn"]


def _function_facts(fn: Function, ov: Overview, prices: dict[str, float], now: datetime) -> _Facts:
    detail = ov.detail(fn)
    m = detail.metrics
    found = function_findings(fn, m, detail, prices=prices, now=now)
    status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
    cost = _total(function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices))
    if _errors_level(m, now) == "warn" or fn.state == "Failed" or fn.last_update == "Failed" or (
            fn.reserved_concurrency == 0):
        tone = "bad"
    elif any(level == "warn" for level, _ in found):
        tone = "warn"
    elif m is not None:
        tone = "ok" if m.invocations else "idle"
    else:
        tone = ""
    return _Facts(fn, m, status, found, detail.triggers, cost, tone, _headline(fn, m, status, found, now))


def _headline(fn: Function, m: FunctionMetrics | None, status: RuntimeStatus, found: list[tuple[str, str]],
              now: datetime) -> str:
    """
    What a function's line in the list says is wrong: the lead of its first warning ('3 of 120 calls failed (2.5%)
    in the last 30 days'), or '' when it has none. When its calls fail often enough to act on, that comes before an
    old runtime, so the line says why its dot is red.
    """
    warnings = [message for level, message in found if level == "warn"]
    if _errors_level(m, now) == "warn":
        about_runtime = {message for _, message in _runtime_findings(fn, status)}
        warnings = [message for message in warnings if message not in about_runtime] or warnings
    if not warnings:
        return ""
    lead, _, _ = _split_lead(warnings[0])
    return _window_text(lead or warnings[0])


def _trend_days(m: FunctionMetrics | None, now: datetime) -> list[tuple[float, float]]:
    """
    (calls, failed calls) for each of the last _TREND_DAYS UTC days, oldest first, 0 for a day without calls; [] when
    CloudWatch's numbers aren't daily ones.
    """
    if m is None or m.period < 86400:
        return []
    by_day = {d.start.astimezone(timezone.utc).date(): d for d in m.daily}
    today = _midnight(now)
    days = []
    for back in range(_TREND_DAYS - 1, -1, -1):
        usage = by_day.get((today - timedelta(days=back)).date())
        days.append((usage.invocations, usage.errors) if usage is not None else (0.0, 0.0))
    return days


def _trend_html(days: list[tuple[float, float]]) -> str:
    """
    The trend column: a bar a day as tall as its calls against the busiest day, its failed part in red on top. A day
    with any calls is at least _SHORTEST_BAR percent tall, so it stays visible beside a busy one.
    """
    if not days:
        return "-"
    busiest = max(calls for calls, _ in days)
    bars = []
    for calls, failed in days:
        if not calls:
            bars.append('<i class="z"></i>')
            continue
        height = max(_SHORTEST_BAR, 100 * calls / busiest)
        red = f'<b style="height:{min(100.0, 100 * failed / calls):.1f}%"></b>' if failed else ""
        bars.append(f'<i style="height:{height:.1f}%">{red}</i>')
    return f'<span class="tr">{"".join(bars)}</span>'


def _function_face(f: _Facts, words: list[str], multi: bool, numbers: bool, now: datetime) -> str:
    """
    A function's line in the list: its health dot, name, runtime and description, then its numbers in columns.
    """
    fn, m = f.fn, f.metrics
    runtime = f.status.label
    parts = [fn.description, _triggers_text(f.triggers), f"{fn.memory:,} MB", f"{fn.timeout} s"]
    line = " · ".join(p for p in parts if p)
    if f.headline and not words:
        about = f'<div class="ff hl {f.tone}">{_esc(f.headline)}</div>'
    else:
        about = f'<div class="ff">{_marked(line, words)}</div>'
    loading = '<span class="sk"></span>'

    def cell(css: str, value: str, tone: str = "") -> str:
        inner = f'<span class="pill {tone}">{_esc(value)}</span>' if tone else _esc(value)
        return f'<div class="fc {css}">{inner}</div>'

    if m is None:
        dash = loading if numbers else "-"
        columns = ("c-trend", "", "", "c-dur", "", "c-called")
        cells = "".join(f'<div class="fc {css}">{dash}</div>' for css in columns)
    else:
        level = _errors_level(m, now)
        rate = _pct(m.error_rate) if m.invocations else "-"
        cells = (f'<div class="fc c-trend">{_trend_html(_trend_days(m, now))}</div>'
                 + cell("", _count(round(m.invocations)) if m.invocations else "0")
                 + cell("" if m.invocations else "dim", rate, "bad" if level == "warn" else "warn" if level else "")
                 + cell("c-dur" + ("" if m.invocations else " dim"), human_ms(m.avg_duration) if m.invocations
                        else "-")
                 + cell("" if f.cost is not None else "dim", human_money(f.cost) if f.cost is not None else "-")
                 + cell("c-called" + ("" if m.invocations else " dim"),
                        _day_age(m.last_invoked, now) if m.invocations else "not in 30d"))
    warnings = len(f.warnings)
    badge = f'<span class="wb">{warnings}</span>' if warnings else ""
    region = f'<span class="rg">{_esc(fn.region)}</span>' if multi else ""
    return (f'<div class="fr"><span class="dot {f.tone}"></span><div class="fm"><div class="fn">'
            f"<b>{_marked(fn.name, words)}</b>"
            f'<span class="rt {f.status.tone}">{_esc(runtime)}</span>{region}</div>'
            f'{about}</div>{cells}<div class="fc">{badge}</div><div></div></div>')


def _triggers_text(triggers: list[Trigger]) -> str:
    labels = list(dict.fromkeys(t.short for t in triggers))
    return ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")


class _FunctionsTab:
    """
    The Functions tab of LambdaExplorer: every function in the region, read in three steps so the list shows at
    once, searched, filtered by its chips and sorted by a click on a column.
    """

    def _functions_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.find = w.Text(placeholder="Search functions: name, description, runtime, trigger…",
                           continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.find.add_class("lmx-find")
        self.find.observe(self._safely(self._found_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("lmx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.find, "value", "")))
        finder = w.HBox([self.find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("lmx-bar")
        self.chips = w.HBox(layout=layout(width="100%"))
        self.chips.add_class("lmx-chips")
        self.list_note = w.HTML(layout=layout(width="100%"))
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip, width in _COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width=width or "auto"))
            for name in ("lmx-col", f"lmx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_by(key)))
            self.column_buttons[key] = button
        spacer = w.HTML(layout=layout(width="52px", flex="0 0 auto"))
        self.list_head = w.HBox([*self.column_buttons.values(), spacer], layout=layout(width="100%"))
        self.list_head.add_class("lmx-lhead")
        self.rows_box = w.VBox([self.list_head], layout=layout(width="100%", flex="1 1 auto"))
        self.rows_box.add_class("lmx-rows")
        pager = w.HBox(self._make_pager("functions"), layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        page = w.VBox([finder, self.chips, self.list_note, self.rows_box, pager])
        page.add_class("lmx-listpage")
        return page

    def _fill_regions(self) -> None:
        here = self.core.region
        try:
            others = sorted(set(self.core.session.get_available_regions("lambda")) - {here})
        except Exception:  # an odd session: the field still offers this region and every region
            others = []
        options = [(f"Region: {here}", here), ("Region: every region", "all")]
        if self.region not in (here, "all", None) and self.region not in others:
            options.append((f"Region: {self.region}", self.region))
        options += [(f"Region: {name}", name) for name in others]
        self._quietly(self.region_pick, options=options, value=self.region or here)

    def _region_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self.region = str(change["new"])
        self._load_list(reopen=self.function, elsewhere=False)

    def _load_list(self, reopen: Function | None = None, elsewhere: bool = True) -> None:
        """
        Lists the region's functions (in the background); then CloudWatch's numbers, then who may call each.
        reopen: the function to show again once they're listed; elsewhere=False closes it if the list doesn't hold it
        (another region was picked).
        """
        self._drop("list", "numbers", "extras")
        self.overview, self._facts, self._numbers_pending = None, {}, False
        self._offset = 0
        self.field.problem = ""
        self.field.set_choices([])
        self._listing_started = time.monotonic()
        self._listing = True
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._set(self.list_note, "")
        self._refilter()
        where = self._where()
        regions = None if self.region == self.core.region else "all" if self.region == "all" else [self.region]
        self._counted = 0
        self._status(f"Listing the functions in {where}…", busy=True, owner="list")

        def progress(done: int, total: int | None = None) -> None:
            self._counted = done

        self._later("list", lambda: self.core.overview(regions=regions, metrics=False, details=False,
                                                       progress=progress),
                    lambda ov: self._listed(ov, reopen, elsewhere), self._list_failed,
                    counting="Listing the functions in every region… {n} regions read so far" if self.region == "all"
                    else "",
                    owner="list")

    def _list_failed(self, exc: BaseException) -> None:
        self._listing = False
        text = self._error_text(exc)
        self._status(text, "warn")
        self.field.problem = f"Couldn't list the functions ({text}). Type a function's name or ARN and press Enter."
        self.field.draw()
        self._set(self.list_note, self._html([_Note(f"Couldn't list the functions: {text}", "warn")]))
        self._draw_rows()
        self._open_wanted()

    def _listed(self, ov: Overview, reopen: Function | None = None, elsewhere: bool = True) -> None:
        self.overview = ov
        self._listing = False
        self._compute_facts()
        denied = sorted({code for key, code in ov.errors.items() if key.endswith(":list")})
        self.field.problem = (f"Couldn't list the functions ({', '.join(denied)}; needs lambda:ListFunctions). Type "
                              "a function's name or ARN and press Enter." if denied and not ov.functions else "")
        self.field.set_choices(ov.functions)
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._draw_list_note()
        self._refilter()
        if ov.functions:
            self._numbers_pending = True
            self._draw_rows()
            until = _utcnow()
            since = _midnight(until) - timedelta(days=ov.days - 1)
            functions, limits = list(ov.functions), set(ov.accounts)
            self._finish("list", f"{_plural(len(ov.functions), 'function')} in {self._where()} · reading their calls, "
                         "errors and run times from CloudWatch…", tabs=("functions",), busy=True)
            self._later("numbers",
                        lambda: self.core.overview_numbers(functions, since=since, until=until, limits=limits),
                        self._got_numbers, self._numbers_failed)
        elif not any(key.endswith(":list") for key in ov.errors):
            self._finish("list", f"No Lambda functions in {self._where()}"
                         + ("." if self.region == "all" else ": pick every region in the region field to look in "
                                                              "all of them."), tabs=("functions",))
        same = next((f for f in ov.functions if f.arn == reopen.arn), None) if reopen is not None else None
        if reopen is not None and (same is not None or elsewhere):
            self._open_function(same or reopen, self._tab if self._tab in _FUNCTION_TABS else None)
        elif reopen is not None:
            self._close_function()
        else:
            self._open_wanted()

    def _open_wanted(self) -> None:
        want, tab, search = self._want, self._want_tab, self._want_search
        self._want, self._want_tab, self._want_search = None, None, None
        if want:
            self._open_named(want, tab, search=search)
        elif tab and tab not in _FUNCTION_TABS:
            self._show_tab(tab)

    def _numbers_failed(self, exc: BaseException) -> None:
        self._numbers_pending = False
        self._draw_rows()
        self._status(f"Couldn't read CloudWatch's numbers: {self._error_text(exc)}", "warn")

    def _got_numbers(self, got: tuple[dict[str, FunctionMetrics], int, dict[str, float | None], dict[str, str]]
                     ) -> None:
        ov = self.overview
        if ov is None:
            return
        ov.add_numbers(*got)
        self._numbers_pending = False
        self._redraw_list()
        functions = list(ov.functions)
        self._finish("list", f"{_plural(len(functions), 'function')} in {self._where()} · reading who may call each one…",
                     tabs=("functions",), busy=True)
        self._later("extras", lambda: self.core._all_extras(functions), self._got_extras, self._extras_failed)

    def _extras_failed(self, exc: BaseException) -> None:
        self._status(f"Couldn't read the functions' resource policies: {self._error_text(exc)}", "warn")

    def _got_extras(self, found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]) -> None:
        ov = self.overview
        if ov is None:
            return
        ov.add_extras(found)
        self._redraw_list()
        attention = sum(1 for f in self._facts.values() if f.warnings)
        took = time.monotonic() - self._listing_started
        cost = ov.metrics_read / 1000 * self.core.prices["metric_request"]
        read = f" ({ov.metrics_read:,} CloudWatch metrics, about {human_money(cost)})" if ov.metrics_read else ""
        self._finish("list", f"{_plural(len(ov.functions), 'function')} in {self._where()}, read in {took:.1f}s{read}"
                     + (f" · {attention:,} need attention: the Functions tab lists them first" if attention
                        else " · none needs attention"), "" if attention else "ok", tabs=("functions",))

    def _redraw_list(self) -> None:
        self._compute_facts()
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._draw_list_note()
        self._refilter()
        self._draw_alerts()

    def _compute_facts(self) -> None:
        ov = self.overview
        now = _utcnow()
        self._facts = {fn.arn: _function_facts(fn, ov, self.core.prices, now) for fn in ov.functions} if ov else {}

    def _draw_list_note(self) -> None:
        """
        Notes above the list: regions whose functions couldn't be listed, sections that couldn't be read, regions
        left out, and limits that are close to running out.
        """
        ov = self.overview
        if ov is None:
            return
        notes: list[Any] = []
        for key, code in sorted(ov.errors.items()):
            region, _, section = key.partition(":")
            if section == "list":
                notes.append(_Note(f"Couldn't list the functions in {region} ({_why(code, 'lambda:ListFunctions')}).",
                                   "warn"))
        unread: dict[str, dict[str, str]] = defaultdict(dict)
        for key, code in ov.errors.items():
            region, _, section = key.partition(":")
            if section != "list":
                unread[region][section] = code
        for region, errors in sorted(unread.items()):
            note = _unread(errors, f"{region}'s ")
            if note:
                notes.append(note)
        if ov.skipped and self.region == "all":
            notes.append(_Note(f"Left out {_plural(len(ov.skipped), 'region')} not turned on for this account: "
                               f"{', '.join(sorted(ov.skipped))}."))
        for region, limits in sorted(ov.accounts.items()):
            notes += [_Note(f"{message}", "warn") for level, message in account_findings(limits, ov.days)
                      if level == "warn"]
        self._set(self.list_note, self._html(notes) if notes else "")

    def _found_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._query = str(self.find.value or "").strip()
        self._offset = 0
        self._refilter()

    def _pick_chip(self, key: str) -> None:
        self._chip = "" if key == self._chip else key
        self._offset = 0
        self._draw_chips()
        self._refilter(renew=True)

    def _sort_by(self, key: str) -> None:
        if key == self._sort:
            self._descending = not self._descending
        else:
            self._sort, self._descending = key, key != "name"
        self._offset = 0
        self._refilter(renew=True)

    def _in_chip(self, f: _Facts, key: str) -> bool:
        m = f.metrics
        return {"": True, "attention": bool(f.warnings), "errors": bool(m and m.errors >= 1),
                "runtime": f.status.state in ("deprecated", "blocked", "ending"),
                "public": any(t.public for t in f.triggers), "idle": bool(m is not None and not m.invocations)}[key]

    def _chip_button(self, store: dict[str, Any], key: str, tone: str, on_click: Callable[[str], None]) -> Any:
        """
        The chip button for `key`, made once and kept: redrawing changes its label, not the widget (Live redraws
        the run chips every few seconds, and a widget is never freed unless it's closed).
        """
        if key not in store:
            chip = self._w.Button(layout=self._w.Layout(width="auto"))
            for name in ("lmx-chip", f"lmx-t-{tone}"):
                chip.add_class(name)
            chip.on_click(self._safely(lambda _button: on_click(key)))
            store[key] = chip
        return store[key]

    def _show_chips(self, box: Any, chips: list[tuple[Any, str, str, bool]]) -> None:
        """
        Puts chips (button, label, tooltip, picked) in their row, changing only what changed.
        """
        for chip, label, tip, picked in chips:
            if chip.description != label:
                chip.description = label
            if chip.tooltip != tip:
                chip.tooltip = tip
            _class_if(chip, "lmx-on", picked)
        shown = tuple(chip for chip, _, _, _ in chips)
        if tuple(box.children) != shown:
            box.children = shown

    def _draw_chips(self) -> None:
        """
        A chip per kind of function there is, with how many: a click shows only those (again shows all).
        """
        facts = list(self._facts.values())
        chips = []
        for key, label, tone in _FUNCTION_CHIPS:
            n = sum(1 for f in facts if self._in_chip(f, key))
            if key and not n:
                continue
            tip = ("Show every function" if not key
                   else f"Show only the functions that are {label.lower()} (click again for all)")
            chips.append((self._chip_button(self._function_chips, key, tone, self._pick_chip), f"{label} {n:,}", tip,
                          key == self._chip))
        self._show_chips(self.chips, chips if facts else [])

    def _sort_key(self, f: _Facts) -> Any:
        m = f.metrics
        calls = m.invocations if m is not None else -1.0
        if self._sort == "name":
            return (f.fn.name.lower(), f.fn.region)
        if self._sort == "calls":
            return (calls,)
        if self._sort == "trend":
            recent = _recent(m, _utcnow()) if m is not None else None
            failed_lately = recent[1] / recent[0] if recent and recent[0] else -1.0
            return (failed_lately, recent[1] if recent else -1.0)
        if self._sort == "errors":
            return ((m.error_rate or 0.0) if m is not None else -1.0, m.errors if m is not None else -1.0)
        if self._sort == "duration":
            return ((m.avg_duration or 0.0) if m is not None else -1.0,)
        if self._sort == "cost":
            return (f.cost if f.cost is not None else -1.0,)
        if self._sort == "called":
            return ((m.last_invoked.timestamp() if m is not None and m.last_invoked else 0.0),)
        return ({"bad": 3, "warn": 2}.get(f.tone, 0), len(f.warnings), m.errors if m is not None else 0.0, calls)

    def _refilter(self, renew: bool = False) -> None:
        facts = [f for f in self._facts.values() if self._in_chip(f, self._chip)] if self._chip else list(
            self._facts.values())
        if self._query:
            ranked = []
            for i, f in enumerate(facts):
                fn = f.fn
                rank = search_rank(self._query, (fn.name, fn.description, fn.runtime, fn.region, fn.handler,
                                                 _triggers_text(f.triggers), " ".join(t.source for t in f.triggers),
                                                 (fn.role or "").split("/")[-1]))
                if rank is not None:
                    ranked.append((rank, i, f))
            facts = [f for _, _, f in sorted(ranked, key=lambda r: r[:2])]
        if not self._query or self._sort != "problems":  # a search ranks its best match first, unless a column sorts
            facts = sorted(facts, key=lambda f: (f.fn.name.lower(), f.fn.region))
            facts = sorted(facts, key=self._sort_key, reverse=self._descending)
        self._visible = facts
        self._offset = min(self._offset, max(0, (len(facts) - 1) // _FUNCTION_PAGE * _FUNCTION_PAGE))
        for key, button in self.column_buttons.items():
            _class_if(button, "lmx-on", key == self._sort)
            label = next(label for k, label, _, _ in _COLUMNS if k == key)
            arrow = (" ▾" if self._descending else " ▴") if key == self._sort and key != "problems" else ""
            if button.description != label + arrow:
                button.description = label + arrow
        self._draw_rows(renew=renew)

    def _draw_rows(self, renew: bool = False) -> None:
        page = self._visible[self._offset:self._offset + _FUNCTION_PAGE]
        while len(self._rows) < len(page):
            self._rows.append(_Row(self, self._clicked_function,
                                   action=("Logs", "Open its logs", self._clicked_logs)))
        words = self._query.split()
        now = _utcnow()
        for row, f in zip(self._rows, page):
            row.item = f.fn
            self._set(row.face, _function_face(f, words, self.multi, self._numbers_pending, now))
            warns = f.warnings
            tip = (f"{f.fn.name}" + (f": {f.fn.description}" if f.fn.description else "")
                   + (f" · {_plural(len(warns), 'warning')}" if warns else "") + " · click to open it")
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            _class_if(row.box, "lmx-on", self.function is not None and f.fn.arn == self.function.arn)
        children: list[Any] = [self.list_head, *(row.box for row in self._rows[:len(page)])]
        if not page:
            if self.overview is None:
                text = "Listing the functions…" if self._listing else "No functions listed."
            elif not self.overview.functions:
                text = (f"No Lambda functions in {self._where()}."
                        + ("" if self.region == "all" else " Functions are regional: pick every region in the region "
                                                           "field above to look in all of them."))
            else:
                text = "No function matches." + (" Clear the search, or pick All above." if self._query or self._chip
                                                 else "")
            children = [self.list_head, self._empty("functions", text)]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%", flex="1 1 auto"))
            box.add_class("lmx-rows")
            page_box = self.pages["functions"] if hasattr(self, "pages") else None
            if page_box is not None:
                page_box.children = [box if child is self.rows_box else child for child in page_box.children]
            self.rows_box = box
        else:
            self.rows_box.children = children
        whole = len(self._facts) if self._facts else None
        self._draw_pager("functions", self._offset, len(self._visible), _FUNCTION_PAGE, "functions", whole)

    def _mark_rows(self) -> None:
        for row in self._rows:
            _class_if(row.box, "lmx-on", self.function is not None and row.item is not None
                      and row.item.arn == self.function.arn)

    def _clicked_function(self, fn: Function) -> None:
        self._open_function(fn, "overview")

    def _clicked_logs(self, fn: Function) -> None:
        self._open_function(fn, "logs")
