"""
The Open a file commands, and how a preview's parts are drawn.
"""

from __future__ import annotations

import io
import posixpath
from datetime import datetime
from typing import Any, Iterable

from ..._kit.blocks import _Cards, _Next, _Note, _Table, _Text, _Title
from ..._kit.deps import _require
from ..._kit.fmt import _fmt_dt, _plural, human_age, human_size
from ..._kit.jsontree import _JSON_KEYS, _JSON_ROWS, _JsonTree
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..helpers import KB, MB, _MAX_PICTURES, _PREVIEW_PAGES, _PREVIEW_PICTURES, parse_s3_uri
from ..models import Document, Picture
from ..readers.pdf import _encode_picture
from ..analysis.documents import pdf_flow, pdf_furniture
from ..blocks import _Flow, _Frame, _Image, _Link, _Media, _Pages
from .cells import _FORMAT_LABELS


_INFO_CARDS = {  # Preview.info key -> card label, in display order
    "rows": "Rows",
    "records": "Records",
    "keys": "Keys",
    "items": "Items",
    "depth": "Nesting",
    "columns": "Columns",
    "row_groups": "Row groups",
    "stripes": "Stripes",
    "batches": "Record batches",
    "sheets": "Sheets",
    "codec": "Codec",
    "compression": "Compression",
    "files": "Files",
    "unpacked_size": "Unpacked size",
    "arrays": "Arrays",
    "shape": "Shape",
    "dtype": "Dtype",
    "tensors": "Tensors",
    "parameters": "Parameters",
    "dtypes": "Dtypes",
    "kernel": "Kernel",
    "language": "Language",
    "cells": "Cells",
    "pages": "Pages",
    "slides": "Slides",
    "words": "Words",
    "paragraphs": "Paragraphs",
    "headings": "Headings",
    "tables": "Tables",
    "pictures": "Pictures",
    "title": "Title",
    "author": "Author",
}

_LISTING_TITLES = {
    "zip": "Files",
    "tar": "Files",
    "torch": "Files in the checkpoint",
    "npz": "Arrays",
    "pptx": "Slides",
    "safetensors": "Tensors",
    "notebook": "Cells",
}


