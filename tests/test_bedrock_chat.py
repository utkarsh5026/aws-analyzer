import ast
import asyncio
import html
import json
import re
import shlex
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from botocore.validate import ParamValidator

import bedrock_chat as chatmod
from bedrock_chat import (
    DEFAULT_PROMPT,
    DEFAULT_SETTINGS,
    Answer,
    Batch,
    BatchItem,
    BedrockChatAnalyzer,
    BedrockChatView,
    Citation,
    Passage,
    _answer_lines,
    _diff,
    _markdown_html,
    _py_literal,
    _python_html,
    _question_text,
    answer_cost,
    answer_findings,
    apply_setup,
    as_filter,
    batch_changes,
    batch_estimate,
    batch_findings,
    build_request,
    build_retrieve_request,
    cited_ranks,
    cli_command,
    compare_findings,
    coerce_setting,
    collect_stream,
    config_json,
    config_of,
    describe_filter,
    describe_setting,
    expected_at,
    format_questions,
    format_variations,
    item_verdict,
    normalize_settings,
    parse_questions,
    parse_rag,
    parse_retrieve,
    parse_variations,
    python_call,
    python_script,
    question_list,
    rank_runs,
    ranking_findings,
    read_runs,
    request_schema,
    retrieve_settings,
    run_from_record,
    run_record,
    run_score,
    settings_findings,
    setup_label,
    shared_questions,
    sweep_estimate,
    sweep_setups,
    varied_setups,
    describe_files,
    file_labels,
    match_files,
    match_kbs,
    parse_document,
    search_rank,
    settings_from_request,
    split_data_sources,
    validate_request,
)

KB_ID, KB2_ID = "KBID123456", "KBID654321"
ACCOUNT = "123456789012"
NOW = datetime.now(timezone.utc)
SCHEMA = request_schema()
F = SCHEMA.fields
KB = ("retrieveAndGenerateConfiguration", "knowledgeBaseConfiguration")
SONNET = "anthropic.claude-sonnet-5"
HAIKU = "anthropic.claude-haiku-4-5-20251001-v1:0"  # what DEFAULT_MODEL resolves to
SONNET_PROFILE = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.{SONNET}"
OPUS_PROFILE = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.anthropic.claude-opus-5"


def model(model_id, name, provider="Anthropic", on_demand=False):
    return {"modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{model_id}", "modelId": model_id,
            "modelName": name, "providerName": provider, "inputModalities": ["TEXT"], "outputModalities": ["TEXT"],
            "inferenceTypesSupported": ["ON_DEMAND"] if on_demand else [], "modelLifecycle": {"status": "ACTIVE"}}


CLAUDES = ["anthropic.claude-opus-5", SONNET, HAIKU]
MODEL_LIST = [model(CLAUDES[0], "Claude Opus 5"), model(SONNET, "Claude Sonnet 5"), model(HAIKU, "Claude Haiku 4.5"),
              model("amazon.nova-pro-v1:0", "Nova Pro", "Amazon", True)]
PROFILES = [
    {"inferenceProfileName": f"US {m}", "inferenceProfileId": f"us.{m}", "status": "ACTIVE", "type": "SYSTEM_DEFINED",
     "inferenceProfileArn": f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.{m}",
     "models": [{"modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{m}"}]}
    for m in CLAUDES
]


def ref(text, key="policies/refund-policy.pdf", page=3, **metadata):
    md = {"x-amz-bedrock-kb-source-uri": f"s3://docs/{key}", "x-amz-bedrock-kb-chunk-id": f"chunk-{key}-{page}",
          "x-amz-bedrock-kb-data-source-id": "DSID123456", **metadata}
    if page is not None:
        md["x-amz-bedrock-kb-document-page-number"] = float(page)
    return {"content": {"type": "TEXT", "text": text}, "location": {"type": "S3", "s3Location": {"uri": f"s3://docs/{key}"}},
            "metadata": md}


REFUND = ref("Refunds are issued within 5-7 business days of receiving the returned item.", team="billing")
BANK = ref("Orders paid by bank transfer can take up to 10 business days.", page=4)
SHIPPING = ref("Standard shipping takes 3-5 business days within the EU.", "faq/shipping.md", page=None)
RETRIEVED = {"retrievalResults": [{**REFUND, "score": 0.81}, {**BANK, "score": 0.62}, {**SHIPPING, "score": 0.4}]}
ANSWER = "Refunds take 5-7 business days. Bank transfers can take up to 10 days. Ask support for anything else."


def rag_resp(text=ANSWER, citations=(("Refunds take 5-7 business days.", [REFUND]),
                                     ("Bank transfers can take up to 10 days.", [BANK])), session="session-1"):
    """A RetrieveAndGenerate response; each citation is (the answer text it covers, [refs]). Spans end inclusively."""
    cites = []
    for piece, refs in citations:
        start = text.index(piece)
        cites.append({"generatedResponsePart": {"textResponsePart": {
            "text": piece, "span": {"start": start, "end": start + len(piece) - 1}}}, "retrievedReferences": list(refs)})
    resp = {"output": {"text": text}, "sessionId": session}
    if cites:
        resp["citations"] = cites
    return resp


def stream_events(resp, size=12):
    text = resp["output"]["text"]
    for i in range(0, len(text), size):
        yield {"output": {"text": text[i:i + size]}}
    for cite in resp.get("citations", []):
        yield {"citation": cite}


def client_error(code, message, operation="RetrieveAndGenerate"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class Fake:
    """A boto3 client stand-in answering from functions in any order (the window's flows don't keep Stubber's
    order). Every request, response and stream event is checked against the service model, and calls are recorded.
    Operations without a handler don't exist on it, like on an old boto3."""

    def __init__(self, service, handlers):
        model = boto3.client(service, region_name="us-east-1").meta.service_model
        self.meta = SimpleNamespace(region_name="us-east-1", service_model=model)
        self.ops = {xform_name(op): model.operation_model(op) for op in model.operation_names}
        self.handlers, self.calls = dict(handlers), []

    def _call(self, name, params):
        op = self.ops[name]
        report = ParamValidator().validate(params, op.input_shape)
        assert not report.has_errors(), report.generate_report()
        self.calls.append((name, params))
        resp = self.handlers[name](**params)
        if op.has_event_stream_output:
            return {**resp, "stream": self._checked(resp["stream"], op.get_event_stream_output())}
        report = ParamValidator().validate(resp, op.output_shape)
        assert not report.has_errors(), report.generate_report()
        return resp

    @staticmethod
    def _checked(events, shape):
        for event in events:
            report = ParamValidator().validate(event, shape)
            assert not report.has_errors(), report.generate_report()
            yield event

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.handlers:
            raise AttributeError(name)
        return lambda **params: self._call(name, params)

    def get_paginator(self, name):
        return SimpleNamespace(paginate=lambda **params: iter([self._call(name, params)]))

    def called(self, name):
        return [params for op, params in self.calls if op == name]


def kb_summary(kb_id=KB_ID, name="support-docs", status="ACTIVE"):
    return {"knowledgeBaseId": kb_id, "name": name, "status": status, "description": f"{name} answers",
            "updatedAt": NOW - timedelta(days=2)}


DS_ID, DS2_ID = "DSID123456", "DSID654321"


def ds_summary(ds_id=DS_ID, name="docs-s3", kb_id=KB_ID):
    return {"knowledgeBaseId": kb_id, "dataSourceId": ds_id, "name": name, "status": "AVAILABLE",
            "updatedAt": NOW - timedelta(days=3)}


REFUND_PDF, RETURNS_MD, SCAN_PDF = ("s3://docs/policies/refund-policy.pdf", "s3://docs/faq/returns.md",
                                   "s3://docs/policies/scanned-invoice.pdf")


def doc(uri, status="INDEXED", ds_id=DS_ID, kb_id=KB_ID):
    return {"knowledgeBaseId": kb_id, "dataSourceId": ds_id, "status": status, "updatedAt": NOW - timedelta(days=4),
            "identifier": {"dataSourceType": "S3", "s3": {"uri": uri}}}


def fakes(kbs=None, rag=None, stream=None, sources=None, documents=None, retrieve=None):
    """bedrock-agent (the knowledge base list, each one's data sources and their files), bedrock (models) and
    bedrock-agent-runtime fakes. sources: {knowledge base ID: [data source summaries]}; one data source each by
    default. documents: {data source ID: [document details] or an exception}; three files each by default. retrieve
    answers Retrieve (RETRIEVED by default: the two passages rag_resp() cites, then one it doesn't)."""
    kbs = [kb_summary()] if kbs is None else kbs
    rag = rag or (lambda **params: rag_resp())
    retrieve = retrieve or (lambda **params: RETRIEVED)
    sources = sources or {}
    documents = documents or {}

    def listed(knowledgeBaseId, **_):
        found = sources.get(knowledgeBaseId, [ds_summary(kb_id=knowledgeBaseId)])
        return {"dataSourceSummaries": found}

    def files(knowledgeBaseId, dataSourceId, **_):
        found = documents.get(dataSourceId, [doc(REFUND_PDF, ds_id=dataSourceId, kb_id=knowledgeBaseId),
                                             doc(RETURNS_MD, ds_id=dataSourceId, kb_id=knowledgeBaseId),
                                             doc(SCAN_PDF, "FAILED", ds_id=dataSourceId, kb_id=knowledgeBaseId)])
        if isinstance(found, Exception):
            raise found
        return {"documentDetails": found}

    def streamed(**params):
        resp = rag(**params)
        return {"sessionId": resp["sessionId"], "stream": stream_events(resp)}

    runtime = {"retrieve_and_generate": rag, "retrieve": retrieve}
    if stream is not False:
        runtime["retrieve_and_generate_stream"] = stream or streamed
    return {
        "bedrock-agent": Fake("bedrock-agent", {"list_knowledge_bases": lambda **_: {"knowledgeBaseSummaries": kbs},
                                                "list_data_sources": listed,
                                                "list_knowledge_base_documents": files}),
        "bedrock": Fake("bedrock", {"list_foundation_models": lambda **_: {"modelSummaries": MODEL_LIST},
                                    "list_inference_profiles": lambda **_: {"inferenceProfileSummaries": PROFILES}}),
        "bedrock-agent-runtime": Fake("bedrock-agent-runtime", runtime),
    }


@pytest.fixture
def clients():
    return fakes()


@pytest.fixture
def core(clients):
    return BedrockChatAnalyzer(clients=clients)


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


# ----------------------------------------------------------------------------- helpers


def test_schema_reads_every_setting_from_the_service_model():
    for key in ("n", "search_type", "filter", "reranker", "rerank_n", "temperature", "top_p", "max_tokens", "stop",
                "prompt", "model_fields", "guardrail_id", "guardrail_version", "latency", "query_decomposition",
                "orchestration_prompt", "kms_key"):
        assert key in F, key
    assert F["n"].path == (*KB, "retrievalConfiguration", "vectorSearchConfiguration", "numberOfResults")
    assert (F["n"].kind, F["n"].low, F["n"].high, F["n"].group) == ("integer", 1, 100, "Retrieval")
    assert (F["temperature"].kind, F["temperature"].low, F["temperature"].high) == ("float", 0, 1)
    assert F["search_type"].choices == ("HYBRID", "SEMANTIC")
    assert (F["prompt"].kind, F["prompt"].high) == ("long_text", 4000)
    assert (F["filter"].kind, F["model_fields"].kind, F["model_fields"].container) == ("json", "json", "object")
    assert (F["stop"].kind, F["stop"].high) == ("list", 4)
    assert F["reranker"].kind == "text" and F["kms_key"].path == ("sessionConfiguration", "kmsKeyArn")
    # Fields without a short name go by their path; the window's pickers, the question and the session aren't settings.
    assert "orchestrationConfiguration.inferenceConfig.textInferenceConfig.temperature" in F
    assert not any(f.path[-1] in ("knowledgeBaseId", "text") or "managedSearchConfiguration" in f.path
                   or f.path[0] in ("input", "sessionId") for f in F.values())
    assert [g for g in dict.fromkeys(f.group for f in F.values())] == ["Retrieval", "Generation", "Orchestration",
                                                                       "Session"]
    assert ((*KB, "retrievalConfiguration", "vectorSearchConfiguration", "rerankingConfiguration", "type"),
            "BEDROCK_RERANKING_MODEL") in SCHEMA.auto
    assert all("modelArn" not in f.where or f.key == "reranker" or "implicitFilter" in f.where for f in F.values())


@pytest.mark.parametrize("name, key", [
    ("temperature", "temperature"),
    ("maxTokens", "max_tokens"),
    ("MAX-TOKENS", "max_tokens"),
    ("where", "filter"),
    ("numberOfResults", "n"),
    ("generationConfiguration.performanceConfig.latency", "latency"),
    ("retrieveAndGenerateConfiguration.knowledgeBaseConfiguration.retrievalConfiguration.vectorSearchConfiguration"
     ".numberOfResults", "n"),
    ("selectionMode", "retrievalConfiguration.vectorSearchConfiguration.rerankingConfiguration"
                      ".bedrockRerankingConfiguration.metadataConfiguration.selectionMode"),
    ("orchestrationConfiguration.performanceConfig.latency", "orchestrationConfiguration.performanceConfig.latency"),
])
def test_find_takes_short_names_other_names_and_paths(name, key):
    assert SCHEMA.find(name).key == key


@pytest.mark.parametrize("name, message", [
    ("temprature", "Did you mean 'temperature'"),
    ("textInferenceConfig.temperature", "could be 'temperature' or"),
    ("", "Name a setting"),
    ("nothing_like_it", "fields() lists every one"),
])
def test_find_explains_what_it_cant_find(name, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        SCHEMA.find(name)


@pytest.mark.parametrize("key, value, expected", [
    ("n", "8", 8),
    ("n", 8.0, 8),
    ("temperature", "0.3", 0.3),
    ("temperature", 1, 1.0),
    ("search_type", "hybrid", "HYBRID"),
    ("query_decomposition", True, "QUERY_DECOMPOSITION"),
    ("stop", "END\nSTOP\n", ["END", "STOP"]),
    ("stop", '["\\n\\nHuman:"]', ["\n\nHuman:"]),
    ("stop", ("A",), ["A"]),
    ("model_fields", "{'top_k': 50}", {"top_k": 50}),
    ("model_fields", '{"top_k": 50}', {"top_k": 50}),
    ("reranker", True, "cohere"),
    ("reranker", "Amazon", "amazon"),
    ("reranker", "arn:aws:bedrock:eu-west-1::foundation-model/cohere.rerank-v3-5:0", "cohere"),
    ("reranker", "acme.rerank-v9", "acme.rerank-v9"),
    ("kms_key", "  arn:aws:kms:us-east-1:1:key/x  ", "arn:aws:kms:us-east-1:1:key/x"),
    ("prompt", DEFAULT_PROMPT, DEFAULT_PROMPT),
    ("latency", "OPTIMIZED", "optimized"),
])
def test_coerce_setting_is_forgiving(key, value, expected):
    assert coerce_setting(F[key], value) == expected


@pytest.mark.parametrize("key, value, message", [
    ("n", 0, "n can be 1 to 100; got 0"),
    ("n", "lots", "n takes a number of items"),
    ("n", True, "n takes a number"),
    ("temperature", 1.5, "temperature can be 0 to 1; got 1.5"),
    ("temperature", "warm", "temperature takes a number, like 0.2"),
    ("search_type", "fuzzy", "search_type is 'HYBRID' or 'SEMANTIC'"),
    ("stop", ["a", "b", "c", "d", "e"], "stop takes up to 4 items; got 5"),
    ("stop", 5, "stop takes a list"),
    ("model_fields", "[1, 2]", "model_fields takes a JSON object"),
    ("model_fields", "{oops", "model_fields isn't valid JSON: Expecting property name"),
    ("prompt", "Answer the question.", "needs \\$search_results\\$"),
    ("prompt", "x" * 4001 + "$search_results$", "takes up to 4,000 characters"),
    ("filter", {}, "filter needs at least one condition"),
    ("filter", {"year": ("~", 1)}, "Can't read the condition"),
    ("reranker", "", "reranker takes 'cohere', 'amazon'"),
])
def test_coerce_setting_says_what_a_field_takes(key, value, message):
    with pytest.raises(ValueError, match=message):
        coerce_setting(F[key], value)


def test_filters_in_every_form():
    bedrock = {"equals": {"key": "team", "value": "billing"}}
    assert as_filter(bedrock) == bedrock
    assert as_filter('{"team": "billing"}') == bedrock
    assert as_filter({"team": ["billing", "support"]}) == {"in": {"key": "team", "value": ["billing", "support"]}}
    assert as_filter('{"year": [">=", 2024], "region": ["in", ["eu", "uk"]]}') == {"andAll": [
        {"greaterThanOrEquals": {"key": "year", "value": 2024}}, {"in": {"key": "region", "value": ["eu", "uk"]}}]}
    assert as_filter({"year": ("between", 2020, 2024)})["andAll"][1] == {
        "lessThanOrEquals": {"key": "year", "value": 2024}}
    nested = {"orAll": [bedrock, {"andAll": [{"greaterThan": {"key": "year", "value": 2020}},
                                             {"startsWith": {"key": "doc", "value": "POL-"}}]}]}
    assert describe_filter(nested) == 'team = "billing" or (year > 2020 and doc starts with "POL-")'
    assert describe_filter("nope") == "a filter"


def test_build_request_puts_each_setting_in_place_and_fills_required_fields():
    values = normalize_settings({"n": 20, "reranker": "cohere", "rerank_n": 4, "temperature": 0.2,
                                 "where": {"team": "billing"}, "kms_key": "arn:aws:kms:us-east-1:1:key/k"}, SCHEMA)
    params = build_request("How long?", KB_ID, SONNET_PROFILE, values, SCHEMA, session_id="s-1", region="eu-west-1")
    vector = params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]["retrievalConfiguration"][
        "vectorSearchConfiguration"]
    assert list(params) == ["input", "sessionId", "retrieveAndGenerateConfiguration", "sessionConfiguration"]
    assert vector["numberOfResults"] == 20 and vector["filter"] == {"equals": {"key": "team", "value": "billing"}}
    assert vector["rerankingConfiguration"] == {
        "bedrockRerankingConfiguration": {
            "modelConfiguration": {"modelArn": "arn:aws:bedrock:eu-west-1::foundation-model/cohere.rerank-v3-5:0"},
            "numberOfRerankedResults": 4},
        "type": "BEDROCK_RERANKING_MODEL"}
    assert params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]["generationConfiguration"] == {
        "inferenceConfig": {"textInferenceConfig": {"temperature": 0.2}}}
    assert validate_request(params, SCHEMA) == []
    # The values are copies: editing the request doesn't change the settings.
    vector["filter"]["equals"]["value"] = "x"
    assert values["filter"]["equals"]["value"] == "billing"
    assert "sessionId" not in build_request("q", KB_ID, SONNET_PROFILE, {}, SCHEMA)


def test_settings_from_request_is_the_opposite_of_build_request():
    values = normalize_settings({"n": 8, "search_type": "semantic", "stop": ["END"], "query_decomposition": True,
                                 "reranker": "amazon", "orchestrationConfiguration.performanceConfig.latency":
                                 "optimized"}, SCHEMA)
    params = build_request("q?", KB_ID, SONNET_PROFILE, values, SCHEMA, session_id="s-9", region="us-east-1")
    picked, back = settings_from_request(params, SCHEMA)
    assert back == values
    assert picked == {"question": "q?", "sessionId": "s-9", "knowledgeBaseId": KB_ID, "modelArn": SONNET_PROFILE}


def test_settings_from_request_names_what_the_chat_cant_send():
    params = build_request("q", KB_ID, SONNET_PROFILE, {}, SCHEMA)
    params["retrieveAndGenerateConfiguration"]["type"] = "EXTERNAL_SOURCES"
    params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]["generationConfiguration"] = {
        "inferenceConfig": {"textInferenceConfig": {"temperature": 3}}}
    params["userContext"] = {"userId": "u-1"}
    params["extra"] = 1
    with pytest.raises(ValueError) as err:
        settings_from_request(params, SCHEMA)
    message = str(err.value)
    assert message.startswith("This chat asks knowledge bases")
    assert "temperature can be 0 to 1; got 3" in message and "the chat doesn't send extra" in message
    with pytest.raises(ValueError, match="is a JSON object"):
        settings_from_request([], SCHEMA)



DS_KEY = "x-amz-bedrock-kb-data-source-id"


