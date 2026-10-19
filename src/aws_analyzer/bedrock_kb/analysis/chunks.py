"""
A file's chunks in document order: where each sits in the file's text, what it repeats from the
one before, and their sizes.
"""

from __future__ import annotations

from typing import Iterable

from ..._bedrock.estimates import estimate_tokens
from ..._bedrock.models import Passage

from ..models import ChunkStats


# --------------------------------------------------- a file's chunks, in order


def _single_spaced(text: str | None) -> str:
    return " ".join((text or "").split())


def chunk_overlap(before: str, after: str, *, longest: int = 4000) -> int:
    """
    How many characters the start of chunk `after` repeats from the end of chunk `before`, whitespace collapsed:
    the overlap fixed-size and hierarchical chunking leave at each boundary. Overlaps under 20 characters count as 0.
    """
    a, b = _single_spaced(before)[-longest:], _single_spaced(after)[:longest]
    head = b[:20]
    if len(head) < 20:
        return 0
    i = a.find(head)
    while i != -1:
        if b.startswith(a[i:]):
            return len(a) - i
        i = a.find(head, i + 1)
    return 0


def place_chunks(text: str, chunks: Iterable[Passage]) -> dict[str, tuple[int, int]]:
    """
    Where each chunk sits in a file's text -> {chunk key: (start, end)}, in characters of the text with its
    whitespace collapsed. A chunk is found by its first 80 characters, or failing that by 80 from its middle; one that
    isn't found (a parser can change what it reads) is left out.
    """
    flat = _single_spaced(text)
    spans: dict[str, tuple[int, int]] = {}
    for p in chunks:
        piece = _single_spaced(p.text)
        if not piece or p.key in spans:
            continue
        start = flat.find(piece[:80])
        if start == -1 and len(piece) > 160:
            middle = len(piece) // 2
            at = flat.find(piece[middle:middle + 80])
            start = max(0, at - middle) if at != -1 else -1
        if start != -1:
            spans[p.key] = (start, min(len(flat), start + len(piece)))
    return spans


def _chained(group: list[Passage]) -> list[Passage]:
    """
    Chunks put in order by the text each repeats from the one before it (chunk_overlap); chains start in the
    order the chunks came.
    """
    if len(group) < 2:
        return list(group)
    links = sorted(((chunk_overlap(a.text, b.text), i, j) for i, a in enumerate(group) for j, b in enumerate(group)
                    if i != j), reverse=True)
    after: dict[int, int] = {}
    before: dict[int, int] = {}
    for size, i, j in links:
        if not size:
            break
        if i in after or j in before:
            continue
        end = j
        while end in after:  # linking i -> j mustn't close a loop
            end = after[end]
        if end == i:
            continue
        after[i], before[j] = j, i
    ordered: list[Passage] = []
    seen: set[int] = set()
    for start in range(len(group)):
        if start in before or start in seen:
            continue
        at: int | None = start
        while at is not None and at not in seen:
            ordered.append(group[at])
            seen.add(at)
            at = after.get(at)
    return ordered + [p for i, p in enumerate(group) if i not in seen]


def order_chunks(chunks: Iterable[Passage], spans: dict[str, tuple[int, int]] | None = None) -> list[Passage]:
    """
    A file's chunks in document order, each once: by where it sits in the file's text when that's known
    (place_chunks), else by page and, within a page, by the text each repeats from the one before it. Chunks that
    can't be placed keep the order they were retrieved in.
    """
    unique = list({p.key: p for p in reversed(list(chunks))}.values())[::-1]
    spans = spans or {}
    placed = sorted((p for p in unique if p.key in spans), key=lambda p: spans[p.key])
    pages: dict[int | None, list[Passage]] = {}
    for p in unique:
        if p.key not in spans:
            pages.setdefault(p.page, []).append(p)
    rest = [p for page in sorted(pages, key=lambda pg: (pg is None, pg or 0)) for p in _chained(pages[page])]
    return placed + rest


def chunk_stats(chunks: list[Passage], spans: dict[str, tuple[int, int]] | None = None,
                text_length: int = 0) -> ChunkStats:
    """
    How a file's chunks (in document order) look: each one's estimated tokens and words, the pages they come
    from, how many are tiny or repeat another, the overlap at each boundary and, when the file's text was read
    (spans, text_length), the share of it inside a chunk.
    """
    stats = ChunkStats(count=len(chunks))
    seen: set[str] = set()
    for i, p in enumerate(chunks):
        stats.tokens.append(estimate_tokens(p.text))
        stats.words.append(len(p.text.split()))
        flat = _single_spaced(p.text).lower()
        if len(flat) >= 40 and flat in seen:
            stats.repeats += 1
        seen.add(flat)
        stats.overlaps.append(chunk_overlap(chunks[i - 1].text, p.text) if i else 0)
    stats.tiny = sum(1 for words in stats.words if words < 20)
    stats.pages = sorted({p.page for p in chunks if p.page is not None})
    spans = spans or {}
    stats.placed = sum(1 for p in chunks if p.key in spans)
    if text_length and stats.placed:
        covered, reach = 0, 0
        for start, end in sorted(spans[p.key] for p in chunks if p.key in spans):
            start = max(start, reach)
            if end > start:
                covered += end - start
                reach = end
        stats.coverage = min(1.0, covered / text_length)
    return stats
