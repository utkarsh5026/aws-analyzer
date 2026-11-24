"""
The test window's Live logs: the function's log group watched while you test it, every few seconds, its runs drawn
newest first as they come, yours marked and opened. It shows what the 4 KB returned with an answer can't: a long
run's every line once CloudWatch has it, and what happens after (a retry, the function called again by what it
wrote to a queue, another caller's runs). ▶ Run starts it; Watch logs turns it on or off; it stops by itself after
_WATCH_MINUTES.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Note
from ..._kit.errors import _error_code
from ..._kit.fmt import _in_zone, _plural, _utcnow, _zone_name
from ..._kit.window import _class_if, _running_loop

from ..models import LogEvent
from ..analysis.invoke import Invoked
from ..analysis.logs import LogRun, split_runs
from ..blocks.logs import _LogRuns

_WATCH_SECONDS = 5

_WATCH_MINUTES = 15

_LOOK_BACK = timedelta(minutes=10)

_OVERLAP = timedelta(seconds=2)

_READ_LINES = 2_000

_KEPT_LINES = 20_000

_SHOWN_RUNS = 30


def merge_lines(kept: list[LogEvent], read: list[LogEvent]) -> tuple[list[LogEvent], int]:
    """
    The lines read added to the ones kept, oldest first, each once (reads overlap by a couple of seconds, since lines
    can share a millisecond), at most _KEPT_LINES of the newest; and how many were new.
    """
    known = {(event.stream, event.time, event.message) for event in kept}
    new = [event for event in read if (event.stream, event.time, event.message) not in known]
    merged = sorted(kept + new, key=lambda event: event.time)[-_KEPT_LINES:]
    return merged, len(new)


def _opened_keys(runs: list[LogRun], mine: set[str]) -> set[str]:
    """
    The runs drawn open: the ones you ran here, else the newest one.
    """
    yours = {run.key for run in runs if run.request_id in mine}
    if yours:
        return yours
    return {runs[0].key} if runs else set()


class _TesterLogs:
    """
    LambdaTester's Live logs (a mixin). `self.watched` holds the lines read, oldest first; `self._watching` whether
    it's on, `self._watch_token` bumped to stop a loop, `self._watch_started` when it was turned on and
    `self._watch_new` the runs that came since.
    """

    def _init_watch(self) -> None:
        self.watched: list[LogEvent] = []
        self._watching = False
        self._watch_token = 0
        self._watch_started = 0.0
        self._watch_new = 0
        self._watch_checked: datetime | None = None
        self._view = "result"

    def _view_row(self) -> Any:
        """
        The Result / Live logs switch, and Watch logs with what it last saw.
        """
        w, layout = self._w, self._w.Layout
        self.result_chip = w.Button(description="Result", tooltip="What the run you picked returned and logged",
                                    layout=layout(width="auto"))
        self.logs_chip = w.Button(description="Live logs", layout=layout(width="auto"),
                                  tooltip="The function's log group, watched while you test it: every run, newest first")
        for chip, view in ((self.result_chip, "result"), (self.logs_chip, "logs")):
            for name in ("lmx-chip", "lmx-t-all"):
                chip.add_class(name)
            chip.on_click(self._safely(lambda _button, view=view: self._show_view(view)))
        self.watch_button = w.Button(description="Watch logs", layout=layout(width="auto"),
                                     tooltip=f"Look for new log lines every {_WATCH_SECONDS} seconds (stops after "
                                             f"{_WATCH_MINUTES} minutes)")
        self.watch_button.add_class("lmx-live")
        self.watch_button.on_click(self._safely(lambda _button: self._toggle_watch()))
        self.watch_said = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        row = w.HBox([self.result_chip, self.logs_chip, self.watch_said, self.watch_button],
                     layout=layout(width="100%"))
        row.add_class("lmx-tst-views")
        self.logs_view = w.HTML(layout=layout(width="100%", display="none"))
        self._show_view("result")
        self._draw_watch_line()
        return row

    def _show_view(self, view: str) -> None:
        self._view = view
        _class_if(self.result_chip, "lmx-on", view == "result")
        _class_if(self.logs_chip, "lmx-on", view == "logs")
        self.test_result.layout.display = "" if view == "result" else "none"
        self.logs_view.layout.display = "" if view == "logs" else "none"
        if view == "logs":
            self._draw_watched()

    def _toggle_watch(self, on: bool | None = None) -> None:
        """
        Turns watching on (reading the last _LOOK_BACK first) or off.
        """
        on = not self._watching if on is None else on
        if not on:
            self._stop_watch("Stopped watching the logs.")
            return
        if self._watching or self.function is None:
            return
        self._watching, self._watch_new, self._watch_started = True, 0, time.monotonic()
        self._watch_token += 1
        _class_if(self.watch_button, "lmx-on", True)
        self.watch_button.description = "Stop watching"
        self._draw_watch_line()
        loop = _running_loop()
        if loop is None:
            self._look_now()
            return
        token = self._watch_token
        self._tasks["watch"] = loop.create_task(self._watch_loop(token))

    def _stop_watch(self, text: str = "", level: str = "") -> None:
        was = self._watching
        self._watching = False
        self._watch_token += 1
        _class_if(self.watch_button, "lmx-on", False)
        self.watch_button.description = "Watch logs"
        self._draw_watch_line()
        if was and text:
            self._status(text, level)

    def _read_since(self) -> datetime:
        newest = max((event.time for event in self.watched), default=None)
        return newest - _OVERLAP if newest is not None else _utcnow() - _LOOK_BACK

    def _read_new(self, since: datetime) -> list[LogEvent]:
        """
        The lines logged since `since`; none while its log group doesn't exist yet (a function that never logged).
        """
        fn = self.function
        try:
            return self.core._filter(fn.log_group, fn.region or None, since, _utcnow(), None, _READ_LINES)[0]
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return []
            raise

    def _look_now(self) -> None:
        """
        One look, here and now (a script or the tests, with no event loop to watch from).
        """
        try:
            events = self._read_new(self._read_since())
        except (ClientError, BotoCoreError) as exc:
            self._stop_watch(f"Stopped watching: {self._error_text(exc)}", "warn")
            return
        self._merge_watched(events)

    async def _watch_loop(self, token: int) -> None:
        loop = asyncio.get_running_loop()
        first = True
        try:
            while self._watching and self._watch_token == token:
                if not first:
                    await asyncio.sleep(_WATCH_SECONDS)
                first = False
                if not (self._watching and self._watch_token == token):
                    return
                if time.monotonic() - self._watch_started > _WATCH_MINUTES * 60:
                    self._stop_watch(f"Stopped watching after {_WATCH_MINUTES} minutes: Watch logs goes on.")
                    return
                since = self._read_since()
                try:
                    events = await loop.run_in_executor(self._workers(), self._read_new, since)
                except (ClientError, BotoCoreError) as exc:
                    if self._watch_token == token:
                        self._stop_watch(f"Stopped watching: {self._error_text(exc)}", "warn")
                    return
                if self._watching and self._watch_token == token:
                    self._merge_watched(events)
        finally:
            if self._tasks.get("watch") is asyncio.current_task():
                del self._tasks["watch"]

    def _merge_watched(self, events: list[LogEvent]) -> None:
        """
        New lines in: counted as new runs, and drawn when Live logs is shown.
        """
        before = {run.key for run in split_runs(self.watched)}
        self.watched, added = merge_lines(self.watched, events)
        self._watch_checked = _utcnow()
        if added:
            fresh = [run for run in split_runs(self.watched) if run.key not in before and run.request_id]
            self._watch_new += len(fresh) if before else 0
            if self._view == "logs":
                self._draw_watched()
        self._draw_watch_line()

    def _draw_watch_line(self) -> None:
        runs = [run for run in split_runs(self.watched) if run.request_id]
        count = f" · {len(runs):,}" if runs else ""
        self.logs_chip.description = f"Live logs{count}"
        if not self._watching:
            said = "not watching · ▶ Run or Watch logs starts it" if not self.watched else "not watching"
            self._set(self.watch_said, f'<span class="lmx-tst-ws">{said}</span>')
            return
        checked = ""
        if self._watch_checked is not None:
            clock = _in_zone(self._watch_checked)
            checked = f" · checked {clock:%H:%M:%S} {_zone_name(clock)}"
        new = f" · {_plural(self._watch_new, 'new run')}" if self._watch_new else ""
        self._set(self.watch_said, f'<span class="lmx-tst-ws on">● watching every {_WATCH_SECONDS} s{checked}{new}'
                                   "</span>")

    def _draw_watched(self) -> None:
        """
        The runs read, newest first, yours (and else the newest) open.
        """
        fn = self.function
        if fn is None:
            return
        runs = split_runs(self.watched)[:_SHOWN_RUNS]
        if not runs:
            text = ("Nothing logged in the last 10 minutes yet: new lines show here as they come."
                    if self._watching else "Watch logs to see the function's runs as they come.")
            self._draw("logs", self.logs_view, [_Note(text)])
            return
        mine = {invoked.request_id for invoked in self.runs if invoked.request_id}
        has_yours = any(run.request_id in mine for run in runs)
        title = f"{fn.log_group}, newest run first" + (" · your runs are open" if has_yours else "")
        block = _LogRuns(runs, timeout=fn.timeout, opened=_opened_keys(runs, mine), title=title)
        self._draw("logs", self.logs_view, [block])

    def _watch_after_run(self, invoked: Invoked) -> None:
        """
        After a run: watch the logs (if not already), so its every line and what follows show in Live logs.
        """
        if invoked.dry_run:
            return
        if self._watching:
            if self._view == "logs":
                self._draw_watched()
            return
        self._toggle_watch(True)
