"""
The right pane: the files it draws as documents, the quick reports loaded in the background, and the
pane's mixin.
"""

from __future__ import annotations

import asyncio
import html
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Cards, _Note, _Table, _Text, _Title
from ..._kit.fmt import _plural, human_age, human_size, parse_size
from ..._kit.jsontree import _JsonTree
from ..._kit.window import _cell_number, _running_loop
from ..helpers import _MAX_PICTURES
from ..readers.formats import _READ_ERRORS
from ..style import _render_html
from ..._kit.themes import _theme_name
from ..view.s3_view import S3View

from .changes import changes_card, changes_sentence
from .navigator import Entry, Folder, _extension, breadcrumbs, entry_icon, explain_list_error, parse_location
from .snippets import file_snippet, files_snippet


_DOCUMENTS = ("pdf", "docx", "docm", "dotx", "pptx", "pptm", "potx", "ppsx")  # files "Read all" opens

_TEXT_PAGES = 50  # PDF pages one "Text" report shows (text is light, so more than the 20 "Read all" draws)


_BACKGROUND = ("preview", "head")  # quick reports (no progress bar) that load on worker threads in a notebook

_WORKERS = 4  # background reports loading at once, so a click doesn't wait for files clicked before it

_TOP_FILES = 20

_FOLDER_REPORTS = {
    "largest": ("largest", {"n": _TOP_FILES}),
    "newest": ("newest", {"n": _TOP_FILES}),
    "oldest": ("oldest", {"n": _TOP_FILES}),
    "details": ("file_details", {}),
    "duplicates": ("duplicates", {"method": "etag"}),
    "uploads": ("uploads", {}),
    "versions": ("versions", {}),
    "deleted": ("deleted", {}),
}

_STEPS = {"prev": -1, "next": 1}

_KEPT_WHILE_STEPPING = ("preview", "head", "code", "history")

_TAIL = ("prev", "next", "close")

_ICON_ACTIONS = {
    "prev": ("s3x-step", "s3x-ic", "s3x-i-previous"),
    "next": ("s3x-step", "s3x-ic", "s3x-i-next"),
    "close": ("s3x-close", "s3x-ic", "s3x-i-close"),
}

_UNMARKED = ("open", *_TAIL)


