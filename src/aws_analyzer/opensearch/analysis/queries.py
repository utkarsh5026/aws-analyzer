"""
Metadata filters (where=) as OpenSearch query DSL, and the k-NN query and Python to copy.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Iterable

from ..models import IndexInfo, VectorField
from .parsing import _SPACE_NAMES


_RANGE_OPS = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}

_EQUAL_OPS = ("=", "==", "eq")

_NOT_EQUAL_OPS = ("!=", "<>", "ne")

_FILTER_OPS = (
    *_RANGE_OPS, *_EQUAL_OPS, *_NOT_EQUAL_OPS, "between", "in", "prefix", "begins_with", "contains", "exists",
    "missing", "not_exists",
)

_DSL_KEYS = {
    "bool", "term", "terms", "range", "match", "match_phrase", "exists", "prefix", "wildcard", "ids", "nested",
    "query_string", "simple_query_string", "regexp", "match_all", "geo_distance", "geo_bounding_box",
}


def _exact(name: str, fields: dict[str, str]) -> tuple[str, bool]:
    """
    (field to match exact values on, whether it's analyzed text without a keyword sub-field).
    """
    if fields.get(name) in ("text", "match_only_text"):
        keyword = f"{name}.keyword"
        return (keyword, False) if fields.get(keyword) == "keyword" else (name, True)
    return name, False


def _filter_clause(name: str, spec: Any, fields: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """
    One where= entry -> (query DSL clause, whether it excludes the documents it matches).
    """
    if isinstance(spec, tuple) and spec and isinstance(spec[0], str) and spec[0].lower() in _FILTER_OPS:
        op, args = spec[0].lower(), spec[1:]
    elif isinstance(spec, (list, set, frozenset)):
        op, args = "in", (list(spec),)
    else:
        op, args = "=", (spec,)
    wanted = {"between": 2, "exists": 0, "missing": 0, "not_exists": 0}.get(op, 1)
    if len(args) != wanted:
        example = {"between": "('between', 2020, 2024)", "exists": "('exists',)"}.get(op, f"('{op}', value)")
        raise ValueError(f"where={{{name!r}: ...}}: {op!r} takes {wanted} value{'s' if wanted != 1 else ''}, "
                         f"like {example}")
    exact, text_only = _exact(name, fields)
    if op in _EQUAL_OPS + _NOT_EQUAL_OPS:
        clause = {"match_phrase": {name: args[0]}} if text_only else {"term": {exact: args[0]}}
        return clause, op in _NOT_EQUAL_OPS
    if op in _RANGE_OPS:
        return {"range": {name: {_RANGE_OPS[op]: args[0]}}}, False
    if op == "between":
        return {"range": {name: {"gte": args[0], "lte": args[1]}}}, False
    if op == "in":
        values = list(args[0]) if isinstance(args[0], (list, tuple, set, frozenset)) else [args[0]]
        return {"terms": {exact: values}}, False
    if op in ("prefix", "begins_with"):
        return {"prefix": {exact: args[0]}}, False
    if op == "contains":
        return {"match": {name: args[0]}}, False
    return {"exists": {"field": name}}, op != "exists"


def _check_where(where: Any, info: IndexInfo) -> None:
    """
    Refuse a where= on a field the mapping doesn't index, which would silently match nothing.
    """
    if isinstance(where, dict) and not (len(where) == 1 and next(iter(where)) in _DSL_KEYS):
        for name in where:
            if name in info.unindexed:
                raise ValueError(f"Can't filter on {name!r}: the mapping keeps it without indexing it (\"index\": "
                                 "false), so OpenSearch can't search it. index_info() lists the fields you can filter on.")


def build_filter(where: Any, fields: dict[str, str] | None = None) -> dict[str, Any] | None:
    """
    A friendly filter -> OpenSearch query DSL, for the filter of a k-NN search.

    where={'lang': 'en', 'year': ('>=', 2024), 'team': ['billing', 'support'], 'draft': ('!=', True)} matches
    documents in English from 2024 on, of the billing or support team, that aren't drafts. Operators: '=', '!=',
    '>', '>=', '<', '<=', ('between', low, high), ('in', [...]), ('prefix', 'abc'), ('contains', 'some words'),
    ('exists',) and ('missing',). With `fields` (the index's field types), a text field is matched exactly through
    its .keyword sub-field, and a field the index doesn't have is reported with the closest names. Query DSL
    ({'term': {...}}, {'bool': {...}}) passes through unchanged.
    """
    if where is None or where == {}:
        return None
    if not isinstance(where, dict):
        raise ValueError("where= takes a dict, like {'lang': 'en', 'year': ('>=', 2024)}")
    if len(where) == 1 and next(iter(where)) in _DSL_KEYS:
        return where
    fields = fields or {}
    must: list[dict[str, Any]] = []
    must_not: list[dict[str, Any]] = []
    for name, spec in where.items():
        if fields and name not in fields:
            close = difflib.get_close_matches(name, list(fields), n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            raise ValueError(f"The index has no field {name!r}.{hint} index_info() lists the fields you can filter on.")
        clause, negate = _filter_clause(name, spec, fields)
        (must_not if negate else must).append(clause)
    query: dict[str, Any] = {}
    if must:
        query["filter"] = must
    if must_not:
        query["must_not"] = must_not
    return {"bool": query}


def describe_filter(where: Any) -> str:
    """
    where= in words: {'lang': 'en', 'year': ('>=', 2024)} -> "lang = 'en' and year >= 2024".
    """
    if not where:
        return "no filter"
    if not isinstance(where, dict) or (len(where) == 1 and next(iter(where)) in _DSL_KEYS):
        return "a query DSL filter"
    parts = []
    for name, spec in where.items():
        if isinstance(spec, tuple) and spec and isinstance(spec[0], str) and spec[0].lower() in _FILTER_OPS:
            op, args = spec[0].lower(), spec[1:]
            if op == "between":
                parts.append(f"{name} between {args[0]!r} and {args[1]!r}")
            elif op in ("exists", "missing", "not_exists"):
                parts.append(f"{name} {'is set' if op == 'exists' else 'is missing'}")
            else:
                parts.append(f"{name} {op} {args[0]!r}")
        elif isinstance(spec, (list, set, frozenset)):
            parts.append(f"{name} in {sorted(spec, key=str)!r}")
        else:
            parts.append(f"{name} = {spec!r}")
    return " and ".join(parts)


def knn_query(
    vf: VectorField,
    vector: list[float],
    k: int = 10,
    *,
    filter: dict[str, Any] | None = None,
    ef_search: int | None = None,
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    """
    The _search body for the k nearest neighbours of `vector` in field `vf`. The filter is applied during the
    search (faiss and lucene), or after it for nmslib and nested fields, which can return fewer than k then.
    ef_search overrides the index's candidate list size for this query (OpenSearch 2.16+); `exclude` leaves those
    fields out of the documents returned.
    """
    inner: dict[str, Any] = {"vector": list(vector), "k": k}
    after = filter is not None and (vf.engine == "nmslib" or vf.nested is not None)
    if filter is not None and not after:
        inner["filter"] = filter
    if ef_search:
        inner["method_parameters"] = {"ef_search": ef_search}
    query: dict[str, Any] = {"knn": {vf.path: inner}}
    if vf.nested:
        query = {"nested": {"path": vf.nested, "query": query, "score_mode": "max"}}
    if after:
        clauses = filter.get("bool", {}) if isinstance(filter, dict) and set(filter) == {"bool"} else {}
        if clauses and set(clauses) <= {"filter", "must_not"}:
            query = {"bool": {"must": [query], **clauses}}
        else:
            query = {"bool": {"must": [query], "filter": [filter]}}
    body: dict[str, Any] = {"size": k, "query": query}
    excluded = sorted(set(exclude))
    if excluded:
        body["_source"] = {"excludes": excluded}
    return body


def query_python(body: dict[str, Any], index: str) -> str:
    """
    A k-NN search body as opensearch-py code to copy, with the query vector as a variable.
    """
    import pprint

    shown = json.loads(json.dumps(body))

    def blank(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "vector" and isinstance(value, list):
                    node[key] = "__QUERY_VECTOR__"
                else:
                    blank(value)
        elif isinstance(node, list):
            for value in node:
                blank(value)

    blank(shown)
    text = pprint.pformat(shown, indent=1, width=100, sort_dicts=False).replace("'__QUERY_VECTOR__'", "query_vector")
    return f"body = {text}\nclient.search(index={index!r}, body=body)   # opensearch-py"


def describe_vector_field(vf: VectorField) -> str:
    """
    '1,024 dims · faiss HNSW · cosine' (· fp16, · on disk 32x when compressed).
    """
    parts = [f"{vf.dimension:,} dims" if vf.dimension else "? dims"]
    if vf.model_id:
        parts.append(f"trained model {vf.model_id}")
    else:
        parts.append(f"{vf.engine or 'default engine'} {vf.algorithm.upper()}")
    parts.append(_SPACE_NAMES.get(vf.space, vf.space))
    if vf.data_type != "float":
        parts.append(f"{vf.data_type} vectors")
    if vf.encoder:
        parts.append(vf.encoder)
    if vf.mode == "on_disk" or vf.compression:
        parts.append(" ".join(filter(None, ["on disk" if vf.mode == "on_disk" else "", vf.compression or "32x"])))
    return " · ".join(parts)
