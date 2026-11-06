"""
The explorer's Documents tab: a random sample of the index's documents on the left (each with the start of its text
and its vector's size, or that it has none, those first; the filter looks through every document the sample read),
and on the right how healthy the sample's vectors are, then the document picked: its text, its fields as a tree and
a button for the documents nearest to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Cards, _Findings, _Note, _Table, _Title
from ..._kit.text import _esc, search_rank
from ..._kit.window import _class_if, _skeleton

from ..helpers import values_at
from ..models import IndexInfo, Sample
from ..analysis.parsing import source_of
from .frame import _document_face
from .widgets import _Line

if TYPE_CHECKING:
    from .window import OpenSearchExplorer


_SAMPLE_SIZE = 40

_ROWS_SHOWN = 40

_CHECKED = 200

_HEAD_BLOCKS = (_Title, _Cards, _Findings, _Note)


def _sample_head(blocks: list[Any]) -> list[Any]:
    """
    sample()'s blocks up to its table of documents (the title, the vectors' cards and findings): the list on the
    left takes the table's place.
    """
    head: list[Any] = []
    for block in blocks:
        if isinstance(block, _Table):
            break
        if isinstance(block, _HEAD_BLOCKS):
            head.append(block)
    return head


def _document_text(doc: dict[str, Any], info: IndexInfo) -> tuple[str, ...]:
    """
    What the Documents tab's filter looks through: the ID first, then its text and where it came from.
    """
    text = values_at(doc, info.text_field) if info.text_field else []
    return (str(doc.get("_id", "")), str(text[0]) if text else "", source_of(doc))


class _DocumentsTab:
    """
    The Documents tab, a part of OpenSearchExplorer: what it keeps is `sample` (the Sample read for the index shown,
    or None until the tab is first opened), `doc` (the document shown on the right; `doc_from_hit` when it's a
    search's hit, which comes without its vectors) and `doc_query` (the filter's text).
    """

    def _documents_page(self: OpenSearchExplorer) -> Any:
        w, layout = self._w, self._w.Layout
        self.sample: Sample | None = None
        self.doc: dict[str, Any] | None = None
        self.doc_from_hit = False
        self.doc_query = ""
        self.docs_shown: list[dict[str, Any]] = []
        self._doc_rows: list[_Line] = []
        self.doc_find = w.Text(placeholder="Filter the sample: ID, text, source…", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.doc_find.add_class("osx-find")
        self.doc_find.observe(self._safely(self._doc_typed), names="value")
        self.resample_button = w.Button(description="↻ New sample", tooltip="Read another random sample",
                                        layout=layout(width="auto", flex="0 0 auto"))
        self.resample_button.add_class("osx-small")
        self.resample_button.on_click(self._safely(lambda _button: self._load_sample(again=True)))
        finder = w.HBox([self.doc_find, self.resample_button], layout=layout(width="100%", align_items="center"))
        finder.add_class("osx-filters")
        self.doc_rows = w.VBox(layout=layout(width="100%"))
        self.doc_rows.add_class("osx-rows")
        self.doc_count = w.HTML(layout=layout(width="100%"))
        count = w.HBox([self.doc_count], layout=layout(width="100%"))
        count.add_class("osx-pager")
        left = w.VBox([finder, self.doc_rows, count])
        for name in ("osx-left", "osx-docs"):
            left.add_class(name)
        self.similar_button = w.Button(description="≈ Nearest documents", button_style="primary",
                                       tooltip="Search for the documents whose vectors are nearest to this one's",
                                       layout=layout(width="auto", display="none"))
        self.similar_button.add_class("osx-small")
        self.similar_button.on_click(self._safely(lambda _button: self._search_like_doc()))
        bar = w.HBox([self.similar_button], layout=layout(width="100%", display="none"))
        bar.add_class("osx-bar")
        self.doc_bar = bar
        self.doc_view = w.HTML(layout=layout(width="100%"))
        right = w.VBox([bar, self.doc_view])
        right.add_class("osx-right")
        split = w.HBox([left, right])
        split.add_class("osx-split")
        return split

    def _clear_documents(self: OpenSearchExplorer) -> None:
        self.sample, self.doc, self.doc_query, self.doc_from_hit = None, None, "", False
        self._quietly(self.doc_find, value="")
        self.doc_rows.children = []
        self.doc_bar.layout.display = "none"
        self._set(self.doc_count, "")
        self._set(self.doc_view, _skeleton("Waiting for the index…"))

    def _load_sample(self: OpenSearchExplorer, *, again: bool = False) -> None:
        """
        Reads a random sample of the index shown, once per index (again=True reads a new one): the documents to
        click, and how healthy their vectors are.
        """
        if self.store is None or not self.index_name or (self.sample is not None and not again):
            return
        store, index = self.store, self.index_name
        if again:
            self.doc, self.doc_from_hit = None, False
        self._status(f"Sampling {index}…", busy=True, owner="sample")
        self._set(self.doc_view, _skeleton(f"Reading a random sample of {index}…"))
        self._later("sample", lambda: self.core.sample(store, index, _SAMPLE_SIZE, check=_CHECKED), self._got_sample,
                    self._sample_failed)

    def _sample_failed(self: OpenSearchExplorer, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._finish("sample", text, "warn", tabs=("documents",))
        self._draw("documents", self.doc_view, [_Note(text, "warn")])

    def _got_sample(self: OpenSearchExplorer, sample: Sample) -> None:
        self.sample = sample
        if self.doc is None:
            self.doc = sample.docs[0] if sample.docs else None
        self._draw_doc_rows()
        self._draw_document()
        first = next((b for b in self.shown.get("documents", []) if isinstance(b, _Findings)), None)
        healthy = first is None or not any(level == "warn" for level, _ in first.items)
        said = f"{len(sample.docs):,} documents sampled from {sample.index}"
        self._finish("sample", said + ("" if healthy else ": their vectors need a look"), tabs=("documents",))

    def _doc_info(self: OpenSearchExplorer) -> IndexInfo:
        if self.sample is not None and self.sample.info is not None:
            return self.sample.info
        return self.info or IndexInfo(self.index_name)

    def _doc_typed(self: OpenSearchExplorer, change: dict[str, Any]) -> None:
        self.doc_query = str(change["new"] or "").strip()
        self._draw_doc_rows()

    def _draw_doc_rows(self: OpenSearchExplorer) -> None:
        docs = self.sample.docs if self.sample else []
        info = self._doc_info()
        if self.doc_query:
            docs = [d for d in docs if search_rank(self.doc_query, _document_text(d, info)) is not None]
        vector_field = info.vectors[0].path if info.vectors else None
        if vector_field:
            docs = sorted(docs, key=lambda d: bool(values_at(d, vector_field)))
        found = len(docs)
        docs = docs[:_ROWS_SHOWN]
        self.docs_shown = docs
        while len(self._doc_rows) < len(docs):
            self._doc_rows.append(_Line(self, self._clicked_doc))
        words = self.doc_query.split()
        shown_id = str(self.doc.get("_id", "")) if self.doc else ""
        for row, doc in zip(self._doc_rows, docs):
            row.key = str(doc.get("_id", ""))
            self._set(row.face, _document_face(doc, text_field=info.text_field, vector_field=vector_field,
                                               words=words))
            _class_if(row.box, "osx-on", row.key == shown_id)
        children: list[Any] = [row.box for row in self._doc_rows[:len(docs)]]
        if not children and self.sample is not None:
            text = "No document in the sample matches the filter." if self.doc_query else "The index has no documents."
            children = [self._w.HTML(f'<div class="osx-empty">{_esc(text)}</div>')]
        self.doc_rows.children = children
        total = len(self.sample.docs) if self.sample else 0
        text = f"{len(docs):,} of {found:,} matching" if self.doc_query else f"{len(docs):,} of {total:,} sampled"
        if not self.doc_query and len(docs) == total:
            text = f"{total:,} sampled"
        self._set(self.doc_count, _esc(text) if total else "")

    def _draw_document(self: OpenSearchExplorer) -> None:
        """
        The right side: the sample's vector health, then the document picked.
        """
        if self.sample is None and self.doc is None:
            return
        info = self._doc_info()
        blocks = _sample_head(self.ui._sample_blocks(self.store, self.sample, _SAMPLE_SIZE)) if self.sample else []
        if self.doc is not None:
            blocks += self.ui._document_blocks(self.store, info, self.doc, with_vectors=not self.doc_from_hit)
        self._draw("documents", self.doc_view, blocks)
        has_vector = self.doc is not None and bool(info.vectors) and (
            self.doc_from_hit or bool(values_at(self.doc, info.vectors[0].path)))
        self.doc_bar.layout.display = "" if has_vector else "none"
        self.similar_button.layout.display = "" if has_vector else "none"

    def _show_document(self: OpenSearchExplorer, doc: dict[str, Any], *, from_hit: bool = False) -> None:
        self.doc, self.doc_from_hit = doc, from_hit
        shown_id = str(doc.get("_id", ""))
        for row in self._doc_rows:
            _class_if(row.box, "osx-on", row.key == shown_id)
        self._draw_document()

    def _clicked_doc(self: OpenSearchExplorer, line: _Line) -> None:
        doc = next((d for d in self.docs_shown if str(d.get("_id", "")) == line.key), None)
        if doc is None:
            return
        self._show_document(doc)
        if self.similar_button.layout.display == "none":
            self._status(f"Document {line.key}: its text and fields.")
        else:
            self._status(f"Document {line.key}: its text and fields. ≈ Nearest documents searches for the ones "
                         "closest to it.")

    def _search_like_doc(self: OpenSearchExplorer) -> None:
        if self.doc is not None:
            self.search(like=str(self.doc.get("_id", "")))
