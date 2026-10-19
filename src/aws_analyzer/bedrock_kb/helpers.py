"""
Parsing and formatting: durations and token counts, ARNs, the file types and sizes Bedrock indexes,
and the sync command or call to copy.
"""

from __future__ import annotations

from datetime import timedelta

from .._bedrock.helpers import source_name
from .._bedrock.words import _TERM_RE


def human_duration(delta: timedelta | float | None) -> str:
    """
    timedelta or seconds -> '45s', '3m 20s', '2h 05m'.
    """
    if delta is None:
        return "-"
    seconds = int(delta.total_seconds() if isinstance(delta, timedelta) else delta)
    if seconds < 60:
        return f"{max(seconds, 0)}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _isnt(count: int) -> str:
    return "isn't" if count == 1 else "aren't"


def _arn_region(arn: str) -> str:
    """
    'arn:aws:bedrock:eu-west-1:123456789012:knowledge-base/X' -> 'eu-west-1' ('' for anything else).
    """
    parts = (arn or "").split(":")
    return parts[3] if len(parts) > 5 and parts[0] == "arn" else ""


# The file types Bedrock's parsers read from S3: the default parser takes the documents, and a foundation model or
# Data Automation parser (or multimodal embeddings) the pictures too. A sync skips other types.
DOCUMENT_TYPES = frozenset({"txt", "md", "markdown", "html", "htm", "doc", "docx", "csv", "xls", "xlsx", "pdf"})

IMAGE_TYPES = frozenset({"png", "jpg", "jpeg"})

MAX_FILE_SIZE = 50 * 1024**2  # the biggest S3 file Bedrock indexes

MAX_METADATA_SIZE = 10 * 1024  # the biggest <file>.metadata.json it reads

CHUNK_LIMIT = 100  # Retrieve returns at most 100 passages, so it shows at most 100 chunks of a file at a time

METADATA_SUFFIX = ".metadata.json"

_PLAIN_TYPES = frozenset({"txt", "md", "markdown", "rst", "log", "text"})  # files whose chunks are their own words

FILE_TEXT_LIMIT = 1024**2  # bytes of a plain text file read to place its chunks in it


def file_type(uri: str | None) -> str:
    """
    The extension a file is read by, lower case: 's3://b/Refund-Policy.PDF' -> 'pdf', 'faq/README' -> ''.
    """
    name = source_name(uri)
    return name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""


def sync_command(kb_id: str, data_source_id: str, region: str = "") -> str:
    """
    The AWS CLI command that syncs one data source. This module never runs it: syncing changes the index.
    """
    where = f" --region {region}" if region else ""
    return f"aws bedrock-agent start-ingestion-job --knowledge-base-id {kb_id} --data-source-id {data_source_id}{where}"


def sync_call(kb_id: str, data_source_id: str, region: str = "") -> str:
    """
    The same sync as a boto3 call to copy into a cell.
    """
    where = f", region_name={region!r}" if region else ""
    return (
        f"boto3.client('bedrock-agent'{where}).start_ingestion_job(knowledgeBaseId={kb_id!r}, "
        f"dataSourceId={data_source_id!r})"
    )


def human_tokens(count: int | None, *, estimate: bool = False) -> str:
    """
    1234 -> '1,234 tokens' ('~1,234 tokens' when it's an estimate).
    """
    if count is None:
        return "-"
    return f"{'~' if estimate else ''}{count:,} token{'' if count == 1 else 's'}"


def _code_terms(question: str) -> list[str]:
    """
    IDs and codes in a question ('E1234', 'SKU-42', 'GDPR', '4412'): exact strings that semantic search, which
    matches meaning, tends to miss.
    """
    codes = []
    for match in _TERM_RE.finditer(question or ""):
        word = match.group(0)
        digits, letters = any(c.isdigit() for c in word), any(c.isalpha() for c in word)
        if (digits and (letters or len(word) >= 4)) or (
            word.isalpha() and word.isupper() and len(word) >= 3
        ):
            codes.append(word)
    return list(dict.fromkeys(codes))
