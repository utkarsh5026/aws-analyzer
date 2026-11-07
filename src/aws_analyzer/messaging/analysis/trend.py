"""
Which way a queue's backlog is going, from the looks Live takes every few seconds: growing, draining (and when it
would be empty), or steady. Pure: no AWS calls.
"""

from __future__ import annotations

from ..helpers import seconds_words
from ..models import QueueSample, Trend


STEADY_PER_MINUTE = 1.0

MINUTE = 60


def backlog_trend(samples: list[QueueSample]) -> Trend:
    """
    The change in a queue's visible messages a minute, from the first look to the last, and what it means: 'draining
    40 a minute, empty in about 3 minutes', 'growing 12 a minute', 'steady' or, after one look, 'one look so far'.
    """
    if len(samples) < 2:
        return Trend(None, None, "one look so far")
    first, last = samples[0], samples[-1]
    seconds = (last.at - first.at).total_seconds()
    if seconds <= 0:
        return Trend(None, None, "one look so far")
    per_minute = (last.visible - first.visible) * MINUTE / seconds
    if abs(per_minute) < STEADY_PER_MINUTE:
        return Trend(per_minute, None, "empty" if not last.visible else "steady")
    rate = f"{abs(per_minute):,.0f} a minute"
    if per_minute > 0:
        return Trend(per_minute, None, f"growing {rate}")
    empty_in = last.visible / -per_minute * MINUTE
    return Trend(per_minute, empty_in, f"draining {rate}, empty in about {seconds_words(empty_in)}")
