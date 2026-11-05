"""
The right side of the Files tab: the files' summary, or one file's page (how it was indexed) and its question box.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _plural
from ..._kit.text import _esc
from ..._kit.window import _class_if, _skeleton

from ..models import FILE_STATES, DocumentChunks, FileProbe, KBFile, MetadataFile

_SUMMARY_HINT = ("Click a file on the left to see how it was indexed: the parser, its chunks in order, its metadata, "
                 "and what to fix. Ask it a question there to see whether it ranks.")


def _can_ask(f: KBFile) -> bool:
    """
    Whether a question can be asked of a file: it's searchable, and in S3, where a filter can name it.
    """
    return f.searchable and f.uri.startswith("s3://")


class _FilePane:
    """
    The right side of KBExplorer's Files tab: with no file open, the files' findings and counts; with one, how it was
    indexed (its chunks in order and its metadata file, read in the background) and a box to ask it a question.
    """

    def _file_bar(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.back_button = w.Button(description="‹ All files", tooltip="Back to the summary of every file",
                                    layout=layout(flex="0 0 auto", display="none"))
        for name in ("kbx-small", "kbx-ghost"):
            self.back_button.add_class(name)
        self.back_button.on_click(self._safely(lambda _button: self._close_file()))
        self.ask_box = w.Text(placeholder="Ask a question to see whether this file answers it",
                              continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.ask_box.on_msg(self._on_enter(self._ask_file))
        self.ask_button = w.Button(description="Ask", button_style="primary", tooltip="Search this file, and the "
                                   "whole knowledge base, for the question (Enter does too)",
                                   layout=layout(flex="0 0 auto", width="auto"))
        self.ask_button.on_click(self._safely(lambda _button: self._ask_file()))
        self.ask_row = w.HBox([self.ask_box, self.ask_button], layout=layout(display="none"))
        self.ask_row.add_class("kbx-ask")
        self.pane_bar = w.HBox([self.back_button, self.ask_row], layout=layout(width="100%", display="none"))
        self.pane_bar.add_class("kbx-bar")
        return self.pane_bar

    def _file_pane(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar = self._file_bar()
        self.probe_view = w.HTML(layout=layout(width="100%"))
        self.file_view = w.HTML(layout=layout(width="100%"))
        self.right = w.VBox([bar, self.probe_view, self.file_view])
        self.right.add_class("kbx-right")
        return self.right

    def _files_line(self) -> str:
        f = self.selected
        if f is None:
            return "Click a file to see how it was indexed. Search, pick a state or sort above the list."
        return f"{f.name}: {FILE_STATES.get(f.state, (f.state,))[0].lower()}"

    def _draw_summary(self) -> None:
        """
        The right side of the Files tab with no file open: the files' findings and counts.
        """
        inv = self.inventory
        if inv is None:
            return
        blocks = self.ui._files_blocks(inv, window=True)
        self.shown["file"] = blocks
        self._set(self.file_view, f'<div class="kbx-hint">👈 <div>{_esc(_SUMMARY_HINT)}</div></div>'
                                  + self._html(blocks))
        self._set(self.probe_view, "")

    def _close_file(self, draw: bool = True) -> None:
        self.selected, self.chunks, self.metadata, self.probe = None, None, None, None
        self.pane_bar.layout.display = "none"
        self.back_button.layout.display = self.ask_row.layout.display = "none"
        self._set(self.probe_view, "")
        if draw:
            self._renew_pane(keep=False)
            self._draw_summary()
            self._mark_rows()

    def _mark_rows(self) -> None:
        for row in self._rows:
            is_open = self.selected is not None and row.file is not None and row.file.uri == self.selected.uri
            _class_if(row.box, "kbx-on", is_open)

    def _open_file(self, f: KBFile) -> None:
        """
        A file's page on the right: what's known at once, then its chunks and metadata file as they're read.
        """
        self.selected, self.chunks, self.metadata, self.probe = f, None, None, None
        self._mark_rows()
        self.pane_bar.layout.display = ""
        self.back_button.layout.display = ""
        self.ask_row.layout.display = "" if _can_ask(f) else "none"
        self._renew_pane(keep=False)
        reads = f.uri.startswith("s3://")
        self._draw("file", self.file_view, self.ui._file_blocks(f, self.info, None, None, loading=reads, window=True))
        if not reads:
            return
        kb_id = self.kb or ""
        self._status(f"Reading {f.name}'s chunks and metadata file…", busy=True)
        was = f.state
        self._later("file", lambda: self.ui._file_view(kb_id, f), lambda got: self._got_file(f, got, was),
                    lambda exc: self._file_failed(f, exc))

    def _renew_pane(self, keep: bool = True) -> None:
        """
        A new box for the right side of the Files tab, which starts at the top (widgets can't be scrolled from
        Python). keep: with what the old one showed.
        """
        w = self._w
        probe = w.HTML(self.probe_view.value if keep else "", layout=w.Layout(width="100%"))
        page = w.HTML(self.file_view.value if keep else "", layout=w.Layout(width="100%"))
        self.probe_view, self.file_view = probe, page
        self.right = w.VBox([self.pane_bar, probe, page])
        self.right.add_class("kbx-right")
        self.split.children = [self.left, self.right]

    def _file_failed(self, f: KBFile, exc: BaseException) -> None:
        if self.selected is not f:
            return
        text = self._error_text(exc)
        self._status(text, "warn")
        self._draw("file", self.file_view, self.ui._file_blocks(f, self.info, None, None,
                                                                notes=[_Note(text, "warn")], window=True))

    def _state_learned(self, f: KBFile) -> None:
        """
        An unchecked file was looked up when it was opened, so its state is known now: the cards, chips and rows
        that count it are drawn again.
        """
        self._draw_stats()
        self._draw_chips()
        self._draw_rows()
        self.ask_row.layout.display = "" if _can_ask(f) else "none"

    def _got_file(self, f: KBFile, got: tuple[DocumentChunks | None, MetadataFile | None, list[_Note]],
                  was: str = "") -> None:
        if self.selected is not f:
            return
        self.chunks, self.metadata, notes = got
        if was and f.state != was:
            self._state_learned(f)
        self._draw("file", self.file_view, self.ui._file_blocks(f, self.info, self.chunks, self.metadata, notes=notes,
                                                                window=True))
        state = FILE_STATES.get(f.state, (f.state,))[0].lower()
        if self.chunks is None:
            self._status(f"{f.name}: {state}")
            return
        self._status(f"{f.name}: {state}, {_plural(self.chunks.stats.count, 'chunk')} ({self.chunks.seconds:.1f}s)")

    def _asked_file(self, f: KBFile, probe: FileProbe) -> None:
        self.ask_button.disabled, self.ask_button.description = False, "Ask"
        if self.selected is not f:
            return
        self.probe = probe
        self._draw("probe", self.probe_view, self.ui._probe_blocks(probe, window=True))
        where = (f"its best passage ranks #{probe.rank} across the knowledge base" if probe.rank
                 else "not in the knowledge base's top results")
        self._status(f"Asked {f.name}: {where}")

    def _ask_failed(self, exc: BaseException) -> None:
        self.ask_button.disabled, self.ask_button.description = False, "Ask"
        self._set(self.probe_view, self._html([_Note(self._error_text(exc), "warn")]))

    def _ask_file(self) -> None:
        """
        The file's question box: searches the file, and the whole knowledge base, for the question, and draws the
        answer at the top of the page.
        """
        f, question = self.selected, str(self.ask_box.value or "").strip()
        if f is None:
            return
        if not question:
            self._status("Type a question this file should answer, then press Enter.")
            return
        kb_id = self.kb or ""
        self.ask_button.disabled, self.ask_button.description = True, "Asking…"
        self._set(self.probe_view, _skeleton(f"Searching {f.name}, and the whole knowledge base, for the question…"))
        self._renew_pane()
        self._later("probe", lambda: self.core.probe_file(kb_id, f.uri, question),
                    lambda probe: self._asked_file(f, probe), self._ask_failed)
