"""
The functions() report's own blocks: _Todo (the few problems to act on first, each kind once with every function
that has it, the rest folded) and _FunctionList (every function as a line under the group it's in, failing first,
its calls a day, failed share, longest run against its timeout and cost on the line, its findings and next calls
folded under it, the healthy ones folded together).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..._kit.blocks import _Report, _draws
from ..._kit.fmt import _count, _plural, human_money
from ..._kit.render import _finding_html, _lead, _prose
from ..._kit.text import _clip, _esc

from ..helpers import _pct, human_ms
from ..analysis.fleet import FLEET_GROUPS, FailureTrend, FleetIssue, FleetRow, failure_trend
from ..models import FunctionMetrics

_DAY = timedelta(days=1)
_SPARK_WIDTH = 150
_SPARK_HEIGHT = 30
_FAIL_LANE = 6
_FAIL_REFERENCE = 0.02
_SPARK_CHARACTERS = " ▁▂▃▄▅▆▇█"
_TEXT_SPARK_DAYS = 30
_TIMEOUT_WARN = 0.7
_TIMEOUT_BAD = 0.9
_TEXT_LINE_WIDTH = 100
_GROUP_TONES = {"failing": "bad", "waste": "warn", "risk": "warn", "unused": "", "healthy": "ok"}



@dataclass
class _Todo:
    """
    The problems to act on first (`first`, each open with every function's finding and its command) and the rest
    folded under them (`rest`).
    """

    first: list[FleetIssue]
    rest: list[FleetIssue] = field(default_factory=list)


def _issue_money(issue: FleetIssue) -> str:
    return f"{human_money(issue.cost)}/month" if issue.cost >= 0.01 else ""


def _issue_html(issue: FleetIssue, number: str, *, is_open: bool) -> str:
    """
    One issue as a card: its number, what it is and why it matters, who has it and what it costs over a fold with
    each one's finding.
    """
    who = "".join(f'<span class="fx-who">{_esc(label)}</span>' for label in issue.who)
    money = _issue_money(issue)
    cost = f'<span class="fx-m">{_esc(money)}</span>' if money else ""
    why = f"<small>{_esc(issue.kind.why)}</small>" if issue.kind.why else ""
    hits = "".join(f'<div class="fx-h"><b>{_esc(label)}</b>{_lead(message)}</div>' for label, message in issue.hits)
    state = " open" if is_open else ""
    return (f'<details class="fx-i {issue.level}"{state}><summary><span class="fx-n">{number}</span>'
            f'<span class="fx-t"><b>{_esc(issue.kind.title)}</b>{why}</span><span class="fx-w">{who}</span>{cost}'
            f'</summary><div class="fx-b">{hits}</div></details>')


@_draws(_Todo, "html")
def _todo_html(block: _Todo, report: _Report) -> str:
    if not block.first and not block.rest:
        return ""
    heading = "Do this first" if block.first else "Worth knowing"
    cards = [_issue_html(issue, str(place), is_open=True) for place, issue in enumerate(block.first, 1)]
    if block.rest:
        later = "".join(_issue_html(issue, "!" if issue.level == "warn" else "i", is_open=False)
                        for issue in block.rest)
        if block.first:
            later = (f'<details class="fx-more"><summary>{_plural(len(block.rest), "more thing")} to look at'
                     f"</summary>{later}</details>")
        cards.append(later)
    return f'<div class="fx-todo"><h4>{heading}</h4>{"".join(cards)}</div>'


def _hit_lines(issue: FleetIssue) -> list[str]:
    return [f"     {label}: {message}" for label, message in issue.hits]


@_draws(_Todo, "text")
def _todo_lines(block: _Todo, report: _Report) -> list[str]:
    out: list[str] = []
    if block.first:
        out += ["", "-- Do this first --"]
        for place, issue in enumerate(block.first, 1):
            money = _issue_money(issue)
            out.append(f"  {place}. {issue.kind.title}" + (f" ({money})" if money else ""))
            out += _hit_lines(issue)
    if block.rest:
        out += ["", "-- Also worth a look --" if block.first else "-- Worth knowing --"]
        for issue in block.rest:
            mark = " (!)" if issue.level == "warn" else ""
            out.append(f"  {issue.kind.title}{mark}: {', '.join(issue.who)}")
            if issue.level == "warn":
                out += _hit_lines(issue)
    return out



@dataclass
class _FunctionList:
    """
    Every function as a line under its group (FLEET_GROUPS), with what CloudWatch counted when `with_metrics`. A
    line opens to its findings and `next_calls` (by function ARN); the healthy group is folded.
    """

    rows: list[FleetRow]
    now: datetime
    with_region: bool = False
    with_metrics: bool = True
    total_cost: float = 0.0
    next_calls: dict[str, list[str]] = field(default_factory=dict)


def _days_of(metrics: FunctionMetrics, now: datetime) -> list[tuple[str, float, float]]:
    """
    (day, calls, failed calls) for each day of the window, oldest first, the days CloudWatch has nothing for as 0.
    Numbers counted by the hour are kept as they are.
    """
    if metrics.period < _DAY.total_seconds():
        return [(f"{d.start:%H:%M}", d.invocations, d.errors) for d in metrics.daily]
    counted = {d.start.date(): d for d in metrics.daily}
    last = now.date()
    days = max(1, round(metrics.days))
    out = []
    for back in range(days - 1, -1, -1):
        day: date = last - timedelta(days=back)
        usage = counted.get(day)
        out.append((day.isoformat(), usage.invocations if usage else 0.0, usage.errors if usage else 0.0))
    return out


def _trend_svg(days: list[tuple[str, float, float]], name: str) -> str:
    """
    Calls a day as grey bars, each to the busiest day, over a lane of red marks as tall as the day's share of
    failed calls against the worst day's (or _FAIL_REFERENCE, when every day is below it), so a few failures show as
    a thin line and a jump in them stands out.
    """
    busiest = max((calls for _, calls, _ in days), default=0.0) or 1.0
    worst = max([failed / calls for _, calls, failed in days if calls] + [_FAIL_REFERENCE])
    width = _SPARK_WIDTH / max(1, len(days))
    plot = _SPARK_HEIGHT - _FAIL_LANE - 1
    marks = []
    for place, (day, calls, failed) in enumerate(days):
        x = place * width
        tip = f"{day}: {_count(round(calls))} calls" + (f", {_count(round(failed))} failed" if failed else "")
        height = max(1.0, calls / busiest * plot) if calls else 0.5
        marks.append(f'<rect x="{x + .4:.1f}" y="{plot - height:.1f}" width="{max(width - .8, .6):.1f}" '
                     f'height="{height:.1f}" class="{"c" if calls else "z"}"><title>{_esc(tip)}</title></rect>')
        if failed:
            lane = max(1.5, failed / calls / worst * _FAIL_LANE)
            marks.append(f'<rect x="{x + .4:.1f}" y="{_SPARK_HEIGHT - lane:.1f}" width="{max(width - .8, .6):.1f}" '
                         f'height="{lane:.1f}" class="e"/>')
    label = f"{name}: calls a day, failed calls in red below"
    return (f'<svg class="fx-spark" width="{_SPARK_WIDTH}" height="{_SPARK_HEIGHT}" viewBox="0 0 {_SPARK_WIDTH} '
            f'{_SPARK_HEIGHT}" role="img" aria-label="{_esc(label)}">{"".join(marks)}</svg>')


def _text_spark(days: list[tuple[str, float, float]]) -> str:
    """
    Calls a day as block characters from zero to the busiest day, the last _TEXT_SPARK_DAYS of them.
    """
    shown = days[-_TEXT_SPARK_DAYS:]
    busiest = max((calls for _, calls, _ in shown), default=0.0) or 1.0
    top = len(_SPARK_CHARACTERS) - 1
    return "".join(_SPARK_CHARACTERS[round(calls / busiest * top) if calls else 0] for _, calls, _ in shown)


def _failed_text(trend: FailureTrend | None) -> str:
    if trend is None:
        return "-"
    if trend.is_rising and trend.recent_rate is not None:
        return f"{_pct(trend.rate)}, {_pct(trend.recent_rate)} in the last 3 days"
    return _pct(trend.rate)


def _failed_html(trend: FailureTrend | None) -> str:
    if trend is None:
        return '<span class="fx-q">-</span>'
    rising = ""
    if trend.is_rising and trend.recent_rate is not None:
        rising = f'<span class="fx-up">↑ {_esc(_pct(trend.recent_rate))} last 3 days</span>'
    return f"<span>{_esc(_pct(trend.rate))}</span>{rising}"


def _timeout_share(row: FleetRow) -> float | None:
    longest = row.metrics.duration_max if row.metrics else None
    if not longest or not row.function.timeout:
        return None
    return longest / (row.function.timeout * 1000)


def _timeout_html(row: FleetRow) -> str:
    """
    The longest run as a bar against the timeout, amber past _TIMEOUT_WARN and red past _TIMEOUT_BAD.
    """
    share = _timeout_share(row)
    timeout = f"{row.function.timeout:,} s"
    if share is None:
        return f'<span class="fx-q">not run · {_esc(timeout)}</span>'
    tone = "bad" if share >= _TIMEOUT_BAD else "warn" if share >= _TIMEOUT_WARN else ""
    longest = human_ms(row.metrics.duration_max if row.metrics else None)
    return (f'<span class="fx-bar"><i class="{tone}" style="width:{min(share, 1.0) * 100:.0f}%"></i></span>'
            f'<span class="fx-q">{_esc(longest)} of {_esc(timeout)}</span>')


def _runtime_html(row: FleetRow) -> str:
    return f'<span class="fx-rt {_esc(row.runtime.tone or "")}">{_esc(row.runtime.label)}</span>'


def _row_facts(row: FleetRow, *, with_region: bool) -> list[str]:
    """
    The facts under a function's name: region, memory and what triggers it.
    """
    fn = row.function
    parts = [fn.region] if with_region else []
    parts.append(f"{fn.memory:,} MB")
    parts.append(", ".join(row.triggers) if row.triggers else "no trigger")
    return parts


def _finding_chips_html(row: FleetRow) -> str:
    chips = []
    for found in row.findings:
        if not found.kind.short:
            continue
        tone = "warn" if found.level == "warn" else "info"
        chips.append(f'<span class="chip {tone}">{_esc(found.kind.short)}</span>')
    return f'<span class="fx-chips">{"".join(dict.fromkeys(chips))}</span>' if chips else ""


def _row_body(row: FleetRow, calls: list[str]) -> str:
    findings = "".join(_finding_html(found.level, found.message) for found in row.findings)
    if not findings:
        findings = '<div class="fi ok"><div class="hd">Every check passed</div></div>'
    steps = "".join(f"<span>{_prose(call)}</span>" for call in calls)
    return f'<div class="fx-body"><div class="fd">{findings}</div><div class="fx-next">{steps}</div></div>'


def _cost_html(row: FleetRow, total: float) -> str:
    cost = row.total_cost
    if cost is None:
        return '<span class="fx-q">-</span>'
    share = cost / total if total else 0.0
    return (f"<span>{_esc(human_money(cost))}</span>"
            f'<span class="fx-share"><i style="width:{min(share, 1.0) * 100:.1f}%"></i></span>')


def _row_html(row: FleetRow, block: _FunctionList) -> str:
    fn = row.function
    tone = _GROUP_TONES[row.group]
    about = " · ".join(_esc(part) for part in _row_facts(row, with_region=block.with_region))
    name = (f'<span class="fx-nm"><b>{_esc(fn.name)}</b><small>{_runtime_html(row)} · {about}</small>'
            f"{_finding_chips_html(row)}</span>")
    cells = [f'<span class="fx-dot {tone}"></span>', name]
    if block.with_metrics:
        days = _days_of(row.metrics, block.now) if row.metrics else []
        calls = _count(round(row.metrics.invocations)) if row.metrics else "-"
        trend = _trend_svg(days, fn.name) if days else ""
        cells += [f'<span class="fx-tr">{trend}<span class="fx-q">{_esc(calls)} calls</span></span>',
                  f'<span class="fx-fr">{_failed_html(failure_trend(row.metrics, block.now))}</span>',
                  f'<span class="fx-to">{_timeout_html(row)}</span>']
    cells.append(f'<span class="fx-co">{_cost_html(row, block.total_cost)}</span>')
    body = _row_body(row, block.next_calls.get(fn.arn, []))
    return f'<details class="fx-row {tone}"><summary>{"".join(cells)}</summary>{body}</details>'


def _grouped(rows: list[FleetRow]) -> list[tuple[str, str, list[FleetRow]]]:
    return [(key, title, [row for row in rows if row.group == key]) for key, title in FLEET_GROUPS
            if any(row.group == key for row in rows)]


def _list_header_html(with_metrics: bool) -> str:
    names = ["", "Function"]
    if with_metrics:
        names += ["Calls a day", "Failed", "Longest run"]
    names.append("Est. $/month")
    return '<div class="fx-head">' + "".join(f"<span>{_esc(name)}</span>" for name in names) + "</div>"


@_draws(_FunctionList, "html")
def _function_list_html(block: _FunctionList, report: _Report) -> str:
    parts = [_list_header_html(block.with_metrics)]
    for key, title, rows in _grouped(block.rows):
        lines = "".join(_row_html(row, block) for row in rows)
        heading = f"{_esc(title)} · {len(rows):,}"
        if key == "healthy" and len(rows) < len(block.rows):
            parts.append(f'<details class="fx-g ok"><summary>{heading}</summary>{lines}</details>')
        else:
            parts.append(f'<div class="fx-g {_GROUP_TONES[key]}"><div class="fx-gh">{heading}</div>{lines}</div>')
    kind = "" if block.with_metrics else " fx-nomet"
    return f'<div class="fx-list{kind}">{"".join(parts)}</div>'


def _row_lines(row: FleetRow, block: _FunctionList) -> list[str]:
    fn = row.function
    mark = {"bad": "!!", "warn": "! ", "ok": "  "}.get(_GROUP_TONES[row.group], "  ")
    about = " · ".join([row.runtime.label] + _row_facts(row, with_region=block.with_region))
    out = [f"{mark} {fn.name}  {about}"]
    numbers = []
    if block.with_metrics and row.metrics is not None:
        days = _days_of(row.metrics, block.now)
        numbers.append(f"{_count(round(row.metrics.invocations))} calls {_text_spark(days)}")
        numbers.append(f"failed {_failed_text(failure_trend(row.metrics, block.now))}")
        share = _timeout_share(row)
        if share is not None:
            numbers.append(f"longest {human_ms(row.metrics.duration_max)} of {fn.timeout:,} s")
    cost = row.total_cost
    numbers.append(f"{human_money(cost)}/month" if cost is not None else "cost unknown")
    out.append(f"     {'  ·  '.join(numbers)}")
    shorts = list(dict.fromkeys(found.kind.short for found in row.findings if found.kind.short))
    if shorts:
        out.append(_clip(f"     {', '.join(shorts)}", _TEXT_LINE_WIDTH))
    return out


@_draws(_FunctionList, "text")
def _function_list_lines(block: _FunctionList, report: _Report) -> list[str]:
    out: list[str] = []
    for key, title, rows in _grouped(block.rows):
        out += ["", f"-- {title} ({len(rows):,}) --"]
        shown = rows if key != "healthy" or not report.max_rows else rows[:report.max_rows]
        for row in shown:
            out += _row_lines(row, block)
        if len(shown) < len(rows):
            out.append(f"   ... and {len(rows) - len(shown):,} more")
    return out

