"""
opensearch.py - self-contained Amazon OpenSearch toolkit for vector (k-NN) indexes, for SageMaker / Jupyter notebooks.

Copy this one file into a notebook cell (or upload it next to your notebook and
``import opensearch``). Nothing else from this repo is needed.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker. No OpenSearch client library is
needed: requests to a domain or collection are signed with your AWS credentials
by botocore, the way boto3 signs its own.

The file has two layers:

    OpenSearchAnalyzer  Pure logic. Talks to AWS and returns plain Python data
                        (dataclasses, dicts, lists, DataFrames). Never prints.
    OpenSearchView      Notebook UI. Calls OpenSearchAnalyzer and renders readable
                        cards and tables (HTML in Jupyter, plain text in a terminal).

It covers OpenSearch Service domains, OpenSearch Serverless collections and any
OpenSearch you pass by URL. Nothing in this file writes: it only sends GET requests,
and POSTs to _search and _count (searches with a body), and refuses anything else.

Quick start
-----------
    ui = OpenSearchView()                             # or OpenSearchView(OpenSearchAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.overview()                                     # every domain and Serverless collection: size, cost, warnings
    ui.indexes("vectors-prod")                        # its indexes: documents, size, vector fields, memory, warnings
    ui.index_info("vectors-prod/docs")                # one vector index in plain English, and the query to copy
    ui.use("vectors-prod/docs")                       # later commands use this index
    ui.sample()                                       # a few documents, and how healthy their vectors are
    ui.search("how do refunds work?")                 # nearest neighbours of a question (embedded with Bedrock)
    ui.search(like="doc-17")                          # the documents closest to doc-17 (no model needed)
    ui.search(vector=my_model.encode("refunds"), where={"lang": "en", "year": (">=", 2024)})

    aos = ui.core                                     # same analyzer, raw data
    info = aos.index("vectors-prod", "docs")          # IndexInfo: info.vectors[0].dimension, .engine, ...
    hits = aos.search("vectors-prod", "docs", vector=v, k=20).to_df()
"""

from __future__ import annotations

import base64
import difflib
import fnmatch
import functools
import hashlib
import importlib
import inspect
import json
import math
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Generator, Iterable
from urllib.parse import quote, unquote, urlencode, urlsplit

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError
from botocore.httpsession import URLLib3Session
from botocore.utils import get_environ_proxies

from ._kit.deps import _in_notebook, _require
from ._kit.errors import _Hint, _error_code, _error_name, _why
from ._kit.fmt import (
    HOURS_PER_MONTH, _as_int, _count, _duration, _fmt_dt, _plural, _total, _utcnow, human_age, human_money,
    human_size,
)
from ._kit.text import _clip, _esc, _pad, _text_bar, _width

# =============================================================================
# 1. Helpers: parsing and formatting
# =============================================================================

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4

# Instance type -> (USD per hour, vCPUs, memory in GiB). us-east-1 on-demand list prices for OpenSearch Service, read
# from the AWS Price List API (the AmazonES offer file) on 2026-10-05. Data nodes, dedicated master nodes and
# UltraWarm nodes are all billed per instance-hour at these prices. Other regions differ; pass
# OpenSearchAnalyzer(prices={"r6g.large.search": 0.20}) to use your own.
INSTANCE_TYPES: dict[str, tuple[float, int, float]] = {
    # t: small, burstable (development and testing)
    "t2.micro.search": (0.018, 1, 1),
    "t2.small.search": (0.036, 1, 2),
    "t2.medium.search": (0.073, 2, 4),
    "t3.small.search": (0.036, 2, 2),
    "t3.medium.search": (0.073, 2, 4),
    # m: general purpose
    "m5.large.search": (0.142, 2, 8),
    "m5.xlarge.search": (0.283, 4, 16),
    "m5.2xlarge.search": (0.566, 8, 32),
    "m5.4xlarge.search": (1.133, 16, 64),
    "m5.12xlarge.search": (3.398, 48, 192),
    "m6g.large.search": (0.128, 2, 8),
    "m6g.xlarge.search": (0.256, 4, 16),
    "m6g.2xlarge.search": (0.511, 8, 32),
    "m6g.4xlarge.search": (1.023, 16, 64),
    "m6g.8xlarge.search": (2.045, 32, 128),
    "m6g.12xlarge.search": (3.068, 48, 192),
    "m7g.medium.search": (0.068, 1, 4),
    "m7g.large.search": (0.135, 2, 8),
    "m7g.xlarge.search": (0.271, 4, 16),
    "m7g.2xlarge.search": (0.542, 8, 32),
    "m7g.4xlarge.search": (1.084, 16, 64),
    "m7g.8xlarge.search": (2.167, 32, 128),
    "m7g.12xlarge.search": (3.251, 48, 192),
    "m7g.16xlarge.search": (4.335, 64, 256),
    "m7i.large.search": (0.161, 2, 8),
    "m7i.xlarge.search": (0.323, 4, 16),
    "m7i.2xlarge.search": (0.645, 8, 32),
    "m7i.4xlarge.search": (1.29, 16, 64),
    "m7i.8xlarge.search": (2.58, 32, 128),
    "m7i.12xlarge.search": (3.871, 48, 192),
    "m7i.16xlarge.search": (5.161, 64, 256),
    "m8g.medium.search": (0.075, 1, 4),
    "m8g.large.search": (0.15, 2, 8),
    "m8g.xlarge.search": (0.299, 4, 16),
    "m8g.2xlarge.search": (0.597, 8, 32),
    "m8g.4xlarge.search": (1.193, 16, 64),
    "m8g.8xlarge.search": (2.385, 32, 128),
    "m8g.12xlarge.search": (3.577, 48, 192),
    "m8g.16xlarge.search": (4.769, 64, 256),
    # c: compute optimized
    "c5.large.search": (0.125, 2, 4),
    "c5.xlarge.search": (0.251, 4, 8),
    "c5.2xlarge.search": (0.502, 8, 16),
    "c5.4xlarge.search": (1.003, 16, 32),
    "c5.9xlarge.search": (2.257, 36, 72),
    "c5.18xlarge.search": (4.514, 72, 144),
    "c6g.large.search": (0.113, 2, 4),
    "c6g.xlarge.search": (0.226, 4, 8),
    "c6g.2xlarge.search": (0.452, 8, 16),
    "c6g.4xlarge.search": (0.903, 16, 32),
    "c6g.8xlarge.search": (1.806, 32, 64),
    "c6g.12xlarge.search": (2.709, 48, 96),
    "c7g.large.search": (0.12, 2, 4),
    "c7g.xlarge.search": (0.241, 4, 8),
    "c7g.2xlarge.search": (0.481, 8, 16),
    "c7g.4xlarge.search": (0.963, 16, 32),
    "c7g.8xlarge.search": (1.926, 32, 64),
    "c7g.12xlarge.search": (2.888, 48, 96),
    "c7g.16xlarge.search": (3.851, 64, 128),
    "c7i.large.search": (0.143, 2, 4),
    "c7i.xlarge.search": (0.286, 4, 8),
    "c7i.2xlarge.search": (0.571, 8, 16),
    "c7i.4xlarge.search": (1.142, 16, 32),
    "c7i.8xlarge.search": (2.285, 32, 64),
    "c7i.12xlarge.search": (3.427, 48, 96),
    "c7i.16xlarge.search": (4.57, 64, 128),
    "c8g.large.search": (0.133, 2, 4),
    "c8g.xlarge.search": (0.265, 4, 8),
    "c8g.2xlarge.search": (0.53, 8, 16),
    "c8g.4xlarge.search": (1.06, 16, 32),
    "c8g.8xlarge.search": (2.119, 32, 64),
    "c8g.12xlarge.search": (3.178, 48, 96),
    "c8g.16xlarge.search": (4.237, 64, 128),
    # r: memory optimized, the usual choice for vector search (k-NN graphs live in memory)
    "r5.large.search": (0.186, 2, 16),
    "r5.xlarge.search": (0.372, 4, 32),
    "r5.2xlarge.search": (0.743, 8, 64),
    "r5.4xlarge.search": (1.487, 16, 128),
    "r5.12xlarge.search": (4.46, 48, 384),
    "r6g.large.search": (0.167, 2, 16),
    "r6g.xlarge.search": (0.335, 4, 32),
    "r6g.2xlarge.search": (0.669, 8, 64),
    "r6g.4xlarge.search": (1.339, 16, 128),
    "r6g.8xlarge.search": (2.677, 32, 256),
    "r6g.12xlarge.search": (4.016, 48, 384),
    "r6gd.large.search": (0.191, 2, 16),
    "r6gd.xlarge.search": (0.382, 4, 32),
    "r6gd.2xlarge.search": (0.765, 8, 64),
    "r6gd.4xlarge.search": (1.53, 16, 128),
    "r6gd.8xlarge.search": (3.06, 32, 256),
    "r6gd.12xlarge.search": (4.59, 48, 384),
    "r6gd.16xlarge.search": (6.119, 64, 512),
    "r7g.medium.search": (0.089, 1, 8),
    "r7g.large.search": (0.178, 2, 16),
    "r7g.xlarge.search": (0.356, 4, 32),
    "r7g.2xlarge.search": (0.711, 8, 64),
    "r7g.4xlarge.search": (1.422, 16, 128),
    "r7g.8xlarge.search": (2.845, 32, 256),
    "r7g.12xlarge.search": (4.267, 48, 384),
    "r7g.16xlarge.search": (5.689, 64, 512),
    "r7gd.medium.search": (0.113, 1, 8),
    "r7gd.large.search": (0.226, 2, 16),
    "r7gd.xlarge.search": (0.452, 4, 32),
    "r7gd.2xlarge.search": (0.904, 8, 64),
    "r7gd.4xlarge.search": (1.807, 16, 128),
    "r7gd.8xlarge.search": (3.614, 32, 256),
    "r7gd.12xlarge.search": (5.421, 48, 384),
    "r7gd.16xlarge.search": (7.229, 64, 512),
    "r7i.large.search": (0.212, 2, 16),
    "r7i.xlarge.search": (0.423, 4, 32),
    "r7i.2xlarge.search": (0.847, 8, 64),
    "r7i.4xlarge.search": (1.693, 16, 128),
    "r7i.8xlarge.search": (3.387, 32, 256),
    "r7i.12xlarge.search": (5.08, 48, 384),
    "r7i.16xlarge.search": (6.774, 64, 512),
    "r8g.medium.search": (0.098, 1, 8),
    "r8g.large.search": (0.196, 2, 16),
    "r8g.xlarge.search": (0.392, 4, 32),
    "r8g.2xlarge.search": (0.783, 8, 64),
    "r8g.4xlarge.search": (1.565, 16, 128),
    "r8g.8xlarge.search": (3.13, 32, 256),
    "r8g.12xlarge.search": (4.694, 48, 384),
    "r8g.16xlarge.search": (6.259, 64, 512),
    "r8gd.medium.search": (0.122, 1, 8),
    "r8gd.large.search": (0.244, 2, 16),
    "r8gd.xlarge.search": (0.488, 4, 32),
    "r8gd.2xlarge.search": (0.976, 8, 64),
    "r8gd.4xlarge.search": (1.952, 16, 128),
    "r8gd.8xlarge.search": (3.904, 32, 256),
    "r8gd.12xlarge.search": (5.855, 48, 384),
    "r8gd.16xlarge.search": (7.807, 64, 512),
    # i / im: storage optimized (local NVMe)
    "i3.large.search": (0.25, 2, 15.25),
    "i3.xlarge.search": (0.499, 4, 30.5),
    "i3.2xlarge.search": (0.998, 8, 61),
    "i3.4xlarge.search": (1.997, 16, 122),
    "i3.8xlarge.search": (3.994, 32, 244),
    "i3.16xlarge.search": (7.987, 64, 488),
    "i4g.large.search": (0.247, 2, 16),
    "i4g.xlarge.search": (0.494, 4, 32),
    "i4g.2xlarge.search": (0.988, 8, 64),
    "i4g.4xlarge.search": (1.977, 16, 128),
    "i4g.8xlarge.search": (3.954, 32, 256),
    "i4g.16xlarge.search": (7.907, 64, 512),
    "i4i.large.search": (0.275, 2, 16),
    "i4i.xlarge.search": (0.549, 4, 32),
    "i4i.2xlarge.search": (1.098, 8, 64),
    "i4i.4xlarge.search": (2.197, 16, 128),
    "i4i.8xlarge.search": (4.394, 32, 256),
    "i4i.12xlarge.search": (6.589, 48, 384),
    "i4i.16xlarge.search": (8.786, 64, 512),
    "i4i.24xlarge.search": (13.179, 96, 768),
    "i4i.32xlarge.search": (17.572, 128, 1024),
    "im4gn.large.search": (0.273, 2, 8),
    "im4gn.xlarge.search": (0.546, 4, 16),
    "im4gn.2xlarge.search": (1.091, 8, 32),
    "im4gn.4xlarge.search": (2.183, 16, 64),
    "im4gn.8xlarge.search": (4.366, 32, 128),
    "im4gn.16xlarge.search": (8.731, 64, 256),
    # or / om / oi: OpenSearch optimized (indexes kept in S3)
    "or1.medium.search": (0.105, 1, 8),
    "or1.large.search": (0.209, 2, 16),
    "or1.xlarge.search": (0.419, 4, 32),
    "or1.2xlarge.search": (0.836, 8, 64),
    "or1.4xlarge.search": (1.674, 16, 128),
    "or1.8xlarge.search": (3.346, 32, 256),
    "or1.12xlarge.search": (5.02, 48, 384),
    "or1.16xlarge.search": (6.683, 64, 512),
    "or2.medium.search": (0.1, 1, 8),
    "or2.large.search": (0.2, 2, 16),
    "or2.xlarge.search": (0.401, 4, 32),
    "or2.2xlarge.search": (0.8, 8, 64),
    "or2.4xlarge.search": (1.6, 16, 128),
    "or2.8xlarge.search": (3.2, 32, 256),
    "or2.12xlarge.search": (4.8, 48, 384),
    "or2.16xlarge.search": (6.4, 64, 512),
    "om2.large.search": (0.153, 2, 8),
    "om2.xlarge.search": (0.305, 4, 16),
    "om2.2xlarge.search": (0.61, 8, 32),
    "om2.4xlarge.search": (1.221, 16, 64),
    "om2.8xlarge.search": (2.441, 32, 128),
    "om2.12xlarge.search": (3.662, 48, 192),
    "om2.16xlarge.search": (4.883, 64, 256),
    "oi2.large.search": (0.29172, 2, 16),
    "oi2.xlarge.search": (0.58344, 4, 32),
    "oi2.2xlarge.search": (1.16688, 8, 64),
    "oi2.4xlarge.search": (2.33376, 16, 128),
    "oi2.8xlarge.search": (4.66752, 32, 256),
    "oi2.12xlarge.search": (7.00128, 48, 384),
    "oi2.16xlarge.search": (9.33504, 64, 512),
    "oi2.24xlarge.search": (14.0026, 96, 768),
    # UltraWarm
    "ultrawarm1.medium.search": (0.238, 2, 15.25),
    "ultrawarm1.large.search": (2.68, 16, 122),
}

# USD, us-east-1 list prices for OpenSearch Service storage and OpenSearch Serverless, from the same offer file.
OPENSEARCH_PRICES: dict[str, float] = {
    "gp3": 0.122,  # per GB-month of gp3 EBS storage on each data node (IOPS and throughput above its baseline extra)
    "gp2": 0.135,  # per GB-month of gp2 EBS storage
    "io1": 0.169,  # per GB-month of provisioned-IOPS (io1) storage; the IOPS are billed on top
    "standard": 0.067,  # per GB-month of magnetic storage
    "ocu_hour": 0.24,  # Serverless: per OpenSearch Compute Unit hour, indexing and search alike
    "serverless_storage": 0.024,  # Serverless: per GB-month of index data (kept in S3)
    **{name: spec[0] for name, spec in INSTANCE_TYPES.items()},
}
# OCUs a Serverless collection bills even when idle, with standby replicas (redundancy across Availability Zones) on
# and off: half an OCU each for indexing and search, doubled by the standby copies. Collections that share an
# encryption key, a kind (vector search or not) and the standby setting share these OCUs.
SERVERLESS_MIN_OCUS = {True: 2.0, False: 1.0}

# Bedrock embedding models search() can embed a question with: model ID -> (request format, default dimensions,
# dimensions it can return, USD per million input tokens, name). us-east-1 on-demand list prices from the AWS Price
# List API (the AmazonBedrock and AmazonBedrockFoundationModels offer files) on 2026-10-05. Any other model works
# through search(embed=your_function) or search(vector=[...]).
EMBEDDING_MODELS: dict[str, tuple[str, int, tuple[int, ...], float, str]] = {
    "amazon.titan-embed-text-v2:0": ("titan-v2", 1024, (256, 512, 1024), 0.02, "Titan Text Embeddings V2"),
    "amazon.titan-embed-text-v1": ("titan", 1536, (1536,), 0.10, "Titan Text Embeddings G1"),
    "amazon.titan-embed-image-v1": ("titan-image", 1024, (256, 384, 1024), 0.80, "Titan Multimodal Embeddings G1"),
    "cohere.embed-english-v3": ("cohere", 1024, (1024,), 0.10, "Cohere Embed English v3"),
    "cohere.embed-multilingual-v3": ("cohere", 1024, (1024,), 0.10, "Cohere Embed Multilingual v3"),
    "cohere.embed-v4:0": ("cohere-v4", 1536, (256, 512, 1024, 1536), 0.12, "Cohere Embed v4"),
}
_EMBEDDING_ALIASES = {
    "titan": "amazon.titan-embed-text-v2:0",
    "titan-v2": "amazon.titan-embed-text-v2:0",
    "titan-v1": "amazon.titan-embed-text-v1",
    "titan-multimodal": "amazon.titan-embed-image-v1",
    "cohere": "cohere.embed-english-v3",
    "cohere-english": "cohere.embed-english-v3",
    "cohere-multilingual": "cohere.embed-multilingual-v3",
    "cohere-v4": "cohere.embed-v4:0",
}

def _int(value: Any) -> int | None:
    """'1,024' / '1024' / 1024.0 -> 1024; None for missing or unreadable values (the _cat API answers in strings)."""
    try:
        return None if value in (None, "") else int(float(str(value).replace(",", "")))
    except ValueError:
        return None


_CAT_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?b)?\s*$", re.IGNORECASE)


def _bytes(value: Any) -> int | None:
    """A size from the _cat API -> bytes: '123456' (bytes=b) or OpenSearch's own '1.2gb'. None when missing."""
    match = _CAT_SIZE_RE.match(str(value)) if value not in (None, "") else None
    if not match:
        return None
    unit = (match.group(2) or "b").lower()
    return int(float(match.group(1)) * {"b": 1, "kb": KB, "mb": MB, "gb": GB, "tb": TB, "pb": TB * 1024}[unit])


def _epoch_ms(value: Any) -> datetime | None:
    """Milliseconds since 1970 (Serverless dates, index.creation_date) -> an aware datetime."""
    number = _int(value)
    return None if number is None else datetime.fromtimestamp(number / 1000, timezone.utc)


def parse_engine_version(text: str | None) -> tuple[str, str]:
    """'OpenSearch_2.17' -> ('OpenSearch', '2.17'); 'Elasticsearch_7.10' -> ('Elasticsearch', '7.10')."""
    engine, _, version = (text or "").partition("_")
    if not version:
        return ("OpenSearch", engine) if re.match(r"^\d", engine) else (engine or "OpenSearch", "")
    return engine, version


def version_at_least(version: str | None, wanted: tuple[int, ...]) -> bool | None:
    """'2.17' >= (2, 13) -> True. None when the version is unknown, so callers can stay quiet."""
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
    """Where an index lives -> (domain, collection or URL; index or None).

    'vectors-prod/docs' -> ('vectors-prod', 'docs'), 'vectors-prod' -> ('vectors-prod', None). Also takes an
    endpoint URL with or without https:// ('https://search-x.us-east-1.es.amazonaws.com/docs' ->
    ('https://search-x.us-east-1.es.amazonaws.com', 'docs')), a domain or collection ARN, and a collection ID."""
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
    """(SigV4 service, region) of an AWS OpenSearch endpoint: ('es', 'us-east-1') for a domain, ('aoss', ...) for a
    Serverless collection, (None, None) for anything else (an OpenSearch you run yourself)."""
    host = (urlsplit(url).hostname or "").lower()
    match = _AWS_HOST_RE.search(host)
    if not match:
        return None, None
    if match.group(3):
        return "es", match.group(3)
    return match.group(2), match.group(1)


def vector_norm(vector: Iterable[Any]) -> float | None:
    """Euclidean length of a vector; None when it holds something that isn't a number."""
    try:
        return math.sqrt(sum(float(x) * float(x) for x in vector))
    except (TypeError, ValueError):
        return None


def vector_summary(vector: Any, shown: int = 3) -> str:
    """[0.0123, -0.0441, 0.0087, …] -> '[0.0123, -0.0441, 0.0087, …] 1,024 dims · length 1.00'."""
    if not isinstance(vector, (list, tuple)):
        return "-" if vector is None else _clip(str(vector), 40)
    head = ", ".join(f"{x:.4g}" if isinstance(x, (int, float)) else str(x) for x in vector[:shown])
    norm = vector_norm(vector)
    tail = f" · length {norm:.2f}" if norm is not None else ""
    return f"[{head}{', …' if len(vector) > shown else ''}] {len(vector):,} dims{tail}"


def values_at(source: Any, path: str) -> list[Any]:
    """Every value at a dotted path in a document, through lists (nested documents): {'chunks': [{'v': 1}, {'v': 2}]}
    and 'chunks.v' -> [1, 2]. A field literally named 'a.b' wins over the nested path."""
    if isinstance(source, dict) and path in source:
        return [source[path]]
    head, _, rest = path.partition(".")
    if isinstance(source, list):
        return [v for item in source for v in values_at(item, path)]
    if not isinstance(source, dict) or head not in source or not rest:
        return []
    return values_at(source[head], rest)


def _format_value(value: Any, width: int = 80) -> str:
    """Short display text for a field value: strings as they are, numbers with commas, lists of numbers (vectors)
    summarized, other lists and dicts as compact JSON."""
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


# =============================================================================
# 2. Data models (what OpenSearchAnalyzer returns)
# =============================================================================


@dataclass
class Domain:
    """An OpenSearch Service domain (a managed cluster), from DescribeDomains. Parts that couldn't be read are in
    `errors` (section -> error code)."""

    name: str
    arn: str = ""
    engine: str = "OpenSearch"  # or 'Elasticsearch'
    version: str = ""  # '2.17'
    status: str = ""  # 'Active', 'Creating', 'Modifying', 'UpgradingEngineVersion', 'Deleting', ...
    endpoint: str | None = None  # host name; inside the VPC when `vpc` is set
    vpc: str | None = None  # VPC ID when the domain only answers inside a VPC
    instance_type: str = ""  # data nodes
    instance_count: int = 0
    master_type: str | None = None  # dedicated master nodes, when there are any
    master_count: int = 0
    warm_type: str | None = None  # UltraWarm nodes
    warm_count: int = 0
    zones: int = 1  # Availability Zones the data nodes are spread over
    standby: bool = False  # Multi-AZ with standby (one zone's nodes wait as standby)
    volume_type: str | None = None  # 'gp3' | 'gp2' | 'io1' | 'standard'; None = the instances' own disks
    volume_gb: int = 0  # per data node
    iops: int | None = None
    throughput: int | None = None  # MiB/s (gp3)
    encrypted: bool | None = None  # encryption at rest
    node_to_node: bool | None = None
    https_only: bool | None = None
    fine_grained: bool | None = None  # fine-grained access control (users and roles inside OpenSearch)
    internal_users: bool | None = None  # ... with its own user database (user name and password)
    access_policy: dict[str, Any] | None = None  # the domain's resource-based policy
    update_available: bool = False  # a service software update is waiting
    update_version: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str | None:
        return f"https://{self.endpoint}" if self.endpoint else None

    @property
    def label(self) -> str:
        return f"domain {self.name}"


