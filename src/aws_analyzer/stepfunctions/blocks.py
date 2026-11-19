"""
The Step Functions reports' own blocks, with their drawings: _Waterfall (an execution's states on one time axis),
_Story (its log, Step Functions' lines and every Lambda run's merged by time) and _FlowGraph (the state machine as a
graph, coloured by an execution's path or by where failures happen), and _payload_block, which draws an input, an
output or an error's cause.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.blocks import _Report, _Text, _draws
from .._kit.jsontree import _JsonTree
from .._kit.fmt import _duration, _fmt_dt, _in_zone, _tz_label
from .._kit.render import _prose
from .._kit.text import _clip, _esc

from .analysis.layout import END, LABEL_CHAR_WIDTH, LABEL_CHARS, FlowEdge, FlowLayout, FlowMarks, FlowNode
from .analysis.story import STEP_FUNCTIONS, LogLine, Waterfall
from .analysis.trend import DayCount, FailureJump, day_label
from .analysis.suspects import change_text
from .models import Change, Execution


_TEXT_BAR = 44

_TEXT_MARKS = {"ok": "█", "failed": "X", "retry": "r", "wait": "·", "running": "▒", "sdk": "▓", "choice": "|"}

_SOURCE_TONES = 6

_LEGEND = (("ok", "ran"), ("retry", "a try that failed and was retried"), ("wait", "back-off before the next try"),
           ("sdk", "an AWS service call"), ("failed", "failed"), ("running", "never finished"))

_TICKS = 4

_LINE_CHARS = 2_000

_PAYLOAD_ITEMS = 50

_PAYLOAD_CHARS = 4_000


def _payload_block(text: str, title: str, *, name: str = "data") -> Any:
    """
    A payload Step Functions recorded (an execution's input or output, what a state was given or gave back, an
    error's cause) folded under `title`: a JSON object or array as a tree to unfold, field by field, anything else as
    its text, clipped to _PAYLOAD_CHARS. `name` starts each field's path in its tooltip (output['items'][0]).
    """
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, (dict, list)):
        return _JsonTree(value, items=_PAYLOAD_ITEMS, title=title, name=name, collapsed=True)
    shown = text or ""
    if len(shown) > _PAYLOAD_CHARS:
        shown = f"{shown[:_PAYLOAD_CHARS]}\n… ({len(shown) - _PAYLOAD_CHARS:,} more characters)"
    return _Text(shown, title=title, collapsed=True)


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


_ARROW = 7

_CORNER = 9

_NAME_CHARS = 26

_KIND_CHARS = 32

_NOTE_CHARS = 34

_ZOOMS = (("fit", "Fit"), ("z15", "1.5×"), ("z2", "2×"))

_MARK_WORDS = {"ok": "ran", "failed": "failed", "running": "never finished", "skip": "not reached",
               "h1": "some stopped here", "h2": "many stopped here", "h3": "most stopped here",
               "t1": "under 15% of the time", "t2": "15% of the time or more", "t3": "half the time or more"}

_ICON_PATHS = {
    "database": "M2 4c0-2 12-2 12 0v8c0 2-12 2-12 0zM2 4c0 2 12 2 12 0M2 8c0 2 12 2 12 0",
    "messaging": "M2 4h12v8H2zM2 4l6 5 6-5",
    "storage": "M2 4h12l-1.6 9H3.6zM2 4c0 1.6 12 1.6 12 0",
    "compute": "M3 3h10v10H3zM6 6h4v4H6zM6 1v2M10 1v2M6 13v2M10 13v2",
    "ml": "M8 2v12M2 8h12M4 4l8 8M12 4l-8 8",
    "analytics": "M3 13V8M8 13V3M13 13V6",
    "security": "M8 2l5 2v4c0 3-2.4 5-5 6-2.6-1-5-3-5-6V4z",
    "management": "M2 12l3-4 3 2 3-5 3 3",
    "service": "M8 2l6 3v6l-6 3-6-3V5zM2 5l6 3 6-3M8 8v6",
    "choice": "M8 1.5l6.5 6.5L8 14.5 1.5 8z",
    "wait": "M8 2a6 6 0 1 0 .01 0M8 5v3.4l2.4 1.6",
    "pass": "M2 8h11M9.5 4.5 13 8l-3.5 3.5",
    "map": "M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z",
    "parallel": "M4 2v12M8 2v12M12 2v12",
    "succeed": "M3 8.5l3 3 7-7",
    "fail": "M4 4l8 8M12 4l-8 8",
}

_STATUS_PATHS = {"ok": "M-3.2 0.2l2.1 2.1 4-4.4", "failed": "M-2.6-2.6l5.2 5.2M2.6-2.6l-5.2 5.2",
                 "running": "M0-3.6a3.6 3.6 0 1 1-3.6 3.6"}


@dataclass
class _FlowGraph:
    """
    A state machine as a graph, the way the console draws it: Start and End, a box per state with its service's
    icon and what it calls, a Map's or Parallel's inner states drawn inside its box, right-angled arrows labelled
    with the Choice rule or the errors a Catch takes them on (dashed), coloured by `marks` (one execution's path,
    flowing along the arrows it took; where failures happen; where the time goes). Pointing at a box lights up its
    arrows and the boxes next to it. An inline SVG in HTML, with a legend and a zoom; a list of states and arrows in
    text. `bare` draws the SVG alone, for a window that shows the legend itself and puts its click targets over it.
    """

    layout: FlowLayout
    marks: FlowMarks = field(default_factory=FlowMarks)
    title: str = ""
    bare: bool = False


@dataclass
class _FlowKey:
    """
    What a graph's colours mean: its legend's words, then a swatch per mark it uses.
    """

    marks: FlowMarks


def _rounded(points: list[tuple[float, float]]) -> str:
    """
    A line through its corners, each corner rounded.
    """
    if len(points) < 2:
        return ""
    path = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    for before, corner, after in zip(points, points[1:], points[2:]):
        radius = min(_CORNER, _length(before, corner) / 2, _length(corner, after) / 2)
        into = _toward(corner, before, radius)
        out_of = _toward(corner, after, radius)
        path.append(f"L{into[0]:.1f} {into[1]:.1f} Q{corner[0]:.1f} {corner[1]:.1f} {out_of[0]:.1f} {out_of[1]:.1f}")
    path.append(f"L{points[-1][0]:.1f} {points[-1][1]:.1f}")
    return " ".join(path)


def _length(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _toward(origin: tuple[float, float], to: tuple[float, float], distance: float) -> tuple[float, float]:
    """
    The point `distance` from origin on the straight line to `to` (the lines are all straight across or down).
    """
    length = _length(origin, to) or 1.0
    return (origin[0] + (to[0] - origin[0]) * distance / length, origin[1] + (to[1] - origin[1]) * distance / length)


def _arrowhead(points: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], str]:
    """
    The line shortened by its head, and the head: a triangle pointing the way the last stretch goes.
    """
    (bx, by), (tx, ty) = points[-2], points[-1]
    length = _length((bx, by), (tx, ty)) or 1.0
    dx, dy = (tx - bx) / length, (ty - by) / length
    base_x, base_y = tx - dx * (_ARROW + 2), ty - dy * (_ARROW + 2)
    half_x, half_y = -dy * _ARROW * 0.62, dx * _ARROW * 0.62
    head = (f"M{tx:.1f} {ty:.1f} L{base_x + half_x:.1f} {base_y + half_y:.1f} "
            f"L{base_x - half_x:.1f} {base_y - half_y:.1f} Z")
    return points[:-1] + [(base_x + dx, base_y + dy)], head


class _GraphIds:
    """
    A number per box, so the rules that light a box's arrows and neighbours up can name them: n<i> on the box,
    s<i> / t<i> on the arrows leaving and entering it, a<i> on the boxes next to it (and the Map or Parallel it sits
    in), in<i> on the boxes inside it.
    """

    def __init__(self, layout: FlowLayout) -> None:
        boxes = layout.pills + layout.nodes
        self.number = {box.name: i for i, box in enumerate(boxes)}
        self.near: dict[str, set[int]] = {box.name: set() for box in boxes}
        for edge in layout.edges:
            if edge.source in self.number and edge.target in self.number:
                self.near[edge.source].add(self.number[edge.target])
                self.near[edge.target].add(self.number[edge.source])
        for box in layout.nodes:
            if box.parent in self.number:
                self.near[box.parent].add(self.number[box.name])

    def classes(self, box: FlowNode) -> str:
        own = [f"n{self.number[box.name]}"] + [f"a{i}" for i in sorted(self.near[box.name])]
        if box.parent in self.number:
            own.append(f"in{self.number[box.parent]}")
        return " ".join(own)

    def edge_classes(self, edge: FlowEdge) -> str:
        return f"s{self.number.get(edge.source, -1)} t{self.number.get(edge.target, -1)}"


def _edge_svg(edge: FlowEdge, ids: _GraphIds, *, taken: bool, delay: int) -> str:
    """
    One arrow: its rounded line, a dashed one flowing along it when the execution took it, and its head.
    """
    if len(edge.points) < 2:
        return ""
    line, head = _arrowhead(edge.points)
    drawn = _rounded(line)
    classes = " ".join(filter(None, ["fe", edge.kind, "on" if taken else "", ids.edge_classes(edge)]))
    flow = f'<path class="fa" d="{drawn}"/>' if taken else ""
    return (f'<g class="{classes}" style="--l:{delay}"><path class="fp" d="{drawn}"/>{flow}'
            f'<path class="fh" d="{head}"/></g>')


def _edge_label_svg(edge: FlowEdge, ids: _GraphIds, *, taken: bool, delay: int) -> str:
    """
    An arrow's label (a Choice rule, 'Default', a Catch's errors) in a pill over its line, the whole of it in the
    tooltip.
    """
    if not edge.label or edge.label_at is None:
        return ""
    x, y = edge.label_at
    text = _clip(edge.label, LABEL_CHARS)
    width = len(text) * LABEL_CHAR_WIDTH + 14
    classes = " ".join(filter(None, ["fq", edge.kind, "on" if taken else "", ids.edge_classes(edge)]))
    return (f'<g class="{classes}" style="--l:{delay}"><title>{_esc(edge.label)}</title>'
            f'<rect x="{x - width / 2:.1f}" y="{y - 9:.1f}" width="{width:.1f}" height="18" rx="9"/>'
            f'<text x="{x:.1f}" y="{y + 3.6:.1f}" text-anchor="middle">{_esc(text)}</text></g>')


def _icon_svg(icon: str, x: float, y: float, size: float) -> str:
    """
    A box's icon: a rounded tile in its service's colour with a white line drawing (λ for Lambda).
    """
    tile = f'<rect class="fi i-{icon}" x="{x:.1f}" y="{y:.1f}" width="{size:.0f}" height="{size:.0f}" rx="{size / 4:.0f}"/>'
    if icon == "lambda":
        glyph = (f'<text class="fgl" x="{x + size / 2:.1f}" y="{y + size * 0.72:.1f}" text-anchor="middle" '
                 f'font-size="{size * 0.62:.1f}">λ</text>')
        return tile + glyph
    scale = size / 28
    glyph = (f'<path class="fgl" transform="translate({x + 6 * scale:.1f} {y + 6 * scale:.1f}) scale({scale:.2f})" '
             f'd="{_ICON_PATHS.get(icon, _ICON_PATHS["service"])}"/>')
    return tile + glyph


def _status_svg(mark: str, x: float, y: float) -> str:
    """
    The badge in a box's corner: a tick for ran, a cross for failed, a turning arc for never finished.
    """
    if mark not in _STATUS_PATHS:
        return ""
    return (f'<g class="fst s-{mark}" transform="translate({x:.1f} {y:.1f})"><circle r="8"/>'
            f'<path d="{_STATUS_PATHS[mark]}"/></g>')


def _tip(node: FlowNode, marks: FlowMarks) -> str:
    mark = marks.states.get(node.name, "")
    return " · ".join(filter(None, [node.name, node.kind, node.does, marks.notes.get(node.name, ""),
                                    _MARK_WORDS.get(mark, "")]))


def _box_classes(node: FlowNode, marks: FlowMarks, ids: _GraphIds) -> str:
    mark = marks.states.get(node.name, "")
    return " ".join(filter(None, ["fn", "fc" if node.is_container else "", f"t-{node.type.lower()}",
                                  f"m-{mark}" if mark else "", "sel" if node.name == marks.selected else "",
                                  ids.classes(node)]))


def _second_line(node: FlowNode) -> str:
    """
    What a box says under its name: the Lambda function it calls, else what the console says ('DynamoDB:
    UpdateItem', 'Wait state').
    """
    if node.icon == "lambda" and node.does.startswith("λ "):
        return node.does.removeprefix("λ ")
    return node.kind


def _halo_svg(node: FlowNode, mark: str) -> str:
    if mark not in ("failed", "running"):
        return ""
    return (f'<rect class="fhalo" x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="10"/>')


def _leaf_svg(node: FlowNode, marks: FlowMarks, ids: _GraphIds) -> str:
    """
    A state's box: its icon, its name, what it does, and the mark's note (its time, tries, share) in the mark's
    colour, with a badge for how it went.
    """
    mark = marks.states.get(node.name, "")
    note = marks.notes.get(node.name, "")
    text_x = node.x + 52
    name_y, kind_y = (node.y + 22, node.y + 38) if note else (node.y + 27, node.y + 44)
    parts = [_halo_svg(node, mark),
             f'<rect class="fb" x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
             f'height="{node.height:.1f}" rx="10"/>',
             _icon_svg(node.icon, node.x + 12, node.y + (node.height - 30) / 2, 30),
             f'<text class="fnm" x="{text_x:.1f}" y="{name_y:.1f}">{_esc(_clip(node.name, _NAME_CHARS))}</text>',
             f'<text class="fns" x="{text_x:.1f}" y="{kind_y:.1f}">'
             f"{_esc(_clip(_second_line(node), _KIND_CHARS))}</text>"]
    if note:
        parts.append(f'<text class="fnt" x="{text_x:.1f}" y="{node.y + 53:.1f}">{_esc(_clip(note, _NOTE_CHARS))}'
                     "</text>")
    parts.append(_status_svg(mark, node.right - 2, node.y + 2))
    return (f'<g class="{_box_classes(node, marks, ids)}" style="--l:{node.layer + node.depth}">'
            f'<title>{_esc(_tip(node, marks))}</title>{"".join(parts)}</g>')


def _container_svg(node: FlowNode, marks: FlowMarks, ids: _GraphIds) -> str:
    """
    A Map's or Parallel's box: dashed round the graph drawn in it, its name and kind in the header, its note at the
    header's right, and a dashed line between a Parallel's branches.
    """
    mark = marks.states.get(node.name, "")
    note = marks.notes.get(node.name, "")
    parts = [_halo_svg(node, mark),
             f'<rect class="fb" x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
             f'height="{node.height:.1f}" rx="12"/>',
             _icon_svg(node.icon, node.x + 12, node.y + 11, 24),
             f'<text class="fnm" x="{node.x + 44:.1f}" y="{node.y + 22:.1f}">{_esc(_clip(node.name, _NAME_CHARS))}'
             "</text>",
             f'<text class="fns" x="{node.x + 44:.1f}" y="{node.y + 36:.1f}">{_esc(node.kind)}</text>']
    if note:
        parts.append(f'<text class="fnt" x="{node.right - 16:.1f}" y="{node.y + 36:.1f}" text-anchor="end">'
                     f"{_esc(_clip(note, _NOTE_CHARS))}</text>")
    for x in node.dividers:
        parts.append(f'<path class="fdv" d="M{x:.1f} {node.y + 44:.1f} V{node.bottom - 10:.1f}"/>')
    parts.append(_status_svg(mark, node.right - 2, node.y + 2))
    return (f'<g class="{_box_classes(node, marks, ids)}" style="--l:{node.layer + node.depth}">'
            f'<title>{_esc(_tip(node, marks))}</title>{"".join(parts)}</g>')


def _pill_svg(pill: FlowNode, ids: _GraphIds) -> str:
    return (f'<g class="fpl {ids.classes(pill)}" style="--l:{pill.layer}"><rect x="{pill.x:.1f}" y="{pill.y:.1f}" '
            f'width="{pill.width:.0f}" height="{pill.height:.0f}" rx="{pill.height / 2:.0f}"/>'
            f'<text x="{pill.middle:.1f}" y="{pill.y + pill.height / 2 + 4:.1f}" text-anchor="middle">'
            f"{pill.type}</text></g>")


def _hover_rules(layout: FlowLayout, ids: _GraphIds, root: str) -> list[str]:
    """
    The rules that light a box up while it's pointed at (in the report, or through a window's click target over it,
    .fgh<i> in a .fgc): its arrows and their labels bold, the boxes next to it and inside it as they were, the rest
    faded.
    """
    rules = []
    for box in layout.pills + layout.nodes:
        i = ids.number[box.name]
        scopes = [f".{root} .fg:has(.n{i}:hover)", f".fgc:has(.fgh{i}:hover) .{root} .fg"]
        faded_lines = ",".join(f"{scope} :is(.fe,.fq):not(.s{i}):not(.t{i})" for scope in scopes)
        faded_boxes = ",".join(f"{scope} :is(.fn,.fpl):not(.n{i}):not(.a{i}):not(.in{i})" for scope in scopes)
        bold = ",".join(f"{scope} .fe:is(.s{i},.t{i}) .fp" for scope in scopes)
        lifted = ",".join(f"{scope} .n{i} .fb" for scope in scopes)
        rules += [f"{faded_lines}{{opacity:.14}}", f"{faded_boxes}{{opacity:.32}}",
                  f"{bold}{{stroke:var(--fg-hot);stroke-width:2.6}}", f"{lifted}{{stroke:var(--fg-hot);stroke-width:2}}"]
    return rules


def _graph_id(block: _FlowGraph) -> str:
    """
    A name for the graph's zoom buttons, the same each time it's drawn (so a window doesn't redraw it for nothing)
    and different for another graph in the notebook.
    """
    names = ",".join(n.name for n in block.layout.nodes)
    return f"fz{zlib.crc32(f'{block.title}|{block.marks.legend}|{names}'.encode()):x}"


def _zoom_html(block: _FlowGraph) -> str:
    """
    The zoom buttons: radio buttons under labels, hidden so a notebook without its style shows none, that rules in
    the stylesheet read to size the drawing.
    """
    name = _graph_id(block)
    choices = "".join(f'<label><input type="radio" name="{name}" class="{key}" hidden="hidden"'
                      f'{" checked" if key == "fit" else ""}>{words}</label>' for key, words in _ZOOMS)
    return f'<span class="fgz" hidden="hidden">{choices}</span>'


def _key_html(marks: FlowMarks) -> str:
    used = sorted(set(marks.states.values()) & set(_MARK_WORDS), key=list(_MARK_WORDS).index)
    swatches = "".join(f'<span><i class="m-{m}"></i>{_esc(_MARK_WORDS[m])}</span>' for m in used)
    flowing = '<span><i class="m-flow"></i>the way it went</span>' if marks.taken else ""
    return f"<b>{_esc(marks.legend)}</b>{swatches}{flowing}" if marks.legend else ""


@_draws(_FlowKey, "html")
def _flow_key_html(block: _FlowKey, report: _Report) -> str:
    key = _key_html(block.marks)
    return f'<div class="fgk">{key}</div>' if key else ""


@_draws(_FlowKey, "text")
def _flow_key_text(block: _FlowKey, report: _Report) -> list[str]:
    return [block.marks.legend] if block.marks.legend else []


def _graph_svg(block: _FlowGraph, ids: _GraphIds) -> str:
    """
    The drawing, back to front: the Map and Parallel boxes, the arrows, the states, the arrows' labels, Start and
    End.
    """
    layout, marks = block.layout, block.marks
    layers = {box.name: box.layer + box.depth for box in layout.nodes + layout.pills}
    containers = sorted((n for n in layout.nodes if n.is_container), key=lambda n: n.depth)
    edges = [(edge, (edge.source, edge.target) in marks.taken, layers.get(edge.source, 0)) for edge in layout.edges]
    return "".join([
        "".join(_container_svg(n, marks, ids) for n in containers),
        "".join(_edge_svg(edge, ids, taken=taken, delay=delay) for edge, taken, delay in edges),
        "".join(_leaf_svg(n, marks, ids) for n in layout.nodes if not n.is_container),
        "".join(_edge_label_svg(edge, ids, taken=taken, delay=delay) for edge, taken, delay in edges),
        "".join(_pill_svg(pill, ids) for pill in layout.pills),
    ])


@_draws(_FlowGraph, "html")
def _flow_html(block: _FlowGraph, report: _Report) -> str:
    layout = block.layout
    if not layout.nodes:
        return ""
    ids = _GraphIds(layout)
    report.rules.update(_hover_rules(layout, ids, report.root))
    svg = (f'<svg class="fgs" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" width="{layout.width:.0f}" '
           f'height="{layout.height:.0f}" role="img" aria-label="{_esc(block.title or "state machine graph")}">'
           f"{_graph_svg(block, ids)}</svg>")
    if block.bare:
        return f'<div class="fg bare">{svg}</div>'
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    head = f'<div class="fgk">{_key_html(block.marks)}{_zoom_html(block)}</div>'
    return f'{title}<div class="fg" style="--w:{layout.width:.0f}px">{head}<div class="fgv">{svg}</div></div>'


def _edge_word(edge: FlowEdge) -> str:
    if edge.kind == "catch":
        return " (on error)" if edge.label == "any error" else f" (on {edge.label})"
    if edge.kind == "loop":
        return f" ({edge.label}, loop)" if edge.label else " (loop)"
    return f" ({edge.label})" if edge.label else ""


def _text_line(node: FlowNode, layout: FlowLayout, marks: FlowMarks, indent: str) -> str:
    mark = marks.states.get(node.name, "")
    word = f" [{_MARK_WORDS.get(mark, mark)}]" if mark else ""
    note = marks.notes.get(node.name) or node.does
    targets = [f"{e.target}{_edge_word(e)}" for e in layout.edges if e.source == node.name and e.target != END]
    arrow = f" -> {', '.join(targets)}" if targets else (" -> end" if node.ends else "")
    return f"{indent}{node.name} ({node.type}){word}{' · ' + note if note else ''}{arrow}"


@_draws(_FlowGraph, "text")
def _flow_text(block: _FlowGraph, report: _Report) -> list[str]:
    layout, marks = block.layout, block.marks
    lines = [f"-- {block.title} --"] if block.title else []
    if marks.legend and not block.bare:
        lines.append(marks.legend)
    inside: dict[str, list[FlowNode]] = {}
    for node in layout.nodes:
        inside.setdefault(node.parent, []).append(node)

    def add(parent: str, base: str) -> None:
        for node in sorted(inside.get(parent, []), key=lambda n: (n.branch, n.layer, n.column)):
            indent = base + "  " * node.layer
            lines.append(_text_line(node, layout, marks, indent))
            add(node.name, indent + "    ")

    add("", "")
    return lines


_DAY_LABELS = 8

_DAYS_SHOWN_ONE_BY_ONE = 14

_DAYS_A_WEEK = 7

_TEXT_DAY_BAR = 24


@dataclass
class _Days:
    """
    Executions a day, a bar each, the part that didn't succeed in red, the day failures jumped (a FailureJump)
    outlined, over CloudWatch's whole UTC days. Text draws a line a day, or a week when there are more than two weeks.
    """

    days: list[DayCount]
    jump: FailureJump | None = None
    title: str = ""


def _day_tip(day: DayCount) -> str:
    return f"{day_label(day.day)}: {day.started:,} started, {day.unsuccessful:,} didn't succeed (UTC day)"


def _labelled(index: int, count: int) -> bool:
    """
    Whether the index-th of count bars gets its date under it: every one up to _DAY_LABELS, else about _DAY_LABELS
    of them, the last always.
    """
    step = max(1, -(-count // _DAY_LABELS))
    return index == count - 1 or (count - 1 - index) % step == 0


@_draws(_Days, "html")
def _days_html(block: _Days, report: _Report) -> str:
    if not block.days:
        return ""
    busiest = max(max(d.started for d in block.days), 1)
    jump_day = block.jump.day if block.jump is not None else None
    bars, labels = [], []
    for i, day in enumerate(block.days):
        height = day.started / busiest * 100
        failed = min(day.unsuccessful, day.started) / day.started * 100 if day.started else 0
        red = f'<b style="height:{failed:.1f}%"></b>' if day.unsuccessful else ""
        jumped = " jz" if day.day == jump_day else ""
        bars.append(f'<div class="dz{jumped}" title="{_esc(_day_tip(day))}"><i style="height:{height:.1f}%">{red}'
                    "</i></div>")
        failures = f"<b>{day.unsuccessful:,}</b>" if day.unsuccessful and len(block.days) <= _DAY_LABELS else ""
        text = _esc(day_label(day.day)) if _labelled(i, len(block.days)) else ""
        labels.append(f"<span>{text}{failures}</span>")
    legend = (f'<span><i></i>started</span><span><i class="f"></i>didn\'t succeed</span>'
              f"<span>UTC days · the busiest {busiest:,}</span>")
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="dzs" style="--n:{len(block.days)}"><div class="dzb">{"".join(bars)}</div>'
            f'<div class="dzl">{"".join(labels)}</div><div class="dzk">{legend}</div></div>')


def _weeks(days: list[DayCount]) -> list[tuple[str, int, int]]:
    """
    Days summed into weeks counted back from the newest: (label, started, didn't succeed), oldest first.
    """
    weeks = []
    for end in range(len(days), 0, -_DAYS_A_WEEK):
        week = days[max(0, end - _DAYS_A_WEEK):end]
        label = f"{day_label(week[0].day)} to {day_label(week[-1].day)}"
        weeks.append((label, sum(d.started for d in week), sum(d.unsuccessful for d in week)))
    return weeks[::-1]


@_draws(_Days, "text")
def _days_text(block: _Days, report: _Report) -> list[str]:
    if not block.days:
        return []
    if len(block.days) <= _DAYS_SHOWN_ONE_BY_ONE:
        rows = [(day_label(d.day), d.started, d.unsuccessful) for d in block.days]
    else:
        rows = _weeks(block.days)
    busiest = max(max(started for _, started, _ in rows), 1)
    width = max(len(label) for label, _, _ in rows) + 2
    jump_label = day_label(block.jump.day) if block.jump is not None else ""
    lines = [f"-- {block.title} --"] if block.title else []
    for label, started, failed in rows:
        bar = "█" * round(started / busiest * _TEXT_DAY_BAR)
        mark = "  <- failures jumped" if label == jump_label else ""
        failures = f" · {failed:,} didn't succeed" if failed else ""
        lines.append(f"{label:{width}}{bar:{_TEXT_DAY_BAR}}  {started:,} started{failures}{mark}")
    lines.append("(UTC days)")
    return lines


_LABEL_FLIPS_AT = 0.6

_TEXT_FAILURES = 10


@dataclass
class _Changes:
    """
    What changed around one kind of failure, on one time axis: each change a dashed line with what it was, each
    failure a dot, the change just before the first failure (suspect) marked. Text lists them in time order.
    """

    changes: list[Change]
    failures: list[Execution]
    machine: str
    start: datetime
    end: datetime
    suspect: Change | None = None
    title: str = ""


def _timeline_left(moment: datetime, start: datetime, end: datetime) -> float:
    span = (end - start).total_seconds() or 1.0
    return max(0.0, min(1.0, (moment - start).total_seconds() / span)) * 100


@_draws(_Changes, "html")
def _changes_html(block: _Changes, report: _Report) -> str:
    marks = []
    for change in block.changes:
        left = _timeline_left(change.when, block.start, block.end)
        side = " r" if left > _LABEL_FLIPS_AT * 100 else ""
        suspect = " sus" if change is block.suspect else ""
        label = f"{change_text(change, block.machine)} · {_fmt_dt(change.when)}"
        marks.append(f'<i class="chl{side}{suspect}" style="left:{left:.2f}%"><em>{_esc(label)}</em></i>')
    for execution in block.failures:
        if execution.start is None:
            continue
        tip = f"{execution.name} failed, started {_fmt_dt(execution.start, seconds=True)}"
        marks.append(f'<b class="chd" style="left:{_timeline_left(execution.start, block.start, block.end):.2f}%" '
                     f'title="{_esc(tip)}"></b>')
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="chg"><div class="cht">{"".join(marks)}</div><div class="cha"><span>'
            f"{_esc(_fmt_dt(block.start))}</span><span>{_esc(_fmt_dt(block.end))}</span></div>"
            '<div class="chk"><span><i class="l"></i>a change</span><span><i class="d"></i>a failure of this kind'
            f"</span><span>times in {_esc(_tz_label())}</span></div></div>")


@_draws(_Changes, "text")
def _changes_text(block: _Changes, report: _Report) -> list[str]:
    events = [(c.when, f"◆ {change_text(c, block.machine)}" + ("  <- just before" if c is block.suspect else ""))
              for c in block.changes]
    dated = sorted((e for e in block.failures if e.start is not None), key=lambda e: e.start)
    events += [(e.start, f"✕ {e.name} failed") for e in dated[:_TEXT_FAILURES]]
    lines = [f"-- {block.title} --"] if block.title else []
    lines += [f"{_fmt_dt(when)}  {what}" for when, what in sorted(events, key=lambda event: event[0])]
    if len(dated) > _TEXT_FAILURES:
        lines.append(f"... and {len(dated) - _TEXT_FAILURES:,} more failures")
    return lines
