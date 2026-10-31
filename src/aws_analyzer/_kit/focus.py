"""
What an explorer window is showing, as data: Focus, which each window's `focus` property gives.

A window knows what the user is looking at (the function and tab, the run opened, the folder, the file and its
state); Focus writes it down so something else can start from it without the user typing it again. It's pure (no
AWS, no widgets), so a window builds one from its own state and anything can read it: x.focus.chips() is what a
row of chips would show, x.focus.value('request_id') one part's value, and x.focus.without('range') the same focus
without one part, as removing a chip would leave it. QueryDraft is a search a window asked an assistant to write
from plain English (the Lambda explorer's ✦ in its Logs search box).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime

_CLEARED_FIELDS = {"range": {"since": None, "until": None}, "region": {"region": ""}}


@dataclass(frozen=True)
class FocusPart:
    """
    One thing in a window's focus, as a chip shows it. key says what it is ('function', 'request_id', 'range'),
    value is the thing itself (an ARN, a request ID, an s3:// path), and text is how a person reads it
    ('orders-etl', 'run c0ffee00', 'the last 24 hours').
    """

    key: str
    value: str
    text: str


@dataclass(frozen=True)
class Focus:
    """
    What an explorer window is showing. service is the window's service ('lambda', 's3', 'bedrock_kb'), kind what it
    is on (each window's `focus` property lists its kinds: 'errors', 'run', 'folder', 'file', ...), region and the
    time range (since, until) when it shows one, and parts the rest, in the order a person would say them: the
    function or folder first, then the tab, the range and what's picked or searched for.
    """

    service: str
    kind: str
    region: str = ""
    since: datetime | None = None
    until: datetime | None = None
    parts: tuple[FocusPart, ...] = ()

    def value(self, key: str) -> str:
        """
        The value of the part named key ('request_id' -> 'c0ffee00-1d2e-...'), or '' when the focus has none.
        """
        for part in self.parts:
            if part.key == key:
                return part.value
        return ""

    def chips(self) -> list[str]:
        """
        The parts as a person reads them, in order: ['orders-etl', 'us-east-1', 'Errors tab', 'the last 24 hours'].
        """
        return [part.text for part in self.parts]

    def without(self, key: str) -> Focus:
        """
        The same focus without the part named key, as removing its chip would leave it. Removing 'range' also drops
        since and until, and removing 'region' drops region.
        """
        kept = tuple(part for part in self.parts if part.key != key)
        return dataclasses.replace(self, parts=kept, **_CLEARED_FIELDS.get(key, {}))

    def __str__(self) -> str:
        return " · ".join(self.chips())


@dataclass(frozen=True)
class QueryDraft:
    """
    A search a window asked an assistant to write from plain English: the query in the window's own language
    ('duration > 5s and cold'), the time range it names, as the window reads one (since and until: '24h',
    '2026-10-30T12:00Z'; '' when the words name none), and a note on how it read them ('slow' as over 5 s).
    """

    query: str
    since: str = ""
    until: str = ""
    note: str = ""
