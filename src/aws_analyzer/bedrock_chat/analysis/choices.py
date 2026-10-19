"""
What the pickers list: files by name or path, knowledge bases by search, and the reranker's
ARN.
"""

from __future__ import annotations

import difflib
from collections import Counter
from typing import Any, Iterable

from ..._bedrock.helpers import parse_kb_ref, source_name
from ..._bedrock.models import KBDocument
from ..._bedrock.prices import DEFAULT_RERANK_MODEL, _RERANK_ALIASES
from ..._kit.text import search_rank

from ..models import KnowledgeBase


def file_path(uri: str) -> str:
    """
    's3://support-docs-bucket/policies/refund-policy.pdf' -> 'policies/refund-policy.pdf' (the path in its bucket).
    """
    rest = uri.split("://", 1)[1] if "://" in uri else uri
    return rest.split("/", 1)[1] if "/" in rest else rest


def file_labels(uris: Iterable[str]) -> dict[str, str]:
    """
    {s3:// path: how to show it}: its path in the bucket, or bucket and path when two buckets hold the same one.
    """
    uris = list(dict.fromkeys(uris))
    counts = Counter(file_path(u) for u in uris)
    return {u: file_path(u) if counts[file_path(u)] == 1 else u.split("://", 1)[-1] for u in uris}


def match_files(documents: Iterable[KBDocument], wanted: Iterable[str]) -> tuple[list[str], list[str]]:
    """
    Files named by s3:// path, path in the bucket ('policies/refund-policy.pdf') or name ('refund-policy.pdf', any
    case) -> ([their s3:// paths], [what's wrong with the ones that name no file, or several]). An s3:// path is
    taken as it is, listed or not.
    """
    docs = [d for d in documents if d.uri]
    labels = file_labels(d.uri for d in docs)
    found: list[str] = []
    problems: list[str] = []
    for w in wanted:
        if "://" in w:
            found.append(w)
            continue
        low = w.lower().lstrip("/")
        hits = list(dict.fromkeys(
            d.uri for d in docs if labels[d.uri].lower() == low or d.uri.lower().endswith("/" + low)))
        if len(hits) == 1:
            found.append(hits[0])
        elif hits:
            shown = ", ".join(repr(labels[u]) for u in hits[:4]) + (", …" if len(hits) > 4 else "")
            problems.append(f"{w!r} names {len(hits)} files ({shown}): pass more of its path, like "
                            f"{labels[hits[0]]!r}")
        else:
            names: dict[str, list[str]] = {}
            for d in docs:
                names.setdefault(source_name(d.uri).lower(), []).append(labels[d.uri])
            close = difflib.get_close_matches(source_name(low), list(names), n=2, cutoff=0.6)
            guesses = [label for c in close for label in names[c]][:3]
            hint = f" Did you mean {' or '.join(map(repr, guesses))}?" if guesses else ""
            problems.append(f"No file {w!r} in the knowledge base.{hint}")
    return list(dict.fromkeys(found)), problems


def describe_files(uris: list[str]) -> str:
    """
    The files searched, in words: 'every file', "file 'refund-policy.pdf'", '3 files'.
    """
    if not uris:
        return "every file"
    if len(uris) == 1:
        return f"file {source_name(uris[0])!r}"
    names = [repr(source_name(u)) for u in uris]
    return f"files {', '.join(names[:-1])} and {names[-1]}" if len(uris) <= 3 else f"{len(uris)} files"


def kb_search_text(text: str) -> str:
    """
    What to search knowledge bases for: a knowledge base ARN becomes the ID inside it, anything else stays.
    """
    try:
        kind, value = parse_kb_ref(text)
    except ValueError:
        return str(text or "").strip()
    return value if kind == "arn" else str(text).strip()


def match_kbs(kbs: Iterable[KnowledgeBase], text: str) -> list[KnowledgeBase]:
    """
    The knowledge bases a search finds by name, ID (or part of either), ARN, description or status, best first:
    an exact name or ID, then one that starts with the text, then the rest in the order given.
    """
    wanted = kb_search_text(text)
    ranked = []
    for i, kb in enumerate(kbs):
        rank = search_rank(wanted, (kb.name, kb.id, kb.description, kb.status))
        if rank is not None:
            ranked.append((rank, i, kb))
    return [kb for _, _, kb in sorted(ranked, key=lambda r: r[:2])]


def rerank_arn(model: Any, region: str) -> str:
    """
    'cohere' (or True), 'amazon', a reranking model ID or its ARN -> the ARN Bedrock wants.
    """
    model_id = DEFAULT_RERANK_MODEL if model is True else _RERANK_ALIASES.get(str(model).lower(), str(model))
    return model_id if model_id.startswith("arn:") else f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
