import io
import json
import math
import random
from types import SimpleNamespace

import boto3
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.validate import ParamValidator
from fake_opensearch import FakeCluster, FakeIndex, score, topic_vector, unit
from moto import mock_aws

from aws_analyzer import opensearch as osmod
from aws_analyzer.opensearch import (
    GB,
    INSTANCE_TYPES,
    OPENSEARCH_PRICES,
    SERVERLESS_MIN_OCUS,
    Collection,
    Domain,
    Endpoint,
    IndexInfo,
    KnnStats,
    OpenSearchAnalyzer,
    OpenSearchError,
    OpenSearchView,
    SearchResult,
    StoreReport,
    VectorCheck,
    VectorField,
    build_filter,
    check_vectors,
    collection_findings,
    describe_filter,
    describe_vector_field,
    domain_findings,
    domain_knn_memory,
    domain_monthly_cost,
    endpoint_service,
    guess_text_field,
    index_findings,
    index_vector_memory,
    knn_findings,
    knn_memory_limit,
    knn_query,
    network_access,
    ocu_monthly_cost,
    parse_collection,
    parse_domain,
    parse_engine_version,
    parse_location,
    parse_mapping,
    query_python,
    read_settings,
    score_to_similarity,
    search_findings,
    serverless_minimum,
    source_of,
    store_findings,
    vector_findings,
    vector_memory,
    vector_summary,
    version_at_least,
)

