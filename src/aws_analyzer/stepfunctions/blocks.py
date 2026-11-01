"""
The Step Functions reports' own blocks, with their drawings: _Waterfall (an execution's states on one time axis),
_Story (its log, Step Functions' lines and every Lambda run's merged by time) and _FlowGraph (the state machine as a
graph, coloured by an execution's path or by where failures happen).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.blocks import _Report, _draws
from .._kit.fmt import _duration, _fmt_dt, _in_zone, _tz_label
from .._kit.render import _prose
from .._kit.text import _clip, _esc

from .analysis.layout import FlowLayout, FlowMarks
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


_ARROW = 6

_LABEL_CHARS = 26

_MARK_WORDS = {"ok": "ran", "failed": "failed", "running": "never finished", "skip": "not reached",
               "h1": "some stopped here", "h2": "many stopped here", "h3": "most stopped here"}


@dataclass
class _FlowGraph:
    """
    A state machine as a graph: a box per top-level state (a Map's or Parallel's inner states written in it), an
    arrow per transition (dashed for a Catch, round the side for a loop), coloured by `marks` (one execution's path,
    or where failures happen). An inline SVG in HTML, with a legend; a list of states and arrows in text.
    """

    layout: FlowLayout
    marks: FlowMarks = field(default_factory=FlowMarks)
    title: str = ""


def _arrowhead(x: float, y: float, pointing: str) -> str:
    if pointing == "left":
        return f"M{x:.1f} {y:.1f} l{_ARROW + 2} {-_ARROW / 1.4:.1f} v{_ARROW * 1.43:.1f} z"
    return f"M{x:.1f} {y:.1f} l{-_ARROW / 1.4:.1f} {-_ARROW - 2} h{_ARROW * 1.43:.1f} z"


def _edge_svg(edge: Any, source: Any, target: Any, offset: float, taken: bool) -> str:
    """
    One arrow: down from the source's bottom to the target's top (a loop round the right side, back up to the
    target's side), with its head as a path.
    """
    classes = " ".join(filter(None, ["fe", edge.kind, "on" if taken else ""]))
    if edge.kind == "loop":
        x1, y1 = source.x + source.width, source.y + source.height / 2
        x2, y2 = target.x + target.width, target.y + target.height / 2
        bulge = max(x1, x2) + 46
        path = f"M{x1:.1f} {y1:.1f} C{bulge:.1f} {y1:.1f} {bulge:.1f} {y2:.1f} {x2 + _ARROW + 2:.1f} {y2:.1f}"
        head = _arrowhead(x2, y2, "left")
    else:
        x1, y1 = source.middle + offset, source.y + source.height
        x2, y2 = target.middle, target.y
        bend = max(18.0, (y2 - y1) / 2)
        path = f"M{x1:.1f} {y1:.1f} C{x1:.1f} {y1 + bend:.1f} {x2:.1f} {y2 - bend:.1f} {x2:.1f} {y2 - _ARROW - 2:.1f}"
        head = _arrowhead(x2, y2, "down")
    label = ""
    if edge.kind == "catch":
        anchor, at = ("end", x1 - 8) if x2 > x1 else ("start", x1 + 8)
        label = f'<text class="fl" x="{at:.1f}" y="{y1 + 14:.1f}" text-anchor="{anchor}">on error</text>'
    return f'<g class="{classes}"><path d="{path}"/><path class="fh" d="{head}"/>{label}</g>'


def _node_svg(node: Any, marks: FlowMarks) -> str:
    mark = marks.states.get(node.name, "")
    classes = " ".join(filter(None, ["fn", f"t-{node.type.lower()}", f"m-{mark}" if mark else "",
                                     "sel" if node.name == marks.selected else ""]))
    radius = node.height / 2 if node.type == "Choice" else 9
    note = " · ".join(filter(None, [node.does, marks.notes.get(node.name, "")])) or node.type
    tip = " · ".join(filter(None, [node.name, node.type, node.does, marks.notes.get(node.name, ""),
                                   _MARK_WORDS.get(mark, "")]))
    lines = [f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.0f}" height="{node.height:.0f}" '
             f'rx="{radius:.0f}"/>',
             f'<text class="fnm" x="{node.x + 12:.1f}" y="{node.y + 19:.1f}">{_esc(_clip(node.name, _LABEL_CHARS))}'
             "</text>",
             f'<text class="fns" x="{node.x + 12:.1f}" y="{node.y + 35:.1f}">{_esc(_clip(note, _LABEL_CHARS + 6))}'
             "</text>"]
    for i, inner in enumerate(node.inner):
        lines.append(f'<text class="fni" x="{node.x + 20:.1f}" y="{node.y + 52 + i * 15:.1f}">↳ '
                     f"{_esc(_clip(inner, _LABEL_CHARS))}</text>")
    if node.ends:
        cx, cy = node.middle, node.y + node.height + 16
        lines.append(f'<path class="fe" d="M{cx:.1f} {node.y + node.height:.1f} V{cy - 7:.1f}"/>'
                     f'<circle class="fend" cx="{cx:.1f}" cy="{cy:.1f}" r="6"/>')
    return f'<g class="{classes}"><title>{_esc(tip)}</title>{"".join(lines)}</g>'


def _offsets(layout: FlowLayout) -> dict[tuple[str, str], float]:
    """
    Where each arrow leaves its box's bottom: spread side by side when a box has several, so they don't overlap.
    """
    offsets = {}
    for node in layout.nodes:
        down = [e for e in layout.edges if e.source == node.name and e.kind != "loop"]
        for i, edge in enumerate(down):
            offsets[(edge.source, edge.target)] = (i - (len(down) - 1) / 2) * 22
    return offsets


@_draws(_FlowGraph, "html")
def _flow_html(block: _FlowGraph, report: _Report) -> str:
    layout, marks = block.layout, block.marks
    if not layout.nodes:
        return ""
    nodes = {n.name: n for n in layout.nodes}
    offsets = _offsets(layout)
    edges = "".join(_edge_svg(e, nodes[e.source], nodes[e.target], offsets.get((e.source, e.target), 0.0),
                              (e.source, e.target) in marks.taken)
                    for e in layout.edges if e.source in nodes and e.target in nodes)
    first = nodes.get(layout.start)
    start = ""
    if first is not None:
        start = (f'<circle class="fstart" cx="{first.middle:.1f}" cy="{first.y - 22:.1f}" r="7"/>'
                 f'<path class="fe{" on" if marks.taken else ""}" d="M{first.middle:.1f} '
                 f'{first.y - 15:.1f} V{first.y - _ARROW - 2:.1f}"/><path class="fh" '
                 f'd="{_arrowhead(first.middle, first.y, "down")}"/>')
    body = "".join(_node_svg(n, marks) for n in layout.nodes)
    used = sorted(set(marks.states.values()), key=list(_MARK_WORDS).index)
    legend = "".join(f'<span><i class="m-{m}"></i>{_esc(_MARK_WORDS[m])}</span>' for m in used)
    caption = f'<div class="fgk"><b>{_esc(marks.legend)}</b>{legend}</div>' if marks.legend else ""
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="fg">{caption}<svg viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
            f'width="{layout.width:.0f}" role="img" aria-label="{_esc(block.title or "state machine graph")}">'
            f"{edges}{start}{body}</svg></div>")


@_draws(_FlowGraph, "text")
def _flow_text(block: _FlowGraph, report: _Report) -> list[str]:
    layout, marks = block.layout, block.marks
    lines = [f"-- {block.title} --"] if block.title else []
    if marks.legend:
        lines.append(marks.legend)
    for node in sorted(layout.nodes, key=lambda n: (n.layer, n.column)):
        mark = marks.states.get(node.name, "")
        word = f" [{_MARK_WORDS.get(mark, mark)}]" if mark else ""
        note = marks.notes.get(node.name) or node.does
        targets = [f"{e.target}{' (on error)' if e.kind == 'catch' else ' (loop)' if e.kind == 'loop' else ''}"
                   for e in layout.edges if e.source == node.name]
        arrow = f" -> {', '.join(targets)}" if targets else (" -> end" if node.ends else "")
        lines.append(f"{'  ' * node.layer}{node.name} ({node.type}){word}{' · ' + note if note else ''}{arrow}")
    return lines
