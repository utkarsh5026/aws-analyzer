"""
s3.py - self-contained S3 analysis toolkit for SageMaker / Jupyter notebooks.

Copy this one file into a notebook cell (or upload it next to your notebook and
``import s3``). Nothing else from this repo is needed.

Requirements: boto3 (required). pandas + pyarrow only for DataFrame / parquet
previews, IPython only for rich HTML output. All are preinstalled on SageMaker.

The file has two layers:

    S3Analyzer   Pure logic. Talks to AWS and returns plain Python data
                 (dataclasses, dicts, lists, DataFrames). Never prints.
    S3View       Notebook UI. Calls S3Analyzer and renders readable cards and
                 tables (HTML in Jupyter, plain text in a terminal).

Quick start
-----------
    ui = S3View()                                   # or S3View(S3Analyzer(profile="dev"))
    ui.help()                                       # list every command
    ui.buckets()                                    # all buckets + regions
    ui.bucket_info("my-bucket")                     # versioning, encryption, lifecycle, size
    ui.ls("s3://my-bucket/data/")                   # one level, like `aws s3 ls`
    ui.summary("s3://my-bucket/data/")              # full dashboard for a prefix
    ui.tree("s3://my-bucket/data/", depth=2)        # folder sizes as a tree
    ui.find("s3://my-bucket/data/", pattern="*.parquet", min_size="100MB")
    ui.preview("s3://my-bucket/data/part-0.csv.gz")

    s3 = ui.core                                    # same analyzer, raw data
    summary = s3.summarize("s3://my-bucket/data/")
    df = s3.read_df("s3://my-bucket/data/part-0.parquet", nrows=1000)
    files = objects_to_df(s3.find("s3://my-bucket/data/", extensions=["csv"]))
"""

from __future__ import annotations

import bz2
import fnmatch
import functools
import gzip
import heapq
import html
import importlib
import inspect
import io
import json
import lzma
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# =============================================================================
# 1. Helpers: parsing and formatting
# =============================================================================

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """'s3://bucket/some/prefix' (or 'bucket/some/prefix') -> ('bucket', 'some/prefix')."""
    uri = uri.strip()
    for scheme in ("s3://", "s3a://", "s3n://"):
        if uri.lower().startswith(scheme):
            uri = uri[len(scheme):]
            break
    bucket, _, key = uri.partition("/")
    if not bucket:
        raise ValueError(f"No bucket in S3 URI {uri!r}; expected 's3://bucket/prefix'")
    return bucket, key


def s3_uri(bucket: str, key: str = "") -> str:
    return f"s3://{bucket}/{key}"


def base_prefix(prefix: str) -> str:
    """The 'folder' part of a prefix: 'logs/2024-0' -> 'logs/', 'logs/' -> 'logs/'."""
    return prefix[: prefix.rfind("/") + 1]


def relative_key(key: str, prefix: str) -> str:
    return key[len(prefix):] if key.startswith(prefix) else key


def human_size(num_bytes: float | None) -> str:
    """1536 -> '1.5 KB' (binary units, like the S3 console)."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)i?b?\s*$", re.IGNORECASE)


def parse_size(value: int | float | str | None) -> int | None:
    """'10MB', '1.5 GiB', '512k', 1024 -> bytes. Units are binary (1 KB = 1024 B)."""
    if value is None or isinstance(value, (int, float)):
        return None if value is None else int(value)
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Can't parse size {value!r}; try 1024, '10MB' or '1.5GB'")
    number, unit = match.groups()
    return int(float(number) * 1024 ** " kmgtp".index(unit.lower() or " "))


_RELATIVE_TIME_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: datetime | date | timedelta | str | None, now: datetime | None = None) -> datetime | None:
    """datetime/date, ISO string ('2024-05-01', '2024-05-01T10:00Z'), or a relative
    age like '7d', '12h', '30m', '2w' meaning "that long ago". Naive values are UTC."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return (now or _utcnow()) - value
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        relative = _RELATIVE_TIME_RE.match(str(value))
        if relative:
            seconds = float(relative.group(1)) * _UNIT_SECONDS[relative.group(2).lower()]
            return (now or _utcnow()) - timedelta(seconds=seconds)
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def human_age(when: datetime | None, now: datetime | None = None) -> str:
    """datetime -> '3d ago' / '5mo ago' / 'just now'."""
    if when is None:
        return "-"
    seconds = ((now or _utcnow()) - when).total_seconds()
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


_COMPRESSION_EXTS = {"gz", "gzip", "bz2", "xz", "zst", "zstd", "snappy", "lz4", "zip", "z"}


def file_extension(key: str) -> str:
    """'a/data.csv.gz' -> 'csv.gz', 'a/part-0.snappy.parquet' -> 'parquet', 'a/README' -> '(none)'."""
    if key.endswith("/"):
        return "(folder marker)"
    parts = key.rsplit("/", 1)[-1].lower().split(".")
    if len(parts) < 2 or parts[-1] == "" or (len(parts) == 2 and parts[0] == ""):
        return "(none)"  # 'README', 'name.', '.env'
    ext = parts[-1]
    if ext in _COMPRESSION_EXTS and len(parts) > 2 and parts[-2].isalpha() and len(parts[-2]) <= 8:
        return f"{parts[-2]}.{ext}"
    return ext


_TEXT_EXTS = (
    "txt", "log", "md", "rst", "yaml", "yml", "xml", "html", "htm", "py", "sql", "ini", "cfg",
    "conf", "toml", "sh", "js", "ts", "out", "err", "properties",
)
_FORMAT_BY_EXT = {
    "csv": "csv", "tsv": "tsv", "tab": "tsv",
    "parquet": "parquet", "pq": "parquet",
    "json": "json", "jsonl": "jsonl", "ndjson": "jsonl",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image", "bmp": "image",
    **{ext: "text" for ext in _TEXT_EXTS},
}
_DECOMPRESSORS: dict[str, Callable[[Any], Any]] = {
    "gz": lambda f: gzip.GzipFile(fileobj=f),
    "gzip": lambda f: gzip.GzipFile(fileobj=f),
    "bz2": bz2.BZ2File,
    "xz": lzma.LZMAFile,
}


def detect_format(key: str) -> tuple[str | None, str | None]:
    """Guess (format, compression) from the key: 'x.csv.gz' -> ('csv', 'gz'), 'x.bin' -> (None, None)."""
    parts = key.rsplit("/", 1)[-1].lower().split(".")
    compression = parts.pop() if len(parts) > 1 and parts[-1] in _DECOMPRESSORS else None
    return (_FORMAT_BY_EXT.get(parts[-1]) if len(parts) > 1 else None), compression


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _require(module: str, purpose: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"{purpose} needs `{module.split('.')[0]}` (pip install {module.split('.')[0]})") from exc


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


