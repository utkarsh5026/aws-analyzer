"""
AgentView: the agent's notebook UI. ask() shows the answer first, with the charts the agent drew for it (_Charts),
then its findings (what it found in the account, and what to do), the plan it worked to (_Plan, when it wrote one), what it checked to get it (each tool call
with its window and regions, what it couldn't read or was refused, the AWS reads, the tokens and the cost) and the
View commands to look closer, the ones its findings name first; transcript() and tools() show the questions so far and what the agent can call. A
question that pauses before a big read shows what it would read instead, and go(), smaller() and skip() go on.

Its reports sit in a box of the class _ROOT, which scopes its stylesheet's rules, and each title starts with
_BADGE, so its reports are easy to tell from other analyzers'.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError

from .._bedrock.blocks import _Answer
from .._bedrock.prices import _per_million, short_model
from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Report, _Table, _Text, _Title, _draws
from .._kit.deps import _require
from .._kit.errors import _Hint
from .._kit.fmt import _duration, _fmt_dt, _plural, _utcnow, _zone_name, human_money, human_size
from .._kit.render import _base_css, _Look, _report_html
from .._kit.text import _esc
from .._kit.view import _BaseView, _friendly_errors
from .analyzer import AgentAnalyzer
from .answers import AgentAnswer, PlanStep, ToolCall, answer_findings, answer_next, reply_findings
from .workspace.charts import CHARTS_FOLDER, Chart
from .llm import model_place, no_model_hint, provider_info

if TYPE_CHECKING:
    from .window import _AgentChat

_ROOT = "agt"
_EXTRA_CSS = """\
.agt .ans{font-size:14px;line-height:1.6;margin:8px 0 12px;max-width:900px;overflow-wrap:anywhere}
.agt .ans>:first-child{margin-top:0}
.agt .ans>:last-child{margin-bottom:0}
.agt .ans p{margin:0 0 .65em}
.agt .ans h1,.agt .ans h2,.agt .ans h3,.agt .ans h4{margin:.95em 0 .4em;line-height:1.3;font-weight:650;font-size:1.08em}
.agt .ans ul,.agt .ans ol{margin:.25em 0 .65em;padding-left:1.45em}
.agt .ans li{margin:.18em 0}
.agt .ans blockquote{margin:.4em 0 .75em;padding:.15em 0 .15em .9em;border-left:3px solid rgba(127,127,127,.36)}
.agt .ans code{font-size:.86em;user-select:text;-webkit-user-select:text}
.agt .ans a{color:#3b82f6;text-decoration:none;border-bottom:1px solid rgba(59,130,246,.35)}
.agt .ans .pre{position:relative;margin:.45em 0 .8em}
.agt .ans .pre pre{margin:0;padding:11px 14px;white-space:pre;overflow:auto;max-height:420px;font-size:12.5px;line-height:1.55}
.agt .ans .pre code{background:none;padding:0;font-size:inherit}
.agt .ans .pre .lang{position:absolute;top:6px;right:86px;font-size:10px;font-weight:600;text-transform:uppercase;opacity:.5}
.agt .ans .mdt{overflow-x:auto;margin:.45em 0 .8em;border:1px solid rgba(127,127,127,.22);border-radius:10px}
.agt .ans table{border-collapse:collapse;font-size:13px;line-height:1.45;width:100%}
.agt .ans th,.agt .ans td{border-bottom:1px solid rgba(127,127,127,.22);padding:6px 11px;text-align:left;vertical-align:top}
.agt .ans th{background:rgba(127,127,127,.06);font-weight:600}
.agt .ans tbody tr:last-child td{border-bottom:0}
.agt .ans th.r,.agt .ans td.r{text-align:right;font-variant-numeric:tabular-nums}
.agt .cht{display:flex;flex-direction:column;gap:16px;margin:4px 0 14px;max-width:900px}
.agt .cht figure{margin:0}
.agt .cht figcaption{font-size:13.5px;font-weight:600;margin:0 0 6px}
.agt .cht img{display:block;max-width:100%;height:auto;border-radius:10px;border:1px solid rgba(127,127,127,.22);background:#fcfcfb}
.agt .cht .cht-f{font-size:11.5px;opacity:.6;margin-top:4px}
.agt .pln{margin:6px 0 12px;max-width:900px}
.agt .pln-h{font-size:12px;font-weight:600;opacity:.7;margin:0 0 4px}
.agt .pln ol{list-style:none;margin:0;padding:0}
.agt .pln li{display:flex;gap:8px;margin:2px 0;line-height:1.5}
.agt .pln-m{flex:0 0 1.1em;text-align:center;opacity:.55}
.agt .pln .pln-done{opacity:.65}
.agt .pln .pln-done .pln-m{color:#10b981;opacity:1}
.agt .pln .pln-now{font-weight:600}
.agt .pln .pln-now .pln-m{color:#3b82f6;opacity:1}"""
_CSS = _base_css(_ROOT, _EXTRA_CSS)
_BADGE = "🤖 Agent"

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look (the windows' reports).
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)


@dataclass
class _Charts:
    """
    The charts an answer shows under its text, each under its title, with its kind, size and workspace file.
    """

    charts: list[Chart]


@_draws(_Charts, "html")
def _charts_html(block: _Charts, report: _Report) -> str:
    """
    Each chart as an <img> holding the picture itself (a data: URL, so it stays in the saved notebook; an SVG's
    scripts don't run in an <img>), under its title.
    """
    figures = []
    for chart in block.charts:
        source = f"data:{chart.mime};base64,{base64.b64encode(chart.data).decode()}"
        figures.append(f'<figure><figcaption>{_esc(chart.title)}</figcaption>'
                       f'<img src="{source}" alt="{_esc(chart.title)}">'
                       f'<div class="cht-f">{chart.format_name} · {_esc(chart.size)} · {_esc(chart.path)}</div>'
                       '</figure>')
    return f'<div class="cht">{"".join(figures)}</div>'


@_draws(_Charts, "text")
def _charts_lines(block: _Charts, report: _Report) -> list[str]:
    lines = [f"📈 {chart.title} ({chart.format_name}, {chart.size}): open in a notebook to see it"
             for chart in block.charts]
    return ["", *lines, "   save_charts() writes them to files."]


_PLAN_MARKS = {"completed": "✓", "in_progress": "▸", "pending": "○"}
_PLAN_CLASSES = {"completed": "pln-done", "in_progress": "pln-now", "pending": ""}


@dataclass
class _Plan:
    """
    The plan the agent wrote for a question, as a checklist: each step ticked, under way or still to do.
    """

    steps: list[PlanStep]

    @property
    def heading(self) -> str:
        """
        'Plan · 2 of 3 done'.
        """
        done = sum(1 for step in self.steps if step.done)
        return f"Plan · {done} of {len(self.steps)} done"


@_draws(_Plan, "html")
def _plan_html(block: _Plan, report: _Report) -> str:
    items = []
    for step in block.steps:
        css = _PLAN_CLASSES.get(step.status, "")
        items.append(f'<li class="{css}"><span class="pln-m">{_PLAN_MARKS.get(step.status, "○")}</span>'
                     f"<span>{_esc(step.text)}</span></li>")
    return f'<div class="pln"><div class="pln-h">{_esc(block.heading)}</div><ol>{"".join(items)}</ol></div>'


@_draws(_Plan, "text")
def _plan_lines(block: _Plan, report: _Report) -> list[str]:
    lines = [f"  {_PLAN_MARKS.get(step.status, '○')} {step.text}" for step in block.steps]
    return ["", block.heading, *lines]


def _coverage_text(call: ToolCall) -> str:
    """
    '2026-10-15 18:00 to 2026-10-16 08:00 IST · us-east-1 · cut short' for the table of what it checked: the window
    in the zone reports show times in (set_timezone()), the regions, the S3 keys it read, and whether it was cut
    short.
    """
    cov = call.coverage
    parts = []
    window = cov.get("window")
    if isinstance(window, dict) and window.get("from") and window.get("to"):
        start, end = (datetime.fromisoformat(str(window[k]).replace("Z", "+00:00")) for k in ("from", "to"))
        parts.append(f"{_fmt_dt(start)} to {_fmt_dt(end, zone=True)}")
    where = cov.get("regions") or cov.get("region")
    if where:
        parts.append(", ".join(where) if isinstance(where, (list, tuple)) else str(where))
    if cov.get("scanned"):
        parts.append(f"{int(cov['scanned']):,} keys read")
    if cov.get("cut_short"):
        parts.append("cut short")
    return " · ".join(parts) or "-"


def _zone_label(zone: tzinfo | None) -> str:
    """
    'Asia/Kolkata (IST)', 'UTC', or 'UTC+05:30': a zone as the tools() report names it.
    """
    moment = _utcnow().astimezone(zone)
    short, key = _zone_name(moment), getattr(zone, "key", None)
    return f"{key} ({short})" if key and key != short else short


def _first_line(doc: str | None) -> str:
    """
    A docstring's first sentence, on one line.
    """
    flat = " ".join((doc or "").split())
    match = re.match(r"(.+?\.)(\s|$)", flat)
    return match.group(1) if match else flat


def _price_key(answer: AgentAnswer) -> str:
    """
    The name to price a model by in model_prices=: a Bedrock model's family ('claude-haiku-4-5'), else the model or
    deployment without the version it was served as ('prod-gpt-4o', not 'prod-gpt-4o (gpt-4o-2024-11-20)').
    """
    base = answer.model.split(" (")[0]
    return short_model(base) if answer.provider == "bedrock" else base


def _cost_basis(answer: AgentAnswer) -> str:
    """
    What a report's model cost is based on: the prices passed in model_prices=, or Amazon Bedrock's list prices for
    the model; when neither knows it, how to pass them.
    """
    rates = answer.rates
    tokens = f"{answer.input_tokens:,} input and {answer.output_tokens:,} output tokens"
    if answer.cost is None or rates is None:
        sheet = f"{provider_info(answer.provider).title}'s price sheet" if answer.provider else "your provider's prices"
        return (f"The model's cost is unknown: pass its prices, AgentView(model_prices={{{_price_key(answer)!r}: "
                f"(input_price, output_price)}}) (USD per 1M input and output tokens, from {sheet}).")
    if rates.source == "list":
        return (f"Model cost is an estimate: {tokens} at Amazon Bedrock's list prices for {_price_key(answer)} "
                f"({_per_million(rates.price_in)} in and {_per_million(rates.price_out)} out per 1M tokens; "
                "model_prices= sets your own).")
    return f"Model cost is an estimate: {tokens} at the prices you passed in model_prices=."


def _look_closer(commands: list[tuple[str, str]]) -> str:
    """
    The title over the commands to run next, naming the Views they're on: 'Look closer (aws_analyzer.LambdaView)'.
    """
    views = dict.fromkeys(m.group(1) for command, _ in commands if (m := re.match(r"(\w+)\(\)\.", command)))
    return f"Look closer ({', '.join(f'aws_analyzer.{view}' for view in views)})" if views else "Look closer"


class AgentView(_BaseView):
    """
    Notebook UI over AgentAnalyzer: ask questions about your AWS account in plain English. Each method renders a
    report and returns nothing; for the data, call the matching method on `view.core` (ask() -> AgentAnswer).

    AgentView(provider='bedrock', model='sonnet', timezone='Asia/Kolkata', ...) passes its settings to a new
    AgentAnalyzer (see there for each, and for every provider it can answer with); AgentView(core) uses one you made.
    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a progress bar while it works, when tqdm is installed), 'plain' or 'off'.
    """

    _render_html = staticmethod(_render_html)
    _GROUPS = {
        "💬 Ask": ("chat", "ask", "new_conversation"),
        "✋ Big reads": ("go", "smaller", "skip"),
        "🔎 Look back": ("transcript", "save_charts"),
        "🧰 What it can do": ("tools", "use_model"),
        "❓ Help": ("help",),
    }
    _START = (
        ("chat()", "a chat window: ask, and watch each tool call as it runs"),
        ("ask('Which Lambda failed last night?')", "the answer, what it checked, and what to run next"),
        ("ask('Why did it fail?')", "a follow-up: it remembers the conversation"),
        ("ask('Did the files uploaded to acme-raw last night get processed?')", "S3 and Lambda together"),
        ("AgentView(python='local').ask('Chart the CSVs in s3://acme-raw/sales/ by month')",
         "its own Python over your files, with charts"),
        ("tools()", "what the agent can call, its model and its budget"),
    )
    _PROGRESS = ("Thinking", "steps")

    def __init__(
        self,
        core: AgentAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
        **settings: Any,
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        if core is not None and settings:
            raise TypeError(f"AgentView(core, ...) takes the settings on the analyzer: AgentAnalyzer("
                            f"{', '.join(f'{k}=...' for k in settings)})")
        self.core: AgentAnalyzer = core or AgentAnalyzer(**settings)
        self.window: _AgentChat | None = None

    def _display(self, widget: Any) -> None:
        from IPython.display import display

        display(widget)

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in message:
            return f"{text} README lists the read-only IAM permissions each tool needs."
        return message


    @_friendly_errors
    def ask(self, question: str, *, new: bool = False) -> None:
        """
        The agent's answer to a question about your AWS account (Lambda and S3, for now), then what it checked to
        get it: the plan it worked to, each tool call with its time window and regions, what it couldn't read or was refused, the AWS reads,
        the tokens and what it cost, and the LambdaView and S3View commands to look closer. Follow-up questions continue the
        conversation; new=True starts a new one. The kernel's stop button stops it, and the report shows what it
        found until then. Before a big read (a deep S3 listing, many days of logs, a large save) it stops to ask:
        go(), smaller() or skip() goes on.
        """
        window = self.window
        if window is not None and window.busy:
            raise _Hint("The chat window is answering a question: wait for it, press its Stop, or type the question in "
                        "the window, which asks it next.")
        with self._progress() as tick:
            answer = self.core.ask(question, new=new, progress=tick)
        if window is not None and not answer.waiting:
            window.added(answer)
        self._show(self._answer_blocks(answer))

    @_friendly_errors
    def go(self) -> None:
        """
        Lets the agent make the big read it stopped to ask about (a deep S3 listing, many days of logs, a large save),
        all of it, and shows the answer it goes on to write.
        """
        self._resume("go")

    @_friendly_errors
    def smaller(self) -> None:
        """
        Lets the agent make the smaller read its question named instead of the big one (the keys listed so far, the
        last few days of logs, the files that fit), and shows the answer, which says what that left out.
        """
        self._resume("smaller")

    @_friendly_errors
    def skip(self) -> None:
        """
        Tells the agent to answer without the big read it stopped to ask about, and shows the answer, which says what
        it didn't read.
        """
        self._resume("skip")

    def _resume(self, choice: str) -> None:
        window = self.window
        if window is not None and window.busy:
            raise _Hint("The chat window is answering a question: pick go, smaller or skip with its buttons.")
        if not self.core.waiting:
            raise _Hint("Nothing is waiting for your choice: go(), smaller() and skip() answer the agent when it "
                        "stops to ask before a big read.")
        with self._progress() as tick:
            answer = self.core.resume(choice, progress=tick)
        if window is not None and not answer.waiting:
            window.added(answer)
        self._show(self._answer_blocks(answer))

    @_friendly_errors
    def chat(self, *, height: int | str | None = None) -> None:
        """
        A chat window to talk to the agent: type a question and watch it work, step by step, as it happens: its
        plan for a question of several steps (a checklist that ticks off), what it means to do, each tool it calls with what that found, then the answer, with what it checked folded under
        it. Follow-ups continue the conversation, Stop ends a question with what it found so far, and New chat starts
        over. Questions asked with ask() in other cells join it. height= sets the conversation's height (800
        pixels, or CSS such as '70vh'); it fills the browser window otherwise.
        """
        if not self.use_html:
            raise _Hint("The chat window needs Jupyter (SageMaker Studio, a notebook instance, JupyterLab or VS "
                        "Code). Here, ask('Which Lambda failed last night?') answers as a report, in the same "
                        "conversation.")
        widgets = _require("ipywidgets", "The chat window")
        from .window import _AgentChat

        if self.window is None:
            self.window = _AgentChat(self, widgets, height=height)
        elif height is not None:
            self.window.resize(height)
        self._display(self.window.root)

    def _answer_blocks(self, answer: AgentAnswer) -> list[Any]:
        if answer.waiting:
            return self._waiting_blocks(answer)
        blocks: list[Any] = [_Title(answer.question, self._answer_line(answer))]
        if answer.text:
            blocks.append(_Answer(answer.text))
        if answer.charts:
            blocks.append(_Charts(answer.charts))
        if not answer.text and not answer.error:
            blocks.append(_Note("The agent stopped before it wrote an answer: the table below shows what it read.",
                                "warn"))
        return blocks + self._checked_blocks(answer)

    def _waiting_blocks(self, answer: AgentAnswer) -> list[Any]:
        """
        A question paused before a big read: what it would read, the plan so far, and the three choices to run.
        """
        call = answer.waiting[0]
        read = call.waiting
        blocks: list[Any] = [
            _Title(answer.question, f"waiting for you · {self._answer_line(answer)}"),
            _Findings([("warn", f"It stopped to ask before a big read: {read.what} Pick go(), smaller() or skip(); "
                                "the agent only reads, whichever you pick.")]),
        ]
        if answer.plan:
            blocks.append(_Plan(answer.plan))
        others = len(answer.waiting) - 1
        if others:
            blocks.append(_Note(f"{_plural(others, 'more read')} waits after this one: it's asked next."))
        blocks.append(_Next([("go()", read.go), ("smaller()", read.smaller), ("skip()", "answer without it")],
                            title=f"Go on with {call.brief}"))
        return blocks

    def _answer_line(self, answer: AgentAnswer) -> str:
        """
        'gpt-4.1 · 2 tool calls · 14s · follow-up': who answered and how much it took, under the question.
        """
        is_follow_up = sum(1 for a in self.core.history if a.thread == answer.thread) > 1
        return " · ".join(filter(None, [
            answer.model or "model not named",
            _plural(len(answer.calls), "tool call"),
            _plural(len(answer.charts), "chart") if answer.charts else "",
            _duration(answer.seconds),
            "follow-up" if is_follow_up else "",
        ]))

    def _checked_blocks(self, answer: AgentAnswer) -> list[Any]:
        """
        What an answer's report shows under the answer: the cards, the findings (what the agent found in the account,
        then what to know about how it got the answer), the plan it worked to, each tool call, the AWS reads, what
        it cost and the commands to look closer (the calls its findings name first).
        """
        core = self.core
        failed = sum(1 for c in answer.calls if c.error)
        blocks: list[Any] = []
        tokens = f"{answer.input_tokens + answer.output_tokens:,}"
        cost = human_money(answer.cost) if answer.cost is not None else "unknown"
        stopped = answer.stopped not in (None, "stopped")
        blocks.append(_Cards([
            ("Tool calls", f"{len(answer.calls):,}" + (f" ({failed} failed)" if failed else ""),
             *(["warn"] if failed or stopped else [])),
            ("AWS reads", f"{sum(answer.aws_calls.values()):,}", *(["bad"] if answer.blocked else [])),
            ("Tokens", tokens),
            ("Model cost (est.)", cost),
            ("Time", _duration(answer.seconds)),
        ]))
        blocks.append(_Findings([*reply_findings(answer), *answer_findings(answer, core.budget)]))
        if answer.plan:
            blocks.append(_Plan(answer.plan))
        if answer.calls:
            rows = [[str(i), call.brief, _coverage_text(call), call.summary or "-", f"{call.seconds:.1f}s"]
                    for i, call in enumerate(answer.calls, 1)]
            blocks.append(_Table(["#", "Tool call", "Covered", "Found", "Time"], rows, title="What it checked",
                                 code_cols=(1,), prose_cols=(3,), sortable=False))
            blocks += [_Text(call.code, title=f"Call {i}'s code", code=True, collapsed=True)
                       for i, call in enumerate(answer.calls, 1) if call.code]
        if answer.aws_calls:
            rows = [[op, f"{n:,}"] for op, n in sorted(answer.aws_calls.items(), key=lambda kv: (-kv[1], kv[0]))]
            blocks.append(_Table(["AWS call (read-only)", "Calls"], rows, title="AWS reads it made", collapsed=True))
        basis = [_cost_basis(answer)]
        billed = [f"{answer.metrics_read:,} CloudWatch metrics"] if answer.metrics_read else []
        requests = sum(n for op, n in answer.aws_calls.items() if op.startswith("s3:"))
        if requests:
            billed.append(f"{_plural(requests, 'S3 request')}")
        kb_answers = answer.aws_calls.get("bedrock-agent-runtime:RetrieveAndGenerate", 0)
        if kb_answers:
            billed.append(f"{_plural(kb_answers, 'knowledge base answer')}, estimated")
        if billed:
            basis.append(f"Its AWS reads cost {human_money(answer.aws_cost)} ({' and '.join(billed)}); the rest are "
                         "free.")
        blocks.append(_Note(" ".join(basis)))
        if answer.error:
            blocks.append(_Note(answer.error, "warn"))
        nxt = answer_next(answer)
        if answer.charts:
            nxt = [("save_charts()", f"the {_plural(len(answer.charts), 'chart')} as pictures, in {CHARTS_FOLDER}/"),
                   *nxt][:3]
        if nxt:
            blocks.append(_Next(nxt, title=_look_closer(nxt)))
        return blocks

    @_friendly_errors
    def new_conversation(self) -> None:
        """
        Starts a new conversation: the next ask() won't see the questions before it (follow-ups like "why did
        it fail?" need the conversation they follow).
        """
        self.core.new_conversation()
        if self.window is not None:
            self.window.cleared()
        self._show([_Note("New conversation: the next ask() starts fresh.", "ok")])


    @_friendly_errors
    def use_model(  # noqa: PLR0913
        self,
        provider: str | None = None,
        model: str | None = None,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_version: str | None = None,
        chat_model: Any = None,
    ) -> None:
        """
        Switches the model the agent answers with, then says where what it sends goes and how its tokens are priced:
        use_model('bedrock', 'sonnet'), use_model('openai', 'gpt-4.1', api_key=...), use_model('azure', 'prod-gpt-4o',
        endpoint=...), or any LangChain provider. The chat window's ⚙ model does the same by clicking. A new
        conversation starts, and a key or package that's missing is said here, keeping the model it had.
        """
        window = self.window
        if window is not None and window.busy:
            raise _Hint("The chat window is answering a question: wait for it, or press its Stop, then switch.")
        core = self.core
        core.use_model(provider, model, endpoint=endpoint, api_key=api_key, api_version=api_version,
                       chat_model=chat_model)
        if window is not None:
            window.model_changed()
        place = model_place(core.provider, core.model, core.endpoint)
        self._show([_Note(f"Now answering with {core.model} ({provider_info(core.provider).title}): what it sends "
                          f"goes to {place}. A new conversation starts with the next question.", "ok")])

    @_friendly_errors
    def transcript(self) -> None:
        """
        Every question asked so far and its answer, oldest first, with the tool calls, tokens and cost of each,
        and the total. Questions of the same conversation are numbered together.
        """
        history = self.core.history
        if not history:
            self._show([_Title("Transcript"), _Note("Nothing asked yet: ask('Which Lambda failed last night?').")])
            return
        costs = [a.cost for a in history if a.cost is not None]
        blocks: list[Any] = [
            _Title("Transcript", f"{_plural(len(history), 'question')} · "
                                 f"{_plural(len({a.thread for a in history}), 'conversation')}"),
            _Cards([("Questions", f"{len(history):,}"),
                    ("Tool calls", f"{sum(len(a.calls) for a in history):,}"),
                    ("Tokens", f"{sum(a.input_tokens + a.output_tokens for a in history):,}"),
                    ("Model cost (est.)", human_money(sum(costs)) if costs else "unknown")]),
        ]
        threads: dict[str, int] = {}
        for answer in history:
            number = threads.setdefault(answer.thread, len(threads) + 1)
            sub = f"conversation {number} · {_plural(len(answer.calls), 'tool call')} · {_duration(answer.seconds)}"
            if answer.cost is not None:
                sub += f" · {human_money(answer.cost)}"
            blocks.append(_Title(answer.question, sub))
            blocks.append(_Answer(answer.text) if answer.text else
                          _Note(answer.error or "No answer: it stopped before writing one.", "warn"))
            if answer.charts:
                blocks.append(_Charts(answer.charts))
        self._show(blocks)

    @_friendly_errors
    def save_charts(self, folder: str | os.PathLike = CHARTS_FOLDER, *, answer: int = -1) -> None:
        """
        Saves the charts of the last answer as pictures (SVG, or the PNG the code drew, each named after its title) in a
        folder beside the notebook, agent-charts/ unless you name another, to put in a slide or a report; a Vega-Lite
        chart's spec goes beside it (.vl.json). answer= picks an earlier answer (answer=0 is the first asked). A file
        that's there already is never replaced: the new one gets a number.
        """
        paths = self.core.save_charts(folder, answer=answer)
        rows = [[str(path), chart.title, chart.size] for path, chart in zip(paths, self.core.history[answer].charts)]
        self._show([_Title("Charts saved", f"{_plural(len(paths), 'file')} in {paths[0].parent}"),
                    _Table(["File", "Chart", "Size"], rows, path_cols=(0,), sortable=False)])


    def _budget_rows(self) -> list[list[str]]:
        """
        The tools() report's Budget table: each limit per question, and what happens past it.
        """
        core = self.core
        budget = core.budget
        long_logs = "a longer log read is refused; CloudWatch's counts still work"
        if core.interrupt_on:
            long_logs = "a longer log read asks first: all of it, the last days only, or none"
        limits = [
            ["Tool calls", f"{budget.tool_calls:,}", "tools stop running; it answers with what it has"],
            ["Time", _duration(budget.seconds), "the same"],
            ["Model cost", human_money(budget.usd) if budget.usd is not None else "none",
             "the same (only when its tokens' prices are known: model_prices=, or Bedrock's list prices)"],
            ["Steps", f"{budget.steps:,}", "the agent stops (LangGraph's recursion limit)"],
            ["Log window", f"{budget.log_days:g} days", long_logs],
            ["S3 keys", f"{budget.s3_keys:,}", "a listing stops there and says so: its counts cover the keys it read"],
        ]
        if core.interrupt_on:
            limits.append(["Ask first", f"{budget.ask_keys:,} keys", "a listing past it asks: go on, stop there, "
                                                                     "or skip it"])
            if core.workspace is not None:
                limits.append(["Ask first", human_size(budget.ask_bytes), "a save past it asks: all of it, what "
                                                                          "fits, or none"])
        if core.workspace is not None:
            limits += [
                ["Python runs", f"{budget.python_runs:,}", "run_python stops running; it answers with what it has"],
                ["One run", _duration(budget.python_seconds), "the workspace stops the code"],
                ["Workspace", human_size(budget.workspace_bytes), "a save that would go past it is refused"],
                ["Charts", f"{budget.charts:,}", "show_chart puts no more in the answer"],
            ]
        if any(getattr(toolkit, "read_files", False) for toolkit in core.toolkits):
            limits += [
                ["File read", human_size(budget.file_bytes), "preview_file reads a text or JSON file's start only"],
                ["File content", f"{budget.file_chars:,} characters", "what preview_file sends the model is cut there"],
            ]
        if any(toolkit.name == "bedrock_kb" for toolkit in core.toolkits):
            limits.append(["KB searches", f"{budget.kb_searches:,}", "search_kb, file_chunks and answer_kb stop running"])
        return limits

    @_friendly_errors
    def tools(self) -> None:
        """
        What the agent can call to answer: each tool, what it tells the model and the AWS reads it makes, then
        the model it answers with and who serves it, the regions and time zone it assumes, its budget per question,
        and where what it sends the model goes. Every AWS call goes through a guard that refuses anything but a read.
        """
        core = self.core
        is_set_up = bool(core.provider) or core.chat_model is not None
        model = core.model or "not set up"
        if core.provider:
            provider = provider_info(core.provider).title
        else:
            provider = type(core.chat_model).__name__ if core.chat_model is not None else "not set up"
        try:
            regions = ", ".join(core.default_regions())
        except (ValueError, BotoCoreError):
            regions = "not set (AWS_DEFAULT_REGION)"
        blocks: list[Any] = [
            _Title("What the agent can do", ", ".join(t.title for t in core.toolkits) + ", read-only"),
            _Cards([("Model", model, *([] if is_set_up else ["warn"])), ("Provider", provider),
                    ("Regions", regions), ("Time zone", _zone_label(core.zone)), ("Tools", f"{len(core.tools())}")]),
        ]
        reads = core.reads
        rows = [[fn.__name__, _first_line(fn.__doc__), ", ".join(reads.get(fn.__name__, ()))] for fn in core.tools()]
        blocks.append(_Table(["Tool", "What it does", "AWS reads"], rows, title="Tools", prose_cols=(1,),
                             sortable=False))
        limits = self._budget_rows()
        has_kb = any(toolkit.name == "bedrock_kb" for toolkit in core.toolkits)
        blocks.append(_Table(["Limit", "Per question", "What happens past it"], limits, title="Budget",
                             sortable=False))
        never = " ".join(t.privacy for t in core.toolkits if t.privacy)
        if not is_set_up:
            blocks.append(_Note(no_model_hint(core.model_id), "warn"))
        place = f" It goes to {model_place(core.provider, model, core.endpoint)}." if is_set_up else ""
        stay = ("" if core.provider == "bedrock" else
                " To keep it in AWS, answer with a model on Amazon Bedrock: AgentView(provider='bedrock').")
        blocks.append(_Note(
            "Sent to the model: your question and what the tools return (names, settings, counts, error messages and "
            "log lines, with anything that looks like a key or password hidden)." + (f" {never}" if never else "")
            + place + stay))
        together = ("ask('Which Lambda processes files uploaded to acme-raw?')", "S3 and Lambda together")
        if has_kb:
            together = ("ask('Why does support-docs miss the refund policy?')",
                        "a knowledge base's syncs, files and searches")
        blocks.append(_Next([
            ("ask('Which Lambda failed last night?')", "an answer, and what it checked"),
            together,
            ("AgentView(budget={'tool_calls': 50, 'log_days': 7})", "a bigger budget per question"),
        ]))
        self._show(blocks)
