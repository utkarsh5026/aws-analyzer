"""
StepFunctionsExplorer, the Step Functions explorer window, and explore() to open it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _duration, _plural, human_money
from ..._kit.text import _clip, _esc, search_rank
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..helpers import EXECUTION_ARN_RE, STATUS_WORDS, execution_name, parse_execution_ref
from ..models import ExecutionDetail, ExecutionPage, FailureReport, Overview, StateMachine
from ..analysis import monthly_cost
from ..analysis.story import ExecutionStory, lambda_steps
from ..analyzer import StepFunctionsAnalyzer
from ..style import _CSS, _render_html
from ..view import StepFunctionsView, _with_story_run
from .frame import (
    _EXECUTION_PAGE, _EXPLORER_CSS, _EXPLORER_TABS, _LOGO, _MACHINE_TABS, _SINCE_CHOICES, _STATUS_CHIPS,
    _execution_face, _machine_face, _window_text,
)
from .graph import _GraphTab
from .widgets import _Line, _MachineField


_EXECUTIONS_READ = 500

_OVERVIEW_DAYS = 7

_FAILURE_BUTTONS = 6


class StepFunctionsExplorer(_GraphTab, _Explorer):
    """
    The Step Functions explorer: a window to look through your state machines by clicking. The state machine field at
    the top switches to another one; the cards beside it say how it's doing.

        State machines  every state machine in the region: its executions and how many didn't succeed (7 days), its
                        average run, its cost and its logging, the failing ones first; click one to open it
        Overview        how the state machine is doing, what its definition risks, and every state
        Executions      its executions, newest first: pick a time range, filter by outcome, search by name, and click
                        one for its waterfall (each state on one time axis, with its tries and back-off) and one log
                        of what happened, Step Functions' events and every Lambda run's lines merged by time
        Graph           the state machine drawn as a graph: the path the execution open took (green ran, red
                        failed, dashed never reached), or where executions stop in the time range, shaded; pick a
                        state beside it for its tries, its Lambda run's lines, or the failures that stopped there
        Failures        where and why its executions fail, grouped by the state they stopped at and the error, each
                        group's newest execution a click away
        Definition      each state, what it does and where it goes next, and the flow as Mermaid to paste

    machine: the state machine to open (a name or ARN); without it, the one failing most, else the first.
    execution: an execution to open in the Executions tab (its ARN, 'machine/name', or a name of the machine's).
    tab: the tab to start on. since: how far back the Executions and Failures tabs read ('24h', '7d', '30d', '90d').
    view / core: a StepFunctionsView or StepFunctionsAnalyzer to use (else one is made from region / profile).
    height: the height of the tabs' pages (pixels, or CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports
    elsewhere), 'widgets' or 'text'.

    It only reads: nothing here starts, stops or redrives an execution. x.ui is a StepFunctionsView for reports in
    other cells; x.overview, x.machine, x.page, x.detail, x.story and x.report hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "sfx", "sfn-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        machine: str | None = None,
        *,
        execution: str | None = None,
        tab: str | None = None,
        since: str = "7d",
        view: StepFunctionsView | None = None,
        core: StepFunctionsAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        tabs = [key for key, _, _, _ in _EXPLORER_TABS]
        if tab is not None and tab not in tabs:
            raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")
        if since not in [value for _, value in _SINCE_CHOICES]:
            raise ValueError(f"since is one of {', '.join(v for _, v in _SINCE_CHOICES)}, not {since!r}")
        super().__init__()
        if view is None:
            view = StepFunctionsView(core or StepFunctionsAnalyzer(region=region, profile=profile),
                                     mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.height = height
        self.overview: Overview | None = None
        self.numbers: dict[str, Any] = {}
        self.machine: StateMachine | None = None
        self.page: ExecutionPage | None = None
        self.detail: ExecutionDetail | None = None
        self.story: ExecutionStory | None = None
        self.report: FailureReport | None = None
        self._since = since
        self._want_execution = execution
        self._want_tab = tab
        self._status_filter, self._query, self._offset = "", "", 0
        self._selected = ""
        self._visible: list[Any] = []
        self._rows: list[_Line] = []
        self._machine_rows: list[_Line] = []
        self._tab = "machines"
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(machine, execution, note, mode)
            return
        self._build()
        self._begin(machine)
        self._display()

    def __repr__(self) -> str:
        name = self.machine.name if self.machine else "no state machine"
        return f"StepFunctionsExplorer({name}) · help(StepFunctionsExplorer) says what it shows"

    @_window_errors
    def open(self, machine: str) -> None:
        """
        Shows another state machine (a name or ARN), as picking it in the state machine field does.
        """
        self._open_machine(self.core.resolve(machine))

    @_window_errors
    def execution(self, ref: str) -> None:
        """
        Opens an execution in the Executions tab (its ARN, 'machine/name', or a name of the machine shown), as
        clicking it in the list does: its waterfall and one log of what happened.
        """
        if self._w is None:
            self.ui.execution(ref, machine=self.machine.name if self.machine else None)
            return
        arn = self.core.execution_arn(ref, machine=self.machine.arn if self.machine else None)
        self._show_tab("executions")
        self._open_execution(arn)

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the state machines, and the one shown (the ↻ button).
        """
        keep = self.machine.arn if self.machine else None
        self._want_execution = self._selected or None
        self._begin(keep)

    def _reports(self, machine: str | None, execution: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: failures('name') says where and why a state machine fails, and "
                                 "execution('name/execution') shows one step by step.")])
        if execution is not None:
            self.ui.execution(execution, machine=machine)
        elif machine is not None:
            self.ui.state_machine(machine)
        else:
            self.ui.overview()

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it).
        """
        return _render_html(_for_window(blocks, _window_text), self.ui.max_rows).replace(_CSS, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("sfx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the state machines and executions "
                                       "again", layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("sfx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.refresh_button], layout=layout(width="100%"))
        top.add_class("sfx-top")
        self.field = _MachineField(self, on_pick=self._open_machine, on_text=self._typed_machine)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.field, self.stats], layout=layout(width="100%"))
        meta.add_class("sfx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("sfx-head")
        tabs = self._tab_bar(_EXPLORER_TABS)
        self.machine_view = w.HTML(layout=layout(width="100%"))
        self.machines_box = w.VBox(layout=layout(width="100%"))
        self.machines_box.add_class("sfx-machines")
        self.overview_view = w.HTML(layout=layout(width="100%"))
        self.failure_opens = w.HBox(layout=layout(width="100%"))
        self.failure_opens.add_class("sfx-opens")
        self.failures_view = w.HTML(layout=layout(width="100%"))
        self.definition_view = w.HTML(layout=layout(width="100%"))
        self._frame(_CSS + _EXPLORER_CSS, head, tabs, {
            "machines": w.VBox([self.machine_view, self.machines_box]),
            "overview": w.VBox([self.overview_view]),
            "executions": self._executions_page(),
            "graph": self._graph_page(),
            "failures": w.VBox([self.failure_opens, self.failures_view]),
            "definition": w.VBox([self.definition_view]),
        })
        self._show_tab(self._tab)
        self._draw_title()

    def _executions_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.since_pick = w.Dropdown(options=list(_SINCE_CHOICES), value=self._since,
                                     layout=layout(flex="0 0 auto", width="150px"))
        self.since_pick.observe(self._safely(self._since_changed), names="value")
        self.find = w.Text(placeholder="Search executions by name…", continuous_update=True,
                           layout=layout(flex="1 1 auto", width="auto"))
        self.find.add_class("sfx-find")
        self.find.observe(self._safely(self._find_typed), names="value")
        finder = w.HBox([self.since_pick, self.find], layout=layout(width="100%", align_items="center"))
        finder.add_class("sfx-filters")
        self.chips = w.HBox(layout=layout(width="100%"))
        self.chips.add_class("sfx-chips")
        self.rows_box = w.VBox(layout=layout(width="100%"))
        self.rows_box.add_class("sfx-rows")
        self.pager_text = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.page_buttons = {}
        for key, glyph, tip in (("first", "«", "The first page"), ("previous", "‹", "The page before"),
                                ("next", "›", "The next page"), ("last", "»", "The last page")):
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("sfx-pg")
            button.on_click(self._safely(lambda _button, key=key: self._page_to(key)))
            self.page_buttons[key] = button
        pager = w.HBox([self.pager_text, *self.page_buttons.values()], layout=layout(width="100%"))
        pager.add_class("sfx-pager")
        self.left = w.VBox([finder, self.chips, self.rows_box, pager])
        self.left.add_class("sfx-left")
        self.execution_view = w.HTML(layout=layout(width="100%"))
        self.right = w.VBox([self.execution_view])
        self.right.add_class("sfx-right")
        self.split = w.HBox([self.left, self.right])
        self.split.add_class("sfx-split")
        return self.split

    def _show_tab(self, key: str) -> None:
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "sfx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        wants_failures = key == "failures" or (key == "graph" and self.graph_mode == "failures")
        if wants_failures and self.report is None and self.machine is not None and self.machine.has_executions:
            self._load_failures()
        elif not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        machine, page = self.machine, self.page
        if key == "machines" and self.overview is not None:
            return f"{_plural(len(self.overview.machines), 'state machine')} in {self.core.region}: click one to open it."
        if key in _MACHINE_TABS and machine is None:
            return "Pick a state machine in the field above, or click one in the State machines tab."
        if key == "executions" and self.detail is not None:
            execution = self.detail.execution
            return f"{execution.name}: {STATUS_WORDS.get(execution.status, execution.status)}"
        if key == "executions" and page is not None:
            return f"{_plural(len(page.executions), 'execution')} in the last {self._since_label()}: click one to see it."
        if key == "failures" and self.report is not None:
            return f"{_plural(len(self.report.failed), 'execution')} didn't succeed in the last {self._since_label()}."
        return ""

    def _since_label(self) -> str:
        return next(label.lower().removeprefix("last ") for label, value in _SINCE_CHOICES if value == self._since)

    def _begin(self, machine: str | None) -> None:
        """
        Reads the region's state machines with their numbers, then opens the one asked for (else the one failing
        most, else the first).
        """
        self._status(f"Reading the state machines in {self.core.region}…", busy=True, owner="list")
        self._set(self.machine_view, _skeleton("Reading the state machines and their CloudWatch numbers…"))
        self._later("list", lambda: self.core.overview(days=_OVERVIEW_DAYS), lambda ov: self._listed(ov, machine),
                    self._list_failed)

    def _list_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "states:ListStateMachines")
        self.field.problem = f"Couldn't list the state machines ({reason}). Type one's ARN and press Enter."
        self.field.set_choices([])
        self._status(f"Couldn't list the state machines ({reason}).", "warn")
        self._set(self.machine_view, self._html([_Note(f"Couldn't list the state machines: {reason}.", "warn")]))

    def _listed(self, overview: Overview, wanted: str | None) -> None:
        self.overview = overview
        self.numbers = dict(overview.numbers)
        self.field.set_choices(overview.machines)
        self._draw_machines()
        self._draw_stats()
        if not overview.machines:
            self._status(f"No state machines in {self.core.region}.", "warn")
            return
        target = self._target(wanted)
        failing = sum(1 for n in self.numbers.values() if n.unsuccessful)
        self._status(f"{_plural(len(overview.machines), 'state machine')} in {self.core.region}"
                     + (f" · {failing:,} with failures in {_OVERVIEW_DAYS} days" if failing else ""), owner="list")
        if target is not None:
            self._open_machine(target)

    def _target(self, wanted: str | None) -> str | None:
        """
        The state machine to open first: the one asked for, else the one failing most whose executions can be read
        (by share of executions, _failing_most), else the first.
        """
        machines = self.overview.machines if self.overview else []
        if wanted:
            try:
                return self.core.resolve(wanted)
            except (ValueError, ClientError, BotoCoreError) as exc:
                self._status(self._error_text(exc), "warn")
                return None
        ranked = sorted(machines, key=self._failing_most)
        standard = [m for m in ranked if m.has_executions]
        return (standard or ranked)[0].arn if machines else None

    def _failing_most(self, machine: StateMachine) -> tuple[float, int]:
        """
        How a state machine ranks for being opened first: the highest share of executions that didn't succeed, then
        the most of them (a busy workflow with a few failures comes after a small one failing often).
        """
        numbers = self.numbers.get(machine.arn)
        if numbers is None:
            return (0.0, 0)
        return (-numbers.failure_rate, -numbers.unsuccessful)

    def _typed_machine(self, text: str) -> None:
        self._open_machine(self.core.resolve(text))

    def _draw_title(self) -> None:
        machine = self.machine
        sub = "Every state machine, its executions step by step, and the Lambda runs they called · read-only"
        if machine is not None:
            sub = " · ".join(filter(None, [machine.name, f"{machine.type.title()} workflow",
                                           _plural(len(machine.states), "state"),
                                           _clip(machine.description, 90) if machine.description else ""]))
        self._set(self.title, f'<div class="sfx-brand"><span class="sfx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="sfx-name">Step Functions explorer<span>{_esc(self.core.region)}</span>'
                              f'</div><div class="sfx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the state machine field: the one shown at a glance, or the region's before one is.
        """
        machine = self.machine
        numbers = self.numbers.get(machine.arn) if machine else None
        if machine is None:
            cards = self._region_cards()
        else:
            failing = numbers is not None and numbers.unsuccessful > 0
            logging_tone = "warn" if machine.is_express and machine.log_level == "OFF" else ""
            cards = [
                (f"Executions · {_OVERVIEW_DAYS}d", f"{numbers.started:,}" if numbers else "-", ""),
                ("Didn't succeed", f"{numbers.unsuccessful:,} ({numbers.failure_rate:.1%})" if failing
                 else ("0" if numbers else "-"), "bad" if failing else ""),
                ("Avg run", _duration((numbers.avg_ms or 0) / 1000) if numbers and numbers.avg_ms else "-", ""),
                ("Est. $ / month", human_money(monthly_cost(machine, numbers, self.core.prices)), ""),
                ("Logging", machine.log_level.lower(), logging_tone),
            ]
        html_cards = "".join(f'<div class="sfx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="sfx-stats">{html_cards}</div>')

    def _region_cards(self) -> list[tuple[str, str, str]]:
        if self.overview is None:
            return [("State machines", "…", "sk-on"), ("Executions", "…", "sk-on"), ("Didn't succeed", "…", "sk-on")]
        numbers = list(self.numbers.values())
        unsuccessful = sum(n.unsuccessful for n in numbers)
        return [("State machines", f"{len(self.overview.machines):,}", ""),
                (f"Executions · {_OVERVIEW_DAYS}d", f"{sum(n.started for n in numbers):,}", ""),
                ("Didn't succeed", f"{unsuccessful:,}", "bad" if unsuccessful else "")]

    def _draw_machines(self) -> None:
        """
        The State machines tab: a line per state machine, the failing ones first, each a click away.
        """
        overview = self.overview
        if overview is None:
            return
        machines = sorted(overview.machines, key=lambda m: (-(self.numbers[m.arn].unsuccessful
                                                               if m.arn in self.numbers else 0), m.name.lower()))
        blocks = self.ui._overview_blocks(overview) if overview.machines else [_Note(
            f"No state machines in {overview.region}. They're regional: explore(region='us-west-2') looks in another "
            "region.")]
        findings = [b for b in blocks if type(b).__name__ in ("_Title", "_Findings")]
        self._draw("machines", self.machine_view, findings)
        while len(self._machine_rows) < len(machines):
            self._machine_rows.append(_Line(self, self._clicked_machine))
        header = self._w.HTML('<div class="sfx-cols"><span>State machine</span><span>Executions</span>'
                              "<span>Didn't succeed</span><span>Avg run</span><span>$ / month</span>"
                              "<span>Logging</span></div>")
        for line, machine in zip(self._machine_rows, machines):
            line.key = machine.arn
            numbers = self.numbers.get(machine.arn)
            cost = monthly_cost(machine, numbers, self.core.prices)
            self._set(line.face, _machine_face(machine, numbers, cost, []))
            _class_if(line.box, "sfx-on", self.machine is not None and machine.arn == self.machine.arn)
        self.machines_box.children = [header] + [line.box for line in self._machine_rows[:len(machines)]]

    def _clicked_machine(self, line: _Line) -> None:
        if line.key:
            self._open_machine(line.key)
            self._show_tab("overview")

    def _open_machine(self, arn: str) -> None:
        """
        Shows a state machine: its settings and definition first, then its executions, in the background; what's
        still being read for the one shown before is dropped when it comes back.
        """
        self._drop("machine", "executions", "execution", "story", "failures")
        self.machine = self.page = self.detail = self.story = self.report = None
        self._selected, self._status_filter, self._query, self._offset = "", "", "", 0
        self._quietly(self.find, value="")
        self.field.value = arn
        self.field.draw()
        self._draw_machines()
        name = arn.rsplit(":", 1)[-1]
        for widget, text in ((self.overview_view, f"Reading {name}…"), (self.definition_view, "Reading its definition…"),
                             (self.failures_view, "The Failures tab reads when it's opened."),
                             (self.execution_view, "Listing its executions…"),
                             (self.graph_view, "Drawing its graph…")):
            self._set(widget, _skeleton(text))
        self.failure_opens.children = []
        self.rows_box.children, self.chips.children = [], []
        self._draw_pager()
        self._status(f"Reading {name}…", busy=True, owner="machine")
        self._later("machine", lambda: self.core.state_machine(arn), self._described, self._machine_failed)

    def _machine_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview_view, self.definition_view, self.execution_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _described(self, machine: StateMachine) -> None:
        self.machine = machine
        self._draw_title()
        self._draw_stats()
        self._draw("overview", self.overview_view, self.ui._machine_blocks(machine, self.numbers.get(machine.arn)))
        self._draw("definition", self.definition_view, self.ui._graph_blocks(machine))
        self._graph_machine()
        want_tab, self._want_tab = self._want_tab, None
        if want_tab:
            self._show_tab(want_tab)
        if not machine.has_executions:
            self._unrecorded_executions(machine)
            return
        self._load_executions()
        if self._tab == "failures" or (self._tab == "graph" and self.graph_mode == "failures"):
            self._load_failures()

    def _unrecorded_executions(self, machine: StateMachine) -> None:
        note = _Note(f"{machine.name} is an Express workflow with logging off: Step Functions keeps no list or "
                     "history of its executions, so there's nothing to show here. The Overview tab has the command "
                     "that turns logging on; executions after that show up here, read from its log group.")
        for widget in (self.execution_view, self.failures_view):
            self._set(widget, self._html([note]))
        self._status(f"{machine.name} is an Express workflow with logging off: none of its executions are recorded.",
                     owner="machine")

    def _load_executions(self) -> None:
        machine = self.machine
        if machine is None:
            return
        self._status(f"Listing {machine.name}'s executions in the last {self._since_label()}…", busy=True,
                     owner="executions")
        arn, since = machine.arn, self._since
        self._later("executions", lambda: self.core.executions(arn, since=since, limit=_EXECUTIONS_READ),
                    self._got_executions, self._executions_failed)

    def _executions_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.execution_view, self._html([_Note(f"Couldn't list the executions: {text}", "warn")]))

    def _got_executions(self, page: ExecutionPage) -> None:
        self.page = page
        self._draw_chips()
        self._refilter()
        unsuccessful = sum(1 for e in page.executions if e.failed)
        _class_if(self.tab_buttons["executions"], "sfx-alarm", bool(unsuccessful))
        cut = f" (the newest {len(page.executions):,})" if page.truncated else ""
        self._status(f"{_plural(len(page.executions), 'execution')} in the last {self._since_label()}{cut}"
                     + (f" · {unsuccessful:,} didn't succeed" if unsuccessful else ""), owner="executions")
        wanted, self._want_execution = self._want_execution, None
        if wanted:
            self._open_wanted(wanted)
        elif not self._selected:
            self._draw("executions", self.execution_view,
                       self.ui._executions_blocks(page, status=None, since=self._since))

    def _open_wanted(self, wanted: str) -> None:
        machine = self.machine
        named, name = parse_execution_ref(wanted)
        if EXECUTION_ARN_RE.match(name):
            arn = name
        else:
            arn = self.core.execution_arn(name, machine=named or (machine.arn if machine else None))
        self._show_tab("executions")
        self._open_execution(arn)

    def _since_changed(self, change: dict[str, Any]) -> None:
        self._since = change["new"]
        self.report = None
        if self.machine is not None and self.machine.has_executions:
            self._load_executions()
            if self._tab == "failures" or (self._tab == "graph" and self.graph_mode == "failures"):
                self._load_failures()

    def _find_typed(self, change: dict[str, Any]) -> None:
        self._query = str(change["new"] or "").strip()
        self._offset = 0
        self._refilter()

    def _draw_chips(self) -> None:
        """
        A chip per outcome the executions had, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        executions = self.page.executions if self.page else []
        counts = Counter(e.status for e in executions)
        chips = []
        for status, label, tone in _STATUS_CHIPS:
            if status and not counts[status]:
                continue
            n = len(executions) if not status else counts[status]
            chip = w.Button(description=f"{label} {n:,}", tooltip=f"Show only the executions that "
                            f"{STATUS_WORDS.get(status, '')} (click again for all)" if status else "Show every execution",
                            layout=layout(width="auto"))
            for name in ("sfx-chip", f"sfx-t-{tone or 'none'}"):
                chip.add_class(name)
            _class_if(chip, "sfx-on", status == self._status_filter)
            chip.on_click(self._safely(lambda _button, status=status: self._pick_status(status)))
            chips.append(chip)
        self.chips.children = chips

    def _pick_status(self, status: str) -> None:
        self._status_filter = "" if status == self._status_filter else status
        self._offset = 0
        self._draw_chips()
        self._refilter()

    def _refilter(self) -> None:
        executions = self.page.executions if self.page else []
        shown = [e for e in executions if not self._status_filter or e.status == self._status_filter]
        if self._query:
            shown = [e for e in shown if search_rank(self._query, (e.name, e.status)) is not None]
        self._visible = shown
        self._offset = min(self._offset, max(0, (len(shown) - 1) // _EXECUTION_PAGE * _EXECUTION_PAGE))
        self._draw_rows()

    def _draw_rows(self) -> None:
        page = self._visible[self._offset:self._offset + _EXECUTION_PAGE]
        while len(self._rows) < len(page):
            self._rows.append(_Line(self, self._clicked_execution))
        words = self._query.split()
        for line, execution in zip(self._rows, page):
            line.key = execution.arn
            self._set(line.face, _execution_face(execution, words))
            tip = f"{execution.name}: click for its waterfall and log"
            if line.button.tooltip != tip:
                line.button.tooltip = tip
            _class_if(line.box, "sfx-on", execution.arn == self._selected)
        children: list[Any] = [line.box for line in self._rows[:len(page)]]
        if not page:
            text = ("Listing the executions…" if self.page is None else "No execution matches. Clear the search, or "
                    "pick All above." if self._query or self._status_filter else
                    "No executions in this time range: pick a longer one above.")
            children = [self._w.HTML(f'<div class="sfx-empty">{_esc(text)}</div>')]
        self.rows_box.children = children
        self._draw_pager()

    def _draw_pager(self) -> None:
        total = len(self._visible)
        first, last = self._offset + 1, min(self._offset + _EXECUTION_PAGE, total)
        self._set(self.pager_text, f"<b>{first:,}–{last:,}</b> of {total:,}" if total else "0 executions")
        more = total > _EXECUTION_PAGE
        for key, button in self.page_buttons.items():
            button.layout.display = "" if more else "none"
            button.disabled = (self._offset == 0) if key in ("first", "previous") else (last >= total)

    def _page_to(self, where: str) -> None:
        end = max(0, (len(self._visible) - 1) // _EXECUTION_PAGE * _EXECUTION_PAGE)
        self._offset = {"first": 0, "previous": max(0, self._offset - _EXECUTION_PAGE),
                        "next": min(end, self._offset + _EXECUTION_PAGE), "last": end}[where]
        self._draw_rows()

    def _clicked_execution(self, line: _Line) -> None:
        if line.key:
            self._open_execution(line.key)

    def _renew_pane(self) -> None:
        """
        A new box for the right side of the Executions tab, which starts at the top (widgets can't be scrolled from
        Python).
        """
        w = self._w
        self.execution_view = w.HTML(layout=w.Layout(width="100%"))
        self.right = w.VBox([self.execution_view])
        self.right.add_class("sfx-right")
        self.split.children = [self.left, self.right]

    def _open_execution(self, arn: str) -> None:
        """
        An execution's page on the right: its steps and waterfall as soon as its history is read, then its log with
        every Lambda run's lines.
        """
        self._selected = arn
        self.detail = self.story = None
        self._drop("story")
        for line in self._rows:
            _class_if(line.box, "sfx-on", line.key == arn)
        self._renew_pane()
        name = execution_name(arn)
        self._set(self.execution_view, _skeleton(f"Reading {name}'s history…"))
        self._status(f"Reading {name}…", busy=True, owner="execution")
        listed = next((e for e in self.page.executions if e.arn == arn), None) if self.page else None
        started = listed.start if listed else None
        self._later("execution", lambda: self.core.execution(arn, logs=False, started=started), self._got_execution,
                    self._execution_failed)

    def _execution_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.execution_view, self._html([_Note(text, "warn")]))

    def _got_execution(self, detail: ExecutionDetail) -> None:
        self.detail = detail
        self._draw_graph()
        steps, _ = lambda_steps(detail, 1)
        reading = [_Note("Reading the Lambda runs it called, for its log…")] if steps else []
        self._draw("execution", self.execution_view, self.ui._execution_blocks(detail) + reading)
        execution = detail.execution
        word = STATUS_WORDS.get(execution.status, execution.status)
        if not steps:
            self._status(f"{execution.name}: {word}", owner="execution")
            return
        self._status(f"{execution.name}: {word} · reading the Lambda runs it called…", busy=True, owner="execution")
        self._later("story", lambda: self.core.execution_story(detail), lambda story: self._got_story(detail, story))

    def _got_story(self, detail: ExecutionDetail, story: ExecutionStory) -> None:
        if self.detail is not detail:
            return
        self.story = story
        _with_story_run(detail, story)
        self._draw_graph()
        self._draw("execution", self.execution_view, self.ui._execution_blocks(detail, story))
        read = sum(1 for r in story.runs if r.run is not None)
        execution = detail.execution
        self._status(f"{execution.name}: {STATUS_WORDS.get(execution.status, execution.status)} · "
                     f"{_plural(read, 'Lambda run')} in its log", owner="execution")

    def _load_failures(self) -> None:
        machine = self.machine
        if machine is None or not machine.has_executions:
            return
        self._set(self.failures_view, _skeleton(f"Reading why {machine.name}'s executions fail…"))
        self.failure_opens.children = []
        self._status(f"Reading {machine.name}'s failed executions…", busy=True, owner="failures")
        arn, since = machine.arn, self._since
        self._later("failures", lambda: self.core.failures(arn, since=since), self._got_failures,
                    self._failures_failed)

    def _failures_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.failures_view, self._html([_Note(f"Couldn't read the failures: {text}", "warn")]))

    def _got_failures(self, report: FailureReport) -> None:
        self.report = report
        self._draw_graph()
        self._draw("failures", self.failures_view, self.ui._failure_blocks(report))
        w, layout = self._w, self._w.Layout
        buttons = []
        for group in report.groups[:_FAILURE_BUTTONS]:
            if group.newest is None:
                continue
            newest = group.newest
            button = w.Button(description=f"Open {newest.name} ({group.state}: {_clip(group.error, 30)})",
                              tooltip=f"The newest execution that stopped at {group.state} with {group.error}",
                              layout=layout(width="auto"))
            button.add_class("sfx-small")
            button.on_click(self._safely(lambda _button, arn=newest.arn: self._open_from_failures(arn)))
            buttons.append(button)
        self.failure_opens.children = buttons
        _class_if(self.tab_buttons["failures"], "sfx-alarm", bool(report.groups))
        self._finish("failures", f"{_plural(len(report.failed), 'execution')} didn't succeed since "
                                 f"{self._since_label()}, in {_plural(len(report.groups), 'group')}",
                     tabs=("failures",))

    def _open_from_failures(self, arn: str) -> None:
        self._show_tab("executions")
        self._open_execution(arn)


def explore(  # noqa: PLR0913
    machine: str | None = None,
    *,
    execution: str | None = None,
    tab: str | None = None,
    since: str = "7d",
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> StepFunctionsExplorer:
    """
    Opens the Step Functions explorer window and returns it: every state machine, its executions to click through,
    each one's waterfall and one log of what happened (Step Functions' events and every Lambda run's lines), its
    failures by cause and its definition.

        explore()                                        # the state machine failing most, or the first
        explore("order-pipeline")                        # by name or ARN
        explore("order-pipeline", execution="ord-7f3a")  # with one execution open
        explore("order-pipeline", tab="failures", since="30d")

    It only reads: nothing here starts, stops or redrives an execution.
    """
    return StepFunctionsExplorer(machine, execution=execution, tab=tab, since=since, region=region, profile=profile,
                                 height=height)
