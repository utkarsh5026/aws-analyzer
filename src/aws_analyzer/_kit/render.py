"""How reports are drawn, for every service: the stylesheet each report starts with, and the HTML and text of the
blocks every service uses (blocks.py); a service's own blocks register how they're drawn when it's imported.

Each service's reports sit in an HTML box of its own root class (.ddb, .kba, ...): `_base_css(root, extra)` writes
the shared rules for it and adds the rules for the service's own blocks, and the service's `_render_html(blocks,
max_rows)` is `_report_html` with its stylesheet, root class and badge. `_render_text` is the same for every service.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from .blocks import (
    _CELLS, _HTML, _TEXT, _Cards, _Findings, _Next, _Note, _Report, _Table, _Text, _Title, _Tone, _drawer, _draws,
)
from .fmt import _plural
from .text import _clip, _esc, _pad, _text_bar, _width

# ----------------------------------------------------------------------------- the stylesheet

# The rules every service's reports share. "{root}" stands for the service's root class (filled in by str.replace,
# not str.format: the rules are full of braces).
_BASE_RULES = """\
.{root}{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.{root} h3{margin:10px 0 2px;font-size:16px}
.{root} h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.{root} h4{margin:14px 0 4px;font-size:13px}
.{root} .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.{root} .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.{root} .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.{root} .card.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.{root} .card.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.{root} .card.ok{border-color:rgba(16,185,129,.7)}
.{root} .card .l{font-size:11px;opacity:.65}
.{root} .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.{root} .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.{root} .tw.scroll{max-height:640px;overflow:auto}
.{root} table.t{border-collapse:collapse;width:auto;font-size:inherit}
.{root} table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.{root} .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.{root} .tw.scroll table.t th{background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.{root} table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.{root} table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.{root} table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.{root} table.t td.s{white-space:nowrap}
.{root} table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.{root} table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.{root} table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.{root} .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.{root} .track{vertical-align:middle;margin-right:6px}
.{root} .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.{root} .pill{display:inline-block;padding:0 7px;border-radius:9px;font-weight:600;font-size:12px}
.{root} .pill.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.6)}
.{root} .pill.bad{background:rgba(239,68,68,.16);box-shadow:inset 0 0 0 1px rgba(239,68,68,.6)}
.{root} .pill.ok{background:rgba(16,185,129,.14);box-shadow:inset 0 0 0 1px rgba(16,185,129,.55)}
.{root} .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.{root} .note::before{content:"\\2139\\FE0E";margin-right:7px;opacity:.7}
.{root} .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.{root} .note.warn::before{content:"\\26A0\\FE0E"}
.{root} .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.{root} .note.ok::before{content:"\\2713"}
.{root} .fh{font-size:12px;font-weight:600;opacity:.75;margin:10px 0 2px}
.{root} code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:0 4px;border-radius:4px}
.{root} code{background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
.{root} .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.{root} pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.{root} pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.5;max-height:560px}
.{root} pre.code{user-select:all;-webkit-user-select:all;cursor:text}
.{root} .hint{font-weight:400;font-size:11px;opacity:.55;margin-left:8px}
.{root} details.sec{margin:14px 0 4px}
.{root} details.sec>summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.{root} .next{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:12px 0 4px;padding-top:8px}
.{root} .next{border-top:1px dashed rgba(127,127,127,.35)}
.{root} .next .nl{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.{root} .next .nw{font-size:12px;opacity:.65;margin-left:6px}"""


def _base_css(root: str, extra: str = "", *, theme: str = "", restyle: dict[str, str] | None = None,
              after: dict[str, str] | None = None) -> str:
    """The <style> every report starts with: the shared rules for the root class `root`, then `extra`, the rules
    for the service's own blocks.

    A service whose reports look different (the chat's match its window) changes some shared rules: `theme` goes
    first (the custom properties its rules use), `restyle` replaces a shared rule and `after` adds rules after one.
    Their keys are how the shared rule starts after `.<root> ` ("h3 .badge{", "code{background:"); one that matches
    no rule, or more than one, raises ValueError, so a change to the shared rules can't silently undo a restyle."""
    rules = _BASE_RULES.replace("{root}", root).split("\n")
    for edits, keep in ((restyle or {}, False), (after or {}, True)):
        for start, new in edits.items():
            found = [i for i, rule in enumerate(rules) if rule.startswith(f".{root} {start}")]
            if len(found) != 1:
                raise ValueError(f"{start!r} starts {len(found)} of the shared CSS rules, not one")
            rules[found[0]] = f"{rules[found[0]]}\n{new}" if keep else new
    return "\n".join(["<style>", *filter(None, [theme, "\n".join(rules), extra]), "</style>"])


# ----------------------------------------------------------------------------- sentences and calls

_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")  # right-aligned in tables
# A command in a sentence: a call (kb_info(), documents(status='FAILED'), .core.find(...), S3View().preview('s3://..'))
# or an AWS CLI command with its options (aws dynamodb update-table --table-name orders --deletion-protection-enabled).
_CALL_RE = re.compile(
    r"((?<![\w.])\.?(?:[A-Za-z_]\w*(?:\(\))?\.)*[A-Za-z_]\w*"
    r"\((?:[^()'\"]|'[^']*'|\"[^\"]*\"|\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))*\)"
    r"|\baws [a-z0-9-]+ [a-z0-9-]+(?: --[\w-]+(?: (?!--)[^\s,;]*[^\s,;.])?)*)"
)
_TONES = ("warn", "bad", "ok")
_MARKS = {"warn": "[!] ", "ok": "[ok] "}  # text-mode prefix of a note by level; anything else is "[i] "
_SELECT = ' title="Click to select, then copy"'


def _prose(value: Any) -> str:
    """Escaped HTML for a sentence this tool wrote, with the calls in it as code that one click selects.
    The text is split on the calls and every piece escaped before it's wrapped, so nothing in it becomes markup."""
    pieces = _CALL_RE.split("" if value is None else str(value))
    return "".join(f"<code{_SELECT}>{_esc(piece)}</code>" if i % 2 else _esc(piece) for i, piece in enumerate(pieces))


def _call(name: str, *args: Any, **kwargs: Any) -> str:
    """_call('tree', 's3://b/', depth=2) -> "tree('s3://b/', depth=2)": a next step, ready to copy."""

    def literal(value: Any) -> str:  # repr, but DynamoDB numbers read 42 rather than Decimal('42')
        if type(value).__name__ == "Decimal":
            return str(value)
        if isinstance(value, dict):
            return "{" + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items()) + "}"
        if isinstance(value, (list, tuple)):
            inner = ", ".join(map(literal, value)) + ("," if isinstance(value, tuple) and len(value) == 1 else "")
            return f"[{inner}]" if isinstance(value, list) else f"({inner})"
        return repr(value)

    return f"{name}({', '.join([literal(a) for a in args] + [f'{k}={literal(v)}' for k, v in kwargs.items()])})"


def _signature(function: Callable) -> str:
    """'(uri, *, top_n=10, limit=None)': a command's parameters without self or type hints."""
    sig = inspect.signature(function)
    params = [p.replace(annotation=inspect.Parameter.empty) for name, p in sig.parameters.items() if name != "self"]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


# ----------------------------------------------------------------------------- the shared blocks


def _tone(item: tuple[str, ...]) -> str:
    """The tone of a card: its third element, when it's one this renderer colours."""
    return item[2] if len(item) > 2 and item[2] in _TONES else ""


def _ordered(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Warnings first, then notes, each in the order they were found."""
    return sorted(findings, key=lambda f: {"warn": 0, "info": 1}.get(f[0], 2))


def _counts(findings: list[tuple[str, str]], sep: str) -> str:
    """'2 warnings · 3 notes'."""
    warns = sum(level == "warn" for level, _ in findings)
    return sep.join(filter(None, [_plural(warns, "warning") if warns else "",
                                  _plural(len(findings) - warns, "note") if len(findings) > warns else ""]))


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _hidden(count: int, table: _Table, default_max: int) -> str:
    """The line under a table that was cut short, and how to see the rest when the view's max_rows cut it."""
    text = f"... {count:,} more rows not shown"
    return text + (f" (the view shows {default_max:,}; ui.max_rows = 0 shows all)" if table.max_rows is None else "")


def _undrawn(block: Any) -> _Note:
    """The note shown in place of a block that nothing draws, rather than a traceback."""
    return _Note(f"Part of this report isn't shown: there's no way to draw a {type(block).__name__} here "
                 "(a bug in aws-analyzer).", "warn")


@_draws(_Title, "html")
def _title_html(block: _Title, report: _Report) -> str:
    sub = f'<div class="sub">{_prose(block.sub)}</div>' if block.sub else ""
    return f'<h3><span class="badge">{_esc(report.badge)}</span>{_esc(block.text)}</h3>{sub}'


@_draws(_Cards, "html")
def _cards_html(block: _Cards, report: _Report) -> str:
    cards = "".join(
        f'<div class="{" ".join(filter(None, ["card", _tone(item)]))}"><div class="l">'
        f'{_esc(item[0])}</div><div class="v">{_esc(item[1])}</div></div>'
        for item in block.items
    )
    return f'<div class="cards">{cards}</div>'


@_draws(_Note, "html")
def _note_html(block: _Note, report: _Report) -> str:
    return f'<div class="note {block.level}">{_prose(block.text)}</div>'


@_draws(_Findings, "html")
def _findings_html(block: _Findings, report: _Report) -> str:
    """The findings panel: warnings first, with how many there are of each; block.empty (an ok note) when there's
    none."""
    items = _ordered(block.items)
    if items:
        notes = "".join(f'<div class="note {level}">{_prose(message)}</div>' for level, message in items)
        head = f'<div class="fh">Findings · {_esc(_counts(items, " · "))}</div>'
        return f'<div class="fd">{head}{notes}</div>'
    return f'<div class="note ok">{_prose(block.empty)}</div>' if block.empty else ""


@_draws(_Next, "html")
def _next_html(block: _Next, report: _Report) -> str:
    if not block.items:
        return ""
    calls = "".join(
        f'<span class="ni"><code{_SELECT}>{_esc(call)}</code>'
        + (f'<span class="nw">{_esc(why)}</span>' if why else "")
        + "</span>"
        for call, why in block.items
    )
    return f'<div class="next"><span class="nl">{_esc(block.title)}</span>{calls}</div>'


@_draws(_Tone, "cell")
def _tone_cell_html(cell: _Tone, text: str) -> str:
    return f'<span class="pill {cell.tone}">{_esc(text)}</span>' if cell.tone in _TONES and text else _esc(text)


def _cell_html(cell: Any, text: str) -> str:
    """A table cell's HTML: its text, escaped, or what's registered for its type (a _Tone's coloured pill)."""
    draw = _drawer(_CELLS, cell)
    return draw(cell, text) if draw else _esc(text)


@_draws(_Table, "html")
def _table_html(block: _Table, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    if not block.rows:
        return f'{title}<div class="more">(none)</div>'
    rows, hidden = _visible_rows(block, report.max_rows)
    head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
    head += f"<th>{_esc(block.bar_label)}</th>" if block.bars is not None else ""
    body = []
    for i, row in enumerate(rows):
        cells = []
        for j, cell in enumerate(row):
            text = "" if cell is None else str(cell)
            inner = _cell_html(cell, text)
            if block.tree and j == 0:
                css = "tree"
            elif j in block.code_cols and text:
                css, inner = "c", f"<code{_SELECT}>{inner}</code>"
            elif j in block.prose_cols:
                css, inner = "", _prose(text)
            elif _NUMERIC_RE.match(text):
                css = "n"
            else:
                css = "s" if len(text) <= 16 and "\n" not in text else ""
            cells.append(f'<td class="{css}">{inner}</td>' if css else f"<td>{inner}</td>")
        if block.bars is not None:
            pct = max(0.0, min(1.0, block.bars[i])) * 100
            cells.append(
                f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                f"</span></span>{pct:.1f}%</td>"
            )
        body.append(f"<tr>{''.join(cells)}</tr>")
    table = (
        f'<div class="tw{" scroll" if len(rows) > 30 else ""}"><table class="t"><thead><tr>{head}</tr>'
        f"</thead><tbody>{''.join(body)}</tbody></table></div>"
    )
    if hidden:
        table += f'<div class="more">{_esc(_hidden(hidden, block, report.max_rows))}</div>'
    if block.collapsed:
        return (
            f'<details class="sec"><summary>{_prose(block.title or "Details")} '
            f"({len(block.rows):,})</summary>{table}</details>"
        )
    return title + table


@_draws(_Text, "html")
def _text_html(block: _Text, report: _Report) -> str:
    css = " ".join(filter(None, ["wrap" if block.wrap else "", "code" if block.code else ""]))
    pre = (
        f'<pre class="{css}"{_SELECT if block.code else ""}>{_esc(block.text)}</pre>'
        if css
        else f"<pre>{_esc(block.text)}</pre>"
    )
    if block.collapsed:
        return f'<details class="sec"><summary>{_prose(block.title or "Details")}</summary>{pre}</details>'
    hint = '<span class="hint">click it to select all, then copy</span>' if block.code else ""
    return (f"<h4>{_prose(block.title)}{hint}</h4>" if block.title or hint else "") + pre


@_draws(_Title, "text")
def _title_lines(block: _Title, report: _Report) -> list[str]:
    return ["", block.text, "=" * min(len(block.text), 100)] + ([block.sub] if block.sub else [])


@_draws(_Cards, "text")
def _cards_lines(block: _Cards, report: _Report) -> list[str]:
    out, line = [], ""
    for entry in block.items:
        item = f"{entry[0]}: {entry[1]}" + (" (!)" if _tone(entry) in ("warn", "bad") else "")
        if line and len(line) + len(item) > 100:
            out.append(line)
            line = ""
        line += ("   " if line else "") + item
    return out + [line]


@_draws(_Note, "text")
def _note_lines(block: _Note, report: _Report) -> list[str]:
    return [_MARKS.get(block.level, "[i] ") + block.text]


@_draws(_Findings, "text")
def _findings_lines(block: _Findings, report: _Report) -> list[str]:
    items = _ordered(block.items)
    if items:
        return ["", f"-- Findings: {_counts(items, ', ')} --"] + [
            _MARKS.get(level, "[i] ") + message for level, message in items
        ]
    return ["[ok] " + block.empty] if block.empty else []


@_draws(_Next, "text")
def _next_lines(block: _Next, report: _Report) -> list[str]:
    if not block.items:
        return []
    width = max(len(call) for call, _ in block.items)
    return ["", f"{block.title}:"] + [f"  {call.ljust(width)}   {why}".rstrip() for call, why in block.items]


@_draws(_Table, "text")
def _table_lines(block: _Table, report: _Report) -> list[str]:
    out = [""] + ([f"-- {block.title} --"] if block.title else [])
    if not block.rows:
        return out + ["(none)"]
    rows, hidden = _visible_rows(block, report.max_rows)
    headers = list(block.headers) + ([block.bar_label] if block.bars is not None else [])
    cells = [
        [_clip(("" if c is None else str(c)).replace("\n", ", ")) for c in row]
        + ([_text_bar(block.bars[i])] if block.bars is not None else [])
        for i, row in enumerate(rows)
    ]
    widths = [max([_width(h)] + [_width(r[j]) for r in cells]) for j, h in enumerate(headers)]

    def line_of(values: list[str]) -> str:
        return "  ".join(_pad(v, w, right=bool(_NUMERIC_RE.match(v))) for v, w in zip(values, widths)).rstrip()

    out += [line_of(headers), "  ".join("-" * w for w in widths)] + [line_of(r) for r in cells]
    return out + ([_hidden(hidden, block, report.max_rows)] if hidden else [])


@_draws(_Text, "text")
def _text_lines(block: _Text, report: _Report) -> list[str]:
    return (["", f"-- {block.title} --"] if block.title else []) + [block.text]


# ----------------------------------------------------------------------------- whole reports


def _block_html(block: Any, report: _Report) -> str:
    """One block as HTML, drawn by what's registered for its type."""
    draw = _drawer(_HTML, block)
    return draw(block, report) if draw else _note_html(_undrawn(block), report)


def _blocks_html(blocks: list[Any], max_rows: int = 0) -> str:
    """Blocks as HTML without a report's stylesheet and box, for a window that has both (a title gets no badge)."""
    report = _Report(max_rows)
    return "".join(_block_html(block, report) for block in blocks)


def _report_html(blocks: list[Any], max_rows: int, css: str, root: str, badge: str) -> str:
    """A report as HTML: the service's stylesheet, then its blocks in a box of the service's root class."""
    report = _Report(max_rows, badge)
    return "".join([css, f'<div class="{root}">', *(_block_html(block, report) for block in blocks), "</div>"])


def _block_lines(block: Any, report: _Report) -> list[str]:
    """One block as lines of text, drawn by what's registered for its type."""
    draw = _drawer(_TEXT, block)
    return draw(block, report) if draw else _note_lines(_undrawn(block), report)


def _render_text(blocks: list[Any], max_rows: int) -> str:
    """A report as text: each block's lines, one after the other."""
    report = _Report(max_rows)
    return "\n".join(line for block in blocks for line in _block_lines(block, report))
