"""How reports are drawn, for every service: the stylesheet each report starts with, and the HTML and text of the
blocks every service uses (blocks.py); a service's own blocks register how they're drawn when it's imported.

Each service's reports sit in an HTML box of its own root class (.s3a, .ddb, .kba, ...): `_base_css(root, extra)`
writes the shared rules for it and adds the rules for the service's own blocks, and the service's
`_render_html(blocks, max_rows)` is `_report_html` with its stylesheet, root class and badge. `_render_text` is the
same for every service.

Tables sort by a click on a header, filter a column of a few repeated values and choose their columns, and a finding
reads as a headline over its detail. All of it is plain HTML and CSS (radio buttons, selects and checkboxes in a form,
read by :has() rules), never JavaScript, so it still works in a reopened notebook, where scripts don't run.
"""

from __future__ import annotations

import inspect
import re
from collections import Counter
from typing import Any, Callable

from .blocks import (
    _CELLS, _HTML, _TEXT, _Cards, _Findings, _Next, _Note, _Report, _Table, _Text, _Title, _Tone, _drawer, _draws,
)
from .fmt import _plural, file_extension
from .text import _clip, _esc, _pad, _text_bar, _width

# ----------------------------------------------------------------------------- the stylesheet

_SORT_COLUMNS = 12  # columns a header click can sort by: each needs two CSS rules
# The rules every service's reports share. "{root}" stands for the service's root class (filled in by str.replace,
# not str.format: the rules are full of braces).
_BASE_RULES = """\
.{root}{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.{root} h3{margin:10px 0 2px;font-size:16px}
.{root} h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.{root} h4{margin:14px 0 4px;font-size:13px;font-weight:600}
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
.{root} table.t{border-collapse:collapse;width:auto;font-size:inherit;margin:0;table-layout:auto}
.{root} table.t tr{padding:0;border:0}
.{root} table.t tbody tr,.{root} table.t tbody tr:hover{background:transparent}
.{root} table.t th{text-align:left;font-weight:600;padding:5px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.{root} table.t th{background-image:linear-gradient(rgba(127,127,127,.08),rgba(127,127,127,.08))}
.{root} table.t th.n{text-align:right}
.{root} .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.{root} .tw.scroll table.t th{background-color:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.{root} table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.{root} table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.{root} table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.{root} table.t td.s{white-space:nowrap}
.{root} table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.{root} table.t td.p{white-space:normal}
.{root} td.p .pl{display:block;white-space:nowrap}
.{root} td.p .pd{display:inline-block;max-width:30em;overflow:hidden;text-overflow:ellipsis;direction:rtl;vertical-align:top;opacity:.6}
.{root} td.p .pd::before,.{root} td.p .pd::after{content:"\\200E"}
.{root} td.p .pn{white-space:normal;overflow-wrap:anywhere}
.{root} td.p .pi{margin-right:5px;user-select:none;-webkit-user-select:none}
.{root} form.tbl{margin:0;counter-reset:rows}
.{root} form.tbl tbody tr{counter-increment:rows}
.{root} .tt{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:14px}
.{root} form.tbl h4,.{root} .tt h4{margin:14px 0 4px}
.{root} .tb{display:none;flex-wrap:wrap;align-items:center;gap:4px 10px;font-size:12px;margin:2px 0 4px}
.{root} .tb .tl{opacity:.6}
.{root} .flt{display:inline-flex;align-items:center;gap:5px;opacity:.9}
.{root} .flt select{font:inherit;font-size:12px;color:inherit;background:transparent;cursor:pointer}
.{root} .flt select{border:1px solid rgba(127,127,127,.45);border-radius:12px;padding:1px 6px}
.{root} .flt option{background:var(--jp-layout-color1,var(--vscode-editor-background,Canvas))}
.{root} .flt option{color:var(--jp-ui-font-color1,var(--vscode-editor-foreground,CanvasText))}
.{root} .cols{position:relative}
.{root} .cols>summary{list-style:none;cursor:pointer;border:1px solid rgba(127,127,127,.45);border-radius:12px;padding:0 8px}
.{root} .cols>summary{opacity:.55;transition:opacity .15s}
.{root} .tbl:hover .cols>summary,.{root} .cols[open]>summary,.{root} .cols:has(input:not(:checked))>summary{opacity:1}
.{root} .cols>summary::-webkit-details-marker{display:none}
.{root} .cols>summary::after{content:" \\25BE";opacity:.7}
.{root} .cols>.menu{position:absolute;z-index:10;top:calc(100% + 4px);left:0;min-width:170px;max-height:320px;overflow:auto}
.{root} .cols>.menu{display:flex;flex-direction:column;gap:2px;padding:6px 10px 8px;border:1px solid rgba(127,127,127,.35)}
.{root} .cols>.menu{border-radius:6px;box-shadow:0 6px 18px rgba(0,0,0,.2)}
.{root} .cols>.menu{background:var(--jp-layout-color1,var(--vscode-editor-background,Canvas))}
.{root} .cols label{display:flex;align-items:center;gap:6px;white-space:nowrap;cursor:pointer;padding:1px 0}
.{root} .cols input{margin:0}
.{root} .cols button{align-self:flex-start;margin-top:4px}
.{root} .tbl button[type=reset]{font:inherit;font-size:12px;color:#3b82f6;background:none;border:0;padding:0;cursor:pointer}
.{root} .tbl button[type=reset]:hover{text-decoration:underline}
.{root} .tf{display:none;align-items:center;gap:8px;font-size:12px;margin:-4px 0 8px}
.{root} .tf .cnt{font-weight:600}
.{root} .tf .cnt::before{content:counter(rows)}
@supports selector(:has(*)){
.{root} .tb{display:flex}
.{root} .flt select:has(option:checked:not(.all)),.{root} .cols:has(input:not(:checked))>summary{border-color:#3b82f6}
.{root} .flt select:has(option:checked:not(.all)),.{root} .cols:has(input:not(:checked))>summary{background:rgba(59,130,246,.12)}
.{root} form.tbl:has(select option:checked:not(.all)) .tf{display:flex}
}
@supports (grid-template-columns:subgrid) and selector(:has(*)){
.{root} form.tbl table.t{display:grid;grid-template-columns:var(--gc);width:fit-content;max-width:100%}
.{root} form.tbl table.t>thead,.{root} form.tbl table.t>tbody{display:contents}
.{root} form.tbl table.t tr{display:grid;grid-column:1/-1;grid-template-columns:subgrid}
.{root} form.tbl table.t thead tr{order:-1}
.{root} form.tbl .tw.scroll table.t thead tr{position:sticky;top:0;z-index:2}
.{root} th.srt label{display:none;position:relative;cursor:pointer;user-select:none;-webkit-user-select:none}
.{root} th.srt label.o1{display:inline-flex}
.{root} th.srt:has(.o1 input:checked) label.o1,.{root} th.srt:has(.o2 input:checked) label.o1{display:none}
.{root} th.srt:has(.o1 input:checked) label.o2,.{root} th.srt:has(.o2 input:checked) label.o3{display:inline-flex}
.{root} th.srt label::after{content:"\\2195";position:absolute;left:100%;margin-left:2px;font-size:11px;font-weight:400;opacity:.28}
.{root} th.srt.n label::after{left:auto;right:100%;margin:0 2px 0 0}
.{root} th.srt:hover label::after{opacity:.6}
.{root} th.srt:hover{color:#3b82f6}
.{root} th.srt:has(.ra:checked),.{root} th.srt:has(.rd:checked){color:#3b82f6}
.{root} th.srt:has(.ra:checked) label::after{content:"\\2191";opacity:1}
.{root} th.srt:has(.rd:checked) label::after{content:"\\2193";opacity:1}
{sort_rules}}
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
.{root} .ld{font-weight:600}
.{root} .fd{margin:6px 0 10px}
.{root} .fh{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px;margin:10px 0 4px}
.{root} .fh .fl{font-weight:600;opacity:.75;margin-right:2px}
.{root} .chip{display:inline-block;padding:0 8px;border-radius:9px;font-size:11px;font-weight:600;line-height:18px}
.{root} .chip.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.5)}
.{root} .chip.info{background:rgba(59,130,246,.12);box-shadow:inset 0 0 0 1px rgba(59,130,246,.4)}
.{root} .fi{display:grid;grid-template-columns:16px minmax(0,1fr);column-gap:7px;padding:6px 12px 6px 9px;margin:4px 0}
.{root} .fi{border-left:3px solid #3b82f6;background:rgba(59,130,246,.06);border-radius:0 5px 5px 0}
.{root} .fi.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.{root} .fi::before{content:"\\2139\\FE0E";grid-row:span 2;opacity:.7}
.{root} .fi.warn::before{content:"\\26A0\\FE0E"}
.{root} .fi .hd{font-weight:600}
.{root} .fi .dt{grid-column:2;margin:2px 0 0;opacity:.9}
.{root} .fi ul.dt{padding-left:16px}
.{root} .fi ul.dt li{margin:1px 0}
.{root} .m{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
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
.{root} .next .nw{font-size:12px;opacity:.65;margin-left:6px}""".replace("{sort_rules}", "".join(
    f".{{root}} form.tbl:has(.k{j} .ra:checked) tbody tr{{order:var(--a{j})}}\n"
    f".{{root}} form.tbl:has(.k{j} .rd:checked) tbody tr{{order:var(--d{j})}}\n"
    for j in range(_SORT_COLUMNS)
))


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


