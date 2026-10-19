"""
RetrieveAndGenerate's settings, from botocore's service model: each Field's path, kind,
range and docs, with the short names and plain-English docs the chat knows.
"""

from __future__ import annotations

import botocore.session
from dataclasses import dataclass
from typing import Any

from ..helpers import DEFAULT_PROMPT, _number, _plain_doc, _sentences, _words_of
from ..models import Schema


@dataclass
class Field:
    """
    A setting the chat can send with every question: where it goes in the RetrieveAndGenerate request, what it
    takes, and what it does.
    """

    key: str  # what set() takes: a short name ('temperature') or, for fields without one, the dotted path
    path: tuple[str, ...]  # from the request's root
    kind: str  # 'integer' | 'float' | 'boolean' | 'choice' | 'text' | 'long_text' | 'list' | 'json'
    label: str = ""
    doc: str = ""  # what it does, in plain English
    group: str = ""  # 'Retrieval' | 'Generation' | 'Orchestration' | 'Session'
    choices: tuple[str, ...] = ()  # kind 'choice'
    low: float | None = None  # numbers: the smallest value; text: the shortest length; lists: the fewest items
    high: float | None = None  # numbers: the largest value; text: the longest length; lists: the most items
    names: tuple[str, ...] = ()  # other names set() accepts ('topP' for top_p)
    default: Any = None  # what a new row in the chat window starts with (None: empty until you fill it in)
    common: bool = False  # offered as a one-click "+" button in the chat window
    container: str = ""  # kind 'json': 'object' or 'list'; '' when either is fine
    placeholder: str = ""  # an example, shown in an empty box

    @property
    def where(self) -> str:
        """
        The dotted path as the JSON shows it, from knowledgeBaseConfiguration (or from the request's root).
        """
        inside = self.path[: len(_KB_CONFIG)] == _KB_CONFIG
        return ".".join(self.path[len(_KB_CONFIG) :] if inside else self.path)


# -------------------------------------------------------------- the settings

_KB_CONFIG = ("retrieveAndGenerateConfiguration", "knowledgeBaseConfiguration")

_VECTOR = "retrievalConfiguration.vectorSearchConfiguration"

_FILTER_PATH = (*_KB_CONFIG, "retrievalConfiguration", "vectorSearchConfiguration", "filter")

_RERANKING = f"{_VECTOR}.rerankingConfiguration.bedrockRerankingConfiguration"

_GENERATION = "generationConfiguration"

_INFERENCE = f"{_GENERATION}.inferenceConfig.textInferenceConfig"

_ORCHESTRATION = "orchestrationConfiguration"

