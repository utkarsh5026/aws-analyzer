"""
KBExplorer, the knowledge base explorer window, and explore() to open it.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.files import SavedFileList
from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _plural
from ..._kit.window import _Explorer, _check_assistant, _class_if, _for_window, _skeleton, _window_errors

from ..models import (
    FILE_STATES, DocumentChunks, FileInventory, FileProbe, IngestionJob, KBFile, KnowledgeBaseInfo, MetadataFile,
    Retrieval, SearchComparison,
)
from ..analysis.files import find_files
from ..analyzer.kb_analyzer import BedrockKBAnalyzer
from ..style import _CSS, _render_html
from ..view.kb_view import BedrockKBView
from .frame import _EXPLORER_CSS, _EXPLORER_TABS, _window_text
from .focus import _FocusReads
from .widgets import _Chooser, _FileRow
from .overview import _OverviewTab
from .files import _FilesTab
from .file import _FilePane
from .syncs import _SyncsTab
from .search import _SearchTab
from .settings import _SettingsTab

_LISTED_KINDS = ("S3", "CUSTOM")


class KBExplorer(_OverviewTab, _FilesTab, _FilePane, _SyncsTab, _SearchTab, _SettingsTab, _FocusReads, _Explorer):
    """
    The knowledge base explorer: a window to look through a knowledge base by clicking, with nothing to type but
    a question. The knowledge base field at the top switches to another one; the cards beside it say how it's doing.

        Overview   the files in one sentence, a card for each thing to do (sync a data source, fix the files that
                   failed, convert the ones Bedrock can't read), how each data source becomes vectors (source,
                   parser, chunking, embedding model, vector store) with each problem under the step where it
                   happens, and the recent syncs
        Files      every file next to Bedrock's record of it: indexed, failed and why, changed in S3 since it was
                   indexed, added after the last sync, skipped, deleted. Search, filter by state or data source, sort,
                   and click a file to see how it was indexed: the parser, every chunk in document order (with the
                   text it shares with the one before), its metadata file next to what its chunks carry, and what to
                   fix. Ask it a question to see whether it holds the answer, and whether it ranks high enough among
                   the whole knowledge base's passages for an answer to see it. ⬇ List saves the s3:// paths of the
                   files listed, one per line, in a .txt file beside the notebook, with a link that downloads it.
        Syncs      the sync history: when, how long, what each read, added, changed and deleted, and why it failed
        Search     what a question retrieves, with scores and highlighted words; each passage opens its file's page.
                   A metadata filter (team = billing; year >= 2024) keeps to the files whose metadata matches, and
                   Compare searches the question as Semantic and Hybrid, with more passages too, to show which
                   setting finds what
        Settings   every setting in plain English, and as AWS returns it (JSON)

    kb: the knowledge base to open (a name, ID or ARN); without it, the only one in the region, or the first active
    one. file: a file to open in the Files tab once the files are listed. view / core: a BedrockKBView or
    BedrockKBAnalyzer to use (else one is made from region / profile). height: the height of the tabs' pages, which
    fill the browser window unless set (pixels, or CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports
    elsewhere), 'widgets' or 'text'. assistant: an AgentView to ask about what the window shows: ✦ Ask, at the end of
    the tab bar, opens a drawer where "why isn't this file searchable?" means the file you have open
    (x.attach(agent) does it later, and aws_analyzer.set_assistant(agent) for every explorer).

    Nothing here changes the knowledge base: the window only reads (where a sync is needed it shows the command), and
    the one file it writes is the list ⬇ List saves. x.ui is a BedrockKBView for reports in other cells
    (x.ui.search(...)); x.inventory, x.info, x.chunks and x.saved hold the data behind what's shown, and x.focus
    what you're looking at, as data: the knowledge base, the tab and the file or search open (x.focus.chips() reads
    it as a row of chips).
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
        assistant: Any = None,
    ):
        """
        What the window holds: kb (the knowledge base shown, its ID), kbs (the region's, as listed for the field),
        info (its settings, data sources and recent syncs), inventory (its files), selected (the file open in the
        Files tab, with its chunks and metadata once read, and probe, the last question asked of it), jobs (the Syncs
        tab's history, once read), found (the Search tab's last search), compared (its last Compare) and saved (the
        list ⬇ List saved last).
        _visible is the files the list's filters let through, in order; _want_file opens once the files are listed.
        """
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        super().__init__()
        if assistant is not None:
            _check_assistant(assistant)
            self.assistant = assistant
        if view is None:
            view = BedrockKBView(core or BedrockKBAnalyzer(region=region, profile=profile), kb=kb,
                                 mode="text" if mode == "text" else "auto", progress=progress)
        self.ui = view
        self.core = view.core
        self.height = height
        self.kb: str | None = None
        self.kbs: list[KnowledgeBaseInfo] = []
        self.info: KnowledgeBaseInfo | None = None
        self.inventory: FileInventory | None = None
        self.selected: KBFile | None = None
        self.chunks: DocumentChunks | None = None
        self.metadata: MetadataFile | None = None
        self.probe: FileProbe | None = None
        self.jobs: list[IngestionJob] | None = None
        self.found: Retrieval | None = None
        self.compared: SearchComparison | None = None
        self.saved: SavedFileList | None = None
        self._want_file = file
        self._query, self._state, self._source, self._sort, self._offset = "", "", "", "problems", 0
        self._tab = "overview"
        self._rows: list[_FileRow] = []
        self._visible: list[KBFile] = []
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(kb, note, mode)
            return
        self._build()
        self._begin(kb if kb is not None else view.kb)
        self._display()

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
            self.ui._find_file(self.inventory.kb_id, path)
        self._show_tab("files")
        self._open_file(found[0])

    @_window_errors
    def search(self, question: str, *, where: str | None = None) -> None:
        """
        Searches the knowledge base in the Search tab, as typing the question there does. where= types a metadata
        filter in its box ('team = billing; year >= 2024'); without it, the box keeps what it holds.
        """
        if self._w is None:
            self.ui.search(question, kb=self.kb, where=where)
            return
        self._fill_search(question, where)
        self._search()

    @_window_errors
    def compare(self, question: str, *, where: str | None = None) -> None:
        """
        Compares search settings for a question in the Search tab, as its Compare button does: Semantic and Hybrid,
        with the passages picked and twice as many, and where each passage ranks under each.
        """
        if self._w is None:
            self.ui.compare(question, kb=self.kb, where=where)
            return
        self._fill_search(question, where)
        self._compare()

    def _fill_search(self, question: str, where: str | None) -> None:
        self._quietly(self.question, value=str(question))
        if where is not None:
            self.where_box.value = str(where)
        self._show_tab("search")

    @_window_errors
    def refresh(self) -> None:
        """
        Reads the knowledge base again: settings, files, syncs (the ↻ button).
        """
        if self.kb:
            keep = self.selected.uri if self.selected else None
            self._open_kb(self.kb, file=keep)

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
        self.kb = next(reversed(self.ui._inventories), None)

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it).
        """
        return _render_html(_for_window(blocks, _window_text), self.ui.max_rows).replace(_CSS, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("kbx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self._frame(_CSS + _EXPLORER_CSS, self._head(), self._tab_bar(_EXPLORER_TABS), self._pages())
        self._show_tab(self._tab)

    def _head(self) -> Any:
        w, layout = self._w, self._w.Layout
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
        return head

    def _pages(self) -> dict[str, Any]:
        w, layout = self._w, self._w.Layout
        self.overview = w.HTML(layout=layout(width="100%"))
        self.syncs_view = w.HTML(layout=layout(width="100%"))
        self.settings_view = w.HTML(layout=layout(width="100%"))
        return {
            "overview": w.VBox([self.overview]),
            "files": self._files_page(),
            "syncs": w.VBox([self.syncs_view]),
            "search": self._search_page(),
            "settings": w.VBox([self.settings_view]),
        }

    def _show_tab(self, key: str) -> None:
        """
        Shows a tab's page. The Syncs tab reads the history the first time it's opened; otherwise the status line
        says what the tab holds, unless it's busy or warning.
        """
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "kbx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        status_is_free = not self._said[2] and self._said[1] != "warn"
        if key == "syncs" and self.jobs is None and self.info is not None:
            self._load_syncs()
        elif status_is_free:
            self._status(self._tab_line(key))

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        lines = {"overview": self._overview_line, "files": self._files_line, "syncs": self._syncs_line,
                 "search": self._search_line, "settings": self._settings_line}
        line = lines.get(key)
        return line() if line else ""

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
        target = self._resolved(kb) if kb is not None else None
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

    def _resolved(self, kb: str) -> str | None:
        """
        A knowledge base's ID from its name, ID or ARN, or None (said in the status line) when there's no such one.
        """
        try:
            return self.core.resolve(kb)
        except (ValueError, ClientError, BotoCoreError) as exc:
            self._status(self._error_text(exc), "warn")
            return None

    def _picked_kb(self, kb_id: str) -> None:
        self._open_kb(kb_id)

    def _typed_kb(self, text: str) -> None:
        self._open_kb(self.core.resolve(text))

    def _forget_shown(self, kb_id: str, file: str | None) -> None:
        """
        Drops what the window holds about the knowledge base shown before: its data, the list's filters, the
        searches' fields. Every background job's key is bumped, so what's still being read for it is dropped when it
        comes back.
        """
        for key in self._jobs:
            self._jobs[key] += 1
        self._searching(False)
        self.ask_button.disabled, self.ask_button.description = False, "Ask"
        self.kb = self.ui.kb = kb_id
        self.ui._conversation = None
        self.info = self.inventory = self.selected = self.chunks = self.metadata = self.probe = None
        self.jobs, self.found, self.compared, self.saved, self._want_file = None, None, None, None, file
        self._query = self._state = self._source = ""
        self._offset = 0
        self._quietly(self.find, value="")
        self._quietly(self.source_pick, options=[("All data sources", "")], value="")
        self._quietly(self.search_source, options=[("Every data source", "")], value="")
        self._quietly(self.where_box, value="")
        self._mark_filter()
        self.search_source.layout.display = self.source_pick.layout.display = "none"

    def _open_kb(self, kb_id: str, file: str | None = None) -> None:
        """
        Shows a knowledge base: everything reads again, settings first, then its files, in the background.
        """
        self._forget_shown(kb_id, file)
        self._set(self.saved_view, "")
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

    def _fill_source_pick(self, inv: FileInventory) -> None:
        sources = [(inv.sources[ds_id], ds_id) for ds_id in inv.sources if inv.kinds.get(ds_id) in _LISTED_KINDS]
        if len(sources) > 1:
            self._quietly(self.source_pick, options=[("All data sources", ""), *sorted(sources)], value="")
            self.source_pick.layout.display = ""

    def _say_listed(self, inv: FileInventory) -> None:
        """
        The Files tab's button turns red when a file failed, amber when one needs a look, and the status line says
        how many.
        """
        counts = inv.counts()
        problems = sum(n for state, n in counts.items() if FILE_STATES[state][1] in ("bad", "warn"))
        _class_if(self.tab_buttons["files"], "kbx-alarm", bool(counts.get("failed")))
        _class_if(self.tab_buttons["files"], "kbx-alert", bool(problems) and not counts.get("failed"))
        found = (f" · {_plural(problems, 'file')} to look at: the Files tab lists them first" if problems
                 else " · every file is indexed and up to date")
        self._status(f"{_plural(len(inv.files), 'file')} listed in {inv.seconds:.1f}s{found}",
                     "ok" if not problems else "")

    def _open_wanted(self, wanted: str, inv: FileInventory) -> None:
        found = find_files(inv.files, wanted)
        if len(found) != 1:
            self._status(f"No single file matches {wanted!r}: search for it in the Files tab.", "warn")
            return
        self._show_tab("files")
        self._open_file(found[0])

    def _listed(self, inv: FileInventory) -> None:
        self.inventory = inv
        self.ui._inventories[inv.kb_id] = inv
        self._fill_source_pick(inv)
        self._draw_stats()
        self._draw_overview()
        self._draw_chips()
        self._refilter()
        self._say_listed(inv)
        wanted, self._want_file = self._want_file, None
        if wanted:
            self._open_wanted(wanted, inv)
        elif self.selected is None:
            self._draw_summary()


def explore(
    kb: str | None = None,
    *,
    file: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
    assistant: Any = None,
) -> KBExplorer:
    """
    Opens the knowledge base explorer window and returns it: every file next to Bedrock's record of it, how each
    was indexed (its chunks in document order, its metadata), the indexing pipeline, the sync history, search and
    every setting, all by clicking.

        explore()                                   # the only knowledge base in the region, or the first active one
        explore("support-docs")                     # by name, ID or ARN
        explore("support-docs", file="refund-policy.pdf")   # with one file open in the Files tab
        explore("support-docs").search("refund window", where="team = billing")   # a search with a filter
        explore(region="eu-west-1", profile="dev")  # another region or AWS profile

    The tabs' pages fill the browser's height; height= sets theirs instead (800 pixels, or CSS such as '70vh'). It only
    reads: where a sync is needed it shows the command to run. assistant=AgentView(...) adds ✦ Ask: a drawer where you
    ask the agent about what the window shows.
    """
    return KBExplorer(kb, file=file, region=region, profile=profile, height=height, assistant=assistant)
