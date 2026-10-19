"""
Files one by one: each file's state next to Bedrock's record of it, what needs a sync, how a file
was indexed, findings about the files and one file, and finding, sorting and picking them.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import unquote

from ..._bedrock.helpers import source_name
from ..._bedrock.models import KBDocument
from ..._kit.errors import _why
from ..._kit.fmt import _plural, human_age, human_size
from ..._kit.text import _clip, search_rank

from ..helpers import (
    CHUNK_LIMIT, DOCUMENT_TYPES, IMAGE_TYPES, MAX_FILE_SIZE, METADATA_SUFFIX, _code_terms, file_type, sync_command,
)
from ..models import (
    FILE_STATES, DataSourceInfo, DocumentChunks, FileInventory, FileProbe, IngestionJob, KBFile, KnowledgeBaseInfo,
    MetadataFile,
)
from .common import _METADATA_EXAMPLE, _fmt_day, _reasons_text
from .settings import describe_chunking, describe_parsing, describe_vector_store, store_name


# ------------------------------------------------------ files, one by one

SOURCE_KEY = "x-amz-bedrock-kb-source-uri"  # Bedrock tags every chunk with the file it came from

_ARCHIVED = ("GLACIER", "DEEP_ARCHIVE")  # storage classes a file has to be restored from before it can be read

_INDEXING = ("PENDING", "STARTING", "IN_PROGRESS", "DELETING", "DELETE_IN_PROGRESS")


def _same_uri(a: str | None, b: str | None) -> bool:
    """
    Whether two source locations name the same file, one of them URL-encoded or not.
    """
    return bool(a) and bool(b) and (a == b or unquote(str(a)) == unquote(str(b)))


def skip_reason(f: KBFile) -> str:
    """
    Why a sync probably left a file out, in a few words: 'it's 61.2 MB, over the 50 MB Bedrock reads from one
    file', '.mp4 isn't a type Bedrock reads', ... ('' when nothing about the file explains it).
    """
    kind = file_type(f.uri)
    if f.storage_class in _ARCHIVED:
        return f"it's in the {f.storage_class} storage class, which Bedrock can't read"
    if f.size is not None and f.size > MAX_FILE_SIZE:
        return f"it's {human_size(f.size)}, over the {human_size(MAX_FILE_SIZE)} Bedrock reads from one file"
    if f.size == 0:
        return "it's empty"
    if kind in IMAGE_TYPES:
        return "it's a picture, which only a foundation model or Data Automation parser reads"
    if not kind:
        return "it has no file extension, so Bedrock can't tell what type it is"
    if kind not in DOCUMENT_TYPES:
        return f".{kind} isn't a type Bedrock reads"
    return ""


def file_state(
    f: KBFile,
    synced: datetime | None,
    *,
    listed: bool = True,
    recorded: bool = True,
    in_scope: bool = True,
    sync_known: bool = True,
) -> tuple[str, str]:
    """
    (state, why) for a file, from Bedrock's record of it and the S3 object: one of FILE_STATES and a sentence.

    synced: when the data source's last successful sync started (None = it never finished one). listed: the bucket
    was listed in full, so a recorded file missing from it is deleted; recorded: Bedrock's document list was read in
    full, so a file without a record has none; in_scope: the file is in the data source's bucket and inclusion
    prefixes; sync_known: the sync history could be read.
    """
    status, reason = f.status, " ".join(f.reason.split()).rstrip(".")
    if status == "FAILED":
        return "failed", f"Bedrock couldn't index it: {reason or 'no reason given'}."
    if status == "NOT_FOUND" or (status and f.size is None and listed and in_scope):
        what = "Bedrock couldn't find it in the data source" if status == "NOT_FOUND" else "It's gone from S3"
        still = (", but searches still find it until the next sync removes it"
                 if f.searchable else ", and the next sync removes it from the knowledge base")
        return "deleted", f"{what}{still}."
    if status == "PARTIALLY_INDEXED":
        return "partial", f"Only part of it was indexed{': ' + reason if reason else ''}."
    if status in ("METADATA_PARTIALLY_INDEXED", "METADATA_UPDATE_FAILED"):
        how = "only part of its metadata was" if status == "METADATA_PARTIALLY_INDEXED" else "its metadata couldn't be"
        return "partial", (f"Its text is searchable, but {how} indexed, so where= filters see old values or none"
                           f"{': ' + reason if reason else ''}.")
    if status == "IGNORED":
        return "ignored", f"Bedrock ignored it{': ' + reason if reason else ''}."
    if status in _INDEXING:
        doing = "removing it from the index" if "DELET" in status else "indexing it"
        return "indexing", f"Bedrock is {doing} now ({status})."
    if status:
        since = f.indexed or synced
        if since is not None and f.modified is not None and f.modified > since:
            return "changed", (f"It changed in S3 {human_age(f.modified)}, after it was indexed, so searches use the "
                               "old version until the next sync.")
        if since is not None and f.metadata_modified is not None and f.metadata_modified > since:
            return "changed", ("Its metadata file changed after it was indexed, so where= filters use the old values "
                               "until the next sync.")
        return "indexed", "Indexed and searchable."
    if not recorded:
        return "unchecked", ("Bedrock's document list stopped at the limit before this file, so whether it's indexed "
                             "wasn't checked.")
    if not sync_known:
        return "new", "Bedrock has no record of it, so it isn't searchable."
    if synced is None:
        return "new", "The data source has never finished a sync, so it isn't searchable yet."
    if f.modified is None or f.modified > synced:
        return "new", f"Added after the last sync ({_fmt_day(synced)}), so it isn't searchable yet."
    why = skip_reason(f)
    return "skipped", (f"The last sync ({_fmt_day(synced)}) ran after it was saved but didn't index it"
                       + (f": {why}." if why else ", and Bedrock kept no record of why."))


def inventory_files(
    documents: Iterable[KBDocument],
    objects: Iterable[dict[str, Any]] | None = None,
    *,
    data_source_id: str = "",
    bucket: str = "",
    prefixes: Iterable[str] = (),
    last_sync: IngestionJob | None = None,
    complete: bool = True,
    recorded: bool = True,
    sync_known: bool = True,
) -> list[KBFile]:
    """
    One data source's files from both sides -> [KBFile] sorted by path, each with its state (file_state).

    documents: Bedrock's records of them (KBDocuments, from ListKnowledgeBaseDocuments); objects: the bucket's
    ListObjectsV2 'Contents' entries for an S3 data source (None when it wasn't listed). A <file>.metadata.json is
    attached to its file rather than listed. last_sync: the data source's last successful sync; complete: the bucket
    was listed in full; recorded: the document list was read in full.
    """
    prefixes = [p for p in prefixes if p]
    records = {d.uri: d for d in documents if d.uri}
    stored: dict[str, dict[str, Any]] = {}
    sidecars: dict[str, dict[str, Any]] = {}
    for obj in objects or []:
        key = str(obj.get("Key") or "")
        if not key or key.endswith("/"):
            continue
        uri = f"s3://{bucket}/{key}"
        if key.endswith(METADATA_SUFFIX):
            sidecars[uri[: -len(METADATA_SUFFIX)]] = obj
        else:
            stored[uri] = obj
    synced = last_sync.started if last_sync else None
    listed = objects is not None and complete
    files = []
    for uri in sorted(records.keys() | stored.keys()):
        record, obj, sidecar = records.get(uri), stored.get(uri), sidecars.get(uri)
        f = KBFile(uri, data_source_id or (record.data_source_id if record else ""))
        if record is not None:
            f.status, f.reason, f.indexed = record.status, record.reason, record.updated
        if obj is not None:
            f.size, f.modified = int(obj.get("Size") or 0), obj.get("LastModified")
            f.storage_class = str(obj.get("StorageClass") or "")
        if sidecar is not None:
            f.metadata_size, f.metadata_modified = int(sidecar.get("Size") or 0), sidecar.get("LastModified")
        in_scope = uri.startswith(f"s3://{bucket}/") and (not prefixes or any(f.key.startswith(p) for p in prefixes))
        f.state, f.note = file_state(f, synced, listed=listed, recorded=recorded, in_scope=in_scope,
                                     sync_known=sync_known)
        files.append(f)
    return files


_NEEDS_SYNC = ("failed", "changed", "new", "deleted")  # states the next sync of a file's data source changes


def sync_needed(inv: FileInventory) -> list[str]:
    """
    The data sources (IDs) whose next sync would change what's searchable: a file failed, changed, was added
    or was deleted since the last one. In the order they were listed.
    """
    wanted = {f.data_source_id for f in inv.files if f.state in _NEEDS_SYNC and f.data_source_id}
    return [ds_id for ds_id in inv.sources if ds_id in wanted]


def _named(files: list[KBFile], shown: int = 3) -> str:
    """
    'refund-policy.pdf, faq.md and 4 more'.
    """
    names = [f.name for f in files[:shown]]
    more = len(files) - len(names)
    if more:
        return ", ".join(names) + f" and {more:,} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


_SECTION_READS = {  # file_inventory() section -> (what it is, the permission that reads it)
    "settings": ("its settings", "bedrock:GetDataSource"),
    "syncs": ("its sync history", "bedrock:ListIngestionJobs"),
    "documents": ("Bedrock's document list", "bedrock:ListKnowledgeBaseDocuments"),
    "files": ("the files in its bucket", "s3:ListBucket"),
}


def inventory_findings(inv: FileInventory) -> list[tuple[str, str]]:
    """
    What a knowledge base's files say to do -> [(level, message)]: failed, changed, not yet synced, skipped and
    deleted files, and what couldn't be listed. sync_needed() says which data sources a sync would fix.
    """
    found: list[tuple[str, str]] = []
    by_state: dict[str, list[KBFile]] = {}
    for f in inv.files:
        by_state.setdefault(f.state, []).append(f)

    def they(files: list[KBFile], one: str, many: str) -> str:
        return one if len(files) == 1 else many

    failed = by_state.get("failed", [])
    if failed:
        reasons = Counter(_clip(" ".join(f.reason.split()).rstrip("."), 160) for f in failed if f.reason)
        top = ""
        if len(reasons) == 1:
            top = f" Bedrock's reason: {next(iter(reasons))}."
        elif reasons and reasons.most_common(1)[0][1] > 1:
            reason, count = reasons.most_common(1)[0]
            top = f" The most common reason: {reason} ({count} of them)."
        elif reasons:
            top = f" Bedrock's reasons: {'; '.join(list(reasons)[:2])}{'; …' if len(reasons) > 2 else ''}."
        found.append(("warn", f"{_plural(len(failed), 'file')} failed to index ({_named(failed)}), so nothing in "
                              f"{they(failed, 'it', 'them')} is searchable.{top} Fix or replace "
                              f"{they(failed, 'it', 'them')}, then sync."))
    changed = by_state.get("changed", [])
    if changed:
        found.append(("warn", f"{_plural(len(changed), 'file')} changed in S3 after "
                              f"{they(changed, 'it was', 'they were')} indexed ({_named(changed)}): searches and "
                              f"answers use the old {they(changed, 'version', 'versions')} until the next sync."))
    new = by_state.get("new", [])
    if new:
        found.append(("warn", f"{_plural(len(new), 'file')} {they(new, 'was', 'were')} added after the last sync "
                              f"({_named(new)}), so nothing in {they(new, 'it', 'them')} is searchable until the "
                              "next sync."))
    skipped = by_state.get("skipped", [])
    if skipped:
        why = Counter(skip_reason(f) or "no reason Bedrock kept" for f in skipped)
        reasons = "; ".join(f"{count} because {reason}" if count > 1 else reason
                            for reason, count in why.most_common(3))
        found.append(("warn", f"{_plural(len(skipped), 'file')} {they(skipped, 'was', 'were')} in S3 before the last "
                              f"sync, yet Bedrock has no record of {they(skipped, 'it', 'them')} "
                              f"({_named(skipped)}): {reasons}. Bedrock reads PDF, Word, Excel, CSV, HTML, Markdown "
                              "and text files of up to 50 MB (pictures too, with a model parser): convert or remove "
                              "the others."))
    deleted = by_state.get("deleted", [])
    live = [f for f in deleted if f.searchable]
    if live:
        found.append(("warn", f"{_plural(len(live), 'file')} {they(live, 'is', 'are')} gone from S3 but still in "
                              f"the index ({_named(live)}): answers can cite {they(live, 'it', 'them')} until the "
                              f"next sync removes {they(live, 'it', 'them')}."))
    if len(deleted) > len(live):
        gone = [f for f in deleted if not f.searchable]
        found.append(("info", f"{_plural(len(gone), 'file')} Bedrock has a record of {they(gone, 'is', 'are')} gone "
                              f"from S3 ({_named(gone)}); the next sync forgets {they(gone, 'it', 'them')}."))
    partial = by_state.get("partial", [])
    if partial:
        found.append(("warn", f"{_plural(len(partial), 'file')} {they(partial, 'is', 'are')} only partly indexed "
                              f"({_named(partial)}): open {they(partial, 'it', 'one')} to see what's missing."))
    ignored = by_state.get("ignored", [])
    if ignored:
        found.append(("info", f"Bedrock ignored {_plural(len(ignored), 'file')} ({_named(ignored)}), usually because "
                              "of their type: they aren't searchable."))
    indexing = by_state.get("indexing", [])
    if indexing:
        found.append(("info", f"{_plural(len(indexing), 'file')} {they(indexing, 'is', 'are')} being indexed or "
                              "removed now: their state changes when the sync finishes."))
    unchecked = by_state.get("unchecked", [])
    if unchecked:
        found.append(("info", f"Bedrock's document list stopped at {inv.limit:,}, so {_plural(len(unchecked), 'file')} "
                              "in S3 weren't checked: .core.file_inventory(..., limit=None) reads everything."))
    for ds_id, parts in inv.truncated.items():
        if parts == ["documents"] and unchecked:
            continue
        what = " and ".join("Bedrock's document list" if p == "documents" else "the bucket's files" for p in parts)
        found.append(("info", f"Listing {what} for {inv.sources.get(ds_id, ds_id)} stopped at {inv.limit:,}, so the "
                              "counts cover part of it: .core.file_inventory(..., limit=None) reads everything."))
    for ds_id, errors in inv.errors.items():
        name = inv.sources.get(ds_id, ds_id)
        for section, code in errors.items():
            what, permission = _SECTION_READS.get(section, (section, ""))
            effect = {
                "settings": "so its files aren't listed",
                "syncs": "so files added since the last sync can't be told from skipped ones",
                "documents": "so whether each file is indexed can't be told",
                "files": "so files added, changed or deleted since the last sync can't be told",
            }.get(section, "")
            found.append(("info", f"Couldn't read {what} for {name} ({_why(code, permission)}), {effect}."))
    for ds_id, kind in inv.kinds.items():
        if kind not in ("S3", "CUSTOM"):
            found.append(("info", f"{inv.sources.get(ds_id, ds_id)} is a {kind or 'non-S3'} data source: Bedrock "
                                  "keeps no list of its documents, so they aren't listed here (syncs() shows how "
                                  "many each sync read)."))
    if not inv.files and not inv.errors and all(k in ("S3", "CUSTOM") for k in inv.kinds.values()):
        found.append(("info", "No files yet: the data sources are empty, or have never been synced."))
    return found


def _failure_fix(reason: str) -> str:
    """
    What usually fixes a failed file, from Bedrock's reason.
    """
    text = reason.lower()
    if any(word in text for word in ("scanned", "no text", "image", "ocr")):
        return ("A scanned page has no text for the default parser: a data source with a foundation model or Data "
                "Automation parser reads it.")
    if any(word in text for word in ("encrypt", "password", "protected")):
        return "Save it without a password."
    if any(word in text for word in ("too large", "size", "exceed", "limit")):
        return f"Split it into files under {human_size(MAX_FILE_SIZE)}."
    if any(word in text for word in ("unsupported", "file type", "format", "extension")):
        return "Convert it to a type Bedrock reads: PDF, Word, Excel, CSV, HTML, Markdown or text."
    if "metadata" in text:
        return "Fix its metadata file (below)."
    if any(word in text for word in ("denied", "permission", "not authorized", "kms")):
        return "Let the knowledge base's role read it (s3:GetObject, and kms:Decrypt when a KMS key encrypts it)."
    if "throttl" in text:
        return "It was throttled: syncing again usually works."
    return "Fix or replace the file."


def file_findings(
    f: KBFile,
    chunks: DocumentChunks | None = None,
    metadata: MetadataFile | None = None,
    ds: DataSourceInfo | None = None,
    *,
    kb_id: str = "",
    region: str = "",
    others_have_metadata: bool = False,
) -> list[tuple[str, str]]:
    """
    What's wrong with how one file was indexed and what to do -> [(level, message)]: its state, with the command
    that syncs it, what its chunks show (none, tiny, repeated, cut at Retrieve's limit) and what's wrong with its
    metadata file. others_have_metadata: other files of its data source have a metadata file.
    """
    found: list[tuple[str, str]] = []
    command = sync_command(kb_id, f.data_source_id, region) if kb_id and f.data_source_id else ""
    if f.state == "failed":
        why = _reasons_text([f.reason], 1) if f.reason else "no reason given"
        found.append(("warn", f"Bedrock couldn't index this file ({why}), so nothing in it is searchable. "
                              f"{_failure_fix(f.reason)} Then sync"
                              + (f": {command}" if command else " its data source.")))
    elif f.state == "changed":
        found.append(("warn", f"{f.note} Sync to index the new version" + (f": {command}" if command else ".")))
    elif f.state == "new":
        found.append(("warn", f"{f.note} Sync to index it" + (f": {command}" if command else ".")))
    elif f.state == "skipped":
        found.append(("warn", f"{f.note} Fix that, then sync" + (f": {command}" if command else ".")))
    elif f.state == "deleted":
        found.append(("warn" if f.searchable else "info", f.note + (f" {command}" if command and f.searchable else "")))
    elif f.state in ("partial", "ignored"):
        found.append(("warn", f.note))
    elif f.state == "unchecked":
        found.append(("info", f.note))
    if f.storage_class in _ARCHIVED:
        found.append(("warn", f"It's in the {f.storage_class} storage class, which Bedrock can't read: restore it, or "
                              "copy it back to STANDARD, then sync."))
    if f.size is not None and f.size > MAX_FILE_SIZE:
        found.append(("warn", f"It's {human_size(f.size)}, over the {human_size(MAX_FILE_SIZE)} Bedrock reads from one "
                              "file: split it into smaller files, then sync."))
    if chunks is not None and f.searchable:
        stats = chunks.stats
        strategy = (ds.chunking if ds else {}).get("chunkingStrategy")
        if not chunks.chunks and not chunks.outside:
            found.append(("warn", f"Bedrock lists it as {f.status}, but a search limited to this file found none of "
                                  "its chunks. A scanned PDF or a picture has no text for the default parser (a data "
                                  "source with a foundation model or Data Automation parser reads it), or this vector "
                                  "store can't filter on the file (x-amz-bedrock-kb-source-uri)."))
        if chunks.outside:
            found.append(("warn", f"{_plural(chunks.outside, 'passage')} of other files came back for a search limited "
                                  "to this one: this vector store ignored the filter, so the chunks shown may not be "
                                  "all of this file's."))
        if chunks.truncated:
            found.append(("info", f"Retrieve returns at most {CHUNK_LIMIT} passages, so these are {CHUNK_LIMIT} of the "
                                  "file's chunks: it has more."))
        if stats.count == 1 and strategy == "NONE" and stats.tokens and stats.tokens[0] > 8000:
            found.append(("warn", f"The whole file is one chunk of about {stats.tokens[0]:,} tokens (its data source "
                                  "doesn't chunk). The embedding model reads only about the first 8,000 tokens of a "
                                  "text, so a question about the rest may never find it."))
        if stats.count >= 3 and stats.tiny * 2 >= stats.count:
            found.append(("info", f"{stats.tiny} of its {stats.count} chunks are under 20 words: little for a search "
                                  "to match or an answer to use. Headers, footers and page numbers often end up as chunks "
                                  "like these; bigger chunks (a new data source) also help."))
        if stats.repeats:
            found.append(("info", f"{_plural(stats.repeats, 'chunk')} repeat{'s' if stats.repeats == 1 else ''} "
                                  "another word for word: the file repeats itself (copied sections, headers), and the "
                                  "copies take places in search results that other text could have."))
        if (stats.coverage is not None and stats.coverage < 0.9 and not chunks.truncated
                and stats.placed == stats.count):
            found.append(("warn", f"Only about {stats.coverage:.0%} of the file's text is inside a chunk, so the rest "
                                  "can't be found by a search. If it changed since the last sync, sync again"
                                  + (f": {command}" if command else ".")))
    if metadata is not None:
        if metadata.error:
            found.append(("info", f"Couldn't read its metadata file ({_why(metadata.error, 's3:GetObject')})."))
        elif metadata.found and metadata.problems:
            more = f" ({len(metadata.problems) - 1} more problems below)" if len(metadata.problems) > 1 else ""
            found.append(("warn", f"Its metadata file has a problem: {metadata.problems[0]}{more}"))
        elif metadata.found and chunks is not None and chunks.chunks:
            missing = [k for k in metadata.attributes if not any(k in p.metadata for p in chunks.chunks)]
            if missing:
                found.append(("warn", f"Its metadata file sets {', '.join(map(repr, missing[:4]))}, which its chunks "
                                      "don't have yet, so where= filters on them don't match this file. Sync to apply "
                                      "it" + (f": {command}" if command else ".")))
        elif not metadata.found and others_have_metadata:
            found.append(("info", f"Other files of its data source have a metadata file and this one doesn't, so "
                                  f"where= filters on those attributes never match it: add {f.name}{METADATA_SUFFIX} "
                                  f"holding {_METADATA_EXAMPLE}, then sync."))
    return found


def file_steps(
    f: KBFile,
    ds: DataSourceInfo | None = None,
    kb: KnowledgeBaseInfo | None = None,
    chunks: DocumentChunks | None = None,
) -> list[tuple[str, str, str, str]]:
    """
    How a file went from S3 into the vector store, a step at a time -> [(step, what happened, detail, tone)]:
    stored, read by the parser, cut into chunks, transformed, embedded, stored as vectors, and where it stands now.
    tone is 'ok', 'warn', 'bad' or '' (not known).
    """
    steps: list[tuple[str, str, str, str]] = []
    failed = f.state == "failed"
    reason = f.reason.lower()
    if f.size is not None:
        parts = [human_size(f.size), (file_type(f.uri) or "no extension").upper(),
                 f"changed {human_age(f.modified)}" if f.modified else ""]
        if f.storage_class and f.storage_class != "STANDARD":
            parts.append(f.storage_class)
        tone = "bad" if f.storage_class in _ARCHIVED or f.size > MAX_FILE_SIZE else "warn" if not f.size else "ok"
        steps.append(("Stored in S3", " · ".join(p for p in parts if p), f.uri, tone))
    elif f.uri.startswith("s3://"):
        gone = f.state == "deleted"
        steps.append(("Stored in S3", "not in the bucket any more" if gone else "not listed", f.uri,
                      "warn" if gone else ""))
    else:
        steps.append(("Sent through the API", "a custom document", f.uri, ""))
    parse_failed = failed and any(word in reason for word in ("parse", "scanned", "text", "encrypt", "read", "corrupt"))
    steps.append(("Read by the parser", describe_parsing(ds.parsing) if ds else "-", "",
                  "bad" if parse_failed else "ok" if f.searchable else ""))
    how = describe_chunking(ds.chunking) if ds else ""
    if chunks is not None and chunks.chunks:
        stats = chunks.stats
        value = f"{stats.count}{'+' if chunks.truncated else ''} chunks, about {stats.median_tokens:,} tokens each"
        if stats.pages:
            value += f", from page {stats.pages[0]}" + (f" to {stats.pages[-1]}" if len(stats.pages) > 1 else "")
        steps.append(("Cut into chunks", value, how, "ok"))
    elif chunks is not None:
        steps.append(("Cut into chunks", "no chunks found", how, "warn" if f.searchable else ""))
    else:
        steps.append(("Cut into chunks", how or "-", "", "ok" if f.searchable else ""))
    if ds is not None and ds.transformation:
        steps.append(("Transformed", ds.transformation, "", "ok" if f.searchable else ""))
    if kb is not None:
        dims = f", {kb.embedding_dims:,} dimensions" if kb.embedding_dims else ""
        steps.append(("Embedded", (kb.embedding_model or "-") + dims, "each chunk becomes one vector",
                      "ok" if f.searchable else ""))
        name, where = store_name(kb.vector_store), describe_vector_store(kb.vector_store_detail)
        steps.append(("Stored as vectors", name, where[len(name):].strip(" ,:") if where.startswith(name) else where,
                      "ok" if f.searchable else ""))
    label, tone = FILE_STATES.get(f.state, (f.state or "?", ""))
    record = (f"Bedrock's status: {f.status}, updated {human_age(f.indexed)}" if f.status
              else "Bedrock has no record of it")
    steps.append(("Now", label, f"{f.note} {record}.".strip(), tone))
    return steps


def find_files(files: Iterable[KBFile], ref: str) -> list[KBFile]:
    """
    The files `ref` names: an s3:// path, a path in the bucket ('policies/refund-policy.pdf'), a file name in any
    case ('Refund-Policy.pdf'), or else every file whose path holds it.
    """
    text = str(ref or "").strip()
    files = list(files)
    if not text:
        return []
    exact = [f for f in files if f.uri == text or _same_uri(f.uri, text)]
    if exact:
        return exact
    low = text.lower().lstrip("/")
    for test in (lambda f: f.key.lower() == low, lambda f: f.key.lower().endswith("/" + low),
                 lambda f: f.name.lower() == low):
        hits = [f for f in files if test(f)]
        if hits:
            return hits
    return [f for f in files if low in f.uri.lower()]


def probe_findings(probe: FileProbe, n: int = 5) -> list[tuple[str, str]]:
    """
    Whether a file can answer a question, and whether an answer will see it -> [(level, message)]: where its best
    passage ranks across the whole knowledge base, against the n passages an answer gets (5 unless you change it).
    """
    name = source_name(probe.uri) or probe.uri
    inside, across = probe.inside.passages, probe.across.passages
    if not inside:
        return [("warn", f"No chunk of {name} came back for this question. The file isn't indexed (or has no text), or "
                         "this vector store can't filter on one file.")]
    found: list[tuple[str, str]] = []
    best = inside[0]
    above = list(dict.fromkeys(p.source for p in across[: (probe.rank or len(across) + 1) - 1]
                               if not _same_uri(p.uri, probe.uri)))
    if probe.rank == 1:
        found.append(("info", f"{name}'s best passage for this question is the knowledge base's best too, so answers "
                              "start from it."))
    elif probe.rank is not None and probe.rank <= n:
        found.append(("info", f"{name}'s best passage ranks #{probe.rank} across the whole knowledge base: within the "
                              f"{n} passages an answer gets."))
    elif probe.rank is not None:
        found.append(("warn", f"{name}'s best passage ranks #{probe.rank} across the whole knowledge base, so an "
                              f"answer that gets {n} passages won't see it ({', '.join(above[:3])} rank higher). Ask for "
                              f"n={probe.rank} passages, or narrow the search with data_source= or where= so the "
                              "others are left out."))
    else:
        firsts = ", ".join(above[:3]) or "other files"
        found.append(("warn", f"{name} has passages for this question (the best scores "
                              f"{'-' if best.score is None else f'{best.score:.2f}'}), but none ranks in the knowledge "
                              f"base's top {len(across)}: {firsts} come first. A narrower search (data_source= or "
                              "where=) or a reranker can bring it up."))
    codes = [c for c in _code_terms(probe.question) if not any(c.lower() in p.text.lower() for p in inside)]
    if codes and (probe.inside.search_type or "").upper() != "HYBRID":
        found.append(("info", f"{' and '.join(map(repr, codes[:3]))} from the question appear in none of its passages: "
                              "semantic search matches meaning, not codes; search_type='HYBRID' also matches words."))
    return found


_SORTS = {"problems": "problems first", "name": "name", "folder": "folder", "size": "largest first",
          "modified": "changed last", "indexed": "indexed last"}  # sort_files() orders

_STATE_WORDS = {  # what else status= takes: Bedrock's own statuses and a few plain words
    "not synced": "new", "unsynced": "new", "added": "new", "not indexed": "skipped", "missing": "skipped",
    "gone": "deleted", "not found": "deleted", "partially indexed": "partial", "metadata partially indexed": "partial",
    "metadata update failed": "partial", "pending": "indexing", "starting": "indexing", "in progress": "indexing",
    "deleting": "indexing", "delete in progress": "indexing", "stale": "changed", "modified": "changed",
}


def parse_file_state(text: str) -> str:
    """
    A state for status=: a FILE_STATES key ('failed', 'changed', 'new'...), its label ('Not synced yet'), or
    Bedrock's status ('PARTIALLY_INDEXED'), in any case -> the key.
    """
    words = " ".join(str(text or "").lower().replace("_", " ").replace("-", " ").split())
    if words in FILE_STATES:
        return words
    for key, (label, _) in FILE_STATES.items():
        if words == label.lower():
            return key
    if words in _STATE_WORDS:
        return _STATE_WORDS[words]
    raise ValueError(f"status= is one of {', '.join(map(repr, FILE_STATES))} (or Bedrock's own, like "
                     f"'PARTIALLY_INDEXED'); got {text!r}")


def sort_files(files: Iterable[KBFile], by: str = "problems") -> list[KBFile]:
    """
    Files in an order: 'problems' (worst state first, FILE_STATES' order, then by path), 'name', 'folder' (by
    path), 'size' (largest first), 'modified' (changed in S3 last, first) or 'indexed' (indexed last, first).
    """
    files = list(files)
    order = list(FILE_STATES)

    def when(moment: datetime | None) -> float:
        return moment.timestamp() if moment is not None else float("-inf")

    if by == "problems":
        return sorted(files, key=lambda f: (order.index(f.state) if f.state in order else len(order), f.key.lower()))
    if by == "name":
        return sorted(files, key=lambda f: (f.name.lower(), f.key.lower()))
    if by == "folder":
        return sorted(files, key=lambda f: f.key.lower())
    if by == "size":
        return sorted(files, key=lambda f: (-(f.size if f.size is not None else -1), f.key.lower()))
    if by == "modified":
        return sorted(files, key=lambda f: (-when(f.modified), f.key.lower()))
    if by == "indexed":
        return sorted(files, key=lambda f: (-when(f.indexed), f.key.lower()))
    raise ValueError(f"by= is one of {', '.join(map(repr, _SORTS))}; got {by!r}")


def pick_files(files: Iterable[KBFile], *, state: str = "", match: str | None = None) -> list[KBFile]:
    """
    The files in `state` (a FILE_STATES key; '' for any) whose path, name, Bedrock's reason or note hold `match`
    (search_rank's words), in the order given: what files() lists and save_file_list() saves.
    """
    picked = [f for f in files if not state or f.state == state]
    if match:
        picked = [f for f in picked if search_rank(match, (f.key, f.name, f.reason, f.note)) is not None]
    return picked
