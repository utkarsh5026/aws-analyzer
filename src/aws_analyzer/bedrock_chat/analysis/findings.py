"""
Findings about the settings, an answer or a search, and what one answer cites of the search
behind it, with what an answer cost.
"""

from __future__ import annotations

from typing import Any

from ..._bedrock.estimates import estimate_tokens, generation_cost, query_cost
from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import _REFUSAL, source_name
from ..._bedrock.models import Passage
from ..._kit.fmt import _plural

from ..models import Answer
from .choices import describe_files


# ------------------------- findings: what's wrong, why it matters, what to do


def settings_findings(settings: dict[str, Any], model: str = "") -> list[tuple[str, str]]:
    """
    Settings that will fail, or won't do what they seem to -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    if settings.get("temperature") is not None and settings.get("top_p") is not None:
        claude = "claude" in (model or "").lower()
        found.append(("warn" if claude else "info",
                      "Both temperature and top_p are set. Newer Claude models take only one of them and refuse the "
                      "question: keep one (unset('top_p'))."))
    for key in ("prompt", "orchestration_prompt"):
        prompt = settings.get(key)
        if key == "prompt" and isinstance(prompt, str) and "$output_format_instructions$" not in prompt:
            found.append(("warn", "The prompt has no $output_format_instructions$, which is where Bedrock asks the "
                                  "model to cite its sources, so answers will come back without citations. Add it "
                                  "back on a line of its own."))
    if settings.get("reranker"):
        n = settings.get("n") or 5
        if n < 10:
            found.append(("info", f"The reranker can only re-order the {_plural(n, 'passage')} retrieved. Give it more "
                                  "to choose from and keep the best few: set(n=20, rerank_n=5)."))
    if (settings.get("guardrail_id") is None) != (settings.get("guardrail_version") is None):
        missing = "guardrail_version" if settings.get("guardrail_id") is not None else "guardrail_id"
        found.append(("warn", f"A guardrail needs both guardrail_id and guardrail_version, and {missing} isn't set: "
                              "Bedrock will refuse the question until it is."))
    if settings.get("query_decomposition"):
        found.append(("info", "Query decomposition adds a model call before searching, so answers take longer and "
                              "cost a little more."))
    return found


def _outside_picks(a: Answer) -> list[int]:
    """
    The numbers of the passages that came from outside the data sources and files the question was asked of.
    """
    return [i for i, p in enumerate(a.sources, 1)
            if (a.data_sources and p.data_source_id and p.data_source_id not in a.data_sources)
            or (a.files and p.uri and p.uri not in a.files)]


def _outside_finding(a: Answer, outside: list[int]) -> tuple[str, str]:
    picked = " and ".join(filter(None, [describe_sources(a.data_sources) if a.data_sources else "",
                                        describe_files(a.files) if a.files else ""]))
    what = (f"{_plural(len(outside), 'passage')} ({', '.join(f'#{i}' for i in outside[:5])})" if a.retrieve_only
            else f"{_plural(len(outside), 'source')} ({', '.join(f'[{i}]' for i in outside[:5])})")
    return ("warn", f"{what} came from outside {picked}: this vector store didn't apply the filter on Bedrock's own "
                    "keys. Tag the files with your own metadata instead (a <file>.metadata.json), sync, and use the "
                    "filter setting.")


def _search_findings(a: Answer) -> list[tuple[str, str]]:
    """
    What a retrieve-only search found, and what to try when it isn't what an answer needs.
    """
    found: list[tuple[str, str]] = []
    if not a.sources:
        tries = []
        if a.settings.get("filter") is not None:
            tries.append("check the filter isn't too narrow (unset('filter'))")
        if a.files:
            tries.append(f"search more than {describe_files(a.files)} (use(files='all'))")
        if a.data_sources:
            tries.append("search every data source (use(data_source='all'))")
        found.append(("warn", "Nothing came back, so an answer would have nothing to go on. Try to "
                              + (", or ".join(tries) if tries else "check that the knowledge base has indexed files "
                                 "(files() shows each one's status)") + "."))
    outside = _outside_picks(a)
    if outside:
        found.append(_outside_finding(a, outside))
    files = {p.uri or p.source for p in a.sources}
    if len(a.sources) >= 3 and len(files) == 1:
        tries = ["more passages (set(n=10))"] if (a.settings.get("n") or 5) < 10 else []
        if a.settings.get("search_type") != "HYBRID":
            tries.append("exact words too (set(search_type='HYBRID'))")
        found.append(("info", f"All {len(a.sources)} passages come from one file ({source_name(next(iter(files)))}). "
                              "If the answer could be in other files, try " + (" or ".join(tries) or "a filter that "
                              "leaves this one out") + "."))
    found += [("info", note) for note in a.notes]
    return found


def answer_findings(a: Answer) -> list[tuple[str, str]]:
    """
    How far to trust an answer, and which setting to try next -> [(level, message)]. For a retrieve-only search,
    what it found and what to try when it isn't what an answer needs.
    """
    if a.retrieve_only:
        return _search_findings(a)
    found: list[tuple[str, str]] = []
    if a.guardrail_action == "INTERVENED":
        found.append(("warn", "A guardrail stepped in: the question or the answer was blocked or rewritten. The "
                              "guardrail's settings in the Bedrock console say what it blocks."))
    text = a.text.strip()
    if _REFUSAL in text.lower() or not text:
        tries = []
        if (a.settings.get("n") or 5) < 10:
            tries.append("retrieve more passages (set(n=10))")
        if a.settings.get("search_type") != "HYBRID":
            tries.append("match exact words too (set(search_type='HYBRID'))")
        if a.settings.get("filter") is not None:
            tries.append("check the filter isn't too narrow (unset('filter'))")
        if a.files:
            tries.append(f"search more than {describe_files(a.files)} (use(files='all'))")
        if a.data_sources:
            tries.append("search every data source (use(data_source='all'))")
        what = "Bedrock's default \"unable to assist\" reply" if text else "An empty answer"
        found.append(("warn", f"{what}: the passages it found don't hold the answer, or none were found. Try to "
                              + (", or ".join(tries) if tries else "ask with the words your documents use") + "."))
    elif not a.cited:
        prompt = a.settings.get("prompt")
        if isinstance(prompt, str) and "$output_format_instructions$" not in prompt:
            found.append(("warn", "No citations: the prompt has no $output_format_instructions$, which is where "
                                  "Bedrock asks the model to cite its sources. Add it back to the prompt."))
        else:
            found.append(("warn", "The answer cites no source, so it may come from the model's own knowledge rather "
                                  "than your documents."))
    elif a.grounded_share < 0.5:
        found.append(("warn", f"Only {a.grounded_share:.0%} of the answer is backed by a citation; the rest may be the "
                              "model's own knowledge. Check the sentences without a [n]."))
    outside = _outside_picks(a)
    if outside:
        found.append(_outside_finding(a, outside))
    limit = a.settings.get("max_tokens")
    if limit and a.output_tokens >= 0.9 * limit:
        found.append(("warn", f"The answer is about as long as max_tokens allows ({limit:,}), so it may have been cut "
                              f"off: raise it (set(max_tokens={max(limit * 2, 1024)}))."))
    found += [("info", note) for note in a.notes]
    return found


def _same_passage(a: Passage, b: Passage) -> bool:
    if a.chunk_id and b.chunk_id:
        return a.chunk_id == b.chunk_id
    return a.uri == b.uri and " ".join(a.text.split()) == " ".join(b.text.split())


def cited_ranks(search: Answer, answer: Answer) -> dict[int, int]:
    """
    {rank in a retrieve-only search: [n] in an answer} for each passage the search found that the answer cites,
    in rank order: how a search and an answer to the same question line up.
    """
    ranks: dict[int, int] = {}
    for n, cited in enumerate(answer.sources, 1):
        for p in search.sources:
            if _same_passage(p, cited):
                ranks.setdefault(p.rank, n)
                break
    return dict(sorted(ranks.items()))


def compare_findings(search: Answer, answer: Answer) -> list[tuple[str, str]]:
    """
    What asking a question both ways shows (a retrieve-only search, and an answer): which of the passages found
    the answer cites, and whether a poor answer comes from the search or from the answer step -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    ranks = cited_ranks(search, answer)
    count = len(search.sources)
    text = answer.text.strip()
    if (not text or _REFUSAL in text.lower()) and count:
        what = "empty" if not text else "Bedrock's \"unable to assist\" reply"
        found.append(("warn", f"The answer to this question was {what}, yet the search found "
                              f"{_plural(count, 'passage')}. If they hold the answer, the search works and the answer "
                              "step doesn't: try another model, or a prompt that asks it to answer from the search "
                              "results (set(prompt=...)). If they don't, the search is what to fix."))
    elif ranks:
        pairs = ", ".join(f"#{rank} as [{n}]" for rank, n in ranks.items())
        rest = count - len(ranks)
        found.append(("info", f"The answer cites {len(ranks)} of the {_plural(count, 'passage')} the search found "
                              f"({pairs})" + (f"; the other {rest} weren't cited." if rest > 1 else
                                              "; the other one wasn't cited." if rest else ".")))
    missing = [n for n in range(1, len(answer.sources) + 1) if n not in ranks.values()]
    if missing:
        one = len(missing) == 1
        found.append(("info", f"{'Source' if one else 'Sources'} {', '.join(f'[{n}]' for n in missing[:5])} of the "
                              f"answer {'is' if one else 'are'}n't among the search's passages, so the two searches "
                              "differed: other settings, query decomposition, or a follow-up question Bedrock "
                              "rewrote with the earlier ones."))
    return found


def answer_cost(
    a: Answer,
    model_prices: dict[str, tuple[float, float]] | None = None,
    prices: dict[str, float] | None = None,
) -> float | None:
    """
    Estimated USD for one answer: the model's tokens (estimated from characters), embedding the question and, with
    a reranker, the reranking. None when the model isn't in the price table (pass model_prices=...). A retrieve-only
    search costs the embedding and the reranking only.
    """
    if a.retrieve_only:
        return query_cost(1, a.settings.get("reranker") or False, prices, question_tokens=estimate_tokens(a.question))
    generation = generation_cost(a.input_tokens, a.output_tokens, a.model, model_prices)
    if generation is None:
        return None
    return generation + query_cost(1, a.settings.get("reranker") or False, prices,
                                   question_tokens=estimate_tokens(a.question))
