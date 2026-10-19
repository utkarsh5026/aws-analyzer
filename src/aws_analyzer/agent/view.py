"""
AgentView: the agent's notebook UI. ask() shows the answer first, then what it checked to get it (each tool call
with its window and regions, what it couldn't read or was refused, the AWS reads, the tokens and the cost) and the
View commands to look closer; transcript() and tools() show the questions so far and what the agent can call.

Its reports sit in a box of the class _ROOT, which scopes its stylesheet's rules, and each title starts with
_BADGE, so its reports are easy to tell from other analyzers'.
"""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from typing import Any

from botocore.exceptions import BotoCoreError

from .._bedrock.blocks import _Answer
from .._bedrock.prices import _per_million, short_model
from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from .._kit.fmt import _duration, _fmt_dt, _plural, _utcnow, _zone_name, human_money
from .._kit.render import _base_css, _report_html
from .._kit.view import _BaseView, _friendly_errors
from .analyzer import AgentAnalyzer
from .answers import AgentAnswer, ToolCall, answer_findings, answer_next
from .llm import model_place, no_model_hint, provider_info

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
.agt .ans .pre .lang{position:absolute;top:6px;right:12px;font-size:10px;font-weight:600;text-transform:uppercase;opacity:.5}
.agt .ans .mdt{overflow-x:auto;margin:.45em 0 .8em;border:1px solid rgba(127,127,127,.22);border-radius:10px}
.agt .ans table{border-collapse:collapse;font-size:13px;line-height:1.45;width:100%}
.agt .ans th,.agt .ans td{border-bottom:1px solid rgba(127,127,127,.22);padding:6px 11px;text-align:left;vertical-align:top}
.agt .ans th{background:rgba(127,127,127,.06);font-weight:600}
.agt .ans tbody tr:last-child td{border-bottom:0}
.agt .ans th.r,.agt .ans td.r{text-align:right;font-variant-numeric:tabular-nums}"""
_CSS = _base_css(_ROOT, _EXTRA_CSS)
_BADGE = "🤖 Agent"


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws.
    """
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)


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
        "💬 Ask": ("ask", "new_conversation"),
        "🔎 Look back": ("transcript",),
        "🧰 What it can do": ("tools",),
        "❓ Help": ("help",),
    }
    _START = (
        ("ask('Which Lambda failed last night?')", "the answer, what it checked, and what to run next"),
        ("ask('Why did it fail?')", "a follow-up: it remembers the conversation"),
        ("ask('Did the files uploaded to acme-raw last night get processed?')", "S3 and Lambda together"),
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
        get it: each tool call with its time window and regions, what it couldn't read or was refused, the AWS reads,
        the tokens and what it cost, and the LambdaView and S3View commands to look closer. Follow-up questions continue the
        conversation; new=True starts a new one. The kernel's stop button stops it, and the report shows what it
        found until then.
        """
        with self._progress() as tick:
            answer = self.core.ask(question, new=new, progress=tick)
        self._show(self._answer_blocks(answer))

    def _answer_blocks(self, answer: AgentAnswer) -> list[Any]:
        core = self.core
        failed = sum(1 for c in answer.calls if c.error)
        sub = " · ".join(filter(None, [
            answer.model or "model not named",
            _plural(len(answer.calls), "tool call"),
            _duration(answer.seconds),
            "follow-up" if sum(1 for a in core.history if a.thread == answer.thread) > 1 else "",
        ]))
        blocks: list[Any] = [_Title(answer.question, sub)]
        if answer.text:
            blocks.append(_Answer(answer.text))
        elif not answer.error:
            blocks.append(_Note("The agent stopped before it wrote an answer: the table below shows what it read.",
                                "warn"))
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
        blocks.append(_Findings(answer_findings(answer, core.budget)))
        if answer.calls:
            rows = [[str(i), call.call, _coverage_text(call), call.summary or "-", f"{call.seconds:.1f}s"]
                    for i, call in enumerate(answer.calls, 1)]
            blocks.append(_Table(["#", "Tool call", "Covered", "Found", "Time"], rows, title="What it checked",
                                 code_cols=(1,), prose_cols=(3,), sortable=False))
        if answer.aws_calls:
            rows = [[op, f"{n:,}"] for op, n in sorted(answer.aws_calls.items(), key=lambda kv: (-kv[1], kv[0]))]
            blocks.append(_Table(["AWS call (read-only)", "Calls"], rows, title="AWS reads it made", collapsed=True))
        basis = [_cost_basis(answer)]
        billed = [f"{answer.metrics_read:,} CloudWatch metrics"] if answer.metrics_read else []
        requests = sum(n for op, n in answer.aws_calls.items() if op.startswith("s3:"))
        if requests:
            billed.append(f"{_plural(requests, 'S3 request')}")
        if billed:
            basis.append(f"Its AWS reads cost {human_money(answer.aws_cost)} ({' and '.join(billed)}); the rest are "
                         "free.")
        blocks.append(_Note(" ".join(basis)))
        if answer.error:
            blocks.append(_Note(answer.error, "warn"))
        nxt = answer_next(answer)
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
        self._show([_Note("New conversation: the next ask() starts fresh.", "ok")])


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
        self._show(blocks)


    @_friendly_errors
    def tools(self) -> None:
        """
        What the agent can call to answer: each tool, what it tells the model and the AWS reads it makes, then
        the model it answers with and who serves it, the regions and time zone it assumes, its budget per question,
        and where what it sends the model goes. Every AWS call goes through a guard that refuses anything but a read.
        """
        core = self.core
        budget = core.budget
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
        blocks.append(_Table(["Limit", "Per question", "What happens past it"], [
            ["Tool calls", f"{budget.tool_calls:,}", "tools stop running; it answers with what it has"],
            ["Time", _duration(budget.seconds), "the same"],
            ["Model cost", human_money(budget.usd) if budget.usd is not None else "none",
             "the same (only when its tokens' prices are known: model_prices=, or Bedrock's list prices)"],
            ["Steps", f"{budget.steps:,}", "the agent stops (LangGraph's recursion limit)"],
            ["Log window", f"{budget.log_days:g} days", "a longer log read is refused; CloudWatch's counts still work"],
            ["S3 keys", f"{budget.s3_keys:,}", "a listing stops there and says so: its counts cover the keys it read"],
        ], title="Budget", sortable=False))
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
        blocks.append(_Next([
            ("ask('Which Lambda failed last night?')", "an answer, and what it checked"),
            ("ask('Which Lambda processes files uploaded to acme-raw?')", "S3 and Lambda together"),
            ("AgentView(budget={'tool_calls': 50, 'log_days': 7})", "a bigger budget per question"),
        ]))
        self._show(blocks)