_LEAD_END_RE = re.compile(r"[:;](?=\s)|\.(?=\s+[A-Z0-9\"'(<$])")
_MONEY_RE = re.compile(r"(-?<?\$[\d,]+(?:\.\d+)?(?:/month|/mo\b| a month| per month)?)")


def _prose(value: Any, money: bool = False) -> str:
    """Escaped HTML for a sentence this tool wrote, with the calls in it as code that one click selects
    (and, with money=True, amounts like $12.40/month stressed). The text is split on the calls and every
    piece escaped before it's wrapped, so nothing in it becomes markup."""
    pieces = _CALL_RE.split("" if value is None else str(value))
    return "".join(
        f"<code{_SELECT}>{_esc(piece)}</code>" if i % 2
        else "".join(f'<span class="m">{_esc(part)}</span>' if k % 2 else _esc(part)
                     for k, part in enumerate(_MONEY_RE.split(piece))) if money
        else _esc(piece)
        for i, piece in enumerate(pieces)
    )


def _masked(text: str) -> str:
    """The text with its commands and anything in brackets blanked out (same length), so a sentence is never
    split inside them: 'Something went wrong (RuntimeError: boom)' has no lead."""
    masked, before = _CALL_RE.sub(lambda m: "x" * len(m.group(0)), text), None
    while masked != before:  # innermost brackets first
        before, masked = masked, re.sub(r"[(\[][^()\[\]]*[)\]]", lambda m: "x" * len(m.group(0)), masked)
    return masked