REGION = "us-east-1"
ACCOUNT = "123456789012"
DIMS = 16
OPEN_POLICY = json.dumps({"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "es:*", "Resource": "*"}]})


def mapping(engine="faiss", space="cosinesimil", dims=DIMS, **extra_fields):
    """A typical vector index: text with a keyword sub-field, a vector, filterable metadata."""
    return {"properties": {
        "text": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "embedding": {"type": "knn_vector", "dimension": dims, "method": {
            "name": "hnsw", "engine": engine, "space_type": space, "parameters": {"m": 16, "ef_construction": 512}}},
        "lang": {"type": "keyword"},
        "year": {"type": "integer"},
        "metadata": {"properties": {"source": {"type": "keyword"}, "page": {"type": "integer"}}},
        **extra_fields,
    }}


def corpus(n=60, *, missing=(5, 9), duplicate=True, seed=1):
    """Documents about four topics: their vectors sit near one axis per topic, so neighbours share a topic."""
    rng = random.Random(seed)
    docs = []
    for i in range(n):
        topic = i % 4
        source = {
            "text": f"Document {i} about {['refunds', 'shipping', 'accounts', 'billing'][topic]}",
            "embedding": topic_vector(topic, DIMS, rng),
            "lang": "en" if i % 3 else "de",
            "year": 2020 + i % 6,
            "metadata": {"source": f"s3://docs/file-{i}.pdf", "page": i % 7},
        }
        if i in missing:
            source.pop("embedding")
        docs.append((f"doc-{i}", source))
    if duplicate:
        docs.append(("doc-copy", json.loads(json.dumps(docs[0][1]))))
    return docs


def cluster(**kwargs):
    return FakeCluster([
        FakeIndex("docs", mapping(), corpus(), settings={"knn": True}, shards=1, replicas=1,
                  size_bytes=50 * 1024**2),
        FakeIndex("logs", {"properties": {"msg": {"type": "text"}}}, [("1", {"msg": "hello"})]),
        FakeIndex(".kibana_1", {"properties": {"x": {"type": "keyword"}}}, []),
    ], knn_memory_kb=3072, **kwargs)


# ---------------------------------------------------------------- fake boto3 clients


class Fake:
    """A boto3 client answering from functions in any order. Every request and response is checked against the
    service model, and calls are recorded. Operations without a handler don't exist on it, like on an old boto3."""

    def __init__(self, service, handlers):
        model = boto3.client(service, region_name=REGION).meta.service_model
        self.meta = SimpleNamespace(region_name=REGION, service_model=model)
        self.ops = {xform_name(op): model.operation_model(op) for op in model.operation_names}
        self.handlers, self.calls = dict(handlers), []

    def _call(self, name, params):
        op = self.ops[name]
        report = ParamValidator().validate(params, op.input_shape)
        assert not report.has_errors(), report.generate_report()
        self.calls.append((name, params))
        resp = self.handlers[name](**params)
        checked = {k: v for k, v in resp.items() if k != "ResponseMetadata"}
        report = ParamValidator().validate(checked, op.output_shape)
        assert not report.has_errors(), report.generate_report()
        return resp

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.handlers:
            raise AttributeError(name)
        return lambda **params: self._call(name, params)

    def get_paginator(self, name):
        return SimpleNamespace(paginate=lambda **params: iter([self._call(name, params)]))

    def called(self, name):
        return [params for op, params in self.calls if op == name]


def denied(operation):
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, operation)


def collection_detail(name="kb-vectors", cid="abc123def456ghi789jk", kind="VECTORSEARCH", standby="ENABLED", **extra):
    return {"id": cid, "name": name, "arn": f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/{cid}", "type": kind,
            "status": "ACTIVE", "standbyReplicas": standby, "kmsKeyArn": "auto", "createdDate": 1717200000000,
            "collectionEndpoint": f"https://{cid}.{REGION}.aoss.amazonaws.com",
            "dashboardEndpoint": f"https://{cid}.{REGION}.aoss.amazonaws.com/_dashboards", **extra}


def serverless(collections=(), policies=None, capacity=(10.0, 10.0), fail=None):
    """A fake opensearchserverless client: collections (collection_detail dicts) and network policies."""
    details = list(collections)
    policies = policies if policies is not None else {
        "public-access": [{"Rules": [{"ResourceType": "collection", "Resource": ["collection/kb-*"]}],
                           "AllowFromPublic": True}],
    }

    def check(op):
        if fail == op:
            raise denied(op)

    def list_collections(**_):
        check("list_collections")
        return {"collectionSummaries": [{k: d[k] for k in ("id", "name", "status", "arn")} for d in details]}

    def batch_get(ids=None, names=None):
        found = [d for d in details if (ids and d["id"] in ids) or (names and d["name"] in names)]
        return {"collectionDetails": found, "collectionErrorDetails": []}

    def list_policies(type, **_):
        return {"securityPolicySummaries": [{"name": n, "type": type} for n in policies]}

    def get_policy(name, type):
        return {"securityPolicyDetail": {"name": name, "type": type, "policy": policies[name]}}

    def settings():
        check("get_account_settings")
        return {"accountSettingsDetail": {"capacityLimits": {"maxIndexingCapacityInOCU": int(capacity[0]),
                                                             "maxSearchCapacityInOCU": int(capacity[1])}}}

    return Fake("opensearchserverless", {
        "list_collections": list_collections, "batch_get_collection": batch_get,
        "list_security_policies": list_policies, "get_security_policy": get_policy,
        "get_account_settings": settings,
    })


def cloudwatch(indexing=1.5, search=2.5):
    def metric_data(MetricDataQueries, **_):
        values = {"IndexingOCU": indexing, "SearchOCU": search}
        return {"MetricDataResults": [
            {"Id": q["Id"], "Values": [values[q["MetricStat"]["Metric"]["MetricName"]]] * 24}
            for q in MetricDataQueries]}
    return Fake("cloudwatch", {"get_metric_data": metric_data})


def sts():
    return Fake("sts", {"get_caller_identity": lambda: {
        "UserId": "AROA:x", "Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/Notebook/x"}})


def bedrock(cohere=False):
    """A fake bedrock-runtime: InvokeModel returns a unit vector pointing at topic 0 ('refunds'), of the size the
    model returns by default unless the request asks for another."""
    def invoke(modelId, body, contentType=None, accept=None):
        request = json.loads(body)
        default = 1536 if "v4" in modelId or modelId.endswith("text-v1") else 1024
        size = request.get("dimensions") or request.get("output_dimension") or default
        vector = topic_vector(0, size, random.Random(11))
        if cohere:
            payload = {"id": "x", "response_type": "embeddings_floats", "texts": request["texts"],
                       "embeddings": {"float": [vector]} if "embedding_types" in request else [vector]}
        else:
            payload = {"embedding": vector, "inputTextTokenCount": 4}
        return {"body": io.BytesIO(json.dumps(payload).encode()), "contentType": "application/json",
                "ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "5"}}}
    return Fake("bedrock-runtime", {"invoke_model": invoke})


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("opensearch", region_name=REGION)


def make_domain(aws, name="vectors-prod", **kwargs):
    options = {
        "EngineVersion": "OpenSearch_2.17",
        "ClusterConfig": {"InstanceType": "r6g.large.search", "InstanceCount": 3},
        "EBSOptions": {"EBSEnabled": True, "VolumeType": "gp3", "VolumeSize": 100},
        "EncryptionAtRestOptions": {"Enabled": True},
        "NodeToNodeEncryptionOptions": {"Enabled": True},
        "DomainEndpointOptions": {"EnforceHTTPS": True},
    }
    options.update(kwargs)
    aws.create_domain(DomainName=name, **options)


@pytest.fixture
def core(aws):
    """One domain (vectors-prod) over a fake cluster, and no Serverless collections."""
    make_domain(aws)
    fake = cluster()
    analyzer = OpenSearchAnalyzer(region=REGION, http=fake, clients={
        "opensearchserverless": serverless(), "cloudwatch": cloudwatch(), "sts": sts(), "bedrock-runtime": bedrock()})
    analyzer.fake = fake
    return analyzer


@pytest.fixture
def ui(core):
    return OpenSearchView(core, mode="text")


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


# ---- helpers


@pytest.mark.parametrize("where, expected", [
    ("vectors-prod/docs", ("vectors-prod", "docs")),
    ("vectors-prod", ("vectors-prod", None)),
    (" vectors-prod/docs/ ", ("vectors-prod", "docs")),
    ("https://search-x.us-east-1.es.amazonaws.com/docs", ("https://search-x.us-east-1.es.amazonaws.com", "docs")),
    ("search-x.us-east-1.es.amazonaws.com/docs/_search", ("https://search-x.us-east-1.es.amazonaws.com", "docs")),
    ("https://abc.us-east-1.aoss.amazonaws.com/_dashboards", ("https://abc.us-east-1.aoss.amazonaws.com", None)),
    ("http://localhost:9200/my-index", ("http://localhost:9200", "my-index")),
    (f"arn:aws:es:{REGION}:{ACCOUNT}:domain/vectors-prod", (f"arn:aws:es:{REGION}:{ACCOUNT}:domain/vectors-prod", None)),
    (f"arn:aws:es:{REGION}:{ACCOUNT}:domain/vectors-prod/index/docs",
     (f"arn:aws:es:{REGION}:{ACCOUNT}:domain/vectors-prod", "docs")),
    (f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/abc123", (f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/abc123", None)),
])
def test_parse_location(where, expected):
    assert parse_location(where) == expected


def test_parse_location_needs_something():
    with pytest.raises(ValueError, match="vectors-prod/docs"):
        parse_location("  ")


@pytest.mark.parametrize("url, expected", [
    ("https://search-x-abc.us-east-1.es.amazonaws.com", ("es", "us-east-1")),
    ("https://vpc-x-abc.eu-west-2.es.amazonaws.com", ("es", "eu-west-2")),
    ("https://abc123.us-west-2.aoss.amazonaws.com", ("aoss", "us-west-2")),
    ("https://search-x-abc.aos.ap-south-1.on.aws", ("es", "ap-south-1")),
    ("http://localhost:9200", (None, None)),
])
def test_endpoint_service(url, expected):
    assert endpoint_service(url) == expected


def test_engine_versions():
    assert parse_engine_version("OpenSearch_2.17") == ("OpenSearch", "2.17")
    assert parse_engine_version("Elasticsearch_7.10") == ("Elasticsearch", "7.10")
    assert version_at_least("2.17", (2, 13)) is True and version_at_least("2.11", (2, 17)) is False
    assert version_at_least("3.1", (2, 17)) is True and version_at_least("", (2, 17)) is None


def test_cat_values():
    assert osmod._int("1,024") == 1024 and osmod._int("") is None and osmod._int("n/a") is None
    assert osmod._bytes("123456") == 123456 and osmod._bytes("1.5kb") == 1536 and osmod._bytes(None) is None


def test_parse_mapping_reads_vector_fields_and_their_settings():
    fields, vectors, excludes = parse_mapping({"docs": {"mappings": {
        "_source": {"excludes": ["embedding"]},
        "properties": {
            **mapping()["properties"],
            "chunks": {"type": "nested", "properties": {
                "vec": {"type": "knn_vector", "dimension": 8, "space_type": "innerproduct", "mode": "on_disk",
                        "compression_level": "16x"}}},
            "quantized": {"type": "knn_vector", "dimension": 8, "method": {
                "name": "hnsw", "engine": "faiss", "parameters": {"encoder": {"name": "sq", "parameters": {
                    "type": "fp16"}}}}},
            "lucene_sq": {"type": "knn_vector", "dimension": 8, "method": {
                "name": "hnsw", "engine": "lucene", "parameters": {"encoder": {"name": "sq"}}}},
        }}}})
    assert fields["text"] == "text" and fields["text.keyword"] == "keyword"
    assert fields["metadata.source"] == "keyword" and fields["chunks"] == "nested"
    assert excludes == ["embedding"]
    by_path = {v.path: v for v in vectors}
    emb = by_path["embedding"]
    assert (emb.dimension, emb.engine, emb.algorithm, emb.space, emb.m, emb.ef_construction) == (
        DIMS, "faiss", "hnsw", "cosinesimil", 16, 512)
    nested = by_path["chunks.vec"]
    assert nested.nested == "chunks" and nested.space == "innerproduct" and nested.compression_factor == 16
    assert by_path["quantized"].encoder == "fp16" and by_path["quantized"].compression_factor == 2
    assert by_path["lucene_sq"].encoder == "int7" and by_path["lucene_sq"].compression_factor == 4
    assert describe_vector_field(emb) == f"{DIMS} dims · faiss HNSW · cosine"
    assert "on disk 16x" in describe_vector_field(nested)


def test_parse_mapping_defaults_and_elasticsearch_6():
    _, vectors, _ = parse_mapping({"_doc": {"properties": {"v": {"type": "knn_vector", "dimension": 4}}}})
    (vf,) = vectors
    assert vf.engine is None and vf.space == "l2" and vf.algorithm == "hnsw" and vf.links == 16
    assert describe_vector_field(vf) == "4 dims · default engine HNSW · L2 (Euclidean)"


def test_read_settings_fills_in_old_index_wide_settings():
    info = IndexInfo("old")
    _, info.vectors, _ = parse_mapping({"properties": {"v": {"type": "knn_vector", "dimension": 4}}})
    read_settings(info, {"index": {"knn": "true", "knn.algo_param.ef_search": "256", "knn.algo_param.m": "48",
                                   "knn.space_type": "cosinesimil", "number_of_shards": "2",
                                   "number_of_replicas": "1", "creation_date": "1717200000000"}})
    assert info.knn is True and info.ef_search == 256 and (info.shards, info.replicas) == (2, 1)
    (vf,) = info.vectors
    assert (vf.m, vf.space, vf.ef_search) == (48, "cosinesimil", 256)
    assert info.created.year == 2024
    other = IndexInfo("x")
    read_settings(other, {"index.number_of_shards": "1"})
    assert other.knn is False  # not set means off


@pytest.mark.parametrize("fields, expected", [
    ({"AMAZON_BEDROCK_TEXT_CHUNK": "text", "AMAZON_BEDROCK_METADATA": "text", "x": "keyword"},
     "AMAZON_BEDROCK_TEXT_CHUNK"),
    ({"title": "text", "text": "text"}, "text"),
    ({"title": "text", "body2": "text"}, "title"),
    ({"tag": "keyword"}, None),
])
def test_guess_text_field(fields, expected):
    assert guess_text_field(fields) == expected


def test_source_of_reads_the_usual_fields():
    assert source_of({"x-amz-bedrock-kb-source-uri": "s3://kb/a.pdf"}) == "s3://kb/a.pdf"
    assert source_of({"metadata": {"source": "https://help/1"}}) == "https://help/1"
    assert source_of({"AMAZON_BEDROCK_METADATA": json.dumps({"source": "s3://kb/b.pdf"})}) == "s3://kb/b.pdf"
    assert source_of({"text": "no source"}) == ""


def test_vector_memory_follows_opensearch_sizing():
    vf = VectorField("v", 1024)
    assert vector_memory(vf, 1_000_000) == int(1.1 * (4 * 1024 + 8 * 16) * 1_000_000)
    assert vector_memory(VectorField("v", 1024, encoder="fp16"), 10) == int(1.1 * (2 * 1024 + 128) * 10)
    assert vector_memory(VectorField("v", 1024, mode="on_disk"), 10) == int(1.1 * (128 + 128) * 10)
    assert vector_memory(VectorField("v", 1024, data_type="binary"), 10) == int(1.1 * (128 + 128) * 10)
    assert vector_memory(VectorField("v", 64, method="ivf", nlist=128), 100) == int(1.1 * (256 * 100 + 4 * 128 * 64))
    assert vector_memory(VectorField("v", 64, model_id="pq-model"), 100) is None
    assert vector_memory(VectorField("v"), 100) is None
    info = IndexInfo("i", docs=100, replicas=1, vectors=[VectorField("a", 8), VectorField("b", 8, engine="lucene")])
    one = vector_memory(VectorField("a", 8), 100)
    assert index_vector_memory(info) == 4 * one and index_vector_memory(info, native=True) == 2 * one
    nested = IndexInfo("n", docs=30, top_docs=10, replicas=0, with_vector={"c.v": 10},
                       vectors=[VectorField("c.v", 8, nested="c")])
    assert osmod.vector_count(nested, nested.vectors[0]) == 20  # the nested documents, not their parents
    assert index_vector_memory(nested) == vector_memory(VectorField("c.v", 8), 20)


def test_knn_memory_limit_and_domain_memory():
    assert knn_memory_limit(16) == 4 * GB  # r6g.large: 8 GiB heap, half of the other 8
    assert knn_memory_limit(128) == 48 * GB  # the heap stops at 32 GiB
    domain = Domain("d", instance_type="r6g.large.search", instance_count=3)
    assert domain_knn_memory(domain) == 12 * GB
    assert domain_knn_memory(Domain("d", instance_type="z9.huge.search", instance_count=3)) is None


def test_domain_monthly_cost():
    domain = Domain("d", instance_type="r6g.large.search", instance_count=3, master_type="m6g.large.search",
                    master_count=3, volume_type="gp3", volume_gb=100)
    cost = domain_monthly_cost(domain)
    assert cost["data nodes"] == pytest.approx(0.167 * 3 * 730)
    assert cost["master nodes"] == pytest.approx(0.128 * 3 * 730)
    assert cost["storage"] == pytest.approx(0.122 * 300)
    assert domain_monthly_cost(Domain("d", instance_type="z9.huge.search", instance_count=1))["data nodes"] is None
    assert domain_monthly_cost(domain, {**OPENSEARCH_PRICES, "r6g.large.search": 1.0})["data nodes"] == 3 * 730


def test_serverless_minimum_counts_each_shared_group_once():
    collections = [
        Collection("a", kind="VECTORSEARCH"), Collection("b", kind="VECTORSEARCH"),
        Collection("c", kind="SEARCH"), Collection("d", kind="VECTORSEARCH", standby=False),
        Collection("e", kind="VECTORSEARCH", kms_key="arn:aws:kms:us-east-1:1:key/k1"),
        Collection("gone", kind="VECTORSEARCH", status="DELETING", kms_key="arn:aws:kms:us-east-1:1:key/k2"),
    ]
    groups = serverless_minimum(collections)
    assert sum(ocus for _, ocus, _ in groups) == 2 + 2 + 1 + 2
    assert ["a", "b"] in [names for _, _, names in groups]
    assert ocu_monthly_cost(2) == pytest.approx(2 * 0.24 * 730) == pytest.approx(350.4)
    assert SERVERLESS_MIN_OCUS == {True: 2.0, False: 1.0}


@pytest.mark.parametrize("space", ["cosinesimil", "innerproduct", "l2", "l1"])
def test_scores_turn_back_into_similarity(space):
    rng = random.Random(4)
    for _ in range(20):
        a, b = [rng.gauss(0, 1) for _ in range(8)], [rng.gauss(0, 1) for _ in range(8)]
        got = score_to_similarity(score(space, a, b), space)
        if space == "cosinesimil":
            expected = sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
        elif space == "innerproduct":
            expected = sum(x * y for x, y in zip(a, b))
        elif space == "l2":
            expected = sum((x - y) ** 2 for x, y in zip(a, b))
        else:
            expected = sum(abs(x - y) for x, y in zip(a, b))
        assert got == pytest.approx(expected, rel=1e-6, abs=1e-9)
    assert score_to_similarity(None, "l2") is None and score_to_similarity(0, "l2") is None


def test_l2_is_squared_except_on_nmslib():
    assert osmod.similarity_name(VectorField("v", 4)) == "Squared L2 distance"
    assert osmod.similarity_name(VectorField("v", 4, engine="nmslib")) == "L2 distance"
    assert "1 / (1 + squared L2 distance)" in osmod.score_meaning(VectorField("v", 4, engine="faiss"))
    assert "1 / (1 + L2 distance)" in osmod.score_meaning(VectorField("v", 4, engine="nmslib"))
    assert osmod.similarity_name(VectorField("v", 4, space_type="cosinesimil")) == "Cosine"


def test_check_vectors():
    good = unit([1, 2, 3, 4])
    check = check_vectors([good, good, [0, 0, 0, 0], [1, float("nan"), 0, 0], "junk", [0.5, 0.5, 0.5, 0.5]], "v")
    assert check.vectors == 6 and check.bad == 2 and check.zeros == 1 and check.repeats == 1
    assert check.dims == {4: 4} and check.norm_min == 0 and not check.unit_length
    assert check_vectors([good, unit([4, 3, 2, 1])]).unit_length
    assert vector_summary(good).endswith("4 dims · length 1.00")


def test_build_filter():
    fields = {"lang": "keyword", "title": "text", "title.keyword": "keyword", "body": "text", "year": "integer",
              "tags": "keyword", "draft": "boolean"}
    query = build_filter({"lang": "en", "title": "Refunds", "body": "late refund", "year": (">=", 2024),
                          "tags": ["a", "b"], "draft": ("!=", True)}, fields)
    assert query == {"bool": {
        "filter": [{"term": {"lang": "en"}}, {"term": {"title.keyword": "Refunds"}},
                   {"match_phrase": {"body": "late refund"}}, {"range": {"year": {"gte": 2024}}},
                   {"terms": {"tags": ["a", "b"]}}],
        "must_not": [{"term": {"draft": True}}]}}
    assert build_filter({"year": ("between", 2020, 2022)})["bool"]["filter"] == [
        {"range": {"year": {"gte": 2020, "lte": 2022}}}]
    assert build_filter({"v": ("missing",)}) == {"bool": {"must_not": [{"exists": {"field": "v"}}]}}
    assert build_filter({"v": ("exists",), "t": ("prefix", "ab"), "b": ("contains", "x")})["bool"]["filter"] == [
        {"exists": {"field": "v"}}, {"prefix": {"t": "ab"}}, {"match": {"b": "x"}}]
    dsl = {"term": {"lang": "en"}}
    assert build_filter(dsl) is dsl and build_filter(None) is None and build_filter({}) is None
    with pytest.raises(ValueError, match="Did you mean 'lang'"):
        build_filter({"langg": "en"}, fields)
    with pytest.raises(ValueError, match="takes 2 values"):
        build_filter({"year": ("between", 2020)})
    with pytest.raises(ValueError, match="takes a dict"):
        build_filter(["lang"])
    assert describe_filter({"lang": "en", "year": (">=", 2024), "v": ("missing",)}) == (
        "lang = 'en' and year >= 2024 and v is missing")


def test_knn_query_shapes():
    vf = VectorField("embedding", 4, engine="faiss")
    flt = {"bool": {"filter": [{"term": {"lang": "en"}}]}}
    body = knn_query(vf, [1, 0, 0, 0], 5, filter=flt, ef_search=200, exclude=["other"])
    assert body == {"size": 5, "query": {"knn": {"embedding": {
        "vector": [1, 0, 0, 0], "k": 5, "filter": flt, "method_parameters": {"ef_search": 200}}}},
        "_source": {"excludes": ["other"]}}
    late = knn_query(VectorField("embedding", 4, engine="nmslib"), [1, 0, 0, 0], 5, filter=flt)
    assert late["query"] == {"bool": {"must": [{"knn": {"embedding": {"vector": [1, 0, 0, 0], "k": 5}}}],
                                      "filter": [{"term": {"lang": "en"}}]}}
    nested = knn_query(VectorField("chunks.vec", 4, nested="chunks"), [1, 0, 0, 0], 3)
    assert nested["query"]["nested"]["path"] == "chunks"
    code = query_python(body, "docs")
    assert "query_vector" in code and "[1, 0, 0, 0]" not in code
    compile(code, "<query>", "exec")


def test_network_access():
    policies = [
        ("vpc-only", [{"Rules": [{"ResourceType": "collection", "Resource": ["collection/private"]}],
                       "SourceVPCEs": ["vpce-123"]}]),
        ("bedrock", {"Rules": [{"ResourceType": "collection", "Resource": ["collection/kb"]}],
                     "SourceServices": ["bedrock.amazonaws.com"]}),
        ("public", [{"Rules": [{"ResourceType": "dashboard", "Resource": ["collection/private"]},
                               {"ResourceType": "collection", "Resource": ["collection/web-*"]}],
                     "AllowFromPublic": True}]),
    ]
    assert network_access(policies, "private") == ("vpc", "vpc-only", ["vpce-123"])
    assert network_access(policies, "kb") == ("aws services", "bedrock", [])
    assert network_access(policies, "web-search") == ("public", "public", [])
    assert network_access(policies, "other") == (None, None, [])


def test_parse_domain_and_collection():
    domain = parse_domain({
        "DomainName": "d", "ARN": "arn", "EngineVersion": "OpenSearch_2.17", "Processing": True,
        "Endpoints": {"vpc": "vpc-d-abc.us-east-1.es.amazonaws.com"}, "VPCOptions": {"VPCId": "vpc-1"},
        "ClusterConfig": {"InstanceType": "r6g.large.search", "InstanceCount": 2, "DedicatedMasterEnabled": True,
                          "DedicatedMasterType": "m6g.large.search", "DedicatedMasterCount": 3,
                          "ZoneAwarenessEnabled": True, "ZoneAwarenessConfig": {"AvailabilityZoneCount": 2}},
        "EBSOptions": {"EBSEnabled": True, "VolumeSize": 20}, "AccessPolicies": OPEN_POLICY,
        "AdvancedSecurityOptions": {"Enabled": True, "InternalUserDatabaseEnabled": True},
        "ServiceSoftwareOptions": {"UpdateAvailable": True, "NewVersion": "R2026"},
    })
    assert (domain.status, domain.version, domain.vpc, domain.volume_type, domain.zones) == (
        "Modifying", "2.17", "vpc-1", "gp2", 2)
    assert domain.url == "https://vpc-d-abc.us-east-1.es.amazonaws.com"
    assert domain.master_count == 3 and domain.fine_grained and domain.update_version == "R2026"
    collection = parse_collection(collection_detail(standby="DISABLED"))
    assert collection.vector and not collection.standby and collection.kms_key is None
    assert collection.created.year == 2024 and collection.url.endswith(".aoss.amazonaws.com")


def test_domain_findings():
    risky = Domain("d", version="2.11", instance_type="t3.small.search", instance_count=1, volume_type="gp2",
                   volume_gb=50, encrypted=False, https_only=False, fine_grained=False, update_available=True,
                   access_policy=json.loads(OPEN_POLICY))
    found = domain_findings(risky)
    text = " ".join(m for _, m in found)
    assert [m for level, m in found if level == "warn"] == [found[0][1]] and "Anyone on the internet" in found[0][1]
    for expected in ("2.17 or later", "burstable", "One data node", "gp3 costs $0.65/month less",
                     "--encryption-at-rest-options Enabled=true", "EnforceHTTPS=true", "start-service-software-update"):
        assert expected in text
    guarded = Domain("d", version="2.17", instance_type="r6g.large.search", instance_count=3, fine_grained=True,
                     access_policy=json.loads(OPEN_POLICY))
    assert domain_findings(guarded) == []
    assert "Elasticsearch 7.10" in domain_findings(Domain("d", engine="Elasticsearch", version="7.10"))[0][1]
    assert "No list price for z9.huge.search" in domain_findings(Domain("d", instance_type="z9.huge.search",
                                                                       instance_count=2))[-1][1]


def test_collection_findings():
    failed = collection_findings(Collection("c", status="FAILED", failure="quota"))
    assert failed[0][0] == "warn" and "quota" in failed[0][1]
    notes = " ".join(m for _, m in collection_findings(Collection(
        "c", status="ACTIVE", standby=False, network="vpc", network_policy="p", vpc_endpoints=["vpce-1"])))
    assert "Standby replicas are off" in notes and "vpce-1" in notes
    assert collection_findings(Collection("c", status="ACTIVE")) == []


def vector_index(**kwargs):
    defaults = {"docs": 1000, "top_docs": 1000, "shards": 1, "replicas": 1, "primary_bytes": GB // 2,
                "health": "green", "knn": True}
    info = IndexInfo("docs", **{**defaults, **kwargs})
    if not info.vectors:
        info.vectors = [VectorField("embedding", 1024, engine="faiss", space_type="cosinesimil")]
    info.with_vector = info.with_vector or {"embedding": 1000}
    return info


def test_index_findings():
    store = Domain("vectors-prod", version="2.17", instance_type="r6g.large.search", instance_count=3)
    assert index_findings(vector_index(), store, knn_memory=12 * GB, data_nodes=3) == []
    found = index_findings(vector_index(knn=False, with_vector={"embedding": 900},
                                        vectors=[VectorField("embedding", 1024, engine="nmslib")]),
                           store, knn_memory=12 * GB)
    warnings = [m for level, m in found if level == "warn"]
    assert len(warnings) == 3
    assert "index.knn is off" in warnings[0] and "nmslib" in warnings[1]
    assert "100 of 1,000 documents (10.0%)" in warnings[2]
    assert "sample('vectors-prod/docs', where={'embedding': ('missing',)})" in warnings[2]
    big = vector_index(docs=5_000_000, top_docs=5_000_000, with_vector={"embedding": 5_000_000})
    found = index_findings(big, store, knn_memory=12 * GB, version="2.17")
    assert "more than the 12.0 GB" in found[0][1] and found[0][0] == "warn"
    assert '"mode": "on_disk"' in found[1][1]
    notes = " ".join(m for _, m in index_findings(
        vector_index(replicas=0, shards=4, health="yellow", segments=400, deleted=600), store, data_nodes=3))
    for expected in ("health yellow", "No replicas", "number_of_replicas': 1", "4 primary shards", "segments per shard",
                     "600 deleted documents"):
        assert expected in notes
    bedrock_kb = vector_index()
    bedrock_kb.fields = {"AMAZON_BEDROCK_TEXT_CHUNK": "text"}
    assert "Bedrock knowledge base" in index_findings(bedrock_kb, store)[0][1]
    assert index_findings(vector_index(health="red"), store)[0][0] == "warn"


def test_knn_and_store_findings():
    assert knn_findings(None) == []
    assert "circuit breaker has tripped" in knn_findings(KnnStats(circuit_breaker=True))[0][1]
    assert "95%" in knn_findings(KnnStats(memory_percent=95.0))[0][1]
    assert "dropped graphs 7 times" in knn_findings(KnnStats(evictions=7))[0][1]
    report = StoreReport(Domain("d"), indexes=[vector_index(docs=5_000_000, top_docs=5_000_000,
                                                            with_vector={"embedding": 5_000_000})])
    assert "more than the 1.0 GB" in store_findings(report, knn_memory=GB)[0][1]


def test_vector_findings():
    cosine = VectorField("v", 4, space_type="cosinesimil")
    found = vector_findings(check_vectors([[0, 0, 0, 0]] + [unit([1, 2, 3, 4])] * 3, "v"), cosine)
    assert found[0][0] == "warn" and "never match" in found[0][1]
    assert "2 of 4 sampled vectors (50%) repeat" in found[1][1] and found[1][0] == "warn"
    spread = check_vectors([[1, 0, 0, 0], [3, 0.1, 0, 0]], "v")
    assert "favours long vectors" in vector_findings(spread, VectorField("v", 4, space_type="innerproduct"))[0][1]
    assert "counts length" in vector_findings(spread, VectorField("v", 4))[0][1]
    empty = VectorCheck("v", docs=10)
    assert "couldn't be checked" in vector_findings(empty)[0][1]
    assert vector_findings(check_vectors([unit([1, 2]), unit([2, 1])], "v"), cosine) == []


def test_search_findings():
    vf = VectorField("v", 4, space_type="innerproduct", engine="nmslib")
    result = SearchResult("t", "i", "v", "innerproduct", 5, text_field="text", where={"lang": "en"}, query_norm=3.0,
                          hits=[osmod.Hit(1, "a", 2.0, {"text": "same"}), osmod.Hit(2, "b", 1.9, {"text": "same"})])
    text = " ".join(m for _, m in search_findings(result, vf, [1.0, 1.0]))
    assert "2 of 5 results" in text and "bigger k finds more" in text
    assert "Results 1 and 2 have the same text" in text and "query's is 3.00" in text


# ---- AWS (moto for domains; fakes for Serverless, CloudWatch, STS and Bedrock) and the REST endpoint


def test_overview_reads_domains_collections_and_serverless_usage(aws):
    make_domain(aws)
    make_domain(aws, "legacy", EngineVersion="Elasticsearch_7.10",
                ClusterConfig={"InstanceType": "t3.small.search", "InstanceCount": 1})
    aoss = serverless([collection_detail(), collection_detail("logs", "zzz999yyy888xxx777ww", kind="TIMESERIES")])
    core = OpenSearchAnalyzer(region=REGION, http=cluster(), clients={
        "opensearchserverless": aoss, "cloudwatch": cloudwatch(), "sts": sts()})
    ov = core.overview()
    assert [d.name for d in ov.domains] == ["legacy", "vectors-prod"]
    assert {c.name: c.network for c in ov.collections} == {"kb-vectors": "public", "logs": None}
    assert (ov.max_indexing_ocus, ov.max_search_ocus) == (10, 10)
    assert ov.ocus == {"indexing": 1.5, "search": 2.5} and ov.errors == {}
    assert core.overview(match="vec*", metrics=False).ocus is None


def test_overview_records_what_it_cannot_read(aws):
    make_domain(aws)
    core = OpenSearchAnalyzer(region=REGION, clients={"opensearchserverless": serverless(fail="list_collections")})
    ov = core.overview()
    assert [d.name for d in ov.domains] == ["vectors-prod"] and ov.errors == {"collections": "AccessDeniedException"}


def test_resolve_finds_domains_collections_and_urls(aws):
    make_domain(aws)
    aoss = serverless([collection_detail()])
    core = OpenSearchAnalyzer(region=REGION, http=cluster(), clients={"opensearchserverless": aoss})
    domain = core.resolve("vectors-prod/docs")
    assert isinstance(domain, Domain) and domain.endpoint == "vectors-prod.us-east-1.es.amazonaws.com"
    assert core.resolve(f"arn:aws:es:{REGION}:{ACCOUNT}:domain/vectors-prod") is domain
    assert core.resolve("https://vectors-prod.us-east-1.es.amazonaws.com/docs") is domain
    collection = core.resolve("kb-vectors")
    assert isinstance(collection, Collection) and collection.network == "public"
    assert core.resolve("abc123def456ghi789jk") is collection
    fresh = OpenSearchAnalyzer(region=REGION, clients={"opensearchserverless": aoss})
    assert fresh.resolve(f"https://abc123def456ghi789jk.{REGION}.aoss.amazonaws.com").name == "kb-vectors"
    assert fresh.resolve("vectors-prod.us-east-1.es.amazonaws.com/docs").label == "domain vectors-prod"
    other = fresh.resolve("https://search-elsewhere-x.eu-west-1.es.amazonaws.com")
    assert isinstance(other, Endpoint) and (other.service, other.region) == ("es", "eu-west-1")
    local = core.resolve("http://localhost:9200/docs")
    assert isinstance(local, Endpoint) and local.service is None
    with pytest.raises(ValueError, match="Did you mean 'vectors-prod'"):
        core.resolve("vector-prod")
    assert core.resolve("Vectors-Prod") is domain  # names are lowercase only, so any case finds them


def test_requests_are_signed_and_read_only(core):
    fake = core.fake
    core.request("vectors-prod", "_cat/indices", params={"format": "json"})
    sent = fake.requests[-1]
    assert sent.method == "GET" and sent.headers["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert f"/{REGION}/es/aws4_request" in sent.headers["Authorization"]
    assert sent.headers["X-Amz-Content-SHA256"] and "X-Amz-Security-Token" in sent.headers
    core.request("vectors-prod", "docs/_count", {"query": {"match_all": {}}})
    assert fake.requests[-1].method == "POST" and fake.requests[-1].body == {"query": {"match_all": {}}}
    before = len(fake.requests)
    for path in ("docs/_doc/1", "docs/_bulk", "_reindex", "docs/_update_by_query", "docs/_delete_by_query"):
        with pytest.raises(ValueError, match="only reads"):
            core.request("vectors-prod", path, {"x": 1})
    assert len(fake.requests) == before  # refused before anything was sent


def test_serverless_requests_use_aoss_and_basic_auth_replaces_signing(aws):
    fake = cluster(serverless=True)
    core = OpenSearchAnalyzer(region=REGION, http=fake, clients={"opensearchserverless": serverless([collection_detail()])})
    core.request("kb-vectors", "_cat/indices")
    assert f"/{REGION}/aoss/aws4_request" in fake.requests[-1].headers["Authorization"]
    basic = OpenSearchAnalyzer(region=REGION, http=fake, auth=("admin", "secret"))
    basic.request("http://localhost:9200", "_cat/indices")
    assert fake.requests[-1].headers["Authorization"] == "Basic YWRtaW46c2VjcmV0"
    OpenSearchAnalyzer(region=REGION, http=fake).request("http://localhost:9200", "_cat/indices")
    assert "Authorization" not in fake.requests[-1].headers


def test_errors_carry_status_and_reason(core):
    with pytest.raises(OpenSearchError) as missing:
        core.index("vectors-prod", "nope")
    assert missing.value.status == 404 and missing.value.kind == "index_not_found_exception"

    def unreachable(*_):
        raise EndpointConnectionError(endpoint_url="https://x")

    core._transport = unreachable
    with pytest.raises(OpenSearchError) as down:
        core.request("vectors-prod", "_cat/indices")
    assert down.value.status is None and down.value.kind == "EndpointConnectionError"


def test_index_reads_mapping_settings_counts_and_segments(core):
    info = core.index("vectors-prod", "docs")
    assert (info.docs, info.top_docs, info.with_vector) == (61, 61, {"embedding": 59})
    assert info.knn is True and (info.shards, info.replicas) == (1, 1) and info.segments == 8
    assert info.text_field == "text" and info.vector().dimension == DIMS
    assert info.size_bytes == 100 * 1024**2 and info.primary_bytes == 50 * 1024**2
    with pytest.raises(ValueError, match="no vector field 'nope'"):
        info.vector("nope")


def test_indexes_on_a_domain(core):
    report = core.indexes("vectors-prod")
    assert [i.name for i in report.indexes] == ["docs", "logs"]  # .kibana_1 is hidden
    assert [i.name for i in report.vector_indexes] == ["docs"]
    assert report.health.status == "green" and report.knn.memory_bytes == 3 * 1024**2
    assert report.indexes[0].with_vector == {"embedding": 59} and report.errors == {}
    assert "_mapping" in [r.path.strip("/") for r in core.fake.requests]  # one call for every mapping
    assert [i.name for i in core.indexes("vectors-prod", hidden=True, details=False).indexes] == [
        ".kibana_1", "docs", "logs"]


def test_indexes_on_serverless_falls_back_to_one_call_per_index(aws):
    fake = cluster(serverless=True)
    core = OpenSearchAnalyzer(region=REGION, http=fake, clients={"opensearchserverless": serverless([collection_detail()])})
    report = core.indexes("kb-vectors")
    assert [i.name for i in report.vector_indexes] == ["docs"]
    docs = report.vector_indexes[0]
    assert docs.health is None and docs.with_vector == {"embedding": 59} and docs.knn is True
    paths = [r.path for r in fake.requests]
    assert "/docs/_mapping" in paths and "/docs/_settings" in paths
    assert not any("_cluster" in p or "_knn" in p or "_stats" in p for p in paths)
    assert report.health is None and report.knn is None


def test_nested_vectors_are_counted_per_document(core):
    rng = random.Random(3)
    docs = [(f"p{i}", {"title": f"Paper {i}", "chunks": [{"text": "a", "vec": topic_vector(i, DIMS, rng)},
                                                         {"text": "b", "vec": topic_vector(i + 1, DIMS, rng)}]})
            for i in range(6)] + [("p-empty", {"title": "No chunks", "chunks": []})]
    core.fake.add(FakeIndex("papers", {"properties": {
        "title": {"type": "text"},
        "chunks": {"type": "nested", "properties": {
            "text": {"type": "text"}, "vec": {"type": "knn_vector", "dimension": DIMS}}}}}, docs))
    info = core.index("vectors-prod", "papers")
    assert info.docs == 19 and info.top_docs == 7 and info.with_vector == {"chunks.vec": 6}
    result = core.search("vectors-prod", "papers", vector=topic_vector(2, DIMS, random.Random(9)), k=3)
    assert result.hits[0].id in ("p1", "p2") and len(result.hits) == 3


def test_sample_checks_vectors(core):
    sample = core.sample("vectors-prod", "docs", 5, check=50)
    assert sample.random and len(sample.docs) == 50 and sample.total == 61
    check = sample.checks["embedding"]
    assert check.docs == 50 and check.vectors + check.missing == 50 and check.unit_length
    assert core.fake.requests[-1].body["query"]["function_score"]["random_score"] == {}
    missing = core.sample("vectors-prod", "docs", where={"embedding": ("missing",)})
    assert sorted(d["_id"] for d in missing.docs) == ["doc-5", "doc-9"]


def test_sample_falls_back_to_the_first_documents(core):
    original = core.fake._search

    def no_random(index, body):
        if "function_score" in json.dumps(body):
            return 400, json.dumps({"error": {"type": "parsing_exception", "reason": "no random_score"}}).encode()
        return original(index, body)

    core.fake._search = no_random
    sample = core.sample("vectors-prod", "docs", 3, check=3)
    assert not sample.random and len(sample.docs) == 3


def test_search_by_vector_document_and_function(core):
    query = topic_vector(0, DIMS, random.Random(5))
    result = core.search("vectors-prod", "docs", vector=query, k=5, where={"lang": "en"})
    assert len(result.hits) == 5 and all(h.source["lang"] == "en" for h in result.hits)
    assert all("refunds" in h.source["text"] for h in result.hits)
    assert [h.rank for h in result.hits] == [1, 2, 3, 4, 5] and "embedding" not in result.hits[0].source
    assert result.hits[0].similarity == pytest.approx(2 * result.hits[0].score - 1)
    assert result.hit_norms and result.query_norm == pytest.approx(1.0)
    like = core.search("vectors-prod", "docs", like="doc-4", k=3)
    assert like.excluded_self and "doc-4" not in [h.id for h in like.hits] and len(like.hits) == 3
    filtered = core.search("vectors-prod", "docs", like="doc-4", k=3, where={"lang": "de"})  # doc-4 is 'en'
    assert not filtered.excluded_self and all(h.source["lang"] == "de" for h in filtered.hits)
    assert like.hits[0].id in ("doc-0", "doc-copy")
    by_function = core.search("vectors-prod", "docs", "refunds", embed=lambda text: [query], k=2)
    assert by_function.text == "refunds" and len(by_function.hits) == 2 and by_function.embedding is None


def test_search_embeds_text_with_bedrock(aws):
    make_domain(aws)
    runtime = bedrock()
    fake = FakeCluster([FakeIndex("kb", mapping(dims=1024), [
        ("a", {"text": "refunds", "embedding": topic_vector(0, 1024, random.Random(1))}),
        ("b", {"text": "shipping", "embedding": topic_vector(1, 1024, random.Random(2))})])])
    core = OpenSearchAnalyzer(region=REGION, http=fake, clients={"bedrock-runtime": runtime})
    result = core.search("vectors-prod", "kb", "how do refunds work?", k=1)
    assert result.hits[0].id == "a" and result.embedding.model == "amazon.titan-embed-text-v2:0"
    assert result.embedding.tokens == 4 and result.embedding.cost == pytest.approx(4 * 0.02 / 1e6)
    (call,) = runtime.called("invoke_model")
    assert json.loads(call["body"]) == {"inputText": "how do refunds work?"}  # 1,024 is Titan V2's default size
    small = core.embed("x", dimension=256)
    assert len(small.vector) == 256 and json.loads(runtime.called("invoke_model")[-1]["body"])["dimensions"] == 256


def test_embed_with_cohere_and_profiles(aws):
    runtime = bedrock(cohere=True)
    core = OpenSearchAnalyzer(region=REGION, clients={"bedrock-runtime": runtime})
    v3 = core.embed("hello", model="cohere")
    assert v3.model == "cohere.embed-english-v3" and len(v3.vector) == 1024 and v3.tokens == 5
    assert json.loads(runtime.called("invoke_model")[-1]["body"]) == {"texts": ["hello"], "input_type": "search_query"}
    v4 = core.embed("hello", model="us.cohere.embed-v4:0", dimension=512)
    assert len(v4.vector) == 512 and v4.cost == pytest.approx(5 * 0.12 / 1e6)
    with pytest.raises(ValueError, match="model='amazon.titan-embed-text-v1' or model='cohere.embed-v4:0'"):
        core.embed("x", dimension=1536)
    with pytest.raises(ValueError, match="embed=your_function"):
        core.embed("x", model="openai.text-embedding-3")


def test_search_checks_its_query(core):
    with pytest.raises(ValueError, match="one query"):
        core.search("vectors-prod", "docs", "text", vector=[1.0] * DIMS)
    with pytest.raises(ValueError, match="has 3 numbers, but 'embedding' holds 16-dimension"):
        core.search("vectors-prod", "docs", vector=[1, 2, 3])
    with pytest.raises(ValueError, match="No document 'nope'"):
        core.search("vectors-prod", "docs", like="nope")
    with pytest.raises(ValueError, match="Did you mean 'lang'"):
        core.search("vectors-prod", "docs", "x", where={"langg": "en"}, embed=lambda t: [1.0] * DIMS)
    with pytest.raises(ValueError, match="Which model embedded these 16-dimension vectors"):
        core.search("vectors-prod", "docs", "refunds")


# ---- UI


def test_ui_overview(aws, capsys):
    make_domain(aws)
    make_domain(aws, "open-dev", EngineVersion="OpenSearch_2.11", AccessPolicies=OPEN_POLICY,
                ClusterConfig={"InstanceType": "t3.small.search", "InstanceCount": 1},
                EBSOptions={"EBSEnabled": True, "VolumeType": "gp2", "VolumeSize": 10})
    aoss = serverless([collection_detail(), collection_detail("dev-vectors", "ddd111eee222fff333gg", standby="DISABLED")])
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=cluster(), clients={
        "opensearchserverless": aoss, "cloudwatch": cloudwatch(), "sts": sts()}), mode="text")
    out = run(capsys, ui.overview)
    for expected in (
        "OpenSearch in us-east-1 (2 domains, 2 collections)",
        "Serverless at the OCUs it used in the last 24h",
        "3 × r6g.large.search", "100 GB gp3 × 3", "12.0 GB",
        "kb-vectors", "vector search", "off (dev)", "public",
        "Used: average over the last 24h (CloudWatch)", "4.0 (1.5 indexing + 2.5 search)",
        "Account limit: the most it scales to", "20 (10 indexing + 10 search)",
        "Serverless bills at least 3 OCUs ($525.60/month)",
        "open-dev  Anyone on the internet",
        "indexes('open-dev')",
    ):
        assert expected in out, expected
    cost = 0.167 * 3 * 730 + 0.122 * 300 + 0.036 * 730 + 0.135 * 10 + 4 * 0.24 * 730
    assert f"Est. cost / month: {osmod.human_money(cost)}" in out


