"""
The code map as a CodeGraph: its inputs, the handler and its functions, and what it
reaches, with .mermaid().
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ...._kit.fmt import _plural
from ...._kit.text import _clip

from .terms import _LAMBDA_ENV
from .syntax import _a
from .aws_model import _event_kind, _service_name
from .model import AwsCall, CodeMap, CodeSpot
from .findings import _listed, _short_name, code_map_findings


@dataclass
class GraphNode:
    """
    A box in how_it_works()'s graph: what calls the code (the event), what it's configured with (its environment),
    one of its own functions, or what it reaches (an AWS service, the web).
    """

    id: str  # 'event', 'env', 'start', 'f0' (a function), 'aws-s3', 'http'
    kind: str  # 'event', 'env', 'start', 'handler', 'function', 'aws', 'http'
    label: str  # 'handler()', 'S3', 'an SQS batch'
    sub: str = ""  # under the label: 'etl.py line 13', 'reads · writes'
    lines: list[str] = field(default_factory=list)  # what the box lists: its operations, fields or variables
    tone: str = ""  # 'warn' when a warning in the report is about it
    tip: str = ""  # said on hover
    column: int = 0  # left to right: 0 what comes in, 1 the handler, then its functions by depth, last what it reaches
    flags: dict[str, str] = field(default_factory=dict)  # a listed line a warning is about -> a word or two:


    # {'TEAM_TOPIC': 'not set'}, {'Query': 'first page only'}, {'GET https://...': 'no timeout'}


@dataclass
class GraphEdge:
    """
    An arrow in how_it_works()'s graph: a call, an AWS call, an HTTP request, or the event and variables coming in.
    """

    source: str  # a GraphNode id
    target: str
    kind: str  # 'call', 'event', 'env', 'http', 'client' (a client made at start-up), or an AWS call's access:
    # 'reads', 'writes', 'sends', 'runs', 'signs'
    label: str = ""  # what goes along it: 'GetObject, PutItem', 'DB_HOST', 'line 19'
    tone: str = ""  # 'warn' when a warning in the report is about it
    loop: bool = False  # made inside a loop: once per item
    guessed: bool = False  # AWS calls known only by their method's name: their client couldn't be followed
    indirect: bool = False  # made in functions the graph leaves out, below the box it starts from


@dataclass
class CodeGraph:
    """
    How the code fits together, as boxes and arrows: what comes in, its own functions and who calls whom, and what
    they reach outside. code_graph() makes it from a CodeMap; mermaid() writes it for a Markdown file. `shortened`
    counts the boxes whose list ends in '+3 more'.
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    hidden: int = 0  # functions left out to keep the graph readable (too deep, or too many)
    shortened: int = 0

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def what_was_cut(self) -> str:
        """
        What code_graph() left out to keep the graph readable: 'leaves out 4 functions (too deep or too many) and
        shortens 2 boxes' lists', or '' when it drew all of it.
        """
        cut = []
        if self.hidden:
            cut.append(f"leaves out {_plural(self.hidden, 'function')} (too deep or too many)")
        if self.shortened == 1:
            cut.append("shortens 1 box's list")
        elif self.shortened:
            cut.append(f"shortens {self.shortened} boxes' lists")
        return " and ".join(cut)

    def mermaid(self) -> str:
        """
        The graph in Mermaid's language, for a Markdown file, a wiki or a pull request that renders it.
        """
        shapes = {"event": ("([", "])"), "env": ("[/", "/]"), "aws": ("[(", ")]"), "http": ("{{", "}}"),
                  "start": ("[[", "]]")}
        out = ["flowchart LR"]
        for n in self.nodes:
            left, right = shapes.get(n.kind, ("[", "]"))
            text = n.label + (f"<br/><small>{n.sub}</small>" if n.sub else "")
            out.append(f'  {_mermaid_id(n.id)}{left}"{_mermaid_text(text)}"{right}')
        arrows = {"call": "-->", "event": "==>", "env": "-.->"}
        for e in self.edges:
            arrow = arrows.get(e.kind, "-->")
            label = e.label if e.kind != "call" or not e.label.startswith("line ") else ""
            text = f'|"{_mermaid_text(_clip(label, 60))}"|' if label else ""
            out.append(f"  {_mermaid_id(e.source)} {arrow}{text} {_mermaid_id(e.target)}")
        warned = [n.id for n in self.nodes if n.tone == "warn"]
        if warned:
            out.append("  classDef warn stroke:#f59e0b,stroke-width:2px")
            out.append(f"  class {','.join(_mermaid_id(i) for i in warned)} warn")
        return "\n".join(out)


