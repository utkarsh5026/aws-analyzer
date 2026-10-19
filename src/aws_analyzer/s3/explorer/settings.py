"""
The ⚙ settings panel: the pane's mixin.
"""

from __future__ import annotations

import html
import os
from typing import Any, Callable

from ..._kit.blocks import _Note, _Title
from ..._kit.fmt import human_size, parse_size
from ..analysis.downloads import DOWNLOADS
from ..style import _render_html


class _SettingsPane:
    """
    S3Explorer's ⚙ settings: the zip limits and the downloads folder.
    """

    def _build_settings(self, button: Callable[..., Any]) -> Any:
        """
        The ⚙ Settings panel, shown on the right under its title: a text box per setting, Save and Close.
        Enter in a box saves too.
        """
        w = self._widgets
        self._set_size = w.Text(placeholder="100MB", layout=w.Layout(width="140px"))
        self._set_files = w.Text(placeholder="10,000", layout=w.Layout(width="140px"))
        self._set_folder = w.Text(placeholder=DOWNLOADS, layout=w.Layout(width="200px"))
        rows = []
        for label, box, hint in (
            ("Biggest zip", self._set_size, "500MB, 2GB, …: a bigger folder isn't zipped"),
            ("Most files", self._set_files, "files in one zip"),
            ("Downloads in", self._set_folder, "downloads and zips; from the notebook's folder"),
        ):
            box.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._save_settings))
            hint_label = w.HTML(f'<span class="s3x-hint">{html.escape(hint)}</span>')
            row = w.HBox([w.Label(label, layout=w.Layout(width="96px")), box, hint_label], layout=w.Layout(width="100%"))
            row.add_class("s3x-setting")
            rows.append(row)
        save = button("Save", "s3x-act", "Use these settings", self._save_settings, width="auto")
        cancel = button("Close", "s3x-act", "Close the settings", self._close_settings, width="auto")
        actions = w.HBox([save, cancel], layout=w.Layout(width="100%"))
        actions.add_class("s3x-setting")
        self._set_note = w.HTML()
        panel = w.VBox([*rows, actions, self._set_note], layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        panel.add_class("s3x-settings")
        return panel

    def _toggle_settings(self) -> None:
        if self._settings.layout.display == "none":
            self._open_settings()
        else:
            self._close_settings()

    def _open_settings(self) -> None:
        """
        ⚙: the settings on the right, in place of the report.
        """
        self._action = "settings"
        self._mark_actions()
        try:
            self._set_size.value = human_size(parse_size(self.zip_max_size)).replace(".0 ", " ")
        except (TypeError, ValueError):
            self._set_size.value = str(self.zip_max_size)
        self._set_files.value = f"{self.zip_max_files:,}"
        self._set_folder.value = self.downloads
        self._set_note.value = ""
        self._set_pane([[_Title("Settings", "for this explorer, until the kernel restarts"),
                         _Note("“⬇ Download .zip” on a folder packs everything below it into one .zip on the "
                                  "notebook's disk, if it's within these limits. It also checks the disk space, memory "
                                  "and read access, and writes nothing when a check fails. To start with another "
                                  "limit, use S3Explorer(zip_max_size='2GB')."),
                         _Note(f"“⬇ Download” and the zips save into {self._where()}, so they don't mix with your "
                                  "notebooks. In a cell, x.ui.downloads() lists what's there and how much disk it "
                                  "takes, and x.ui.clean_downloads() deletes it.")]], keep=False)
        self._settings.layout.display = None

    def _close_settings(self) -> None:
        """
        Back to the file or folder that was shown.
        """
        self._settings.layout.display = "none"
        if self.selected:
            self._select(self.selected)
        else:
            self._show_folder(self._source())

    def _save_settings(self) -> None:

        def say(text: str, level: str) -> None:
            self._set_note.value = _render_html([_Note(text, level)], 0)

        size_text = self._set_size.value.strip() or "100MB"
        try:
            size = parse_size(size_text)
        except ValueError:
            size = None
        if not size or size <= 0:
            return say(f"“{size_text}” isn't a size; try 500MB or 2GB.", "warn")
        files_text = self._set_files.value.replace(",", "").replace("_", "").strip() or "10000"
        if not files_text.isdigit() or int(files_text) < 1:
            return say(f"“{self._set_files.value}” isn't a number of files; try 10,000.", "warn")
        folder = self._set_folder.value.strip() or DOWNLOADS
        if os.path.exists(os.path.expanduser(folder)) and not os.path.isdir(os.path.expanduser(folder)):
            return say(f"{folder} is a file, not a folder.", "warn")
        self.zip_max_size, self.zip_max_files, self.downloads = size, int(files_text), folder
        self._set_size.value = human_size(size).replace(".0 ", " ")
        self._set_files.value = f"{self.zip_max_files:,}"
        self._set_folder.value = folder
        where = self._where() + ("" if os.path.isdir(os.path.expanduser(folder)) else " (made with the first download)")
        say(f"Saved: “⬇ Download .zip” now packs folders up to {self._zip_limit()}, and downloads go into {where}.",
            "ok")
