"""
What each live stream reads (live.py runs them): a Feed names the service, how often it reads, the parameters it
takes, and makes a reader, a function that reads once and returns what's new as JSON, keeping what it needs between
reads (where the last log read ended, the queue's looks so far). Readers run on worker threads and call the
service's analyzer, the same reads the explorer windows' live tabs make:

    lambda_functions/activity  CloudWatch's last minutes for every function in the region (GetMetricData), the
                               ones that ran first, every ACTIVITY_SECONDS
    lambda_functions/lines     one function's new log lines (FilterLogEvents) from where the last read ended, and
                               the runs going now (a START line without a REPORT line), every LINES_SECONDS
    messaging/queue            one SQS queue's counts (GetQueueAttributes; never ReceiveMessage) and which way its
                               backlog is going, every QUEUE_SECONDS
    sagemaker_env/machine      this machine's load, memory, disks and GPUs (local files only), every MACHINE_SECONDS
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .._kit.datasets import json_value
from .._kit.fmt import _utcnow

ACTIVITY_SECONDS = 60.0
LINES_SECONDS = 5.0
QUEUE_SECONDS = 5.0
MACHINE_SECONDS = 5.0
FUNCTIONS_KEPT = 300.0
FIRST_LINES = 1_000
NEW_LINES = 2_000
LINES_OVERLAP = timedelta(seconds=2)
LINES_SENT = 200
QUEUE_LOOKS_KEPT = 120

Reader = Callable[[], dict[str, Any]]


class FeedProblem(LookupError):
    """
    A live stream that can't start: a parameter it needs is missing, or what it names isn't there. The message says
    which, in words for the page.
    """


@dataclass(frozen=True)
class Feed:
    """
    A live stream's kind: its service and name, how often it reads (seconds), the parameters it takes (the first ones
    up to `needed` must be given), what it shows, and make(core, params), which returns its reader.
    """

    service: str
    name: str
    every: float
    about: str
    make: Callable[[Any, dict[str, str]], Reader]
    params: tuple[str, ...] = ()
    needed: int = 0


def _stamp() -> str:
    return json_value(_utcnow())


def _lambda_activity(core: Any, params: dict[str, str]) -> Reader:
    """
    Every function's last LIVE_WINDOW_MINUTES minutes, a value a minute, the ones that ran first: the function list is
    read again every FUNCTIONS_KEPT seconds.
    """
    from ..lambda_functions.analysis.live import LIVE_WINDOW_MINUTES

    listed: dict[str, Any] = {"functions": [], "at": None}

    def functions() -> list[Any]:
        is_stale = listed["at"] is None or time.monotonic() - listed["at"] > FUNCTIONS_KEPT
        if is_stale:
            listed["functions"], listed["at"] = core.list_functions(core.region), time.monotonic()
        return listed["functions"]

    def read() -> dict[str, Any]:
        listing = functions()
        found, metrics_read, errors = core.activity(listing)
        rows = [_activity_row(fn, found.get(fn.arn)) for fn in listing]
        ran = sorted((row for row in rows if row["total_calls"]), key=lambda row: (-row["total_calls"], row["name"]))
        return {"read_at": _stamp(), "minutes": LIVE_WINDOW_MINUTES, "functions": ran,
                "quiet": len(rows) - len(ran), "metrics_read": metrics_read, "errors": errors}

    return read


def _activity_row(fn: Any, activity: Any) -> dict[str, Any]:
    if activity is None:
        return {"name": fn.name, "arn": fn.arn, "region": fn.region, "total_calls": 0}
    return {"name": fn.name, "arn": fn.arn, "region": fn.region, "runtime": fn.runtime, "timeout": fn.timeout,
            "calls": activity.calls, "errors": activity.errors, "concurrency": activity.concurrency,
            "total_calls": activity.total_calls, "total_errors": activity.total_errors,
            "minutes_since_called": activity.minutes_since_called}


def _lambda_lines(core: Any, params: dict[str, str]) -> Reader:
    """
    One function's new log lines since the last read (its last LIVE_WINDOW_MINUTES minutes the first time), at most
    LINES_SENT of them sent, and its runs going now.
    """
    from ..lambda_functions.analysis.live import LIVE_WINDOW_MINUTES, LiveFunction, running_for

    function = core.function(params["name"])
    followed = LiveFunction(function)
    read_until: dict[str, datetime] = {}

    def read() -> dict[str, Any]:
        end = _utcnow()
        until = read_until.get("end")
        start = end - timedelta(minutes=LIVE_WINDOW_MINUTES) if until is None else until - LINES_OVERLAP
        limit = FIRST_LINES if until is None else NEW_LINES
        events = core._filter(function.log_group, function.region, start, end, None, limit)[0]
        held = {(event.stream, event.time, event.message) for event in followed.events}
        new = [event for event in events if (event.stream, event.time, event.message) not in held]
        followed.add(events, now=end)
        read_until["end"] = end
        running = [{"request_id": run.request_id, "started": json_value(run.start),
                    "seconds": round(running_for(run, end), 1)} for run in followed.running(end)]
        lines = [json_value(asdict(event)) for event in new[-LINES_SENT:]]
        return {"read_at": _stamp(), "function": function.name, "timeout": function.timeout, "lines": lines,
                "cut": max(0, len(new) - LINES_SENT), "running": running}

    return read


def _queue(core: Any, params: dict[str, str]) -> Reader:
    """
    One queue's counts now and which way its backlog is going over the last QUEUE_LOOKS_KEPT looks.
    """
    from ..messaging.analysis.trend import backlog_trend
    from ..messaging.models import QueueSample

    named = params["name"]
    url = named if named.startswith("https://") else core.client.get_queue_url(QueueName=named)["QueueUrl"]
    looks: list[QueueSample] = []

    def read() -> dict[str, Any]:
        queue = core.read_queue(url)
        looks.append(QueueSample(_utcnow(), queue.visible, queue.in_flight, queue.delayed))
        del looks[:-QUEUE_LOOKS_KEPT]
        return {"read_at": _stamp(), "queue": queue.name, "visible": queue.visible, "in_flight": queue.in_flight,
                "delayed": queue.delayed, "trend": asdict(backlog_trend(looks)), "looks": len(looks)}

    return read


def _machine(core: Any, params: dict[str, str]) -> Reader:
    """
    This machine now, as machine() reads it: vCPUs and load, memory, disks, GPUs and the biggest processes.
    """

    def read() -> dict[str, Any]:
        return {"read_at": _stamp(), "machine": json_value(asdict(core.machine()))}

    return read


FEEDS = {
    (feed.service, feed.name): feed
    for feed in (
        Feed("lambda_functions", "activity", ACTIVITY_SECONDS, "every function's calls, errors and runs at once, a "
             "value a minute", _lambda_activity),
        Feed("lambda_functions", "lines", LINES_SECONDS, "one function's new log lines and the runs going now",
             _lambda_lines, ("name",), 1),
        Feed("messaging", "queue", QUEUE_SECONDS, "one SQS queue's messages and which way its backlog is going",
             _queue, ("name",), 1),
        Feed("sagemaker_env", "machine", MACHINE_SECONDS, "this machine's load, memory, disks and GPUs", _machine),
    )
}


def feed_params(feed: Feed, given: dict[str, str]) -> dict[str, str]:
    """
    The parameters a stream was asked for, checked against its feed's: FeedProblem names one it doesn't take or one it
    needs that's missing.
    """
    unknown = set(given) - set(feed.params)
    if unknown:
        raise FeedProblem(f"The {feed.name} stream takes no {', '.join(sorted(unknown))}.")
    missing = [name for name in feed.params[:feed.needed] if not given.get(name, "").strip()]
    if missing:
        raise FeedProblem(f"The {feed.name} stream needs {' and '.join(missing)}.")
    return {name: given[name].strip() for name in feed.params if given.get(name, "").strip()}
