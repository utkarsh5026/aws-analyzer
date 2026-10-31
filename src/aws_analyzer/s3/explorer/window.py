"""
S3Explorer, made of its panes' mixins: what you drive it with from code, and its widgets.
"""

from __future__ import annotations

import dataclasses
import html
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Next, _Note, _Title
from ..._kit.deps import _in_notebook, _require
from ..._kit.history import PlaceHistory
from ..._kit.window import _cell_number, _css_height, _window_errors
from ..analyzer.s3_analyzer import S3Analyzer
from ..readers.formats import _DATA_ERRORS
from ..view.s3_view import S3View

from .navigator import _KINDS, Entry, Folder, S3Navigator, _s3_module, parse_filter, parse_location
from .frame import _CHECK, _CSS, _PAGE_BUTTONS, _Row
from .listing import _ListPane
from .recent import _RecentPane
from .report import _ReportPane
from .selection import _SelectionPane
from .settings import _SettingsPane


class S3Explorer(_ListPane, _ReportPane, _SelectionPane, _SettingsPane, _RecentPane):
    """
    Browse S3 like a file explorer in a notebook: folders and files on the left, what's inside the file you
    click on the right. Click a folder to open it; ← → ↑ go back, forward and up; click the path to jump
    anywhere (paste an s3:// path or an S3 console link); click a column header to sort.

    Above the list, the search box narrows it by name or file type ('.csv', '.csv .json', 'part-0*'), All / Folders /
    Files show only one kind, a chip for each file type here shows only those files, and "Include subfolders"
    lists everything below the folder, not only its first level, to search all of it (x.filter() does the same).
    They cover the whole folder: a big one shows its first page of entries at once and lists the rest in the
    background, up to x.nav.list_limit (10,000) entries. The list shows page_size rows at a time, and « ‹ › » under
    it move between pages.
    Tick files (a checkbox shows when you point at a row) and "Download selected", in the bar under the list,
    downloads them as one .zip; x.picked lists what's ticked.

    uri: where to start ('s3://bucket/prefix/', 'bucket/prefix', a file's path, a console link); leave it
    out to start from your buckets.
    core: an S3Analyzer or S3View to use (else one is made from profile / region).
    height: the height of the two panes. They fill the browser window (at least 560 pixels); a number of pixels
    (720) or CSS ('80vh') sets it instead. page_size: rows on each page of the list.
    zip_max_size: the biggest folder "Download .zip" packs ('100MB', '2GB'); ⚙ Settings changes it, and the most
    files in a zip (zip_max_files, 10,000).
    downloads: the folder "Download" and the zips save into (s3.py's downloads folder: 's3-downloads', next to the
    notebook); ⚙ Settings changes it too. x.ui.downloads() lists what's there and x.ui.clean_downloads() deletes it.
    history: whether to remember the folders you open, between sessions, for the Recent row over your list of
    buckets (and while you type a path), where ↩ picks up where you left off: True (in aws-analyzer's cache folder,
    ~/.cache/aws-analyzer/history/, per AWS account; only the paths and when), False, or a file to keep them in.
    AWS_ANALYZER_HISTORY=off turns it off everywhere. x.recent lists them and x.forget_recent() clears them.
    mode: 'auto' (the clickable explorer in Jupyter, a text listing elsewhere), 'widgets' or 'text'.

    The previews and reports on the right come from s3.py's S3View; `x.ui` is one you can use in any cell
    (x.ui.summary(x.location)), and `x.nav` is the S3Navigator behind the list.
    """

    def __init__(
        self,
        uri: str = "",
        core: Any = None,
        *,
        profile: str | None = None,
        region: str | None = None,
        height: int | str | None = None,
        page_size: int = 100,
        zip_max_size: int | str = "100MB",
        downloads: str | None = None,
        mode: str = "auto",
        progress: str = "auto",
        history: bool | str = True,
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        self._history = PlaceHistory.from_setting(history, "s3")
        self._baseline = None
        self.height = height
        self.page_size = max(10, int(page_size))
        self.zip_max_size = zip_max_size  # "Download .zip" zips a folder up to this size (⚙ Settings changes it)
        self.zip_max_files = 10_000  # and up to this many files
        self.selected = ""  # the uri of the file shown on the right ('' = the folder's overview)
        self.shown: list[list[Any]] = []  # the blocks of the last report on the right, for tests and curious users
        self._action = ""
        self._broken = ""
        self._shown_at: Any = object()
        self._widgets: Any = None
        self._quiet = False  # set while the code (not the user) clears the filter
        self._query = ""  # the search box's text
        self._kind = "all"  # All / Folders / Files: what the list shows (it stays as you move around, like the sort)
        self._deep = False  # "Include subfolders": the list holds everything below the folder (S3Navigator.below)
        self._all_types = False  # every file type has a chip, not only the most common
        self._picked: dict[str, Entry] = {}  # what's ticked in the list, by uri, to download as one .zip
        self._picks_auto = ""  # the zip name the explorer suggested, until it's typed over
        self._job = 0  # counts changes of the right pane; a background report made for an older one isn't shown
        self._workers: ThreadPoolExecutor | None = None  # the threads background reports load on
        self._task: Any = None  # the last background report's asyncio task (tests wait for it)
        self._first_page = 1  # where "Read all" or "Text" starts in a PDF; the pager under the report moves it
        self._page_counts: dict[tuple[str, str], int] = {}  # (uri, etag) -> a PDF's pages (0: couldn't count)
        self._more_open = False
        self._versioning: dict[str, bool] = {}
        self._expand_all = False  # ▾ Expand all: JSON trees show every object and array (it stays on, like the sort)
        self._reports: list[list[Any]] = []  # the reports on the right, to draw again when that changes
        try:
            self.s3 = _s3_module()
        except ImportError as exc:
            self.s3, self._broken = None, str(exc)
            self._say(self._broken)
            return
        if core is None and (profile or region):
            core = S3Analyzer(profile=profile, region=region)
        self.nav = S3Navigator(core)
        self.core = self.nav.core
        if downloads is not None:
            self.core.downloads = downloads
        self.ui = S3View(self.core, progress=progress)  # for your own cells; reports show where you call them
        self._pane = S3View(self.core, mode="text" if mode == "text" else "auto", progress=progress)
        self._pane._show = self._capture  # its reports land in the right-hand pane, not in a new output
        note = ""
        if mode != "text" and (mode == "widgets" or _in_notebook()):
            try:
                self._widgets = _require("ipywidgets", "Clicking through folders")
            except ImportError as exc:
                note = (f"{exc}, which SageMaker notebooks normally have. Install it and restart the kernel; "
                        "until then this is a plain listing, and open(...) moves around.")
        self._pane.use_html = self._widgets is not None or (mode != "text" and _in_notebook())
        self._cache: dict[tuple[str, str, str], list[list[Any]]] = {}
        self._sort, self._descending = "name", False
        self._offset = 0  # the list's page: the index of its first row in _visible
        self._visible: list[Entry] = []
        self._folder: Folder | None = None  # what the list was last drawn from
        self._overview: Folder | None = None  # the folder whose overview the right pane shows, if it does
        self._order: tuple[Any, ...] = ()  # (folder, what it was sorted by, its entries in that order)
        self._counted: tuple[Any, ...] = ()  # (folder, how many entries, their FolderStats)
        self._lister: Folder | None = None  # the folder being listed in the background
        self._list_task: Any = None  # its asyncio task (tests wait for it)
        self._list_error: tuple[Any, ...] = ()  # (folder, why its background listing stopped early)
        self._rows_key: tuple = ()
        self._rows_at = 0.0
        if self._widgets is not None:
            self._build()
        self._navigate(lambda: self.nav.open(uri))
        if note:
            self._say(note)
        self._display()

    # ------------------------------------------------------------------ public commands

    @property
    def location(self) -> str:
        """
        The folder you're in ('s3://bucket/prefix/'), or '' for the list of buckets.
        """
        return self.nav.location if self.s3 is not None else ""

    @property
    def downloads(self) -> str:
        """
        Where "Download" and the zips save: s3.py's downloads folder (core.downloads, 's3-downloads' next to the
        notebook unless set), which ⚙ Settings changes.
        """
        return self.core.downloads

    @downloads.setter
    def downloads(self, folder: str) -> None:
        self.core.downloads = folder

    zip_folder = downloads  # its name before downloads went in one folder

    def _where(self) -> str:
        """
        The downloads folder in words: 's3-downloads', or "the notebook's folder" for '.'.
        """
        folder = str(self.downloads).strip() or "."
        return "the notebook's folder" if os.path.abspath(os.path.expanduser(folder)) == os.getcwd() else folder

    @property
    def picked(self) -> list[str]:
        """
        The files and folders ticked in the list (s3:// paths), in the order they were ticked: x.ui.download_zip(
        x.picked) zips them from code.
        """
        return list(self._picked)

    @_window_errors
    def open(self, uri: str = "") -> None:
        """
        Go to a folder, or show a file (its folder opens with the file on the right). Takes s3:// paths,
        bucket/prefix and S3 console links; open() with nothing goes to your list of buckets.
        """
        self._navigate(lambda: self.nav.open(uri))

    @_window_errors
    def back(self) -> None:
        """
        Go back to the folder you were in before (the ← button).
        """
        self._navigate(self.nav.back)

    @_window_errors
    def forward(self) -> None:
        """
        Go forward again after back() (the → button).
        """
        self._navigate(self.nav.forward)

    @_window_errors
    def up(self) -> None:
        """
        Go to the folder above this one (the ↑ button).
        """
        self._navigate(self.nav.up)

    @_window_errors
    def refresh(self) -> None:
        """
        List this folder again, to pick up files added or removed since it was opened (the ↻ button).
        """
        keep = self.selected
        self._cache.clear()
        self._versioning.clear()
        self._navigate(self.nav.refresh, keep=keep, same=True)

    @_window_errors
    def filter(self, text: str = "", kind: str = "all", subfolders: bool = False) -> None:
        """
        Show only part of the list, as the search box and the buttons above it do: names with `text` in them,
        file types such as '.csv' or '.csv .json' (.csv also finds .csv.gz), patterns such as 'part-0*'; only
        kind='folders' or 'files'; and with subfolders=True everything below this folder, not only its first
        level (the first 10,000 files). It searches the whole folder, up to its first 10,000 entries, and in a bigger
        one it also asks S3 for the names that start with `text`. filter() shows everything again.

        x.filter(".parquet", subfolders=True)    # every Parquet file below this folder
        x.filter(kind="folders")                 # only the folders here
        """
        if kind not in _KINDS:
            raise ValueError(f"kind={kind!r}: use 'all', 'folders' or 'files'")
        self._kind, self._all_types = _KINDS[kind], False
        if bool(subfolders) != self._deep:
            self._deep = bool(subfolders)
            self._busy("Listing everything below this folder…")
        folder = self._source()
        self._follow(folder)
        wanted = parse_filter(text)
        if (folder.more and self._lister is not folder and not folder.deep and len(wanted.words) == 1
                and not wanted.patterns and not wanted.types):
            self.nav.lookup(wanted.words[0])  # past what's listed: what "Look up" under the list does
        self._set_query(text or "", renew=True)
        if self._widgets is not None and not self.selected:
            self._show_folder(self._source())

    # ------------------------------------------------------------------ plumbing

    def __repr__(self) -> str:
        where = self.location or "all buckets" if self.s3 is not None else "not ready"
        return f"S3Explorer({where})"

    def _ipython_display_(self) -> None:
        if _cell_number() != self._shown_at:  # it already showed itself when this cell made it
            self._display()

    def _display(self) -> None:
        self._shown_at = _cell_number()
        if self.s3 is None or self._widgets is None or self._shown_at is None:
            return  # a text explorer prints as it goes; outside IPython there's nowhere to show widgets
        from IPython.display import display

        display(self._app)

    def _say(self, text: str) -> None:
        """
        A warning note; works even when s3.py couldn't be loaded and nothing else can render.
        """
        if self.s3 is not None:
            self._set_pane([[_Note(text, "warn")]])
            return
        if _cell_number() is not None:
            from IPython.display import HTML, display

            display(HTML(f'<div style="padding:6px 10px;border-left:3px solid #f59e0b;'
                         f'background:rgba(245,158,11,.1)">⚠ {html.escape(text)}</div>'))
            return
        print("[!] " + text)

    def _fail(self, text: str) -> None:
        self._set_pane([[_Note(text, "warn")]])

    def _ready(self) -> bool:
        """
        Whether a command can run (see _window_errors): not without s3.py, which says so instead.
        """
        if self.s3 is None:
            self._say(self._broken)
            return False
        return True

    def _command_failed(self, exc: Exception, command: str) -> bool:
        """
        A command's AWS or input error as a note in the explorer (see _window_errors); False for any other.
        """
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            self._fail(f"{error.get('Code', 'Error')}: {error.get('Message', exc)}  [{command}]")
        elif isinstance(exc, ImportError):  # a missing optional package: the message says what to pip install
            self._fail(f"{str(exc).rstrip('.')}.")
        elif isinstance(exc, (BotoCoreError, *_DATA_ERRORS)):
            self._fail(f"{type(exc).__name__}: {exc}  [{command}]")
        else:
            return False
        return True

    def _capture(self, blocks: list[Any]) -> None:
        """
        The pane's S3View renders here (see _trim).
        """
        self._captured.append(self._trim(blocks, self.selected))

    def _trim(self, blocks: list[Any], selected: str) -> list[Any]:
        """
        A report for the right pane: without its Next block (its calls name commands of a view you don't have
        here), and with the file's name instead of its whole path (the status bar shows that).
        """
        kept = []
        for block in blocks:
            if isinstance(block, _Next):
                continue
            if isinstance(block, _Title) and selected and selected in block.text:
                block = dataclasses.replace(block, text=block.text.replace(selected, Entry(
                    "file", *parse_location(selected)).name))
            kept.append(block)
        return kept

    def _busy(self, text: str) -> None:
        """
        The status bar while S3 is listing: a spinner and what it's doing.
        """
        if self._widgets is not None:
            self._status.value = f'<span class="s3x-at s3x-busy">{html.escape(text)}</span>'

    # ------------------------------------------------------------------ navigation

    def _navigate(self, move: Callable[[], Folder], keep: str = "", same: bool = False) -> None:
        """
        Move (open / back / up / ...), then redraw the list, the path and the right pane. Another folder starts
        with an empty search box and only its first level listed (same=True, for ↻, keeps them); All / Folders /
        Files stays, like the sort.
        """
        self._busy("Listing…")
        move()
        self.selected, self._offset, self._action = "", 0, ""
        if not same:
            self._load_baseline()
            self._query, self._deep, self._all_types = "", False, False
            self._picked.clear()  # another folder: start a new selection
        if self.nav.focus and not same:
            self._kind = "all"  # so the file it opens is in the list
        if self._deep:
            self._busy("Listing everything below this folder…")
        folder = self._source()
        self._follow(folder)
        focus = self.nav.focus or keep
        if focus and folder.more and not folder.deep and all(e.uri != focus for e in folder.entries):
            self.nav.lookup(Entry("file", *parse_location(focus)).name)  # past what's listed: ask S3 for it
        if self._widgets is not None:
            self._quiet = True
            self._filter.value = self._query
            self._quiet = False
            self._draw_crumbs()
            self._draw_filters(folder)
            self._draw_rows(folder)
            self._renew("left")
        if focus and any(e.uri == focus for e in folder.entries):
            self._select(focus)
        elif self._widgets is None:
            self._set_pane([self._folder_blocks(folder, listing=True)])
        else:
            self._show_folder(folder)
        self._draw_status(folder)
        self._draw_picks()
        if not same:
            self._remember()
        self._save_look(self.nav.folder())
        self._draw_recent()

    def _select(self, uri: str, action: str = "preview") -> None:
        """
        Show a file on the right (its preview, or another of its file reports: `action`), and the page of the list
        it's on.
        """
        self.selected = uri
        if self._widgets is not None:
            index = next((i for i, e in enumerate(self._visible) if e.uri == uri), -1)
            if index >= 0 and not self._offset <= index < self._offset + self.page_size:
                self._offset = index - index % self.page_size
                self._draw_rows(self._source())
                self._renew("left")
            self._action = action
            self._mark_rows()
            self._draw_actions()
            self._draw_status(self._source())
        self._on_action(action)

    # ------------------------------------------------------------------ widgets

    def _build(self) -> None:
        w = self._widgets

        def button(text: str, css: str, tip: str, on_click: Callable[[], None], **layout: Any) -> Any:
            b = w.Button(description=text, tooltip=tip, layout=w.Layout(**layout))
            for name in css.split():
                b.add_class(name)
            b.on_click(lambda _: self._guard(on_click))
            return b

        style = w.HTML(_CSS, layout=w.Layout(display="none"))
        # The icon buttons keep their glyph as the description: hidden by the style, which draws the icon instead.
        self._back_btn = button("←", "s3x-nav s3x-ic s3x-i-back", "Back", self.back)
        self._fwd_btn = button("→", "s3x-nav s3x-ic s3x-i-forward", "Forward", self.forward)
        self._up_btn = button("↑", "s3x-nav s3x-ic s3x-i-up", "Up one level", self.up)
        refresh = button("↻", "s3x-nav s3x-ic s3x-i-refresh", "List this folder again", self.refresh)
        navs = w.HBox([self._back_btn, self._fwd_btn, self._up_btn, refresh], layout=w.Layout(width="auto"))
        navs.add_class("s3x-navs")
        self._crumbs = w.HBox()
        self._crumbs.add_class("s3x-crumbs")
        self._path = w.Text(placeholder="s3://bucket/folder/ or an S3 console link, then Enter",
                            layout=w.Layout(flex="1 1 auto", display="none", width="auto"))
        self._path.add_class("s3x-path")
        # Go on Enter only (the box sends "submit"), not when it loses focus: clicking ✕ then cancels.
        self._path.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._on_path))
        self._edit_btn = button("✎", "s3x-nav s3x-ic s3x-i-edit", "Type or paste a path", self._toggle_path)
        self._gear = button("⚙", "s3x-nav s3x-ic s3x-i-settings", "Settings: the biggest folder to zip, and where "
                            "zips go", self._toggle_settings)
        bar = w.HBox([navs, self._crumbs, self._path, self._edit_btn, self._gear], layout=w.Layout(width="100%"))
        bar.add_class("s3x-bar")

        self._filter = w.Text(continuous_update=True, layout=w.Layout(width="100%"))
        self._filter.add_class("s3x-search")
        self._filter.add_class("s3x-i-search")
        self._filter.observe(lambda _: self._quiet or self._guard(self._on_filter), "value")
        self._clear_btn = button("✕", "s3x-nav s3x-clear s3x-ic s3x-i-close", "Clear the search",
                                 lambda: self._set_query(""))
        search = w.HBox([self._filter, self._clear_btn], layout=w.Layout(width="100%"))
        search.add_class("s3x-searchrow")
        self._kind_btns = {
            kind: button(label, "s3x-seg", tip, lambda kind=kind: self._on_kind(kind))
            for kind, label, tip in (("all", "All", "Folders and files"), ("folders", "Folders", "Only the folders"),
                                     ("files", "Files", "Only the files"))
        }
        segs = w.HBox(list(self._kind_btns.values()), layout=w.Layout(width="auto"))
        segs.add_class("s3x-segs")
        self._deep_btn = button("Include subfolders", "s3x-chip s3x-deep s3x-ic s3x-i-below", "", self._on_deep)
        self._kinds = w.HBox([segs, self._deep_btn], layout=w.Layout(width="100%"))
        self._kinds.add_class("s3x-kinds")
        self._types = w.HBox(layout=w.Layout(width="100%"))
        self._types.add_class("s3x-types")
        self._chips: list[Any] = []  # a button per file type, reused as the types change
        self._chip_types: list[str] = []  # the type each chip stands for now
        self._more_types = button("", "s3x-chip s3x-more", "", self._on_more_types)
        self._finder = w.VBox([self._build_recent(), search, self._kinds, self._types], layout=w.Layout(width="100%", flex="0 0 auto"))
        self._finder.add_class("s3x-find")

        self._check_all = button("✓", " ".join(_CHECK), "Select everything listed", self._on_check_all)
        self._cols = {
            "name": button("Name", "s3x-col s3x-namecol", "Sort by name", lambda: self._on_sort("name"),
                           flex="1 1 auto", width="auto"),
            "size": button("Size", "s3x-col s3x-num", "Sort by size", lambda: self._on_sort("size"), width="76px"),
            "modified": button("Modified", "s3x-col s3x-num", "Sort by date", lambda: self._on_sort("modified"),
                               width="76px"),
        }
        head = w.HBox([self._check_all, *self._cols.values()], layout=w.Layout(width="100%", flex="0 0 auto"))
        head.add_class("s3x-head")
        self._rows_box = w.VBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._rows_box.add_class("s3x-rows")
        self._pool: list[_Row] = []
        self._load_btn = button("Load more from S3", "s3x-link", "List more of this folder", self._on_load)
        self._lookup_btn = button("", "s3x-link", "Ask S3 for names starting with the search text", self._on_lookup)
        self._fix_btn = button("", "s3x-link", "", lambda: self._fix())  # what the note above it suggests
        self._fix: Callable[[], None] = lambda: None
        self._foot_note = w.HTML(layout=w.Layout(width="100%"))
        self._foot = w.HBox([self._foot_note, self._fix_btn, self._load_btn, self._lookup_btn],
                            layout=w.Layout(width="100%", flex="0 0 auto"))
        self._foot.add_class("s3x-foot")
        self._head = head
        # The glyphs are hidden by the style, which draws the icons instead.
        self._page_btns = {name: button(glyph, f"s3x-nav s3x-ic s3x-i-{name}", tip, lambda name=name: self._on_page(name))
                           for name, glyph, tip in _PAGE_BUTTONS}
        self._range = w.HTML(layout=w.Layout(flex="1 1 auto", min_width="0"))
        self._pages = w.HBox([self._range, *self._page_btns.values()],
                             layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._pages.add_class("s3x-pages")

        self._actions = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._actions.add_class("s3x-actions")
        self._expand_btn = button("▾ Expand all", "s3x-act s3x-expand", "", self._on_expand)
        self._progress = w.Output(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._content = w.HTML(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._pager = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._pager.add_class("s3x-pager")
        self._settings = self._build_settings(button)
        self._picks_panel = self._build_picks(button)
        self._picks_note = w.HTML(layout=w.Layout(flex="1 1 auto", min_width="0"))
        self._picks_bar = w.HBox([self._picks_note,
                                  button("Clear", "s3x-quiet", "Unselect everything", self._clear_picks),
                                  button("🐍 Use in Python", "s3x-quiet", "The lines that load what's selected "
                                         "in a notebook cell: one DataFrame when they're tables of one format",
                                         self._open_picks_code),
                                  button("⬇ Download selected", "s3x-primary", "Download what's selected as one "
                                         ".zip: see what goes in and name it first", self._open_picks)],
                                 layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._picks_bar.add_class("s3x-picks")
        self._side = w.VBox([self._finder], layout=w.Layout(width="42%", min_width="300px", flex="0 0 auto"))
        self._side.add_class("s3x-side")
        # The style makes the panes fill the browser window, but in VS Code, whose 100vh is the whole notebook's
        # height, 560px. height= puts its own on the box, which wins over the style's.
        self._body = w.HBox(layout=w.Layout(width="100%", height=_css_height(self.height)))
        self._body.add_class("s3x-body")
        self._left = self._right = None
        self._renew("left", "right")
        self._status = w.HTML(layout=w.Layout(width="100%"))
        self._status.add_class("s3x-status")
        self._app = w.VBox([style, bar, self._body, self._status], layout=w.Layout(width="100%"))
        self._app.add_class("s3x")
        self._act_buttons: dict[str, Any] = {}

    def _renew(self, *sides: str) -> None:
        """
        Put the list ('left') or the report ('right') in a new box. Widgets can't scroll without JavaScript, but a
        new box starts at the top, so a folder you open shows its first rows and a new report shows its title.
        """
        w, old = self._widgets, [self._left, self._right]
        if "left" in sides:  # under the search box and its buttons, which stay where they are
            self._left = w.VBox([self._head, self._rows_box, self._foot],
                                layout=w.Layout(width="100%", flex="1 1 auto", min_height="0", overflow="auto"))
            self._left.add_class("s3x-list")
            self._side.children = (self._finder, self._left, self._pages, self._picks_bar)
        if "right" in sides:
            self._right = w.VBox([self._actions, self._progress, self._content, self._settings, self._picks_panel,
                                  self._pager],
                                 layout=w.Layout(flex="1 1 auto", width="auto", min_width="0", overflow="auto"))
            self._right.add_class("s3x-right")  # min_width 0: wide tables scroll
        self._body.children = (self._side, self._right)
        for box in old:
            if box is not None and box not in (self._left, self._right):
                box.close()  # closes the box only; the widgets in it live on in the new one

    def _guard(self, handler: Callable[[], None]) -> None:
        """
        Run a widget callback; anything that goes wrong becomes a note on the right. (A callback's
        exception would otherwise go to Jupyter's log, where nobody sees it, and the click would do nothing.)
        """
        try:
            handler()
        except (ClientError, BotoCoreError, *_DATA_ERRORS) as exc:
            code = exc.response.get("Error", {}).get("Code", "Error") if isinstance(exc, ClientError) else ""
            self._fail(f"{code or type(exc).__name__}: {exc}")
        except KeyboardInterrupt:  # the kernel's stop button, during a long scan
            self._fail("Stopped. Click the file or button again to start over.")
        except Exception as exc:  # a bug: still say so where the user is looking
            self._fail(f"Something went wrong ({type(exc).__name__}: {exc}). Press ↻ to try again.")
        finally:
            if "…" in self._status.value:  # "Listing…" that never finished
                folder = (self.nav._below if self._deep else self.nav._cache).get(self.nav.location)
                if folder is not None:
                    self._draw_status(folder)
                else:
                    self._status.value = '<span class="s3x-at">Stopped</span>'
