"""
A function's log lines as runs: its REPORT lines, START to REPORT for each call, and each line's
level.
"""

from __future__ import annotations

import functools
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from urllib.parse import quote

from ..._kit.deps import _require

from ..helpers import _lambda_time
from ..models import Function, Invocation, LogEvent
from .errors import _REPORT_FIELD_RE, _REPORT_RE, _TIMESTAMP_RE, classify_error
from .performance import percentile


@dataclass
class LogRun:
    """
    One run (a call) as its log stream recorded it: its lines from START to REPORT, oldest first, with the start-up
    lines just before START on a cold start. request_id is None for lines that belong to no run in the window.
    """

    request_id: str | None
    stream: str = ""
    events: list[LogEvent] = field(default_factory=list)
    report: Invocation | None = None  # its REPORT line: run time, memory used, cold start; None until it ends

    @property
    def start(self) -> datetime:
        return self.events[0].time

    @property
    def end(self) -> datetime:
        return self.events[-1].time

    @property
    def key(self) -> str:
        """
        What tells it apart from the window's other runs, for the window to remember which ones are open.
        """
        return f"{self.stream}|{self.request_id or self.start.isoformat()}"

    @functools.cached_property
    def failures(self) -> list[tuple[LogEvent, str, str]]:
        """
        (line, kind, summary) for each line classify_error() says reports an error.
        """
        found = []
        for event in self.events:
            kind = classify_error(event.message)
            if kind:
                found.append((event, *kind))
        return found

    @property
    def status(self) -> str:
        """
        'timeout'; 'failed' (an error it didn't handle, running out of memory, the runtime exiting); 'logged' (it
        logged an error, which it may have handled); 'running' (no REPORT line yet: still running, or it ends after
        the window); 'ok'; or 'outside' (lines that belong to no run).
        """
        if self.request_id is None:
            return "outside"
        report = self.report
        kinds = {kind for _, kind, _ in self.failures}
        if (report is not None and report.status == "timeout") or "Timeout" in kinds:
            return "timeout"
        if (report is not None and (report.status in ("error", "failure") or report.error_type)) or (
                kinds - {"Logged error"}):
            return "failed"
        if kinds:
            return "logged"
        return "running" if report is None else "ok"

    @property
    def error(self) -> tuple[str, str] | None:
        """
        (kind, summary) of what made it fail, else of the first error it logged; None when it logged none.
        """
        if not self.failures and self.report is not None and self.report.error_type:
            return self.report.error_type, f"{self.report.error_type} ({self.report.status or 'error'})"
        serious = [(kind, summary) for _, kind, summary in self.failures if kind != "Logged error"]
        return (serious or [(kind, summary) for _, kind, summary in self.failures] or [None])[0]

    @property
    def cold(self) -> bool:
        """
        Whether it was a cold start: its REPORT line has a start-up (or SnapStart restore) time, or its stream logged
        the runtime starting just before it.
        """
        if self.report is not None and (self.report.init is not None or self.report.restore is not None):
            return True
        return any((_marker(e.message) or ("",))[0] == "init" for e in self.events)

    @property
    def duration(self) -> float | None:
        """
        How long it ran, in ms, from its REPORT line.
        """
        return self.report.duration if self.report is not None else None

    @property
    def matched(self) -> bool:
        return any(e.matched for e in self.events)


TIMELINE_BUCKETS = 30

SLOW_SHARE = 0.10

_SLOW_MIN_RUNS = 10


@dataclass
class TimeBucket:
    """
    A slice of a time range: how many runs started in it, and how many of them failed or timed out.
    """

    start: datetime
    end: datetime
    runs: int = 0
    failed: int = 0


def run_timeline(runs: Iterable[LogRun], start: datetime, end: datetime, buckets: int = TIMELINE_BUCKETS
                 ) -> list[TimeBucket]:
    """
    The runs that started from start to end, counted in `buckets` equal slices, oldest first: when they ran, and
    when they failed, at a glance. Runs outside the range are left out.
    """
    if end <= start or buckets < 1:
        return []
    step = (end - start) / buckets
    slices = [TimeBucket(start + step * i, start + step * (i + 1)) for i in range(buckets)]
    for run in runs:
        if not start <= run.start < end:
            continue
        bucket = slices[min(buckets - 1, int((run.start - start) / step))]
        bucket.runs += 1
        bucket.failed += run.status in ("failed", "timeout")
    return slices


def slow_cutoff(runs: Iterable[LogRun]) -> float | None:
    """
    The run time (ms) from which a run is among the slowest SLOW_SHARE of the runs; None with fewer than
    _SLOW_MIN_RUNS runs that have a run time, too few to call any of them slow.
    """
    durations = [run.duration for run in runs if run.duration is not None]
    if len(durations) < _SLOW_MIN_RUNS:
        return None
    return percentile(durations, 100 * (1 - SLOW_SHARE))


