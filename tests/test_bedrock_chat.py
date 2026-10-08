import ast
import html
import json
import re
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
    as_filter,
    build_request,
    build_retrieve_request,
    cited_ranks,
    compare_findings,
    coerce_setting,
    collect_stream,
    describe_filter,
    describe_setting,
    normalize_settings,
    parse_rag,
    parse_retrieve,
    python_call,
    request_schema,
    retrieve_settings,
    settings_findings,
    describe_files,
    file_labels,
    match_files,
    parse_document,
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
SONNET_PROFILE = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.{SONNET}"
OPUS_PROFILE = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.anthropic.claude-opus-5"


def model(model_id, name, provider="Anthropic", on_demand=False):
    return {"modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{model_id}", "modelId": model_id,
            "modelName": name, "providerName": provider, "inputModalities": ["TEXT"], "outputModalities": ["TEXT"],
            "inferenceTypesSupported": ["ON_DEMAND"] if on_demand else [], "modelLifecycle": {"status": "ACTIVE"}}


CLAUDES = ["anthropic.claude-opus-5", SONNET]
MODEL_LIST = [model(CLAUDES[0], "Claude Opus 5"), model(SONNET, "Claude Sonnet 5"),
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
                 "Sources cited: 2", "Model: Claude Opus 5", "Refunds take 5-7 business days [1].",
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
    assert "Bedrock (Claude Opus 5 · " in out and "  [1] refund-policy.pdf p.3" in out
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


def test_window_opens_on_the_knowledge_base_and_model(window):
    app = window._app
    assert "kbc-app" in app.root._dom_classes
    assert (app.kb_pick.value, app.model_pick.value) == (KB_ID, "us.anthropic.claude-opus-5")
    assert ("Claude Opus 5 · Anthropic · $5.50 / $27.50 per 1M tokens", "us.anthropic.claude-opus-5") in (
        app.model_pick.options)
    assert [label for label, _ in app.model_pick.options][0].startswith("Nova Pro · Amazon")  # by provider
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
    assert "chat('support-docs', model='us.anthropic.claude-opus-5', n=5)" in plain(app.setup.value)
    assert '<span class="pf">chat</span>' in app.setup.value  # highlighted, like the Python view
    assert app.results.children == ()  # nothing is listed under the search box until you search
    assert "Nothing yet" in app.response_view.value


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
    assert app.question.value == "" and "Answer: the next questions get an answer from Claude Opus 5." in (
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
    app.model_pick.value = "us.anthropic.claude-sonnet-5"
    assert view.model == "us.anthropic.claude-sonnet-5" and view.answers  # a new model keeps the conversation
    assert SONNET_PROFILE in app.request_view.value
    app.kb_pick.value = KB_ID
    assert view.kb == KB_ID and view.answers == [] and view.session_id is None
    assert "Now asking support-docs: a new conversation." in texts(app)[-1]



def test_window_picks_a_data_source(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales")], sources=TWO_SOURCES)
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="html")
    view._display = lambda widget: None
    view.app()
    app = view._app
    pick = app.source_pick
    assert pick.layout.display == "" and pick.value == ""
    assert list(pick.options) == [("All data sources", ""), ("faq", DS_ID), ("help-site", DS2_ID)]
    assert "<b>Data source</b> asks only one" in texts(app)[0]
    app.question.value = "q"
    app._send()
    pick.value = DS2_ID
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
    pick.value = ""
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
    assert pick.value == f"{DS_ID},{DS2_ID}" and ("faq + help-site", f"{DS_ID},{DS2_ID}") in pick.options
    assert "questions search data sources &#x27;faq&#x27; and &#x27;help-site&#x27;" in app.status.value
    # another knowledge base has its own data sources (one here: nothing to pick)
    app.kb_pick.value = KB2_ID
    assert view.data_source is None and pick.value == "" and pick.layout.display == "none"
    assert "<b>Data source</b>" not in texts(app)[0]


def test_window_data_source_from_another_cell_and_one_it_cant_list(window, monkeypatch, capsys):
    app = window._app
    assert app.source_pick.layout.display == "none"  # one data source: nothing to pick
    window.use(data_source="docs-s3")
    assert app.source_pick.value == DS_ID and app.source_pick.layout.display == ""
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
    assert pick.value == DS2_ID and (DS2_ID, DS2_ID) in pick.options  # an ID still works
    assert "Couldn&#x27;t list the data sources (AccessDeniedException; needs bedrock:ListDataSources)" in (
        view._app.status.value)


def test_window_picks_files(window, monkeypatch):
    app = window._app
    assert app.files_box.layout.display == "" and app.file_box.layout.display == "none"
    assert app.pick_files_button.layout.display == "" and app.all_files_button.layout.display == "none"
    assert "Pick files</b> asks only the files you pick" in texts(app)[0]
    app.pick_files_button.click()
    assert app.file_box.layout.display == "" and app.pick_files_button.layout.display == "none"
    assert list(app.file_box.options) == ["faq/returns.md", "policies/refund-policy.pdf"]  # indexed ones only
    assert "2 indexed files to pick from" in app.status.value
    app.file_box.value = "policies/refund-policy.pdf"  # chosen from the list
    assert window.picked_files == [REFUND_PDF] and app.file_box.value == ""
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]
    assert app.all_files_button.layout.display == ""
    assert REFUND_PDF in app.request_view.value and "files picker" in app.request_view.value
    assert "The next questions search file &#x27;refund-policy.pdf&#x27;" in app.status.value
    app.file_box.value = "RETURNS"
    app.file_box._handle_custom_msg({"event": "submit"}, [])  # Enter takes the only match
    assert window.picked_files == [REFUND_PDF, RETURNS_MD]
    app.file_box.value = "re"
    app.file_box._handle_custom_msg({"event": "submit"}, [])
    assert "2 files contain &#x27;re&#x27;: choose one from the list" in app.status.value
    app.file_box.value = "nothing-like-it"
    app.file_box._handle_custom_msg({"event": "submit"}, [])
    assert "No indexed file&#x27;s path contains &#x27;nothing-like-it&#x27;" in app.status.value
    app.question.value = "q"
    app._send()
    sent = window.core._runtime_client().called("retrieve_and_generate_stream")[-1]
    assert sent[KB[0]][KB[1]]["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"] == {
        "in": {"key": URI_KEY, "value": [REFUND_PDF, RETURNS_MD]}}
    app.file_chips.children[0].click()  # a chip's click stops asking only that file
    assert window.picked_files == [RETURNS_MD]
    app.all_files_button.click()
    assert window.picked_files == [] and app.file_chips.children == () and URI_KEY not in app.request_view.value
    window.use(files=["refund-policy.pdf"])  # from another cell
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]


