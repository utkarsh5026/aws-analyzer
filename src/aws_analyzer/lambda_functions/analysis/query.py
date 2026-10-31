"""
The small query language that filters a function's runs: `duration > 15s and cold`, `status in (failed, timeout)`,
`error ~ KeyError`, `event.Records.s3.object.size > 30MB`. A query is parsed once into a tree (parse_query) and
then asked of each run's values (RunQuery.matches); nothing in it is ever run as code.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Union

from ..._kit.fmt import _utcnow, parse_time


class QueryError(ValueError):
    """
    A query that can't be read: says where, and shows a query that works.
    """


QUERY_EXAMPLE = "duration > 15s and cold"

COMPARISONS = (">=", "<=", "!=", "!~", "==", "=", ">", "<", "~")

KEYWORDS = ("and", "or", "not", "in")

_TOKEN_RE = re.compile(r"""\s*(?:(?P<quoted>"[^"]*"|'[^']*')|(?P<op>>=|<=|!=|!~|==|=|>|<|~)|(?P<punct>[(),])|"""
                       r"""(?P<word>[^\s()<>=!~,"']+))""")

FIELD_KINDS = {
    "duration": "ms", "billed": "ms", "init": "ms",
    "memory": "mb", "memory_size": "mb",
    "memory_share": "pct", "timeout_share": "pct",
    "started": "time",
    "hour": "number", "lines": "number",
    "cold": "bool", "failed": "bool",
}

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|m|min|mins|h)?\s*$", re.IGNORECASE)

_MS_PER_UNIT = {"ms": 1, "s": 1000, "sec": 1000, "secs": 1000, "m": 60_000, "min": 60_000, "mins": 60_000,
                "h": 3_600_000}

_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(kb|k|mb|m|gb|g)?\s*$", re.IGNORECASE)

_MB_PER_UNIT = {"kb": 1 / 1024, "k": 1 / 1024, "mb": 1, "m": 1, "gb": 1024, "g": 1024}

_NUMBER_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")

_BYTES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|kib|mb|mib|gb|gib|tb|tib)\s*$", re.IGNORECASE)

_BYTES_PER_UNIT = {"b": 1, "kb": 1024, "kib": 1024, "mb": 1024 ** 2, "mib": 1024 ** 2, "gb": 1024 ** 3,
                   "gib": 1024 ** 3, "tb": 1024 ** 4, "tib": 1024 ** 4}

_CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

_TRUE_WORDS = {"true", "yes", "1", "on"}

_FALSE_WORDS = {"false", "no", "0", "off"}


@dataclass
class _Token:
    kind: str
    text: str
    at: int


@dataclass
class Compare:
    """
    `field op value`, or `field in (a, b)`: what one run's value must be.
    """

    field: str
    op: str
    values: list[str]


@dataclass
class Has:
    """
    A field on its own (`cold`): true when the run's value is.
    """

    field: str


@dataclass
class Not:
    """
    `not ...`: true when what follows isn't.
    """

    inner: Node


@dataclass
class AllOf:
    """
    `a and b`: true when every part is.
    """

    parts: list[Node]


@dataclass
class AnyOf:
    """
    `a or b`: true when a part is.
    """

    parts: list[Node]


Node = Union[Compare, Has, Not, AllOf, AnyOf]


def _lex(text: str) -> list[_Token]:
    """
    The query's words, operators, quoted values and brackets, in order.
    """
    found: list[_Token] = []
    at = 0
    while at < len(text):
        if text[at:].strip() == "":
            break
        match = _TOKEN_RE.match(text, at)
        if match is None or match.end() == at:
            raise QueryError(f"Can't read the query from {text[at:].strip()[:20]!r}; try {QUERY_EXAMPLE!r}")
        kind = match.lastgroup or "word"
        value, start = match.group(kind), match.start(kind)
        if kind == "quoted":
            value = value[1:-1]
        elif kind == "word" and value.lower() in KEYWORDS:
            kind, value = "keyword", value.lower()
        found.append(_Token(kind, value, start))
        at = match.end()
    return found


