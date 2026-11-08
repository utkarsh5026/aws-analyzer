"""
The OpenSearch toolkit: tools over OpenSearchAnalyzer for vector search in OpenSearch Service domains and Serverless
collections. Which domains and collections there are, what each costs and risks (find_vector_stores), one's indexes
with which hold vectors, how many documents have one and the memory the vector graphs need against what the nodes
have (store_indexes), one index's vector fields in plain words (engine, similarity, what a score means) with its
findings (index_details), and a check of a sample of its vectors: dimensions, lengths, zero and repeated vectors,
documents without one (check_vectors). A Bedrock knowledge base can keep its vectors in a Serverless collection, so
the agent can follow "why doesn't my knowledge base find X" into the index behind it.

Indexes are read through each domain's or collection's own REST API, signed with the session's credentials. The
read-only guard can't hook those requests, so OpenSearchAnalyzer.request() itself sends only GET, and POST to _search
and _count, refusing anything else before it's sent. check_vectors reads documents' vectors and never sends their
other fields or the vectors themselves to the model: only the counts and lengths it works out. No question is
embedded here, so no Bedrock model is called. Nothing changes an index: findings show the opensearch-py call that
would.

The summarize_* functions are pure: they run on any Overview, StoreReport, IndexInfo, KnnStats or Sample.
OPENSEARCH_TOOL_READS is what each tool reads (its IAM permissions), for tools() and README. The analyzer's types
are imported for the type hints only, since its module loads when the first tool runs (_os_module). OpenSearch is
regional: the toolkit reads the agent's region. A list shows at most _LISTED stores or indexes and _LISTED_FIELDS
fields of an index; check_vectors reads at most _MOST_CHECKED documents.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, human_money, human_size
from ..budget import Budget
from ..results import _with_permissions, clamp_count, hide_secrets, model_findings, share_pct
from .base import Toolkit

if TYPE_CHECKING:
    from ...opensearch import (
        Collection, Domain, IndexInfo, KnnStats, OpenSearchAnalyzer, Overview, Sample, StoreReport, VectorCheck,
        VectorField,
    )

_STORES = ("es:ListDomainNames", "es:DescribeDomains", "aoss:ListCollections", "aoss:BatchGetCollection",
           "aoss:ListSecurityPolicies", "aoss:GetSecurityPolicy")
_REST_GET = "es:ESHttpGet (domains) / aoss:APIAccessAll (collections)"
_REST_POST = "es:ESHttpPost (domains) / aoss:APIAccessAll (collections), to _search and _count only"
OPENSEARCH_TOOL_READS: dict[str, tuple[str, ...]] = {
    "find_vector_stores": (*_STORES, "aoss:GetAccountSettings", "cloudwatch:GetMetricData"),
    "store_indexes": (*_STORES, _REST_GET, _REST_POST),
    "index_details": (*_STORES, _REST_GET, _REST_POST),
    "check_vectors": (*_STORES, _REST_GET, _REST_POST),
}
_LISTED = 60
_LISTED_FIELDS = 40
_DEFAULT_CHECKED = 200
_MOST_CHECKED = 1_000
_SECTION_PERMISSIONS = {"domains": "es:DescribeDomains", "collections": "aoss:BatchGetCollection",
                        "capacity": "aoss:GetAccountSettings", "usage": "cloudwatch:GetMetricData",
                        "network": "aoss:GetSecurityPolicy"}


def _os_module() -> Any:
    """
    aws_analyzer.opensearch, which the tools are built on. Loaded when a tool first needs it, so importing the agent
    loads no analyzer.
    """
    from ... import opensearch

    return opensearch


def _domain_row(domain: Domain, prices: dict[str, float]) -> dict[str, Any]:
    """
    One domain: its engine and version, status, nodes and storage, where it answers, its encryption and access
    control, its estimated monthly cost and how many warnings its findings have.
    """
    os_ = _os_module()
    cost = os_.domain_monthly_cost(domain, prices)
    known = [usd for usd in cost.values() if usd is not None]
    found = os_.domain_findings(domain, prices)
    return {"name": domain.name, "kind": "domain", "engine": f"{domain.engine} {domain.version}".strip(),
            "status": domain.status, "data_nodes": f"{domain.instance_count} × {domain.instance_type}",
            "storage_per_node_gb": domain.volume_gb or None, "network": f"VPC {domain.vpc}" if domain.vpc else "public",
            "encrypted_at_rest": domain.encrypted, "fine_grained_access": domain.fine_grained,
            "estimated_monthly_cost": human_money(sum(known)) + ("+" if len(known) < len(cost) else ""),
            "warnings": sum(level == "warn" for level, _ in found)}


def _collection_row(collection: Collection) -> dict[str, Any]:
    """
    One Serverless collection: its kind, status, network access, standby replicas, and how many warnings.
    """
    found = _os_module().collection_findings(collection)
    return {"name": collection.name, "kind": "collection", "type": collection.kind, "id": collection.id,
            "status": collection.status, "network": collection.network, "standby_replicas": collection.standby,
            "failure": hide_secrets(collection.failure) or None,
            "warnings": sum(level == "warn" for level, _ in found)}


def _serverless_part(overview: Overview, prices: dict[str, float]) -> dict[str, Any] | None:
    """
    The account's Serverless capacity: the OCUs it bills even when idle, its limits, and what it used in the last
    day.
    """
    if not overview.collections:
        return None
    os_ = _os_module()
    minimum = os_.serverless_minimum(overview.collections)
    idle_ocus = sum(ocus for _, ocus, _ in minimum)
    return {"billed_ocus_even_when_idle": idle_ocus,
            "idle_cost_per_month": human_money(os_.ocu_monthly_cost(idle_ocus, prices)),
            "max_indexing_ocus": overview.max_indexing_ocus, "max_search_ocus": overview.max_search_ocus,
            "average_ocus_last_day": overview.ocus}


def summarize_stores(overview: Overview, prices: dict[str, float]) -> dict[str, Any]:
    """
    find_vector_stores()'s answer: every OpenSearch Service domain and Serverless collection in the region (the ones
    with warnings first), the account's Serverless capacity and what it bills when idle, and every finding.
    """
    os_ = _os_module()
    rows = [_domain_row(d, prices) for d in overview.domains] + [_collection_row(c) for c in overview.collections]
    rows.sort(key=lambda r: (-r["warnings"], r["name"].lower()))
    found: list[tuple[str, str]] = []
    for domain in overview.domains:
        found += os_.domain_findings(domain, prices)
    for collection in overview.collections:
        found += os_.collection_findings(collection)
    out: dict[str, Any] = {"region": overview.region, "domains": len(overview.domains),
                           "collections": len(overview.collections), "stores": rows[:_LISTED],
                           "serverless": _serverless_part(overview, prices), "findings": model_findings(found)}
    if len(rows) > _LISTED:
        out["more"] = f"{len(rows) - _LISTED:,} more not listed: narrow with match='prefix-*'"
    if overview.errors:
        out["errors"] = _with_permissions(overview.errors, _SECTION_PERMISSIONS)
    return out


def _vector_row(info: IndexInfo, vf: VectorField) -> dict[str, Any]:
    """
    One vector field in plain words: its dimension, engine and algorithm, similarity and what a score means, how
    it's stored, and how many documents have one.
    """
    os_ = _os_module()
    have, total = info.with_vector.get(vf.path), info.top_docs
    return {"field": vf.path, "summary": os_.describe_vector_field(vf), "dimension": vf.dimension,
            "engine": vf.engine or "default", "algorithm": vf.algorithm, "similarity": os_.similarity_name(vf),
            "score_means": os_.score_meaning(vf), "data_type": vf.data_type, "mode": vf.mode,
            "compression": vf.compression, "nested_in": vf.nested,
            "documents_with_one": have, "documents_with_one_pct": share_pct(have, total) if have is not None else None}


def _store_index_row(info: IndexInfo, store: Any, *, version: str | None, data_nodes: int | None) -> dict[str, Any]:
    """
    One index in a store's list: its health, documents, size, shards and copies, its vector fields, the memory its
    vectors need, and how many warnings its findings have.
    """
    os_ = _os_module()
    memory = os_.index_vector_memory(info)
    found = os_.index_findings(info, store, version=version, data_nodes=data_nodes)
    return {"name": info.name, "health": info.health, "documents": info.documents, "size": human_size(info.size_bytes),
            "shards": info.shards, "copies": info.copies, "vector_fields": [_vector_row(info, vf)["summary"]
                                                                            for vf in info.vectors],
            "documents_with_vector_pct": (share_pct(info.with_vector.get(info.vectors[0].path, 0), info.top_docs)
                                          if info.vectors and info.top_docs else None),
            "estimated_vector_memory": human_size(memory) if memory is not None else None,
            "warnings": sum(level == "warn" for level, _ in found)}


def _store_facts(store: Any) -> tuple[float | None, str | None, int | None]:
    """
    (the cluster's k-NN memory in bytes, its OpenSearch version, its data nodes) for a domain; Nones otherwise.
    """
    os_ = _os_module()
    if isinstance(store, os_.Domain):
        return os_.domain_knn_memory(store), store.version, store.instance_count
    return None, None, None


def _knn_part(knn: KnnStats | None) -> dict[str, Any] | None:
    """
    What the k-NN plugin says about graph memory: how much is used, how full the fullest node is, and whether it
    ran out (circuit breaker, evictions).
    """
    if knn is None:
        return None
    return {"graph_memory_in_use": human_size(knn.memory_bytes), "fullest_node_pct": knn.memory_percent,
            "circuit_breaker_tripped": knn.circuit_breaker, "cache_full": knn.cache_full, "evictions": knn.evictions,
            "graph_loads_from_disk": knn.misses}


def summarize_indexes(report: StoreReport, prices: dict[str, float]) -> dict[str, Any]:
    """
    store_indexes()'s answer: a domain's or collection's indexes (the vector ones first), each with its documents,
    size, vector fields, how many documents have a vector and the memory its vectors need; the cluster's health and
    k-NN memory against what the vectors need; and the findings.
    """
    os_ = _os_module()
    store = report.store
    knn_memory, version, data_nodes = _store_facts(store)
    ordered = sorted(report.indexes, key=lambda i: (not i.vectors, i.name))
    need = sum(os_.index_vector_memory(i) or 0 for i in report.vector_indexes)
    own = []
    if isinstance(store, os_.Domain):
        own = os_.domain_findings(store, prices)
    elif isinstance(store, os_.Collection):
        own = os_.collection_findings(store)
    out: dict[str, Any] = {
        "store": store.label, "url": store.url, "indexes_total": len(report.indexes),
        "vector_indexes": len(report.vector_indexes),
        "indexes": [_store_index_row(i, store, version=version, data_nodes=data_nodes) for i in ordered[:_LISTED]],
        "estimated_vector_memory": human_size(need) if need else None,
        "knn_memory_of_nodes": human_size(knn_memory) if knn_memory else None,
        "cluster_health": report.health.status if report.health else None,
        "unassigned_shards": report.health.unassigned_shards if report.health else None,
        "knn": _knn_part(report.knn), "findings": model_findings(own + os_.store_findings(report, knn_memory)),
    }
    if len(ordered) > _LISTED:
        out["more"] = f"{len(ordered) - _LISTED:,} more indexes not listed"
    if report.errors:
        out["errors"] = dict(report.errors)
    return out


def summarize_index(store: Any, info: IndexInfo, knn: KnnStats | None) -> dict[str, Any]:
    """
    index_details()'s answer: one index's documents, size, shards and health, each vector field in plain words
    (dimension, engine, similarity, what a score means, how many documents have one), the memory its vectors need
    against what the nodes have, the fields a search can filter on, and the findings with what to do.
    """
    os_ = _os_module()
    knn_memory, version, data_nodes = _store_facts(store)
    need = os_.index_vector_memory(info)
    filterable = [f"{name} ({kind})" for name, kind in info.fields.items()
                  if kind not in ("knn_vector", "object", "nested") and name not in info.unindexed]
    found = os_.index_findings(info, store, knn_memory=knn_memory, stats=knn, version=version, data_nodes=data_nodes)
    out: dict[str, Any] = {
        "index": info.name, "store": store.label, "health": info.health, "documents": info.documents,
        "deleted_documents": info.deleted, "size": human_size(info.size_bytes), "shards": info.shards,
        "copies": info.copies, "segments": info.segments, "knn_enabled": info.knn, "created": info.created,
        "text_field": info.text_field, "vector_fields": [_vector_row(info, vf) for vf in info.vectors],
        "estimated_vector_memory": human_size(need) if need is not None else None,
        "knn_memory_of_nodes": human_size(knn_memory) if knn_memory else None,
        "graphs_loaded_now": human_size(knn.by_index[info.name]) if knn and info.name in knn.by_index else None,
        "filterable_fields": filterable[:_LISTED_FIELDS], "aliases": info.aliases or None,
        "findings": model_findings(found + os_.knn_findings(knn)),
    }
    if len(filterable) > _LISTED_FIELDS:
        out["more_fields"] = len(filterable) - _LISTED_FIELDS
    if info.errors:
        out["errors"] = dict(info.errors)
    return out


def _check_row(check: VectorCheck) -> dict[str, Any]:
    """
    What one field's sampled vectors look like: their dimensions, lengths (1 when normalized), zero and repeated
    vectors, broken ones, and documents without one.
    """
    return {"field": check.field, "documents": check.docs, "vectors": check.vectors,
            "dimensions": {str(dim): n for dim, n in check.dims.most_common()},
            "length_min": check.norm_min, "length_max": check.norm_max, "length_mean": check.norm_mean,
            "normalized": check.unit_length, "zero_vectors": check.zeros, "repeated_vectors": check.repeats,
            "broken_vectors": check.bad, "documents_without_one": check.missing}


def summarize_vectors(sample: Sample) -> dict[str, Any]:
    """
    check_vectors()'s answer: how a sample of an index's documents' vectors look, field by field, and the findings
    (wrong dimensions, zero or repeated vectors, unnormalized vectors under a cosine-like similarity). No document's
    fields or vectors are included.
    """
    os_ = _os_module()
    info = sample.info
    found: list[tuple[str, str]] = []
    for name, check in sample.checks.items():
        field = next((vf for vf in info.vectors if vf.path == name), None) if info is not None else None
        found += os_.vector_findings(check, field)
    return {"store": sample.target, "index": sample.index, "documents_read": len(sample.docs),
            "documents_in_index": sample.total, "random_sample": sample.random,
            "fields": [_check_row(c) for c in sample.checks.values()], "findings": model_findings(found),
            "note": "Counted from a sample: no document's fields or vectors are shown."}


class OpenSearchToolkit(Toolkit):
    """
    The agent's tools for OpenSearch vector search, reading through OpenSearchAnalyzer: `analyzer` is one to use
    (else one is made on the agent's session, in its region). Its AWS clients come through _service, which the guard
    hooks; its REST requests to a domain or collection are signed requests the analyzer itself keeps to GET and to
    POSTs to _search and _count.
    """

    name = "opensearch"
    title = "OpenSearch"
    reads = OPENSEARCH_TOOL_READS
    service = "es"
    services = {"ListCollections": "aoss", "BatchGetCollection": "aoss", "ListSecurityPolicies": "aoss",
                "GetSecurityPolicy": "aoss", "GetAccountSettings": "aoss", "GetMetricData": "cloudwatch"}
    view = "OpenSearchView"
    client_factory = "_service"
    privacy = ("OpenSearch: mappings and settings; check_vectors measures vectors without sending documents "
               "or vectors.")
    covers = ("OpenSearch vector search (Service domains and Serverless collections): which domains and collections "
              "exist and what they cost, their indexes and which hold vectors, how many documents have a vector, "
              "whether the vector graphs fit in the nodes' memory, each vector field's engine and similarity in "
              "plain words, and whether the stored vectors look right (dimensions, zero or repeated vectors).")

    def view_class(self) -> type:
        """
        OpenSearchView, whose commands the agent's findings can name.
        """
        return _os_module().OpenSearchView

    def make_analyzer(self, session: Any) -> OpenSearchAnalyzer:
        return _os_module().OpenSearchAnalyzer(session)

    @property
    def analyzer(self) -> OpenSearchAnalyzer:
        """
        The OpenSearchAnalyzer the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        return f"""\
OpenSearch
- Domains and collections are regional: these tools read {self._region_text()}. Name an index like a path,
  'store/index' (store is a domain's or collection's name); a store alone lists its indexes.
- For "what does OpenSearch cost me" or "is it healthy", start with find_vector_stores. store_indexes lists a
  store's indexes, which hold vectors, how many documents have one and whether the graphs fit in memory.
- For "why does vector search return bad results", index_details explains each vector field (engine, similarity,
  what a score means) and check_vectors measures a sample of the stored vectors (wrong dimensions, zero or repeated
  vectors, unnormalized ones under cosine).
- Findings name OpenSearchView calls (indexes(), index_info(), sample(), search()); these tools are yours for the
  first three, and search() is the user's to run. Nothing here changes an index: give the user the call the
  findings show."""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        find_vector_stores, store_indexes, index_details and check_vectors: methods whose type hints and docstrings
        tell the model how to call them, each reading through OpenSearchAnalyzer and returning a compact, JSON-ready
        summary.
        """
        return [self.find_vector_stores, self.store_indexes, self.index_details, self.check_vectors]

    def find_vector_stores(self, match: str = "") -> dict[str, Any]:
        """
        Lists the OpenSearch Service domains and Serverless collections in the region: a domain's engine and
        version, status, nodes, storage, network, encryption and estimated monthly cost; a collection's type
        (vector search or not), status, network access and standby replicas; the OCUs Serverless bills even when
        idle and what it used in the last day; and every finding (the ones with warnings first). match: a name glob
        like 'vectors-*'.
        """
        core = self.analyzer
        overview = core.overview(match=str(match or "").strip() or None)
        out = summarize_stores(overview, core.prices)
        out["_summary"] = (f"{_plural(len(overview.domains), 'domain')} and "
                           f"{_plural(len(overview.collections), 'collection')} in {overview.region}")
        out["_next"] = [(self._view_call("overview", match=str(match or "").strip() or None),
                         "every domain and collection, with cost and warnings")]
        return out

    def store_indexes(self, store: str) -> dict[str, Any]:
        """
        A domain's or collection's indexes, the vector ones first: each one's health, documents, size, shards and
        copies, its vector fields in a few words, the share of documents with a vector and the memory its vectors
        need; with the cluster's health, the k-NN plugin's memory numbers against what the vectors need, and the
        findings. store: a domain's or collection's name (or its endpoint URL).
        """
        core = self.analyzer
        report = core.indexes(str(store or "").split("/")[0].strip())
        out = summarize_indexes(report, core.prices)
        out["region"] = core.region
        out["_summary"] = (f"{len(report.indexes):,} indexes, "
                           f"{len(report.vector_indexes):,} with vectors")
        out["_next"] = [(self._view_call("indexes", report.store.name), "its indexes and the memory they need")]
        return out

    def index_details(self, index: str) -> dict[str, Any]:
        """
        One OpenSearch index in plain words: its documents, size, shards and health, each vector field (dimension,
        engine and algorithm, similarity and what a score means, how it's stored, how many documents have one), the
        memory its vectors need against the nodes' k-NN memory, the fields a search can filter on, and the findings
        with what to do. index: 'store/index', a domain's or collection's name and the index's.
        """
        core = self.analyzer
        target, name = self._location(index)
        store = core.resolve(target)
        info = core.index(store, name)
        knn = self._knn(store, info)
        out = summarize_index(store, info, knn)
        out["region"] = core.region
        warnings = sum(f["level"] == "warn" for f in out["findings"])
        out["_summary"] = (f"{_plural(info.documents or 0, 'document')}, {_plural(len(info.vectors), 'vector field')}"
                           + (f", {_plural(warnings, 'warning')}" if warnings else ""))
        out["_next"] = [(self._view_call("index_info", f"{store.name}/{info.name}"),
                         "the index in plain English, with the k-NN query to copy")]
        return out

    def _knn(self, store: Any, info: IndexInfo) -> KnnStats | None:
        """
        The k-NN plugin's memory numbers for a vector index outside Serverless; None elsewhere or when they can't
        be read (index_details goes on without them).
        """
        os_ = _os_module()
        if not info.vectors or not isinstance(store, os_.Domain):
            return None
        try:
            return self.analyzer.knn_stats(store)
        except os_.OpenSearchError:
            return None

    def check_vectors(self, index: str, documents: int = _DEFAULT_CHECKED) -> dict[str, Any]:
        """
        Measures a random sample of an index's stored vectors, field by field: their dimensions, lengths (1 when
        normalized), zero, repeated and broken vectors, and documents without one, with findings on what would make
        a search return poor results. No document's fields or vectors are returned. index: 'store/index'.
        documents: how many to sample (at most 1,000).
        """
        core = self.analyzer
        target, name = self._location(index)
        sample = core.sample(target, name, n=1, check=clamp_count(documents, _DEFAULT_CHECKED, _MOST_CHECKED))
        out = summarize_vectors(sample)
        out["region"] = core.region
        problems = sum(f["level"] == "warn" for f in out["findings"])
        out["_summary"] = (f"{_plural(len(sample.docs), 'document')} checked"
                           + (f", {_plural(problems, 'problem')}" if problems else ", vectors look right"))
        out["_next"] = [(self._view_call("sample", f"{target}/{name}"), "a few documents with their vectors checked")]
        return out

    def _location(self, index: str) -> tuple[str, str]:
        """
        ('store', 'index') from 'store/index'; ValueError naming what's missing.
        """
        target, name = _os_module().parse_location(str(index or "").strip())
        if not name:
            raise ValueError(f"Name the index as 'store/index', not {index!r}: store_indexes({target!r}) lists "
                             "its indexes.")
        return target, name

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """
        A tool's answer when a domain or collection isn't there: the closest domain names.
        """
        if _error_code(exc) != "ResourceNotFoundException":
            return None
        wanted = str(arguments.get("store") or arguments.get("index") or "").split("/")[0].strip()
        try:
            names = self.analyzer.domain_names()
        except (ClientError, BotoCoreError):
            names = []
        return {"error": f"No domain or collection {wanted!r} in {self.analyzer.region}. They're regional.",
                "close_names": difflib.get_close_matches(wanted, names, n=3, cutoff=0.5),
                "hint": "Use one of close_names, or find_vector_stores to list them."}
