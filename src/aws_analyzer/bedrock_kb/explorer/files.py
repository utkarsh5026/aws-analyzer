"""
The Files tab's list: its search, state chips, data source and sort, its pages, and ⬇ List.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..._bedrock.blocks import _Download
from ..._bedrock.files import _LIST_LINK_BYTES, _data_href, file_list_name
from ..._kit.fmt import _plural
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if

from ..models import FILE_STATES, KBFile
from ..analysis.files import _SORTS, sort_files
from .frame import _CHIP_LABELS, _FILE_PAGE, _file_face
from .widgets import _FileRow

_PAGE_BUTTONS = (("first", "«", "The first page"), ("previous", "‹", "The page before"),
                 ("next", "›", "The next page"), ("last", "»", "The last page"))
_LISTED_KINDS = ("S3", "CUSTOM")


class _FilesTab:
    """
    The Files tab's list in KBExplorer: every file next to Bedrock's record of it, filtered by its search, a state
    chip and a data source, sorted, a page at a time.
    """

    def _finder(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.find = w.Text(placeholder="Search files: name, folder, reason…", continuous_update=True,
                           layout=layout(flex="1 1 auto", width="auto"))
        self.find.add_class("kbx-find")
        self.find.observe(self._safely(self._found_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("kbx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.find, "value", "")))
        finder = w.HBox([self.find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("kbx-flat")
        return finder

    def _list_filters(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.source_pick = w.Dropdown(options=[("All data sources", "")], value="",
                                      layout=layout(flex="1 1 auto", width="auto", display="none"))
        self.source_pick.observe(self._safely(self._source_changed), names="value")
        self.sort_pick = w.Dropdown(options=[(f"Sort: {label}", key) for key, label in _SORTS.items()],
                                    value="problems", layout=layout(flex="1 1 auto", width="auto"))
        self.sort_pick.observe(self._safely(self._sort_changed), names="value")
        self.list_button = w.Button(description="⬇ List", tooltip="Save the s3:// path of each file the list shows "
                                    "(its search, state and order), one per line, in a .txt file beside the "
                                    "notebook, with a link that downloads it",
                                    layout=layout(flex="0 0 auto", width="auto"))
        self.list_button.add_class("kbx-small")
        self.list_button.on_click(self._safely(lambda _button: self._save_list()))
        filters = w.HBox([self.source_pick, self.sort_pick, self.list_button], layout=layout(width="100%"))
        filters.add_class("kbx-filters")
        return filters

    def _pager(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.pager_text = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.page_buttons = {}
        for key, glyph, tip in _PAGE_BUTTONS:
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("kbx-pg")
            button.on_click(self._safely(lambda _button, key=key: self._page_to(key)))
            self.page_buttons[key] = button
        pager = w.HBox([self.pager_text, *self.page_buttons.values()], layout=layout(width="100%"))
        pager.add_class("kbx-pager")
        return pager

    def _files_page(self) -> Any:
        """
        The Files tab: the list on the left, and the file it opens (or the files' summary) on the right.
        """
        w, layout = self._w, self._w.Layout
        self.chips = w.HBox(layout=layout(width="100%"))
        self.chips.add_class("kbx-chips")
        self.rows_box = w.VBox(layout=layout(width="100%"))
        self.rows_box.add_class("kbx-rows")
        self.saved_view = w.HTML(layout=layout(width="100%"))
        self.saved_view.add_class("kbx-saved")
        self.left = w.VBox([self._finder(), self.chips, self._list_filters(), self.rows_box, self._pager(),
                            self.saved_view])
        self.left.add_class("kbx-left")
        self.split = w.HBox([self.left, self._file_pane()])
        self.split.add_class("kbx-split")
        return self.split

    def _found_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._query = str(self.find.value or "").strip()
        self._offset = 0
        self._refilter()

    def _source_changed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._source = str(self.source_pick.value or "")
        self._offset = 0
        self._draw_chips()
        self._refilter()

    def _sort_changed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._sort = str(self.sort_pick.value or "problems")
        self._offset = 0
        self._refilter()

    def _pick_state(self, state: str) -> None:
        self._state = "" if state == self._state else state
        self._offset = 0
        self._draw_chips()
        self._refilter()

    def _in_source(self) -> list[KBFile]:
        files = self.inventory.files if self.inventory else []
        return [f for f in files if not self._source or f.data_source_id == self._source]

    def _chip(self, state: str, label: str, tone: str, count: int) -> Any:
        w = self._w
        tip = f"Show only the files that are {label.lower()} (click again for every file)" if state else "Show every file"
        chip = w.Button(description=f"{_CHIP_LABELS.get(state, label)} {count:,}", tooltip=tip,
                        layout=w.Layout(width="auto"))
        for name in ("kbx-chip", f"kbx-t-{tone or 'none'}", f"kbx-s-{state or 'all'}"):
            chip.add_class(name)
        _class_if(chip, "kbx-on", state == self._state)
        chip.on_click(self._safely(lambda _button: self._pick_state(state)))
        return chip

    def _draw_chips(self) -> None:
        """
        A chip per state the files are in, with how many: a click shows only those (again shows all).
        """
        counts = Counter(f.state for f in self._in_source())
        chips = [self._chip("", "All", "all", sum(counts.values()))]
        chips += [self._chip(state, *FILE_STATES[state], counts[state]) for state in FILE_STATES if counts[state]]
        self.chips.children = chips

    def _matches_query(self, f: KBFile) -> bool:
        names = self.inventory.sources if self.inventory else {}
        fields = (f.key, f.name, f.reason, FILE_STATES.get(f.state, ("",))[0], names.get(f.data_source_id))
        return search_rank(self._query, fields) is not None

    def _refilter(self) -> None:
        files = [f for f in self._in_source() if not self._state or f.state == self._state]
        if self._query:
            files = [f for f in files if self._matches_query(f)]
        self._visible = sort_files(files, self._sort)
        last_page = max(0, (len(self._visible) - 1) // _FILE_PAGE * _FILE_PAGE)
        self._offset = min(self._offset, last_page)
        self._draw_rows()

    def _shows_source(self) -> bool:
        """
        Whether a row names its data source: when the files come from more than one and no single one is picked.
        """
        if self.inventory is None or self._source:
            return False
        return len([kind for kind in self.inventory.kinds.values() if kind in _LISTED_KINDS]) > 1

    def _empty_text(self) -> str:
        if self.inventory is None:
            return "Listing the files…"
        if not self.inventory.files:
            return "No files: this knowledge base has no S3 or custom data source, or nothing in them yet."
        return "No file matches." + (" Clear the search, or pick All above." if self._query or self._state else "")

    def _draw_row(self, row: _FileRow, f: KBFile, words: list[str], source: str) -> None:
        row.file = f
        self._set(row.face, _file_face(f, words, source))
        label = FILE_STATES.get(f.state, (f.state,))[0]
        tip = f"{f.key}: {label}. {f.note} Click to see how it was indexed."
        if row.button.tooltip != tip:
            row.button.tooltip = tip
        _class_if(row.box, "kbx-on", self.selected is not None and f.uri == self.selected.uri)

    def _draw_rows(self) -> None:
        page = self._visible[self._offset:self._offset + _FILE_PAGE]
        while len(self._rows) < len(page):
            self._rows.append(_FileRow(self))
        words = self._query.split()
        names = self.inventory.sources if self.inventory and self._shows_source() else {}
        for row, f in zip(self._rows, page):
            self._draw_row(row, f, words, names.get(f.data_source_id, ""))
        children: list[Any] = [row.box for row in self._rows[:len(page)]]
        if not page:
            children = [self._w.HTML(f'<div class="kbx-empty">{_esc(self._empty_text())}</div>')]
        self.rows_box.children = children
        self._draw_pager()

    def _draw_pager(self) -> None:
        total = len(self._visible)
        first, last = self._offset + 1, min(self._offset + _FILE_PAGE, total)
        whole = len(self._in_source()) if self.inventory else 0
        text = f"<b>{first:,}–{last:,}</b> of {total:,}" if total else "0 files"
        if self.inventory and total != whole:
            text += f" (of {whole:,})"
        self._set(self.pager_text, text)
        has_pages = total > _FILE_PAGE
        for key, button in self.page_buttons.items():
            button.layout.display = "" if has_pages else "none"
            button.disabled = (self._offset == 0) if key in ("first", "previous") else (last >= total)

    def _page_to(self, where: str) -> None:
        total = len(self._visible)
        end = max(0, (total - 1) // _FILE_PAGE * _FILE_PAGE)
        self._offset = {"first": 0, "previous": max(0, self._offset - _FILE_PAGE),
                        "next": min(end, self._offset + _FILE_PAGE), "last": end}[where]
        self._draw_rows()

    def _clicked_row(self, row: _FileRow) -> None:
        if row.file is not None:
            self._open_file(row.file)

    def _save_list(self) -> None:
        """
        ⬇ List: the s3:// paths of the files the list shows, in its order, one per line, in
        <knowledge base>-files.txt beside the notebook, and a link under the list that downloads it.
        """
        inv = self.inventory
        if inv is None:
            self._status("The list is saved once the files are listed.")
            return
        if not self._visible:
            self._status("No file in the list to save: clear the search, or pick All above.", "warn")
            return
        name = inv.kb_name or inv.kb_id
        self.saved = saved = self.core.save_file_list(self._visible, file_list_name(name, self._state))
        href = _data_href(saved.text, _LIST_LINK_BYTES)
        self._set(self.saved_view, self._html([_Download(saved.path, href)]))
        left_out = (f" ({_plural(saved.left_out, 'custom document')} {'has' if saved.left_out == 1 else 'have'} no "
                    "s3:// path)") if saved.left_out else ""
        where = ("the link under the list downloads it to your computer" if href else
                 "it's too big to download from here: download it from Jupyter's file browser")
        self._status(f"Saved the s3:// paths of {_plural(saved.count, 'file')}{left_out}, one per line, in "
                     f"{saved.name} beside the notebook{', replacing the one there' if saved.replaced else ''}: "
                     f"{where}.", "ok")
