"""
How findings say things: a time window in words, and the AWS CLI commands they show.
"""

from __future__ import annotations

from ..._kit.fmt import _plural

from ..models import Function


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


def _cli(fn: Function) -> str:
    """
    The options that name a function in an AWS CLI command, region included so it works from any terminal.
    """
    return f"--function-name {fn.name}" + (f" --region {fn.region}" if fn.region else "")


def _region_flag(fn: Function) -> str:
    return f" --region {fn.region}" if fn.region else ""


def _reason(text: str | None) -> str:
    return f" ({text.rstrip('.')})" if text else ""
