"""
BedrockKBAnalyzer's reads that help decide what to change: unsynced files, comparisons and
evaluations.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.files import presigned_url
from ..._bedrock.parsing import match_expected
from ..._kit.errors import _error_code, _error_name, _why
from ..._kit.fmt import _as_count, _as_int

from ..models import EvalCase, EvalReport, Retrieval, SearchComparison, SyncFreshness
from ..analysis.prompting import _question_text
from ..analysis.parsing import _with_errors, parse_data_source
from ..analysis.deciding import _eval_pairs, changed_since, compare_retrievals, retrieval_metrics


class _DecidingReads:
    """
    BedrockKBAnalyzer's reads that help decide what to change: links to files, S3 files changed since the last
    sync, two search setups side by side, and how well test questions find their sources.
    """

    # ------------------------------------------------------------------ deciding

    def _s3_client(self) -> Any:
        """
        s3: listing a data source's bucket for unsynced(), and signing links to its files (SigV4, which every
        region accepts).
        """
        return self._cached_client(
            "s3",
            lambda: self.session.client(
                "s3",
                region_name=self.region,
                config=self._config.merge(Config(signature_version="s3v4")),
            ),
        )

    def file_url(
        self, uri: str, *, page: int | None = None, expires: int = 3600
    ) -> str | None:
        """
        A link that opens a source's file in a browser tab, or None when there's no file to open (a custom or
        SQL data source).

        For an s3:// file it's a presigned GetObject link, signed here with your credentials (no AWS call): it works
        for `expires` seconds, only while those credentials are valid, and only if they may read the file
        (s3:GetObject). It makes the browser show a PDF (at `page`), picture, text or HTML file instead of saving it.
        A web, Confluence, SharePoint or Salesforce source links to its own address.
        """
        return presigned_url(self._s3_client, uri, page=page, expires=expires)

    def unsynced(
        self,
        kb: str,
        data_source: str | None = None,
        limit: int | None = 100_000,
        progress: Callable[[int], None] | None = None,
    ) -> list[SyncFreshness]:
        """
        For each S3 data source (or one, by ID or name): the files added or changed since its last successful sync
        started. Lists the bucket (and inclusion prefixes) up to `limit` objects in all, marking results truncated;
        metadata files are counted apart. Other data source types are returned with a note.
        """
        kb_id = self.resolve(kb)
        limit = _as_count(limit, "limit")
        listed = 0
        results = []
        for summary in self._pick_sources(kb_id, data_source):
            fresh = SyncFreshness(summary)
            results.append(fresh)
            try:
                ds = _with_errors(
                    parse_data_source(
                        self.client.get_data_source(
                            knowledgeBaseId=kb_id, dataSourceId=summary.id
                        )["dataSource"]
                    ),
                    {},
                )
            except (ClientError, BotoCoreError) as exc:
                fresh.note = f"couldn't read its settings ({_why(_error_name(exc), 'bedrock:GetDataSource')})"
                continue
            fresh.data_source = ds
            if ds.source_type != "S3" or not ds.bucket:
                fresh.note = f"it's a {ds.source_type or 'non-S3'} data source, and only S3 files can be listed"
                continue
            done = self._recent_jobs(kb_id, ds.id, 1, status="COMPLETE")
            fresh.last_sync = done[0] if done else None
            objects: dict[str, dict[str, Any]] = {}
            try:
                for prefix in ds.prefixes or [""]:
                    for page in (
                        self._s3_client()
                        .get_paginator("list_objects_v2")
                        .paginate(Bucket=ds.bucket, Prefix=prefix)
                    ):
                        for obj in page.get("Contents", []):
                            if limit is not None and listed >= limit:
                                fresh.truncated = True
                                break
                            if obj["Key"] not in objects:
                                objects[obj["Key"]] = obj
                                listed += 1
                        if progress:
                            progress(listed)
                        if fresh.truncated:
                            break
                    if fresh.truncated:
                        break
            except (ClientError, BotoCoreError) as exc:
                fresh.note = f"couldn't list {ds.location} ({_why(_error_name(exc), 's3:ListBucket')})"
                continue
            since = fresh.last_sync.started if fresh.last_sync else None
            fresh.files = sum(
                1 for key in objects if not key.endswith((".metadata.json", "/"))
            )
            fresh.changed = changed_since(objects.values(), since)
            for change in fresh.changed:
                change.bucket = ds.bucket
            fresh.metadata_changed = sum(
                1
                for key, obj in objects.items()
                if key.endswith(".metadata.json")
                and (
                    since is None
                    or (
                        obj.get("LastModified") is not None
                        and obj["LastModified"] > since
                    )
                )
            )
        return results

    def compare(
        self,
        kb: str,
        question: str,
        *,
        n: int | Iterable[int] = (5, 10),
        search_types: str | Iterable[str | None] = ("SEMANTIC", "HYBRID"),
        where: Any = None,
        data_source: Any = None,
        progress: Callable[[int], None] | None = None,
    ) -> SearchComparison:
        """
        The same question searched with each search type and each n (one Retrieve per combination), and how much
        the results overlap. A setting the vector store rejects (e.g. HYBRID) is recorded in `errors`.
        """
        kb_id = self.resolve(kb)
        sources = self.resolve_sources(kb_id, data_source)
        sizes = [n] if isinstance(n, (int, str)) else list(n)
        kinds = (
            [search_types]
            if isinstance(search_types, str) or search_types is None
            else list(search_types)
        )
        runs: dict[str, Retrieval] = {}
        errors: dict[str, str] = {}
        for kind in kinds:
            for size in sizes:
                label = (
                    f"{str(kind).upper() if kind else 'DEFAULT'} n={_as_int(size, 'n')}"
                )
                try:
                    runs[label] = self.retrieve(
                        kb_id,
                        question,
                        size,
                        where=where,
                        search_type=kind,
                        data_source=sources,
                    )
                except ClientError as exc:
                    if _error_code(exc) != "ValidationException":
                        raise
                    errors[label] = exc.response.get("Error", {}).get(
                        "Message", "ValidationException"
                    )
                if progress:
                    progress(len(runs) + len(errors))
        comparison = compare_retrievals(runs)
        comparison.question, comparison.kb_id, comparison.kb_name = (
            _question_text(question),
            kb_id,
            self.kb_name(kb_id),
        )
        comparison.errors = errors
        comparison.data_sources = sources
        return comparison

    def evaluate(
        self,
        kb: str,
        cases: Any,
        *,
        n: int = 5,
        search_type: str | None = None,
        where: Any = None,
        data_source: Any = None,
        progress: Callable[[int], None] | None = None,
    ) -> EvalReport:
        """
        Retrieval hit rate and MRR on test questions: where each question's expected source came up in the top n.
        Retrieval only, no answers generated, so it stays cheap. cases: (question, expected) pairs, dicts with
        'question' and 'expected', or a DataFrame with those columns; expected is a piece of the source's URI, file
        name or text.
        """
        kb_id = self.resolve(kb)
        n = _as_int(n, "n")
        sources = self.resolve_sources(kb_id, data_source)
        report = EvalReport(
            kb_id=kb_id,
            kb_name=self.kb_name(kb_id),
            k=n,
            search_type=search_type,
            where=where,
            data_sources=sources,
        )
        started = time.monotonic()
        for i, (question, expected) in enumerate(_eval_pairs(cases), 1):
            r = self.retrieve(
                kb_id,
                question,
                n,
                where=where,
                search_type=search_type,
                data_source=sources,
            )
            rank = next(
                (p.rank for p in r.passages if match_expected(p, expected)), None
            )
            report.cases.append(
                EvalCase(
                    r.question,
                    expected,
                    rank,
                    [p.source for p in r.passages[:3]],
                    r.seconds,
                )
            )
            if progress:
                progress(i)
        report.hit_rate, report.mrr = retrieval_metrics(report.cases, n)
        report.seconds = time.monotonic() - started
        return report
