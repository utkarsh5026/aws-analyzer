"""
BedrockKBAnalyzer's reads of files one by one: the inventory, metadata files, text, chunks and
probes.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.files import (
    SavedFileList, _link_seconds, _list_target, file_list_name, parse_list_link, write_file_list,
)
from ..._bedrock.filters import with_data_sources
from ..._bedrock.helpers import source_name
from ..._bedrock.models import KBDocument
from ..._bedrock.parsing import parse_document, parse_retrieve
from ..._kit.errors import _error_code, _error_name, _why
from ..._kit.fmt import _as_count, _as_int, human_size

from ..helpers import CHUNK_LIMIT, FILE_TEXT_LIMIT, METADATA_SUFFIX, _PLAIN_TYPES, file_type
from ..models import DataSourceInfo, DocumentChunks, FileInventory, FileProbe, KBFile, KnowledgeBaseInfo, MetadataFile
from ..analysis.parsing import _with_errors, parse_data_source
from ..analysis.files import SOURCE_KEY, _same_uri, inventory_files
from ..analysis.metadata import parse_metadata_file
from ..analysis.chunks import _single_spaced, chunk_stats, order_chunks, place_chunks


class _FileReads:
    """
    BedrockKBAnalyzer's reads of files one by one: every file next to Bedrock's record of it, the list saved
    as text, one file's status, metadata file, text and chunks in document order, and whether a question
    finds it.
    """

    # -------------------------------------------------------------------- files

    def file_inventory(
        self,
        kb: str,
        data_source: str | None = None,
        *,
        limit: int | None = 10_000,
        info: KnowledgeBaseInfo | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> FileInventory:
        """
        Every file of a knowledge base's S3 and custom data sources (or one, by ID or name), and whether each is
        searchable. Bedrock's document list is read next to the bucket's files, so files a sync skipped or hasn't
        reached yet, and files deleted since, show up too (KBFile.state). Reads at most `limit` documents and `limit`
        S3 files per data source (None = all) and says what stopped early; a part that can't be read is recorded in
        `errors` instead of raising. info: a describe() result to take the data sources' settings and syncs from.
        """
        kb_id = self.resolve(kb)
        limit = _as_count(limit, "limit")
        started = time.monotonic()
        inv = FileInventory(kb_id, self.kb_name(kb_id), limit=limit)
        if info is not None and info.id == kb_id and "data_sources" not in info.errors:
            sources = list(info.data_sources)
            if data_source is not None:
                wanted = str(data_source).strip()
                sources = [ds for ds in sources if ds.id == wanted] or [
                    ds for ds in sources if ds.name.lower() == wanted.lower()]
                if not sources:
                    self._pick_sources(kb_id, data_source)  # raises the error that lists them
        else:
            sources = self._pick_sources(kb_id, data_source)
        read = [0]

        def tick(count: int) -> None:
            read[0] += count
            if progress:
                progress(read[0])

        for ds in sources:
            inv.sources[ds.id] = ds.name or ds.id
            errors = inv.errors.setdefault(ds.id, {})
            if not ds.source_type and "data_source" not in ds.errors:  # a summary: its settings need their own call
                try:
                    ds = _with_errors(parse_data_source(self.client.get_data_source(
                        knowledgeBaseId=kb_id, dataSourceId=ds.id)["dataSource"]), {})
                except (ClientError, BotoCoreError) as exc:
                    errors["settings"] = _error_name(exc)
                    continue
            elif not ds.source_type:
                errors["settings"] = ds.errors["data_source"]
                continue
            inv.kinds[ds.id] = ds.source_type
            if ds.source_type not in ("S3", "CUSTOM"):
                continue
            last, sync_known = ds.last_success, True
            if last is None and "ingestion" not in ds.errors:
                try:
                    done = self._recent_jobs(kb_id, ds.id, 1, status="COMPLETE")
                    last = done[0] if done else None
                except (ClientError, BotoCoreError) as exc:
                    errors["syncs"], sync_known = _error_name(exc), False
            elif last is None:
                errors["syncs"], sync_known = ds.errors["ingestion"], False
            inv.last_sync[ds.id] = last
            docs, more_docs = self._documents_of(kb_id, ds.id, limit, tick, errors)
            objects, complete = None, False
            if ds.source_type == "S3" and ds.bucket:
                objects, complete = self._objects_of(ds, limit, tick, errors)
            cut = (["documents"] if more_docs else []) + (["files"] if objects is not None and not complete else [])
            if cut:
                inv.truncated[ds.id] = cut
            inv.files += inventory_files(
                docs, objects, data_source_id=ds.id, bucket=ds.bucket or "", prefixes=ds.prefixes, last_sync=last,
                complete=complete, recorded="documents" not in errors and not more_docs, sync_known=sync_known)
        inv.errors = {ds_id: found for ds_id, found in inv.errors.items() if found}
        inv.seconds = time.monotonic() - started
        return inv

    def _documents_of(self, kb_id: str, ds_id: str, limit: int | None, tick: Callable[[int], None],
                      errors: dict[str, str]) -> tuple[list[KBDocument], bool]:
        """
        (Bedrock's documents of one data source, whether it stopped at `limit`); an error goes in `errors`.
        """
        docs: list[KBDocument] = []
        try:
            for page in self.client.get_paginator("list_knowledge_base_documents").paginate(
                    knowledgeBaseId=kb_id, dataSourceId=ds_id):
                details = page.get("documentDetails", [])
                for desc in details:
                    if limit is not None and len(docs) >= limit:
                        return docs, True
                    docs.append(parse_document(desc))
                tick(len(details))
        except (ClientError, BotoCoreError) as exc:
            errors["documents"] = _error_name(exc)
        return docs, False

    def _objects_of(self, ds: DataSourceInfo, limit: int | None, tick: Callable[[int], None],
                    errors: dict[str, str]) -> tuple[list[dict[str, Any]] | None, bool]:
        """
        (The S3 objects under a data source's inclusion prefixes, whether every one was listed): at most `limit`
        files, metadata files aside. None when the bucket can't be listed (the error goes in `errors`).
        """
        objects: dict[str, dict[str, Any]] = {}
        files = 0
        try:
            for prefix in ds.prefixes or [""]:
                for page in self._s3_client().get_paginator("list_objects_v2").paginate(
                        Bucket=ds.bucket, Prefix=prefix):
                    contents = page.get("Contents", [])
                    for obj in contents:
                        if obj["Key"] in objects:
                            continue
                        if not obj["Key"].endswith((METADATA_SUFFIX, "/")):
                            if limit is not None and files >= limit:
                                return list(objects.values()), False
                            files += 1
                        objects[obj["Key"]] = obj
                    tick(len(contents))
        except (ClientError, BotoCoreError) as exc:
            errors["files"] = _error_name(exc)
            return None, False
        return list(objects.values()), True

    def save_file_list(
        self,
        files: FileInventory | Iterable[KBFile],
        path: str | None = None,
        *,
        link: str = "s3",
        expires: int = 3600,
    ) -> SavedFileList:
        """
        Writes a text file with a line per S3 file, in the order given: its s3:// path (link='s3'), its path in the
        bucket ('key') or a link that opens it in a browser ('https': presigned here with your credentials, no AWS
        call, working for `expires` seconds). files: a file_inventory() or some of its files. path: the file to write
        (a folder puts it there), else <knowledge base>-files.txt beside the notebook; a file of that name is
        replaced. Nothing in AWS changes.
        """
        link = parse_list_link(link)
        default = file_list_name(files.kb_name or files.kb_id) if isinstance(files, FileInventory) else "kb-files.txt"
        items = files.files if isinstance(files, FileInventory) else files
        sign = None
        if link == "https":
            expires = _link_seconds(expires)

            def sign(uri: str) -> str | None:
                return self.file_url(uri, expires=expires)

        return write_file_list(items, _list_target(path, default), link=link, sign=sign, expires=expires)

    def document_status(self, kb: str, uri: str, data_source: str) -> KBDocument | None:
        """
        Bedrock's record of one file (GetKnowledgeBaseDocuments), for a file a truncated document list didn't
        reach: its status and reason, or None when Bedrock has none. data_source: its data source's ID or name.
        """
        kb_id = self.resolve(kb)
        [ds] = self._pick_sources(kb_id, data_source)
        identifier = ({"dataSourceType": "S3", "s3": {"uri": uri}} if uri.startswith("s3://")
                      else {"dataSourceType": "CUSTOM", "custom": {"id": uri}})
        found = self.client.get_knowledge_base_documents(
            knowledgeBaseId=kb_id, dataSourceId=ds.id, documentIdentifiers=[identifier]).get("documentDetails", [])
        docs = [parse_document(d) for d in found if d.get("status")]
        return docs[0] if docs else None

    def metadata_file(self, uri: str) -> MetadataFile:
        """
        The <file>.metadata.json next to an S3 file, read (its first 64 KB) and checked (parse_metadata_file): its
        attributes, which of them are embedded with the text, and what's wrong with it. found=False when there is none;
        one that can't be read keeps the error code in `error`.
        """
        target = str(uri or "").strip()
        target = target if target.endswith(METADATA_SUFFIX) else target + METADATA_SUFFIX
        bucket, _, key = target[5:].partition("/") if target.startswith("s3://") else ("", "", "")
        if not bucket or not key:
            raise ValueError(f"{uri!r} isn't an s3:// path, so it has no metadata file")
        try:
            resp = self._s3_client().get_object(Bucket=bucket, Key=key, Range="bytes=0-65535")
        except ClientError as exc:
            code = _error_code(exc)
            if code in ("NoSuchKey", "404", "NotFound"):
                return MetadataFile(target)
            return MetadataFile(target, error=code)
        except BotoCoreError as exc:
            return MetadataFile(target, error=type(exc).__name__)
        body = resp["Body"].read()
        total = str(resp.get("ContentRange") or "").rpartition("/")[2]
        meta = parse_metadata_file(body, target, size=int(total) if total.isdigit() else len(body))
        meta.modified = resp.get("LastModified")
        return meta

    def file_text(self, uri: str, limit: int = FILE_TEXT_LIMIT) -> str | None:
        """
        The first `limit` bytes of a plain text file (.txt, .md, ...) in S3, as text; None for other types, whose
        text only Bedrock's parser can read.
        """
        if file_type(uri) not in _PLAIN_TYPES or not str(uri).startswith("s3://"):
            return None
        bucket, _, key = str(uri)[5:].partition("/")
        resp = self._s3_client().get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max(1, limit) - 1}")
        return resp["Body"].read().decode("utf-8", "replace")

    def document_chunks(
        self, kb: str, uri: str, *, n: int = CHUNK_LIMIT, data_source: Any = None, text: bool = True
    ) -> DocumentChunks:
        """
        One file's chunks as the vector store holds them, in document order: a Retrieve limited to the file (a
        filter on x-amz-bedrock-kb-source-uri) for up to n of them (at most 100, Retrieve's limit). For a plain text
        file, text=True also reads the file to place each chunk in it, and to tell how much of the text is in a
        chunk. Passages of other files that come back anyway (a vector store that ignores the filter) are counted in
        `outside` and left out.
        """
        kb_id = self.resolve(kb)
        uri = str(uri or "").strip()
        if not uri.startswith("s3://"):
            raise ValueError(f"document_chunks() takes a file's s3:// path, got {uri!r}: a custom document's chunks "
                             "can't be told apart by their source")
        n = _as_int(n, "n")
        if not 1 <= n <= CHUNK_LIMIT:
            raise ValueError(f"n can be 1 to {CHUNK_LIMIT} (Retrieve returns at most {CHUNK_LIMIT} passages); got {n}")
        sources = self.resolve_sources(kb_id, data_source)
        condition = with_data_sources({"equals": {"key": SOURCE_KEY, "value": uri}}, sources)
        query = " ".join(re.split(r"[\W_]+", source_name(uri).rsplit(".", 1)[0])).strip() or "document"
        started = time.monotonic()
        resp = self._runtime_client().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": n, "filter": condition}},
        )
        results = resp.get("retrievalResults") or []
        passages = parse_retrieve(resp)
        mine = [p for p in passages if _same_uri(p.uri, uri)]
        found = DocumentChunks(kb_id, uri, asked=n, truncated=len(passages) >= n, outside=len(passages) - len(mine),
                               query=query)
        found.document_ids = {p.key: str(r["documentId"]) for p, r in zip(passages, results) if r.get("documentId")}
        spans: dict[str, tuple[int, int]] = {}
        if text and mine and file_type(uri) in _PLAIN_TYPES:
            try:
                body = self.file_text(uri) or ""
            except (ClientError, BotoCoreError) as exc:
                why = _why(_error_name(exc), "s3:GetObject")
                found.text_note = f"couldn't read the file to place its chunks ({why})"
            else:
                spans, found.text_length = place_chunks(body, mine), len(_single_spaced(body))
                if len(body.encode("utf-8")) >= FILE_TEXT_LIMIT:
                    found.text_note = f"only its first {human_size(FILE_TEXT_LIMIT)} was read to place its chunks"
        found.chunks = order_chunks(mine, spans)
        found.spans = spans
        found.stats = chunk_stats(found.chunks, spans, found.text_length if not found.text_note else 0)
        found.seconds = time.monotonic() - started
        return found

    def probe_file(
        self,
        kb: str,
        uri: str,
        question: str,
        *,
        n: int = 10,
        across: int = 20,
        search_type: str | None = None,
    ) -> FileProbe:
        """
        One question asked of a file and of the whole knowledge base (two Retrieves): the file's n best passages
        for it, and where the file's best one ranks among the knowledge base's top `across`, so whether it holds an
        answer and whether an answer will see it.
        """
        kb_id = self.resolve(kb)
        inside = self.retrieve(kb_id, question, n, where={"equals": {"key": SOURCE_KEY, "value": str(uri)}},
                               search_type=search_type)
        inside.passages = [p for p in inside.passages if _same_uri(p.uri, uri)]
        whole = self.retrieve(kb_id, question, across, search_type=search_type)
        rank = next((p.rank for p in whole.passages if _same_uri(p.uri, uri)), None)
        return FileProbe(str(uri), inside.question, inside, whole, rank)
