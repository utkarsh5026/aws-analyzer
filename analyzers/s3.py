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
    ui.overview()                                   # every bucket: size, cost, security warnings
    ui.policy("my-bucket")                          # bucket policy in plain English
    ui.what_if("s3://my-bucket/logs/", move_after=30, to="STANDARD_IA")   # preview a lifecycle rule
    ui.deleted("s3://my-bucket/data/")              # deleted files you can still restore

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
import math
import mimetypes
import os
import re
import struct
import sys
import tarfile
import time
import zipfile
import zlib
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


def human_money(usd: float | None) -> str:
    """12.345 -> '$12.35', 0.004 -> '<$0.01', 12345.6 -> '$12,346', -3 -> '-$3.00'."""
    if usd is None:
        return "-"
    sign, usd = ("-" if usd < 0 else ""), abs(usd)
    if 0 < usd < 0.01:
        return f"{sign}<$0.01"
    return f"{sign}${usd:,.0f}" if usd >= 1000 else f"{sign}${usd:,.2f}"


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
    "conf", "toml", "sh", "js", "ts", "out", "err", "properties", "r", "scala", "java", "go", "rs",
    "c", "cpp", "h", "css", "jsx", "tsx", "tf", "srt", "vtt",
)
_FORMAT_BY_EXT = {
    "csv": "csv", "tsv": "tsv", "tab": "tsv", "psv": "psv",
    "parquet": "parquet", "pq": "parquet", "orc": "orc", "feather": "arrow", "arrow": "arrow", "ipc": "arrow",
    "avro": "avro", "xlsx": "excel", "xlsm": "excel", "xls": "excel",
    "json": "json", "jsonl": "jsonl", "ndjson": "jsonl", "ipynb": "notebook",
    "zip": "zip", "tar": "tar", "tgz": "tar",
    "npy": "npy", "npz": "npz", "safetensors": "safetensors", "pt": "torch", "pth": "torch", "ckpt": "torch",
    "pkl": "pickle", "pickle": "pickle", "joblib": "pickle",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image", "bmp": "image",
    "wav": "audio", "mp3": "audio", "flac": "audio", "ogg": "audio", "m4a": "audio", "aac": "audio",
    "mp4": "video", "webm": "video", "mov": "video", "m4v": "video", "pdf": "pdf",
    **{ext: "text" for ext in _TEXT_EXTS},
}
_CSV_SEPARATORS = {"csv": ",", "tsv": "\t", "psv": "|"}


def _zstd_reader(stream: Any) -> Any:
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        try:
            zstandard = importlib.import_module("zstandard")
        except ImportError as exc:
            raise ImportError("Reading .zst needs Python 3.14+ or the zstandard package (pip install zstandard)") from exc
        return zstandard.ZstdDecompressor().stream_reader(stream, read_across_frames=True)
    return zstd.ZstdFile(stream)


_DECOMPRESSORS: dict[str, Callable[[Any], Any]] = {
    "gz": lambda f: gzip.GzipFile(fileobj=f),
    "bz2": bz2.BZ2File,
    "xz": lzma.LZMAFile,
    "zst": _zstd_reader,
}
_CODEC_ALIASES = {"gz": "gz", "gzip": "gz", "tgz": "gz", "bz2": "bz2", "xz": "xz", "zst": "zst", "zstd": "zst"}


def detect_format(key: str) -> tuple[str | None, str | None]:
    """Guess (format, compression) from the key: 'x.csv.gz' -> ('csv', 'gz'), 'm.tgz' -> ('tar', 'gz'),
    'x.bin' -> (None, None)."""
    parts = key.rsplit("/", 1)[-1].lower().split(".")
    if len(parts) > 1 and parts[-1] == "tgz":
        return "tar", "gz"
    compression = _CODEC_ALIASES[parts.pop()] if len(parts) > 1 and parts[-1] in _CODEC_ALIASES else None
    return (_FORMAT_BY_EXT.get(parts[-1]) if len(parts) > 1 else None), compression


_MAGIC_CODECS = [(b"\x1f\x8b", "gz"), (b"\xfd7zXZ\x00", "xz"), (b"\x28\xb5\x2f\xfd", "zst")]
_MAGIC_FORMATS = [  # (offset, leading bytes, format)
    (0, b"PAR1", "parquet"), (0, b"ORC", "orc"), (0, b"ARROW1", "arrow"), (0, b"FEA1", "arrow"),
    (0, b"Obj\x01", "avro"), (0, b"\x93NUMPY", "npy"), (0, b"%PDF-", "pdf"),
    (0, b"PK\x03\x04", "zip"), (0, b"PK\x05\x06", "zip"),
    (0, b"\x89PNG\r\n\x1a\n", "image"), (0, b"\xff\xd8\xff", "image"), (0, b"GIF8", "image"),
    (257, b"ustar", "tar"),
]
_IMAGE_MIMES = [(b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
                (b"BM", "image/bmp"), (b"RIFF", "image/webp")]


def sniff_format(head: bytes) -> tuple[str | None, str | None]:
    """Guess (format, compression) from an object's first bytes (512 is enough), for files
    whose name has no extension or the wrong one."""
    for magic, codec in _MAGIC_CODECS:
        if head.startswith(magic):
            return None, codec
    if head.startswith(b"BZh") and head[3:4].isdigit():
        return None, "bz2"
    for offset, magic, fmt in _MAGIC_FORMATS:
        if head[offset:offset + len(magic)] == magic:
            return fmt, None
    if head.lstrip()[:1] in (b"{", b"["):
        return "json", None
    return None, None


# ---- Avro object container files (no extra package needed)


def _avro_long(buf: bytes, pos: int) -> tuple[int, int]:
    """Zig-zag varint at buf[pos:] -> (value, next position)."""
    shift = result = 0
    while True:
        if pos >= len(buf):
            raise ValueError("Truncated Avro data")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return (result >> 1) ^ -(result & 1), pos
        shift += 7


def _avro_names(schema: Any, names: dict[str, Any]) -> dict[str, Any]:
    """Collect every named type (record / enum / fixed) so references to it can be resolved."""
    if isinstance(schema, list):
        for branch in schema:
            _avro_names(branch, names)
    elif isinstance(schema, dict):
        if schema.get("type") in ("record", "error", "enum", "fixed") and "name" in schema:
            name, namespace = schema["name"], schema.get("namespace")
            for alias in {name, name.rsplit(".", 1)[-1], f"{namespace}.{name}" if namespace else name}:
                names[alias] = schema
        for child in [f["type"] for f in schema.get("fields", [])] + [schema.get("items"), schema.get("values")]:
            if child is not None:
                _avro_names(child, names)
    return names


def _avro_read(schema: Any, buf: bytes, pos: int, names: dict[str, Any]) -> tuple[Any, int]:
    """Decode one value of `schema` at buf[pos:] -> (value, next position)."""
    if isinstance(schema, list):  # union: branch index, then the value
        index, pos = _avro_long(buf, pos)
        return _avro_read(schema[index], buf, pos, names)
    if isinstance(schema, dict):
        kind = schema["type"]
        if kind in ("record", "error"):
            record = {}
            for f in schema["fields"]:
                record[f["name"]], pos = _avro_read(f["type"], buf, pos, names)
            return record, pos
        if kind == "enum":
            index, pos = _avro_long(buf, pos)
            return schema["symbols"][index], pos
        if kind == "fixed":
            return buf[pos:pos + schema["size"]], pos + schema["size"]
        if kind in ("array", "map"):
            items: list[Any] = []
            while True:  # blocks of items; a negative count is followed by the block's byte size
                count, pos = _avro_long(buf, pos)
                if count == 0:
                    break
                if count < 0:
                    count, (_, pos) = -count, _avro_long(buf, pos)
                for _ in range(count):
                    if kind == "map":
                        key, pos = _avro_read("string", buf, pos, names)
                        value, pos = _avro_read(schema["values"], buf, pos, names)
                        items.append((key, value))
                    else:
                        value, pos = _avro_read(schema["items"], buf, pos, names)
                        items.append(value)
            return (dict(items) if kind == "map" else items), pos
        value, pos = _avro_read(kind, buf, pos, names)  # e.g. {"type": "long", "logicalType": ...}
        logical = schema.get("logicalType")
        if logical in ("timestamp-millis", "timestamp-micros") and isinstance(value, int):
            scale = 1000 if logical == "timestamp-millis" else 1_000_000
            value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value / scale)
        elif logical == "date" and isinstance(value, int):
            value = date(1970, 1, 1) + timedelta(days=value)
        return value, pos
    if schema == "null":
        return None, pos
    if schema == "boolean":
        return buf[pos] != 0, pos + 1
    if schema in ("int", "long"):
        return _avro_long(buf, pos)
    if schema == "float":
        return struct.unpack_from("<f", buf, pos)[0], pos + 4
    if schema == "double":
        return struct.unpack_from("<d", buf, pos)[0], pos + 8
    if schema in ("bytes", "string"):
        size, pos = _avro_long(buf, pos)
        data = buf[pos:pos + size]
        return (data.decode("utf-8", "replace") if schema == "string" else data), pos + size
    if schema in names:
        return _avro_read(names[schema], buf, pos, names)
    raise ValueError(f"Unsupported Avro type {schema!r}")


def _avro_decompress(codec: str, block: bytes) -> bytes:
    if codec == "null":
        return block
    if codec == "deflate":
        return zlib.decompress(block, -15)
    if codec == "bzip2":
        return bz2.decompress(block)
    if codec == "xz":
        return lzma.decompress(block)
    if codec == "zstandard":
        with _zstd_reader(io.BytesIO(block)) as reader:
            return reader.read()
    if codec == "snappy":  # raw snappy + a 4-byte CRC
        for module, call in (("snappy", "decompress"), ("cramjam", "snappy.decompress_raw")):
            try:
                target: Any = importlib.import_module(module)
            except ImportError:
                continue
            for part in call.split("."):
                target = getattr(target, part)
            return bytes(target(block[:-4]))
        raise ImportError("Avro snappy blocks need python-snappy or cramjam (pip install python-snappy)")
    raise ValueError(f"Unsupported Avro codec {codec!r}")


def parse_avro(data: bytes, n: int | None = None) -> tuple[Any, str, list[Any], bool]:
    """Avro object container bytes -> (schema, codec, records, complete). Decodes up to `n` records;
    `data` may be just the start of a file (complete=False when it stops at a cut-off block)."""
    if not data.startswith(b"Obj\x01"):
        raise ValueError("Not an Avro container file")
    pos, meta = 4, {}
    while True:
        count, pos = _avro_long(data, pos)
        if count == 0:
            break
        if count < 0:
            count, (_, pos) = -count, _avro_long(data, pos)
        for _ in range(count):
            key, pos = _avro_read("string", data, pos, {})
            meta[key], pos = _avro_read("bytes", data, pos, {})
    sync, pos = data[pos:pos + 16], pos + 16
    schema = json.loads(meta["avro.schema"])
    codec = meta.get("avro.codec", b"null").decode()
    names = _avro_names(schema, {})
    records: list[Any] = []
    while pos < len(data) and (n is None or len(records) < n):
        try:
            count, pos = _avro_long(data, pos)
            size, pos = _avro_long(data, pos)
        except ValueError:
            return schema, codec, records, False
        if pos + size + 16 > len(data):
            return schema, codec, records, False
        block = _avro_decompress(codec, data[pos:pos + size])
        if data[pos + size:pos + size + 16] != sync:
            raise ValueError("Avro sync marker doesn't match; the file may be corrupt")
        pos += size + 16
        block_pos = 0
        for _ in range(count if n is None else min(count, n - len(records))):
            record, block_pos = _avro_read(schema, block, block_pos, names)
            records.append(record)
    return schema, codec, records, pos >= len(data)


