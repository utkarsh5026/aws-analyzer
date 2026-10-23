"""
What the S3 toolkit's save_s3_files needs to copy files from S3 into the agent's Python workspace, under
s3/<bucket>/<key>, so run_python can read them whole: which of a listing's files to read (plan_saves), reading them
(read_files), the text of a PDF, Word or PowerPoint file (document_text, with the s3 package's readers: a PDF needs
pypdf), saved beside it as <key>.txt so the code doesn't need those readers, and what the model reads of a save
(summarize_saved). plan_saves(), document_text() and summarize_saved() are pure.

A save takes at most max_files files (up to _MOST_FILES) that fit in what's left of the workspace, read _SAVE_WORKERS
at a time. Files in Glacier Flexible Retrieval or Deep Archive (_ARCHIVED) can't be read until they're restored, so
they're skipped, and so are folder markers (keys ending in '/') and keys that can't be a path in the workspace. The
model reads at most _LISTED files of each list.
"""

from __future__ import annotations

import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, human_size
from ..workspace import clean_path

if TYPE_CHECKING:
    from ...s3 import ObjectInfo

SAVE_READS = {"save_s3_files": ("s3:ListBucket", "s3:GetObject (whole files, into the Python workspace)")}
MOST_FILES = 500
DEFAULT_FILES = 50
_ARCHIVED = frozenset({"GLACIER", "DEEP_ARCHIVE"})
_DOCUMENT_KINDS = frozenset({"pdf", "docx", "pptx"})
_DOCUMENT_ERRORS = (ValueError, ImportError, OSError, EOFError, KeyError, IndexError, zipfile.BadZipFile)
_SAVE_WORKERS = 8
_LISTED = 20
_WHY_CHARACTERS = 200


@dataclass
class SavePlan:
    """
    Which of a listing's files a save reads (`chosen`, in key order) and why it leaves out the others (`skipped`:
    'key: why'), with the bytes the chosen ones take.
    """

    chosen: list[ObjectInfo] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    size: int = 0


@dataclass
class ReadFiles:
    """
    What a save read: each file's bytes by key, the documents' text by '<key>.txt', and 'key: why' for each file it
    couldn't read (or whose text it couldn't).
    """

    files: dict[str, bytes] = field(default_factory=dict)
    texts: dict[str, bytes] = field(default_factory=dict)
    unread: list[str] = field(default_factory=list)


def workspace_key(bucket: str, key: str) -> str:
    """
    Where a file from S3 goes in the workspace: ('acme', 'docs/a.pdf') -> 's3/acme/docs/a.pdf'. ValueError for a key
    that can't be a path there ('../x').
    """
    return clean_path(f"s3/{bucket}/{key}")


def _skip_reason(obj: ObjectInfo) -> str:
    """
    Why a listed file can't be saved, or '' when it can.
    """
    if obj.key.endswith("/"):
        return "a folder marker"
    if obj.storage_class in _ARCHIVED:
        return f"in {obj.storage_class}: restore it before anything can read it"
    try:
        workspace_key(obj.bucket, obj.key)
    except ValueError:
        return "its key can't be a path in the workspace"
    return ""


def plan_saves(found: list[ObjectInfo], *, room: int, max_files: int) -> SavePlan:
    """
    The files of `found` a save reads, in key order: at most `max_files`, and only as many as fit in `room` bytes,
    each other one with why it's left out.
    """
    plan = SavePlan()
    for obj in sorted(found, key=lambda o: o.key):
        reason = _skip_reason(obj)
        if not reason and len(plan.chosen) >= max_files:
            reason = f"past the {_plural(max_files, 'file')} asked for"
        if not reason and plan.size + obj.size > room:
            reason = f"{human_size(obj.size)}: past what's left of the workspace"
        if reason:
            plan.skipped.append(f"{obj.key}: {reason}")
            continue
        plan.chosen.append(obj)
        plan.size += obj.size
    return plan


def _document_errors() -> tuple[type[BaseException], ...]:
    """
    What reading a broken document can raise: the usual decoding errors, and pypdf's once it's loaded.
    """
    pypdf_errors = sys.modules.get("pypdf.errors")
    extra = (pypdf_errors.PyPdfError,) if pypdf_errors is not None else ()
    return (*_DOCUMENT_ERRORS, *extra)


def document_text(s3: Any, key: str, data: bytes) -> tuple[str | None, str | None]:
    """
    (the text of a PDF, Word .docx or PowerPoint .pptx file, None) from its bytes, read with the s3 package `s3`'s
    readers; (None, why) when it couldn't be read; (None, None) for any other file.
    """
    kind, codec = s3.detect_format(key)
    if kind not in _DOCUMENT_KINDS or codec:
        return None, None
    readers = {"pdf": s3.parse_pdf, "docx": s3.parse_docx, "pptx": s3.parse_pptx}
    try:
        return readers[kind](io.BytesIO(data), key).text, None
    except _document_errors() as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:_WHY_CHARACTERS]}"


def read_files(core: Any, s3: Any, chosen: list[ObjectInfo]) -> ReadFiles:
    """
    The chosen files read through `core` (an S3Analyzer), _SAVE_WORKERS at a time on its client (boto3's clients are
    thread-safe), with the documents' text read from their bytes.
    """

    def read(obj: ObjectInfo) -> tuple[ObjectInfo, bytes | None, str]:
        try:
            return obj, core.read_bytes(obj.uri), ""
        except (ClientError, BotoCoreError) as exc:
            return obj, None, _error_code(exc)

    with ThreadPoolExecutor(max_workers=_SAVE_WORKERS) as pool:
        results = list(pool.map(read, chosen))
    out = ReadFiles()
    for obj, data, error in results:
        if data is None:
            out.unread.append(f"{obj.key}: {error}")
            continue
        out.files[obj.key] = data
        text, why = document_text(s3, obj.key, data)
        if text is not None:
            out.texts[f"{obj.key}.txt"] = text.encode()
        elif why:
            out.unread.append(f"{obj.key}: saved, but its text couldn't be read ({why})")
    return out


def summarize_saved(saved: dict[str, Any], plan: SavePlan, read: ReadFiles, *, location: str,
                    found: int) -> dict[str, Any]:
    """
    save_s3_files()'s answer from the workspace's save (`saved`: its folder, files and size), the plan and what was
    read: the documents saved as text, what was skipped and why, and the report's one line.
    """
    out: dict[str, Any] = {"location": location, **saved, "files_found": found}
    if read.texts:
        out["documents_as_text"] = sorted(read.texts)[:_LISTED]
    skipped = plan.skipped + read.unread
    if skipped:
        out["skipped"] = skipped[:_LISTED]
        out["skipped_count"] = len(skipped)
    if not found:
        out["note"] = f"No file under {location} matched."
    out["_summary"] = (f"saved {_plural(len(read.files), 'file')} ({saved['size']})"
                       + (f" and the text of {_plural(len(read.texts), 'document')}" if read.texts else "")
                       + (f"; {len(skipped)} skipped" if skipped else ""))
    return out
