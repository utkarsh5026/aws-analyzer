"""
The Overview tab's mixin, and the header's cards above every tab.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..._kit.blocks import _Findings, _Note, _Table, _Title
from ..._kit.fmt import _fmt_dt, _plural, human_money
from ..._kit.text import _clip, _esc

from ..models import FILE_STATES, _EPOCH, DataSourceInfo, FileInventory, KnowledgeBaseInfo
from ..analysis.settings import describe_chunking, describe_parsing, describe_vector_store, store_name
from ..analysis.cost import vector_store_monthly_cost
from ..analysis.findings import kb_findings
from ..analysis.files import inventory_findings, sync_needed
from ..blocks import _Pipeline, _Shares
from ..view.cells import _KB_TYPES, _job_row, _section, _status_tone, _sync_label, _sync_tone
from .frame import _LOGO, _parser_name

_RECENT_SYNCS = 5
_WAITING_STATES = ("failed", "changed", "new")
_SYNC_COLUMNS = ["Data source", "Started", "Took", "Status", "Scanned", "New", "Modified", "Deleted", "Failed"]

Stage = tuple[str, str, str, str]


def _source_files_text(files: Counter | None) -> str:
    """
    A data source's files as its Source stage says them: '12 files, 2 failed, 1 changed'.
    """
    if not files:
        return ""
    listed = f"{sum(files.values()):,} files"
    waiting = [f"{files[state]:,} {FILE_STATES[state][0].lower()}" for state in _WAITING_STATES if files[state]]
    return ", ".join([listed, *waiting])


def _source_stage(ds: DataSourceInfo, files: Counter | None) -> Stage:
    where = " · ".join(part for part in (ds.location, _source_files_text(files)) if part)
    tone = "bad" if ds.status == "FAILED" else "warn" if files and files.get("failed") else ""
    return ("Source", ds.source_type or "?", where, tone)


def _chunking_stage(ds: DataSourceInfo) -> Stage:
    chunking = describe_chunking(ds.chunking)
    how = chunking.partition(": ")[2] or chunking
    tone = "warn" if ds.chunking.get("chunkingStrategy") == "NONE" else ""
    return ("Chunking", _section(ds, "data_source", chunking.split(":")[0]), _section(ds, "data_source", how), tone)


def _store_stage(info: KnowledgeBaseInfo) -> Stage:
    store = describe_vector_store(info.vector_store_detail) if info.vector_store_detail else ""
    name = store_name(info.vector_store)
    detail = store[len(name):].strip() if store.startswith(name) else store
    return ("Vector store", name, detail, "")


def _last_sync_stage(ds: DataSourceInfo) -> Stage:
    started = _fmt_dt(ds.last_sync.started) if ds.last_sync else ""
    tone = "" if "ingestion" in ds.errors else _sync_tone(ds.last_sync)
    return ("Last sync", _section(ds, "ingestion", _sync_label(ds.last_sync)), started, tone)


def _pipeline_stages(info: KnowledgeBaseInfo, ds: DataSourceInfo, files: Counter | None) -> list[Stage]:
    """
    How one data source becomes vectors, stage by stage: its source, parser, chunking, transformation (when it has
    one), the embedding model, the vector store and its last sync.
    """
    stages = [
        _source_stage(ds, files),
        ("Parser", _section(ds, "data_source", _parser_name(ds.parsing)),
         _section(ds, "data_source", describe_parsing(ds.parsing)), ""),
        _chunking_stage(ds),
    ]
    if ds.transformation:
        stages.append(("Transformation", "custom", ds.transformation, ""))
    dims = f"{info.embedding_dims:,} dimensions" if info.embedding_dims else ""
    stages.append(("Embedding", info.embedding_model.split(".", 1)[-1] or "-", dims, ""))
    stages.append(_store_stage(info))
    stages.append(_last_sync_stage(ds))
    return stages


def _counts_by_source(inv: FileInventory | None) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for f in inv.files if inv else []:
        counts.setdefault(f.data_source_id, Counter())[f.state] += 1
    return counts


def _recent_syncs(info: KnowledgeBaseInfo) -> _Table | None:
    jobs = sorted((job for ds in info.data_sources for job in ds.jobs), key=lambda j: j.started or _EPOCH,
                  reverse=True)[:_RECENT_SYNCS]
    if not jobs:
        return None
    names = {ds.id: ds.name for ds in info.data_sources}
    rows = [_job_row(job, names)[:len(_SYNC_COLUMNS)] for job in jobs]
    return _Table(_SYNC_COLUMNS, rows, title="Recent syncs (the Syncs tab has more, with reasons)", max_rows=0)


def _file_cards(inv: FileInventory) -> list[tuple[str, str, str]]:
    counts = inv.counts()
    searchable = sum(1 for f in inv.files if f.searchable)
    all_searchable = searchable == len(inv.files) and bool(inv.files)
    cards = [("Files", f"{len(inv.files):,}", ""), ("Searchable", f"{searchable:,}", "ok" if all_searchable else "")]
    if counts.get("failed"):
        cards.append(("Failed", f"{counts['failed']:,}", "bad"))
    waiting = counts.get("changed", 0) + counts.get("new", 0)
    if waiting:
        cards.append(("To sync", f"{waiting:,}", "warn"))
    return cards


class _OverviewTab:
    """
    The Overview tab of KBExplorer (what's wrong, how each data source becomes vectors, recent syncs), and the title
    and cards over every tab.
    """

    def _draw_title(self) -> None:
        info = self.info
        sub = ["Every file, how it was indexed, its syncs and what a question finds · read-only"]
        if info is not None:
            sub = [info.name or info.id, _KB_TYPES.get(info.kb_type, info.kb_type.lower() or "knowledge base"),
                   store_name(info.vector_store), _clip(info.description, 90) if info.description else ""]
        self._set(self.title, f'<div class="kbx-brand"><span class="kbx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="kbx-name">Knowledge base explorer<span>{_esc(self.core.region)}</span>'
                              f'</div><div class="kbx-sub">{_esc(" · ".join(p for p in sub if p))}</div></div></div>')

    def _kb_cards(self, info: KnowledgeBaseInfo) -> list[tuple[str, str, str]]:
        sync_tone = "" if "ingestion" in info.errors else _sync_tone(info.last_sync)
        cost = vector_store_monthly_cost(info, self.core.prices)
        return [("Last sync", _section(info, "ingestion", _sync_label(info.last_sync)), sync_tone),
                ("Idle cost / mo", human_money(cost) if cost is not None else "not estimated", "")]

    def _draw_stats(self) -> None:
        """
        The cards beside the knowledge base field: how it's doing at a glance. The field's green dot already says
        ACTIVE, so the Status card shows only another status.
        """
        info, inv = self.info, self.inventory
        cards: list[tuple[str, str, str]] = []
        if info is not None and info.status != "ACTIVE":
            cards.append(("Status", info.status.title() if info.status else "?", _status_tone(info.status)))
        if inv is not None:
            cards += _file_cards(inv)
        if info is not None:
            cards += self._kb_cards(info)
        else:
            cards += [("Last sync", "…", "sk-on"), ("Idle cost / mo", "…", "sk-on")]
        if inv is None:
            cards.insert(1, ("Files", "…", "sk-on"))
        html_cards = "".join(f'<div class="kbx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="kbx-stats">{html_cards}</div>')

    def _overview_findings(self, info: KnowledgeBaseInfo, inv: FileInventory | None) -> list[Any]:
        found = kb_findings(info, prices=self.core.prices, files=inv)
        if inv is None:
            return [_Findings(found, empty="No issues found in the settings and syncs."),
                    _Note("Listing the files, to say which are indexed…")]
        found += inventory_findings(inv)
        blocks: list[Any] = [_Findings(found, empty="No issues found by these checks: every file is indexed and up to "
                                                    "date.")]
        sync = self.ui._sync_block(info.id, sync_needed(inv), inv.sources)
        if sync:
            blocks.append(sync)
        return blocks

    def _draw_overview(self) -> None:
        info, inv = self.info, self.inventory
        if info is None:
            return
        blocks: list[Any] = [_Title(f"{info.name}", " · ".join(filter(None, [info.id, _clip(info.description, 120)])))]
        if inv is not None and inv.files:
            shares = [(FILE_STATES[state][0], count, state) for state, count in reversed(inv.counts().items())]
            blocks.append(_Shares(shares, title=f"Its {_plural(len(inv.files), 'file')}, by state (the Files tab "
                                                "lists them)"))
        blocks += self._overview_findings(info, inv)
        counts = _counts_by_source(inv)
        rows = [(f"{ds.name or ds.id} ({ds.id})", _pipeline_stages(info, ds, counts.get(ds.id)))
                for ds in info.data_sources]
        if rows:
            blocks.append(_Pipeline(rows, title="How each data source becomes vectors"))
        elif "data_sources" not in info.errors:
            blocks.append(_Note("It has no data sources yet, so there's nothing to search."))
        recent = _recent_syncs(info)
        if recent is not None:
            blocks.append(recent)
        self._draw("overview", self.overview, blocks)

    def _overview_line(self) -> str:
        inv = self.inventory
        if inv is None:
            return ""
        problems = sum(n for state, n in inv.counts().items() if FILE_STATES[state][1] in ("bad", "warn"))
        if not problems:
            return f"{_plural(len(inv.files), 'file')}, every one indexed and up to date"
        return f"{_plural(len(inv.files), 'file')}, {problems:,} to look at in the Files tab"