@dataclass
class Collection:
    """An OpenSearch Serverless collection, from BatchGetCollection, with its network policy when it could be read."""

    name: str
    id: str = ""
    arn: str = ""
    kind: str = ""  # 'VECTORSEARCH' | 'SEARCH' | 'TIMESERIES'
    status: str = ""  # 'ACTIVE', 'CREATING', 'FAILED', ...
    endpoint: str | None = None  # https://<id>.<region>.aoss.amazonaws.com
    dashboard: str | None = None
    standby: bool = True  # standby replicas (redundancy); off for development collections
    kms_key: str | None = None  # None (or 'auto') = an AWS owned key
    created: datetime | None = None
    description: str = ""
    failure: str = ""  # why it FAILED
    group: str | None = None  # collection group
    network: str | None = None  # 'public' | 'vpc' | 'aws services' | None (unknown)
    network_policy: str | None = None
    vpc_endpoints: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str | None:
        return self.endpoint

    @property
    def label(self) -> str:
        return f"collection {self.name}"

    @property
    def vector(self) -> bool:
        return self.kind == "VECTORSEARCH"


@dataclass
class Endpoint:
    """Any other OpenSearch, given by its URL: one you run yourself, or a domain or collection by its endpoint.
    Requests to an AWS endpoint are signed with `service` ('es' or 'aoss') in `region`; others aren't."""

    url: str
    service: str | None = None
    region: str | None = None

    @property
    def name(self) -> str:
        return self.url

    @property
    def label(self) -> str:
        return self.url


@dataclass
class VectorField:
    """One knn_vector field in an index's mapping, with the defaults OpenSearch fills in left as None."""

    path: str  # 'embedding', or 'chunks.embedding' inside a nested field
    dimension: int | None = None
    data_type: str = "float"  # 'float' | 'byte' | 'binary'
    engine: str | None = None  # 'faiss' | 'lucene' | 'nmslib'; None = the version's default
    method: str | None = None  # 'hnsw' | 'ivf'; None = hnsw
    space_type: str | None = None  # 'l2' | 'cosinesimil' | 'innerproduct' | 'l1' | 'linf' | 'hamming'; None = l2
    m: int | None = None  # HNSW links per node (16 by default)
    ef_construction: int | None = None  # HNSW candidates while building (100 by default)
    ef_search: int | None = None  # HNSW candidates while searching (100 by default)
    encoder: str | None = None  # quantization inside the method: 'fp16', 'int8', 'pq', ...
    mode: str | None = None  # 'on_disk' | 'in_memory' (OpenSearch 2.17+)
    compression: str | None = None  # '2x' ... '32x' (OpenSearch 2.17+)
    model_id: str | None = None  # a trained model (IVF, PQ) defines the method
    nlist: int | None = None  # IVF lists
    nested: str | None = None  # the nested field it's inside: one vector per nested document

    @property
    def space(self) -> str:
        return self.space_type or "l2"

    @property
    def algorithm(self) -> str:
        return self.method or "hnsw"

    @property
    def links(self) -> int:
        return self.m or 16

    @property
    def compression_factor(self) -> float:
        """How much smaller than 32-bit floats each stored vector is: 2 for fp16, 32 for binary or on_disk's default."""
        if self.compression:
            return _int(self.compression.rstrip("xX")) or 1
        if self.data_type == "binary":
            return 32
        if self.mode == "on_disk":
            return 32  # on_disk mode compresses 32x unless compression_level says otherwise
        if self.data_type == "byte" or self.encoder in ("int8", "int7", "sq"):
            return 4
        return 2 if self.encoder == "fp16" else 1


@dataclass
class IndexInfo:
    """One index: what _cat/indices, its mapping and settings, and the counts and stats say about it. Parts that
    couldn't be read are in `errors` (section -> error)."""

    name: str
    health: str | None = None  # 'green' | 'yellow' | 'red' (None on Serverless)
    status: str | None = None  # 'open' | 'close'
    docs: int | None = None  # documents (_cat's count, which includes nested documents)
    deleted: int | None = None  # deleted documents not merged away yet
    size_bytes: int | None = None  # primaries and replicas
    primary_bytes: int | None = None
    shards: int | None = None  # primary shards
    replicas: int | None = None  # copies of each primary
    knn: bool | None = None  # the index.knn setting: graphs for approximate search are built
    ef_search: int | None = None  # index.knn.algo_param.ef_search
    space_type: str | None = None  # the old index-wide index.knn.space_type setting
    refresh_interval: str | None = None
    created: datetime | None = None
    fields: dict[str, str] = field(default_factory=dict)  # field path -> mapping type
    vectors: list[VectorField] = field(default_factory=list)
    text_field: str | None = None  # the field that most likely holds each document's text
    top_docs: int | None = None  # top-level documents (_count)
    with_vector: dict[str, int] = field(default_factory=dict)  # vector field -> top-level documents that have one
    segments: int | None = None  # Lucene segments, all shards and copies
    aliases: list[str] = field(default_factory=list)
    source_excludes: list[str] = field(default_factory=list)  # fields left out of _source
    unindexed: list[str] = field(default_factory=list)  # fields the mapping keeps but doesn't index ("index": false)
    mapping: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)  # flat settings
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def copies(self) -> int:
        """Primaries plus replicas: each copy builds and loads its own graphs."""
        return 1 + (self.replicas or 0)

    @property
    def documents(self) -> int | None:
        return self.top_docs if self.top_docs is not None else self.docs

    def vector(self, name: str | None = None) -> VectorField:
        """The vector field `name`, or the only one (the first when there are several)."""
        if not self.vectors:
            raise ValueError(f"Index {self.name} has no vector (knn_vector) fields")
        if name is None:
            return self.vectors[0]
        for vf in self.vectors:
            if vf.path == name:
                return vf
        listed = ", ".join(v.path for v in self.vectors)
        raise ValueError(f"Index {self.name} has no vector field {name!r} (its vector fields: {listed})")


@dataclass
class ClusterHealth:
    """_cluster/health of a domain or self-run cluster."""

    status: str = ""  # 'green' | 'yellow' | 'red'
    nodes: int = 0
    data_nodes: int = 0
    active_shards: int = 0
    unassigned_shards: int = 0


@dataclass
class KnnStats:
    """What the k-NN plugin reports (_plugins/_knn/stats; not on Serverless): native memory the faiss and nmslib
    graphs use, and whether it ran out."""

    nodes: int = 0
    memory_bytes: int = 0  # graph memory on all nodes
    memory_percent: float | None = None  # the fullest node, as a share of its k-NN memory limit
    circuit_breaker: bool = False  # tripped: the graph memory limit was hit
    cache_full: bool = False
    evictions: int = 0  # graphs dropped from memory to make room
    hits: int = 0
    misses: int = 0  # searches that had to load a graph from disk first
    by_index: dict[str, int] = field(default_factory=dict)  # index -> graph memory bytes


@dataclass
class StoreReport:
    """indexes(): every index in a domain, collection or cluster, and the cluster's health and k-NN memory."""

    store: Domain | Collection | Endpoint
    indexes: list[IndexInfo] = field(default_factory=list)
    health: ClusterHealth | None = None
    knn: KnnStats | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def vector_indexes(self) -> list[IndexInfo]:
        return [i for i in self.indexes if i.vectors]


@dataclass
class VectorCheck:
    """How a sample of one field's vectors looks: their dimensions, lengths, zeros and repeats."""

    field: str
    docs: int = 0  # documents looked at
    vectors: int = 0  # vectors found in them (several per document in a nested field)
    dims: Counter = field(default_factory=Counter)  # dimension -> vectors
    norm_min: float | None = None
    norm_max: float | None = None
    norm_mean: float | None = None
    zeros: int = 0  # all-zero vectors
    repeats: int = 0  # vectors identical to one seen earlier in the sample
    bad: int = 0  # vectors holding NaN, infinity or something that isn't a number
    missing: int = 0  # documents without the field

    @property
    def unit_length(self) -> bool:
        """Every vector has length 1 (to within 1%): normalized, as most embedding models return them."""
        return self.norm_min is not None and self.norm_max is not None and 0.99 <= self.norm_min <= self.norm_max <= 1.01


@dataclass
class Sample:
    """Documents read from an index (random ones when the endpoint allows), with a check of each vector field."""

    target: str
    index: str
    docs: list[dict[str, Any]] = field(default_factory=list)  # '_id' plus the _source fields
    checks: dict[str, VectorCheck] = field(default_factory=dict)  # vector field -> what its vectors look like
    random: bool = True
    total: int | None = None  # documents in the index (the search's count)
    took_ms: int | None = None
    info: IndexInfo | None = field(default=None, repr=False)  # the index's mapping and settings

    def to_df(self, *, vectors: bool = False):
        """The documents as a pandas DataFrame (vector fields left out unless vectors=True)."""
        pd = _require("pandas", "Sample.to_df")
        rows = [{k: v for k, v in doc.items() if vectors or k not in self.checks} for doc in self.docs]
        return pd.DataFrame(rows)


@dataclass
class Embedding:
    """A text embedded by a Bedrock model, and what it cost."""

    vector: list[float]
    model: str
    tokens: int | None = None
    cost: float | None = None  # USD


@dataclass
class Hit:
    """One search result: its rank, OpenSearch's score, and the similarity or distance read back from the score."""

    rank: int
    id: str
    score: float
    source: dict[str, Any] = field(default_factory=dict)
    similarity: float | None = None  # cosine, inner product or distance, depending on the space type


@dataclass
class SearchResult:
    """The k nearest neighbours of a query vector, from one k-NN search."""

    target: str
    index: str
    field: str
    space_type: str
    k: int
    hits: list[Hit] = field(default_factory=list)
    text_field: str | None = None  # the field holding each document's text
    query: dict[str, Any] = field(default_factory=dict)  # the request body sent
    took_ms: int | None = None  # OpenSearch's own time
    seconds: float = 0.0  # the whole call, embedding included
    text: str | None = None
    like: str | None = None
    where: Any = None
    embedding: Embedding | None = None
    query_norm: float | None = None
    hit_norms: list[float] = field(default_factory=list)  # lengths of the results' own vectors
    excluded_self: bool = False  # like=: the document itself was dropped from the results
    info: IndexInfo | None = field(default=None, repr=False)  # the index's mapping and settings

    def to_df(self):
        """One row per hit: rank, id, score, similarity and the document's fields."""
        pd = _require("pandas", "SearchResult.to_df")
        return pd.DataFrame(
            [{"rank": h.rank, "id": h.id, "score": h.score, "similarity": h.similarity, **h.source} for h in self.hits]
        )


@dataclass
class Overview:
    """overview(): every domain and Serverless collection in the region, plus the account's Serverless capacity."""

    region: str
    domains: list[Domain] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    max_indexing_ocus: float | None = None  # the account's Serverless capacity limits
    max_search_ocus: float | None = None
    ocus: dict[str, float] | None = None  # CloudWatch: average 'indexing' / 'search' OCUs over `hours`
    hours: int = 24
    errors: dict[str, str] = field(default_factory=dict)


# =============================================================================
# 3. Pure analysis (no AWS calls - works on DescribeDomains / BatchGetCollection answers, mappings and documents)
# =============================================================================

_DOMAIN_STATES = {
    "Creating": "being created",
    "Modifying": "applying a configuration change",
    "UpgradingEngineVersion": "upgrading its OpenSearch version",
    "UpdatingServiceSoftware": "installing a service software update",
    "Isolated": "isolated (AWS suspended it; see the console)",
    "Deleting": "being deleted",
}
_SPACE_NAMES = {
    "cosinesimil": "cosine",
    "innerproduct": "inner product",
    "l2": "L2 (Euclidean)",
    "l1": "L1 (Manhattan)",
    "linf": "L∞ (Chebyshev)",
    "hamming": "Hamming",
}
# space type -> what search() reads back from each score (the score formulas are in score_to_similarity). faiss and
# lucene score L2 by the squared distance; nmslib by the distance itself (similarity_name).
_SIMILARITY_NAMES = {
    "cosinesimil": "Cosine",
    "innerproduct": "Inner product",
    "l2": "Squared L2 distance",
    "l1": "L1 distance",
    "linf": "L∞ distance",
    "hamming": "Hamming distance",
}
_SCORE_MEANINGS = {
    "cosinesimil": "score = (1 + cosine) / 2: 1.0 is the same direction, 0.5 unrelated (cosine 0), 0 the opposite",
    "innerproduct": "score = 1 + inner product when that's positive, else 1 / (1 - inner product): higher is closer, "
    "and for unit-length vectors the inner product is the cosine",
    "l2": "score = 1 / (1 + {distance}): 1.0 is identical, falling toward 0 as vectors get further apart",
    "l1": "score = 1 / (1 + L1 distance): 1.0 is identical, falling toward 0 as vectors get further apart",
    "linf": "score = 1 / (1 + L∞ distance): 1.0 is identical, falling toward 0 as vectors get further apart",
    "hamming": "score = 1 / (1 + differing bits): 1.0 is identical",
}


def similarity_name(vf: VectorField) -> str:
    """What search() reads back from a score in this field: 'Cosine', 'Squared L2 distance', ..."""
    if vf.space == "l2" and vf.engine == "nmslib":
        return "L2 distance"
    return _SIMILARITY_NAMES.get(vf.space, "Similarity")


def score_meaning(vf: VectorField) -> str:
    """How OpenSearch turns this field's distance into a score, in words."""
    distance = "L2 distance" if vf.engine == "nmslib" else "squared L2 distance"
    return _SCORE_MEANINGS.get(vf.space, "").format(distance=distance)


_BEST = {"Cosine": "Best cosine", "Inner product": "Best inner product", "Squared L2 distance": "Smallest squared L2 "
         "distance", "Hamming distance": "Fewest differing bits"}  # the search card for the nearest result
# Fields a Bedrock knowledge base writes into the OpenSearch index it stores its vectors in.
_BEDROCK_FIELDS = ("AMAZON_BEDROCK_TEXT_CHUNK", "AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA")
# The field holding each document's text, by the names tools give it: Bedrock knowledge bases, LangChain
# (text, page_content), LlamaIndex (content), Haystack (content), and common hand-made ones.
_TEXT_FIELDS = (
    "AMAZON_BEDROCK_TEXT_CHUNK",
    "AMAZON_BEDROCK_TEXT",
    "text",
    "content",
    "page_content",
    "chunk",
    "chunk_text",
    "passage",
    "body",
    "document",
    "contents",
    "description",
)
_TEXT_TYPES = ("text", "match_only_text", "keyword", "wildcard")
# Fields that say where a document came from, shown next to its text.
_SOURCE_FIELDS = (
    "x-amz-bedrock-kb-source-uri",
    "source",
    "source_uri",
    "url",
    "uri",
    "file",
    "file_name",
    "filename",
    "path",
    "title",
    "metadata.source",
    "metadata.file_name",
    "metadata.title",
)


def parse_domain(status: dict[str, Any]) -> Domain:
    """A DescribeDomain(s) DomainStatus -> Domain."""
    engine, version = parse_engine_version(status.get("EngineVersion"))
    cluster = status.get("ClusterConfig") or {}
    ebs = status.get("EBSOptions") or {}
    endpoints = status.get("Endpoints") or {}
    vpc = (status.get("VPCOptions") or {}).get("VPCId") or ("(VPC)" if "vpc" in endpoints else None)
    if status.get("DomainProcessingStatus"):
        state = status["DomainProcessingStatus"]
    elif status.get("Deleted"):
        state = "Deleting"
    elif status.get("Created") is False:
        state = "Creating"
    elif status.get("UpgradeProcessing"):
        state = "UpgradingEngineVersion"
    elif status.get("Processing"):
        state = "Modifying"
    else:
        state = "Active"
    try:
        policy = json.loads(status["AccessPolicies"]) if status.get("AccessPolicies") else None
    except ValueError:
        policy = None
    software = status.get("ServiceSoftwareOptions") or {}
    security = status.get("AdvancedSecurityOptions") or {}
    zones = cluster.get("ZoneAwarenessConfig", {}).get("AvailabilityZoneCount", 2)
    return Domain(
        name=status.get("DomainName", ""),
        arn=status.get("ARN", ""),
        engine=engine,
        version=version,
        status=state,
        endpoint=status.get("Endpoint") or endpoints.get("vpc") or status.get("EndpointV2")
        or next(iter(endpoints.values()), None),
        vpc=vpc,
        instance_type=cluster.get("InstanceType", ""),
        instance_count=cluster.get("InstanceCount") or 0,
        master_type=cluster.get("DedicatedMasterType") if cluster.get("DedicatedMasterEnabled") else None,
        master_count=(cluster.get("DedicatedMasterCount") or 0) if cluster.get("DedicatedMasterEnabled") else 0,
        warm_type=cluster.get("WarmType") if cluster.get("WarmEnabled") else None,
        warm_count=(cluster.get("WarmCount") or 0) if cluster.get("WarmEnabled") else 0,
        zones=zones if cluster.get("ZoneAwarenessEnabled") else 1,
        standby=bool(cluster.get("MultiAZWithStandbyEnabled")),
        volume_type=(ebs.get("VolumeType") or "gp2") if ebs.get("EBSEnabled") else None,
        volume_gb=(ebs.get("VolumeSize") or 0) if ebs.get("EBSEnabled") else 0,
        iops=ebs.get("Iops"),
        throughput=ebs.get("Throughput"),
        encrypted=(status.get("EncryptionAtRestOptions") or {}).get("Enabled"),
        node_to_node=(status.get("NodeToNodeEncryptionOptions") or {}).get("Enabled"),
        https_only=(status.get("DomainEndpointOptions") or {}).get("EnforceHTTPS"),
        fine_grained=security.get("Enabled"),
        internal_users=security.get("InternalUserDatabaseEnabled"),
        access_policy=policy if isinstance(policy, dict) else None,
        update_available=bool(software.get("UpdateAvailable")),
        update_version=software.get("NewVersion") or "",
    )


def parse_collection(detail: dict[str, Any]) -> Collection:
    """A BatchGetCollection collectionDetail (or a ListCollections summary) -> Collection."""
    key = detail.get("kmsKeyArn")
    return Collection(
        name=detail.get("name", ""),
        id=detail.get("id", ""),
        arn=detail.get("arn", ""),
        kind=detail.get("type", ""),
        status=detail.get("status", ""),
        endpoint=detail.get("collectionEndpoint"),
        dashboard=detail.get("dashboardEndpoint"),
        standby=detail.get("standbyReplicas", "ENABLED") != "DISABLED",
        kms_key=None if key in (None, "", "auto") else key,
        created=_epoch_ms(detail.get("createdDate")),
        description=detail.get("description", ""),
        failure=detail.get("failureMessage", ""),
        group=detail.get("collectionGroupName"),
    )


def network_access(policies: Iterable[tuple[str, Any]], collection: str) -> tuple[str | None, str | None, list[str]]:
    """Which network policy covers a collection, and what it allows -> (access, policy name, VPC endpoint IDs).
    access is 'public' (the internet, still behind IAM and the data access policy), 'vpc' (only through the listed
    VPC endpoints), 'aws services' (only AWS services such as Bedrock) or None when no policy names it.
    policies: (name, policy document) pairs, as GetSecurityPolicy returns them for type='network'."""
    found: tuple[str | None, str | None, list[str]] = (None, None, [])
    for name, document in policies:
        rule_sets = document if isinstance(document, list) else [document]
        for rules in rule_sets:
            if not isinstance(rules, dict):
                continue
            covered = any(
                rule.get("ResourceType") == "collection"
                and any(fnmatch.fnmatchcase(f"collection/{collection}", pattern) for pattern in rule.get("Resource", []))
                for rule in rules.get("Rules", [])
                if isinstance(rule, dict)
            )
            if not covered:
                continue
            if rules.get("AllowFromPublic"):
                return "public", name, []
            if rules.get("SourceVPCEs"):
                found = ("vpc", name, list(rules["SourceVPCEs"]))
            elif rules.get("SourceServices") and found[0] is None:
                found = ("aws services", name, [])
    return found


def _vector_field(path: str, spec: dict[str, Any], nested: str | None) -> VectorField:
    method = spec.get("method") or {}
    params = method.get("parameters") or {}
    encoder = params.get("encoder") or {}
    name = encoder.get("name")
    options = encoder.get("parameters") or {}
    if name == "sq":
        lucene = method.get("engine") == "lucene" or "bits" in options
        label: str | None = f"int{options.get('bits', 7)}" if lucene else str(options.get("type", "fp16"))
    else:
        label = name if name and name != "flat" else None
    return VectorField(
        path=path,
        dimension=_int(spec.get("dimension")),
        data_type=spec.get("data_type") or "float",
        engine=method.get("engine"),
        method=method.get("name"),
        space_type=method.get("space_type") or spec.get("space_type"),
        m=_int(params.get("m")),
        ef_construction=_int(params.get("ef_construction")),
        ef_search=_int(params.get("ef_search")),
        encoder=label,
        mode=spec.get("mode"),
        compression=spec.get("compression_level"),
        model_id=spec.get("model_id"),
        nlist=_int(params.get("nlist")),
        nested=nested,
    )


def parse_mapping(mapping: dict[str, Any]) -> tuple[dict[str, str], list[VectorField], list[str]]:
    """An index's mapping -> (field path -> type, its vector fields, the fields left out of _source).
    Takes the 'mappings' object, or GET <index>/_mapping's whole answer for one index."""
    if len(mapping) == 1:
        (only,) = mapping.values()
        if isinstance(only, dict) and "mappings" in only:
            mapping = only
    mapping = mapping.get("mappings", mapping)
    if "properties" not in mapping and len(mapping) == 1:  # Elasticsearch 6: {'_doc': {'properties': ...}}
        (only,) = mapping.values()
        if isinstance(only, dict) and "properties" in only:
            mapping = only
    fields: dict[str, str] = {}
    vectors: list[VectorField] = []

    def walk(properties: dict[str, Any], prefix: str, nested: str | None) -> None:
        for name, spec in properties.items():
            if not isinstance(spec, dict):
                continue
            path = prefix + name
            kind = spec.get("type") or ("object" if "properties" in spec else "")
            fields[path] = kind
            if kind == "knn_vector":
                vectors.append(_vector_field(path, spec, nested))
            if isinstance(spec.get("properties"), dict):
                walk(spec["properties"], path + ".", path if kind == "nested" else nested)
            for sub, sub_spec in (spec.get("fields") or {}).items():
                if isinstance(sub_spec, dict):
                    fields[f"{path}.{sub}"] = sub_spec.get("type", "")

    walk(mapping.get("properties") or {}, "", None)
    excludes = (mapping.get("_source") or {}).get("excludes") or []
    return fields, vectors, [str(e) for e in excludes]


def unindexed_fields(mapping: dict[str, Any]) -> list[str]:
    """Fields a mapping keeps in _source but doesn't index ("index": false): they can't be searched or filtered on."""
    found: list[str] = []

    def walk(properties: dict[str, Any], prefix: str) -> None:
        for name, spec in properties.items():
            if isinstance(spec, dict):
                if str(spec.get("index", "")).lower() == "false" or spec.get("enabled") is False:
                    found.append(prefix + name)
                if isinstance(spec.get("properties"), dict):
                    walk(spec["properties"], f"{prefix}{name}.")

    walk(mapping.get("properties") or {}, "")
    return found


