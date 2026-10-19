"""
PDFs read with pypdf and drawn with pypdfium2: each page's lines and pictures, the pages as
images, and the facts file_details() shows.
"""

from __future__ import annotations

import io
import math
import re
from collections import Counter
from contextlib import suppress
from typing import Any, Callable, Iterable

from ..._kit.deps import _require

from ..helpers import KB
from ..models import Document, PdfLine, PdfPage, Picture
from .formats import _READ_ERRORS


def parse_pdf(
    source: Any,
    uri: str = "",
    *,
    pages: Iterable[int] | None = None,
    password: str | None = None,
    layout: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> Document:
    """
    A PDF (path or seekable binary file) -> Document with one part per page. Needs pypdf.
    pages: 1-based page numbers to read. Scanned pages have no text layer and come back empty.
    layout=True also keeps each page's lines with where they start, their size and whether they're bold
    (doc.layout), which pdf_flow turns into headings, paragraphs and lists. progress gets (pages read, pages to read).
    """
    pypdf = _require("pypdf", "Reading PDF text")
    laid: list[PdfPage] = []
    try:
        reader = pypdf.PdfReader(source)
        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ValueError("The PDF is password-protected; pass password=")
        count = len(reader.pages)
        wanted = list(range(1, count + 1)) if pages is None else [int(n) for n in pages]
        bad = [n for n in wanted if not 1 <= n <= count]
        if bad:
            raise ValueError(f"Page {bad[0]} doesn't exist; the PDF has {count} pages")
        parts = []
        for number in wanted:
            page = reader.pages[number - 1]
            if layout:
                text, lines = _pdf_page_layout(page, number)
                laid.append(lines)
            else:
                text = page.extract_text()
            parts.append(text or "")
            if progress:
                progress(len(parts), len(wanted))
        meta = reader.metadata
        title, author = (meta.title, meta.author) if meta else (None, None)
    except pypdf.errors.PyPdfError as exc:
        raise ValueError(f"pypdf couldn't read this PDF: {exc}") from exc
    return Document(
        uri=uri,
        kind="pdf",
        parts=parts,
        numbers=wanted,
        title=title or None,
        author=author or None,
        page_count=count,
        layout=laid,
    )


_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demibold", re.I)  # a bold font, by its name: /ABCDEF+Carlito-Bold

_LIST_MARK = re.compile(r"[•◦▪▫●○■□‣⁃∙·\x7f-]|[-–—*]|\(?(?:\d{1,3}|[a-zA-Z])[.)]?")  # alone on a line


def _pdf_page_layout(page: Any, number: int) -> tuple[str, PdfPage]:
    """
    A pypdf page -> (its text, as extract_text() reads it, and its lines with where each starts, its size and
    weight). The lines are pypdf's, read through its visitor_text hook, except that a bullet or number drawn
    apart from its item joins the item's line, and so does a piece pypdf put on a line of its own although it
    sits on the line before (the next cell of a table row). A line pypdf can't place (text turned sideways, or a
    sideways page) gets no position, and pdf_flow lays it out by its text alone.
    """
    box = page.mediabox
    left, bottom, width, height = float(box.left), float(box.bottom), float(box.width), float(box.height)
    sideways = int(page.get("/Rotate", 0) or 0) % 180 != 0
    found: list[PdfLine] = []
    line: dict[str, Any] = {}

    def start() -> None:
        line.update(text="", at=None, sizes=Counter(), chars=0, bold=0, first="", pieces=0)

    def end() -> None:
        text = line["text"].strip()
        if line["pieces"] > 1 and line["first"].isdigit() and _LIST_MARK.fullmatch(line["first"]):
            text = line["first"] + "." + text[len(line["first"]):]  # '1' drawn before its item: '1. Hire ...'
        if text:
            x, y = line["at"] or (None, None)
            size = line["sizes"].most_common(1)[0][0] if line["sizes"] else 0.0
            found.append(PdfLine(text, x, y, size, line["bold"] * 2 > line["chars"]))
        start()

    def visit(text: Any, cm: Any, tm: Any, font: Any, size: Any) -> None:
        if not text:
            return
        at, points = None, 0.0
        try:  # where the text starts on the page: the text matrix times the current matrix
            a, b, c, d, e, f = (float(v) for v in tm)
            ca, cb, cc, cd, ce, cf = (float(v) for v in cm)
            x, y = e * ca + f * cc + ce, e * cb + f * cd + cf
            across = (a * ca + b * cc, a * cb + b * cd)
            points = float(size) * math.hypot(c * ca + d * cc, c * cb + d * cd)
            if (not sideways and abs(across[0]) > abs(across[1]) and left <= x <= left + width
                    and bottom <= y <= bottom + height):
                at = (round(x - left, 1), round(bottom + height - y, 1))
        except (TypeError, ValueError):
            pass
        heavy = bool(_BOLD_FONT.search(str(font.get("/BaseFont", "")))) if hasattr(font, "get") else False
        for i, piece in enumerate(str(text).split("\n")):
            if i:
                end()
            if piece.strip():
                line["at"] = line["at"] or at
                line["first"] = line["first"] or piece.strip()
                line["pieces"] += 1
            line["text"] += piece
            chars = sum(not ch.isspace() for ch in piece)
            if chars and points > 0:
                line["sizes"][round(points * 2) / 2] += chars
                line["chars"] += chars
                line["bold"] += chars * heavy

    start()
    text = page.extract_text(visitor_text=visit)
    end()
    lines: list[PdfLine] = []
    for item in found:
        prev = lines[-1] if lines else None
        if prev is not None and None not in (prev.x, prev.y, item.x, item.y) and item.x > prev.x:
            apart, em = abs(item.y - prev.y), max(prev.size, item.size, 1.0)
            if apart < 0.6 * em and _LIST_MARK.fullmatch(prev.text):
                mark = prev.text + "." if prev.text.isdigit() else prev.text
                lines[-1] = PdfLine(f"{mark} {item.text}", prev.x, item.y, item.size, item.bold)
                continue
            if apart < 0.3 * em:
                lines[-1] = PdfLine(f"{prev.text} {item.text}", prev.x, prev.y, max(prev.size, item.size),
                                    prev.bold and item.bold)
                continue
        lines.append(item)
    return text or "", PdfPage(number, round(width, 1), round(height, 1), lines)


def _encode_picture(image: Any) -> tuple[bytes, str]:
    """
    A pillow image -> (bytes, MIME type): PNG, which keeps text and lines sharp, unless JPEG is much
    smaller (a scan or a photo).
    """
    if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
        image = image.convert("RGB")
    png = io.BytesIO()
    image.save(png, "PNG")
    if image.mode in ("1", "LA", "P", "RGBA") or png.tell() <= 128 * KB:  # transparency needs PNG
        return png.getvalue(), "image/png"
    jpeg = io.BytesIO()
    image.save(jpeg, "JPEG", quality=85)
    return (jpeg.getvalue(), "image/jpeg") if jpeg.tell() * 1.5 < png.tell() else (png.getvalue(), "image/png")


def render_pdf_pages(source: Any, pages: Iterable[int] | None = None, *, width: int = 1000,
                     password: str | None = None, max_pages: int | None = None,
                     progress: Callable[[int, int], None] | None = None) -> list[Picture]:
    """
    Pictures of a PDF's pages as they print, scanned pages included (source: path, bytes or seekable
    binary file). Needs pypdfium2 and pillow. pages: 1-based page numbers (default: all of them, or the
    first max_pages); width: pixels across a page (a very tall page is drawn narrower, at most 1.5 x width
    high). progress gets (pages drawn, pages to draw).
    """
    pdfium = _require("pypdfium2", "Seeing PDF pages as pictures")
    _require("PIL.Image", "Seeing PDF pages as pictures", "pillow")
    try:
        pdf = pdfium.PdfDocument(source, password=password)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise ValueError("The PDF is password-protected; pass password=") from exc
        raise ValueError(f"pypdfium2 couldn't open this PDF: {exc}") from exc
    try:
        count = len(pdf)
        wanted = list(range(1, count + 1)) if pages is None else [int(n) for n in pages]
        bad = [n for n in wanted if not 1 <= n <= count]
        if bad:
            raise ValueError(f"Page {bad[0]} doesn't exist; the PDF has {count} pages")
        wanted = wanted if max_pages is None else wanted[:max_pages]
        pictures = []
        for number in wanted:
            page = pdf[number - 1]
            try:
                page_width, page_height = page.get_size()
                scale = min(width / page_width, 1.5 * width / page_height) if page_width > 0 and page_height > 0 else 1
                data, mime = _encode_picture(page.render(scale=scale).to_pil())
            except pdfium.PdfiumError as exc:
                raise ValueError(f"pypdfium2 couldn't draw page {number}: {exc}") from exc
            finally:
                page.close()
            pictures.append(Picture(data, mime, f"Page {number}", page=number))
            if progress:
                progress(len(pictures), len(wanted))
        return pictures
    finally:
        pdf.close()


# ---- What a file's own bytes say about it (describe_file): PDF facts, picture size, audio / video length, sheets

_PAPER_SIZES = {  # portrait (width, height) in points, 1/72 inch
    "A3": (842, 1191),
    "A4": (595, 842),
    "A5": (420, 595),
    "Letter": (612, 792),
    "Legal": (612, 1008),
    "Tabloid": (792, 1224),
}

_PDF_TEXT_PAGES = 3  # describe_file checks this many first pages of a PDF for text


def _paper_size(width: float, height: float) -> str:
    """
    A page size in words: 'A4', 'Letter landscape', or '13.3 × 7.5 in' (a 16:9 slide).
    """
    short, long = sorted((abs(width), abs(height)))
    for name, (w, h) in _PAPER_SIZES.items():
        if abs(short - w) <= 3 and abs(long - h) <= 3:
            return name + (" landscape" if abs(width) > abs(height) else "")
    return f"{abs(width) / 72:.1f} × {abs(height) / 72:.1f} in"


def _meta_text(value: Any) -> str | None:
    text = " ".join(str(value).replace("\x00", "").split()) if value is not None else ""
    return text[:200] or None


def _pdf_facts(source: Any, facts: dict[str, Any], password: str | None = None) -> None:
    """
    Fill `facts` from a PDF (seekable binary file): 'pages', 'title', 'author', 'made_with' (the app that wrote
    it), 'created', 'version', 'page_size', and 'text': whether its first pages have a text layer (False for
    scanned pages; None when it couldn't be checked). 'encrypted', and 'locked' when it needs a password. Needs
    pypdf. Only the page tree and the first pages are read, and facts found before an error stay in `facts`.
    """
    pypdf = _require("pypdf", "Counting PDF pages")
    try:
        reader = pypdf.PdfReader(source)
        header = getattr(reader, "pdf_header", "") or ""
        if header.startswith("%PDF-"):
            facts["version"] = header[5:]
        if reader.is_encrypted:
            facts["encrypted"] = True
            if not reader.decrypt(password or ""):
                facts["locked"] = True
                with suppress(Exception):  # the page count isn't encrypted, but may be missing
                    facts["pages"] = int(reader.trailer["/Root"]["/Pages"]["/Count"])
                return
        try:  # the count the page tree keeps, without loading every page
            facts["pages"] = int(reader.trailer["/Root"]["/Pages"]["/Count"])
        except (KeyError, TypeError, ValueError, AttributeError):
            facts["pages"] = len(reader.pages)
        meta = reader.metadata
        if meta:
            facts["title"], facts["author"] = _meta_text(meta.title), _meta_text(meta.author)
            facts["made_with"] = _meta_text(meta.creator) or _meta_text(meta.producer)
            with suppress(Exception):  # a malformed date
                facts["created"] = meta.creation_date
        try:
            pages = reader.pages
            first = [pages[i] for i in range(min(_PDF_TEXT_PAGES, facts["pages"] or len(pages)))]
            if first:
                box = first[0].mediabox
                facts["page_size"] = _paper_size(float(box.width), float(box.height))
                facts["text"] = any((page.extract_text() or "").strip() for page in first)
        except (*_READ_ERRORS, pypdf.errors.PyPdfError):  # pages too big to read here, or a broken one
            facts["text"] = None
    except pypdf.errors.PyPdfError as exc:
        raise ValueError(f"pypdf couldn't read this PDF: {exc}") from exc