def test_ui_overview_never_bills_serverless_below_its_minimum(aws, capsys):
    aoss = serverless([collection_detail()])
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, clients={
        "opensearchserverless": aoss, "cloudwatch": cloudwatch(0.2, 0.3), "sts": sts()}), mode="text")
    out = run(capsys, ui.overview)
    assert "Est. cost / month: $350.40" in out and "0.5 (0.2 indexing + 0.3 search)" in out


def test_ui_overview_empty_and_denied(aws, capsys):
    core = OpenSearchAnalyzer(region=REGION, clients={"opensearchserverless": serverless()})
    out = run(capsys, OpenSearchView(core, mode="text").overview)
    assert "No OpenSearch domains or Serverless collections in us-east-1" in out
    core = OpenSearchAnalyzer(region=REGION, clients={"opensearchserverless": serverless(fail="list_collections")})
    out = run(capsys, OpenSearchView(core, mode="text").overview)
    assert "Couldn't list the collections (AccessDeniedException; needs aoss:ListCollections)" in out


def test_ui_indexes(ui, capsys):
    out = run(capsys, ui.indexes, "vectors-prod")
    for expected in (
        "Indexes in domain vectors-prod",
        "Vector indexes: 1", "k-NN memory: 12.0 GB", "Graph memory in use: 3.0 MB", "Cluster health: green",
        f"embedding: {DIMS} dims · faiss HNSW · cosine",
        "59 (97%)",
        "2 of 61 documents (3.3%) have no 'embedding'",
        "index_info('vectors-prod/docs')",
    ):
        assert expected in out, expected
    assert ".kibana_1" not in out


