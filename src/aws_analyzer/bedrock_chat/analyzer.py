"""
BedrockChatAnalyzer: talks to Bedrock and S3, and returns the dataclasses in models. Never prints,
and never changes a knowledge base.
"""

from __future__ import annotations

import botocore.session
import difflib
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Iterable

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._bedrock.estimates import estimate_tokens
from .._bedrock.files import (
    SavedFileList, _link_seconds, _list_target, file_list_name, parse_list_link, presigned_url, write_file_list,
)
from .._bedrock.foundation_models import parse_models
from .._bedrock.helpers import _KB_ID_RE, _match_kb, _match_sources, _model_id, _session_expired, parse_kb_ref
from .._bedrock.models import KBDocument, ModelInfo
from .._bedrock.parsing import parse_document, parse_retrieve
from .._bedrock.prices import BEDROCK_PRICES, DEFAULT_MODEL, MODEL_PRICES, _MODEL_ALIASES, _family_match, short_model
from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _error_name, _why
from .._kit.fmt import _as_int, _utcnow

from .helpers import BATCH_LIMIT, BATCH_WORKERS, DEFAULT_PROMPT, FILE_LIMIT, _short
from .models import Answer, DataSource, FileList, KnowledgeBase, Schema
from .analysis.parsing import collect_stream, parse_rag
from .analysis.schema import request_schema
from .analysis.choices import match_files
from .analysis.request import _is_retrieve, build_request, build_retrieve_request, normalize_settings, retrieve_settings
from .analysis.findings import answer_cost
from .analysis.batches import Batch, BatchItem, _question_cap, _question_text, question_list
from .analysis.sweeps import _setup_key, _sweep_name, apply_setup, run_setup, setup_label, varied_setups
from .analysis.ranking import Sweep


