"""
BedrockChatView's commands to keep and compare test runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import _model_id
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone
from ..._kit.errors import _Hint
from ..._kit.fmt import _duration, _plural, human_age, human_money
from ..._kit.render import _TONES, _call
from ..._kit.view import _friendly_errors

from ..helpers import RUNS_FILE, _diff
from ..models import RunScore
from ..analysis.choices import describe_files
from ..analysis.request import retrieve_settings
from ..analysis.batches import Batch, _filled, _key, _out_of, _settings_text, item_verdict
from ..analysis.sweeps import _OFF_WHEN_UNSET, _PICKED, _setup_key, _setup_value, run_setup, varied_setups
from ..analysis.ranking import (
    Sweep, _came_back, _cost_gap, _quality, rank_runs, ranking_findings, run_score, shared_questions,
)
from ..analysis.records import read_runs, run_record


class _RunsCommands:
    """
    BedrockChatView's commands to keep and compare test runs: runs(), compare_runs(), use_run(), save_runs() and
    load_runs(), with how runs are numbered, named, saved and ranked.
    """

    def _run_number(self, batch: Batch) -> int:
        """
        A run's number, as the reports show it and results(), use_run() and compare_runs() take it: from 1.
        """
        return next((k for k, b in enumerate(self.batches, 1) if b is batch), len(self.batches))

    def _run(self, run: Any) -> Batch:
        """
        The test run `run` names: its number (1 the first, -1 the last) or the Batch; a _Hint says what there is.
        """
        if isinstance(run, Batch):
            return run
        if not self.batches:
            raise _Hint("No test runs yet: ask_all(['a question', 'another']), sweep(n=[5, 10]), or the window's "
                        "Test tab. load_runs() reads runs saved before.")
        try:
            number = int(str(run).strip()) if not isinstance(run, bool) else None
        except ValueError:
            number = None
        count = len(self.batches)
        if number is None or not (1 <= number <= count or -count <= number <= -1):
            raise _Hint(f"There {'is' if count == 1 else 'are'} {_plural(count, 'test run')}, numbered from 1: "
                        f"{'1' if count == 1 else f'1 to {count}'} (-1 is the last). runs() lists them.")
        return self.batches[number - 1 if number > 0 else number]

    def _sweep_number(self, batch: Batch) -> int:
        """
        Which sweep a run was part of, counted from 1 in the order they ran (0: none).
        """
        ids = list(dict.fromkeys(b.sweep for b in self.batches if b.sweep))
        return ids.index(batch.sweep) + 1 if batch.sweep in ids else 0

    def _run_name(self, batch: Batch) -> str:
        """
        'run 7', 'run 7 (baseline)', 'run 7 (sweep 2: n=10 · search_type=HYBRID)'.
        """
        name = f"run {self._run_number(batch)}"
        if batch.sweep:
            return f"{name} (sweep {self._sweep_number(batch)}: {batch.label})" if batch.label else name
        return f"{name} ({batch.label})" if batch.label else name

    def _family(self, batch: Batch) -> list[Batch]:
        """
        The runs asked the same way (answers, or searches) with the same questions as this one, in order.
        """
        keys = {_key(i.question) for i in batch.items}
        return [b for b in self.batches if b.retrieve_only == batch.retrieve_only
                and {_key(i.question) for i in b.items} == keys]

    def _before(self, batch: Batch) -> Batch | None:
        """
        The run to compare this one with: the last one before it that asked some of the same questions the same way
        (answers, or searches), apart from the rest of its own sweep. When that was a sweep, its setup that's the same
        as this run's, else its best.
        """
        keys = {_key(i.question) for i in batch.items}
        at = next((k for k, b in enumerate(self.batches) if b is batch), len(self.batches))
        for earlier in reversed(self.batches[:at]):
            if batch.sweep and earlier.sweep == batch.sweep:
                continue
            if earlier.retrieve_only == batch.retrieve_only and keys & {_key(i.question) for i in earlier.items}:
                if not earlier.sweep:
                    return earlier
                group = [b for b in self.batches[:at] if b.sweep == earlier.sweep]
                mine = _setup_key(run_setup(batch))
                same = next((b for b in group if _setup_key(run_setup(b)) == mine), None)
                return same or rank_runs(group)[0][0]
        return None

    def _setup_now(self, retrieve_only: bool) -> str | None:
        """
        The setup questions are asked with now, as _setup_key(run_setup()) gives a run's, to tell which run that is;
        None when it can't be told (no knowledge base picked, or a model that can't be resolved).
        """
        try:
            kb_id = self._kb_id()
            model = "" if retrieve_only else _model_id(self.core.resolve_model(self.model)[1])
            settings = retrieve_settings(self.values, self.core.schema()) if retrieve_only else self.values
            now = Batch(kb_id=kb_id, kb_name=self.core.kb_name(kb_id), model=model, data_sources=self._sources(kb_id),
                        files=self._files_for(kb_id), settings=settings, retrieve_only=retrieve_only)
        except (ValueError, ClientError, BotoCoreError):
            return None
        return _setup_key(run_setup(now))

    def _now_in(self, batches: list[Batch]) -> Batch | None:
        """
        The run among these whose setup is the one in use now (the newest, if several).
        """
        if not batches:
            return None
        now = self._setup_now(batches[0].retrieve_only)
        return next((b for b in reversed(batches) if _setup_key(run_setup(b)) == now), None) if now else None

    def _saved_ids(self, path: Path) -> set[str]:
        """
        The IDs of the runs already in a file save_runs() writes, read once per file.
        """
        key = str(path.resolve())
        if key not in self._in_file:
            ids: set[str] = set()
            if path.exists():
                with path.open(encoding="utf-8") as lines:
                    for line in lines:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(record, dict) and record.get("id"):
                            ids.add(str(record["id"]))
            self._in_file[key] = ids
        return self._in_file[key]

    def _append_runs(self, path: Path, batches: list[Batch]) -> int:
        """
        Adds the runs that aren't in the file yet to its end, one JSON line each -> how many. Nothing in it is
        changed. An OSError says why it couldn't be written.
        """
        saved = self._saved_ids(path)
        new = [b for b in batches if b.id and b.id not in saved]
        if new:
            with path.open("a", encoding="utf-8") as out:
                for b in new:
                    out.write(json.dumps(run_record(b), ensure_ascii=False) + "\n")
            saved.update(b.id for b in new)
        return len(new)

    def _unsaved(self, batches: Iterable[Batch]) -> list[Batch]:
        """
        The runs that aren't in a file yet (saved, logged as they finished, or loaded from one).
        """
        kept = set().union(*self._in_file.values()) if self._in_file else set()
        return [b for b in batches if b.id not in kept]

    def _memory_findings(self, batches: list[Batch]) -> list[tuple[str, str]]:
        """
        A note when some of these runs would be lost with the kernel: what keeps them.
        """
        unsaved = self._unsaved(batches)
        if not unsaved:
            return []
        one = len(unsaved) == 1
        who = ("These runs are" if len(unsaved) == len(batches) and not one else "This run is" if len(batches) == 1
               else f"{_out_of(len(unsaved), len(batches), 'run')} {'is' if one else 'are'}")
        return [("info", f"{who} kept in this notebook's memory only, so a kernel restart loses "
                         f"{'it' if one else 'them'}: {_call('save_runs')} keeps every run in "
                         f"{self.log or RUNS_FILE} (and every later one), and load_runs() reads them back.")]

    def _saved_text(self) -> str:
        """
        Where the runs are kept: the log file, else the file they were loaded from, and how many aren't.
        """
        files = [Path(key).name for key, ids in self._in_file.items() if any(b.id in ids for b in self.batches)]
        where = self.log or ", ".join(files)
        unsaved = len(self._unsaved(self.batches))
        if not where:
            return "not saved"
        return f"{where} ({unsaved:,} not yet)" if unsaved else where

    def _log_runs(self, batches: list[Batch]) -> list[_Note]:
        """
        Adds finished runs to the file log= names (save_runs() sets it): [] when it worked or there's no log, else
        a note saying why it didn't.
        """
        if not self.log:
            return []
        try:
            self._append_runs(Path(self.log), [b for b in batches if b.asked or b.failed])
        except OSError as exc:
            return [_Note(f"This run wasn't saved to {self.log}: {exc.strerror or exc}. save_runs('another/path.jsonl') "
                          "saves every run somewhere else.", "warn")]
        return []

    # ------------------------------------------------- test runs: compared, listed

    def _money(self, cost: float | None) -> str:
        if cost is None:
            return "unknown"
        text = human_money(cost)
        return text if text.startswith("<") else "~" + text

    def _ranked_by(self, batches: list[Batch]) -> str:
        """
        How the runs are ranked, in words, for a report's subtitle.
        """
        checked = any(_filled(i.expected) for b in batches for i in b.items)
        if batches[0].retrieve_only:
            first = "expected sources found, then MRR, then searches that found passages" if checked else \
                "searches that found passages"
        else:
            first = ("expected sources cited, then answers, then grounded share (in 5-point steps)" if checked
                     else "answers that say something, then grounded share (in 5-point steps)")
        return f"ranked by {first}; the cheaper first when as good"

    def _compare_blocks(self, batches: list[Batch], *, sweep: Sweep | None = None, notes: Iterable[_Note] = (),
                        memory: bool = True) -> list[Any]:
        """
        Test runs of the same questions side by side (a sweep's setups, or compare_runs()'s runs): cards, what to
        do, the runs best first, then how each question did in each. memory=False leaves out the note on runs that
        aren't saved (the window's Runs tab says it).
        """
        ranked = rank_runs(batches)
        retrieve = batches[0].retrieve_only
        varied = varied_setups(batches)
        now = self._now_in(batches)
        earlier = None if now is not None else self._now_in(  # the setup in use, as an earlier run of these questions
            [b for b in self._family(batches[0]) if not any(b is x for x in batches)
             and self._run_number(b) < self._run_number(batches[0])])
        common = shared_questions(batches)
        best, top = ranked[0]
        kb = batches[0].kb_name or batches[0].kb_id
        numbers = sorted(self._run_number(b) for b in batches)
        if numbers == list(range(numbers[0], numbers[-1] + 1)):
            which = f"runs {numbers[0]}–{numbers[-1]}"
        else:
            which = "runs " + ", ".join(map(str, numbers))
        how = "Retrieve: the search only, no answers" if retrieve else (
            "" if "model" in varied else self._model_label(batches[0].model))
        fixed = {k: v for k, v in batches[0].settings.items() if k not in varied}
        legend = ("default: not sent, so Bedrock's own applies"
                  if any(v is None for k, vs in varied.items() if k not in (*_PICKED, *_OFF_WHEN_UNSET) for v in vs)
                  else "")
        sub = " · ".join(filter(None, [
            which, "each question asked on its own with each setup", how,
            f"the same in each: {_settings_text(fixed)}" if fixed else "", legend, self._ranked_by(batches),
            f"cost at {self._price_basis(models=not retrieve)}"]))
        calls = sum(_came_back(i) for b in batches for i in b.items)
        if sweep is not None:
            title = (f"Sweep on {kb}: {len(batches)} setups × {_plural(len(batches[0].items), 'question')}"
                     + (f" ({sweep.label})" if sweep.label else ""))
            cards: list[tuple[str, ...]] = [("Setups", f"{len(batches):,}"),
                                            ("Questions", f"{len(batches[0].items):,}"), ("Calls", f"{calls:,}")]
        else:
            title = f"{len(batches)} test runs on {kb} compared: {_plural(len(common), 'question')}"
            cards = [("Runs", f"{len(batches):,}"), ("Questions", f"{len(common):,}")]
        cards.append(("Best", f"run {self._run_number(best)}"))
        if top.checked:
            cards.append((f"Expected {'found' if retrieve else 'cited'} (best)", f"{top.hits} of {top.checked}"))
        else:
            cards.append((f"{'Found passages' if retrieve else 'Answered'} (best)", f"{top.answered} of "
                                                                                    f"{top.questions}"))
        failed = sum(s.failed for _, s in ranked)
        if failed:
            cards.append(("Failed", f"{failed:,}", "bad"))
        costs = [s.cost for _, s in ranked]
        cards.append(("Est. cost", self._money(None if any(c is None for c in costs) else sum(costs))))
        if sweep is not None:
            cards.append(("Time", _duration(sweep.seconds)))
        findings = ranking_findings(batches, now=now or earlier, number=self._run_number, label=self._model_label,
                                    explain=self._explain) + (self._memory_findings(batches) if memory else [])
        blocks: list[Any] = [_Title(title, sub), _Cards(cards), _Findings(findings), *notes,
                             self._ranking_table(ranked, varied, now, sweep is not None), self._matrix_table(ranked)]
        if retrieve:
            blocks.append(_Note("Each question only searched (Retrieve) with each setup, so no model was called. A "
                                "search finds the same passages every time it's asked the same way, so the "
                                "differences come from the setups."))
        else:
            blocks.append(_Note("Each question was asked on its own with each setup (a new Bedrock session each). A "
                                "model doesn't answer the same way every time, so a difference of one question can be "
                                "chance. Tokens and cost are estimated from characters."))
        steps: list[tuple[str, str]] = []
        better = any(_quality(s, retrieve) != _quality(top, retrieve) for _, s in ranked)
        cheaper = _cost_gap(top, ranked[-1][1]) not in ("", "about the same cost")
        if best is not now and (better or cheaper):
            steps.append((_call("use_run", self._run_number(best)), "switch to the best setup"))
        steps.append((_call("results", self._run_number(best)), "the best setup's answers, question by question"))
        steps.append(("runs()", "every test run so far"))
        blocks.append(_Next(steps))
        return blocks

    def _ranking_table(self, ranked: list[tuple[Batch, RunScore]], varied: dict[str, list[Any]],
                       now: Batch | None, sweep: bool = False) -> _Table:
        """
        The runs best first: rank, run number, what differs between them, and how each did.
        """
        retrieve = ranked[0][0].retrieve_only
        checked = any(s.checked for _, s in ranked)
        headers = ["Rank", "Run", *varied]
        if retrieve:
            headers += ["Found passages"] + (["Expected found", "MRR"] if checked else [])
        else:
            headers += ["Answered", "Grounded"] + (["Expected cited"] if checked else [])
        headers += ["Failed", "Avg time", "Est. cost"]
        rows = []
        for rank, (b, s) in enumerate(ranked, 1):
            setup = run_setup(b)
            mark = f"{rank} (now)" if b is now else str(rank)
            row: list[Any] = [_Tone(mark, "ok") if rank == 1 else mark, str(self._run_number(b))]
            row += [_setup_value(key, setup.get(key), values, self._model_label) for key, values in varied.items()]
            row.append(f"{s.answered} of {s.questions}")
            if retrieve:
                row += [f"{s.hits} of {s.checked}", f"{s.mrr or 0:.2f}"] if checked else []
            else:
                row.append(f"{s.grounded:.0%}" if s.grounded is not None else "-")
                row += [f"{s.hits} of {s.checked}"] if checked else []
            row += [_Tone(f"{s.failed:,}", "bad") if s.failed else "0",
                    f"{s.seconds:.1f}s" if s.seconds is not None else "-", self._money(s.cost)]
            rows.append(row)
        return _Table(headers, rows, title="Setups, best first" if sweep else "Runs, best first", max_rows=0)

    def _matrix_table(self, ranked: list[tuple[Batch, RunScore]]) -> _Table:
        """
        How each question did with each setup: a column per run, in the ranking's order; the questions whose result
        changes with the setup first.
        """
        items = [{_key(i.question): i for i in b.items} for b, _ in ranked]
        changes, same = [], []
        for question in dict.fromkeys(i.question for b, _ in ranked for i in b.items):
            cells: list[Any] = []
            seen = set()
            for found in items:
                item = found.get(_key(question))
                verdict, tone = item_verdict(item) if item is not None else ("not asked", "")
                cells.append(_Tone(verdict, tone) if tone in _TONES else verdict)
                seen.add(verdict)
            (changes if len(seen) > 1 else same).append([question, *cells])
        title = "How each question did with each setup (#1 is the best, as above)"
        if changes and same:
            title += f": the {_plural(len(changes), 'question')} the setup changes first"
        return _Table(["Question", *[f"#{k}" for k in range(1, len(ranked) + 1)]], changes + same, title=title,
                      max_rows=0)

    def _families(self) -> list[list[Batch]]:
        """
        The runs grouped by what they asked: the same questions, asked the same way (answers, or searches).
        """
        families: dict[str, list[Batch]] = {}
        for b in self.batches:
            key = _setup_key([b.retrieve_only, sorted({_key(i.question) for i in b.items})])
            families.setdefault(key, []).append(b)
        return list(families.values())

    def _places(self) -> dict[int, tuple[int, int]]:
        """
        id(run) -> (its rank, how many runs asked its questions), for the runs that have others to rank against.
        """
        place: dict[int, tuple[int, int]] = {}
        for family in self._families():
            if len(family) > 1:
                for rank, (b, _) in enumerate(rank_runs(family), 1):
                    place[id(b)] = (rank, len(family))
        return place

    def _result_text(self, batch: Batch) -> tuple[str, str]:
        """
        How a run did, short: ('5 of 6 answered · 81% grounded', '4 of 6 cited'), the second '-' when nothing was
        expected.
        """
        s = run_score(batch)
        if batch.retrieve_only:
            result = f"{s.answered} of {s.questions} found passages" + (f" · MRR {s.mrr:.2f}" if s.mrr is not None
                                                                        else "")
        else:
            result = f"{s.answered} of {s.questions} answered" + (f" · {s.grounded:.0%} grounded"
                                                                  if s.grounded is not None else "")
        expected = f"{s.hits} of {s.checked} {'found' if batch.retrieve_only else 'cited'}" if s.checked else "-"
        return result, expected

    def _runs_blocks(self, memory: bool = True) -> list[Any]:
        """
        Every test run, newest first, each ranked against the others of the same questions, under which setup of
        the last list to use (compare_runs() says the rest). memory=False leaves out the note on runs that aren't
        saved.
        """
        families = self._families()
        place = self._places()
        latest = self.batches[-1]
        family = self._family(latest)
        best = rank_runs(family)[0][0] if len(family) > 1 else None
        rows = []
        for b in reversed(self.batches):
            s = run_score(b)
            result, expected = self._result_text(b)
            rank, of = place.get(id(b), (0, 0))
            ranked: Any = "-" if not of else _Tone(f"1 of {of}", "ok") if rank == 1 else f"{rank} of {of}"
            name = (f"sweep {self._sweep_number(b)}: {b.label}" if b.sweep else b.label) or "-"
            rows.append([str(self._run_number(b)), human_age(b.started), name, self._setup_text(b),
                         f"{len(b.items):,}", result, expected, _Tone(f"{s.failed:,}", "bad") if s.failed else "0",
                         self._money(s.cost), ranked])
        sweeps = len({b.sweep for b in self.batches if b.sweep})
        cards: list[tuple[str, ...]] = [("Runs", f"{len(self.batches):,}")]
        cards += [("Sweeps", f"{sweeps:,}")] if sweeps else []
        cards += [("Question lists", f"{len(families):,}")]
        if best is not None:
            cards.append(("Best of the last list", f"run {self._run_number(best)}"))
        costs = [run_score(b).cost for b in self.batches]
        cards.append(("Est. cost", self._money(None if any(c is None for c in costs) else sum(costs))))
        cards.append(("Saved to", self._saved_text()))
        findings: list[tuple[str, str]] = []
        if len(family) > 1:
            findings += ranking_findings(family, now=self._now_in(family), number=self._run_number,
                                         label=self._model_label, explain=self._explain, brief=True)
        findings += self._memory_findings(self.batches) if memory else []
        subtitle = (f"newest first · rank: against the other runs of the same questions, "
                    f"{self._ranked_by(family).replace('ranked by ', 'by ')}")
        blocks: list[Any] = [
            _Title(f"Test runs: {len(self.batches):,} so far", subtitle),
            _Cards(cards),
            _Findings(findings),
            _Table(["Run", "When", "Name", "Setup", "Questions", "Result", "Expected", "Failed", "Est. cost", "Rank"],
                   rows, title="Every test run, newest first"),
        ]
        steps: list[tuple[str, str]] = [(_call("results", self._run_number(latest)), "the last run's answers")]
        if len(family) > 1:
            steps.insert(0, ("compare_runs()", "the runs of the last list side by side, question by question"))
            if best is not None and best is not self._now_in(family):
                steps.append((_call("use_run", self._run_number(best)), "switch to the best setup"))
        if self._unsaved(self.batches):
            steps.append(("save_runs()", "keep them in a file, to read back after a restart"))
        blocks.append(_Next(steps))
        return blocks

    def _setup_text(self, batch: Batch) -> str:
        """
        A run's whole setup on one line: 'Claude Haiku 4.5 · only faq · n=10, search_type=HYBRID'.
        """
        parts = ["Retrieve only" if batch.retrieve_only else self._model_label(batch.model)]
        if batch.data_sources:
            parts.append(f"only {describe_sources(batch.data_sources)}")
        if batch.files:
            parts.append(f"only {describe_files(batch.files)}")
        parts.append(_settings_text(batch.settings) or "Bedrock's defaults")
        return " · ".join(parts)

    @_friendly_errors
    def runs(self) -> None:
        """
        Every test run so far, newest first: when it ran, its setup, and how it did (answered or found, grounded,
        expected sources, failures, estimated cost), ranked against the other runs of the same questions, with the
        best setup to switch to. load_runs() brings back runs saved before a restart.
        """
        if not self.batches:
            raise _Hint("No test runs yet: ask_all(['a question', 'another']), sweep(n=[5, 10]), or the window's "
                        "Test tab. load_runs() reads runs saved to a file before.")
        self._show(self._runs_blocks())

    @_friendly_errors
    def compare_runs(self, *runs: Any) -> None:
        """
        Test runs of the same questions side by side, best first: how each did, how each question did in each, which
        setup to use and what the differences between them changed. compare_runs(2, 5) compares runs 2 and 5 (as
        runs() numbers them); compare_runs() every run of the last run's questions.
        """
        if not self.batches:
            raise _Hint("No test runs yet: ask_all(['a question', 'another']), then change a setting and ask_all() "
                        "again, or sweep(n=[5, 10]).")
        if runs:
            chosen: list[Batch] = []
            for run in runs:
                batch = self._run(run)
                if not any(b is batch for b in chosen):
                    chosen.append(batch)
        else:
            chosen = self._family(self.batches[-1])
        if len(chosen) < 2:
            only = self._run_number(chosen[0])
            raise _Hint(f"Only run {only} asked {'these' if not runs else 'its'} questions this way, so there's nothing "
                        "to compare it with: change a setting and ask_all() again, or sweep(n=[5, 10]), then "
                        "compare_runs().")
        kinds = {b.retrieve_only for b in chosen}
        if len(kinds) > 1:
            searched = next(b for b in chosen if b.retrieve_only)
            answered = next(b for b in chosen if not b.retrieve_only)
            raise _Hint(f"Run {self._run_number(searched)} only searched and run {self._run_number(answered)} "
                        "answered: compare runs of one kind.")
        if not shared_questions(chosen):
            raise _Hint("Those runs have no question in common that came back: compare runs of the same questions.")
        self._show(self._compare_blocks(chosen))

    @_friendly_errors
    def use_run(self, run: Any = None) -> None:
        """
        Switches to a test run's setup, so the chat, ask_all() and the window use it from now on: its settings,
        model, data source and files (and knowledge base). use_run() picks the best run of the last run's questions,
        use_run(7) run 7, as runs() numbers them. A search-only run sets the search settings and keeps the rest.
        """
        if run is None:
            if not self.batches:
                raise _Hint("No test runs yet: sweep(n=[5, 10]) or ask_all([...]) first, then use_run() switches to the "
                            "best one.")
            batch = rank_runs(self._family(self.batches[-1]))[0][0]
        else:
            batch = self._run(run)
        self._show([_Note(self._switch_to(batch), "ok"),
                    _Next([("ask_all()", "the test questions with it: the report says what changed"),
                           ("settings()", "every setting, in plain English"),
                           ("code()", "this setup as Python, JSON or an AWS CLI command")])])

    def _switch_to(self, batch: Batch) -> str:
        """
        Makes a run's setup the one in use (use_run() and the window's Use this setup) -> what changed, in words.
        """
        schema = self.core.schema()
        if batch.retrieve_only:  # its search settings; the answer's stay as they are
            searched = retrieve_settings(self.values, schema)
            values = {**{k: v for k, v in self.values.items() if k not in searched}, **batch.settings}
        else:
            values = dict(batch.settings)
        values = {key: values[key] for key in schema.fields if key in values}
        note = ""
        if batch.kb_id and batch.kb_id != self.kb:
            self._use_kb(batch.kb_id)
            note = f"Now asking {batch.kb_name or batch.kb_id}: a new conversation."
        sources, uris = self._sources_now(), self._files_now()
        changes = [f"knowledge base {batch.kb_name or batch.kb_id}"] if note else []
        changes += _diff(self.values, values)
        self.values = values
        if not batch.retrieve_only and batch.model:
            try:
                before = _model_id(self.core.resolve_model(self.model)[1])
            except (ValueError, ClientError, BotoCoreError):
                before = str(self.model or "")
            if before != batch.model:
                changes.append(f"model {self._model_label(before)} → {self._model_label(batch.model)}")
            self.model = batch.model
        if sources != batch.data_sources:
            changes.append(f"{describe_sources(sources) if sources else 'every data source'} → "
                           f"{describe_sources(batch.data_sources) if batch.data_sources else 'every data source'}")
        if uris != batch.files:
            changes.append(f"{describe_files(uris) if uris else 'every file'} → "
                           f"{describe_files(batch.files) if batch.files else 'every file'}")
        self.data_source, self.picked_files = dict(batch.data_sources), list(batch.files)
        self._changed(note)
        name = self._run_name(batch)
        return (f"Now using the setup of {name}: " + "; ".join(changes) + "." if changes
                else f"The setup of {name} is the one in use already.")

    @_friendly_errors
    def save_runs(self, path: str | None = None) -> None:
        """
        Saves every test run to a file, so they outlast a kernel restart and can be shared: a line per run (JSON
        Lines) with its setup, and each question's answer, sources, cost and request. Runs already in the file aren't
        written again, and nothing in it is changed. Every later run is added as it finishes (ui.log holds the file;
        None stops that). load_runs() reads them back, and pandas.read_json(path, lines=True) reads the file too.
        path: default kb-test-runs.jsonl, beside the notebook.
        """
        note = self._save(path)
        steps = [(_call("load_runs", self.log), "read them back after a restart")] if note.level == "ok" else []
        self._show([note, _Next(steps + [("runs()", "every test run, ranked")])])

    def _save(self, path: Any = None) -> _Note:
        """
        save_runs() and the window's Save: the runs added to the file, and from then on every run -> what happened.
        """
        target = Path(str(path or self.log or RUNS_FILE).strip())
        if target.is_dir():
            raise _Hint(f"{target} is a folder: pass a file in it, like save_runs('{target / RUNS_FILE}').")
        try:
            written = self._append_runs(target, self.batches)
        except OSError as exc:
            return _Note(f"Couldn't save to {target}: {exc.strerror or exc}. Pass another path: "
                         "save_runs('runs/kb-test-runs.jsonl').", "warn")
        self.log = str(target)
        total = len(self._saved_ids(target))
        where = f"{target} (in {target.resolve().parent})"
        later = "Every run from now on is added as it finishes (ui.log = None stops that)."
        if not self.batches:
            text = f"No test runs yet, so nothing was written: every run from now on is added to {where} as it finishes."
        elif written:
            text = f"Saved {_plural(written, 'test run')} to {where}: {total:,} in the file now. {later}"
        else:
            text = f"Every run here is in {where} already ({total:,} in the file). {later}"
        if self._app is not None:
            self._app.runs_changed()
        return _Note(text, "ok")

    @_friendly_errors
    def load_runs(self, path: str | None = None) -> None:
        """
        Reads test runs saved with save_runs() (or log=) back in, after a kernel restart or from someone else's
        file, and lists them as runs() does: results(), compare_runs() and use_run() work on them like on new runs, and
        the window's Test tab shows the last one. Runs already here are skipped. path: default kb-test-runs.jsonl.
        """
        notes = self._load(path)
        self._show(notes + (self._runs_blocks() if self.batches else []))

    def _load(self, path: Any = None) -> list[_Note]:
        """
        load_runs() and the window's Load: the runs in the file that aren't here yet, added in the order they ran
        -> what happened.
        """
        target = Path(str(path or self.log or RUNS_FILE).strip())
        if not target.is_file():
            raise _Hint(f"There's no file {target} in {target.resolve().parent}: save_runs() writes one, or pass the "
                        f"path of yours: load_runs('folder/{RUNS_FILE}').")
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return [_Note(f"Couldn't read {target}: {getattr(exc, 'strerror', None) or exc}.", "warn")]
        runs, problems = read_runs(text.splitlines())
        known = {b.id for b in self.batches}
        new = []
        for b in runs:
            if b.id not in known:
                known.add(b.id)
                new.append(b)
        self._saved_ids(target).update(b.id for b in runs)
        self.batches = sorted(self.batches + new, key=lambda b: b.started.timestamp() if b.started else 0.0)
        for sweep_id in dict.fromkeys(b.sweep for b in new if b.sweep):
            if not any(sw.id == sweep_id for sw in self.sweeps):
                members = [b for b in self.batches if b.sweep == sweep_id]
                self.sweeps.append(Sweep(batches=members, varied=varied_setups(members),
                                         retrieve_only=members[0].retrieve_only, id=sweep_id,
                                         seconds=max(b.seconds for b in members),
                                         stopped=any(b.stopped for b in members)))
        self.sweeps.sort(key=lambda sw: self._run_number(sw.batches[0]))
        if new and not self.questions:
            last = max(new, key=lambda b: b.started.timestamp() if b.started else 0.0)
            self.questions = [(i.question, i.expected) for i in last.items]
        skipped = len(runs) - len(new)
        if new:
            text = (f"Loaded {_plural(len(new), 'test run')} from {target}"
                    + (f" ({skipped:,} {'was' if skipped == 1 else 'were'} here already)" if skipped else "") + ".")
        elif runs:
            text = f"Every run in {target} is here already ({len(runs):,})."
        else:
            text = f"There are no test runs in {target}."
        notes = [_Note(text, "ok" if new else "info")]
        if problems:
            more = "; …" if len(problems) > 3 else ""
            notes.append(_Note(f"{_plural(len(problems), 'line')} couldn't be read: {'; '.join(problems[:3])}{more}. "
                               "The rest were loaded.", "warn"))
        if self._app is not None:
            self._app.loaded()
        return notes