def _mermaid_id(node_id: str) -> str:
    return re.sub(r"\W", "_", node_id)


def _mermaid_text(text: str) -> str:
    """
    Text for a Mermaid label: its quotes and brackets as entities, so a label can hold any code.
    """
    return (text.replace("#", "#35;").replace("&", "#amp;").replace('"', "#quot;").replace("<br/>", "\0").replace("<small>", "\1")
            .replace("</small>", "\2").replace("<", "#lt;").replace(">", "#gt;").replace("\0", "<br/>")
            .replace("\1", "<small>").replace("\2", "</small>"))


def _aws_node(service: str) -> str:
    """
    An AWS service's box id: 'aws-s3'. The service is a literal from the code, so only letters, digits and hyphens
    stay: the id goes into class names and the CSS rules that light up what a box is joined to.
    """
    return "aws-" + (re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-") or "service")


_GRAPH_FUNCTIONS = 24  # functions code_graph() draws, at most (the shallowest first)

_GRAPH_DEPTH = 5  # ...and how deep below the handler

_GRAPH_LINES = 5  # lines a box lists before "+3 more"


def _listed_lines(items: list[str], most: int | None = _GRAPH_LINES) -> list[str]:
    if most is None or len(items) <= most:
        return items
    return items[:most - 1] + [f"+{len(items) - most + 1} more"]


def code_graph(cmap: CodeMap, *, max_functions: int | None = _GRAPH_FUNCTIONS, max_depth: int | None = _GRAPH_DEPTH,
               max_lines: int | None = _GRAPH_LINES) -> CodeGraph:
    """
    How the code fits together, as boxes and arrows, from a CodeMap: on the left what comes in (the event, with the
    fields it reads, and the environment variables), then the handler, what runs at start-up, and its own functions
    by how many calls they are from those (an arrow for each call), and on the right the AWS services and the web it
    reaches, each arrow by what it does (reads, writes, sends, runs). A box or an arrow a warning is about is marked,
    and says which on hover. At most 24 functions, 5 calls deep, the nearest first, and 5 lines a box; `hidden` counts
    the functions left out, and what they do is drawn from the nearest box above them that is, and `shortened` the
    boxes whose list ends in '+3 more'. max_functions=None, max_depth=None and max_lines=None draw all of it (what
    how_it_works_page() saves).
    """
    graph = CodeGraph()
    if cmap.note or cmap.entry is None:
        return graph

    def listed(items: list[str]) -> list[str]:
        """
        A box's lines, counted in `shortened` when they end in '+3 more'.
        """
        if max_lines is not None and len(items) > max_lines:
            graph.shortened += 1
        return _listed_lines(items, max_lines)

    fn = cmap.function
    entry = cmap.entry
    found = code_map_findings(cmap)
    by_name = {f.name: f for f in cmap.functions}
    entry_is_function = entry in by_name
    start_key = "<start>"  # a module's top level: what runs when Lambda loads the code

    # who calls whom; a caller that isn't one of its functions is a module's top level, which runs at start-up
    parents: dict[str, list[tuple[str, str]]] = defaultdict(list)
    children: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in cmap.functions:
        for where in f.called_from:
            key, _, line = where.rpartition(" line ")
            caller = key if key in by_name or key == entry else start_key
            if (caller, line) not in parents[f.name]:
                parents[f.name].append((caller, line))
                children[caller].append((f.name, line))
    routes: dict[str, list[str]] = defaultdict(list)  # a function -> the routes it answers ('GET /items')
    for method, path, key in cmap.routes:
        routes[key].append(f"{method} {path}")
    if not entry_is_function:  # a handler that's an object (a web app's adapter) calls each route
        for key in routes:
            if key in by_name and (entry, "") not in parents[key]:
                parents[key].append((entry, ""))
                children[entry].append((key, ""))
    level: dict[str, int] = {entry: 0, start_key: 0}
    todo = [entry, start_key]
    while todo:
        key = todo.pop(0)
        for child, _ in children.get(key, []):
            if child not in level:
                level[child] = level[key] + 1
                todo.append(child)
    order = {f.name: i for i, f in enumerate(cmap.functions)}
    deep_enough = [f for f in cmap.functions if f.name != entry
                   and (max_depth is None or level.get(f.name, f.depth + 1) <= max_depth)]
    rest = sorted(deep_enough, key=lambda f: (level.get(f.name, f.depth + 1), order[f.name]))
    if max_functions is not None:
        rest = rest[:max_functions - 1]
    shown = ([by_name[entry]] if entry_is_function else []) + rest
    graph.hidden = len(cmap.functions) - len(shown)
    ids = {f.name: f"f{i}" for i, f in enumerate(shown)}
    if not entry_is_function:
        ids[entry] = "f-entry"

    def owner(key: str) -> tuple[str | None, bool]:
        """
        The box what a function (or a module's top level) does is drawn from, and whether that's a box above it
        because the graph leaves the function out.
        """
        if key in ids:
            return ids[key], False
        if key not in by_name:
            return "start", False
        seen, queue = {key}, [key]
        while queue:
            for parent, _ in parents.get(queue.pop(0), []):
                if parent in ids:
                    return ids[parent], True
                if parent == start_key:
                    return "start", True
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        return None, False

    # why a box or arrow is amber: the warnings about it, said on hover
    reasons: dict[str, list[str]] = defaultdict(list)
    for spot in cmap.spots:
        line = f" (line {spot.line})"
        why = ("an HTTP request without a timeout" if spot.kind == "http" and not spot.ok else
               "drops every error" if spot.kind == "swallow" else
               f"writes {spot.detail} outside /tmp" if spot.kind == "write" and not spot.ok else "")
        if why:
            reasons[spot.function].append(why + line)
    for call in cmap.aws:
        if call.pages == "one" and call.operation.startswith(("List", "Scan", "Query")) and not call.guessed:
            reasons[call.function].append(f"{call.label} reads only its first page (line {call.line})")
    handler_reasons = [m.split(":")[0] for level_, m in found if level_ == "warn" and (
        "is async def" in m or "Lambda passes two" in m)]
    unset = set()
    if fn is not None and fn.env_error is None:
        unset = {e.name for e in cmap.env if e.required and e.name not in fn.env_names and e.name not in _LAMBDA_ENV}

    def warned_tip(tip: str, why: list[str]) -> str:
        return tip + ("\nA warning is about it: " + "; ".join(why) if why else "")

    edges: list[GraphEdge] = []
    uses_start = bool(children.get(start_key))

    # calls between its functions, and from start-up
    entry_id = ids[entry]
    seen_calls: set[tuple[str, str]] = set()
    for f in shown:
        for caller, line in parents.get(f.name, []):
            source = "start" if caller == start_key else ids.get(caller)
            if source is None or (source, ids[f.name]) in seen_calls:
                continue
            seen_calls.add((source, ids[f.name]))
            lines = [ln for c, ln in parents[f.name] if c == caller and ln]
            label = ("; ".join(routes[f.name]) if caller == entry and f.name in routes else
                     "line " + ", ".join(lines[:3]) + (" …" if len(lines) > 3 else "") if lines else "")
            edges.append(GraphEdge(source, ids[f.name], "call", label))
    edges.insert(0, GraphEdge("event", entry_id, "event", "the event"))

    # the environment
    names = list(dict.fromkeys(e.name for e in cmap.env))
    reads: dict[str, list[str]] = {}
    for e in cmap.env:
        source, _ = owner(e.function)
        if source is not None and e.name not in reads.setdefault(source, []):
            reads[source].append(e.name)
    for target, read in reads.items():
        uses_start |= target == "start"
        edges.append(GraphEdge("env", target, "env", ", ".join(read),
                               "warn" if any(name in unset for name in read) else ""))

    # what it reaches: AWS
    services: dict[str, list[AwsCall]] = {}
    for call in cmap.aws:
        services.setdefault(call.service, []).append(call)
    reach: list[GraphNode] = []
    for service, calls in services.items():
        node_id = _aws_node(service)
        why = [f"{c.operation} reads only its first page ({c.file} line {c.line})" for c in calls
               if c.pages == "one" and c.operation.startswith(("List", "Scan", "Query")) and not c.guessed]
        ops = list(dict.fromkeys(c.operation for c in calls))
        does = list(dict.fromkeys(c.access for c in calls))
        tip = f"{_service_name(service)}: " + ", ".join(
            f"{c.operation} ({c.permission or 'no permission needed'})" for c in {c.operation: c for c in calls}.values())
        if all(c.guessed for c in calls):
            tip += "\nKnown only by the methods' names: their client couldn't be followed"
        reach.append(GraphNode(node_id, "aws", _service_name(service), " · ".join(does), listed(ops),
                               "warn" if why else "", warned_tip(tip, why), flags={
                                   c.operation: "first page only" for c in calls if c.pages == "one" and not c.guessed
                                   and c.operation.startswith(("List", "Scan", "Query"))}))
        grouped: dict[tuple[str, str, bool], list[AwsCall]] = {}
        for call in calls:
            source, indirect = owner(call.function)
            if source is not None:
                grouped.setdefault((source, call.access, indirect), []).append(call)
        for (source, access, indirect), group in grouped.items():
            uses_start |= source == "start"
            edges.append(GraphEdge(
                source, node_id, access, ", ".join(dict.fromkeys(c.operation for c in group)),
                "warn" if any(c.pages == "one" and c.operation.startswith(("List", "Scan", "Query")) and not c.guessed
                              for c in group) else "",
                any(c.loop for c in group), all(c.guessed for c in group), indirect))

    # ...the clients start-up (or a function it runs) makes, for the services they're for
    clients = [s for s in cmap.spots if s.kind == "client" and s.startup]
    made = []
    for spot in clients:
        source, _ = owner(spot.function)
        target = _aws_node(spot.detail)
        if source is None or not any(n.id == target for n in reach):
            continue
        if source == "start" and spot.detail != "?":
            made.append(_service_name(spot.detail))
        if not any(e.source == source and e.target == target and e.kind == "client" for e in edges):
            uses_start |= source == "start"
            edges.append(GraphEdge(source, target, "client", f"makes its {_service_name(spot.detail)} client"))

    # ...and the web
    http = [s for s in cmap.spots if s.kind == "http"]
    if http:
        slow = [s for s in http if not s.ok]
        reach.append(GraphNode(
            "http", "http", "The web", "HTTP requests", listed(list(dict.fromkeys(s.detail for s in http))),
            "warn" if slow else "", warned_tip("HTTP requests it sends", [
                f"{s.detail} has no timeout ({s.file} line {s.line})" for s in slow]),
            flags={s.detail: "no timeout" for s in slow}))
        by_source: dict[tuple[str, bool], list[CodeSpot]] = {}
        for spot in http:
            source, indirect = owner(spot.function)
            if source is not None:
                by_source.setdefault((source, indirect), []).append(spot)
        for (source, indirect), group in by_source.items():
            uses_start |= source == "start"
            edges.append(GraphEdge(source, "http", "http", ", ".join(dict.fromkeys(s.detail for s in group)),
                                   "warn" if any(not s.ok for s in group) else "", any(s.loop for s in group),
                                   indirect=indirect))

    # the boxes, left to right
    labels = [f.label for f in cmap.event]
    leaves = [label for label in labels if not any(
        other.startswith((label + "[", label + ".", label + " (")) for other in labels)]
    kind = _event_kind(cmap.event)
    graph.nodes.append(GraphNode(
        "event", "event", kind[0].upper() + kind[1:] if kind else "The event", "Lambda calls it with",
        listed(leaves) or ["(no fields read)"],
        tip="The event Lambda passes the handler" + (f": the code reads {', '.join(leaves)}" if leaves else "")))
    if names:
        graph.nodes.append(GraphNode(
            "env", "env", "Environment", "variables it reads", listed(names), "warn" if unset else "",
            warned_tip("Environment variables it reads (names only)",
                       [f"{name} isn't set on the function" for name in names if name in unset]),
            flags={name: "not set" for name in names if name in unset}))
    if uses_start:
        services_made = list(dict.fromkeys(made))
        graph.nodes.append(GraphNode(
            "start", "start", "At start-up", "once per cold start",
            [f"makes {_a(services_made[0])} {services_made[0]} client" if len(services_made) == 1 else
             f"makes {_listed(services_made)} clients"] if services_made else [],
            tip="What runs when Lambda loads the code, before the first call", column=1))
    entry_file = cmap.handler_file or ""
    if not entry_is_function:
        graph.nodes.append(GraphNode(
            "f-entry", "handler", entry.rsplit(".", 1)[-1], f"{entry_file} line {cmap.entry_line}",
            ["an object, not a function"], tip=f"Lambda calls {entry} with the event", column=1))
    for f in shown:
        is_entry = f.name == entry
        why = reasons.get(f.name, []) + (handler_reasons if is_entry else [])
        graph.nodes.append(GraphNode(
            ids[f.name], "handler" if is_entry else "function", f"{_short_name(f.name)}()",
            f"{f.file} line {f.line}", [f.doc] if f.doc else [], "warn" if why else "",
            warned_tip(f"{f.name}({f.args}) in {f.file}, {_plural(f.lines, 'line')}" + (f": {f.doc}" if f.doc else ""),
                       why),
            column=1 + level.get(f.name, f.depth)))
    out_column = 1 + max([n.column for n in graph.nodes if n.kind in ("handler", "function", "start")] + [1])
    for node in reach:
        node.column = out_column
    graph.nodes += reach
    graph.edges = edges
    return graph
