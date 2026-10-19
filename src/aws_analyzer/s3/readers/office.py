"""
Word, PowerPoint and Excel files read with the standard library: their text, pictures,
properties and sheets, and what to say about the old binary formats.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from contextlib import suppress
from datetime import datetime
from typing import Any, Iterable
from xml.etree import ElementTree

from ..._kit.fmt import human_size

from ..helpers import KB, MB
from ..models import Document, Picture
from .formats import _IMAGE_MIMES


# ---- Documents: PDF (text with pypdf, pages drawn with pypdfium2), Word and PowerPoint (standard library)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"

_V = "{urn:schemas-microsoft-com:vml}"

_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_OFFICE_PARTS = {
    "word/document.xml": "docx",
    "ppt/presentation.xml": "pptx",
    "xl/workbook.xml": "excel",
}

_OLD_OFFICE = {
    "doc": "Word 97-2003 document",
    "dot": "Word 97-2003 template",
    "ppt": "PowerPoint 97-2003 deck",
    "pps": "PowerPoint 97-2003 show",
    "pot": "PowerPoint 97-2003 template",
    "msg": "Outlook message",
}


def _old_office_note(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key.rsplit("/", 1)[-1] else ""
    what = _OLD_OFFICE.get(ext, "Office 97-2003 file")
    modern = {"ppt": "pptx", "pps": "pptx", "pot": "pptx"}.get(ext, "docx")
    return (
        f"{what} (the old binary format): its text can't be read here. Save it as .{modern} in Office, "
        f"or convert it with LibreOffice (soffice --headless --convert-to {modern} FILE), then preview that."
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_xml(archive: zipfile.ZipFile, name: str, *, required: bool = True) -> Any:
    """
    Parse one XML part of an Office file (None if it's missing and not required).
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        if required:
            raise ValueError(f"Not a valid Office file: {name} is missing") from None
        return None
    if info.file_size > 256 * MB:
        raise ValueError(f"{name} unpacks to {human_size(info.file_size)}; not read")
    data = archive.read(info)
    if (
        b"<!DOCTYPE" in data or b"<!ENTITY" in data
    ):  # Office never writes these; refuse entity tricks
        raise ValueError(
            f"{name} has a DTD, which Office files never contain; not parsed"
        )
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:  # a SyntaxError, which the callers' error handling doesn't expect
        raise ValueError(f"{name} isn't valid XML ({exc}); the file is damaged") from exc


def _zip_rels(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """
    Relationships of an Office part -> {id: (type, path inside the zip)}.
    """
    folder, name = posixpath.split(part)
    root = _zip_xml(
        archive, posixpath.join(folder, "_rels", name + ".rels"), required=False
    )
    rels: dict[str, tuple[str, str]] = {}
    for rel in [] if root is None else root.iter(f"{_REL}Relationship"):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External" or not target:
            continue
        path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(folder, target))
        )
        rels[rel.get("Id", "")] = (rel.get("Type", "").rsplit("/", 1)[-1], path)
    return rels


def _office_properties(archive: zipfile.ZipFile) -> dict[str, Any]:
    """
    Title, author and the page / slide count the app saved (docProps/core.xml and app.xml).
    """
    props: dict[str, Any] = {}
    for part in ("docProps/core.xml", "docProps/app.xml"):
        root = _zip_xml(archive, part, required=False)
        for elem in [] if root is None else root:
            value = (elem.text or "").strip()
            if value:
                props[_local(elem.tag).lower()] = value
    modified = None
    with suppress(ValueError):  # ISO time, e.g. 2024-05-01T10:00:00Z
        modified = datetime.fromisoformat(props.get("modified", "").replace("Z", "+00:00"))
    return {
        "title": props.get("title"),
        "author": props.get("creator"),
        "pages": int(props["pages"]) if props.get("pages", "").isdigit() else None,
        "modified": modified,
    }


def office_kind(source: Any) -> str | None:
    """
    'docx', 'pptx' or 'excel' for an Office zip file (by the parts inside it), else None.
    """
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
    return next((kind for part, kind in _OFFICE_PARTS.items() if part in names), None)


def _word_text(paragraph: Any) -> str:
    out = []
    for elem in paragraph.iter():
        if (
            elem.tag == f"{_W}t"
        ):  # deleted text (w:delText) and field codes (w:instrText) are skipped
            out.append(elem.text or "")
        elif elem.tag == f"{_W}tab":
            out.append("\t")
        elif elem.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out).strip()


def _picture_mime(data: bytes) -> str:
    """
    MIME type of a picture a browser can show, from its first bytes; '' for EMF, WMF, TIFF and the rest.
    """
    mime = next((m for magic, m in _IMAGE_MIMES if data.startswith(magic)), "")
    if mime == "image/webp" and data[8:12] != b"WEBP":  # RIFF also starts WAV and AVI files
        mime = ""
    head = data[:2048].lstrip()
    if not mime and head.startswith((b"<svg", b"<?xml")) and b"<svg" in head:
        mime = "image/svg+xml"
    return mime


