"""
BedrockKBAnalyzer's searches and answers, and the models it answers with.
"""

from __future__ import annotations

import difflib
import time
from typing import Any, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.estimates import estimate_tokens
from ..._bedrock.filters import build_filter, with_data_sources
from ..._bedrock.foundation_models import parse_models
from ..._bedrock.helpers import _model_id
from ..._bedrock.models import ModelInfo, Passage
from ..._bedrock.parsing import parse_retrieve
from ..._bedrock.prices import (
    DEFAULT_MODEL, DEFAULT_RERANK_MODEL, _MODEL_ALIASES, _RERANK_ALIASES, _family_match, short_model,
)
from ..._kit.errors import _error_code, _error_name
from ..._kit.fmt import _as_int

from ..models import Answer, Retrieval
from ..analysis.prompting import _as_passages, _question_text, build_prompt
from ..analysis.parsing import parse_converse, parse_rag


class _SearchReads:
    """
    BedrockKBAnalyzer's searches and answers: Retrieve, RetrieveAndGenerate and Converse over the passages, the
    models you can answer with, and which ID or inference profile to call each with.
    """

    # ---------------------------------------------------------------- retrieval

    def _runtime_client(self) -> Any:
        """
        bedrock-agent-runtime: Retrieve and RetrieveAndGenerate.
        """
        return self._cached_client(
            "bedrock-agent-runtime",
            lambda: self.session.client(
                "bedrock-agent-runtime", region_name=self.region, config=self._config
            ),
        )

    def _rerank_arn(self, model: str | bool) -> str:
        """
        True, 'cohere', 'amazon', a reranking model ID or its ARN -> the ARN Bedrock wants.
        """
        model_id = (
            DEFAULT_RERANK_MODEL
            if model is True
            else _RERANK_ALIASES.get(str(model).lower(), str(model))
        )
        return (
            model_id
            if model_id.startswith("arn:")
            else f"arn:aws:bedrock:{self.region}::foundation-model/{model_id}"
        )

    def _search_config(
        self,
        n: int,
        where: Any,
        search_type: str | None,
        rerank_model: str | bool | None = None,
        sources: Iterable[str] = (),
    ) -> dict[str, Any]:
        """
        The vectorSearchConfiguration shared by Retrieve and RetrieveAndGenerate. sources: the IDs of the only
        data sources to search.
        """
        n = _as_int(n, "n")
        if not 1 <= n <= 100:
            raise ValueError(
                f"n can be 1 to 100 (a search returns at most 100 passages); got {n}"
            )
        config: dict[str, Any] = {"numberOfResults": n}
        condition = with_data_sources(build_filter(where), sources)
        if condition:
            config["filter"] = condition
        if search_type:
            kind = str(search_type).upper()
            if kind not in ("SEMANTIC", "HYBRID"):
                raise ValueError(
                    "search_type is 'SEMANTIC' (matches meaning) or 'HYBRID' (meaning and keywords), or "
                    "None to let Bedrock choose"
                )
            config["overrideSearchType"] = kind
        if rerank_model:
            config["numberOfResults"] = min(
                100, max(4 * n, 20)
            )  # the reranker picks the best n of these
            config["rerankingConfiguration"] = {
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": self._rerank_arn(rerank_model)},
                    "numberOfRerankedResults": n,
                },
            }
        return config

    def retrieve(
        self,
        kb: str,
        question: str,
        n: int = 5,
        *,
        where: Any = None,
        search_type: str | None = None,
        rerank_model: str | bool | None = None,
        data_source: Any = None,
    ) -> Retrieval:
        """
        The n passages (up to 100) that best match `question`, best first. where= filters on the documents'
        metadata (see build_filter); search_type='HYBRID' adds keyword matching, where the vector store supports it;
        rerank_model re-orders a wider set of results with a reranking model (True = Cohere Rerank 3.5);
        data_source= searches only that data source (a name or ID, or a list of them; see resolve_sources).
        """
        kb_id = self.resolve(kb)
        question = _question_text(question)
        sources = self.resolve_sources(kb_id, data_source)
        config = self._search_config(n, where, search_type, rerank_model, sources)
        started = time.monotonic()
        resp = self._runtime_client().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": question},
            retrievalConfiguration={"vectorSearchConfiguration": config},
        )
        n = _as_int(n, "n")
        reranker = None
        if rerank_model:
            reranker = _model_id(
                config["rerankingConfiguration"]["bedrockRerankingConfiguration"][
                    "modelConfiguration"
                ]["modelArn"]
            )
        return Retrieval(
            kb_id=kb_id,
            question=question,
            passages=parse_retrieve(resp)[:n],
            kb_name=self.kb_name(kb_id),
            n=n,
            search_type=config.get("overrideSearchType"),
            where=where,
            reranked=reranker,
            seconds=time.monotonic() - started,
            guardrail_action=resp.get("guardrailAction"),
            data_sources=sources,
        )

    # --------------------------------------------------------------- generation

    def _llm_client(self) -> Any:
        """
        bedrock-runtime: Converse.
        """
        return self._cached_client(
            "bedrock-runtime",
            lambda: self.session.client(
                "bedrock-runtime", region_name=self.region, config=self._config
            ),
        )

    def _bedrock_client(self) -> Any:
        """
        bedrock: the model and inference profile lists.
        """
        return self._cached_client(
            "bedrock",
            lambda: self.session.client(
                "bedrock", region_name=self.region, config=self._config
            ),
        )

    def models(
        self, match: str | None = None, *, refresh: bool = False
    ) -> list[ModelInfo]:
        """
        Text models ask() can use in this region, and how to call each: on demand, or through an inference profile.
        Cached. match= keeps models whose ID, name or provider contains it.
        """
        if self._models is None or refresh:
            bedrock = self._bedrock_client()
            summaries = bedrock.list_foundation_models(byOutputModality="TEXT").get(
                "modelSummaries", []
            )
            profiles: list[dict[str, Any]] | None
            try:
                profiles = [
                    profile
                    for page in bedrock.get_paginator(
                        "list_inference_profiles"
                    ).paginate()
                    for profile in page.get("inferenceProfileSummaries", [])
                ]
                self.model_errors.pop("profiles", None)
            except (ClientError, BotoCoreError) as exc:
                profiles = None  # models that need a profile say which one when called
                self.model_errors["profiles"] = _error_name(exc)
            self._profiles = profiles or []
            self._models = parse_models(
                summaries, profiles, self.region, self.model_prices
            )
        if not match:
            return list(self._models)
        wanted = str(match).lower()
        return [
            m
            for m in self._models
            if wanted in f"{m.id} {m.invoke_id} {m.name} {m.provider}".lower()
        ]

    def resolve_model(self, name: str | None = None) -> tuple[str, str]:
        """
        (ID to call, ARN) for a model: a model ID or ARN, an inference profile ID, or a short name ('opus',
        'sonnet', 'haiku', 'claude-opus-5', 'nova-pro'). None means default_model, else DEFAULT_MODEL (Claude Haiku 4.5).
        A model that can't be called on demand resolves to this region's inference profile. If the model list can't
        be read, the name is used as given.
        """
        wanted = str(name or self.default_model or DEFAULT_MODEL).strip()
        if wanted.startswith("arn:"):
            return wanted, wanted
        family = _MODEL_ALIASES.get(wanted.lower(), wanted)
        try:
            models = self.models()
        except (ClientError, BotoCoreError):
            guess = f"anthropic.{family}" if family.startswith("claude-") else family
            return guess, guess
        for m in models:
            if wanted in (m.id, m.invoke_id):
                return m.invoke_id, m.arn
        for profile in self._profiles:
            if wanted in (
                profile.get("inferenceProfileId"),
                profile.get("inferenceProfileArn"),
            ):
                return profile["inferenceProfileId"], profile["inferenceProfileArn"]
        matches = [
            m
            for m in models
            if _family_match(family, m.id) or _family_match(family, m.invoke_id)
        ]
        if not matches:
            close = difflib.get_close_matches(
                family.lower(), [short_model(m.id) for m in models], n=3, cutoff=0.5
            )
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            raise ValueError(
                f"No model matching {wanted!r} in {self.region}.{hint} models() lists the ones you can use "
                "here, with the ID to pass."
            )
        best = min(
            matches,
            key=lambda m: (
                m.status != "ACTIVE",
                m.via == "provisioned only",
                len(m.id),
                m.id,
            ),
        )
        return best.invoke_id, best.arn

    def retrieve_and_generate(
        self,
        kb: str,
        question: str,
        *,
        n: int = 5,
        where: Any = None,
        search_type: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        session_id: str | None = None,
        data_source: Any = None,
    ) -> Answer:
        """
        An answer from Bedrock's managed RAG (RetrieveAndGenerate): it retrieves n passages and has `model` answer
        from them with citations. Only the settings you pass are sent (newer Claude models reject temperature). A
        custom prompt must contain $search_results$. Tokens are estimated from characters: this API doesn't report
        them. session_id continues an earlier conversation; data_source= searches only that data source.
        """
        kb_id = self.resolve(kb)
        question = _question_text(question)
        if prompt is not None and "$search_results$" not in prompt:
            raise ValueError(
                "A prompt for engine='kb' must contain $search_results$, where Bedrock puts the passages "
                "($query$ and $output_format_instructions$ are optional). For a template with {sources} "
                "and {question}, use engine='converse'."
            )
        sources = self.resolve_sources(kb_id, data_source)
        invoke_id, arn = self.resolve_model(model)
        config: dict[str, Any] = {
            "knowledgeBaseId": kb_id,
            "modelArn": arn,
            "retrievalConfiguration": {
                "vectorSearchConfiguration": self._search_config(
                    n, where, search_type, sources=sources
                )
            },
        }
        generation: dict[str, Any] = {}
        if prompt is not None:
            generation["promptTemplate"] = {"textPromptTemplate": prompt}
        inference: dict[str, Any] = {}
        if temperature is not None:
            inference["temperature"] = float(temperature)
        if max_tokens is not None:
            inference["maxTokens"] = _as_int(max_tokens, "max_tokens")
        if inference:
            generation["inferenceConfig"] = {"textInferenceConfig": inference}
        if generation:
            config["generationConfiguration"] = generation
        params: dict[str, Any] = {
            "input": {"text": question},
            "retrieveAndGenerateConfiguration": {
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": config,
            },
        }
        if session_id:
            params["sessionId"] = session_id
        started = time.monotonic()
        resp = self._runtime_client().retrieve_and_generate(**params)
        answer = parse_rag(resp)
        answer.question, answer.model, answer.kb_id, answer.kb_name = (
            question,
            invoke_id,
            kb_id,
            self.kb_name(kb_id),
        )
        answer.seconds, answer.max_tokens, answer.tokens_estimated = (
            time.monotonic() - started,
            max_tokens,
            True,
        )
        answer.data_sources = sources
        answer.input_tokens = (
            estimate_tokens(question)
            + estimate_tokens(prompt)
            + sum(estimate_tokens(p.text) for p in answer.sources)
        )
        answer.output_tokens = estimate_tokens(answer.text)
        return answer

    def generate(
        self,
        question: str,
        passages: Iterable[Passage | str],
        *,
        model: str | None = None,
        prompt: str | None = None,
        history: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = 16_000,
    ) -> Answer:
        """
        An answer from `model` (Bedrock Converse) that cites `passages` as numbered sources, with exact token
        counts. passages can be Passage objects (e.g. a Retrieval's) or plain strings. prompt= is a template with
        {sources} and {question} (see DEFAULT_PROMPT); history= holds earlier Converse messages, for follow-ups.
        """
        question = _question_text(question)
        sources = _as_passages(passages)
        system, user = build_prompt(question, sources, prompt)
        invoke_id, _ = self.resolve_model(model)
        inference: dict[str, Any] = {}
        if max_tokens is not None:
            inference["maxTokens"] = _as_int(max_tokens, "max_tokens")
        if temperature is not None:
            inference["temperature"] = float(temperature)
        params: dict[str, Any] = {
            "modelId": invoke_id,
            "system": [{"text": system}],
            "messages": [
                *(history or []),
                {"role": "user", "content": [{"text": user}]},
            ],
        }
        if inference:
            params["inferenceConfig"] = inference
        started = time.monotonic()
        try:
            resp = self._llm_client().converse(**params)  # read-only: generates text, changes no AWS resource
        except ClientError as exc:
            reason = str(exc.response.get("Error", {}).get("Message", "")).lower()
            if _error_code(exc) != "ValidationException" or "system" not in reason:
                raise
            # Some models take no system prompt: send its instructions at the top of the question instead.
            params.pop("system")
            params["messages"][-1] = {
                "role": "user",
                "content": [{"text": f"{system}\n\n{user}"}],
            }
            resp = self._llm_client().converse(**params)  # read-only: generates text, changes no AWS resource
        answer = parse_converse(resp, sources)
        answer.question, answer.model, answer.prompt = question, invoke_id, user
        answer.seconds, answer.max_tokens = (
            time.monotonic() - started,
            inference.get("maxTokens"),
        )
        return answer

    def ask(
        self,
        kb: str,
        question: str,
        *,
        engine: str = "kb",
        n: int = 5,
        where: Any = None,
        search_type: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        session_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        data_source: Any = None,
    ) -> Answer:
        """
        An answer with citations. engine='kb' uses Bedrock's RetrieveAndGenerate (session_id= continues a
        conversation); engine='converse' retrieves, then calls the model itself: exact tokens and cost, any model, and
        your own prompt= template (history= continues a conversation). data_source= searches only that data source
        (a name or ID, or a list of them).
        """
        engine = str(engine).lower()
        if engine == "kb":
            return self.retrieve_and_generate(
                kb,
                question,
                n=n,
                where=where,
                search_type=search_type,
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=session_id,
                data_source=data_source,
            )
        if engine != "converse":
            raise ValueError(
                "engine is 'kb' (Bedrock's RetrieveAndGenerate) or 'converse' (retrieve, then your model "
                "and prompt)"
            )
        r = self.retrieve(
            kb, question, n, where=where, search_type=search_type, data_source=data_source
        )
        answer = self.generate(
            question,
            r.passages,
            model=model,
            prompt=prompt,
            history=history,
            temperature=temperature,
            max_tokens=16_000 if max_tokens is None else max_tokens,
        )
        answer.kb_id, answer.kb_name, answer.seconds = (
            r.kb_id,
            r.kb_name,
            answer.seconds + r.seconds,
        )
        answer.data_sources = r.data_sources
        return answer
