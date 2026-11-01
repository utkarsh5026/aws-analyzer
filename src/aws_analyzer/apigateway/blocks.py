"""
The API Gateway reports' own blocks, with their drawings: _Path (where a route sends a request: the caller, the
authorizer, the validation, then the integration, left to right, the step that fails marked) and _TimeSplit (where a
call's time went: the authorizer, the integration and API Gateway itself, as bars on one axis).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._kit.blocks import _Report, _draws
from .._kit.fmt import _duration
from .._kit.text import _esc


_TEXT_BAR = 40


@dataclass
class _Step:
    """
    One box of a route's path: what it is (kind), what it says (title, under it a detail), and a tone ('bad', 'warn'
    or '') when it's the step that fails.
    """

    kind: str
    title: str
    detail: str = ""
    tone: str = ""


@dataclass
class _Path:
    """
    Where a route sends a request, as boxes left to right.
    """

    steps: list[_Step]
    title: str = ""


@dataclass
class _TimeSplit:
    """
    Where a call's time went: (part, milliseconds) bars on one axis as long as the whole call, the integration's
    marked `tone` when it's the problem. limit_ms: API Gateway's time limit, drawn as a line when it's reached.
    """

    parts: list[tuple[str, float]]
    total_ms: float
    tone: str = ""
    title: str = ""
    limit_ms: float | None = None
    notes: list[str] = field(default_factory=list)


@_draws(_Path, "html")
def _route_path_html(block: _Path, report: _Report) -> str:
    boxes = []
    for step in block.steps:
        detail = f"<span>{_esc(step.detail)}</span>" if step.detail else ""
        boxes.append(f'<div class="pb {step.tone}"><i>{_esc(step.kind)}</i><b>{_esc(step.title)}</b>{detail}</div>')
    title = f'<div class="tt">{_esc(block.title)}</div>' if block.title else ""
    return f'{title}<div class="pp">{"<em>→</em>".join(boxes)}</div>'


@_draws(_Path, "text")
def _route_path_text(block: _Path, report: _Report) -> list[str]:
    lines = [block.title] if block.title else []
    parts = []
    for step in block.steps:
        mark = " (!)" if step.tone else ""
        parts.append(f"[{step.kind}: {step.title}{' · ' + step.detail if step.detail else ''}{mark}]")
    return [*lines, " → ".join(parts), ""]


def _ms(ms: float | None) -> str:
    """
    Milliseconds as people read them: '120 ms' under a second, '2.4s' or '1m 05s' above.
    """
    if ms is None:
        return "-"
    return f"{ms:,.0f} ms" if ms < 1000 else _duration(ms / 1000)


@_draws(_TimeSplit, "html")
def _split_html(block: _TimeSplit, report: _Report) -> str:
    if not block.parts or block.total_ms <= 0:
        return ""
    rows, start = [], 0.0
    for part, ms in block.parts:
        tone = block.tone if part == "integration" else ""
        left, width = start / block.total_ms * 100, ms / block.total_ms * 100
        rows.append(f'<div class="tsr"><span>{_esc(part)}</span><div class="tsl"><i class="tsb {tone}" '
                    f'style="left:{left:.2f}%;width:max(2px,{width:.2f}%)"></i></div><span class="tsm {tone}">'
                    f"{_esc(_ms(ms))}</span></div>")
        start += ms
    title = f'<div class="tt">{_esc(block.title)}</div>' if block.title else ""
    notes = "".join(f'<div class="tsn">{_esc(note)}</div>' for note in block.notes)
    return f'{title}<div class="ts">{"".join(rows)}{notes}</div>'


@_draws(_TimeSplit, "text")
def _split_text(block: _TimeSplit, report: _Report) -> list[str]:
    if not block.parts or block.total_ms <= 0:
        return []
    lines = [block.title] if block.title else []
    start = 0.0
    for part, ms in block.parts:
        begin = round(start / block.total_ms * _TEXT_BAR)
        size = max(1, round(ms / block.total_ms * _TEXT_BAR))
        lines.append(f"  {part:<12} {' ' * begin}{'█' * size}{' ' * max(0, _TEXT_BAR - begin - size)} {_ms(ms)}")
        start += ms
    return [*lines, *(f"  {note}" for note in block.notes), ""]
