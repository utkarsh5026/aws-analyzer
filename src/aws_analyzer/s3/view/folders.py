"""
The Explore a folder commands, and the tables file_details() draws.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.fmt import (
    Stat, _duration, _fmt_dt, _in_zone, _plural, _share, _tz_label, human_age, human_money, human_size,
)
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import GB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..models import FileDetailsReport, ObjectInfo
from ..analysis.summary import summary_findings
from ..analysis.details import (
    _KIND_NAMES, _TABLE_KINDS, FileDetails, _clock, _count_text, _sheets_text, _short_count, file_details_findings,
)
from .cells import _FORMAT_LABELS, _folder_label


def _stat_table(
    title: str,
    label: str,
    stats: dict[str, Stat],
    total_count: int,
    total_size: int,
    *,
    by: str = "size",
    name: Callable[[str], str] = str,
    costs: dict[str, float | None] | None = None,
) -> _Table:
    rows = [
        [name(key), f"{st.count:,}", human_size(st.size)]
        + ([human_money(costs.get(key))] if costs else [])
        for key, st in stats.items()
    ]
    bars = [
        _share(st.size, total_size) if by == "size" else _share(st.count, total_count)
        for st in stats.values()
    ]
    return _Table(
        [label, "Objects", "Size"] + (["Est. $/month"] if costs else []),
        rows,
        title=title,
        bars=bars,
        bar_label="% of size" if by == "size" else "% of objects",
    )


def _objects_table(title: str, objects: list[ObjectInfo], base: str = "") -> _Table:
    return _Table(
        ["Key", "Size", f"Last modified ({_tz_label()})", "Age", "Storage class"],
        [
            [
                relative_key(o.key, base),
                human_size(o.size),
                _fmt_dt(o.last_modified),
                human_age(o.last_modified),
                o.storage_class,
            ]
            for o in objects
        ],
        title=title,
        path_cols=(0,),
    )


def _file_steps(bucket: str, objects: list[ObjectInfo]) -> list[tuple[str, str]]:
    """
    Next steps after a list of files: look at the first one.
    """
    if not objects:
        return []
    uri = s3_uri(bucket, objects[0].key)
    return [
        (_call("preview", uri), "what's inside the first one"),
        (_call("head", uri), "all its metadata"),
    ]


_DETAIL_TABLES: list[tuple[str, tuple[str, ...], list[tuple[str, str]]]] = [  # file_details(): one table per kind
    ("PDFs", ("pdf",), [("Pages", "pages"), ("Text", "text"), ("Page size", "page_size"), ("Title", "title"),
                        ("Author", "author"), ("Made with", "made_with"), ("Created", "created")]),
    ("Word documents (pages as Word last saved them)", ("docx",),
     [("Words", "words"), ("Pages", "pages"), ("Headings", "headings"), ("Tables", "tables"),
      ("Pictures", "pictures"), ("Title", "title"), ("Author", "author"), ("Last saved", "modified")]),
    ("PowerPoint decks", ("pptx",), [("Slides", "slides"), ("Words", "words"), ("With notes", "notes"),
                                     ("Tables", "tables"), ("Pictures", "pictures"), ("Title", "title"),
                                     ("Author", "author")]),
    ("Excel workbooks (rows × columns, header row included)", ("excel",),
     [("Sheets", "sheet_count"), ("Sheet sizes", "sheets"), ("Title", "title"), ("Author", "author")]),
    ("Tables (≈ estimated from the first 256 KB, + at least)", _TABLE_KINDS,
     [("Format", "format"), ("Rows", "rows"), ("Columns", "column_count"), ("Column names", "columns"),
      ("Compression", "codec")]),
    ("Pictures", ("image",), [("Format", "kind"), ("Width × height", "pixels")]),
    ("Audio and video", ("audio", "video"), [("Format", "kind"), ("Length", "duration"), ("Width × height", "pixels"),
                                            ("Sample rate", "sample_rate"), ("Channels", "channels")]),
    ("Archives", ("zip", "tar"), [("Files inside", "files"), ("Unpacked", "unpacked")]),
    ("Models and arrays", ("safetensors", "npy", "npz", "torch", "pickle"),
     [("Format", "format"), ("Tensors / arrays", "tensors"), ("Parameters", "parameters"), ("Dtypes", "dtypes"),
      ("Shape", "shape")]),
    ("Notebooks", ("notebook",), [("Cells", "cells"), ("Code cells", "code_cells"), ("Outputs", "outputs"),
                                  ("Kernel", "kernel")]),
    ("Text and JSON", ("text", "json"), [("Format", "format"), ("Lines", "lines"), ("Keys", "keys")]),
    ("Other files", (), [("Format", "format")]),
]


def _details_table_of(d: FileDetails) -> str:
    """
    The title of the file_details() table a file goes in; 'Not read' when there's nothing to show.
    """
    if d.skipped or (d.error and not d.facts):
        return "Not read"
    if d.format == "json" and "rows" not in d.facts:
        return "Text and JSON"
    return next((title for title, kinds, _ in _DETAIL_TABLES if d.format in kinds), "Other files")


def _fact_cell(d: FileDetails, key: str) -> Any:
    """
    One fact of a file as a table cell ('' when the file doesn't have it).
    """
    facts = d.facts
    if key == "format":
        label = _FORMAT_LABELS.get(d.format or "", d.format) or "unknown"
        return label + (f" ({d.compression})" if d.compression else "")
    if key == "text":
        if facts.get("locked"):
            return _Tone("needs a password")
        if "text" not in facts:
            return ""
        return {True: "yes", False: _Tone("none: scanned?"), None: "not checked"}[facts["text"]]
    if key == "pixels":
        return f"{facts['width']:,} × {facts['height']:,}" if facts.get("width") else ""
    if key in ("column_count", "sheet_count"):
        value = facts.get("columns" if key == "column_count" else "sheets")
        return f"{len(value):,}" if isinstance(value, list) else ""
    if key == "tensors" and "tensors" not in facts:
        key = "arrays"
    if key == "dtypes" and "dtypes" not in facts:
        key = "dtype"
    value = facts.get(key)
    if value is None or value == "":
        return ""
    if key == "columns":
        names = [str(c) for c in value[:6]]
        return ", ".join(names) + (f" +{len(value) - 6:,} more" if len(value) > 6 else "")
    if key == "keys":
        return f"{len(value):,}: " + ", ".join(map(str, value[:5])) + (" …" if len(value) > 5 else "")
    if key == "sheets":
        return _sheets_text(value, 4)
    if key == "unpacked":
        return human_size(value) + ("+" if facts.get("unpacked_estimate") else "")
    if key == "parameters":
        return _short_count(value) + ("+" if facts.get("parameters_estimate") else "")
    if key == "duration":
        return _clock(value)
    if key == "sample_rate":
        return f"{value / 1000:g} kHz"
    if key == "channels":
        return {1: "mono", 2: "stereo"}.get(value, f"{value:,}")
    if isinstance(value, datetime):
        return (_in_zone(value) if value.tzinfo else value).strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return _count_text(facts, key)
    if isinstance(value, (list, tuple)) and key != "shape":
        return ", ".join(map(str, value))
    return str(value)


def _details_note(d: FileDetails) -> str:
    """
    What's worth knowing about a file beyond its facts: why it couldn't be read, or what it really is.
    """
    if d.error:
        return f"Couldn't read: {d.error}"
    if d.named:
        start = d.facts.get("starts_with")
        return (f"The name says {_KIND_NAMES.get(d.named, d.named)}, but it's "
                f"{_KIND_NAMES.get(d.format or '', 'binary data')}" + (f": starts {start!r}" if start else ""))
    return " ".join(d.notes)


def _details_tables(files: list[FileDetails], base: str) -> list[_Table]:
    """
    file_details(): a table per kind of file, only the columns some file has, and a Note column when needed.
    """
    groups: dict[str, list[FileDetails]] = defaultdict(list)
    for d in files:
        groups[_details_table_of(d)].append(d)
    tables = []
    for title, _, columns in _DETAIL_TABLES:
        group = groups.get(title)
        if not group:
            continue
        cells = [[_fact_cell(d, key) for _, key in columns] for d in group]
        shown = [i for i in range(len(columns)) if any(str(row[i]) for row in cells)]
        notes = [_details_note(d) for d in group]
        headers = ["File", "Size"] + [columns[i][0] for i in shown] + (["Note"] if any(notes) else [])
        rows = [
            [relative_key(d.key, base) or d.key, human_size(d.size)] + [row[i] for i in shown]
            + ([note] if any(notes) else [])
            for d, row, note in zip(group, cells, notes)
        ]
        tables.append(_Table(headers, rows, title=f"{title} · {len(group):,}", path_cols=(0,)))
    unread = groups.get("Not read")
    if unread:
        tables.append(_Table(
            ["File", "Size", "Format (by name)", "Why"],
            [[relative_key(d.key, base) or d.key, human_size(d.size), _fact_cell(d, "format"),
              d.summary.removeprefix("not read: ") if d.skipped else d.error]
             for d in unread],
            title=f"Not read · {len(unread):,}",
            path_cols=(0,),
        ))
    return tables


def _details_cards(report: FileDetailsReport) -> list[tuple[str, ...]]:
    """
    file_details(): file count, the totals of the most common kinds, what wasn't read, and what was read.
    """
    files = report.files

    def total(key: str, kinds: tuple[str, ...]) -> tuple[int, int, str]:
        """
        (files with the fact, sum, accuracy mark) of a fact over the files of these kinds.
        """
        having = [d for d in files if d.format in kinds and isinstance(d.facts.get(key), int)]
        hows = {d.facts.get(f"{key}_estimate") for d in having}
        mark = "estimate" if "estimate" in hows else "at least" if "at least" in hows else ""
        return len(having), sum(d.facts[key] for d in having), mark

    totals: list[tuple[int, tuple[str, str]]] = []
    for label, key, kinds in (("PDF pages", "pages", ("pdf",)), ("Slides", "slides", ("pptx",)),
                              ("Words in Word files", "words", ("docx",)), ("Table rows", "rows", _TABLE_KINDS),
                              ("Files in archives", "files", ("zip", "tar")),
                              ("Parameters", "parameters", ("safetensors", "npy", "npz"))):
        count, value, mark = total(key, kinds)
        if count:
            text = _short_count(value) if key == "parameters" else _count_text({key: value, f"{key}_estimate": mark}, key)
            totals.append((count, (label, text)))
    pictures = sum(d.format == "image" and "width" in d.facts for d in files)
    if pictures:
        totals.append((pictures, ("Pictures", f"{pictures:,}")))
    length = [d.facts["duration"] for d in files if d.format in ("audio", "video") and "duration" in d.facts]
    if length:
        totals.append((len(length), ("Audio / video length", _clock(sum(length)))))
    totals.sort(key=lambda item: -item[0])
    cards: list[tuple[str, ...]] = [("Files", f"{len(files):,}{'+' if report.truncated else ''}")]
    cards += [card for _, card in totals[:3]]
    scanned = sum(d.format == "pdf" and d.facts.get("text") is False for d in files)
    if scanned:
        cards.append(("PDFs without text", f"{scanned:,}", "warn"))
    unread = [d for d in files if d.skipped or (d.error and not d.facts)]
    if unread:
        warned = any(d.skipped == "max_read" or d.error for d in unread)
        cards.append(("Not read", f"{len(unread):,}") + (("warn",) if warned else ()))
    cards.append(("Read", human_size(report.bytes_read)))
    return cards


class _FolderCommands:
    """
    S3View's commands to explore a folder: ls(), tree(), summary(), find(), file_details(), largest(),
    newest(), oldest() and compare().
    """

    # ------------------------------------------------------------------ listing

    @_friendly_errors
    def ls(self, uri: str, *, limit: int = 500, details: bool = False) -> None:
        """
        Folders and files directly under a prefix (one level, fast). details=True also says what's inside each
        file: a PDF's pages, a table's rows and columns, a picture's size (reading a little of each file).

        details=True reads up to 1 GB in all, only the parts each format needs; file_details(uri) shows the same
        for every file below, with a table per kind of file (titles, authors, column names).
        """
        listing = self.core.ls(uri, limit=limit)
        report = None
        if details and listing.objects:
            with self._progress("Looking inside", unit="files") as tick:
                report = self.core.describe_objects(listing.objects, progress=tick)
            report.uri = listing.uri
        inside = {d.key: d.summary for d in report.files} if report else {}
        extra = [""] if report else []
        base = base_prefix(parse_s3_uri(listing.uri)[1])
        rows = [
            [f"📁 {relative_key(folder, base)}", "", "", ""] + extra
            for folder in listing.folders
        ]
        rows += [
            [
                relative_key(o.key, base),
                human_size(o.size),
                _fmt_dt(o.last_modified),
                o.storage_class,
            ]
            + ([inside.get(o.key, "")] if report else [])
            for o in listing.objects
        ]
        read = (
            f"looked inside {_plural(len(report.files), 'file')}: {human_size(report.bytes_read)} read in "
            f"{_plural(report.requests, 'request')}"
            if report
            else ""
        )
        blocks: list[Any] = [
            _Title(f"ls {listing.uri}", read),
            _Cards(
                [
                    ("Folders", f"{len(listing.folders):,}"),
                    ("Files", f"{len(listing.objects):,}"),
                    (
                        "Size of files here",
                        human_size(sum(o.size for o in listing.objects)),
                    ),
                ]
            ),
        ]
        if report:
            blocks.append(_Findings(file_details_findings(report)))
        if listing.truncated:
            blocks.append(
                _Note(
                    f"Showing the first {limit:,} entries; pass limit= for more.",
                    "warn",
                )
            )
        if not rows and not listing.uri.endswith("/") and self.core.exists(listing.uri):
            blocks.append(
                _Note(
                    "That's a file, not a folder: head(uri) shows its details, preview(uri) what's in it."
                )
            )
        elif not rows:
            blocks.append(
                _Note("Nothing here. Check the prefix (keys are case-sensitive).")
            )
        else:
            blocks.append(
                _Table(
                    ["Name", "Size", f"Last modified ({_tz_label()})", "Storage class"]
                    + (["What's inside"] if report else []),
                    rows,
                    max_rows=0,
                    path_cols=(0,),
                )
            )
            bucket = parse_s3_uri(listing.uri)[0]
            steps = [
                (
                    _call("summary", listing.uri),
                    "sizes, file types and cost of everything below",
                )
            ]
            if report:
                steps.insert(0, (_call("file_details", listing.uri), "every file below, a table per kind of file"))
            steps += (
                [(_call("ls", s3_uri(bucket, listing.folders[0])), "one level down")]
                if listing.folders
                else []
            )
            steps += (
                [
                    (
                        _call("preview", s3_uri(bucket, listing.objects[0].key)),
                        "what's inside the first file",
                    )
                ]
                if listing.objects
                else []
            )
            blocks.append(_Next(steps[:3]))
        self._show(blocks)

    @_friendly_errors
    def summary(
        self,
        uri: str,
        *,
        top_n: int = 10,
        folder_depth: int = 1,
        limit: int | None = None,
    ) -> None:
        """
        Full dashboard for a prefix: totals, estimated monthly cost, findings, folders, file types,
        storage classes, size and age distribution, largest objects. Lists every key once (use limit= on huge prefixes).
        """
        with self._progress() as tick:
            s = self.core.summarize(
                uri, top_n=top_n, folder_depth=folder_depth, limit=limit, progress=tick
            )
        base = base_prefix(parse_s3_uri(s.uri)[1])
        blocks: list[Any] = [
            _Title(
                f"Summary of {s.uri}",
                f"{s.object_count:,} objects scanned in {s.scan_seconds:.1f}s",
            )
        ]
        findings = _Findings(
            summary_findings(s, self.core.prices),
            empty="No issues found by these checks.",
        )
        if not s.object_count:
            blocks.append(_Note("No objects under this prefix."))
            return self._show(blocks + [findings])
        blocks.append(
            _Cards(
                [
                    ("Objects", f"{s.object_count:,}{'+' if s.truncated else ''}"),
                    ("Total size", human_size(s.total_size)),
                    ("Average size", human_size(s.avg_size)),
                    ("Largest", human_size(s.max_size)),
                    ("Smallest", human_size(s.min_size)),
                    ("Empty files", f"{s.empty_count:,}"),
                    ("File types", f"{len(s.by_extension):,}"),
                    (
                        "Oldest change",
                        human_age(s.oldest.last_modified if s.oldest else None),
                    ),
                    (
                        "Newest change",
                        human_age(s.newest.last_modified if s.newest else None),
                    ),
                    ("Est. cost / month", human_money(s.monthly_cost)),
                ]
            )
        )
        blocks.append(findings)
        n, size = s.object_count, s.total_size
        blocks += [
            _stat_table(
                f"Folders (depth {folder_depth})",
                "Folder",
                s.by_folder,
                n,
                size,
                name=_folder_label,
            ),
            _stat_table("File types", "Extension", s.by_extension, n, size),
            _stat_table(
                f"Storage classes (current versions; cost {self._price_basis()})",
                "Storage class",
                s.by_storage_class,
                n,
                size,
                costs=s.cost_by_storage_class,
            ),
            _stat_table(
                "Object size distribution",
                "Size band",
                s.size_histogram,
                n,
                size,
                by="count",
            ),
            _stat_table("Last modified", "Age", s.age_histogram, n, size, by="count"),
            _objects_table(f"Largest {len(s.largest)} objects", s.largest, base),
        ]
        steps = [
            (_call("tree", s.uri), "the folders below, level by level"),
            (_call("duplicates", s.uri), "identical files and what the copies cost"),
        ]
        if s.cold_standard.size >= GB:
            steps.append(
                (
                    _call("what_if", s.uri, move_after=90, to="STANDARD_IA"),
                    "the saving if files unchanged for 90 days moved to STANDARD_IA",
                )
            )
        else:
            steps.append(
                (_call("find", s.uri, min_size="1GB"), "every file of 1 GB or more")
            )
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def tree(
        self,
        uri: str,
        *,
        depth: int = 2,
        files: int = 10,
        limit: int | None = None,
        max_rows: int = 300,
    ) -> None:
        """
        Folder tree with object count and size at every level down to `depth`, and the files in each folder.

        Each folder shown lists its first `files` files (by name) under its sub-folders, then one
        "… N more files" row with their size; `files=0` shows folders only. Files deeper than `depth`
        count toward their folder's size but aren't listed: raise `depth` or run `ls` on that folder.
        """
        with self._progress() as tick:
            tree = self.core.folder_tree(
                uri, depth=depth, limit=limit, files=max(files, 0), progress=tick
            )
        rows, bars = [], []
        children: dict[str, list[str]] = defaultdict(list)
        for path in tree.folders:
            if path:
                parent = path.rstrip("/").rpartition("/")[0]
                children[parent + "/" if parent else ""].append(path)
        hidden = 0

        def walk(folder: str, level: int) -> None:
            nonlocal hidden
            pad = "    " * level
            for sub in children.get(folder, []):
                st = tree.folders[sub]
                rows.append(
                    [pad + sub.rstrip("/").split("/")[-1] + "/", f"{st.count:,}", human_size(st.size)]
                )
                bars.append(_share(st.size, tree.total.size))
                walk(sub, level + 1)
            shown = tree.files.get(folder, [])
            for obj in shown:
                rows.append([pad + obj.key.rsplit("/", 1)[-1], "", human_size(obj.size)])
                bars.append(_share(obj.size, tree.total.size))
            rest = tree.direct.get(folder, Stat())
            more = rest.count - len(shown)
            if more > 0:
                hidden += more
                size = rest.size - sum(o.size for o in shown)
                noun = "file" if more == 1 else "files"
                rows.append([f"{pad}… {more:,} more {noun}", f"{more:,}", human_size(size)])
                bars.append(_share(size, tree.total.size))
            if folder and level == depth:  # deepest level shown: what sits in its sub-folders
                st = tree.folders[folder]
                deeper, size = st.count - rest.count, st.size - rest.size
                if deeper > 0:
                    rows.append(
                        [f"{pad}… {deeper:,} more in sub-folders", f"{deeper:,}", human_size(size)]
                    )
                    bars.append(_share(size, tree.total.size))

        if tree.max_files:
            walk("", 0)
        else:
            for path, st in tree.folders.items():
                parts = path.rstrip("/").split("/")
                label = (
                    "(files at this level)"
                    if not path
                    else "    " * (len(parts) - 1) + parts[-1] + "/"
                )
                rows.append([label, f"{st.count:,}", human_size(st.size)])
                bars.append(_share(st.size, tree.total.size))
        blocks: list[Any] = [
            _Title(
                f"Folder tree of {tree.uri}",
                f"{tree.total.count:,} objects · {human_size(tree.total.size)} · depth {depth}",
            )
        ]
        if tree.truncated:
            blocks.append(
                _Note(f"Scan stopped at limit={limit:,}; sizes are partial.", "warn")
            )
        if hidden:
            blocks.append(
                _Note(
                    f"Showing up to {files:,} files per folder; {hidden:,} more are counted in the "
                    f"'more files' rows. {_call('tree', tree.uri, depth=depth, files=files * 10)} lists more, "
                    f"{_call('tree', tree.uri, depth=depth, files=0)} only the folders."
                )
            )
        blocks.append(
            _Table(
                ["Folder / file" if tree.max_files else "Folder", "Objects", "Size"],
                rows,
                bars=bars,
                bar_label="% of size",
                tree=True,
                max_rows=max_rows,
            )
        )
        top = max(
            (path for path in tree.folders if path.count("/") == 1),
            key=lambda p: tree.folders[p].size,
            default=None,
        )
        if top:
            bucket, prefix = parse_s3_uri(tree.uri)
            blocks.append(
                _Next(
                    [
                        (
                            _call("summary", s3_uri(bucket, base_prefix(prefix) + top)),
                            "the biggest folder in detail: file types, cost, findings",
                        )
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def find(
        self,
        uri: str,
        *,
        pattern: str | None = None,
        regex: str | None = None,
        extensions: str | Iterable[str] | None = None,
        min_size: int | str | None = None,
        max_size: int | str | None = None,
        modified_after: Any = None,
        modified_before: Any = None,
        storage_classes: str | Iterable[str] | None = None,
        limit: int | None = 1000,
    ) -> None:
        """
        Search by glob / regex / extension / size / date / storage class, e.g.
        find(uri, pattern='*.csv', min_size='10MB', modified_after='7d').
        """
        filters = {
            "pattern": pattern,
            "regex": regex,
            "extensions": extensions,
            "min_size": min_size,
            "max_size": max_size,
            "modified_after": modified_after,
            "modified_before": modified_before,
            "storage_classes": storage_classes,
        }
        filters = {k: v for k, v in filters.items() if v is not None}
        with self._progress() as tick:
            matches = self.core.find(uri, limit=limit, progress=tick, **filters)
        bucket, prefix = parse_s3_uri(uri)
        hit_limit = limit is not None and len(matches) >= limit
        blocks: list[Any] = [
            _Title(
                f"Find in {s3_uri(bucket, prefix)}",
                ", ".join(f"{k}={v!r}" for k, v in filters.items()) or "no filters",
            ),
            _Cards(
                [
                    ("Matches", f"{len(matches):,}{'+' if hit_limit else ''}"),
                    ("Total size", human_size(sum(o.size for o in matches))),
                ]
            ),
        ]
        if hit_limit:
            blocks.append(_Note(f"Stopped at limit={limit:,} matches.", "warn"))
        blocks += [
            _objects_table("Matches", matches, base_prefix(prefix)),
            _Next(_file_steps(bucket, matches)),
        ]
        self._show(blocks)

    @_friendly_errors
    def file_details(
        self,
        uri: str,
        *,
        pattern: str | None = None,
        extensions: str | Iterable[str] | None = None,
        limit: int | None = 200,
        max_read: int | str | None = "1GB",
    ) -> None:
        """
        What's inside each file under a prefix: a PDF's pages, title, author and whether it has text, a Word
        file's words, a deck's slides, each Excel sheet's size, a table's rows and columns, a picture's size, a
        video's length, an archive's files. One table per kind of file.

        Reads only the parts each format needs (a PDF's page tree, a parquet footer, a picture's first bytes, the
        first 256 KB of a CSV, whose rows are then estimated), at most max_read in all. The findings point out
        scanned PDFs that need OCR, files that need a password, files whose content isn't what their name says,
        and table files in one folder with different columns. pattern='*.pdf' or extensions=['pdf', 'docx'] picks
        files; limit= is how many, in key order. A uri that names one file shows just that file. PDFs need pypdf,
        and parquet / ORC / Feather need pyarrow. The data: ui.core.file_details(uri).to_df().
        """
        with (
            self._progress("Listing", unit="files") as tick,
            self._progress("Looking inside", unit="files") as read_tick,
        ):
            report = self.core.file_details(
                uri,
                pattern=pattern,
                extensions=extensions,
                limit=limit,
                max_read=max_read,
                progress=tick,
                read_progress=read_tick,
            )
        bucket, prefix = parse_s3_uri(report.uri)
        one = len(report.files) == 1 and report.files[0].key == prefix
        base = posixpath.dirname(prefix) + "/" if one and "/" in prefix else base_prefix(prefix)
        filters = ", ".join(f"{k}={v!r}" for k, v in report.filters.items())
        looked = f"{_plural(len(report.files), 'file')} looked into" + (
            f" (the first {len(report.files):,} in key order)" if report.truncated else ""
        )
        sub = [
            looked,
            filters,
            f"{human_size(report.bytes_read)} read in {_plural(report.requests, 'request')}",
            f"took {_duration(report.seconds)}",
        ]
        blocks: list[Any] = [
            _Title(f"File details {'of' if one else 'in'} {report.uri}", " · ".join(filter(None, sub)))
        ]
        if not report.files:
            match = " that match" if report.filters else ""
            blocks.append(_Note(f"No files here{match}. Check the prefix (keys are case-sensitive)."))
            return self._show(blocks)
        blocks += [
            _Cards(_details_cards(report)),
            _Findings(
                file_details_findings(report),
                empty="Nothing stood out: every file opened, and each is what its name says.",
            ),
        ]
        blocks += _details_tables(report.files, base)
        options = {"pattern": (pattern, None), "extensions": (extensions, None), "limit": (limit, 200),
                   "max_read": (max_read, "1GB")}
        call = _call("ui.core.file_details", report.uri, **{k: v for k, (v, default) in options.items() if v != default})
        blocks.append(
            _Text(
                f"report = {call}\n"
                "df = report.to_df()   # one row per file: format, size, summary, then pages, rows, columns, ...",
                title="Get it in pandas",
                code=True,
            )
        )
        steps = []
        scanned = next((d for d in report.files if d.format == "pdf" and d.facts.get("text") is False), None)
        pdfs = [d for d in report.files if d.format == "pdf" and d.facts.get("pages")]
        if scanned:
            steps.append((_call("document", scanned.uri), "the scanned pages, as pictures"))
        elif pdfs:
            longest = max(pdfs, key=lambda d: d.facts["pages"])
            steps.append((_call("document", longest.uri),
                          "the PDF, page by page" if len(pdfs) == 1 else "the longest PDF, page by page"))
        for kinds, why in ((_TABLE_KINDS, "the first rows of a table"), (("docx", "pptx"), "a document as it reads"),
                           (("image",), "the first picture"), (("zip", "tar"), "the files in an archive")):
            first = next((d for d in report.files if d.format in kinds and d.facts and not d.error), None)
            if first:
                command = "document" if first.format in ("docx", "pptx") else "preview"
                steps.append((_call(command, first.uri), why))
        if report.truncated and limit is not None:
            more = {k: v for k, v in (("pattern", pattern), ("extensions", extensions)) if v is not None}
            steps.insert(0, (_call("file_details", report.uri, **more, limit=limit * 5), "more of the files"))
        blocks.append(_Next(steps[:3]))
        self._show(blocks)

    @_friendly_errors
    def largest(self, uri: str, n: int = 20) -> None:
        """
        The n biggest objects under a prefix.
        """
        with self._progress() as tick:
            self._top(
                f"Largest {n} objects", uri, self.core.largest(uri, n, progress=tick)
            )

    @_friendly_errors
    def newest(self, uri: str, n: int = 20) -> None:
        """
        The n most recently modified objects.
        """
        with self._progress() as tick:
            self._top(
                f"{n} most recently modified",
                uri,
                self.core.newest(uri, n, progress=tick),
            )

    @_friendly_errors
    def oldest(self, uri: str, n: int = 20) -> None:
        """
        The n least recently modified objects.
        """
        with self._progress() as tick:
            self._top(
                f"{n} least recently modified",
                uri,
                self.core.oldest(uri, n, progress=tick),
            )

    def _top(self, title: str, uri: str, objects: list[ObjectInfo]) -> None:
        bucket, prefix = parse_s3_uri(uri)
        self._show(
            [
                _Title(f"{title} in {s3_uri(bucket, prefix)}"),
                _objects_table("", objects, base_prefix(prefix)),
                _Next(_file_steps(bucket, objects)),
            ]
        )

    @_friendly_errors
    def compare(self, uri_a: str, uri_b: str, *, show: int = 20) -> None:
        """
        Diff two prefixes (e.g. verify a copy / sync / migration).
        """
        with self._progress() as tick:
            r = self.core.compare(uri_a, uri_b, progress=tick)
        _, prefix_a = parse_s3_uri(r.uri_a)
        blocks: list[Any] = [
            _Title("Compare prefixes", f"A = {r.uri_a}   B = {r.uri_b}"),
            _Cards(
                [
                    ("Identical", f"{r.identical:,}"),
                    ("Different", f"{len(r.different):,}"),
                    ("Only in A", f"{len(r.only_in_a):,}"),
                    ("Only in B", f"{len(r.only_in_b):,}"),
                    ("Same size, ETag not comparable", f"{r.unverifiable:,}"),
                ]
            ),
        ]
        if r.in_sync:
            blocks.append(
                _Note(
                    "In sync: every key exists on both sides with the same size.", "ok"
                )
            )
        if r.different:
            blocks.append(
                _Table(
                    ["Key", "Size A", "Size B", "Modified A", "Modified B"],
                    [
                        [
                            relative_key(a.key, prefix_a),
                            human_size(a.size),
                            human_size(b.size),
                            _fmt_dt(a.last_modified),
                            _fmt_dt(b.last_modified),
                        ]
                        for a, b in r.different[:show]
                    ],
                    title=f"Different ({len(r.different):,})",
                    path_cols=(0,),
                )
            )
        if r.only_in_a:
            blocks.append(
                _objects_table(
                    f"Only in A ({len(r.only_in_a):,})", r.only_in_a[:show], prefix_a
                )
            )
        if r.only_in_b:
            blocks.append(
                _objects_table(
                    f"Only in B ({len(r.only_in_b):,})",
                    r.only_in_b[:show],
                    parse_s3_uri(r.uri_b)[1],
                )
            )
        self._show(blocks)
