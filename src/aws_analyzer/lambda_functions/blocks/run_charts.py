"""
performance()'s charts, each drawn in HTML and in text: _RunTimeline (every run by when it ran, the ones that timed
out in a lane of their own, the typical-to-slow band and the runs per slice under it), _RunHistogram (how long finished
runs take, and the gap up to the timeout), _ColdWarm (a cold start's start-up beside a warm run) and _Spark (a
table cell: a line of values, memory run after run).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..._kit.blocks import _Report, _draws
from ..._kit.charts import _BAR_CHARACTERS
from ..._kit.fmt import _in_zone, _plural
from ..._kit.render import _prose
from ..._kit.text import _esc

from ..helpers import human_ms
from ..analysis.performance import ColdWarm, RunTimeBin, TimeSlice, _nice_step
from ..analysis.text import _window


_SPARK_CHARACTERS = "▁▂▃▄▅▆▇█"
_AXIS_STEPS = 4
_MOST_TIME_TICKS = 6
_TICK_SPACINGS = (60, 300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800)
_DAY_SECONDS = 86400

_WIDTH = 760
_LEFT = 46
_RIGHT = 10
_LANE_HEIGHT = 22
_LANE_GAP = 16
_RUNS_PLOT_HEIGHT = 150
_AXIS_TEXT = 15
_COUNTS_GAP = 14
_COUNTS_HEIGHT = 30
_DOT = 2.8
_MARKED_DOT = 3.4
_CROSS = 4

_SPARK_WIDTH = 120
_SPARK_HEIGHT = 18

_CHART_RULES = """\
.{root} .rtl{{max-width:760px;margin:6px 0 14px}}
.{root} .rtl svg{{display:block;width:100%;height:auto;overflow:visible}}
.{root} .rtl text{{font-size:11px;fill:currentColor;fill-opacity:.62}}
.{root} .rtl .grid{{stroke:currentColor;stroke-opacity:.13}}
.{root} .rtl .ax{{stroke:currentColor;stroke-opacity:.35}}
.{root} .rtl .lane{{fill:var(--bad,#ef4444);fill-opacity:.09}}
.{root} .rtl text.lane-t{{fill:var(--bad-fg,#b91c1c);fill-opacity:1}}
.{root} .rtl .brk{{fill:none;stroke:currentColor;stroke-opacity:.45}}
.{root} .rtl .band{{fill:var(--info,#3b82f6);fill-opacity:.14}}
.{root} .rtl .mid{{fill:none;stroke:var(--info,#3b82f6);stroke-width:1.6;stroke-opacity:.9}}
.{root} .rtl circle{{fill:var(--info,#3b82f6);fill-opacity:.7}}
.{root} .rtl circle.cold{{fill:#8b5cf6;fill-opacity:.95}}
.{root} .rtl circle.failed{{fill:var(--bad,#ef4444);fill-opacity:1}}
.{root} .rtl path.timeout{{stroke:var(--bad,#ef4444);stroke-width:2}}
.{root} .rtl rect.cnt{{fill:currentColor;fill-opacity:.22}}
.{root} .rtl .key{{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:4px;font-size:12px;opacity:.85}}
.{root} .rtl .key i{{display:inline-block;width:8px;height:8px;margin-right:5px;border-radius:50%;background:var(--info,#3b82f6)}}
.{root} .rtl .key i.cold{{background:#8b5cf6}}
.{root} .rtl .key i.failed,.{root} .rtl .key i.timeout{{background:var(--bad,#ef4444)}}
.{root} .rtl .key i.band{{border-radius:2px;background:var(--info,#3b82f6);opacity:.35}}
.{root} .rtl-note,.{root} .rhg-note,.{root} .cwb-note{{margin-top:6px;font-size:12.5px;opacity:.85}}
.{root} .rhg{{max-width:760px;margin:6px 0 14px}}
.{root} .rhg-plot{{display:flex;align-items:flex-end;gap:3px;height:120px;border-bottom:1px solid rgba(127,127,127,.4)}}
.{root} .rhg-plot>span{{display:flex;flex:1 1 0;flex-direction:column;justify-content:flex-end;align-items:stretch;height:100%;min-width:14px}}
.{root} .rhg-plot b{{font-size:11px;font-weight:600;text-align:center;opacity:.8}}
.{root} .rhg-plot i{{display:block;background:var(--info,#3b82f6);border-radius:2px 2px 0 0;opacity:.75}}
.{root} .rhg-plot .to i{{background:var(--bad,#ef4444);opacity:1}}
.{root} .rhg-plot .to b{{color:var(--bad-fg,#b91c1c)}}
.{root} .rhg-plot .gap{{justify-content:center;text-align:center;font-size:11px;opacity:.55;flex-grow:1.4}}
.{root} .rhg-axis{{display:flex;gap:3px;margin-top:3px;font-size:11px;opacity:.65}}
.{root} .rhg-axis>span{{flex:1 1 0;min-width:14px;text-align:center}}
.{root} .rhg-axis .gap{{flex-grow:1.4}}
.{root} .rhg-axis .to{{color:var(--bad-fg,#b91c1c);opacity:1}}
.{root} .cwb{{max-width:760px;margin:6px 0 14px}}
.{root} .cwb-row{{display:grid;grid-template-columns:44px minmax(0,1fr) 150px;gap:10px;align-items:center;margin:5px 0;font-size:12px}}
.{root} .cwb-bar{{display:flex;height:20px;border-radius:4px;overflow:hidden;min-width:2px}}
.{root} .cwb-bar i{{display:block;font:600 11px/20px var(--lmb-mono,monospace);font-style:normal;color:#fff;padding:0 6px;white-space:nowrap;overflow:hidden;min-width:0}}
.{root} .cwb-bar .run{{background:var(--info,#3b82f6)}}
.{root} .cwb-bar .up{{background:#8b5cf6}}
.{root} .cwb-v{{font-variant-numeric:tabular-nums}}
.{root} svg.spk{{display:block;width:{spark_width}px;height:{spark_height}px;overflow:visible}}
.{root} svg.spk polyline{{fill:none;stroke:var(--info,#3b82f6);stroke-width:1.5}}
.{root} svg.spk.warn polyline{{stroke:var(--warn,#d97706);stroke-width:1.8}}
.{root} svg.spk circle{{fill:var(--info,#3b82f6)}}
.{root} svg.spk.warn circle{{fill:var(--warn,#d97706)}}
"""


def _add_rules(report: _Report) -> None:
    report.rules.add(_CHART_RULES.format(root=report.root or "mtr-any", spark_width=_SPARK_WIDTH,
                                         spark_height=_SPARK_HEIGHT))


def _sparkline(values: list[float]) -> str:
    """
    Values as a line of block characters, lowest to highest: [1, 5, 3] -> '▁█▄'.
    """
    if not values:
        return ""
    low, high = min(values), max(values)
    spread = (high - low) or 1.0
    top = len(_SPARK_CHARACTERS) - 1
    return "".join(_SPARK_CHARACTERS[round((value - low) / spread * top)] for value in values)


def _count_bar(amount: float, most: float) -> str:
    filled = round(amount / most * _BAR_CHARACTERS) if most else 0
    if amount and not filled:
        filled = 1
    return "█" * filled + "░" * (_BAR_CHARACTERS - filled)


@dataclass
class _RunMark:
    """
    One run on the timeline: when it ran, how long it took, what kind of dot it is ('run', 'cold', 'failed' or
    'timeout') and what pointing at it says.
    """

    at: datetime
    duration: float
    kind: str
    label: str


@dataclass
class _RunTimeline:
    """
    Every run by when it ran and how long it took. The runs that finished are drawn to their own scale, so one
    timeout doesn't squash the rest into a line; the ones that timed out are crosses in a lane above. A band shows
    the typical-to-slow run time (median to 1 in 10) per slice of time, and bars under it the runs per slice. Past
    `most` runs only the cold, failed and timed-out ones stay dots.
    """

    marks: list[_RunMark]
    slices: list[TimeSlice]
    counts: list[int]
    start: datetime
    end: datetime
    timeout_s: int
    title: str = ""
    note: str = ""
    most: int = 300

    @property
    def finished(self) -> list[_RunMark]:
        return [mark for mark in self.marks if mark.kind != "timeout"]

    @property
    def timeouts(self) -> list[_RunMark]:
        return [mark for mark in self.marks if mark.kind == "timeout"]

    @property
    def slice_seconds(self) -> float:
        return (self.end - self.start).total_seconds() / max(len(self.counts), 1)


@dataclass
class _Plot:
    """
    Where a timeline's parts go: its time span, the top of its run-time scale, and the y of each band.
    """

    start: datetime
    seconds: float
    top_ms: float
    step_ms: float
    plot_top: float
    plot_bottom: float
    counts_top: float
    height: float

    def x(self, moment: datetime) -> float:
        share = (moment - self.start).total_seconds() / self.seconds if self.seconds else 0.0
        return _LEFT + min(max(share, 0.0), 1.0) * (_WIDTH - _LEFT - _RIGHT)

    def y(self, ms: float) -> float:
        share = min(max(ms / self.top_ms, 0.0), 1.0) if self.top_ms else 0.0
        return self.plot_bottom - share * (self.plot_bottom - self.plot_top)


def _plot_for(timeline: _RunTimeline) -> _Plot:
    longest = max((mark.duration for mark in timeline.finished), default=0.0) or 1.0
    step = _nice_step(longest, _AXIS_STEPS)
    top = math.ceil(longest / step) * step
    plot_top = (_LANE_HEIGHT + _LANE_GAP) if timeline.timeouts else 6.0
    plot_bottom = plot_top + _RUNS_PLOT_HEIGHT
    counts_top = plot_bottom + _AXIS_TEXT + _COUNTS_GAP
    height = counts_top + (_COUNTS_HEIGHT if timeline.counts else 0)
    seconds = (timeline.end - timeline.start).total_seconds()
    return _Plot(timeline.start, seconds, top, step, plot_top, plot_bottom, counts_top, height)


def _scale_text(ms: float) -> str:
    """
    A run-time scale's tick: '0', '250 ms', '2.5 s', '10 s', '2 min'.
    """
    if ms == 0:
        return "0"
    if ms < 1000:
        return f"{ms:g} ms"
    if ms < 120_000:
        return f"{ms / 1000:g} s"
    return f"{ms / 60_000:g} min"


def _grid_svg(plot: _Plot) -> str:
    lines = []
    steps = round(plot.top_ms / plot.step_ms)
    for i in range(steps + 1):
        y = plot.y(i * plot.step_ms)
        kind = "ax" if i == 0 else "grid"
        lines.append(f'<line class="{kind}" x1="{_LEFT}" x2="{_WIDTH - _RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="{_LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">{_esc(_scale_text(i * plot.step_ms))}'
                     "</text>")
    return "".join(lines)


def _tick_text(moment: datetime, seconds: float) -> str:
    shown = _in_zone(moment)
    return shown.strftime("%H:%M") if seconds <= 2 * _DAY_SECONDS else shown.strftime("%b %d").replace(" 0", " ")


def _time_ticks(plot: _Plot) -> str:
    spacing = next((s for s in _TICK_SPACINGS if plot.seconds / s <= _MOST_TIME_TICKS), _TICK_SPACINGS[-1])
    first = math.ceil(plot.start.timestamp() / spacing) * spacing
    ticks = []
    moment = datetime.fromtimestamp(first, plot.start.tzinfo)
    while (moment - plot.start).total_seconds() <= plot.seconds:
        x = plot.x(moment)
        ticks.append(f'<line class="ax" x1="{x:.1f}" x2="{x:.1f}" y1="{plot.plot_bottom}" y2="{plot.plot_bottom + 4}"/>'
                     f'<text x="{x:.1f}" y="{plot.plot_bottom + _AXIS_TEXT}" text-anchor="middle">'
                     f"{_esc(_tick_text(moment, plot.seconds))}</text>")
        moment += timedelta(seconds=spacing)
    return "".join(ticks)


def _lane_svg(timeline: _RunTimeline, plot: _Plot) -> str:
    if not timeline.timeouts:
        return ""
    middle = _LANE_HEIGHT / 2
    crosses = "".join(
        f'<path class="timeout" d="M{plot.x(m.at) - _CROSS:.1f},{middle - _CROSS:.1f}l{2 * _CROSS},{2 * _CROSS}'
        f'm0,{-2 * _CROSS}l{-2 * _CROSS},{2 * _CROSS}"><title>{_esc(m.label)}</title></path>'
        for m in timeline.timeouts)
    gap_y = plot.plot_top - _LANE_GAP / 2
    zigzag = f'<path class="brk" d="M{_LEFT + 4},{gap_y + 3} l4,-5 l4,5 l4,-5 l4,5"/>'
    return (f'<rect class="lane" x="{_LEFT}" y="0" width="{_WIDTH - _LEFT - _RIGHT}" height="{_LANE_HEIGHT}" rx="4"/>'
            f'<text class="lane-t" x="{_LEFT - 6}" y="{middle + 4}" text-anchor="end">{timeline.timeout_s} s</text>'
            f'<text class="lane-t" x="{_LEFT + 6}" y="{middle + 4}">timed out · {len(timeline.timeouts):,}</text>'
            f"{crosses}{zigzag}")


def _band_svg(timeline: _RunTimeline, plot: _Plot) -> str:
    if len(timeline.slices) < 2:
        return ""
    centres = [plot.x(piece.start + (piece.end - piece.start) / 2) for piece in timeline.slices]
    highs = [f"{x:.1f},{plot.y(piece.p90_ms):.1f}" for x, piece in zip(centres, timeline.slices)]
    middles = [f"{x:.1f},{plot.y(piece.median_ms):.1f}" for x, piece in zip(centres, timeline.slices)]
    return (f'<polygon class="band" points="{" ".join(highs)} {" ".join(reversed(middles))}"/>'
            f'<polyline class="mid" points="{" ".join(middles)}"/>')


def _dots_svg(timeline: _RunTimeline, plot: _Plot) -> str:
    finished = timeline.finished
    if len(timeline.marks) > timeline.most:
        finished = [mark for mark in finished if mark.kind != "run"][-timeline.most:]
    dots = []
    for mark in finished:
        radius = _DOT if mark.kind == "run" else _MARKED_DOT
        kind = "" if mark.kind == "run" else f' class="{mark.kind}"'
        dots.append(f'<circle{kind} cx="{plot.x(mark.at):.1f}" cy="{plot.y(mark.duration):.1f}" r="{radius}">'
                    f"<title>{_esc(mark.label)}</title></circle>")
    return "".join(dots)


def _counts_svg(timeline: _RunTimeline, plot: _Plot) -> str:
    most = max(timeline.counts, default=0)
    if not most:
        return ""
    width = (_WIDTH - _LEFT - _RIGHT) / len(timeline.counts)
    bars = []
    for i, count in enumerate(timeline.counts):
        height = count / most * (_COUNTS_HEIGHT - 4)
        x = _LEFT + i * width
        when = timeline.start + timedelta(seconds=i * timeline.slice_seconds)
        bars.append(f'<rect class="cnt" x="{x + 1:.1f}" y="{plot.counts_top + _COUNTS_HEIGHT - height:.1f}" '
                    f'width="{max(width - 2, 1):.1f}" height="{height:.1f}" rx="1.5"><title>'
                    f"{_esc(_tick_text(when, plot.seconds))}: {_plural(count, 'run')}</title></rect>")
    labels = (f'<text x="{_LEFT - 6}" y="{plot.counts_top + 9}" text-anchor="end">{most:,}</text>'
              f'<text x="{_LEFT - 6}" y="{plot.counts_top + _COUNTS_HEIGHT}" text-anchor="end">runs</text>')
    return "".join(bars) + labels


def _timeline_key(timeline: _RunTimeline) -> str:
    counted = {kind: sum(1 for mark in timeline.marks if mark.kind == kind) for kind in ("cold", "failed", "timeout")}
    words = {"cold": "cold start", "failed": "failed", "timeout": "timed out"}
    keys = ["<span><i></i>a run</span>"]
    keys += [f'<span><i class="{kind}"></i>{words[kind]} <b>{count:,}</b></span>'
             for kind, count in counted.items() if count]
    if len(timeline.slices) >= 2:
        keys.append(f'<span><i class="band"></i>median to 1 in 10, every {_window(timeline.slice_seconds)}</span>')
    return f'<div class="key">{"".join(keys)}</div>'


@_draws(_RunTimeline, "html")
def _run_timeline_html(timeline: _RunTimeline, report: _Report) -> str:
    _add_rules(report)
    plot = _plot_for(timeline)
    parts = (_lane_svg(timeline, plot) + _grid_svg(plot) + _band_svg(timeline, plot) + _dots_svg(timeline, plot)
             + _time_ticks(plot) + _counts_svg(timeline, plot))
    svg = (f'<svg viewBox="0 0 {_WIDTH} {plot.height:.0f}" role="img" '
           f'aria-label="{_esc(timeline.title or "runs over time")}">{parts}</svg>')
    title = f"<h4>{_prose(timeline.title)}</h4>" if timeline.title else ""
    note = f'<div class="rtl-note">{_prose(timeline.note)}</div>' if timeline.note else ""
    return f'{title}<div class="rtl">{svg}{_timeline_key(timeline)}{note}</div>'


@_draws(_RunTimeline, "text")
def _run_timeline_lines(timeline: _RunTimeline, report: _Report) -> list[str]:
    out = ["", f"-- {timeline.title} --"] if timeline.title else [""]
    counted = [f"{_plural(len(timeline.marks), 'run')} from {_tick_text(timeline.start, 3 * _DAY_SECONDS)} "
               f"{_tick_text(timeline.start, 0)} to {_tick_text(timeline.end, 3 * _DAY_SECONDS)} "
               f"{_tick_text(timeline.end, 0)}"]
    for kind, word in (("timeout", "timed out"), ("failed", "failed"), ("cold", "cold starts")):
        count = sum(1 for mark in timeline.marks if mark.kind == kind)
        if count:
            counted.append(f"{count:,} {word}")
    out.append("  " + " · ".join(counted))
    every = _window(timeline.slice_seconds)
    medians = [piece.median_ms for piece in timeline.slices]
    if len(medians) >= 2:
        out.append(f"  median run time  {_sparkline(medians)}  {human_ms(min(medians))} to {human_ms(max(medians))}, "
                   f"every {every}")
    if len(timeline.counts) >= 2:
        out.append(f"  runs             {_sparkline([float(c) for c in timeline.counts])}  "
                   f"{min(timeline.counts):,} to {max(timeline.counts):,} every {every}")
    if timeline.timeouts:
        moments = ", ".join(_tick_text(mark.at, 0) for mark in timeline.timeouts[:8])
        more = f" and {len(timeline.timeouts) - 8:,} more" if len(timeline.timeouts) > 8 else ""
        out.append(f"  timed out at     {moments}{more}")
    if timeline.note:
        out.append(f"  {timeline.note}")
    return out


@dataclass
class _RunHistogram:
    """
    How many finished runs took how long, a bar per bin of a round width, then the stretch up to the timeout that no
    run reached (when there's one) and a red bar for the runs that timed out.
    """

    bins: list[RunTimeBin]
    timeouts: int
    timeout_s: int
    title: str = ""
    note: str = ""

    @property
    def gap(self) -> tuple[float, float] | None:
        """
        From the end of the last bin to the timeout, when runs timed out and none came close: (from, to) in ms.
        """
        if not self.bins or not self.timeouts:
            return None
        end, limit = self.bins[-1].high, self.timeout_s * 1000.0
        return (end, limit) if end < limit else None


def _bin_text(ms: float, *, unit: bool) -> str:
    """
    A bin's edge: in seconds from a second up ('2' or '2 s'), else in ms.
    """
    if ms >= 1000:
        number = f"{ms / 1000:g}"
        return f"{number} s" if unit else number
    return f"{ms:g} ms" if unit else f"{ms:g}"


def _bin_label(low: float, high: float) -> str:
    return f"{_bin_text(low, unit=high < 1000)} to {_bin_text(high, unit=True)}"


@_draws(_RunHistogram, "html")
def _run_histogram_html(histogram: _RunHistogram, report: _Report) -> str:
    _add_rules(report)
    most = max([b.runs for b in histogram.bins] + [histogram.timeouts], default=0) or 1
    columns, axis = [], []
    for piece in histogram.bins:
        height = piece.runs / most * 82
        count = f"<b>{piece.runs:,}</b>" if piece.runs else ""
        columns.append(f'<span title="{_esc(_bin_label(piece.low, piece.high))}: {_plural(piece.runs, "run")}">'
                       f'{count}<i style="height:{height:.1f}%"></i></span>')
        axis.append(f"<span>{_esc(_bin_text(piece.low, unit=False))}</span>")
    gap = histogram.gap
    if gap:
        columns.append(f'<span class="gap" title="{_esc(_bin_label(*gap))}: no run">none up to the timeout</span>')
        axis.append(f'<span class="gap">{_esc(_bin_text(gap[0], unit=False))}+</span>')
    if histogram.timeouts:
        height = histogram.timeouts / most * 82
        columns.append(f'<span class="to" title="timed out at {histogram.timeout_s} s: '
                       f'{_plural(histogram.timeouts, "run")}"><b>{histogram.timeouts:,}</b>'
                       f'<i style="height:{height:.1f}%"></i></span>')
        axis.append(f'<span class="to">{histogram.timeout_s} s</span>')
    unit = "seconds" if histogram.bins and histogram.bins[-1].high >= 1000 else "ms"
    title = f"<h4>{_prose(histogram.title)}</h4>" if histogram.title else ""
    note = f'<div class="rhg-note">{_prose(histogram.note)}</div>' if histogram.note else ""
    return (f'{title}<div class="rhg"><div class="rhg-plot">{"".join(columns)}</div>'
            f'<div class="rhg-axis">{"".join(axis)}</div><div class="rhg-note">run time, in {unit} →</div>{note}</div>')


@_draws(_RunHistogram, "text")
def _run_histogram_lines(histogram: _RunHistogram, report: _Report) -> list[str]:
    out = ["", f"-- {histogram.title} --"] if histogram.title else [""]
    most = max([b.runs for b in histogram.bins] + [histogram.timeouts], default=0)
    rows = [(_bin_label(b.low, b.high), _count_bar(b.runs, most), f"{b.runs:,}") for b in histogram.bins]
    if histogram.gap:
        rows.append((_bin_label(*histogram.gap), " " * _BAR_CHARACTERS, "none"))
    if histogram.timeouts:
        rows.append((f"timed out ({histogram.timeout_s} s)", _count_bar(histogram.timeouts, most),
                     f"{histogram.timeouts:,} (!)"))
    width = max((len(label) for label, _, _ in rows), default=0)
    out += [f"  {label.ljust(width)}  {bar}  {count}" for label, bar, count in rows]
    if histogram.note:
        out.append(f"  {histogram.note}")
    return out


@dataclass
class _ColdWarm:
    """
    A warm run beside a cold start, each its median: the cold one's start-up and its run, one bar each, to the same
    scale.
    """

    numbers: ColdWarm
    title: str = ""
    note: str = ""


def _cold_warm_rows(block: _ColdWarm) -> list[tuple[str, float, float, str]]:
    numbers = block.numbers
    cold_total = numbers.start_up_median_ms + numbers.cold_median_ms
    return [
        ("warm", 0.0, numbers.warm_median_ms, f"{human_ms(numbers.warm_median_ms)} · {_plural(numbers.warm, 'run')}"),
        ("cold", numbers.start_up_median_ms, numbers.cold_median_ms,
         f"{human_ms(cold_total)} · {_plural(numbers.cold, 'run')}"),
    ]


@_draws(_ColdWarm, "html")
def _cold_warm_html(block: _ColdWarm, report: _Report) -> str:
    _add_rules(report)
    rows = _cold_warm_rows(block)
    longest = max(start + run for _, start, run, _ in rows) or 1.0
    lines = []
    for label, start, run, said in rows:
        start_part = f'<i class="up" style="flex:{start:.0f}">start-up</i>' if start else ""
        bar = f'{start_part}<i class="run" style="flex:{run:.0f}">run</i>'
        lines.append(f'<div class="cwb-row"><span>{label}</span><span><span class="cwb-bar" '
                     f'style="width:{(start + run) / longest * 100:.1f}%">{bar}</span></span>'
                     f'<span class="cwb-v">{_esc(said)}</span></div>')
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    note = f'<div class="cwb-note">{_prose(block.note)}</div>' if block.note else ""
    return f'{title}<div class="cwb">{"".join(lines)}{note}</div>'


@_draws(_ColdWarm, "text")
def _cold_warm_lines(block: _ColdWarm, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    rows = _cold_warm_rows(block)
    longest = max(start + run for _, start, run, _ in rows) or 1.0
    for label, start, run, said in rows:
        starting = round(start / longest * _BAR_CHARACTERS)
        running = max(round(run / longest * _BAR_CHARACTERS), 1)
        out.append(f"  {label}  {('░' * starting + '█' * running).ljust(_BAR_CHARACTERS)}  {said}")
    out.append("  (░ starting up, █ the run itself; medians)")
    if block.note:
        out.append(f"  {block.note}")
    return out


@dataclass
class _Spark:
    """
    A table cell holding a line of values (the memory each run used in a container, oldest first), drawn as a small
    line in HTML and as block characters in text; tone 'warn' draws it in amber.
    """

    values: list[float] = field(default_factory=list)
    tone: str = ""

    def __str__(self) -> str:
        return _sparkline(self.values)


@_draws(_Spark, "cell")
def _spark_cell_html(cell: _Spark, text: str) -> str:
    values = cell.values
    if len(values) < 2:
        return _esc(text)
    low, high = min(values), max(values)
    spread = (high - low) or 1.0
    step = (_SPARK_WIDTH - 4) / (len(values) - 1)
    points = [(2 + i * step, _SPARK_HEIGHT - 2 - (value - low) / spread * (_SPARK_HEIGHT - 4))
              for i, value in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    last_x, last_y = points[-1]
    tone = f" {cell.tone}" if cell.tone else ""
    return (f'<svg class="spk{tone}" width="{_SPARK_WIDTH}" height="{_SPARK_HEIGHT}" '
            f'viewBox="0 0 {_SPARK_WIDTH} {_SPARK_HEIGHT}" role="img" aria-label="{_esc(text)}">'
            f'<polyline points="{line}" fill="none" stroke="currentColor"/>'
            f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.2" fill="currentColor"/></svg>')
