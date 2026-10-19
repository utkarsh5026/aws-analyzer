"""
Parsing and formatting: s3:// paths and keys, the units sizes are counted in, and the limits on what a
preview draws.
"""

from __future__ import annotations

import shlex


KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """
    's3://bucket/some/prefix' (or 'bucket/some/prefix') -> ('bucket', 'some/prefix').
    """
    uri = uri.strip()
    for scheme in ("s3://", "s3a://", "s3n://"):
        if uri.lower().startswith(scheme):
            uri = uri[len(scheme) :]
            break
    bucket, _, key = uri.partition("/")
    if not bucket:
        raise ValueError(f"No bucket in S3 URI {uri!r}; expected 's3://bucket/prefix'")
    return bucket, key


def s3_uri(bucket: str, key: str = "") -> str:
    return f"s3://{bucket}/{key}"


def base_prefix(prefix: str) -> str:
    """
    The 'folder' part of a prefix: 'logs/2024-0' -> 'logs/', 'logs/' -> 'logs/'.
    """
    return prefix[: prefix.rfind("/") + 1]


def relative_key(key: str, prefix: str) -> str:
    return key[len(prefix) :] if key.startswith(prefix) else key


_PREVIEW_PAGES = 3  # PDF pages preview() draws

_PREVIEW_PICTURES = 5  # pictures of a Word file preview() shows

_MAX_PICTURES = 20  # pages or pictures one document() report shows, so the notebook stays small


def _count_words(text: str) -> int:
    """
    Words in text; list markers and table separators ('-', '|') don't count.
    """
    return sum(any(ch.isalnum() for ch in word) for word in text.split())


def _restore_note(bucket: str, key: str, storage_class: str, restore: str = "") -> str:
    """
    Why an archived object can't be read, and the command that restores it (shown, never run).
    """
    name, wait = key.rsplit("/", 1)[-1], "3-5 hours" if storage_class == "GLACIER" else "up to 12 hours"
    if 'ongoing-request="true"' in restore:
        return f"{name} is in {storage_class} and its restore is under way; it can be read when that finishes ({wait})."
    return (f"{name} is in {storage_class}, so it can't be read until it's restored, which takes {wait} and costs a "
            f"retrieval fee. To make it readable for 7 days: aws s3api restore-object --bucket {bucket} "
            f"--key {shlex.quote(key)} --restore-request Days=7")
