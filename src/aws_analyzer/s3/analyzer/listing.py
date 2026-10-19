"""
S3Analyzer's listings, and the hasher that tells duplicates apart.
"""

from __future__ import annotations

import hashlib
import heapq
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Iterable, Iterator

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import parse_size

from ..helpers import MB, parse_s3_uri, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..models import CompareResult, DuplicateReport, FolderTree, LifecycleImpact, Listing, ObjectInfo, PrefixSummary
from ..analysis.summary import build_folder_tree, compare_objects, make_filter, summarize_objects
from ..analysis.duplicates import DUPLICATE_METHODS, HASH_HEAD_BYTES, files_to_hash, group_duplicates
from ..analysis.lifecycle import simulate_lifecycle_objects
from .threads import _pool_size, _run_in_threads


class _ContentHasher:
    """
    Reads files for find_duplicates, in parallel: the SHA-256 of each file's first HASH_HEAD_BYTES, then of
    the rest where those match another file's. Stops planning reads at `budget` bytes. Progress is reported
    from the calling thread only, so a notebook progress bar is never touched from a worker thread.
    """

    def __init__(
        self,
        client: Any,
        budget: int | None,
        max_workers: int,
        progress: Callable[[int, int], None] | None,
    ):
        self.client, self.budget, self.progress = client, budget, progress
        self.workers = max(1, min(max_workers, _pool_size(client)))
        self.hashes: dict[str, str] = {}  # uri -> SHA-256 of the whole file
        self.distinct: set[str] = (
            set()
        )  # uris whose first bytes differ from every other file of their size
        self.unreadable: dict[str, str] = {}  # key -> storage class or error code
        self.files_read = self.bytes_read = self.requests = self.planned = 0
        self._phase: tuple[int, int] = (
            0,
            0,
        )  # (bytes read before this phase, bytes this phase will read)
        self.capped = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _fits(self, size: int) -> bool:
        if self.budget is not None and self.planned + size > self.budget:
            self.capped = True
            return False
        self.planned += size
        return True

    def run(self, plan: list[list[ObjectInfo]]) -> None:
        heads: list[list[ObjectInfo]] = []
        for files in plan:
            readable = [o for o in files if o.storage_class not in ARCHIVE_CLASSES]
            self.unreadable.update(
                (o.key, o.storage_class)
                for o in files
                if o.storage_class in ARCHIVE_CLASSES
            )
            if len(readable) > 1 and self._fits(
                sum(min(o.size, HASH_HEAD_BYTES) for o in readable)
            ):
                heads.append(readable)
        started = self._read_all(
            [(o, 0, hashlib.sha256()) for files in heads for o in files],
            sum(min(o.size, HASH_HEAD_BYTES) for files in heads for o in files),
        )

        rest: list[list[ObjectInfo]] = []
        for files in heads:
            by_start: dict[str, list[ObjectInfo]] = defaultdict(list)
            for obj in files:
                if obj.uri in started:
                    by_start[started[obj.uri].hexdigest()].append(obj)
            for digest, same in by_start.items():
                if (
                    same[0].size <= HASH_HEAD_BYTES
                ):  # the first bytes were the whole file
                    self.hashes.update((o.uri, digest) for o in same)
                elif len(same) == 1:
                    self.distinct.add(same[0].uri)
                else:
                    rest.append(same)
        rest.sort(key=lambda same: same[0].size * (len(same) - 1), reverse=True)
        todo = [
            (o, HASH_HEAD_BYTES, started[o.uri])
            for same in rest
            if self._fits(sum(o.size - HASH_HEAD_BYTES for o in same))
            for o in same
        ]
        whole = self._read_all(todo, sum(o.size - start for o, start, _ in todo))
        self.hashes.update((uri, hasher.hexdigest()) for uri, hasher in whole.items())

    def _read_all(
        self, todo: list[tuple[ObjectInfo, int, Any]], size: int
    ) -> dict[str, Any]:
        """
        Read every (object, from byte, hasher) in parallel -> {uri: hasher}; failures go to `unreadable`.
        Progress counts this phase's `size` bytes from 0.
        """
        done_hashers: dict[str, Any] = {}
        if not todo:
            return done_hashers
        self._phase = (self.bytes_read, size)
        self._report()
        for (obj, _, _), hasher, error in _run_in_threads(
            self._read, todo, self.workers, self._report, self._stop
        ):
            if isinstance(error, ClientError):
                self.unreadable[obj.key] = _error_code(error)
            elif isinstance(error, BotoCoreError):
                self.unreadable[obj.key] = type(error).__name__
            elif error is not None:
                raise error
            else:
                done_hashers[obj.uri] = hasher
        return done_hashers

    def _read(self, job: tuple[ObjectInfo, int, Any]) -> Any:
        obj, start, hasher = job
        if obj.size == 0:
            return hasher
        end = min(obj.size, HASH_HEAD_BYTES) - 1 if start == 0 else obj.size - 1
        match = (
            {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}
        )  # the file listed, not one written since
        body = self.client.get_object(
            Bucket=obj.bucket, Key=obj.key, Range=f"bytes={start}-{end}", **match
        )["Body"]
        with self._lock:
            self.requests += 1
            self.files_read += start == 0
        try:
            while not self._stop.is_set():
                chunk = body.read(MB)
                if not chunk:
                    break
                hasher.update(chunk)
                with self._lock:
                    self.bytes_read += len(chunk)
        finally:
            body.close()
        return hasher

    def _report(self) -> None:
        if self.progress:
            self.progress(self.bytes_read - self._phase[0], self._phase[1])