class _ReportPane:
    """
    The right pane of S3Explorer: what the folder holds, the chosen file's report (quick ones loaded in the
    background), the buttons above it, Download .zip, a PDF's pages and Expand all.
    """

    def _report(self, action: str, method: Callable | str, *args: Any, cache: bool = True, variant: Any = None,
                **kwargs: Any) -> None:
        """
        Run one of the pane's S3View commands (by name), or a method of the explorer, and show its report on the
        right, with its progress bar above it while it runs. Reports are cached per file version (and `variant`,
        such as the first page shown).

        In a notebook, the quick reports (_BACKGROUND) load on worker threads instead: the click returns at once,
        so the next click is handled straight away instead of waiting for this file to finish loading, and a report
        that a newer click made out of date is cached but not shown.
        """
        entry = self._entry()
        key = (action, self.selected or self.location, entry.etag if entry else "", variant)
        self._action = action
        self._mark_actions()
        if cache and key in self._cache:
            self._set_pane(self._cache[key])
            return
        run = getattr(self._pane, method) if isinstance(method, str) else method
        self._captured: list[list[Any]] = []
        if self._widgets is None:
            run(*args, **kwargs)
            self._set_pane(self._captured)
            return
        name = Entry("file", *parse_location(self.selected)).name if self.selected else ""
        waiting = {"preview": f"Opening {name}…", "head": f"Reading the details of {name}…",
                   "document": f"Reading {name}…", "text": f"Reading the text of {name}…",
                   "download": f"Downloading {name}…",
                   "summary": "Reading every file below this folder…",
                   "zip": "Listing every file below this folder, then zipping them…",
                   "picks": f"Zipping the {_plural(len(self._picked), 'selected item')}…",
                   "history": f"Listing the versions of {name}…",
                   **dict.fromkeys(("largest", "newest", "oldest", "duplicates"), "Listing every file below this folder…"),
                   "details": "Looking inside the files below this folder…",
                   "uploads": "Looking for uploads that never finished…",
                   "versions": "Listing the old versions below this folder…",
                   "deleted": "Looking for deleted files below this folder…"}
        self._wait(waiting.get(action, "Working…"))
        loop = _running_loop() if isinstance(method, str) and action in _BACKGROUND else None
        if loop is not None:
            self._task = loop.create_task(self._later(self._job, key, cache, method, self.selected, args, kwargs))
            return
        try:
            with self._progress:
                run(*args, **kwargs)
        finally:
            if _cell_number() is not None:  # outside a kernel, clear_output() prints terminal escape codes
                self._progress.clear_output()
        self._keep(key, self._captured, cache)
        self._set_pane(self._captured)

    def _keep(self, key: tuple, reports: list[list[Any]], cache: bool) -> None:
        """
        Cache a report, but not an error note: trying again then asks AWS again.
        """
        if cache and reports and reports[0] and isinstance(reports[0][0], _Title):
            while len(self._cache) >= 24:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = reports

    async def _later(self, job: int, key: tuple, cache: bool, method: str, selected: str, args: tuple,
                     kwargs: dict) -> None:
        """
        Wait, on the kernel's event loop, for a report loading on a worker thread, then show it unless the
        right pane has changed since (another file was clicked); it's cached either way.
        """
        try:
            reports = await asyncio.wrap_future(self._threads().submit(self._load, job, method, selected, args, kwargs))
            if reports is None:
                return
            self._keep(key, reports, cache)
            if job == self._job:
                self._set_pane(reports)
        except Exception as exc:  # a bug: still say so where the user is looking, and not in the kernel's log
            if job == self._job:
                self._fail(f"Something went wrong ({type(exc).__name__}: {exc}). Click the file again to try again.")

    def _threads(self) -> ThreadPoolExecutor:
        """
        The worker threads that background reports and listings run on.
        """
        if self._workers is None:
            self._workers = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="s3-explorer")
        return self._workers

    def _load(self, job: int, method: str, selected: str, args: tuple, kwargs: dict) -> list[list[Any]] | None:
        """
        On a worker thread: a report from an S3View of its own, which touches no widgets (no progress bar, and
        its reports are kept here). None when another click came before it started: it would only be thrown away.
        """
        if job != self._job:
            return None
        reports: list[list[Any]] = []
        view = S3View(self.core, progress="off")
        view.use_html = self._pane.use_html
        view._show = lambda blocks: reports.append(self._trim(blocks, selected))
        getattr(view, method)(*args, **kwargs)
        return reports

    def _wait(self, text: str) -> None:
        """
        The right pane while a report loads: a spinner, what it's doing, and the outline of a report.
        """
        self._job += 1
        self._overview = None
        lines = "".join(f'<div class="s3x-sk" style="width:{width}%"></div>' for width in (92, 84, 88, 64, 76))
        self._content.value = (f'<div class="s3x-wait"><div class="s3x-wait-t"><span class="s3x-spin"></span>'
                               f'{html.escape(text)}</div><div class="s3x-sk s3x-t"></div><div class="s3x-sk-cards">'
                               + '<div class="s3x-sk"></div>' * 4 + f"</div>{lines}</div>")
        self._settings.layout.display = self._picks_panel.layout.display = "none"
        self._draw_pager(False)
        self._draw_expand(False)
        self._renew("right")

    def _set_pane(self, reports: list[list[Any]], keep: bool = True) -> None:
        """
        Show reports on the right; the text explorer (no ipywidgets) shows them as the S3View would. keep=False
        is a note shown while a report loads.
        """
        self._job += 1
        self._overview = None
        if keep:
            self.shown = [block for blocks in reports for block in blocks]
            self._reports = reports
            for tree in self._trees():
                tree.unfold("all" if self._expand_all else "start")
        if self._widgets is None:
            for blocks in reports:
                type(self._pane)._show(self._pane, blocks)
            return
        self._content.value = "".join(_render_html(blocks, self._pane.max_rows, theme=_theme_name()) for blocks in reports)
        self._settings.layout.display = self._picks_panel.layout.display = "none"
        self._draw_pager(keep)
        self._draw_expand(keep)
        self._renew("right")

    def _show_folder(self, folder: Folder) -> None:
        self.selected = ""
        self._mark_rows()
        self._draw_actions()
        self._show_overview(folder)
        self._draw_status(folder)

    def _show_overview(self, folder: Folder) -> None:
        """
        What the folder holds, on the right (a background listing redraws it as more comes in).
        """
        self._set_pane([self._folder_blocks(folder)])
        self._overview = folder

    def _folder_blocks(self, folder: Folder, listing: bool = False) -> list[Any]:
        """
        The right pane when no file is chosen: what this folder holds, from the entries already listed (no
        extra AWS calls). listing=True adds the entries themselves, for the text explorer.
        """
        bucket, prefix = parse_location(folder.uri)
        title = breadcrumbs(folder.uri)[-1][0]
        blocks: list[Any] = [_Title(title if bucket else "Your buckets", folder.uri or "every bucket you can see")]
        if self.nav.notice:
            blocks.append(_Note(self.nav.notice, "warn"))
        if folder.error:
            blocks += [_Note(f"{explain_list_error(folder.error_code, bucket, prefix)} ({folder.error})", "warn")]
            return blocks
        stats = self._stats(folder)
        plus = "+" if folder.more else ""
        if not bucket:
            newest = max((e.modified for e in folder.entries if e.modified), default=None)
            blocks.append(_Cards([("Buckets", f"{len(folder.entries):,}")]
                                    + ([("Newest bucket", human_age(newest))] if newest else [])))
            blocks.append(_Note("Click a bucket to open it. The “Every bucket” button above compares their size, "
                                   "cost and security settings." if folder.entries else
                                   "No buckets in this account. Type a bucket's path above to open one you "
                                   "can read in another account."))
        else:
            below = " below" if folder.deep else ""
            cards = [(f"Folders{below}", f"{stats.folders:,}{plus}")] if stats.folders else []
            if stats.files:
                cards += [(f"Files{below}", f"{stats.files:,}{plus}"),
                          ("Size of these files", human_size(stats.size) + plus)]
            if stats.newest:
                cards.append(("Newest file", human_age(stats.newest)))
            if stats.archived:
                cards.append(("Archived", f"{stats.archived:,}", "warn"))
            changes = self._changes(folder)
            if changes is not None and changes.new_files:
                cards.append(changes_card(changes))
            if cards:
                blocks.append(_Cards(cards))
            if changes is not None:
                blocks.append(_Note(changes_sentence(changes, marked=self._widgets is not None)))
            if self._lister is folder:
                what = (f"everything below this folder: these numbers cover the {_plural(stats.files, 'file')}"
                        if folder.deep else f"this folder: these numbers cover the {len(folder.entries):,} entries")
                blocks.append(_Note(f"Still listing {what} listed so far, and grow as more come in. The search "
                                       "over the list catches up too."))
            elif folder.more:
                if listing:  # the text view has no buttons: the calls that do what they do
                    more = (f" To list more, set x.nav.{'deep_limit' if folder.deep else 'list_limit'} = "
                            f"{2 * self._goal(folder):_} and run x.refresh(); x.open('s3://…') opens a file by its "
                            "path, however far down it is.")
                else:
                    more = f" “Load more from S3” at the end of the list lists the next {self._goal(folder):,}" + (
                        "." if folder.deep else "; to find a file past those, type the start of its name in the "
                        "search box and click “Look up”.")
                blocks.append(_Note(
                    (f"These numbers cover the first {stats.files:,} files below this folder." if folder.deep else
                     f"This folder has more than {len(folder.entries):,} entries, and these numbers cover the first "
                     f"{len(folder.entries):,}.") + more))
            if stats.archived:
                blocks.append(_Note(f"{_plural(stats.archived, 'file')} here {'is' if stats.archived == 1 else 'are'}"
                                       " in GLACIER or DEEP_ARCHIVE (❄ in the list): they can't be opened until "
                                       "they're restored.", "warn"))
            if not folder.entries:
                blocks.append(_Note("This folder is empty." if prefix else "This bucket is empty."))
            if stats.types:
                blocks.append(_Table(["Type", "Files", "Size"],
                                        [[ext, f"{n:,}", human_size(size)] for ext, n, size in stats.types],
                                        title=f"File types {'below' if folder.deep else 'here'}", max_rows=0))
            if folder.entries and not listing:
                blocks.append(_Note(
                    "Everything below this folder is on the left, each under the folder it's in. Click a file to see "
                    "what's inside, or a folder to open it; the “What's in here” button above adds up every file, "
                    "with its cost." if folder.deep else
                    "Click a folder to open it, or a file to see what's inside. The sizes are for the files at this "
                    "level: “Include subfolders” over the list adds everything below, and the “What's in here” "
                    "button above adds it all up."))
        if listing:
            shown = self._shown_entries(folder)
            rows = [[f"{entry_icon(e)} {e.key[len(prefix):] if folder.deep else e.name}"
                     f"{'/' if e.kind == 'folder' and not folder.deep else ''}{' ❄' if e.archived else ''}",
                     "" if e.is_folder else human_size(e.size), human_age(e.modified) if e.modified else ""]
                    for e in shown]
            blocks.append(_Table(["Name", "Size", "Modified" if bucket else "Created"], rows,
                                    title=("Below this folder" if folder.deep else "In this folder") if bucket
                                    else "Buckets"))
            searched = [f"“{self._query.strip()}”"] if self._query.strip() else []
            searched += [f"{self._kind} only"] if bucket and self._kind != "all" else []
            if searched:
                blocks.append(_Note(f"Showing {len(shown):,} of {len(folder.entries):,}: {', '.join(searched)}. "
                                       "x.filter() shows them all."))
            blocks.append(_Note("This is the text view: x.open('s3://…') moves around and x.filter('.csv') "
                                   "searches; in a Jupyter notebook with ipywidgets every row is clickable."))
        return blocks

    def _file_actions(self) -> list[tuple[str, str, str]]:
        """
        The buttons for the chosen file: what you can see of it, take from it, and the files beside it.
        """
        name = Entry("file", *parse_location(self.selected)).name
        actions = [("preview", "👁️ Preview", "What's inside the file"),
                   ("head", "🏷️ Details", "Size, dates, storage class, metadata and tags")]
        kind = _extension(name).split(".")[0]
        if kind in _DOCUMENTS:
            actions.append(("document", "📖 Read all", "Every page as it looks, 20 at a time (click a page to see it "
                            "full size)" if kind == "pdf" else "The whole document, page by page"))
        if kind == "pdf":
            actions.append(("text", "📄 Text", f"The words of every page, {_TEXT_PAGES} at a time, laid out to read: "
                            "headings, paragraphs and lists, without the running headers and footers"))
        actions.append(("code", "🐍 Use in Python", "The lines that load this file in a notebook cell, written for "
                        "its format, with the same AWS credentials as this explorer"))
        if self._is_versioned(parse_location(self.selected)[0]):
            actions.append(("history", "🕘 Versions", "Every version of this file kept by the bucket's versioning: "
                            "when each was written, and its size"))
        actions += [("download", "⬇ Download", f"Save a copy in {self._where()} (⚙ changes the folder)"),
                    ("open", "↗ Open in new tab", "The file in a new browser tab: PDFs, pictures, sound, video "
                     "and text show there, other files download. The link works for an hour after you click the "
                     "file (right-click to copy it for someone without AWS access)")]
        actions += self._step_actions()
        actions.append(("close", "✕", "Close the file and show this folder"))
        return actions

    def _folder_actions(self, bucket: str, prefix: str) -> list[tuple[str, str, str]]:
        """
        The buttons for a folder: what's in it, its bucket's settings, the .zip, and More ▾, which opens a second
        row of reports on it (the biggest, newest and oldest files, what's inside them, copies, unfinished uploads,
        and in a versioned bucket old versions and deleted files).
        """
        actions = [("summary", "📊 What's in here", "Every file below this folder: sizes, types, cost and "
                    "findings (reads the whole listing, so big folders take a while)")]
        if not prefix:
            actions.append(("bucket_info", "🛡️ Bucket settings", "Versioning, encryption, lifecycle, policy, risks"))
        actions.append(("zip", "⬇ Download .zip", f"Everything below this folder as one .zip in {self._where()}, "
                        f"if it's no bigger than {self._zip_limit()} (⚙ changes that)"))
        if self._more_open:
            actions.append(("more", "Fewer ▴", "Hide the other reports on this folder"))
            return actions + self._more_actions(bucket)
        actions.append(("more", "More ▾", "Other reports on this folder: its biggest, newest and oldest files, "
                        "what's inside them, copies and unfinished uploads"))
        return actions

    def _more_actions(self, bucket: str) -> list[tuple[str, str, str]]:
        """
        The second row of folder reports that More ▾ opens. Each reads the whole listing below the folder.
        """
        actions = [
            ("largest", "🐘 Largest files", f"The {_TOP_FILES} biggest files below this folder"),
            ("newest", "🆕 Newest files", f"The {_TOP_FILES} files below this folder changed most recently"),
            ("oldest", "🕰️ Oldest files", f"The {_TOP_FILES} files below this folder left alone the longest"),
            ("details", "🔍 Inside the files", "What's inside each file: a table's rows and columns (and whether the "
             "tables here agree on them), a PDF's pages, a picture's size; reads only the parts it needs"),
            ("duplicates", "📑 Copies", "Files stored more than once below this folder, matched by size and ETag "
             "(no file is read), and what the copies cost"),
            ("uploads", "🧩 Unfinished uploads", "Uploads that never finished: billed, but not in any listing"),
        ]
        if self._is_versioned(bucket):
            actions += [("versions", "🕘 Old versions", "Old versions and delete markers below this folder, kept by "
                         "versioning, and what they cost"),
                        ("deleted", "🗑️ Deleted files", "Files deleted below this folder that versioning can still "
                         "bring back, with the call that does it (nothing is changed)")]
        return actions

    def _is_versioned(self, bucket: str) -> bool:
        """
        Whether the bucket keeps old versions (versioning on, or suspended with some kept), asked once per bucket.
        A bucket whose versioning can't be read counts as not versioned: its buttons would only show an error.
        """
        if bucket not in self._versioning:
            try:
                self._versioning[bucket] = self.core.versioning_status(bucket) in ("Enabled", "Suspended")
            except (ClientError, BotoCoreError):
                self._versioning[bucket] = False
        return self._versioning[bucket]

    def _draw_actions(self) -> None:
        """
        The buttons above the right pane: what you can do with the chosen file, or with this folder.
        """
        w = self._widgets
        if w is None:
            return
        bucket, prefix = parse_location(self.nav.location)
        if self.selected:
            actions = self._file_actions()
        elif bucket:
            actions = self._folder_actions(bucket, prefix)
        else:
            actions = [("overview", "🪣 Every bucket", "Each bucket's size, cost and security warnings")]
        old = self._actions.children
        self._act_buttons = {}
        for action, label, tip in actions:
            if action == "open":  # a link, so the browser opens the tab (a button's click only reaches Python)
                link = self._open_link(label, tip)
                if link:
                    self._act_buttons[action] = w.HTML(link, layout=w.Layout(width="auto"))
                continue
            b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
            b.add_class("s3x-act")
            for name in _ICON_ACTIONS.get(action, ()):
                b.add_class(name)
            if action in _STEPS:
                b.disabled = not self._neighbour(_STEPS[action])
            b.on_click(lambda _, action=action: self._guard(lambda: self._on_action(action)))
            self._act_buttons[action] = b
        self._draw_expand(False)
        for widget in old:
            if widget is not self._expand_btn:
                widget.close()
        if self.selected and self._action not in _KEPT_WHILE_STEPPING:
            self._action = "preview"
        elif not self.selected:
            self._action = ""
        self._mark_actions()

    def _neighbour(self, step: int) -> Entry | None:
        """
        The file before (step -1) or after (+1) the chosen one in the list as it's sorted and searched now,
        skipping folders; None at either end, or when the chosen file isn't in the list.
        """
        files = [entry for entry in self._visible if not entry.is_folder]
        at = next((i for i, entry in enumerate(files) if entry.uri == self.selected), -1)
        if at < 0 or not 0 <= at + step < len(files):
            return None
        return files[at + step]

    def _step_actions(self) -> list[tuple[str, str, str]]:
        """
        ‹ and ›, with the name of the file each goes to.
        """
        actions = []
        for action, step, word in (("prev", -1, "before"), ("next", 1, "after")):
            neighbour = self._neighbour(step)
            tip = f"The file {word} it in the list: {neighbour.name}" if neighbour else f"No file {word} it in the list"
            actions.append((action, "‹" if step < 0 else "›", tip))
        return actions

    def _step(self, step: int) -> None:
        """
        ‹ ›: show the file before or after in the list, the same way as this one (its preview, details or code),
        without going back to the list.
        """
        neighbour = self._neighbour(step)
        if neighbour is not None:
            self._select(neighbour.uri, self._action if self._action in _KEPT_WHILE_STEPPING else "preview")

    def _profile(self) -> str:
        return getattr(self.core.session, "profile_name", None) or ""

    def _use_in_python(self, uri: str) -> None:
        """
        🐍 Use in Python, for the chosen file: the lines to copy into a cell, and its path for other tools.
        """
        name = Entry("file", *parse_location(uri)).name
        snippet = file_snippet(uri, profile=self._profile())
        blocks: list[Any] = [_Title(f"Use {name} in Python", "with the same AWS credentials as this explorer"),
                             _Text(snippet.code, title="Copy this into a cell (one click selects it)", code=True)]
        if snippet.note:
            blocks.append(_Note(snippet.note))
        blocks.append(_Note(f"Its path, for other tools: {uri}. pd.read_csv('s3://…') reads a path directly when "
                            "s3fs is installed, and the explorer's .selected holds the path of the file shown here."))
        self._captured.append(blocks)

    def _use_picks_in_python(self) -> None:
        """
        🐍 Use in Python, for what's ticked in the list: one DataFrame when they're tables of one format.
        """
        uris = list(self._picked)
        snippet = files_snippet(uris, profile=self._profile())
        blocks: list[Any] = [_Title(f"Use the {_plural(len(uris), 'selected item')} in Python",
                                    "with the same AWS credentials as this explorer"),
                             _Text(snippet.code, title="Copy this into a cell (one click selects it)", code=True)]
        if snippet.note:
            blocks.append(_Note(snippet.note))
        blocks.append(_Note("The explorer's .picked holds the same paths, in the order they were ticked."))
        self._captured.append(blocks)

    def _open_link(self, label: str, tip: str) -> str:
        """
        ↗ Open in new tab: a link to the chosen file that looks like the buttons beside it, presigned (for an
        hour) to show the file in the tab rather than save it. Empty when it can't be signed (no credentials).
        """
        try:
            url = self.core.presigned_url(self.selected, inline=True)
        except (ClientError, BotoCoreError):
            return ""
        esc = html.escape
        return (f'<a class="s3x-act" href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
                f'title="{esc(tip)}">{esc(label)}</a>')

    def _draw_expand(self, show: bool = True) -> None:
        """
        ▾ Expand all, at the end of the buttons above the right pane (beside ✕), while the pane shows a JSON file
        with something collapsed in it, or with every part expanded by it.
        """
        acts = [b for action, b in self._act_buttons.items() if action not in _TAIL]
        tail = [self._act_buttons[action] for action in _TAIL if action in self._act_buttons]
        expand = show and any(tree.folded for tree in self._trees())
        self._expand_btn._dom_classes = ("s3x-act", "s3x-expand") + (("s3x-on",) if self._expand_all else ())
        tail = [self._expand_btn] * expand + tail
        for b in tail:
            b.remove_class("s3x-push")
        if tail:
            tail[0].add_class("s3x-push")
        self._actions.children = tuple(acts + tail)
        self._expand_btn.tooltip = (
            "Collapse the JSON back to its first levels, here and in the next files you open" if self._expand_all else
            "Expand every object and array in this JSON, and in the next files you open, instead of clicking them "
            "one by one (long strings stay collapsed: click one to read it)")

    def _trees(self) -> list[Any]:
        """
        The JSON trees in the report on the right (a .json file's preview).
        """
        return [block for block in self.shown if isinstance(block, _JsonTree)]

    def _mark_actions(self) -> None:
        for action, b in getattr(self, "_act_buttons", {}).items():
            if action not in _UNMARKED:
                lit = action == self._action or (action == "more" and self._more_open)
                b._dom_classes = ("s3x-act", "s3x-on") if lit else ("s3x-act",)

    def _on_action(self, action: str) -> None:
        uri = self.selected or self.nav.location
        if action == "close":
            self._show_folder(self._source())
        elif action == "preview":
            self._report("preview", "preview", uri)
        elif action == "head":
            self._report("head", "head", uri)
        elif action == "document" and _extension(Entry("file", *parse_location(uri)).name).split(".")[0] == "pdf":
            self._show_pages(1)
        elif action == "text":
            self._show_pages(1, "text")
        elif action == "document":
            self._report("document", "document", uri)
        elif action == "download":
            self._report("download", "download", uri, cache=False)
        elif action == "summary":
            self._report("summary", "summary", uri)
        elif action == "bucket_info":
            self._report("bucket_info", "bucket_info", parse_location(uri)[0])
        elif action == "overview":
            self._report("overview", "overview")
        elif action == "zip":
            self._report("zip", self._zip, uri, cache=False)
        elif action in _STEPS:
            self._step(_STEPS[action])
        elif action == "code":
            self._report("code", self._use_in_python, uri)
        elif action == "history":
            self._report("history", "history", uri)
        elif action == "more":
            self._more_open = not self._more_open
            self._draw_actions()
            self._draw_expand()
        elif action in _FOLDER_REPORTS:
            self._report(action, self._folder_report, action, uri)

    def _folder_report(self, action: str, uri: str) -> None:
        """
        One of More ▾'s reports on the folder: the pane's S3View command, with the options the explorer gives it.
        """
        command, options = _FOLDER_REPORTS[action]
        getattr(self._pane, command)(uri, **options)

    # ------------------------------------------------------------------ a folder as a .zip, and ⚙ Settings

    def _zip_limit(self) -> str:
        """
        The zip limits in words: '100.0 MB and 10,000 files'.
        """
        try:
            size = human_size(parse_size(self.zip_max_size))
        except (TypeError, ValueError):
            size = str(self.zip_max_size)
        return f"{size} and {self.zip_max_files:,} files"

    def _zip(self, target: str | list[Any], path: str = "") -> None:
        """
        ⬇ Download .zip, for a folder (`target` is its uri) or what's selected (a list, and the path it goes to):
        s3's download_zip with the limits from ⚙ Settings. It checks the size, file count, disk space, memory and
        read access first, and writes nothing when one fails.
        """
        self._pane.download_zip(target, os.path.normpath(path) if path else None,  # None: in the downloads folder
                                max_size=self.zip_max_size, max_files=self.zip_max_files)
        report = self._captured[-1] if self._captured else []
        refused = any(card[:2] == ("Can download", "no") for block in report if isinstance(block, _Cards)
                      for card in block.items)
        if refused:  # after the line that says why
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, _Note)), len(report))
            fix = ("⚙ at the top right raises them" if isinstance(target, str) else
                   "untick some files, or raise the limits with ⚙ at the top right")
            again = "⬇ Download .zip" if isinstance(target, str) else "⬇ Download selected"
            report.insert(at, _Note(f"If it's over a limit, {fix} (now {self._zip_limit()}); then click "
                                            f"{again} again."))

    # ------------------------------------------------------------------ a PDF, page by page: as it looks, or its text

    def _show_pages(self, first: int, action: str = "document") -> None:
        """
        Read all (action 'document': the pages as they look) or Text ('text': their words laid out to read), for
        a PDF, from page `first` on; the pager under the report moves on.
        """
        self._first_page = first
        self._report(action, self._read_pdf if action == "document" else self._read_text, self.selected, variant=first)

    def _read_pdf(self, uri: str) -> None:
        """
        The pages of a PDF drawn as they print, one report's worth (s3's _MAX_PICTURES, 20) from _first_page on,
        each with its text folded underneath.
        """
        count = self._page_count(uri)
        if not count:  # a broken or locked PDF, or no pypdf: document() says what's wrong
            self._pane.document(uri, pictures=True)
            return
        first, last = self._page_span(count)
        self._pane.document(uri, pages=range(first, last + 1), pictures=True)
        report = self._captured[-1] if self._captured else []
        if report and isinstance(report[0], _Title):
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, _Cards)), 1)
            span = f"Pages {first}–{last} of {count:,}; the buttons at the end show the others. " if count > last - first + 1 else ""
            report.insert(at, _Note(f"{span}Click a page to see it full size; ‹ › there step through the pages."))

    def _read_text(self, uri: str) -> None:
        """
        Text, for a PDF: the words of one report's worth of pages (_TEXT_PAGES, 50) from _first_page on, as
        s3's document(pictures=False) lays them out: headings, paragraphs and lists, a line where each page starts,
        and no running headers or footers.
        """
        count = self._page_count(uri)
        if not count:  # a broken or locked PDF, or no pypdf: document() says what's wrong
            self._pane.document(uri, pictures=False)
            return
        first, last = self._page_span(count)
        self._pane.document(uri, pages=range(first, last + 1), pictures=False)
        report = self._captured[-1] if self._captured else []
        if report and isinstance(report[0], _Title) and count > last - first + 1:
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, _Cards)), 1)
            report.insert(at, _Note(f"Pages {first}–{last} of {count:,}; the buttons at the end show the "
                                            "others, and 📖 Read all shows the pages as they look."))

    def _page_count(self, uri: str) -> int:
        """
        How many pages a PDF has (0 when it can't be read), once per file version.
        """
        entry = self._entry()
        key = (uri, entry.etag if entry else "")
        if key not in self._page_counts:
            try:
                self._page_counts[key] = self.core.read_pdf(uri, pages=[1]).page_count or 0
            except (ClientError, BotoCoreError, *_READ_ERRORS):
                self._page_counts[key] = 0
        return self._page_counts[key]

    def _per_report(self) -> int:
        """
        PDF pages one report shows: Read all draws s3's _MAX_PICTURES (20), Text shows _TEXT_PAGES (50).
        """
        return _TEXT_PAGES if self._action == "text" else _MAX_PICTURES

    def _page_span(self, count: int) -> tuple[int, int]:
        """
        The first and last page "Read all" or "Text" shows now.
        """
        first = max(1, min(self._first_page, count))
        return first, min(first + self._per_report() - 1, count)

    def _draw_pager(self, show: bool = True) -> None:
        """
        The buttons under a PDF's pages (or their text) that show the pages before and after them.
        """
        w, entry = self._widgets, self._entry()
        count = self._page_counts.get((self.selected, entry.etag if entry else ""), 0)
        per, action = self._per_report(), self._action
        if not (show and action in ("document", "text") and count > per):
            self._pager.layout.display = "none"
            return
        first, last = self._page_span(count)
        old, children = self._pager.children, [w.Label(f"Pages {first}–{last} of {count:,}")]
        for start, label, tip in ((max(1, first - per), f"‹ Pages {max(1, first - per)}–{first - 1}", "The pages before"),
                                  (last + 1, f"Pages {last + 1}–{min(last + per, count)} ›", "The pages after")):
            if 1 <= start <= count and start != first:
                b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
                b.add_class("s3x-act")
                b.on_click(lambda _, start=start: self._guard(lambda: self._show_pages(start, action)))
                children.append(b)
        self._pager.children = children
        self._pager.layout.display = None
        for widget in old:
            widget.close()

    def _on_expand(self) -> None:
        """
        ▾ Expand all: every object and array of the JSON on the right, and of the JSON files opened after it,
        until it's clicked again. The report is drawn again in place, so the pane stays where it was scrolled to.
        """
        self._expand_all = not self._expand_all
        for tree in self._trees():
            tree.unfold("all" if self._expand_all else "start")
        self._content.value = "".join(_render_html(blocks, self._pane.max_rows, theme=_theme_name()) for blocks in self._reports)
        self._draw_expand()