def _looks_binary(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        return exc.start < len(sample) - 3  # a multi-byte char cut at the end is still text


# =============================================================================
# 2. Data models (what S3Analyzer returns)
# =============================================================================


@dataclass
class ObjectInfo:
    """One object from a listing."""

    bucket: str
    key: str
    size: int
    last_modified: datetime
    storage_class: str = "STANDARD"
    etag: str = ""

    @property
    def uri(self) -> str:
        return s3_uri(self.bucket, self.key)

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def extension(self) -> str:
        return file_extension(self.key)

    @property
    def is_folder_marker(self) -> bool:
        return self.key.endswith("/") and self.size == 0

    @property
    def is_multipart(self) -> bool:
        return "-" in self.etag  # multipart ETags look like "<md5-of-md5s>-<part count>"


@dataclass
class Stat:
    """Running object count + total bytes."""

    count: int = 0
    size: int = 0

    def add(self, size: int) -> None:
        self.count += 1
        self.size += size


@dataclass
class BucketInfo:
    name: str
    created: datetime | None = None
    region: str | None = None


@dataclass
class Listing:
    """One level of a prefix, like `aws s3 ls`."""

    uri: str
    folders: list[str] = field(default_factory=list)  # full prefixes, ending in '/'
    objects: list[ObjectInfo] = field(default_factory=list)
    truncated: bool = False


@dataclass
class BucketConfig:
    """Bucket settings. Sections that couldn't be read are listed in `errors` (section -> error code)."""

    name: str
    region: str | None = None
    versioning: str | None = None  # 'Enabled' | 'Suspended' | 'Disabled'
    mfa_delete: str | None = None
    encryption: str | None = None  # 'AES256' | 'aws:kms' | 'aws:kms:dsse' | None
    kms_key: str | None = None
    bucket_key_enabled: bool | None = None
    public_access_block: dict[str, bool] | None = None  # None = not configured on the bucket
    has_policy: bool | None = None
    policy_is_public: bool | None = None
    object_ownership: str | None = None
    object_lock: bool | None = None
    lifecycle_rules: list[dict] = field(default_factory=list)
    replication_rules: list[dict] = field(default_factory=list)
    logging_target: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    inventory_configs: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class BucketMetrics:
    """Daily CloudWatch storage metrics: bucket totals without listing a single key."""

    bucket: str
    object_count: int | None = None
    size_by_storage_type: dict[str, int] = field(default_factory=dict)
    as_of: datetime | None = None

    @property
    def total_size(self) -> int:
        return sum(self.size_by_storage_type.values())


@dataclass
class PrefixSummary:
    """Everything `summarize` learns from one pass over a prefix. Folder markers are excluded."""

    uri: str
    object_count: int = 0
    total_size: int = 0
    min_size: int | None = None
    max_size: int = 0
    empty_count: int = 0
    folder_markers: int = 0
    oldest: ObjectInfo | None = None
    newest: ObjectInfo | None = None
    by_extension: dict[str, Stat] = field(default_factory=dict)
    by_storage_class: dict[str, Stat] = field(default_factory=dict)
    by_folder: dict[str, Stat] = field(default_factory=dict)  # '' = files directly under the prefix
    size_histogram: dict[str, Stat] = field(default_factory=dict)
    age_histogram: dict[str, Stat] = field(default_factory=dict)
    cold_standard: Stat = field(default_factory=Stat)  # STANDARD objects unchanged for 90+ days
    largest: list[ObjectInfo] = field(default_factory=list)
    truncated: bool = False
    scan_seconds: float = 0.0

    @property
    def avg_size(self) -> float:
        return self.total_size / self.object_count if self.object_count else 0.0


@dataclass
class FolderTree:
    """Size per folder, every level down to `depth`. Keys are folder paths relative to the prefix."""

    uri: str
    depth: int
    folders: dict[str, Stat] = field(default_factory=dict)  # '' = files directly under the prefix
    total: Stat = field(default_factory=Stat)
    truncated: bool = False


@dataclass
class CompareResult:
    """Two prefixes compared by relative key, size and ETag."""

    uri_a: str
    uri_b: str
    only_in_a: list[ObjectInfo] = field(default_factory=list)
    only_in_b: list[ObjectInfo] = field(default_factory=list)
    different: list[tuple[ObjectInfo, ObjectInfo]] = field(default_factory=list)
    identical: int = 0
    unverifiable: int = 0  # same size, but multipart ETags can't prove equal content

    @property
    def in_sync(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.different)


@dataclass
class VersionStats:
    uri: str
    current: Stat = field(default_factory=Stat)
    noncurrent: Stat = field(default_factory=Stat)
    delete_markers: int = 0
    deleted_keys: int = 0  # keys whose latest version is a delete marker (old versions still billed)
    top_noncurrent: list[tuple[str, Stat]] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ObjectVersion:
    key: str
    version_id: str
    is_latest: bool
    last_modified: datetime
    size: int = 0
    is_delete_marker: bool = False
    storage_class: str | None = None


@dataclass
class MultipartUpload:
    bucket: str
    key: str
    upload_id: str
    initiated: datetime
    storage_class: str = "STANDARD"
    parts: int | None = None  # filled when with_sizes=True
    size: int | None = None


@dataclass
class Preview:
    """First look at an object. kind: 'table' (DataFrame), 'json', 'text' (list of lines),
    'image' (bytes), 'binary' (bytes) or 'unavailable'."""

    uri: str
    kind: str
    size: int
    format: str | None = None
    compression: str | None = None
    content_type: str | None = None
    data: Any = None
    truncated: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def objects_to_df(objects: Iterable[ObjectInfo]):
    """List of ObjectInfo -> pandas DataFrame (key, size, last_modified, storage_class, ...)."""
    pd = _require("pandas", "objects_to_df")
    columns = ["key", "size", "last_modified", "storage_class", "extension", "etag", "uri"]
    return pd.DataFrame(
        [[o.key, o.size, o.last_modified, o.storage_class, o.extension, o.etag, o.uri] for o in objects],
        columns=columns,
    )


# =============================================================================
# 3. Pure analysis (no AWS calls - works on any iterable of ObjectInfo)
# =============================================================================

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
AGE_BANDS: list[tuple[str, int | None]] = [  # (label, exclusive upper bound in days since modified)
    ("< 1 day", 1),
    ("1 - 7 days", 7),
    ("1 - 4 weeks", 30),
    ("1 - 3 months", 90),
    ("3 - 12 months", 365),
    ("1 - 3 years", 3 * 365),
    ("> 3 years", None),
]
ARCHIVE_CLASSES = {"GLACIER", "DEEP_ARCHIVE"}  # need a restore before GetObject works


def _band(value: float, bands: list[tuple[str, int | None]]) -> str:
    for label, upper in bands:
        if upper is None or value < upper:
            return label
    return bands[-1][0]


def _by_size(stats: dict[str, Stat]) -> dict[str, Stat]:
    return dict(sorted(stats.items(), key=lambda kv: kv[1].size, reverse=True))


def folder_of(key: str, base: str, depth: int = 1) -> str:
    """Folder of `key` relative to `base`, at most `depth` levels: ('a/b/c/f.csv', 'a/', 1) -> 'b/'.
    Returns '' for files directly under `base`."""
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
) -> PrefixSummary:
    """One streaming pass over `objects` -> PrefixSummary (counts, sizes, histograms, top-N)."""
    now = now or _utcnow()
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    base = base_prefix(prefix)
    summary = PrefixSummary(uri=s3_uri(bucket, prefix) if bucket else uri)
    size_hist = {label: Stat() for label, _ in SIZE_BANDS}
    age_hist = {label: Stat() for label, _ in AGE_BANDS}
    by_ext: dict[str, Stat] = defaultdict(Stat)
    by_class: dict[str, Stat] = defaultdict(Stat)
    by_folder: dict[str, Stat] = defaultdict(Stat)
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
        summary.min_size = size if summary.min_size is None else min(summary.min_size, size)
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
        if top_n > 0:
            if len(largest) < top_n:
                heapq.heappush(largest, (size, i, obj))
            elif size > largest[0][0]:
                heapq.heapreplace(largest, (size, i, obj))

    summary.scan_seconds = time.monotonic() - started
    summary.size_histogram, summary.age_histogram = size_hist, age_hist
    summary.by_extension, summary.by_storage_class = _by_size(by_ext), _by_size(by_class)
    summary.by_folder = _by_size(by_folder)
    summary.largest = [obj for _, _, obj in sorted(largest, reverse=True)]
    return summary


def build_folder_tree(
    objects: Iterable[ObjectInfo], uri: str = "", *, depth: int = 2, limit: int | None = None
) -> FolderTree:
    """Aggregate objects into every folder level down to `depth` (each object counts toward all its ancestors)."""
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    base = base_prefix(prefix)
    tree = FolderTree(uri=s3_uri(bucket, prefix) if bucket else uri, depth=depth)
    folders: dict[str, Stat] = defaultdict(Stat)
    for i, obj in enumerate(objects):
        if limit is not None and i >= limit:
            tree.truncated = True
            break
        if obj.is_folder_marker:
            continue
        tree.total.add(obj.size)
        parts = relative_key(obj.key, base).split("/")[:-1][:depth]
        if not parts:
            folders[""].add(obj.size)
        for level in range(1, len(parts) + 1):
            folders["/".join(parts[:level]) + "/"].add(obj.size)
    tree.folders = dict(sorted(folders.items(), key=lambda kv: kv[0].split("/")))
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
    """Predicate for find(); every given condition must match.

    pattern     glob, matched against the file name - or the full key if it contains '/'
    regex       re.search against the full key
    extensions  ['csv', 'parquet']; 'csv' also matches 'csv.gz'
    min/max_size  bytes or '10MB' / '1.5GB'
    modified_after/before  datetime, '2024-05-01', or relative '7d' / '12h' (= that long ago)
    """
    low, high = parse_size(min_size), parse_size(max_size)
    after, before = parse_time(modified_after), parse_time(modified_before)
    compiled = re.compile(regex) if regex else None
    exts = {e.lower().lstrip(".") for e in ([extensions] if isinstance(extensions, str) else extensions or [])}
    classes = {c.upper() for c in ([storage_classes] if isinstance(storage_classes, str) else storage_classes or [])}

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
        if exts and obj.extension not in exts and obj.extension.split(".")[0] not in exts:
            return False
        if pattern and not fnmatch.fnmatchcase(obj.key if "/" in pattern else obj.name, pattern):
            return False
        return not (compiled and not compiled.search(obj.key))

    return keep


def find_duplicate_groups(objects: Iterable[ObjectInfo], *, min_size: int | str = 1) -> list[list[ObjectInfo]]:
    """Group objects with the same (size, ETag); biggest reclaimable bytes first.

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


def compare_objects(
    a: Iterable[ObjectInfo], b: Iterable[ObjectInfo], *, prefix_a: str = "", prefix_b: str = "",
    uri_a: str = "", uri_b: str = "",
) -> CompareResult:
    """Match objects by key relative to their prefix; compare size, then ETag.
    SSE-KMS objects have random ETags, so identical KMS-encrypted files show up as 'different'."""
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


def summary_findings(summary: PrefixSummary) -> list[tuple[str, str]]:
    """Plain-language observations about a prefix -> [(level, message)], level 'warn' or 'info'."""
    found: list[tuple[str, str]] = []
    n = summary.object_count
    if summary.truncated:
        found.append(("warn", f"Scan stopped at the limit: numbers cover only the first {n:,} keys (lexical order)."))
    if not n:
        return found
    small = sum(summary.size_histogram[label].count for label, upper in SIZE_BANDS[1:] if upper and upper <= MB)
    if n >= 1000 and small / n >= 0.5:
        found.append(("warn", f"{small / n:.0%} of objects are under 1 MB. Many small files slow down "
                              "Athena/Spark/Glue and inflate request costs; consider compacting them."))
    if summary.empty_count:
        found.append(("info", f"{_plural(summary.empty_count, 'empty (0-byte) object')}."))
    archived = sum(st.count for cls, st in summary.by_storage_class.items() if cls in ARCHIVE_CLASSES)
    if archived:
        found.append(("warn", f"{_plural(archived, 'object')} in GLACIER / DEEP_ARCHIVE must be restored before reading."))
    if summary.cold_standard.size >= GB:
        cold = summary.cold_standard
        found.append(("info", f"{human_size(cold.size)} in {_plural(cold.count, 'STANDARD object')} hasn't changed in 90+ days. "
                              "If it's rarely read, Intelligent-Tiering or a lifecycle transition could cut storage cost."))
    if summary.folder_markers:
        found.append(("info", f"{_plural(summary.folder_markers, 'zero-byte folder-marker key')} (ending in '/') not counted."))
    return found


def bucket_findings(cfg: BucketConfig) -> list[tuple[str, str]]:
    """Plain-language risks / cost notes for a bucket config -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    pab = cfg.public_access_block
    if "public_access_block" not in cfg.errors and not (pab and all(pab.values())):
        found.append(("warn", "Block Public Access is not fully on for this bucket "
                              "(account-level Block Public Access may still apply)."))
    if cfg.policy_is_public:
        found.append(("warn", "The bucket policy grants public access."))
    if "encryption" not in cfg.errors and not cfg.encryption:
        found.append(("warn", "No default encryption configured."))
    if "lifecycle" not in cfg.errors:
        enabled = [rule for rule in cfg.lifecycle_rules if rule.get("Status") == "Enabled"]
        if cfg.versioning == "Enabled" and not any("NoncurrentVersionExpiration" in rule for rule in enabled):
            found.append(("warn", "Versioning is on but no lifecycle rule expires noncurrent versions: every "
                                  "overwritten or deleted object is kept (and billed) forever."))
        if not any("AbortIncompleteMultipartUpload" in rule for rule in enabled):
            found.append(("info", "No lifecycle rule aborts incomplete multipart uploads; leftover parts are billed "
                                  "until aborted (see incomplete uploads)."))
    if cfg.errors:
        found.append(("info", "Couldn't read: " + ", ".join(f"{k} ({v})" for k, v in cfg.errors.items())))
    return found


