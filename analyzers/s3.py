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
    ui.duplicates("s3://my-bucket/data/")           # identical files (size, ETag, SHA-256) and what they cost
    ui.download("s3://my-bucket/data/")             # a file or folder to the notebook's disk, with progress
    ui.download_zip("s3://my-bucket/data/")         # the same as one .zip, once size / disk / access checks pass

    s3 = ui.core                                    # same analyzer, raw data
    summary = s3.summarize("s3://my-bucket/data/")
    df = s3.read_df("s3://my-bucket/data/part-0.parquet", nrows=1000)
    files = objects_to_df(s3.find("s3://my-bucket/data/", extensions=["csv"]))
"""

from __future__ import annotations

import bz2
import difflib
import fnmatch
import functools
import gzip
import hashlib
import heapq
import html
import importlib
import importlib.util
import inspect
import io
import json
import lzma
import math
import mimetypes
import os
import posixpath
import re
import shutil
import struct
import sys
import tarfile
import threading
import time
import zipfile
import zlib
from xml.etree import ElementTree
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, BinaryIO, Callable, Iterable, Iterator

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
    "docx": "docx", "docm": "docx", "dotx": "docx", "dotm": "docx",
    "pptx": "pptx", "pptm": "pptx", "potx": "pptx", "ppsx": "pptx", "ppsm": "pptx",
    "doc": "oldoffice", "dot": "oldoffice", "ppt": "oldoffice", "pps": "oldoffice", "pot": "oldoffice",
    "msg": "oldoffice",
    **{ext: "text" for ext in _TEXT_EXTS},
}
_CSV_SEPARATORS = {"csv": ",", "tsv": "\t", "psv": "|"}


def _zstd_reader(stream: Any) -> Any:
    try:
        from compression import zstd  # pyright: ignore[reportMissingImports]  # Python 3.14+
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
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "oldoffice"),  # OLE2: Office 97-2003 files
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

# ---- Documents: PDF (with pypdf), Word and PowerPoint (standard library only)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_PARTS = {"word/document.xml": "docx", "ppt/presentation.xml": "pptx", "xl/workbook.xml": "excel"}
_OLD_OFFICE = {"doc": "Word 97-2003 document", "dot": "Word 97-2003 template", "ppt": "PowerPoint 97-2003 deck",
               "pps": "PowerPoint 97-2003 show", "pot": "PowerPoint 97-2003 template", "msg": "Outlook message"}


def _count_words(text: str) -> int:
    """Words in text; list markers and table separators ('-', '|') don't count."""
    return sum(any(ch.isalnum() for ch in word) for word in text.split())


