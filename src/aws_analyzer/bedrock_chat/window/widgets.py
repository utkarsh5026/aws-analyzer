"""
The chat window's header fields: a picker with a searchable list, and one line of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.fmt import _plural
from ..._kit.render import _prose
from ..._kit.text import _esc, _marked, search_rank
from ..._kit.window import _Picker, _class_if

from ..blocks import _wrap

if TYPE_CHECKING:
    from .app import _ChatApp


_PICKER_ROWS = 40  # lines a picker's list shows at once; the search box finds the rest


@dataclass
class _Choice:
    """
    One line of a picker's list.
    """

    value: str  # what picking it sets: a knowledge base ID, a model ID, data source IDs, a file's s3:// path
    title: str  # its name
    detail: str = ""  # beside the name, in a code font: its ID
    note: str = ""  # the line under it: what it is, its status, when it changed
    badge: str = ""  # beside the name on the field once it's picked, instead of the detail
    tone: str = ""  # the dot before it: 'ok' | 'warn' | 'bad', or '' for none
    also: tuple[str, ...] = ()  # more text the search box finds it by (an ARN, a model's foundation model ID)

    def fields(self) -> tuple[str, ...]:
        return (self.title, self.value, self.detail, self.note, *self.also)


class _ChoicePicker(_Picker):
    """
    A field of the window's header that opens a list to pick from, with a search box over it: the knowledge base,
    data source, model and files pickers. Plain widgets and CSS, like the rest of the window: the field is a button
    under its label and value, the list opens under it over the chat (.kbc-pop), and each line is a button under its
    text, so the whole line is the click target.

    The search box finds every word anywhere in a line (name, ID, description...), best first (search_rank), with
    what it found marked. Enter picks the first line; when nothing matches, on_text gets the text (an ID the list
    doesn't hold). With multi=True a click ticks or unticks a line and the list stays open. on_pick gets the value
    picked (the values, with multi); setting the value from code (set_value) doesn't call it.

    While a list is open, the window's backdrop (an invisible button over the rest of the window, under the open
    field) takes a click anywhere else and closes it, as a dropdown would.
    """

    def __init__(self, app: _ChatApp, label: str, noun: str, *, on_pick: Callable[[Any], None], placeholder: str,
                 empty: str, on_text: Callable[[str], None] | None = None, on_open: Callable[[], None] | None = None,
                 query: Callable[[str], str] | None = None,
                 describe: Callable[[list[str]], tuple[str, str]] | None = None, multi: bool = False,
                 basis: str = "240px", list_width: str = ""):
        # the header's fields share its row, each from its basis up to 420px, and wrap when they don't fit
        super().__init__(app, app.w, "kbc", on_pick=on_pick, on_text=on_text, placeholder=placeholder,
                         close_width="26px", list_width=list_width or None,
                         field_layout={"flex": f"1 1 {basis}", "min_width": "150px", "max_width": "420px"})
        self.label, self.noun, self.multi, self.empty = label, noun, multi, empty
        self.on_open, self.query, self.describe = on_open, query, describe
        self.choices: list[_Choice] = []
        self.picked: list[str] = []  # the value picked (one, unless multi)
        self.note = ""  # more to say under the list (only part of it was listed)
        self.tip_off = ""  # the field's tooltip while it's disabled
        self._draw()

    @property
    def value(self) -> str:
        """
        The value picked ('' when none); with multi, see `picked`.
        """
        return self.picked[0] if self.picked else ""

    @property
    def visible(self) -> bool:
        return self.field.layout.display != "none"

    @visible.setter
    def visible(self, on: bool) -> None:
        self.field.layout.display = "" if on else "none"
        if not on:
            self.close()

    @property
    def disabled(self) -> bool:
        return bool(self.button.disabled)

    @disabled.setter
    def disabled(self, on: bool) -> None:
        if on:
            self.close()
        self.button.disabled = on
        _class_if(self.field, "kbc-off", on)
        self._draw()

    def set_choices(self, choices: Iterable[_Choice], picked: Any = None) -> None:
        """
        What the list holds, and (unless None) what's picked.
        """
        self.choices = list(choices)
        if picked is None:
            self._draw()
        else:
            self.set_value(picked)

    def set_value(self, picked: Any) -> None:
        """
        Shows `picked` (a value, or a list of them with multi) as picked, without calling on_pick.
        """
        values = [picked] if isinstance(picked, str) else [str(v) for v in picked or []]
        self.picked = [v for v in values if v] if self.multi else values[:1]
        self._draw()

    def _can_open(self) -> bool:
        return not self.disabled

    def _opening(self) -> None:
        if self.on_open is not None:
            self.on_open()

    def _query(self, text: str) -> str:
        return self.query(text) if self.query else text

    def _fields(self, c: _Choice) -> tuple[str, ...]:
        return c.fields()

    def _key(self, c: _Choice) -> str:
        return c.value

    def _choice(self, value: str) -> _Choice | None:
        return next((c for c in self.choices if c.value == value), None)

    def _draw(self) -> None:
        """
        The field: its label, what's picked and a chevron.
        """
        tone, mono = "", False
        if self.describe is not None:
            title, badge = self.describe(list(self.picked))
        else:
            c = self._choice(self.value)
            if c is None:
                title, badge = self.value, ""
            else:
                title, badge, tone, mono = c.title, c.badge or c.detail, c.tone, not c.badge
        shown = f"<b>{_esc(title)}</b>" if title else f'<b class="fxe">{_esc(self.empty)}</b>'
        self.app._set(self.face, _wrap(
            f'<div class="fx"><div class="fxl">{_esc(self.label)}</div><div class="fxv">'
            + (f'<span class="dot {tone}"></span>' if tone else "") + shown
            + (f'<span class="fxb{" id" if mono else ""}">{_esc(badge)}</span>' if badge else "")
            + '</div><span class="chev"></span></div>'))
        if self.disabled and self.tip_off:
            tip = self.tip_off
        else:
            tip = f"{self.label}: {title or self.empty}" + (f" ({badge})" if badge else "") + (
                ". Click to search the list" + (" by name or ID" if self.query or self.on_text else ""))
        if self.button.tooltip != tip:
            self.button.tooltip = tip
        if self.is_open:
            self._draw_list()

    def _row(self, c: _Choice, words: list[str], dots: bool) -> Any:
        row, button, text = self._line(c.value)
        on = c.value in self.picked
        _class_if(row, "kbc-on", on)
        tip = " · ".join(part for part in (c.title, c.detail, c.note) if part)
        if self.multi:
            tip += ": click to stop asking only it" if on else ": click to ask it"
        if button.tooltip != tip:
            button.tooltip = tip
        dot = f'<span class="dot {c.tone or "none"}"></span>' if dots else ""
        self.app._set(text, _wrap(
            f'<div class="op{" on" if on else ""}{" multi" if self.multi else ""}">{dot}<div class="opb">'
            f'<div class="opt"><b>{_marked(c.title, words)}</b>'
            + (f'<span class="opi">{_marked(c.detail, words)}</span>' if c.detail else "") + "</div>"
            + (f'<div class="opn">{_marked(c.note, words)}</div>' if c.note else "")
            + '</div><span class="opk"></span></div>'))
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()
        shown = found[:_PICKER_ROWS]
        words = (self.query(text) if self.query else text).split()
        dots = any(c.tone for c in self.choices)
        self.list.children = [self._row(c, words, dots) for c in shown]
        self.shown = [c.value for c in shown]
        level, line = "", ""
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level = "warn" if self.problem else ""
            line = self.problem or f"There's no {self.noun} to pick from."
        elif text and not found:
            level = "warn"
            line = f"No {self.noun} matches {text!r}." + (
                " Enter tries it as an ID." if self.on_text is not None and not self.multi else "")
        else:
            line = (f"{len(found):,} of {_plural(len(self.choices), self.noun)}" if text
                    else _plural(len(self.choices), self.noun))
            if len(found) > len(shown):
                line += f", the first {len(shown):,} shown: type more to narrow them"
            if self.multi:
                line += (f" · {len(self.picked):,} ticked" if self.picked else "") + " · a click ticks or unticks one"
            elif text:
                line += " · Enter picks the first"
            if self.note:
                line += f". {self.note}"
        self.app._set(self.foot, _wrap(f'<div class="opf {level}">{_prose(line)}</div>') if line else "")

    def _typed(self, change: dict[str, Any]) -> None:
        if self.app.quiet:
            return
        self.message = ""
        self._draw_list()

    def _clicked(self, value: str) -> None:
        self.message = ""
        if self.multi:
            self.picked = [v for v in self.picked if v != value] if value in self.picked else [*self.picked, value]
            self._draw()
            self.on_pick(list(self.picked))
            return
        self.close()
        if value != self.value or not self.picked:
            self.picked = [value]
            self._draw()
            self.on_pick(value)

    def _entered(self) -> None:
        """
        Enter in the search box: picks the first line found (with multi, the only one), or hands the text to
        on_text when nothing is found.
        """
        text = str(self.search.value or "").strip()
        if not text:
            if not self.multi:
                self.close()
            return
        found = self.matches()
        if self.multi:
            exact = [c for c in found if search_rank(text, c.fields()) == 0]
            pick = exact[0] if len(exact) == 1 else found[0] if len(found) == 1 else None
        else:
            pick = found[0] if found else None
        try:
            if pick is not None and self.multi:
                self.app._quietly(self.search, value="")
                if pick.value not in self.picked:
                    self._clicked(pick.value)
            elif pick is not None:
                self._clicked(pick.value)
            elif found:
                self.message = (f"{_plural(len(found), self.noun)} match {text!r}: click the ones you want, or type "
                                "more of the name.")
            elif self.on_text is not None:
                self.on_text(text)
                if self.multi:
                    self.app._quietly(self.search, value="")
                else:
                    self.close()
            else:
                self.message = f"No {self.noun} matches {text!r}."
        except (ValueError, ClientError, BotoCoreError) as exc:  # said under the list, where the eyes are
            self.message = str(exc) if isinstance(exc, ValueError) else self.app._error_text(exc)
        if self.is_open:
            self._draw_list()
