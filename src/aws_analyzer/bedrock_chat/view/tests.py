"""
BedrockChatView's commands to test a list of questions.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from ..._bedrock.filters import describe_sources
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Title
from ..._kit.errors import _Hint
from ..._kit.fmt import _as_int, _duration, _plural, human_money
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import BATCH_LIMIT, BATCH_WORKERS, SWEEP_LIMIT, SWEEP_MAX_COST
from ..analysis.choices import describe_files
from ..analysis.findings import answer_findings
from ..analysis.batches import (
    _GRADES, Batch, BatchItem, _avg_grounded, _filled, _key, _settings_text, _unhelpful, batch_changes,
    batch_findings, item_verdict, question_list,
)
from ..analysis.sweeps import setup_label, sweep_setups
from ..analysis.ranking import Sweep, sweep_estimate
from ..blocks import _ResultRow, _Results


class _TestCommands:
    """
    BedrockChatView's commands to test a list of questions: ask_all(), sweep() and results(), with the blocks a
    test run is drawn with.
    """

    # ------------------------------------------------------ test runs and code

    def _prepare(self, questions: Any, retrieve_only: bool, limit: Any = BATCH_LIMIT, label: Any = None) -> Batch:
        """
        A test run of these questions with this setup (knowledge base, model, settings, data source and files), ready
        to send and kept in self.batches. The questions become the Test tab's list.
        """
        cases = question_list(questions)
        kb_id = self._kb_id()
        batch = self.core._prepare_batch(kb_id, cases, self.values, model=self.model, data_source=self._sources(kb_id),
                                         files=self._files_for(kb_id), retrieve_only=retrieve_only, limit=limit,
                                         label=str(label or ""))
        self.questions = cases
        self.batches.append(batch)
        return batch

    def _prepare_sweep(self, questions: Any, grid: dict[str, Any] | None, *, setups: Any = None,
                       retrieve_only: bool, limit: Any = BATCH_LIMIT, max_setups: Any = SWEEP_LIMIT,
                       label: Any = None) -> Sweep:
        """
        A sweep of these questions over every combination in `grid` (and `setups`), each starting from this setup,
        ready to send, and not kept yet (_keep() does that). A ValueError says what's wrong before anything is
        sent.
        """
        cap = None if max_setups is None else _as_int(max_setups, "max_setups", hint=", or None for no limit")
        combos = sweep_setups(grid, setups, limit=cap or None)
        cases = question_list(questions)
        kb_id = self._kb_id()
        sweep = self.core._prepare_sweep(kb_id, cases, combos, self.values, model=self.model,
                                         data_source=self._sources(kb_id), files=self._files_for(kb_id),
                                         retrieve_only=retrieve_only, limit=limit, label=str(label or ""))
        for batch in sweep.batches:  # models by their names, as the reports show them
            batch.label = " · ".join(filter(None, [sweep.label, setup_label(batch, sweep.varied, self._model_label)]))
        return sweep

    def _sweep_cost(self, sweep: Sweep) -> float | None:
        return sweep_estimate(sweep, model_prices=self.core.model_prices, prices=self.core.prices)

    def _too_costly(self, sweep: Sweep, cost: float | None, max_cost: Any) -> str:
        """
        What a sweep estimated over max_cost USD asks, and costs ('' when it isn't over).
        """
        if max_cost is None or cost is None or cost <= float(max_cost):
            return ""
        calls = sum(1 for b in sweep.batches for i in b.items if i.request)
        questions = len(sweep.batches[0].items)
        return (f"That's {len(sweep.batches)} setups × {_plural(questions, 'question')} = {calls:,} calls, about "
                f"{human_money(cost)} (estimate, {self._price_basis(models=not sweep.retrieve_only)})")

    def _item_error(self, item: BatchItem) -> str:
        """
        Why a test question failed, with what to do about it.
        """
        return f"{item.error_code}: {self._explain(item.error_code, item.error)}" if item.error_code else item.error

    def _result_rows(self, batch: Batch) -> list[_ResultRow]:
        """
        Each question of a run with how it did, and how that changed since the run before it.
        """
        old = self._old_items(batch)
        return [self._result_row(n, item, old) for n, item in enumerate(batch.items, 1)]

    def _old_items(self, batch: Batch) -> dict[str, BatchItem]:
        """
        The run before this one's questions that came back, by question.
        """
        before = self._before(batch)
        return {_key(i.question): i for i in before.items if i.answer is not None or i.error_code} if before else {}

    def _result_row(self, number: int, item: BatchItem, old: dict[str, BatchItem]) -> _ResultRow:
        verdict, tone = item_verdict(item)
        a = item.answer
        row = _ResultRow(number, item, verdict, tone, self._meta(a) if a is not None else "",
                         answer_findings(a) if a is not None else [], self._item_error(item))
        was = old.get(_key(item.question))
        if was is not None and (a is not None or item.error_code):
            label, old_tone = item_verdict(was)
            if _GRADES.get(tone, 1) != _GRADES.get(old_tone, 1):
                row.change, row.better = f"was {label}", _GRADES.get(tone, 1) > _GRADES.get(old_tone, 1)
        return row

    def _batch_cost_text(self, batch: Batch) -> str:
        if any(i.cost is None for i in batch.asked):
            return "unknown (pass model_prices=...)"
        text = human_money(batch.cost)
        return text if text.startswith("<") else "~" + text

    def _batch_cards(self, batch: Batch) -> list[tuple[str, ...]]:
        """
        The numbers that say how a run went: answered (or found), grounded, expected sources, failures, cost, time.
        A card is coloured when a finding of the same report is about it.
        """
        items, asked = batch.items, batch.asked
        checked = [i for i in asked if _filled(i.expected)]
        hits = [i for i in checked if i.found is not None]
        cards: list[tuple[str, ...]] = [
            ("Questions", f"{len(items):,}" + (f" of {len(items) + batch.skipped:,}" if batch.skipped else ""))]
        if batch.retrieve_only:
            some = [i for i in asked if i.answer.sources]
            cards.append(("Found passages", f"{len(some)} of {len(asked)}", "warn" if len(some) < len(asked) else ""))
            if checked:
                mrr = sum(1 / i.found for i in hits) / len(checked)
                cards += [("Expected found", f"{len(hits)} of {len(checked)}", "warn" if len(hits) < len(checked)
                           else ""), ("MRR", f"{mrr:.2f}")]
        else:
            spoke = [i for i in asked if not _unhelpful(i.answer) and i.answer.guardrail_action != "INTERVENED"]
            share = _avg_grounded(batch)
            cards += [("Answered", f"{len(spoke)} of {len(asked)}", "warn" if len(spoke) < len(asked) else ""),
                      ("Grounded", f"{share:.0%}" if share is not None else "-")]
            if checked:  # warn when an answer that said something missed it (the others have their own warning)
                missed = any(i in spoke and i.found is None for i in checked)
                cards.append(("Expected cited", f"{len(hits)} of {len(checked)}", "warn" if missed else ""))
        if batch.failed:
            cards.append(("Failed", f"{len(batch.failed):,}", "bad"))
        return cards + [("Est. cost", self._batch_cost_text(batch)), ("Time", _duration(batch.seconds))]

    def _batch_findings(self, batch: Batch) -> list[tuple[str, str]]:
        before = self._before(batch)
        return batch_findings(batch, self._explain) + (batch_changes(before, batch, self._model_label) if before
                                                       else [])

    def _batch_blocks(self, batch: Batch, notes: Iterable[_Note] = ()) -> list[Any]:
        """
        A test run as a report: title, cards, findings across every question and since the run before, then each
        question with how it did, opening to its answer.
        """
        kb = batch.kb_name or batch.kb_id
        narrowed = [f"only {describe_sources(batch.data_sources)}" if batch.data_sources else "",
                    f"only {describe_files(batch.files)}" if batch.files else ""]
        sent = _settings_text(batch.settings) or "no settings (Bedrock's defaults)"
        how = "Retrieve: the search only, no answers" if batch.retrieve_only else self._model_label(batch.model)
        sub = " · ".join(filter(None, [f"test {self._run_name(batch)}", "each question asked on its own, not as a "
                                       "follow-up", how, *narrowed, sent,
                                       f"cost at {self._price_basis(models=not batch.retrieve_only)}"]))
        checked = any(_filled(i.expected) for i in batch.items)
        if batch.retrieve_only:
            empty = "Every search found passages" + (", and the expected source among them." if checked else ".")
            note = ("Each question only searched (Retrieve) with the search settings, so no model was called: the "
                    "cost is each question's embedding" + (" and its reranking." if batch.settings.get("reranker")
                                                           else ".") + " Scores rank one search's passages: compare "
                    "them within a question, not across questions.")
        else:
            empty = "Every question was answered, citing its sources" + (
                ", and the expected one." if checked else ".")
            note = ("Each question was asked on its own (a new Bedrock session each), so none follows up on another. "
                    "Tokens and cost are estimated from characters: RetrieveAndGenerate doesn't report tokens.")
        return [
            _Title(f"Test run on {kb}: {_plural(len(batch.items), 'question')}", sub),
            _Cards(self._batch_cards(batch)),
            _Findings(self._batch_findings(batch), empty=empty),
            *notes,
            _Results(self._result_rows(batch)),
            _Note(note),
            _Next(self._batch_steps(batch)),
        ]

    def _batch_steps(self, batch: Batch) -> list[tuple[str, str]]:
        """
        What to run after a test run: the search behind the first question that didn't work, several settings at
        once (or for a sweep's run, switching to it), and the same questions again.
        """
        steps: list[tuple[str, str]] = []
        weak = next((i for i in batch.asked if item_verdict(i)[1] == "warn"), None)
        if weak is not None and not batch.retrieve_only:
            steps.append((_call("retrieve", weak.question), "the search behind the first answer that didn't work"))
        elif weak is not None:
            steps.append(("files()", "whether the files those questions need are indexed"))
        if batch.sweep:
            steps.append((_call("use_run", self._run_number(batch)), "switch to this setup"))
            steps.append(("runs()", "every test run, ranked against the others of the same questions"))
            return steps
        steps.append((self._next_sweep(batch.retrieve_only), "try several settings at once: every combination, "
                                                             "best first"))
        steps.append(("ask_all()", "the same questions again: the report says what changed"))
        if weak is None:
            steps.append(("code()", "this setup as Python, JSON or an AWS CLI command"))
        return steps

    def _next_sweep(self, retrieve_only: bool = False) -> str:
        """
        A sweep() call worth trying: the passages now and twice as many, with both search types.
        """
        try:
            n = int(self.values.get("n") or 5)
        except (TypeError, ValueError):
            n = 5
        values = [n, min(100, n * 2)] if n < 100 else [50, 100]
        kwargs: dict[str, Any] = {"n": values, "search_type": ["SEMANTIC", "HYBRID"]}
        if retrieve_only:
            kwargs["retrieve_only"] = True
        return _call("sweep", **kwargs)

    # ------------------------------------------------------- test questions

    @_friendly_errors
    def ask_all(self, questions: Any = None, *, retrieve_only: bool | None = None, workers: int = BATCH_WORKERS,
                limit: int | None = BATCH_LIMIT, label: str | None = None) -> None:
        """
        Asks a list of test questions with these settings and shows how each did: answered or "unable to assist",
        grounded share, sources cited, time and estimated cost, with findings across them all and what changed since
        the last run. Each question is asked on its own, not as a follow-up.

        questions: a list, or text with one question per line. 'question | file' also checks that the answer cites
        that file (a piece of its name, path or text). ask_all() with no questions asks the last list again: change a
        setting, run it, and the report says which questions did better or worse. retrieve_only=True only searches
        (default: the window's Answer / Retrieve only). workers questions are asked at a time, and up to limit of them
        (None for all). label names the run in runs(). ui.batches[-1].to_df() has the answers as a table.
        """
        if questions is None:
            if not self.questions:
                raise _Hint("Pass the questions to ask: ask_all(['How long do refunds take?', 'Can I return a digital "
                            "product?']), or text with one per line. Add | and a file name to check the answer cites "
                            "it: 'How long do refunds take? | refund-policy.pdf'.")
            questions = self.questions
        retrieve = self._retrieving(retrieve_only)
        batch = self._prepare(questions, retrieve, limit, label)
        with self._progress("Searching" if retrieve else "Asking", unit="questions") as tick:
            self.core._run_batch(batch, workers=workers, progress=tick)
        notes = self._log_runs([batch])
        if self._app is not None:
            self._app.ran(batch)
        self._show(self._batch_blocks(batch, notes))

    @_friendly_errors
    def sweep(self, questions: Any = None, *, setups: Any = None, retrieve_only: bool | None = None,
              workers: int = BATCH_WORKERS, limit: int | None = BATCH_LIMIT, max_setups: int | None = SWEEP_LIMIT,
              max_cost: float | None = SWEEP_MAX_COST, label: str | None = None, **grid: Any) -> None:
        """
        Asks the test questions with every combination of the settings, models, data sources or files you list, and
        ranks the setups best first (expected sources cited, answers, grounded share, failures, time and estimated
        cost), with which setup to use, what each setting changed, and how each question did with each setup.

        sweep(n=[5, 10], search_type=['SEMANTIC', 'HYBRID']) asks 4 setups, each starting from the settings in use.
        model=['haiku', 'sonnet'], data_source=['faq', 'manuals'] and files=[...] vary those too; None leaves a
        setting out (reranker=[None, 'cohere']). setups=[{'n': 5}, {'n': 10, 'reranker': 'cohere'}] lists whole setups
        instead (combined with any lists given). questions: as ask_all() takes them (default: the last list).
        retrieve_only=True only searches, for the embedding's cost alone: a cheap way to pick the search settings
        before trying models. Nothing is sent when it's more than max_setups setups or estimated over max_cost USD
        (raise them, or None for no limit). Each setup becomes a test run: use_run() switches to the best,
        results(n) shows one's answers, and runs() lists them all.
        """
        if questions is None:
            if not self.questions:
                raise _Hint("Pass the questions to ask: sweep(['How long do refunds take? | refund-policy.pdf', 'Can I "
                            "return a gift?'], n=[5, 10]), or ask_all() a list first.")
            questions = self.questions
        if not grid and setups is None:
            raise _Hint("Pass what to try, a list of values each: sweep(n=[5, 10], search_type=['SEMANTIC', "
                        "'HYBRID']), sweep(model=['haiku', 'sonnet']), or sweep(setups=[{'n': 5}, {'n': 10, "
                        "'reranker': 'cohere'}]). fields() lists every setting.")
        retrieve = self._retrieving(retrieve_only)
        sweep = self._prepare_sweep(questions, grid, setups=setups, retrieve_only=retrieve, limit=limit,
                                    max_setups=max_setups, label=label)
        cost = self._sweep_cost(sweep)
        refused = self._too_costly(sweep, cost, max_cost)
        if refused:
            raise _Hint(f"{refused}, more than max_cost={float(max_cost):g}, so nothing was sent. Pass "
                        f"max_cost={math.ceil(cost or 0)} to ask them anyway, or try fewer values or questions "
                        "(limit=10).")
        self._keep(sweep, questions)
        with self._progress(f"Asking {len(sweep.batches)} setups", unit="questions") as tick:
            self.core._run_sweep(sweep, workers=workers, progress=tick)
        notes = self._log_runs(sweep.batches)
        if cost is None and not retrieve:
            notes.append(_Note("A model's price isn't known, so the cost wasn't estimated before asking: pass "
                               "model_prices= to the analyzer to add it.", "warn"))
        if self._app is not None:
            self._app.swept(sweep)
        self._show(self._compare_blocks(sweep.batches, sweep=sweep, notes=notes))

    def _keep(self, sweep: Sweep, questions: Any) -> None:
        """
        A sweep about to be sent: its runs join self.batches, and its questions become the Test tab's list.
        """
        self.questions = question_list(questions)
        self.batches += sweep.batches
        self.sweeps.append(sweep)

    @_friendly_errors
    def results(self, run: int = -1) -> None:
        """
        A test run again, as a report that stays in the notebook when it's saved (the window doesn't): the last
        run, or results(1) for the first, as runs() numbers them. Nothing is asked again.
        """
        self._show(self._batch_blocks(self._run(run)))