def _split_lead(text: str, shortest: int = 8, longest: int = 160) -> tuple[str, str, str]:
    """'Versioning is on: every deleted file is kept.' -> ('Versioning is on', ':', 'every deleted file is
    kept.'): what a sentence says first, the mark after it and the rest (why it matters, what to do).
    ('', '', text) when it has no lead that short. Commands and brackets in it are never split."""
    masked = _masked(text)
    for match in _LEAD_END_RE.finditer(masked):
        if match.start() < shortest:
            continue
        rest = text[match.end():].strip()
        if match.start() > longest or not rest:
            break
        return text[: match.start()], text[match.start()], rest
    return "", "", text


def _prose_sentences(text: str) -> list[str]:
    """Split this tool's prose into sentences, never inside a command or brackets."""
    masked = _masked(text)
    cuts = [m.end() for m in re.finditer(r"\.(?=\s+[A-Z0-9\"'(<$])", masked)]
    return [text[a:b].strip() for a, b in zip([0] + cuts, cuts + [len(text)]) if text[a:b].strip()]


def _capitalized(text: str) -> str:
    """'every file is kept' -> 'Every file is kept', but 'invoices/a.pdf', 'uploads(...)' or a command to copy
    ('aws s3 ls ...') stay as they are."""
    first = text.split(" ", 1)[0]
    if _CALL_RE.match(text):
        return text
    return text[0].upper() + text[1:] if re.fullmatch(r"[a-z][a-z']*,?", first) else text