def test_data_sources_go_in_the_filter_and_come_back_out():
    one = {"equals": {"key": DS_KEY, "value": "DSID123456"}}
    params = build_request("q", KB_ID, SONNET_PROFILE, {"n": 5}, SCHEMA, data_sources=["DSID123456"])
    vector = params[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert vector == {"numberOfResults": 5, "filter": one} and validate_request(params, SCHEMA) == []
    picked, back = settings_from_request(params, SCHEMA)
    assert picked["dataSources"] == ["DSID123456"] and back == {"n": 5}
    # with a filter of your own, both must match, and Edit JSON gives each back to where it came from
    values = normalize_settings({"filter": {"team": "billing", "year": [">=", 2024]}}, SCHEMA)
    params = build_request("q", KB_ID, SONNET_PROFILE, values, SCHEMA, data_sources=["DSID123456", "DSID654321"])
    both = params[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert both["andAll"][0] == {"in": {"key": DS_KEY, "value": ["DSID123456", "DSID654321"]}}
    assert both["andAll"][1:] == values["filter"]["andAll"] and validate_request(params, SCHEMA) == []
    picked, back = settings_from_request(params, SCHEMA)
    assert picked["dataSources"] == ["DSID123456", "DSID654321"] and back == values
    team = {"equals": {"key": "team", "value": "billing"}}
    assert split_data_sources({"andAll": [team, one]}) == (["DSID123456"], team)
    assert split_data_sources(team) == ([], team)
    assert split_data_sources({"orAll": [one, team]}) == ([], {"orAll": [one, team]})  # either: not a data source pick
    assert split_data_sources({"in": {"key": DS_KEY, "value": []}}) == ([], {"in": {"key": DS_KEY, "value": []}})
    assert "filter" not in str(build_request("q", KB_ID, SONNET_PROFILE, {}, SCHEMA, data_sources=[]))


URI_KEY = "x-amz-bedrock-kb-source-uri"


def test_files_go_in_the_filter_with_the_data_source_and_come_back_out():
    values = normalize_settings({"filter": {"team": "billing"}}, SCHEMA)
    params = build_request("q", KB_ID, SONNET_PROFILE, values, SCHEMA, data_sources=[DS_ID],
                           files=[REFUND_PDF, RETURNS_MD])
    condition = params[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    assert condition == {"andAll": [{"equals": {"key": DS_KEY, "value": DS_ID}},
                                    {"in": {"key": URI_KEY, "value": [REFUND_PDF, RETURNS_MD]}},
                                    {"equals": {"key": "team", "value": "billing"}}]}
    assert validate_request(params, SCHEMA) == []
    picked, back = settings_from_request(params, SCHEMA)
    assert (picked["dataSources"], picked["files"], back) == ([DS_ID], [REFUND_PDF, RETURNS_MD], values)
    params = build_request("q", KB_ID, SONNET_PROFILE, {}, SCHEMA, files=[REFUND_PDF])
    picked, back = settings_from_request(params, SCHEMA)
    assert picked["files"] == [REFUND_PDF] and "dataSources" not in picked and back == {}


def test_files_are_named_by_path_name_or_s3_path():
    docs = [parse_document(doc(uri)) for uri in (REFUND_PDF, RETURNS_MD, "s3://docs/old/refund-policy.pdf",
                                                  "s3://other/faq/returns.md")]
    assert file_labels(d.uri for d in docs) == {
        REFUND_PDF: "policies/refund-policy.pdf", RETURNS_MD: "docs/faq/returns.md",
        "s3://docs/old/refund-policy.pdf": "old/refund-policy.pdf", "s3://other/faq/returns.md": "other/faq/returns.md"}
    uris, problems = match_files(docs, ["Policies/Refund-Policy.PDF", "s3://docs/new.pdf", "other/faq/returns.md"])
    assert uris == [REFUND_PDF, "s3://docs/new.pdf", "s3://other/faq/returns.md"] and problems == []
    uris, problems = match_files(docs, ["refund-policy.pdf", "refund-polcy.pdf"])
    assert uris == []
    assert problems[0].startswith("'refund-policy.pdf' names 2 files ('policies/refund-policy.pdf', "
                                  "'old/refund-policy.pdf'): pass more of its path")
    assert problems[1] == ("No file 'refund-polcy.pdf' in the knowledge base. Did you mean "
                           "'policies/refund-policy.pdf' or 'old/refund-policy.pdf'?")
    assert describe_files([]) == "every file" and describe_files([REFUND_PDF]) == "file 'refund-policy.pdf'"
    assert describe_files([REFUND_PDF, RETURNS_MD]) == "files 'refund-policy.pdf' and 'returns.md'"
    assert describe_files([REFUND_PDF] * 4) == "4 files"

def test_validate_request_reports_what_bedrock_would_refuse():
    params = build_request("q", KB_ID, SONNET_PROFILE, {"guardrail_id": "gr-1"}, SCHEMA)
    params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]["generationConfiguration"][
        "inferenceConfig"] = {"textInferenceConfig": {"temprature": 0.2}}
    problems = validate_request(params, SCHEMA)
    assert any('Missing required parameter in generationConfiguration.guardrailConfiguration: "guardrailVersion"'
               in p for p in problems)
    assert any('Unknown parameter in generationConfiguration.inferenceConfig.textInferenceConfig: "temprature"' in p
               for p in problems)
    assert validate_request(params, chatmod.Schema({}, [], None)) == []


def test_parse_rag_numbers_each_passage_once():
    resp = rag_resp(citations=(("Refunds take 5-7 business days.", [REFUND, BANK]),
                               ("Bank transfers can take up to 10 days.", [BANK])))
    a = parse_rag(resp)
    assert [c.sources for c in a.citations] == [[1, 2], [2]]
    assert [p.source for p in a.sources] == ["refund-policy.pdf p.3", "refund-policy.pdf p.4"]
    assert a.citations[0].text == "Refunds take 5-7 business days." and a.session_id == "session-1"
    assert a.sources[0].metadata == {"team": "billing"}
    assert 0.6 < a.grounded_share < 0.8 and a.cited == [1, 2]


def test_collect_stream_builds_a_response_like_retrieve_and_generates():
    resp = rag_resp()
    old_style = {"citation": {"citation": resp["citations"][1]}}  # older events nest the parts under 'citation'
    events = [{"output": {"text": "Refunds take 5-7 "}}, {"output": {"text": "business days."}},
              {"citation": resp["citations"][0]}, {"output": {"text": ANSWER[len("Refunds take 5-7 business days."):]}},
              old_style, {"guardrail": {"action": "INTERVENED"}}]
    seen = []
    built = collect_stream(events, seen.append)
    assert seen == ["Refunds take 5-7 ", "Refunds take 5-7 business days.", ANSWER]
    assert built == {"output": {"text": ANSWER}, "citations": resp["citations"], "guardrailAction": "INTERVENED"}
    assert parse_rag(built).cited == [1, 2]
    assert collect_stream([]) == {"output": {"text": ""}}


@pytest.mark.parametrize("key, value, text", [
    ("n", 8, "Retrieves the 8 passages that match best."),
    ("n", 1, "Retrieves the 1 passage that match best."),
    ("search_type", "SEMANTIC", "Matches meaning only."),
    ("filter", {"equals": {"key": "team", "value": "billing"}}, 'Only documents where team = "billing".'),
    ("reranker", "amazon", "Re-orders the passages with Amazon Rerank 1.0 before the model sees them."),
    ("temperature", 0.9, "0.9: varied wording."),
    ("max_tokens", 2048, "Answers stop at 2,048 tokens (about 1,536 words)."),
    ("stop", ["END"], "The answer ends at 'END'."),
    ("model_fields", {"top_k": 50}, "Passed to the model as they are: top_k=50."),
    ("prompt", DEFAULT_PROMPT, f"Your own prompt ({len(DEFAULT_PROMPT):,} characters)."),
    ("kms_key", None, "Not sent until you fill it in."),
])
def test_describe_setting_says_what_a_value_means(key, value, text):
    assert describe_setting(F[key], value) == text


def test_settings_findings():
    assert settings_findings(DEFAULT_SETTINGS) == []
    both = settings_findings({"temperature": 0.2, "top_p": 0.9}, "us.anthropic.claude-sonnet-5")
    assert both[0][0] == "warn" and "unset('top_p')" in both[0][1]
    assert settings_findings({"temperature": 0.2, "top_p": 0.9}, "amazon.nova-pro-v1:0")[0][0] == "info"
    found = dict((m.split(" ")[1], level) for level, m in settings_findings(
        {"prompt": "Use $search_results$", "reranker": "cohere", "n": 5, "guardrail_id": "gr", "query_decomposition":
         "QUERY_DECOMPOSITION"}))
    assert found == {"prompt": "warn", "reranker": "info", "guardrail": "warn", "decomposition": "info"}
    assert not any("reranker" in m for _, m in settings_findings({"reranker": "cohere", "n": 20}))


def answer(text=ANSWER, settings=None, **kwargs):
    a = parse_rag(rag_resp(text) if text == ANSWER else {"output": {"text": text}})
    a.question, a.model, a.settings = "How long?", "us.anthropic.claude-sonnet-5", settings or {"n": 5}
    for name, value in kwargs.items():
        setattr(a, name, value)
    return a


def test_answer_findings_say_which_setting_to_try():
    assert answer_findings(answer()) == []
    refusal = answer("Sorry, I am unable to assist you with this request.")
    (level, message), = answer_findings(refusal)
    assert level == "warn" and "set(n=10)" in message and "set(search_type='HYBRID')" in message
    narrow = answer("Sorry, I am unable to assist you with this request.",
                    {"n": 20, "search_type": "HYBRID", "filter": {"equals": {"key": "a", "value": 1}}})
    assert "unset('filter')" in answer_findings(narrow)[0][1] and "set(n=10)" not in answer_findings(narrow)[0][1]
    assert "An empty answer" in answer_findings(answer(""))[0][1]
    uncited = answer("They take a week.", {"prompt": "Use $search_results$"})
    assert "no $output_format_instructions$" in answer_findings(uncited)[0][1]
    assert "cites no source" in answer_findings(answer("They take a week."))[0][1]
    thin = answer()
    thin.citations = thin.citations[:1]
    thin.text += " " + "More words that no source backs up." * 3
    assert "is backed by a citation" in answer_findings(thin)[0][1]
    long = answer(settings={"max_tokens": 20}, output_tokens=19, guardrail_action="INTERVENED",
                  notes=["The earlier conversation had expired."])
    assert [level for level, _ in answer_findings(long)] == ["warn", "warn", "info"]
    assert "set(max_tokens=1024)" in answer_findings(long)[1][1]


def test_answer_cost_adds_the_reranker():
    a = answer(input_tokens=1_000_000, output_tokens=0)
    assert answer_cost(a) == pytest.approx(2.20 + 0.02 * estimate("How long?") / 1e6)
    a.settings = {"reranker": "cohere"}
    assert answer_cost(a) == pytest.approx(2.20 + 0.002 + 0.02 * estimate("How long?") / 1e6)
    a.model = "acme.unknown-v1"
    assert answer_cost(a) is None


def estimate(text):
    return chatmod.estimate_tokens(text)


def test_python_call_is_python_that_makes_the_same_call():
    values = normalize_settings({"filter": {"team": "billing"}, "temperature": 0.2, "stop": ["END"]}, SCHEMA)
    params = build_request("How long do refunds take?", KB_ID, SONNET_PROFILE, values, SCHEMA)
    code = python_call(params, "us-east-1")
    compile(code, "<cell>", "exec")
    assert "boto3.client('bedrock-agent-runtime', region_name='us-east-1')" in code
    literal = code.split("retrieve_and_generate(**", 1)[1].rsplit(")\nprint", 1)[0]
    assert ast.literal_eval(literal) == params
    assert ast.literal_eval(_py_literal({"a": [1, {"b": None}] * 30})) == {"a": [1, {"b": None}] * 30}
    narrow = python_call(params, "us-east-1", width=60)
    literal = narrow.split("retrieve_and_generate(**", 1)[1].rsplit(")\nprint", 1)[0]
    assert ast.literal_eval(literal) == params
    assert all(len(line) <= 60 for line in literal.splitlines() if "modelArn" not in line)  # keys count too


def plain(markup):
    """HTML -> the text it shows."""
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def test_python_is_highlighted_and_escaped():
    code = "import boto3\nclient = boto3.client('x', region_name='<us>')  # hi\nprint({'k': 1, 'n': None})"
    out = _python_html(code)
    assert plain(out) == code
    for part in ('<span class="pk">import</span>', '<span class="pf">client</span>', '<span class="pa">region_name</span>',
                 '<span class="js">&#x27;&lt;us&gt;&#x27;</span>', '<span class="pc"># hi</span>',
                 '<span class="jk">&#x27;k&#x27;</span>', '<span class="jn">1</span>', '<span class="jl">None</span>',
                 '<span class="pf">print</span>'):
        assert part in out
    assert _python_html("x = (1, '<b>'") == "x = (1, &#x27;&lt;b&gt;&#x27;"  # doesn't tokenize: plain, still escaped
    out = chatmod._json_text_html({"a": [1, True, None, 'x"<']})
    assert json.loads(plain(out)) == {"a": [1, True, None, 'x"<']}
    assert '<span class="jk">&quot;a&quot;</span>:' in out and '<span class="jl">true</span>' in out


def test_markdown_answers_are_laid_out_and_escaped():
    out = _markdown_html("# Steps\n\n1. Open **Refunds**\n2. Pick `order-42`\n   - nested\n\n| a | b |\n|---|--:|\n"
                         "| x | 1 |\n\n```bash\naws s3 ls\n```\n> a *quote*\n\n<script>x</script> [ok](https://a.b) "
                         "[bad](javascript:alert(1)) ~~old~~ https://aws.amazon.com/bedrock.")
    assert "<h1>Steps</h1>" in out
    assert "<ol><li>Open <strong>Refunds</strong></li><li>Pick <code>order-42</code><ul><li>nested</li></ul></li></ol>" in out
    assert "<thead><tr><th>a</th><th class=\"r\">b</th></tr></thead><tbody><tr><td>x</td><td class=\"r\">1</td>" in out
    assert '<span class="lang">bash</span>' in out and "<code>aws s3 ls</code></pre>" in out
    assert "<blockquote><p>a <em>quote</em></p></blockquote>" in out and "<del>old</del>" in out
    assert "&lt;script&gt;x&lt;/script&gt;" in out and "<script>" not in out  # raw HTML stays text
    assert '<a href="https://a.b" target="_blank" rel="noopener noreferrer">ok</a>' in out
    assert " bad " in out and "javascript" not in out  # only http(s) and mailto links open
    assert '<a href="https://aws.amazon.com/bedrock" target="_blank" rel="noopener noreferrer">' in out
    assert _markdown_html("line one\nline two") == "<p>line one<br>line two</p>"  # plain answers keep their breaks
    assert _markdown_html("snake_case_name, 2 * 3 * 4, **not closed") == "<p>snake_case_name, 2 * 3 * 4, **not closed</p>"
    assert _markdown_html("- a\n- b\n\n- c") == "<ul><li><p>a</p></li><li><p>b</p></li><li><p>c</p></li></ul>"
    assert _markdown_html("![chart](https://x/y.png)") == ('<p><a href="https://x/y.png" target="_blank" '
                                                          'rel="noopener noreferrer">chart</a></p>')  # never loaded


def test_markdown_keeps_citations_in_place():
    def cited(text, *parts):
        spans = [(text.index(part), part, sources) for part, sources in parts]
        return _markdown_html(text, [Citation(start, start + len(part), part, sources) for start, part, sources in spans])

    text = "**Refunds** take *5-7 days*.\n\n- Bank: **10 days**\n- Card: 5 days\n\nOther."
    out = cited(text, ("**Refunds** take *5-7 days*.", [1]), ("- Bank: **10 days**\n- Card: 5 days", [2, 3]))
    assert out.startswith('<p><strong><span class="cite">Refunds</span></strong><span class="cite"> take </span>'
                          '<em><span class="cite">5-7 days<sup>[1]</sup></span></em>')
    assert '<li><span class="cite">Card: 5 days<sup>[2][3]</sup></span></li></ul><p>Other.</p>' in out
    out = cited("See the [refund policy](https://x.com).", ("See the [refund policy](https://x.com)", [1]))
    assert out.endswith('<span class="cite">refund policy</span></a><sup>[1]</sup>.</p>')  # after the markup
    inline = _markdown_html("Use `x[1]` here [2].", [Citation(0, 9, "", [2])], inline=True)
    assert "<code>x[1]</code>" in inline.replace('<span class="cite">', "").replace("</span>", "")  # code keeps its [1]
    assert inline.endswith("here <sup>[2]</sup>.</p>")


def test_answer_text_keeps_code_and_tables_whole():
    text = "Steps:\n1. " + "word " * 25 + "\n\n```\n" + "x" * 120 + "\n```\n| a | " + "b" * 110 + " |"
    lines = _answer_lines(text, 60, "  ")
    assert lines[1].startswith("  1. word") and lines[2].startswith("     word")  # under the item's text
    assert "  " + "x" * 120 in lines and lines[-1] == "  | a | " + "b" * 110 + " |"


@pytest.mark.parametrize("text, first", [
    ("rerank", "reranker"), ("temprature", "temperature"), ("search type", "search_type"),
    ("encrypts the conversation", "kms_key"), ("latency", "latency"), ("guardrail", "guardrail_id"), ("topP", "top_p"),
])
def test_search_finds_settings_by_name_path_or_what_they_do(text, first):
    assert SCHEMA.search(text)[0].key == first


def test_search_limits_and_blanks():
    assert SCHEMA.search("") == list(F.values()) and SCHEMA.search("xyzzy") == []
    assert len(SCHEMA.search("rerank", 2)) == 2


def test_diff_and_normalize_settings():
    assert _diff({"n": 5, "temperature": 0.2, "top_p": 0.9}, {"n": 8, "temperature": 0.2, "stop": ["END"]}) == [
        "n 5 → 8", 'stop = ["END"] (added)', "top_p removed"]
    assert normalize_settings({"temperature": None, "topP": "0.5", "MAX_TOKENS": 100}, SCHEMA) == {
        "top_p": 0.5, "max_tokens": 100}
    with pytest.raises(ValueError) as err:
        normalize_settings({"temprature": 1, "n": 0}, SCHEMA)
    assert "Did you mean 'temperature'" in str(err.value) and "n can be 1 to 100" in str(err.value)


def test_question_text_limits():
    assert _question_text("  How long?  ") == "How long?"
    with pytest.raises(ValueError, match="Pass a question"):
        _question_text("   ")
    with pytest.raises(ValueError, match="up to 1,000 characters, and this one has 1,001"):
        _question_text("x" * 1001)


def test_schema_needs_a_boto3_that_knows_retrieve_and_generate():
    class Old:
        def operation_model(self, name):
            raise KeyError(name)

    with pytest.raises(ValueError, match="doesn't know RetrieveAndGenerate: pip install -U boto3"):
        request_schema(Old())


def test_retrieve_request_is_the_same_search_without_the_model():
    values = normalize_settings({"n": 20, "search_type": "hybrid", "reranker": "cohere", "rerank_n": 4,
                                 "where": {"team": "billing"}, "temperature": 0.2, "query_decomposition": True,
                                 "kms_key": "arn:aws:kms:us-east-1:1:key/k"}, SCHEMA)
    assert list(retrieve_settings(values, SCHEMA)) == ["n", "search_type", "filter", "reranker", "rerank_n"]
    rag = build_request("q?", KB_ID, SONNET_PROFILE, values, SCHEMA, region="us-east-1", data_sources=[DS_ID],
                        files=[REFUND_PDF])
    params = build_retrieve_request("q?", KB_ID, values, SCHEMA, region="us-east-1", data_sources=[DS_ID],
                                    files=[REFUND_PDF])
    assert list(params) == ["knowledgeBaseId", "retrievalQuery", "retrievalConfiguration"]
    assert params["retrievalQuery"] == {"text": "q?"} and params["knowledgeBaseId"] == KB_ID
    assert params["retrievalConfiguration"] == rag[KB[0]][KB[1]]["retrievalConfiguration"]  # the very same search
    assert validate_request(params, SCHEMA) == []
    picked, back = settings_from_request(params, SCHEMA)  # Edit JSON reads it back
    assert picked == {"question": "q?", "knowledgeBaseId": KB_ID, "dataSources": [DS_ID], "files": [REFUND_PDF],
                      "retrieve_only": True}
    assert back == retrieve_settings(values, SCHEMA)
    assert list(build_retrieve_request("q", KB_ID, {"temperature": 0.2}, SCHEMA)) == ["knowledgeBaseId",
                                                                                      "retrievalQuery"]
    params["nextToken"] = "abc"
    with pytest.raises(ValueError, match="The chat doesn't send nextToken"):
        settings_from_request(params, SCHEMA)
    params.pop("nextToken")
    params["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResult"] = 3
    assert any('Unknown parameter in retrievalConfiguration.vectorSearchConfiguration: "numberOfResult"' in p
               for p in validate_request(params, SCHEMA))  # checked against Retrieve's own shape


def test_python_call_for_a_retrieve_request():
    params = build_retrieve_request("How long?", KB_ID, {"n": 8}, SCHEMA)
    code = python_call(params, "us-east-1")
    compile(code, "<cell>", "exec")
    assert ast.literal_eval(code.split("retrieve(**", 1)[1].rsplit(")\nfor", 1)[0]) == params
    assert "retrieve_and_generate" not in code and "response['retrievalResults']" in code


def search(passages=(REFUND, BANK, SHIPPING), settings=None, question="How long?", **kwargs):
    a = Answer(question, "", sources=parse_retrieve({"retrievalResults": list(passages)}), settings=settings or {"n": 5},
               retrieve_only=True)
    for name, value in kwargs.items():
        setattr(a, name, value)
    return a


def test_search_findings_say_what_to_widen():
    assert answer_findings(search()) == []
    (level, message), = answer_findings(search([], {"n": 5, "filter": {"equals": {"key": "a", "value": 1}}},
                                               files=[REFUND_PDF], data_sources={DS_ID: "faq"}))
    assert level == "warn" and message.startswith("Nothing came back, so an answer would have nothing to go on.")
    assert "unset('filter')" in message and "use(files='all')" in message and "use(data_source='all')" in message
    assert "files() shows each one's status" in answer_findings(search([]))[0][1]
    (level, message), = answer_findings(search([REFUND, BANK, REFUND]))
    assert level == "info" and "All 3 passages come from one file (refund-policy.pdf)" in message
    assert "set(n=10)" in message and "set(search_type='HYBRID')" in message
    outside = answer_findings(search(files=[RETURNS_MD]))
    assert outside[0][1].startswith("3 passages (#1, #2, #3) came from outside file 'returns.md'")


def test_a_search_and_an_answer_to_the_same_question_line_up():
    a, s = answer(), search()
    assert cited_ranks(s, a) == {1: 1, 2: 2}
    (level, message), = compare_findings(s, a)
    assert level == "info" and message == ("The answer cites 2 of the 3 passages the search found (#1 as [1], #2 as "
                                           "[2]); the other one wasn't cited.")
    flipped = search([{**SHIPPING}, {**BANK}])  # the answer's [1] isn't among them
    assert cited_ranks(flipped, a) == {2: 2}
    found = compare_findings(flipped, a)
    assert "#2 as [2]" in found[0][1] and "Source [1] of the answer isn't among the search's passages" in found[1][1]
    refused = compare_findings(s, answer("Sorry, I am unable to assist you with this request."))
    assert refused[0][0] == "warn" and "yet the search found 3 passages" in refused[0][1]
    assert "set(prompt=...)" in refused[0][1]
    # without chunk IDs, the same file and text is the same passage
    bare = Passage(1, "  Refunds are issued within 5-7 business days of receiving the returned item.",
                   uri="s3://docs/policies/refund-policy.pdf")
    assert cited_ranks(Answer("q", "", sources=[bare], retrieve_only=True), a) == {1: 1}


def test_a_search_costs_the_embedding_and_the_reranking():
    s = search(question="How long?")
    assert answer_cost(s) == pytest.approx(0.02 * estimate("How long?") / 1e6)
    s.settings = {"reranker": "amazon"}
    assert answer_cost(s) == pytest.approx(0.001 + 0.02 * estimate("How long?") / 1e6)


# ------------------------------------------------- test runs: a list of questions, and the setup as code

REFUSED = "Sorry, I am unable to assist you with this request."


def asked(question, a=None, expected=None, **kwargs):
    """A test question that came back with `a` (an answer citing refund-policy.pdf p.3 and p.4 by default)."""
    a = a or answer()
    a.question = question
    return BatchItem(question, expected, answer=a, **kwargs)


def run_of(*items, settings=None, **fields):
    fields = {"kb_id": KB_ID, "kb_name": "support-docs", "model": "us.anthropic.claude-sonnet-5", **fields}
    return Batch(items=list(items), settings=settings or {"n": 5}, **fields)


def test_parse_questions_reads_a_pasted_list():
    text = ("1. How long do refunds take? | refund-policy.pdf\n- Can I return a digital product?\n\n# a comment\n"
            "Q: Do you ship to the UK?\tshipping.md | faq/returns.md\n   * What does E1234 mean?  \n| no question\n"
            "2024 refund rules?")
    cases = parse_questions(text)
    assert cases == [("How long do refunds take?", "refund-policy.pdf"), ("Can I return a digital product?", None),
                     ("Do you ship to the UK?", ["shipping.md", "faq/returns.md"]), ("What does E1234 mean?", None),
                     ("2024 refund rules?", None)]  # a number that isn't a list mark stays
    assert parse_questions(format_questions(cases)) == cases
    assert format_questions([("Two\nlines?", None)]) == "Two lines?"
    assert parse_questions("") == [] and parse_questions(None) == []


def test_question_list_takes_what_a_notebook_has():
    assert question_list(["How long? | refund-policy.pdf", "  ", None, "Can I return it?"]) == [
        ("How long?", "refund-policy.pdf"), ("Can I return it?", None)]
    assert question_list([("How long?", ["a.pdf", "b.pdf"]), {"question": "Return?", "expected": ""},
                          {"question": "Ship?", "source": "shipping.md"}]) == [
        ("How long?", ["a.pdf", "b.pdf"]), ("Return?", None), ("Ship?", "shipping.md")]
    assert question_list("One?\nTwo? | two.md") == [("One?", None), ("Two?", "two.md")]
    assert question_list(Batch(items=[BatchItem("One?", "one.md"), BatchItem("Two?")])) == [("One?", "one.md"),
                                                                                             ("Two?", None)]
    with pytest.raises(ValueError, match="No questions to ask: pass a list, like ask_all"):
        question_list(["", None])
    with pytest.raises(ValueError, match="takes questions as text with one per line"):
        question_list([42])
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"question": ["One?", "Two?", None], "expected": ["one.md", float("nan"), "x"]})
    assert question_list(frame) == [("One?", "one.md"), ("Two?", None)]
    assert question_list(pd.Series(["One?", "Two? | two.md"])) == [("One?", None), ("Two?", "two.md")]


