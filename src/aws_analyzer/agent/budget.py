"""How far one question may go (Budget), and the state of the question being answered (_Run): its tool calls, what
the model has cost so far, and the stop button. check_budget() is what every tool asks before it runs."""

from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .._kit.fmt import _duration, _plural, human_money

if TYPE_CHECKING:
    from .answers import ToolCall


@dataclass
class Budget:
    """How far one question may go. A tool that would go past a limit isn't run: it tells the model to answer with
    what it has, and the answer says what stopped it."""

    tool_calls: int = 25  # tool calls per question
    seconds: float = 300.0  # wall-clock time per question, model included
    usd: float | None = 1.00  # estimated model cost per question (only when the model's prices are known)
    steps: int = 80  # LangGraph's recursion limit: model calls, tool calls and the harness's own steps together
    log_days: float = 3.0  # the longest window a log read (errors, logs, performance) may cover

    @classmethod
    def of(cls, value: Budget | dict[str, Any] | None) -> Budget:
        """A Budget from a Budget, a dict of its fields ({'tool_calls': 50}) or None (the defaults)."""
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
    """One question's state while it's being answered: what the tools did and what's left of the budget. The tools
    of one turn run side by side, so everything they change is changed under `lock`."""

    budget: Budget
    started: float = field(default_factory=time.monotonic)
    stop: threading.Event = field(default_factory=threading.Event)
    calls: list[ToolCall] = field(default_factory=list)
    cost: float = 0.0  # the model's estimated cost so far (USD)
    metrics_read: int = 0  # CloudWatch metrics the tools requested (GetMetricData bills per 1,000)
    aws_cost: float = 0.0  # USD for those metrics; every other read the tools make is free
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def read_metrics(self, count: int, price: float) -> None:
        """Counts `count` CloudWatch metrics a tool requested, at `price` USD per 1,000."""
        with self.lock:
            self.metrics_read += count
            self.aws_cost += count / 1000 * price


class _Refused(Exception):
    """A tool that won't run: the budget is used up, or the window is too long. Carries what to tell the model."""

    def __init__(self, reason: str, message: str, **extra: Any):
        super().__init__(message)
        self.reason, self.message, self.extra = reason, message, extra


_REFUSED = {  # what the table of tool calls says about a call that wasn't run
    "log_window": "not run: the window is longer than the log limit",
    "tool_calls": "not run: the budget of tool calls was used up",
    "time": "not run: the time budget was used up",
    "cost": "not run: the cost budget was used up",
    "stopped": "not run: stopped",
}


def check_budget(run: _Run, used: int) -> None:
    """_Refused when the question has used up its budget (`used` tool calls so far) or was stopped."""
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
