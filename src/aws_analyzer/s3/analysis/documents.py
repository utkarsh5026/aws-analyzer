"""
A PDF's text laid out to read: headings, paragraphs and lists, without the running headers
and footers.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from ..models import Document, PdfLine, PdfPage


# ---- A PDF's text laid out to read (pdf_flow): headings, paragraphs and lists from where its lines sit

_PDF_BULLET = re.compile(r"([•◦▪▫●○■□‣⁃∙·\x7f-]|[-–—*])\s+(?=\S)")  # '• ', Symbol / Wingdings bullets

_LIST_NUMBER = re.compile(r"\(?(?:\d{1,3}(?:\.\d{1,3})*|[a-zA-Z]|[ivxlcdm]{1,6})[.)]\s+(?=\S)")  # '1. ', 'a) ', '(iv) '

_PAGE_NUMBER = re.compile(
    r"(?:page\s+)?[-–—(\[]?\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*[-–—)\]]?(?:\s*(?:of|/)\s*\d{1,4})?", re.I)

_SENTENCE_END = re.compile(r"[.!?:;…。]['\"”’)\]]*$")

_PDF_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
                                "\xad": ""})


def _pdf_pages(doc: Document | Iterable[PdfPage]) -> list[PdfPage]:
    """
    The pages to lay out: a Document's layout (read with layout=True), else its pages' text as lines with no
    place or size; or the PdfPages given.
    """
    if not isinstance(doc, Document):
        return list(doc)
    return doc.layout or [PdfPage(number, lines=[PdfLine(text.strip()) for text in part.splitlines() if text.strip()])
                          for number, part in zip(doc.numbers, doc.parts)]


def _pdf_furniture(pages: list[PdfPage]) -> set[tuple[int, int]]:
    """
    (index in pages, line index) of the running headers and footers: lines in the top or bottom tenth of a page
    that come back on at least two pages and 40% of the pages with text (digits aside, so 'Page 3 of 9' does),
    and page numbers there.
    """
    edges: dict[tuple[int, int], str] = {}
    on_pages: dict[str, set[int]] = defaultdict(set)
    numbers = set()
    texts = [at for at, page in enumerate(pages) if page.lines]
    for at in texts:
        page = pages[at]
        for i, line in enumerate(page.lines):
            if line.y is None or not page.height or 0.1 * page.height <= line.y <= 0.9 * page.height:
                continue
            edges[(at, i)] = key = re.sub(r"\d+", "#", " ".join(line.text.lower().split()))
            on_pages[key].add(at)
            if _PAGE_NUMBER.fullmatch(line.text.strip()):
                numbers.add((at, i))
    need = max(2, math.ceil(0.4 * len(texts)))
    return numbers | {where for where, key in edges.items() if len(on_pages[key]) >= need}


def pdf_furniture(doc: Document | Iterable[PdfPage]) -> list[str]:
    """
    The running headers and footers pdf_flow leaves out of a PDF's text, each once ('ACME - Confidential'),
    then 'page numbers' if there were any.
    """
    pages = _pdf_pages(doc)
    said, numbers = {}, False
    for at, i in sorted(_pdf_furniture(pages)):
        text = pages[at].lines[i].text
        if _PAGE_NUMBER.fullmatch(text.strip()):
            numbers = True
        else:
            said.setdefault(re.sub(r"\d+", "#", text.lower()), text)
    return list(said.values()) + ["page numbers"] * numbers


def pdf_flow(doc: Document | Iterable[PdfPage]) -> list[tuple[str, Any]]:
    """
    A PDF's text laid out to read, from read_pdf(uri, layout=True) (or its doc.layout): ('page', number) where
    each page starts, then its headings ('title', 'h1' to 'h3' by font size; a short bold line is a heading too),
    paragraphs ('p': lines that run on are joined, a word split at the end of a line is made whole, and a line
    that ends short keeps its line break, '\\n', so table rows and addresses stay apart) and list items ('li',
    without the bullet; a numbered item keeps its number). Running headers and footers (pdf_furniture) are left
    out, and a page with no text is ('missing', a line saying so). A Document read without layout=True is laid
    out from its text alone: paragraphs, no headings.
    """
    pages = _pdf_pages(doc)
    skip = _pdf_furniture(pages)
    kept = [(page, [line for i, line in enumerate(page.lines) if (at, i) not in skip and line.text.strip()])
            for at, page in enumerate(pages)]
    every = [line for _, lines in kept for line in lines]
    sizes: Counter = Counter()
    for line in every:
        sizes[line.size] += len(line.text)
    body = sizes.most_common(1)[0][0] if sizes else 0.0  # the size most of the text is in
    bold_body = 2 * sum(len(line.text) for line in every if line.size == body and line.bold) > sizes[body]

    def big(line: PdfLine) -> bool:
        return body > 0 and line.size >= body * 1.15

    def heading(line: PdfLine) -> bool:
        if sum(ch.isalnum() for ch in line.text) < 2 or len(line.text) > 160:
            return False
        return big(line) or (line.bold and not bold_body and line.size >= body * 0.95 and len(line.text) <= 90
                             and not line.text.endswith((".", ",", ";")))

    steps = sorted({round(line.size) for line in every if big(line) and heading(line)}, reverse=True)
    largest = [id(line) for line in every if steps and big(line) and heading(line) and round(line.size) == steps[0]]
    first = kept[0][1] if kept else []
    at = [i for i, line in enumerate(first) if id(line) in largest]
    titled = (len(steps) > 1 and pages[0].number == 1 and len(at) == len(largest) <= 3  # one big title on page 1,
              and at == list(range(at[0], at[0] + len(at)))  # on up to 3 lines that read as one heading
              and all(b.y is None or a.y is None or 0 < b.y - a.y < 1.4 * b.size
                      for a, b in zip(first[at[0]:], first[at[0] + 1:at[-1] + 1])))

    def role(line: PdfLine) -> str:
        if not big(line):  # a bold line in the body's size: a level under the sized headings
            return f"h{max(1, min(len(steps) - titled + 1, 3))}"
        rank = steps.index(round(line.size)) - titled
        return "title" if rank < 0 else f"h{min(rank + 1, 3)}"

    gaps: list[float] = []
    for _, lines in kept:
        gaps += [b.y - a.y for a, b in zip(lines, lines[1:]) if a.y is not None and b.y is not None
                 and a.size == b.size == body and 0 < b.y - a.y < 3 * max(body, 1)]
    step = sorted(gaps)[(len(gaps) - 1) // 2] if gaps else 1.2 * max(body, 1)  # from a line of body text to the next

    def measure(lines: list[PdfLine]) -> float:  # characters in a full line of body text
        lengths = sorted(len(line.text) for line in lines if line.size == body)
        return lengths[int(len(lengths) * 0.9)] if lengths else 0

    whole = measure(every)
    items: list[tuple[str, Any]] = []
    for page, lines in kept:
        items.append(("page", page.number))
        if not any(line.text.strip() for line in page.lines):
            items.append(("missing", "No text on this page (probably a scan), so only its picture shows what's on it"))
            continue
        width = max(measure(lines) if sum(line.size == body for line in lines) >= 8 else whole, 40)
        prev: PdfLine | None = None
        start_x: float | None = None  # where the paragraph or list item being read starts
        for line in lines:
            text, last = line.text, items[-1][0]
            gap = line.y - prev.y if prev is not None and prev.y is not None and line.y is not None else None
            same = prev is not None and abs(prev.size - line.size) < 0.5 and prev.bold == line.bold
            wrapped = prev is not None and len(prev.text) + 1 + len(text.split()[0]) > 0.85 * width
            ends = prev is not None and bool(_SENTENCE_END.search(prev.text))
            near = gap is not None and 0 < gap <= step * 1.4
            runs_on = same and last in ("p", "li") and (
                (near and not (line.x is not None and start_x is not None and last == "li" and line.x <= start_x + 1)
                 and not (line.x is not None and prev.x is not None and line.x > prev.x + 0.8 * line.size
                          and last == "p" and (ends or not wrapped)))  # a first-line indent starts a paragraph
                or (gap is None and (wrapped or not ends))
                or (gap is not None and (gap < 0 or gap > 3 * step) and wrapped and not ends))  # the next column
            bullet = _PDF_BULLET.match(text)
            marker = bullet or _LIST_NUMBER.match(text)
            plain = bullet is not None and bullet.group(1) not in "-–—*"  # a bullet, not a dash or a number
            if heading(line):
                if last in ("title", "h1", "h2", "h3") and prev is not None and same and heading(prev) and (
                        gap is None or 0 < gap < 1.4 * line.size):
                    items[-1] = (last, f"{items[-1][1]} {text}")  # a heading on two lines
                else:
                    items.append((role(line), text))
            elif marker and (plain or not (runs_on and last == "p" and wrapped and not ends)):  # not '...in\n2. ...'
                items.append(("li", text[bullet.end():] if bullet else text))
                start_x = line.x
            elif runs_on:
                before = items[-1][1]
                if before.endswith("\xad"):
                    joined = before + text
                elif wrapped and re.search(r"[^\W\d_]-$", before) and text[:1].islower():
                    joined = before[:-1] + text  # a word split at the end of the line
                else:
                    joined = before + (" " if wrapped else "\n") + text
                items[-1] = (last, joined)
            else:
                items.append(("p", text))
                start_x = line.x
            prev = line
    return [(role, value.translate(_PDF_LIGATURES) if role != "page" else value) for role, value in items]
