"""
The Search tab's mixin: what a question retrieves, narrowed by a metadata filter when one is typed, each passage
opening its file's page, and Compare, the same question under several search settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from ..._bedrock.estimates import query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.words import question_terms
from ..._kit.blocks import _Cards, _Findings, _Note, _Title
from ..._kit.fmt import _plural, human_money
from ..._kit.text import _clip
from ..._kit.window import _class_if, _skeleton

from ..models import KBFile, Retrieval, SearchComparison
from ..analysis.filters import describe_filter, parse_where
from ..analysis.findings import _search_label, retrieval_findings
from ..view.cells import _passage_blocks
from .frame import _RESULTS

_SEARCH_TYPES = [("Default", ""), ("Semantic", "SEMANTIC"), ("Hybrid", "HYBRID")]
_SEARCH_TYPE_TIPS = ["Bedrock's choice for this vector store", "Matches meaning",
                     "Meaning and exact words (codes, names): OpenSearch, Aurora and MongoDB stores"]
_COMPARED_TYPES = ("SEMANTIC", "HYBRID")
_MOST_PASSAGES = 100
_RERANKERS = [("No reranker", ""), ("Cohere Rerank 3.5", "cohere"), ("Amazon Rerank", "amazon")]
_PASSAGE_WIDTH = 420
_TITLE_CHARS = 90
_NAME_CHARS = 40
_FILTER_HINT = "Metadata filter, like team = billing; year >= 2024 (blank: every file)"


@dataclass
class _SearchRequest:
    """
    What the Search tab's fields ask for: the question, how many passages, the search type, the data source, the
    reranker and the metadata filter (where=, parsed from what's typed).
    """

    kb_id: str
    question: str
    n: int
    search_type: str | None
    data_source: str | None
    rerank_model: str | None
    where: dict[str, Any] | None

    def compared_sizes(self) -> tuple[int, ...]:
        """
        The numbers of passages Compare tries: the one picked, and twice as many (at most 100), to show whether
        more passages would find more.
        """
        return tuple(sorted({self.n, min(self.n * 2, _MOST_PASSAGES)}))


def _search_subtitle(r: Retrieval) -> str:
    parts = [f"{len(r.passages)} of up to {r.n} passages", _search_label(r.search_type)]
    if r.data_sources:
        parts.append(f"only {describe_sources(r.data_sources)}")
    if r.where is not None:
        parts.append(f"where {describe_filter(r.where)}")
    if r.reranked:
        parts.append(f"reranked by {r.reranked}")
    return " · ".join(parts)


class _SearchTab:
    """
    The Search tab of KBExplorer: a question, how many passages, the search type, a data source, a reranker and a
    metadata filter, then the passages found, each with a button to the page of the file it came from. Compare
    searches the question with each search type and two numbers of passages, and shows where each passage ranks
    under each.
    """

    def _search_box(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.question = w.Text(placeholder="Ask the knowledge base a question, then press Enter",
                               continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.question.on_msg(self._on_enter(self._search))
        self.search_button = w.Button(description="Search", button_style="primary", tooltip="Retrieve the passages "
                                      "that match it best (Enter does too)",
                                      layout=layout(flex="0 0 auto", width="auto"))
        self.search_button.on_click(self._safely(lambda _button: self._search()))
        self.compare_button = w.Button(description="Compare", tooltip="Search the question as Semantic and as Hybrid, "
                                       "with the passages picked and twice as many, and show where each passage "
                                       "ranks under each: whether another setting would find more",
                                       layout=layout(flex="0 0 auto", width="auto"))
        self.compare_button.add_class("kbx-compare")
        self.compare_button.on_click(self._safely(lambda _button: self._compare()))
        ask = w.HBox([self.question, self.search_button, self.compare_button], layout=layout(width="100%"))
        for name in ("kbx-ask", "kbx-big"):
            ask.add_class(name)
        return ask

    def _search_options(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.n_pick = w.Dropdown(options=[(f"{n} passages", n) for n in _RESULTS], value=5,
                                 layout=layout(width="130px"))
        self.kind_pick = w.ToggleButtons(options=_SEARCH_TYPES, value="", tooltips=_SEARCH_TYPE_TIPS,
                                         style={"button_width": "auto"})
        self.search_source = w.Dropdown(options=[("Every data source", "")], value="",
                                        layout=layout(width="200px", display="none"))
        self.rerank_pick = w.Dropdown(options=_RERANKERS, value="", layout=layout(width="170px"))
        options = w.HBox([self.n_pick, self.kind_pick, self.search_source, self.rerank_pick],
                         layout=layout(width="100%"))
        options.add_class("kbx-options")
        return options

    def _filter_row(self) -> Any:
        """
        The metadata filter: typed as text (parse_where), and sent with every search and comparison until cleared.
        """
        w, layout = self._w, self._w.Layout
        self.where_box = w.Text(placeholder=_FILTER_HINT, continuous_update=True,
                                tooltip="Only passages from files whose .metadata.json matches: =, !=, >, >=, <, <=, "
                                        "in, not in, begins with, contains, has (a list holds it), between … and …; "
                                        "join conditions with ; or and. Enter searches.",
                                layout=layout(flex="1 1 auto", width="auto"))
        self.where_box.on_msg(self._on_enter(self._search))
        self.where_box.observe(self._safely(lambda _change: self._mark_filter()), names="value")
        clear = w.Button(description="✕", tooltip="Clear the metadata filter", layout=layout(flex="0 0 auto"))
        clear.add_class("kbx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.where_box, "value", "")))
        self.where_row = w.HBox([self.where_box, clear], layout=layout(width="100%"))
        self.where_row.add_class("kbx-where")
        return self.where_row

    def _search_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.search_head = w.HTML(layout=layout(width="100%"))
        self.hits = w.VBox(layout=layout(width="100%"))
        self.hits.add_class("kbx-hits")
        return w.VBox([self._search_box(), self._search_options(), self._filter_row(), self.search_head, self.hits])

    def _search_line(self) -> str:
        if self.compared is not None:
            settings = len(self.compared.runs) + len(self.compared.errors)
            return f"{_plural(settings, 'search setting')} compared for {_clip(self.compared.question, 60)!r}"
        if self.found is None:
            return "Type a question and press Enter: each passage found opens its file's page."
        return f"{_plural(len(self.found.passages), 'passage')} for {_clip(self.found.question, 60)!r}"

    def _mark_filter(self, problem: bool = False) -> None:
        """
        The filter box's look: outlined while it holds a filter, red when what's typed can't be read.
        """
        has_filter = bool(str(self.where_box.value or "").strip())
        _class_if(self.where_row, "kbx-on", has_filter and not problem)
        _class_if(self.where_row, "kbx-bad", problem)

    def _search_request(self) -> _SearchRequest | None:
        """
        What the fields ask for, or None after the status line says what's missing or can't be read.
        """
        question = str(self.question.value or "").strip()
        if not question:
            self._status("Type a question first, then press Enter.")
            return None
        if not self.kb:
            self._status("Pick a knowledge base first.", "warn")
            return None
        try:
            where = parse_where(self.where_box.value)
        except ValueError as exc:
            self._mark_filter(problem=True)
            self._status(f"Metadata filter: {exc}", "warn")
            return None
        self._mark_filter()
        return _SearchRequest(self.kb, question, int(self.n_pick.value), self.kind_pick.value or None,
                              self.search_source.value or None, self.rerank_pick.value or None, where)

    def _searching(self, busy: bool, button: Any = None) -> None:
        """
        Search and Compare are turned off while either runs; the one clicked says it's running.
        """
        self.search_button.disabled = self.compare_button.disabled = busy
        self.search_button.description = "Searching…" if busy and button is self.search_button else "Search"
        self.compare_button.description = "Comparing…" if busy and button is self.compare_button else "Compare"

    def _start_search(self, button: Any, note: str) -> None:
        self._searching(True, button)
        self._set(self.search_head, _skeleton(note))
        self.hits.children = []
        self._status(note, busy=True)

    def _search_failed(self, exc: BaseException) -> None:
        self._searching(False)
        text = self._error_text(exc)
        self._set(self.search_head, self._html([_Note(text, "warn")]))
        self._status(text, "warn")

    def _searched(self, r: Retrieval) -> None:
        self._searching(False)
        self.found, self.compared = r, None
        self._draw_hits(r)

    def _search(self) -> None:
        asked = self._search_request()
        if asked is None:
            return
        self._start_search(self.search_button, "Searching…")
        self._later("search", lambda: self.core.retrieve(
            asked.kb_id, asked.question, asked.n, search_type=asked.search_type, data_source=asked.data_source,
            rerank_model=asked.rerank_model, where=asked.where), self._searched, self._search_failed)

    def _compared(self, c: SearchComparison, where: dict[str, Any] | None) -> None:
        self._searching(False)
        self.found, self.compared = None, c
        self._draw("search", self.search_head, self.ui._compare_blocks(c, where))
        seconds = sum(r.seconds for r in c.runs.values())
        skipped = f" ({_plural(len(c.errors), 'setting')} the vector store refused)" if c.errors else ""
        self._status(f"Compared {_plural(len(c.runs), 'search setting')} in {seconds:.1f}s{skipped}: each row is a "
                     "passage, each column where that setting ranked it")

    def _compare(self) -> None:
        """
        Compare: the question searched with each search type and two numbers of passages, without the reranker, as
        compare() does, with the window's data source and metadata filter.
        """
        asked = self._search_request()
        if asked is None:
            return
        self._start_search(self.compare_button, "Searching with each setting…")
        self._later("search", lambda: self.core.compare(
            asked.kb_id, asked.question, n=asked.compared_sizes(), search_types=_COMPARED_TYPES, where=asked.where,
            data_source=asked.data_source), lambda c: self._compared(c, asked.where), self._search_failed)

    def _search_cards(self, r: Retrieval) -> _Cards:
        top = max((p.score for p in r.passages if p.score is not None), default=None)
        return _Cards([("Passages", f"{len(r.passages):,}"), ("Top score", "-" if top is None else f"{top:.2f}"),
                       ("Files", f"{len({p.uri for p in r.passages}):,}"), ("Time", f"{r.seconds:.1f}s"),
                       ("Est. cost", human_money(query_cost(1, r.reranked or False, self.core.prices)))])

    def _listed_files(self) -> dict[str, KBFile]:
        """
        The knowledge base's listed files by URI, as listed and decoded, to find the file a passage came from.
        """
        files = self.inventory.files if self.inventory else []
        by_uri = {unquote(f.uri): f for f in files}
        by_uri.update({f.uri: f for f in files})
        return by_uri

    def _hit(self, card: Any, known: KBFile | None) -> Any:
        w, layout = self._w, self._w.Layout
        children = [w.HTML(self._html([card]), layout=layout(width="100%"))]
        if known is not None:
            button = w.Button(description=f"How {_clip(known.name, _NAME_CHARS)} was indexed ›",
                              tooltip=f"Open {known.key} in the Files tab: its state, chunks and metadata",
                              layout=layout(width="auto"))
            button.add_class("kbx-link")
            button.on_click(self._safely(lambda _button: self._jump_to(known)))
            children.append(button)
        box = w.VBox(children, layout=layout(width="100%"))
        box.add_class("kbx-hit")
        return box

    def _draw_hits(self, r: Retrieval) -> None:
        blocks: list[Any] = [_Title(_clip(r.question, _TITLE_CHARS), _search_subtitle(r)), self._search_cards(r),
                             _Findings(retrieval_findings(r))]
        self._draw("search", self.search_head, blocks)
        names = self.ui._source_names(r.kb_id, r.passages)
        cards = _passage_blocks(r.passages, question_terms(r.question), width=_PASSAGE_WIDTH, sources=names,
                                link=self.ui._link)
        listed = self._listed_files()
        hits = []
        for card, p in zip(cards, r.passages):
            known = listed.get(p.uri or "") or listed.get(unquote(p.uri or ""))
            hits.append(self._hit(card, known))
        self.hits.children = hits
        opens_a_file = any(len(hit.children) > 1 for hit in hits)
        self._status(f"Found {_plural(len(r.passages), 'passage')} in {r.seconds:.1f}s"
                     + (": click How it was indexed under one to open its file" if opens_a_file else ""))

    def _jump_to(self, f: KBFile) -> None:
        self._show_tab("files")
        self._open_file(f)
