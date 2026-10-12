"""What bedrock_kb.py and bedrock_chat.py share, written once: Bedrock's list prices and model helpers, the
passages, documents and models Bedrock returns, metadata filters, token and cost estimates, the answer's markdown
with its citations, and the _Answer and _Json blocks with their drawings.

Only those two import it (rules.py checks): it's Bedrock's, not every service's, so it isn't in _kit. Each imports
the names it uses, so `bedrock_kb.MODEL_PRICES` and `bedrock_chat.Passage` still work, and the price tables are one
dict for both: change prices with BedrockKBAnalyzer(prices=..., model_prices=...) or BedrockChatAnalyzer(...), which
copy them, rather than by editing the tables.
"""

from __future__ import annotations

import json
import math
import re
import textwrap
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import unquote

from botocore.exceptions import ClientError

from ._kit.blocks import _Report, _draws
from ._kit.fmt import _plural
from ._kit.render import _SELECT, _prose
from ._kit.text import _esc


# ----------------------------------------------------------------------------- helpers: prices, models, names, words

# USD, us-east-1 list prices, read from aws.amazon.com/bedrock/pricing and
# aws.amazon.com/opensearch-service/pricing on 2026-09-25 and checked against the AWS Price List API on
# 2026-09-27. Other regions differ; pass prices={...} to BedrockKBAnalyzer or BedrockChatAnalyzer to use your own.
BEDROCK_PRICES: dict[str, float] = {
    "opensearch_ocu_hour": 0.24,  # per OpenSearch Compute Unit hour; indexing and search OCUs cost the same
    "opensearch_min_ocus": 2,  # a classic vector collection bills 1 indexing + 1 search OCU even when idle
    "rerank_per_1k_queries": 2.00,  # Cohere Rerank 3.5, the default reranker
    "amazon_rerank_per_1k_queries": 1.00,  # Amazon Rerank 1.0 (us-west-2 price; it isn't offered in us-east-1)
    "embedding_per_million_tokens": 0.02,  # Amazon Titan Text Embeddings V2, to embed each question
}

# USD per million input / output tokens, on demand in us-east-1 (in-region and US cross-region inference
# profiles), read from aws.amazon.com/bedrock/pricing on 2026-09-25 and checked against the AWS Price List API on
# 2026-09-27. Global profiles ('global.' IDs) use GLOBAL_MODEL_PRICES below. Keys are pieces of Bedrock model IDs,
# and the longest matching key wins; pass model_prices={...} to either analyzer to add models or use your own prices.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (11.00, 55.00),
    "claude-fable-5": (11.00, 55.00),
    "claude-opus-5-5": (4.40, 22.00),
    "claude-opus-5": (5.50, 27.50),
    "claude-sonnet-5": (2.20, 11.00),
    "claude-opus-4-8": (5.50, 27.50),
    "claude-opus-4-7": (5.50, 27.50),
    "claude-opus-4-6": (5.50, 27.50),
    "claude-opus-4-5": (5.50, 27.50),
    "claude-opus-4-1": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-6": (3.30, 16.50),
    "claude-sonnet-4-5": (3.30, 16.50),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.10, 5.50),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-haiku": (0.25, 1.25),
    "nova-premier": (2.50, 12.50),
    "nova-pro": (0.80, 3.20),
    "nova-2-lite": (0.33, 2.75),
    "nova-lite": (0.06, 0.24),
    "nova-micro": (0.035, 0.14),
    "llama4-maverick": (0.24, 0.97),
    "llama4-scout": (0.17, 0.66),
    "llama3-3-70b": (0.72, 0.72),
    "mistral-large-3": (0.50, 1.50),
    "deepseek.r1": (1.35, 5.40),
}

# USD per million input / output tokens through a global cross-region profile ('global.' IDs), for the models where
# it costs less than MODEL_PRICES (same source and dates). model_price() uses these for a 'global.' ID unless
# model_prices= changed that model's price, in which case your price applies to every profile.
GLOBAL_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "nova-2-lite": (0.30, 2.50),
}

DEFAULT_MODEL = "anthropic.claude-haiku-4-5"  # Claude Haiku 4.5; resolve_model() finds the ID or profile to call it with
_MODEL_ALIASES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5-1",
    "nova": "nova-pro",
    "llama": "llama4-maverick",
    "mistral": "mistral-large-3",
    "deepseek": "deepseek.r1",
}


_KB_ID_RE = re.compile(r"^[0-9A-Z]{10}$")
_KB_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:bedrock:[\w-]+:\d{12}:knowledge-base/([0-9A-Za-z]{10})$"
)


def parse_kb_ref(ref: str) -> tuple[str, str]:
    """How a knowledge base was named: 'ABCDE12345' -> ('id', 'ABCDE12345'), 'support-docs' -> ('name',
    'support-docs'), and an ARN (arn:aws:bedrock:<region>:<account>:knowledge-base/ABCDE12345) -> ('arn', 'ABCDE12345'),
    the ID inside it."""
    text = str(ref or "").strip()
    if not text:
        raise ValueError(
            "Pass a knowledge base: its name, its 10-character ID or its ARN (kbs() lists them)"
        )
    match = _KB_ARN_RE.match(text)
    if match:
        return "arn", match.group(1)
    if text.lower().startswith("arn:"):
        raise ValueError(
            f"{text!r} isn't a knowledge base ARN; those look like "
            "arn:aws:bedrock:<region>:<account>:knowledge-base/<ID>"
        )
    return ("id", text) if _KB_ID_RE.match(text) else ("name", text)


def _model_id(arn: str | None) -> str:
    """'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0' -> 'amazon.titan-embed-text-v2:0'."""
    return (arn or "").rsplit("/", 1)[-1]


def source_name(uri: str | None) -> str:
    """The file name in a source location: 's3://docs/policies/refund-policy.pdf' -> 'refund-policy.pdf',
    'https://example.com/help/refunds?x=1' -> 'refunds', 'https://example.com/' -> 'example.com'."""
    if not uri:
        return ""
    text = str(uri).split("?", 1)[0].split("#", 1)[0]
    scheme, _, rest = text.partition("://")
    if not rest:
        rest, scheme = scheme, ""
    parts = [p for p in rest.split("/") if p]
    if not parts:
        return str(uri)
    if len(parts) == 1 and scheme not in ("s3", ""):
        return parts[0]  # just a host
    return unquote(parts[-1])


SEARCHABLE = {"INDEXED", "PARTIALLY_INDEXED", "METADATA_PARTIALLY_INDEXED", "METADATA_UPDATE_FAILED"}  # has chunks


def estimate_tokens(text: str | None) -> int:
    """Roughly how many tokens `text` is: one per 4 characters. An estimate: label it as one wherever it's shown."""
    return math.ceil(len(text or "") / 4)


_BEDROCK_META_PREFIX = "x-amz-bedrock-kb-"


