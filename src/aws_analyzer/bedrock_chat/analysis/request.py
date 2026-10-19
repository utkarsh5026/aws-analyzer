"""
The request: settings coerced to their field's kind, placed in RetrieveAndGenerate's (or
Retrieve's) request, read back from an edited one, validated, and described.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Iterable

from botocore.validate import ParamValidator

from ..._bedrock.blocks import _plain_json
from ..._bedrock.filters import with_data_sources
from ..._bedrock.prices import DEFAULT_RERANK_MODEL, _RERANK_ALIASES
from ..._kit.fmt import _as_int, _plural

from ..helpers import _loads, _number, _sentences, _short
from ..models import Schema
from .schema import _FILTER_PATH, _KB_CONFIG, Field, _check_range
from .filters import SOURCE_URI_KEY, as_filter, describe_filter, split_condition, split_data_sources, with_files
from .choices import rerank_arn


def coerce_setting(f: Field, value: Any) -> Any:
    """
    The value to send for field `f`, from what someone typed or passed: '0.2' -> 0.2, '5' -> 5, 'hybrid' ->
    'HYBRID', JSON text -> a dict, lines -> a list. A ValueError says what the field takes.
    """
    if f.key == "reranker":
        if value is True:
            return "cohere"
        text = str(value).strip()
        if not text or value is False:
            raise ValueError("reranker takes 'cohere', 'amazon', or a reranking model's ID or ARN")
        for short, model_id in _RERANK_ALIASES.items():
            if text.lower() in (short, model_id) or text.endswith("/" + model_id):
                return short  # its ARN depends on the region; build_request() writes it
        return text
    if f.kind in ("integer", "float") and isinstance(value, bool):
        raise ValueError(f"{f.key} takes a number; got {value!r}")
    if f.kind == "integer":
        number = _as_int(value.strip() if isinstance(value, str) else value, f.key)
        _check_range(f, number)
        return number
    if f.kind == "float":
        try:
            number = float(value.strip() if isinstance(value, str) else value)
        except (TypeError, ValueError):
            raise ValueError(f"{f.key} takes a number, like 0.2; got {value!r}") from None
        if math.isnan(number):
            raise ValueError(f"{f.key} takes a number, like 0.2; got NaN")
        _check_range(f, number)
        return number
    if f.kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{f.key} is true or false; got {value!r}")
    if f.kind == "choice":
        if value is True and len(f.choices) == 1:
            return f.choices[0]  # query_decomposition=True
        text = str(value).strip()
        for choice in f.choices:
            if text.lower() == choice.lower():
                return choice
        raise ValueError(f"{f.key} is {' or '.join(map(repr, f.choices))}; got {value!r}")
    if f.kind == "list":
        if isinstance(value, str):
            text = value.strip()
            items = _loads(text, f.key) if text.startswith("[") else [line for line in text.splitlines() if line]
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
        else:
            raise ValueError(f"{f.key} takes a list, like ['END'], or text with one item per line; got {value!r}")
        if not isinstance(items, list) or not all(isinstance(x, (str, int, float)) and str(x) for x in items):
            raise ValueError(f"{f.key} takes a list of text, with no empty items; got {value!r}")
        if f.high is not None and len(items) > f.high:
            raise ValueError(f"{f.key} takes up to {_number(f.high)} items; got {len(items)}")
        return [str(x) for x in items]
    if f.kind == "json":
        if f.key == "filter":
            return as_filter(value)
        data = _loads(value, f.key) if isinstance(value, str) else _plain_json(value)
        expected = {"object": dict, "list": list}.get(f.container)
        if expected is not None and not isinstance(data, expected):
            example = '{"top_k": 50}' if expected is dict else "[...]"
            raise ValueError(f"{f.key} takes a JSON {f.container}, like {example}; got {_short(data)}")
        return data
    text = value if isinstance(value, str) else str(value)
    text = text if f.kind == "long_text" else text.strip()
    if f.low and len(text) < f.low:
        raise ValueError(f"{f.key} can't be empty")
    if f.high is not None and len(text) > f.high:
        raise ValueError(f"{f.key} takes up to {_number(f.high)} characters; this has {len(text):,}")
    if f.key == "prompt" and "$search_results$" not in text:
        raise ValueError(
            "The prompt needs $search_results$, which is where Bedrock puts the passages it found "
            "(DEFAULT_PROMPT shows one)"
        )
    return text


def normalize_settings(settings: dict[str, Any] | None, schema: Schema) -> dict[str, Any]:
    """
    {name or path: value} -> {key: value}, each value checked and converted by coerce_setting(), in the order the
    chat shows them. A value of None leaves the setting out. A ValueError lists every problem at once.
    """
    out: dict[str, Any] = {}
    problems: list[str] = []
    for name, value in (settings or {}).items():
        try:
            f = schema.find(name)
            if value is not None:
                out[f.key] = coerce_setting(f, value)
        except ValueError as exc:
            problems.append(str(exc).rstrip("."))
    if problems:
        raise ValueError(". ".join(problems) + ".")
    return {key: out[key] for key in schema.fields if key in out}


def _put(tree: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for name in path[:-1]:
        tree = tree.setdefault(name, {})
    tree[path[-1]] = value


def _get(tree: Any, path: tuple[str, ...]) -> Any:
    for name in path:
        if not isinstance(tree, dict) or name not in tree:
            return None
        tree = tree[name]
    return tree


def build_request(
    question: str,
    kb_id: str,
    model_arn: str,
    settings: dict[str, Any],
    schema: Schema,
    *,
    session_id: str | None = None,
    region: str = "",
    data_sources: Iterable[str] = (),
    files: Iterable[str] = (),
) -> dict[str, Any]:
    """
    The RetrieveAndGenerate request for a question: the knowledge base, the model, and each setting at its place
    in the JSON. settings is {key: value} as normalize_settings() returns it. data_sources (IDs) and files (s3://
    paths) are the only ones to search, added to the filter. Required fields that can only have one value
    (rerankingConfiguration.type) are filled in. No AWS call.
    """
    params: dict[str, Any] = {"input": {"text": question}}
    if session_id:
        params["sessionId"] = session_id
    params["retrieveAndGenerateConfiguration"] = {
        "type": "KNOWLEDGE_BASE",
        "knowledgeBaseConfiguration": {"knowledgeBaseId": kb_id, "modelArn": model_arn},
    }
    for key, f in schema.fields.items():
        if settings.get(key) is None:
            continue
        value = rerank_arn(settings[key], region) if key == "reranker" else copy.deepcopy(settings[key])
        _put(params, f.path, value)
    only = with_data_sources(with_files(_get(params, _FILTER_PATH), files), data_sources)
    if only is not None:
        _put(params, _FILTER_PATH, only)
    for path, value in schema.auto:
        parent = _get(params, path[:-1])
        if isinstance(parent, dict) and path[-1] not in parent:
            parent[path[-1]] = value
    return params


# -------------------------------------------- retrieve only: the search without the answer

_RETRIEVAL = (*_KB_CONFIG, "retrievalConfiguration")  # the part of the request a Retrieve request takes as it is


def _is_retrieve(params: Any) -> bool:
    """
    Whether a request is Retrieve's (retrievalQuery and knowledgeBaseId at its root), not RetrieveAndGenerate's.
    """
    return isinstance(params, dict) and "retrieveAndGenerateConfiguration" not in params and (
        "retrievalQuery" in params or "knowledgeBaseId" in params)


def _retrieve_path(path: tuple[str, ...]) -> tuple[str, ...] | None:
    """
    Where a RetrieveAndGenerate path goes in a Retrieve request; None when Retrieve has no place for it.
    """
    return path[len(_KB_CONFIG):] if path[: len(_RETRIEVAL)] == _RETRIEVAL else None


def retrieve_settings(settings: dict[str, Any], schema: Schema) -> dict[str, Any]:
    """
    The settings a retrieve-only search sends: the Retrieval ones (passages, search type, filter, reranker...).
    The rest are for the model's answer, which a search doesn't ask for.
    """
    return {key: value for key, value in settings.items()
            if key in schema.fields and _retrieve_path(schema.fields[key].path) is not None}


def build_retrieve_request(
    question: str,
    kb_id: str,
    settings: dict[str, Any],
    schema: Schema,
    *,
    region: str = "",
    data_sources: Iterable[str] = (),
    files: Iterable[str] = (),
) -> dict[str, Any]:
    """
    The Retrieve request for a question: the same search build_request()'s RetrieveAndGenerate request makes
    (passages, search type, filter, reranker, data sources and files) without the model, so what comes back is every
    passage found instead of an answer. Settings for the answer (temperature, prompt...) are left out. No AWS call.
    """
    full = build_request(question, kb_id, "", retrieve_settings(settings, schema), schema, region=region,
                         data_sources=data_sources, files=files)
    params: dict[str, Any] = {"knowledgeBaseId": kb_id, "retrievalQuery": {"text": question}}
    retrieval = _get(full, _RETRIEVAL)
    if retrieval:
        params["retrievalConfiguration"] = retrieval
    return params


def _retrieve_as_rag(params: dict[str, Any]) -> dict[str, Any]:
    """
    A Retrieve request laid out like RetrieveAndGenerate's, so settings_from_request() reads both. A field
    RetrieveAndGenerate has no place for stays at the root, where it's named as one the chat can't send.
    """
    out: dict[str, Any] = {}
    kb: dict[str, Any] = {}
    for name, value in params.items():
        if name == "retrievalQuery":
            out["input"] = value
        elif name in ("knowledgeBaseId", "retrievalConfiguration"):
            kb[name] = value
        else:
            out[name] = value
    out["retrieveAndGenerateConfiguration"] = {"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": kb}
    return out


def settings_from_request(params: dict[str, Any], schema: Schema) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    A RetrieveAndGenerate or Retrieve request (one you edited, say) -> (picked, settings): picked holds the
    'question', 'knowledgeBaseId', 'modelArn', 'sessionId', 'dataSources' (IDs) and 'files' (s3:// paths) it names,
    and 'retrieve_only': True for a Retrieve request; settings is {key: value} like normalize_settings()'s. The
    opposite of build_request() and build_retrieve_request(). A ValueError names anything the chat can't send.
    """
    if not isinstance(params, dict):
        raise ValueError("The request is a JSON object: {\"input\": ..., \"retrieveAndGenerateConfiguration\": ...}")
    retrieve_only = _is_retrieve(params)
    if retrieve_only:
        params = _retrieve_as_rag(params)
    by_path = {f.path: f for f in schema.fields.values()}
    fixed = {("input", "text"): "question", ("sessionId",): "sessionId",
             (*_KB_CONFIG, "knowledgeBaseId"): "knowledgeBaseId", (*_KB_CONFIG, "modelArn"): "modelArn"}
    auto = dict(schema.auto)
    picked: dict[str, Any] = {}
    values: dict[str, Any] = {}
    problems: list[str] = []

    def walk(node: dict[str, Any], path: tuple[str, ...]) -> None:
        for name, value in node.items():
            here = (*path, name)
            if here in fixed:
                picked[fixed[here]] = value
            elif here == ("retrieveAndGenerateConfiguration", "type"):
                if value != "KNOWLEDGE_BASE":
                    problems.append("this chat asks knowledge bases, so retrieveAndGenerateConfiguration.type is "
                                    "KNOWLEDGE_BASE")
            elif here in auto:
                continue
            elif here == _FILTER_PATH and (split_data_sources(value)[0] or split_condition(value, SOURCE_URI_KEY)[0]):
                ids, rest = split_data_sources(value)
                uris, rest = split_condition(rest, SOURCE_URI_KEY)
                picked.update({"dataSources": ids} if ids else {})
                picked.update({"files": uris} if uris else {})
                if rest is not None:
                    try:
                        values[by_path[here].key] = coerce_setting(by_path[here], rest)
                    except (KeyError, ValueError) as exc:
                        problems.append(str(exc).rstrip("."))
            elif here in by_path:
                try:
                    values[by_path[here].key] = coerce_setting(by_path[here], value)
                except ValueError as exc:
                    problems.append(str(exc).rstrip("."))
            elif isinstance(value, dict):
                walk(value, here)
            else:
                problems.append(f"the chat doesn't send {'.'.join(here)}")

    walk(params, ())
    if problems:
        text = "; ".join(problems)
        raise ValueError(text[:1].upper() + text[1:] + ".")
    if retrieve_only:
        picked["retrieve_only"] = True
    return picked, {key: values[key] for key in schema.fields if key in values}


