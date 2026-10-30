"""
The picked function's header, Overview and Settings tabs.
"""

from __future__ import annotations

import dataclasses
import difflib
from datetime import timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError

from ..._kit.blocks import _Cards, _Note, _Text, _Title
from ..._kit.errors import _error_code
from ..._kit.fmt import _count, _plural, _total, _utcnow, human_money, human_size
from ..._kit.text import _clip, _esc
from ..._kit.window import _class_if, _skeleton

from ..helpers import MB, _midnight, _pct, human_ms, parse_function_ref
from ..models import Function, FunctionDetail
from ..analysis.functions import _source_name
from ..analysis.cost import function_monthly_cost
from ..analysis.findings import _errors_level, function_findings
from ..analysis.changes import change_findings, function_changes
from ..blocks.wiring import _Columns, _Wiring
from .frame import _FUNCTION_TABS, _short_day, _with_findings


_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.3" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 4.5h2.3c.9 0 1.6.5 2 1.3l6.7 13.7"/>'
         '<path d="M12.3 10.6 6.4 19.5"/></svg>')


_SETUP_CARDS = {"Runtime", "Memory", "Timeout", "Architecture", "Triggers"}  # function_info()'s cards the Overview

# keeps: the window's header already shows the calls, errors, run times and cost
_DESTINATIONS = {"sqs": "SQS queue", "sns": "SNS topic", "lambda": "Lambda function", "events": "EventBridge bus",
                 "s3": "S3 bucket"}  # an on-success / on-failure destination's service -> what it is


def _retries(count: int) -> str:
    return "no retries" if not count else "1 retry" if count == 1 else f"{count} retries"


def _destination(arn: str | None) -> tuple[str, str]:
    """
    (what it is, its name) for an on-success / on-failure destination or a dead-letter queue: an SQS queue, an SNS
    topic, another function...
    """
    parts = (arn or "").split(":")
    kind = _DESTINATIONS.get(parts[2], parts[2]) if len(parts) > 5 else ""
    return kind or "destination", _source_name(arn)[0] or (arn or "-")


