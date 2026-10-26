"""
How far one question may go (Budget), and the state of the question being answered (_Run): its tool calls, what
the model has cost so far, the stop button, and the clock, which stops while it waits for the user's choice on a big
read. check_budget() is what every tool asks before it runs, and
_REFUSED is what the table of tool calls says about a call that wasn't run.
"""

from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .._kit.fmt import _duration, _plural, human_money

if TYPE_CHECKING:
    from .answers import AgentStep, PlanStep, ToolCall
    from .workspace.charts import Chart


_METRICS_PER_PRICE = 1000


@dataclass
class Budget:
    """
    How far one question may go. A tool that would go past a limit isn't run: it tells the model to answer with
    what it has, and the answer says what stopped it.

        tool_calls  tool calls per question
        seconds     wall-clock time per question, model included
        usd         estimated model cost per question (only when the model's prices are known)
        steps       LangGraph's recursion limit: model calls, tool calls and the harness's own steps together
        log_days    the longest window a log read (errors, logs, performance) may cover
        s3_keys     the most keys one S3 listing may read (find_files, folder_summary, s3_triggers)
        file_bytes  the most of a text or JSON file preview_file reads (tables read only the rows they show)
        file_chars  the most characters of a file's content preview_file hands the model
        python_runs     run_python calls per question (only with a Python workspace)
        python_seconds  the longest one run_python call may take
        workspace_bytes the most the tools may save into the workspace, in all
        charts          the most charts show_chart may put in one answer (only with a Python workspace)
        kb_searches     search_kb, file_chunks and answer_kb calls per question (only with the Bedrock knowledge base toolkit)
        ask_keys        an S3 listing (find_files, folder_summary, save_s3_files) that goes past this many keys asks
                        the user first: go on (up to s3_keys), stop here, or skip it (with interrupt_on)
        ask_bytes       saving more than this into the workspace asks the user first (with interrupt_on)

    With interrupt_on, a log read longer than log_days asks too (go reads it all, smaller the last log_days) instead
    of being refused.
    """

    tool_calls: int = 25
    seconds: float = 300.0
    usd: float | None = 1.00
    steps: int = 200
    log_days: float = 3.0
    s3_keys: int = 100_000
    file_bytes: int = 2 * 1024 * 1024
    file_chars: int = 50_000
    python_runs: int = 10
    python_seconds: float = 30.0
    workspace_bytes: int = 50 * 1024 * 1024
    charts: int = 8
    kb_searches: int = 10
    ask_keys: int = 20_000
    ask_bytes: int = 10 * 1024 * 1024

    @classmethod
    def of(cls, value: Budget | dict[str, Any] | None) -> Budget:
        """
        A Budget from a Budget, a dict of its fields ({'tool_calls': 50}) or None (the defaults).
        """
        if value is None:
            return cls()
        if isinstance(value, Budget):
            return value
        if not isinstance(value, dict):
            raise TypeError("budget= takes a Budget or a dict like {'tool_calls': 50, 'log_days': 7}")
        names = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(value) - names)
        if unknown:
            close = difflib.get_close_matches(unknown[0], names, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(f"budget= has no {unknown[0]!r}.{hint} It takes {', '.join(sorted(names))}")
        return cls(**value)


@dataclass
class _Run:
    """
    One question's state while it's being answered: what the tools did and what's left of the budget. The tools
    of one turn run side by side, so everything they change is changed under `lock`.

        cost          the model's estimated cost so far (USD)
        metrics_read  CloudWatch metrics the tools requested (GetMetricData bills per 1,000)
        aws_cost      USD for those metrics; every other read the tools make is free
        listener      told each AgentStep as it happens (ask()'s on_step), from whichever thread it happens on
        charts        the charts show_chart put in the answer, in the order it showed them
        plan          the plan the model last wrote (write_todos), which the answer keeps
        paused_at     when it paused for the user's choice on a big read (time.monotonic), or None: the wait doesn't
                      count against the time budget
    """

    budget: Budget
    started: float = field(default_factory=time.monotonic)
    stop: threading.Event = field(default_factory=threading.Event)
    calls: list[ToolCall] = field(default_factory=list)
    cost: float = 0.0
    metrics_read: int = 0
    aws_cost: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    listener: Callable[[AgentStep], None] | None = None
    charts: list[Chart] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    paused_at: float | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def pause(self) -> None:
        """
        Stops the clock while the question waits for the user's choice.
        """
        self.paused_at = time.monotonic()

    def go_on(self) -> None:
        """
        Starts the clock again, leaving out the time it waited.
        """
        if self.paused_at is not None:
            self.started += time.monotonic() - self.paused_at
            self.paused_at = None

    def paused_call(self, name: str, args: dict[str, Any]) -> ToolCall | None:
        """
        The paused call a tool runs again for once the user has chosen (LangGraph runs a paused tool again from its
        start), so it stays one call; None for a new call.
        """
        with self.lock:
            for call in self.calls:
                if call.paused and call.name == name and call.args == args:
                    call.paused = False
                    return call
        return None

    def tell(self, step: AgentStep) -> None:
        """
        Tells the listener, if there is one, what the agent just did.
        """
        if self.listener is not None:
            self.listener(step)

    def read_metrics(self, count: int, price: float) -> None:
        """
        Counts `count` CloudWatch metrics a tool requested, at `price` USD per 1,000.
        """
        with self.lock:
            self.metrics_read += count
            self.aws_cost += count / _METRICS_PER_PRICE * price


class _Refused(Exception):
    """
    A tool that won't run: the budget is used up, or the window is too long. Carries what to tell the model.
    """

    def __init__(self, reason: str, message: str, **extra: Any):
        super().__init__(message)
        self.reason, self.message, self.extra = reason, message, extra


_REFUSED = {
    "log_window": "not run: the window is longer than the log limit",
    "tool_calls": "not run: the budget of tool calls was used up",
    "time": "not run: the time budget was used up",
    "cost": "not run: the cost budget was used up",
    "stopped": "not run: stopped",
    "python_runs": "not run: the budget of Python runs was used up",
    "workspace": "not saved: the workspace is full",
    "charts": "not shown: the answer has as many charts as the budget allows",
    "kb_searches": "not run: the budget of knowledge base searches was used up",
    "skipped": "not run: you skipped it",
}


def check_budget(run: _Run, used: int) -> None:
    """
    _Refused when the question has used up its budget (`used` tool calls so far) or was stopped.
    """
    budget = run.budget
    if run.stop.is_set():
        raise _Refused("stopped", "Stopped by the user: answer now with what you found so far.")
    if used >= budget.tool_calls:
        raise _Refused("tool_calls", f"The budget of {_plural(budget.tool_calls, 'tool call')} for this question "
                                     "is used up: answer now with what you found, and say what you couldn't "
                                     "check.")
    if run.elapsed >= budget.seconds:
        raise _Refused("time", f"The time budget ({_duration(budget.seconds)}) for this question is used up: "
                               "answer now with what you found, and say what you couldn't check.")
    if budget.usd is not None and run.cost >= budget.usd:
        raise _Refused("cost", f"The cost budget ({human_money(budget.usd)}) for this question is used up: "
                               "answer now with what you found, and say what you couldn't check.")
