"""
What format a file is, by its name and by its first bytes, how it's compressed, and the
errors decoding a broken file raises.
"""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import lzma
import mimetypes
import struct
import tarfile
import zipfile
import zlib
from typing import Any, Callable

from ..._kit.fmt import file_extension


_TEXT_EXTS = (
    "txt",
    "log",
    "md",
    "rst",
    "yaml",
    "yml",
    "xml",
    "html",
    "htm",
    "py",
    "sql",
    "ini",
    "cfg",
    "conf",
    "toml",
    "sh",
    "js",
    "ts",
    "out",
    "err",
    "properties",
    "r",
    "scala",
    "java",
    "go",
    "rs",
    "c",
    "cpp",
    "h",
    "css",
    "jsx",
    "tsx",
    "tf",
    "srt",
    "vtt",
)

_FORMAT_BY_EXT = {
    "csv": "csv",
    "tsv": "tsv",
    "tab": "tsv",
    "psv": "psv",
    "parquet": "parquet",
    "pq": "parquet",
    "orc": "orc",
    "feather": "arrow",
    "arrow": "arrow",
    "ipc": "arrow",
    "avro": "avro",
    "xlsx": "excel",
    "xlsm": "excel",
    "xls": "excel",
    "json": "json",
    "jsonl": "jsonl",
    "ndjson": "jsonl",
    "ipynb": "notebook",
    "zip": "zip",
    "tar": "tar",
    "tgz": "tar",
    "npy": "npy",
    "npz": "npz",
    "safetensors": "safetensors",
    "pt": "torch",
    "pth": "torch",
    "ckpt": "torch",
    "pkl": "pickle",
    "pickle": "pickle",
    "joblib": "pickle",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "bmp": "image",
    "wav": "audio",
    "mp3": "audio",
    "flac": "audio",
    "ogg": "audio",
    "m4a": "audio",
    "aac": "audio",
    "mp4": "video",
    "webm": "video",
    "mov": "video",
    "m4v": "video",
    "pdf": "pdf",
    "docx": "docx",
    "docm": "docx",
    "dotx": "docx",
    "dotm": "docx",
    "pptx": "pptx",
    "pptm": "pptx",
    "potx": "pptx",
    "ppsx": "pptx",
    "ppsm": "pptx",
    "doc": "oldoffice",
    "dot": "oldoffice",
    "ppt": "oldoffice",
    "pps": "oldoffice",
    "pot": "oldoffice",
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
            raise ImportError(
                "Reading .zst needs Python 3.14+ or the zstandard package (pip install zstandard)"
            ) from exc
        return zstandard.ZstdDecompressor().stream_reader(
            stream, read_across_frames=True
        )
    return zstd.ZstdFile(stream)


_DECOMPRESSORS: dict[str, Callable[[Any], Any]] = {
    "gz": lambda f: gzip.GzipFile(fileobj=f),
    "bz2": bz2.BZ2File,
    "xz": lzma.LZMAFile,
    "zst": _zstd_reader,
}

_CODEC_ALIASES = {
    "gz": "gz",
    "gzip": "gz",
    "tgz": "gz",
    "bz2": "bz2",
    "xz": "xz",
    "zst": "zst",
    "zstd": "zst",
}


def detect_format(key: str) -> tuple[str | None, str | None]:
    """
    Guess (format, compression) from the key: 'x.csv.gz' -> ('csv', 'gz'), 'm.tgz' -> ('tar', 'gz'),
    'x.bin' -> (None, None).
    """
    parts = key.rsplit("/", 1)[-1].lower().split(".")
    if len(parts) > 1 and parts[-1] == "tgz":
        return "tar", "gz"
    compression = (
        _CODEC_ALIASES[parts.pop()]
        if len(parts) > 1 and parts[-1] in _CODEC_ALIASES
        else None
    )
    return (_FORMAT_BY_EXT.get(parts[-1]) if len(parts) > 1 else None), compression


def _browser_type(key: str) -> str | None:
    """
    The Content-Type that makes a browser show the file in its tab rather than save it: 'a.pdf' ->
    'application/pdf', 'a.csv' -> plain text (a browser saves text/csv), 'a.parquet' or 'a.csv.gz' -> None.
    """
    fmt, compression = detect_format(key)
    if compression or fmt is None:
        return None
    if fmt in ("image", "audio", "video", "pdf", "json") or file_extension(key) in ("html", "htm"):
        return mimetypes.guess_type(key.rsplit("/", 1)[-1])[0]
    if fmt in ("text", "csv", "tsv", "psv", "jsonl", "notebook"):
        return "text/plain; charset=utf-8"
    return None


_MAGIC_CODECS = [
    (b"\x1f\x8b", "gz"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zst"),
]

_MAGIC_FORMATS = [  # (offset, leading bytes, format)
    (0, b"PAR1", "parquet"),
    (0, b"ORC", "orc"),
    (0, b"ARROW1", "arrow"),
    (0, b"FEA1", "arrow"),
    (0, b"Obj\x01", "avro"),
    (0, b"\x93NUMPY", "npy"),
    (0, b"%PDF-", "pdf"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "oldoffice"),  # OLE2: Office 97-2003 files
    (0, b"PK\x03\x04", "zip"),
    (0, b"PK\x05\x06", "zip"),
    (0, b"\x89PNG\r\n\x1a\n", "image"),
    (0, b"\xff\xd8\xff", "image"),
    (0, b"GIF8", "image"),
    (257, b"ustar", "tar"),
]

_IMAGE_MIMES = [
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
    (b"RIFF", "image/webp"),
]


def sniff_format(head: bytes) -> tuple[str | None, str | None]:
    """
    Guess (format, compression) from an object's first bytes (512 is enough), for files
    whose name has no extension or the wrong one.
    """
    for magic, codec in _MAGIC_CODECS:
        if head.startswith(magic):
            return None, codec
    if head.startswith(b"BZh") and head[3:4].isdigit():
        return None, "bz2"
    for offset, magic, fmt in _MAGIC_FORMATS:
        if head[offset : offset + len(magic)] == magic:
            return fmt, None
    if head.lstrip()[:1] in (b"{", b"["):
        return "json", None
    return None, None


def _looks_binary(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        return (
            exc.start < len(sample) - 3
        )  # a multi-byte char cut at the end is still text


# What a broken or mislabeled file can raise while being decoded (pyarrow's errors subclass ValueError / OSError).
_DATA_ERRORS = (
    ValueError,
    ImportError,
    OSError,
    EOFError,
    zlib.error,
    lzma.LZMAError,
    zipfile.BadZipFile,
    tarfile.TarError,
)

_READ_ERRORS = (
    *_DATA_ERRORS,
    KeyError,
    IndexError,
    struct.error,
)  # + malformed headers, while previewing


class _ReadLimit(OSError):
    """
    A read that would go over max_read, or over DETAILS_FILE_MAX of one file.
    """
