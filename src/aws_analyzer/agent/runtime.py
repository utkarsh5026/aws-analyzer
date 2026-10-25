"""
How a tool runs for the agent, whichever toolkit it comes from: the budget is checked first, the call is recorded
on the question, its errors become its result (a tool never raises into the agent loop), and the result is made
JSON-ready, with the report's `_summary` and `_next` taken out before the model sees it.

A tool can pause the question to ask the user about a big read (ask_user, through its toolkit's ask_user): the pause
leaves the agent loop, the call is kept as paused, and once the user has chosen, LangGraph runs the tool again from its
start, as the same call (_Run.paused_call), its asks answered in the order it made them. _CURRENT is the call a tool is
running for, on the thread it runs on.
"""

from __future__ import annotations

import functools
import inspect
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import ClientError

from .._kit.errors import _error_code, _why
from .._kit.text import _clip
from . import harness
from .answers import AgentStep, BigRead, ToolCall, choice_of
from .budget import _REFUSED, _Refused, _Run, check_budget
from .guard import ReadOnlyError
from .results import _plain

if TYPE_CHECKING:
    from .toolkits import Toolkit

COVERAGE = ("window", "regions", "region", "cut_short", "covered_from", "scanned", "stopped_at")
_ERROR_CHARACTERS = 200
_MESSAGE_CHARACTERS = 400
_AWS_MESSAGE_CHARACTERS = 300


@dataclass
class _Current:
    """
    The call a tool is running for, and how many times it has asked the user so far in this run of it.
    """

    call: ToolCall
    asked: int = 0


_CURRENT: ContextVar[_Current | None] = ContextVar("aws_analyzer_agent_call", default=None)


def ask_user(read: BigRead) -> str:
    """
    Pauses the question to ask the user about a big read, and returns their choice ('go', 'smaller' or 'skip') once
    they've made it; '' when no tool call is running (a tool called outside ask()). The first time, the pause leaves
    the tool and the agent loop; LangGraph then runs the tool again from its start, and the asks it makes again return
    the choices made, in order.
    """
    current = _CURRENT.get()
    if current is None:
        return ""
    call, index = current.call, current.asked
    current.asked += 1
    if index < len(call.asks):
        read = call.asks[index]
    else:
        call.asks.append(read)
    read.choice = choice_of(harness.pause_for(read.as_value(call.key)))
    return read.choice


def as_tool(fn: Callable[..., dict[str, Any]], toolkit: Toolkit,
            current: Callable[[], _Run]) -> Callable[..., dict[str, Any]]:
    """
    `fn` (one of `toolkit`'s tools) as the agent calls it: the budget of the question `current()` returns is
    checked first, the call recorded on it, its errors turned into its answer (a resource that isn't there into the
    toolkit's close names, a refused call into the permission it needed), and its result made JSON-ready. A call
    that pauses to ask the user is kept as paused, and its pause goes on out to the agent loop. The signature and
    docstring stay the tool's, which is how the model learns to call it.
    """

    @functools.wraps(fn)
    def tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        run = current()
        arguments = _asked_for(fn, args, kwargs)
        call = run.paused_call(fn.__name__, arguments) or _new_call(run, fn, toolkit, arguments)
        with run.lock:
            used = sum(1 for c in run.calls if c.refused is None and c is not call)
        started = time.monotonic()
        token = _CURRENT.set(_Current(call))
        try:
            check_budget(run, used)
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if harness.is_pause(exc):
                _paused(run, call, started)
                raise
            result = _failed(exc, call, toolkit, arguments)
        finally:
            _CURRENT.reset(token)
        call.seconds += time.monotonic() - started
        reported = _reported(call, result)
        run.tell(AgentStep("tool_done", call=call))
        return reported

    return tool


def _new_call(run: _Run, fn: Callable[..., Any], toolkit: Toolkit, arguments: dict[str, Any]) -> ToolCall:
    """
    A tool call recorded on the question, and told to its listener.
    """
    call = ToolCall(fn.__name__, arguments, first=_first_parameter(fn), reads=tuple(toolkit.reads.get(fn.__name__, ())))
    with run.lock:
        run.calls.append(call)
    run.tell(AgentStep("tool", call=call))
    return call


