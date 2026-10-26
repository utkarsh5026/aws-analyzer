"""
An answer's markdown laid out as HTML, stdlib only, keeping each character's offset in the answer,
since citations are offsets into the raw text; and the answer as text lines with its [n] markers.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any, Iterable

from .._kit.render import _SELECT, _copy_button
from .._kit.text import _esc

from .models import Citation


_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")


def _split_marks(span: str) -> tuple[str, str]:
    """
    'returned within 14 days. ' -> ('returned within 14 days', '. '): where a citation marker goes.
    """
    stripped = span.rstrip()
    body = stripped.rstrip(".!?:;,")
    return body, span[len(body) :]


def _with_markers(text: str, citations: list[Citation]) -> str:
    """
    The answer with [n] markers after each cited span, before its closing punctuation: 'days [1].'
    """
    out, pos = [], 0
    for c in sorted((c for c in citations if c.sources), key=lambda c: c.end):
        end = min(len(text), c.end)
        if end <= pos:
            continue
        body, tail = _split_marks(text[pos:end])
        out.append(body + " " + "".join(f"[{n}]" for n in c.sources) + tail)
        pos = end
    return "".join(out) + text[pos:]


_MD_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")

_MD_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")

_MD_RULE_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")

_MD_ITEM_RE = re.compile(r"^([ \t]*)([-*+•]|\d{1,9}[.)])(?:[ \t]+|$)")

_MD_QUOTE_RE = re.compile(r"^ {0,3}>[ \t]?")

_MD_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")

_MD_TABLE_RULE_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")

_MD_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

_MD_AUTOLINK_RE = re.compile(r"<((?:https?://|mailto:)[^\s<>]+)>", re.IGNORECASE)

_MD_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

_MD_TAGS = {"em": ("<em>", "</em>"), "strong": ("<strong>", "</strong>"), "del": ("<del>", "</del>"),
            "strongem": ("<strong><em>", "</em></strong>")}

_MD_MAX_DEPTH = 8

_Line = tuple[int, str]


def _md_indent(line: str) -> int:
    """
    Columns of leading whitespace, a tab counting to the next multiple of 4.
    """
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4 - n % 4
        else:
            break
    return n


def _md_dedent(item: _Line, cols: int) -> _Line:
    offset, line = item
    i = n = 0
    while i < len(line) and n < cols and line[i] in " \t":
        n += 1 if line[i] == " " else 4 - n % 4
        i += 1
    return offset + i, line[i:]


def _md_lstrip(item: _Line) -> _Line:
    offset, line = item
    rest = line.lstrip()
    return offset + len(line) - len(rest), rest


def _md_cells(item: _Line) -> list[_Line]:
    """
    A table row's cells, on its unescaped pipes outside `code`, with a leading and trailing pipe dropped.
    """
    offset, line = item
    cells, start, k, tick = [], 0, 0, False
    while k < len(line):
        ch = line[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "`":
            tick = not tick
        elif ch == "|" and not tick:
            cells.append((offset + start, line[start:k]))
            start = k + 1
        k += 1
    cells.append((offset + start, line[start:]))
    if cells and not cells[0][1].strip() and line.lstrip().startswith("|"):
        cells = cells[1:]
    if cells and not cells[-1][1].strip() and line.rstrip().endswith("|"):
        cells = cells[:-1]
    return [(o + len(c) - len(c.lstrip()), c.strip()) for o, c in cells]


def _md_table_start(lines: list[_Line], i: int) -> bool:
    if i + 1 >= len(lines) or "|" not in lines[i][1] or not _MD_TABLE_RULE_RE.match(lines[i + 1][1]):
        return False
    rule = lines[i + 1][1]
    return "-" in rule and ("|" in rule or "|" in lines[i][1]) and (
        len(_md_cells(lines[i])) == len(_md_cells(lines[i + 1])))


def _md_starts_block(lines: list[_Line], i: int) -> bool:
    """
    Whether line i starts a block that ends a paragraph above it.
    """
    line = lines[i][1]
    return bool(_MD_FENCE_RE.match(line) or _MD_HEADING_RE.match(line) or _MD_RULE_RE.match(line)
                or _MD_QUOTE_RE.match(line) or _MD_ITEM_RE.match(line) or _md_table_start(lines, i))


def _md_blocks(lines: list[_Line], depth: int = 0) -> list[tuple[Any, ...]]:
    """
    Markdown lines -> blocks: ('p', lines), ('h', level, lines), ('hr',), ('code', language, lines),
    ('quote', blocks), ('list', ordered, start, items, loose) and ('table', aligns, header, rows).
    """
    blocks: list[tuple[Any, ...]] = []
    i = 0
    while i < len(lines):
        if not lines[i][1].strip():
            i += 1
            continue
        block, i = _md_block(lines, i, depth)
        blocks.append(block)
    return blocks


def _md_block(lines: list[_Line], i: int, depth: int) -> tuple[tuple[Any, ...], int]:
    """
    The block starting at line i, which isn't blank, and the line after it. Quotes and lists nested more than
    _MD_MAX_DEPTH deep are read as plain paragraphs.
    """
    offset, line = lines[i]
    fence = _MD_FENCE_RE.match(line)
    if fence:
        return _md_code(lines, i, fence)
    heading = _MD_HEADING_RE.match(line)
    if heading:
        text = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", line[heading.end():]).rstrip()
        return ("h", len(heading.group(1)), [(offset + heading.end(), text)]), i + 1
    if _MD_RULE_RE.match(line):
        return ("hr",), i + 1
    if _MD_QUOTE_RE.match(line) and depth < _MD_MAX_DEPTH:
        return _md_quote(lines, i, depth)
    if _MD_ITEM_RE.match(line) and depth < _MD_MAX_DEPTH:
        return _md_list(lines, i, depth)
    if _md_table_start(lines, i):
        return _md_table(lines, i)
    return _md_paragraph(lines, i)


def _md_code(lines: list[_Line], i: int, fence: re.Match) -> tuple[tuple[Any, ...], int]:
    """
    The fenced code block opening at line i, up to its closing fence (or the end), and the line after it.
    """
    mark, body = fence.group(2), []
    i += 1
    while i < len(lines):
        closing = lines[i][1].strip()
        if closing.startswith(mark) and not closing.strip(mark[0]):
            i += 1
            break
        body.append(_md_dedent(lines[i], len(fence.group(1))))
        i += 1
    return ("code", fence.group(3), body), i


def _md_quote(lines: list[_Line], i: int, depth: int) -> tuple[tuple[Any, ...], int]:
    """
    The quote starting at line i, its lines read as blocks of their own, and the line after it. An unquoted line
    goes on the quote's paragraph unless it starts a block.
    """
    inner = []
    while i < len(lines) and lines[i][1].strip():
        quoted = _MD_QUOTE_RE.match(lines[i][1])
        if not quoted and _md_starts_block(lines, i):
            break
        inner.append((lines[i][0] + quoted.end(), lines[i][1][quoted.end():]) if quoted else _md_lstrip(lines[i]))
        i += 1
    return ("quote", _md_blocks(inner, depth + 1)), i


def _md_align(rule: str) -> str:
    """
    A column's alignment from its cell in the table's rule: ':-:' centre, '-:' right, ':-' left, '-' none.
    """
    if rule.startswith(":") and rule.endswith(":"):
        return "c"
    if rule.endswith(":"):
        return "r"
    return "l" if rule.startswith(":") else ""


def _md_table(lines: list[_Line], i: int) -> tuple[tuple[Any, ...], int]:
    """
    The table whose header is line i (its rule is the next), each row cut or padded to the header's cells, and
    the line after it.
    """
    aligns = [_md_align(cell) for _, cell in _md_cells(lines[i + 1])]
    header, rows = _md_cells(lines[i]), []
    i += 2
    while i < len(lines) and "|" in lines[i][1] and lines[i][1].strip():
        cells = _md_cells(lines[i])[: len(header)]
        rows.append(cells + [(lines[i][0], "")] * (len(header) - len(cells)))
        i += 1
    return ("table", aligns, header, rows), i


def _md_paragraph(lines: list[_Line], i: int) -> tuple[tuple[Any, ...], int]:
    """
    The paragraph starting at line i, up to a blank line or the start of another block, and the line after it. An
    underline of = or - makes it a heading.
    """
    para = [_md_lstrip(lines[i])]
    i += 1
    level = 0
    while i < len(lines) and lines[i][1].strip():
        setext = _MD_SETEXT_RE.match(lines[i][1])
        if setext:
            level = 1 if setext.group(1)[0] == "=" else 2
            i += 1
            break
        if _md_starts_block(lines, i):
            break
        para.append(_md_lstrip(lines[i]))
        i += 1
    return (("h", level, para) if level else ("p", para)), i


def _md_list(lines: list[_Line], i: int, depth: int) -> tuple[tuple[Any, ...], int]:
    """
    The list starting at line i (its items' lines, nested ones dedented) and the line after it. Lenient where
    models are: any bullet character continues a bullet list, nested lists can be indented by 2, 3 or 4, and a
    paragraph's next line needn't be indented.
    """
    first = _MD_ITEM_RE.match(lines[i][1])
    assert first is not None
    indent, ordered = _md_indent(first.group(1)), first.group(2)[0].isdigit()
    start = int(first.group(2)[:-1]) if ordered else 1
    items: list[list[tuple[Any, ...]]] = []
    loose = False
    while i < len(lines):
        offset, line = lines[i]
        item = _MD_ITEM_RE.match(line)
        if (not item or _md_indent(item.group(1)) > indent or item.group(2)[0].isdigit() != ordered
                or _MD_RULE_RE.match(line)):
            break
        content = len(item.group(0)) if item.group(0).strip() != item.group(0) else len(item.group(0)) + 1
        body: list[_Line] = [(offset + item.end(), line[item.end():])]
        i += 1
        blank = False
        while i < len(lines):
            o, text = lines[i]
            if not text.strip():
                body.append((o, ""))
                blank = True
                i += 1
                continue
            cols = _md_indent(text)
            if cols > indent and not _MD_RULE_RE.match(text):
                body.append(_md_dedent(lines[i], min(cols, content)))
                loose = loose or (blank and not _MD_ITEM_RE.match(body[-1][1]))
                blank = False
                i += 1
                continue
            if blank or _md_starts_block(lines, i):
                break
            body.append(_md_lstrip(lines[i]))
            i += 1
        while body and not body[-1][1].strip():
            body.pop()
        items.append(_md_blocks(body, depth + 1))
        if blank and i < len(lines):
            follows = _MD_ITEM_RE.match(lines[i][1])
            if follows and _md_indent(follows.group(1)) <= indent and follows.group(2)[0].isdigit() == ordered:
                loose = True
            else:
                break
    return ("list", ordered, start, items, loose), i


def _md_source(lines: list[_Line]) -> tuple[str, list[int]]:
    """
    Lines joined with newlines, and where each character of that is in the answer.
    """
    parts: list[str] = []
    where: list[int] = []
    for k, (offset, line) in enumerate(lines):
        if k:
            parts.append("\n")
            where.append(lines[k - 1][0] + len(lines[k - 1][1]))
        parts.append(line)
        where.extend(range(offset, offset + len(line)))
    return "".join(parts), where


def _md_closing_bracket(s: str, k: int, j: int) -> int:
    """
    Where the [ at k closes, past escaped characters and nested brackets; j or more when it doesn't.
    """
    depth, n = 0, k
    while n < j:
        if s[n] == "\\":
            n += 2
            continue
        if s[n] == "[":
            depth += 1
        elif s[n] == "]":
            depth -= 1
            if depth == 0:
                return n
        n += 1
    return n


def _md_link_target_after(s: str, k: int, n: int, j: int) -> tuple[int, int, str, int] | None:
    """
    The link whose text opens at k and closes at n, with its url in the brackets after n: (text start, text end,
    url, after it), or None when they don't close on that line.
    """
    parens, m = 0, n + 1
    while m < j and s[m] != "\n":
        if s[m] == "(":
            parens += 1
        elif s[m] == ")":
            parens -= 1
            if parens == 0:
                target = s[n + 2:m].strip().split()
                return k + 1, n, target[0] if target else "", m + 1
        m += 1
    return None


def _md_link_target(url: str) -> str:
    """
    The URL a link may open: http(s) and mailto only, else '' (shown as text).
    """
    url = url.strip().strip("<>")
    return url if url.lower().startswith(("http://", "https://", "mailto:")) else ""


class _Markdown:
    """
    Markdown -> HTML, with the cited spans of the answer shaded and the [n] markers placed after them.
    Citations hold offsets into the raw text, so every character keeps where it came from (`where`), and each
    line is a _Line: (where its text starts in the answer, its text).

    Models often answer in markdown (lists, **bold**, headings, tables, code). It's laid out with the stdlib, and
    every piece of text is escaped before it's wrapped: answers can quote untrusted knowledge base content, so raw
    HTML in them stays text, links go only to http(s) and mailto, and pictures become links (nothing loads from
    elsewhere).

    A marker goes after the cited span's last word, before its closing punctuation or markup. `no_closer` remembers,
    for each (delimiter, run, end), where a search for its closer started and found none, so it isn't searched
    again.
    """

    def __init__(self, text: str, citations: Iterable[Citation] = (), inline: bool = False):
        self.text, self.inline = text, inline
        self.cited = bytearray(len(text))
        marks: dict[int, list[int]] = {}
        for c in citations:
            start, end = max(0, c.start), min(len(text), c.end)
            if not c.sources or end <= start:
                continue
            self.cited[start:end] = b"\x01" * (end - start)
            if not inline:
                k = end
                while k > start + 1 and text[k - 1] in " \t\r\n.!?:;,*_~`":
                    k -= 1
                marks.setdefault(k - 1, []).extend(c.sources)
        self.marks = [(at, "".join(f"[{n}]" for n in dict.fromkeys(sources))) for at, sources in sorted(marks.items())]
        self.next_mark = 0
        self.no_closer: dict[tuple[str, int, int], int] = {}

    def html(self) -> str:
        lines, at = [], 0
        for line in self.text.split("\n"):
            lines.append((at, line[:-1] if line.endswith("\r") else line))
            at += len(line) + 1
        out = self.blocks(_md_blocks(lines))
        rest = self.marks_before(len(self.text) + 1)
        return out + (f"<p>{rest}</p>" if rest else "")


    def marks_before(self, offset: int) -> str:
        """
        Markers placed before this offset not shown yet (their character was markup): shown now.
        """
        out = []
        while self.next_mark < len(self.marks) and self.marks[self.next_mark][0] < offset:
            out.append(f"<sup>{self.marks[self.next_mark][1]}</sup>")
            self.next_mark += 1
        return "".join(out)

    def plain(self, text: str, code: bool) -> str:
        if not self.inline or code:
            return _esc(text)
        return "".join(f"<sup>[{_esc(piece)}]</sup>" if i % 2 else _esc(piece)
                       for i, piece in enumerate(_MARKER_RE.split(text)))

    def chars(self, s: str, where: list[int], i: int, j: int, code: bool = False) -> str:
        """
        s[i:j] escaped, cited runs in <span class="cite">, each marker after the character it follows. A run's HTML
        is gathered in `run` (shaded throughout, or not at all), with its characters not escaped yet in `text`.
        """
        out: list[str] = []
        run: list[str] = []
        text: list[str] = []
        shaded = [False]

        def add(markup: str = "") -> None:
            if text:
                run.append(self.plain("".join(text), code))
                text.clear()
            if markup:
                run.append(markup)

        def close() -> None:
            add()
            if run:
                out.append(f'<span class="cite">{"".join(run)}</span>' if shaded[0] else "".join(run))
                run.clear()

        for k in range(i, j):
            at = where[k]
            pending = self.marks_before(at)
            if pending:
                add(pending)
            cited = bool(at < len(self.cited) and self.cited[at])
            if cited != shaded[0]:
                close()
                shaded[0] = cited
            text.append(s[k])
            if self.next_mark < len(self.marks) and self.marks[self.next_mark][0] == at:
                add(f"<sup>{self.marks[self.next_mark][1]}</sup>")
                self.next_mark += 1
        close()
        return "".join(out)


    def inline_html(self, lines: list[_Line]) -> str:
        s, where = _md_source(lines)
        out = self.nodes(s, where, self.parse(s, 0, len(s)))
        return out + self.marks_before(where[-1] + 1 if where else 0)

    def blocks(self, blocks: list[tuple[Any, ...]], tight: bool = False) -> str:
        out = []
        for block in blocks:
            kind = block[0]
            if kind == "p":
                inner = self.inline_html(block[1])
                out.append(inner if tight else f"<p>{inner}</p>")
            elif kind == "h":
                out.append(f"<h{block[1]}>{self.inline_html(block[2])}</h{block[1]}>")
            elif kind == "hr":
                out.append("<hr>")
            elif kind == "code":
                s, where = _md_source(block[2])
                lang = f'<span class="lang">{_esc(block[1])}</span>' if block[1] else ""
                body = self.chars(s, where, 0, len(s), code=True) + self.marks_before(where[-1] + 1 if where else 0)
                copy = _copy_button(s, f"the {block[1]} code" if block[1] else "the code")
                out.append(f'<div class="pre">{lang}{copy}<pre{_SELECT}><code>{body}</code></pre></div>')
            elif kind == "quote":
                out.append(f"<blockquote>{self.blocks(block[1])}</blockquote>")
            elif kind == "list":
                _, ordered, start, items, loose = block
                tag = "ol" if ordered else "ul"
                first = f' start="{start}"' if ordered and start != 1 else ""
                inner = "".join(f"<li>{self.blocks(item, tight=not loose)}</li>" for item in items)
                out.append(f"<{tag}{first}>{inner}</{tag}>")
            elif kind == "table":
                _, aligns, header, rows = block

                def cell(tag: str, j: int, item: _Line) -> str:
                    align = f' class="{aligns[j]}"' if j < len(aligns) and aligns[j] else ""
                    return f"<{tag}{align}>{self.inline_html([item])}</{tag}>"

                head = "".join(cell("th", j, c) for j, c in enumerate(header))
                body = "".join("<tr>" + "".join(cell("td", j, c) for j, c in enumerate(row)) + "</tr>" for row in rows)
                out.append(f'<div class="mdt"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        return "".join(out)


    def parse(self, s: str, i: int, j: int, depth: int = 0) -> list[tuple[Any, ...]]:
        """
        Inline markdown in s[i:j] -> nodes: ('t', i, j) text, ('code', i, j), ('br',), (tag, children) for em /
        strong / del, and ('a', href, children). Anything that doesn't close is text.
        """
        nodes: list[tuple[Any, ...]] = []
        text_from, k = i, i
        while k < j:
            node, after = self._inline_at(s, k, j, depth)
            if node is None:
                k = after
                continue
            if k > text_from:
                nodes.append(("t", text_from, k))
            nodes.append(node)
            k = text_from = after
        if j > text_from:
            nodes.append(("t", text_from, j))
        return nodes

    def _inline_at(self, s: str, k: int, j: int, depth: int) -> tuple[tuple[Any, ...] | None, int]:
        """
        The inline node starting at s[k] and where it ends, or (None, where to look next) when there's none.
        """
        ch = s[k]
        if ch == "\\" and k + 1 < j and s[k + 1] in _MD_PUNCT:
            return ("t", k + 1, k + 2), k + 2
        if ch == "\n":
            return ("br",), k + 1
        if ch == "`":
            return self._code_span(s, k, j)
        if ch in "*_~" and depth < _MD_MAX_DEPTH:
            return self._emphasis_node(s, k, j, depth)
        if ch == "[" or (ch == "!" and s.startswith("[", k + 1)):
            return self._link_node(s, k, j, depth)
        if ch == "<":
            return self._autolink(s, k, j)
        if ch in "hH" and (k == 0 or not (s[k - 1].isalnum() or s[k - 1] in "/:@")):
            return self._bare_url(s, k, j)
        return None, k + 1

    def _code_span(self, s: str, k: int, j: int) -> tuple[tuple[Any, ...] | None, int]:
        """
        `code` opening at k, without the one space each side may pad it with; a run of backticks that doesn't
        close is text.
        """
        run = self.run(s, k, j, "`")
        close = self.code_close(s, k + run, j, run)
        if close < 0:
            return None, k + run
        a, b = k + run, close
        if b - a >= 2 and s[a] == " " and s[b - 1] == " " and s[a:b].strip():
            a, b = a + 1, b - 1
        return ("code", a, b), close + run

    def _emphasis_node(self, s: str, k: int, j: int, depth: int) -> tuple[tuple[Any, ...] | None, int]:
        emphasis = self.emphasis(s, k, j)
        if emphasis is None:
            return None, k + self.run(s, k, j, s[k])
        tag, a, b, after = emphasis
        return (tag, self.parse(s, a, b, depth + 1)), after

    def _link_node(self, s: str, k: int, j: int, depth: int) -> tuple[tuple[Any, ...] | None, int]:
        """
        [text](url), or ![picture](url), which becomes a link: nothing loads from elsewhere.
        """
        link = self.link(s, k + (s[k] == "!"), j)
        if link is None:
            return None, k + 1
        a, b, url, after = link
        children = self.parse(s, a, b, depth + 1) if b > a else [("t", k, after)]
        return ("a", _md_link_target(url), children), after

    @staticmethod
    def _autolink(s: str, k: int, j: int) -> tuple[tuple[Any, ...] | None, int]:
        auto = _MD_AUTOLINK_RE.match(s, k, j)
        if not auto:
            return None, k + 1
        return ("a", _md_link_target(auto.group(1)), [("t", k + 1, auto.end() - 1)]), auto.end()

    @staticmethod
    def _bare_url(s: str, k: int, j: int) -> tuple[tuple[Any, ...] | None, int]:
        """
        An http(s) address in the text, as a link: without the punctuation after it, or a closing bracket it
        doesn't open.
        """
        url = _MD_URL_RE.match(s, k, j)
        if not url:
            return None, k + 1
        end = url.end()
        while end > k and (s[end - 1] in ".,;:!?*_~" or (s[end - 1] == ")" and
                                                         s.count("(", k, end) < s.count(")", k, end))):
            end -= 1
        return ("a", _md_link_target(s[k:end]), [("t", k, end)]), end

    @staticmethod
    def run(s: str, k: int, j: int, ch: str) -> int:
        n = k
        while n < j and s[n] == ch:
            n += 1
        return n - k

    def code_close(self, s: str, k: int, j: int, run: int) -> int:
        """
        Where a code span of `run` backticks closes, or -1.
        """
        while True:
            k = s.find("`" * run, k, j)
            if k < 0:
                return -1
            length = self.run(s, k, j, "`")
            if length == run:
                return k
            k += length

    def emphasis(self, s: str, k: int, j: int) -> tuple[str, int, int, int] | None:
        """
        *em*, **strong**, ***both***, _em_, __strong__ or ~~del~~ opening at k: (tag, inner start, inner end,
        after it), or None. An opener needs a non-space after it, a closer a non-space before it, and _ doesn't work
        inside a word (snake_case_names stay as they are). A *** closes a shorter run with its last characters.
        """
        ch = s[k]
        run = self.run(s, k, j, ch)
        if run > 3 or (ch == "~" and run != 2):
            return None
        a = k + run
        if a >= j or s[a].isspace() or (ch == "_" and k > 0 and s[k - 1].isalnum()):
            return None
        key = (ch, run, j)
        if self.no_closer.get(key, j) <= a:
            return None
        n = a
        while n < j:
            c = s[n]
            if c == "\\":
                n += 2
                continue
            if c == "`":
                ticks = self.run(s, n, j, "`")
                close = self.code_close(s, n + ticks, j, ticks)
                n = close + ticks if close >= 0 else n + ticks
                continue
            if c == ch:
                length = self.run(s, n, j, ch)
                fits = not s[n - 1].isspace() and (ch != "_" or n + length >= len(s) or not s[n + length].isalnum())
                if fits and (length == run or (length == 3 and ch != "~")):
                    close = n + length - run if length == 3 else n
                    tag = "del" if ch == "~" else {1: "em", 2: "strong", 3: "strongem"}[run]
                    return tag, a, close, close + run
                n += length
                continue
            n += 1
        self.no_closer[key] = min(a, self.no_closer.get(key, a))
        return None

    @staticmethod
    def link(s: str, k: int, j: int) -> tuple[int, int, str, int] | None:
        """
        [text](url) opening at k: (text start, text end, url, after it), or None.
        """
        if s.find("](", k, j) < 0:
            return None
        n = _md_closing_bracket(s, k, j)
        if n >= j or not s.startswith("(", n + 1):
            return None
        return _md_link_target_after(s, k, n, j)

    def nodes(self, s: str, where: list[int], nodes: list[tuple[Any, ...]]) -> str:
        out = []
        for node in nodes:
            kind = node[0]
            if kind == "t":
                out.append(self.chars(s, where, node[1], node[2]))
            elif kind == "br":
                out.append("<br>")
            elif kind == "code":
                out.append(f"<code>{self.chars(s, where, node[1], node[2], code=True)}</code>")
            elif kind == "a":
                inner = self.nodes(s, where, node[2])
                out.append(f'<a href="{_esc(node[1])}" target="_blank" rel="noopener noreferrer">{inner}</a>'
                           if node[1] else inner)
            else:
                opening, closing = _MD_TAGS[kind]
                out.append(opening + self.nodes(s, where, node[1]) + closing)
        return "".join(out)


def _markdown_html(text: str, citations: Iterable[Citation] = (), inline: bool = False) -> str:
    """
    Markdown as HTML: paragraphs, headings, lists, quotes, code, tables, **bold**, *italic*, `code` and links.
    With citations, the cited spans are shaded and [n] follows each one; inline=True when the text already holds the
    [n] markers. A single line break stays a line break, so plain-text answers look as they were written.
    """
    return _Markdown(text, citations, inline).html()


def _answer_lines(text: str, width: int, indent: str = "") -> list[str]:
    """
    An answer as text lines: its markdown kept as written (it reads well as text), long lines wrapped, a list
    item's continuation lined up under its text, and code blocks and table rows never wrapped.
    """
    out: list[str] = []
    fence = ""
    for line in text.split("\n"):
        opening = _MD_FENCE_RE.match(line)
        if fence or opening or line.lstrip().startswith("|"):
            out.append((indent + line).rstrip())
            if fence and line.strip().startswith(fence) and not line.strip().strip(fence[0]):
                fence = ""
            elif not fence and opening:
                fence = opening.group(2)
            continue
        item = _MD_ITEM_RE.match(line)
        hang = " " * (len(item.group(0)) if item else _md_indent(line))
        out += textwrap.wrap(line, width, initial_indent=indent, subsequent_indent=indent + hang) or [""]
    return out
