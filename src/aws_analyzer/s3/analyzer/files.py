"""
S3Analyzer's reads of one file, and file_details().
"""

from __future__ import annotations

import io
import json
import math
import tarfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, BinaryIO, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.deps import _require
from ..._kit.errors import _error_code
from ..._kit.fmt import parse_size

from ..helpers import KB, MB, parse_s3_uri, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..models import ArchiveEntry, ArchiveListing, Document, FileDetailsReport, ObjectInfo, Picture
from ..readers.formats import (
    _CODEC_ALIASES, _CSV_SEPARATORS, _DECOMPRESSORS, _READ_ERRORS, _browser_type, detect_format, sniff_format,
)
from ..readers.avro import parse_avro
from ..readers.office import _old_office_note, office_kind, parse_docx, parse_pptx
from ..readers.pdf import parse_pdf, render_pdf_pages
from ..readers.data import _npy_header, _parse_safetensors, _read_arrow_table, _safetensors_length, _zip_time
from ..analysis.summary import make_filter
from ..analysis.duplicates import _UNREADABLE_REASONS
from ..analysis.details import FileDetails, _describe_into, _error_text
from .threads import _pool_size, _run_in_threads
from .streams import _BlockReader, _BodyReader, _RangeReader, _ReadBudget, _stopped


