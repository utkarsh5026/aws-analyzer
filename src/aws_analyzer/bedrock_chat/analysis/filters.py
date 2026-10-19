"""
Filters: where= as JSON or Bedrock's filter, in words, and the data sources and files a
request is narrowed to, put in and taken back out.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..._bedrock.blocks import _plain_json
from ..._bedrock.filters import DATA_SOURCE_KEY, _is_bedrock_filter, build_filter

from ..helpers import _loads


# ----------------------------------------------------------- filters (where=)


_SYMBOLIC = {">", ">=", "<", "<=", "=", "==", "!=", "<>", "between", "begins_with", "contains", "list_contains"}


def _where_from_json(where: dict[str, Any]) -> dict[str, Any]:
    """
    JSON has no tuples, so in JSON a condition is a list that starts with its operator: {"year": [">=", 2024]}
    -> {"year": (">=", 2024)}. Any other list is the values allowed ({"team": ["billing", "support"]}), except
    ["in", [...]] and ["not_in", [...]].
    """
    out: dict[str, Any] = {}
    for key, spec in where.items():
        op = spec[0].lower() if isinstance(spec, list) and spec and isinstance(spec[0], str) else None
        if op in _SYMBOLIC and len(spec) >= 2 or op in ("in", "not_in") and len(spec) == 2 and isinstance(spec[1], list):
            out[key] = tuple(spec)
        else:
            out[key] = spec
    return out


def as_filter(where: Any) -> dict[str, Any]:
    """
    The `filter` setting -> a Bedrock RetrievalFilter. Takes a RetrievalFilter as it is ({"equals": {"key": "team",
    "value": "billing"}}), or the short form build_filter() reads ({"team": "billing", "year": (">=", 2024)}), as
    a dict or JSON text. In JSON a condition is a list that starts with its operator: {"year": [">=", 2024]}.
    """
    if isinstance(where, str):
        where = _loads(where, "filter")
    if isinstance(where, dict) and not _is_bedrock_filter(where):
        where = _where_from_json(where)
    built = build_filter(where)
    if not built:
        raise ValueError('filter needs at least one condition, like {"team": "billing"}')
    return _plain_json(built)


_FILTER_WORDS = {
    "equals": "=", "notEquals": "≠", "greaterThan": ">", "greaterThanOrEquals": "≥", "lessThan": "<",
    "lessThanOrEquals": "≤", "in": "is one of", "notIn": "is none of", "startsWith": "starts with",
    "stringContains": "contains", "listContains": "has",
}


def describe_filter(condition: Any) -> str:
    """
    A Bedrock RetrievalFilter in words: {"andAll": [{"equals": {"key": "team", "value": "billing"}}, ...]} ->
    'team = "billing" and year ≥ 2024'.
    """
    if not isinstance(condition, dict) or len(condition) != 1:
        return "a filter"
    op, body = next(iter(condition.items()))
    if op in ("andAll", "orAll") and isinstance(body, list):
        parts = [describe_filter(c) for c in body]
        parts = [f"({p})" if isinstance(c, dict) and next(iter(c), "") in ("andAll", "orAll") else p
                 for p, c in zip(parts, body)]
        return (" and " if op == "andAll" else " or ").join(parts)
    if isinstance(body, dict):
        return f"{body.get('key')} {_FILTER_WORDS.get(op, op)} {json.dumps(body.get('value'), ensure_ascii=False)}"
    return "a filter"


SOURCE_URI_KEY = "x-amz-bedrock-kb-source-uri"  # Bedrock tags every chunk with the file it came from


def files_filter(uris: Iterable[str]) -> dict[str, Any] | None:
    """
    A RetrievalFilter that keeps only passages from these files (their s3:// paths, as Bedrock stores them):
    {'equals': ...} for one, {'in': ...} for several, None for none.
    """
    unique = list(dict.fromkeys(str(u) for u in uris if u))
    if not unique:
        return None
    if len(unique) == 1:
        return {"equals": {"key": SOURCE_URI_KEY, "value": unique[0]}}
    return {"in": {"key": SOURCE_URI_KEY, "value": unique}}


def with_files(condition: dict[str, Any] | None, uris: Iterable[str]) -> dict[str, Any] | None:
    """
    A RetrievalFilter narrowed to these files: both must match.
    """
    only = files_filter(uris)
    if only is None:
        return condition
    if not condition:
        return only
    rest = condition["andAll"] if list(condition) == ["andAll"] else [condition]
    return {"andAll": [only, *rest]}


def split_condition(condition: Any, key: str) -> tuple[list[str], Any]:
    """
    Takes the condition on one of Bedrock's own keys (DATA_SOURCE_KEY, SOURCE_URI_KEY) out of a RetrievalFilter:
    -> ([the values it allows], the rest of the filter, or None). A filter without one comes back as it is, with [].
    """

    def values_of(part: Any) -> list[str] | None:
        if not isinstance(part, dict) or len(part) != 1:
            return None
        op, body = next(iter(part.items()))
        if not isinstance(body, dict) or body.get("key") != key:
            return None
        value = body.get("value")
        if op == "equals" and isinstance(value, str):
            return [value]
        if op == "in" and isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return list(value)
        return None

    found = values_of(condition)
    if found is not None:
        return found, None
    if isinstance(condition, dict) and list(condition) == ["andAll"] and isinstance(condition["andAll"], list):
        parts = condition["andAll"]
        for i, part in enumerate(parts):
            found = values_of(part)
            if found is not None:
                rest = parts[:i] + parts[i + 1 :]
                return found, rest[0] if len(rest) == 1 else {"andAll": rest} if rest else None
    return [], condition


def split_data_sources(condition: Any) -> tuple[list[str], Any]:
    """
    The opposite of with_data_sources(): a RetrievalFilter -> ([IDs of the data sources it keeps], the rest of the
    filter, or None). A filter without a data source condition comes back as it is, with [].
    """
    return split_condition(condition, DATA_SOURCE_KEY)