def _word_pictures(elem: Any) -> list[tuple[str, str]]:
    """
    (relationship id, alt text) of each picture in a Word paragraph or table, in order. A picture saved
    twice (the modern copy and its fallback for old Word versions) counts once.
    """
    found: dict[str, str] = {}
    for drawing in elem.iter(f"{_W}drawing"):
        blip = next(drawing.iter(f"{_A}blip"), None)
        ref = None if blip is None else blip.get(f"{_R}embed")
        if ref:
            props = next(drawing.iter(f"{_WP}docPr"), None)
            found.setdefault(ref, "" if props is None else props.get("descr") or props.get("title") or "")
    for image in elem.iter(f"{_V}imagedata"):  # older pictures, and the pictures of embedded objects
        ref = image.get(f"{_R}id")
        if ref:
            found.setdefault(ref, image.get("{urn:schemas-microsoft-com:office:office}title") or "")
    return list(found.items())


def parse_docx(source: Any, uri: str = "", *, pictures: bool = False) -> Document:
    """
    A Word .docx (path or binary file object) -> Document: paragraphs and tables in order, what each one
    is (doc.roles: title, heading, list item, table, paragraph), headings, list items prefixed with '- ',
    title and author. pictures=True also loads the pictures, in reading order (doc.pictures). No packages needed.
    """
    with zipfile.ZipFile(source) as archive:
        root = _zip_xml(archive, "word/document.xml")
        styles = _zip_xml(archive, "word/styles.xml", required=False)
        props = _office_properties(archive)
        rels = _zip_rels(archive, "word/document.xml") if pictures else {}
        style_names, based_on, numbered = {}, {}, set()
        for style in [] if styles is None else styles.iter(f"{_W}style"):
            style_id, name, parent = style.get(f"{_W}styleId"), style.find(f"{_W}name"), style.find(f"{_W}basedOn")
            style_names[style_id] = "" if name is None else name.get(f"{_W}val", "")
            based_on[style_id] = None if parent is None else parent.get(f"{_W}val")
            if style.find(f"{_W}pPr/{_W}numPr") is not None:
                numbered.add(style_id)

        def style_is_list(style_id: str | None) -> bool:  # bullets can come from the style or one it's based on
            for _ in range(10):
                if style_id is None:
                    return False
                if style_id in numbered:
                    return True
                style_id = based_on.get(style_id)
            return False

        doc = Document(uri=uri, kind="docx", title=props["title"], author=props["author"], page_count=props["pages"])

        def heading_level(paragraph: Any) -> int | None:
            properties = paragraph.find(f"{_W}pPr")
            if properties is None:
                return None
            style = properties.find(f"{_W}pStyle")
            style_id = "" if style is None else style.get(f"{_W}val", "")
            name = (style_names.get(style_id) or style_id).lower().replace(" ", "")
            if name == "title":
                return 0
            if match := re.fullmatch(r"heading(\d)", name):
                return int(match.group(1))
            outline = properties.find(f"{_W}outlineLvl")
            return None if outline is None else int(outline.get(f"{_W}val", "0")) + 1

        def add_pictures(elem: Any) -> None:  # each one goes after the text read so far
            for ref, alt in _word_pictures(elem) if pictures else []:
                kind, path = rels.get(ref, ("", ""))
                try:
                    info = archive.getinfo(path)
                except KeyError:
                    continue
                if kind == "image" and info.file_size <= 256 * MB:
                    data = archive.read(info)
                    doc.pictures.append(Picture(data, _picture_mime(data), posixpath.basename(path),
                                                part=len(doc.parts), alt=alt))

        def walk(container: Any) -> None:
            for child in container:
                if child.tag == f"{_W}p":
                    text = _word_text(child)
                    if text:
                        level = heading_level(child)
                        style = child.find(f"{_W}pPr/{_W}pStyle")
                        listed = (child.find(f"{_W}pPr/{_W}numPr") is not None
                                  or style_is_list(None if style is None else style.get(f"{_W}val")))
                        if level is not None:
                            doc.headings.append((level, text))
                        doc.parts.append(f"- {text}" if listed else text)
                        doc.roles.append("title" if level == 0 else f"h{level}" if level else "li" if listed else "p")
                    add_pictures(child)
                elif child.tag == f"{_W}tbl":
                    rows = [["\n".join(filter(None, (_word_text(p) for p in cell.iter(f"{_W}p"))))
                             for cell in row.findall(f"{_W}tc")] for row in child.findall(f"{_W}tr")]
                    if any(any(cells) for cells in rows):
                        doc.tables.append(rows)
                        doc.parts.append("\n".join(" | ".join(cells) for cells in rows))
                        doc.roles.append("table")
                    add_pictures(child)
                elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml", f"{_W}ins"):
                    walk(child)  # content controls and tracked insertions wrap ordinary paragraphs

        body = root.find(f"{_W}body")
        walk(root if body is None else body)
    return doc


