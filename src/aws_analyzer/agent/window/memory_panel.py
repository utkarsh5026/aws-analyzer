"""
The chat window's memory panel (🧠 memory in its head): what the agent remembers about the account between
conversations (AgentAnalyzer(memory=True)), shown and cleared in the window instead of in code. Each note is a line
with the day it was learned and a ✕ that forgets it; a line above says how many notes there are, how full the
memory is and which account they're about, and a note over STALE_DAYS old is marked. Forget all asks twice. With
memory off, the panel says how to turn it on. The user's notes= file is named under the notes.

It reads the notes when it opens and again after each answer while it's open (the agent may have kept a new one), on
the event loop, from the local SQLite file: no AWS call. _MEMORY_CSS is its look, which the window adds to its
stylesheet.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ..._kit.fmt import _plural, human_size
from ..._kit.render import _prose
from ..._kit.text import _esc
from ..._kit.window import _class_if
from ..memory import STALE_DAYS, MemoryState, Note, is_notes_file

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer
    from .chat import _AgentChat

_FORGET_ALL = "forget all"
_MEMORY_OFF = ("The agent's memory is off: each conversation starts from nothing but your notes=. "
               "AgentView(memory=True) lets it keep notes about your account (where a function runs, what a bucket "
               "is for) between conversations, in a SQLite file on this machine.")
_NOTHING_NOTED = ("Nothing noted yet: the agent keeps a note when it finds a lasting fact about your account, so the "
                  "next conversation doesn't look it up again.")

_MEMORY_CSS = """\
.agc-app.agc-app .agc-mem{gap:4px;padding:10px 16px 12px;border-bottom:1px solid var(--ag-line)}
.agc-app.agc-app .agc-mem>*{margin:0}
.agc-app .agc-mem .ag-mh{font-size:12px;line-height:1.55;color:var(--ag-dim)}
.agc-app .agc-mem .ag-mh b{color:inherit;font-weight:600}
.agc-app .agc-mem .ag-mh .warn{color:var(--ag-warn)}
.agc-app.agc-app .agc-mem .agc-notes{gap:0;max-height:260px;overflow:hidden auto}
.agc-app.agc-app .agc-mem .agc-note{align-items:center;gap:10px;padding:3px 0;border-top:1px dashed var(--ag-line)}
.agc-app.agc-app .agc-mem .agc-note>*{margin:0}
.agc-app.agc-app .agc-mem .agc-note:first-child{border-top:0}
.agc-app .agc-mem .ag-nt{font-size:12.5px;line-height:1.5;overflow-wrap:anywhere}
.agc-app .agc-mem .ag-nt .ag-nd{margin-left:8px;font-size:11.5px;color:var(--ag-comment);white-space:nowrap}
.agc-app .agc-mem .ag-nt .ag-nd.warn{color:var(--ag-warn)}
.agc-app.agc-app .agc-mem .agc-x{height:22px;min-width:22px;line-height:20px;padding:0 6px;margin:0;border-radius:5px;font-size:12px;background:transparent;border:1px solid transparent;box-shadow:none;color:var(--ag-comment)}
.agc-app.agc-app .agc-mem .agc-x:hover:enabled{border-color:var(--ag-warn);color:var(--ag-warn)}
.agc-app.agc-app .agc-mem .agc-acts{gap:8px;margin-top:6px}
.agc-app.agc-app .agc-mem .agc-sure{border-color:var(--ag-warn);color:var(--ag-warn)}"""


def note_age(note: Note, today: date) -> tuple[str, bool]:
    """
    When a note was learned as its line says it ('today', '3d ago', 'not dated'), and whether it's old enough to check.
    """
    days = note.age_days(today)
    if days is None:
        return "not dated", False
    return ("today" if days <= 0 else f"{days:,}d ago"), days >= STALE_DAYS


def memory_head_html(state: MemoryState, today: date) -> str:
    """
    The line above the notes: how many, how full, which account, and how many are old enough to check.
    """
    stale = sum(1 for note in state.notes if note_age(note, today)[1])
    parts = [f"<b>{_plural(len(state.notes), 'note')}</b>", f"{human_size(state.size)} of {human_size(state.limit)}",
             f"account {_esc(state.account)}"]
    if stale:
        which = "it" if stale == 1 else "them"
        parts.append(f'<span class="warn">{_plural(stale, "note")} over {STALE_DAYS} days old: check {which}</span>')
    return f'<div class="ag-mh">{" · ".join(parts)}</div>'


def note_html(note: Note, today: date) -> str:
    """
    One note's line: its text and when it was learned (marked when it's old enough to check).
    """
    age, is_stale = note_age(note, today)
    age_class = "ag-nd warn" if is_stale else "ag-nd"
    return f'<div class="ag-nt">{_esc(note.text)}<span class="{age_class}">{_esc(age)}</span></div>'


class _MemoryPanel:
    """
    The agent's notes, under the window's head while open: the line above them (head), a line per note with its ✕
    (notes), the line naming the user's notes= file (file_line), and Forget all and close.

        sure    Forget all was clicked once, and the next click forgets every note
    """

    def __init__(self, window: _AgentChat, widgets: Any):
        self.window, self.w = window, widgets
        self.sure = False
        self.root = self._build()

    @property
    def core(self) -> AgentAnalyzer:
        return self.window.view.core

    @property
    def is_open(self) -> bool:
        return self.root.layout.display != "none"

    def _build(self) -> Any:
        w, layout = self.w, self.w.Layout
        self.head = w.HTML(layout=layout(width="100%"))
        self.notes = w.VBox(layout=layout(width="100%"))
        self.notes.add_class("agc-notes")
        self.file_line = w.HTML(layout=layout(width="100%"))
        self.forget_all_button = self._button(_FORGET_ALL, "Forget every note about this account",
                                              self._forget_all, "agc-new")
        close = self._button("close", "Close the memory panel", self.close, "agc-new")
        self.actions = w.HBox([self.forget_all_button, close])
        self.actions.add_class("agc-acts")
        root = w.VBox([self.head, self.notes, self.file_line, self.actions],
                      layout=layout(display="none", width="100%"))
        root.add_class("agc-mem")
        return root

    def _button(self, label: str, tooltip: str, on_click: Any, css: str) -> Any:
        button = self.w.Button(description=label, tooltip=tooltip,
                               layout=self.w.Layout(width="auto", flex="0 0 auto"))
        button.add_class(css)
        button.on_click(self.window._safely(lambda _button: on_click()))
        return button

    def open(self) -> None:
        """
        Shows the notes as they are now.
        """
        self.root.layout.display = ""
        _class_if(self.window.memory_button, "agc-on", True)
        self.refresh()

    def close(self) -> None:
        self.root.layout.display = "none"
        _class_if(self.window.memory_button, "agc-on", False)
        self._unsure()

    def refresh(self) -> None:
        """
        Draws the notes again from the store (after an answer, or a forget).
        """
        core = self.core
        self.file_line.value = self._file_html()
        if core.memory_file is None:
            self.window._set(self.head, f'<div class="ag-mh">{_prose(_MEMORY_OFF)}</div>')
            self.notes.children = ()
            self.forget_all_button.layout.display = "none"
            return
        state = core.memories()
        today = core._today()
        self.forget_all_button.layout.display = "" if state.notes else "none"
        if not state.notes:
            self.window._set(self.head, f'<div class="ag-mh">{_prose(_NOTHING_NOTED)}</div>')
            self.notes.children = ()
            return
        self.window._set(self.head, memory_head_html(state, today))
        self.notes.children = tuple(self._note_row(note, today) for note in state.notes)

    def _file_html(self) -> str:
        """
        The line naming the user's notes= file, or '' when notes= holds the facts itself.
        """
        if not is_notes_file(self.core.notes):
            return ""
        return (f'<div class="ag-mh">Your notes: <code>{_esc(str(self.core.notes))}</code>, read for each new '
                "conversation; the agent never writes to it.</div>")

    def _note_row(self, note: Note, today: date) -> Any:
        w = self.w
        text = w.HTML(note_html(note, today), layout=w.Layout(flex="1 1 auto", min_width="0"))
        forget = self._button("✕", "Forget this note", lambda: self._forget(note), "agc-x")
        row = w.HBox([text, forget], layout=w.Layout(width="100%"))
        row.add_class("agc-note")
        return row

    def _forget(self, note: Note) -> None:
        if self.core.forget_note(note):
            self.window._set_status(f"Forgotten: {note.text}. The next conversation starts without it.", "ok")
        self._unsure()
        self.refresh()

    def _unsure(self) -> None:
        self.sure = False
        self.forget_all_button.description = _FORGET_ALL
        _class_if(self.forget_all_button, "agc-sure", False)

    def _forget_all(self) -> None:
        """
        The first click asks to click again; the second forgets every note about the account.
        """
        if not self.sure:
            count = len(self.core.memories().notes)
            self.sure = True
            self.forget_all_button.description = f"click again to forget {_plural(count, 'note')}"
            _class_if(self.forget_all_button, "agc-sure", True)
            return
        count = self.core.forget_all()
        self._unsure()
        self.refresh()
        self.window._set_status(f"Forgot {_plural(count, 'note')} about account {self.core.account()}: the next "
                                "conversation starts from nothing but your notes=.", "ok")
