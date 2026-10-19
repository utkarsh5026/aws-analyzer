"""
The dataclasses BedrockChatAnalyzer returns: knowledge bases, data sources and files to pick from,
the request's settings, an answer, and a test run's score. A Field, a Batch and a Sweep are next to
the functions their properties call, in analysis.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .._bedrock.helpers import SEARCHABLE, source_name
from .._bedrock.models import Citation, KBDocument, Passage
from .._kit.deps import _require

from .helpers import _norm

if TYPE_CHECKING:
    from .analysis.schema import Field


@dataclass
class KnowledgeBase:
    """
    A knowledge base you can chat with.
    """

    id: str
    name: str = ""
    status: str = ""  # ACTIVE | CREATING | UPDATING | DELETING | FAILED ...
    description: str = ""
    updated: datetime | None = None


@dataclass
class DataSource:
    """
    One of a knowledge base's data sources (an S3 bucket, a website, ...): questions can be asked of it alone.
    """

    id: str
    name: str = ""
    status: str = ""  # AVAILABLE | DELETING | DELETE_UNSUCCESSFUL
    description: str = ""
    updated: datetime | None = None


@dataclass
class FileList:
    """
    The files of a knowledge base, as ListKnowledgeBaseDocuments lists them, to pick from.
    """

    kb_id: str
    documents: list[KBDocument] = field(default_factory=list)
    truncated: bool = False  # stopped at FILE_LIMIT: there are more
    errors: dict[str, str] = field(default_factory=dict)  # data source ID -> why its files couldn't be listed

    @property
    def searchable(self) -> list[KBDocument]:
        """
        The files a question can find something in: indexed, at least in part.
        """
        return [d for d in self.documents if d.status in SEARCHABLE]


@dataclass
class Schema:
    """
    Every setting the installed boto3 can send with RetrieveAndGenerate, read from its service model, plus the
    short names, plain-English descriptions and defaults this chat adds. request_schema() builds it.
    """

    fields: dict[str, Field]  # key -> Field, in the order the chat shows them
    auto: list[tuple[tuple[str, ...], str]] = field(default_factory=list)  # required, one possible value: filled in
    input_shape: Any = None  # botocore's input shape, for validate_request()
    boto: str = ""  # the botocore version the fields came from
    retrieve_shape: Any = None  # Retrieve's input shape, for validate_request() on a retrieve-only request

    def find(self, name: Any) -> Field:
        """
        The field a name means: its short name or another name ('topP'), its path (full, from
        knowledgeBaseConfiguration, or just its last parts if they're unique), in any case, with or without
        underscores. A ValueError suggests close names.
        """
        text = str(name if name is not None else "").strip()
        if not text:
            raise ValueError(
                "Name a setting, like 'temperature', or a field's path, like "
                "'generationConfiguration.performanceConfig.latency'. fields() lists them all."
            )
        if text in self.fields:
            return self.fields[text]
        wanted = _norm(text)
        for f in self.fields.values():
            if wanted in {_norm(f.key), _norm(f.where), _norm(".".join(f.path)), *map(_norm, f.names)}:
                return f
        tail = [f for f in self.fields.values() if ("." + _norm(f.where)).endswith("." + wanted)]
        if len(tail) == 1:
            return tail[0]
        if tail:
            raise ValueError(f"{text!r} could be {' or '.join(repr(f.key) for f in tail)}: pass one of those")
        known = list(self.fields) + [n for f in self.fields.values() for n in f.names]
        lowered = {k.lower(): k for k in known}
        close = [lowered[c] for c in difflib.get_close_matches(text.lower(), list(lowered), n=3, cutoff=0.6)]
        hint = f" Did you mean {' or '.join(map(repr, dict.fromkeys(close)))}?" if close else ""
        raise ValueError(
            f"No setting {text!r}.{hint} fields() lists every one RetrieveAndGenerate takes in this boto3 "
            f"(botocore {self.boto}); pip install -U boto3 adds fields AWS added since."
        )

    def search(self, text: Any, limit: int | None = None) -> list[Field]:
        """
        The fields some words could mean, best first: every word must be in a field's name, label, other names,
        path or description, and names count most. A misspelt name ('temprature') finds the closest ones; blank
        text gives every field, in order.
        """
        words = str(text or "").lower().split()
        if not words:
            return list(self.fields.values())[:limit]
        scored = []
        for order, f in enumerate(self.fields.values()):
            names = [_norm(n) for n in (f.key, f.label, *f.names)]
            path, doc, score = _norm(".".join(f.path)), f.doc.lower(), 0
            for word in words:
                wanted = _norm(word)
                points = (100 if wanted in names else 60 if any(n.startswith(wanted) for n in names)
                          else 40 if any(wanted in n for n in names) else 25 if wanted in path
                          else 10 if word in doc else 0)
                if not points:
                    break
                score += points
            else:
                scored.append((-score, order, f))
        if scored:
            return [f for _, _, f in sorted(scored)][:limit]
        known: dict[str, list[Field]] = {}
        for f in self.fields.values():
            for name in dict.fromkeys(_norm(n) for n in (f.key, f.label, *f.names)):
                known.setdefault(name, []).append(f)
        close = difflib.get_close_matches(_norm(" ".join(words)), list(known), n=5, cutoff=0.6)
        return list({f.key: f for c in close for f in known[c]}.values())[:limit]


@dataclass
class Answer:
    """
    One question of the conversation and Bedrock's answer: the text, the passages it cites, and the exact
    request and response. Asked retrieve-only (Retrieve), there's no answer: text is empty and sources holds every
    passage the search found, best first, each with its score.
    """

    question: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    sources: list[Passage] = field(default_factory=list)  # [1] is sources[0]; only the cited ones come back
    session_id: str | None = None  # Bedrock's conversation; the next question passes it to follow up
    guardrail_action: str | None = None  # INTERVENED when a guardrail stepped in
    kb_id: str = ""
    kb_name: str = ""
    model: str = ""  # the model ID or inference profile that answered
    settings: dict[str, Any] = field(default_factory=dict)  # the settings it was asked with
    request: dict[str, Any] = field(default_factory=dict)  # exactly what was sent
    response: dict[str, Any] = field(default_factory=dict)  # what came back (built from the events when streamed)
    streamed: bool = False
    seconds: float = 0.0
    first_words: float | None = None  # seconds until the first words, when streamed
    input_tokens: int = 0  # estimated from characters: RetrieveAndGenerate doesn't report tokens
    output_tokens: int = 0
    notes: list[str] = field(default_factory=list)  # what happened on the way (a new session, no streaming)
    data_sources: dict[str, str] = field(default_factory=dict)  # ID -> name of the only ones searched; {} = all
    files: list[str] = field(default_factory=list)  # s3:// paths of the only files searched; [] = all
    retrieve_only: bool = False  # a search without an answer (Retrieve): sources are every passage it found, ranked

    @property
    def cited(self) -> list[int]:
        """
        The source numbers the answer cites.
        """
        return sorted({n for c in self.citations for n in c.sources})

    @property
    def grounded_share(self) -> float:
        """
        The share of the answer's characters (spaces aside) inside a span that cites a source.
        """
        covered = [False] * len(self.text)
        for c in self.citations:
            if c.sources:
                for i in range(max(0, c.start), min(len(self.text), c.end)):
                    covered[i] = True
        chars = [i for i, ch in enumerate(self.text) if not ch.isspace()]
        return sum(covered[i] for i in chars) / len(chars) if chars else 0.0

    def to_df(self):
        """
        One row per source: its number, where it's from, its page, its score (retrieve-only searches) and its
        text.
        """
        pd = _require("pandas", "Answer.to_df")
        return pd.DataFrame(
            [
                {"n": i, "source": source_name(p.uri), "page": p.page, "score": p.score, "uri": p.uri, "text": p.text,
                 "metadata": p.metadata}
                for i, p in enumerate(self.sources, 1)
            ]
        )


@dataclass
class RunScore:
    """
    How a test run did, in numbers, on the questions it's compared on: what ranks the setups of a sweep and the runs
    of the same questions. rank_runs() makes them.
    """

    questions: int = 0  # questions counted: they came back, or Bedrock refused them
    answered: int = 0  # answers that say something (not "unable to assist"); for a search, searches that found passages
    grounded: float | None = None  # the average grounded share of those answers
    checked: int = 0  # questions with an expected source
    hits: int = 0  # ... whose answer cited it (for a search: that found it)
    mrr: float | None = None  # a search: the mean of 1 / the expected source's rank (None when nothing was expected)
    failed: int = 0  # questions Bedrock refused
    cost: float | None = None  # estimated USD; None when a model's price is unknown
    seconds: float | None = None  # the average time a question took

    def per_question(self) -> float | None:
        """
        The estimated cost of one question.
        """
        return None if self.cost is None or not self.questions else self.cost / self.questions
