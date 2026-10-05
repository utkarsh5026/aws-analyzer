"""A small in-memory OpenSearch REST endpoint for the opensearch.py tests and the demo (.claude/skills/demo/demo.py).

FakeCluster answers what OpenSearchAnalyzer sends: _cat/indices, _mapping, _settings, _count, _search (k-NN with
OpenSearch's score formulas, filters, nested fields, random sampling, ids), _stats/segments, _cluster/health and
_plugins/_knn/stats. Pass it as OpenSearchAnalyzer(http=cluster). It records every request, refuses anything that
isn't a GET or a POST to _search / _count (so a test fails if the analyzer ever tries to write), and with
serverless=True answers like OpenSearch Serverless, which has no cluster-level APIs.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass
class FakeIndex:
    name: str
    mappings: dict[str, Any]
    docs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)  # flat, without the "index." prefix
    shards: int = 1
    replicas: int = 1
    health: str = "green"
    segments: int = 4
    deleted: int = 0
    size_bytes: int | None = None  # primaries; estimated from the documents when None
    scale: int = 1  # counts report this many documents for each one stored (the demo's big indexes); search doesn't


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, str]
    body: Any
    headers: dict[str, str]


def _values(source: Any, path: str) -> list[Any]:
    if isinstance(source, list):
        return [v for item in source for v in _values(item, path)]
    if not isinstance(source, dict):
        return []
    if path in source:
        value = source[path]
        if isinstance(value, list) and (not value or not isinstance(value[0], (int, float))):
            return list(value)  # a list of values or nested documents; a list of numbers is one vector
        return [value]
    head, _, rest = path.partition(".")
    return _values(source[head], rest) if rest and head in source else []


def _compare(value: Any, op: str, bound: Any) -> bool:
    try:
        return {"gt": value > bound, "gte": value >= bound, "lt": value < bound, "lte": value <= bound}[op]
    except TypeError:
        return False


def matches(doc_id: str, source: dict[str, Any], query: dict[str, Any] | None) -> bool:
    """Evaluate the query DSL this file needs against one document."""
    if not query:
        return True
    (kind, spec), = query.items()
    if kind == "match_all":
        return True
    if kind == "exists":
        return any(v not in (None, [], "") for v in _values(source, spec["field"]))
    if kind == "ids":
        return doc_id in spec["values"]
    if kind == "term":
        (name, value), = spec.items()
        value = value.get("value") if isinstance(value, dict) else value
        name = name.removesuffix(".keyword")
        return value in _values(source, name)
    if kind == "terms":
        (name, values), = spec.items()
        return any(v in values for v in _values(source, name.removesuffix(".keyword")))
    if kind == "range":
        (name, ops), = spec.items()
        return any(all(_compare(v, op, b) for op, b in ops.items()) for v in _values(source, name))
    if kind == "prefix":
        (name, value), = spec.items()
        return any(str(v).startswith(value) for v in _values(source, name.removesuffix(".keyword")))
    if kind in ("match", "match_phrase"):
        (name, value), = spec.items()
        words = str(value).lower()
        return any(words in str(v).lower() for v in _values(source, name))
    if kind == "nested":
        items = _values(source, spec["path"])
        return any(matches(doc_id, item if isinstance(item, dict) else {}, _strip(spec["query"], spec["path"]))
                   for item in items)
    if kind == "bool":
        def clauses(key: str) -> list[dict[str, Any]]:
            found = spec.get(key) or []
            return found if isinstance(found, list) else [found]
        return (
            all(matches(doc_id, source, q) for q in clauses("filter") + clauses("must"))
            and not any(matches(doc_id, source, q) for q in clauses("must_not"))
            and (not clauses("should") or any(matches(doc_id, source, q) for q in clauses("should")))
        )
    if kind == "function_score":
        return matches(doc_id, source, spec.get("query"))
    raise ValueError(f"the fake cluster doesn't know the {kind!r} query")


def _strip(query: Any, prefix: str) -> Any:
    """Field names inside a nested query are full paths; inside one nested document they're relative."""
    if isinstance(query, list):
        return [_strip(q, prefix) for q in query]
    if not isinstance(query, dict):
        return query
    out: dict[str, Any] = {}
    for key, value in query.items():
        if key == "exists":
            out[key] = {"field": value["field"].removeprefix(prefix + ".")}
        elif key in ("term", "terms", "range", "prefix", "match", "match_phrase"):
            out[key] = {name.removeprefix(prefix + "."): v for name, v in value.items()}
        elif key in ("bool", "function_score"):
            out[key] = {name: _strip(v, prefix) for name, v in value.items()}
        else:
            out[key] = _strip(value, prefix)
    return out


