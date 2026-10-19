"""
Identical files: which to hash, the groups they make, the folders that repeat, and what
the copies cost.
"""

from __future__ import annotations

import math
import posixpath
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from ..._kit.fmt import _plural, human_money, human_size, parse_size

from ..helpers import GB, KB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..models import DuplicateFolder, DuplicateGroup, DuplicateReport, ObjectInfo
from .cost import object_monthly_cost


def find_duplicate_groups(
    objects: Iterable[ObjectInfo], *, min_size: int | str = 1
) -> list[list[ObjectInfo]]:
    """
    Group objects with the same (size, ETag); biggest reclaimable bytes first.

    Same size + ETag means same content. It can miss copies: an identical file uploaded
    with different multipart part sizes, or encrypted with SSE-KMS, gets a different ETag.
    """
    threshold = parse_size(min_size) or 0
    groups: dict[tuple[int, str], list[ObjectInfo]] = defaultdict(list)
    for obj in objects:
        if obj.size >= threshold and obj.etag and not obj.is_folder_marker:
            groups[(obj.size, obj.etag)].append(obj)
    dupes = [group for group in groups.values() if len(group) > 1]
    dupes.sort(key=lambda group: group[0].size * (len(group) - 1), reverse=True)
    return dupes


DUPLICATE_METHODS = ("etag", "hash", "strict")

HASH_HEAD_BYTES = (
    64 * KB
)  # find_duplicates hashes this much of a file first; the rest only if the starts match


def _etag_clusters(same_size: list[ObjectInfo]) -> list[list[ObjectInfo]]:
    """
    Files of one size -> lists of files sharing an ETag (a file without one is on its own).
    """
    clusters: dict[str, list[ObjectInfo]] = {}
    for obj in same_size:
        clusters.setdefault(obj.etag or obj.uri, []).append(obj)
    return list(clusters.values())


def files_to_hash(
    objects: Iterable[ObjectInfo], *, min_size: int | str = 1, method: str = "hash"
) -> list[list[ObjectInfo]]:
    """
    The files find_duplicates reads, one list per size, biggest possible saving first.

    A file whose size no other file has can't be a copy, and files with the same size and ETag already match,
    so method='hash' reads one file per ETag, only for sizes where the ETags differ, picking one outside GLACIER /
    DEEP_ARCHIVE when it can (those can't be read without a restore). 'strict' reads every file that shares its
    size with another; 'etag' reads nothing.
    """
    if method not in DUPLICATE_METHODS:
        raise ValueError(
            f"method must be one of {', '.join(map(repr, DUPLICATE_METHODS))}"
        )
    threshold = parse_size(min_size) or 0
    by_size: dict[int, list[ObjectInfo]] = defaultdict(list)
    for obj in objects:
        if obj.size >= threshold and not obj.is_folder_marker:
            by_size[obj.size].append(obj)
    plan: list[list[ObjectInfo]] = []
    for same in by_size.values():
        if len(same) < 2 or method == "etag":
            continue
        if method == "strict":
            plan.append(same)
            continue
        clusters = _etag_clusters(same)
        if len(clusters) > 1:
            plan.append(
                [
                    next((o for o in c if o.storage_class not in ARCHIVE_CLASSES), c[0])
                    for c in clusters
                ]
            )
    plan.sort(key=lambda files: files[0].size * (len(files) - 1), reverse=True)
    return plan


def _root(parent: list[int], i: int) -> int:
    while parent[i] != i:
        i = parent[i]
    return i


