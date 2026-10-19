"""
What a question gives back (AgentAnswer, with a ToolCall per tool call), and what the reader should know about
how it was reached: answer_findings() and the commands worth running next, answer_next(). All pure: no AWS, no
model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.fmt import _duration, _fmt_dt, _plural, human_money
from .._kit.render import _call
from .budget import Budget
from .llm import Rates


@dataclass
class ToolCall:
    """
    One call a tool made for a question: what it was asked, what it covered and found, or why it didn't run.

        summary   one line: what it found
        coverage  its window, regions, whether it was cut short, ...
        error     why it failed: 'AccessDeniedException; needs logs:FilterLogEvents'
        refused   why it wasn't run: 'tool_calls', 'time', 'cost', 'log_window' or 'stopped'
        next      View calls that show more, and what they show
        first     the tool's first parameter when it has no default, which the call is written without the name of
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    summary: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    refused: str | None = None
    next: list[tuple[str, str]] = field(default_factory=list)
    first: str | None = "function"

    @property
    def ok(self) -> bool:
        return self.error is None and self.refused is None

    @property
    def call(self) -> str:
        """
        The call as the model made it: "function_errors('orders-etl', since='2026-10-15T12:30Z')".
        """
        args = {k: v for k, v in self.args.items() if v not in ("", None)}
        first = args.pop(self.first, None) if self.first else None
        return _call(self.name, first, **args) if first is not None else _call(self.name, **args)


@dataclass
class AgentAnswer:
    """
    One question and its answer, with every tool call it made, the AWS reads, the tokens and what it cost.

        text          the answer, in markdown
        aws_calls     the AWS reads it made: 'logs:FilterLogEvents' -> 12
        blocked       AWS calls the read-only guard refused
        metrics_read  CloudWatch metrics requested (GetMetricData bills per 1,000)
        aws_cost      USD for those metrics ($0.01 per 1,000 at list price) and S3's requests; the rest are free
        model         the deployment or model that answered
        provider      who served it: 'bedrock', 'openai', 'azure', another LangChain provider, or '' for a chat model
                      passed in
        cost          estimated USD for the model's tokens; None when its prices aren't known
        rates         the prices `cost` is at (Rates: per 1M input and output tokens, yours or Bedrock's list prices)
        stopped       None when it finished, else 'tool_calls', 'time', 'cost', 'steps', 'stopped' or 'error'
        error         what went wrong with the model or the agent loop, when something did
        thread        the conversation it belongs to
        exception     the error itself, to raise when debugging
    """

    question: str
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    aws_calls: dict[str, int] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    metrics_read: int = 0
    aws_cost: float = 0.0
    model: str = ""
    provider: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    rates: Rates | None = None
    seconds: float = 0.0
    stopped: str | None = None
    error: str | None = None
    thread: str = ""
    asked: datetime | None = None
    exception: BaseException | None = field(default=None, repr=False)


def answer_findings(answer: AgentAnswer, budget: Budget | None = None) -> list[tuple[str, str]]:
    """
    What the reader should know about how an answer was reached: where it stopped short, what it couldn't read or
    was refused, a call the guard blocked, and an answer that used no tool. (level, message) pairs, each with what
    to do about it.
    """
    budget = budget or Budget()
    found: list[tuple[str, str]] = []
    stops = {
        "tool_calls": f"Stopped at the budget of {_plural(budget.tool_calls, 'tool call')}: the answer leaves out what "
                      "it didn't get to check. Ask a narrower question, or raise the budget: "
                      f"AgentView(budget={{'tool_calls': {budget.tool_calls * 2}}}).",
        "time": f"Stopped at the time budget ({_duration(budget.seconds)}): the answer covers what it read before then. "
                f"Raise it with AgentView(budget={{'seconds': {int(budget.seconds * 2)}}}).",
        "cost": f"Stopped at the cost budget ({human_money(budget.usd)} a question): the answer covers what it read "
                f"before then. Raise it with AgentView(budget={{'usd': {(budget.usd or 1) * 2:g}}}).",
        "steps": f"Stopped after {budget.steps} steps (the agent's recursion limit): it was still working, so the answer "
                 "may be incomplete. Ask a narrower question.",
        "stopped": "You stopped it: the answer covers what it read before then.",
    }
    if answer.stopped in stops:
        found.append(("warn" if answer.stopped != "stopped" else "info", stops[answer.stopped]))
    if answer.error:
        found.append(("warn", f"The model call failed, so the question wasn't answered: {answer.error}"))
    for op in dict.fromkeys(answer.blocked):
        found.append(("warn", f"Blocked {op}: it would change your account, and the agent only reads. Nothing was "
                              "sent to AWS."))
    for call in answer.calls:
        if call.error:
            found.append(("warn", f"{call.name} couldn't read everything ({call.error}): the answer leaves that "
                                  "out. README lists the read-only IAM permissions each tool needs."))
        if call.refused == "log_window":
            window = call.coverage.get("window") or {}
            days = max(1, round((window.get("hours") or 24 * budget.log_days) / 24))
            found.append(("info", f"{call.call} wasn't run: its {_plural(days, 'day')} of logs are more than the "
                                  f"{budget.log_days:g}-day limit for a log read, which keeps questions fast. "
                                  "CloudWatch's counts cover longer windows; to read the logs too, raise the limit: "
                                  f"AgentView(budget={{'log_days': {days}}})."))
        if call.coverage.get("cut_short") and call.coverage.get("scanned"):
            keys = int(call.coverage["scanned"])
            found.append(("info", f"{call.call} stopped after {keys:,} S3 keys (the budget's s3_keys), in key order: "
                                  "its counts cover those keys only. Ask about a narrower prefix, or raise the limit: "
                                  f"AgentView(budget={{'s3_keys': {max(keys, budget.s3_keys) * 5}}})."))
        elif call.coverage.get("cut_short"):
            reach = call.coverage.get("covered_from")
            found.append(("info", f"{call.name} for {call.args.get('function') or 'the functions'} stopped at its "
                                  "line limit" + (f": it covers {_fmt_dt(reach, zone=True)} to the end of the window"
                                                  if isinstance(reach, datetime) else "")
                                  + ". The newest lines were kept."))
    if not answer.calls and not answer.error and answer.stopped is None:
        found.append(("info", "It answered without calling a tool, so nothing in the answer comes from your account. "
                              "Ask about your functions or buckets, or a time window."))
    return found


def answer_next(answer: AgentAnswer, n: int = 3) -> list[tuple[str, str]]:
    """
    The commands worth running next: the View calls behind the tools the answer used, the most specific first,
    without repeats: one per command and function (errors('etl') once, with the latest window).
    """
    seen: dict[str, tuple[str, str]] = {}
    for call in reversed(answer.calls):
        for command, what in call.next:
            key = re.match(r"(?:\w+\(\)\.)?(\w+)\(('[^']*')?", command)
            seen.setdefault(key.group(0) if key else command, (command, what))
    return list(seen.values())[:n]