# The settings people change most, with short names, plain-English help and the value a new row starts with.
# (key, path from knowledgeBaseConfiguration or '/' + path from the request's root, label, other names, default,
#  offered as a one-click button, what it does, an example for an empty box)
_KNOWN: list[tuple[str, str, str, tuple[str, ...], Any, bool, str, str]] = [
    ("n", f"{_VECTOR}.numberOfResults", "Passages", ("number_of_results", "numberOfResults", "passages"), 5, True,
     "How many passages Bedrock retrieves and gives the model. More gives the model more to go on, and costs more "
     "input tokens. Bedrock's default is 5.", ""),
    ("search_type", f"{_VECTOR}.overrideSearchType", "Search type", ("overrideSearchType",), "HYBRID", True,
     "HYBRID matches meaning and exact words, which helps with names, codes and IDs; SEMANTIC matches meaning only. "
     "Without it Bedrock picks. HYBRID needs a vector store that has it (OpenSearch Serverless with a text field).",
     ""),
    ("filter", f"{_VECTOR}.filter", "Metadata filter", ("where", "metadata_filter"), None, True,
     "Only search documents whose metadata matches: {\"team\": \"billing\"}, {\"year\": [\">=\", 2024]}, or a "
     "Bedrock filter like {\"equals\": {\"key\": \"team\", \"value\": \"billing\"}}. Metadata comes from each "
     "file's .metadata.json.", '{"team": "billing", "year": [">=", 2024]}'),
    ("reranker", f"{_RERANKING}.modelConfiguration.modelArn", "Reranker", ("rerank_model", "reranking_model"), "cohere", True,
     "A reranking model re-orders the passages by how well they answer the question, before the model sees them: "
     "'cohere' (Cohere Rerank 3.5) or 'amazon' (Amazon Rerank 1.0), or a model ID or ARN. Billed per question.",
     "cohere, amazon, or a model ARN"),
    ("rerank_n", f"{_RERANKING}.numberOfRerankedResults", "Passages after reranking", ("numberOfRerankedResults",),
     5, False, "How many of the reranked passages the model sees: retrieve more (n=20), keep the best few.", ""),
    ("temperature", f"{_INFERENCE}.temperature", "Temperature", (), 0.2, True,
     "Randomness, 0 to 1. Low (0 to 0.3) gives steady, factual answers; high gives more varied wording. Newer Claude "
     "models take temperature or top_p, not both.", ""),
    ("top_p", f"{_INFERENCE}.topP", "Top P", ("topP",), 0.9, True,
     "Picks each word from the most likely ones that add up to this share (0 to 1). Lower is more predictable. "
     "Newer Claude models take temperature or top_p, not both.", ""),
    ("max_tokens", f"{_INFERENCE}.maxTokens", "Max answer tokens", ("maxTokens",), 2048, True,
     "The longest answer the model may write, in tokens (a token is about 4 characters). An answer that reaches it "
     "stops mid-sentence.", ""),
    ("stop", f"{_INFERENCE}.stopSequences", "Stop sequences", ("stop_sequences", "stopSequences"), None, False,
     "Up to 4 pieces of text that end the answer when the model writes them. One per line.", "one per line"),
    ("prompt", f"{_GENERATION}.promptTemplate.textPromptTemplate", "Prompt template",
     ("prompt_template", "textPromptTemplate"), DEFAULT_PROMPT, True,
     "Your own instructions for the model, up to 4,000 characters. $search_results$ is where Bedrock puts the "
     "passages (required); $output_format_instructions$ is where it asks for citations (keep it, or answers lose "
     "them); $query$ is the question.", ""),
    ("model_fields", f"{_GENERATION}.additionalModelRequestFields", "Extra model fields",
     ("additional_model_request_fields", "additionalModelRequestFields"), None, False,
     "Settings of the model itself, passed through as they are, as JSON: {\"top_k\": 50} for Claude, for example.",
     '{"top_k": 50}'),
    ("guardrail_id", f"{_GENERATION}.guardrailConfiguration.guardrailId", "Guardrail ID", ("guardrailId", "guardrail"),
     None, False, "A Bedrock guardrail that screens the question and the answer. Needs guardrail_version too.",
     "the guardrail's ID"),
    ("guardrail_version", f"{_GENERATION}.guardrailConfiguration.guardrailVersion", "Guardrail version",
     ("guardrailVersion",), "DRAFT", False, "The guardrail's version: a number like '1', or 'DRAFT'.", ""),
    ("latency", f"{_GENERATION}.performanceConfig.latency", "Latency", (), "optimized", False,
     "'optimized' uses latency-optimized inference, where the model and region offer it; 'standard' is the default.",
     ""),
    ("query_decomposition", f"{_ORCHESTRATION}.queryTransformationConfiguration.type", "Query decomposition",
     ("queryTransformationConfiguration", "decompose"), "QUERY_DECOMPOSITION", True,
     "Bedrock splits a complicated question into simpler ones, searches for each, then answers from all of them. "
     "Better for questions with several parts; slower.", ""),
    ("orchestration_prompt", f"{_ORCHESTRATION}.promptTemplate.textPromptTemplate", "Orchestration prompt", (), None,
     False, "The prompt of the step that rewrites the question before searching (with query decomposition).", ""),
    ("kms_key", "/sessionConfiguration.kmsKeyArn", "Session KMS key", ("kmsKeyArn",), None, False,
     "A KMS key that encrypts the conversation Bedrock keeps for follow-up questions.", "arn:aws:kms:..."),
]

_GROUP_OF = {"retrievalConfiguration": "Retrieval", "generationConfiguration": "Generation",
             "orchestrationConfiguration": "Orchestration"}

_GROUP_ORDER = ("Retrieval", "Generation", "Orchestration", "Session")

# Picked in the window or set by the chat itself, so not settings: the question, the session, the knowledge base,
# the model, the request type, external sources, and managed knowledge bases (RetrieveAndGenerate can't ask those).
_NOT_SETTINGS = {
    ("input",), ("sessionId",), ("retrieveAndGenerateConfiguration", "type"),
    ("retrieveAndGenerateConfiguration", "externalSourcesConfiguration"),
    (*_KB_CONFIG, "knowledgeBaseId"), (*_KB_CONFIG, "modelArn"),
    (*_KB_CONFIG, "retrievalConfiguration", "managedSearchConfiguration"),
}


def _known_path(text: str) -> tuple[str, ...]:
    return tuple(text[1:].split(".")) if text.startswith("/") else (*_KB_CONFIG, *text.split("."))


def _children(shape: Any) -> list[Any]:
    if shape.type_name == "structure":
        return list(shape.members.values())
    if shape.type_name == "list":
        return [shape.member]
    if shape.type_name == "map":
        return [shape.value]
    return []


def _recursive(shape: Any) -> bool:
    """
    Whether a botocore shape contains itself (RetrievalFilter holds lists of RetrievalFilters).
    """
    seen: set[str] = set()
    todo = _children(shape)
    while todo:
        child = todo.pop()
        if child.name == shape.name:
            return True
        if child.name not in seen:
            seen.add(child.name)
            todo += _children(child)
    return False


