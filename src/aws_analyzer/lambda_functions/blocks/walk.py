"""
_Walk: how_it_works()'s steps to click through one at a time, like a debugger: the code on one side, the step picked
on the other with what it does, and buttons that step into, over and out of its functions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..._kit.blocks import _Report, _draws
from ..._kit.highlight import _python_html
from ..._kit.render import _HIDE
from ..._kit.text import _esc

from ..analysis.codemap.findings import _short_name
from ..analysis.codemap.model import CodeStep
from .outline import _NOTE_TIPS, _Outline, _chips_html, _outline_lines


@dataclass
class _Walk:
    """
    how_it_works()'s steps to step through: what runs when Lambda starts the code (startup), then each call from the
    handler on (steps). In HTML the code's lines on the left, where a click picks one, and the step picked on the
    right: the functions it's in, the loops and branches around it, what it does, and Back, Next (into the functions
    a line calls), Over (past them) and Out (back to the caller). CSS only, so it still works in a reopened notebook.
    In text the same as an _Outline of each.
    """

    startup: list[CodeStep]
    steps: list[CodeStep]
    startup_title: str = ""
    title: str = ""
    empty: str = "(nothing it can follow)"


_FRAMES = ("def", "module")
_LINE_CHIPS = 2

_KIND_WORDS = {
    "loop": "A loop: the lines under it run once for each pass.",
    "if": "A branch: the lines under it run only when its condition holds.",
    "else": "The other way: the lines under it run when the branches above don't.",
    "with": "Opens something for the lines under it, and closes it after them, even when one fails.",
    "try": "Guards the lines under it: an error in one goes to the except below instead of failing the call.",
    "except": "Runs only when a line in the try above raises this error.",
}

_AWS_WORDS = {"reads": "reads from AWS", "writes": "writes to AWS", "sends": "sends a message or an event",
              "runs": "runs something in AWS", "signs": "signs a link: no request is sent now"}


@dataclass
class _Stop:
    """
    One step of the walk: a line of the code, where it sits, and where the buttons go from it.

        part    'start' (what runs when Lambda starts the code) or 'call'
        frames  the stops of the functions it runs in (a def or module step), outermost first; its own when it's one
        around  the stops of the loops, branches, with and try lines around it, in its function
        caller  a function's stop: the stop of the line that calls it
        end     the first stop after everything under it, where Over goes
    """

    step: CodeStep
    depth: int
    part: str
    frames: list[int] = field(default_factory=list)
    around: list[int] = field(default_factory=list)
    caller: int | None = None
    end: int = 0


class _Stops:
    """
    The walk's steps in the order they run: a line, then the functions it calls with their lines, then the lines
    under it (a loop's body, a branch's). The lines under a _FRAMES step (a function, or a module being imported)
    run inside it.
    """

    def __init__(self, walk: _Walk):
        self.stops: list[_Stop] = []
        self.part = "start"
        for step in walk.startup:
            self._add(step, 0, [], [])
        self.part = "call"
        for step in walk.steps:
            self._add(step, 0, [], [])
        self.first_shown = {}
        for i, stop in enumerate(self.stops):
            if stop.step.kind == "def" and stop.step.steps and not stop.step.seen:
                self.first_shown.setdefault(stop.step.name, i)

    def _add(self, step: CodeStep, depth: int, frames: list[int], around: list[int], caller: int | None = None) -> None:
        index = len(self.stops)
        is_frame = step.kind in _FRAMES
        inner_frames = [*frames, index] if is_frame else frames
        inner_around = [] if is_frame else [*around, index]
        self.stops.append(_Stop(step, depth, self.part, inner_frames, [] if is_frame else around, caller))
        for call in step.calls:
            self._add(call, depth + 1, frames, around, caller=index)
        for child in step.steps:
            self._add(child, depth + 1, inner_frames, inner_around)
        self.stops[index].end = len(self.stops)


def _frame_name(step: CodeStep) -> str:
    """
    A function as the walk names it: 'find_order()', or a module's file for the code it runs when it's imported.
    """
    return step.file if step.kind == "module" else f"{_short_name(step.name)}()"


class _WalkHtml:
    """
    Draws a _Walk: each step's line of the code, a radio button in it (focusable, so the code scrolls to the step
    picked, and in a browser tab of its own the arrow keys step through; JupyterLab keeps them for its cells), and each step's panel, which a :has() rule in report.rules shows while
    its radio is checked. A random group name keeps two reports in one notebook apart. A line shows _LINE_CHIPS of
    its tags; its step's panel shows them all.
    """

    def __init__(self, walk: _Walk, report: _Report):
        self.walk = walk
        self.report = report
        found = _Stops(walk)
        self.stops = found.stops
        self.first_shown = found.first_shown
        self.group = "w" + os.urandom(5).hex()

    def _to(self, index: int, inner: str, css: str = "", tip: str = "") -> str:
        title = f' title="{_esc(tip)}"' if tip else ""
        return f'<label class="{css}" for="{self.group}-{index}"{title}>{inner}</label>'

    def _line_text(self, i: int, stop: _Stop) -> str:
        step = stop.step
        if step.kind == "module":
            return f'↳ <b>{_esc(step.file)}</b> <span class="of">runs when it\'s imported</span>'
        if step.kind == "def" and stop.caller is not None:
            shown = self.first_shown.get(step.name, i)
            where = f"shown at step {shown + 1}" if shown != i else f"{step.file} line {step.line}"
            return f'↳ <b>{_esc(_frame_name(step))}</b> <span class="of">{_esc(where)}</span>'
        return f'<code class="ocd">{_python_html(step.code)}</code>'

    def _line(self, i: int, stop: _Stop) -> str:
        step = stop.step
        checked = " checked" if i == 0 else ""
        radio = f'<input type="radio" class="wr w{i}" name="{self.group}" id="{self.group}-{i}"{checked}{_HIDE}>'
        chips = _chips_html(step.notes[:_LINE_CHIPS])
        if len(step.notes) > _LINE_CHIPS:
            chips += f'<span class="oq">+{len(step.notes) - _LINE_CHIPS}</span>'
        return (f'<label class="wl k-{_esc(step.kind)}" style="--d:{stop.depth}">{radio}'
                f'<span class="ln">{step.line or ""}</span><span class="wt">{self._line_text(i, stop)}</span>'
                f'<span class="wx">{chips}</span></label>')

    def _tree(self) -> str:
        out = []
        part = ""
        for i, stop in enumerate(self.stops):
            if stop.part != part:
                part = stop.part
                heading = self.walk.startup_title if part == "start" else self.walk.title
                out.append(f'<div class="wsec">{_esc(heading)}</div>' if heading else "")
            out.append(self._line(i, stop))
        if not self.walk.steps:
            out.append(f'<div class="wsec">{_esc(self.walk.title)}</div><div class="more">{_esc(self.walk.empty)}</div>')
        return "".join(out)

    def _crumbs(self, i: int, stop: _Stop) -> str:
        crumbs = ['<span class="wc0">when Lambda starts it</span>'] if stop.part == "start" else []
        for frame in stop.frames:
            name = _esc(_frame_name(self.stops[frame].step))
            crumbs.append(f"<b>{name}</b>" if frame == i else self._to(frame, name, "wc", "Go to its first line"))
        around = [self._to(a, f'<code class="ocd">{_python_html(self.stops[a].step.code)}</code>', "wc",
                           "Go to this line") for a in stop.around]
        inside = f'<div class="wa">in {" › ".join(around)}</div>' if around else ""
        return f'<div class="wcr">{" › ".join(crumbs)}</div>{inside}' if crumbs else inside

    def _meaning(self, i: int, stop: _Stop) -> str:
        """
        What the step's kind of line means: entering a function, a loop, returning to the caller...
        """
        step = stop.step
        in_handler = stop.part == "call" and len(stop.frames) <= 1
        if step.kind == "module":
            return f"Imports {step.file}: the code at its top level runs now, once per cold start, not on each call."
        if step.kind == "def" and stop.caller is None:
            return "Lambda calls this with the event and the context: each call starts here."
        if step.kind == "def":
            called = self.stops[stop.caller].step.line if stop.caller is not None else 0
            shown = self.first_shown.get(step.name, i)
            if shown != i:
                return f"Runs {_frame_name(step)} again: its steps are shown where it's first called, step {shown + 1}."
            return f"Goes into {_frame_name(step)}, in {step.file} line {step.line}, called from line {called}."
        if step.kind == "return" and in_handler:
            return "Returns: what Lambda hands back to whoever called the function."
        if step.kind == "return" and stop.frames:
            return f"Returns to the line that called {_frame_name(self.stops[stop.frames[-1]].step)}."
        if step.kind == "raise":
            return ("Raises an error: the call fails, and Lambda reports it (and may retry it)." if in_handler else
                    "Raises an error: it goes up to the line that called this function, unless a try catches it.")
        return _KIND_WORDS.get(step.kind, "")

    def _does(self, i: int, stop: _Stop) -> str:
        items = []
        meaning = self._meaning(i, stop)
        if meaning:
            items.append(f'<li class="wm">{_esc(meaning)}</li>')
        for kind, text in stop.step.notes:
            tip = _AWS_WORDS.get(kind[4:], "") if kind.startswith("aws-") else _NOTE_TIPS.get(kind, "")
            items.append(f"<li>{_chips_html([(kind, text)])}<span>{_esc(tip)}</span></li>")
        for call in stop.step.calls:
            target = next(j for j in range(i + 1, stop.end) if self.stops[j].step is call)
            what = "its own module: Next goes into the code it runs when it's imported" if call.kind == "module" else (
                "its own function: Next goes into it, Over runs it and goes on")
            items.append(f"<li>{self._to(target, f'<b>{_esc(_frame_name(call))}</b>', 'wc', 'Go into it')}"
                         f"<span>{what}</span></li>")
        return f'<ul class="wd">{"".join(items)}</ul>' if items else ""

    def _button(self, target: int | None, label: str, tip: str, css: str = "") -> str:
        if target is None:
            return f'<span class="wbt off {css}">{label}</span>'
        return self._to(target, label, f"wbt {css}", tip)

    def _buttons(self, i: int, stop: _Stop) -> str:
        count = len(self.stops)
        is_last = i + 1 >= count
        nested = stop.part == "start" or len(stop.frames) > 1
        out_to = self.stops[stop.frames[-1]].end if stop.frames and nested else None
        over = stop.end if i + 1 < stop.end < count else None
        into = stop.step.calls[0] if stop.step.calls else None
        next_label = f"Into {_esc(_frame_name(into))} ›" if into is not None else "Next ›"
        return "".join([
            self._button(i - 1 if i else None, "‹ Back", "The step before"),
            self._button(0 if is_last else i + 1, "From the top ⟲" if is_last else next_label,
                         "Back to the first step" if is_last else "The next line that runs", "go"),
            self._button(over, "Over ⤼", "Past what's under this line: the functions it calls and its block"),
            self._button(out_to if out_to is not None and out_to != over and out_to < count else None, "Out ↑",
                         "Out of this function: back to the line after the one that called it"),
        ])

    def _panel(self, i: int, stop: _Stop) -> str:
        step = stop.step
        count = len(self.stops)
        self.report.rules.add(f".{self.report.root} .wk:has(.w{i}:checked) .wp{i}{{display:block}}")
        where = f"{step.file} line {step.line}" if step.line else step.file
        return (f'<div class="wp wp{i}"{_HIDE}><div class="wh"><span class="wn">Step {i + 1} of {count}</span>'
                f'<span class="wf">{_esc(where)}</span></div>'
                f'<div class="wbar"><i style="width:{(i + 1) * 100 / count:.1f}%"></i></div>'
                f"{self._crumbs(i, stop)}"
                f'<div class="wcode"><code class="ocd">{_python_html(step.code)}</code></div>'
                f"{self._does(i, stop)}"
                f'<div class="wb">{self._buttons(i, stop)}</div></div>')

    def html(self) -> str:
        panels = "".join(self._panel(i, stop) for i, stop in enumerate(self.stops))
        tip = ("Click a line to jump to it, or step with the buttons: Next goes into the functions a line calls, "
               "Over goes past them, Out back to the line after the one that called it.")
        return (f'<div class="wk"><div class="wtip">{_esc(tip)}</div><div class="wg">'
                f'<div class="wtree">{self._tree()}</div><div class="wps">{panels}</div></div></div>')


@_draws(_Walk, "html")
def _walk_html(block: _Walk, report: _Report) -> str:
    if not block.startup and not block.steps:
        title = f"<h4>{_esc(block.title)}</h4>" if block.title else ""
        return title + f'<div class="more">{_esc(block.empty)}</div>'
    return _WalkHtml(block, report).html()


@_draws(_Walk, "text")
def _walk_lines(block: _Walk, report: _Report) -> list[str]:
    lines = _outline_lines(_Outline(block.startup, block.startup_title), report) if block.startup else []
    return lines + _outline_lines(_Outline(block.steps, block.title, block.empty), report)