# =============================================================================
# 4. S3Analyzer - pure logic layer (talks to AWS, returns data)
# =============================================================================


class _BodyReader(io.RawIOBase):
    """Raw stream over a botocore StreamingBody, so io/gzip/pandas can wrap it."""

    def __init__(self, body: Any):
        self._body = body

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._body.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._body.close()
        super().close()


class _RangeReader(io.RawIOBase):
    """Seekable reader over one object using ranged GETs, so pyarrow can fetch
    just the parquet footer / needed row groups instead of the whole file."""

    def __init__(self, client: Any, bucket: str, key: str, size: int):
        self._client, self._bucket, self._key, self._size, self._pos = client, bucket, key, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        origin = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, origin + offset)
        return self._pos

    def readinto(self, buffer: Any) -> int:
        if self._pos >= self._size or not len(buffer):
            return 0
        end = min(self._pos + len(buffer), self._size) - 1
        data = self._client.get_object(Bucket=self._bucket, Key=self._key, Range=f"bytes={self._pos}-{end}")["Body"].read()
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


class S3Analyzer:
    """Pure-logic S3 analysis: every method returns data, nothing is printed.

    Anywhere a `uri` is taken you can pass 's3://bucket/prefix' or 'bucket/prefix'.
    Scans accept `limit` (stop after N keys) and `progress` (called with the running count).
    """

    def __init__(self, session: Any = None, *, region: str | None = None, profile: str | None = None,
                 client: Any = None):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        self.client = client or self.session.client("s3", config=self._config)
        self._regional_clients: dict[str, Any] = {}
        self._regions: dict[str, str] = {}

    # ------------------------------------------------------------------ buckets

    def list_buckets(self, *, with_region: bool = True) -> list[BucketInfo]:
        """All buckets you can see. Regions are looked up in parallel when S3 doesn't return them."""
        if self.client.can_paginate("list_buckets"):
            raw = [b for page in self.client.get_paginator("list_buckets").paginate() for b in page.get("Buckets", [])]
        else:
            raw = self.client.list_buckets().get("Buckets", [])
        buckets = [BucketInfo(b["Name"], b.get("CreationDate"), b.get("BucketRegion")) for b in raw]
        self._regions.update({b.name: b.region for b in buckets if b.region})
        missing = [b for b in buckets if not b.region]
        if with_region and missing:
            with ThreadPoolExecutor(max_workers=16) as pool:
                for bucket, region in zip(missing, pool.map(self._safe_region, [b.name for b in missing])):
                    bucket.region = region
        return buckets

    def bucket_region(self, bucket: str) -> str:
        """Region of a bucket (cached). Falls back to HeadBucket if GetBucketLocation is denied."""
        bucket, _ = parse_s3_uri(bucket)
        if bucket in self._regions:
            return self._regions[bucket]
        try:
            location = self.client.get_bucket_location(Bucket=bucket).get("LocationConstraint")
            region = {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(location, location)
        except ClientError as exc:
            region = self._region_header(exc.response)
            if not region:
                try:
                    region = self._region_header(self.client.head_bucket(Bucket=bucket))
                except ClientError as head_exc:
                    region = self._region_header(head_exc.response)
            if not region:
                raise exc
        self._regions[bucket] = region
        return region

    @staticmethod
    def _region_header(response: dict) -> str | None:
        return response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region")

    def _safe_region(self, bucket: str) -> str | None:
        try:
            return self.bucket_region(bucket)
        except (ClientError, BotoCoreError):
            return None

    def _client_for(self, bucket: str) -> Any:
        """S3 client in the bucket's own region (bucket-config APIs and presigned URLs need it)."""
        region = self._safe_region(bucket)
        if not region or region == self.client.meta.region_name:
            return self.client
        if region not in self._regional_clients:
            self._regional_clients[region] = self.session.client("s3", region_name=region, config=self._config)
        return self._regional_clients[region]

    def bucket_config(self, bucket: str) -> BucketConfig:
        """Versioning, encryption, public access, ownership, lock, lifecycle, replication, logging, tags."""
        bucket, _ = parse_s3_uri(bucket)
        cfg = BucketConfig(name=bucket, region=self._safe_region(bucket))
        client = self._client_for(bucket)

        def get(section: str, method: str, not_found: tuple[str, ...] = ()) -> dict | None:
            try:
                return getattr(client, method)(Bucket=bucket)
            except ClientError as exc:
                if _error_code(exc) not in not_found:
                    cfg.errors[section] = _error_code(exc)
            except BotoCoreError as exc:
                cfg.errors[section] = type(exc).__name__
            return None

        if (resp := get("versioning", "get_bucket_versioning")) is not None:
            cfg.versioning = resp.get("Status", "Disabled")
            cfg.mfa_delete = resp.get("MFADelete", "Disabled")
        if resp := get("encryption", "get_bucket_encryption", ("ServerSideEncryptionConfigurationNotFoundError",)):
            rules = resp.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
            if rules:
                default = rules[0].get("ApplyServerSideEncryptionByDefault", {})
                cfg.encryption, cfg.kms_key = default.get("SSEAlgorithm"), default.get("KMSMasterKeyID")
                cfg.bucket_key_enabled = rules[0].get("BucketKeyEnabled")
        if resp := get("public_access_block", "get_public_access_block", ("NoSuchPublicAccessBlockConfiguration",)):
            cfg.public_access_block = resp.get("PublicAccessBlockConfiguration")
        resp = get("policy", "get_bucket_policy_status", ("NoSuchBucketPolicy",))
        if "policy" not in cfg.errors:
            cfg.has_policy = resp is not None
            cfg.policy_is_public = resp["PolicyStatus"].get("IsPublic", False) if resp else None
        if resp := get("ownership", "get_bucket_ownership_controls", ("OwnershipControlsNotFoundError",)):
            cfg.object_ownership = resp["OwnershipControls"]["Rules"][0]["ObjectOwnership"]
        resp = get("object_lock", "get_object_lock_configuration", ("ObjectLockConfigurationNotFoundError",))
        if "object_lock" not in cfg.errors:
            cfg.object_lock = bool(resp and resp["ObjectLockConfiguration"].get("ObjectLockEnabled") == "Enabled")
        if resp := get("lifecycle", "get_bucket_lifecycle_configuration", ("NoSuchLifecycleConfiguration",)):
            cfg.lifecycle_rules = resp.get("Rules", [])
        if resp := get("replication", "get_bucket_replication", ("ReplicationConfigurationNotFoundError",)):
            cfg.replication_rules = resp["ReplicationConfiguration"].get("Rules", [])
        if (resp := get("logging", "get_bucket_logging")) and "LoggingEnabled" in resp:
            target = resp["LoggingEnabled"]
            cfg.logging_target = s3_uri(target["TargetBucket"], target.get("TargetPrefix", ""))
        if resp := get("tags", "get_bucket_tagging", ("NoSuchTagSet",)):
            cfg.tags = {tag["Key"]: tag["Value"] for tag in resp.get("TagSet", [])}
        if resp := get("inventory", "list_bucket_inventory_configurations"):
            cfg.inventory_configs = [c["Id"] for c in resp.get("InventoryConfigurationList", [])]
        return cfg

    def bucket_metrics(self, bucket: str, *, days: int = 3) -> BucketMetrics:
        """Object count and size per storage type from CloudWatch (published daily, free, instant -
        the fastest way to size a bucket with millions of objects). Needs cloudwatch:ListMetrics/GetMetricData."""
        bucket, _ = parse_s3_uri(bucket)
        cloudwatch = self.session.client("cloudwatch", region_name=self.bucket_region(bucket))
        result = BucketMetrics(bucket=bucket)
        metrics = [
            metric
            for page in cloudwatch.get_paginator("list_metrics").paginate(
                Namespace="AWS/S3", Dimensions=[{"Name": "BucketName", "Value": bucket}])
            for metric in page.get("Metrics", [])
            if metric["MetricName"] in ("BucketSizeBytes", "NumberOfObjects")
        ]
        if not metrics:
            return result
        now = _utcnow()
        queries = [{"Id": f"m{i}", "MetricStat": {"Metric": m, "Period": 86400, "Stat": "Average"}}
                   for i, m in enumerate(metrics)]
        for start in range(0, len(queries), 500):
            response = cloudwatch.get_metric_data(
                MetricDataQueries=queries[start:start + 500], StartTime=now - timedelta(days=days),
                EndTime=now, ScanBy="TimestampDescending")
            for series in response["MetricDataResults"]:
                if not series["Values"]:
                    continue
                metric = metrics[int(series["Id"][1:])]
                storage_type = next((d["Value"] for d in metric["Dimensions"] if d["Name"] == "StorageType"), "?")
                if metric["MetricName"] == "NumberOfObjects":
                    result.object_count = int(series["Values"][0])
                else:
                    result.size_by_storage_type[storage_type] = int(series["Values"][0])
                result.as_of = max(filter(None, [result.as_of, series["Timestamps"][0]]))
        result.size_by_storage_type = dict(sorted(result.size_by_storage_type.items(), key=lambda kv: -kv[1]))
        return result

    # ------------------------------------------------------------------ listing

    def iter_objects(self, uri: str, *, limit: int | None = None,
                     progress: Callable[[int], None] | None = None) -> Iterator[ObjectInfo]:
        """Stream every object under `uri` (recursive), 1000 per request, in key order."""
        bucket, prefix = parse_s3_uri(uri)
        seen = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield ObjectInfo(bucket, item["Key"], item["Size"], item["LastModified"],
                                 item.get("StorageClass", "STANDARD"), item.get("ETag", "").strip('"'))
                seen += 1
                if limit is not None and seen >= limit:
                    return
            if progress:
                progress(seen)

    def list_objects(self, uri: str, *, limit: int | None = None) -> list[ObjectInfo]:
        return list(self.iter_objects(uri, limit=limit))

    def ls(self, uri: str, *, limit: int = 1000) -> Listing:
        """Sub-folders and files directly under `uri` (one level). Fast even on huge buckets."""
        bucket, prefix = parse_s3_uri(uri)
        listing = self._ls(bucket, prefix, limit)
        # 's3://b/data' almost always means the folder 'data/' - follow it when that's the only match
        if prefix and not prefix.endswith("/") and not listing.objects and listing.folders == [prefix + "/"]:
            listing = self._ls(bucket, prefix + "/", limit)
        return listing

    def _ls(self, bucket: str, prefix: str, limit: int) -> Listing:
        listing = Listing(uri=s3_uri(bucket, prefix))
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")
        for page in pages:
            listing.folders += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
            listing.objects += [
                ObjectInfo(bucket, o["Key"], o["Size"], o["LastModified"], o.get("StorageClass", "STANDARD"),
                           o.get("ETag", "").strip('"'))
                for o in page.get("Contents", []) if o["Key"] != prefix
            ]
            found = len(listing.folders) + len(listing.objects)
            if found >= limit:
                listing.truncated = bool(page.get("IsTruncated")) or found > limit
                break
        listing.folders = listing.folders[:limit]
        listing.objects = listing.objects[: limit - len(listing.folders)]
        return listing

    # ----------------------------------------------------------------- analysis

    def summarize(self, uri: str, *, top_n: int = 10, folder_depth: int = 1, limit: int | None = None,
                  progress: Callable[[int], None] | None = None) -> PrefixSummary:
        """One pass over the prefix: totals, file types, storage classes, folders, size/age histograms, largest."""
        scan = self.iter_objects(uri, limit=None if limit is None else limit + 1, progress=progress)
        return summarize_objects(scan, uri, top_n=top_n, folder_depth=folder_depth, limit=limit)

    def folder_tree(self, uri: str, *, depth: int = 2, limit: int | None = None,
                    progress: Callable[[int], None] | None = None) -> FolderTree:
        """Object count and size for every folder down to `depth` levels."""
        scan = self.iter_objects(uri, limit=None if limit is None else limit + 1, progress=progress)
        return build_folder_tree(scan, uri, depth=depth, limit=limit)

    def find(self, uri: str, *, pattern: str | None = None, regex: str | None = None,
             extensions: str | Iterable[str] | None = None, min_size: int | str | None = None,
             max_size: int | str | None = None, modified_after: Any = None, modified_before: Any = None,
             storage_classes: str | Iterable[str] | None = None, limit: int | None = None,
             scan_limit: int | None = None, progress: Callable[[int], None] | None = None) -> list[ObjectInfo]:
        """Objects matching every given filter (see make_filter). Stops after `limit` matches
        or after scanning `scan_limit` keys."""
        keep = make_filter(pattern=pattern, regex=regex, extensions=extensions, min_size=min_size,
                           max_size=max_size, modified_after=modified_after, modified_before=modified_before,
                           storage_classes=storage_classes)
        matches: list[ObjectInfo] = []
        for obj in self.iter_objects(uri, limit=scan_limit, progress=progress):
            if keep(obj):
                matches.append(obj)
                if limit is not None and len(matches) >= limit:
                    break
        return matches

    def largest(self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None) -> list[ObjectInfo]:
        return heapq.nlargest(n, self._files(uri, progress), key=lambda o: o.size)

    def newest(self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None) -> list[ObjectInfo]:
        return heapq.nlargest(n, self._files(uri, progress), key=lambda o: o.last_modified)

    def oldest(self, uri: str, n: int = 20, *, progress: Callable[[int], None] | None = None) -> list[ObjectInfo]:
        return heapq.nsmallest(n, self._files(uri, progress), key=lambda o: o.last_modified)

    def _files(self, uri: str, progress: Callable[[int], None] | None) -> Iterator[ObjectInfo]:
        return (o for o in self.iter_objects(uri, progress=progress) if not o.is_folder_marker)

    def find_duplicates(self, uri: str, *, min_size: int | str = 1, limit: int | None = None,
                        progress: Callable[[int], None] | None = None) -> list[list[ObjectInfo]]:
        """Groups of objects with identical size + ETag (see find_duplicate_groups for caveats)."""
        return find_duplicate_groups(self.iter_objects(uri, limit=limit, progress=progress), min_size=min_size)

    def compare(self, uri_a: str, uri_b: str, *, progress: Callable[[int], None] | None = None) -> CompareResult:
        """Diff two prefixes (e.g. a copy/sync source and target) by relative key, size and ETag."""
        bucket_a, prefix_a = parse_s3_uri(uri_a)
        bucket_b, prefix_b = parse_s3_uri(uri_b)
        return compare_objects(self.iter_objects(uri_a, progress=progress), self.iter_objects(uri_b, progress=progress),
                               prefix_a=prefix_a, prefix_b=prefix_b,
                               uri_a=s3_uri(bucket_a, prefix_a), uri_b=s3_uri(bucket_b, prefix_b))

    def version_stats(self, uri: str, *, top_n: int = 10, limit: int | None = None,
                      progress: Callable[[int], None] | None = None) -> VersionStats:
        """Current vs noncurrent versions and delete markers - the hidden cost of versioned buckets."""
        bucket, prefix = parse_s3_uri(uri)
        stats = VersionStats(uri=s3_uri(bucket, prefix))
        noncurrent_by_key: dict[str, Stat] = defaultdict(Stat)
        seen = 0
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
            for version in page.get("Versions", []):
                if version["IsLatest"]:
                    stats.current.add(version["Size"])
                else:
                    stats.noncurrent.add(version["Size"])
                    noncurrent_by_key[version["Key"]].add(version["Size"])
            for marker in page.get("DeleteMarkers", []):
                stats.delete_markers += 1
                stats.deleted_keys += marker["IsLatest"]
            seen += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
            if progress:
                progress(seen)
            if limit is not None and seen >= limit:
                stats.truncated = bool(page.get("IsTruncated"))
                break
        stats.top_noncurrent = heapq.nlargest(top_n, noncurrent_by_key.items(), key=lambda kv: kv[1].size)
        return stats

    def object_versions(self, uri: str) -> list[ObjectVersion]:
        """Full version history of one key, newest first (includes delete markers)."""
        bucket, key = parse_s3_uri(uri)
        history: list[ObjectVersion] = []
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=key):
            history += [ObjectVersion(key, v["VersionId"], v["IsLatest"], v["LastModified"], v["Size"],
                                      storage_class=v.get("StorageClass"))
                        for v in page.get("Versions", []) if v["Key"] == key]
            history += [ObjectVersion(key, m["VersionId"], m["IsLatest"], m["LastModified"], is_delete_marker=True)
                        for m in page.get("DeleteMarkers", []) if m["Key"] == key]
        return sorted(history, key=lambda v: (v.last_modified, v.is_latest), reverse=True)

    def incomplete_uploads(self, uri: str, *, with_sizes: bool = False) -> list[MultipartUpload]:
        """Multipart uploads that were started but never completed/aborted - their parts are billed
        but invisible in normal listings. with_sizes=True adds one ListParts call per upload."""
        bucket, prefix = parse_s3_uri(uri)
        uploads: list[MultipartUpload] = []
        for page in self.client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Uploads", []):
                upload = MultipartUpload(bucket, item["Key"], item["UploadId"], item["Initiated"],
                                         item.get("StorageClass", "STANDARD"))
                if with_sizes:
                    parts = [part for p in self.client.get_paginator("list_parts").paginate(
                        Bucket=bucket, Key=item["Key"], UploadId=item["UploadId"]) for part in p.get("Parts", [])]
                    upload.parts, upload.size = len(parts), sum(part["Size"] for part in parts)
                uploads.append(upload)
        return sorted(uploads, key=lambda u: u.initiated)

    # ------------------------------------------------------------------ objects

    def head(self, uri: str) -> dict[str, Any]:
        """Object metadata: size, type, storage class, encryption, version, restore/lock status, user metadata, tags."""
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
        return {t["Key"]: t["Value"] for t in self.client.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", [])}

    def exists(self, uri: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def open(self, uri: str, *, decompress: bool = True) -> io.BufferedIOBase:
        """Streaming binary reader (use as a context manager). .gz/.bz2/.xz are decompressed on the fly."""
        bucket, key = parse_s3_uri(uri)
        body = self.client.get_object(Bucket=bucket, Key=key)["Body"]
        stream = io.BufferedReader(_BodyReader(body), buffer_size=256 * KB)
        compression = detect_format(key)[1]
        if not (decompress and compression):
            return stream
        reader = _DECOMPRESSORS[compression](stream)
        close_reader = reader.close

        def close() -> None:  # decompressors don't close a file object they were handed
            try:
                close_reader()
            finally:
                stream.close()

        reader.close = close
        return reader

    def read_bytes(self, uri: str, start: int | None = None, end: int | None = None) -> bytes:
        """Raw bytes (not decompressed). start/end are inclusive byte offsets for a ranged GET."""
        bucket, key = parse_s3_uri(uri)
        extra = {"Range": f"bytes={start or 0}-{'' if end is None else end}"} if start or end is not None else {}
        return self.client.get_object(Bucket=bucket, Key=key, **extra)["Body"].read()

    def _read_head(self, uri: str, max_bytes: int) -> tuple[bytes, bool]:
        """First max_bytes of the (decompressed) object, and whether there was more."""
        with self.open(uri) as stream:
            data = stream.read(max_bytes + 1)
        return data[:max_bytes], len(data) > max_bytes

    def read_text(self, uri: str, *, max_bytes: int = MB, encoding: str = "utf-8") -> str:
        """Decoded text of the first `max_bytes` (decompressed) bytes."""
        return self._read_head(uri, max_bytes)[0].decode(encoding, errors="replace")

    def read_lines(self, uri: str, n: int = 20, *, encoding: str = "utf-8", max_line_chars: int = 100_000) -> list[str]:
        """First n lines; downloads only as much as needed."""
        lines: list[str] = []
        with io.TextIOWrapper(self.open(uri), encoding=encoding, errors="replace") as text:
            while len(lines) < n:
                line = text.readline(max_line_chars)
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        return lines

    def read_json(self, uri: str) -> Any:
        """Parse a whole JSON document (reads the full object)."""
        with self.open(uri) as stream:
            return json.load(stream)

    def read_jsonl(self, uri: str, n: int | None = None, *, encoding: str = "utf-8") -> list[Any]:
        """Parse JSON-lines records; n limits how many (only that much is downloaded)."""
        records: list[Any] = []
        with io.TextIOWrapper(self.open(uri), encoding=encoding) as text:
            for line in text:
                if line.strip():
                    records.append(json.loads(line))
                    if n is not None and len(records) >= n:
                        break
        return records

    def read_df(self, uri: str, *, nrows: int | None = None, columns: list[str] | None = None,
                fmt: str | None = None, **kwargs: Any):
        """Load csv / tsv / json / jsonl / parquet (optionally .gz/.bz2/.xz) into a pandas DataFrame.
        nrows reads just the first rows; for parquet only the needed row group is fetched.
        Extra kwargs go to pandas.read_csv for csv/tsv."""
        pd = _require("pandas", "read_df")
        bucket, key = parse_s3_uri(uri)
        fmt = fmt or detect_format(key)[0]
        if fmt == "parquet":
            pq = _require("pyarrow.parquet", "Reading parquet")
            with self._seekable(bucket, key) as handle:
                parquet = pq.ParquetFile(handle)
                if nrows is None:
                    return parquet.read(columns=columns).to_pandas()
                batch = next(parquet.iter_batches(batch_size=max(nrows, 1), columns=columns), None)
                return (batch.slice(0, nrows) if batch is not None else parquet.schema_arrow.empty_table()).to_pandas()
        if fmt in ("csv", "tsv"):
            kwargs.setdefault("sep", "\t" if fmt == "tsv" else ",")
            with self.open(uri) as stream:
                return pd.read_csv(stream, nrows=nrows, usecols=columns, **kwargs)
        if fmt in ("json", "jsonl"):
            records = self.read_jsonl(uri, n=nrows) if fmt == "jsonl" else self.read_json(uri)
            records = records if isinstance(records, list) else [records]
            records = records if nrows is None else records[:nrows]
            if records and all(isinstance(r, dict) for r in records):
                frame = pd.json_normalize(records)
            else:
                frame = pd.DataFrame({"value": records})
            return frame[columns] if columns else frame
        raise ValueError(f"Can't tell how to read {key!r} as a table; pass fmt='csv'|'tsv'|'json'|'jsonl'|'parquet'")

    def _seekable(self, bucket: str, key: str) -> io.BufferedReader:
        size = self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        return io.BufferedReader(_RangeReader(self.client, bucket, key, size), buffer_size=MB)

    def parquet_info(self, uri: str) -> dict[str, Any]:
        """Row count, row groups, schema and compression from the parquet footer (a few KB downloaded)."""
        pq = _require("pyarrow.parquet", "Reading parquet")
        bucket, key = parse_s3_uri(uri)
        with self._seekable(bucket, key) as handle:
            parquet = pq.ParquetFile(handle)
            meta = parquet.metadata
            return {
                "rows": meta.num_rows,
                "row_groups": meta.num_row_groups,
                "columns": [(f.name, str(f.type)) for f in parquet.schema_arrow],
                "compression": meta.row_group(0).column(0).compression if meta.num_row_groups and meta.num_columns else None,
                "created_by": meta.created_by,
            }

    def preview(self, uri: str, n: int = 20, *, max_bytes: int = 512 * KB) -> Preview:
        """Best-effort look at an object: DataFrame for tabular files, parsed JSON, text lines,
        image bytes or a binary sample. Downloads only what it needs."""
        bucket, key = parse_s3_uri(uri)
        meta = self.client.head_object(Bucket=bucket, Key=key)
        fmt, compression = detect_format(key)
        preview = Preview(uri=s3_uri(bucket, key), kind="text", size=meta["ContentLength"], format=fmt,
                          compression=compression, content_type=meta.get("ContentType"))
        storage_class = meta.get("StorageClass", "STANDARD")
        if storage_class in ARCHIVE_CLASSES and 'ongoing-request="false"' not in meta.get("Restore", ""):
            preview.kind, preview.note = "unavailable", f"Object is in {storage_class}; restore it before reading."
            return preview
        if fmt is None and (preview.content_type or "").startswith("image/"):
            fmt = preview.format = "image"
        try:
            if fmt in ("csv", "tsv", "jsonl", "parquet"):
                preview.kind, preview.data = "table", self.read_df(uri, nrows=n)
                if fmt == "parquet":
                    preview.info = self.parquet_info(uri)
                return preview
            if fmt == "image" and preview.size <= 10 * MB:
                preview.kind, preview.data = "image", self.read_bytes(uri)
                return preview
            data, preview.truncated = self._read_head(uri, max_bytes)
            if fmt == "json":
                if not preview.truncated:
                    try:
                        parsed = json.loads(data)
                    except ValueError:
                        pass  # maybe JSON lines with a .json name - tried below
                    else:
                        if isinstance(parsed, list) and parsed and all(isinstance(r, dict) for r in parsed):
                            pd = _require("pandas", "Table preview")
                            preview.kind, preview.data = "table", pd.json_normalize(parsed[:n])
                            preview.info["records"] = len(parsed)
                        else:
                            preview.kind, preview.data = "json", parsed
                        return preview
                try:  # Firehose / Spark often write JSON lines into '.json' files
                    preview.kind, preview.data = "table", self.read_df(uri, nrows=n, fmt="jsonl")
                    preview.format = "jsonl"
                    return preview
                except ValueError:
                    preview.kind = "text"
                    preview.note = (f"JSON is larger than the {human_size(max_bytes)} preview window; showing raw text."
                                    if preview.truncated else "Not valid JSON; showing raw text.")
            if _looks_binary(data):
                preview.kind, preview.data = "binary", data[:512]
                return preview
            lines = data.decode("utf-8", errors="replace").splitlines()
            preview.truncated = preview.truncated or len(lines) > n
            preview.data = lines[:n]
        except (ValueError, ImportError) as exc:  # parse errors, missing pandas/pyarrow: fall back to raw text
            data, preview.truncated = self._read_head(uri, min(max_bytes, 64 * KB))
            preview.kind = "binary" if _looks_binary(data) else "text"
            preview.data = data[:512] if preview.kind == "binary" else data.decode("utf-8", "replace").splitlines()[:n]
            preview.note = f"Couldn't parse as {fmt}: {exc}"
        return preview

    def presigned_url(self, uri: str, *, expires: int = 3600) -> str:
        """Temporary HTTPS link to download the object without AWS credentials."""
        bucket, key = parse_s3_uri(uri)
        return self._client_for(bucket).generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)

    def download(self, uri: str, path: str | None = None) -> str:
        """Download to `path` (a file or directory; default: current directory). Returns the local path."""
        bucket, key = parse_s3_uri(uri)
        path = path or os.path.basename(key)
        if os.path.isdir(path):
            path = os.path.join(path, os.path.basename(key))
        self.client.download_file(bucket, key, path)
        return os.path.abspath(path)


# =============================================================================
# 5. S3View - notebook UI layer (renders what S3Analyzer returns)
# =============================================================================


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[tuple[str, str]]


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""


@dataclass
class _Frame:
    df: Any
    title: str = ""


@dataclass
class _Image:
    data: bytes
    mime: str


@dataclass
class _Link:
    url: str
    label: str


_CSS = """<style>
.s3a{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.s3a h3{margin:10px 0 2px;font-size:16px}
.s3a h4{margin:14px 0 4px;font-size:13px}
.s3a .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.s3a .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.s3a .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.s3a .card .l{font-size:11px;opacity:.65}
.s3a .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.s3a .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.s3a table.t{border-collapse:collapse;width:auto;font-size:inherit}
.s3a table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.s3a table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.s3a table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.s3a table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.s3a table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.s3a table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.s3a .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.s3a .track{vertical-align:middle;margin-right:6px}
.s3a .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.s3a .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.s3a .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.s3a .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.s3a .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.s3a pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.s3a img{max-width:100%;max-height:480px;border:1px solid rgba(127,127,127,.3)}
</style>"""

_NUMERIC_RE = re.compile(r"^-?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _render_html(blocks: list[Any], max_rows: int) -> str:
    out = [_CSS, '<div class="s3a">']
    for block in blocks:
        if isinstance(block, _Title):
            out.append(f"<h3>{_esc(block.text)}</h3>")
            if block.sub:
                out.append(f'<div class="sub">{_esc(block.sub)}</div>')
        elif isinstance(block, _Cards):
            cards = "".join(f'<div class="card"><div class="l">{_esc(label)}</div><div class="v">{_esc(value)}</div></div>'
                            for label, value in block.items)
            out.append(f'<div class="cards">{cards}</div>')
        elif isinstance(block, _Note):
            out.append(f'<div class="note {block.level}">{_esc(block.text)}</div>')
        elif isinstance(block, _Table):
            if block.title:
                out.append(f"<h4>{_esc(block.title)}</h4>")
            if not block.rows:
                out.append('<div class="more">(none)</div>')
                continue
            rows, hidden = _visible_rows(block, max_rows)
            head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
            head += f"<th>{_esc(block.bar_label)}</th>" if block.bars is not None else ""
            body = []
            for i, row in enumerate(rows):
                cells = []
                for j, cell in enumerate(row):
                    text = "" if cell is None else str(cell)
                    css = "tree" if block.tree and j == 0 else ("n" if _NUMERIC_RE.match(text) else "")
                    cells.append(f'<td class="{css}">{_esc(text)}</td>' if css else f"<td>{_esc(text)}</td>")
                if block.bars is not None:
                    pct = max(0.0, min(1.0, block.bars[i])) * 100
                    cells.append(f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                                 f"</span></span>{pct:.1f}%</td>")
                body.append(f"<tr>{''.join(cells)}</tr>")
            out.append(f'<div class="tw"><table class="t"><thead><tr>{head}</tr></thead>'
                       f'<tbody>{"".join(body)}</tbody></table></div>')
            if hidden:
                out.append(f'<div class="more">... {hidden:,} more rows not shown</div>')
        elif isinstance(block, _Text):
            if block.title:
                out.append(f"<h4>{_esc(block.title)}</h4>")
            out.append(f"<pre>{_esc(block.text)}</pre>")
        elif isinstance(block, _Frame):
            if block.title:
                out.append(f"<h4>{_esc(block.title)}</h4>")
            pd = _require("pandas", "Table rendering")
            with pd.option_context("display.max_colwidth", 120):
                frame = block.df.to_html(max_rows=max_rows or None, max_cols=40, border=0, classes="t")
                out.append(f'<div class="tw">{frame}</div>')
        elif isinstance(block, _Image):
            import base64

            out.append(f'<img src="data:{_esc(block.mime)};base64,{base64.b64encode(block.data).decode()}">')
        elif isinstance(block, _Link):
            out.append(f'<a href="{_esc(block.url)}" target="_blank" rel="noopener">{_esc(block.label)}</a>')
    out.append("</div>")
    return "".join(out)


def _text_bar(fraction: float, width: int = 20) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled) + f" {fraction * 100:5.1f}%"


def _clip(text: str, width: int = 90) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _render_text(blocks: list[Any], max_rows: int) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Title):
            out += ["", block.text, "=" * min(len(block.text), 100)] + ([block.sub] if block.sub else [])
        elif isinstance(block, _Cards):
            line = ""
            for label, value in block.items:
                item = f"{label}: {value}"
                if line and len(line) + len(item) > 100:
                    out.append(line)
                    line = ""
                line += ("   " if line else "") + item
            out.append(line)
        elif isinstance(block, _Note):
            out.append({"warn": "[!] ", "ok": "[ok] "}.get(block.level, "[i] ") + block.text)
        elif isinstance(block, _Table):
            out.append("")
            if block.title:
                out.append(f"-- {block.title} --")
            if not block.rows:
                out.append("(none)")
                continue
            rows, hidden = _visible_rows(block, max_rows)
            headers = list(block.headers) + ([block.bar_label] if block.bars is not None else [])
            cells = [[_clip(("" if c is None else str(c)).replace("\n", ", ")) for c in row]
                     + ([_text_bar(block.bars[i])] if block.bars is not None else []) for i, row in enumerate(rows)]
            widths = [max([len(h)] + [len(r[j]) for r in cells]) for j, h in enumerate(headers)]

            def line_of(values: list[str], widths: list[int] = widths) -> str:
                return "  ".join(v.rjust(w) if _NUMERIC_RE.match(v) else v.ljust(w)
                                 for v, w in zip(values, widths)).rstrip()

            out += [line_of(headers), "  ".join("-" * w for w in widths)] + [line_of(r) for r in cells]
            if hidden:
                out.append(f"... {hidden:,} more rows not shown")
        elif isinstance(block, _Text):
            if block.title:
                out += ["", f"-- {block.title} --"]
            out.append(block.text)
        elif isinstance(block, _Frame):
            if block.title:
                out += ["", f"-- {block.title} --"]
            out.append(block.df.to_string(max_rows=max_rows or None, max_cols=20))
        elif isinstance(block, _Image):
            out.append(f"(image, {human_size(len(block.data))} - open in a notebook to see it)")
        elif isinstance(block, _Link):
            out += [block.label, block.url]
    return "\n".join(out)


