"""
A state machine drawn as a graph: its top-level states placed in layers from StartAt down (a loop's way back drawn
beside it, a Map's or Parallel's inner states listed in its box), and what to colour it by: the path one execution
took, or where executions stopped over a window.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..models import ExecutionDetail, FailureReport, StateDef, StateMachine
from .definition import resource_label


NODE_WIDTH = 196

NODE_HEIGHT = 46

INNER_LINE = 15

INNER_MOST = 4

GAP_X = 34

GAP_Y = 44

MARGIN = 24

START_SPACE = 34

END_SPACE = 30

_HEAT_SHARES = ((0.5, "h3"), (0.15, "h2"), (0.0, "h1"))


@dataclass
class FlowNode:
    """
    A top-level state in the drawing: where its box sits (x, y the top-left corner), what it does, the states inside
    it (a Map's or Parallel's), and whether the workflow can end there.
    """

    name: str
    type: str
    does: str
    layer: int
    column: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = NODE_WIDTH
    height: float = NODE_HEIGHT
    inner: list[str] = field(default_factory=list)
    ends: bool = False

    @property
    def middle(self) -> float:
        return self.x + self.width / 2


@dataclass
class FlowEdge:
    """
    A transition: from one state to another, and its kind ('next', 'choice', 'catch', or 'loop' for one that goes
    back up to a state above).
    """

    source: str
    target: str
    kind: str = "next"


@dataclass
class FlowLayout:
    """
    The whole drawing: its boxes, its arrows, the state it starts at, and its size.
    """

    nodes: list[FlowNode]
    edges: list[FlowEdge]
    start: str
    width: float
    height: float

    def node(self, name: str) -> FlowNode | None:
        return next((n for n in self.nodes if n.name == name), None)


@dataclass
class FlowMarks:
    """
    What to colour a drawing by: each state's mark ('ok', 'failed', 'running', 'skip', or a heat 'h1'..'h3'), the
    arrows taken, a short note under each state's name, and the state picked (drawn with a ring).
    """

    states: dict[str, str] = field(default_factory=dict)
    taken: set[tuple[str, str]] = field(default_factory=set)
    notes: dict[str, str] = field(default_factory=dict)
    selected: str = ""
    legend: str = ""


def _edges(top: list[StateDef]) -> list[FlowEdge]:
    names = {s.name for s in top}
    edges = []
    for state in top:
        caught = {c.get("Next") for c in state.catches}
        for target in state.next:
            if target not in names:
                continue
            kind = "catch" if target in caught else ("choice" if state.type == "Choice" else "next")
            edges.append(FlowEdge(state.name, target, kind))
    return edges


def _loops(start: str, edges: list[FlowEdge], names: list[str]) -> set[tuple[str, str]]:
    """
    The edges that go back to a state already on the way to them (from StartAt, depth first): drawn as loops, and
    left out when the states are put in layers.
    """
    outgoing: dict[str, list[str]] = {n: [] for n in names}
    for edge in edges:
        outgoing[edge.source].append(edge.target)
    loops: set[tuple[str, str]] = set()
    seen: set[str] = set()
    on_path: list[str] = []

    def visit(name: str) -> None:
        seen.add(name)
        on_path.append(name)
        for target in outgoing.get(name, []):
            if target in on_path:
                loops.add((name, target))
            elif target not in seen:
                visit(target)
        on_path.pop()

    for root in [start] + names:
        if root in outgoing and root not in seen:
            visit(root)
    return loops


def _layers(start: str, edges: list[FlowEdge], names: list[str]) -> dict[str, int]:
    """
    Each state's layer: the longest way to it from StartAt, loops left out, so every arrow but a loop points down.
    """
    incoming: dict[str, list[str]] = {n: [] for n in names}
    for edge in edges:
        if edge.kind != "loop":
            incoming[edge.target].append(edge.source)
    layer: dict[str, int] = {}

    def depth(name: str, guard: frozenset[str]) -> int:
        if name in layer:
            return layer[name]
        before = [depth(s, guard | {name}) for s in incoming[name] if s not in guard]
        layer[name] = 0 if name == start or not before else max(before) + 1
        return layer[name]

    for name in names:
        depth(name, frozenset())
    return layer


def _inner_names(machine: StateMachine, parent: StateDef) -> list[str]:
    prefix = f"{parent.path} > "
    return [s.name for s in machine.states if s.depth == parent.depth + 1 and s.path.startswith(prefix)]


def flow_layout(machine: StateMachine) -> FlowLayout:
    """
    A state machine's top level laid out top-down: a layer per step from StartAt, each layer's states side by side
    in the order the definition gives them (centred on the widest layer), a Map's or Parallel's inner states written
    in its box (at most INNER_MOST), and room above for the start and below for the end.
    """
    top = [s for s in machine.states if s.depth == 0]
    names = [s.name for s in top]
    edges = _edges(top)
    loops = _loops(machine.start_at, edges, names)
    for edge in edges:
        if (edge.source, edge.target) in loops:
            edge.kind = "loop"
    layers = _layers(machine.start_at, edges, names)
    nodes = []
    for state in top:
        inner = _inner_names(machine, state)
        shown = inner[:INNER_MOST] + ([f"… {len(inner) - INNER_MOST} more"] if len(inner) > INNER_MOST else [])
        does = resource_label(state)
        nodes.append(FlowNode(state.name, state.type, "" if does == state.type else does, layers.get(state.name, 0),
                              inner=shown, ends=state.end and state.type not in ("Fail", "Succeed"),
                              height=NODE_HEIGHT + INNER_LINE * len(shown)))
    return _place(nodes, edges, machine.start_at)


def _wanted_lefts(row: list[FlowNode], placed: dict[str, FlowNode], edges: list[FlowEdge], width: float) -> list[float]:
    """
    Where each box of a layer would like to sit: under the middle of the boxes above that lead to it, else as if the
    layer were centred.
    """
    span = len(row) * NODE_WIDTH + (len(row) - 1) * GAP_X
    centred = (width - span) / 2
    wanted = []
    for column, node in enumerate(row):
        above = [placed[e.source].middle for e in edges
                 if e.target == node.name and e.kind != "loop" and e.source in placed]
        default = centred + column * (NODE_WIDTH + GAP_X)
        wanted.append(sum(above) / len(above) - NODE_WIDTH / 2 if above else default)
    return wanted


def _spread(row: list[FlowNode], wanted: list[float], width: float) -> None:
    """
    Places a layer's boxes as near where they want to be as they fit: in that order, at least GAP_X apart, the
    group centred where they want to be on average, inside the margins.
    """
    order = sorted(range(len(row)), key=lambda i: wanted[i])
    lefts = []
    for i in order:
        left = max(wanted[i], lefts[-1] + NODE_WIDTH + GAP_X if lefts else MARGIN)
        lefts.append(left)
    shift = sum(wanted) / len(wanted) - sum(lefts) / len(lefts)
    shift = min(shift, width - MARGIN - NODE_WIDTH - lefts[-1])
    shift = max(shift, MARGIN - lefts[0])
    lefts = [left + shift for left in lefts]
    for column, (i, left) in enumerate(zip(order, lefts)):
        row[i].column, row[i].x = column, left


def _place(nodes: list[FlowNode], edges: list[FlowEdge], start: str) -> FlowLayout:
    by_layer: dict[int, list[FlowNode]] = {}
    for node in nodes:
        by_layer.setdefault(node.layer, []).append(node)
    widest = max((len(row) for row in by_layer.values()), default=1)
    width = MARGIN * 2 + widest * NODE_WIDTH + (widest - 1) * GAP_X + NODE_WIDTH / 2
    placed: dict[str, FlowNode] = {}
    y = MARGIN + START_SPACE
    for layer in sorted(by_layer):
        row = by_layer[layer]
        _spread(row, _wanted_lefts(row, placed, edges, width), width)
        for node in row:
            node.y = y
            placed[node.name] = node
        y += max(n.height for n in row) + GAP_Y
    height = y - GAP_Y + END_SPACE + MARGIN
    return FlowLayout(nodes, edges, start, width, height)


def path_marks(layout: FlowLayout, detail: ExecutionDetail) -> FlowMarks:
    """
    The path one execution took: each top-level state it entered as ok or failed (or still running), the ones it
    never reached as skipped, the arrows between the states it went through one after another, and under each
    state how long it took and how many tries (a Map's or Parallel's: how many of its inner runs failed).
    """
    marks = FlowMarks(legend=f"The path {detail.execution.name} took")
    top = [s for s in detail.steps if s.depth == 0]
    entered = {s.name: s for s in top}
    for node in layout.nodes:
        step = entered.get(node.name)
        if step is None:
            marks.states[node.name] = "skip"
            continue
        failed_inside = sum(1 for s in detail.steps if s.depth > 0 and s.status == "failed"
                            and s.entered >= step.entered and (step.exited is None or s.entered <= step.exited))
        status = {"ended": "failed" if step.type == "Fail" else "ok"}.get(step.status, step.status)
        marks.states[node.name] = "failed" if failed_inside and status != "running" else status
        marks.notes[node.name] = _step_note(step.seconds, len(step.attempts), failed_inside)
    for before, after in zip(top, top[1:]):
        marks.taken.add((before.name, after.name))
    return marks


def _step_note(seconds: float | None, tries: int, failed_inside: int) -> str:
    parts = [f"{seconds:.2f}s" if seconds is not None else "never finished"]
    if tries > 1:
        parts.append(f"{tries} tries")
    if failed_inside:
        parts.append(f"{failed_inside} failed inside")
    return " · ".join(parts)


def failure_marks(layout: FlowLayout, report: FailureReport) -> FlowMarks:
    """
    Where executions stopped over the report's window: each top-level state (a state inside a Map or Parallel
    counts for it) shaded by its share of the failures, h3 for half or more, with how many under its name.
    """
    marks = FlowMarks(legend=f"Where {len(report.failed):,} executions stopped")
    owners = _owners(layout, report)
    counts: Counter[str] = Counter()
    for group in report.groups:
        counts[owners.get(group.state, group.state)] += group.count
    total = sum(counts.values())
    for node in layout.nodes:
        count = counts.get(node.name, 0)
        if not count:
            continue
        share = count / total
        marks.states[node.name] = next(level for floor, level in _HEAT_SHARES if share >= floor)
        marks.notes[node.name] = f"{count:,} stopped here ({share:.0%})"
    return marks


def _owners(layout: FlowLayout, report: FailureReport) -> dict[str, str]:
    """
    Inner state -> the top-level state whose box lists it.
    """
    owners = {}
    for state in report.machine.states:
        if state.depth:
            owners[state.name] = state.path.split(" > ")[0]
    return {name: top for name, top in owners.items() if layout.node(top) is not None}
