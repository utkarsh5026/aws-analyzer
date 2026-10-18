"""
What's wrong with a domain, collection, index, its vectors or a search, and what to do about it.
"""

from __future__ import annotations

from typing import Any

from ..._kit.fmt import human_money, human_size

from ..prices import INSTANCE_TYPES, OPENSEARCH_PRICES
from ..helpers import GB, values_at, version_at_least
from ..models import (
    Collection, Domain, Endpoint, IndexInfo, KnnStats, SearchResult, StoreReport, VectorCheck, VectorField,
)
from .parsing import _BEDROCK_FIELDS, _DOMAIN_STATES, _SPACE_NAMES
from .vectors import index_vector_memory, knn_memory_limit, vector_count, vector_memory
from .queries import describe_filter


def _ref(store: Any, index: str) -> str:
    """
    'vectors-prod/docs': how commands name an index in a domain, collection or URL.
    """
    if store is None:
        return index
    return f"{store.url if isinstance(store, Endpoint) else store.name}/{index}"


def _is_serverless(store: Any) -> bool:
    return isinstance(store, Collection) or (isinstance(store, Endpoint) and store.service == "aoss")


def _open_to_anyone(domain: Domain) -> bool:
    """
    The domain's access policy allows every principal, with no condition, and the domain isn't in a VPC.
    """
    if domain.vpc or not domain.access_policy:
        return False
    statements = domain.access_policy.get("Statement") or []
    for st in statements if isinstance(statements, list) else [statements]:
        if not isinstance(st, dict) or st.get("Effect") != "Allow" or st.get("Condition"):
            continue
        principal = st.get("Principal")
        aws = principal.get("AWS") if isinstance(principal, dict) else principal
        if aws == "*" or (isinstance(aws, list) and "*" in aws):
            return True
    return False


def domain_findings(domain: Domain, prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """
    Plain-language risks and cost notes for a domain, each with what to do about it -> [(level, message)].
    """
    prices = OPENSEARCH_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    name = domain.name
    if domain.status and domain.status != "Active":
        found.append(("info", f"The domain is {_DOMAIN_STATES.get(domain.status, domain.status)}."))
    if _open_to_anyone(domain) and not domain.fine_grained:
        found.append((
            "warn",
            "Anyone on the internet can send requests to the domain: its access policy allows every principal, it "
            "isn't in a VPC and fine-grained access control is off. Limit the policy to your roles' ARNs: "
            f"aws opensearch update-domain-config --domain-name {name} --access-policies file://policy.json",
        ))
    if domain.engine == "Elasticsearch":
        found.append((
            "info",
            f"The domain runs Elasticsearch {domain.version}, whose k-NN plugin is the old Open Distro one: no "
            "filtering during the search and no vector quantization. OpenSearch 2.x has both; "
            f"aws opensearch get-compatible-versions --domain-name {name} lists the versions it can upgrade to.",
        ))
    elif version_at_least(domain.version, (2, 17)) is False:
        found.append((
            "info",
            f"OpenSearch {domain.version}: on-disk vectors and binary quantization, which cut k-NN memory up to 32x, "
            f"need 2.17 or later. aws opensearch get-compatible-versions --domain-name {name} lists the versions it "
            "can upgrade to.",
        ))
    spec = INSTANCE_TYPES.get(domain.instance_type)
    if spec and domain.instance_type.startswith("t"):
        found.append((
            "info",
            f"Data nodes are {domain.instance_type}, a burstable type with {spec[2]:g} GiB of memory, of which k-NN "
            f"graphs get about {human_size(knn_memory_limit(spec[2]))} per node: fine for trying things, small for "
            "real vector indexes. Memory-optimized types (r6g, r7g) suit vector search.",
        ))
    if domain.instance_count == 1:
        found.append((
            "info",
            "One data node: replica shards have nowhere to go (indexes that ask for replicas stay yellow), and losing "
            "the node takes the domain down. Fine for development; production domains run 2 or more nodes in "
            "different Availability Zones.",
        ))
    if domain.volume_type == "gp2" and domain.volume_gb:
        saving = (prices.get("gp2", 0) - prices.get("gp3", 0)) * domain.volume_gb * domain.instance_count
        found.append((
            "info",
            f"Storage is gp2: gp3 costs {human_money(saving)}/month less for these volumes and has a higher baseline "
            "(3,000 IOPS). Change the EBS volume type to gp3 in the console (Actions, Edit cluster configuration).",
        ))
    if domain.encrypted is False:
        found.append((
            "info",
            "Encryption at rest is off: index files, logs and automated snapshots are stored unencrypted. "
            f"aws opensearch update-domain-config --domain-name {name} --encryption-at-rest-options Enabled=true",
        ))
    if domain.https_only is False:
        found.append((
            "info",
            "The domain also answers plain HTTP, so requests (and any credentials in them) can travel unencrypted. "
            f"aws opensearch update-domain-config --domain-name {name} --domain-endpoint-options EnforceHTTPS=true",
        ))
    if domain.update_available:
        version = f" ({domain.update_version})" if domain.update_version else ""
        found.append((
            "info",
            f"A service software update{version} with fixes and security patches is waiting: "
            f"aws opensearch start-service-software-update --domain-name {name}",
        ))
    missing = [t for t in (domain.instance_type, domain.master_type, domain.warm_type) if t and t not in prices]
    if missing:
        found.append((
            "info",
            f"No list price for {', '.join(sorted(set(missing)))}, so the cost leaves it out: pass "
            f"OpenSearchAnalyzer(prices={{'{missing[0]}': <USD per hour>}}).",
        ))
    if domain.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in domain.errors.items()) + "."))
    return found