def _avro_type_name(schema: Any) -> str:
    if isinstance(schema, list):
        return " | ".join(_avro_type_name(branch) for branch in schema)
    if isinstance(schema, dict):
        kind = schema.get("logicalType") or schema["type"]
        if kind == "array":
            return f"array<{_avro_type_name(schema['items'])}>"
        if kind == "map":
            return f"map<{_avro_type_name(schema['values'])}>"
        return f"{kind} {schema['name']}" if kind in ("record", "enum", "fixed") and "name" in schema else kind
    return str(schema)


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
    policy: dict | None = None  # the bucket policy document (see explain_policy)
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
    # Estimated USD per month per storage class (see object_monthly_cost); None = no price for that class
    cost_by_storage_class: dict[str, float | None] = field(default_factory=dict)
    below_minimum: dict[str, Stat] = field(default_factory=dict)  # objects billed as 128 KB, per class
    largest: list[ObjectInfo] = field(default_factory=list)
    truncated: bool = False
    scan_seconds: float = 0.0

    @property
    def avg_size(self) -> float:
        return self.total_size / self.object_count if self.object_count else 0.0

    @property
    def monthly_cost(self) -> float:
        return sum(cost for cost in self.cost_by_storage_class.values() if cost)


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
    noncurrent_cost: float = 0.0  # estimated USD per month for the noncurrent versions
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
class DeletedObject:
    """A key whose latest version is a delete marker. Deleting that marker brings `last_version` back."""

    key: str
    deleted: datetime
    marker_version_id: str
    last_version: ObjectVersion | None = None  # newest real version; None = nothing left to restore
    old_versions: Stat = field(default_factory=Stat)  # every version still stored for this key
    monthly_cost: float = 0.0  # estimated USD per month for those versions

    @property
    def restorable(self) -> bool:
        return self.last_version is not None


@dataclass
class DeletedFiles:
    uri: str
    files: list[DeletedObject] = field(default_factory=list)  # most recently deleted first
    truncated: bool = False


@dataclass
class LifecycleImpact:
    """What a lifecycle rule would do to a prefix if it ran today (see simulate_lifecycle_objects)."""

    uri: str
    transitions: list[tuple[int, str]] = field(default_factory=list)  # (days, storage class), ascending
    expire_days: int | None = None
    scanned: Stat = field(default_factory=Stat)
    moves: dict[str, Stat] = field(default_factory=dict)  # target storage class -> objects moved there
    expired: Stat = field(default_factory=Stat)
    too_small: Stat = field(default_factory=Stat)  # old enough to move, but under S3's 128 KB transition minimum
    early_removals: Stat = field(default_factory=Stat)  # deleted or moved before their class's minimum storage duration
    cost_before: float = 0.0  # USD per month for the scanned objects today
    cost_after: float = 0.0
    one_time_cost: float = 0.0  # transition requests + early-deletion charges
    truncated: bool = False

    @property
    def monthly_savings(self) -> float:
        return self.cost_before - self.cost_after

    def describe(self) -> str:
        steps = [f"move to {cls} after {days} days" for days, cls in self.transitions]
        if self.expire_days is not None:
            steps.append(f"delete after {self.expire_days} days")
        return ", then ".join(steps)

    def rule(self, rule_id: str | None = None) -> dict:
        """The rule in the format put_bucket_lifecycle_configuration takes."""
        prefix = parse_s3_uri(self.uri)[1] if self.uri else ""
        rule: dict[str, Any] = {"ID": rule_id or f"{prefix.strip('/') or 'whole-bucket'}-lifecycle"[:255],
                                "Status": "Enabled", "Filter": {"Prefix": prefix}}
        if self.transitions:
            rule["Transitions"] = [{"Days": days, "StorageClass": cls} for days, cls in self.transitions]
        if self.expire_days is not None:
            rule["Expiration"] = {"Days": self.expire_days}
        return rule


@dataclass
class PolicyStatement:
    """One bucket-policy statement in plain English (see explain_policy)."""

    sid: str
    effect: str  # 'Allow' | 'Deny'
    who: list[str]
    actions: list[str]
    resources: list[str]
    conditions: list[str]
    anyone: bool = False  # the principal is '*' (or NotPrincipal): everyone, including anonymous users
    restricted: bool = False  # a condition limits callers to a VPC, IP range, organization, account or ARN
    writes: bool = False  # can change or delete data or settings (not just read / list)
    other_accounts: list[str] = field(default_factory=list)  # account ids other than yours

    @property
    def public(self) -> bool:
        return self.effect == "Allow" and self.anyone and not self.restricted


@dataclass
class BucketReport:
    """One bucket in the all-buckets overview."""

    bucket: BucketInfo
    config: BucketConfig
    metrics: BucketMetrics | None = None
    metrics_error: str | None = None


@dataclass
class ArchiveEntry:
    name: str
    size: int
    modified: datetime | None = None
    is_dir: bool = False


@dataclass
class ArchiveListing:
    """Files inside a zip / tar archive (see S3Analyzer.list_archive)."""

    uri: str
    kind: str  # 'zip' | 'tar'
    entries: list[ArchiveEntry] = field(default_factory=list)
    total_files: int | None = None  # None when the listing stopped early
    complete: bool = True
    bytes_read: int | None = None  # compressed tar: how much of the archive was streamed


@dataclass
class Preview:
    """First look at an object. kind: 'table' (DataFrame), 'json', 'text' (list of lines),
    'listing' (list of dicts: archive members, notebook cells, tensors, arrays), 'image' (bytes),
    'media' (presigned URL for audio / video / PDF), 'binary' (bytes) or 'unavailable'."""

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

# Storage price in USD per GB-month (GB = 2**30 bytes): us-east-1 list prices for the first 50 TB.
# Other regions and volume tiers differ; pass S3Analyzer(prices={...}) to use your own.
S3_PRICES: dict[str, float] = {
    "STANDARD": 0.023,
    "INTELLIGENT_TIERING": 0.023,  # frequent-access tier; listings don't say which tier an object is in
    "STANDARD_IA": 0.0125,
    "ONEZONE_IA": 0.01,
    "GLACIER_IR": 0.004,
    "GLACIER": 0.0036,
    "DEEP_ARCHIVE": 0.00099,
    "REDUCED_REDUNDANCY": 0.024,
    "EXPRESS_ONEZONE": 0.11,
}
# USD per 1,000 lifecycle transition requests into each class (us-east-1).
S3_TRANSITION_PRICES: dict[str, float] = {
    "INTELLIGENT_TIERING": 0.01, "STANDARD_IA": 0.01, "ONEZONE_IA": 0.01,
    "GLACIER_IR": 0.02, "GLACIER": 0.03, "DEEP_ARCHIVE": 0.05,
}
MIN_BILLABLE_SIZE = {"STANDARD_IA": 128 * KB, "ONEZONE_IA": 128 * KB, "GLACIER_IR": 128 * KB}
MIN_STORAGE_DAYS = {"STANDARD_IA": 30, "ONEZONE_IA": 30, "GLACIER_IR": 90, "GLACIER": 90, "DEEP_ARCHIVE": 180}
ARCHIVE_INDEX_BYTES = (32 * KB, 8 * KB)  # per GLACIER / DEEP_ARCHIVE object: billed at its class's rate, at STANDARD's
MIN_TRANSITION_SIZE = 128 * KB  # lifecycle rules don't move smaller objects (S3 default since September 2024)
# Lifecycle transitions only go "down" this list (S3's waterfall; STANDARD_IA can move to
# INTELLIGENT_TIERING but not the other way round).
TRANSITION_ORDER = {"STANDARD": 0, "REDUCED_REDUNDANCY": 0, "STANDARD_IA": 1, "INTELLIGENT_TIERING": 2,
                    "ONEZONE_IA": 3, "GLACIER_IR": 4, "GLACIER": 5, "DEEP_ARCHIVE": 6}


def object_monthly_cost(size: int, storage_class: str, prices: dict[str, float] | None = None) -> float | None:
    """Estimated USD per month to store one object, with S3's billing minimums: STANDARD_IA,
    ONEZONE_IA and GLACIER_IR bill at least 128 KB per object; GLACIER and DEEP_ARCHIVE add 40 KB
    of index data per object (32 KB at the archive rate, 8 KB at the STANDARD rate).
    None when `prices` has no price for the class. Storage only: no requests or data transfer."""
    prices = S3_PRICES if prices is None else prices
    price = prices.get(storage_class)
    if price is None:
        return None
    billed = max(size, MIN_BILLABLE_SIZE.get(storage_class, 0))
    if storage_class in ARCHIVE_CLASSES:
        at_class, at_standard = ARCHIVE_INDEX_BYTES
        return ((billed + at_class) * price + at_standard * prices.get("STANDARD", 0.0)) / GB
    return billed * price / GB


_STORAGE_TYPE_PREFIXES = [  # CloudWatch StorageType prefix -> class whose price applies; longest match first
    ("IntelligentTieringFA", "INTELLIGENT_TIERING"),
    ("IntelligentTieringIA", "STANDARD_IA"),  # each Intelligent-Tiering tier costs the same as this class
    ("IntelligentTieringAIA", "GLACIER_IR"),
    ("IntelligentTieringAA", "GLACIER"),
    ("IntelligentTieringDAA", "DEEP_ARCHIVE"),
    ("StandardIA", "STANDARD_IA"),
    ("Standard", "STANDARD"),
    ("OneZoneIA", "ONEZONE_IA"),
    ("ReducedRedundancy", "REDUCED_REDUNDANCY"),
    ("GlacierInstantRetrieval", "GLACIER_IR"),
    ("Glacier", "GLACIER"),
    ("DeepArchive", "DEEP_ARCHIVE"),
    ("ExpressOneZone", "EXPRESS_ONEZONE"),
]


def storage_type_class(storage_type: str) -> str | None:
    """CloudWatch StorageType ('StandardIAStorage', 'GlacierObjectOverhead', ...) -> the storage
    class whose price applies to it, or None if unknown."""
    if "S3ObjectOverhead" in storage_type or "Staging" in storage_type:
        return "STANDARD"  # archive index data and archive uploads in progress are billed as STANDARD
    return next((cls for prefix, cls in _STORAGE_TYPE_PREFIXES if storage_type.startswith(prefix)), None)


def cloudwatch_cost(size_by_storage_type: dict[str, int], prices: dict[str, float] | None = None
                    ) -> dict[str, float | None]:
    """BucketMetrics.size_by_storage_type -> estimated USD per month per storage type (None = no price).
    CloudWatch already reports minimum-size and archive overheads as their own storage types."""
    prices = S3_PRICES if prices is None else prices
    costs: dict[str, float | None] = {}
    for storage_type, size in size_by_storage_type.items():
        price = prices.get(storage_type_class(storage_type) or "")
        costs[storage_type] = None if price is None else size * price / GB
    return costs


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
    prices: dict[str, float] | None = None,
) -> PrefixSummary:
    """One streaming pass over `objects` -> PrefixSummary (counts, sizes, histograms, top-N, cost)."""
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
    summary.by_extension, summary.by_storage_class = _by_size(by_ext), _by_size(by_class)
    summary.by_folder = _by_size(by_folder)
    summary.cost_by_storage_class = {cls: cost_by_class.get(cls) for cls in summary.by_storage_class}
    summary.below_minimum = dict(below_minimum)
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