def _in_notebook() -> bool:
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and type(shell).__name__ != "TerminalInteractiveShell"


def _fmt_dt(moment: datetime | None) -> str:
    return "-" if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _stat_table(title: str, label: str, stats: dict[str, Stat], total_count: int, total_size: int, *,
                by: str = "size", name: Callable[[str], str] = str) -> _Table:
    rows = [[name(key), f"{st.count:,}", human_size(st.size)] for key, st in stats.items()]
    bars = [_share(st.size, total_size) if by == "size" else _share(st.count, total_count) for st in stats.values()]
    return _Table([label, "Objects", "Size"], rows, title=title, bars=bars,
                  bar_label="% of size" if by == "size" else "% of objects")


def _objects_table(title: str, objects: list[ObjectInfo], base: str = "") -> _Table:
    return _Table(["Key", "Size", "Last modified (UTC)", "Age", "Storage class"],
                  [[relative_key(o.key, base), human_size(o.size), _fmt_dt(o.last_modified), human_age(o.last_modified),
                    o.storage_class] for o in objects], title=title)


def _folder_label(folder: str) -> str:
    return folder or "(files at this level)"


def _hexdump(data: bytes) -> str:
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {chunk.hex(' '):<47}  {ascii_}")
    return "\n".join(lines)


def _encryption_label(cfg: BucketConfig) -> str:
    if "encryption" in cfg.errors:
        return f"? ({cfg.errors['encryption']})"
    labels = {"AES256": "SSE-S3", "aws:kms": "SSE-KMS", "aws:kms:dsse": "DSSE-KMS"}
    label = labels.get(cfg.encryption or "", cfg.encryption or "none")
    return label + (" + bucket key" if cfg.bucket_key_enabled else "")


