"""
OpenSearchExplorer, the OpenSearch explorer window, and explore() to open it.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Note
from ..._kit.errors import _Hint, _error_name, _why
from ..._kit.fmt import _count, _plural, _total, human_money, human_size
from ..._kit.text import _esc
from ..._kit.themes import _theme_css, _theme_name
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..helpers import parse_location
from ..models import Domain, IndexInfo, KnnStats, Overview, StoreReport
from ..analysis.cost import domain_monthly_cost
from ..analysis.vectors import domain_knn_memory, index_vector_memory
from ..http import OpenSearchError
from ..analyzer import OpenSearchAnalyzer
from ..style import _CSS, _ROOT, _render_html
from ..view import OpenSearchView, _target_arg
from .documents import _DocumentsTab
from .focus import _FocusReads
from .frame import _EXPLORER_CSS, _EXPLORER_TABS, _LOGO, _STORE_TABS, _window_text
from .indexes import _IndexesTab
from .search import _SearchTab
from .stores import _StoresTab
from .widgets import _StoreField


class OpenSearchExplorer(_StoresTab, _IndexesTab, _DocumentsTab, _SearchTab, _FocusReads, _Explorer):
    """
    The OpenSearch explorer: a window to look through your vector indexes by clicking. The field at the top switches
    to another domain or Serverless collection; the cards beside it say how the one shown is doing.

        Domains    every domain and Serverless collection in the region: its status, the memory its data nodes
                   have for vectors, its cost and its warnings; click one to open it
        Overview   its indexes, the memory their vectors need against what the nodes have, and what needs attention
        Indexes    its indexes, vector indexes first; click one to see its vector fields in plain English, the
                   fields to filter on, its findings and the k-NN query to copy
        Documents  a random sample of the index's documents and how healthy their vectors are; click one to see its
                   text and fields, and search for the documents nearest to it
        Search     the documents nearest a question (embedded with Bedrock) or nearest one of its documents, with
                   their scores and what they mean

    where: what to open: a domain or collection ('vectors-prod'), an index in one ('vectors-prod/docs'), an ARN or
    an endpoint URL; without it, the domain with the most memory for vectors, else the first vector collection.
    tab: the tab to start on. view / core: an OpenSearchView or OpenSearchAnalyzer to use (else one is made from
    region / profile). height: the height of the tabs' pages (pixels, or CSS like '80vh'). mode: 'auto' (the window
    in Jupyter, reports elsewhere), 'widgets' or 'text'.

    It only reads: GET requests, and searches. x.ui is an OpenSearchView for reports in other cells; x.overview,
    x.store, x.report, x.info, x.sample, x.doc and x.result hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "osx", "opensearch-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        where: str | None = None,
        *,
        tab: str | None = None,
        view: OpenSearchView | None = None,
        core: OpenSearchAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        _check_choices(mode, tab)
        super().__init__()
        if view is None:
            view = OpenSearchView(core or OpenSearchAnalyzer(region=region, profile=profile),
                                  mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.height = height
        self.overview: Overview | None = None
        self.store: Any = None
        self.report: StoreReport | None = None
        self.index_name = ""
        self.info: IndexInfo | None = None
        self.knn: KnnStats | None = None
        self.knn_problem = ""
        self.model: str | None = None
        self._want_tab = tab
        self._want_index: str | None = None
        self._tab = "stores"
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(where, note, mode)
            return
        self._build()
        self._begin(where)
        self._display()

    def __repr__(self) -> str:
        shown = self.store.name if self.store is not None else "nothing open"
        if self.index_name:
            shown += f"/{self.index_name}"
        return f"OpenSearchExplorer({shown}) · help(OpenSearchExplorer) says what it shows"

    @_window_errors
    def open(self, where: str, *, tab: str | None = None) -> None:
        """
        Shows another domain or collection ('vectors-prod'), or an index in one ('vectors-prod/docs'), as picking it
        in the field does, on `tab` when one is named. An ARN, a collection ID or an endpoint URL works too.
        """
        _check_choices("widgets", tab)
        head, index = parse_location(where)
        store = self.core.resolve(head)
        self._want_index, self._want_tab = index, tab or ("indexes" if index else None)
        self._open_store(_target_arg(store))

    @_window_errors
    def index(self, name: str) -> None:
        """
        Opens one of the shown domain's or collection's indexes in the Indexes tab, as clicking it does: its vector
        fields, the memory they need, its findings and the query to copy.
        """
        names = [i.name for i in self.report.indexes] if self.report else []
        if self.store is None or name not in names:
            where = self.store.label if self.store is not None else "the domain or collection shown"
            listed = f" (its indexes: {', '.join(sorted(names)[:10])})" if names else ""
            raise ValueError(f"No index {name!r} in {where}{listed}")
        self._show_tab("indexes")
        if name != self.index_name:
            self._open_index(name)

    @_window_errors
    def search(self, question: str | None = None, *, like: str | None = None, k: int = 10,
               model: str | None = None) -> None:
        """
        Searches the index shown for the k documents nearest a question (embedded with a Bedrock model that fits
        its vectors, or model='<Bedrock model ID>'), or with like='doc-id' nearest one of its documents, and shows
        them in the Search tab. The model picked stays for the searches typed in the tab.
        """
        if (question is None) == (like is None):
            raise ValueError("Pass a question, or like='a document ID', and not both")
        if model is not None:
            self.model = model
        self._start_search(question, like, k)

    @_window_errors
    def document(self, doc_id: str) -> None:
        """
        Shows a document in the Documents tab, as clicking it does: one of the sample's, or of the last search's
        results.
        """
        doc = next((d for d in (self.sample.docs if self.sample else []) if str(d.get("_id", "")) == doc_id), None)
        hit = next((h for h in (self.result.hits if self.result else []) if h.id == doc_id), None)
        if doc is None and hit is None:
            raise ValueError(f"No document {doc_id!r} in the sample or in the last search's results")
        self._show_tab("documents")
        if doc is not None:
            self._show_document(doc)
        else:
            self._show_document({"_id": hit.id, **hit.source}, from_hit=True)
        self._status(f"Document {doc_id}: its text and fields.")

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the domains and collections, and the one shown (the ↻ button).
        """
        keep = _target_arg(self.store) if self.store is not None else None
        if keep and self.index_name:
            keep = f"{keep}/{self.index_name}"
        self._want_tab = self._tab
        self.core._domains.clear()
        self.core._collections.clear()
        self._begin(keep)

    def _reports(self, where: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: indexes('name') shows a domain's indexes, and index_info('name/index') "
                                 "one of them.")])
        if where is None:
            self.ui.overview()
            return
        _, index = parse_location(where)
        if index:
            self.ui.index_info(where)
        else:
            self.ui.indexes(where)

    def _error_text(self, exc: BaseException) -> str:
        """
        An error as the status line says it: a failed request to OpenSearch in words (what to change), and the
        commands in a sentence turned into tabs.
        """
        if isinstance(exc, OpenSearchError):
            return self._reword(self.ui._explain_http(exc))
        if isinstance(exc, ValueError) and not isinstance(exc, _Hint):
            return f"{type(exc).__name__}: {self._reword(str(exc))}"
        return super()._error_text(exc)

    def _command_failed(self, exc: Exception, command: str) -> bool:
        """
        A window command's error said in the window: OpenSearch's failed requests too, as well as AWS's.
        """
        if not isinstance(exc, OpenSearchError):
            return super()._command_failed(exc, command)
        if self._w is None:
            self.ui._show([_Note(self._error_text(exc), "warn")])
        else:
            self._status(self._error_text(exc), "warn")
        return True

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, drawn in the report
        theme the window opened with, and without the report and theme CSS each one carries (the window's style holds
        them).
        """
        html = _render_html(_for_window(blocks, _window_text), self.ui.max_rows, theme=self._theme)
        return html.replace(_CSS, "", 1).replace(self._theme_style, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("osx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the domains, collections and indexes "
                                       "again", layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("osx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.refresh_button], layout=layout(width="100%"))
        top.add_class("osx-top")
        self.field = _StoreField(self, on_pick=self._open_store, on_text=self._typed_store)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.field, self.stats], layout=layout(width="100%"))
        meta.add_class("osx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("osx-head")
        tabs = self._tab_bar(_EXPLORER_TABS)
        self.overview_view = w.HTML(layout=layout(width="100%"))
        self._theme = _theme_name()
        self._theme_style = _theme_css(_ROOT, self._theme)
        self._frame(_CSS + _EXPLORER_CSS + self._theme_style, head, tabs, {
            "stores": self._stores_page(),
            "overview": w.VBox([self.overview_view]),
            "indexes": self._indexes_page(),
            "documents": self._documents_page(),
            "search": self._search_page(),
        })
        self._show_tab(self._tab)
        self._draw_title()
        self._draw_stats()

    def _show_tab(self, key: str) -> None:
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "osx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if key == "documents":
            self._load_sample()
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))
        self._focus_moved()

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        store, report = self.store, self.report
        if key == "stores" and self.overview is not None:
            count = len(self.overview.domains) + len(self.overview.collections)
            return f"{count:,} domains and collections in {self.core.region}: click one to open it."
        if key in _STORE_TABS and store is None:
            return "Pick a domain or collection in the field above, or click one in the Domains tab."
        if key == "overview" and report is not None:
            return (f"{store.label}: {_indexes(len(report.indexes))}, {len(report.vector_indexes):,} with "
                    "vectors.")
        if key == "indexes" and report is not None:
            return f"{_indexes(len(report.indexes))}: click one to see its vector fields and memory."
        if key == "documents" and self.sample is not None:
            return f"{len(self.sample.docs):,} documents sampled from {self.sample.index}: click one to see it."
        if key == "search" and self.index_name:
            return f"Type a question about {self.index_name} and press Enter, or search like one of its documents."
        return ""

    def _begin(self, where: str | None) -> None:
        """
        Reads the region's domains and collections, then opens the one asked for (else the domain with the most
        memory for vectors, else the first vector collection, else the first).
        """
        self._status(f"Reading the domains and collections in {self.core.region}…", busy=True, owner="list")
        self._set(self.stores_view, _skeleton("Reading the domains, collections and their configuration…"))
        self._later("list", lambda: self.core.overview(), lambda ov: self._listed(ov, where), self._list_failed)

    def _list_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "es:ListDomainNames")
        self.field.problem = f"Couldn't list the domains and collections ({reason}). Type one's name and press Enter."
        self.field.set_choices([])
        self._status(f"Couldn't list the domains and collections ({reason}).", "warn")
        self._set(self.stores_view, self._html([_Note(f"Couldn't list the domains and collections: {reason}.",
                                                      "warn")]))

    def _listed(self, overview: Overview, wanted: str | None) -> None:
        self.overview = overview
        stores = [*overview.domains, *overview.collections]
        self.field.set_choices(stores)
        if overview.errors and not stores:
            self.field.problem = "Couldn't list them: " + ", ".join(f"{k} ({v})" for k, v in overview.errors.items())
        self._draw_stores()
        self._draw_stats()
        if not stores and not wanted:
            self._status(f"No OpenSearch domains or Serverless collections in {self.core.region}.", "warn")
            return
        warned = sum(1 for store in stores if self._warning_count(store))
        listed = f"{_plural(len(overview.domains), 'domain')} and {_plural(len(overview.collections), 'collection')}"
        self._status(f"{listed} in {self.core.region}" + (f" · {warned:,} with warnings" if warned else ""),
                     owner="list")
        target = self._target(wanted)
        if target is not None:
            self._open_store(target)

    def _target(self, wanted: str | None) -> str | None:
        """
        What to open first: the one asked for (its index too, when 'name/index' names one), else the domain with the
        most memory for vectors, else the first vector collection, else the first one.
        """
        if wanted:
            try:
                head, index = parse_location(wanted)
                store = self.core.resolve(head)
            except (ValueError, ClientError, BotoCoreError) as exc:
                self._status(self._error_text(exc), "warn")
                return None
            self._want_index = index
            if index and self._want_tab is None:
                self._want_tab = "indexes"
            return _target_arg(store)
        overview = self.overview
        domains = [d for d in overview.domains if "describe" not in d.errors]
        if domains:
            return max(domains, key=lambda d: (domain_knn_memory(d), d.name)).name
        vectors = sorted((c for c in overview.collections if c.vector), key=lambda c: c.name)
        stores = vectors or sorted([*overview.domains, *overview.collections], key=lambda s: s.name)
        return stores[0].name if stores else None

    def _typed_store(self, text: str) -> None:
        self._open_store(_target_arg(self.core.resolve(parse_location(text)[0])))

    def _draw_title(self) -> None:
        store = self.store
        sub = "Every domain and collection, its indexes, documents and vectors · read-only"
        if store is not None:
            engine = f"{store.engine} {store.version}" if isinstance(store, Domain) else ""
            sub = " · ".join(filter(None, [store.label, engine, store.url or "no endpoint"]))
        self._set(self.title, f'<div class="osx-brand"><span class="osx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="osx-name">OpenSearch explorer<span>{_esc(self.core.region)}</span>'
                              f'</div><div class="osx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the field: the domain or collection shown at a glance, or the region's before one is.
        """
        report = self.report
        cards = self._region_cards() if report is None else self._store_cards(report)
        html_cards = "".join(f'<div class="osx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="osx-stats">{html_cards}</div>')

    def _store_cards(self, report: StoreReport) -> list[tuple[str, str, str]]:
        store = report.store
        need = sum(index_vector_memory(i) or 0 for i in report.vector_indexes)
        native = sum(index_vector_memory(i, native=True) or 0 for i in report.vector_indexes)
        cards = [
            ("Vector indexes", f"{len(report.vector_indexes):,} of {len(report.indexes):,}", ""),
            ("Documents", _count(sum(i.documents or 0 for i in report.indexes)), ""),
        ]
        memory = domain_knn_memory(store) if isinstance(store, Domain) else None
        if memory:
            used = f"{human_size(need)} of {human_size(memory)}" if need else f"- of {human_size(memory)}"
            cards.append(("Vector memory / k-NN", used, "warn" if native > memory else ""))
        else:
            cards.append(("Vector memory", human_size(need) if need else "-", ""))
        if isinstance(store, Domain):
            cards.append(("Est. $ / month", human_money(_total(domain_monthly_cost(store, self.core.prices))), ""))
        if report.health and report.health.status != "green":
            tone = "bad" if report.health.status == "red" else "warn"
            cards.append(("Health", report.health.status or "?", tone))
        return cards

    def _region_cards(self) -> list[tuple[str, str, str]]:
        overview = self.overview
        if overview is None:
            return [("Domains", "…", "sk-on"), ("Collections", "…", "sk-on"), ("With warnings", "…", "sk-on")]
        stores = [*overview.domains, *overview.collections]
        warned = sum(1 for store in stores if self._warning_count(store))
        return [("Domains", f"{len(overview.domains):,}", ""), ("Collections", f"{len(overview.collections):,}", ""),
                ("With warnings", f"{warned:,}", "warn" if warned else "")]

    def _open_store(self, name: str) -> None:
        """
        Shows a domain or collection: its indexes first, then the index worth a look, in the background; what's still
        being read for the one shown before is dropped when it comes back.
        """
        self._drop("store", "index", "sample", "search")
        self.store, self.report = None, None
        self.index_name, self.info, self.knn, self.knn_problem = "", None, None, ""
        self._quietly(self.index_find, value="")
        self.index_query, self.index_chip = "", ""
        self.field.value = name
        self.field.draw()
        self._draw_store_rows()
        self._clear_documents()
        self._clear_search()
        for widget, text in ((self.overview_view, f"Reading the indexes in {name}…"),
                             (self.index_view, "Reading its indexes…")):
            self._set(widget, _skeleton(text))
        self.index_rows.children, self.index_chips.children = [], []
        self._set(self.index_count, "")
        self._status(f"Reading the indexes in {name}…", busy=True, owner="store")
        self._later("store", lambda: self.core.indexes(name), self._got_store, self._store_failed)

    def _store_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview_view, self.index_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _got_store(self, report: StoreReport) -> None:
        self.report, self.store = report, report.store
        self.field.value = _target_arg(report.store)
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_store_rows()
        self._draw("overview", self.overview_view, self.ui._indexes_blocks(report))
        self._draw_indexes()
        wanted, self._want_index = self._want_index, None
        want_tab, self._want_tab = self._want_tab, None
        if want_tab:
            self._show_tab(want_tab)
        self._finish("store", f"{report.store.label}: {_indexes(len(report.indexes))}, "
                              f"{len(report.vector_indexes):,} with vectors", tabs=_STORE_TABS)
        name = wanted or self._first_index()
        if name:
            self._open_index(name)

    def _open_index(self, name: str) -> None:
        """
        Shows an index: its mapping, settings, counts and the k-NN plugin's memory numbers, read in the background;
        the Documents and Search tabs start over for it.
        """
        self._drop("index", "sample", "search")
        self.index_name, self.info, self.knn, self.knn_problem = name, None, None, ""
        self._clear_documents()
        self._clear_search()
        self._draw_index_rows()
        self._draw_index()
        store = self.store
        self._status(f"Reading {name}…", busy=True, owner="index")
        self._later("index", lambda: self._read_index(store, name), self._got_index, self._index_failed)

    def _read_index(self, store: Any, name: str) -> tuple[IndexInfo, KnnStats | None, str]:
        """
        What the Indexes tab shows of an index, read off the loop: the index, and the k-NN plugin's numbers.
        """
        info = self.core.index(store, name)
        knn, problem = self.ui._knn_stats(store, info)
        return info, knn, problem

    def _index_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._finish("index", text, "warn", tabs=("indexes",))
        self._draw("indexes", self.index_view, [_Note(text, "warn")])

    def _got_index(self, read: tuple[IndexInfo, KnnStats | None, str]) -> None:
        self.info, self.knn, self.knn_problem = read
        self._draw_index()
        self._draw_search_hint()
        vectors = f"{_plural(len(self.info.vectors), 'vector field')}" if self.info.vectors else "no vector field"
        self._finish("index", f"{self.info.name}: {_count(self.info.documents)} documents · {vectors}",
                     tabs=("indexes",))
        if self._tab == "documents":
            self._load_sample()
        self._focus_moved()


def _indexes(count: int) -> str:
    return f"{count:,} index{'' if count == 1 else 'es'}"


def _check_choices(mode: str, tab: str | None) -> None:
    """
    Refuses a mode or tab the window doesn't have, saying which it does.
    """
    if mode not in ("auto", "widgets", "text"):
        raise ValueError("mode must be 'auto', 'widgets' or 'text'")
    tabs = [key for key, _, _, _ in _EXPLORER_TABS]
    if tab is not None and tab not in tabs:
        raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")


def explore(
    where: str | None = None,
    *,
    tab: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> OpenSearchExplorer:
    """
    Opens the OpenSearch explorer window and returns it: every domain and Serverless collection, its indexes (vector
    fields, the memory they need, the query to copy), a sample of an index's documents with how healthy their
    vectors are, and a search for the documents nearest a question or a document.

        explore()                                     # the domain with the most memory for vectors
        explore("vectors-prod")                       # a domain or collection by name, ARN or endpoint URL
        explore("vectors-prod/support-docs")          # one of its indexes
        explore("kb-support", tab="documents")

    It only reads: GET requests, and searches.
    """
    return OpenSearchExplorer(where, tab=tab, region=region, profile=profile, height=height)
