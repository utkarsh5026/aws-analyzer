"""
logs()'s report, drawn like a log viewer: a function's runs newest first, each a line you open to its log lines
(oldest first, each with its time, level and text, JSON fields and tracebacks laid out), with chips that keep the
failed runs, the cold starts or the runs that logged an error, a switch that keeps only errors and warnings, Expand all,
and the search's words marked. Also what the explorer's Logs tab shares with it: a run's status, start and summary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..._kit.blocks import _draws, _Report
from ..._kit.fmt import _count, _fmt_dt, _in_zone, _plural, _tz_label, _utcnow
from ..._kit.highlight import _json_source_html
from ..._kit.render import _prose
from ..._kit.text import _clip, _esc, _marked

from ..helpers import human_ms
from ..models import LogEvent
from ..analysis.logs import LogRun, _line_parts, _marker, line_level, readable_line, split_runs
from ..analysis.word_search import SearchError, WordSearch, word_search
from ..analysis.runs import LoggedEvent, logged_event

_RUN_STATES = {
    "ok": ("✓", "ok", "ran"), "failed": ("✕", "bad", "failed"), "timeout": ("⏱", "bad", "timed out"),
    "logged": ("!", "warn", "logged an error"), "running": ("•", "run", "no REPORT line yet: still running, or it "
                                                                         "ends after the time range"),
    "outside": ("·", "", "lines that belong to no run in the time range"),
}

_STATUS_WORDS = {"ok": "OK", "failed": "Failed", "timeout": "Timed out", "logged": "Error logged",
                 "running": "Running", "outside": "No run"}

_LEVEL_TAGS = {"error": "ERROR", "warn": "WARN", "info": "INFO", "debug": "DEBUG"}

_RUN_MARKERS = ("start", "end", "report")

_LINES_PER_RUN = 1_000

_OPEN_EVENT_LINES = 24

_TIGHT_SHARE = 0.9

_CLOSE_SHARE = 0.7


@dataclass(frozen=True)
class _Chip:
    """
    One of the chips over the runs: the CSS class its radio button carries, its label, how many runs it keeps and
    its tone.
    """

    key: str
    label: str
    count: int
    tone: str = ""


@dataclass
class _LogRuns:
    """
    A function's runs as a log viewer shows them, newest first: `opened` holds the keys (LogRun.key) of the runs
    drawn open, `words` are marked wherever they're found, `timeout` (seconds) scales each run-time bar, and
    `search_hint` is a sentence under the chips saying how to search every line (a call, drawn as code);
    `searched` adds a switch that keeps only the lines the search found.
    """

    runs: list[LogRun]
    timeout: int = 0
    words: list[str] = field(default_factory=list)
    opened: set[str] = field(default_factory=set)
    title: str = ""
    search_hint: str = ""
    searched: bool = False


def _runs_holding(events: list[LogEvent], shown: list[LogEvent], request_id: str | None) -> list[LogRun]:
    """
    The runs logs() draws, newest first: the one run asked for by request ID, else every run that holds one of the
    lines shown, whole (its START and REPORT lines too, which say how it went).
    """
    runs = split_runs(events)
    if request_id:
        return [run for run in runs if run.request_id == request_id] or runs
    holding = {id(event) for event in shown}
    return [run for run in runs if any(id(event) in holding for event in run.events)]


def _marked_words(phrase: str | None, words: WordSearch | None) -> list[str]:
    """
    The words logs() marks in the lines it shows: what the search wanted.
    """
    if words is not None:
        return words.wanted
    return [phrase] if phrase else []


_OPEN_SEARCHED_RUNS = 3

def _opened_runs(runs: list[LogRun], *, request_id: str | None, search: str | None) -> set[str]:
    """
    The runs logs() draws open: the run asked for, the first runs a search found, else the newest run that failed or
    logged an error.
    """
    if request_id or search:
        return {run.key for run in runs[:_OPEN_SEARCHED_RUNS]}
    troubled = next((run for run in runs if run.failures or run.status in ("failed", "timeout")), None)
    return {troubled.key} if troubled is not None else set()


def _runs_found(runs: list[LogRun], text: str) -> tuple[list[LogRun], list[str]]:
    """
    The runs with a line holding `text`, in any case, and the words to mark: words joined with AND, OR and NOT
    ('KeyError OR Timeout') hold across a run's lines, anything else is one phrase. An empty search keeps every run.
    """
    text = text.strip()
    if not text:
        return runs, []
    try:
        words = word_search(text)
    except SearchError:
        words = None
    if words is not None:
        kept = [run for run in runs if words.matches((e.message for e in run.events), ignore_case=True)]
        return kept, words.wanted
    needle = text.lower()
    kept = [run for run in runs if any(needle in event.message.lower() for event in run.events)]
    return kept, [text]


def _line_mentions(message: str, words: list[str]) -> bool:
    lowered = message.lower()
    return any(word.lower() in lowered for word in words)


def _short_day(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}"


def _run_clock(moment: datetime, today: date) -> str:
    """
    A run's start in the zone reports show times in: '14:02:31' today, 'Oct 5 14:02' another day.
    """
    moment = _in_zone(moment)
    return moment.strftime("%H:%M:%S") if moment.date() == today else f"{_short_day(moment)} {moment:%H:%M}"


def _line_clock(moment: datetime) -> str:
    """
    A log line's time to the millisecond, in the zone reports show times in: '14:02:31.052'.
    """
    return _in_zone(moment).strftime("%H:%M:%S.%f")[:-3]


def _offset(seconds: float) -> str:
    """
    How far into a run a line came: '+0.052s', '+12.4s', '+184s'.
    """
    if seconds < 10:
        return f"+{seconds:.3f}s"
    return f"+{seconds:.1f}s" if seconds < 100 else f"+{seconds:,.0f}s"


def _run_summary(run: LogRun) -> tuple[str, str]:
    """
    (what a run's line says after its time and numbers, its tone): what made it fail, else the first thing it
    logged.
    """
    error = run.error
    if error is not None:
        return error[1].splitlines()[0][:300], "bad" if run.status in ("failed", "timeout") else "warn"
    logged = logged_event(run)
    for event in run.events:
        if _marker(event.message) is None and (logged is None or event is not logged.line):
            text = _line_parts(event.message)[0].strip()
            if text:
                return text.splitlines()[0][:300], ""
    if run.status == "outside":
        return f"{_plural(len(run.events), 'line')} outside a run", ""
    return "(it logged nothing of its own)", ""


def _run_started(run: LogRun) -> datetime:
    """
    When a run's START line came, else its first line: what each line's offset is counted from.
    """
    return next((e.time for e in run.events if (_marker(e.message) or ("",))[0] == "start"), run.start)


def _lines_of_its_own(run: LogRun, logged: LoggedEvent | None) -> list[Any]:
    """
    A run's lines without Lambda's START, END and REPORT lines (the run's line and its footer say what they do) and
    without the line that logged its event (drawn on its own above them).
    """
    return [e for e in run.events if (_marker(e.message) or ("",))[0] not in _RUN_MARKERS
            and (logged is None or e is not logged.line)]


def _run_classes(run: LogRun) -> str:
    """
    The classes the chips' rules read: its status, and whether it was a cold start or logged an error.
    """
    classes = [f"lgr s-{run.status}"]
    if run.cold:
        classes.append("cold")
    if run.failures:
        classes.append("err")
    return " ".join(classes)


def _run_chips(runs: list[LogRun]) -> list[_Chip]:
    """
    The chips over the runs, each only when some run has it: all of them, the failed (or timed out), the ones that
    logged an error, the cold starts and the ones that went well.
    """
    failed = sum(1 for run in runs if run.status in ("failed", "timeout"))
    errors = sum(1 for run in runs if run.failures)
    cold = sum(1 for run in runs if run.cold)
    went_well = sum(1 for run in runs if run.status == "ok")
    chips = [_Chip("all", "All runs", len(runs))]
    candidates = [_Chip("failed", "Failed", failed, "bad"), _Chip("err", "Errors logged", errors, "warn"),
                  _Chip("cold", "Cold starts", cold), _Chip("ok", "OK", went_well, "ok")]
    for chip in candidates:
        says_nothing_new = chip.key == "ok" and chip.count == len(runs)
        if chip.count and not says_nothing_new:
            chips.append(chip)
    return chips


def _duration_html(run: LogRun, timeout: int) -> str:
    """
    A run's time as a bar against its timeout (amber past 70%, red past 90%) and in words.
    """
    report = run.report
    if report is None:
        return '<span class="lgdim">—</span>'
    share = min(1.0, report.duration / (timeout * 1000)) if timeout else 0.0
    fill = "bad" if share >= _TIGHT_SHARE else "warn" if share >= _CLOSE_SHARE else ""
    tip = f"{human_ms(report.duration)} of its {timeout} s timeout" if timeout else human_ms(report.duration)
    return (f'<span class="lgbar" title="{_esc(tip)}"><i class="{fill}" style="width:{max(share * 100, 2):.1f}%">'
            f"</i></span>{_esc(human_ms(report.duration))}")


def _memory_text(run: LogRun) -> str:
    report = run.report
    if report is None or not report.memory:
        return ""
    return f"{report.max_memory:,} / {report.memory:,} MB"


def _cold_html(run: LogRun) -> str:
    report = run.report
    if report is not None and (report.init is not None or report.restore is not None):
        took = report.init if report.init is not None else report.restore
        return f'<span class="lgcold" title="A cold start: the runtime started first">❄ {_esc(human_ms(took))}</span>'
    return '<span class="lgcold">❄ cold</span>' if run.cold else ""


def _run_line_html(run: LogRun, block: _LogRuns, today: date) -> str:
    """
    A run's line: an arrow, its status, when it started, its run time against the timeout, the memory it used, a
    cold start, what failed or the first thing it logged, and the start of its request ID.
    """
    icon, tone, meaning = _RUN_STATES.get(run.status, ("?", "", run.status))
    summary, summary_tone = _run_summary(run)
    request = run.request_id or ""
    status = (f'<span class="lgst {tone}" title="{_esc(meaning[:1].upper() + meaning[1:])}"><i>{_esc(icon)}</i>'
              f"{_esc(_STATUS_WORDS.get(run.status, run.status))}</span>")
    return (f'<summary class="lgf"><span class="lgchev"></span>{status}'
            f'<span class="lgw" title="{_esc(_fmt_dt(run.start, seconds=True, zone=True))}">'
            f"{_esc(_run_clock(run.start, today))}</span>"
            f'<span class="lgd">{_duration_html(run, block.timeout)}</span>'
            f'<span class="lgm">{_esc(_memory_text(run))}</span>'
            f'<span class="lgk">{_cold_html(run)}</span>'
            f'<span class="lgs {summary_tone}">{_marked(summary, block.words)}</span>'
            f'<span class="lgq" title="{_esc(request)}">{_esc(request[:8])}</span></summary>')


def _log_line_html(event: Any, started: datetime, words: list[str]) -> str:
    """
    One log line: its time (and how far into the run, on hover), its level and its text, a JSON line's other fields
    folded under it.
    """
    level = line_level(event.message)
    text, fields = _line_parts(event.message)
    seconds = (event.time - started).total_seconds()
    into = "during start-up" if seconds < 0 else f"{_offset(seconds)} into the run"
    tag = _LEVEL_TAGS.get(level, "")
    folded = (f'<details class="lgj"><summary>{{…}} fields</summary>'
              f'<pre class="lgpre">{_json_source_html(fields)}</pre></details>' if fields else "")
    hit = " hit" if event.matched else ""
    return (f'<div class="lgln {level}{hit}"><span class="lgt" title="{_esc(_fmt_dt(event.time, seconds=True))} · '
            f'{_esc(into)}">{_esc(_line_clock(event.time))}</span><span class="lgl {level}">{tag}</span>'
            f'<span class="lgx">{_marked(text, words) or "&nbsp;"}{folded}</span></div>')


def _called_with_html(run: LogRun, logged: LoggedEvent | None) -> str:
    """
    The event a run was called with, when it logged it, as JSON to fold: open when it's short.
    """
    if logged is None:
        return ""
    text = json.dumps(logged.event, indent=2, default=str, ensure_ascii=False)
    shown = " open" if text.count("\n") < _OPEN_EVENT_LINES else ""
    return (f'<details class="lgev"{shown}><summary>Event <span>what it was called with</span></summary>'
            f'<pre class="lgpre">{_json_source_html(text)}</pre></details>')


def _footer_html(run: LogRun) -> str:
    """
    Under a run's lines: what its REPORT line says, its request ID to copy and its log stream.
    """
    facts = []
    report = run.report
    if report is not None:
        facts.append(f"<span>Duration <b>{_esc(human_ms(report.duration))}</b></span>")
        facts.append(f"<span>Billed <b>{_esc(human_ms(report.billed))}</b></span>")
        if report.memory:
            tight = " warn" if report.max_memory >= _TIGHT_SHARE * report.memory else ""
            facts.append(f'<span class="{tight.strip()}">Memory <b>{report.max_memory:,}</b> of {report.memory:,} MB'
                         "</span>")
        if report.init is not None:
            facts.append(f"<span>Init <b>{_esc(human_ms(report.init))}</b></span>")
        if report.restore is not None:
            facts.append(f"<span>SnapStart restore <b>{_esc(human_ms(report.restore))}</b></span>")
        if report.status:
            error = f" ({_esc(report.error_type)})" if report.error_type else ""
            facts.append(f'<span class="bad">Status <b>{_esc(report.status)}</b>{error}</span>')
    elif run.request_id:
        facts.append("<span>No REPORT line yet: still running, or it ended after the time range</span>")
    if run.request_id:
        facts.append(f'<span>Request ID <code class="lgid" title="Click to select it">{_esc(run.request_id)}</code>'
                     "</span>")
    if run.stream:
        facts.append(f'<span class="lgdim" title="The log stream: one execution environment">'
                     f"{_esc(_clip(run.stream, 80))}</span>")
    return f'<div class="lgfoot">{"".join(facts)}</div>'


def _body_html(run: LogRun, words: list[str]) -> str:
    """
    An open run: the event it was called with, its lines oldest first, then its REPORT line's numbers.
    """
    logged = logged_event(run)
    lines = _lines_of_its_own(run, logged)
    started = _run_started(run)
    rows = [_log_line_html(event, started, words) for event in lines[:_LINES_PER_RUN]]
    if not lines:
        rows.append('<div class="lgempty">It logged nothing of its own: only Lambda\'s START and REPORT lines.</div>')
    elif len(lines) > _LINES_PER_RUN:
        rows.append(f'<div class="lgempty">… {_count(len(lines) - _LINES_PER_RUN)} more lines</div>')
    return f'<div class="lgb">{_called_with_html(run, logged)}<div class="lglines">{"".join(rows)}</div>{_footer_html(run)}</div>'


def _run_chips_html(chips: list[_Chip]) -> str:
    """
    The chips as radio buttons in one group, All ticked; the stylesheet's rules hide the runs a ticked chip doesn't
    keep.
    """
    out = []
    for chip in chips:
        checked = " checked" if chip.key == "all" else ""
        out.append(f'<label class="lgc {chip.tone}"><input type="radio" name="lgs" class="lgf-{chip.key}"{checked}>'
                   f"<span>{_esc(chip.label)} <b>{_count(chip.count)}</b></span></label>")
    return f'<span class="lgchips">{"".join(out)}</span>'


_HITS_TOGGLE = '<label class="lgtg"><input type="checkbox" class="lg-hits"><span>Matching lines only</span></label>'

_TOGGLES = ('<label class="lgtg"><input type="checkbox" class="lg-only"><span>Errors &amp; warnings only</span></label>'
            '<label class="lgtg"><input type="checkbox" class="lg-open"><span>Expand all</span></label>')

def _head_html() -> str:
    """
    The header over the runs' lines, their start in the zone reports show times in.
    """
    return ('<div class="lghd"><span></span><span>Status</span>'
            f"<span>Started ({_esc(_tz_label())})</span><span>Run time</span><span>Memory used</span><span></span>"
            "<span>What it logged</span><span>Request</span></div>")


@_draws(_LogRuns, "html")
def _log_runs_html(block: _LogRuns, report: _Report) -> str:
    """
    The runs as a log viewer: chips and switches over them (radio buttons and checkboxes in a form, read by the
    stylesheet's :has() rules, so no script), then a line per run that opens to its lines. Without the stylesheet
    the controls stay hidden and every run reads as a plain list.
    """
    if not block.runs:
        return ""
    today = _in_zone(_utcnow()).date()
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    hint = f'<div class="lghint">{_prose(block.search_hint)}</div>' if block.search_hint else ""
    hits = _HITS_TOGGLE if block.searched else ""
    tools = f'<div class="lgtools" hidden="hidden">{_run_chips_html(_run_chips(block.runs))}{hits}{_TOGGLES}</div>'
    runs = []
    for run in block.runs:
        opened = " open" if run.key in block.opened else ""
        runs.append(f'<details class="{_run_classes(run)}"{opened}>{_run_line_html(run, block, today)}'
                    f"{_body_html(run, block.words)}</details>")
    return (f'<form class="lgv" method="dialog" autocomplete="off">{title}{tools}{hint}'
            f'<div class="lglist">{_head_html()}{"".join(runs)}</div></form>')


def _text_face(run: LogRun, block: _LogRuns, today: date) -> str:
    icon, _, _ = _RUN_STATES.get(run.status, ("?", "", run.status))
    report = run.report
    took = human_ms(report.duration) if report is not None else "-"
    cold = " cold" if run.cold else ""
    summary = _run_summary(run)[0]
    status = _STATUS_WORDS.get(run.status, run.status)
    return (f"{icon} {status:<12} {_run_clock(run.start, today):<15} {took:>9}  {_memory_text(run):<16}{cold:<5}  "
            f"{_clip(summary, 80)}  {(run.request_id or '')[:8]}")


def _text_lines_of(run: LogRun) -> list[str]:
    logged = logged_event(run)
    out = []
    if logged is not None:
        event = json.dumps(logged.event, default=str, ensure_ascii=False)
        out.append(f"    event   {_clip(event, 160)}")
    for event in _lines_of_its_own(run, logged)[:_LINES_PER_RUN]:
        level = _LEVEL_TAGS.get(line_level(event.message), "")
        text = _line_parts(event.message)[0].rstrip() or ""
        first, *rest = text.splitlines() or [""]
        out.append(f"    {_line_clock(event.time)}  {level:<5}  {first}")
        out += [f"    {'':12}  {'':5}  {more}" for more in rest]
    report_line = next((e for e in run.events if (_marker(e.message) or ("",))[0] == "report"), None)
    if report_line is not None:
        out.append(f"    {_line_clock(report_line.time)}         {readable_line(report_line.message)}")
    return out


@_draws(_LogRuns, "text")
def _log_runs_lines(block: _LogRuns, report: _Report) -> list[str]:
    """
    The runs in text: a line each, newest first, with its lines indented under it.
    """
    today = _in_zone(_utcnow()).date()
    out = [f"-- {block.title} --"] if block.title else []
    if block.search_hint:
        out.append(block.search_hint)
    out.append(f"  {'Status':<12} {'Started (' + _tz_label() + ')':<15} {'Run time':>9}  {'Memory used':<21}  "
               "What it logged")
    for run in block.runs:
        out.append(_text_face(run, block, today))
        out += _text_lines_of(run)
    return out
