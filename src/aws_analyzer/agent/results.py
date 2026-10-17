"""
What a tool hands the model: JSON-ready values, time windows in UTC, text with anything that looks like a secret
hidden, and the parts it couldn't read with the permission each one needs. Every toolkit builds its results with
these, so the model reads the same shapes whichever service a tool is about.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .._kit.errors import _why

_SECRETS = (  # (pattern, replacement): what hide_secrets() takes out of a log line before the model sees it
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[access key hidden]"),
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "[token hidden]"),  # a JWT
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{12,}"), r"\1 [hidden]"),
    (re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     r"\1 [hidden]"),
    (re.compile(r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
                r"credential)[\w-]*)(\"?\s*[:=]\s*\"?)([^\s\"',;&}]+)"), r"\1\2[hidden]"),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)"), r"\1[hidden]\3"),  # user:password@host
)


def hide_secrets(text: Any) -> str:
    """
    The text with anything that looks like a secret replaced by [hidden]: AWS access key IDs, tokens (JWTs,
    Bearer ...), private keys, the password in a URL, and the value after names like password=, api_key: or
    "token": ... What the agent reads from logs and code goes through this before the model sees it.
    """
    out = "" if text is None else str(text)
    for pattern, replacement in _SECRETS:
        out = pattern.sub(replacement, out)
    return out


def _iso(moment: datetime | None) -> str | None:
    """
    A time as the tools hand it to the model: '2026-10-15T18:00Z', always UTC.
    """
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _plain(value: Any, depth: int = 0) -> Any:
    """
    A JSON-ready copy of a tool's result: times as ISO UTC text, floats rounded, tuples and sets as lists.
    """
    if depth > 12:
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 3) if abs(value) < 1e6 else round(value)
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v, depth + 1) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _window(start: datetime, end: datetime) -> dict[str, Any]:
    """
    The window a result covers, as the model reads it: {'from': ..., 'to': ..., 'hours': ...}, in UTC.
    """
    return {"from": _iso(start), "to": _iso(end), "hours": round((end - start).total_seconds() / 3600, 1)}


def _with_permissions(errors: dict[str, str], permissions: dict[str, str]) -> dict[str, str]:
    """
    {'us-east-1:list': 'AccessDeniedException'} -> {'us-east-1:list': 'AccessDeniedException; needs
    lambda:ListFunctions'}: what couldn't be read, and the permission that would read it. `permissions` maps the
    part after the last ':' of a section to its permission.
    """
    out = {}
    for section, code in errors.items():
        permission = permissions.get(section.rsplit(":", 1)[-1], "")
        out[section] = _why(code, permission) if permission else code
    return out