def test_ui_indexes_shows_the_domains_own_findings(aws, capsys):
    make_domain(aws, EngineVersion="OpenSearch_2.11")
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=cluster(circuit_breaker=True)), mode="text")
    out = run(capsys, ui.indexes)  # the only domain in the region
    assert "OpenSearch 2.11: on-disk vectors" in out and "circuit breaker has tripped" in out
    ui.core._transport = FakeCluster([])
    out = run(capsys, ui.indexes, "vectors-prod")
    assert "No indexes in domain vectors-prod yet" in out and "OpenSearch 2.11: on-disk vectors" in out


def test_ui_indexes_on_serverless(aws, capsys):
    aoss = serverless([collection_detail(), collection_detail("kb-empty", "eee555fff666ggg777hh")])
    fake = cluster(serverless=True)
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, clients={"opensearchserverless": aoss},
                                           http=lambda m, url, b, h: (fake if "abc123" in url else FakeCluster(
                                               [], serverless=True))(m, url, b, h)), mode="text")
    out = run(capsys, ui.indexes, "kb-vectors")
    assert "Indexes in collection kb-vectors" in out and "managed" in out and "Cluster health" not in out
    assert "shards: primaries" not in out and "59 (97%)" in out
    assert "No indexes in collection kb-empty yet" in run(capsys, ui.indexes, "kb-empty")


