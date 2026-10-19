"""
Conversion and formatting: DynamoDB JSON to plain Python and back, the type names, item sizes and the
capacity units they cost, JSON and one-line values, nested items flattened and read by path.
"""

from __future__ import annotations

import base64
import json
import math
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import Binary, TypeSerializer

from .._kit.text import _clip


KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4

ITEM_SIZE_LIMIT = 400 * KB  # DynamoDB rejects bigger items

TYPE_NAMES = {
    "S": "string",
    "N": "number",
    "B": "binary",
    "BOOL": "boolean",
    "NULL": "null",
    "M": "map",
    "L": "list",
    "SS": "string set",
    "NS": "number set",
    "BS": "binary set",
}


def _units(value: float | None) -> str:
    """
    Capacity units for display: 12.0 -> '12', 0.5 -> '0.5', 14457041.1 -> '14,457,041'.
    """
    if value is None:
        return "-"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}".removesuffix(".0")


def _number(text: str) -> int | float:
    """
    DynamoDB number string -> int when it's whole, else float: '42' -> 42, '1.50' -> 1.5.
    """
    value = Decimal(text)
    return int(value) if value == value.to_integral_value() else float(value)


def _binary(raw: Any) -> bytes:
    return (
        bytes(raw) if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
    )  # exports hold base64 text


def _raw_bytes(value: Any) -> bytes:
    return bytes(value.value) if isinstance(value, Binary) else bytes(value)


def from_dynamo(value: dict[str, Any]) -> Any:
    """
    One DynamoDB-JSON value -> plain Python: {'N': '42'} -> 42, {'SS': ['a']} -> {'a'},
    {'M': {'city': {'S': 'Pune'}}} -> {'city': 'Pune'}.
    """
    [(kind, raw)] = value.items()
    if kind == "M":
        return {name: from_dynamo(child) for name, child in raw.items()}
    if kind == "L":
        return [from_dynamo(child) for child in raw]
    if kind == "N":
        return _number(raw)
    if kind == "NS":
        return {_number(n) for n in raw}
    if kind == "SS":
        return set(raw)
    if kind == "B":
        return _binary(raw)
    if kind == "BS":
        return {_binary(b) for b in raw}
    if kind == "NULL":
        return None
    return raw  # S, BOOL


def from_dynamo_item(item: dict[str, dict]) -> dict[str, Any]:
    """
    A whole item in DynamoDB JSON (the low-level API, or a line of an S3 export) -> plain dict.
    """
    return {name: from_dynamo(value) for name, value in item.items()}


_SERIALIZER = TypeSerializer()


def _decimals(value: Any) -> Any:
    """
    floats -> Decimal, recursively (boto3 refuses floats).
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _decimals(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimals(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return {_decimals(v) for v in value}
    return value


def to_dynamo(value: Any) -> dict[str, Any]:
    """
    Plain Python -> DynamoDB-JSON value: 42 -> {'N': '42'}, 'x' -> {'S': 'x'}. Floats are fine.
    """
    try:
        return _SERIALIZER.serialize(_decimals(value))
    except TypeError as exc:
        raise ValueError(
            f"DynamoDB can't store a {type(value).__name__} ({exc}). Dates are usually stored as "
            "ISO strings or epoch numbers: pass the form your table uses."
        ) from exc


def dynamo_type(value: Any) -> str:
    """
    DynamoDB type code of a plain Python value: 'S', 'N', 'B', 'BOOL', 'NULL', 'M', 'L', 'SS', 'NS' or 'BS'.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, (int, float, Decimal)):
        return "N"
    if isinstance(value, str):
        return "S"
    if isinstance(value, (bytes, bytearray, Binary)):
        return "B"
    if isinstance(value, dict):
        return "M"
    if isinstance(value, (list, tuple)):
        return "L"
    if isinstance(value, (set, frozenset)):
        return dynamo_type(next(iter(value), "")) + "S"
    return type(value).__name__


