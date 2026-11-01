"""
The Step Functions reports' own blocks, with their drawings: _Waterfall (an execution's states on one time axis)
and _Story (its log, Step Functions' lines and every Lambda run's merged by time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .._kit.blocks import _Report, _draws
from .._kit.fmt import _duration, _fmt_dt, _in_zone, _tz_label
from .._kit.render import _prose
from .._kit.text import _clip, _esc

from .analysis.story import STEP_FUNCTIONS, LogLine, Waterfall


_TEXT_BAR = 44

_TEXT_MARKS = {"ok": "█", "failed": "X", "retry": "r", "wait": "·", "running": "▒", "sdk": "▓", "choice": "|"}

_SOURCE_TONES = 6

_LEGEND = (("ok", "ran"), ("retry", "a try that failed and was retried"), ("wait", "back-off before the next try"),
           ("sdk", "an AWS service call"), ("failed", "failed"), ("running", "never finished"))

_TICKS = 4

_LINE_CHARS = 2_000


@dataclass
class _Waterfall:
    """
    An execution's waterfall: a row per state, its bar placed on the execution's time axis, a Map's iterations on
    one row. HTML draws the bars with a time scale and a legend; text draws them with characters.
    """

    waterfall: Waterfall
    title: str = ""


@dataclass
class _Story:
    """
    An execution's log: each line's time, what wrote it (a tag in a colour per source), and its text, error lines
    marked. `sources` are the writers in the order their colours go; `note` says what wasn't read.
    """

    lines: list[LogLine]
    sources: list[str] = field(default_factory=list)
    title: str = ""
    note: str = ""


def _scale(seconds: float) -> list[str]:
    return [_duration(seconds * i / _TICKS) if i else "0s" for i in range(_TICKS + 1)]


def _row_label(name: str, depth: int, count: int, failed: int) -> str:
    times = f" ×{count}" if count > 1 else ""
    broke = f" ({failed} failed)" if failed and count > 1 else ""
    return f"{'↳ ' if depth else ''}{name}{times}{broke}"


@_draws(_Waterfall, "html")
def _waterfall_html(block: _Waterfall, report: _Report) -> str:
    chart = block.waterfall
    if not chart.rows:
        return ""
    ticks = "".join(f"<span>{_esc(t)}</span>" for t in _scale(chart.seconds))
    rows = []
    for row in chart.rows:
        bars = "".join(
            f'<i class="wfs {seg.kind}" style="left:{seg.start * 100:.2f}%;width:max(2px,{seg.width * 100:.2f}%)" '
            f'title="{_esc(seg.label or seg.kind)}">{_esc(seg.label) if seg.width > 0.12 else ""}</i>'
            for seg in row.segments)
        label = _row_label(row.name, row.depth, row.count, row.failed)
        rows.append(f'<div class="wfr d{min(row.depth, 3)}"><div class="wfn" title="{_esc(row.does)}">'
                    f'{_esc(label)}<span>{_esc(row.does if row.does != row.type else row.type)}</span></div>'
                    f'<div class="wfl">{bars}</div></div>')
    legend = "".join(f'<span><i class="wfs {kind}"></i>{_esc(text)}</span>' for kind, text in _LEGEND)
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="wf"><div class="wfr wfh"><div class="wfn">State</div><div class="wft">{ticks}</div>'
            f'</div>{"".join(rows)}<div class="wfk">{legend}</div></div>')


@_draws(_Waterfall, "text")
def _waterfall_text(block: _Waterfall, report: _Report) -> list[str]:
    chart = block.waterfall
    if not chart.rows:
        return []
    width = max(len(_row_label(r.name, r.depth, r.count, r.failed)) for r in chart.rows) + 2
    lines = [f"-- {block.title} --"] if block.title else []
    lines.append(f"{'':{width}}0s{' ' * (_TEXT_BAR - 2 - len(_duration(chart.seconds)))}{_duration(chart.seconds)}")
    for row in chart.rows:
        cells = [" "] * _TEXT_BAR
        for seg in row.segments:
            first = int(seg.start * (_TEXT_BAR - 1))
            last = max(first, int((seg.start + seg.width) * (_TEXT_BAR - 1)))
            for i in range(first, last + 1):
                cells[i] = _TEXT_MARKS.get(seg.kind, "#")
        lines.append(f"{_row_label(row.name, row.depth, row.count, row.failed):{width}}{''.join(cells)}")
    lines.append("  ".join(f"{_TEXT_MARKS[kind]} {text}" for kind, text in _LEGEND))
    return lines


def _clock(moment: datetime) -> str:
    local = _in_zone(moment)
    return f"{local:%H:%M:%S}.{local.microsecond // 1000:03d}"


def _source_class(source: str, sources: list[str]) -> str:
    if source == STEP_FUNCTIONS:
        return "sf"
    order = [s for s in sources if s != STEP_FUNCTIONS]
    return f"l{order.index(source) % _SOURCE_TONES if source in order else 0}"


@_draws(_Story, "html")
def _story_html(block: _Story, report: _Report) -> str:
    sources = block.sources or list(dict.fromkeys(line.source for line in block.lines))
    tags = "".join(f'<span class="src {_source_class(s, sources)}">{_esc(s)}</span>' for s in sources)
    rows = []
    for line in block.lines:
        text = _esc(line.text[:_LINE_CHARS]).replace("\n", "<br>")
        rows.append(f'<div class="sl {line.kind}"><span class="sc">{_esc(_clock(line.time))}</span>'
                    f'<span class="src {_source_class(line.source, sources)}">{_esc(line.source)}</span>'
                    f'<span class="sx">{text}</span></div>')
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    note = f'<div class="sn">{_prose(block.note)}</div>' if block.note else ""
    return (f'{title}<div class="story"><div class="sh">{tags}<span class="sz">times in {_esc(_tz_label())}</span>'
            f'</div>{"".join(rows)}{note}</div>')


@_draws(_Story, "text")
def _story_text(block: _Story, report: _Report) -> list[str]:
    lines = [f"-- {block.title} --"] if block.title else []
    width = max((len(line.source) for line in block.lines), default=0)
    for line in block.lines:
        mark = "!" if line.kind == "error" else " "
        first = line.text.splitlines()[0] if line.text else ""
        lines.append(f"{mark} {_fmt_dt(line.time, seconds=True)}  {line.source:{width}}  {_clip(first, 140)}")
    if block.note:
        lines.append(f"[i] {block.note}")
    return lines
