"""
The downloads folder, and the checks a .zip download passes before it's made.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..._kit.fmt import Stat, _plural, _utcnow, human_size
from ..._kit.render import _call

from ..helpers import GB, MB
from ..prices import ARCHIVE_CLASSES
from ..models import DownloadsFolder, ObjectInfo


@dataclass
class ZipPlan:
    """
    What download_zip would put in a .zip, and whether this notebook can make it (see S3Analyzer.plan_zip).
    """

    uri: str  # the file or folder zipped; for a list of them, the folder they share
    path: str  # the .zip file it would write
    max_size: int  # bytes of files allowed in one zip
    max_files: int
    files: list[tuple[ObjectInfo, str]] = field(
        default_factory=list
    )  # (object, its name in the zip), key order
    archived: Stat = field(
        default_factory=Stat
    )  # GLACIER / DEEP_ARCHIVE files left out (they need a restore)
    left_out: dict[str, str] = field(
        default_factory=dict
    )  # key -> why it isn't in the zip
    more: bool = False  # counting stopped at max_files: there are more files
    disk_free: int | None = None  # bytes free where the zip goes
    memory_free: int | None = None  # RAM available, when it can be read
    read_error: str | None = (
        None  # what reading one file returned, e.g. 'AccessDenied'; None = it worked
    )
    probed: str | None = None  # the key read to check access (None = nothing to read)
    picked: list[str] = field(
        default_factory=list
    )  # the files and folders download_zip was given as a list (s3:// paths; a folder's ends in '/')

    @property
    def size(self) -> int:
        return sum(obj.size for obj, _ in self.files)

    @property
    def space_needed(self) -> int:
        """
        Most the zip can take on disk: the files as they are, plus the zip's own headers.
        """
        return (
            self.size
            + self.size // 1000
            + sum(130 + 2 * len(name.encode()) for _, name in self.files)
            + 100
        )

    @property
    def can_download(self) -> bool:
        return all(ok is not False for _, ok, _ in zip_checks(self))


DOWNLOADS = "s3-downloads"  # where download() and download_zip() save without a path=: this folder, next to the notebook

_DOWNLOADS_MARK = (  # the .gitignore in a folder made for downloads: clean_downloads may empty it, and git skips it
    "# Made by aws-analyzer for what download() and download_zip() save from S3.\n"
    "# S3View().clean_downloads() deletes them; this file keeps them out of git.\n"
    "*\n"
)


_ZIP_SMALL_FILE = (
    8 * MB
)  # download_zip fetches files up to this size ahead, 16 at a time; bigger ones stream

_ALREADY_COMPRESSED = {
    "gz",
    "tgz",
    "bz2",
    "xz",
    "zst",
    "zip",
    "7z",
    "rar",
    "jar",
    "whl",
    "parquet",
    "orc",
    "npz",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "mp3",
    "m4a",
    "aac",
    "ogg",
    "flac",
    "mp4",
    "mov",
    "webm",
    "docx",
    "xlsx",
    "pptx",
}  # stored in a zip as they are: compressing them again gains nothing


def zip_checks(plan: ZipPlan) -> list[tuple[str, bool | None, str]]:
    """
    Whether a zip can be made -> [(check, passed, details)]; passed is None for a note that doesn't block it.
    Checks: something to zip and not too many files, the size limit, disk space, memory and read access.
    """
    files, size = len(plan.files), plan.size
    rows: list[tuple[str, bool | None, str]] = []
    if not files:
        what = (
            f"all {_plural(plan.archived.count, 'file')} are in GLACIER / DEEP_ARCHIVE"
            if plan.archived.count
            else "no files under this prefix"
        )
        return [("Files", False, what)]
    else:
        rows.append(
            (
                "Files",
                not plan.more,
                f"more than {plan.max_files:,}: over the max_files limit"
                if plan.more
                else f"{files:,} of the {plan.max_files:,} allowed (max_files=)",
            )
        )
    at_least = "at least " if plan.more else ""
    rows.append(
        (
            "Size",
            size <= plan.max_size,
            f"{at_least}{human_size(size)} of the {human_size(plan.max_size)} allowed (max_size=)",
        )
    )
    if plan.disk_free is None:
        rows.append(("Disk space", None, "couldn't check the free space"))
    else:
        rows.append(
            (
                "Disk space",
                plan.space_needed <= plan.disk_free,
                f"{at_least}{human_size(plan.space_needed)} needed, {human_size(plan.disk_free)} free in "
                f"{os.path.dirname(plan.path) or '.'}",
            )
        )
    held = min(size, 16 * _ZIP_SMALL_FILE)
    free = "" if plan.memory_free is None else f"; {human_size(plan.memory_free)} free"
    rows.append(
        (
            "Memory",
            True if plan.memory_free is None or plan.memory_free > held else None,
            f"files stream into the zip, at most about {human_size(held + MB)} at a time{free}",
        )
    )
    if plan.probed is None:
        rows.append(("Read access", None, "nothing to read: the files are empty"))
    elif plan.read_error:
        rows.append(("Read access", False, f"{plan.read_error} reading {plan.probed}"))
    else:
        rows.append(("Read access", True, f"read the first byte of {plan.probed}"))
    if plan.archived.count:
        rows.append(
            (
                "Archived files",
                None,
                f"{_plural(plan.archived.count, 'file')} "
                f"({human_size(plan.archived.size)}) left out: restore them first",
            )
        )
    return rows


def _nice_size(size: int) -> str:
    """
    A max_size= value just above `size`: 3.3 MB -> '4MB', 340 MB -> '400MB', 3.2 GB -> '4GB'.
    """
    if size < 100 * MB:
        return f"{max(1, math.ceil(size / MB))}MB"
    if size < GB:
        return f"{math.ceil(size / (100 * MB)) * 100}MB"
    return f"{math.ceil(size / GB)}GB"


def zip_findings(plan: ZipPlan) -> list[tuple[str, str]]:
    """
    What stops a zip, or what's left out of it, and what to do -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    size = plan.size
    if not plan.files and not plan.archived.count:
        found.append(
            (
                "warn",
                "Nothing to zip: there are no files under this prefix. Check the path (keys are "
                "case-sensitive); ls(uri) shows what's there.",
            )
        )
    if plan.more:
        found.append(
            (
                "warn",
                f"There are more than {plan.max_files:,} files here, so it stopped counting. Pass a "
                f"bigger max_files= (and max_size=), or zip a sub-folder; tree(uri) shows their sizes.",
            )
        )
    elif size > plan.max_size:
        room = (
            f" (the disk has {human_size(plan.disk_free)} free)"
            if plan.disk_free is not None
            else ""
        )
        found.append(
            (
                "warn",
                f"The files take {human_size(size)}, over the {human_size(plan.max_size)} limit. Pass "
                f"max_size='{_nice_size(size)}' to zip them anyway{room}, or zip a sub-folder; "
                "tree(uri) shows their sizes.",
            )
        )
    if plan.disk_free is not None and plan.space_needed > plan.disk_free and plan.files:
        found.append(
            (
                "warn",
                f"The disk doesn't have room: the zip needs up to {human_size(plan.space_needed)} and "
                f"{human_size(plan.disk_free)} is free in {os.path.dirname(plan.path) or '.'}. Delete "
                "files you don't need there, or pass path= on a disk with more room.",
            )
        )
    if plan.read_error:
        found.append(
            (
                "warn",
                f"The notebook's role can't read these files ({plan.read_error} on {plan.probed}). It "
                "needs s3:GetObject on them, and kms:Decrypt on the key when they're encrypted with "
                "SSE-KMS.",
            )
        )
    if plan.archived.count:
        found.append(
            (
                "warn" if not plan.files else "info",
                f"{_plural(plan.archived.count, 'file')} ({human_size(plan.archived.size)}) in GLACIER / "
                "DEEP_ARCHIVE can't go in the zip until they're restored (restore_object, or the S3 console).",
            )
        )
    unsafe = [key for key, why in plan.left_out.items() if why not in ARCHIVE_CLASSES]
    if unsafe:
        found.append(
            (
                "info",
                f"{_plural(len(unsafe), 'file')} left out because their names would unzip outside the "
                "folder or clash with another file's (the table lists them).",
            )
        )
    return found


