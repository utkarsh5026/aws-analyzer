"""
LambdaExplorer, made of its tabs' mixins, and explore().
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.blocks import _Note
from ..._kit.errors import _Hint
from ..._kit.fmt import _in_zone, _plural
from ..._kit.history import Place, PlaceHistory, Snapshot
from ..._kit.render import _call
from ..._kit.text import _esc
from ..._kit.window import _Explorer, _check_assistant, _class_if, _for_window, _window_errors

from ..helpers import parse_function_ref
from ..models import CodeFrame, CodePackage, ErrorReport, Function, FunctionDetail, PackageDownload, Performance, SavedPage
from ..analysis.text import _window
from ..analysis.overview import Overview
from ..analysis.findings import function_findings
from ..analysis.logs import LogPage, LogRun, TimeBucket
from ..analysis.codemap.model import CodeMap
from ..analysis.changes import FunctionChanges
from ..analyzer import LambdaAnalyzer
from ..._kit.themes import _theme_css, _theme_name
from ..style import _CSS, _ROOT, _render_html
from ..view import LambdaView
from .frame import (
    _CODE_PAGE, _EXPLORER_CSS, _EXPLORER_TABS, _FUNCTION_PAGE, _FUNCTION_TABS, _RUN_PAGE, _seconds, _window_text,
)
from .widgets import _FunctionField, _Row, _RunRow
from .functions import _Facts, _FunctionsTab, _arn_account
from .live import _LIVE_CSS, _LiveTab
from .function import _FunctionTab, _KeptReads
from .logs import _FIRST_LOGS_RANGE, _LogsTab, _Reading
from .timeline import _Timeline
from .errors import _ErrorsTab
from .performance import _PerformanceTab
from .code import _CodeTab
from .focus import _FocusReads
from .plain import _PLAIN_CSS, _PlainSearch


class LambdaExplorer(_FunctionsTab, _LiveTab, _FunctionTab, _LogsTab, _Timeline, _ErrorsTab, _PerformanceTab, _CodeTab,
                     _PlainSearch, _FocusReads, _Explorer):
    """
    The Lambda explorer: a window to look through your functions by clicking, with nothing to type but a search.
    The region field and the function field at the top pick what it shows; the cards beside them say how it's doing.

        Functions    every function in the region (or in every region): runtime, calls, errors, run time, cost and
                     warnings, the ones that need attention first. Search, filter, sort by a column, and click one to
                     open it (or Logs on its line, to go straight to its logs)
        Live         what's running now: every function that ran in the last 10 minutes, the ones with runs going
                     first, with how long each run has gone against its timeout and its calls a minute, looked at
                     again every few seconds. Click one to follow it: its runs, newest first, the ones going open
                     with their lines as they come
        Overview     how the picked function is doing: what's wrong and what to do, what calls it and where its
                     results, failed events and logs go, calls and run time day by day, and what it costs
        Logs         what it logged, run by run, newest first: each run's start, status, run time, memory and cold
                     start, the failed ones in red; click a run for its lines. Type to find runs with some text (an
                     order ID, KeyError, a request ID), Enter to search CloudWatch for the whole time range, pick the
                     time range, show only failed runs or cold starts, and turn on Live to see new runs as they come
        Errors       its errors grouped by cause, with what to do about each, and the runs that failed: click one to
                     read every line of it
        Performance  run times against the timeout, memory used against what it has, cold starts, and the slowest
                     runs (click one to read it)
        Code         the files in its deployment package as a tree of folders, and the source of the one you click,
                     with Copy to copy all of it (secrets files held back). Hide files gives the source the whole
                     width, and Show files brings the tree back. Download .zip saves the package next to the notebook
        Settings     every setting in plain English, and as Lambda returns it (environment values hidden)

    name: a function to open (its name, 'name:alias', an ARN or a console link), on tab= ('overview', 'logs',
    'errors', 'performance', 'code' or 'settings'; 'live' follows it on the Live tab, which tab='live' opens without
    a name). since / until: the time range of the Logs, Errors and Performance tabs ('3h', '7d', '2026-10-27 09:00';
    until is now when left out). search: text to find in the logs (opens the Logs tab). region: the region to list,
    or 'all' for every region your account has turned on (the region field switches it). view / core: a LambdaView or LambdaAnalyzer to use (else one is made
    from region / profile). height: the height of the tabs' pages, which fill the browser window unless set (pixels, or
    CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.
    history: whether to remember the functions you open, between sessions, so the function field lists them first and
    the Recent chip shows only them: True (in aws-analyzer's cache folder, ~/.cache/aws-analyzer/history/, per AWS
    account; only the ARNs and when), False, or a file to keep them in. AWS_ANALYZER_HISTORY=off turns it off
    everywhere. x.recent lists them and x.forget_recent() clears them.
    assistant: an AgentView to ask about what the window shows: ✦ Ask, at the end of the tab bar, opens a drawer where
    "why does this fail?" means the function, run or file you have open (x.attach(agent) does it later, and
    aws_analyzer.set_assistant(agent) for every explorer).

    Nothing here invokes or changes a function: the window only reads, and where a change would help it shows the
    command. The files it writes are the package Download .zip saves, on the notebook's disk, and its recent
    functions (history=). x.ui is a LambdaView for reports in other cells (x.ui.errors(...)); x.overview, x.function,
    x.detail, x.logs_page, x.package and x.saved hold the data behind what's shown, and x.board what the Live tab read
    (function ARN -> LiveFunction). x.focus is what you're looking at, as data: the function, the tab, its time range
    and the run or file opened (x.focus.chips() reads it as a row of chips).
    """

    _PREFIX, _THREADS = "lmx", "lambda-explorer"
    _reword = staticmethod(_window_text)

    def __init__(
        self,
        name: str | None = None,
        *,
        tab: str | None = None,
        since: Any = None,
        until: Any = None,
        search: str | None = None,
        view: LambdaView | None = None,
        core: LambdaAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
        progress: str = "auto",
        history: bool | str = True,
        assistant: Any = None,
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        if tab is not None and tab not in {key for key, *_ in _EXPLORER_TABS}:
            raise ValueError(f"tab= is one of {', '.join(repr(key) for key, *_ in _EXPLORER_TABS)}; got {tab!r}")
        if since is not None or until is not None:
            LambdaAnalyzer.time_range(since if since is not None else _FIRST_LOGS_RANGE, until)
        super().__init__()
        if assistant is not None:
            _check_assistant(assistant)
            self.assistant = assistant
        every = isinstance(region, str) and region.strip().lower() == "all"
        if view is None:
            view = LambdaView(core or LambdaAnalyzer(region=None if every else region, profile=profile),
                              mode="text" if mode == "text" else "auto", progress=progress)
        self.ui = view
        self.core = view.core
        self.height = height
        self.region: str | None = "all" if every else region  # the region field: a region, or 'all'
        self.overview: Overview | None = None  # the functions listed
        self.function: Function | None = None  # the one picked
        self.detail: FunctionDetail | None = None  # describe() of it: the Overview and Settings tabs
        self.logs_page: LogPage | None = None  # the Logs tab's read of the time range
        self.found: LogPage | None = None  # the Logs tab's CloudWatch search, or the one run opened
        self.error_report: ErrorReport | None = None  # the Errors tab
        self.perf: Performance | None = None  # the Performance tab
        self.package: CodePackage | None = None  # the Code tab
        self.code_map: CodeMap | None = None  # the Code tab's How it works, read from the package it downloaded
        self.saved: PackageDownload | None = None  # what the Code tab's Download .zip saved last
        self.page_saved: SavedPage | None = None
        self._facts: dict[str, _Facts] = {}  # function ARN -> what the list says about it
        self._history = PlaceHistory.from_setting(history, "lambda")
        self._recent: dict[str, Place] = {}
        self._looks: dict[str, Snapshot] = {}
        self._news: dict[str, FunctionChanges] = {}
        self._numbers_pending = False  # CloudWatch's numbers are being read for the list
        self._want, self._want_tab = name, tab or ("logs" if search and name else None)  # opened once listed...
        self._want_search: str | None = search or None  # ...and searched for in its logs
        self._tab = "functions"
        self._init_live()
        if tab == "live":
            self._tab, self._live_wanted, self._want, self._want_tab = "live", name, None, None
        self._asked: set[str] = set()  # the function's tabs whose data was asked for
        self._listing_started = 0.0
        self._query, self._chip, self._sort, self._descending, self._offset = "", "", "problems", True, 0
        self._rows: list[_Row] = []
        self._visible: list[_Facts] = []  # the functions the list's search and chip let through, in order
        self._range, self._log_query, self._run_chip, self._run_offset = _FIRST_LOGS_RANGE, "", "", 0
        self._run_sort, self._run_values = "", {}
        self._linked = False
        self._until: Any = None
        self._reading: _Reading | None = None
        self._reach: datetime | None = None
        self.range_picks: list[Any] = []
        self._runs: list[LogRun] = []  # logs_page's runs, newest first
        self._found_runs: list[LogRun] = []  # found's
        self._searched = ""  # what the CloudWatch search (or the run opened) looked for
        self._open_runs: set[str] = set()  # LogRun.key of the runs opened to show their lines
        self._run_rows: list[_RunRow] = []
        self._live, self._live_token, self._live_started, self._live_new = False, 0, 0.0, 0
        self._error_rows: list[_Row] = []
        self._cause_rows: list[_Row] = []
        self._run_window: tuple[datetime, datetime] | None = None
        self._kept: dict[str, _KeptReads] = {}
        self.view_code: Any = None
        self._rereading = False
        self._slow_cutoff: float | None = None
        self._buckets: list[TimeBucket] = []
        self.change_line: Any = None
        self._code_target: CodeFrame | None = None
        self._slow_rows: list[_Row] = []
        self._code_fn: Function | None = None  # the function as GetFunction gave it, with the link to its package
        self._package_data: bytes | None = None
        self._code_query, self._code_offset, self._code_file = "", 0, None
        self._code_open: set[str] = set()  # the tree's folders shown open ('boto3/')
        self._code_shut: set[str] = set()  # ...and the ones folded while searching, when every other one is open
        self._toggled: tuple[str, float] = ("", 0.0)  # the folder clicked last, and when
        self._files_shown = True  # the tree beside the source; Hide files folds it away (for every function after)
        self._how = False  # the Code tab shows How it works instead of a file's source
        self._code_rows: list[_Row] = []
        self._pagers: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._function_chips: dict[str, Any] = {}  # chip key -> its button, made once
        self._run_chip_buttons: dict[str, Any] = {}
        self._empties: dict[str, Any] = {}  # list -> the line it shows when it's empty
        self._listing = False  # the functions are being listed
        if since is not None or until is not None:
            self._range, self._until, self._linked = since if since is not None else _FIRST_LOGS_RANGE, until, True
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(note, mode)
            return
        self._build()
        if self._linked:
            self._set_range(self._range, self._until)
        self._begin()
        self._display()

    # ------------------------------------------------------------------ public commands

    def __repr__(self) -> str:
        name = self.function.name if self.function is not None else "no function picked"
        return f"LambdaExplorer({name}) · help(LambdaExplorer) says what it shows"

    @property
    def recent(self) -> list[str]:
        """
        The functions listed here that you've opened before (their ARNs), the ones you open most and most lately
        first: x.open(x.recent[0]) opens the first. [] when recent places are off (history=False).
        """
        return list(self._recent)

    @_window_errors
    def forget_recent(self, name: str = "") -> None:
        """
        Forget a function you opened (its name or ARN), or with nothing every function this explorer remembers in this
        account, so the Recent chip and the function field's order start over. Nothing in Lambda changes.
        """
        functions = self.overview.functions if self.overview is not None else []
        if self._history is None or not functions:
            self._status("Recent places are off (history=False or AWS_ANALYZER_HISTORY=off)." if self._history is None
                         else "Nothing to forget until the functions are listed.")
            return
        arns = [arn for arn, place in self._recent.items() if name in (arn, place.label)] if name else [""]
        account = _arn_account(functions[0].arn)
        removed = sum(self._history.forget(account, arn) for arn in arns)
        self._load_recent()
        if self._chip == "recent" and not self._recent:
            self._chip = ""
        self._draw_chips()
        self._refilter()
        what = name or "every function"
        self._status(f"Forgot {what}." if removed else f"Nothing to forget: {what} isn't among the recent functions.")

    @_window_errors
    def open(self, name: str, *, tab: str | None = None) -> None:
        """
        Opens a function (its name, 'name:alias', an ARN or a console link), as picking it in the function field
        does: its Overview, or tab= ('logs', 'errors', 'performance', 'code', 'settings').
        """
        if tab is not None and tab not in _FUNCTION_TABS:
            raise ValueError(f"tab= is one of {', '.join(map(repr, _FUNCTION_TABS))}; got {tab!r}")
        parse_function_ref(name)  # says what a name looks like, before anything is read
        if self._w is None:
            self._report_for(name, tab)
            return
        if self._listing:
            self._want, self._want_tab, self._want_search = name, tab, None
            self._status(f"{name} opens once the functions are listed.")
            return
        self._open_named(name, tab)

    @_window_errors
    def logs(
        self, name: str | None = None, *, search: str | None = None, since: Any = None, until: Any = None
    ) -> None:
        """
        Shows a function's logs in the Logs tab (the one picked, or name=), as clicking the tab does. since= picks
        the time range ('15m', '1h', '24h', '7d', a date...), and until= where it ends ('2026-10-28 17:30', '1d', a
        datetime; now when left out), as the range field's Custom range does. search= finds the runs with a line that
        has the text (an order ID, KeyError, a request ID, or a CloudWatch Logs filter pattern), searching the whole
        time range.
        """
        ranged = since is not None or until is not None
        if ranged:
            since = self._range if since is None else since
            LambdaAnalyzer.time_range(since, until)  # a time it can't read is said before anything changes
        if self._w is None:
            target = name or (self.function.name if self.function is not None else None)
            if target is None:
                raise _Hint("Name the function: x.logs('my-function').")
            if until is not None:
                raise _Hint("until= is the explorer window's (it needs ipywidgets in Jupyter): without it, logs() reads "
                            "up to now, so pick since= alone.")
            self.ui.logs(target, search=search, since=since)
            return
        if ranged:
            self._set_range(since, until)
        if name is not None:
            if self._listing:
                self._want, self._want_tab, self._want_search = name, "logs", search
                self._status(f"{name}'s logs open once the functions are listed.")
                return
            self._open_named(name, "logs", search=search)
            return
        if self.function is None:
            raise _Hint("Pick a function first: click one in the Functions tab, or x.logs('my-function').")
        if search:
            self._show_tab("logs", load=False)
            self._quietly(self.log_find, value=search)
            self._log_query = search
            self._log_search()
        else:
            self._show_tab("logs", load=False)
            self._load_logs()

    @_window_errors
    def run(self, request_id: str) -> None:
        """
        Shows one run of the picked function in the Logs tab, every line of it, by its request ID (from an error, a
        REPORT line or your own logs), looking back at least 24 hours.
        """
        if self.function is None:
            raise _Hint("Pick a function first: click one in the Functions tab, or x.open('my-function').")
        if self._w is None:
            self.ui.logs(self.function.name, request_id=request_id)
            return
        if self._until is not None or _seconds(self._range) < 86400:
            self._set_range("24h")
        self._show_tab("logs", load=False)
        self._quietly(self.log_find, value=request_id)
        self._log_query = request_id.strip()
        self._log_search()

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the functions, and the picked function's tabs (the ↻ button), forgetting what the
        window kept about the functions opened before.
        """
        keep = self.function
        self._rereading = True
        self._load_list(reopen=keep)

    # ------------------------------------------------------------------ plumbing

    def _reports(self, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: function_info('name') explains one function, logs('name') shows what it "
                                 "logged and errors('name') why it fails.")])
        if self._want:
            self._report_for(self._want, self._want_tab)
        else:
            regions = "all" if self.region == "all" else [self.region] if self.region else None
            self.ui.functions(regions=regions)

    def _report_for(self, name: str, tab: str | None) -> None:
        """
        A function's tab, as a report (without the window).
        """
        region = None if self.region in (None, "all") else self.region
        picked = {"since": self._range} if self._linked else {}
        if tab == "logs":
            self.ui.logs(name, region=region, search=self._want_search, **picked)
        elif tab in ("errors", "performance"):
            {"errors": self.ui.errors, "performance": self.ui.performance}[tab](name, region=region, **picked)
        else:
            command = {"code": self.ui.code}.get(tab or "", self.ui.function_info)
            command(name, region=region)

    @property
    def multi(self) -> bool:
        """
        Whether the list holds more than one region's functions.
        """
        return self.region == "all" or bool(self.overview is not None and len(self.overview.regions) > 1)

    def _status(self, text: str, level: str = "", busy: bool = False, owner: str = "") -> None:
        """
        The line under the tabs, and with it the code that opens what the window shows (it changes with each).
        """
        super()._status(text, level, busy, owner)
        if self.view_code is not None:
            self._set(self.view_code, _view_code_html(self._view_code()))

    def _view_code(self) -> str:
        """
        The one call that opens the window as it is now, to run in another cell or send to someone: the function,
        its tab, the region, and on the Logs tab the time range and the search.
        """
        if self._tab == "live":
            return self._live_code()
        fn = self.function
        args = [fn.name] if fn is not None else []
        kwargs: dict[str, Any] = {}
        if fn is not None and self._tab in _FUNCTION_TABS and self._tab != "overview":
            kwargs["tab"] = self._tab
        region = self.region if self.region == "all" or fn is None else fn.region or self.region
        if region and (region == "all" or region != self._session_region()):
            kwargs["region"] = region
        if fn is not None and self._tab == "logs":
            kwargs.update(self._logs_code_args())
        return _call("explore", *args, **kwargs)

    def _live_code(self) -> str:
        """
        explore() on the Live tab, following the function followed.
        """
        followed = self._followed()
        args = [followed.function.name] if followed is not None else []
        kwargs: dict[str, Any] = {"tab": "live"}
        if self.region and (self.region == "all" or self.region != self._session_region()):
            kwargs["region"] = self.region
        return _call("explore", *args, **kwargs)

    def _logs_code_args(self) -> dict[str, str]:
        """
        The Logs tab's time range and search, as explore()'s arguments; none when they're as it opens.
        """
        args = {}
        if self._until is not None or self._range != _FIRST_LOGS_RANGE:
            args["since"] = _time_text(self._range)
        if self._until is not None:
            args["until"] = _time_text(self._until)
        if self.found is not None and self._searched:
            args["search"] = self._searched
        elif self._asks():
            args["search"] = self._log_query
        return args

    def _session_region(self) -> str | None:
        try:
            return self.core.region
        except ValueError:
            return None

    def _where(self) -> str:
        return "every region" if self.region == "all" else str(self.region or "this region")

    def _full(self) -> Function:
        """
        The picked function as GetFunction gave it, once describe() has read it (else as the list has it).
        """
        assert self.function is not None
        return self.detail.function if self.detail is not None else self.function

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, drawn in the report
        theme the window opened with, and without the report and theme CSS each one carries (the window's style holds
        them).
        """
        blocks = _for_window(blocks, _window_text)
        html = _render_html(blocks, self.ui.max_rows, theme=self._theme)
        return html.replace(_CSS, "", 1).replace(self._theme_style, "", 1)

    def _empty(self, key: str, text: str) -> Any:
        """
        The line an empty list shows, one widget per list, kept.
        """
        if key not in self._empties:
            self._empties[key] = self._w.HTML(layout=self._w.Layout(width="100%"))
        self._set(self._empties[key], f'<div class="lmx-empty">{_esc(text)}</div>')
        return self._empties[key]

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("lmx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.region_pick = w.Dropdown(options=[("Region: …", "")], value="", layout=layout(width="auto"))
        self.region_pick.add_class("lmx-region")
        self.region_pick.observe(self._safely(self._region_changed), names="value")
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read everything again: the functions, and "
                                       "the picked function's tabs", layout=layout(width="auto"))
        self.refresh_button.add_class("lmx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        self.view_code = w.HTML(layout=layout(width="auto"))
        self.view_code.add_class("lmx-vc")
        top = w.HBox([self.title, self.view_code, self.region_pick, self.refresh_button], layout=layout(width="100%"))
        top.add_class("lmx-top")
        self.field = _FunctionField(self, on_pick=self._picked, on_text=self._typed)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.field, self.stats], layout=layout(width="100%"))
        meta.add_class("lmx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("lmx-head")
        self.head_box = head

        tabs = self._tab_bar(_EXPLORER_TABS)

        self.overview_view = w.HTML(layout=layout(width="100%"))
        self.settings_view = w.HTML(layout=layout(width="100%"))
        self._theme = _theme_name()
        self._theme_style = _theme_css(_ROOT, self._theme)
        self._frame(_CSS + _EXPLORER_CSS + _LIVE_CSS + _PLAIN_CSS + self._theme_style, head, tabs, {
            "functions": self._functions_page(),
            "live": self._live_page(),
            "overview": self._scroll_page([self.overview_view]),
            "logs": self._logs_page(),
            "errors": self._errors_page(),
            "performance": self._performance_page(),
            "code": self._code_page(),
            "settings": self._scroll_page([self.settings_view]),
        })
        self._show_tab(self._tab)

    def _scroll_page(self, children: list[Any], above: list[Any] | None = None) -> Any:
        """
        A tab's page: what stays at its top (`above`), then a box that scrolls (`children`).
        """
        w = self._w
        scroller = w.VBox(children, layout=w.Layout(width="100%"))
        scroller.add_class("lmx-scroll")
        return w.VBox([*(above or []), scroller])

    def _renew(self, key: str) -> None:
        """
        A new scrolling box for a tab's page, which starts at the top (widgets can't be scrolled from Python).
        """
        page = self.pages[key]
        old = page.children[-1]
        scroller = self._w.VBox(list(old.children), layout=self._w.Layout(width="100%"))
        scroller.add_class("lmx-scroll")
        page.children = [*page.children[:-1], scroller]

    def _make_pager(self, key: str) -> Any:
        w, layout = self._w, self._w.Layout
        text = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        buttons = {}
        for where, glyph, tip in (("first", "«", "The first page"), ("previous", "‹", "The page before"),
                                  ("next", "›", "The next page"), ("last", "»", "The last page")):
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("lmx-pg")
            button.on_click(self._safely(lambda _button, where=where: self._page(key, where)))
            buttons[where] = button
        self._pagers[key] = (text, buttons)
        return [text, *buttons.values()]

    def _draw_pager(self, key: str, offset: int, total: int, size: int, noun: str, whole: int | None = None,
                    label: str | None = None) -> None:
        text, buttons = self._pagers[key]
        first, last = offset + 1, min(offset + size, total)
        shown = f"<b>{first:,}–{last:,}</b> of {total:,} {noun}" if total else f"0 {noun}"
        self._set(text, label if label is not None else
                  shown + (f" (of {whole:,})" if whole is not None and whole != total else ""))
        more = total > size
        for where, button in buttons.items():
            button.layout.display = "" if more else "none"
            button.disabled = (offset == 0) if where in ("first", "previous") else (last >= total)

    def _page(self, key: str, where: str) -> None:
        size = {"functions": _FUNCTION_PAGE, "runs": _RUN_PAGE, "files": _CODE_PAGE}[key]
        total = {"functions": len(self._visible), "runs": len(self._visible_runs()),
                 "files": len(self._code_lines())}[key]
        offset = {"functions": self._offset, "runs": self._run_offset, "files": self._code_offset}[key]
        end = max(0, (total - 1) // size * size)
        offset = {"first": 0, "previous": max(0, offset - size), "next": min(end, offset + size), "last": end}[where]
        if key == "functions":
            self._offset = offset
            self._draw_rows(renew=True)
        elif key == "runs":
            self._run_offset = offset
            self._draw_runs(renew=True)
        else:
            self._code_offset = offset
            self._draw_code_rows(renew=True)

    def _show_tab(self, key: str, *, load: bool = True) -> None:
        if key != "logs" and self._live:
            self._stop_live("Live stopped: it runs while the Logs tab is open.")
        if key != "live" and self._watching:
            self._stop_watch("Live stopped: it watches while the Live tab is open.")
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "lmx-on", name == key)
            _class_if(button, "lmx-dim", name in _FUNCTION_TABS and self.function is None and name != key)
            self.pages[name].layout.display = "" if name == key else "none"
        if key == "live" and load:
            self._open_live()
        elif key in _FUNCTION_TABS and self.function is None:
            self._need_function(key)
        elif load and key in _FUNCTION_TABS and key not in self._asked:
            {"logs": self._load_logs, "errors": self._load_errors, "performance": self._load_perf,
             "code": self._load_code}.get(key, lambda: None)()
        if not self._said[2] and self._said[1] != "warn":  # the line under the tabs says what this one does
            self._status(self._live_line() if key == "live" else self._tab_line(key))

    def _need_function(self, key: str) -> None:
        """
        A function's tab before one is picked: how to pick one.
        """
        hint = ('<div class="lmx-hint">👆 <div><b>Pick a function first.</b> Click one in the <b>Functions</b> tab, or '
                "in the function field above (it searches as you type).</div></div>")
        widget = {"overview": self.overview_view, "settings": self.settings_view, "errors": self.errors_view,
                  "performance": self.perf_view, "code": self.code_view, "logs": self.run_head}[key]
        self._set(widget, hint)

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        fn, ov = self.function, self.overview
        if key == "functions":
            if ov is None:
                return ""
            attention = sum(1 for f in self._facts.values() if f.warnings)
            return (f"{_plural(len(ov.functions), 'function')} in {self._where()}"
                    + (f" · {attention:,} need attention, listed first" if attention else "")
                    + " · click one to open it, or Logs on its line for its logs")
        if fn is None:
            return "Pick a function: click one in the Functions tab, or in the field above."
        if key == "logs":
            return self._logs_line()
        if key == "overview" and self.detail is not None:
            warnings = sum(level == "warn" for level, _ in function_findings(
                self.detail.function, self.detail.metrics, self.detail, prices=self.core.prices))
            return (f"{fn.name}: " + (_plural(warnings, "warning") if warnings else "no warnings")
                    + " · the Logs tab shows what it logged, run by run")
        if key == "errors" and self.error_report is not None:
            report = self.error_report
            return (f"{_plural(report.errors_found, 'error line')} in the last "
                    f"{_window((report.until - report.since).total_seconds())}, "
                    f"{_plural(len(report.groups), 'cause')} · click a failed run to read it")
        if key == "performance" and self.perf is not None:
            return (f"{_plural(len(self.perf.invocations), 'run')} in the last "
                    f"{_window((self.perf.until - self.perf.since).total_seconds())} · click a slow run to read it")
        if key == "code" and self.package is not None:
            if not self.package.files:
                return ""
            files = f"{_plural(len(self.package.files), 'file')} in its package"
            if self._how and self.code_map is not None:
                return (f"How it works, read from {_plural(self.code_map.files, 'Python file')} with nothing run · "
                        "click a file to see its source")
            how = " · How it works explains the code" if self._reads_python() else ""
            if not self._files_shown:
                return f"{files} · Show files lists them beside the source{how}"
            return f"{files} · click a folder to open it, a file to see its source{how}"
        if key == "settings" and self.detail is not None:
            return "Every setting in plain English, then as Lambda returns it (folded, at the end)."
        return ""

    # ------------------------------------------------------------------ the functions

    def _begin(self) -> None:
        try:
            if self.region != "all":
                self.region = self.region or self.core.region
        except ValueError as exc:  # no region set anywhere
            self._status(str(exc), "warn")
            self._set(self.list_note, self._html([_Note(str(exc), "warn")]))
            self._draw_title()
            return
        self._fill_regions()
        self._load_list()

def _time_text(value: Any) -> str:
    """
    A time range's end as explore() reads it: '24h' as it is, a moment as text in the reports' time zone
    ('2026-10-27 09:00').
    """
    if isinstance(value, datetime):
        return f"{_in_zone(value):%Y-%m-%d %H:%M}"
    return str(value)


def _view_code_html(code: str) -> str:
    """
    The header's Copy as code: a click selects the code it holds (hidden: the button shows only its label), so
    Ctrl+C copies it, with no JavaScript (the window has none).
    """
    return (f'<span class="cpy" tabindex="0" role="button" aria-label="Copy this view as code" title="{_esc(code)}'
            '\n\nClick, then press Ctrl+C (⌘C on a Mac) to copy it, and run it in a cell (after from '
            'aws_analyzer.lambda_functions import explore) to open the window as it is now">'
            f'<span class="cpt">{_esc(code)}</span></span>')


def explore(
    name: str | None = None,
    *,
    tab: str | None = None,
    since: Any = None,
    until: Any = None,
    search: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
    history: bool | str = True,
    assistant: Any = None,
) -> LambdaExplorer:
    """
    Opens the Lambda explorer window and returns it: every function in the region, what's running now (the Live tab),
    and for the one you click, how it's doing, its logs run by run (failed runs in red, a search box, a time range, and Live to watch new runs come
    in), its errors grouped by cause, its run times, the code in its package and every setting, all by clicking.

        explore()                                   # every function in the notebook's region
        explore("orders-etl")                       # straight to one function (a name, an ARN or a console link)
        explore("orders-etl", tab="logs")           # ...on its logs ('errors', 'performance', 'code', 'settings')
        explore("orders-etl", tab="logs", since="3h", search="KeyError")  # ...its last 3 hours, searched
        explore(tab="live")                         # what's running now, looked at every few seconds
        explore("orders-etl", tab="live")           # ...following one function's runs as they come
        explore(region="all")                       # every region your account has turned on
        explore(region="eu-west-1", profile="dev")  # another region or AWS profile

    since= and until= pick the time range of the Logs, Errors and Performance tabs ('3h', '7d', '2026-10-27 09:00'),
    and search= finds the runs whose lines have the text (on the Logs tab). The window's ⧉ Copy as code gives this
    call for what it shows. The tabs' pages fill the browser's height; height= sets theirs instead (800 pixels, or CSS
    such as '70vh'). It only reads: where a change would help, it shows the command to run. It remembers the functions
    you open, for its Recent chip (history=False: it doesn't). assistant=AgentView(...) adds ✦ Ask: a drawer where
    you ask the agent about what the window shows.
    """
    return LambdaExplorer(name, tab=tab, since=since, until=until, search=search, region=region, profile=profile,
                          height=height, history=history, assistant=assistant)