def _lead(value: Any) -> str:
    """_prose with the sentence's lead in bold and amounts of money stressed:
    '<span class="ld">Versioning is on:</span> every deleted file is kept.' For notes and Warnings cells."""
    text = "" if value is None else str(value)
    head, mark, rest = _split_lead(text)
    if not head:
        return _prose(text, money=True)
    return f'<span class="ld">{_prose(head + mark, money=True)}</span> {_prose(rest, money=True)}'


def _finding_html(level: str, message: str) -> str:
    """One finding as a headline (what's wrong) over its detail (why it matters, what it costs, what to do),
    the detail's sentences as points when there are several."""
    head, _, rest = _split_lead(message)
    if not head:
        head, rest = (message, "") if len(message) <= 200 else ("", message)
    head = head.rstrip().removesuffix(".") if not head.endswith("..") else head
    parts = _prose_sentences(_capitalized(rest)) if rest else []
    if len(parts) > 1:
        detail = '<ul class="dt">' + "".join(f"<li>{_prose(p, money=True)}</li>" for p in parts) + "</ul>"
    else:
        detail = f'<div class="dt">{_prose(parts[0], money=True)}</div>' if parts else ""
    headline = f'<div class="hd">{_prose(head, money=True)}</div>' if head else ""
    return f'<div class="fi {"warn" if level == "warn" else "info"}">{headline}{detail}</div>'


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


# ----------------------------------------------------------------------------- tables: sorting, filters, keys

_SORT_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5, "ms": 1}
_SORT_NUMBER_RE = re.compile(
    r"^(-?)(<?)\$?([\d,]*\.?\d+)\+?(?: ?(B|KB|MB|GB|TB|PB|%|ms))?(?: of [\d,]+| \([^()]*\))?$")
_SORT_AGE_RE = re.compile(r"^(\d+)(y|mo|d|h|m) ago$")
# '2024-05-01', '2024-05-01 14:02', or a time with its zone's name: '2024-05-01 14:02:31 IST', '... UTC+05:30'
_SORT_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?: [A-Z]{2,5}(?:[+-]\d{2}(?::\d{2})?)?)?)?$")
_AGE_SECONDS = {"y": 365 * 86400, "mo": 30 * 86400, "d": 86400, "h": 3600, "m": 60}
_SORT_WORDS = {  # a column's kind -> what its first and second header click show
    "number": ("largest first", "smallest first"),
    "age": ("oldest first", "newest first"),
    "date": ("newest first", "oldest first"),
    "text": ("A to Z", "Z to A"),
}
_FILTER_VALUES = 12  # a column gets a filter when it holds 2 to this many different values, some repeated
_FILTER_COLUMNS = 3  # filters per table at most, the leftmost columns first
# Hides the table controls until the stylesheet shows them, so a notebook opened without its styles (JupyterLab
# strips <style> from an untrusted notebook) shows a plain table. Its sanitizer drops a bare `hidden`, not this.
_HIDE = ' hidden="hidden"'


