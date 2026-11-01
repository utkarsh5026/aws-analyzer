"""
The explorer's Routes tab: the API's routes as a tree of paths on the left (each folder a line, each route a line to
click, with a tag when anyone can call it and its p95), and on the right the route picked: where a request goes, from
the caller through the authorizer to the integration, the step that fails marked, its settings and the command that
calls it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..helpers import route_sort_key
from ..models import Route, RouteNumbers
from ..analysis import route_numbers_from_calls
from ..view import _route_tone
from .frame import _folder_face, _route_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import APIGatewayExplorer


@dataclass
class _TreeLine:
    """
    One line of the routes' tree at its depth: a folder (a path other routes' paths go on from, shown by its last
    part) or a route, with what its line shows of its path (leaf: '' when it's its folder's own method).
    """

    depth: int
    folder: str = ""
    route: Route | None = None
    leaf: str = ""


def _segments(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part] if path.startswith("/") else [path]


def _prefix(segments: list[str], count: int) -> str:
    return "/" + "/".join(segments[:count])


def _folders(routes: list[Route]) -> set[str]:
    """
    The paths other routes' paths go on from ('/orders' when there's '/orders/{id}'): each a folder in the tree.
    """
    folders: set[str] = set()
    for route in routes:
        segments = _segments(route.path)
        folders.update(_prefix(segments, count) for count in range(1, len(segments)))
    return folders


def _route_tree(routes: list[Route]) -> list[_TreeLine]:
    """
    Routes as a tree of their paths, by path and then by method (GET, POST, PUT, PATCH, DELETE, the rest): a folder
    line for each path others go on from, with its own methods under it, then the paths under it, each shown by its
    last part. A WebSocket route key is a line at the top.
    """
    folders = _folders(routes)
    lines: list[_TreeLine] = []
    seen: set[str] = set()
    for route in sorted(routes, key=lambda r: route_sort_key(r.key)):
        segments = _segments(route.path)
        inside = [_prefix(segments, n) for n in range(1, len(segments) + 1) if _prefix(segments, n) in folders]
        for depth, folder in enumerate(inside):
            if folder not in seen:
                seen.add(folder)
                lines.append(_TreeLine(depth, folder="/" + folder.rsplit("/", 1)[-1]))
        is_folder = route.path.startswith("/") and _prefix(segments, len(segments)) in folders
        leaf = "" if is_folder else ("/" + segments[-1] if route.path.startswith("/") and segments else route.path)
        lines.append(_TreeLine(len(inside), route=route, leaf=leaf or ("/" if not segments else "")))
    return lines


def _route_text(route: Route) -> tuple[str, ...]:
    target = route.integration.target if route.integration else ""
    return (route.key, target, route.auth)


class _RoutesTab:
    """
    The Routes tab, a part of APIGatewayExplorer: what it keeps is `route_query` (the search box's text) and
    `route_key`, the route shown on the right.
    """

    def _routes_page(self: APIGatewayExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.route_query, self.route_key = "", ""
        self._route_rows: list[_Line] = []
        self.route_find = w.Text(placeholder="Filter routes: path, method, function…", continuous_update=True,
                                 layout=layout(flex="1 1 auto", width="auto"))
        self.route_find.add_class("agx-find")
        self.route_find.observe(self._safely(self._route_typed), names="value")
        finder = w.HBox([self.route_find], layout=layout(width="100%", align_items="center"))
        finder.add_class("agx-filters")
        self.route_rows = w.VBox(layout=layout(width="100%"))
        self.route_rows.add_class("agx-rows")
        self.route_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.route_count], layout=layout(width="100%"))
        count.add_class("agx-pager")
        left = w.VBox([finder, self.route_rows, count])
        for name in ("agx-left", "agx-routes"):
            left.add_class(name)
        self.route_view = w.HTML(layout=layout(width="100%"))
        right = w.VBox([self.route_view])
        right.add_class("agx-right")
        split = w.HBox([left, right])
        split.add_class("agx-split")
        return split

    def _route_numbers(self: APIGatewayExplorer) -> dict[str, RouteNumbers]:
        """
        Each route's numbers: from the stage's detailed metrics when it has them, else from the calls read.
        """
        if self.report is not None and self.report.route_numbers:
            return self.report.route_numbers
        if self.page is not None and self.page.calls:
            return route_numbers_from_calls(self.page.calls)
        return {}

    def _route_typed(self: APIGatewayExplorer, change: dict[str, Any]) -> None:
        self.route_query = str(change["new"] or "").strip()
        self._draw_route_rows()

    def _first_route(self: APIGatewayExplorer) -> str:
        """
        The route to show first: the one closest to the time limit, else one anyone can call, else the busiest.
        """
        api = self.api
        if api is None or not api.routes:
            return ""
        numbers = self._route_numbers()
        slow = [r for r in api.routes if _route_tone(api, r, numbers.get(r.key))]
        if slow:
            return slow[0].key
        if api.open_routes:
            return api.open_routes[0].key
        busiest = max(api.routes, key=lambda r: numbers[r.key].calls if r.key in numbers else 0)
        return busiest.key

    def _draw_routes(self: APIGatewayExplorer) -> None:
        """
        The Routes tab, drawn anew: the tree, and the route shown (the one picked, else the first worth a look).
        """
        if self.api is None:
            return
        if not self.route_key or self.api.route(self.route_key) is None:
            self.route_key = self._first_route()
        self._draw_route_rows()
        self._draw_route()

    def _draw_route_rows(self: APIGatewayExplorer) -> None:
        api = self.api
        routes = api.routes if api else []
        if self.route_query:
            routes = [r for r in routes if search_rank(self.route_query, _route_text(r)) is not None]
        tree = _route_tree(routes)
        numbers = self._route_numbers()
        words = self.route_query.split()
        children: list[Any] = []
        used = 0
        for line in tree:
            if line.route is None:
                children.append(self._w.HTML(_folder_face(line.folder, line.depth)))
                continue
            while len(self._route_rows) <= used:
                self._route_rows.append(_Line(self, self._clicked_route))
            row = self._route_rows[used]
            used += 1
            found = numbers.get(line.route.key)
            row.key = line.route.key
            self._set(row.face, _route_face(line.route, line.leaf, found, _route_tone(api, line.route, found), line.depth,
                                            words))
            _class_if(row.box, "agx-on", line.route.key == self.route_key)
            children.append(row.box)
        if not children:
            text = "No route matches the filter." if self.route_query else "This API has no routes."
            children = [self._w.HTML(f'<div class="agx-empty">{_esc(text)}</div>')]
        self.route_rows.children = children
        total = len(api.routes) if api else 0
        shown = f"{used:,} of {total:,} routes" if used != total else f"{total:,} routes"
        self._set(self.route_count, _esc(shown))

    def _draw_route(self: APIGatewayExplorer) -> None:
        api = self.api
        route = api.route(self.route_key) if api is not None and self.route_key else None
        if route is None:
            self._draw("routes", self.route_view, [_Note("Pick a route on the left to see where it goes.")])
            return
        stage = self.stage.name if self.stage else None
        self._draw("routes", self.route_view, self.ui._route_blocks(api, route, self._route_numbers().get(route.key),
                                                                     stage))

    def _clicked_route(self: APIGatewayExplorer, line: _Line) -> None:
        if not line.key:
            return
        self.route_key = line.key
        for row in self._route_rows:
            _class_if(row.box, "agx-on", row.key == line.key)
        self._draw_route()
        self._status(f"{line.key}: where a request goes, who can call it and the command that calls it.")
