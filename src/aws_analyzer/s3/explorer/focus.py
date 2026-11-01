"""
What S3Explorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from ..._kit.fmt import _plural, human_size
from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from .navigator import Entry, FolderStats, parse_location

_CHIP_CHARS = 80
_KIND_LABELS = {"folders": "Folders only", "files": "Files only"}


def _path_text(uri: str) -> str:
    """
    A folder as a chip reads it: its path without 's3://'.
    """
    return uri.removeprefix("s3://")


def _entry_name(uri: str) -> str:
    """
    What the list calls a file or folder: 'a.json' for s3://b/raw/a.json, 'raw' for s3://b/raw/.
    """
    bucket, key = parse_location(uri)
    kind = "folder" if key.endswith("/") else "file"
    return Entry(kind, bucket, key).name if key else bucket


def _contents_part(stats: FolderStats, *, more: bool) -> FocusPart:
    """
    What the folder holds, as far as it's listed: '13 files · 9.8 MB', with 'so far' while S3 has more.
    """
    pieces = [_plural(stats.files, "file")]
    if stats.folders:
        pieces.insert(0, _plural(stats.folders, "folder"))
    text = f"{' · '.join(pieces)} · {human_size(stats.size)}"
    value = f"{stats.folders} folders, {stats.files} files, {stats.size} bytes"
    if more:
        return FocusPart("contents", f"{value} (listed so far)", f"{text} so far")
    return FocusPart("contents", value, text)


def _picked_part(uris: list[str]) -> list[FocusPart]:
    """
    What's ticked in the list: one file by its name, several as a count. Their s3:// paths are one per line.
    """
    if not uris:
        return []
    text = _entry_name(uris[0]) if len(uris) == 1 else f"{len(uris)} ticked"
    return [FocusPart("picked", "\n".join(uris), text)]


class _FocusReads:
    """
    S3Explorer's `focus`: what the window is showing, as data.
    """

    @property
    def focus(self) -> Focus | None:
        """
        What the window is showing, as a Focus (aws_analyzer._kit.focus): its kind is 'buckets' (your list of
        buckets), 'folder' (the folder you're in: what it holds as far as it's listed, the search, Folders or Files,
        Include subfolders and what's ticked) or 'file' (the file shown on the right, and its folder).
        x.focus.chips() reads it as chips; None when the s3 package couldn't be loaded.
        """
        if self.s3 is None:
            return None
        location = self.location
        picked = _picked_part(self.picked)
        if not location:
            return Focus("s3", "buckets", parts=(*self._list_parts(), *picked))
        folder = FocusPart("folder", location, _path_text(location))
        if self.selected:
            file = FocusPart("file", self.selected, _entry_name(self.selected))
            return Focus("s3", "file", parts=(file, folder, *picked))
        return Focus("s3", "folder", parts=(folder, *self._contents_parts(location), *self._list_parts(), *picked))

    def _contents_parts(self, location: str) -> list[FocusPart]:
        """
        What the list says the folder holds, once it's listed; nothing before that, or for another folder's listing.
        """
        folder = self._folder
        if folder is None or folder.uri != location:
            return []
        return [_contents_part(self._stats(folder), more=folder.more)]

    def _list_parts(self) -> list[FocusPart]:
        """
        How the list is filtered: the search box, Folders or Files, and Include subfolders.
        """
        parts = []
        if self._query:
            parts.append(FocusPart("search", self._query, f"search: {_clip(self._query, _CHIP_CHARS)}"))
        if self._kind in _KIND_LABELS:
            parts.append(FocusPart("kind", self._kind, _KIND_LABELS[self._kind]))
        if self._deep:
            parts.append(FocusPart("subfolders", "yes", "with subfolders"))
        return parts
