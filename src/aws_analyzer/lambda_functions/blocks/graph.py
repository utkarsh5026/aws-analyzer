"""
_Graph: a CodeGraph drawn as an SVG of cards in columns, with arrows routed between them.
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from ..._kit.blocks import _Report, _draws
from ..._kit.fmt import _plural
from ..._kit.render import _prose
from ..._kit.text import _esc

from ..analysis.codemap.graph import CodeGraph, GraphEdge, GraphNode
from ..style import _ROOT


@dataclass
class _Graph:
    """
    how_it_works()'s graph: a CodeGraph drawn as boxes in columns joined by arrows. In HTML an SVG, where pointing
    at a box leaves only what it's joined to in sight; in text each box with the arrows that leave it.
    """

    graph: CodeGraph
    title: str = ""
    empty: str = "(nothing to draw)"


_EDGE_WORDS = {"call": "calls", "event": "passes the event to", "env": "configures", "client": "makes the client for",
               "http": "sends HTTP requests to", "reads": "reads from", "writes": "writes to", "sends": "sends to",
               "runs": "calls", "signs": "signs links for"}


# how_it_works()'s graph, drawn: cards with an icon badge per kind on a quiet dotted canvas, column captions, arrows that
# land on the operation (or leave from the variable) they're about, and a legend of pills. The _g names are its helpers.
_GRAPH_PAD_X = 18  # left and right of the drawing

_GRAPH_BAND = 44  # the captions' band at the top

_GRAPH_PAD_B = 22

_GRAPH_GAP = 56  # between columns: room for the arrows

_GRAPH_ROW_GAP = 22  # between boxes in a column

_GRAPH_MIN_W, _GRAPH_MAX_W, _GRAPH_MAX_IN, _GRAPH_MAX_HANDLER = 152, 206, 228, 240  # a column's width, from its boxes

_GRAPH_MAX_FLAGGED = 264  # ...or a column whose box has a line a warning is about ('ListObjectsV2  first page only')

_GRAPH_LABEL_LINE = 17  # a name too long for one line takes two

_GRAPH_HEAD, _GRAPH_HEAD_BARE = 56, 50  # a card's header (badge, name, what's under it), and without a line under it

_GRAPH_ROW = 19  # a listed line: a field, a variable, an operation

_GRAPH_PROSE = 17  # a line of prose: a docstring, what start-up does

_GRAPH_ROUTE = 28  # a web route's pill

_GRAPH_SWEEPS = 6  # passes that reorder each column by where its neighbours are, so fewer arrows cross

_GRAPH_LISTS = ("event", "env", "aws", "http")  # boxes whose lines are names: fields, variables, operations, requests

# Arial / Helvetica advance widths (per 1000 em), what the system sans stack draws on most machines; mono is 0.6 em
_G_SANS = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, *[556] * 10, 278, 278, 584, 584,
     584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611,
     722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500,
     222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]))

_G_LAMBDA = "M3.6 2.6h2.1l6 10.8h1.7M8.4 7.4 4.2 13.4"

_G_ICONS = {  # 16 x 16 line drawings, stroked in the badge's colour
    "function": _G_LAMBDA,
    "handler": _G_LAMBDA,
    "event": "M9.2 1.8 3.6 9h4.2l-1 5.2L12.4 7H8.2z",
    "env": "M2.5 4.5h1.6M7.1 4.5h6.4M2.5 8h6.4M11.9 8h1.6M2.5 11.5h3M8.5 11.5h5M5.6 3v3M10.4 6.5v3M7 10v3",
    "start": "M8 2.2v5.2M4.9 4.4a5 5 0 1 0 6.2 0",
    "http": "M8 2a6 6 0 1 0 0 12A6 6 0 1 0 8 2zM2.2 8h11.6M8 2c-2.1 2.2-2.1 9.8 0 12M8 2c2.1 2.2 2.1 9.8 0 12",
    "cloud": "M4.6 12.6h6.9a2.8 2.8 0 0 0 .5-5.55A4 4 0 0 0 4.3 6.4a3.1 3.1 0 0 0 .3 6.2z",
    "database": "M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2zM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4"
                "M3 8c0 1.1 2.2 2 5 2s5-.9 5-2",
    "bucket": "M2.4 4.2c0 1 2.5 1.8 5.6 1.8s5.6-.8 5.6-1.8S11.1 2.4 8 2.4 2.4 3.2 2.4 4.2zM2.4 4.2l1.5 8.5"
              "c.2 1 1.9 1.6 4.1 1.6s3.9-.6 4.1-1.6l1.5-8.5",
    "queue": "M2 5.2h3.2v5.6H2zM6.4 5.2h3.2v5.6H6.4zM10.8 5.2H14v5.6h-3.2z",
    "send": "M13.8 2.2 6.9 9.1M13.8 2.2 9.6 13.8 6.9 9.1 2.2 6.4z",
    "key": "M10.4 2.6a3 3 0 1 1-2.2 5.1L3 12.9v1.5h1.9v-1.5h1.5v-1.5l1.8-1.8M10.9 5.1h.01",
    "spark": "M8 1.8l1.5 3.9 3.9 1.5-3.9 1.5L8 12.6 6.5 8.7 2.6 7.2l3.9-1.5zM12.6 11.2l.6 1.5 1.5.6-1.5.6-.6 1.5"
             "-.6-1.5-1.5-.6 1.5-.6z",
    "flow": "M4 2.8a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2zM12 10a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2z"
            "M4 6v2.4c0 1.3 1 2.3 2.3 2.3h4.1",
}

_G_SERVICE_ICONS = (  # an AWS service's box id, by what it starts with -> its icon
    (("aws-dynamodb", "aws-rds", "aws-redshift", "aws-timestream", "aws-docdb", "aws-neptune", "aws-elasticache",
      "aws-keyspaces", "aws-athena"), "database"),
    (("aws-s3",), "bucket"),
    (("aws-sqs", "aws-kinesis", "aws-firehose", "aws-mq"), "queue"),
    (("aws-sns", "aws-events", "aws-ses", "aws-pinpoint", "aws-iot"), "send"),
    (("aws-secretsmanager", "aws-ssm", "aws-kms", "aws-sts", "aws-cognito", "aws-iam", "aws-acm"), "key"),
    (("aws-bedrock", "aws-sagemaker", "aws-comprehend", "aws-textract", "aws-rekognition", "aws-translate",
      "aws-polly", "aws-transcribe"), "spark"),
    (("aws-stepfunctions", "aws-states", "aws-glue", "aws-batch", "aws-ecs", "aws-codebuild"), "flow"),
    (("aws-lambda",), "function"),
)

_G_LOOP = "M12.4 9.3A4.6 4.6 0 1 1 11.3 4.7M12.1 2.3v2.8H9.3"

_G_ACCESS = ("reads", "writes", "sends", "runs", "signs")

_G_LEGEND = (("call", "calls"), ("reads", "reads"), ("writes", "writes"), ("sends", "sends"), ("runs", "runs"),
             ("http", "HTTP request"), ("signs", "signs links"), ("env", "configures"), ("client", "makes a client"))


def _g_sans(text: str, size: float, bold: bool = False) -> float:
    """
    About how wide `text` is in the sans stack, a little generous (Segoe UI, Roboto and Inter run wider).
    """
    return sum(_G_SANS.get(ch) or (1000 if ch == "…" or _g_wide(ch) else 600) for ch in text) / 1000 * size * (
        1.1 if bold else 1.05)


def _g_mono(text: str, size: float) -> float:
    return (len(text) + sum(map(_g_wide, text))) * 0.61 * size


def _g_wide(ch: str) -> bool:
    """
    A character drawn twice as wide: Chinese, Japanese, Korean.
    """
    return unicodedata.east_asian_width(ch) in "WF"


def _g_fit(text: str, room: float, measure: Callable[[str], float]) -> str:
    """
    text, cut with … to fit `room` pixels.
    """
    if measure(text) <= room:
        return text
    while text and measure(text + "…") > room:
        text = text[:-1]
    return text.rstrip() + "…"


def _g_wrap(text: str, room: float, measure: Callable[[str], float], most: int = 2) -> list[str]:
    """
    Words in lines that fit `room`, at most `most`, the last cut with … when there's more.
    """
    words, lines, line = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{line} {word}".strip()
        if measure(trial) <= room or not line:
            line = trial
            continue
        lines.append(line)
        line = word
        if len(lines) == most:
            line = ""
            lines[-1] = _g_fit(lines[-1] + " " + " ".join(words[i:]), room, measure)
            break
    if line:
        lines.append(line)
    return [_g_fit(x, room, measure) for x in lines[:most]]


def _g_icon(n: GraphNode) -> str:
    if n.kind == "aws":
        return next((icon for starts, icon in _G_SERVICE_ICONS if n.id.startswith(starts)), "cloud")
    return n.kind if n.kind in _G_ICONS else "cloud"


class _GCard:
    """
    A box of the graph being drawn: its node, its size, and what it shows in what order.
    """

    def __init__(self, node: GraphNode) -> None:
        self.node = node
        self.width = self.height = self.body = 0.0
        self.head = float(_GRAPH_HEAD)
        self.route = ""  # the web route that leads to it ('GET /orders')
        self.rows: list[str] = []  # its listed lines, in the order drawn
        self.prose: list[str] = []
        self.label: list[str] = []  # its name, in one line or two
        self.arrows = 0  # the most arrows on one of its sides: a box with many is tall enough to space them

    def row_y(self, text: str) -> float | None:
        if text in self.rows:
            return self.body + 6 + _GRAPH_ROW * self.rows.index(text) + _GRAPH_ROW / 2
        return None


def _g_measure_label(n: GraphNode, text: str) -> float:
    if n.kind in ("handler", "function"):
        return _g_mono(text, 14 if n.kind == "handler" else 13.5) * 1.03
    return _g_sans(text, 14, bold=True)


def _g_need(card: _GCard) -> float:
    """
    How wide its card would like to be: its name, what's under it and its listed lines whole.
    """
    n = card.node
    need = 52 + _g_measure_label(n, n.label) + 16 + (26 if n.tone == "warn" else 0)
    if n.sub:
        whole = 52 + _g_sans(n.sub, 12.5) + 14
        if n.kind in ("handler", "function") and "/" in n.sub:  # past 190px its folder is shortened instead
            whole = min(whole, max(190.0, 52 + _g_sans(n.sub.rsplit("/", 1)[-1], 12.5) + 14))
        need = max(need, whole)
    if card.route:
        need = max(need, 16 + _g_mono(_g_route(card)[0] + _g_route(card)[2], 12.5) + 36)
    if n.kind in _GRAPH_LISTS:
        need = max([need] + [28 + _g_mono(line, 12.5) + 16 + (_g_sans(n.flags[line], 12, True) + 12 if line in n.flags
                                                             else 0)
                             for line in n.lines if not line.startswith(("+", "("))])
    return need


def _g_route(card: _GCard) -> tuple[str, str, str]:
    """
    A route's pill: the first route the box answers, its method ('GET', or 'GET, POST') and ' +1' for the rest.
    """
    first, *rest = card.route.split("; ")
    return first, first.rpartition(" ")[0], f" +{len(rest)}" if rest else ""


def _g_cards(graph: CodeGraph) -> dict[str, _GCard]:
    cards = {n.id: _GCard(n) for n in graph.nodes}
    sides: dict[tuple[str, int], int] = defaultdict(int)
    for e in graph.edges:
        sides[(e.source, 0)] += 1
        sides[(e.target, 1)] += 1
    for (node_id, _), count in sides.items():
        if node_id in cards:
            cards[node_id].arrows = max(cards[node_id].arrows, count)
    for e in graph.edges:  # a web app's route goes on the function it leads to
        if e.kind == "call" and e.label and not e.label.startswith("line ") and e.target in cards:
            cards[e.target].route = e.label
    for card in cards.values():
        if card.node.kind in _GRAPH_LISTS:
            card.rows = list(card.node.lines)
    return cards


def _g_size(cards: dict[str, _GCard], widths: dict[int, float], column_of: dict[str, int]) -> None:
    for card in cards.values():
        n = card.node
        card.width = widths[column_of[n.id]]
        room = card.width - 52 - 14 - (24 if n.tone == "warn" else 0)
        if n.kind in ("handler", "function"):  # code: clipped, never wrapped
            card.label = [_g_fit(n.label, room, lambda t: _g_measure_label(n, t))]
        else:
            card.label = _g_wrap(n.label, room, lambda t: _g_measure_label(n, t))
        card.head = (_GRAPH_HEAD if n.sub else _GRAPH_HEAD_BARE) + _GRAPH_LABEL_LINE * (len(card.label) - 1)
        top = card.head + (_GRAPH_ROUTE if card.route else 0)
        card.body = top
        if n.kind in _GRAPH_LISTS and card.rows:
            card.height = top + 6 + _GRAPH_ROW * len(card.rows) + 8
        else:
            card.prose = [part for line in n.lines for part in
                          _g_wrap(line, card.width - 30, lambda t: _g_sans(t, 12.5))][:3]
            card.height = top + (_GRAPH_PROSE * len(card.prose) + 10 if card.prose else 4)
            card.height = max(card.height, 24 + 12 * card.arrows)


def _graph_layout(graph: CodeGraph, cards: dict[str, _GCard]) -> tuple[
        dict[str, tuple[float, float, float, float]], dict[int, list[float]], float, float, dict[int, float],
        dict[int, float], dict[str, int]]:
    """
    Where each box goes: (x, y, width, height) by its id; for each arrow that skips columns, the heights where it
    crosses the columns between (a waypoint in each, so it runs between their boxes, not behind them); the drawing's
    width and height; and each column's x and width, and each box's column. A column is as wide as its boxes want,
    within limits. Each column is put in the order of its neighbours' heights, a few passes left to right and back
    (the barycenter method), so arrows cross less; then each box is moved as near its neighbours as the boxes around
    it let it be.
    """
    order = sorted({n.column for n in graph.nodes})
    place = {col: i for i, col in enumerate(order)}
    column_of = {n.id: place[n.column] for n in graph.nodes}
    widths = {k: 0.0 for k in range(len(order))}
    for card in cards.values():
        k = column_of[card.node.id]
        widths[k] = max(widths[k], _g_need(card))
    most = {k: float(_GRAPH_MAX_W) for k in widths}
    for n in graph.nodes:
        if n.kind in ("event", "env", "handler"):
            k = column_of[n.id]
            most[k] = max(most[k], _GRAPH_MAX_HANDLER if n.kind == "handler" else _GRAPH_MAX_IN)
        if n.kind in _GRAPH_LISTS and n.flags:  # the line a warning is about, whole beside its flag
            k = column_of[n.id]
            most[k] = max(most[k], min(_g_need(cards[n.id]), _GRAPH_MAX_FLAGGED))
    widths = {k: min(max(w, _GRAPH_MIN_W), most[k]) for k, w in widths.items()}
    _g_size(cards, widths, column_of)
    height = {n.id: cards[n.id].height for n in graph.nodes}
    columns: dict[int, list[str]] = defaultdict(list)
    for node in graph.nodes:
        columns[column_of[node.id]].append(node.id)
    near: dict[str, list[str]] = defaultdict(list)
    hops: dict[int, list[str]] = {}
    for i, e in enumerate(graph.edges):
        a, b = column_of[e.source], column_of[e.target]
        chain = [e.source]
        if abs(b - a) > 1:  # a waypoint in each column it crosses, either way
            step = 1 if b > a else -1
            hops[i] = [f"~{i}.{k}" for k in range(a + step, b, step)]
            for k, hop in zip(range(a + step, b, step), hops[i]):
                columns[k].append(hop)
                column_of[hop] = k
                height[hop] = 4.0
            chain += hops[i]
        chain.append(e.target)
        for x, y in zip(chain, chain[1:]):
            if x != y:
                near[x].append(y)
                near[y].append(x)
    center: dict[str, float] = {}
    for k in range(len(order)):
        top = 0.0
        for item in columns[k]:
            center[item] = top + height[item] / 2
            top += height[item] + _GRAPH_ROW_GAP

    def gap(x: str, y: str) -> float:
        return _GRAPH_ROW_GAP if not (x.startswith("~") or y.startswith("~")) else 12.0

    def settle(items: list[str], wanted: list[float]) -> None:
        """
        Each box at the height it wants, pushed down so none overlaps, then the column moved back up by how far
        it was pushed on average.
        """
        at = list(wanted)
        for j in range(1, len(items)):
            at[j] = max(at[j], at[j - 1] + height[items[j - 1]] / 2 + gap(items[j - 1], items[j]) + height[items[j]] / 2)
        shift = sum(w - a for w, a in zip(wanted, at)) / len(items)
        for item, a in zip(items, at):
            center[item] = a + shift

    for sweep in range(_GRAPH_SWEEPS):
        for k in (range(len(order)) if sweep % 2 == 0 else range(len(order) - 1, -1, -1)):
            items = columns[k]

            def pull(item: str) -> float:
                ys = [center[m] for m in near[item] if column_of[m] != k]
                return sum(ys) / len(ys) if ys else center[item]

            items.sort(key=pull)
            settle(items, [pull(item) for item in items])
    top = min(center[item] - height[item] / 2 for item in center)
    xs, x = {}, float(_GRAPH_PAD_X)
    for k in range(len(order)):
        xs[k] = x
        x += widths[k] + _GRAPH_GAP
    boxes = {n.id: (xs[column_of[n.id]], center[n.id] - height[n.id] / 2 - top + _GRAPH_BAND, widths[column_of[n.id]],
                    height[n.id]) for n in graph.nodes}
    routes = {i: [center[hop] - top + _GRAPH_BAND for hop in chain] for i, chain in hops.items()}
    last = len(order) - 1  # a call within the last column curls round into the margin; the others, into a gap
    loops = 52 if any(column_of[e.source] == column_of[e.target] == last for e in graph.edges) else 0
    width = x - _GRAPH_GAP + _GRAPH_PAD_X + loops
    tall = max(max(y + h for _, y, _, h in boxes.values()),
               max((y for ys in routes.values() for y in ys), default=0)) + _GRAPH_PAD_B
    return boxes, routes, width, tall, xs, widths, column_of


def _g_names(e: GraphEdge) -> list[str]:
    return [part for part in e.label.split(", ") if part]


def _g_rows(card: _GCard, e: GraphEdge) -> list[str]:
    """
    The rows of its box an arrow leaves from or lands on: the variables it carries, the operations it calls (the
    '+3 more' row for one that isn't listed); none for the event as a whole or a client.
    """
    if card.node.kind not in _GRAPH_LISTS or e.kind in ("event", "client"):
        return []
    if e.label in card.rows:
        return [e.label]
    hit = [r for r in card.rows if f", {r}, " in f", {e.label}, "]
    if not hit and any(r.startswith("+") for r in card.rows):
        hit = [r for r in card.rows if r.startswith("+")]
    return [r for r in hit if card.row_y(r) is not None]


def _g_anchors(graph: CodeGraph, cards: dict[str, _GCard], boxes: dict[str, tuple[float, float, float, float]]
               ) -> dict[int, tuple[list[float], list[float]]]:
    """
    Where each arrow leaves and arrives, as heights on the page: from the rows of the variables it carries, to the
    rows of the operations or requests it makes; otherwise spread down the box's side in the order of what's at the
    other end, outgoing arrows and incoming ones on separate parts of a side they share. Rows are first put in the
    order of what they're joined to, so arrows cross less.
    """
    def middle(node_id: str) -> float:
        return boxes[node_id][1] + boxes[node_id][3] / 2

    rows_for = _g_rows
    joined: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for e in graph.edges:
        for here, there in ((e.source, e.target), (e.target, e.source)):
            for row in rows_for(cards[here], e):
                joined[here][row].append(middle(there))
    for node_id, by_row in joined.items():
        rank = {row: sum(ys) / len(ys) for row, ys in by_row.items()}
        cards[node_id].rows.sort(key=lambda r: (r.startswith("+"), rank.get(r, 1e9)))

    ends: dict[int, list[list[float]]] = {}
    loose: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)  # (node, side of the box) -> (edge, end)
    for i, e in enumerate(graph.edges):
        pair: list[list[float]] = []
        for end, node_id in ((0, e.source), (1, e.target)):
            card = cards[node_id]
            ys = [boxes[node_id][1] + y for r in rows_for(card, e) if (y := card.row_y(r)) is not None]
            if not ys:  # which side of the box the arrow touches there: right, unless it arrives from the left
                forward = boxes[e.target][0] > boxes[e.source][0]
                side = "left" if (end == 1 and forward) or (end == 0 and boxes[e.target][0] < boxes[e.source][0]) \
                    else "right"
                loose[(node_id, side)].append((i, end))
            pair.append(ys)
        ends[i] = pair

    def other(i: int, end: int) -> float:
        far = ends[i][1 - end]
        e = graph.edges[i]
        return sum(far) / len(far) if far else middle(e.target if end == 0 else e.source)

    for (node_id, _), items in loose.items():
        card, (_, y, _, h) = cards[node_id], boxes[node_id]
        items.sort(key=lambda item: (item[1], other(*item)))  # arrivals above departures on a shared side
        if card.node.kind in _GRAPH_LISTS:  # at the name: the event as a whole, a client made for the service
            hub, room = y + 27, 22.0
        else:
            hub, room = y + h / 2, h - 22
        step = min(12.0, room / max(len(items), 1))
        for k, (i, end) in enumerate(items):
            ends[i][end] = [hub + (k - (len(items) - 1) / 2) * step]
    shared: dict[tuple[str, int, float], list[int]] = defaultdict(list)  # arrows that share a row's port
    for i, e in enumerate(graph.edges):
        for end, node_id in ((0, e.source), (1, e.target)):
            for y in ends[i][end]:
                shared[(node_id, end, round(y, 1))].append(i)
    for (node_id, end, y), edges in shared.items():
        if len(edges) > 1 and end == 0:  # leaving one row: side by side, within the row (arriving: one head)
            edges.sort(key=lambda i: other(i, end))
            step = min(5.0, (_GRAPH_ROW - 6) / (len(edges) - 1))
            for k, i in enumerate(edges):
                ends[i][end] = [v + (k - (len(edges) - 1) / 2) * step if round(v, 1) == y else v for v in ends[i][end]]
    return {i: (pair[0], pair[1]) for i, pair in ends.items()}


def _g_cubic(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float],
             t: float) -> tuple[float, float]:
    u = 1 - t
    x, y = (u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d for a, b, c, d in zip(p0, p1, p2, p3))
    return x, y


def _g_spots(curve: tuple[tuple[float, float], ...]) -> list[tuple[float, float]]:
    return [_g_cubic(*curve, t) for t in (0.5, 0.3, 0.7, 0.18, 0.82)]  # type: ignore[arg-type]


def _g_strand(points: list[tuple[float, float]]) -> tuple[str, list[tuple[float, float]]]:
    """
    One line through `points`: (start, then a column's left and right for each column it skips, then the end).
    Curves in the gaps, straight across each column it skips; returns the path and where its marks can go, the best
    first: along its middle straight run, in the clear between a column's boxes, or else along its curve.
    """
    (x1, y1) = points[0]
    parts: list[str] = [f"M{x1:.1f},{y1:.1f}"]
    runs: list[tuple[float, float, float]] = []
    curve: tuple[tuple[float, float], ...] = ()
    for j in range(1, len(points)):
        (ax, ay), (bx, by) = points[j - 1], points[j]
        if j % 2 == 0 and j < len(points) - 1:
            parts.append(f"L{bx:.1f},{by:.1f}")
            runs.append((ax, bx, by))
            continue
        reach = max(18.0, abs(bx - ax) * 0.5) * (1 if bx >= ax else -1)
        parts.append(f"C{ax + reach:.1f},{ay:.1f} {bx - reach:.1f},{by:.1f} {bx:.1f},{by:.1f}")
        curve = curve or ((ax, ay), (ax + reach, ay), (bx - reach, by), (bx, by))
    if not runs:
        return " ".join(parts), _g_spots(curve)
    ax, bx, y = runs[(len(runs) - 1) // 2]
    middle, d = (ax + bx) / 2, (1 if bx >= ax else -1)
    return " ".join(parts), [(middle + k * 19 * d, y) for k in (0, 1, -1, 2, -2) if abs(k * 19) < abs(bx - ax) / 2 - 9]


def _g_head(x: float, y: float, d: int) -> str:
    """
    A slim arrowhead with a notched back, its tip at (x, y), pointing right (d=1) or left (d=-1).
    """
    return (f"M{x:.1f},{y:.1f} L{x - 9 * d:.1f},{y - 4.4:.1f} Q{x - 6.2 * d:.1f},{y:.1f} {x - 9 * d:.1f},{y + 4.4:.1f}"
            " Z")


def _g_edge_tip(e: GraphEdge, graph: CodeGraph) -> str:
    """
    What an arrow says on hover, in a sentence.
    """
    source, target = graph.node(e.source), graph.node(e.target)
    src = source.label if source else e.source
    dst = target.label if target else e.target
    if e.kind == "event":
        said = f"Lambda calls {dst} with the event"
    elif e.kind == "env":
        said = f"{dst} reads {e.label} from its environment"
    elif e.kind == "call":
        said = f"{src} calls {dst}" + (f" ({e.label})" if e.label else "")
    elif e.kind == "client":
        said = f"{src} makes the {dst} client"
    elif e.kind == "http":
        said = f"{src} sends {e.label}"
    else:
        said = f"{src} {_EDGE_WORDS.get(e.kind, e.kind)} {dst}: {e.label}"
    said += "; in a loop, once per item" if e.loop else ""
    said += "; known only by the methods' names (their client couldn't be followed)" if e.guessed else ""
    said += "; in functions further down that the graph leaves out" if e.indirect else ""
    return said + ("; a warning is about it" if e.tone == "warn" else "")


def _edge_svg(e: GraphEdge, graph: CodeGraph, boxes: dict[str, tuple[float, float, float, float]],
              xs: dict[int, float], widths: dict[int, float], column_of: dict[str, int], ys_out: list[float],
              ys_in: list[float], hops: list[float] | None, mark_loop: bool = True,
              hot: set[tuple[int, int]] | None = None, taken: list[tuple[float, float]] | None = None,
              lane: int = 0) -> tuple[str, str]:
    """
    An arrow: its lines (one from each row it leaves to each row it reaches), its head, its loop mark; and, drawn
    over the boxes, the dots where it leaves them. A warned arrow's glow and ! go on the lines in `hot` (by the index
    of the row each leaves and reaches), or on all of them. Its marks go on its line, clear of the marks in `taken`
    (other arrows'), which they're added to; `lane` puts a call within a column outside the ones drawn before it.
    """
    sx, _, sw, _ = boxes[e.source]
    tx, _, tw, _ = boxes[e.target]
    lines: list[str] = []
    spots: list[tuple[float, float]] = []  # where its marks can go, along its first line (or the first one warned)
    start, end = column_of[e.source], column_of[e.target]
    if end > start:  # left to right: from the source's right side to the target's left side
        x1, tip, d = sx + sw, tx - 0.6, 1
        between = [(xs[start + 1 + k], xs[start + 1 + k] + widths[start + 1 + k], y) for k, y in enumerate(hops or [])]
    elif end < start:  # back to an earlier column: from the source's left side to the target's right side
        x1, tip, d = sx, tx + tw + 0.6, -1
        between = [(xs[start - 1 - k] + widths[start - 1 - k], xs[start - 1 - k], y) for k, y in enumerate(hops or [])]
    else:  # the same column: out of the right side, round, and back into the target's right side
        x1, tip, d = sx + sw, tx + tw + 0.6, -1
        between = []
    warned: list[str] = []
    for ia, a in enumerate(ys_out):
        for ib, b in enumerate(ys_in):
            if end == start:
                bulge = 34 + 16 * lane + 8 * len(lines)
                curve = ((x1, a), (x1 + bulge, a), (tip + 6.6 + bulge, b), (tip + 6.6, b))
                path = "M{:.1f},{:.1f} C{:.1f},{:.1f} {:.1f},{:.1f} {:.1f},{:.1f}".format(*(v for p in curve for v in p))
                at = _g_spots(curve)
            else:
                points = [(x1, a)] + [(p, y) for left, right, y in between for p in (left, right)] + [(tip - 6.6 * d, b)]
                path, at = _g_strand(points)
            lines.append(path)
            spots = spots or at
            if e.tone == "warn" and (hot is None or (ia, ib) in hot):
                if not warned:  # the marks go on the first line the warning is about
                    spots = at
                warned.append(path)
    heads = [_g_head(tip, b, d) for b in dict.fromkeys(ys_in)]
    d_attr = " ".join(lines)
    classes = (f"ce ek-{_esc(e.kind)} s-{_esc(e.source)} t-{_esc(e.target)}" + (" warn" if e.tone == "warn" else "")
               + (" guess" if e.guessed else "") + (" via" if e.indirect else ""))
    taken = [] if taken is None else taken

    def place() -> tuple[float, float]:  # the first spot clear of every mark drawn so far
        at = next((p for p in spots if all(math.dist(p, q) >= 17.5 for q in taken)), spots[0])
        taken.append(at)
        return at

    mark = ""
    if spots and e.tone == "warn":
        cx, cy = place()
        mark += (f'<g class="wm" transform="translate({cx:.1f},{cy:.1f})"><circle r="7.5"/>'
                 '<path d="M0,-3.7V.5M0,3.4V3.5"/></g>')
    if spots and e.loop and mark_loop:
        cx, cy = place()
        mark += (f'<g class="lpb" transform="translate({cx:.1f},{cy:.1f})"><circle r="8.5"/>'
                 f'<path transform="scale(.66) translate(-8,-8)" d="{_G_LOOP}"/></g>')
    halo = (f'<path class="halo" d="{" ".join(warned)}"/><path class="cs" d="{" ".join(warned)}"/>'  # amber, edged
            if warned else "")
    body = (f'<g class="{classes}"><title>{_esc(_g_edge_tip(e, graph))}</title><path class="hit" d="{d_attr}"/>{halo}'
            f'<path class="ln" d="{d_attr}"/>' + "".join(f'<path class="ah" d="{h}"/>' for h in heads) + mark + "</g>")
    dots = f'<g class="{classes} pt">' + "".join(f'<circle cx="{x1:.1f}" cy="{y:.1f}" r="2.8"/>'
                                                for y in dict.fromkeys(ys_out)) + "</g>"
    return body, dots


def _g_sub_svg(n: GraphNode, room: float) -> str:
    if n.kind in ("handler", "function") and "/" in n.sub:  # the folder dimmed, shortened from the left
        folder, _, rest = n.sub.rpartition("/")
        folder += "/"
        if _g_sans(rest, 12.5) > room:
            return _esc(_g_fit(rest, room, lambda t: _g_sans(t, 12.5)))
        if _g_sans(folder + rest, 12.5) > room:  # whole folders dropped from the left: '…/orders.py line 10'
            parts = folder.rstrip("/").split("/")
            while parts and _g_sans("…/" + "/".join(parts) + "/" + rest, 12.5) > room:
                parts.pop(0)
            folder = "…/" + ("/".join(parts) + "/" if parts else "")
            if not parts and _g_sans(folder + rest, 12.5) > room:
                folder = ""
        return (f'<tspan class="dir">{_esc(folder)}</tspan>' if folder else "") + _esc(rest)
    if n.kind == "aws" and n.sub:  # 'reads · writes', each in its arrows' colour, cut with … when they don't fit
        words = n.sub.split(" · ")
        shown = list(words)
        while len(shown) > 1 and _g_sans(" · ".join(shown) + (" · …" if shown != words else ""), 12.5, True) > room:
            shown.pop()
        return '<tspan class="sep"> · </tspan>'.join(
            [f'<tspan class="ac ek-{_esc(w)}">{_esc(w)}</tspan>' for w in shown] + (["…"] if shown != words else []))
    return _esc(_g_fit(n.sub, room, lambda t: _g_sans(t, 12.5)))


def _node_svg(card: _GCard, box: tuple[float, float, float, float], neighbours: list[str], access: dict[str, str]
              ) -> str:
    """
    A box: its badge and name, what's under it, a route's pill, its listed lines (an operation's dot in its arrows'
    colour, a line a warning is about with its flag) or its docstring, and a warning badge.
    """
    n = card.node
    x, y, w, h = box
    warn = n.tone == "warn"
    label_y = 27 if n.sub else 31
    parts = [f'<text class="lb" x="52" y="{label_y + _GRAPH_LABEL_LINE * k}">{_esc(line)}</text>'
             for k, line in enumerate(card.label)]
    if n.sub:
        parts.append(f'<text class="sb" x="52" y="{45 + _GRAPH_LABEL_LINE * (len(card.label) - 1)}">'
                     f"{_g_sub_svg(n, w - 52 - 12)}</text>")
    badge_y = 13 if n.sub else 11
    parts.append(f'<rect class="bdg" x="12" y="{badge_y}" width="28" height="28" rx="8"/>'
                 f'<path class="ico" transform="translate(18,{badge_y + 6})" d="{_G_ICONS[_g_icon(n)]}"/>')
    top = card.head
    if card.route:
        first, method, more = _g_route(card)
        path = first[len(method) + 1:]
        pill_w = min(_g_mono(first + more, 12.5) + 20, w - 28)
        shown = _g_fit(path, pill_w - 20 - _g_mono(method + " " + more, 12.5), lambda t: _g_mono(t, 12.5))
        parts.append(f'<g class="rt"><rect x="14" y="{top:.0f}" width="{pill_w:.0f}" height="21" rx="10.5"/>'
                     f'<text x="24" y="{top + 14.8:.1f}"><tspan class="mt">{_esc(method)}</tspan> {_esc(shown)}'
                     f'{_esc(more)}</text></g>')
        top += _GRAPH_ROUTE
    if n.kind in _GRAPH_LISTS and card.rows:
        parts.append(f'<path class="dv" d="M1,{card.body:.0f}H{w - 1:.0f}"/>')
        for row in card.rows:
            ry = card.row_y(row)
            if ry is None:
                continue
            if row.startswith("(") or (row.startswith("+") and row.endswith(" more")):
                parts.append(f'<text class="rw mo" x="16" y="{ry + 4.3:.1f}">{_esc(row)}</text>')
                continue
            tone = (" ac ek-" + _esc(access[row])) if n.kind == "aws" and row in access else ""
            flag = n.flags.get(row, "")
            room = w - 28 - 14 - (_g_sans(flag, 12, True) + 12 if flag else 0)
            parts.append(f'<circle class="bu{tone}{" flagged" if flag else ""}" cx="17" cy="{ry:.1f}" r="2.6"/>'
                         f'<text class="rw" x="27" y="{ry + 4.3:.1f}">{_esc(_g_fit(row, room, lambda t: _g_mono(t, 12.5)))}'
                         "</text>")
            if flag:
                parts.append(f'<text class="ns" x="{w - 14:.0f}" y="{ry + 4.3:.1f}" text-anchor="end">{_esc(flag)}</text>')
    for k, line in enumerate(card.prose):
        parts.append(f'<text class="pr" x="14" y="{top + 13 + _GRAPH_PROSE * k:.1f}">{_esc(line)}</text>')
    if warn:
        parts.append(f'<g class="wb" transform="translate({w - 21:.0f},{badge_y + 14})"><circle r="8.5"/>'
                     '<path d="M0,-4.2V.6M0,3.9V4"/></g>')
    classes = " ".join(["cn", f"k-{n.kind}", f"n-{n.id}", *(f"a-{m}" for m in neighbours)]) + (" warn" if warn else "")
    return (f'<g class="{_esc(classes)}" transform="translate({x:.1f},{y:.1f})"><title>{_esc(n.tip or n.label)}</title>'
            f'<rect class="bg" width="{w:.0f}" height="{h:.0f}" rx="11"/>'
            f'<rect class="fg" x=".5" y=".5" width="{w - 1:.0f}" height="{h - 1:.0f}" rx="10.5"/>{"".join(parts)}</g>')


def _g_captions(graph: CodeGraph, xs: dict[int, float], widths: dict[int, float], column_of: dict[str, int]) -> str:
    """
    The words over the columns: COMES IN, HANDLER, CALLS, REACHES, each over the columns it covers.
    """
    kinds: dict[int, set[str]] = defaultdict(set)
    for n in graph.nodes:
        kinds[column_of[n.id]].add(n.kind)

    def word(found: set[str]) -> str:
        return ("Reaches" if found & {"aws", "http"} else "Handler" if "handler" in found else
                "Comes in" if found & {"event", "env"} else "Calls" if "function" in found else "Start-up")

    groups: list[list[Any]] = []
    for k in sorted(kinds):
        said = word(kinds[k])
        if groups and groups[-1][0] == said:
            groups[-1][2] = k
        else:
            groups.append([said, k, k])
    out = []
    for said, first, last in groups:
        x, right = xs[first], xs[last] + widths[last]
        text = said.upper()
        end = x + _g_sans(text, 12, bold=True) + len(text) * 1.2 + 10
        out.append(f'<text class="cap" x="{x + 2:.0f}" y="25">{_esc(text)}</text>')
        if right - end > 12:
            out.append(f'<path class="capl" d="M{end:.0f},21H{right:.0f}"/>')
    return "".join(out)


def _g_swatch(kind: str) -> str:
    return (f'<svg class="sw ek-{kind}" width="26" height="10" viewBox="0 0 26 10" aria-hidden="true">'
            f'<path class="ln" d="M1.5,5H17"/><path class="ah" d="{_g_head(24.5, 5, 1)}"/></svg>')


@_draws(_Graph, "html")
def _graph_html(block: _Graph, report: _Report) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    graph = block.graph
    if not graph.nodes:
        return title + f'<div class="more">{_esc(block.empty)}</div>'
    cards = _g_cards(graph)
    boxes, routes, width, tall, xs, widths, column_of = _graph_layout(graph, cards)
    ends = _g_anchors(graph, cards, boxes)
    near: dict[str, list[str]] = defaultdict(list)
    access: dict[str, dict[str, str]] = defaultdict(dict)  # an AWS box's operation -> what it does
    for e in graph.edges:
        near[e.source].append(e.target)
        near[e.target].append(e.source)
        if e.kind in _G_ACCESS:
            for name in _g_names(e):
                access[e.target].setdefault(name, e.kind)
    root = report.root or _ROOT
    for n in graph.nodes:  # pointing at a box leaves only it, its arrows and the boxes they join in sight
        report.rules.add(f".{root} .cg:has(.n-{n.id}:hover) .ce:not(.s-{n.id}):not(.t-{n.id}){{opacity:.06}}")
        report.rules.add(f".{root} .cg:has(.n-{n.id}:hover) .cn:not(.n-{n.id}):not(.a-{n.id}){{opacity:.28}}")
        report.rules.add(f".{root} .cg:has(.n-{n.id}:hover) :is(.s-{n.id},.t-{n.id}) .ln{{stroke-width:2.4}}")
    marked: set[tuple[str, str]] = set()  # one loop mark between two boxes, even for two arrows
    taken: list[tuple[float, float]] = []  # where marks are, so the next one goes clear of them
    lanes: dict[frozenset[str], int] = defaultdict(int)  # calls within a column between the same two boxes
    drawn = []
    for i, e in enumerate(graph.edges):
        hot = None
        if e.tone == "warn":  # the lines from or to the row the warning is about (TEAM_TOPIC, not set), else all
            out, into = _g_rows(cards[e.source], e), _g_rows(cards[e.target], e)
            hot = {(a, b) for a in range(len(ends[i][0])) for b in range(len(ends[i][1]))
                   if (a < len(out) and out[a] in cards[e.source].node.flags)
                   or (b < len(into) and into[b] in cards[e.target].node.flags)} or None
        lane = 0
        if column_of[e.source] == column_of[e.target]:
            lane = lanes[frozenset((e.source, e.target))]
            lanes[frozenset((e.source, e.target))] += 1
        drawn.append(_edge_svg(e, graph, boxes, xs, widths, column_of, *ends[i], routes.get(i),
                               (e.source, e.target) not in marked, hot, taken, lane))
        if e.loop:
            marked.add((e.source, e.target))
    edges = "".join(body for body, _ in drawn)
    ports = "".join(dots for _, dots in drawn)
    nodes = "".join(_node_svg(cards[n.id], boxes[n.id], list(dict.fromkeys(near[n.id])), access[n.id])
                    for n in graph.nodes)
    kinds = {e.kind for e in graph.edges}
    pills = [f'<span class="kp">{_g_swatch(k)}{_esc(word)}</span>' for k, word in _G_LEGEND if k in kinds]
    if any(e.guessed for e in graph.edges):
        pills.append(f'<span class="kp">{_g_swatch("guess")}guessed from a method\'s name</span>')
    if any(e.indirect for e in graph.edges):
        pills.append(f'<span class="kp">{_g_swatch("via")}through functions not drawn</span>')
    if any(e.tone == "warn" for e in graph.edges) or any(n.tone == "warn" for n in graph.nodes):
        pills.append('<span class="kp"><svg class="sw kw" width="16" height="16" viewBox="-8 -8 16 16" '
                     'aria-hidden="true"><circle r="7"/><path d="M0,-3.6V.5M0,3.4V3.5"/></svg>'
                     "a warning is about it</span>")
    if any(e.loop for e in graph.edges):
        pills.append('<span class="kp"><svg class="sw kl" width="16" height="16" viewBox="-8 -8 16 16" '
                     f'aria-hidden="true"><circle r="7"/><path transform="scale(.6) translate(-8,-8)" d="{_G_LOOP}"/>'
                     "</svg>in a loop: once per item</span>")
    hidden = (f'<span class="kp kh"><b>+{graph.hidden}</b>{"more function" if graph.hidden == 1 else "more functions"} '
              "not drawn (too deep or too many): the steps below have them all</span>") if graph.hidden else ""
    hint = '<span class="kt">Point at a box to see only what it\'s joined to</span>'
    return (f'{title}<div class="cgb"><div class="cg"><svg viewBox="0 0 {width:.0f} {tall:.0f}" width="{width:.0f}" '
            f'height="{tall:.0f}" style="min-width:{width * 0.72:.0f}px" role="img" '
            f'aria-label="{_esc(block.title or "How the code fits together")}">'
            f'{_g_captions(graph, xs, widths, column_of)}{edges}{nodes}{ports}</svg></div>'
            f'<div class="cgk">{"".join(pills)}{hidden}{hint}</div></div>')


@_draws(_Graph, "text")
def _graph_lines(block: _Graph, report: _Report) -> list[str]:
    out = ["", f"-- {block.title} --"] if block.title else [""]
    graph = block.graph
    if not graph.nodes:
        return out + [block.empty]
    leaving: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in graph.edges:
        leaving[e.source].append(e)
    for n in sorted(graph.nodes, key=lambda n: n.column):
        if n.kind in ("aws", "http"):
            continue
        lines = [f"{line} ({n.flags[line]})" if line in n.flags else line for line in n.lines]
        head = n.label + (f": {', '.join(lines)}" if n.kind in ("event", "env") and n.lines else
                          f"  {n.sub}" if n.sub else "")
        out.append(f"  {head}" + (" (!)" if n.tone == "warn" else ""))
        for e in leaving.get(n.id, []):
            target = graph.node(e.target)
            name = target.label if target else e.target
            said = {"call": f"calls {name}" + (f" ({e.label})" if e.label else ""), "event": f"→ {name}",
                    "env": f"→ {name} reads {e.label}", "client": f"makes the {name} client",
                    "http": f"sends {e.label}"}.get(e.kind) or f"{_EDGE_WORDS.get(e.kind, e.kind)} {name}: {e.label}"
            said += "".join([", in a loop" if e.loop else "", ", guessed from a method's name" if e.guessed else "",
                             ", through functions not drawn" if e.indirect else ""])
            out.append(f"      {said}" + (" (!)" if e.tone == "warn" else ""))
        if n.kind in ("handler", "function", "start") and not leaving.get(n.id):
            out.append("      (does nothing this follows: no AWS calls, own functions or HTTP)")
    if graph.hidden:
        out.append(f"  ({_plural(graph.hidden, 'more function')} not drawn: the steps below have them all)")
    return out