def _old_office_note(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower() if "." in key.rsplit("/", 1)[-1] else ""
    what = _OLD_OFFICE.get(ext, "Office 97-2003 file")
    modern = {"ppt": "pptx", "pps": "pptx", "pot": "pptx"}.get(ext, "docx")
    return (f"{what} (the old binary format): its text can't be read here. Save it as .{modern} in Office, "
            f"or convert it with LibreOffice (soffice --headless --convert-to {modern} FILE), then preview that.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_xml(archive: zipfile.ZipFile, name: str, *, required: bool = True) -> Any:
    """Parse one XML part of an Office file (None if it's missing and not required)."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        if required:
            raise ValueError(f"Not a valid Office file: {name} is missing") from None
        return None
    if info.file_size > 256 * MB:
        raise ValueError(f"{name} unpacks to {human_size(info.file_size)}; not read")
    data = archive.read(info)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:  # Office never writes these; refuse entity tricks
        raise ValueError(f"{name} has a DTD, which Office files never contain; not parsed")
    return ElementTree.fromstring(data)


def _zip_rels(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationships of an Office part -> {id: (type, path inside the zip)}."""
    folder, name = posixpath.split(part)
    root = _zip_xml(archive, posixpath.join(folder, "_rels", name + ".rels"), required=False)
    rels: dict[str, tuple[str, str]] = {}
    for rel in [] if root is None else root.iter(f"{_REL}Relationship"):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External" or not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        rels[rel.get("Id", "")] = (rel.get("Type", "").rsplit("/", 1)[-1], path)
    return rels


def _office_properties(archive: zipfile.ZipFile) -> dict[str, Any]:
    """Title, author and the page / slide count the app saved (docProps/core.xml and app.xml)."""
    props: dict[str, Any] = {}
    for part in ("docProps/core.xml", "docProps/app.xml"):
        root = _zip_xml(archive, part, required=False)
        for elem in [] if root is None else root:
            value = (elem.text or "").strip()
            if value:
                props[_local(elem.tag).lower()] = value
    return {"title": props.get("title"), "author": props.get("creator"),
            "pages": int(props["pages"]) if props.get("pages", "").isdigit() else None}


def office_kind(source: Any) -> str | None:
    """'docx', 'pptx' or 'excel' for an Office zip file (by the parts inside it), else None."""
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
    return next((kind for part, kind in _OFFICE_PARTS.items() if part in names), None)


def _word_text(paragraph: Any) -> str:
    out = []
    for elem in paragraph.iter():
        if elem.tag == f"{_W}t":  # deleted text (w:delText) and field codes (w:instrText) are skipped
            out.append(elem.text or "")
        elif elem.tag == f"{_W}tab":
            out.append("\t")
        elif elem.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out).strip()


def parse_docx(source: Any, uri: str = "") -> Document:
    """A Word .docx (path or binary file object) -> Document: paragraphs and tables in order,
    headings (by style), list items prefixed with '- ', title and author. No packages needed."""
    with zipfile.ZipFile(source) as archive:
        root = _zip_xml(archive, "word/document.xml")
        styles = _zip_xml(archive, "word/styles.xml", required=False)
        props = _office_properties(archive)
    style_names, based_on, numbered = {}, {}, set()
    for style in [] if styles is None else styles.iter(f"{_W}style"):
        style_id, name, parent = style.get(f"{_W}styleId"), style.find(f"{_W}name"), style.find(f"{_W}basedOn")
        style_names[style_id] = "" if name is None else name.get(f"{_W}val", "")
        based_on[style_id] = None if parent is None else parent.get(f"{_W}val")
        if style.find(f"{_W}pPr/{_W}numPr") is not None:
            numbered.add(style_id)

    def style_is_list(style_id: str | None) -> bool:  # bullets can come from the style or one it's based on
        for _ in range(10):
            if style_id is None:
                return False
            if style_id in numbered:
                return True
            style_id = based_on.get(style_id)
        return False
    doc = Document(uri=uri, kind="docx", title=props["title"], author=props["author"], page_count=props["pages"])

    def heading_level(paragraph: Any) -> int | None:
        properties = paragraph.find(f"{_W}pPr")
        if properties is None:
            return None
        style = properties.find(f"{_W}pStyle")
        style_id = "" if style is None else style.get(f"{_W}val", "")
        name = (style_names.get(style_id) or style_id).lower().replace(" ", "")
        if name == "title":
            return 0
        if match := re.fullmatch(r"heading(\d)", name):
            return int(match.group(1))
        outline = properties.find(f"{_W}outlineLvl")
        return None if outline is None else int(outline.get(f"{_W}val", "0")) + 1

    def walk(container: Any) -> None:
        for child in container:
            if child.tag == f"{_W}p":
                text = _word_text(child)
                if not text:
                    continue
                level = heading_level(child)
                if level is not None:
                    doc.headings.append((level, text))
                style = child.find(f"{_W}pPr/{_W}pStyle")
                listed = (child.find(f"{_W}pPr/{_W}numPr") is not None
                          or style_is_list(None if style is None else style.get(f"{_W}val")))
                doc.parts.append(f"- {text}" if listed else text)
            elif child.tag == f"{_W}tbl":
                rows = [["\n".join(filter(None, (_word_text(p) for p in cell.iter(f"{_W}p"))))
                         for cell in row.findall(f"{_W}tc")] for row in child.findall(f"{_W}tr")]
                if any(any(cells) for cells in rows):
                    doc.tables.append(rows)
                    doc.parts.append("\n".join(" | ".join(cells) for cells in rows))
            elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml", f"{_W}ins"):
                walk(child)  # content controls and tracked insertions wrap ordinary paragraphs

    body = root.find(f"{_W}body")
    walk(root if body is None else body)
    return doc


def _drawing_text(paragraph: Any) -> str:
    parts = [(elem.text or "") if elem.tag == f"{_A}t" else "\n"
             for elem in paragraph.iter() if elem.tag in (f"{_A}t", f"{_A}br")]
    return "".join(parts).strip()


def _slide_text(root: Any, skip_placeholders: tuple[str, ...] = ()) -> tuple[str | None, list[str], list[list[list[str]]]]:
    """(title, paragraphs, tables) of a slide or notes page."""
    title, paragraphs, tables = None, [], []
    for shape in root.iter(f"{_P}sp"):
        placeholder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        kind = None if placeholder is None else placeholder.get("type")
        if kind in skip_placeholders:
            continue
        texts = [t for t in (_drawing_text(p) for p in shape.iter(f"{_A}p")) if t]
        if kind in ("title", "ctrTitle") and texts and title is None:
            title = " ".join(texts)
        paragraphs += texts
    for table in root.iter(f"{_A}tbl"):
        rows = [["\n".join(t for t in (_drawing_text(p) for p in cell.iter(f"{_A}p")) if t)
                 for cell in row.findall(f"{_A}tc")] for row in table.findall(f"{_A}tr")]
        if any(any(cells) for cells in rows):
            tables.append(rows)
            paragraphs.append("\n".join(" | ".join(cells) for cells in rows))
    return title, paragraphs, tables


def parse_pptx(source: Any, uri: str = "", *, slides: Iterable[int] | None = None) -> Document:
    """A PowerPoint .pptx -> Document: one part per slide in presentation order, slide titles,
    tables and speaker notes. slides: 1-based slide numbers to read. No packages needed."""
    with zipfile.ZipFile(source) as archive:
        presentation = _zip_xml(archive, "ppt/presentation.xml")
        rels = _zip_rels(archive, "ppt/presentation.xml")
        props = _office_properties(archive)
        paths = [rels[ref][1] for ref in (s.get(f"{_R}id") for s in presentation.iter(f"{_P}sldId")) if ref in rels]
        wanted = range(1, len(paths) + 1) if slides is None else [int(n) for n in slides]
        bad = [n for n in wanted if not 1 <= n <= len(paths)]
        if bad:
            raise ValueError(f"Slide {bad[0]} doesn't exist; the deck has {len(paths)} slides")
        doc = Document(uri=uri, kind="pptx", title=props["title"], author=props["author"], page_count=len(paths))
        for number in wanted:
            path = paths[number - 1]
            title, paragraphs, tables = _slide_text(_zip_xml(archive, path))
            notes = ""
            for kind, notes_path in _zip_rels(archive, path).values():
                if kind == "notesSlide":
                    _, note_paragraphs, _ = _slide_text(_zip_xml(archive, notes_path), ("sldNum", "sldImg", "hdr", "ftr", "dt"))
                    notes = "\n".join(note_paragraphs)
            doc.parts.append("\n".join(paragraphs))
            doc.numbers.append(number)
            doc.slide_titles.append(title)
            doc.notes.append(notes)
            doc.tables += tables
    return doc


def parse_pdf(source: Any, uri: str = "", *, pages: Iterable[int] | None = None, password: str | None = None) -> Document:
    """A PDF (path or seekable binary file) -> Document with one part per page. Needs pypdf.
    pages: 1-based page numbers to read. Scanned pages have no text layer and come back empty."""
    pypdf = _require("pypdf", "Reading PDF text")
    try:
        reader = pypdf.PdfReader(source)
        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ValueError("The PDF is password-protected; pass password=")
        count = len(reader.pages)
        wanted = list(range(1, count + 1)) if pages is None else [int(n) for n in pages]
        bad = [n for n in wanted if not 1 <= n <= count]
        if bad:
            raise ValueError(f"Page {bad[0]} doesn't exist; the PDF has {count} pages")
        parts = [reader.pages[n - 1].extract_text() or "" for n in wanted]
        meta = reader.metadata
        title, author = (meta.title, meta.author) if meta else (None, None)
    except pypdf.errors.PyPdfError as exc:
        raise ValueError(f"pypdf couldn't read this PDF: {exc}") from exc
    return Document(uri=uri, kind="pdf", parts=parts, numbers=wanted, title=title or None, author=author or None,
                    page_count=count)


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
class DuplicateGroup:
    """Files with identical content. The first is the one to keep (see group_duplicates); the rest are copies."""

    size: int
    objects: list[ObjectInfo]
    sha256: str | None = None  # hex SHA-256 of the content, when it was read
    # How the files are known to match: 'ETag' (from the listing), 'SHA-256' (every file's content hashed) or
    # 'SHA-256 + ETag' (some by each, e.g. one file per ETag hashed)
    matched_by: str = "ETag"
    monthly_cost: float = 0.0  # estimated USD per month to store the copies

    @property
    def keep(self) -> ObjectInfo:
        return self.objects[0]

    @property
    def copies(self) -> list[ObjectInfo]:
        return self.objects[1:]

    @property
    def reclaimable(self) -> int:
        return self.size * (len(self.objects) - 1)


@dataclass
class DuplicateReport:
    """Identical files under a prefix (see S3Analyzer.find_duplicates and group_duplicates)."""

    uri: str
    method: str = "etag"  # 'etag' (listing only), 'hash' or 'strict' (see S3Analyzer.find_duplicates)
    groups: list[DuplicateGroup] = field(default_factory=list)  # biggest reclaimable size first
    scanned: Stat = field(default_factory=Stat)  # files listed, folder markers excluded
    candidates: Stat = field(default_factory=Stat)  # files of at least min_size sharing their size with another
    not_compared: Stat = field(default_factory=Stat)  # candidates whose ETag differs and whose content wasn't read
    files_by_folder: dict[str, int] = field(default_factory=dict)  # folder -> files of at least min_size in it
    unreadable: dict[str, str] = field(default_factory=dict)  # key -> why it couldn't be read
    files_read: int = 0  # files read to hash (the first 64 KB or all of it)
    bytes_read: int = 0
    requests: int = 0  # GET requests made to read them
    max_read: int | None = None  # the read budget, in bytes (None = no cap)
    read_capped: bool = False  # some files weren't read because of max_read
    truncated: bool = False  # the listing stopped at `limit`
    versioning: str | None = None  # the bucket's versioning status, when it could be read
    scan_seconds: float = 0.0

    @property
    def copies(self) -> int:
        return sum(len(g.objects) - 1 for g in self.groups)

    @property
    def reclaimable(self) -> int:
        return sum(g.reclaimable for g in self.groups)

    @property
    def monthly_cost(self) -> float:
        return sum(g.monthly_cost for g in self.groups)

    def to_df(self):
        """One row per file in a duplicate group: group number, 'keep' or 'copy', key, size, sha256, ..."""
        pd = _require("pandas", "DuplicateReport.to_df")
        columns = ["group", "role", "key", "size", "last_modified", "storage_class", "matched_by", "sha256", "etag",
                   "uri"]
        return pd.DataFrame([[n, "keep" if i == 0 else "copy", o.key, o.size, o.last_modified, o.storage_class,
                              g.matched_by, g.sha256, o.etag, o.uri]
                             for n, g in enumerate(self.groups, 1) for i, o in enumerate(g.objects)], columns=columns)


@dataclass
class DuplicateFolder:
    """A folder holding files that have an identical file somewhere (see duplicate_folders)."""

    folder: str  # full prefix of the folder, '' = the bucket's top level
    files: int  # files of at least min_size in the folder
    duplicated: Stat = field(default_factory=Stat)  # its files that have an identical file anywhere
    outside: int = 0  # its files that have an identical file in another folder
    elsewhere: dict[str, int] = field(default_factory=dict)  # folder holding copies -> files; itself = same folder

    @property
    def all_copies(self) -> bool:
        """Every file here also exists in another folder, so the folder could go without losing data."""
        return 0 < self.files == self.outside


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
class FolderDownload:
    """What S3Analyzer.download_folder fetched."""

    uri: str
    path: str  # the local folder
    downloaded: Stat = field(default_factory=Stat)
    already_there: Stat = field(default_factory=Stat)  # same size and time as the local file: not fetched again
    skipped: dict[str, str] = field(default_factory=dict)  # key -> why it wasn't downloaded
    truncated: bool = False  # the listing stopped at `limit`
    seconds: float = 0.0


@dataclass
class ZipPlan:
    """What download_zip would put in a .zip, and whether this notebook can make it (see S3Analyzer.plan_zip)."""

    uri: str
    path: str  # the .zip file it would write
    max_size: int  # bytes of files allowed in one zip
    max_files: int
    files: list[tuple[ObjectInfo, str]] = field(default_factory=list)  # (object, its name in the zip), key order
    archived: Stat = field(default_factory=Stat)  # GLACIER / DEEP_ARCHIVE files left out (they need a restore)
    left_out: dict[str, str] = field(default_factory=dict)  # key -> why it isn't in the zip
    more: bool = False  # counting stopped at max_files: there are more files
    disk_free: int | None = None  # bytes free where the zip goes
    memory_free: int | None = None  # RAM available, when it can be read
    read_error: str | None = None  # what reading one file returned, e.g. 'AccessDenied'; None = it worked
    probed: str | None = None  # the key read to check access (None = nothing to read)

    @property
    def size(self) -> int:
        return sum(obj.size for obj, _ in self.files)

    @property
    def space_needed(self) -> int:
        """Most the zip can take on disk: the files as they are, plus the zip's own headers."""
        return self.size + self.size // 1000 + sum(130 + 2 * len(name.encode()) for _, name in self.files) + 100

    @property
    def can_download(self) -> bool:
        return all(ok is not False for _, ok, _ in zip_checks(self))


@dataclass
class ZipDownload:
    """What S3Analyzer.download_zip did: the checks it ran (plan) and the zip it wrote."""

    plan: ZipPlan
    written: bool = False  # False: a check failed, or dry_run=True
    zip_size: int = 0
    files: Stat = field(default_factory=Stat)  # files in the zip
    failed: dict[str, str] = field(default_factory=dict)  # key -> error, files that couldn't be read while zipping
    seconds: float = 0.0


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
class Document:
    """Text of a PDF, Word (.docx) or PowerPoint (.pptx) file (see S3Analyzer.read_document)."""

    uri: str
    kind: str  # 'pdf' | 'docx' | 'pptx'
    parts: list[str] = field(default_factory=list)  # pages / slides / Word paragraphs and tables, in order
    numbers: list[int] = field(default_factory=list)  # page or slide number of each part (PDF, PPTX)
    title: str | None = None
    author: str | None = None
    page_count: int | None = None  # PDF pages, PPTX slides; for a DOCX, the count Word last saved (can be stale)
    headings: list[tuple[int, str]] = field(default_factory=list)  # DOCX outline: (level, text), 0 = title
    slide_titles: list[str | None] = field(default_factory=list)  # PPTX, one per part
    notes: list[str] = field(default_factory=list)  # PPTX speaker notes, one per part
    tables: list[list[list[str]]] = field(default_factory=list)  # DOCX / PPTX tables: rows of cell texts

    @property
    def text(self) -> str:
        return "\n\n".join(part for part in self.parts if part)

    @property
    def word_count(self) -> int:
        return _count_words(self.text)


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


_ZIP_SMALL_FILE = 8 * MB  # download_zip fetches files up to this size ahead, 16 at a time; bigger ones stream
_ALREADY_COMPRESSED = {"gz", "tgz", "bz2", "xz", "zst", "zip", "7z", "rar", "jar", "whl", "parquet", "orc", "npz",
                       "png", "jpg", "jpeg", "gif", "webp", "mp3", "m4a", "aac", "ogg", "flac", "mp4", "mov", "webm",
                       "docx", "xlsx", "pptx"}  # stored in a zip as they are: compressing them again gains nothing
DUPLICATE_METHODS = ("etag", "hash", "strict")
HASH_HEAD_BYTES = 64 * KB  # find_duplicates hashes this much of a file first; the rest only if the starts match


def _etag_clusters(same_size: list[ObjectInfo]) -> list[list[ObjectInfo]]:
    """Files of one size -> lists of files sharing an ETag (a file without one is on its own)."""
    clusters: dict[str, list[ObjectInfo]] = {}
    for obj in same_size:
        clusters.setdefault(obj.etag or obj.uri, []).append(obj)
    return list(clusters.values())


def files_to_hash(objects: Iterable[ObjectInfo], *, min_size: int | str = 1, method: str = "hash"
                  ) -> list[list[ObjectInfo]]:
    """The files find_duplicates reads, one list per size, biggest possible saving first.

    A file whose size no other file has can't be a copy, and files with the same size and ETag already match,
    so method='hash' reads one file per ETag, only for sizes where the ETags differ, picking one outside GLACIER /
    DEEP_ARCHIVE when it can (those can't be read without a restore). 'strict' reads every file that shares its
    size with another; 'etag' reads nothing.
    """
    if method not in DUPLICATE_METHODS:
        raise ValueError(f"method must be one of {', '.join(map(repr, DUPLICATE_METHODS))}")
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
            plan.append([next((o for o in c if o.storage_class not in ARCHIVE_CLASSES), c[0]) for c in clusters])
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
    """Group identical files -> DuplicateReport. Files match when they have the same size and either the same
    ETag or the same SHA-256 in `hashes` (uri -> hex digest). `distinct` lists uris already known to differ from
    every other file of their size (their first bytes differ). Works on any list of ObjectInfo, e.g. S3 Inventory
    rows. The groups are sorted by the space their copies take. In each, the file to keep comes first: one that can
    be read without a restore (not GLACIER / DEEP_ARCHIVE), in the folder with the smallest share of duplicated
    files so that a folder of copies empties out, then the oldest."""
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

    found: list[tuple[int, list[list[ObjectInfo]]]] = []  # (size, the ETag clusters making up one group)
    for size, same in by_size.items():
        if len(same) < 2:
            continue
        report.candidates.count += len(same)
        report.candidates.size += size * len(same)
        clusters = _etag_clusters(same)
        parent = list(range(len(clusters)))  # union-find: clusters with a SHA-256 in common are one group
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
        found += [(size, parts) for parts in merged.values() if sum(map(len, parts)) > 1]

    duplicated = Counter(base_prefix(o.key) for _, parts in found for cluster in parts for o in cluster)

    def keep_first(obj: ObjectInfo) -> tuple[bool, float, datetime, str]:
        folder = base_prefix(obj.key)
        archived = obj.storage_class in ARCHIVE_CLASSES
        return archived, duplicated[folder] / max(folders[folder], 1), obj.last_modified, obj.key

    for size, parts in found:
        members = sorted((o for cluster in parts for o in cluster), key=keep_first)
        hashed = [o for o in members if o.uri in hashes]
        by_hash = len(parts) > 1 or len(hashed) > 1  # some files are known to match by their SHA-256
        matched_by = "SHA-256" if len(hashed) == len(members) else "SHA-256 + ETag" if by_hash else "ETag"
        cost = sum(object_monthly_cost(o.size, o.storage_class, prices) or 0.0 for o in members[1:])
        report.groups.append(DuplicateGroup(size, members, hashes[hashed[0].uri] if hashed else None, matched_by, cost))
    report.groups.sort(key=lambda g: (-g.reclaimable, g.keep.key))
    return report


def duplicate_folders(report: DuplicateReport) -> list[DuplicateFolder]:
    """Folders holding duplicated files, most duplicated bytes first. A folder whose files all exist in other
    folders too (all_copies) can go without losing data, as long as those other copies stay."""
    folders: dict[str, DuplicateFolder] = {}
    for group in report.groups:
        where = Counter(base_prefix(o.key) for o in group.objects)
        for obj in group.objects:
            here = base_prefix(obj.key)
            entry = folders.setdefault(here, DuplicateFolder(here, report.files_by_folder.get(here, 0)))
            entry.duplicated.add(obj.size)
            entry.outside += len(where) > 1
            for folder, count in where.items():
                if folder != here or count > 1:
                    entry.elsewhere[folder] = entry.elsewhere.get(folder, 0) + 1
    for entry in folders.values():
        entry.elsewhere = dict(sorted(entry.elsewhere.items(), key=lambda kv: (-kv[1], kv[0])))
    return sorted(folders.values(), key=lambda f: (-f.duplicated.size, -f.duplicated.count, f.folder))


def _folders_text(folders: list[str], base: str = "", most: int = 2) -> str:
    """['logs/a/', 'logs/b/', 'logs/c/'] -> '3 folders under logs/'; ['a/', 'b/'] -> 'a/ and b/'."""
    names = [relative_key(f, base) or "(top level)" for f in folders]
    if len(names) <= most:
        return " and ".join(names)
    common = posixpath.commonprefix(folders)
    common = common[: common.rfind("/") + 1]
    if len(common) > len(base):
        return f"{len(names):,} folders under {relative_key(common, base)}"
    return ", ".join(names[:most]) + f" and {len(names) - most:,} more folders"


_UNREADABLE_REASONS = {
    "GLACIER": "in GLACIER, which needs a restore first", "DEEP_ARCHIVE": "in DEEP_ARCHIVE, which needs a restore first",
    "InvalidObjectState": "archived, which needs a restore first", "AccessDenied": "AccessDenied (needs s3:GetObject)",
    "PreconditionFailed": "changed while being read", "NoSuchKey": "deleted while being read",
}


def duplicate_findings(report: DuplicateReport) -> list[tuple[str, str]]:
    """Plain-language observations about duplicate files -> [(level, message)], level 'warn' or 'info'."""
    found: list[tuple[str, str]] = []
    base = base_prefix(parse_s3_uri(report.uri)[1]) if report.uri else ""

    def name(folder: str) -> str:
        return relative_key(folder, base) or "(top level)"

    if report.truncated:
        found.append(("warn", f"Listing stopped at the limit: only the first {report.scanned.count:,} files (in key "
                              "order) were compared, so copies of later files are missing. Pass limit=None to "
                              "check them all."))
    if report.groups:
        copies = report.copies
        big = report.monthly_cost >= 1 or report.reclaimable >= GB
        found.append(("warn" if big else "info",
                      f"{copies:,} redundant {'copy takes' if copies == 1 else 'copies take'} "
                      f"{human_size(report.reclaimable)}, costing {human_money(report.monthly_cost)}/month. Keep "
                      "one file per group and delete the others, after checking that no job or notebook reads "
                      "their paths. Keep suggests a readable file in the folder with the fewest copies, then the "
                      "oldest."))
    whole = {f.folder: f for f in duplicate_folders(report) if f.all_copies}
    reported: set[str] = set()
    for folder in list(whole.values())[:3]:
        others = [f for f in folder.elsewhere if f != folder.folder]
        size = human_size(folder.duplicated.size)
        level = "warn" if folder.duplicated.size >= GB else "info"
        mirror = others[0] if len(others) == 1 and others[0] in whole else None
        if mirror and [f for f in whole[mirror].elsewhere if f != mirror] == [folder.folder]:
            if mirror in reported:
                continue
            found.append((level, f"{name(folder.folder)} and {name(mirror)} hold the same "
                                 f"{_plural(folder.files, 'file')} ({size}). Keeping one of the two folders "
                                 "loses no data."))
        else:
            what = (f"its only file ({size}) is identical to a file" if folder.files == 1 else
                    f"all {folder.files:,} of its files ({size}) are identical to files")
            found.append((level, f"{name(folder.folder)} holds only copies: {what} in {_folders_text(others, base)}. "
                                 "Removing it, or no longer writing it, loses no data while those stay."))
        reported.add(folder.folder)
    if report.groups and report.versioning == "Enabled":
        found.append(("warn", "Versioning is on for this bucket: a deleted copy stays as a noncurrent version, still "
                              "billed, until a lifecycle rule with NoncurrentVersionExpiration removes it "
                              "(bucket_info shows the rules)."))
    missed = report.not_compared
    if missed.count and report.method == "etag":
        found.append(("info", f"{_plural(missed.count, 'file')} ({human_size(missed.size)}) share their size with a "
                              "file whose ETag differs, so their contents weren't compared. Copies uploaded in parts "
                              "of another size, or encrypted with SSE-KMS, look like this; method='hash' reads them "
                              "to check."))
    elif missed.count and report.read_capped:
        suggest = max(1, math.ceil((report.bytes_read + missed.size) / GB))
        found.append(("warn", f"Stopped reading at max_read={human_size(report.max_read)}: "
                              f"{_plural(missed.count, 'file')} ({human_size(missed.size)}) sharing a size with "
                              "another file weren't compared, so some copies may be missing. Pass "
                              f"max_read='{suggest}GB' to check them all."))
    if report.unreadable:
        reasons = Counter(report.unreadable.values())
        text = ", ".join(f"{n:,} {_UNREADABLE_REASONS.get(code, code)}" for code, n in reasons.most_common())
        found.append(("info", f"{_plural(len(report.unreadable), 'file')} could only be compared by ETag, because "
                              f"they couldn't be read: {text}."))
    by_etag = sum(g.matched_by == "ETag" for g in report.groups)
    if by_etag and report.method != "strict":
        found.append(("info", f"{_plural(by_etag, 'group')} matched on size + ETag without reading the files: the "
                              "ETag is S3's checksum of the content (the MD5, for a file uploaded in one part). "
                              "method='strict' reads every file to confirm with SHA-256."))
    return found


def zip_checks(plan: ZipPlan) -> list[tuple[str, bool | None, str]]:
    """Whether a zip can be made -> [(check, passed, details)]; passed is None for a note that doesn't block it.
    Checks: something to zip and not too many files, the size limit, disk space, memory and read access."""
    files, size = len(plan.files), plan.size
    rows: list[tuple[str, bool | None, str]] = []
    if not files:
        what = (f"all {_plural(plan.archived.count, 'file')} are in GLACIER / DEEP_ARCHIVE" if plan.archived.count
                else "no files under this prefix")
        return [("Files", False, what)]
    else:
        rows.append(("Files", not plan.more, f"more than {plan.max_files:,}: over the max_files limit" if plan.more
                     else f"{files:,} of the {plan.max_files:,} allowed (max_files=)"))
    at_least = "at least " if plan.more else ""
    rows.append(("Size", size <= plan.max_size,
                 f"{at_least}{human_size(size)} of the {human_size(plan.max_size)} allowed (max_size=)"))
    if plan.disk_free is None:
        rows.append(("Disk space", None, "couldn't check the free space"))
    else:
        rows.append(("Disk space", plan.space_needed <= plan.disk_free,
                     f"{at_least}{human_size(plan.space_needed)} needed, {human_size(plan.disk_free)} free in "
                     f"{os.path.dirname(plan.path) or '.'}"))
    held = min(size, 16 * _ZIP_SMALL_FILE)
    free = "" if plan.memory_free is None else f"; {human_size(plan.memory_free)} free"
    rows.append(("Memory", True if plan.memory_free is None or plan.memory_free > held else None,
                 f"files stream into the zip, at most about {human_size(held + MB)} at a time{free}"))
    if plan.probed is None:
        rows.append(("Read access", None, "nothing to read: the files are empty"))
    elif plan.read_error:
        rows.append(("Read access", False, f"{plan.read_error} reading {plan.probed}"))
    else:
        rows.append(("Read access", True, f"read the first byte of {plan.probed}"))
    if plan.archived.count:
        rows.append(("Archived files", None, f"{_plural(plan.archived.count, 'file')} "
                                             f"({human_size(plan.archived.size)}) left out: restore them first"))
    return rows


def _nice_size(size: int) -> str:
    """A max_size= value just above `size`: 3.3 MB -> '4MB', 340 MB -> '400MB', 3.2 GB -> '4GB'."""
    if size < 100 * MB:
        return f"{max(1, math.ceil(size / MB))}MB"
    if size < GB:
        return f"{math.ceil(size / (100 * MB)) * 100}MB"
    return f"{math.ceil(size / GB)}GB"


def zip_findings(plan: ZipPlan) -> list[tuple[str, str]]:
    """What stops a zip, or what's left out of it, and what to do -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    size = plan.size
    if not plan.files and not plan.archived.count:
        found.append(("warn", "Nothing to zip: there are no files under this prefix. Check the path (keys are "
                              "case-sensitive); ls(uri) shows what's there."))
    if plan.more:
        found.append(("warn", f"There are more than {plan.max_files:,} files here, so it stopped counting. Pass a "
                              f"bigger max_files= (and max_size=), or zip a sub-folder; tree(uri) shows their sizes."))
    elif size > plan.max_size:
        room = (f" (the disk has {human_size(plan.disk_free)} free)" if plan.disk_free is not None else "")
        found.append(("warn", f"The files take {human_size(size)}, over the {human_size(plan.max_size)} limit. Pass "
                              f"max_size='{_nice_size(size)}' to zip them anyway{room}, or zip a sub-folder; "
                              "tree(uri) shows their sizes."))
    if plan.disk_free is not None and plan.space_needed > plan.disk_free and plan.files:
        found.append(("warn", f"The disk doesn't have room: the zip needs up to {human_size(plan.space_needed)} and "
                              f"{human_size(plan.disk_free)} is free in {os.path.dirname(plan.path) or '.'}. Delete "
                              "files you don't need there, or pass path= on a disk with more room."))
    if plan.read_error:
        found.append(("warn", f"The notebook's role can't read these files ({plan.read_error} on {plan.probed}). It "
                              "needs s3:GetObject on them, and kms:Decrypt on the key when they're encrypted with "
                              "SSE-KMS."))
    if plan.archived.count:
        found.append(("warn" if not plan.files else "info",
                       f"{_plural(plan.archived.count, 'file')} ({human_size(plan.archived.size)}) in GLACIER / "
                       "DEEP_ARCHIVE can't go in the zip until they're restored (restore_object, or the S3 console)."))
    unsafe = [key for key, why in plan.left_out.items() if why not in ARCHIVE_CLASSES]
    if unsafe:
        found.append(("info", f"{_plural(len(unsafe), 'file')} left out because their names would unzip outside the "
                              "folder or clash with another file's (the table lists them)."))
    return found


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
        found.append(("warn" if billed - in_standard >= 1 else "info",
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
                                  f"until aborted: uploads('s3://{cfg.name}/') lists them."))
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
    doc = json.loads(policy) if isinstance(policy, str) else policy
    explained = []
    for i, stmt in enumerate(_as_list(doc.get("Statement", []))):
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


def _run_in_threads(work: Callable[[Any], Any], items: list[Any], workers: int, tick: Callable[[], None],
                    stop: threading.Event) -> list[tuple[Any, Any, Exception | None]]:
    """Run work(item) for every item on `workers` threads -> [(item, result, error)], in the order they finish.
    tick() runs about four times a second on the calling thread, so progress bars are only touched from it.
    An interrupt (the notebook's stop button) sets `stop` for the workers to see and cancels what hasn't started."""
    results: list[tuple[Any, Any, Exception | None]] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, item): item for item in items}
        pending = set(futures)
        try:
            while pending:
                finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    try:
                        results.append((futures[future], future.result(), None))
                    except Exception as exc:
                        results.append((futures[future], None, exc))
                tick()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            raise
    return results


def _pool_size(client: Any) -> int:
    """How many connections the client keeps open: more threads than that just wait for one."""
    return getattr(getattr(getattr(client, "meta", None), "config", None), "max_pool_connections", None) or 10


def _free_space(path: str) -> int:
    """Bytes free on the disk that holds `path` (or the nearest folder above it that exists)."""
    folder = os.path.abspath(path)
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    return shutil.disk_usage(folder).free


def _memory_available() -> int | None:
    """RAM the notebook can still use (MemAvailable on Linux), or None when it can't be read."""
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * KB
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def _zip_name(key: str, base: str, used: set[str]) -> str | None:
    """The name a file gets in the zip, relative to `base`; None if it would unzip outside the folder or clash."""
    name = relative_key(key, base)
    if name.startswith("/"):
        return None
    name = posixpath.normpath(name)
    if name in (".", "..") or name.startswith("../") or name in used:
        return None
    used.add(name)
    return name


def _check_disk_space(path: str, needed: int) -> None:
    folder = os.path.abspath(path)
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    free = _free_space(folder)
    if needed > free:
        raise ValueError(f"Not enough disk space: {human_size(needed)} to download, {human_size(free)} free in "
                         f"{folder}. Pass limit=, or a path on a bigger disk.")


class _ContentHasher:
    """Reads files for find_duplicates, in parallel: the SHA-256 of each file's first HASH_HEAD_BYTES, then of
    the rest where those match another file's. Stops planning reads at `budget` bytes. Progress is reported
    from the calling thread only, so a notebook progress bar is never touched from a worker thread."""

    def __init__(self, client: Any, budget: int | None, max_workers: int,
                 progress: Callable[[int, int], None] | None):
        self.client, self.budget, self.progress = client, budget, progress
        self.workers = max(1, min(max_workers, _pool_size(client)))
        self.hashes: dict[str, str] = {}  # uri -> SHA-256 of the whole file
        self.distinct: set[str] = set()  # uris whose first bytes differ from every other file of their size
        self.unreadable: dict[str, str] = {}  # key -> storage class or error code
        self.files_read = self.bytes_read = self.requests = self.planned = 0
        self._phase: tuple[int, int] = (0, 0)  # (bytes read before this phase, bytes this phase will read)
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
            self.unreadable.update((o.key, o.storage_class) for o in files if o.storage_class in ARCHIVE_CLASSES)
            if len(readable) > 1 and self._fits(sum(min(o.size, HASH_HEAD_BYTES) for o in readable)):
                heads.append(readable)
        started = self._read_all([(o, 0, hashlib.sha256()) for files in heads for o in files],
                                 sum(min(o.size, HASH_HEAD_BYTES) for files in heads for o in files))

        rest: list[list[ObjectInfo]] = []
        for files in heads:
            by_start: dict[str, list[ObjectInfo]] = defaultdict(list)
            for obj in files:
                if obj.uri in started:
                    by_start[started[obj.uri].hexdigest()].append(obj)
            for digest, same in by_start.items():
                if same[0].size <= HASH_HEAD_BYTES:  # the first bytes were the whole file
                    self.hashes.update((o.uri, digest) for o in same)
                elif len(same) == 1:
                    self.distinct.add(same[0].uri)
                else:
                    rest.append(same)
        rest.sort(key=lambda same: same[0].size * (len(same) - 1), reverse=True)
        todo = [(o, HASH_HEAD_BYTES, started[o.uri]) for same in rest
                if self._fits(sum(o.size - HASH_HEAD_BYTES for o in same)) for o in same]
        whole = self._read_all(todo, sum(o.size - start for o, start, _ in todo))
        self.hashes.update((uri, hasher.hexdigest()) for uri, hasher in whole.items())

    def _read_all(self, todo: list[tuple[ObjectInfo, int, Any]], size: int) -> dict[str, Any]:
        """Read every (object, from byte, hasher) in parallel -> {uri: hasher}; failures go to `unreadable`.
        Progress counts this phase's `size` bytes from 0."""
        done_hashers: dict[str, Any] = {}
        if not todo:
            return done_hashers
        self._phase = (self.bytes_read, size)
        self._report()
        for (obj, _, _), hasher, error in _run_in_threads(self._read, todo, self.workers, self._report, self._stop):
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
        match = {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}  # the file listed, not one written since
        body = self.client.get_object(Bucket=obj.bucket, Key=obj.key, Range=f"bytes={start}-{end}", **match)["Body"]
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


def _zip_time_of(moment: datetime) -> tuple[int, int, int, int, int, int]:
    """A file's time as a zip stores it (zip can't hold times before 1980)."""
    moment = moment.astimezone(timezone.utc) if moment.tzinfo else moment
    return max((moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second), (1980, 1, 1, 0, 0, 0))


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
            location = self.client.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
            region = "eu-west-1" if location == "EU" else location
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

    def find_duplicates(self, uri: str, *, min_size: int | str = 1, method: str = "hash",
                        max_read: int | str | None = "10GB", limit: int | None = None, max_workers: int = 16,
                        progress: Callable[[int], None] | None = None,
                        read_progress: Callable[[int, int], None] | None = None) -> DuplicateReport:
        """Identical files under `uri`, with what their copies take and cost.

        Files are grouped by size (a file no other file matches in size can't be a copy), then by ETag.
        method='hash' (the default) also reads files that share a size but not an ETag, because copies uploaded
        in parts of another size or encrypted with SSE-KMS get a different ETag: their first 64 KB first, and
        the whole file only where those match, hashed with SHA-256. method='strict' reads every file that shares
        its size, to prove each group byte for byte; 'etag' reads nothing. Reads stop at `max_read` bytes
        (None = no cap), biggest possible saving first. read_progress gets (bytes read, bytes to read) for
        each of the two passes, counting from 0 in each.
        """
        if method not in DUPLICATE_METHODS:
            raise ValueError(f"method must be one of {', '.join(map(repr, DUPLICATE_METHODS))}")
        budget = parse_size(max_read)
        bucket, prefix = parse_s3_uri(uri)
        started = time.monotonic()
        objects = list(self.iter_objects(uri, limit=None if limit is None else limit + 1, progress=progress))
        truncated = limit is not None and len(objects) > limit
        if truncated:
            del objects[limit:]
        reader = _ContentHasher(self.client, budget, max_workers, read_progress)
        reader.run(files_to_hash(objects, min_size=min_size, method=method))
        report = group_duplicates(objects, s3_uri(bucket, prefix), hashes=reader.hashes, distinct=reader.distinct,
                                  min_size=min_size, prices=self.prices)
        report.method, report.max_read, report.truncated = method, budget, truncated
        report.unreadable, report.read_capped = reader.unreadable, reader.capped
        report.files_read, report.bytes_read, report.requests = reader.files_read, reader.bytes_read, reader.requests
        if report.groups:
            try:
                report.versioning = self.versioning_status(bucket)
            except (ClientError, BotoCoreError):
                pass
        report.scan_seconds = time.monotonic() - started
        return report

    def compare(self, uri_a: str, uri_b: str, *, progress: Callable[[int], None] | None = None) -> CompareResult:
        """Diff two prefixes (e.g. a copy/sync source and target) by relative key, size and ETag."""
        bucket_a, prefix_a = parse_s3_uri(uri_a)
        bucket_b, prefix_b = parse_s3_uri(uri_b)
        seen = [0, 0]  # keys listed on each side, so the progress count keeps going up across both listings

        def side(i: int) -> Callable[[int], None] | None:
            def tick(count: int) -> None:
                seen[i] = count
                if progress:
                    progress(sum(seen))

            return tick if progress else None

        return compare_objects(self.iter_objects(uri_a, progress=side(0)), self.iter_objects(uri_b, progress=side(1)),
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

    def incomplete_uploads(self, uri: str, *, with_sizes: bool = False,
                           progress: Callable[[int], None] | None = None) -> list[MultipartUpload]:
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
                if progress and with_sizes:
                    progress(len(uploads))
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

    def open(self, uri: str, *, decompress: bool = True, compression: str | None = None) -> BinaryIO:
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

    def _open_document(self, bucket: str, key: str, codec: str | None, *, whole_under: int = 64 * MB) -> io.BufferedIOBase:
        """Seekable handle for a document: the whole object in memory when it's small (or compressed),
        ranged GETs for bigger ones."""
        if codec:
            return self._random_access(bucket, key, codec)
        size = self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        if size <= whole_under:
            return io.BytesIO(self.read_bytes(s3_uri(bucket, key)))
        return io.BufferedReader(_RangeReader(self.client, bucket, key, size), buffer_size=MB)

    def read_pdf(self, uri: str, *, pages: Iterable[int] | None = None, password: str | None = None,
                 compression: str | None = None) -> Document:
        """Text of a PDF, one part per page (needs pypdf). pages: 1-based page numbers, e.g. [1, 2] or
        range(1, 11); only those are read. Scanned pages have no text layer and come back empty."""
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(bucket, key, codec, whole_under=256 * MB if pages is None else 16 * MB) as handle:
            return parse_pdf(handle, s3_uri(bucket, key), pages=pages, password=password)

    def read_docx(self, uri: str, *, compression: str | None = None) -> Document:
        """Text of a Word .docx: paragraphs and tables in order, headings, title and author. No packages needed."""
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(bucket, key, codec) as handle:
            return parse_docx(handle, s3_uri(bucket, key))

    def read_pptx(self, uri: str, *, slides: Iterable[int] | None = None, compression: str | None = None) -> Document:
        """Text of a PowerPoint .pptx, one part per slide, with slide titles, tables and speaker notes.
        slides: 1-based slide numbers to read. No packages needed."""
        bucket, key = parse_s3_uri(uri)
        codec = detect_format(key)[1] if compression is None else compression
        with self._open_document(bucket, key, codec) as handle:
            return parse_pptx(handle, s3_uri(bucket, key), slides=slides)

    def read_document(self, uri: str, *, pages: Iterable[int] | None = None, password: str | None = None,
                      compression: str | None = None) -> Document:
        """Text of a PDF, Word .docx or PowerPoint .pptx, told apart by name or content. pages: page numbers
        (PDF) or slide numbers (PPTX). doc.text is everything; doc.parts has one entry per page / slide."""
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
            return self.read_pdf(uri, pages=pages, password=password, compression=codec or "")
        if fmt == "pptx":
            return self.read_pptx(uri, slides=pages, compression=codec or "")
        if fmt == "docx":
            if pages is not None:
                raise ValueError("A .docx has no fixed pages; read it whole and use doc.parts")
            return self.read_docx(uri, compression=codec or "")
        if fmt == "oldoffice":
            raise ValueError(_old_office_note(key))
        raise ValueError(f"{key!r} isn't a PDF, Word .docx or PowerPoint .pptx file")

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
        fmt = p.format or "orc"  # preview() sets it before picking this handler
        with self._random_access(bucket, key, codec) as handle:
            p.info = _columnar_info(fmt, handle)
            handle.seek(0)
            p.kind, p.data = "table", _read_arrow_table(fmt, handle, n, None).to_pandas()
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
        names = {e.name for e in listing.entries}
        kind = next((kind for part, kind in _OFFICE_PARTS.items() if part in names), None)
        if kind and p.format in ("zip", None):  # a Word / PowerPoint / Excel file without its extension
            p.format = kind
            return getattr(self, f"_preview_{kind}")(p, uri, n, codec)
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
        mime = p.content_type if (p.content_type or "").startswith(f"{p.format}/") else None
        p.info = {"media": p.format, "mime": mime or mimetypes.guess_type(uri)[0] or f"{p.format}/*"}
        return True

    _preview_video = _preview_audio

    def _preview_pdf(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        url = self.presigned_url(uri)
        if importlib.util.find_spec("pypdf") is None:
            p.kind, p.data, p.info = "media", url, {"media": "pdf"}
            p.note = "Install pypdf (pip install pypdf) to see the page count and text here."
            return True
        bucket, key = parse_s3_uri(uri)
        try:
            with self._open_document(bucket, key, codec, whole_under=16 * MB) as handle:
                doc = parse_pdf(handle, p.uri, pages=[1])
        except _READ_ERRORS as exc:
            p.kind, p.data, p.info = "media", url, {"media": "pdf"}
            p.note = f"Couldn't read the PDF's text ({exc}); the link may still open it."
            return True
        p.kind, p.data = "document", doc.parts[0]
        p.info = {"pages": doc.page_count, "title": doc.title, "author": doc.author, "url": url,
                  "excerpt": "Page 1" + (f" of {doc.page_count}" if (doc.page_count or 0) > 1 else "")}
        if not doc.parts[0].strip():
            p.note = "Page 1 has no text layer (probably a scanned image); reading it needs OCR."
        return True

    def _preview_docx(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._open_document(bucket, key, codec) as handle:
            doc = parse_docx(handle, p.uri)
        shown = doc.parts[:n]
        p.kind, p.data, p.truncated = "document", "\n\n".join(shown), len(doc.parts) > n
        p.info = {"words": doc.word_count, "paragraphs": len(doc.parts) - len(doc.tables),
                  "headings": len(doc.headings) or None, "tables": len(doc.tables) or None,
                  "title": doc.title, "author": doc.author,
                  "excerpt": f"First {len(shown)} paragraphs" if p.truncated else "Text",
                  "outline": [{"level": level, "heading": text} for level, text in doc.headings[:50]],
                  "table": doc.tables[0] if doc.tables else None}
        if not doc.parts:
            p.note = "The document has no text."
        return True

    def _preview_pptx(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._open_document(bucket, key, codec) as handle:
            doc = parse_pptx(handle, p.uri)
        rows = []
        for number, title, text, notes in list(zip(doc.numbers, doc.slide_titles, doc.parts, doc.notes))[: max(n, 50)]:
            body = text.split("\n", 1)[1] if title and text.startswith(title) and "\n" in text else text
            body = " · ".join(" ".join(line.split()) for line in body.splitlines() if line.strip())
            rows.append({"slide": number, "title": title or "", "text": body[:120] + ("…" if len(body) > 120 else ""),
                         "words": _count_words(text), "notes": "yes" if notes else ""})
        p.kind, p.data = "listing", rows
        p.info = {"slides": doc.page_count, "words": doc.word_count, "tables": len(doc.tables) or None,
                  "title": doc.title, "author": doc.author}
        return True

    def _preview_oldoffice(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = _old_office_note(uri)
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

    def download(self, uri: str, path: str | None = None, *,
                 progress: Callable[[int, int], None] | None = None) -> str:
        """Download one file to `path` (a file or directory; default: current directory). Returns the local path.
        Refuses when the disk hasn't room. progress gets (bytes downloaded, file size)."""
        bucket, key = parse_s3_uri(uri)
        path = path or os.path.basename(key)
        if os.path.isdir(path):
            path = os.path.join(path, os.path.basename(key))
        head = self.client.head_object(Bucket=bucket, Key=key)
        _check_disk_space(path, head["ContentLength"])
        failed = self._fetch([(bucket, key, path, head["ContentLength"], head.get("LastModified"))], progress)
        if failed:
            raise failed[key]
        return os.path.abspath(path)

    def download_folder(self, uri: str, path: str | None = None, *, limit: int | None = None, max_workers: int = 8,
                        progress: Callable[[int, int], None] | None = None,
                        list_progress: Callable[[int], None] | None = None) -> FolderDownload:
        """Download every file under a folder into `path` (default: a folder of the same name in the current
        directory), keeping the sub-folders. A file already there with the same size and time is skipped, so
        running it again resumes. GLACIER / DEEP_ARCHIVE files are skipped (they need a restore first). Refuses
        when the disk hasn't room. progress gets (bytes downloaded, bytes to download)."""
        bucket, prefix = parse_s3_uri(uri)
        if prefix and not prefix.endswith("/"):
            prefix += "/"  # 's3://b/data' means the folder data/, not also data-old/
        root = os.path.abspath(path or prefix.rstrip("/").rsplit("/", 1)[-1] or bucket)
        result = FolderDownload(uri=s3_uri(bucket, prefix), path=root)
        started = time.monotonic()
        jobs = []
        listing = self.iter_objects(result.uri, limit=None if limit is None else limit + 1, progress=list_progress)
        for i, obj in enumerate(listing):
            if limit is not None and i >= limit:
                result.truncated = True
                break
            local = os.path.normpath(os.path.join(root, relative_key(obj.key, prefix)))
            if obj.is_folder_marker or obj.key.endswith("/"):
                continue
            if not local.startswith(root + os.sep):
                result.skipped[obj.key] = "its name leads outside the folder"
            elif obj.storage_class in ARCHIVE_CLASSES:
                result.skipped[obj.key] = obj.storage_class
            elif (os.path.isfile(local) and os.path.getsize(local) == obj.size
                  and int(os.path.getmtime(local)) == int(obj.last_modified.timestamp())):
                result.already_there.add(obj.size)
            else:
                jobs.append((bucket, obj.key, local, obj.size, obj.last_modified))
        _check_disk_space(root, sum(job[3] for job in jobs))
        failed = self._fetch(jobs, progress, max_workers)
        for _, key, _, size, _ in jobs:
            if key in failed:
                error = failed[key]
                result.skipped[key] = _error_code(error) if isinstance(error, ClientError) else str(error)
            else:
                result.downloaded.add(size)
        result.seconds = time.monotonic() - started
        return result

    def _fetch(self, jobs: list[tuple[str, str, str, int, datetime | None]],
               progress: Callable[[int, int], None] | None, max_workers: int = 1) -> dict[str, Exception]:
        """Download (bucket, key, local path, size, last modified) jobs in parallel, stamping each file with
        the object's time. Returns {key: error} for the ones that failed (AWS errors, a full disk)."""
        total, done, lock, stop = sum(job[3] for job in jobs), [0], threading.Lock(), threading.Event()

        def add(count: int) -> None:  # boto3 calls this from its own threads as bytes arrive
            if stop.is_set():
                raise RuntimeError("Download stopped")
            with lock:
                done[0] += count

        def one(job: tuple[str, str, str, int, datetime | None]) -> None:
            bucket, key, local, _, modified = job
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            self.client.download_file(bucket, key, local, Callback=add)
            if modified is not None:
                os.utime(local, (modified.timestamp(), modified.timestamp()))

        def report() -> None:
            if progress:
                progress(done[0], total)

        report()
        failed: dict[str, Exception] = {}
        for job, _, error in _run_in_threads(one, jobs, min(max_workers, _pool_size(self.client)), report, stop):
            if isinstance(error, (ClientError, BotoCoreError, OSError)):
                failed[job[1]] = error
            elif error is not None:
                raise error
        return failed

    def plan_zip(self, uri: str, path: str | None = None, *, max_size: int | str = "100MB", max_files: int = 10_000,
                 progress: Callable[[int], None] | None = None) -> ZipPlan:
        """Check whether a file or folder can be zipped here, without downloading it: what would go in, the size
        and file-count limits, free disk space and memory, and whether the files can be read (one 1-byte read).
        `path` is the .zip to write (default: named after the folder, in the current directory)."""
        bucket, key = parse_s3_uri(uri)
        limit = parse_size(max_size)
        if limit is None:
            raise ValueError("max_size can't be None; pass a size such as '2GB'")
        first = self.client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1).get("Contents", [])
        single = bool(key) and not key.endswith("/") and bool(first) and first[0]["Key"] == key
        if single:  # a file (it lists first among the keys it prefixes)
            base, name = base_prefix(key), key.rsplit("/", 1)[-1]
        else:
            base = key if not key or key.endswith("/") else key + "/"  # 's3://b/data' means the folder data/
            name = base.rstrip("/").rsplit("/", 1)[-1] or bucket
        if path is None:
            path = f"{name}.zip"
        elif os.path.isdir(path):
            path = os.path.join(path, f"{name}.zip")
        elif not path.lower().endswith(".zip"):
            path += ".zip"
        plan = ZipPlan(uri=s3_uri(bucket, key if single else base), path=os.path.abspath(path), max_size=limit,
                       max_files=max_files)
        if single:
            item = first[0]
            listing: Iterable[ObjectInfo] = [ObjectInfo(bucket, key, item["Size"], item["LastModified"],
                                                        item.get("StorageClass", "STANDARD"),
                                                        item.get("ETag", "").strip('"'))]
        else:
            listing = self.iter_objects(plan.uri, progress=progress)
        used: set[str] = set()
        for obj in listing:
            if obj.is_folder_marker or obj.key.endswith("/"):
                continue
            if len(plan.files) + len(plan.left_out) >= max_files:
                plan.more = True
                break
            name_in_zip = _zip_name(obj.key, base, used)
            if obj.storage_class in ARCHIVE_CLASSES:
                plan.archived.add(obj.size)
                plan.left_out[obj.key] = obj.storage_class
            elif name_in_zip is None:
                plan.left_out[obj.key] = "its name would unzip outside the folder, or clash with another file's"
            else:
                plan.files.append((obj, name_in_zip))
        try:
            plan.disk_free = _free_space(os.path.dirname(plan.path))
        except OSError:
            pass
        plan.memory_free = _memory_available()
        probe = next((obj for obj, _ in plan.files if obj.size), None)
        if probe is not None:
            plan.probed = probe.key
            try:
                self.client.get_object(Bucket=bucket, Key=probe.key, Range="bytes=0-0")["Body"].close()
            except ClientError as exc:
                plan.read_error = _error_code(exc)
        return plan

    def download_zip(self, uri: str, path: str | None = None, *, max_size: int | str = "100MB",
                     max_files: int = 10_000, dry_run: bool = False, max_workers: int = 8,
                     progress: Callable[[int, int], None] | None = None,
                     list_progress: Callable[[int], None] | None = None) -> ZipDownload:
        """Zip a file or a folder (with its sub-folders) into one .zip on the notebook's disk, after plan_zip's
        checks pass: at most max_size of files (100 MB by default) and max_files files, room on the disk, and read
        access. Nothing is written when a check fails, or with dry_run=True. Already-compressed files (parquet,
        gz, images, ...) are stored as they are, the rest compressed. progress gets (bytes zipped, bytes to zip)."""
        plan = self.plan_zip(uri, path, max_size=max_size, max_files=max_files, progress=list_progress)
        result = ZipDownload(plan)
        if dry_run or not plan.can_download:
            return result
        started, done = time.monotonic(), [0]

        def report() -> None:
            if progress:
                progress(done[0], plan.size)

        window = 16 if plan.memory_free is None or plan.memory_free > 2 * 16 * _ZIP_SMALL_FILE else 2
        part = plan.path + ".part"  # renamed once complete, so a stopped zip never looks finished
        try:
            with zipfile.ZipFile(part, "w", allowZip64=True) as archive, \
                    ThreadPoolExecutor(max_workers=max(1, min(max_workers, _pool_size(self.client)))) as pool:
                ahead: deque[tuple[ObjectInfo, str, Any]] = deque()
                pending = iter(plan.files)

                def fetch_ahead() -> None:
                    while len(ahead) < window:
                        item = next(pending, None)
                        if item is None:
                            return
                        obj, name = item
                        small = obj.size <= _ZIP_SMALL_FILE
                        ahead.append((obj, name, pool.submit(self._read_object, obj) if small else None))

                fetch_ahead()
                report()
                try:
                    while ahead:
                        obj, name, future = ahead.popleft()
                        fetch_ahead()
                        info = zipfile.ZipInfo(name, date_time=_zip_time_of(obj.last_modified))
                        info.compress_type = (zipfile.ZIP_STORED if file_extension(name).rsplit(".", 1)[-1]
                                              in _ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED)
                        info.file_size = obj.size  # lets zipfile pick ZIP64 for files over 2 GB
                        info.external_attr = 0o644 << 16  # unzipped files: readable, writable by you
                        try:
                            if future is not None:
                                data = future.result()
                                archive.writestr(info, data)
                                done[0] += len(data)
                            else:
                                self._stream_into(archive, info, obj, done, report)
                        except (ClientError, BotoCoreError) as exc:
                            result.failed[obj.key] = (_error_code(exc) if isinstance(exc, ClientError)
                                                      else type(exc).__name__)
                            continue
                        result.files.add(obj.size)
                        report()
                except BaseException:
                    for _, _, future in ahead:
                        if future is not None:
                            future.cancel()
                    raise
            os.replace(part, plan.path)
        except BaseException:
            if os.path.exists(part):
                os.remove(part)
            raise
        result.written, result.zip_size = True, os.path.getsize(plan.path)
        result.seconds = time.monotonic() - started
        return result

    def _read_object(self, obj: ObjectInfo) -> bytes:
        match = {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}  # the file listed, not one written since
        return self.client.get_object(Bucket=obj.bucket, Key=obj.key, **match)["Body"].read()

    def _stream_into(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, obj: ObjectInfo, done: list[int],
                     report: Callable[[], None]) -> None:
        """Copy a big object into the zip 1 MB at a time, so it never sits in memory whole."""
        match = {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}
        body = self.client.get_object(Bucket=obj.bucket, Key=obj.key, **match)["Body"]
        try:
            with archive.open(info, "w") as entry:
                while chunk := body.read(MB):
                    entry.write(chunk)
                    done[0] += len(chunk)
                    report()
        finally:
            body.close()


# =============================================================================
# 5. S3View - notebook UI layer (renders what S3Analyzer returns)
# =============================================================================


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[tuple[str, ...]]  # (label, value), or (label, value, tone) with tone 'warn' | 'bad' | 'ok'


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]  # cells are text, or _Tone for a coloured status
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap
    code_cols: tuple[int, ...] = ()  # columns holding calls to copy, shown as code
    prose_cols: tuple[int, ...] = ()  # columns of sentences this tool wrote (findings): calls in them shown as code
    collapsed: bool = False  # a secondary view: folded under its title in HTML


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""
    wrap: bool = False  # prose: wrap long lines instead of scrolling sideways
    code: bool = False  # a snippet to copy: in HTML one click selects all of it
    collapsed: bool = False  # a secondary view (raw JSON): folded under its title in HTML


@dataclass
class _Findings:
    items: list[tuple[str, str]]  # (level, message) pairs from a *_findings function
    empty: str = ""  # said (as an ok note) when there are none; nothing when blank


@dataclass
class _Next:
    items: list[tuple[str, str]]  # (call, what it shows): the commands worth running next, arguments filled in
    title: str = "Next"


@dataclass
class _Tone:
    """A table cell with a status colour: a pill in HTML, plain text elsewhere."""
    text: str
    tone: str = "warn"  # 'warn' | 'bad' | 'ok'

    def __str__(self) -> str:
        return self.text


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
.s3a h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.s3a h4{margin:14px 0 4px;font-size:13px}
.s3a .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.s3a .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.s3a .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.s3a .card.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.s3a .card.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.s3a .card.ok{border-color:rgba(16,185,129,.7)}
.s3a .card .l{font-size:11px;opacity:.65}
.s3a .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.s3a .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.s3a .tw.scroll{max-height:640px;overflow:auto}
.s3a table.t{border-collapse:collapse;width:auto;font-size:inherit}
.s3a table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.s3a .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.s3a .tw.scroll table.t th{background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.s3a table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.s3a table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.s3a table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.s3a table.t td.s{white-space:nowrap}
.s3a table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.s3a table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.s3a table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.s3a .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.s3a .track{vertical-align:middle;margin-right:6px}
.s3a .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.s3a .pill{display:inline-block;padding:0 7px;border-radius:9px;font-weight:600;font-size:12px}
.s3a .pill.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.6)}
.s3a .pill.bad{background:rgba(239,68,68,.16);box-shadow:inset 0 0 0 1px rgba(239,68,68,.6)}
.s3a .pill.ok{background:rgba(16,185,129,.14);box-shadow:inset 0 0 0 1px rgba(16,185,129,.55)}
.s3a .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.s3a .note::before{content:"\\2139\\FE0E";margin-right:7px;opacity:.7}
.s3a .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.s3a .note.warn::before{content:"\\26A0\\FE0E"}
.s3a .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.s3a .note.ok::before{content:"\\2713"}
.s3a .fh{font-size:12px;font-weight:600;opacity:.75;margin:10px 0 2px}
.s3a code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:0 4px;border-radius:4px}
.s3a code{background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
.s3a .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.s3a pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.s3a pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.5;max-height:560px}
.s3a pre.code{user-select:all;-webkit-user-select:all;cursor:text}
.s3a .hint{font-weight:400;font-size:11px;opacity:.55;margin-left:8px}
.s3a details.sec{margin:14px 0 4px}
.s3a details.sec>summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.s3a .next{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:12px 0 4px;padding-top:8px}
.s3a .next{border-top:1px dashed rgba(127,127,127,.35)}
.s3a .next .nl{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.s3a .next .nw{font-size:12px;opacity:.65;margin-left:6px}
.s3a img{max-width:100%;max-height:480px;border:1px solid rgba(127,127,127,.3)}
</style>"""

_BADGE = "S3"  # the chip before each report's title, so reports from different analyzers are easy to tell apart
_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")
# A command in a sentence: a call (kb_info(), documents(status='FAILED'), .core.find(...), S3View().preview('s3://..'))
# or an AWS CLI command with its options (aws dynamodb update-table --table-name orders --deletion-protection-enabled).
_CALL_RE = re.compile(r"((?<![\w.])\.?(?:[A-Za-z_]\w*(?:\(\))?\.)*[A-Za-z_]\w*"
                      r"\((?:[^()'\"]|'[^']*'|\"[^\"]*\"|\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))*\)"
                      r"|\baws [a-z0-9-]+ [a-z0-9-]+(?: --[\w-]+(?: (?!--)[^\s,;]*[^\s,;.])?)*)")
_TONES = ("warn", "bad", "ok")
_MARKS = {"warn": "[!] ", "ok": "[ok] "}  # text-mode prefix of a note by level; anything else is "[i] "
_SELECT = ' title="Click to select, then copy"'


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _prose(value: Any) -> str:
    """Escaped HTML for a sentence this tool wrote, with the calls in it as code that one click selects.
    The text is split on the calls and every piece escaped before it's wrapped, so nothing in it becomes markup."""
    pieces = _CALL_RE.split("" if value is None else str(value))
    return "".join(f"<code{_SELECT}>{_esc(piece)}</code>" if i % 2 else _esc(piece) for i, piece in enumerate(pieces))


def _call(name: str, *args: Any, **kwargs: Any) -> str:
    """_call('tree', 's3://b/', depth=2) -> "tree('s3://b/', depth=2)": a next step, ready to copy."""
    def literal(value: Any) -> str:  # repr, but DynamoDB numbers read 42 rather than Decimal('42')
        if type(value).__name__ == "Decimal":
            return str(value)
        if isinstance(value, dict):
            return "{" + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items()) + "}"
        if isinstance(value, (list, tuple)):
            inner = ", ".join(map(literal, value)) + ("," if isinstance(value, tuple) and len(value) == 1 else "")
            return f"[{inner}]" if isinstance(value, list) else f"({inner})"
        return repr(value)

    return f"{name}({', '.join([literal(a) for a in args] + [f'{k}={literal(v)}' for k, v in kwargs.items()])})"


def _signature(function: Callable) -> str:
    """'(uri, *, top_n=10, limit=None)': a command's parameters without self or type hints."""
    sig = inspect.signature(function)
    params = [p.replace(annotation=inspect.Parameter.empty) for name, p in sig.parameters.items() if name != "self"]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


def _tone(item: tuple[str, ...]) -> str:
    """The tone of a card: its third element, when it's one this renderer colours."""
    return item[2] if len(item) > 2 and item[2] in _TONES else ""


def _ordered(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Warnings first, then notes, each in the order they were found."""
    return sorted(findings, key=lambda f: {"warn": 0, "info": 1}.get(f[0], 2))


def _counts(findings: list[tuple[str, str]], sep: str) -> str:
    """'2 warnings · 3 notes'."""
    warns = sum(level == "warn" for level, _ in findings)
    return sep.join(filter(None, [_plural(warns, "warning") if warns else "",
                                  _plural(len(findings) - warns, "note") if len(findings) > warns else ""]))


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _hidden(count: int, table: _Table, default_max: int) -> str:
    """The line under a table that was cut short, and how to see the rest when the view's max_rows cut it."""
    text = f"... {count:,} more rows not shown"
    return text + (f" (the view shows {default_max:,}; ui.max_rows = 0 shows all)" if table.max_rows is None else "")


def _render_html(blocks: list[Any], max_rows: int) -> str:
    out = [_CSS, '<div class="s3a">']
    for block in blocks:
        if isinstance(block, _Title):
            out.append(f'<h3><span class="badge">{_esc(_BADGE)}</span>{_esc(block.text)}</h3>')
            if block.sub:
                out.append(f'<div class="sub">{_prose(block.sub)}</div>')
        elif isinstance(block, _Cards):
            cards = "".join(f'<div class="{" ".join(filter(None, ["card", _tone(item)]))}"><div class="l">'
                            f'{_esc(item[0])}</div><div class="v">{_esc(item[1])}</div></div>' for item in block.items)
            out.append(f'<div class="cards">{cards}</div>')
        elif isinstance(block, _Note):
            out.append(f'<div class="note {block.level}">{_prose(block.text)}</div>')
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                notes = "".join(f'<div class="note {level}">{_prose(message)}</div>' for level, message in items)
                head = f'<div class="fh">Findings · {_esc(_counts(items, " · "))}</div>'
                out.append(f'<div class="fd">{head}{notes}</div>')
            elif block.empty:
                out.append(f'<div class="note ok">{_prose(block.empty)}</div>')
        elif isinstance(block, _Next):
            if block.items:
                items = "".join(f'<span class="ni"><code{_SELECT}>{_esc(call)}</code>'
                                + (f'<span class="nw">{_esc(why)}</span>' if why else "") + "</span>"
                                for call, why in block.items)
                out.append(f'<div class="next"><span class="nl">{_esc(block.title)}</span>{items}</div>')
        elif isinstance(block, _Table):
            if not block.rows:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
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
                    inner = _esc(text)
                    if isinstance(cell, _Tone) and cell.tone in _TONES and text:
                        inner = f'<span class="pill {cell.tone}">{inner}</span>'
                    if block.tree and j == 0:
                        css = "tree"
                    elif j in block.code_cols and text:
                        css, inner = "c", f"<code{_SELECT}>{inner}</code>"
                    elif j in block.prose_cols:
                        css, inner = "", _prose(text)
                    elif _NUMERIC_RE.match(text):
                        css = "n"
                    else:
                        css = "s" if len(text) <= 16 and "\n" not in text else ""
                    cells.append(f'<td class="{css}">{inner}</td>' if css else f"<td>{inner}</td>")
                if block.bars is not None:
                    pct = max(0.0, min(1.0, block.bars[i])) * 100
                    cells.append(f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                                 f"</span></span>{pct:.1f}%</td>")
                body.append(f"<tr>{''.join(cells)}</tr>")
            table = (f'<div class="tw{" scroll" if len(rows) > 30 else ""}"><table class="t"><thead><tr>{head}</tr>'
                     f'</thead><tbody>{"".join(body)}</tbody></table></div>')
            if hidden:
                table += f'<div class="more">{_esc(_hidden(hidden, block, max_rows))}</div>'
            if block.collapsed:
                out.append(f'<details class="sec"><summary>{_prose(block.title or "Details")} '
                           f"({len(block.rows):,})</summary>{table}</details>")
            else:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
                out.append(table)
        elif isinstance(block, _Text):
            css = " ".join(filter(None, ["wrap" if block.wrap else "", "code" if block.code else ""]))
            pre = (f'<pre class="{css}"{_SELECT if block.code else ""}>{_esc(block.text)}</pre>' if css
                   else f"<pre>{_esc(block.text)}</pre>")
            if block.collapsed:
                out.append(f'<details class="sec"><summary>{_prose(block.title or "Details")}</summary>{pre}</details>')
            else:
                hint = '<span class="hint">click it to select all, then copy</span>' if block.code else ""
                if block.title or hint:
                    out.append(f"<h4>{_prose(block.title)}{hint}</h4>")
                out.append(pre)
        elif isinstance(block, _Frame):
            if block.title:
                out.append(f"<h4>{_prose(block.title)}</h4>")
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
            for entry in block.items:
                item = f"{entry[0]}: {entry[1]}" + (" (!)" if _tone(entry) in ("warn", "bad") else "")
                if line and len(line) + len(item) > 100:
                    out.append(line)
                    line = ""
                line += ("   " if line else "") + item
            out.append(line)
        elif isinstance(block, _Note):
            out.append(_MARKS.get(block.level, "[i] ") + block.text)
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                out += ["", f"-- Findings: {_counts(items, ', ')} --"]
                out += [_MARKS.get(level, "[i] ") + message for level, message in items]
            elif block.empty:
                out.append("[ok] " + block.empty)
        elif isinstance(block, _Next):
            if block.items:
                width = max(len(call) for call, _ in block.items)
                out += ["", f"{block.title}:"]
                out += [f"  {call.ljust(width)}   {why}".rstrip() for call, why in block.items]
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
                out.append(_hidden(hidden, block, max_rows))
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
        from IPython.core.getipython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and type(shell).__name__ != "TerminalInteractiveShell"


def _progress_bar_class(notebook: bool) -> Any:
    """tqdm's widget bar in a notebook (it needs ipywidgets) or its text bar elsewhere; None without tqdm."""
    try:
        if notebook:
            importlib.import_module("ipywidgets")
            return importlib.import_module("tqdm.notebook").tqdm
        return importlib.import_module("tqdm").tqdm
    except Exception:  # not installed, or too old to import cleanly: the plain progress line takes over
        return None


def _progress_bar(bar_class: Any, label: str, unit: str, total: int | None) -> Any:
    """A tqdm bar that shows up after half a second and disappears when closed. unit='B' counts bytes."""
    options: dict[str, Any] = {"desc": label, "total": total, "leave": False, "delay": 0.5, "mininterval": 0.25,
                               "dynamic_ncols": True, "disable": False, "unit_scale": True}
    if unit == "B":
        options.update(unit="B", unit_divisor=1024)
    else:
        known = total is not None
        counts = "{percentage:3.0f}%|{bar}| {n:,}/{total:,}" if known else "{n:,}"
        timing = "{elapsed}<{remaining}, {rate_fmt}" if known else "{elapsed}, {rate_fmt}"
        options.update(unit=f" {unit}", bar_format=f"{{desc}}: {counts} {unit} [{timing}]")
    return bar_class(**options)


def _duration(seconds: float) -> str:
    """0.42 -> '0.4s', 42.4 -> '42s', 125 -> '2m 05s', 7500 -> '2h 05m'."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m"


def _progress_text(label: str, unit: str, count: int, total: int | None, elapsed: float) -> str:
    """The progress line shown without tqdm: 'Reading... 1.2 GB of 3.0 GB (40%) · 12s · 98.0 MB/s · about 18s left'."""
    amount = human_size if unit == "B" else (lambda n: f"{n:,}")
    text = f"{label}... {amount(count)}"
    if total:
        text += f" of {amount(total)}"
    text += "" if unit == "B" else f" {unit}"
    if total:
        text += f" ({min(count / total, 1):.0%})"
    text += f" · {_duration(elapsed)}"
    if elapsed >= 1 and count:
        rate = count / elapsed
        text += f" · {human_size(rate)}/s" if unit == "B" else f" · {rate:,.0f}/s" if rate >= 10 else f" · {rate:.1f}/s"
        if total and total > count:
            text += f" · about {_duration((total - count) / rate)} left"
    return text


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


def _file_steps(bucket: str, objects: list[ObjectInfo]) -> list[tuple[str, str]]:
    """Next steps after a list of files: look at the first one."""
    if not objects:
        return []
    uri = s3_uri(bucket, objects[0].key)
    return [(_call("preview", uri), "what's inside the first one"), (_call("head", uri), "all its metadata")]


def _folder_label(folder: str) -> str:
    return folder or "(files at this level)"


_FORMAT_LABELS = {"arrow": "feather / arrow", "excel": "excel", "torch": "PyTorch checkpoint", "pdf": "PDF",
                  "docx": "Word document", "pptx": "PowerPoint deck", "oldoffice": "Office 97-2003 file",
                  "notebook": "Jupyter notebook", "npy": "NumPy array", "npz": "NumPy arrays (npz)"}
_INFO_CARDS = {  # Preview.info key -> card label, in display order
    "rows": "Rows", "records": "Records", "columns": "Columns", "row_groups": "Row groups", "stripes": "Stripes",
    "batches": "Record batches", "sheets": "Sheets", "codec": "Codec", "compression": "Compression",
    "files": "Files", "unpacked_size": "Unpacked size", "arrays": "Arrays", "shape": "Shape", "dtype": "Dtype",
    "tensors": "Tensors", "parameters": "Parameters", "dtypes": "Dtypes", "kernel": "Kernel",
    "language": "Language", "cells": "Cells", "pages": "Pages", "slides": "Slides", "words": "Words",
    "paragraphs": "Paragraphs", "headings": "Headings", "tables": "Tables", "title": "Title", "author": "Author",
}
_LISTING_TITLES = {"zip": "Files", "tar": "Files", "torch": "Files in the checkpoint", "npz": "Arrays", "pptx": "Slides",
                   "safetensors": "Tensors", "notebook": "Cells"}


_PANDAS_READERS = {  # format -> how pandas opens a downloaded file of it
    "csv": "read_csv({path})", "tsv": "read_csv({path}, sep='\\t')", "psv": "read_csv({path}, sep='|')",
    "parquet": "read_parquet({path})", "orc": "read_orc({path})", "arrow": "read_feather({path})",
    "jsonl": "read_json({path}, lines=True)", "json": "read_json({path})", "excel": "read_excel({path})",
}


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


def _document_table(rows: list[list[str]], title: str) -> _Table:
    """A table from a Word / PowerPoint file: first row as the header, ragged rows padded."""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    headers = rows[0] if len(rows) > 1 else [""] * width
    return _Table(headers, rows[1:] if len(rows) > 1 else rows, title=title)


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
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _GROUPS = {  # help() lists the commands in these groups, in this order
        "Buckets": ("buckets", "overview", "bucket_info", "policy"),
        "Explore a folder": ("ls", "tree", "summary", "find", "largest", "newest", "oldest", "compare"),
        "Cut cost": ("duplicates", "what_if", "uploads"),
        "Versions and deleted files": ("versions", "history", "deleted"),
        "Open a file": ("head", "preview", "document", "download", "download_zip", "link"),
        "Help": ("help",),
    }
    _START = (("overview()", "every bucket: size, cost and warnings"),
              ("summary('s3://bucket/prefix/')", "what's in a folder and what it costs"))

    def __init__(self, core: S3Analyzer | None = None, *, mode: str = "auto", max_rows: int = 50,
                 progress: str = "auto"):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.core = core or S3Analyzer()
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.progress = progress

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            from IPython.display import HTML, display

            display(HTML(_render_html(blocks, self.max_rows)))
        else:
            print(_render_text(blocks, self.max_rows))

    @contextmanager
    def _progress(self, label: str = "Scanning", unit: str = "objects") -> Iterator[Callable[..., None]]:
        """Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away."""
        bar_class = [_progress_bar_class(self.use_html and _in_notebook()) if self.progress == "auto" else None]
        bar: list[Any] = [None]
        handle: list[Any] = [None]
        started: list[Any] = [time.monotonic(), None]  # when the current total started, and that total
        shown, width, stopped = [0.0], [0], [False]

        def close_bar() -> None:
            if bar[0] is not None:
                if not stopped[0] and bar[0].total and bar[0].n < bar[0].total:
                    bar[0].total = bar[0].n  # done early (a file it couldn't read): no red "failed" widget
                bar[0].close()
                bar[0] = None

        def clear() -> None:
            close_bar()
            if handle[0] is not None:
                from IPython.display import HTML

                handle[0].update(HTML(""))
            elif width[0]:
                print("\r" + " " * width[0] + "\r", end="", file=sys.stderr, flush=True)
                width[0] = 0

        def take_over() -> None:
            owner = getattr(self, "_progress_owner", None)
            if owner is not clear:
                if owner is not None:
                    owner()
                self._progress_owner = clear

        def tick(count: int, total: int | None = None) -> None:
            if self.progress == "off":
                return
            if bar_class[0] is not None:
                try:
                    if bar[0] is not None and total != bar[0].total:
                        close_bar()
                    if bar[0] is None:
                        take_over()
                        bar[0] = _progress_bar(bar_class[0], label, unit, total)
                    bar[0].update(count - bar[0].n)
                    return
                except Exception:  # an old tqdm or a broken widget front end: use the plain line instead
                    bar_class[0] = None
            now = time.monotonic()
            if total != started[1]:
                started[:] = [now, total]
            if now - started[0] < 0.5 or now - shown[0] < 0.5:
                return
            shown[0] = now
            take_over()
            text = _progress_text(label, unit, count, total, now - started[0])
            if self.use_html:
                from IPython.display import HTML, display

                if handle[0] is None:
                    handle[0] = display(HTML(""), display_id=True)
                if handle[0] is not None:  # display() returns None outside IPython
                    handle[0].update(HTML(f'<div style="opacity:.6">{_esc(text)}</div>'))
            else:
                width[0] = max(width[0], len(text))
                print("\r" + text.ljust(width[0]), end="", file=sys.stderr, flush=True)

        try:
            yield tick
        except BaseException:
            stopped[0] = True  # interrupted: a tqdm widget stays, red, where it stopped
            raise
        finally:
            clear()
            if getattr(self, "_progress_owner", None) is clear:
                self._progress_owner = None

    def help(self, command: Any = None) -> None:
        """Every command, grouped by task; help('name') shows one command in full."""
        view = type(self).__name__
        commands = {name: inspect.unwrap(member) for name, member in vars(type(self)).items()
                    if not name.startswith("_") and callable(member)}

        def about(name: str) -> str:  # the docstring's first paragraph, on one line
            return " ".join((inspect.getdoc(commands[name]) or "").split("\n\n")[0].split())

        if command is not None:
            name = getattr(command, "__name__", str(command))
            if name not in commands:
                close = difflib.get_close_matches(name, list(commands), n=3)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                self._show([_Note(f"{view} has no command {name!r}.{hint} help() lists them all.", "warn")])
                return
            self._show([_Title(f"{name}{_signature(commands[name])}", f"{view} command · help() lists them all"),
                        _Text(inspect.getdoc(commands[name]) or "(no description)", wrap=True)])
            return
        grouped = {name for names in self._GROUPS.values() for name in names}
        groups = {**self._GROUPS, "Other": tuple(name for name in commands if name not in grouped)}
        blocks: list[Any] = [_Title(f"{view} commands", "help('name') shows one in full · the data behind each report "
                                                        f"comes from .core ({type(self.core).__name__})"),
                             _Next(list(self._START), title="Start here")]
        for group, names in groups.items():
            rows = [[f"{name}{_signature(commands[name])}", about(name)] for name in names if name in commands]
            if rows:
                blocks.append(_Table(["Command", "What it shows"], rows, title=group, max_rows=0, code_cols=(0,)))
        self._show(blocks)

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
            _Next([("overview()", "size, cost and warnings of every bucket")]
                  + ([(_call("bucket_info", buckets[0].name), "one bucket's settings and risks")] if buckets else [])),
        ])

    @_friendly_errors
    def bucket_info(self, bucket: str, *, metrics: bool = True) -> None:
        """Bucket settings, policy in plain English, risks, and CloudWatch size, object count and
        estimated monthly cost (instant, even for huge buckets)."""
        cfg = self.core.bucket_config(bucket)
        account_block = self._account_block()
        blocked = any(settings and all(settings.values()) for settings in (account_block, cfg.public_access_block))
        open_to_public = "warn" if not blocked and "public_access_block" not in cfg.errors else ""
        cards = [
            ("Region", cfg.region or "?"),
            ("Versioning", _section(cfg, "versioning", cfg.versioning or "?")),
            ("Encryption", _encryption_label(cfg), "" if cfg.encryption or "encryption" in cfg.errors else "warn"),
            ("Block public access", _public_access_label(cfg), open_to_public),
            ("Account block public access", "?" if account_block is None else _block_label(account_block),
             open_to_public),
            ("Bucket policy", _section(cfg, "policy", "public" if cfg.policy_is_public else
                                       "private" if cfg.has_policy else "none"), "bad" if cfg.policy_is_public else ""),
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
        blocks.append(_Findings(bucket_findings(cfg, account_block) + policy_findings(statements),
                                empty="No issues found by these checks."))
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
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(cfg.tags.items())], title="Tags",
                                 collapsed=True))
        root = s3_uri(cfg.name, "")
        blocks.append(_Next([(_call("summary", root), "what's in it: folders, file types, cost, findings")]
                            + ([(_call("policy", cfg.name), "the policy in plain English, and its JSON")]
                               if statements else [])
                            + ([(_call("versions", root), "old versions and deleted files still billed")]
                               if cfg.versioning == "Enabled" else [(_call("tree", root), "folder sizes")])))
        self._show(blocks)

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """Every bucket in one table: size, estimated cost, versioning, encryption, public access,
        lifecycle and warnings. match='sagemaker-*' checks only matching bucket names."""
        with self._progress("Checking buckets", unit="buckets") as tick:
            reports = self.core.bucket_reports(match=match, metrics=metrics, progress=tick)
        account_block, account = self._account_block(), self.core.account_id()
        rows: list[tuple[int, list[Any]]] = []  # cells are text, or _Tone for a coloured status
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
            exposure, encryption = _exposure(cfg, account_block), _encryption_label(cfg)
            exposed = {"blocked": "ok", "not blocked": "warn"}.get(exposure, "bad" if "PUBLIC" in exposure else "")
            rows.append((-1 if bucket_size is None else bucket_size, [
                cfg.name, cfg.region or "?", "-" if bucket_objects is None else f"{bucket_objects:,}",
                human_size(bucket_size), human_money(bucket_cost), _section(cfg, "versioning", cfg.versioning or "?"),
                _Tone(encryption, "warn" if encryption == "none" else ""),
                _Tone(exposure, exposed),
                _section(cfg, "lifecycle", str(enabled_rules)),
                _Tone(str(len(bucket_warnings)), "warn" if bucket_warnings else "")]))
        rows.sort(key=lambda row: row[0], reverse=True)
        blocks: list[Any] = [
            _Title(f"All buckets ({len(reports)})", f"names matching {match!r}" if match else ""),
            _Cards([("Buckets", f"{len(reports):,}"), ("Objects", f"{objects:,}" if metrics else "-"),
                    ("Total size", human_size(size if metrics else None)),
                    ("Est. cost / month", human_money(cost if metrics else None)),
                    ("Buckets with warnings", f"{len({bucket for bucket, _ in warnings}):,}",
                     "warn" if warnings else "ok"),
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
            blocks.append(_Table(["Bucket", "Warning"], warnings, prose_cols=(1,),
                                 title="Warnings (bucket_info(name) shows every finding for one bucket)"))
        if rows:
            flagged = Counter(bucket for bucket, _ in warnings).most_common(1)
            biggest = rows[0][1][0]
            look = flagged[0][0] if flagged else biggest
            blocks.append(_Next([(_call("bucket_info", look), "why it's flagged, and what to change" if flagged
                                  else "its settings, policy and cost"),
                                 (_call("summary", s3_uri(biggest)), "what's in the biggest bucket")]))
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
        blocks += [_Findings(policy_findings(statements)), _policy_table(statements),
                   _Text(json.dumps(document, indent=2), title="Policy JSON", collapsed=True),
                   _Next([(_call("bucket_info", name), "the bucket's other settings and risks")])]
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
        if not rows and not listing.uri.endswith("/") and self.core.exists(listing.uri):
            blocks.append(_Note("That's a file, not a folder: head(uri) shows its details, preview(uri) what's in it."))
        elif not rows:
            blocks.append(_Note("Nothing here. Check the prefix (keys are case-sensitive)."))
        else:
            blocks.append(_Table(["Name", "Size", "Last modified (UTC)", "Storage class"], rows, max_rows=0))
            bucket = parse_s3_uri(listing.uri)[0]
            steps = [(_call("summary", listing.uri), "sizes, file types and cost of everything below")]
            steps += [(_call("ls", s3_uri(bucket, listing.folders[0])), "one level down")] if listing.folders else []
            steps += [(_call("preview", s3_uri(bucket, listing.objects[0].key)), "what's inside the first file")
                      ] if listing.objects else []
            blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def summary(self, uri: str, *, top_n: int = 10, folder_depth: int = 1, limit: int | None = None) -> None:
        """Full dashboard for a prefix: totals, estimated monthly cost, findings, folders, file types,
        storage classes, size and age distribution, largest objects. Lists every key once (use limit= on huge prefixes)."""
        with self._progress() as tick:
            s = self.core.summarize(uri, top_n=top_n, folder_depth=folder_depth, limit=limit, progress=tick)
        base = base_prefix(parse_s3_uri(s.uri)[1])
        blocks: list[Any] = [_Title(f"Summary of {s.uri}", f"{s.object_count:,} objects scanned in {s.scan_seconds:.1f}s")]
        findings = _Findings(summary_findings(s, self.core.prices), empty="No issues found by these checks.")
        if not s.object_count:
            blocks.append(_Note("No objects under this prefix."))
            return self._show(blocks + [findings])
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
        blocks.append(findings)
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
        steps = [(_call("tree", s.uri), "the folders below, level by level"),
                 (_call("duplicates", s.uri), "identical files and what the copies cost")]
        if s.cold_standard.size >= GB:
            steps.append((_call("what_if", s.uri, move_after=90, to="STANDARD_IA"),
                          "the saving if files unchanged for 90 days moved to STANDARD_IA"))
        else:
            steps.append((_call("find", s.uri, min_size="1GB"), "every file of 1 GB or more"))
        blocks.append(_Next(steps))
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
        top = max((path for path in tree.folders if path.count("/") == 1), key=lambda p: tree.folders[p].size,
                  default=None)
        if top:
            bucket, prefix = parse_s3_uri(tree.uri)
            blocks.append(_Next([(_call("summary", s3_uri(bucket, base_prefix(prefix) + top)),
                                  "the biggest folder in detail: file types, cost, findings")]))
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
        blocks += [_objects_table("Matches", matches, base_prefix(prefix)), _Next(_file_steps(bucket, matches))]
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
        self._show([_Title(f"{title} in {s3_uri(bucket, prefix)}"), _objects_table("", objects, base_prefix(prefix)),
                    _Next(_file_steps(bucket, objects))])

    @_friendly_errors
    def duplicates(self, uri: str, *, method: str = "hash", min_size: int | str = 1,
                   max_read: int | str | None = "10GB", limit: int | None = None) -> None:
        """Identical files under a prefix: what the copies take and cost, and folders that hold only copies.
        Matched by size + ETag, and by SHA-256 of the content where the ETags differ, reading at most max_read.
        method='strict' reads every file that shares its size; method='etag' reads none."""
        with self._progress("Listing", unit="files") as tick, \
                self._progress("Reading to compare", unit="B") as read_tick:
            report = self.core.find_duplicates(uri, min_size=min_size, method=method, max_read=max_read, limit=limit,
                                               progress=tick, read_progress=read_tick)
        base = base_prefix(parse_s3_uri(report.uri)[1])
        smallest = parse_size(min_size) or 0
        if report.method == "etag":
            read = "matched on size + ETag from the listing, no file read"
        else:
            read = (f"{_plural(report.files_read, 'file')} read to compare contents ({human_size(report.bytes_read)} "
                    f"in {_plural(report.requests, 'request')})")
        sub = [f"{report.scanned.count:,} files listed ({human_size(report.scanned.size)})", read,
               f"files of at least {human_size(smallest)}" if smallest > 1 else "",
               f"took {_duration(report.scan_seconds)}"]
        blocks: list[Any] = [_Title(f"Duplicate files in {report.uri}", " · ".join(filter(None, sub)))]
        findings = _Findings(duplicate_findings(report))
        if not report.scanned.count:
            blocks.append(_Note("No files under this prefix. Check the prefix (keys are case-sensitive)."))
            return self._show(blocks + [findings])
        blocks.append(_Cards([
            ("Duplicate groups", f"{len(report.groups):,}"),
            ("Redundant copies", f"{report.copies:,}"),
            ("Reclaimable", human_size(report.reclaimable)),
            ("Est. saving / month", human_money(report.monthly_cost)),
            ("Files listed", f"{report.scanned.count:,}{'+' if report.truncated else ''}"),
            ("Read to compare", "nothing" if report.method == "etag" else human_size(report.bytes_read)),
        ]))
        if not report.candidates.count:
            blocks.append(_Note("No two files have the same size, so none can be a copy of another.", "ok"))
        elif not report.groups and not report.not_compared.count:
            blocks.append(_Note(f"No duplicates: the {_plural(report.candidates.count, 'file')} that share a size "
                                "with another file all have different contents.", "ok"))
        blocks.append(findings)
        if not report.groups:
            return self._show(blocks)

        def name(folder: str) -> str:
            return _folder_label(relative_key(folder, base))

        folders = duplicate_folders(report)
        rows = []
        for folder in folders:
            others = [f for f in folder.elsewhere if f != folder.folder]
            where = [_folders_text(others, base)] if others else []
            where += ["this folder"] if folder.folder in folder.elsewhere else []
            rows.append([name(folder.folder), f"{folder.duplicated.count:,} of {folder.files:,}",
                         human_size(folder.duplicated.size), "\n".join(where)])
        blocks.append(_Table(["Folder", "Files with a copy", "Their size", "The copies are in"], rows,
                             title="Folders with duplicated files", bars=[_share(f.duplicated.count, f.files)
                                                                           for f in folders],
                             bar_label="% of the folder's files"))

        def listed(objects: list[ObjectInfo], most: int = 5) -> str:
            keys = [relative_key(o.key, base) for o in objects[:most]]
            return "\n".join(keys + ([f"… and {len(objects) - most:,} more"] if len(objects) > most else []))

        blocks.append(_Table(
            ["Size", "Files", "Reclaimable", "Est. $/month", "Matched by", "Keep", "Copies"],
            [[human_size(g.size), f"{len(g.objects):,}", human_size(g.reclaimable), human_money(g.monthly_cost),
              g.matched_by, relative_key(g.keep.key, base), listed(g.copies)] for g in report.groups],
            title=f"Duplicate groups, biggest saving first (cost {self._price_basis()})"))
        options = {"method": (method, "hash"), "min_size": (min_size, 1), "max_read": (max_read, "10GB"),
                   "limit": (limit, None)}
        args = "".join(f", {key}={value!r}" for key, (value, default) in options.items() if value != default)
        blocks.append(_Text(f"report = ui.core.find_duplicates({report.uri!r}{args})\n"
                            "df = report.to_df()              # one row per file: group, role, key, size, sha256, ...\n"
                            "copies = df[df.role == 'copy']   # every file but the one to keep in each group",
                            title="Get the list in pandas (nothing is deleted here)", code=True))
        copied = next((f for f in folders if f.all_copies), None) or next((f for f in folders if f.outside), None)
        if copied:
            bucket = parse_s3_uri(report.uri)[0]
            other = next(f for f in copied.elsewhere if f != copied.folder)
            blocks.append(_Next([(_call("compare", s3_uri(bucket, copied.folder), s3_uri(bucket, other)),
                                  "whether one folder is a full copy of the other")]))
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
        if v.top_noncurrent:
            blocks.append(_Table(["Key", "Old versions", "Old versions size"],
                                 [[k, f"{st.count:,}", human_size(st.size)] for k, st in v.top_noncurrent],
                                 title="Keys with the most noncurrent data"))
        steps = [(_call("deleted", v.uri), f"the {_plural(v.deleted_keys, 'deleted file')} you may be able to restore")
                 ] if v.deleted_keys else []
        steps += [(_call("history", s3_uri(parse_s3_uri(v.uri)[0], v.top_noncurrent[0][0])),
                   "every version of the key with the most old data")] if v.top_noncurrent else []
        blocks.append(_Next(steps))
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
                    ("Size to restore", human_size(sum(f.last_version.size for f in d.files if f.last_version))),
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
                f"    VersionId={example.marker_version_id!r},\n)", title="How to restore a file", code=True))
            blocks.append(_Next([(_call("history", s3_uri(bucket, example.key)),
                                  "every version of that file, to pick the one to bring back")]))
        self._show(blocks)

    @_friendly_errors
    def uploads(self, uri: str, *, with_sizes: bool = True) -> None:
        """Incomplete multipart uploads (billed, but invisible in normal listings)."""
        with self._progress("Measuring uploads", unit="uploads") as tick:
            uploads = self.core.incomplete_uploads(uri, with_sizes=with_sizes, progress=tick)
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
                                  "so add this to the existing list", code=True))
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
        blocks.append(_Next([(_call("preview", info["uri"]), "what's inside it"),
                             (_call("link", info["uri"]), "a download link that works without AWS access")]))
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
        elif p.kind == "document":
            blocks.append(_Text(_clip(p.data, 20_000) or "(no text)", title=p.info.get("excerpt", "Text"), wrap=True))
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
        if p.info.get("outline"):
            blocks.append(_listing_table(p.info["outline"], "Outline"))
        if p.info.get("table"):
            blocks.append(_document_table(p.info["table"], "First table"))
        if p.truncated and p.kind == "document":
            blocks.append(_Note(f"Showing the first {n} paragraphs; ui.document(uri) shows all of it."))
        if p.info.get("metadata"):
            blocks.append(_Table(["Key", "Value"], [[k, _clip(str(v), 200)] for k, v in p.info["metadata"].items()],
                                 title="Metadata"))
        if p.truncated and p.kind == "text":
            blocks.append(_Note(f"Showing the first {n} lines; pass n= for more."))
        document = p.format in ("pdf", "docx", "pptx")
        steps = [(_call("document", p.uri), "the full text, page by page")] if document else []
        blocks.append(_Next(steps + [(_call("download", p.uri), "a copy in this notebook's folder")]))
        self._show(blocks)

    @_friendly_errors
    def document(self, uri: str, *, pages: Iterable[int] | None = None, password: str | None = None,
                 max_chars: int = 200_000) -> None:
        """Full text of a PDF, Word .docx or PowerPoint .pptx, page by page or slide by slide,
        e.g. document(uri, pages=[1, 2]). PDFs need pypdf."""
        doc = self.core.read_document(uri, pages=pages, password=password)
        if doc.kind == "docx":  # Word's saved page count is often stale, so count paragraphs instead
            cards = [("Paragraphs", f"{len(doc.parts) - len(doc.tables):,}"), ("Tables", f"{len(doc.tables):,}")]
        else:
            cards = [("Pages" if doc.kind == "pdf" else "Slides", f"{doc.page_count or 0:,}")]
        cards.append(("Words", f"{doc.word_count:,}"))
        cards += [(label, value) for label, value in (("Title", doc.title), ("Author", doc.author)) if value]
        blocks: list[Any] = [_Title(f"{_FORMAT_LABELS[doc.kind]} {doc.uri}"), _Cards(cards)]
        if doc.kind == "pdf" and doc.parts and not any(part.strip() for part in doc.parts):
            blocks.append(_Note("No text layer (probably a scanned document); reading it needs OCR.", "warn"))
        shown = 0
        if doc.kind == "docx":
            sections = [("Text", doc.text)]
        else:
            name = "Page" if doc.kind == "pdf" else "Slide"
            sections = []
            for i, (number, part) in enumerate(zip(doc.numbers, doc.parts)):
                title = doc.slide_titles[i] if doc.kind == "pptx" else None
                notes = doc.notes[i] if doc.kind == "pptx" and doc.notes[i] else ""
                body = part + (f"\n\nSpeaker notes:\n{notes}" if notes else "")
                sections.append((f"{name} {number}" + (f": {title}" if title else ""), body))
        for heading, text in sections:
            if shown >= max_chars:
                blocks.append(_Note(f"Stopped after {max_chars:,} characters; pass max_chars= for more, "
                                    "or use ui.core.read_document(uri).text."))
                break
            blocks.append(_Text(text[: max_chars - shown] or "(no text)", title=heading, wrap=True))
            shown += len(text)
        self._show(blocks)

    @_friendly_errors
    def download(self, uri: str, path: str | None = None, *, limit: int | None = None) -> None:
        """Download a file, or a whole folder with its sub-folders, with a progress bar, and say where it went.
        Files already downloaded are skipped, so running it again resumes, e.g. download('s3://b/data/', 'data')."""
        bucket, key = parse_s3_uri(uri)
        if key and not key.endswith("/") and self.core.exists(uri):
            started = time.monotonic()
            with self._progress("Downloading", unit="B") as tick:
                local = self.core.download(uri, path, progress=tick)
            seconds, size = time.monotonic() - started, os.path.getsize(local)
            blocks: list[Any] = [
                _Title(f"Downloaded {s3_uri(bucket, key)}", f"to {local}"),
                _Cards([("Size", human_size(size)), ("Took", _duration(seconds)),
                        ("Speed", f"{human_size(size / max(seconds, 1e-3))}/s")]),
            ]
            opener = _PANDAS_READERS.get(detect_format(key)[0] or "")
            if opener:
                blocks.append(_Text(f"import pandas as pd\n\ndf = pd.{opener.format(path=repr(local))}",
                                    title="Open it", code=True))
            return self._show(blocks)
        with self._progress("Listing", unit="files") as list_tick, self._progress("Downloading", unit="B") as tick:
            d = self.core.download_folder(uri, path, limit=limit, progress=tick, list_progress=list_tick)
        speed = f"{human_size(d.downloaded.size / max(d.seconds, 1e-3))}/s" if d.downloaded.size else "-"
        blocks = [
            _Title(f"{'Downloaded' if d.downloaded.count else 'Download of'} {d.uri}", f"to {d.path}"),
            _Cards([("Files downloaded", f"{d.downloaded.count:,}"), ("Size", human_size(d.downloaded.size)),
                    ("Already there", f"{d.already_there.count:,}"), ("Not downloaded", f"{len(d.skipped):,}"),
                    ("Took", _duration(d.seconds)), ("Speed", speed)]),
        ]
        if d.truncated:
            blocks.append(_Note(f"Stopped at limit={limit:,} files; pass a bigger limit= (or none) for the rest. "
                                "Running it again skips what's already downloaded.", "warn"))
        if not (d.downloaded.count or d.already_there.count or d.skipped):
            blocks.append(_Note("No files under this folder. Check the path (keys are case-sensitive)."))
        elif d.downloaded.count or d.already_there.count:
            again = (f" {_plural(d.already_there.count, 'file')} ({human_size(d.already_there.size)}) were already "
                     "there with the same size and time, so they weren't downloaded again.") if d.already_there.count else ""
            blocks.append(_Note(f"Files are in {d.path}, in the same sub-folders as in S3.{again}", "ok"))
        if d.skipped:
            archived = sum(reason in ARCHIVE_CLASSES for reason in d.skipped.values())
            if archived:
                blocks.append(_Note(f"{_plural(archived, 'file')} in GLACIER / DEEP_ARCHIVE weren't downloaded: they "
                                    "need a restore first (the S3 console, or restore_object).", "warn"))
            if len(d.skipped) > archived:
                blocks.append(_Note(f"{_plural(len(d.skipped) - archived, 'file')} couldn't be downloaded; the table "
                                    "says why. Running download() again retries them.", "warn"))
            blocks.append(_Table(["Key", "Why"], [[relative_key(k, parse_s3_uri(d.uri)[1]), why]
                                                  for k, why in sorted(d.skipped.items())], title="Not downloaded"))
        self._show(blocks)

    @_friendly_errors
    def download_zip(self, uri: str, path: str | None = None, *, max_size: int | str = "100MB",
                     max_files: int = 10_000, dry_run: bool = False) -> None:
        """Download a file or folder as one .zip, after checking this notebook can: size limit (100 MB by default),
        file count, disk space, memory and read access. dry_run=True only runs the checks."""
        with self._progress("Listing", unit="files") as list_tick, self._progress("Zipping", unit="B") as tick:
            z = self.core.download_zip(uri, path, max_size=max_size, max_files=max_files, dry_run=dry_run,
                                       progress=tick, list_progress=list_tick)
        plan = z.plan
        checks = zip_checks(plan)
        can = plan.can_download
        files = f"{len(plan.files):,}{'+' if plan.more else ''}"
        blocks: list[Any] = [_Title(f"Zip of {plan.uri}", f"{_plural(len(plan.files), 'file')} · "
                                                        f"{human_size(plan.size)} → {plan.path}")]
        cards = [("Can download", "yes" if can else "no", "ok" if can else "bad"), ("Files", files),
                 ("Size", human_size(plan.size)),
                 ("Limit", human_size(plan.max_size)), ("Free disk", human_size(plan.disk_free))]
        if z.written:
            saved = _share(plan.size - z.zip_size, plan.size)
            cards += [("Zip size", human_size(z.zip_size)), ("Took", _duration(z.seconds))]
            smaller = f", {saved:.0%} smaller than the files" if saved >= 0.01 else ""
            blocks += [_Cards(cards), _Note(
                f"Saved {plan.path} ({human_size(z.zip_size)}{smaller}). To get it onto your computer, right-click "
                "it in JupyterLab's file browser and choose Download.", "ok")]
        elif can:
            options = {"max_size": (max_size, "100MB"), "max_files": (max_files, 10_000)}
            args = "".join(f", {k}={v!r}" for k, (v, default) in options.items() if v != default)
            blocks += [_Cards(cards), _Note(f"It can be downloaded: every check passed. Run "
                                            f"download_zip({plan.uri!r}{args}) to make the zip.", "ok")]
        else:
            why = {"Files": "too many files" if plan.more else "nothing to zip", "Size": "over the size limit",
                   "Disk space": "not enough disk space", "Read access": "the files can't be read"}
            failed = ", ".join(why.get(name, name) for name, ok, _ in checks if ok is False)
            blocks += [_Cards(cards), _Note(f"Can't zip this here yet: {failed}. Nothing was downloaded; the notes "
                                            "below say what to change.", "warn")]
        blocks.append(_Findings(zip_findings(plan)))
        if z.failed:
            blocks.append(_Note(f"{_plural(len(z.failed), 'file')} couldn't be read while zipping, so the zip leaves "
                                "them out (the table lists them). Running download_zip() again retries them.", "warn"))
        blocks.append(_Table(["Check", "Result", "Details"],
                             [[name, _Tone("✓ ok", "ok") if ok else _Tone("✗ no", "bad") if ok is False else "· note",
                               details]
                              for name, ok, details in checks], title="Can this notebook make the zip?", max_rows=0))
        left_out = {**plan.left_out, **z.failed}
        if left_out:
            base = parse_s3_uri(plan.uri)[1]
            blocks.append(_Table(["Key", "Why it isn't in the zip"],
                                 [[relative_key(key, base_prefix(base)) or key, why]
                                  for key, why in sorted(left_out.items())], title="Left out"))
        self._show(blocks)

    @_friendly_errors
    def link(self, uri: str, *, expires: int = 3600) -> None:
        """Clickable presigned download link (no AWS login needed until it expires)."""
        url = self.core.presigned_url(uri, expires=expires)
        self._show([_Link(url, f"Download {parse_s3_uri(uri)[1].rsplit('/', 1)[-1]} (valid {expires // 60} min)")])
