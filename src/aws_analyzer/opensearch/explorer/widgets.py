"""
The explorer window's widgets: the domain or collection field and a reusable line of a list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

from ..models import Collection, Domain
from .frame import _store_note, _store_tone

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_LIST_MOST = 60


class _StoreField(_Picker):
    """
    The explorer's field for the domain or collection it shows: a button under its face (the name, its kind and a
    dot for how it is) that opens a list over the window, with a search box that finds one by any part of its name,
    collection ID, engine or description. Enter picks the first line, or hands what's typed to on_text when no line
    has it (an ARN, an endpoint URL, a collection ID).
    """

    def __init__(self, app: OpenSearchExplorer, *, on_pick: Callable[[str], None], on_text: Callable[[str], None]):
        super().__init__(app, app._w, "osx", on_pick=on_pick, on_text=on_text,
                         tooltip="Pick another domain or collection",
                         placeholder="Search by name, collection ID or engine")
        self.explorer = app
        self.choices: list[Domain | Collection] = []
        self.value = ""
        self.draw()

    def set_choices(self, stores: list[Domain | Collection]) -> None:
        self.choices = sorted(stores, key=lambda s: s.name.lower())
        self.draw()

    def _choice(self, name: str) -> Domain | Collection | None:
        return next((s for s in self.choices if s.name == name), None)

    def draw(self) -> None:
        store = self._choice(self.value)
        if store is not None:
            tone = _store_tone(store, self.explorer._warning_count(store))
            shown = f'<span class="dot {tone}"></span><b>{_esc(store.name)}</b>'
            kind = "domain" if isinstance(store, Domain) else "collection"
            shown += f'<span class="fxi">{kind}</span>'
        elif self.value:
            shown = f"<b>{_esc(self.value)}</b>"
        else:
            shown = '<b style="opacity:.55;font-weight:500">Pick a domain or collection</b>'
        self.app._set(self.face, f'<div class="fx"><div class="fxl">Domain or collection</div><div class="fxv">'
                                 f'{shown}</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def _fields(self, store: Domain | Collection) -> tuple[str, ...]:
        if isinstance(store, Domain):
            return (store.name, store.engine, store.version, store.instance_type)
        return (store.name, store.id, store.description, store.kind)

    def _key(self, store: Domain | Collection) -> str:
        return store.name

    def _row(self, store: Domain | Collection, words: list[str]) -> Any:
        row, button, text = self._line(store.name)
        _class_if(row, "osx-on", store.name == self.value)
        if button.tooltip != store.name:
            button.tooltip = store.name
        tone = _store_tone(store, self.explorer._warning_count(store))
        self.app._set(text, f'<div class="op"><span class="dot {tone}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(store.name, words)}</b></div>'
                            f'<div class="opn">{_esc(_store_note(store))}</div></div></div>')
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()[:_LIST_MOST]
        self.list.children = [self._row(s, text.split()) for s in found]
        self.shown = [s.name for s in found]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level = "warn" if self.problem else ""
            line = self.problem or "There are no domains or collections to pick from."
        elif text and not found:
            level, line = "warn", f"Nothing matches {text!r}. Enter tries it as a collection ID, ARN or endpoint URL."
        else:
            everything = f"{len(self.choices):,} domains and collections"
            line = f"{len(found):,} of {len(self.choices):,}" if text else everything
            line += f" in {self.explorer.core.region}"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, name: str) -> None:
        self.close()
        if name != self.value:
            self.on_pick(name)


class _Line:
    """
    One reusable line of a list: a full-width button under its face, so the whole line is the click target. key is
    what the line stands for (a domain or collection's name, an index's name, a document's ID), set when it's drawn.
    """

    def __init__(self, app: OpenSearchExplorer, on_click: Callable[[_Line], None]):
        self.key = ""
        self.box, self.button, self.face = _row_button(app._w, ("osx-row", "osx-row-b", "osx-row-t"),
                                                       app._safely(lambda _button: on_click(self)))
