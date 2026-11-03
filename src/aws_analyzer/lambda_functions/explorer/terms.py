"""
The Logs tab's search terms: when the search box joins words with AND, OR and NOT, a row under it shows them as
chips. The word between two chips is a button that flips every joiner (AND to OR and back), a click on a chip leaves
its word out (struck through) or brings it back, and its ✕ takes it away. Each click writes the search back into the
box, which stays the one record of it, and the box then filters the runs as typing does. A search grouped in a way
chips can't draw ('(a OR b) AND (c OR d)') is shown written out instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..._kit.text import _esc
from ..._kit.window import _class_if

from ..analysis.word_search import AND, SearchTerms
from .logs import _words_of

_TERMS_CSS = """<style>
.lmx-app.lmx-app .lmx-terms{flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 8px}
.lmx-app.lmx-app .lmx-terms>*{margin:0}
.lmx-app .lmx-terms .tk{font-size:12px;opacity:.7;margin-right:2px}
.lmx-app .lmx-terms .th{font-size:12px;opacity:.6;margin-left:4px}
.lmx-app .lmx-terms .tw{font-size:12px;color:var(--lx-ink-warn)}
.lmx-app .lmx-terms code{font-weight:600}
.lmx-app.lmx-app button.lmx-term{height:26px;padding:0 4px 0 10px;border-radius:999px 0 0 999px;border-right:0}
.lmx-app.lmx-app button.lmx-term::before{display:none}
.lmx-app.lmx-app button.lmx-term.lmx-term-out{color:var(--lx-ink-bad);border-color:var(--lx-bad);
text-decoration:line-through;background:var(--lx-hit)}
.lmx-app.lmx-app button.lmx-term-x{height:26px;width:24px;min-width:24px;padding:0 6px 0 2px;margin-left:-6px;
border:1px solid var(--lx-line);border-left:0;border-radius:0 999px 999px 0;background:transparent;opacity:.75}
.lmx-app.lmx-app button.lmx-term-x.lmx-term-out{border-color:var(--lx-bad);background:var(--lx-hit)}
.lmx-app.lmx-app button.lmx-term-x:hover:enabled{opacity:1;color:var(--lx-ink-bad)}
.lmx-app.lmx-app button.lmx-join{width:auto;height:26px;line-height:24px;padding:0 7px;border-radius:6px;border:1px dashed
var(--lx-line-2);background:transparent;color:var(--lx-accent-2);font-size:11px;font-weight:700;letter-spacing:.04em}
.lmx-app.lmx-app button.lmx-join:hover:enabled{border-style:solid;border-color:var(--lx-accent-2)}
.lmx-app.lmx-app button.lmx-join:disabled{border-color:transparent;color:inherit;opacity:.55;cursor:default}
</style>"""

_FLIP_TIP = {AND: "Every word: click to show runs with any of them (OR)",
             "OR": "Any word: click to show only runs with all of them (AND)"}

_LEAD = '<span class="tk">Matching</span>'

_HINT = '<span class="th">click AND / OR to flip it · click a word to leave it out</span>'

_GROUPED_HINT = '<span class="th">grouped in brackets: change it in the search box</span>'


@dataclass
class _TermSlot:
    """
    One chip's widgets, pooled and reused by place: the joiner before it, the chip, and its ✕.
    """

    joiner: Any
    chip: Any
    drop: Any


class _SearchTerms:
    """
    The Logs tab's row of search terms, in LambdaExplorer: chips for words joined with AND, OR and NOT.
    """

    def _terms_widgets(self) -> Any:
        """
        The row under the search bar, hidden until the box joins words with AND, OR or NOT.
        """
        w, layout = self._w, self._w.Layout
        self._term_slots: list[_TermSlot] = []
        self._term_keys: list[str] = []
        self._terms_shown: SearchTerms | None = None
        self._terms_drawn: str | None = None
        self._term_joiner = AND
        self.terms_lead = w.HTML(value=_LEAD, layout=layout(flex="0 0 auto"))
        self.terms_note = w.HTML(layout=layout(flex="1 1 auto", width="auto", min_width="0"))
        self.terms_row = w.HBox([self.terms_lead], layout=layout(width="100%", display="none"))
        self.terms_row.add_class("lmx-terms")
        return self.terms_row

    def _term_slot(self, place: int) -> _TermSlot:
        while len(self._term_slots) <= place:
            self._term_slots.append(self._new_slot(len(self._term_slots)))
        return self._term_slots[place]

    def _new_slot(self, place: int) -> _TermSlot:
        w, layout = self._w, self._w.Layout
        joiner = w.Button(layout=layout(width="auto", flex="0 0 auto"))
        joiner.add_class("lmx-join")
        joiner.on_click(self._safely(lambda _button: self._flip_terms()))
        chip = w.Button(layout=layout(width="auto", flex="0 0 auto"))
        for name in ("lmx-chip", "lmx-term"):
            chip.add_class(name)
        chip.on_click(self._safely(lambda _button: self._toggle_term(place)))
        drop = w.Button(description="✕", layout=layout(width="auto", flex="0 0 auto"))
        drop.add_class("lmx-term-x")
        drop.on_click(self._safely(lambda _button: self._drop_term(place)))
        return _TermSlot(joiner, chip, drop)

    def _draw_terms(self) -> None:
        """
        Draws the row for what the box holds now; nothing changes while the box doesn't.
        """
        text = self._log_query
        if text == self._terms_drawn:
            return
        self._terms_drawn = text
        words, problem = (None, "") if self._asks() else _words_of(text)
        self._terms_shown = self._with_joiner(words.terms()) if words is not None else None
        if problem:
            self._show_terms([self.terms_note], f'<span class="tw">{_esc(problem)}</span>')
        elif words is None:
            self.terms_row.layout.display = "none"
        elif self._terms_shown is None:
            self._show_terms([self.terms_lead, self.terms_note], f"<code>{_esc(words.written())}</code>{_GROUPED_HINT}")
        else:
            self._show_terms([self.terms_lead, *self._chip_widgets(self._terms_shown), self.terms_note], _HINT)

    def _with_joiner(self, terms: SearchTerms | None) -> SearchTerms | None:
        """
        The terms with the joiner last picked while one word or none is wanted, so a word left out and brought back
        joins the others as it did before.
        """
        if terms is None:
            return None
        if len(terms.wanted) > 1:
            self._term_joiner = terms.joiner
            return terms
        return SearchTerms(self._term_joiner, terms.wanted, terms.left_out)

    def _show_terms(self, children: list[Any], note: str) -> None:
        self._set(self.terms_note, note)
        if tuple(self.terms_row.children) != tuple(children):
            self.terms_row.children = tuple(children)
        self.terms_row.layout.display = ""

    def _chip_widgets(self, terms: SearchTerms) -> list[Any]:
        """
        The joiners, chips and ✕s for the terms, in order: the wanted words joined by AND or OR (a button that
        flips them), then the words left out, each after an AND.
        """
        self._term_keys = [*terms.wanted, *terms.left_out]
        shown: list[Any] = []
        for place, phrase in enumerate(self._term_keys):
            slot = self._term_slot(place)
            left_out = place >= len(terms.wanted)
            if place:
                flips = not left_out
                self._label(slot.joiner, terms.joiner if flips else AND,
                            _FLIP_TIP[terms.joiner] if flips else "A word left out: runs that have it don't show")
                slot.joiner.disabled = not flips
                shown.append(slot.joiner)
            self._label(slot.chip, phrase, f"Click to look for {phrase!r} again" if left_out
                        else f"Click to leave out the runs with {phrase!r}")
            self._label(slot.drop, "✕", f"Take {phrase!r} out of the search")
            for widget in (slot.chip, slot.drop):
                _class_if(widget, "lmx-term-out", left_out)
            shown += [slot.chip, slot.drop]
        return shown

    @staticmethod
    def _label(button: Any, description: str, tooltip: str) -> None:
        if button.description != description:
            button.description = description
        if button.tooltip != tooltip:
            button.tooltip = tooltip

    def _write_terms(self, terms: SearchTerms) -> None:
        """
        Puts the changed terms in the search box, which filters the runs read as typing does.
        """
        self.log_find.value = terms.text()

    def _flip_terms(self) -> None:
        if self._terms_shown is not None:
            self._write_terms(self._terms_shown.flipped())

    def _toggle_term(self, place: int) -> None:
        if self._terms_shown is not None and place < len(self._term_keys):
            self._write_terms(self._terms_shown.toggled(self._term_keys[place]))

    def _drop_term(self, place: int) -> None:
        if self._terms_shown is not None and place < len(self._term_keys):
            self._write_terms(self._terms_shown.without(self._term_keys[place]))
