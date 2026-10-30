"""
_Wiring (what calls a function and where its results go) and _Columns, with their drawings.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._kit.blocks import _Report, _draws
from ..._kit.render import _TONES, _prose
from ..._kit.text import _esc

from ..helpers import human_ms


@dataclass
class _Wiring:
    """
    How a function is wired, left to right: what calls it, the function, and where its results, failed events and
    logs go. A diagram in HTML, a line for each side in text.
    """

    inputs: list[tuple[str, str, str, str]]  # (kind, name, detail, tone 'warn' | 'bad' | '')
    center: tuple[str, str]  # (the function's name, what it runs)
    outputs: list[tuple[str, str, str, str]]  # (what goes there, where, detail, tone)
    title: str = ""
    empty: str = "Nothing calls it on its own"  # said in the inputs column when there are none


@dataclass
class _Columns:
    """
    Numbers over time, a column each (a day, or an hour), with part of each column in a second colour: errors in
    red, or a column's average under its longest. A row of columns in HTML, a sparkline in text. `mark` is the index
    of a column to draw a dashed line through, labelled `mark_label` (when the function was last changed).
    """

    items: list[tuple[str, float, float, str]]  # (label, the column's value, the part in the second colour, tooltip)
    title: str = ""
    part: str = "bad"  # the part's colour: 'bad' (red: errors) or 'dim' (the average under the longest)
    limit: float | None = None  # a dashed line at this value (a timeout), drawn when the columns come near it
    limit_label: str = ""
    unit: str = ""  # 'ms' shows the scale as run times; '' as counts
    mark: int | None = None
    mark_label: str = ""


@_draws(_Wiring, "html")
def _wiring_html(block: _Wiring, report: _Report) -> str:
    def node(kind: str, name: str, detail: str, tone: str) -> str:
        return (f'<div class="wn {tone if tone in _TONES else ""}"><span class="wk">{_esc(kind)}</span>'
                f"<b>{_esc(name or '-')}</b>" + (f'<span class="wd">{_prose(detail)}</span>' if detail else "") + "</div>")

    inputs = "".join(node(*item) for item in block.inputs) or f'<div class="wn none">{_esc(block.empty)}</div>'
    outputs = "".join(node(*item) for item in block.outputs)
    name, detail = block.center
    center = (f'<div class="wf"><span class="wl">λ</span><b>{_esc(name)}</b>'
              + (f'<span class="wd">{_esc(detail)}</span>' if detail else "") + "</div>")
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return (f'{title}<div class="wire"><div class="wc">{inputs}</div><div class="wa">→</div><div class="wc">{center}'
            f'</div><div class="wa">→</div><div class="wc">{outputs}</div></div>')


@_draws(_Columns, "html")
def _columns_html(block: _Columns, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    if not block.items:
        return title + '<div class="more">(none)</div>'
    tallest = max(value for _, value, _, _ in block.items)
    shown_limit = block.limit is not None and tallest >= 0.5 * block.limit
    top = max(tallest, block.limit or 0) if shown_limit else tallest
    top = top or 1
    late = block.mark is not None and block.mark >= len(block.items) / 2
    marker = f'<u class="mk{" late" if late else ""}"><span>{_esc(block.mark_label)}</span></u>'
    bars = "".join(
        f'<i style="height:{max(value / top * 100, 1.5 if value else 0):.1f}%" title="{_esc(tip)}">'
        + (f'<b style="height:{min(100.0, part / value * 100):.1f}%"></b>' if value and part else "")
        + (marker if index == block.mark else "") + "</i>"
        for index, (_, value, part, tip) in enumerate(block.items)
    )
    scale = human_ms(tallest) if block.unit == "ms" else f"{tallest:,.0f}"
    limit = ""
    if shown_limit and block.limit is not None:
        limit = (f'<span class="lim" style="bottom:{block.limit / top * 100:.1f}%"><span>'
                 f"{_esc(block.limit_label)}</span></span>")
    labels = [block.items[0][0], block.items[len(block.items) // 2][0], block.items[-1][0]]
    axis = "".join(f"<span>{_esc(label)}</span>" for label in (labels if len(block.items) > 2 else labels[::2]))
    return (f'{title}<div class="colbars{" dim" if block.part == "dim" else ""}"><span class="top">{_esc(scale)}</span>'
            f'{limit}{bars}</div><div class="colx">{axis}</div>')


@_draws(_Wiring, "text")
def _wiring_lines(block: _Wiring, report: _Report) -> list[str]:
    calls = "; ".join(f"{kind} {name}".strip() + (" (!)" if tone in ("warn", "bad") else "")
                      for kind, name, _, tone in block.inputs) or block.empty
    name, detail = block.center
    goes = "; ".join(f"{what}: {where}" + (" (!)" if tone in ("warn", "bad") else "")
                     for what, where, _, tone in block.outputs)
    return (["", f"-- {block.title} --"] if block.title else [""]) + [
        f"  Called by: {calls}", f"  Runs:      {name}" + (f" ({detail})" if detail else ""), f"  Then:      {goes}"]


@_draws(_Columns, "text")
def _columns_lines(block: _Columns, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    if not block.items:
        return out + ["(none)"]
    tallest = max(value for _, value, _, _ in block.items) or 1
    spark = "".join(" ▁▂▃▄▅▆▇█"[min(8, round(value / tallest * 8))] for _, value, _, _ in block.items)
    label, value = max(((label, value) for label, value, _, _ in block.items), key=lambda lv: lv[1])
    most = human_ms(value) if block.unit == "ms" else f"{value:,.0f}"
    lines = [f"  {block.items[0][0]} {spark} {block.items[-1][0]}   (the most: {most}, {label})"]
    if block.mark is not None and 0 <= block.mark < len(block.items):
        lines.append(f"  {' ' * len(block.items[0][0])} {' ' * block.mark}^ {block.mark_label}")
    return out + lines
