"""
Avro container files read with the standard library: the header, the schema and the rows.
"""

from __future__ import annotations

import bz2
import importlib.util
import io
import json
import lzma
import struct
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .formats import _zstd_reader


# ---- Avro object container files (no extra package needed)


def _avro_long(buf: bytes, pos: int) -> tuple[int, int]:
    """
    Zig-zag varint at buf[pos:] -> (value, next position).
    """
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
    """
    Collect every named type (record / enum / fixed) so references to it can be resolved.
    """
    if isinstance(schema, list):
        for branch in schema:
            _avro_names(branch, names)
    elif isinstance(schema, dict):
        if (
            schema.get("type") in ("record", "error", "enum", "fixed")
            and "name" in schema
        ):
            name, namespace = schema["name"], schema.get("namespace")
            for alias in {
                name,
                name.rsplit(".", 1)[-1],
                f"{namespace}.{name}" if namespace else name,
            }:
                names[alias] = schema
        for child in [f["type"] for f in schema.get("fields", [])] + [
            schema.get("items"),
            schema.get("values"),
        ]:
            if child is not None:
                _avro_names(child, names)
    return names


def _avro_read(
    schema: Any, buf: bytes, pos: int, names: dict[str, Any]
) -> tuple[Any, int]:
    """
    Decode one value of `schema` at buf[pos:] -> (value, next position).
    """
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
            return buf[pos : pos + schema["size"]], pos + schema["size"]
        if kind in ("array", "map"):
            items: list[Any] = []
            while (
                True
            ):  # blocks of items; a negative count is followed by the block's byte size
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
        value, pos = _avro_read(
            kind, buf, pos, names
        )  # e.g. {"type": "long", "logicalType": ...}
        logical = schema.get("logicalType")
        if logical in ("timestamp-millis", "timestamp-micros") and isinstance(
            value, int
        ):
            scale = 1000 if logical == "timestamp-millis" else 1_000_000
            value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                seconds=value / scale
            )
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
        data = buf[pos : pos + size]
        return (
            data.decode("utf-8", "replace") if schema == "string" else data
        ), pos + size
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
        for module, call in (
            ("snappy", "decompress"),
            ("cramjam", "snappy.decompress_raw"),
        ):
            try:
                target: Any = importlib.import_module(module)
            except ImportError:
                continue
            for part in call.split("."):
                target = getattr(target, part)
            return bytes(target(block[:-4]))
        raise ImportError(
            "Avro snappy blocks need python-snappy or cramjam (pip install python-snappy)"
        )
    raise ValueError(f"Unsupported Avro codec {codec!r}")


def _avro_header(data: bytes) -> tuple[Any, str, bytes, int]:
    """
    (schema, codec, sync marker, where the first block starts) from the start of an Avro container file.
    """
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
    schema = json.loads(meta["avro.schema"])
    return schema, meta.get("avro.codec", b"null").decode(), data[pos : pos + 16], pos + 16


def _avro_rows(data: bytes, pos: int) -> tuple[int, bool]:
    """
    Records in an Avro file's blocks from `pos` on, counted from the block headers without decoding them
    -> (records, whether every block was there).
    """
    rows = 0
    while pos < len(data):
        try:
            count, pos = _avro_long(data, pos)
            size, pos = _avro_long(data, pos)
        except ValueError:
            return rows, False
        if pos + size + 16 > len(data):
            return rows, False
        rows, pos = rows + count, pos + size + 16
    return rows, True


def parse_avro(data: bytes, n: int | None = None) -> tuple[Any, str, list[Any], bool]:
    """
    Avro object container bytes -> (schema, codec, records, complete). Decodes up to `n` records;
    `data` may be just the start of a file (complete=False when it stops at a cut-off block).
    """
    schema, codec, sync, pos = _avro_header(data)
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
        block = _avro_decompress(codec, data[pos : pos + size])
        if data[pos + size : pos + size + 16] != sync:
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
        return (
            f"{kind} {schema['name']}"
            if kind in ("record", "enum", "fixed") and "name" in schema
            else kind
        )
    return str(schema)
