"""
BedrockKBView's commands to measure retrieval.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..._bedrock.estimates import query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.words import best_snippet, question_terms
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from ..._kit.fmt import _plural, human_money
from ..._kit.render import _call
from ..._kit.text import _clip
from ..._kit.view import _friendly_errors

from ..analysis.filters import _source_arg, describe_filter
from ..analysis.deciding import _setting
from ..analysis.findings import _search_label, comparison_findings, eval_findings
from ..blocks import _Link


class _MeasureCommands:
    """
    BedrockKBView's commands to measure retrieval: compare() and evaluate().
    """

    # ------------------------------------------------------------------ deciding

    @_friendly_errors
    def compare(
        self,
        question: str,
        *,
        kb: str | None = None,
        n: int | Iterable[int] = (5, 10),
        search_types: str | Iterable[str | None] = ("SEMANTIC", "HYBRID"),
        where: Any = None,
        data_source: Any = None,
    ) -> None:
        """
        One row per passage and one column per search setting (its rank there, or "-"), overlap cards and findings.
        data_source= compares searches of one data source only.
        """
        kb_id = self._kb(kb)
        with self._progress("Searching", unit="searches") as tick:
            c = self.core.compare(
                kb_id,
                question,
                n=n,
                search_types=search_types,
                where=where,
                data_source=data_source,
                progress=tick,
            )
        labels = list(c.runs)
        seconds = sum(r.seconds for r in c.runs.values())
        pairs = [
            (a, b)
            for (a, b) in c.overlap
            if _setting(a)[1] == _setting(b)[1] or _setting(a)[0] == _setting(b)[0]
        ]
        cards = [("Settings tried", f"{len(labels) + len(c.errors):,}")]
        cards += [
            (f"{a} vs {b}", f"{c.overlap[(a, b)]:.0%} overlap") for a, b in pairs[:4]
        ]
        cards += [
            ("Time", f"{seconds:.1f}s"),
            (
                "Est. cost",
                human_money(query_cost(len(labels), prices=self.core.prices)),
            ),
        ]
        sub = (
            "overlap = passages both settings found, out of all they found"
            + (f" · only {describe_sources(c.data_sources)}" if c.data_sources else "")
            + (f" · where {describe_filter(where)}" if where is not None else "")
        )
        blocks: list[Any] = [
            _Title(f"Compare searches in {c.kb_name}: {_clip(c.question, 80)}", sub),
            _Cards(cards),
        ]
        blocks.append(_Findings(comparison_findings(c)))
        terms = question_terms(c.question)
        rows = [
            [_Link(self._link(p), p.source), best_snippet(p.text, terms, 70)]
            + ["-" if ranks[label] is None else str(ranks[label]) for label in labels]
            for p, ranks in c.ranks()
        ]
        blocks.append(
            _Table(
                ["Source", "Passage"] + labels,
                rows,
                title="Rank of each passage under each setting",
                max_rows=0,
            )
        )
        if labels:
            kind, size = _setting(labels[-1])
            setting = {"search_type": kind} if kind != "DEFAULT" else {}
            only = (
                {"data_source": _source_arg(c.data_sources)} if c.data_sources else {}
            )
            blocks.append(
                _Next(
                    [
                        (
                            _call(
                                "search",
                                c.question,
                                int(size),
                                **setting,
                                **self._on(c.kb_id, c.kb_name),
                                **only,
                            ),
                            "one setting's passages in full",
                        )
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def evaluate(
        self,
        cases: Any,
        *,
        kb: str | None = None,
        n: int = 5,
        search_type: str | None = None,
        where: Any = None,
        data_source: Any = None,
    ) -> None:
        """
        Retrieval hit rate @n and MRR on test questions: where each expected source ranked (or missed), and what
        came up first instead. cases: [(question, expected file or text), ...]. data_source= checks one data source.
        """
        kb_id = self._kb(kb)
        with self._progress("Checking questions", unit="questions") as tick:
            report = self.core.evaluate(
                kb_id,
                cases,
                n=n,
                search_type=search_type,
                where=where,
                data_source=data_source,
                progress=tick,
            )
        sub = [
            f"top {report.k}",
            _search_label(report.search_type),
            "retrieval only (no answers generated)",
        ]
        if report.data_sources:
            sub.append(f"only {describe_sources(report.data_sources)}")
        if where is not None:
            sub.append(f"where {describe_filter(where)}")
        blocks: list[Any] = [
            _Title(
                f"Retrieval check on {report.kb_name}: {_plural(len(report.cases), 'question')}",
                " · ".join(sub),
            ),
            _Cards(
                [
                    (f"Hit rate @{report.k}", f"{report.hit_rate:.0%}"),
                    ("MRR", f"{report.mrr:.2f}"),
                    ("Questions", f"{len(report.cases):,}"),
                    (
                        "Missed",
                        f"{len(report.missed):,}",
                        "warn" if report.missed else "ok",
                    ),
                    ("Time", f"{report.seconds:.1f}s"),
                    (
                        "Est. cost",
                        human_money(
                            query_cost(len(report.cases), prices=self.core.prices)
                        ),
                    ),
                ]
            ),
        ]
        blocks.append(
            _Findings(
                eval_findings(report), empty="Every expected source came up first."
            )
        )
        rows = [
            [
                c.question,
                c.expected
                if isinstance(c.expected, str)
                else ", ".join(map(str, c.expected)),
                "missed" if c.rank is None else f"#{c.rank}",
                ", ".join(list(dict.fromkeys(c.top_sources))[:2]) or "-",
            ]
            for c in report.cases
        ]
        blocks.append(
            _Table(["Question", "Expected", "Rank", "Came up first"], rows, max_rows=0)
        )
        blocks.append(
            _Note(
                "MRR (mean reciprocal rank) averages 1/rank: 1.00 means the expected source always came "
                "first, 0.50 second on average."
            )
        )
        if report.missed:
            question = report.missed[0].question
            on: dict[str, Any] = dict(self._on(report.kb_id, report.kb_name))
            if report.data_sources:
                on["data_source"] = _source_arg(report.data_sources)
            blocks.append(
                _Next(
                    [
                        (
                            _call("search", question, report.k, **on),
                            "what came up instead for the first miss",
                        ),
                        (
                            _call("compare", question, **on),
                            "whether another search setting finds it",
                        ),
                    ]
                )
            )
        self._show(blocks)