def test_unindexed_fields_are_not_offered_as_filters(core, capsys):
    core.fake.add(FakeIndex("kb", {"properties": {
        "vec": {"type": "knn_vector", "dimension": DIMS},
        "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
        "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
        "x-amz-bedrock-kb-source-uri": {"type": "keyword"}}},
        [("1", {"vec": unit([1.0] * DIMS), "AMAZON_BEDROCK_TEXT_CHUNK": "refund policy",
                "AMAZON_BEDROCK_METADATA": json.dumps({"source": "s3://kb/a.pdf"})})]))
    info = core.index("vectors-prod", "kb")
    assert info.unindexed == ["AMAZON_BEDROCK_METADATA"] and info.text_field == "AMAZON_BEDROCK_TEXT_CHUNK"
    with pytest.raises(ValueError, match="Can't filter on 'AMAZON_BEDROCK_METADATA'"):
        core.search("vectors-prod", "kb", vector=[1.0] * DIMS, where={"AMAZON_BEDROCK_METADATA": "x"})
    out = run(capsys, OpenSearchView(core, mode="text").index_info, "vectors-prod/kb")
    assert "where={'x-amz-bedrock-kb-source-uri': '<value>'}" in out
    assert "where={'AMAZON_BEDROCK_METADATA'" not in out and "Bedrock knowledge base" in out