def _card_value(key: str, value: Any) -> str:
    if key == "columns":
        return f"{len(value):,}"
    if key == "depth":
        return _plural(value, "level")
    if key.endswith("size") and isinstance(value, int):
        return human_size(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if isinstance(value, (list, tuple)) and key != "shape":
        return ", ".join(map(str, value))
    return str(value)


def _document_table(rows: list[list[str]], title: str) -> _Table:
    """
    A table from a Word / PowerPoint file: first row as the header, ragged rows padded.
    """
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    headers = rows[0] if len(rows) > 1 else [""] * width
    return _Table(headers, rows[1:] if len(rows) > 1 else rows, title=title)


def _web_picture(picture: Picture) -> tuple[Picture | None, str]:
    """
    A picture ready to show in a notebook -> (picture, '') or (None, why it can't be shown). With pillow
    installed, a big picture shrinks to 1,600 pixels and TIFF becomes PNG, so the notebook stays small.
    """
    if picture.mime and (len(picture.data) <= 512 * KB or picture.mime == "image/svg+xml"):
        return picture, ""
    pil, missing = None, ""
    try:
        pil = _require("PIL.Image", "Showing this picture", "pillow")
    except ImportError as exc:
        missing = str(exc)
    if pil is not None:
        try:
            with pil.open(io.BytesIO(picture.data)) as image:
                image.thumbnail((1600, 1600))
                data, mime = _encode_picture(image)
        except Exception:  # EMF / WMF, or a broken file: pillow's decoders raise all sorts, even ZeroDivisionError
            pass
        else:
            if not picture.mime or len(data) < len(picture.data):
                return Picture(data, mime, picture.label, picture.page, picture.part, picture.alt), ""
            return picture, ""
    if picture.mime and len(picture.data) <= 8 * MB:
        return picture, ""
    if picture.mime:
        return None, f"too big to show here ({missing})" if pil is None else "too big to show here"
    kind = posixpath.splitext(picture.label)[1].lstrip(".").upper() or "this"
    return None, f"{kind} format, which browsers can't show" + (f" ({missing})" if pil is None else "")


def _flow_items(doc: Document, *, max_parts: int | None = None, max_chars: int | None = None,
                max_pictures: int = _MAX_PICTURES) -> tuple[list[tuple[str, Any]], bool, int]:
    """
    A Word document as _Flow items in reading order: its headings, paragraphs, list items and tables, with
    its pictures in place (after max_pictures, a line naming each one instead). Stops after max_parts parts
    or max_chars characters -> (items, whether it stopped early, pictures left out by max_pictures).
    """
    items: list[tuple[str, Any]] = []
    tables, waiting = iter(doc.tables), list(doc.pictures)
    counts = {"chars": 0, "shown": 0, "capped": 0}

    def pictures_before(index: int) -> None:
        while waiting and (waiting[0].part or 0) <= index:
            picture = waiting.pop(0)
            what = f"Picture {picture.label}, {human_size(len(picture.data))}"
            if counts["shown"] >= max_pictures:
                counts["capped"] += 1
                items.append(("missing", f"{what}: not shown here"))
                continue
            ready, why = _web_picture(picture)
            counts["shown"] += ready is not None
            items.append(("picture", ready) if ready else ("missing", f"{what}: {why}"))

    for index, (part, role) in enumerate(zip(doc.parts, doc.roles)):
        pictures_before(index)
        room = None if max_chars is None else max_chars - counts["chars"]
        if (max_parts is not None and index >= max_parts) or (room is not None and room <= 0):
            return items, True, counts["capped"]
        if role == "table":
            items.append(("table", next(tables)))
        else:  # list items (and numbered headings) carry a '- ' in doc.parts
            text = part[2:] if role != "p" and part.startswith("- ") else part
            items.append((role, text if room is None else text[:room]))
        counts["chars"] += len(part)
    pictures_before(len(doc.parts))
    return items, False, counts["capped"]


def _pdf_blocks(doc: Document, drawn: dict[int, Picture], max_chars: int) -> tuple[list[Any], bool]:
    """
    A PDF's pages in order: the pages with text laid out by pdf_flow, in one _Flow with a line where each page
    starts, and the drawn pages (`drawn`, by page number) as pictures with their text folded under them. Stops
    after max_chars characters -> (blocks, whether it stopped early).
    """
    texts = dict(zip(doc.numbers, doc.parts))
    blocks: list[Any] = []
    items: list[tuple[str, Any]] = []
    shown, page, cut = 0, None, False
    for role, value in pdf_flow(doc):
        if shown >= max_chars:
            cut = True
            break
        if role == "page":
            page = value
            if page in drawn:
                if items:
                    blocks.append(_Flow(items))
                    items = []
                blocks.append(_Pages([drawn[page]], title=f"Page {page}"))
                text = texts.get(page, "")
                if text.strip():
                    blocks.append(_Text(text[: max_chars - shown], title=f"Text of page {page}", wrap=True,
                                        collapsed=True))
                shown += len(text)
                continue
        elif page in drawn:
            continue
        else:
            value = value[: max_chars - shown]
            shown += len(value)
        items.append((role, value))
    if items:
        blocks.append(_Flow(items))
    return blocks, cut


def _pages_title(pictures: list[Picture], count: int | None) -> str:
    """
    'Pages 1-3 of 12' over PDF pages drawn from the start.
    """
    first, last = pictures[0].page, pictures[-1].page
    span = f"Page {first}" if first == last else f"Pages {first}–{last}"
    return span + (f" of {count:,}" if count and count > len(pictures) else "")


def _page_numbers(numbers: list[int]) -> list[int] | range:
    """
    Page numbers to put in a call: a range when they run on without gaps.
    """
    run = len(numbers) > 2 and numbers == list(range(numbers[0], numbers[-1] + 1))
    return range(numbers[0], numbers[-1] + 1) if run else numbers


def _listing_table(rows: list[dict[str, Any]], title: str) -> _Table:
    """
    list of dicts -> table; sizes, dates and big numbers formatted for reading.
    """
    if not rows:
        return _Table(["(empty)"], [], title=title)

    def cell(key: str, value: Any) -> str:
        if value is None:
            return "-"
        if key.endswith("size") and isinstance(value, int):
            return human_size(value)
        if isinstance(value, datetime):
            return _fmt_dt(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,}"
        return str(value)

    headers = list(rows[0])
    return _Table(
        headers, [[cell(k, row.get(k)) for k in headers] for row in rows], title=title,
        path_cols=tuple(j for j, key in enumerate(headers) if key in ("name", "path", "file")),
    )


def _hexdump(data: bytes) -> str:
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {chunk.hex(' '):<47}  {ascii_}")
    return "\n".join(lines)


class _FileCommands:
    """
    S3View's commands to open a file: head(), preview(), document() and link().
    """

    # ------------------------------------------------------------------ objects

    @_friendly_errors
    def head(self, uri: str) -> None:
        """
        All metadata of one object.
        """
        info = self.core.head(uri)
        skip = {"uri", "metadata", "tags"}
        blocks: list[Any] = [
            _Title(info["uri"]),
            _Cards(
                [
                    ("Size", human_size(info.get("size"))),
                    ("Last modified", human_age(info.get("last_modified"))),
                    ("Content type", info.get("content_type", "-")),
                    ("Storage class", info.get("storage_class", "-")),
                    ("Encryption", info.get("encryption", "none")),
                ]
            ),
            _Table(
                ["Field", "Value"],
                [
                    [k, _fmt_dt(v, zone=True) if isinstance(v, datetime) else v]
                    for k, v in info.items()
                    if k not in skip
                ],
                title="System metadata",
                max_rows=0,
            ),
        ]
        if info["metadata"]:
            blocks.append(
                _Table(
                    ["Key", "Value"],
                    [[k, v] for k, v in info["metadata"].items()],
                    title="User metadata",
                )
            )
        if info["tags"] is None:
            blocks.append(_Note("Tags: no permission to read (s3:GetObjectTagging)."))
        elif info["tags"]:
            blocks.append(
                _Table(
                    ["Tag", "Value"],
                    [[k, v] for k, v in info["tags"].items()],
                    title="Tags",
                )
            )
        blocks.append(
            _Next(
                [
                    (_call("preview", info["uri"]), "what's inside it"),
                    (
                        _call("link", info["uri"]),
                        "a download link that works without AWS access",
                    ),
                ]
            )
        )
        self._show(blocks)

    @_friendly_errors
    def preview(self, uri: str, n: int = 20) -> None:
        """
        Peek inside a file: tables (csv, tsv, psv, json, jsonl, parquet, orc, feather, avro, excel, npy)
        with their schema, archive contents (zip, tar, tar.gz, model.tar.gz, npz, .pt), safetensors tensors,
        notebook cells, JSON as a tree you fold with a click, text, images, audio / video players, a PDF's first
        pages as they look (scans too), a Word file with its pictures in place, or a hex dump.
        Downloads only what it needs; files without an extension are recognised by their content.

        n is the number of rows, lines or paragraphs shown, and the items shown of each JSON array. In the JSON
        tree, hover over a line for the Python that reaches it in data = ui.core.read_json(uri).
        """
        p = self.core.preview(uri, n=n)
        doc, items, capped = p.document, [], 0
        detail = " · ".join(
            filter(
                None,
                [
                    human_size(p.size),
                    _FORMAT_LABELS.get(p.format or "", p.format) or p.content_type,
                    f"{p.compression}-compressed" if p.compression else "",
                ],
            )
        )
        blocks: list[Any] = [_Title(f"Preview of {p.uri}", detail)]
        if p.note:
            blocks.append(_Note(p.note, "warn"))
        cards = [
            (label, _card_value(key, p.info[key]))
            for key, label in _INFO_CARDS.items()
            if p.info.get(key) not in (None, "", [])
        ]
        if cards:
            blocks.append(_Cards(cards))
        if p.kind == "table":
            blocks.append(_Frame(p.data, title=f"First {len(p.data):,} rows"))
        elif p.kind == "listing":
            blocks.append(
                _listing_table(p.data, _LISTING_TITLES.get(p.format or "", "Contents"))
            )
        elif p.kind == "document" and doc is not None and doc.kind == "docx":
            items, _, capped = _flow_items(doc, max_parts=n, max_pictures=_PREVIEW_PICTURES)
            blocks.append(_Flow(items, title=p.info.get("excerpt", "Document")))
        elif p.kind == "document":
            drawn = doc.pictures if doc is not None else []
            if drawn:
                blocks.append(_Pages(drawn, title=_pages_title(drawn, p.info.get("pages"))))
            if p.data.strip() or not drawn:  # a scan's empty text layer isn't worth a section
                title = "Text of page 1" if drawn else p.info.get("excerpt", "Text")
                blocks.append(_Text(_clip(p.data, 20_000) or "(no text)", title=title, wrap=True,
                                    collapsed=bool(drawn)))
        elif p.kind == "json":
            tree = _JsonTree(p.data, items=n, cut=p.info.get("cut", 0), title="Contents")
            blocks.append(tree)
            limits = {"item": f"long arrays show their first {n:,} items",
                      "key": f"big objects show their first {max(n, _JSON_KEYS):,} keys"}
            if tree.capped:
                said = " and ".join(text for what, text in limits.items() if what in tree.capped)
                blocks.append(_Note(f"{said[0].upper()}{said[1:]}; pass n= for more."))
            if tree.full:
                blocks.append(_Note(f"The tree stops at {_JSON_ROWS:,} lines so the notebook stays small; "
                                    f"{_call('download', p.uri)} gets the whole file."))
        elif p.kind == "text":
            blocks.append(
                _Text("\n".join(p.data or []), title=f"First {len(p.data or [])} lines")
            )
        elif p.kind == "image":
            blocks.append(
                _Image(p.data, p.info.get("mime") or p.content_type or "image/png")
            )
        elif p.kind == "media" and p.info.get("media") in ("audio", "video"):
            blocks += [
                _Media(p.data, p.info["media"], p.info.get("mime", "")),
                _Link(p.data, "Open in a new tab (link valid 1 hour)"),
            ]
        elif p.kind == "media":
            blocks.append(_Link(p.data, "Open the PDF (link valid 1 hour)"))
        elif p.kind == "binary":
            blocks.append(
                _Text(_hexdump(p.data), title="Binary content (first 512 bytes)")
            )
        if p.info.get("url") and p.kind != "media":
            blocks.append(_Link(p.info["url"], "Open the file (link valid 1 hour)"))
        if p.info.get("columns"):
            blocks.append(
                _Table(
                    ["Column", "Type"],
                    [list(c) for c in p.info["columns"]],
                    title="Schema",
                )
            )
        if p.info.get("outline") and p.truncated:  # the whole document's headings, when only its start shows
            blocks.append(_listing_table(p.info["outline"], "Outline"))
        if p.info.get("table") and not any(role == "table" for role, _ in items):
            blocks.append(_document_table(p.info["table"], "First table"))
        if p.truncated and p.kind == "document":
            blocks.append(
                _Note(
                    f"Showing the first {n} paragraphs; {_call('document', p.uri)} shows all of it."
                )
            )
        if capped:
            blocks.append(_Note(f"Showing {_PREVIEW_PICTURES} of the pictures; {_call('document', p.uri)} "
                                f"shows up to {_MAX_PICTURES}."))
        if p.info.get("metadata"):
            blocks.append(
                _Table(
                    ["Key", "Value"],
                    [[k, _clip(str(v), 200)] for k, v in p.info["metadata"].items()],
                    title="Metadata",
                )
            )
        if p.truncated and p.kind == "text":
            blocks.append(_Note(f"Showing the first {n} lines; pass n= for more."))
        steps = []
        if p.format in ("pdf", "docx", "pptx"):
            what = {"docx": "the whole document, pictures in place", "pptx": "the full text, slide by slide",
                    "pdf": "every page, scans drawn" if p.kind == "document" and not p.data.strip()
                    else "the full text, page by page"}
            steps.append((_call("document", p.uri), what[p.format]))
        if p.kind == "json":
            steps.append((f"data = {_call('ui.core.read_json', p.uri)}", "the whole file as Python dicts and lists"))
        drawn = len(doc.pictures) if doc is not None and doc.kind == "pdf" else 0
        if drawn and (p.info.get("pages") or 0) > drawn:
            more = list(range(drawn + 1, min(drawn + _PREVIEW_PAGES, p.info["pages"]) + 1))
            span = f"pages {more[0]}–{more[-1]}" if len(more) > 1 else f"page {more[0]}"
            steps.append((_call("document", p.uri, pages=more, pictures=True), f"{span} as pictures"))
        blocks.append(
            _Next(
                steps + [(_call("download", p.uri), "a copy on the notebook's disk, in s3-downloads")]
            )
        )
        self._show(blocks)

    @_friendly_errors
    def document(
        self,
        uri: str,
        *,
        pages: Iterable[int] | None = None,
        password: str | None = None,
        pictures: bool | None = None,
        max_chars: int = 200_000,
    ) -> None:
        """
        A document as it reads: a Word file with its headings, lists, tables and pictures in place, a PDF's
        text laid out to read (headings, paragraphs and lists, a line where each page starts, and no running
        headers, footers or page numbers), or a PowerPoint deck slide by slide, e.g. document(uri, pages=[1, 2]).
        PDF pages with no text, such as scans, are drawn as pictures; pictures=True draws every page as it looks
        (with its text folded under it), pictures=False none.

        A PDF's text needs pypdf, and drawing its pages needs pypdfium2 and pillow. One report shows at most
        20 pages or pictures, so the notebook stays small; pass pages= for other pages. Click a drawn page to see
        it as big as the notebook, with ‹ › to step through the pages and ✕ to go back. The PDF's text as
        pypdf reads it, line by line, is in ui.core.read_document(uri).text.
        """
        with self._progress("Reading pages" if pictures is False else "Drawing pages", unit="pages") as tick:
            doc = self.core.read_document(uri, pages=pages, password=password, pictures=pictures,
                                          max_pictures=_MAX_PICTURES, layout=True, progress=tick)
        if doc.kind == "docx":  # Word's saved page count is often stale, so count paragraphs instead
            cards = [("Paragraphs", f"{len(doc.parts) - len(doc.tables):,}"), ("Tables", f"{len(doc.tables):,}")]
            cards += [("Pictures", f"{len(doc.pictures):,}")] if doc.pictures else []
        else:
            cards = [("Pages" if doc.kind == "pdf" else "Slides", f"{doc.page_count or 0:,}")]
        cards.append(("Words", f"{doc.word_count:,}"))
        cards += [(label, value) for label, value in (("Title", doc.title), ("Author", doc.author)) if value]
        blocks: list[Any] = [_Title(f"{_FORMAT_LABELS[doc.kind]} {doc.uri}"), _Cards(cards)]
        stopped = _Note(f"Stopped after {max_chars:,} characters; pass max_chars= for more, "
                        "or use ui.core.read_document(uri).text.")
        if doc.kind == "docx":
            items, cut, capped = _flow_items(doc, max_chars=max_chars, max_pictures=_MAX_PICTURES)
            if not doc.parts:
                blocks.append(_Note("The document has no text" + (", only pictures (probably scanned pages), so its words "
                                    "can't be searched or copied without OCR." if doc.pictures else "."), "warn"))
            blocks.append(_Flow(items))
            if capped:
                blocks.append(_Note(
                    f"Showed {len(doc.pictures) - capped} of the {len(doc.pictures)} pictures (one report shows "
                    f"{_MAX_PICTURES}, so the notebook stays small); ui.core.read_docx({doc.uri!r}, pictures=True)"
                    ".pictures holds them all, and each one shows itself in a notebook."))
            if cut:
                blocks.append(stopped)
            self._show(blocks)
            return
        drawn = {picture.page: picture for picture in doc.pictures}
        if doc.kind == "pdf":
            blank = [n for n, part in zip(doc.numbers, doc.parts) if not part.strip()]
            wanted = [] if pictures is False else list(doc.numbers) if pictures else blank
            if doc.parts and len(blank) == len(doc.parts):
                blocks.append(_Note("No text layer (probably a scanned document), so its words can't be searched "
                                    "or copied without OCR.", "warn"))
            if wanted and "pictures" in doc.errors:
                blocks.append(_Note(f"{doc.errors['pictures']}.", "warn"))
            elif len(wanted) > len(drawn):
                rest = [n for n in wanted if n not in drawn][:_MAX_PICTURES]
                blocks.append(_Note(
                    f"Drew {len(drawn)} of the {len(wanted)} pages {'asked for' if pictures else 'without text'} "
                    f"(one report draws {_MAX_PICTURES}, so the notebook stays small); "
                    f"{_call('document', doc.uri, pages=_page_numbers(rest), pictures=True)} draws the next ones."))
            elif pictures is False and blank and len(blank) < len(doc.parts):
                one, some = len(blank) == 1, blank[:_MAX_PICTURES]
                blocks.append(_Note(
                    f"{'Page ' + str(blank[0]) + ' has' if one else _plural(len(blank), 'page') + ' have'} no text "
                    f"(probably {'a scan' if one else 'scans'}): "
                    f"{_call('document', doc.uri, pages=_page_numbers(some), pictures=True)} draws "
                    f"{'it' if len(some) == 1 else 'them'}."))
            shown_blocks, cut = _pdf_blocks(doc, drawn, max_chars)
            blocks += shown_blocks + ([stopped] if cut else [])
            left_out = pdf_furniture(doc) if any(n not in drawn for n in doc.numbers) else []
            if left_out:
                said = [f"“{_clip(text, 60)}”" for text in left_out if text != "page numbers"][:2]
                said += ["page numbers"] if left_out[-1] == "page numbers" else []
                blocks.append(_Note(
                    f"Left out what repeats at the top and bottom of the pages ({', '.join(said)}), so the text "
                    f"reads on; {_call('ui.core.read_document', doc.uri)}.text has every line."))
            self._show(blocks)
            return
        name, shown = ("Page" if doc.kind == "pdf" else "Slide"), 0
        for i, (number, part) in enumerate(zip(doc.numbers, doc.parts)):
            if shown >= max_chars:
                blocks.append(stopped)
                break
            title = doc.slide_titles[i] if doc.kind == "pptx" else None
            notes = doc.notes[i] if doc.kind == "pptx" and doc.notes[i] else ""
            text = part + (f"\n\nSpeaker notes:\n{notes}" if notes else "")
            heading = f"{name} {number}" + (f": {title}" if title else "")
            if number in drawn:
                blocks.append(_Pages([drawn[number]], title=heading))
                if text.strip():
                    blocks.append(_Text(text[: max_chars - shown], title=f"Text of page {number}", wrap=True,
                                        collapsed=True))
            else:
                blocks.append(_Text(text[: max_chars - shown] or "(no text)", title=heading, wrap=True))
            shown += len(text)
        self._show(blocks)

    @_friendly_errors
    def link(self, uri: str, *, expires: int = 3600) -> None:
        """
        Clickable presigned download link (no AWS login needed until it expires).
        """
        url = self.core.presigned_url(uri, expires=expires)
        self._show(
            [
                _Link(
                    url,
                    f"Download {parse_s3_uri(uri)[1].rsplit('/', 1)[-1]} (valid {expires // 60} min)",
                )
            ]
        )
