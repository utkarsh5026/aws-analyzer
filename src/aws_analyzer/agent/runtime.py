"""How a tool runs for the agent, whichever toolkit it comes from: the budget is checked first, the call is recorded
on the question, its errors become its result (a tool never raises into the agent loop), and the result is made
JSON-ready, with the report's `_summary` and `_next` taken out before the model sees it."""

from __future__ import annotations

import functools
import inspect
import time
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code, _why
from .._kit.text import _clip
from .answers import ToolCall
from .budget import _REFUSED, _Refused, _Run, check_budget
from .guard import ReadOnlyError
from .results import _plain

if TYPE_CHECKING:
    from .toolkits import Toolkit

COVERAGE = ("window", "regions", "region", "cut_short", "covered_from")  # a result's keys the report shows


def as_tool(fn: Callable[..., dict[str, Any]], toolkit: Toolkit,
            current: Callable[[], _Run]) -> Callable[..., dict[str, Any]]:
    """`fn` (one of `toolkit`'s tools) as the agent calls it: the budget of the question `current()` returns is
    checked first, the call recorded on it, its errors turned into its answer (a resource that isn't there into the
    toolkit's close names, a refused call into the permission it needed), and its result made JSON-ready. The
    signature and docstring stay the tool's, which is how the model learns to call it."""

    @functools.wraps(fn)
    def tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        run = current()
        try:
            arguments = dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
        except TypeError:
            arguments = dict(kwargs)
        call = ToolCall(fn.__name__, arguments)
        started = time.monotonic()
        with run.lock:
            run.calls.append(call)
            used = sum(1 for c in run.calls if c.refused is None) - 1
        try:
            check_budget(run, used)
            result = fn(*args, **kwargs)
        except _Refused as exc:
            call.refused, call.summary = exc.reason, _REFUSED.get(exc.reason, "not run")
            result = {"refused": exc.reason, "message": exc.message, **exc.extra}
        except ClientError as exc:
            try:
                missing = toolkit.not_found(exc, arguments)
            except Exception:  # looking for close names failed too: report the error itself
                missing = None
            if missing is not None:
                result = missing
                call.summary = str(missing.get("error") or "")
            else:
                code = _error_code(exc)
                permission = toolkit.permission_for(exc)
                call.error = _why(code, permission) if permission else code
                result = {"error": call.error,
                          "message": _clip(exc.response.get("Error", {}).get("Message", str(exc)), 300)}
        except ReadOnlyError as exc:
            call.error = str(exc)
            result = {"error": "blocked", "message": str(exc)}
        except (BotoCoreError, ValueError, TypeError) as exc:  # bad input, a region or time it can't read
            call.error = f"{type(exc).__name__}: {_clip(str(exc), 200)}"
            result = {"error": type(exc).__name__, "message": _clip(str(exc), 400)}
        except Exception as exc:  # a tool never brings the agent down; the answer says what failed
            call.error = f"{type(exc).__name__}: {_clip(str(exc), 200)}"
            result = {"error": type(exc).__name__, "message": _clip(str(exc), 400)}
        call.seconds = time.monotonic() - started
        unread = result.get("errors") if isinstance(result.get("errors"), dict) else None
        if unread and not call.error:
            call.error = "; ".join(f"{section}: {why}" for section, why in unread.items())
        call.next = list(result.pop("_next", []) or [])
        call.summary = result.pop("_summary", None) or call.summary or (call.error or "")
        call.coverage = {k: result[k] for k in COVERAGE if k in result}
        return _plain(result)

    return tool
