"""
The explorer window's widgets: the API field and a reusable line of a list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

from ..models import Api
from .frame import _api_tone

if TYPE_CHECKING:
    from .window import APIGatewayExplorer


_LIST_MOST = 60


class _ApiField(_Picker):
    """
    The explorer's API field: a button under its face (the name, its kind and a dot for how it's doing) that opens a
    list over the window, with a search box that finds an API by any part of its name, ID or description. Enter picks
    the first line, or hands what's typed to on_text when no line has it (an invoke URL, a console link).
    """

    def __init__(self, app: APIGatewayExplorer, *, on_pick: Callable[[str], None], on_text: Callable[[str], None]):
        super().__init__(app, app._w, "agx", on_pick=on_pick, on_text=on_text, tooltip="Pick another API",
                         placeholder="Search by name, ID or description")
        self.explorer = app
        self.choices: list[Api] = []
        self.value = ""
        self.draw()

    def set_choices(self, apis: list[Api]) -> None:
        self.choices = sorted(apis, key=lambda a: a.name.lower())
        self.draw()

    def _choice(self, api_id: str) -> Api | None:
        return next((a for a in self.choices if a.id == api_id), None)

    def draw(self) -> None:
        api = self._choice(self.value)
        tone = _api_tone(self.explorer.numbers.get(self.value))
        shown = f"<b>{_esc(api.name)}</b>" if api else '<b style="opacity:.55;font-weight:500">Pick an API</b>'
        kind = f'<span class="fxi">{_esc(api.kind_label)}</span>' if api else ""
        dot = f'<span class="dot {tone}"></span>' if tone else ""
        self.app._set(self.face, f'<div class="fx"><div class="fxl">API</div><div class="fxv">{dot}{shown}{kind}'
                                 '</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def _fields(self, api: Api) -> tuple[str, ...]:
        return (api.name, api.id, api.description, api.kind_label)

    def _key(self, api: Api) -> str:
        return api.id

    def _row(self, api: Api, words: list[str]) -> Any:
        row, button, text = self._line(api.id)
        _class_if(row, "agx-on", api.id == self.value)
        numbers = self.explorer.numbers.get(api.id)
        note = f"{api.kind_label} · {api.id}"
        if numbers is not None:
            note += f" · {numbers.requests:,} requests, {numbers.rate_5xx:.1%} 5xx"
        if button.tooltip != api.id:
            button.tooltip = api.id
        self.app._set(text, f'<div class="op"><span class="dot {_api_tone(numbers)}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(api.name, words)}</b></div>'
                            f'<div class="opn">{_esc(note)}</div></div></div>')
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()[:_LIST_MOST]
        self.list.children = [self._row(a, text.split()) for a in found]
        self.shown = [a.id for a in found]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level, line = ("warn" if self.problem else ""), self.problem or "There are no APIs to pick from."
        elif text and not found:
            level, line = "warn", f"No API matches {text!r}. Enter tries it as an ID or invoke URL."
        else:
            line = f"{len(found):,} of {len(self.choices):,}" if text else f"{len(self.choices):,} APIs"
            line += f" in {self.explorer.core.region}"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, api_id: str) -> None:
        self.close()
        if api_id != self.value:
            self.on_pick(api_id)


class _Line:
    """
    One reusable line of a list: a full-width button under its face, so the whole line is the click target. key is
    what the line stands for (an API ID, a route key, a request ID), set when it's drawn.
    """

    def __init__(self, app: APIGatewayExplorer, on_click: Callable[[_Line], None]):
        self.key = ""
        self.box, self.button, self.face = _row_button(app._w, ("agx-row", "agx-row-b", "agx-row-t"),
                                                       app._safely(lambda _button: on_click(self)))
