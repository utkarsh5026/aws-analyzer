"""
BedrockKBView's commands about what's indexed: files, syncs, documents and unsynced files.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable

from botocore.exceptions import ClientError

from ..._bedrock.blocks import _file_list_blocks
from ..._bedrock.estimates import query_cost
from ..._bedrock.files import (
    SavedFileList, _link_seconds, _list_target, _looks_like_path, _notebook_href, file_list_name, parse_list_link,
)
from ..._bedrock.helpers import source_name
from ..._bedrock.models import KBDocument, Passage
from ..._bedrock.words import question_terms
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.errors import _Hint, _error_name, _why
from ..._kit.fmt import _as_int, _fmt_dt, _plural, human_age, human_money, human_size
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..helpers import CHUNK_LIMIT, METADATA_SUFFIX, _isnt, human_duration, sync_call, sync_command
from ..models import FILE_STATES, DocumentChunks, FileInventory, FileProbe, KBFile, KnowledgeBaseInfo, MetadataFile
from ..analysis.common import _METADATA_EXAMPLE
from ..analysis.findings import freshness_findings, sync_findings
from ..analysis.files import (
    _same_uri, file_findings, file_state, file_steps, find_files, inventory_findings, parse_file_state, pick_files,
    probe_findings, sort_files, sync_needed,
)
from ..blocks import _Chunks, _Link, _Steps
from .cells import _DOC_ORDER, _DOC_STATES, _job_row, _open_label, _passage_blocks, _short_value


class _IndexCommands:
    """
    BedrockKBView's commands about what's indexed: files(), save_file_list(), file(), search_file(), syncs(),
    documents() and unsynced(), with the blocks the explorer window draws them with.
    """

    @_friendly_errors
    def syncs(
        self, kb: str | None = None, *, data_source: str | None = None, n: int = 10
    ) -> None:
        """
        Sync history: when, how long, status, and scanned / new / modified / deleted / failed counts, with
        why syncs failed.
        """
        kb_id = self._kb(kb)
        jobs = self.core.ingestion_jobs(kb_id, data_source, n=n)
        sources = self.core.data_sources(kb_id)
        names = {ds.id: ds.name for ds in sources}
        name = self.core.kb_name(kb_id)
        last_ok = next((job for job in jobs if job.status == "COMPLETE"), None)
        latest = jobs[0] if jobs else None
        sub = f"newest first · {_plural(len(jobs), 'sync')}" + (
            f" of data source {data_source}" if data_source else ""
        )
        blocks: list[Any] = [
            _Title(f"Syncs of {name}", sub),
            _Cards(
                [
                    ("Syncs shown", f"{len(jobs):,}"),
                    (
                        "Failed",
                        f"{sum(j.status == 'FAILED' for j in jobs):,}",
                        "warn" if any(j.status == "FAILED" for j in jobs) else "",
                    ),
                    (
                        "Last successful",
                        human_age(last_ok.started) if last_ok else "none shown",
                    ),
                    (
                        "Docs failed (latest)",
                        f"{latest.failed:,}" if latest else "-",
                        "warn" if latest and latest.failed else "",
                    ),
                    ("Latest took", human_duration(latest.duration) if latest else "-"),
                ]
            ),
        ]
        if not jobs:
            blocks.append(
                _Note(
                    "No syncs yet, so nothing is searchable. Sync each data source: "
                    + "; ".join(
                        sync_command(kb_id, ds.id, self.core.region)
                        for ds in sources[:3]
                    ),
                    "warn",
                )
            )
            self._show(blocks)
            return
        blocks.append(
            _Findings(
                sync_findings(jobs, names), empty="No issues found by these checks."
            )
        )
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
                    "Why it failed",
                ],
                [_job_row(job, names) for job in jobs],
                max_rows=0,
            )
        )
        picked = [
            ds for ds in sources if not data_source or data_source in (ds.id, ds.name)
        ] or sources
        commands = [
            f"{sync_command(kb_id, ds.id, self.core.region)}   # {ds.name}"
            for ds in picked[:5]
        ]
        commands.append(
            f"# or from Python: {sync_call(kb_id, picked[0].id, self.core.region)}"
            if picked
            else ""
        )
        blocks.append(
            _Text(
                "\n".join(filter(None, commands)),
                title="To sync again (this tool never starts a sync: it changes the index)",
                code=True,
            )
        )
        on = self._on(kb_id, name)
        steps = (
            [
                (
                    _call("documents", status="FAILED", **on),
                    "which documents failed, and why",
                )
            ]
            if any(j.failed for j in jobs)
            else []
        )
        if any(ds.source_type == "S3" for ds in picked):
            steps.append(
                (_call("unsynced", **on), "files changed in S3 since the last sync")
            )
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def documents(
        self,
        kb: str | None = None,
        *,
        data_source: str | None = None,
        status: str | None = None,
        n: int = 50,
    ) -> None:
        """
        Documents by status (indexed / failed / pending ...), the ones that aren't indexed with Bedrock's reason,
        and the command to sync again.
        """
        kb_id = self._kb(kb)
        n = _as_int(n, "n")
        with self._progress("Listing documents", unit="documents") as tick:
            docs, summary = self.core.documents(
                kb_id, data_source, status=status, progress=tick
            )
        sources = {ds.id: ds for ds in self.core.data_sources(kb_id)}
        sub = f"{summary.total:,} documents read" + (
            " (stopped at the limit, so counts are partial: use .core.documents"
            "(..., limit=None) for all)"
            if summary.truncated
            else ""
        )
        if status:
            sub += f" · showing status {status.upper()}"
        cards = [("Documents", f"{summary.total:,}")]
        cards += [
            (
                _DOC_STATES.get(state, state.title()),
                f"{count:,}",
                "bad" if "FAILED" in state and count else "",
            )
            for state, count in sorted(
                summary.counts.items(),
                key=lambda kv: _DOC_ORDER.index(kv[0]) if kv[0] in _DOC_ORDER else 99,
            )
        ]
        blocks: list[Any] = [
            _Title(f"Documents in {self.core.kb_name(kb_id)}", sub),
            _Cards(cards),
        ]
        for ds_id, code in summary.errors.items():
            ds = sources.get(ds_id)
            kind = f"{ds.name} ({ds.id})" if ds else ds_id
            blocks.append(
                _Note(
                    f"Couldn't list the documents of data source {kind}: "
                    f"{_why(code, 'bedrock:ListKnowledgeBaseDocuments')}. Document status is only kept for "
                    "S3 and custom data sources; syncs() shows the others' failed counts."
                )
            )
        failed = summary.counts.get("FAILED", 0)
        if failed:
            top = (
                f" Most common reason: {summary.reasons[0][0].rstrip('.')}."
                if summary.reasons
                else ""
            )
            commands = "; ".join(
                sync_command(kb_id, ds_id, self.core.region)
                for ds_id in sorted(
                    {d.data_source_id for d in docs if d.status == "FAILED"}
                    or set(sources)
                )[:3]
            )
            blocks.append(
                _Note(
                    f"{_plural(failed, 'document')} failed to index and {_isnt(failed)} searchable.{top} Fix or "
                    f"replace the files (see the reasons below), then sync again: {commands}",
                    "warn",
                )
            )
        if not summary.total and not summary.errors:
            blocks.append(
                _Note(
                    "No documents yet: the data sources haven't been synced, or they're empty. syncs() "
                    "shows the sync history."
                )
            )
        ordered = sorted(
            docs,
            key=lambda d: (
                _DOC_ORDER.index(d.status) if d.status in _DOC_ORDER else 99,
                d.uri,
            ),
        )
        title = (
            f"Documents with status {status.upper()}"
            if status
            else "Documents, failed first"
        )
        attention = [d for d in ordered if d.status != "INDEXED"]
        if (
            not status and attention
        ):  # the indexed ones are fine: list what needs a look
            hidden = len(ordered) - len(attention)
            ordered, title = attention, "Documents that aren't fully indexed"
            if hidden:
                blocks.append(
                    _Note(
                        f"{_plural(hidden, 'indexed document')} {_isnt(hidden)} listed: "
                        "documents(status='INDEXED') lists them."
                    )
                )
        elif not status and ordered:
            blocks.append(
                _Note(
                    f"All {len(ordered):,} documents read are indexed and searchable.",
                    "ok",
                )
            )
        rows = [
            [
                _Tone(
                    _DOC_STATES.get(d.status, d.status),
                    ""
                    if d.status == "INDEXED"
                    else "bad"
                    if "FAILED" in d.status
                    else "warn",
                ),
                _Link(self._url(d.uri), d.name or d.uri),
                sources[d.data_source_id].name
                if d.data_source_id in sources
                else d.data_source_id,
                d.reason or "",
                human_age(d.updated),
            ]
            for d in ordered[:n]
        ]
        if docs or status:
            blocks.append(
                _Table(
                    ["Status", "Document", "Data source", "Reason", "Updated"],
                    rows,
                    max_rows=0,
                    title=title,
                )
            )
        if len(ordered) > n:
            blocks.append(
                _Note(
                    f"{len(ordered) - n:,} more not shown: pass n= for more, or use .core.documents(...) "
                    "for all of them."
                )
            )
        on = self._on(kb_id, self.core.kb_name(kb_id))
        steps = [(_call("syncs", **on), "sync history, and why syncs failed")]
        if any(ds.source_type == "S3" for ds in sources.values()):
            steps.append(
                (_call("unsynced", **on), "files changed in S3 since the last sync")
            )
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def unsynced(
        self, kb: str | None = None, *, data_source: str | None = None
    ) -> None:
        """
        Files added or changed in S3 since the last successful sync, and the command to sync them.
        """
        kb_id = self._kb(kb)
        with self._progress("Listing files", unit="files") as tick:
            results = self.core.unsynced(kb_id, data_source, progress=tick)
        region = self.core.region
        checked = [f for f in results if not f.note]
        changed = sum(len(f.changed) for f in checked)
        syncs = [
            f.last_sync.started for f in checked if f.last_sync and f.last_sync.started
        ]
        blocks: list[Any] = [
            _Title(
                f"Changes since the last sync: {self.core.kb_name(kb_id)}",
                "S3 files compared with the start of each data source's last successful sync",
            ),
            _Cards(
                [
                    ("Data sources checked", f"{len(checked):,} of {len(results):,}"),
                    ("Files", f"{sum(f.files for f in checked):,}"),
                    (
                        "Changed since sync",
                        f"{changed:,}",
                        "warn" if changed else "ok" if checked else "",
                    ),
                    ("Oldest last sync", human_age(min(syncs)) if syncs else "-"),
                ]
            ),
        ]
        blocks.append(_Findings(freshness_findings(results, kb_id, region)))
        for fresh in checked:
            ds = fresh.data_source
            if fresh.truncated:
                blocks.append(
                    _Note(
                        f"Stopped listing {ds.location} at the limit, so there may be more changes: "
                        ".core.unsynced(..., limit=None) lists everything."
                    )
                )
            if fresh.changed:
                rows = [
                    [
                        _Link(self._url(c.uri), c.key),
                        _fmt_dt(c.modified),
                        human_age(c.modified),
                        human_size(c.size),
                    ]
                    for c in fresh.changed
                ]
                blocks.append(
                    _Table(
                        ["File", "Modified", "Age", "Size"],
                        rows,
                        title=f"{ds.name}: changed files",
                    )
                )
            elif fresh.last_sync is not None and not fresh.metadata_changed:
                blocks.append(
                    _Note(
                        f"{ds.name} is up to date: none of its {fresh.files:,} files changed since the sync "
                        f"of {_fmt_dt(fresh.last_sync.started)}.",
                        "ok",
                    )
                )
        stale = [
            f.data_source
            for f in checked
            if f.changed or f.metadata_changed or f.last_sync is None
        ]
        if stale:
            lines = [
                f"{sync_command(kb_id, ds.id, region)}   # {ds.name}" for ds in stale
            ]
            lines.append(f"# or from Python: {sync_call(kb_id, stale[0].id, region)}")
            blocks.append(
                _Text(
                    "\n".join(lines),
                    title="To sync (this tool never starts a sync: it changes the index)",
                    code=True,
                )
            )
        self._show(blocks)

    # ------------------------------------------------------------- files, one by one

    def _inventory(self, kb_id: str, *, refresh: bool = False, info: KnowledgeBaseInfo | None = None) -> FileInventory:
        """
        The knowledge base's files (file_inventory), listed once and kept, so naming a file is quick.
        """
        if refresh or kb_id not in self._inventories:
            with self._progress("Listing files", unit="files") as tick:
                self._inventories[kb_id] = self.core.file_inventory(kb_id, info=info, progress=tick)
        return self._inventories[kb_id]

    def _find_file(self, kb_id: str, path: Any, info: KnowledgeBaseInfo | None = None) -> KBFile:
        """
        The file `path` names (find_files): a name, a path in the bucket or an s3:// path. A name several files
        share, or one no file has, raises a _Hint that says which there are.
        """
        text = str(path.uri if isinstance(path, KBFile) else path or "").strip()
        if not text:
            raise ValueError("Pass a file: its name, its path in the bucket or its s3:// path (files() lists them)")
        inv = self._inventory(kb_id, info=info)
        found = find_files(inv.files, text)
        if not found and kb_id in self._inventories and text.startswith("s3://"):
            found = find_files(self._inventory(kb_id, refresh=True, info=info).files, text)  # maybe added since
        if len(found) == 1:
            return found[0]
        if found:
            paths = ", ".join(f.key for f in found[:6])
            raise _Hint(f"{len(found)} files match {text!r}: pass one's path, like file({found[0].key!r}). They are "
                        f"{paths}{', …' if len(found) > 6 else ''}.")
        names = {f.name.lower(): f.name for f in inv.files}
        close = [names[n] for n in difflib.get_close_matches(text.lower().rsplit("/", 1)[-1], list(names), n=3,
                                                             cutoff=0.6)]
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        listed = f" It lists {_plural(len(inv.files), 'file')}" if inv.files else " It lists no files"
        raise _Hint(f"No file of {inv.kb_name or kb_id} matches {text!r}.{hint}{listed} from its S3 and custom data "
                    "sources: files() shows them.")

    def _file_view(self, kb_id: str, f: KBFile) -> tuple[DocumentChunks | None, MetadataFile | None, list[_Note]]:
        """
        What file() shows of a file beyond its record: its chunks (when it's searchable) and its metadata file,
        with notes for what couldn't be read. A file the document list stopped before (unchecked) is looked up on its
        own first, and its state set from what Bedrock says.
        """
        notes: list[_Note] = []
        chunks = meta = None
        if f.state == "unchecked" and f.data_source_id:
            try:
                record = self.core.document_status(kb_id, f.uri, f.data_source_id)
            except ClientError as exc:
                why = _why(_error_name(exc), "bedrock:GetKnowledgeBaseDocuments")
                notes.append(_Note(f"Couldn't look up Bedrock's record of it ({why}), so whether it's indexed isn't "
                                   "known.", "warn"))
            else:
                self._recheck(kb_id, f, record)
        if f.uri.startswith("s3://"):
            if f.searchable:
                try:
                    chunks = self.core.document_chunks(kb_id, f.uri)
                except ClientError as exc:
                    error = exc.response.get("Error", {})
                    notes.append(_Note(f"Couldn't read its chunks ({error.get('Code', 'Error')}: "
                                       f"{self._explain(error.get('Code', ''), error.get('Message', str(exc)))})",
                                       "warn"))
            meta = self.core.metadata_file(f.uri)
        return chunks, meta, notes

    def _recheck(self, kb_id: str, f: KBFile, record: KBDocument | None) -> None:
        """
        A file's state again, now that Bedrock's record of it (or that it has none) is known.
        """
        inv = self._inventories.get(kb_id)
        last = inv.last_sync.get(f.data_source_id) if inv else None
        if record is not None:
            f.status, f.reason, f.indexed = record.status, record.reason, record.updated
        f.state, f.note = file_state(
            f, last.started if last else None,
            listed=not inv or "files" not in inv.truncated.get(f.data_source_id, []),
            sync_known=not inv or "syncs" not in inv.errors.get(f.data_source_id, {}))

    @_friendly_errors
    def files(
        self,
        kb: str | None = None,
        *,
        data_source: str | None = None,
        status: str | None = None,
        match: str | None = None,
        n: int = 50,
    ) -> None:
        """
        Every file of the knowledge base next to Bedrock's record of it: indexed, failed (and why), changed in S3
        since it was indexed, added after the last sync, skipped by the sync, deleted from S3, with the command that
        syncs them. Problems first; status= ('failed', 'changed', 'new', ...) or match= (part of a name or path)
        narrows the list.
        """
        kb_id = self._kb(kb)
        n = _as_int(n, "n")
        wanted = parse_file_state(status) if status else ""
        inv = self._list_files(kb_id, data_source)
        self._show(self._files_blocks(inv, state=wanted, match=match, n=n, data_source=data_source))

    def _list_files(self, kb_id: str, data_source: str | None) -> FileInventory:
        """
        The knowledge base's files read now (one data source's, or every one's, kept for file()).
        """
        if data_source is None:
            return self._inventory(kb_id, refresh=True)
        with self._progress("Listing files", unit="files") as tick:
            return self.core.file_inventory(kb_id, data_source, progress=tick)

    def _files_blocks(self, inv: FileInventory, *, state: str = "", match: str | None = None, n: int = 50,
                      window: bool = False, data_source: str | None = None) -> list[Any]:
        """
        files()'s report: cards per state, findings, and the files that need a look first (window=True: the
        explorer's summary, without the list, which the window has beside it). data_source: as files() was given it,
        for the next steps' calls.
        """
        counts = inv.counts()
        several = len({f.data_source_id for f in inv.files}) > 1  # a column for it only when the files tell apart
        sub = [f"{_plural(len(inv.files), 'file')} from {_plural(len(inv.sources), 'data source')}",
               "S3 next to Bedrock's document list", "problems first"]
        cards: list[tuple[str, ...]] = [("Files", f"{len(inv.files):,}")]
        for key, (label, tone) in FILE_STATES.items():
            if counts.get(key):
                cards.append((label, f"{counts[key]:,}", tone))
        blocks: list[Any] = [_Title(f"Files of {inv.kb_name or inv.kb_id}", " · ".join(sub))]
        if not window:  # the window's chips count the states
            blocks.append(_Cards(cards))
        blocks.append(_Findings(inventory_findings(inv),
                                empty="Every file is indexed and up to date with S3."))
        sync = self._sync_block(inv.kb_id, sync_needed(inv), inv.sources)
        if window:
            return blocks + ([sync] if sync else [])
        shown = sort_files(pick_files(inv.files, state=state, match=match), "problems")
        rows = []
        for f in shown[:n]:
            label, tone = FILE_STATES.get(f.state, (f.state, ""))
            row: list[Any] = [_Tone(label, tone), _Link(self._url(f.uri), f.name), f.folder or "-"]
            if several:
                row.append(inv.sources.get(f.data_source_id, f.data_source_id))
            row += [human_size(f.size) if f.size is not None else "-", human_age(f.modified),
                    human_age(f.indexed) if f.indexed else "never", f.note]
            rows.append(row)
        headers = ["State", "File", "Folder"] + (["Data source"] if several else []) + [
            "Size", "Changed in S3", "Indexed", "Why"]
        title = ("Files" + (f" that are {FILE_STATES[state][0].lower()}" if state in FILE_STATES else "")
                 + (f" matching {match!r}" if match else ""))
        if inv.files:
            blocks.append(_Table(headers, rows, title=title, max_rows=0, prose_cols=(len(headers) - 1,)))
        if len(shown) > n:
            blocks.append(_Note(f"{len(shown) - n:,} more not shown: pass n= for more, or .core.file_inventory(...)"
                                ".to_df() for all of them."))
        if sync:
            blocks.append(sync)
        if shown:
            first = shown[0]
            on = self._on(inv.kb_id, inv.kb_name)
            narrowed = {k: v for k, v in (("data_source", data_source), ("status", state), ("match", match)) if v}
            some = "these files'" if narrowed else "every file's"
            steps = [(_call("file", first.key, **on), "how it was indexed: its chunks, metadata and what to fix"),
                     (_call("search_file", first.key, "a question it should answer", **on),
                      "whether a question finds it, and where it ranks"),
                     (_call("save_file_list", **on, **narrowed), f"{some} s3:// path in a .txt file, one per line, "
                                                                 "to download")]
            blocks.append(_Next(steps))
        return blocks

    @_friendly_errors
    def save_file_list(
        self,
        kb: str | None = None,
        path: str | None = None,
        *,
        data_source: str | None = None,
        status: str | None = None,
        match: str | None = None,
        link: str = "s3",
        expires: int = 3600,
    ) -> None:
        """
        Saves the knowledge base's files as a text file, one per line, with a link that downloads it to your
        computer: each file's s3:// path, sorted by path, ready for a script, aws s3 cp or a spreadsheet.

        status= ('failed', 'changed', 'new', ...), match= (part of a name or path) and data_source= keep only the
        files files() lists with them. link='https' writes a link that opens each file in a browser instead (presigned
        here with your credentials, no AWS call; each works for `expires` seconds, an hour unless you say, and anyone
        holding one can open its file until then), and link='key' each one's path in the bucket. path: default
        <knowledge base>-files.txt beside the notebook ('failed-files' etc. with status=); a folder puts it there, and
        a file of that name is replaced. Only that file is written: nothing in AWS changes.
        """
        if path is None and _looks_like_path(kb):
            kb, path = None, kb
        kb_id = self._kb(kb)
        wanted = parse_file_state(status) if status else ""
        link = parse_list_link(link)
        if link == "https":
            expires = _link_seconds(expires)
        inv = self._list_files(kb_id, data_source)
        files = sort_files(pick_files(inv.files, state=wanted, match=match), "folder")
        name = inv.kb_name or inv.kb_id
        if not files:
            some = " and ".join(filter(None, [f"is {FILE_STATES[wanted][0].lower()}" if wanted else "",
                                              f"matches {match!r}" if match else ""]))
            raise _Hint(f"No file of {name} {some}, so nothing was saved: files() shows the files there are." if some
                        else f"{name} has no files to list (no S3 or custom data source, or nothing in them yet), so "
                             "nothing was saved.")
        saved = self.core.save_file_list(files, _list_target(path, file_list_name(name, wanted)), link=link,
                                         expires=expires)
        narrowed = {"data_source": data_source, "status": wanted, "match": match}
        self._show(self._saved_list_blocks(saved, inv, href=_notebook_href(saved.path), narrowed=narrowed))

    def _saved_list_blocks(self, saved: SavedFileList, inv: FileInventory, *, href: str = "",
                           narrowed: dict[str, Any] | None = None) -> list[Any]:
        """
        save_file_list()'s report: what was written where, the link that downloads it, its first lines and what's
        not in it. narrowed: the filters it was given, for the next steps' calls.
        """
        notes = []
        for ds_id, parts in inv.truncated.items():
            what = " and ".join("Bedrock's document list" if p == "documents" else "the bucket's files" for p in parts)
            notes.append(_Note(f"Listing {what} for {inv.sources.get(ds_id, ds_id)} stopped at {inv.limit:,}, so it "
                               "may miss files: pass .core.file_inventory(..., limit=None) to .core.save_file_list() "
                               "for every one.", "warn"))
        unread = [inv.sources.get(ds_id, ds_id) for ds_id, errors in inv.errors.items()
                  if {"settings", "files"} & set(errors)]
        if unread:
            notes.append(_Note(f"{' and '.join(unread)}'s files couldn't be listed, so it may miss some of them: "
                               "files() says why.", "warn"))
        blocks = _file_list_blocks(saved, inv.kb_name or inv.kb_id, href=href, notes=notes)
        narrowed = {k: v for k, v in (narrowed or {}).items() if v}
        on = self._on(inv.kb_id, inv.kb_name)
        steps = []
        if saved.link != "https":
            steps.append((_call("save_file_list", **on, **narrowed, link="https"),
                          "the same files as links that open each one in a browser, for an hour"))
        if saved.link != "s3":
            steps.append((_call("save_file_list", **on, **narrowed), "the same files' s3:// paths"))
        steps.append((_call("files", **on, **narrowed), "the same files with their state, and why"))
        blocks.append(_Next(steps))
        return blocks

    def _sync_block(self, kb_id: str, ds_ids: list[str], names: dict[str, str]) -> _Text | None:
        """
        The commands that sync these data sources, to copy (this tool never syncs: a sync changes the index).
        """
        if not ds_ids:
            return None
        region = self.core.region
        lines = [f"{sync_command(kb_id, ds_id, region)}   # {names.get(ds_id) or ds_id}" for ds_id in ds_ids[:5]]
        lines.append(f"# or from Python: {sync_call(kb_id, ds_ids[0], region)}")
        return _Text("\n".join(lines), title="To sync (this tool never starts a sync: it changes the index)", code=True)

    @_friendly_errors
    def file(self, path: str, kb: str | None = None) -> None:
        """
        How one file was indexed, step by step: stored in S3, read by the parser, cut into chunks (each in document
        order with its size, page and the text it shares with the one before), embedded and stored, and where it
        stands now. Also its metadata file, checked against what its chunks carry, and what to fix. path: the file's
        name, its path in the bucket or its s3:// path.
        """
        kb_id = self._kb(kb)
        info = self.core.describe(kb_id)
        f = self._find_file(kb_id, path, info)
        with self._progress("Reading its chunks", unit="chunks"):
            chunks, meta, notes = self._file_view(kb_id, f)
        self._show(self._file_blocks(f, info, chunks, meta, notes=notes))

    def _file_blocks(
        self,
        f: KBFile,
        info: KnowledgeBaseInfo | None,
        chunks: DocumentChunks | None,
        meta: MetadataFile | None,
        *,
        notes: Iterable[Any] = (),
        loading: bool = False,
        window: bool = False,
    ) -> list[Any]:
        """
        file()'s report (and the explorer's file page): cards, findings, how it was indexed, its chunks, its
        metadata next to its chunks', and Bedrock's record. loading: the chunks and metadata are still being read.
        """
        kb_id = info.id if info else ""
        ds = next((d for d in info.data_sources if d.id == f.data_source_id), None) if info else None
        inv = self._inventories.get(kb_id)
        others = bool(inv) and any(o.metadata_size is not None for o in inv.files
                                   if o.data_source_id == f.data_source_id and o.uri != f.uri)
        found = file_findings(f, chunks, meta, ds, kb_id=kb_id, region=self.core.region, others_have_metadata=others)
        label, tone = FILE_STATES.get(f.state, (f.state or "?", ""))
        warned = any(level == "warn" for level, _ in found)
        meta_label = ("-" if meta is None else "can't read" if meta.error else "none" if not meta.found
                      else "has problems" if meta.problems else _plural(len(meta.attributes), "attribute"))
        count = ("…" if loading else "-" if chunks is None else
                 f"{chunks.stats.count:,}{'+' if chunks.truncated else ''}")
        cards: list[tuple[str, ...]] = [
            ("State", label, tone if tone == "bad" or (tone == "warn" and warned) else "ok" if tone == "ok" else ""),
            ("Chunks", count),
            ("Size", human_size(f.size) if f.size is not None else "-"),
            ("Changed in S3", human_age(f.modified)),
            ("Indexed", human_age(f.indexed) if f.indexed else "never"),
            ("Metadata", "…" if loading else meta_label, "warn" if meta is not None and meta.problems else ""),
        ]
        where = " · ".join(filter(None, [f.uri, f"data source {ds.name}" if ds and ds.name else ""]))
        blocks: list[Any] = [_Title(f.name, where), _Cards(cards)]
        url = self._url(f.uri) if f.uri.startswith("s3://") and f.size is not None else ""
        if url:
            blocks.append(_Link(url, _open_label(Passage(rank=0, text="", uri=f.uri), url)))
        blocks += list(notes)
        blocks.append(_Findings(found, empty="Indexed, up to date and searchable: no issues found by these checks."))
        blocks.append(_Steps(file_steps(f, ds, info, chunks), title="How it was indexed"))
        if loading:
            blocks.append(_Note("Reading its chunks and its metadata file…"))
        elif chunks is not None:
            items = [(i, p, chunks.stats.overlaps[i - 1]) for i, p in enumerate(chunks.chunks, 1)]
            spans = ([(chunks.spans[p.key][0] / chunks.text_length, chunks.spans[p.key][1] / chunks.text_length)
                      for p in chunks.chunks if p.key in chunks.spans] if chunks.text_length else [])
            title = (f"Its {chunks.stats.count:,} chunks, in document order" if chunks.chunks else "Its chunks")
            if chunks.truncated:
                title += f" (the first {CHUNK_LIMIT} Retrieve returns)"
            blocks.append(_Chunks(items, title=title, spans=spans, coverage=chunks.stats.coverage))
            fixed = (ds.chunking.get("fixedSizeChunkingConfiguration") or {}) if ds else {}
            if fixed.get("overlapPercentage") and any(chunks.stats.overlaps):
                blocks.append(_Note(f"Fixed-size chunking with {fixed['overlapPercentage']}% overlap: each chunk starts "
                                    "with the end of the one before it, so neighbours share some text, and each "
                                    "chunk's line says how much."))
            if chunks.text_note:
                blocks.append(_Note(f"The chunks are ordered by page and overlap: {chunks.text_note}."))
        elif f.uri.startswith("s3://") and not f.searchable:
            blocks.append(_Note("It has no chunks: nothing of it is in the vector store."))
        blocks += self._metadata_blocks(f, meta, chunks)
        record = [
            ["Bedrock's status", f.status or "no record"],
            ["Bedrock's reason", f.reason or "-"],
            ["Record updated", _fmt_dt(f.indexed)],
            ["Data source", f"{ds.name} ({ds.id})" if ds else f.data_source_id or "-"],
            ["S3 path", f.uri],
            ["Size", f"{f.size:,} bytes" if f.size is not None else "-"],
            ["Changed in S3", _fmt_dt(f.modified)],
            ["Storage class", f.storage_class or "-"],
        ]
        if chunks is not None:
            record.append(["Chunks read with", f"Retrieve, filtered to this file, asked {chunks.query!r} for up to "
                                               f"{chunks.asked} passages ({chunks.seconds:.1f}s)"])
        blocks.append(_Table(["Field", "Value"], record, title="Bedrock's record and the S3 object", collapsed=True))
        if not window:
            on = self._on(kb_id, info.name if info else "")
            blocks.append(_Next([
                (_call("search_file", f.key, "a question it should answer", **on),
                 "whether a question finds it, and where it ranks"),
                (_call("files", status=f.state, **on), f"every file that's {label.lower()}"),
            ]))
        return blocks

    def _metadata_blocks(self, f: KBFile, meta: MetadataFile | None, chunks: DocumentChunks | None) -> list[Any]:
        """
        The file's metadata file next to the metadata its chunks carry, and the problems with it.
        """
        if meta is None or meta.error:
            return []
        on_chunks: dict[str, Any] = {}
        for p in (chunks.chunks if chunks else []):
            for key, value in p.metadata.items():
                on_chunks.setdefault(key, value)
        name = f"{f.name}{METADATA_SUFFIX}"
        blocks: list[Any] = []
        if not meta.found and not on_chunks:
            blocks.append(_Note(f"No metadata file: a {name} next to it gives its chunks attributes that where= filters "
                                f"can match, like {_METADATA_EXAMPLE}."))
            return blocks
        read = chunks is not None and bool(chunks.chunks)
        rows = []
        for key in dict.fromkeys([*meta.attributes, *on_chunks]):
            in_file = meta.attributes.get(key, "-") if meta.found else "-"
            row: list[Any] = [key, _short_value(in_file)]
            if read:
                same = key in meta.attributes and key in on_chunks and meta.attributes[key] == on_chunks[key]
                row.append(_Tone(_short_value(on_chunks.get(key, "-")), "" if same else "warn"))
            rows.append(row + [meta.types.get(key, "-"), "yes" if key in meta.embedded else "-"])
        if read:
            title = (f"Its metadata: {name} next to what its chunks carry (what where= filters match)" if meta.found
                     else "The metadata its chunks carry (what where= filters match)")
            headers = ["Attribute", "In the metadata file", "On its chunks", "Type", "Embedded with the text"]
        else:
            title, headers = f"Its metadata ({name})", ["Attribute", "Value", "Type", "Embedded with the text"]
        if rows:
            blocks.append(_Table(headers, rows, title=title, max_rows=0))
        if len(meta.problems) > 1:
            blocks += [_Note(problem, "warn") for problem in meta.problems[1:]]
        if meta.found:
            blocks.append(_Text(meta.text, title=f"{name} as written", collapsed=True))
        return blocks

    @_friendly_errors
    def search_file(self, path: str, question: str, kb: str | None = None, *, n: int = 10) -> None:
        """
        Whether a question finds a file: the file's own best passages for it, and where the file ranks among the
        whole knowledge base's (an answer only sees the top few), with what to change when it ranks too low.
        """
        kb_id = self._kb(kb)
        f = self._find_file(kb_id, path)
        with self._progress("Searching", unit="searches"):
            probe = self.core.probe_file(kb_id, f.uri, question, n=n)
        self._show(self._probe_blocks(probe))

    def _probe_blocks(self, probe: FileProbe, *, window: bool = False) -> list[Any]:
        name = source_name(probe.uri)
        inside, across = probe.inside.passages, probe.across.passages
        best = max((p.score for p in inside if p.score is not None), default=None)
        far = probe.rank is None or probe.rank > 5
        blocks: list[Any] = [
            _Title(f"Searching {name}: {_clip(probe.question, 80)}",
                   f"its own passages, and where it ranks among the whole knowledge base's top {len(across)} · cost "
                   f"at {self._price_basis()}"),
            _Cards([
                ("Its passages", f"{len(inside):,}", "warn" if not inside else ""),
                ("Best score", "-" if best is None else f"{best:.2f}"),
                ("Rank in the knowledge base", f"#{probe.rank}" if probe.rank else f"not in the top {len(across)}",
                 "warn" if inside and far else "ok" if probe.rank == 1 else ""),
                ("Time", f"{probe.inside.seconds + probe.across.seconds:.1f}s"),
                ("Est. cost", human_money(query_cost(2, prices=self.core.prices)) + " (2 searches)"),
            ]),
            _Findings(probe_findings(probe)),
        ]
        terms = question_terms(probe.question)
        blocks += _passage_blocks(inside, terms, width=320, link=self._link)
        rows = [[_Tone(f"#{p.rank}", "ok" if _same_uri(p.uri, probe.uri) else ""), _Link(self._link(p), p.source),
                 "-" if p.score is None else f"{p.score:.2f}", "this file" if _same_uri(p.uri, probe.uri) else ""]
                for p in across[:10]]
        if rows:
            blocks.append(_Table(["Rank", "Passage from", "Score", ""], rows, max_rows=0,
                                 title="The whole knowledge base's best passages for it"))
        if not window and inside:
            blocks.append(_Next([(_call("search", probe.question, n=max(5, probe.rank or 10)),
                                  "the passages an answer would get")]))
        return blocks
