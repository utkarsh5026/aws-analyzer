"""
Test questions and runs of them (Batch): questions read from text and checked against what
each should cite, and a run's findings, changes since the last run and estimate.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..._bedrock.estimates import estimate_tokens, generation_cost, query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import _REFUSAL, source_name
from ..._bedrock.parsing import match_expected
from ..._bedrock.prices import short_model
from ..._kit.deps import _require
from ..._kit.fmt import _as_int, _plural, human_money
from ..._kit.render import _call
from ..._kit.text import _clip

from ..helpers import DEFAULT_PROMPT, MAX_QUESTION_CHARS, _diff, _short
from ..models import Answer
from .filters import describe_filter
from .choices import describe_files
from .findings import _outside_picks


@dataclass
class BatchItem:
    """
    One question of a test run: what was asked, what came back (or why nothing did), and where the source it was
    expected to come from turned up.
    """

    question: str
    expected: Any = None  # a piece of the file name, s3:// path or text the answer should come from (a list: any)
    answer: Answer | None = None  # None when it failed, wasn't sent, or wasn't asked (the run stopped first)
    error: str = ""  # why it failed (Bedrock's message), or why it wasn't sent (a question that's too long)
    error_code: str = ""  # 'ThrottlingException', 'AccessDeniedException'...; '' when it wasn't sent at all
    cost: float | None = None  # estimated USD; None for a model that isn't in the price table, or nothing asked
    request: dict[str, Any] = field(default_factory=dict)  # what was (or would have been) sent

    @property
    def found(self) -> int | None:
        """
        Where the expected source came up: the [n] the answer cites it as, or its rank in a retrieve-only search.
        None when it didn't come up, or nothing was expected.
        """
        return expected_at(self.answer, self.expected) if self.answer is not None else None


@dataclass
class Batch:
    """
    A list of test questions asked with one setup, each on its own (never as a follow-up), and what came back.
    ask_all() returns one, and the view keeps every run in view.batches.
    """

    items: list[BatchItem] = field(default_factory=list)  # in the order the questions were given
    kb_id: str = ""
    kb_name: str = ""
    model: str = ""  # the model ID or inference profile that answered ('' for a retrieve-only run)
    settings: dict[str, Any] = field(default_factory=dict)  # what was sent with every question
    data_sources: dict[str, str] = field(default_factory=dict)  # ID -> name of the only ones searched; {} = all
    files: list[str] = field(default_factory=list)  # s3:// paths of the only files searched; [] = all
    retrieve_only: bool = False  # each question only searched (Retrieve): every passage found, no answer
    request: dict[str, Any] = field(default_factory=dict)  # the setup: the request, with a placeholder question
    seconds: float = 0.0  # from the first question sent to the last answer back
    skipped: int = 0  # questions past limit=, not asked
    stopped: bool = False  # stopped (Stop, or an interrupt) before every question was asked
    id: str = ""  # unique, so save_runs() and load_runs() keep each run once
    label: str = ""  # what you called it (label=), or for a sweep's run its setup: 'n=10 · search_type=HYBRID'
    sweep: str = ""  # the ID of the sweep it's part of; '' when it was asked on its own
    started: datetime | None = None  # when it was sent

    @property
    def asked(self) -> list[BatchItem]:
        """
        The questions that came back: with an answer, or with the search's passages.
        """
        return [i for i in self.items if i.answer is not None]

    @property
    def failed(self) -> list[BatchItem]:
        """
        The questions Bedrock refused, and the ones that couldn't be sent.
        """
        return [i for i in self.items if i.error]

    @property
    def cost(self) -> float:
        """
        Estimated USD for the questions asked, as far as their model's price is known.
        """
        return sum(i.cost for i in self.items if i.cost is not None)

    def to_df(self):
        """
        One row per question: the question, the source expected, how it did, the answer, its grounded share, the
        sources it cites (every passage found, for a retrieve-only run), where the expected one came up, the best
        score, time, estimated cost and any error.
        """
        pd = _require("pandas", "Batch.to_df")
        rows = []
        for n, item in enumerate(self.items, 1):
            a = item.answer
            spoke = a is not None and not a.retrieve_only and bool(a.text.strip())
            rows.append({
                "n": n, "question": item.question, "expected": item.expected, "result": item_verdict(item)[0],
                "answer": a.text if spoke else None,
                "grounded": a.grounded_share if spoke else None,
                "sources": [p.source for p in a.sources] if a is not None else [],
                "found": item.found,
                "best_score": (max((p.score for p in a.sources if p.score is not None), default=None)
                               if a is not None and a.retrieve_only else None),
                "seconds": a.seconds if a is not None else None,
                "cost": item.cost,
                "error": item.error or None,
            })
        return pd.DataFrame(rows)


# ---------------------------------------------- test runs: a list of questions asked with one setup

_LIST_MARK_RE = re.compile(r"^(?:[-*•]|\d{1,4}[.)]|q\d{0,4}\s*[:.)])\s+", re.IGNORECASE)

_THROTTLED = {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}

_GRADES = {"ok": 2, "warn": 1, "bad": 0}  # how a question did, to tell whether it did better than before


def _filled(value: Any) -> bool:
    """
    Whether a value says something: not None, NaN or pandas' NA (an empty cell), blank text or an empty list.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)) or type(value).__name__ in ("NAType",
                                                                                                     "NaTType"):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return bool(value)
    return True