_CONSOLE_HOSTS = (("cn-", "console.amazonaws.cn"), ("us-gov-", "console.amazonaws-us-gov.com"))


def _console_part(text: str) -> str:
    """
    A name or value in a CloudWatch console address, the way the console writes them after its #: escaped as a URL
    twice, then each % written as $ ('/aws/lambda/x' -> '$252Faws$252Flambda$252Fx').
    """
    return quote(quote(text, safe=""), safe="").replace("%", "$")


def run_console_url(region: str, log_group: str, stream: str, request_id: str) -> str:
    """
    The AWS console's page of one run: its log stream in CloudWatch, filtered to the lines that name its request ID.
    """
    host = next((host for prefix, host in _CONSOLE_HOSTS if region.startswith(prefix)), "console.aws.amazon.com")
    return (f"https://{region}.{host}/cloudwatch/home?region={region}#logsV2:log-groups/log-group/"
            f"{_console_part(log_group)}/log-events/{_console_part(stream)}"
            f"$3FfilterPattern$3D{_console_part(chr(34) + request_id + chr(34))}")


def run_insights_query(request_id: str) -> str:
    """
    A CloudWatch Logs Insights query for every line of one run, oldest first: run it on the function's log group.
    """
    return (f'fields @timestamp, @message\n| filter @requestId = "{request_id}" or @message like "{request_id}"\n'
            "| sort @timestamp asc")


@dataclass
class LogPage:
    """
    Log events read from a function's log group, oldest first.
    """

    function: Function
    log_group: str
    since: datetime
    until: datetime
    events: list[LogEvent] = field(default_factory=list)
    pattern: str | None = None  # the CloudWatch Logs filter pattern used
    truncated: bool = False  # stopped at the limit before reaching `since`
    covered_from: datetime | None = None  # how far back the events read reach (when truncated)
    latest: datetime | None = None  # the newest event in the log group, when the window had none
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def runs(self) -> list[LogRun]:
        """
        The events grouped into the runs they belong to, newest first (split_runs). After a search
        (log_runs(search=...)), only the runs with a line that matched it.
        """
        runs = split_runs(self.events)
        return [run for run in runs if run.matched] if any(e.matched for e in self.events) else runs

    def to_df(self):
        pd = _require("pandas", "LogPage.to_df()")
        return pd.DataFrame(
            [{"time": e.time, "request_id": e.request_id, "message": e.message, "stream": e.stream}
             for e in self.events]
        )


def parse_report(message: str, time: datetime | None = None) -> Invocation | None:
    """
    A REPORT line (or a JSON platform.report record), which Lambda logs at the end of every run, -> Invocation:
    run time, billed time, memory used, and the start-up time of a cold start. None for any other line.
    """
    text = message.strip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        if not isinstance(doc, dict) or doc.get("type") != "platform.report":
            return None
        record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
        numbers = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
        status = record.get("status")
        return Invocation(
            str(record.get("requestId") or ""),
            _lambda_time(doc.get("time")) or time,
            float(numbers.get("durationMs") or 0),
            float(numbers.get("billedDurationMs") or 0),
            int(numbers.get("memorySizeMB") or 0),
            int(numbers.get("maxMemoryUsedMB") or 0),
            numbers.get("initDurationMs"),
            numbers.get("restoreDurationMs"),
            None if status in (None, "success") else str(status),
            record.get("errorType"),
        )
    match = _REPORT_RE.match(text)
    if not match:
        return None
    values = {key: float(value) for key, value in _REPORT_FIELD_RE.findall(text)}
    status = re.search(r"Status: (\w+)", text)
    error_type = re.search(r"Error Type: ([\w.]+)", text)
    return Invocation(
        match.group(1),
        time,
        values.get("Duration", 0.0),
        values.get("Billed Duration", 0.0),
        int(values.get("Memory Size", 0)),
        int(values.get("Max Memory Used", 0)),
        values.get("Init Duration"),
        values.get("Restore Duration"),
        status.group(1) if status and status.group(1) != "success" else None,
        error_type.group(1) if error_type else None,
    )


_PLATFORM_KINDS = {"platform.start": "start", "platform.runtimeDone": "end", "platform.report": "report",
                   "platform.initStart": "init", "platform.initRuntimeDone": "init", "platform.initReport": "init",
                   "platform.restoreStart": "init", "platform.restoreRuntimeDone": "init",
                   "platform.restoreReport": "init", "platform.extension": "init", "platform.telemetrySubscription":
                   "init"}  # Lambda's own JSON records -> where they sit in a run

_STARTING = ("INIT_START", "INIT_REPORT", "INIT_RUNTIME_DONE", "RESTORE_START", "RESTORE_REPORT", "EXTENSION",
             "TELEMETRY")  # the text lines Lambda writes while an execution environment starts


