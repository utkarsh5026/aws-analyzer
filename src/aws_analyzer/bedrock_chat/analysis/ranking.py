"""
Runs ranked by expected sources cited, answers and grounding, a Sweep's setups among them,
and findings about which setup does best and why.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..._kit.deps import _require
from ..._kit.fmt import _plural, human_money
from ..._kit.render import _call

from ..models import RunScore
from .batches import Batch, BatchItem, _examples, _filled, _key, _out_of, _unhelpful, batch_estimate, item_verdict
from .sweeps import _setup_key, _setup_value, run_setup, setup_label, varied_setups


@dataclass
class Sweep:
    """
    The same test questions asked with several setups (every combination of the settings, models, data sources or
    files given), each setup a Batch of its own. sweep() returns one, and the view keeps each setup's run in
    view.batches like any other test run.
    """

    batches: list[Batch] = field(default_factory=list)  # one per setup, in the order the combinations were made
    varied: dict[str, list[Any]] = field(default_factory=dict)  # what differs between the setups -> its values
    retrieve_only: bool = False  # every setup only searched (Retrieve)
    id: str = ""
    label: str = ""  # what you called it (label=)
    seconds: float = 0.0  # from the first question sent to the last answer back
    stopped: bool = False  # stopped before every question was asked with every setup

    @property
    def ranked(self) -> list[tuple[Batch, RunScore]]:
        """
        The setups best first, each with its score (rank_runs()).
        """
        return rank_runs(self.batches)

    @property
    def best(self) -> Batch | None:
        ranked = self.ranked
        return ranked[0][0] if ranked else None

    @property
    def cost(self) -> float:
        """
        Estimated USD for every question asked, as far as each model's price is known.
        """
        return sum(b.cost for b in self.batches)

    def to_df(self):
        """
        One row per setup, best first: its rank, what it varies, and how it did: answered (or found), grounded,
        expected sources, MRR (searches), failures, average time and estimated cost.
        """
        pd = _require("pandas", "Sweep.to_df")
        rows = []
        for rank, (b, s) in enumerate(self.ranked, 1):
            setup = run_setup(b)
            rows.append({"rank": rank, **{key: setup.get(key) for key in self.varied},
                         "answered": s.answered, "questions": s.questions, "grounded": s.grounded,
                         "expected_hits": s.hits, "expected_checked": s.checked, "mrr": s.mrr, "failed": s.failed,
                         "seconds": s.seconds, "cost": s.cost})
        return pd.DataFrame(rows)


def _came_back(item: BatchItem) -> bool:
    """
    A question that was answered (or searched), or that Bedrock refused: one that counts in a comparison.
    """
    return item.answer is not None or bool(item.error_code)


def shared_questions(batches: Iterable[Batch]) -> set[str]:
    """
    The questions every one of these runs came back with (an answer, or Bedrock's refusal), as compared: in any
    case and spacing.
    """
    sets = [{_key(i.question) for i in b.items if _came_back(i)} for b in batches]
    return set.intersection(*sets) if sets else set()


def run_score(batch: Batch, questions: Iterable[str] | None = None) -> RunScore:
    """
    How a test run did, in numbers -> RunScore, counting `questions` only (their text, in any case; default every
    question that came back).
    """
    keep = None if questions is None else {_key(q) for q in questions}
    items = [i for i in batch.items if _came_back(i) and (keep is None or _key(i.question) in keep)]
    asked = [i for i in items if i.answer is not None]
    s = RunScore(questions=len(items), failed=len(items) - len(asked),
                 checked=sum(_filled(i.expected) for i in items),
                 hits=sum(i.found is not None for i in asked if _filled(i.expected)))
    if batch.retrieve_only:
        s.answered = sum(bool(i.answer.sources) for i in asked)
        if s.checked:
            s.mrr = sum(1 / i.found for i in asked if _filled(i.expected) and i.found) / s.checked
    else:
        spoke = [i for i in asked if not _unhelpful(i.answer) and i.answer.guardrail_action != "INTERVENED"]
        s.answered = len(spoke)
        s.grounded = sum(i.answer.grounded_share for i in spoke) / len(spoke) if spoke else None
    costs = [i.cost for i in asked]
    s.cost = None if any(c is None for c in costs) else sum(costs)
    s.seconds = sum(i.answer.seconds for i in asked) / len(asked) if asked else None
    return s


def _quality(s: RunScore, retrieve_only: bool) -> tuple[int, ...]:
    """
    What ranks a run, best highest: expected sources cited (or found), then answers that say something, then the
    grounded share in steps of 5 points; for searches, expected sources found, then MRR, then searches that found
    passages.
    """
    if retrieve_only:
        return s.hits, round((s.mrr or 0) * 20), s.answered
    return s.hits, s.answered, round((s.grounded or 0) * 20)


def rank_runs(batches: Iterable[Batch]) -> list[tuple[Batch, RunScore]]:
    """
    Test runs of the same questions, best first, each with its score (run_score()) on the questions every one of
    them came back with: by how many answers cite the expected source (searches: find it), then how many say
    something (searches: their MRR, then how many found passages), then the grounded share; when two are as good,
    the cheaper first, then the order given.
    """
    runs = list(batches)
    common = shared_questions(runs)
    scored = [(b, run_score(b, common)) for b in runs]

    def order(k: int) -> tuple[Any, ...]:
        b, s = scored[k]
        return tuple(-x for x in _quality(s, b.retrieve_only)), s.cost if s.cost is not None else math.inf, k

    return [scored[k] for k in sorted(range(len(scored)), key=order)]


def _index_of(runs: list[Batch], batch: Batch) -> int:
    return next(k for k, b in enumerate(runs) if b is batch)


def _score_text(s: RunScore, retrieve_only: bool) -> str:
    """
    The number a run is ranked by, in words: 'cites the expected source in 5 of 6 questions', 'answers 6 of 6
    questions (81% grounded)', 'finds the expected source for 5 of 6 questions (MRR 0.83)'.
    """
    if retrieve_only:
        if s.checked:
            return f"finds the expected source for {_out_of(s.hits, s.checked, 'question')} (MRR {s.mrr or 0:.2f})"
        return f"finds passages for {_out_of(s.answered, s.questions, 'question')}"
    if s.checked:
        return f"cites the expected source in {_out_of(s.hits, s.checked, 'question')}"
    grounded = f" ({s.grounded:.0%} grounded)" if s.grounded is not None else ""
    return f"answers {_out_of(s.answered, s.questions, 'question')}{grounded}"


def _versus(best: RunScore, other: RunScore, retrieve_only: bool) -> str:
    """
    Where `best` does better than `other`, the first thing they're ranked by that differs: 'cites the expected
    source in 5 of 6 questions, against 3 of 6'.
    """
    if best.hits != other.hits:
        verb = "finds the expected source for" if retrieve_only else "cites the expected source in"
        return f"{verb} {_out_of(best.hits, best.checked, 'question')}, against {other.hits} of {other.checked}"
    if retrieve_only and round((best.mrr or 0) * 20) != round((other.mrr or 0) * 20):
        return f"ranks the expected source higher (MRR {best.mrr or 0:.2f}, against {other.mrr or 0:.2f})"
    if best.answered != other.answered:
        verb = "finds passages for" if retrieve_only else "answers"
        return f"{verb} {_out_of(best.answered, best.questions, 'question')}, against {other.answered} of " \
               f"{other.questions}"
    return f"is {best.grounded or 0:.0%} grounded on average, against {other.grounded or 0:.0%}"


def _cost_gap(a: RunScore, b: RunScore) -> str:
    """
    How much more or less a's setup costs than b's: 'about $0.21 more per 100 questions' ('' if unknown).
    """
    pa, pb = a.per_question(), b.per_question()
    if pa is None or pb is None:
        return ""
    gap = (pa - pb) * 100
    if abs(gap) < 0.005:
        return "about the same cost"
    return f"about {human_money(abs(gap))} {'more' if gap > 0 else 'less'} per 100 questions (estimate)"


def ranking_findings(batches: Iterable[Batch], *, now: Batch | None = None,
                     number: Callable[[Batch], int] | None = None, label: Callable[[str], str] | None = None,
                     explain: Callable[[str, str], str] | None = None, brief: bool = False) -> list[tuple[str, str]]:
    """
    What a comparison of test runs of the same questions (a sweep's setups, or compare_runs()) says to do ->
    [(level, message)]: which setup did best and how it beats yours (`now`: the run whose setup is in use, one of
    them or an earlier run of the same questions), whether its lead could be chance, setups Bedrock refused, questions
    no setup handled, and with two or more things varied, what each one changed. brief=True keeps the setups Bedrock
    refused and which setup to use, as a list of runs wants. number(run) gives a run's number for use_run() (default:
    its place in the list, from 1); label(model) names a model; explain(code, message) adds what to do about an AWS
    error.
    """
    runs = list(batches)
    if len(runs) < 2:
        return []
    numbered = runs + ([now] if now is not None and not any(b is now for b in runs) else [])
    num = number or (lambda b: _index_of(numbered, b) + 1)
    retrieve = runs[0].retrieve_only
    varied = varied_setups(runs)

    def name(b: Batch) -> str:
        return setup_label(b, varied, label)

    found: list[tuple[str, str]] = []
    refused = [b for b in runs if any(i.error_code for i in b.items)]
    for b in refused[:3]:
        failed = [i for i in b.items if i.error_code]
        top = Counter(i.error_code for i in failed).most_common(1)[0][0]
        first = next(i for i in failed if i.error_code == top)
        message = (explain(top, first.error) if explain else first.error).strip().rstrip(".")
        found.append(("warn", f"{name(b)} (run {num(b)}): {_out_of(len(failed), len(b.items), 'question')} failed "
                              f"({top}): {message}."))
    if len(refused) > 3:
        found.append(("warn", f"{len(refused) - 3} more setups had questions Bedrock refused: results(run) shows "
                              "each one's errors."))
    common = shared_questions(runs)
    if not common:
        return found + [("warn", "These runs have no question in common that came back, so they can't be ranked: "
                                 "compare runs of the same questions.")]
    ranked = rank_runs(runs)
    best, top = ranked[0]
    score = {id(b): s for b, s in ranked}

    def quality(b: Batch) -> tuple[int, ...]:
        return _quality(score[id(b)], retrieve)

    switch = f"{_call('use_run', num(best))} switches to it"
    current = next((b for b in runs if b is now), None)
    even = all(quality(b) == quality(best) for b in runs)
    if current is None and now is not None and not shared_questions([best, now]):
        now = None  # nothing to compare it on: rank the runs alone
    if current is None and now is not None:  # the setup in use is an earlier run's: the best against it
        pair = {id(b): s for b, s in rank_runs([best, now])}
        theirs, mine = pair[id(best)], pair[id(now)]
        yours = f"your setup now (run {num(now)}: {setup_label(now, varied_setups([*runs, now]), label)})"
        gap = _cost_gap(theirs, mine)
        if _quality(theirs, retrieve) > _quality(mine, retrieve):
            cost = f", for {gap}" if gap else ""
            found.append(("warn", f"{name(best)} did better than {yours}: it {_versus(theirs, mine, retrieve)}{cost}. "
                                  f"{switch}."))
        elif _quality(theirs, retrieve) == _quality(mine, retrieve):
            cheaper = " less " in gap
            found.append(("info", f"{name(best)}, the best here, did as well as {yours}"
                                  + (f", for {gap}" if gap and gap != "about the same cost" else "")
                                  + (f": {switch}." if cheaper else ".")))
        else:
            found.append(("info", f"{yours[:1].upper()}{yours[1:]} did better than every setup tried here: it "
                                  f"{_versus(mine, theirs, retrieve)} for the best of them ({name(best)})."))
    elif current is None:
        if even:
            dearest, high = ranked[-1]
            gap = _cost_gap(top, high)
            cheaper = (f" The cheapest is {name(best)}, {gap} than {name(dearest)}: {switch}."
                       if gap not in ("", "about the same cost") else "")
            found.append(("info", f"Every setup did as well as the others on these {_plural(len(common), 'question')} "
                                  f"(each {_score_text(top, retrieve)}), so what was tried made no difference here."
                                  + cheaper))
        else:
            worst, low = ranked[-1]
            found.append(("info", f"{name(best)} did best: it {_versus(top, low, retrieve)} for the worst setup "
                                  f"({name(worst)}). {switch}."))
    else:
        mine = score[id(current)]
        gap = _cost_gap(top, mine)
        if current is best or (quality(best) == quality(current) and gap in ("", "about the same cost")):
            runner = next((s for b, s in ranked if b is not current), None)
            if runner is not None and _quality(runner, retrieve) == quality(current):
                found.append(("info", f"Your setup now ({name(current)}) did as well as any setup tried "
                                      f"({_score_text(mine, retrieve)}): nothing here beats it on these questions."))
            else:
                beaten = runner if runner is not None else mine
                found.append(("info", f"Your setup now ({name(current)}) did best: it "
                                      f"{_versus(mine, beaten, retrieve)} for the next best. Nothing tried here beats "
                                      "it on these questions."))
        elif quality(best) == quality(current):
            found.append(("info", f"{name(best)} did as well as your setup now ({name(current)}), for {gap}: "
                                  f"{switch}."))
        else:
            cost = f", for {gap}" if gap else ""
            found.append(("warn", f"{name(best)} did better than your setup now ({name(current)}): it "
                                  f"{_versus(top, mine, retrieve)}{cost}. {switch}."))
    if brief:
        return found
    items = {id(b): {_key(i.question): i for i in b.items} for b in runs}
    order = [i.question for i in runs[0].items if _key(i.question) in common]
    if not even and not retrieve and len(ranked) > 1:  # a search finds the same passages every time; a model varies
        second, low = ranked[1]
        lead = [x - y for x, y in zip(_quality(top, retrieve), _quality(low, retrieve))]
        at = next((k for k, d in enumerate(lead) if d), None)
        if at is not None and ((at < 2 and lead[at] == 1) or (at == 2 and lead[at] <= 2)):
            by = "one question" if at < 2 else "a few points of grounded share"
            found.append(("info", f"{name(best)} leads {name(second)} by {by}, which can be chance: a model doesn't "
                                  "answer the same way every time. Ask more questions, or run them again, before you "
                                  "switch."))
    stuck = [items[id(runs[0])][_key(q)] for q in order
             if all(item_verdict(items[id(b)][_key(q)])[1] != "ok" for b in runs)]
    if stuck:
        one = len(stuck) == 1
        how = item_verdict(items[id(best)][_key(stuck[0].question)])[0]
        found.append(("warn", f"{_plural(len(stuck), 'question')} didn't work with any setup: nothing tried here "
                              f"fixes {'it' if one else 'them'} ({_examples(stuck)}; {'' if one else 'the first: '}"
                              f"{how} with the best one), so the documents may not hold the answer, or a file isn't "
                              f"indexed. {_call('retrieve', stuck[0].question)} shows what the search finds"
                              f"{'' if one else ' for the first'}, and files() whether a file is indexed."))
    if len(varied) >= 2:
        for key in varied:
            groups: dict[str, list[Batch]] = {}
            for b, _ in ranked:  # each group keeps the ranking's order: its best first
                setup = run_setup(b)
                rest = {k: setup.get(k) for k in varied if k != key}
                groups.setdefault(_setup_key(rest), []).append(b)
            pairs = [g for g in groups.values() if len(g) > 1]
            if not pairs:
                continue
            winners = [_setup_key(run_setup(g[0]).get(key)) for g in pairs if quality(g[0]) != quality(g[1])]
            values = {_setup_key(v): v for v in varied[key]}
            shown = ((f"{len(pairs)} groups" if len(pairs) > 1 else "the one pair")
                     + f" of setups that differ only in {key}")
            if not winners:
                found.append(("info", f"{key} made no difference: {'each of the ' if len(pairs) > 1 else ''}"
                                      f"{shown} did the same on these questions."))
                continue
            value, wins = Counter(winners).most_common(1)[0]
            text = f"{key}={_setup_value(key, values[value], varied[key], label)}"
            if wins == len(pairs):
                found.append(("info", f"{text} did best in {'each of the ' if len(pairs) > 1 else ''}{shown}."))
            else:
                ties = len(pairs) - len(winners)
                found.append(("info", f"{key} has no clear effect: {text} did best in {wins} of the {shown}"
                                      + (f", and {ties} {'was a tie' if ties == 1 else 'were ties'}" if ties else "")
                                      + "."))
    asked = {_key(i.question) for b in runs for i in b.items if i.request}
    if len(common) < len(asked):
        missing = len(asked) - len(common)
        found.append(("info", f"Ranked on the {_plural(len(common), 'question')} every setup came back with: "
                              f"{_plural(missing, 'question')} {'was' if missing == 1 else 'were'}n't asked with every "
                              "setup (the run stopped first, or Bedrock refused them)."))
    return found


def sweep_estimate(sweep: Sweep, *, model_prices: dict[str, tuple[float, float]] | None = None,
                   prices: dict[str, float] | None = None) -> float | None:
    """
    Roughly what asking a prepared sweep costs in USD, before it's asked (batch_estimate() for each setup's
    questions): None when a model's price isn't known. An estimate: label it as one.
    """
    total = 0.0
    for b in sweep.batches:
        cost = batch_estimate([i.question for i in b.items if i.request and i.answer is None and not i.error],
                              b.settings, b.model, retrieve_only=b.retrieve_only, model_prices=model_prices,
                              prices=prices)
        if cost is None:
            return None
        total += cost
    return total
