"""
What Bedrock returns, read into models: documents, retrieved passages, the span of the answer a
citation covers, and whether a passage is the one a test question expects.
"""

from __future__ import annotations

from typing import Any

from .helpers import source_name, split_metadata
from .models import KBDocument, Passage


def parse_document(desc: dict[str, Any]) -> KBDocument:
    """
    One ListKnowledgeBaseDocuments 'documentDetails' entry -> KBDocument.
    """
    ident = desc.get("identifier") or {}
    uri = (
        (ident.get("s3") or {}).get("uri")
        or (ident.get("custom") or {}).get("id")
        or ""
    )
    return KBDocument(
        data_source_id=desc.get("dataSourceId", ""),
        uri=uri,
        status=desc.get("status", ""),
        reason=desc.get("statusReason") or "",
        updated=desc.get("updatedAt"),
    )


_LOCATIONS = {
    "S3": ("s3Location", "uri"),
    "WEB": ("webLocation", "url"),
    "CONFLUENCE": ("confluenceLocation", "url"),
    "SALESFORCE": ("salesforceLocation", "url"),
    "SHAREPOINT": ("sharePointLocation", "url"),
    "CUSTOM": ("customDocumentLocation", "id"),
    "KENDRA": ("kendraDocumentLocation", "uri"),
    "SQL": ("sqlLocation", "query"),
    "ONEDRIVE": ("oneDriveLocation", "url"),
    "GOOGLEDRIVE": ("googleDriveLocation", "url"),
}


def _page_number(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_passage(ref: dict[str, Any], rank: int) -> Passage:
    """
    One Retrieve result (or one RetrieveAndGenerate retrievedReference) -> Passage.
    """
    content = ref.get("content") or {}
    location = ref.get("location") or {}
    kind = location.get("type", "")
    section, key = _LOCATIONS.get(kind, ("", ""))
    uri = (location.get(section) or {}).get(key, "") if section else ""
    system, user = split_metadata(ref.get("metadata"))
    content_type = content.get("type") or "TEXT"
    text = content.get("text") or ""
    row = None
    if content.get("row"):
        row = {
            column.get("columnName", ""): column.get("columnValue")
            for column in content["row"]
        }
        text = text or ", ".join(f"{name}: {value}" for name, value in row.items())
    elif content.get("audio"):
        text = text or content["audio"].get("transcription") or "(audio)"
    elif content.get("video"):
        text = text or content["video"].get("summary") or "(video)"
    elif content_type == "IMAGE":
        text = text or "(an image)"
    return Passage(
        rank=rank,
        text=text,
        score=ref.get("score"),
        uri=uri or str(system.get("source-uri") or ""),
        location_type=kind,
        page=_page_number(system.get("document-page-number")),
        chunk_id=str(system.get("chunk-id") or ""),
        data_source_id=str(system.get("data-source-id") or ""),
        metadata=user,
        content_type=content_type,
        row=row,
    )


def parse_retrieve(resp: dict[str, Any]) -> list[Passage]:
    """
    A Retrieve response -> Passages, best first.
    """
    return [
        parse_passage(ref, i)
        for i, ref in enumerate(resp.get("retrievalResults") or [], 1)
    ]


def _span(text: str, part: dict[str, Any]) -> tuple[int, int]:
    """
    Where a RetrieveAndGenerate citation's text sits in the answer. The API doesn't say whether span.end is
    inclusive, so the offsets are checked against the text itself.
    """
    piece = part.get("text") or ""
    span = part.get("span") or {}
    start, end = span.get("start"), span.get("end")
    if start is not None and end is not None:
        for stop in (end, end + 1):
            if piece and text[start:stop] == piece:
                return start, stop
    if piece:
        found = text.find(piece, max(0, (start or 0) - 10))
        found = text.find(piece) if found == -1 else found
        if found != -1:
            return found, found + len(piece)
    if start is None or end is None:
        return 0, 0
    return max(0, start), min(len(text), end + 1)


def match_expected(passage: Passage, expected: Any) -> bool:
    """
    Whether a passage is the one a test question expects: `expected` is a case-insensitive piece of its URI, its file
    name or its text (a list means any of them).
    """
    wanted = [expected] if isinstance(expected, str) else list(expected or [])
    haystack = f"{passage.uri}\n{source_name(passage.uri)}\n{passage.text}".lower()
    return any(str(w).strip().lower() in haystack for w in wanted if str(w).strip())
