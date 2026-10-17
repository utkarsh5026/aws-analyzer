"""
JSON as a tree you can fold, coloured like code: the _JsonTree block, which S3's preview of a .json file and
Lambda's configuration draw with. In HTML each object and array is a <details>, so a click folds or unfolds it with no
script (it still works in a saved notebook); in text it's indented JSON, all of it unfolded.

A service that shows one adds `_json_tree_css(_ROOT)` to the rules it passes to `_base_css`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

from .blocks import _Report, _draws
from .fmt import _plural
from .render import _prose
from .text import _esc

_JSON_KEYS = 100  # keys of one object a JSON tree shows (more when n is bigger)
_JSON_ROWS = 2_000  # lines one JSON tree shows at most, so the notebook stays small
_JSON_OPEN = 80  # lines a JSON tree shows unfolded at first: the shallow levels open first, big parts stay folded
_JSON_DEPTH = 100  # objects and arrays nested deeper than this stay folded, with nothing inside
_JSON_INLINE = 80  # an array of plain values this short shows on one line: [3, 224, 224]
_JSON_STRING = 120  # a longer string folds to its start; a click shows all of it
_JSON_FULL = 20_000  # characters of one string shown at most


@dataclass
class _JNode:
    """
    One line of a _JsonTree: a value, or an object / array whose children are the lines under it.
    """

    path: str  # how Python reaches it in the loaded file: data['Records'][0]
    key: str | None = None  # its key in an object, as JSON ('"name"')
    index: int | None = None  # its place in an array
    kind: str = "value"  # 'value' | 'object' | 'array' | 'string' (a long one, folded to its start)
    tokens: list[tuple[str, str]] = field(default_factory=list)  # a value as (css class, text) pieces
    children: list[_JNode] = field(default_factory=list)
    total: int = 0  # an object's keys, an array's items, a long string's characters
    more: int = 0  # children not shown
    cut: bool = False  # the file goes on past it: it was cut off at the end of the preview
    parsed: bool = False  # a string holding JSON, shown as the JSON it holds
    packed: bool = False  # an array of plain values (an embedding, labels): its items wrap like words
    text: str = ""  # a long string's text, up to _JSON_FULL characters
    open: bool = False  # starts unfolded


def _json_tokens(value: Any) -> list[tuple[str, str]]:
    """
    A plain value (or an empty object or array) as coloured pieces of JSON.
    """
    if isinstance(value, str):
        return [("js", json.dumps(value, ensure_ascii=False))]
    if isinstance(value, bool) or value is None:
        return [("jb", json.dumps(value))]
    if isinstance(value, (int, float)):
        return [("jd", json.dumps(value))]
    if isinstance(value, (dict, list)):
        return [("jp", "{}" if isinstance(value, dict) else "[]")]
    return [("js", json.dumps(str(value), ensure_ascii=False))]  # not from a JSON file (a date): its text


def _json_inline(values: list) -> list[tuple[str, str]] | None:
    """
    A short array of plain values as one line ([3, 224, 224]); None when it's long or holds objects.
    """
    if len(values) > _JSON_INLINE // 3 or any(isinstance(v, (dict, list)) and v for v in values):
        return None
    pieces = [("jp", "[")]
    for i, value in enumerate(values):
        pieces += ([("jp", ", ")] if i else []) + _json_tokens(value)
    pieces.append(("jp", "]"))
    return pieces if sum(len(text) for _, text in pieces) <= _JSON_INLINE else None


def _json_inside(text: str) -> Any:
    """
    The JSON object or array a string holds (an SNS message, an SQS body, a Step Functions input), else None.
    """
    text = text.strip()
    if len(text) < 3 or text[0] + text[-1] not in ("{}", "[]"):
        return None
    try:
        return json.loads(text) or None
    except (ValueError, RecursionError):
        return None


def _json_more(node: _JNode) -> str:
    """
    '… 980 more items', or '… 980+ more items' in an array that was cut off (the file has more).
    """
    more = _plural(node.more, "more key" if node.kind == "object" else "more item")
    return "… " + (more.replace(" ", "+ ", 1) if node.cut else more)


class _JsonTree:
    """
    A render block: a JSON value as a tree you can fold, coloured like code. It holds the value's lines, each
    array capped at `items`, each object at _JSON_KEYS (or `items` when that's bigger) and the whole tree at
    _JSON_ROWS. A string that holds a JSON object or array shows
    as the JSON it holds. `cut` is how many of the objects and arrays along the end were cut off (the start of a
    file too big to read whole). The shallow levels start unfolded, as many as fit in _JSON_OPEN lines.

    `name` is what the value is called in Python, where each line's path (its tooltip) starts: S3's preview loads a
    file as `data`, Lambda's configuration is a Function's `raw`. `collapsed=True` folds the whole tree under its
    title, a secondary view like a collapsed _Text.
    """

    def __init__(self, value: Any, items: int = 20, cut: int = 0, title: str = "", *, name: str = "data",
                 collapsed: bool = False):
        self.items, self.rows, self.cut, self.title = max(items, 1), 0, cut > 0, title
        self.collapsed = collapsed
        self.capped: set[str] = set()  # 'item' / 'key': an array / object had more than it shows
        self.full = False  # the tree stopped at _JSON_ROWS lines
        self.root = self._node(value, name, cut, 0)
        self._unfold()
        # an object or array starts folded: unfold("all") would show more (the S3 explorer's "Expand all")
        self.folded = any(not node.open for node in self._nodes() if node.kind in ("object", "array"))

    def _node(self, value: Any, path: str, cut: int, depth: int, key: str | None = None,
              index: int | None = None) -> _JNode:
        self.rows += 1
        node = _JNode(path, key, index)
        if isinstance(value, str):
            inner = _json_inside(value)
            if inner is None:
                return self._string(node, value)
            value, node.path, node.parsed = inner, f"json.loads({path})", True
        if not isinstance(value, (dict, list)) or not value:
            node.tokens = _json_tokens(value)
            return node
        line = _json_inline(value) if isinstance(value, list) and not cut else None
        if line:
            node.tokens = line
            return node
        node.kind, node.total, node.cut = "object" if isinstance(value, dict) else "array", len(value), cut > 0
        most = max(self.items, _JSON_KEYS) if node.kind == "object" else self.items
        entries = value.items() if isinstance(value, dict) else enumerate(value)
        for i, (name, child) in enumerate(entries):
            if i >= most or self.rows >= _JSON_ROWS or depth >= _JSON_DEPTH:
                node.more = len(value) - i
                self.full = self.full or self.rows >= _JSON_ROWS
                if i >= most:
                    self.capped.add("key" if node.kind == "object" else "item")
                break
            inner_cut = cut - 1 if cut and i == len(value) - 1 else 0
            if node.kind == "object":
                node.children.append(self._node(child, f"{node.path}[{name!r}]", inner_cut, depth + 1,
                                                key=json.dumps(name, ensure_ascii=False)))
            else:
                node.children.append(self._node(child, f"{node.path}[{i}]", inner_cut, depth + 1, index=i))
        node.packed = node.kind == "array" and all(c.kind == "value" and not c.parsed for c in node.children)
        return node

    def _string(self, node: _JNode, value: str) -> _JNode:
        """
        A string; a long one folds to its start, and unfolds to its text as it reads (line breaks, not \\n).
        """
        if len(value) <= _JSON_STRING:
            node.tokens = _json_tokens(value)
            return node
        node.kind, node.total, node.text = "string", len(value), value[:_JSON_FULL]
        node.tokens = [("js", json.dumps(value[:80].rstrip(), ensure_ascii=False)[:-1] + '…"')]  # its start
        return node

    def _nodes(self) -> Iterator[_JNode]:
        nodes = [self.root]
        while nodes:
            node = nodes.pop()
            yield node
            nodes += node.children

    def unfold(self, how: str = "all") -> None:
        """
        Unfold every object and array ('all'), or fold the tree back to how it starts ('start'). Long strings
        stay folded to their start either way: a click shows one.
        """
        for node in self._nodes():
            node.open = how == "all" and node.kind in ("object", "array")
        if how != "all":
            self._unfold()

    def _unfold(self) -> None:
        """
        Unfolds the shallow levels first: each object or array whose lines still fit in _JSON_OPEN. In an array
        of more than three, only the first item can unfold, as a sample: the rest stay one line each.
        """
        shown, level = 1, [self.root]
        while level:
            below: list[_JNode] = []
            for node in level:
                if node.kind not in ("object", "array"):
                    continue
                rows = (len(node.children) // 8 if node.packed else len(node.children)) + 1 + (node.more > 0)
                if node is not self.root and shown + rows > _JSON_OPEN:
                    continue
                node.open, shown = True, shown + rows
                below += node.children[:1] if node.kind == "array" and node.total > 3 else node.children
            level = below


_JSON_END = "… the file goes on past what the preview read"


def _json_tree_html(tree: _JsonTree) -> str:
    """
    A _JsonTree as nested <details>: a click on an object's or array's line folds or unfolds it. The browser
    does that itself, so it needs no script and still works in a saved notebook. Counts and array indexes are CSS
    content (attr()), so a copied selection is plain JSON; each line's tooltip is its path in Python.
    """
    out: list[str] = []

    def walk(node: _JNode, last: bool) -> None:
        comma = "" if last else '<span class="jp">,</span>'
        tip = f' title="{_esc(node.path)}"'
        lead = (f'<span class="jk">{_esc(node.key)}</span><span class="jp">: </span>' if node.key is not None
                else f'<span class="ji" data-i="{node.index}"></span>' if node.index is not None else "")
        lead += '<span class="jx">JSON in a string</span>' if node.parsed else ""
        value = "".join(f'<span class="{css}">{_esc(text)}</span>' for css, text in node.tokens)
        opened = " open" if node.open else ""
        if node.kind == "value":
            out.append(f'<div class="jr"{tip}>{lead}{value}{comma}</div>')
        elif node.kind == "string":
            clipped = "…" if node.total > len(node.text) else ""
            out.append(f'<details class="jo"{opened}><summary{tip}>{lead}<span class="jf">{value}{comma}</span>'
                       f'<span class="jz" data-n="{_plural(node.total, "character")}"></span></summary>'
                       f'<div class="jv">{_esc(node.text)}{clipped}</div></details>')
        else:
            start, end = "{}" if node.kind == "object" else "[]"
            count = _plural(node.total, "key" if node.kind == "object" else "item")
            count = count.replace(" ", "+ ", 1) if node.cut else count
            out.append(f'<details class="jo"{opened}><summary{tip}>{lead}<span class="jp">{start}</span>'
                       f'<span class="jf"><span class="jp">…{end}</span>{comma}</span>'
                       f'<span class="jz" data-n="{count}"></span></summary><div class="jc">')
            if node.packed:
                out.append('<div class="jw">' + "".join(
                    f'<span title="{_esc(child.path)}">'
                    + "".join(f'<span class="{css}">{_esc(text)}</span>' for css, text in child.tokens)
                    + ("" if i == len(node.children) - 1 and not node.more else '<span class="jp">, </span>')
                    + "</span>" for i, child in enumerate(node.children)) + "</div>")
            else:
                for i, child in enumerate(node.children):
                    walk(child, i == len(node.children) - 1 and not node.more)
            if node.more:
                out.append(f'<div class="jm">{_json_more(node)}</div>')
            out.append("</div>" + ("" if node.cut else f'<div class="jr"><span class="jp">{end}</span>{comma}</div>')
                       + "</details>")

    walk(tree.root, True)
    if tree.cut:
        out.append(f'<div class="jm">{_esc(_JSON_END)}</div>')
    return f'<div class="jt">{"".join(out)}</div>'


def _json_tree_text(tree: _JsonTree) -> str:
    """
    A _JsonTree as indented JSON, all of it unfolded.
    """
    lines: list[str] = []

    def walk(node: _JNode, depth: int, last: bool) -> None:
        pad, comma = "  " * depth, "" if last else ","
        lead = (f"{node.key}: " if node.key is not None else "") + ("(JSON in a string) " if node.parsed else "")
        value = "".join(text for _, text in node.tokens)
        if node.kind == "value":
            lines.append(f"{pad}{lead}{value}{comma}")
        elif node.kind == "string":
            lines.append(f"{pad}{lead}{value}{comma}  ({_plural(node.total, 'character')})")
        else:
            start, end = "{}" if node.kind == "object" else "[]"
            lines.append(f"{pad}{lead}{start}")
            if node.packed:  # items wrap like words, at 100 characters
                line = ""
                for i, child in enumerate(node.children):
                    item = "".join(text for _, text in child.tokens)
                    item += "" if i == len(node.children) - 1 and not node.more else ","
                    if line and len(pad) + 2 + len(line) + 1 + len(item) > 100:
                        lines.append(f"{pad}  {line}")
                        line = ""
                    line += (" " if line else "") + item
                lines.append(f"{pad}  {line}")
            for i, child in enumerate([] if node.packed else node.children):
                walk(child, depth + 1, i == len(node.children) - 1 and not node.more)
            if node.more:
                lines.append(f"{pad}  {_json_more(node)}")
            if not node.cut:
                lines.append(f"{pad}{end}{comma}")

    walk(tree.root, 0, True)
    return "\n".join(lines + ([_JSON_END] if tree.cut else []))


@_draws(_JsonTree, "html")
def _json_tree_block_html(block: _JsonTree, report: _Report) -> str:
    if block.collapsed:
        title = _prose(block.title or "JSON")
        return f'<details class="sec"><summary>{title}</summary>{_json_tree_html(block)}</details>'
    hint = '<span class="hint">click a line to fold or unfold it, hover over one for its path in Python</span>'
    return f"<h4>{_prose(block.title)}{hint}</h4>{_json_tree_html(block)}"


@_draws(_JsonTree, "text")
def _json_tree_lines(block: _JsonTree, report: _Report) -> list[str]:
    return (["", f"-- {block.title} --"] if block.title else []) + [_json_tree_text(block)]


def _json_tree_css(root: str) -> str:
    """
    The rules a _JsonTree is drawn with, for the service whose reports' root class is `root`.
    """
    return _JSON_TREE_CSS.replace("{root}", root)


_JSON_TREE_CSS = """\
.{root} .jt{max-height:640px;overflow:auto;margin:2px 0 8px;padding:6px 10px 6px 24px;border:1px solid rgba(127,127,127,.3)}
.{root} .jt{border-radius:6px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.6}
.{root} .jt .jr,.{root} .jt summary{white-space:pre-wrap;overflow-wrap:anywhere;border-radius:3px}
.{root} .jt .jr:hover,.{root} .jt summary:hover{background:rgba(127,127,127,.1)}
.{root} .jt summary{display:block;position:relative;list-style:none;cursor:pointer}
.{root} .jt summary::-webkit-details-marker{display:none}
.{root} .jt summary::before{content:"\\25B8";position:absolute;left:-16px;width:14px;text-align:center;opacity:.45}
.{root} .jt details[open]>summary::before{content:"\\25BE"}
.{root} .jt summary:hover::before{opacity:1;color:#3b82f6}
.{root} .jt details[open]>summary>.jf{display:none}
.{root} .jt .jc{margin-left:4px;padding-left:16px;border-left:1px solid rgba(127,127,127,.2)}
.{root} .jt .jc:hover{border-left-color:rgba(127,127,127,.45)}
.{root} .jt .jk{color:var(--jp-mirror-editor-property-color,var(--vscode-debugTokenExpression-name,light-dark(#0451a5,#9cdcfe)))}
.{root} .jt .js{color:var(--jp-mirror-editor-string-color,var(--vscode-debugTokenExpression-string,light-dark(#a31515,#ce9178)))}
.{root} .jt .jd{color:var(--jp-mirror-editor-number-color,var(--vscode-debugTokenExpression-number,light-dark(#098658,#b5cea8)))}
.{root} .jt .jb{color:var(--jp-mirror-editor-atom-color,var(--vscode-debugTokenExpression-boolean,light-dark(#0000ff,#569cd6)))}
.{root} .jt .jp{opacity:.65}
.{root} .jt .jz::before,.{root} .jt .ji::before,.{root} .jt .jm,.{root} .jt .jx{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:11px}
.{root} .jt .jz::before{content:attr(data-n);margin-left:10px;opacity:.5;white-space:nowrap}
.{root} .jt .ji::before{content:attr(data-i);display:inline-block;min-width:22px;margin-right:6px;opacity:.45}
.{root} .jt .jm{opacity:.6;font-style:italic;padding:1px 0}
.{root} .jt .jw{white-space:pre-wrap;max-width:110ch}
.{root} .jt .jw>span:hover{background:rgba(127,127,127,.15);border-radius:3px}
.{root} .jt .jx{display:inline-block;margin-right:6px;padding:0 6px;border-radius:8px;line-height:16px;vertical-align:1px}
.{root} .jt .jx{background:rgba(59,130,246,.12);box-shadow:inset 0 0 0 1px rgba(59,130,246,.4)}
.{root} .jt .jv{margin:2px 0 6px 20px;padding:6px 10px;border-left:2px solid rgba(127,127,127,.35);white-space:pre-wrap;overflow-wrap:anywhere}
.{root} .jt .jv{max-height:320px;overflow:auto;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px}"""
