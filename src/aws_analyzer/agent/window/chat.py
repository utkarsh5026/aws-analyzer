"""
The agent's chat window (AgentView.chat()): a conversation with the agent in a notebook, drawn like a terminal, where
each question shows the agent's work as it happens. Under the `›` question come its plan, when it writes one (a
checklist that ticks off as it works, kept under the question), the model's words before it acts (`●`), and each
tool call as the code it is, in colour, with a badge for every AWS service it reads, a comment saying
what it does in plain English, and what it found under it (`⎿`); a click opens the AWS operations it used, what it
covered, the View calls to look closer and the data it handed the model. Once it has answered, the steps fold into
one line with the services they read under the plan as it left it, and the answer leads, with the findings that matter and the details under it.

A service's badge (_SERVICES) is its colour and a line drawing, the same wherever it shows (tool calls, the folded
steps, the header, the empty conversation's legend), so a step's services can be told apart at a glance however many
toolkits or agents take part; a service it doesn't know shows the start of its name.

Before a big read (a deep S3 listing, many days of logs, a large save) the agent stops to ask: the call's line says
it's waiting, and a box under it says what it would read with Go, Smaller and Skip, which resume the question with that
choice (Stop skips it and has the agent answer).

The question runs on a worker thread, so the window keeps answering clicks (Stop) while the agent works. A question
typed meanwhile waits under it, marked queued with a ✕ to drop it, and is asked once the one before is answered (or
stopped), in the order they were typed. The agent
tells its steps (AgentStep) from whichever thread they happen on, into a queue the window drains on the kernel's
event loop, where the widgets are drawn. Without a running loop (a script, the tests) the question runs right away
and the steps are drawn when it's done.

The head names the open chat (its title: its first question, or the one it was renamed to). Its 🗂 chats, ⚙ model
and 🧠 memory open their panels under it, one at a time (chats_panel.py, model_panel.py, memory_panel.py): with
chats=True, 🗂 chats lists the saved chats, and a click opens one here to carry on.

Each step is a widget of its own while the agent works, so a line that changes redraws only itself and a line someone
opened stays open. Plain widgets and CSS, no JavaScript, on the windows' kit (_Window). Widgets live in the kernel: a
reopened notebook loses the window, and transcript() shows the conversation as a report that stays.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable

from ..._bedrock.markdown import _markdown_html
from ..._bedrock.prices import short_model
from ..._kit.blocks import _Findings, _Note
from ..._kit.fmt import _duration, _plural, human_money, human_size
from ..._kit.highlight import _python_html
from ..._kit.jsontree import _json_tree_css, _JsonTree
from ..._kit.render import _blocks_html, _copyable, _prose
from ..._kit.text import _clip, _esc
from ..._kit.window import _class_if, _css_height, _icon_url, _row_button, _running_loop, _terminal_theme, _Window, _HUMAN, _MACHINE
from ..answers import AgentAnswer, AgentStep, PlanStep, ToolCall, services_of
from ..llm import provider_info
from ..view import _CSS, _ROOT, _Charts, _coverage_text, _Plan
from .chats_panel import _CHATS_CSS, _ChatsPanel
from .memory_panel import _MEMORY_CSS, _MemoryPanel
from .model_panel import _PANEL_CSS, _ModelPanel, shown_model

if TYPE_CHECKING:
    from ..view import AgentView

_DRAW_EVERY = 0.15
_SEND_HINT = "enter to send · stop ends a question with what it found · read-only"
_QUEUE_HELD = ("The questions you queued wait, since this one went wrong: press Enter in the empty box to ask the "
               "next, or ✕ to drop it.")
_QUEUE_HINT = "enter queues a question for after this one · stop ends this one with what it found · read-only"
_TARGET_CHARACTERS = 48
_DATA_ITEMS = 8


@dataclass(frozen=True)
class _Service:
    """
    How an AWS service shows in the window: its name in full (the badge's tooltip), its short name, and its badge,
    a line drawing (a _DRAWINGS key) in white on its colour, after AWS's colour for the service's category.
    """

    title: str
    short: str
    color: str
    drawing: str


_COMPUTE, _STORAGE, _MANAGEMENT, _SECURITY = "#ED7100", "#7AA116", "#E7157B", "#DD344C"
_DATABASE, _MACHINE_LEARNING, _ANALYTICS = "#C925D1", "#01A88D", "#8C4FFF"
_SERVICES = {
    "lambda": _Service("AWS Lambda", "lambda", _COMPUTE, "lambda"),
    "ec2": _Service("Amazon EC2", "ec2", _COMPUTE, "chip"),
    "logs": _Service("Amazon CloudWatch Logs", "logs", _MANAGEMENT, "logs"),
    "cloudwatch": _Service("Amazon CloudWatch", "cloudwatch", _MANAGEMENT, "chart"),
    "s3": _Service("Amazon S3", "s3", _STORAGE, "bucket"),
    "s3control": _Service("Amazon S3 Control", "s3", _STORAGE, "bucket"),
    "sts": _Service("AWS STS", "sts", _SECURITY, "key"),
    "iam": _Service("AWS IAM", "iam", _SECURITY, "key"),
    "dynamodb": _Service("Amazon DynamoDB", "dynamodb", _DATABASE, "table"),
    "bedrock": _Service("Amazon Bedrock", "bedrock", _MACHINE_LEARNING, "spark"),
    "bedrock-runtime": _Service("Amazon Bedrock", "bedrock", _MACHINE_LEARNING, "spark"),
    "bedrock-agent": _Service("Amazon Bedrock", "bedrock", _MACHINE_LEARNING, "spark"),
    "bedrock-agent-runtime": _Service("Amazon Bedrock", "bedrock", _MACHINE_LEARNING, "spark"),
    "sagemaker": _Service("Amazon SageMaker", "sagemaker", _MACHINE_LEARNING, "hex"),
    "es": _Service("Amazon OpenSearch Service", "opensearch", _ANALYTICS, "search"),
    "aoss": _Service("Amazon OpenSearch Serverless", "opensearch", _ANALYTICS, "search"),
}

_DRAWINGS = {
    "lambda": "<path d='M5 20l6.5-10.5'/><path d='M7 4h3.5L18 20'/>",
    "chip": "<rect x='7' y='7' width='10' height='10' rx='1'/><path d='M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17"
            " 10h4M17 14h4'/>",
    "logs": "<path d='M5 6h14'/><path d='M5 10h14'/><path d='M5 14h10'/><path d='M5 18h7'/>",
    "chart": "<path d='M4 19h16'/><path d='M5 15l4-5 4 3 6-7'/>",
    "bucket": "<path d='M4 6l2 14h12l2-14'/><path d='M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z'/>",
    "key": "<circle cx='8' cy='15' r='4'/><path d='M11 12l9-9'/><path d='M17 6l3 3'/>",
    "table": "<rect x='4' y='4' width='16' height='16' rx='2'/><path d='M4 10h16M10 4v16'/>",
    "spark": "<path d='M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z'/>",
    "hex": "<path d='M12 3l8 4.5v9L12 21l-8-4.5v-9z'/>",
    "search": "<circle cx='11' cy='11' r='6'/><path d='M20 20l-4.5-4.5'/>",
}


@dataclass(frozen=True)
class _Suggestion:
    """
    A question the empty conversation offers, for the toolkits it needs, with the services answering it reads.
    """

    needs: frozenset[str]
    question: str
    services: tuple[str, ...]


def _and_list(names: list[str]) -> str:
    """
    ['Lambda', 'S3', 'Bedrock Knowledge Bases'] -> 'Lambda, S3 and Bedrock Knowledge Bases'.
    """
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


_SUGGESTIONS = (
    _Suggestion(frozenset({"lambda"}), "Which Lambda failed last night?", ("lambda", "cloudwatch", "logs")),
    _Suggestion(frozenset({"lambda"}), "Which functions are slow or close to their timeout?",
                ("lambda", "cloudwatch", "logs")),
    _Suggestion(frozenset({"s3"}), "Which buckets do I have, and how big are they?", ("s3", "cloudwatch")),
    _Suggestion(frozenset({"s3", "lambda"}), "Did last night's uploads trigger their Lambda?",
                ("s3", "lambda", "cloudwatch")),
    _Suggestion(frozenset({"s3", "python"}), "Chart how big each of my buckets is", ("s3", "cloudwatch")),
    _Suggestion(frozenset({"bedrock_kb"}), "Are my knowledge bases up to date?", ("bedrock", "s3")),
)


@dataclass(frozen=True)
class _ToolWords:
    """
    What a tool call does, for the comment beside it: while it runs and once it's done, with {t} standing for what
    it's about (the function, bucket or folder), and what to say when the call names none.
    """

    running: str
    done: str
    nothing: str = ""


_TOOL_WORDS = {
    "find_functions": _ToolWords("finding functions", "found functions"),
    "function_health": _ToolWords("checking every function's health", "checked every function's health"),
    "function_errors": _ToolWords("reading {t}'s errors", "read {t}'s errors", "the function"),
    "function_logs": _ToolWords("reading {t}'s logs", "read {t}'s logs", "the function"),
    "function_performance": _ToolWords("timing {t}'s runs", "timed {t}'s runs", "the function"),
    "function_details": _ToolWords("reading {t}'s settings", "read {t}'s settings", "the function"),
    "function_code": _ToolWords("reading {t}'s code", "read {t}'s code", "the function"),
    "function_source": _ToolWords("reading {t}'s source", "read {t}'s source", "the function"),
    "search_code": _ToolWords("searching {t}'s code", "searched {t}'s code", "the function"),
    "find_symbol": _ToolWords("looking up a name in {t}'s code", "looked up a name in {t}'s code", "the function"),
    "find_buckets": _ToolWords("finding buckets", "found buckets"),
    "bucket_details": _ToolWords("reading {t}'s settings", "read {t}'s settings", "the bucket"),
    "list_folder": _ToolWords("listing {t}", "listed {t}", "a folder"),
    "find_files": _ToolWords("finding files in {t}", "found files in {t}", "a bucket"),
    "folder_summary": _ToolWords("summing up {t}", "summed up {t}", "a folder"),
    "file_info": _ToolWords("reading {t}", "read {t}", "a file"),
    "preview_file": _ToolWords("reading what's in {t}", "read what's in {t}", "a file"),
    "s3_triggers": _ToolWords("checking what {t} triggers", "checked what {t} triggers", "each bucket"),
    "find_knowledge_bases": _ToolWords("finding knowledge bases", "found knowledge bases"),
    "kb_health": _ToolWords("checking {t}'s health", "checked {t}'s health", "the knowledge base"),
    "kb_syncs": _ToolWords("reading {t}'s syncs", "read {t}'s syncs", "the knowledge base"),
    "kb_files": _ToolWords("checking {t}'s files", "checked {t}'s files", "the knowledge base"),
    "search_kb": _ToolWords("searching the knowledge base for '{t}'", "searched the knowledge base for '{t}'",
                            "a question"),
    "file_chunks": _ToolWords("reading {t}'s chunks", "read {t}'s chunks", "a file"),
    "answer_kb": _ToolWords("asking the knowledge base '{t}'", "asked the knowledge base '{t}'", "a question"),
    "save_function_code": _ToolWords("saving {t}'s code to the workspace", "saved {t}'s code to the workspace",
                                     "the function"),
    "save_logs": _ToolWords("saving {t}'s logs to the workspace", "saved {t}'s logs to the workspace", "the function"),
    "save_s3_files": _ToolWords("saving {t} to the workspace", "saved {t} to the workspace", "files"),
    "read_runbook": _ToolWords("reading the runbook {t}", "read the runbook {t}", "a runbook"),
    "run_python": _ToolWords("running Python", "ran Python"),
    "workspace_files": _ToolWords("listing the workspace", "listed the workspace"),
    "show_chart": _ToolWords("putting {t} in the answer", "put {t} in the answer", "a chart"),
    "ls": _ToolWords("listing {t}", "listed {t}", "the workspace"),
    "read_file": _ToolWords("reading {t}", "read {t}", "a file"),
    "write_file": _ToolWords("writing {t}", "wrote {t}", "a file"),
    "edit_file": _ToolWords("editing {t}", "edited {t}", "a file"),
    "glob": _ToolWords("finding files like {t}", "found files like {t}", "a pattern"),
    "grep": _ToolWords("searching the workspace for {t}", "searched the workspace for {t}", "a text"),
    "delete": _ToolWords("deleting {t}", "deleted {t}", "a file"),
}
_TARGET_ARGS = ("function", "bucket", "location", "path", "uri", "key", "file", "knowledge_base")
_STATE_MARKS = {"ok": "✓", "err": "✗", "off": "–"}
_CHOICE_BUTTONS = (("go", "go ▸", "Read all of it"), ("smaller", "smaller", "Read the smaller part instead"),
                   ("skip", "skip", "Answer without it"))
_LIMIT_BUTTONS = (("go", "raise ▸", "Raise the limit for this question and go on"),
                  ("smaller", "answer now", "Answer with what it found"))

_THEME_CSS = _terminal_theme((".agc-app",), "ag") + """
@keyframes ag-spin{to{transform:rotate(360deg)}}
@keyframes ag-shine{to{background-position:-200% 0}}"""

_FRAME_CSS = """\
.agc-app.agc-app{font-family:var(--ag-mono);font-size:13px;border:1px solid var(--ag-line);border-radius:10px;background:var(--ag-bg);overflow:hidden}
.agc-app.agc-app .agt{font-family:var(--ag-mono);font-size:13px;line-height:1.6}
.agc-app.agc-app .jupyter-button,.agc-app.agc-app input{font-family:var(--ag-mono)}
.agc-app .ag-dim{color:var(--ag-dim);opacity:.75}
.agc-app .ag-pr{color:var(--ag-prompt);font-weight:700}
.agc-app .ag-k{color:var(--ag-comment)}
.agc-app .ag-svc{position:relative;display:inline-block;flex:0 0 auto;width:16px;height:16px;border-radius:4px;background:var(--ag-c);vertical-align:-3px;color:#fff;font-size:9px;font-weight:700;line-height:16px;text-align:center;text-transform:uppercase}
.agc-app .ag-svc::after{content:"";position:absolute;inset:2px;background:#fff;-webkit-mask:var(--ag-d) center/contain no-repeat;mask:var(--ag-d) center/contain no-repeat}
.agc-app .ag-svc.ag-s-other{--ag-c:#6e7781}
.agc-app .ag-svc.ag-s-other::after{content:none}
.agc-app .ag-svcs{display:inline-flex;gap:3px;flex:0 0 auto}
.agc-app .ag-named{display:inline-flex;align-items:center;gap:5px;margin-right:12px;white-space:nowrap}
.agc-app.agc-app .agc-head{align-items:center;gap:10px;padding:9px 12px 9px 16px;border-bottom:1px solid var(--ag-line)}
.agc-app .ag-head{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;min-width:0}
.agc-app .ag-head b{font-weight:700}
.agc-app .ag-head .ag-ct{font-weight:600;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agc-app .ag-ro{color:var(--ag-ok)}
.agc-app.agc-app .agc-new,.agc-app.agc-app .agc-send{height:26px;line-height:24px;padding:0 10px;margin:0;border-radius:6px;font-size:12px;background:transparent;border:1px solid var(--ag-line);box-shadow:none;color:inherit}
.agc-app.agc-app .agc-new:hover:enabled,.agc-app.agc-app .agc-send:hover:enabled{border-color:var(--ag-accent);color:var(--ag-accent)}
.agc-app.agc-app .agc-send.agc-stop{border-color:var(--ag-bad);color:var(--ag-bad)}
.agc-app.agc-app .agc-send:disabled{opacity:.5}
.agc-app.agc-app .agc-ask{flex-wrap:wrap;align-items:center;gap:8px;margin:4px 0 2px 26px;padding:8px 12px;border:1px solid var(--ag-warn);border-radius:6px}
.agc-app.agc-app .agc-ask>.widget-html,.agc-app.agc-app .agc-ask>.jupyter-widget-html{flex:1 1 100%;margin:0}
.agc-app.agc-app .agc-choice{height:26px;line-height:24px;padding:0 12px;margin:0;border-radius:6px;font-size:12px;background:transparent;border:1px solid var(--ag-line);box-shadow:none;color:inherit}
.agc-app.agc-app .agc-choice:hover:enabled{border-color:var(--ag-accent);color:var(--ag-accent)}
.agc-app.agc-app .agc-choice.agc-go{border-color:var(--ag-accent);color:var(--ag-accent);font-weight:700}
.agc-app.agc-app .agc-queued{gap:6px}
.agc-app.agc-app .agc-queued>.widget-html,.agc-app.agc-app .agc-queued>.jupyter-widget-html{flex:1 1 auto;margin:0}
.agc-app.agc-app .agc-drop{height:22px;line-height:20px;padding:0 7px;margin:10px 0 0;border-radius:6px;font-size:11px;background:transparent;border:1px solid var(--ag-line);box-shadow:none;color:inherit;opacity:.7}
.agc-app.agc-app .agc-drop:hover:enabled{border-color:var(--ag-bad);color:var(--ag-bad);opacity:1}
.agc-app.agc-app .agc-log{height:clamp(460px,calc(100vh - 320px),1400px);padding:4px 16px 12px}
body[class*=vscode-] .agc-app.agc-app .agc-log{height:520px}
.agc-app.agc-app .agc-log>*,.agc-app.agc-app .agc-turn>*,.agc-app.agc-app .agc-empty>*{margin:0}
.agc-app.agc-app .agc-composer{align-items:center;gap:8px;padding:6px 10px 6px 16px;border-top:1px solid var(--ag-line)}
.agc-app.agc-app .agc-composer .widget-text,.agc-app.agc-app .agc-composer .jupyter-widget-text{margin:0}
.agc-app.agc-app .agc-composer input{border:0;box-shadow:none;background:transparent;font-size:13.5px;padding:4px 2px;height:30px;color:inherit}
.agc-app.agc-app .agc-composer .widget-html,.agc-app.agc-app .agc-composer .jupyter-widget-html{margin:0}
.agc-app .ag-hint{display:flex;gap:12px;justify-content:space-between;padding:0 16px 8px;font-size:11.5px;color:var(--ag-dim);opacity:.75}
.agc-app .ag-hint.warn{color:var(--ag-warn);opacity:1}
.agc-app .ag-hint.ok{color:var(--ag-ok);opacity:1}
.agc-app.agc-app .agc-row{position:relative;overflow:visible}
.agc-app.agc-app .agc-row>.agc-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:6px;background:transparent;box-shadow:none}
.agc-app.agc-app .agc-row>.agc-row-b:hover:enabled{background:var(--ag-hover)}
.agc-app.agc-app .agc-row>.agc-row-b:active:enabled{transform:none}
.agc-app.agc-app .agc-row>.agc-row-f{position:relative;z-index:1;pointer-events:none;margin:0;width:100%}
.agc-app .ag-intro{padding:14px 0 6px}
.agc-app .ag-intro .ag-big{font-size:16px;font-weight:700}
.agc-app .ag-intro p{margin:6px 0 14px;max-width:760px}
.agc-app .ag-intro .ag-label{margin-right:12px}
.agc-app .ag-intro .ag-legend{display:flex;flex-wrap:wrap;align-items:center;gap:6px 0;margin-bottom:14px}
.agc-app .ag-sg{display:flex;align-items:center;gap:10px;padding:5px 8px;white-space:normal}
.agc-app .ag-sg .ag-svcs{margin-left:auto}"""

_CONVERSATION_CSS = """\
.agt .pk{color:var(--ag-k)}
.agt .pf{color:var(--ag-f)}
.agt .pa{color:var(--ag-a)}
.agt .pc{color:var(--ag-comment);font-style:italic}
.agt .js{color:var(--ag-s)}
.agt .jn{color:var(--ag-n)}
.agt .jl{color:var(--ag-l)}
.agt .jk{color:var(--ag-a)}
.agt .ag-q{display:flex;flex-direction:row-reverse;align-items:flex-start;gap:10px;margin:18px 0 8px;font-weight:600}
.agt .ag-q>.ag-said{max-width:min(680px,80%);padding:7px 12px;border:1px solid var(--ag-line);border-radius:6px;background:var(--ag-hover);white-space:pre-wrap;overflow-wrap:anywhere}
.agt .ag-q>.ag-mark{margin-top:6px}
.agt .ag-q.ag-queued>.ag-said{border-style:dashed;opacity:.65;font-weight:500}
.agt .ag-q>.ag-wait{align-self:center;font-size:11px;font-weight:400;opacity:.6}
.agt .ag-say{display:flex;gap:10px;margin:6px 0 2px}
.agt .ag-say>.ag-bul{color:var(--ag-comment)}
.agt .ag-say .ans{margin:0;font-size:13px;color:var(--ag-dim)}
.agt .ag-step{margin:6px 0 2px}
.agt .ag-step>details>summary{list-style:none;cursor:pointer;display:block;margin:0 -8px;padding:1px 8px;border-radius:6px}
.agt .ag-step>details>summary::-webkit-details-marker{display:none}
.agt .ag-step>details>summary:hover{background:var(--ag-hover)}
.agt .ag-tl{display:flex;align-items:center;gap:10px;min-width:0}
.agt .ag-call{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.agt .ag-cm{color:var(--ag-comment);font-style:italic;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.agt .ag-run .ag-cm{background:linear-gradient(90deg,var(--ag-comment) 0%,var(--ag-accent) 50%,var(--ag-comment) 100%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:ag-shine 1.6s linear infinite}
.agt .ag-tm{margin-left:auto;flex:0 0 auto;font-size:12px;color:var(--ag-dim);opacity:.8;white-space:nowrap}
.agt .ag-ok .ag-tm b{color:var(--ag-ok)}
.agt .ag-err .ag-tm b{color:var(--ag-bad)}
.agt .ag-off .ag-tm b{color:var(--ag-warn)}
.agt .ag-ask .ag-tm{color:var(--ag-warn);opacity:1}
.agt .ag-asked{font-size:12.5px;line-height:1.55}
.agt .ag-asked b{color:var(--ag-warn)}
.agt .ag-asked .ag-k{display:block;margin-top:2px}
.agt .ag-spin{display:inline-block;color:var(--ag-accent);animation:ag-spin 1s linear infinite}
.agt .ag-res{display:flex;gap:8px;padding-left:4px;color:var(--ag-dim)}
.agt .ag-code{margin:3px 0 2px 26px;padding:4px 0 4px 12px;border-left:1px solid var(--ag-line);font-size:12px;white-space:pre;overflow-x:auto;max-height:240px;overflow-y:auto}
.agt .ag-res>.ag-el{color:var(--ag-comment);flex:0 0 auto}
.agt .ag-err .ag-res{color:var(--ag-bad)}
.agt .ag-off .ag-res{color:var(--ag-warn)}
.agt .ag-detail{margin:4px 0 6px 26px;padding:6px 0 6px 12px;border-left:1px solid var(--ag-line);font-size:12px}
.agt .ag-detail>div{display:flex;gap:10px;margin:2px 0}
.agt .ag-detail .ag-k{flex:0 0 64px}
.agt .ag-detail .ag-ops{color:var(--ag-dim);overflow-wrap:anywhere}
.agt .ag-detail code{background:none;padding:0;font-size:12px}
.agt .ag-data>summary{cursor:pointer;color:var(--ag-accent);list-style:none;margin-top:4px}
.agt .ag-data>summary::-webkit-details-marker{display:none}
.agt .ag-data>summary::before{content:"▸ "}
.agt .ag-data[open]>summary::before{content:"▾ "}
.agt .ag-data .jt{margin-top:4px;background:transparent}
.agt .ag-data .jt,.agt .ag-data .jt *{font-family:var(--ag-mono)}
.agt .ag-pulse{display:flex;align-items:center;gap:8px;margin:8px 0 2px}
.agt .ag-pulse .ag-star{display:inline-block;color:var(--ag-accent);animation:ag-spin 2.4s linear infinite}
.agt .ag-think{font-weight:600;background:linear-gradient(90deg,var(--ag-accent) 0%,var(--ag-prompt) 50%,var(--ag-accent) 100%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:ag-shine 1.6s linear infinite}
.agt .ag-trail{margin:6px 0 2px}
.agt .ag-trail>summary{list-style:none;cursor:pointer;display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px;width:fit-content;max-width:100%;margin:0 -8px;padding:1px 8px;border-radius:6px;color:var(--ag-dim)}
.agt .ag-trail>summary::-webkit-details-marker{display:none}
.agt .ag-trail>summary:hover{background:var(--ag-hover)}
.agt .ag-trail>summary .ag-car{color:var(--ag-comment);transition:transform .15s}
.agt .ag-trail[open]>summary .ag-car{transform:rotate(90deg)}
.agt .ag-trail>summary .ag-bad{color:var(--ag-bad)}
.agt .ag-trail>.ag-steps{margin:2px 0 6px 6px;padding-left:12px;border-left:1px solid var(--ag-line)}
.agt .ag-mark{flex:0 0 auto;font-size:15px;line-height:1.5}
.agt .ag-ans{display:flex;gap:10px;margin:8px 0 2px}
.agt .ag-ans .ans{font-size:13px;line-height:1.65;margin:0;padding-left:12px;border-left:2px solid var(--ag-accent);min-width:0}
.agt .ag-ans .ans code{background:var(--ag-hover);padding:1px 4px;border-radius:4px;font-size:12.5px}
.agt .ag-plan .pln{margin:8px 0 4px;padding:6px 12px;border:1px solid var(--ag-line);border-radius:6px;width:fit-content;max-width:100%}
.agt .ag-plan .pln-h{color:var(--ag-comment);opacity:1}
.agt .ag-plan .pln .pln-done .pln-m{color:var(--ag-ok)}
.agt .ag-plan .pln .pln-now .pln-m{color:var(--ag-accent)}
.agt .ag-charts{margin:10px 0 2px;padding-left:36px}
.agt .ag-charts .cht{margin:0}
.agt .ag-fd .fd{margin:10px 0 4px}
.agt .ag-fail{margin:8px 0;padding-left:14px;border-left:2px solid var(--ag-bad);color:var(--ag-bad)}
.agt .ag-foot{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 14px;margin:8px 0 4px;font-size:12px}
.agt .ag-more>summary{list-style:none;cursor:pointer;color:var(--ag-accent)}
.agt .ag-more>summary::-webkit-details-marker{display:none}
.agt .ag-more>summary::before{content:"▸ "}
.agt .ag-more[open]>summary::before{content:"▾ "}
.agt .ag-more[open]{flex:1 1 100%}
.agt .ag-more>.ag-box{margin-top:6px;padding:2px 12px 6px;border-left:1px solid var(--ag-line)}"""


def _service_rules() -> str:
    """
    Each service's badge colour and drawing (.ag-s-lambda, ...), from _SERVICES.
    """
    return "\n".join(f".agc-app .ag-s-{code}{{--ag-c:{service.color};--ag-d:{_icon_url(_DRAWINGS[service.drawing])}}}"
                     for code, service in _SERVICES.items())


def _window_css() -> str:
    return "\n".join([_THEME_CSS, _service_rules(), _FRAME_CSS, _CONVERSATION_CSS, _PANEL_CSS, _MEMORY_CSS,
                      _CHATS_CSS, _json_tree_css(_ROOT)])


def _wrap(inner: str) -> str:
    """
    A piece of the conversation in a box of the reports' root class, so the report stylesheet's rules apply.
    """
    return f'<div class="{_ROOT}">{inner}</div>'


def _badge(code: str) -> str:
    """
    A service's badge (its tooltip, the service's name); a service the window doesn't know shows the start of its
    name on grey.
    """
    service = _SERVICES.get(code)
    if service is None:
        return f'<span class="ag-svc ag-s-other" title="{_esc(code)}">{_esc(code[:2])}</span>'
    return f'<span class="ag-svc ag-s-{code}" title="{_esc(service.title)}"></span>'


def _badges(codes: Iterable[str]) -> str:
    return f'<span class="ag-svcs">{"".join(_badge(code) for code in codes)}</span>'


def _named_badges(codes: Iterable[str]) -> str:
    """
    Services as their badges with their short names beside them, each service once ('lambda', 'logs', ...).
    """
    names = {code: _SERVICES[code].short if code in _SERVICES else code for code in codes}
    shown = dict.fromkeys(names.values())
    first = {name: next(code for code, short in names.items() if short == name) for name in shown}
    return "".join(f'<span class="ag-named">{_badge(code)}{_esc(name)}</span>' for name, code in first.items())


def _answer_text_html(text: str) -> str:
    """
    The model's markdown as HTML, the way reports draw an answer (every piece of it escaped).
    """
    return f'<div class="ans">{_markdown_html(text)}</div>'


def _question_html(question: str) -> str:
    """
    A question on the right, in a box, with the person's mark beside it.
    """
    return f'<div class="ag-q"><span class="ag-mark">{_HUMAN}</span><span class="ag-said">{_esc(question)}</span></div>'


def _thinking_html(text: str) -> str:
    """
    What the model said before it called tools: a grey bullet, then its words.
    """
    return f'<div class="ag-say"><span class="ag-bul">●</span>{_answer_text_html(text)}</div>'


def _queued_html(question: str) -> str:
    """
    A question that waits for the one being answered: on the right like any question, dimmed and marked queued.
    """
    return (f'<div class="ag-q ag-queued"><span class="ag-mark">{_HUMAN}</span><span class="ag-said">'
            f'{_esc(question)}</span><span class="ag-wait">queued</span></div>')


def _switch_html(model: str) -> str:
    """
    The line across the conversation where the agent switched model: a new conversation starts under it.
    """
    return f'<div class="ag-switch"><span>now answering with {_esc(model)} · a new conversation</span></div>'


def _target(call: ToolCall) -> str | None:
    """
    What a tool call is about, for its comment: the function, bucket or folder it was asked for.
    """
    names = (call.first, *_TARGET_ARGS) if call.first else _TARGET_ARGS
    for name in names:
        value = call.args.get(name)
        if value not in (None, ""):
            return _clip(str(value), _TARGET_CHARACTERS)
    return None


def _tool_words(call: ToolCall, *, running: bool) -> str:
    """
    What a tool call does, in plain English: "reading orders-etl's errors" while it runs, "read orders-etl's
    errors" once it's done. A tool the window doesn't know reads as its name.
    """
    words = _TOOL_WORDS.get(call.name)
    if words is None:
        return call.name.replace("_", " ")
    template = words.running if running else words.done
    return template.replace("{t}", _target(call) or words.nothing)


def _tool_state(call: ToolCall, *, running: bool) -> str:
    """
    A tool call's step as it stands: 'run' while it runs, 'off' when it wasn't run, 'err' when it couldn't read
    everything, else 'ok'.
    """
    if running:
        return "run"
    if call.refused:
        return "off"
    if call.error:
        return "err"
    return "ok"


def _data_html(result: dict[str, Any] | None) -> str:
    """
    The data a tool handed the model, folded under how big it is: a JSON tree to open.
    """
    if not result:
        return ""
    size = human_size(len(json.dumps(result, default=str).encode()))
    tree = _blocks_html([_JsonTree(result, items=_DATA_ITEMS, name="result")], root=_ROOT)
    return f'<details class="ag-data"><summary>data the model read · {size}</summary>{tree}</details>'


def _tool_detail_html(call: ToolCall) -> str:
    """
    What opens under a finished tool call: the AWS operations it reads with, what it covered, why it failed, the
    View calls to look closer, and the data it handed the model.
    """
    parts = []
    operations = [read for read in call.reads if read.split(":", 1)[0] in call.services]
    if operations:
        parts.append(f'<div><span class="ag-k">reads</span><span class="ag-ops">{_esc(", ".join(operations))}'
                     "</span></div>")
    covered = _coverage_text(call)
    if covered != "-":
        parts.append(f'<div><span class="ag-k">covered</span><span>{_esc(covered)}</span></div>')
    if call.error and call.summary and call.summary != call.error:
        parts.append(f'<div><span class="ag-k">error</span><span>{_esc(call.error)}</span></div>')
    for command, what in call.next:
        parts.append(f'<div><span class="ag-k">next</span><span><code>{_python_html(command)}</code> '
                     f'<span class="ag-k"># {_esc(what)}</span></span></div>')
    return "".join(parts) + _data_html(call.result)


def _tool_html(call: ToolCall, *, running: bool) -> str:
    """
    One tool call: the badges of the services it reads, the call as the model made it in colour, a comment saying
    what it does, its time, the code it ran (run_python's) and what it found under it. A finished call opens to its
    details; one waiting for the user's choice on a big read says so.
    """
    state = "ask" if call.waiting is not None and not running else _tool_state(call, running=running)
    if state == "ask":
        timing = "✋ waiting for you"
    elif running:
        timing = '<span class="ag-spin">◐</span>'
    else:
        timing = f"<b>{_STATE_MARKS[state]}</b> {call.seconds:.1f}s"
    line = (f'<div class="ag-tl">{_badges(call.services)}<span class="ag-call">{_python_html(call.brief)}</span>'
            f'<span class="ag-cm"># {_esc(_tool_words(call, running=running or state == "ask"))}</span>'
            f'<span class="ag-tm">{timing}</span></div>')
    if call.code:
        line += _copyable(f'<div class="ag-code">{_python_html(call.code)}</div>', call.code, "the code it ran")

    if running or state == "ask":
        return f'<div class="ag-step ag-{state}">{line}</div>'
    found = (f'<div class="ag-res"><span class="ag-el">⎿</span><span>{_prose(call.summary or call.error or "done")}'
             "</span></div>")
    return (f'<div class="ag-step ag-{state}"><details><summary>{line}{found}</summary>'
            f'<div class="ag-detail">{_tool_detail_html(call)}</div></details></div>')


def _asked_html(call: ToolCall) -> str:
    """
    What a call waiting for the user's choice would read, and what each choice does, over the buttons.
    """
    read = call.waiting
    if read is None:
        return ""
    if read.limit:
        return (f'<div class="ag-asked"><b>✋ Limit reached: raise it, or answer now?</b> {_prose(read.what)}'
                f'<span class="ag-k"># raise: {_esc(read.go)}</span>'
                f'<span class="ag-k"># answer now: {_esc(read.smaller)}</span></div>')
    return (f'<div class="ag-asked"><b>✋ Big read: go, smaller or skip?</b> {_prose(read.what)}'
            f'<span class="ag-k"># go: {_esc(read.go)}</span><span class="ag-k"># smaller: {_esc(read.smaller)}</span>'
            '<span class="ag-k"># skip: answer without it · the agent only reads, whichever you pick</span></div>')


def _plan_box_html(plan: list[PlanStep]) -> str:
    """
    The agent's plan as a checklist in a box: each step ticked (✓), under way (▸) or still to do (○).
    """
    return f'<div class="ag-plan">{_blocks_html([_Plan(plan)], root=_ROOT)}</div>'


def _pulse_html(turn: _Turn, *, queued: int = 0) -> str:
    """
    The line at the bottom of a question while the agent works: thinking, for how long, how many questions wait
    after it, and how to stop it.
    """
    running = len(turn.running)
    waiting = next((call.waiting for call in turn.calls if call.waiting is not None), None)
    if turn.ask is not None and waiting is not None and waiting.limit:
        doing = "Waiting for you: raise the limit, or answer now?"
    elif turn.ask is not None:
        doing = "Waiting for you: go, smaller or skip?"
    elif running:
        doing = f"Waiting for {_plural(running, 'tool call')}…"
    else:
        doing = "Thinking…"
    counts = [_duration(time.monotonic() - turn.started)]
    if turn.calls:
        counts.append(_plural(len(turn.calls), "tool call"))
    if queued:
        counts.append(f"{queued} queued")
    return (f'<div class="ag-pulse"><span class="ag-star">✻</span><span class="ag-think">{_esc(doing)}</span>'
            f'<span class="ag-dim">({_esc(" · ".join(counts))} · stop ends it with what it found)</span></div>')


def _trail_html(trail: list[Any], seconds: float, *, is_open: bool) -> str:
    """
    The steps a finished question took, folded into one line: how many tool calls, how long, and the services they
    read; a click shows them again. A call that stopped at a limit the user raised isn't counted as not read: the
    agent called it again.
    """
    calls = [step for step in trail if isinstance(step, ToolCall)]
    steps = "".join(_tool_html(step, running=False) if isinstance(step, ToolCall) else _thinking_html(step)
                    for step in trail)
    services = [code for call in calls for code in call.services]
    failed = sum(1 for call in calls if not call.ok and call.refused != "raised")
    trouble = f'<span class="ag-bad">{failed} not read</span>' if failed else ""
    return (f'<details class="ag-trail"{" open" if is_open else ""}><summary><span class="ag-car">▸</span>'
            f"<span>{_esc(_plural(len(calls), 'tool call'))} · {_esc(_duration(seconds))}</span>{trouble}"
            f'<span>{_named_badges(services)}</span></summary><div class="ag-steps">{steps}</div></details>')


@dataclass
class _Turn:
    """
    One question in the window while it's answered: a widget per step in `box`, so a step that changes redraws only
    its own line, and a line someone opened stays open.

        pulse    the "Thinking…" line at the bottom while it works, and the answer once there is one
        plan     the checklist under the question, once the agent writes a plan: redrawn in place as it changes
        ask      the box with Go, Smaller and Skip while it waits for the user's choice on a big read
        steps    the steps' widgets, in order
        trail    the steps as they'll be folded once it's answered: the model's words, or a ToolCall
        lines    ToolCall id -> its step's widget
        calls    the tool calls it made, in the order they started
        running  ids of the calls that started and haven't finished
        started  when it was asked (time.monotonic)
    """

    question: str
    box: Any
    pulse: Any
    plan: Any = None
    ask: Any = None
    steps: list[Any] = field(default_factory=list)
    trail: list[Any] = field(default_factory=list)
    lines: dict[int, Any] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    running: set[int] = field(default_factory=set)
    started: float = field(default_factory=time.monotonic)

    @property
    def shown(self) -> tuple[Any, ...]:
        """
        The widgets the question shows while it works: the plan, the steps, the choice it waits for, then the
        "Thinking…" line.
        """
        plan = (self.plan,) if self.plan is not None else ()
        ask = (self.ask,) if self.ask is not None else ()
        return (*plan, *self.steps, *ask, self.pulse)


@dataclass
class _Queued:
    """
    A question typed while another was answered: its text, and its line in the conversation (the question and a ✕
    that drops it) until it's asked.
    """

    question: str
    row: Any


class _AgentChat(_Window):
    """
    The chat window: ipywidgets over an AgentView, which holds the agent and its conversation. Every widget callback
    says its errors in the window (_safely): raised, they would only reach the browser's log.

    What it keeps:

        items    the conversation's widgets, oldest first (the log shows them newest first: it runs bottom-up, so
                 it stays scrolled to the newest without a script)
        empty    what an empty conversation shows: what the agent does, its services, and questions to start from
        turn     the question being answered, while there is one
        queued   questions typed while it's answered, asked one after another once it is (their lines sit under
                 the conversation's, out of `items`, so the question asked next moves above the rest)
        busy     a question is being answered (the send button is Stop meanwhile)
        task     the asyncio task waiting for it, in a notebook (tests await it)
    """

    def __init__(self, view: AgentView, widgets: Any, *, height: int | str | None = None):
        self.view, self.w = view, widgets
        self.pickers = []
        self.quiet = False
        self.busy = False
        self.items: list[Any] = []
        self.queued: list[_Queued] = []
        self.turn: _Turn | None = None
        self.stop: threading.Event | None = None
        self.task: Any = None
        self.root = self._build(height)

    def _build(self, height: int | str | None) -> Any:
        w, layout = self.w, self.w.Layout
        style = w.HTML(f"{_CSS}<style>{_window_css()}</style>", layout=layout(display="none"))
        self.title = w.HTML(self._title_html(), layout=layout(flex="1 1 auto", min_width="0"))
        self.model_button = self._button("⚙ model", "Pick the provider and model the agent answers with",
                                         lambda: self._toggle(self.panel), "agc-new")
        self.memory_button = self._button("🧠 memory", "What the agent remembers about your account between "
                                                       "conversations", lambda: self._toggle(self.memory_panel),
                                          "agc-new")
        self.chats_button = self._button("🗂 chats", "Your saved chats: open one to carry it on, or rename this one",
                                         lambda: self._toggle(self.chats_panel), "agc-new")
        self.new_button = self._button("new chat", "Start over: the next question starts a new conversation",
                                       self._new_chat, "agc-new")
        head = w.HBox([self.title, self.chats_button, self.memory_button, self.model_button, self.new_button],
                      layout=layout(width="100%"))
        head.add_class("agc-head")
        self.panel = _ModelPanel(self, w, os.environ)
        self.memory_panel = _MemoryPanel(self, w)
        self.chats_panel = _ChatsPanel(self, w)
        self.log = w.VBox(layout=layout(flex_flow="column-reverse", overflow="hidden auto",
                                        height=_css_height(height), width="100%"))
        self.log.add_class("agc-log")
        self.empty = self._empty_state()
        prompt = w.HTML(_wrap('<span class="ag-pr">›</span>'), layout=layout(flex="0 0 auto"))
        self.question = w.Text(placeholder="ask about your AWS account", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.question.on_msg(self._on_enter(self._send))
        self.send_button = self._button("send ⏎", "Send (Enter does too)", self._send_or_stop, "agc-send")
        composer = w.HBox([prompt, self.question, self.send_button], layout=layout(width="100%"))
        composer.add_class("agc-composer")
        self.status = w.HTML(layout=layout(width="100%"))
        root = w.VBox([style, head, self.panel.root, self.memory_panel.root, self.chats_panel.root, self.log, composer, self.status],
                      layout=layout(width="100%"))
        root.add_class("agc-app")
        self._fill_log()
        self._say_ready()
        if not self._has_model():
            self._safely(self.panel.open)()
        return root

    def _button(self, label: str, tooltip: str, on_click: Callable[[], None], css: str) -> Any:
        button = self.w.Button(description=label, tooltip=tooltip, layout=self.w.Layout(width="auto",
                                                                                         flex="0 0 auto"))
        button.add_class(css)
        button.on_click(self._safely(lambda _button: on_click()))
        return button

    def _toggle(self, panel: _ModelPanel | _MemoryPanel | _ChatsPanel) -> None:
        """
        Opens a panel under the head (🗂 chats, ⚙ model, 🧠 memory), closing the others, or closes it when it's open.
        """
        if panel.is_open:
            panel.close()
            return
        for other in (self.panel, self.memory_panel, self.chats_panel):
            if other is not panel and other.is_open:
                other.close()
        panel.open()

    def _services(self) -> list[str]:
        """
        Every service the agent's tools read, in the order its toolkits name them.
        """
        reads = [read for operations in self.view.core.reads.values() for read in operations]
        return list(services_of(reads))

    def _empty_state(self) -> Any:
        """
        What an empty conversation shows: what the agent does, the badges of the services it reads, and a line per
        question to start from that asks it.
        """
        w = self.w
        reads = _and_list([t.title for t in self.view.core.toolkits if t.service]) or "your account"
        intro = w.HTML(_wrap(
            '<div class="ag-intro"><div class="ag-big"><span class="ag-pr">›</span> aws-agent</div>'
            f'<p class="ag-dim">Ask about {_esc(reads)} in plain English. Every tool call shows as it runs: the '
            "services it reads, the call itself and the data it got back. It only reads.</p>"
            f'<div class="ag-legend"><span class="ag-k ag-label">services</span>'
            f'{_named_badges(self._services())}</div><div class="ag-k"># try</div></div>'),
            layout=w.Layout(width="100%"))
        rows = [self._suggestion(suggestion) for suggestion in self._suggestions()]
        empty = w.VBox([intro, *rows], layout=w.Layout(width="100%", margin="0 0 auto 0", flex="0 0 auto"))
        empty.add_class("agc-empty")
        return empty

    def _suggestion(self, suggestion: _Suggestion) -> Any:
        box, _, face = _row_button(self.w, ("agc-row", "agc-row-b", "agc-row-f"),
                                   self._safely(lambda _button: self._send(suggestion.question)),
                                   tooltip="Ask this")
        face.value = _wrap(f'<div class="ag-sg"><span class="ag-pr">›</span><span>{_esc(suggestion.question)}</span>'
                           f"{_badges(suggestion.services)}</div>")
        return box

    def _suggestions(self) -> list[_Suggestion]:
        """
        Questions to start from, for the toolkits the agent has.
        """
        names = {toolkit.name for toolkit in self.view.core.toolkits}
        return [suggestion for suggestion in _SUGGESTIONS if suggestion.needs <= names]

    def resize(self, height: int | str | None) -> None:
        self.log.layout.height = _css_height(height)

    def _has_model(self) -> bool:
        core = self.view.core
        return bool(core.provider) or core.chat_model is not None

    def _model_where(self) -> str:
        """
        The model and who serves it: 'claude-haiku-4-5 @ bedrock', 'gpt-test @ Scripted', or that there's none.
        """
        core = self.view.core
        served = core.provider or (type(core.chat_model).__name__ if core.chat_model is not None else "")
        model = shown_model(core.provider, core.model) or "no model set up"
        return f"{model} @ {served}" if served else model

    def _title_html(self) -> str:
        """
        The window's head: what it is, the model and who serves it, the services it reads, and that it only reads.
        """
        chat = self.view.core.chat_title
        named = f'<span class="ag-ct" title="{_esc(chat)}">{_esc(chat)}</span>' if chat else ""
        return (f'<div class="ag-head"><b>aws-agent</b>{named}<span class="ag-dim">{_esc(self._model_where())}</span>'
                f'{_badges(self._services())}<span class="ag-ro">● read-only</span></div>')

    def _set_status(self, text: str, level: str = "") -> None:
        """
        The line under the question box: what went wrong or what's happening when there's something to say, else
        how to use it and what the conversation has cost.
        """
        if text:
            self._set(self.status, _wrap(f'<div class="ag-hint {level}"><span>{_prose(text)}</span></div>'))
            return
        usage = _QUEUE_HINT if self.busy else _SEND_HINT
        self._set(self.status, _wrap(f'<div class="ag-hint"><span>{_esc(usage)}</span>'
                                     f"<span>{_esc(self._spent())}</span></div>"))

    def _spent(self) -> str:
        core = self.view.core
        asked = [a for a in core.history if a.thread == core.thread]
        if not asked:
            return ""
        costs = [a.cost for a in asked if a.cost is not None]
        spent = f" · {human_money(sum(costs))} (est.)" if costs else ""
        return f"This conversation: {_plural(len(asked), 'question')}{spent}"

    def _warn(self, text: str) -> None:
        self._set_status(text, "warn")

    def _explain(self, code: str, message: str) -> str:
        return self.view._explain(code, message)

    def _say_ready(self) -> None:
        """
        The line under the question box between questions: what's wrong with the setup first, else the usual.
        """
        if self._has_model():
            self._set_status("")
            return
        self._set_status("The agent needs a model to answer with, and none is set up: pick one above and press use "
                         "this model. Amazon Bedrock needs no key: it's called with this notebook's AWS credentials.",
                         "warn")

    def _show_log(self) -> None:
        """
        The conversation, then the questions that wait, newest at the bottom (the log runs bottom-up).
        """
        rows = [waiting.row for waiting in self.queued]
        self.log.children = tuple(reversed([*self.items, *rows]))

    def _add(self, widget: Any) -> Any:
        if self.empty in self.items:
            self.items.remove(self.empty)
        self.items.append(widget)
        self._show_log()
        return widget

    def _html(self, value: str) -> Any:
        return self.w.HTML(value, layout=self.w.Layout(width="100%", flex="0 0 auto"))

    def _box(self, children: list[Any]) -> Any:
        """
        A box of the conversation's widgets that keeps its full height: a flex item of the log would otherwise
        shrink to fit it, and cut off its own end.
        """
        box = self.w.VBox(children, layout=self.w.Layout(width="100%", flex="0 0 auto", overflow="visible"))
        box.add_class("agc-turn")
        return box

    def _show_empty(self) -> None:
        self.queued.clear()
        self.items = [self.empty]
        self.log.children = (self.empty,)

    def _fill_log(self) -> None:
        """
        The questions of the conversation that were asked before the window opened, or the empty conversation.
        """
        core = self.view.core
        self._show_empty()
        for answer in core.history:
            if answer.thread == core.thread:
                self._add_answered(answer)

    def _add_answered(self, answer: AgentAnswer) -> None:
        """
        A question answered outside the window (before it opened, or with ask() in a cell): the steps it took,
        folded, then its answer. The model's words between them weren't kept.
        """
        self._add(self._html(_wrap(_question_html(answer.question))))
        self._add(self._box([self._html(self._reply_html(answer, list(answer.calls)))]))

    def added(self, answer: AgentAnswer) -> None:
        """
        A question asked with ask() in another cell joins the conversation shown here.
        """
        self._add_answered(answer)
        self.renamed()
        self._say_ready()

    def cleared(self) -> None:
        """
        The conversation was reset (new chat, or new_conversation() in a cell): the window starts over.
        """
        self._show_empty()
        self.renamed()
        self._say_ready()

    def opened(self) -> None:
        """
        A saved chat was opened (🗂 chats, or open_chat() in a cell): the window shows it, and the next question
        carries it on.
        """
        self._fill_log()
        self.renamed()
        self._say_ready()

    def renamed(self) -> None:
        """
        The open chat's title changed (its first answer, a rename, another chat opened): the head and the chats
        panel show it.
        """
        self._set(self.title, self._title_html())
        if self.chats_panel.is_open:
            self.chats_panel.refresh()

    def model_changed(self) -> None:
        """
        The agent answers with another model now (⚙ model, or use_model() in a cell): the head names it, the
        settings show it, and when there was a conversation a line across it says a new one starts under it.
        """
        core = self.view.core
        self._set(self.title, self._title_html())
        self.panel.refresh()
        if self.empty not in self.items:
            self._add(self._html(_wrap(_switch_html(shown_model(core.provider, core.model)))))
        title = provider_info(core.provider).title
        self._set_status(f"Now answering with {shown_model(core.provider, core.model)} ({title}). A new conversation "
                         "starts with the next question.", "ok")

    def _new_chat(self) -> None:
        if self.busy:
            self._set_status("Still answering: press stop first, then start a new chat.", "warn")
            return
        self.view.core.new_conversation()
        self.cleared()

    def _send_or_stop(self) -> None:
        if self.busy:
            self._stop()
        else:
            self._send()

    def _stop(self) -> None:
        if self.stop is not None:
            self.stop.set()
        self.send_button.disabled, self.send_button.tooltip = True, "Stopping…"
        self._set_status("Stopping: the agent finishes the step it's on, then answers with what it found.")
        if self.turn is not None and self.turn.ask is not None:
            self._choose("skip")

    def _send(self, question: str | None = None) -> None:
        text = str(self.question.value if question is None else question).strip()
        if not text and self.queued and not self.busy:
            self._ask_next()
            return
        if not text:
            self._set_status("Type a question first, then press Enter.")
            return
        self._quietly(self.question, value="")
        if self.busy:
            self._queue(text)
            return
        self._ask(text)

    def _queue(self, question: str) -> None:
        """
        A question typed while another is answered: a line under the conversation, asked once the ones before it
        are answered.
        """
        drop = self.w.Button(description="✕", tooltip="Don't ask this", layout=self.w.Layout(width="auto",
                                                                                            flex="0 0 auto"))
        drop.add_class("agc-drop")
        line = self.w.HTML(_wrap(_queued_html(question)), layout=self.w.Layout(flex="1 1 auto", width="auto",
                                                                               min_width="0"))
        row = self.w.HBox([line, drop],
                          layout=self.w.Layout(width="100%", flex="0 0 auto", align_items="center"))
        row.add_class("agc-queued")
        waiting = _Queued(question, row)
        drop.on_click(self._safely(lambda _button: self._unqueue(waiting)))
        self.queued.append(waiting)
        self._show_log()
        self._tick()
        ahead = len(self.queued) - 1
        after = f"once the {_plural(ahead, 'question')} queued before it" if ahead else "once this answer is done"
        self._set_status(f"Queued: it's asked {after}. Press stop to end this answer sooner, or ✕ to drop it.")

    def _unqueue(self, waiting: _Queued) -> None:
        if waiting not in self.queued:
            return
        self.queued.remove(waiting)
        self._show_log()
        self._tick()
        self._set_status("Dropped: that question won't be asked.")

    def _ask_next(self) -> None:
        """
        The question that waited longest, now that nothing is being answered. After a question the agent couldn't
        answer they wait instead (the next would most likely fail the same way): Enter in the empty box, or the next
        question answered, carries on.
        """
        if self.busy or not self.queued:
            return
        self._ask(self.queued.pop(0).question)

    def _ask(self, text: str) -> None:
        self._begin(text)
        stop, steps = threading.Event(), queue.SimpleQueue()
        self.stop = stop

        def work() -> AgentAnswer:
            return self.view.core.ask(text, stop=stop, on_step=steps.put)

        self._start(work, steps)

    def _show_busy(self, busy: bool) -> None:
        """
        The send button as stop while a question is answered, and back as send after; new chat waits meanwhile.
        """
        self.busy = busy
        self.send_button.disabled = False
        self.send_button.description = "stop ■" if busy else "send ⏎"
        self.send_button.tooltip = "Stop: answer with what it found so far" if busy else "Send (Enter does too)"
        _class_if(self.send_button, "agc-stop", busy)
        self.new_button.disabled = busy
        self.panel.use_button.disabled = busy

    def _begin(self, question: str) -> None:
        """
        The question in the conversation, a turn under it that its steps go in, and the window set to wait.
        """
        self._show_busy(True)
        self._add(self._html(_wrap(_question_html(question))))
        pulse = self._html("")
        box = self._add(self._box([pulse]))
        self.turn = _Turn(question, box, pulse)
        self._tick()
        self._set_status("")

    def _start(self, work: Callable[[], AgentAnswer], steps: queue.SimpleQueue) -> None:
        """
        Runs the question: in a notebook on a worker thread, drawing its steps on the kernel's event loop as they
        come; without a running loop, right away.
        """
        loop = _running_loop()
        if loop is not None:
            self.task = loop.create_task(self._run_later(work, steps))
            return
        try:
            answer = work()
        except Exception as exc:  # noqa: BLE001
            self._drain(steps)
            self._failed(exc)
            return
        self._drain(steps)
        self._answered(answer)

    async def _run_later(self, work: Callable[[], AgentAnswer], steps: queue.SimpleQueue) -> None:
        """
        The question on a worker thread, while its steps are drawn here every _DRAW_EVERY seconds, so Stop and the
        rest of the window keep working.
        """
        job = asyncio.get_running_loop().run_in_executor(None, work)
        while not job.done():
            await asyncio.wait([job], timeout=_DRAW_EVERY)
            self._drain(steps)
            self._safely(self._tick)()
        self._drain(steps)
        try:
            answer = await job
        except Exception as exc:  # noqa: BLE001
            self._safely(self._failed)(exc)
            return
        self._safely(self._answered)(answer)

    def _drain(self, steps: queue.SimpleQueue) -> None:
        while True:
            try:
                step = steps.get_nowait()
            except queue.Empty:
                return
            self._safely(self._stepped)(step)

    def _tick(self) -> None:
        if self.turn is not None:
            self._set(self.turn.pulse, _wrap(_pulse_html(self.turn, queued=len(self.queued))))

    def _stepped(self, step: AgentStep) -> None:
        """
        One step of the agent's work, drawn under the question: its plan, its words, or a tool call starting or
        finishing.
        """
        turn = self.turn
        if turn is None:
            return
        if step.kind == "thinking":
            turn.trail.append(step.text)
            self._add_step(turn, _thinking_html(step.text))
        elif step.kind == "tool" and step.call is not None:
            self._tool_started(turn, step.call)
        elif step.kind in ("tool_done", "asking") and step.call is not None:
            self._tool_finished(turn, step.call)
        elif step.kind == "plan":
            self._show_plan(turn, step.plan)
        self._tick()

    def _show_plan(self, turn: _Turn, plan: list[PlanStep]) -> None:
        """
        The plan under the question: drawn the first time the agent writes one, then redrawn in place.
        """
        if turn.plan is None:
            turn.plan = self._html(_wrap(_plan_box_html(plan)))
            turn.box.children = turn.shown
            return
        self._set(turn.plan, _wrap(_plan_box_html(plan)))

    def _add_step(self, turn: _Turn, html: str) -> Any:
        """
        A new step, above the "Thinking…" line.
        """
        widget = self._html(_wrap(html))
        turn.steps.append(widget)
        turn.box.children = turn.shown
        return widget

    def _tool_started(self, turn: _Turn, call: ToolCall) -> None:
        turn.calls.append(call)
        turn.trail.append(call)
        turn.running.add(id(call))
        turn.lines[id(call)] = self._add_step(turn, _tool_html(call, running=True))

    def _tool_finished(self, turn: _Turn, call: ToolCall) -> None:
        if id(call) not in turn.lines:
            self._tool_started(turn, call)
        turn.running.discard(id(call))
        self._set(turn.lines[id(call)], _wrap(_tool_html(call, running=False)))

    @staticmethod
    def _meta_text(answer: AgentAnswer) -> str:
        """
        Who answered and what it took, under the answer: 'claude-haiku-4-5 · 2 tool calls · 11s · $0.02 (est.)'.
        """
        served = answer.model.split(" (")[0]
        model = short_model(served) if answer.provider == "bedrock" else answer.model
        cost = f"{human_money(answer.cost)} (est.)" if answer.cost is not None else ""
        parts = [model or "model not named", _plural(len(answer.calls), "tool call"), _duration(answer.seconds), cost]
        return " · ".join(part for part in parts if part)

    def _reply_html(self, answer: AgentAnswer, trail: list[Any]) -> str:
        """
        The answer as the window shows it: the plan as it left it, the steps it took folded into one line (open when
        something went wrong), the answer and its charts, the findings (what it found, and how it got there), who answered and what it cost, and the rest of the report
        (each call, the AWS reads, the commands to look closer) under details.
        """
        view = self.view
        checked = view._checked_blocks(answer)
        findings = [b for b in checked if isinstance(b, _Findings) and b.items]
        rest = [b for b in checked if not isinstance(b, (_Findings, _Plan))]
        went_wrong = bool(answer.error) or not answer.text
        parts = [_plan_box_html(answer.plan)] if answer.plan else []
        if trail:
            parts.append(_trail_html(trail, answer.seconds, is_open=went_wrong))
        if answer.text:
            mark = f'<span class="ag-mark">{_MACHINE}</span>'
            parts.append(f'<div class="ag-ans">{mark}{_answer_text_html(answer.text)}</div>')
        if answer.charts:
            parts.append(f'<div class="ag-charts">{_blocks_html([_Charts(answer.charts)], root=_ROOT)}</div>')
        if not answer.text and not answer.error:
            parts.append(_blocks_html([_Note("The agent stopped before it wrote an answer: the steps above show what "
                                             "it read.", "warn")], root=_ROOT))
        if findings:
            parts.append(f'<div class="ag-fd">{_blocks_html(findings, view.max_rows, root=_ROOT)}</div>')
        parts.append(f'<div class="ag-foot"><span class="ag-dim">{_esc(self._meta_text(answer))}</span>'
                     '<details class="ag-more"><summary>details: tool calls, AWS reads and cost</summary>'
                     f'<div class="ag-box">{_blocks_html(rest, view.max_rows, root=_ROOT)}</div></details></div>')
        return _wrap("".join(parts))

    def _ask_choice(self, turn: _Turn, call: ToolCall) -> None:
        """
        The box under a question waiting for the user's choice on a big read: what it would read, and Go, Smaller and
        Skip, which go on with that choice. At a used-up limit of the budget, Smaller and Skip both answer now, so the
        box has two buttons: raise the limit, or answer now.
        """
        if id(call) in turn.lines:
            self._set(turn.lines[id(call)], _wrap(_tool_html(call, running=False)))
        at_limit = bool(call.waiting is not None and call.waiting.limit)
        buttons = []
        for choice, label, tooltip in _LIMIT_BUTTONS if at_limit else _CHOICE_BUTTONS:
            button = self.w.Button(description=label, tooltip=tooltip, layout=self.w.Layout(width="auto"))
            button.add_class("agc-choice")
            button.add_class(f"agc-{choice}")
            button.on_click(self._safely(lambda _button, picked=choice: self._choose(picked)))
            buttons.append(button)
        turn.ask = self.w.HBox([self._html(_wrap(_asked_html(call))), *buttons],
                               layout=self.w.Layout(width="auto", flex="0 0 auto"))
        turn.ask.add_class("agc-ask")
        turn.box.children = turn.shown
        self._tick()
        if at_limit:
            self._set_status("The agent is waiting for you: raise the limit for this question, or have it answer "
                             "now with what it found.", "warn")
            return
        self._set_status("The agent is waiting for you: go reads it all, smaller the smaller part, skip answers "
                         "without it.", "warn")

    def _choose(self, choice: str) -> None:
        """
        Goes on with the question waiting for the user's choice: the box goes, and the call runs again with it.
        """
        turn = self.turn
        if turn is None or turn.ask is None:
            return
        turn.ask = None
        turn.box.children = turn.shown
        waiting = next((call for call in turn.calls if call.waiting is not None), None)
        if waiting is not None and id(waiting) in turn.lines:
            self._set(turn.lines[id(waiting)], _wrap(_tool_html(waiting, running=True)))
        self._set_status("")
        stop, steps = self.stop, queue.SimpleQueue()

        def work() -> AgentAnswer:
            return self.view.core.resume(choice, stop=stop, on_step=steps.put)

        self._start(work, steps)

    def _answered(self, answer: AgentAnswer) -> None:
        """
        The question's answer, with its steps folded above it, and the window ready again; or, when it stopped to
        ask before a big read, the choice it waits for.
        """
        turn = self.turn
        if turn is not None and answer.waiting:
            self._ask_choice(turn, answer.waiting[0])
            return
        if turn is not None:
            for call in answer.calls:
                if id(call) not in turn.lines:
                    turn.trail.append(call)
            turn.box.children = (turn.pulse,)
            self._set(turn.pulse, self._reply_html(answer, turn.trail))
        self._done()
        if self.memory_panel.is_open:
            self.memory_panel.refresh()
        self.renamed()
        self._say_ready()
        if answer.error and self.queued:
            self._set_status(_QUEUE_HELD, "warn")
            return
        self._ask_next()

    def _failed(self, exc: BaseException) -> None:
        """
        A question the agent couldn't take (no model set up, a missing package): said where the answer would be,
        with the question back in the box to ask again.
        """
        turn = self.turn
        if turn is not None:
            turn.box.children = turn.shown
            self._set(turn.pulse, _wrap(f'<div class="ag-fail">✗ {_prose(self._error_text(exc))}</div>'))
            self._quietly(self.question, value=turn.question)
        self._done()
        waiting = f" {_QUEUE_HELD}" if self.queued else ""
        self._set_status(f"Your question is back in the box: fix what's said above, then press Enter.{waiting}",
                         "warn")

    def _done(self) -> None:
        self.turn = None
        self.stop = None
        self._show_busy(False)
