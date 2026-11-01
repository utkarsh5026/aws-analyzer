"""
The explorer's State machines tab: the region's findings, then a line per state machine with a search box, a chip per
kind of state machine (failing, Express, logging off, ...) and columns that sort by a click, as the Lambda explorer's
Functions tab does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _plural
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..models import MachineNumbers, StateMachine
from ..analysis import monthly_cost
from .frame import _machine_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import StepFunctionsExplorer


_MACHINE_COLUMNS = (
    ("name", "State machine", "Sort by name"),
    ("started", "Executions", "Sort by executions in the last 7 days"),
    ("unsuccessful", "Didn't succeed", "Sort by the share of executions that didn't succeed"),
    ("duration", "Avg run", "Sort by the average run time"),
    ("cost", "$ / month", "Sort by the estimated monthly cost"),
    ("logging", "Logging", "Sort by logging: off first, then errors only, then everything"),
)

_MACHINE_CHIPS = (
    ("", "All", "all"),
    ("failing", "Failing", "bad"),
    ("express", "Express", "none"),
    ("standard", "Standard", "none"),
    ("unlogged", "Logging off", "none"),
    ("idle", "Not run", "none"),
)

_CHIP_TIPS = {
    "": "Show every state machine",
    "failing": "Show only the state machines with executions that didn't succeed in the last 7 days",
    "express": "Show only the Express workflows",
    "standard": "Show only the Standard workflows",
    "unlogged": "Show only the state machines with logging off",
    "idle": "Show only the state machines with no executions in the last 7 days",
}

_ASCENDING_FIRST = ("name", "logging")

_DEFAULT_SORT = "unsuccessful"

_LOG_LEVELS = {"OFF": 0, "FATAL": 1, "ERROR": 2, "ALL": 3}

_NO_VALUE = -1.0


@dataclass
class _MachineLine:
    """
    What a state machine's line in the list is drawn and sorted from: the state machine, its CloudWatch numbers (None
    when they weren't read) and its estimated monthly cost.
    """

    machine: StateMachine
    numbers: MachineNumbers | None
    cost: float | None


def _in_chip(line: _MachineLine, chip: str) -> bool:
    """
    Whether a state machine's line belongs under a chip ('' is every one).
    """
    machine, numbers = line.machine, line.numbers
    if chip == "failing":
        return numbers is not None and numbers.unsuccessful > 0
    if chip == "express":
        return machine.is_express
    if chip == "standard":
        return not machine.is_express
    if chip == "unlogged":
        return machine.log_level == "OFF"
    if chip == "idle":
        return numbers is not None and numbers.started == 0
    return True


def _column_value(line: _MachineLine, column: str) -> tuple[Any, ...]:
    """
    What a column sorts a line by; a state machine without numbers sorts below every one with them.
    """
    numbers = line.numbers
    if column == "name":
        return (line.machine.name.lower(),)
    if column == "logging":
        return (_LOG_LEVELS.get(line.machine.log_level, len(_LOG_LEVELS)), line.machine.name.lower())
    if column == "cost":
        return (line.cost if line.cost is not None else _NO_VALUE,)
    if numbers is None:
        return (_NO_VALUE, _NO_VALUE)
    if column == "started":
        return (float(numbers.started),)
    if column == "duration":
        return (numbers.avg_ms if numbers.avg_ms is not None else _NO_VALUE,)
    return (numbers.failure_rate, float(numbers.unsuccessful))


def _searched_text(machine: StateMachine) -> tuple[str, ...]:
    """
    What the search box looks through: the name first, then the description, type, functions it calls and logging.
    """
    return (machine.name, machine.description or "", machine.type, " ".join(machine.functions), machine.log_level)


class _MachinesTab:
    """
    The State machines tab, a part of StepFunctionsExplorer: what it keeps is `machine_query` (the search box's text),
    `machine_chip`, `machine_sort` and `machine_descending`, and `machines_shown`, the lines in the order drawn.
    """

    def _machines_page(self: StepFunctionsExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.machine_query, self.machine_chip = "", ""
        self.machine_sort, self.machine_descending = _DEFAULT_SORT, True
        self.machines_shown: list[_MachineLine] = []
        self.machine_view = w.HTML(layout=layout(width="100%"))
        self.machine_find = w.Text(placeholder="Search state machines: name, description, type, function…",
                                   continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.machine_find.add_class("sfx-find")
        self.machine_find.observe(self._safely(self._machine_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("sfx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.machine_find, "value", "")))
        finder = w.HBox([self.machine_find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("sfx-filters")
        self.machine_chips = w.HBox(layout=layout(width="100%"))
        self.machine_chips.add_class("sfx-chips")
        self.machine_head = w.HBox([w.HTML(), *self._column_buttons()], layout=layout(width="100%"))
        self.machine_head.add_class("sfx-lhead")
        self.machines_box = w.VBox([self.machine_head], layout=layout(width="100%"))
        self.machines_box.add_class("sfx-machines")
        self.machine_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.machine_count], layout=layout(width="100%"))
        count.add_class("sfx-pager")
        return w.VBox([self.machine_view, finder, self.machine_chips, self.machines_box, count])

    def _column_buttons(self: StepFunctionsExplorer) -> list[Any]:
        w, layout = self._w, self._w.Layout
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip in _MACHINE_COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            for name in ("sfx-col", f"sfx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_machines_by(key)))
            self.column_buttons[key] = button
        return list(self.column_buttons.values())

    def _machine_lines(self: StepFunctionsExplorer) -> list[_MachineLine]:
        machines = self.overview.machines if self.overview else []
        lines = []
        for machine in machines:
            numbers = self.numbers.get(machine.arn)
            lines.append(_MachineLine(machine, numbers, monthly_cost(machine, numbers, self.core.prices)))
        return lines

    def _machine_typed(self: StepFunctionsExplorer, change: dict[str, Any]) -> None:
        self.machine_query = str(change["new"] or "").strip()
        self._draw_machine_rows()

    def _pick_machine_chip(self: StepFunctionsExplorer, chip: str) -> None:
        self.machine_chip = "" if chip == self.machine_chip else chip
        self._draw_machine_chips()
        self._draw_machine_rows()

    def _sort_machines_by(self: StepFunctionsExplorer, column: str) -> None:
        if column == self.machine_sort:
            self.machine_descending = not self.machine_descending
        else:
            self.machine_sort, self.machine_descending = column, column not in _ASCENDING_FIRST
        self._draw_machine_rows()

    def _draw_machines(self: StepFunctionsExplorer) -> None:
        """
        The State machines tab: the region's findings, then the chips and the lines, searched, filtered and sorted.
        """
        overview = self.overview
        if overview is None:
            return
        blocks = self.ui._overview_blocks(overview) if overview.machines else [_Note(
            f"No state machines in {overview.region}. They're regional: explore(region='us-west-2') looks in another "
            "region.")]
        findings = [b for b in blocks if type(b).__name__ in ("_Title", "_Findings")]
        self._draw("machines", self.machine_view, findings)
        self._draw_machine_chips()
        self._draw_machine_rows()

    def _draw_machine_chips(self: StepFunctionsExplorer) -> None:
        """
        A chip per kind of state machine there is, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        lines = self._machine_lines()
        chips = []
        for key, label, tone in _MACHINE_CHIPS if lines else ():
            count = sum(1 for line in lines if _in_chip(line, key))
            if key and not count:
                continue
            chip = w.Button(description=f"{label} {count:,}", tooltip=f"{_CHIP_TIPS[key]}"
                            + (" (click again for all)" if key else ""), layout=layout(width="auto"))
            for name in ("sfx-chip", f"sfx-t-{tone}"):
                chip.add_class(name)
            _class_if(chip, "sfx-on", key == self.machine_chip)
            chip.on_click(self._safely(lambda _button, key=key: self._pick_machine_chip(key)))
            chips.append(chip)
        self.machine_chips.children = chips

    def _shown_lines(self: StepFunctionsExplorer) -> list[_MachineLine]:
        """
        The lines the search and the chip leave, in the column's order (by name first, so equal values stay A to Z).
        """
        lines = [line for line in self._machine_lines() if _in_chip(line, self.machine_chip)]
        if self.machine_query:
            lines = [line for line in lines if search_rank(self.machine_query, _searched_text(line.machine)) is not None]
        lines.sort(key=lambda line: line.machine.name.lower())
        lines.sort(key=lambda line: _column_value(line, self.machine_sort), reverse=self.machine_descending)
        return lines

    def _draw_machine_rows(self: StepFunctionsExplorer) -> None:
        lines = self._shown_lines()
        self.machines_shown = lines
        self._draw_column_heads()
        while len(self._machine_rows) < len(lines):
            self._machine_rows.append(_Line(self, self._clicked_machine))
        words = self.machine_query.split()
        for row, line in zip(self._machine_rows, lines):
            row.key = line.machine.arn
            self._set(row.face, _machine_face(line.machine, line.numbers, line.cost, words))
            _class_if(row.box, "sfx-on", self.machine is not None and line.machine.arn == self.machine.arn)
        rows = [row.box for row in self._machine_rows[:len(lines)]]
        self.machines_box.children = [self.machine_head, *rows, *self._nothing_shown(lines)]
        self._draw_machine_count(lines)

    def _draw_column_heads(self: StepFunctionsExplorer) -> None:
        for key, button in self.column_buttons.items():
            _class_if(button, "sfx-on", key == self.machine_sort)
            label = next(label for column, label, _ in _MACHINE_COLUMNS if column == key)
            arrow = (" ▾" if self.machine_descending else " ▴") if key == self.machine_sort else ""
            if button.description != label + arrow:
                button.description = label + arrow

    def _nothing_shown(self: StepFunctionsExplorer, lines: list[_MachineLine]) -> list[Any]:
        """
        The line under the header when the search and the chip leave nothing (none when something is shown).
        """
        if lines or not self._machine_lines():
            return []
        what = f"matches {self.machine_query!r}" if self.machine_query else "is under this chip"
        return [self._w.HTML(f'<div class="sfx-empty">No state machine {_esc(what)}.</div>')]

    def _draw_machine_count(self: StepFunctionsExplorer, lines: list[_MachineLine]) -> None:
        total = len(self._machine_lines())
        text = _plural(total, "state machine") if len(lines) == total else f"{len(lines):,} of {total:,} state machines"
        self._set(self.machine_count, _esc(text) if total else "")

    def _clicked_machine(self: StepFunctionsExplorer, line: _Line) -> None:
        if line.key:
            self._open_machine(line.key)
            self._show_tab("overview")