def split_metadata(md: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A retrieved passage's metadata -> (system, user). System keys lose their prefix: 'source-uri', 'chunk-id',
    'data-source-id', 'document-page-number'. User keys are the knowledge base author's own metadata (from
    <file>.metadata.json), which is what where= filters on."""
    system: dict[str, Any] = {}
    user: dict[str, Any] = {}
    for key, value in (md or {}).items():
        if key.startswith(_BEDROCK_META_PREFIX):
            system[key[len(_BEDROCK_META_PREFIX) :]] = value
        elif key.startswith("AMAZON_BEDROCK_"):
            system[key] = value
        else:
            user[key] = value
    return system, user


_STOPWORDS = frozenset(
    """
a about after all also am an and any are as at be been but by can could did do does doing for from had has have how
i if in into is it its me my no not of on or our she should so than that the their them then there these they this
those to too us was we were what when where which who whom why will with would you your
""".split()
)
_TERM_RE = re.compile(r"[^\W_](?:[\w'’.\-/#]*[^\W_])?")


def question_terms(question: str) -> list[str]:
    """The words of a question worth highlighting, lower case, each once: no stopwords, 3+ characters or holding a
    digit. 'How long do refunds take for order E1234?' -> ['long', 'refunds', 'take', 'order', 'e1234']."""
    terms: list[str] = []
    for match in _TERM_RE.finditer(question or ""):
        word = match.group(0).lower()
        if (
            word in _STOPWORDS
            or (len(word) < 3 and not any(c.isdigit() for c in word))
            or word in terms
        ):
            continue
        terms.append(word)
    return terms


def _terms_regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    """One regex (with a single group) matching any of `terms` as whole words, plus simple plural / verb endings:
    'refunds' also matches 'refund' and 'refunded'."""
    stems = set()
    for term in terms:
        term = term.lower()
        if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
            term = term[:-1]
        if term:
            stems.add(term)
    if not stems:
        return None
    words = "|".join(re.escape(t) for t in sorted(stems, key=len, reverse=True))
    return re.compile(rf"(?<!\w)((?:{words})(?:s|es|ed|ing|'s)?)(?!\w)", re.IGNORECASE)


def best_snippet(text: str, terms: Iterable[str], width: int = 320) -> str:
    """The `width`-character window of `text` holding the most question words, cut at spaces, with … where the text
    was cut. Whitespace is collapsed. Text without any of the words gives its start."""
    flat = " ".join((text or "").split())
    if len(flat) <= width:
        return flat
    regex = _terms_regex(terms)
    hits = [m.start() for m in regex.finditer(flat)] if regex else []
    start, best = 0, -1
    for pos in hits:
        begin = max(0, min(pos - width // 5, len(flat) - width))
        count = sum(1 for h in hits if begin <= h < begin + width - 10)
        if count > best:
            start, best = begin, count
    end = min(len(flat), start + width)
    if start > 0:
        space = flat.find(" ", start, start + 30)
        start = space + 1 if space != -1 else start
    if end < len(flat):
        space = flat.rfind(" ", start + width // 2, end)
        end = space if space != -1 else end
    return (
        ("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else "")
    )


def _family_match(family: str, model_id: str) -> bool:
    """Whether `model_id` belongs to a model family: 'claude-opus-5' matches 'anthropic.claude-opus-5-v1:0' and
    'us.anthropic.claude-opus-5-20260301-v1:0', but not 'anthropic.claude-opus-5-5' (a newer version, another model);
    'llama4-maverick' matches 'meta.llama4-maverick-17b-instruct-v1:0' (17b is its size, not a version)."""
    version = r"(?!\d)(?!-\d{1,2}(?:[-:.]|$))"  # right after the family, '-5' then '-', ':', '.' or the end
    return (
        re.search(re.escape(family.lower()) + version, (model_id or "").lower())
        is not None
    )


def model_price(
    model: str, model_prices: dict[str, tuple[float, float]] | None = None
) -> tuple[float, float] | None:
    """(USD per 1M input tokens, per 1M output tokens) for a model ID, profile ID or ARN; None when it isn't in the
    table. The longest matching key wins, so 'claude-opus-5-5' isn't priced as 'claude-opus-5'. A global profile
    ('global.anthropic.claude-opus-5-v1:0') gets its GLOBAL_MODEL_PRICES price unless `model_prices` changed the
    model's list price."""
    prices = MODEL_PRICES if model_prices is None else model_prices
    is_global = (model or "").rsplit("/", 1)[-1].lower().startswith("global.")
    for key in sorted(prices, key=len, reverse=True):
        if _family_match(key, model):
            if (
                is_global
                and key in GLOBAL_MODEL_PRICES
                and prices[key] == MODEL_PRICES.get(key)
            ):
                return GLOBAL_MODEL_PRICES[key]
            return prices[key]
    return None


def short_model(model: str) -> str:
    """'us.anthropic.claude-opus-5-v1:0' -> 'claude-opus-5', 'amazon.nova-pro-v1:0' -> 'nova-pro'."""
    name = (model or "").rsplit("/", 1)[-1]
    name = re.sub(r"^(us|eu|apac|ap|ca|us-gov|jp|au|global)\.", "", name)
    name = (
        name.split(".", 1)[1]
        if "." in name and not name.split(".", 1)[0][-1:].isdigit()
        else name
    )
    return re.sub(r"(-\d{8})?(-v\d+)?(:\d+)*$", "", name) or model


DEFAULT_RERANK_MODEL = (
    "cohere.rerank-v3-5:0"  # rerank=True uses this; Amazon's is 'amazon.rerank-v1:0'
)
_RERANK_ALIASES = {"cohere": DEFAULT_RERANK_MODEL, "amazon": "amazon.rerank-v1:0"}


# ----------------------------------------------------------------------------- data models

@dataclass
class KBDocument:
    """One document (file or record) of a data source, and whether it's searchable."""

    data_source_id: str
    uri: str  # s3://... for S3 data sources, the document ID for custom ones
    status: str  # INDEXED | FAILED | PENDING | IN_PROGRESS | IGNORED | PARTIALLY_INDEXED | ...
    reason: str = ""  # why it failed or was ignored
    updated: datetime | None = None

    @property
    def name(self) -> str:
        return source_name(self.uri)


@dataclass
class Passage:
    """One retrieved chunk of a document, with where it came from."""

    rank: int  # 1 = best match
    text: str
    score: float | None = (
        None  # relevance; only comparable with other scores of the same search
    )
    uri: str = ""  # s3://bucket/key, a web page URL, ... (see location_type)
    location_type: str = ""  # S3 | WEB | CONFLUENCE | SALESFORCE | SHAREPOINT | CUSTOM | KENDRA | SQL | ...
    page: int | None = None  # page number in a PDF, when the parser recorded one
    chunk_id: str = ""
    data_source_id: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )  # the author's metadata only (what where= filters on)
    content_type: str = "TEXT"  # TEXT | IMAGE | ROW | AUDIO | VIDEO
    row: dict[str, Any] | None = (
        None  # ROW results (SQL knowledge bases): column -> value
    )

    @property
    def source(self) -> str:
        """'refund-policy.pdf p.3'."""
        name = source_name(self.uri) or self.uri or "(unknown source)"
        return f"{name} p.{self.page}" if self.page is not None else name

    @property
    def key(self) -> str:
        """What identifies this chunk when comparing searches: its chunk ID, or its source and text."""
        return self.chunk_id or f"{self.uri}|{self.page}|{self.text[:500]}"


