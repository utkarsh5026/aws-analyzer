"""
The Search tab's mixin: what a question retrieves, each passage opening its file's page.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from ..._bedrock.estimates import query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.words import question_terms
from ..._kit.blocks import _Cards, _Findings, _Note, _Title
from ..._kit.fmt import _plural, human_money
from ..._kit.text import _clip
from ..._kit.window import _skeleton

from ..models import KBFile, Retrieval
from ..analysis.findings import _search_label, retrieval_findings
from ..view.cells import _passage_blocks
from .frame import _RESULTS

_SEARCH_TYPES = [("Default", ""), ("Semantic", "SEMANTIC"), ("Hybrid", "HYBRID")]
_SEARCH_TYPE_TIPS = ["Bedrock's choice for this vector store", "Matches meaning",
                     "Meaning and exact words (codes, names): OpenSearch, Aurora and MongoDB stores"]
_RERANKERS = [("No reranker", ""), ("Cohere Rerank 3.5", "cohere"), ("Amazon Rerank", "amazon")]
_PASSAGE_WIDTH = 420
_TITLE_CHARS = 90
_NAME_CHARS = 40


def _search_subtitle(r: Retrieval) -> str:
    parts = [f"{len(r.passages)} of up to {r.n} passages", _search_label(r.search_type)]
    if r.data_sources:
        parts.append(f"only {describe_sources(r.data_sources)}")
    if r.reranked:
        parts.append(f"reranked by {r.reranked}")
    return " · ".join(parts)


class _SearchTab:
    """
    The Search tab of KBExplorer: a question, how many passages, the search type, a data source and a reranker,
    then the passages found, each with a button to the page of the file it came from.
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
        ask = w.HBox([self.question, self.search_button], layout=layout(width="100%"))
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

    def _search_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.search_head = w.HTML(layout=layout(width="100%"))
        self.hits = w.VBox(layout=layout(width="100%"))
        self.hits.add_class("kbx-hits")
        return w.VBox([self._search_box(), self._search_options(), self.search_head, self.hits])

    def _search_line(self) -> str:
        if self.found is None:
            return "Type a question and press Enter: each passage found opens its file's page."
        return f"{_plural(len(self.found.passages), 'passage')} for {_clip(self.found.question, 60)!r}"

    def _searching(self, busy: bool) -> None:
        self.search_button.disabled = busy
        self.search_button.description = "Searching…" if busy else "Search"

    def _searched(self, r: Retrieval) -> None:
        self._searching(False)
        self.found = r
        self._draw_hits(r)

    def _search_failed(self, exc: BaseException) -> None:
        self._searching(False)
        self._set(self.search_head, self._html([_Note(self._error_text(exc), "warn")]))

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
        self._searching(True)
        self._set(self.search_head, _skeleton("Searching…"))
        self.hits.children = []
        self._later("search", lambda: self.core.retrieve(kb_id, question, n, search_type=kind, data_source=source,
                                                         rerank_model=rerank or None),
                    self._searched, self._search_failed)

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