def _drawing_text(paragraph: Any) -> str:
    parts = [
        (elem.text or "") if elem.tag == f"{_A}t" else "\n"
        for elem in paragraph.iter()
        if elem.tag in (f"{_A}t", f"{_A}br")
    ]
    return "".join(parts).strip()


def _slide_text(
    root: Any, skip_placeholders: tuple[str, ...] = ()
) -> tuple[str | None, list[str], list[list[list[str]]]]:
    """
    (title, paragraphs, tables) of a slide or notes page.
    """
    title, paragraphs, tables = None, [], []
    for shape in root.iter(f"{_P}sp"):
        placeholder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        kind = None if placeholder is None else placeholder.get("type")
        if kind in skip_placeholders:
            continue
        texts = [t for t in (_drawing_text(p) for p in shape.iter(f"{_A}p")) if t]
        if kind in ("title", "ctrTitle") and texts and title is None:
            title = " ".join(texts)
        paragraphs += texts
    for table in root.iter(f"{_A}tbl"):
        rows = [
            [
                "\n".join(
                    t for t in (_drawing_text(p) for p in cell.iter(f"{_A}p")) if t
                )
                for cell in row.findall(f"{_A}tc")
            ]
            for row in table.findall(f"{_A}tr")
        ]
        if any(any(cells) for cells in rows):
            tables.append(rows)
            paragraphs.append("\n".join(" | ".join(cells) for cells in rows))
    return title, paragraphs, tables


def parse_pptx(
    source: Any, uri: str = "", *, slides: Iterable[int] | None = None
) -> Document:
    """
    A PowerPoint .pptx -> Document: one part per slide in presentation order, slide titles,
    tables and speaker notes. slides: 1-based slide numbers to read. No packages needed.
    """
    with zipfile.ZipFile(source) as archive:
        presentation = _zip_xml(archive, "ppt/presentation.xml")
        rels = _zip_rels(archive, "ppt/presentation.xml")
        props = _office_properties(archive)
        paths = [
            rels[ref][1]
            for ref in (s.get(f"{_R}id") for s in presentation.iter(f"{_P}sldId"))
            if ref in rels
        ]
        wanted = (
            range(1, len(paths) + 1) if slides is None else [int(n) for n in slides]
        )
        bad = [n for n in wanted if not 1 <= n <= len(paths)]
        if bad:
            raise ValueError(
                f"Slide {bad[0]} doesn't exist; the deck has {len(paths)} slides"
            )
        doc = Document(
            uri=uri,
            kind="pptx",
            title=props["title"],
            author=props["author"],
            page_count=len(paths),
        )
        for number in wanted:
            path = paths[number - 1]
            title, paragraphs, tables = _slide_text(_zip_xml(archive, path))
            notes = ""
            for kind, notes_path in _zip_rels(archive, path).values():
                if kind == "notesSlide":
                    _, note_paragraphs, _ = _slide_text(
                        _zip_xml(archive, notes_path),
                        ("sldNum", "sldImg", "hdr", "ftr", "dt"),
                    )
                    notes = "\n".join(note_paragraphs)
            doc.parts.append("\n".join(paragraphs))
            doc.numbers.append(number)
            doc.slide_titles.append(title)
            doc.notes.append(notes)
            doc.tables += tables
    return doc


_SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _column_number(letters: str) -> int:
    """
    'A' -> 1, 'Z' -> 26, 'AA' -> 27.
    """
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - 64
    return number


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    """
    [{'sheet', 'rows', 'columns', 'hidden'}] for each sheet of an .xlsx, in order. The size is the used range
    Excel saves at the top of each sheet (header row included), so only that much of the sheet is unpacked;
    None when the file doesn't say (some tools don't save it).
    """
    workbook = _zip_xml(archive, "xl/workbook.xml")
    rels = _zip_rels(archive, "xl/workbook.xml")
    sheets = []
    for sheet in workbook.iter(f"{_SS}sheet"):
        entry: dict[str, Any] = {"sheet": sheet.get("name", ""), "rows": None, "columns": None,
                                 "hidden": sheet.get("state") in ("hidden", "veryHidden")}
        path = rels.get(sheet.get(f"{_R}id", ""), ("", ""))[1]
        try:
            with archive.open(path) as part:
                start = part.read(64 * KB)
        except KeyError:
            start = b""
        found = re.search(rb'<(?:\w+:)?dimension\s+ref="\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?"', start)
        if found:
            first_col, first_row, last_col, last_row = (g.decode() if g else None for g in found.groups())
            last_col, last_row = last_col or first_col, last_row or first_row
            entry["rows"] = int(last_row) - int(first_row) + 1  # pyright: ignore[reportArgumentType]
            entry["columns"] = _column_number(last_col) - _column_number(first_col) + 1  # pyright: ignore[reportArgumentType]
        sheets.append(entry)
    return sheets
