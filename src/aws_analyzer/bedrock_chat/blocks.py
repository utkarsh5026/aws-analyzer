"""
bedrock_chat's own render blocks and their drawings: a turn of the conversation, code to copy and a
test run's results, with the HTML pieces the View and the window share.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Iterable

from .._bedrock.blocks import _Answer, _answer_html, _json_html, _plain_json
from .._bedrock.helpers import source_name
from .._bedrock.markdown import _answer_lines, _markdown_html, _with_markers
from .._bedrock.models import Passage
from .._bedrock.words import _highlight, best_snippet, question_terms
from .._kit.blocks import _Report, _draws
from .._kit.fmt import _plural
from .._kit.highlight import _json_source_html, _python_html
from .._kit.render import _MARKS, _SELECT, _TONES, _ordered, _prose, _text_lines
from .._kit.text import _clip, _esc
from .._kit.window import _HUMAN, _MACHINE

from .helpers import _number, _short
from .models import Answer, Schema
from .analysis.schema import Field
from .analysis.request import _retrieve_path
from .analysis.batches import BatchItem, _filled, _unhelpful


@dataclass
class _Turn:
    answer: Answer
    meta: str  # 'claude-sonnet-5 · 2.1s · 2 sources cited · ~$0.004'
    findings: list[tuple[str, str]] = field(default_factory=list)
    ranks: dict[int, int] = field(default_factory=dict)  # a search's ranks the answer to its question cites -> [n]


@dataclass
class _Code:
    """
    Code to copy, highlighted in HTML (one click selects all of it), as it is in text.
    """

    text: str
    title: str = ""
    lang: str = "python"  # 'python' | 'json' | 'shell'


@dataclass
class _ResultRow:
    """
    One question of a test run, as a report or the window's Test tab shows it.
    """

    number: int
    item: BatchItem
    verdict: str  # 'answered', 'unable to assist', 'found #2'...
    tone: str  # 'ok' | 'warn' | 'bad' | ''
    meta: str = ""  # who answered, how fast, cited, grounded, cost: 'Claude Opus 5 · 2.1s · ...'
    findings: list[tuple[str, str]] = field(default_factory=list)  # about this answer alone
    error: str = ""  # why it failed, with what to do about it
    change: str = ""  # since the last run: 'was unable to assist'
    better: bool = False  # the change is for the better


@dataclass
class _Results:
    """
    A test run's questions: a line each, opening to the answer (or the passages found) in full.
    """

    rows: list[_ResultRow]


def _json_text_html(value: Any) -> str:
    """
    JSON as indented text, keys, text, numbers and true / false / null in their own colours (escaped).
    """
    return _json_source_html(json.dumps(_plain_json(value), indent=2, ensure_ascii=False))


_SHELL_RE = re.compile(r"(?<![\w-])(--[\w-]+)|'(\{.*?\})'(?=\s|$)", re.S)


def _shell_html(command: str) -> str:
    """
    A shell command (the AWS CLI's): its options in colour, and the JSON it passes in single quotes highlighted as
    JSON. Every piece is escaped.
    """
    out, last = [], 0
    for m in _SHELL_RE.finditer(command):
        out.append(_esc(command[last:m.start()]))
        out.append(f'<span class="pa">{_esc(m.group(1))}</span>' if m.group(1)
                   else f"'{_json_source_html(m.group(2))}'")
        last = m.end()
    return "".join(out) + _esc(command[last:])


def _code_html(text: str, lang: str) -> str:
    """
    Code as highlighted HTML: Python, JSON or a shell command.
    """
    return {"python": _python_html, "json": _json_source_html, "shell": _shell_html}.get(lang, _esc)(text)


@_draws(_Code, "html")
def _code_block_html(block: _Code, report: _Report) -> str:
    hint = '<span class="hint">click it to select all, then copy</span>'
    return f'<h4>{_prose(block.title)}{hint}</h4><pre class="code hl"{_SELECT}>{_code_html(block.text, block.lang)}</pre>'


_draws(_Code, "text")(_text_lines)  # as text, code to copy is shown like any other text


_SHOWN_ELSEWHERE = {"source-uri", "AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_TEXT_CHUNK"}

_META_VALUE_CHARS = 300

_NO_OWN_METADATA = "None of the file's own: a <file>.metadata.json next to it adds some, for where= to filter on."


def _meta_value(value: Any) -> str:
    """
    A metadata value as text: a list joined by commas, a dict as JSON, 4.0 as 4, anything long clipped.
    """
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    return _clip(text, _META_VALUE_CHARS)


def _bedrock_keys(p: Passage) -> dict[str, Any]:
    """
    Bedrock's own metadata on a passage, less the source URI and the text it already shows. A passage read back from
    a run log written before Bedrock's keys were kept falls back to its chunk and data source IDs.
    """
    kept = p.bedrock_metadata or {"chunk-id": p.chunk_id, "data-source-id": p.data_source_id}
    return {key: value for key, value in kept.items() if value not in ("", None) and key not in _SHOWN_ELSEWHERE}


def _meta_text(p: Passage) -> str:
    """
    A passage's own metadata on one line, for a report's table: 'team=billing · year=2024', or '-'.
    """
    return " · ".join(f"{key}={_meta_value(value)}" for key, value in p.metadata.items()) or "-"


def _passage_meta_html(p: Passage) -> str:
    """
    A passage's metadata as name / value lines: the file's own (its <file>.metadata.json, what where= filters on)
    first, then Bedrock's, dimmed.
    """
    lines = [f'<span class="mk">{_esc(key)}</span><span class="mv">{_esc(_meta_value(value))}</span>'
             for key, value in p.metadata.items()]
    if not lines:
        lines.append(f'<span class="mn">{_esc(_NO_OWN_METADATA)}</span>')
    lines += [f'<span class="mk mb">{_esc(key)}</span><span class="mv mb">{_esc(_meta_value(value))}</span>'
              for key, value in _bedrock_keys(p).items()]
    return f'<div class="pmeta"><span class="pmh">Metadata</span>{"".join(lines)}</div>'


def _sources_html(a: Answer) -> str:
    """
    The cited passages under an answer: one line each, opening to the full passage, its location and its metadata
    (the file's own and Bedrock's).
    """
    terms = question_terms(a.question)
    items = []
    for i, p in enumerate(a.sources, 1):
        where = " · ".join(filter(None, [source_name(p.uri) or p.uri or "(unknown source)",
                                         f"p.{p.page}" if p.page is not None else ""]))
        body = f'<div class="pt">{_highlight(p.text, terms)}</div>'
        body += f'<div class="pm">{_esc(p.uri)}</div>' if p.uri else ""
        body += _passage_meta_html(p)
        items.append(f'<details class="src"><summary><span class="sx">{i}</span><span class="sf">{_esc(where)}</span>'
                     f'<span class="sn">{_esc(best_snippet(p.text, terms, 120))}</span></summary>{body}</details>')
    count = f" · {len(items)}" if len(items) > 1 else ""
    return f'<div class="srcs"><div class="sh">Sources{count}</div>{"".join(items)}</div>' if items else ""


_AVATAR = f'<span class="av">{_MACHINE}</span>'  # the mark before the model's name on each answer

_YOU = f'<span class="av me">{_HUMAN}</span>'

_SEARCH = '<span class="av">\U0001F50E</span>'  # and the one on a retrieve-only search's passages


def _meta_html(meta: str, avatar: str = _AVATAR) -> str:
    """
    'Claude Sonnet 5 · 2.1s · ~$0.004' as the head of an answer: the model's name in bold over the rest.
    """
    model, _, rest = meta.partition(" · ")
    return f'{avatar}<div><b>{_esc(model)}</b><div class="wm">{_esc(rest)}</div></div>'


def _score(score: float | None) -> str:
    """
    A passage's relevance score: 0.8213 -> '0.821', 0.61 -> '0.610'; None -> ''.
    """
    return "" if score is None else f"{score:.3f}"


def _search_html(a: Answer, meta: str, findings: list[tuple[str, str]], ranks: dict[int, int], *,
                 raw: bool = True) -> str:
    """
    A retrieve-only search as a chat message: every passage found, best first, each with its score (and a bar
    against the best one), the [n] the answer to the same question cites it as, and the start of its text, opening to
    all of it; then the findings and (raw=True) the request and response JSON folded at the bottom.
    """
    terms = question_terms(a.question)
    top = max((p.score for p in a.sources if p.score is not None and p.score > 0), default=None)
    items = []
    for p in a.sources:
        where = " · ".join(filter(None, [source_name(p.uri) or p.uri or "(unknown source)",
                                         f"p.{p.page}" if p.page is not None else ""]))
        score = ""
        if p.score is not None:
            share = max(0.0, min(1.0, p.score / top)) * 100 if top else 0.0
            score = (f'<span class="sc" title="Relevance score: compare it with this search\'s other scores only">'
                     f'<span class="sb"><i style="width:{share:.0f}%"></i></span>{_esc(_score(p.score))}</span>')
        n = ranks.get(p.rank)
        cited = (f'<span class="ct" title="The answer to this question cites it as [{n}]">cited [{n}]</span>'
                 if n else "")
        body = f'<div class="pt">{_highlight(p.text, terms)}</div>'
        body += f'<div class="pm">{_esc(p.uri)}</div>' if p.uri else ""
        body += _passage_meta_html(p)
        snippet = _highlight(best_snippet(p.text, terms, 220), terms)
        items.append(f'<details class="src"><summary><span class="sx">{p.rank}</span><span class="sf">{_esc(where)}'
                     f'</span>{score}{cited}<span class="sn">{snippet}</span></summary>{body}</details>')
    if items:
        head = f"{_plural(len(items), 'passage')}, best first" if len(items) > 1 else "1 passage"
        passages = f'<div class="srcs hits"><div class="sh">{_esc(head)}</div>{"".join(items)}</div>'
    else:
        passages = '<div class="ans"><p class="none">(no passages found)</p></div>'
    notes = "".join(f'<div class="note {level}">{_prose(message)}</div>' for level, message in _ordered(findings))
    json_part = ""
    if raw:
        json_part = (f'<details class="raw"><summary>Request and response JSON</summary>'
                     f'<div class="jh">Request</div>{_json_html(a.request)}'
                     f'<div class="jh">Response</div>{_json_html(a.response, open_depth=3)}</details>')
    return (f'<div class="msg bot"><div class="who">{_meta_html(meta, _SEARCH)}</div>{passages}{notes}{json_part}'
            "</div>")


def _turn_html(a: Answer, meta: str, findings: list[tuple[str, str]], *, raw: bool = True) -> str:
    """
    One answer as a chat message: who answered and how fast, the text (its markdown laid out) with cited spans
    shaded, the findings, the sources, and (raw=True) the request and response JSON folded at the bottom.
    """
    notes = "".join(f'<div class="note {level}">{_prose(message)}</div>' for level, message in _ordered(findings))
    text = _answer_html(_Answer(a.text, a.citations)) if a.text.strip() else '<p class="none">(no answer)</p>'
    json_part = ""
    if raw:
        json_part = (f'<details class="raw"><summary>Request and response JSON</summary>'
                     f'<div class="jh">Request</div>{_json_html(a.request)}'
                     f'<div class="jh">Response</div>{_json_html(a.response, open_depth=3)}</details>')
    return (f'<div class="msg bot"><div class="who">{_meta_html(meta)}</div><div class="ans">{text}</div>{notes}'
            f"{_sources_html(a)}{json_part}</div>")


def _writing_html(model: str, text: str) -> str:
    """
    An answer while it streams in: its markdown so far, with a caret where the next words go.
    """
    body, caret = _markdown_html(text), '<span class="caret"></span>'
    closing = re.search(r"(?:</(?:p|li|h[1-6]|td|th|tr|tbody|table|div|blockquote|ul|ol|code|pre)>)+$", body)
    at = closing.start() if closing else len(body)
    body = body[:at] + caret + body[at:]
    who = _meta_html(f"{model} · writing…")
    return f'<div class="msg bot"><div class="who">{who}</div><div class="ans">{body}</div></div>'


def _question_html(question: str) -> str:
    return f'<div class="ask">{_YOU}<div class="msg you">{_esc(question)}</div></div>'


def _row_stats(item: BatchItem) -> str:
    """
    The numbers on a test question's line: '92% grounded · 2 cited · 2.1s', 'best 0.812 · 0.3s'.
    """
    a = item.answer
    if a is None:
        return item.error_code
    if a.retrieve_only:
        top = max((p.score for p in a.sources if p.score is not None), default=None)
        return " · ".join(filter(None, [f"best {_score(top)}" if top is not None else "", f"{a.seconds:.1f}s"]))
    if _unhelpful(a):
        return f"{a.seconds:.1f}s"
    return f"{a.grounded_share:.0%} grounded · {len(a.cited)} cited · {a.seconds:.1f}s"


def _expected_text(item: BatchItem) -> str:
    """
    'expected 'refund-policy.pdf': cited as [1]' ('' when nothing was expected).
    """
    if not _filled(item.expected) or item.answer is None:
        return ""
    if item.answer.retrieve_only:
        where = f"found at #{item.found}" if item.found else "not among the passages found"
    else:
        where = f"cited as [{item.found}]" if item.found else "not cited"
    return f"Expected {_short(item.expected, 60)}: {where}"


def _result_html(row: _ResultRow) -> str:
    """
    A test question as a line (number, question, how it did, its numbers, what changed since the last run) that
    opens to the answer in full, as the chat shows answers: citations, sources, findings, request and response.
    """
    item, a = row.item, row.item.answer
    tone = row.tone if row.tone in _TONES else ""
    pill = f'<span class="pill{" " + tone if tone else ""}">{_esc(row.verdict)}</span>'
    change = (f'<span class="bc{" up" if row.better else " down"}" title="Since the last run">'
              f'{"↑" if row.better else "↓"} {_esc(row.change)}</span>' if row.change else "")
    summary = (f'<summary><span class="bn">{row.number}</span><span class="bt">{_esc(item.question)}</span>'
               f'<span class="bx">{change}{pill}<span class="bm">{_esc(_row_stats(item))}</span></span></summary>')
    expected = _expected_text(item)
    body = f'<div class="be">{_esc(expected)}</div>' if expected else ""
    if a is None:
        what = row.error or item.error or "Not asked: the run stopped before this question."
        body += f'<div class="note {"warn" if item.error else "info"}">{_prose(what)}</div>'
    elif a.retrieve_only:
        body += _search_html(a, row.meta, row.findings, {})
    else:
        body += _turn_html(a, row.meta, row.findings)
    return f'<details class="bq{" " + tone if tone else ""}">{summary}<div class="bb">{body}</div></details>'


def _waiting_html(number: int, question: str, sending: bool = True) -> str:
    """
    A test question still waiting for its answer, in the window.
    """
    state = "asking…" if sending else "not asked"
    spin = '<span class="spin"></span>' if sending else ""
    return (f'<div class="bq wait"><span class="bn">{number}</span><span class="bt">{_esc(question)}</span>'
            f'<span class="bx">{spin}<span class="bm">{state}</span></span></div>')


def _result_lines(row: _ResultRow) -> list[str]:
    """
    A test question as text: its number and question, how it did with who answered and how fast, the expected
    source, and the answer with its [n] markers and sources (every passage found, for a search).
    """
    item, a = row.item, row.item.answer
    change = f" ({'↑' if row.better else '↓'} {row.change})" if row.change else ""
    what = row.meta if a is not None else row.error or item.error or "the run stopped before this question"
    out = ["", f"{row.number}. {item.question}", f"   [{row.verdict}]{change} {what}"]
    expected = _expected_text(item)
    if expected:
        out.append(f"   {expected}")
    if a is not None and a.retrieve_only:
        out += [" " + line for line in _passage_lines(a, {})] or ["   (no passages found)"]
    elif a is not None:
        out += _answer_lines(_with_markers(a.text, a.citations), 100, "   ")
        out += [f"   [{i}] {p.source}" for i, p in enumerate(a.sources, 1)]
    return out


@_draws(_Results, "html")
def _results_html(block: _Results, report: _Report) -> str:
    return f'<div class="bqs">{"".join(_result_html(row) for row in block.rows)}</div>'


@_draws(_Results, "text")
def _results_lines(block: _Results, report: _Report) -> list[str]:
    return [line for row in block.rows for line in _result_lines(row)]


@_draws(_Turn, "html")
def _turn_block_html(block: _Turn, report: _Report) -> str:
    a = block.answer
    if a.retrieve_only:
        return _question_html(a.question) + _search_html(a, block.meta, block.findings, block.ranks)
    return _question_html(a.question) + _turn_html(a, block.meta, block.findings)


@_draws(_Turn, "text")
def _turn_block_lines(block: _Turn, report: _Report) -> list[str]:
    a = block.answer
    if a.retrieve_only:
        out = ["", f"You: {a.question}", f"Bedrock search ({block.meta}):"]
        out += _passage_lines(a, block.ranks) or ["  (no passages found)"]
    else:
        out = ["", f"You: {a.question}", f"Bedrock ({block.meta}):"]
        out += _answer_lines(_with_markers(a.text, a.citations), 100, "  ")
        out += [f"  [{i}] {p.source}" for i, p in enumerate(a.sources, 1)]
    return out + ["  " + _MARKS.get(level, "[i] ") + message for level, message in _ordered(block.findings)]


def _passage_lines(a: Answer, ranks: dict[int, int]) -> list[str]:
    """
    A retrieve-only search's passages as text: rank, score, source and the [n] the answer cites it as, over the
    start of its text.
    """
    terms = question_terms(a.question)
    out: list[str] = []
    for p in a.sources:
        cited = f"cited [{ranks[p.rank]}]" if p.rank in ranks else ""
        score = f"score {_score(p.score)}" if p.score is not None else ""
        out.append("  " + " · ".join(filter(None, [f"#{p.rank}", p.source, score, cited])))
        out += textwrap.wrap(f'"{best_snippet(p.text, terms, 200)}"', 100, initial_indent="     ",
                             subsequent_indent="      ")
    return out


def _kind_text(f: Field) -> str:
    """
    What a field takes, in words: 'number 0–1', 'HYBRID | SEMANTIC', 'text up to 4,000 characters'.
    """
    span = ""
    if f.low is not None and f.high is not None:
        span = f" {_number(f.low)}–{_number(f.high)}"
    if f.kind == "integer":
        return "whole number" + span
    if f.kind == "float":
        return "number" + span
    if f.kind == "boolean":
        return "true / false"
    if f.key == "reranker":
        return "cohere | amazon | a model ID or ARN"
    if f.kind == "choice":
        return " | ".join(f.choices)
    if f.kind == "list":
        return "list of text" + (f" (up to {_number(f.high)})" if f.high is not None else "")
    if f.kind == "json":
        return f"JSON {f.container}".strip()
    return "text" + (f" up to {_number(f.high)} characters" if f.high is not None else "")


def _wrap(inner: str) -> str:
    return f'<div class="kbc">{inner}</div>'


def _waiting(keys: list[str]) -> str:
    """
    ['temperature'] -> 'temperature waits', ['temperature', 'prompt'] -> 'temperature and prompt wait'.
    """
    names = ", ".join(keys[:-1]) + " and " + keys[-1] if len(keys) > 1 else "".join(keys)
    return f"{names} {'waits' if len(keys) == 1 else 'wait'}"


def _setting_marks(keys: Iterable[str], schema: Schema, retrieve_only: bool = False) -> dict[tuple[str, ...], str]:
    """
    {path in the request: 'set as <key>'} for the settings a request sends, to highlight them in its JSON.
    """
    marks = {}
    for key in keys:
        f = schema.fields.get(key)
        path = (_retrieve_path(f.path) if retrieve_only else f.path) if f is not None else None
        if path:
            marks[path] = f"set as {key}"
    return marks


_QUICK = ("n", "search_type", "filter", "reranker", "temperature", "top_p", "max_tokens", "prompt", "query_decomposition")
