"""
The charts an answer shows, which the model puts in it with show_chart: a Vega-Lite chart its code wrote in the Python
workspace (Altair's chart.save('charts/x.json')), which the workspace draws as SVG in CHART_THEME, or a picture it drew
itself (a PNG or SVG file, from matplotlib). Chart is one of them. read_spec() checks that a file is a Vega-Lite chart
drawn from its own data, themed_spec() readies it to be drawn, chart_of() checks that a file is a picture a report can
show, and chart_file_name() names one on disk; those are pure. write_charts() is save_charts()'s writer, the one place
charts reach the disk: into the folder the user names, each to <name>.part then renamed, never replacing a file that's
there.

A chart comes from code the model wrote, so it's shown only as an <img> (an SVG's scripts don't run there), a picture
only when it starts like the picture it claims to be (_SIGNATURES), up to MAX_CHART_BYTES, and a Vega-Lite chart only
when it reads no URL (_FETCHING_KEYS): its data is in the chart itself, so drawing it fetches nothing.
"""

from __future__ import annotations

import copy
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..._kit.fmt import human_size

CHARTS_FOLDER = "agent-charts"
MAX_CHART_BYTES = 3 * 1024 * 1024
_SVG_SNIFF_BYTES = 1024
_TITLE_CHARACTERS = 120
_FILE_NAME_CHARACTERS = 60
_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}
MIME_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "svg": "image/svg+xml"}
_EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp", "svg": ".svg"}
SPEC_EXTENSION = ".vl.json"
MAX_SPEC_BYTES = 5 * 1024 * 1024
_SPEC_KEYS = ("mark", "layer", "concat", "hconcat", "vconcat", "facet", "repeat", "spec")
_FETCHING_KEYS = ("url",)
_DATA_KEYS = ("datasets", "values")
_ALTAIR_VIEW_SIZE = ("continuousWidth", "continuousHeight")
CHART_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
_INK, _MUTED, _LINE, _GRID, _SURFACE = "#0b0b0b", "#52514e", "#c3c2b7", "#e6e5e0", "#fcfcfb"
CHART_THEME: dict[str, Any] = {
    "background": _SURFACE,
    "padding": 14,
    "font": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
    "view": {"stroke": None, "continuousWidth": 620, "continuousHeight": 300, "discreteWidth": 620,
             "discreteHeight": {"step": 30}},
    "title": {"anchor": "start", "fontSize": 14, "fontWeight": 600, "color": _INK, "offset": 14},
    "axis": {"labelColor": _MUTED, "titleColor": _MUTED, "labelFontSize": 11, "titleFontSize": 11,
             "titleFontWeight": 500, "titlePadding": 10, "labelPadding": 6, "gridColor": _GRID, "domainColor": _LINE,
             "tickColor": _LINE, "tickSize": 4},
    "axisQuantitative": {"grid": True, "domain": False, "ticks": False},
    "axisTemporal": {"grid": False},
    "axisDiscrete": {"grid": False, "ticks": False},
    "legend": {"labelColor": _MUTED, "titleColor": _MUTED, "labelFontSize": 11, "titleFontSize": 11,
               "titleFontWeight": 500, "symbolType": "circle", "orient": "top", "direction": "horizontal"},
    "header": {"labelColor": _MUTED, "titleColor": _MUTED, "labelFontSize": 11},
    "range": {"category": list(CHART_COLORS), "ramp": {"scheme": "blues"}, "heatmap": {"scheme": "blues"}},
    "mark": {"color": CHART_COLORS[0]},
    "bar": {"cornerRadiusEnd": 4, "stroke": _SURFACE, "strokeWidth": 1, "discreteBandSize": {"band": 0.72}},
    "rect": {"stroke": _SURFACE, "strokeWidth": 1},
    "arc": {"stroke": _SURFACE, "strokeWidth": 2},
    "area": {"opacity": 0.85, "line": {"strokeWidth": 2}},
    "line": {"strokeWidth": 2},
    "point": {"size": 64, "filled": True},
    "text": {"color": _MUTED, "fontSize": 11},
    "rule": {"color": _LINE},
}