def test_ui_index_info(ui, capsys):
    out = run(capsys, ui.index_info, "vectors-prod/docs")
    for expected in (
        "Index docs",
        "Documents: 61", "With a vector: 59 (97%) (!)", "Shards: 1 primary, 1 replica each",
        f"Dimensions: {DIMS}", "Engine: faiss HNSW", "Similarity: cosine",
        "m=16 · ef_construction=512 · ef_search=100 (default)",
        "score = (1 + cosine) / 2",
        "Each document's text is in 'text'",
        "client.search(index='docs', body=body)",
        "where={'lang': '<value>'}", "where={'text': '<exact value>'}", "where={'year': ('>=', 10)}",
        "sample('vectors-prod/docs')",
        "search('a question your documents answer', index='vectors-prod/docs')",
    ):
        assert expected in out, expected


def test_ui_sample(ui, capsys):
    out = run(capsys, ui.sample, "vectors-prod/docs", 4)
    for expected in (
        "Sample of docs", "random documents", "Documents: 61", "Looked at: 61",
        "Vector length: 1.00 (unit length)", "Repeats: 1",
        "1 of 59 sampled vectors (2%) repeat another one exactly",
        "Text (text)", "Source", "s3://docs/file-", f"{DIMS} dims · length 1.00", "metadata.page",
        "search(like=",
    ):
        assert expected in out, expected
    out = run(capsys, ui.sample, "vectors-prod/docs", where={"embedding": ("missing",)})
    assert "matching embedding is missing" in out and "(none)" in out


