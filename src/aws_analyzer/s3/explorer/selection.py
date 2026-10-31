"""
Ticked files and the panel that zips them: the pane's mixin.
"""

from __future__ import annotations

import html
import os
from typing import Any, Callable

from ..._kit.blocks import _Cards, _Note, _Table, _Title
from ..._kit.fmt import _plural, human_age, human_size, parse_size
from ..analyzer.downloads import _zip_layout
from ..models import ObjectInfo
from ..style import _render_html

from .navigator import Entry, _natural, parse_location
from .frame import _CHECK, _Row


class _SelectionPane:
    """
    Ticking files in S3Explorer: the checkboxes, the bar under the list, and the panel that downloads the
    selection as one .zip.
    """

    def _build_picks(self, button: Callable[..., Any]) -> Any:
        """
        The panel ⬇ Download .zip in the selection bar opens on the right, under what's selected: the zip's name
        (Enter downloads too), Download and Cancel, and the list of what goes in.
        """
        w = self._widgets
        self._picks_name = w.Text(layout=w.Layout(width="280px"))
        self._picks_name.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._save_picks))
        self._picks_where = w.HTML()
        name = w.HBox([w.Label("Save as", layout=w.Layout(width="64px")), self._picks_name, self._picks_where],
                      layout=w.Layout(width="100%"))
        self._picks_go = button("⬇ Download", "s3x-primary", "Make the zip, after checking the size, disk space and "
                                "read access", self._save_picks)
        cancel = button("Cancel", "s3x-act", "Close this and keep the selection", self._close_picks)
        actions = w.HBox([self._picks_go, cancel], layout=w.Layout(width="100%"))
        for row in (name, actions):
            row.add_class("s3x-setting")
        self._picks_msg = w.HTML()
        self._picks_list = w.HTML(layout=w.Layout(width="100%"))
        panel = w.VBox([name, actions, self._picks_msg, self._picks_list],
                       layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        panel.add_class("s3x-settings")
        return panel

    # ------------------------------------------------------------------ selecting files to download as one .zip

    def _on_check(self, row: _Row) -> None:
        """
        A row's checkbox: select it, or unselect it.
        """
        entry = row.entry
        if entry is None or entry.kind == "bucket":
            return
        if self._picked.pop(entry.uri, None) is None:
            self._picked[entry.uri] = entry
        row.check._dom_classes = _CHECK + (("s3x-on",) if entry.uri in self._picked else ())
        row.button._dom_classes = self._row_classes(entry)
        self._draw_picks()

    def _on_check_all(self) -> None:
        """
        The header's checkbox: select everything the list shows (with a search, what matches it, past "Show
        more" too), or unselect it when it's all selected already.
        """
        shown = [e for e in self._visible if e.kind != "bucket"]
        if shown and all(e.uri in self._picked for e in shown):
            for entry in shown:
                self._picked.pop(entry.uri, None)
        else:
            self._picked.update((e.uri, e) for e in shown)
        self._mark_rows()
        self._draw_picks()

    def _clear_picks(self) -> None:
        self._picked.clear()
        self._mark_rows()
        self._draw_picks()

    def _draw_check_all(self) -> None:
        """
        The header's checkbox: empty, ticked when everything listed is selected, or a dash for some of it.
        """
        if self._widgets is None:
            return
        shown = [e for e in self._visible if e.kind != "bucket"]
        on = sum(e.uri in self._picked for e in shown)
        if shown and on == len(shown):
            self._check_all._dom_classes, tip = _CHECK + ("s3x-on",), "Unselect everything listed"
        elif on:
            self._check_all._dom_classes = ("s3x-check", "s3x-ic", "s3x-i-minus", "s3x-on")
            tip = f"Select all {len(shown):,} listed"
        else:
            self._check_all._dom_classes, tip = _CHECK, f"Select all {len(shown):,} listed"
        self._check_all.tooltip = tip
        self._check_all.layout.display = None if shown else "none"
        self._draw_side()

    def _picks_summary(self) -> tuple[list[Entry], int, int]:
        """
        (the selected files, how many folders are selected, the files' bytes).
        """
        files = [e for e in self._picked.values() if not e.is_folder]
        return files, len(self._picked) - len(files), sum(e.size or 0 for e in files)

    def _draw_picks(self) -> None:
        """
        After the selection changed: the bar under the list (how many, how big, Clear and ⬇ Download .zip), the
        header's checkbox, and the panel on the right if it's open.
        """
        if self._widgets is None:
            return
        files, folders, size = self._picks_summary()
        if self._picked:
            more = f" + {_plural(folders, 'folder')}" if folders else ""
            self._picks_note.value = (f'<span class="s3x-picks-n">{len(self._picked):,} selected</span>'
                                      f'<span class="s3x-picks-s">{human_size(size)}{more}</span>')
        self._picks_bar.layout.display = None if self._picked else "none"
        self._draw_check_all()
        if self._action == "picks_code":
            self._redraw_picks_code()
        elif self._picks_panel.layout.display != "none":
            if self._picked:
                self._open_picks(keep_name=True)
            else:
                self._close_picks()

    def _picks_layout(self) -> tuple[str, str, str]:
        """
        (bucket, the folder the selected entries share, the zip's name without .zip), as download_zip names it.
        """
        bucket = parse_location(self.nav.location)[0]
        base, name = _zip_layout(bucket, [e.key for e in self._picked.values()])
        return bucket, base, name

    def _picks_default(self) -> str:
        """
        The zip's name: download_zip's ('churn-12-files.zip'), with -2, -3, ... when that file is already there.
        """
        name = self._picks_layout()[2]
        folder = self.core.downloads_folder()
        number, candidate = 1, f"{name}.zip"
        while os.path.exists(os.path.join(folder, candidate)):
            number += 1
            candidate = f"{name}-{number}.zip"
        return candidate

    def _open_picks(self, keep_name: bool = False) -> None:
        """
        ⬇ Download .zip in the selection bar: on the right, what goes in the zip and how big it is, its name,
        and the button that makes it. Nothing is downloaded until that's clicked.
        """
        files, folders, size = self._picks_summary()
        bucket, base, _ = self._picks_layout()
        try:
            limit = parse_size(self.zip_max_size)
        except (TypeError, ValueError):
            limit = None
        over = limit is not None and size > limit
        parts = [_plural(len(files), "file")] if files else []
        parts += [_plural(folders, "folder")] if folders else []
        plus = "+" if folders else ""
        cards: list[tuple[str, ...]] = [("Files", f"{len(files):,}{plus}"),
                                        ("Size", human_size(size) + plus, *(("warn",) if over else ()))]
        cards += [("Limit", human_size(limit))] if limit is not None else []
        blocks: list[Any] = [_Title(f"Download {' and '.join(parts)} as one .zip", f"from s3://{bucket}/{base}"),
                             _Cards(cards)]
        if over:
            blocks.append(_Note(f"That's over the {human_size(limit)} limit for one zip, so it won't be made: "
                                   "untick some files, or raise the limit with ⚙ at the top right.", "warn"))
        if folders:
            blocks.append(_Note(f"The {'folder goes' if folders == 1 else 'folders go'} in with everything below "
                                   f"{'it' if folders == 1 else 'them'}, which is listed and counted when you click "
                                   "Download; the size here is the selected files'."))
        archived = sum(e.archived for e in files)
        if archived:
            blocks.append(_Note(f"{_plural(archived, 'selected file')} {'is' if archived == 1 else 'are'} in "
                                   "GLACIER or DEEP_ARCHIVE (❄), so the zip leaves "
                                   f"{'it' if archived == 1 else 'them'} out until restored.", "warn"))
        self._action = "picks"
        self._mark_actions()
        self._set_pane([blocks])
        rows = [[e.key[len(base):] or e.key, "" if e.is_folder else human_size(e.size),
                 human_age(e.modified) if e.modified else ""]
                for e in sorted(self._picked.values(), key=lambda e: _natural(e.key))]
        self._picks_list.value = _render_html(
            [_Table(["Name", "Size", "Modified"], rows, title="What goes in the zip", path_cols=(0,), max_rows=0)],
            0)
        if not keep_name or self._picks_name.value == self._picks_auto:
            self._picks_name.value = self._picks_auto = self._picks_default()
        self._picks_where.value = f'<span class="s3x-hint">in {html.escape(self._where())}</span>'
        self._picks_msg.value = ""
        self._picks_go.disabled = over  # the note above says what to do instead
        self._picks_panel.layout.display = None

    def _open_picks_code(self) -> None:
        """
        🐍 Use in Python in the selection bar: on the right, the lines that load what's ticked in a notebook cell.
        """
        self._report("picks_code", self._use_picks_in_python, cache=False)

    def _redraw_picks_code(self) -> None:
        """
        After the selection changed while its code is on the right: the code for the new selection, or the folder
        or file that was shown once nothing's ticked.
        """
        if self._picked:
            self._open_picks_code()
        elif self.selected:
            self._select(self.selected)
        else:
            self._show_folder(self._source())

    def _close_picks(self) -> None:
        """
        Back to the file or folder that was shown; the selection stays.
        """
        self._picks_panel.layout.display = "none"
        if self.selected:
            self._select(self.selected)
        else:
            self._show_folder(self._source())

    def _save_picks(self) -> None:
        """
        Download in the panel: zip what's selected under the name typed, unless a file of that name is there.
        """
        name = self._picks_name.value.strip()
        if not name:
            self._picks_msg.value = _render_html([_Note("Type a name for the zip.", "warn")], 0)
            return
        if not name.lower().endswith(".zip"):
            name += ".zip"
        path = os.path.join(self.core.downloads_folder(), os.path.expanduser(name))
        if os.path.exists(path):
            self._picks_msg.value = _render_html([_Note(
                f"{name} is already there, and the explorer doesn't replace files: type another name.", "warn")], 0)
            return
        picks = [ObjectInfo(e.bucket, e.key, e.size or 0, e.modified, e.storage_class or "STANDARD", e.etag)
                 if not e.is_folder and e.modified else e.uri for e in self._picked.values()]
        self._report("picks", self._zip, picks, path, cache=False)