def _kind(shape: Any) -> str:
    """
    A botocore shape -> how the chat edits it: 'structure' (open it up), a scalar kind, 'list' (of text) or
    'json' (anything nested: filters, maps, lists of objects).
    """
    kind = shape.type_name
    if kind == "structure":
        return "json" if getattr(shape, "is_document_type", False) or _recursive(shape) else "structure"
    if kind in ("integer", "long"):
        return "integer"
    if kind in ("float", "double"):
        return "float"
    if kind == "boolean":
        return "boolean"
    if kind == "string":
        return "choice" if shape.enum else "long_text" if shape.metadata.get("max", 0) > 2048 else "text"
    if kind == "list" and shape.member.type_name == "string" and not shape.member.enum:
        return "list"
    return "json"


def _default_for(name: str, kind: str, low: float | None, high: float | None, choices: tuple[str, ...]) -> Any:
    """
    What a new row of a field without a curated default starts with; None leaves it empty until filled in.
    """
    if kind == "integer":
        guess = 2048 if name == "maxTokens" else 5 if name.startswith("numberOf") else 1
        return int(min(max(guess, low if low is not None else guess), high if high is not None else guess))
    if kind == "float":
        guess = {"temperature": 0.2, "topP": 0.9}.get(name, 0.0)
        return float(min(max(guess, low if low is not None else guess), high if high is not None else guess))
    if kind == "boolean":
        return False
    if kind == "choice":
        return choices[0]
    return None


def request_schema(service_model: Any = None) -> Schema:
    """
    Every setting RetrieveAndGenerate takes, read from botocore's service model: its path in the request, type,
    range and documentation, plus this chat's short names ('temperature', 'n', 'reranker'), plain-English help and
    starting values. service_model is a botocore ServiceModel for bedrock-agent-runtime (default: the installed
    boto3's). Reads local files only; no AWS call.
    """
    if service_model is None:
        service_model = botocore.session.Session().get_service_model("bedrock-agent-runtime")
    try:
        shape = service_model.operation_model("RetrieveAndGenerate").input_shape
    except Exception:  # OperationNotFoundError on a boto3 from before knowledge bases
        raise ValueError(
            f"This boto3 (botocore {botocore.__version__}) doesn't know RetrieveAndGenerate: pip install -U boto3"
        ) from None
    known = {_known_path(entry[1]): entry for entry in _KNOWN}
    found: list[Field] = []
    auto: list[tuple[tuple[str, ...], str]] = []

    def walk(struct: Any, path: tuple[str, ...]) -> None:
        for name in struct.required_members:  # e.g. rerankingConfiguration.type, which can only be one thing
            member = struct.members[name]
            if member.type_name == "string" and len(member.enum or []) == 1 and (*path, name) not in known:
                auto.append(((*path, name), member.enum[0]))
        for name, member in struct.members.items():
            here = (*path, name)
            if here in _NOT_SETTINGS or here in dict(auto):
                continue
            kind = _kind(member)
            if kind == "structure":
                walk(member, here)
                continue
            low, high = member.metadata.get("min"), member.metadata.get("max")
            choices = tuple(member.enum or ()) if kind == "choice" else ()
            inside = here[: len(_KB_CONFIG)] == _KB_CONFIG
            group = _GROUP_OF.get(here[len(_KB_CONFIG)], "Session") if inside and len(here) > 2 else "Session"
            entry = known.get(here)
            doc = _sentences(_plain_doc(member.documentation), 2)
            container = ""
            if kind == "json":
                container = {"map": "object", "structure": "object", "list": "list"}.get(member.type_name, "")
                container = "" if getattr(member, "is_document_type", False) else container
            f = Field(
                key=entry[0] if entry else ".".join(here[len(_KB_CONFIG) :] if inside else here),
                path=here,
                kind=kind,
                label=entry[2] if entry else _words_of(name),
                doc=entry[6] if entry else doc,
                group=group,
                choices=choices,
                low=low,
                high=high,
                names=entry[3] if entry else (),
                default=entry[4] if entry else _default_for(name, kind, low, high, choices),
                common=entry[5] if entry else False,
                container=container,
                placeholder=entry[7] if entry else ("JSON" if kind == "json" else ""),
            )
            found.append(f)

    walk(shape, ())
    order = {path: i for i, path in enumerate(known)}
    found.sort(key=lambda f: (_GROUP_ORDER.index(f.group), order.get(f.path, len(order))))
    try:
        retrieve = service_model.operation_model("Retrieve").input_shape
    except Exception:  # OperationNotFoundError
        retrieve = None
    return Schema({f.key: f for f in found}, auto, shape, botocore.__version__, retrieve)


def _check_range(f: Field, number: float) -> None:
    low, high = f.low, f.high
    if (low is not None and number < low) or (high is not None and number > high):
        span = (
            f"{_number(low)} to {_number(high)}" if low is not None and high is not None
            else f"{_number(low)} or more" if low is not None else f"up to {_number(high or 0)}"
        )
        raise ValueError(f"{f.key} can be {span}; got {_number(number)}")