def test_expected_sources_and_how_each_question_did():
    a = answer()  # cites refund-policy.pdf p.3 as [1] and p.4 as [2]
    assert expected_at(a, "refund-policy") == 1 and expected_at(a, ["nope", "10 BUSINESS days"]) == 2
    assert expected_at(a, "shipping.md") is None and expected_at(a, None) is None and expected_at(a, "  ") is None
    assert expected_at(search(), "SHIPPING.MD") == 3  # a search: its rank

    def verdict(**kwargs):
        return item_verdict(BatchItem("q", **kwargs))

    thin = answer()
    thin.citations = thin.citations[:1]
    thin.text += " " + "More words that no source backs up." * 3
    assert verdict(answer=answer()) == ("answered", "ok")
    assert verdict(answer=answer(), expected="shipping.md") == ("expected not cited", "warn")
    assert verdict(answer=answer(REFUSED)) == ("unable to assist", "warn")
    assert verdict(answer=answer("")) == ("empty answer", "warn")
    assert verdict(answer=answer("They take a week.")) == ("no citations", "warn")
    assert verdict(answer=thin) == ("partly grounded", "warn")
    assert verdict(answer=answer(guardrail_action="INTERVENED")) == ("guardrail stepped in", "warn")
    assert verdict(error="Too long.") == ("not sent", "warn")
    assert verdict(error="Rate exceeded.", error_code="ThrottlingException") == ("failed", "bad")
    assert verdict() == ("not asked", "")
    assert verdict(answer=search()) == ("3 passages", "ok")
    assert verdict(answer=search(), expected="bank transfer") == ("found #2", "ok")
    assert verdict(answer=search(), expected="nope") == ("expected not found", "warn")
    assert verdict(answer=search([])) == ("nothing found", "warn")


def test_batch_findings_say_what_to_try():
    assert batch_findings(run_of(asked("How long?"), asked("And bank transfers?"))) == []
    run = run_of(asked("How long?", expected="refund-policy"), asked("Digital goods?", answer(REFUSED)),
                 asked("Shipping?", answer("A week."), expected="shipping"),
                 BatchItem("Third?", error="Rate exceeded.", error_code="ThrottlingException"),
                 BatchItem("Fourth?", error="Rate exceeded.", error_code="ThrottlingException"),
                 BatchItem("x" * 1001, error="Bedrock takes questions of up to 1,000 characters, and this one has "
                                             "1,001. Shorten it, or ask it as two questions."))
    found = dict((m.split(":")[0], (level, m)) for level, m in batch_findings(run))
    level, message = found["2 of 6 questions failed (ThrottlingException ×2)"]
    assert level == "warn" and message.endswith("Rate exceeded. Bedrock throttled them: ask fewer at a time "
                                                "(ask_all(workers=1)).")
    level, message = found["1 question ('" + "x" * 47 + "…') wasn't sent"]
    assert level == "warn" and "Bedrock takes questions of up to 1,000 characters" in message
    level, message = found["1 of 3 answers is Bedrock's \"unable to assist\" reply or empty ('Digital goods?')"]
    assert level == "warn" and "retrieve('Digital goods?') shows what the search finds for it" in message
    assert "set(n=10)" in message and "set(search_type='HYBRID')" in message and "ask_all() again" in message
    level, message = found["1 of 3 answers cites no source ('Shipping?')"]
    assert level == "warn" and "the model's own knowledge" in message
    level, message = found["The expected source isn't cited in 1 of 2 answers checked ('Shipping?' expected "
                           "'shipping', cited nothing). retrieve('Shipping?') shows whether the search finds it; if it "
                           "doesn't, check the file is indexed (files()), then try set(search_type='HYBRID') or "
                           "set(n=10)."]
    assert level == "warn"
    thin = answer(output_tokens=19)
    thin.citations = thin.citations[:1]
    thin.text += " " + "More words that no source backs up." * 3
    more = run_of(asked("Blocked?", answer(guardrail_action="INTERVENED", files=[RETURNS_MD])),
                  asked("Thin?", thin), BatchItem("Not asked?", request={"input": {"text": "Not asked?"}}),
                  settings={"max_tokens": 20}, files=[RETURNS_MD], skipped=4, stopped=True)
    thin.files = [RETURNS_MD]  # asked about returns.md only, as ask_all() records it
    text = " | ".join(m for _, m in batch_findings(more))
    for part in ("A guardrail stepped in on 1 question ('Blocked?')", "1 of 2 answers is less than half backed by "
                 "citations ('Thin?')", "1 of 2 answers reached max_tokens (20) and may be cut off ('Thin?'): raise "
                 "it (set(max_tokens=1024))", "2 of 2 questions got passages from outside file 'returns.md'",
                 "Stopped before 1 question was asked: ask_all() asks the whole list again.",
                 "Only the first 3 of 7 questions were asked (limit=3): ask_all(limit=7) asks them all."):
        assert part in text, part
    searches = run_of(asked("How long?", search(), expected="refund-policy"), asked("Holiday shipping?", search([])),
                      asked("Bank?", search(), expected="bank transfer"), asked("Nope?", search(), expected="nope"),
                      retrieve_only=True)
    text = " | ".join(m for _, m in batch_findings(searches))
    for part in ("Nothing came back for 1 of 4 questions ('Holiday shipping?'), so an answer would have nothing to go "
                 "on. Try to retrieve more passages (set(n=10))", "The expected source wasn't among the passages "
                 "found for 1 of 3 questions checked ('Nope?' expected 'nope'; first came refund-policy.pdf p.3, "
                 "refund-policy.pdf p.4)", "1 question found the expected source below the first passage (MRR 0.50; "
                 "1.00 means it always came first). A reranker (set(reranker='cohere'))"):
        assert part in text, part


def test_batch_changes_say_which_questions_did_better_or_worse():
    before = run_of(asked("How long?"), asked("Digital goods?", answer(REFUSED)), asked("Bank?"))
    after = run_of(asked("How long?", answer(REFUSED)), asked("Digital goods?"), asked("bank?"), settings={"n": 10},
                   model="us.anthropic.claude-opus-5")
    (level, message), = batch_changes(before, after)
    assert level == "warn" and message.startswith("Since the last run (n 5 → 10; model claude-sonnet-5 → "
                                                  "claude-opus-5): ")
    assert "1 question did better ('Digital goods?' unable to assist → answered)" in message
    assert "1 question did worse ('How long?' answered → unable to assist)" in message
    assert batch_changes(before, before) == [("info", "Since the last run (the same setup): all 3 questions did as "
                                                      "before.")]
    fewer = run_of(asked("How long?"), asked("Elsewhere?"))
    assert batch_changes(before, fewer)[0][1] == ("Since the last run (the same setup; the 1 question both runs "
                                                  "asked): the question did as before.")
    assert batch_changes(before, run_of(asked("Other?"))) == []


def test_batch_estimate_prices_a_run_before_it_runs():
    qs = ["How long do refunds take?", "Can I return a digital product?"]
    tokens = sum(estimate(q) + estimate(DEFAULT_PROMPT) + 5 * 300 for q in qs)
    embedding = sum(0.02 * estimate(q) / 1e6 for q in qs)
    assert batch_estimate(qs, {"n": 5}, "us.anthropic.claude-sonnet-5") == pytest.approx(
        (tokens * 2.20 + 600 * 11.00) / 1e6 + embedding)
    assert batch_estimate(qs, {"n": 5}, "acme.unknown-v1") is None
    assert batch_estimate(qs, {"reranker": "cohere"}, "", retrieve_only=True) == pytest.approx(2 * 0.002 + embedding)


def test_sweep_setups_makes_every_combination():
    assert sweep_setups({"n": [5, 10], "search_type": ["SEMANTIC", "HYBRID"]}) == [
        {"n": 5, "search_type": "SEMANTIC"}, {"n": 5, "search_type": "HYBRID"},
        {"n": 10, "search_type": "SEMANTIC"}, {"n": 10, "search_type": "HYBRID"}]
    # one value goes in every setup, None leaves a setting out, and names are forgiving
    assert sweep_setups({"Model": ("haiku", "sonnet"), "temperature": 0.2, "reranker": [None, "cohere"]}) == [
        {"model": "haiku", "temperature": 0.2, "reranker": None}, {"model": "haiku", "temperature": 0.2,
                                                                     "reranker": "cohere"},
        {"model": "sonnet", "temperature": 0.2, "reranker": None}, {"model": "sonnet", "temperature": 0.2,
                                                                      "reranker": "cohere"}]
    assert sweep_setups({"data source": ["faq", "all"], "n": {8, 4}}) == [
        {"data_source": "faq", "n": 4}, {"data_source": "faq", "n": 8}, {"data_source": "all", "n": 4},
        {"data_source": "all", "n": 8}]
    # whole setups, each combined with the lists, and the same setup only once
    assert sweep_setups({"n": [5]}, [{"search_type": "HYBRID"}, {"reranker": "cohere"}, {"search_type": "HYBRID"}]) == [
        {"search_type": "HYBRID", "n": 5}, {"reranker": "cohere", "n": 5}]
    assert sweep_setups(setups=[{"n": 5}, {"n": 10, "reranker": "cohere"}]) == [{"n": 5}, {"n": 10, "reranker": "cohere"}]
    with pytest.raises(ValueError, match="That's one setup, so there's nothing to compare"):
        sweep_setups({"n": 5, "temperature": 0.2})
    many = {"n": [1, 2, 3], "search_type": ["A", "B", "C"], "model": ["x", "y"]}
    with pytest.raises(ValueError, match=re.escape("That's 18 setups (3 n × 3 search_type × 2 model), and a sweep asks "
                                                   "up to 16: try fewer values, or pass max_setups=18.")):
        sweep_setups(many)
    assert len(sweep_setups(many, limit=None)) == 18
    with pytest.raises(ValueError, match="n has no values to try"):
        sweep_setups({"n": []})
    with pytest.raises(ValueError, match="setups takes a list of dicts"):
        sweep_setups(setups=["n=5"])


def test_parse_variations_reads_the_try_variations_box():
    text = ('n = 5, 10\n# a comment\n\nsearch_type: SEMANTIC, "HYBRID"\nreranker = none, cohere\n'
            'filter = {"team": "billing", "year": 2024}, none\nData source = all, "faq, archived"\n'
            'prompt = "Hi, $search_results$"\nmodel: arn:aws:bedrock:us-east-1::foundation-model/x, haiku')
    assert parse_variations(text) == {
        "n": ["5", "10"], "search_type": ["SEMANTIC", "HYBRID"], "reranker": [None, "cohere"],
        "filter": ['{"team": "billing", "year": 2024}', None], "data_source": ["all", "faq, archived"],
        "prompt": ["Hi, $search_results$"], "model": ["arn:aws:bedrock:us-east-1::foundation-model/x", "haiku"]}
    grid = {"n": [5, 10], "reranker": [None, "cohere"], "data_source": ["faq, archived"]}
    assert format_variations(grid) == 'n = 5, 10\nreranker = none, cohere\ndata_source = "faq, archived"'
    assert parse_variations(format_variations(grid)) == {"n": ["5", "10"], "reranker": [None, "cohere"],
                                                         "data_source": ["faq, archived"]}
    assert parse_variations("") == {} and parse_variations("# nothing yet") == {}
    for typed, message in (("n 5, 10", "Line 1: write a setting, =, then the values to try, like n = 5, 10"),
                           ("= 5", "Line 1: write a setting"), ("n = 5\n\nn =", "Line 3: n has no values"),
                           ("n = 5\nN = 10", "Line 2: N is on line 1 already")):
        with pytest.raises(ValueError, match=re.escape(message)):
            parse_variations(typed)


def test_apply_setup_changes_the_settings_it_starts_from():
    base = {"n": 5, "temperature": 0.2}
    assert apply_setup(base, {"number_of_results": "10", "temperature": None, "search_type": "hybrid",
                              "model": "sonnet", "data_source": "faq"}, SCHEMA) == {"n": 10, "search_type": "HYBRID"}
    assert base == {"n": 5, "temperature": 0.2}  # changed in a copy
    with pytest.raises(ValueError, match="n takes a number"):
        apply_setup(base, {"n": "lots"}, SCHEMA)


def test_runs_are_scored_and_ranked_on_the_questions_they_share():
    def run(moon, cost):
        return run_of(asked("How long?", expected="refund-policy", cost=cost), asked("Bank?", cost=cost),
                      asked("Moon?", answer() if moon else answer(REFUSED), cost=cost))

    good, unable, dear = run(True, 0.002), run(False, 0.001), run(True, 0.003)
    s = run_score(unable)
    assert (s.questions, s.answered, s.checked, s.hits, s.failed) == (3, 2, 1, 1, 0)
    assert s.cost == pytest.approx(0.003) and s.per_question() == pytest.approx(0.001)
    assert s.grounded == pytest.approx(answer().grounded_share) and s.mrr is None
    assert [b for b, _ in rank_runs([unable, dear, good])] == [good, dear, unable]  # the cheaper first when as good
    stopped = run_of(asked("How long?", expected="refund-policy", cost=0.001), BatchItem("Bank?", request={"x": 1}),
                     BatchItem("Moon?", error="Rate exceeded.", error_code="ThrottlingException"))
    assert shared_questions([good, stopped]) == {"how long?", "moon?"}  # a question not asked isn't compared
    s = run_score(stopped, shared_questions([good, stopped]))
    assert (s.questions, s.answered, s.failed, s.checked, s.hits) == (2, 1, 1, 1, 1)
    assert run_score(run_of(asked("q?"))).cost is None  # a price that isn't known
    first = run_of(asked("How long?", search(), expected="refund-policy"), retrieve_only=True)
    second = run_of(asked("How long?", search(), expected="bank transfer"), retrieve_only=True)
    assert [(s.hits, s.mrr) for s in map(run_score, (first, second))] == [(1, 1.0), (1, 0.5)]
    assert rank_runs([second, first])[0][0] is first


def test_ranking_findings_name_the_best_setup_and_what_each_setting_changed():
    def setup(n, kind, moon, cost):
        return run_of(asked("How long?", expected="refund-policy", cost=cost),
                      asked("Moon?", answer() if moon else answer(REFUSED), cost=cost),
                      settings={"n": n, "search_type": kind})

    runs = [setup(5, "SEMANTIC", False, 0.001), setup(5, "HYBRID", False, 0.001), setup(10, "SEMANTIC", True, 0.002),
            setup(10, "HYBRID", True, 0.002)]
    found = ranking_findings(runs, now=runs[0])
    assert found[0] == ("warn", "n=10 · search_type=SEMANTIC did better than your setup now (n=5 · search_type=SEMANTIC): "
                                "it answers 2 of 2 questions, against 1 of 2, for about $0.10 more per 100 questions "
                                "(estimate). use_run(3) switches to it.")
    assert ("info", "n=10 did best in each of the 2 groups of setups that differ only in n.") in found
    assert ("info", "search_type made no difference: each of the 2 groups of setups that differ only in search_type "
                    "did the same on these questions.") in found
    assert len(found) == 3 and ranking_findings(runs, now=runs[0], brief=True) == found[:1]
    (level, message), = [f for f in ranking_findings(runs, now=runs[2]) if "your setup" in f[1].lower()]
    assert level == "info" and message.startswith("Your setup now (n=10 · search_type=SEMANTIC) did as well as any "
                                                  "setup tried")
    # one value: the lead could be chance; an earlier run with the setup in use; nothing different
    two = ranking_findings(runs[1:3], number=lambda b: 7)
    assert two[0] == ("info", "n=10 · search_type=SEMANTIC did best: it answers 2 of 2 questions, against 1 of 2 for "
                              "the worst setup (n=5 · search_type=HYBRID). use_run(7) switches to it.")
    assert two[1][1].startswith("n=10 · search_type=SEMANTIC leads n=5 · search_type=HYBRID by one question, which can "
                                "be chance")
    earlier = ranking_findings(runs[2:], now=runs[0])[0]
    assert earlier == ("warn", "search_type=SEMANTIC did better than your setup now (run 3: n=5 · "
                               "search_type=SEMANTIC): it answers 2 of 2 questions, against 1 of 2, for about $0.10 "
                               "more per 100 questions (estimate). use_run(1) switches to it.")
    unasked = run_of(BatchItem("How long?", request={"x": 1}), BatchItem("Moon?", request={"x": 1}))
    assert not any("your setup" in m for _, m in ranking_findings(runs[1:3], now=unasked))  # nothing to compare on
    same = ranking_findings(runs[:2])
    assert same[0][1].startswith("Every setup did as well as the others on these 2 questions (each cites the expected "
                                 "source in 1 of 1 question), so what was tried made no difference here.")
    assert same[1] == ("warn", "1 question didn't work with any setup ('Moon?'; unable to assist with the best one): "
                               "nothing tried here fixes it, so the documents may not hold the answer, or a file isn't "
                               "indexed. retrieve('Moon?') shows what the search finds, and files() whether a file is "
                               "indexed.")
    refused = run_of(BatchItem("How long?", error="HYBRID isn't supported.", error_code="ValidationException"),
                     BatchItem("Moon?", error="HYBRID isn't supported.", error_code="ValidationException"),
                     settings={"n": 5, "search_type": "HYBRID"})
    text = " | ".join(m for _, m in ranking_findings([runs[0], refused], explain=lambda code, m: m + " Unset it."))
    assert ("search_type=HYBRID (run 2): 2 of 2 questions failed (ValidationException): HYBRID isn't supported. "
            "Unset it.") in text
    assert ranking_findings(runs[:1]) == []


def test_setup_labels_say_what_differs():
    plain = run_of(asked("q?"))
    picked = run_of(asked("q?"), model="us.anthropic.claude-opus-5", data_sources={DS_ID: "faq"}, files=[REFUND_PDF],
                    settings={"n": 10, "reranker": "cohere", "filter": {"equals": {"key": "team", "value": "billing"}},
                              "prompt": "x" * 50 + "$search_results$"})
    varied = varied_setups([plain, picked])
    assert set(varied) == {"model", "data_source", "files", "n", "reranker", "filter", "prompt"}
    assert setup_label(plain, varied) == ("model=claude-sonnet-5 · n=5 · data_source=all · files=all · reranker=none · "
                                          "no filter · prompt=default")
    assert setup_label(picked, varied, lambda m: "Claude Opus 5") == (
        'model=Claude Opus 5 · n=10 · data_source=faq · files=refund-policy.pdf · reranker=cohere · where team = '
        '"billing" · prompt=#2 (66 characters)')
    assert setup_label(plain, varied_setups([plain, plain])) == "the same setup"


