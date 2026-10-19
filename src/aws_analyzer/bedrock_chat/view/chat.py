"""
BedrockChatView's commands to chat.
"""

from __future__ import annotations

from typing import Any, Callable

from ..._bedrock.blocks import _Answer, _Json
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import source_name
from ..._bedrock.words import best_snippet, question_terms
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from ..._kit.deps import _require
from ..._kit.errors import _Hint
from ..._kit.fmt import _plural, human_money
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors
from ..._kit.window import _cell_number

from ..helpers import _short
from ..models import Answer
from ..analysis.choices import describe_files
from ..analysis.findings import answer_cost, cited_ranks
from ..analysis.code import python_call
from ..blocks import _Code, _Turn, _meta_text, _score, _setting_marks


class _ChatCommands:
    """
    BedrockChatView's commands to chat: app(), ask(), retrieve(), new_chat(), transcript() and last(), with the
    blocks an answer or a search is drawn with.
    """

    def _turn(self, question: str, on_text: Callable[[str], None] | None = None, retrieve_only: bool = False
              ) -> Answer:
        """
        Asks one question of this conversation and keeps the answer. retrieve_only=True only searches (Retrieve):
        the passages join the conversation, and the Bedrock session the answers share isn't touched.
        """
        kb_id = self._kb_id()
        if retrieve_only:
            a = self.core.retrieve(kb_id, question, self.values, data_source=self._sources(kb_id),
                                   files=self._files_for(kb_id))
        else:
            a = self.core.ask(kb_id, question, self.values, model=self.model, session_id=self.session_id,
                              stream=on_text is not None, on_text=on_text, data_source=self._sources(kb_id),
                              files=self._files_for(kb_id))
            self.session_id = a.session_id
        a.kb_name = a.kb_name or self.core.kb_name(kb_id)
        self.answers.append(a)
        return a

    def _search_blocks(self, a: Answer, number: int, *, full: bool = False) -> list[Any]:
        """
        A retrieve-only search: title, cards, findings and every passage found, best first, with its score and the
        [n] the answer to the same question cites it as; full=True shows each passage whole, with the request, the
        response and the call in Python.
        """
        kb = a.kb_name or a.kb_id
        only = "".join([f" · only {describe_sources(a.data_sources)}" if a.data_sources else "",
                        f" · only {describe_files(a.files)}" if a.files else ""])
        other = self._counterpart(a)
        ranks = cited_ranks(a, other) if other is not None else {}
        findings = self._findings(a)
        top = max((p.score for p in a.sources if p.score is not None), default=None)
        cards: list[tuple[str, ...]] = [
            ("Passages", f"{len(a.sources):,}", "" if a.sources else "warn"),
            ("Best score", _score(top) or "-"),
            ("Files", f"{len({p.uri or p.source for p in a.sources}):,}"),
        ]
        if other is not None:
            cards.append(("Cited by the answer", f"{len(ranks)} of {len(a.sources)}"))
        cards += [("Est. cost", self._cost_text(a)), ("Time", f"{a.seconds:.1f}s")]
        blocks: list[Any] = [
            _Title(f"{kb}: {_clip(a.question, 80)}",
                   f"Bedrock Retrieve: the search only, no answer · question {number} of this conversation{only} · "
                   f"cost at {self._price_basis()}"),
            _Cards(cards),
            _Findings(findings),
        ]
        terms = question_terms(a.question)
        names = self._source_names(a.kb_id, a.sources)
        has_metadata = any(p.metadata for p in a.sources)
        rows = [
            [str(p.rank), _score(p.score) or "-", source_name(p.uri) or p.uri or "-",
             "-" if p.page is None else str(p.page)]
            + ([names.get(p.data_source_id, "-")] if names else [])
            + ([f"[{ranks[p.rank]}]" if p.rank in ranks else "-"] if other is not None else [])
            + [p.text if full else f'"{best_snippet(p.text, terms, 90)}"']
            + ([_meta_text(p)] if has_metadata else [])
            for p in a.sources
        ]
        headers = (["#", "Score", "File", "Page"] + (["Data source"] if names else [])
                   + (["Cited as"] if other is not None else []) + ["Passage"]
                   + (["Metadata"] if has_metadata else []))
        blocks.append(_Table(headers, rows, title="Passages, best first", max_rows=0))
        if full:
            marks = _setting_marks(a.settings, self.core.schema(), retrieve_only=True)
            blocks += [
                _Json(a.request, "Request sent", marks=marks, notes=self._json_notes(retrieve_only=True)),
                _Json(a.response, "Response", open_depth=3),
                _Code(python_call(a.request, self._region()), "The same call in Python"),
            ]
        sent = ", ".join(f"{k}={_short(v, 30)}" for k, v in a.settings.items()) or "none set"
        blocks.append(_Note(f"Retrieve sent the search settings only ({sent}), with the data source and files an "
                            "answer would use. Scores rank this search's passages: compare them with each other, not "
                            "with another search's. The cost is the question's embedding"
                            + (" and the reranking." if a.settings.get("reranker") else ".")))
        steps: list[tuple[str, str]] = []
        if other is None:
            steps.append((_call("ask", a.question), "the answer to the same question, to compare"))
        steps.append(("request(retrieve_only=True)", "the Retrieve request the next search sends") if full else
                     ("last()", "every passage in full, the request and the response"))
        top_source = None if a.data_sources else self._top_source(a.sources, names)
        if top_source:
            steps.append((_call("use", data_source=top_source[0]), f"search only data source {top_source[0]!r}, where "
                          f"{_plural(top_source[1], 'passage')} came from"))
        elif (a.settings.get("n") or 5) < 10:
            steps.append(("set(n=10)", "retrieve more passages"))
        blocks.append(_Next(steps))
        return blocks

    def _answer_blocks(self, a: Answer, number: int, *, full: bool = False) -> list[Any]:
        """
        Title, cards, the answer, findings and sources; full=True adds every passage in full, the request and the
        response.
        """
        if a.retrieve_only:
            return self._search_blocks(a, number, full=full)
        kb = a.kb_name or a.kb_id
        tokens = f"~{a.input_tokens + a.output_tokens:,}"
        only = "".join([f" · only {describe_sources(a.data_sources)}" if a.data_sources else "",
                        f" · only {describe_files(a.files)}" if a.files else ""])
        blocks: list[Any] = [
            _Title(f"{kb}: {_clip(a.question, 80)}",
                   f"Bedrock RetrieveAndGenerate · question {number} of this conversation{only} · cost at "
                   f"{self._price_basis(models=True)}"),
            _Cards([
                ("Grounded", f"{a.grounded_share:.0%}", "warn" if a.text.strip() and a.grounded_share < 0.5 else ""),
                ("Sources cited", f"{len(a.cited):,}"),
                ("Model", self._model_label(a.model)),
                ("Est. tokens", tokens),
                ("Est. cost", self._cost_text(a)),
                ("Time", f"{a.seconds:.1f}s"),
            ]),
            _Answer(a.text, a.citations),
            _Findings(self._findings(a)),
        ]
        terms = question_terms(a.question)
        names = self._source_names(a.kb_id, a.sources)
        has_metadata = any(p.metadata for p in a.sources)
        rows = [
            [str(i), source_name(p.uri) or p.uri or "-", "-" if p.page is None else str(p.page)]
            + ([names.get(p.data_source_id, "-")] if names else [])
            + [p.text if full else f'"{best_snippet(p.text, terms, 90)}"']
            + ([_meta_text(p)] if has_metadata else [])
            for i, p in enumerate(a.sources, 1)
        ]
        headers = (["#", "File", "Page"] + (["Data source"] if names else []) + ["Passage"]
                   + (["Metadata"] if has_metadata else []))
        blocks.append(_Table(headers, rows, title="Sources", max_rows=0))
        if full:
            marks = _setting_marks(a.settings, self.core.schema())
            blocks += [
                _Json(a.request, "Request sent", marks=marks, notes=self._json_notes(retrieve_only=False)),
                _Json(a.response, "Response", open_depth=3),
                _Code(python_call(a.request, self._region()), "The same call in Python"),
            ]
        blocks.append(_Note("Tokens and cost are estimated from characters: RetrieveAndGenerate doesn't report tokens, "
                            "and returns only the passages the answer cites."))
        steps = [("ask('a follow-up question')", "continues this conversation")]
        steps.append(("request()", "the JSON the next question sends") if full else
                     ("last()", "this answer's sources in full, its request and response"))
        top_source = None if a.data_sources else self._top_source(a.sources, names)
        if top_source:
            steps.append((_call("use", data_source=top_source[0]), f"ask only data source {top_source[0]!r}, where "
                          f"{_plural(top_source[1], 'source')} came from"))
        elif self._counterpart(a) is None:
            steps.append((_call("retrieve", a.question), "every passage the search finds, to see what the answer "
                          "left out"))
        else:
            steps.append(("app()", "the chat window, with settings and JSON side by side"))
        blocks.append(_Next(steps))
        return blocks

    # ---------------------------------------------------------------------- chat

    @_friendly_errors
    def app(self) -> None:
        """
        The chat window: pick the knowledge base and model, ask questions (follow-ups keep the conversation), and
        change what's sent in the Settings tab, with the exact request and response as JSON.
        """
        self._shown_in = _cell_number()
        if not self.use_html:
            raise _Hint(
                "The chat window needs Jupyter (SageMaker Studio, a notebook instance, JupyterLab or VS Code). Here, "
                "ask('a question') answers as a report, in the same conversation; settings() and request() show "
                "what's sent."
            )
        from ..window.app import _ChatApp

        widgets = _require("ipywidgets", "The chat window")
        if self._app is None:
            self._app = _ChatApp(self, widgets)
        self._display(self._app.root)

    @_friendly_errors
    def ask(self, question: str) -> None:
        """
        The answer to a question, with [1][2] citations, grounded %, sources, model, estimated cost and time.
        Each question follows up on the ones before it (new_chat() starts over); an open window shows it too.
        retrieve('...') shows only the search behind it.
        """
        with self._progress("Asking", unit="answers"):
            a = self._turn(question)
        if self._app is not None:
            self._app.added(a)
        self._show(self._answer_blocks(a, len(self.answers)))

    @_friendly_errors
    def retrieve(self, question: str) -> None:
        """
        Only the search behind an answer: every passage a question retrieves, best first, with its score, and no
        answer (Retrieve, with the same settings, data source and files, without the model). Asked both ways, the
        same question shows which passages the answer cites, and whether a poor answer comes from the search or
        from the model. It joins the conversation, and an open window shows it too.
        """
        with self._progress("Searching", unit="searches"):
            a = self._turn(question, retrieve_only=True)
        if self._app is not None:
            self._app.added(a)
        self._show(self._answer_blocks(a, len(self.answers)))

    @_friendly_errors
    def new_chat(self) -> None:
        """
        Forgets the conversation: the next question starts a new Bedrock session. The settings stay.
        """
        count = len(self.answers)
        self._reset()
        self._changed("New conversation: the next question starts a new Bedrock session.")
        self._show([_Note(f"New conversation ({_plural(count, 'earlier question')} forgotten). The settings stay: "
                          "settings() shows them.", "ok")])

    @_friendly_errors
    def transcript(self) -> None:
        """
        The conversation so far, as a report that stays in the notebook when it's saved (the window doesn't).
        """
        if not self.answers:
            raise _Hint("No questions yet: ask('...') or app() first.")
        a0 = self.answers[0]
        costs = [answer_cost(a, self.core.model_prices, self.core.prices) for a in self.answers]
        searches = sum(a.retrieve_only for a in self.answers)
        apis = " and ".join(name for name, used in (("RetrieveAndGenerate", searches < len(self.answers)),
                                                    ("Retrieve", searches)) if used)
        models = ", ".join(dict.fromkeys(self._model_label(a.model) for a in self.answers if not a.retrieve_only))
        cards: list[tuple[str, ...]] = [("Questions", f"{len(self.answers):,}")]
        cards += [("Retrieve only", f"{searches:,}")] if searches else []
        cards += [("Est. cost", human_money(sum(c for c in costs if c is not None))), ("Models", models or "-"),
                  ("Session", (self.session_id or "-")[:12])]
        blocks: list[Any] = [
            _Title(f"Conversation with {a0.kb_name or a0.kb_id} ({_plural(len(self.answers), 'question')})",
                   f"Bedrock {apis} · costs estimated at {self._price_basis(models=True)}"),
            _Cards(cards),
        ]
        blocks += [_Turn(a, self._meta(a), self._findings(a), self._ranks(a)) for a in self.answers]
        blocks.append(_Next([("last()", "the last answer's sources in full, its request and response"),
                             ("new_chat()", "start over")]))
        self._show(blocks)

    @_friendly_errors
    def last(self) -> None:
        """
        The last answer in full: every cited passage, the exact request sent and the response, and the same call in
        Python. After a retrieve-only search, every passage it found, in full.
        """
        if not self.answers:
            raise _Hint("No questions yet: ask('...') or app() first.")
        self._show(self._answer_blocks(self.answers[-1], len(self.answers), full=True))
