"""
The explorer's APIs tab: the region's findings, then a line per API with a search box, a chip per kind of API
(failing, REST, HTTP, WebSocket, open routes, unused) and columns that sort by a click, as the Step Functions
explorer's State machines tab does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Findings, _Note, _Title
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..models import Api, ApiNumbers
from ..analysis import monthly_cost
from ..view import _auth_cell
from .frame import _api_face, _api_tone
from .widgets import _Line

if TYPE_CHECKING:
    from .window import APIGatewayExplorer


_API_COLUMNS = (
    ("name", "API", "Sort by name"),
    ("requests", "Requests", "Sort by requests in the last 7 days"),
    ("errors", "5xx", "Sort by the share of calls that got a server error"),
    ("p95", "p95", "Sort by how long the slowest 5% of calls take"),
    ("cost", "$ / month", "Sort by the estimated monthly cost"),
    ("auth", "Auth", "Sort by who can call it: open to anyone first"),
)

_API_CHIPS = (
    ("", "All", "all"),
    ("failing", "Failing", "bad"),
    ("REST", "REST", "none"),
    ("HTTP", "HTTP", "none"),
    ("WEBSOCKET", "WebSocket", "none"),
    ("open", "Open routes", "warn"),
    ("idle", "Unused", "none"),
)

_CHIP_TIPS = {
    "": "Show every API",
    "failing": "Show only the APIs returning server errors on 1% of calls or more",
    "REST": "Show only the REST APIs",
    "HTTP": "Show only the HTTP APIs",
    "WEBSOCKET": "Show only the WebSocket APIs",
    "open": "Show only the APIs with routes anyone can call",
    "idle": "Show only the APIs no one called in the last 7 days",
}

_ASCENDING_FIRST = ("name", "auth")

_DEFAULT_SORT = "errors"

_NO_VALUE = -1.0


@dataclass
class _ApiLine:
    """
    What an API's line is drawn and sorted from: the API, its CloudWatch numbers (None when they weren't read), its
    estimated monthly cost and who can call it.
    """

    api: Api
    numbers: ApiNumbers | None
    cost: float | None
    auth: str


def _in_chip(line: _ApiLine, chip: str) -> bool:
    """
    Whether an API's line belongs under a chip ('' is every one).
    """
    api, numbers = line.api, line.numbers
    if chip == "failing":
        return _api_tone(numbers) == "bad"
    if chip in ("REST", "HTTP", "WEBSOCKET"):
        return api.kind == chip
    if chip == "open":
        return bool(api.open_routes)
    if chip == "idle":
        return numbers is not None and numbers.requests == 0
    return True


def _column_value(line: _ApiLine, column: str) -> tuple[Any, ...]:
    """
    What a column sorts a line by; an API without numbers sorts below every one with them.
    """
    numbers = line.numbers
    if column == "name":
        return (line.api.name.lower(),)
    if column == "auth":
        return (0 if line.auth.startswith("none") else 1, line.auth)
    if column == "cost":
        return (line.cost if line.cost is not None else _NO_VALUE,)
    if numbers is None:
        return (_NO_VALUE, _NO_VALUE)
    if column == "requests":
        return (float(numbers.requests),)
    if column == "p95":
        return (numbers.p95_ms if numbers.p95_ms is not None else _NO_VALUE,)
    return (numbers.rate_5xx, float(numbers.errors_5xx))


def _searched_text(api: Api) -> tuple[str, ...]:
    """
    What the search box looks through: the name first, then the ID, description, kind, routes and functions.
    """
    return (api.name, api.id, api.description, api.kind_label, " ".join(r.key for r in api.routes),
            " ".join(api.functions))


class _ApisTab:
    """
    The APIs tab, a part of APIGatewayExplorer: what it keeps is `api_query` (the search box's text), `api_chip`,
    `api_sort` and `api_descending`, and `apis_shown`, the lines in the order drawn.
    """

    def _apis_page(self: APIGatewayExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.api_query, self.api_chip = "", ""
        self.api_sort, self.api_descending = _DEFAULT_SORT, True
        self.apis_shown: list[_ApiLine] = []
        self.api_view = w.HTML(layout=layout(width="100%"))
        self.api_find = w.Text(placeholder="Search APIs: name, ID, route, function…", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.api_find.add_class("agx-find")
        self.api_find.observe(self._safely(self._api_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("agx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.api_find, "value", "")))
        finder = w.HBox([self.api_find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("agx-filters")
        self.api_chips = w.HBox(layout=layout(width="100%"))
        self.api_chips.add_class("agx-chips")
        self.api_head = w.HBox([w.HTML(), *self._column_buttons()], layout=layout(width="100%"))
        self.api_head.add_class("agx-lhead")
        self.apis_box = w.VBox([self.api_head], layout=layout(width="100%"))
        self.apis_box.add_class("agx-machines")
        self.api_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.api_count], layout=layout(width="100%"))
        count.add_class("agx-pager")
        return w.VBox([self.api_view, finder, self.api_chips, self.apis_box, count])

    def _column_buttons(self: APIGatewayExplorer) -> list[Any]:
        w, layout = self._w, self._w.Layout
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip in _API_COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            for name in ("agx-col", f"agx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_apis_by(key)))
            self.column_buttons[key] = button
        return list(self.column_buttons.values())

    def _api_lines(self: APIGatewayExplorer) -> list[_ApiLine]:
        apis = self.overview.apis if self.overview else []
        lines = []
        for api in apis:
            numbers = self.numbers.get(api.id)
            auth = str(_auth_cell(api))
            lines.append(_ApiLine(api, numbers, monthly_cost(api, numbers, self.core.prices), auth))
        return lines

    def _api_typed(self: APIGatewayExplorer, change: dict[str, Any]) -> None:
        self.api_query = str(change["new"] or "").strip()
        self._draw_api_rows()

    def _pick_api_chip(self: APIGatewayExplorer, chip: str) -> None:
        self.api_chip = "" if chip == self.api_chip else chip
        self._draw_api_chips()
        self._draw_api_rows()

    def _sort_apis_by(self: APIGatewayExplorer, column: str) -> None:
        if column == self.api_sort:
            self.api_descending = not self.api_descending
        else:
            self.api_sort, self.api_descending = column, column not in _ASCENDING_FIRST
        self._draw_api_rows()

    def _draw_apis(self: APIGatewayExplorer) -> None:
        """
        The APIs tab: the region's findings, then the chips and the lines, searched, filtered and sorted.
        """
        overview = self.overview
        if overview is None:
            return
        blocks = self.ui._overview_blocks(overview) if overview.apis else [_Note(
            f"No APIs in {overview.region}. They're regional: explore(region='us-west-2') looks in another region.")]
        findings = [b for b in blocks if isinstance(b, (_Title, _Findings))] if overview.apis else blocks
        self._draw("apis", self.api_view, findings)
        self._draw_api_chips()
        self._draw_api_rows()

    def _draw_api_chips(self: APIGatewayExplorer) -> None:
        """
        A chip per kind of API there is, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        lines = self._api_lines()
        chips = []
        for key, label, tone in _API_CHIPS if lines else ():
            count = sum(1 for line in lines if _in_chip(line, key))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=f"{_CHIP_TIPS[key]}"
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("agx-chip", f"agx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "agx-on", key == self.api_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_api_chip(key)))
            chips.append(chip)
        self.api_chips.children = chips

    def _shown_lines(self: APIGatewayExplorer) -> list[_ApiLine]:
        """
        The lines the search and the chip leave, in the column's order (by name first, so equal values stay A to Z).
        """
        lines = [line for line in self._api_lines() if _in_chip(line, self.api_chip)]
        if self.api_query:
            lines = [line for line in lines if search_rank(self.api_query, _searched_text(line.api)) is not None]
        lines.sort(key=lambda line: line.api.name.lower())
        lines.sort(key=lambda line: _column_value(line, self.api_sort), reverse=self.api_descending)
        return lines

    def _draw_api_rows(self: APIGatewayExplorer) -> None:
        lines = self._shown_lines()
        self.apis_shown = lines
        self._draw_column_heads()
        while len(self._api_rows) < len(lines):
            self._api_rows.append(_Line(self, self._clicked_api))
        words = self.api_query.split()
        for row, line in zip(self._api_rows, lines):
            row.key = line.api.id
            self._set(row.face, _api_face(line.api, line.numbers, line.cost, line.auth, words))
            _class_if(row.box, "agx-on", self.api is not None and line.api.id == self.api.id)
        rows = [row.box for row in self._api_rows[:len(lines)]]
        self.apis_box.children = [self.api_head, *rows, *self._nothing_shown(lines)]
        self._draw_api_count(lines)

    def _draw_column_heads(self: APIGatewayExplorer) -> None:
        for key, button in self.column_buttons.items():
            _class_if(button, "agx-on", key == self.api_sort)
            label = next(label for column, label, _ in _API_COLUMNS if column == key)
            arrow = (" ▾" if self.api_descending else " ▴") if key == self.api_sort else ""
            if button.description != label + arrow:
                button.description = label + arrow

    def _nothing_shown(self: APIGatewayExplorer, lines: list[_ApiLine]) -> list[Any]:
        """
        The line under the header when the search and the chip leave nothing (none when something is shown).
        """
        if lines or not self._api_lines():
            return []
        what = f"matches {self.api_query!r}" if self.api_query else "is under this chip"
        return [self._w.HTML(f'<div class="agx-empty">No API {_esc(what)}.</div>')]

    def _draw_api_count(self: APIGatewayExplorer, lines: list[_ApiLine]) -> None:
        total = len(self._api_lines())
        text = f"{total:,} APIs" if len(lines) == total else f"{len(lines):,} of {total:,} APIs"
        self._set(self.api_count, _esc(text) if total else "")

    def _clicked_api(self: APIGatewayExplorer, line: _Line) -> None:
        if line.key:
            self._open_api(line.key)
            self._show_tab("overview")
