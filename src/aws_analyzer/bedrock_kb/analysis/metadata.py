"""
A file's .metadata.json read and checked: its attributes, their kinds, and what's wrong.
"""

from __future__ import annotations

import json
from typing import Any

from ..._bedrock.helpers import _BEDROCK_META_PREFIX
from ..._kit.fmt import human_size

from ..helpers import MAX_METADATA_SIZE
from ..models import MetadataFile
from .common import _METADATA_EXAMPLE


# ----------------------------------------------------------- metadata files

_METADATA_TYPES = {"STRING": "stringValue", "NUMBER": "numberValue", "BOOLEAN": "booleanValue",
                   "STRING_LIST": "stringListValue"}


def _metadata_kind(value: Any) -> str | None:
    """
    The Bedrock type of a plain metadata value: STRING, NUMBER, BOOLEAN or STRING_LIST; None for anything else.
    """
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        return "NUMBER"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "STRING_LIST"
    return None


def _json_kind(value: Any) -> str:
    return {dict: "an object", list: "a list", str: "text", bool: "true / false", type(None): "null"}.get(
        type(value), "a number" if isinstance(value, (int, float)) else type(value).__name__)


def parse_metadata_file(text: str | bytes, uri: str = "", size: int | None = None) -> MetadataFile:
    """
    A <file>.metadata.json's content -> MetadataFile: its attributes (plain values, or Bedrock's typed form with
    includeForEmbedding), and what's wrong with it in plain English. Bedrock skips a metadata file it can't read, and
    then where= filters match nothing from the file.
    """
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    meta = MetadataFile(uri, found=True, size=len(raw) if size is None else size)
    meta.text = raw[:65536].decode("utf-8", "replace")
    if meta.size is not None and meta.size > MAX_METADATA_SIZE:
        meta.problems.append(f"It's {human_size(meta.size)}, over the {human_size(MAX_METADATA_SIZE)} Bedrock reads "
                             "from a metadata file: keep only the attributes you filter on.")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        meta.problems.append("It isn't UTF-8 text, so Bedrock can't read it.")
        return meta
    except ValueError as exc:
        detail = f"{exc.msg} at line {exc.lineno}, column {exc.colno}" if isinstance(exc, json.JSONDecodeError) else exc
        meta.problems.append(f"It isn't valid JSON ({detail}), so Bedrock can't read it: the file's chunks get no "
                             "metadata, and where= filters can't match them.")
        return meta
    if not isinstance(doc, dict):
        meta.problems.append(f"It holds {_json_kind(doc)}; Bedrock reads an object like {_METADATA_EXAMPLE}.")
        return meta
    attributes = doc.get("metadataAttributes")
    if attributes is None:
        others = [k for k in doc if k != "documentStructureConfiguration"]
        meta.problems.append("It has no metadataAttributes, so Bedrock finds no metadata in it"
                             + (f": put {', '.join(map(repr, others[:4]))} under metadataAttributes, like "
                                f"{_METADATA_EXAMPLE}." if others else "."))
        return meta
    if not isinstance(attributes, dict):
        meta.problems.append(f"metadataAttributes holds {_json_kind(attributes)}; it should be an object of names and "
                             f"values, like {_METADATA_EXAMPLE}.")
        return meta
    for name, value in attributes.items():
        if name.lower().startswith(_BEDROCK_META_PREFIX) or name.startswith("AMAZON_BEDROCK_"):
            meta.problems.append(f"{name!r} is a name Bedrock keeps for its own metadata: rename it.")
            continue
        if isinstance(value, dict) and "value" in value:  # the typed form: {"value": {"type": ..., ...}, ...}
            inner = value.get("value") if isinstance(value.get("value"), dict) else {}
            kind = str(inner.get("type", "")).upper()
            key = _METADATA_TYPES.get(kind)
            if key is None:
                meta.problems.append(f"{name!r} has type {kind or 'none'!r}; Bedrock takes STRING, NUMBER, BOOLEAN or "
                                     "STRING_LIST.")
            elif key not in inner:
                meta.problems.append(f"{name!r} is a {kind} without its {key}.")
            else:
                meta.attributes[name], meta.types[name] = inner[key], kind
                if value.get("includeForEmbedding"):
                    meta.embedded.append(name)
            continue
        kind = _metadata_kind(value)
        if kind is None:
            meta.problems.append(f"{name!r} holds {_json_kind(value)}: Bedrock takes text, a number, true / false or a "
                                 "list of text.")
            continue
        meta.attributes[name], meta.types[name] = value, kind
    return meta