class _Parser:
    """
    Reads tokens into a tree: `or` binds loosest, then `and`, then `not`; brackets group.
    """

    def __init__(self, text: str):
        self.text = text
        self.tokens = _lex(text)
        self.at = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise QueryError(f"The query {self.text!r} stops too soon; try {QUERY_EXAMPLE!r}")
        self.at += 1
        return token

    def is_keyword(self, word: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "keyword" and token.text == word

    def query(self) -> Node:
        node = self.any_of()
        leftover = self.peek()
        if leftover is not None:
            raise QueryError(f"Didn't expect {leftover.text!r} in {self.text!r}: join conditions with 'and' or "
                             f"'or', as in {QUERY_EXAMPLE!r}")
        return node

    def any_of(self) -> Node:
        parts = [self.all_of()]
        while self.is_keyword("or"):
            self.take()
            parts.append(self.all_of())
        return parts[0] if len(parts) == 1 else AnyOf(parts)

    def all_of(self) -> Node:
        parts = [self.negated()]
        while self.is_keyword("and"):
            self.take()
            parts.append(self.negated())
        return parts[0] if len(parts) == 1 else AllOf(parts)

    def negated(self) -> Node:
        if self.is_keyword("not"):
            self.take()
            return Not(self.negated())
        return self.atom()

    def atom(self) -> Node:
        token = self.take()
        if token.kind == "punct" and token.text == "(":
            inner = self.any_of()
            self.expect(")")
            return inner
        if token.kind not in ("word", "quoted"):
            raise QueryError(f"Expected a field name where {token.text!r} is, in {self.text!r}; fields are things "
                             f"like duration, memory, status, cold or a field your function logs")
        return self.condition(token.text)

    def expect(self, punct: str) -> None:
        token = self.take()
        if token.kind != "punct" or token.text != punct:
            raise QueryError(f"Expected {punct!r} where {token.text!r} is, in {self.text!r}")

    def condition(self, name: str) -> Node:
        token = self.peek()
        if token is not None and token.kind == "op":
            self.take()
            return Compare(name, token.text, [self.value()])
        if self.is_keyword("in"):
            self.take()
            return Compare(name, "in", self.value_list())
        return Has(name)

    def value(self) -> str:
        token = self.take()
        if token.kind not in ("word", "quoted"):
            raise QueryError(f"Expected a value where {token.text!r} is, in {self.text!r}; values are like 15s, "
                             f"512MB, 80%, failed or \"some text\"")
        return token.text

    def value_list(self) -> list[str]:
        token = self.peek()
        if token is None or token.kind != "punct" or token.text != "(":
            return [self.value()]
        self.expect("(")
        values = [self.value()]
        while (token := self.peek()) is not None and token.kind == "punct" and token.text == ",":
            self.take()
            values.append(self.value())
        self.expect(")")
        return values


def _fields_of(node: Node) -> list[str]:
    if isinstance(node, (Compare, Has)):
        return [node.field]
    if isinstance(node, Not):
        return _fields_of(node.inner)
    return [name for part in node.parts for name in _fields_of(part)]


def _has_comparison(node: Node) -> bool:
    if isinstance(node, Compare):
        return True
    if isinstance(node, Has):
        return False
    if isinstance(node, Not):
        return _has_comparison(node.inner)
    return any(_has_comparison(part) for part in node.parts)


@dataclass
class RunQuery:
    """
    A parsed query: `.matches(values)` says whether one run's values ({field: value}) pass it.
    """

    text: str
    tree: Node
    now: datetime = field(default_factory=_utcnow)

    @property
    def fields(self) -> list[str]:
        """
        The fields it names, in order, once each.
        """
        return list(dict.fromkeys(_fields_of(self.tree)))

    @property
    def compares(self) -> bool:
        """
        Whether it asks about the runs' fields rather than being a word or two to search for: it compares a field
        with a value (`duration > 15s`), or names only yes-or-no fields (`cold or failed`).
        """
        if _has_comparison(self.tree):
            return True
        return all(FIELD_KINDS.get(name) == "bool" for name in self.fields)

    def matches(self, values: dict[str, Any]) -> bool:
        return _holds(self.tree, values, self.now)

    def renamed(self, names: dict[str, str]) -> RunQuery:
        """
        The same query with fields renamed ({'size': 'event.Records.s3.object.size'}).
        """
        renamed = RunQuery(self.text, _rename(self.tree, names), self.now)
        _check_values(renamed.tree, renamed.now)
        return renamed


def _rename(node: Node, names: dict[str, str]) -> Node:
    if isinstance(node, Compare):
        return Compare(names.get(node.field, node.field), node.op, node.values)
    if isinstance(node, Has):
        return Has(names.get(node.field, node.field))
    if isinstance(node, Not):
        return Not(_rename(node.inner, names))
    return type(node)([_rename(part, names) for part in node.parts])


def parse_query(text: str, now: datetime | None = None) -> RunQuery:
    """
    'duration > 15s and cold' -> RunQuery. Raises QueryError, which says what's wrong and shows a query that works.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise QueryError(f"The query is empty; try {QUERY_EXAMPLE!r}")
    query = RunQuery(cleaned, _Parser(cleaned).query(), now or _utcnow())
    _check_values(query.tree, query.now)
    return query


def _check_values(node: Node, now: datetime) -> None:
    """
    Reads every value the query compares with, so one its field can't hold is said now, not as runs are matched.
    """
    if isinstance(node, Compare) and node.op not in ("~", "!~"):
        for text in node.values:
            query_value(node.field, text, now)
    elif isinstance(node, Not):
        _check_values(node.inner, now)
    elif isinstance(node, (AllOf, AnyOf)):
        for part in node.parts:
            _check_values(part, now)


def looks_like_query(text: str) -> bool:
    """
    Whether text typed in a search box is a query (`duration > 15s`, `cold or failed`) rather than words to
    search the log lines for.
    """
    try:
        return parse_query(text).compares
    except QueryError:
        return False


def _holds(node: Node, values: dict[str, Any], now: datetime) -> bool:
    if isinstance(node, Compare):
        return _compare(node, values, now)
    if isinstance(node, Has):
        return _truthy(values.get(node.field))
    if isinstance(node, Not):
        return not _holds(node.inner, values, now)
    if isinstance(node, AllOf):
        return all(_holds(part, values, now) for part in node.parts)
    return any(_holds(part, values, now) for part in node.parts)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", *_FALSE_WORDS)
    return bool(value)


def query_value(name: str, text: str, now: datetime | None = None) -> Any:
    """
    A value typed in a query as the field `name` holds it: '15s' -> 15000 (ms) for duration, '1GB' -> 1024 (MB) for
    memory, '80%' -> 80 for memory_share, '14:00' -> today at 14:00 for started, '30MB' -> bytes and '42' -> 42 for a
    field the function logs. Raises QueryError when it can't be one.
    """
    kind = FIELD_KINDS.get(name, "")
    reader = _READERS.get(kind)
    if reader is None:
        return _loose_value(text)
    try:
        return reader(text, now or _utcnow())
    except (ValueError, KeyError):
        raise QueryError(f"{name} can't be {text!r}; {_EXAMPLES[kind]}") from None


def _as_ms(text: str, _now: datetime) -> float:
    match = _DURATION_RE.match(text)
    if match is None:
        raise ValueError(text)
    return float(match.group(1)) * _MS_PER_UNIT[(match.group(2) or "ms").lower()]


def _as_mb(text: str, _now: datetime) -> float:
    match = _MEMORY_RE.match(text)
    if match is None:
        raise ValueError(text)
    return float(match.group(1)) * _MB_PER_UNIT[(match.group(2) or "mb").lower()]


def _as_pct(text: str, _now: datetime) -> float:
    return float(text.strip().rstrip("%"))


def _as_number(text: str, _now: datetime) -> float:
    return float(text)


def _as_bool(text: str, _now: datetime) -> bool:
    word = text.strip().lower()
    if word not in _TRUE_WORDS | _FALSE_WORDS:
        raise ValueError(text)
    return word in _TRUE_WORDS


def _as_time(text: str, now: datetime) -> datetime:
    clock = _CLOCK_RE.match(text)
    if clock is not None:
        today = parse_time("today", now=now)
        return today + timedelta(hours=int(clock.group(1)), minutes=int(clock.group(2)))
    moment = parse_time(text.replace(" ", "T") if re.match(r"^\d{4}-\d\d-\d\d \d", text) else text, now=now)
    if moment is None:
        raise ValueError(text)
    return moment


_READERS: dict[str, Callable[[str, datetime], Any]] = {
    "ms": _as_ms, "mb": _as_mb, "pct": _as_pct, "number": _as_number, "bool": _as_bool, "time": _as_time,
}

_EXAMPLES = {
    "ms": "a run time is like 15s, 500ms or 2m (a bare number is in ms)",
    "mb": "memory is like 512MB or 1GB (a bare number is in MB)",
    "pct": "a share is like 80%",
    "number": "it's a number, like 3",
    "bool": "it's yes or no",
    "time": "a time is like 14:00 (today), 2h (2 hours ago), today or 2026-10-31T09:00",
}


def _loose_value(text: str) -> Any:
    """
    A value compared with a field the function logs: a number when it reads as one ('42', '30MB' in bytes), else
    the text.
    """
    if _NUMBER_RE.match(text):
        return float(text)
    size = _BYTES_RE.match(text)
    if size is not None:
        return float(size.group(1)) * _BYTES_PER_UNIT[size.group(2).lower()]
    return text


def _compare(node: Compare, values: dict[str, Any], now: datetime) -> bool:
    if node.field not in values or values[node.field] is None:
        return False
    actual = values[node.field]
    if node.op in ("~", "!~"):
        found = any(str(wanted).lower() in _text_of(actual).lower() for wanted in node.values)
        return found if node.op == "~" else not found
    wanted = [query_value(node.field, text, now) for text in node.values]
    if node.op == "in":
        return any(_equal(actual, one) for one in wanted)
    if node.op in ("=", "=="):
        return _equal(actual, wanted[0])
    if node.op == "!=":
        return not _equal(actual, wanted[0])
    return _in_order(actual, node.op, wanted[0])


def _text_of(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _comparable(actual: Any, wanted: Any) -> tuple[Any, Any] | None:
    """
    The two values in a form they can be compared in (numbers as floats, text without case), or None when they
    can't be.
    """
    if isinstance(actual, bool) or isinstance(wanted, bool):
        return (_truthy(actual), _truthy(wanted))
    if isinstance(actual, datetime) and isinstance(wanted, datetime):
        return (actual, wanted)
    if isinstance(actual, (int, float)) and isinstance(wanted, (int, float)):
        return (float(actual), float(wanted))
    if isinstance(actual, str) and isinstance(wanted, (int, float)) and _NUMBER_RE.match(actual):
        return (float(actual), float(wanted))
    if isinstance(actual, (dict, list, datetime)) or isinstance(wanted, datetime):
        return None
    return (str(actual).strip().lower(), str(wanted).strip().lower())


def _equal(actual: Any, wanted: Any) -> bool:
    pair = _comparable(actual, wanted)
    return pair is not None and pair[0] == pair[1]


def _in_order(actual: Any, op: str, wanted: Any) -> bool:
    pair = _comparable(actual, wanted)
    if pair is None:
        return False
    left, right = pair
    try:
        return {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right}[op]
    except TypeError:
        return False


def short_name_of(name: str, known: Iterable[str]) -> str | None:
    """
    The one known field a short name stands for: 'size' -> 'event.Records.s3.object.size' when no other field ends
    in '.size'; None when it's ambiguous or nothing does.
    """
    ending = "." + name.lower()
    found = [known_name for known_name in known if known_name.lower().endswith(ending)]
    return found[0] if len(found) == 1 else None


def closest_field(name: str, known: Iterable[str]) -> str | None:
    """
    The known field a mistyped name most likely meant ('durtion' -> 'duration'), or None.
    """
    names = list(known)
    lowered = {known_name.lower(): known_name for known_name in names}
    if name.lower() in lowered:
        return lowered[name.lower()]
    short = short_name_of(name, names)
    if short is not None:
        return short
    found = difflib.get_close_matches(name, names, n=1, cutoff=0.75)
    return found[0] if found else None

