"""
where= filters in words, typed filters read back into where= (parse_where), and how findings name the data sources
a search was narrowed to.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..._bedrock.filters import _is_bedrock_filter
from ..._bedrock.models import Passage


# ----------------------------------------------------------- filters (where=)


def describe_filter(where: Any) -> str:
    """
    `where` as text: {'team': 'billing', 'year': ('>=', 2024)} -> "team = 'billing', year >= 2024". A filter
    already typed as text (parse_where) reads the same way.
    """
    if isinstance(where, str):
        where = parse_where(where)
    if where is None:
        return ""
    if _is_bedrock_filter(where) or not isinstance(where, dict):
        return "a Bedrock filter"
    parts = []
    for name, spec in where.items():
        if not isinstance(spec, tuple):
            parts.append(
                f"{name} in {list(spec)!r}"
                if isinstance(spec, (list, set, frozenset))
                else f"{name} = {spec!r}"
            )
        elif spec and spec[0] == "between" and len(spec) == 3:
            parts.append(f"{name} between {spec[1]!r} and {spec[2]!r}")
        else:
            op, *args = spec
            parts.append(f"{name} {op} " + ", ".join(map(repr, args)))
    return ", ".join(parts)


_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_HIDDEN = re.compile(r"\x00(\d+)\x00")
_JOINERS = re.compile(r"(;|,|\s+and\s+)", re.IGNORECASE)
_CONDITION = re.compile(
    r"^\s*(?P<key>[\w.\-]+)\s*(?P<op>!=|<>|>=|<=|==|=|>|<|:|\bnot\s+in\b|\bin\b|\bbegins\s+with\b|"
    r"\bstarts\s+with\b|\bcontains\b|\bhas\b|\bbetween\b)\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
_TYPED_OPERATORS = {
    "=": "=", "==": "=", ":": "=", "!=": "!=", "<>": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<=",
    "in": "in", "not in": "not_in", "begins with": "begins_with", "starts with": "begins_with",
    "contains": "contains", "has": "list_contains", "between": "between",
}
_BETWEEN_AND = re.compile(r"\s+and\s+", re.IGNORECASE)
_FILTER_EXAMPLE = "team = billing; year >= 2024"


def _hide_quotes(text: str) -> tuple[str, list[str]]:
    """
    The text with each quoted value replaced by a numbered mark, so the words and separators inside quotes are
    kept as they are, and the quoted values in order.
    """
    quoted: list[str] = []

    def hide(match: re.Match[str]) -> str:
        quoted.append(match.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    return _QUOTED.sub(hide, text), quoted


def _restore_quotes(text: str, quoted: list[str]) -> str:
    return _HIDDEN.sub(lambda match: quoted[int(match.group(1))], text)


def _condition_texts(text: str) -> list[str]:
    """
    The conditions in a typed filter: split on ';', ',' and 'and', where a piece with no operator of its own
    belongs to the condition before it ("region in eu, uk", "year between 2020 and 2024").
    """
    pieces = _JOINERS.split(text)
    conditions: list[str] = []
    for i in range(0, len(pieces), 2):
        piece = pieces[i]
        joiner = pieces[i - 1] if i else ""
        continues = bool(conditions) and _CONDITION.match(piece) is None and joiner != ";"
        if continues:
            conditions[-1] += joiner + piece
        elif piece.strip():
            conditions.append(piece)
    return conditions


def _typed_value(text: str, quoted: list[str]) -> Any:
    """
    One value as typed: a quoted value is text, true / false a boolean, a number a number, else the text.
    """
    text = text.strip()
    whole = _HIDDEN.fullmatch(text)
    if whole:
        return quoted[int(whole.group(1))][1:-1]
    text = _restore_quotes(text, quoted)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            continue
    return text


def _typed_list(text: str, quoted: list[str]) -> list[Any]:
    inside = text.strip().removeprefix("[").removesuffix("]")
    return [_typed_value(item, quoted) for item in inside.split(",") if item.strip()]


def _typed_spec(op: str, value: str, quoted: list[str]) -> Any:
    """
    One condition's operator and value as where= takes them: a value for '=', a list for 'in', else a tuple.
    """
    if op in ("in", "not_in"):
        values = _typed_list(value, quoted)
        return values if op == "in" else ("not_in", values)
    if op == "between":
        bounds = _BETWEEN_AND.split(value.strip())
        if len(bounds) != 2:
            raise ValueError(f"'between' takes two values, like year between 2020 and 2024; got {value.strip()!r}")
        return ("between", _typed_value(bounds[0], quoted), _typed_value(bounds[1], quoted))
    typed = _typed_value(value, quoted)
    return typed if op == "=" else (op, typed)


def parse_where(text: str | None) -> dict[str, Any] | None:
    """
    A metadata filter typed as text -> the where= dict build_filter takes, or None for no filter:

        team = billing                          {'team': 'billing'}
        team = billing; year >= 2024            both must match (also ',' or 'and' between them)
        region in eu, uk                        {'region': ['eu', 'uk']}; also 'not in'
        year between 2020 and 2024              {'year': ('between', 2020, 2024)}
        doc_id begins with POL-                 also 'starts with'
        title contains refund                   text containing it
        tags has gdpr                           a list attribute holding it
        title = "Terms and conditions"          quotes keep a value as it is

    Numbers become numbers and true / false booleans. Raises ValueError, saying how to write it, for a condition it
    can't read or an attribute named twice.
    """
    hidden, quoted = _hide_quotes(str(text or "").strip())
    where: dict[str, Any] = {}
    for condition in _condition_texts(hidden):
        match = _CONDITION.match(condition)
        if match is None or not match.group("value").strip():
            shown = _restore_quotes(condition, quoted).strip()
            raise ValueError(f"Can't read {shown!r} as a condition: write an attribute, an operator and a value, "
                             f"like {_FILTER_EXAMPLE}")
        key = match.group("key")
        if key in where:
            raise ValueError(f"{key!r} is named twice: say it once (year between 2020 and 2024 for a range)")
        op = _TYPED_OPERATORS[" ".join(match.group("op").lower().split())]
        where[key] = _typed_spec(op, match.group("value"), quoted)
    return where or None


def _source_arg(sources: dict[str, str]) -> str | list[str]:
    """
    The data_source= to pass for these data sources in a next step: a name, or a list of names.
    """
    names = [name or ds_id for ds_id, name in sources.items()]
    return names[0] if len(names) == 1 else names


def _outside(passages: Iterable[Passage], sources: dict[str, str]) -> list[Passage]:
    """
    The passages that came from other data sources than the ones asked for.
    """
    if not sources:
        return []
    return [p for p in passages if p.data_source_id and p.data_source_id not in sources]
