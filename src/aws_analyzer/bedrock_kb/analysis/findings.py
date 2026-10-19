"""
Findings: what's wrong with a knowledge base, its syncs, a search, an answer, a comparison or
an evaluation, why it matters, and what to do.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import _REFUSAL, source_name
from ..._bedrock.models import Passage
from ..._bedrock.prices import BEDROCK_PRICES
from ..._kit.errors import _why
from ..._kit.fmt import _plural, human_age, human_money
from ..._kit.render import _call
from ..._kit.text import _clip

from ..helpers import _code_terms, _isnt, human_duration, sync_command
from ..models import (
    _EPOCH, Answer, DataSourceInfo, DocumentSummary, EvalReport, FileInventory, IngestionJob, KnowledgeBaseInfo,
    Retrieval, SearchComparison, SyncFreshness,
)
from .common import _METADATA_EXAMPLE, _fmt_day, _reasons_text
from .filters import _outside, _source_arg, describe_filter
from .deciding import _pairs, _setting, _snippet_of
from .cost import vector_store_monthly_cost


# ------------------------- findings: what's wrong, why it matters, what to do

# describe() section -> (what it is, the permission that reads it)
_SECTIONS = {
    "describe": ("the knowledge base", "bedrock:GetKnowledgeBase"),
    "data_sources": ("its data sources", "bedrock:ListDataSources"),
    "data_source": ("a data source's settings", "bedrock:GetDataSource"),
    "ingestion": ("sync history", "bedrock:ListIngestionJobs"),
    "documents": ("document status", "bedrock:ListKnowledgeBaseDocuments"),
    "tags": ("tags", "bedrock:ListTagsForResource"),
}


def _source_label(ds: DataSourceInfo) -> str:
    return f"data source {ds.name!r}" if ds.name else f"data source {ds.id}"


def kb_findings(
    info: KnowledgeBaseInfo,
    docs: DocumentSummary | None = None,
    freshness: list[SyncFreshness] | None = None,
    prices: dict[str, float] | None = None,
    files: FileInventory | None = None,
) -> list[tuple[str, str]]:
    """
    What's wrong with a knowledge base and what to do about it -> [(level, message)]. `docs` (from documents())
    and `freshness` (from unsynced()) add their findings when given. `files` (from file_inventory()) says the files
    were listed, so a sync's failed documents aren't reported again here: inventory_findings names them.
    """
    prices = BEDROCK_PRICES if prices is None else prices
    listed = {ds_id for ds_id, kind in (files.kinds if files else {}).items()
              if kind in ("S3", "CUSTOM") and "documents" not in files.errors.get(ds_id, {})}
    found: list[tuple[str, str]] = []
    region = info.region
    if info.status == "FAILED" or info.status.endswith("_UNSUCCESSFUL"):
        found.append(
            (
                "warn",
                f"The knowledge base is {info.status}: {_reasons_text(info.failure_reasons)}. Searches "
                "and answers can fail until it's fixed. Check that its service role "
                f"({info.role_arn.rsplit('/', 1)[-1] or '?'}) can read the data sources and the vector "
                "store, then fix the settings in the Bedrock console.",
            )
        )
    elif info.status and info.status != "ACTIVE":
        found.append(
            (
                "info",
                f"The knowledge base is {info.status}; wait until it's ACTIVE before searching it.",
            )
        )
    failed_docs_named = False
    for ds in info.data_sources:
        label = _source_label(ds)
        command = sync_command(info.id, ds.id, region)
        if ds.status == "FAILED" or ds.status.endswith("_UNSUCCESSFUL"):
            found.append(
                (
                    "warn",
                    f"The {label} is {ds.status}: {_reasons_text(ds.failure_reasons)}. Fix its settings "
                    "in the Bedrock console, then sync it again.",
                )
            )
        job = ds.last_sync
        if "ingestion" in ds.errors:
            pass
        elif job is None:
            found.append(
                (
                    "warn",
                    f"The {label} has never been synced, so nothing from it is searchable until you "
                    f"sync: {command}",
                )
            )
        elif job.status == "FAILED":
            found.append(
                (
                    "warn",
                    f"The last sync of the {label} failed {human_age(job.started)} "
                    f"({_reasons_text(job.failure_reasons)}). Searches use what earlier syncs indexed; "
                    f"syncs() shows the history. Once the cause is fixed, sync again: {command}",
                )
            )
        elif job.running:
            found.append(
                (
                    "info",
                    f"The {label} is syncing now (started {human_age(job.started)}): results can "
                    "change until it finishes. syncs() shows its progress.",
                )
            )
        elif job.failed and ds.id in listed:
            failed_docs_named = True
        elif job.failed:
            failed_docs_named = True
            found.append(
                (
                    "warn",
                    f"The last sync of the {label} finished with "
                    f"{_plural(job.failed, 'document')} that failed to index, so "
                    f"{'it is' if job.failed == 1 else 'they are'} not searchable: "
                    "documents(status='FAILED') shows which and why.",
                )
            )
        if (
            ds.source_type == "S3"
            and job is not None
            and job.status == "COMPLETE"
            and job.scanned
            and not job.metadata_scanned
        ):
            found.append(
                (
                    "info",
                    f"The last sync of the {label} found no metadata files, so where= filters match "
                    "nothing from it. Filtering needs a `<file>.metadata.json` next to each file, e.g. "
                    f"refund-policy.pdf.metadata.json holding {_METADATA_EXAMPLE}; sync after adding "
                    "them.",
                )
            )
        strategy = ds.chunking.get("chunkingStrategy")
        if strategy == "NONE":
            found.append(
                (
                    "warn",
                    f"The {label} doesn't chunk: each file is one chunk, so a long file becomes one "
                    "vector and text past the embedding model's input limit may not be searchable. "
                    "Unless your files are already short passages, create a data source with fixed-size, "
                    "semantic or hierarchical chunking (chunking can't be changed later) and sync it.",
                )
            )
        elif strategy == "FIXED_SIZE" and not (
            ds.chunking.get("fixedSizeChunkingConfiguration") or {}
        ).get("overlapPercentage"):
            found.append(
                (
                    "info",
                    f"The {label} cuts fixed-size chunks with 0% overlap, so a sentence cut at a chunk "
                    "boundary is split in two and may match neither half well. 10-20% overlap is "
                    "usual; chunking is set when a data source is created.",
                )
            )
        if ds.deletion_policy == "RETAIN":
            found.append(
                (
                    "info",
                    f"The {label} keeps its data when deleted (deletion policy RETAIN): if you delete "
                    "this data source its chunks stay in the vector store and keep appearing in "
                    "answers. Set its data deletion policy to Delete (Bedrock console, the data "
                    "source's settings) before deleting it.",
                )
            )
    found += freshness_findings(freshness or [], info.id, region)
    if docs is not None and docs.counts.get("FAILED") and not failed_docs_named:
        top = (
            f" The most common reason: {docs.reasons[0][0].rstrip('.')}."
            if docs.reasons
            else ""
        )
        found.append(
            (
                "warn",
                f"{_plural(docs.counts['FAILED'], 'document')} failed to index and "
                f"{_isnt(docs.counts['FAILED'])} searchable.{top} documents(status='FAILED') lists them.",
            )
        )
    cost = vector_store_monthly_cost(info, prices)
    if cost is not None:
        found.append(
            (
                "info",
                f"The vector store is OpenSearch Serverless, which costs about {human_money(cost)}/month "
                f"even when idle ({prices['opensearch_min_ocus']:g} OCUs minimum at "
                f"${prices['opensearch_ocu_hour']:g}/hour). Knowledge bases whose collections share a "
                "KMS key share those OCUs, and deleting a knowledge base doesn't delete its collection: "
                "remove unused collections in the OpenSearch Service console.",
            )
        )
    if info.errors:
        parts = [
            f"{_SECTIONS.get(k, (k, ''))[0]} ({_why(v, _SECTIONS[k][1]) if k in _SECTIONS else v})"
            for k, v in info.errors.items()
        ]
        found.append(("info", "Couldn't read " + ", ".join(parts) + "."))
    return found


def freshness_findings(
    freshness: list[SyncFreshness], kb_id: str, region: str = ""
) -> list[tuple[str, str]]:
    """
    What unsynced() found, with the command that syncs each stale data source -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    for fresh in freshness:
        ds = fresh.data_source
        label = _source_label(ds)
        command = sync_command(kb_id, ds.id, region)
        more = "+" if fresh.truncated else ""
        if fresh.note:
            found.append(("info", f"The {label} wasn't checked: {fresh.note}."))
        elif fresh.last_sync is None:
            found.append(
                (
                    "warn",
                    f"The {label} has never finished a sync, so none of its {fresh.files:,}{more} files "
                    f"are searchable yet: {command}",
                )
            )
        elif fresh.changed:
            found.append(
                (
                    "warn",
                    f"{_plural(len(fresh.changed), 'file')}{more} in {ds.location} changed since the last "
                    f"sync on {_fmt_day(fresh.last_sync.started)}: searches and answers don't see those "
                    f"changes until you sync: {command}",
                )
            )
        elif fresh.metadata_changed:
            found.append(
                (
                    "warn",
                    f"{_plural(fresh.metadata_changed, 'metadata file')} in {ds.location} changed since "
                    "the last sync, so where= filters still use the old values until you sync: "
                    f"{command}",
                )
            )
    return found