def test_ui_search(ui, capsys):
    out = run(capsys, ui.search, like="doc-4", index="vectors-prod/docs", k=3)
    for expected in ("Nearest to document doc-4", "Cosine", "doc-copy", "Results 1 and 2 have the same text",
                     "How to read scores", "search(like='doc-0', index='vectors-prod/docs')"):
        assert expected in out, expected
    out = run(capsys, ui.search, vector=topic_vector(1, DIMS, random.Random(2)), index="vectors-prod/docs", k=3,
              where={"year": (">=", 2024)})
    assert "Nearest to your vector" in out and "where year >= 2024" in out and "shipping" in out


def test_ui_search_with_bedrock_says_which_model(aws, capsys):
    make_domain(aws)
    fake = FakeCluster([FakeIndex("kb", mapping(dims=1024), [
        ("a", {"text": "refunds", "embedding": topic_vector(0, 1024, random.Random(1))})])])
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=fake, clients={"bedrock-runtime": bedrock()}),
                        mode="text")
    out = run(capsys, ui.search, "how do refunds work?", index="vectors-prod/kb")
    assert "Embedded with: Titan Text Embeddings V2" in out and "Embedding cost: <$0.01 (4 tokens)" in out
    assert "picked because 'embedding' has 1,024 dimensions" in out


