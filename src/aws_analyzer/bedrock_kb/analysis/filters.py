"""
where= filters in words, and how findings name the data sources a search was narrowed to.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..._bedrock.filters import _is_bedrock_filter
from ..._bedrock.models import Passage


# ----------------------------------------------------------- filters (where=)


def describe_filter(where: Any) -> str:
    """
    `where` as text: {'team': 'billing', 'year': ('>=', 2024)} -> "team = 'billing', year >= 2024".
    """
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