def collection_findings(collection: Collection) -> list[tuple[str, str]]:
    """
    Plain-language notes about a Serverless collection, each with what to do about it -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    c = collection
    if c.status == "FAILED":
        found.append((
            "warn",
            f"The collection failed to create{': ' + c.failure if c.failure else ''}. Create it again (and delete "
            "this one) in the console.",
        ))
    elif c.status and c.status != "ACTIVE":
        found.append(("info", f"The collection is {c.status.lower().replace('_', ' ')}."))
    if not c.standby:
        found.append((
            "info",
            "Standby replicas are off (a development setting): there's no copy in a second Availability Zone, so a "
            "zone outage takes the collection offline. It bills half the minimum (1 OCU instead of 2); production "
            "collections need standby replicas, which are chosen when a collection is created.",
        ))
    if c.network == "vpc":
        endpoints = ", ".join(c.vpc_endpoints) or "a VPC endpoint"
        found.append((
            "info",
            f"The collection only answers through {endpoints} (network policy {c.network_policy}): a notebook "
            "outside that VPC can't read its indexes. Run the notebook in the VPC, or allow its VPC endpoint in the "
            "network policy.",
        ))
    elif c.network == "aws services":
        found.append((
            "info",
            f"Only AWS services (such as Bedrock) can reach the collection (network policy {c.network_policy}), so "
            "this notebook can't read its indexes. Allow your VPC endpoint or public access in the network policy "
            "to look inside.",
        ))
    if c.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in c.errors.items()) + "."))
    return found


def index_findings(
    info: IndexInfo,
    store: Any = None,
    *,
    knn_memory: float | None = None,
    stats: KnnStats | None = None,
    version: str | None = None,
    data_nodes: int | None = None,
) -> list[tuple[str, str]]:
    """
    Plain-language risks for one index, each with what to do about it -> [(level, message)].

    knn_memory: the k-NN memory of the whole cluster in bytes (domain_knn_memory), to check the graphs fit;
    stats: the k-NN plugin's own numbers; version: the OpenSearch version, for which fixes are available;
    data_nodes: how many data nodes hold the shards.
    """
    found: list[tuple[str, str]] = []
    name = info.name
    serverless = _is_serverless(store)
    if info.health == "red":
        found.append((
            "warn",
            "Some primary shards aren't assigned (health red): searches miss their documents and writes to them "
            "fail. On a domain, the console's Cluster health tab and GET _cluster/allocation/explain say why.",
        ))
    elif info.health == "yellow":
        found.append((
            "info",
            "Some replica shards aren't assigned (health yellow): there's no spare copy if a node fails. Usually there "
            "are fewer data nodes than copies; add a node, or lower number_of_replicas.",
        ))
    if info.status == "close":
        found.append(("info", "The index is closed: it can't be searched until it's opened again."))
    if info.vectors:
        found += _vector_index_findings(info, store, knn_memory, stats, version, serverless)
        if not serverless and data_nodes and data_nodes > 1 and info.replicas == 0:
            found.append((
                "info",
                "No replicas: if a node fails, the index is unavailable until it's restored from a snapshot. One "
                "replica also spreads searches over two copies, at the cost of twice the vector memory: "
                f"client.indices.put_settings(index={name!r}, body={{'index': {{'number_of_replicas': 1}}}})",
            ))
    if info.shards and info.shards > 1 and info.primary_bytes is not None and info.primary_bytes < info.shards * GB:
        found.append((
            "info",
            f"{info.shards} primary shards for {human_size(info.primary_bytes)}: every search runs on each shard and "
            "merges the results, so an index this size is faster with one shard (aim for 10 to 50 GB a shard). The "
            "shard count is fixed when an index is created: reindex into one with fewer.",
        ))
    elif info.shards and info.primary_bytes and info.primary_bytes / info.shards > 50 * GB:
        found.append((
            "info",
            f"Shards average {human_size(info.primary_bytes / info.shards)}, above the 50 GB OpenSearch suggests: "
            "recovery and rebalancing get slow. Reindex into an index with more primary shards.",
        ))
    copies = (info.shards or 0) * info.copies
    if info.segments and copies and info.segments / copies > 50 and info.vectors:
        found.append((
            "info",
            f"{info.segments / copies:,.0f} segments per shard: a k-NN search walks every segment's graph, so many "
            "small segments slow it down. After bulk loading, merge them during a quiet hour: "
            f"client.indices.forcemerge(index={name!r}, max_num_segments=1)",
        ))
    if info.deleted and info.docs and info.deleted > 0.2 * (info.docs + info.deleted):
        found.append((
            "info",
            f"{info.deleted:,} deleted documents ({info.deleted / (info.docs + info.deleted):.0%}) still take disk "
            "and graph memory until their segments merge. Merging happens on its own as you write; a force merge "
            "(expunge_deletes) clears them at once.",
        ))
    if info.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in info.errors.items()) + "."))
    return found


def _vector_index_findings(
    info: IndexInfo,
    store: Any,
    knn_memory: float | None,
    stats: KnnStats | None,
    version: str | None,
    serverless: bool,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    name = info.name
    if any(f in info.fields for f in _BEDROCK_FIELDS):
        found.append((
            "info",
            "A Bedrock knowledge base writes this index (it has AMAZON_BEDROCK_* fields): change it through the "
            "knowledge base, which rewrites it on every sync. bedrock_kb.py's kbs() shows the knowledge base.",
        ))
    if info.knn is False and not serverless:
        found.append((
            "warn",
            "index.knn is off, so OpenSearch builds no search graphs for this index: a k-NN search either fails or "
            "compares the query with every vector, which gets slow as the index grows. It can only be set when an "
            "index is created: create one with index.knn true and the same mapping, then reindex into it.",
        ))
    for vf in info.vectors:
        if vf.engine == "nmslib":
            found.append((
                "warn",
                f"'{vf.path}' uses the nmslib engine, deprecated since OpenSearch 2.19; 3.0 can't create nmslib "
                "indexes. It also can't filter during the search. Reindex into a field with \"engine\": \"faiss\" "
                "(same space type) to keep upgrades open.",
            ))
        have, total = info.with_vector.get(vf.path), info.top_docs
        if have is not None and total and have < total:
            missing = total - have
            found.append((
                "warn",
                f"{missing:,} of {total:,} documents ({missing / total:.1%}) have no '{vf.path}', so vector search "
                "never returns them. They are usually documents whose embedding failed, or that were indexed before "
                "the field was added: "
                f"sample({_ref(store, name)!r}, where={{{vf.path!r}: ('missing',)}}) shows some, to re-embed.",
            ))
    need = index_vector_memory(info, native=True)
    if need and knn_memory:
        share = need / knn_memory
        used = f" (the k-NN plugin reports {human_size(stats.memory_bytes)} in use now)" if stats else ""
        if share > 1:
            found.append((
                "warn",
                f"The vector graphs don't fit in the k-NN plugin's memory: they need about {human_size(need)} with "
                f"replicas, more than the {human_size(knn_memory)} it can use on these data nodes{used}. Searches "
                "then load graphs from disk, which is slow, and indexing can trip the k-NN circuit breaker. Use "
                "bigger (memory-optimized) instances, more nodes, or compressed vectors (see the next note).",
            ))
        elif share > 0.6:
            found.append((
                "info",
                f"The vector graphs have little room to grow: they need about {human_size(need)} of memory with "
                f"replicas, {share:.0%} of the {human_size(knn_memory)} the k-NN plugin can use on these data "
                f"nodes{used}.",
            ))
    plain = [
        vf for vf in info.vectors
        if vf.engine != "lucene" and vf.compression_factor == 1 and vf.dimension and not vf.model_id
    ]
    if need and need > GB and plain and not serverless:
        vf = plain[0]
        count = vector_count(info, vf) or 0
        now = (vector_memory(vf, count) or 0) * info.copies
        on_disk = VectorField(vf.path, vf.dimension, mode="on_disk", m=vf.m, method=vf.method)
        small = (vector_memory(on_disk, count) or 0) * info.copies
        upgrade = "" if version_at_least(version, (2, 17)) is not False else " (on OpenSearch 2.17 or later)"
        found.append((
            "info",
            f"'{vf.path}' keeps full 32-bit vectors in memory (about {human_size(now)} with replicas). "
            f"\"mode\": \"on_disk\"{upgrade} keeps 32x compressed copies in memory instead (about "
            f"{human_size(small)}) and rescores the best matches from disk, so recall stays close; \"data_type\": "
            "\"byte\" or an fp16 encoder halve it or better. The mapping can't change in place: create a new index "
            "and reindex.",
        ))
    return found


def store_findings(report: StoreReport, knn_memory: float | None = None) -> list[tuple[str, str]]:
    """
    Cluster-wide notes for indexes(): health, the k-NN plugin's memory, and whether all the vector graphs fit in
    `knn_memory` (bytes, all data nodes) -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    health = report.health
    if health and health.status == "red":
        found.append((
            "warn",
            f"Cluster health is red: {health.unassigned_shards:,} shards are unassigned, primaries among them, so "
            "some documents can't be searched and writes to them fail. GET _cluster/allocation/explain (Dev Tools) "
            "says why.",
        ))
    elif health and health.status == "yellow":
        found.append((
            "info",
            f"Cluster health is yellow: {health.unassigned_shards:,} replica shards are unassigned, so there's no "
            "spare copy if a node fails. Usually there are fewer data nodes than copies; add a node or lower "
            "number_of_replicas.",
        ))
    found += knn_findings(report.knn)
    need = sum(index_vector_memory(info, native=True) or 0 for info in report.vector_indexes)
    if need and knn_memory:
        if need > knn_memory:
            found.append((
                "warn",
                f"The vector indexes need about {human_size(need)} of k-NN memory with their replicas, more than the "
                f"{human_size(knn_memory)} these data nodes have (half of what the Java heap leaves). Searches then "
                "load graphs from disk, which is slow, and indexing can trip the circuit breaker. Use bigger "
                "memory-optimized instances, more nodes, or compressed vectors (index_info() shows the savings).",
            ))
        elif need > 0.6 * knn_memory:
            found.append((
                "info",
                f"The vector indexes need about {human_size(need)} of k-NN memory with their replicas, "
                f"{need / knn_memory:.0%} of the {human_size(knn_memory)} these data nodes have: little room to grow.",
            ))
    return found


