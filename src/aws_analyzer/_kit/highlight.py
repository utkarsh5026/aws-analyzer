"""Source code as HTML, every piece escaped: JSON and Python with their tokens in colour (jk / js / jn / jl for JSON's
keys, text, numbers and literals; pk / pf / pa / pc for Python's keywords, calls, keyword arguments and comments).
The chat's code to copy and the Lambda explorer's Code tab draw with them; each service's stylesheet colours them."""

from __future__ import annotations

import io
import keyword
import re
import tokenize

from .text import _esc

_JSON_TOKEN_RE = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b')


def _json_source_html(text: str) -> str:
    """JSON text with its keys, text, numbers and true / false / null in their own colours (escaped)."""
    out, last = [], 0
    for m in _JSON_TOKEN_RE.finditer(text):
        out.append(_esc(text[last:m.start()]))
        if m.group(1):
            css = "jk" if m.group(2) else "js"
            out.append(f'<span class="{css}">{_esc(m.group(1))}</span>{_esc(m.group(2) or "")}')
        else:
            out.append(f'<span class="{"jn" if m.group(3) else "jl"}">{_esc(m.group(0))}</span>')
        last = m.end()
    return "".join(out) + _esc(text[last:])


_PY_LITERALS = frozenset({"True", "False", "None"})


def _python_html(code: str) -> str:
    """Python as HTML: keywords, text, numbers, True / False / None, calls, keyword arguments, dict keys and
    comments in their own colours. Every piece is escaped; code that doesn't tokenize is shown plain."""
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(code).readline)
                  if t.type not in (tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, SyntaxError):
        return _esc(code)
    starts = [0]
    for line in code.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    real = [t for t in tokens if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT)]
    after = {id(t): real[k + 1].string if k + 1 < len(real) else "" for k, t in enumerate(real)}
    before = {id(t): real[k - 1].string if k else "" for k, t in enumerate(real)}
    out, last = [], 0
    for t in tokens:
        a = starts[t.start[0] - 1] + t.start[1] if t.start[0] - 1 < len(starts) else len(code)
        b = starts[t.end[0] - 1] + t.end[1] if t.end[0] - 1 < len(starts) else len(code)
        if a < last or b <= a:
            continue
        css = ""
        if t.type == tokenize.COMMENT:
            css = "pc"
        elif t.type == tokenize.STRING:
            css = "jk" if after.get(id(t)) == ":" else "js"
        elif t.type == tokenize.NUMBER:
            css = "jn"
        elif t.type == tokenize.NAME:
            if t.string in _PY_LITERALS:
                css = "jl"
            elif keyword.iskeyword(t.string):
                css = "pk"
            elif after.get(id(t)) == "(":
                css = "pf"
            elif after.get(id(t)) == "=" and before.get(id(t)) in ("(", ","):
                css = "pa"
        out.append(_esc(code[last:a]))
        out.append(f'<span class="{css}">{_esc(code[a:b])}</span>' if css else _esc(code[a:b]))
        last = b
    return "".join(out) + _esc(code[last:])