def group_duplicates(
    objects: Iterable[ObjectInfo],
    uri: str = "",
    *,
    hashes: dict[str, str] | None = None,
    distinct: Iterable[str] = (),
    min_size: int | str = 1,
    prices: dict[str, float] | None = None,
) -> DuplicateReport:
    """
    Group identical files -> DuplicateReport. Files match when they have the same size and either the same
    ETag or the same SHA-256 in `hashes` (uri -> hex digest). `distinct` lists uris already known to differ from
    every other file of their size (their first bytes differ). Works on any list of ObjectInfo, e.g. S3 Inventory
    rows. The groups are sorted by the space their copies take. In each, the file to keep comes first: one that can
    be read without a restore (not GLACIER / DEEP_ARCHIVE), in the folder with the smallest share of duplicated
    files so that a folder of copies empties out, then the oldest.
    """
    hashes = hashes or {}
    distinct = set(distinct)
    threshold = parse_size(min_size) or 0
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    report = DuplicateReport(uri=s3_uri(bucket, prefix) if bucket else uri)
    by_size: dict[int, list[ObjectInfo]] = defaultdict(list)
    folders: Counter[str] = Counter()
    for obj in objects:
        if obj.is_folder_marker:
            continue
        report.scanned.add(obj.size)
        if obj.size >= threshold:
            by_size[obj.size].append(obj)
            folders[base_prefix(obj.key)] += 1
    report.files_by_folder = dict(folders)

    found: list[
        tuple[int, list[list[ObjectInfo]]]
    ] = []  # (size, the ETag clusters making up one group)
    for size, same in by_size.items():
        if len(same) < 2:
            continue
        report.candidates.count += len(same)
        report.candidates.size += size * len(same)
        clusters = _etag_clusters(same)
        parent = list(
            range(len(clusters))
        )  # union-find: clusters with a SHA-256 in common are one group
        first_with: dict[str, int] = {}
        for i, cluster in enumerate(clusters):
            for digest in {hashes[o.uri] for o in cluster if o.uri in hashes}:
                if digest in first_with:
                    parent[_root(parent, i)] = _root(parent, first_with[digest])
                else:
                    first_with[digest] = i
        if len(clusters) > 1:
            for cluster in clusters:
                if not any(o.uri in hashes or o.uri in distinct for o in cluster):
                    report.not_compared.count += len(cluster)
                    report.not_compared.size += size * len(cluster)
        merged: dict[int, list[list[ObjectInfo]]] = defaultdict(list)
        for i, cluster in enumerate(clusters):
            merged[_root(parent, i)].append(cluster)
        found += [
            (size, parts) for parts in merged.values() if sum(map(len, parts)) > 1
        ]

    duplicated = Counter(
        base_prefix(o.key) for _, parts in found for cluster in parts for o in cluster
    )

    def keep_first(obj: ObjectInfo) -> tuple[bool, float, datetime, str]:
        folder = base_prefix(obj.key)
        archived = obj.storage_class in ARCHIVE_CLASSES
        return (
            archived,
            duplicated[folder] / max(folders[folder], 1),
            obj.last_modified,
            obj.key,
        )

    for size, parts in found:
        members = sorted((o for cluster in parts for o in cluster), key=keep_first)
        hashed = [o for o in members if o.uri in hashes]
        by_hash = (
            len(parts) > 1 or len(hashed) > 1
        )  # some files are known to match by their SHA-256
        matched_by = (
            "SHA-256"
            if len(hashed) == len(members)
            else "SHA-256 + ETag"
            if by_hash
            else "ETag"
        )
        cost = sum(
            object_monthly_cost(o.size, o.storage_class, prices) or 0.0
            for o in members[1:]
        )
        report.groups.append(
            DuplicateGroup(
                size,
                members,
                hashes[hashed[0].uri] if hashed else None,
                matched_by,
                cost,
            )
        )
    report.groups.sort(key=lambda g: (-g.reclaimable, g.keep.key))
    return report


def duplicate_folders(report: DuplicateReport) -> list[DuplicateFolder]:
    """
    Folders holding duplicated files, most duplicated bytes first. A folder whose files all exist in other
    folders too (all_copies) can go without losing data, as long as those other copies stay.
    """
    folders: dict[str, DuplicateFolder] = {}
    for group in report.groups:
        where = Counter(base_prefix(o.key) for o in group.objects)
        for obj in group.objects:
            here = base_prefix(obj.key)
            entry = folders.setdefault(
                here, DuplicateFolder(here, report.files_by_folder.get(here, 0))
            )
            entry.duplicated.add(obj.size)
            entry.outside += len(where) > 1
            for folder, count in where.items():
                if folder != here or count > 1:
                    entry.elsewhere[folder] = entry.elsewhere.get(folder, 0) + 1
    for entry in folders.values():
        entry.elsewhere = dict(
            sorted(entry.elsewhere.items(), key=lambda kv: (-kv[1], kv[0]))
        )
    return sorted(
        folders.values(),
        key=lambda f: (-f.duplicated.size, -f.duplicated.count, f.folder),
    )


def _folders_text(folders: list[str], base: str = "", most: int = 2) -> str:
    """
    ['logs/a/', 'logs/b/', 'logs/c/'] -> '3 folders under logs/'; ['a/', 'b/'] -> 'a/ and b/'.
    """
    names = [relative_key(f, base) or "(top level)" for f in folders]
    if len(names) <= most:
        return " and ".join(names)
    common = posixpath.commonprefix(folders)
    common = common[: common.rfind("/") + 1]
    if len(common) > len(base):
        return f"{len(names):,} folders under {relative_key(common, base)}"
    return ", ".join(names[:most]) + f" and {len(names) - most:,} more folders"