def score(space: str, query: list[float], vector: list[float]) -> float:
    """OpenSearch's k-NN score for one vector (the formulas opensearch.score_to_similarity undoes)."""
    if space == "cosinesimil":
        norm = math.sqrt(sum(x * x for x in query)) * math.sqrt(sum(x * x for x in vector))
        cosine = sum(a * b for a, b in zip(query, vector)) / norm if norm else 0.0
        return (1 + cosine) / 2
    if space == "innerproduct":
        ip = sum(a * b for a, b in zip(query, vector))
        return ip + 1 if ip > 0 else 1 / (1 - ip)
    if space == "l1":
        return 1 / (1 + sum(abs(a - b) for a, b in zip(query, vector)))
    return 1 / (1 + sum((a - b) ** 2 for a, b in zip(query, vector)))


class FakeCluster:
    """Call it like a transport: cluster(method, url, body, headers) -> (status, body bytes)."""

    def __init__(self, indexes: list[FakeIndex] | None = None, *, serverless: bool = False, nodes: int = 3,
                 knn_memory_kb: int = 0, circuit_breaker: bool = False, evictions: int = 0, deny: int | None = None):
        self.indexes = {i.name: i for i in indexes or []}
        self.serverless = serverless
        self.nodes = nodes
        self.knn_memory_kb = knn_memory_kb
        self.circuit_breaker = circuit_breaker
        self.evictions = evictions
        self.deny = deny  # answer every request with this status (401 / 403)
        self.requests: list[Request] = []
        self.random = random.Random(7)

    def add(self, index: FakeIndex) -> FakeIndex:
        self.indexes[index.name] = index
        return index

    # --------------------------------------------------------------- plumbing

    def __call__(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        parts = urlsplit(url)
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        payload = json.loads(body) if body else None
        path = unquote(parts.path)
        self.requests.append(Request(method, path, params, payload, dict(headers)))
        if method not in ("GET", "POST") or (method == "POST" and not path.endswith(("/_search", "/_count"))):
            raise AssertionError(f"the analyzer sent {method} {path}: it must only read")
        if self.deny == 403:
            if self.serverless:
                return self._answer(403, {"status": 403, "error": {"reason": "403 Forbidden", "type": "Forbidden"}})
            return self._answer(403, {"Message": "User: arn:aws:sts::123456789012:assumed-role/Notebook/x is not "
                                                 "authorized to perform: es:ESHttpGet"})
        if self.deny == 401:
            return self._answer(401, {"error": {"type": "security_exception", "reason": "missing authentication"}})
        try:
            return self._route(method, path, params, payload)
        except KeyError as exc:
            return self._missing(str(exc).strip("'"))

    @staticmethod
    def _answer(status: int, payload: Any) -> tuple[int, bytes]:
        return status, json.dumps(payload).encode()

    def _missing(self, name: str) -> tuple[int, bytes]:
        error = {"type": "index_not_found_exception", "reason": f"no such index [{name}]", "index": name}
        return self._answer(404, {"error": {"root_cause": [error], **error}, "status": 404})

    def _unsupported(self, path: str) -> tuple[int, bytes]:
        return self._answer(404, {"error": f"no handler found for uri [{path}]", "status": 404})

    def _route(self, method: str, path: str, params: dict[str, str], body: Any) -> tuple[int, bytes]:
        parts = [p for p in path.split("/") if p]
        if parts[:2] == ["_cat", "indices"]:
            names = list(self.indexes) if len(parts) == 2 else self._names(parts[2])
            return self._answer(200, [self._cat(self.indexes[n]) for n in names])
        if parts == ["_cluster", "health"] or parts in (["_plugins", "_knn", "stats"], ["_stats", "segments"]):
            if self.serverless:
                return self._unsupported(path)
            if parts[0] == "_cluster":
                return self._answer(200, self._health())
            if parts[0] == "_plugins":
                return self._answer(200, self._knn())
            return self._answer(200, self._stats(list(self.indexes)))
        if parts in (["_mapping"], ["_settings"]):
            if self.serverless:
                return self._unsupported(path)
            names = list(self.indexes)
        elif len(parts) >= 2:
            names = self._names(parts[0])
        else:
            return self._unsupported(path)
        action = parts[-1]
        if action == "_mapping":
            return self._answer(200, {n: {"mappings": self.indexes[n].mappings} for n in names})
        if action == "_settings":
            return self._answer(200, {n: {"settings": self._settings(self.indexes[n])} for n in names})
        if action == "segments" and parts[-2] == "_stats":
            return self._unsupported(path) if self.serverless else self._answer(200, self._stats(names))
        index = self.indexes[names[0]]
        if action == "_count":
            query = (body or {}).get("query")
            count = sum(matches(i, s, query) for i, s in index.docs)
            return self._answer(200, {"count": count * index.scale})
        if action == "_search":
            return self._search(index, body or {})
        return self._unsupported(path)

    def _names(self, pattern: str) -> list[str]:
        names = [n for n in pattern.split(",") if n]
        for name in names:
            if name not in self.indexes:
                raise KeyError(name)
        return names

    # ------------------------------------------------------------- answers

    def _size(self, index: FakeIndex) -> int:
        return index.size_bytes if index.size_bytes is not None else sum(len(json.dumps(s)) for _, s in index.docs)

    def _lucene_docs(self, index: FakeIndex) -> int:
        nested = [p for p, spec in index.mappings.get("properties", {}).items() if spec.get("type") == "nested"]
        return len(index.docs) + sum(len(_values(s, p)) for _, s in index.docs for p in nested)

    def _cat(self, index: FakeIndex) -> dict[str, Any]:
        size = self._size(index)
        row = {"health": index.health, "status": "open", "index": index.name, "uuid": f"uuid-{index.name}",
               "pri": str(index.shards), "rep": str(index.replicas),
               "docs.count": str(self._lucene_docs(index) * index.scale),
               "docs.deleted": str(index.deleted), "store.size": str(size * (1 + index.replicas)),
               "pri.store.size": str(size)}
        if self.serverless:  # Serverless leaves out what it manages itself
            for key in ("health", "pri", "rep", "pri.store.size"):
                row.pop(key)
        return row

    def _settings(self, index: FakeIndex) -> dict[str, Any]:
        flat = {"index.creation_date": "1717200000000", "index.provided_name": index.name}
        if not self.serverless:  # Serverless manages shards and replicas itself
            flat.update({"index.number_of_shards": str(index.shards), "index.number_of_replicas": str(index.replicas)})
        flat.update({f"index.{k}": str(v).lower() if isinstance(v, bool) else str(v)
                     for k, v in index.settings.items()})
        return flat

    def _stats(self, names: list[str]) -> dict[str, Any]:
        indices = {}
        for name in names:
            index = self.indexes[name]
            copies = index.shards * (1 + index.replicas)
            indices[name] = {"primaries": {"segments": {"count": index.segments * index.shards}},
                             "total": {"segments": {"count": index.segments * copies}}}
        return {"_all": {}, "indices": indices}

    def _health(self) -> dict[str, Any]:
        statuses = [i.health for i in self.indexes.values()] or ["green"]
        status = "red" if "red" in statuses else "yellow" if "yellow" in statuses else "green"
        unassigned = sum(i.shards * i.replicas for i in self.indexes.values() if i.health == "yellow")
        return {"cluster_name": "123456789012:vectors", "status": status, "number_of_nodes": self.nodes,
                "number_of_data_nodes": self.nodes, "active_shards": 10, "unassigned_shards": unassigned}

    def _knn(self) -> dict[str, Any]:
        per_node = self.knn_memory_kb // max(1, self.nodes)
        vector = [n for n, i in self.indexes.items() if "knn_vector" in json.dumps(i.mappings)]
        nodes = {}
        for n in range(self.nodes):
            nodes[f"node-{n}"] = {
                "graph_memory_usage": per_node,
                "graph_memory_usage_percentage": 95.0 if self.circuit_breaker else 40.0,
                "cache_capacity_reached": self.circuit_breaker,
                "eviction_count": self.evictions // max(1, self.nodes),
                "hit_count": 120,
                "miss_count": 8,
                "indices_in_cache": {name: {"graph_memory_usage": per_node // max(1, len(vector)),
                                            "graph_memory_usage_percentage": 20.0, "graph_count": 2}
                                     for name in vector},
            }
        return {"_nodes": {"total": self.nodes, "successful": self.nodes, "failed": 0},
                "cluster_name": "123456789012:vectors", "circuit_breaker_triggered": self.circuit_breaker,
                "nodes": nodes}

    def _space(self, index: FakeIndex, path: str) -> str:
        spec: Any = {"properties": index.mappings.get("properties", {})}
        for part in path.split("."):
            spec = (spec.get("properties") or {}).get(part, {})
        method = spec.get("method") or {}
        return method.get("space_type") or spec.get("space_type") or index.settings.get("knn.space_type") or "l2"

    def _search(self, index: FakeIndex, body: dict[str, Any]) -> tuple[int, bytes]:
        query = body.get("query") or {"match_all": {}}
        size = int(body.get("size", 10))
        knn, nested, outer = self._knn_parts(query)
        if knn is not None:
            (path, spec), = knn.items()
            space = self._space(index, path)
            ranked = []
            for doc_id, source in index.docs:  # a filter inside the knn clause applies during the search
                if not matches(doc_id, source, spec.get("filter")):
                    continue
                vectors = [v for v in _values(source, path) if isinstance(v, list)]
                if vectors:
                    ranked.append((max(score(space, spec["vector"], v) for v in vectors), doc_id, source))
            ranked.sort(key=lambda r: -r[0])
            nearest = ranked[: spec["k"]]  # one around it applies after: fewer than k can come back
            hits = [(s, i, src) for s, i, src in nearest if matches(i, src, outer)][:size]
        else:
            found = [(1.0, i, s) for i, s in index.docs if matches(i, s, query)]
            if "function_score" in query:
                found = [(self.random.random(), i, s) for _, i, s in found]
                found.sort(key=lambda r: -r[0])
            hits = found[:size]
            ranked = found
        excludes = set((body.get("_source") or {}).get("excludes") or []) if isinstance(body.get("_source"), dict) \
            else set()
        answer_hits = []
        for value, doc_id, source in hits:
            kept = {k: v for k, v in source.items() if k not in excludes}
            answer_hits.append({"_index": index.name, "_id": doc_id, "_score": value, "_source": kept})
        return self._answer(200, {
            "took": 7,
            "timed_out": False,
            "hits": {"total": {"value": len(ranked) * (1 if knn is not None else index.scale), "relation": "eq"},
                     "max_score": answer_hits[0]["_score"] if answer_hits else None, "hits": answer_hits},
        })

    @staticmethod
    def _knn_parts(query: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
        """(the knn clause, its nested path, a filter applied after it) from a k-NN query, or (None, None, None)."""
        if "knn" in query:
            return query["knn"], None, None
        if "nested" in query and "knn" in query["nested"]["query"]:
            return query["nested"]["query"]["knn"], query["nested"]["path"], None
        if "bool" in query:
            must = query["bool"].get("must") or []
            for clause in must if isinstance(must, list) else [must]:
                knn, nested, _ = FakeCluster._knn_parts(clause)
                if knn is not None:
                    rest = {key: value for key, value in query["bool"].items() if key != "must"}
                    return knn, nested, {"bool": rest}
        return None, None, None


def unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def topic_vector(topic: int, dims: int, rng: random.Random, noise: float = 0.6) -> list[float]:
    """A unit vector near the axis of `topic`: documents about the same topic are close to each other. `noise` is
    the typical length of the random part, whatever the number of dimensions."""
    vector = [rng.gauss(0, noise / math.sqrt(dims)) for _ in range(dims)]
    vector[topic % dims] += 1.0
    return unit(vector)
