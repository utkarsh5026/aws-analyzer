"""
What's under a prefix: sizes, ages and storage classes, the folder tree, filters, two
prefixes compared, and the findings about them.
"""

from __future__ import annotations

import fnmatch
import heapq
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable

from ..._kit.fmt import Stat, _band, _plural, _utcnow, human_money, human_size, parse_size, parse_time

from ..helpers import GB, KB, MB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..prices import ARCHIVE_CLASSES, MIN_BILLABLE_SIZE, S3_PRICES
from ..models import CompareResult, FolderTree, ObjectInfo, PrefixSummary
from .cost import object_monthly_cost


SIZE_BANDS: list[tuple[str, int | None]] = [  # (label, exclusive upper bound in bytes)
    ("0 B (empty)", 1),
    ("< 1 KB", KB),
    ("1 KB - 1 MB", MB),
    ("1 - 10 MB", 10 * MB),
    ("10 - 100 MB", 100 * MB),
    ("100 MB - 1 GB", GB),
    ("1 - 5 GB", 5 * GB),
    ("> 5 GB", None),
]

AGE_BANDS: list[
    tuple[str, int | None]
] = [  # (label, exclusive upper bound in days since modified)
    ("< 1 day", 1),
    ("1 - 7 days", 7),
    ("1 - 4 weeks", 30),
    ("1 - 3 months", 90),
    ("3 - 12 months", 365),
    ("1 - 3 years", 3 * 365),
    ("> 3 years", None),
]


def _by_size(stats: dict[str, Stat]) -> dict[str, Stat]:
    return dict(sorted(stats.items(), key=lambda kv: kv[1].size, reverse=True))


def folder_of(key: str, base: str, depth: int = 1) -> str:
    """
    Folder of `key` relative to `base`, at most `depth` levels: ('a/b/c/f.csv', 'a/', 1) -> 'b/'.
    Returns '' for files directly under `base`.
    """
    parts = relative_key(key, base).split("/")[:-1][:depth]
    return "/".join(parts) + "/" if parts else ""


def summarize_objects(
    objects: Iterable[ObjectInfo],
    uri: str = "",
    *,
    top_n: int = 10,
    folder_depth: int = 1,
    limit: int | None = None,
    now: datetime | None = None,
    prices: dict[str, float] | None = None,
) -> PrefixSummary:
    """
    One streaming pass over `objects` -> PrefixSummary (counts, sizes, histograms, top-N, cost).
    """
    now = now or _utcnow()
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    base = base_prefix(prefix)
    summary = PrefixSummary(uri=s3_uri(bucket, prefix) if bucket else uri)
    size_hist = {label: Stat() for label, _ in SIZE_BANDS}
    age_hist = {label: Stat() for label, _ in AGE_BANDS}
    by_ext: dict[str, Stat] = defaultdict(Stat)
    by_class: dict[str, Stat] = defaultdict(Stat)
    by_folder: dict[str, Stat] = defaultdict(Stat)
    cost_by_class: dict[str, float] = defaultdict(float)
    below_minimum: dict[str, Stat] = defaultdict(Stat)
    largest: list[tuple[int, int, ObjectInfo]] = []  # min-heap of the top_n biggest
    started = time.monotonic()

    for i, obj in enumerate(objects):
        if limit is not None and i >= limit:
            summary.truncated = True
            break
        if obj.is_folder_marker:
            summary.folder_markers += 1
            continue
        size = obj.size
        age_days = (now - obj.last_modified).total_seconds() / 86400
        summary.object_count += 1
        summary.total_size += size
        summary.max_size = max(summary.max_size, size)
        summary.min_size = (
            size if summary.min_size is None else min(summary.min_size, size)
        )
        summary.empty_count += size == 0
        if summary.oldest is None or obj.last_modified < summary.oldest.last_modified:
            summary.oldest = obj
        if summary.newest is None or obj.last_modified > summary.newest.last_modified:
            summary.newest = obj
        size_hist[_band(size, SIZE_BANDS)].add(size)
        age_hist[_band(age_days, AGE_BANDS)].add(size)
        by_ext[obj.extension].add(size)
        by_class[obj.storage_class].add(size)
        by_folder[folder_of(obj.key, base, folder_depth)].add(size)
        if obj.storage_class == "STANDARD" and age_days > 90:
            summary.cold_standard.add(size)
        cost = object_monthly_cost(size, obj.storage_class, prices)
        if cost is not None:
            cost_by_class[obj.storage_class] += cost
        if size < MIN_BILLABLE_SIZE.get(obj.storage_class, 0):
            below_minimum[obj.storage_class].add(size)
        if top_n > 0:
            if len(largest) < top_n:
                heapq.heappush(largest, (size, i, obj))
            elif size > largest[0][0]:
                heapq.heapreplace(largest, (size, i, obj))

    summary.scan_seconds = time.monotonic() - started
    summary.size_histogram, summary.age_histogram = size_hist, age_hist
    summary.by_extension, summary.by_storage_class = (
        _by_size(by_ext),
        _by_size(by_class),
    )
    summary.by_folder = _by_size(by_folder)
    summary.cost_by_storage_class = {
        cls: cost_by_class.get(cls) for cls in summary.by_storage_class
    }
    summary.below_minimum = dict(below_minimum)
    summary.largest = [obj for _, _, obj in sorted(largest, reverse=True)]
    return summary


