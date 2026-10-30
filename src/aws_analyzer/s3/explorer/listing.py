"""
The list on the left: its rows' tooltips, the clicks it drops, the type chips, and the pane's mixin.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.fmt import _fmt_dt, _plural, human_age, human_size
from ..._kit.render import _file_icon
from ..._kit.window import _running_loop
from ..style import _render_html

from .navigator import (
    _KINDS, Entry, Folder, FolderStats, _parent_prefix, _type_word, breadcrumbs, count_types, entry_icon,
    filter_entries, folder_stats, parse_filter, parse_location, sort_entries,
)
from .frame import _CHECK, _Row


def _tip_time(moment: datetime | None) -> str:
    return _fmt_dt(moment, zone=True) if moment else ""


_CLICK_GRACE = 0.35  # seconds after the rows change during which a click is ignored (it was aimed at the old rows)


_TYPE_CHIPS = 6  # file types shown as chips over the list before "+N more"


class _ListPane:
    """
    The list on the left of S3Explorer: the path bar, the folder's entries (the rest listed in the
    background), the search box, its chips and filters, the sort, the pages and the status line.
    """

    def _entry(self) -> Entry | None:
        """
        The file shown on the right, as listed.
        """
        return next((e for e in self._source().entries if e.uri == self.selected), None) if self.selected else None

    def _source(self) -> Folder:
        """
        What the list is drawn from: this folder's first level, or with "Include subfolders" everything below it.
        The first time, a notebook lists its first page (_follow lists the rest in the background) and anywhere else
        its first 10,000 files, with the count so far in the status bar.
        """
        if self._deep and parse_location(self.nav.location)[0]:
            first = self.nav.page_size if self._background() else self.nav.deep_limit
            return self.nav._deep(None, first, self._listing)
        return self.nav.folder()

    def _listing(self, count: int) -> None:
        self._busy(f"Listing everything below this folder… {_plural(count, 'file')} so far" if self._deep else
                   f"Listing this folder… {count:,} so far")

    # ------------------------------------------------------------------ listing the rest of a big folder

    def _background(self) -> bool:
        """
        Whether listings go on in the background: in a notebook, where the kernel's event loop runs the clicks.
        """
        return self._widgets is not None and _running_loop() is not None

    def _goal(self, folder: Folder) -> int:
        """
        How much of a folder the explorer lists before it stops and offers "Load more from S3".
        """
        return self.nav.deep_limit if folder.deep else self.nav.list_limit

    def _follow(self, folder: Folder) -> None:
        """
        The list now shows `folder`: list the rest of it, up to its first list_limit entries (deep_limit files
        with Include subfolders), so the search, the counts and the sort cover the whole folder and not only the
        first page S3 returned. A listing of another folder stops.
        """
        if self._lister is not None and self._lister is not folder:
            self._stop_listing()
        keys = self._goal(folder) - folder.listed
        if folder.more and keys > 0:
            self._list_more(folder, keys)

    def _list_more(self, folder: Folder, keys: int) -> None:
        """
        List up to `keys` more of a folder. In a notebook it's a page at a time on a worker thread, so the list can
        be searched and clicked meanwhile and each page updates it (_list_later); elsewhere it's right away.
        """
        if self._lister is folder:
            return
        if not self._background():
            self._busy("Listing…")
            self.nav._list(folder, keys, self._listing)
            return
        self._stop_listing()
        self._lister, self._list_error = folder, ()
        self._list_task = _running_loop().create_task(self._list_later(folder, keys))

    def _stop_listing(self) -> None:
        if self._lister is not None:
            self._lister = None
            self._list_task.cancel()

    async def _list_later(self, folder: Folder, keys: int) -> None:
        """
        List a folder a page at a time: each request on a worker thread, and each page added on the kernel's event
        loop, where the clicks run, so nothing changes under them. It stops after `keys` entries, when S3 has no
        more, or when the list moves to another folder.
        """
        try:
            while keys > 0 and folder.more and self._lister is folder:
                token = folder.token
                page = await asyncio.wrap_future(self._threads().submit(
                    self.nav._request, folder, token, min(self.nav.page_size, keys)))
                if self._lister is not folder:
                    return
                if folder.token == token:  # else this page was listed meanwhile: ask for the one after it
                    keys -= self.nav._add(folder, page)
                    self._guard(lambda: self._listed(folder))
        except (ClientError, BotoCoreError) as exc:
            if self._lister is folder:
                code = exc.response.get("Error", {}).get("Code", "") if isinstance(exc, ClientError) else ""
                self._list_error = (folder, f"{code or type(exc).__name__}: {exc}")
        except Exception as exc:  # a bug: still say so under the list, and not in the kernel's log
            if self._lister is folder:
                self._list_error = (folder, f"something went wrong, {type(exc).__name__}: {exc}")
        finally:
            if self._lister is folder:
                self._lister = None
                self._guard(lambda: self._listed(folder))

    def _listed(self, folder: Folder) -> None:
        """
        After a page of a background listing (and once it's done): the counts, the chips, the list, the status bar
        and the folder's overview on the right again, if they still show that folder. The overview changes in place,
        where the reader has scrolled it to.
        """
        self._save_look(folder)
        if folder is not self._folder or self._widgets is None:
            return
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if self._overview is folder:
            self.shown = self._folder_blocks(folder)
            self._content.value = _render_html(self.shown, self._pane.max_rows)

    def _shown_entries(self, folder: Folder) -> list[Entry]:
        """
        The list's entries: the folder's, narrowed by the search box and All / Folders / Files, in the order of the
        column clicked.
        """
        kind = _KINDS[self._kind] if parse_location(folder.uri)[0] else "all"
        wanted = parse_filter(self._query)
        return [e for e in self._ordered(folder)
                if wanted.matches(e) and (kind == "all" or e.is_folder == (kind == "folders"))]

    def _ordered(self, folder: Folder) -> list[Entry]:
        """
        The folder's entries in the order of the column clicked, kept until the folder, the sort or what's listed
        changes, so a key typed in the search box only filters them. With "Include subfolders", Name sorts by path, so
        each folder comes before what's in it, and Size and Modified put the files first: the biggest or newest
        anywhere below.
        """
        key = (len(folder.entries), self._sort, self._descending)
        if not self._order or self._order[0] is not folder or self._order[1] != key:
            if folder.deep and self._sort == "name":
                entries = sort_entries(folder.entries, "path", self._descending)
            else:
                entries = sort_entries(folder.entries, self._sort, self._descending)
                if folder.deep:
                    entries = [e for e in entries if not e.is_folder] + [e for e in entries if e.is_folder]
            self._order = (folder, key, entries)
        return self._order[2]

    def _stats(self, folder: Folder) -> FolderStats:
        """
        folder_stats of what's listed, kept until more is listed (the status bar shows them after each key).
        """
        if not self._counted or self._counted[0] is not folder or self._counted[1] != len(folder.entries):
            self._counted = (folder, len(folder.entries), folder_stats(folder.entries))
        return self._counted[2]

    def _draw_crumbs(self) -> None:
        w = self._widgets
        crumbs = breadcrumbs(self.nav.location)
        if len(crumbs) > 6:  # All buckets › bucket › … › the last three
            crumbs = crumbs[:2] + [("…", crumbs[-4][1])] + crumbs[-3:]
        old = self._crumbs.children
        children: list[Any] = []
        for i, (label, uri) in enumerate(crumbs):
            last = i == len(crumbs) - 1
            b = w.Button(description=label, tooltip=("Show what's in this folder" if last else f"Open {uri}")
                         if uri else ("Show your buckets" if last else "Open your list of buckets"),
                         layout=w.Layout(width="auto"))
            b.add_class("s3x-crumb")
            if last:
                b.add_class("s3x-here")
            b.on_click(lambda _, uri=uri, last=last: self._guard(
                lambda: self._show_folder(self._source()) if last else self.open(uri)))
            children.append(b)
            if not last:
                children.append(w.Label("›"))
                for name in ("s3x-sep", "s3x-ic", "s3x-i-chevron"):
                    children[-1].add_class(name)
        self._crumbs.children = children
        for widget in old:
            widget.close()
        self._back_btn.disabled = not self.nav.can_back
        self._fwd_btn.disabled = not self.nav.can_forward
        self._up_btn.disabled = not self.nav.location
        bucket = parse_location(self.nav.location)[0]
        self._cols["size"].layout.display = None if bucket else "none"  # buckets have no size: their names get the room
        self._draw_side()
        self._draw_header()

    def _draw_header(self) -> None:
        bucket = parse_location(self.nav.location)[0]
        labels = {"name": "Name", "size": "Size", "modified": "Modified" if bucket else "Created"}
        for column, b in self._cols.items():
            on = column == self._sort
            b.description = labels[column] + ((" ↓" if self._descending else " ↑") if on else "")
            b._dom_classes = ("s3x-col", "s3x-namecol" if column == "name" else "s3x-num") + (
                ("s3x-on",) if on else ())

    def _draw_side(self) -> None:
        """
        The list's state, for the style: on the list of buckets (no sizes, nothing to select), and while
        something is selected (every row shows its checkbox).
        """
        bucket = parse_location(self.nav.location)[0]
        self._side._dom_classes = ("s3x-side",) + (() if bucket else ("s3x-buckets",)) + (
            ("s3x-picking",) if self._picked else ())

    def _draw_filters(self, folder: Folder) -> None:
        """
        Above the list: the search box, All / Folders / Files with how many of each match it, Include subfolders,
        and a chip for each file type here (the most common first), lit when the search asks for that type.
        """
        w = self._widgets
        bucket = parse_location(self.nav.location)[0]
        self._filter.placeholder = "Search: a name, .csv, *.parquet, part-0*" if bucket else "Search your buckets"
        self._clear_btn.layout.display = None if self._query.strip() else "none"
        self._kinds.layout.display = self._types.layout.display = None if bucket else "none"
        if not bucket:
            return
        wanted, plus = parse_filter(self._query), "+" if folder.more else ""
        named = [e for e in folder.entries if wanted.named(e)]
        matching = [e for e in named if wanted.typed(e)]
        folders = sum(e.is_folder for e in matching)
        for kind, label, count in (("all", "All", len(matching)), ("folders", "Folders", folders),
                                   ("files", "Files", len(matching) - folders)):
            b = self._kind_btns[kind]
            b.description = f"{label} {count:,}{plus}"
            b._dom_classes = ("s3x-seg", "s3x-on") if kind == self._kind else ("s3x-seg",)
        self._deep_btn._dom_classes = ("s3x-chip", "s3x-deep", "s3x-ic", "s3x-i-below") + (
            ("s3x-on",) if self._deep else ())
        self._deep_btn.tooltip = (
            "Showing everything below this folder; click to show only its own folders and files" if self._deep else
            f"List everything below this folder (the first {self.nav.deep_limit:,} files), not only its first level, "
            "to search all of it")

        types = count_types(named)
        shown = types
        if not self._all_types and len(types) > _TYPE_CHIPS + 1:
            shown = [t for i, t in enumerate(types) if i < _TYPE_CHIPS or t[0] in wanted.types]
        while len(self._chips) < len(shown):
            chip = w.Button(layout=w.Layout(width="auto"))
            chip.on_click(lambda _, i=len(self._chips): self._guard(lambda: self._toggle_type(self._chip_types[i])))
            self._chips.append(chip)
        self._chip_types = [kind for kind, _, _ in shown]
        for chip, (kind, files, size) in zip(self._chips, shown):
            on = kind in wanted.types
            with chip.hold_sync():
                chip.description = f"{_file_icon('x.' + kind)} .{kind} {files:,}"
                chip.tooltip = (f"{_plural(files, f'.{kind} file')}, {human_size(size)}"
                                f"{' so far' if folder.more else ''}: " + (
                                    "click to show every type again" if on else
                                    "click to show only these (click another type to add it)"))
                chip._dom_classes = ("s3x-chip", "s3x-on") if on else ("s3x-chip",)
        children = self._chips[: len(shown)]
        hidden = len(types) - len(shown)
        if hidden or (self._all_types and len(types) > _TYPE_CHIPS + 1):
            self._more_types.description = f"+{hidden} more" if hidden else "Fewer"
            self._more_types.tooltip = "Show every file type here" if hidden else "Show only the most common types"
            children.append(self._more_types)
        self._types.children = tuple(children)
        if self._kind == "folders" or (len(types) < 2 and not wanted.types):  # one type: its chip would change nothing
            self._types.layout.display = "none"

    def _draw_rows(self, folder: Folder) -> None:
        """
        Fill the list with a page of the folder's entries, filtered and sorted, reusing row widgets so redraws are
        quick; and the bar under it, which says which rows these are and moves to the others.
        """
        w = self._widgets
        entries = self._shown_entries(folder)
        self._visible, self._folder = entries, folder
        if self._offset >= len(entries):  # fewer match now: the last page
            self._offset = max(0, len(entries) - 1) // self.page_size * self.page_size
        shown = entries[self._offset: self._offset + self.page_size]
        while len(self._pool) < len(shown):
            self._pool.append(_Row(w, self._on_row, self._on_check))
        base = parse_location(folder.uri)[1] if folder.deep else None
        for row, entry in zip(self._pool, shown):
            self._fill(row, entry, base)
        self._rows_box.children = tuple(row.box for row in self._pool[: len(shown)])
        key = (folder.uri, tuple(e.uri for e in shown))
        if key[1] and key != self._rows_key[:2]:
            self._rows_at = time.monotonic()
        self._rows_key = key
        self._draw_pages(folder, len(entries))
        self._draw_foot(folder, entries, len(shown))
        self._draw_check_all()

    def _draw_pages(self, folder: Folder, total: int) -> None:
        """
        The bar under a list longer than a page: which rows these are, of how many, and « ‹ › » for the first,
        previous, next and last page.
        """
        size = self.page_size
        self._pages.layout.display = None if total > size else "none"
        if total <= size:
            return
        first, last = self._offset + 1, min(self._offset + size, total)
        page, pages = self._offset // size + 1, -(-total // size)
        plus = "+" if folder.more else ""
        self._range.value = (f'<span title="Page {page:,} of {pages:,}{plus}"><b>{first:,}–{last:,}</b> of '
                             f'{total:,}{plus}</span>')
        for name, b in self._page_btns.items():
            b.disabled = first == 1 if name in ("first", "previous") else last >= total

    def _fill(self, row: _Row, entry: Entry, base: str | None = None) -> None:
        """
        Show an entry in a row. In a list of everything below a folder (`base`, its prefix), the folder the entry
        is in goes under its name.
        """
        row.entry = entry
        if entry.kind == "file":
            tip = f"{entry.key}\n{human_size(entry.size)} · modified {_tip_time(entry.modified)}"
            tip += f"\n{entry.storage_class}: restore it before it can be opened" if entry.archived else (
                f" · {entry.storage_class}" if entry.storage_class not in ("", "STANDARD") else "")
            size, age = human_size(entry.size), human_age(entry.modified) if entry.modified else ""
        elif entry.kind == "folder":
            tip, size, age = f"Open {entry.key}", "", ""
        else:
            tip = f"Open the bucket {entry.bucket}" + (f"\ncreated {_tip_time(entry.modified)}" if entry.modified else "")
            size, age = "", human_age(entry.modified) if entry.modified else ""
        name = entry.name or "(no name)"
        with row.button.hold_sync():
            row.button.description = f"{entry_icon(entry)}  {name}{'  ❄' if entry.archived else ''}"
            row.button.tooltip = tip
            row.button._dom_classes = self._row_classes(entry)
        row.check._dom_classes = _CHECK + (("s3x-on",) if entry.uri in self._picked else ())
        row.size.value, row.age.value = size, age
        is_new = self._is_new(entry)
        row.age._dom_classes = ("s3x-new",) if is_new else ()
        if is_new:
            row.button.tooltip = f"{tip}\nnew or changed since your last visit"
        if base is not None:
            where = _parent_prefix(entry.key[:-1] if entry.kind == "folder" else entry.key)[len(base):]
            row.where.value = where.rstrip("/") or "in this folder"
        row.where.layout.display = None if base is not None else "none"
        row.box._dom_classes = ("s3x-r", "s3x-r2") if base is not None else ("s3x-r",)

    def _row_classes(self, entry: Entry) -> tuple[str, ...]:
        """
        A row's button: lit for the file shown on the right, tinted when it's selected.
        """
        return ("s3x-row",) + (("s3x-on",) if entry.uri == self.selected else ()) + (
            ("s3x-picked",) if entry.uri in self._picked else ())

    def _mark_rows(self) -> None:
        if self._widgets is None:
            return
        for row in self._pool[: len(self._rows_box.children)]:
            if row.entry is not None:
                row.button._dom_classes = self._row_classes(row.entry)
                row.check._dom_classes = _CHECK + (("s3x-on",) if row.entry.uri in self._picked else ())

    def _draw_foot(self, folder: Folder, entries: list[Entry], shown: int) -> None:
        """
        At the end of the list: Load more from S3 and Look up for a folder bigger than what's listed, or why the
        list is empty and what to do.
        """
        text, esc = self._query.strip(), html.escape
        bucket = parse_location(folder.uri)[0]
        busy = self._lister is folder  # listing the rest in the background: it'll be in the list in a moment
        files = sum(not e.is_folder for e in folder.entries) if folder.deep else 0
        count = f"{_plural(files, 'file')} below" if folder.deep else f"{len(folder.entries):,} entries"
        if folder.deep:
            self._load_btn.description = f"Load more from S3 (the first {files:,} files below are listed)"
        else:
            self._load_btn.description = f"Load more from S3 (the first {len(folder.entries):,} are listed)"
        self._load_btn.tooltip = f"List the next {self._goal(folder):,}"
        last_page = self._offset + shown >= len(entries)
        self._load_btn.layout.display = None if folder.more and not busy and last_page else "none"
        wanted = parse_filter(text)
        lookup = (folder.more and not busy and not folder.deep and len(wanted.words) == 1 and not wanted.patterns
                  and not wanted.types and "/" not in text)
        self._lookup_btn.description = f"Look up names starting with “{text}” in S3"
        self._lookup_btn.layout.display = None if lookup else "none"
        note, fix = "", None
        among = f" yet ({count} listed so far)" if busy else f" among the first {count}" if folder.more else ""
        if folder.error:
            note = "Couldn't list this folder; the note on the right says why."
        elif self._list_error and self._list_error[0] is folder and folder.more and not busy and last_page:
            note = f"S3 stopped listing this folder ({esc(self._list_error[1])}). Load more from S3 tries again."
        elif not folder.entries:
            note = ("Nothing below this folder." if folder.deep else "This folder is empty.") if bucket else "No buckets."
        elif not entries:
            others = filter_entries(folder.entries, text)  # of either kind
            if bucket and self._kind != "all" and others:
                note = f"No {self._kind} {f'match “{esc(text)}”' if text else 'here'}{among}."
                other = "file" if self._kind == "folders" else "folder"
                fix = (f"Show the {_plural(len(others), other)}", lambda: self._on_kind("all"))
            else:
                note = f"Nothing here matches “{esc(text)}”{among}."
                if bucket and not folder.deep:
                    fix = ("Search the subfolders too", self._on_deep)
        elif last_page and folder.more and (text or self._kind != "all") and not busy:
            note = f"These are the matches {among.strip()}; this folder has more."
        if busy and not folder.error and not entries and folder.entries:
            note += " Still listing the rest…"
        self._fix = fix[1] if fix else (lambda: None)
        self._fix_btn.description = fix[0] if fix else ""
        self._fix_btn.layout.display = None if fix else "none"
        self._foot_note.value = f'<div class="s3x-empty">{note}</div>' if note else ""
        self._foot_note.layout.display = None if note else "none"

    def _draw_status(self, folder: Folder) -> None:
        """
        The bar at the bottom: what's listed (and how much of it matches the search), and where you are. While the
        rest of the folder is listed in the background, a spinner and "Listing…" in front.
        """
        if self._widgets is None:
            return
        esc = html.escape
        bucket = parse_location(folder.uri)[0]
        if folder.error:
            left = "Couldn't list this folder"
        elif not bucket:
            left = _plural(len(folder.entries), "bucket")
        else:
            stats = self._stats(folder)
            plus = "+" if folder.more else ""
            parts = [f"{stats.folders:,}{plus} folder{'' if stats.folders == 1 and not plus else 's'}"] if stats.folders else []
            if stats.files:
                parts += [f"{stats.files:,}{plus} file{'' if stats.files == 1 and not plus else 's'}",
                          human_size(stats.size) + (" so far" if plus else "")]
            left = ("Below this folder: " if folder.deep and parts else "") + (" · ".join(parts) or "Empty")
        text = self._query.strip()
        if not folder.error and (text or (bucket and self._kind != "all")):
            left += f" · {len(self._visible):,} match “{esc(text)}”" if text else f" · {self._kind} only"
            size = sum(e.size or 0 for e in self._visible if not e.is_folder)
            left += f" ({human_size(size)})" if size else ""
        here = self.selected or folder.uri
        right = f'<code title="Click to select, then copy">{esc(here)}</code>' if here else ""
        busy = self._lister is folder and not folder.error
        self._status.value = (f'<span class="s3x-at{" s3x-busy" if busy else ""}">{"Listing… " if busy else ""}{left}'
                              f'</span>{right}')

    # ------------------------------------------------------------------ events

    def _on_row(self, row: _Row) -> None:
        if row.entry is None or time.monotonic() - self._rows_at < _CLICK_GRACE:
            return  # a double click, or a click queued while the list was changing: it was meant for the old rows
        entry = row.entry
        self._guard(lambda: self.open(entry.uri) if entry.is_folder else self._click_file(entry))

    def _click_file(self, entry: Entry) -> None:
        if entry.uri == self.selected and self._action == "preview":
            return
        self._select(entry.uri)

    def _on_filter(self) -> None:
        self._query = self._filter.value
        self._refilter()

    def _set_query(self, text: str, renew: bool = False) -> None:
        """
        Put text in the search box (without it calling _on_filter too) and redraw the list for it.
        """
        self._query = text
        if self._widgets is not None:
            self._quiet = True
            self._filter.value = text
            self._quiet = False
        self._refilter(renew)

    def _refilter(self, renew: bool = False) -> None:
        """
        Redraw the list after the search, All / Folders / Files or Include subfolders changed (the text explorer
        prints it), from its first page. renew: start the list at the top.
        """
        self._offset = 0
        folder = self._source()
        if self._widgets is None:
            self._set_pane([self._folder_blocks(folder, listing=True)])
            return
        self._draw_filters(folder)
        self._draw_rows(folder)
        if renew:
            self._renew("left")
        self._draw_status(folder)

    def _on_kind(self, kind: str) -> None:
        self._kind = kind
        self._refilter(renew=True)

    def _on_deep(self) -> None:
        """
        Include subfolders: list everything below this folder (once; it's kept), or only its first level again.
        """
        self._deep, self._all_types = not self._deep, False
        if self._deep:
            self._busy("Listing everything below this folder…")
        self._follow(self._source())
        self._refilter(renew=True)
        if not self.selected:
            self._show_folder(self._source())

    def _toggle_type(self, kind: str) -> None:
        """
        A file type's chip: add '.csv' to the search, or take it out again. The search box stays the one place a
        filter is written down, so what a chip did can be read, changed or typed next time.
        """
        words = [word for word in re.split(r"[\s,]+", self._query.strip()) if word]
        kept = [word for word in words if _type_word(word) != kind]
        if len(kept) == len(words):
            kept.append(f".{kind}")
        self._set_query(" ".join(kept))

    def _on_more_types(self) -> None:
        self._all_types = not self._all_types
        self._draw_filters(self._source())

    def _on_sort(self, by: str) -> None:
        if by == self._sort:
            self._descending = not self._descending
        else:
            self._sort, self._descending = by, by != "name"  # biggest / newest first
        self._offset = 0
        self._draw_header()
        self._draw_rows(self._source())
        self._renew("left")

    def _on_page(self, to: str) -> None:
        """
        « ‹ › » under the list: its first, previous, next or last page.
        """
        size, total = self.page_size, len(self._visible)
        last = max(0, total - 1) // size * size
        self._offset = {"first": 0, "previous": max(0, self._offset - size), "next": min(last, self._offset + size),
                        "last": last}[to]
        self._draw_rows(self._source())
        self._renew("left")

    def _on_load(self) -> None:
        """
        Load more from S3, at the end of a folder bigger than what's listed: the next list_limit entries (deep_limit
        files with Include subfolders), in the background in a notebook.
        """
        folder = self._source()
        self._list_more(folder, self._goal(folder))
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if not self.selected:
            self._show_folder(folder)

    def _on_lookup(self) -> None:
        text = self._query.strip()
        self._busy("Looking up…")
        added = self.nav.lookup(text)
        folder = self.nav.folder()
        self._offset = 0
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if not added:
            self._foot_note.value = (f'<div class="s3x-empty">S3 has nothing else starting with '
                                     f'“{html.escape(text)}”.</div>')
            self._foot_note.layout.display = None

    def _toggle_path(self) -> None:
        editing = self._path.layout.display != "none"
        if not editing:
            self._path.value = self.nav.location or "s3://"
        self._path.layout.display = "none" if editing else None
        self._crumbs.layout.display = None if editing else "none"
        self._edit_btn.description = "✎" if editing else "✕"
        self._edit_btn.tooltip = "Type or paste a path" if editing else "Cancel"
        self._edit_btn._dom_classes = ("s3x-nav", "s3x-ic", "s3x-i-edit" if editing else "s3x-i-close")
        self._draw_recent()

    def _on_path(self) -> None:
        value = self._path.value.strip()
        self._toggle_path()
        if value and value not in (self.nav.location, "s3://"):
            self.open(value)