def knn_findings(stats: KnnStats | None) -> list[tuple[str, str]]:
    """
    What the k-NN plugin's numbers mean for the whole cluster: a tripped circuit breaker, a full graph cache.
    """
    found: list[tuple[str, str]] = []
    if stats is None:
        return found
    if stats.circuit_breaker:
        found.append((
            "warn",
            "The cluster's k-NN circuit breaker has tripped: graph memory hit its limit, so new vector indexing is "
            "refused until memory frees up. Free memory (delete or close unused vector indexes, compress vectors) or "
            "add memory.",
        ))
    elif stats.memory_percent is not None and stats.memory_percent >= 90:
        found.append((
            "warn",
            f"Graph memory on the cluster's fullest node is {stats.memory_percent:.0f}% of its k-NN limit: the next "
            "graphs loaded push older ones out, and indexing can trip the k-NN circuit breaker. Compress vectors, or "
            "add memory.",
        ))
    if stats.evictions or stats.cache_full:
        found.append((
            "info",
            f"The cluster's k-NN cache has filled up and dropped graphs {stats.evictions:,} times: searches on a "
            "dropped graph load it from disk first, which is slow. The graphs need more memory than the nodes have.",
        ))
    return found


def vector_findings(check: VectorCheck, vf: VectorField | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about a sample of vectors (check_vectors), given the field's settings -> [(level, msg)].
    """
    found: list[tuple[str, str]] = []
    path = check.field
    if not check.vectors:
        if check.docs:
            found.append((
                "info",
                f"None of the {check.docs:,} sampled documents has '{path}' in what OpenSearch returns (the mapping "
                "can leave vectors out of _source), so the vectors couldn't be checked.",
            ))
        return found
    if check.bad:
        found.append((
            "warn",
            f"{check.bad:,} of {check.vectors:,} sampled vectors hold NaN, infinity or values that aren't numbers: "
            "the embedding step produced garbage for those documents. Re-embed them.",
        ))
    if len(check.dims) > 1:
        sizes = ", ".join(f"{d:,} ({n:,})" for d, n in check.dims.most_common())
        found.append(("warn", f"Vectors of different lengths in the sample: {sizes}. Re-embed the odd ones."))
    if check.zeros:
        cosine = vf is not None and vf.space == "cosinesimil"
        found.append((
            "warn" if cosine else "info",
            f"{check.zeros:,} sampled vectors are all zeros"
            + (", which cosine similarity can't compare, so those documents never match" if cosine else "")
            + ". They usually come from empty text or an embedding call that failed: re-embed those documents.",
        ))
    if check.repeats:
        share = check.repeats / check.vectors
        found.append((
            "warn" if share > 0.05 else "info",
            f"{check.repeats:,} of {check.vectors:,} sampled vectors ({share:.0%}) repeat another one exactly: the "
            "same text was indexed more than once (overlapping loads, a document added twice). Copies crowd other "
            "results out of the top k; search(like=...) on one of them shows its copies.",
        ))
    spread = (
        check.norm_min is not None and check.norm_max is not None and check.norm_min > 0
        and check.norm_max / check.norm_min > 1.5
    )
    if vf is not None and spread and not check.unit_length:
        lengths = f"{check.norm_min:.2f} to {check.norm_max:.2f}"
        if vf.space == "innerproduct":
            found.append((
                "info",
                f"Vector lengths run from {lengths}, and the index ranks by inner product, which favours long vectors "
                "over close ones. Unless your embedding model is made for inner product, normalize vectors before "
                "indexing, or use \"space_type\": \"cosinesimil\".",
            ))
        elif vf.space == "l2":
            found.append((
                "info",
                f"Vector lengths run from {lengths}, and L2 distance counts length as well as direction. Most "
                "embedding models are meant to be compared by cosine: normalize vectors before indexing, or use "
                "\"space_type\": \"cosinesimil\".",
            ))
    return found


def search_findings(
    result: SearchResult, vf: VectorField, hit_norms: list[float] | None = None
) -> list[tuple[str, str]]:
    """
    Notes on one k-NN search: too few results, repeated texts, a query that doesn't look like the stored vectors.
    """
    found: list[tuple[str, str]] = []
    if result.where and len(result.hits) < result.k:
        late = vf.engine == "nmslib" or vf.nested is not None
        found.append((
            "info",
            f"{len(result.hits)} of {result.k} results: few documents near the query match {describe_filter(result.where)}."
            + (" This index filters after the search, keeping only those of the k nearest that match: a bigger k "
               "finds more." if late else ""),
        ))
    texts: dict[str, int] = {}
    for hit in result.hits:
        text = next(iter(values_at(hit.source, result.text_field)), None) if result.text_field else None
        if isinstance(text, str) and text.strip():
            if text in texts:
                found.append((
                    "info",
                    f"Results {texts[text]} and {hit.rank} have the same text: the document is indexed more than once. "
                    "sample() counts repeated vectors across the index.",
                ))
                break
            texts[text] = hit.rank
    if result.query_norm is not None and hit_norms and vf.space in ("innerproduct", "l2"):
        unit_docs = all(0.99 <= n <= 1.01 for n in hit_norms)
        if unit_docs and not 0.98 <= result.query_norm <= 1.02:
            found.append((
                "warn",
                f"The stored vectors have length 1 but the query's is {result.query_norm:.2f}, and this index ranks by "
                f"{_SPACE_NAMES[vf.space]}, which depends on length: the query probably came from another model or "
                "wasn't normalized. Normalize it (divide by its length), and check it's the model that embedded the "
                "documents.",
            ))
    return found