def _marker(message: str) -> tuple[str, str | None] | None:
    """
    ('start' | 'end' | 'report' | 'init', request ID) for the lines Lambda itself writes around a run (START, END,
    REPORT and the start-up lines), in text or JSON format; None for any other line.
    """
    text = message.lstrip()
    for kind, prefix in (("start", "START RequestId:"), ("end", "END RequestId:"), ("report", "REPORT RequestId:")):
        if text.startswith(prefix):
            words = text[len(prefix):].split()
            return kind, words[0] if words else None
    if text.startswith(_STARTING):
        return "init", None
    if text.startswith("{") and '"platform.' in text:
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        kind = _PLATFORM_KINDS.get(str(doc.get("type"))) if isinstance(doc, dict) else None
        if kind:
            record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
            return kind, str(record["requestId"]) if record.get("requestId") else None
    return None


def split_runs(events: Iterable[LogEvent]) -> list[LogRun]:
    """
    Log lines grouped into the runs they belong to, newest run first. An execution environment (a log stream) runs
    one call at a time, so a run is what its stream recorded from its START line to its REPORT line, with the start-up
    lines just before START on a cold start; a line that names an open run's request ID goes to that run. A run the
    window cuts off keeps the lines the window has (no START, or no REPORT yet), and lines that belong to no run come
    back as a LogRun with request_id None, one per stream.
    """
    streams: dict[str, list[LogEvent]] = defaultdict(list)
    for event in events:
        streams[event.stream].append(event)
    runs: list[LogRun] = []
    for stream, items in streams.items():
        items.sort(key=lambda e: e.time)
        running: dict[str, LogRun] = {}  # request ID -> its run, in the order they started
        ended: dict[str, LogRun] = {}  # request ID -> its run, once its REPORT line came
        pending: list[LogEvent] = []  # lines no run has claimed yet: a cold start's start-up, or a run's cut-off start

        def begin(request_id: str) -> LogRun:
            nonlocal pending
            run = LogRun(request_id, stream, [] if running else pending)
            if not running:
                pending = []
            running[request_id] = run
            return run

        for event in items:
            kind, request_id = _marker(event.message) or ("", None)
            if kind == "start" and request_id:
                if request_id in running:  # a second START for the same ID: the first never reported
                    runs.append(running.pop(request_id))
                begin(request_id).events.append(event)
            elif kind == "end" and request_id in ended and request_id not in running:
                ended[request_id].events.append(event)  # its REPORT line came first (the same millisecond)
            elif kind in ("end", "report") and request_id:
                run = running.get(request_id) or begin(request_id)  # started before the window
                run.events.append(event)
                if kind == "report":
                    run.report = parse_report(event.message, event.time)
                    if run.report is not None:
                        run.report.stream = stream
                    runs.append(running.pop(request_id))
                    ended[request_id] = run
            else:
                run = running.get(event.request_id or "")
                if run is None and running:
                    run = next(reversed(running.values()))  # the run that started last (one at a time, usually)
                if run is not None:
                    run.events.append(event)
                else:
                    pending.append(event)
        runs.extend(running.values())  # no REPORT yet: still running, or it ended after the window
        if pending:
            runs.append(LogRun(None, stream, pending))
    return sorted(runs, key=lambda r: (r.start, r.end), reverse=True)


_LEVEL_TAG_RE = re.compile(r"^\[(ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\]")

_LEVELS = {"ERROR": "error", "CRITICAL": "error", "FATAL": "error", "WARNING": "warn", "WARN": "warn",
           "INFO": "info", "DEBUG": "debug", "TRACE": "debug"}


def line_level(message: str) -> str:
    """
    What kind of line a log line is: 'report' (the REPORT line that ends a run), 'platform' (Lambda's START, END and
    start-up lines), 'error', 'warn', 'info', 'debug', or '' when it doesn't say. Reads Lambda's text formats (Python's
    '[ERROR]', Node's '<time>\\t<request>\\tWARN\\t...') and JSON logs (their "level").
    """
    text = message.lstrip()
    mark = _marker(text)
    if mark is not None:
        return "report" if mark[0] == "report" else "platform"
    if classify_error(text):
        return "error"
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            return _LEVELS.get(str(doc.get("level") or doc.get("levelname") or doc.get("severity") or "").upper(), "")
    tag = _LEVEL_TAG_RE.match(text)
    if tag:
        return _LEVELS[tag.group(1)]
    parts = text.split("\t", 3)
    if len(parts) >= 3 and _TIMESTAMP_RE.match(parts[0].strip()) and parts[2].strip() in _LEVELS:
        return _LEVELS[parts[2].strip()]
    word = re.match(r"^(WARNING|WARN|INFO|DEBUG)\b", text)
    return _LEVELS[word.group(1)] if word else ""
