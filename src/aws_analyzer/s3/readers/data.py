"""
Data files: safetensors headers, the start of a big JSON file, Arrow tables (parquet, ORC,
Feather), .npy headers and the times of zip entries.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from ..._kit.deps import _require

from ..helpers import MB


def _safetensors_length(head: bytes) -> int:
    """
    The header length a .safetensors file starts with (its first 8 bytes).
    """
    length = int.from_bytes(head, "little") if len(head) == 8 else 0
    if not 2 <= length <= 100 * MB:
        raise ValueError("Not a safetensors file (bad header length)")
    return length


def _parse_safetensors(header: bytes) -> dict[str, Any]:
    """
    A .safetensors JSON header -> {'tensors': [{tensor, dtype, shape, parameters}], 'metadata': {...}}.
    """
    tensors = json.loads(header)
    metadata = tensors.pop("__metadata__", None) or {}
    return {
        "tensors": [
            {
                "tensor": name,
                "dtype": spec.get("dtype"),
                "shape": tuple(spec.get("shape", [])),
                "parameters": math.prod(spec.get("shape", [])),
            }
            for name, spec in tensors.items()
        ],
        "metadata": metadata,
    }


_JSON_PREFIX_RE = re.compile(
    r'\s*(?:([{}\[\]:,])|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(true|false|null|NaN|-?Infinity))'
)

_JSON_CUT_RE = re.compile(r'\s*(?:"|-?[\d.eE+-]*$|-?[tfnNI][a-z]*$)')  # what a cut can leave: a string, number, word


def _json_prefix(text: str) -> tuple[Any, int] | None:
    """
    The start of a JSON document that was cut off (the first bytes of a file too big to read whole): the objects
    and arrays read so far, closed where the text stops, and how many of them were still open there (0: the text
    holds the whole document). None when the text isn't the start of a JSON object or array.
    """
    stack: list[Any] = []  # the open objects and arrays, outermost first
    keys: list[Any] = []  # each open object's key waiting for its value (None for arrays, or before the key)
    root: Any = None
    text = text.lstrip("\ufeff")
    pos, end = 0, len(text)
    while stack or root is None:
        match = _JSON_PREFIX_RE.match(text, pos)
        if not match or (match.group(3) and match.end() == end):  # a number at the very end may be cut short
            break
        pos = match.end()
        punct, string, number, word = match.groups()
        if punct in (",", ":"):
            continue
        if punct in ("}", "]"):
            if not stack or (punct == "}") != isinstance(stack[-1], dict):
                return None
            stack.pop()
            keys.pop()
            continue
        if punct:
            value: Any = {} if punct == "{" else []
        elif string is not None and "\\" not in string:
            value = string[1:-1]  # most strings have no escapes: much quicker than json.loads
        else:
            try:
                value = json.loads(string or number or word)
            except ValueError:
                return None
        if string is not None and stack and isinstance(stack[-1], dict) and keys[-1] is None:
            keys[-1] = value
            continue
        if not stack:
            if not isinstance(value, (dict, list)):
                return None  # a lone string or number isn't worth a tree
            root = value
        elif isinstance(stack[-1], dict):
            if keys[-1] is None:
                return None  # a value where a key belongs
            stack[-1][keys[-1]] = value
            keys[-1] = None
        else:
            stack[-1].append(value)
        if punct:
            stack.append(value)
            keys.append(None)
    if root is None or (stack and not _JSON_CUT_RE.match(text, pos)):
        return None  # not JSON, or broken before the cut
    return root, len(stack)


def _json_lines_start(data: bytes) -> bool:
    """
    Whether data starts like JSON lines (one record per line): a whole JSON value on its first line, then more.
    """
    first, newline, _ = data.lstrip().partition(b"\n")
    if not newline:
        return False
    try:
        json.loads(first)
    except ValueError:
        return False
    return True


def _json_shape(value: Any, cut: int = 0) -> dict[str, Any]:
    """
    Preview.info for a JSON value: its keys (an object) or items (an array), and how deep it nests. A document
    that was cut off (cut > 0) has at least that many, and its depth isn't known.
    """
    if not isinstance(value, (dict, list)):
        return {}
    count = f"{len(value):,}+" if cut else len(value)
    shape: dict[str, Any] = {"keys" if isinstance(value, dict) else "items": count}
    if not cut:
        depth, level = 0, [value]
        while level:
            depth += 1
            level = [child for node in level for child in (node.values() if isinstance(node, dict) else node)
                     if isinstance(child, (dict, list)) and child]
        shape["depth"] = depth
    return shape


def _read_arrow_table(
    fmt: str, handle: Any, nrows: int | None, columns: list[str] | None
) -> Any:
    """
    Parquet / ORC / Feather (Arrow IPC) from a seekable file -> pyarrow Table (first nrows only
    reads the row groups / stripes / batches it needs).
    """
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
            reader = _require("pyarrow.ipc", "Reading Feather / Arrow").open_file(
                handle
            )
        except pa.ArrowInvalid:  # Feather V1 (pre-2020) isn't an Arrow IPC file
            handle.seek(0)
            table = _require("pyarrow.feather", "Reading Feather").read_table(
                handle, columns=columns
            )
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
    return (
        table.select(columns) if columns and (fmt == "arrow" or not batches) else table
    )


def _columnar_info(fmt: str, handle: Any) -> dict[str, Any]:
    """
    Rows, columns and layout of an ORC / Feather file from its metadata.
    """
    if fmt == "orc":
        orc = _require("pyarrow.orc", "Reading ORC").ORCFile(handle)
        return {
            "rows": orc.nrows,
            "stripes": orc.nstripes,
            "compression": getattr(orc, "compression", None),
            "columns": [(f.name, str(f.type)) for f in orc.schema],
        }
    pa = _require("pyarrow", "Reading Feather / Arrow")
    try:
        reader = _require("pyarrow.ipc", "Reading Feather / Arrow").open_file(handle)
    except pa.ArrowInvalid:
        return {}
    info: dict[str, Any] = {
        "batches": reader.num_record_batches,
        "columns": [(f.name, str(f.type)) for f in reader.schema],
    }
    if hasattr(reader, "count_rows"):
        info["rows"] = reader.count_rows()
    return info


def _zip_time_of(moment: datetime) -> tuple[int, int, int, int, int, int]:
    """
    A file's time as a zip stores it (zip can't hold times before 1980).
    """
    moment = moment.astimezone(timezone.utc) if moment.tzinfo else moment
    return max(
        (
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
        ),
        (1980, 1, 1, 0, 0, 0),
    )


def _zip_time(stamp: tuple[int, ...]) -> datetime | None:
    try:
        return datetime(*stamp, tzinfo=timezone.utc)
    except (TypeError, ValueError):  # zip allows dates like 1980-00-00
        return None


def _npy_header(fp: Any) -> tuple[tuple[int, ...], bool, Any]:
    """
    Read a .npy header from `fp` -> (shape, fortran_order, dtype); fp is left at the first data byte.
    """
    npformat = _require("numpy.lib.format", "Reading .npy")
    version = npformat.read_magic(fp)
    read_header = (
        npformat.read_array_header_1_0
        if version == (1, 0)
        else npformat.read_array_header_2_0
    )
    return read_header(fp)
