"""
BedrockKBView's commands to search and answer.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..._bedrock.blocks import _Answer
from ..._bedrock.estimates import estimate_tokens, generation_cost, query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import _session_expired, source_name
from ..._bedrock.models import Passage
from ..._bedrock.prices import _per_million, short_model
from ..._bedrock.words import best_snippet, question_terms
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title
from ..._kit.errors import _Hint, _why
from ..._kit.fmt import _as_int, _count, _plural, human_money
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..models import Answer
from ..analysis.filters import _source_arg, describe_filter
from ..analysis.findings import _search_label, answer_findings, retrieval_findings
from ..blocks import _Link
from .cells import _open_label, _passage_blocks, _turns


class _SearchCommands:
    """
    BedrockKBView's commands to search and answer: search(), chunk(), link(), ask(), follow_up() and models().
    """

    # ---------------------------------------------------------------- retrieval

    @_friendly_errors
    def search(
        self,
        question: str,
        n: int = 5,
        *,
        kb: str | None = None,
        data_source: Any = None,
        where: Any = None,
        search_type: str | None = None,
        rerank: str | bool | None = None,
    ) -> None:
        """
        Ranked passages for a question, with score bars, source and page, highlighted words and metadata.
        Also findings, time and cost. data_source= searches only one data source (its name or ID, or a list of them);
        where= filters on metadata: where={'team': 'billing', 'year': ('>=', 2024)}.
        """
        kb_id = self._kb(kb)
        with self._progress("Searching", unit="passages"):
            r = self.core.retrieve(
                kb_id,
                question,
                n,
                where=where,
                search_type=search_type,
                rerank_model=rerank,
                data_source=data_source,
            )
        self._last = r
        terms = question_terms(r.question)
        top = max((p.score for p in r.passages if p.score is not None), default=None)
        sub = [
            f"{len(r.passages)} of up to {r.n} passages",
            _search_label(r.search_type),
        ]
        if r.data_sources:
            sub.append(f"only {describe_sources(r.data_sources)}")
        if where is not None:
            sub.append(f"where {describe_filter(where)}")
        if r.reranked:
            sub.append(f"reranked by {r.reranked}")
        sub.append(f"cost at {self._price_basis()}")
        blocks: list[Any] = [
            _Title(f"Search {r.kb_name}: {_clip(r.question, 80)}", " · ".join(sub)),
            _Cards(
                [
                    ("Passages", f"{len(r.passages):,}"),
                    ("Top score", "-" if top is None else f"{top:.2f}"),
                    ("Files", f"{len({p.uri for p in r.passages}):,}"),
                    ("Time", f"{r.seconds:.1f}s"),
                    (
                        "Est. cost",
                        human_money(query_cost(1, r.reranked or False, self.core.prices))
                        + (
                            " (question embedding and reranking)"
                            if r.reranked
                            else " (question embedding)"
                        ),
                    ),
                ]
            ),
        ]
        blocks.append(_Findings(retrieval_findings(r)))
        names = self._source_names(r.kb_id, r.passages)
        blocks += _passage_blocks(r.passages, terms, sources=names, link=self._link)
        if r.passages:
            only = (
                {"data_source": _source_arg(r.data_sources)} if r.data_sources else {}
            )
            same = {
                **self._on(r.kb_id, r.kb_name),
                **only,
                **({"where": where} if where is not None else {}),
                **({"search_type": search_type} if search_type else {}),
            }
            steps = [
                ("chunk(1)", "result #1 in full, with its metadata"),
                (
                    _call("ask", r.question, **same),
                    "an answer from passages like these",
                ),
            ]
            top_source = (
                None if r.data_sources else self._top_source(r.passages, names)
            )
            if top_source:
                steps.append(
                    (
                        _call(
                            "search",
                            r.question,
                            **self._on(r.kb_id, r.kb_name),
                            data_source=top_source[0],
                        ),
                        f"only data source {top_source[0]!r}, where {top_source[1]} of these came from",
                    )
                )
            else:
                steps.append(
                    (
                        _call(
                            "compare",
                            r.question,
                            **self._on(r.kb_id, r.kb_name),
                            **only,
                        ),
                        "how other search settings rank them",
                    )
                )
            blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def chunk(self, rank: int = 1) -> None:
        """
        The full text and metadata of result #rank from the last search or ask, and a link that opens its file in
        a new tab.
        """
        last = self._last
        if last is None:
            raise _Hint(
                "Nothing to show yet: run search('...') or ask('...') first, then chunk(1)."
            )
        answer = isinstance(last, Answer)
        passages = last.sources if answer else last.passages
        rank = _as_int(rank, "rank")
        if not 1 <= rank <= len(passages):
            raise ValueError(
                f"rank goes from 1 to {len(passages)}: the last {'answer' if answer else 'search'} has "
                f"{_plural(len(passages), 'source' if answer else 'passage')}"
            )
        p = passages[rank - 1]
        ds_name = self.core.data_source_name(last.kb_id, p.data_source_id)
        source = (
            f"{ds_name} ({p.data_source_id})"
            if ds_name != p.data_source_id
            else p.data_source_id or "-"
        )
        blocks: list[Any] = [
            _Title(f"{'Source' if answer else 'Result'} #{rank}: {p.source}", p.uri),
            _Cards(
                [
                    ("Score", "-" if p.score is None else f"{p.score:.3f}"),
                    ("Page", _count(p.page)),
                    ("Words", f"{len(p.text.split()):,}"),
                    ("Tokens (estimate)", f"~{estimate_tokens(p.text):,}"),
                    ("Kind", p.content_type.lower()),
                    ("Data source", source),
                ]
            ),
        ]
        url = self._link(p)
        if url:
            blocks.append(_Link(url, _open_label(p, url)))
        blocks.append(_Text(p.text, title="Full text", wrap=True))
        if p.row:
            blocks.append(
                _Table(
                    ["Column", "Value"], [[k, v] for k, v in p.row.items()], title="Row"
                )
            )
        rows = [[k, v] for k, v in sorted(p.metadata.items())]
        blocks.append(
            _Table(
                ["Attribute", "Value"], rows, title="Metadata (what where= filters on)"
            )
        )
        if not rows:
            blocks.append(
                _Note(
                    "No metadata on this passage: where= filters need a <file>.metadata.json next to each "
                    "file, then a sync."
                )
            )
        stored = [
            ["Chunk ID", p.chunk_id or "-"],
            ["Data source", p.data_source_id or "-"],
            ["Location type", p.location_type or "-"],
        ]
        blocks.append(
            _Table(
                ["Field", "Value"], stored, title="Where it's stored", collapsed=True
            )
        )
        if p.uri.startswith("s3://"):
            blocks.append(
                _Note(
                    f"The link above works for an hour; link({rank}) makes a fresh one. To preview the file here "
                    f"instead: S3View().preview({p.uri!r}), from s3.py in this repo (import s3 first)."
                    if url
                    else f"To open the whole file: S3View().preview({p.uri!r}), from s3.py in this repo "
                    "(import s3 first)."
                )
            )
        self._show(blocks)

    @_friendly_errors
    def link(self, source: Any = 1, *, expires: int = 3600) -> None:
        """
        A link that opens a file in a new browser tab: source #n of the last search or ask (a PDF opens at the
        passage's page), a file name from it, or any s3:// path.

        An S3 file gets a presigned link, signed here with your credentials (no AWS call). It works for `expires`
        seconds (an hour unless you say), only while your credentials do, and only if they may read the file
        (s3:GetObject); anyone you send it to can open the file until then. The browser shows PDFs, pictures, text
        and HTML in the tab; Word and Excel files download. A web, Confluence, SharePoint or Salesforce source links
        to its page. search() and ask() already link each source's name; this is the link on its own, for text mode
        or for longer than an hour: link(2, expires=86400).
        """
        if isinstance(source, str) and "://" in source:
            p, number, what = Passage(rank=0, text="", uri=source.strip()), 0, repr(source.strip())
        else:
            p, number, what = self._pick_source(source)
        url = self.core.file_url(p.uri, page=p.page, expires=expires)
        if not url and not number:
            raise _Hint(f"{what} isn't a file to open: pass an s3://bucket/key path or a web address.")
        if not url:
            raise _Hint(
                f"{what} has no file to open: it came from a {p.location_type or 'non-S3'} data source. "
                f"chunk({number}) shows its full text."
            )
        blocks: list[Any] = [_Link(url, _open_label(p, url, _as_int(expires, "expires")))]
        if p.uri.startswith("s3://"):
            blocks.append(
                _Note(
                    "Signed with your credentials, so it stops working sooner if they expire first, and opens only "
                    "if they may read the file (s3:GetObject). Anyone with the link can open the file until then."
                )
            )
        self._show(blocks)

    def _pick_source(self, source: Any) -> tuple[Passage, int, str]:
        """
        link()'s source: a number, or a file name, from the last search or answer -> (passage, its number as
        chunk() takes it, how to name it).
        """
        last = self._last
        if last is None:
            raise _Hint(
                "Nothing to link yet: run search('...') or ask('...') first, then link(1), or pass an s3:// path."
            )
        answer = isinstance(last, Answer)
        passages = last.sources if answer else last.passages
        kind = "source" if answer else "result"
        try:
            rank = _as_int(source, kind)
        except ValueError:
            wanted = str(source).strip().lower()
            found = [p for p in passages if source_name(p.uri).lower() == wanted] or [
                p for p in passages if wanted and wanted in p.uri.lower()
            ]
            if not found:
                names = ", ".join(sorted({source_name(p.uri) for p in passages if p.uri})[:5]) or "none"
                raise _Hint(
                    f"No {kind} of the last {'answer' if answer else 'search'} is {source!r}: pass its number "
                    f"(link(1)) or one of its files ({names})."
                ) from None
            rank = passages.index(found[0]) + 1
            return found[0], rank, f"{kind.capitalize()} #{rank}"
        if not 1 <= rank <= len(passages):
            raise ValueError(
                f"{kind} goes from 1 to {len(passages)}: the last {'answer' if answer else 'search'} has "
                f"{_plural(len(passages), kind)}"
            )
        return passages[rank - 1], rank, f"{kind.capitalize()} #{rank}"

    # --------------------------------------------------------------- generation

    def _cost_label(self, a: Answer) -> str:
        cost = generation_cost(
            a.input_tokens, a.output_tokens, a.model, self.core.model_prices
        )
        if cost is None:
            return "unknown (pass model_prices=...)"
        text = human_money(cost)
        return "~" + text if a.tokens_estimated and not text.startswith("<") else text

    def _answer_blocks(
        self, a: Answer, title: str, notes: list[Any] | None = None
    ) -> list[Any]:
        """
        Title, cards, the answer, warnings, the sources table, then the notes.
        """
        engine = "KB engine" if a.engine == "kb" else "Converse"
        tokens = (
            f"~{a.input_tokens + a.output_tokens:,} (estimate)"
            if a.tokens_estimated
            else f"{a.input_tokens:,} in + {a.output_tokens:,} out"
        )
        how = (
            "Bedrock RetrieveAndGenerate"
            if a.engine == "kb"
            else "Retrieve, then Converse"
        )
        only = f" · only {describe_sources(a.data_sources)}" if a.data_sources else ""
        sub = (
            f"{how} · {_plural(len(a.sources), 'source')}{only} · cost at "
            f"{self._price_basis(models=True)}"
        )
        used = len(a.cited)
        blocks: list[Any] = [
            _Title(f"{title} {a.kb_name or a.kb_id}: {_clip(a.question, 80)}", sub),
            _Cards(
                [
                    (
                        "Grounded",
                        f"{a.grounded_share:.0%}",
                        "warn" if a.text.strip() and a.grounded_share < 0.5 else "",
                    ),
                    ("Sources used", f"{used:,}"),
                    ("Model", f"{short_model(a.model)} ({engine})"),
                    ("Tokens", tokens),
                    ("Est. cost", self._cost_label(a)),
                    ("Time", f"{a.seconds:.1f}s"),
                ]
            ),
            _Answer(a.text, a.citations, inline=a.engine == "converse"),
        ]
        blocks.append(_Findings(answer_findings(a)))
        cited = set(a.cited)
        terms = question_terms(a.question)
        names = self._source_names(a.kb_id, a.sources)
        headers = (
            ["#", "File", "Page"]
            + (["Data source"] if names else [])
            + ([] if a.engine == "kb" else ["Cited"])
            + ["Passage"]
        )
        rows = [
            [str(i), _Link(self._link(p), source_name(p.uri) or p.uri or "-"), _count(p.page)]
            + ([names.get(p.data_source_id, "-")] if names else [])
            + ([] if a.engine == "kb" else ["yes" if i in cited else ""])
            + [f'"{best_snippet(p.text, terms, 90)}"']
            for i, p in enumerate(a.sources, 1)
        ]
        blocks.append(_Table(headers, rows, title="Sources", max_rows=0))
        blocks += notes or []
        if a.tokens_estimated:
            blocks.append(
                _Note(
                    "Tokens and cost are estimated from characters: RetrieveAndGenerate doesn't return "
                    "token counts."
                )
            )
        steps = (
            [("chunk(1)", "source #1 in full, with its metadata")] if a.sources else []
        )
        steps.append(
            ("follow_up('...')", "a follow-up question that keeps this conversation")
        )
        top_source = (
            None if a.data_sources else self._top_source(a.sources, names)
        )
        if top_source and title == "Ask":
            steps.append(
                (
                    _call(
                        "ask",
                        a.question,
                        **self._on(a.kb_id, a.kb_name or a.kb_id),
                        data_source=top_source[0],
                    ),
                    f"the answer from data source {top_source[0]!r} only, where "
                    f"{_plural(top_source[1], 'source')} came from",
                )
            )
        elif (
            a.tokens_estimated and title == "Ask"
        ):  # a follow-up's question alone lacks the earlier turns
            steps.append(
                (
                    _call(
                        "ask",
                        a.question,
                        engine="converse",
                        **self._on(a.kb_id, a.kb_name or a.kb_id),
                        **(
                            {"data_source": _source_arg(a.data_sources)}
                            if a.data_sources
                            else {}
                        ),
                    ),
                    "the same question with exact tokens and cost",
                )
            )
        blocks.append(_Next(steps))
        return blocks

    @_friendly_errors
    def ask(
        self,
        question: str,
        *,
        kb: str | None = None,
        data_source: Any = None,
        n: int = 5,
        where: Any = None,
        search_type: str | None = None,
        model: str | None = None,
        engine: str = "kb",
        prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """
        The answer with [1][2] citations, grounded %, sources used, model, tokens, cost and time.
        Also a sources table and findings. data_source= answers from one data source only (its name or ID, or a list
        of them), and follow-ups keep it. engine='converse' gives exact tokens and cost, and takes your prompt=.
        """
        kb_id = self._kb(kb)
        with self._progress("Asking", unit="answers"):
            a = self.core.ask(
                kb_id,
                question,
                engine=engine,
                data_source=data_source,
                n=n,
                where=where,
                search_type=search_type,
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        self._last = a
        self._conversation = {
            "engine": a.engine,
            "kb": kb_id,
            "session_id": a.session_id,
            "question": a.question,
            "history": _turns(a.question, a),
            "turns": 1,
            "options": {
                "data_source": a.data_sources,
                "n": n,
                "where": where,
                "search_type": search_type,
                "model": model,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        }
        self._show(self._answer_blocks(a, "Ask"))

    @_friendly_errors
    def follow_up(self, question: str, *, data_source: Any = None) -> None:
        """
        The answer to a follow-up question, in the same session (engine='kb') or conversation as the last ask().
        It searches the data source the conversation does; data_source= moves this and later follow-ups to another
        one, and data_source='all' back to every data source.
        """
        conv = self._conversation
        if conv is None:
            raise _Hint(
                "Nothing to follow up yet: ask('...') first, then follow_up('...')."
            )
        options = conv["options"]
        if data_source is not None:
            options["data_source"] = self.core.resolve_sources(conv["kb"], data_source)
        notes: list[Any] = []
        with self._progress("Asking", unit="answers"):
            if conv["engine"] == "kb":
                try:
                    a = self.core.retrieve_and_generate(
                        conv["kb"], question, session_id=conv["session_id"], **options
                    )
                except ClientError as exc:
                    if not _session_expired(exc):
                        raise
                    a = self.core.retrieve_and_generate(conv["kb"], question, **options)
                    notes.append(
                        _Note(
                            "The earlier session had expired (Bedrock ends them after a while), so this "
                            "question started a new one: it was answered without the earlier questions."
                        )
                    )
            else:  # search with the previous question too, so 'and for digital goods?' finds the right passages
                r = self.core.retrieve(
                    conv["kb"],
                    f"{conv['question']} {question}",
                    options["n"],
                    where=options["where"],
                    search_type=options["search_type"],
                    data_source=options["data_source"],
                )
                a = self.core.generate(
                    question,
                    r.passages,
                    model=options["model"],
                    prompt=options["prompt"],
                    history=conv["history"][-20:],
                    temperature=options["temperature"],
                    max_tokens=16_000
                    if options["max_tokens"] is None
                    else options["max_tokens"],
                )
                a.kb_id, a.kb_name, a.seconds = (
                    r.kb_id,
                    r.kb_name,
                    a.seconds + r.seconds,
                )
                a.data_sources = r.data_sources
        conv.update(
            session_id=a.session_id,
            question=question,
            history=conv["history"] + _turns(question, a),
            turns=conv["turns"] + 1,
        )
        self._last = a
        self._show(self._answer_blocks(a, f"Follow-up {conv['turns']} to", notes))

    @_friendly_errors
    def models(self, match: str | None = None) -> None:
        """
        Models you can use for ask() here: the ID to pass as model=, provider, on demand or through an inference
        profile, and $ per 1M tokens in and out.
        """
        with self._progress("Listing models", unit="models"):
            models = self.core.models(match)
        try:
            default = self.core.resolve_model(None)[0]
        except ValueError:
            default = "not offered here"
        rows = [
            [
                m.invoke_id,
                m.name,
                m.provider,
                m.via + (" (legacy)" if m.status == "LEGACY" else ""),
                _per_million(m.price_in),
                _per_million(m.price_out),
            ]
            for m in models
        ]
        blocks: list[Any] = [
            _Title(
                f"Models for ask() in {self.core.region} ({len(models)})",
                (f"matching {match!r} · " if match else "")
                + "text models; $ per 1M tokens at "
                + self._price_basis(models=True),
            ),
            _Cards(
                [
                    ("Models", f"{len(models):,}"),
                    ("On demand", f"{sum(m.via == 'on-demand' for m in models):,}"),
                    (
                        "Through a profile",
                        f"{sum(m.via == 'inference profile' for m in models):,}",
                    ),
                    ("Default for ask()", default),
                ]
            ),
            _Table(
                [
                    "Pass as model=",
                    "Name",
                    "Provider",
                    "How it's called",
                    "$ in / 1M",
                    "$ out / 1M",
                ],
                rows,
                max_rows=0,
            ),
            _Note(
                "Short names work too: model='opus', 'sonnet' or 'haiku' pick the current Claude model of that kind. A "
                "model you haven't enabled fails with AccessDeniedException: enable it in the Bedrock console under "
                "Model access. '-' means no price in the table: pass BedrockKBAnalyzer(model_prices={...})."
            ),
        ]
        if "profiles" in self.core.model_errors:
            blocks.insert(
                2,
                _Note(
                    "Couldn't list inference profiles ("
                    f"{_why(self.core.model_errors['profiles'], 'bedrock:ListInferenceProfiles')}), so "
                    "models that need one show 'inference profile (unknown)'. Calling one names the "
                    "profile to use.",
                    "warn",
                ),
            )
        self._show(blocks)