@dataclass
class Citation:
    """A span of an answer and the sources behind it."""

    start: int  # character offsets into Answer.text; end is exclusive
    end: int
    text: str
    sources: list[int] = field(
        default_factory=list
    )  # 1-based numbers into Answer.sources


@dataclass
class ModelInfo:
    """A model ask() can use, and how to call it."""

    id: str  # the foundation model ID, e.g. 'anthropic.claude-opus-5'
    name: str = ""
    provider: str = ""
    invoke_id: str = ""  # what to pass as model=: the model ID, or the inference profile that serves it
    arn: str = ""  # the model's ARN, or the inference profile's when it needs one
    via: str = "on-demand"  # 'on-demand' | 'inference profile' | 'provisioned only'
    price_in: float | None = (
        None  # USD per 1M input tokens (None = not in the price table)
    )
    price_out: float | None = None
    status: str = "ACTIVE"  # ACTIVE | LEGACY


# ----------------------------------------------------------------------------- reading responses, filters and costs

def parse_document(desc: dict[str, Any]) -> KBDocument:
    """One ListKnowledgeBaseDocuments 'documentDetails' entry -> KBDocument."""
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
    """One Retrieve result (or one RetrieveAndGenerate retrievedReference) -> Passage."""
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
    """A Retrieve response -> Passages, best first."""
    return [
        parse_passage(ref, i)
        for i, ref in enumerate(resp.get("retrievalResults") or [], 1)
    ]


def _span(text: str, part: dict[str, Any]) -> tuple[int, int]:
    """Where a RetrieveAndGenerate citation's text sits in the answer. The API doesn't say whether span.end is
    inclusive, so the offsets are checked against the text itself."""
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