def _public_access_label(cfg: BucketConfig) -> str:
    if "public_access_block" in cfg.errors:
        return f"? ({cfg.errors['public_access_block']})"
    settings = cfg.public_access_block
    if not settings:
        return "not set"
    on = sum(bool(v) for v in settings.values())
    return "all on" if on == len(settings) else f"{on}/{len(settings)} on"


def _section(cfg: BucketConfig, section: str, text: str) -> str:
    return f"? ({cfg.errors[section]})" if section in cfg.errors else text


def _rule_scope(rule: dict) -> str:
    filt = rule.get("Filter")
    if filt is None:
        return rule.get("Prefix") or "whole bucket"  # legacy rule format
    filt = filt.get("And", filt)
    bits = [f"prefix '{filt['Prefix']}'"] if filt.get("Prefix") else []
    tags = filt.get("Tags") or ([filt["Tag"]] if "Tag" in filt else [])
    bits += [f"tag {t['Key']}={t['Value']}" for t in tags]
    if "ObjectSizeGreaterThan" in filt:
        bits.append(f"size > {human_size(filt['ObjectSizeGreaterThan'])}")
    if "ObjectSizeLessThan" in filt:
        bits.append(f"size < {human_size(filt['ObjectSizeLessThan'])}")
    return ", ".join(bits) or "whole bucket"


