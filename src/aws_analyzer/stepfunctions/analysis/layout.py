"""
A state machine drawn as a graph, the way the Step Functions console draws it: a Start and an End, a box per state
with what it calls, a Map's or Parallel's inner states drawn as their own graph inside its box (a Parallel's branches
side by side), and right-angled arrows labelled with the Choice rule or the errors a Catch takes them on. States sit
in layers from StartAt down, and a loop's way back runs up beside them. Then what to colour it by: the path one
execution took, where executions stopped over a window, or where their time goes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..._kit.fmt import _plural

from ..models import ExecutionDetail, FailureReport, StateDef, StateMachine, StateTiming, StepRun, TimingReport
from .definition import resource_label
from .timing import took_text


NODE_WIDTH = 214

NODE_HEIGHT = 62

PILL_WIDTH = 76

PILL_HEIGHT = 28

GAP_X = 40

GAP_Y = 58

MARGIN = 24

PAD = 18

HEADER = 46

BRANCH_GAP = 30

PORT_GAP = 26

LANE_GAP = 20

LANE_CLEARANCE = 8

LABEL_CHARS = 26
"""
At most how many characters of an arrow's label are drawn; the whole of it is in its tooltip.
"""

LABEL_CHAR_WIDTH = 5.9

START = "(start)"

END = "(end)"

CONTAINERS = ("Map", "Parallel")

_STATES_ARN = "arn:aws:states:::"

_HEAT_SHARES = ((0.5, 3), (0.15, 2), (0.0, 1))

SERVICE_NAMES = {
    "lambda": "Lambda", "dynamodb": "DynamoDB", "sns": "SNS", "sqs": "SQS", "s3": "S3", "events": "EventBridge",
    "eventbridge": "EventBridge", "states": "Step Functions", "sfn": "Step Functions", "glue": "Glue",
    "athena": "Athena", "ecs": "ECS", "eks": "EKS", "batch": "Batch", "bedrock": "Bedrock",
    "bedrockagent": "Bedrock Agent", "bedrockagentruntime": "Bedrock Agent", "sagemaker": "SageMaker",
    "apigateway": "API Gateway", "http": "HTTP", "emr": "EMR", "emr-serverless": "EMR Serverless",
    "codebuild": "CodeBuild", "secretsmanager": "Secrets Manager", "ssm": "Systems Manager", "kms": "KMS",
    "cloudwatch": "CloudWatch", "rds": "RDS", "redshiftdata": "Redshift Data", "kinesis": "Kinesis",
    "firehose": "Firehose", "comprehend": "Comprehend", "textract": "Textract", "mediaconvert": "MediaConvert",
}
"""
A service's name as the console writes it, from the name in a Task's resource ('dynamodb' -> 'DynamoDB').
"""

SERVICE_ICONS = {
    "lambda": "lambda", "ecs": "compute", "eks": "compute", "batch": "compute", "codebuild": "compute",
    "dynamodb": "database", "rds": "database", "redshiftdata": "database",
    "sns": "messaging", "sqs": "messaging", "events": "messaging", "eventbridge": "messaging", "states": "messaging",
    "sfn": "messaging", "apigateway": "messaging", "http": "messaging", "s3": "storage",
    "bedrock": "ml", "bedrockagent": "ml", "bedrockagentruntime": "ml", "sagemaker": "ml", "comprehend": "ml",
    "textract": "ml", "glue": "analytics", "athena": "analytics", "emr": "analytics", "emr-serverless": "analytics",
    "kinesis": "analytics", "firehose": "analytics", "secretsmanager": "security", "kms": "security",
    "ssm": "management", "cloudwatch": "management",
}
"""
The icon (and its colour, AWS's for the service's category) a Task box gets for the service it calls; a service not
here gets 'service'.
"""

_FLOW_ICONS = {"Choice": "choice", "Wait": "wait", "Pass": "pass", "Map": "map", "Parallel": "parallel",
               "Succeed": "succeed", "Fail": "fail"}


@dataclass
class FlowNode:
    """
    A box in the drawing: a state (or the Start or End pill), where it sits (x, y the top-left corner), what it does
    ('kind', the console's line under its name: 'Lambda: Invoke', 'Choice state'), the icon it shows, the states
    drawn inside it (a Map's or Parallel's, `inner`), the Parallel's dividers between branches, the box it's drawn
    in (`parent`) and whether the workflow can end there (End: true).
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
    parent: str = ""
    depth: int = 0
    kind: str = ""
    icon: str = "service"
    dividers: list[float] = field(default_factory=list)
    branch: int = 0

    @property
    def middle(self) -> float:
        return self.x + self.width / 2

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def is_container(self) -> bool:
        return bool(self.inner)

    @property
    def is_pill(self) -> bool:
        return self.name in (START, END)


@dataclass
class FlowEdge:
    """
    A transition: from one state to another, its kind ('next', 'choice', 'catch', 'loop' for one that goes back up
    to a state above, 'end' into the End pill), what sends it there (a Choice rule, 'Default', a Catch's errors), the
    corners its line turns at, first to last, and where its label sits.
    """

    source: str
    target: str
    kind: str = "next"
    label: str = ""
    points: list[tuple[float, float]] = field(default_factory=list)
    label_at: tuple[float, float] | None = None


@dataclass
class FlowLayout:
    """
    The whole drawing: its boxes (every state, the ones inside a Map or Parallel too), its arrows, the state it
    starts at, its Start and End pills, and its size.
    """

    nodes: list[FlowNode]
    edges: list[FlowEdge]
    start: str
    width: float
    height: float
    pills: list[FlowNode] = field(default_factory=list)

    def node(self, name: str) -> FlowNode | None:
        return next((n for n in self.nodes + self.pills if n.name == name), None)

    @property
    def top(self) -> list[FlowNode]:
        """
        The top-level states: the boxes not drawn inside a Map or Parallel.
        """
        return [n for n in self.nodes if not n.depth]


@dataclass
class FlowMarks:
    """
    What to colour a drawing by: each state's mark ('ok', 'failed', 'running', 'skip', a failure heat 'h1'..'h3' or a
    time heat 't1'..'t3'), the arrows taken, a short note under each state's name, and the state picked (drawn with a ring).
    """

    states: dict[str, str] = field(default_factory=dict)
    taken: set[tuple[str, str]] = field(default_factory=set)
    notes: dict[str, str] = field(default_factory=dict)
    selected: str = ""
    legend: str = ""


@dataclass
class _Level:
    """
    One graph being laid out: the top level, or the states of a Map or one Parallel branch, with its boxes and
    arrows in its own coordinates, the bottom of each layer's row, and its size.
    """

    nodes: list[FlowNode]
    edges: list[FlowEdge]
    width: float = 0.0
    height: float = 0.0
    inside: list[FlowNode] = field(default_factory=list)
    inside_edges: list[FlowEdge] = field(default_factory=list)


def service_of(state: StateDef) -> str:
    """
    The service a Task calls, as its resource names it: 'lambda', 'dynamodb', 'bedrockagent' (an aws-sdk
    integration's), 'activity', or '' for a state that calls nothing.
    """
    resource = state.resource
    if state.function or resource.startswith("arn:aws:lambda:"):
        return "lambda"
    if ":activity:" in resource:
        return "activity"
    if resource.startswith(_STATES_ARN):
        return resource[len(_STATES_ARN):].removeprefix("aws-sdk:").split(":")[0]
    return ""


def _action_of(state: StateDef) -> str:
    """
    The API action a Task's resource calls, as the console writes it: 'updateItem' -> 'UpdateItem', with
    '(wait)' for a .waitForTaskToken and '(run)' for a .sync one.
    """
    action = state.resource.rsplit(":", 1)[-1]
    suffix = " (wait)" if action.endswith(".waitForTaskToken") else (" (run)" if ".sync" in action else "")
    action = action.split(".")[0]
    return f"{action[:1].upper()}{action[1:]}{suffix}"


def kind_text(state: StateDef) -> str:
    """
    A state's line under its name, as the console writes it: 'Lambda: Invoke', 'DynamoDB: UpdateItem', 'Activity',
    'Choice state'.
    """
    service = service_of(state)
    if service == "lambda":
        return "Lambda: Invoke"
    if service == "activity":
        return "Activity"
    if service:
        return f"{SERVICE_NAMES.get(service, service)}: {_action_of(state)}"
    return f"{state.type} state"


def _icon_of(state: StateDef) -> str:
    if state.type in _FLOW_ICONS:
        return _FLOW_ICONS[state.type]
    return SERVICE_ICONS.get(service_of(state), "service")


def _what_it_does(state: StateDef) -> str:
    does = resource_label(state)
    return "" if does == state.type else does


def _level_edges(states: list[StateDef]) -> list[FlowEdge]:
    names = {s.name for s in states}
    edges = []
    for state in states:
        caught = {c.get("Next") for c in state.catches}
        for target in state.next:
            if target not in names:
                continue
            kind = "catch" if target in caught else ("choice" if state.type == "Choice" else "next")
            edges.append(FlowEdge(state.name, target, kind, state.labels.get(target, "")))
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


def _branches(machine: StateMachine, parent: StateDef) -> list[tuple[str, list[StateDef]]]:
    """
    The graphs nested in a Map or Parallel: (StartAt, its states) for a Map's one and each Parallel branch.
    """
    prefix = f"{parent.path} > "
    inner = [s for s in machine.states if s.depth == parent.depth + 1 and s.path.startswith(prefix)]
    starts = parent.starts or [inner[0].name if inner else ""]
    return [(start, [s for s in inner if s.branch == number]) for number, start in enumerate(starts)]


def _pill(name: str) -> FlowNode:
    return FlowNode(name, "Start" if name == START else "End", "", 0, width=PILL_WIDTH, height=PILL_HEIGHT,
                    icon="start" if name == START else "end")


def _state_node(machine: StateMachine, state: StateDef) -> tuple[FlowNode, list[tuple[_Level, float]]]:
    """
    A state's box, and for a Map or Parallel the graphs drawn in it, each with how far from the box's left it sits:
    the box as wide as its branches side by side (a dashed line between them) and as tall as the tallest.
    """
    ends = state.end and state.type not in ("Fail", "Succeed")
    node = FlowNode(state.name, state.type, _what_it_does(state), 0, ends=ends,
                    depth=state.depth, kind=kind_text(state), icon=_icon_of(state), branch=state.branch)
    if state.type not in CONTAINERS:
        return node, []
    levels = [_lay_level(machine, states, start) for start, states in _branches(machine, state) if states]
    if not levels:
        return node, []
    inner_width = sum(level.width for level in levels) + BRANCH_GAP * (len(levels) - 1)
    node.width = max(NODE_WIDTH, inner_width + 2 * PAD)
    node.height = HEADER + max(level.height for level in levels) + PAD
    node.inner = [n.name for level in levels for n in level.nodes]
    left = (node.width - inner_width) / 2
    placed = []
    for level in levels:
        placed.append((level, left))
        left += level.width + BRANCH_GAP
        node.dividers.append(left - BRANCH_GAP / 2)
    node.dividers.pop()
    return node, placed


def _shift_level(level: _Level, dx: float, dy: float) -> None:
    for node in level.nodes + level.inside:
        node.x, node.y = node.x + dx, node.y + dy
        node.dividers = [x + dx for x in node.dividers]
    for edge in level.edges + level.inside_edges:
        edge.points = [(x + dx, y + dy) for x, y in edge.points]
        if edge.label_at is not None:
            edge.label_at = (edge.label_at[0] + dx, edge.label_at[1] + dy)


def _lay_level(machine: StateMachine, states: list[StateDef], start: str, *, top: bool = False) -> _Level:
    """
    One graph laid out in its own coordinates: its boxes in layers, its arrows routed between them, and the graphs
    inside each Map or Parallel moved into its box. The top level also gets the Start and End pills, every state
    that ends the workflow (a Succeed, a Fail, End: true) leading to End.
    """
    built = [_state_node(machine, state) for state in states]
    nodes = [node for node, _ in built]
    edges = _level_edges(states)
    if top:
        nodes = [_pill(START)] + nodes + [_pill(END)]
        if start in {s.name for s in states}:
            edges.insert(0, FlowEdge(START, start, "next"))
        edges += [FlowEdge(s.name, END, "end") for s in states if s.end]
        start = START
    names = [n.name for n in nodes]
    loops = _loops(start, edges, names)
    for edge in edges:
        if (edge.source, edge.target) in loops:
            edge.kind = "loop"
    layers = _layers(start, edges, names)
    for node in nodes:
        node.layer = layers.get(node.name, 0)
    level = _Level(nodes, edges)
    rows = _place(level, MARGIN if top else 0.0)
    for node in nodes:
        node.dividers = [node.x + x for x in node.dividers]
    _route(level, rows)
    for node, inside in built:
        for inner, left in inside:
            _shift_level(inner, node.x + left, node.y + HEADER)
            for inner_node in inner.nodes + inner.inside:
                inner_node.parent = inner_node.parent or node.name
            level.inside += inner.nodes + inner.inside
            level.inside_edges += inner.edges + inner.inside_edges
    return level


def flow_layout(machine: StateMachine) -> FlowLayout:
    """
    A state machine laid out the way the console draws it, top-down: Start, a layer per step from StartAt (each
    layer's boxes under the boxes that lead to them), a Map's or Parallel's inner states drawn as their own graph
    in its box, and End below every state that ends the workflow.
    """
    top = [s for s in machine.states if s.depth == 0]
    if not top:
        return FlowLayout([], [], machine.start_at, 0.0, 0.0)
    level = _lay_level(machine, top, machine.start_at, top=True)
    pills = [n for n in level.nodes if n.is_pill]
    states = [n for n in level.nodes if not n.is_pill]
    return FlowLayout(states + level.inside, level.edges + level.inside_edges, machine.start_at,
                      level.width, level.height, pills)


def _wanted_lefts(row: list[FlowNode], placed: dict[str, FlowNode], edges: list[FlowEdge], *,
                  widest: float, margin: float) -> list[float]:
    """
    Where each box of a layer would like to sit: under the middle of the boxes above that lead to it, else as if the
    layer were centred under the widest.
    """
    span = sum(n.width for n in row) + GAP_X * (len(row) - 1)
    left = margin + (widest - span) / 2
    wanted = []
    for node in row:
        above = [placed[e.source].middle for e in edges
                 if e.target == node.name and e.kind != "loop" and e.source in placed]
        wanted.append(sum(above) / len(above) - node.width / 2 if above else left)
        left += node.width + GAP_X
    return wanted


def _spread(row: list[FlowNode], wanted: list[float], margin: float) -> None:
    """
    Places a layer's boxes as near where they want to be as they fit: in that order, at least GAP_X apart, the
    group centred where they want to be on average, not left of the margin.
    """
    order = sorted(range(len(row)), key=lambda i: wanted[i])
    lefts: list[float] = []
    for place, i in enumerate(order):
        after = lefts[-1] + row[order[place - 1]].width + GAP_X if lefts else margin
        lefts.append(max(wanted[i], after))
    shift = sum(wanted[i] for i in order) / len(order) - sum(lefts) / len(lefts)
    shift = max(shift, margin - lefts[0])
    for column, (i, left) in enumerate(zip(order, lefts)):
        row[i].column, row[i].x = column, left + shift


def _place(level: _Level, margin: float) -> dict[int, float]:
    """
    Puts each layer's boxes in a row, the rows GAP_Y apart, then moves the whole graph to the margin, and sizes it.
    Returns the bottom of each row, which the arrows between rows turn in the middle below.
    """
    rows: dict[int, list[FlowNode]] = {}
    for node in level.nodes:
        rows.setdefault(node.layer, []).append(node)
    widest = max(sum(n.width for n in row) + GAP_X * (len(row) - 1) for row in rows.values())
    placed: dict[str, FlowNode] = {}
    bottoms: dict[int, float] = {}
    y = margin
    for layer in sorted(rows):
        row = rows[layer]
        _spread(row, _wanted_lefts(row, placed, level.edges, widest=widest, margin=margin), margin)
        tallest = max(n.height for n in row)
        for node in row:
            node.y = y + (tallest - node.height) / 2 if node.is_pill else y
            placed[node.name] = node
        bottoms[layer] = y + tallest
        y = bottoms[layer] + GAP_Y
    leftmost = min(n.x for n in level.nodes)
    for node in level.nodes:
        node.x += margin - leftmost
    level.width = max(n.right for n in level.nodes) + margin
    level.height = y - GAP_Y + margin
    return bottoms


def _port(node: FlowNode, place: int, count: int, step: float = PORT_GAP) -> float:
    """
    Where the place-th of count arrows leaves (or enters) a box: spread across its middle, `step` apart at most.
    """
    if count == 1:
        return node.middle
    step = min(step, (node.width - 24) / (count - 1))
    return node.middle + (place - (count - 1) / 2) * step


def _label_room(edges: list[FlowEdge]) -> float:
    """
    How far apart arrows entering one box sit: PORT_GAP, or wide enough that two of their labels don't overlap.
    """
    labelled = [len(e.label) for e in edges if e.label]
    if len(labelled) < 2:
        return PORT_GAP
    return max(PORT_GAP, min(max(labelled), LABEL_CHARS) * LABEL_CHAR_WIDTH + 20)


def _ports(level: _Level, nodes: dict[str, FlowNode]) -> dict[int, tuple[float, float]]:
    """
    Where each arrow leaves its source's bottom and enters its target's top, so that arrows sharing a box sit side
    by side in the order of the boxes at their other end, and don't cross on the way; a loop leaves and enters on
    the side its lane runs up.
    """
    leaving: dict[int, float] = {}
    entering: dict[int, float] = {}
    for name, node in nodes.items():
        outs = sorted((e for e in level.edges if e.source == name), key=lambda e: nodes[e.target].middle)
        for place, edge in enumerate(outs):
            leaving[id(edge)] = _port(node, place, len(outs))
        ins = sorted((e for e in level.edges if e.target == name), key=lambda e: nodes[e.source].middle)
        step = _label_room(ins)
        for place, edge in enumerate(ins):
            entering[id(edge)] = _port(node, place, len(ins), step)
    return {key: (leaving[key], entering[key]) for key in leaving}


def _is_clear(x: float, boxes: list[FlowNode]) -> bool:
    return all(not (box.x - LANE_CLEARANCE <= x <= box.right + LANE_CLEARANCE) for box in boxes)


def _lane(x1: float, x2: float, boxes: list[FlowNode], taken: list[float]) -> float:
    """
    Where an arrow that skips layers runs down past the boxes between: straight down at either end if that's clear,
    else in the nearest gap beside a box, a little aside from a lane another arrow took.
    """
    candidates = [x2, x1] + [box.x - GAP_X / 2 for box in boxes] + [box.right + GAP_X / 2 for box in boxes]
    clear = [x for x in candidates if _is_clear(x, boxes)]
    lane = min(clear, key=lambda x: abs(x - x2) + abs(x - x1))
    while any(abs(lane - other) < LANE_CLEARANCE for other in taken) and _is_clear(lane + LANE_CLEARANCE, boxes):
        lane += LANE_CLEARANCE
    taken.append(lane)
    return lane


def _without_bends(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """
    The corners of a line with its repeated points and the corners it goes straight through taken out.
    """
    kept: list[tuple[float, float]] = []
    for point in points:
        if kept and abs(kept[-1][0] - point[0]) < 0.5 and abs(kept[-1][1] - point[1]) < 0.5:
            continue
        if len(kept) >= 2:
            (ax, ay), (bx, by) = kept[-2], kept[-1]
            if (abs(ax - bx) < 0.5 and abs(bx - point[0]) < 0.5) or (abs(ay - by) < 0.5 and abs(by - point[1]) < 0.5):
                kept[-1] = point
                continue
        kept.append(point)
    return kept


def _route_down(edge: FlowEdge, ends: tuple[float, float], nodes: dict[str, FlowNode], bottoms: dict[int, float],
                taken: list[float]) -> None:
    """
    An arrow down: out of its source's bottom, along the gap below its row, down to the gap above its target's row
    (past the rows between in a clear lane), and into its target's top, its label just above the target.
    """
    source, target = nodes[edge.source], nodes[edge.target]
    (x1, x2), y1, y2 = ends, source.bottom, target.y
    below = bottoms[source.layer] + GAP_Y / 2
    above = bottoms.get(target.layer - 1, target.y - GAP_Y) + GAP_Y / 2
    if target.layer - source.layer <= 1:
        points = [(x1, y1), (x1, below), (x2, below), (x2, y2)]
    else:
        between = [n for n in nodes.values() if source.layer < n.layer < target.layer]
        lane = _lane(x1, x2, between, taken)
        points = [(x1, y1), (x1, below), (lane, below), (lane, above), (x2, above), (x2, y2)]
    edge.points = _without_bends(points)
    if edge.label:
        edge.label_at = (x2, (above + y2) / 2)


def _route_loop(edge: FlowEdge, ends: tuple[float, float], nodes: dict[str, FlowNode], bottoms: dict[int, float],
                lanes: int) -> None:
    """
    A loop: down out of its source into the gap below its row, along it to a lane beside every box between (on the
    side nearer the source), up the lane to the gap above its target's row, and down into its target's top.
    """
    source, target = nodes[edge.source], nodes[edge.target]
    (x1, x2) = ends
    low, high = sorted((source.layer, target.layer))
    between = [n for n in nodes.values() if low <= n.layer <= high]
    left_edge, right_edge = min(n.x for n in between), max(n.right for n in between)
    goes_left = source.middle - left_edge < right_edge - source.middle
    lane = left_edge - LANE_GAP * (lanes + 1) if goes_left else right_edge + LANE_GAP * (lanes + 1)
    below = bottoms[source.layer] + GAP_Y / 2 + 6
    above = bottoms.get(target.layer - 1, target.y - GAP_Y) + GAP_Y / 2 - 6
    edge.points = _without_bends([(x1, source.bottom), (x1, below), (lane, below), (lane, above), (x2, above),
                                  (x2, target.y)])
    if edge.label:
        edge.label_at = (lane, (below + above) / 2)


def _route(level: _Level, bottoms: dict[int, float]) -> None:
    """
    Every arrow's corners, and the graph widened to hold the loops that run up its right side.
    """
    nodes = {n.name: n for n in level.nodes}
    ports = _ports(level, nodes)
    lanes: list[float] = []
    loops = 0
    for edge in level.edges:
        if edge.kind == "loop":
            _route_loop(edge, ports[id(edge)], nodes, bottoms, loops)
            loops += 1
        else:
            _route_down(edge, ports[id(edge)], nodes, bottoms, lanes)
    leftmost = min((x for edge in level.edges for x, _ in edge.points), default=LANE_GAP)
    if leftmost < LANE_GAP:
        _shift_level(level, LANE_GAP - leftmost, 0.0)
        level.width += LANE_GAP - leftmost
    rightmost = max((x for edge in level.edges for x, _ in edge.points), default=0.0)
    level.width = max(level.width, rightmost + LANE_GAP)


def path_marks(layout: FlowLayout, detail: ExecutionDetail) -> FlowMarks:
    """
    The path one execution took: each top-level state it entered as ok or failed (or still running), the ones it
    never reached as skipped, the arrows between the states it went through one after another, and under each
    state how long it took and how many tries (a Map's or Parallel's: how many of its inner runs failed).
    """
    marks = FlowMarks(legend=f"The path {detail.execution.name} took")
    top = [s for s in detail.steps if s.depth == 0]
    entered = {s.name: s for s in top}
    for node in layout.top:
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
    if top:
        marks.taken.add((START, top[0].name))
        if top[-1].status != "running":
            marks.taken.add((top[-1].name, END))
    _mark_inner_path(marks, layout, detail)
    return marks


def _mark_inner_path(marks: FlowMarks, layout: FlowLayout, detail: ExecutionDetail) -> None:
    """
    How each state drawn inside a Map or Parallel went (as inner_path_marks says), and the arrows between them
    taken: both ends ran, and for a Catch's, its source failed.
    """
    for node in layout.nodes:
        if node.depth:
            runs = [step for step in detail.steps if step.depth and step.name == node.name]
            marks.states[node.name], marks.notes[node.name] = _inner_mark(runs)
    inner = {n.name for n in layout.nodes if n.depth}
    for edge in layout.edges:
        if edge.source not in inner or edge.target not in inner:
            continue
        both_ran = marks.states[edge.source] != "skip" and marks.states[edge.target] != "skip"
        if both_ran and (edge.kind != "catch" or marks.states[edge.source] == "failed"):
            marks.taken.add((edge.source, edge.target))


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
    marks = FlowMarks(legend=f"Where {_plural(len(report.failed), 'execution')} stopped")
    owners = _owners(layout, report)
    counts: Counter[str] = Counter()
    for group in report.groups:
        counts[owners.get(group.state, group.state)] += group.count
    total = sum(counts.values())
    for node in layout.top:
        if counts.get(node.name):
            _mark_share(marks, node.name, counts[node.name], total)
    inner: Counter[str] = Counter()
    for group in report.groups:
        if group.state in owners:
            inner[group.state] += group.count
    for name, count in inner.items():
        if layout.node(name) is not None:
            _mark_share(marks, name, count, total)
    return marks


def _heat(share: float, kind: str) -> str:
    """
    A share's shade: 'h' (failures) or 't' (time), then 3 for half or more, 2 from 15%, else 1.
    """
    level = next(level for floor, level in _HEAT_SHARES if share >= floor)
    return f"{kind}{level}"


def _mark_share(marks: FlowMarks, name: str, count: int, total: int) -> None:
    """
    Shades a state by its share of the failures, with how many under its name.
    """
    share = count / total
    marks.states[name] = _heat(share, "h")
    marks.notes[name] = f"{count:,} stopped here ({share:.0%})"


def _owners(layout: FlowLayout, report: FailureReport) -> dict[str, str]:
    """
    Inner state -> the top-level state whose box lists it.
    """
    owners = {}
    for state in report.machine.states:
        if state.depth:
            owners[state.name] = state.path.split(" > ")[0]
    return {name: top for name, top in owners.items() if layout.node(top) is not None}


def inner_states(machine: StateMachine, name: str) -> list[StateDef]:
    """
    The states inside a Map or Parallel (at any depth), in definition order; none for any other state.
    """
    state = machine.state(name)
    if state is None:
        return []
    inside = f"{state.path} > "
    return [s for s in machine.states if s.path.startswith(inside)]


def owner_of(machine: StateMachine, name: str) -> str:
    """
    The top-level state whose box a state is drawn in: itself, or the Map or Parallel it sits inside.
    """
    state = machine.state(name)
    return state.path.split(" > ")[0] if state is not None and state.depth else name


def _inner_mark(runs: list[StepRun]) -> tuple[str, str]:
    """
    How an inner state's runs in one execution went: a mark and a note ('ran 3 times, 1 failed').
    """
    if not runs:
        return "skip", "never ran"
    failed = sum(1 for run in runs if run.status == "failed")
    unfinished = any(run.status == "running" for run in runs)
    mark = "failed" if failed else ("running" if unfinished else "ok")
    note = f"ran {_plural(len(runs), 'time')}" + (f", {failed:,} failed" if failed else "")
    return mark, note


def inner_path_marks(machine: StateMachine, detail: ExecutionDetail) -> FlowMarks:
    """
    How each state inside a Map or Parallel went in one execution, for the lines that list them (the graph draws
    the top-level boxes only): failed when any of its runs failed, still running, ok, or never ran, with how many
    times it ran and failed.
    """
    marks = FlowMarks()
    for state in machine.states:
        if not state.depth:
            continue
        runs = [step for step in detail.steps if step.depth and step.name == state.name]
        marks.states[state.name], marks.notes[state.name] = _inner_mark(runs)
    return marks


def inner_failure_marks(machine: StateMachine, report: FailureReport) -> FlowMarks:
    """
    Where executions stopped inside a Map or Parallel over the report's window: each inner state shaded by its
    share of all the failures, as failure_marks shades the boxes.
    """
    marks = FlowMarks()
    counts: Counter[str] = Counter()
    for group in report.groups:
        counts[group.state] += group.count
    total = sum(counts.values())
    for state in machine.states:
        if state.depth and counts.get(state.name):
            _mark_share(marks, state.name, counts[state.name], total)
    return marks


def _timing_note(timing: StateTiming) -> str:
    """
    A state's time in a few words, for under its name, what matters most first since a box clips it: its share,
    its tries a run when it retries, then its median run ('72% · 1.3 tries · 9.5s').
    """
    parts = [f"{timing.share:.0%}"] if timing.share is not None else []
    if timing.retried_runs and timing.tries_per_run is not None:
        parts.append(f"{timing.tries_per_run:.2g} tries")
    parts.append(took_text(timing.median) if timing.median is not None else "never finished")
    return " · ".join(parts)


def _sample_words(report: TimingReport) -> str:
    read = len(report.executions)
    if read == report.asked:
        return f"the newest {read:,} succeeded executions"
    return f"{_plural(read, 'succeeded execution')} (all there were)"


def timing_marks(layout: FlowLayout, report: TimingReport) -> FlowMarks:
    """
    Where the time goes over the succeeded executions read: each top-level state shaded by its share of their time
    (t3 for half or more, t2 from 15%), with its median run, its share and, when it retries, its tries a run under its
    name; a state none of them entered stays plain.
    """
    marks = FlowMarks(legend=f"Where the time goes in {_sample_words(report)}")
    marks.notes.update(inner_timing_marks(report.machine, report).notes)
    for node in layout.top:
        timing = report.state(node.name)
        if timing is None or timing.depth:
            continue
        if timing.share is not None:
            marks.states[node.name] = _heat(timing.share, "t")
        marks.notes[node.name] = _timing_note(timing)
    return marks


def inner_timing_marks(machine: StateMachine, report: TimingReport) -> FlowMarks:
    """
    How long each state inside a Map or Parallel takes, for the lines that list them: how many times it runs an
    execution (a Map's once per item) and its median run, unshaded, since its runs can overlap.
    """
    marks = FlowMarks()
    for state in machine.states:
        timing = report.state(state.name) if state.depth else None
        if timing is None:
            continue
        per_execution = timing.runs / max(1, timing.executions)
        median = took_text(timing.median) if timing.median is not None else "never finished"
        marks.notes[state.name] = f"{per_execution:.3g}× an execution · {median}"
    return marks