def parse_models(
    summaries: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None,
    region: str = "",
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> list[ModelInfo]:
    """ListFoundationModels summaries + ListInferenceProfiles summaries -> the text models ask() can use. A model
    that can't be called on demand gets the inference profile for this region's geography (e.g. 'us.' in us-east-1),
    else a global one. profiles=None means they couldn't be listed, so such a model's profile is unknown."""
    known = profiles is not None
    profiles = profiles or []
    served: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        for model_id in dict.fromkeys(
            _model_id(m.get("modelArn")) for m in profile.get("models") or []
        ):
            served.setdefault(model_id, []).append(profile)
    geo = {"us": "us.", "eu": "eu.", "ap": "apac.", "ca": "ca.", "sa": "sa."}.get(
        region.split("-")[0], ""
    )

    def preference(profile: dict[str, Any]) -> tuple[int, str]:
        pid = profile.get("inferenceProfileId", "")
        return (
            0 if geo and pid.startswith(geo) else 1 if pid.startswith("global.") else 2,
            pid,
        )

    found = []
    for summary in summaries:
        model_id = summary.get("modelId", "")
        if "TEXT" not in (summary.get("outputModalities") or ["TEXT"]) or re.search(
            "rerank|embed", model_id
        ):
            continue
        options = sorted(served.get(model_id, []), key=preference)
        info = ModelInfo(
            id=model_id,
            name=summary.get("modelName", ""),
            provider=summary.get("providerName", ""),
            invoke_id=model_id,
            arn=summary.get("modelArn", ""),
            status=(summary.get("modelLifecycle") or {}).get("status", "ACTIVE"),
        )
        if "ON_DEMAND" not in (summary.get("inferenceTypesSupported") or []):
            if options:
                info.via, info.invoke_id, info.arn = (
                    "inference profile",
                    options[0]["inferenceProfileId"],
                    options[0]["inferenceProfileArn"],
                )
            else:
                info.via = (
                    "provisioned only" if known else "inference profile (unknown)"
                )
        price = model_price(info.invoke_id, model_prices)  # a global profile can cost less
        if price:
            info.price_in, info.price_out = price
        found.append(info)
    for profile in profiles:
        if profile.get("type") == "APPLICATION":
            model = _model_id(((profile.get("models") or [{}])[0]).get("modelArn"))
            price = model_price(model, model_prices)
            found.append(
                ModelInfo(
                    id=profile["inferenceProfileArn"],
                    name=profile.get("inferenceProfileName", ""),
                    provider="your inference profile",
                    invoke_id=profile["inferenceProfileArn"],
                    arn=profile["inferenceProfileArn"],
                    via="inference profile",
                    price_in=price[0] if price else None,
                    price_out=price[1] if price else None,
                )
            )
    return sorted(found, key=lambda m: (m.provider.lower(), m.name.lower(), m.id))


_FILTER_OPERATORS = {
    "=": "equals",
    "==": "equals",
    "!=": "notEquals",
    "<>": "notEquals",
    ">": "greaterThan",
    ">=": "greaterThanOrEquals",
    "<": "lessThan",
    "<=": "lessThanOrEquals",
    "in": "in",
    "not_in": "notIn",
    "begins_with": "startsWith",
    "contains": "stringContains",
    "list_contains": "listContains",
    "between": "between",
}


_FILTER_KEYS = {
    "equals",
    "notEquals",
    "greaterThan",
    "greaterThanOrEquals",
    "lessThan",
    "lessThanOrEquals",
    "in",
    "notIn",
    "startsWith",
    "listContains",
    "stringContains",
    "andAll",
    "orAll",
}


def _filter_value(value: Any) -> Any:
    """A metadata value Bedrock accepts: tuples and sets become lists, dates ISO text."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_filter_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _is_bedrock_filter(where: Any) -> bool:
    return (
        isinstance(where, dict)
        and len(where) == 1
        and next(iter(where)) in _FILTER_KEYS
    )


def _conditions(key: str, spec: Any) -> list[dict[str, Any]]:
    """One where= entry -> Bedrock filter conditions (two for 'between')."""
    if not isinstance(key, str) or not key:
        raise ValueError(f"where= keys are metadata attribute names, got {key!r}")
    if not isinstance(spec, tuple):
        if isinstance(spec, (list, set, frozenset)):  # a list of allowed values
            return [{"in": {"key": key, "value": _filter_value(spec)}}]
        return [{"equals": {"key": key, "value": _filter_value(spec)}}]
    op = spec[0].lower() if spec and isinstance(spec[0], str) else None
    if op not in _FILTER_OPERATORS:
        raise ValueError(
            f"Can't read the condition {spec!r} on {key!r}: use a value, or (operator, value) with one of "
            + ", ".join(_FILTER_OPERATORS)
        )
    args = list(spec[1:])
    if op == "between":
        if len(args) != 2:
            raise ValueError(
                f"'between' takes two values, like ('between', 2020, 2024); got {spec!r}"
            )
        return [
            {"greaterThanOrEquals": {"key": key, "value": _filter_value(args[0])}},
            {"lessThanOrEquals": {"key": key, "value": _filter_value(args[1])}},
        ]
    if op in ("in", "not_in") and len(args) > 1:
        args = [args]  # ('in', 'a', 'b') means ('in', ['a', 'b'])
    if len(args) != 1:
        raise ValueError(
            f"{op!r} takes one value, like ({op!r}, {'[...]' if 'in' in op else 'x'}); got {spec!r}"
        )
    value = _filter_value(args[0])
    if op in ("in", "not_in") and not isinstance(value, list):
        value = [value]
    name = _FILTER_OPERATORS[op]
    if op == "contains" and not isinstance(value, str):
        name = "listContains"  # a number or boolean can only be an element of a list attribute
    return [{name: {"key": key, "value": value}}]


def build_filter(where: Any) -> dict[str, Any] | None:
    """`where` -> a Bedrock RetrievalFilter on the documents' metadata. Takes None, a filter Bedrock already
    understands ({'andAll': [...]}, {'equals': {...}}, ...), or a dict of attribute -> value or (operator, *values),
    which must all match:

        {'team': 'billing'}                     team = 'billing'
        {'team': ['billing', 'support']}        team is one of these
        {'year': ('>=', 2024)}                  also '=', '!=', '>', '<', '<=', ('between', 2020, 2024)
        {'region': ('in', ['eu', 'uk'])}        also ('not_in', [...])
        {'doc_id': ('begins_with', 'POL-')}     text starting with this
        {'title': ('contains', 'refund')}       text containing this, or a list with an element containing it
        {'tags': ('list_contains', 'gdpr')}     a list attribute holding exactly this element

    Metadata comes from a <file>.metadata.json next to each file; values are typed, so 2024 and '2024' differ."""
    if where is None:
        return None
    if _is_bedrock_filter(where):
        return where
    if not isinstance(where, dict):
        raise ValueError(
            "where= takes a dict like {'team': 'billing', 'year': ('>=', 2024)}, or a Bedrock "
            "RetrievalFilter like {'equals': {'key': 'team', 'value': 'billing'}}"
        )
    conditions = [
        condition for key, spec in where.items() for condition in _conditions(key, spec)
    ]
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"andAll": conditions}


DATA_SOURCE_KEY = "x-amz-bedrock-kb-data-source-id"  # Bedrock tags every chunk with its data source's ID


def data_source_filter(ids: Iterable[str]) -> dict[str, Any] | None:
    """A RetrievalFilter that keeps only passages from these data sources (by ID): {'equals': ...} for one, {'in':
    ...} for several, None for none. Bedrock tags every chunk with its data source's ID, so this needs no
    .metadata.json files."""
    unique = list(dict.fromkeys(str(i) for i in ids if i))
    if not unique:
        return None
    if len(unique) == 1:
        return {"equals": {"key": DATA_SOURCE_KEY, "value": unique[0]}}
    return {"in": {"key": DATA_SOURCE_KEY, "value": unique}}


def with_data_sources(
    condition: dict[str, Any] | None, ids: Iterable[str]
) -> dict[str, Any] | None:
    """A RetrievalFilter (build_filter's) narrowed to the data sources with these IDs: both must match."""
    only = data_source_filter(ids)
    if only is None:
        return condition
    if not condition:
        return only
    rest = condition["andAll"] if list(condition) == ["andAll"] else [condition]
    return {"andAll": [only, *rest]}


def describe_sources(sources: dict[str, str]) -> str:
    """{ID: name} of the data sources searched -> "data source 'faq'" / "data sources 'faq' and 'policies'"."""
    names = [repr(name or ds_id) for ds_id, name in sources.items()]
    if not names:
        return "every data source"
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"data source{'s' if len(names) > 1 else ''} {listed}"


_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")


def match_expected(passage: Passage, expected: Any) -> bool:
    """Whether a passage is the one a test question expects: `expected` is a case-insensitive piece of its URI, its file
    name or its text (a list means any of them)."""
    wanted = [expected] if isinstance(expected, str) else list(expected or [])
    haystack = f"{passage.uri}\n{source_name(passage.uri)}\n{passage.text}".lower()
    return any(str(w).strip().lower() in haystack for w in wanted if str(w).strip())


def query_cost(
    n_queries: int,
    rerank: bool | str = False,
    prices: dict[str, float] | None = None,
    *,
    question_tokens: int = 20,
) -> float:
    """Estimated USD for n searches: embedding each question (about question_tokens tokens) and, with `rerank`, the
    reranking model: True or 'cohere' for Cohere Rerank 3.5, 'amazon' or 'amazon.rerank-v1:0' (or its ARN) for
    Amazon Rerank. The vector store's own charges aren't included."""
    prices = BEDROCK_PRICES if prices is None else prices
    per_query = question_tokens * prices["embedding_per_million_tokens"] / 1e6
    if rerank:
        model = _RERANK_ALIASES.get(str(rerank).lower(), str(rerank))
        key = (
            "amazon_rerank_per_1k_queries"
            if "amazon.rerank" in model
            else "rerank_per_1k_queries"
        )
        per_query += prices[key] / 1000
    return n_queries * per_query


def generation_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    model_prices: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """Estimated USD for one answer; None when the model isn't in the price table (pass model_prices=...)."""
    price = model_price(model, model_prices)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1e6


_REFUSAL = "unable to assist you with this request"


# ----------------------------------------------------------------------------- knowledge bases by name

def _match_kb(names: dict[str, str], kind: str, value: str) -> str | None:
    """The ID in {ID: name} that `value` names (an ID, or a name in any case); None if nothing matches."""
    if kind == "id" and value in names:
        return value
    hits = [kb_id for kb_id, name in names.items() if name.lower() == value.lower()]
    if len(hits) > 1:
        raise ValueError(
            f"{len(hits)} knowledge bases are named {value!r}; pass one of their IDs: {', '.join(hits)}"
        )
    return hits[0] if hits else None


def _match_sources(
    names: dict[str, str], wanted: list[str]
) -> tuple[dict[str, str], list[str]]:
    """({ID: name} of the data sources `wanted` names, by ID or by name in any case, [what matched none])."""
    found: dict[str, str] = {}
    missing: list[str] = []
    for w in wanted:
        hits = [w] if w in names else [i for i, n in names.items() if n.lower() == w.lower()]
        if not hits:
            missing.append(w)
        for ds_id in hits:
            found[ds_id] = names[ds_id]
    return found, missing


# ----------------------------------------------------------------------------- the answer and JSON blocks

@dataclass
class _Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    inline: bool = False  # the text already holds [n] markers (engine='converse')


@dataclass
class _Json:
    value: Any
    title: str = ""
    open_depth: int = 8  # levels shown open; deeper objects are folded (HTML)
    marks: dict[tuple[str, ...], str] = field(default_factory=dict)  # path -> why it's highlighted (your settings)
    notes: dict[tuple[str, ...], str] = field(default_factory=dict)  # path -> a note shown after the value
    collapsed: bool = False  # folded under its title in HTML


def _plain_json(value: Any) -> Any:
    """A deep copy holding only JSON types: tuples become lists, dates ISO text, anything else its str()."""
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)))


