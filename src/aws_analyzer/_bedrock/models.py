"""
The dataclasses Bedrock's answers are read into: a knowledge base's document, a retrieved passage, a
citation, a foundation model and an inference profile.
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
        bedrock_metadata  Bedrock's own keys, without their x-amz-bedrock-kb- prefix ('chunk-id', 'source-uri',
                       'document-page-number', ...)
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
    bedrock_metadata: dict[str, Any] = field(default_factory=dict)
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


@dataclass
class InferenceProfile:
    """
    A way to call a model that Bedrock offers beside the model's own ID: a cross-region profile AWS defines, which
    sends each request to whichever of its regions has room, or an application profile your account made to track
    one application's cost and usage.

        id         'us.anthropic.claude-opus-5-v1:0', or an application profile's short ID
        invoke_id  what to pass as model=: the ID, or an application profile's ARN
        geography  where a cross-region profile sends requests: 'US', 'Europe', 'Global', ... ('' for yours)
        models     the foundation model IDs it serves
        regions    the regions a request can be sent to, when the profile names them
        price_in   USD per 1M input tokens (None when it isn't in the price table), and price_out per 1M output
        status     ACTIVE, or what Bedrock says instead
    """

    id: str
    name: str = ""
    arn: str = ""
    invoke_id: str = ""
    is_yours: bool = False
    geography: str = ""
    models: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    description: str = ""
    status: str = "ACTIVE"
    price_in: float | None = None
    price_out: float | None = None

    @property
    def kind(self) -> str:
        """
        'yours', 'cross-region · US' or 'cross-region · Global'.
        """
        if self.is_yours:
            return "yours"
        return f"cross-region · {self.geography}" if self.geography else "cross-region"

    @property
    def sends_to(self) -> str:
        """
        'us-east-1, us-west-2', or 'any region' for a global profile that names none.
        """
        if self.regions:
            return ", ".join(self.regions)
        return "any region" if self.geography == "Global" else "-"
