"""
The explorer window's widgets: the knowledge base field and a file's row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable

from ..._bedrock.helpers import parse_kb_ref
from ..._kit.fmt import _plural, human_age
from ..._kit.render import _prose
from ..._kit.text import _esc, _marked
from ..._kit.window import _Picker, _class_if, _row_button

from ..models import KBFile, KnowledgeBaseInfo
from .frame import _kb_tone

if TYPE_CHECKING:
    from .window import KBExplorer


class _Chooser(_Picker):
    """
    The explorer's knowledge base field: a button under its face (the name, ID and a status dot) that opens a list
    over the window, with a search box that finds a knowledge base by any part of its name, ID or description. Enter
    picks the first line, or hands what's typed to on_text when no line has it (an ID or ARN). Plain widgets and CSS:
    each line is a button under its text, so the whole line is the click target. While the list is open, the window's
    backdrop takes a click anywhere else and closes it.
    """

    def __init__(self, app: KBExplorer, *, on_pick: Callable[[str], None], on_text: Callable[[str], None]):
        super().__init__(app, app._w, "kbx", on_pick=on_pick, on_text=on_text, tooltip="Pick another knowledge base",
                         placeholder="Search by name, ID or description")
        self.choices: list[KnowledgeBaseInfo] = []
        self.value = ""  # the ID shown
        self.draw()

    def set_choices(self, kbs: Iterable[KnowledgeBaseInfo]) -> None:
        self.choices = sorted(kbs, key=lambda kb: (kb.name or kb.id).lower())
        self.draw()

    def _choice(self, kb_id: str) -> KnowledgeBaseInfo | None:
        return next((kb for kb in self.choices if kb.id == kb_id), None)

    def draw(self) -> None:
        kb = self._choice(self.value)
        name = ((kb.name if kb else "") or self.app.core.kb_name(self.value)) if self.value else ""
        tone = _kb_tone(kb.status) if kb else ""
        shown = f"<b>{_esc(name)}</b>" if name else '<b style="opacity:.55;font-weight:500">Pick a knowledge base</b>'
        ident = f'<span class="fxi">{_esc(self.value)}</span>' if self.value and self.value != name else ""
        self.app._set(self.face, '<div class="fx"><div class="fxl">Knowledge base</div><div class="fxv">'
                                 + (f'<span class="dot {tone}"></span>' if tone else "") + shown + ident
                                 + '</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def _query(self, text: str) -> str:
        try:
            kind, value = parse_kb_ref(text)
        except ValueError:
            return text
        return value if kind == "arn" else text

    def _fields(self, kb: KnowledgeBaseInfo) -> tuple[str, ...]:
        return (kb.name, kb.id, kb.description, kb.status)

    def _key(self, kb: KnowledgeBaseInfo) -> str:
        return kb.id

    def _row(self, kb: KnowledgeBaseInfo, words: list[str]) -> Any:
        row, button, text = self._line(kb.id)
        _class_if(row, "kbx-on", kb.id == self.value)
        status = "" if kb.status == "ACTIVE" else kb.status.lower().replace("_", " ")
        note = " · ".join(p for p in (status, kb.description, f"changed {human_age(kb.updated)}" if kb.updated else "")
                          if p)
        tip = " · ".join(p for p in (kb.name, kb.id, note) if p)
        if button.tooltip != tip:
            button.tooltip = tip
        self.app._set(text, f'<div class="op"><span class="dot {_kb_tone(kb.status)}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(kb.name or kb.id, words)}</b><span class="opi">'
                            f"{_marked(kb.id, words)}</span></div>"
                            + (f'<div class="opn">{_marked(note, words)}</div>' if note else "") + "</div></div>")
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()
        words = text.split()
        self.list.children = [self._row(kb, words) for kb in found[:60]]
        self.shown = [kb.id for kb in found[:60]]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level, line = ("warn" if self.problem else ""), self.problem or "There are no knowledge bases to pick from."
        elif text and not found:
            level, line = "warn", f"No knowledge base matches {text!r}. Enter tries it as an ID or ARN."
        else:
            line = (f"{len(found):,} of {len(self.choices):,}" if text else _plural(len(self.choices), "knowledge base"))
            line += f" in {self.app.core.region}" + (" · Enter picks the first" if text else "")
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, kb_id: str) -> None:
        self.close()
        if kb_id != self.value:
            self.on_pick(kb_id)


class _FileRow:
    """
    One reusable line of the file list: a full-width button under its face (the file type, with the state's dot on
    it, the name, its folder and state, its size and age), so the whole line is the click target.
    """

    def __init__(self, app: KBExplorer):
        self.file: KBFile | None = None
        self.box, self.button, self.face = _row_button(app._w, ("kbx-row", "kbx-row-b", "kbx-row-t"),
                                                       app._safely(lambda _button: app._clicked_row(self)))
