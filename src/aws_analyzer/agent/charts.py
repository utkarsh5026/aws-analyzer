"""
The charts an answer shows: pictures the model's code draws in the Python workspace (matplotlib's PNG or SVG files),
which it puts in the answer with show_chart. Chart is one of them; chart_of() checks that a workspace file is a picture
a report can show, and chart_file_name() names one on disk; those are pure. write_charts() is save_charts()'s writer,
the one place charts reach the disk: into the folder the user names, each to <name>.part then renamed, never replacing
a file that's there.

A chart's bytes come from code the model wrote, so they're shown only as an <img> (an SVG's scripts don't run there),
and only when they start like the picture they claim to be (_SIGNATURES), up to MAX_CHART_BYTES.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from .._kit.fmt import human_size

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


@dataclass
class Chart:
    """
    One chart in an answer: its title, the workspace file it was drawn to ('charts/errors_by_day.png'), its bytes and
    what kind of picture it is ('png', 'svg', 'jpeg', 'gif' or 'webp').
    """

    title: str
    path: str
    data: bytes = field(repr=False)
    kind: str = "png"

    @property
    def mime(self) -> str:
        """
        The picture's media type: 'image/png'.
        """
        return MIME_TYPES[self.kind]

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


def chart_of(path: str, data: bytes, title: str = "") -> Chart:
    """
    The workspace file at `path` as a chart, titled `title` (else after its file name). ValueError when it isn't a
    picture or is bigger than MAX_CHART_BYTES, saying how to make one that is.
    """
    kind = image_kind(data)
    if kind is None:
        raise ValueError(f"{path!r} isn't a picture: save the chart as PNG with matplotlib, "
                         "plt.savefig('charts/name.png'), and show that file")
    if len(data) > MAX_CHART_BYTES:
        raise ValueError(f"{path!r} is {human_size(len(data))}, more than the {human_size(MAX_CHART_BYTES)} a chart "
                         "may be: save it again smaller (a lower dpi=, or PNG rather than SVG for many points)")
    shown_title = " ".join(str(title or "").split())[:_TITLE_CHARACTERS] or title_from_path(path)
    return Chart(shown_title, path, bytes(data), kind)


def chart_file_name(chart: Chart, taken: set[str]) -> str:
    """
    A file name for `chart` on disk, from its title, that isn't in `taken` (lower case): 'errors-by-day.png', else
    'errors-by-day-2.png'.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", chart.title.lower()).strip("-")[:_FILE_NAME_CHARACTERS].strip("-") or "chart"
    extension = _EXTENSIONS[chart.kind]
    name, number = f"{slug}{extension}", 1
    while name.lower() in taken:
        number += 1
        name = f"{slug}-{number}{extension}"
    return name


def write_charts(charts: list[Chart], folder: str | os.PathLike) -> list[Path]:
    """
    Writes each chart into `folder` (made when it isn't there), named after its title (chart_file_name), and returns
    the files' paths. A name that's taken gets a number, so no file is ever replaced.
    """
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    taken = {entry.name.lower() for entry in target.iterdir()}
    written: list[Path] = []
    for chart in charts:
        name = chart_file_name(chart, taken)
        taken.add(name.lower())
        path = target / name
        part = path.with_name(f"{name}.part")
        part.write_bytes(chart.data)
        os.replace(part, path)
        written.append(path)
    return written
