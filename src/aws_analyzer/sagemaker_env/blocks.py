"""
The SageMaker reports' own blocks, with their drawings: _PipelineGraph (a pipeline run as a graph of its steps,
coloured by how each went) and _StepWaterfall (the steps that ran, on the run's time axis).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._kit.blocks import _Report, _draws
from .._kit.fmt import _duration
from .._kit.render import _prose
from .._kit.text import _clip, _esc

from .analysis.pipelines import STEP_HEIGHT, STEP_WIDTH, StepBar, StepLayout, StepNode


_ARROW = 6

_LABEL_CHARS = 24

_TEXT_BAR = 44

_TICKS = 4

_STATUS_WORDS = {"succeeded": "succeeded", "failed": "failed", "executing": "running", "starting": "starting",
                 "stopped": "stopped", "stopping": "stopping", "cached": "reused a cached result", "": "never ran"}

_TEXT_MARKS = {"succeeded": "█", "failed": "X", "executing": "▒", "starting": "▒", "stopped": "s", "stopping": "s",
               "cached": "·"}


@dataclass
class _PipelineGraph:
    """
    A pipeline run as a graph: a box per step (its name, type and how long it took), an arrow from each step to
    the ones waiting for it, each box coloured by its status. An inline SVG in HTML, with a legend; a list of steps
    and what each waits for in text.
    """

    layout: StepLayout
    title: str = ""
    notes: dict[str, str] = field(default_factory=dict)


@dataclass
class _StepWaterfall:
    """
    The steps that ran, each a bar on the run's time axis, coloured by status. HTML draws a time scale and a legend;
    text draws the bars with characters.
    """

    bars: list[StepBar]
    seconds: float
    title: str = ""


def _status(node: StepNode) -> str:
    return "cached" if node.step.cached else node.step.status.lower()


def _arrowhead(x: float, y: float) -> str:
    return f"M{x:.1f} {y:.1f} l{-_ARROW / 1.4:.1f} {-_ARROW - 2} h{_ARROW * 1.43:.1f} z"


def _edge_svg(source: StepNode, target: StepNode, label: str, offset: float) -> str:
    x1, y1 = source.middle + offset, source.y + STEP_HEIGHT
    x2, y2 = target.middle, target.y
    bend = max(16.0, (y2 - y1) / 2)
    path = f"M{x1:.1f} {y1:.1f} C{x1:.1f} {y1 + bend:.1f} {x2:.1f} {y2 - bend:.1f} {x2:.1f} {y2 - _ARROW - 2:.1f}"
    anchor, at = ("end", x1 - 8) if x2 < x1 else ("start", x1 + 8)
    tag = (f'<text class="pl" x="{at:.1f}" y="{y1 + 14:.1f}" text-anchor="{anchor}">{_esc(label)}</text>'
           if label else "")
    return f'<g class="pe"><path d="{path}"/><path class="ph" d="{_arrowhead(x2, y2)}"/>{tag}</g>'


def _node_svg(node: StepNode, note: str) -> str:
    step, status = node.step, _status(node)
    seconds = step.seconds()
    timing = "cached" if step.cached else _duration(seconds) if seconds is not None and step.end else ""
    sub = " · ".join(filter(None, [step.type, timing, note]))
    tip = " · ".join(filter(None, [step.name, step.type, _STATUS_WORDS.get(status, status), step.job[1], note]))
    return (f'<g class="pn s-{_esc(status or "none")}"><title>{_esc(tip)}</title>'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{STEP_WIDTH}" height="{STEP_HEIGHT}" rx="9"/>'
            f'<text class="pnm" x="{node.x + 12:.1f}" y="{node.y + 19:.1f}">{_esc(_clip(step.name, _LABEL_CHARS))}'
            f'</text><text class="pns" x="{node.x + 12:.1f}" y="{node.y + 35:.1f}">'
            f"{_esc(_clip(sub, _LABEL_CHARS + 8))}</text></g>")


def _offsets(layout: StepLayout) -> dict[tuple[str, str], float]:
    offsets = {}
    for node in layout.nodes:
        down = [e for e in layout.edges if e.source == node.step.name]
        for i, edge in enumerate(down):
            offsets[(edge.source, edge.target)] = (i - (len(down) - 1) / 2) * 18
    return offsets


@_draws(_PipelineGraph, "html")
def _graph_html(block: _PipelineGraph, report: _Report) -> str:
    layout = block.layout
    if not layout.nodes:
        return ""
    nodes = {n.step.name: n for n in layout.nodes}
    offsets = _offsets(layout)
    edges = "".join(_edge_svg(nodes[e.source], nodes[e.target], e.label, offsets.get((e.source, e.target), 0.0))
                    for e in layout.edges if e.source in nodes and e.target in nodes)
    body = "".join(_node_svg(n, block.notes.get(n.step.name, "")) for n in layout.nodes)
    used = list(dict.fromkeys(_status(n) for n in layout.nodes))
    legend = "".join(f'<span><i class="s-{_esc(s or "none")}"></i>{_esc(_STATUS_WORDS.get(s, s))}</span>' for s in used)
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="pg"><div class="pgk">{legend}</div><svg viewBox="0 0 {layout.width:.0f} '
            f'{layout.height:.0f}" width="{layout.width:.0f}" role="img" aria-label="{_esc(block.title or "pipeline")}">'
            f"{edges}{body}</svg></div>")


@_draws(_PipelineGraph, "text")
def _graph_text(block: _PipelineGraph, report: _Report) -> list[str]:
    lines = [""] + ([f"-- {block.title} --"] if block.title else [])
    for node in sorted(block.layout.nodes, key=lambda n: (n.layer, n.column)):
        step = node.step
        status = _STATUS_WORDS.get(_status(node), step.status)
        waits = [f"{e.source}{' (' + e.label + ')' if e.label else ''}" for e in block.layout.edges
                 if e.target == step.name]
        after = f" <- {', '.join(waits)}" if waits else ""
        note = block.notes.get(step.name, "")
        lines.append(f"{'  ' * node.layer}{step.name} ({step.type or '?'}) [{status}]{' · ' + note if note else ''}"
                     f"{after}")
    return lines


def _scale(seconds: float) -> list[str]:
    return [_duration(seconds * i / _TICKS) if i else "0s" for i in range(_TICKS + 1)]


@_draws(_StepWaterfall, "html")
def _waterfall_html(block: _StepWaterfall, report: _Report) -> str:
    if not block.bars:
        return ""
    ticks = "".join(f"<span>{_esc(t)}</span>" for t in _scale(block.seconds))
    rows = []
    for bar in block.bars:
        shown = _esc(bar.label) if bar.width > 0.12 else ""
        rows.append(f'<div class="pwr"><div class="pwn" title="{_esc(bar.type)}">{_esc(bar.name)}'
                    f'<span>{_esc(bar.type)}</span></div><div class="pwl"><i class="pws s-{_esc(bar.status)}" '
                    f'style="left:{bar.start * 100:.2f}%;width:max(2px,{bar.width * 100:.2f}%)" '
                    f'title="{_esc(bar.label)}">{shown}</i></div></div>')
    title = f'<div class="tt">{_prose(block.title)}</div>' if block.title else ""
    return (f'{title}<div class="pw"><div class="pwr pwh"><div class="pwn">Step</div><div class="pwt">{ticks}</div>'
            f'</div>{"".join(rows)}</div>')


@_draws(_StepWaterfall, "text")
def _waterfall_text(block: _StepWaterfall, report: _Report) -> list[str]:
    if not block.bars:
        return []
    width = max(len(bar.name) for bar in block.bars) + 2
    lines = [""] + ([f"-- {block.title} --"] if block.title else [])
    total = _duration(block.seconds)
    lines.append(f"{'':{width}}0s{' ' * (_TEXT_BAR - 2 - len(total))}{total}")
    for bar in block.bars:
        cells = [" "] * _TEXT_BAR
        first = int(bar.start * (_TEXT_BAR - 1))
        last = max(first, int((bar.start + bar.width) * (_TEXT_BAR - 1)))
        for i in range(first, min(last, _TEXT_BAR - 1) + 1):
            cells[i] = _TEXT_MARKS.get(bar.status, "#")
        lines.append(f"{bar.name:{width}}{''.join(cells)}  {bar.label}")
    return lines
