"""
The dataclasses OpenSearchAnalyzer returns: domains, collections, indexes and their vector fields,
health, k-NN stats, samples and search results.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require

from .helpers import _int


@dataclass
class Domain:
    """
    An OpenSearch Service domain (a managed cluster), from DescribeDomains. Parts that couldn't be read are in
    `errors` (section -> error code).
    """

    name: str
    arn: str = ""
    engine: str = "OpenSearch"  # or 'Elasticsearch'
    version: str = ""  # '2.17'
    status: str = ""  # 'Active', 'Creating', 'Modifying', 'UpgradingEngineVersion', 'Deleting', ...
    endpoint: str | None = None  # host name; inside the VPC when `vpc` is set
    vpc: str | None = None  # VPC ID when the domain only answers inside a VPC
    instance_type: str = ""  # data nodes
    instance_count: int = 0
    master_type: str | None = None  # dedicated master nodes, when there are any
    master_count: int = 0
    warm_type: str | None = None  # UltraWarm nodes
    warm_count: int = 0
    zones: int = 1  # Availability Zones the data nodes are spread over
    standby: bool = False  # Multi-AZ with standby (one zone's nodes wait as standby)
    volume_type: str | None = None  # 'gp3' | 'gp2' | 'io1' | 'standard'; None = the instances' own disks
    volume_gb: int = 0  # per data node
    iops: int | None = None
    throughput: int | None = None  # MiB/s (gp3)
    encrypted: bool | None = None  # encryption at rest
    node_to_node: bool | None = None
    https_only: bool | None = None
    fine_grained: bool | None = None  # fine-grained access control (users and roles inside OpenSearch)
    internal_users: bool | None = None  # ... with its own user database (user name and password)
    access_policy: dict[str, Any] | None = None  # the domain's resource-based policy
    update_available: bool = False  # a service software update is waiting
    update_version: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str | None:
        return f"https://{self.endpoint}" if self.endpoint else None

    @property
    def label(self) -> str:
        return f"domain {self.name}"


@dataclass
class Collection:
    """
    An OpenSearch Serverless collection, from BatchGetCollection, with its network policy when it could be read.
    """

    name: str
    id: str = ""
    arn: str = ""
    kind: str = ""  # 'VECTORSEARCH' | 'SEARCH' | 'TIMESERIES'
    status: str = ""  # 'ACTIVE', 'CREATING', 'FAILED', ...
    endpoint: str | None = None  # https://<id>.<region>.aoss.amazonaws.com
    dashboard: str | None = None
    standby: bool = True  # standby replicas (redundancy); off for development collections
    kms_key: str | None = None  # None (or 'auto') = an AWS owned key
    created: datetime | None = None
    description: str = ""
    failure: str = ""  # why it FAILED
    group: str | None = None  # collection group
    network: str | None = None  # 'public' | 'vpc' | 'aws services' | None (unknown)
    network_policy: str | None = None
    vpc_endpoints: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str | None:
        return self.endpoint

    @property
    def label(self) -> str:
        return f"collection {self.name}"

    @property
    def vector(self) -> bool:
        return self.kind == "VECTORSEARCH"


@dataclass
class Endpoint:
    """
    Any other OpenSearch, given by its URL: one you run yourself, or a domain or collection by its endpoint.
    Requests to an AWS endpoint are signed with `service` ('es' or 'aoss') in `region`; others aren't.
    """

    url: str
    service: str | None = None
    region: str | None = None

    @property
    def name(self) -> str:
        return self.url

    @property
    def label(self) -> str:
        return self.url


@dataclass
class VectorField:
    """
    One knn_vector field in an index's mapping, with the defaults OpenSearch fills in left as None.
    """

    path: str  # 'embedding', or 'chunks.embedding' inside a nested field
    dimension: int | None = None
    data_type: str = "float"  # 'float' | 'byte' | 'binary'
    engine: str | None = None  # 'faiss' | 'lucene' | 'nmslib'; None = the version's default
    method: str | None = None  # 'hnsw' | 'ivf'; None = hnsw
    space_type: str | None = None  # 'l2' | 'cosinesimil' | 'innerproduct' | 'l1' | 'linf' | 'hamming'; None = l2
    m: int | None = None  # HNSW links per node (16 by default)
    ef_construction: int | None = None  # HNSW candidates while building (100 by default)
    ef_search: int | None = None  # HNSW candidates while searching (100 by default)
    encoder: str | None = None  # quantization inside the method: 'fp16', 'int8', 'pq', ...
    mode: str | None = None  # 'on_disk' | 'in_memory' (OpenSearch 2.17+)
    compression: str | None = None  # '2x' ... '32x' (OpenSearch 2.17+)
    model_id: str | None = None  # a trained model (IVF, PQ) defines the method
    nlist: int | None = None  # IVF lists
    nested: str | None = None  # the nested field it's inside: one vector per nested document

    @property
    def space(self) -> str:
        return self.space_type or "l2"

    @property
    def algorithm(self) -> str:
        return self.method or "hnsw"

    @property
    def links(self) -> int:
        return self.m or 16

    @property
    def compression_factor(self) -> float:
        """
        How much smaller than 32-bit floats each stored vector is: 2 for fp16, 32 for binary or on_disk's default.
        """
        if self.compression:
            return _int(self.compression.rstrip("xX")) or 1
        if self.data_type == "binary":
            return 32
        if self.mode == "on_disk":
            return 32  # on_disk mode compresses 32x unless compression_level says otherwise
        if self.data_type == "byte" or self.encoder in ("int8", "int7", "sq"):
            return 4
        return 2 if self.encoder == "fp16" else 1


@dataclass
class IndexInfo:
    """
    One index: what _cat/indices, its mapping and settings, and the counts and stats say about it. Parts that
    couldn't be read are in `errors` (section -> error).
    """

    name: str
    health: str | None = None  # 'green' | 'yellow' | 'red' (None on Serverless)
    status: str | None = None  # 'open' | 'close'
    docs: int | None = None  # documents (_cat's count, which includes nested documents)
    deleted: int | None = None  # deleted documents not merged away yet
    size_bytes: int | None = None  # primaries and replicas
    primary_bytes: int | None = None
    shards: int | None = None  # primary shards
    replicas: int | None = None  # copies of each primary
    knn: bool | None = None  # the index.knn setting: graphs for approximate search are built
    ef_search: int | None = None  # index.knn.algo_param.ef_search
    space_type: str | None = None  # the old index-wide index.knn.space_type setting
    refresh_interval: str | None = None
    created: datetime | None = None
    fields: dict[str, str] = field(default_factory=dict)  # field path -> mapping type
    vectors: list[VectorField] = field(default_factory=list)
    text_field: str | None = None  # the field that most likely holds each document's text
    top_docs: int | None = None  # top-level documents (_count)
    with_vector: dict[str, int] = field(default_factory=dict)  # vector field -> top-level documents that have one
    segments: int | None = None  # Lucene segments, all shards and copies
    aliases: list[str] = field(default_factory=list)
    source_excludes: list[str] = field(default_factory=list)  # fields left out of _source
    unindexed: list[str] = field(default_factory=list)  # fields the mapping keeps but doesn't index ("index": false)
    mapping: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)  # flat settings
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def copies(self) -> int:
        """
        Primaries plus replicas: each copy builds and loads its own graphs.
        """
        return 1 + (self.replicas or 0)

    @property
    def documents(self) -> int | None:
        return self.top_docs if self.top_docs is not None else self.docs

    def vector(self, name: str | None = None) -> VectorField:
        """
        The vector field `name`, or the only one (the first when there are several).
        """
        if not self.vectors:
            raise ValueError(f"Index {self.name} has no vector (knn_vector) fields")
        if name is None:
            return self.vectors[0]
        for vf in self.vectors:
            if vf.path == name:
                return vf
        listed = ", ".join(v.path for v in self.vectors)
        raise ValueError(f"Index {self.name} has no vector field {name!r} (its vector fields: {listed})")


@dataclass
class ClusterHealth:
    """
    _cluster/health of a domain or self-run cluster.
    """

    status: str = ""  # 'green' | 'yellow' | 'red'
    nodes: int = 0
    data_nodes: int = 0
    active_shards: int = 0
    unassigned_shards: int = 0


@dataclass
class KnnStats:
    """
    What the k-NN plugin reports (_plugins/_knn/stats; not on Serverless): native memory the faiss and nmslib
    graphs use, and whether it ran out.
    """

    nodes: int = 0
    memory_bytes: int = 0  # graph memory on all nodes
    memory_percent: float | None = None  # the fullest node, as a share of its k-NN memory limit
    circuit_breaker: bool = False  # tripped: the graph memory limit was hit
    cache_full: bool = False
    evictions: int = 0  # graphs dropped from memory to make room
    hits: int = 0
    misses: int = 0  # searches that had to load a graph from disk first
    by_index: dict[str, int] = field(default_factory=dict)  # index -> graph memory bytes


@dataclass
class StoreReport:
    """
    indexes(): every index in a domain, collection or cluster, and the cluster's health and k-NN memory.
    """

    store: Domain | Collection | Endpoint
    indexes: list[IndexInfo] = field(default_factory=list)
    health: ClusterHealth | None = None
    knn: KnnStats | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def vector_indexes(self) -> list[IndexInfo]:
        return [i for i in self.indexes if i.vectors]


@dataclass
class VectorCheck:
    """
    How a sample of one field's vectors looks: their dimensions, lengths, zeros and repeats.
    """

    field: str
    docs: int = 0  # documents looked at
    vectors: int = 0  # vectors found in them (several per document in a nested field)
    dims: Counter = field(default_factory=Counter)  # dimension -> vectors
    norm_min: float | None = None
    norm_max: float | None = None
    norm_mean: float | None = None
    zeros: int = 0  # all-zero vectors
    repeats: int = 0  # vectors identical to one seen earlier in the sample
    bad: int = 0  # vectors holding NaN, infinity or something that isn't a number
    missing: int = 0  # documents without the field

    @property
    def unit_length(self) -> bool:
        """
        Every vector has length 1 (to within 1%): normalized, as most embedding models return them.
        """
        return self.norm_min is not None and self.norm_max is not None and 0.99 <= self.norm_min <= self.norm_max <= 1.01


@dataclass
class Sample:
    """
    Documents read from an index (random ones when the endpoint allows), with a check of each vector field.
    """

    target: str
    index: str
    docs: list[dict[str, Any]] = field(default_factory=list)  # '_id' plus the _source fields
    checks: dict[str, VectorCheck] = field(default_factory=dict)  # vector field -> what its vectors look like
    random: bool = True
    total: int | None = None  # documents in the index (the search's count)
    took_ms: int | None = None
    info: IndexInfo | None = field(default=None, repr=False)  # the index's mapping and settings

    def to_df(self, *, vectors: bool = False):
        """
        The documents as a pandas DataFrame (vector fields left out unless vectors=True).
        """
        pd = _require("pandas", "Sample.to_df")
        rows = [{k: v for k, v in doc.items() if vectors or k not in self.checks} for doc in self.docs]
        return pd.DataFrame(rows)


@dataclass
class Embedding:
    """
    A text embedded by a Bedrock model, and what it cost.
    """

    vector: list[float]
    model: str
    tokens: int | None = None
    cost: float | None = None  # USD


@dataclass
class Hit:
    """
    One search result: its rank, OpenSearch's score, and the similarity or distance read back from the score.
    """

    rank: int
    id: str
    score: float
    source: dict[str, Any] = field(default_factory=dict)
    similarity: float | None = None  # cosine, inner product or distance, depending on the space type


@dataclass
class SearchResult:
    """
    The k nearest neighbours of a query vector, from one k-NN search.
    """

    target: str
    index: str
    field: str
    space_type: str
    k: int
    hits: list[Hit] = field(default_factory=list)
    text_field: str | None = None  # the field holding each document's text
    query: dict[str, Any] = field(default_factory=dict)  # the request body sent
    took_ms: int | None = None  # OpenSearch's own time
    seconds: float = 0.0  # the whole call, embedding included
    text: str | None = None
    like: str | None = None
    where: Any = None
    embedding: Embedding | None = None
    query_norm: float | None = None
    hit_norms: list[float] = field(default_factory=list)  # lengths of the results' own vectors
    excluded_self: bool = False  # like=: the document itself was dropped from the results
    info: IndexInfo | None = field(default=None, repr=False)  # the index's mapping and settings

    def to_df(self):
        """
        One row per hit: rank, id, score, similarity and the document's fields.
        """
        pd = _require("pandas", "SearchResult.to_df")
        return pd.DataFrame(
            [{"rank": h.rank, "id": h.id, "score": h.score, "similarity": h.similarity, **h.source} for h in self.hits]
        )


@dataclass
class Overview:
    """
    overview(): every domain and Serverless collection in the region, plus the account's Serverless capacity.
    """

    region: str
    domains: list[Domain] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    max_indexing_ocus: float | None = None  # the account's Serverless capacity limits
    max_search_ocus: float | None = None
    ocus: dict[str, float] | None = None  # CloudWatch: average 'indexing' / 'search' OCUs over `hours`
    hours: int = 24
    errors: dict[str, str] = field(default_factory=dict)