def _sort_key(text: str) -> tuple[int, Any] | None:
    """What a table cell sorts by: (0, number) for sizes, money, counts, percentages and ages ('2.1 GB' is
    bytes, '<$0.01' half a cent, '3d ago' seconds), (1, text) for dates, (2, parts) for the rest in natural
    order ('epoch-2' before 'epoch-10'). None for an empty cell, which sorts last both ways."""
    text = text.split("\n", 1)[0].strip().removeprefix("📁 ")
    if text in ("", "-", "—"):
        return None
    number = _SORT_NUMBER_RE.match(text)
    if number:
        sign, below, digits, unit = number.groups()
        value = float(digits.replace(",", "")) * _SORT_UNITS.get(unit or "", 1) / (2 if below else 1)
        return 0, -value if sign else value
    age = _SORT_AGE_RE.match(text)
    if age or text == "just now":
        return 0, float(int(age.group(1)) * _AGE_SECONDS[age.group(2)]) if age else 0.0
    if _SORT_DATE_RE.match(text):
        return 1, text
    parts = re.split(r"(\d+)", text.casefold())
    return 2, tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts if part)


def _sort_kind(values: list[str], keys: list[tuple[int, Any]]) -> str:
    """'number', 'age', 'date' or 'text': what most of a column's cells hold, which decides its first sort."""
    cls = Counter(key[0] for key in keys).most_common(1)[0][0]
    if cls:
        return "date" if cls == 1 else "text"
    ages = sum(bool(_SORT_AGE_RE.match(v.strip())) or v.strip() == "just now" for v in values)
    return "age" if ages * 2 > len(keys) else "number"


def _ranks(keys: list[Any]) -> tuple[list[int], list[int]]:
    """Each row's place when the column is sorted up and when it's sorted down. Rows that tie share a place,
    so they keep their order; empty cells come last both ways."""
    present = sorted(key for key in keys if key is not None)
    first: dict[Any, int] = {}
    for i, key in enumerate(present):
        first.setdefault(key, i)
    counts, last = Counter(present), len(keys)
    up = [last if key is None else first[key] for key in keys]
    down = [last if key is None else len(present) - first[key] - counts[key] for key in keys]
    return up, down


def _filters(block: _Table, columns: list[list[str]], numeric: list[bool]) -> list[tuple[int, list[tuple[str, int]]]]:
    """The columns that get a filter: a few different values, some repeated (a storage class, a status, a
    region), never numbers, dates, keys or sentences -> [(column, [(value, rows), ...] most rows first)]."""
    found: list[tuple[int, list[tuple[str, int]]]] = []
    for j, column in enumerate(columns):
        skip = set(block.prose_cols) | set(block.code_cols) | set(block.path_cols) | ({0} if block.tree else set())
        if len(found) == _FILTER_COLUMNS or len(column) < 6:
            break
        values = [value.strip() for value in column if value.strip()]
        if j in skip or numeric[j] or any("\n" in value or ", " in value for value in values):
            continue
        counts = Counter(values)
        keys = [key for key in map(_sort_key, counts) if key is not None]
        if 2 <= len(counts) <= _FILTER_VALUES and len(counts) < len(values) and all(key[0] == 2 for key in keys):
            found.append((j, counts.most_common()))
    return found


_ICONS = {  # extension -> the icon in front of a file's name, in key columns and in s3_explorer's list
    **dict.fromkeys(("csv", "tsv", "tab", "psv", "parquet", "pq", "orc", "feather", "arrow", "ipc", "avro"), "📊"),
    **dict.fromkeys(("xlsx", "xlsm", "xls"), "📗"),
    **dict.fromkeys(("json", "jsonl", "ndjson", "yaml", "yml", "toml", "xml", "ini", "cfg", "conf"), "📋"),
    **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg"), "🖼️"),
    **dict.fromkeys(("wav", "mp3", "flac", "ogg", "m4a", "aac"), "🎵"),
    **dict.fromkeys(("mp4", "webm", "mov", "m4v", "avi", "mkv"), "🎬"),
    **dict.fromkeys(("zip", "tar", "tgz", "7z", "rar", "gz", "bz2", "xz", "zst"), "📦"),
    **dict.fromkeys(("safetensors", "pt", "pth", "ckpt", "onnx", "h5", "keras", "pkl", "pickle", "joblib",
                     "gguf", "bin"), "🧠"),
    **dict.fromkeys(("npy", "npz"), "🔢"),
    **dict.fromkeys(("docx", "docm", "doc", "dotx", "rtf"), "📘"),
    **dict.fromkeys(("pptx", "pptm", "ppt", "ppsx"), "📙"),
    "pdf": "📕",
    "ipynb": "📓",
}


