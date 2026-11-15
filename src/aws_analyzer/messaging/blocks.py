"""
The messaging reports' own block: _HopMap, follow()'s map drawn as boxes and arrows, left to right from what sends to
it, with a hop that has a problem outlined in its colour and the arrow into it dashed. Inline SVG, no JavaScript;
text mode leaves it out, since the table of hops under it says the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from .._kit.blocks import _Report, _draws
from .._kit.render import _prose
from .._kit.text import _clip, _esc

from .analysis.layout import MapLayout, MapNode, map_layout
from .models import Hop

_BOX_WIDTH = 200
_BOX_HEIGHT = 54
_COLUMN_GAP = 64
_ROW_PITCH = 66
_NAME_CHARACTERS = 26
_LINE_CHARACTERS = 32
_ARROW = 5

_KIND_WORDS = {
    "SQS queue": "SQS QUEUE", "SNS topic": "SNS TOPIC", "EventBridge rule": "EVENTBRIDGE RULE",
    "Lambda function": "LAMBDA", "EventBridge bus": "EVENT BUS",
}

_HOP_MAP_RULES = """\
.{root} .hmap{{max-width:100%;overflow-x:auto;margin:8px 0 14px}}
.{root} .hmap svg{{display:block;height:auto}}
.{root} .hmap rect{{fill:var(--surface,rgba(127,127,127,.06));stroke:rgba(127,127,127,.45);stroke-width:1}}
.{root} .hmap rect.start{{stroke:var(--info,#3b82f6);stroke-width:2}}
.{root} .hmap rect.bad{{stroke:var(--bad,#ef4444);stroke-width:1.6;fill:color-mix(in srgb,var(--bad,#ef4444) 7%,transparent)}}
.{root} .hmap rect.warn{{stroke:var(--warn,#d97706);stroke-width:1.6;fill:color-mix(in srgb,var(--warn,#d97706) 7%,transparent)}}
.{root} .hmap rect.more{{stroke-dasharray:4 3;fill:none}}
.{root} .hmap text{{fill:currentColor;font-size:12.5px}}
.{root} .hmap text.k{{font-size:9.5px;letter-spacing:.06em;fill-opacity:.6}}
.{root} .hmap text.n{{font-weight:600}}
.{root} .hmap text.d{{font-size:11px;fill-opacity:.7}}
.{root} .hmap text.bad{{fill:var(--bad-fg,#b91c1c);fill-opacity:1}}
.{root} .hmap text.warn{{fill:var(--warn-fg,#92400e);fill-opacity:1}}
.{root} .hmap path{{fill:none;stroke:currentColor;stroke-opacity:.45;stroke-width:1.3}}
.{root} .hmap path.bad{{stroke:var(--bad,#ef4444);stroke-opacity:1;stroke-dasharray:5 4}}
.{root} .hmap path.warn{{stroke:var(--warn,#d97706);stroke-opacity:1;stroke-dasharray:5 4}}
.{root} .hmap path.head{{fill:currentColor;fill-opacity:.45;stroke:none}}
.{root} .hmap path.head.bad{{fill:var(--bad,#ef4444);fill-opacity:1}}
.{root} .hmap path.head.warn{{fill:var(--warn,#d97706);fill-opacity:1}}
"""


@dataclass
class _HopMap:
    """
    follow()'s hops as a diagram: where a message goes, box by box (the box it starts from outlined).
    """

    hops: list[Hop]
    title: str = ""


def _box_left(node: MapNode) -> float:
    return node.column * (_BOX_WIDTH + _COLUMN_GAP)


def _box_top(node: MapNode) -> float:
    return node.row * _ROW_PITCH


def _box_lines(hop: Hop) -> tuple[str, str, str, str]:
    """
    A hop's box in words: its kind, its name, and a last line, its problem in its tone or else its numbers.
    """
    kind = _KIND_WORDS.get(hop.kind, hop.kind.upper())
    last, tone = (hop.problem, hop.tone) if hop.problem else (hop.numbers, "")
    return kind, _clip(hop.name, _NAME_CHARACTERS), _clip(last, _LINE_CHARACTERS), tone


def _hop_box(node: MapNode, *, is_start: bool) -> str:
    """
    One box: a hop's kind, name and last line, or for a folded box how many more there are.
    """
    left, top = _box_left(node), _box_top(node)
    if node.hop is None:
        return (f'<rect class="more" x="{left}" y="{top}" width="{_BOX_WIDTH}" height="{_BOX_HEIGHT}" rx="7"/>'
                f'<text class="d" x="{left + 12}" y="{top + 31}">+{node.folded:,} more, without problems</text>')
    hop = node.hop
    kind, name, last, tone = _box_lines(hop)
    tip = " · ".join(part for part in (hop.kind, hop.name, hop.via, hop.numbers, hop.problem) if part)
    box_class = "start" if is_start else hop.tone
    rect_class = f' class="{_esc(box_class)}"' if box_class else ""
    last_class = f"d {tone}" if tone else "d"
    return (f"<g><title>{_esc(tip)}</title>"
            f'<rect{rect_class} x="{left}" y="{top}" width="{_BOX_WIDTH}" height="{_BOX_HEIGHT}" rx="7"/>'
            f'<text class="k" x="{left + 12}" y="{top + 15}">{_esc(kind)}</text>'
            f'<text class="n" x="{left + 12}" y="{top + 31}">{_esc(name)}</text>'
            f'<text class="{last_class}" x="{left + 12}" y="{top + 46}">{_esc(last)}</text></g>')


def _arrow(start: MapNode, end: MapNode, tone: str) -> str:
    """
    An arrow from the right of one box to the left of the next, bending between them, its head a path.
    """
    x1, y1 = _box_left(start) + _BOX_WIDTH, _box_top(start) + _BOX_HEIGHT / 2
    x2, y2 = _box_left(end) - 1, _box_top(end) + _BOX_HEIGHT / 2
    bend = (x2 - x1) / 2
    kind = f' class="{_esc(tone)}"' if tone else ""
    head_kind = f"head {_esc(tone)}" if tone else "head"
    return (f'<path{kind} d="M{x1:.1f},{y1:.1f} C{x1 + bend:.1f},{y1:.1f} {x2 - bend:.1f},{y2:.1f} '
            f'{x2 - _ARROW:.1f},{y2:.1f}"/>'
            f'<path class="{head_kind}" d="M{x2 - _ARROW:.1f},{y2 - _ARROW:.1f} L{x2:.1f},{y2:.1f} '
            f'L{x2 - _ARROW:.1f},{y2 + _ARROW:.1f} Z"/>')


def _map_svg(layout: MapLayout) -> str:
    start = layout.nodes[layout.start]
    arrows = "".join(_arrow(layout.nodes[e.start], layout.nodes[e.end], e.tone) for e in layout.edges)
    boxes = "".join(_hop_box(node, is_start=node is start) for node in layout.nodes)
    width = layout.columns * (_BOX_WIDTH + _COLUMN_GAP) - _COLUMN_GAP + 2
    height = (layout.rows - 1) * _ROW_PITCH + _BOX_HEIGHT + 2
    return (f'<svg viewBox="-1 -1 {width} {height}" width="{width}" role="img" '
            f'aria-label="Where a message goes, box by box">{arrows}{boxes}</svg>')


@_draws(_HopMap, "html")
def _hop_map_html(block: _HopMap, report: _Report) -> str:
    layout = map_layout(block.hops)
    if not layout.nodes:
        return ""
    report.rules.add(_HOP_MAP_RULES.format(root=report.root or "msg"))
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return f'{title}<div class="hmap">{_map_svg(layout)}</div>'


@_draws(_HopMap, "text")
def _hop_map_lines(block: _HopMap, report: _Report) -> list[str]:
    return []