class _ListingReads:
    """
    S3Analyzer's listings: the objects under a prefix, one level or all of them, and what's worked out from
    them (summary, folder tree, find, duplicates, compare, a lifecycle rule's effect).
    """

    # ------------------------------------------------------------------ listing

    def iter_objects(
        self,
        uri: str,
        *,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> Iterator[ObjectInfo]:
        """
        Stream every object under `uri` (recursive), 1000 per request, in key order.
        """
        bucket, prefix = parse_s3_uri(uri)
        seen = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix
        ):
            for item in page.get("Contents", []):
                yield ObjectInfo(
                    bucket,
                    item["Key"],
                    item["Size"],
                    item["LastModified"],
                    item.get("StorageClass", "STANDARD"),
                    item.get("ETag", "").strip('"'),
                )
                seen += 1
                if limit is not None and seen >= limit:
                    return
            if progress:
                progress(seen)

    def list_objects(self, uri: str, *, limit: int | None = None) -> list[ObjectInfo]:
        return list(self.iter_objects(uri, limit=limit))

    def ls(self, uri: str, *, limit: int = 1000) -> Listing:
        """
        Sub-folders and files directly under `uri` (one level). Fast even on huge buckets.
        """
        bucket, prefix = parse_s3_uri(uri)
        listing = self._ls(bucket, prefix, limit)
        # 's3://b/data' almost always means the folder 'data/' - follow it when that's the only match
        if (
            prefix
            and not prefix.endswith("/")
            and not listing.objects
            and listing.folders == [prefix + "/"]
        ):
            listing = self._ls(bucket, prefix + "/", limit)
        return listing

    def _ls(self, bucket: str, prefix: str, limit: int) -> Listing:
        listing = Listing(uri=s3_uri(bucket, prefix))
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix, Delimiter="/"
        )
        for page in pages:
            listing.folders += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
            listing.objects += [
                ObjectInfo(
                    bucket,
                    o["Key"],
                    o["Size"],
                    o["LastModified"],
                    o.get("StorageClass", "STANDARD"),
                    o.get("ETag", "").strip('"'),
                )
                for o in page.get("Contents", [])
                if o["Key"] != prefix
            ]
            found = len(listing.folders) + len(listing.objects)
            if found >= limit:
                listing.truncated = bool(page.get("IsTruncated")) or found > limit
                break
        listing.folders = listing.folders[:limit]
        listing.objects = listing.objects[: limit - len(listing.folders)]
        return listing

    # ----------------------------------------------------------------- analysis

    def summarize(
        self,
        uri: str,
        *,
        top_n: int = 10,
        folder_depth: int = 1,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> PrefixSummary:
        """
        One pass over the prefix: totals, file types, storage classes, folders, size/age histograms,
        largest, and estimated monthly storage cost.
        """
        scan = self.iter_objects(
            uri, limit=None if limit is None else limit + 1, progress=progress
        )
        return summarize_objects(
            scan,
            uri,
            top_n=top_n,
            folder_depth=folder_depth,
            limit=limit,
            prices=self.prices,
        )

    def folder_tree(
        self,
        uri: str,
        *,
        depth: int = 2,
        limit: int | None = None,
        files: int = 0,
        progress: Callable[[int], None] | None = None,
    ) -> FolderTree:
        """
        Object count and size for every folder down to `depth` levels, plus up to `files` files per folder.
        """
        scan = self.iter_objects(
            uri, limit=None if limit is None else limit + 1, progress=progress
        )
        return build_folder_tree(scan, uri, depth=depth, limit=limit, files=files)

    def find(
        self,
        uri: str,
        *,
        pattern: str | None = None,
        regex: str | None = None,
        extensions: str | Iterable[str] | None = None,
        min_size: int | str | None = None,
        max_size: int | str | None = None,
        modified_after: Any = None,
        modified_before: Any = None,
        storage_classes: str | Iterable[str] | None = None,
        limit: int | None = None,
        scan_limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> list[ObjectInfo]:
        """
        Objects matching every given filter (see make_filter). Stops after `limit` matches
        or after scanning `scan_limit` keys.
        """
        keep = make_filter(
            pattern=pattern,
            regex=regex,
            extensions=extensions,
            min_size=min_size,
            max_size=max_size,
            modified_after=modified_after,
            modified_before=modified_before,
            storage_classes=storage_classes,
        )
        matches: list[ObjectInfo] = []
        for obj in self.iter_objects(uri, limit=scan_limit, progress=progress):
            if keep(obj):
                matches.append(obj)
                if limit is not None and len(matches) >= limit:
                    break
        return matches

    def largest(
        self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None
    ) -> list[ObjectInfo]:
        return heapq.nlargest(n, self._files(uri, progress), key=lambda o: o.size)

    def newest(
        self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None
    ) -> list[ObjectInfo]:
        return heapq.nlargest(
            n, self._files(uri, progress), key=lambda o: o.last_modified
        )

    def oldest(
        self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None
    ) -> list[ObjectInfo]:
        return heapq.nsmallest(
            n, self._files(uri, progress), key=lambda o: o.last_modified
        )

    def _files(
        self, uri: str, progress: Callable[[int], None] | None
    ) -> Iterator[ObjectInfo]:
        return (
            o
            for o in self.iter_objects(uri, progress=progress)
            if not o.is_folder_marker
        )

    def find_duplicates(
        self,
        uri: str,
        *,
        min_size: int | str = 1,
        method: str = "hash",
        max_read: int | str | None = "10GB",
        limit: int | None = None,
        max_workers: int = 16,
        progress: Callable[[int], None] | None = None,
        read_progress: Callable[[int, int], None] | None = None,
    ) -> DuplicateReport:
        """
        Identical files under `uri`, with what their copies take and cost.

        Files are grouped by size (a file no other file matches in size can't be a copy), then by ETag.
        method='hash' (the default) also reads files that share a size but not an ETag, because copies uploaded
        in parts of another size or encrypted with SSE-KMS get a different ETag: their first 64 KB first, and
        the whole file only where those match, hashed with SHA-256. method='strict' reads every file that shares
        its size, to prove each group byte for byte; 'etag' reads nothing. Reads stop at `max_read` bytes
        (None = no cap), biggest possible saving first. read_progress gets (bytes read, bytes to read) for
        each of the two passes, counting from 0 in each.
        """
        if method not in DUPLICATE_METHODS:
            raise ValueError(
                f"method must be one of {', '.join(map(repr, DUPLICATE_METHODS))}"
            )
        budget = parse_size(max_read)
        bucket, prefix = parse_s3_uri(uri)
        started = time.monotonic()
        objects = list(
            self.iter_objects(
                uri, limit=None if limit is None else limit + 1, progress=progress
            )
        )
        truncated = limit is not None and len(objects) > limit
        if truncated:
            del objects[limit:]
        reader = _ContentHasher(self.client, budget, max_workers, read_progress)
        reader.run(files_to_hash(objects, min_size=min_size, method=method))
        report = group_duplicates(
            objects,
            s3_uri(bucket, prefix),
            hashes=reader.hashes,
            distinct=reader.distinct,
            min_size=min_size,
            prices=self.prices,
        )
        report.method, report.max_read, report.truncated = method, budget, truncated
        report.unreadable, report.read_capped = reader.unreadable, reader.capped
        report.files_read, report.bytes_read, report.requests = (
            reader.files_read,
            reader.bytes_read,
            reader.requests,
        )
        if report.groups:
            try:
                report.versioning = self.versioning_status(bucket)
            except (ClientError, BotoCoreError):
                pass
        report.scan_seconds = time.monotonic() - started
        return report

    def compare(
        self, uri_a: str, uri_b: str, *, progress: Callable[[int], None] | None = None
    ) -> CompareResult:
        """
        Diff two prefixes (e.g. a copy/sync source and target) by relative key, size and ETag.
        """
        bucket_a, prefix_a = parse_s3_uri(uri_a)
        bucket_b, prefix_b = parse_s3_uri(uri_b)
        seen = [
            0,
            0,
        ]  # keys listed on each side, so the progress count keeps going up across both listings

        def side(i: int) -> Callable[[int], None] | None:
            def tick(count: int) -> None:
                seen[i] = count
                if progress:
                    progress(sum(seen))

            return tick if progress else None

        return compare_objects(
            self.iter_objects(uri_a, progress=side(0)),
            self.iter_objects(uri_b, progress=side(1)),
            prefix_a=prefix_a,
            prefix_b=prefix_b,
            uri_a=s3_uri(bucket_a, prefix_a),
            uri_b=s3_uri(bucket_b, prefix_b),
        )

    def simulate_lifecycle(
        self,
        uri: str,
        *,
        move_after: int | dict[int, str] | None = None,
        to: str | None = None,
        delete_after: int | None = None,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> LifecycleImpact:
        """
        What a lifecycle rule on `uri` would move or delete if it ran today, and the cost before and after.
        See simulate_lifecycle_objects for the arguments.
        """
        scan = self.iter_objects(
            uri, limit=None if limit is None else limit + 1, progress=progress
        )
        return simulate_lifecycle_objects(
            scan,
            uri,
            move_after=move_after,
            to=to,
            delete_after=delete_after,
            prices=self.prices,
            limit=limit,
        )
