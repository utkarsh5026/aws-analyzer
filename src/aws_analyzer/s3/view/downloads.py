"""
The Download commands.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.fmt import _duration, _fmt_dt, _in_zone, _plural, _share, human_age, human_size
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import base_prefix, parse_s3_uri, relative_key, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..readers.formats import detect_format
from ..analysis.downloads import downloads_findings, zip_checks, zip_findings
from ..analyzer.downloads import _free_space, _made_for_downloads, _protected_folder


_PANDAS_READERS = {  # format -> how pandas opens a downloaded file of it
    "csv": "read_csv({path})",
    "tsv": "read_csv({path}, sep='\\t')",
    "psv": "read_csv({path}, sep='|')",
    "parquet": "read_parquet({path})",
    "orc": "read_orc({path})",
    "arrow": "read_feather({path})",
    "jsonl": "read_json({path}, lines=True)",
    "json": "read_json({path})",
    "excel": "read_excel({path})",
}


class _DownloadCommands:
    """
    S3View's commands to download: download(), download_zip(), downloads() and clean_downloads().
    """

    @_friendly_errors
    def download(
        self, uri: str, path: str | None = None, *, limit: int | None = None
    ) -> None:
        """
        Download a file, or a whole folder with its sub-folders, with a progress bar, and say where it went.
        Files already downloaded are skipped, so running it again resumes.

        Without a path it saves into the downloads folder (s3-downloads next to the notebook; S3View(downloads=...)
        picks another), so downloads don't mix with your notebooks and code: downloads() lists them and
        clean_downloads() deletes them. A path saves there instead, e.g. download('s3://b/data/', '/tmp/data').
        """
        bucket, key = parse_s3_uri(uri)
        if key and not key.endswith("/") and self.core.exists(uri):
            started = time.monotonic()
            with self._progress("Downloading", unit="B") as tick:
                local = self.core.download(uri, path, progress=tick)
            seconds, size = time.monotonic() - started, os.path.getsize(local)
            blocks: list[Any] = [
                _Title(f"Downloaded {s3_uri(bucket, key)}", f"to {local}"),
                _Cards(
                    [
                        ("Size", human_size(size)),
                        ("Took", _duration(seconds)),
                        ("Speed", f"{human_size(size / max(seconds, 1e-3))}/s"),
                    ]
                ),
            ]
            opener = _PANDAS_READERS.get(detect_format(key)[0] or "")
            if opener:
                blocks.append(
                    _Text(
                        f"import pandas as pd\n\ndf = pd.{opener.format(path=repr(local))}",
                        title="Open it",
                        code=True,
                    )
                )
            blocks.append(self._downloads_next(local))
            return self._show(blocks)
        with (
            self._progress("Listing", unit="files") as list_tick,
            self._progress("Downloading", unit="B") as tick,
        ):
            d = self.core.download_folder(
                uri, path, limit=limit, progress=tick, list_progress=list_tick
            )
        speed = (
            f"{human_size(d.downloaded.size / max(d.seconds, 1e-3))}/s"
            if d.downloaded.size
            else "-"
        )
        blocks = [
            _Title(
                f"{'Downloaded' if d.downloaded.count else 'Download of'} {d.uri}",
                f"to {d.path}",
            ),
            _Cards(
                [
                    ("Files downloaded", f"{d.downloaded.count:,}"),
                    ("Size", human_size(d.downloaded.size)),
                    ("Already there", f"{d.already_there.count:,}"),
                    ("Not downloaded", f"{len(d.skipped):,}"),
                    ("Took", _duration(d.seconds)),
                    ("Speed", speed),
                ]
            ),
        ]
        if d.truncated:
            blocks.append(
                _Note(
                    f"Stopped at limit={limit:,} files; pass a bigger limit= (or none) for the rest. "
                    "Running it again skips what's already downloaded.",
                    "warn",
                )
            )
        if not (d.downloaded.count or d.already_there.count or d.skipped):
            blocks.append(
                _Note(
                    "No files under this folder. Check the path (keys are case-sensitive)."
                )
            )
        elif d.downloaded.count or d.already_there.count:
            again = (
                (
                    f" {_plural(d.already_there.count, 'file')} ({human_size(d.already_there.size)}) were already "
                    "there with the same size and time, so they weren't downloaded again."
                )
                if d.already_there.count
                else ""
            )
            blocks.append(
                _Note(
                    f"Files are in {d.path}, in the same sub-folders as in S3.{again}",
                    "ok",
                )
            )
        if d.skipped:
            archived = sum(reason in ARCHIVE_CLASSES for reason in d.skipped.values())
            if archived:
                blocks.append(
                    _Note(
                        f"{_plural(archived, 'file')} in GLACIER / DEEP_ARCHIVE weren't downloaded: they "
                        "need a restore first (the S3 console, or restore_object).",
                        "warn",
                    )
                )
            if len(d.skipped) > archived:
                blocks.append(
                    _Note(
                        f"{_plural(len(d.skipped) - archived, 'file')} couldn't be downloaded; the table "
                        "says why. Running download() again retries them.",
                        "warn",
                    )
                )
            blocks.append(
                _Table(
                    ["Key", "Why"],
                    [
                        [relative_key(k, parse_s3_uri(d.uri)[1]), why]
                        for k, why in sorted(d.skipped.items())
                    ],
                    title="Not downloaded",
                    path_cols=(0,),
                )
            )
        if d.downloaded.count or d.already_there.count:
            blocks.append(self._downloads_next(d.path))
        self._show(blocks)

    def _downloads_next(self, path: str) -> _Next:
        """
        The Next block of a download: what's downloaded so far, and deleting this one when it's done with.
        """
        steps = [(_call("downloads"), "everything downloaded so far, how much disk it takes, and cleaning up")]
        folder = self.core.downloads_folder()
        if os.path.dirname(path) == folder and _made_for_downloads(folder) and not _protected_folder(folder):
            steps.append((_call("clean_downloads", os.path.basename(path)), "delete this download when you're done"))
        return _Next(steps)

    @_friendly_errors
    def download_zip(
        self,
        uri: str | Iterable[str],
        path: str | None = None,
        *,
        max_size: int | str = "100MB",
        max_files: int = 10_000,
        dry_run: bool = False,
    ) -> None:
        """
        Download a file or folder as one .zip, after checking this notebook can: size limit (100 MB by default),
        file count, disk space, memory and read access. dry_run=True only runs the checks.

        Pass a list to zip several files and folders from one bucket together, laid out as they are under the
        folder they share: download_zip(['s3://b/raw/a.csv', 's3://b/raw/b.csv', 's3://b/raw/2024/']) makes
        raw-3-items.zip. Without a path the zip goes in the downloads folder (s3-downloads next to the notebook;
        see download()).
        """
        with (
            self._progress("Listing", unit="files") as list_tick,
            self._progress("Zipping", unit="B") as tick,
        ):
            z = self.core.download_zip(
                uri,
                path,
                max_size=max_size,
                max_files=max_files,
                dry_run=dry_run,
                progress=tick,
                list_progress=list_tick,
            )
        plan = z.plan
        checks = zip_checks(plan)
        can = plan.can_download
        files = f"{len(plan.files):,}{'+' if plan.more else ''}"
        what = plan.uri
        if plan.picked:
            folders = sum(picked.endswith("/") for picked in plan.picked)
            parts = [_plural(len(plan.picked) - folders, "file")] if len(plan.picked) > folders else []
            what = f"{' and '.join(parts + ([_plural(folders, 'folder')] if folders else []))} from {plan.uri}"
        blocks: list[Any] = [
            _Title(
                f"Zip of {what}",
                f"{_plural(len(plan.files), 'file')} · "
                f"{human_size(plan.size)} → {plan.path}",
            )
        ]
        cards = [
            ("Can download", "yes" if can else "no", "ok" if can else "bad"),
            ("Files", files),
            ("Size", human_size(plan.size)),
            ("Limit", human_size(plan.max_size)),
            ("Free disk", human_size(plan.disk_free)),
        ]
        if z.written:
            saved = _share(plan.size - z.zip_size, plan.size)
            cards += [
                ("Zip size", human_size(z.zip_size)),
                ("Took", _duration(z.seconds)),
            ]
            smaller = f", {saved:.0%} smaller than the files" if saved >= 0.01 else ""
            blocks += [
                _Cards(cards),
                _Note(
                    f"Saved {plan.path} ({human_size(z.zip_size)}{smaller}). To get it onto your computer, right-click "
                    "it in JupyterLab's file browser and choose Download.",
                    "ok",
                ),
            ]
        elif can:
            options = {
                "max_size": (max_size, "100MB"),
                "max_files": (max_files, 10_000),
            }
            args = "".join(
                f", {k}={v!r}" for k, (v, default) in options.items() if v != default
            )
            call = (
                "the same download_zip() call without dry_run=True"
                if plan.picked
                else f"download_zip({plan.uri!r}{args})"
            )
            blocks += [
                _Cards(cards),
                _Note(
                    f"It can be downloaded: every check passed. Run {call} to make the zip.",
                    "ok",
                ),
            ]
        else:
            why = {
                "Files": "too many files" if plan.more else "nothing to zip",
                "Size": "over the size limit",
                "Disk space": "not enough disk space",
                "Read access": "the files can't be read",
            }
            failed = ", ".join(
                why.get(name, name) for name, ok, _ in checks if ok is False
            )
            blocks += [
                _Cards(cards),
                _Note(
                    f"Can't zip this here yet: {failed}. Nothing was downloaded; the notes "
                    "below say what to change.",
                    "warn",
                ),
            ]
        blocks.append(_Findings(zip_findings(plan)))
        if z.failed:
            blocks.append(
                _Note(
                    f"{_plural(len(z.failed), 'file')} couldn't be read while zipping, so the zip leaves "
                    "them out (the table lists them). Running download_zip() again retries them.",
                    "warn",
                )
            )
        blocks.append(
            _Table(
                ["Check", "Result", "Details"],
                [
                    [
                        name,
                        _Tone("✓ ok", "ok")
                        if ok
                        else _Tone("✗ no", "bad")
                        if ok is False
                        else "· note",
                        details,
                    ]
                    for name, ok, details in checks
                ],
                title="Can this notebook make the zip?",
                max_rows=0,
            )
        )
        left_out = {**plan.left_out, **z.failed}
        if left_out:
            base = parse_s3_uri(plan.uri)[1]
            blocks.append(
                _Table(
                    ["Key", "Why it isn't in the zip"],
                    [
                        [relative_key(key, base_prefix(base)) or key, why]
                        for key, why in sorted(left_out.items())
                    ],
                    title="Left out",
                    path_cols=(0,),
                )
            )
        if z.written:
            blocks.append(self._downloads_next(plan.path))
        self._show(blocks)

    @_friendly_errors
    def downloads(self) -> None:
        """
        What you've downloaded: each file, folder and zip in the downloads folder with its size and when it was
        downloaded, how much of the disk they take, and the clean_downloads() calls that delete them.

        The downloads folder is where download() and download_zip() save without a path: s3-downloads, next to the
        notebook. S3View(downloads='~/scratch/s3') or ui.core.downloads = '...' picks another. It's made with the
        first download, with a .gitignore that keeps it out of git and marks it as one clean_downloads() may empty.
        """
        d = self.core.list_downloads()
        blocks: list[Any] = [_Title("Downloads", f"in {d.path}")]
        if d.protected:  # not listed: it holds your notebooks too
            return self._show(blocks + [_Findings(downloads_findings(d))])
        if not d.exists or not (d.entries or d.errors):
            blocks += [
                _Note(f"Nothing downloaded yet: download(uri) and download_zip(uri) save into {d.path}"
                      f"{'' if d.exists else ', which is made with the first download'}."),
                _Findings(downloads_findings(d)),
                _Next([(_call("download", "s3://bucket/path/"), "a file or a folder, with a progress bar"),
                       (_call("download_zip", "s3://bucket/folder/"), "a folder as one .zip, to get it onto your "
                        "computer")]),
            ]
            return self._show(blocks)
        found = downloads_findings(d)
        low = any(message.startswith("The disk is running out") for _, message in found)
        oldest = min(entry.downloaded for entry in d.entries) if d.entries else None
        blocks += [
            _Cards([
                ("Downloads", f"{len(d.entries):,}"),
                ("Files", f"{d.files:,}"),
                ("Size", human_size(d.size)),
                ("Oldest", human_age(oldest)),
                ("Free disk", human_size(d.disk_free), *(("warn",) if low else ())),
            ]),
            _Findings(found, empty="Nothing to tidy up: every download is under a month old, and the disk has room."),
            _Table(
                ["Name", "What", "Size", "Files", "Downloaded"],
                [[entry.name + ("/" if entry.is_folder else ""), entry.kind, human_size(entry.size),
                  f"{entry.files:,}", human_age(entry.downloaded)] for entry in d.entries],
                title="In the downloads folder, newest first",
                path_cols=(0,),
            ),
        ]
        if d.errors:
            blocks.append(_Note(f"Couldn't read {', '.join(sorted(d.errors))} ({', '.join(sorted(set(d.errors.values())))}), "
                                "so the totals leave it out.", "warn"))
        if d.managed and not d.protected and d.entries:
            biggest = max(d.entries, key=lambda entry: entry.size)
            blocks.append(_Next([
                (_call("clean_downloads", dry_run=True), "what clean_downloads() would delete, deleting nothing"),
                (_call("clean_downloads", older_than="7d"), "delete what was downloaded before this week"),
                (_call("clean_downloads", biggest.name), f"delete the biggest, {human_size(biggest.size)}"),
            ]))
        self._show(blocks)

    @_friendly_errors
    def clean_downloads(
        self,
        names: str | Iterable[str] | None = None,
        *,
        older_than: datetime | date | timedelta | str | None = None,
        dry_run: bool = False,
    ) -> None:
        """
        Delete downloads to free the disk: everything download() and download_zip() saved in the downloads
        folder, or the ones named (clean_downloads('churn.zip'), as downloads() lists them), or those downloaded
        before older_than ('7d', '2024-05-01'). dry_run=True only shows what would go. Nothing in S3 changes.

        It only empties a folder made for downloads (s3-downloads, or the one S3View(downloads=...) names, when
        download() made it): never the notebook's own folder, your home folder, or a folder that held other files
        first. The folder itself stays, so the next download goes in the same place.
        """
        c = self.core.clean_downloads(names, older_than=older_than, dry_run=dry_run)
        removed = [entry for entry in c.removed if entry.name not in c.failed]
        verb = "Would delete" if dry_run else "Deleted"
        when = f" downloaded before {_fmt_dt(c.older_than, zone=True)}" if c.older_than else ""
        try:
            free = human_size(_free_space(c.path))
        except OSError:
            free = "-"
        blocks: list[Any] = [
            _Title(f"{verb} {_plural(len(removed), 'download')}" if c.removed else "Nothing to delete",
                   f"from {c.path}"),
            _Cards([
                (verb, f"{len(removed):,}"),
                ("Would free" if dry_run else "Freed", human_size(c.freed)),
                ("Kept", f"{len(c.kept):,}"),
                ("Free disk", free),
            ]),
        ]
        if not c.removed:
            what = (f"Nothing in the downloads folder was downloaded before {_in_zone(c.older_than):%Y-%m-%d}"
                    if c.older_than
                    else "Nothing was named" if c.kept else "The downloads folder is empty")
            blocks.append(_Note(f"{what}; downloads() lists what's there." if c.kept else f"{what}."))
        elif dry_run:
            again = {key: value for key, value in (("older_than", older_than),) if value is not None}
            call = _call("clean_downloads", *([names] if names is not None else []), **again)
            blocks.append(_Note(f"Nothing was deleted (dry_run=True): {call} deletes "
                                f"{'it' if len(removed) == 1 else 'these'}{when}, {human_size(c.freed)}.", "ok"))
        elif removed:
            blocks.append(_Note(f"Freed {human_size(c.freed)} on the notebook's disk; the files are still in S3, so "
                                "download() gets them again.", "ok"))
        if c.failed:
            blocks.append(_Note(f"{_plural(len(c.failed), 'download')} couldn't be deleted "
                                f"({', '.join(sorted(set(c.failed.values())))}); the table says which. A file that's "
                                "open elsewhere (a running job) can't be deleted until it's closed.", "warn"))
            blocks.append(_Table(["Name", "Why"], [[name, why] for name, why in sorted(c.failed.items())],
                                 title="Not deleted", path_cols=(0,)))
        if removed:
            blocks.append(_Table(
                ["Name", "What", "Size", "Files", "Downloaded"],
                [[entry.name + ("/" if entry.is_folder else ""), entry.kind, human_size(entry.size),
                  f"{entry.files:,}", human_age(entry.downloaded)] for entry in removed],
                title="Would delete" if dry_run else "Deleted",
                path_cols=(0,),
            ))
        if c.kept:
            blocks.append(_Next([(_call("downloads"), f"the {_plural(len(c.kept), 'download')} left")]))
        self._show(blocks)
