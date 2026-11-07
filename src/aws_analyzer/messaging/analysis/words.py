"""
EventBridge event patterns and SNS filter policies in plain English: which events a rule matches, which messages a
subscription lets through. Both use the same matching language. Pure: no AWS calls.
"""

from __future__ import annotations

import json
from typing import Any


_MOST_VALUES = 4

_NUMERIC_WORDS = {"=": "=", ">": ">", ">=": "≥", "<": "<", "<=": "≤"}


def _value_text(value: Any) -> str:
    return "null" if value is None else str(value)


def _listed(values: list[Any]) -> str:
    """
    A few values as words: 'paid', 'paid or shipped', 'paid, shipped or refunded', '... and 3 more'.
    """
    texts = [_value_text(v) for v in values]
    if len(texts) > _MOST_VALUES:
        shown = ", ".join(texts[:_MOST_VALUES])
        return f"{shown} or {len(texts) - _MOST_VALUES} more"
    if len(texts) <= 1:
        return "".join(texts)
    return f"{', '.join(texts[:-1])} or {texts[-1]}"


def _numeric_text(terms: list[Any]) -> str:
    """
    {"numeric": [">", 0, "<=", 5]} -> '> 0 and ≤ 5'.
    """
    pairs = [f"{_NUMERIC_WORDS.get(str(terms[i]), terms[i])} {terms[i + 1]}" for i in range(0, len(terms) - 1, 2)]
    return " and ".join(pairs)


def _anything_but_text(value: Any) -> str:
    if isinstance(value, dict):
        return f"anything but {_matcher_text(value)}"
    values = value if isinstance(value, list) else [value]
    return f"anything but {_listed(values)}"


_MATCHERS = {
    "prefix": lambda v: f"starts with {v.get('equals-ignore-case', v) if isinstance(v, dict) else v}",
    "suffix": lambda v: f"ends with {v.get('equals-ignore-case', v) if isinstance(v, dict) else v}",
    "equals-ignore-case": lambda v: f"is {v} (any case)",
    "wildcard": lambda v: f"matches {v}",
    "exists": lambda v: "is present" if v else "is missing",
    "numeric": lambda v: _numeric_text(v if isinstance(v, list) else [v]),
    "anything-but": _anything_but_text,
    "cidr": lambda v: f"is in {v}",
}


def _matcher_text(matcher: dict[str, Any]) -> str:
    """
    One content filter as words: {"prefix": "eu-"} -> 'starts with eu-'.
    """
    parts = [_MATCHERS[key](value) if key in _MATCHERS else f"{key} {json.dumps(value)}"
             for key, value in matcher.items()]
    return " and ".join(parts)


def _field_text(path: str, values: list[Any]) -> str:
    """
    What one field of a pattern asks for: 'detail.status is paid or shipped', 'detail.amount > 100'.
    """
    plain = [v for v in values if not isinstance(v, dict)]
    filters = [_matcher_text(v) for v in values if isinstance(v, dict)]
    clauses = []
    if plain:
        clauses.append(f"is {_listed(plain)}")
    clauses += filters
    return f"{path} {' or '.join(clauses)}"


def _conditions(pattern: dict[str, Any], prefix: str = "") -> list[str]:
    """
    Every field a pattern matches on, as words, nested fields by their dotted path; '$or' becomes 'either ... or ...'.
    """
    conditions: list[str] = []
    for key, value in pattern.items():
        if key == "$or" and isinstance(value, list):
            options = [" and ".join(_conditions(option, prefix)) for option in value if isinstance(option, dict)]
            conditions.append("either " + " or ".join(f"({option})" for option in options))
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            conditions += _conditions(value, f"{path}.")
        else:
            conditions.append(_field_text(path, value if isinstance(value, list) else [value]))
    return conditions


def describe_pattern(pattern: dict[str, Any] | None) -> str:
    """
    Which events an EventBridge rule matches, in a sentence: {"source": ["shop.orders"], "detail-type":
    ["OrderPlaced"], "detail": {"total": [{"numeric": [">", 100]}]}} -> 'OrderPlaced events from shop.orders where
    detail.total > 100'.
    """
    if not pattern:
        return "no event pattern"
    rest = dict(pattern)
    types = rest.pop("detail-type", None)
    sources = rest.pop("source", None)
    head = "events"
    if isinstance(types, list) and all(isinstance(t, str) for t in types):
        head = f"{_listed(types)} events"
    elif types is not None:
        rest["detail-type"] = types
    if isinstance(sources, list) and all(isinstance(s, str) for s in sources):
        head += f" from {_listed(sources)}"
    elif sources is not None:
        rest["source"] = sources
    conditions = _conditions(rest)
    return f"{head} where {' and '.join(conditions)}" if conditions else head


def describe_filter(policy: dict[str, Any] | None, scope: str = "MessageAttributes") -> str:
    """
    Which messages an SNS subscription's filter policy lets through, in a sentence: {"type": ["order"]} ->
    "messages whose attribute type is order"; with scope 'MessageBody' it reads the message's JSON body instead.
    """
    if not policy:
        return "every message"
    where = "body field" if scope == "MessageBody" else "attribute"
    conditions = _conditions(policy)
    return f"messages whose {where} " + " and ".join(conditions)
