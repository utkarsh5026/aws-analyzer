"""
What a tool hands the model: JSON-ready values, time windows in UTC, text with anything that looks like a secret
hidden, and the parts it couldn't read with the permission each one needs. Every toolkit builds its results with
these, so the model reads the same shapes whichever service a tool is about.

_SECRETS is what hide_secrets() takes out of a log line before the model sees it, as (pattern, replacement) pairs:
AWS access key IDs, JWTs, Bearer and Basic credentials, private keys, values after secret-sounding names, and the
password in a user:password@host URL.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .._kit.errors import _why
from .._kit.text import _clip

_SECRETS = (
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[access key hidden]"),
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "[token hidden]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{12,}"), r"\1 [hidden]"),
    (re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     r"\1 [hidden]"),
    (re.compile(r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
                r"credential)[\w-]*)([\"']?\s*[:=]\s*[\"']?)([^\s\"',;&}]+)"), r"\1\2[hidden]"),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)"), r"\1[hidden]\3"),
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


_LINES_AROUND = 12

_MOST_LINES = 300

_LINE_CHARACTERS = 300

_LINES_RE = re.compile(r"^\s*(\d+)\s*(?:[-,:]\s*(\d*)\s*)?$")


def _numbered(lines: list[str], numbers: list[int], marked: set[int]) -> list[str]:
    """
    Lines by number (1-based) as '42: text', secrets hidden, '>> ' before the marked ones and '...' where numbers
    skip.
    """
    numbered = []
    previous = None
    for number in numbers:
        if previous is not None and number > previous + 1:
            numbered.append("...")
        text = hide_secrets(_clip(lines[number - 1].rstrip(), _LINE_CHARACTERS))
        numbered.append(f"{'>> ' if number in marked else ''}{number}: {text}")
        previous = number
    return numbered


def _asked_lines(spec: str, total: int) -> tuple[int, int, set[int]]:
    """
    The lines a read call (function_source, read_notebook_file) asks for, as (first, last, marked): '' the file's
    start, '42' the lines around line 42 (marked), '150-300' (or sed's '150,300') that range and '150-' from line 150
    on, each at most _MOST_LINES long. A ValueError says what it takes.
    """
    if not spec.strip():
        return 1, min(total, _MOST_LINES), set()
    match = _LINES_RE.match(spec)
    if not match:
        raise ValueError(f"lines={spec!r} isn't a line or a range: give one line ('42') or a range ('150-300')")
    first = int(match[1])
    if match[2] is None:
        return max(1, first - _LINES_AROUND), min(total, first + _LINES_AROUND), {first}
    last = int(match[2]) if match[2] else first + _MOST_LINES - 1
    if last < first:
        raise ValueError(f"lines={spec!r} ends before it starts: give a range like '150-300'")
    return first, min(total, last, first + _MOST_LINES - 1), set()


_SHARE_DIGITS = 1


def share_pct(part: float, whole: float) -> float | None:
    """
    `part` as a share of `whole`, in % to one decimal, so the model copies it rather than works it out: 35 of 1,200
    -> 2.9. None when there's no whole.
    """
    if not whole:
        return None
    return round(100 * part / whole, _SHARE_DIGITS)


def shares_pct(counts: dict[str, float]) -> dict[str, float | None]:
    """
    Each count as a share of their total, in %: {'failed': 3, 'indexed': 97} -> {'failed': 3.0, 'indexed': 97.0}.
    """
    total = sum(counts.values())
    return {key: share_pct(value, total) for key, value in counts.items()}


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


def model_findings(found: list[tuple[str, str]]) -> list[dict[str, str]]:
    """
    A report's findings as the model reads them: [{'level': 'warn', 'message': ...}], warnings first, each with
    anything that looks like a secret hidden (findings quote error messages, paths and commands).
    """
    ordered = sorted(found, key=lambda pair: pair[0] != "warn")
    return [{"level": level, "message": hide_secrets(text)} for level, text in ordered]


def clamp_count(value: Any, default: int, most: int) -> int:
    """
    A count the model asked for, between 1 and `most` (`default` when it gave none or something that isn't one).
    """
    try:
        wanted = int(value or default)
    except (TypeError, ValueError):
        wanted = default
    return max(1, min(wanted, most))
