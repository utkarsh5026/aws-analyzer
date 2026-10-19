"""
KBExplorer, the knowledge base explorer window, and explore() to open it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import unquote

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.blocks import _Download, _Json
from ..._bedrock.estimates import query_cost
from ..._bedrock.files import _LIST_LINK_BYTES, SavedFileList, _data_href, file_list_name
from ..._bedrock.filters import describe_sources
from ..._bedrock.words import question_terms
from ..._kit.blocks import _Cards, _Findings, _Note, _Table, _Text, _Title
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _fmt_dt, _plural, human_age, human_money
from ..._kit.text import _clip, _esc, search_rank
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..helpers import sync_command
from ..models import (
    FILE_STATES, _EPOCH, DocumentChunks, FileInventory, FileProbe, IngestionJob, KBFile, KnowledgeBaseInfo,
    MetadataFile, Retrieval,
)
from ..analysis.settings import describe_chunking, describe_parsing, describe_vector_store, store_name
from ..analysis.cost import idle_cost_label, vector_store_monthly_cost
from ..analysis.findings import _search_label, kb_findings, retrieval_findings, sync_findings
from ..analysis.files import _SORTS, find_files, inventory_findings, sort_files, sync_needed
from ..analyzer.kb_analyzer import BedrockKBAnalyzer
from ..style import _CSS, _render_html
from ..blocks import _Pipeline, _Shares, _Steps
from ..view.cells import (
    _DELETION, _KB_TYPES, _job_row, _passage_blocks, _section, _status_tone, _sync_label, _sync_tone,
)
from ..view.kb_view import BedrockKBView
from .frame import (
    _CHIP_LABELS, _EXPLORER_CSS, _EXPLORER_TABS, _FIELD_ROLES, _FILE_PAGE, _LOGO, _RESULTS, _file_face, _parser_name,
    _sync_step, _window_text,
)
from .widgets import _Chooser, _FileRow


class KBExplorer(_Explorer):
    """
    The knowledge base explorer: a window to look through a knowledge base by clicking, with nothing to type but
    a question. The knowledge base field at the top switches to another one; the cards beside it say how it's doing.

        Overview   what's wrong and what to do, how each data source becomes vectors (source, parser, chunking,
                   embedding model, vector store), and each data source's files and last sync
        Files      every file next to Bedrock's record of it: indexed, failed and why, changed in S3 since it was
                   indexed, added after the last sync, skipped, deleted. Search, filter by state or data source, sort,
                   and click a file to see how it was indexed: the parser, every chunk in document order (with the
                   text it shares with the one before), its metadata file next to what its chunks carry, and what to
                   fix. Ask it a question to see whether it holds the answer, and whether it ranks high enough among
                   the whole knowledge base's passages for an answer to see it. ⬇ List saves the s3:// paths of the
                   files listed, one per line, in a .txt file beside the notebook, with a link that downloads it.
        Syncs      the sync history: when, how long, what each read, added, changed and deleted, and why it failed
        Search     what a question retrieves, with scores and highlighted words; each passage opens its file's page
        Settings   every setting in plain English, and as AWS returns it (JSON)

    kb: the knowledge base to open (a name, ID or ARN); without it, the only one in the region, or the first active
    one. file: a file to open in the Files tab once the files are listed. view / core: a BedrockKBView or
    BedrockKBAnalyzer to use (else one is made from region / profile). height: the height of the tabs' pages, which
    fill the browser window unless set (pixels, or CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports
    elsewhere), 'widgets' or 'text'.

    Nothing here changes the knowledge base: the window only reads (where a sync is needed it shows the command), and
    the one file it writes is the list ⬇ List saves. x.ui is a BedrockKBView for reports in other cells
    (x.ui.search(...)); x.inventory, x.info, x.chunks and x.saved hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "kbx", "kb-explorer"
    _reword = staticmethod(_window_text)

    def __init__(
        self,
        kb: str | None = None,
        *,
        file: str | None = None,
        view: BedrockKBView | None = None,
        core: BedrockKBAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
        progress: str = "auto",
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        super().__init__()
        if view is None:
            view = BedrockKBView(core or BedrockKBAnalyzer(region=region, profile=profile), kb=kb,
                                 mode="text" if mode == "text" else "auto", progress=progress)
        self.ui = view
        self.core = view.core
        self.height = height
        self.kb: str | None = None  # the knowledge base shown (its ID)
        self.kbs: list[KnowledgeBaseInfo] = []  # the region's knowledge bases, as listed for the field
        self.info: KnowledgeBaseInfo | None = None  # its settings, data sources and recent syncs
        self.inventory: FileInventory | None = None  # its files
        self.selected: KBFile | None = None  # the file open in the Files tab
        self.chunks: DocumentChunks | None = None  # that file's chunks and metadata file, once read
        self.metadata: MetadataFile | None = None
        self.probe: FileProbe | None = None  # the last question asked of that file
        self.jobs: list[IngestionJob] | None = None  # the Syncs tab's history, once read
        self.found: Retrieval | None = None  # the Search tab's last search
        self.saved: SavedFileList | None = None  # the list ⬇ List saved last
        self._want_file = file  # opened once the files are listed
        self._query, self._state, self._source, self._sort, self._offset = "", "", "", "problems", 0
        self._tab = "overview"
        self._rows: list[_FileRow] = []
        self._visible: list[KBFile] = []  # the files the list's filters let through, in order
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(kb, note, mode)
            return
        self._build()
        self._begin(kb if kb is not None else view.kb)
        self._display()

    # ------------------------------------------------------------------ public commands

    def __repr__(self) -> str:
        name = self.core.kb_name(self.kb) if self.kb else "no knowledge base"
        return f"KBExplorer({name}) · help(KBExplorer) says what it shows"

    @_window_errors
    def open(self, kb: str) -> None:
        """
        Shows another knowledge base (a name, ID or ARN), as picking it in the knowledge base field does.
        """
        self._open_kb(self.core.resolve(kb))

    @_window_errors
    def file(self, path: str) -> None:
        """
        Opens a file in the Files tab (its name, path in the bucket or s3:// path), as clicking it in the list
        does: how it was indexed, its chunks and its metadata.
        """
        if self._w is None:
            self.ui.file(path, kb=self.kb)
            return
        if self.inventory is None:
            self._want_file = path
            self._show_tab("files")
            self._status("The file opens once the files are listed.")
            return
        found = find_files(self.inventory.files, path)
        if len(found) != 1:
            self.ui._find_file(self.inventory.kb_id, path)  # raises the hint that says why
        self._show_tab("files")
        self._open_file(found[0])

    @_window_errors
    def search(self, question: str) -> None:
        """
        Searches the knowledge base in the Search tab, as typing the question there does.
        """
        if self._w is None:
            self.ui.search(question, kb=self.kb)
            return
        self._quietly(self.question, value=str(question))
        self._show_tab("search")
        self._search()

    @_window_errors
    def refresh(self) -> None:
        """
        Reads the knowledge base again: settings, files, syncs (the ↻ button).
        """
        if self.kb:
            keep = self.selected.uri if self.selected else None
            self._open_kb(self.kb, file=keep)

    # ------------------------------------------------------------------ plumbing

    def _reports(self, kb: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: file('name') shows how one file was indexed, and search_file('name', "
                                 "'a question') whether a question finds it.")])
        self.ui.files(kb)
        self.kb = next(reversed(self.ui._inventories), None)  # the ID files() listed (None when it couldn't)

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it).
        """
        return _render_html(_for_window(blocks, _window_text), self.ui.max_rows).replace(_CSS, "", 1)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("kbx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the knowledge base again: settings, "
                                       "files and syncs", layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("kbx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.refresh_button], layout=layout(width="100%"))
        top.add_class("kbx-top")
        self.chooser = _Chooser(self, on_pick=self._picked_kb, on_text=self._typed_kb)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.chooser.field, self.stats], layout=layout(width="100%"))
        meta.add_class("kbx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("kbx-head")
        tabs = self._tab_bar(_EXPLORER_TABS)

        self.overview = w.HTML(layout=layout(width="100%"))
        self.syncs_view = w.HTML(layout=layout(width="100%"))
        self.settings_view = w.HTML(layout=layout(width="100%"))
        self._frame(_CSS + _EXPLORER_CSS, head, tabs, {
            "overview": w.VBox([self.overview]),
            "files": self._files_page(),
            "syncs": w.VBox([self.syncs_view]),
            "search": self._search_page(),
            "settings": w.VBox([self.settings_view]),
        })
        self._show_tab(self._tab)

    def _files_page(self) -> Any:
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
        self.chips = w.HBox(layout=layout(width="100%"))
        self.chips.add_class("kbx-chips")
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
        self.rows_box = w.VBox(layout=layout(width="100%"))
        self.rows_box.add_class("kbx-rows")
        self.pager_text = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.page_buttons = {}
        for key, glyph, tip in (("first", "«", "The first page"), ("previous", "‹", "The page before"),
                                ("next", "›", "The next page"), ("last", "»", "The last page")):
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("kbx-pg")
            button.on_click(self._safely(lambda _button, key=key: self._page_to(key)))
            self.page_buttons[key] = button
        pager = w.HBox([self.pager_text, *self.page_buttons.values()], layout=layout(width="100%"))
        pager.add_class("kbx-pager")
        self.saved_view = w.HTML(layout=layout(width="100%"))
        self.saved_view.add_class("kbx-saved")
        left = self.left = w.VBox([finder, self.chips, filters, self.rows_box, pager, self.saved_view])
        left.add_class("kbx-left")

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
        self.probe_view = w.HTML(layout=layout(width="100%"))
        self.file_view = w.HTML(layout=layout(width="100%"))
        self.right = w.VBox([self.pane_bar, self.probe_view, self.file_view])
        self.right.add_class("kbx-right")
        page = w.HBox([left, self.right])
        page.add_class("kbx-split")
        self.split = page
        return page

    def _search_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.question = w.Text(placeholder="Ask the knowledge base a question, then press Enter",
                               continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.question.on_msg(self._on_enter(self._search))
        self.search_button = w.Button(description="Search", button_style="primary", tooltip="Retrieve the passages "
                                      "that match it best (Enter does too)", layout=layout(flex="0 0 auto",
                                                                                           width="auto"))
        self.search_button.on_click(self._safely(lambda _button: self._search()))
        ask = w.HBox([self.question, self.search_button], layout=layout(width="100%"))
        for name in ("kbx-ask", "kbx-big"):
            ask.add_class(name)
        self.n_pick = w.Dropdown(options=[(f"{n} passages", n) for n in _RESULTS], value=5,
                                 layout=layout(width="130px"))
        self.kind_pick = w.ToggleButtons(options=[("Default", ""), ("Semantic", "SEMANTIC"), ("Hybrid", "HYBRID")],
                                         value="", tooltips=["Bedrock's choice for this vector store",
                                                             "Matches meaning", "Meaning and exact words (codes, "
                                                             "names): OpenSearch, Aurora and MongoDB stores"],
                                         style={"button_width": "auto"})
        self.search_source = w.Dropdown(options=[("Every data source", "")], value="",
                                        layout=layout(width="200px", display="none"))
        self.rerank_pick = w.Dropdown(options=[("No reranker", ""), ("Cohere Rerank 3.5", "cohere"),
                                               ("Amazon Rerank", "amazon")], value="", layout=layout(width="170px"))
        options = w.HBox([self.n_pick, self.kind_pick, self.search_source, self.rerank_pick],
                         layout=layout(width="100%"))
        options.add_class("kbx-options")
        self.search_head = w.HTML(layout=layout(width="100%"))
        self.hits = w.VBox(layout=layout(width="100%"))
        self.hits.add_class("kbx-hits")
        return w.VBox([ask, options, self.search_head, self.hits])

    def _show_tab(self, key: str) -> None:
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "kbx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if key == "syncs" and self.jobs is None and self.info is not None:
            self._load_syncs()
        elif not self._said[2] and self._said[1] != "warn":  # the line under the tabs says what this one does
            self._status(self._tab_line(key))

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        inv, info = self.inventory, self.info
        if key == "files" and self.selected is not None:
            return f"{self.selected.name}: {FILE_STATES.get(self.selected.state, (self.selected.state,))[0].lower()}"
        if key == "files":
            return "Click a file to see how it was indexed. Search, pick a state or sort above the list."
        if key == "overview" and inv is not None:
            problems = sum(n for state, n in inv.counts().items() if FILE_STATES[state][1] in ("bad", "warn"))
            return (f"{_plural(len(inv.files), 'file')}" + (f", {problems:,} to look at in the Files tab" if problems
                                                             else ", every one indexed and up to date"))
        if key == "syncs" and self.jobs:
            failed = sum(job.status == "FAILED" for job in self.jobs)
            return f"{_plural(len(self.jobs), 'sync')}, newest first" + (f" · {failed:,} failed" if failed else "")
        if key == "search":
            return ("Type a question and press Enter: each passage found opens its file's page."
                    if self.found is None else f"{_plural(len(self.found.passages), 'passage')} for "
                                               f"{_clip(self.found.question, 60)!r}")
        if key == "settings" and info is not None:
            return "Every setting in plain English, then as AWS returns them (folded, at the end)."
        return ""

    # ------------------------------------------------------------------ the knowledge base

    def _begin(self, kb: str | None) -> None:
        """
        Lists the knowledge bases for the field and opens the one asked for (else the only one, else the first
        active one).
        """
        try:
            self.kbs = self.core.list_knowledge_bases(details=False)
        except (ClientError, BotoCoreError, ValueError) as exc:
            reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "bedrock:ListKnowledgeBases")
            self.chooser.problem = f"Couldn't list the knowledge bases ({reason}). Type one's ID or ARN and press Enter."
            self._status(f"Couldn't list the knowledge bases ({reason}): click the knowledge base field and type one's "
                         "ID.", "warn")
        self.chooser.set_choices(self.kbs)
        target = None
        if kb is not None:
            try:
                target = self.core.resolve(kb)
            except (ValueError, ClientError, BotoCoreError) as exc:
                self._status(self._error_text(exc), "warn")
        if target is None and self.kbs:
            ready = [k for k in self.chooser.choices if k.status == "ACTIVE"] or self.chooser.choices
            target = ready[0].id
        if target is None:
            self._draw_title()
            self._set(self.overview, self._html([_Note(
                f"There are no knowledge bases in {self.core.region} to show. They're regional: "
                "explore(region='us-west-2') looks in another region, or type a knowledge base's ID in the field "
                "above.")]))
            return
        self._open_kb(target, file=self._want_file)

    def _picked_kb(self, kb_id: str) -> None:
        self._open_kb(kb_id)

    def _typed_kb(self, text: str) -> None:
        self._open_kb(self.core.resolve(text))

    def _open_kb(self, kb_id: str, file: str | None = None) -> None:
        """
        Shows a knowledge base: everything reads again, settings first, then its files, in the background.
        """
        for key in self._jobs:  # what's still being read for the one shown before is dropped when it comes back
            self._jobs[key] += 1
        self.search_button.disabled, self.search_button.description = False, "Search"
        self.ask_button.disabled, self.ask_button.description = False, "Ask"
        self.kb = self.ui.kb = kb_id
        self.ui._conversation = None
        self.info = self.inventory = self.selected = self.chunks = self.metadata = self.probe = None
        self.jobs, self.found, self.saved, self._want_file = None, None, None, file
        self._set(self.saved_view, "")
        self._query = self._state = self._source = ""
        self._offset = 0
        self._quietly(self.find, value="")
        self._quietly(self.source_pick, options=[("All data sources", "")], value="")
        self._quietly(self.search_source, options=[("Every data source", "")], value="")
        self.search_source.layout.display = self.source_pick.layout.display = "none"
        self.chooser.value = kb_id
        self.chooser.draw()
        self._draw_title()
        self._draw_stats()
        name = self.core.kb_name(kb_id)
        self._set(self.overview, _skeleton(f"Reading {name}: its settings, data sources and recent syncs…"))
        self._set(self.syncs_view, _skeleton("Reading the sync history…"))
        self._set(self.settings_view, _skeleton(f"Reading {name}'s settings…"))
        self._close_file(draw=False)
        self._set(self.file_view, _skeleton("Listing the files…"))
        self.rows_box.children = []
        self.chips.children = []
        self._draw_pager()
        self.search_head.value, self.hits.children = "", []
        self._status(f"Reading {name}…", busy=True)
        self._later("kb", lambda: self.core.describe(kb_id), self._described, self._kb_failed)

    def _kb_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview, self.settings_view, self.syncs_view, self.file_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _described(self, info: KnowledgeBaseInfo) -> None:
        self.info = info
        self._draw_title()
        self._draw_stats()
        self._draw_overview()
        self._draw_settings()
        sources = [(ds.name or ds.id, ds.id) for ds in sorted(info.data_sources, key=lambda d: (d.name or d.id).lower())]
        if len(sources) > 1:
            self._quietly(self.search_source, options=[("Every data source", ""), *sources], value="")
            self.search_source.layout.display = ""
        if self._tab == "syncs":
            self._load_syncs()
        self._counted = 0
        self._status(f"Listing {info.name}'s files: Bedrock's document list next to the files in S3…", busy=True)
        self._later("files", lambda: self.core.file_inventory(
            info.id, info=info, progress=lambda count: setattr(self, "_counted", count)),
            self._listed, self._files_failed, counting="Listing the files… {n} read so far")

    def _files_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        self._set(self.file_view, self._html([_Note(f"Couldn't list the files: {text}", "warn")]))

    def _listed(self, inv: FileInventory) -> None:
        self.inventory = inv
        self.ui._inventories[inv.kb_id] = inv
        sources = [(inv.sources[ds_id], ds_id) for ds_id in inv.sources if inv.kinds.get(ds_id) in ("S3", "CUSTOM")]
        if len(sources) > 1:
            self._quietly(self.source_pick, options=[("All data sources", ""), *sorted(sources)], value="")
            self.source_pick.layout.display = ""
        self._draw_stats()
        self._draw_overview()
        self._draw_chips()
        self._refilter()
        counts = inv.counts()
        problems = sum(n for state, n in counts.items() if FILE_STATES[state][1] in ("bad", "warn"))
        _class_if(self.tab_buttons["files"], "kbx-alarm", bool(counts.get("failed")))
        _class_if(self.tab_buttons["files"], "kbx-alert", bool(problems) and not counts.get("failed"))
        self._status(f"{_plural(len(inv.files), 'file')} listed in {inv.seconds:.1f}s"
                     + (f" · {_plural(problems, 'file')} to look at: the Files tab lists them first" if problems
                        else " · every file is indexed and up to date"), "ok" if not problems else "")
        wanted, self._want_file = self._want_file, None
        if wanted:
            found = find_files(inv.files, wanted)
            if len(found) == 1:
                self._show_tab("files")
                self._open_file(found[0])
            else:
                self._status(f"No single file matches {wanted!r}: search for it in the Files tab.", "warn")
        elif self.selected is None:
            self._draw_summary()

    # ------------------------------------------------------------------ header and overview

    def _draw_title(self) -> None:
        info = self.info
        sub = ["Every file, how it was indexed, its syncs and what a question finds · read-only"]
        if info is not None:
            sub = [info.name or info.id, _KB_TYPES.get(info.kb_type, info.kb_type.lower() or "knowledge base"),
                   store_name(info.vector_store), _clip(info.description, 90) if info.description else ""]
        self._set(self.title, f'<div class="kbx-brand"><span class="kbx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="kbx-name">Knowledge base explorer<span>{_esc(self.core.region)}</span>'
                              f'</div><div class="kbx-sub">{_esc(" · ".join(p for p in sub if p))}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the knowledge base field: how it's doing at a glance.
        """
        info, inv = self.info, self.inventory
        cards: list[tuple[str, str, str]] = []
        if info is not None and info.status != "ACTIVE":  # the field's green dot says ACTIVE
            cards.append(("Status", info.status.title() if info.status else "?", _status_tone(info.status)))
        if inv is not None:
            counts = inv.counts()
            searchable = sum(1 for f in inv.files if f.searchable)
            cards.append(("Files", f"{len(inv.files):,}", ""))
            cards.append(("Searchable", f"{searchable:,}", "ok" if searchable == len(inv.files) and inv.files else ""))
            if counts.get("failed"):
                cards.append(("Failed", f"{counts['failed']:,}", "bad"))
            waiting = counts.get("changed", 0) + counts.get("new", 0)
            if waiting:
                cards.append(("To sync", f"{waiting:,}", "warn"))
        if info is not None:
            cards.append(("Last sync", _section(info, "ingestion", _sync_label(info.last_sync)),
                          "" if "ingestion" in info.errors else _sync_tone(info.last_sync)))
            cost = vector_store_monthly_cost(info, self.core.prices)
            cards.append(("Idle cost / mo", human_money(cost) if cost is not None else "not estimated", ""))
        if info is None:
            cards += [("Last sync", "…", "sk-on"), ("Idle cost / mo", "…", "sk-on")]
        if inv is None:
            cards.insert(1, ("Files", "…", "sk-on"))
        html_cards = "".join(f'<div class="kbx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="kbx-stats">{html_cards}</div>')

    def _draw_overview(self) -> None:
        info, inv = self.info, self.inventory
        if info is None:
            return
        blocks: list[Any] = [_Title(f"{info.name}", " · ".join(filter(None, [info.id, _clip(info.description, 120)])))]
        if inv is not None and inv.files:
            counts = inv.counts()
            blocks.append(_Shares([(FILE_STATES[state][0], count, state) for state, count in reversed(counts.items())],
                                  title=f"Its {_plural(len(inv.files), 'file')}, by state (the Files tab lists them)"))
        found = kb_findings(info, prices=self.core.prices, files=inv)
        if inv is not None:
            found += inventory_findings(inv)
        blocks.append(_Findings(found, empty="No issues found by these checks: every file is indexed and up to date."
                                if inv is not None else "No issues found in the settings and syncs."))
        if inv is None:
            blocks.append(_Note("Listing the files, to say which are indexed…"))
        else:
            sync = self.ui._sync_block(info.id, sync_needed(inv), inv.sources)
            if sync:
                blocks.append(sync)
        rows = []
        counts: dict[str, Counter] = {}
        for f in inv.files if inv else []:
            counts.setdefault(f.data_source_id, Counter())[f.state] += 1
        for ds in info.data_sources:
            stages: list[tuple[str, str, str, str]] = []
            files = counts.get(ds.id)
            listed = f"{sum(files.values()):,} files" if files else ""
            if files and (files.get("failed") or files.get("changed") or files.get("new")):
                listed += ", " + ", ".join(f"{files[s]:,} {FILE_STATES[s][0].lower()}" for s in ("failed", "changed",
                                                                                                  "new") if files[s])
            stages.append(("Source", ds.source_type or "?", " · ".join(p for p in (ds.location, listed) if p),
                           "bad" if ds.status == "FAILED" else "warn" if files and files.get("failed") else ""))
            stages.append(("Parser", _section(ds, "data_source", _parser_name(ds.parsing)),
                           _section(ds, "data_source", describe_parsing(ds.parsing)), ""))
            chunking = describe_chunking(ds.chunking)
            stages.append(("Chunking", _section(ds, "data_source", chunking.split(":")[0]),
                           _section(ds, "data_source", chunking.partition(": ")[2] or chunking),
                           "warn" if ds.chunking.get("chunkingStrategy") == "NONE" else ""))
            if ds.transformation:
                stages.append(("Transformation", "custom", ds.transformation, ""))
            dims = f"{info.embedding_dims:,} dimensions" if info.embedding_dims else ""
            stages.append(("Embedding", info.embedding_model.split(".", 1)[-1] or "-", dims, ""))
            store = describe_vector_store(info.vector_store_detail) if info.vector_store_detail else ""
            name = store_name(info.vector_store)
            stages.append(("Vector store", name, store[len(name):].strip() if store.startswith(name) else store, ""))
            stages.append(("Last sync", _section(ds, "ingestion", _sync_label(ds.last_sync)), _fmt_dt(
                ds.last_sync.started) if ds.last_sync else "", "" if "ingestion" in ds.errors else _sync_tone(
                ds.last_sync)))
            rows.append((f"{ds.name or ds.id} ({ds.id})", stages))
        if rows:
            blocks.append(_Pipeline(rows, title="How each data source becomes vectors"))
        elif "data_sources" not in info.errors:
            blocks.append(_Note("It has no data sources yet, so there's nothing to search."))
        jobs = sorted((job for ds in info.data_sources for job in ds.jobs), key=lambda j: j.started or _EPOCH,
                      reverse=True)[:5]
        if jobs:
            names = {ds.id: ds.name for ds in info.data_sources}
            blocks.append(_Table(["Data source", "Started", "Took", "Status", "Scanned", "New", "Modified", "Deleted",
                                  "Failed"], [_job_row(job, names)[:9] for job in jobs],
                                 title="Recent syncs (the Syncs tab has more, with reasons)", max_rows=0))
        self._draw("overview", self.overview, blocks)

    def _draw_settings(self) -> None:
        info = self.info
        if info is None:
            return
        cost = vector_store_monthly_cost(info, self.core.prices)
        rows = [
            ["Name", info.name or "-"], ["ID", info.id], ["ARN", info.arn or "-"], ["Status", info.status or "-"],
            ["Type", _KB_TYPES.get(info.kb_type, info.kb_type or "-")],
            ["Embedding model", info.embedding_model or "-"],
            ["Dimensions", f"{info.embedding_dims:,} numbers in each vector" if info.embedding_dims else "-"],
            ["Vector store", describe_vector_store(info.vector_store_detail)],
            ["Idle cost", f"about {human_money(cost)}/month ({self.ui._price_basis()})" if cost is not None
             else idle_cost_label(info, self.core.prices)],
            ["Service role", info.role_arn or "-"], ["Created", _fmt_dt(info.created)],
            ["Last changed", _fmt_dt(info.updated)],
        ]
        blocks: list[Any] = [_Title(f"Settings of {info.name}", "in plain English, then as AWS returns them"),
                             _Table(["Setting", "Value"], rows, title="Knowledge base", max_rows=0)]
        fields = (info.vector_store_detail.get(next((k for k in info.vector_store_detail if k.endswith("Configuration")),
                                                    ""), {}) or {}).get("fieldMapping") or {}
        if fields:
            blocks.append(_Table(["Part of a chunk", "Kept in", "Setting"],
                                 [[_FIELD_ROLES.get(k, k), v, k] for k, v in fields.items()],
                                 title="Where the vector store keeps each part of a chunk", max_rows=0))
        for ds in info.data_sources:
            blocks.append(_Table(["Setting", "Value"], [
                ["ID", ds.id], ["Type", ds.source_type or "?"],
                ["Location", _section(ds, "data_source", ds.location or "-")],
                ["Chunking", _section(ds, "data_source", describe_chunking(ds.chunking))],
                ["Parsing", _section(ds, "data_source", describe_parsing(ds.parsing))],
                ["Transformation", ds.transformation or "none"],
                ["When deleted", _DELETION.get(ds.deletion_policy or "", ds.deletion_policy or "-")],
                ["Status", ds.status or "-"], ["Created", _fmt_dt(ds.created)], ["Last changed", _fmt_dt(ds.updated)],
            ], title=f"Data source {ds.name or ds.id}", max_rows=0))
        if info.tags:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(info.tags.items())], title="Tags",
                                 max_rows=0))
        if info.raw:
            blocks.append(_Json(info.raw, "GetKnowledgeBase, as AWS returns it", open_depth=1, collapsed=True))
        for ds in info.data_sources:
            if ds.raw:
                blocks.append(_Json(ds.raw, f"GetDataSource for {ds.name or ds.id}", open_depth=1, collapsed=True))
        region = self.core.region
        commands = [f"aws bedrock-agent get-knowledge-base --knowledge-base-id {info.id} --region {region}"] + [
            f"aws bedrock-agent get-data-source --knowledge-base-id {info.id} --data-source-id {ds.id} "
            f"--region {region}" for ds in info.data_sources]
        blocks.append(_Text("\n".join(commands), title="The same from a terminal (read-only)", code=True))
        self._draw("settings", self.settings_view, blocks)

    # ------------------------------------------------------------------ syncs

    def _load_syncs(self) -> None:
        info = self.info
        if info is None:
            return
        self.jobs = []  # asked for: not asked again while it's read
        self._set(self.syncs_view, _skeleton("Reading the sync history…"))
        if self._tab == "syncs":
            self._status(f"Reading {info.name}'s sync history…", busy=True)
        self._later("syncs", lambda: self.core.ingestion_jobs(info.id, n=50), self._got_syncs, self._syncs_failed)

    def _syncs_failed(self, exc: BaseException) -> None:
        self.jobs = None
        text = f"Couldn't read the sync history: {self._error_text(exc)}"
        self._set(self.syncs_view, self._html([_Note(text, "warn")]))
        if self._tab == "syncs":
            self._status(text, "warn")

    def _got_syncs(self, jobs: list[IngestionJob]) -> None:
        self.jobs = jobs
        info = self.info
        if info is None:
            return
        names = {ds.id: ds.name for ds in info.data_sources}
        last_ok = next((job for job in jobs if job.status == "COMPLETE"), None)
        latest: dict[str, IngestionJob] = {}  # each data source's newest finished sync, which the findings are about
        for job in jobs:
            if job.status in ("COMPLETE", "FAILED", "STOPPED"):
                latest.setdefault(job.data_source_id, job)
        failed_docs = sum(job.failed for job in latest.values())
        blocks: list[Any] = [
            _Title(f"Syncs of {info.name}", f"newest first · {_plural(len(jobs), 'sync')} of "
                                            f"{_plural(len(info.data_sources), 'data source')}"),
            _Cards([
                ("Syncs", f"{len(jobs):,}"),
                ("Failed", f"{sum(j.status == 'FAILED' for j in jobs):,}",
                 "warn" if any(j.status == "FAILED" for j in latest.values()) else ""),
                ("Last successful", human_age(last_ok.started) if last_ok else "none"),
                ("Documents failed", f"{failed_docs:,}" if latest else "-", "warn" if failed_docs else ""),
            ]),
        ]
        if not jobs:
            blocks.append(_Note("No syncs yet, so nothing is searchable. Sync each data source: " + "; ".join(
                sync_command(info.id, ds.id, self.core.region) for ds in info.data_sources[:3]), "warn"))
        else:
            blocks.append(_Findings(sync_findings(jobs, names), empty="No issues found by these checks."))
            blocks.append(_Steps([_sync_step(job, names) for job in jobs], title="Every sync, newest first"))
        sync = self.ui._sync_block(info.id, [ds.id for ds in info.data_sources], names)
        if sync:
            blocks.append(sync)
        self._draw("syncs", self.syncs_view, blocks)
        if self._tab == "syncs":
            self._status(self._tab_line("syncs") or "No syncs yet")

    # ------------------------------------------------------------------ the file list

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

    def _draw_chips(self) -> None:
        """
        A chip per state the files are in, with how many: a click shows only those (again shows all).
        """
        w, layout = self._w, self._w.Layout
        counts = Counter(f.state for f in self._in_source())
        chips = []
        for state, label, tone in [("", "All", "all")] + [(s, *FILE_STATES[s]) for s in FILE_STATES if counts[s]]:
            n = sum(counts.values()) if not state else counts[state]
            chip = w.Button(description=f"{_CHIP_LABELS.get(state, label)} {n:,}",
                            tooltip=f"Show only the files that are {label.lower()} (click again for every file)"
                            if state else "Show every file", layout=layout(width="auto"))
            for name in ("kbx-chip", f"kbx-t-{tone or 'none'}", f"kbx-s-{state or 'all'}"):
                chip.add_class(name)
            _class_if(chip, "kbx-on", state == self._state)
            chip.on_click(self._safely(lambda _button, state=state: self._pick_state(state)))
            chips.append(chip)
        self.chips.children = chips

    def _refilter(self) -> None:
        names = self.inventory.sources if self.inventory else {}
        files = [f for f in self._in_source() if not self._state or f.state == self._state]
        if self._query:
            files = [f for f in files if search_rank(self._query, (
                f.key, f.name, f.reason, FILE_STATES.get(f.state, ("",))[0], names.get(f.data_source_id))) is not None]
        self._visible = sort_files(files, self._sort)
        self._offset = min(self._offset, max(0, (len(self._visible) - 1) // _FILE_PAGE * _FILE_PAGE))
        self._draw_rows()

    def _draw_rows(self) -> None:
        page = self._visible[self._offset:self._offset + _FILE_PAGE]
        while len(self._rows) < len(page):
            self._rows.append(_FileRow(self))
        words = self._query.split()
        several = self.inventory is not None and len([k for k in self.inventory.kinds.values()
                                                      if k in ("S3", "CUSTOM")]) > 1 and not self._source
        names = self.inventory.sources if self.inventory else {}
        for row, f in zip(self._rows, page):
            row.file = f
            self._set(row.face, _file_face(f, words, names.get(f.data_source_id, "") if several else ""))
            label = FILE_STATES.get(f.state, (f.state,))[0]
            tip = f"{f.key}: {label}. {f.note} Click to see how it was indexed."
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            _class_if(row.box, "kbx-on", self.selected is not None and f.uri == self.selected.uri)
        children: list[Any] = [row.box for row in self._rows[:len(page)]]
        if not page:
            if self.inventory is None:
                text = "Listing the files…"
            elif not self.inventory.files:
                text = "No files: this knowledge base has no S3 or custom data source, or nothing in them yet."
            else:
                text = "No file matches." + (" Clear the search, or pick All above." if self._query or self._state
                                             else "")
            children = [self._w.HTML(f'<div class="kbx-empty">{_esc(text)}</div>')]
        self.rows_box.children = children
        self._draw_pager()

    def _draw_pager(self) -> None:
        total = len(self._visible)
        first, last = self._offset + 1, min(self._offset + _FILE_PAGE, total)
        whole = len(self._in_source()) if self.inventory else 0
        text = (f"<b>{first:,}–{last:,}</b> of {total:,}" if total else "0 files") + (
            f" (of {whole:,})" if self.inventory and total != whole else "")
        self._set(self.pager_text, text)
        more = total > _FILE_PAGE
        for key, button in self.page_buttons.items():
            button.layout.display = "" if more else "none"
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

    # ------------------------------------------------------------------ one file

    def _draw_summary(self) -> None:
        """
        The right side of the Files tab with no file open: the files' findings and counts.
        """
        inv = self.inventory
        if inv is None:
            return
        blocks = self.ui._files_blocks(inv, window=True)
        hint = ("Click a file on the left to see how it was indexed: the parser, its chunks in order, its metadata, "
                "and what to fix. Ask it a question there to see whether it ranks.")
        self.shown["file"] = blocks
        self._set(self.file_view, f'<div class="kbx-hint">👈 <div>{_esc(hint)}</div></div>' + self._html(blocks))
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
            _class_if(row.box, "kbx-on", self.selected is not None and row.file is not None
                      and row.file.uri == self.selected.uri)

    def _open_file(self, f: KBFile) -> None:
        """
        A file's page on the right: what's known at once, then its chunks and metadata file as they're read.
        """
        self.selected, self.chunks, self.metadata, self.probe = f, None, None, None
        self._mark_rows()
        self.pane_bar.layout.display = ""
        self.back_button.layout.display = ""
        self.ask_row.layout.display = "" if f.searchable and f.uri.startswith("s3://") else "none"
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

    def _got_file(self, f: KBFile, got: tuple[DocumentChunks | None, MetadataFile | None, list[_Note]],
                  was: str = "") -> None:
        if self.selected is not f:
            return
        self.chunks, self.metadata, notes = got
        if was and f.state != was:  # an unchecked file, looked up: its state is known now
            self._draw_stats()
            self._draw_chips()
            self._draw_rows()
            self.ask_row.layout.display = "" if f.searchable and f.uri.startswith("s3://") else "none"
        self._draw("file", self.file_view, self.ui._file_blocks(f, self.info, self.chunks, self.metadata, notes=notes,
                                                                window=True))
        count = self.chunks.stats.count if self.chunks else 0
        self._status(f"{f.name}: {FILE_STATES.get(f.state, (f.state,))[0].lower()}"
                     + (f", {_plural(count, 'chunk')}" if self.chunks is not None else "")
                     + (f" ({self.chunks.seconds:.1f}s)" if self.chunks is not None else ""))

    def _ask_file(self) -> None:
        f, question = self.selected, str(self.ask_box.value or "").strip()
        if f is None:
            return
        if not question:
            self._status("Type a question this file should answer, then press Enter.")
            return
        kb_id = self.kb or ""
        self.ask_button.disabled, self.ask_button.description = True, "Asking…"
        self._set(self.probe_view, _skeleton(f"Searching {f.name}, and the whole knowledge base, for the question…"))
        self._renew_pane()  # back to the top, where the answer goes

        def done(probe: FileProbe) -> None:
            self.ask_button.disabled, self.ask_button.description = False, "Ask"
            if self.selected is not f:
                return
            self.probe = probe
            self._draw("probe", self.probe_view, self.ui._probe_blocks(probe, window=True))
            self._status(f"Asked {f.name}: " + (f"its best passage ranks #{probe.rank} across the knowledge base"
                                                 if probe.rank else "not in the knowledge base's top results"))

        def failed(exc: BaseException) -> None:
            self.ask_button.disabled, self.ask_button.description = False, "Ask"
            self._set(self.probe_view, self._html([_Note(self._error_text(exc), "warn")]))

        self._later("probe", lambda: self.core.probe_file(kb_id, f.uri, question), done, failed)

    # ------------------------------------------------------------------ search

    def _search(self) -> None:
        question = str(self.question.value or "").strip()
        if not question:
            self._status("Type a question first, then press Enter.")
            return
        if not self.kb:
            self._status("Pick a knowledge base first.", "warn")
            return
        kb_id, n = self.kb, int(self.n_pick.value)
        kind, source, rerank = self.kind_pick.value or None, self.search_source.value or None, self.rerank_pick.value
        self.search_button.disabled, self.search_button.description = True, "Searching…"
        self._set(self.search_head, _skeleton("Searching…"))
        self.hits.children = []

        def done(r: Retrieval) -> None:
            self.search_button.disabled, self.search_button.description = False, "Search"
            self.found = r
            self._draw_hits(r)

        def failed(exc: BaseException) -> None:
            self.search_button.disabled, self.search_button.description = False, "Search"
            self._set(self.search_head, self._html([_Note(self._error_text(exc), "warn")]))

        self._later("search", lambda: self.core.retrieve(kb_id, question, n, search_type=kind, data_source=source,
                                                         rerank_model=rerank or None), done, failed)

    def _draw_hits(self, r: Retrieval) -> None:
        w, layout = self._w, self._w.Layout
        terms = question_terms(r.question)
        top = max((p.score for p in r.passages if p.score is not None), default=None)
        sub = [f"{len(r.passages)} of up to {r.n} passages", _search_label(r.search_type)]
        if r.data_sources:
            sub.append(f"only {describe_sources(r.data_sources)}")
        if r.reranked:
            sub.append(f"reranked by {r.reranked}")
        blocks: list[Any] = [
            _Title(_clip(r.question, 90), " · ".join(sub)),
            _Cards([("Passages", f"{len(r.passages):,}"), ("Top score", "-" if top is None else f"{top:.2f}"),
                    ("Files", f"{len({p.uri for p in r.passages}):,}"), ("Time", f"{r.seconds:.1f}s"),
                    ("Est. cost", human_money(query_cost(1, r.reranked or False, self.core.prices)))]),
            _Findings(retrieval_findings(r)),
        ]
        self._draw("search", self.search_head, blocks)
        names = self.ui._source_names(r.kb_id, r.passages)
        cards = _passage_blocks(r.passages, terms, width=420, sources=names, link=self.ui._link)
        hits = []
        files = {f.uri: f for f in self.inventory.files} if self.inventory else {}
        plain = {unquote(uri): f for uri, f in files.items()}
        for card, p in zip(cards, r.passages):
            body = w.HTML(self._html([card]), layout=layout(width="100%"))
            children = [body]
            known = files.get(p.uri) or plain.get(unquote(p.uri or ""))
            if known is not None:
                button = w.Button(description=f"How {_clip(known.name, 40)} was indexed ›", tooltip=f"Open {known.key}"
                                  " in the Files tab: its state, chunks and metadata", layout=layout(width="auto"))
                button.add_class("kbx-link")
                button.on_click(self._safely(lambda _button, f=known: self._jump_to(f)))
                children.append(button)
            box = w.VBox(children, layout=layout(width="100%"))
            box.add_class("kbx-hit")
            hits.append(box)
        self.hits.children = hits
        self._status(f"Found {_plural(len(r.passages), 'passage')} in {r.seconds:.1f}s"
                     + (": click How it was indexed under one to open its file" if any(
                         len(h.children) > 1 for h in hits) else ""))

    def _jump_to(self, f: KBFile) -> None:
        self._show_tab("files")
        self._open_file(f)


def explore(
    kb: str | None = None,
    *,
    file: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> KBExplorer:
    """
    Opens the knowledge base explorer window and returns it: every file next to Bedrock's record of it, how each
    was indexed (its chunks in document order, its metadata), the indexing pipeline, the sync history, search and
    every setting, all by clicking.

        explore()                                   # the only knowledge base in the region, or the first active one
        explore("support-docs")                     # by name, ID or ARN
        explore("support-docs", file="refund-policy.pdf")   # with one file open in the Files tab
        explore(region="eu-west-1", profile="dev")  # another region or AWS profile

    The tabs' pages fill the browser's height; height= sets theirs instead (800 pixels, or CSS such as '70vh'). It only
    reads: where a sync is needed it shows the command to run.
    """
    return KBExplorer(kb, file=file, region=region, profile=profile, height=height)
