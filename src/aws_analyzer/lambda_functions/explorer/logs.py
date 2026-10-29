"""
The Logs tab: each run's line and its lines of log, and the tab's mixin.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..._kit.blocks import _Note
from ..._kit.errors import _why
from ..._kit.fmt import _count, _fmt_dt, _in_zone, _plural, _tz_label, _utcnow, _zone_name, human_age, parse_time
from ..._kit.highlight import _json_source_html
from ..._kit.text import _clip, _esc, _marked
from ..._kit.window import _class_if, _running_loop

from ..helpers import _pct, human_ms
from ..models import LogEvent
from ..analysis.errors import _phrase
from ..analysis.logs import LogPage, LogRun, _marker, line_level
from ..analysis.performance import percentile
from ..analyzer import _SEARCH_HITS, LambdaAnalyzer
from ..view import _LINE_PREFIX_RE, _stamp
from .frame import _RUN_PAGE, _RUN_STATES, _run_clock, _seconds
from .widgets import _RunRow


_RANGES = (("Last 15 minutes", "15m"), ("Last hour", "1h"), ("Last 3 hours", "3h"), ("Last 12 hours", "12h"),
           ("Last 24 hours", "24h"), ("Last 3 days", "3d"), ("Last 7 days", "7d"), ("Last 30 days", "30d"))

_CUSTOM_RANGE = "custom"

_RANGE_CHOICES = (*_RANGES, ("Custom range…", _CUSTOM_RANGE))

_FIRST_TAB_RANGE = "24h"


_RUN_LINES = 3_000  # log lines the Logs tab reads at a time, the newest first ("Older runs" reads more)

_BODY_LINES = 500  # lines an open run shows; the rest are counted

_LIVE_SECONDS = 5  # how often Live looks for new lines...

_LIVE_MINUTES = 15  # ...and for how long, before it stops by itself


_RUN_CHIPS = (("", "All runs", "all"), ("failed", "Failed", "bad"), ("timeout", "Timed out", "bad"),
              ("logged", "Logged an error", "warn"), ("cold", "Cold starts", "cold"))


@dataclasses.dataclass
class _Reading:
    """
    A read of the Logs tab's time range under way (the job's key): what it says it's doing, the range, the most
    lines it reads, and what it says once the range is read but it isn't done (a search then reads each run found).
    """

    key: str
    label: str
    start: datetime
    end: datetime
    limit: int
    then: str = ""


def _read_share(reading: _Reading, reach: datetime | None, lines: int) -> float:
    """
    How much of a read is done, 0 to 1: the share of the time range read so far (it reads back from the end), or of
    the lines it stops at, whichever is further, since either ends it.
    """
    span = (reading.end - reading.start).total_seconds()
    covered = (reading.end - reach).total_seconds() / span if reach is not None and span > 0 else 0.0
    return min(1.0, max(covered, lines / reading.limit if reading.limit else 0.0))


def _reading_html(reading: _Reading, reach: datetime | None, lines: int) -> str:
    """
    The bar over the runs while the time range is read: what's being read, how much is done, how far back it has
    read and the lines found; a bar that shimmers until the first slice is in.
    """
    share = _read_share(reading, reach, lines)
    percent = round(share * 100)
    facts = [f"<b>{percent}%</b>"] if reach is not None or lines else ["starting…"]
    if reach is not None:
        facts.append(f"read back to {_esc(_fmt_dt(reach))}")
    if lines:
        facts.append(f"{lines:,} {'line' if lines == 1 else 'lines'} found")
    if share >= 1 and reading.then:
        facts.append(_esc(reading.then))
    waiting = " lmx-wait" if reach is None and not lines else ""
    return (f'<div class="lmx-reading"><div class="lmx-reading-t">{_esc(reading.label)}</div>'
            f'<div class="lmx-meter{waiting}"><i style="width:{percent}%"></i></div>'
            f'<div class="lmx-reading-f">{" · ".join(facts)}</div></div>')


def _picker_time(moment: datetime) -> datetime:
    """
    A moment as the From and To fields show it: in the zone reports show times in, to the minute, without its zone.
    """
    return _in_zone(moment).replace(second=0, microsecond=0, tzinfo=None)


def _span_text(start: datetime, end: datetime) -> str:
    """
    A custom range as sentences name it: '2026-10-26 09:00 – 2026-10-28 17:30', or '2026-10-28 09:00 – 17:30' within
    a day.
    """
    first, last = _fmt_dt(start), _fmt_dt(end)
    same_day = first[:10] == last[:10]
    return f"{first} – {last[11:] if same_day else last}"


def _offset(seconds: float) -> str:
    """
    How far into a run a line came: '+0.052s', '+12.4s', '+184s'.
    """
    if seconds < 10:
        return f"+{seconds:.3f}s"
    return f"+{seconds:.1f}s" if seconds < 100 else f"+{seconds:,.0f}s"


def _line_parts(message: str) -> tuple[str, str]:
    """
    A log line as the window shows it: (its text, without what Lambda writes before it (the time, request ID and
    level, which the window shows in its own columns), and its other fields as indented JSON when it's a JSON line).
    """
    text = message.rstrip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            if str(doc.get("type", "")).startswith("platform."):
                record = doc.get("record")
                return f"{doc['type']} {json.dumps(record, default=str)}" if record else str(doc["type"]), ""
            inner = doc.get("message", doc.get("msg"))
            rest = {k: v for k, v in doc.items() if k not in ("message", "msg", "level", "timestamp", "time",
                                                                "requestId", "AWSRequestId")}
            if isinstance(inner, dict) and (inner.get("errorType") or inner.get("errorMessage")):
                body = _error_text_of(inner)  # an error Lambda's runtime logged: its type, message and stack
                rest.update({k: v for k, v in inner.items() if k not in ("errorType", "errorMessage", "stack",
                                                                         "stackTrace", "trace")})
            elif inner is None:
                if not (doc.get("errorType") or doc.get("errorMessage")):
                    return text, ""
                body = _error_text_of(doc)
                rest = {k: v for k, v in rest.items() if k not in ("errorType", "errorMessage", "stack",
                                                                   "stackTrace", "trace")}
            else:
                body = inner if isinstance(inner, str) else json.dumps(inner, default=str)
            for key in ("exception", "stack_trace", "stackTrace"):  # a logger's traceback (Powertools: exception)
                if isinstance(rest.get(key), str) and rest[key].strip():
                    body = f"{body}\n{rest.pop(key).rstrip()}"
            return body, json.dumps(rest, indent=2, default=str, ensure_ascii=False) if rest else ""
    text = _LINE_PREFIX_RE.sub("", text, count=1)  # '[INFO]\t<time>\t<request ID>\t', '<time>\t<request ID>\t'
    return _LEVEL_PREFIX_RE.sub("", text, count=1), ""  # then '[ERROR] ' (Python), 'INFO\t' (Node.js)


_LEVEL_PREFIX_RE = re.compile(r"^(?:\[(?:ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\]\s*|"
                              r"(?:ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\t)")


def _error_text_of(doc: dict[str, Any]) -> str:
    """
    An error as JSON logs carry it ({"errorType", "errorMessage", "stack"}) as a traceback reads: 'TypeError:
    Cannot read ...' and then the stack's lines.
    """
    head = ": ".join(str(doc[k]) for k in ("errorType", "errorMessage") if doc.get(k))
    stack = doc.get("stack") or doc.get("stackTrace") or doc.get("trace") or []
    lines = stack.splitlines() if isinstance(stack, str) else [str(line) for line in stack]
    if lines and head and lines[0].strip().startswith(head.split(":")[0]):
        lines = lines[1:]  # Node.js puts the message first in its stack too
    return "\n".join([head, *lines]) if lines else head


def _run_summary(run: LogRun) -> tuple[str, str]:
    """
    (what a run's line says after its time and numbers, its tone): what made it fail, else the first thing it
    logged.
    """
    error = run.error
    if error is not None:
        return error[1].splitlines()[0][:300], "bad" if run.status in ("failed", "timeout") else "warn"
    for event in run.events:
        if _marker(event.message) is None:
            text = _line_parts(event.message)[0].strip()
            if text:
                return text.splitlines()[0][:300], ""
    if run.status == "outside":
        return f"{_plural(len(run.events), 'line')} outside a run", ""
    return "(it logged nothing of its own)", ""


def _run_tip(run: LogRun, opened: bool) -> str:
    """
    A run's tooltip: what happened, when, its request ID, and what a click does.
    """
    label = _RUN_STATES.get(run.status, ("", "", run.status))[2]
    return " · ".join(filter(None, [label[:1].upper() + label[1:], f"started {_stamp(run.start, zone=True)}",
                                    f"request {run.request_id}" if run.request_id else "",
                                    "click to fold its lines" if opened else "click to see its lines"]))


def _run_face(run: LogRun, timeout: int, words: list[str], today: date) -> str:
    """
    A run's line in the Logs tab: its status, when it started, its run time against the timeout, the memory it
    used, a cold start, what failed or the first thing it logged, and its request ID.
    """
    icon, tone, _ = _RUN_STATES.get(run.status, ("?", "", run.status))
    report = run.report
    took = ""
    if report is not None:
        share = min(1.0, report.duration / (timeout * 1000)) if timeout else 0.0
        fill = "bad" if share >= 0.9 else "warn" if share >= 0.7 else ""
        took = (f'<span class="rb"><i class="{fill}" style="width:{max(share * 100, 2):.1f}%"></i></span>'
                f"{_esc(human_ms(report.duration))}")
    memory = f"{report.max_memory:,} of {report.memory:,} MB" if report is not None and report.memory else ""
    cold = ""
    if report is not None and (report.init is not None or report.restore is not None):
        cold = f"cold {human_ms(report.init if report.init is not None else report.restore)}"
    elif run.cold:
        cold = "cold start"
    summary, summary_tone = _run_summary(run)
    request = run.request_id or ""
    return (f'<div class="rr {tone}"><span class="rv"></span><span class="ri">{_esc(icon)}'
            f'</span><span class="rw">{_esc(_run_clock(run.start, today))}</span><span class="rd">{took}</span>'
            f'<span class="rm">{_esc(memory)}</span><span class="rc">{_esc(cold)}</span>'
            f'<span class="rs {summary_tone}">{_marked(summary, words)}</span>'
            f'<span class="rq">{_esc(request[:8])}</span></div>')


def _run_body(run: LogRun, words: list[str], limit: int = _BODY_LINES) -> str:
    """
    An open run's lines: how far into the run each came, its level, and its text (JSON lines as their message, with
    their other fields a click away), then what its REPORT line says and its request ID to copy.
    """
    started = next((e.time for e in run.events if (_marker(e.message) or ("",))[0] == "start"), run.start)
    lines = [e for e in run.events if (_marker(e.message) or ("",))[0] not in ("start", "end", "report")]
    rows = []
    for event in lines[:limit]:
        level = line_level(event.message)
        text, fields = _line_parts(event.message)
        seconds = (event.time - started).total_seconds()
        when = "init" if seconds < 0 else _offset(seconds)
        badge = {"error": "ERROR", "warn": "WARN", "info": "INFO", "debug": "DEBUG"}.get(level, "")
        extra = (f'<details><summary>{{…}} fields</summary><span class="jf">{_json_source_html(fields)}</span>'
                 "</details>" if fields else "")
        rows.append(f'<div class="rl {level}{" hit" if event.matched else ""}"><span class="lt" title="'
                    f'{_esc(_stamp(event.time, zone=True))}">{_esc(when)}</span><span class="lv {level}">{badge}</span>'
                    f'<span class="lm">{_marked(text, words) or "&nbsp;"}{extra}</span></div>')
    if not lines:
        rows.append('<div class="rmore">It logged nothing of its own: only Lambda\'s START and REPORT lines.</div>')
    elif len(lines) > limit:
        rows.append(f'<div class="rmore">… {len(lines) - limit:,} more lines. x.ui.logs(name, request_id=...) shows '
                    "all of them.</div>")
    facts = []
    report = run.report
    if report is not None:
        facts.append(f"<span>Ran <b>{_esc(human_ms(report.duration))}</b></span>")
        facts.append(f"<span>billed {_esc(human_ms(report.billed))}</span>")
        if report.memory:
            tight = report.max_memory >= 0.9 * report.memory
            facts.append(f'<span class="{"warn" if tight else ""}">used <b>{report.max_memory:,}</b> of '
                         f"{report.memory:,} MB</span>")
        if report.init is not None:
            facts.append(f"<span>cold start {_esc(human_ms(report.init))}</span>")
        if report.restore is not None:
            facts.append(f"<span>SnapStart restore {_esc(human_ms(report.restore))}</span>")
        if report.status:
            facts.append(f'<span class="bad">status {_esc(report.status)}'
                         + (f" ({_esc(report.error_type)})" if report.error_type else "") + "</span>")
    elif run.request_id:
        facts.append("<span>No REPORT line yet: still running, or it ended after the time range</span>")
    if run.request_id:
        facts.append(f'<span>request <code title="Click to select, then copy">{_esc(run.request_id)}</code></span>')
    if run.stream:
        facts.append(f'<span title="The log stream: one execution environment">{_esc(_clip(run.stream, 70))}</span>')
    return f'<div class="rbody">{"".join(rows)}<div class="rf">{"".join(facts)}</div></div>'


class _LogsTab:
    """
    The Logs tab of LambdaExplorer: the function's runs in a time range, newest first, filtered as you type, a
    CloudWatch search on Enter, older runs on request, and Live.
    """

    def _logs_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.range_pick = self._range_pick(_RANGE_CHOICES)
        self.log_find = w.Text(placeholder="Find runs with: an order ID, KeyError, a request ID… (Enter searches "
                                           "CloudWatch)", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.log_find.observe(self._safely(self._log_typed), names="value")
        self.log_find.on_msg(self._on_enter(self._log_search))
        clear = w.Button(description="✕", tooltip="Clear the search: every run again", layout=layout(flex="0 0 auto"))
        clear.add_class("lmx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.log_find, "value", "")))
        ask = w.HBox([self.log_find, clear], layout=layout(width="auto"))
        for name in ("lmx-ask", "lmx-find"):
            ask.add_class(name)
        self.live_button = w.Button(description="Live", tooltip=f"Look for new lines every {_LIVE_SECONDS} seconds "
                                    f"and show new runs as they come (stops after {_LIVE_MINUTES} minutes)",
                                    layout=layout(width="auto"))
        self.live_button.add_class("lmx-live")
        self.live_button.on_click(self._safely(lambda _button: self._toggle_live()))
        again = w.Button(description="↻", tooltip="Read the time range again", layout=layout(width="auto"))
        again.add_class("lmx-small")
        again.on_click(self._safely(lambda _button: self._reload_logs()))
        bar = w.HBox([self.range_pick, ask, self.live_button, again], layout=layout(width="100%"))
        bar.add_class("lmx-bar")
        self.custom_bar = self._custom_range_bar()
        self.run_chips = w.HBox(layout=layout(width="100%"))
        self.run_chips.add_class("lmx-chips")
        self.run_head = w.HTML(layout=layout(width="100%"))
        self.jump_button = w.Button(layout=layout(width="auto", display="none"))
        self.jump_button.add_class("lmx-small")
        self.jump_button.on_click(self._safely(lambda _button: self._jump()))
        self.runs_box = w.VBox(layout=layout(width="100%", flex="1 1 auto"))
        self.runs_box.add_class("lmx-runs")
        self.older_button = w.Button(description="Older runs ›", tooltip="Read the lines before the oldest one read",
                                     layout=layout(width="auto", display="none"))
        self.older_button.add_class("lmx-small")
        self.older_button.on_click(self._safely(lambda _button: self._older()))
        pager = w.HBox([*self._make_pager("runs"), self.older_button], layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        page = w.VBox([bar, self.custom_bar, self.run_chips, self.run_head, self.jump_button, self.runs_box, pager])
        page.add_class("lmx-logpage")
        return page

    def _custom_range_bar(self) -> Any:
        """
        The From and To fields a custom range is picked with (a calendar and a clock each, in the zone reports show
        times in), shown while the range field says Custom range.
        """
        w, layout = self._w, self._w.Layout
        label_width = {"description_width": "initial"}
        self.range_from = w.NaiveDatetimePicker(description="From", style=label_width, layout=layout(width="auto"))
        self.range_to = w.NaiveDatetimePicker(description="To", style=label_width, layout=layout(width="auto"))
        show = w.Button(description="Show runs", tooltip="Read the runs from From to To", button_style="primary",
                        layout=layout(width="auto"))
        show.add_class("lmx-small")
        show.on_click(self._safely(lambda _button: self._apply_custom_range()))
        self.range_hint = w.HTML(layout=layout(width="auto"))
        self.range_hint.add_class("lmx-rangehint")
        bar = w.HBox([self.range_from, self.range_to, show, self.range_hint],
                     layout=layout(width="100%", display="none"))
        for name in ("lmx-bar", "lmx-custom"):
            bar.add_class(name)
        return bar

    def _range_pick(self, choices: tuple[tuple[str, str], ...] | None = None) -> Any:
        """
        A time range dropdown. The Logs, Errors and Performance tabs each have one. Logs starts at the last hour, and
        Errors and Performance at _FIRST_TAB_RANGE; once a range is picked in any of them, the three share it, so the
        tabs show the same stretch of time. The Logs tab's `choices` end in Custom range…; the others list the
        presets and the custom range picked there, if any.
        """
        w = self._w
        if choices is not None:
            options, value = list(choices), self._range_value()
        else:
            options, value = self._shared_options(), self._range_value() if self._linked else _FIRST_TAB_RANGE
        pick = w.Dropdown(options=options, value=value, layout=w.Layout(width="auto"))
        pick.add_class("lmx-range")
        pick.observe(self._safely(self._range_changed), names="value")
        self.range_picks.append(pick)
        return pick

    def _range_bar(self, label: str) -> tuple[Any, Any]:
        """
        The time range dropdown and a ↻ button that reads the tab again, for the Errors and Performance tabs.
        """
        w, layout = self._w, self._w.Layout
        pick = self._range_pick()
        again = w.Button(description="↻", tooltip=f"Read {label} again", layout=layout(width="auto"))
        again.add_class("lmx-small")
        bar = w.HBox([pick, again], layout=layout(width="100%"))
        bar.add_class("lmx-bar")
        return bar, (pick, again)

    # ------------------------------------------------------------------ logs

    def _range_value(self) -> str:
        """
        What the range fields show: a preset's value, or _CUSTOM_RANGE for any other range.
        """
        is_preset = self._until is None and self._range in {value for _, value in _RANGES}
        return self._range if is_preset else _CUSTOM_RANGE

    def _shared_options(self) -> list[tuple[str, str]]:
        """
        The Errors and Performance tabs' range field: the presets, and the custom range the Logs tab picked
        ('Since 2026-10-01 00:00', '2026-10-28 09:00 – 17:30').
        """
        options = list(_RANGES)
        if self._range_value() == _CUSTOM_RANGE:
            text = self._range_text()
            options.append((text[:1].upper() + text[1:], _CUSTOM_RANGE))
        return options

    def _set_range(self, since: Any, until: Any = None) -> None:
        """
        Picks a time range in every tab's field, which from then on share it: one of the presets, or a custom range (since= and until= as
        LambdaAnalyzer.time_range reads them), shown in the Logs tab's From and To fields. The tabs that read the
        old range read again when they're opened.
        """
        if (since, until) != (self._range, self._until) or not self._linked:
            self._asked -= {"logs", "errors", "performance"}
        self._range, self._until, self._linked = since, until, True
        for pick in self.range_picks:
            if pick is not self.range_pick:
                self._quietly(pick, options=self._shared_options(), value=self._range_value())
        if self._range_value() != _CUSTOM_RANGE:
            self._quietly(self.range_pick, value=since)
            self.custom_bar.layout.display = "none"
            return
        self._quietly(self.range_pick, value=_CUSTOM_RANGE)
        self._fill_custom_range()

    def _tab_range(self) -> tuple[Any, Any]:
        """
        The Errors and Performance tabs' range, (since, until): _FIRST_TAB_RANGE until a range is picked, then the
        one the three tabs share.
        """
        return (self._range, self._until) if self._linked else (_FIRST_TAB_RANGE, None)

    def _tab_window(self) -> tuple[datetime, datetime]:
        return LambdaAnalyzer.time_range(*self._tab_range())

    def _tab_range_text(self) -> str:
        return self._range_text() if self._linked else self._range_text(_FIRST_TAB_RANGE)

    def _range_window(self) -> tuple[datetime, datetime]:
        """
        The Logs tab's time range, (start, end), worked out now: '1h' moves with the clock, a custom range doesn't.
        """
        return LambdaAnalyzer.time_range(self._range, self._until)

    def _range_text(self, value: Any = None) -> str:
        """
        'the last hour', 'the last 3 days', 'since 2026-10-01 00:00', '2026-10-28 09:00 – 17:30'; the Logs tab's
        range without a value.
        """
        if value is None and self._until is not None:
            return _span_text(*self._range_window())
        value = self._range if value is None else value
        label = next((label for label, v in _RANGES if v == value), None)
        if label:
            return f"the {label.lower()}"
        if isinstance(value, datetime):
            return f"since {_fmt_dt(value)}"
        return f"since {value}"

    def _fill_custom_range(self) -> None:
        """
        Shows the From and To fields, set to the range the tab reads (To empty when it runs up to now).
        """
        start, end = self._range_window()
        self._quietly(self.range_from, value=_picker_time(start))
        self._quietly(self.range_to, value=_picker_time(end) if self._until is not None else None)
        self._set(self.range_hint, f"Times in {_esc(_tz_label())}; an empty To means up to now.")
        self.custom_bar.layout.display = ""

    def _apply_custom_range(self) -> None:
        """
        Show runs: reads the runs from From to To (or up to now), or says what's wrong with them.
        """
        if self.range_from.value is None:
            self._status("Pick when the range starts in From, then Show runs.", "warn")
            return
        since = parse_time(self.range_from.value)
        until = parse_time(self.range_to.value) if self.range_to.value is not None else None
        try:
            LambdaAnalyzer.time_range(since, until)
        except ValueError:
            end = "To" if until is not None else "now"
            self._status(f"From ({_fmt_dt(since)}) has to be before {end}: pick an earlier From.", "warn")
            return
        self._set_range(since, until)
        self._reload_logs()

    def _range_changed(self, change: dict[str, Any]) -> None:
        """
        A time range picked in one tab's field: every tab's field shows it, and the tab on show reads it.
        """
        if self.quiet or not change.get("new"):
            return
        if change["new"] == _CUSTOM_RANGE:
            if change.get("owner") is self.range_pick:
                self._fill_custom_range()
                self._status("Pick From and To (To empty means up to now), then Show runs.")
            return
        self._set_range(str(change["new"]))
        reload = {"logs": self._reload_logs, "errors": self._load_errors, "performance": self._load_perf}
        reload.get(self._tab, self._reload_logs)()

    def _reload_logs(self) -> None:
        if self.found is not None and self._searched and self._searched == self._log_query:
            self._log_search()
        else:
            self._load_logs()

    def _load_logs(self) -> None:
        """
        Reads the time range's newest lines, as runs (in the background).
        """
        fn = self.function
        if fn is None:
            return
        self._asked.add("logs")
        self._stop_live()
        start, end = self._range_window()
        self.logs_page, self._runs, self._run_offset = None, [], 0
        self.jump_button.layout.display = self.older_button.layout.display = "none"
        self.runs_box.children = []
        self._draw_run_chips()
        self._draw_pager("runs", 0, 0, _RUN_PAGE, "runs", None)
        self._start_reading(_Reading("logs", f"Reading what {fn.name} logged in {self._range_text()}…", start, end,
                                     _RUN_LINES))
        self._status(f"Reading what {fn.name} logged in {self._range_text()}…", busy=True, owner="logs")
        self._later("logs", lambda: self.core._log_runs(fn, start, end, limit=_RUN_LINES, progress=self._count_lines,
                                                        reached=self._note_reach),
                    self._got_logs, self._logs_failed, counting=f"Reading {fn.name}'s logs… {{n}} lines read so far",
                    owner="logs", ticking=lambda: self._draw_reading("logs"))

    def _start_reading(self, reading: _Reading) -> None:
        """
        Shows the bar a read of the time range fills as it goes (_draw_reading, as the job ticks).
        """
        self._reading, self._reach, self._counted = reading, None, 0
        self._set(self.run_head, _reading_html(reading, None, 0))

    def _count_lines(self, count: int) -> None:
        self._counted = count

    def _note_reach(self, moment: datetime) -> None:
        self._reach = moment

    def _draw_reading(self, key: str) -> None:
        """
        Redraws the bar of the read under way, if it's this job's.
        """
        reading = self._reading
        if reading is not None and reading.key == key:
            self._set(self.run_head, _reading_html(reading, self._reach, self._counted))

    def _done_reading(self, key: str) -> None:
        if self._reading is not None and self._reading.key == key:
            self._reading = None

    def _logs_failed(self, exc: BaseException) -> None:
        self._done_reading("logs")
        text = self._error_text(exc)
        self._set(self.run_head, self._html([_Note(f"Couldn't read the logs: {text}", "warn")]))
        if self._tab == "logs":
            self._status(text, "warn")

    def _got_logs(self, page: LogPage) -> None:
        self._done_reading("logs")
        self.logs_page = page
        self._runs = page.runs
        self._run_offset = 0
        self._draw_run_chips()
        self._draw_runs(renew=True)
        self._finish("logs", self._logs_line(), "warn" if page.errors else "", tabs=("logs",))

    def _logs_line(self) -> str:
        """
        The status line for the Logs tab.
        """
        fn, page = self.function, self.found if self.found is not None else self.logs_page
        if fn is None or page is None:
            return "Reading the logs…" if fn is not None else ""
        if page.errors:
            code = page.errors.get("logs", "")
            return (f"{fn.name} has no log group yet: it hasn't logged anything" if code == "ResourceNotFoundException"
                    else f"Couldn't read its logs ({_why(code, 'logs:FilterLogEvents')})")
        runs = self._found_runs if self.found is not None else self._runs
        failed = sum(1 for r in runs if r.status in ("failed", "timeout"))
        if self.found is not None:
            return (f"{_plural(len(runs), 'run')} with {self._searched!r} in {self._range_text()}"
                    + (f", {failed:,} failed" if failed else "") + " · ✕ shows every run again")
        if not runs:
            return f"Nothing logged in {self._range_text()} · pick another time range above"
        counted = (f"The newest {_plural(len(runs), 'run')} of {self._range_text()}" if page.truncated
                   else f"{_plural(len(runs), 'run')} in {self._range_text()}")
        return (counted + (f", {failed:,} failed" if failed else "")
                + " · click a run to see its lines" + (" · Older runs reads further back" if page.truncated else ""))

    def _words(self) -> list[str]:
        phrase = _phrase(self._log_query) if self._log_query else None
        return [phrase] if phrase else []

    def _log_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._log_query = str(self.log_find.value or "").strip()
        if self.found is not None and self._log_query != self._searched:
            self.found, self._found_runs = None, []
            self._draw_run_chips()
            if self.logs_page is None and "logs" not in self._tasks:  # it opened on a search: read the range now
                self._load_logs()
                return
        self._run_offset = 0
        self._draw_runs()
        if self._log_query and _phrase(self._log_query) is None:
            self._status("That's a CloudWatch Logs filter pattern: press Enter to search the time range with it.")
        elif self._log_query:
            shown = len(self._visible_runs())
            truncated = self.logs_page is not None and self.logs_page.truncated
            self._status(f"{shown:,} of {len(self._runs):,} runs read have {self._log_query!r}"
                         + (" · press Enter to search the whole time range in CloudWatch" if truncated
                            else " · Enter searches CloudWatch too"))
        else:
            self._status(self._logs_line())

    def _log_search(self) -> None:
        """
        Enter in the search box: CloudWatch searches the whole time range, and the runs with a matching line come
        back whole (in the background).
        """
        fn = self.function
        text = str(self.log_find.value or "").strip()
        if fn is None:
            return
        if not text:
            self.found, self._found_runs, self._searched = None, [], ""
            self._draw_run_chips()
            if self.logs_page is None and "logs" not in self._tasks:
                self._load_logs()
            else:
                self._draw_runs(renew=True)
            return
        start, end = self._range_window()
        self._searched = self._log_query = text
        self._stop_live()
        self._status(f"Searching what {fn.name} logged in {self._range_text()} for {text!r}, then reading each run "
                     "it's in…", busy=True, owner="logs")
        self._start_reading(_Reading("search", f"Searching {self._range_text()} for {text!r}…", start, end,
                                     _SEARCH_HITS, then="now reading each run it's in"))
        self._later("search", lambda: self.core._log_runs(fn, start, end, search=text, limit=_RUN_LINES,
                                                          progress=self._count_lines, reached=self._note_reach),
                    self._got_search, self._search_failed, ticking=lambda: self._draw_reading("search"))

    def _search_failed(self, exc: BaseException) -> None:
        self._done_reading("search")
        text = self._error_text(exc)
        self._set(self.run_head, self._html([_Note(f"The search failed: {text}", "warn")]))
        self._status(text, "warn")

    def _got_search(self, page: LogPage) -> None:
        self._done_reading("search")
        self.found = page
        self._found_runs = page.runs
        self._run_offset = 0
        if len(self._found_runs) <= 3:
            self._open_runs |= {run.key for run in self._found_runs}
        self._draw_run_chips()
        self._draw_runs(renew=True)
        self._finish("logs", self._logs_line(), "warn" if page.errors else "", tabs=("logs",))

    def _show_run(self, moment: datetime, stream: str, request_id: str | None) -> None:
        """
        Opens one run in the Logs tab, every line of it: a failed run from the Errors tab, a slow one from the
        Performance tab.
        """
        fn = self._full()
        self._show_tab("logs")
        if request_id:
            self._quietly(self.log_find, value=request_id)
            self._searched = self._log_query = request_id
        label = request_id[:8] if request_id else f"from {_stamp(moment)}"
        self._status(f"Reading run {label}…", busy=True, owner="logs")

        def done(run: LogRun | None) -> None:
            if run is None:
                self._status(f"Couldn't find run {label}'s lines around {_stamp(moment, zone=True)}: its log stream "
                             "may have been deleted, or the logs aren't kept that long.", "warn")
                return
            self.found = LogPage(fn, fn.log_group, run.start, run.end, events=run.events,
                                 pattern=f'"{run.request_id}"' if run.request_id else None)
            self._searched = self._log_query = run.request_id or self._log_query
            self._quietly(self.log_find, value=self._searched)
            self._found_runs = [run]
            self._open_runs.add(run.key)
            self._run_offset = 0
            self._draw_run_chips()
            self._draw_runs(renew=True)
            self._finish("logs", f"Run {run.request_id or label}: {_RUN_STATES.get(run.status, ('', '', run.status))[2]}"
                         + (f" in {human_ms(run.duration)}" if run.duration is not None else "")
                         + " · ✕ shows every run again", tabs=("logs",))

        self._later("search", lambda: self.core._run_around(fn, moment, stream=stream, request_id=request_id), done)

    def _source_runs(self) -> list[LogRun]:
        return self._found_runs if self.found is not None else self._runs

    def _in_run_chip(self, run: LogRun, key: str) -> bool:
        return {"": True, "failed": run.status in ("failed", "timeout"), "timeout": run.status == "timeout",
                "logged": run.status == "logged", "cold": run.cold}[key]

    def _visible_runs(self) -> list[LogRun]:
        runs = self._source_runs()
        if self._run_chip:
            runs = [r for r in runs if self._in_run_chip(r, self._run_chip)]
        if self._log_query and self.found is None:
            phrase = _phrase(self._log_query)
            if phrase:
                needle = phrase.lower()
                runs = [r for r in runs if needle in (r.request_id or "").lower()
                        or any(needle in e.message.lower() for e in r.events)]
        return runs

    def _pick_run_chip(self, key: str) -> None:
        self._run_chip = "" if key == self._run_chip else key
        self._run_offset = 0
        self._draw_run_chips()
        self._draw_runs(renew=True)

    def _draw_run_chips(self) -> None:
        runs = self._source_runs()
        chips = []
        for key, label, tone in _RUN_CHIPS:
            n = sum(1 for r in runs if self._in_run_chip(r, key))
            if key and not n:
                continue
            tip = "Show every run" if not key else f"Show only the runs that {label.lower()} (click again for every run)"
            chips.append((self._chip_button(self._run_chip_buttons, key, tone, self._pick_run_chip), f"{label} {n:,}", tip,
                          key == self._run_chip))
        self._show_chips(self.run_chips, chips if runs else [])

    def _draw_runs(self, renew: bool = False) -> None:
        visible = self._visible_runs()
        self._run_offset = min(self._run_offset, max(0, (len(visible) - 1) // _RUN_PAGE * _RUN_PAGE))
        page = visible[self._run_offset:self._run_offset + _RUN_PAGE]
        while len(self._run_rows) < len(page):
            self._run_rows.append(_RunRow(self))
        words = self._words()
        today = _in_zone(_utcnow()).date()
        timeout = self._full().timeout if self.function is not None else 0
        for row, run in zip(self._run_rows, page):
            row.run = run
            self._set(row.face, _run_face(run, timeout, words, today))
            opened = run.key in self._open_runs
            _class_if(row.box, "lmx-open", opened)
            tip = _run_tip(run, opened)
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            if opened:
                self._set(row.body, _run_body(run, words))
            row.body.layout.display = "" if opened else "none"
        children: list[Any] = [row.box for row in self._run_rows[:len(page)]]
        if not page:
            children = [self._empty("runs", self._no_runs_text())]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%", flex="1 1 auto"))
            box.add_class("lmx-runs")
            logs = self.pages["logs"]
            logs.children = [box if child is self.runs_box else child for child in logs.children]
            self.runs_box = box
        else:
            self.runs_box.children = children
        self._draw_pager("runs", self._run_offset, len(visible), _RUN_PAGE, "runs",
                         len(self._source_runs()) if (self._run_chip or self._log_query) else None)
        page_read = self.logs_page
        self.older_button.layout.display = "" if (self.found is None and page_read is not None
                                                  and page_read.truncated) else "none"
        self._draw_run_head(visible)

    def _no_runs_text(self) -> str:
        page = self.found if self.found is not None else self.logs_page
        if self.function is None:
            return "Pick a function first."
        if page is None:
            return "Reading the logs…" if "logs" in self._asked or self.found is not None else "No logs read yet."
        if page.errors:
            return "No runs to show."
        if self.found is not None:
            return (f"No run in {self._range_text()} has a line with {self._searched!r}. Pick a longer time range, or "
                    "check the spelling: CloudWatch's search is case-sensitive.")
        if self._source_runs():
            return "No run matches. Clear the search, or pick All runs above."
        if page.latest is not None:
            return f"Nothing logged in {self._range_text()}. Its newest line is from {human_age(page.latest)}."
        return (f"Nothing logged in {self._range_text()}, and nothing at all in its log group: it hasn't run, or it "
                "logs somewhere else.")

    def _draw_run_head(self, visible: list[LogRun]) -> None:
        """
        The line over the runs: how many, how many failed, how long they took, the memory they used, the cold
        starts; and what couldn't be read, or where reading stopped.
        """
        fn = self.function
        page = self.found if self.found is not None else self.logs_page
        if fn is None or page is None:
            return
        notes: list[Any] = list(self.ui._log_notes(page.errors, fn, page.log_group)) if page.errors else []
        if self.found is None and page.truncated:
            notes.append(_Note(f"These are the newest {_count(_RUN_LINES)} lines, back to "
                               f"{_stamp(page.covered_from, zone=True)}: Older runs (under the list) reads further "
                               "back."))
        elif self.found is not None and page.truncated:
            notes.append(_Note(f"CloudWatch stopped at the newest {_count(_SEARCH_HITS)} matching lines, back to "
                               f"{_stamp(page.covered_from, zone=True)}: pick a shorter time range to see older ones."))
        bits = []
        if visible:
            runs = [r for r in visible if r.request_id]
            failed = sum(1 for r in runs if r.status in ("failed", "timeout"))
            durations = [r.duration for r in runs if r.duration is not None]
            used = [(r.report.max_memory, r.report.memory) for r in runs if r.report is not None and r.report.memory]
            cold = sum(1 for r in runs if r.cold)
            sampled = self.found is None and page.truncated
            newest = "the newest " if sampled else ""
            bits.append(f"<span>{newest}<b>{len(runs):,}</b> {'run' if len(runs) == 1 else 'runs'}</span>")
            if failed:
                bits.append(f'<span class="bad"><b>{failed:,}</b> failed ({_pct(failed / len(runs))})</span>')
            if durations:
                fastest = percentile(durations, 50)
                bits.append(f"<span>median <b>{_esc(human_ms(fastest))}</b></span>")
                bits.append(f'<span class="{"warn" if max(durations) >= 0.9 * fn.timeout * 1000 else ""}">slowest '
                            f"<b>{_esc(human_ms(max(durations)))}</b> of {fn.timeout} s</span>")
            if used:
                most, size = max(used)
                bits.append(f'<span class="{"warn" if most >= 0.9 * size else ""}">memory up to <b>{most:,}</b> of '
                            f"{size:,} MB</span>")
            if cold:
                bits.append(f"<span><b>{cold:,}</b> cold {'start' if cold == 1 else 'starts'}</span>")
            where = (f"with {self._searched!r}" if self.found is not None else self._range_text())
            bits.append(f'<span class="lmx-note">Newest first, {where}; times in {_tz_label()}. Click a run to see '
                        "its lines"
                        + (", the matching ones marked" if self.found is not None or self._log_query else "")
                        + ".</span>")
        summary = f'<div class="lmx-sum">{"".join(bits)}</div>' if bits else ""
        self._set(self.run_head, summary + (self._html(notes) if notes else ""))
        latest = page.latest if self.found is None and not self._source_runs() else None
        if latest is not None:
            seconds = (_utcnow() - latest).total_seconds()
            option = next(((label, value) for label, value in _RANGES if _seconds(value) > seconds), None)
            if option is not None and option[1] != self._range:
                self.jump_button.description = f"Show {option[0].lower()} ›"
                self.jump_button.tooltip = f"Its newest line is from {human_age(latest)}"
                self._jump_to = option[1]
                self.jump_button.layout.display = ""
                return
        self.jump_button.layout.display = "none"

    def _jump(self) -> None:
        target = getattr(self, "_jump_to", None)
        if target:
            self.range_pick.value = target  # its observer reads the logs again

    def _toggle_run(self, row: _RunRow) -> None:
        run = row.run
        if run is None:
            return
        if run.key in self._open_runs:
            self._open_runs.discard(run.key)
            row.body.layout.display = "none"
        else:
            self._open_runs.add(run.key)
            self._set(row.body, _run_body(run, self._words()))
            row.body.layout.display = ""
        opened = run.key in self._open_runs
        _class_if(row.box, "lmx-open", opened)
        row.button.tooltip = _run_tip(run, opened)

    def _older(self) -> None:
        """
        Reads the lines before the oldest one read (when reading stopped at the limit), and adds their runs.
        """
        page, fn = self.logs_page, self.function
        if page is None or fn is None or not page.truncated or page.covered_from is None:
            return
        start, end = page.since, page.covered_from - timedelta(milliseconds=1)
        self._status(f"Reading older lines, before {_stamp(page.covered_from, zone=True)}…", busy=True, owner="logs")
        self.older_button.disabled = True

        def done(got: tuple[list[LogEvent], bool, datetime | None]) -> None:
            self.older_button.disabled = False
            if self.logs_page is not page:
                return
            events, truncated, covered = got
            known = {(e.stream, e.time, e.message) for e in page.events}
            page.events = [e for e in events if (e.stream, e.time, e.message) not in known] + page.events
            page.truncated, page.covered_from = truncated, covered if truncated else None
            self._runs = page.runs
            self._draw_run_chips()
            self._draw_runs()
            more = f", back to {_stamp(covered, zone=True)}" if truncated else f": every line of {self._range_text()}"
            self._finish("logs", f"Read {len(events):,} more lines{more}", tabs=("logs",))

        def failed(exc: BaseException) -> None:
            self.older_button.disabled = False
            self._status(self._error_text(exc), "warn")

        self._later("older", lambda: self.core._filter(fn.log_group, fn.region, start, end, None, _RUN_LINES), done,
                    failed)

    # ------------------------------------------------------------------ live

    def _toggle_live(self) -> None:
        if self._live:
            self._stop_live("Live is off.")
            return
        if self.function is None or self.logs_page is None:
            self._status("Live follows the logs shown: open a function's Logs tab first.", "warn")
            return
        if self._until is not None:
            self._status("Live adds the newest lines, and this range ends before now: empty To, or pick a range "
                         "like the last hour, then Live.", "warn")
            return
        if self.found is not None:  # back to every run: Live adds to them
            self._quietly(self.log_find, value="")
            self.found, self._found_runs, self._searched, self._log_query = None, [], "", ""
            self._draw_run_chips()
            self._draw_runs(renew=True)
        self._live, self._live_new, self._live_started = True, 0, time.monotonic()
        self._live_token += 1
        self.live_button.description = "Live"
        _class_if(self.live_button, "lmx-on", True)
        self._status(f"Live: looking for new lines every {_LIVE_SECONDS} seconds; new runs show at the top.", "live")
        loop = _running_loop()
        if loop is None:  # a script or the tests: one look now
            self._poll_now()
            return
        token = self._live_token
        self._tasks["live"] = loop.create_task(self._live_loop(token))

    def _stop_live(self, text: str = "", level: str = "") -> None:
        was = self._live
        self._live = False
        self._live_token += 1
        if hasattr(self, "live_button"):
            _class_if(self.live_button, "lmx-on", False)
        if was and text:
            self._status(text, level)

    def _poll_work(self) -> tuple[LogPage, Callable[[], list[LogEvent]]] | None:
        page, fn = self.logs_page, self.function
        if page is None or fn is None:
            return None
        newest = max((e.time for e in page.events), default=page.until)
        start = min(newest, page.until) - timedelta(seconds=2)  # a little overlap: lines can share a millisecond
        return page, lambda: self.core._filter(fn.log_group, fn.region, start, _utcnow(), None, 2_000)[0]

    def _poll_now(self) -> None:
        polled = self._poll_work()
        if polled is not None:
            page, work = polled
            self._merge_live(page, work())

    async def _live_loop(self, token: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._live and self._live_token == token:
                await asyncio.sleep(_LIVE_SECONDS)
                if not (self._live and self._live_token == token):
                    return
                if time.monotonic() - self._live_started > _LIVE_MINUTES * 60:
                    self._stop_live(f"Live stopped after {_LIVE_MINUTES} minutes: click Live to go on.")
                    return
                polled = self._poll_work()
                if polled is None:
                    continue
                page, work = polled
                try:
                    events = await loop.run_in_executor(self._workers(), work)
                except Exception as exc:
                    if self._live_token == token:
                        self._stop_live(f"Live stopped: {self._error_text(exc)}", "warn")
                    return
                if self._live and self._live_token == token:
                    self._merge_live(page, events)
        finally:
            if self._tasks.get("live") is asyncio.current_task():
                del self._tasks["live"]

    def _merge_live(self, page: LogPage, events: list[LogEvent]) -> None:
        if self.logs_page is not page:
            return
        known = {(e.stream, e.time, e.message) for e in page.events}
        new = [e for e in events if (e.stream, e.time, e.message) not in known]
        page.until = _utcnow()
        clock = _in_zone(page.until)
        checked = f"checked {clock:%H:%M:%S} {_zone_name(clock)}"
        if new:
            before = {run.key for run in self._runs}
            page.events = sorted(page.events + new, key=lambda e: e.time)[-20_000:]
            self._runs = page.runs
            self._live_new += sum(1 for run in self._runs if run.key not in before and run.request_id)
            if self.found is None:
                self._draw_run_chips()
                self._draw_runs()
        if self._live:
            self._status(f"Live · {_plural(self._live_new, 'new run')} since it started · {checked} · every "
                         f"{_LIVE_SECONDS} seconds", "live")
