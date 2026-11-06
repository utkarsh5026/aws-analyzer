"""
The explorer's Search tab: a box for a question (embedded with a Bedrock model that fits the index's vectors) or a
document's ID (its own vector is searched with), and k; then the hits on the left, best first, each opening in the
Documents tab, and on the right the search as search() shows it: the cards, findings, every hit with its score, how
to read the scores, and the search as Python.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.text import _clip, _esc
from ..._kit.window import _class_if, _skeleton

from ..models import SearchResult
from ..view import _hit_text
from .widgets import _Line

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_MODES = (("Question", "text"), ("Like a document", "like"))

_K_CHOICES = (5, 10, 20, 50)

_PLACEHOLDERS = {
    "text": "Ask what the documents answer, then Enter: how do refunds work?",
    "like": "A document's ID, then Enter: the documents nearest to it",
}


def _hit_face(rank: int, doc_id: str, score: str, said: str) -> str:
    """
    A hit's line: its rank, ID and score, and the start of its text.
    """
    return (f'<div class="fr"><span class="ei run">{rank}</span><div class="fm"><div class="fn">{_esc(doc_id)}</div>'
            f'<div class="ff">{_esc(said)}</div></div><div class="fz"><b>{_esc(score)}</b>score</div></div>')


class _SearchTab:
    """
    The Search tab, a part of OpenSearchExplorer: what it keeps is `result`, the last SearchResult on the index shown
    (None before a search).
    """

    def _search_page(self: OpenSearchExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.result: SearchResult | None = None
        self._hit_rows: list[_Line] = []
        self.mode_pick = w.ToggleButtons(options=list(_MODES), value="text", tooltips=[
            "Embed the words with a Bedrock model and search with that vector",
            "Search with the vector a document of the index already has"], layout=layout(width="auto"))
        self.mode_pick.observe(self._safely(self._mode_changed), names="value")
        self.query_box = w.Text(placeholder=_PLACEHOLDERS["text"], continuous_update=True,
                                layout=layout(flex="1 1 260px", width="auto"))
        self.query_box.add_class("osx-find")
        self.query_box.on_msg(self._on_enter(self._run_search))
        self.k_pick = w.Dropdown(options=[(f"k = {k}", k) for k in _K_CHOICES], value=10,
                                 tooltip="How many nearest documents to return", layout=layout(width="96px"))
        self.search_button = w.Button(description="Search", button_style="primary", layout=layout(width="auto"))
        self.search_button.add_class("osx-small")
        self.search_button.on_click(self._safely(lambda _button: self._run_search()))
        bar = w.HBox([self.mode_pick, self.query_box, self.k_pick, self.search_button], layout=layout(width="100%"))
        bar.add_class("osx-search")
        self.hit_rows = w.VBox(layout=layout(width="100%"))
        self.hit_rows.add_class("osx-rows")
        self.hit_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.hit_count], layout=layout(width="100%"))
        count.add_class("osx-pager")
        left = w.VBox([self.hit_rows, count])
        for name in ("osx-left", "osx-docs"):
            left.add_class(name)
        self.search_view = w.HTML(layout=layout(width="100%"))
        right = w.VBox([self.search_view])
        right.add_class("osx-right")
        split = w.HBox([left, right], layout=layout(flex="1 1 auto", min_height="0"))
        split.add_class("osx-split")
        page = w.VBox([bar, split])
        page.add_class("osx-flat")
        return page

    def _clear_search(self: OpenSearchExplorer) -> None:
        self.result = None
        self._draw_hits()
        self._draw_search_hint()

    def _draw_search_hint(self: OpenSearchExplorer) -> None:
        info = self.info
        if info is not None and not info.vectors:
            text = (f"{info.name} has no vector field, so it can't be searched by similarity. Pick a vector index in "
                    "the Indexes tab.")
            self._draw("search", self.search_view, [_Note(text)])
            return
        where = f" in {self.index_name}" if self.index_name else ""
        self._draw("search", self.search_view, [_Note(
            f"Type a question and press Enter to see the documents{where} nearest to it, or pick Like a document and "
            "paste a document's ID. The question is embedded with a Bedrock model that fits the index's vectors.")])

    def _mode_changed(self: OpenSearchExplorer, change: dict[str, Any]) -> None:
        self.query_box.placeholder = _PLACEHOLDERS[change["new"]]

    def _run_search(self: OpenSearchExplorer) -> None:
        text = str(self.query_box.value or "").strip()
        if not text:
            self._status("Type a question (or a document's ID) first.", "warn")
            return
        if self.mode_pick.value == "like":
            self.search(like=text, k=self.k_pick.value)
        else:
            self.search(text, k=self.k_pick.value)

    def _start_search(self: OpenSearchExplorer, question: str | None, like: str | None, k: int) -> None:
        """
        Runs a search on the index shown, in the background, and shows the Search tab.
        """
        if self.store is None or not self.index_name:
            raise ValueError("Pick an index first (the Indexes tab)")
        store, index = self.store, self.index_name
        self._quietly(self.mode_pick, value="like" if like is not None else "text")
        self.query_box.placeholder = _PLACEHOLDERS[self.mode_pick.value]
        self._quietly(self.query_box, value=like if like is not None else question or "")
        if k in _K_CHOICES:
            self._quietly(self.k_pick, value=k)
        self._show_tab("search")
        what = f"the documents nearest to {like}" if like is not None else f"“{_clip(question or '', 60)}”"
        self._status(f"Searching {index} for {what}…", busy=True, owner="search")
        self._set(self.search_view, _skeleton("Searching…" if like is not None else "Embedding the question…"))
        model = self.model
        self._later("search", lambda: self.core.search(store, index, question, like=like, k=k, model=model),
                    self._got_search, self._search_failed)

    def _search_failed(self: OpenSearchExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._finish("search", text, "warn", tabs=("search",))
        self._draw("search", self.search_view, [_Note(text, "warn")])

    def _got_search(self: OpenSearchExplorer, result: SearchResult) -> None:
        self.result = result
        self._draw("search", self.search_view, self.ui._search_blocks(self.store, result))
        self._draw_hits()
        best = f", the best scoring {result.hits[0].score:.3f}" if result.hits else ""
        self._finish("search", f"{len(result.hits):,} nearest documents in {result.index}{best}: click one to see "
                               "it.", tabs=("search",))

    def _draw_hits(self: OpenSearchExplorer) -> None:
        result = self.result
        hits = result.hits if result else []
        while len(self._hit_rows) < len(hits):
            self._hit_rows.append(_Line(self, self._clicked_hit))
        for row, hit in zip(self._hit_rows, hits):
            row.key = hit.id
            said = _hit_text(hit.source, result.text_field, 140) if result.text_field else ""
            self._set(row.face, _hit_face(hit.rank, hit.id, f"{hit.score:.3f}", said))
            _class_if(row.box, "osx-on", False)
        children: list[Any] = [row.box for row in self._hit_rows[:len(hits)]]
        if not children:
            text = "No documents came back." if result is not None else "The nearest documents show here, best first."
            children = [self._w.HTML(f'<div class="osx-empty">{text}</div>')]
        self.hit_rows.children = children
        self._set(self.hit_count, f"{len(hits):,} results" if result is not None else "")

    def _clicked_hit(self: OpenSearchExplorer, line: _Line) -> None:
        if line.key:
            self.document(line.key)
