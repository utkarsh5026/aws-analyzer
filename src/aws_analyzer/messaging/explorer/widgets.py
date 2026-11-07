"""
The explorer window's widgets: the field for the queue, topic or rule it shows, and a reusable line of a list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

if TYPE_CHECKING:
    from .items import _Item
    from .window import MessagingExplorer


_LIST_MOST = 60

_KIND_WORDS = {"queue": "queue", "dlq": "dead-letter queue", "topic": "topic", "rule": "rule"}


class _ItemField(_Picker):
    """
    The explorer's field for the queue, topic or rule it shows: a button under its face (the name, its kind and a
    dot for how it is) that opens a list over the window, with a search box that finds one by any part of its name or
    what it is. Enter picks the first line, or hands what's typed to on_text when no line has it (a queue URL, an
    ARN, 'bus/rule').
    """

    def __init__(self, app: MessagingExplorer, *, on_pick: Callable[[str], None], on_text: Callable[[str], None]):
        super().__init__(app, app._w, "mgx", on_pick=on_pick, on_text=on_text,
                         tooltip="Pick another queue, topic or rule",
                         placeholder="Search a queue, topic or rule, or paste a URL or ARN")
        self.explorer = app
        self.choices: list[_Item] = []
        self.value = ""
        self.draw()

    def set_choices(self, items: list[_Item]) -> None:
        self.choices = sorted(items, key=lambda item: (item.opens, item.name.lower()))
        self.draw()

    def _choice(self, key: str) -> _Item | None:
        return next((item for item in self.choices if item.key == key), None)

    def draw(self) -> None:
        item = self._choice(self.value)
        if item is not None:
            shown = (f'<span class="dot {item.tone}"></span><b>{_esc(item.name)}</b>'
                     f'<span class="fxi">{_KIND_WORDS[item.kind]}</span>')
        else:
            shown = '<b style="opacity:.55;font-weight:500">Pick a queue, topic or rule</b>'
        self.app._set(self.face, f'<div class="fx"><div class="fxl">Queue, topic or rule</div><div class="fxv">'
                                 f'{shown}</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def _fields(self, item: _Item) -> tuple[str, ...]:
        return (item.name, _KIND_WORDS[item.kind], item.note)

    def _key(self, item: _Item) -> str:
        return item.key

    def _row(self, item: _Item, words: list[str]) -> Any:
        row, button, text = self._line(item.key)
        _class_if(row, "mgx-on", item.key == self.value)
        if button.tooltip != item.name:
            button.tooltip = item.name
        self.app._set(text, f'<div class="op"><span class="dot {item.tone}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(item.name, words)}</b><span class="fxi">'
                            f'{_KIND_WORDS[item.kind]}</span></div><div class="opn">{_esc(item.note)}</div></div>'
                            "</div>")
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()[:_LIST_MOST]
        self.list.children = [self._row(item, text.split()) for item in found]
        self.shown = [item.key for item in found]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level = "warn" if self.problem else ""
            line = self.problem or "There are no queues, topics or rules to pick from."
        elif text and not found:
            level, line = "warn", f"Nothing matches {text!r}. Enter tries it as a queue URL, an ARN or 'bus/rule'."
        else:
            everything = f"{len(self.choices):,} queues, topics and rules"
            line = f"{len(found):,} of {len(self.choices):,}" if text else everything
            line += f" in {self.explorer.core.region}"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, key: str) -> None:
        self.close()
        if key != self.value:
            self.on_pick(key)


class _Line:
    """
    One reusable line of a list: a full-width button under its face, so the whole line is the click target. key is
    what the line stands for (a queue's, topic's or rule's key, a hop's), set when it's drawn.
    """

    def __init__(self, app: MessagingExplorer, on_click: Callable[[_Line], None]):
        self.key = ""
        self.box, self.button, self.face = _row_button(app._w, ("mgx-row", "mgx-row-b", "mgx-row-t"),
                                                       app._safely(lambda _button: on_click(self)))