def test_runs_are_saved_as_json_lines_and_read_back():
    a = answer()
    a.response = {"output": {"text": a.text}}
    batch = run_of(asked("How long?", a, expected=["refund-policy", "x"], cost=0.002, request={"input": {"text": "x"}}),
                   BatchItem("Bank?", error="Rate exceeded.", error_code="ThrottlingException"),
                   id="abc123", label="baseline", started=NOW, data_sources={DS_ID: "docs-s3"}, files=[REFUND_PDF],
                   settings={"n": 5, "filter": {"equals": {"key": "team", "value": "billing"}}}, seconds=2.5)
    line = json.dumps(run_record(batch))
    assert '"response"' not in line  # Bedrock's raw response isn't kept: the answer and its sources are
    (back,), problems = read_runs(["", line])
    assert problems == [] and (back.id, back.label, back.started, back.seconds) == ("abc123", "baseline", NOW, 2.5)
    assert (back.settings, back.data_sources, back.files) == (batch.settings, batch.data_sources, batch.files)
    first, failed = back.items
    assert first.expected == ["refund-policy", "x"] and first.cost == 0.002 and first.request == {"input": {"text": "x"}}
    assert first.answer.text == a.text and first.answer.cited == a.cited and first.answer.response == {}
    assert first.answer.sources[0].source == "refund-policy.pdf p.3" and first.found == 1
    assert (failed.error_code, failed.answer) == ("ThrottlingException", None)
    assert run_score(back) == run_score(batch) and item_verdict(first) == item_verdict(batch.items[0])
    newer = json.loads(line)
    newer["added_later"] = newer["items"][0]["answer"]["added_later"] = True
    assert run_from_record(newer).id == "abc123"  # what a newer version adds is left out
    broken = {**json.loads(line), "items": [{"question": "q?", "answer": {"text": "no question"}}]}
    _, problems = read_runs(["{", json.dumps({"format": "other"}), json.dumps({**json.loads(line), "id": ""}),
                             json.dumps(broken)])
    assert problems[:3] == ["line 1 isn't JSON (Expecting property name enclosed in double quotes)",
                            "line 2 is not a test run saved by save_runs() (format 'other', not "
                            "'aws-analyzer/bedrock-chat-run/1')", "line 3 is a test run without an id"]
    assert problems[3].startswith("line 4 is a test run that can't be read back (TypeError: ")


def test_python_script_asks_the_questions_with_boto3_alone(monkeypatch, capsys):
    values = normalize_settings({"n": 8, "temperature": 0.2, "prompt": "Don't guess.\n$search_results$\n"
                                 "$output_format_instructions$"}, SCHEMA)
    params = build_request("How long?", KB_ID, SONNET_PROFILE, values, SCHEMA, session_id="s-1", region="us-east-1")
    code = python_script(params, "us-east-1", ["How long do refunds take?", "Can I return it, and how?"],
                         about="From the tests.")
    assert "# From the tests." in code and "boto3.client('bedrock-agent-runtime', region_name='us-east-1')" in code
    config = ast.literal_eval(code.split("CONFIG = ", 1)[1].split("\n\n\ndef ", 1)[0])
    assert config == config_of(params) and "sessionId" not in config and "input" not in config
    runtime = fakes()["bedrock-agent-runtime"]
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda service, **_: runtime))
    exec(compile(code, "<script>", "exec"), {})  # the script as a user would run it, against the fake Bedrock
    out = capsys.readouterr().out
    assert out.startswith("Q: How long do refunds take?\nA: Refunds take 5-7 business days.") and out.count("Q: ") == 2
    assert "   cited: s3://docs/policies/refund-policy.pdf\n" in out
    sent = runtime.called("retrieve_and_generate")
    assert [p["input"]["text"] for p in sent] == ["How long do refunds take?", "Can I return it, and how?"]
    assert all({k: v for k, v in p.items() if k != "input"} == config for p in sent)
    search_params = build_retrieve_request("How long?", KB_ID, values, SCHEMA)
    code = python_script(search_params)  # no questions: the request's own
    assert "boto3.client('bedrock-agent-runtime')" in code and "questions = ['How long?']" in code
    exec(compile(code, "<script>", "exec"), {})
    out = capsys.readouterr().out
    assert out.startswith("Q: How long?\n   0.810  s3://docs/policies/refund-policy.pdf  Refunds are issued within")
    assert runtime.called("retrieve") == [search_params]


def test_config_json_and_the_aws_cli_command():
    values = normalize_settings({"n": 8, "prompt": "Don't guess. $search_results$ $output_format_instructions$",
                                 "kms_key": "arn:aws:kms:us-east-1:1:key/k"}, SCHEMA)
    params = build_request("How long?", KB_ID, SONNET_PROFILE, values, SCHEMA, session_id="s-1")
    assert json.loads(config_json(params)) == config_of(params) == {
        k: params[k] for k in ("retrieveAndGenerateConfiguration", "sessionConfiguration")}
    command = cli_command(params, "eu-west-1", "Can I return it, and how?")
    assert command.startswith("aws bedrock-agent-runtime retrieve-and-generate \\\n  --region eu-west-1 \\\n  "
                              "--query output.text --output text \\\n  --cli-input-json '{")
    words = shlex.split(command.replace("\\\n", " "))
    sent = json.loads(words[words.index("--cli-input-json") + 1])  # the quote in the prompt survives the shell
    assert sent == {"input": {"text": "Can I return it, and how?"}, **config_of(params)}
    assert validate_request(sent, SCHEMA) == []
    search_params = build_retrieve_request("How long?", KB_ID, values, SCHEMA)
    words = shlex.split(cli_command(search_params).replace("\\\n", " "))
    assert words[:3] == ["aws", "bedrock-agent-runtime", "retrieve"] and "--region" not in words
    assert json.loads(words[words.index("--cli-input-json") + 1]) == search_params  # its own question
    assert 'retrievalResults[].[score, metadata."x-amz-bedrock-kb-source-uri"]' in words


def test_code_is_highlighted_and_escaped():
    command = cli_command(build_request("<b>?", KB_ID, SONNET_PROFILE, {}, SCHEMA), "us-east-1")
    out = chatmod._shell_html(command)
    assert plain(out) == command and '<span class="pa">--cli-input-json</span>' in out
    assert '<span class="jk">&quot;knowledgeBaseId&quot;</span>' in out and "<b>" not in out
    assert chatmod._code_html('{"a": 1}', "json") == '{<span class="jk">&quot;a&quot;</span>: <span class="jn">1</span>}'
    assert chatmod._code_html("<x>", "other") == "&lt;x&gt;"


# ----------------------------------------------------------------------------- AWS


class Stubs:
    """Real clients with a botocore Stubber on each: calls must be queued in order, and parameters are checked."""

    def __init__(self):
        names = ("bedrock-agent", "bedrock-agent-runtime", "bedrock")
        self.clients = {name: boto3.client(name, region_name="us-east-1") for name in names}
        self.stubs = {name: Stubber(client) for name, client in self.clients.items()}
        for stub in self.stubs.values():
            stub.activate()
        self.agent, self.runtime, self.bedrock = (self.stubs[n] for n in names)

    def analyzer(self):
        return BedrockChatAnalyzer(clients=dict(self.clients))

    def list_kbs(self):
        self.agent.add_response("list_knowledge_bases", {"knowledgeBaseSummaries": [kb_summary()]}, {})

    def models(self):
        self.bedrock.add_response("list_foundation_models", {"modelSummaries": MODEL_LIST},
                                  {"byOutputModality": "TEXT"})
        self.bedrock.add_response("list_inference_profiles", {"inferenceProfileSummaries": PROFILES}, {})

    def done(self):
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()
            stub.deactivate()


@pytest.fixture
def stubs():
    s = Stubs()
    yield s
    s.done()


def test_ask_sends_exactly_the_settings_and_reads_the_answer(stubs):
    stubs.list_kbs()
    stubs.models()
    expected = {
        "input": {"text": "How long do refunds take?"},
        "retrieveAndGenerateConfiguration": {"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": {
            "knowledgeBaseId": KB_ID, "modelArn": SONNET_PROFILE,
            "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 8,
                                                                     "overrideSearchType": "HYBRID"}},
            "generationConfiguration": {"inferenceConfig": {"textInferenceConfig": {"maxTokens": 500}}}}},
    }
    stubs.runtime.add_response("retrieve_and_generate", rag_resp(), expected)
    core = stubs.analyzer()
    a = core.ask("Support-Docs", "How long do refunds take?", {"n": "8", "search_type": "hybrid", "maxTokens": 500},
                 model="sonnet")
    assert (a.kb_id, a.kb_name, a.model, a.session_id) == (KB_ID, "support-docs", "us.anthropic.claude-sonnet-5",
                                                           "session-1")
    assert a.request == expected and a.response["output"]["text"] == ANSWER and not a.streamed
    assert a.settings == {"n": 8, "search_type": "HYBRID", "max_tokens": 500}
    assert a.cited == [1, 2] and a.notes == []
    # 8 passages of about the cited ones' size, the question and the default prompt: an estimate.
    per = sum(estimate(p.text) for p in a.sources) // 2
    assert a.input_tokens == estimate(a.question) + estimate(DEFAULT_PROMPT) + 8 * per
    assert a.output_tokens == estimate(ANSWER)


def test_ask_starts_a_new_session_when_bedrock_ended_the_old_one(stubs):
    stubs.list_kbs()
    stubs.models()
    stubs.runtime.add_client_error("retrieve_and_generate", "ValidationException",
                                   "Session with Id old-session is not valid. Please check and try again.")
    stubs.runtime.add_response("retrieve_and_generate", rag_resp(session="new-session"))
    a = stubs.analyzer().ask(KB_ID, "And then?", session_id="old-session")
    assert a.session_id == "new-session" and "sessionId" not in a.request
    assert "expired" in a.notes[0]


def test_ask_raises_other_errors(stubs):
    stubs.list_kbs()
    stubs.models()
    stubs.runtime.add_client_error("retrieve_and_generate", "ValidationException", "The model failed.")
    with pytest.raises(ClientError, match="The model failed"):
        stubs.analyzer().ask(KB_ID, "q", session_id="s-1")


def test_ask_streams_the_answer_as_its_written(core, clients):
    seen = []
    a = core.ask(KB_ID, "How long?", {"n": 3}, stream=True, on_text=seen.append)
    assert a.streamed and a.first_words is not None and a.text == ANSWER and a.cited == [1, 2]
    assert len(seen) > 3 and seen[-1] == ANSWER and all(ANSWER.startswith(s) for s in seen)
    assert a.response["sessionId"] == "session-1" and a.session_id == "session-1"
    assert clients["bedrock-agent-runtime"].called("retrieve_and_generate") == []


def test_streaming_refused_falls_back_and_stops_trying():
    def denied(**_):
        raise client_error("AccessDeniedException", "not authorized to perform: RetrieveAndGenerateStream",
                           "RetrieveAndGenerateStream")

    clients = fakes(stream=denied)
    core = BedrockChatAnalyzer(clients=clients)
    a = core.ask(KB_ID, "How long?", stream=True, on_text=lambda text: None)
    assert not a.streamed and a.text == ANSWER and "Streaming was refused here" in a.notes[0]
    b = core.ask(KB_ID, "And then?", stream=True, on_text=lambda text: None)
    runtime = clients["bedrock-agent-runtime"]
    assert b.notes == [] and len(runtime.called("retrieve_and_generate_stream")) == 1
    assert len(runtime.called("retrieve_and_generate")) == 2


def test_streaming_refused_like_everything_else_raises_and_keeps_streaming():
    def denied(**_):
        raise client_error("AccessDeniedException", "You don't have access to the model.")

    core = BedrockChatAnalyzer(clients=fakes(rag=denied, stream=denied))
    with pytest.raises(ClientError, match="access to the model"):
        core.ask(KB_ID, "q", stream=True, on_text=print)
    assert core.stream_problem is None


def test_old_boto3_without_streaming_answers_all_at_once():
    core = BedrockChatAnalyzer(clients=fakes(stream=False))
    a = core.ask(KB_ID, "q", stream=True, on_text=print)
    assert not a.streamed and "can't stream answers" in a.notes[0]
    assert core.ask(KB_ID, "q", stream=True, on_text=print).notes == []


def test_request_resolves_without_sending(core, clients):
    params = core.request("support-docs", "How long?", {"temperature": 0.1}, model="opus", session_id="s-2")
    knowledge = params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
    assert (knowledge["knowledgeBaseId"], knowledge["modelArn"], params["sessionId"]) == (KB_ID, OPUS_PROFILE, "s-2")
    assert clients["bedrock-agent-runtime"].calls == []
    with pytest.raises(ValueError, match="No knowledge base 'nope'"):
        core.request("nope", "q")
    with pytest.raises(ValueError, match="temperature can be 0 to 1"):
        core.request(KB_ID, "q", {"temperature": 7})


def test_retrieve_sends_only_the_search_and_returns_every_passage(stubs):
    stubs.list_kbs()  # the knowledge base by name; no model is looked up
    expected = {"knowledgeBaseId": KB_ID, "retrievalQuery": {"text": "How long do refunds take?"},
                "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 8,
                                                                         "overrideSearchType": "HYBRID"}}}
    stubs.runtime.add_response("retrieve", RETRIEVED, expected)
    a = stubs.analyzer().retrieve("support-docs", "How long do refunds take?",
                                  {"n": 8, "search_type": "hybrid", "temperature": 0.2, "prompt": DEFAULT_PROMPT})
    assert a.retrieve_only and a.text == "" and a.citations == [] and a.session_id is None
    assert (a.kb_id, a.kb_name, a.model) == (KB_ID, "support-docs", "")
    assert [(p.rank, p.source, p.score) for p in a.sources] == [(1, "refund-policy.pdf p.3", 0.81),
                                                               (2, "refund-policy.pdf p.4", 0.62),
                                                               (3, "shipping.md", 0.4)]
    assert a.settings == {"n": 8, "search_type": "HYBRID"}  # what was sent: the answer's settings weren't
    assert a.request == expected and a.response["retrievalResults"] and (a.input_tokens, a.output_tokens) == (0, 0)