@dataclass
class Chart:
    """
    One chart in an answer: its title, the workspace file it came from ('charts/errors_by_day.json'), the picture's
    bytes and what kind of picture it is ('svg', 'png', 'jpeg', 'gif' or 'webp'), and for a Vega-Lite chart its spec
    (alt.Chart.from_dict(chart.spec) shows it again, with its tooltips, where Altair is installed).
    """

    title: str
    path: str
    data: bytes = field(repr=False)
    kind: str = "png"
    spec: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def mime(self) -> str:
        """
        The picture's media type: 'image/png'.
        """
        return MIME_TYPES[self.kind]

    @property
    def format_name(self) -> str:
        """
        What the chart was made as, for a report's line: 'Vega-Lite', or the picture's kind ('PNG').
        """
        return "Vega-Lite" if self.spec is not None else self.kind.upper()

    @property
    def size(self) -> str:
        """
        How big the picture is: '42.0 KB'.
        """
        return human_size(len(self.data))


def _looks_like_svg(data: bytes) -> bool:
    """
    Whether `data` starts like an SVG file: an <svg> element, after an XML declaration, a doctype or comments.
    """
    start = data[:_SVG_SNIFF_BYTES].decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    return start.startswith(("<svg", "<?xml", "<!DOCTYPE svg", "<!--")) and "<svg" in start


def image_kind(data: bytes) -> str | None:
    """
    What kind of picture `data` is, from how it starts: 'png', 'jpeg', 'gif', 'webp' or 'svg'; None for anything
    else.
    """
    for kind, signatures in _SIGNATURES.items():
        if data.startswith(signatures):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if _looks_like_svg(data):
        return "svg"
    return None


def title_from_path(path: str) -> str:
    """
    A title for a chart whose code gave none, from its file name: 'charts/errors_by_day.png' -> 'Errors by day'.
    """
    stem = posixpath.splitext(posixpath.basename(path))[0]
    words = re.sub(r"[_\-.]+", " ", stem).strip()
    return words[:1].upper() + words[1:] if words else "Chart"


def is_spec(data: bytes) -> bool:
    """
    Whether a workspace file holds a JSON object (a Vega-Lite chart), rather than a picture.
    """
    return data[:_SVG_SNIFF_BYTES].decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n").startswith("{")


def _fetching_key(value: Any) -> str | None:
    """
    The first key in a spec that would make drawing it fetch something ('url': a dataset's, an image mark's), or None
    when there's none. The chart's rows (_DATA_KEYS) are data, so a column named url is fine.
    """
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        fetching = [key for key in value if key in _FETCHING_KEYS]
        if fetching:
            return fetching[0]
        items = [inner for key, inner in value.items() if key not in _DATA_KEYS]
    else:
        return None
    for item in items:
        found = _fetching_key(item)
        if found:
            return found
    return None


def read_spec(path: str, data: bytes) -> dict[str, Any]:
    """
    The Vega-Lite chart in the workspace file at `path`. ValueError when it isn't one, is bigger than MAX_SPEC_BYTES
    or reads anything from a URL, saying how to save one that's right.
    """
    if len(data) > MAX_SPEC_BYTES:
        raise ValueError(f"{path!r} is {human_size(len(data))}, more than the {human_size(MAX_SPEC_BYTES)} a chart may "
                         "be: aggregate the data first (a row per point the chart shows), then save it again")
    try:
        spec = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path!r} isn't a Vega-Lite chart ({exc}): save one with Altair, "
                         "chart.save('charts/name.json')") from None
    is_chart = isinstance(spec, dict) and ("vega-lite" in str(spec.get("$schema", "")) or
                                           any(key in spec for key in _SPEC_KEYS))
    if not is_chart:
        raise ValueError(f"{path!r} is JSON but not a Vega-Lite chart: save one with Altair, "
                         "chart.save('charts/name.json')")
    fetching = _fetching_key(spec)
    if fetching:
        raise ValueError(f"{path!r} reads from a URL ({fetching!r}): a chart is drawn from its own data, so build it "
                         "from a DataFrame (alt.Chart(df)) and save it again")
    return spec


