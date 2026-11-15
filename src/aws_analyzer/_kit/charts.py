"""
Charts every service can put in a report: _Meter, an amount against a limit as one bar.

A chart is a block like any other, drawn in HTML and as lines of text. Its HTML is plain HTML and CSS, so it still
draws in a reopened notebook, and the rules it needs go in the report's own <style> (report.rules), so a service
adds nothing to its stylesheet to use one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .blocks import _Report, _draws
from .render import _prose
from .text import _esc

_SMALLEST_PX = 3
_TEXT_WIDTH = 40
_PARTS_SHOWN = 6


@dataclass
class _Part:
    """
    One part of a meter's amount: what it is, how much, and that amount as people read it ('15.7 GB').
    """

    label: str
    amount: float
    text: str


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


def _percent(amount: float, scale: float) -> float:
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
        f'<i style="flex:0 1 {_percent(part.amount, scale):.2f}%;min-width:{_SMALLEST_PX}px" '
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
    limit_at = _percent(meter.limit, scale)
    over = ""
    if meter.is_over:
        over = f'<span class="mtr-over" style="left:{limit_at:.2f}%;right:0"></span>'
    mark = ""
    if meter.mark is not None:
        mark = _meter_line_html(_percent(meter.mark, scale), meter.mark_text, " mtr-mark")
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
