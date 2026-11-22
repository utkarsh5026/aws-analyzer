"""
_Causes (a function's errors by cause: a card each, with a row of dots on a shared time axis for when it happened,
the line of its own code it failed on and the call that shows its newest run), with its drawings, and the block
errors() builds from an ErrorReport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ..._kit.blocks import _Report, _draws
from ..._kit.fmt import _in_zone, _plural, _utcnow, human_age
from ..._kit.render import _prose
from ..._kit.text import _clip, _esc

from ..models import ErrorGroup, ErrorReport
from ..analysis.errors import failing_frame, time_slots


_SLOTS = 48

_AXIS_LABELS = 5

_HOURS_AXIS = timedelta(days=2)

_MESSAGE_CHARS = 110

_NOW_WITHIN = timedelta(minutes=5)

_EXAMPLE_CHARS = 4000

_TEXT_NAME_CHARS = 44

_SMALLEST_DOT, _LARGEST_DOT = 5, 12

_TEXT_DOTS = " ·•●"


@dataclass
class _Cause:
    """
    One cause as a card draws it: what it is, how often (`share` of every error line), when (`slots`, its count in
    each slice of the window), where in the code, and the call that reads its newest run.
    """

    number: int
    kind: str
    message: str
    count: int
    share: float
    first: str
    last: str
    slots: list[int] = field(default_factory=list)
    where: str = ""
    versions: str = ""
    run_call: str = ""
    example: str = ""


@dataclass
class _Causes:
    """
    A function's errors by cause, the most frequent first: a card each, every card's row of dots on the same time
    axis (`axis`, labels from the window's start to its end), so you see which cause is new, which is steady and
    which stopped. A few lines per cause in text.
    """

    causes: list[_Cause] = field(default_factory=list)
    axis: list[str] = field(default_factory=list)
    title: str = ""


def _axis_labels(since: datetime, until: datetime) -> list[str]:
    """
    _AXIS_LABELS times spread from `since` to `until` in the reports' zone: clock times for a window of two days or
    less, dates otherwise, the last one 'now' when the window ends now.
    """
    step = (until - since) / (_AXIS_LABELS - 1)
    shape = "%H:%M" if until - since <= _HOURS_AXIS else "%b %d"
    labels = [_in_zone(since + step * i).strftime(shape) for i in range(_AXIS_LABELS)]
    if abs(_utcnow() - until) <= _NOW_WITHIN:
        labels[-1] = "now"
    return labels


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


def _cause(number: int, group: ErrorGroup, report: ErrorReport, *, lines: int,
           run_call: Callable[[str], str]) -> _Cause:
    newest = group.request_ids[0] if group.request_ids else ""
    return _Cause(
        number, group.kind, _clip(group.message.strip().splitlines()[0] if group.message.strip() else group.kind,
                                  _MESSAGE_CHARS),
        group.count, group.count / lines if lines else 0.0,
        human_age(group.first, report.until), human_age(group.last, report.until),
        slots=time_slots(group.times, report.since, report.until, _SLOTS),
        where=_failed_at(group), versions=_versions(group),
        run_call=run_call(newest) if newest else "",
        example=_example(group),
    )


def _causes_block(report: ErrorReport, *, run_call: Callable[[str], str], title: str) -> _Causes:
    """
    errors()' causes as one _Causes block; `run_call` turns a request ID into the call that reads that run.
    """
    lines = sum(group.count for group in report.groups)
    causes = [_cause(number, group, report, lines=lines, run_call=run_call)
              for number, group in enumerate(report.groups, start=1)]
    return _Causes(causes, _axis_labels(report.since, report.until), title)


def _dot_size(count: int, most: int) -> float:
    return _SMALLEST_DOT + (_LARGEST_DOT - _SMALLEST_DOT) * min(1.0, count / most)


def _lane_html(slots: list[int]) -> str:
    most = max(slots, default=0) or 1
    cells = "".join(
        f'<i><b style="--d:{_dot_size(count, most):.1f}px" title="{_plural(count, "time")}"></b></i>' if count
        else "<i></i>"
        for count in slots
    )
    return f'<div class="cz-lane">{cells}</div>'


def _shows_kind(cause: _Cause) -> bool:
    """
    Whether the card names the kind beside the message: when the message doesn't start with it already.
    """
    return not cause.message.startswith(cause.kind)


def _cause_html(cause: _Cause) -> str:
    kind = f'<span class="cz-kind">{_esc(cause.kind)}</span>' if _shows_kind(cause) else ""
    when = f"first {cause.first} · last {cause.last}" + (f" · {cause.versions}" if cause.versions else "")
    rows = ""
    if cause.where:
        rows += f'<div class="cz-row"><span class="cz-k">Where</span><code>{_esc(cause.where)}</code></div>'
    if cause.run_call:
        rows += f'<div class="cz-row"><span class="cz-k">Newest run</span><span>{_prose(cause.run_call)}</span></div>'
    if cause.example:
        rows += (f'<details class="cz-full"><summary>The whole message</summary><pre>{_esc(cause.example)}</pre>'
                 "</details>")
    return (f'<div class="cz-c"><div class="cz-left"><div class="cz-head"><span class="cz-n">{cause.number}</span>'
            f'<div class="cz-what">{kind}<code>{_esc(cause.message)}</code></div></div>'
            f'<div class="cz-when">{_esc(when)}</div>{rows}</div>'
            f'<div class="cz-right"><div class="cz-count"><b>{cause.count:,}</b> {"time" if cause.count == 1 else "times"}'
            f'<span>{cause.share:.0%} of error lines</span></div>{_lane_html(cause.slots)}</div></div>')


@_draws(_Causes, "html")
def _causes_html(block: _Causes, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    ticks = "".join(f"<span>{_esc(label)}</span>" for label in block.axis)
    axis = f'<div class="cz-axis"><span></span><div class="cz-ticks">{ticks}</div></div>'
    return f'{title}<div class="cz">{axis}{"".join(_cause_html(cause) for cause in block.causes)}</div>'


def _text_axis(labels: list[str]) -> str:
    width = _SLOTS + max((len(label) for label in labels), default=0)
    row = [" "] * width
    for i, label in enumerate(labels):
        start = round(i * (_SLOTS - 1) / max(1, len(labels) - 1))
        start = min(start, width - len(label))
        row[start:start + len(label)] = label
    return "".join(row).rstrip()


def _text_lane(slots: list[int]) -> str:
    return "".join(_TEXT_DOTS[min(count, 2) + 1] if count else _TEXT_DOTS[1] for count in slots)


@_draws(_Causes, "text")
def _causes_lines(block: _Causes, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    indent = " " * (_TEXT_NAME_CHARS + 6)
    out.append(indent + _text_axis(block.axis))
    for cause in block.causes:
        name = _clip(cause.message, _TEXT_NAME_CHARS)
        out.append(f"{cause.number:>2}  {name:<{_TEXT_NAME_CHARS}}  {_text_lane(cause.slots)}  "
                   f"{cause.count:,} ({cause.share:.0%})")
        details = [f"first {cause.first}, last {cause.last}"]
        if cause.versions:
            details.append(cause.versions)
        if cause.where:
            details.append(f"where: {cause.where}")
        out.append("    " + " · ".join(details))
        if cause.run_call:
            out.append(f"    newest run: {cause.run_call}")
    return out
