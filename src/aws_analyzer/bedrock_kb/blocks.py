"""
bedrock_kb's own render blocks and their drawings: passages, links, steps, the pipeline, a file's
chunks and shares of a whole.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any

from .._bedrock.estimates import estimate_tokens
from .._bedrock.models import Passage
from .._bedrock.words import _highlight, best_snippet
from .._kit.blocks import _Report, _draws
from .._kit.render import _TONES, _prose
from .._kit.text import _esc, _text_bar


@dataclass
class _Passage:
    rank: int
    score_share: (
        float | None
    )  # 0..1: the score against the top result's, drawn as a bar
    source: str  # 'refund-policy.pdf'
    detail: str  # 'p.3'
    text: str  # the snippet shown
    terms: list[str] = field(default_factory=list)  # words to highlight
    score: float | None = None
    meta: str = ""  # the passage's metadata, e.g. 'team=billing · year=2024'
    url: str = ""  # opens the source's file in a new tab (file_url); '' = the name stays text


@dataclass
class _Link:
    """
    A link that opens in a new tab: a line of its own, or a table cell (the label as a link). Text mode shows
    the label, then the address on its own line; in a table, the label only.
    """

    url: str
    label: str

    def __str__(self) -> str:
        return self.label


@dataclass
class _Steps:
    """
    How something happened, a step at a time: a dot per step on a line (HTML), a numbered list (text).
    """

    items: list[tuple[str, str, str, str]]  # (step, what happened, detail, tone 'ok' | 'warn' | 'bad' | '')
    title: str = ""


@dataclass
class _Pipeline:
    """
    How a knowledge base turns each data source into vectors: a row of stages per data source, left to right.
    """

    rows: list[tuple[str, list[tuple[str, str, str, str]]]]  # (data source, [(stage, value, detail, tone)])
    title: str = ""


@dataclass
class _Chunks:
    """
    A file's chunks in document order: a bar of their sizes (and where they sit in the file, when its text was
    read), then each one folded under a line with its number, page, size and first words, opening to its full text
    (what it repeats from the chunk before it marked) and its metadata.
    """

    items: list[tuple[int, Passage, int]]  # (number, chunk, characters it repeats from the chunk before it)
    title: str = ""
    spans: list[tuple[float, float]] = field(default_factory=list)  # where each chunk sits in the file, 0..1
    coverage: float | None = None  # the share of the file's text inside a chunk
    terms: list[str] = field(default_factory=list)  # words to highlight


@dataclass
class _Shares:
    """
    Parts of a whole as one bar with a colour for each part, and a legend: a knowledge base's files by state.
    """

    items: list[tuple[str, int, str]]  # (label, count, kind): kind picks the colour (a FILE_STATES key)
    title: str = ""


def _link_html(url: str, text: str) -> str:
    """
    text as a link that opens url in a new tab; only http(s) links, so anything else stays text.
    """
    if not url.lower().startswith(("https://", "http://")):
        return _esc(text)
    return (
        f'<a class="fl" href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
        f'title="Open in a new tab">{_esc(text)}</a>'
    )


@_draws(_Steps, "html")
def _steps_html(block: _Steps, report: _Report) -> str:
    rows = "".join(
        f'<div class="st {tone if tone in _TONES else ""}"><span class="sd"></span><div class="sb">'
        f'<span class="sk">{_esc(step)}</span><span class="sv">{_esc(value)}</span>'
        + (f'<div class="sx">{_prose(detail)}</div>' if detail else "")
        + "</div></div>"
        for step, value, detail, tone in block.items
    )
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return f'{title}<div class="steps">{rows}</div>'


@_draws(_Pipeline, "html")
def _pipeline_html(block: _Pipeline, report: _Report) -> str:
    out = [f"<h4>{_prose(block.title)}</h4>"] if block.title else []
    for label, stages in block.rows:
        cells = '<span class="pa">→</span>'.join(
            f'<div class="ps {tone if tone in _TONES else ""}"><div class="pk">{_esc(stage)}</div>'
            f'<div class="pv">{_esc(value)}</div>' + (f'<div class="pd">{_prose(detail)}</div>' if detail else "")
            + "</div>"
            for stage, value, detail, tone in stages
        )
        out.append(f'<div class="pipe"><div class="pl">{_esc(label)}</div><div class="pr">{cells}</div></div>')
    return "".join(out)


def _raw_offset(text: str, flat_offset: int) -> int:
    """
    Where in `text` the character `flat_offset` of its whitespace-collapsed form (_single_spaced) is: the overlap a chunk
    repeats is measured on the collapsed text, but marked in the text as written.
    """
    count, pending = 0, False
    for i, ch in enumerate(text):
        if ch.isspace():
            pending = count > 0
            continue
        if pending:
            count, pending = count + 1, False  # the one space _single_spaced keeps for a run of whitespace
        if count >= flat_offset:
            return i
        count += 1
    return len(text)


@_draws(_Chunks, "html")
def _chunks_html(block: _Chunks, report: _Report) -> str:
    out = [f"<h4>{_prose(block.title)}</h4>"] if block.title else []
    if block.items:
        bars = "".join(
            f'<i class="{"tiny" if len(p.text.split()) < 20 else ""}" style="flex:{max(1, estimate_tokens(p.text))} 1 0"'
            f' title="#{n}{f" · p.{p.page}" if p.page is not None else ""} · ~{estimate_tokens(p.text):,} tokens"></i>'
            for n, p, _ in block.items
        )
        legend = "Each bar is a chunk, as wide as its text" + (", amber when under 20 words" if any(
            len(p.text.split()) < 20 for _, p, _ in block.items) else "")
        out.append(f'<div class="ckbar">{bars}</div>')
        if block.spans:
            marks = "".join(f'<i style="left:{a * 100:.2f}%;width:{max(0.3, (b - a) * 100):.2f}%"></i>'
                            for a, b in block.spans)
            out.append(f'<div class="ckpos" title="Green where a chunk holds the file\'s text">{marks}</div>')
            legend += (f" · under it, the file: green where a chunk holds its text, red where none does "
                       f"({block.coverage:.0%} covered)" if block.coverage is not None else "")
        out.append(f'<div class="cklg">{_esc(legend)}</div>')
    for n, p, overlap in block.items:
        tokens, words = estimate_tokens(p.text), len(p.text.split())
        page = f'<span class="ckp">p.{p.page}</span>' if p.page is not None else ""
        shared = (f'<span class="cko" title="Its first {overlap:,} characters repeat the end of chunk #{n - 1}">'
                  f"↩ {overlap:,} chars shared</span>" if overlap else "")
        score = f'<span class="cks">score {p.score:.2f}</span>' if p.score is not None and block.terms else ""
        cut = _raw_offset(p.text, overlap) if overlap else 0
        body = (f'<mark class="ov">{_highlight(p.text[:cut], block.terms)}</mark>' if cut else "") + _highlight(
            p.text[cut:], block.terms)
        meta = " · ".join(filter(None, [_meta_label(p.metadata), f"chunk {p.chunk_id}" if p.chunk_id else ""]))
        out.append(
            f'<details class="ck"><summary><span class="ckn">{n}</span>{page}<span class="ckz">~{tokens:,} tokens · '
            f'{words:,} words</span>{shared}<span class="ckw">{_esc(best_snippet(p.text, block.terms, 140))}</span>'
            f"{score}</summary><div class=\"ckt\">{body or '(no text)'}</div>"
            + (f'<div class="ckm">{_esc(meta)}</div>' if meta else "") + "</details>"
        )
    return "".join(out)


@_draws(_Shares, "html")
def _shares_html(block: _Shares, report: _Report) -> str:
    total = sum(count for _, count, _ in block.items) or 1
    bar = "".join(f'<i class="s-{_esc(kind)}" style="flex:{count} 1 0" title="{_esc(label)}: {count:,} '
                  f'({count / total:.0%})"></i>' for label, count, kind in block.items if count)
    legend = "".join(f'<span><i class="s-{_esc(kind)}"></i>{_esc(label)} <b>{count:,}</b></span>'
                     for label, count, kind in block.items if count)
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return f'{title}<div class="shr">{bar}</div><div class="shl">{legend}</div>'


_STEP_MARKS = {"ok": "[ok]", "warn": "[!]", "bad": "[x]"}  # text mode's mark for a step's tone; '[ ]' for none


@_draws(_Steps, "text")
def _steps_lines(block: _Steps, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    for i, (step, value, detail, tone) in enumerate(block.items, 1):
        out.append(f"{i}. {_STEP_MARKS.get(tone, '[ ]')} {step}: {value}")
        if detail:
            out += textwrap.wrap(detail, 100, initial_indent="      ", subsequent_indent="      ")
    return out


@_draws(_Pipeline, "text")
def _pipeline_lines(block: _Pipeline, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    for label, stages in block.rows:
        out.append(f"{label}:")
        for stage, value, detail, tone in stages:
            out.append(f"  {_STEP_MARKS.get(tone, '[ ]')} {stage}: {value}" + (f" ({detail})" if detail else ""))
    return out


@_draws(_Chunks, "text")
def _chunks_lines(block: _Chunks, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    shown = block.items if not report.max_rows else block.items[:report.max_rows]
    for n, p, overlap in shown:
        where = f" p.{p.page}" if p.page is not None else ""
        shared = f", {overlap:,} characters shared with #{n - 1}" if overlap else ""
        out.append(f"#{n}{where} ~{estimate_tokens(p.text):,} tokens, {len(p.text.split()):,} words{shared}")
        out += textwrap.wrap(best_snippet(p.text, block.terms, 300), 100, initial_indent="    ",
                             subsequent_indent="    ") or ["    (no text)"]
    if len(block.items) > len(shown):
        out.append(f"... {len(block.items) - len(shown):,} more chunks not shown (ui.max_rows = 0 shows all)")
    return out


@_draws(_Shares, "text")
def _shares_lines(block: _Shares, report: _Report) -> list[str]:
    total = sum(count for _, count, _ in block.items) or 1
    out = ["", f"-- {block.title} --"] if block.title else [""]
    return out + [f"  {label.ljust(22)} {count:>7,}  {_text_bar(count / total)}" for label, count, _ in block.items]


@_draws(_Passage, "html")
def _passage_block_html(block: _Passage, report: _Report) -> str:
    head = " · ".join(filter(None, [
        _esc(f"#{block.rank}"),
        _link_html(block.url, block.source),
        _esc(block.detail),
        "" if block.score is None else f"score {block.score:.2f}",
    ]))
    bar = ""
    if block.score_share is not None:
        pct = max(0.0, min(1.0, block.score_share)) * 100
        bar = f'<span class="track"><span class="fill" style="width:{pct:.1f}%"></span></span>'
    meta = f'<div class="pm">{_esc(block.meta)}</div>' if block.meta else ""
    return (f'<div class="psg"><div class="ph">{bar}{head}</div>{meta}'
            f'<div class="pt">{_highlight(block.text, block.terms)}</div></div>')


@_draws(_Passage, "text")
def _passage_block_lines(block: _Passage, report: _Report) -> list[str]:
    score = "" if block.score is None else f" (score {block.score:.2f})"
    out = ["", f"[{block.rank}] {block.source}" + (f" {block.detail}" if block.detail else "") + score]
    if block.meta:
        out.append("    " + block.meta)
    return out + (textwrap.wrap(block.text, 100, initial_indent="    ", subsequent_indent="    ") or ["    (no text)"])


@_draws(_Link, "html")
def _link_block_html(block: _Link, report: _Report) -> str:
    return f'<div class="lnk">{_link_html(block.url, block.label)}</div>'


@_draws(_Link, "text")
def _link_block_lines(block: _Link, report: _Report) -> list[str]:
    return [block.label + ":", block.url]


@_draws(_Link, "cell")
def _link_cell_html(cell: _Link, text: str) -> str:
    return _link_html(cell.url, text) if text else _esc(text)


def _meta_label(metadata: dict[str, Any]) -> str:
    return " · ".join(f"{k}={v}" for k, v in sorted(metadata.items()))