def parse_questions(text: str) -> list[tuple[str, Any]]:
    """
    Test questions pasted as text, one per line -> [(question, expected or None)]. Blank lines and lines starting
    with # are left out, and a list mark in front ('1.', '-', 'Q:') is dropped. After a | (or a tab, as two columns
    pasted from a spreadsheet) comes the source the answer should come from: a piece of its file name, s3:// path or
    text. Several, each after its own |, mean any of them.

        How long do refunds take? | refund-policy.pdf
        2. Can I return a digital product?
    """
    cases: list[tuple[str, Any]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        question, expected = _split_case(_LIST_MARK_RE.sub("", line, count=1))
        if question:
            cases.append((question, expected))
    return cases


def _split_case(line: str) -> tuple[str, Any]:
    """
    'How long? | refund-policy.pdf' -> ('How long?', 'refund-policy.pdf'); several after their own | -> a list.
    """
    question, *rest = re.split(r"\s*[|\t]\s*", line.strip())
    expected = [e.strip() for e in rest if e.strip()]
    return question.strip(), expected[0] if len(expected) == 1 else expected or None


def format_questions(cases: Iterable[tuple[str, Any]]) -> str:
    """
    The opposite of parse_questions(): one question per line, each expected source after a ' | '.
    """
    lines = []
    for question, expected in cases:
        wanted = ([] if not _filled(expected) else [str(expected)] if isinstance(expected, str)
                  else [str(e) for e in expected])
        lines.append(" | ".join([" ".join(str(question).split()), *wanted]))
    return "\n".join(lines)


def question_list(questions: Any) -> list[tuple[str, Any]]:
    """
    What ask_all() takes -> [(question, expected or None)]: text with one question per line (parse_questions()),
    a list of questions, of (question, expected) pairs or of dicts with 'question' and 'expected', a DataFrame with
    those columns, or an earlier run (a Batch) to ask its questions again. Blank questions are left out; a ValueError
    says what to pass when nothing is left.
    """
    if isinstance(questions, str):
        cases = parse_questions(questions)
    else:
        if isinstance(questions, Batch):
            rows: list[Any] = [(i.question, i.expected) for i in questions.items]
        elif hasattr(questions, "to_dict") and hasattr(questions, "columns"):  # a DataFrame
            rows = questions.to_dict("records")
        elif hasattr(questions, "tolist") and not isinstance(questions, (list, tuple)):  # a Series or an array
            rows = questions.tolist()
        else:
            rows = list(questions or [])
        cases = []
        for row in rows:
            if not _filled(row):  # a gap in the list
                continue
            if isinstance(row, dict):
                question, expected = row.get("question"), row.get("expected", row.get("source"))
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                question, expected = row
            elif isinstance(row, str):
                question, expected = _split_case(row)  # 'question | expected file', as in the window
            else:
                raise ValueError("ask_all() takes questions as text with one per line, a list of questions, (question, "
                                 f"expected file) pairs, or dicts with 'question' and 'expected'; got {row!r}")
            if _filled(question):
                cases.append((str(question).strip(), expected if _filled(expected) else None))
    if not cases:
        raise ValueError("No questions to ask: pass a list, like ask_all(['How long do refunds take?', 'Can I return "
                         "a digital product?']), or text with one question per line")
    return cases


def expected_at(a: Answer, expected: Any) -> int | None:
    """
    Where a test question's expected source came up (see match_expected()): the [n] an answer cites it as, or its
    rank in a retrieve-only search. None when it didn't come up, or nothing was expected.
    """
    if not _filled(expected):
        return None
    for n, p in enumerate(a.sources, 1):
        if match_expected(p, expected):
            return p.rank if a.retrieve_only else n
    return None


def _unhelpful(a: Answer) -> bool:
    """
    An empty answer, or Bedrock's "unable to assist" reply.
    """
    text = a.text.strip()
    return not text or _REFUSAL in text.lower()


def item_verdict(item: BatchItem) -> tuple[str, str]:
    """
    How a test question did, in a few words, and its tone: ('answered', 'ok'), ('unable to assist', 'warn'),
    ('expected not cited', 'warn'), ('failed', 'bad'), ('found #2', 'ok') for a search...
    """
    a = item.answer
    if a is None:
        if item.error_code:
            return "failed", "bad"
        return ("not sent", "warn") if item.error else ("not asked", "")
    checked = _filled(item.expected)
    if a.retrieve_only:
        if not a.sources:
            return "nothing found", "warn"
        if checked:
            return (f"found #{item.found}", "ok") if item.found else ("expected not found", "warn")
        return _plural(len(a.sources), "passage"), "ok"
    if a.guardrail_action == "INTERVENED":
        return "guardrail stepped in", "warn"
    if _unhelpful(a):
        return ("unable to assist" if a.text.strip() else "empty answer"), "warn"
    if checked and not item.found:
        return "expected not cited", "warn"
    if not a.cited:
        return "no citations", "warn"
    if a.grounded_share < 0.5:
        return "partly grounded", "warn"
    return "answered", "ok"


def _examples(items: list[BatchItem], count: int = 2, width: int = 48) -> str:
    """
    "'How long do refunds take?', 'Can I return…'" (and ', …' when there are more).
    """
    shown = ", ".join(repr(_clip(i.question, width)) for i in items[:count])
    return shown + (", …" if len(items) > count else "")


def _out_of(part: int, whole: int, noun: str = "answer") -> str:
    """
    '1 of 6 answers'.
    """
    return f"{part:,} of {_plural(whole, noun)}"


def _tries(settings: dict[str, Any], data_sources: dict[str, str], files: list[str]) -> str:
    """
    What to try when a search finds nothing useful: 'retrieve more passages (set(n=10)), or ...'.
    """
    tries = []
    if (settings.get("n") or 5) < 10:
        tries.append("retrieve more passages (set(n=10))")
    if settings.get("search_type") != "HYBRID":
        tries.append("match exact words too (set(search_type='HYBRID'))")
    if settings.get("filter") is not None:
        tries.append("check the filter isn't too narrow (unset('filter'))")
    if files:
        tries.append(f"search more than {describe_files(files)} (use(files='all'))")
    if data_sources:
        tries.append("search every data source (use(data_source='all'))")
    return ", or ".join(tries) or "ask with the words your documents use"


def _avg_grounded(batch: Batch) -> float | None:
    """
    The average grounded share of a run's answers that say something (not "unable to assist").
    """
    shares = [i.answer.grounded_share for i in batch.asked if not i.answer.retrieve_only and not _unhelpful(i.answer)
              and i.answer.guardrail_action != "INTERVENED"]
    return sum(shares) / len(shares) if shares else None


def batch_findings(batch: Batch, explain: Callable[[str, str], str] | None = None) -> list[tuple[str, str]]:
    """
    What a test run says to change -> [(level, message)]: questions that failed or weren't sent, answers that
    couldn't help or cite nothing, expected sources that didn't come up, answers cut off, and which setting to try.
    explain(code, message) can add what to do about an AWS error (the view passes its own).
    """
    found: list[tuple[str, str]] = []
    items, asked = batch.items, batch.asked
    failed = [i for i in items if i.error_code]
    if failed:
        codes = Counter(i.error_code for i in failed)
        top = codes.most_common(1)[0][0]
        first = next(i for i in failed if i.error_code == top)
        message = (explain(top, first.error) if explain else first.error).rstrip()
        what = ", ".join(f"{code} ×{n}" if n > 1 else code for code, n in codes.most_common())
        tip = f" Bedrock throttled them: ask fewer at a time ({_call('ask_all', workers=1)})." if top in _THROTTLED else ""
        found.append(("warn", f"{_out_of(len(failed), len(items), 'question')} failed ({what}): "
                              f"{message.rstrip('.')}.{tip}"))
    unsent = [i for i in items if i.error and not i.error_code]
    if unsent:
        one = len(unsent) == 1
        found.append(("warn", f"{_plural(len(unsent), 'question')} ({_examples(unsent)}) {'was' if one else 'were'}n't "
                              f"sent: {unsent[0].error.rstrip('.')}."))
    if batch.retrieve_only:
        found += _search_batch_findings(batch, asked)
    else:
        found += _answer_batch_findings(batch, asked)
    outside = [i for i in asked if _outside_picks(i.answer)]
    if outside:
        picked = " and ".join(filter(None, [describe_sources(batch.data_sources) if batch.data_sources else "",
                                            describe_files(batch.files) if batch.files else ""]))
        found.append(("warn", f"{_out_of(len(outside), len(asked), 'question')} got passages from outside {picked} "
                              f"({_examples(outside)}): this vector store didn't apply the filter on Bedrock's own "
                              "keys. Tag the files with your own metadata instead (a <file>.metadata.json), sync, and "
                              "use the filter setting."))
    waiting = [i for i in items if i.answer is None and not i.error]
    if waiting:
        one = len(waiting) == 1
        found.append(("info", f"Stopped before {_plural(len(waiting), 'question')} {'was' if one else 'were'} asked: "
                              "ask_all() asks the whole list again."))
    if batch.skipped:
        everything = len(items) + batch.skipped
        found.append(("info", f"Only the first {len(items):,} of {everything:,} questions were asked (limit="
                              f"{len(items)}): {_call('ask_all', limit=everything)} asks them all."))
    return found


def _answer_batch_findings(batch: Batch, asked: list[BatchItem]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    blocked = [i for i in asked if i.answer.guardrail_action == "INTERVENED"]
    unable = [i for i in asked if i not in blocked and _unhelpful(i.answer)]
    if blocked:
        found.append(("warn", f"A guardrail stepped in on {_plural(len(blocked), 'question')} ({_examples(blocked)}): "
                              "the question or the answer was blocked or rewritten. The guardrail's settings in the "
                              "Bedrock console say what it blocks."))
    if unable:
        one = len(unable) == 1
        found.append(("warn", f"{_out_of(len(unable), len(asked))} {'is' if one else 'are'} Bedrock's \"unable to "
                              f"assist\" reply or empty ({_examples(unable)}): the passages found don't hold the "
                              f"answer, or none were found. {_call('retrieve', unable[0].question)} shows what the "
                              f"search finds for {'it' if one else 'the first'}; then try to "
                              f"{_tries(batch.settings, batch.data_sources, batch.files)}, and ask_all() again to "
                              "compare."))
    spoke = [i for i in asked if i not in blocked and i not in unable]
    uncited = [i for i in spoke if not i.answer.cited]
    if uncited:
        prompt = batch.settings.get("prompt")
        if isinstance(prompt, str) and "$output_format_instructions$" not in prompt:
            why = ("the prompt has no $output_format_instructions$, which is where Bedrock asks the model to cite its "
                   "sources. Add it back to the prompt")
        else:
            why = "they may come from the model's own knowledge rather than your documents"
        verb = "cites" if len(uncited) == 1 else "cite"
        found.append(("warn", f"{_out_of(len(uncited), len(asked))} {verb} no source ({_examples(uncited)}): {why}."))
    thin = [i for i in spoke if i.answer.cited and i.answer.grounded_share < 0.5]
    if thin:
        one = len(thin) == 1
        found.append(("info", f"{_out_of(len(thin), len(asked))} {'is' if one else 'are'} less than half backed by "
                              f"citations ({_examples(thin)}); the rest may be the model's own knowledge. Check the "
                              "sentences without a [n]."))
    checked = [i for i in asked if _filled(i.expected)]
    missed = [i for i in checked if i.found is None and i not in unable and i not in blocked]
    if missed:
        first = missed[0]
        cited = ", ".join(dict.fromkeys(source_name(p.uri) or p.source for p in first.answer.sources)) or "nothing"
        found.append(("warn", f"The expected source isn't cited in {_out_of(len(missed), len(checked))} checked "
                              f"({first.question!r} expected {_short(first.expected, 40)}, cited {cited}). "
                              f"{_call('retrieve', first.question)} shows whether the search finds it; if it doesn't, "
                              "check the file is indexed (files()), then try set(search_type='HYBRID') or "
                              "set(n=10)."))
    limit = batch.settings.get("max_tokens")
    cut = [i for i in spoke if limit and i.answer.output_tokens >= 0.9 * limit]
    if cut:
        found.append(("warn", f"{_out_of(len(cut), len(asked))} reached max_tokens ({limit:,}) and may be cut off "
                              f"({_examples(cut)}): raise it (set(max_tokens={max(limit * 2, 1024)}))."))
    return found


def _search_batch_findings(batch: Batch, asked: list[BatchItem]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    empty = [i for i in asked if not i.answer.sources]
    if empty:
        found.append(("warn", f"Nothing came back for {_out_of(len(empty), len(asked), 'question')} "
                              f"({_examples(empty)}), so an answer would have nothing to go on. Try to "
                              f"{_tries(batch.settings, batch.data_sources, batch.files)}."))
    checked = [i for i in asked if _filled(i.expected)]
    missed = [i for i in checked if i.answer.sources and i.found is None]
    if missed:
        first = missed[0]
        came = ", ".join(dict.fromkeys(p.source for p in first.answer.sources[:2]))
        found.append(("warn", f"The expected source wasn't among the passages found for "
                              f"{_out_of(len(missed), len(checked), 'question')} checked ({first.question!r} expected "
                              f"{_short(first.expected, 40)}; first came {came}). Check the file is indexed (files()); "
                              f"then try to {_tries(batch.settings, batch.data_sources, batch.files)}."))
    late = [i for i in checked if i.found is not None and i.found > 1]
    if late:
        mrr = sum(1 / i.found for i in checked if i.found) / len(checked)
        rerank = "" if batch.settings.get("reranker") else " A reranker (set(reranker='cohere')) or HYBRID search can " \
                                                           "move it up."
        found.append(("info", f"{_plural(len(late), 'question')} found the expected source below the first passage "
                              f"(MRR {mrr:.2f}; 1.00 means it always came first).{rerank}"))
    return found


def _key(question: str) -> str:
    return " ".join(str(question).lower().split())


def _settings_text(settings: dict[str, Any]) -> str:
    """
    The settings on one line: 'n=8, where team = "billing", temperature=0.2, prompt (412 characters)'.
    """
    parts = []
    for key, value in settings.items():
        if key == "filter":
            parts.append(f"where {describe_filter(value)}")
        elif isinstance(value, str) and len(value) > 40:
            parts.append(f"{key} ({len(value):,} characters)")
        else:
            parts.append(f"{key}={_short(value, 30)}")
    return ", ".join(parts)


def batch_changes(before: Batch, after: Batch, label: Callable[[str], str] | None = None) -> list[tuple[str, str]]:
    """
    What changed since an earlier run of the same questions -> [(level, message)]: what differs in the setup (the
    settings, model, knowledge base, data sources and files), which questions did better or worse, and the averages.
    [] when the two runs share no question. label(model) names a model (its short ID by default).
    """
    name = label or short_model
    old = {_key(i.question): i for i in before.items if i.answer is not None or i.error_code}
    pairs = [(old[_key(i.question)], i) for i in after.items
             if _key(i.question) in old and (i.answer is not None or i.error_code)]
    if not pairs:
        return []
    setup = _diff(before.settings, after.settings)
    if before.retrieve_only != after.retrieve_only:
        setup.append("answers → retrieve only" if after.retrieve_only else "retrieve only → answers")
    elif before.model != after.model and not after.retrieve_only:
        setup.append(f"model {name(before.model)} → {name(after.model)}")
    if before.kb_id != after.kb_id:
        setup.append(f"knowledge base {before.kb_name or before.kb_id} → {after.kb_name or after.kb_id}")
    if before.data_sources != after.data_sources:
        setup.append(f"{describe_sources(before.data_sources)} → {describe_sources(after.data_sources)}")
    if before.files != after.files:
        setup.append(f"{describe_files(before.files)} → {describe_files(after.files)}")
    better, worse = [], []
    for was, now in pairs:
        (old_label, old_tone), (new_label, new_tone) = item_verdict(was), item_verdict(now)
        if _GRADES.get(new_tone, 1) > _GRADES.get(old_tone, 1):
            better.append(f"{_clip(now.question, 40)!r} {old_label} → {new_label}")
        elif _GRADES.get(new_tone, 1) < _GRADES.get(old_tone, 1):
            worse.append(f"{_clip(now.question, 40)!r} {old_label} → {new_label}")
    parts = []
    for changed, how in ((better, "did better"), (worse, "did worse")):
        if changed:
            more = ", …" if len(changed) > 2 else ""
            parts.append(f"{_plural(len(changed), 'question')} {how} ({'; '.join(changed[:2])}{more})")
    if not parts:
        parts.append(f"{'both questions' if len(pairs) == 2 else f'all {len(pairs)} questions'} did as before"
                     if len(pairs) > 1 else "the question did as before")
    before_share, after_share = _avg_grounded(before), _avg_grounded(after)
    if before_share is not None and after_share is not None and round(before_share, 2) != round(after_share, 2):
        parts.append(f"grounded {before_share:.0%} → {after_share:.0%} on average")
    if human_money(before.cost) != human_money(after.cost):
        parts.append(f"cost {human_money(before.cost)} → {human_money(after.cost)}")
    shared = "" if len(pairs) == len(after.items) == len(before.items) else (
        f"; the {_plural(len(pairs), 'question')} both runs asked")
    what = "; ".join(setup) if setup else "the same setup"
    return [("warn" if worse else "info", f"Since the last run ({what}{shared}): " + "; ".join(parts) + ".")]


def batch_estimate(questions: Iterable[str], settings: dict[str, Any], model: str, *, retrieve_only: bool = False,
                   model_prices: dict[str, tuple[float, float]] | None = None,
                   prices: dict[str, float] | None = None) -> float | None:
    """
    Roughly what asking these questions costs in USD, before they're asked: each question's embedding (and its
    reranking), and for answers the model's tokens, guessing about 300 tokens a passage and 300 for each answer. None
    when the model isn't in the price table. An estimate: label it as one.
    """
    texts = [str(q) for q in questions]
    search = sum(query_cost(1, settings.get("reranker") or False, prices, question_tokens=estimate_tokens(q))
                 for q in texts)
    if retrieve_only:
        return search
    given = (settings.get("rerank_n") if settings.get("reranker") else None) or settings.get("n") or 5
    prompt = estimate_tokens(settings.get("prompt") or DEFAULT_PROMPT)
    tokens_in = sum(estimate_tokens(q) + prompt + given * 300 for q in texts)
    tokens_out = min(300, settings.get("max_tokens") or 300) * len(texts)
    generation = generation_cost(tokens_in, tokens_out, model, model_prices)
    return None if generation is None else generation + search


def _question_cap(limit: Any) -> int | None:
    """
    How many test questions limit= lets a run ask (None: every one, as 0 does too); a ValueError for anything else.
    """
    cap = None if limit is None else _as_int(limit, "limit", hint=", or None for every question")
    if cap is not None and cap < 0:
        raise ValueError(f"limit takes a number of questions, like 50, or None for every question; got {limit!r}")
    return cap or None


def _question_text(question: Any) -> str:
    text = str(question if question is not None else "").strip()
    if not text:
        raise ValueError("Pass a question, like ask('How long do refunds take?')")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(
            f"Bedrock takes questions of up to {MAX_QUESTION_CHARS:,} characters, and this one has {len(text):,}. "
            "Shorten it, or ask it as two questions."
        )
    return text
