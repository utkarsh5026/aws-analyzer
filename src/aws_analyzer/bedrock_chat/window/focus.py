"""
What the knowledge base chat window is showing, as a Focus (_kit/focus.py): its `focus` property, which the ✦ Ask
drawer asks the agent about, so "why didn't it find the refund policy?" starts from the question just asked, the
answer and the files it cited, and the setup it was asked with.
"""

from __future__ import annotations

from typing import Any

from ..._bedrock.prices import short_model
from ..._kit.fmt import _plural
from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from ..analysis.batches import Batch
from ..models import Answer
from ..style import _TAB_TITLES

_CHIP_CHARS = 80
_FOCUS_FILES = 10
_PERCENT = 100
_RUN_TABS = ("Test", "Runs")


def _file_name(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1] or uri


def _setup_parts(view: Any) -> list[FocusPart]:
    """
    What questions are asked with: the knowledge base, the model (none for Retrieve only), the data sources and
    files it's narrowed to.
    """
    parts = []
    if isinstance(view.kb, str) and view.kb:
        parts.append(FocusPart("knowledge_base", view.kb, view.core.kb_name(view.kb)))
    if view.retrieve_only:
        parts.append(FocusPart("mode", "retrieve", "Retrieve only"))
    elif isinstance(view.model, str) and view.model:
        parts.append(FocusPart("model", view.model, short_model(view.model)))
    if isinstance(view.data_source, dict) and view.data_source:
        names = ", ".join(view.data_source.values())
        parts.append(FocusPart("data_source", ", ".join(view.data_source), names))
    if isinstance(view.picked_files, list) and view.picked_files:
        files = view.picked_files[:_FOCUS_FILES]
        parts.append(FocusPart("files", "\n".join(files), _plural(len(view.picked_files), "file picked")))
    return parts


def _answer_parts(answer: Answer) -> list[FocusPart]:
    """
    The last question and what came back: for an answer, how much of it is grounded and the files it cited; for a
    retrieve-only search, how many passages it found and the best file.
    """
    parts = [FocusPart("question", answer.question, f"asked: {_clip(answer.question, _CHIP_CHARS)}")]
    if answer.retrieve_only:
        parts.append(FocusPart("passages", str(len(answer.sources)), _plural(len(answer.sources), "passage")))
        if answer.sources:
            parts.append(FocusPart("best", answer.sources[0].uri, f"best: {_file_name(answer.sources[0].uri)}"))
        return parts
    share = round(answer.grounded_share * _PERCENT)
    parts.append(FocusPart("grounded", f"{share}%", f"{share}% grounded"))
    cited = list(dict.fromkeys(source.uri for source in answer.sources if source.uri))
    if cited:
        names = ", ".join(_file_name(uri) for uri in cited[:_FOCUS_FILES])
        parts.append(FocusPart("cited", "\n".join(cited), f"cited: {_clip(names, _CHIP_CHARS)}"))
    else:
        parts.append(FocusPart("cited", "", "cited nothing"))
    return parts


def _run_parts(batch: Batch, number: int) -> list[FocusPart]:
    """
    A test run: its questions, and the ones whose expected file didn't come up (by question, one per line).
    """
    asked = len(batch.items)
    parts = [FocusPart("run", batch.id or str(number), f"test run {number} · {_plural(asked, 'question')}")]
    missed = [item.question for item in batch.items if item.expected and item.found is None]
    if missed:
        parts.append(FocusPart("missed", "\n".join(missed), f"{len(missed)} missed their file"))
    return parts


class _FocusReads:
    """
    The chat window's `focus`: what it's showing, as data.
    """

    @property
    def focus(self) -> Focus:
        """
        What the window is showing, as a Focus (aws_analyzer._kit.focus): its kind is 'chat' (nothing asked yet: the
        setup), 'answer' (the last question, and the answer or search that came back) or 'test_run' (the Test or Runs
        tab, on a test run: its questions and the ones that missed their file). x.focus.chips() reads it as chips.
        """
        view = self.view
        region = view.core.region or ""
        setup = _setup_parts(view)
        tab = _TAB_TITLES[self.tabs.selected_index or 0] if getattr(self, "tabs", None) is not None else ""
        on_a_run = tab in _RUN_TABS and self.batch is not None
        if on_a_run:
            number = view.batches.index(self.batch) + 1 if self.batch in view.batches else len(view.batches)
            parts = [*setup, FocusPart("tab", tab, f"{tab} tab"), *_run_parts(self.batch, number)]
            return Focus("bedrock_chat", "test_run", region=region, parts=tuple(parts))
        if view.answers:
            return Focus("bedrock_chat", "answer", region=region, parts=(*setup, *_answer_parts(view.answers[-1])))
        return Focus("bedrock_chat", "chat", region=region, parts=tuple(setup))
