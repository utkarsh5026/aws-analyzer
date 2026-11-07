"""
The explorer's Map tab: follow()'s map of the queue, topic or rule open, drawn from the overview already read (no
more reads), each hop a line to click: a queue, topic or rule opens there, and the map follows it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Next, _Table
from ..._kit.window import _class_if

from ..analysis import follow_map
from ..helpers import arn_name, function_of
from ..models import Hop, MessageMap
from .frame import _hop_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import MessagingExplorer


_OPENS = {"SQS queue": "queue", "SNS topic": "topic", "EventBridge rule": "rule"}


def hop_key(hop: Hop) -> str:
    """
    What clicking a hop opens, as an item's key ('queue:<arn>'), or '' for a hop the window doesn't open (a function,
    an endpoint, a hop shown already).
    """
    kind = _OPENS.get(hop.kind, "")
    return f"{kind}:{hop.arn}" if kind and hop.arn else ""


def _without_tables(blocks: list[Any]) -> list[Any]:
    return [b for b in blocks if not isinstance(b, (_Table, _Next))]


class _MapTab:
    """
    The Map tab, a part of MessagingExplorer: `found` is the MessageMap drawn (None before something is open), and
    `hops_shown` its hops in the order drawn.
    """

    def _map_page(self: MessagingExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.found: MessageMap | None = None
        self.hops_shown: list[Hop] = []
        self._hop_rows: list[_Line] = []
        self.map_view = w.HTML(layout=layout(width="100%"))
        self.hops_box = w.VBox(layout=layout(width="100%"))
        for name in ("mgx-machines", "mgx-hops"):
            self.hops_box.add_class(name)
        return w.VBox([self.map_view, self.hops_box])

    def _draw_map(self: MessagingExplorer, kind: str, arn: str) -> None:
        """
        The map from the queue, topic or rule open, built from the overview: what sends to it, then the way down.
        """
        overview = self.overview
        if overview is None:
            return
        hops = follow_map(overview, kind, arn)
        self.found = MessageMap(kind=kind, name=arn_name(arn), arn=arn, hops=hops, overview=overview)
        blocks = _without_tables(self.ui._follow_blocks(self.found))
        self._draw("map", self.map_view, blocks)
        self._draw_hops()

    def _draw_hops(self: MessagingExplorer) -> None:
        hops = self.found.hops if self.found else []
        self.hops_shown = hops
        while len(self._hop_rows) < len(hops):
            self._hop_rows.append(_Line(self, self._clicked_hop))
        for row, hop in zip(self._hop_rows, hops):
            row.key = hop_key(hop) or f"hop:{hop.arn or hop.name}"
            self._set(row.face, _hop_face(hop))
            _class_if(row.box, "mgx-on", row.key == self.item_key)
        self.hops_box.children = [row.box for row in self._hop_rows[:len(hops)]]

    def _clicked_hop(self: MessagingExplorer, line: _Line) -> None:
        if not line.key.startswith("hop:"):
            self._open_item(line.key, tab="map")
            return
        hop = next((h for h in self.hops_shown if f"hop:{h.arn or h.name}" == line.key), None)
        if hop is None:
            return
        if hop.kind == "Lambda function":
            name = arn_name(function_of(hop.arn))
            self._status(f"{name} is a Lambda function: LambdaView().explore({name!r}) opens its logs, run by run.")
            return
        self._status(f"{hop.name} is a {hop.kind}: this window opens queues, topics and rules.")
