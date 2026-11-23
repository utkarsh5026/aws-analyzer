"""
What invoke() works out without AWS: the event to send, read forgivingly; the log tail Lambda hands back with the
answer, as log lines; what the call cost; and findings that say how the run went and what to try next.
"""

from __future__ import annotations

import ast
import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..helpers import _lambda_time, human_ms
from ..models import Function, Invocation, LogEvent
from .logs import LogRun, _marker, split_runs

TAIL_BYTES = 4096

TAIL_STREAM = "(returned with the answer)"

MAX_EVENT_BYTES = 6 * 1024 * 1024

_MEMORY_TIGHT = 0.9

_TIME_TIGHT = 0.8

_LINE_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")

_TIMED_OUT_RE = re.compile(r"Task timed out after ([\d.]+) seconds")

_STARTS_AS_JSON = ("{", "[", '"')


def _python_literal(text: str) -> Any:
    """
    Text written as a Python dict or list ("{'id': 1, 'ok': True}"), read as one; ValueError when it isn't.
    """
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(
            f"event= looks like JSON but isn't: {exc}. Pass a dict ({{'order_id': 42}}) or JSON text "
            "('{\"order_id\": 42}')"
        ) from None


def event_payload(event: Any) -> bytes:
    """
    The bytes invoke() sends as the event: a dict, list, number or True / False as JSON; JSON text as it is; a
    Python dict or list written as text ("{'id': 1}") turned into JSON; other text as a JSON string; bytes as they
    are; None as {} (what the Lambda console's test sends by default). ValueError when it's over 6 MB or can't be
    turned into JSON.
    """
    if event is None:
        payload = b"{}"
    elif isinstance(event, (bytes, bytearray)):
        payload = bytes(event)
    elif isinstance(event, str):
        payload = _text_payload(event)
    else:
        try:
            payload = json.dumps(event, default=str).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"event= can't be sent as JSON: {exc}") from None
    if len(payload) > MAX_EVENT_BYTES:
        raise ValueError(f"event= is {len(payload):,} bytes; a synchronous call takes at most 6 MB")
    return payload


def _text_payload(text: str) -> bytes:
    """
    event_payload() for text: JSON as it is, a Python literal as JSON, anything else as a JSON string.
    """
    stripped = text.strip()
    if not stripped:
        return b"{}"
    try:
        json.loads(stripped)
        return stripped.encode()
    except ValueError:
        pass
    if stripped.startswith(_STARTS_AS_JSON):
        return json.dumps(_python_literal(stripped), default=str).encode()
    return json.dumps(text).encode()


def decode_tail(log_result: str | None) -> str:
    """
    Invoke's LogResult (base64) -> the text of the run's last 4 KB of log; '' when there's none.
    """
    if not log_result:
        return ""
    try:
        return base64.b64decode(log_result).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def tail_is_whole(tail: str) -> bool:
    """
    Whether the tail holds the run from its START line on: Lambda cuts it to the last 4 KB, so a long log loses its
    beginning.
    """
    return any((_marker(line) or ("",))[0] == "start" for line in tail.splitlines())


def _line_time(message: str) -> datetime | None:
    """
    When a line says it was written: the time Python's and Node's runtimes put before it, or a JSON line's own.
    """
    text = message.lstrip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            return _lambda_time(doc.get("timestamp") or doc.get("time"))
    found = _LINE_TIME_RE.search(text[:80])
    return _lambda_time(found.group(0)) if found else None


def tail_events(tail: str, *, request_id: str, started: datetime) -> list[LogEvent]:
    """
    The tail's lines as LogEvents, oldest first. A line takes the time it says it was written, else the time of the
    line before it (the first: `started`), each a microsecond after the one before, so their order is kept. A tail
    cut short starts mid-line: that line is marked with '…'.
    """
    lines = [line for line in tail.split("\n") if line.strip()]
    if lines and not tail_is_whole(tail) and len(tail.encode()) >= TAIL_BYTES - 64:
        lines[0] = f"…{lines[0]}"
    events: list[LogEvent] = []
    moment = started
    for line in lines:
        said = _line_time(line)
        moment = max(said, moment + timedelta(microseconds=1)) if said else moment + timedelta(microseconds=1)
        events.append(LogEvent(moment, line, TAIL_STREAM, request_id))
    return events


def read_answer(payload: bytes) -> Any:
    """
    What the function returned: JSON read into Python, else the text it returned; None when it returned nothing.
    """
    if not payload:
        return None
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


@dataclass
class Invoked:
    """
    One call invoke() made, and what came back: the function's answer (`answer`, read from JSON when it can be;
    `payload` the bytes), whether it failed (`function_error`: 'Unhandled' when its code raised), its request ID and
    the version that ran, and its log lines (`events`: from CloudWatch Logs when they were there in time, else the
    last 4 KB Lambda returns with the answer, `logs_from` says which). `run` is them as one LogRun, `report` its
    REPORT line (run time, billed time, memory used, cold start) and `cost` what the call cost in USD. `tail` is the
    last 4 KB of its log as Lambda returned it, and `errors` says why CloudWatch Logs couldn't be read ('logs' -> the
    error code). Tail lines carry TAIL_STREAM as their stream, since Lambda doesn't say which one ran it.
    """

    function: Function
    event: bytes
    qualifier: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    status_code: int = 0
    request_id: str = ""
    executed_version: str = ""
    function_error: str | None = None
    payload: bytes = b""
    tail: str = ""
    events: list[LogEvent] = field(default_factory=list)
    logs_from: str = "tail"
    dry_run: bool = False
    cost: float | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.function_error

    @property
    def answer(self) -> Any:
        return read_answer(self.payload)

    @property
    def failure(self) -> dict[str, Any] | None:
        """
        A failed call's answer: {'errorType', 'errorMessage', 'stackTrace'}; None when it didn't fail.
        """
        answer = self.answer
        if not self.function_error or not isinstance(answer, dict):
            return None
        return answer if "errorType" in answer or "errorMessage" in answer else None

    @property
    def run(self) -> LogRun | None:
        """
        Its log lines as one run (START to REPORT); None when it logged nothing.
        """
        runs = split_runs(self.events)
        mine = [run for run in runs if run.request_id == self.request_id]
        return (mine or runs or [None])[0]

    @property
    def report(self) -> Invocation | None:
        run = self.run
        return run.report if run is not None else None

    @property
    def whole_log(self) -> bool:
        return self.logs_from == "cloudwatch" or tail_is_whole(self.tail)

    @property
    def seconds(self) -> float | None:
        """
        How long the call took from here, round trip.
        """
        if self.started is None or self.ended is None:
            return None
        return (self.ended - self.started).total_seconds()