def test_retrieve_narrows_like_ask_and_its_request_needs_no_model(core, clients):
    a = core.retrieve(KB_ID, "q", data_source="docs-s3", files=["refund-policy.pdf"])
    assert a.data_sources == {DS_ID: "docs-s3"} and a.files == [REFUND_PDF]
    assert clients["bedrock-agent-runtime"].called("retrieve")[0]["retrievalConfiguration"] == {
        "vectorSearchConfiguration": {"filter": {"andAll": [{"equals": {"key": DS_KEY, "value": DS_ID}},
                                                            {"equals": {"key": URI_KEY, "value": REFUND_PDF}}]}}}
    params = core.request(KB_ID, "q", {"n": 3}, model="opus", session_id="s-1", retrieve_only=True)
    assert params == {"knowledgeBaseId": KB_ID, "retrievalQuery": {"text": "q"},
                      "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 3}}}
    assert clients["bedrock"].calls == []  # a search calls no model, so none is looked up
    assert core.send(params).retrieve_only  # send() takes a Retrieve request too


def test_knowledge_bases_are_listed_once(core, clients):
    kbs = core.knowledge_bases()
    assert [(kb.id, kb.name, kb.status) for kb in kbs] == [(KB_ID, "support-docs", "ACTIVE")]
    core.knowledge_bases()
    core.resolve("support-docs")
    assert len(clients["bedrock-agent"].called("list_knowledge_bases")) == 1



TWO_SOURCES = {KB_ID: [ds_summary(DS_ID, "faq"), ds_summary(DS2_ID, "help-site")]}


def test_ask_searches_only_the_data_source_asked_for():
    clients = fakes(sources=TWO_SOURCES)
    core = BedrockChatAnalyzer(clients=clients)
    a = core.ask("support-docs", "q", {"n": 5}, data_source="FAQ")
    assert a.data_sources == {DS_ID: "faq"}
    assert a.request[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "equals": {"key": DS_KEY, "value": DS_ID}}
    a = core.ask(KB_ID, "q", data_source=[DS_ID, "help-site"])
    assert a.data_sources == {DS_ID: "faq", DS2_ID: "help-site"}
    assert core.ask(KB_ID, "q", data_source="all").data_sources == {}
    assert len(clients["bedrock-agent"].called("list_data_sources")) == 1  # listed once, then cached
    assert [ds.name for ds in core.data_sources(KB_ID)] == ["faq", "help-site"]
    with pytest.raises(ValueError) as err:
        core.request(KB_ID, "q", data_source="help-sites")
    assert str(err.value) == ("support-docs has no data source 'help-sites'. Did you mean 'help-site'? Its data "
                              f"sources: faq ({DS_ID}), help-site ({DS2_ID}).")
    assert len(clients["bedrock-agent"].called("list_data_sources")) == 2  # looked again: it may be new


def test_data_sources_by_id_when_they_cant_be_listed():
    def denied(**_):
        raise client_error("AccessDeniedException", "not authorized", "ListDataSources")

    clients = fakes()
    clients["bedrock-agent"].handlers["list_data_sources"] = denied
    core = BedrockChatAnalyzer(clients=clients)
    assert core.resolve_sources(KB_ID, DS_ID) == {DS_ID: ""}
    with pytest.raises(ClientError):
        core.resolve_sources(KB_ID, "faq")


def test_files_are_listed_from_every_data_source_that_keeps_a_list():
    web = client_error("ValidationException", "ListKnowledgeBaseDocuments supports S3 and CUSTOM data sources only",
                       "ListKnowledgeBaseDocuments")
    clients = fakes(sources=TWO_SOURCES, documents={DS2_ID: web})
    core = BedrockChatAnalyzer(clients=clients)
    listing = core.files("support-docs")
    assert [d.uri for d in listing.documents] == [REFUND_PDF, RETURNS_MD, SCAN_PDF]
    assert [d.uri for d in listing.searchable] == [REFUND_PDF, RETURNS_MD]
    assert listing.errors == {DS2_ID: "ValidationException"} and not listing.truncated
    assert core.files(KB_ID) is listing  # cached
    assert len(clients["bedrock-agent"].called("list_knowledge_base_documents")) == 2
    assert core.file_status(KB_ID, SCAN_PDF) == "FAILED" and core.file_status(KB_ID, "s3://x/y") == ""
    assert core.files(KB_ID, limit=2, refresh=True).truncated


def test_ask_searches_only_the_files_asked_for():
    clients = fakes()
    core = BedrockChatAnalyzer(clients=clients)
    assert core.resolve_files(KB_ID, [REFUND_PDF]) == [REFUND_PDF]  # s3:// paths need no list
    assert clients["bedrock-agent"].called("list_knowledge_base_documents") == []
    a = core.ask("support-docs", "q", files=["refund-policy.PDF", "faq/returns.md"])
    assert a.files == [REFUND_PDF, RETURNS_MD]
    assert a.request[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "in": {"key": URI_KEY, "value": [REFUND_PDF, RETURNS_MD]}}
    assert core.resolve_files(KB_ID, "all") == [] and core.resolve_files(KB_ID, None) == []
    with pytest.raises(ValueError, match="No file 'refunds.pdf' in the knowledge base"):
        core.request(KB_ID, "q", files="refunds.pdf")
    assert len(clients["bedrock-agent"].called("list_knowledge_base_documents")) == 2  # looked again: it may be new
    with pytest.raises(ValueError, match="takes file names or s3:// paths"):
        core.resolve_files(KB_ID, ["a.pdf", " "])


def test_files_by_name_when_they_cant_be_listed():
    denied = client_error("AccessDeniedException", "not authorized", "ListKnowledgeBaseDocuments")
    core = BedrockChatAnalyzer(clients=fakes(documents={DS_ID: denied}))
    with pytest.raises(ValueError) as err:
        core.resolve_files(KB_ID, "refund-policy.pdf")
    assert ("couldn't be listed (AccessDeniedException; needs bedrock:ListKnowledgeBaseDocuments): pass full s3:// "
            "paths instead") in str(err.value)
    assert core.resolve_files(KB_ID, REFUND_PDF) == [REFUND_PDF]

def test_missing_region_is_a_readable_error(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with pytest.raises(ValueError, match=r"No AWS region is set.*chat\(region='us-east-1'\)"):
        BedrockChatAnalyzer().client


def by_question(**params):
    """RetrieveAndGenerate that can't help with a question about the moon until it retrieves 8 passages or more."""
    vector = params[KB[0]][KB[1]].get("retrievalConfiguration", {}).get("vectorSearchConfiguration", {})
    if "moon" in params["input"]["text"] and vector.get("numberOfResults", 5) < 8:
        return rag_resp(REFUSED, citations=())
    return rag_resp()


def test_ask_all_asks_each_question_on_its_own():
    clients = fakes(rag=by_question)
    core = BedrockChatAnalyzer(clients=clients)
    ticks = []
    batch = core.ask_all("support-docs", ["How long do refunds take? | refund-policy", "Do you ship to the moon?",
                                          "And bank transfers?"], {"n": 5, "temperature": 0.2}, model="sonnet",
                         workers=2, progress=lambda done, total: ticks.append((done, total)))
    assert [i.question for i in batch.items] == ["How long do refunds take?", "Do you ship to the moon?",
                                                 "And bank transfers?"]  # in the order given
    assert (batch.kb_id, batch.kb_name, batch.model) == (KB_ID, "support-docs", "us.anthropic.claude-sonnet-5")
    assert batch.settings == {"n": 5, "temperature": 0.2} and not batch.stopped and batch.seconds > 0
    assert ticks == [(1, 3), (2, 3), (3, 3)] and batch.request["input"] == {"text": "<your question>"}
    runtime = clients["bedrock-agent-runtime"]
    sent = runtime.called("retrieve_and_generate")
    assert sorted(p["input"]["text"] for p in sent) == sorted(i.question for i in batch.items)
    assert all("sessionId" not in p for p in sent)  # each on its own: never a follow-up
    assert all(p[KB[0]] == batch.request[KB[0]] for p in sent) and not runtime.called("retrieve_and_generate_stream")
    first, moon, bank = batch.items
    assert first.found == 1 and item_verdict(first) == ("answered", "ok") and first.cost > 0
    assert item_verdict(moon) == ("unable to assist", "warn") and first.answer.settings == batch.settings
    assert first.answer.kb_name == "support-docs" and batch.cost == pytest.approx(sum(i.cost for i in batch.items))
    assert batch.failed == [] and len(batch.asked) == 3


def test_ask_all_keeps_going_when_bedrock_refuses_a_question():
    def flaky(**params):
        if "bank" in params["input"]["text"]:
            raise client_error("ThrottlingException", "Rate exceeded.")
        return rag_resp()

    core = BedrockChatAnalyzer(clients=fakes(rag=flaky))
    batch = core.ask_all(KB_ID, ["How long?", "And bank transfers?", "x" * 1001, "Fourth?"], limit=3, workers=1)
    ok, throttled, long = batch.items
    assert ok.answer is not None and throttled.answer is None and batch.skipped == 1
    assert (throttled.error_code, throttled.error) == ("ThrottlingException", "Rate exceeded.")
    assert long.error.startswith("Bedrock takes questions of up to 1,000 characters") and not long.request
    assert batch.failed == [throttled, long] and not batch.stopped
    assert len(core.ask_all(KB_ID, ["a?", "b?"], limit=None).items) == 2
    assert len(core.ask_all(KB_ID, ["a?", "b?"], limit="0").items) == 2  # 0, like None: every question
    with pytest.raises(ValueError, match="limit takes a number of questions"):
        core.ask_all(KB_ID, ["a?"], limit=-1)
    with pytest.raises(ValueError, match="No questions to ask"):
        core.ask_all(KB_ID, " \n# only a comment")


def test_ask_all_stops_sending_when_asked_to():
    stop = threading.Event()

    def stopping(**params):
        stop.set()  # Stop, while the first question is being answered
        return rag_resp()

    clients = fakes(rag=stopping)
    core = BedrockChatAnalyzer(clients=clients)
    batch = core.ask_all(KB_ID, ["One?", "Two?", "Three?"], workers=1, stop=stop)
    assert [i.answer is not None for i in batch.items] == [True, False, False] and batch.stopped
    assert len(clients["bedrock-agent-runtime"].called("retrieve_and_generate")) == 1  # the rest weren't sent
    assert item_verdict(batch.items[1]) == ("not asked", "")
    assert batch_findings(batch)[-1] == ("info", "Stopped before 2 questions were asked: ask_all() asks the whole "
                                                 "list again.")

    def interrupt(done, total):
        raise KeyboardInterrupt  # the notebook's stop button: what came back is kept

    batch = core.ask_all(KB_ID, ["One?", "Two?", "Three?"], workers=1, progress=interrupt)
    assert batch.items[0].answer is not None and batch.items[2].answer is None and batch.stopped


def test_ask_all_retrieve_only_searches():
    clients = fakes()
    core = BedrockChatAnalyzer(clients=clients)
    batch = core.ask_all("support-docs", [("How long?", "bank transfer"), ("Shipping?", "nope")],
                         {"n": 3, "temperature": 0.2}, retrieve_only=True)
    assert batch.retrieve_only and batch.model == "" and batch.settings == {"n": 3}
    assert [item_verdict(i) for i in batch.items] == [("found #2", "ok"), ("expected not found", "warn")]
    runtime = clients["bedrock-agent-runtime"]
    assert not runtime.called("retrieve_and_generate") and len(runtime.called("retrieve")) == 2
    assert batch.request == {"knowledgeBaseId": KB_ID, "retrievalQuery": {"text": "<your question>"},
                             "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 3}}}
    assert not clients["bedrock"].called("list_foundation_models")  # a search needs no model
    assert batch.items[0].cost == pytest.approx(0.02 * estimate("How long?") / 1e6)


def test_sweep_asks_every_setup_a_question_at_a_time():
    clients = fakes(rag=by_question)
    core = BedrockChatAnalyzer(clients=clients)
    ticks = []
    sweep = core.sweep("support-docs", ["How long do refunds take? | refund-policy", "Do you ship to the moon?"],
                       sweep_setups({"n": [5, 8], "model": ["haiku", "sonnet"]}), {"n": 5, "temperature": 0.2},
                       workers=1, progress=lambda done, total: ticks.append((done, total)), label="first try")
    assert [b.label for b in sweep.batches] == [
        "first try · model=claude-haiku-4-5 · n=5", "first try · model=claude-sonnet-5 · n=5",
        "first try · model=claude-haiku-4-5 · n=8", "first try · model=claude-sonnet-5 · n=8"]
    assert ticks[-1] == (8, 8) and not sweep.stopped and sweep.seconds > 0 and list(sweep.varied) == ["model", "n"]
    assert len({b.id for b in sweep.batches}) == 4 and {b.sweep for b in sweep.batches} == {sweep.id}
    assert all(b.started is not None and b.settings["temperature"] == 0.2 for b in sweep.batches)
    sent = clients["bedrock-agent-runtime"].called("retrieve_and_generate")
    assert [p["input"]["text"] for p in sent] == ["How long do refunds take?"] * 4 + ["Do you ship to the moon?"] * 4
    vector = [p[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"] for p in sent]
    assert vector == [5, 5, 8, 8] * 2 and len({p[KB[0]][KB[1]]["modelArn"] for p in sent}) == 2
    assert sweep.best is sweep.batches[2]  # n=8 answers the moon question, and Haiku costs less than Sonnet
    assert sweep.cost == pytest.approx(sum(b.cost for b in sweep.batches))
    assert sweep_estimate(sweep) == 0.0  # nothing left to ask
    with pytest.raises(ValueError, match="Those setups come out the same"):
        core.sweep(KB_ID, ["a?"], [{"n": 5}, {"n": "5"}])
    with pytest.raises(ValueError, match="The setup with n='lots' can't be sent: n takes a number"):
        core.sweep(KB_ID, ["a?"], [{"n": 5}, {"n": "lots"}])
    with pytest.raises(ValueError, match="^No questions to ask"):  # not one setup's problem: said as it is
        core.sweep(KB_ID, " ", [{"n": 5}, {"n": 8}])
    with pytest.raises(ValueError, match="^limit takes a number of questions"):
        core.sweep(KB_ID, ["a?"], [{"n": 5}, {"n": 8}], limit=-1)
    assert len(clients["bedrock-agent-runtime"].called("retrieve_and_generate")) == 8  # nothing more was sent
    search = core.sweep(KB_ID, ["a?"], sweep_setups({"n": [3, 8], "temperature": [0, 0.5]}), retrieve_only=True)
    assert len(search.batches) == 2 and search.varied == {"n": [3, 8]}  # a search sends no temperature
    assert all(b.model == "" and b.retrieve_only for b in search.batches)


def test_sweep_stopped_leaves_every_setup_with_the_same_questions():
    stop = threading.Event()
    asked_so_far = []

    def counting(**params):
        asked_so_far.append(params["input"]["text"])
        if len(asked_so_far) == 2:  # Stop, while the second setup answers the first question
            stop.set()
        return rag_resp()

    core = BedrockChatAnalyzer(clients=fakes(rag=counting))
    sweep = core.sweep(KB_ID, ["One?", "Two?", "Three?"], [{"n": 5}, {"n": 8}], workers=1, stop=stop)
    assert asked_so_far == ["One?", "One?"] and sweep.stopped and all(b.stopped for b in sweep.batches)
    assert [[i.answer is not None for i in b.items] for b in sweep.batches] == [[True, False, False]] * 2
    assert shared_questions(sweep.batches) == {"one?"}
    assert ("info", "Ranked on the 1 question every setup came back with: 2 questions weren't asked with every setup "
                    "(the run stopped first, or Bedrock refused them).") in ranking_findings(sweep.batches)
    assert sweep_estimate(sweep) > 0  # what's left to ask


# ----------------------------------------------------------------------------- UI (reports)


@pytest.fixture
def ui(core):
    return BedrockChatView(core, kb="support-docs", mode="text", progress="off")


def test_ui_help_groups_every_command(ui, capsys):
    out = run(capsys, ui.help)
    commands = {name for name in vars(BedrockChatView) if not name.startswith("_")
                and callable(getattr(BedrockChatView, name))}
    assert commands == {name for names in BedrockChatView._GROUPS.values() for name in names}
    assert "Start here:" in out and "-- ⚙️ Settings --" in out and "set(name=None, value=None, **values)" in out
    assert "None removes a setting; an open window follows." in run(capsys, ui.help, "set")


def test_ui_ask_and_follow_up(ui, clients, capsys):
    out = run(capsys, ui.ask, "How long do refunds take?")
    for text in ("support-docs: How long do refunds take?", "question 1 of this conversation", "Grounded: 69%",
                 "Sources cited: 2", "Model: Claude Haiku 4.5", "Refunds take 5-7 business days [1].",
                 "refund-policy.pdf     3", "Tokens and cost are estimated", "last()"):
        assert text in out, text
    out = run(capsys, ui.ask, "And bank transfers?")
    assert "question 2 of this conversation" in out
    first, second = clients["bedrock-agent-runtime"].called("retrieve_and_generate")
    assert "sessionId" not in first and second["sessionId"] == "session-1"
    assert len(ui.answers) == 2 and ui.session_id == "session-1"


def test_ui_last_transcript_and_new_chat(ui, capsys):
    assert "No questions yet" in run(capsys, ui.last)
    ui.ask("How long do refunds take?")
    capsys.readouterr()
    out = run(capsys, ui.last)
    assert "-- Request sent --" in out and '"numberOfResults": 5' in out and "-- Response --" in out
    assert "Refunds are issued within 5-7 business days of receiving the returned item." in out
    assert "client.retrieve_and_generate(**{" in out
    out = run(capsys, ui.transcript)
    assert "Conversation with support-docs (1 question)" in out and "You: How long do refunds take?" in out
    assert "Bedrock (Claude Haiku 4.5 · " in out and "  [1] refund-policy.pdf p.3" in out
    out = run(capsys, ui.new_chat)
    assert "New conversation (1 earlier question forgotten)" in out
    assert ui.answers == [] and ui.session_id is None and ui.values == DEFAULT_SETTINGS


def test_ui_retrieve_shows_every_passage_and_what_the_answer_cited(ui, clients, capsys):
    ui.set(temperature=0.2)
    out = run(capsys, ui.ask, "How long do refunds take?")
    assert "retrieve('How long do refunds take?')" in out  # the next step: the search behind it
    out = run(capsys, ui.retrieve, "how long do refunds  take?")  # the same question, written a little differently
    for text in ("support-docs: how long do refunds  take?", "Bedrock Retrieve: the search only, no answer",
                 "question 2 of this conversation", "Passages: 3", "Best score: 0.810", "Files: 2",
                 "Cited by the answer: 2 of 3", "The answer cites 2 of the 3 passages the search found (#1 as [1], "
                 "#2 as [2]); the other one wasn't cited.", "-- Passages, best first --",
                 "#  Score  File               Page  Cited as  Passage", "3  0.400  shipping.md        -     -",
                 "Retrieve sent the search settings only (n=5)", "last()"):
        assert text in out, text
    sent = clients["bedrock-agent-runtime"].called("retrieve")[-1]
    assert "sessionId" not in sent and "temperature" not in str(sent)
    assert ui.session_id == "session-1" and len(ui.answers) == 2 and ui.answers[-1].retrieve_only  # the session stays
    out = run(capsys, ui.ask, "How long do refunds take?")  # an answer after a search says where its sources ranked
    assert "The answer cites 2 of the 3 passages the search found" in out
    assert clients["bedrock-agent-runtime"].called("retrieve_and_generate")[-1]["sessionId"] == "session-1"
    out = run(capsys, ui.transcript)
    assert "Bedrock RetrieveAndGenerate and Retrieve" in out and "Retrieve only: 1" in out
    assert "Bedrock search (Retrieve only · 3 passages · best score 0.810 · " in out
    assert "  #1 · refund-policy.pdf p.3 · score 0.810 · cited [1]" in out
    ui.retrieve("Nothing like it")
    out = run(capsys, ui.last)
    assert "-- Request sent --" in out and "client.retrieve(**{" in out and '"retrievalResults"' in out
    assert "Refunds are issued within 5-7 business days of receiving the returned item." in out
    assert "ask('Nothing like it')" in out and "Cited by the answer" not in out  # no answer to compare with yet
    assert "4 questions in this conversation (2 retrieve only)" in ui._conversation_line()


def test_ui_request_and_settings_follow_retrieve_only(ui, capsys):
    ui.set(temperature=0.2, search_type="hybrid")
    out = run(capsys, ui.request, retrieve_only=True)
    assert "Retrieve: the search only, no answer" in out and '"retrievalQuery"' in out and "temperature" in out
    assert "Model: none: no answer" in out and "Settings: 2 of 3" in out and "Conversation: not part of it" in out
    assert "Retrieve sends the search settings only: temperature waits for an answer." in out
    assert "request(retrieve_only=False)" in out and '"temperature"' not in out
    assert "RetrieveAndGenerate · highlighted" in run(capsys, ui.request)  # the window's mode: Answer
    ui.retrieve_only = True
    assert '"retrievalQuery"' in run(capsys, ui.request)
    out = run(capsys, ui.settings)
    assert "The window is on Retrieve only" in out and "temperature waits for an answer" in out
    assert "retrieve_only=True" in ui._setup_call()


def test_ui_settings_set_and_unset(ui, capsys):
    out = run(capsys, ui.set, temperature="0.2", top_p=0.9, where={"team": "billing"})
    assert "Changed: filter = " in out and "temperature = 0.2 (added)" in out and "unset('top_p')" in out
    out = run(capsys, ui.settings)
    for text in ("Settings: 4 settings sent with every question", "Retrieves the 5 passages that match best.",
                 'Only documents where team = "billing".', "generationConfiguration.inferenceConfig."
                 "textInferenceConfig.topP", "Open this setup again",
                 "chat('support-docs', n=5, filter={'equals': {'key': 'team', 'value': 'billing'}}, temperature=0.2, "
                 "top_p=0.9)"):
        assert text in out, text
    out = run(capsys, ui.set, "generationConfiguration.performanceConfig.latency", "optimized")
    assert "latency = 'optimized' (added)" in out
    assert "Removed: top_p, latency." in run(capsys, ui.unset, "topP", "latency")
    assert "Nothing changed: top_p was not set." in run(capsys, ui.unset, "top_p")
    assert list(ui.values) == ["n", "filter", "temperature"]
    ui.unset("n")
    assert "settings={}" in ui._setup_call()


@pytest.mark.parametrize("call, message", [
    (lambda ui: ui.set(temprature=1, n=3), "Did you mean 'temperature'? "),
    (lambda ui: ui.set("temperature"), "Pass a value too: set('temperature', ...)"),
    (lambda ui: ui.set(), "Pass what to change"),
    (lambda ui: ui.unset(), "Name the settings to stop sending"),
    (lambda ui: ui.set(prompt="no placeholder"), "The prompt needs $search_results$"),
])
def test_ui_settings_errors_are_notes_and_change_nothing(ui, capsys, call, message):
    before = dict(ui.values)
    out = run(capsys, call, ui)
    assert message in out and "Traceback" not in out
    assert ui.values == before


def test_ui_fields_and_request(ui, capsys):
    out = run(capsys, ui.fields)
    assert "-- Retrieval --" in out and "-- Orchestration --" in out and "HYBRID | SEMANTIC" in out
    assert "whole number 1–100" in out and "text up to 4,000 characters" in out
    out = run(capsys, ui.fields, "rerank")
    assert "reranker" in out and "rerank_n" in out and "temperature" not in out
    assert "No setting mentions 'zzz'" in run(capsys, ui.fields, "zzz")
    ui.set(guardrail_id="gr-1")
    out = run(capsys, ui.request, "How long?")
    assert "The request for: How long?" in out and '"text": "How long?"' in out
    assert "Bedrock would refuse this request: Missing required parameter" in out
    assert "A guardrail needs both guardrail_id and guardrail_version" in out
    assert "client.retrieve_and_generate(**{" in out


def test_ui_use_kbs_and_models(core, capsys):
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales", "CREATING")])
    ui = BedrockChatView(BedrockChatAnalyzer(clients=clients), mode="text", progress="off")
    assert "Which knowledge base? There are 2 in us-east-1: sales, support-docs" in run(capsys, ui.ask, "q")
    out = run(capsys, ui.kbs)
    assert "Knowledge bases in us-east-1 (2)" in out and "CREATING" in out and "use('support-docs')" in out
    out = run(capsys, ui.use, "sales", model="sonnet")
    assert "Knowledge base: sales (KBID654321). Model: Claude Sonnet 5 (us.anthropic.claude-sonnet-5)." in out
    out = run(capsys, ui.models)
    assert "us.anthropic.claude-sonnet-5 (in use)" in out and "amazon.nova-pro-v1:0" in out
    assert "Pass kb=, model=, data_source= or files=" in run(capsys, ui.use)
    assert "Did you mean 'sales'" in run(capsys, ui.use, "sale")
    out = run(capsys, ui.kbs, "kbid65")  # part of an ID, any case
    assert "Knowledge bases in us-east-1 (1 of 2)" in out and "matching 'kbid65' by name, ID or description" in out
    assert "KBID654321" in out and "KBID123456" not in out
    out = run(capsys, ui.kbs, f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/{KB_ID}")
    assert "(1 of 2)" in out and "support-docs" in out and "KBID654321" not in out
    out = run(capsys, ui.kbs, "suport-docs")
    assert "No knowledge base's name, ID or description holds 'suport-docs'. Did you mean 'support-docs'?" in out
    assert "kbs() lists all 2." in out


def test_search_rank_and_match_kbs():
    assert search_rank("", ["anything"]) == 3  # an empty search finds everything
    assert search_rank("KBID123456", ["support-docs", "KBID123456"]) == 0  # a whole ID
    assert search_rank("kbid12", ["support-docs", "KBID123456"]) == 1  # the start of one, any case
    assert search_rank("docs", ["support-docs"]) == 2
    assert search_rank("refund answers", ["support-docs", "Refund and returns answers"]) == 3  # every word somewhere
    assert search_rank("refund invoices", ["support-docs", "Refund and returns answers"]) is None
    kbs = [chatmod.KnowledgeBase("KBID123456", "support-docs", "ACTIVE", "Refunds and returns"),
           chatmod.KnowledgeBase("SUPP000001", "archive", "FAILED", "Old support answers"),
           chatmod.KnowledgeBase("KBID654321", "sales", "ACTIVE", "Pitch decks")]
    assert [kb.name for kb in match_kbs(kbs, "supp")] == ["support-docs", "archive"]  # names and IDs first
    assert [kb.name for kb in match_kbs(kbs, "654321")] == ["sales"]
    assert [kb.name for kb in match_kbs(kbs, f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/KBID654321")] == [
        "sales"]
    assert [kb.name for kb in match_kbs(kbs, "failed")] == ["archive"]  # its status
    assert match_kbs(kbs, "nothing") == [] and len(match_kbs(kbs, "")) == 3



def test_ui_use_a_data_source(capsys):
    clients = fakes(sources=TWO_SOURCES)
    ui = BedrockChatView(BedrockChatAnalyzer(clients=clients), mode="text", progress="off")
    out = run(capsys, ui.use, "support-docs")
    assert "It has 2 data sources (faq, help-site): use(data_source='faq') asks only one." in out
    out = run(capsys, ui.use, data_source="help-site")
    assert "Questions search data source 'help-site'." in out
    out = run(capsys, ui.ask, "How long do refunds take?")
    assert "question 1 of this conversation · only data source 'help-site'" in out
    sent = clients["bedrock-agent-runtime"].called("retrieve_and_generate")[-1]
    assert sent[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "equals": {"key": DS_KEY, "value": DS2_ID}}
    assert "Data source: help-site" in run(capsys, ui.settings)
    out = run(capsys, ui.request)
    assert "Data source: help-site" in out and DS2_ID in out
    assert "chat('support-docs', data_source='help-site'" in ui._setup_call()
    out = run(capsys, ui.use, data_source="nope")
    assert "has no data source 'nope'" in out and ui.data_source == {DS2_ID: "help-site"}  # nothing changed
    out = run(capsys, ui.use, data_source="all")
    assert "Questions search every data source." in out and ui.data_source == {}
    assert len(ui.answers) == 1  # the conversation goes on
    run(capsys, ui.use, data_source="faq")
    run(capsys, ui.use, "support-docs")  # the same knowledge base: the data source stays
    assert ui.data_source == {DS_ID: "faq"}


def test_ui_answer_says_which_data_source_each_source_came_from(capsys):
    web = ref("Bank transfers take up to 10 days.", "help/bank.html", page=None)
    web["metadata"][DS_KEY] = DS2_ID
    rag = rag_resp(citations=(("Refunds take 5-7 business days.", [REFUND, BANK]),
                              ("Bank transfers can take up to 10 days.", [web])))
    clients = fakes(sources=TWO_SOURCES, rag=lambda **_: rag)
    ui = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="text", progress="off")
    out = run(capsys, ui.ask, "How long do refunds take?")
    assert "#  File               Page  Data source  Passage" in out and "  help-site    " in out
    assert "use(data_source='faq')" in out and "ask only data source 'faq', where 2 sources came from" in out


def test_ui_files_and_use_files(capsys):
    web = client_error("ValidationException", "S3 and CUSTOM only", "ListKnowledgeBaseDocuments")
    clients = fakes(sources=TWO_SOURCES, documents={DS2_ID: web})
    ui = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="text", progress="off")
    out = run(capsys, ui.files)
    for text in ("Files in support-docs (3)", "Files: 3", "Indexed: 2", "Not indexed: 1 (!)", "Questions search: all",
                 "1 file isn't indexed", "The data source 'help-site' has no file list (ValidationException: only S3 "
                 "and custom data sources keep one)", "refund-policy.pdf", "policies/", "faq", "failed",
                 "use(files=['faq/returns.md'])"):
        assert text in out, text
    out = run(capsys, ui.use, files=["refund-policy.pdf"])
    assert "Questions search file 'refund-policy.pdf'." in out
    run(capsys, ui.ask, "How long do refunds take?")
    sent = clients["bedrock-agent-runtime"].called("retrieve_and_generate")[-1]
    assert sent[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "equals": {"key": URI_KEY, "value": REFUND_PDF}}
    assert ui.answers[-1].files == [REFUND_PDF]
    assert "Files: refund-policy.pdf" in run(capsys, ui.settings)
    assert "files=['policies/refund-policy.pdf']" in ui._setup_call()
    out = run(capsys, ui.files)
    assert "refund-policy.pdf (picked)" in out and "use(files='all')" in out
    out = run(capsys, ui.use, files=["refund-policy.pdf", "scanned-invoice.pdf"])
    assert "[!]" in out and "File 'scanned-invoice.pdf' isn't indexed (failed)" in out
    assert "No file 'nope.pdf'" in run(capsys, ui.use, files="nope.pdf")
    assert ui.picked_files == [REFUND_PDF, SCAN_PDF]  # nothing changed
    assert "Questions search every file." in run(capsys, ui.use, files="all") and ui.picked_files == []
    run(capsys, ui.use, files=RETURNS_MD)
    run(capsys, ui.use, "support-docs")  # the same knowledge base: the files stay
    assert ui.picked_files == [RETURNS_MD]


def test_ui_answer_from_outside_the_picked_files_is_flagged(capsys):
    ui = BedrockChatView(BedrockChatAnalyzer(clients=fakes()), kb="support-docs", mode="text", progress="off",
                         files=[RETURNS_MD])
    out = run(capsys, ui.ask, "How long do refunds take?")  # the fake ignores the filter: its passages are elsewhere
    assert "question 1 of this conversation · only file 'returns.md'" in out
    assert "2 sources ([1], [2]) came from outside file 'returns.md'" in out

def test_ui_explains_aws_errors(capsys):
    def denied(**_):
        raise client_error("AccessDeniedException", "You don't have access to the model with the specified model ID.")

    ui = BedrockChatView(BedrockChatAnalyzer(clients=fakes(rag=denied)), kb=KB_ID, mode="text", progress="off")
    out = run(capsys, ui.ask, "q")
    assert "AccessDeniedException: You don't have access" in out and "Model access" in out and "[ask]" in out
    assert ui.answers == []
    hybrid = client_error("ValidationException", "HYBRID search type is not supported for this vector store")
    assert "unset('search_type')" in ui._explain("ValidationException", str(hybrid.response["Error"]["Message"]))
    both = "The model returned the following errors: temperature and top_p cannot both be specified for this model."
    assert "unset('top_p')" in ui._explain("ValidationException", both)


def test_ui_app_outside_jupyter_says_what_to_use(ui, capsys):
    out = run(capsys, ui.app)
    assert "The chat window needs Jupyter" in out and "ask('a question')" in out
    ui._ipython_display_()
    assert "BedrockChatView(kb='support-docs'" in capsys.readouterr().out


def test_chat_opens_the_window_and_names_unusable_settings(core, monkeypatch, capsys):
    monkeypatch.setattr(chatmod, "BedrockChatAnalyzer", lambda region=None, profile=None: core)
    view = chatmod.chat("support-docs", model="sonnet", temperature=0.3, bogus=1, settings={"n": 9})
    assert view.values == {"n": 9, "temperature": 0.3} and view.model == "sonnet"
    assert view._notes == ["Not used: No setting 'bogus'. fields() lists every one RetrieveAndGenerate takes in this "
                           f"boto3 (botocore {SCHEMA.boto}); pip install -U boto3 adds fields AWS added since."]
    assert "The chat window needs Jupyter" in capsys.readouterr().out
    view = chatmod.chat("support-docs", data_source="docs-s3")
    assert view.values == DEFAULT_SETTINGS and view._setup_call() == ("chat('support-docs', data_source='docs-s3', "
                                                                     "n=5)")
    view = chatmod.chat("support-docs", retrieve_only=True)
    assert view.retrieve_only and view._setup_call() == "chat('support-docs', retrieve_only=True, n=5)"


def test_ui_ask_all_reports_each_question_and_what_changed(capsys):
    ui = BedrockChatView(BedrockChatAnalyzer(clients=fakes(rag=by_question)), kb="support-docs", mode="text",
                         progress="off")
    assert "Pass the questions to ask: ask_all(['How long do refunds take?'" in run(capsys, ui.ask_all)
    assert "No test runs yet: ask_all(['a question', 'another'])" in run(capsys, ui.results)
    out = run(capsys, ui.ask_all, "How long do refunds take? | refund-policy\nDo you ship to the moon?\n"
                                  "And bank transfers? | shipping")
    for text in ("Test run on support-docs: 3 questions", "test run 1 · each question asked on its own, not as a "
                 "follow-up · Claude Haiku 4.5 · n=5 · cost at us-east-1 list prices", "Questions: 3",
                 "Answered: 2 of 3 (!)", "Grounded: 69%", "Expected cited: 1 of 2 (!)", "Est. cost: ",
                 "1 of 3 answers is Bedrock's \"unable to assist\" reply or empty ('Do you ship to the moon?')",
                 "The expected source isn't cited in 1 of 2 answers checked ('And bank transfers?' expected "
                 "'shipping', cited refund-policy.pdf)", "1. How long do refunds take?",
                 "   [answered] Claude Haiku 4.5 · ", "   Expected 'refund-policy': cited as [1]",
                 "   Refunds take 5-7 business days [1].", "   [1] refund-policy.pdf p.3",
                 "2. Do you ship to the moon?", "   [unable to assist] Claude Haiku 4.5",
                 "Each question was asked on its own (a new Bedrock session each)",
                 "retrieve('Do you ship to the moon?')", "ask_all()   "):
        assert text in out, text
    assert ui.questions[0] == ("How long do refunds take?", "refund-policy") and len(ui.batches) == 1
    assert ui.answers == [] and ui.session_id is None  # the conversation isn't touched
    ui.set(n=8)
    out = run(capsys, ui.ask_all)  # the same questions again, with n=8: the moon question is answered now
    assert "test run 2 · " in out and "n=8" in out and "Answered: 3 of 3" in out
    assert ("Since the last run (n 5 → 8): 1 question did better ('Do you ship to the moon?' unable to assist → "
            "answered)") in out
    assert "   [answered] (↑ was unable to assist) Claude Haiku 4.5" in out
    assert "test run 1 · " in run(capsys, ui.results, 1)  # numbered as the reports number them
    assert "test run 2 · " in run(capsys, ui.results, -1) and "test run 1 · " in run(capsys, ui.results, "1")
    for wrong in (0, 5, "two"):
        assert "There are 2 test runs, numbered from 1: 1 to 2 (-1 is the last)." in run(capsys, ui.results, wrong)
    out = run(capsys, ui.ask_all, ["How long?", "Bank?"], retrieve_only=True)
    assert "Retrieve: the search only, no answers" in out and "Found passages: 2 of 2" in out
    assert "Since the last run" not in out  # the last run of searches: none before it
    assert "#1 · refund-policy.pdf p.3 · score 0.810" in out and len(ui.batches) == 3


def text_view(**kwargs):
    return BedrockChatView(BedrockChatAnalyzer(clients=kwargs.pop("clients", None) or fakes(rag=by_question),
                                               **kwargs.pop("core", {})),
                           kb="support-docs", mode="text", progress="off", **kwargs)


TWO_QUESTIONS = "How long do refunds take? | refund-policy\nDo you ship to the moon?"


def test_ui_sweep_ranks_the_setups_and_says_which_to_use(capsys):
    ui = text_view()
    assert "Pass the questions to ask: sweep([" in run(capsys, ui.sweep, n=[5, 8])
    assert "test run 1 (baseline) · " in run(capsys, ui.ask_all, TWO_QUESTIONS, label="baseline")
    assert "Pass what to try, a list of values each: sweep(n=[5, 10]" in run(capsys, ui.sweep)
    out = run(capsys, ui.sweep, n=[5, 8], search_type=["SEMANTIC", "HYBRID"])
    for text in ("Sweep on support-docs: 4 setups × 2 questions", "runs 2–5 · each question asked on its own with "
                 "each setup · Claude Haiku 4.5 · ranked by expected sources cited, then answers, then grounded share",
                 "Setups: 4   Questions: 2   Calls: 8   Best: run 4   Expected cited (best): 1 of 1",
                 "[!] n=8 · search_type=SEMANTIC did better than your setup now (run 1: n=5 · search_type=default): it "
                 "answers 2 of 2 questions, against 1 of 2", "use_run(4) switches to it.",
                 "[i] n=8 did best in each of the 2 groups of setups that differ only in n.",
                 "[i] search_type made no difference", "These runs are kept in this notebook's memory only",
                 "-- Setups, best first --", "Rank  Run  n  search_type  Answered  Grounded  Expected cited  Failed",
                 "How each question did with each setup (#1 is the best, as above): the 1 question the setup changes "
                 "first", "Do you ship to the moon?   answered", "unable to assist", "use_run(4)   switch to the best "
                 "setup", "results(4)   the best setup's answers"):
        assert text in out, text
    assert len(ui.batches) == 5 and len(ui.sweeps) == 1 and all(a is b for a, b in zip(ui.sweeps[0].batches,
                                                                                       ui.batches[1:]))
    assert ui.questions == [("How long do refunds take?", "refund-policy"), ("Do you ship to the moon?", None)]
    out = run(capsys, ui.results, 4)
    assert "test run 4 (sweep 1: n=8 · search_type=SEMANTIC) · " in out and "use_run(4)" in out
    assert "Since the last run (n 5 → 8; search_type = 'SEMANTIC' (added))" in out  # the run before the sweep
    assert ("That's 17 setups (17 n), and a sweep asks up to 16: try fewer values, or pass max_setups=17."
            in run(capsys, ui.sweep, n=list(range(1, 18))))
    out = run(capsys, ui.sweep, ["How long?"], n=[5, 8], search_type=["SEMANTIC", "HYBRID"], retrieve_only=True)
    assert "Sweep on support-docs: 4 setups × 1 question" in out and "Retrieve: the search only" in out
    assert "Found passages (best): 1 of 1" in out and "search finds the same passages every time" in out


def test_ui_sweep_isnt_sent_over_max_cost(capsys):
    ui = text_view(core={"model_prices": {"claude-haiku-4-5": (900.0, 900.0)}})
    out = run(capsys, ui.sweep, ["How long?", "Bank?"], n=[5, 8])
    assert "That's 2 setups × 2 questions = 4 calls, about $" in out and "(estimate, your prices), more than " \
           "max_cost=2, so nothing was sent. Pass max_cost=" in out
    assert ui.batches == [] and not ui.core._runtime_client().called("retrieve_and_generate")
    assert "Sweep on support-docs: 2 setups × 2 questions" in run(capsys, ui.sweep, ["How long?", "Bank?"], n=[5, 8],
                                                                   max_cost=None)


def test_ui_runs_compare_runs_and_use_run(capsys):
    ui = text_view()
    assert "No test runs yet: ask_all(['a question', 'another']), sweep(n=[5, 10])" in run(capsys, ui.runs)
    run(capsys, ui.ask_all, TWO_QUESTIONS, label="baseline")
    assert "Only run 1 asked these questions this way, so there's nothing to compare it with" in run(
        capsys, ui.compare_runs)
    run(capsys, ui.sweep, n=[5, 8])
    out = run(capsys, ui.runs)
    for text in ("Test runs: 3 so far", "newest first · rank: against the other runs of the same questions",
                 "Runs: 3   Sweeps: 1   Question lists: 1   Best of the last list: run 3", "Saved to: not saved",
                 "sweep 1: n=8", "Claude Haiku 4.5 · n=8", "2 of 2 answered", "1 of 3", "baseline",
                 "did better than your setup now", "keep them in a file, to read back after a restart"):
        assert text in out, text
    assert out.index("sweep 1: n=8") < out.index("baseline")  # newest first
    assert "made no difference" not in out and "compare_runs()" in out  # what changed what: compare_runs() says it
    out = run(capsys, ui.compare_runs, 1, "3")
    assert "2 test runs on support-docs compared: 2 questions" in out and "runs 1, 3 · " in out
    assert "-- Runs, best first --" in out and "2 (now)    1  5  1 of 2" in out  # run 1's setup is the one in use
    assert "3 test runs on support-docs compared" in run(capsys, ui.compare_runs)
    assert "There are 3 test runs, numbered from 1: 1 to 3 (-1 is the last)." in run(capsys, ui.compare_runs, 1, 9)
    out = run(capsys, ui.use_run)  # the best run of the last list
    assert "Now using the setup of run 3 (sweep 1: n=8): n 5 → 8." in out and ui.values == {"n": 8}
    assert "The setup of run 3 (sweep 1: n=8) is the one in use already." in run(capsys, ui.use_run, -1)
    ui.set(temperature=0.2)
    run(capsys, ui.ask_all, ["How long?"], retrieve_only=True)
    assert "Run 4 only searched and run 1 answered: compare runs of one kind." in run(capsys, ui.compare_runs, 1, 4)
    assert "Those runs have no question in common" in run(capsys, ui.compare_runs, 1, run_of(asked("Else?")))
    out = run(capsys, ui.use_run, 1)
    assert "Now using the setup of run 1 (baseline): n 8 → 5; temperature removed." in out
    ui.set(temperature=0.5, n=3)
    assert "n 3 → 8" in run(capsys, ui.use_run, 4) and ui.values == {"n": 8, "temperature": 0.5}  # a search keeps it
    run(capsys, ui.sweep, ["How long?"], model=["haiku", "sonnet"])
    out = run(capsys, ui.use_run, 6)
    assert "model Claude Haiku 4.5 → Claude Sonnet 5" in out and ui.model == "us.anthropic.claude-sonnet-5"


def test_ui_saves_test_runs_and_reads_them_back(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ui = text_view()
    out = run(capsys, ui.save_runs)
    assert "No test runs yet, so nothing was written: every run from now on is added to kb-test-runs.jsonl" in out
    assert ui.log == "kb-test-runs.jsonl"
    run(capsys, ui.ask_all, TWO_QUESTIONS, label="baseline")
    out = run(capsys, ui.sweep, n=[5, 8])
    assert "memory only" not in out  # every run went into the file as it finished
    lines = (tmp_path / "kb-test-runs.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["label"] == "baseline"
    out = run(capsys, ui.save_runs)
    assert f"Every run here is in kb-test-runs.jsonl (in {tmp_path}) already (3 in the file)." in out
    assert len((tmp_path / "kb-test-runs.jsonl").read_text().splitlines()) == 3  # none written twice
    again = text_view()  # after a kernel restart
    out = run(capsys, again.load_runs)
    assert "Loaded 3 test runs from kb-test-runs.jsonl." in out and "Test runs: 3 so far" in out
    assert "Saved to: kb-test-runs.jsonl" in out and "memory only" not in out
    assert [b.label for b in again.batches] == ["baseline", "n=5", "n=8"] and len(again.sweeps) == 1
    assert again.questions == [("How long do refunds take?", "refund-policy"), ("Do you ship to the moon?", None)]
    assert "test run 3 (sweep 1: n=8) · " in run(capsys, again.results, 3)
    assert "Every run in kb-test-runs.jsonl is here already (3)." in run(capsys, again.load_runs)
    out = run(capsys, again.ask_all)  # the loaded questions, with the setup in use
    assert "test run 4 · " in out and "Since the last run (the same setup): both questions did as before." in out
    assert "1 of 4 runs is kept in this notebook's memory only" in run(capsys, again.runs)
    assert "There's no file nope.jsonl in " in run(capsys, again.load_runs, "nope.jsonl")
    (tmp_path / "bad.jsonl").write_text(lines[0] + "\nnot json\n")
    out = run(capsys, text_view().load_runs, "bad.jsonl")
    assert "Loaded 1 test run from bad.jsonl." in out
    assert "1 line couldn't be read: line 2 isn't JSON (Expecting value). The rest were loaded." in out
    assert "is a folder: pass a file in it, like save_runs(" in run(capsys, again.save_runs, str(tmp_path))
    out = run(capsys, again.save_runs, str(tmp_path / "no-such-folder" / "runs.jsonl"))
    assert "Couldn't save to " in out and "No such file or directory" in out
    logged = text_view(log=str(tmp_path / "missing" / "runs.jsonl"))
    out = run(capsys, logged.ask_all, ["How long?"])
    assert "[!] This run wasn't saved to " in out and "save_runs('another/path.jsonl')" in out


def test_ui_code_gives_the_setup_to_copy(ui, capsys):
    ui.set(temperature=0.2)
    out = run(capsys, ui.code)
    for text in ("This setup as code: support-docs", "RetrieveAndGenerate · nothing is sent", "Questions: none yet",
                 "-- Python: asks a question (put yours in) with this setup and prints each answer with the files it "
                 "cites (boto3 only) --", "questions = ['<your question>']", "'temperature': 0.2",
                 "# Knowledge base support-docs (KBID123456), answered by Claude Haiku 4.5.",
                 "-- The config as JSON: the request without the question --", '"retrieveAndGenerateConfiguration": {',
                 "-- AWS CLI: one question from a terminal (bash or zsh) --",
                 "aws bedrock-agent-runtime retrieve-and-generate \\", "--cli-input-json file://bedrock-config.json",
                 "code(retrieve_only=True)"):
        assert text in out, text
    ui.ask("How long do refunds take?")  # the conversation's questions, when there are no test questions
    assert "questions = ['How long do refunds take?']" in run(capsys, ui.code)
    out = run(capsys, ui.code, ["Q1?", "Q2?"], retrieve_only=True)
    assert "Retrieve: the search only, no answer" in out and "return client.retrieve(retrievalQuery=" in out
    assert "questions = ['Q1?', 'Q2?']" in out and "aws bedrock-agent-runtime retrieve \\" in out
    assert "temperature" not in out  # the answer's settings aren't part of a search
    ui.set(guardrail_id="gr-1")
    out = run(capsys, ui.code)
    assert "Bedrock would refuse this request, so the code would fail the same way: Missing required parameter" in out


def test_chat_fills_the_test_tab(core, monkeypatch, capsys):
    monkeypatch.setattr(chatmod, "BedrockChatAnalyzer", lambda region=None, profile=None: core)
    view = chatmod.chat("support-docs", questions="One?\nTwo? | two.md")
    assert view.questions == [("One?", None), ("Two?", "two.md")]
    view = chatmod.chat("support-docs", questions=[42])
    assert view.questions == [] and view._notes[0].startswith("The test questions weren't used: ask_all() takes")
    assert BedrockChatView(core, questions=["One?"], mode="text").questions == [("One?", None)]
    capsys.readouterr()


# ----------------------------------------------------------------------------- UI (the chat window)

widgets = pytest.importorskip("ipywidgets")


@pytest.fixture
def window(core, monkeypatch):
    """A view in HTML mode with its window built, as app() would show it in Jupyter."""
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 7)
    view = BedrockChatView(core, kb="support-docs", mode="html", progress="off")
    view._shown = []
    view._display = view._shown.append
    view.app()
    assert view._shown == [view._app.root]
    return view


def texts(app):
    return [bubble.value for bubble in app.bubbles]


def click_line(picker, value):
    """Opens a picker and clicks the line holding `value`, as a person would."""
    if not picker.is_open:
        picker.button.click()
    picker.rows[value][1].click()


def find(picker, text):
    """Opens a picker and types in its search box -> the values of the lines it shows."""
    if not picker.is_open:
        picker.button.click()
    picker.search.value = text
    return picker.shown


def enter(picker, text):
    find(picker, text)
    picker.search._handle_custom_msg({"event": "submit"}, [])


def test_window_opens_on_the_knowledge_base_and_model(window):
    app = window._app
    assert "kbc-app" in app.root._dom_classes
    assert (app.kb_pick.value, app.model_pick.value) == (KB_ID, f"us.{HAIKU}")  # DEFAULT_MODEL, Claude Haiku 4.5
    assert "support-docs" in app.kb_pick.face.value and KB_ID in app.kb_pick.face.value  # the field: name and ID
    haiku = app.model_pick._choice(f"us.{HAIKU}")
    assert (haiku.title, haiku.badge) == ("Claude Haiku 4.5", "Anthropic · $1.10 / $5.50")
    assert haiku.note == "Anthropic · inference profile · $1.10 / $5.50 per 1M tokens"
    assert "Claude Haiku 4.5" in app.model_pick.face.value and "$1.10 / $5.50" in app.model_pick.face.value
    assert app.model_pick.choices[0].title == "Nova Pro"  # by provider
    assert not any(picker.is_open for picker in app.pickers)  # the lists open on a click
    assert list(app.rows) == ["n"] and app.inputs["n"].value == 5
    assert "Retrieves the 5 passages that match best." in app.row_notes["n"].value
    name = app.rows["n"].children[0]
    assert app.inputs["n"] in name.children  # a number sits beside its name
    tip = name.children[0].value  # hovering the name says what it does, and where it goes
    assert "How many passages Bedrock retrieves" in tip and "vectorSearchConfiguration.numberOfResults" in tip
    assert app.adding.layout.display == "none"  # Add a setting waits behind its button
    assert [chip.description for chip in app.chip_box.children][:3] == ["+ Search type", "+ Metadata filter",
                                                                         "+ Reranker"]
    assert "Ask support-docs a question." in texts(app)[0]
    assert '<span class="jm" title="set as n">' in app.request_view.value
    assert f"chat('support-docs', model='us.{HAIKU}', n=5)" in plain(app.setup.value)
    assert '<span class="pf">chat</span>' in app.setup.value  # highlighted, like the Python view
    assert app.results.children == ()  # nothing is listed under the search box until you search
    assert "Nothing yet" in app.response_view.value


def test_window_fills_the_browser_unless_given_a_height(window, core, monkeypatch):
    app = window._app
    assert app.log.layout.height is None and all(tab.layout.max_height is None for tab in app.tabs.children)
    assert ".kbc-app.kbc-app .kbc-log{height:clamp(540px,calc(100vh - 400px),1400px);" in chatmod._CSS
    assert "body[class*=vscode-] .kbc-app.kbc-app .kbc-log{height:540px}" in chatmod._CSS
    monkeypatch.setattr(chatmod, "BedrockChatAnalyzer", lambda region=None, profile=None: core)
    monkeypatch.setattr(chatmod, "_in_notebook", lambda: True)
    shown = []
    monkeypatch.setattr(BedrockChatView, "_display", lambda self, widget: shown.append(widget))
    view = chatmod.chat("support-docs", height=800)
    assert shown == [view._app.root] and view._app.log.layout.height == "800px"
    assert {tab.layout.max_height for tab in view._app.tabs.children} == {"calc(800px + 80px)"}  # the side follows
    assert "height" not in view._setup_call()  # a display preference, like stream=, not part of the setup


def test_window_adds_edits_and_removes_settings(window):
    app = window._app
    app.chips["temperature"].click()
    assert window.values["temperature"] == 0.2 and "temperature" in app.rows
    assert "Added Temperature. 0.2: steady, factual wording." in app.status.value
    assert "kbc-fresh" in app.rows["temperature"]._dom_classes  # the new card is outlined until another is added
    app.inputs["temperature"].value = 0.8
    assert window.values["temperature"] == 0.8 and "0.8: varied wording." in app.row_notes["temperature"].value
    app.inputs["n"].value = 12
    assert window.values["n"] == 12 and 'numberOfResults&quot;</span>: </span><span class="jn">12' in (
        app.request_view.value)
    app.chips["top_p"].click()
    assert "kbc-fresh" not in app.rows["temperature"]._dom_classes and "kbc-fresh" in app.rows["top_p"]._dom_classes
    app.removes["temperature"].click()  # its ✕
    assert "temperature" not in window.values and "temperature" not in app.rows
    assert "+ Temperature" in [chip.description for chip in app.chip_box.children]


def test_window_keeps_a_setting_out_until_it_can_be_sent(window):
    app = window._app
    app.chips["filter"].click()
    assert "filter" in app.pending and "filter" not in window.values
    assert app.inputs["filter"] in app.rows["filter"].children  # JSON gets a box under the name
    assert "Not sent until you fill it in." in app.row_notes["filter"].value
    assert "kbc-pending" in app.rows["filter"]._dom_classes
    app.inputs["filter"].value = '{"team": '
    assert "filter" in app.broken and "Not sent: filter isn&#x27;t valid JSON" in app.row_notes["filter"].value
    assert "kbc-broken" in app.rows["filter"]._dom_classes and "kbc-pending" not in app.rows["filter"]._dom_classes
    assert "filter isn&#x27;t sent" in app.findings.value
    app.inputs["filter"].value = '{"team": "billing", "year": [">=", 2024]}'
    assert window.values["filter"]["andAll"][1] == {"greaterThanOrEquals": {"key": "year", "value": 2024}}
    assert "team = &quot;billing&quot; and year ≥ 2024" in app.row_notes["filter"].value
    assert not app.broken and "greaterThanOrEquals&quot;" in app.request_view.value


def test_window_opens_and_folds_add_a_setting(window):
    app = window._app
    app.add_button.click()
    assert app.adding.layout.display == "" and app.add_button.layout.display == "none"
    app.add_name.value = "rerank"
    app.browse_button.click()
    assert app.results.children and app.browsing
    app.chips["temperature"].click()
    assert "temperature" in app.rows and app.adding.layout.display == ""  # it stays open, to add another
    app.close_adding.click()
    assert app.adding.layout.display == "none" and app.add_button.layout.display == ""
    assert app.add_name.value == "" and app.results.children == () and not app.browsing  # the search is forgotten


def test_window_finds_any_field_by_name_path_or_what_it_does(window):
    app = window._app

    def listed():
        return [key for key, (row, _) in app.picks.items() if row in app.results.children]

    app.add_name.value = "rerank"
    assert listed()[:2] == ["reranker", "rerank_n"] and len(listed()) == len(SCHEMA.search("rerank"))
    app.add_name.value = "model"
    assert len(listed()) == chatmod._SHOWN_MATCHES and "more match" in app.results.children[-1].value
    app.add_name.value = "rerank"
    assert "re-orders the passages" in app.picks["reranker"][0].children[0].value
    app.picks["rerank_n"][1].click()
    assert window.values["rerank_n"] == 5 and app.picks["rerank_n"][1].description == "✓ Added"
    assert app.picks["rerank_n"][1].disabled and app.add_name.value == "rerank"  # the list stays, to add another
    app.add_name.value = "selectionMode"
    assert listed()[0].endswith("selectionMode") and "SELECTIVE | ALL" in app.picks[listed()[0]][0].children[0].value
    app.add_name.value = "temprature"
    assert "Did you mean Temperature?" in app.add_help.value
    app.add_name._handle_custom_msg({"event": "submit"}, [])  # Enter adds nothing on a near miss
    assert "No setting &#x27;temprature&#x27;" in app.status.value and "temperature" not in window.values
    app.add_name.value = "orchestrationConfiguration.performanceConfig.latency"
    app.add_name._handle_custom_msg({"event": "focus"}, [])  # anything but Enter does nothing
    assert "orchestrationConfiguration.performanceConfig.latency" not in window.values
    app.add_name._handle_custom_msg({"event": "submit"}, [])
    assert window.values["orchestrationConfiguration.performanceConfig.latency"] == "standard"
    assert app.add_name.value == "" and app.results.children == ()
    app.add_name.value = "encrypts the conversation"  # what it does
    app.add_name._handle_custom_msg({"event": "submit"}, [])
    assert "kms_key" in app.pending and "kms_key" in app.rows
    app.browse_button.click()
    assert len(listed()) == len(F) and app.browse_button.description == "Hide the list"
    assert [h for h in app.results.children if isinstance(h, widgets.HTML)] == [
        app.pick_headers[g] for g in ("Retrieval", "Generation", "Orchestration", "Session")]  # by group
    app.browse_button.click()
    assert app.results.children == () and app.browse_button.description == f"Browse all {len(F)}"


def test_window_sends_a_question_and_streams_the_answer(window, clients):
    app = window._app
    app.question.value = "How long do refunds take?"
    app.send_button.click()
    a = window.answers[-1]
    assert a.streamed and app.question.value == "" and not app.busy
    assert "How long do refunds take?" in texts(app)[1] and chatmod._YOU in texts(app)[1]  # your mark beside it
    assert "Refunds take 5-7 business days" in texts(app)[2] and "<sup>[1]</sup>" in texts(app)[2]
    assert '<div class="sh">📎 Sources' in texts(app)[2]
    assert "Request and response JSON" in texts(app)[2] and "refund-policy.pdf · p.3" in texts(app)[2]
    assert app.log.children[0] is app.bubbles[-1]  # newest first: the box runs bottom-up
    assert "1 question in this conversation" in app.status.value and "session session-…" in app.status.value
    assert "Answer 1" in app.response_view.value and "streamed" in app.response_view.value
    assert "sessionId&quot;" in app.request_view.value
    app.question.value = "And bank transfers?"
    app.question._handle_custom_msg({"event": "submit"}, [])  # what Enter sends
    assert clients["bedrock-agent-runtime"].called("retrieve_and_generate_stream")[1]["sessionId"] == "session-1"
    app.response_mode.value = "Request sent"
    assert "And bank transfers?" in app.response_view.value


def test_window_retrieve_only_searches_without_an_answer(window, clients):
    app = window._app
    assert app.mode_pick.value == "answer" and app.mode_pick in app.composer.children
    assert "<b>Retrieve only</b>, beside the box, only searches" in texts(app)[0]
    app.question.value = "How long do refunds take?"
    app._send()
    app.chips["temperature"].click()
    app.mode_pick.value = "retrieve"
    assert window.retrieve_only and app.send_button.description == "Retrieve" and app.model_pick.disabled
    assert app.question.value == "How long do refunds take?"  # the last question, to ask it the other way
    assert "Your last question is back in the box: press Enter to see what it retrieves." in app.status.value
    assert "temperature waits for Answer" in app.status.value
    assert "Not sent with Retrieve only" in app.row_notes["temperature"].value
    assert "kbc-off" in app.rows["temperature"]._dom_classes and "kbc-off" not in app.rows["n"]._dom_classes
    assert "Generation · not sent with Retrieve only" in app.headers["Generation"].value
    assert "retrievalQuery" in app.request_view.value and "temperature" not in app.request_view.value
    assert "Retrieve, the search only" in app.request_view.value and "Retrieve (retrieve only)" in app.title.value
    app.question._handle_custom_msg({"event": "submit"}, [])  # Enter
    a = window.answers[-1]
    assert a.retrieve_only and not a.streamed and window.session_id == "session-1"
    assert "Searching" not in texts(app)[-1] and "Retrieve only" in texts(app)[-1]
    assert "3 passages, best first" in texts(app)[-1] and "cited [1]" in texts(app)[-1]
    assert "The answer cites 2 of the 3 passages the search found" in texts(app)[-1]
    assert "Search 2 · " in app.response_view.value and "retrievalResults" in app.response_view.value
    assert "2 questions in this conversation (1 retrieve only)" in app.status.value
    assert app.send_button.description == "Retrieve" and not app.send_button.disabled
    assert clients["bedrock-agent-runtime"].called("retrieve")[-1]["retrievalQuery"] == {
        "text": "How long do refunds take?"}
    # Edit JSON shows the Retrieve request; applying it keeps the answer's settings, which it has no place for
    app.edit_button.click()
    assert '"retrievalQuery"' in app.editor.value
    app.editor.value = app.editor.value.replace('"numberOfResults": 5', '"numberOfResults": 9')
    app._apply()
    assert window.values == {"n": 9, "temperature": 0.2} and window.retrieve_only
    assert "Applied: n 5 → 9." in app.status.value
    # a RetrieveAndGenerate request turns Answer back on
    app.edit_button.click()
    request = json.loads(json.dumps(window._preview(retrieve_only=False)[0]))
    app.editor.value = json.dumps(request)
    app._apply()
    assert not window.retrieve_only and app.mode_pick.value == "answer" and not app.model_pick.disabled
    assert "Answer: questions get an answer" in app.status.value and app.send_button.description == "Send"
    window.retrieve_only = True  # from another cell
    window.set(n=4)
    assert app.mode_pick.value == "retrieve" and app.send_button.description == "Retrieve"


def test_window_opens_on_retrieve_only(core, monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    view = BedrockChatView(core, kb=KB_ID, mode="html", retrieve_only=True, settings={"n": 5, "temperature": 0.3})
    view._display = lambda widget: None
    view.app()
    app = view._app
    assert app.mode_pick.value == "retrieve" and app.model_pick.disabled
    assert app.question.placeholder == "Type a question to search for"
    app.mode_pick.value = "answer"  # no question yet: the box stays empty
    assert app.question.value == "" and "Answer: the next questions get an answer from Claude Haiku 4.5." in (
        app.status.value)
    assert "kbc-off" not in app.rows["temperature"]._dom_classes


def test_window_shows_errors_where_the_answer_would_be(core, monkeypatch):
    def denied(**_):
        raise client_error("AccessDeniedException", "You don't have access to the model with the specified model ID.")

    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    view = BedrockChatView(BedrockChatAnalyzer(clients=fakes(rag=denied, stream=denied)), kb=KB_ID, mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    app.question.value = "How long?"
    app._send()
    assert "msg bot err" in texts(app)[-1] and "Model access" in texts(app)[-1]
    assert app.question.value == "How long?" and view.answers == [] and not app.send_button.disabled
    app.question.value = "x" * 1001
    app._send()
    assert "up to 1,000 characters" in app.status.value
    app.question.value = " "
    app._send()
    assert "Type a question first" in app.status.value


def test_window_request_views_and_an_editor_that_follows_the_settings(window):
    app = window._app
    tree = app.request_view.value
    app._refresh()
    assert app.request_view.value is tree  # the same HTML isn't sent again, so folded parts stay folded
    app.request_mode.value = "JSON"
    assert '<span class="jk">&quot;numberOfResults&quot;</span>: <span class="jn">5</span>' in app.request_view.value
    app.request_mode.value = "Python"
    assert '<span class="pf">retrieve_and_generate</span>' in app.request_view.value
    assert "region_name=&#x27;us-east-1&#x27;" in plain(app.request_view.value).replace("'", "&#x27;")
    app.edit_button.click()
    assert app.request_mode.disabled and app.edit_button.disabled  # the editor is the view while it's open
    app.chips["temperature"].click()  # the editor hasn't been touched: it follows
    assert '"temperature": 0.2' in app.editor.value and "changed" not in app.edit_message.value
    app.editor.value = app.editor.value.replace('"numberOfResults": 5', '"numberOfResults": 7')
    app.chips["top_p"].click()  # it has: the edits stay, and it says what Apply would undo
    assert '"numberOfResults": 7' in app.editor.value and '"topP"' not in app.editor.value
    assert "The request changed since you started editing: top_p = 0.9 (added)." in app.edit_message.value
    app.restart_button.click()
    assert '"topP": 0.9' in app.editor.value and '"numberOfResults": 5' in app.editor.value
    app._cancel_edit()
    assert not app.request_mode.disabled and not app.edit_button.disabled and app.request_view.layout.display == ""


def test_window_applies_edited_json(window):
    app = window._app
    app.edit_button.click()
    assert app.edit_box.layout.display == "" and '"numberOfResults": 5' in app.editor.value
    app.editor.value = app.editor.value.replace('"numberOfResults": 5', '"numberOfResults": 5, "bogus": 1')
    app._apply()
    assert "Bedrock would refuse this request" in app.edit_message.value and "bogus" in app.edit_message.value
    assert window.values == {"n": 5}
    app.editor.value = app.editor.value.replace(', "bogus": 1', "").replace('"numberOfResults": 5', (
        '"numberOfResults": 9, "overrideSearchType": "SEMANTIC"'))
    app._apply()
    assert window.values == {"n": 9, "search_type": "SEMANTIC"} and app.edit_box.layout.display == "none"
    assert app.inputs["n"].value == 9 and app.inputs["search_type"].value == "SEMANTIC"
    assert "Applied: n 5 → 9; search_type = &#x27;SEMANTIC&#x27; (added)." in app.status.value
    app.edit_button.click()
    app.editor.value = "{not json"
    app._apply()
    assert "The request isn&#x27;t valid JSON" in app.edit_message.value
    request = json.loads(json.dumps(app._params))
    request["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]["generationConfiguration"] = {
        "inferenceConfig": {"textInferenceConfig": {"topK": 50}}}
    app.editor.value = json.dumps(request)
    app._apply()
    assert "&quot;topK&quot;, must be one of" in app.edit_message.value
    assert "the model_fields setting" in app.edit_message.value and window.values == {"n": 9, "search_type": "SEMANTIC"}


def test_window_switches_model_and_knowledge_base(core, monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales")])
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    assert view.kb == KB2_ID  # the first active one, by name
    app.question.value = "q"
    app._send()
    click_line(app.model_pick, "us.anthropic.claude-sonnet-5")
    assert view.model == "us.anthropic.claude-sonnet-5" and view.answers  # a new model keeps the conversation
    assert SONNET_PROFILE in app.request_view.value and not app.model_pick.is_open  # a pick closes the list
    assert "Claude Sonnet 5" in app.model_pick.face.value
    click_line(app.kb_pick, KB_ID)
    assert view.kb == KB_ID and view.answers == [] and view.session_id is None
    assert "Now asking support-docs: a new conversation." in texts(app)[-1]


def test_window_finds_a_knowledge_base_by_name_or_id(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales"), kb_summary("ZZZZ999999", "archive", "FAILED")])
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    kb = app.kb_pick
    kb.button.click()
    assert kb.is_open and "kbc-open" in kb.field._dom_classes
    assert kb.shown == ["ZZZZ999999", KB2_ID, KB_ID]  # by name
    assert "kbc-on" in kb.rows[KB_ID][0]._dom_classes and "kbc-on" not in kb.rows[KB2_ID][0]._dom_classes
    assert '<span class="dot bad">' in kb.rows["ZZZZ999999"][2].value and "failed · archive answers" in (
        kb.rows["ZZZZ999999"][2].value)
    assert "3 knowledge bases" in plain(kb.foot.value)
    assert find(kb, "kbid65") == [KB2_ID]  # part of an ID, any case
    assert "<mark>KBID65</mark>4321" in kb.rows[KB2_ID][2].value
    assert "1 of 3 knowledge bases · Enter picks the first" in plain(kb.foot.value)
    assert find(kb, f"arn:aws:bedrock:us-east-1:{ACCOUNT}:knowledge-base/{KB_ID}") == [KB_ID]  # an ARN
    assert find(kb, "answers") == ["ZZZZ999999", KB2_ID, KB_ID]  # their descriptions
    assert find(kb, "SUPPORT") == [KB_ID]
    assert find(kb, "nothing-here") == [] and "No knowledge base matches 'nothing-here'. Enter tries it as an ID." in (
        plain(kb.foot.value))
    enter(kb, "nothing-here")
    assert kb.is_open and "No knowledge base 'nothing-here' in us-east-1." in plain(kb.foot.value)
    enter(kb, "kbid65")  # Enter picks the first line
    assert view.kb == KB2_ID and not kb.is_open and kb.search.value == ""
    assert "Now asking sales: a new conversation." in texts(app)[-1] and "sales" in kb.face.value
    app.model_pick.button.click()
    kb.button.click()  # one list open at a time
    assert kb.is_open and not app.model_pick.is_open
    assert app.backdrop in app.root.children and app.backdrop.layout.display == ""  # over the rest of the window
    kb.button.click()  # a second click closes it
    assert not kb.is_open and app.backdrop.layout.display == "none"  # clicks reach the window again
    app.files_pick.button.click()
    assert app.files_pick.is_open and app.backdrop.layout.display == ""
    app.backdrop.click()  # a click anywhere else in the window closes the list
    assert not app.files_pick.is_open and app.backdrop.layout.display == "none"
    view.use("support-docs")  # from another cell
    assert kb.value == KB_ID and "support-docs" in kb.face.value



def test_window_picks_a_data_source(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales")], sources=TWO_SOURCES)
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    pick = app.source_pick
    assert pick.visible and pick.value == ""
    assert [(c.title, c.value) for c in pick.choices] == [("All data sources", ""), ("faq", DS_ID),
                                                          ("help-site", DS2_ID)]
    assert "<b>Data source</b> asks only one" in texts(app)[0]
    assert find(pick, "dsid") == [DS_ID, DS2_ID] and find(pick, "help") == [DS2_ID]  # by ID or name
    pick.close()
    app.question.value = "q"
    app._send()
    click_line(pick, DS2_ID)
    assert view.data_source == {DS2_ID: "help-site"} and view.answers  # the conversation goes on
    assert "The next questions search data source &#x27;help-site&#x27;" in app.status.value
    assert DS2_ID in app.request_view.value and "data source picker" in app.request_view.value
    assert "data_source='help-site'" in plain(app.setup.value)
    app.question.value = "and?"
    app._send()
    sent = clients["bedrock-agent-runtime"].called("retrieve_and_generate_stream")[-1]
    assert sent[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "equals": {"key": DS_KEY, "value": DS2_ID}}
    assert "only data source &#x27;help-site&#x27;" in texts(app)[-1]
    click_line(pick, "")
    assert view.data_source == {} and DS2_ID not in app.request_view.value
    # Edit JSON: a data source condition in the filter moves the picker
    app.edit_button.click()
    request = json.loads(app.editor.value)
    request[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] = {
        "andAll": [{"in": {"key": DS_KEY, "value": [DS_ID, DS2_ID]}}, {"equals": {"key": "team", "value": "a"}}]}
    app.editor.value = json.dumps(request)
    app._apply()
    assert view.data_source == {DS_ID: "faq", DS2_ID: "help-site"} and view.values["filter"] == {
        "equals": {"key": "team", "value": "a"}}
    assert pick.value == f"{DS_ID},{DS2_ID}" and "faq + help-site" in pick.face.value
    assert ("faq + help-site", f"{DS_ID},{DS2_ID}") in [(c.title, c.value) for c in pick.choices]
    assert "questions search data sources &#x27;faq&#x27; and &#x27;help-site&#x27;" in app.status.value
    # another knowledge base has its own data sources (one here: nothing to pick)
    click_line(app.kb_pick, KB2_ID)
    assert view.data_source is None and pick.value == "" and not pick.visible
    assert "<b>Data source</b>" not in texts(app)[0]


def test_window_data_source_from_another_cell_and_one_it_cant_list(window, monkeypatch, capsys):
    app = window._app
    assert not app.source_pick.visible  # one data source: nothing to pick
    window.use(data_source="docs-s3")
    assert app.source_pick.value == DS_ID and app.source_pick.visible and "docs-s3" in app.source_pick.face.value
    capsys.readouterr()

    def denied(**_):
        raise client_error("AccessDeniedException", "not authorized", "ListDataSources")

    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes()
    clients["bedrock-agent"].handlers["list_data_sources"] = denied
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb=KB_ID, mode="html", data_source=DS2_ID)
    view._display = lambda widget: None
    view.app()
    pick = view._app.source_pick
    assert pick.value == DS2_ID and (DS2_ID, DS2_ID) in [(c.title, c.value) for c in pick.choices]  # an ID works
    assert "Couldn&#x27;t list the data sources (AccessDeniedException; needs bedrock:ListDataSources)" in (
        view._app.status.value)


def test_window_picks_files(window, monkeypatch):
    app = window._app
    files = app.files_pick
    assert files.visible and not files.is_open and files.choices == []  # listed once opened
    assert app.file_bar.layout.display == "none" and "All files" in files.face.value
    assert "<b>Files</b> asks only the files you tick" in texts(app)[0]
    files.button.click()
    assert files.is_open and files.shown == [RETURNS_MD, REFUND_PDF]  # indexed ones only, by path
    assert "2 indexed files · a click ticks or unticks one" in plain(files.foot.value)
    files.rows[REFUND_PDF][1].click()  # ticks it; the list stays open for more
    assert window.picked_files == [REFUND_PDF] and files.is_open and "kbc-on" in files.rows[REFUND_PDF][0]._dom_classes
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]
    assert "1 file" in files.face.value and app.file_bar.layout.display == ""
    assert "1 ticked" in plain(files.foot.value)
    assert REFUND_PDF in app.request_view.value and "files picker" in app.request_view.value
    assert "The next questions search file &#x27;refund-policy.pdf&#x27;" in app.status.value
    enter(files, "RETURNS")  # Enter ticks the only match
    assert window.picked_files == [REFUND_PDF, RETURNS_MD] and files.search.value == ""
    enter(files, "re")
    assert "2 indexed files match 're': click the ones you want" in plain(files.foot.value)
    enter(files, "nothing-like-it")
    assert "No indexed file's path holds 'nothing-like-it'" in plain(files.foot.value)
    enter(files, "s3://docs/elsewhere/unlisted.pdf")  # a full path is taken as it is
    assert window.picked_files == [REFUND_PDF, RETURNS_MD, "s3://docs/elsewhere/unlisted.pdf"]
    assert "3 files" in files.face.value
    app.file_chips.children[-1].click()  # a chip's click stops asking only that file
    assert window.picked_files == [REFUND_PDF, RETURNS_MD]
    app.question.value = "q"
    app._send()
    sent = window.core._runtime_client().called("retrieve_and_generate_stream")[-1]
    assert sent[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "in": {"key": URI_KEY, "value": [REFUND_PDF, RETURNS_MD]}}
    click_line(files, REFUND_PDF)  # a second click unticks it
    assert window.picked_files == [RETURNS_MD]
    app.all_files_button.click()
    assert window.picked_files == [] and app.file_chips.children == () and URI_KEY not in app.request_view.value
    assert "All files" in files.face.value and app.file_bar.layout.display == "none"
    window.use(files=["refund-policy.pdf"])  # from another cell
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]
    assert files.picked == [REFUND_PDF]


def test_window_files_reset_with_another_knowledge_base(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales")])
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="html",
                           files=["refund-policy.pdf"])
    view._display = lambda widget: None
    view.app()
    app = view._app
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]
    app.files_pick.button.click()
    assert app.files_kb == KB_ID and app.files_pick.shown
    click_line(app.kb_pick, KB2_ID)
    assert view.picked_files is None and app.file_chips.children == () and not app.files_pick.is_open
    assert app.files_kb is None and app.files_pick.choices == [] and app.files_pick.picked == []
    bad = BedrockChatView(BedrockChatAnalyzer(clients=fakes()), kb="support-docs", mode="html", files="nope.pdf")
    bad._display = lambda widget: None
    bad.app()
    assert "No file &#x27;nope.pdf&#x27; in the knowledge base. Questions search every file." in bad._app.status.value
    assert bad.picked_files == []


def test_window_follows_other_cells(window, capsys):
    app = window._app
    window.set(temperature=0.4, search_type="semantic")
    assert app.inputs["temperature"].value == 0.4 and app.inputs["search_type"].value == "SEMANTIC"
    window.unset("search_type")
    assert "search_type" not in app.rows
    window.ask("How long?")
    assert "How long?" in texts(app)[-2] and "Refunds take" in texts(app)[-1]
    window.new_chat()
    assert "New conversation" in texts(app)[-1] and len(app.bubbles) == 2
    window.use(model="sonnet")
    assert app.model_pick.value == "us.anthropic.claude-sonnet-5"
    capsys.readouterr()


def test_window_shows_once_per_cell(window, monkeypatch):
    window._ipython_display_()  # chat() in the cell's last line: already shown
    assert len(window._shown) == 1
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 8)
    window._ipython_display_()  # `ui` in a later cell shows it again
    assert len(window._shown) == 2 and window._shown[1] is window._shown[0]


def test_window_without_ipywidgets_says_what_to_install(core, monkeypatch, capsys):
    monkeypatch.setitem(__import__("sys").modules, "ipywidgets", None)
    view = BedrockChatView(core, kb=KB_ID, mode="html")
    shown = []
    view._show = shown.extend
    view.app()
    assert "The chat window needs `ipywidgets` (pip install ipywidgets)" in shown[0].text


def test_window_lists_nothing_it_cant_read(core, monkeypatch):
    def denied(**_):
        raise client_error("AccessDeniedException", "not authorized to perform: bedrock:ListKnowledgeBases",
                           "ListKnowledgeBases")

    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes()
    clients["bedrock-agent"].handlers["list_knowledge_bases"] = denied
    clients["bedrock"].handlers["list_foundation_models"] = denied
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb=KB_ID, model="us.anthropic.claude-opus-5",
                           mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    assert app.kb_pick.choices == [] and app.model_pick.choices == []
    assert (app.kb_pick.value, app.model_pick.value) == (KB_ID, "us.anthropic.claude-opus-5")
    assert KB_ID in app.kb_pick.face.value  # what it was given, as it was given
    assert "Couldn&#x27;t list the knowledge bases (AccessDeniedException; needs bedrock:ListKnowledgeBases)" in (
        app.status.value)
    app.kb_pick.button.click()
    assert "Couldn't list the knowledge bases" in plain(app.kb_pick.foot.value)
    assert "Type one's ID or ARN and press Enter." in plain(app.kb_pick.foot.value)
    enter(app.kb_pick, KB2_ID)  # an ID works without the list
    assert view.kb == KB2_ID and KB2_ID in app.kb_pick.face.value and not app.kb_pick.is_open
    enter(app.kb_pick, KB_ID)
    enter(app.model_pick, "us.anthropic.claude-sonnet-5")
    assert view.model == "us.anthropic.claude-sonnet-5" and "us.anthropic.claude-sonnet-5" in app.model_pick.face.value
    app.question.value = "How long?"
    app._send()
    assert view.answers and "Refunds take" in texts(app)[-1]


def open_window(monkeypatch, clients=None, **kwargs):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients or fakes()), kb="support-docs", mode="html",
                           progress="off", **kwargs)
    view._display = lambda widget: None
    view.app()
    return view


def code_text(app):
    """What the Code tab's code block shows, as text."""
    return plain(re.search(r"<pre[^>]*>(.*)</pre>", app.code_view.value, re.S).group(1))


def test_window_test_tab_asks_a_list_and_shows_how_each_did(monkeypatch, capsys):
    view = open_window(monkeypatch, fakes(rag=by_question))
    app = view._app
    assert [app.tabs.get_title(i) for i in range(6)] == ["Settings", "Test", "Runs", "Code", "Request", "Response"]
    assert "<b>Test</b> asks a list of questions" in texts(app)[0] and "<b>Code</b> gives" in texts(app)[0]
    assert app.run_button.disabled and app.run_button.description == "▶ Run"
    assert "Type or paste questions above." in app.test_note.value
    app.test_box.value = "How long do refunds take? | refund-policy\n2. Do you ship to the moon?\nAnd bank transfers?"
    assert app.run_button.description == "▶ Run 3 questions" and not app.run_button.disabled
    assert "Claude Haiku 4.5 · about $" in app.test_note.value and "(estimate)" in app.test_note.value
    app.run_button.click()  # no event loop here, so the questions are asked right away
    batch = view.batches[-1]
    assert [i.question for i in batch.items] == ["How long do refunds take?", "Do you ship to the moon?",
                                                 "And bank transfers?"]
    assert app.test_rows.children == tuple(app.batch_rows[:3]) and view.answers == []  # not the conversation
    first = app.batch_rows[0].value
    assert "How long do refunds take?" in first and "answered" in first and "cited as [1]" in first
    assert "<sup>[1]</sup>" in first and "Request and response JSON" in first  # the answer, as the chat shows it
    assert "unable to assist" in app.batch_rows[1].value and 'class="bq warn"' in app.batch_rows[1].value
    head = app.test_head.value
    assert 'Test run 1<span class="hint">support-docs · Claude Haiku 4.5 · n=5' in head and "2 of 3" in head
    assert "Bedrock&#x27;s &quot;unable to assist&quot; reply" in head
    assert "Test run 1: 3 of 3 questions came back in " in app.status.value and "ui.results()" in app.status.value
    assert app.run_button.description == "▶ Run 3 questions" and app.stop_button.layout.display == "none"
    assert "questions = [" in code_text(app) and "Do you ship to the moon?" in code_text(app)
    view.set(n=8)  # from another cell; then Run again: each line says what changed
    app.run_button.click()
    assert "↑ was unable to assist" in app.batch_rows[1].value and "Test run 2" in app.test_head.value
    assert "Since the last run (n 5 → 8): 1 question did better" in app.test_head.value
    app.test_box.value = "  "
    app._run_tests()
    assert "Type or paste questions in the Test tab first" in app.status.value and len(view.batches) == 2
    capsys.readouterr()


def test_window_test_tab_runs_in_the_background_and_stops(monkeypatch):
    gate = threading.Event()

    def slow(**params):
        gate.wait(5)
        return rag_resp()

    view = open_window(monkeypatch, fakes(rag=slow))
    app = view._app

    async def main():
        app.test_box.value = "\n".join(f"Question {n}?" for n in range(1, 7))
        app.run_button.click()  # in a notebook the click returns at once, and the questions are asked meanwhile
        task = app.batch_task
        assert task is not None and app.running and app.run_button.disabled
        assert app.run_button.description == "Asking… 0 of 6" and app.stop_button.layout.display == ""
        assert all("asking…" in row.value for row in app.batch_rows[:6])
        await asyncio.sleep(0.3)
        app.stop_button.click()  # four are being answered; the other two aren't sent
        assert app.stop_button.description == "Stopping…" and app.stop_button.disabled
        assert "Stopping: no more questions are sent" in app.status.value
        gate.set()
        await task
        batch = view.batches[-1]
        assert [i.answer is not None for i in batch.items] == [True] * 4 + [False] * 2 and batch.stopped
        assert "not asked" in app.batch_rows[5].value and "answered" in app.batch_rows[0].value
        assert "Stopped before 2 questions were asked" in app.test_head.value
        assert not app.running and app.stop_button.layout.display == "none" and app.batch_task is None
        assert "stopped before the rest were asked" in app.status.value
        assert len(view.core._runtime_client().called("retrieve_and_generate")) == 4

    asyncio.run(main())


def test_window_tabs_stay_on_one_row_with_an_icon_each():
    rules = chatmod._tab_rules()
    assert rules in chatmod._CSS and len(chatmod._TABS) == 6
    for x in ("lm", "p"):  # ipywidgets 8 and 7
        assert f".kbc-side>.{x}-TabBar>.{x}-TabBar-content{{gap:2px;border:0;align-items:stretch;flex-wrap:nowrap}}" \
            in rules
        icons = [f".{x}-TabBar-tab:nth-child({k}) .{x}-TabBar-tabIcon{{--kc-icon:url(\"data:image/svg+xml,%3Csvg"
                 for k in range(1, 7)]
        assert all(icon in rules for icon in icons)
    narrow = rules[rules.index("@container (max-width:479px)"):]
    assert ".lm-TabBar-tabIcon{display:none}" in narrow  # without room for the icons, the titles alone


def test_window_shows_a_test_run_from_another_cell(window, capsys):
    app = window._app
    window.ask_all(["How long?", "And bank transfers? | refund-policy"])
    assert app.test_box.value == "How long?\nAnd bank transfers? | refund-policy" and len(app.test_rows.children) == 2
    assert "Test run 1" in app.test_head.value and app.run_button.description == "▶ Run 2 questions"
    assert "cited as [1]" in app.batch_rows[1].value
    capsys.readouterr()


def test_window_try_variations_asks_every_combination(monkeypatch, capsys):
    view = open_window(monkeypatch, fakes(rag=by_question))
    app = view._app
    assert app.varying_card.layout.display == "none" and app.vary_button.description == "+ Try variations"
    assert "<b>Try variations</b>" in texts(app)[0] and "<b>Runs</b> keeps every test run" in texts(app)[0]
    app.test_box.value = "How long do refunds take? | refund-policy\nDo you ship to the moon?\nAnd bank transfers?"
    app.vary_button.click()
    assert app.varying_card.layout.display == "" and app.vary_button.layout.display == "none"
    assert app.vary_box.value == "n = 5, 10\nsearch_type = SEMANTIC, HYBRID"  # a start, to change
    assert [c.description for c in app.vary_chip_box.children] == ["+ Passages", "+ Search type", "+ Reranker",
                                                                    "+ Model", "+ Temperature"]  # one data source
    assert app.run_button.description == "▶ Run 4 setups × 3 questions"
    assert "12 calls · Claude Haiku 4.5 · about $" in app.test_note.value and "4 setups: every" in app.vary_note.value
    app.vary_chips["model"].click()
    assert app.vary_box.value.endswith("\nmodel = haiku, sonnet") and "Claude Haiku" not in app.test_note.value
    assert app.run_button.description == "▶ Run 8 setups × 3 questions" and "24 calls" in app.test_note.value
    app.vary_box.value = "Passages = 3, 4\nmodel = haiku, sonnet"
    app.vary_chips["n"].click()  # takes the place of the line for passages
    assert app.vary_box.value == "n = 5, 10\nmodel = haiku, sonnet"
    app.vary_box.value = "n = 5, 8\nserch_type = SEMANTIC, HYBRID"
    assert app.run_button.disabled and "No setting &#x27;serch_type&#x27;" in app.vary_note.value
    assert "Fix the line above, or close Try variations" in app.test_note.value
    app.vary_box.value = "n = 5"
    assert app.run_button.disabled and "Those come out as one setup: give two or more values to try, like n = 5, 10." \
        in app.vary_note.value
    app.vary_box.value = "n = 1, 2, 3, 4, 5\nsearch_type = SEMANTIC, HYBRID\nreranker = none, cohere"
    assert "That&#x27;s 20 setups, and Try variations asks up to 16 at a time: try fewer values." in app.vary_note.value
    app.vary_box.value = "n = 5, 8\nsearch_type = SEMANTIC, HYBRID"
    app.run_button.click()  # no event loop here: asked right away
    sweep = view.sweeps[-1]
    assert len(view.batches) == 4 and all(a is b for a, b in zip(view.batches, sweep.batches))
    head = html.unescape(app.sweep_head.value)
    assert "Sweep 1" in head and "Setups, best first" in head and "n=8 · search_type=SEMANTIC did best" in head
    assert app.sweep_bar.layout.display == "" and app.setup_pick.value == 3 and sweep.best is view.batches[2]
    assert [label for label, _ in app.setup_pick.options][0] == "#1 · run 3 · n=8 · search_type=SEMANTIC"
    assert "Test run 3 (n=8 · search_type=SEMANTIC)" in app.test_head.value and len(app.test_rows.children) == 3
    assert "Sweep 1: 4 setups × 3 questions in " in app.status.value and "best: run 3" in app.status.value
    assert app.run_button.description == "▶ Run 4 setups × 3 questions" and view.answers == []
    app.setup_pick.value = 1  # n=5: its answers show under the ranking
    assert "Test run 1 (n=5 · search_type=SEMANTIC)" in app.test_head.value
    assert "unable to assist" in app.batch_rows[1].value
    app.use_setup_button.click()
    assert view.values == {"n": 5, "search_type": "SEMANTIC"} and "Now using the setup of run 1" in app.status.value
    app.close_vary.click()  # Run asks with one setup again
    assert app.varying_card.layout.display == "none" and app.run_button.description == "▶ Run 3 questions"
    app.run_button.click()
    assert app.sweep_head.value == "" and app.sweep_bar.layout.display == "none" and "Test run 5" in app.test_head.value
    assert "Since the last run (the same setup)" in html.unescape(app.test_head.value)  # the sweep's same setup
    view.sweep(n=[3, 8])  # from another cell: the window shows it
    assert "Sweep 2" in app.sweep_head.value and app.setup_pick.value == view._run_number(view.sweeps[-1].best)
    capsys.readouterr()


def test_window_asks_twice_before_a_costly_sweep(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes()
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients, model_prices={"claude-haiku-4-5": (900.0, 900.0)}),
                           kb="support-docs", mode="html", progress="off")
    view._display = lambda widget: None
    view.app()
    app = view._app
    app.test_box.value = "How long?\nBank?"
    app.vary_button.click()
    app.run_button.click()
    assert "more than the $2.00 the window asks without checking: click Run again" in app.status.value
    assert app.run_button.description.startswith("▶ Run anyway (about $") and view.batches == []
    assert not clients["bedrock-agent-runtime"].called("retrieve_and_generate")
    app.run_button.click()
    assert len(view.batches) == 4 and len(clients["bedrock-agent-runtime"].called("retrieve_and_generate")) == 8
    assert app.run_button.description == "▶ Run 4 setups × 2 questions"


def test_window_sweep_runs_in_the_background_and_stops(monkeypatch):
    gate = threading.Event()

    def slow(**params):
        gate.wait(5)
        return rag_resp()

    view = open_window(monkeypatch, fakes(rag=slow))
    app = view._app

    async def main():
        app.test_box.value = "One?\nTwo?\nThree?"
        app.vary_button.click()
        app.vary_box.value = "n = 5, 8"
        app.run_button.click()  # returns at once: four questions are asked meanwhile, the first two of each setup
        task = app.batch_task
        assert task is not None and app.running and app.run_button.description == "Asking… 0 of 6"
        assert "0 of 3 back" in app.sweep_head.value and 'class="spin"' in app.sweep_head.value
        assert app.stop_button.layout.display == "" and app.sweep_bar.layout.display == "none"
        await asyncio.sleep(0.3)
        app.stop_button.click()
        gate.set()
        await task
        sweep = view.sweeps[-1]
        assert sweep.stopped and [[i.answer is not None for i in b.items] for b in sweep.batches] == [
            [True, True, False]] * 2
        assert "Ranked on the 2 questions every setup came back with" in html.unescape(app.sweep_head.value)
        assert not app.running and app.stop_button.layout.display == "none" and app.batch_task is None
        assert "stopped before every setup was asked every question" in app.status.value

    asyncio.run(main())


def test_window_runs_tab_lists_shows_switches_compares_and_saves(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    view = open_window(monkeypatch, fakes(rag=by_question))
    app = view._app
    assert "No test runs yet" in app.runs_view.value and app.run_actions.layout.display == "none"
    view.ask_all(["How long? | refund-policy", "Do you ship to the moon?"], label="baseline")  # from other cells
    view.sweep(n=[5, 8])
    runs = html.unescape(app.runs_view.value)
    assert "sweep 1: n=8" in runs and "baseline" in runs and "1 of 3" in runs and "results(3)" in runs
    assert "did better than your setup now" in runs and app.run_actions.layout.display == ""
    assert [label for label, _ in app.run_pick.options] == [
        "Run 3 · sweep 1: n=8 · 2 of 2 answered · 69% grounded", "Run 2 · sweep 1: n=5 · 1 of 2 answered · 69% grounded",
        "Run 1 · baseline · 1 of 2 answered · 69% grounded"]
    assert "These runs are in this notebook's memory only: Save keeps them" in html.unescape(app.runs_note.value)
    app.run_pick.value = 1
    app.show_run_button.click()
    assert app.tabs.selected_index == app.TEST_TAB and "Test run 1 (baseline)" in app.test_head.value
    assert app.sweep_bar.layout.display == "none" and "The Test tab shows run 1 (baseline)." in app.status.value
    app.run_pick.value = 2
    app.show_run_button.click()  # a sweep's run: the sweep, with that setup's answers under it
    assert "Sweep 1" in app.sweep_head.value and app.setup_pick.value == 2 and "Test run 2 (n=5)" in app.test_head.value
    app.run_pick.value = 3
    app.use_run_button.click()
    assert view.values == {"n": 8} and "Now using the setup of run 3 (sweep 1: n=8): n 5 → 8." in app.status.value
    app.compare_button.click()
    assert "3 test runs on support-docs compared" in html.unescape(app.compare_view.value)
    view.ask_all(["Something else?"])
    app.run_pick.value = 4
    app.compare_button.click()
    assert "Only run 4 asked these questions this way" in html.unescape(app.compare_view.value)
    app.runs_file.value = "mine.jsonl"
    app.save_runs_button.click()
    assert "Saved 4 test runs to mine.jsonl" in app.runs_note.value and view.log == "mine.jsonl"
    assert len((tmp_path / "mine.jsonl").read_text().splitlines()) == 4
    view.ask_all(["Something else?"])  # added to the file as it finishes
    assert len((tmp_path / "mine.jsonl").read_text().splitlines()) == 5
    assert "Every run is added to mine.jsonl as it finishes." in app.runs_note.value
    other = open_window(monkeypatch, fakes(rag=by_question))  # after a restart
    other._app.runs_file.value = "mine.jsonl"
    other._app.load_runs_button.click()
    assert len(other.batches) == 5 and "Loaded 5 test runs from mine.jsonl." in other._app.runs_note.value
    assert "Test run 5" in other._app.test_head.value and other._app.test_box.value == "Something else?"
    other._app.runs_file.value = "nope.jsonl"
    other._app.load_runs_button.click()
    assert "There&#x27;s no file nope.jsonl" in other._app.status.value
    app.tabs.selected_index = app.RUNS_TAB  # drawn again when looked at
    assert "Run 5 · " in app.run_pick.options[0][0]
    capsys.readouterr()


def test_window_opens_on_the_test_questions_and_the_last_run(core, monkeypatch, capsys):
    view = BedrockChatView(core, kb="support-docs", mode="text", progress="off", questions=["One?", "Two? | two.md"])
    view.ask_all()
    capsys.readouterr()
    view.use_html = True
    view._display = lambda widget: None
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    view.app()
    app = view._app
    assert app.test_box.value == "One?\nTwo? | two.md" and len(app.test_rows.children) == 2
    assert "Test run 1" in app.test_head.value and "expected not cited" in app.batch_rows[1].value


def test_window_code_tab_follows_the_setup(window):
    app = window._app
    assert "A script that asks a question (put yours in) with this setup" in app.code_view.value
    assert "client.retrieve_and_generate(**request)" in code_text(app) and "'numberOfResults': 5" in code_text(app)
    app.chips["temperature"].click()
    assert "'temperature': 0.2" in code_text(app)
    app.test_box.value = "How long?\nAnd bank transfers?"
    assert "questions = ['How long?', 'And bank transfers?']" in code_text(app)
    window.set(guardrail_id="gr-1")
    assert "Bedrock would refuse this request, so the code would fail the same way" in app.code_view.value
    window.unset("guardrail_id")
    app.code_mode.value = "JSON"
    assert json.loads(code_text(app)) == config_of(window._preview()[0])
    assert "Save it as bedrock-config.json" in app.code_view.value
    app.code_mode.value = "AWS CLI"
    words = shlex.split(code_text(app).replace("\\\n", " "))
    assert json.loads(words[words.index("--cli-input-json") + 1])["input"] == {"text": "How long?"}
    app.mode_pick.value = "retrieve"  # the search-only setup
    assert code_text(app).startswith("aws bedrock-agent-runtime retrieve \\")
    app.code_mode.value = "Python"
    assert "client.retrieve(retrievalQuery={'text': question}, **CONFIG)" in code_text(app)
    assert "temperature" not in code_text(app)


def test_batch_to_df():
    pytest.importorskip("pandas")
    df = run_of(asked("How long?", expected="refund-policy", cost=0.01),
                BatchItem("Bank?", error="Rate exceeded.", error_code="ThrottlingException")).to_df()
    assert list(df["result"]) == ["answered", "failed"] and df.loc[0, "found"] == 1
    assert df.loc[1, "error"] == "Rate exceeded." and df.loc[0, "sources"] == ["refund-policy.pdf p.3",
                                                                               "refund-policy.pdf p.4"]
    assert 0 < df.loc[0, "grounded"] < 1 and df.loc[0, "cost"] == 0.01


def test_sweep_to_df():
    pytest.importorskip("pandas")
    sweep = BedrockChatAnalyzer(clients=fakes(rag=by_question)).sweep(
        KB_ID, ["How long? | refund-policy", "Do you ship to the moon?"], [{"n": 5}, {"n": 8}])
    df = sweep.to_df()
    assert list(df["rank"]) == [1, 2] and list(df["n"]) == [8, 5]  # best first
    assert list(df["answered"]) == [2, 1] and list(df["expected_hits"]) == [1, 1] and list(df["failed"]) == [0, 0]
    assert df["cost"].sum() == pytest.approx(sweep.cost)


def test_answer_to_df():
    pytest.importorskip("pandas")
    df = Answer(**{**vars(answer())}).to_df()
    assert list(df["n"]) == [1, 2] and df.loc[0, "source"] == "refund-policy.pdf" and df.loc[1, "page"] == 4
    df = Answer("q", "", sources=parse_retrieve(RETRIEVED), retrieve_only=True).to_df()
    assert list(df["score"]) == [0.81, 0.62, 0.4]