def sync_findings(
    jobs: list[IngestionJob], names: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    """
    Patterns in a sync history (newest first, one or more data sources) -> [(level, message)]: syncs that keep
    failing (with their reasons grouped), documents that keep failing, and syncs that seem stuck.
    """
    names = names or {}
    found: list[tuple[str, str]] = []
    by_source: dict[str, list[IngestionJob]] = {}
    for job in sorted(jobs, key=lambda j: j.started or _EPOCH, reverse=True):
        by_source.setdefault(job.data_source_id, []).append(job)
    for ds_id, history in by_source.items():
        label = (
            f"data source {names[ds_id]!r}"
            if names.get(ds_id)
            else f"data source {ds_id}"
        )
        latest = history[0]
        failures = 0
        for job in history:
            if job.status != "FAILED":
                break
            failures += 1
        if failures:
            reasons = Counter(
                _clip(" ".join(r.split()).rstrip("."), 160)
                for job in history[:failures]
                for r in job.failure_reasons
            )
            grouped = "; ".join(
                f"{reason} ({count}x)" if count > 1 else reason
                for reason, count in reasons.most_common(3)
            )
            what = "The last sync" if failures == 1 else f"The last {failures} syncs"
            found.append(
                (
                    "warn",
                    f"{what} of the {label} failed ({grouped or 'no reason given'}). "
                    + (
                        "Syncing again won't help until the cause is fixed: usually the knowledge base's "
                        "role can't read the source or write to the vector store."
                        if failures > 1
                        else "Fix the cause, then sync again."
                    ),
                )
            )
        finished = [job for job in history if job.status == "COMPLETE"]
        if finished and finished[0].failed:
            streak = 0
            for job in finished:
                if not job.failed:
                    break
                streak += 1
            again = (
                f" Each of the last {streak} syncs had failed documents, so this isn't a one-off."
                if streak > 1
                else ""
            )
            found.append(
                (
                    "warn",
                    f"The last finished sync of the {label} couldn't index "
                    f"{_plural(finished[0].failed, 'document')}: documents(status='FAILED') shows which "
                    f"and why.{again}",
                )
            )
        if latest.running and latest.duration and latest.duration > timedelta(hours=12):
            found.append(
                (
                    "info",
                    f"A sync of the {label} has been running for {human_duration(latest.duration)}. "
                    "Large sources can take hours; if it doesn't move, check it in the Bedrock console.",
                )
            )
    return found


def _search_label(search_type: str | None) -> str:
    return {
        "SEMANTIC": "semantic search",
        "HYBRID": "hybrid search (meaning and keywords)",
    }.get((search_type or "").upper(), "Bedrock's default search")


def retrieval_findings(r: Retrieval) -> list[tuple[str, str]]:
    """
    What a search result says about the knowledge base, with what to try next -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    passages = r.passages
    if r.guardrail_action == "INTERVENED":
        found.append(
            (
                "warn",
                "A guardrail intervened in this search, so passages may be missing or masked. The "
                "guardrail's settings in the Bedrock console say what it blocks.",
            )
        )
    if not passages:
        if r.where is not None:
            found.append(
                (
                    "warn",
                    f"Nothing came back. The filter ({describe_filter(r.where)}) may match no documents: "
                    "metadata values are exact and typed (2024 and '2024' differ), and filtering needs a "
                    "<file>.metadata.json next to each file. Try without where=, then check kb_info().",
                )
            )
        elif r.data_sources:
            only = _source_arg(r.data_sources)
            check = _call("syncs", data_source=only) if isinstance(only, str) else "syncs()"
            found.append(
                (
                    "warn",
                    f"Nothing came back from {describe_sources(r.data_sources)}. Check that it's synced "
                    f"({check}) and holds searchable documents, or search without data_source= to cover "
                    "every data source.",
                )
            )
        else:
            found.append(
                (
                    "warn",
                    "Nothing came back. Check that the data sources are synced (syncs()) and hold "
                    "searchable documents (documents()), or try a larger n=.",
                )
            )
        return found
    outside = _outside(passages, r.data_sources)
    if outside:
        found.append(
            (
                "warn",
                f"{_plural(len(outside), 'passage')} (#{', #'.join(str(p.rank) for p in outside[:5])}) came from "
                f"outside {describe_sources(r.data_sources)}: this vector store didn't apply the data source "
                "filter. Tag the files with your own metadata instead (a <file>.metadata.json like "
                '{"metadataAttributes": {"source": "faq"}}), sync, and filter with where={\'source\': \'faq\'}.',
            )
        )
    files = {p.uri or p.source for p in passages}
    if len(passages) >= 3 and len(files) == 1:
        found.append(
            (
                "info",
                f"All {len(passages)} passages come from one file ({source_name(next(iter(files)))}). If "
                "the answer could be in other files, try search_type='HYBRID', a larger n=, or where= "
                "to leave that file out.",
            )
        )
    seen: dict[str, Passage] = {}
    repeats: list[tuple[Passage, Passage]] = []
    for p in passages:
        text = " ".join(p.text.lower().split())
        if len(text) >= 40 and text in seen:
            repeats.append((seen[text], p))
        seen.setdefault(text, p)
    if repeats:
        first, again = repeats[0]
        where = (
            f"{source_name(first.uri)} and {source_name(again.uri)}"
            if first.uri != again.uri
            else source_name(first.uri)
        )
        found.append(
            (
                "info",
                f"{_plural(len(repeats), 'passage')} repeat{'s' if len(repeats) == 1 else ''} another "
                f"one word for word (e.g. #{first.rank} and #{again.rank}, from {where}): the same "
                "content is probably in several files. Removing the copies, then syncing, frees those "
                "slots for other passages.",
            )
        )
    short = [
        p for p in passages if p.content_type == "TEXT" and len(p.text.split()) < 20
    ]
    if len(short) >= 2 and len(short) * 2 >= len(passages):
        found.append(
            (
                "info",
                f"{len(short)} of {len(passages)} passages are under 20 words, which gives an answer "
                "little to work with. kb_info() shows the chunking; bigger chunks, or hierarchical "
                "chunking, usually help (set on a new data source).",
            )
        )
    missing = [
        code
        for code in _code_terms(r.question)
        if not any(code.lower() in p.text.lower() for p in passages)
    ]
    if missing and (r.search_type or "").upper() != "HYBRID":
        listed = " and ".join(repr(code) for code in missing[:3])
        found.append(
            (
                "warn",
                f"{listed} from the question appear{'s' if len(missing) == 1 else ''} in no passage. "
                "Semantic search matches meaning, not exact codes or names: try search_type='HYBRID', "
                "which also matches keywords (OpenSearch, Aurora and MongoDB stores support it).",
            )
        )
    if any(p.score is not None for p in passages):
        found.append(
            (
                "info",
                "Scores are relative: compare them with each other, not against a fixed cutoff. They "
                "depend on the vector store and the embedding model.",
            )
        )
    return found


def answer_findings(a: Answer) -> list[tuple[str, str]]:
    """
    How far to trust an answer, and what to check next -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    if a.guardrail_action == "INTERVENED":
        found.append(
            (
                "warn",
                "A guardrail intervened: the question or the answer was blocked or rewritten. The "
                "guardrail's settings in the Bedrock console say what it blocks.",
            )
        )
    if _REFUSAL in a.text.lower():
        narrowed = (
            f" Only {describe_sources(a.data_sources)} was searched: ask without data_source= to search "
            "every data source."
            if a.data_sources
            else ""
        )
        found.append(
            (
                "warn",
                'Bedrock gave its default "unable to assist" reply, which usually means the passages it '
                "retrieved don't hold the answer (or a filter removed them): run search(question) to see "
                f"what was retrieved.{narrowed}",
            )
        )
    elif not a.text.strip():
        found.append(
            ("warn", "The answer is empty. search(question) shows what was retrieved.")
        )
    elif not a.cited:
        found.append(
            (
                "warn",
                "The answer cites no source, so it's not grounded: it may be the model's own knowledge. "
                "search(question) shows what the knowledge base holds on this.",
            )
        )
    elif a.grounded_share < 0.5:
        found.append(
            (
                "warn",
                f"Only {a.grounded_share:.0%} of the answer is backed by a citation; the rest may be the "
                "model's own knowledge. Check the uncited sentences against the sources.",
            )
        )
    outside = [i for i, p in enumerate(a.sources, 1) if _outside([p], a.data_sources)]
    if outside:
        numbers = ", ".join(f"[{i}]" for i in outside[:5])
        found.append(
            (
                "warn",
                f"{_plural(len(outside), 'source')} ({numbers}) came from outside "
                f"{describe_sources(a.data_sources)}: this vector store didn't apply the data source filter. "
                "Tag the files with your own metadata instead (a <file>.metadata.json), sync, and filter with "
                "where=.",
            )
        )
    if a.stop_reason in ("max_tokens", "model_context_window_exceeded"):
        limit = f" (it was {a.max_tokens:,})" if a.max_tokens else ""
        found.append(
            (
                "warn",
                f"The answer hit the token limit and was cut off: raise max_tokens={limit}.",
            )
        )
    return found


def comparison_findings(c: SearchComparison) -> list[tuple[str, str]]:
    """
    What the differences between search settings mean for this question -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    for label, error in c.errors.items():
        kind, _ = _setting(label)
        why = (
            "this vector store only supports SEMANTIC search"
            if kind == "HYBRID" and "hybrid" in error.lower()
            else error
        )
        found.append(("info", f"{label} couldn't run: {why}."))
    labels = list(c.runs)
    reordered: set[tuple[str, str]] = (
        set()
    )  # (search types) whose different first passage was already reported
    for a, b in _pairs(labels):
        (kind_a, n_a), (kind_b, n_b) = _setting(a), _setting(b)
        new = [
            p
            for p in c.runs[b].passages
            if p.key not in {q.key for q in c.runs[a].passages}
        ]
        if n_a == n_b and kind_a != kind_b:
            if not new and len(c.runs[a].passages) == len(c.runs[b].passages):
                first_a, first_b = c.runs[a].passages[:1], c.runs[b].passages[:1]
                if first_a and first_b and first_a[0].key != first_b[0].key:
                    if (kind_a, kind_b) in reordered:
                        continue
                    reordered.add((kind_a, kind_b))
                    rank = next(
                        p.rank for p in c.runs[a].passages if p.key == first_b[0].key
                    )
                    found.append(
                        (
                            "info",
                            f"{kind_a} and {kind_b} return the same passages at n={n_a}, but {kind_b} "
                            f"puts a different one first: {first_b[0].source} ({_snippet_of(first_b[0])}), "
                            f"#{rank} under {kind_a}. The first passages weigh most in an answer: "
                            f"chunk() shows each in full; if {kind_b}'s is the better one, use "
                            f"search_type={kind_b!r}.",
                        )
                    )
                else:
                    found.append(
                        (
                            "info",
                            f"{kind_a} and {kind_b} return the same passages in the same order at "
                            f"n={n_a}: the search type doesn't change this question's results.",
                        )
                    )
                continue
            top = c.runs[b].passages[0] if c.runs[b].passages else None
            with_top = (
                ", including its top result" if top is not None and top in new else ""
            )
            if new:
                found.append(
                    (
                        "warn" if with_top else "info",
                        f"{kind_b} found {_plural(len(new), 'passage')} {kind_a} missed at n={n_a}{with_top} "
                        f"({', '.join(dict.fromkeys(p.source for p in new[:3]))}). If those are the right ones, "
                        f"use search_type={kind_b!r} in search() and ask().",
                    )
                )
        elif kind_a == kind_b and n_a != n_b and new:
            files = {source_name(p.uri) for p in c.runs[a].passages}
            new_files = sorted({source_name(p.uri) for p in new} - files)
            what = (
                f", {_plural(len(new_files), 'new file')} among them ({', '.join(new_files[:3])})"
                if new_files
                else ", all from files the first " + n_a + " already had"
            )
            found.append(
                (
                    "info",
                    f"{kind_a or 'The default search'} with n={n_b} adds {_plural(len(new), 'passage')}"
                    f"{what}. More passages give ask() more to work with, at more input tokens.",
                )
            )
    return found


def eval_findings(report: EvalReport) -> list[tuple[str, str]]:
    """
    What a retrieval check says to change -> [(level, message)]: the questions that missed, what came up instead,
    and the usual fixes.
    """
    found: list[tuple[str, str]] = []
    cases, missed = report.cases, report.missed
    if not cases:
        return found
    if missed:
        examples = "; ".join(
            f"{c.question!r} expected {c.expected!r}, got "
            f"{', '.join(c.top_sources[:2]) or 'nothing'}"
            for c in missed[:3]
        )
        found.append(
            (
                "warn",
                f"{len(missed)} of {len(cases)} questions missed: the expected source wasn't in the top "
                f"{report.k} ({examples}). First check the expected files are indexed "
                "(documents(), unsynced()); then the usual fixes: search_type='HYBRID' when questions "
                "hold codes or names, a larger n=, smaller chunks (a new data source), or where= "
                "filters.",
            )
        )
        firsts = Counter(
            c.top_sources[0].split(" p.")[0] for c in missed if c.top_sources
        )
        crowd = [(name, count) for name, count in firsts.most_common(1) if count >= 2]
        if crowd:
            found.append(
                (
                    "info",
                    f"{crowd[0][0]} came up first for {crowd[0][1]} of the missed questions: it may be "
                    "too broad, or duplicate the files you expected. where= can leave it out while you "
                    "check.",
                )
            )
    late = [c for c in cases if c.rank is not None and c.rank > 1]
    if late:
        found.append(
            (
                "info",
                f"{_plural(len(late), 'question')} found the expected source below the top result "
                f"(MRR {report.mrr:.2f}; 1.00 means always first). A reranker (search(..., rerank=True)) "
                "or HYBRID search can move it up.",
            )
        )
    return found