_UNREADABLE_REASONS = {
    "GLACIER": "in GLACIER, which needs a restore first",
    "DEEP_ARCHIVE": "in DEEP_ARCHIVE, which needs a restore first",
    "InvalidObjectState": "archived, which needs a restore first",
    "AccessDenied": "AccessDenied (needs s3:GetObject)",
    "PreconditionFailed": "changed while being read",
    "NoSuchKey": "deleted while being read",
}


def duplicate_findings(report: DuplicateReport) -> list[tuple[str, str]]:
    """
    Plain-language observations about duplicate files -> [(level, message)], level 'warn' or 'info'.
    """
    found: list[tuple[str, str]] = []
    base = base_prefix(parse_s3_uri(report.uri)[1]) if report.uri else ""

    def name(folder: str) -> str:
        return relative_key(folder, base) or "(top level)"

    if report.truncated:
        found.append(
            (
                "warn",
                f"Listing stopped at the limit: only the first {report.scanned.count:,} files (in key "
                "order) were compared, so copies of later files are missing. Pass limit=None to "
                "check them all.",
            )
        )
    if report.groups:
        copies = report.copies
        big = report.monthly_cost >= 1 or report.reclaimable >= GB
        found.append(
            (
                "warn" if big else "info",
                f"{copies:,} redundant {'copy takes' if copies == 1 else 'copies take'} "
                f"{human_size(report.reclaimable)}, costing {human_money(report.monthly_cost)}/month. Keep "
                "one file per group and delete the others, after checking that no job or notebook reads "
                "their paths. Keep suggests a readable file in the folder with the fewest copies, then the "
                "oldest.",
            )
        )
    whole = {f.folder: f for f in duplicate_folders(report) if f.all_copies}
    reported: set[str] = set()
    for folder in list(whole.values())[:3]:
        others = [f for f in folder.elsewhere if f != folder.folder]
        size = human_size(folder.duplicated.size)
        level = "warn" if folder.duplicated.size >= GB else "info"
        mirror = others[0] if len(others) == 1 and others[0] in whole else None
        if mirror and [f for f in whole[mirror].elsewhere if f != mirror] == [
            folder.folder
        ]:
            if mirror in reported:
                continue
            found.append(
                (
                    level,
                    f"{name(folder.folder)} and {name(mirror)} hold the same "
                    f"{_plural(folder.files, 'file')} ({size}). Keeping one of the two folders "
                    "loses no data.",
                )
            )
        else:
            what = (
                f"its only file ({size}) is identical to a file"
                if folder.files == 1
                else f"all {folder.files:,} of its files ({size}) are identical to files"
            )
            found.append(
                (
                    level,
                    f"{name(folder.folder)} holds only copies: {what} in {_folders_text(others, base)}. "
                    "Removing it, or no longer writing it, loses no data while those stay.",
                )
            )
        reported.add(folder.folder)
    if report.groups and report.versioning == "Enabled":
        found.append(
            (
                "warn",
                "Versioning is on for this bucket: a deleted copy stays as a noncurrent version, still "
                "billed, until a lifecycle rule with NoncurrentVersionExpiration removes it "
                "(bucket_info shows the rules).",
            )
        )
    missed = report.not_compared
    if missed.count and report.method == "etag":
        found.append(
            (
                "info",
                f"{_plural(missed.count, 'file')} ({human_size(missed.size)}) share their size with a "
                "file whose ETag differs, so their contents weren't compared. Copies uploaded in parts "
                "of another size, or encrypted with SSE-KMS, look like this; method='hash' reads them "
                "to check.",
            )
        )
    elif missed.count and report.read_capped:
        suggest = max(1, math.ceil((report.bytes_read + missed.size) / GB))
        found.append(
            (
                "warn",
                f"Stopped reading at max_read={human_size(report.max_read)}: "
                f"{_plural(missed.count, 'file')} ({human_size(missed.size)}) sharing a size with "
                "another file weren't compared, so some copies may be missing. Pass "
                f"max_read='{suggest}GB' to check them all.",
            )
        )
    if report.unreadable:
        reasons = Counter(report.unreadable.values())
        text = ", ".join(
            f"{n:,} {_UNREADABLE_REASONS.get(code, code)}"
            for code, n in reasons.most_common()
        )
        found.append(
            (
                "info",
                f"{_plural(len(report.unreadable), 'file')} could only be compared by ETag, because "
                f"they couldn't be read: {text}.",
            )
        )
    by_etag = sum(g.matched_by == "ETag" for g in report.groups)
    if by_etag and report.method != "strict":
        found.append(
            (
                "info",
                f"{_plural(by_etag, 'group')} matched on size + ETag without reading the files: the "
                "ETag is S3's checksum of the content (the MD5, for a file uploaded in one part). "
                "method='strict' reads every file to confirm with SHA-256.",
            )
        )
    return found