def build_folder_tree(
    objects: Iterable[ObjectInfo],
    uri: str = "",
    *,
    depth: int = 2,
    limit: int | None = None,
    files: int = 0,
) -> FolderTree:
    """
    Aggregate objects into every folder level down to `depth` (each object counts toward all its ancestors).

    With `files=N`, also keep the first N files (in key order) directly in each folder shown, and count
    every file there in `direct`.
    """
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    base = base_prefix(prefix)
    tree = FolderTree(
        uri=s3_uri(bucket, prefix) if bucket else uri, depth=depth, max_files=files
    )
    folders: dict[str, Stat] = defaultdict(Stat)
    direct: dict[str, Stat] = defaultdict(Stat)
    kept: dict[str, list[ObjectInfo]] = defaultdict(list)
    for i, obj in enumerate(objects):
        if limit is not None and i >= limit:
            tree.truncated = True
            break
        if obj.is_folder_marker:
            continue
        tree.total.add(obj.size)
        all_parts = relative_key(obj.key, base).split("/")[:-1]
        parts = all_parts[:depth]
        if not parts:
            folders[""].add(obj.size)
        for level in range(1, len(parts) + 1):
            folders["/".join(parts[:level]) + "/"].add(obj.size)
        if files > 0 and len(all_parts) <= depth:
            parent = "/".join(all_parts) + "/" if all_parts else ""
            direct[parent].add(obj.size)
            if len(kept[parent]) < files:
                kept[parent].append(obj)
    tree.folders = dict(sorted(folders.items(), key=lambda kv: kv[0].split("/")))
    tree.files = {k: sorted(v, key=lambda o: o.key) for k, v in kept.items()}
    tree.direct = dict(direct)
    return tree


def make_filter(
    *,
    pattern: str | None = None,
    regex: str | None = None,
    extensions: str | Iterable[str] | None = None,
    min_size: int | str | None = None,
    max_size: int | str | None = None,
    modified_after: Any = None,
    modified_before: Any = None,
    storage_classes: str | Iterable[str] | None = None,
) -> Callable[[ObjectInfo], bool]:
    """
    Predicate for find(); every given condition must match.

    pattern     glob, matched against the file name - or the full key if it contains '/'
    regex       re.search against the full key
    extensions  ['csv', 'parquet']; 'csv' also matches 'csv.gz'
    min/max_size  bytes or '10MB' / '1.5GB'
    modified_after/before  datetime, '2024-05-01', or relative '7d' / '12h' (= that long ago)
    """
    low, high = parse_size(min_size), parse_size(max_size)
    after, before = parse_time(modified_after), parse_time(modified_before)
    compiled = re.compile(regex) if regex else None
    exts = {
        e.lower().lstrip(".")
        for e in ([extensions] if isinstance(extensions, str) else extensions or [])
    }
    classes = {
        c.upper()
        for c in (
            [storage_classes]
            if isinstance(storage_classes, str)
            else storage_classes or []
        )
    }

    def keep(obj: ObjectInfo) -> bool:
        if obj.is_folder_marker:
            return False
        if low is not None and obj.size < low:
            return False
        if high is not None and obj.size > high:
            return False
        if after is not None and obj.last_modified < after:
            return False
        if before is not None and obj.last_modified >= before:
            return False
        if classes and obj.storage_class not in classes:
            return False
        if (
            exts
            and obj.extension not in exts
            and obj.extension.split(".")[0] not in exts
        ):
            return False
        if pattern and not fnmatch.fnmatchcase(
            obj.key if "/" in pattern else obj.name, pattern
        ):
            return False
        return not (compiled and not compiled.search(obj.key))

    return keep