def _file_icon(key: str) -> str:
    """📁 for a folder, else the file's icon by its extension: 📊 tables, 📕 PDF, 🖼️ pictures, 🧠 models, ...,
    📄 for anything else. 'data.csv.gz' is a table and 'logs.tar.gz' an archive."""
    if key.endswith("/"):
        return "📁"
    ext = file_extension(key)
    if ext.startswith("("):  # '(none)'
        return "📄"
    return _ICONS.get(ext.split(".")[0], _ICONS.get(ext.rsplit(".", 1)[-1], "📄"))


def _path_html(text: str) -> str:
    """A key in a table cell: its icon (📁 for a folder, 📊 for a table, ... by _file_icon), its folder dimmed and,
    when long, shortened from the left (the nearest folders stay), then the file name in full. Each line of the
    cell is one key. The icon is only drawn: the cell's text, which sorts and copies, is the key alone."""
    lines = []
    for line in text.split("\n"):
        if line.startswith("📁 "):
            icon, line = "📁", line[2:]
        else:
            icon = "" if line in ("", "-") or line.startswith(("…", "(")) else _file_icon(line)
        folder, _, name = line.rstrip("/").rpartition("/")
        name += line[len(line.rstrip("/")):]
        folder = f"{folder}/" if folder and not line.startswith(("…", "(")) else ""  # not '… and 3 more'
        if not folder:
            name = line
        room = 86 - len(name)  # a long name leaves less room for its folder (30em, about 56 characters)
        cap = f' style="max-width:{max(room, 12)}ch"' if room < 56 else ""
        lines.append('<span class="pl">' + (f'<span class="pi">{icon}</span>' if icon else "")
                     + (f'<span class="pd"{cap}>{_esc(folder)}</span>' if folder else "")
                     + f'<span class="pn">{_esc(name)}</span></span>')
    return "".join(lines)


def _clip_path(text: str, width: int = 90) -> str:
    """A key for a text table: when it's too long the start of its folder goes, never the file name."""
    if len(text) <= width:
        return text
    tail = text[-(width - 1):]
    cut = tail.find("/")
    return "…" + (tail[cut:] if 0 <= cut < len(tail) - 1 else tail)


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
    return f'<div class="note {block.level}">{_lead(block.text)}</div>'


