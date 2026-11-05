"""
The Syncs tab's mixin: the sync history, read when the tab is first opened.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Note, _Title
from ..._kit.fmt import _plural, human_age
from ..._kit.window import _skeleton

from ..helpers import sync_command
from ..models import IngestionJob, KnowledgeBaseInfo
from ..analysis.findings import sync_findings
from ..blocks import _Steps
from .frame import _sync_step

_SYNCS_READ = 50
_FINISHED = ("COMPLETE", "FAILED", "STOPPED")
_COMMANDS_SHOWN = 3


def _latest_finished(jobs: list[IngestionJob]) -> dict[str, IngestionJob]:
    """
    Each data source's newest finished sync (the jobs come newest first): what the findings are about.
    """
    latest: dict[str, IngestionJob] = {}
    for job in jobs:
        if job.status in _FINISHED:
            latest.setdefault(job.data_source_id, job)
    return latest


def _sync_cards(jobs: list[IngestionJob]) -> _Cards:
    latest = _latest_finished(jobs)
    last_ok = next((job for job in jobs if job.status == "COMPLETE"), None)
    failed_docs = sum(job.failed for job in latest.values())
    newest_failed = any(job.status == "FAILED" for job in latest.values())
    return _Cards([
        ("Syncs", f"{len(jobs):,}"),
        ("Failed", f"{sum(job.status == 'FAILED' for job in jobs):,}", "warn" if newest_failed else ""),
        ("Last successful", human_age(last_ok.started) if last_ok else "none"),
        ("Documents failed", f"{failed_docs:,}" if latest else "-", "warn" if failed_docs else ""),
    ])


class _SyncsTab:
    """
    The Syncs tab of KBExplorer: every sync, newest first, with what each read, added, changed and deleted, and why
    one failed.
    """

    def _load_syncs(self) -> None:
        """
        Reads the sync history in the background. self.jobs becomes [] at once, so it isn't asked for twice.
        """
        info = self.info
        if info is None:
            return
        self.jobs = []
        self._set(self.syncs_view, _skeleton("Reading the sync history…"))
        if self._tab == "syncs":
            self._status(f"Reading {info.name}'s sync history…", busy=True)
        self._later("syncs", lambda: self.core.ingestion_jobs(info.id, n=_SYNCS_READ), self._got_syncs,
                    self._syncs_failed)

    def _syncs_failed(self, exc: BaseException) -> None:
        self.jobs = None
        text = f"Couldn't read the sync history: {self._error_text(exc)}"
        self._set(self.syncs_view, self._html([_Note(text, "warn")]))
        if self._tab == "syncs":
            self._status(text, "warn")

    def _history_blocks(self, info: KnowledgeBaseInfo, jobs: list[IngestionJob], names: dict[str, str]) -> list[Any]:
        if not jobs:
            commands = "; ".join(sync_command(info.id, ds.id, self.core.region)
                                 for ds in info.data_sources[:_COMMANDS_SHOWN])
            return [_Note(f"No syncs yet, so nothing is searchable. Sync each data source: {commands}", "warn")]
        return [_Findings(sync_findings(jobs, names), empty="No issues found by these checks."),
                _Steps([_sync_step(job, names) for job in jobs], title="Every sync, newest first")]

    def _got_syncs(self, jobs: list[IngestionJob]) -> None:
        self.jobs = jobs
        info = self.info
        if info is None:
            return
        names = {ds.id: ds.name for ds in info.data_sources}
        blocks: list[Any] = [
            _Title(f"Syncs of {info.name}", f"newest first · {_plural(len(jobs), 'sync')} of "
                                            f"{_plural(len(info.data_sources), 'data source')}"),
            _sync_cards(jobs),
            *self._history_blocks(info, jobs, names),
        ]
        sync = self.ui._sync_block(info.id, [ds.id for ds in info.data_sources], names)
        if sync:
            blocks.append(sync)
        self._draw("syncs", self.syncs_view, blocks)
        if self._tab == "syncs":
            self._status(self._syncs_line() or "No syncs yet")

    def _syncs_line(self) -> str:
        if not self.jobs:
            return ""
        failed = sum(job.status == "FAILED" for job in self.jobs)
        return f"{_plural(len(self.jobs), 'sync')}, newest first" + (f" · {failed:,} failed" if failed else "")