class _FunctionTab:
    """
    The picked function in LambdaExplorer: opening it, its header (title, cards and alerts), and its Overview
    and Settings tabs.
    """

    # ------------------------------------------------------------------ header

    def _draw_title(self) -> None:
        fn, ov = self.function, self.overview
        if fn is None:
            sub = [_plural(len(ov.functions), "function") if ov is not None else "",
                   "every function, its logs run by run, its errors and code", "read-only"]
        else:
            sub = [fn.name, fn.runtime or "container image", fn.region if self.multi else "",
                   _clip(fn.description, 90) if fn.description else ""]
        where = "every region" if self.region == "all" else (self.region or "")
        self._set(self.title, f'<div class="lmx-brand"><span class="lmx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="lmx-name">Lambda explorer<span>{_esc(where)}</span></div>'
                              f'<div class="lmx-sub">{_esc(" · ".join(p for p in sub if p))}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the function field: the region at a glance, or the picked function.
        """
        now = _utcnow()
        cards: list[tuple[str, str, str]] = []
        loading = self._numbers_pending or (self.overview is None and self._listing)
        if self.function is None:
            facts = list(self._facts.values())
            if self.overview is None:
                cards = [("Functions", "…", "sk-on"), ("Calls · 30d", "…", "sk-on"), ("Error rate", "…", "sk-on")]
            else:
                read = [f.metrics for f in facts if f.metrics is not None]
                calls = sum(m.invocations for m in read)
                failed = sum(m.errors for m in read)
                costs = [f.cost for f in facts if f.cost is not None]
                warn = any(_errors_level(f.metrics, now) == "warn" for f in facts)
                attention = sum(1 for f in facts if f.warnings)
                cards.append(("Functions", f"{len(facts):,}", ""))
                cards.append(("Calls · 30d", "…" if loading else _count(round(calls)), "sk-on" if loading else ""))
                cards.append(("Error rate", "…" if loading else (_pct(failed / calls) if calls else "-"),
                              "sk-on" if loading else "warn" if warn else ""))
                cards.append(("Est. $ / month", "…" if loading else (human_money(sum(costs)) if costs else "-"),
                              "sk-on" if loading else ""))
                cards.append(("Need attention", f"{attention:,}", "warn" if attention else "ok" if facts else ""))
        else:
            fn = self._full()
            f = self._facts.get(self.function.arn)
            m = self.detail.metrics if self.detail is not None and self.detail.metrics is not None else (
                f.metrics if f is not None else None)
            if m is None:
                waiting = loading or self.detail is None
                cards += [(label, "…" if waiting else "-", "sk-on" if waiting else "")
                          for label in ("Calls · 30d", "Error rate", "Avg / longest run")]
            else:
                level = _errors_level(m, now)
                near = bool(m.duration_max and fn.timeout and m.duration_max >= 0.9 * fn.timeout * 1000)
                cards.append(("Calls · 30d", _count(round(m.invocations)), ""))
                cards.append(("Error rate", _pct(m.error_rate) if m.invocations else "-",
                              "bad" if level == "warn" else "warn" if level else ""))
                cards.append(("Avg / longest run", f"{human_ms(m.avg_duration)} / {human_ms(m.duration_max)}"
                              if m.invocations else "not called in 30 days", "warn" if near else ""))
            if self.detail is not None:
                cost = _total(function_monthly_cost(fn, self.detail.metrics, self.detail.provisioned,
                                                    self.detail.log_group, prices=self.core.prices))
                warnings = sum(level == "warn" for level, _ in function_findings(
                    fn, self.detail.metrics, self.detail, prices=self.core.prices, now=now))
            else:
                cost = f.cost if f is not None else None
                warnings = len(f.warnings) if f is not None else 0
            cards.append(("Est. $ / month", human_money(cost) if cost is not None else "-", ""))
            cards.append(("Warnings", f"{warnings:,}", "warn" if warnings else "ok"))
        html_cards = "".join(f'<div class="lmx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="lmx-stats">{html_cards}</div>')

    def _draw_alerts(self) -> None:
        """
        A dot on a tab that holds something to look at: amber on Overview for warnings, red on Errors when calls
        fail often enough to act on.
        """
        fn = self.function
        f = self._facts.get(fn.arn) if fn is not None else None
        m = self.detail.metrics if self.detail is not None and self.detail.metrics is not None else (
            f.metrics if f is not None else None)
        warned = bool(f.warnings) if f is not None else False
        if self.detail is not None:
            warned = any(level == "warn" for level, _ in function_findings(
                self.detail.function, self.detail.metrics, self.detail, prices=self.core.prices))
        _class_if(self.tab_buttons["overview"], "lmx-alert", fn is not None and warned)
        _class_if(self.tab_buttons["errors"], "lmx-alarm", fn is not None and _errors_level(m, _utcnow()) == "warn")

    # ------------------------------------------------------------------ one function

    def _close_function(self) -> None:
        """
        Back to no function picked: the Functions tab, the header showing the region.
        """
        self._drop("describe", "logs", "search", "older", "errors", "perf", "code", "map", "lookup")
        self._stop_live()
        self.function = self.detail = None
        self.logs_page = self.found = self.error_report = self.perf = self.package = self.code_map = None
        self._how = False
        self._asked = set()
        self.field.value = ""
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_alerts()
        self._mark_rows()
        self._show_tab("functions")

    def _picked(self, fn: Function) -> None:
        self._open_function(fn)

    def _typed(self, text: str) -> None:
        self._open_named(text, None)

    def _open_named(self, name: str, tab: str | None, *, search: str | None = None) -> None:
        """
        Opens a function named the way you'd paste it: from the list when it's there, else read with GetFunction
        (an ARN in another region, a function the list doesn't hold).
        """
        wanted, _, region = parse_function_ref(name)
        listed = self.overview.functions if self.overview is not None else []
        fn = next((f for f in listed if f.name == wanted and (region is None or f.region == region)), None)
        if fn is not None:
            self._open_function(fn, tab, search=search)
            return
        where = region or (self.region if self.region not in (None, "all") else None)
        self._status(f"Looking up {wanted}…", busy=True, owner="lookup")

        def failed(exc: BaseException) -> None:
            if isinstance(exc, ClientError) and _error_code(exc) == "ResourceNotFoundException":
                close = difflib.get_close_matches(wanted, [f.name for f in listed], n=3, cutoff=0.6)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                if self.region == "all" and not region:
                    self._status(f"No function {wanted!r} in any region listed.{hint} Names are case-sensitive.",
                                 "warn")
                else:
                    self._status(f"No function {wanted!r} in {where or self.core.region}.{hint} Names are "
                                 "case-sensitive; pick every region in the region field to look in all of them.",
                                 "warn")
            else:
                self._status(self._error_text(exc), "warn")

        self._later("lookup", lambda: self.core.function(name, region=where),
                    lambda got: self._open_function(got, tab, search=search), failed)

    def _open_function(self, fn: Function, tab: str | None = None, *, search: str | None = None) -> None:
        """
        Shows a function: its Overview (or tab), read in the background; the other tabs read when they're opened.
        """
        self._drop("describe", "logs", "search", "older", "errors", "perf", "code", "map", "lookup")
        self._stop_live()
        self.function, self.detail = fn, None
        self.logs_page = self.found = self.error_report = self.perf = self.package = self.code_map = None
        self._how = False
        self._runs, self._found_runs, self._searched, self._log_query = [], [], "", ""
        self._open_runs, self._run_offset, self._run_chip = set(), 0, ""
        self._code_fn = self._package_data = self._code_file = self.saved = self.page_saved = None
        self._code_query, self._code_offset, self._code_open, self._code_shut = "", 0, set(), set()
        self._asked = set()
        self._quietly(self.log_find, value="")
        self._quietly(self.code_find, value="")
        self._remember(fn)
        self.field.value = fn.arn
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_alerts()
        self._mark_rows()
        name = fn.name
        self._set(self.overview_view, _skeleton(f"Reading {name}: its settings, what calls it, its last 30 days…"))
        self._set(self.settings_view, _skeleton(f"Reading {name}'s settings…"))
        for widget in (self.errors_view, self.perf_view, self.code_view, self.source_view, self.error_head,
                       self.slow_head, self.run_head):
            self._set(widget, "")
        self.error_rows_box.children = self.slow_rows_box.children = self.files_box.children = []
        self.runs_box.children = []
        self.run_chips.children = []
        self.code_split.layout.display = self.code_bar.layout.display = "none"
        self.code_more.layout.display = self.jump_button.layout.display = self.older_button.layout.display = "none"
        for key in ("overview", "settings", "errors", "performance"):
            self._renew(key)
        target = tab or (self._tab if self._tab in _FUNCTION_TABS else "overview")
        self._show_tab(target, load=not search)
        if search:
            self._quietly(self.log_find, value=search)
            self._log_query = search
            self._asked.add("logs")
            self._log_search()
        if target in ("overview", "settings"):
            self._status(f"Reading {name}: its settings, what calls it, its last 30 days…", busy=True,
                         owner="describe")
        self._later("describe", lambda: self.core.describe(name, region=fn.region or None, days=30), self._described,
                    self._describe_failed)

    def _describe_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview_view, self.settings_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _described(self, detail: FunctionDetail) -> None:
        self.detail = detail
        self._draw_stats()
        self._draw_alerts()
        self._draw_overview()
        self._draw_settings()
        self._save_look(dataclasses.replace(detail.function, arn=self.function.arn))
        self._finish("describe", self._tab_line(self._tab), tabs=("overview", "settings"))

    def _draw_overview(self) -> None:
        detail = self.detail
        if detail is None:
            return
        sections = self.ui._function_sections(detail, days=30)
        head = [dataclasses.replace(block, items=[item for item in block.items if item[0] in _SETUP_CARDS])
                if isinstance(block, _Cards) else block for block in sections["head"]]  # the header has the numbers
        blocks = [*_with_findings(head, self._since_last_look(detail)), self._wiring(detail), *self._charts(detail),
                  *sections["cost"]]
        self._draw("overview", self.overview_view, blocks)

    def _since_last_look(self, detail: FunctionDetail) -> list[tuple[str, str]]:
        """
        What changed in the function since your last look, as findings, when you've looked before.
        """
        before, since = self._look_before(self.function)
        if before is None or since is None:
            return []
        changes = function_changes(detail.function, before=before, since=since, now=_utcnow(), metrics=detail.metrics)
        return change_findings(changes)

    def _wiring(self, detail: FunctionDetail) -> _Wiring:
        """
        What calls the function, and where its results, failed events and logs go.
        """
        fn = detail.function
        inputs = []
        for t in detail.triggers:
            disabled = (t.state or "").lower() == "disabled"
            problem = bool(t.last_result and t.last_result.upper().startswith("PROBLEM"))
            detail_text = "; ".join(filter(None, ["anyone on the internet can call it" if t.public else "",
                                                  "disabled" if disabled else "", t.detail, t.last_result]))
            inputs.append((t.kind, t.source or ("anyone" if t.public else "-"), detail_text,
                           "warn" if t.public or disabled or problem else ""))
        outputs = []
        config = detail.async_config
        if config is not None and config.on_success:
            kind, name = _destination(config.on_success)
            outputs.append(("On success", name, f"{kind}: each asynchronous call's result", ""))
        failure = (config.on_failure if config is not None else None) or fn.dead_letter
        asynchronous = list(dict.fromkeys(t.kind for t in detail.triggers if t.asynchronous))
        if failure:
            kind, name = _destination(failure)
            retries = f"after {_retries(config.retries)}" if config is not None else ""
            outputs.append(("On failure", name, f"{kind}, {retries}".rstrip(", "), ""))
        elif asynchronous and config is not None:
            outputs.append(("On failure", "nowhere: dropped",
                            f"events from {', '.join(asynchronous)} that still fail after {_retries(config.retries)} "
                            "are lost", "warn"))
        group = detail.log_group
        kept = ("kept forever" if group is not None and group.retention_days is None
                else f"kept {_plural(group.retention_days, 'day')}" if group is not None and group.retention_days
                else "no log group yet" if group is None and "log_group" not in detail.errors else "")
        stored = f", {human_size(group.stored_bytes)} stored" if group is not None and group.stored_bytes else ""
        forever_and_big = group is not None and group.retention_days is None and (group.stored_bytes or 0) >= 100 * MB
        outputs.append(("Logs", fn.log_group, (kept + stored).strip(", "), "warn" if forever_and_big else ""))
        if fn.role:
            outputs.append(("Allowed to use", fn.role.split("/")[-1],
                            f"its execution role{' · in VPC ' + fn.vpc_id if fn.vpc_id else ''}", ""))
        center = (fn.name, " · ".join(filter(None, [fn.runtime or "container image", f"{fn.memory:,} MB",
                                                    f"{fn.timeout} s timeout", fn.architecture])))
        return _Wiring(inputs, center, outputs, title="How it's wired: what calls it, and where its results and logs go",
                       empty="Nothing calls it on its own: it's called directly (an SDK, Step Functions...)")

    def _charts(self, detail: FunctionDetail) -> list[Any]:
        """
        Calls a day (the failed part in red) and run time a day (the average under the longest), for 30 days.
        """
        m, fn = detail.metrics, detail.function
        if m is None or not m.invocations or m.period < 86400:
            return []
        today = _midnight(_utcnow())
        by_day = {d.start.astimezone(timezone.utc).date(): d for d in m.daily}
        days = [today - timedelta(days=30 - 1 - i) for i in range(30)]
        calls, times = [], []
        for day in days:
            d = by_day.get(day.date())
            label = _short_day(day)
            if d is None:
                calls.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
                times.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
                continue
            failed = f", {_count(round(d.errors))} failed ({_pct(d.errors / d.invocations)})" if d.errors and (
                d.invocations) else ""
            calls.append((label, d.invocations, d.errors, f"{day:%a} {label}: {_count(round(d.invocations))} calls"
                          + failed + (f", {_count(round(d.throttles))} throttled" if d.throttles else "")))
            times.append((label, d.duration_max or d.avg_duration or 0.0, d.avg_duration or 0.0,
                          f"{day:%a} {label}: average {human_ms(d.avg_duration)}, longest {human_ms(d.duration_max)}"))
        return [
            _Columns(calls, title="Calls a day, the failed part in red (CloudWatch, the last 30 days, UTC days)"),
            _Columns(times, title=f"Run time a day: the average (dark) under the longest (light), against its "
                                  f"{fn.timeout} s timeout", part="dim", limit=fn.timeout * 1000,
                     limit_label=f"timeout {fn.timeout} s", unit="ms"),
        ]

    def _draw_settings(self) -> None:
        detail = self.detail
        if detail is None:
            return
        fn = detail.function
        sections = self.ui._function_sections(detail, days=30)
        blocks: list[Any] = [_Title(f"Settings of {fn.name}", "in plain English, then as Lambda returns them")]
        for part in ("runs", "triggers", "async", "access", "versions", "tags", "raw"):
            blocks += sections[part]
        flag = f" --region {fn.region}" if fn.region else ""
        commands = [f"aws lambda get-function-configuration --function-name {fn.name}{flag}",
                    f"aws lambda get-policy --function-name {fn.name}{flag}",
                    f"aws lambda list-event-source-mappings --function-name {fn.name}{flag}",
                    f"aws lambda get-function-event-invoke-config --function-name {fn.name}{flag}"]
        blocks.append(_Text("\n".join(commands), title="The same from a terminal (read-only)", code=True))
        self._draw("settings", self.settings_view, blocks)
