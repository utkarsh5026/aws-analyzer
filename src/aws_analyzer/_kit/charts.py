"""
Charts every service can put in a report: _Meter, an amount against a limit as one bar; _Split, a whole as one bar
of its largest parts; _Treemap, a whole as tiles sized by their parts; _Bars, an amount per day or hour; _Dots, one
mark per event over time, under a limit.

A chart is a block like any other, drawn in HTML and as lines of text. Its HTML is plain HTML and CSS, so it still
draws in a reopened notebook, and the rules it needs go in the report's own <style> (report.rules), so a service
adds nothing to its stylesheet to use one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .blocks import _Report, _draws
from .fmt import _plural
from .render import _prose
from .text import _clip, _esc, _text_bar

_SMALLEST_PX = 3
_TEXT_WIDTH = 40
_PARTS_SHOWN = 6
_SPLIT_LABEL_WIDTH = 60
_BAR_CHARACTERS = 20


@dataclass
class _Part:
    """
    One part of a meter's amount: what it is, how much, and that amount as people read it ('15.7 GB').
    """

    label: str
    amount: float
    text: str
    is_rest: bool = False


def _fold_parts(parts: list[_Part], shown: int, say: Callable[[float], str]) -> list[_Part]:
    """
    The largest `shown` parts as they are, then the rest as one part, "1,204 others", its amount said by `say`
    (human_money, human_size), so a whole of thousands of parts draws the same as one of five.
    """
    ordered = sorted(parts, key=lambda part: part.amount, reverse=True)
    kept, rest = ordered[:shown], ordered[shown:]
    if len(rest) < 2:
        return ordered
    amount = sum(part.amount for part in rest)
    return kept + [_Part(f"{len(rest):,} others", amount, say(amount), is_rest=True)]


@dataclass
class _Meter:
    """
    An amount against a limit as one bar: the parts that make up the amount side by side, a line where the limit
    is, and whatever runs past it in red, so "16.0 GB of 12.0 GB" reads as too much at a glance.

        parts        what the amount is made of, largest first is clearest; a part too small to see still gets a
                     sliver, and its label carries the real number
        limit        the most that fits, in the parts' unit
        limit_text   the limit as people read it, over its line ('12.0 GB')
        total_text   the amount as people read it ('16.0 GB')
        title        the heading over the meter
        note         a sentence under it (what running over means, what to do)
        mark         a point worth showing on the scale (a setting that would do), with mark_text beside it
    """

    parts: list[_Part]
    limit: float
    limit_text: str
    total_text: str
    title: str = ""
    note: str = ""
    mark: float | None = None
    mark_text: str = ""

    @property
    def total(self) -> float:
        """
        The parts added up.
        """
        return sum(part.amount for part in self.parts)

    @property
    def is_over(self) -> bool:
        """
        Whether the amount runs past the limit.
        """
        return self.limit > 0 and self.total > self.limit

    @property
    def scale(self) -> float:
        """
        What the whole width of the bar stands for: the limit, or the amount when it's more.
        """
        return max(self.total, self.limit, self.mark or 0) or 1.0


_METER_RULES = """\
.{root} .mtr{{margin:10px 0 14px;max-width:760px}}
.{root} .mtr-bar{{position:relative;display:flex;height:16px;margin:22px 0 6px;border-radius:4px;background:rgba(127,127,127,.16)}}
.{root} .mtr-bar>i{{display:block;height:100%;background:var(--info,#3b82f6);box-shadow:inset -1px 0 0 rgba(255,255,255,.55)}}
.{root} .mtr-bar>i:first-child{{border-radius:4px 0 0 4px}}
.{root} .mtr-bar>i:nth-child(2n){{opacity:.72}}
.{root} .mtr-over{{position:absolute;top:0;bottom:0;border-radius:0 4px 4px 0;background:repeating-linear-gradient(135deg,var(--bad,#ef4444) 0 5px,color-mix(in srgb,var(--bad,#ef4444) 75%,transparent) 5px 10px)}}
.{root} .mtr-line{{position:absolute;top:-6px;bottom:-6px;width:0;border-left:2px solid currentColor}}
.{root} .mtr-line>span{{position:absolute;bottom:100%;white-space:nowrap;font-size:11px;font-weight:600;transform:translateX(-50%)}}
.{root} .mtr-line.end>span{{transform:translateX(-100%)}}
.{root} .mtr-line.start>span{{transform:none}}
.{root} .mtr-mark{{border-left:2px dashed var(--warn,#d97706);color:var(--warn-fg,#92400e)}}
.{root} .mtr.over .mtr-line:not(.mtr-mark){{color:var(--bad-fg,#b91c1c)}}
.{root} .mtr-sum{{font-size:12.5px;font-variant-numeric:tabular-nums}}
.{root} .mtr.over .mtr-sum b{{color:var(--bad-fg,#b91c1c)}}
.{root} .mtr-keys{{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:4px;font-size:12px;opacity:.8}}
.{root} .mtr-keys i{{display:inline-block;width:9px;height:9px;margin-right:5px;border-radius:2px;background:var(--info,#3b82f6)}}
.{root} .mtr-keys span:nth-child(2n) i{{opacity:.72}}
.{root} .mtr-note{{margin-top:6px;font-size:12.5px}}
"""


def _meter_percent(amount: float, scale: float) -> float:
    """
    amount as a share of scale, in percent of the bar's width, kept on the bar.
    """
    return max(0.0, min(100.0, amount / scale * 100))


def _meter_line_class(percent: float) -> str:
    """
    Where a line's label sits, so it stays inside the bar: hung from its left near the start, its right near the end.
    """
    if percent > 85:
        return " end"
    if percent < 15:
        return " start"
    return ""


def _meter_line_html(percent: float, text: str, kind: str = "") -> str:
    """
    A vertical line across the bar at percent of its width, with its label above.
    """
    return (f'<span class="mtr-line{kind}{_meter_line_class(percent)}" style="left:{percent:.2f}%">'
            f"<span>{_esc(text)}</span></span>")


def _meter_parts_html(meter: _Meter) -> str:
    """
    The parts side by side, each as wide as its share of the scale, none narrower than a sliver.
    """
    scale = meter.scale
    return "".join(
        f'<i style="flex:0 1 {_meter_percent(part.amount, scale):.2f}%;min-width:{_SMALLEST_PX}px" '
        f'title="{_esc(part.label)}: {_esc(part.text)}"></i>'
        for part in meter.parts if part.amount > 0
    )


def _meter_keys_html(meter: _Meter) -> str:
    """
    A key under the bar naming each part and its amount, when there's more than one.
    """
    shown = [part for part in meter.parts if part.amount > 0]
    if len(shown) < 2:
        return ""
    keys = "".join(f"<span><i></i>{_esc(part.label)} <b>{_esc(part.text)}</b></span>"
                   for part in shown[:_PARTS_SHOWN])
    rest = len(shown) - _PARTS_SHOWN
    more = f"<span>and {rest} more</span>" if rest > 0 else ""
    return f'<div class="mtr-keys">{keys}{more}</div>'


def _meter_summary(meter: _Meter) -> str:
    """
    The meter's one line: the amount, the limit and the share, '16.0 GB of 12.0 GB (133%)'.
    """
    share = f" ({meter.total / meter.limit:.0%})" if meter.limit > 0 else ""
    return f"{meter.total_text} of {meter.limit_text}{share}"


@_draws(_Meter, "html")
def _meter_html(meter: _Meter, report: _Report) -> str:
    report.rules.add(_METER_RULES.format(root=report.root or "mtr-any"))
    scale = meter.scale
    limit_at = _meter_percent(meter.limit, scale)
    over = ""
    if meter.is_over:
        over = f'<span class="mtr-over" style="left:{limit_at:.2f}%;right:0"></span>'
    mark = ""
    if meter.mark is not None:
        mark = _meter_line_html(_meter_percent(meter.mark, scale), meter.mark_text, " mtr-mark")
    title = f"<h4>{_prose(meter.title)}</h4>" if meter.title else ""
    note = f'<div class="mtr-note">{_prose(meter.note)}</div>' if meter.note else ""
    tone = " over" if meter.is_over else ""
    return (f'{title}<div class="mtr{tone}"><div class="mtr-bar">{_meter_parts_html(meter)}{over}'
            f'{_meter_line_html(limit_at, meter.limit_text)}{mark}</div>'
            f'<div class="mtr-sum"><b>{_esc(_meter_summary(meter))}</b></div>{_meter_keys_html(meter)}{note}</div>')


def _text_meter(meter: _Meter) -> str:
    """
    The bar in characters: filled up to the amount, '|' at the limit, '!' for what runs past it.
    """
    scale = meter.scale
    limit_at = round(meter.limit / scale * _TEXT_WIDTH)
    reached = round(meter.total / scale * _TEXT_WIDTH)
    inside = "█" * min(reached, limit_at) + "░" * max(limit_at - reached, 0)
    past = "!" * max(reached - limit_at, 0)
    after = "░" * (_TEXT_WIDTH - limit_at - len(past))
    return f"[{inside}|{past}{after}]"


@_draws(_Meter, "text")
def _meter_lines(meter: _Meter, report: _Report) -> list[str]:
    out = ["", f"-- {meter.title} --"] if meter.title else [""]
    out.append(f"  {_text_meter(meter)}  {_meter_summary(meter)}")
    shown = [part for part in meter.parts if part.amount > 0]
    if len(shown) > 1:
        out.append("  " + " · ".join(f"{part.label} {part.text}" for part in shown[:_PARTS_SHOWN])
                   + (f" and {len(shown) - _PARTS_SHOWN} more" if len(shown) > _PARTS_SHOWN else ""))
    if meter.mark is not None and meter.mark_text:
        out.append(f"  {meter.mark_text}")
    if meter.note:
        out.append(f"  {meter.note}")
    return out


@dataclass
class _Split:
    """
    A whole as one bar of its parts, so "one query is 90% of the bill" is seen, not read.

        parts       the parts, largest first; _fold_parts keeps the largest few and folds the rest into one
        total_text  the whole as people read it ('$25.16')
        title       the heading over the bar
        note        a sentence under it
        with_keys   a key under the bar naming each part; a window whose lines already name them leaves it out
    """

    parts: list[_Part]
    total_text: str
    title: str = ""
    note: str = ""
    with_keys: bool = True

    @property
    def total(self) -> float:
        """
        The parts added up.
        """
        return sum(part.amount for part in self.parts)


_SPLIT_RULES = """\
.{root} .spl{{margin:10px 0 14px;max-width:760px}}
.{root} .spl-bar{{display:flex;height:16px;margin:6px 0;overflow:hidden;border-radius:4px;background:rgba(127,127,127,.16)}}
.{root} .spl-bar>i{{display:block;height:100%;box-shadow:inset -1px 0 0 rgba(255,255,255,.6)}}
.{root} .spl i.p0{{background:var(--warn,#d97706)}}
.{root} .spl i.p1{{background:var(--info,#3b82f6)}}
.{root} .spl i.p2{{background:color-mix(in srgb,var(--info,#3b82f6) 70%,transparent)}}
.{root} .spl i.p3{{background:color-mix(in srgb,var(--info,#3b82f6) 50%,transparent)}}
.{root} .spl i.p4{{background:color-mix(in srgb,var(--info,#3b82f6) 35%,transparent)}}
.{root} .spl i.p5{{background:rgba(127,127,127,.35)}}
.{root} .spl-keys{{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;gap:3px 10px;align-items:center;font-size:12px;font-variant-numeric:tabular-nums}}
.{root} .spl-keys i{{display:inline-block;width:9px;height:9px;border-radius:2px}}
.{root} .spl-keys .n{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.{root} .spl-keys .s{{opacity:.7;text-align:right}}
.{root} .spl-note{{margin-top:6px;font-size:12.5px}}
"""


def _split_colour(index: int, part: _Part) -> str:
    """
    A part's colour: the largest stands out, the next ones fade, and the folded rest is grey.
    """
    return "p5" if part.is_rest else f"p{min(index, 4)}"


@_draws(_Split, "html")
def _split_bar_html(split: _Split, report: _Report) -> str:
    report.rules.add(_SPLIT_RULES.format(root=report.root or "mtr-any"))
    total = split.total or 1.0
    bar, keys = [], []
    for index, part in enumerate(split.parts):
        colour = _split_colour(index, part)
        share = part.amount / total
        bar.append(f'<i class="{colour}" style="flex:0 1 {share * 100:.2f}%;min-width:{_SMALLEST_PX}px" '
                   f'title="{_esc(part.label)}: {_esc(part.text)} ({share:.0%})"></i>')
        keys.append(f'<i class="{colour}"></i><span class="n" title="{_esc(part.label)}">{_esc(part.label)}</span>'
                    f"<b>{_esc(part.text)}</b><span class=\"s\">{share:.0%}</span>")
    title = f"<h4>{_prose(split.title)}</h4>" if split.title else ""
    note = f'<div class="spl-note">{_prose(split.note)}</div>' if split.note else ""
    key = f'<div class="spl-keys">{"".join(keys)}</div>' if split.with_keys else ""
    return f'{title}<div class="spl"><div class="spl-bar">{"".join(bar)}</div>{key}{note}</div>'


@_draws(_Split, "text")
def _split_bar_lines(split: _Split, report: _Report) -> list[str]:
    out = ["", f"-- {split.title} --"] if split.title else [""]
    total = split.total or 1.0
    for part in split.parts:
        out.append(f"  {_text_bar(part.amount / total)}  {part.text:>9}  {_clip(part.label, _SPLIT_LABEL_WIDTH)}")
    out.append(f"  of {split.total_text}")
    if split.note:
        out.append(f"  {split.note}")
    return out


@dataclass
class _Bars:
    """
    An amount per day, hour or week as bars side by side, part of each in red when some of it went wrong.

        labels   each bar's place on the time axis ('Nov 9'); the first and last are written under the bars
        values   each bar's amount
        texts    each amount as people read it ('$3.20'), shown when the bar is pointed at and in text mode
        title    the heading over the bars
        bad      the part of each bar to draw in red (failed calls), in the same unit as values
        note     a sentence under them
    """

    labels: list[str]
    values: list[float]
    texts: list[str]
    title: str = ""
    bad: list[float] | None = None
    note: str = ""


_BARS_RULES = """\
.{root} .brs{{margin:10px 0 14px;max-width:760px}}
.{root} .brs-plot{{display:flex;align-items:flex-end;gap:2px;height:84px;padding-top:4px;border-bottom:1px solid rgba(127,127,127,.4)}}
.{root} .brs-plot>span{{display:flex;flex:1 1 0;flex-direction:column-reverse;min-width:2px;height:100%}}
.{root} .brs-plot>span:hover{{background:rgba(127,127,127,.1)}}
.{root} .brs-plot i{{display:block;flex:none;background:var(--info,#3b82f6);border-radius:2px 2px 0 0}}
.{root} .brs-plot i.bad{{background:var(--bad,#ef4444);border-radius:0}}
.{root} .brs-axis{{display:flex;justify-content:space-between;margin-top:3px;font-size:11px;opacity:.65}}
.{root} .brs-top{{display:block;text-align:right;font-size:11px;opacity:.65}}
.{root} .brs-note{{margin-top:6px;font-size:12.5px}}
"""


def _day_bar_html(label: str, text: str, height: float, bad_height: float) -> str:
    """
    One bar: its good part over its red part, together `height` percent of the plot, with its label and amount on
    hover.
    """
    good = max(height - bad_height, 0.0)
    red = f'<i class="bad" style="height:{bad_height:.2f}%"></i>' if bad_height > 0 else ""
    blue = f'<i style="height:{good:.2f}%;min-height:{1 if height > 0 else 0}px"></i>'
    return f'<span title="{_esc(label)}: {_esc(text)}">{red}{blue}</span>'


@_draws(_Bars, "html")
def _bars_chart_html(bars: _Bars, report: _Report) -> str:
    report.rules.add(_BARS_RULES.format(root=report.root or "mtr-any"))
    top = max(bars.values, default=0) or 1.0
    bad = bars.bad or [0.0] * len(bars.values)
    columns = "".join(_day_bar_html(label, text, value / top * 100, min(red, value) / top * 100)
                      for label, value, text, red in zip(bars.labels, bars.values, bars.texts, bad))
    peak = bars.texts[bars.values.index(max(bars.values))] if bars.values else ""
    title = f"<h4>{_prose(bars.title)}</h4>" if bars.title else ""
    axis = (f'<div class="brs-axis"><span>{_esc(bars.labels[0])}</span><span>{_esc(bars.labels[-1])}</span></div>'
            if bars.labels else "")
    note = f'<div class="brs-note">{_prose(bars.note)}</div>' if bars.note else ""
    return (f'{title}<div class="brs"><span class="brs-top">highest {_esc(peak)}</span>'
            f'<div class="brs-plot">{columns}</div>{axis}{note}</div>')


@_draws(_Bars, "text")
def _bars_chart_lines(bars: _Bars, report: _Report) -> list[str]:
    out = ["", f"-- {bars.title} --"] if bars.title else [""]
    top = max(bars.values, default=0) or 1.0
    width = max((len(label) for label in bars.labels), default=0)
    for label, value, text in zip(bars.labels, bars.values, bars.texts):
        filled = round(value / top * _BAR_CHARACTERS)
        out.append(f"  {label.ljust(width)}  {'█' * filled}{'░' * (_BAR_CHARACTERS - filled)}  {text}")
    if bars.note:
        out.append(f"  {bars.note}")
    return out


@dataclass
class _Tile:
    """
    Where a treemap's tile goes, in percent of the map's width and height.
    """

    left: float
    top: float
    width: float
    height: float


def _worst_ratio(row: list[float], side: float) -> float:
    """
    How far from square the most stretched tile of a row would be, laid along a side of this length.
    """
    total = sum(row)
    thickness = total / side
    return max(max(thickness * thickness / area, area / (thickness * thickness)) for area in row)


def _lay_row(row: list[float], box: _Tile, tiles: list[_Tile]) -> _Tile:
    """
    Lays a row of areas along the box's shorter side and returns the box that's left.
    """
    total = sum(row)
    if box.width >= box.height:
        thickness = total / box.height
        top = box.top
        for area in row:
            tiles.append(_Tile(box.left, top, thickness, area / thickness))
            top += area / thickness
        return _Tile(box.left + thickness, box.top, box.width - thickness, box.height)
    thickness = total / box.width
    left = box.left
    for area in row:
        tiles.append(_Tile(left, box.top, area / thickness, thickness))
        left += area / thickness
    return _Tile(box.left, box.top + thickness, box.width, box.height - thickness)


def _squarified(amounts: list[float], *, aspect: float = 1.0) -> list[_Tile]:
    """
    Tiles for amounts given largest first, filling a map `aspect` times as wide as it is high, each tile's area its
    share of the whole and as close to square as rows allow (Bruls, Huizing and van Wijk's _squarified layout), in
    percent of the map's width and height.
    """
    total = sum(amounts)
    if total <= 0:
        return []
    width, height = aspect, 1.0
    areas = [amount / total * width * height for amount in amounts]
    tiles: list[_Tile] = []
    box = _Tile(0.0, 0.0, width, height)
    row: list[float] = []
    for area in areas:
        side = min(box.width, box.height)
        if row and _worst_ratio(row + [area], side) > _worst_ratio(row, side):
            box = _lay_row(row, box, tiles)
            row = []
        row.append(area)
    if row:
        _lay_row(row, box, tiles)
    return [_Tile(t.left / width * 100, t.top / height * 100, t.width / width * 100, t.height / height * 100)
            for t in tiles]


@dataclass
class _Treemap:
    """
    A whole as tiles, each sized by its part, so what takes the space is seen at once: a folder's subfolders by
    bytes.

        parts       the parts, largest first; fold_parts keeps the largest few and folds the rest into one
        total_text  the whole as people read it ('4.3 TB')
        title       the heading over the map
        note        a sentence under it
    """

    parts: list[_Part]
    total_text: str
    title: str = ""
    note: str = ""


_TREEMAP_ASPECT = 3.4
_TILE_LABEL_SHARE = 0.025

_TREEMAP_RULES = """\
.{root} .tmap{{position:relative;max-width:760px;aspect-ratio:3.4;margin:8px 0 6px;border-radius:6px;overflow:hidden}}
.{root} .tmap>span{{position:absolute;box-sizing:border-box;overflow:hidden;padding:5px 7px;border:1.5px solid Canvas;border-radius:5px;font-size:11.5px;line-height:1.3;color:#fff;background:color-mix(in srgb,var(--info,#3b82f6) 80%,#0f172a)}}
.{root} .tmap>span:nth-child(n+2){{background:color-mix(in srgb,var(--info,#3b82f6) 64%,#0f172a)}}
.{root} .tmap>span:nth-child(n+4){{background:color-mix(in srgb,var(--info,#3b82f6) 52%,#0f172a)}}
.{root} .tmap>span:nth-child(n+7){{background:color-mix(in srgb,var(--info,#3b82f6) 42%,#0f172a)}}
.{root} .tmap>span.rest{{background:rgba(127,127,127,.35);color:inherit}}
.{root} .tmap b{{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.{root} .tmap i{{font-style:normal;opacity:.9;white-space:nowrap}}
.{root} .tmap-note{{font-size:12.5px;max-width:760px}}
"""


def _tile_html(part: _Part, tile: _Tile, share: float) -> str:
    """
    One tile, placed by percent, named and sized when it's big enough to hold the words; pointing at it says all.
    """
    place = (f"left:{tile.left:.3f}%;top:{tile.top:.3f}%;width:{tile.width:.3f}%;height:{tile.height:.3f}%")
    words = (f"<b>{_esc(part.label)}</b><i>{_esc(part.text)} · {share:.0%}</i>"
             if share >= _TILE_LABEL_SHARE else "")
    kind = ' class="rest"' if part.is_rest else ""
    return f'<span{kind} style="{place}" title="{_esc(part.label)}: {_esc(part.text)} ({share:.0%})">{words}</span>'


@_draws(_Treemap, "html")
def _treemap_html(treemap: _Treemap, report: _Report) -> str:
    report.rules.add(_TREEMAP_RULES.format(root=report.root or "mtr-any"))
    shown = [part for part in treemap.parts if part.amount > 0]
    total = sum(part.amount for part in shown) or 1.0
    tiles = _squarified([part.amount for part in shown], aspect=_TREEMAP_ASPECT)
    body = "".join(_tile_html(part, tile, part.amount / total) for part, tile in zip(shown, tiles))
    title = f"<h4>{_prose(treemap.title)}</h4>" if treemap.title else ""
    note = f'<div class="tmap-note">{_prose(treemap.note)}</div>' if treemap.note else ""
    return f'{title}<div class="tmap">{body}</div>{note}'


_TEXT_TILES = 4


@_draws(_Treemap, "text")
def _treemap_lines(treemap: _Treemap, report: _Report) -> list[str]:
    total = sum(part.amount for part in treemap.parts) or 1.0
    named = [f"{part.label} {part.amount / total:.0%}" for part in treemap.parts[:_TEXT_TILES]]
    rest = len(treemap.parts) - _TEXT_TILES
    if rest > 0:
        named.append(f"{rest} more")
    out = [f"{treemap.title}: " if treemap.title else "", f"  of {treemap.total_text}: " + " · ".join(named)]
    out = [""] + [line for line in out if line]
    return out + ([f"  {treemap.note}"] if treemap.note else [])


@dataclass
class _Point:
    """
    One event on a _Dots chart: when (any number that grows with time, seconds say), its amount, its tone ('' for
    an ordinary one, 'bad', 'warn' or 'note') and what pointing at it says.
    """

    at: float
    value: float
    tone: str = ""
    label: str = ""


@dataclass
class _Dots:
    """
    Events over time, a dot each, under a dashed line at their limit, so "three runs sat on the timeout" is seen.
    Past `most` dots it draws a band instead, the typical and the highest per slice of time, keeping only the events
    with a tone as dots (the newest `most`), so a million runs draw as fast as a hundred and still show the ones
    that matter.

        points      the events
        top         the top of the scale, in the points' unit (the limit, or the highest value when more)
        top_text    the top as people read it ('60 s')
        start_text  the left end of the time axis as people read it; end_text the right
        limit       the line to draw (a timeout), in the points' unit; limit_text its label
        tones       what each tone means, for the key ('bad': 'failed', 'note': 'cold start')
        title, note a heading over the chart and a sentence under it
        noun        what one event is ('run'), for the key and text mode
        zoom        draw the scale only up to this amount (zoom_text its label), with the points above it and the
                    limit on a strip along the top, so runs of seconds under a 15-minute timeout don't lie flat
    """

    points: list[_Point]
    top: float
    top_text: str
    start_text: str
    end_text: str
    limit: float | None = None
    limit_text: str = ""
    tones: dict[str, str] | None = None
    title: str = ""
    note: str = ""
    noun: str = "event"
    most: int = 300
    zoom: float | None = None
    zoom_text: str = ""

    @property
    def is_banded(self) -> bool:
        """
        Whether there are too many events for a dot each.
        """
        return len(self.points) > self.most

    @property
    def is_zoomed(self) -> bool:
        """
        Whether the scale stops below the top, the higher points drawn on a strip above it.
        """
        return self.zoom is not None and 0 < self.zoom < self.top


_PLOT_WIDTH = 760
_PLOT_HEIGHT = 150
_PLOT_PAD = 6
_BAND_SLICES = 60
_STRIP_Y = 11
_ZOOM_TOP = 34
_DOT_RADIUS = 2.6
_TONED_RADIUS = 3.4

_DOTS_RULES = """\
.{root} .dts{{max-width:760px;margin:8px 0 14px}}
.{root} .dts svg{{display:block;width:100%;height:auto;overflow:visible}}
.{root} .dts .ax{{stroke:currentColor;stroke-opacity:.35;stroke-width:1}}
.{root} .dts .lim{{stroke:var(--bad,#ef4444);stroke-width:1.2;stroke-dasharray:4 4}}
.{root} .dts text{{font-size:11px;fill:currentColor;fill-opacity:.65}}
.{root} .dts text.lim-t{{fill:var(--bad-fg,#b91c1c);fill-opacity:1}}
.{root} .dts circle{{fill:var(--info,#3b82f6);fill-opacity:.75}}
.{root} .dts circle.t-bad,.{root} .dts .key i.t-bad{{fill:var(--bad,#ef4444);background:var(--bad,#ef4444);fill-opacity:1}}
.{root} .dts circle.t-warn,.{root} .dts .key i.t-warn{{fill:var(--warn,#d97706);background:var(--warn,#d97706);fill-opacity:1}}
.{root} .dts circle.t-note,.{root} .dts .key i.t-note{{fill:#8b5cf6;background:#8b5cf6;fill-opacity:1}}
.{root} .dts .over{{fill:var(--bad,#ef4444);fill-opacity:.08}}
.{root} .dts .brk{{stroke:currentColor;stroke-opacity:.3;stroke-dasharray:2 3}}
.{root} .dts .band{{fill:var(--info,#3b82f6);fill-opacity:.22}}
.{root} .dts .mid{{fill:none;stroke:var(--info,#3b82f6);stroke-width:1.6}}
.{root} .dts .key{{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:4px;font-size:12px;opacity:.85}}
.{root} .dts .key i{{display:inline-block;width:8px;height:8px;margin-right:5px;border-radius:50%;background:var(--info,#3b82f6)}}
.{root} .dts-note{{margin-top:6px;font-size:12.5px}}
"""


def _plot_x(at: float, start: float, span: float) -> float:
    """
    Where a moment goes across the plot.
    """
    return _PLOT_PAD + (at - start) / span * (_PLOT_WIDTH - 2 * _PLOT_PAD)


def _plot_y(value: float, top: float) -> float:
    """
    Where an amount goes up the plot (0 at the bottom).
    """
    share = max(0.0, min(1.0, value / top)) if top > 0 else 0.0
    return _PLOT_HEIGHT - share * (_PLOT_HEIGHT - 2 * _PLOT_PAD) - _PLOT_PAD


def _dots_y(dots: _Dots, value: float) -> float:
    """
    Where an amount goes up a _Dots plot: on its scale, or with a zoom, on the zoomed scale or the strip above it.
    """
    if not dots.is_zoomed:
        return _plot_y(value, dots.top)
    zoom = dots.zoom or 1.0
    if value > zoom:
        return _STRIP_Y
    bottom = _PLOT_HEIGHT - _PLOT_PAD
    return bottom - max(0.0, value) / zoom * (bottom - _ZOOM_TOP)


def _middle(values: list[float]) -> float:
    """
    The median of some amounts.
    """
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _band_paths(dots: _Dots, start: float, span: float) -> str:
    """
    The typical (median) amount per slice of time as a line, over a band up to the highest, for too many points.
    """
    slices: list[list[float]] = [[] for _ in range(_BAND_SLICES)]
    for point in dots.points:
        index = min(int((point.at - start) / span * _BAND_SLICES), _BAND_SLICES - 1)
        slices[index].append(point.value)
    filled = [(i, values) for i, values in enumerate(slices) if values]
    xs = [_plot_x(start + (i + 0.5) / _BAND_SLICES * span, start, span) for i, _ in filled]
    highs = [_dots_y(dots, max(values)) for _, values in filled]
    middles = [_dots_y(dots, _middle(values)) for _, values in filled]
    upper = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, highs))
    lower = " ".join(f"{x:.1f},{y:.1f}" for x, y in reversed(list(zip(xs, middles))))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, middles))
    return f'<polygon class="band" points="{upper} {lower}"/><polyline class="mid" points="{line}"/>'


def _dot_html(point: _Point, x: float, y: float) -> str:
    """
    One event's dot, with what it is when pointed at.
    """
    radius = _TONED_RADIUS if point.tone else _DOT_RADIUS
    kind = f' class="t-{_esc(point.tone)}"' if point.tone else ""
    tip = f"<title>{_esc(point.label)}</title>" if point.label else ""
    return f'<circle{kind} cx="{x:.1f}" cy="{y:.1f}" r="{radius}">{tip}</circle>'


def _dots_key(dots: _Dots) -> str:
    """
    The key under the plot: what a plain dot is (or the band), then each tone that's drawn, with how many.
    """
    counts = {tone: sum(1 for p in dots.points if p.tone == tone) for tone in (dots.tones or {})}
    plain = f"typical {dots.noun}, and up to the highest, per slice of time" if dots.is_banded else f"a {dots.noun}"
    keys = [f"<span><i></i>{_esc(plain)}</span>"]
    keys += [f'<span><i class="t-{_esc(tone)}"></i>{_esc(meaning)} <b>{counts[tone]:,}</b></span>'
             for tone, meaning in (dots.tones or {}).items() if counts[tone]]
    return f'<div class="key">{"".join(keys)}</div>'


def _limit_line(dots: _Dots) -> str:
    """
    The dashed line at the limit, with its label.
    """
    if dots.limit is None:
        return ""
    y = _plot_y(dots.limit, dots.top)
    return (f'<line class="lim" x1="0" x2="{_PLOT_WIDTH}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="lim-t" x="{_PLOT_WIDTH}" y="{y - 4:.1f}" text-anchor="end">{_esc(dots.limit_text)}</text>')


def _zoom_marks(dots: _Dots) -> str:
    """
    A zoomed plot's strip along the top, for the points above the zoom, and the break under it, labelled with the
    zoom's amount on the left and what the strip holds (the limit) on the right.
    """
    strip = (f'<rect class="over" x="0" y="{_STRIP_Y - 8}" width="{_PLOT_WIDTH}" height="16" rx="4"/>'
             f'<text class="lim-t" x="{_PLOT_WIDTH}" y="{_ZOOM_TOP - 4}" text-anchor="end">'
             f'↑ {_esc(dots.limit_text or "above the scale")}</text>')
    brk = (f'<line class="brk" x1="0" x2="{_PLOT_WIDTH}" y1="{_ZOOM_TOP}" y2="{_ZOOM_TOP}"/>'
           f'<text x="0" y="{_ZOOM_TOP - 4}">{_esc(dots.zoom_text)}</text>')
    return strip + brk


@_draws(_Dots, "html")
def _dots_html(dots: _Dots, report: _Report) -> str:
    report.rules.add(_DOTS_RULES.format(root=report.root or "mtr-any"))
    start = min((p.at for p in dots.points), default=0.0)
    span = (max((p.at for p in dots.points), default=0.0) - start) or 1.0
    shown = dots.points
    marks = ""
    if dots.is_banded:
        marks = _band_paths(dots, start, span)
        shown = [p for p in dots.points if p.tone][-dots.most:]
    marks += "".join(_dot_html(p, _plot_x(p.at, start, span), _dots_y(dots, p.value)) for p in shown)
    limit = _zoom_marks(dots) if dots.is_zoomed else _limit_line(dots)
    axis = (f'<line class="ax" x1="0" x2="{_PLOT_WIDTH}" y1="{_PLOT_HEIGHT}" y2="{_PLOT_HEIGHT}"/>'
            f'<text x="0" y="{_PLOT_HEIGHT + 14}">{_esc(dots.start_text)}</text>'
            f'<text x="{_PLOT_WIDTH}" y="{_PLOT_HEIGHT + 14}" text-anchor="end">{_esc(dots.end_text)}</text>')
    title = f"<h4>{_prose(dots.title)}</h4>" if dots.title else ""
    note = f'<div class="dts-note">{_prose(dots.note)}</div>' if dots.note else ""
    svg = (f'<svg viewBox="0 -4 {_PLOT_WIDTH} {_PLOT_HEIGHT + 22}" role="img" '
           f'aria-label="{_esc(dots.title or "events over time")}">{limit}{marks}{axis}</svg>')
    return f'{title}<div class="dts">{svg}{_dots_key(dots)}{note}</div>'


@_draws(_Dots, "text")
def _dots_lines(dots: _Dots, report: _Report) -> list[str]:
    out = ["", f"-- {dots.title} --"] if dots.title else [""]
    counted = [f"{_plural(len(dots.points), dots.noun)} from {dots.start_text} to {dots.end_text}"]
    for tone, meaning in (dots.tones or {}).items():
        count = sum(1 for p in dots.points if p.tone == tone)
        if count:
            counted.append(f"{count:,} {meaning}")
    out.append("  " + " · ".join(counted))
    if dots.note:
        out.append(f"  {dots.note}")
    return out
