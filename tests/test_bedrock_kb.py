from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber

import bedrock_kb as kbmod
from bedrock_kb import (
    BEDROCK_PRICES,
    BedrockKBAnalyzer,
    BedrockKBView,
    DataSourceInfo,
    IngestionJob,
    KBDocument,
    describe_chunking,
    describe_parsing,
    describe_vector_store,
    human_duration,
    kb_findings,
    parse_data_source,
    parse_ingestion_job,
    parse_kb_ref,
    parse_knowledge_base,
    source_name,
    summarize_documents,
    sync_call,
    sync_command,
    sync_findings,
    vector_store_monthly_cost,
)

KB_ID = "KBID123456"
KB2_ID = "KBID654321"
DS_ID = "DSID123456"
DS2_ID = "DSID654321"
ACCOUNT = "123456789012"
KB_ARN = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/{KB_ID}"
NOW = datetime.now(timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


# ----------------------------------------------------------------------------- response builders


def kb_desc(kb_id=KB_ID, name="support-docs", *, status="ACTIVE", store="OPENSEARCH_SERVERLESS", reasons=None):
    storage = {"type": store}
    if store == "OPENSEARCH_SERVERLESS":
        storage["opensearchServerlessConfiguration"] = {
            "collectionArn": f"arn:aws:aoss:us-east-1:{ACCOUNT}:collection/abc123", "vectorIndexName": "kb-index",
            "fieldMapping": {"vectorField": "vec", "textField": "text", "metadataField": "meta"}}
    elif store == "PINECONE":
        storage["pineconeConfiguration"] = {
            "connectionString": "https://docs-abc.svc.pinecone.io", "credentialsSecretArn":
                f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:pc", "namespace": "prod",
            "fieldMapping": {"textField": "text", "metadataField": "meta"}}
    desc = {
        "knowledgeBaseId": kb_id, "name": name, "description": "Answers for the support team",
        "knowledgeBaseArn": f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/{kb_id}",
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/service-role/KBRole",
        "knowledgeBaseConfiguration": {"type": "VECTOR", "vectorKnowledgeBaseConfiguration": {
            "embeddingModelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
            "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {"dimensions": 1024}}}},
        "storageConfiguration": storage, "status": status, "createdAt": ago(days=90), "updatedAt": ago(days=2),
    }
    if reasons:
        desc["failureReasons"] = reasons
    return desc


def ds_desc(ds_id=DS_ID, name="docs-s3", *, chunking=None, parsing=None, policy="DELETE", prefixes=("policies/",),
            kind="S3"):
    if kind == "S3":
        config = {"type": "S3", "s3Configuration": {"bucketArn": "arn:aws:s3:::support-docs-bucket",
                                                     "inclusionPrefixes": list(prefixes)}}
    else:
        config = {"type": "WEB", "webConfiguration": {"sourceConfiguration": {"urlConfiguration": {
            "seedUrls": [{"url": "https://help.example.com/"}]}}}}
    desc = {"knowledgeBaseId": KB_ID, "dataSourceId": ds_id, "name": name, "status": "AVAILABLE",
            "dataSourceConfiguration": config, "dataDeletionPolicy": policy,
            "createdAt": ago(days=90), "updatedAt": ago(days=10)}
    ingestion = {}
    if chunking is not None:
        ingestion["chunkingConfiguration"] = chunking
    if parsing is not None:
        ingestion["parsingConfiguration"] = parsing
    if ingestion:
        desc["vectorIngestionConfiguration"] = ingestion
    return desc


FIXED_20 = {"chunkingStrategy": "FIXED_SIZE",
            "fixedSizeChunkingConfiguration": {"maxTokens": 300, "overlapPercentage": 20}}


def job(job_id="JOB0000001", *, status="COMPLETE", started=None, ds_id=DS_ID, scanned=120, metadata=120, new=3,
        modified=1, deleted=0, failed=0, minutes=4):
    started = started or ago(days=3)
    return {"knowledgeBaseId": KB_ID, "dataSourceId": ds_id, "ingestionJobId": job_id, "status": status,
            "startedAt": started, "updatedAt": started + timedelta(minutes=minutes), "statistics": {
                "numberOfDocumentsScanned": scanned, "numberOfMetadataDocumentsScanned": metadata,
                "numberOfNewDocumentsIndexed": new, "numberOfModifiedDocumentsIndexed": modified,
                "numberOfDocumentsDeleted": deleted, "numberOfDocumentsFailed": failed}}


def doc(key, status="INDEXED", reason=None, ds_id=DS_ID):
    detail = {"knowledgeBaseId": KB_ID, "dataSourceId": ds_id, "status": status, "updatedAt": ago(days=1),
              "identifier": {"dataSourceType": "S3", "s3": {"uri": f"s3://support-docs-bucket/policies/{key}"}}}
    if reason:
        detail["statusReason"] = reason
    return detail


def denied(stub, operation, code="AccessDeniedException"):
    stub.add_client_error(operation, service_error_code=code, service_message="User is not authorized",
                          http_status_code=403 if "Denied" in code else 400)


# ----------------------------------------------------------------------------- helpers


@pytest.mark.parametrize("ref, expected", [
    ("KBID123456", ("id", "KBID123456")),
    ("support-docs", ("name", "support-docs")),
    ("  Support Docs ", ("name", "Support Docs")),
    ("kbid123456", ("name", "kbid123456")),  # IDs are upper case
    (KB_ARN, ("arn", KB_ID)),
])
def test_parse_kb_ref(ref, expected):
    assert parse_kb_ref(ref) == expected


def test_parse_kb_ref_rejects_bad_input():
    with pytest.raises(ValueError, match="kbs\\(\\) lists them"):
        parse_kb_ref("")
    with pytest.raises(ValueError, match="isn't a knowledge base ARN"):
        parse_kb_ref("arn:aws:s3:::bucket")


def test_small_helpers():
    assert source_name("s3://bucket/policies/refund-policy.pdf") == "refund-policy.pdf"
    assert source_name("s3://bucket/a%20b.txt") == "a b.txt"
    assert source_name("https://help.example.com/billing/refunds?x=1#top") == "refunds"
    assert source_name("https://help.example.com/") == "help.example.com"
    assert source_name(None) == ""
    assert (human_duration(45), human_duration(200), human_duration(timedelta(hours=2, minutes=5))) == (
        "45s", "3m 20s", "2h 05m")
    assert sync_command(KB_ID, DS_ID) == (f"aws bedrock-agent start-ingestion-job --knowledge-base-id {KB_ID} "
                                          f"--data-source-id {DS_ID}")
    assert sync_command(KB_ID, DS_ID, "eu-west-1").endswith("--region eu-west-1")
    assert "start_ingestion_job(knowledgeBaseId='KBID123456', dataSourceId='DSID123456')" in sync_call(KB_ID, DS_ID)


def test_parse_knowledge_base():
    info = parse_knowledge_base(kb_desc())
    assert (info.id, info.name, info.status, info.kb_type) == (KB_ID, "support-docs", "ACTIVE", "VECTOR")
    assert info.embedding_model == "amazon.titan-embed-text-v2:0" and info.embedding_dims == 1024
    assert info.vector_store == "OPENSEARCH_SERVERLESS" and info.region == "us-east-1"
    assert describe_vector_store(info.vector_store_detail) == "OpenSearch Serverless collection abc123, index kb-index"
    pinecone = parse_knowledge_base(kb_desc(store="PINECONE"))
    assert describe_vector_store(pinecone.vector_store_detail) == "Pinecone index docs-abc, namespace prod"
    summary = parse_knowledge_base({"knowledgeBaseId": KB_ID, "name": "x", "status": "ACTIVE", "updatedAt": NOW})
    assert summary.name == "x" and summary.vector_store == "" and summary.last_sync is None


def test_parse_data_source():
    ds = parse_data_source(ds_desc(chunking=FIXED_20, policy="RETAIN", prefixes=("policies/", "faq/")))
    assert (ds.id, ds.name, ds.source_type, ds.bucket) == (DS_ID, "docs-s3", "S3", "support-docs-bucket")
    assert ds.prefixes == ["policies/", "faq/"]
    assert ds.location == "s3://support-docs-bucket/policies/, s3://support-docs-bucket/faq/"
    assert ds.deletion_policy == "RETAIN" and ds.chunking == FIXED_20 and ds.parsing == {}
    assert parse_data_source(ds_desc(prefixes=())).location == "s3://support-docs-bucket/"
    web = parse_data_source(ds_desc(kind="WEB"))
    assert web.location == "https://help.example.com/" and web.bucket is None


def test_parse_ingestion_job():
    j = parse_ingestion_job({**job(failed=2, minutes=4), "failureReasons": ["boom"]})
    assert (j.scanned, j.metadata_scanned, j.new, j.modified, j.deleted, j.failed) == (120, 120, 3, 1, 0, 2)
    assert j.duration == timedelta(minutes=4) and not j.ok and j.failure_reasons == ["boom"]
    assert parse_ingestion_job(job()).ok
    running = parse_ingestion_job(job(status="IN_PROGRESS", started=ago(minutes=10)))
    assert running.running and running.duration >= timedelta(minutes=10)


@pytest.mark.parametrize("cfg, expected", [
    (None, "Default: up to about 300 tokens per chunk, split at sentence ends"),
    (FIXED_20, "Fixed size: 300 tokens per chunk, 20% overlap"),
    ({"chunkingStrategy": "HIERARCHICAL", "hierarchicalChunkingConfiguration": {
        "levelConfigurations": [{"maxTokens": 1500}, {"maxTokens": 300}], "overlapTokens": 60}},
     "Hierarchical: 1,500-token parents, 300-token children, 60-token overlap"),
    ({"chunkingStrategy": "SEMANTIC", "semanticChunkingConfiguration": {
        "maxTokens": 300, "bufferSize": 0, "breakpointPercentileThreshold": 95}},
     "Semantic: up to 300 tokens, split where the topic changes"),
    ({"chunkingStrategy": "NONE"}, "None: each file is one chunk"),
])
def test_describe_chunking(cfg, expected):
    assert describe_chunking(cfg).startswith(expected)


def test_describe_parsing():
    assert describe_parsing({}).startswith("Default: the text only")
    model = describe_parsing({"parsingStrategy": "BEDROCK_FOUNDATION_MODEL", "bedrockFoundationModelConfiguration": {
        "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-5",
        "parsingPrompt": {"parsingPromptText": "x"}}})
    assert model.startswith("anthropic.claude-sonnet-5 reads text, tables") and "custom parsing prompt" in model
    assert "per page" in describe_parsing({"parsingStrategy": "BEDROCK_DATA_AUTOMATION"})


def test_summarize_documents():
    docs = [KBDocument(DS_ID, "s3://b/a.pdf", "INDEXED"), KBDocument(DS_ID, "s3://b/b.pdf", "FAILED", "Too big"),
            KBDocument(DS_ID, "s3://b/c.pdf", "FAILED", "Too big"), KBDocument(DS_ID, "s3://b/d.png", "IGNORED",
                                                                                "Unsupported")]
    summary = summarize_documents(docs)
    assert summary.total == 4 and summary.counts == {"FAILED": 2, "INDEXED": 1, "IGNORED": 1}
    assert summary.reasons[0] == ("Too big", 2) and docs[1].name == "b.pdf"


def test_vector_store_monthly_cost():
    info = parse_knowledge_base(kb_desc())
    assert vector_store_monthly_cost(info) == pytest.approx(0.24 * 2 * 730)
    assert vector_store_monthly_cost(info, {**BEDROCK_PRICES, "opensearch_min_ocus": 1}) == pytest.approx(0.24 * 730)
    assert vector_store_monthly_cost(parse_knowledge_base(kb_desc(store="PINECONE"))) is None
    assert kbmod.idle_cost_label(parse_knowledge_base(kb_desc(store="PINECONE"))) == "billed by Pinecone, not estimated"


def healthy_kb(**ds_changes):
    info = parse_knowledge_base(kb_desc(store="PINECONE"))
    ds = parse_data_source(ds_desc(chunking=FIXED_20))
    ds.last_sync = ds.last_success = parse_ingestion_job(job())
    for name, value in ds_changes.items():
        setattr(ds, name, value)
    info.data_sources = [ds]
    return info


def messages(found, level=None):
    return " ".join(m for lv, m in found if level is None or lv == level)


def test_kb_findings_silent_when_healthy():
    assert kb_findings(healthy_kb()) == []


def test_kb_findings_fire_on_their_triggers():
    failed = healthy_kb()
    failed.status, failed.failure_reasons = "FAILED", ["Role can't read the collection"]
    assert "The knowledge base is FAILED: Role can't read the collection" in messages(kb_findings(failed), "warn")

    never = healthy_kb(last_sync=None, last_success=None)
    text = messages(kb_findings(never), "warn")
    assert "never been synced" in text and "nothing from it is searchable until you sync" in text
    assert f"start-ingestion-job --knowledge-base-id {KB_ID} --data-source-id {DS_ID} --region us-east-1" in text

    bad_sync = parse_ingestion_job({**job(status="FAILED"), "failureReasons": ["S3 access denied"]})
    text = messages(kb_findings(healthy_kb(last_sync=bad_sync)), "warn")
    assert "The last sync of the data source 'docs-s3' failed" in text and "S3 access denied" in text

    some_failed = parse_ingestion_job(job(failed=3))
    assert "3 documents that failed to index" in messages(kb_findings(healthy_kb(last_sync=some_failed)))
    assert "documents(status='FAILED')" in messages(kb_findings(healthy_kb(last_sync=some_failed)))

    no_metadata = parse_ingestion_job(job(metadata=0))
    assert "metadata.json" in messages(kb_findings(healthy_kb(last_sync=no_metadata)), "info")

    assert "chunks stay in the vector store" in messages(kb_findings(healthy_kb(deletion_policy="RETAIN")))
    none = messages(kb_findings(healthy_kb(chunking={"chunkingStrategy": "NONE"})), "warn")
    assert "each file is one chunk" in none and "input limit" in none
    no_overlap = {"chunkingStrategy": "FIXED_SIZE",
                  "fixedSizeChunkingConfiguration": {"maxTokens": 300, "overlapPercentage": 0}}
    assert "0% overlap" in messages(kb_findings(healthy_kb(chunking=no_overlap)))

    oss = healthy_kb()
    oss.vector_store = "OPENSEARCH_SERVERLESS"
    assert "about $350.40/month even when idle (2 OCUs minimum" in messages(kb_findings(oss), "info")

    unreadable = healthy_kb(errors={"ingestion": "AccessDeniedException"}, last_sync=None)
    unreadable.errors["ingestion"] = "AccessDeniedException"
    text = messages(kb_findings(unreadable))
    assert "never been synced" not in text and "needs bedrock:ListIngestionJobs" in text

    docs = summarize_documents([KBDocument(DS_ID, "s3://b/x.pdf", "FAILED", "Encrypted PDF")])
    assert "The most common reason: Encrypted PDF" in messages(kb_findings(healthy_kb(), docs), "warn")


def test_sync_findings():
    assert sync_findings([parse_ingestion_job(job())]) == []
    failing = [parse_ingestion_job({**job(f"J{i}", status="FAILED", started=ago(days=i)),
                                    "failureReasons": ["Role can't write to the collection"]}) for i in range(1, 4)]
    failing.append(parse_ingestion_job(job("J9", started=ago(days=9))))
    text = messages(sync_findings(failing, {DS_ID: "docs-s3"}), "warn")
    assert "The last 3 syncs of the data source 'docs-s3' failed" in text
    assert "Role can't write to the collection (3x)" in text and "won't help until the cause is fixed" in text
    bad_docs = [parse_ingestion_job(job(f"J{i}", started=ago(days=i), failed=2)) for i in range(1, 3)]
    text = messages(sync_findings(bad_docs))
    assert "couldn't index 2 documents" in text and "Each of the last 2 syncs had failed documents" in text
    stuck = [parse_ingestion_job(job(status="IN_PROGRESS", started=ago(hours=20)))]
    assert "has been running for 20h" in messages(sync_findings(stuck))


# ----------------------------------------------------------------------------- AWS (Stubber / moto)


class Stubs:
    """Real boto3 clients with a botocore Stubber on each: every call must be queued, and its parameters are
    checked against the service model."""

    def __init__(self):
        names = ("bedrock-agent", "bedrock-agent-runtime", "bedrock-runtime", "bedrock")
        self.clients = {name: boto3.client(name, region_name="us-east-1") for name in names}
        self.stubs = {name: Stubber(client) for name, client in self.clients.items()}
        self.agent, self.runtime = self.stubs["bedrock-agent"], self.stubs["bedrock-agent-runtime"]
        self.llm, self.bedrock = self.stubs["bedrock-runtime"], self.stubs["bedrock"]
        for stub in self.stubs.values():
            stub.activate()

    def analyzer(self, **kwargs):
        core = BedrockKBAnalyzer(client=self.clients["bedrock-agent"], clients=dict(self.clients), **kwargs)
        core.max_workers = 1  # a Stubber answers in order, so describe knowledge bases one at a time
        return core

    def done(self):
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()
            stub.deactivate()

    # --- bedrock-agent

    def list_kbs(self, *kbs):
        self.agent.add_response("list_knowledge_bases", {"knowledgeBaseSummaries": [
            {"knowledgeBaseId": kb_id, "name": name, "status": "ACTIVE", "updatedAt": ago(days=1)}
            for kb_id, name in (kbs or [(KB_ID, "support-docs")])]}, {})

    def describe(self, desc=None, sources=None, *, tags=None, reasons=None):
        """Queue what describe() reads: the knowledge base, its data sources, each one's settings and recent syncs
        (and why the newest failed, when it did), then the tags."""
        desc = desc or kb_desc()
        kb_id = desc["knowledgeBaseId"]
        self.agent.add_response("get_knowledge_base", {"knowledgeBase": desc}, {"knowledgeBaseId": kb_id})
        sources = [(ds_desc(chunking=FIXED_20), [job()])] if sources is None else sources
        self.agent.add_response("list_data_sources", {"dataSourceSummaries": [
            {"knowledgeBaseId": kb_id, "dataSourceId": d["dataSourceId"], "name": d["name"], "status": d["status"],
             "updatedAt": d["updatedAt"]} for d, _ in sources]}, {"knowledgeBaseId": kb_id})
        for d, jobs in sources:
            self.agent.add_response("get_data_source", {"dataSource": d},
                                    {"knowledgeBaseId": kb_id, "dataSourceId": d["dataSourceId"]})
            if jobs is None:
                denied(self.agent, "list_ingestion_jobs")
                continue
            self.agent.add_response("list_ingestion_jobs", {"ingestionJobSummaries": jobs}, {
                "knowledgeBaseId": kb_id, "dataSourceId": d["dataSourceId"], "maxResults": 5,
                "sortBy": {"attribute": "STARTED_AT", "order": "DESCENDING"}})
            if jobs and jobs[0]["status"] == "FAILED":
                self.agent.add_response("get_ingestion_job", {"ingestionJob": {
                    **jobs[0], "failureReasons": reasons or ["Access denied to s3://support-docs-bucket"]}})
        self.agent.add_response("list_tags_for_resource", {"tags": tags or {"team": "support"}},
                                {"resourceArn": desc["knowledgeBaseArn"]})

    def data_sources(self, *descs, kb_id=KB_ID):
        self.agent.add_response("list_data_sources", {"dataSourceSummaries": [
            {"knowledgeBaseId": kb_id, "dataSourceId": d["dataSourceId"], "name": d["name"], "status": d["status"],
             "updatedAt": d["updatedAt"]} for d in (descs or [ds_desc()])]}, {"knowledgeBaseId": kb_id})


@pytest.fixture
def aws():
    stubs = Stubs()
    yield stubs
    stubs.done()


@pytest.fixture
def core(aws):
    return aws.analyzer()


def test_resolve_accepts_id_name_and_arn(aws, core):
    aws.list_kbs((KB_ID, "support-docs"), (KB2_ID, "Sales Playbooks"))
    assert core.resolve("support-docs") == KB_ID
    assert core.resolve("SALES playbooks") == KB2_ID  # case doesn't matter
    assert core.resolve(KB2_ID) == KB2_ID
    assert core.resolve(KB_ARN) == KB_ID  # no call needed
    aws.list_kbs((KB_ID, "support-docs"), (KB2_ID, "Sales Playbooks"))  # a miss lists once more
    with pytest.raises(ValueError) as err:
        core.resolve("suport-docs")
    assert "No knowledge base 'suport-docs' in us-east-1" in str(err.value)
    assert "Did you mean 'support-docs'?" in str(err.value) and "kbs() lists them" in str(err.value)


def test_describe_and_list(aws, core):
    aws.list_kbs()
    aws.describe()
    [info] = core.list_knowledge_bases()
    assert info.name == "support-docs" and info.tags == {"team": "support"} and not info.errors
    [ds] = info.data_sources
    assert ds.bucket == "support-docs-bucket" and ds.last_sync.id == "JOB0000001" and ds.last_success.ok
    assert info.last_sync.scanned == 120


def test_describe_records_sections_it_cant_read(aws, core):
    aws.list_kbs()
    aws.describe(sources=[(ds_desc(), None)])  # ListIngestionJobs: AccessDenied
    info = core.describe("support-docs")
    assert info.errors == {"ingestion": "AccessDeniedException"}
    assert info.data_sources[0].errors == {"ingestion": "AccessDeniedException"}
    assert info.data_sources[0].last_sync is None and info.tags == {"team": "support"}


def test_describe_reads_why_the_last_sync_failed(aws, core):
    aws.list_kbs()
    aws.describe(sources=[(ds_desc(), [job(status="FAILED"), job("JOB0000000", started=ago(days=9))])],
                 reasons=["The knowledge base role can't read the bucket"])
    [ds] = core.describe(KB_ID).data_sources
    assert ds.last_sync.failure_reasons == ["The knowledge base role can't read the bucket"]
    assert ds.last_success.id == "JOB0000000"


def test_ingestion_jobs_merges_data_sources_newest_first(aws, core):
    aws.list_kbs()
    aws.data_sources(ds_desc(), ds_desc(DS2_ID, "web"))
    params = {"knowledgeBaseId": KB_ID, "maxResults": 3, "sortBy": {"attribute": "STARTED_AT", "order": "DESCENDING"}}
    aws.agent.add_response("list_ingestion_jobs", {"ingestionJobSummaries": [
        job("A1", started=ago(days=1), failed=2), job("A2", started=ago(days=5))]}, {**params, "dataSourceId": DS_ID})
    aws.agent.add_response("list_ingestion_jobs", {"ingestionJobSummaries": [
        job("B1", status="FAILED", started=ago(days=2), ds_id=DS2_ID)]}, {**params, "dataSourceId": DS2_ID})
    aws.agent.add_response("get_ingestion_job", {"ingestionJob": {**job("A1", failed=2), "failureReasons": ["x"]}},
                           {"knowledgeBaseId": KB_ID, "dataSourceId": DS_ID, "ingestionJobId": "A1"})
    aws.agent.add_response("get_ingestion_job", {"ingestionJob": {**job("B1", ds_id=DS2_ID), "failureReasons": ["y"]}},
                           {"knowledgeBaseId": KB_ID, "dataSourceId": DS2_ID, "ingestionJobId": "B1"})
    jobs = core.ingestion_jobs("support-docs", n=3)
    assert [j.id for j in jobs] == ["A1", "B1", "A2"] and jobs[1].failure_reasons == ["y"]


def test_documents_reads_statuses_and_notes_unsupported_sources(aws, core):
    aws.list_kbs()
    aws.data_sources(ds_desc(), ds_desc(DS2_ID, "web", kind="WEB"))
    aws.agent.add_response("list_knowledge_base_documents", {"documentDetails": [
        doc("a.pdf"), doc("b.pdf", "FAILED", "File is encrypted"), doc("c.pdf")]},
        {"knowledgeBaseId": KB_ID, "dataSourceId": DS_ID})
    aws.agent.add_client_error("list_knowledge_base_documents", service_error_code="ValidationException",
                               service_message="Not supported for WEB data sources")
    failed, summary = core.documents(KB_ID, status="failed")
    assert [d.name for d in failed] == ["b.pdf"] and failed[0].reason == "File is encrypted"
    assert summary.total == 3 and summary.counts == {"INDEXED": 2, "FAILED": 1} and not summary.truncated
    assert summary.errors == {DS2_ID: "ValidationException"}


def test_documents_stops_at_the_limit(aws, core):
    aws.list_kbs()
    aws.data_sources()
    aws.agent.add_response("list_knowledge_base_documents", {"documentDetails": [doc(f"{i}.pdf") for i in range(5)]})
    docs, summary = core.documents(KB_ID, limit=3)
    assert len(docs) == 3 and summary.truncated


def test_missing_region_is_a_readable_error(monkeypatch):
    for name in ("AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with pytest.raises(ValueError, match="No AWS region is set"):
        BedrockKBAnalyzer().client


# ----------------------------------------------------------------------------- UI


@pytest.fixture
def ui(core):
    return BedrockKBView(core, mode="text")


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


def test_ui_kbs(aws, ui, capsys):
    aws.list_kbs((KB_ID, "support-docs"), (KB2_ID, "sales"))
    aws.describe(sources=[(ds_desc(chunking=FIXED_20), [job()])])
    aws.describe(kb_desc(KB2_ID, "sales", store="PINECONE"), sources=[(ds_desc(DS2_ID, "crm"), [])])
    out = run(capsys, ui.kbs)
    for expected in ("Knowledge bases in us-east-1 (2)", "support-docs", "OpenSearch Serverless", "Pinecone",
                     "amazon.titan-embed-text-v2:0", "done 3d ago", "Never synced: 1 data source",
                     "Est. idle cost / month: $350.40", "With warnings: 1", "has never been synced"):
        assert expected in out


def test_ui_kb_info(aws, ui, capsys):
    aws.list_kbs()
    aws.describe(sources=[(ds_desc(chunking=FIXED_20, policy="RETAIN"), [job(failed=2)])])
    out = run(capsys, ui.kb_info, "support-docs")
    for expected in ("Knowledge base support-docs", "Status: ACTIVE", "Vector store: OpenSearch Serverless",
                     "Embedding model: amazon.titan-embed-text-v2:0 (1,024 dims)", "Est. idle cost / month: $350.40",
                     "Fixed size: 300 tokens per chunk, 20% overlap", "Default: the text only",
                     "s3://support-docs-bucket/policies/", "chunks kept (RETAIN)", "done 3d ago, 2 docs failed",
                     "2 documents that failed to index", "Recent syncs", "team", "syncs("):
        assert expected in out


def test_ui_kb_info_shows_unreadable_sections(aws, ui, capsys):
    aws.list_kbs()
    aws.describe(sources=[(ds_desc(), None)])
    out = run(capsys, ui.kb_info)  # the only knowledge base in the region
    assert "Last sync: ? (AccessDeniedException)" in out and "needs bedrock:ListIngestionJobs" in out


def test_ui_asks_which_kb_when_there_are_several(aws, ui, capsys):
    aws.list_kbs((KB_ID, "support-docs"), (KB2_ID, "sales"))
    out = run(capsys, ui.kb_info)
    assert "Which knowledge base? There are 2 in us-east-1: sales, support-docs" in out
    assert "use('sales')" in out and "Error" not in out


def test_ui_use_sets_the_default(aws, ui, capsys):
    aws.list_kbs((KB_ID, "support-docs"), (KB2_ID, "sales"))
    assert "Using knowledge base sales (KBID654321)" in run(capsys, ui.use, "Sales")
    aws.describe(kb_desc(KB2_ID, "sales"), sources=[])
    assert "Knowledge base sales" in run(capsys, ui.kb_info)


def test_ui_syncs(aws, ui, capsys):
    aws.list_kbs()
    aws.data_sources()
    aws.agent.add_response("list_ingestion_jobs", {"ingestionJobSummaries": [
        job("J2", status="FAILED", started=ago(days=1)), job("J1", started=ago(days=4), minutes=65)]})
    aws.agent.add_response("get_ingestion_job", {"ingestionJob": {
        **job("J2", status="FAILED"), "failureReasons": ["Access denied to s3://support-docs-bucket/policies/"]}})
    aws.data_sources()
    out = run(capsys, ui.syncs)
    for expected in ("Syncs of support-docs", "Failed: 1", "Last successful: 4d ago", "1h 05m",
                     "Access denied to s3://support-docs-bucket", "The last sync of the data source 'docs-s3' failed",
                     f"aws bedrock-agent start-ingestion-job --knowledge-base-id {KB_ID} --data-source-id {DS_ID}",
                     "this tool never starts a sync"):
        assert expected in out


def test_ui_documents(aws, ui, capsys):
    aws.list_kbs()
    aws.data_sources()
    aws.agent.add_response("list_knowledge_base_documents", {"documentDetails": [
        doc("a.pdf"), doc("scan.pdf", "FAILED", "The file is encrypted"), doc("b.pdf")]})
    aws.data_sources()
    out = run(capsys, ui.documents)
    for expected in ("Documents in support-docs", "Documents: 3", "Failed: 1", "Indexed: 2",
                     "1 document failed to index", "The file is encrypted", "start-ingestion-job"):
        assert expected in out
    assert out.index("scan.pdf") < out.index("a.pdf")  # failed first


def test_ui_turns_errors_into_notes(aws, ui, capsys):
    aws.list_kbs()  # listed once: the names were just read, so there's nothing newer to find
    out = run(capsys, ui.kb_info, "nope")
    assert "[!] ValueError: No knowledge base 'nope' in us-east-1" in out and "kbs() lists them" in out
    aws.agent.add_client_error("get_knowledge_base", service_error_code="ResourceNotFoundException",
                               service_message="KB not found", http_status_code=404)
    assert "knowledge base (or data source) not found in us-east-1; kbs() lists them" in run(capsys, ui.kb_info, KB_ARN)
    aws.agent.add_client_error("get_knowledge_base", service_error_code="AccessDeniedException",
                               service_message="User is not authorized to perform bedrock:GetKnowledgeBase",
                               http_status_code=403)
    out = run(capsys, ui.kb_info, KB_ARN)
    assert "AccessDeniedException: User is not authorized" in out and "README lists the read-only IAM" in out


def test_ui_without_a_region(monkeypatch, capsys):
    for name in ("AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    ui = BedrockKBView(mode="text")  # no traceback here...
    assert "No AWS region is set" in run(capsys, ui.kbs)  # ...and a note that says what to pass


def test_ui_help(ui, capsys):
    out = run(capsys, ui.help)
    assert "BedrockKBView commands" in out and "kb_info(kb: 'str | None' = None)" in out


def test_ui_html_mode(aws, core, monkeypatch):
    import IPython.display

    shown = []

    class Handle:
        def update(self, obj):
            pass

    def fake_display(obj, display_id=None):
        shown.append(obj.data)
        return Handle() if display_id else None

    monkeypatch.setattr(IPython.display, "display", fake_display)
    aws.list_kbs()
    aws.describe()
    BedrockKBView(core, mode="html").kb_info()
    html_out = "".join(shown)
    assert '<div class="kba">' in html_out and "OpenSearch Serverless" in html_out


def test_html_escapes_values():
    blocks = [kbmod._Title("<b>x</b>"), kbmod._Table(["Value"], [["<script>alert(1)</script>"]])]
    rendered = kbmod._render_html(blocks, 50)
    assert "<script>alert" not in rendered and "&lt;script&gt;" in rendered


def test_dataclasses_default_cleanly():
    assert DataSourceInfo("x").errors == {} and IngestionJob("j").duration is None
