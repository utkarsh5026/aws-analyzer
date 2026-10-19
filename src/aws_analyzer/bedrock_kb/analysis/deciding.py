"""
What helps decide what to change: documents by status, files changed since a sync, two
searches side by side, retrieval metrics, and evaluate()'s test questions.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from ..._bedrock.models import KBDocument, Passage
from ..._bedrock.words import best_snippet
from ..._kit.fmt import parse_time
from ..._kit.text import _clip

from ..models import _EPOCH, DocumentSummary, EvalCase, FileChange, Retrieval, SearchComparison


# ---------------------------------------------------- deciding what to change


def summarize_documents(
    docs: Iterable[KBDocument], *, truncated: bool = False
) -> DocumentSummary:
    """
    Counts by status and the most common failure reasons.
    """
    docs = list(docs)
    counts = Counter(d.status for d in docs)
    reasons = Counter(
        _clip(d.reason.strip(), 200)
        for d in docs
        if d.reason and d.status not in ("INDEXED",)
    )
    return DocumentSummary(
        total=len(docs),
        counts=dict(counts.most_common()),
        reasons=reasons.most_common(10),
        truncated=truncated,
    )


def changed_since(
    objects: Iterable[dict[str, Any] | FileChange], when: datetime | str | None
) -> list[FileChange]:
    """
    Files modified after `when` (all of them when None: never synced), newest first. Takes ListObjectsV2
    'Contents' entries or FileChanges. Metadata files (<file>.metadata.json) and folder markers are left out.
    """
    since = parse_time(when)
    changed = []
    for obj in objects:
        change = (
            obj
            if isinstance(obj, FileChange)
            else FileChange(
                obj["Key"], obj.get("LastModified"), int(obj.get("Size") or 0)
            )
        )
        if change.key.endswith((".metadata.json", "/")):
            continue
        if since is None or (change.modified is not None and change.modified > since):
            changed.append(change)
    return sorted(changed, key=lambda c: c.modified or _EPOCH, reverse=True)


def _pairs(labels: list[str]) -> list[tuple[str, str]]:
    return [(a, b) for i, a in enumerate(labels) for b in labels[i + 1 :]]


def compare_retrievals(runs: dict[str, Retrieval]) -> SearchComparison:
    """
    How much searches agree: for each pair of runs the share of their passages both found (shared / all, by chunk
    ID, or by source and text), and for each run the passages no other run found.
    """
    keys = {label: {p.key for p in r.passages} for label, r in runs.items()}
    overlap = {}
    for a, b in _pairs(list(runs)):
        either = keys[a] | keys[b]
        overlap[(a, b)] = len(keys[a] & keys[b]) / len(either) if either else 1.0
    unique = {
        label: [
            p
            for p in r.passages
            if not any(p.key in keys[other] for other in runs if other != label)
        ]
        for label, r in runs.items()
    }
    first = next(iter(runs.values()), None)
    return SearchComparison(
        question=first.question if first else "",
        kb_id=first.kb_id if first else "",
        kb_name=first.kb_name if first else "",
        runs=dict(runs),
        overlap=overlap,
        unique=unique,
    )


def _snippet_of(passage: Passage, width: int = 60) -> str:
    return '"' + best_snippet(passage.text, [], width) + '"'


def _setting(label: str) -> tuple[str, str]:
    """
    'HYBRID n=10' -> ('HYBRID', '10').
    """
    kind, _, n = label.rpartition(" n=")
    return kind, n


def retrieval_metrics(cases: Iterable[EvalCase], k: int) -> tuple[float, float]:
    """
    (hit rate, mean reciprocal rank) at k: the share of questions whose expected source was in the top k, and the
    average of 1/rank, counting a miss (or a rank past k) as 0.
    """
    ranks = [c.rank if c.rank is not None and c.rank <= k else None for c in cases]
    if not ranks:
        return 0.0, 0.0
    return (
        sum(r is not None for r in ranks) / len(ranks),
        sum(1 / r for r in ranks if r) / len(ranks),
    )


def _eval_pairs(cases: Any) -> list[tuple[str, Any]]:
    """
    evaluate()'s cases -> [(question, expected)].
    """
    rows = (
        cases.to_dict("records")
        if hasattr(cases, "to_dict") and hasattr(cases, "columns")
        else list(cases or [])
    )
    pairs = []
    for row in rows:
        if isinstance(row, dict):
            question, expected = (
                row.get("question"),
                row.get("expected", row.get("source")),
            )
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            question, expected = row
        else:
            raise ValueError(
                "cases holds (question, expected source) pairs, dicts with 'question' and 'expected', or a "
                f"DataFrame with those columns; got {row!r}"
            )
        if not str(question or "").strip() or expected is None or expected == "":
            raise ValueError(
                f"Each case needs a question and an expected source (part of its file name, URI or text); "
                f"got {row!r}"
            )
        pairs.append((str(question), expected))
    if not pairs:
        raise ValueError(
            "No test questions: pass [(question, expected source), ...], e.g. "
            "[('refund window?', 'refund-policy.pdf')]"
        )
    return pairs