def _number_size(value: Any) -> int:
    digits = (
        Decimal(str(value)).normalize().as_tuple().digits
    )  # significant digits, trailing zeros dropped
    return math.ceil(len(digits) / 2) + 1


def value_size(value: Any) -> int:
    """
    Bytes DynamoDB counts for one attribute value (without its name), following its sizing rules.
    """
    if value is None or isinstance(value, bool):
        return 1
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (int, float, Decimal)):
        return _number_size(value)
    if isinstance(value, (bytes, bytearray, Binary)):
        return len(_raw_bytes(value))
    if isinstance(
        value, dict
    ):  # 3 bytes per map, 1 per element, plus each element's name and value
        return 3 + sum(
            len(str(k).encode("utf-8")) + value_size(v) + 1 for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return 3 + sum(value_size(v) + 1 for v in value)
    if isinstance(value, (set, frozenset)):
        return sum(value_size(v) for v in value)
    return len(str(value).encode("utf-8"))


def item_size(item: dict[str, Any]) -> int:
    """
    Approximate item size as DynamoDB counts it (attribute names + values). The limit is 400 KB;
    a read unit covers 4 KB (strongly consistent) and a write unit 1 KB.
    """
    return sum(
        len(name.encode("utf-8")) + value_size(value) for name, value in item.items()
    )


def read_units(size: int, *, consistent: bool = True) -> float:
    """
    Capacity units to read one item of `size` bytes with GetItem (eventually consistent is half).
    """
    units = max(1, math.ceil(size / (4 * KB)))
    return units if consistent else units / 2


def write_units(size: int) -> int:
    """
    Capacity units to write one item of `size` bytes.
    """
    return max(1, math.ceil(size / KB))


def _plain(value: Any) -> Any:
    """
    JSON-friendly copy: sets -> sorted lists, binary -> base64 text.
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_plain(v) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=str)
    if isinstance(value, (bytes, bytearray, Binary)):
        return base64.b64encode(_raw_bytes(value)).decode()
    if isinstance(value, Decimal):
        return _number(str(value))
    return value


def to_json(value: Any, *, indent: int | None = None) -> str:
    """
    Item or value -> JSON text (sets become sorted lists, binary becomes base64).
    """
    return json.dumps(_plain(value), indent=indent, ensure_ascii=False, default=str)


def format_value(value: Any, width: int = 80, *, oneline: bool = True) -> str:
    """
    Short display text for one attribute value: strings as they are ('' shows as ""), maps,
    lists and sets as compact JSON, binary as base64.
    """
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, str):
        text = value or '""'
    elif isinstance(value, (bytes, bytearray, Binary)):
        text = "b64:" + base64.b64encode(_raw_bytes(value)).decode()
    elif isinstance(value, (dict, list, tuple, set, frozenset)):
        text = to_json(value)
    else:
        text = str(value)
    if oneline:
        text = text.replace("\r\n", " ↵ ").replace("\n", " ↵ ")
    return _clip(text, width)


def flatten_item(
    item: dict[str, Any], *, max_depth: int | None = None
) -> dict[str, Any]:
    """
    Nested maps -> dotted columns: {'address': {'city': 'Pune'}} -> {'address.city': 'Pune'}.
    Lists, sets and empty maps stay as values.
    """
    flat: dict[str, Any] = {}

    def walk(path: str, value: Any, depth: int) -> None:
        if (
            isinstance(value, dict)
            and value
            and (max_depth is None or depth < max_depth)
        ):
            for name, child in value.items():
                walk(f"{path}.{name}", child, depth + 1)
        else:
            flat[path] = value

    for name, value in item.items():
        walk(name, value, 0)
    return flat


_MISSING = object()


def get_path(item: dict[str, Any], path: str, default: Any = None) -> Any:
    """
    item['address']['city'] for path 'address.city'. An attribute literally named 'address.city' wins.
    """
    if path in item:
        return item[path]
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value