def _rule_actions(rule: dict) -> str:
    def when(spec: dict) -> str:
        if "Days" in spec:
            return f"{spec['Days']} days"
        moment = spec.get("Date")
        return f"{moment:%Y-%m-%d}" if isinstance(moment, (date, datetime)) else str(moment)

    actions = [f"to {t['StorageClass']} after {when(t)}" for t in rule.get("Transitions", [])]
    expiration = rule.get("Expiration", {})
    if "Days" in expiration or "Date" in expiration:
        actions.append(f"expire after {when(expiration)}")
    if expiration.get("ExpiredObjectDeleteMarker"):
        actions.append("remove expired delete markers")
    actions += [f"noncurrent to {t['StorageClass']} after {t.get('NoncurrentDays')} days"
                for t in rule.get("NoncurrentVersionTransitions", [])]
    if "NoncurrentVersionExpiration" in rule:
        spec = rule["NoncurrentVersionExpiration"]
        keep = f" (keep newest {spec['NewerNoncurrentVersions']})" if spec.get("NewerNoncurrentVersions") else ""
        actions.append(f"delete noncurrent after {spec.get('NoncurrentDays')} days{keep}")
    if "AbortIncompleteMultipartUpload" in rule:
        actions.append(f"abort incomplete uploads after {rule['AbortIncompleteMultipartUpload']['DaysAfterInitiation']} days")
    return "\n".join(actions) or "-"


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS / input errors as a readable note instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: S3View, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            if code in ("404", "NoSuchKey", "NotFound"):
                message = "object not found"
            self._show([_Note(f"{code}: {message}  [{method.__name__}]", "warn")])
        except (BotoCoreError, ValueError, ImportError) as exc:
            self._show([_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")])

    return wrapper


class S3View:
    """Notebook UI over S3Analyzer. Each method renders a report and returns nothing;
    for the underlying data call the same-named method on `view.core` (an S3Analyzer).

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    """

    def __init__(self, core: S3Analyzer | None = None, *, mode: str = "auto", max_rows: int = 50):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        self.core = core or S3Analyzer()
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            from IPython.display import HTML, display

            display(HTML(_render_html(blocks, self.max_rows)))
        else:
            print(_render_text(blocks, self.max_rows))

    @contextmanager
    def _progress(self, label: str = "Scanning") -> Iterator[Callable[[int], None]]:
        last_update = [0.0]
        handle = None
        if self.use_html:
            from IPython.display import HTML, display

            handle = display(HTML(""), display_id=True)

        def tick(count: int) -> None:
            now = time.monotonic()
            if now - last_update[0] < 0.5:
                return
            last_update[0] = now
            if handle is not None:
                handle.update(HTML(f'<div style="opacity:.6">{_esc(label)}... {count:,} objects</div>'))
            else:
                print(f"\r{label}... {count:,} objects", end="", file=sys.stderr, flush=True)

        try:
            yield tick
        finally:
            if handle is not None:
                handle.update(HTML(""))
            elif last_update[0]:
                print("\r" + " " * 50 + "\r", end="", file=sys.stderr, flush=True)

    def help(self) -> None:
        """This list."""
        rows = []
        for name, member in vars(type(self)).items():
            if name.startswith("_") or not callable(member):
                continue
            target = inspect.unwrap(member)
            params = str(inspect.signature(target)).replace("(self, ", "(").replace("(self)", "()")
            rows.append([f"{name}{params}", (inspect.getdoc(target) or "").split("\n")[0]])
        self._show([_Title("S3View commands", "Data versions of each live on .core (S3Analyzer)"),
                    _Table(["Command", "What it shows"], rows, max_rows=0)])

    # ------------------------------------------------------------------ buckets

    @_friendly_errors
    def buckets(self, *, with_region: bool = True) -> None:
        """All buckets in the account with region, creation date and age."""
        buckets = sorted(self.core.list_buckets(with_region=with_region), key=lambda b: b.name)
        regions = Counter(b.region or "unknown" for b in buckets)
        self._show([
            _Title(f"S3 buckets ({len(buckets)})", " · ".join(f"{r}: {n}" for r, n in regions.most_common())),
            _Table(["Bucket", "Region", "Created (UTC)", "Age"],
                   [[b.name, b.region or "-", _fmt_dt(b.created), human_age(b.created)] for b in buckets], max_rows=0),
        ])

    @_friendly_errors
    def bucket_info(self, bucket: str, *, metrics: bool = True) -> None:
        """Bucket settings, risks, and CloudWatch size / object count (instant, even for huge buckets)."""
        cfg = self.core.bucket_config(bucket)
        cards = [
            ("Region", cfg.region or "?"),
            ("Versioning", _section(cfg, "versioning", cfg.versioning or "?")),
            ("Encryption", _encryption_label(cfg)),
            ("Block public access", _public_access_label(cfg)),
            ("Bucket policy", _section(cfg, "policy", "public" if cfg.policy_is_public else
                                       "private" if cfg.has_policy else "none")),
            ("Object ownership", _section(cfg, "ownership", cfg.object_ownership or "-")),
            ("Object lock", _section(cfg, "object_lock", "on" if cfg.object_lock else "off")),
            ("Lifecycle rules", _section(cfg, "lifecycle", str(len(cfg.lifecycle_rules)))),
            ("Replication rules", _section(cfg, "replication", str(len(cfg.replication_rules)))),
            ("Access logging", _section(cfg, "logging", cfg.logging_target or "off")),
            ("Inventory", _section(cfg, "inventory", ", ".join(cfg.inventory_configs) or "none")),
        ]
        blocks: list[Any] = [_Title(f"Bucket s3://{cfg.name}", f"region {cfg.region or 'unknown'}")]
        storage_table = None
        if metrics:
            try:
                usage = self.core.bucket_metrics(cfg.name)
            except (ClientError, BotoCoreError) as exc:
                blocks.append(_Note(f"CloudWatch metrics unavailable: {exc}", "warn"))
            else:
                if usage.as_of is None:
                    blocks.append(_Note("No CloudWatch storage metrics yet (published once a day; "
                                        "new or empty buckets have none)."))
                else:
                    count = "-" if usage.object_count is None else f"{usage.object_count:,}"
                    cards = [("Objects", count), ("Total size", human_size(usage.total_size))] + cards
                    total = usage.total_size
                    storage_table = _Table(
                        ["Storage type", "Size"],
                        [[kind, human_size(size)] for kind, size in usage.size_by_storage_type.items()],
                        title=f"Size by storage type (CloudWatch, {_fmt_dt(usage.as_of)} UTC)",
                        bars=[_share(size, total) for size in usage.size_by_storage_type.values()],
                        bar_label="% of size")
        blocks.append(_Cards(cards))
        blocks += [_Note(message, level) for level, message in bucket_findings(cfg)]
        if storage_table:
            blocks.append(storage_table)
        if cfg.lifecycle_rules:
            blocks.append(_Table(["Rule", "Status", "Applies to", "Actions"],
                                 [[r.get("ID", "-"), r.get("Status"), _rule_scope(r), _rule_actions(r)]
                                  for r in cfg.lifecycle_rules], title="Lifecycle rules"))
        if cfg.replication_rules:
            blocks.append(_Table(["Rule", "Status", "Destination"],
                                 [[r.get("ID", "-"), r.get("Status"), r.get("Destination", {}).get("Bucket", "-")]
                                  for r in cfg.replication_rules], title="Replication rules"))
        if cfg.tags:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(cfg.tags.items())], title="Tags"))
        self._show(blocks)

    # ------------------------------------------------------------------ listing

    @_friendly_errors
    def ls(self, uri: str, *, limit: int = 500) -> None:
        """Folders and files directly under a prefix (one level, fast)."""
        listing = self.core.ls(uri, limit=limit)
        base = base_prefix(parse_s3_uri(listing.uri)[1])
        rows = [[f"📁 {relative_key(folder, base)}", "", "", ""] for folder in listing.folders]
        rows += [[relative_key(o.key, base), human_size(o.size), _fmt_dt(o.last_modified), o.storage_class]
                 for o in listing.objects]
        blocks: list[Any] = [
            _Title(f"ls {listing.uri}"),
            _Cards([("Folders", f"{len(listing.folders):,}"), ("Files", f"{len(listing.objects):,}"),
                    ("Size of files here", human_size(sum(o.size for o in listing.objects)))]),
        ]
        if listing.truncated:
            blocks.append(_Note(f"Showing the first {limit:,} entries; pass limit= for more.", "warn"))
        if not rows:
            blocks.append(_Note("Nothing here. Check the prefix (keys are case-sensitive)."))
        else:
            blocks.append(_Table(["Name", "Size", "Last modified (UTC)", "Storage class"], rows, max_rows=0))
        self._show(blocks)

    @_friendly_errors
    def summary(self, uri: str, *, top_n: int = 10, folder_depth: int = 1, limit: int | None = None) -> None:
        """Full dashboard for a prefix: totals, findings, folders, file types, storage classes,
        size and age distribution, largest objects. Lists every key once (use limit= on huge prefixes)."""
        with self._progress() as tick:
            s = self.core.summarize(uri, top_n=top_n, folder_depth=folder_depth, limit=limit, progress=tick)
        base = base_prefix(parse_s3_uri(s.uri)[1])
        blocks: list[Any] = [_Title(f"Summary of {s.uri}", f"{s.object_count:,} objects scanned in {s.scan_seconds:.1f}s")]
        if not s.object_count:
            blocks.append(_Note("No objects under this prefix."))
            blocks += [_Note(message, level) for level, message in summary_findings(s)]
            return self._show(blocks)
        blocks.append(_Cards([
            ("Objects", f"{s.object_count:,}{'+' if s.truncated else ''}"),
            ("Total size", human_size(s.total_size)),
            ("Average size", human_size(s.avg_size)),
            ("Largest", human_size(s.max_size)),
            ("Smallest", human_size(s.min_size)),
            ("Empty files", f"{s.empty_count:,}"),
            ("File types", f"{len(s.by_extension):,}"),
            ("Oldest change", human_age(s.oldest.last_modified if s.oldest else None)),
            ("Newest change", human_age(s.newest.last_modified if s.newest else None)),
        ]))
        blocks += [_Note(message, level) for level, message in summary_findings(s)]
        n, size = s.object_count, s.total_size
        blocks += [
            _stat_table(f"Folders (depth {folder_depth})", "Folder", s.by_folder, n, size, name=_folder_label),
            _stat_table("File types", "Extension", s.by_extension, n, size),
            _stat_table("Storage classes", "Storage class", s.by_storage_class, n, size),
            _stat_table("Object size distribution", "Size band", s.size_histogram, n, size, by="count"),
            _stat_table("Last modified", "Age", s.age_histogram, n, size, by="count"),
            _objects_table(f"Largest {len(s.largest)} objects", s.largest, base),
        ]
        self._show(blocks)

    @_friendly_errors
    def tree(self, uri: str, *, depth: int = 2, limit: int | None = None, max_rows: int = 300) -> None:
        """Folder tree with object count and size at every level down to `depth`."""
        with self._progress() as tick:
            tree = self.core.folder_tree(uri, depth=depth, limit=limit, progress=tick)
        rows, bars = [], []
        for path, st in tree.folders.items():
            parts = path.rstrip("/").split("/")
            label = "(files at this level)" if not path else "    " * (len(parts) - 1) + parts[-1] + "/"
            rows.append([label, f"{st.count:,}", human_size(st.size)])
            bars.append(_share(st.size, tree.total.size))
        blocks: list[Any] = [_Title(f"Folder tree of {tree.uri}",
                                    f"{tree.total.count:,} objects · {human_size(tree.total.size)} · depth {depth}")]
        if tree.truncated:
            blocks.append(_Note(f"Scan stopped at limit={limit:,}; sizes are partial.", "warn"))
        blocks.append(_Table(["Folder", "Objects", "Size"], rows, bars=bars, bar_label="% of size",
                             tree=True, max_rows=max_rows))
        self._show(blocks)

    @_friendly_errors
    def find(self, uri: str, *, pattern: str | None = None, regex: str | None = None,
             extensions: str | Iterable[str] | None = None, min_size: int | str | None = None,
             max_size: int | str | None = None, modified_after: Any = None, modified_before: Any = None,
             storage_classes: str | Iterable[str] | None = None, limit: int | None = 1000) -> None:
        """Search by glob / regex / extension / size / date / storage class, e.g.
        find(uri, pattern='*.csv', min_size='10MB', modified_after='7d')."""
        filters = {"pattern": pattern, "regex": regex, "extensions": extensions, "min_size": min_size,
                   "max_size": max_size, "modified_after": modified_after, "modified_before": modified_before,
                   "storage_classes": storage_classes}
        filters = {k: v for k, v in filters.items() if v is not None}
        with self._progress() as tick:
            matches = self.core.find(uri, limit=limit, progress=tick, **filters)
        bucket, prefix = parse_s3_uri(uri)
        hit_limit = limit is not None and len(matches) >= limit
        blocks: list[Any] = [
            _Title(f"Find in {s3_uri(bucket, prefix)}", ", ".join(f"{k}={v!r}" for k, v in filters.items()) or "no filters"),
            _Cards([("Matches", f"{len(matches):,}{'+' if hit_limit else ''}"),
                    ("Total size", human_size(sum(o.size for o in matches)))]),
        ]
        if hit_limit:
            blocks.append(_Note(f"Stopped at limit={limit:,} matches.", "warn"))
        blocks.append(_objects_table("Matches", matches, base_prefix(prefix)))
        self._show(blocks)

    @_friendly_errors
    def largest(self, uri: str, n: int = 20) -> None:
        """The n biggest objects under a prefix."""
        with self._progress() as tick:
            self._top(f"Largest {n} objects", uri, self.core.largest(uri, n, progress=tick))

    @_friendly_errors
    def newest(self, uri: str, n: int = 20) -> None:
        """The n most recently modified objects."""
        with self._progress() as tick:
            self._top(f"{n} most recently modified", uri, self.core.newest(uri, n, progress=tick))

    @_friendly_errors
    def oldest(self, uri: str, n: int = 20) -> None:
        """The n least recently modified objects."""
        with self._progress() as tick:
            self._top(f"{n} least recently modified", uri, self.core.oldest(uri, n, progress=tick))

    def _top(self, title: str, uri: str, objects: list[ObjectInfo]) -> None:
        bucket, prefix = parse_s3_uri(uri)
        self._show([_Title(f"{title} in {s3_uri(bucket, prefix)}"), _objects_table("", objects, base_prefix(prefix))])

    @_friendly_errors
    def duplicates(self, uri: str, *, min_size: int | str = 1, limit: int | None = None) -> None:
        """Identical objects (same size + ETag) and how much space removing the copies would save."""
        with self._progress() as tick:
            groups = self.core.find_duplicates(uri, min_size=min_size, limit=limit, progress=tick)
        bucket, prefix = parse_s3_uri(uri)
        base = base_prefix(prefix)
        reclaimable = sum(g[0].size * (len(g) - 1) for g in groups)
        blocks: list[Any] = [
            _Title(f"Duplicates in {s3_uri(bucket, prefix)}", f"objects of at least {human_size(parse_size(min_size))}"),
            _Cards([("Duplicate groups", f"{len(groups):,}"),
                    ("Redundant copies", f"{sum(len(g) - 1 for g in groups):,}"),
                    ("Reclaimable", human_size(reclaimable))]),
            _Note("Matched on size + ETag. Copies uploaded with different multipart settings or SSE-KMS "
                  "have different ETags and won't show up here."),
            _Table(["Size", "Copies", "Reclaimable", "Keys"],
                   [[human_size(g[0].size), str(len(g)), human_size(g[0].size * (len(g) - 1)),
                     "\n".join(relative_key(o.key, base) for o in g)] for g in groups], title="Groups"),
        ]
        self._show(blocks)

    @_friendly_errors
    def compare(self, uri_a: str, uri_b: str, *, show: int = 20) -> None:
        """Diff two prefixes (e.g. verify a copy / sync / migration)."""
        with self._progress() as tick:
            r = self.core.compare(uri_a, uri_b, progress=tick)
        _, prefix_a = parse_s3_uri(r.uri_a)
        blocks: list[Any] = [
            _Title("Compare prefixes", f"A = {r.uri_a}   B = {r.uri_b}"),
            _Cards([("Identical", f"{r.identical:,}"), ("Different", f"{len(r.different):,}"),
                    ("Only in A", f"{len(r.only_in_a):,}"), ("Only in B", f"{len(r.only_in_b):,}"),
                    ("Same size, ETag not comparable", f"{r.unverifiable:,}")]),
        ]
        if r.in_sync:
            blocks.append(_Note("In sync: every key exists on both sides with the same size.", "ok"))
        if r.different:
            blocks.append(_Table(
                ["Key", "Size A", "Size B", "Modified A", "Modified B"],
                [[relative_key(a.key, prefix_a), human_size(a.size), human_size(b.size), _fmt_dt(a.last_modified),
                  _fmt_dt(b.last_modified)] for a, b in r.different[:show]],
                title=f"Different ({len(r.different):,})"))
        if r.only_in_a:
            blocks.append(_objects_table(f"Only in A ({len(r.only_in_a):,})", r.only_in_a[:show], prefix_a))
        if r.only_in_b:
            blocks.append(_objects_table(f"Only in B ({len(r.only_in_b):,})", r.only_in_b[:show],
                                         parse_s3_uri(r.uri_b)[1]))
        self._show(blocks)

    @_friendly_errors
    def versions(self, uri: str, *, limit: int | None = None) -> None:
        """Current vs noncurrent versions and delete markers (hidden storage in versioned buckets)."""
        with self._progress("Listing versions") as tick:
            v = self.core.version_stats(uri, limit=limit, progress=tick)
        blocks: list[Any] = [
            _Title(f"Versions under {v.uri}"),
            _Cards([("Current objects", f"{v.current.count:,}"), ("Current size", human_size(v.current.size)),
                    ("Noncurrent versions", f"{v.noncurrent.count:,}"), ("Noncurrent size", human_size(v.noncurrent.size)),
                    ("Delete markers", f"{v.delete_markers:,}"), ("Deleted keys (still billed)", f"{v.deleted_keys:,}")]),
        ]
        if v.truncated:
            blocks.append(_Note(f"Stopped at limit={limit:,} versions; numbers are partial.", "warn"))
        if v.noncurrent.size:
            extra = _share(v.noncurrent.size, v.current.size)
            blocks.append(_Note(f"Old versions add {human_size(v.noncurrent.size)} ({extra:.0%} on top of current data). "
                                "A NoncurrentVersionExpiration lifecycle rule would clean them up.",
                                "warn" if extra > 0.25 else "info"))
        if v.top_noncurrent:
            blocks.append(_Table(["Key", "Old versions", "Old versions size"],
                                 [[k, f"{st.count:,}", human_size(st.size)] for k, st in v.top_noncurrent],
                                 title="Keys with the most noncurrent data"))
        self._show(blocks)

    @_friendly_errors
    def history(self, uri: str) -> None:
        """Version history of a single object."""
        history = self.core.object_versions(uri)
        rows = [["delete marker" if v.is_delete_marker else human_size(v.size), _fmt_dt(v.last_modified),
                 human_age(v.last_modified), "latest" if v.is_latest else "", v.version_id] for v in history]
        self._show([_Title(f"History of {s3_uri(*parse_s3_uri(uri))}", f"{len(history)} versions"),
                    _Table(["Size", "Modified (UTC)", "Age", "", "Version id"], rows)])

    @_friendly_errors
    def uploads(self, uri: str, *, with_sizes: bool = True) -> None:
        """Incomplete multipart uploads (billed, but invisible in normal listings)."""
        uploads = self.core.incomplete_uploads(uri, with_sizes=with_sizes)
        total = sum(u.size or 0 for u in uploads)
        blocks: list[Any] = [
            _Title(f"Incomplete multipart uploads under {s3_uri(*parse_s3_uri(uri))}"),
            _Cards([("Uploads", f"{len(uploads):,}")] + ([("Stored parts", human_size(total))] if with_sizes else [])),
        ]
        if uploads:
            blocks.append(_Note("These parts cost storage until aborted; add an AbortIncompleteMultipartUpload "
                                "lifecycle rule to clean them up automatically.", "warn"))
        blocks.append(_Table(["Key", "Started (UTC)", "Age", "Parts", "Size", "Upload id"],
                             [[u.key, _fmt_dt(u.initiated), human_age(u.initiated),
                               "-" if u.parts is None else str(u.parts), human_size(u.size), u.upload_id[:16] + "…"]
                              for u in uploads]))
        self._show(blocks)

    # ------------------------------------------------------------------ objects

    @_friendly_errors
    def head(self, uri: str) -> None:
        """All metadata of one object."""
        info = self.core.head(uri)
        skip = {"uri", "metadata", "tags"}
        blocks: list[Any] = [
            _Title(info["uri"]),
            _Cards([("Size", human_size(info.get("size"))), ("Last modified", human_age(info.get("last_modified"))),
                    ("Content type", info.get("content_type", "-")), ("Storage class", info.get("storage_class", "-")),
                    ("Encryption", info.get("encryption", "none"))]),
            _Table(["Field", "Value"],
                   [[k, _fmt_dt(v) + " UTC" if isinstance(v, datetime) else v] for k, v in info.items() if k not in skip],
                   title="System metadata", max_rows=0),
        ]
        if info["metadata"]:
            blocks.append(_Table(["Key", "Value"], [[k, v] for k, v in info["metadata"].items()], title="User metadata"))
        if info["tags"] is None:
            blocks.append(_Note("Tags: no permission to read (s3:GetObjectTagging)."))
        elif info["tags"]:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in info["tags"].items()], title="Tags"))
        self._show(blocks)

    @_friendly_errors
    def preview(self, uri: str, n: int = 20) -> None:
        """Peek inside an object: table for csv/tsv/jsonl/json/parquet (+ parquet schema),
        pretty JSON, text lines, image, or hex dump. Downloads only what it needs."""
        p = self.core.preview(uri, n=n)
        detail = " · ".join(filter(None, [human_size(p.size), p.format or p.content_type,
                                          f"{p.compression}-compressed" if p.compression else ""]))
        blocks: list[Any] = [_Title(f"Preview of {p.uri}", detail)]
        if p.note:
            blocks.append(_Note(p.note, "warn"))
        if p.kind == "table":
            if "rows" in p.info:
                blocks.append(_Cards([("Rows", f"{p.info['rows']:,}"), ("Columns", f"{len(p.info['columns']):,}"),
                                      ("Row groups", f"{p.info['row_groups']:,}"),
                                      ("Compression", str(p.info.get("compression") or "-"))]))
            elif "records" in p.info:
                blocks.append(_Cards([("Records", f"{p.info['records']:,}")]))
            blocks.append(_Frame(p.data, title=f"First {len(p.data):,} rows"))
            if "columns" in p.info:
                blocks.append(_Table(["Column", "Type"], [list(c) for c in p.info["columns"]], title="Schema"))
        elif p.kind == "json":
            text = json.dumps(p.data, indent=2, default=str, ensure_ascii=False)
            blocks.append(_Text(_clip(text, 20_000)))
        elif p.kind == "text":
            blocks.append(_Text("\n".join(p.data or []), title=f"First {len(p.data or [])} lines"))
        elif p.kind == "image":
            blocks.append(_Image(p.data, p.content_type or "image/png"))
        elif p.kind == "binary":
            blocks.append(_Text(_hexdump(p.data), title="Binary content (first 512 bytes)"))
        if p.truncated and p.kind == "text":
            blocks.append(_Note(f"Showing the first {n} lines; pass n= for more."))
        self._show(blocks)

    @_friendly_errors
    def link(self, uri: str, *, expires: int = 3600) -> None:
        """Clickable presigned download link (no AWS login needed until it expires)."""
        url = self.core.presigned_url(uri, expires=expires)
        self._show([_Link(url, f"Download {parse_s3_uri(uri)[1].rsplit('/', 1)[-1]} (valid {expires // 60} min)")])
