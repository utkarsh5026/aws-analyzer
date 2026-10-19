"""
What the View's commands and the explorer window share: labels and tones for syncs and documents,
passages as blocks, and short values.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..._bedrock.blocks import _plain_json
from ..._bedrock.files import _browser_type, _link_life
from ..._bedrock.helpers import source_name
from ..._bedrock.markdown import _MARKER_RE
from ..._bedrock.models import Passage
from ..._bedrock.words import best_snippet
from ..._kit.blocks import _Tone
from ..._kit.fmt import _fmt_dt, _plural, human_age
from ..._kit.text import _clip

from ..helpers import human_duration
from ..models import Answer, DataSourceInfo, IngestionJob, KnowledgeBaseInfo
from ..analysis.common import _reasons_text
from ..blocks import _Passage, _meta_label


_JOB_STATES = {
    "COMPLETE": "done",
    "FAILED": "FAILED",
    "IN_PROGRESS": "running",
    "STARTING": "starting",
    "STOPPED": "stopped",
    "STOPPING": "stopping",
}

_DOC_STATES = {
    "INDEXED": "Indexed",
    "FAILED": "Failed",
    "PARTIALLY_INDEXED": "Partly indexed",
    "PENDING": "Pending",
    "STARTING": "Starting",
    "IN_PROGRESS": "Indexing",
    "IGNORED": "Ignored",
    "NOT_FOUND": "Not found",
    "METADATA_PARTIALLY_INDEXED": "Metadata partly indexed",
    "METADATA_UPDATE_FAILED": "Metadata failed",
    "DELETING": "Deleting",
    "DELETE_IN_PROGRESS": "Deleting",
}

_DOC_ORDER = [
    "FAILED",
    "METADATA_UPDATE_FAILED",
    "PARTIALLY_INDEXED",
    "METADATA_PARTIALLY_INDEXED",
    "IGNORED",
    "NOT_FOUND",
    "PENDING",
    "STARTING",
    "IN_PROGRESS",
    "DELETING",
    "DELETE_IN_PROGRESS",
    "INDEXED",
]

_KB_TYPES = {
    "VECTOR": "vector search",
    "KENDRA": "Kendra index",
    "SQL": "SQL on Redshift",
    "MANAGED": "managed",
}

_DELETION = {"DELETE": "chunks deleted too", "RETAIN": "chunks kept (RETAIN)"}


def _sync_label(job: IngestionJob | None) -> str:
    """
    'done 3d ago', 'FAILED 2h ago', 'done 1d ago, 2 docs failed', 'never'.
    """
    if job is None:
        return "never"
    text = f"{_JOB_STATES.get(job.status, job.status.lower())} {human_age(job.started)}"
    return text + (f", {_plural(job.failed, 'doc')} failed" if job.failed else "")


def _status_tone(status: str | None) -> str:
    """
    A knowledge base's or data source's status at a glance: FAILED is bad, anything but ACTIVE / AVAILABLE a
    warning (it's being created, updated or deleted).
    """
    return (
        "bad"
        if status == "FAILED"
        else ""
        if status in ("ACTIVE", "AVAILABLE", None, "")
        else "warn"
    )


def _sync_tone(job: IngestionJob | None) -> str:
    """
    How a sync reads at a glance: a failed one is bad; never synced, stopped or with failed documents, a warning.
    """
    if job is not None and job.status == "FAILED":
        return "bad"
    return "warn" if job is None or job.failed or job.status == "STOPPED" else ""


def _job_row(job: IngestionJob, names: dict[str, str]) -> list[Any]:
    return [
        names.get(job.data_source_id) or job.data_source_id,
        _fmt_dt(job.started),
        human_duration(job.duration),
        _Tone(
            _JOB_STATES.get(job.status, job.status.lower()),
            _sync_tone(job) if job.status != "COMPLETE" else "",
        ),
        f"{job.scanned:,}",
        f"{job.new:,}",
        f"{job.modified:,}",
        f"{job.deleted:,}",
        _Tone(f"{job.failed:,}", "warn" if job.failed else ""),
        _reasons_text(job.failure_reasons, 1) if job.failure_reasons else "",
    ]


def _passage_blocks(
    passages: list[Passage],
    terms: list[str],
    width: int = 320,
    sources: dict[str, str] | None = None,
    link: Callable[[Passage], str] | None = None,
) -> list[_Passage]:
    """
    sources: {data source ID: name}, to say which data source each passage came from ({}: don't). link: the
    passage -> the link that opens its file ('' for none).
    """
    top = max((p.score for p in passages if p.score is not None), default=None)
    sources = sources or {}
    return [
        _Passage(
            p.rank,
            None if p.score is None or not top else p.score / top,
            source_name(p.uri) or p.uri or "?",
            " · ".join(
                filter(
                    None,
                    [
                        f"p.{p.page}" if p.page is not None else "",
                        f"from {sources[p.data_source_id]}"
                        if p.data_source_id in sources
                        else "",
                    ],
                )
            ),
            best_snippet(p.text, terms, width),
            terms,
            p.score,
            _meta_label(p.metadata),
            link(p) if link else "",
        )
        for p in passages
    ]


def _open_label(p: Passage, url: str, expires: int = 3600) -> str:
    """
    'Open refund-policy.pdf at page 3 (link valid for 1 hour)', or 'Download ...' for a file a browser saves
    rather than shows.
    """
    name = source_name(p.uri) or p.uri
    if not p.uri.startswith("s3://"):
        return f"Open {name}"
    verb = "Open" if _browser_type(p.uri) else "Download"
    page = f" at page {p.page}" if "#page=" in url else ""
    return f"{verb} {name}{page} (link valid for {_link_life(expires)})"


def _strip_markers(text: str) -> str:
    return re.sub(r"\s*" + _MARKER_RE.pattern, "", text)


def _turns(question: str, answer: Answer) -> list[dict[str, Any]]:
    """
    The Converse messages one question and its answer add to a conversation (the sources and markers left out:
    the next question gets its own numbered sources).
    """
    return [
        {"role": "user", "content": [{"text": question}]},
        {
            "role": "assistant",
            "content": [{"text": _strip_markers(answer.text).strip() or "(no answer)"}],
        },
    ]


def _model_label(model: str) -> str:
    """
    'amazon.titan-embed-text-v2:0' stays as is; '' -> '-'.
    """
    return model or "-"


def _section(info: KnowledgeBaseInfo | DataSourceInfo, section: str, text: str) -> str:
    return f"? ({info.errors[section]})" if section in info.errors else text


def _short_value(value: Any, width: int = 48) -> str:
    """
    A metadata value on one short line, typed: "billing" (text), 2024 (a number), ["a", "b"]; '-' and '?' (not
    there, not known) as they are.
    """
    if value in ("-", "?"):
        return str(value)
    return _clip(json.dumps(_plain_json(value), ensure_ascii=False), width)