def _flat_settings(settings: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """{'index': {'knn': 'true'}} -> {'index.knn': 'true'}; flat settings pass through."""
    flat: dict[str, Any] = {}
    for key, value in settings.items():
        if isinstance(value, dict):
            flat.update(_flat_settings(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def read_settings(info: IndexInfo, settings: dict[str, Any]) -> None:
    """Fill in what an index's settings say: index.knn, ef_search, shards, replicas, refresh interval, creation date.
    settings: the 'settings' object of GET <index>/_settings (flat or nested)."""
    flat = _flat_settings(settings)
    info.settings = flat
    s = {key.removeprefix("index."): value for key, value in flat.items()}
    info.knn = _true(s.get("knn"))
    info.ef_search = _int(s.get("knn.algo_param.ef_search"))
    info.space_type = s.get("knn.space_type")
    info.refresh_interval = s.get("refresh_interval")
    info.created = _epoch_ms(s.get("creation_date")) or info.created
    if info.shards is None:
        info.shards = _int(s.get("number_of_shards"))
    if info.replicas is None:
        info.replicas = _int(s.get("number_of_replicas"))
    for vf in info.vectors:  # mappings without a method take the index-wide settings (the oldest k-NN style)
        if vf.method is None and vf.model_id is None:
            vf.m = vf.m or _int(s.get("knn.algo_param.m"))
            vf.ef_construction = vf.ef_construction or _int(s.get("knn.algo_param.ef_construction"))
            vf.space_type = vf.space_type or info.space_type
        vf.ef_search = vf.ef_search or info.ef_search


def guess_text_field(fields: dict[str, str]) -> str | None:
    """The field that most likely holds each document's text: a name tools give it (Bedrock's
    AMAZON_BEDROCK_TEXT_CHUNK, LangChain's text, ...), else the first text field."""
    texts = [path for path, kind in fields.items() if kind in _TEXT_TYPES]
    by_name = {path.lower(): path for path in texts}
    for name in _TEXT_FIELDS:
        if name.lower() in by_name:
            return by_name[name.lower()]
    top = [path for path in texts if "." not in path and fields[path] != "keyword"]
    return (top or [p for p in texts if fields[p] != "keyword"] or [None])[0]


def source_field(doc: dict[str, Any]) -> tuple[str, str]:
    """(field, value) saying where a document came from, by the fields tools use for it (Bedrock's
    x-amz-bedrock-kb-source-uri, LangChain's metadata.source, url, file, title, ...); ('', '') when none."""
    for name in _SOURCE_FIELDS:
        values = values_at(doc, name)
        if values and values[0] not in (None, "", [], {}):
            return name, str(values[0])
    metadata = doc.get("AMAZON_BEDROCK_METADATA")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            found = parsed.get("source") or parsed.get("x-amz-bedrock-kb-source-uri")
            if found:
                return "AMAZON_BEDROCK_METADATA", str(found)
    return "", ""


def source_of(doc: dict[str, Any]) -> str:
    """Where a document came from (see source_field); '' when it doesn't say."""
    return source_field(doc)[1]


def index_from_cat(row: dict[str, Any]) -> IndexInfo:
    """A row of GET _cat/indices?format=json -> IndexInfo (its counts and sizes)."""
    return IndexInfo(
        name=str(row.get("index", "")),
        health=row.get("health") or None,
        status=row.get("status") or None,
        docs=_int(row.get("docs.count")),
        deleted=_int(row.get("docs.deleted")),
        size_bytes=_bytes(row.get("store.size")),
        primary_bytes=_bytes(row.get("pri.store.size")),
        shards=_int(row.get("pri")),
        replicas=_int(row.get("rep")),
    )


def parse_knn_stats(resp: dict[str, Any]) -> KnnStats:
    """GET _plugins/_knn/stats -> KnnStats. Graph memory is reported in KB per node."""
    nodes = resp.get("nodes") or {}
    stats = KnnStats(nodes=len(nodes), circuit_breaker=bool(resp.get("circuit_breaker_triggered")))
    for node in nodes.values():
        stats.memory_bytes += int(float(node.get("graph_memory_usage") or 0) * KB)
        percent = node.get("graph_memory_usage_percentage")
        if percent is not None:
            stats.memory_percent = max(stats.memory_percent or 0.0, float(percent))
        stats.cache_full = stats.cache_full or bool(node.get("cache_capacity_reached"))
        stats.evictions += int(node.get("eviction_count") or 0)
        stats.hits += int(node.get("hit_count") or 0)
        stats.misses += int(node.get("miss_count") or 0)
        for index, usage in (node.get("indices_in_cache") or {}).items():
            used = int(float(usage.get("graph_memory_usage") or 0) * KB)
            stats.by_index[index] = stats.by_index.get(index, 0) + used
    return stats


def vector_memory(vf: VectorField, vectors: int) -> int | None:
    """Estimated bytes the search structure of `vectors` vectors needs in memory, by OpenSearch's sizing rules:
    HNSW 1.1 x (bytes per vector + 8 x m) per vector; IVF 1.1 x (bytes per vector x vectors + 4 x nlist x dimension).
    fp16, byte, binary and on-disk compression shrink the bytes per vector. None for a trained (PQ) model or an unknown
    dimension. Multiply by IndexInfo.copies for the replicas, which hold their own graphs."""
    if not vf.dimension or vf.model_id or vf.encoder == "pq":
        return None
    per_vector = 4 * vf.dimension / vf.compression_factor
    if vf.algorithm == "ivf":
        return int(1.1 * (per_vector * vectors + 4 * (vf.nlist or 4) * vf.dimension))
    return int(1.1 * (per_vector + 8 * vf.links) * vectors)


def vector_count(info: IndexInfo, vf: VectorField) -> int | None:
    """How many vectors a field holds: the documents that have one, or for a field inside a nested field, the nested
    documents (Lucene's count, which _cat/indices reports, less the top-level documents)."""
    if vf.nested and info.docs is not None and info.top_docs is not None and info.docs > info.top_docs:
        return info.docs - info.top_docs
    count = info.with_vector.get(vf.path)
    if count is None:
        count = info.docs if vf.nested else info.documents
    return count


def index_vector_memory(info: IndexInfo, *, native: bool = False) -> int | None:
    """Estimated memory of all of an index's vector fields, primaries and replicas; None when it can't be told.
    native=True counts only the fields whose graphs live in the k-NN plugin's memory (faiss and nmslib, not lucene)."""
    total, known = 0, False
    for vf in info.vectors:
        if native and vf.engine == "lucene":
            continue
        count = vector_count(info, vf)
        need = vector_memory(vf, count or 0) if count is not None else None
        if need is not None:
            total, known = total + need * info.copies, True
    return total if known else None


def knn_memory_limit(memory_gib: float) -> float:
    """Bytes of native memory the k-NN plugin may use on a node with `memory_gib` of RAM, at its default limit: half
    of what the Java heap leaves (OpenSearch Service gives the heap half the RAM, up to 32 GiB). faiss and nmslib
    graphs live there; lucene's live in the operating system's file cache instead."""
    heap = min(memory_gib / 2, 32)
    return 0.5 * (memory_gib - heap) * GB


def domain_knn_memory(domain: Domain) -> float | None:
    """k-NN memory of all the domain's data nodes together (knn_memory_limit per node); None for an unknown type."""
    spec = INSTANCE_TYPES.get(domain.instance_type)
    return knn_memory_limit(spec[2]) * domain.instance_count if spec and domain.instance_count else None


def domain_monthly_cost(domain: Domain, prices: dict[str, float] | None = None) -> dict[str, float | None]:
    """Estimated USD per month: 'data nodes', 'master nodes' and 'UltraWarm nodes' (instance-hours at list price) and
    'storage' (every data node's EBS volume). None where a price isn't known (pass it in prices). IOPS and throughput
    above gp3's baseline, UltraWarm storage and data transfer aren't included."""
    prices = OPENSEARCH_PRICES if prices is None else prices

    def nodes(kind: str | None, count: int) -> float | None:
        price = prices.get(kind or "")
        return None if price is None else price * count * HOURS_PER_MONTH

    cost: dict[str, float | None] = {"data nodes": nodes(domain.instance_type, domain.instance_count)}
    if domain.master_type and domain.master_count:
        cost["master nodes"] = nodes(domain.master_type, domain.master_count)
    if domain.warm_type and domain.warm_count:
        cost["UltraWarm nodes"] = nodes(domain.warm_type, domain.warm_count)
    if domain.volume_type and domain.volume_gb:
        rate = prices.get(domain.volume_type)
        cost["storage"] = None if rate is None else rate * domain.volume_gb * domain.instance_count
    return cost


def _key_label(key: str | None) -> str:
    return "AWS owned key" if not key or key == "AWS owned key" else f"key {key.rsplit('/', 1)[-1]}"


def serverless_minimum(collections: Iterable[Collection]) -> list[tuple[str, float, list[str]]]:
    """The OCUs OpenSearch Serverless bills even when idle -> [(group, OCUs, collection names)], one entry for each
    group of collections that share OCUs: same encryption key, same kind (vector search or not) and same standby
    setting. Collections being deleted or that failed to create aren't counted."""
    groups: dict[tuple[bool, str, bool], list[str]] = {}
    for c in collections:
        if c.status in ("DELETING", "FAILED"):
            continue
        groups.setdefault((c.vector, c.kms_key or "AWS owned key", c.standby), []).append(c.name)
    return [
        (
            f"{'vector search' if vector else 'search / time series'} · {_key_label(key)} · "
            f"standby replicas {'on' if standby else 'off'}",
            SERVERLESS_MIN_OCUS[standby],
            sorted(names),
        )
        for (vector, key, standby), names in sorted(groups.items())
    ]


def ocu_monthly_cost(ocus: float, prices: dict[str, float] | None = None) -> float:
    """USD per month for `ocus` OpenSearch Compute Units running all month."""
    prices = OPENSEARCH_PRICES if prices is None else prices
    return ocus * prices["ocu_hour"] * HOURS_PER_MONTH


def score_to_similarity(score: float | None, space_type: str | None) -> float | None:
    """OpenSearch's k-NN score -> the similarity or distance it was computed from (faiss, lucene and nmslib use the
    same formulas): cosinesimil: score = (1 + cosine) / 2; innerproduct: score = 1 + ip when ip > 0, else
    1 / (1 - ip); l2, l1, linf and hamming: score = 1 / (1 + d), where d for l2 is the squared distance on faiss and
    lucene and the distance itself on nmslib."""
    if score is None or score <= 0:
        return None
    space = space_type or "l2"
    if space == "cosinesimil":
        return 2 * score - 1
    if space == "innerproduct":
        return score - 1 if score >= 1 else 1 - 1 / score
    return 1 / score - 1


def check_vectors(vectors: Iterable[Any], field: str = "") -> VectorCheck:
    """Look over vectors (lists of numbers) -> their dimensions, range of lengths, zeros, exact repeats and vectors
    that hold NaN, infinity or something that isn't a number."""
    check = VectorCheck(field)
    seen: set[int] = set()
    norms: list[float] = []
    for vector in vectors:
        check.vectors += 1
        if not isinstance(vector, (list, tuple)) or not vector:
            check.bad += 1
            continue
        try:
            numbers = [float(x) for x in vector]
        except (TypeError, ValueError):
            check.bad += 1
            continue
        if not all(math.isfinite(x) for x in numbers):
            check.bad += 1
            continue
        check.dims[len(numbers)] += 1
        norm = math.sqrt(sum(x * x for x in numbers))
        norms.append(norm)
        check.zeros += norm == 0
        marker = hash(tuple(numbers))
        if marker in seen:
            check.repeats += 1
        seen.add(marker)
    if norms:
        check.norm_min, check.norm_max, check.norm_mean = min(norms), max(norms), sum(norms) / len(norms)
    return check


_RANGE_OPS = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
_EQUAL_OPS = ("=", "==", "eq")
_NOT_EQUAL_OPS = ("!=", "<>", "ne")
_FILTER_OPS = (
    *_RANGE_OPS, *_EQUAL_OPS, *_NOT_EQUAL_OPS, "between", "in", "prefix", "begins_with", "contains", "exists",
    "missing", "not_exists",
)
_DSL_KEYS = {
    "bool", "term", "terms", "range", "match", "match_phrase", "exists", "prefix", "wildcard", "ids", "nested",
    "query_string", "simple_query_string", "regexp", "match_all", "geo_distance", "geo_bounding_box",
}


def _exact(name: str, fields: dict[str, str]) -> tuple[str, bool]:
    """(field to match exact values on, whether it's analyzed text without a keyword sub-field)."""
    if fields.get(name) in ("text", "match_only_text"):
        keyword = f"{name}.keyword"
        return (keyword, False) if fields.get(keyword) == "keyword" else (name, True)
    return name, False


def _filter_clause(name: str, spec: Any, fields: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """One where= entry -> (query DSL clause, whether it excludes the documents it matches)."""
    if isinstance(spec, tuple) and spec and isinstance(spec[0], str) and spec[0].lower() in _FILTER_OPS:
        op, args = spec[0].lower(), spec[1:]
    elif isinstance(spec, (list, set, frozenset)):
        op, args = "in", (list(spec),)
    else:
        op, args = "=", (spec,)
    wanted = {"between": 2, "exists": 0, "missing": 0, "not_exists": 0}.get(op, 1)
    if len(args) != wanted:
        example = {"between": "('between', 2020, 2024)", "exists": "('exists',)"}.get(op, f"('{op}', value)")
        raise ValueError(f"where={{{name!r}: ...}}: {op!r} takes {wanted} value{'s' if wanted != 1 else ''}, "
                         f"like {example}")
    exact, text_only = _exact(name, fields)
    if op in _EQUAL_OPS + _NOT_EQUAL_OPS:
        clause = {"match_phrase": {name: args[0]}} if text_only else {"term": {exact: args[0]}}
        return clause, op in _NOT_EQUAL_OPS
    if op in _RANGE_OPS:
        return {"range": {name: {_RANGE_OPS[op]: args[0]}}}, False
    if op == "between":
        return {"range": {name: {"gte": args[0], "lte": args[1]}}}, False
    if op == "in":
        values = list(args[0]) if isinstance(args[0], (list, tuple, set, frozenset)) else [args[0]]
        return {"terms": {exact: values}}, False
    if op in ("prefix", "begins_with"):
        return {"prefix": {exact: args[0]}}, False
    if op == "contains":
        return {"match": {name: args[0]}}, False
    return {"exists": {"field": name}}, op != "exists"


def _check_where(where: Any, info: IndexInfo) -> None:
    """Refuse a where= on a field the mapping doesn't index, which would silently match nothing."""
    if isinstance(where, dict) and not (len(where) == 1 and next(iter(where)) in _DSL_KEYS):
        for name in where:
            if name in info.unindexed:
                raise ValueError(f"Can't filter on {name!r}: the mapping keeps it without indexing it (\"index\": "
                                 "false), so OpenSearch can't search it. index_info() lists the fields you can filter on.")


def build_filter(where: Any, fields: dict[str, str] | None = None) -> dict[str, Any] | None:
    """A friendly filter -> OpenSearch query DSL, for the filter of a k-NN search.

    where={'lang': 'en', 'year': ('>=', 2024), 'team': ['billing', 'support'], 'draft': ('!=', True)} matches
    documents in English from 2024 on, of the billing or support team, that aren't drafts. Operators: '=', '!=',
    '>', '>=', '<', '<=', ('between', low, high), ('in', [...]), ('prefix', 'abc'), ('contains', 'some words'),
    ('exists',) and ('missing',). With `fields` (the index's field types), a text field is matched exactly through
    its .keyword sub-field, and a field the index doesn't have is reported with the closest names. Query DSL
    ({'term': {...}}, {'bool': {...}}) passes through unchanged."""
    if where is None or where == {}:
        return None
    if not isinstance(where, dict):
        raise ValueError("where= takes a dict, like {'lang': 'en', 'year': ('>=', 2024)}")
    if len(where) == 1 and next(iter(where)) in _DSL_KEYS:
        return where
    fields = fields or {}
    must: list[dict[str, Any]] = []
    must_not: list[dict[str, Any]] = []
    for name, spec in where.items():
        if fields and name not in fields:
            close = difflib.get_close_matches(name, list(fields), n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            raise ValueError(f"The index has no field {name!r}.{hint} index_info() lists the fields you can filter on.")
        clause, negate = _filter_clause(name, spec, fields)
        (must_not if negate else must).append(clause)
    query: dict[str, Any] = {}
    if must:
        query["filter"] = must
    if must_not:
        query["must_not"] = must_not
    return {"bool": query}


def describe_filter(where: Any) -> str:
    """where= in words: {'lang': 'en', 'year': ('>=', 2024)} -> "lang = 'en' and year >= 2024"."""
    if not where:
        return "no filter"
    if not isinstance(where, dict) or (len(where) == 1 and next(iter(where)) in _DSL_KEYS):
        return "a query DSL filter"
    parts = []
    for name, spec in where.items():
        if isinstance(spec, tuple) and spec and isinstance(spec[0], str) and spec[0].lower() in _FILTER_OPS:
            op, args = spec[0].lower(), spec[1:]
            if op == "between":
                parts.append(f"{name} between {args[0]!r} and {args[1]!r}")
            elif op in ("exists", "missing", "not_exists"):
                parts.append(f"{name} {'is set' if op == 'exists' else 'is missing'}")
            else:
                parts.append(f"{name} {op} {args[0]!r}")
        elif isinstance(spec, (list, set, frozenset)):
            parts.append(f"{name} in {sorted(spec, key=str)!r}")
        else:
            parts.append(f"{name} = {spec!r}")
    return " and ".join(parts)


def knn_query(
    vf: VectorField,
    vector: list[float],
    k: int = 10,
    *,
    filter: dict[str, Any] | None = None,
    ef_search: int | None = None,
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    """The _search body for the k nearest neighbours of `vector` in field `vf`. The filter is applied during the
    search (faiss and lucene), or after it for nmslib and nested fields, which can return fewer than k then.
    ef_search overrides the index's candidate list size for this query (OpenSearch 2.16+); `exclude` leaves those
    fields out of the documents returned."""
    inner: dict[str, Any] = {"vector": list(vector), "k": k}
    after = filter is not None and (vf.engine == "nmslib" or vf.nested is not None)
    if filter is not None and not after:
        inner["filter"] = filter
    if ef_search:
        inner["method_parameters"] = {"ef_search": ef_search}
    query: dict[str, Any] = {"knn": {vf.path: inner}}
    if vf.nested:
        query = {"nested": {"path": vf.nested, "query": query, "score_mode": "max"}}
    if after:
        clauses = filter.get("bool", {}) if isinstance(filter, dict) and set(filter) == {"bool"} else {}
        if clauses and set(clauses) <= {"filter", "must_not"}:
            query = {"bool": {"must": [query], **clauses}}
        else:
            query = {"bool": {"must": [query], "filter": [filter]}}
    body: dict[str, Any] = {"size": k, "query": query}
    excluded = sorted(set(exclude))
    if excluded:
        body["_source"] = {"excludes": excluded}
    return body


def query_python(body: dict[str, Any], index: str) -> str:
    """A k-NN search body as opensearch-py code to copy, with the query vector as a variable."""
    import pprint

    shown = json.loads(json.dumps(body))

    def blank(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "vector" and isinstance(value, list):
                    node[key] = "__QUERY_VECTOR__"
                else:
                    blank(value)
        elif isinstance(node, list):
            for value in node:
                blank(value)

    blank(shown)
    text = pprint.pformat(shown, indent=1, width=100, sort_dicts=False).replace("'__QUERY_VECTOR__'", "query_vector")
    return f"body = {text}\nclient.search(index={index!r}, body=body)   # opensearch-py"


def describe_vector_field(vf: VectorField) -> str:
    """'1,024 dims · faiss HNSW · cosine' (· fp16, · on disk 32x when compressed)."""
    parts = [f"{vf.dimension:,} dims" if vf.dimension else "? dims"]
    if vf.model_id:
        parts.append(f"trained model {vf.model_id}")
    else:
        parts.append(f"{vf.engine or 'default engine'} {vf.algorithm.upper()}")
    parts.append(_SPACE_NAMES.get(vf.space, vf.space))
    if vf.data_type != "float":
        parts.append(f"{vf.data_type} vectors")
    if vf.encoder:
        parts.append(vf.encoder)
    if vf.mode == "on_disk" or vf.compression:
        parts.append(" ".join(filter(None, ["on disk" if vf.mode == "on_disk" else "", vf.compression or "32x"])))
    return " · ".join(parts)


def _ref(store: Any, index: str) -> str:
    """'vectors-prod/docs': how commands name an index in a domain, collection or URL."""
    if store is None:
        return index
    return f"{store.url if isinstance(store, Endpoint) else store.name}/{index}"


def _is_serverless(store: Any) -> bool:
    return isinstance(store, Collection) or (isinstance(store, Endpoint) and store.service == "aoss")


def _open_to_anyone(domain: Domain) -> bool:
    """The domain's access policy allows every principal, with no condition, and the domain isn't in a VPC."""
    if domain.vpc or not domain.access_policy:
        return False
    statements = domain.access_policy.get("Statement") or []
    for st in statements if isinstance(statements, list) else [statements]:
        if not isinstance(st, dict) or st.get("Effect") != "Allow" or st.get("Condition"):
            continue
        principal = st.get("Principal")
        aws = principal.get("AWS") if isinstance(principal, dict) else principal
        if aws == "*" or (isinstance(aws, list) and "*" in aws):
            return True
    return False


def domain_findings(domain: Domain, prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """Plain-language risks and cost notes for a domain, each with what to do about it -> [(level, message)]."""
    prices = OPENSEARCH_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    name = domain.name
    if domain.status and domain.status != "Active":
        found.append(("info", f"The domain is {_DOMAIN_STATES.get(domain.status, domain.status)}."))
    if _open_to_anyone(domain) and not domain.fine_grained:
        found.append((
            "warn",
            "Anyone on the internet can send requests to the domain: its access policy allows every principal, it "
            "isn't in a VPC and fine-grained access control is off. Limit the policy to your roles' ARNs: "
            f"aws opensearch update-domain-config --domain-name {name} --access-policies file://policy.json",
        ))
    if domain.engine == "Elasticsearch":
        found.append((
            "info",
            f"The domain runs Elasticsearch {domain.version}, whose k-NN plugin is the old Open Distro one: no "
            "filtering during the search and no vector quantization. OpenSearch 2.x has both; "
            f"aws opensearch get-compatible-versions --domain-name {name} lists the versions it can upgrade to.",
        ))
    elif version_at_least(domain.version, (2, 17)) is False:
        found.append((
            "info",
            f"OpenSearch {domain.version}: on-disk vectors and binary quantization, which cut k-NN memory up to 32x, "
            f"need 2.17 or later. aws opensearch get-compatible-versions --domain-name {name} lists the versions it "
            "can upgrade to.",
        ))
    spec = INSTANCE_TYPES.get(domain.instance_type)
    if spec and domain.instance_type.startswith("t"):
        found.append((
            "info",
            f"Data nodes are {domain.instance_type}, a burstable type with {spec[2]:g} GiB of memory, of which k-NN "
            f"graphs get about {human_size(knn_memory_limit(spec[2]))} per node: fine for trying things, small for "
            "real vector indexes. Memory-optimized types (r6g, r7g) suit vector search.",
        ))
    if domain.instance_count == 1:
        found.append((
            "info",
            "One data node: replica shards have nowhere to go (indexes that ask for replicas stay yellow), and losing "
            "the node takes the domain down. Fine for development; production domains run 2 or more nodes in "
            "different Availability Zones.",
        ))
    if domain.volume_type == "gp2" and domain.volume_gb:
        saving = (prices.get("gp2", 0) - prices.get("gp3", 0)) * domain.volume_gb * domain.instance_count
        found.append((
            "info",
            f"Storage is gp2: gp3 costs {human_money(saving)}/month less for these volumes and has a higher baseline "
            "(3,000 IOPS). Change the EBS volume type to gp3 in the console (Actions, Edit cluster configuration).",
        ))
    if domain.encrypted is False:
        found.append((
            "info",
            "Encryption at rest is off: index files, logs and automated snapshots are stored unencrypted. "
            f"aws opensearch update-domain-config --domain-name {name} --encryption-at-rest-options Enabled=true",
        ))
    if domain.https_only is False:
        found.append((
            "info",
            "The domain also answers plain HTTP, so requests (and any credentials in them) can travel unencrypted. "
            f"aws opensearch update-domain-config --domain-name {name} --domain-endpoint-options EnforceHTTPS=true",
        ))
    if domain.update_available:
        version = f" ({domain.update_version})" if domain.update_version else ""
        found.append((
            "info",
            f"A service software update{version} with fixes and security patches is waiting: "
            f"aws opensearch start-service-software-update --domain-name {name}",
        ))
    missing = [t for t in (domain.instance_type, domain.master_type, domain.warm_type) if t and t not in prices]
    if missing:
        found.append((
            "info",
            f"No list price for {', '.join(sorted(set(missing)))}, so the cost leaves it out: pass "
            f"OpenSearchAnalyzer(prices={{'{missing[0]}': <USD per hour>}}).",
        ))
    if domain.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in domain.errors.items()) + "."))
    return found


def collection_findings(collection: Collection) -> list[tuple[str, str]]:
    """Plain-language notes about a Serverless collection, each with what to do about it -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    c = collection
    if c.status == "FAILED":
        found.append((
            "warn",
            f"The collection failed to create{': ' + c.failure if c.failure else ''}. Create it again (and delete "
            "this one) in the console.",
        ))
    elif c.status and c.status != "ACTIVE":
        found.append(("info", f"The collection is {c.status.lower().replace('_', ' ')}."))
    if not c.standby:
        found.append((
            "info",
            "Standby replicas are off (a development setting): there's no copy in a second Availability Zone, so a "
            "zone outage takes the collection offline. It bills half the minimum (1 OCU instead of 2); production "
            "collections need standby replicas, which are chosen when a collection is created.",
        ))
    if c.network == "vpc":
        endpoints = ", ".join(c.vpc_endpoints) or "a VPC endpoint"
        found.append((
            "info",
            f"The collection only answers through {endpoints} (network policy {c.network_policy}): a notebook "
            "outside that VPC can't read its indexes. Run the notebook in the VPC, or allow its VPC endpoint in the "
            "network policy.",
        ))
    elif c.network == "aws services":
        found.append((
            "info",
            f"Only AWS services (such as Bedrock) can reach the collection (network policy {c.network_policy}), so "
            "this notebook can't read its indexes. Allow your VPC endpoint or public access in the network policy "
            "to look inside.",
        ))
    if c.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in c.errors.items()) + "."))
    return found


def index_findings(
    info: IndexInfo,
    store: Any = None,
    *,
    knn_memory: float | None = None,
    stats: KnnStats | None = None,
    version: str | None = None,
    data_nodes: int | None = None,
) -> list[tuple[str, str]]:
    """Plain-language risks for one index, each with what to do about it -> [(level, message)].

    knn_memory: the k-NN memory of the whole cluster in bytes (domain_knn_memory), to check the graphs fit;
    stats: the k-NN plugin's own numbers; version: the OpenSearch version, for which fixes are available;
    data_nodes: how many data nodes hold the shards."""
    found: list[tuple[str, str]] = []
    name = info.name
    serverless = _is_serverless(store)
    if info.health == "red":
        found.append((
            "warn",
            "Some primary shards aren't assigned (health red): searches miss their documents and writes to them "
            "fail. On a domain, the console's Cluster health tab and GET _cluster/allocation/explain say why.",
        ))
    elif info.health == "yellow":
        found.append((
            "info",
            "Some replica shards aren't assigned (health yellow): there's no spare copy if a node fails. Usually there "
            "are fewer data nodes than copies; add a node, or lower number_of_replicas.",
        ))
    if info.status == "close":
        found.append(("info", "The index is closed: it can't be searched until it's opened again."))
    if info.vectors:
        found += _vector_index_findings(info, store, knn_memory, stats, version, serverless)
        if not serverless and data_nodes and data_nodes > 1 and info.replicas == 0:
            found.append((
                "info",
                "No replicas: if a node fails, the index is unavailable until it's restored from a snapshot. One "
                "replica also spreads searches over two copies, at the cost of twice the vector memory: "
                f"client.indices.put_settings(index={name!r}, body={{'index': {{'number_of_replicas': 1}}}})",
            ))
    if info.shards and info.shards > 1 and info.primary_bytes is not None and info.primary_bytes < info.shards * GB:
        found.append((
            "info",
            f"{info.shards} primary shards for {human_size(info.primary_bytes)}: every search runs on each shard and "
            "merges the results, so an index this size is faster with one shard (aim for 10 to 50 GB a shard). The "
            "shard count is fixed when an index is created: reindex into one with fewer.",
        ))
    elif info.shards and info.primary_bytes and info.primary_bytes / info.shards > 50 * GB:
        found.append((
            "info",
            f"Shards average {human_size(info.primary_bytes / info.shards)}, above the 50 GB OpenSearch suggests: "
            "recovery and rebalancing get slow. Reindex into an index with more primary shards.",
        ))
    copies = (info.shards or 0) * info.copies
    if info.segments and copies and info.segments / copies > 50 and info.vectors:
        found.append((
            "info",
            f"{info.segments / copies:,.0f} segments per shard: a k-NN search walks every segment's graph, so many "
            "small segments slow it down. After bulk loading, merge them during a quiet hour: "
            f"client.indices.forcemerge(index={name!r}, max_num_segments=1)",
        ))
    if info.deleted and info.docs and info.deleted > 0.2 * (info.docs + info.deleted):
        found.append((
            "info",
            f"{info.deleted:,} deleted documents ({info.deleted / (info.docs + info.deleted):.0%}) still take disk "
            "and graph memory until their segments merge. Merging happens on its own as you write; a force merge "
            "(expunge_deletes) clears them at once.",
        ))
    if info.errors:
        found.append(("info", "Couldn't read " + ", ".join(f"{k} ({v})" for k, v in info.errors.items()) + "."))
    return found


def _vector_index_findings(
    info: IndexInfo,
    store: Any,
    knn_memory: float | None,
    stats: KnnStats | None,
    version: str | None,
    serverless: bool,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    name = info.name
    if any(f in info.fields for f in _BEDROCK_FIELDS):
        found.append((
            "info",
            "A Bedrock knowledge base writes this index (it has AMAZON_BEDROCK_* fields): change it through the "
            "knowledge base, which rewrites it on every sync. bedrock_kb.py's kbs() shows the knowledge base.",
        ))
    if info.knn is False and not serverless:
        found.append((
            "warn",
            "index.knn is off, so OpenSearch builds no search graphs for this index: a k-NN search either fails or "
            "compares the query with every vector, which gets slow as the index grows. It can only be set when an "
            "index is created: create one with index.knn true and the same mapping, then reindex into it.",
        ))
    for vf in info.vectors:
        if vf.engine == "nmslib":
            found.append((
                "warn",
                f"'{vf.path}' uses the nmslib engine, deprecated since OpenSearch 2.19; 3.0 can't create nmslib "
                "indexes. It also can't filter during the search. Reindex into a field with \"engine\": \"faiss\" "
                "(same space type) to keep upgrades open.",
            ))
        have, total = info.with_vector.get(vf.path), info.top_docs
        if have is not None and total and have < total:
            missing = total - have
            found.append((
                "warn",
                f"{missing:,} of {total:,} documents ({missing / total:.1%}) have no '{vf.path}', so vector search "
                "never returns them. They are usually documents whose embedding failed, or that were indexed before "
                "the field was added: "
                f"sample({_ref(store, name)!r}, where={{{vf.path!r}: ('missing',)}}) shows some, to re-embed.",
            ))
    need = index_vector_memory(info, native=True)
    if need and knn_memory:
        share = need / knn_memory
        used = f" (the k-NN plugin reports {human_size(stats.memory_bytes)} in use now)" if stats else ""
        if share > 1:
            found.append((
                "warn",
                f"The vector graphs need about {human_size(need)} of memory with replicas, more than the "
                f"{human_size(knn_memory)} the k-NN plugin can use on these data nodes{used}. Searches then load "
                "graphs from disk, which is slow, and indexing can trip the k-NN circuit breaker. Use bigger "
                "(memory-optimized) instances, more nodes, or compressed vectors (see the next note).",
            ))
        elif share > 0.6:
            found.append((
                "info",
                f"The vector graphs need about {human_size(need)} of memory with replicas, {share:.0%} of the "
                f"{human_size(knn_memory)} the k-NN plugin can use on these data nodes{used}: little room to grow.",
            ))
    plain = [
        vf for vf in info.vectors
        if vf.engine != "lucene" and vf.compression_factor == 1 and vf.dimension and not vf.model_id
    ]
    if need and need > GB and plain and not serverless:
        vf = plain[0]
        count = vector_count(info, vf) or 0
        now = (vector_memory(vf, count) or 0) * info.copies
        on_disk = VectorField(vf.path, vf.dimension, mode="on_disk", m=vf.m, method=vf.method)
        small = (vector_memory(on_disk, count) or 0) * info.copies
        upgrade = "" if version_at_least(version, (2, 17)) is not False else " (on OpenSearch 2.17 or later)"
        found.append((
            "info",
            f"'{vf.path}' keeps full 32-bit vectors in memory (about {human_size(now)} with replicas). "
            f"\"mode\": \"on_disk\"{upgrade} keeps 32x compressed copies in memory instead (about "
            f"{human_size(small)}) and rescores the best matches from disk, so recall stays close; \"data_type\": "
            "\"byte\" or an fp16 encoder halve it or better. The mapping can't change in place: create a new index "
            "and reindex.",
        ))
    return found


def store_findings(report: StoreReport, knn_memory: float | None = None) -> list[tuple[str, str]]:
    """Cluster-wide notes for indexes(): health, the k-NN plugin's memory, and whether all the vector graphs fit in
    `knn_memory` (bytes, all data nodes) -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    health = report.health
    if health and health.status == "red":
        found.append((
            "warn",
            f"Cluster health is red: {health.unassigned_shards:,} shards are unassigned, primaries among them, so "
            "some documents can't be searched and writes to them fail. GET _cluster/allocation/explain (Dev Tools) "
            "says why.",
        ))
    elif health and health.status == "yellow":
        found.append((
            "info",
            f"Cluster health is yellow: {health.unassigned_shards:,} replica shards are unassigned, so there's no "
            "spare copy if a node fails. Usually there are fewer data nodes than copies; add a node or lower "
            "number_of_replicas.",
        ))
    found += knn_findings(report.knn)
    need = sum(index_vector_memory(info, native=True) or 0 for info in report.vector_indexes)
    if need and knn_memory:
        if need > knn_memory:
            found.append((
                "warn",
                f"The vector indexes need about {human_size(need)} of k-NN memory with their replicas, more than the "
                f"{human_size(knn_memory)} these data nodes have (half of what the Java heap leaves). Searches then "
                "load graphs from disk, which is slow, and indexing can trip the circuit breaker. Use bigger "
                "memory-optimized instances, more nodes, or compressed vectors (index_info() shows the savings).",
            ))
        elif need > 0.6 * knn_memory:
            found.append((
                "info",
                f"The vector indexes need about {human_size(need)} of k-NN memory with their replicas, "
                f"{need / knn_memory:.0%} of the {human_size(knn_memory)} these data nodes have: little room to grow.",
            ))
    return found


def knn_findings(stats: KnnStats | None) -> list[tuple[str, str]]:
    """What the k-NN plugin's numbers mean for the whole cluster: a tripped circuit breaker, a full graph cache."""
    found: list[tuple[str, str]] = []
    if stats is None:
        return found
    if stats.circuit_breaker:
        found.append((
            "warn",
            "The cluster's k-NN circuit breaker has tripped: graph memory hit its limit, so new vector indexing is "
            "refused until memory frees up. Free memory (delete or close unused vector indexes, compress vectors) or "
            "add memory.",
        ))
    elif stats.memory_percent is not None and stats.memory_percent >= 90:
        found.append((
            "warn",
            f"Graph memory on the cluster's fullest node is {stats.memory_percent:.0f}% of its k-NN limit: the next "
            "graphs loaded push older ones out, and indexing can trip the k-NN circuit breaker. Compress vectors, or "
            "add memory.",
        ))
    if stats.evictions or stats.cache_full:
        found.append((
            "info",
            f"The cluster's k-NN cache has filled up and dropped graphs {stats.evictions:,} times: searches on a "
            "dropped graph load it from disk first, which is slow. The graphs need more memory than the nodes have.",
        ))
    return found


def vector_findings(check: VectorCheck, vf: VectorField | None = None) -> list[tuple[str, str]]:
    """Plain-language notes about a sample of vectors (check_vectors), given the field's settings -> [(level, msg)]."""
    found: list[tuple[str, str]] = []
    path = check.field
    if not check.vectors:
        if check.docs:
            found.append((
                "info",
                f"None of the {check.docs:,} sampled documents has '{path}' in what OpenSearch returns (the mapping "
                "can leave vectors out of _source), so the vectors couldn't be checked.",
            ))
        return found
    if check.bad:
        found.append((
            "warn",
            f"{check.bad:,} of {check.vectors:,} sampled vectors hold NaN, infinity or values that aren't numbers: "
            "the embedding step produced garbage for those documents. Re-embed them.",
        ))
    if len(check.dims) > 1:
        sizes = ", ".join(f"{d:,} ({n:,})" for d, n in check.dims.most_common())
        found.append(("warn", f"Vectors of different lengths in the sample: {sizes}. Re-embed the odd ones."))
    if check.zeros:
        cosine = vf is not None and vf.space == "cosinesimil"
        found.append((
            "warn" if cosine else "info",
            f"{check.zeros:,} sampled vectors are all zeros"
            + (", which cosine similarity can't compare, so those documents never match" if cosine else "")
            + ". They usually come from empty text or an embedding call that failed: re-embed those documents.",
        ))
    if check.repeats:
        share = check.repeats / check.vectors
        found.append((
            "warn" if share > 0.05 else "info",
            f"{check.repeats:,} of {check.vectors:,} sampled vectors ({share:.0%}) repeat another one exactly: the "
            "same text was indexed more than once (overlapping loads, a document added twice). Copies crowd other "
            "results out of the top k; search(like=...) on one of them shows its copies.",
        ))
    spread = (
        check.norm_min is not None and check.norm_max is not None and check.norm_min > 0
        and check.norm_max / check.norm_min > 1.5
    )
    if vf is not None and spread and not check.unit_length:
        lengths = f"{check.norm_min:.2f} to {check.norm_max:.2f}"
        if vf.space == "innerproduct":
            found.append((
                "info",
                f"Vector lengths run from {lengths}, and the index ranks by inner product, which favours long vectors "
                "over close ones. Unless your embedding model is made for inner product, normalize vectors before "
                "indexing, or use \"space_type\": \"cosinesimil\".",
            ))
        elif vf.space == "l2":
            found.append((
                "info",
                f"Vector lengths run from {lengths}, and L2 distance counts length as well as direction. Most "
                "embedding models are meant to be compared by cosine: normalize vectors before indexing, or use "
                "\"space_type\": \"cosinesimil\".",
            ))
    return found


def search_findings(
    result: SearchResult, vf: VectorField, hit_norms: list[float] | None = None
) -> list[tuple[str, str]]:
    """Notes on one k-NN search: too few results, repeated texts, a query that doesn't look like the stored vectors."""
    found: list[tuple[str, str]] = []
    if result.where and len(result.hits) < result.k:
        late = vf.engine == "nmslib" or vf.nested is not None
        found.append((
            "info",
            f"{len(result.hits)} of {result.k} results: few documents near the query match {describe_filter(result.where)}."
            + (" This index filters after the search, keeping only those of the k nearest that match: a bigger k "
               "finds more." if late else ""),
        ))
    texts: dict[str, int] = {}
    for hit in result.hits:
        text = next(iter(values_at(hit.source, result.text_field)), None) if result.text_field else None
        if isinstance(text, str) and text.strip():
            if text in texts:
                found.append((
                    "info",
                    f"Results {texts[text]} and {hit.rank} have the same text: the document is indexed more than once. "
                    "sample() counts repeated vectors across the index.",
                ))
                break
            texts[text] = hit.rank
    if result.query_norm is not None and hit_norms and vf.space in ("innerproduct", "l2"):
        unit_docs = all(0.99 <= n <= 1.01 for n in hit_norms)
        if unit_docs and not 0.98 <= result.query_norm <= 1.02:
            found.append((
                "warn",
                f"The stored vectors have length 1 but the query's is {result.query_norm:.2f}, and this index ranks by "
                f"{_SPACE_NAMES[vf.space]}, which depends on length: the query probably came from another model or "
                "wasn't normalized. Normalize it (divide by its length), and check it's the model that embedded the "
                "documents.",
            ))
    return found


# =============================================================================
# 4. OpenSearchAnalyzer - pure logic layer (talks to AWS, returns data)
# =============================================================================

_READ_POSTS = re.compile(r"/_(?:search|count)$")  # the only paths a body is sent to (searches): nothing else is POSTed


class OpenSearchError(Exception):
    """A request to an OpenSearch endpoint that failed. `status` is the HTTP status (None when the endpoint couldn't
    be reached), `kind` the error type OpenSearch gave (index_not_found_exception, security_exception, ...) and
    `reason` its message."""

    def __init__(self, store: Any, path: str, status: int | None, kind: str, reason: str):
        self.store, self.path, self.status, self.kind, self.reason = store, path, status, kind, reason
        super().__init__(f"{status or 'no answer'} {kind}: {reason} ({path})")


def _error_details(status: int, content: bytes) -> tuple[str, str]:
    """(error type, reason) from an OpenSearch error answer, whichever shape it has."""
    text = (content or b"").decode("utf-8", "replace").strip()
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        return f"HTTP {status}", _clip(text, 300) or "no message"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            causes = error.get("root_cause")
            root = causes[0] if isinstance(causes, list) and causes and isinstance(causes[0], dict) else {}
            kind = error.get("type") or root.get("type") or f"HTTP {status}"
            return str(kind), _clip(str(error.get("reason") or root.get("reason") or ""), 500)
        message = error if isinstance(error, str) else body.get("Message") or body.get("message")
        if message:
            return f"HTTP {status}", _clip(str(message), 500)
    return f"HTTP {status}", _clip(text, 300)


def _path(name: str) -> str:
    """An index name for a URL path (commas and wildcards kept)."""
    return quote(name, safe=",*")


def _store_url(store: Any) -> str | None:
    return store.url


def _as_vector(value: Any) -> list[float]:
    """A query vector from a list, tuple or numpy array -> list of floats."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]  # a batch of one, as embedding functions often return
    try:
        vector = [float(x) for x in value]
    except (TypeError, ValueError):
        raise ValueError("vector= takes a list of numbers (a list, tuple or numpy array)") from None
    if not vector:
        raise ValueError("The query vector is empty")
    return vector


def _exists(vf: VectorField) -> dict[str, Any]:
    """Query matching documents that have a vector in `vf` (through its nested field when it's inside one)."""
    query = {"exists": {"field": vf.path}}
    return {"nested": {"path": vf.nested, "query": query}} if vf.nested else query


def _drop(source: dict[str, Any], path: str) -> None:
    """Remove a (possibly dotted) field from a document, so vectors don't fill the table of results."""
    if path in source:
        source.pop(path)
        return
    head, _, rest = path.partition(".")
    if rest and isinstance(source.get(head), dict):
        _drop(source[head], rest)


def _base_model(model_id: str) -> str:
    """An inference profile's model: 'us.cohere.embed-v4:0' -> 'cohere.embed-v4:0'."""
    return re.sub(r"^(?:us|eu|apac|global|us-gov|ca|jp|au)\.", "", model_id)


def _embedding_style(model_id: str) -> str:
    """How to call a Bedrock embedding model, from its ID (inference profiles such as 'us.cohere.embed-v4:0' too)."""
    known = EMBEDDING_MODELS.get(_base_model(model_id))
    if known:
        return known[0]
    for marker, style in (
        ("titan-embed-text-v2", "titan-v2"),
        ("titan-embed-text-v1", "titan"),
        ("titan-embed-image", "titan-image"),
        ("cohere.embed-v4", "cohere-v4"),
        ("cohere.embed", "cohere"),
    ):
        if marker in model_id:
            return style
    raise ValueError(
        f"Can't tell how to call {model_id!r}: this file knows Titan and Cohere embedding models. For another model, "
        "pass embed=your_function (text -> list of floats) or vector=[...]."
    )


def _embedding_request(style: str, text: str, dimension: int | None) -> dict[str, Any]:
    if style == "titan-v2":
        return {"inputText": text, **({"dimensions": dimension} if dimension else {})}
    if style == "titan-image":
        return {"inputText": text, **({"embeddingConfig": {"outputEmbeddingLength": dimension}} if dimension else {})}
    if style == "cohere-v4":
        extra = {"output_dimension": dimension} if dimension else {}
        return {"texts": [text], "input_type": "search_query", "embedding_types": ["float"], **extra}
    if style == "cohere":
        return {"texts": [text], "input_type": "search_query"}
    return {"inputText": text}


def _embedding_from(payload: dict[str, Any]) -> list[float]:
    """The vector in a Bedrock embedding answer (Titan's 'embedding', Cohere's 'embeddings', by type or not)."""
    if isinstance(payload.get("embedding"), list):
        return payload["embedding"]
    found = payload.get("embeddings")
    if isinstance(found, dict):
        found = found.get("float")
    if isinstance(found, list) and found and isinstance(found[0], list):
        return found[0]
    by_type = payload.get("embeddingsByType")
    if isinstance(by_type, dict) and isinstance(by_type.get("float"), list):
        return by_type["float"]
    raise ValueError("The embedding model's answer holds no vector")


class OpenSearchAnalyzer:
    """Pure-logic OpenSearch analysis: every method returns data; nothing is printed or written.

    Domains and Serverless collections are found through the AWS APIs. Their indexes are read through each one's own
    REST endpoint, with requests signed with your AWS credentials (SigV4), or with a user name and password when you
    pass auth=('user', 'password') (fine-grained access control's internal users, or an OpenSearch you run yourself).
    Only GET requests are sent, plus POSTs to _search and _count; anything else is refused before it's sent.

    Methods that read an index take `target` (a domain or collection name, a collection ID, an ARN or an endpoint URL)
    and `index`. `prices` overrides OPENSEARCH_PRICES. `client` is the OpenSearch Service client and `clients` other
    boto3 clients by service name ('opensearchserverless', 'cloudwatch', 'sts', 'bedrock-runtime'). `http` replaces
    the HTTP transport: a function (method, url, body, headers) -> (status, body bytes), e.g. for tests.
    """

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        auth: tuple[str, str] | None = None,
        http: Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]] | None = None,
        timeout: float | tuple[float, float] = (5, 60),
    ):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["opensearch"] = client
        self.prices = {**OPENSEARCH_PRICES, **(prices or {})}
        self.auth = auth
        self.timeout = timeout
        self.max_workers = 8
        self._transport = http or self._send
        self._http_session: Any = None
        self._domains: dict[str, Domain] = {}
        self._collections: dict[str, Collection] = {}  # by name and by ID

    # ------------------------------------------------------------------ clients

    def _service(self, name: str) -> Any:
        """A boto3 client, made on first use (or the one passed in client= / clients=)."""
        if name not in self._clients:
            makers = {  # named one by one so the project's read-only check can see which services are used
                "opensearch": lambda: self.session.client("opensearch", config=self._config),
                "opensearchserverless": lambda: self.session.client("opensearchserverless", config=self._config),
                "cloudwatch": lambda: self.session.client("cloudwatch", region_name=self.region),
                "sts": lambda: self.session.client("sts", region_name=self.region),
                "bedrock-runtime": lambda: self.session.client(
                    "bedrock-runtime", region_name=self.region, config=self._config
                ),
            }
            try:
                self._clients[name] = makers[name]()
            except NoRegionError:
                raise ValueError(
                    "No AWS region is set, and OpenSearch domains and collections are regional. Pass one: "
                    "OpenSearchView(OpenSearchAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                ) from None
        return self._clients[name]

    @property
    def client(self) -> Any:
        """The OpenSearch Service client (domains)."""
        return self._service("opensearch")

    @property
    def region(self) -> str:
        return self.client.meta.region_name

    # ---------------------------------------------------------- domains and collections

    def domain_names(self) -> list[str]:
        """Every OpenSearch Service domain in the region (OpenSearch and Elasticsearch)."""
        return sorted(d["DomainName"] for d in self.client.list_domain_names().get("DomainNames", []))

    def list_domains(
        self, names: list[str] | None = None, *, progress: Callable[..., None] | None = None
    ) -> list[Domain]:
        """Every domain's configuration (DescribeDomains, five domains a call). A domain that can't be described
        comes back with errors['describe'] set."""
        names = self.domain_names() if names is None else names
        chunks = [names[i : i + 5] for i in range(0, len(names), 5)]

        def describe(chunk: list[str]) -> list[Domain]:
            try:
                found = self.client.describe_domains(DomainNames=chunk).get("DomainStatusList", [])
            except (ClientError, BotoCoreError) as exc:
                return [Domain(name, errors={"describe": _error_name(exc)}) for name in chunk]
            domains = [parse_domain(status) for status in found]
            seen = {d.name for d in domains}
            return domains + [Domain(n, errors={"describe": "not returned"}) for n in chunk if n not in seen]

        domains: list[Domain] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for part in pool.map(describe, chunks):
                domains += part
                if progress:
                    progress(len(domains), len(names))
        for d in domains:
            if "describe" not in d.errors:
                self._domains[d.name] = d
        return domains

    def domain(self, name: str, *, refresh: bool = False) -> Domain:
        """One domain's configuration (DescribeDomain, cached)."""
        if refresh or name not in self._domains:
            self._domains[name] = parse_domain(self.client.describe_domain(DomainName=name)["DomainStatus"])
        return self._domains[name]

    def _serverless_client(self) -> Any:
        return self._service("opensearchserverless")

    def collection_summaries(self) -> list[dict[str, Any]]:
        """Every Serverless collection in the region: ListCollections' id, name, status and ARN."""
        found: list[dict[str, Any]] = []
        token = None
        while True:
            resp = self._serverless_client().list_collections(**({"nextToken": token} if token else {}))
            found += resp.get("collectionSummaries", [])
            token = resp.get("nextToken")
            if not token:
                return found

    def list_collections(self, *, network: bool = True) -> list[Collection]:
        """Every Serverless collection with its endpoint and settings (BatchGetCollection, 100 a call), and with
        network=True which network policy covers it. Parts that can't be read are in each collection's errors."""
        summaries = self.collection_summaries()
        ids = [s["id"] for s in summaries if s.get("id")]
        details: dict[str, dict[str, Any]] = {}
        failed: dict[str, str] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            try:
                resp = self._serverless_client().batch_get_collection(ids=chunk)
            except (ClientError, BotoCoreError) as exc:
                failed.update(dict.fromkeys(chunk, _error_name(exc)))
                continue
            details.update({d["id"]: d for d in resp.get("collectionDetails", []) if d.get("id")})
        collections = []
        for summary in summaries:
            c = parse_collection(details.get(summary.get("id", ""), summary))
            if summary.get("id") in failed:
                c.errors["describe"] = failed[summary["id"]]
            collections.append(c)
        if network and collections:
            self._add_network(collections)
        for c in collections:
            self._collections[c.name] = self._collections[c.id] = c
        return collections

    def collection(self, name: str, *, refresh: bool = False) -> Collection:
        """One collection by name or ID (BatchGetCollection, cached), with its network policy."""
        if not refresh and name in self._collections:
            return self._collections[name]
        details = self._serverless_client().batch_get_collection(names=[name]).get("collectionDetails") or []
        if not details and re.fullmatch(r"[a-z0-9]{3,40}", name):
            details = self._serverless_client().batch_get_collection(ids=[name]).get("collectionDetails") or []
        if not details:
            raise ValueError(f"No Serverless collection named {name!r} in {self.region}")
        found = parse_collection(details[0])
        self._add_network([found])
        self._collections[found.name] = self._collections[found.id] = found
        return found

    def network_policies(self) -> list[tuple[str, Any]]:
        """Every Serverless network policy: (name, policy document)."""
        names: list[str] = []
        token = None
        while True:
            resp = self._serverless_client().list_security_policies(type="network", **({"nextToken": token} if token else {}))
            names += [p["name"] for p in resp.get("securityPolicySummaries", [])]
            token = resp.get("nextToken")
            if not token:
                break
        return [
            (name, self._serverless_client().get_security_policy(name=name, type="network")["securityPolicyDetail"].get("policy"))
            for name in names
        ]

    def _add_network(self, collections: list[Collection]) -> None:
        try:
            policies = self.network_policies()
        except (ClientError, BotoCoreError) as exc:
            for c in collections:
                c.errors["network"] = _error_name(exc)
            return
        for c in collections:
            c.network, c.network_policy, c.vpc_endpoints = network_access(policies, c.name)

    def serverless_capacity(self) -> tuple[float | None, float | None]:
        """The account's Serverless capacity limits: (most indexing OCUs, most search OCUs) it may scale to."""
        detail = self._serverless_client().get_account_settings().get("accountSettingsDetail") or {}
        limits = detail.get("capacityLimits") or {}
        return limits.get("maxIndexingCapacityInOCU"), limits.get("maxSearchCapacityInOCU")

    def serverless_ocus(self, hours: int = 24) -> dict[str, float] | None:
        """Average indexing and search OCUs the account's Serverless collections used over the last `hours`
        (CloudWatch AWS/AOSS IndexingOCU and SearchOCU) -> {'indexing': ..., 'search': ...}; None without data.
        Collections in a collection group report their OCUs separately and aren't included."""
        account = self._service("sts").get_caller_identity()["Account"]
        names = ("IndexingOCU", "SearchOCU")
        queries = [
            {
                "Id": f"m{i}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/AOSS",
                        "MetricName": name,
                        "Dimensions": [{"Name": "ClientId", "Value": account}],
                    },
                    "Period": 3600,
                    "Stat": "Average",
                },
            }
            for i, name in enumerate(names)
        ]
        now = _utcnow()
        values: dict[int, list[float]] = {0: [], 1: []}
        for page in (
            self._service("cloudwatch")
            .get_paginator("get_metric_data")
            .paginate(MetricDataQueries=queries, StartTime=now - timedelta(hours=hours), EndTime=now)
        ):
            for series in page["MetricDataResults"]:
                values[int(series["Id"][1:])] += series.get("Values", [])
        if not values[0] and not values[1]:
            return None
        return {
            "indexing": sum(values[0]) / len(values[0]) if values[0] else 0.0,
            "search": sum(values[1]) / len(values[1]) if values[1] else 0.0,
        }

    def overview(
        self, *, match: str | None = None, metrics: bool = True, progress: Callable[..., None] | None = None
    ) -> Overview:
        """Every domain and Serverless collection in the region (match='prod-*' keeps matching names), the account's
        Serverless capacity limits, and with metrics=True the OCUs it used in the last 24 hours (CloudWatch).
        Lists that can't be read are recorded in `errors`."""
        result = Overview(region=self.region)

        def wanted(name: str) -> bool:
            return match is None or fnmatch.fnmatchcase(name, match)

        try:
            names = [n for n in self.domain_names() if wanted(n)]
            result.domains = self.list_domains(names, progress=progress)
        except (ClientError, BotoCoreError) as exc:
            result.errors["domains"] = _error_name(exc)
        try:
            result.collections = [c for c in self.list_collections() if wanted(c.name)]
        except (ClientError, BotoCoreError) as exc:
            result.errors["collections"] = _error_name(exc)
        if result.collections:
            try:
                result.max_indexing_ocus, result.max_search_ocus = self.serverless_capacity()
            except (ClientError, BotoCoreError) as exc:
                result.errors["capacity"] = _error_name(exc)
            if metrics:
                try:
                    result.ocus = self.serverless_ocus(result.hours)
                except (ClientError, BotoCoreError) as exc:
                    result.errors["usage"] = _error_name(exc)
        return result

    def resolve(self, target: Any) -> Domain | Collection | Endpoint:
        """A domain, Serverless collection or other OpenSearch from its name, collection ID, ARN or endpoint URL ('name/index'
        works too; the index is ignored). A Domain, Collection or Endpoint passes through. Raises ValueError, with the
        closest names, when there's no such domain or collection."""
        if isinstance(target, (Domain, Collection, Endpoint)):
            return target
        head, _ = parse_location(target)
        if re.match(r"^https?://", head, re.IGNORECASE):
            return self._endpoint(head)
        arn = _ARN_RE.match(head)
        if arn:
            return self.domain(arn.group(5)) if arn.group(4) == "domain" else self.collection(arn.group(5))
        head = head.lower()  # domain and collection names (and collection IDs) are lowercase only
        if head in self._domains:
            return self._domains[head]
        if head in self._collections:
            return self._collections[head]
        problems: list[str] = []
        try:
            return self.domain(head)
        except ClientError as exc:
            if _error_code(exc) not in ("ResourceNotFoundException", "ValidationException"):
                problems.append(f"domains: {_why(_error_code(exc), 'es:DescribeDomain')}")
        except BotoCoreError as exc:
            problems.append(f"domains: {type(exc).__name__}")
        try:
            return self.collection(head)
        except ValueError:
            pass
        except ClientError as exc:
            problems.append(f"collections: {_why(_error_code(exc), 'aoss:BatchGetCollection')}")
        except BotoCoreError as exc:
            problems.append(f"collections: {type(exc).__name__}")
        raise ValueError(self._not_found(head, problems))

    def _not_found(self, name: str, problems: list[str]) -> str:
        names: list[str] = []
        for lister in (self.domain_names, lambda: [s.get("name", "") for s in self.collection_summaries()]):
            try:
                names += lister()
            except (ClientError, BotoCoreError):
                pass
        close = [n for n in names if n.lower() == name.lower()] or difflib.get_close_matches(name, names, n=3)
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        couldnt = f" (couldn't check {'; '.join(problems)})" if problems else ""
        return (
            f"No domain or Serverless collection named {name!r} in {self.region}{couldnt}.{hint} overview() lists "
            "them; they're regional. An OpenSearch you run yourself works by URL, like indexes('https://host:9200')."
        )

    def _endpoint(self, url: str) -> Domain | Collection | Endpoint:
        """A URL -> the domain or collection it belongs to when this analyzer knows it, else an Endpoint."""
        host = (urlsplit(url).hostname or "").lower()
        for d in self._domains.values():
            if d.endpoint and d.endpoint.lower() == host:
                return d
        for c in self._collections.values():
            if c.endpoint and (urlsplit(c.endpoint).hostname or "").lower() == host:
                return c
        service, region = endpoint_service(url)
        try:
            if service == "aoss":  # a collection's endpoint starts with its ID
                return self.collection(host.split(".")[0])
            if service == "es":  # a domain's endpoint: name it when it's one of this region's domains
                for d in self.list_domains():
                    if d.endpoint and d.endpoint.lower() == host:
                        return d
        except (ClientError, BotoCoreError, ValueError):
            pass  # not one this account can see: still usable by URL
        if service and region is None:
            region = self.session.region_name
        return Endpoint(url.rstrip("/"), service, region)

    # ------------------------------------------------------------- REST requests

    def _send(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        """Send one request with botocore's HTTP client, through the proxies and CA bundle boto3 would use."""
        if self._http_session is None:
            core = getattr(self.session, "_session", None)
            bundle = core.get_config_variable("ca_bundle") if hasattr(core, "get_config_variable") else None
            self._http_session = URLLib3Session(
                verify=bundle or os.environ.get("REQUESTS_CA_BUNDLE") or True,
                proxies=get_environ_proxies(url),
                timeout=self.timeout,
                max_pool_connections=max(10, self.max_workers),
            )
        response = self._http_session.send(AWSRequest(method=method, url=url, data=body, headers=headers).prepare())
        return response.status_code, response.content

    def _headers(self, store: Any, method: str, url: str, body: bytes | None) -> dict[str, str]:
        """Request headers: SigV4 for AWS endpoints (the 'es' or 'aoss' service), Basic auth with auth=."""
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.auth is not None:
            token = base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
            return {**headers, "Authorization": f"Basic {token}"}
        if isinstance(store, Domain):
            service, region = "es", self.region
        elif isinstance(store, Collection):
            service, region = "aoss", self.region
        else:
            service, region = store.service, store.region
        if not service:
            return headers
        credentials = self.session.get_credentials()
        if credentials is None:
            raise ValueError(
                f"No AWS credentials to sign requests to {store.label}. On SageMaker the notebook's role provides "
                "them; elsewhere run aws configure, or pass OpenSearchAnalyzer(profile='...')."
            )
        request = AWSRequest(method=method, url=url, data=body or b"", headers=headers)
        request.headers["X-Amz-Content-SHA256"] = hashlib.sha256(body or b"").hexdigest()  # Serverless requires it
        SigV4Auth(credentials.get_frozen_credentials(), service, region or self.region).add_auth(request)
        return dict(request.headers.items())

    def request(self, target: Any, path: str, body: Any = None, *, params: dict[str, Any] | None = None) -> Any:
        """GET `path` from a domain, collection or URL -> the parsed JSON answer. With `body`, POSTs it, which only
        _search and _count accept: anything that could change data is refused before it's sent."""
        store = self.resolve(target)
        base = _store_url(store)
        if not base:
            state = getattr(store, "status", "") or "not ready"
            raise ValueError(f"{store.label} has no endpoint yet ({state}).")
        clean = "/" + path.lstrip("/")
        method, data = "GET", None
        if body is not None:
            if not _READ_POSTS.search(clean):
                raise ValueError(f"OpenSearchAnalyzer only reads: it sends a body only to _search and _count, not {clean}")
            method, data = "POST", json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = base.rstrip("/") + clean + ("?" + urlencode(params) if params else "")
        headers = self._headers(store, method, url, data)
        try:
            status, content = self._transport(method, url, data, headers)
        except BotoCoreError as exc:
            raise OpenSearchError(store, clean, None, type(exc).__name__, str(exc)) from exc
        if status >= 400:
            kind, reason = _error_details(status, content)
            raise OpenSearchError(store, clean, status, kind, reason)
        try:
            return json.loads(content) if content else None
        except ValueError:
            text = _clip((content or b"").decode("utf-8", "replace"), 200)
            raise OpenSearchError(store, clean, status, "not JSON", text) from None

    def _search(self, store: Any, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request(store, f"{_path(index)}/_search", body) or {}

    def _count(self, store: Any, index: str, query: dict[str, Any]) -> int:
        return int((self.request(store, f"{_path(index)}/_count", {"query": query}) or {}).get("count", 0))

    # ------------------------------------------------------------------- indexes

    def cat_indices(self, target: Any) -> list[IndexInfo]:
        """Every index with its health, documents, size, shards and replicas (GET _cat/indices)."""
        rows = self.request(target, "_cat/indices", params={"format": "json", "bytes": "b"})
        return [index_from_cat(row) for row in rows or [] if isinstance(row, dict)]

    def _with_mapping(self, info: IndexInfo, mapping: dict[str, Any]) -> IndexInfo:
        info.mapping = mapping.get("mappings", mapping) if isinstance(mapping, dict) else {}
        info.fields, info.vectors, info.source_excludes = parse_mapping(mapping or {})
        info.unindexed = unindexed_fields(info.mapping)
        info.text_field = guess_text_field({k: v for k, v in info.fields.items() if k not in info.unindexed})
        return info

    def _counts(self, store: Any, info: IndexInfo) -> None:
        """Top-level documents, and how many have each vector field (one _count each)."""
        try:
            info.top_docs = self._count(store, info.name, {"match_all": {}})
            for vf in info.vectors:
                info.with_vector[vf.path] = self._count(store, info.name, _exists(vf))
        except OpenSearchError as exc:
            info.errors["counts"] = f"{exc.status or ''} {exc.kind}".strip()

    def index(self, target: Any, index: str, *, counts: bool = True) -> IndexInfo:
        """Everything about one index: its counts and size (_cat/indices), mapping (fields and vector fields) and
        settings, and with counts=True how many documents have each vector field and (outside Serverless) its
        segments. Parts that can't be read are recorded in `errors`; an index that doesn't exist raises
        OpenSearchError (404)."""
        store = self.resolve(target)
        info = IndexInfo(index)
        try:
            rows = self.request(store, f"_cat/indices/{_path(index)}", params={"format": "json", "bytes": "b"})
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
                info = index_from_cat(rows[0])
        except OpenSearchError as exc:
            if exc.status in (None, 401, 403) or exc.kind == "index_not_found_exception":
                raise
            info.errors["_cat/indices"] = f"{exc.status} {exc.kind}"
        mappings = self.request(store, f"{_path(index)}/_mapping") or {}
        real = info.name if info.name in mappings else next(iter(mappings), index)
        info.name = real
        self._with_mapping(info, mappings.get(real) or {})
        try:
            settings = self.request(store, f"{_path(real)}/_settings", params={"flat_settings": "true"}) or {}
            read_settings(info, (settings.get(real) or next(iter(settings.values()), {})).get("settings", {}))
        except OpenSearchError as exc:
            info.errors["settings"] = f"{exc.status or ''} {exc.kind}".strip()
        if counts and info.vectors:
            self._counts(store, info)
            if not _is_serverless(store):
                try:
                    stats = self.request(store, f"{_path(real)}/_stats/segments") or {}
                    total = (stats.get("indices", {}).get(real) or stats.get("_all") or {}).get("total", {})
                    info.segments = _int(total.get("segments", {}).get("count"))
                except OpenSearchError as exc:
                    info.errors["segments"] = f"{exc.status or ''} {exc.kind}".strip()
        return info

    def indexes(
        self,
        target: Any,
        *,
        hidden: bool = False,
        details: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> StoreReport:
        """Every index in a domain, collection or cluster with its mapping and settings, and with details=True the
        documents that have each vector field, plus (outside Serverless) segment counts, cluster health and the k-NN
        plugin's memory use. hidden=True includes system indexes (names starting with a dot). Parts that can't be
        read are recorded in `errors`."""
        store = self.resolve(target)
        report = StoreReport(store)
        infos: dict[str, IndexInfo] = {}
        listed = False
        try:
            infos, listed = {i.name: i for i in self.cat_indices(store)}, True
        except OpenSearchError as exc:
            if exc.status in (None, 401, 403):
                raise
            report.errors["_cat/indices"] = f"{exc.status} {exc.kind}"
        if listed and not infos:
            return report
        mappings = self._bulk(store, "_mapping", list(infos), {}, report)
        for name in mappings:
            infos.setdefault(name, IndexInfo(name))
        names = sorted(n for n in infos if hidden or not n.startswith("."))
        settings = self._bulk(store, "_settings", names, {"flat_settings": "true"}, report)
        for name in names:
            info = self._with_mapping(infos[name], mappings.get(name) or {})
            if name in settings:
                read_settings(info, (settings[name] or {}).get("settings", {}))
            elif "_settings" in report.errors:
                info.errors.setdefault("settings", report.errors["_settings"])
            report.indexes.append(info)
        if not details:
            return report
        vector_indexes = report.vector_indexes
        done = [0]

        def count(info: IndexInfo) -> None:
            self._counts(store, info)
            done[0] += 1

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(count, info) for info in vector_indexes]
            for future in futures:
                future.result()
                if progress:
                    progress(done[0], len(vector_indexes))
        if not _is_serverless(store):
            self._cluster_details(store, report)
        return report

    def _bulk(
        self, store: Any, what: str, names: list[str], params: dict[str, str], report: StoreReport
    ) -> dict[str, Any]:
        """GET /_mapping or /_settings for every index in one call; when that's refused (Serverless), one call per
        index. -> index name -> its answer."""
        try:
            found = self.request(store, what, params=params or None) or {}
            if isinstance(found, dict):
                return found
        except OpenSearchError as exc:
            if exc.status in (None, 401) or not names:
                raise
        out: dict[str, Any] = {}

        def one(name: str) -> None:
            try:
                answer = self.request(store, f"{_path(name)}/{what}", params=params or None) or {}
                out[name] = answer.get(name) or next(iter(answer.values()), {})
            except OpenSearchError as exc:
                report.errors.setdefault(what, f"{exc.status or ''} {exc.kind}".strip())

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(one, names))
        return out

    def _cluster_details(self, store: Any, report: StoreReport) -> None:
        """Segment counts, cluster health and k-NN memory, each optional."""
        try:
            stats = self.request(store, "_stats/segments") or {}
            for info in report.indexes:
                total = (stats.get("indices", {}).get(info.name) or {}).get("total", {})
                info.segments = _int(total.get("segments", {}).get("count"))
        except OpenSearchError as exc:
            report.errors["segments"] = f"{exc.status or ''} {exc.kind}".strip()
        try:
            report.health = self.cluster_health(store)
        except OpenSearchError as exc:
            report.errors["health"] = f"{exc.status or ''} {exc.kind}".strip()
        if report.vector_indexes:
            try:
                report.knn = self.knn_stats(store)
            except OpenSearchError as exc:
                report.errors["k-NN stats"] = f"{exc.status or ''} {exc.kind}".strip()

    def cluster_health(self, target: Any) -> ClusterHealth:
        """GET _cluster/health (not on Serverless)."""
        resp = self.request(target, "_cluster/health") or {}
        return ClusterHealth(
            status=str(resp.get("status", "")),
            nodes=int(resp.get("number_of_nodes") or 0),
            data_nodes=int(resp.get("number_of_data_nodes") or 0),
            active_shards=int(resp.get("active_shards") or 0),
            unassigned_shards=int(resp.get("unassigned_shards") or 0),
        )

    def knn_stats(self, target: Any) -> KnnStats:
        """The k-NN plugin's memory and cache numbers (GET _plugins/_knn/stats; _opendistro on Elasticsearch)."""
        try:
            resp = self.request(target, "_plugins/_knn/stats")
        except OpenSearchError as exc:
            if exc.status not in (400, 404):
                raise
            resp = self.request(target, "_opendistro/_knn/stats")
        return parse_knn_stats(resp or {})

    # --------------------------------------------------------------- documents

    def sample(
        self, target: Any, index: str, n: int = 10, *, check: int = 200, where: Any = None
    ) -> Sample:
        """Random documents from an index: max(n, check) of them (at most 1,000), with each vector field's vectors
        checked (dimensions, lengths, zeros, repeats). where= narrows the sample (see build_filter). Falls back to
        the first documents when the endpoint can't sample at random."""
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        size = max(1, min(1000, max(_as_int(n, "n"), _as_int(check, "check"))))
        _check_where(where, info)
        query = build_filter(where, info.fields) or {"match_all": {}}
        body: dict[str, Any] = {
            "size": size,
            "query": {"function_score": {"query": query, "random_score": {}, "boost_mode": "replace"}},
            "track_total_hits": True,
        }
        random = True
        try:
            resp = self._search(store, info.name, body)
        except OpenSearchError as exc:
            if exc.status != 400:
                raise
            random, body = False, {"size": size, "query": query, "track_total_hits": True}
            resp = self._search(store, info.name, body)
        hits = (resp.get("hits") or {}).get("hits") or []
        docs = [{"_id": h.get("_id"), **(h.get("_source") or {})} for h in hits]
        checks = {}
        for vf in info.vectors:
            vectors = [v for doc in docs for v in values_at(doc, vf.path)]
            result = check_vectors(vectors, vf.path)
            result.docs = len(docs)
            result.missing = sum(1 for doc in docs if not values_at(doc, vf.path))
            checks[vf.path] = result
        total = (resp.get("hits") or {}).get("total")
        sample = Sample(
            target=store.name,
            index=info.name,
            docs=docs,
            checks=checks,
            random=random,
            total=total.get("value") if isinstance(total, dict) else _int(total),
            took_ms=resp.get("took"),
            info=info,
        )
        return sample

    # ------------------------------------------------------------------ search

    def embed(self, text: str, *, model: str | None = None, dimension: int | None = None) -> Embedding:
        """Embed a text with a Bedrock embedding model: an ID from EMBEDDING_MODELS (or an inference profile), or a short
        name ('titan', 'titan-v1', 'cohere', 'cohere-multilingual', 'cohere-v4'). Without a model, Titan Text
        Embeddings V2 is used for 256, 512 or 1,024 dimensions; other sizes need a model. Needs bedrock:InvokeModel."""
        if model:
            model_id = _EMBEDDING_ALIASES.get(model.lower(), model)
        elif dimension in (256, 512, 1024):
            model_id = "amazon.titan-embed-text-v2:0"
        else:
            fits = [mid for mid, spec in EMBEDDING_MODELS.items() if dimension in spec[2]]
            options = " or ".join(f"model={mid!r}" for mid in fits) or "model='<Bedrock model ID>'"
            raise ValueError(
                f"Which model embedded these {dimension or '?'}-dimension vectors? Pass {options}, embed=your_function "
                "(text -> list of floats), or vector=[...]. Vectors from a different model aren't comparable."
            )
        style = _embedding_style(model_id)
        spec = EMBEDDING_MODELS.get(_base_model(model_id))
        size = dimension if spec and dimension in spec[2] and dimension != spec[1] else None
        resp = self._service("bedrock-runtime").invoke_model(  # read-only: computes an embedding, changes no AWS resource
            modelId=model_id,
            body=json.dumps(_embedding_request(style, text, size)),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        headers = (resp.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
        tokens = _int(payload.get("inputTextTokenCount")) or _int(headers.get("x-amzn-bedrock-input-token-count"))
        price = spec[3] if spec else None
        cost = tokens * price / 1e6 if tokens is not None and price is not None else None
        return Embedding([float(x) for x in _embedding_from(payload)], model_id, tokens, cost)

    def doc_vector(self, target: Any, index: str, doc_id: str, field: str | None = None) -> list[float]:
        """The vector a document holds in a vector field (the index's first one by default)."""
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        return self._doc_vector(store, info, str(doc_id), info.vector(field))

    def _doc_vector(self, store: Any, info: IndexInfo, doc_id: str, vf: VectorField) -> list[float]:
        resp = self._search(store, info.name, {"size": 1, "query": {"ids": {"values": [doc_id]}}})
        hits = (resp.get("hits") or {}).get("hits") or []
        if not hits:
            raise ValueError(f"No document {doc_id!r} in {info.name}. sample() shows some document IDs.")
        vectors = [v for v in values_at(hits[0].get("_source") or {}, vf.path) if isinstance(v, list)]
        if not vectors:
            raise ValueError(
                f"Document {doc_id!r} has no '{vf.path}' in what OpenSearch returns (it has no vector, or the "
                "mapping leaves vectors out of _source), so like= can't use it."
            )
        return [float(x) for x in vectors[0]]

    def search(
        self,
        target: Any,
        index: str,
        text: str | None = None,
        *,
        vector: Any = None,
        like: str | None = None,
        k: int = 10,
        field: str | None = None,
        where: Any = None,
        model: str | None = None,
        embed: Callable[[str], Any] | None = None,
        ef_search: int | None = None,
    ) -> SearchResult:
        """The k nearest neighbours of a query, by one k-NN search. The query is one of: `text`, embedded by
        embed=your_function or a Bedrock model (see embed()); vector=[...]; or like='<document id>', that document's
        own vector (the document itself is left out of the results). field: the vector field (the index's first by
        default); where: a filter (see build_filter); ef_search: candidates to consider (more is slower, more exact)."""
        started = time.monotonic()
        store = self.resolve(target)
        info = self.index(store, index, counts=False)
        vf = info.vector(field)
        if sum(x is not None for x in (text, vector, like)) != 1:
            raise ValueError(
                "search() takes one query: a text (search('how do refunds work?')), vector=[...] or "
                "like='<document id>'"
            )
        _check_where(where, info)
        query_filter = build_filter(where, info.fields)  # before embedding, so a typo costs nothing
        embedding = None
        if like is not None:
            query_vector = self._doc_vector(store, info, str(like), vf)
        elif vector is not None:
            query_vector = _as_vector(vector)
        elif embed is not None:
            query_vector = _as_vector(embed(str(text)))
        else:
            embedding = self.embed(str(text), model=model, dimension=vf.dimension)
            query_vector = embedding.vector
        if vf.dimension and len(query_vector) != vf.dimension:
            raise ValueError(
                f"The query vector has {len(query_vector):,} numbers, but '{vf.path}' holds {vf.dimension:,}-dimension "
                "vectors: embed the query with the model (and dimension setting) that embedded the documents."
            )
        k = max(1, _as_int(k, "k"))
        body = knn_query(
            vf,
            query_vector,
            k + (1 if like is not None else 0),
            filter=query_filter,
            ef_search=ef_search,
            exclude=[v.path for v in info.vectors if v.path != vf.path],
        )
        resp = self._search(store, info.name, body)
        hits: list[Hit] = []
        norms: list[float] = []
        excluded = False
        for h in (resp.get("hits") or {}).get("hits") or []:
            if like is not None and h.get("_id") == str(like):
                excluded = True
                continue
            source = h.get("_source") or {}
            for value in values_at(source, vf.path):
                norm = vector_norm(value) if isinstance(value, list) else None
                if norm is not None:
                    norms.append(norm)
            _drop(source, vf.path)
            score = float(h.get("_score") or 0.0)
            hits.append(Hit(len(hits) + 1, str(h.get("_id")), score, source, score_to_similarity(score, vf.space)))
        result = SearchResult(
            target=store.name,
            index=info.name,
            field=vf.path,
            space_type=vf.space,
            k=k,
            hits=hits[:k],
            text_field=info.text_field,
            query=body,
            took_ms=resp.get("took"),
            seconds=time.monotonic() - started,
            text=text,
            like=None if like is None else str(like),
            where=where,
            embedding=embedding,
            query_norm=vector_norm(query_vector),
            hit_norms=norms,
            excluded_self=excluded,
            info=info,
        )
        return result


# =============================================================================
# 5. OpenSearchView - notebook UI layer (renders what OpenSearchAnalyzer returns)
# =============================================================================


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[
        tuple[str, ...]
    ]  # (label, value), or (label, value, tone) with tone 'warn' | 'bad' | 'ok'


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]  # cells are text, or _Tone for a coloured status
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap
    code_cols: tuple[int, ...] = ()  # columns holding calls to copy, shown as code
    prose_cols: tuple[
        int, ...
    ] = ()  # columns of sentences this tool wrote (findings): calls in them shown as code
    collapsed: bool = False  # a secondary view: folded under its title in HTML


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""
    wrap: bool = False  # prose: wrap long lines instead of scrolling sideways
    code: bool = False  # a snippet to copy: in HTML one click selects all of it
    collapsed: bool = (
        False  # a secondary view (raw JSON): folded under its title in HTML
    )


@dataclass
class _Findings:
    items: list[tuple[str, str]]  # (level, message) pairs from a *_findings function
    empty: str = ""  # said (as an ok note) when there are none; nothing when blank


@dataclass
class _Next:
    items: list[
        tuple[str, str]
    ]  # (call, what it shows): the commands worth running next, arguments filled in
    title: str = "Next"


@dataclass
class _Tone:
    """A table cell with a status colour: a pill in HTML, plain text elsewhere."""

    text: str
    tone: str = "warn"  # 'warn' | 'bad' | 'ok'

    def __str__(self) -> str:
        return self.text


_CSS = """<style>
.osv{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.osv h3{margin:10px 0 2px;font-size:16px}
.osv h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.osv h4{margin:14px 0 4px;font-size:13px}
.osv .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.osv .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.osv .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.osv .card.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.osv .card.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.osv .card.ok{border-color:rgba(16,185,129,.7)}
.osv .card .l{font-size:11px;opacity:.65}
.osv .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.osv .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.osv .tw.scroll{max-height:640px;overflow:auto}
.osv table.t{border-collapse:collapse;width:auto;font-size:inherit}
.osv table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.osv .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.osv .tw.scroll table.t th{background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.osv table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.osv table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.osv table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.osv table.t td.s{white-space:nowrap}
.osv table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.osv table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.osv table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.osv .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.osv .track{vertical-align:middle;margin-right:6px}
.osv .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.osv .pill{display:inline-block;padding:0 7px;border-radius:9px;font-weight:600;font-size:12px}
.osv .pill.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.6)}
.osv .pill.bad{background:rgba(239,68,68,.16);box-shadow:inset 0 0 0 1px rgba(239,68,68,.6)}
.osv .pill.ok{background:rgba(16,185,129,.14);box-shadow:inset 0 0 0 1px rgba(16,185,129,.55)}
.osv .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.osv .note::before{content:"\\2139\\FE0E";margin-right:7px;opacity:.7}
.osv .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.osv .note.warn::before{content:"\\26A0\\FE0E"}
.osv .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.osv .note.ok::before{content:"\\2713"}
.osv .fh{font-size:12px;font-weight:600;opacity:.75;margin:10px 0 2px}
.osv code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:0 4px;border-radius:4px}
.osv code{background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
.osv .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.osv pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.osv pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.5;max-height:560px}
.osv pre.code{user-select:all;-webkit-user-select:all;cursor:text}
.osv .hint{font-weight:400;font-size:11px;opacity:.55;margin-left:8px}
.osv details.sec{margin:14px 0 4px}
.osv details.sec>summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.osv .next{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:12px 0 4px;padding-top:8px}
.osv .next{border-top:1px dashed rgba(127,127,127,.35)}
.osv .next .nl{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.osv .next .nw{font-size:12px;opacity:.65;margin-left:6px}
</style>"""

_BADGE = "🔎 OpenSearch"  # the chip before each report's title, so reports from different analyzers are easy to tell apart
_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")
# A command in a sentence: a call (kb_info(), documents(status='FAILED'), .core.find(...), S3View().preview('s3://..'))
# or an AWS CLI command with its options (aws dynamodb update-table --table-name orders --deletion-protection-enabled).
_CALL_RE = re.compile(
    r"((?<![\w.])\.?(?:[A-Za-z_]\w*(?:\(\))?\.)*[A-Za-z_]\w*"
    r"\((?:[^()'\"]|'[^']*'|\"[^\"]*\"|\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))*\)"
    r"|\baws [a-z0-9-]+ [a-z0-9-]+(?: --[\w-]+(?: (?!--)[^\s,;]*[^\s,;.])?)*)"
)
_TONES = ("warn", "bad", "ok")
_MARKS = {
    "warn": "[!] ",
    "ok": "[ok] ",
}  # text-mode prefix of a note by level; anything else is "[i] "
_SELECT = ' title="Click to select, then copy"'


def _prose(value: Any) -> str:
    """Escaped HTML for a sentence this tool wrote, with the calls in it as code that one click selects.
    The text is split on the calls and every piece escaped before it's wrapped, so nothing in it becomes markup."""
    pieces = _CALL_RE.split("" if value is None else str(value))
    return "".join(
        f"<code{_SELECT}>{_esc(piece)}</code>" if i % 2 else _esc(piece)
        for i, piece in enumerate(pieces)
    )


def _call(name: str, *args: Any, **kwargs: Any) -> str:
    """_call('tree', 's3://b/', depth=2) -> "tree('s3://b/', depth=2)": a next step, ready to copy."""

    def literal(
        value: Any,
    ) -> str:  # repr, but DynamoDB numbers read 42 rather than Decimal('42')
        if type(value).__name__ == "Decimal":
            return str(value)
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items())
                + "}"
            )
        if isinstance(value, (list, tuple)):
            inner = ", ".join(map(literal, value)) + (
                "," if isinstance(value, tuple) and len(value) == 1 else ""
            )
            return f"[{inner}]" if isinstance(value, list) else f"({inner})"
        return repr(value)

    return f"{name}({', '.join([literal(a) for a in args] + [f'{k}={literal(v)}' for k, v in kwargs.items()])})"


def _signature(function: Callable) -> str:
    """'(uri, *, top_n=10, limit=None)': a command's parameters without self or type hints."""
    sig = inspect.signature(function)
    params = [
        p.replace(annotation=inspect.Parameter.empty)
        for name, p in sig.parameters.items()
        if name != "self"
    ]
    return str(
        sig.replace(parameters=params, return_annotation=inspect.Signature.empty)
    )


def _tone(item: tuple[str, ...]) -> str:
    """The tone of a card: its third element, when it's one this renderer colours."""
    return item[2] if len(item) > 2 and item[2] in _TONES else ""


def _ordered(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Warnings first, then notes, each in the order they were found."""
    return sorted(findings, key=lambda f: {"warn": 0, "info": 1}.get(f[0], 2))


def _counts(findings: list[tuple[str, str]], sep: str) -> str:
    """'2 warnings · 3 notes'."""
    warns = sum(level == "warn" for level, _ in findings)
    return sep.join(
        filter(
            None,
            [
                _plural(warns, "warning") if warns else "",
                _plural(len(findings) - warns, "note") if len(findings) > warns else "",
            ],
        )
    )


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _hidden(count: int, table: _Table, default_max: int) -> str:
    """The line under a table that was cut short, and how to see the rest when the view's max_rows cut it."""
    text = f"... {count:,} more rows not shown"
    return text + (
        f" (the view shows {default_max:,}; ui.max_rows = 0 shows all)"
        if table.max_rows is None
        else ""
    )


def _render_html(blocks: list[Any], max_rows: int) -> str:
    out = [_CSS, '<div class="osv">']
    for block in blocks:
        if isinstance(block, _Title):
            out.append(
                f'<h3><span class="badge">{_esc(_BADGE)}</span>{_esc(block.text)}</h3>'
            )
            if block.sub:
                out.append(f'<div class="sub">{_prose(block.sub)}</div>')
        elif isinstance(block, _Cards):
            cards = "".join(
                f'<div class="{" ".join(filter(None, ["card", _tone(item)]))}"><div class="l">'
                f'{_esc(item[0])}</div><div class="v">{_esc(item[1])}</div></div>'
                for item in block.items
            )
            out.append(f'<div class="cards">{cards}</div>')
        elif isinstance(block, _Note):
            out.append(f'<div class="note {block.level}">{_prose(block.text)}</div>')
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                notes = "".join(
                    f'<div class="note {level}">{_prose(message)}</div>'
                    for level, message in items
                )
                head = f'<div class="fh">Findings · {_esc(_counts(items, " · "))}</div>'
                out.append(f'<div class="fd">{head}{notes}</div>')
            elif block.empty:
                out.append(f'<div class="note ok">{_prose(block.empty)}</div>')
        elif isinstance(block, _Next):
            if block.items:
                calls = "".join(
                    f'<span class="ni"><code{_SELECT}>{_esc(call)}</code>'
                    + (f'<span class="nw">{_esc(why)}</span>' if why else "")
                    + "</span>"
                    for call, why in block.items
                )
                out.append(
                    f'<div class="next"><span class="nl">{_esc(block.title)}</span>{calls}</div>'
                )
        elif isinstance(block, _Table):
            if not block.rows:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
                out.append('<div class="more">(none)</div>')
                continue
            rows, hidden = _visible_rows(block, max_rows)
            head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
            head += (
                f"<th>{_esc(block.bar_label)}</th>" if block.bars is not None else ""
            )
            body = []
            for i, row in enumerate(rows):
                cells = []
                for j, cell in enumerate(row):
                    text = "" if cell is None else str(cell)
                    inner = _esc(text)
                    if isinstance(cell, _Tone) and cell.tone in _TONES and text:
                        inner = f'<span class="pill {cell.tone}">{inner}</span>'
                    if block.tree and j == 0:
                        css = "tree"
                    elif j in block.code_cols and text:
                        css, inner = "c", f"<code{_SELECT}>{inner}</code>"
                    elif j in block.prose_cols:
                        css, inner = "", _prose(text)
                    elif _NUMERIC_RE.match(text):
                        css = "n"
                    else:
                        css = "s" if len(text) <= 16 and "\n" not in text else ""
                    cells.append(
                        f'<td class="{css}">{inner}</td>'
                        if css
                        else f"<td>{inner}</td>"
                    )
                if block.bars is not None:
                    pct = max(0.0, min(1.0, block.bars[i])) * 100
                    cells.append(
                        f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                        f"</span></span>{pct:.1f}%</td>"
                    )
                body.append(f"<tr>{''.join(cells)}</tr>")
            table = (
                f'<div class="tw{" scroll" if len(rows) > 30 else ""}"><table class="t"><thead><tr>{head}</tr>'
                f"</thead><tbody>{''.join(body)}</tbody></table></div>"
            )
            if hidden:
                table += (
                    f'<div class="more">{_esc(_hidden(hidden, block, max_rows))}</div>'
                )
            if block.collapsed:
                out.append(
                    f'<details class="sec"><summary>{_prose(block.title or "Details")} '
                    f"({len(block.rows):,})</summary>{table}</details>"
                )
            else:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
                out.append(table)
        elif isinstance(block, _Text):
            css = " ".join(
                filter(
                    None, ["wrap" if block.wrap else "", "code" if block.code else ""]
                )
            )
            pre = (
                f'<pre class="{css}"{_SELECT if block.code else ""}>{_esc(block.text)}</pre>'
                if css
                else f"<pre>{_esc(block.text)}</pre>"
            )
            if block.collapsed:
                out.append(
                    f'<details class="sec"><summary>{_prose(block.title or "Details")}</summary>{pre}</details>'
                )
            else:
                hint = (
                    '<span class="hint">click it to select all, then copy</span>'
                    if block.code
                    else ""
                )
                if block.title or hint:
                    out.append(f"<h4>{_prose(block.title)}{hint}</h4>")
                out.append(pre)
    out.append("</div>")
    return "".join(out)


def _render_text(blocks: list[Any], max_rows: int) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Title):
            out += ["", block.text, "=" * min(len(block.text), 100)] + (
                [block.sub] if block.sub else []
            )
        elif isinstance(block, _Cards):
            line = ""
            for entry in block.items:
                item = f"{entry[0]}: {entry[1]}" + (
                    " (!)" if _tone(entry) in ("warn", "bad") else ""
                )
                if line and len(line) + len(item) > 100:
                    out.append(line)
                    line = ""
                line += ("   " if line else "") + item
            out.append(line)
        elif isinstance(block, _Note):
            out.append(_MARKS.get(block.level, "[i] ") + block.text)
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                out += ["", f"-- Findings: {_counts(items, ', ')} --"]
                out += [_MARKS.get(level, "[i] ") + message for level, message in items]
            elif block.empty:
                out.append("[ok] " + block.empty)
        elif isinstance(block, _Next):
            if block.items:
                width = max(len(call) for call, _ in block.items)
                out += ["", f"{block.title}:"]
                out += [
                    f"  {call.ljust(width)}   {why}".rstrip()
                    for call, why in block.items
                ]
        elif isinstance(block, _Table):
            out.append("")
            if block.title:
                out.append(f"-- {block.title} --")
            if not block.rows:
                out.append("(none)")
                continue
            rows, hidden = _visible_rows(block, max_rows)
            headers = list(block.headers) + (
                [block.bar_label] if block.bars is not None else []
            )
            cells = [
                [_clip(("" if c is None else str(c)).replace("\n", ", ")) for c in row]
                + ([_text_bar(block.bars[i])] if block.bars is not None else [])
                for i, row in enumerate(rows)
            ]
            widths = [
                max([_width(h)] + [_width(r[j]) for r in cells])
                for j, h in enumerate(headers)
            ]

            def line_of(values: list[str], widths: list[int] = widths) -> str:
                return "  ".join(
                    _pad(v, w, right=bool(_NUMERIC_RE.match(v)))
                    for v, w in zip(values, widths)
                ).rstrip()

            out += [line_of(headers), "  ".join("-" * w for w in widths)] + [
                line_of(r) for r in cells
            ]
            if hidden:
                out.append(_hidden(hidden, block, max_rows))
        elif isinstance(block, _Text):
            if block.title:
                out += ["", f"-- {block.title} --"]
            out.append(block.text)
    return "\n".join(out)


def _progress_bar_class(notebook: bool) -> Any:
    """tqdm's widget bar in a notebook (it needs ipywidgets) or its text bar elsewhere; None without tqdm."""
    try:
        if notebook:
            importlib.import_module("ipywidgets")
            return importlib.import_module("tqdm.notebook").tqdm
        return importlib.import_module("tqdm").tqdm
    except Exception:  # not installed, or too old to import cleanly: the plain progress line takes over
        return None


def _progress_bar(bar_class: Any, label: str, unit: str, total: int | None) -> Any:
    """A tqdm bar that shows up after half a second and disappears when closed. unit='B' counts bytes."""
    options: dict[str, Any] = {
        "desc": label,
        "total": total,
        "leave": False,
        "delay": 0.5,
        "mininterval": 0.25,
        "dynamic_ncols": True,
        "disable": False,
        "unit_scale": True,
    }
    if unit == "B":
        options.update(unit="B", unit_divisor=1024)
    else:
        known = total is not None
        counts = "{percentage:3.0f}%|{bar}| {n:,}/{total:,}" if known else "{n:,}"
        timing = (
            "{elapsed}<{remaining}, {rate_fmt}" if known else "{elapsed}, {rate_fmt}"
        )
        options.update(
            unit=f" {unit}", bar_format=f"{{desc}}: {counts} {unit} [{timing}]"
        )
    return bar_class(**options)


def _progress_text(
    label: str, unit: str, count: int, total: int | None, elapsed: float
) -> str:
    """The progress line shown without tqdm: 'Reading... 1.2 GB of 3.0 GB (40%) · 12s · 98.0 MB/s · about 18s left'."""
    amount = human_size if unit == "B" else (lambda n: f"{n:,}")
    text = f"{label}... {amount(count)}"
    if total:
        text += f" of {amount(total)}"
    text += "" if unit == "B" else f" {unit}"
    if total:
        text += f" ({min(count / total, 1):.0%})"
    text += f" · {_duration(elapsed)}"
    if elapsed >= 1 and count:
        rate = count / elapsed
        text += (
            f" · {human_size(rate)}/s"
            if unit == "B"
            else f" · {rate:,.0f}/s"
            if rate >= 10
            else f" · {rate:.1f}/s"
        )
        if total and total > count:
            text += f" · about {_duration((total - count) / rate)} left"
    return text


_COLLECTION_KINDS = {"VECTORSEARCH": "vector search", "SEARCH": "search", "TIMESERIES": "time series"}
_NETWORK = {"public": "public", "vpc": "VPC endpoints", "aws services": "AWS services only"}
_FILTER_EXAMPLES = {  # mapping type -> a where= example for index_info's table of fields
    "keyword": "'<value>'",
    "constant_keyword": "'<value>'",
    "text": "('contains', 'some words')",
    "match_only_text": "('contains', 'some words')",
    "integer": "('>=', 10)",
    "long": "('>=', 10)",
    "short": "('>=', 10)",
    "byte": "('>=', 10)",
    "float": "('>=', 0.5)",
    "double": "('>=', 0.5)",
    "half_float": "('>=', 0.5)",
    "scaled_float": "('>=', 0.5)",
    "unsigned_long": "('>=', 10)",
    "date": "('>=', '2024-01-01')",
    "date_nanos": "('>=', '2024-01-01')",
    "boolean": "True",
    "ip": "'10.0.0.1'",
}


def _state_tone(status: str) -> str:
    """'' for a healthy status, 'bad' for a failed one, 'warn' for anything in between."""
    lowered = (status or "").lower()
    if lowered in ("active", "green", "open", ""):
        return ""
    return "bad" if lowered in ("failed", "red", "update_failed", "isolated") else "warn"


def _target_arg(store: Any) -> str:
    """What to pass to a command to name this domain, collection or endpoint."""
    return store.url if isinstance(store, Endpoint) else store.name


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and getattr(a, "name", None) == getattr(b, "name", None)


def _node_label(domain: Domain) -> str:
    """'3 × r6g.large.search' (+ masters and UltraWarm)."""
    parts = [f"{domain.instance_count} × {domain.instance_type or '?'}"]
    if domain.master_type:
        parts.append(f"{domain.master_count} masters")
    if domain.warm_type:
        parts.append(f"{domain.warm_count} UltraWarm")
    return "\n".join(parts)


def _storage_label(domain: Domain) -> str:
    if not domain.volume_type:
        return "instance disks"
    return f"{domain.volume_gb:,} GB {domain.volume_type} × {domain.instance_count}"


def _shards_label(info: IndexInfo) -> str:
    """'3 primaries, 1 replica each' / '1 primary, no replicas'."""
    if not info.shards:
        return "-"
    primaries = f"{info.shards} primar{'y' if info.shards == 1 else 'ies'}"
    if info.replicas is None:
        return primaries
    return primaries + (f", {_plural(info.replicas, 'replica')} each" if info.replicas else ", no replicas")


def _vector_label(info: IndexInfo) -> str:
    """'embedding: 1,024 dims · faiss HNSW · cosine', one line per vector field."""
    return "\n".join(f"{vf.path}: {describe_vector_field(vf)}" for vf in info.vectors) or "-"


def _with_vector_cell(info: IndexInfo) -> Any:
    """'48,750 (97.5%)' for the first vector field, toned when some documents have none."""
    if not info.vectors:
        return "-"
    have, total = info.with_vector.get(info.vectors[0].path), info.top_docs
    if have is None or not total:
        return "?" if "counts" in info.errors else "-"
    text = f"{have:,} ({have / total:.0%})" if have < total else f"{have:,} (all)"
    return _Tone(text, "warn" if have < total else "")


def _method_settings(vf: VectorField) -> str:
    """'m=16 · ef_construction=512 · ef_search=100', defaults marked."""
    if vf.model_id:
        return f"trained model {vf.model_id}"
    if vf.algorithm == "ivf":
        return f"nlist={vf.nlist or 4}" + ("" if vf.nlist else " (default)")
    parts = []
    for name, value, default in (("m", vf.m, 16), ("ef_construction", vf.ef_construction, 100),
                                 ("ef_search", vf.ef_search, 100)):
        parts.append(f"{name}={value}" if value else f"{name}={default} (default)")
    return " · ".join(parts)


def _compression_label(vf: VectorField) -> str:
    factor = vf.compression_factor
    parts = []
    if vf.mode == "on_disk":
        parts.append("on disk")
    if vf.data_type != "float":
        parts.append(f"{vf.data_type} vectors")
    if vf.encoder:
        parts.append(vf.encoder)
    label = ", ".join(parts) or "none (32-bit floats)"
    return label + (f", {factor:g}x smaller" if factor > 1 else "")


def _hit_text(source: dict[str, Any], text_field: str | None, width: int = 300) -> str:
    values = values_at(source, text_field) if text_field else []
    value = values[0] if values else ""
    return _clip(" ".join(str(value).split()), width) if value not in (None, "") else ""


def _flat(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """A document with its objects spread into dotted fields ('metadata.page'); lists stay values."""
    flat: dict[str, Any] = {}
    for key, value in doc.items():
        if isinstance(value, dict) and value:
            flat.update(_flat(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _metadata_columns(docs: list[dict[str, Any]], skip: set[str], limit: int) -> list[str]:
    """The fields most documents have (objects spread into dotted fields), besides the text, source and vectors."""
    counts: Counter = Counter()
    for doc in docs:
        counts.update(k for k in _flat(doc) if k not in skip and not k.startswith("_"))
    return [name for name, _ in counts.most_common(limit or None)]


def _filter_rows(info: IndexInfo) -> list[list[str]]:
    """Fields a where= filter can use, with an example for each."""
    vectors = {vf.path for vf in info.vectors}
    rows = []
    for path, kind in info.fields.items():
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        if path in vectors or kind in ("object", "nested", "knn_vector", "") or info.fields.get(parent) in _TEXT_TYPES:
            continue  # sub-fields such as title.keyword are used through their parent
        if path in info.unindexed:
            continue
        example = _FILTER_EXAMPLES.get(kind)
        if example is None:
            continue
        if kind in ("text", "match_only_text") and info.fields.get(f"{path}.keyword") == "keyword":
            example, kind = "'<exact value>'", f"{kind} (+ keyword)"
        rows.append([path, kind, f"where={{{path!r}: {example}}}"])
    return rows


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS, OpenSearch and input errors as a readable note instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: OpenSearchView, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            self._show([_Note(f"{code}: {self._explain(code, message)}  [{method.__name__}]", "warn")])
        except OpenSearchError as exc:
            self._show([_Note(f"{self._explain_http(exc)}  [{method.__name__}]", "warn")])
        except _Hint as exc:
            self._show([_Note(str(exc))])
        except ImportError as exc:  # a missing optional package: the message says what to pip install
            self._show([_Note(f"{str(exc).rstrip('.')}.", "warn")])
        except (BotoCoreError, ValueError, TypeError, ImportError) as exc:
            self._show([_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")])
        except (KeyError, AttributeError, IndexError) as exc:  # an answer shaped unlike any this file has seen
            self._show([_Note(
                f"An answer from OpenSearch wasn't shaped the way this file expects ({type(exc).__name__}: {exc}), "
                f"so the report stopped. Please report it with the command you ran.  [{method.__name__}]", "warn")])

    return wrapper


class OpenSearchView:
    """Notebook UI over OpenSearchAnalyzer. Each method renders a report and returns nothing; for the underlying
    data call the matching method on `view.core` (an OpenSearchAnalyzer).

    index: the domain or collection, and optionally the index ('vectors-prod/docs'), that commands use when they
    aren't given one; use() changes it. Without it, commands use the only domain or collection in the region and its
    only vector index, or say how to pick one.
    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    max_columns: most document fields shown side by side next to the text (0 = all).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _progress_owner: Callable[[], None] | None = None  # clears the progress bar showing now
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🗂️ Domains and collections": ("overview", "use"),
        "📇 Indexes": ("indexes", "index_info"),
        "🧭 Vectors": ("sample", "search"),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "every domain and collection: size, cost and warnings"),
        ("indexes('name')", "its indexes, which hold vectors, and the memory they need"),
        ("index_info('name/index')", "one vector index in plain English"),
    )

    def __init__(
        self,
        core: OpenSearchAnalyzer | None = None,
        *,
        index: str | None = None,
        mode: str = "auto",
        max_rows: int = 50,
        max_columns: int = 6,
        progress: str = "auto",
    ):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.core = core or OpenSearchAnalyzer()
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.progress = progress
        self.target: str | None = None  # what use() picked: a domain or collection name, or a URL
        self.index: str | None = None
        if index:
            self.target, self.index = parse_location(index)

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            try:
                from IPython.display import HTML, display
            except ImportError:  # mode='html' outside Jupyter: show text, and say why (once)
                self.use_html = False
                note = "mode='html' only works in Jupyter (IPython isn't installed here), so this is shown as text."
                blocks = [*blocks, _Note(note, "warn")]
            else:
                display(HTML(_render_html(blocks, self.max_rows)))
                return
        print(_render_text(blocks, self.max_rows))

    @contextmanager
    def _progress(
        self, label: str = "Reading", unit: str = "items read"
    ) -> Generator[Callable[..., None], None, None]:
        """Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away."""
        notebook = self.use_html and _in_notebook()  # elsewhere (or without IPython) the plain line goes to stderr
        bar_class = [
            _progress_bar_class(notebook)
            if self.progress == "auto"
            else None
        ]
        bar: list[Any] = [None]
        handle: list[Any] = [None]
        started: list[Any] = [
            time.monotonic(),
            None,
        ]  # when the current total started, and that total
        shown, width, stopped = [0.0], [0], [False]

        def close_bar() -> None:
            if bar[0] is not None:
                if not stopped[0] and bar[0].total and bar[0].n < bar[0].total:
                    bar[0].total = bar[
                        0
                    ].n  # done early (a file it couldn't read): no red "failed" widget
                bar[0].close()
                bar[0] = None

        def clear() -> None:
            close_bar()
            if handle[0] is not None:
                from IPython.display import HTML

                handle[0].update(HTML(""))
            elif width[0]:
                print("\r" + " " * width[0] + "\r", end="", file=sys.stderr, flush=True)
                width[0] = 0

        def take_over() -> None:
            owner = self._progress_owner
            if owner is not clear:
                if owner is not None:
                    owner()
                self._progress_owner = clear

        def tick(count: int, total: int | None = None) -> None:
            if self.progress == "off":
                return
            if bar_class[0] is not None:
                try:
                    if bar[0] is not None and total != bar[0].total:
                        close_bar()
                    if bar[0] is None:
                        take_over()
                        bar[0] = _progress_bar(bar_class[0], label, unit, total)
                    bar[0].update(count - bar[0].n)
                    return
                except Exception:  # an old tqdm or a broken widget front end: use the plain line instead
                    bar_class[0] = None
            now = time.monotonic()
            if total != started[1]:
                started[:] = [now, total]
            if now - started[0] < 0.5 or now - shown[0] < 0.5:
                return
            shown[0] = now
            take_over()
            text = _progress_text(label, unit, count, total, now - started[0])
            if notebook:
                from IPython.display import HTML, display

                if handle[0] is None:
                    handle[0] = display(HTML(""), display_id=True)
                if handle[0] is not None:  # display() returns None outside IPython
                    handle[0].update(
                        HTML(f'<div style="opacity:.6">{_esc(text)}</div>')
                    )
            else:
                width[0] = max(width[0], len(text))
                print("\r" + text.ljust(width[0]), end="", file=sys.stderr, flush=True)

        try:
            yield tick
        except BaseException:
            stopped[0] = True  # interrupted: a tqdm widget stays, red, where it stopped
            raise
        finally:
            clear()
            if self._progress_owner is clear:
                self._progress_owner = None

    def help(self, command: Any = None) -> None:
        """Every command, grouped by task; help('name') shows one command in full."""
        view = type(self).__name__
        commands = {
            name: inspect.unwrap(member)
            for name, member in vars(type(self)).items()
            if not name.startswith("_") and callable(member)
        }

        def about(name: str) -> str:  # the docstring's first paragraph, on one line
            return " ".join(
                (inspect.getdoc(commands[name]) or "").split("\n\n")[0].split()
            )

        if command is not None:
            name = getattr(command, "__name__", str(command))
            if name not in commands:
                close = difflib.get_close_matches(name, list(commands), n=3)
                hint = (
                    f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                )
                self._show(
                    [
                        _Note(
                            f"{view} has no command {name!r}.{hint} help() lists them all.",
                            "warn",
                        )
                    ]
                )
                return
            self._show(
                [
                    _Title(
                        f"{name}{_signature(commands[name])}",
                        f"{view} command · help() lists them all",
                    ),
                    _Text(
                        inspect.getdoc(commands[name]) or "(no description)", wrap=True
                    ),
                ]
            )
            return
        grouped = {name for names in self._GROUPS.values() for name in names}
        groups = {
            **self._GROUPS,
            "Other": tuple(name for name in commands if name not in grouped),
        }
        blocks: list[Any] = [
            _Title(
                f"{view} commands",
                "help('name') shows one in full · the data behind each report "
                f"comes from .core ({type(self.core).__name__})",
            ),
            _Next(list(self._START), title="Start here"),
        ]
        for group, names in groups.items():
            rows = [
                [f"{name}{_signature(commands[name])}", about(name)]
                for name in names
                if name in commands
            ]
            if rows:
                blocks.append(
                    _Table(
                        ["Command", "What it shows"],
                        rows,
                        title=group,
                        max_rows=0,
                        code_cols=(0,),
                    )
                )
        self._show(blocks)

    def _explain(self, code: str, message: str) -> str:
        """An AWS API error message plus what to do about it."""
        lowered = message.lower()
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code in ("ResourceNotFoundException", "ResourceNotFound"):
            return f"{text} overview() lists the domains and collections in {self.core.region}."
        if code == "AccessDeniedException" and "model" in lowered and "not authorized to perform" not in lowered:
            return (f"{text} Enable the model in the Bedrock console (Model access), or pass model= with one you can "
                    "use, or embed=your_function.")
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in lowered:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code == "ValidationException" and "model" in lowered:
            return f"{text} Pass model= with a Bedrock embedding model ID (EMBEDDING_MODELS lists the ones this file knows)."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    def _explain_http(self, exc: OpenSearchError) -> str:
        """A failed REST request in words, with what to change."""
        store, status = exc.store, exc.status
        where = store.label
        reason = exc.reason.rstrip(".")
        if status is None:
            if isinstance(store, Domain) and store.vpc:
                return (f"Couldn't reach {where} ({exc.kind}). It only answers inside VPC {store.vpc}: run this "
                        "notebook in that VPC, and allow HTTPS (port 443) from it in the domain's security group.")
            if isinstance(store, Collection) and store.network in ("vpc", "aws services"):
                return (f"Couldn't reach {where} ({exc.kind}): its network policy {store.network_policy} only lets "
                        f"{_NETWORK[store.network]} in. Run the notebook where the policy allows, or allow it there.")
            return (f"Couldn't reach {store.url} ({exc.kind}: {reason}). Check the address, and that this notebook "
                    "can reach it (VPC, security groups, proxy).")
        if status == 401:
            return (f"{where} asked for a user name and password ({reason}): with fine-grained access control's "
                    "internal users, pass OpenSearchView(OpenSearchAnalyzer(auth=('user', 'password'))).")
        if status == 403:
            if _is_serverless(store):
                index = f"index/{store.name}/*" if isinstance(store, Collection) else "index/<collection>/*"
                return (f"{where} refused the request ({reason}). Two policies must allow it: an IAM policy with "
                        f"aoss:APIAccessAll, and a data access policy that gives your role aoss:DescribeIndex and "
                        f"aoss:ReadDocument on {index} (Serverless console, Data access policies).")
            if isinstance(store, Domain) or getattr(store, "service", None) == "es":
                arn = f"{store.arn}/*" if isinstance(store, Domain) and store.arn else "the domain's ARN/*"
                fgac = (" Fine-grained access control is on, so also map your role to an OpenSearch role that can "
                        "read, such as the built-in readall_and_monitor (OpenSearch Dashboards: Security, Roles)."
                        if isinstance(store, Domain) and store.fine_grained else "")
                return (f"{where} refused the request ({reason}). Your role needs es:ESHttpGet and es:ESHttpPost on "
                        f"{arn}, allowed by the domain's access policy too.{fgac}")
            return f"{where} refused the request ({reason}). If it uses basic authentication, pass auth=('user', 'password')."
        if status == 404 and exc.kind == "index_not_found_exception":
            missing = re.search(r"\[([^\]]+)\]", reason)
            name = missing.group(1) if missing else exc.path.strip("/").split("/")[0]
            try:
                names = [i.name for i in self.core.cat_indices(store) if not i.name.startswith(".")]
            except (OpenSearchError, ValueError, BotoCoreError):
                names = []
            close = difflib.get_close_matches(name, names, n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            return f"No index {name!r} in {where}.{hint} indexes({_target_arg(store)!r}) lists them."
        if status == 429:
            return f"{where} is busy ({reason}): wait a few seconds and retry."
        kind = "" if exc.kind.startswith("HTTP ") else f" {exc.kind}"
        return f"{where} answered {status}{kind}: {reason} ({exc.path})."

    def _price_basis(self) -> str:
        return "us-east-1 list prices" if self.core.prices == OPENSEARCH_PRICES else "your prices"

    def _store(self, target: str | None = None) -> Domain | Collection | Endpoint:
        """The domain, collection or endpoint a command works on: `target`, else use()'s, else the only one."""
        if target is not None:
            return self.core.resolve(target)
        if self.target is not None:
            return self.core.resolve(self.target)
        names: list[str] = []
        problems: list[str] = []
        for lister, permission in (
            (self.core.domain_names, "es:ListDomainNames"),
            (lambda: [s.get("name", "") for s in self.core.collection_summaries()], "aoss:ListCollections"),
        ):
            try:
                names += lister()
            except ClientError as exc:
                problems.append(_why(_error_code(exc), permission))
            except BotoCoreError as exc:  # e.g. Serverless isn't offered in this region
                problems.append(type(exc).__name__)
        if len(names) == 1:
            return self.core.resolve(names[0])
        region = self.core.region
        if not names:
            couldnt = f" (couldn't list them: {'; '.join(problems)})" if problems else ""
            raise _Hint(
                f"There are no OpenSearch domains or Serverless collections in {region}{couldnt}. They're regional: "
                "try OpenSearchView(OpenSearchAnalyzer(region='us-west-2')). An OpenSearch you run yourself works by "
                "URL: indexes('https://localhost:9200')."
            )
        listed = sorted(names, key=str.lower)
        raise _Hint(
            f"Which domain or collection? There are {len(names)} in {region}: {', '.join(listed[:15])}"
            f"{', …' if len(listed) > 15 else ''}. Pass one, like indexes('{listed[0]}'), or pick one for every "
            f"command with use('{listed[0]}')."
        )

    def _maybe_store(self, name: str) -> Domain | Collection | Endpoint | None:
        try:
            return self.core.resolve(name)
        except ValueError:
            return None

    def _index_ref(self, index: str | None, command: str) -> tuple[Any, str]:
        """(store, index name) for a command: 'name/index', a bare index name (in use()'s domain or collection, or
        the only one), a domain or collection on its own (its only vector index), or use()'s index."""
        if index is not None:
            head, tail = parse_location(index)
            if tail is not None:
                return self.core.resolve(head), tail
            if re.match(r"^(https?://|arn:)", head, re.IGNORECASE):
                store = self.core.resolve(head)
                return store, self._only_vector_index(store, command)
            if self.target is not None:
                return self.core.resolve(self.target), head
            store = self._maybe_store(head)
            if store is not None:
                return store, self._only_vector_index(store, command)
            return self._store(), head
        store = self._store()
        if self.index and self.target is not None:
            return store, self.index
        return store, self._only_vector_index(store, command)

    def _only_vector_index(self, store: Any, command: str) -> str:
        report = self.core.indexes(store, details=False)
        vectors = sorted(i.name for i in report.vector_indexes)
        if len(vectors) == 1:
            return vectors[0]
        arg = _target_arg(store)
        if not vectors:
            names = sorted(i.name for i in report.indexes)
            listed = f" (its indexes: {', '.join(names[:10])}{', …' if len(names) > 10 else ''})" if names else ""
            raise _Hint(f"{store.label} has no vector indexes{listed}. indexes({arg!r}) shows them all.")
        example = (f"search('your question', index='{arg}/{vectors[0]}')" if command == "search"
                   else f"{command}('{arg}/{vectors[0]}')")
        raise _Hint(
            f"Which index? {store.label} has {len(vectors)} vector indexes: {', '.join(vectors[:15])}"
            f"{', …' if len(vectors) > 15 else ''}. Pass one, like {example}, or pick one for every command with "
            f"use('{arg}/{vectors[0]}')."
        )

    def _is_default(self, store: Any, index: str | None = None) -> bool:
        """Whether use() picked this store (and index), so next steps can leave the argument out."""
        if self.target is None or (index is not None and self.index != index):
            return False
        try:
            return _same(self.core.resolve(self.target), store)
        except (ValueError, ClientError, BotoCoreError):
            return False

    def _ref_args(self, store: Any, index: str) -> tuple[str, ...]:
        """('vectors-prod/docs',) for a next step's call, or () when it's use()'s index."""
        return () if self._is_default(store, index) else (f"{_target_arg(store)}/{index}",)

    def _ref_kwargs(self, store: Any, index: str) -> dict[str, str]:
        return {} if self._is_default(store, index) else {"index": f"{_target_arg(store)}/{index}"}

    # ---------------------------------------------------- domains and collections

    @_friendly_errors
    def use(self, where: str) -> None:
        """Sets the domain or collection, and with 'name/index' the index, that later commands use when you don't
        pass one: use('vectors-prod/docs') and then index_info(), sample() or search('a question')."""
        head, tail = parse_location(where)
        store = self.core.resolve(head)
        if tail is not None:
            tail = self.core.index(store, tail, counts=False).name  # checks it exists
        self.target, self.index = _target_arg(store), tail
        if tail:
            text = f"Using index {tail} in {store.label} from now on."
            steps = [
                ("index_info()", "the index in plain English"),
                ("sample()", "a few documents, and how healthy their vectors are"),
                (_call("search", "a question your documents answer"), "the documents nearest to it"),
            ]
        else:
            text = f"Using {store.label} from now on."
            steps = [("indexes()", "its indexes, and which hold vectors")]
        self._show([_Note(text, "ok"), _Next(steps)])

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """Every OpenSearch Service domain and Serverless collection in the region: what it runs on, where it
        answers, estimated monthly cost, and warnings. match='prod-*' keeps matching names; metrics=False skips
        CloudWatch (the OCUs Serverless used)."""
        with self._progress("Checking domains", unit="domains") as tick:
            ov = self.core.overview(match=match, metrics=metrics, progress=tick)
        prices = self.core.prices
        domains = sorted(ov.domains, key=lambda d: d.name)
        collections = sorted(ov.collections, key=lambda c: c.name)
        warnings: list[list[str]] = []
        domain_rows: list[list[Any]] = []
        domain_total = 0.0
        unreadable: list[str] = []
        for d in domains:
            if "describe" in d.errors:
                unreadable.append(f"{d.name} ({_why(d.errors['describe'], 'es:DescribeDomains')})")
                domain_rows.append([d.name, "?", "?", "-", "-", "-", "-", "-", "-"])
                continue
            cost = _total(domain_monthly_cost(d, prices))
            domain_total += cost or 0.0
            found = [m for level, m in domain_findings(d, prices) if level == "warn"]
            warnings += [[d.name, m] for m in found]
            domain_rows.append([
                d.name,
                f"{d.engine} {d.version}".strip(),
                _Tone(d.status or "?", _state_tone(d.status)),
                _node_label(d),
                _storage_label(d),
                f"VPC {d.vpc}" if d.vpc else "public",
                human_size(domain_knn_memory(d)),
                human_money(cost) if cost is not None else "?",
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        collection_rows: list[list[Any]] = []
        for c in collections:
            found = [m for level, m in collection_findings(c) if level == "warn"]
            warnings += [[c.name, m] for m in found]
            collection_rows.append([
                c.name,
                c.id,
                _COLLECTION_KINDS.get(c.kind, c.kind.lower() or "-"),
                _Tone(c.status or "?", _state_tone(c.status)),
                "on" if c.standby else "off (dev)",
                _NETWORK.get(c.network or "", "?" if "network" in c.errors else "-"),
                _key_label(c.kms_key),
                human_age(c.created),
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        groups = serverless_minimum(collections)
        minimum = sum(ocus for _, ocus, _ in groups)
        used = (ov.ocus["indexing"] + ov.ocus["search"]) if ov.ocus else None
        billed = max(used or 0.0, minimum)  # Serverless never bills less than its minimum
        serverless_total = ocu_monthly_cost(billed, prices) if collections else 0.0
        if collections and used is not None:
            basis = f"Serverless at the OCUs it used in the last {ov.hours}h (at least its idle minimum)"
        elif collections:
            basis = "Serverless at its idle minimum"
        else:
            basis = ""
        title = f"OpenSearch in {ov.region} ({_plural(len(domains), 'domain')}, {_plural(len(collections), 'collection')})"
        blocks: list[Any] = [
            _Title(
                title,
                (f"names matching {match!r} · " if match else "")
                + "cost: domains at their instances and storage"
                + (f", {basis}" if basis else "")
                + f", at {self._price_basis()}",
            ),
            _Cards([
                ("Domains", f"{len(domains):,}"),
                ("Serverless collections", f"{len(collections):,}"),
                ("Vector collections", f"{sum(c.vector for c in collections):,}"),
                ("Est. cost / month", human_money(domain_total + serverless_total)),
                ("With warnings", f"{len({name for name, _ in warnings}):,}", "warn" if warnings else "ok"),
            ]),
        ]
        for section, permission in (("domains", "es:ListDomainNames"), ("collections", "aoss:ListCollections")):
            if section in ov.errors:
                blocks.append(_Note(f"Couldn't list the {section} ({_why(ov.errors[section], permission)}).", "warn"))
        if not domains and not collections and not ov.errors:
            where = f"matching {match!r} " if match else ""
            blocks.append(_Note(
                f"No OpenSearch domains or Serverless collections {where}in {ov.region}. They're regional: try "
                "OpenSearchView(OpenSearchAnalyzer(region='us-west-2')). An OpenSearch you run yourself works by URL: "
                "indexes('https://localhost:9200')."
            ))
            self._show(blocks)
            return
        if unreadable:
            blocks.append(_Note(f"Couldn't describe {', '.join(unreadable)}.", "warn"))
        if domains:
            blocks.append(_Table(
                ["Domain", "Engine", "Status", "Data nodes", "Storage", "Endpoint", "k-NN memory", "Est. $/month",
                 "Warnings"],
                domain_rows,
                title="Domains (k-NN memory: what the data nodes have for vector graphs, about a quarter of their RAM)",
                max_rows=0,
            ))
        if collections:
            blocks.append(_Table(
                ["Collection", "ID", "Type", "Status", "Standby replicas", "Network", "Encryption", "Created",
                 "Warnings"],
                collection_rows,
                title="Serverless collections",
                max_rows=0,
            ))
            blocks += self._serverless_blocks(ov, groups, minimum, used)
        if warnings:
            blocks.append(_Table(
                ["Name", "Warning"],
                warnings,
                prose_cols=(1,),
                title="Warnings (indexes(name) shows every finding for one domain or collection)",
                max_rows=0,
            ))
        readable = [d.name for d in domains if "describe" not in d.errors] + [c.name for c in collections]
        if readable:
            flagged = Counter(name for name, _ in warnings).most_common(1)
            vector = [c.name for c in collections if c.vector]
            look = flagged[0][0] if flagged else (vector or readable)[0]
            blocks.append(_Next([
                (_call("indexes", look), "its indexes, which hold vectors, and their memory"),
                (_call("use", look), "make it the one later commands use"),
            ]))
        self._show(blocks)

    def _serverless_blocks(
        self, ov: Overview, groups: list[tuple[str, float, list[str]]], minimum: float, used: float | None
    ) -> list[Any]:
        prices = self.core.prices
        rows = [
            [f"Idle minimum: {label}", ", ".join(names), f"{ocus:g}", human_money(ocu_monthly_cost(ocus, prices))]
            for label, ocus, names in groups
        ]
        if used is not None and ov.ocus:
            rows.append([
                f"Used: average over the last {ov.hours}h (CloudWatch)",
                "all",
                f"{used:.1f} ({ov.ocus['indexing']:.1f} indexing + {ov.ocus['search']:.1f} search)",
                human_money(ocu_monthly_cost(used, prices)),
            ])
        if ov.max_indexing_ocus is not None or ov.max_search_ocus is not None:
            top = (ov.max_indexing_ocus or 0) + (ov.max_search_ocus or 0)
            rows.append([
                "Account limit: the most it scales to",
                "all",
                f"{top:g} ({ov.max_indexing_ocus or 0:g} indexing + {ov.max_search_ocus or 0:g} search)",
                human_money(ocu_monthly_cost(top, prices)),
            ])
        blocks: list[Any] = [_Table(
            ["Capacity", "Collections", "OCUs", "Est. $/month"],
            rows,
            title=f"Serverless capacity (OpenSearch Compute Units, ${prices['ocu_hour']:g} per OCU-hour; storage "
            f"${prices['serverless_storage']:g} per GB-month is extra)",
            max_rows=0,
        )]
        if minimum:
            blocks.append(_Note(
                f"Serverless bills at least {minimum:g} OCUs ({human_money(ocu_monthly_cost(minimum, prices))}/month) "
                f"for these collections even with no traffic: {SERVERLESS_MIN_OCUS[True]:g} for each group of "
                f"collections that share an encryption key, kind and standby setting "
                f"({SERVERLESS_MIN_OCUS[False]:g} without standby replicas). Delete collections you no longer use in "
                "the console to stop paying for their group."
            ))
        for section, permission, what in (
            ("capacity", "aoss:GetAccountSettings", "the account's capacity limits"),
            ("usage", "cloudwatch:GetMetricData", "the OCUs used (CloudWatch)"),
        ):
            if section in ov.errors:
                blocks.append(_Note(f"Couldn't read {what} ({_why(ov.errors[section], permission)})."))
        if used is None and "usage" not in ov.errors and ov.ocus is None and minimum:
            blocks.append(_Note(f"CloudWatch has no OCU numbers for the last {ov.hours}h, so the cost is the minimum."))
        return blocks

    # ------------------------------------------------------------------ indexes

    @_friendly_errors
    def indexes(self, target: str | None = None, *, hidden: bool = False) -> None:
        """Every index in a domain or collection: documents, size, shards, vector fields (dimensions, engine,
        similarity), documents missing a vector, the memory the vectors need against what the nodes have, and
        warnings. hidden=True adds system indexes (names starting with a dot)."""
        store = self._store(target)
        with self._progress("Counting vectors", unit="indexes") as tick:
            report = self.core.indexes(store, hidden=hidden, progress=tick)
        knn_memory = domain_knn_memory(store) if isinstance(store, Domain) else None
        version = store.version if isinstance(store, Domain) else None
        data_nodes = store.instance_count if isinstance(store, Domain) else (
            report.health.data_nodes if report.health else None)
        serverless = _is_serverless(store)
        ordered = sorted(report.indexes, key=lambda i: (not i.vectors, i.name))
        rows: list[list[Any]] = []
        warnings: list[list[str]] = []
        for info in ordered:
            found = [m for level, m in index_findings(info, store, version=version, data_nodes=data_nodes)
                     if level == "warn"]
            warnings += [[info.name, m] for m in found]
            memory = index_vector_memory(info)
            rows.append([
                info.name,
                _Tone(info.health, _state_tone(info.health)) if info.health else "-",
                _count(info.documents),
                human_size(info.size_bytes),
                "managed" if serverless else f"{info.shards} × {info.copies}" if info.shards else "-",
                _vector_label(info),
                _with_vector_cell(info),
                human_size(memory) if memory is not None else "-",
                _Tone(str(len(found)), "warn" if found else ""),
            ])
        need = sum(index_vector_memory(i) or 0 for i in report.vector_indexes)
        native = sum(index_vector_memory(i, native=True) or 0 for i in report.vector_indexes)
        cards: list[tuple[str, ...]] = [
            ("Indexes", f"{len(report.indexes):,}"),
            ("Vector indexes", f"{len(report.vector_indexes):,}"),
            ("Documents", _count(sum(i.documents or 0 for i in report.indexes))),
            ("Size", human_size(sum(i.size_bytes or 0 for i in report.indexes))),
            ("Est. vector memory", human_size(need) if need else "-"),
        ]
        if knn_memory:
            cards.append(("k-NN memory", human_size(knn_memory), "warn" if native > knn_memory else ""))
        if report.knn:
            cards.append(("Graph memory in use", human_size(report.knn.memory_bytes)))
        if report.health:
            cards.append(("Cluster health", report.health.status or "?", _state_tone(report.health.status)))
        subtitle = (
            f"{store.url or 'no endpoint'} · "
            + ("" if serverless else "shards: primaries × copies · ")
            + "vector memory: the HNSW / IVF graphs of all copies, estimated by OpenSearch's sizing rule"
        )
        blocks: list[Any] = [_Title(f"Indexes in {store.label}", subtitle), _Cards(cards)]
        notes = {
            "_cat/indices": "document counts and sizes",
            "_mapping": "some mappings",
            "_settings": "settings (shards, replicas, index.knn)",
            "segments": "segment counts",
            "health": "cluster health",
            "k-NN stats": "the k-NN plugin's memory numbers",
        }
        missing = [f"{what} ({report.errors[key]})" for key, what in notes.items() if key in report.errors]
        if missing:
            blocks.append(_Note("Couldn't read " + ", ".join(missing) + "."))
        own = (domain_findings(store, self.core.prices) if isinstance(store, Domain)
               else collection_findings(store) if isinstance(store, Collection) else [])
        blocks.append(_Findings(own + store_findings(report, knn_memory)))
        if not report.indexes:
            blocks.append(_Note(f"No indexes in {store.label} yet{'' if hidden else ' (hidden=True adds system ones)'}."))
            self._show(blocks)
            return
        if not report.vector_indexes:
            blocks.append(_Note("None of these indexes has a vector (knn_vector) field."))
        blocks.append(_Table(
            ["Index", "Health", "Documents", "Size", "Shards", "Vector fields", "With a vector", "Est. vector memory",
             "Warnings"],
            rows,
            max_rows=0,
        ))
        if warnings:
            blocks.append(_Table(
                ["Index", "Warning"],
                warnings,
                prose_cols=(1,),
                title="Warnings (index_info(name) shows every finding for one index)",
                max_rows=0,
            ))
        if report.vector_indexes:
            counts = Counter(name for name, _ in warnings)
            size = {i.name: i.documents or 0 for i in report.indexes}
            flagged = max(counts, key=lambda n: (counts[n], size.get(n, 0))) if counts else None
            look = flagged or max(report.vector_indexes, key=lambda i: i.documents or 0).name
            ref = f"{_target_arg(store)}/{look}"
            blocks.append(_Next([
                (_call("index_info", ref), "why it's flagged, and what to change" if flagged
                 else "its vector fields in plain English, and the query to copy"),
                (_call("sample", ref), "a few documents, and how healthy the vectors are"),
            ]))
        self._show(blocks)

    @_friendly_errors
    def index_info(self, index: str | None = None) -> None:
        """One index in plain English: its vector fields (dimensions, engine, algorithm and settings, similarity and
        what a score means), documents without a vector, the memory the vectors need against what the nodes have,
        the fields you can filter on, findings, and the k-NN query to copy. index='vectors-prod/docs', or 'docs'
        after use('vectors-prod')."""
        store, name = self._index_ref(index, "index_info")
        info = self.core.index(store, name)
        serverless = _is_serverless(store)
        blocks: list[Any] = [_Title(f"Index {info.name}", f"{store.label} · {store.url or 'no endpoint'}")]
        knn = None
        if info.vectors and not serverless:
            try:
                knn = self.core.knn_stats(store)
            except OpenSearchError as exc:
                blocks.append(_Note(f"Couldn't read the k-NN plugin's memory numbers ({exc.status} {exc.kind})."))
        knn_memory = domain_knn_memory(store) if isinstance(store, Domain) else None
        version = store.version if isinstance(store, Domain) else None
        data_nodes = store.instance_count if isinstance(store, Domain) else None
        need = index_vector_memory(info)
        native = index_vector_memory(info, native=True)
        cards: list[tuple[str, ...]] = [("Documents", _count(info.documents))]
        if info.vectors:
            cell = _with_vector_cell(info)
            cards.append(("With a vector", str(cell), getattr(cell, "tone", "")))
        cards += [
            ("Size", human_size(info.size_bytes)),
            ("Shards", "managed by Serverless" if serverless else _shards_label(info)),
        ]
        if info.health:
            cards.append(("Health", info.health, _state_tone(info.health)))
        if info.vectors:
            vf = info.vectors[0]
            cards += [
                ("Vector fields", f"{len(info.vectors):,}"),
                ("Dimensions", f"{vf.dimension:,}" if vf.dimension else "?"),
                ("Engine", f"{vf.engine or 'default'} {vf.algorithm.upper()}" if not vf.model_id else "trained model"),
                ("Similarity", _SPACE_NAMES.get(vf.space, vf.space)),
                ("Est. vector memory", human_size(need) if need is not None else "?",
                 "warn" if knn_memory and native and native > knn_memory else ""),
            ]
            if knn_memory:
                cards.append(("k-NN memory (all nodes)", human_size(knn_memory)))
            if knn and info.name in knn.by_index:
                cards.append(("Graphs loaded now", human_size(knn.by_index[info.name])))
        if info.created:
            cards.append(("Created", _fmt_dt(info.created)))
        blocks.append(_Cards(cards))
        found = index_findings(info, store, knn_memory=knn_memory, stats=knn, version=version, data_nodes=data_nodes)
        found += knn_findings(knn)
        blocks.append(_Findings(found, empty="No issues found by these checks."))
        if not info.vectors:
            blocks.append(_Note(
                f"{info.name} has no vector (knn_vector) fields, so it can't be searched by similarity. "
                f"indexes({_target_arg(store)!r}) shows which indexes have them."
            ))
        else:
            blocks += self._vector_blocks(store, info)
        rows = _filter_rows(info)
        if rows:
            blocks.append(_Table(
                ["Field", "Type", "Filter with (search(..., where=...))"],
                rows,
                title=f"Fields you can filter on ({len(rows):,})",
                code_cols=(2,),
                collapsed=len(rows) > 12,
            ))
        if info.mapping:
            blocks.append(_Text(json.dumps(info.mapping, indent=2, ensure_ascii=False), title="Mapping (JSON)",
                                collapsed=True))
        if info.settings:
            blocks.append(_Table(["Setting", "Value"], [[k, str(v)] for k, v in sorted(info.settings.items())],
                                 title="Settings", collapsed=True))
        if not info.vectors:
            blocks.append(_Next([(_call("indexes", _target_arg(store)), "which of its indexes hold vectors")]))
        else:
            blocks.append(_Next([
                (_call("sample", *self._ref_args(store, info.name)), "a few documents, and how healthy the vectors are"),
                (_call("search", "a question your documents answer", **self._ref_kwargs(store, info.name)),
                 "the documents nearest to it"),
            ]))
        self._show(blocks)

    def _vector_blocks(self, store: Any, info: IndexInfo) -> list[Any]:
        rows = []
        for vf in info.vectors:
            count = info.with_vector.get(vf.path)
            vectors = vector_count(info, vf)
            memory = vector_memory(vf, vectors or 0) if vectors is not None else None
            rows.append([
                vf.path + (f"\n(nested in {vf.nested})" if vf.nested else ""),
                f"{vf.dimension:,}" if vf.dimension else "?",
                f"{vf.engine or 'default engine'} {vf.algorithm.upper()}" if not vf.model_id else "trained model",
                _SPACE_NAMES.get(vf.space, vf.space) + ("" if vf.space_type else " (default)"),
                _method_settings(vf),
                _compression_label(vf),
                _count(count),
                human_size(memory * info.copies) if memory is not None else "?",
            ])
        blocks: list[Any] = [_Table(
            ["Vector field", "Dimensions", "Engine", "Similarity", "Graph settings", "Compression", "Documents",
             "Est. memory (all copies)"],
            rows,
            title="Vector fields",
            max_rows=0,
        )]
        for space in dict.fromkeys(vf.space for vf in info.vectors):
            first = next(vf for vf in info.vectors if vf.space == space)
            blocks.append(_Note(f"How to read scores ({_SPACE_NAMES.get(space, space)}): {score_meaning(first)}."))
        if info.text_field:
            blocks.append(_Note(f"Each document's text is in '{info.text_field}'; search() and sample() show it."))
        vf = info.vectors[0]
        example = knn_query(vf, [0.0], 10, exclude=[v.path for v in info.vectors])
        blocks.append(_Text(
            f"query_vector = ...  # {f'{vf.dimension:,}' if vf.dimension else 'the'} numbers from the model that "
            "embedded the documents\n"
            + query_python(example, info.name),
            title="The k-NN query, to copy",
            code=True,
        ))
        return blocks

    # ------------------------------------------------------------------ vectors

    @_friendly_errors
    def sample(self, index: str | None = None, n: int = 10, *, check: int = 200, where: Any = None) -> None:
        """A few random documents (text, where each came from, its fields, and its vector as dimensions and length),
        and a check of `check` documents' vectors: lengths, all-zero vectors and exact repeats. where= picks the
        documents, e.g. where={'embedding': ('missing',)} for those without a vector."""
        store, name = self._index_ref(index, "sample")
        n = _as_int(n, "n")
        result = self.core.sample(store, name, n, check=check, where=where)
        info = result.info or IndexInfo(name)
        docs = result.docs
        shown = docs[: max(0, n)]
        how = "random documents" if result.random else "the first documents (random sampling isn't available here)"
        blocks: list[Any] = [_Title(
            f"Sample of {result.index}",
            f"{store.label} · {how}" + (f" · matching {describe_filter(where)}" if where else ""),
        )]
        cards: list[tuple[str, ...]] = [
            ("Documents" + (" matching" if where else ""), _count(result.total)),
            ("Looked at", f"{len(docs):,}"),
        ]
        vf = info.vectors[0] if info.vectors else None
        check_result = result.checks.get(vf.path) if vf else None
        findings: list[tuple[str, str]] = []
        for field_vf in info.vectors:
            if field_vf.path in result.checks:
                findings += vector_findings(result.checks[field_vf.path], field_vf)
        if vf and check_result and check_result.vectors:
            c = check_result
            length = (
                "1.00 (unit length)" if c.unit_length
                else f"{c.norm_min:.2f} – {c.norm_max:.2f}" if c.norm_min is not None and c.norm_max is not None
                else "?"
            )
            cards += [
                ("Dimensions", ", ".join(f"{d:,}" for d in c.dims) or "?"),
                ("Vector length", length),
                ("All-zero vectors", f"{c.zeros:,}", "warn" if c.zeros else ""),
                ("Repeats", f"{c.repeats:,}", "warn" if any(lvl == "warn" and "repeat" in m for lvl, m in findings)
                 else ""),
            ]
        blocks.append(_Cards(cards))
        if not docs:
            blocks.append(_Note("No documents" + (f" match {describe_filter(where)}." if where else " in this index.")))
            self._show(blocks)
            return
        blocks.append(_Findings(findings, empty="The sampled vectors look healthy: one size, no zeros, no repeats."
                                if info.vectors else ""))
        text_field = info.text_field
        vector_paths = [v.path for v in info.vectors]
        source_fields = {source_field(doc)[0] for doc in shown} - {""}
        skip = {text_field or "", *vector_paths, "AMAZON_BEDROCK_METADATA", *source_fields}
        extra = _metadata_columns(shown, skip, self.max_columns)
        has_source = bool(source_fields)
        headers = ["ID"] + ([f"Text ({text_field})"] if text_field else []) + (["Source"] if has_source else [])
        headers += [f"Vector ({vector_paths[0]})"] if vector_paths else []
        headers += extra
        rows = []
        for doc in shown:
            row: list[Any] = [str(doc.get("_id", ""))]
            if text_field:
                row.append(_hit_text(doc, text_field))
            if has_source:
                row.append(_clip(source_of(doc), 120))
            if vector_paths:
                vectors = values_at(doc, vector_paths[0])
                row.append(vector_summary(vectors[0]) if vectors else "(none)")
            flat = _flat(doc)
            row += [_format_value(flat.get(column), 60) for column in extra]
            rows.append(row)
        blocks.append(_Table(headers, rows, title=f"Documents ({len(shown):,} of the {len(docs):,} looked at)",
                             max_rows=0))
        if len(info.vectors) > 1 or (check_result and check_result.vectors):
            vector_rows = []
            for path, c in result.checks.items():
                vector_rows.append([
                    path,
                    f"{c.vectors:,}",
                    ", ".join(f"{d:,}" for d in c.dims) or "-",
                    f"{c.norm_min:.3f} – {c.norm_max:.3f} (mean {c.norm_mean:.3f})" if c.norm_min is not None else "-",
                    f"{c.zeros:,}",
                    f"{c.repeats:,}",
                    f"{c.missing:,}",
                ])
            blocks.append(_Table(
                ["Vector field", "Vectors checked", "Dimensions", "Length (min – max)", "All zeros", "Repeats",
                 "Documents without"],
                vector_rows,
                title=f"Vectors in the {len(docs):,} documents looked at",
                max_rows=0,
            ))
        first = str(shown[0].get("_id", "")) if shown else ""
        steps = []
        if first and info.vectors:
            steps.append((_call("search", like=first, **self._ref_kwargs(store, result.index)),
                          "the documents nearest to the first one"))
        steps.append((_call("index_info", *self._ref_args(store, result.index)), "its settings and memory in plain English"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def search(
        self,
        query: str | None = None,
        *,
        index: str | None = None,
        vector: Any = None,
        like: str | None = None,
        k: int = 10,
        where: Any = None,
        field: str | None = None,
        model: str | None = None,
        embed: Callable[[str], Any] | None = None,
    ) -> None:
        """The k documents nearest a question: rank, score and the similarity it stands for (cosine or distance),
        text, source and fields. The question is embedded with a Bedrock model (Titan Text Embeddings V2 for 256,
        512 or 1,024 dimensions; model= picks another) or with embed=your_function; vector=[...] searches with your
        own vector, and like='doc-id' finds the documents nearest an existing one. where= filters, e.g.
        where={'lang': 'en', 'year': ('>=', 2024)}."""
        store, name = self._index_ref(index, "search")
        result = self.core.search(store, name, query, vector=vector, like=like, k=k, field=field, where=where,
                                  model=model, embed=embed)
        info = result.info or IndexInfo(name)
        vf = info.vector(result.field) if info.vectors else VectorField(result.field)
        if result.like is not None:
            title = f"Nearest to document {result.like}"
        elif result.text is not None:
            title = f"Nearest to “{_clip(result.text, 70)}”"
        else:
            title = "Nearest to your vector"
        similarity = similarity_name(vf)
        best_label = _BEST.get(similarity, f"Smallest {similarity}")
        sub = f"{result.index} in {store.label} · field '{result.field}' · {_SPACE_NAMES.get(vf.space, vf.space)} · k={result.k}"
        if where:
            sub += f" · where {describe_filter(where)}"
        best = result.hits[0] if result.hits else None
        cards: list[tuple[str, ...]] = [
            ("Results", f"{len(result.hits):,}"),
            ("Best score", f"{best.score:.3f}" if best else "-"),
            (best_label, f"{best.similarity:.3f}" if best and best.similarity is not None else "-"),
            ("Search time", f"{result.took_ms:,} ms" if result.took_ms is not None else "-"),
            ("Total time", f"{result.seconds:.1f}s"),
        ]
        emb = result.embedding
        if emb:
            label = EMBEDDING_MODELS.get(_base_model(emb.model), ("", 0, (), 0.0, emb.model))[4]
            cost = human_money(emb.cost) if emb.cost is not None else "?"
            cards.append(("Embedded with", label))
            cards.append(("Embedding cost", f"{cost}" + (f" ({emb.tokens:,} tokens)" if emb.tokens else "")))
        blocks: list[Any] = [_Title(title, sub), _Cards(cards)]
        if emb and model is None:
            blocks.append(_Note(
                f"The question was embedded with {cards[-2][1]}, picked because '{result.field}' has "
                f"{vf.dimension:,} dimensions. Vectors from different models aren't comparable: if the documents "
                "were embedded another way, pass model= or embed=your_function."
            ))
        blocks.append(_Findings(search_findings(result, vf, result.hit_norms)))
        if not result.hits:
            blocks.append(_Note(
                "No results." + (f" Nothing near the query matches {describe_filter(where)}." if where else "")
            ))
        else:
            text_field = result.text_field
            sources = [h.source for h in result.hits]
            source_fields = {source_field(s)[0] for s in sources} - {""}
            skip = {text_field or "", "AMAZON_BEDROCK_METADATA", *source_fields}
            extra = _metadata_columns(sources, skip, self.max_columns)
            has_source = bool(source_fields)
            headers = ["#", "Score", similarity, "ID"] + (["Text"] if text_field else [])
            headers += (["Source"] if has_source else []) + extra
            rows = []
            for hit in result.hits:
                row: list[Any] = [
                    str(hit.rank),
                    f"{hit.score:.4f}",
                    f"{hit.similarity:.4f}" if hit.similarity is not None else "-",
                    hit.id,
                ]
                if text_field:
                    row.append(_hit_text(hit.source, text_field))
                if has_source:
                    row.append(_clip(source_of(hit.source), 120))
                flat = _flat(hit.source)
                row += [_format_value(flat.get(column), 60) for column in extra]
                rows.append(row)
            blocks.append(_Table(headers, rows, max_rows=0))
            blocks.append(_Note(f"How to read scores: {score_meaning(vf)}."))
            if result.like is not None and not result.excluded_self and not where:
                blocks.append(_Note(f"Document {result.like} itself wasn't among the nearest: other documents hold "
                                    "the same vector (repeats), or the search is approximate."))
        blocks.append(_Text(
            "query_vector = ...  # the vector searched with\n" + query_python(result.query, result.index),
            title="This search as Python (opensearch-py)",
            code=True,
            collapsed=True,
        ))
        steps = []
        if result.hits:
            steps.append((_call("search", like=result.hits[0].id, **self._ref_kwargs(store, result.index)),
                          "the documents nearest to the top result"))
        steps.append((_call("sample", *self._ref_args(store, result.index)), "a few documents, and how healthy the vectors are"))
        blocks.append(_Next(steps))
        self._show(blocks)
