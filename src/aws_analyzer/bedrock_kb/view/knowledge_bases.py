"""
BedrockKBView's commands about knowledge bases.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone
from ..._kit.errors import _why
from ..._kit.fmt import _fmt_dt, human_money
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..models import _EPOCH
from ..analysis.settings import describe_chunking, describe_parsing, describe_vector_store, store_name
from ..analysis.cost import idle_cost_label, vector_store_monthly_cost
from ..analysis.findings import kb_findings
from .cells import _DELETION, _KB_TYPES, _job_row, _model_label, _section, _status_tone, _sync_label, _sync_tone


class _KnowledgeBaseCommands:
    """
    BedrockKBView's commands about knowledge bases: kbs(), use() and kb_info().
    """

    # ---------------------------------------------------------- knowledge bases

    @_friendly_errors
    def use(self, kb: str) -> None:
        """
        Sets the knowledge base that later commands use when you don't pass kb=.
        """
        kb_id = self.core.resolve(kb)
        self.kb, self._conversation = kb_id, None
        name = self.core.kb_name(kb_id)
        self._show(
            [
                _Note(f"Using knowledge base {name} ({kb_id}) from now on.", "ok"),
                _Next(
                    [
                        (
                            "kb_info()",
                            "its settings in plain English, data sources and syncs",
                        ),
                        (
                            _call("search", "a question your documents answer"),
                            "the passages it retrieves",
                        ),
                    ]
                ),
            ]
        )

    @_friendly_errors
    def kbs(self) -> None:
        """
        Every knowledge base in the region: status, type, vector store, embedding model, sources, documents,
        last sync, estimated idle cost and warnings.
        """
        with self._progress("Checking knowledge bases", unit="knowledge bases") as tick:
            infos = sorted(
                self.core.list_knowledge_bases(progress=tick),
                key=lambda i: i.name.lower(),
            )
        rows: list[list[Any]] = []  # cells are text, or _Tone for a coloured status
        warnings: list[list[str]] = []
        unreadable: list[str] = []
        idle: dict[
            str, float
        ] = {}  # collection -> $/month, so knowledge bases sharing one count it once
        never = 0
        for info in infos:
            if "describe" in info.errors:
                unreadable.append(
                    f"{info.name} ({_why(info.errors['describe'], 'bedrock:GetKnowledgeBase')})"
                )
                rows.append(
                    [
                        info.name,
                        info.id,
                        info.status or "?",
                        "?",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                    ]
                )
                continue
            found = [
                message
                for level, message in kb_findings(info, prices=self.core.prices)
                if level == "warn"
            ]
            warnings += [[info.name, message] for message in found]
            cost = vector_store_monthly_cost(info, self.core.prices)
            if cost is not None:
                collection = (
                    info.vector_store_detail.get("opensearchServerlessConfiguration")
                    or {}
                ).get("collectionArn", info.id)
                idle[collection] = cost
            never += sum(
                1
                for ds in info.data_sources
                if ds.last_sync is None and "ingestion" not in ds.errors
            )
            scanned = [
                ds.last_success.scanned for ds in info.data_sources if ds.last_success
            ]
            rows.append(
                [
                    info.name,
                    info.id,
                    _Tone(info.status, _status_tone(info.status)),
                    _KB_TYPES.get(info.kb_type, info.kb_type.lower() or "-"),
                    store_name(info.vector_store),
                    _model_label(info.embedding_model),
                    _section(info, "data_sources", f"{len(info.data_sources):,}"),
                    f"{sum(scanned):,}" if scanned else "-",
                    _Tone(
                        _section(info, "ingestion", _sync_label(info.last_sync)),
                        ""
                        if "ingestion" in info.errors
                        else _sync_tone(info.last_sync),
                    ),
                    idle_cost_label(info, self.core.prices)
                    if cost is not None
                    else "-",
                    _Tone(str(len(found)), "warn" if found else ""),
                ]
            )
        blocks: list[Any] = [
            _Title(
                f"Knowledge bases in {self.core.region} ({len(infos)})",
                "documents = files the last successful sync read · idle cost is the OpenSearch Serverless minimum "
                f"at {self._price_basis()}, before any searches",
            ),
            _Cards(
                [
                    ("Knowledge bases", f"{len(infos):,}"),
                    ("Data sources", f"{sum(len(i.data_sources) for i in infos):,}"),
                    (
                        "Never synced",
                        f"{never:,} data source{'' if never == 1 else 's'}",
                        "warn" if never else "",
                    ),
                    (
                        "Est. idle cost / month",
                        human_money(sum(idle.values())) if idle else "-",
                    ),
                    (
                        "With warnings",
                        f"{len({name for name, _ in warnings}):,}",
                        "warn" if warnings else "ok",
                    ),
                ]
            ),
        ]
        if not infos:
            blocks.append(
                _Note(
                    f"No knowledge bases in {self.core.region}. They're regional: try "
                    "BedrockKBView(BedrockKBAnalyzer(region='us-west-2'))."
                )
            )
            self._show(blocks)
            return
        if unreadable:
            blocks.append(_Note(f"Couldn't describe {', '.join(unreadable)}.", "warn"))
        blocks.append(
            _Table(
                [
                    "Name",
                    "ID",
                    "Status",
                    "Type",
                    "Vector store",
                    "Embedding model",
                    "Sources",
                    "Documents",
                    "Last sync",
                    "Est. idle $/month",
                    "Warnings",
                ],
                rows,
                max_rows=0,
            )
        )
        if warnings:
            blocks.append(
                _Table(
                    ["Knowledge base", "Warning"],
                    warnings,
                    max_rows=0,
                    prose_cols=(1,),
                    title="Warnings (kb_info(name) shows every finding for one knowledge base)",
                )
            )
        readable = [info.name for info in infos if "describe" not in info.errors]
        if readable:
            flagged = Counter(name for name, _ in warnings).most_common(1)
            look = flagged[0][0] if flagged else readable[0]
            blocks.append(
                _Next(
                    [
                        (
                            _call("kb_info", look),
                            "why it's flagged, and what to change"
                            if flagged
                            else "its settings in plain English, data sources and syncs",
                        ),
                        (_call("use", look), "make it the one later commands use"),
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def kb_info(self, kb: str | None = None) -> None:
        """
        Settings in plain English, data sources (chunking, parsing, last sync), recent syncs, findings and cost.
        Also tags, and how to try the knowledge base.
        """
        info = self.core.describe(self._kb(kb))
        cost = vector_store_monthly_cost(info, self.core.prices)
        embedding = _model_label(info.embedding_model) + (
            f" ({info.embedding_dims:,} dims)" if info.embedding_dims else ""
        )
        blocks: list[Any] = [
            _Title(
                f"Knowledge base {info.name}",
                " · ".join(filter(None, [info.id, _clip(info.description, 120)])),
            ),
            _Cards(
                [
                    ("Status", info.status or "?", _status_tone(info.status)),
                    ("Type", _KB_TYPES.get(info.kb_type, info.kb_type.lower() or "?")),
                    ("Vector store", store_name(info.vector_store)),
                    ("Embedding model", embedding),
                    (
                        "Data sources",
                        _section(info, "data_sources", f"{len(info.data_sources):,}"),
                    ),
                    (
                        "Last sync",
                        _section(info, "ingestion", _sync_label(info.last_sync)),
                        ""
                        if "ingestion" in info.errors
                        else _sync_tone(info.last_sync),
                    ),
                    (
                        "Est. idle cost / month",
                        human_money(cost) if cost is not None else "not estimated",
                    ),
                    ("Created", _fmt_dt(info.created)),
                ]
            ),
        ]
        blocks.append(
            _Findings(
                kb_findings(info, prices=self.core.prices),
                empty="No issues found by these checks.",
            )
        )
        settings = [
            ["Vector store", describe_vector_store(info.vector_store_detail)],
            ["Embedding model", embedding],
            [
                "Idle cost",
                f"about {human_money(cost)}/month ({self._price_basis()})"
                if cost is not None
                else idle_cost_label(info, self.core.prices),
            ],
            ["Service role", info.role_arn or "-"],
            ["ARN", info.arn or "-"],
            ["Last changed", _fmt_dt(info.updated)],
        ]
        blocks.append(
            _Table(["Setting", "Value"], settings, title="Settings", max_rows=0)
        )
        rows = [
            [
                f"{ds.name} ({ds.id})",
                ds.source_type or "?",
                _section(ds, "data_source", ds.location or "-"),
                _section(ds, "data_source", describe_chunking(ds.chunking)),
                _section(ds, "data_source", describe_parsing(ds.parsing))
                + (f"; then {ds.transformation}" if ds.transformation else ""),
                _DELETION.get(ds.deletion_policy or "", ds.deletion_policy or "-"),
                _Tone(
                    _section(ds, "ingestion", _sync_label(ds.last_sync)),
                    "" if "ingestion" in ds.errors else _sync_tone(ds.last_sync),
                ),
            ]
            for ds in info.data_sources
        ]
        headers = [
            "Data source",
            "Type",
            "Location",
            "Chunking",
            "Parsing",
            "When deleted",
            "Last sync",
        ]
        several = len(info.data_sources) > 1
        if several:  # how to ask one of them
            headers.append("To ask only it")
            for row, ds in zip(rows, info.data_sources):
                row.append(f"data_source={ds.name or ds.id!r}")
        blocks.append(
            _Table(
                headers,
                rows,
                title="Data sources"
                + (" (search() and ask() take data_source= to use one)" if several else ""),
                max_rows=0,
                code_cols=(len(headers) - 1,) if several else (),
            )
        )
        jobs = sorted(
            (job for ds in info.data_sources for job in ds.jobs),
            key=lambda j: j.started or _EPOCH,
            reverse=True,
        )[:5]
        if jobs:
            names = {ds.id: ds.name for ds in info.data_sources}
            blocks.append(
                _Table(
                    [
                        "Data source",
                        "Started",
                        "Took",
                        "Status",
                        "Scanned",
                        "New",
                        "Modified",
                        "Deleted",
                        "Failed",
                    ],
                    [_job_row(job, names)[:9] for job in jobs],
                    title="Recent syncs (syncs() shows more, with reasons)",
                    max_rows=0,
                )
            )
        if info.tags:
            blocks.append(
                _Table(
                    ["Tag", "Value"],
                    [[k, v] for k, v in sorted(info.tags.items())],
                    title="Tags",
                    collapsed=True,
                )
            )
        on = self._on(info.id, info.name)
        steps = [
            (
                _call("search", "a question your documents answer", **on),
                "try it: the passages it retrieves",
            ),
            (_call("syncs", **on), "sync history, and why syncs failed"),
        ]
        if any(ds.source_type == "S3" for ds in info.data_sources):
            steps.append(
                (_call("unsynced", **on), "files changed in S3 since the last sync")
            )
        blocks.append(_Next(steps))
        self._show(blocks)
