"""
What errors() draws about its causes: the verdict at the top (_Verdict), the chart of calls and failed calls with a
row of dots per cause under it (_ErrorChart), and a case card per cause with what it means, where it fails and what to
do (_Causes), with their drawings, and the function that builds them from an ErrorReport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from ..._kit.blocks import _Report, _Table, _draws
from ..._kit.fmt import _fmt_dt, _in_zone, _tz_label, _utcnow, human_age
from ..._kit.render import _prose
from ..._kit.text import _clip, _esc

from ..models import ErrorGroup, ErrorReport
from ..analysis.causes import (
    NEW, STOPPED, CauseAdvice, CauseState, ChartColumns, cause_advice, cause_state, chart_columns, errors_verdict,
    failure_pattern,
)
from ..analysis.errors import failing_frame
from ..analysis.logs import readable_line


_AXIS_LABELS = 5

_NOW_WITHIN = timedelta(minutes=5)

_MESSAGE_CHARS = 110

_EXAMPLE_CHARS = 4000

_LANE_NAME_CHARS = 22

_LINE_CHARS = 300

_SPARKS = " ▁▂▃▄▅▆▇█"

_SMALLEST_BAR = 2.0

_STATE_TONES = {STOPPED: "ok", NEW: "bad"}



@dataclass
class _Verdict:
    """
    The report's answer in one sentence, the numbers behind it under it, in a band coloured by `tone`.
    """

    headline: str
    detail: str = ""
    tone: str = "bad"


@_draws(_Verdict, "html")
def _verdict_html(block: _Verdict, report: _Report) -> str:
    detail = f'<span class="vd-d">{_prose(block.detail)}</span>' if block.detail else ""
    return f'<div class="vd {_esc(block.tone)}"><b>{_esc(block.headline)}</b>{detail}</div>'


@_draws(_Verdict, "text")
def _verdict_lines(block: _Verdict, report: _Report) -> list[str]:
    mark = " (!)" if block.tone == "bad" else ""
    return ["", f">> {block.headline}{mark}"] + ([f"   {block.detail}"] if block.detail else [])



@dataclass
class _Lane:
    """
    One cause's row of dots in the chart: its count per column, and whether it has stopped (drawn faded).
    """

    name: str
    counts: list[int]
    stopped: bool = False


@dataclass
class _ErrorChart:
    """
    Calls (grey) and failed calls (red, on their own scale, `most_failed` the tallest) per column, the axis under them,
    and a row of dots per cause in the same columns, with the sentence that reads it above. Text draws each as a line
    of sparks or dots.
    """

    says: str = ""
    axis: list[str] = field(default_factory=list)
    calls: list[float] | None = None
    failed: list[float] | None = None
    unit: str = "hour"
    lanes: list[_Lane] = field(default_factory=list)
    title: str = ""

    @property
    def most_failed(self) -> float:
        return max(self.failed or [0.0])


def _axis_labels(columns: ChartColumns, until: datetime) -> list[str]:
    """
    _AXIS_LABELS times along the columns in the reports' zone, the last one 'now' when the window ends now.
    """
    first, last = columns.starts[0], columns.starts[-1] + timedelta(seconds=columns.period)
    step = (last - first) / (_AXIS_LABELS - 1)
    shape = "%H:%M" if columns.unit == "hour" else "%b %d"
    labels = [_in_zone(first + step * i).strftime(shape) for i in range(_AXIS_LABELS)]
    if abs(_utcnow() - until) <= _NOW_WITHIN:
        labels[-1] = "now"
    return labels


def _height(value: float, most: float) -> float:
    if not value or not most:
        return 0.0
    return max(_SMALLEST_BAR, value / most * 100)


def _bars_html(block: _ErrorChart) -> str:
    if block.calls is None or block.failed is None or not block.most_failed:
        return ""
    most_calls = max(block.calls) or 1.0
    columns = "".join(
        f'<i title="{calls:,.0f} calls, {failed:,.0f} failed"><s style="height:{_height(calls, most_calls):.1f}%"></s>'
        f'<b style="height:{_height(failed, block.most_failed):.1f}%"></b></i>'
        for calls, failed in zip(block.calls, block.failed)
    )
    legend = (f'<div class="ec-key"><span><i class="g"></i>calls</span><span><i class="r"></i>failed calls, on their '
              f"own scale: at most {block.most_failed:,.0f} in one {block.unit}</span></div>")
    return f'{legend}<div class="ec-row"><span></span><div class="ec-bars">{columns}</div></div>'


def _lane_html(lane: _Lane) -> str:
    cells = "".join("<i><b></b></i>" if count else "<i></i>" for count in lane.counts)
    faded = " stopped" if lane.stopped else ""
    name = _esc(_clip(lane.name, _LANE_NAME_CHARS))
    return (f'<div class="ec-row ec-lane{faded}"><span title="{_esc(lane.name)}">{name}</span>'
            f'<div class="ec-dots">{cells}</div></div>')


@_draws(_ErrorChart, "html")
def _chart_html(block: _ErrorChart, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    says = f'<p class="ec-says">{_prose(block.says)}</p>' if block.says else ""
    ticks = "".join(f"<span>{_esc(label)}</span>" for label in block.axis)
    axis = f'<div class="ec-row"><span></span><div class="ec-axis">{ticks}</div></div>'
    lanes = "".join(_lane_html(lane) for lane in block.lanes)
    return f'{title}<div class="ec">{says}{_bars_html(block)}{axis}{lanes}</div>'


def _sparks(values: list[float]) -> str:
    most = max(values, default=0) or 1
    return "".join(_SPARKS[min(8, max(1, round(value / most * 8)))] if value else " " for value in values)


def _text_axis(labels: list[str], width: int) -> str:
    row = [" "] * (width + max((len(label) for label in labels), default=0))
    for i, label in enumerate(labels):
        start = round(i * (width - 1) / max(1, len(labels) - 1))
        row[start:start + len(label)] = label
    return "".join(row).rstrip()


@_draws(_ErrorChart, "text")
def _chart_lines(block: _ErrorChart, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    if block.says:
        out.append(block.says)
    pad = _LANE_NAME_CHARS + 2
    if block.calls is not None and block.failed is not None and block.most_failed:
        out.append(f"{'calls':<{pad}}{_sparks(block.calls)}")
        most = f"   (at most {block.most_failed:,.0f} in one {block.unit})"
        out.append(f"{'failed calls':<{pad}}{_sparks(block.failed)}{most}")
    for lane in block.lanes:
        dots = "".join("•" if count else "·" for count in lane.counts)
        out.append(f"{_clip(lane.name, _LANE_NAME_CHARS):<{pad}}{dots}" + ("   (stopped)" if lane.stopped else ""))
    width = len(block.lanes[0].counts) if block.lanes else len(block.calls or [])
    out.append(" " * pad + _text_axis(block.axis, width))
    return out



@dataclass
class _Cause:
    """
    One cause as its card draws it: what it is, how often (`share` of every error line), whether it's still failing,
    what it means, where its own code fails, what to do, and the call that reads its newest run.
    """

    number: int
    kind: str
    message: str
    count: int
    share: float
    state: str
    state_says: str
    means: str = ""
    steps: list[str] = field(default_factory=list)
    where: str = ""
    versions: str = ""
    run_call: str = ""
    example: str = ""
    first: str = ""
    share_of: str = "error lines"


@dataclass
class _Causes:
    """
    A function's errors as a card per cause, still failing ones first: its message and count, a tag saying whether
    it's new, still failing or stopped, then what it means and where it fails beside what to do. A few lines per
    cause in text.
    """

    causes: list[_Cause] = field(default_factory=list)
    title: str = ""


def _failed_at(group: ErrorGroup) -> str:
    """
    'etl.py, line 48, in transform()': where its traceback says the function's own code failed, '' when it names none.
    """
    frame = failing_frame(group.example)
    if frame is None:
        return ""
    place = f"{frame.path}, line {frame.line}" + (f", in {frame.function}()" if frame.function else "")
    return place + (" (a library it bundles)" if frame.library else "")


def _versions(group: ErrorGroup) -> str:
    """
    'versions 3 and $LATEST' when versions other than $LATEST logged it, else ''.
    """
    if not group.versions or group.versions == ["$LATEST"]:
        return ""
    if len(group.versions) == 1:
        return f"version {group.versions[0]}"
    return "versions " + ", ".join(group.versions[:-1]) + f" and {group.versions[-1]}"


def _example(group: ErrorGroup) -> str:
    """
    The full message, when the card's headline doesn't show all of it (a traceback, or a long line); '' otherwise.
    """
    example = group.example.strip()
    says_more = len(example.splitlines()) > 1 or len(example) > _MESSAGE_CHARS
    return _clip(example, _EXAMPLE_CHARS) if says_more else ""


def _cause_headline(group: ErrorGroup) -> str:
    first_line = group.message.strip().splitlines()[0] if group.message.strip() else group.kind
    return _clip(first_line, _MESSAGE_CHARS)


@dataclass
class _CaseFacts:
    """
    What errors() worked out about one cause before it's drawn: its group, its state and the advice for it.
    """

    group: ErrorGroup
    state: CauseState
    advice: CauseAdvice


def _cause(number: int, facts: _CaseFacts, *, until: datetime, lines: int, run_call: Callable[[str], str]) -> _Cause:
    """
    One cause's card, its count a share of `lines` error lines.
    """
    group = facts.group
    newest = group.request_ids[0] if group.request_ids else ""
    return _Cause(
        number, group.kind, _cause_headline(group), group.count, group.count / lines if lines else 0.0,
        facts.state.state, facts.state.says, means=facts.advice.means, steps=list(facts.advice.steps),
        where=_failed_at(group), versions=_versions(group), run_call=run_call(newest) if newest else "",
        example=_example(group), first=human_age(group.first, until),
    )


def _shows_kind(cause: _Cause) -> bool:
    """
    Whether the card names the kind beside the message: when the message doesn't start with it already.
    """
    return not cause.message.startswith(cause.kind)


def _steps_html(cause: _Cause) -> str:
    steps = list(cause.steps)
    if cause.run_call:
        steps.append(f"See its newest run: {cause.run_call}")
    return "<ol>" + "".join(f"<li>{_prose(step)}</li>" for step in steps) + "</ol>"


def _cause_top_html(cause: _Cause, tone: str) -> str:
    kind = f'<span class="cz-kind">{_esc(cause.kind)}</span>' if _shows_kind(cause) else ""
    meta = f"first {cause.first}" + (f" · {cause.versions}" if cause.versions else "")
    times = "time" if cause.count == 1 else "times"
    return (f'<div class="cz-top"><span class="cz-n">{cause.number}</span>'
            f'<div class="cz-what">{kind}<code>{_esc(cause.message)}</code><div class="cz-tags">'
            f'<span class="cz-tag {tone}">{_esc(cause.state_says)}</span><span class="cz-tag">{_esc(meta)}</span>'
            f'</div></div><div class="cz-count"><b>{cause.count:,}</b> {times}'
            f"<span>{cause.share:.0%} of {_esc(cause.share_of)}</span></div></div>")


def _cause_html(cause: _Cause) -> str:
    tone = _STATE_TONES.get(cause.state, "warn")
    where = f'<div class="cz-k">Where it fails</div><code>{_esc(cause.where)}</code>' if cause.where else ""
    full = (f'<details class="cz-full"><summary>The whole message</summary><pre>{_esc(cause.example)}</pre>'
            "</details>" if cause.example else "")
    return (f'<div class="cz-c {tone}">{_cause_top_html(cause, tone)}<div class="cz-body"><div>'
            f'<div class="cz-k">What it means</div><p>{_prose(cause.means)}</p>{where}{full}</div>'
            f'<div><div class="cz-k">What to do</div>{_steps_html(cause)}</div></div></div>')


@_draws(_Causes, "html")
def _causes_html(block: _Causes, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return f'{title}<div class="cz">{"".join(_cause_html(cause) for cause in block.causes)}</div>'


@_draws(_Causes, "text")
def _causes_lines(block: _Causes, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    for cause in block.causes:
        mark = "" if cause.state == STOPPED else " (!)"
        times = "time" if cause.count == 1 else "times"
        out.append(f"{cause.number:>2}  {cause.message}   {cause.count:,} {times} ({cause.share:.0%}){mark}")
        details = [cause.state_says, f"first {cause.first}"] + ([cause.versions] if cause.versions else [])
        out.append("    " + " · ".join(details))
        out.append(f"    means: {cause.means}")
        if cause.where:
            out.append(f"    where: {cause.where}")
        for step in cause.steps:
            out.append(f"    do:    {step}")
        if cause.run_call:
            out.append(f"    run:   {cause.run_call}")
    return out



def _case_facts(report: ErrorReport) -> list[_CaseFacts]:
    """
    Each cause with its state and advice, the ones still failing first, then by how often.
    """
    facts = [_CaseFacts(group, cause_state(group, since=report.since, until=report.until, metrics=report.metrics),
                        cause_advice(group, report.function)) for group in report.groups]
    return sorted(facts, key=lambda fact: fact.state.state == STOPPED)


def _newest_lines(report: ErrorReport) -> _Table:
    rows = [[_fmt_dt(event.time, seconds=True), event.request_id or "-",
             _clip(readable_line(event.message).strip().splitlines()[0], _LINE_CHARS)] for event in report.newest]
    return _Table([f"Time ({_tz_label()})", "Request ID", "Line"], rows, title="The newest error lines", max_rows=0,
                  collapsed=True, sortable=False)


def _cause_blocks(report: ErrorReport, *, run_call: Callable[[str], str]) -> list[Any]:
    """
    errors()' verdict, chart, a card per cause and the newest error lines folded, for a report with causes.
    """
    facts = _case_facts(report)
    verdict = errors_verdict(report, [fact.state for fact in facts])
    columns = chart_columns(report)
    lanes_by_group = {id(group): lane for group, lane in zip(report.groups, columns.lanes)}
    lanes = [_Lane(_cause_headline(fact.group), lanes_by_group[id(fact.group)], fact.state.state == STOPPED)
             for fact in facts]
    chart = _ErrorChart(failure_pattern(columns), _axis_labels(columns, report.until), columns.calls, columns.failed,
                        columns.unit, lanes, title=f"When it failed, a column per {columns.unit}")
    lines = sum(group.count for group in report.groups)
    causes = [_cause(number, fact, until=report.until, lines=lines, run_call=run_call)
              for number, fact in enumerate(facts, start=1)]
    title = "Errors by cause (from its log lines: a failed call can log more than one)"
    return [_Verdict(verdict.headline, verdict.detail, verdict.tone), chart, _Causes(causes, title),
            _newest_lines(report)]