@_draws(_Findings, "html")
def _findings_html(block: _Findings, report: _Report) -> str:
    """The findings panel: warnings first, with a chip counting each kind, every finding a headline over its detail
    (_finding_html); block.empty (an ok note) when there's none."""
    items = _ordered(block.items)
    if not items:
        return f'<div class="note ok">{_lead(block.empty)}</div>' if block.empty else ""
    warns = sum(level == "warn" for level, _ in items)
    chips = (f'<span class="chip warn">{_plural(warns, "warning")}</span>' if warns else "") + (
        f'<span class="chip info">{_plural(len(items) - warns, "note")}</span>' if len(items) > warns else "")
    head = f'<div class="fh"><span class="fl">Findings</span>{chips}</div>'
    return f'<div class="fd">{head}{"".join(_finding_html(level, msg) for level, msg in items)}</div>'


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
    """A _Table as HTML. Over 30 rows it scrolls under a sticky header. With 3 rows or more, a click on a
    header sorts by that column (largest, newest or A to Z first; again for the other way; a third click
    for the original order); a column of a few repeated values gets a filter; with 3 columns or more, a
    Columns menu ticks columns off. All plain HTML and CSS: radio buttons, selects and checkboxes in a form,
    read by :has() rules (the per-table ones go into report.rules), so they keep working in a reopened notebook,
    where scripts don't run. Without the stylesheet the controls stay hidden and the table is plain."""
    if not block.rows:
        return (f"<h4>{_prose(block.title)}</h4>" if block.title else "") + '<div class="more">(none)</div>'
    rows, hidden = _visible_rows(block, report.max_rows)
    width = len(block.headers)
    columns = [["" if j >= len(row) or row[j] is None else str(row[j]) for row in rows] for j in range(width)]
    numeric = [
        sum(bool(_NUMERIC_RE.match(v)) for v in column if v) * 2 > sum(bool(v) for v in column) > 0
        for column in columns
    ]
    sorts: dict[int, tuple[list[int], list[int], str]] = {}
    if block.sortable and not block.tree and 0 not in block.code_cols and len(rows) >= 3:  # help() keeps its order
        for j in range(min(width + (block.bars is not None), _SORT_COLUMNS)):
            if j in block.prose_cols:
                continue
            values = columns[j] if j < width else []
            keys = [_sort_key(v) for v in values] if j < width else [(0, float(b)) for b in block.bars[: len(rows)]]
            present = [key for key in keys if key is not None]
            if len(set(present)) > 1:
                sorts[j] = (*_ranks(keys), _sort_kind(values, present))
    filters = _filters(block, columns, numeric)
    heads = list(block.headers) + ([block.bar_label] if block.bars is not None else [])
    cells_th = []
    for j, header in enumerate(heads):
        css = ["n"] if j < width and numeric[j] else []
        inner = _esc(header)
        if j in sorts:
            first, second = _SORT_WORDS[sorts[j][2]]
            radios = ("ra", "rd") if sorts[j][2] == "text" else ("rd", "ra")
            inner = "".join(
                f'<label class="o{n}" title="{tip}"{_HIDE if n > 1 else ""}>'
                f'<input type="radio" name="s" class="{radio}"{_HIDE}>{_esc(header)}</label>'
                for n, radio, tip in ((1, radios[0], f"Sort: {first}"), (2, radios[1], f"Sort: {second}"),
                                      (3, "rn", "Back to the original order"))
            )
            css = ["srt", f"k{j}", *css]
        cells_th.append(f'<th class="{" ".join(css)}">{inner}</th>' if css else f"<th>{inner}</th>")
    position = {j: {value: i for i, (value, _) in enumerate(values)} for j, values in filters}
    body = []
    for i, row in enumerate(rows):
        cells = []
        for j, cell in enumerate(row):
            text = "" if cell is None else str(cell)
            inner, attrs = _cell_html(cell, text), ""
            if block.tree and j == 0:
                css = "tree"
            elif j in block.code_cols and text:
                css, inner = "c", f"<code{_SELECT}>{inner}</code>"
            elif j in block.prose_cols:
                css, inner = "", _lead(text)
            elif j in block.path_cols and text:
                css, inner = "p", _path_html(text)
                attrs = f' title="{_esc(text)}"' if "/" in text.rstrip("/") else ""
            elif _NUMERIC_RE.match(text):
                css = "n"
            else:
                css = "s" if len(text) <= 16 and "\n" not in text else ""
            cells.append(f'<td class="{css}"{attrs}>{inner}</td>' if css else f"<td>{inner}</td>")
        if block.bars is not None:
            pct = max(0.0, min(1.0, block.bars[i])) * 100
            cells.append(
                f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                f"</span></span>{pct:.1f}%</td>"
            )
        marks = [f"f{j}v{position[j][columns[j][i].strip()]}" for j, _ in filters if columns[j][i].strip()]
        order = ";".join(f"--a{j}:{up[i]};--d{j}:{down[i]}" for j, (up, down, _) in sorts.items())
        body.append("<tr" + (f' class="{" ".join(marks)}"' if marks else "")
                    + (f' style="{order}"' if order else "") + f">{''.join(cells)}</tr>")
    choose = len(heads) >= 3  # a column chooser: tick off the columns you don't need
    interactive = bool(sorts or filters or choose)
    grid = f' style="--gc:repeat({len(heads)},auto)"' if interactive else ""
    table = (
        f'<div class="tw{" scroll" if len(rows) > 30 else ""}"><table class="t"{grid}><thead><tr>{"".join(cells_th)}'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )
    tools = foot = more = ""
    root = report.root
    if choose:
        report.rules.update(f".{root} form.tbl:has(.cv{j}:not(:checked)) tr>:nth-child({j + 1}){{display:none}}"
                            for j in range(len(heads)))
        tools = ('<details class="cols"><summary title="Choose the columns to show">Columns</summary><div class="menu">'
                 + "".join(f'<label><input type="checkbox" class="cv{j}" checked>{_esc(header)}</label>'
                           for j, header in enumerate(heads))
                 + '<button type="reset" title="Every column, the original order and no filters">Reset table'
                 "</button></div></details>")
    if filters:
        for j, values in filters:
            report.rules.update(f".{root} form.tbl:has(.f{j} .v{i}:checked) tbody tr:not(.f{j}v{i}){{display:none}}"
                                for i in range(len(values)))
        tools += '<span class="tl">Filter</span>' + "".join(
            f'<label class="flt">{_esc(heads[j])}<select class="f{j}"><option class="all" selected>All</option>'
            + "".join(f'<option class="v{i}">{_esc(value)} ({count:,})</option>'
                      for i, (value, count) in enumerate(values))
            + "</select></label>"
            for j, values in filters
        )
        foot = (f'<div class="tf"{_HIDE}><span><span class="cnt"></span> of {len(rows):,} rows match</span>'
                '<button type="reset">Clear filters</button></div>')
    tools = f'<div class="tb"{_HIDE}>{tools}</div>' if tools else ""
    if hidden:
        also = f"; sorting and filters use the {len(rows):,} shown" if sorts or filters else ""
        more = f'<div class="more">{_esc(_hidden(hidden, block, report.max_rows) + also)}</div>'
    if block.collapsed:
        out = (f'<details class="sec"><summary>{_prose(block.title or "Details")} ({len(block.rows):,})</summary>'
               f"{tools}{table}{foot}{more}</details>")
    else:
        title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
        out = (f'<div class="tt">{title}{tools}</div>' if title and tools else title + tools) + table + foot + more
    return f'<form class="tbl" method="dialog" autocomplete="off">{out}</form>' if interactive else out


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
        [", ".join(map(_clip_path, text.split("\n"))) if j in block.path_cols else _clip(text.replace("\n", ", "))
         for j, text in enumerate("" if c is None else str(c) for c in row)]
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