def summary_findings(summary: PrefixSummary, prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """Plain-language observations about a prefix -> [(level, message)], level 'warn' or 'info'."""
    prices = S3_PRICES if prices is None else prices
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
        saving = ""
        if "STANDARD" in prices and "STANDARD_IA" in prices:
            monthly = cold.size * (prices["STANDARD"] - prices["STANDARD_IA"]) / GB
            saving = f" In STANDARD_IA it would cost about {human_money(monthly)}/month less (see what_if)."
        found.append(("info", f"{human_size(cold.size)} in {_plural(cold.count, 'STANDARD object')} hasn't changed in 90+ days. "
                              "If it's rarely read, Intelligent-Tiering or a lifecycle transition could cut storage cost."
                              + saving))
    if summary.below_minimum:
        count = sum(st.count for st in summary.below_minimum.values())
        stored = sum(st.size for st in summary.below_minimum.values())
        billed = sum(st.count * MIN_BILLABLE_SIZE[cls] * prices.get(cls, 0.0) / GB
                     for cls, st in summary.below_minimum.items())
        in_standard = stored * prices.get("STANDARD", 0.0) / GB
        cheaper = (f" In STANDARD they would cost {human_money(in_standard)}/month."
                   if "STANDARD" in prices and in_standard < billed else "")
        classes = " / ".join(summary.below_minimum)
        found.append(("warn" if count >= 1000 else "info",
                      f"{_plural(count, 'object')} in {classes} are under 128 KB, but S3 bills each one as 128 KB: "
                      f"{human_size(count * 128 * KB)} billed for {human_size(stored)} stored, "
                      f"costing {human_money(billed)}/month.{cheaper}"))
    if summary.folder_markers:
        found.append(("info", f"{_plural(summary.folder_markers, 'zero-byte folder-marker key')} (ending in '/') not counted."))
    return found


def bucket_findings(cfg: BucketConfig, account_block: dict[str, bool] | None = None) -> list[tuple[str, str]]:
    """Plain-language risks / cost notes for a bucket config -> [(level, message)].
    account_block: the account-level Block Public Access settings ({} = not set, None = unknown)."""
    found: list[tuple[str, str]] = []
    pab = cfg.public_access_block
    account_on = bool(account_block) and all(account_block.values())
    if not account_on and "public_access_block" not in cfg.errors and not (pab and all(pab.values())):
        where = ("this bucket or the account" if account_block is not None
                 else "this bucket (account-level Block Public Access may still apply)")
        found.append(("warn", f"Block Public Access is not fully on for {where}."))
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


def _lifecycle_steps(move_after: int | dict[int, str] | None, to: str | None) -> list[tuple[int, str]]:
    """Normalize move_after / to into [(days, class)] and apply the checks S3 does on a rule."""
    if move_after is None:
        if to is not None:
            raise ValueError("to= needs move_after= (days)")
        return []
    if isinstance(move_after, dict):
        if to is not None:
            raise ValueError("Pass either move_after=days with to=class, or move_after={days: class}")
        steps = sorted((int(days), str(cls).upper()) for days, cls in move_after.items())
    elif to is None:
        raise ValueError("move_after= needs to=, e.g. to='STANDARD_IA'")
    else:
        steps = [(int(move_after), to.upper())]
    targets = ", ".join(cls for cls, rank in TRANSITION_ORDER.items() if rank)
    last_days, last_rank, last_cls = 0, 0, ""
    for days, cls in steps:
        rank = TRANSITION_ORDER.get(cls)
        if not rank:
            raise ValueError(f"Lifecycle rules can't move objects to {cls!r}; use one of {targets}")
        if days < 0:
            raise ValueError("Days can't be negative")
        if cls in ("STANDARD_IA", "ONEZONE_IA") and days < 30:
            raise ValueError(f"S3 only moves objects to {cls} once they are at least 30 days old")
        if rank <= last_rank:
            raise ValueError(f"Each move must go to a colder class: {cls} comes after {last_cls}")
        if last_cls in ("STANDARD_IA", "ONEZONE_IA") and days < last_days + 30:
            raise ValueError(f"S3 keeps objects in {last_cls} for at least 30 days before moving them again")
        last_days, last_rank, last_cls = days, rank, cls
    return steps


def simulate_lifecycle_objects(
    objects: Iterable[ObjectInfo],
    uri: str = "",
    *,
    move_after: int | dict[int, str] | None = None,
    to: str | None = None,
    delete_after: int | None = None,
    prices: dict[str, float] | None = None,
    transition_prices: dict[str, float] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> LifecycleImpact:
    """What a lifecycle rule would do to `objects` if it ran today, and what it would save.

    move_after    days since last modified, with to='STANDARD_IA' / 'GLACIER_IR' / 'GLACIER' / ...,
                  or several moves at once: {30: 'STANDARD_IA', 180: 'GLACIER'}
    delete_after  days since last modified before the object is deleted (expired)

    Follows S3's rules: objects under 128 KB are not moved, objects only move to colder classes,
    and removing an object before its class's minimum storage duration is billed for the rest of it.
    """
    steps = _lifecycle_steps(move_after, to)
    if delete_after is None and not steps:
        raise ValueError("Nothing to simulate: pass move_after= with to=, and/or delete_after=")
    if delete_after is not None and (delete_after < 1 or (steps and delete_after <= steps[-1][0])):
        raise ValueError("delete_after must be at least 1 day and later than the last move")
    prices = S3_PRICES if prices is None else prices
    transition_prices = S3_TRANSITION_PRICES if transition_prices is None else transition_prices
    now = now or _utcnow()
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    impact = LifecycleImpact(uri=s3_uri(bucket, prefix) if bucket else uri, transitions=steps,
                             expire_days=delete_after)
    moves: dict[str, Stat] = defaultdict(Stat)

    for i, obj in enumerate(objects):
        if limit is not None and i >= limit:
            impact.truncated = True
            break
        if obj.is_folder_marker:
            continue
        size, cls = obj.size, obj.storage_class
        age_days = (now - obj.last_modified).total_seconds() / 86400
        before = after = object_monthly_cost(size, cls, prices) or 0.0
        target = next((step_cls for days, step_cls in reversed(steps) if age_days >= days), None)
        removed = False
        impact.scanned.add(size)
        if delete_after is not None and age_days >= delete_after:
            impact.expired.add(size)
            after, removed = 0.0, True
        elif target and TRANSITION_ORDER.get(cls, 99) < TRANSITION_ORDER[target]:  # unknown classes never move
            if size < MIN_TRANSITION_SIZE:
                impact.too_small.add(size)
            else:
                moves[target].add(size)
                after, removed = object_monthly_cost(size, target, prices) or 0.0, True
                impact.one_time_cost += transition_prices.get(target, 0.0) / 1000
        min_days = MIN_STORAGE_DAYS.get(cls, 0)
        if removed and age_days < min_days:
            impact.early_removals.add(size)
            impact.one_time_cost += before * (min_days - age_days) / 30
        impact.cost_before += before
        impact.cost_after += after

    impact.moves = dict(moves)
    return impact


# ---- bucket policies in plain English

_POLICY_ACTIONS = {name.lower(): text for name, text in {
    "*": "everything", "s3:*": "everything in S3",
    "s3:Get*": "all read actions", "s3:List*": "all list actions", "s3:Put*": "all write actions",
    "s3:Delete*": "all delete actions",
    "s3:GetObject": "read files", "s3:GetObjectVersion": "read old versions",
    "s3:PutObject": "upload / overwrite files", "s3:DeleteObject": "delete files",
    "s3:DeleteObjectVersion": "permanently delete versions", "s3:ListBucket": "list files",
    "s3:ListBucketVersions": "list versions", "s3:GetBucketLocation": "look up the region",
    "s3:GetObjectAcl": "read file ACLs", "s3:PutObjectAcl": "change file ACLs",
    "s3:GetObjectTagging": "read file tags", "s3:PutObjectTagging": "change file tags",
    "s3:AbortMultipartUpload": "cancel uploads", "s3:ListMultipartUploadParts": "list upload parts",
    "s3:ListBucketMultipartUploads": "list unfinished uploads", "s3:RestoreObject": "restore archived files",
    "s3:PutBucketPolicy": "change the bucket policy", "s3:DeleteBucketPolicy": "delete the bucket policy",
    "s3:PutBucketAcl": "change the bucket ACL", "s3:DeleteBucket": "delete the bucket",
    "s3:PutLifecycleConfiguration": "change lifecycle rules", "s3:PutBucketVersioning": "change versioning",
    "s3:ReplicateObject": "replicate files in", "s3:ReplicateDelete": "replicate deletes in",
}.items()}
_WRITE_VERBS = ("put", "delete", "replicate", "restore", "abort", "create", "bypass", "update")
_CONDITION_KEYS = {
    "aws:securetransport": "HTTPS", "aws:sourcevpce": "VPC endpoint", "aws:sourcevpc": "VPC",
    "aws:sourceip": "source IP", "aws:vpcsourceip": "VPC source IP", "aws:principalorgid": "caller's organization",
    "aws:principalorgpaths": "caller's organization path", "aws:principalaccount": "caller's account",
    "aws:sourceaccount": "source account", "aws:sourcearn": "source ARN", "aws:principalarn": "caller ARN",
    "aws:userid": "caller user id", "aws:username": "caller user name", "s3:tlsversion": "TLS version",
    "s3:x-amz-server-side-encryption": "upload encryption", "s3:x-amz-acl": "upload ACL",
}
# Condition keys that narrow who can use an Allow (when compared positively, not with a Not... operator).
_RESTRICTING_KEYS = {"aws:sourcevpce", "aws:sourcevpc", "aws:sourceip", "aws:vpcsourceip", "aws:principalorgid",
                     "aws:principalorgpaths", "aws:principalaccount", "aws:sourceaccount", "aws:sourcearn",
                     "aws:principalarn", "aws:userid", "aws:username", "aws:sourceowner"}
_CONDITION_OPERATORS = {
    "stringequals": "=", "stringequalsignorecase": "=", "stringnotequals": "≠", "stringnotequalsignorecase": "≠",
    "stringlike": "matches", "stringnotlike": "doesn't match", "arnequals": "=", "arnlike": "matches",
    "arnnotequals": "≠", "arnnotlike": "doesn't match", "ipaddress": "in", "notipaddress": "not in",
    "numericequals": "=", "numericnotequals": "≠", "numericlessthan": "<", "numericlessthanequals": "≤",
    "numericgreaterthan": ">", "numericgreaterthanequals": "≥", "bool": "=", "dateequals": "=",
    "datelessthan": "before", "dategreaterthan": "after",
}
_PRINCIPAL_ARN_RE = re.compile(r"^arn:aws[\w-]*:(?:iam|sts)::([^:]*):(.+)$")


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _describe_principal(kind: str, value: str) -> tuple[str, str | None]:
    """One principal -> (plain text, its account id or None)."""
    if kind == "AWS":
        if value == "*":
            return "anyone (public)", None
        if value.isdigit():
            return f"account {value}", value
        match = _PRINCIPAL_ARN_RE.match(value)
        if match:
            account, resource = match.groups()
            if account == "cloudfront":
                return f"CloudFront origin access identity {resource.rsplit(' ', 1)[-1]}", None
            if resource == "root":
                return f"account {account}", account
            kind_, _, name = resource.partition("/")
            name = name.split("/")[0] if kind_ == "assumed-role" else name.rsplit("/", 1)[-1]
            label = {"assumed-role": "role session of"}.get(kind_, kind_.replace("-", " "))
            return f"{label} {name} (account {account})", account
        return value, None
    if kind == "Service":
        return f"AWS service {value}", None
    if kind == "CanonicalUser":
        return f"canonical user {value[:12]}…", None
    if kind == "Federated":
        return f"users signed in through {value}", None
    return f"{kind} {value}", None


def _describe_resource(arn: str) -> str:
    if arn == "*":
        return "any resource"
    if ":s3:::" not in arn:
        return arn
    bucket, _, key = arn.split(":::", 1)[1].partition("/")
    if not key:
        return f"bucket {bucket}"
    return f"all files in {bucket}" if key == "*" else s3_uri(bucket, key)


def _describe_condition(operator: str, key: str, values: Any) -> tuple[str, bool]:
    """One condition -> (plain text, whether it narrows down who can use the statement)."""
    base = operator.split(":")[-1]
    if_exists = base.lower().endswith("ifexists")
    base = base[:-8] if if_exists else base
    lowered, texts = key.lower(), [str(v) for v in _as_list(values)]
    label = _CONDITION_KEYS.get(lowered, key)
    restricts = lowered in _RESTRICTING_KEYS and "not" not in base.lower() and base.lower() != "null"
    if lowered == "aws:securetransport" and base.lower() == "bool":
        text = "over HTTPS" if texts[0].lower() == "true" else "not over HTTPS"
    elif base.lower() == "null":
        text = f"{label} {'not set' if texts[0].lower() == 'true' else 'is set'}"
    else:
        text = f"{label} {_CONDITION_OPERATORS.get(base.lower(), base)} {', '.join(texts)}"
    return text + (" (when present)" if if_exists else ""), restricts


def explain_policy(policy: dict | str | None, own_account: str | None = None) -> list[PolicyStatement]:
    """Bucket policy (dict or JSON text) -> one PolicyStatement per statement, in plain English.
    With own_account, principals from any other account are listed in `other_accounts`."""
    if not policy:
        return []
    policy = json.loads(policy) if isinstance(policy, str) else policy
    explained = []
    for i, stmt in enumerate(_as_list(policy.get("Statement", []))):
        who, accounts, anyone = [], [], False
        principal_key = "NotPrincipal" if "NotPrincipal" in stmt else "Principal"
        principal = stmt.get(principal_key, {})
        entries = [("AWS", "*")] if principal == "*" else [
            (kind, value) for kind, values in principal.items() for value in _as_list(values)]
        for kind, value in entries:
            text, account = _describe_principal(kind, value)
            who.append(text)
            anyone = anyone or (kind == "AWS" and value == "*")
            if account and account != own_account and own_account is not None:
                accounts.append(account)
        if principal_key == "NotPrincipal":
            who, anyone = ["everyone except " + ", ".join(who)], True

        action_key = "NotAction" if "NotAction" in stmt else "Action"
        raw_actions = [str(a) for a in _as_list(stmt.get(action_key, []))]
        actions = [_POLICY_ACTIONS.get(a.lower(), a) for a in raw_actions]
        writes = action_key == "NotAction" or any(
            "*" in a or a.lower().split(":", 1)[-1].startswith(_WRITE_VERBS) for a in raw_actions)
        if action_key == "NotAction":
            actions = ["everything except " + ", ".join(actions)]

        resource_key = "NotResource" if "NotResource" in stmt else "Resource"
        resources = [_describe_resource(str(r)) for r in _as_list(stmt.get(resource_key, []))]
        if resource_key == "NotResource":
            resources = ["everything except " + ", ".join(resources)]

        conditions, restricted = [], False
        for operator, pairs in (stmt.get("Condition") or {}).items():
            for key, values in pairs.items():
                text, restricts = _describe_condition(operator, key, values)
                conditions.append(text)
                restricted = restricted or restricts
        explained.append(PolicyStatement(
            sid=str(stmt.get("Sid") or f"#{i + 1}"), effect=stmt.get("Effect", "Allow"), who=who, actions=actions,
            resources=resources, conditions=conditions, anyone=anyone, restricted=restricted, writes=writes,
            other_accounts=sorted(set(accounts))))
    return explained


def policy_findings(statements: list[PolicyStatement]) -> list[tuple[str, str]]:
    """Plain-language risks in an explained bucket policy -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    for st in statements:
        if st.effect != "Allow":
            continue
        name = f"Statement {st.sid}"
        if st.public:
            change = " That includes changing or deleting data." if st.writes else ""
            found.append(("warn", f"{name} lets anyone on the internet: {', '.join(st.actions)} "
                                  f"(on {', '.join(st.resources)}).{change}"))
        elif st.anyone:
            found.append(("info", f"{name} is open to everyone, but only when: {'; '.join(st.conditions)}."))
        if any(w.startswith("everyone except") for w in st.who):
            found.append(("warn", f"{name} uses Allow with NotPrincipal: everyone except the listed principals "
                                  "gets access."))
        if st.other_accounts:
            found.append(("info", f"{name} gives other AWS accounts access ({', '.join(st.other_accounts)}): "
                                  f"{', '.join(st.actions)}."))
    if statements and not any(st.effect == "Deny" and "not over HTTPS" in st.conditions for st in statements):
        found.append(("info", "No statement blocks plain HTTP. A Deny when aws:SecureTransport is false "
                              "makes every request use HTTPS."))
    return found


# =============================================================================
# 4. S3Analyzer - pure logic layer (talks to AWS, returns data)
# =============================================================================


class _BodyReader(io.RawIOBase):
    """Raw stream over a botocore StreamingBody, so io/gzip/pandas can wrap it."""

    def __init__(self, body: Any):
        self._body = body
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._body.read(len(buffer))
        buffer[: len(data)] = data
        self.bytes_read += len(data)
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


# What a broken or mislabeled file can raise while being decoded (pyarrow's errors subclass ValueError / OSError).
_DATA_ERRORS = (ValueError, ImportError, OSError, EOFError, zlib.error, lzma.LZMAError, zipfile.BadZipFile,
                tarfile.TarError)
_READ_ERRORS = (*_DATA_ERRORS, KeyError, IndexError, struct.error)  # + malformed headers, while previewing


def _read_arrow_table(fmt: str, handle: Any, nrows: int | None, columns: list[str] | None) -> Any:
    """Parquet / ORC / Feather (Arrow IPC) from a seekable file -> pyarrow Table (first nrows only
    reads the row groups / stripes / batches it needs)."""
    pa = _require("pyarrow", f"Reading {fmt}")
    if fmt == "parquet":
        parquet = _require("pyarrow.parquet", "Reading parquet").ParquetFile(handle)
        if nrows is None:
            return parquet.read(columns=columns)
        chunks = parquet.iter_batches(batch_size=max(nrows, 1), columns=columns)
        empty = parquet.schema_arrow.empty_table()
    elif fmt == "orc":
        orc = _require("pyarrow.orc", "Reading ORC").ORCFile(handle)
        if nrows is None:
            return orc.read(columns=columns)
        chunks = (orc.read_stripe(i, columns=columns) for i in range(orc.nstripes))
        empty = orc.schema.empty_table()
    elif fmt == "arrow":
        try:
            reader = _require("pyarrow.ipc", "Reading Feather / Arrow").open_file(handle)
        except pa.ArrowInvalid:  # Feather V1 (pre-2020) isn't an Arrow IPC file
            handle.seek(0)
            table = _require("pyarrow.feather", "Reading Feather").read_table(handle, columns=columns)
            return table if nrows is None else table.slice(0, nrows)
        if nrows is None:
            table = reader.read_all()
            return table.select(columns) if columns else table
        chunks = (reader.get_batch(i) for i in range(reader.num_record_batches))
        empty = reader.schema.empty_table()
    else:
        raise ValueError(f"Not a columnar format: {fmt!r}")
    batches, rows = [], 0
    for batch in chunks if nrows > 0 else ():
        batches.append(batch)
        rows += batch.num_rows
        if rows >= nrows:
            break
    table = pa.Table.from_batches(batches).slice(0, nrows) if batches else empty
    return table.select(columns) if columns and (fmt == "arrow" or not batches) else table


def _columnar_info(fmt: str, handle: Any) -> dict[str, Any]:
    """Rows, columns and layout of an ORC / Feather file from its metadata."""
    if fmt == "orc":
        orc = _require("pyarrow.orc", "Reading ORC").ORCFile(handle)
        return {"rows": orc.nrows, "stripes": orc.nstripes, "compression": getattr(orc, "compression", None),
                "columns": [(f.name, str(f.type)) for f in orc.schema]}
    pa = _require("pyarrow", "Reading Feather / Arrow")
    try:
        reader = _require("pyarrow.ipc", "Reading Feather / Arrow").open_file(handle)
    except pa.ArrowInvalid:
        return {}
    info: dict[str, Any] = {"batches": reader.num_record_batches,
                            "columns": [(f.name, str(f.type)) for f in reader.schema]}
    if hasattr(reader, "count_rows"):
        info["rows"] = reader.count_rows()
    return info


def _zip_time(stamp: tuple[int, ...]) -> datetime | None:
    try:
        return datetime(*stamp, tzinfo=timezone.utc)
    except (TypeError, ValueError):  # zip allows dates like 1980-00-00
        return None


def _npy_header(fp: Any) -> tuple[tuple[int, ...], bool, Any]:
    """Read a .npy header from `fp` -> (shape, fortran_order, dtype); fp is left at the first data byte."""
    npformat = _require("numpy.lib.format", "Reading .npy")
    version = npformat.read_magic(fp)
    read_header = npformat.read_array_header_1_0 if version == (1, 0) else npformat.read_array_header_2_0
    return read_header(fp)


class S3Analyzer:
    """Pure-logic S3 analysis: every method returns data, nothing is printed.

    Anywhere a `uri` is taken you can pass 's3://bucket/prefix' or 'bucket/prefix'.
    Scans accept `limit` (stop after N keys) and `progress` (called with the running count).
    `prices` overrides S3_PRICES (USD per GB-month by storage class) for cost estimates.
    """

    def __init__(self, session: Any = None, *, region: str | None = None, profile: str | None = None,
                 client: Any = None, prices: dict[str, float] | None = None):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        # STS and S3 Control may be unreachable from a VPC-only notebook: fail fast instead of hanging.
        self._quick_config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
        self.client = client or self.session.client("s3", config=self._config)
        self.prices = {**S3_PRICES, **(prices or {})}
        self._regional_clients: dict[str, Any] = {}
        self._cloudwatch_clients: dict[str, Any] = {}
        self._regions: dict[str, str] = {}
        self._account_id: str | None = None
        self._account_id_checked = False

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
        return self._s3_in(self._safe_region(bucket))

    def _s3_in(self, region: str | None) -> Any:
        if not region or region == self.client.meta.region_name:
            return self.client
        if region not in self._regional_clients:
            self._regional_clients[region] = self.session.client("s3", region_name=region, config=self._config)
        return self._regional_clients[region]

    def _cloudwatch_in(self, region: str) -> Any:
        if region not in self._cloudwatch_clients:
            self._cloudwatch_clients[region] = self.session.client("cloudwatch", region_name=region)
        return self._cloudwatch_clients[region]

    def account_id(self) -> str | None:
        """Your AWS account id (cached), or None if STS can't be reached."""
        if not self._account_id_checked:
            self._account_id_checked = True
            try:
                self._account_id = self.session.client("sts", config=self._quick_config).get_caller_identity()["Account"]
            except (ClientError, BotoCoreError):
                pass
        return self._account_id

    def account_public_access_block(self) -> dict[str, bool]:
        """Account-level Block Public Access settings ({} if never set). Needs s3:GetAccountPublicAccessBlock."""
        account = self.account_id()
        if account is None:
            raise ValueError("Couldn't look up the account id (sts:GetCallerIdentity)")
        control = self.session.client("s3control", region_name=self.client.meta.region_name or "us-east-1",
                                      config=self._quick_config)
        try:
            return control.get_public_access_block(AccountId=account)["PublicAccessBlockConfiguration"]
        except ClientError as exc:
            if _error_code(exc) == "NoSuchPublicAccessBlockConfiguration":
                return {}
            raise

    def bucket_policy(self, bucket: str) -> dict | None:
        """The bucket policy document, or None if the bucket has none. See explain_policy."""
        bucket, _ = parse_s3_uri(bucket)
        try:
            return json.loads(self._client_for(bucket).get_bucket_policy(Bucket=bucket)["Policy"])
        except ClientError as exc:
            if _error_code(exc) == "NoSuchBucketPolicy":
                return None
            raise

    def versioning_status(self, bucket: str) -> str:
        """'Enabled', 'Suspended' or 'Disabled'."""
        bucket, _ = parse_s3_uri(bucket)
        return self._client_for(bucket).get_bucket_versioning(Bucket=bucket).get("Status", "Disabled")

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
        if cfg.has_policy is not False:
            resp = get("policy_document", "get_bucket_policy", ("NoSuchBucketPolicy",))
            if resp:
                cfg.policy, cfg.has_policy = json.loads(resp["Policy"]), True
            elif "policy_document" not in cfg.errors:
                cfg.has_policy, cfg.policy_is_public = False, None
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
        cloudwatch = self._cloudwatch_in(self.bucket_region(bucket))
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
        """One pass over the prefix: totals, file types, storage classes, folders, size/age histograms,
        largest, and estimated monthly storage cost."""
        scan = self.iter_objects(uri, limit=None if limit is None else limit + 1, progress=progress)
        return summarize_objects(scan, uri, top_n=top_n, folder_depth=folder_depth, limit=limit, prices=self.prices)

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
                    stats.noncurrent_cost += object_monthly_cost(
                        version["Size"], version.get("StorageClass", "STANDARD"), self.prices) or 0.0
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

    def deleted_files(self, uri: str, *, deleted_after: Any = None, limit: int | None = None,
                      progress: Callable[[int], None] | None = None) -> DeletedFiles:
        """Keys under `uri` whose latest version is a delete marker (versioned buckets). Deleting the
        marker (s3:DeleteObjectVersion) brings back `last_version`. deleted_after: datetime, '2024-05-01' or '7d'."""
        bucket, prefix = parse_s3_uri(uri)
        since = parse_time(deleted_after)
        result = DeletedFiles(uri=s3_uri(bucket, prefix))
        markers: dict[str, dict] = {}
        newest: dict[str, ObjectVersion] = {}
        kept: dict[str, Stat] = defaultdict(Stat)
        cost: dict[str, float] = defaultdict(float)
        seen = 0
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
            markers.update((m["Key"], m) for m in page.get("DeleteMarkers", []) if m["IsLatest"])
            for v in page.get("Versions", []):
                if v["IsLatest"]:
                    continue
                key, storage_class = v["Key"], v.get("StorageClass", "STANDARD")
                kept[key].add(v["Size"])
                cost[key] += object_monthly_cost(v["Size"], storage_class, self.prices) or 0.0
                if key not in newest or v["LastModified"] > newest[key].last_modified:
                    newest[key] = ObjectVersion(key, v["VersionId"], False, v["LastModified"], v["Size"],
                                                storage_class=storage_class)
            seen += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
            if progress:
                progress(seen)
            if limit is not None and seen >= limit:
                result.truncated = bool(page.get("IsTruncated"))
                break
        result.files = sorted(
            (DeletedObject(key, m["LastModified"], m["VersionId"], newest.get(key), kept.get(key, Stat()), cost[key])
             for key, m in markers.items() if since is None or m["LastModified"] >= since),
            key=lambda d: d.deleted, reverse=True)
        return result

    def simulate_lifecycle(self, uri: str, *, move_after: int | dict[int, str] | None = None, to: str | None = None,
                           delete_after: int | None = None, limit: int | None = None,
                           progress: Callable[[int], None] | None = None) -> LifecycleImpact:
        """What a lifecycle rule on `uri` would move or delete if it ran today, and the cost before and after.
        See simulate_lifecycle_objects for the arguments."""
        scan = self.iter_objects(uri, limit=None if limit is None else limit + 1, progress=progress)
        return simulate_lifecycle_objects(scan, uri, move_after=move_after, to=to, delete_after=delete_after,
                                          prices=self.prices, limit=limit)

    def bucket_reports(self, *, match: str | None = None, metrics: bool = True, max_workers: int = 8,
                       progress: Callable[[int], None] | None = None) -> list[BucketReport]:
        """Settings (and CloudWatch size unless metrics=False) for every bucket, checked in parallel.
        match: only buckets whose name matches this glob, e.g. 'sagemaker-*'."""
        buckets = [b for b in self.list_buckets() if match is None or fnmatch.fnmatchcase(b.name, match)]
        for region in {b.region for b in buckets if b.region}:
            self._s3_in(region)  # boto3 sessions aren't thread-safe: make every client before the threads start
            if metrics:
                self._cloudwatch_in(region)

        def check(bucket: BucketInfo) -> BucketReport:
            report = BucketReport(bucket, self.bucket_config(bucket.name))
            if metrics:
                try:
                    report.metrics = self.bucket_metrics(bucket.name)
                except (ClientError, BotoCoreError) as exc:
                    report.metrics_error = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
            return report

        reports: list[BucketReport] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for report in pool.map(check, buckets):
                reports.append(report)
                if progress:
                    progress(len(reports))
        return reports

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

    def open(self, uri: str, *, decompress: bool = True, compression: str | None = None) -> io.BufferedIOBase:
        """Streaming binary reader (use as a context manager). .gz / .bz2 / .xz / .zst are decompressed
        on the fly. compression overrides the codec guessed from the name ('gz', 'bz2', 'xz', 'zst'; '' = none)."""
        bucket, key = parse_s3_uri(uri)
        codec = (detect_format(key)[1] if compression is None else _CODEC_ALIASES.get(compression, compression)
                 ) if decompress else None
        if codec and codec not in _DECOMPRESSORS:
            raise ValueError(f"Unknown compression {codec!r}; use one of {', '.join(_DECOMPRESSORS)}")
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
        except AttributeError:  # C-level readers (zstandard) close their source themselves
            pass
        return reader

    def read_bytes(self, uri: str, start: int | None = None, end: int | None = None) -> bytes:
        """Raw bytes (not decompressed). start/end are inclusive byte offsets for a ranged GET."""
        bucket, key = parse_s3_uri(uri)
        extra = {"Range": f"bytes={start or 0}-{'' if end is None else end}"} if start or end is not None else {}
        return self.client.get_object(Bucket=bucket, Key=key, **extra)["Body"].read()

    def _read_head(self, uri: str, max_bytes: int, compression: str | None = None) -> tuple[bytes, bool]:
        """First max_bytes of the (decompressed) object, and whether there was more."""
        with self.open(uri, compression=compression) as stream:
            data = stream.read(max_bytes + 1)
        return data[:max_bytes], len(data) > max_bytes

    def read_text(self, uri: str, *, max_bytes: int = MB, encoding: str = "utf-8", compression: str | None = None) -> str:
        """Decoded text of the first `max_bytes` (decompressed) bytes."""
        return self._read_head(uri, max_bytes, compression)[0].decode(encoding, errors="replace")

    def read_lines(self, uri: str, n: int = 20, *, encoding: str = "utf-8", max_line_chars: int = 100_000,
                   compression: str | None = None) -> list[str]:
        """First n lines; downloads only as much as needed."""
        lines: list[str] = []
        with io.TextIOWrapper(self.open(uri, compression=compression), encoding=encoding, errors="replace") as text:
            while len(lines) < n:
                line = text.readline(max_line_chars)
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        return lines

    def read_json(self, uri: str, *, compression: str | None = None) -> Any:
        """Parse a whole JSON document (reads the full object)."""
        with self.open(uri, compression=compression) as stream:
            return json.load(stream)

    def read_jsonl(self, uri: str, n: int | None = None, *, encoding: str = "utf-8",
                   compression: str | None = None) -> list[Any]:
        """Parse JSON-lines records; n limits how many (only that much is downloaded)."""
        records: list[Any] = []
        with io.TextIOWrapper(self.open(uri, compression=compression), encoding=encoding) as text:
            for line in text:
                if line.strip():
                    records.append(json.loads(line))
                    if n is not None and len(records) >= n:
                        break
        return records

    def read_avro(self, uri: str, n: int | None = None, *, compression: str | None = None) -> list[Any]:
        """Records from an Avro container file (codecs null, deflate, bzip2, xz, zstandard; snappy needs
        python-snappy). n limits how many; then only the start of the file is downloaded."""
        return self._avro(uri, n, compression)[2]

    def _avro(self, uri: str, n: int | None, compression: str | None) -> tuple[Any, str, list[Any], bool]:
        if n is None:
            with self.open(uri, compression=compression) as stream:
                return parse_avro(stream.read())
        window = MB
        while True:  # read more of the file until n records are decoded (or it's all read)
            data, more = self._read_head(uri, window, compression)
            schema, codec, records, _ = parsed = parse_avro(data, n)
            if len(records) >= n or not more:
                return schema, codec, records[:n], parsed[3] and not more
            window *= 4

    def read_npy(self, uri: str, *, nrows: int | None = None, compression: str | None = None) -> Any:
        """NumPy .npy array (never unpickles). With nrows on an uncompressed file only those rows
        are downloaded."""
        np = _require("numpy", "Reading .npy")
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._random_access(bucket, key, codec, buffer_size=64 * KB) as handle:
            shape, fortran, dtype = _npy_header(handle)
            if dtype.hasobject:
                raise ValueError("This .npy holds Python objects (a pickle); not loaded, because unpickling can run code")
            if nrows is None or fortran or not shape:
                handle.seek(0)
                array = _require("numpy.lib.format", "Reading .npy").read_array(handle, allow_pickle=False)
                return array if nrows is None or not shape else array[:nrows]
            rows = min(nrows, shape[0])
            data = handle.read(rows * math.prod(shape[1:]) * dtype.itemsize)
            return np.frombuffer(data, dtype=dtype).reshape((rows, *shape[1:]))

    def read_df(self, uri: str, *, nrows: int | None = None, columns: list[str] | None = None,
                fmt: str | None = None, compression: str | None = None, **kwargs: Any):
        """Load a table into a pandas DataFrame: csv / tsv / psv / json / jsonl / parquet / orc /
        feather (arrow) / avro / excel (xlsx, xls) / npy, optionally .gz / .bz2 / .xz / .zst compressed.
        nrows reads just the first rows (parquet, orc and feather fetch only what they need).
        Extra kwargs go to pandas.read_csv (csv / tsv / psv) or pandas.read_excel (e.g. sheet_name=)."""
        pd = _require("pandas", "read_df")
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
                raise ValueError(f"A {array.ndim}-dimensional array doesn't fit in a table; use read_npy")
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
        raise ValueError(f"Can't tell how to read {key!r} as a table; pass fmt='csv'|'tsv'|'psv'|'json'|'jsonl'|"
                         "'parquet'|'orc'|'arrow'|'avro'|'excel'|'npy'")

    def _seekable(self, bucket: str, key: str, *, buffer_size: int = MB) -> io.BufferedReader:
        size = self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        return io.BufferedReader(_RangeReader(self.client, bucket, key, size), buffer_size=buffer_size)

    def _random_access(self, bucket: str, key: str, codec: str | None = None, *,
                       buffer_size: int = MB) -> io.BufferedIOBase:
        """Seekable reader: ranged GETs, or (for a compressed object) the whole thing decompressed in memory."""
        if not codec:
            return self._seekable(bucket, key, buffer_size=buffer_size)
        with self.open(s3_uri(bucket, key), compression=codec) as stream:
            return io.BytesIO(stream.read())

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

    def list_archive(self, uri: str, *, limit: int = 1000, max_bytes: int = 256 * MB,
                     compression: str | None = None) -> ArchiveListing:
        """Files inside a .zip / .tar / .tar.gz / .tgz (e.g. a SageMaker model.tar.gz) without extracting it.
        A zip's index sits at its end, so only that is downloaded. A compressed tar has no index: it's
        streamed from the start and the listing stops after `max_bytes` of it (complete=False)."""
        bucket, key = parse_s3_uri(uri)
        fmt, codec = detect_format(key)
        codec = codec if compression is None else compression
        if fmt not in ("zip", "tar", "npz", "torch", "excel"):
            fmt = sniff_format(self.read_bytes(uri, 0, 511))[0] or "tar"
        listing = ArchiveListing(uri=s3_uri(bucket, key), kind="tar" if fmt == "tar" else "zip")
        if listing.kind == "zip":
            with self._random_access(bucket, key, codec, buffer_size=256 * KB) as handle, zipfile.ZipFile(handle) as archive:
                infos = archive.infolist()
            listing.total_files = len(infos)
            listing.entries = [ArchiveEntry(i.filename, i.file_size, _zip_time(i.date_time), i.is_dir())
                               for i in infos[:limit]]
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
                    if len(listing.entries) >= limit or (raw is not None and raw.bytes_read > max_bytes):
                        listing.complete = False
                        break
                    listing.entries.append(ArchiveEntry(member.name, member.size,
                                                        datetime.fromtimestamp(member.mtime, timezone.utc),
                                                        member.isdir()))
        finally:
            stream.close()
            if raw is not None:
                listing.bytes_read = raw.bytes_read
                raw.close()
        listing.total_files = len(listing.entries) if listing.complete else None
        return listing

    def safetensors_info(self, uri: str) -> dict[str, Any]:
        """Tensor names, dtypes and shapes plus metadata from a .safetensors header (only the header is read)."""
        head = self.read_bytes(uri, 0, 7)
        header_size = int.from_bytes(head, "little") if len(head) == 8 else 0
        if not 2 <= header_size <= 100 * MB:
            raise ValueError("Not a safetensors file (bad header length)")
        header = json.loads(self.read_bytes(uri, 8, 8 + header_size - 1))
        metadata = header.pop("__metadata__", None) or {}
        tensors = [{"tensor": name, "dtype": spec.get("dtype"), "shape": tuple(spec.get("shape", [])),
                    "parameters": math.prod(spec.get("shape", []))} for name, spec in header.items()]
        return {"tensors": tensors, "metadata": metadata}

    def preview(self, uri: str, n: int = 20, *, max_bytes: int = 512 * KB) -> Preview:
        """Best-effort look at an object: a DataFrame for tables (csv, tsv, psv, json, jsonl, parquet, orc,
        feather, avro, excel, npy), the files in an archive (zip, tar, tar.gz, model.tar.gz, npz, PyTorch
        checkpoints), tensors in a .safetensors file, notebook cells, parsed JSON, text lines, an image,
        an audio / video player or PDF link, or a binary sample. Files without an extension are recognised
        by their first bytes. Downloads only what it needs."""
        bucket, key = parse_s3_uri(uri)
        meta = self.client.head_object(Bucket=bucket, Key=key)
        fmt, compression = detect_format(key)
        p = Preview(uri=s3_uri(bucket, key), kind="text", size=meta["ContentLength"], format=fmt,
                    compression=compression, content_type=meta.get("ContentType"))
        storage_class = meta.get("StorageClass", "STANDARD")
        if storage_class in ARCHIVE_CLASSES and 'ongoing-request="false"' not in meta.get("Restore", ""):
            p.kind, p.note = "unavailable", f"Object is in {storage_class}; restore it before reading."
            return p
        sniffed = False
        if p.size:
            fmt, p.compression, sniffed, p.note = self._confirm_format(bucket, key, fmt, compression)
        if fmt is None:
            content_type = p.content_type or ""
            fmt = next((kind for kind in ("image", "audio", "video") if content_type.startswith(kind + "/")),
                       "pdf" if content_type == "application/pdf" else None)
        p.format = fmt
        codec = p.compression or ""
        try:
            handler = getattr(self, f"_preview_{fmt}", None) if fmt else None
            if handler is None or not handler(p, uri, n, codec):
                self._preview_as_text(p, uri, n, codec, max_bytes, sniffed)
        except _READ_ERRORS as exc:  # broken or mislabeled file, missing optional package: show raw content
            try:
                data, p.truncated = self._read_head(uri, min(max_bytes, 64 * KB), codec)
            except _READ_ERRORS:
                data, p.truncated = self._read_head(uri, min(max_bytes, 64 * KB), "")
            p.kind = "binary" if _looks_binary(data) else "text"
            p.data = data[:512] if p.kind == "binary" else data.decode("utf-8", "replace").splitlines()[:n]
            p.note = " ".join(filter(None, [p.note, f"Couldn't read it as {fmt or 'a known format'}: {exc}"]))
        return p

    def _confirm_format(self, bucket: str, key: str, fmt: str | None, codec: str | None
                        ) -> tuple[str | None, str | None, bool, str]:
        """Check the name's guess against the first bytes -> (format, compression, sniffed, note)."""
        uri = s3_uri(bucket, key)
        sniffed_fmt, sniffed_codec = sniff_format(self.read_bytes(uri, 0, 511))
        note = ""
        if codec and sniffed_codec != codec:
            note = f"The name says .{codec} but the content isn't {codec}-compressed; reading it as-is."
            codec = None
        elif sniffed_codec and not codec:
            codec = sniffed_codec
        if fmt is None and codec:  # 'logs.gz', or a compressed file with no extension: look inside
            try:
                sniffed_fmt = sniff_format(self._read_head(uri, 512, codec)[0])[0]
            except _READ_ERRORS:
                sniffed_fmt = None
        return fmt or sniffed_fmt, codec, fmt is None and sniffed_fmt is not None, note

    # One _preview_<format> per format; each fills in the Preview and returns True (False = show as text).

    def _preview_csv(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "table", self.read_df(uri, nrows=n, fmt=p.format, compression=codec)
        return True

    _preview_tsv = _preview_psv = _preview_jsonl = _preview_csv

    def _preview_parquet(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "table", self.read_df(uri, nrows=n, fmt="parquet", compression=codec)
        if not codec:
            p.info = self.parquet_info(uri)
        return True

    def _preview_orc(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._random_access(bucket, key, codec) as handle:
            p.info = _columnar_info(p.format, handle)
            handle.seek(0)
            p.kind, p.data = "table", _read_arrow_table(p.format, handle, n, None).to_pandas()
        return True

    _preview_arrow = _preview_orc

    def _preview_avro(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        schema, avro_codec, records, _ = self._avro(uri, n, codec)
        fields = schema.get("fields", []) if isinstance(schema, dict) else []
        p.info = {"codec": avro_codec, "columns": [(f["name"], _avro_type_name(f["type"])) for f in fields]}
        pd = _require("pandas", "Table preview")
        rows = records if all(isinstance(r, dict) for r in records) else [{"value": r} for r in records]
        p.kind, p.data = "table", pd.json_normalize(rows) if rows else pd.DataFrame(columns=[f["name"] for f in fields])
        return True

    def _preview_excel(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        pd = _require("pandas", "Reading Excel")
        bucket, key = parse_s3_uri(uri)
        with self._random_access(bucket, key, codec) as handle, pd.ExcelFile(handle) as workbook:
            sheets = workbook.sheet_names
            p.info = {"sheets": sheets, "sheet": sheets[0]}
            p.kind, p.data = "table", workbook.parse(sheets[0], nrows=n)
        return True

    def _preview_npy(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._random_access(bucket, key, codec, buffer_size=64 * KB) as handle:
            shape, fortran, dtype = _npy_header(handle)
        p.info = {"shape": shape, "dtype": str(dtype)}
        array = self.read_npy(uri, nrows=n, compression=codec)
        if array.ndim <= 2:
            p.kind, p.data = "table", _require("pandas", "Table preview").DataFrame(array)
        else:
            p.kind, p.data = "text", repr(array[: min(n, 2)]).splitlines()[:n]
        return True

    def _preview_zip(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        listing = self.list_archive(uri, limit=max(n, 200), compression=codec)
        p.kind = "listing"
        p.data = [{"name": e.name, "size": e.size, "modified": e.modified} for e in listing.entries if not e.is_dir]
        files = sum(not e.is_dir for e in listing.entries)
        p.info = {"files": f"{files:,}" + ("" if listing.complete else "+"),
                  "unpacked_size": sum(e.size for e in listing.entries)}
        if not listing.complete:
            read = f" after reading {human_size(listing.bytes_read)}" if listing.bytes_read else ""
            p.note = f"Listing stopped{read}; there are more files (S3Analyzer.list_archive has limit= / max_bytes=)."
        return True

    _preview_tar = _preview_zip

    def _preview_torch(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        head = self.read_bytes(uri, 0, 3)
        if head.startswith(b"PK"):
            self._preview_zip(p, uri, n, codec)
        else:
            p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = ("PyTorch checkpoint. Not loaded: torch.load unpickles, which can run code from the file. "
                  "If you trust it: torch.load(ui.core.open(uri), weights_only=True).")
        return True

    def _preview_pickle(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = ("Pickle file. Not opened: unpickling can run code from the file. "
                  "If you trust it: pickle.load(ui.core.open(uri)).")
        return True

    def _preview_npz(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        arrays = []
        with self._random_access(bucket, key, codec, buffer_size=256 * KB) as handle, zipfile.ZipFile(handle) as archive:
            for info in archive.infolist()[: max(n, 200)]:
                with archive.open(info) as member:
                    shape, _, dtype = _npy_header(io.BytesIO(member.read(16 * KB)))
                arrays.append({"array": info.filename.removesuffix(".npy"), "shape": shape, "dtype": str(dtype),
                               "size": info.file_size})
        p.kind, p.data, p.info = "listing", arrays, {"arrays": len(arrays)}
        return True

    def _preview_safetensors(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        details = self.safetensors_info(uri)
        tensors = details["tensors"]
        p.kind, p.data = "listing", tensors[: max(n, 100)]
        p.info = {"tensors": len(tensors), "parameters": sum(t["parameters"] for t in tensors),
                  "dtypes": sorted({t["dtype"] for t in tensors if t["dtype"]}), "metadata": details["metadata"]}
        return True

    def _preview_notebook(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        if p.size > 50 * MB:
            raise ValueError("notebook is over 50 MB")
        notebook = self.read_json(uri, compression=codec)
        cells, meta = notebook.get("cells", []), notebook.get("metadata", {})
        rows = []
        for i, cell in enumerate(cells[: max(n, 50)]):
            source = cell.get("source", "")
            lines = ("".join(source) if isinstance(source, list) else source).splitlines()
            first = next((line.strip() for line in lines if line.strip()), "")
            rows.append({"#": i + 1, "type": cell.get("cell_type", "?"), "starts with": first[:100],
                         "lines": len(lines), "outputs": len(cell.get("outputs", []))})
        p.kind, p.data = "listing", rows
        p.info = {k: v for k, v in {"kernel": meta.get("kernelspec", {}).get("display_name"),
                                    "language": meta.get("language_info", {}).get("name"), "cells": len(cells)}.items()
                  if v is not None}
        return True

    def _preview_image(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        if p.size > 10 * MB:
            return False
        p.kind, p.data = "image", self.read_bytes(uri)
        mime = p.content_type if (p.content_type or "").startswith("image/") else None
        p.info["mime"] = mime or next((m for magic, m in _IMAGE_MIMES if p.data.startswith(magic)), "image/png")
        return True

    def _preview_audio(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "media", self.presigned_url(uri)
        mime = p.content_type if (p.content_type or "").startswith(p.format + "/") else None
        p.info = {"media": p.format, "mime": mime or mimetypes.guess_type(uri)[0] or f"{p.format}/*"}
        return True

    _preview_video = _preview_audio

    def _preview_pdf(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        url = self.presigned_url(uri)
        try:
            pypdf = importlib.import_module("pypdf")
        except ImportError:
            p.kind, p.data, p.info = "media", url, {"media": "pdf"}
            p.note = "Install pypdf (pip install pypdf) to see the page count and first page's text here."
            return True
        bucket, key = parse_s3_uri(uri)
        try:
            with self._random_access(bucket, key, codec) as handle:
                reader = pypdf.PdfReader(handle)
                text = reader.pages[0].extract_text() if len(reader.pages) else ""
                title = reader.metadata.title if reader.metadata else None
                p.info = {k: v for k, v in {"pages": len(reader.pages), "title": title, "url": url}.items() if v}
        except (pypdf.errors.PyPdfError, *_READ_ERRORS) as exc:
            p.kind, p.data, p.info = "media", url, {"media": "pdf"}
            p.note = f"pypdf couldn't read this PDF ({exc}); the link may still open it."
            return True
        lines = text.splitlines()
        p.kind, p.data, p.truncated = "text", lines[:n], len(lines) > n
        return True

    def _preview_as_text(self, p: Preview, uri: str, n: int, codec: str, max_bytes: int, sniffed: bool) -> None:
        """JSON, text lines or a binary sample (also the fallback for everything else)."""
        data, p.truncated = self._read_head(uri, max_bytes, codec)
        if p.format == "json":
            if not p.truncated:
                try:
                    parsed = json.loads(data)
                except ValueError:
                    pass  # maybe JSON lines with a .json name - tried below
                else:
                    if isinstance(parsed, list) and parsed and all(isinstance(r, dict) for r in parsed):
                        pd = _require("pandas", "Table preview")
                        p.kind, p.data = "table", pd.json_normalize(parsed[:n])
                        p.info["records"] = len(parsed)
                    else:
                        p.kind, p.data = "json", parsed
                    return
            try:  # Firehose / Spark often write JSON lines into '.json' files
                p.kind, p.data = "table", self.read_df(uri, nrows=n, fmt="jsonl", compression=codec)
                p.format = "jsonl"
                return
            except ValueError:
                p.kind = "text"
                if not sniffed:  # a '[...' log line without an extension isn't worth a warning
                    p.note = (f"JSON is larger than the {human_size(max_bytes)} preview window; showing raw text."
                              if p.truncated else "Not valid JSON; showing raw text.")
        if _looks_binary(data):
            p.kind, p.data = "binary", data[:512]
            return
        lines = data.decode("utf-8", errors="replace").splitlines()
        p.kind = "text"
        p.truncated = p.truncated or len(lines) > n
        p.data = lines[:n]

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


@dataclass
class _Media:
    url: str
    kind: str  # 'audio' | 'video'
    mime: str


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

_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")


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
        elif isinstance(block, _Media):
            size = ' style="max-width:100%;max-height:480px"' if block.kind == "video" else ""
            out.append(f'<{block.kind} controls preload="metadata"{size}><source src="{_esc(block.url)}" '
                       f'type="{_esc(block.mime)}"></{block.kind}>')
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
        elif isinstance(block, _Media):
            out += [f"({block.kind}: open this link in a browser to play it)", block.url]
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
                by: str = "size", name: Callable[[str], str] = str,
                costs: dict[str, float | None] | None = None) -> _Table:
    rows = [[name(key), f"{st.count:,}", human_size(st.size)] + ([human_money(costs.get(key))] if costs else [])
            for key, st in stats.items()]
    bars = [_share(st.size, total_size) if by == "size" else _share(st.count, total_count) for st in stats.values()]
    return _Table([label, "Objects", "Size"] + (["Est. $/month"] if costs else []), rows, title=title, bars=bars,
                  bar_label="% of size" if by == "size" else "% of objects")


def _policy_table(statements: list[PolicyStatement], title: str = "") -> _Table:
    return _Table(["Statement", "Effect", "Who", "Can", "On", "When"],
                  [[st.sid, st.effect, "\n".join(st.who), "\n".join(st.actions), "\n".join(st.resources),
                    "\n".join(st.conditions) or "always"] for st in statements], title=title, max_rows=0)


def _exposure(cfg: BucketConfig, account_block: dict[str, bool] | None) -> str:
    """One-word public access status for the overview table."""
    if cfg.policy_is_public:
        return "PUBLIC (policy)"
    if any(settings and all(settings.values()) for settings in (account_block, cfg.public_access_block)):
        return "blocked"
    if "public_access_block" in cfg.errors:
        return f"? ({cfg.errors['public_access_block']})"
    return "not blocked"


def _block_label(settings: dict[str, bool] | None) -> str:
    """Block Public Access settings -> 'all on' / '2/4 on' / 'not set'."""
    if not settings:
        return "not set"
    on = sum(bool(v) for v in settings.values())
    return "all on" if on == len(settings) else f"{on}/{len(settings)} on"


def _objects_table(title: str, objects: list[ObjectInfo], base: str = "") -> _Table:
    return _Table(["Key", "Size", "Last modified (UTC)", "Age", "Storage class"],
                  [[relative_key(o.key, base), human_size(o.size), _fmt_dt(o.last_modified), human_age(o.last_modified),
                    o.storage_class] for o in objects], title=title)


def _folder_label(folder: str) -> str:
    return folder or "(files at this level)"


_FORMAT_LABELS = {"arrow": "feather / arrow", "excel": "excel", "torch": "PyTorch checkpoint",
                  "notebook": "Jupyter notebook", "npy": "NumPy array", "npz": "NumPy arrays (npz)"}
_INFO_CARDS = {  # Preview.info key -> card label, in display order
    "rows": "Rows", "records": "Records", "columns": "Columns", "row_groups": "Row groups", "stripes": "Stripes",
    "batches": "Record batches", "sheets": "Sheets", "codec": "Codec", "compression": "Compression",
    "files": "Files", "unpacked_size": "Unpacked size", "arrays": "Arrays", "shape": "Shape", "dtype": "Dtype",
    "tensors": "Tensors", "parameters": "Parameters", "dtypes": "Dtypes", "kernel": "Kernel",
    "language": "Language", "cells": "Cells", "pages": "Pages", "title": "Title",
}
_LISTING_TITLES = {"zip": "Files", "tar": "Files", "torch": "Files in the checkpoint", "npz": "Arrays",
                   "safetensors": "Tensors", "notebook": "Cells"}


def _card_value(key: str, value: Any) -> str:
    if key == "columns":
        return f"{len(value):,}"
    if key.endswith("size") and isinstance(value, int):
        return human_size(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    if isinstance(value, (list, tuple)) and key != "shape":
        return ", ".join(map(str, value))
    return str(value)


def _listing_table(rows: list[dict[str, Any]], title: str) -> _Table:
    """list of dicts -> table; sizes, dates and big numbers formatted for reading."""
    if not rows:
        return _Table(["(empty)"], [], title=title)

    def cell(key: str, value: Any) -> str:
        if value is None:
            return "-"
        if key.endswith("size") and isinstance(value, int):
            return human_size(value)
        if isinstance(value, datetime):
            return _fmt_dt(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,}"
        return str(value)

    headers = list(rows[0])
    return _Table(headers, [[cell(k, row.get(k)) for k in headers] for row in rows], title=title)


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
    return _block_label(cfg.public_access_block)


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
        except (BotoCoreError, *_DATA_ERRORS) as exc:
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
    def _progress(self, label: str = "Scanning", unit: str = "objects") -> Iterator[Callable[[int], None]]:
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
                handle.update(HTML(f'<div style="opacity:.6">{_esc(label)}... {count:,} {_esc(unit)}</div>'))
            else:
                print(f"\r{label}... {count:,} {unit}", end="", file=sys.stderr, flush=True)

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

    def _price_basis(self) -> str:
        basis = "us-east-1 list prices" if self.core.prices == S3_PRICES else "your prices"
        return f"estimated at {basis}, storage only"

    def _account_block(self) -> dict[str, bool] | None:
        """Account-level Block Public Access, or None when it can't be read."""
        try:
            return self.core.account_public_access_block()
        except (ClientError, BotoCoreError, ValueError):
            return None

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
        """Bucket settings, policy in plain English, risks, and CloudWatch size, object count and
        estimated monthly cost (instant, even for huge buckets)."""
        cfg = self.core.bucket_config(bucket)
        account_block = self._account_block()
        cards = [
            ("Region", cfg.region or "?"),
            ("Versioning", _section(cfg, "versioning", cfg.versioning or "?")),
            ("Encryption", _encryption_label(cfg)),
            ("Block public access", _public_access_label(cfg)),
            ("Account block public access", "?" if account_block is None else _block_label(account_block)),
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
                    costs = cloudwatch_cost(usage.size_by_storage_type, self.core.prices)
                    monthly = sum(c for c in costs.values() if c)
                    cards = [("Objects", count), ("Total size", human_size(usage.total_size)),
                             ("Est. cost / month", human_money(monthly))] + cards
                    total = usage.total_size
                    storage_table = _Table(
                        ["Storage type", "Size", "Est. $/month"],
                        [[kind, human_size(size), human_money(costs[kind])]
                         for kind, size in usage.size_by_storage_type.items()],
                        title=f"Size by storage type (CloudWatch, {_fmt_dt(usage.as_of)} UTC; "
                              f"all versions; cost {self._price_basis()})",
                        bars=[_share(size, total) for size in usage.size_by_storage_type.values()],
                        bar_label="% of size")
        statements = explain_policy(cfg.policy, self.core.account_id())
        blocks.append(_Cards(cards))
        blocks += [_Note(message, level) for level, message in bucket_findings(cfg, account_block)]
        blocks += [_Note(message, level) for level, message in policy_findings(statements)]
        if storage_table:
            blocks.append(storage_table)
        if statements:
            blocks.append(_policy_table(statements, f"Bucket policy ({_plural(len(statements), 'statement')}; "
                                                    "policy() shows the JSON)"))
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

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """Every bucket in one table: size, estimated cost, versioning, encryption, public access,
        lifecycle and warnings. match='sagemaker-*' checks only matching bucket names."""
        with self._progress("Checking buckets", unit="buckets") as tick:
            reports = self.core.bucket_reports(match=match, metrics=metrics, progress=tick)
        account_block, account = self._account_block(), self.core.account_id()
        rows: list[tuple[int, list[str]]] = []
        warnings: list[list[str]] = []
        objects = size = 0
        cost = 0.0
        missing_metrics: list[str] = []
        for report in reports:
            cfg, usage = report.config, report.metrics
            found = bucket_findings(cfg, account_block) + policy_findings(explain_policy(cfg.policy, account))
            bucket_warnings = [message for level, message in found if level == "warn"]
            warnings += [[cfg.name, message] for message in bucket_warnings]
            bucket_size = bucket_cost = bucket_objects = None
            if usage is not None and usage.as_of is not None:
                bucket_size, bucket_objects = usage.total_size, usage.object_count
                bucket_cost = sum(c for c in cloudwatch_cost(usage.size_by_storage_type, self.core.prices).values() if c)
                size, cost, objects = size + bucket_size, cost + bucket_cost, objects + (bucket_objects or 0)
            elif metrics:
                missing_metrics.append(f"{cfg.name} ({report.metrics_error})" if report.metrics_error else cfg.name)
            enabled_rules = sum(rule.get("Status") == "Enabled" for rule in cfg.lifecycle_rules)
            rows.append((-1 if bucket_size is None else bucket_size, [
                cfg.name, cfg.region or "?", "-" if bucket_objects is None else f"{bucket_objects:,}",
                human_size(bucket_size), human_money(bucket_cost), _section(cfg, "versioning", cfg.versioning or "?"),
                _encryption_label(cfg), _exposure(cfg, account_block),
                _section(cfg, "lifecycle", str(enabled_rules)), str(len(bucket_warnings))]))
        rows.sort(key=lambda row: row[0], reverse=True)
        blocks: list[Any] = [
            _Title(f"All buckets ({len(reports)})", f"names matching {match!r}" if match else ""),
            _Cards([("Buckets", f"{len(reports):,}"), ("Objects", f"{objects:,}" if metrics else "-"),
                    ("Total size", human_size(size if metrics else None)),
                    ("Est. cost / month", human_money(cost if metrics else None)),
                    ("Buckets with warnings", f"{len({bucket for bucket, _ in warnings}):,}"),
                    ("Account block public access", "?" if account_block is None else _block_label(account_block)),
                    ("Regions", f"{len({r.config.region for r in reports if r.config.region}):,}")]),
        ]
        if missing_metrics:
            blocks.append(_Note(f"No CloudWatch size for {_plural(len(missing_metrics), 'bucket')}: "
                                f"{', '.join(missing_metrics[:10])}{' …' if len(missing_metrics) > 10 else ''}. "
                                "Metrics arrive once a day; new or empty buckets have none."))
        blocks.append(_Table(
            ["Bucket", "Region", "Objects", "Size", "Est. $/month", "Versioning", "Encryption", "Public access",
             "Lifecycle rules", "Warnings"], [row for _, row in rows],
            title=f"Buckets by size (CloudWatch, all versions; cost {self._price_basis()})",
            bars=[_share(max(s, 0), size) for s, _ in rows], bar_label="% of size", max_rows=0))
        if warnings:
            blocks.append(_Table(["Bucket", "Warning"], warnings,
                                 title="Warnings (bucket_info(name) shows every finding for one bucket)"))
        self._show(blocks)

    @_friendly_errors
    def policy(self, bucket: str) -> None:
        """Bucket policy in plain English: who can do what, on which files, when; risks; the raw JSON."""
        name = parse_s3_uri(bucket)[0]
        document = self.core.bucket_policy(name)
        blocks: list[Any] = [_Title(f"Bucket policy of s3://{name}")]
        if document is None:
            blocks.append(_Note("This bucket has no bucket policy. Access comes only from IAM policies "
                                "(and ACLs, if they're enabled)."))
            return self._show(blocks)
        statements = explain_policy(document, self.core.account_id())
        blocks.append(_Cards([
            ("Statements", f"{len(statements):,}"), ("Allow", f"{sum(st.effect == 'Allow' for st in statements):,}"),
            ("Deny", f"{sum(st.effect == 'Deny' for st in statements):,}"),
            ("Open to anyone", f"{sum(st.public for st in statements):,}"),
            ("Other accounts", ", ".join(sorted({a for st in statements for a in st.other_accounts})) or "none"),
        ]))
        blocks += [_Note(message, level) for level, message in policy_findings(statements)]
        blocks += [_policy_table(statements), _Text(json.dumps(document, indent=2), title="Policy JSON")]
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
        """Full dashboard for a prefix: totals, estimated monthly cost, findings, folders, file types,
        storage classes, size and age distribution, largest objects. Lists every key once (use limit= on huge prefixes)."""
        with self._progress() as tick:
            s = self.core.summarize(uri, top_n=top_n, folder_depth=folder_depth, limit=limit, progress=tick)
        base = base_prefix(parse_s3_uri(s.uri)[1])
        blocks: list[Any] = [_Title(f"Summary of {s.uri}", f"{s.object_count:,} objects scanned in {s.scan_seconds:.1f}s")]
        findings = [_Note(message, level) for level, message in summary_findings(s, self.core.prices)]
        if not s.object_count:
            blocks.append(_Note("No objects under this prefix."))
            return self._show(blocks + findings)
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
            ("Est. cost / month", human_money(s.monthly_cost)),
        ]))
        blocks += findings
        n, size = s.object_count, s.total_size
        blocks += [
            _stat_table(f"Folders (depth {folder_depth})", "Folder", s.by_folder, n, size, name=_folder_label),
            _stat_table("File types", "Extension", s.by_extension, n, size),
            _stat_table(f"Storage classes (current versions; cost {self._price_basis()})", "Storage class",
                        s.by_storage_class, n, size, costs=s.cost_by_storage_class),
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
                    ("Delete markers", f"{v.delete_markers:,}"), ("Deleted keys (still billed)", f"{v.deleted_keys:,}"),
                    ("Noncurrent est. cost / month", human_money(v.noncurrent_cost))]),
        ]
        if v.truncated:
            blocks.append(_Note(f"Stopped at limit={limit:,} versions; numbers are partial.", "warn"))
        if v.noncurrent.size:
            extra = _share(v.noncurrent.size, v.current.size)
            blocks.append(_Note(f"Old versions add {human_size(v.noncurrent.size)} ({extra:.0%} on top of current data). "
                                "A NoncurrentVersionExpiration lifecycle rule would clean them up.",
                                "warn" if extra > 0.25 else "info"))
        if v.deleted_keys:
            blocks.append(_Note(f"{_plural(v.deleted_keys, 'deleted file')} can still be listed (and maybe restored) "
                                "with deleted(uri)."))
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
    def deleted(self, uri: str, *, deleted_after: Any = None, limit: int | None = None) -> None:
        """Deleted files you can still bring back (versioned buckets), most recent first,
        e.g. deleted(uri, deleted_after='7d'). Shows the call that restores one; never changes anything."""
        with self._progress("Listing versions") as tick:
            d = self.core.deleted_files(uri, deleted_after=deleted_after, limit=limit, progress=tick)
        bucket, prefix = parse_s3_uri(d.uri)
        restorable = [f for f in d.files if f.restorable]
        since = f"deleted since {_fmt_dt(parse_time(deleted_after))} UTC" if deleted_after is not None else ""
        blocks: list[Any] = [
            _Title(f"Deleted files under {d.uri}", since),
            _Cards([("Deleted files", f"{len(d.files):,}"), ("Can be restored", f"{len(restorable):,}"),
                    ("Size to restore", human_size(sum(f.last_version.size for f in restorable))),
                    ("Old versions kept", human_size(sum(f.old_versions.size for f in d.files))),
                    ("Their est. cost / month", human_money(sum(f.monthly_cost for f in d.files)))]),
        ]
        if d.truncated:
            blocks.append(_Note(f"Stopped at limit={limit:,} versions; the list is partial.", "warn"))
        if not d.files:
            try:
                status = self.core.versioning_status(bucket)
            except (ClientError, BotoCoreError):
                status = None
            if status == "Disabled":
                blocks.append(_Note("Versioning is off for this bucket, so deleted files are gone for good. "
                                    "Turning versioning on protects future deletes.", "warn"))
            else:
                blocks.append(_Note("No deleted files here."))
            return self._show(blocks)
        if len(restorable) < len(d.files):
            blocks.append(_Note(f"{_plural(len(d.files) - len(restorable), 'delete marker')} have no versions left, "
                                "so there's nothing to restore. A lifecycle rule with ExpiredObjectDeleteMarker "
                                "removes them."))
        base = base_prefix(prefix)
        blocks.append(_Table(
            ["Key", "Deleted (UTC)", "When", "Last version size", "Versions kept", "Delete marker version id"],
            [[relative_key(f.key, base), _fmt_dt(f.deleted), human_age(f.deleted),
              human_size(f.last_version.size) if f.last_version else "nothing to restore", f"{f.old_versions.count:,}",
              f.marker_version_id] for f in d.files], title="Deleted files"))
        if restorable:
            example = restorable[0]
            blocks.append(_Text(
                "import boto3\n\n"
                "# Deleting the delete marker brings the last version back (needs s3:DeleteObjectVersion).\n"
                f"boto3.client('s3').delete_object(\n    Bucket={bucket!r},\n    Key={example.key!r},\n"
                f"    VersionId={example.marker_version_id!r},\n)", title="How to restore a file"))
        self._show(blocks)

    @_friendly_errors
    def uploads(self, uri: str, *, with_sizes: bool = True) -> None:
        """Incomplete multipart uploads (billed, but invisible in normal listings)."""
        uploads = self.core.incomplete_uploads(uri, with_sizes=with_sizes)
        total = sum(u.size or 0 for u in uploads)
        cost = (object_monthly_cost(total, "STANDARD", self.core.prices) or 0.0) if with_sizes else None
        blocks: list[Any] = [
            _Title(f"Incomplete multipart uploads under {s3_uri(*parse_s3_uri(uri))}"),
            _Cards([("Uploads", f"{len(uploads):,}")] + ([("Stored parts", human_size(total)),
                    ("Est. cost / month (STANDARD rate)", human_money(cost))] if with_sizes else [])),
        ]
        if uploads:
            blocks.append(_Note("These parts cost storage until aborted; add an AbortIncompleteMultipartUpload "
                                "lifecycle rule to clean them up automatically.", "warn"))
        blocks.append(_Table(["Key", "Started (UTC)", "Age", "Parts", "Size", "Upload id"],
                             [[u.key, _fmt_dt(u.initiated), human_age(u.initiated),
                               "-" if u.parts is None else str(u.parts), human_size(u.size), u.upload_id[:16] + "…"]
                              for u in uploads]))
        self._show(blocks)

    @_friendly_errors
    def what_if(self, uri: str, *, move_after: int | dict[int, str] | None = None, to: str | None = None,
                delete_after: int | None = None, limit: int | None = None) -> None:
        """Preview a lifecycle rule before adding it: what it would move or delete today and the money
        saved, e.g. what_if(uri, move_after=30, to='STANDARD_IA') or what_if(uri, delete_after=365)."""
        with self._progress() as tick:
            impact = self.core.simulate_lifecycle(uri, move_after=move_after, to=to, delete_after=delete_after,
                                                  limit=limit, progress=tick)
        moved = Stat(sum(st.count for st in impact.moves.values()), sum(st.size for st in impact.moves.values()))
        savings = impact.monthly_savings
        cards = [
            ("Files checked", f"{impact.scanned.count:,}{'+' if impact.truncated else ''}"),
            ("Would move", f"{moved.count:,} ({human_size(moved.size)})"),
            ("Would delete", f"{impact.expired.count:,} ({human_size(impact.expired.size)})"),
            ("Cost now / month", human_money(impact.cost_before)),
            ("Cost after / month", human_money(impact.cost_after)),
            ("Saving / month", human_money(savings)),
            ("One-time cost", human_money(impact.one_time_cost)),
        ]
        if savings > 0 and impact.one_time_cost > 0:
            months = impact.one_time_cost / savings
            cards.append(("Pays for itself in", "under a month" if months < 1 else f"{months:,.1f} months"))
        blocks: list[Any] = [_Title(f"Lifecycle what-if for {impact.uri}", impact.describe()), _Cards(cards)]
        if impact.truncated:
            blocks.append(_Note(f"Scan stopped at limit={limit:,}; numbers cover only part of the prefix.", "warn"))
        if not (moved.count or impact.expired.count or impact.too_small.count):
            blocks.append(_Note("Nothing under this prefix is old enough for this rule today."))
        elif savings <= 0:
            blocks.append(_Note("This rule would not lower the monthly bill for these files.", "warn"))
        if impact.too_small.count:
            blocks.append(_Note(f"{_plural(impact.too_small.count, 'file')} ({human_size(impact.too_small.size)}) "
                                "are old enough to move but under 128 KB. S3 lifecycle doesn't move files that small, "
                                "so they stay where they are."))
        if impact.early_removals.count:
            blocks.append(_Note(f"{_plural(impact.early_removals.count, 'file')} would leave their storage class before "
                                "its minimum duration (30 days for IA, 90 for Glacier IR / Glacier, 180 for Deep Archive). "
                                "S3 bills the remaining days once; that's part of the one-time cost.", "warn"))
        if moved.count:
            blocks.append(_Note("Savings count storage only. Reading files in IA and Glacier Instant Retrieval adds a "
                                "retrieval fee per GB, and GLACIER / DEEP_ARCHIVE files must be restored before reading."))
        if impact.expired.count:
            try:
                versioned = self.core.versioning_status(uri) != "Disabled"
            except (ClientError, BotoCoreError):
                versioned = False
            if versioned:
                blocks.append(_Note("Versioning is on: a lifecycle delete keeps the old data as a noncurrent version, "
                                    "billed until a NoncurrentVersionExpiration rule removes it. The saving above "
                                    "needs that rule too.", "warn"))
        parts = [(f"move to {cls}", st) for cls, st in impact.moves.items()]
        parts += [("delete", impact.expired)] if impact.expired.count else []
        parts += [("stay (under 128 KB)", impact.too_small)] if impact.too_small.count else []
        parts.append(("no change", Stat(impact.scanned.count - sum(st.count for _, st in parts),
                                        impact.scanned.size - sum(st.size for _, st in parts))))
        blocks.append(_Table(["What happens", "Files", "Size"],
                             [[label, f"{st.count:,}", human_size(st.size)] for label, st in parts],
                             title=f"If the rule ran today (cost {self._price_basis()})",
                             bars=[_share(st.count, impact.scanned.count) for _, st in parts], bar_label="% of files"))
        blocks.append(_Text(json.dumps({"Rules": [impact.rule()]}, indent=2),
                            title="The rule: put_bucket_lifecycle_configuration replaces ALL of a bucket's rules, "
                                  "so add this to the existing list"))
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
        """Peek inside a file: tables (csv, tsv, psv, json, jsonl, parquet, orc, feather, avro, excel, npy)
        with their schema, archive contents (zip, tar, tar.gz, model.tar.gz, npz, .pt), safetensors tensors,
        notebook cells, pretty JSON, text, images, audio / video players, PDFs, or a hex dump.
        Downloads only what it needs; files without an extension are recognised by their content."""
        p = self.core.preview(uri, n=n)
        detail = " · ".join(filter(None, [human_size(p.size), _FORMAT_LABELS.get(p.format or "", p.format)
                                          or p.content_type, f"{p.compression}-compressed" if p.compression else ""]))
        blocks: list[Any] = [_Title(f"Preview of {p.uri}", detail)]
        if p.note:
            blocks.append(_Note(p.note, "warn"))
        cards = [(label, _card_value(key, p.info[key])) for key, label in _INFO_CARDS.items()
                 if p.info.get(key) not in (None, "", [])]
        if cards:
            blocks.append(_Cards(cards))
        if p.kind == "table":
            blocks.append(_Frame(p.data, title=f"First {len(p.data):,} rows"))
        elif p.kind == "listing":
            blocks.append(_listing_table(p.data, _LISTING_TITLES.get(p.format or "", "Contents")))
        elif p.kind == "json":
            text = json.dumps(p.data, indent=2, default=str, ensure_ascii=False)
            blocks.append(_Text(_clip(text, 20_000)))
        elif p.kind == "text":
            blocks.append(_Text("\n".join(p.data or []), title=f"First {len(p.data or [])} lines"))
        elif p.kind == "image":
            blocks.append(_Image(p.data, p.info.get("mime") or p.content_type or "image/png"))
        elif p.kind == "media" and p.info.get("media") in ("audio", "video"):
            blocks += [_Media(p.data, p.info["media"], p.info.get("mime", "")),
                       _Link(p.data, "Open in a new tab (link valid 1 hour)")]
        elif p.kind == "media":
            blocks.append(_Link(p.data, "Open the PDF (link valid 1 hour)"))
        elif p.kind == "binary":
            blocks.append(_Text(_hexdump(p.data), title="Binary content (first 512 bytes)"))
        if p.info.get("url") and p.kind != "media":
            blocks.append(_Link(p.info["url"], "Open the file (link valid 1 hour)"))
        if p.info.get("columns"):
            blocks.append(_Table(["Column", "Type"], [list(c) for c in p.info["columns"]], title="Schema"))
        if p.info.get("metadata"):
            blocks.append(_Table(["Key", "Value"], [[k, _clip(str(v), 200)] for k, v in p.info["metadata"].items()],
                                 title="Metadata"))
        if p.truncated and p.kind == "text":
            blocks.append(_Note(f"Showing the first {n} lines; pass n= for more."))
        self._show(blocks)

    @_friendly_errors
    def link(self, uri: str, *, expires: int = 3600) -> None:
        """Clickable presigned download link (no AWS login needed until it expires)."""
        url = self.core.presigned_url(uri, expires=expires)
        self._show([_Link(url, f"Download {parse_s3_uri(uri)[1].rsplit('/', 1)[-1]} (valid {expires // 60} min)")])
