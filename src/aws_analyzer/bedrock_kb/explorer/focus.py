"""
What KBExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from ..._kit.fmt import _plural
from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from ..models import FILE_STATES, FileInventory, FileProbe, KBFile, Retrieval
from .frame import _EXPLORER_TABS

_FOCUS_FILES = 20
_CHIP_CHARS = 80
_TAB_KINDS = {"overview": "knowledge_base", "settings": "knowledge_base", "syncs": "syncs", "search": "search"}


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


def _state_part(state: str) -> FocusPart:
    """
    A file state as its chip reads it: 'failed' -> 'Failed'.
    """
    label = FILE_STATES.get(state, (state, ""))[0]
    return FocusPart("state", state, label)


def _source_part(source_id: str, inventory: FileInventory | None) -> FocusPart:
    names = inventory.sources if inventory is not None else {}
    return FocusPart("data_source", source_id, names.get(source_id) or source_id)


def _question_part(question: str, label: str) -> FocusPart:
    return FocusPart("question", question, f"{label}: {_clip(question, _CHIP_CHARS)}")


def _file_parts(file: KBFile, inventory: FileInventory | None, probe: FileProbe | None) -> list[FocusPart]:
    """
    A file opened in the Files tab: its path, its state and Bedrock's reason for it, its data source, and the
    question last asked of it.
    """
    parts = [FocusPart("file", file.uri, file.name), _state_part(file.state)]
    if file.reason:
        parts.append(FocusPart("reason", file.reason, _clip(file.reason, _CHIP_CHARS)))
    if file.data_source_id:
        parts.append(_source_part(file.data_source_id, inventory))
    if probe is not None and probe.uri == file.uri:
        parts.append(_question_part(probe.question, "asked"))
    return parts


def _search_parts(found: Retrieval | None) -> list[FocusPart]:
    """
    The Search tab's last search: its question and how many passages came back.
    """
    if found is None:
        return []
    passages = len(found.passages)
    return [_question_part(found.question, "search"), FocusPart("passages", str(passages), _plural(passages, "passage"))]


class _FocusReads:
    """
    KBExplorer's `focus`: what the window is showing, as data.
    """

    @property
    def focus(self) -> Focus | None:
        """
        What the window is showing, as a Focus (aws_analyzer._kit.focus): its kind is 'knowledge_base' (the Overview
        or Settings tab), 'files' (the Files tab's list: its state chip, data source and search, and the files they
        let through), 'file' (a file opened there: its state, Bedrock's reason and the question asked of it),
        'syncs' or 'search' (with the last search's question). x.focus.chips() reads it as chips; None when there's
        no window (text mode) or no knowledge base open.
        """
        if self._w is None or not self.kb:
            return None
        base = (FocusPart("knowledge_base", self.kb, self.core.kb_name(self.kb)), _tab_part(self._tab))
        region = self.core.region or ""
        if self._tab == "files" and self.selected is not None:
            parts = _file_parts(self.selected, self.inventory, self.probe)
            return Focus("bedrock_kb", "file", region=region, parts=(*base, *parts))
        if self._tab == "files":
            return Focus("bedrock_kb", "files", region=region, parts=(*base, *self._list_parts()))
        extra = _search_parts(self.found) if self._tab == "search" else []
        return Focus("bedrock_kb", _TAB_KINDS.get(self._tab, "knowledge_base"), region=region, parts=(*base, *extra))

    def _list_parts(self) -> list[FocusPart]:
        """
        How the Files tab's list is filtered (its state chip, data source and search), and the files it shows.
        """
        parts = []
        if self._state:
            parts.append(_state_part(self._state))
        if self._source:
            parts.append(_source_part(self._source, self.inventory))
        if self._query:
            parts.append(FocusPart("search", self._query, f"search: {_clip(self._query, _CHIP_CHARS)}"))
        if self.inventory is not None:
            uris = "\n".join(file.uri for file in self._visible[:_FOCUS_FILES])
            parts.append(FocusPart("files", uris, _plural(len(self._visible), "file")))
        return parts
