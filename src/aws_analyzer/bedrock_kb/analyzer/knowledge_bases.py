"""
BedrockKBAnalyzer's reads about knowledge bases, data sources, syncs and documents.
"""

from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.helpers import _KB_ID_RE, _match_kb, _match_sources, parse_kb_ref
from ..._bedrock.models import KBDocument
from ..._bedrock.parsing import parse_document
from ..._kit.errors import _error_name
from ..._kit.fmt import _as_count, _as_int

from ..models import _EPOCH, DataSourceInfo, DocumentSummary, IngestionJob, KnowledgeBaseInfo
from ..analysis.parsing import _with_errors, parse_data_source, parse_ingestion_job, parse_knowledge_base
from ..analysis.deciding import summarize_documents


class _KnowledgeBaseReads:
    """
    BedrockKBAnalyzer's reads about knowledge bases and their data sources: the list of them, names to IDs,
    each one's settings, its syncs (with why they failed) and Bedrock's record of its documents.
    """

    # ---------------------------------------------------------- knowledge bases

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

    def list_knowledge_bases(
        self, *, details: bool = True, progress: Callable[[int], None] | None = None
    ) -> list[KnowledgeBaseInfo]:
        """
        Every knowledge base in the region. details=True describes each one (in parallel): settings, data
        sources and their latest syncs. One that can't be described keeps its error in `errors`.
        """
        found = [parse_knowledge_base(summary) for summary in self._kb_summaries()]
        if not details:
            return found
        done: list[KnowledgeBaseInfo] = []
        with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as pool:
            for info in pool.map(self._safe_describe, found):
                done.append(info)
                if progress:
                    progress(len(done))
        return done

    def _safe_describe(self, basic: KnowledgeBaseInfo) -> KnowledgeBaseInfo:
        try:
            return self.describe(basic.id)
        except (ClientError, BotoCoreError) as exc:
            basic.errors["describe"] = _error_name(exc)
            return basic

    def describe(self, kb: str, *, jobs: int = 5) -> KnowledgeBaseInfo:
        """
        Everything about a knowledge base: its settings, each data source's settings and last `jobs` syncs, and its
        tags. A section that can't be read (e.g. a missing permission) is recorded in `errors` instead of raising.
        """
        kb_id = self.resolve(kb)
        info = parse_knowledge_base(
            self.client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        )

        def get(errors: dict[str, str], section: str, call: Callable[[], Any]) -> Any:
            try:
                return call()
            except (ClientError, BotoCoreError) as exc:
                errors[section] = _error_name(exc)
            return None

        summaries = (
            get(
                info.errors,
                "data_sources",
                lambda: self._paginate(
                    "list_data_sources", "dataSourceSummaries", knowledgeBaseId=kb_id
                ),
            )
            or []
        )
        if "data_sources" not in info.errors:
            self._source_names[kb_id] = {
                s["dataSourceId"]: s.get("name", "") for s in summaries
            }
        for summary in summaries:
            ds = DataSourceInfo(
                id=summary["dataSourceId"],
                name=summary.get("name", ""),
                status=summary.get("status", ""),
                kb_id=kb_id,
                description=summary.get("description", ""),
                updated=summary.get("updatedAt"),
            )
            desc = get(
                ds.errors,
                "data_source",
                lambda: self.client.get_data_source(
                    knowledgeBaseId=kb_id, dataSourceId=ds.id
                )["dataSource"],
            )
            if desc:
                ds = _with_errors(parse_data_source(desc), ds.errors)
            recent = get(
                ds.errors, "ingestion", lambda: self._recent_jobs(kb_id, ds.id, jobs)
            )
            if recent is not None:
                self._set_jobs(ds, recent)
                if ds.last_sync and ds.last_sync.status == "FAILED":
                    self._add_reasons(kb_id, ds.last_sync)
            for section in ("data_source", "ingestion"):
                if section in ds.errors:
                    info.errors.setdefault(section, ds.errors[section])
            info.data_sources.append(ds)
        if info.arn:
            tags = get(
                info.errors,
                "tags",
                lambda: self.client.list_tags_for_resource(resourceArn=info.arn),
            )
            if tags is not None:
                info.tags = dict(tags.get("tags") or {})
        return info

    # ------------------------------------------------------------- data sources

    def data_sources(self, kb: str) -> list[DataSourceInfo]:
        """
        The data sources of a knowledge base: ID, name and status (describe() adds their settings and syncs).
        """
        kb_id = self.resolve(kb)
        sources = [
            DataSourceInfo(
                id=s["dataSourceId"],
                name=s.get("name", ""),
                status=s.get("status", ""),
                kb_id=kb_id,
                description=s.get("description", ""),
                updated=s.get("updatedAt"),
            )
            for s in self._paginate(
                "list_data_sources", "dataSourceSummaries", knowledgeBaseId=kb_id
            )
        ]
        self._source_names[kb_id] = {ds.id: ds.name for ds in sources}
        return sources

    def data_source_name(self, kb_id: str, ds_id: str) -> str:
        """
        The name of a data source already listed (its ID otherwise). Makes no AWS call.
        """
        return self._source_names.get(kb_id, {}).get(ds_id) or ds_id

    def data_source_names(self, kb: str, *, refresh: bool = False) -> dict[str, str]:
        """
        {ID: name} of a knowledge base's data sources (one ListDataSources, cached).
        """
        kb_id = self.resolve(kb)
        if refresh or kb_id not in self._source_names:
            self.data_sources(kb_id)
        return dict(self._source_names[kb_id])

    def resolve_sources(self, kb: str, data_source: Any) -> dict[str, str]:
        """
        {ID: name} of the data sources a search should cover, from data_source=: a data source's name (any case)
        or ID, or a list of them. None, [] or 'all' -> {} (every data source). A dict is taken as already resolved
        (a Retrieval's data_sources). An unknown one raises a ValueError that lists the knowledge base's data
        sources.
        """
        if isinstance(data_source, dict):
            return {str(k): str(v or "") for k, v in data_source.items()}
        if data_source is None or (
            isinstance(data_source, str) and data_source.strip().lower() in ("all", "*")
        ):
            return {}
        items = (
            list(data_source)
            if isinstance(data_source, (list, tuple, set, frozenset))
            else [data_source]
        )
        wanted = [
            str(item.id if isinstance(item, DataSourceInfo) else item).strip()
            for item in items
        ]
        if not wanted:
            return {}
        if any(not w for w in wanted):
            raise ValueError(
                "data_source= takes a data source's name or ID, or a list of them (kb_info() lists them)"
            )
        kb_id = self.resolve(kb)
        cached = kb_id in self._source_names
        try:
            names = self.data_source_names(kb_id)
        except (ClientError, BotoCoreError):
            if all(_KB_ID_RE.match(w) for w in wanted):
                return {w: "" for w in wanted}  # can't list them, but the IDs may still be right
            raise
        found, missing = _match_sources(names, wanted)
        if missing and cached:  # maybe added since they were listed
            names = self.data_source_names(kb_id, refresh=True)
            found, missing = _match_sources(names, wanted)
        if missing:
            close = difflib.get_close_matches(
                missing[0].lower(), [n.lower() for n in names.values()], n=2, cutoff=0.6
            )
            close_names = [n for n in names.values() if n.lower() in close]
            text = f"{self.kb_name(kb_id)} has no data source {missing[0]!r}."
            if close_names:
                text += f" Did you mean {' or '.join(map(repr, close_names))}?"
            listed = ", ".join(
                f"{name} ({ds_id})"
                for ds_id, name in sorted(names.items(), key=lambda i: i[1].lower())
            )
            text += f" Its data sources: {listed}." if names else " It has no data sources."
            raise ValueError(text + " kb_info() shows them.")
        return found

    def _pick_sources(
        self, kb_id: str, data_source: str | None
    ) -> list[DataSourceInfo]:
        """
        Every data source, or the one named by `data_source` (its ID or name, any case).
        """
        sources = self.data_sources(kb_id)
        if data_source is None:
            return sources
        wanted = str(data_source).strip()
        picked = [ds for ds in sources if ds.id == wanted] or [
            ds for ds in sources if ds.name.lower() == wanted.lower()
        ]
        if not picked:
            names = ", ".join(f"{ds.name} ({ds.id})" for ds in sources) or "none"
            raise ValueError(
                f"{self.kb_name(kb_id)} has no data source {wanted!r}; its data sources: {names}"
            )
        return picked[:1]

    def _recent_jobs(
        self, kb_id: str, ds_id: str, n: int, status: str | None = None
    ) -> list[IngestionJob]:
        params: dict[str, Any] = {
            "knowledgeBaseId": kb_id,
            "dataSourceId": ds_id,
            "maxResults": max(1, min(n, 1000)),
            "sortBy": {"attribute": "STARTED_AT", "order": "DESCENDING"},
        }
        if status:
            params["filters"] = [
                {"attribute": "STATUS", "operator": "EQ", "values": [status]}
            ]
        resp = self.client.list_ingestion_jobs(**params)
        return [
            parse_ingestion_job(job) for job in resp.get("ingestionJobSummaries", [])
        ][:n]

    @staticmethod
    def _set_jobs(ds: DataSourceInfo, jobs: list[IngestionJob]) -> None:
        ds.jobs = jobs
        ds.last_sync = jobs[0] if jobs else None
        ds.last_success = next((job for job in jobs if job.status == "COMPLETE"), None)

    def _add_reasons(self, kb_id: str, job: IngestionJob) -> None:
        """
        Fill in why a job failed (only GetIngestionJob returns the reasons). Leaves it alone if that call fails.
        """
        try:
            desc = self.client.get_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=job.data_source_id,
                ingestionJobId=job.id,
            )["ingestionJob"]
        except (ClientError, BotoCoreError):
            return
        job.failure_reasons = list(desc.get("failureReasons") or [])

    def ingestion_jobs(
        self, kb: str, data_source: str | None = None, n: int = 10
    ) -> list[IngestionJob]:
        """
        The last n syncs of every data source (or of one, by ID or name), newest first, with the reasons for
        failed ones.
        """
        kb_id = self.resolve(kb)
        n = _as_int(n, "n")
        jobs: list[IngestionJob] = []
        for ds in self._pick_sources(kb_id, data_source):
            jobs += self._recent_jobs(kb_id, ds.id, n)
        jobs = sorted(jobs, key=lambda j: j.started or _EPOCH, reverse=True)[:n]
        for job in [j for j in jobs if j.status == "FAILED" or j.failed][:10]:
            self._add_reasons(kb_id, job)
        return jobs

    def documents(
        self,
        kb: str,
        data_source: str | None = None,
        status: str | Iterable[str] | None = None,
        limit: int | None = 10_000,
        progress: Callable[[int], None] | None = None,
    ) -> tuple[list[KBDocument], DocumentSummary]:
        """
        Documents and whether each is searchable: (documents with `status`, or all of them; a summary of every
        document read). Reads at most `limit` documents (None = all). Data sources whose type has no document list
        (only S3 and custom ones do) are recorded in the summary's `errors`.
        """
        kb_id = self.resolve(kb)
        limit = _as_count(limit, "limit")
        wanted = {
            s.upper() for s in ([status] if isinstance(status, str) else (status or []))
        }
        docs: list[KBDocument] = []
        errors: dict[str, str] = {}
        truncated = False
        for ds in self._pick_sources(kb_id, data_source):
            try:
                for page in self.client.get_paginator(
                    "list_knowledge_base_documents"
                ).paginate(knowledgeBaseId=kb_id, dataSourceId=ds.id):
                    for desc in page.get("documentDetails", []):
                        if limit is not None and len(docs) >= limit:
                            truncated = True
                            break
                        docs.append(parse_document(desc))
                    if progress:
                        progress(len(docs))
                    if truncated:
                        break
            except (ClientError, BotoCoreError) as exc:
                errors[ds.id] = _error_name(exc)
            if truncated:
                break
        summary = summarize_documents(docs, truncated=truncated)
        summary.errors = errors
        return [d for d in docs if not wanted or d.status in wanted], summary
