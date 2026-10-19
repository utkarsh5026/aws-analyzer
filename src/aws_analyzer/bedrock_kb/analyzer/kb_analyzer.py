"""
BedrockKBAnalyzer, made of its reads: the session, prices and clients, over the mixins
in this package.
"""

from __future__ import annotations

import threading
from typing import Any

from botocore.exceptions import NoRegionError

from ..._bedrock.models import ModelInfo
from ..._bedrock.prices import BEDROCK_PRICES, MODEL_PRICES
from ..._kit.analyzer import _BaseAnalyzer

from .knowledge_bases import _KnowledgeBaseReads
from .search import _SearchReads
from .deciding import _DecidingReads
from .files import _FileReads


class BedrockKBAnalyzer(_KnowledgeBaseReads, _SearchReads, _DecidingReads, _FileReads, _BaseAnalyzer):
    """
    Pure-logic Bedrock Knowledge Bases analysis: every method returns data; nothing is printed or written.

    Methods take the knowledge base first, as an ID, a name (any case) or an ARN. Nothing here starts a sync or
    changes a document; where one is needed, sync_command() gives the command to run.
    `prices` and `model_prices` override BEDROCK_PRICES and MODEL_PRICES for cost estimates; `default_model` is the
    model ask() and generate() use when none is given (DEFAULT_MODEL, Claude Haiku 4.5, otherwise). `clients` pre-fills the boto3 clients by service name
    ('bedrock-agent', 'bedrock-agent-runtime', 'bedrock-runtime', 'bedrock', 's3'), e.g. to use stubbed ones.
    """

    _LIST_PRICES = BEDROCK_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        model_prices: dict[str, tuple[float, float]] | None = None,
        default_model: str | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["bedrock-agent"] = client
        self.model_prices = {**MODEL_PRICES, **(model_prices or {})}
        self.default_model = (
            default_model  # what model=None means (None: DEFAULT_MODEL, Claude Haiku 4.5)
        )
        self._models: list[ModelInfo] | None = None
        self._profiles: list[dict[str, Any]] = []
        self.model_errors: dict[
            str, str
        ] = {}  # 'profiles' -> error code, when inference profiles can't be listed
        self.max_workers = (
            8  # knowledge bases described in parallel by list_knowledge_bases
        )
        self._names: dict[str, str] | None = None  # knowledge base ID -> name
        self._source_names: dict[
            str, dict[str, str]
        ] = {}  # knowledge base ID -> {data source ID: name}, as last listed
        self._lock = threading.RLock()  # clients are made once, even when the explorer window reads from threads

    @property
    def client(self) -> Any:
        """
        The bedrock-agent client (settings, syncs, documents), made on first use so a missing region shows up as
        a readable error.
        """
        if "bedrock-agent" not in self._clients:
            with self._lock:
                if "bedrock-agent" not in self._clients:
                    try:
                        self._clients["bedrock-agent"] = self.session.client(
                            "bedrock-agent", config=self._config
                        )
                    except NoRegionError:
                        raise ValueError(
                            "No AWS region is set, and knowledge bases are regional. Pass one: "
                            "BedrockKBView(BedrockKBAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                        ) from None
        return self._clients["bedrock-agent"]
