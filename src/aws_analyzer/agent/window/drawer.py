"""
The ✦ Ask drawer's own pieces: the questions it offers for what an explorer window shows (focus_questions), its
Looking at line (_focus_html), and the places in the window an answer's calls point at (target_for: a finding's
LambdaView().logs(..., request_id=...) is "Run 8f5ce35b in the Logs tab", a button that opens it). The drawer itself
is the chat window, _AgentChat, made with focus_from= the explorer (AgentView._ask_drawer_), so it asks every
question with focus= what the window shows.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from ..._kit.focus import Focus
from ..._kit.text import _clip, _esc

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
    ".agc-app.agc-app.agc-drawer{height:100%;box-sizing:border-box}"
    ".agc-app.agc-app.agc-drawer>*{flex:0 0 auto}"
    ".agc-app.agc-app.agc-drawer>.agc-log{flex:1 1 auto;height:auto;min-height:220px}"
    ".agc-app.agc-app.agc-drawer .ag-head .ag-ct{max-width:100%}"
    ".agc-app .agc-places{flex-wrap:wrap;gap:6px;margin:6px 0 0 30px}"
    ".agc-app.agc-app button.agc-place{height:auto;min-height:26px;padding:2px 10px;font-size:12px;"
    "white-space:normal;text-align:left;border-radius:7px;color:var(--ag-accent);border:1px solid var(--ag-line);"
    "background:transparent}"
    ".agc-app.agc-app button.agc-place:hover:enabled{border-color:var(--ag-accent)}"
)


@dataclass(frozen=True)
class ViewCall:
    """
    A View call as an answer names it (LambdaView().logs('etl', request_id='8f5c...')), read: the View's class, the
    command, and its arguments.
    """

    view: str
    command: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


def parse_call(call: str) -> ViewCall | None:
    """
    A call like "LambdaView().logs('etl', since='24h')" read with ast, its arguments only literals (strings, numbers,
    lists...), so nothing in it ever runs. None for anything else: another shape, or an argument that isn't a literal.
    """
    try:
        tree = ast.parse(str(call).strip(), mode="eval").body
    except SyntaxError:
        return None
    is_view_command = (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Attribute)
                       and isinstance(tree.func.value, ast.Call) and isinstance(tree.func.value.func, ast.Name))
    if not is_view_command:
        return None
    try:
        args = tuple(ast.literal_eval(arg) for arg in tree.args)
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in tree.keywords if kw.arg}
    except (ValueError, TypeError, SyntaxError):
        return None
    return ViewCall(tree.func.value.func.id, tree.func.attr, args, kwargs)


@dataclass(frozen=True)
class Target:
    """
    Where an explorer window shows what a call does: a label for its button ("Run 8f5ce35b in the Logs tab") and the
    window's command that goes there, with its arguments (x.logs('etl', search='8f5c...', since='24h')).
    """

    label: str
    command: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


_SHORT_ID = 8
_LABEL_CHARS = 48
_LAMBDA_TABS = {"errors": "errors", "performance": "performance", "runs": "logs", "code": "code",
                "how_it_works": "code", "function_info": None}
_TAB_TITLES = {"errors": "Errors tab", "performance": "Performance tab", "logs": "Logs tab", "code": "Code tab"}
_S3_PATH_COMMANDS = frozenset({"ls", "tree", "find", "summary", "overview", "preview", "head", "bucket_info", "policy",
                               "file_details", "largest", "newest", "oldest", "versions", "deleted"})


def _lambda_logs_target(name: str, call: ViewCall) -> Target:
    """
    A LambdaView().logs(...) call as the Logs tab: one run by its request ID, or the logs searched for some text.
    """
    request_id = str(call.kwargs.get("request_id") or "")
    if request_id:
        return Target(f"Run {request_id[:_SHORT_ID]} in the Logs tab", "logs", (name,),
                     {"search": request_id, "since": call.kwargs.get("since") or "24h"})
    search = str(call.kwargs.get("search") or "")
    since = call.kwargs.get("since")
    kwargs = {key: value for key, value in (("search", search), ("since", since)) if value}
    label = f"{name}'s logs, searched for {_clip(search, _LABEL_CHARS)}" if search else f"{name}'s logs"
    return Target(label, "logs", (name,), kwargs)


def _lambda_target(call: ViewCall) -> Target | None:
    """
    A LambdaView call as a place in the Lambda explorer: its logs, a run, or one of a function's tabs.
    """
    if not call.args or not isinstance(call.args[0], str):
        return None
    name = call.args[0]
    if call.command == "logs":
        return _lambda_logs_target(name, call)
    if call.command not in _LAMBDA_TABS:
        return None
    tab = _LAMBDA_TABS[call.command]
    if tab is None:
        return Target(f"{name}'s Overview tab", "open", (name,))
    return Target(f"{name}'s {_TAB_TITLES[tab]}", "open", (name,), {"tab": tab})


def _s3_target(call: ViewCall) -> Target | None:
    """
    An S3View call about a path as that file or folder in the S3 explorer.
    """
    if call.command not in _S3_PATH_COMMANDS or not call.args or not isinstance(call.args[0], str):
        return None
    path = call.args[0]
    shown = path.removeprefix("s3://").rstrip("/").rsplit("/", 1)[-1] or path
    return Target(f"{_clip(shown, _LABEL_CHARS)} in the explorer", "open", (path,))


def _kb_target(call: ViewCall) -> Target | None:
    """
    A BedrockKBView call as the knowledge base explorer's Files or Search tab.
    """
    first = call.args[0] if call.args and isinstance(call.args[0], str) else ""
    if call.command in ("file", "search_file") and first:
        name = first.rstrip("/").rsplit("/", 1)[-1]
        return Target(f"{_clip(name, _LABEL_CHARS)} in the Files tab", "file", (first,))
    if call.command == "search" and first:
        return Target(f"Search: {_clip(first, _LABEL_CHARS)}", "search", (first,))
    return None


_TARGETS = {("lambda", "LambdaView"): _lambda_target, ("s3", "S3View"): _s3_target,
           ("bedrock_kb", "BedrockKBView"): _kb_target}


def target_for(call: str, service: str) -> Target | None:
    """
    Where a window of `service` ('lambda', 's3', 'bedrock_kb') shows what a View call an answer names does, as a
    button's label and the window's command; None when the call is another service's, or the window can't show it.
    """
    read = parse_call(call)
    if read is None:
        return None
    target = _TARGETS.get((service, read.view))
    return target(read) if target is not None else None