def _paused(run: _Run, call: ToolCall, started: float) -> None:
    """
    Keeps a call that paused to ask the user, so it's the same call when it runs again, and tells the listener.
    """
    call.seconds += time.monotonic() - started
    call.paused = True
    run.tell(AgentStep("asking", call=call))


def _asked_for(fn: Callable[..., Any], args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    The arguments a tool was called with, by name, leaving out the ones equal to their default: LangChain passes
    every default, so what's left is what the model asked for.
    """
    try:
        arguments = dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
    except TypeError:
        arguments = dict(kwargs)
    defaults = _defaults(fn)
    return {k: v for k, v in arguments.items() if k not in defaults or v != defaults[k]}


def _failed(exc: Exception, call: ToolCall, toolkit: Toolkit, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    What a tool that raised hands the model, recorded on its call. A tool never raises into the agent loop: a
    refused call says why, an AWS error is the toolkit's to explain (_aws_error), a call the guard blocked says so,
    and anything else (bad input, a region or time it can't read, a bug) says what failed.
    """
    if isinstance(exc, _Refused):
        call.refused, call.summary = exc.reason, _REFUSED.get(exc.reason, "not run")
        return {"refused": exc.reason, "message": exc.message, **exc.extra}
    if isinstance(exc, ClientError):
        return _aws_error(exc, call, toolkit, arguments)
    if isinstance(exc, ReadOnlyError):
        call.error = str(exc)
        return {"error": "blocked", "message": str(exc)}
    call.error = f"{type(exc).__name__}: {_clip(str(exc), _ERROR_CHARACTERS)}"
    return {"error": type(exc).__name__, "message": _clip(str(exc), _MESSAGE_CHARACTERS)}


def _aws_error(exc: ClientError, call: ToolCall, toolkit: Toolkit, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    An AWS error as the tool's result: a resource that isn't there as the toolkit's close names, else the error's
    code with the permission a refused call needed. When looking for close names fails too, whatever it raises, the
    error itself is reported.
    """
    try:
        missing = toolkit.not_found(exc, arguments)
    except Exception:  # noqa: BLE001
        missing = None
    if missing is not None:
        call.summary = str(missing.get("error") or "")
        return missing
    code = _error_code(exc)
    permission = toolkit.permission_for(exc)
    call.error = _why(code, permission) if permission else code
    message = exc.response.get("Error", {}).get("Message", str(exc))
    return {"error": call.error, "message": _clip(message, _AWS_MESSAGE_CHARACTERS)}


def _reported(call: ToolCall, result: dict[str, Any]) -> dict[str, Any]:
    """
    Fills in the call from the tool's result, and returns the result as the model reads it: JSON-ready, with the
    report's `_summary` and `_next` taken out. Sections the analyzer couldn't read (its `errors`) become the call's
    error, and the result's COVERAGE keys (its window, regions, whether it was cut short) are what the report shows.
    """
    unread = result.get("errors") if isinstance(result.get("errors"), dict) else None
    if unread and not call.error:
        call.error = "; ".join(f"{section}: {why}" for section, why in unread.items())
    call.next = list(result.pop("_next", []) or [])
    call.summary = result.pop("_summary", None) or call.summary or (call.error or "")
    call.coverage = {k: result[k] for k in COVERAGE if k in result}
    call.result = _plain(result)
    return call.result


def _defaults(fn: Callable[..., Any]) -> dict[str, Any]:
    """
    A tool's parameters that have a default, and their defaults.
    """
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return {}
    return {p.name: p.default for p in params if p.default is not inspect.Parameter.empty}


def _first_parameter(fn: Callable[..., Any]) -> str | None:
    """
    The name of a tool's first parameter when it has no default ('function', 'location'): the report writes it
    without its name, as the call reads in Python.
    """
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return None
    return params[0].name if params and params[0].default is inspect.Parameter.empty else None
