"""
The explorer's Dead letters tab: dead_letters()'s cards and findings, then a line per dead-letter queue, the fullest
first, each opening in the Overview tab.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Next, _Table
from ..._kit.window import _class_if

from ..analysis import dead_letters_from
from ..helpers import seconds_words
from ..models import DeadLetter
from .frame import _dead_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import MessagingExplorer


def _oldest(entry: DeadLetter) -> str:
    oldest = entry.numbers.oldest_now_s if entry.numbers else None
    return f"oldest {seconds_words(oldest)}" if oldest else f"keeps them {seconds_words(entry.queue.retention_s)}"


class _DeadTab:
    """
    The Dead letters tab, a part of MessagingExplorer: `dead_shown` is the dead-letter queues in the order drawn.
    """

    def _dead_page(self: MessagingExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.dead_shown: list[DeadLetter] = []
        self._dead_rows: list[_Line] = []
        self.dead_view = w.HTML(layout=layout(width="100%"))
        self.dead_box = w.VBox(layout=layout(width="100%"))
        for name in ("mgx-machines", "mgx-dead"):
            self.dead_box.add_class(name)
        return w.VBox([self.dead_view, self.dead_box])

    def _draw_dead(self: MessagingExplorer) -> None:
        overview = self.overview
        if overview is None:
            return
        blocks = self.ui._dead_blocks(overview)
        head = [b for b in blocks if not isinstance(b, (_Table, _Next))]
        self._draw("dead", self.dead_view, head)
        self.dead_shown = dead_letters_from(overview)
        while len(self._dead_rows) < len(self.dead_shown):
            self._dead_rows.append(_Line(self, self._clicked_dead))
        for row, entry in zip(self._dead_rows, self.dead_shown):
            row.key = f"queue:{entry.queue.arn}"
            self._set(row.face, _dead_face(name=entry.queue.name, messages=entry.queue.messages,
                                           oldest=_oldest(entry), catches=", ".join(entry.catches) or "-", words=[]))
            _class_if(row.box, "mgx-on", row.key == self.item_key)
        self.dead_box.children = [row.box for row in self._dead_rows[:len(self.dead_shown)]]

    def _clicked_dead(self: MessagingExplorer, line: _Line) -> None:
        if line.key:
            self._open_item(line.key, tab="overview")
