"""
S3Analyzer's reads about versions, deleted files and unfinished uploads.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any, Callable

from ..._kit.fmt import Stat, parse_time

from ..helpers import parse_s3_uri, s3_uri
from ..models import DeletedFiles, DeletedObject, MultipartUpload, ObjectVersion, VersionStats
from ..analysis.cost import object_monthly_cost


class _VersionReads:
    """
    S3Analyzer's reads about versions: how many there are and what they cost, deleted files you can still
    restore, one key's history, and multipart uploads left unfinished.
    """

    def version_stats(
        self,
        uri: str,
        *,
        top_n: int = 10,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> VersionStats:
        """
        Current vs noncurrent versions and delete markers - the hidden cost of versioned buckets.
        """
        bucket, prefix = parse_s3_uri(uri)
        stats = VersionStats(uri=s3_uri(bucket, prefix))
        noncurrent_by_key: dict[str, Stat] = defaultdict(Stat)
        seen = 0
        for page in self.client.get_paginator("list_object_versions").paginate(
            Bucket=bucket, Prefix=prefix
        ):
            for version in page.get("Versions", []):
                if version["IsLatest"]:
                    stats.current.add(version["Size"])
                else:
                    stats.noncurrent.add(version["Size"])
                    noncurrent_by_key[version["Key"]].add(version["Size"])
                    stats.noncurrent_cost += (
                        object_monthly_cost(
                            version["Size"],
                            version.get("StorageClass", "STANDARD"),
                            self.prices,
                        )
                        or 0.0
                    )
            for marker in page.get("DeleteMarkers", []):
                stats.delete_markers += 1
                stats.deleted_keys += marker["IsLatest"]
            seen += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
            if progress:
                progress(seen)
            if limit is not None and seen >= limit:
                stats.truncated = bool(page.get("IsTruncated"))
                break
        stats.top_noncurrent = heapq.nlargest(
            top_n, noncurrent_by_key.items(), key=lambda kv: kv[1].size
        )
        return stats

    def deleted_files(
        self,
        uri: str,
        *,
        deleted_after: Any = None,
        limit: int | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> DeletedFiles:
        """
        Keys under `uri` whose latest version is a delete marker (versioned buckets). Deleting the
        marker (s3:DeleteObjectVersion) brings back `last_version`. deleted_after: datetime, '2024-05-01' or '7d'.
        """
        bucket, prefix = parse_s3_uri(uri)
        since = parse_time(deleted_after)
        result = DeletedFiles(uri=s3_uri(bucket, prefix))
        markers: dict[str, dict] = {}
        newest: dict[str, ObjectVersion] = {}
        kept: dict[str, Stat] = defaultdict(Stat)
        cost: dict[str, float] = defaultdict(float)
        seen = 0
        for page in self.client.get_paginator("list_object_versions").paginate(
            Bucket=bucket, Prefix=prefix
        ):
            markers.update(
                (m["Key"], m) for m in page.get("DeleteMarkers", []) if m["IsLatest"]
            )
            for v in page.get("Versions", []):
                if v["IsLatest"]:
                    continue
                key, storage_class = v["Key"], v.get("StorageClass", "STANDARD")
                kept[key].add(v["Size"])
                cost[key] += (
                    object_monthly_cost(v["Size"], storage_class, self.prices) or 0.0
                )
                if key not in newest or v["LastModified"] > newest[key].last_modified:
                    newest[key] = ObjectVersion(
                        key,
                        v["VersionId"],
                        False,
                        v["LastModified"],
                        v["Size"],
                        storage_class=storage_class,
                    )
            seen += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
            if progress:
                progress(seen)
            if limit is not None and seen >= limit:
                result.truncated = bool(page.get("IsTruncated"))
                break
        result.files = sorted(
            (
                DeletedObject(
                    key,
                    m["LastModified"],
                    m["VersionId"],
                    newest.get(key),
                    kept.get(key, Stat()),
                    cost[key],
                )
                for key, m in markers.items()
                if since is None or m["LastModified"] >= since
            ),
            key=lambda d: d.deleted,
            reverse=True,
        )
        return result

    def object_versions(self, uri: str) -> list[ObjectVersion]:
        """
        Full version history of one key, newest first (includes delete markers).
        """
        bucket, key = parse_s3_uri(uri)
        history: list[ObjectVersion] = []
        for page in self.client.get_paginator("list_object_versions").paginate(
            Bucket=bucket, Prefix=key
        ):
            history += [
                ObjectVersion(
                    key,
                    v["VersionId"],
                    v["IsLatest"],
                    v["LastModified"],
                    v["Size"],
                    storage_class=v.get("StorageClass"),
                )
                for v in page.get("Versions", [])
                if v["Key"] == key
            ]
            history += [
                ObjectVersion(
                    key,
                    m["VersionId"],
                    m["IsLatest"],
                    m["LastModified"],
                    is_delete_marker=True,
                )
                for m in page.get("DeleteMarkers", [])
                if m["Key"] == key
            ]
        return sorted(
            history, key=lambda v: (v.last_modified, v.is_latest), reverse=True
        )

    def incomplete_uploads(
        self,
        uri: str,
        *,
        with_sizes: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> list[MultipartUpload]:
        """
        Multipart uploads that were started but never completed/aborted - their parts are billed
        but invisible in normal listings. with_sizes=True adds one ListParts call per upload.
        """
        bucket, prefix = parse_s3_uri(uri)
        uploads: list[MultipartUpload] = []
        for page in self.client.get_paginator("list_multipart_uploads").paginate(
            Bucket=bucket, Prefix=prefix
        ):
            for item in page.get("Uploads", []):
                upload = MultipartUpload(
                    bucket,
                    item["Key"],
                    item["UploadId"],
                    item["Initiated"],
                    item.get("StorageClass", "STANDARD"),
                )
                if with_sizes:
                    parts = [
                        part
                        for p in self.client.get_paginator("list_parts").paginate(
                            Bucket=bucket, Key=item["Key"], UploadId=item["UploadId"]
                        )
                        for part in p.get("Parts", [])
                    ]
                    upload.parts, upload.size = (
                        len(parts),
                        sum(part["Size"] for part in parts),
                    )
                uploads.append(upload)
                if progress and with_sizes:
                    progress(len(uploads))
        return sorted(uploads, key=lambda u: u.initiated)