def validate_request(params: dict[str, Any], schema: Schema) -> list[str]:
    """
    What's wrong with a request (RetrieveAndGenerate's, or Retrieve's) according to the service model (botocore's
    own checks, the ones it runs before sending): unknown fields, wrong types, numbers out of range, missing required
    fields. [] when it's fine.
    """
    shape = schema.retrieve_shape if _is_retrieve(params) else schema.input_shape
    if shape is None:
        return []
    report = ParamValidator().validate(params, shape)
    if not report.has_errors():
        return []
    prefix = ".".join(_KB_CONFIG) + "."
    lines = report.generate_report().splitlines()
    return [" ".join(line.replace(prefix, "").split()) for line in lines if line.strip()]


def describe_setting(f: Field, value: Any) -> str:
    """
    What a setting's value means, in a sentence: n=8 -> 'Retrieves the 8 passages that match best.'
    """
    if value is None:
        return "Not sent until you fill it in."
    key = f.key
    if key == "n":
        return f"Retrieves the {_plural(value, 'passage')} that match best."
    if key == "search_type":
        return {"HYBRID": "Matches meaning and exact words.", "SEMANTIC": "Matches meaning only."}.get(value, "")
    if key == "filter":
        return f"Only documents where {describe_filter(value)}."
    if key == "reranker":
        model = _RERANK_ALIASES.get(str(value), str(value))
        name = {DEFAULT_RERANK_MODEL: "Cohere Rerank 3.5", "amazon.rerank-v1:0": "Amazon Rerank 1.0"}.get(model, model)
        return f"Re-orders the passages with {name} before the model sees them."
    if key == "rerank_n":
        return f"The model sees the best {_plural(value, 'passage')} after reranking."
    if key == "temperature":
        feel = "steady, factual wording" if value <= 0.3 else "varied wording" if value >= 0.7 else "some variety"
        return f"{_number(value)}: {feel}."
    if key == "top_p":
        return f"Picks words from the likeliest ones that add up to {value:.0%}."
    if key == "max_tokens":
        return f"Answers stop at {value:,} tokens (about {value * 3 // 4:,} words)."
    if key == "stop":
        return "The answer ends at " + " or ".join(map(repr, value)) + "."
    if key in ("prompt", "orchestration_prompt"):
        return f"Your own prompt ({len(value):,} characters)."
    if key == "model_fields" and isinstance(value, dict):
        return "Passed to the model as they are: " + ", ".join(f"{k}={_short(v, 30)}" for k, v in value.items()) + "."
    if key == "query_decomposition":
        return "Splits a complicated question into simpler searches."
    if key == "latency":
        return {"optimized": "Latency-optimized inference, where offered.", "standard": "Standard inference."}.get(
            value, "")
    return _sentences(f.doc, 1)
