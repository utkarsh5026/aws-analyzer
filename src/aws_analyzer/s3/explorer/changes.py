"""
What changed in a folder since your last visit, from what the listing already holds (no AWS call): the files S3 says
were modified after it, and how the folder's counts moved since the snapshot the explorer noted then.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..._kit.fmt import _plural, human_age, human_size

from .navigator import Entry, folder_stats


@dataclass(frozen=True)
class FolderLook:
    """
    What a folder held when you looked: its files, folders and their size, and whether all of it was listed.
    """

    files: int
    folders: int
    size: int
    complete: bool

    def to_data(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> FolderLook | None:
        """
        A look read back from a snapshot, or None when the snapshot isn't one (another version wrote it).
        """
        try:
            return cls(int(data["files"]), int(data["folders"]), int(data["size"]), bool(data["complete"]))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class FolderChanges:
    """
    A folder since your last visit (`since`): the files modified after it (new or replaced, which S3 doesn't tell
    apart), and the files it held then and now when both listings were whole.
    """

    since: datetime
    new_files: int
    new_bytes: int
    files_before: int | None = None
    files_now: int | None = None
    partial: bool = False

    @property
    def has_news(self) -> bool:
        return self.new_files > 0 or self.files_before != self.files_now


def folder_look(entries: list[Entry], *, complete: bool) -> FolderLook:
    """
    The look of a folder from its listed entries.
    """
    stats = folder_stats(entries)
    return FolderLook(stats.files, stats.folders, stats.size, complete)


def is_new(entry: Entry, since: datetime) -> bool:
    """
    Whether a file was modified after `since`.
    """
    return entry.kind == "file" and entry.modified is not None and entry.modified > since


def folder_changes(since: datetime, entries: list[Entry], *, before: FolderLook | None,
                   complete: bool) -> FolderChanges:
    """
    What changed since `since`, among the listed entries; the counts are compared only when the look then and the
    listing now are both whole.
    """
    new = [entry for entry in entries if is_new(entry, since)]
    new_bytes = sum(entry.size or 0 for entry in new)
    if before is None or not before.complete or not complete:
        return FolderChanges(since, len(new), new_bytes, partial=not complete)
    now = folder_look(entries, complete=complete)
    return FolderChanges(since, len(new), new_bytes, before.files, now.files)


def changes_card(changes: FolderChanges) -> tuple[str, str]:
    """
    The overview's card: how many files are new since your last visit.
    """
    return (f"New since {human_age(changes.since)}", f"{changes.new_files:,}")


def changes_sentence(changes: FolderChanges, *, marked: bool) -> str:
    """
    What changed since your last visit, in a sentence; marked=True when the list marks the new files with ●.
    """
    when = f"your last visit {human_age(changes.since)}"
    if not changes.has_news:
        return f"Nothing new here since {when}."

    parts = []
    if changes.new_files:
        listed = " listed so far" if changes.partial else ""
        new = f"{_plural(changes.new_files, 'file')}{listed} {'is' if changes.new_files == 1 else 'are'} new or changed"
        parts.append(f"{new} ({human_size(changes.new_bytes)})" + (", marked ● in the list" if marked else ""))
    else:
        parts.append("no file is new or changed")

    moved = changes.files_before is not None and changes.files_before != changes.files_now
    if moved:
        parts.append(f"the folder went from {changes.files_before:,} to {_plural(changes.files_now or 0, 'file')}")
    return f"Since {when}: {'; '.join(parts)}."