def _json_html(
    value: Any,
    *,
    open_depth: int = 8,
    marks: dict[tuple[str, ...], str] | None = None,
    notes: dict[tuple[str, ...], str] | None = None,
) -> str:
    """JSON as highlighted HTML: keys, text, numbers and true / false / null in their own colours, each object and list
    folding under a click (plain <details>, no script), paths in `marks` highlighted with a tooltip, and `notes`
    after their values. Every piece of text is escaped: responses quote untrusted knowledge base content."""
    marks, notes = marks or {}, notes or {}

    def scalar(v: Any) -> str:
        if v is None or isinstance(v, bool):
            return f'<span class="jl">{json.dumps(v)}</span>'
        if isinstance(v, (int, float)):
            return f'<span class="jn">{_esc(json.dumps(v))}</span>'
        text = v if isinstance(v, str) else (v.isoformat() if hasattr(v, "isoformat") else str(v))
        return f'<span class="js">{_esc(json.dumps(text, ensure_ascii=False))}</span>'

    def node(label: str, v: Any, path: tuple[Any, ...], depth: int, comma: bool) -> str:
        tail = "," if comma else ""
        note = f'<span class="jc">{_esc(notes[path])}</span>' if path in notes else ""
        if path in marks:
            label = f'<span class="jm" title="{_esc(marks[path])}">{label}</span>'
        items = list(v.items()) if isinstance(v, dict) else list(enumerate(v)) if isinstance(v, (list, tuple)) else None
        if not items:
            body = scalar(v) if items is None else ("{}" if isinstance(v, dict) else "[]")
            return f"<div>{label}{body}{tail}{note}</div>"
        opener, closer = ("{", "}") if isinstance(v, dict) else ("[", "]")
        count = _plural(len(items), "key" if isinstance(v, dict) else "item")
        children = "".join(
            node(f'<span class="jk">{_esc(json.dumps(str(k), ensure_ascii=False))}</span>: '
                 if isinstance(v, dict) else "", child, (*path, k), depth + 1, i < len(items) - 1)
            for i, (k, child) in enumerate(items)
        )
        is_open = " open" if depth < open_depth else ""
        return (f"<details{is_open}><summary>{label}{opener}<span class=\"jx\"> {count} {closer}{tail}</span>{note}"
                f"</summary><div class=\"ji\">{children}</div><div>{closer}{tail}</div></details>")

    return f'<div class="json">{node("", value, (), 0, False)}</div>'


@_draws(_Json, "html")
def _json_block_html(block: _Json, report: _Report) -> str:
    tree = _json_html(block.value, open_depth=block.open_depth, marks=block.marks, notes=block.notes)
    if block.collapsed:
        return f'<details class="sec"><summary>{_prose(block.title or "JSON")}</summary>{tree}</details>'
    return (f"<h4>{_prose(block.title)}</h4>" if block.title else "") + tree


@_draws(_Json, "text")
def _json_block_lines(block: _Json, report: _Report) -> list[str]:
    head = ["", f"-- {block.title} --"] if block.title else []
    return head + [json.dumps(_plain_json(block.value), indent=2, ensure_ascii=False)]


def _highlight(text: str, terms: Iterable[str]) -> str:
    """HTML for `text` with the question's words in <mark>. The text is split on the words and each piece escaped
    before it's wrapped, so markup inside a passage (knowledge base content is untrusted) stays text."""
    regex = _terms_regex(terms)
    if regex is None:
        return _esc(text)
    return "".join(
        f"<mark>{_esc(piece)}</mark>" if i % 2 else _esc(piece)
        for i, piece in enumerate(regex.split(text))
    )


def _split_marks(span: str) -> tuple[str, str]:
    """'returned within 14 days. ' -> ('returned within 14 days', '. '): where a citation marker goes."""
    stripped = span.rstrip()
    body = stripped.rstrip(".!?:;,")
    return body, span[len(body) :]


def _with_markers(text: str, citations: list[Citation]) -> str:
    """The answer with [n] markers after each cited span, before its closing punctuation: 'days [1].'"""
    out, pos = [], 0
    for c in sorted((c for c in citations if c.sources), key=lambda c: c.end):
        end = min(len(text), c.end)
        if end <= pos:
            continue
        body, tail = _split_marks(text[pos:end])
        out.append(body + " " + "".join(f"[{n}]" for n in c.sources) + tail)
        pos = end
    return "".join(out) + text[pos:]


# Markdown: models often answer in it (lists, **bold**, headings, tables, code). It's laid out with the stdlib, and
# every piece of text is escaped before it's wrapped: answers can quote untrusted knowledge base content, so raw HTML
# in them stays text, links go only to http(s) and mailto, and pictures become links (nothing loads from elsewhere).
_MD_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")
_MD_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
_MD_RULE_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_MD_ITEM_RE = re.compile(r"^([ \t]*)([-*+•]|\d{1,9}[.)])(?:[ \t]+|$)")
_MD_QUOTE_RE = re.compile(r"^ {0,3}>[ \t]?")
_MD_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_MD_TABLE_RULE_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_MD_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_MD_AUTOLINK_RE = re.compile(r"<((?:https?://|mailto:)[^\s<>]+)>", re.IGNORECASE)
_MD_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_MD_TAGS = {"em": ("<em>", "</em>"), "strong": ("<strong>", "</strong>"), "del": ("<del>", "</del>"),
            "strongem": ("<strong><em>", "</em></strong>")}
_MD_MAX_DEPTH = 8  # nesting (lists in quotes in lists...) beyond this is read as plain paragraphs

_Line = tuple[int, str]  # (where the text starts in the answer, the text): offsets keep citations in place


def _md_indent(line: str) -> int:
    """Columns of leading whitespace, a tab counting to the next multiple of 4."""
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4 - n % 4
        else:
            break
    return n


def _md_dedent(item: _Line, cols: int) -> _Line:
    offset, line = item
    i = n = 0
    while i < len(line) and n < cols and line[i] in " \t":
        n += 1 if line[i] == " " else 4 - n % 4
        i += 1
    return offset + i, line[i:]


def _md_lstrip(item: _Line) -> _Line:
    offset, line = item
    rest = line.lstrip()
    return offset + len(line) - len(rest), rest


