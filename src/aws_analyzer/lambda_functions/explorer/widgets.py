"""
The window's widgets: the function field with its searchable list, and the clickable rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable

from ..._kit.fmt import _count, _plural
from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

from ..helpers import _pct, parse_function_ref
from ..models import CodeFrame, Function
from ..analysis.logs import LogRun

if TYPE_CHECKING:
    from .window import LambdaExplorer


class _FunctionField(_Picker):
    """
    The explorer's function field: a button under its face (the function's name, health dot and runtime) that opens
    a list over the window, with a search box that finds a function by any part of its name, description, runtime or
    region. Enter picks the first line, or hands what's typed to on_text when no line has it (an ARN, or a function the
    list doesn't hold). Plain widgets and CSS: each line is a button under its text, so the whole line is the click
    target. While the list is open, the window's backdrop takes a click anywhere else and closes it.
    """

    def __init__(self, app: LambdaExplorer, *, on_pick: Callable[[Function], None], on_text: Callable[[str], None]):
        super().__init__(app, app._w, "lmx", on_pick=on_pick, on_text=on_text, tooltip="Pick another function",
                         placeholder="Search by name, description, runtime or region")
        self.choices: list[Function] = []
        self.value = ""  # the ARN shown
        self.draw()

    def set_choices(self, functions: Iterable[Function]) -> None:
        self.choices = sorted(functions, key=lambda f: (f.name.lower(), f.region))
        self.draw()

    def _choice(self, arn: str) -> Function | None:
        return next((f for f in self.choices if f.arn == arn), None)

    def draw(self) -> None:
        fn = self._choice(self.value) or self.app.function
        if fn is not None and fn.arn != self.value:
            fn = None
        facts = self.app._facts.get(fn.arn) if fn is not None else None
        tone = facts.tone if facts is not None else ""
        shown = (f"<b>{_esc(fn.name)}</b>" if fn is not None
                 else '<b style="opacity:.55;font-weight:500">Pick a function</b>')
        runtime = (f'<span class="fxi">{_esc(fn.runtime or "container image")}'
                   + (f" · {_esc(fn.region)}" if self.app.multi else "") + "</span>") if fn is not None else ""
        self.app._set(self.face, '<div class="fx"><div class="fxl">Function</div><div class="fxv">'
                                 + (f'<span class="dot {tone}"></span>' if fn is not None else "") + shown + runtime
                                 + '</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def matches(self) -> list[Function]:
        """
        The functions the search box finds, best first; while it's empty, the ones you've opened first (most yours
        first), then the rest by name.
        """
        found = super().matches()
        if str(self.search.value or "").strip():
            return found
        rank = {arn: i for i, arn in enumerate(self.app._recent)}
        return sorted(found, key=lambda fn: rank.get(fn.arn, len(rank)))

    def _query(self, text: str) -> str:
        try:
            name, _, _ = parse_function_ref(text)
        except ValueError:
            return text
        return name if text.strip().startswith(("arn:", "http")) else text

    def _fields(self, fn: Function) -> tuple[str, ...]:
        return (fn.name, fn.description, fn.runtime, fn.region, fn.arn)

    def _key(self, fn: Function) -> str:
        return fn.arn

    def _row(self, fn: Function, words: list[str]) -> Any:
        row, button, text = self._line(fn.arn)
        _class_if(row, "lmx-on", fn.arn == self.value)
        facts = self.app._facts.get(fn.arn)
        m = facts.metrics if facts is not None else None
        numbers = (f"{_count(round(m.invocations))} calls" + (f", {_pct(m.error_rate)} failed" if m.errors else "")
                   if m is not None and m.invocations else "not called in 30 days" if m is not None else "")
        recent = self.app._recent_note(fn)
        note = " · ".join(p for p in (fn.description, fn.region if self.app.multi else "", numbers, recent) if p)
        tip = " · ".join(p for p in (fn.name, fn.runtime or "container image", note) if p)
        if button.tooltip != tip:
            button.tooltip = tip
        self.app._set(text, f'<div class="op"><span class="dot {facts.tone if facts else ""}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(fn.name, words)}</b><span class="opi">'
                            f'{_marked(fn.runtime or "container image", words)}</span></div>'
                            + (f'<div class="opn">{_marked(note, words)}</div>' if note else "") + "</div></div>")
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()
        words = text.split()
        self.list.children = [self._row(f, words) for f in found[:80]]
        self.shown = [f.arn for f in found[:80]]
        level, line = "", ""
        where = self.app._where()
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level, line = ("warn" if self.problem else ""), self.problem or f"There are no functions in {where}."
        elif text and not found:
            level, line = "warn", f"No function in {where} matches {text!r}. Enter looks it up by name or ARN."
        else:
            line = (f"{len(found):,} of {len(self.choices):,}" if text else _plural(len(self.choices), "function"))
            line += f" in {where}" + (" · Enter picks the first" if text else "")
            if len(found) > 80:
                line += " · type to narrow the list"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, arn: str) -> None:
        self.close()
        fn = self._choice(arn)
        if fn is not None and arn != self.value:
            self.on_pick(fn)

class _Row:
    """
    One reusable line of a list: a full-width button under its face (HTML), so the whole line is the click target,
    with an optional small action button on its right (the function list's Logs). `item` is what it shows.
    """

    def __init__(self, app: LambdaExplorer, on_click: Callable[[Any], None], *, short: bool = False,
                 action: tuple[str, str, Callable[[Any], None]] | None = None):
        w, layout = app._w, app._w.Layout
        self.item: Any = None
        self.action = None
        if action is not None:
            label, tip, act = action
            self.action = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            self.action.add_class("lmx-act")
            self.action.on_click(app._safely(lambda _button: act(self.item) if self.item is not None else None))
        self.box, self.button, self.face = _row_button(
            w, ("lmx-row", "lmx-row-b", "lmx-row-t"),
            app._safely(lambda _button: on_click(self.item) if self.item is not None else None),
            after=[self.action] if self.action is not None else [])
        if short:
            self.box.add_class("lmx-short")


class _RunRow:
    """
    One reusable run in the Logs tab: a full-width button under its face (its status, when it started, run time,
    memory, cold start, what failed, its request ID) that opens its lines below it, or folds them again. An open run
    whose traceback names a line of the function's own code has a button under its lines that opens that line in
    the Code tab (`frame`).
    """

    def __init__(self, app: LambdaExplorer):
        w, layout = app._w, app._w.Layout
        self.run: LogRun | None = None
        head, self.button, self.face = _row_button(w, ("lmx-run-h", "lmx-run-b", "lmx-run-t"),
                                                   app._safely(lambda _button: app._toggle_run(self)),
                                                   tooltip="Click to see its lines")
        self.body = w.HTML(layout=layout(width="100%", display="none"))
        self.frame: CodeFrame | None = None
        self.code_button = w.Button(layout=layout(width="auto", display="none", align_self="flex-start"))
        self.code_button.add_class("lmx-small")
        self.code_button.add_class("lmx-tocode")
        self.code_button.on_click(app._safely(lambda _button: app._open_code_at(self.frame) if self.frame else None))
        self.box = w.VBox([head, self.body, self.code_button], layout=layout(width="100%"))
        self.box.add_class("lmx-run")