def test_window_files_reset_with_another_knowledge_base(monkeypatch):
    monkeypatch.setattr(chatmod, "_cell_number", lambda: 1)
    clients = fakes(kbs=[kb_summary(), kb_summary(KB2_ID, "sales")])
    view = BedrockChatView(BedrockChatAnalyzer(clients=clients), kb="support-docs", mode="html",
                           files=["refund-policy.pdf"])
    view._display = lambda widget: None
    view.app()
    app = view._app
    assert [chip.description for chip in app.file_chips.children] == ["refund-policy.pdf ✕"]
    app.pick_files_button.click()
    app.kb_pick.value = KB2_ID
    assert view.picked_files is None and app.file_chips.children == () and app.file_box.layout.display == "none"
    assert app.files_kb is None and app.pick_files_button.layout.display == ""
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
    assert isinstance(app.kb_pick, widgets.Text) and isinstance(app.model_pick, widgets.Text)
    assert "Couldn&#x27;t list the knowledge bases (AccessDeniedException; needs bedrock:ListKnowledgeBases)" in (
        app.status.value)
    app.question.value = "How long?"
    app._send()
    assert view.answers and "Refunds take" in texts(app)[-1]


def test_answer_to_df():
    pytest.importorskip("pandas")
    df = Answer(**{**vars(answer())}).to_df()
    assert list(df["n"]) == [1, 2] and df.loc[0, "source"] == "refund-policy.pdf" and df.loc[1, "page"] == 4
    df = Answer("q", "", sources=parse_retrieve(RETRIEVED), retrieve_only=True).to_df()
    assert list(df["score"]) == [0.81, 0.62, 0.4]
