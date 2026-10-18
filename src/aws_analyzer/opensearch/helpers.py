"""
Parsing and formatting: sizes and numbers from the _cat API, engine versions, where an index is
(parse_location), and vector arithmetic.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from .._kit.text import _clip


KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4


def _int(value: Any) -> int | None:
    """
    '1,024' / '1024' / 1024.0 -> 1024; None for missing or unreadable values (the _cat API answers in strings).
    """
    try:
        return None if value in (None, "") else int(float(str(value).replace(",", "")))
    except ValueError:
        return None


_CAT_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?b)?\s*$", re.IGNORECASE)


def _bytes(value: Any) -> int | None:
    """
    A size from the _cat API -> bytes: '123456' (bytes=b) or OpenSearch's own '1.2gb'. None when missing.
    """
    match = _CAT_SIZE_RE.match(str(value)) if value not in (None, "") else None
    if not match:
        return None
    unit = (match.group(2) or "b").lower()
    return int(float(match.group(1)) * {"b": 1, "kb": KB, "mb": MB, "gb": GB, "tb": TB, "pb": TB * 1024}[unit])


def _epoch_ms(value: Any) -> datetime | None:
    """
    Milliseconds since 1970 (Serverless dates, index.creation_date) -> an aware datetime.
    """
    number = _int(value)
    return None if number is None else datetime.fromtimestamp(number / 1000, timezone.utc)


def parse_engine_version(text: str | None) -> tuple[str, str]:
    """
    'OpenSearch_2.17' -> ('OpenSearch', '2.17'); 'Elasticsearch_7.10' -> ('Elasticsearch', '7.10').
    """
    engine, _, version = (text or "").partition("_")
    if not version:
        return ("OpenSearch", engine) if re.match(r"^\d", engine) else (engine or "OpenSearch", "")
    return engine, version


def version_at_least(version: str | None, wanted: tuple[int, ...]) -> bool | None:
    """
    '2.17' >= (2, 13) -> True. None when the version is unknown, so callers can stay quiet.
    """
    numbers = [int(n) for n in re.findall(r"\d+", version or "")[: len(wanted)]]
    if not numbers:
        return None
    return tuple(numbers + [0] * (len(wanted) - len(numbers))) >= wanted


_ARN_RE = re.compile(r"^arn:aws[\w-]*:(es|aoss):([\w-]+):(\d{12}):(domain|collection)/([^/]+)(?:/(.*))?$")

_AWS_HOST_RE = re.compile(
    r"\.(?:([a-z]{2}(?:-gov|-iso[a-z]?)?-[a-z]+-\d)\.(es|aoss)\.amazonaws\.com"
    r"|aos\.([a-z]{2}(?:-gov|-iso[a-z]?)?-[a-z]+-\d)\.on\.aws)$"
)


def parse_location(where: str) -> tuple[str, str | None]:
    """
    Where an index lives -> (domain, collection or URL; index or None).

    'vectors-prod/docs' -> ('vectors-prod', 'docs'), 'vectors-prod' -> ('vectors-prod', None). Also takes an
    endpoint URL with or without https:// ('https://search-x.us-east-1.es.amazonaws.com/docs' ->
    ('https://search-x.us-east-1.es.amazonaws.com', 'docs')), a domain or collection ARN, and a collection ID.
    """
    text = str(where or "").strip()
    if not text:
        raise ValueError("Pass a domain or collection name, with the index after a slash: 'vectors-prod/docs'")
    if "://" not in text and re.match(r"^[\w.-]+\.(?:amazonaws\.com|on\.aws)(?::\d+)?(?:/|$)", text, re.IGNORECASE):
        text = "https://" + text
    if re.match(r"^https?://", text, re.IGNORECASE):
        parts = urlsplit(text)
        index = unquote(parts.path.strip("/").split("/")[0])
        return f"{parts.scheme.lower()}://{parts.netloc}", (index if index and not index.startswith("_") else None)
    match = _ARN_RE.match(text)
    if match:
        arn = text[: match.start(6)] if match.group(6) is not None else text
        index = (match.group(6) or "").removeprefix("index/").strip("/").split("/")[0]
        return arn.rstrip("/"), index or None
    head, _, tail = text.partition("/")
    return head.strip(), tail.strip().strip("/") or None


def endpoint_service(url: str) -> tuple[str | None, str | None]:
    """
    (SigV4 service, region) of an AWS OpenSearch endpoint: ('es', 'us-east-1') for a domain, ('aoss', ...) for a
    Serverless collection, (None, None) for anything else (an OpenSearch you run yourself).
    """
    host = (urlsplit(url).hostname or "").lower()
    match = _AWS_HOST_RE.search(host)
    if not match:
        return None, None
    if match.group(3):
        return "es", match.group(3)
    return match.group(2), match.group(1)


def vector_norm(vector: Iterable[Any]) -> float | None:
    """
    Euclidean length of a vector; None when it holds something that isn't a number.
    """
    try:
        return math.sqrt(sum(float(x) * float(x) for x in vector))
    except (TypeError, ValueError):
        return None


def vector_summary(vector: Any, shown: int = 3) -> str:
    """
    [0.0123, -0.0441, 0.0087, …] -> '[0.0123, -0.0441, 0.0087, …] 1,024 dims · length 1.00'.
    """
    if not isinstance(vector, (list, tuple)):
        return "-" if vector is None else _clip(str(vector), 40)
    head = ", ".join(f"{x:.4g}" if isinstance(x, (int, float)) else str(x) for x in vector[:shown])
    norm = vector_norm(vector)
    tail = f" · length {norm:.2f}" if norm is not None else ""
    return f"[{head}{', …' if len(vector) > shown else ''}] {len(vector):,} dims{tail}"


def values_at(source: Any, path: str) -> list[Any]:
    """
    Every value at a dotted path in a document, through lists (nested documents): {'chunks': [{'v': 1}, {'v': 2}]}
    and 'chunks.v' -> [1, 2]. A field literally named 'a.b' wins over the nested path.
    """
    if isinstance(source, dict) and path in source:
        return [source[path]]
    head, _, rest = path.partition(".")
    if isinstance(source, list):
        return [v for item in source for v in values_at(item, path)]
    if not isinstance(source, dict) or head not in source or not rest:
        return []
    return values_at(source[head], rest)


def _format_value(value: Any, width: int = 80) -> str:
    """
    Short display text for a field value: strings as they are, numbers with commas, lists of numbers (vectors)
    summarized, other lists and dicts as compact JSON.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}" if abs(value) >= 10000 else str(value)
    if isinstance(value, list) and len(value) > 8 and all(isinstance(x, (int, float)) for x in value[:8]):
        return vector_summary(value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return _clip(text.replace("\r\n", " ↵ ").replace("\n", " ↵ "), width)
