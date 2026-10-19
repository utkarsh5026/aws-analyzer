"""
Parsing and matching: knowledge base IDs and ARNs, a source's file name, Bedrock's own metadata keys,
the names of knowledge bases and data sources, and the errors that mean a session expired.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from botocore.exceptions import ClientError


_KB_ID_RE = re.compile(r"^[0-9A-Z]{10}$")

_KB_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:bedrock:[\w-]+:\d{12}:knowledge-base/([0-9A-Za-z]{10})$"
)


def parse_kb_ref(ref: str) -> tuple[str, str]:
    """
    How a knowledge base was named: 'ABCDE12345' -> ('id', 'ABCDE12345'), 'support-docs' -> ('name',
    'support-docs'), and an ARN (arn:aws:bedrock:<region>:<account>:knowledge-base/ABCDE12345) -> ('arn', 'ABCDE12345'),
    the ID inside it.
    """
    text = str(ref or "").strip()
    if not text:
        raise ValueError(
            "Pass a knowledge base: its name, its 10-character ID or its ARN (kbs() lists them)"
        )
    match = _KB_ARN_RE.match(text)
    if match:
        return "arn", match.group(1)
    if text.lower().startswith("arn:"):
        raise ValueError(
            f"{text!r} isn't a knowledge base ARN; those look like "
            "arn:aws:bedrock:<region>:<account>:knowledge-base/<ID>"
        )
    return ("id", text) if _KB_ID_RE.match(text) else ("name", text)


def _model_id(arn: str | None) -> str:
    """
    'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0' -> 'amazon.titan-embed-text-v2:0'.
    """
    return (arn or "").rsplit("/", 1)[-1]


def source_name(uri: str | None) -> str:
    """
    The file name in a source location: 's3://docs/policies/refund-policy.pdf' -> 'refund-policy.pdf',
    'https://example.com/help/refunds?x=1' -> 'refunds', 'https://example.com/' -> 'example.com' (a web address
    that's just a host is named by its host).
    """
    if not uri:
        return ""
    text = str(uri).split("?", 1)[0].split("#", 1)[0]
    scheme, _, rest = text.partition("://")
    if not rest:
        rest, scheme = scheme, ""
    parts = [p for p in rest.split("/") if p]
    if not parts:
        return str(uri)
    if len(parts) == 1 and scheme not in ("s3", ""):
        return parts[0]
    return unquote(parts[-1])


SEARCHABLE = {"INDEXED", "PARTIALLY_INDEXED", "METADATA_PARTIALLY_INDEXED", "METADATA_UPDATE_FAILED"}


_BEDROCK_META_PREFIX = "x-amz-bedrock-kb-"


def split_metadata(md: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    A retrieved passage's metadata -> (system, user). System keys lose their prefix: 'source-uri', 'chunk-id',
    'data-source-id', 'document-page-number'. User keys are the knowledge base author's own metadata (from
    <file>.metadata.json), which is what where= filters on.
    """
    system: dict[str, Any] = {}
    user: dict[str, Any] = {}
    for key, value in (md or {}).items():
        if key.startswith(_BEDROCK_META_PREFIX):
            system[key[len(_BEDROCK_META_PREFIX) :]] = value
        elif key.startswith("AMAZON_BEDROCK_"):
            system[key] = value
        else:
            user[key] = value
    return system, user


_REFUSAL = "unable to assist you with this request"


def _match_kb(names: dict[str, str], kind: str, value: str) -> str | None:
    """
    The ID in {ID: name} that `value` names (an ID, or a name in any case); None if nothing matches.
    """
    if kind == "id" and value in names:
        return value
    hits = [kb_id for kb_id, name in names.items() if name.lower() == value.lower()]
    if len(hits) > 1:
        raise ValueError(
            f"{len(hits)} knowledge bases are named {value!r}; pass one of their IDs: {', '.join(hits)}"
        )
    return hits[0] if hits else None


def _match_sources(
    names: dict[str, str], wanted: list[str]
) -> tuple[dict[str, str], list[str]]:
    """
    ({ID: name} of the data sources `wanted` names, by ID or by name in any case, [what matched none]).
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    for w in wanted:
        hits = [w] if w in names else [i for i, n in names.items() if n.lower() == w.lower()]
        if not hits:
            missing.append(w)
        for ds_id in hits:
            found[ds_id] = names[ds_id]
    return found, missing


def _session_expired(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return error.get("Code") in (
        "ValidationException",
        "ResourceNotFoundException",
        "BadRequestException",
    ) and ("session" in str(error.get("Message", "")).lower())
