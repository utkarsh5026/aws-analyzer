"""
Source code as HTML, every piece escaped: JSON and Python with their tokens in colour (jk / js / jn / jl for JSON's
keys, text, numbers and literals; pk / pf / pa / pc for Python's keywords, calls, keyword arguments and comments).
The chat's code to copy and the Lambda explorer's Code tab draw with them; each service's stylesheet colours them.
"""

from __future__ import annotations

import io
import keyword
import re
import tokenize

from .text import _esc

_JSON_TOKEN_RE = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b')


def _json_source_html(text: str) -> str:
    """
    JSON text with its keys, text, numbers and true / false / null in their own colours (escaped).
    """
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
_LAYOUT_TOKENS = (tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT)
_NOT_CODE_TOKENS = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT)


def _python_css(token: tokenize.TokenInfo, previous: str, following: str) -> str:
    """
    The colour class of one Python token, given the code tokens just before and after it ('' when there's none):
    a string followed by ':' is a dict key, a name followed by '(' a call, and a name followed by '=' inside a
    call's brackets a keyword argument.
    """
    if token.type == tokenize.COMMENT:
        return "pc"
    if token.type == tokenize.STRING:
        return "jk" if following == ":" else "js"
    if token.type == tokenize.NUMBER:
        return "jn"
    if token.type != tokenize.NAME:
        return ""
    if token.string in _PY_LITERALS:
        return "jl"
    if keyword.iskeyword(token.string):
        return "pk"
    if following == "(":
        return "pf"
    if following == "=" and previous in ("(", ","):
        return "pa"
    return ""


def _source_offset(line_starts: list[int], position: tuple[int, int], code: str) -> int:
    """
    Where a token's (line, column) position is in the code, counted in characters.
    """
    line, column = position
    return line_starts[line - 1] + column if line - 1 < len(line_starts) else len(code)


def _python_html(code: str) -> str:
    """
    Python as HTML: keywords, text, numbers, True / False / None, calls, keyword arguments, dict keys and
    comments in their own colours. Every piece is escaped; code that doesn't tokenize is shown plain.
    """
    try:
        tokens = [token for token in tokenize.generate_tokens(io.StringIO(code).readline)
                  if token.type not in _LAYOUT_TOKENS]
    except (tokenize.TokenError, SyntaxError):
        return _esc(code)

    line_starts = [0]
    for line in code.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    code_tokens = [token for token in tokens if token.type not in _NOT_CODE_TOKENS]
    following = {id(token): code_tokens[k + 1].string if k + 1 < len(code_tokens) else ""
                 for k, token in enumerate(code_tokens)}
    previous = {id(token): code_tokens[k - 1].string if k else "" for k, token in enumerate(code_tokens)}

    out, last = [], 0
    for token in tokens:
        start = _source_offset(line_starts, token.start, code)
        end = _source_offset(line_starts, token.end, code)
        if start < last or end <= start:
            continue
        css = _python_css(token, previous.get(id(token), ""), following.get(id(token), ""))
        piece = _esc(code[start:end])
        out.append(_esc(code[last:start]))
        out.append(f'<span class="{css}">{piece}</span>' if css else piece)
        last = end
    return "".join(out) + _esc(code[last:])