class _FileReads:
    """
    S3Analyzer's reads of one file: its headers and tags, its bytes, text, lines, JSON, tables, arrays,
    archives and documents, a link to it, and file_details() over many files.
    """

    def file_details(
        self,
        uri: str,
        *,
        pattern: str | None = None,
        extensions: str | Iterable[str] | None = None,
        limit: int | None = 200,
        max_read: int | str | None = "1GB",
        password: str | None = None,
        max_workers: int = 8,
        progress: Callable[[int], None] | None = None,
        read_progress: Callable[[int, int], None] | None = None,
    ) -> FileDetailsReport:
        """
        What's inside each file under `uri` (see describe_file): a PDF's pages, a table's rows and columns, a
        picture's size, ... Files can be picked with `pattern` (a glob on the name, or on the key when it has a
        '/') and `extensions` ('pdf' also matches 'pdf.gz'); the first `limit` of them in key order are read
        (None = all). A uri that names one file describes just that file. See describe_objects for what is read
        and max_read. progress gets the running count of keys listed; read_progress (files done, files).
        """
        bucket, prefix = parse_s3_uri(uri)
        started = time.monotonic()
        keep = make_filter(pattern=pattern, extensions=extensions)
        found: list[ObjectInfo] = []
        truncated = False
        for obj in self.iter_objects(uri, progress=progress):
            if obj.key == prefix and not prefix.endswith("/"):  # listed first: the uri names this file
                found = [obj]
                break
            if not keep(obj):
                continue
            if limit is not None and len(found) >= limit:
                truncated = True
                break
            found.append(obj)
        report = self.describe_objects(
            found, max_read=max_read, password=password, max_workers=max_workers, progress=read_progress
        )
        report.uri, report.limit, report.truncated = s3_uri(bucket, prefix), limit, truncated
        report.filters = {k: v for k, v in (("pattern", pattern), ("extensions", extensions)) if v is not None}
        report.seconds = time.monotonic() - started
        return report

    def describe_objects(
        self,
        objects: Iterable[ObjectInfo],
        *,
        max_read: int | str | None = "1GB",
        password: str | None = None,
        max_workers: int = 8,
        progress: Callable[[int, int], None] | None = None,
    ) -> FileDetailsReport:
        """
        What's inside each of these objects (see describe_file), read in parallel with ranged GETs of 256 KB
        blocks, fetching only the parts each format needs: a PDF's page tree and first pages, a parquet footer, a
        picture's header, the first 256 KB of a CSV. Reading stops at `max_read` bytes in all (None = no cap; the
        files not reached get skipped='max_read') and after 128 MB of any one file. Objects in GLACIER /
        DEEP_ARCHIVE aren't read (skipped='archived'). password opens password-protected PDFs. progress gets
        (files done, files).
        """
        started = time.monotonic()
        todo = list(enumerate(o for o in objects if not o.is_folder_marker))
        budget = _ReadBudget(parse_size(max_read))
        stop = threading.Event()
        done = [0]
        lock = threading.Lock()

        def work(job: tuple[int, ObjectInfo]) -> FileDetails:
            try:
                return self._describe_object(job[1], budget, password, stop)
            finally:
                with lock:
                    done[0] += 1

        def tick() -> None:
            if progress:
                progress(done[0], len(todo))

        tick()
        results: list[FileDetails | None] = [None] * len(todo)
        workers = min(max_workers, _pool_size(self.client))
        for (index, obj), details, error in _run_in_threads(work, todo, workers, tick, stop):
            if details is None:  # a bug in a parser: say so on that file rather than lose the report
                details = FileDetails(key=obj.key, size=obj.size, bucket=obj.bucket, storage_class=obj.storage_class,
                                      last_modified=obj.last_modified, error=_error_text(error or Exception()))
            results[index] = details
        tick()
        files = [d for d in results if d is not None]
        return FileDetailsReport(
            uri="",
            files=files,
            max_read=budget.limit,
            read_capped=any(d.skipped == "max_read" for d in files),
            bytes_read=budget.used,
            requests=budget.requests,
            seconds=time.monotonic() - started,
        )

    def _describe_object(
        self, obj: ObjectInfo, budget: _ReadBudget, password: str | None, stop: threading.Event
    ) -> FileDetails:
        d = FileDetails(key=obj.key, size=obj.size, bucket=obj.bucket, storage_class=obj.storage_class,
                        last_modified=obj.last_modified)
        if obj.size and obj.storage_class in ARCHIVE_CLASSES:
            d.format, d.compression = detect_format(obj.key)
            d.skipped = "archived"
            return d
        if obj.size and budget.exhausted():
            d.format, d.compression = detect_format(obj.key)
            d.skipped = "max_read"
            return d
        raw = _BlockReader(self.client, obj, budget, stop)
        try:
            with io.BufferedReader(raw, buffer_size=64 * KB) as handle:
                _describe_into(d, handle, password)
        except ClientError as exc:
            code = _error_code(exc)
            d.error = _UNREADABLE_REASONS.get(code, f"{code}: {exc.response.get('Error', {}).get('Message', '')}")
        except BotoCoreError as exc:
            d.error = f"{type(exc).__name__}: {exc}"
        d.bytes_read = raw.bytes_read
        if raw.stopped == "max_read" and not d.facts and not d.notes:
            d.skipped, d.error = "max_read", ""
        elif raw.stopped and d.error:  # what the parser said is about the cut-off file; this is why it was cut
            d.error = _stopped(raw.stopped)
        elif raw.stopped:
            d.notes.append(f"Some details are missing: reading {_stopped(raw.stopped)}.")
        return d

    # ------------------------------------------------------------------ objects

    def head(self, uri: str) -> dict[str, Any]:
        """
        Object metadata: size, type, storage class, encryption, version, restore/lock status, user metadata, tags.
        """
        bucket, key = parse_s3_uri(uri)
        resp = self.client.head_object(Bucket=bucket, Key=key)
        etag = resp.get("ETag", "").strip('"')
        info = {
            "uri": s3_uri(bucket, key),
            "size": resp.get("ContentLength"),
            "last_modified": resp.get("LastModified"),
            "content_type": resp.get("ContentType"),
            "content_encoding": resp.get("ContentEncoding"),
            "content_disposition": resp.get("ContentDisposition"),
            "cache_control": resp.get("CacheControl"),
            "storage_class": resp.get("StorageClass", "STANDARD"),
            "etag": etag,
            "multipart_parts": int(etag.rsplit("-", 1)[1]) if "-" in etag else None,
            "version_id": resp.get("VersionId"),
            "encryption": resp.get("ServerSideEncryption"),
            "kms_key": resp.get("SSEKMSKeyId"),
            "bucket_key_enabled": resp.get("BucketKeyEnabled"),
            "restore": resp.get("Restore"),
            "archive_status": resp.get("ArchiveStatus"),
            "replication_status": resp.get("ReplicationStatus"),
            "object_lock_mode": resp.get("ObjectLockMode"),
            "object_lock_retain_until": resp.get("ObjectLockRetainUntilDate"),
            "legal_hold": resp.get("ObjectLockLegalHoldStatus"),
            "lifecycle_expiration": resp.get("Expiration"),
        }
        info = {k: v for k, v in info.items() if v is not None}
        info["metadata"] = resp.get("Metadata", {})
        try:
            info["tags"] = self.object_tags(uri)
        except ClientError:
            info["tags"] = None  # no s3:GetObjectTagging permission
        return info

    def object_tags(self, uri: str) -> dict[str, str]:
        bucket, key = parse_s3_uri(uri)
        return {
            t["Key"]: t["Value"]
            for t in self.client.get_object_tagging(Bucket=bucket, Key=key).get(
                "TagSet", []
            )
        }

    def exists(self, uri: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def open(
        self, uri: str, *, decompress: bool = True, compression: str | None = None
    ) -> BinaryIO:
        """
        Streaming binary reader (use as a context manager). .gz / .bz2 / .xz / .zst are decompressed
        on the fly. compression overrides the codec guessed from the name ('gz', 'bz2', 'xz', 'zst'; '' = none).
        """
        bucket, key = parse_s3_uri(uri)
        codec = (
            (
                detect_format(key)[1]
                if compression is None
                else _CODEC_ALIASES.get(compression, compression)
            )
            if decompress
            else None
        )
        if codec and codec not in _DECOMPRESSORS:
            raise ValueError(
                f"Unknown compression {codec!r}; use one of {', '.join(_DECOMPRESSORS)}"
            )
        body = self.client.get_object(Bucket=bucket, Key=key)["Body"]
        stream = io.BufferedReader(_BodyReader(body), buffer_size=256 * KB)
        if not codec:
            return stream
        try:
            reader = _DECOMPRESSORS[codec](stream)
        except BaseException:
            stream.close()
            raise
        close_reader = reader.close

        def close() -> None:  # decompressors don't close a file object they were handed
            try:
                close_reader()
            finally:
                stream.close()

        try:
            reader.close = close
        except (
            AttributeError
        ):  # C-level readers (zstandard) close their source themselves
            pass
        return reader

    def read_bytes(
        self, uri: str, start: int | None = None, end: int | None = None
    ) -> bytes:
        """
        Raw bytes (not decompressed). start/end are inclusive byte offsets for a ranged GET.
        """
        bucket, key = parse_s3_uri(uri)
        extra = (
            {"Range": f"bytes={start or 0}-{'' if end is None else end}"}
            if start or end is not None
            else {}
        )
        return self.client.get_object(Bucket=bucket, Key=key, **extra)["Body"].read()

    def _read_head(
        self, uri: str, max_bytes: int, compression: str | None = None
    ) -> tuple[bytes, bool]:
        """
        First max_bytes of the (decompressed) object, and whether there was more.
        """
        with self.open(uri, compression=compression) as stream:
            data = stream.read(max_bytes + 1)
        return data[:max_bytes], len(data) > max_bytes

    def read_text(
        self,
        uri: str,
        *,
        max_bytes: int = MB,
        encoding: str = "utf-8",
        compression: str | None = None,
    ) -> str:
        """
        Decoded text of the first `max_bytes` (decompressed) bytes.
        """
        return self._read_head(uri, max_bytes, compression)[0].decode(
            encoding, errors="replace"
        )

    def read_lines(
        self,
        uri: str,
        n: int = 20,
        *,
        encoding: str = "utf-8",
        max_line_chars: int = 100_000,
        compression: str | None = None,
    ) -> list[str]:
        """
        First n lines; downloads only as much as needed.
        """
        lines: list[str] = []
        with io.TextIOWrapper(
            self.open(uri, compression=compression), encoding=encoding, errors="replace"
        ) as text:
            while len(lines) < n:
                line = text.readline(max_line_chars)
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        return lines

    def read_json(self, uri: str, *, compression: str | None = None) -> Any:
        """
        Parse a whole JSON document (reads the full object).
        """
        with self.open(uri, compression=compression) as stream:
            return json.load(stream)

    def read_jsonl(
        self,
        uri: str,
        n: int | None = None,
        *,
        encoding: str = "utf-8",
        compression: str | None = None,
    ) -> list[Any]:
        """
        Parse JSON-lines records; n limits how many (only that much is downloaded).
        """
        records: list[Any] = []
        with io.TextIOWrapper(
            self.open(uri, compression=compression), encoding=encoding
        ) as text:
            for line in text:
                if line.strip():
                    records.append(json.loads(line))
                    if n is not None and len(records) >= n:
                        break
        return records

    def read_avro(
        self, uri: str, n: int | None = None, *, compression: str | None = None
    ) -> list[Any]:
        """
        Records from an Avro container file (codecs null, deflate, bzip2, xz, zstandard; snappy needs
        python-snappy). n limits how many; then only the start of the file is downloaded.
        """
        return self._avro(uri, n, compression)[2]

    def _avro(
        self, uri: str, n: int | None, compression: str | None
    ) -> tuple[Any, str, list[Any], bool]:
        if n is None:
            with self.open(uri, compression=compression) as stream:
                return parse_avro(stream.read())
        window = MB
        while (
            True
        ):  # read more of the file until n records are decoded (or it's all read)
            data, more = self._read_head(uri, window, compression)
            schema, codec, records, _ = parsed = parse_avro(data, n)
            if len(records) >= n or not more:
                return schema, codec, records[:n], parsed[3] and not more
            window *= 4

    def read_npy(
        self, uri: str, *, nrows: int | None = None, compression: str | None = None
    ) -> Any:
        """
        NumPy .npy array (never unpickles). With nrows on an uncompressed file only those rows
        are downloaded.
        """
        np = _require("numpy", "Reading .npy")
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._random_access(bucket, key, codec, buffer_size=64 * KB) as handle:
            shape, fortran, dtype = _npy_header(handle)
            if dtype.hasobject:
                raise ValueError(
                    "This .npy holds Python objects (a pickle); not loaded, because unpickling can run code"
                )
            if nrows is None or fortran or not shape:
                handle.seek(0)
                array = _require("numpy.lib.format", "Reading .npy").read_array(
                    handle, allow_pickle=False
                )
                return array if nrows is None or not shape else array[:nrows]
            rows = min(nrows, shape[0])
            data = handle.read(rows * math.prod(shape[1:]) * dtype.itemsize)
            return np.frombuffer(data, dtype=dtype).reshape((rows, *shape[1:]))

    def read_df(
        self,
        uri: str,
        *,
        nrows: int | None = None,
        columns: list[str] | None = None,
        fmt: str | None = None,
        compression: str | None = None,
        **kwargs: Any,
    ):
        """
        Load a table into a pandas DataFrame: csv / tsv / psv / json / jsonl / parquet / orc /
        feather (arrow) / avro / excel (xlsx, xls) / npy, optionally .gz / .bz2 / .xz / .zst compressed.
        nrows reads just the first rows (parquet, orc and feather fetch only what they need).
        Extra kwargs go to pandas.read_csv (csv / tsv / psv) or pandas.read_excel (e.g. sheet_name=).
        """
        pd = _require("pandas", "Reading a table")
        bucket, key = parse_s3_uri(uri)
        guessed_fmt, guessed_codec = detect_format(key)
        fmt = fmt or guessed_fmt
        codec = guessed_codec if compression is None else compression
        if fmt in ("parquet", "orc", "arrow"):
            with self._random_access(bucket, key, codec) as handle:
                return _read_arrow_table(fmt, handle, nrows, columns).to_pandas()
        if fmt == "excel":
            with self._random_access(bucket, key, codec) as handle:
                return pd.read_excel(handle, nrows=nrows, usecols=columns, **kwargs)
        if fmt in _CSV_SEPARATORS:
            kwargs.setdefault("sep", _CSV_SEPARATORS[fmt])
            with self.open(uri, compression=codec or "") as stream:
                return pd.read_csv(stream, nrows=nrows, usecols=columns, **kwargs)
        if fmt == "npy":
            array = self.read_npy(uri, nrows=nrows, compression=codec or "")
            if array.ndim > 2:
                raise ValueError(
                    f"A {array.ndim}-dimensional array doesn't fit in a table; use read_npy"
                )
            frame = pd.DataFrame(array)
            return frame[columns] if columns else frame
        if fmt in ("json", "jsonl", "avro"):
            if fmt == "jsonl":
                records = self.read_jsonl(uri, n=nrows, compression=codec or "")
            elif fmt == "avro":
                records = self.read_avro(uri, n=nrows, compression=codec or "")
            else:
                records = self.read_json(uri, compression=codec or "")
            records = records if isinstance(records, list) else [records]
            records = records if nrows is None else records[:nrows]
            if records and all(isinstance(r, dict) for r in records):
                frame = pd.json_normalize(records)
            else:
                frame = pd.DataFrame({"value": records})
            return frame[columns] if columns else frame
        raise ValueError(
            f"Can't tell how to read {key!r} as a table; pass fmt='csv'|'tsv'|'psv'|'json'|'jsonl'|"
            "'parquet'|'orc'|'arrow'|'avro'|'excel'|'npy'"
        )

    def _seekable(
        self, bucket: str, key: str, *, buffer_size: int = MB
    ) -> io.BufferedReader:
        size = self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        return io.BufferedReader(
            _RangeReader(self.client, bucket, key, size), buffer_size=buffer_size
        )

    def _random_access(
        self, bucket: str, key: str, codec: str | None = None, *, buffer_size: int = MB
    ) -> io.BufferedIOBase:
        """
        Seekable reader: ranged GETs, or (for a compressed object) the whole thing decompressed in memory.
        """
        if not codec:
            return self._seekable(bucket, key, buffer_size=buffer_size)
        with self.open(s3_uri(bucket, key), compression=codec) as stream:
            return io.BytesIO(stream.read())

    def parquet_info(self, uri: str) -> dict[str, Any]:
        """
        Row count, row groups, schema and compression from the parquet footer (a few KB downloaded).
        """
        pq = _require("pyarrow.parquet", "Reading parquet")
        bucket, key = parse_s3_uri(uri)
        with self._seekable(bucket, key) as handle:
            parquet = pq.ParquetFile(handle)
            meta = parquet.metadata
            return {
                "rows": meta.num_rows,
                "row_groups": meta.num_row_groups,
                "columns": [(f.name, str(f.type)) for f in parquet.schema_arrow],
                "compression": meta.row_group(0).column(0).compression
                if meta.num_row_groups and meta.num_columns
                else None,
                "created_by": meta.created_by,
            }

    def list_archive(
        self,
        uri: str,
        *,
        limit: int = 1000,
        max_bytes: int = 256 * MB,
        compression: str | None = None,
    ) -> ArchiveListing:
        """
        Files inside a .zip / .tar / .tar.gz / .tgz (e.g. a SageMaker model.tar.gz) without extracting it.
        A zip's index sits at its end, so only that is downloaded. A compressed tar has no index: it's
        streamed from the start and the listing stops after `max_bytes` of it (complete=False).
        """
        bucket, key = parse_s3_uri(uri)
        fmt, codec = detect_format(key)
        codec = codec if compression is None else compression
        if fmt not in ("zip", "tar", "npz", "torch", "excel"):
            fmt = sniff_format(self.read_bytes(uri, 0, 511))[0] or "tar"
        listing = ArchiveListing(
            uri=s3_uri(bucket, key), kind="tar" if fmt == "tar" else "zip"
        )
        if listing.kind == "zip":
            with (
                self._random_access(bucket, key, codec, buffer_size=256 * KB) as handle,
                zipfile.ZipFile(handle) as archive,
            ):
                infos = archive.infolist()
            listing.total_files = len(infos)
            listing.entries = [
                ArchiveEntry(
                    i.filename, i.file_size, _zip_time(i.date_time), i.is_dir()
                )
                for i in infos[:limit]
            ]
            listing.complete = len(infos) <= limit
            return listing
        raw = None
        if codec:
            raw = _BodyReader(self.client.get_object(Bucket=bucket, Key=key)["Body"])
            stream = _DECOMPRESSORS[codec](io.BufferedReader(raw, buffer_size=256 * KB))
        else:  # plain tar: seek from header to header instead of downloading the contents
            stream = self._seekable(bucket, key, buffer_size=64 * KB)
        try:
            with tarfile.open(fileobj=stream, mode="r|" if codec else "r:") as archive:
                for member in archive:
                    if len(listing.entries) >= limit or (
                        raw is not None and raw.bytes_read > max_bytes
                    ):
                        listing.complete = False
                        break
                    listing.entries.append(
                        ArchiveEntry(
                            member.name,
                            member.size,
                            datetime.fromtimestamp(member.mtime, timezone.utc),
                            member.isdir(),
                        )
                    )
        finally:
            stream.close()
            if raw is not None:
                listing.bytes_read = raw.bytes_read
                raw.close()
        listing.total_files = len(listing.entries) if listing.complete else None
        return listing

    def safetensors_info(self, uri: str) -> dict[str, Any]:
        """
        Tensor names, dtypes and shapes plus metadata from a .safetensors header (only the header is read).
        """
        length = _safetensors_length(self.read_bytes(uri, 0, 7))
        return _parse_safetensors(self.read_bytes(uri, 8, 8 + length - 1))

    def _open_document(
        self, bucket: str, key: str, codec: str | None, *, whole_under: int = 64 * MB
    ) -> io.BufferedIOBase:
        """
        Seekable handle for a document: the whole object in memory when it's small (or compressed),
        ranged GETs for bigger ones.
        """
        if codec:
            return self._random_access(bucket, key, codec)
        size = self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        if size <= whole_under:
            return io.BytesIO(self.read_bytes(s3_uri(bucket, key)))
        return io.BufferedReader(
            _RangeReader(self.client, bucket, key, size), buffer_size=MB
        )

    def read_pdf(
        self,
        uri: str,
        *,
        pages: Iterable[int] | None = None,
        password: str | None = None,
        pictures: bool | None = False,
        max_pictures: int | None = None,
        compression: str | None = None,
        layout: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> Document:
        """
        Text of a PDF, one part per page (needs pypdf). pages: 1-based page numbers, e.g. [1, 2] or
        range(1, 11); only those are read. Scanned pages have no text layer and come back empty.
        pictures=True also draws each page read into doc.pictures, None only the pages with no text (scans);
        drawing needs pypdfium2 and pillow, and what stops it goes in doc.errors['pictures'] instead of
        raising. max_pictures: draw at most this many. layout=True also keeps where each line sits, its size
        and weight (doc.layout), for pdf_flow(doc): the text as headings, paragraphs and lists. progress gets
        (pages drawn, pages to draw), or with pictures=False (pages read, pages to read).
        """
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(
            bucket, key, codec, whole_under=256 * MB if pages is None else 16 * MB
        ) as handle:
            doc = parse_pdf(handle, s3_uri(bucket, key), pages=pages, password=password, layout=layout,
                            progress=progress if pictures is False else None)
            if pictures is not False:
                drawn = [n for n, text in zip(doc.numbers, doc.parts) if pictures or not text.strip()]
                try:
                    handle.seek(0)
                    doc.pictures = render_pdf_pages(
                        handle, drawn, password=password, max_pages=max_pictures, progress=progress
                    ) if drawn else []
                except _READ_ERRORS as exc:
                    doc.errors["pictures"] = str(exc)
            return doc

    def render_pdf(
        self,
        uri: str,
        pages: Iterable[int] | None = None,
        *,
        width: int = 1000,
        password: str | None = None,
        compression: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[Picture]:
        """
        Pictures of a PDF's pages as they print, scanned pages included (needs pypdfium2 and pillow).
        pages: 1-based page numbers, e.g. [1, 2] (default: every page). Each Picture shows itself in a
        notebook; .data holds its PNG / JPEG bytes. progress gets (pages drawn, pages to draw).
        """
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(
            bucket, key, codec, whole_under=256 * MB if pages is None else 16 * MB
        ) as handle:
            return render_pdf_pages(handle, pages, width=width, password=password, progress=progress)

    def read_docx(
        self, uri: str, *, pictures: bool = False, compression: str | None = None
    ) -> Document:
        """
        Text of a Word .docx: paragraphs and tables in order, headings, title and author. pictures=True
        also loads its pictures, in reading order (doc.pictures). No packages needed.
        """
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(bucket, key, codec) as handle:
            return parse_docx(handle, s3_uri(bucket, key), pictures=pictures)

    def read_pptx(
        self,
        uri: str,
        *,
        slides: Iterable[int] | None = None,
        compression: str | None = None,
    ) -> Document:
        """
        Text of a PowerPoint .pptx, one part per slide, with slide titles, tables and speaker notes.
        slides: 1-based slide numbers to read. No packages needed.
        """
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(bucket, key, codec) as handle:
            return parse_pptx(handle, s3_uri(bucket, key), slides=slides)

    def read_document(
        self,
        uri: str,
        *,
        pages: Iterable[int] | None = None,
        password: str | None = None,
        pictures: bool | None = False,
        max_pictures: int | None = None,
        compression: str | None = None,
        layout: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> Document:
        """
        Text of a PDF, Word .docx or PowerPoint .pptx, told apart by name or content. pages: page numbers
        (PDF) or slide numbers (PPTX). doc.text is everything; doc.parts has one entry per page / slide.
        pictures: for a PDF, True draws every page read and None the pages without text (see read_pdf);
        for a Word file, True or None loads its pictures. False (the default) reads text only. layout=True
        keeps a PDF's lines with where they sit (see read_pdf and pdf_flow).
        """
        bucket, key = parse_s3_uri(uri)
        fmt, codec = detect_format(key)
        codec = codec if compression is None else compression
        if fmt not in ("pdf", "docx", "pptx", "oldoffice"):
            head = self._read_head(uri, 512, codec or "")[0]
            fmt = sniff_format(head)[0]
            if fmt == "zip":
                with self._open_document(bucket, key, codec) as handle:
                    fmt = office_kind(handle) or "zip"
        if fmt == "pdf":
            return self.read_pdf(
                uri,
                pages=pages,
                password=password,
                pictures=pictures,
                max_pictures=max_pictures,
                compression=codec or "",
                layout=layout,
                progress=progress,
            )
        if fmt == "pptx":
            return self.read_pptx(uri, slides=pages, compression=codec or "")
        if fmt == "docx":
            if pages is not None:
                raise ValueError(
                    "A .docx has no fixed pages; read it whole and use doc.parts"
                )
            return self.read_docx(uri, pictures=pictures is not False, compression=codec or "")
        if fmt == "oldoffice":
            raise ValueError(_old_office_note(key))
        raise ValueError(f"{key!r} isn't a PDF, Word .docx or PowerPoint .pptx file")

    def presigned_url(self, uri: str, *, expires: int = 3600, inline: bool = False) -> str:
        """
        Temporary HTTPS link to download the object without AWS credentials. inline=True makes it open in a
        browser tab instead: S3 sends a PDF, picture, sound, video or text file as a type the browser shows,
        whatever type it was stored with (other files still download).
        """
        bucket, key = parse_s3_uri(uri)
        params = {"Bucket": bucket, "Key": key}
        if inline:
            params["ResponseContentDisposition"] = "inline"
            shown = _browser_type(key)
            if shown:
                params["ResponseContentType"] = shown
        return self._client_for(bucket).generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires
        )
