"""
invoke()'s report, answer first: whether the call worked and what it returned, then how long it ran, the memory it
used and what it cost, then its log lines as one run you can read top to bottom, everything else Lambda returned (its
request ID, version, X-Ray trace, billed and start-up times, headers), with the event that was sent folded at the end.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title
from ..._kit.fmt import _fmt_dt, human_money
from ..._kit.jsontree import _JsonTree

from ..helpers import human_ms
from ..analysis.invoke import Invoked, invoke_extras, invoke_findings, lambda_lines, memory_tight, time_tight
from .logs import _LogRuns

_ANSWER_ITEMS = 50

_CALLS_PRICED = 1_000_000

_EVENT_PREVIEW_CHARS = 60


class _EventName:
    """
    The word `event` in a next step's call, standing for an event too long to write out in it.
    """

    def __repr__(self) -> str:
        return "event"


def _tiny_money(usd: float | None) -> str:
    """
    What one call cost, to two significant digits: 0.0000021 -> '$0.0000021', 0.42 -> '$0.42'.
    """
    if usd is None:
        return "-"
    if usd >= 0.01:
        return human_money(usd)
    if usd <= 0:
        return "$0"
    decimals = 1 - math.floor(math.log10(usd))
    return f"${usd:.{decimals}f}"


def _invoke_cards(invoked: Invoked) -> list[tuple[str, ...]]:
    """
    The numbers that say how the call went: status, run time against the timeout, memory, cold start and cost. A card
    a warning is about takes its tone.
    """
    fn, report = invoked.function, invoked.report
    status = ("Status", "ran", "ok") if invoked.ok else ("Status", "failed", "bad")
    if report is None:
        took = f"{invoked.seconds:.2f} s round trip" if invoked.seconds is not None else "-"
        return [status, ("Run time", took), ("Version", invoked.executed_version or fn.version)]
    memory_tone = "warn" if memory_tight(report) else ""
    time_tone = "bad" if report.status == "timeout" else "warn" if time_tight(fn, report) else ""
    return [
        status,
        ("Run time", f"{human_ms(report.duration)} of {fn.timeout} s", time_tone),
        ("Memory used", f"{report.max_memory:,} of {report.memory:,} MB", memory_tone),
        ("Cold start", human_ms(report.init) if report.init is not None else "no"),
        ("Cost", _tiny_money(invoked.cost)),
        (f"{_CALLS_PRICED:,} like it", human_money(invoked.cost * _CALLS_PRICED) if invoked.cost else "-"),
    ]


def _error_traceback(failure: dict[str, Any]) -> str:
    """
    A failed call's answer as a traceback reads: its error and message, then its stack, a frame a line.
    """
    head = f"{failure.get('errorType') or 'Error'}: {failure.get('errorMessage') or ''}".rstrip(": ")
    stack = failure.get("stackTrace") or []
    frames = [str(frame).rstrip() for frame in stack] if isinstance(stack, list) else [str(stack)]
    return "\n".join([head, *frames])


def _answer_blocks(invoked: Invoked) -> list[Any]:
    """
    What the function returned: a failure as its traceback, JSON as a tree you can fold, anything else as text.
    """
    failure = invoked.failure
    if failure is not None:
        return [_Text(_error_traceback(failure), title="What it returned: the error")]
    answer = invoked.answer
    if answer is None:
        return [_Note("It returned nothing (null).")]
    if isinstance(answer, (dict, list)):
        return [_JsonTree(answer, items=_ANSWER_ITEMS, title="What it returned", name="answer")]
    return [_Text(answer if isinstance(answer, str) else json.dumps(answer), title="What it returned", wrap=True)]


def _event_blocks(invoked: Invoked) -> list[Any]:
    """
    The event that was sent, folded: JSON as a tree, else as the text it was.
    """
    text = invoked.event.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except ValueError:
        return [_Text(text, title="The event it was sent", collapsed=True)]
    if isinstance(value, (dict, list)):
        return [_JsonTree(value, items=_ANSWER_ITEMS, title="The event it was sent", name="event", collapsed=True)]
    return [_Text(text, title="The event it was sent", collapsed=True)]


def _log_blocks(invoked: Invoked) -> list[Any]:
    """
    Its log lines as one run, drawn open; a note when there were none, or when CloudWatch Logs couldn't be read.
    """
    blocks: list[Any] = []
    if "logs" in invoked.errors:
        blocks.append(_Note(f"CloudWatch Logs couldn't be read ({invoked.errors['logs']}), so these are only the last "
                            "4 KB Lambda returned with the answer. Reading the rest needs logs:FilterLogEvents."))
    run = invoked.run
    if run is None:
        blocks.append(_Note("It logged nothing that came back with the answer."))
        return blocks
    where = "from CloudWatch Logs" if invoked.logs_from == "cloudwatch" else "returned with the answer"
    blocks.append(_LogRuns([run], timeout=invoked.function.timeout, opened={run.key},
                           title=f"Its log lines, oldest first ({where})"))
    return blocks


def _extras_blocks(invoked: Invoked) -> list[Any]:
    """
    Everything else that came back with the call, as a table of plain names and values, then the lines Lambda and
    its extensions wrote (start-up, extensions, the REPORT line with its X-Ray trace), folded.
    """
    rows = [[what, value] for what, value in invoke_extras(invoked)]
    blocks: list[Any] = [_Table(["What", "Value"], rows, title="Everything else Lambda returned", sortable=False)]
    platform = lambda_lines(invoked)
    if platform:
        text = "\n".join(event.message.rstrip() for event in platform)
        blocks.append(_Text(text, title="The lines Lambda and its extensions wrote", collapsed=True))
    return blocks


def _event_arg(invoked: Invoked) -> Any:
    """
    The event for the next steps' invoke(...) call: the event itself when it's short, else the word `event`.
    """
    text = invoked.event.decode("utf-8", errors="replace")
    if len(text) > _EVENT_PREVIEW_CHARS:
        return _EventName()
    try:
        return json.loads(text)
    except ValueError:
        return _EventName()


def _invoke_next(invoked: Invoked, call: Callable[..., str]) -> list[tuple[str, str]]:
    """
    What to run next: the same call again, the whole run in the log viewer, and its other runs' errors or times.
    """
    steps = [(call("invoke", _event_arg(invoked)), "run it again")]
    if invoked.request_id:
        steps.append((call("logs", request_id=invoked.request_id), "this run in the log viewer"))
    if invoked.ok:
        steps.append((call("performance"), "how long its other runs take, and the memory it needs"))
    else:
        steps.append((call("errors"), "whether its other calls fail the same way"))
    return steps


def invoke_blocks(invoked: Invoked, *, call: Callable[..., str]) -> list[Any]:
    """
    invoke()'s report as blocks. `call(command, *args, **kwargs)` writes a next step about the function invoked.
    """
    fn = invoked.function
    if invoked.dry_run:
        title = _Title(f"{fn.name}: you may invoke it", f"dry run · checked at {_fmt_dt(invoked.started)}")
        return [title, _Findings(invoke_findings(invoked)), _Next([(call("invoke", _EventName()), "run it")])]
    findings = invoke_findings(invoked)
    verdict = "ran" if invoked.ok else "failed"
    sub = " · ".join(filter(None, [
        f"version {invoked.executed_version}" if invoked.executed_version else "",
        f"request {invoked.request_id}" if invoked.request_id else "",
        f"invoked {_fmt_dt(invoked.started, seconds=True, zone=True)}",
    ]))
    blocks: list[Any] = [_Title(f"{fn.name} {verdict}", sub), _Cards(_invoke_cards(invoked))]
    blocks.append(_Findings(findings, empty="It ran cleanly: no error, and well within its memory and timeout."))
    blocks += _answer_blocks(invoked)
    blocks += _log_blocks(invoked)
    blocks += _extras_blocks(invoked)
    blocks += _event_blocks(invoked)
    blocks.append(_Next(_invoke_next(invoked, call)))
    return blocks
