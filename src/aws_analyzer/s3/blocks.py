"""
The render blocks only S3's reports draw (frames, pictures, links, media, PDF pages, Word documents),
each with its HTML and text drawings.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

from .._kit.blocks import _Report, _draws
from .._kit.deps import _require
from .._kit.fmt import human_size
from .._kit.render import _prose
from .._kit.text import _esc

from .models import Picture
from .readers.media import _image_info
from .analysis.documents import _LIST_NUMBER


@dataclass
class _Frame:
    df: Any
    title: str = ""


@dataclass
class _Image:
    data: bytes
    mime: str


@dataclass
class _Link:
    url: str
    label: str


@dataclass
class _Media:
    url: str
    kind: str  # 'audio' | 'video'
    mime: str


@dataclass
class _Pages:
    pictures: list[Picture]  # PDF pages drawn as pictures, side by side like a document viewer
    title: str = ""


@dataclass
class _Flow:
    """
    A document in reading order: ('title' | 'h1'.. | 'p' | 'li', text), ('table', rows), ('picture', Picture),
    ('missing', a line about something that isn't shown) and, in a PDF's text (pdf_flow), ('page', number) where
    each page starts. A 'p' keeps its line breaks, and an 'li' that starts with its own number ('2. ', 'b) ')
    shows no bullet.
    """

    items: list[tuple[str, Any]]
    title: str = ""


def _data_img(data: bytes, mime: str, alt: str = "") -> str:
    """
    An <img> that holds the picture itself (a data: URL), so it stays in the saved notebook.
    """
    return f'<img src="data:{_esc(mime)};base64,{base64.b64encode(data).decode()}" alt="{_esc(alt)}">'


class _Zoom:
    """
    The full-size view of a report's PDF pages, with CSS only (so it still works in a saved notebook). Each page
    has a hidden radio button, and its picture is a <label> that checks it; a checked radio turns the page into a
    fixed overlay. In the overlay, ‹ and › are labels for the radios of the pages before and after it (across all
    the report's _Pages blocks), and ✕ or a click on the page checks the group's "none" radio, which closes it.
    A random group name keeps two reports in one notebook apart.
    """

    def __init__(self, blocks: list[Any]):
        self.group = "z" + os.urandom(5).hex()
        self.total = sum(len(block.pictures) for block in blocks if isinstance(block, _Pages))
        self.count = 0
        self.started = False

    def start(self) -> str:
        """
        The "none" radio (checked: no page open), once per report.
        """
        if self.started:
            return ""
        self.started = True
        return f'<input type="radio" class="zr" name="{self.group}" id="{self.group}-0">'

    def page(self, picture: Picture) -> str:
        """
        One page: its picture, which opens it full size, and the overlay's buttons. aspect-ratio keeps the page's
        place in the report while it's open, so the pages behind the overlay don't move.
        """
        self.count += 1
        n, group = self.count, self.group
        info = _image_info(picture.data) or {}
        ratio = f' style="aspect-ratio:{info["width"]}/{info["height"]}"' if info.get("width") and info.get("height") else ""
        prev = f'<label class="zn zprev" for="{group}-{n - 1}" title="Previous page">‹</label>' if n > 1 else ""
        after = f'<label class="zn znext" for="{group}-{n + 1}" title="Next page">›</label>' if n < self.total else ""
        where = f" · {n} of {self.total}" if self.total > 1 else ""
        return (f'<div class="zw"{ratio}><input type="radio" class="zr" name="{group}" id="{group}-{n}">'
                f'<div class="zp"><label class="zo" for="{group}-{n}" title="Click to see it full size">'
                f"{_data_img(picture.data, picture.mime, picture.label)}</label>"
                f'<label class="zx" for="{group}-0" title="Back to the report"></label>'
                f'<label class="zc" for="{group}-0" title="Back to the report">✕</label>{prev}{after}'
                f'<span class="zh">{_esc(picture.label)}{where} · click the page or ✕ to go back</span></div></div>')


def _flow_html(items: list[tuple[str, Any]]) -> str:
    """
    A document's _Flow items as HTML. Every piece of its text is escaped; headings are styled divs, not
    <h1>.. tags, so they don't land in the notebook's table of contents.
    """
    out: list[str] = []
    for i, (role, value) in enumerate(items):
        if role == "li":
            opens = i == 0 or items[i - 1][0] != "li"
            closes = i == len(items) - 1 or items[i + 1][0] != "li"
            numbered = ' class="n"' if _LIST_NUMBER.match(value) else ""
            out.append(("<ul>" if opens else "") + f"<li{numbered}>{_esc(value)}</li>" + ("</ul>" if closes else ""))
        elif role == "page":
            out.append(f'<div class="pg">Page {_esc(value)}</div>')
        elif role == "title" or role.startswith("h"):
            level = 0 if role == "title" else min(int(role[1:]), 3)
            out.append(f'<div class="dh d{level}">{_esc(value)}</div>')
        elif role == "table":
            rows = [[f"<td>{_esc(cell)}</td>" for cell in row] for row in value]
            head = "".join(f"<th>{_esc(cell)}</th>" for cell in value[0]) if len(value) > 1 else ""
            body = "".join(f"<tr>{''.join(cells)}</tr>" for cells in (rows[1:] if head else rows))
            out.append(f'<div class="tw"><table class="t">{f"<thead><tr>{head}</tr></thead>" if head else ""}'
                       f"<tbody>{body}</tbody></table></div>")
        elif role == "picture":
            out.append(f"<figure>{_data_img(value.data, value.mime, value.alt or value.label)}</figure>")
        elif role == "missing":
            out.append(f'<div class="pm">{_esc(value)}</div>')
        else:
            out.append(f"<p>{_esc(value)}</p>")
    return "".join(out)


def _flow_text(items: list[tuple[str, Any]]) -> str:
    """
    A document's _Flow items as plain text: '#' headings, '- ' list items, ' | ' tables, '-- Page 2 --'.
    """
    out: list[str] = []
    for i, (role, value) in enumerate(items):
        if role == "li":
            text = value if _LIST_NUMBER.match(value) else f"- {value}"
        elif role == "page":
            text = f"-- Page {value} --"
        elif role == "title" or role.startswith("h"):
            text = "#" * (1 if role == "title" else min(int(role[1:]) + 1, 6)) + f" {value}"
        elif role == "table":
            text = "\n".join(" | ".join(row) for row in value)
        elif role == "picture":
            alt = f": {value.alt}" if value.alt else ""
            text = f"[Picture {value.label}, {human_size(len(value.data))}{alt}]"
        elif role == "missing":
            text = f"[{value}]"
        else:
            text = value
        out.append(("\n" if role == "li" and i and items[i - 1][0] == "li" else "\n\n" if i else "") + text)
    return "".join(out)


@_draws(_Frame, "html")
def _frame_html(block: _Frame, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    pd = _require("pandas", "Table rendering")
    with pd.option_context("display.max_colwidth", 120):
        frame = block.df.to_html(max_rows=report.max_rows or None, max_cols=40, border=0, classes="t")
    return f'{title}<div class="tw">{frame}</div>'


@_draws(_Frame, "text")
def _frame_lines(block: _Frame, report: _Report) -> list[str]:
    return (["", f"-- {block.title} --"] if block.title else []) + [
        block.df.to_string(max_rows=report.max_rows or None, max_cols=20)]


@_draws(_Image, "html")
def _image_html(block: _Image, report: _Report) -> str:
    return _data_img(block.data, block.mime)


@_draws(_Image, "text")
def _image_lines(block: _Image, report: _Report) -> list[str]:
    return [f"(image, {human_size(len(block.data))} - open in a notebook to see it)"]


@_draws(_Pages, "html")
def _pages_html(block: _Pages, report: _Report) -> str:
    """
    PDF pages side by side; a click on one shows it full size, and ‹ › step through every page in the report
    (the _Zoom its _Pages blocks share).
    """
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    captioned = len(block.pictures) > 1
    zoom = report.shared(_Zoom, lambda: _Zoom(list(report.blocks)))
    figures = "".join(
        f"<figure>{zoom.page(pic)}"
        + (f"<figcaption>{_esc(pic.label)}</figcaption>" if captioned else "")
        + "</figure>"
        for pic in block.pictures
    )
    return f'{title}<div class="pages{"" if captioned else " one"}">{zoom.start()}{figures}</div>'


@_draws(_Pages, "text")
def _pages_lines(block: _Pages, report: _Report) -> list[str]:
    return (["", f"-- {block.title} --"] if block.title else []) + [
        f"[{pic.label}: a picture ({human_size(len(pic.data))}) - open this in a notebook to see it]"
        for pic in block.pictures]


@_draws(_Flow, "html")
def _flow_block_html(block: _Flow, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return f'{title}<div class="flow">{_flow_html(block.items)}</div>'


@_draws(_Flow, "text")
def _flow_lines(block: _Flow, report: _Report) -> list[str]:
    return (["", f"-- {block.title} --"] if block.title else []) + [_flow_text(block.items)]


@_draws(_Link, "html")
def _link_block_html(block: _Link, report: _Report) -> str:
    return f'<a href="{_esc(block.url)}" target="_blank" rel="noopener">{_esc(block.label)}</a>'


@_draws(_Link, "text")
def _link_block_lines(block: _Link, report: _Report) -> list[str]:
    return [block.label, block.url]


@_draws(_Media, "html")
def _media_html(block: _Media, report: _Report) -> str:
    size = ' style="max-width:100%;max-height:480px"' if block.kind == "video" else ""
    return (f'<{block.kind} controls preload="metadata"{size}><source src="{_esc(block.url)}" '
            f'type="{_esc(block.mime)}"></{block.kind}>')


@_draws(_Media, "text")
def _media_lines(block: _Media, report: _Report) -> list[str]:
    return [f"({block.kind}: open this link in a browser to play it)", block.url]
