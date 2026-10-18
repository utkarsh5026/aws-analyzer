"""
Reading what AWS and OpenSearch return: domains, collections, network access, mappings,
settings, _cat rows and k-NN stats, and the names of similarity spaces and scores.
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any, Iterable

from ..helpers import KB, _bytes, _epoch_ms, _int, parse_engine_version, values_at
from ..models import Collection, Domain, IndexInfo, KnnStats, VectorField


_DOMAIN_STATES = {
    "Creating": "being created",
    "Modifying": "applying a configuration change",
    "UpgradingEngineVersion": "upgrading its OpenSearch version",
    "UpdatingServiceSoftware": "installing a service software update",
    "Isolated": "isolated (AWS suspended it; see the console)",
    "Deleting": "being deleted",
}

_SPACE_NAMES = {
    "cosinesimil": "cosine",
    "innerproduct": "inner product",
    "l2": "L2 (Euclidean)",
    "l1": "L1 (Manhattan)",
    "linf": "L∞ (Chebyshev)",
    "hamming": "Hamming",
}

# space type -> what search() reads back from each score (the score formulas are in score_to_similarity). faiss and
# lucene score L2 by the squared distance; nmslib by the distance itself (similarity_name).
_SIMILARITY_NAMES = {
    "cosinesimil": "Cosine",
    "innerproduct": "Inner product",
    "l2": "Squared L2 distance",
    "l1": "L1 distance",
    "linf": "L∞ distance",
    "hamming": "Hamming distance",
}

_SCORE_MEANINGS = {
    "cosinesimil": "score = (1 + cosine) / 2: 1.0 is the same direction, 0.5 unrelated (cosine 0), 0 the opposite",
    "innerproduct": "score = 1 + inner product when that's positive, else 1 / (1 - inner product): higher is closer, "
    "and for unit-length vectors the inner product is the cosine",
    "l2": "score = 1 / (1 + {distance}): 1.0 is identical, falling toward 0 as vectors get further apart",
    "l1": "score = 1 / (1 + L1 distance): 1.0 is identical, falling toward 0 as vectors get further apart",
    "linf": "score = 1 / (1 + L∞ distance): 1.0 is identical, falling toward 0 as vectors get further apart",
    "hamming": "score = 1 / (1 + differing bits): 1.0 is identical",
}


def similarity_name(vf: VectorField) -> str:
    """
    What search() reads back from a score in this field: 'Cosine', 'Squared L2 distance', ...
    """
    if vf.space == "l2" and vf.engine == "nmslib":
        return "L2 distance"
    return _SIMILARITY_NAMES.get(vf.space, "Similarity")


def score_meaning(vf: VectorField) -> str:
    """
    How OpenSearch turns this field's distance into a score, in words.
    """
    distance = "L2 distance" if vf.engine == "nmslib" else "squared L2 distance"
    return _SCORE_MEANINGS.get(vf.space, "").format(distance=distance)


_BEST = {"Cosine": "Best cosine", "Inner product": "Best inner product", "Squared L2 distance": "Smallest squared L2 "
         "distance", "Hamming distance": "Fewest differing bits"}  # the search card for the nearest result

# Fields a Bedrock knowledge base writes into the OpenSearch index it stores its vectors in.
_BEDROCK_FIELDS = ("AMAZON_BEDROCK_TEXT_CHUNK", "AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA")

# The field holding each document's text, by the names tools give it: Bedrock knowledge bases, LangChain
# (text, page_content), LlamaIndex (content), Haystack (content), and common hand-made ones.
_TEXT_FIELDS = (
    "AMAZON_BEDROCK_TEXT_CHUNK",
    "AMAZON_BEDROCK_TEXT",
    "text",
    "content",
    "page_content",
    "chunk",
    "chunk_text",
    "passage",
    "body",
    "document",
    "contents",
    "description",
)

_TEXT_TYPES = ("text", "match_only_text", "keyword", "wildcard")

# Fields that say where a document came from, shown next to its text.
_SOURCE_FIELDS = (
    "x-amz-bedrock-kb-source-uri",
    "source",
    "source_uri",
    "url",
    "uri",
    "file",
    "file_name",
    "filename",
    "path",
    "title",
    "metadata.source",
    "metadata.file_name",
    "metadata.title",
)


def parse_domain(status: dict[str, Any]) -> Domain:
    """
    A DescribeDomain(s) DomainStatus -> Domain.
    """
    engine, version = parse_engine_version(status.get("EngineVersion"))
    cluster = status.get("ClusterConfig") or {}
    ebs = status.get("EBSOptions") or {}
    endpoints = status.get("Endpoints") or {}
    vpc = (status.get("VPCOptions") or {}).get("VPCId") or ("(VPC)" if "vpc" in endpoints else None)
    if status.get("DomainProcessingStatus"):
        state = status["DomainProcessingStatus"]
    elif status.get("Deleted"):
        state = "Deleting"
    elif status.get("Created") is False:
        state = "Creating"
    elif status.get("UpgradeProcessing"):
        state = "UpgradingEngineVersion"
    elif status.get("Processing"):
        state = "Modifying"
    else:
        state = "Active"
    try:
        policy = json.loads(status["AccessPolicies"]) if status.get("AccessPolicies") else None
    except ValueError:
        policy = None
    software = status.get("ServiceSoftwareOptions") or {}
    security = status.get("AdvancedSecurityOptions") or {}
    zones = cluster.get("ZoneAwarenessConfig", {}).get("AvailabilityZoneCount", 2)
    return Domain(
        name=status.get("DomainName", ""),
        arn=status.get("ARN", ""),
        engine=engine,
        version=version,
        status=state,
        endpoint=status.get("Endpoint") or endpoints.get("vpc") or status.get("EndpointV2")
        or next(iter(endpoints.values()), None),
        vpc=vpc,
        instance_type=cluster.get("InstanceType", ""),
        instance_count=cluster.get("InstanceCount") or 0,
        master_type=cluster.get("DedicatedMasterType") if cluster.get("DedicatedMasterEnabled") else None,
        master_count=(cluster.get("DedicatedMasterCount") or 0) if cluster.get("DedicatedMasterEnabled") else 0,
        warm_type=cluster.get("WarmType") if cluster.get("WarmEnabled") else None,
        warm_count=(cluster.get("WarmCount") or 0) if cluster.get("WarmEnabled") else 0,
        zones=zones if cluster.get("ZoneAwarenessEnabled") else 1,
        standby=bool(cluster.get("MultiAZWithStandbyEnabled")),
        volume_type=(ebs.get("VolumeType") or "gp2") if ebs.get("EBSEnabled") else None,
        volume_gb=(ebs.get("VolumeSize") or 0) if ebs.get("EBSEnabled") else 0,
        iops=ebs.get("Iops"),
        throughput=ebs.get("Throughput"),
        encrypted=(status.get("EncryptionAtRestOptions") or {}).get("Enabled"),
        node_to_node=(status.get("NodeToNodeEncryptionOptions") or {}).get("Enabled"),
        https_only=(status.get("DomainEndpointOptions") or {}).get("EnforceHTTPS"),
        fine_grained=security.get("Enabled"),
        internal_users=security.get("InternalUserDatabaseEnabled"),
        access_policy=policy if isinstance(policy, dict) else None,
        update_available=bool(software.get("UpdateAvailable")),
        update_version=software.get("NewVersion") or "",
    )


def parse_collection(detail: dict[str, Any]) -> Collection:
    """
    A BatchGetCollection collectionDetail (or a ListCollections summary) -> Collection.
    """
    key = detail.get("kmsKeyArn")
    return Collection(
        name=detail.get("name", ""),
        id=detail.get("id", ""),
        arn=detail.get("arn", ""),
        kind=detail.get("type", ""),
        status=detail.get("status", ""),
        endpoint=detail.get("collectionEndpoint"),
        dashboard=detail.get("dashboardEndpoint"),
        standby=detail.get("standbyReplicas", "ENABLED") != "DISABLED",
        kms_key=None if key in (None, "", "auto") else key,
        created=_epoch_ms(detail.get("createdDate")),
        description=detail.get("description", ""),
        failure=detail.get("failureMessage", ""),
        group=detail.get("collectionGroupName"),
    )


def network_access(policies: Iterable[tuple[str, Any]], collection: str) -> tuple[str | None, str | None, list[str]]:
    """
    Which network policy covers a collection, and what it allows -> (access, policy name, VPC endpoint IDs).
    access is 'public' (the internet, still behind IAM and the data access policy), 'vpc' (only through the listed
    VPC endpoints), 'aws services' (only AWS services such as Bedrock) or None when no policy names it.
    policies: (name, policy document) pairs, as GetSecurityPolicy returns them for type='network'.
    """
    found: tuple[str | None, str | None, list[str]] = (None, None, [])
    for name, document in policies:
        rule_sets = document if isinstance(document, list) else [document]
        for rules in rule_sets:
            if not isinstance(rules, dict):
                continue
            covered = any(
                rule.get("ResourceType") == "collection"
                and any(fnmatch.fnmatchcase(f"collection/{collection}", pattern) for pattern in rule.get("Resource", []))
                for rule in rules.get("Rules", [])
                if isinstance(rule, dict)
            )
            if not covered:
                continue
            if rules.get("AllowFromPublic"):
                return "public", name, []
            if rules.get("SourceVPCEs"):
                found = ("vpc", name, list(rules["SourceVPCEs"]))
            elif rules.get("SourceServices") and found[0] is None:
                found = ("aws services", name, [])
    return found


def _vector_field(path: str, spec: dict[str, Any], nested: str | None) -> VectorField:
    method = spec.get("method") or {}
    params = method.get("parameters") or {}
    encoder = params.get("encoder") or {}
    name = encoder.get("name")
    options = encoder.get("parameters") or {}
    if name == "sq":
        lucene = method.get("engine") == "lucene" or "bits" in options
        label: str | None = f"int{options.get('bits', 7)}" if lucene else str(options.get("type", "fp16"))
    else:
        label = name if name and name != "flat" else None
    return VectorField(
        path=path,
        dimension=_int(spec.get("dimension")),
        data_type=spec.get("data_type") or "float",
        engine=method.get("engine"),
        method=method.get("name"),
        space_type=method.get("space_type") or spec.get("space_type"),
        m=_int(params.get("m")),
        ef_construction=_int(params.get("ef_construction")),
        ef_search=_int(params.get("ef_search")),
        encoder=label,
        mode=spec.get("mode"),
        compression=spec.get("compression_level"),
        model_id=spec.get("model_id"),
        nlist=_int(params.get("nlist")),
        nested=nested,
    )


def parse_mapping(mapping: dict[str, Any]) -> tuple[dict[str, str], list[VectorField], list[str]]:
    """
    An index's mapping -> (field path -> type, its vector fields, the fields left out of _source).
    Takes the 'mappings' object, or GET <index>/_mapping's whole answer for one index.
    """
    if len(mapping) == 1:
        (only,) = mapping.values()
        if isinstance(only, dict) and "mappings" in only:
            mapping = only
    mapping = mapping.get("mappings", mapping)
    if "properties" not in mapping and len(mapping) == 1:  # Elasticsearch 6: {'_doc': {'properties': ...}}
        (only,) = mapping.values()
        if isinstance(only, dict) and "properties" in only:
            mapping = only
    fields: dict[str, str] = {}
    vectors: list[VectorField] = []

    def walk(properties: dict[str, Any], prefix: str, nested: str | None) -> None:
        for name, spec in properties.items():
            if not isinstance(spec, dict):
                continue
            path = prefix + name
            kind = spec.get("type") or ("object" if "properties" in spec else "")
            fields[path] = kind
            if kind == "knn_vector":
                vectors.append(_vector_field(path, spec, nested))
            if isinstance(spec.get("properties"), dict):
                walk(spec["properties"], path + ".", path if kind == "nested" else nested)
            for sub, sub_spec in (spec.get("fields") or {}).items():
                if isinstance(sub_spec, dict):
                    fields[f"{path}.{sub}"] = sub_spec.get("type", "")

    walk(mapping.get("properties") or {}, "", None)
    excludes = (mapping.get("_source") or {}).get("excludes") or []
    return fields, vectors, [str(e) for e in excludes]


def unindexed_fields(mapping: dict[str, Any]) -> list[str]:
    """
    Fields a mapping keeps in _source but doesn't index ("index": false): they can't be searched or filtered on.
    """
    found: list[str] = []

    def walk(properties: dict[str, Any], prefix: str) -> None:
        for name, spec in properties.items():
            if isinstance(spec, dict):
                if str(spec.get("index", "")).lower() == "false" or spec.get("enabled") is False:
                    found.append(prefix + name)
                if isinstance(spec.get("properties"), dict):
                    walk(spec["properties"], f"{prefix}{name}.")

    walk(mapping.get("properties") or {}, "")
    return found


def _flat_settings(settings: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """
    {'index': {'knn': 'true'}} -> {'index.knn': 'true'}; flat settings pass through.
    """
    flat: dict[str, Any] = {}
    for key, value in settings.items():
        if isinstance(value, dict):
            flat.update(_flat_settings(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def read_settings(info: IndexInfo, settings: dict[str, Any]) -> None:
    """
    Fill in what an index's settings say: index.knn, ef_search, shards, replicas, refresh interval, creation date.
    settings: the 'settings' object of GET <index>/_settings (flat or nested).
    """
    flat = _flat_settings(settings)
    info.settings = flat
    s = {key.removeprefix("index."): value for key, value in flat.items()}
    info.knn = _true(s.get("knn"))
    info.ef_search = _int(s.get("knn.algo_param.ef_search"))
    info.space_type = s.get("knn.space_type")
    info.refresh_interval = s.get("refresh_interval")
    info.created = _epoch_ms(s.get("creation_date")) or info.created
    if info.shards is None:
        info.shards = _int(s.get("number_of_shards"))
    if info.replicas is None:
        info.replicas = _int(s.get("number_of_replicas"))
    for vf in info.vectors:  # mappings without a method take the index-wide settings (the oldest k-NN style)
        if vf.method is None and vf.model_id is None:
            vf.m = vf.m or _int(s.get("knn.algo_param.m"))
            vf.ef_construction = vf.ef_construction or _int(s.get("knn.algo_param.ef_construction"))
            vf.space_type = vf.space_type or info.space_type
        vf.ef_search = vf.ef_search or info.ef_search


def guess_text_field(fields: dict[str, str]) -> str | None:
    """
    The field that most likely holds each document's text: a name tools give it (Bedrock's
    AMAZON_BEDROCK_TEXT_CHUNK, LangChain's text, ...), else the first text field.
    """
    texts = [path for path, kind in fields.items() if kind in _TEXT_TYPES]
    by_name = {path.lower(): path for path in texts}
    for name in _TEXT_FIELDS:
        if name.lower() in by_name:
            return by_name[name.lower()]
    top = [path for path in texts if "." not in path and fields[path] != "keyword"]
    return (top or [p for p in texts if fields[p] != "keyword"] or [None])[0]


def source_field(doc: dict[str, Any]) -> tuple[str, str]:
    """
    (field, value) saying where a document came from, by the fields tools use for it (Bedrock's
    x-amz-bedrock-kb-source-uri, LangChain's metadata.source, url, file, title, ...); ('', '') when none.
    """
    for name in _SOURCE_FIELDS:
        values = values_at(doc, name)
        if values and values[0] not in (None, "", [], {}):
            return name, str(values[0])
    metadata = doc.get("AMAZON_BEDROCK_METADATA")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            found = parsed.get("source") or parsed.get("x-amz-bedrock-kb-source-uri")
            if found:
                return "AMAZON_BEDROCK_METADATA", str(found)
    return "", ""


def source_of(doc: dict[str, Any]) -> str:
    """
    Where a document came from (see source_field); '' when it doesn't say.
    """
    return source_field(doc)[1]


def index_from_cat(row: dict[str, Any]) -> IndexInfo:
    """
    A row of GET _cat/indices?format=json -> IndexInfo (its counts and sizes).
    """
    return IndexInfo(
        name=str(row.get("index", "")),
        health=row.get("health") or None,
        status=row.get("status") or None,
        docs=_int(row.get("docs.count")),
        deleted=_int(row.get("docs.deleted")),
        size_bytes=_bytes(row.get("store.size")),
        primary_bytes=_bytes(row.get("pri.store.size")),
        shards=_int(row.get("pri")),
        replicas=_int(row.get("rep")),
    )


def parse_knn_stats(resp: dict[str, Any]) -> KnnStats:
    """
    GET _plugins/_knn/stats -> KnnStats. Graph memory is reported in KB per node.
    """
    nodes = resp.get("nodes") or {}
    stats = KnnStats(nodes=len(nodes), circuit_breaker=bool(resp.get("circuit_breaker_triggered")))
    for node in nodes.values():
        stats.memory_bytes += int(float(node.get("graph_memory_usage") or 0) * KB)
        percent = node.get("graph_memory_usage_percentage")
        if percent is not None:
            stats.memory_percent = max(stats.memory_percent or 0.0, float(percent))
        stats.cache_full = stats.cache_full or bool(node.get("cache_capacity_reached"))
        stats.evictions += int(node.get("eviction_count") or 0)
        stats.hits += int(node.get("hit_count") or 0)
        stats.misses += int(node.get("miss_count") or 0)
        for index, usage in (node.get("indices_in_cache") or {}).items():
            used = int(float(usage.get("graph_memory_usage") or 0) * KB)
            stats.by_index[index] = stats.by_index.get(index, 0) + used
    return stats