def call_cost(fn: Function, billed_ms: float | None, prices: dict[str, float]) -> float | None:
    """
    What one call cost in USD: its request plus its billed time at its memory and architecture; None without a
    billed time.
    """
    if billed_ms is None:
        return None
    rate = prices["gb_second_arm"] if fn.architecture == "arm64" else prices["gb_second"]
    return billed_ms / 1000 * fn.memory / 1024 * rate + prices["request"] / 1_000_000


def _failure_findings(name: str, failure: dict[str, Any], *, timeout: int) -> list[tuple[str, str]]:
    """
    What a failed call's answer says (errorType, errorMessage): a timeout with the command that raises it, else the
    error with where to look next.
    """
    kind = str(failure.get("errorType") or "Error")
    message = str(failure.get("errorMessage") or "").strip()
    timed_out = _TIMED_OUT_RE.search(message)
    if timed_out or kind == "Sandbox.Timedout":
        longer = min(900, max(timeout * 2, timeout + 10))
        return [("warn", f"It timed out: it was still running when its {timeout} s timeout ran out, so Lambda "
                         f"stopped it. Look in its log lines below for the step it was on, or give it longer: "
                         f"aws lambda update-function-configuration --function-name {name} --timeout {longer}")]
    said = f"{kind}: {_clip_message(message)}" if message else kind
    return [("warn", f"It failed with {said}. The traceback is in its answer and log lines below; "
                     f"how_it_works('{name}') maps what its code does, and errors('{name}') shows whether other "
                     "calls fail the same way.")]


def _clip_message(message: str, most: int = 200) -> str:
    """
    An error message on one line, at most `most` characters.
    """
    flat = " ".join(message.split())
    return flat if len(flat) <= most else f"{flat[:most - 1]}…"


def memory_tight(report: Invocation) -> bool:
    """
    Whether a run used so much of its memory that a bigger event could run it out.
    """
    return bool(report.memory) and report.max_memory >= _MEMORY_TIGHT * report.memory


def time_tight(fn: Function, report: Invocation) -> bool:
    """
    Whether a run that finished took so much of its timeout that a bigger event could time out.
    """
    return report.status != "timeout" and bool(fn.timeout) and report.duration >= _TIME_TIGHT * fn.timeout * 1000


def _run_findings(fn: Function, report: Invocation) -> list[tuple[str, str]]:
    """
    What its REPORT line says about the run: memory or time close to the limit, and a cold start.
    """
    found: list[tuple[str, str]] = []
    if memory_tight(report):
        bigger = min(10_240, report.memory * 2)
        found.append(("warn", f"It used {report.max_memory:,} of its {report.memory:,} MB: close to running out, "
                              "which stops a run with Runtime.OutOfMemory. Give it more memory (CPU grows with it): "
                              f"aws lambda update-function-configuration --function-name {fn.name} "
                              f"--memory-size {bigger}"))
    if time_tight(fn, report):
        found.append(("warn", f"It ran {human_ms(report.duration)} of its {fn.timeout} s timeout: a slightly bigger "
                              f"event would time out. performance('{fn.name}') shows how long its other runs take."))
    if report.init is not None:
        found.append(("info", f"It was a cold start: {human_ms(report.init)} of start-up before your handler ran "
                              "(not billed on managed runtimes). Invoke again within a few minutes for a warm run."))
    return found


def invoke_findings(invoked: Invoked) -> list[tuple[str, str]]:
    """
    How a call went, as ("warn" | "info", message) pairs: a failure or timeout and what to change, memory or time
    close to the limit, a cold start, and a log cut short.
    """
    fn = invoked.function
    found: list[tuple[str, str]] = []
    if invoked.dry_run:
        return [("info", f"Dry run: Lambda checked that you may invoke {fn.name} and ran nothing. "
                         f"invoke('{fn.name}', event) runs it.")]
    failure = invoked.failure
    if failure is not None:
        found += _failure_findings(fn.name, failure, timeout=fn.timeout)
    elif invoked.function_error:
        found.append(("warn", f"It failed ({invoked.function_error} error) without saying why in its answer: "
                              "its log lines below should say."))
    report = invoked.report
    if report is not None:
        found += _run_findings(fn, report)
    if invoked.logs_from == "tail" and not invoked.whole_log:
        found.append(("info", "Its log is longer than the 4 KB Lambda returns with the answer, and CloudWatch Logs "
                              "hadn't got the rest yet: these are its last lines. "
                              f"logs('{fn.name}', request_id='{invoked.request_id}') reads the whole run in a minute."))
    return found
