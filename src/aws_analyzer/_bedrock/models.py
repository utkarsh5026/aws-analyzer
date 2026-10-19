"""
The dataclasses Bedrock's answers are read into: a knowledge base's document, a retrieved passage, a
citation and a foundation model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .helpers import source_name


@dataclass
class KBDocument:
    """
    One document (file or record) of a data source, and whether it's searchable.

        uri     s3://... for S3 data sources, the document ID for custom ones
        status  INDEXED, FAILED, PENDING, IN_PROGRESS, IGNORED, PARTIALLY_INDEXED, ...
        reason  why it failed or was ignored
    """

    data_source_id: str
    uri: str
    status: str
    reason: str = ""
    updated: datetime | None = None

    @property
    def name(self) -> str:
        return source_name(self.uri)


@dataclass
class Passage:
    """
    One retrieved chunk of a document, with where it came from.

        rank           1 for the best match
        score          its relevance, only comparable with other scores of the same search
        uri            s3://bucket/key, a web page URL, ... (see location_type)
        location_type  S3, WEB, CONFLUENCE, SALESFORCE, SHAREPOINT, CUSTOM, KENDRA, SQL, ...
        page           its page number in a PDF, when the parser recorded one
        metadata       the author's metadata only (what where= filters on)
        content_type   TEXT, IMAGE, ROW, AUDIO or VIDEO
        row            a ROW result's columns (SQL knowledge bases): column -> value
    """

    rank: int
    text: str
    score: float | None = None
    uri: str = ""
    location_type: str = ""
    page: int | None = None
    chunk_id: str = ""
    data_source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    content_type: str = "TEXT"
    row: dict[str, Any] | None = None

    @property
    def source(self) -> str:
        """
        'refund-policy.pdf p.3'.
        """
        name = source_name(self.uri) or self.uri or "(unknown source)"
        return f"{name} p.{self.page}" if self.page is not None else name

    @property
    def key(self) -> str:
        """
        What identifies this chunk when comparing searches: its chunk ID, or its source and text.
        """
        return self.chunk_id or f"{self.uri}|{self.page}|{self.text[:500]}"


@dataclass
class Citation:
    """
    A span of an answer and the sources behind it: `start` and `end` are character offsets into Answer.text (end
    is exclusive), and `sources` 1-based numbers into Answer.sources.
    """

    start: int
    end: int
    text: str
    sources: list[int] = field(default_factory=list)


@dataclass
class ModelInfo:
    """
    A model ask() can use, and how to call it.

        id         the foundation model ID, e.g. 'anthropic.claude-opus-5'
        invoke_id  what to pass as model=: the model ID, or the inference profile that serves it
        arn        the model's ARN, or the inference profile's when it needs one
        via        'on-demand', 'inference profile' or 'provisioned only'
        price_in   USD per 1M input tokens (None when it isn't in the price table), and price_out per 1M output
        status     ACTIVE or LEGACY
    """

    id: str
    name: str = ""
    provider: str = ""
    invoke_id: str = ""
    arn: str = ""
    via: str = "on-demand"
    price_in: float | None = None
    price_out: float | None = None
    status: str = "ACTIVE"