def _rules_html(report: _Report) -> str:
    """The <style> holding the CSS rules the report's blocks added (its tables' filters and columns), if any."""
    return "<style>" + "\n".join(sorted(report.rules)) + "</style>" if report.rules else ""


def _blocks_html(blocks: list[Any], max_rows: int = 0, root: str = "") -> str:
    """Blocks as HTML without a report's stylesheet and box, for a window that has both inside a box of the root
    class `root` (a title gets no badge)."""
    report = _Report(max_rows, root=root, blocks=tuple(blocks))
    body = "".join(_block_html(block, report) for block in blocks)
    return _rules_html(report) + body


def _report_html(blocks: list[Any], max_rows: int, css: str, root: str, badge: str) -> str:
    """A report as HTML: the service's stylesheet, the rules its blocks added, then its blocks in a box of the
    service's root class."""
    report = _Report(max_rows, badge, root, tuple(blocks))
    body = [_block_html(block, report) for block in blocks]
    return "".join([css, _rules_html(report), f'<div class="{root}">', *body, "</div>"])


def _block_lines(block: Any, report: _Report) -> list[str]:
    """One block as lines of text, drawn by what's registered for its type."""
    draw = _drawer(_TEXT, block)
    return draw(block, report) if draw else _note_lines(_undrawn(block), report)


def _render_text(blocks: list[Any], max_rows: int) -> str:
    """A report as text: each block's lines, one after the other."""
    report = _Report(max_rows)
    return "\n".join(line for block in blocks for line in _block_lines(block, report))
