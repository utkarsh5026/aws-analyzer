"""
The Live tab: what's running now, every function that ran in the last minutes with its runs going, and one function
followed run by run as its lines come; and the tab's mixin.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, NamedTuple

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _Hint, _error_code, _why
from ..._kit.fmt import _in_zone, _plural, _utcnow, _zone_name, human_money
from ..._kit.render import _prose
from ..._kit.text import _esc
from ..._kit.window import _class_if, _running_loop, _window_errors

from ..helpers import parse_function_ref
from ..models import Function, LogEvent
from ..analysis.logs import LogRun
from ..analysis.live import (
    LIVE_WINDOW_MINUTES, LiveFunction, in_flight, live_rank, ran_lately, running_for, timeout_warning,
)
from .frame import _run_clock
from .functions import _trend_html
from .logs import _run_body, _run_face, _run_summary
from .widgets import _Row, _RunRow


_WATCH_SECONDS = 5

_ACTIVITY_SECONDS = 60

_WATCH_MINUTES = 15

_FOLLOW_MOST = 5

_FIRST_LINES = 1_000

_NEW_LINES = 2_000

_LIVE_ROWS = 25

_LIVE_RUNS = 25

_QUIET_NAMED = 4

_OVERLAP = timedelta(seconds=2)

_JUST_NOW_SECONDS = 5

_TENTHS_UNDER = 10

_ENDED_OR_GOING = "No REPORT line yet: still running, or it ended after the time range"

_STILL_GOING = "Still running: no REPORT line yet"

_LineRead = tuple[LiveFunction, datetime, int]

_LIVE_CSS = """<style>
.lmx-app.lmx-app .lmx-livebar{align-items:center;gap:10px}
.lmx-app.lmx-app .lmx-livebar>*{margin:0}
.lmx-app .lmx-lsum .widget-html-content,.lmx-app .lmx-lsum .jupyter-widget-html-content{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .lvsum{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .lvsum b{font-weight:650}
.lmx-app .lvsum .lvgo{color:var(--lx-accent)}
.lmx-app .lvsum .bad{color:var(--lx-ink-bad)}
.lmx-app .lvsum .lvsep{opacity:.4;margin:0 7px}
.lmx-app .lvf{display:grid;grid-template-columns:10px minmax(0,1fr) 104px 64px 88px 76px 76px;align-items:center;gap:0 12px;height:54px;padding:0 14px 0 12px;min-width:0}
.lmx-app .lvf .lvn{min-width:0;line-height:1.3}
.lmx-app .lvf .lvt{display:flex;align-items:baseline;gap:8px;min-width:0;white-space:nowrap}
.lmx-app .lvf .lvt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0}
.lmx-app .lvf .lvs{font-size:11.5px;opacity:.68;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.lmx-app .lvf .lvs.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app .lvf .lvc{text-align:right;font-variant-numeric:tabular-nums;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .lvf .lvc.dim{opacity:.5}
.lmx-app .lvf .lvc.bad{color:var(--lx-ink-bad);font-weight:600}
.lmx-app .lvb{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11.5px;font-weight:650;background:var(--lx-soft);color:var(--lx-accent);white-space:nowrap}
.lmx-app .lvb.q{background:transparent;color:inherit;opacity:.5;font-weight:500;padding:0}
.lmx-app .dot.live{background:var(--lx-accent);animation:lmx-beat 1.6s ease-in-out infinite}
.lmx-app .rr.lvr .ri{animation:lmx-beat 1.6s ease-in-out infinite}
@keyframes lmx-beat{0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--lx-accent) 45%,transparent)}50%{box-shadow:0 0 0 5px transparent}}
.lmx-app .lvwarn{color:var(--lx-ink-warn);font-size:12px;line-height:1.45;padding:8px 14px 6px 40px;background:var(--lx-code)}
.lmx-app .lvwait{color:var(--lx-accent);font-size:12px;padding:2px 14px 10px 40px;background:var(--lx-code)}
.lmx-app .lvwait::after{content:"";display:inline-block;width:7px;height:13px;margin-left:6px;vertical-align:-2px;background:var(--lx-accent);opacity:.6;animation:lmx-blink 1s steps(2) infinite}
@keyframes lmx-blink{50%{opacity:0}}
.lmx-app .lvnote{font-size:12px;opacity:.68;padding:8px 4px 0;line-height:1.45}
.lmx-app .lvnote.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app.lmx-app button.lmx-tab.lmx-onair::after{content:"";width:7px;height:7px;border-radius:50%;background:#ef4444;flex:0 0 auto;animation:lmx-pulse 1.4s ease-in-out infinite}
@container (max-width:760px){.lmx-app .lvf{grid-template-columns:10px minmax(0,1fr) 96px 80px 70px}.lmx-app .lvf .c-spark,.lmx-app .lvf .c-last{display:none}}
.lmx-app.lmx-app .lmx-livepage{container-type:inline-size}
</style>"""


def _elapsed(seconds: float) -> str:
    """
    How long a run has been going: '0.4 s', '41 s', '3m 05s'.
    """
    if seconds < _TENTHS_UNDER:
        return f"{seconds:.1f} s"
    whole = int(seconds)
    return f"{whole} s" if whole < 60 else f"{whole // 60}m {whole % 60:02d}s"


def _timeout_text(seconds: int) -> str:
    """
    A timeout as the runs' lines show it: '60 s', '15 min'.
    """
    return f"{seconds // 60} min" if seconds >= 120 and seconds % 60 == 0 else f"{seconds} s"


def _ago(seconds: float) -> str:
    """
    'just now', '12 s ago', '3 min ago'.
    """
    if seconds < _JUST_NOW_SECONDS:
        return "just now"
    return f"{seconds:.0f} s ago" if seconds < 60 else f"{seconds // 60:.0f} min ago"


def _seen_text(item: LiveFunction, now: datetime) -> str:
    """
    When a function was last seen running: its newest line, else the newest minute CloudWatch counted a call in.
    """
    if item.last_line is not None:
        return _ago((now - item.last_line).total_seconds())
    minutes = item.activity.minutes_since_called if item.activity is not None else None
    if minutes is None:
        return "-"
    return "this minute" if minutes == 0 else f"{minutes} min ago"


def _last_said(run: LogRun) -> str:
    """
    What a run going says on its line: what failed, if something did, else the newest thing it logged.
    """
    if run.error is not None:
        return _run_summary(run)[0]
    for event in reversed(run.events):
        text = event.message.strip()
        if text and not text.startswith(("START RequestId", "INIT_START")):
            return _run_summary(LogRun(run.request_id, run.stream, [event]))[0]
    return "started: nothing logged yet"


def _live_face(item: LiveFunction, now: datetime, multi: bool) -> str:
    """
    A function's line on the Live tab: a dot that beats while it runs, its name and what its newest run is doing (or
    its description), how many runs are going, its calls a minute (the failed part in red), its calls and failed
    calls in the live window, and when it was last seen.
    """
    fn, act = item.function, item.activity
    going = item.running(now)
    tone = "live" if going else "bad" if act is not None and act.total_errors else "ok"
    about, about_tone = fn.description or fn.runtime or "container image", ""
    if going:
        newest = going[0]
        about = f"{_elapsed(running_for(newest, now))} into a run · {_last_said(newest)}"
        about_tone = "warn" if timeout_warning(newest, fn, now) else ""
    badge = (f'<span class="lvb">{len(going)} running</span>' if going else '<span class="lvb q">idle</span>')
    spark = _trend_html(list(zip(act.calls, act.errors))) if act is not None else "-"
    calls = act.total_calls if act is not None else None
    failed = act.total_errors if act is not None else None
    region = f'<span class="rg">{_esc(fn.region)}</span>' if multi else ""
    calls_cell = f"{calls:,} calls" if calls is not None else "-"
    failed_cell = f"{failed:,} failed" if failed is not None else "-"
    return (f'<div class="lvf"><span class="dot {tone}"></span><div class="lvn"><div class="lvt"><b>{_esc(fn.name)}'
            f'</b>{region}</div><div class="lvs {about_tone}">{_esc(about)}</div></div><div>{badge}</div>'
            f'<div class="lvc c-spark">{spark}</div><div class="lvc{"" if calls else " dim"}">{calls_cell}</div>'
            f'<div class="lvc{" bad" if failed else " dim"}">{failed_cell}</div>'
            f'<div class="lvc c-last">{_esc(_seen_text(item, now))}</div></div>')


def _live_run_face(run: LogRun, fn: Function, now: datetime, today: date) -> str:
    """
    A run still going, as its line shows it: when it started, how long it has run against the timeout (amber, then
    red, as it nears it), a cold start, the last thing it logged, and its request ID.
    """
    seconds = running_for(run, now)
    share = min(1.0, seconds / fn.timeout) if fn.timeout else 0.0
    fill = "bad" if share >= 0.9 else "warn" if share >= 0.7 else ""
    took = (f'<span class="rb"><i class="{fill}" style="width:{max(share * 100, 2):.1f}%"></i></span>'
            f"{_esc(_elapsed(seconds))}")
    summary = _last_said(run)
    cold = "cold" if run.cold else ""
    return (f'<div class="rr run lvr"><span class="rv"></span><span class="ri">•</span><span class="rw">'
            f'{_esc(_run_clock(run.start, today))}</span><span class="rd">{took}</span><span class="rm">of '
            f'{_esc(_timeout_text(fn.timeout))}</span><span class="rc">{cold}</span><span class="rs">{_esc(summary)}'
            f'</span><span class="rq">{_esc((run.request_id or "")[:8])}</span></div>')


def _live_run_body(run: LogRun, fn: Function, now: datetime, region: str) -> str:
    """
    An open run that's still going: a word when it's near its timeout, its lines so far, and that more are coming.
    """
    warning = timeout_warning(run, fn, now)
    warn = f'<div class="lvwarn">{_prose(warning)}</div>' if warning else ""
    body = _run_body(run, [], region=region, log_group=fn.log_group).replace(_ENDED_OR_GOING, _STILL_GOING, 1)
    wait = f'<div class="lvwait">waiting for more lines (it looks every {_WATCH_SECONDS} s)</div>'
    return f"{warn}{body}{wait}"


class _Scene(NamedTuple):
    """
    What drawing the followed function's runs shares: the function, the moment, today in the reports' zone, its
    region, and the keys of its runs going.
    """

    function: Function
    now: datetime
    today: date
    region: str
    going: set[str]


class _LiveTab:
    """
    The Live tab of LambdaExplorer: every function that ran in the last LIVE_WINDOW_MINUTES minutes, the ones with
    runs going first, read again every few seconds while the tab is open; and one function followed, its runs newest
    first with the ones going open, their lines added as they come.

    CloudWatch's numbers say which functions ran (read every _ACTIVITY_SECONDS: they come a minute or two late), and
    the newest lines of the busiest _FOLLOW_MOST (every _WATCH_SECONDS) say which runs are going: a START line without
    a REPORT line. A followed function's lines are read whether CloudWatch counted it or not.
    """

    def _init_live(self) -> None:
        self.board: dict[str, LiveFunction] = {}
        self._following = ""
        self._live_wanted: str | None = None
        self._watching, self._watch_token, self._watch_started = False, 0, 0.0
        self._activity_at: float | None = None
        self._activity_errors: dict[str, str] = {}
        self._metrics_per_read = 0
        self._checked: datetime | None = None
        self._read_until: dict[str, datetime] = {}
        self._live_rows: list[_Row] = []
        self._live_run_rows: list[_RunRow] = []
        self._live_open: set[str] = set()
        self._live_shut: set[str] = set()

    def _live_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.watch_button = w.Button(description="Live", tooltip=f"Watch what's running: new lines every "
                                     f"{_WATCH_SECONDS} seconds, CloudWatch's numbers every minute (stops after "
                                     f"{_WATCH_MINUTES} minutes, or when you leave the tab)", layout=layout(width="auto"))
        self.watch_button.add_class("lmx-live")
        self.watch_button.on_click(self._safely(lambda _button: self._toggle_watch()))
        self.live_back = w.Button(description="‹ All functions", tooltip="Back to every function that ran lately",
                                  layout=layout(width="auto", display="none"))
        self.live_back.add_class("lmx-small")
        self.live_back.on_click(self._safely(lambda _button: self._unfollow()))
        self.live_summary = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.live_summary.add_class("lmx-lsum")
        self.live_logs_button = w.Button(description="Its Logs tab ›", tooltip="Open this function's Logs tab: "
                                         "search its lines, pick a longer time range", layout=layout(width="auto",
                                                                                                     display="none"))
        self.live_logs_button.add_class("lmx-small")
        self.live_logs_button.on_click(self._safely(lambda _button: self._live_to_logs()))
        bar = w.HBox([self.watch_button, self.live_back, self.live_summary, self.live_logs_button],
                     layout=layout(width="100%"))
        for name in ("lmx-bar", "lmx-livebar"):
            bar.add_class(name)
        self.live_box = w.VBox(layout=layout(width="100%", flex="1 1 auto"))
        self.live_box.add_class("lmx-rows")
        self.live_note = w.HTML(layout=layout(width="100%"))
        self.live_page = w.VBox([bar, self.live_box, self.live_note])
        self.live_page.add_class("lmx-livepage")
        return self.live_page


    @_window_errors
    def live(self, name: str | None = None) -> None:
        """
        Shows the Live tab: the functions that ran in the last 10 minutes, the ones running now first, each with its
        runs going and its calls a minute, looked at again every few seconds while the tab is open (for 15 minutes).
        name= follows one function (its name or ARN): its runs, newest first, the ones going open with their lines
        as they come.
        """
        if self._w is None:
            raise _Hint("The Live tab is the explorer window's (it needs ipywidgets in Jupyter). In a report, "
                        "x.ui.logs('my-function', since='15m') shows a function's newest runs.")
        if name is not None and self.overview is None:
            self._live_wanted = name
            self._status(f"Live follows {name} once the functions are listed.")
        elif name is not None:
            self._follow(self._live_function(name))
        self._show_tab("live")


    def _live_function(self, name: str) -> Function:
        """
        The listed function a name, 'name:alias', ARN or console link names, or a hint naming where it looked.
        """
        wanted, _, region = parse_function_ref(name)
        functions = self.overview.functions if self.overview is not None else []
        found = [fn for fn in functions if fn.name == wanted and (region is None or fn.region == region)]
        if not found:
            raise _Hint(f"{wanted} isn't among the functions listed in {self._where()}: check the name, or pick its "
                        "region in the region field.")
        return found[0]

    def _item(self, fn: Function) -> LiveFunction:
        item = self.board.get(fn.arn)
        if item is None:
            item = self.board[fn.arn] = LiveFunction(fn)
        return item

    def _board_items(self) -> list[LiveFunction]:
        functions = self.overview.functions if self.overview is not None else []
        return [self._item(fn) for fn in functions]

    def _followed(self) -> LiveFunction | None:
        return self.board.get(self._following) if self._following else None

    def _open_live(self) -> None:
        """
        The Live tab shown: starts watching (once the functions are listed), or draws what it has.
        """
        if self.overview is not None and self.overview.functions and not self._watching:
            self._start_watch()
        else:
            self._draw_live()

    def _reset_live(self) -> None:
        """
        Forgets what the tab read (the functions are listed again): it reads afresh when it's shown.
        """
        self._stop_watch()
        self.board, self._read_until, self._activity_errors = {}, {}, {}
        self._activity_at, self._checked = None, None
        self._drop("live-follow")
        self._draw_live()

    def _live_listed(self) -> None:
        """
        The functions are listed: follow the one explore(tab='live') or live() named, or drop the one followed when
        another region's list doesn't have it, and watch if the tab is open.
        """
        functions = {fn.arn for fn in self.overview.functions} if self.overview is not None else set()
        if self._following not in functions:
            self._following = ""
        wanted, self._live_wanted = self._live_wanted, None
        if wanted:
            try:
                self._following = self._live_function(wanted).arn
            except _Hint as hint:
                self._status(str(hint), "warn")
        if self._tab == "live":
            self._open_live()


    def _toggle_watch(self) -> None:
        if self._watching:
            self._stop_watch("Live is off: click Live to watch again.")
            return
        if self.overview is None:
            self._status("Live starts once the functions are listed.")
            return
        self._start_watch()

    def _start_watch(self) -> None:
        """
        Watches: a look now, then one every _WATCH_SECONDS on the notebook's loop (just the one look without a loop: a
        script, the tests).
        """
        if self.overview is None or not self.overview.functions:
            self._draw_live()
            return
        self._watching, self._watch_started = True, time.monotonic()
        self._watch_token += 1
        self._mark_watching(True)
        self._status(self._watch_line(), "live")
        loop = _running_loop()
        if loop is None:
            self._watch_once()
            return
        self._tasks["watch"] = loop.create_task(self._watch_loop(self._watch_token))

    def _stop_watch(self, text: str = "", level: str = "") -> None:
        was = self._watching
        self._watching = False
        self._watch_token += 1
        self._mark_watching(False)
        if was and text:
            self._status(text, level)

    def _mark_watching(self, on: bool) -> None:
        if hasattr(self, "watch_button"):
            _class_if(self.watch_button, "lmx-on", on)
            _class_if(self.tab_buttons["live"], "lmx-onair", on)

    def _is_watching(self, token: int) -> bool:
        return self._watching and self._watch_token == token

    def _watch_once(self) -> None:
        if self._activity_due():
            self._got_activity(self._activity_work()())
        self._got_lines(self._read_lines(self._line_reads()))

    async def _watch_loop(self, token: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._is_watching(token):
                try:
                    if not await self._watch_tick(loop, token):
                        return
                except Exception as exc:  # noqa: BLE001
                    if self._watch_token == token:
                        self._stop_watch(f"Live stopped: {self._error_text(exc)}", "warn")
                    return
                if time.monotonic() - self._watch_started > _WATCH_MINUTES * 60:
                    self._stop_watch(f"Live stopped after {_WATCH_MINUTES} minutes: click Live to watch again.")
                    return
                await asyncio.sleep(_WATCH_SECONDS)
        finally:
            if self._tasks.get("watch") is asyncio.current_task():
                del self._tasks["watch"]

    async def _watch_tick(self, loop: asyncio.AbstractEventLoop, token: int) -> bool:
        """
        One look, read on a worker thread and drawn here: CloudWatch's numbers when they're due, then the newest
        lines of the functions followed. False when watching stopped meanwhile (what came back is dropped).
        """
        if self._activity_due():
            got = await loop.run_in_executor(self._workers(), self._activity_work())
            if not self._is_watching(token):
                return False
            self._got_activity(got)
        reads = self._line_reads()
        lines = await loop.run_in_executor(self._workers(), lambda: self._read_lines(reads))
        if not self._is_watching(token):
            return False
        self._got_lines(lines)
        return True

    def _activity_due(self) -> bool:
        return self._activity_at is None or time.monotonic() - self._activity_at >= _ACTIVITY_SECONDS

    def _activity_work(self) -> Callable[[], Any]:
        self._activity_at = time.monotonic()
        functions = list(self.overview.functions) if self.overview is not None else []
        return lambda: self.core.activity(functions)

    def _got_activity(self, got: tuple[dict[str, Any], int, dict[str, str]]) -> None:
        found, read, errors = got
        self._activity_errors, self._metrics_per_read = errors, read
        for item in self._board_items():
            item.activity = found.get(item.function.arn, item.activity)

    def _followed_functions(self, now: datetime) -> list[LiveFunction]:
        """
        The functions whose lines the next look reads: the one followed, else the first _FOLLOW_MOST of the ones that
        ran lately, in the list's order (CloudWatch's FilterLogEvents takes only a few requests a second per account).
        """
        followed = self._followed()
        if followed is not None:
            return [followed]
        active = [item for item in self._board_items() if ran_lately(item) or item.running(now)]
        return sorted(active, key=lambda item: live_rank(item, now))[:_FOLLOW_MOST]

    def _line_reads(self) -> list[_LineRead]:
        """
        What the next look reads, per function: from just before where its last read ended (reads overlap a moment,
        and the lines already held are left out), or its last LIVE_WINDOW_MINUTES minutes the first time.
        """
        now = _utcnow()
        reads = []
        for item in self._followed_functions(now):
            until = self._read_until.get(item.function.arn)
            if until is None:
                reads.append((item, now - timedelta(minutes=LIVE_WINDOW_MINUTES), _FIRST_LINES))
            else:
                reads.append((item, until - _OVERLAP, _NEW_LINES))
        return reads

    def _read_lines(self, reads: list[_LineRead]) -> dict[str, tuple[list[LogEvent], str, datetime]]:
        """
        The lines of each function in `reads` up to now (on a worker thread): ARN -> (lines, the error code of a read
        that failed, where the read ended).
        """
        end = _utcnow()
        got: dict[str, tuple[list[LogEvent], str, datetime]] = {}
        for item, start, limit in reads:
            fn = item.function
            try:
                events = self.core._filter(fn.log_group, fn.region, start, end, None, limit)[0]
            except ClientError as exc:
                got[fn.arn] = ([], _error_code(exc), end)
                continue
            except BotoCoreError as exc:
                got[fn.arn] = ([], type(exc).__name__, end)
                continue
            got[fn.arn] = (events, "", end)
        return got

    def _got_lines(self, got: dict[str, tuple[list[LogEvent], str, datetime]]) -> None:
        now = _utcnow()
        for arn, (events, error, end) in got.items():
            item = self.board.get(arn)
            if item is None:
                continue
            item.error = error
            item.add(events, now=now)
            self._read_until[arn] = end
        self._checked = now
        self._draw_live()
        if self._watching:
            self._status(self._watch_line(), "live")


    def _follow(self, fn: Function) -> None:
        """
        Follows one function: its runs in place of the list, read now (the newest LIVE_WINDOW_MINUTES minutes) if
        they haven't been, then with every look.
        """
        self._following = fn.arn
        self._live_open, self._live_shut = set(), set()
        self._item(fn)
        self._draw_live(renew=True)
        if self._tab == "live":
            self._status(self._live_line(), "live" if self._watching else "")
        if fn.arn not in self._read_until:
            reads = self._line_reads()
            self._later("live-follow", lambda: self._read_lines(reads), self._got_lines)

    def _unfollow(self) -> None:
        self._following = ""
        self._draw_live(renew=True)
        self._status(self._live_line(), "live" if self._watching else "")

    def _live_to_logs(self) -> None:
        followed = self._followed()
        if followed is not None:
            self._open_function(followed.function, "logs")

    def _toggle_live_run(self, row: _RunRow) -> None:
        run = row.run
        if run is None:
            return
        if "lmx-open" in row.box._dom_classes:
            self._live_open.discard(run.key)
            self._live_shut.add(run.key)
        else:
            self._live_open.add(run.key)
            self._live_shut.discard(run.key)
        self._draw_live()


    def _draw_live(self, renew: bool = False) -> None:
        if not hasattr(self, "live_box"):
            return
        followed = self._followed()
        self.live_back.layout.display = "" if followed is not None else "none"
        self.live_logs_button.layout.display = "" if followed is not None else "none"
        self._set(self.live_summary, self._live_summary_html(followed))
        children = self._run_children(followed) if followed is not None else self._board_children()
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%", flex="1 1 auto"))
            box.add_class("lmx-rows")
            self.live_page.children = [box if child is self.live_box else child for child in self.live_page.children]
            self.live_box = box
        else:
            self.live_box.children = children
        self._set(self.live_note, self._live_note_html(followed))

    def _shown_items(self, now: datetime) -> list[LiveFunction]:
        """
        The functions the list shows: the ones that ran in the live window, running ones first.
        """
        items = [item for item in self._board_items() if ran_lately(item) or item.running(now)]
        return sorted(items, key=lambda item: live_rank(item, now))[:_LIVE_ROWS]

    def _board_children(self) -> list[Any]:
        now = _utcnow()
        shown = self._shown_items(now)
        while len(self._live_rows) < len(shown):
            self._live_rows.append(_Row(self, self._follow))
        for row, item in zip(self._live_rows, shown):
            row.item = item.function
            self._set(row.face, _live_face(item, now, self.multi))
            tip = f"{item.function.name}: click to follow its runs, and their lines as they come"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        if not shown:
            return [self._empty("live", self._live_empty_text())]
        return [row.box for row in self._live_rows[:len(shown)]]

    def _run_children(self, item: LiveFunction) -> list[Any]:
        """
        The followed function's runs: the ones going first, open (unless folded), then the rest, newest first.
        """
        fn, now = item.function, _utcnow()
        today = _in_zone(now).date()
        runs = item.runs
        going = in_flight(runs, now, fn.timeout)
        going_keys = {run.key for run in going}
        ordered = (going + [run for run in runs if run.key not in going_keys])[:_LIVE_RUNS]
        while len(self._live_run_rows) < len(ordered):
            self._live_run_rows.append(_RunRow(self, on_toggle=self._toggle_live_run))
        scene = _Scene(fn, now, today, fn.region or self._session_region() or "", going_keys)
        for row, run in zip(self._live_run_rows, ordered):
            self._draw_live_run(row, run, scene)
        if not ordered:
            return [self._empty("live", self._followed_empty_text(item))]
        return [row.box for row in self._live_run_rows[:len(ordered)]]

    def _draw_live_run(self, row: _RunRow, run: LogRun, scene: _Scene) -> None:
        fn, now, today, region = scene.function, scene.now, scene.today, scene.region
        going = run.key in scene.going
        row.run = run
        face = _live_run_face(run, fn, now, today) if going else _run_face(run, fn.timeout, [], today)
        self._set(row.face, face)
        opened = (going and run.key not in self._live_shut) or run.key in self._live_open
        _class_if(row.box, "lmx-open", opened)
        if opened:
            body = (_live_run_body(run, fn, now, region) if going
                    else _run_body(run, [], region=region, log_group=fn.log_group))
            self._set(row.body, body)
        row.body.layout.display = "" if opened else "none"
        row.code_button.layout.display = "none"
        tip = "Click to fold its lines" if opened else "Click to see its lines"
        if row.button.tooltip != tip:
            row.button.tooltip = tip

    def _live_summary_html(self, followed: LiveFunction | None) -> str:
        """
        The line beside Live: how many functions ran and how many runs are going, their calls and failed calls in the
        live window; for the one followed, its own.
        """
        now = _utcnow()
        sep = '<span class="lvsep">·</span>'
        items = [followed] if followed is not None else self._board_items()
        going = sum(len(item.running(now)) for item in items)
        calls = sum(item.activity.total_calls for item in items if item.activity is not None)
        failed = sum(item.activity.total_errors for item in items if item.activity is not None)
        numbers = f"{calls:,} {'call' if calls == 1 else 'calls'}" + (
            f', <span class="bad">{failed:,} failed</span>' if failed else "")
        window = f"in the last {LIVE_WINDOW_MINUTES} minutes"
        running = f'<span class="lvgo"><b>{going:,}</b> running now</span>'
        if followed is not None:
            head = f"<b>{_esc(followed.function.name)}</b>"
        elif self._checked is None and not self._watching:
            return ""
        else:
            ran = sum(1 for item in items if ran_lately(item))
            head = f"<b>{ran:,}</b> of {_plural(len(items), 'function')} ran"
        return f'<div class="lvsum">{head}{sep}{running}{sep}{numbers} {window}</div>'

    def _live_empty_text(self) -> str:
        where = self._where()
        if self.overview is None:
            return "Listing the functions…" if self._listing else "No functions listed."
        if not self.overview.functions:
            return f"No Lambda functions in {where}."
        if self._checked is None:
            return ("Looking at what's running…" if self._watching
                    else "Live is off: click Live to see what's running now.")
        return (f"Nothing ran in {where} in the last {LIVE_WINDOW_MINUTES} minutes. CloudWatch's numbers come a "
                "minute or two late, so a function that just started shows here in a moment.")

    def _followed_empty_text(self, item: LiveFunction) -> str:
        name = item.function.name
        if item.error == "ResourceNotFoundException":
            return f"{name} has no log group yet: it hasn't logged anything."
        if item.error:
            return f"Couldn't read {name}'s logs ({_why(item.error, 'logs:FilterLogEvents')})."
        if not item.read:
            return f"Reading what {name} logged in the last {LIVE_WINDOW_MINUTES} minutes…"
        return (f"{name} logged nothing in the last {LIVE_WINDOW_MINUTES} minutes. Its runs show here as they start"
                + ("." if self._watching else ": click Live to watch for them."))

    def _live_note_html(self, followed: LiveFunction | None) -> str:
        """
        Under the list: the functions that didn't run (named, a few), and the regions CloudWatch couldn't be read in.
        """
        notes: list[tuple[str, str]] = []
        for region, code in sorted(self._activity_errors.items()):
            notes.append(("warn", f"Couldn't read CloudWatch's numbers in {region or 'this region'} ("
                                  f"{_why(code, 'cloudwatch:GetMetricData')}): only the functions with runs in their "
                                  "logs show."))
        if followed is None and self._checked is not None:
            quiet = sorted(item.function.name for item in self._board_items() if not ran_lately(item))
            if quiet:
                named = ", ".join(quiet[:_QUIET_NAMED])
                more = f" and {len(quiet) - _QUIET_NAMED:,} more" if len(quiet) > _QUIET_NAMED else ""
                notes.append(("", f"Not called in the last {LIVE_WINDOW_MINUTES} minutes: {named}{more}."))
        return "".join(f'<div class="lvnote {level}">{_prose(text)}</div>' for level, text in notes)

    def _watch_line(self) -> str:
        """
        The status line while watching: when it last looked, how often it looks, and what CloudWatch's numbers cost.
        """
        checked = "looking…"
        if self._checked is not None:
            clock = _in_zone(self._checked)
            checked = f"checked {clock:%H:%M:%S} {_zone_name(clock)}"
        followed = self._followed()
        if followed is not None:
            return (f"Live · following {followed.function.name} · {checked} · its lines every {_WATCH_SECONDS} s · "
                    "‹ All functions goes back to the list")
        cost = self._metrics_per_read * _WATCH_MINUTES / 1000 * self.core.prices["metric_request"]
        numbers = (f" ({self._metrics_per_read:,} metrics a read, about {human_money(cost)} for {_WATCH_MINUTES} "
                   "minutes)" if self._metrics_per_read else "")
        return (f"Live · {checked} · new lines every {_WATCH_SECONDS} s, CloudWatch's numbers every minute{numbers} · "
                "click a function to follow its runs")

    def _live_line(self) -> str:
        """
        The status line for the Live tab.
        """
        if self._watching:
            return self._watch_line()
        if self.overview is None:
            return "Live starts once the functions are listed."
        followed = self._followed()
        if followed is not None:
            return f"Following {followed.function.name} · click Live to add new lines as they come"
        return "Live is off: click Live to watch what's running now."
