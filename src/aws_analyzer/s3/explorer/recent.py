"""
The explorer's recent places: the folders you opened, remembered between sessions (_kit/history.py), offered as
chips over the list of buckets and while you type a path, so a click takes you back.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import human_age
from ..._kit.history import Place
from ..._kit.window import _window_errors

from .navigator import folder_uri, parse_location

_RECENT_CHIPS = 6
_PICK_UP = "↩ "


def _short_place(uri: str) -> str:
    """
    A folder's name for a chip: 'bucket', 'bucket/folder/', or 'bucket/…/last/' when it's deeper.
    """
    bucket, prefix = parse_location(uri)
    folders = [part for part in prefix.split("/") if part]
    if not folders:
        return bucket
    if len(folders) == 1:
        return f"{bucket}/{folders[0]}/"
    return f"{bucket}/…/{folders[-1]}/"


def _chip_order(places: list[Place]) -> list[Place]:
    """
    The chips' order: where you were last first (to pick up where you left off), then the rest, the most yours first.
    """
    if not places:
        return []
    last = max(places, key=lambda place: place.last)
    return [last] + [place for place in places if place.key != last.key]


class _RecentPane:
    """
    The Recent row over the list: a chip per folder you've opened most (and the one you were in last, first), on
    your list of buckets and while the path box is open.
    """

    def _account(self) -> str:
        return self.core.account_id() or ""

    def _recent_places(self) -> list[Place]:
        if self._history is None:
            return []
        return self._history.places(self._account())

    def _remember(self) -> None:
        """
        Adds the folder the list shows to the recent places (not your list of buckets).
        """
        location = self.nav.location
        if self._history is not None and location:
            self._history.record(self._account(), location, _short_place(location))

    def _build_recent(self) -> Any:
        w = self._widgets
        label = w.HTML('<span class="s3x-recent-l"># recent</span>', layout=w.Layout(width="auto"))
        self._recent_chips, self._recent_keys = [], []
        self._recent_box = w.HBox([label], layout=w.Layout(width="100%", display="none"))
        self._recent_box.add_class("s3x-recent")
        return self._recent_box

    def _recent_chip(self, index: int) -> Any:
        """
        The index-th chip, made once and reused as the places change.
        """
        while len(self._recent_chips) <= index:
            w, at = self._widgets, len(self._recent_chips)
            chip = w.Button(layout=w.Layout(width="auto"))
            for name in ("s3x-chip", "s3x-place"):
                chip.add_class(name)
            chip.on_click(lambda _button, at=at: self._guard(lambda: self._open_recent(at)))
            self._recent_chips.append(chip)
        return self._recent_chips[index]

    def _draw_recent(self) -> None:
        """
        Shows the Recent row on your list of buckets, or while the path box is open, when there's somewhere to go.
        """
        if self._widgets is None:
            return
        editing = self._path.layout.display != "none"
        wanted = editing or not self.nav.location
        places = _chip_order(self._recent_places())[:_RECENT_CHIPS] if wanted else []
        places = [place for place in places if place.key != self.nav.location]

        chips = []
        for index, place in enumerate(places):
            chip = self._recent_chip(index)
            picks_up = index == 0
            chip.description = (_PICK_UP if picks_up else "") + _short_place(place.key)
            lead = "Pick up where you left off" if picks_up else f"Opened {place.visits:,}×"
            chip.tooltip = f"{lead}: {place.key} (last {human_age(place.last)})"
            chips.append(chip)

        self._recent_keys = [place.key for place in places]
        self._recent_box.children = (self._recent_box.children[0], *chips)
        self._recent_box.layout.display = None if chips else "none"

    def _open_recent(self, index: int) -> None:
        if index >= len(self._recent_keys):
            return
        uri = self._recent_keys[index]
        if self._path.layout.display != "none":
            self._toggle_path()
        self.open(uri)

    @property
    def recent(self) -> list[str]:
        """
        The folders you've opened in this account (s3:// paths), the ones you open most and most lately first:
        x.open(x.recent[0]) goes back to the first. [] when recent places are off (history=False).
        """
        return [place.key for place in self._recent_places()]

    @_window_errors
    def forget_recent(self, uri: str = "") -> None:
        """
        Forget a folder in the Recent row (its s3:// path), or with nothing every folder this explorer remembers
        in this account. Nothing in S3 changes.
        """
        if self._history is None:
            self._say("Recent places are off (history=False or AWS_ANALYZER_HISTORY=off): there's nothing to forget.")
            return
        key = folder_uri(*parse_location(uri)) if uri else ""
        removed = self._history.forget(self._account(), key)
        self._draw_recent()
        what = key or "every folder"
        said = f"Forgot {what}." if removed else f"Nothing to forget: {what} isn't among the recent places."
        self._set_pane([[_Note(said)]])
