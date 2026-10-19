"""
The dataclasses BedrockKBAnalyzer returns: knowledge bases, data sources and syncs, documents,
searches and answers, comparisons and evaluations, and files one by one (their state, metadata and
chunks).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .._bedrock.helpers import SEARCHABLE, source_name
from .._bedrock.models import Citation, Passage
from .._kit.deps import _require
from .._kit.fmt import _utcnow

from .helpers import CHUNK_LIMIT, METADATA_SUFFIX, _arn_region


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_RUNNING = ("STARTING", "IN_PROGRESS", "STOPPING")


@dataclass
class IngestionJob:
    """
    One sync (ingestion job) of a data source. Counts are documents: files, web pages or records.
    """

    id: str
    data_source_id: str = ""
    status: str = ""  # STARTING | IN_PROGRESS | COMPLETE | FAILED | STOPPING | STOPPED
    started: datetime | None = None
    updated: datetime | None = None
    scanned: int = 0  # documents the sync looked at
    metadata_scanned: int = 0  # <file>.metadata.json files it found
    new: int = 0  # indexed for the first time
    modified: int = 0  # indexed again because they changed
    metadata_modified: int = 0
    deleted: int = 0  # removed from the index because they're gone from the source
    failed: int = 0  # couldn't be indexed (documents(status='FAILED') says why)
    skipped: int = 0
    failure_reasons: list[str] = field(
        default_factory=list
    )  # why the job itself failed (GetIngestionJob only)

    @property
    def running(self) -> bool:
        return self.status in _RUNNING

    @property
    def duration(self) -> timedelta | None:
        """
        How long it ran (so far, while it's running).
        """
        if self.started is None:
            return None
        end = _utcnow() if self.running else self.updated
        return None if end is None else end - self.started

    @property
    def ok(self) -> bool:
        """
        Finished, and every document it read was indexed.
        """
        return self.status == "COMPLETE" and not self.failed


@dataclass
class DataSourceInfo:
    """
    Where a knowledge base's documents come from and how they're cut into chunks. Parts that couldn't be read
    are listed in `errors` (section -> error code).
    """

    id: str
    name: str = ""
    status: str = (
        ""  # AVAILABLE | CREATING | UPDATING | DELETING | FAILED | DELETE_UNSUCCESSFUL
    )
    kb_id: str = ""
    description: str = ""
    source_type: str = (
        ""  # S3 | WEB | CONFLUENCE | SHAREPOINT | SALESFORCE | CUSTOM | ...
    )
    location: str = ""  # e.g. 's3://bucket/prefix/', or the web site's seed URLs
    bucket: str | None = None  # S3 data sources only
    prefixes: list[str] = field(
        default_factory=list
    )  # S3 inclusion prefixes ([] = the whole bucket)
    chunking: dict[str, Any] = field(
        default_factory=dict
    )  # chunkingConfiguration ({} = Bedrock's default)
    parsing: dict[str, Any] = field(
        default_factory=dict
    )  # parsingConfiguration ({} = Bedrock's default)
    transformation: str | None = None  # custom Lambda / context enrichment, described
    deletion_policy: str | None = (
        None  # DELETE | RETAIN: what happens to the chunks when the data source is deleted
    )
    created: datetime | None = None
    updated: datetime | None = None
    failure_reasons: list[str] = field(default_factory=list)
    jobs: list[IngestionJob] = field(default_factory=list)  # recent syncs, newest first
    last_sync: IngestionJob | None = None  # the newest sync, whatever its outcome
    last_success: IngestionJob | None = None  # the newest COMPLETE sync among `jobs`
    errors: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # GetDataSource's 'dataSource', as read


@dataclass
class KnowledgeBaseInfo:
    """
    A knowledge base's settings, data sources and their latest syncs. Parts that couldn't be read are listed in
    `errors` (section -> error code).
    """

    id: str
    name: str = ""
    arn: str = ""
    status: str = ""  # ACTIVE | CREATING | UPDATING | DELETING | FAILED | ...
    kb_type: str = ""  # VECTOR | KENDRA | SQL | MANAGED
    description: str = ""
    embedding_model: str = ""  # model ID, e.g. 'amazon.titan-embed-text-v2:0'
    embedding_dims: int | None = None
    vector_store: str = ""  # OPENSEARCH_SERVERLESS | PINECONE | RDS | S3_VECTORS | ... (KENDRA / REDSHIFT for those)
    vector_store_detail: dict[str, Any] = field(
        default_factory=dict
    )  # storageConfiguration, as AWS returns it
    role_arn: str = ""
    created: datetime | None = None
    updated: datetime | None = None
    failure_reasons: list[str] = field(default_factory=list)
    data_sources: list[DataSourceInfo] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # GetKnowledgeBase's 'knowledgeBase', as read

    @property
    def region(self) -> str:
        return _arn_region(self.arn)

    @property
    def last_sync(self) -> IngestionJob | None:
        """
        The newest sync of any of its data sources.
        """
        jobs = [ds.last_sync for ds in self.data_sources if ds.last_sync]
        return max(jobs, key=lambda j: j.started or _EPOCH, default=None)


@dataclass
class DocumentSummary:
    """
    Counts of documents by status, and the most common reasons documents failed.
    """

    total: int = 0
    counts: dict[str, int] = field(
        default_factory=dict
    )  # status -> documents, most common first
    reasons: list[tuple[str, int]] = field(
        default_factory=list
    )  # (reason, documents), most common first
    truncated: bool = False  # stopped at `limit`: counts cover only the documents read
    errors: dict[str, str] = field(
        default_factory=dict
    )  # data source ID -> error code (e.g. unsupported type)


@dataclass
class Retrieval:
    """
    What one Retrieve call returned for a question, best passage first.
    """

    kb_id: str
    question: str
    passages: list[Passage] = field(default_factory=list)
    kb_name: str = ""
    n: int = 5  # passages asked for
    search_type: str | None = None  # SEMANTIC | HYBRID; None = Bedrock's choice
    where: Any = None
    reranked: str | None = None  # the reranking model, when one re-ordered the results
    seconds: float = 0.0
    guardrail_action: str | None = None  # INTERVENED when a guardrail stepped in
    data_sources: dict[str, str] = field(
        default_factory=dict
    )  # ID -> name of the data sources searched; {} = all of them

    def to_df(self):
        """
        One row per passage: rank, score, source, page, text, IDs and metadata.
        """
        pd = _require("pandas", "Retrieval.to_df")
        return pd.DataFrame(
            [
                {
                    "rank": p.rank,
                    "score": p.score,
                    "source": source_name(p.uri),
                    "page": p.page,
                    "text": p.text,
                    "uri": p.uri,
                    "chunk_id": p.chunk_id,
                    "data_source_id": p.data_source_id,
                    "content_type": p.content_type,
                    "metadata": p.metadata,
                }
                for p in self.passages
            ]
        )


@dataclass
class Answer:
    """
    A generated answer, the sources it was given and how much of it they back up.
    """

    question: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    sources: list[Passage] = field(default_factory=list)  # [1] is sources[0]
    engine: str = (
        "kb"  # 'kb' (RetrieveAndGenerate) | 'converse' (Retrieve, then Converse)
    )
    model: str = ""  # the model ID or inference profile that answered
    session_id: str | None = None  # RetrieveAndGenerate's, for follow-up questions
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_estimated: bool = (
        False  # True for engine='kb': RetrieveAndGenerate doesn't report tokens
    )
    stop_reason: str | None = (
        None  # converse only: end_turn | max_tokens | guardrail_intervened | ...
    )
    guardrail_action: str | None = None
    prompt: str | None = (
        None  # converse only: the user message sent, with the numbered sources
    )
    kb_id: str = ""
    kb_name: str = ""
    max_tokens: int | None = None
    data_sources: dict[str, str] = field(
        default_factory=dict
    )  # ID -> name of the data sources searched; {} = all of them

    @property
    def cited(self) -> list[int]:
        """
        The source numbers the answer cites.
        """
        return sorted({n for c in self.citations for n in c.sources})

    @property
    def grounded_share(self) -> float:
        """
        The share of the answer's characters (spaces aside) inside a span that cites a source.
        """
        covered = [False] * len(self.text)
        for c in self.citations:
            if c.sources:
                for i in range(max(0, c.start), min(len(self.text), c.end)):
                    covered[i] = True
        chars = [i for i, ch in enumerate(self.text) if not ch.isspace()]
        return sum(covered[i] for i in chars) / len(chars) if chars else 0.0

    def to_df(self):
        """
        One row per source: its number, whether the answer cites it, where it's from, and its text.
        """
        pd = _require("pandas", "Answer.to_df")
        cited = set(self.cited)
        return pd.DataFrame(
            [
                {
                    "n": i,
                    "cited": i in cited,
                    "source": source_name(p.uri),
                    "page": p.page,
                    "uri": p.uri,
                    "data_source_id": p.data_source_id,
                    "score": p.score,
                    "text": p.text,
                    "metadata": p.metadata,
                }
                for i, p in enumerate(self.sources, 1)
            ]
        )


@dataclass
class FileChange:
    """
    A file in a data source's bucket, added or changed after the last successful sync.
    """

    key: str
    modified: datetime | None = None
    size: int = 0
    bucket: str = ""

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@dataclass
class SyncFreshness:
    """
    An S3 data source's files compared with its last successful sync.
    """

    data_source: DataSourceInfo
    last_sync: IngestionJob | None = None  # the last COMPLETE sync (None = never)
    files: int = 0  # files listed, metadata files aside
    changed: list[FileChange] = field(
        default_factory=list
    )  # added or changed since last_sync started, newest first
    metadata_changed: int = (
        0  # <file>.metadata.json files changed since then (their filters change too)
    )
    truncated: bool = False  # stopped listing at `limit`
    note: str = ""  # why it wasn't checked, when it wasn't (not S3, no access to the bucket, ...)

    def to_df(self):
        """
        One row per changed file: key, when it changed, size.
        """
        pd = _require("pandas", "SyncFreshness.to_df")
        return pd.DataFrame(
            [
                {"key": c.key, "modified": c.modified, "size": c.size, "uri": c.uri}
                for c in self.changed
            ],
            columns=["key", "modified", "size", "uri"],
        )


@dataclass
class SearchComparison:
    """
    The same question searched with different settings, and how much the results agree.
    """

    question: str = ""
    kb_id: str = ""
    kb_name: str = ""
    runs: dict[str, Retrieval] = field(
        default_factory=dict
    )  # label ('HYBRID n=10') -> result
    overlap: dict[tuple[str, str], float] = field(
        default_factory=dict
    )  # (label, label) -> shared / all passages
    unique: dict[str, list[Passage]] = field(
        default_factory=dict
    )  # label -> passages no other setting found
    errors: dict[str, str] = field(
        default_factory=dict
    )  # label -> why that setting couldn't run
    data_sources: dict[str, str] = field(
        default_factory=dict
    )  # ID -> name of the data sources searched; {} = all of them

    def ranks(self) -> list[tuple[Passage, dict[str, int | None]]]:
        """
        Every passage any setting found, with its rank under each setting (None = not found), best first.
        """
        found: dict[str, tuple[Passage, dict[str, int | None]]] = {}
        for label, r in self.runs.items():
            for p in r.passages:
                entry = found.setdefault(p.key, (p, dict.fromkeys(self.runs)))
                entry[1][label] = p.rank
        return sorted(
            found.values(),
            key=lambda e: (
                min(r for r in e[1].values() if r is not None),
                -sum(r is not None for r in e[1].values()),
            ),
        )

    def to_df(self):
        """
        One row per passage, one column per setting holding its rank there.
        """
        pd = _require("pandas", "SearchComparison.to_df")
        return pd.DataFrame(
            [{"source": p.source, "text": p.text, **ranks} for p, ranks in self.ranks()]
        )


@dataclass
class EvalCase:
    """
    One test question, and where its expected source came up.
    """

    question: str
    expected: Any  # a piece of the source's URI, file name or text (or a list of them)
    rank: int | None = (
        None  # where the expected source first came up; None = not in the top k
    )
    top_sources: list[str] = field(default_factory=list)  # what came up first
    seconds: float = 0.0


@dataclass
class EvalReport:
    """
    How well retrieval finds the expected sources for a set of test questions.
    """

    kb_id: str = ""
    kb_name: str = ""
    cases: list[EvalCase] = field(default_factory=list)
    k: int = 5  # passages retrieved per question
    hit_rate: float = 0.0  # share of questions whose expected source was in the top k
    mrr: float = 0.0  # mean reciprocal rank: 1.0 = always first, 0.5 = second on average, 0 = never found
    seconds: float = 0.0
    search_type: str | None = None
    where: Any = None
    data_sources: dict[str, str] = field(
        default_factory=dict
    )  # ID -> name of the data sources searched; {} = all of them

    @property
    def missed(self) -> list[EvalCase]:
        return [c for c in self.cases if c.rank is None]

    def to_df(self):
        """
        One row per question: expected source, its rank (None = missed) and what came up first.
        """
        pd = _require("pandas", "EvalReport.to_df")
        return pd.DataFrame(
            [
                {
                    "question": c.question,
                    "expected": c.expected,
                    "rank": c.rank,
                    "hit": c.rank is not None,
                    "top_sources": c.top_sources,
                    "seconds": c.seconds,
                }
                for c in self.cases
            ]
        )


# What a file of a knowledge base adds up to, worst first: state -> (label, tone). KBFile.state is one of these.
FILE_STATES: dict[str, tuple[str, str]] = {
    "failed": ("Failed", "bad"),
    "changed": ("Changed since sync", "warn"),
    "new": ("Not synced yet", "warn"),
    "skipped": ("Skipped by the sync", "warn"),
    "deleted": ("Deleted from S3", "warn"),
    "partial": ("Partly indexed", "warn"),
    "ignored": ("Ignored", ""),
    "indexing": ("Indexing", ""),
    "unchecked": ("Not checked", ""),
    "indexed": ("Indexed", "ok"),
}


@dataclass
class KBFile:
    """
    One file of a knowledge base: the S3 object and Bedrock's record of it side by side, and what they add up to.
    `state` is one of FILE_STATES (failed, changed, new, skipped, deleted, partial, ignored, indexing, unchecked,
    indexed) and `note` says why, in a sentence.
    """

    uri: str  # s3://bucket/key, or a custom data source's document ID
    data_source_id: str = ""
    state: str = ""
    note: str = ""
    status: str = ""  # Bedrock's document status: INDEXED, FAILED, ...; '' when it has no record of the file
    reason: str = ""  # Bedrock's reason, for a failed or ignored file
    indexed: datetime | None = None  # when Bedrock last updated its record of the file
    size: int | None = None  # bytes in S3; None when the file isn't there (or the bucket wasn't listed)
    modified: datetime | None = None  # when it last changed in S3
    storage_class: str = ""
    metadata_size: int | None = None  # its <file>.metadata.json, when it has one
    metadata_modified: datetime | None = None

    @property
    def name(self) -> str:
        return source_name(self.uri) or self.uri

    @property
    def key(self) -> str:
        """
        Its path in the bucket ('policies/refund-policy.pdf'); a custom document's ID as it is.
        """
        return self.uri[5:].partition("/")[2] if self.uri.startswith("s3://") else self.uri

    @property
    def folder(self) -> str:
        """
        'policies/' for 'policies/refund-policy.pdf', '' at the top of the bucket.
        """
        return self.key.rsplit("/", 1)[0] + "/" if "/" in self.key else ""

    @property
    def metadata_uri(self) -> str:
        return self.uri + METADATA_SUFFIX if self.uri.startswith("s3://") else ""

    @property
    def searchable(self) -> bool:
        """
        Whether searches find it: Bedrock indexed its text (maybe an older version of it).
        """
        return self.status in SEARCHABLE


@dataclass
class FileInventory:
    """
    Every file of a knowledge base's S3 and custom data sources, and whether each is searchable (KBFile.state).
    An S3 data source is read from both sides, Bedrock's document list and the bucket, so files a sync skipped or
    hasn't reached yet, and files deleted since, show up too. What couldn't be read is in `errors`.
    """

    kb_id: str
    kb_name: str = ""
    files: list[KBFile] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # ID -> name of every data source looked at
    kinds: dict[str, str] = field(default_factory=dict)  # ID -> its type: S3, CUSTOM, WEB, ...
    last_sync: dict[str, IngestionJob | None] = field(default_factory=dict)  # ID -> its last successful sync
    truncated: dict[str, list[str]] = field(default_factory=dict)  # ID -> what stopped at the limit: documents, files
    errors: dict[str, dict[str, str]] = field(default_factory=dict)  # ID -> {settings|syncs|documents|files: code}
    limit: int | None = 10_000  # documents, and S3 files, read per data source
    seconds: float = 0.0

    def counts(self) -> dict[str, int]:
        """
        Files per state, worst first (FILE_STATES' order), without the states no file is in.
        """
        found = Counter(f.state for f in self.files)
        return {state: found[state] for state in FILE_STATES if found[state]}

    def to_df(self):
        """
        One row per file: where it is, its state and why, Bedrock's status, size and when it changed and was
        indexed.
        """
        pd = _require("pandas", "FileInventory.to_df")
        columns = ["uri", "name", "data_source", "state", "note", "status", "reason", "size", "modified", "indexed",
                   "metadata"]
        return pd.DataFrame(
            [
                {
                    "uri": f.uri,
                    "name": f.name,
                    "data_source": self.sources.get(f.data_source_id, f.data_source_id),
                    "state": f.state,
                    "note": f.note,
                    "status": f.status,
                    "reason": f.reason,
                    "size": f.size,
                    "modified": f.modified,
                    "indexed": f.indexed,
                    "metadata": f.metadata_size is not None,
                }
                for f in self.files
            ],
            columns=columns,
        )


@dataclass
class MetadataFile:
    """
    A file's <file>.metadata.json: the attributes in it (what where= filters match) and what's wrong with it, in
    plain English. found=False when the file has none.
    """

    uri: str  # s3://bucket/key.metadata.json
    found: bool = False
    size: int | None = None
    modified: datetime | None = None
    text: str = ""  # as written (its first 64 KB)
    attributes: dict[str, Any] = field(default_factory=dict)  # name -> value
    types: dict[str, str] = field(default_factory=dict)  # name -> STRING | NUMBER | BOOLEAN | STRING_LIST
    embedded: list[str] = field(default_factory=list)  # the attributes also embedded with the text
    problems: list[str] = field(default_factory=list)
    error: str = ""  # why it couldn't be read (an error code)


@dataclass
class ChunkStats:
    """
    What a file's chunks look like: how many, how big, the pages they come from, and how they fit together.
    """

    count: int = 0
    tokens: list[int] = field(default_factory=list)  # each chunk's estimated tokens, in document order
    words: list[int] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)  # the pages they come from, sorted
    tiny: int = 0  # chunks under 20 words
    repeats: int = 0  # chunks whose text an earlier chunk already has
    overlaps: list[int] = field(default_factory=list)  # characters each chunk repeats from the one before it
    coverage: float | None = None  # the share of the file's text inside a chunk, when the file's text was read
    placed: int = 0  # chunks found in the file's text

    @property
    def median_tokens(self) -> int:
        return int(statistics.median(self.tokens)) if self.tokens else 0


@dataclass
class DocumentChunks:
    """
    A file's chunks as the vector store holds them (a Retrieve limited to the file), in document order.
    """

    kb_id: str
    uri: str
    chunks: list[Passage] = field(default_factory=list)  # in document order (order_chunks)
    asked: int = CHUNK_LIMIT  # passages Retrieve was asked for
    truncated: bool = False  # Retrieve returned all it was asked for: the file may have more chunks
    outside: int = 0  # passages of other files that came back anyway: the vector store ignored the filter
    query: str = ""  # what Retrieve was asked: it needs a question, so the file's name
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)  # chunk key -> where it sits in the file's text
    text_length: int = 0  # characters of the file's text read (whitespace collapsed); 0 = not read
    text_note: str = ""  # why the file's text wasn't read, when that was tried
    stats: ChunkStats = field(default_factory=ChunkStats)
    document_ids: dict[str, str] = field(default_factory=dict)  # chunk key -> Bedrock's document ID (newer boto3)
    seconds: float = 0.0


@dataclass
class FileProbe:
    """
    One question asked of a file and of the whole knowledge base: whether the file holds an answer, and whether it
    ranks high enough among every file's passages for an answer to see it.
    """

    uri: str
    question: str
    inside: Retrieval  # the file's own chunks for the question
    across: Retrieval  # the best passages of the whole knowledge base for it
    rank: int | None = None  # where the file's first passage ranks in `across`; None = not in it
