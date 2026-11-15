"""
Charts every service can put in a report: _Meter, an amount against a limit as one bar; _Split, a whole as one bar
of its largest parts; _Bars, an amount per day or hour.

A chart is a block like any other, drawn in HTML and as lines of text. Its HTML is plain HTML and CSS, so it still
draws in a reopened notebook, and the rules it needs go in the report's own <style> (report.rules), so a service
adds nothing to its stylesheet to use one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .blocks import _Report, _draws
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