def test_ui_use_and_defaults(ui, capsys):
    assert "Using index docs in domain vectors-prod" in run(capsys, ui.use, "vectors-prod/docs")
    out = run(capsys, ui.index_info)
    assert "Index docs" in out and "  sample()" in out  # next steps leave out use()'s index
    assert "Sample of docs" in run(capsys, ui.sample, n=2)
    assert "Nearest to document doc-8" in run(capsys, ui.search, like="doc-8")
    ui.use("vectors-prod")
    assert "Index logs" in run(capsys, ui.index_info, "logs")  # a bare name is an index of use()'s domain


def test_ui_picks_the_only_one_or_asks(aws, capsys):
    make_domain(aws)
    fake = cluster()
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=fake), mode="text")
    assert "Index docs" in run(capsys, ui.index_info)  # the only domain, and its only vector index
    assert "Index docs" in run(capsys, ui.index_info, "vectors-prod")
    fake.add(FakeIndex("faq", mapping(), corpus(8, missing=(), duplicate=False)))
    out = run(capsys, ui.index_info)
    assert "Which index? domain vectors-prod has 2 vector indexes: docs, faq" in out
    assert "[!]" not in out  # a question, not an error
    make_domain(aws, "second")
    out = run(capsys, ui.indexes)
    assert "Which domain or collection? There are 2 in us-east-1: second, vectors-prod" in out


def test_ui_explains_access_problems(aws, capsys):
    make_domain(aws, AdvancedSecurityOptions={"Enabled": True, "InternalUserDatabaseEnabled": False})
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=cluster(deny=403)), mode="text")
    out = run(capsys, ui.indexes, "vectors-prod")
    assert "refused the request" in out and "es:ESHttpGet and es:ESHttpPost" in out and "readall_and_monitor" in out
    ui.core._transport = cluster(deny=401)
    assert "auth=('user', 'password')" in run(capsys, ui.indexes, "vectors-prod")
    aoss = serverless([collection_detail()])
    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=cluster(deny=403, serverless=True),
                                           clients={"opensearchserverless": aoss}), mode="text")
    out = run(capsys, ui.indexes, "kb-vectors")
    assert "aoss:APIAccessAll" in out and "index/kb-vectors/*" in out


def test_ui_explains_unreachable_vpc_domains(aws, capsys):
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.0.0/24")["Subnet"]["SubnetId"]
    make_domain(aws, VPCOptions={"SubnetIds": [subnet]})

    def unreachable(*_):
        raise EndpointConnectionError(endpoint_url="https://x")

    ui = OpenSearchView(OpenSearchAnalyzer(region=REGION, http=unreachable), mode="text")
    out = run(capsys, ui.indexes, "vectors-prod")
    assert "Couldn't reach domain vectors-prod" in out and "only answers inside VPC" in out
    assert "Traceback" not in out


def test_ui_search_with_a_filter_doesnt_blame_repeats(ui, capsys):
    out = run(capsys, ui.search, like="doc-4", index="vectors-prod/docs", k=3, where={"lang": "de"})
    assert "wasn't among the nearest" not in out and "Best cosine" in out


def test_ui_names_distances_as_the_engine_scores_them(core, capsys):
    core.fake.add(FakeIndex("old", mapping(engine="nmslib", space="l2"), corpus(8, missing=(), duplicate=False)))
    out = run(capsys, OpenSearchView(core, mode="text").search, like="doc-1", index="vectors-prod/old", k=2)
    assert "Smallest L2 distance" in out and "score = 1 / (1 + L2 distance)" in out and "squared" not in out


def test_resolve_works_where_serverless_cant_be_reached(aws):
    make_domain(aws)

    def unreachable(**_):
        raise EndpointConnectionError(endpoint_url="https://aoss.us-east-1.amazonaws.com")

    aoss = Fake("opensearchserverless", {"batch_get_collection": unreachable, "list_collections": unreachable})
    core = OpenSearchAnalyzer(region=REGION, http=cluster(), clients={"opensearchserverless": aoss})
    with pytest.raises(ValueError, match=r"couldn't check collections: EndpointConnectionError"):
        core.resolve("nope")
    assert OpenSearchView(core, mode="text")._store().name == "vectors-prod"  # the only domain; Serverless skipped


def test_ui_reports_missing_things_as_notes(ui, capsys):
    assert "No index 'doc' in domain vectors-prod. Did you mean 'docs'?" in run(capsys, ui.index_info,
                                                                                 "vectors-prod/doc")
    assert "Did you mean 'vectors-prod'?" in run(capsys, ui.indexes, "vector-prod")
    out = run(capsys, ui.index_info, "vectors-prod/logs")
    assert "has no vector (knn_vector) fields" in out and "indexes('vectors-prod')" in out
    assert "Which model embedded" in run(capsys, ui.search, "refunds", index="vectors-prod/docs")
    with pytest.raises(ValueError, match="mode must be"):
        OpenSearchView(ui.core, mode="nope")


def test_ui_turns_an_unexpected_answer_into_a_note(ui, capsys):
    ui.core.fake.knn_memory_kb = 0
    original = ui.core.fake._knn
    ui.core.fake._knn = lambda: {**original(), "nodes": {"node-0": "not a dict"}}
    out = run(capsys, ui.index_info, "vectors-prod/docs")
    assert "wasn't shaped the way this file expects (AttributeError" in out and "Traceback" not in out


def test_ui_html_renders(ui, capsys):
    shown = []
    ui._show = shown.append
    ui.overview()
    ui.indexes("vectors-prod")
    ui.index_info("vectors-prod/docs")
    ui.sample("vectors-prod/docs")
    ui.search(like="doc-1", index="vectors-prod/docs")
    for blocks in shown:
        page = osmod._render_html(blocks, 50)
        assert page.startswith("<style>") and 'class="osv"' in page and "🔎 OpenSearch" in page


def test_help_lists_every_command(ui, capsys):
    out = run(capsys, ui.help)
    for name in ("overview", "use", "indexes", "index_info", "sample", "search"):
        assert f"{name}(" in out
    assert "Other" not in out
    commands = {name for name, member in vars(OpenSearchView).items() if not name.startswith("_") and callable(member)}
    assert commands == {name for names in OpenSearchView._GROUPS.values() for name in names}
    assert "where=" in run(capsys, ui.help, "search")
    assert "Did you mean 'search'" in run(capsys, ui.help, "serch")


def test_instance_prices_are_complete():
    for name, (price, cpus, memory) in INSTANCE_TYPES.items():
        assert name.endswith(".search") and price > 0 and cpus > 0 and memory > 0
        assert OPENSEARCH_PRICES[name] == price
