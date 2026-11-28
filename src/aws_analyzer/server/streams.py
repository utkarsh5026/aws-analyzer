"""
Server-Sent Events, the way the page gets what the server pushes: a job's events (jobs.py) and the live streams
(live.py). Each message is `id:` (a job's event number, so an EventSource that reconnects asks for what it missed with
Last-Event-ID), `event:` (its kind) and `data:` (JSON); a comment line every HEARTBEAT_SECONDS keeps the notebook's
proxy and the browser from closing a quiet stream.

The notebook's proxy (jupyter-server-proxy) streams a response only when the request's Accept header is exactly
text/event-stream, which an EventSource sends by itself; anything else arrives in one piece when it ends. A stream
ends with a message the page reads as the end ('done'), never only a closed connection, which an EventSource would
open again. An EventSource can't send a header, so a stream takes the page's token as ?token= too.
"""

from __future__ import annotations

import json
from typing import Any

HEARTBEAT_SECONDS = 15.0
STREAM_TYPE = "text/event-stream"
STREAM_HEADERS = {"X-Accel-Buffering": "no", "Cache-Control": "no-store"}
HEARTBEAT = ": still here\n\n"


def sse_message(kind: str, data: dict[str, Any], number: int | None = None) -> str:
    """
    One message: its number (when it has one), its kind and its data as one line of JSON.
    """
    head = f"id: {number}\n" if number is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def last_seen(header: str | None, after: int | None) -> int:
    """
    The number of the last event the page has (Last-Event-ID when the browser reconnects, else ?after=), 0 for none.
    """
    for given in (header, after):
        if given is None:
            continue
        try:
            return max(0, int(given))
        except ValueError:
            continue
    return 0