def _md_cells(item: _Line) -> list[_Line]:
    """A table row's cells, on its unescaped pipes outside `code`, with a leading and trailing pipe dropped."""
    offset, line = item
    cells, start, k, tick = [], 0, 0, False
    while k < len(line):
        ch = line[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "`":
            tick = not tick
        elif ch == "|" and not tick:
            cells.append((offset + start, line[start:k]))
            start = k + 1
        k += 1
    cells.append((offset + start, line[start:]))
    if cells and not cells[0][1].strip() and line.lstrip().startswith("|"):
        cells = cells[1:]
    if cells and not cells[-1][1].strip() and line.rstrip().endswith("|"):
        cells = cells[:-1]
    return [(o + len(c) - len(c.lstrip()), c.strip()) for o, c in cells]


def _md_table_start(lines: list[_Line], i: int) -> bool:
    if i + 1 >= len(lines) or "|" not in lines[i][1] or not _MD_TABLE_RULE_RE.match(lines[i + 1][1]):
        return False
    rule = lines[i + 1][1]
    return "-" in rule and ("|" in rule or "|" in lines[i][1]) and (
        len(_md_cells(lines[i])) == len(_md_cells(lines[i + 1])))


def _md_starts_block(lines: list[_Line], i: int) -> bool:
    """Whether line i starts a block that ends a paragraph above it."""
    line = lines[i][1]
    return bool(_MD_FENCE_RE.match(line) or _MD_HEADING_RE.match(line) or _MD_RULE_RE.match(line)
                or _MD_QUOTE_RE.match(line) or _MD_ITEM_RE.match(line) or _md_table_start(lines, i))


def _md_blocks(lines: list[_Line], depth: int = 0) -> list[tuple[Any, ...]]:
    """Markdown lines -> blocks: ('p', lines), ('h', level, lines), ('hr',), ('code', language, lines),
    ('quote', blocks), ('list', ordered, start, items, loose) and ('table', aligns, header, rows)."""
    blocks: list[tuple[Any, ...]] = []
    i = 0
    while i < len(lines):
        offset, line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = _MD_FENCE_RE.match(line)
        if fence:
            mark, body = fence.group(2), []
            i += 1
            while i < len(lines):
                closing = lines[i][1].strip()
                if closing.startswith(mark) and not closing.strip(mark[0]):
                    i += 1
                    break
                body.append(_md_dedent(lines[i], len(fence.group(1))))
                i += 1
            blocks.append(("code", fence.group(3), body))
            continue
        heading = _MD_HEADING_RE.match(line)
        if heading:
            text = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", line[heading.end():]).rstrip()
            blocks.append(("h", len(heading.group(1)), [(offset + heading.end(), text)]))
            i += 1
            continue
        if _MD_RULE_RE.match(line):
            blocks.append(("hr",))
            i += 1
            continue
        if _MD_QUOTE_RE.match(line) and depth < _MD_MAX_DEPTH:
            inner = []
            while i < len(lines) and lines[i][1].strip():
                quoted = _MD_QUOTE_RE.match(lines[i][1])
                if not quoted and _md_starts_block(lines, i):
                    break
                inner.append((lines[i][0] + quoted.end(), lines[i][1][quoted.end():]) if quoted
                             else _md_lstrip(lines[i]))
                i += 1
            blocks.append(("quote", _md_blocks(inner, depth + 1)))
            continue
        if _MD_ITEM_RE.match(line) and depth < _MD_MAX_DEPTH:
            block, i = _md_list(lines, i, depth)
            blocks.append(block)
            continue
        if _md_table_start(lines, i):
            aligns = ["c" if c.startswith(":") and c.endswith(":") else "r" if c.endswith(":") else
                      "l" if c.startswith(":") else "" for _, c in _md_cells(lines[i + 1])]
            header, rows = _md_cells(lines[i]), []
            i += 2
            while i < len(lines) and "|" in lines[i][1] and lines[i][1].strip():
                cells = _md_cells(lines[i])[: len(header)]
                rows.append(cells + [(lines[i][0], "")] * (len(header) - len(cells)))
                i += 1
            blocks.append(("table", aligns, header, rows))
            continue
        para = [_md_lstrip(lines[i])]
        i += 1
        level = 0
        while i < len(lines) and lines[i][1].strip():
            setext = _MD_SETEXT_RE.match(lines[i][1])
            if setext:
                level = 1 if setext.group(1)[0] == "=" else 2
                i += 1
                break
            if _md_starts_block(lines, i):
                break
            para.append(_md_lstrip(lines[i]))
            i += 1
        blocks.append(("h", level, para) if level else ("p", para))
    return blocks


def _md_list(lines: list[_Line], i: int, depth: int) -> tuple[tuple[Any, ...], int]:
    """The list starting at line i (its items' lines, nested ones dedented) and the line after it. Lenient where
    models are: any bullet character continues a bullet list, and nested lists can be indented by 2, 3 or 4."""
    first = _MD_ITEM_RE.match(lines[i][1])
    assert first is not None
    indent, ordered = _md_indent(first.group(1)), first.group(2)[0].isdigit()
    start = int(first.group(2)[:-1]) if ordered else 1
    items: list[list[tuple[Any, ...]]] = []
    loose = False
    while i < len(lines):
        offset, line = lines[i]
        item = _MD_ITEM_RE.match(line)
        if (not item or _md_indent(item.group(1)) > indent or item.group(2)[0].isdigit() != ordered
                or _MD_RULE_RE.match(line)):
            break
        content = len(item.group(0)) if item.group(0).strip() != item.group(0) else len(item.group(0)) + 1
        body: list[_Line] = [(offset + item.end(), line[item.end():])]
        i += 1
        blank = False
        while i < len(lines):
            o, text = lines[i]
            if not text.strip():
                body.append((o, ""))
                blank = True
                i += 1
                continue
            cols = _md_indent(text)
            if cols > indent and not _MD_RULE_RE.match(text):
                body.append(_md_dedent(lines[i], min(cols, content)))
                loose = loose or (blank and not _MD_ITEM_RE.match(body[-1][1]))
                blank = False
                i += 1
                continue
            if blank or _md_starts_block(lines, i):
                break
            body.append(_md_lstrip(lines[i]))  # a paragraph's next line, not indented
            i += 1
        while body and not body[-1][1].strip():
            body.pop()
        items.append(_md_blocks(body, depth + 1))
        if blank and i < len(lines):
            follows = _MD_ITEM_RE.match(lines[i][1])
            if follows and _md_indent(follows.group(1)) <= indent and follows.group(2)[0].isdigit() == ordered:
                loose = True
            else:
                break
    return ("list", ordered, start, items, loose), i


def _md_source(lines: list[_Line]) -> tuple[str, list[int]]:
    """Lines joined with newlines, and where each character of that is in the answer."""
    parts: list[str] = []
    where: list[int] = []
    for k, (offset, line) in enumerate(lines):
        if k:
            parts.append("\n")
            where.append(lines[k - 1][0] + len(lines[k - 1][1]))
        parts.append(line)
        where.extend(range(offset, offset + len(line)))
    return "".join(parts), where


def _md_link_target(url: str) -> str:
    """The URL a link may open: http(s) and mailto only, else '' (shown as text)."""
    url = url.strip().strip("<>")
    return url if url.lower().startswith(("http://", "https://", "mailto:")) else ""


class _Markdown:
    """Markdown -> HTML, with the cited spans of the answer shaded and the [n] markers placed after them.
    Citations hold offsets into the raw text, so every character keeps where it came from (`where`)."""

    def __init__(self, text: str, citations: Iterable[Citation] = (), inline: bool = False):
        self.text, self.inline = text, inline
        self.cited = bytearray(len(text))
        marks: dict[int, list[int]] = {}
        for c in citations:
            start, end = max(0, c.start), min(len(text), c.end)
            if not c.sources or end <= start:
                continue
            self.cited[start:end] = b"\x01" * (end - start)
            if not inline:  # the marker goes after the span's last word, before its closing punctuation or markup
                k = end
                while k > start + 1 and text[k - 1] in " \t\r\n.!?:;,*_~`":
                    k -= 1
                marks.setdefault(k - 1, []).extend(c.sources)
        self.marks = [(at, "".join(f"[{n}]" for n in dict.fromkeys(sources))) for at, sources in sorted(marks.items())]
        self.next_mark = 0
        self.no_closer: dict[tuple[str, int, int], int] = {}  # (delimiter, run, end) -> searched from here, none found

    def html(self) -> str:
        lines, at = [], 0
        for line in self.text.split("\n"):
            lines.append((at, line[:-1] if line.endswith("\r") else line))
            at += len(line) + 1
        out = self.blocks(_md_blocks(lines))
        rest = self.marks_before(len(self.text) + 1)
        return out + (f"<p>{rest}</p>" if rest else "")

    # -------------------------------------------------------------- text and markers

    def marks_before(self, offset: int) -> str:
        """Markers placed before this offset not shown yet (their character was markup): shown now."""
        out = []
        while self.next_mark < len(self.marks) and self.marks[self.next_mark][0] < offset:
            out.append(f"<sup>{self.marks[self.next_mark][1]}</sup>")
            self.next_mark += 1
        return "".join(out)

    def plain(self, text: str, code: bool) -> str:
        if not self.inline or code:
            return _esc(text)
        return "".join(f"<sup>[{_esc(piece)}]</sup>" if i % 2 else _esc(piece)
                       for i, piece in enumerate(_MARKER_RE.split(text)))

    def chars(self, s: str, where: list[int], i: int, j: int, code: bool = False) -> str:
        """s[i:j] escaped, cited runs in <span class="cite">, each marker after the character it follows."""
        out: list[str] = []
        run: list[str] = []  # the HTML of the run so far: shaded throughout, or not at all
        text: list[str] = []  # its characters not escaped yet
        shaded = [False]

        def add(markup: str = "") -> None:
            if text:
                run.append(self.plain("".join(text), code))
                text.clear()
            if markup:
                run.append(markup)

        def close() -> None:
            add()
            if run:
                out.append(f'<span class="cite">{"".join(run)}</span>' if shaded[0] else "".join(run))
                run.clear()

        for k in range(i, j):
            at = where[k]
            pending = self.marks_before(at)
            if pending:
                add(pending)
            cited = bool(at < len(self.cited) and self.cited[at])
            if cited != shaded[0]:
                close()
                shaded[0] = cited
            text.append(s[k])
            if self.next_mark < len(self.marks) and self.marks[self.next_mark][0] == at:
                add(f"<sup>{self.marks[self.next_mark][1]}</sup>")
                self.next_mark += 1
        close()
        return "".join(out)

    # ---------------------------------------------------------------------- blocks

    def inline_html(self, lines: list[_Line]) -> str:
        s, where = _md_source(lines)
        out = self.nodes(s, where, self.parse(s, 0, len(s)))
        return out + self.marks_before(where[-1] + 1 if where else 0)

    def blocks(self, blocks: list[tuple[Any, ...]], tight: bool = False) -> str:
        out = []
        for block in blocks:
            kind = block[0]
            if kind == "p":
                inner = self.inline_html(block[1])
                out.append(inner if tight else f"<p>{inner}</p>")
            elif kind == "h":
                out.append(f"<h{block[1]}>{self.inline_html(block[2])}</h{block[1]}>")
            elif kind == "hr":
                out.append("<hr>")
            elif kind == "code":
                s, where = _md_source(block[2])
                lang = f'<span class="lang">{_esc(block[1])}</span>' if block[1] else ""
                body = self.chars(s, where, 0, len(s), code=True) + self.marks_before(where[-1] + 1 if where else 0)
                out.append(f'<div class="pre">{lang}<pre{_SELECT}><code>{body}</code></pre></div>')
            elif kind == "quote":
                out.append(f"<blockquote>{self.blocks(block[1])}</blockquote>")
            elif kind == "list":
                _, ordered, start, items, loose = block
                tag = "ol" if ordered else "ul"
                first = f' start="{start}"' if ordered and start != 1 else ""
                inner = "".join(f"<li>{self.blocks(item, tight=not loose)}</li>" for item in items)
                out.append(f"<{tag}{first}>{inner}</{tag}>")
            elif kind == "table":
                _, aligns, header, rows = block

                def cell(tag: str, j: int, item: _Line) -> str:
                    align = f' class="{aligns[j]}"' if j < len(aligns) and aligns[j] else ""
                    return f"<{tag}{align}>{self.inline_html([item])}</{tag}>"

                head = "".join(cell("th", j, c) for j, c in enumerate(header))
                body = "".join("<tr>" + "".join(cell("td", j, c) for j, c in enumerate(row)) + "</tr>" for row in rows)
                out.append(f'<div class="mdt"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        return "".join(out)

    # ---------------------------------------------------------------------- inline

    def parse(self, s: str, i: int, j: int, depth: int = 0) -> list[tuple[Any, ...]]:
        """Inline markdown in s[i:j] -> nodes: ('t', i, j) text, ('code', i, j), ('br',), (tag, children) for em /
        strong / del, and ('a', href, children). Anything that doesn't close is text."""
        nodes: list[tuple[Any, ...]] = []
        text_from, k = i, i

        def flush(upto: int) -> None:
            if upto > text_from:
                nodes.append(("t", text_from, upto))

        while k < j:
            ch = s[k]
            found: tuple[tuple[Any, ...], int] | None = None
            if ch == "\\" and k + 1 < j and s[k + 1] in _MD_PUNCT:
                found = ("t", k + 1, k + 2), k + 2
            elif ch == "\n":
                found = ("br",), k + 1
            elif ch == "`":
                run = self.run(s, k, j, "`")
                close = self.code_close(s, k + run, j, run)
                if close < 0:
                    k += run
                    continue
                a, b = k + run, close
                if b - a >= 2 and s[a] == " " and s[b - 1] == " " and s[a:b].strip():
                    a, b = a + 1, b - 1
                found = ("code", a, b), close + run
            elif ch in "*_~" and depth < _MD_MAX_DEPTH:
                emphasis = self.emphasis(s, k, j)
                if emphasis is None:
                    k += self.run(s, k, j, ch)
                    continue
                tag, a, b, after = emphasis
                found = (tag, self.parse(s, a, b, depth + 1)), after
            elif ch == "[" or (ch == "!" and s.startswith("[", k + 1)):
                link = self.link(s, k + (ch == "!"), j)
                if link is not None:
                    a, b, url, after = link
                    children = self.parse(s, a, b, depth + 1) if b > a else [("t", k, after)]
                    found = ("a", _md_link_target(url), children), after
            elif ch == "<":
                auto = _MD_AUTOLINK_RE.match(s, k, j)
                if auto:
                    found = ("a", _md_link_target(auto.group(1)), [("t", k + 1, auto.end() - 1)]), auto.end()
            elif ch in "hH" and (k == 0 or not (s[k - 1].isalnum() or s[k - 1] in "/:@")):
                url = _MD_URL_RE.match(s, k, j)
                if url:
                    end = url.end()
                    while end > k and (s[end - 1] in ".,;:!?*_~" or (s[end - 1] == ")" and
                                                                     s.count("(", k, end) < s.count(")", k, end))):
                        end -= 1
                    found = ("a", _md_link_target(s[k:end]), [("t", k, end)]), end
            if found is None:
                k += 1
                continue
            flush(k)
            nodes.append(found[0])
            k = text_from = found[1]
        flush(j)
        return nodes

    @staticmethod
    def run(s: str, k: int, j: int, ch: str) -> int:
        n = k
        while n < j and s[n] == ch:
            n += 1
        return n - k

    def code_close(self, s: str, k: int, j: int, run: int) -> int:
        """Where a code span of `run` backticks closes, or -1."""
        while True:
            k = s.find("`" * run, k, j)
            if k < 0:
                return -1
            length = self.run(s, k, j, "`")
            if length == run:
                return k
            k += length

    def emphasis(self, s: str, k: int, j: int) -> tuple[str, int, int, int] | None:
        """*em*, **strong**, ***both***, _em_, __strong__ or ~~del~~ opening at k: (tag, inner start, inner end,
        after it), or None. An opener needs a non-space after it, a closer a non-space before it, and _ doesn't work
        inside a word (snake_case_names stay as they are)."""
        ch = s[k]
        run = self.run(s, k, j, ch)
        if run > 3 or (ch == "~" and run != 2):
            return None
        a = k + run
        if a >= j or s[a].isspace() or (ch == "_" and k > 0 and s[k - 1].isalnum()):
            return None
        key = (ch, run, j)
        if self.no_closer.get(key, j) <= a:
            return None
        n = a
        while n < j:
            c = s[n]
            if c == "\\":
                n += 2
                continue
            if c == "`":
                ticks = self.run(s, n, j, "`")
                close = self.code_close(s, n + ticks, j, ticks)
                n = close + ticks if close >= 0 else n + ticks
                continue
            if c == ch:
                length = self.run(s, n, j, ch)
                fits = not s[n - 1].isspace() and (ch != "_" or n + length >= len(s) or not s[n + length].isalnum())
                if fits and (length == run or (length == 3 and ch != "~")):
                    close = n + length - run if length == 3 else n  # in ***, this closer is the last `run` of them
                    tag = "del" if ch == "~" else {1: "em", 2: "strong", 3: "strongem"}[run]
                    return tag, a, close, close + run
                n += length
                continue
            n += 1
        self.no_closer[key] = min(a, self.no_closer.get(key, a))
        return None

    @staticmethod
    def link(s: str, k: int, j: int) -> tuple[int, int, str, int] | None:
        """[text](url) opening at k: (text start, text end, url, after it), or None."""
        if s.find("](", k, j) < 0:
            return None
        depth, n = 0, k
        while n < j:
            if s[n] == "\\":
                n += 2
                continue
            if s[n] == "[":
                depth += 1
            elif s[n] == "]":
                depth -= 1
                if depth == 0:
                    break
            n += 1
        if n >= j or not s.startswith("(", n + 1):
            return None
        parens, m = 0, n + 1
        while m < j and s[m] != "\n":
            if s[m] == "(":
                parens += 1
            elif s[m] == ")":
                parens -= 1
                if parens == 0:
                    target = s[n + 2:m].strip().split()
                    return k + 1, n, target[0] if target else "", m + 1
            m += 1
        return None

    def nodes(self, s: str, where: list[int], nodes: list[tuple[Any, ...]]) -> str:
        out = []
        for node in nodes:
            kind = node[0]
            if kind == "t":
                out.append(self.chars(s, where, node[1], node[2]))
            elif kind == "br":
                out.append("<br>")
            elif kind == "code":
                out.append(f"<code>{self.chars(s, where, node[1], node[2], code=True)}</code>")
            elif kind == "a":
                inner = self.nodes(s, where, node[2])
                out.append(f'<a href="{_esc(node[1])}" target="_blank" rel="noopener noreferrer">{inner}</a>'
                           if node[1] else inner)
            else:
                opening, closing = _MD_TAGS[kind]
                out.append(opening + self.nodes(s, where, node[1]) + closing)
        return "".join(out)


def _markdown_html(text: str, citations: Iterable[Citation] = (), inline: bool = False) -> str:
    """Markdown as HTML: paragraphs, headings, lists, quotes, code, tables, **bold**, *italic*, `code` and links.
    With citations, the cited spans are shaded and [n] follows each one; inline=True when the text already holds the
    [n] markers. A single line break stays a line break, so plain-text answers look as they were written."""
    return _Markdown(text, citations, inline).html()


def _answer_html(block: _Answer) -> str:
    """The answer, laid out from its markdown, with cited spans shaded and [n] superscripts. Every piece of text is
    escaped: answers can quote untrusted knowledge base content."""
    return _markdown_html(block.text, block.citations, block.inline)


@_draws(_Answer, "html")
def _answer_block_html(block: _Answer, report: _Report) -> str:
    return f'<div class="ans">{_answer_html(block)}</div>'


@_draws(_Answer, "text")
def _answer_block_lines(block: _Answer, report: _Report) -> list[str]:
    return _answer_lines(block.text if block.inline else _with_markers(block.text, block.citations), 100)


def _answer_lines(text: str, width: int, indent: str = "") -> list[str]:
    """An answer as text lines: its markdown kept as written (it reads well as text), long lines wrapped, a list
    item's continuation lined up under its text, and code blocks and table rows never wrapped."""
    out: list[str] = []
    fence = ""
    for line in text.split("\n"):
        opening = _MD_FENCE_RE.match(line)
        if fence or opening or line.lstrip().startswith("|"):
            out.append((indent + line).rstrip())
            if fence and line.strip().startswith(fence) and not line.strip().strip(fence[0]):
                fence = ""
            elif not fence and opening:
                fence = opening.group(2)
            continue
        item = _MD_ITEM_RE.match(line)
        hang = " " * (len(item.group(0)) if item else _md_indent(line))
        out += textwrap.wrap(line, width, initial_indent=indent, subsequent_indent=indent + hang) or [""]
    return out


# ----------------------------------------------------------------------------- View helpers

def _session_expired(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return error.get("Code") in (
        "ValidationException",
        "ResourceNotFoundException",
        "BadRequestException",
    ) and ("session" in str(error.get("Message", "")).lower())


def _per_million(price: float | None) -> str:
    """0.8 -> '$0.80', 0.035 -> '$0.035' (a price per million tokens); None -> '-'."""
    if price is None:
        return "-"
    return f"${price:,.2f}" if price >= 0.1 or price == 0 else f"${price:.3f}"
