"""
The render blocks both Bedrock modules draw (and the agent's answers): _Answer, _Json and _Download, with
their HTML and text drawings, and the file list's blocks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from .._kit.blocks import _Cards, _Note, _Report, _Text, _Title, _draws
from .._kit.fmt import _plural, human_size
from .._kit.render import _prose
from .._kit.text import _esc

from .models import Citation
from .files import FILE_LIST_LINKS, SavedFileList, _link_life
from .markdown import _answer_lines, _markdown_html, _with_markers


@dataclass
class _Answer:
    """
    A render block: an answer laid out from its markdown, with its cited spans shaded and [n] after each.
    `inline` is True when the text already holds the [n] markers (engine='converse').
    """

    text: str
    citations: list[Citation] = field(default_factory=list)
    inline: bool = False


@dataclass
class _Json:
    """
    A render block: JSON with its keys and values in colour, each object and list folding under a click.

        open_depth  levels shown open (HTML); deeper objects are folded
        marks       path -> why it's highlighted (your settings)
        notes       path -> a note shown after the value
        collapsed   folded under its title in HTML
    """

    value: Any
    title: str = ""
    open_depth: int = 8
    marks: dict[tuple[str, ...], str] = field(default_factory=dict)
    notes: dict[tuple[str, ...], str] = field(default_factory=dict)
    collapsed: bool = False


def _plain_json(value: Any) -> Any:
    """
    A deep copy holding only JSON types: tuples become lists, dates ISO text, anything else its str().
    """
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)))


def _json_html(
    value: Any,
    *,
    open_depth: int = 8,
    marks: dict[tuple[str, ...], str] | None = None,
    notes: dict[tuple[str, ...], str] | None = None,
) -> str:
    """
    JSON as highlighted HTML: keys, text, numbers and true / false / null in their own colours, each object and list
    folding under a click (plain <details>, no script), paths in `marks` highlighted with a tooltip, and `notes`
    after their values. Every piece of text is escaped: responses quote untrusted knowledge base content.
    """
    marks, notes = marks or {}, notes or {}

    def scalar(v: Any) -> str:
        if v is None or isinstance(v, bool):
            return f'<span class="jl">{json.dumps(v)}</span>'
        if isinstance(v, (int, float)):
            return f'<span class="jn">{_esc(json.dumps(v))}</span>'
        text = v if isinstance(v, str) else (v.isoformat() if hasattr(v, "isoformat") else str(v))
        return f'<span class="js">{_esc(json.dumps(text, ensure_ascii=False))}</span>'

    def node(label: str, v: Any, path: tuple[Any, ...], depth: int, comma: bool) -> str:
        tail = "," if comma else ""
        note = f'<span class="jc">{_esc(notes[path])}</span>' if path in notes else ""
        if path in marks:
            label = f'<span class="jm" title="{_esc(marks[path])}">{label}</span>'
        items = list(v.items()) if isinstance(v, dict) else list(enumerate(v)) if isinstance(v, (list, tuple)) else None
        if not items:
            body = scalar(v) if items is None else ("{}" if isinstance(v, dict) else "[]")
            return f"<div>{label}{body}{tail}{note}</div>"
        opener, closer = ("{", "}") if isinstance(v, dict) else ("[", "]")
        count = _plural(len(items), "key" if isinstance(v, dict) else "item")
        children = "".join(
            node(f'<span class="jk">{_esc(json.dumps(str(k), ensure_ascii=False))}</span>: '
                 if isinstance(v, dict) else "", child, (*path, k), depth + 1, i < len(items) - 1)
            for i, (k, child) in enumerate(items)
        )
        is_open = " open" if depth < open_depth else ""
        return (f"<details{is_open}><summary>{label}{opener}<span class=\"jx\"> {count} {closer}{tail}</span>{note}"
                f"</summary><div class=\"ji\">{children}</div><div>{closer}{tail}</div></details>")

    return f'<div class="json">{node("", value, (), 0, False)}</div>'


@_draws(_Json, "html")
def _json_block_html(block: _Json, report: _Report) -> str:
    tree = _json_html(block.value, open_depth=block.open_depth, marks=block.marks, notes=block.notes)
    if block.collapsed:
        return f'<details class="sec"><summary>{_prose(block.title or "JSON")}</summary>{tree}</details>'
    return (f"<h4>{_prose(block.title)}</h4>" if block.title else "") + tree


@_draws(_Json, "text")
def _json_block_lines(block: _Json, report: _Report) -> list[str]:
    head = ["", f"-- {block.title} --"] if block.title else []
    return head + [json.dumps(_plain_json(block.value), indent=2, ensure_ascii=False)]


def _answer_html(block: _Answer) -> str:
    """
    The answer, laid out from its markdown, with cited spans shaded and [n] superscripts. Every piece of text is
    escaped: answers can quote untrusted knowledge base content.
    """
    return _markdown_html(block.text, block.citations, block.inline)


@_draws(_Answer, "html")
def _answer_block_html(block: _Answer, report: _Report) -> str:
    return f'<div class="ans">{_answer_html(block)}</div>'


@_draws(_Answer, "text")
def _answer_block_lines(block: _Answer, report: _Report) -> list[str]:
    return _answer_lines(block.text if block.inline else _with_markers(block.text, block.citations), 100)


@dataclass
class _Download:
    """
    A file saved on the notebook's disk, with a link that downloads it to your computer: `href` is its path from
    the notebook's folder (Jupyter serves it from there) or a data: URL holding it; '' when there's no link to give.
    `path` is where it is (absolute). Text mode says where it is.
    """

    path: str
    href: str = ""


@_draws(_Download, "html")
def _download_html(block: _Download, report: _Report) -> str:
    name, folder = os.path.basename(block.path), os.path.dirname(block.path)
    if not block.href:
        return f'<div class="dlw"><span class="dlp">Saved in {_esc(block.path)}</span></div>'
    return (f'<div class="dlw"><a class="dl" href="{_esc(block.href)}" download="{_esc(name)}" '
            f'title="Download it to your computer">⬇ Download {_esc(name)}</a>'
            f'<span class="dlp">saved in {_esc(folder)}</span></div>')


@_draws(_Download, "text")
def _download_lines(block: _Download, report: _Report) -> list[str]:
    return [f"Saved in {block.path}"]


def _download_css(root: str) -> str:
    """
    The _Download block's rules, scoped to a service's root class.
    """
    return (f".{root} .dlw{{display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px;margin:6px 0 8px}}\n"
            f".{root} a.dl{{display:inline-block;padding:3px 12px;border:1px solid rgba(59,130,246,.45);"
            f"border-radius:999px;color:#3b82f6;font-weight:600;text-decoration:none}}\n"
            f".{root} a.dl:hover{{background:rgba(59,130,246,.1)}}\n"
            f".{root} .dlp{{font-size:12px;opacity:.65;overflow-wrap:anywhere}}")


def _file_list_blocks(saved: SavedFileList, kb_name: str, *, href: str = "", sorted_by: str = "sorted by path",
                      notes: Iterable[Any] = ()) -> list[Any]:
    """
    A saved list's report, without its next steps: what was written where, the link that downloads it, its first
    lines, `notes` (what the listing missed), and what's not in it.
    """
    what = FILE_LIST_LINKS[saved.link]
    cards: list[tuple[str, ...]] = [("Files", f"{saved.count:,}"), ("Each line", what),
                                    ("Size", human_size(saved.size))]
    if saved.expires:
        cards.append(("Links work for", _link_life(saved.expires)))
    blocks: list[Any] = [
        _Title(f"Saved a list of {_plural(saved.count, 'file')}",
               " · ".join(p for p in (kb_name, f"one {what} per line" + (f", {sorted_by}" if sorted_by else "")) if p)),
        _Cards(cards),
        _Download(saved.path, href),
    ]
    lines = saved.lines
    if lines:
        shown = lines[:10]
        title = (f"Its {_plural(len(lines), 'line')}" if len(lines) == len(shown)
                 else f"Its first {len(shown)} lines of {len(lines):,}")
        blocks.append(_Text("\n".join(shown), title=title, code=True))
    blocks += list(notes)
    if saved.replaced:
        blocks.append(_Note(f"It replaced the {saved.name} that was there."))
    if saved.left_out:
        why = ("a custom data source's documents have no s3:// path" if saved.link != "https"
               else "a custom data source's documents and files deleted from S3 have no file to open")
        blocks.append(_Note(f"{_plural(saved.left_out, 'file')} of the list aren't in it: {why}."))
    if saved.expires:
        blocks.append(_Note(f"Each link works for {_link_life(saved.expires)}, and only while the credentials that "
                            "signed it do and may read the file (s3:GetObject). Anyone holding the list can open "
                            "every file in it until then, so share it with care.", "warn"))
    return blocks
