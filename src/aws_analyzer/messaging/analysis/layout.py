"""
Where each hop of follow()'s map goes when it's drawn as a diagram, pure: a column per step from where the map starts
(what sends to it in the column before), each hop on its own row under the one before, and a hop with branches level
with the middle of them. A hop with many branches shows the ones with problems first and folds the rest into one
"+N more" box, so a topic with a hundred subscribers draws like one with six.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Hop

MOST_BRANCHES = 6


@dataclass
class MapNode:
    """
    One box of the diagram: the hop it stands for (None for a "+N more" box, which says how many in `folded`), its
    column and row, and its place in the map's hops (-1 for a folded box).
    """

    hop: Hop | None
    column: int
    row: float
    index: int = -1
    folded: int = 0


@dataclass
class MapEdge:
    """
    An arrow from one box to another (their places in MapLayout.nodes), its tone when the hop it leads to has a
    problem, and how a message gets there.
    """

    start: int
    end: int
    tone: str = ""
    via: str = ""


@dataclass
class MapLayout:
    """
    The diagram's boxes and arrows, how many columns and rows they take, and which box the map starts from.
    """

    nodes: list[MapNode] = field(default_factory=list)
    start: int = -1
    edges: list[MapEdge] = field(default_factory=list)
    columns: int = 0
    rows: int = 0


def _children(hops: list[Hop], index: int) -> list[int]:
    """
    The hops one level under hops[index]: those after it, one deeper, before the next at its depth or above.
    """
    depth = hops[index].depth
    found = []
    for later in range(index + 1, len(hops)):
        if hops[later].depth <= depth:
            break
        if hops[later].depth == depth + 1:
            found.append(later)
    return found


def _once_each(hops: list[Hop], children: list[int]) -> list[int]:
    """
    The branches with a queue reached two ways drawn once (follow() lists it again as "the same queue, above").
    """
    seen: set[str] = set()
    unique = []
    for index in children:
        key = hops[index].arn or hops[index].name
        if key not in seen:
            seen.add(key)
            unique.append(index)
    return unique


def _kept(hops: list[Hop], children: list[int]) -> tuple[list[int], int]:
    """
    The branches drawn, the ones with problems first then in order, and how many more there are.
    """
    children = _once_each(hops, children)
    if len(children) <= MOST_BRANCHES:
        return children, 0
    with_problems = [i for i in children if hops[i].tone]
    others = [i for i in children if not hops[i].tone]
    kept = (with_problems + others)[:MOST_BRANCHES - 1]
    return sorted(kept), len(children) - len(kept)


class _Placer:
    """
    Gives each hop under the root a column and a row, leaves one row after another and each parent level with the
    middle of its branches.
    """

    def __init__(self, hops: list[Hop], first_column: int):
        self.hops, self.first_column = hops, first_column
        self.layout = MapLayout()
        self.next_row = 0

    def place(self, index: int) -> int:
        """
        Places hops[index] and everything under it; returns its box's place in the layout.
        """
        column = self.first_column + self.hops[index].depth
        kept, folded = _kept(self.hops, _children(self.hops, index))
        child_nodes = [self.place(child) for child in kept]
        if folded:
            child_nodes.append(self._folded_box(column + 1, folded))
        if child_nodes:
            rows = [self.layout.nodes[n].row for n in child_nodes]
            row = (rows[0] + rows[-1]) / 2
        else:
            row = float(self.next_row)
            self.next_row += 1
        node = len(self.layout.nodes)
        self.layout.nodes.append(MapNode(self.hops[index], column, row, index))
        for child in child_nodes:
            hop = self.layout.nodes[child].hop
            tone, via = (hop.tone, hop.via) if hop else ("", "")
            self.layout.edges.append(MapEdge(node, child, tone, via))
        return node

    def _folded_box(self, column: int, folded: int) -> int:
        node = len(self.layout.nodes)
        self.layout.nodes.append(MapNode(None, column, float(self.next_row), folded=folded))
        self.next_row += 1
        return node


def map_layout(hops: list[Hop]) -> MapLayout:
    """
    Boxes and arrows for follow()'s hops: what sends to the start in the first column (when anything does), the start
    next, and each step on from it a column further, every box on a row of its own.
    """
    upstream = [i for i, hop in enumerate(hops) if hop.upstream]
    downstream = [i for i, hop in enumerate(hops) if not hop.upstream]
    if not downstream:
        return MapLayout()
    first_column = 1 if upstream else 0
    placer = _Placer(hops, first_column)
    root = placer.place(downstream[0])
    layout = placer.layout
    layout.start = root
    root_row = layout.nodes[root].row
    top = root_row - (len(upstream) - 1) / 2
    for offset, index in enumerate(upstream):
        node = len(layout.nodes)
        layout.nodes.append(MapNode(hops[index], 0, top + offset, index))
        layout.edges.append(MapEdge(node, root, hops[index].tone, hops[index].via))
    lowest = min(node.row for node in layout.nodes)
    for node in layout.nodes:
        node.row -= lowest
    layout.columns = max(node.column for node in layout.nodes) + 1
    layout.rows = int(max(node.row for node in layout.nodes)) + 1
    return layout
