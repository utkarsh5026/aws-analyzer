"""
The chat window's chats panel (🗂 chats in its head): the conversations saved on this machine
(AgentAnalyzer(chats=True)), each a line with its title, how many questions it holds and when it was last asked in. A
click on a line opens that chat in the window, and the next question carries it on; its ✕ deletes it, on a second
click. Above them, the open chat's title, with a box to rename it. With chats off, the panel says how to turn them on.

It reads the chats when it opens and again after each answer while it's open, from the local SQLite file: no AWS
call. _CHATS_CSS is its look, which the window adds to its stylesheet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.fmt import _fmt_dt, _plural
from ..._kit.render import _prose
from ..._kit.text import _esc
from ..._kit.window import _class_if, _row_button
from ..chats import ChatSummary

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer
    from .chat import _AgentChat

_CHATS_OFF = ("Chats aren't saved: each conversation ends with the kernel. AgentView(chats=True) keeps every one on "
              "this machine, titled from its first question, so you can open it again and carry on, even after a "
              "restart.")
_NOTHING_SAVED = ("No chats saved yet: a conversation is saved, with its first question as its title, once that "
                  "question is answered.")
_NOT_SAVED_YET = "This chat is saved once its first question is answered"

_CHATS_CSS = """\
.agc-app.agc-app .agc-chats{gap:4px;padding:10px 16px 12px;border-bottom:1px solid var(--ag-line)}
.agc-app.agc-app .agc-chats>*{margin:0}
.agc-app .agc-chats .ag-mh{font-size:12px;line-height:1.55;color:var(--ag-dim)}
.agc-app .agc-chats .ag-mh b{color:inherit;font-weight:600}
.agc-app.agc-app .agc-chats .agc-rename{gap:8px;align-items:center}
.agc-app.agc-app .agc-chats .agc-rename>*{margin:0}
.agc-app.agc-app .agc-chats .agc-saved{gap:0;max-height:260px;overflow:hidden auto}
.agc-app.agc-app .agc-chats .agc-chat{align-items:center;gap:6px;border-top:1px dashed var(--ag-line)}
.agc-app.agc-app .agc-chats .agc-chat:first-child{border-top:0}
.agc-app.agc-app .agc-chats .agc-chat>*{margin:0}
.agc-app .agc-chats .ag-cl{display:flex;align-items:baseline;gap:10px;padding:4px 6px;font-size:12.5px;line-height:1.5;min-width:0}
.agc-app .agc-chats .ag-cl .ag-ctl{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agc-app .agc-chats .ag-cl .ag-cm{flex:0 0 auto;font-size:11.5px;color:var(--ag-comment);white-space:nowrap}
.agc-app .agc-chats .ag-cl.ag-open .ag-ctl{font-weight:600;color:var(--ag-accent)}
.agc-app.agc-app .agc-chats .agc-x{height:22px;min-width:22px;line-height:20px;padding:0 6px;margin:0;border-radius:5px;font-size:12px;background:transparent;border:1px solid transparent;box-shadow:none;color:var(--ag-comment)}
.agc-app.agc-app .agc-chats .agc-x:hover:enabled{border-color:var(--ag-warn);color:var(--ag-warn)}
.agc-app.agc-app .agc-chats .agc-sure{border-color:var(--ag-warn);color:var(--ag-warn)}
.agc-app.agc-app .agc-chats .agc-acts{gap:8px;margin-top:6px}"""


def chat_line_html(chat: ChatSummary, *, is_open: bool) -> str:
    """
    One saved chat's line: its title (marked when it's the chat open in the window), how many questions it holds and
    when it was last asked in.
    """
    line_class = "ag-cl ag-open" if is_open else "ag-cl"
    when = f"{_plural(chat.questions, 'question')} · {_fmt_dt(chat.updated)}"
    opened = " · open" if is_open else ""
    return (f'<div class="{line_class}"><span class="ag-ctl" title="{_esc(chat.title)}">{_esc(chat.title)}</span>'
            f'<span class="ag-cm">{_esc(when)}{opened}</span></div>')


class _ChatsPanel:
    """
    The saved chats, under the window's head while open: the open chat's title and a box to rename it (head,
    rename_box), a line per saved chat with its ✕ (saved), and close.

        sure    the chat whose ✕ was clicked once: the next click on it deletes it
    """

    def __init__(self, window: _AgentChat, widgets: Any):
        self.window, self.w = window, widgets
        self.sure: str | None = None
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
        self.title_box = w.Text(placeholder="a title for this chat", continuous_update=True,
                                layout=layout(flex="1 1 auto", width="auto", max_width="420px"))
        self.title_box.on_msg(self.window._on_enter(self._rename))
        self.rename_button = self._button("rename", "Give the open chat this title", self._rename, "agc-new")
        self.rename_row = w.HBox([self.title_box, self.rename_button], layout=layout(width="100%"))
        self.rename_row.add_class("agc-rename")
        self.saved = w.VBox(layout=layout(width="100%"))
        self.saved.add_class("agc-saved")
        close = self._button("close", "Close the chats panel", self.close, "agc-new")
        actions = w.HBox([close])
        actions.add_class("agc-acts")
        root = w.VBox([self.head, self.rename_row, self.saved, actions], layout=layout(display="none", width="100%"))
        root.add_class("agc-chats")
        return root

    def _button(self, label: str, tooltip: str, on_click: Any, css: str) -> Any:
        button = self.w.Button(description=label, tooltip=tooltip,
                               layout=self.w.Layout(width="auto", flex="0 0 auto"))
        button.add_class(css)
        button.on_click(self.window._safely(lambda _button: on_click()))
        return button

    def open(self) -> None:
        """
        Shows the saved chats as they are now.
        """
        self.root.layout.display = ""
        _class_if(self.window.chats_button, "agc-on", True)
        self.refresh()

    def close(self) -> None:
        self.root.layout.display = "none"
        _class_if(self.window.chats_button, "agc-on", False)
        self.sure = None

    def refresh(self) -> None:
        """
        Draws the chats again from the file (after an answer, a rename, a delete, or a chat opened).
        """
        core = self.core
        if core.chats_file is None:
            self.window._set(self.head, f'<div class="ag-mh">{_prose(_CHATS_OFF)}</div>')
            self.rename_row.layout.display = "none"
            self.saved.children = ()
            return
        saved = core.chats()
        current = next((chat for chat in saved if chat.thread == core.thread), None)
        self._draw_head(current, saved)
        self.saved.children = tuple(self._chat_row(chat, is_open=chat is current) for chat in saved)

    def _draw_head(self, current: ChatSummary | None, saved: list[ChatSummary]) -> None:
        """
        The line above the chats (the open chat's title, and how many are saved) and the rename box, shown only for
        a chat that's saved.
        """
        self.rename_row.layout.display = "" if current is not None else "none"
        if current is not None:
            self.window._quietly(self.title_box, value=current.title)
            open_line = f"This chat: <b>{_esc(current.title)}</b>"
        else:
            open_line = _esc(_NOT_SAVED_YET)
        if not saved:
            self.window._set(self.head, f'<div class="ag-mh">{_esc(_NOTHING_SAVED)}</div>')
            return
        count = _plural(len(saved), "saved chat")
        self.window._set(self.head, f'<div class="ag-mh">{open_line} · {count}: click one to carry it on</div>')

    def _chat_row(self, chat: ChatSummary, *, is_open: bool) -> Any:
        w = self.w
        delete = self._button("✕", "Delete this chat", lambda: self._delete(chat), "agc-x")
        _class_if(delete, "agc-sure", self.sure == chat.thread)
        if self.sure == chat.thread:
            delete.description, delete.tooltip = "delete?", "Click again to delete this chat from this machine"
        box, _, face = _row_button(w, ("agc-row", "agc-row-b", "agc-row-f"),
                                   self.window._safely(lambda _button: self._open(chat)),
                                   tooltip="Open this chat and carry it on")
        face.value = chat_line_html(chat, is_open=is_open)
        box.layout.flex = "1 1 auto"
        box.layout.min_width = "0"
        row = w.HBox([box, delete], layout=w.Layout(width="100%"))
        row.add_class("agc-chat")
        return row

    def _open(self, chat: ChatSummary) -> None:
        window = self.window
        if window.busy:
            window._set_status("Still answering: press stop first, then open another chat.", "warn")
            return
        self.sure = None
        opened = self.core.open_chat(chat.thread)
        window.opened()
        window._set_status(f"Opened {opened.title!r}: your next question carries it on.", "ok")

    def _rename(self) -> None:
        title = str(self.title_box.value or "").strip()
        if not title:
            self.window._set_status("Type a title first, then press Enter or rename.", "warn")
            return
        renamed = self.core.rename_chat(title)
        self.window.renamed()
        self.window._set_status(f"Renamed: this chat is now {renamed.title!r}.", "ok")

    def _delete(self, chat: ChatSummary) -> None:
        """
        The first click on a chat's ✕ asks to click again; the second deletes it.
        """
        window = self.window
        if self.sure != chat.thread:
            self.sure = chat.thread
            self.refresh()
            return
        if window.busy and chat.thread == self.core.thread:
            window._set_status("Still answering in this chat: press stop first, then delete it.", "warn")
            return
        was_open = chat.thread == self.core.thread
        self.sure = None
        self.core.delete_chat(chat.thread)
        if was_open:
            window.cleared()
        self.refresh()
        window._set_status(f"Deleted {chat.title!r} from this machine.", "ok")