def downloads_findings(folder: DownloadsFolder, now: datetime | None = None) -> list[tuple[str, str]]:
    """
    What to tidy up in the downloads folder, and the call that does it -> [(level, message)]: downloads in a
    folder that isn't theirs, unfinished zips, a disk running out of room, downloads over a month old.
    """
    found: list[tuple[str, str]] = []
    own = f"S3View(downloads={DOWNLOADS!r})"
    if folder.protected:
        found.append(("warn", f"Downloads go into {folder.path}, {folder.protected}: they mix with your own files "
                              f"and clean_downloads() can't tidy them up. Give them a folder of their own: {own}."))
    elif folder.exists and not folder.managed:
        found.append(("warn", f"{folder.path} wasn't made for downloads (it has no aws-analyzer .gitignore), so "
                              "clean_downloads() won't empty it, in case it holds your own files. Delete what you "
                              f"don't need there yourself, or give downloads a folder of their own: {own}."))
    cleanable = folder.managed and not folder.protected
    unfinished = [entry for entry in folder.entries if entry.kind == "unfinished"]
    if unfinished:
        size = sum(entry.size for entry in unfinished)
        names = [entry.name for entry in unfinished]
        fix = (f" {_call('clean_downloads', names if len(names) > 1 else names[0])} deletes "
               f"{'them' if len(names) > 1 else 'it'}.") if cleanable else ""
        found.append(("warn", f"{_plural(len(unfinished), 'unfinished zip')} ({human_size(size)}): a download_zip() "
                              f"that stopped (a restarted kernel) left {', '.join(names[:3])}"
                              f"{', ...' if len(names) > 3 else ''}, which won't open.{fix}"))
    if folder.disk_free is not None and folder.size and folder.disk_free < max(folder.size, GB):
        fix = ("clean_downloads() deletes them all, or clean_downloads(older_than='7d') those from before this week"
               if cleanable else "delete the ones you're done with")
        found.append(("warn", f"The disk is running out of room: {human_size(folder.disk_free)} free, and downloads "
                              f"take {human_size(folder.size)}. The next big download or zip may not fit: {fix}."))
    month = (now or _utcnow()) - timedelta(days=30)
    old = [entry for entry in folder.entries if entry.downloaded < month and entry.kind != "unfinished"]
    if old and cleanable:
        found.append(("info", f"{_plural(len(old), 'download')} ({human_size(sum(e.size for e in old))}) "
                              f"{'is' if len(old) == 1 else 'are'} over a month old: "
                              f"{_call('clean_downloads', older_than='30d')} deletes "
                              f"{'it' if len(old) == 1 else 'them'} and keeps the newer ones."))
    return found