def compare_objects(
    a: Iterable[ObjectInfo],
    b: Iterable[ObjectInfo],
    *,
    prefix_a: str = "",
    prefix_b: str = "",
    uri_a: str = "",
    uri_b: str = "",
) -> CompareResult:
    """
    Match objects by key relative to their prefix; compare size, then ETag.
    SSE-KMS objects have random ETags, so identical KMS-encrypted files show up as 'different'.
    """
    index_a = {relative_key(o.key, prefix_a): o for o in a if not o.is_folder_marker}
    index_b = {relative_key(o.key, prefix_b): o for o in b if not o.is_folder_marker}
    result = CompareResult(uri_a=uri_a, uri_b=uri_b)
    for rel, obj_a in index_a.items():
        obj_b = index_b.get(rel)
        if obj_b is None:
            result.only_in_a.append(obj_a)
        elif obj_a.size != obj_b.size:
            result.different.append((obj_a, obj_b))
        elif obj_a.etag == obj_b.etag:
            result.identical += 1
        elif obj_a.is_multipart or obj_b.is_multipart:
            result.unverifiable += 1
        else:
            result.different.append((obj_a, obj_b))
    result.only_in_b = [obj for rel, obj in index_b.items() if rel not in index_a]
    return result


def summary_findings(
    summary: PrefixSummary, prices: dict[str, float] | None = None
) -> list[tuple[str, str]]:
    """
    Plain-language observations about a prefix -> [(level, message)], level 'warn' or 'info'.
    """
    prices = S3_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    n = summary.object_count
    if summary.truncated:
        found.append(
            (
                "warn",
                f"Scan stopped at the limit: numbers cover only the first {n:,} keys (lexical order).",
            )
        )
    if not n:
        return found
    small = sum(
        summary.size_histogram[label].count
        for label, upper in SIZE_BANDS[1:]
        if upper and upper <= MB
    )
    if n >= 1000 and small / n >= 0.5:
        found.append(
            (
                "warn",
                f"{small / n:.0%} of objects are under 1 MB. Many small files slow down "
                "Athena/Spark/Glue and inflate request costs; consider compacting them.",
            )
        )
    if summary.empty_count:
        found.append(
            ("info", f"{_plural(summary.empty_count, 'empty (0-byte) object')}.")
        )
    archived = sum(
        st.count
        for cls, st in summary.by_storage_class.items()
        if cls in ARCHIVE_CLASSES
    )
    if archived:
        found.append(
            (
                "warn",
                f"{_plural(archived, 'object')} in GLACIER / DEEP_ARCHIVE must be restored before reading.",
            )
        )
    if summary.cold_standard.size >= GB:
        cold = summary.cold_standard
        saving = ""
        if "STANDARD" in prices and "STANDARD_IA" in prices:
            monthly = cold.size * (prices["STANDARD"] - prices["STANDARD_IA"]) / GB
            saving = f" In STANDARD_IA it would cost about {human_money(monthly)}/month less (see what_if)."
        found.append(
            (
                "info",
                f"{human_size(cold.size)} in {_plural(cold.count, 'STANDARD object')} hasn't changed in 90+ days. "
                "If it's rarely read, Intelligent-Tiering or a lifecycle transition could cut storage cost."
                + saving,
            )
        )
    if summary.below_minimum:
        count = sum(st.count for st in summary.below_minimum.values())
        stored = sum(st.size for st in summary.below_minimum.values())
        billed = sum(
            st.count * MIN_BILLABLE_SIZE[cls] * prices.get(cls, 0.0) / GB
            for cls, st in summary.below_minimum.items()
        )
        in_standard = stored * prices.get("STANDARD", 0.0) / GB
        cheaper = (
            f" In STANDARD they would cost {human_money(in_standard)}/month."
            if "STANDARD" in prices and in_standard < billed
            else ""
        )
        classes = " / ".join(summary.below_minimum)
        found.append(
            (
                "warn" if billed - in_standard >= 1 else "info",
                f"{_plural(count, 'object')} in {classes} are under 128 KB, but S3 bills each one as 128 KB: "
                f"{human_size(count * 128 * KB)} billed for {human_size(stored)} stored, "
                f"costing {human_money(billed)}/month.{cheaper}",
            )
        )
    if summary.folder_markers:
        found.append(
            (
                "info",
                f"{_plural(summary.folder_markers, 'zero-byte folder-marker key')} (ending in '/') not counted.",
            )
        )
    return found
