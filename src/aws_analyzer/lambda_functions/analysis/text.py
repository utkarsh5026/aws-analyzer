"""
How findings say things: a time window in words, and the AWS CLI commands they show.
"""

from __future__ import annotations

from datetime import datetime

from ..._kit.fmt import _fmt_dt, _plural, _utcnow

from ..models import Function

_ENDS_NOW_SECONDS = 120


def _window(seconds: float) -> str:
    """
    How long a window is, for 'in the last ...': 900 -> '15 minutes', 3600 -> 'hour', 86400 -> '24 hours',
    2592000 -> '30 days'.
    """
    minutes = round(seconds / 60)
    if minutes < 60:
        return _plural(max(minutes, 1), "minute")
    hours = round(seconds / 3600)
    if hours == 1:
        return "hour"
    if hours <= 48:
        return _plural(hours, "hour")
    return _plural(round(seconds / 86400), "day")


def _span_said(start: datetime, end: datetime, now: datetime | None = None) -> str:
    """
    A window in words: 'the last 24 hours' when it runs up to now, else 'the range 2026-10-01 00:00 – 2026-10-15
    00:00 UTC'.
    """
    ends_now = abs(((now or _utcnow()) - end).total_seconds()) <= _ENDS_NOW_SECONDS
    if ends_now:
        return f"the last {_window((end - start).total_seconds())}"
    return f"the range {_fmt_dt(start)} – {_fmt_dt(end, zone=True)}"


def _cli(fn: Function) -> str:
    """
    The options that name a function in an AWS CLI command, region included so it works from any terminal.
    """
    return f"--function-name {fn.name}" + (f" --region {fn.region}" if fn.region else "")


def _region_flag(fn: Function) -> str:
    return f" --region {fn.region}" if fn.region else ""


def _reason(text: str | None) -> str:
    return f" ({text.rstrip('.')})" if text else ""
