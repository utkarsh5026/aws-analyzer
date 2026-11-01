"""
The explorer window's widgets: the state machine field and a reusable line of a list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ..._kit.fmt import _plural
from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

from ..helpers import machine_name
from ..models import MachineNumbers, StateMachine

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_LIST_MOST = 60


def _machine_tone(numbers: MachineNumbers | None) -> str:
    if numbers is None:
        return ""
    if numbers.failed:
        return "bad"
    return "warn" if numbers.unsuccessful else "ok"


class _MachineField(_Picker):
    """
    The explorer's state machine field: a button under its face (the name, its type and a dot for how it's doing)
    that opens a list over the window, with a search box that finds a state machine by any part of its name or
    description. Enter picks the first line, or hands what's typed to on_text when no line has it (an ARN).
    """

    def __init__(self, app: StepFunctionsExplorer, *, on_pick: Callable[[str], None],
                 on_text: Callable[[str], None]):
        super().__init__(app, app._w, "sfx", on_pick=on_pick, on_text=on_text, tooltip="Pick another state machine",
                         placeholder="Search by name or description")
        self.explorer = app
        self.choices: list[StateMachine] = []
        self.value = ""
        self.draw()

    def set_choices(self, machines: list[StateMachine]) -> None:
        self.choices = sorted(machines, key=lambda m: m.name.lower())
        self.draw()

    def _choice(self, arn: str) -> StateMachine | None:
        return next((m for m in self.choices if m.arn == arn), None)

    def draw(self) -> None:
        machine = self._choice(self.value)
        name = machine.name if machine else machine_name(self.value)
        tone = _machine_tone(self.explorer.numbers.get(self.value))
        shown = f"<b>{_esc(name)}</b>" if name else '<b style="opacity:.55;font-weight:500">Pick a state machine</b>'
        kind = f'<span class="fxi">{_esc(machine.type)}</span>' if machine else ""
        self.app._set(self.face, '<div class="fx"><div class="fxl">State machine</div><div class="fxv">'
                                 + (f'<span class="dot {tone}"></span>' if tone else "") + shown + kind
                                 + '</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def _fields(self, machine: StateMachine) -> tuple[str, ...]:
        return (machine.name, machine.description, machine.type, machine.arn)

    def _key(self, machine: StateMachine) -> str:
        return machine.arn

    def _row(self, machine: StateMachine, words: list[str]) -> Any:
        row, button, text = self._line(machine.arn)
        _class_if(row, "sfx-on", machine.arn == self.value)
        numbers = self.explorer.numbers.get(machine.arn)
        note = machine.type.title()
        if numbers is not None:
            note += f" · {_plural(numbers.started, 'execution')}, {numbers.unsuccessful:,} didn't succeed"
        if button.tooltip != machine.arn:
            button.tooltip = machine.arn
        self.app._set(text, f'<div class="op"><span class="dot {_machine_tone(numbers)}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(machine.name, words)}</b></div>'
                            f'<div class="opn">{_esc(note)}</div></div></div>')
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()[:_LIST_MOST]
        self.list.children = [self._row(m, text.split()) for m in found]
        self.shown = [m.arn for m in found]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level, line = ("warn" if self.problem else ""), self.problem or "There are no state machines to pick from."
        elif text and not found:
            level, line = "warn", f"No state machine matches {text!r}. Enter tries it as an ARN."
        else:
            line = f"{len(found):,} of {len(self.choices):,}" if text else _plural(len(self.choices), "state machine")
            line += f" in {self.explorer.core.region}"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, arn: str) -> None:
        self.close()
        if arn != self.value:
            self.on_pick(arn)


class _Line:
    """
    One reusable line of a list: a full-width button under its face, so the whole line is the click target. key is
    what the line stands for (an ARN), set when it's drawn.
    """

    def __init__(self, app: StepFunctionsExplorer, on_click: Callable[[_Line], None]):
        self.key = ""
        self.box, self.button, self.face = _row_button(app._w, ("sfx-row", "sfx-row-b", "sfx-row-t"),
                                                       app._safely(lambda _button: on_click(self)))