class BedrockChatAnalyzer(_BaseAnalyzer):
    """
    Pure logic for chatting with a Bedrock knowledge base: builds RetrieveAndGenerate requests from settings,
    sends them and returns an Answer, or only the search behind one (Retrieve, retrieve()). Nothing is printed, and
    nothing in AWS is changed.

    Knowledge bases are named by ID, name (any case) or ARN; models by ID, inference profile, ARN or a short name
    ('opus', 'sonnet', 'haiku', 'nova'...). Settings are {name: value} with the names fields() lists. `prices` and
    `model_prices` override BEDROCK_PRICES and MODEL_PRICES for cost estimates; `default_model` is the model used
    when none is given (DEFAULT_MODEL otherwise). `clients` pre-fills boto3 clients by service name
    ('bedrock-agent', 'bedrock-agent-runtime', 'bedrock'), e.g. to use stubbed ones.
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
        self._lock = threading.RLock()  # clients are made once, even when the Test tab asks from threads
        if client is not None:
            self._clients["bedrock-agent"] = client
        self.model_prices = {**MODEL_PRICES, **(model_prices or {})}
        self.default_model = default_model  # what model=None means (None: DEFAULT_MODEL)
        self._models: list[ModelInfo] | None = None
        self._profiles: list[dict[str, Any]] = []
        self.model_errors: dict[str, str] = {}  # 'profiles' -> error code, when inference profiles can't be listed
        self._names: dict[str, str] | None = None  # knowledge base ID -> name
        self._kbs: list[KnowledgeBase] | None = None
        self._sources: dict[str, list[DataSource]] = {}  # knowledge base ID -> its data sources, as last listed
        self._files: dict[str, FileList] = {}  # knowledge base ID -> its files, as last listed
        self._schema: Schema | None = None
        self.stream_problem: str | None = None  # why answers can't stream here, once a streamed call has failed

    @property
    def client(self) -> Any:
        """
        The bedrock-agent client (the knowledge base list), made on first use so a missing region shows up as a
        readable error.
        """
        if "bedrock-agent" not in self._clients:
            try:
                self._clients["bedrock-agent"] = self.session.client("bedrock-agent", config=self._config)
            except NoRegionError:
                raise ValueError(
                    "No AWS region is set, and knowledge bases are regional. Pass one: chat(region='us-east-1'), "
                    "or set AWS_DEFAULT_REGION."
                ) from None
        return self._clients["bedrock-agent"]

    def _kb_summaries(self) -> list[dict[str, Any]]:
        summaries = self._paginate("list_knowledge_bases", "knowledgeBaseSummaries")
        self._names = {s["knowledgeBaseId"]: s.get("name", "") for s in summaries}
        return summaries

    def knowledge_base_names(self, *, refresh: bool = False) -> dict[str, str]:
        """
        {ID: name} of every knowledge base in the region (one ListKnowledgeBases, cached).
        """
        if refresh or self._names is None:
            self._kb_summaries()
        return dict(self._names or {})

    def kb_name(self, kb_id: str) -> str:
        """
        The name of a knowledge base already seen (its ID otherwise). Makes no AWS call.
        """
        return (self._names or {}).get(kb_id) or kb_id

    def resolve(self, kb: str) -> str:
        """
        The ID of a knowledge base, given its ID, name (any case) or ARN. An unknown name raises a ValueError
        that lists the knowledge bases in the region.
        """
        kind, value = parse_kb_ref(kb)
        if kind == "arn":
            return value
        listed_now = self._names is None
        try:
            names = self.knowledge_base_names()
        except (ClientError, BotoCoreError):
            if kind == "id":
                return value  # can't list knowledge bases, but may still read this one
            raise
        found = _match_kb(names, kind, value)
        if (
            found is None and not listed_now
        ):  # maybe created since the names were cached
            names = self.knowledge_base_names(refresh=True)
            found = _match_kb(names, kind, value)
        if found is not None:
            return found
        close = difflib.get_close_matches(
            value.lower(), {n.lower(): n for n in names.values()}, n=3, cutoff=0.6
        )
        close_names = [n for n in names.values() if n.lower() in close]
        text = f"No knowledge base {value!r} in {self.region}."
        if close_names:
            text += f" Did you mean {' or '.join(map(repr, close_names))}?"
        if names:
            listed = sorted(names.values(), key=str.lower)
            text += f" The ones here: {', '.join(listed[:15])}{', …' if len(listed) > 15 else ''}."
        else:
            text += " There are none in this region, and knowledge bases are regional."
        raise ValueError(text + " kbs() lists them.")

    def knowledge_bases(self, *, refresh: bool = False) -> list[KnowledgeBase]:
        """
        Every knowledge base in the region: ID, name, status, description and when it last changed. One
        ListKnowledgeBases, cached.
        """
        if refresh or self._kbs is None:
            self._kbs = [
                KnowledgeBase(s["knowledgeBaseId"], s.get("name", ""), s.get("status", ""), s.get("description", ""),
                              s.get("updatedAt"))
                for s in self._kb_summaries()
            ]
        return list(self._kbs)

    def data_sources(self, kb: str, *, refresh: bool = False) -> list[DataSource]:
        """
        The data sources of a knowledge base (where its documents come from): ID, name, status and description.
        One ListDataSources, cached. Questions can be asked of one of them alone (data_source=).
        """
        kb_id = self.resolve(kb)
        if refresh or kb_id not in self._sources:
            self._sources[kb_id] = [
                DataSource(s["dataSourceId"], s.get("name", ""), s.get("status", ""), s.get("description", ""),
                           s.get("updatedAt"))
                for s in self._paginate("list_data_sources", "dataSourceSummaries", knowledgeBaseId=kb_id)
            ]
        return list(self._sources[kb_id])

    def data_source_names(self, kb: str, *, refresh: bool = False) -> dict[str, str]:
        """
        {ID: name} of a knowledge base's data sources (one ListDataSources, cached).
        """
        return {ds.id: ds.name for ds in self.data_sources(kb, refresh=refresh)}

    def file_status(self, kb_id: str, uri: str) -> str:
        """
        A file's status (INDEXED, FAILED ...) when its knowledge base's files have been listed ('' otherwise).
        Makes no AWS call.
        """
        listing = self._files.get(kb_id)
        return next((d.status for d in listing.documents if d.uri == uri), "") if listing else ""

    def data_source_name(self, kb_id: str, ds_id: str) -> str:
        """
        The name of a data source already listed (its ID otherwise). Makes no AWS call.
        """
        return next((ds.name for ds in self._sources.get(kb_id, []) if ds.id == ds_id and ds.name), ds_id)

    def resolve_sources(self, kb: str, data_source: Any) -> dict[str, str]:
        """
        {ID: name} of the data sources questions should search, from data_source=: a data source's name (any
        case) or ID, or a list of them. None, [] or 'all' -> {} (every data source). A dict is taken as already
        resolved. An unknown one raises a ValueError that lists the knowledge base's data sources.
        """
        if isinstance(data_source, dict):
            return {str(k): str(v or "") for k, v in data_source.items()}
        if data_source is None or (isinstance(data_source, str) and data_source.strip().lower() in ("all", "*")):
            return {}
        items = list(data_source) if isinstance(data_source, (list, tuple, set, frozenset)) else [data_source]
        wanted = [str(item.id if isinstance(item, DataSource) else item).strip() for item in items]
        if not wanted:
            return {}
        if any(not w for w in wanted):
            raise ValueError("data_source= takes a data source's name or ID, or a list of them")
        kb_id = self.resolve(kb)
        cached = kb_id in self._sources
        try:
            names = {ds.id: ds.name for ds in self.data_sources(kb_id)}
        except (ClientError, BotoCoreError):
            if all(_KB_ID_RE.match(w) for w in wanted):
                return {w: "" for w in wanted}  # can't list them, but the IDs may still be right
            raise
        found, missing = _match_sources(names, wanted)
        if missing and cached:  # maybe added since they were listed
            names = {ds.id: ds.name for ds in self.data_sources(kb_id, refresh=True)}
            found, missing = _match_sources(names, wanted)
        if missing:
            close = difflib.get_close_matches(missing[0].lower(), [n.lower() for n in names.values()], n=2,
                                              cutoff=0.6)
            close_names = [n for n in names.values() if n.lower() in close]
            text = f"{self.kb_name(kb_id)} has no data source {missing[0]!r}."
            if close_names:
                text += f" Did you mean {' or '.join(map(repr, close_names))}?"
            listed = ", ".join(f"{name} ({ds_id})" for ds_id, name in sorted(names.items(), key=lambda i: i[1].lower()))
            raise ValueError(text + (f" Its data sources: {listed}." if names else " It has no data sources."))
        return found

    def files(self, kb: str, *, limit: int = FILE_LIMIT, refresh: bool = False) -> FileList:
        """
        The files a knowledge base has indexed (or tried to), from each data source that keeps a list of them (S3
        and custom ones): s3:// path, status, data source and when it changed. Up to `limit` of them, cached. A data
        source whose files can't be listed is recorded in `errors`.
        """
        kb_id = self.resolve(kb)
        if refresh or kb_id not in self._files:
            listing = FileList(kb_id)
            for ds in self.data_sources(kb_id):
                try:
                    for page in self.client.get_paginator("list_knowledge_base_documents").paginate(
                            knowledgeBaseId=kb_id, dataSourceId=ds.id):
                        for desc in page.get("documentDetails", []):
                            if len(listing.documents) >= limit:
                                listing.truncated = True
                                break
                            listing.documents.append(parse_document(desc))
                        if listing.truncated:
                            break
                except (ClientError, BotoCoreError) as exc:
                    listing.errors[ds.id] = _error_name(exc)
                if listing.truncated:
                    break
            self._files[kb_id] = listing
        return self._files[kb_id]

    def _s3_client(self) -> Any:
        """
        s3: signing links to a knowledge base's files (SigV4, which every region accepts; no AWS call).
        """
        return self._cached_client(
            "s3",
            lambda: self.session.client(
                "s3", region_name=self.region, config=self._config.merge(Config(signature_version="s3v4"))
            ),
        )

    def file_url(self, uri: str, *, page: int | None = None, expires: int = 3600) -> str | None:
        """
        A link that opens a file in a browser tab, or None when there's no file to open (a custom data source's
        document). An s3:// file gets a presigned GetObject link, signed here with your credentials (no AWS call): it
        works for `expires` seconds, only while those credentials are valid, and only if they may read the file
        (s3:GetObject).
        """
        return presigned_url(self._s3_client, uri, page=page, expires=expires)

    def save_file_list(
        self,
        files: FileList | Iterable[KBDocument | str],
        path: str | None = None,
        *,
        link: str = "s3",
        expires: int = 3600,
    ) -> SavedFileList:
        """
        Writes a text file with a line per S3 file, in the order given: its s3:// path (link='s3'), its path in the
        bucket ('key') or a link that opens it in a browser ('https': presigned here with your credentials, no AWS
        call, working for `expires` seconds). files: a files() list, or some of its documents or s3:// paths. path:
        the file to write (a folder puts it there), else <knowledge base>-files.txt beside the notebook; a file of
        that name is replaced. Nothing in AWS changes.
        """
        link = parse_list_link(link)
        default = file_list_name(self.kb_name(files.kb_id)) if isinstance(files, FileList) else "kb-files.txt"
        items = files.documents if isinstance(files, FileList) else files
        sign = None
        if link == "https":
            expires = _link_seconds(expires)

            def sign(uri: str) -> str | None:
                return self.file_url(uri, expires=expires)

        return write_file_list(items, _list_target(path, default), link=link, sign=sign, expires=expires)

    def resolve_files(self, kb: str, files: Any) -> list[str]:
        """
        The s3:// paths of the files questions should search, from files=: s3:// paths, paths in the bucket
        ('policies/refund-policy.pdf') or file names ('refund-policy.pdf', any case), or a list of them. None, [] or
        'all' -> [] (every file). Names are looked up in files(); a name that matches no file, or several, raises a
        ValueError that says so.
        """
        if files is None or (isinstance(files, str) and files.strip().lower() in ("all", "*")):
            return []
        items = list(files) if isinstance(files, (list, tuple, set, frozenset)) else [files]
        wanted = [str(item.uri if isinstance(item, KBDocument) else item).strip() for item in items]
        if any(not w for w in wanted):
            raise ValueError("files= takes file names or s3:// paths, or a list of them")
        if all("://" in w for w in wanted):
            return list(dict.fromkeys(wanted))
        kb_id = self.resolve(kb)
        cached = kb_id in self._files
        listing = self.files(kb_id)
        uris, problems = match_files(listing.documents, wanted)
        if problems and cached:  # maybe added since they were listed
            listing = self.files(kb_id, refresh=True)
            uris, problems = match_files(listing.documents, wanted)
        if problems:
            text = ". ".join(p.rstrip(".") for p in problems) + "."
            if listing.truncated:
                text += f" Only the first {len(listing.documents):,} files were listed: pass its full s3:// path."
            elif listing.errors and not listing.documents:
                code = next(iter(listing.errors.values()))
                text += (f" The files couldn't be listed ({_why(code, 'bedrock:ListKnowledgeBaseDocuments')}): pass "
                         "full s3:// paths instead.")
            raise ValueError(text)
        return uris

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

    def schema(self) -> Schema:
        """
        Every setting this boto3 can send with RetrieveAndGenerate (see request_schema()). Cached; no AWS call.
        """
        if self._schema is None:
            runtime = self._clients.get("bedrock-agent-runtime")
            model = getattr(getattr(runtime, "meta", None), "service_model", None)
            self._schema = request_schema(model)
        return self._schema

    def request(
        self,
        kb: str,
        question: str,
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        session_id: str | None = None,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
    ) -> dict[str, Any]:
        """
        The RetrieveAndGenerate request ask() would send, without sending it: the knowledge base and model
        resolved, each setting at its place in the JSON, and data_source= (a name or ID, or a list) and files= (names
        or s3:// paths) in the filter. retrieve_only=True gives retrieve()'s Retrieve request instead: the same
        search, without the model.
        """
        schema = self.schema()
        values = normalize_settings(settings, schema)
        kb_id = self.resolve(kb)
        sources = self.resolve_sources(kb_id, data_source)
        uris = self.resolve_files(kb_id, files)
        if retrieve_only:
            return build_retrieve_request(_question_text(question), kb_id, values, schema, region=self.region,
                                          data_sources=sources, files=uris)
        _, arn = self.resolve_model(model)
        return build_request(_question_text(question), kb_id, arn, values, schema, session_id=session_id,
                             region=self.region, data_sources=sources, files=uris)

    def send(
        self,
        params: dict[str, Any],
        settings: dict[str, Any] | None = None,
        *,
        stream: bool = False,
        on_text: Callable[[str], None] | None = None,
    ) -> Answer:
        """
        Sends a RetrieveAndGenerate request as it is and returns the Answer. stream=True uses
        RetrieveAndGenerateStream and calls on_text with the answer so far as it's written; where streaming isn't
        allowed or this boto3 can't, the answer comes all at once and says why. settings (the ones the request was
        built from) are kept on the Answer, for its findings. A Retrieve request is sent with Retrieve, and comes back
        as a retrieve-only Answer: every passage found, no text.
        """
        if _is_retrieve(params):
            return self._send_retrieve(params, settings)
        runtime = self._runtime_client()
        started = time.monotonic()
        notes: list[str] = []
        first: list[float] = []
        response: dict[str, Any] | None = None
        denied = ""
        if stream and self.stream_problem is None and not hasattr(runtime, "retrieve_and_generate_stream"):
            self.stream_problem = (f"This boto3 (botocore {botocore.__version__}) can't stream answers, so they "
                                   "arrive all at once: pip install -U boto3 to see them as they're written.")
            notes.append(self.stream_problem)
        if stream and self.stream_problem is None:

            def seen(text: str) -> None:
                if not first:
                    first.append(time.monotonic() - started)
                if on_text:
                    on_text(text)

            try:
                raw = runtime.retrieve_and_generate_stream(**params)
                response = collect_stream(raw.get("stream") or [], seen)
                response["sessionId"] = raw.get("sessionId")
            except ClientError as exc:
                if _error_code(exc) != "AccessDeniedException" or first:
                    raise
                denied = _error_code(exc)  # streaming may be denied where RetrieveAndGenerate isn't: try without
        streamed = response is not None
        if response is None:
            response = runtime.retrieve_and_generate(**params)
            if denied:  # it worked without streaming, so it's streaming that's refused: stop trying, and say why
                self.stream_problem = (f"Streaming was refused here ({denied}) while asking without it worked, so "
                                       "answers arrive all at once.")
                notes.append(self.stream_problem)
        answer = parse_rag(response)
        kb_config = params["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        answer.question = params["input"]["text"]
        answer.kb_id = kb_config["knowledgeBaseId"]
        answer.kb_name = (self._names or {}).get(answer.kb_id, "")
        answer.model = _model_id(kb_config["modelArn"])
        answer.settings = dict(settings or {})
        answer.request, answer.response = params, response
        answer.streamed, answer.first_words = streamed, first[0] if first else None
        answer.seconds = time.monotonic() - started
        answer.notes = notes
        # Estimates: RetrieveAndGenerate reports no tokens, and returns only the passages the answer cites.
        given = answer.settings.get("rerank_n") if answer.settings.get("reranker") else None
        given = given or answer.settings.get("n") or 5
        per_passage = (sum(estimate_tokens(p.text) for p in answer.sources) // len(answer.sources)
                       if answer.sources else 300)
        prompt = answer.settings.get("prompt") or DEFAULT_PROMPT
        answer.input_tokens = (estimate_tokens(answer.question) + estimate_tokens(prompt)
                               + max(given, len(answer.sources)) * per_passage)
        answer.output_tokens = estimate_tokens(answer.text)
        return answer

    def _send_retrieve(self, params: dict[str, Any], settings: dict[str, Any] | None) -> Answer:
        started = time.monotonic()
        response = self._runtime_client().retrieve(**params)
        kb_id = params.get("knowledgeBaseId", "")
        return Answer(
            question=(params.get("retrievalQuery") or {}).get("text") or "",
            text="",
            sources=parse_retrieve(response),
            guardrail_action=response.get("guardrailAction"),
            kb_id=kb_id,
            kb_name=(self._names or {}).get(kb_id, ""),
            settings=dict(settings or {}),
            request=params,
            response=response,
            seconds=time.monotonic() - started,
            retrieve_only=True,
        )

    def retrieve(
        self,
        kb: str,
        question: str,
        settings: dict[str, Any] | None = None,
        *,
        data_source: Any = None,
        files: Any = None,
    ) -> Answer:
        """
        Every passage a question retrieves, best first, with its score, and no answer (Retrieve): the search an
        answer with these settings starts from, without the model. Only the Retrieval settings are sent (passages,
        search type, filter, reranker), and kept on the Answer; data_source= and files= narrow it as in ask().
        """
        schema = self.schema()
        values = normalize_settings(settings, schema)
        sources = self.resolve_sources(kb, data_source)
        uris = self.resolve_files(kb, files)
        params = self.request(kb, question, values, data_source=sources, files=uris, retrieve_only=True)
        a = self.send(params, retrieve_settings(values, schema))
        a.data_sources, a.files = sources, uris
        return a

    def ask(
        self,
        kb: str,
        question: str,
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        session_id: str | None = None,
        stream: bool = False,
        on_text: Callable[[str], None] | None = None,
        data_source: Any = None,
        files: Any = None,
    ) -> Answer:
        """
        An answer from the knowledge base (RetrieveAndGenerate), with its citations, sources, request and response.
        session_id continues an earlier conversation; if Bedrock has ended it, a new one starts and the Answer says
        so. stream=True calls on_text with the answer so far as it's written. data_source= searches only that data
        source (a name or ID, or a list of them), and files= only those files (names or s3:// paths).
        """
        values = normalize_settings(settings, self.schema())
        sources = self.resolve_sources(kb, data_source)
        uris = self.resolve_files(kb, files)
        params = self.request(kb, question, values, model=model, session_id=session_id, data_source=sources,
                              files=uris)
        try:
            answer = self.send(params, values, stream=stream, on_text=on_text)
            answer.data_sources, answer.files = sources, uris
            return answer
        except ClientError as exc:
            if not session_id or not _session_expired(exc):
                raise
        params.pop("sessionId", None)
        answer = self.send(params, values, stream=stream, on_text=on_text)
        answer.data_sources, answer.files = sources, uris
        answer.notes.append("The earlier conversation had expired (Bedrock ends them after a while), so this question "
                            "started a new one: it was answered without the earlier questions.")
        return answer

    def ask_all(
        self,
        kb: str,
        questions: Any,
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
        workers: int = BATCH_WORKERS,
        limit: int | None = BATCH_LIMIT,
        progress: Callable[..., None] | None = None,
        stop: threading.Event | None = None,
        label: str = "",
    ) -> Batch:
        """
        Asks a list of test questions with one setup (knowledge base, model, settings, data sources and files),
        each on its own, never as a follow-up, `workers` at a time, and returns a Batch: each question's Answer (or why
        Bedrock refused it), where its expected source came up, and its estimated cost. questions: text with one per
        line ('question | expected file'), a list of questions or of (question, expected) pairs, a DataFrame with
        'question' and 'expected' columns, or an earlier Batch. retrieve_only=True only searches (Retrieve). Up to
        `limit` questions are asked (None for all). A question Bedrock refuses is recorded, not raised, and the rest
        are still asked; setting `stop` (a threading.Event) asks no more. label names the run.
        """
        batch = self._prepare_batch(kb, questions, settings, model=model, data_source=data_source, files=files,
                                    retrieve_only=retrieve_only, limit=limit, label=label)
        return self._run_batch(batch, workers=workers, progress=progress, stop=stop)

    def sweep(
        self,
        kb: str,
        questions: Any,
        setups: Iterable[dict[str, Any]],
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
        workers: int = BATCH_WORKERS,
        limit: int | None = BATCH_LIMIT,
        progress: Callable[..., None] | None = None,
        stop: threading.Event | None = None,
        label: str = "",
    ) -> Sweep:
        """
        Asks the same test questions with several setups and returns a Sweep: a Batch per setup, each question
        asked on its own with each one, `workers` at a time across them all (the first question with every setup,
        then the second...). setups: each setup's changes to `settings`, {name: value}, where None leaves a setting
        out and 'model', 'data_source' or 'files' pick those instead of model=, data_source= and files=;
        sweep_setups() makes them from lists of values to try. Every setup is checked before anything is sent, and
        two that come out the same are asked once. A question Bedrock refuses is recorded, not raised; setting `stop`
        asks no more.
        """
        sweep = self._prepare_sweep(kb, questions, setups, settings, model=model, data_source=data_source,
                                    files=files, retrieve_only=retrieve_only, limit=limit, label=label)
        return self._run_sweep(sweep, workers=workers, progress=progress, stop=stop)

    def _prepare_sweep(
        self,
        kb: str,
        questions: Any,
        setups: Iterable[dict[str, Any]],
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
        limit: int | None = BATCH_LIMIT,
        label: str = "",
    ) -> Sweep:
        """
        A sweep ready to send: each setup's run prepared like _prepare_batch()'s, with setups that come out the same
        dropped. Sends no question; a ValueError says which setup can't be sent.
        """
        schema = self.schema()
        base = normalize_settings(settings, schema)
        cases, kb_id = question_list(questions), self.resolve(kb)  # said once, not as one setup's problem
        _question_cap(limit)
        sweep = Sweep(retrieve_only=retrieve_only, id=uuid.uuid4().hex[:12], label=str(label or ""))
        seen: set[str] = set()
        for changes in setups:
            changes = {_sweep_name(k): v for k, v in dict(changes).items()}
            try:
                values = apply_setup(base, changes, schema)
                batch = self._prepare_batch(
                    kb_id, cases, values, model=changes["model"] if "model" in changes else model,
                    data_source=changes["data_source"] if "data_source" in changes else data_source,
                    files=changes["files"] if "files" in changes else files, retrieve_only=retrieve_only, limit=limit)
            except ValueError as exc:
                shown = ", ".join(f"{k}={_short(v, 40)}" for k, v in changes.items()) or "no changes"
                raise ValueError(f"The setup with {shown} can't be sent: {exc}") from None
            key = _setup_key(run_setup(batch))
            if key not in seen:
                seen.add(key)
                batch.sweep = sweep.id
                sweep.batches.append(batch)
        if len(sweep.batches) < 2:
            raise ValueError("Those setups come out the same, so there's nothing to compare: give values that "
                             "differ, like n=[5, 10].")
        sweep.varied = varied_setups(sweep.batches)
        for batch in sweep.batches:
            batch.label = " · ".join(filter(None, [sweep.label, setup_label(batch, sweep.varied)]))
        return sweep

    def _run_sweep(
        self,
        sweep: Sweep,
        *,
        workers: int = BATCH_WORKERS,
        progress: Callable[..., None] | None = None,
        stop: threading.Event | None = None,
        on_item: Callable[[BatchItem], None] | None = None,
    ) -> Sweep:
        """
        Sends a prepared sweep's questions through one pool (_run_batches()).
        """
        started = time.monotonic()
        try:
            self._run_batches(sweep.batches, workers=workers, progress=progress, stop=stop, on_item=on_item)
        finally:
            sweep.seconds += time.monotonic() - started
            sweep.stopped = any(b.stopped for b in sweep.batches)
        return sweep

    def _prepare_batch(
        self,
        kb: str,
        questions: Any,
        settings: dict[str, Any] | None = None,
        *,
        model: str | None = None,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
        limit: int | None = BATCH_LIMIT,
        label: str = "",
    ) -> Batch:
        """
        A test run, ready to send: the knowledge base, model, data sources and files resolved once, and each
        question's request built, every question on its own (no session). Sends no question.
        """
        schema = self.schema()
        values = normalize_settings(settings, schema)
        cases = question_list(questions)
        cap = _question_cap(limit)
        kb_id = self.resolve(kb)
        sources = self.resolve_sources(kb_id, data_source)
        uris = self.resolve_files(kb_id, files)
        arn = "" if retrieve_only else self.resolve_model(model)[1]
        batch = Batch(kb_id=kb_id, kb_name=self.kb_name(kb_id), model=_model_id(arn), data_sources=sources, files=uris,
                      settings=retrieve_settings(values, schema) if retrieve_only else values,
                      retrieve_only=retrieve_only, id=uuid.uuid4().hex[:12], label=str(label or ""),
                      started=_utcnow())

        def built(question: str) -> dict[str, Any]:
            if retrieve_only:
                return build_retrieve_request(question, kb_id, values, schema, region=self.region,
                                              data_sources=sources, files=uris)
            return build_request(question, kb_id, arn, values, schema, region=self.region, data_sources=sources,
                                 files=uris)

        batch.request = built("<your question>")
        kept = cases if cap is None else cases[:cap]
        batch.skipped = len(cases) - len(kept)
        for question, expected in kept:
            item = BatchItem(question, expected)
            try:
                item.request = built(_question_text(question))
            except ValueError as exc:  # too long: said on its line, and the rest are still asked
                item.error = str(exc)
            batch.items.append(item)
        return batch

    def _run_batch(
        self,
        batch: Batch,
        *,
        workers: int = BATCH_WORKERS,
        progress: Callable[..., None] | None = None,
        stop: threading.Event | None = None,
        on_item: Callable[[BatchItem], None] | None = None,
    ) -> Batch:
        """
        Sends a prepared test run's questions, `workers` at a time (_run_batches()).
        """
        return self._run_batches([batch], workers=workers, progress=progress, stop=stop, on_item=on_item)[0]

    def _run_batches(
        self,
        batches: list[Batch],
        *,
        workers: int = BATCH_WORKERS,
        progress: Callable[..., None] | None = None,
        stop: threading.Event | None = None,
        on_item: Callable[[BatchItem], None] | None = None,
    ) -> list[Batch]:
        """
        Sends prepared test runs' questions through one pool, `workers` at a time: the first question of every run,
        then the second, and so on, so runs stopped early have asked the same questions. Each answer (or error) and
        its cost are filled in on the calling thread, which also calls progress(done, total) and on_item(item) as each
        comes back: nothing else is touched from the worker threads. Once `stop` is set, or the run is interrupted, no
        more questions are sent and what came back is kept; each batch's `stopped` says so.
        """
        rows = max((len(b.items) for b in batches), default=0)
        todo = [(b, b.items[k]) for k in range(rows) for b in batches
                if k < len(b.items) and b.items[k].request and b.items[k].answer is None and not b.items[k].error]
        if not todo:
            return batches
        count = max(1, min(_as_int(workers, "workers"), len(todo)))
        self._runtime_client()  # made before the threads start: a boto3 session isn't thread-safe
        pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix="bedrock-chat")
        waiting = list(todo)
        running: dict[Any, tuple[Batch, BatchItem]] = {}  # the questions being asked, by their future
        spans: dict[int, list[float]] = {}  # id(batch) -> [its first question sent, its last answer back]
        done, interrupted = 0, False

        def send_more() -> None:  # one question per free thread, so nothing waits to be sent once stop is set
            while waiting and len(running) < count and not (stop is not None and stop.is_set()):
                batch, item = waiting.pop(0)
                spans.setdefault(id(batch), [time.monotonic()] * 2)
                running[pool.submit(self.send, item.request, batch.settings)] = (batch, item)

        try:
            send_more()
            while running:
                finished, _ = wait(running, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    batch, item = running.pop(future)
                    self._fill(item, future, batch)
                    spans[id(batch)][1] = time.monotonic()
                    done += 1
                    if progress is not None:
                        progress(done, len(todo))
                    if on_item is not None:
                        on_item(item)
                send_more()
        except KeyboardInterrupt:  # the notebook's stop button: keep what came back, and send nothing more
            interrupted = True
        finally:
            pool.shutdown(wait=not interrupted, cancel_futures=True)
            ended = time.monotonic()
            for batch in batches:
                span = spans.get(id(batch))
                if span is not None:
                    batch.seconds += (ended if interrupted else span[1]) - span[0]
                batch.stopped = any(i.answer is None and not i.error for i in batch.items if i.request)
        return batches

    def _fill(self, item: BatchItem, future: Any, batch: Batch) -> None:
        """
        A test question's answer and its cost, or why Bedrock refused it, from its finished future.
        """
        try:
            a = future.result()
        except ClientError as exc:
            error = exc.response.get("Error", {})
            item.error_code, item.error = error.get("Code") or "Error", error.get("Message") or str(exc)
            return
        except BotoCoreError as exc:
            item.error_code, item.error = type(exc).__name__, str(exc)
            return
        a.kb_name = a.kb_name or batch.kb_name
        a.data_sources, a.files = dict(batch.data_sources), list(batch.files)
        item.answer = a
        item.cost = answer_cost(a, self.model_prices, self.prices)
