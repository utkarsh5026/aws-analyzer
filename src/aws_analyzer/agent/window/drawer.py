"""
The ✦ Ask drawer's own pieces: the questions it offers for what an explorer window shows (focus_questions), and its
Looking at line (_focus_html). The drawer itself is the chat window, _AgentChat, made with focus_from= the explorer
(AgentView._ask_drawer_), so it asks every question with focus= what the window shows.
"""

from __future__ import annotations

from ..._kit.focus import Focus
from ..._kit.text import _esc

_MOST_QUESTIONS = 3

_QUESTIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("lambda", "functions"): ("Which of these functions need attention first?",
                              "Which of them failed in the last 24 hours, and why?"),
    ("lambda", "live"): ("Is anything stuck or close to its timeout right now?",
                         "Is {function} running normally right now?"),
    ("lambda", "function"): ("How is {function} doing, and what should I fix first?",
                             "What does {function} cost, and can it cost less?"),
    ("lambda", "logs"): ("What happened in these runs?", "Which of these runs failed, and why?"),
    ("lambda", "run"): ("What happened in this run?", "Is this run different from the ones that succeeded?"),
    ("lambda", "errors"): ("Why does {function} fail, and what should I change?",
                           "When did these errors start, and what changed then?"),
    ("lambda", "performance"): ("Why is {function} slow, and how can it be faster?",
                                "Are {function}'s memory and timeout set right?"),
    ("lambda", "code"): ("What does {file} do?", "Which AWS services does {function} call, and how?"),
    ("s3", "buckets"): ("Which buckets do I have, and how big are they?",
                        "Which buckets are public, or cost the most?"),
    ("s3", "folder"): ("What's in {folder}, and what changed lately?", "Were the files that arrived here processed?"),
    ("s3", "file"): ("What's in {file}, and is anything wrong with it?", "Did {file} trigger its Lambda?"),
    ("bedrock_kb", "knowledge_base"): ("Is {knowledge_base} up to date?", "Which files can't be searched, and why?"),
    ("bedrock_kb", "files"): ("Which of these files can't be searched, and why?",
                              "Which files changed since the last sync?"),
    ("bedrock_kb", "file"): ("Why does {file} show as {state}, and how do I fix it?", "Does a search find {file}?"),
    ("bedrock_kb", "syncs"): ("How did the recent syncs go, and why did any fail?",),
    ("bedrock_kb", "search"): ("Why did this search find these passages?",
                               "Which file should answer this, and does it?"),
}

_UNNAMED = {"function": "this function", "file": "this file", "folder": "this folder",
            "knowledge_base": "this knowledge base", "state": "it does"}

_FIRST_INSTEAD = {
    ("lambda", "run", "error"): "Why did this run fail, and what should I change?",
    ("bedrock_kb", "file", "indexed"): "What questions does {file} answer?",
}


class _Names(dict):
    """
    A focus's parts by key, as a chip reads them, for str.format_map: a part the focus hasn't got reads as 'this
    function', 'this file', ...
    """

    def __missing__(self, key: str) -> str:
        return _UNNAMED.get(key, "this")


def _first_instead(focus: Focus) -> str:
    """
    The question that leads instead of the usual first one, when the focus says it should: a run that failed is
    asked why, a file that's indexed what it answers. '' when the usual one stands.
    """
    if focus.value("error"):
        return _FIRST_INSTEAD.get((focus.service, focus.kind, "error"), "")
    return _FIRST_INSTEAD.get((focus.service, focus.kind, focus.value("state")), "")


def focus_questions(focus: Focus | None) -> list[str]:
    """
    Questions worth asking about what an explorer window shows, worked out by rules from its focus, so offering them
    costs nothing: on a function's Errors tab "Why does orders-etl fail, and what should I change?", on a failed run
    "Why did this run fail, and what should I change?", on a file that failed to index "Why does scanned.pdf show as
    Failed, and how do I fix it?". At most three; none without a focus or for a window it doesn't know.
    """
    if focus is None:
        return []
    templates = list(_QUESTIONS.get((focus.service, focus.kind), ()))
    first = _first_instead(focus)
    if first and templates:
        templates[0] = first
    names = _Names({part.key: part.text for part in focus.parts})
    return [template.format_map(names) for template in templates[:_MOST_QUESTIONS]]


def _focus_html(focus: Focus | None, *, used: bool) -> str:
    """
    The drawer's Looking at line: what the window shows, as chips, which every question is asked about; or that
    questions are about the whole account (unticked, or nothing to look at yet).
    """
    if focus is None or not focus.parts:
        return '<div class="ag-focus ag-dim">Nothing open yet: questions are about your whole account.</div>'
    chips = "".join(f'<span class="ag-chip" title="{_esc(part.value)}">{_esc(part.text)}</span>'
                    for part in focus.parts)
    if not used:
        return (f'<div class="ag-focus ag-off"><span class="ag-k">not using</span>{chips}'
                '<span class="ag-dim">questions are about your whole account</span></div>')
    return f'<div class="ag-focus"><span class="ag-k">looking at</span>{chips}</div>'


_DRAWER_CSS = (
    ".agc-app .ag-focus{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:12px;line-height:1.5}"
    ".agc-app .ag-focus .ag-k{margin-right:4px}"
    ".agc-app .ag-chip{border:1px solid var(--ag-line,#d0d4dc);border-radius:6px;padding:0 6px;white-space:nowrap;"
    "max-width:100%;overflow:hidden;text-overflow:ellipsis}"
    ".agc-app .ag-off .ag-chip{opacity:.5;text-decoration:line-through}"
    ".agc-app .agc-focus{padding:6px 10px;border-bottom:1px solid var(--ag-line,#d0d4dc);gap:6px}"
    ".agc-app .ag-about{display:block;font-size:11px;opacity:.7;margin-top:2px}"
)
