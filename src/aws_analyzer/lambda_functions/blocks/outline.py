"""
_Outline: how_it_works()'s steps as a tree of code lines, each with tags for what it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..._kit.blocks import _Report, _draws
from ..._kit.highlight import _python_html
from ..._kit.render import _prose
from ..._kit.text import _esc

from ..analysis.codemap.model import CodeStep
from ..analysis.codemap.findings import _short_name


@dataclass
class _Outline:
    """
    how_it_works()'s steps: the lines of the code that do something, as a tree (a loop's lines under it, each of its
    own functions folded under the line that calls it), with what each line does. In HTML the code in colour with
    its line numbers and coloured tags for what it does; in text the lines indented, what they do as a # comment.
    """

    steps: list[CodeStep]
    title: str = ""
    empty: str = "(nothing it can follow)"


_NOTE_TIPS = {  # what an outline tag means, on hover
    "aws-reads": "an AWS call that reads", "aws-writes": "an AWS call that writes",
    "aws-sends": "an AWS call that sends a message or an event", "aws-runs": "an AWS call that runs something",
    "aws-signs": "a signed link for this AWS call: no request is sent now", "client": "a boto3 client or resource",
    "env": "an environment variable it reads", "event": "a field of the event it reads", "http": "an HTTP request",
    "log": "a log line", "file": "a file on Lambda's disk", "sleep": "a pause the function is billed for",
    "command": "a command run as another process", "warn": "worth a look: see the findings", "route": "a web route",
    "more": "",
}

_FOLDED_KINDS = ("aws-reads", "aws-writes", "aws-sends", "aws-runs", "aws-signs", "env", "http", "warn", "file", "sleep",
                 "command")  # the tags a folded function shows of what's inside it


def _note_text(kind: str, text: str) -> str:
    """
    An outline tag in words: 'S3 GetObject (reads)', 'env DB_HOST', 'event Records[].body'.
    """
    if kind.startswith("aws-"):
        return f"{text} ({kind[4:]})"
    return f"{kind} {text}" if kind in ("env", "event") else text


def _chips_html(notes: Iterable[tuple[str, str]]) -> str:
    out = []
    for kind, text in notes:
        word = f"<i>{_esc(kind[4:])}</i>" if kind.startswith("aws-") else ""
        label = f"{kind} {text}" if kind in ("env", "event") else text
        out.append(f'<span class="nt n-{_esc(kind)}" title="{_esc(_NOTE_TIPS.get(kind, ""))}">{_esc(label)}{word}</span>')
    return "".join(out)


def _inside(step: CodeStep) -> list[tuple[str, str]]:
    """
    The tags of every line under a step, once each: what a folded function does.
    """
    found: list[tuple[str, str]] = []
    for each in step.walk():
        for note in each.notes:
            if each is not step and note[0] in _FOLDED_KINDS and note not in found:
                found.append(note)
    return found


def _def_html(step: CodeStep) -> str:
    """
    One of its functions called on a line (or a module it imports), folded under it: its name, where it is and what
    it does inside; open, its steps.
    """
    if step.kind == "module":
        label = f'<b>{_esc(step.file)}</b> <span class="of">runs when it\'s imported</span>'
    else:
        label = f'<b>{_esc(_short_name(step.name))}()</b> <span class="of">{_esc(step.file)} line {step.line}</span>'
    own = _chips_html(step.notes)
    if step.seen or not step.steps:
        why = ("its steps are shown where it's first called" if step.seen and not any(k == "more" for k, _ in step.notes)
               else "" if step.notes else "nothing in it to follow")
        return (f'<div class="ol oseen"><span class="ln"></span><span class="on">↳ {label}</span>{own}'
                + (f'<span class="oq">{_esc(why)}</span>' if why else "") + "</div>")
    inside = _inside(step)
    chips = _chips_html(inside[:6]) + (f'<span class="oq">+{len(inside) - 6} more</span>' if len(inside) > 6 else "")
    head = (f'<summary class="ol"><span class="ln"></span><span class="on"><span class="och"></span>{label}</span>{own}'
            f'<span class="os">{chips}</span></summary>')
    body = (f'<div class="ol k-def"><span class="ln">{step.line}</span><code class="ocd">{_python_html(step.code)}</code>'
            "</div>" if step.kind == "def" else "")
    return (f'<details class="od">{head}<div class="ob">{body}'
            f'<div class="ox">{_flow_html(step.steps)}</div></div></details>')


def _flow_html(steps: list[CodeStep], top: bool = False) -> str:
    out = []
    for step in steps:
        if top and step.kind == "def" and step.steps:  # the handler: its steps, always open
            out.append(f'<div class="ol k-def"><span class="ln">{step.line}</span><code class="ocd">'
                       f'{_python_html(step.code)}</code><span class="of">{_esc(step.file)}</span>'
                       f'{_chips_html(step.notes)}</div><div class="ox">{_flow_html(step.steps)}</div>')
            continue
        if step.kind in ("def", "module"):
            out.append(_def_html(step))
            continue
        out.append(f'<div class="ol k-{_esc(step.kind)}"><span class="ln">{step.line or ""}</span>'
                   f'<code class="ocd">{_python_html(step.code)}</code>{_chips_html(step.notes)}</div>')
        inner = "".join(_def_html(call) for call in step.calls) + _flow_html(step.steps)
        if inner:
            out.append(f'<div class="ox">{inner}</div>')
    return "".join(out)


@_draws(_Outline, "html")
def _outline_html(block: _Outline, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    if not block.steps:
        return title + f'<div class="more">{_esc(block.empty)}</div>'
    return f'{title}<div class="flow">{_flow_html(block.steps, top=True)}</div>'


@_draws(_Outline, "text")
def _outline_lines(block: _Outline, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    if not block.steps:
        return out + [block.empty]

    def comment(notes: list[tuple[str, str]], *more: str) -> str:
        said = [_note_text(kind, text) for kind, text in notes] + [m for m in more if m]
        return "   # " + " · ".join(said) if said else ""

    def walk(steps: list[CodeStep], pad: int, called: bool) -> None:
        for step in steps:
            if step.kind == "module":
                out.append(f"{'':>5}  {' ' * pad}↳ {step.file}, run when it's imported")
                walk(step.steps, pad + 4, False)
                continue
            if step.kind == "def":
                more = ("its steps are shown where it's first called" if step.seen and not any(
                    k == "more" for k, _ in step.notes) else "" if step.steps or step.notes else "nothing in it to follow")
                out.append(f"{step.line:>5}  {' ' * pad}{'↳ ' if called else ''}{step.code}"
                           + comment(step.notes, step.file, more))
                walk(step.steps, pad + (4 if called else 2), False)
                continue
            out.append(f"{step.line or '':>5}  {' ' * pad}{step.code}{comment(step.notes)}")
            for call in step.calls:
                walk([call], pad + 4, True)
            walk(step.steps, pad + 2, False)

    walk(block.steps, 0, False)
    return out