def themed_spec(spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    A Vega-Lite chart readied to be drawn in CHART_THEME, and its title, taken out so the report shows it above the
    chart instead of in it. The size Altair writes into every chart's config (300 x 300) is taken out too, so the
    theme's applies; a width or height the code set on the chart itself stays.
    """
    readied = copy.deepcopy(spec)
    title = readied.pop("title", "")
    if isinstance(title, dict):
        title = title.get("text", "")
    if isinstance(title, list):
        title = " ".join(map(str, title))
    view = (readied.get("config") or {}).get("view")
    if isinstance(view, dict):
        for key in _ALTAIR_VIEW_SIZE:
            view.pop(key, None)
    return readied, str(title or "")


def chart_of(path: str, data: bytes, title: str = "") -> Chart:
    """
    The workspace file at `path` as a chart, titled `title` (else after its file name). ValueError when it isn't a
    picture or is bigger than MAX_CHART_BYTES, saying how to make one that is.
    """
    kind = image_kind(data)
    if kind is None:
        raise ValueError(f"{path!r} isn't a chart: save one with Altair, chart.save('charts/name.json'), or a "
                         "picture (PNG or SVG), and show that file")
    if len(data) > MAX_CHART_BYTES:
        raise ValueError(f"{path!r} is {human_size(len(data))}, more than the {human_size(MAX_CHART_BYTES)} a chart "
                         "may be: save it again smaller (a lower dpi=, or PNG rather than SVG for many points)")
    return Chart(chart_title(title, path), path, bytes(data), kind)


def chart_title(title: str, path: str) -> str:
    """
    A chart's title as the report shows it: `title` on one line and clipped, else after the file's name.
    """
    return " ".join(str(title or "").split())[:_TITLE_CHARACTERS] or title_from_path(path)


def chart_file_name(chart: Chart, taken: set[str]) -> str:
    """
    A file name for `chart` on disk, from its title, that isn't in `taken` (lower case), nor is its spec's beside it
    for a Vega-Lite chart: 'errors-by-day.svg', else 'errors-by-day-2.svg'.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", chart.title.lower()).strip("-")[:_FILE_NAME_CHARACTERS].strip("-") or "chart"
    extensions = [_EXTENSIONS[chart.kind]] + ([SPEC_EXTENSION] if chart.spec is not None else [])
    stem, number = slug, 1
    while any(f"{stem}{extension}" in taken for extension in extensions):
        number += 1
        stem = f"{slug}-{number}"
    return f"{stem}{extensions[0]}"


def _write_file(path: Path, data: bytes) -> None:
    """
    Writes `data` to <path>.part, then renames it to `path`, so a file is never there half written.
    """
    part = path.with_name(f"{path.name}.part")
    part.write_bytes(data)
    os.replace(part, path)


def write_charts(charts: list[Chart], folder: str | os.PathLike) -> list[Path]:
    """
    Writes each chart into `folder` (made when it isn't there), named after its title (chart_file_name), with a
    Vega-Lite chart's spec beside it (<name>.vl.json, to open in the Vega editor or alt.Chart.from_json), and returns
    the pictures' paths. A name that's taken gets a number, so no file is ever replaced.
    """
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    taken = {entry.name.lower() for entry in target.iterdir()}
    written: list[Path] = []
    for chart in charts:
        name = chart_file_name(chart, taken)
        taken.add(name.lower())
        path = target / name
        _write_file(path, chart.data)
        if chart.spec is not None:
            spec_name = f"{posixpath.splitext(name)[0]}{SPEC_EXTENSION}"
            taken.add(spec_name)
            _write_file(target / spec_name, json.dumps(chart.spec, indent=2).encode())
        written.append(path)
    return written
