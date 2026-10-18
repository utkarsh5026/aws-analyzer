"""
Errors in a function's logs: each line's request ID and cause, grouped, and what to do about each.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Iterable

from ..._kit.fmt import _count, _plural, human_age
from ..._kit.text import _clip

from ..models import ErrorGroup, ErrorReport, LogEvent
from .text import _cli, _window
from .findings import _longer, _next_memory, _times


# ------------------------------------------------------------------ log lines

_REQUEST_ID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?$")

_TIMED_OUT_RE = re.compile(r"Task timed out after ([\d.]+) seconds")

_ERROR_TYPE_RE = re.compile(
    r"^((?:[A-Za-z_]\w*\.)*(?:[A-Za-z_]\w*?(?:Error|Exception|Exit|Fault|Failure|Timeout|Interrupt)|Error|Exception)"
    r"|Runtime\.\w+)\s*(?::\s*(.*))?$",
    re.S,
)

_LEVEL_RE = re.compile(r"^\[(ERROR|CRITICAL|FATAL)\]\s*(.*)$", re.S)

_REPORT_RE = re.compile(r"^REPORT RequestId:\s*(\S+)")

_REPORT_FIELD_RE = re.compile(
    r"(Billed Restore Duration|Restore Duration|Billed Duration|Init Duration|Duration|Memory Size|Max Memory Used): "
    r"([\d.]+)"
)

_LIFECYCLE_RE = re.compile(r"^(START|END) RequestId:")

_ACCESS_DENIED_RE = re.compile(r"AccessDenied|not authorized to perform|UnauthorizedOperation|Forbidden", re.I)

_DENIED_ACTION_RE = re.compile(r"not authorized to perform:?\s*([\w-]+:[\w*]+)(?:\s+on resource:?\s*([^\s,;]+))?")

_THROTTLED_RE = re.compile(r"Throttl|TooManyRequests|Rate exceeded|SlowDown|ProvisionedThroughputExceeded")

_CONNECT_RE = re.compile(r"ConnectTimeout|EndpointConnectionError|ETIMEDOUT|ECONNREFUSED|Connect timeout|timed out")

_LOAD_ERRORS = ("Runtime.ImportModuleError", "Runtime.HandlerNotFound", "Runtime.UserCodeSyntaxError")


def request_id_of(message: str) -> str | None:
    """
    The request ID a log line belongs to (Lambda writes it in each line it adds), or None.
    """
    if message.lstrip().startswith("{"):
        try:
            doc = json.loads(message)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
            found = doc.get("requestId") or doc.get("AWSRequestId") or record.get("requestId")
            if found:
                return str(found)
    match = _REQUEST_ID_RE.search(message)
    return match.group(0) if match else None


def _typed(text: str) -> tuple[str, str] | None:
    """
    'KeyError: 'x'' -> ('KeyError', "KeyError: 'x'"); None when the line doesn't start with an error type.
    """
    match = _ERROR_TYPE_RE.match(text.strip())
    if not match:
        return None
    return match.group(1).split(".")[-1] if not match.group(1).startswith("Runtime.") else match.group(1), text.strip()


def _json_error(text: str) -> tuple[str, str] | None:
    """
    An error in a JSON log line: Lambda's own (errorType / errorMessage) or a record at level ERROR.
    """
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
    if str(doc.get("type", "")).startswith("platform."):
        status = record.get("status")
        if doc.get("type") in ("platform.runtimeDone", "platform.initRuntimeDone") and status == "timeout":
            return "Timeout", "Task timed out"
        if status in ("error", "failure") and record.get("errorType"):
            kind = "Out of memory" if str(record["errorType"]).endswith("OutOfMemory") else str(record["errorType"])
            return kind, str(record["errorType"])
        return None
    inner = doc.get("message")
    payload = inner if isinstance(inner, dict) else doc
    error_type = payload.get("errorType") or doc.get("errorType")
    error_message = payload.get("errorMessage") or doc.get("errorMessage")
    if error_type:
        text = f"{error_type}: {error_message}" if error_message else str(error_type)
        kind = str(error_type)
        return (kind if kind.startswith("Runtime.") else kind.split(".")[-1]), text.splitlines()[0][:500]
    if str(doc.get("level", "")).upper() in ("ERROR", "CRITICAL", "FATAL"):
        message = inner if isinstance(inner, str) else json.dumps(inner)
        return "Logged error", (message or "").splitlines()[0][:500] if message else "Logged error"
    return None


def classify_error(message: str) -> tuple[str, str] | None:
    """
    (kind, summary) for a log line that reports an error, or None for any other line. Kinds: 'Timeout',
    'Out of memory', 'Runtime exited', an exception type ('KeyError', 'Runtime.ImportModuleError', 'TypeError',
    ...) or 'Logged error' (an ERROR line the code wrote). REPORT lines are skipped: they repeat what the line
    before them said.
    """
    text = message.strip()
    if not text or text.startswith(("REPORT RequestId", "INIT_REPORT", "START RequestId", "END RequestId")):
        return None
    if text.startswith("{"):
        return _json_error(text)
    if match := _TIMED_OUT_RE.search(text):
        return "Timeout", f"Task timed out after {match.group(1)} seconds"
    if "Runtime.OutOfMemory" in text or "signal: killed" in text:
        return "Out of memory", "Runtime exited: out of memory (signal: killed)"
    if "Runtime exited with error" in text or text.startswith("Runtime.ExitError"):
        detail = re.search(r"Runtime exited with error: ([^\n]+)", text)
        return "Runtime exited", f"Runtime exited with error: {detail.group(1).strip()}" if detail else "Runtime exited"
    if match := _LEVEL_RE.match(text):  # Python: "[ERROR] KeyError: 'x'" or "[ERROR]\t<time>\t<request>\tmessage"
        rest = match.group(2)
        parts = rest.split("\t")
        if len(parts) >= 3 and _TIMESTAMP_RE.match(parts[0].strip()):
            rest = "\t".join(parts[2:])
        first = (rest.strip().splitlines() or [""])[0].strip()
        return _typed(first) or ("Logged error", first[:500] or match.group(1))
    parts = text.split("\t")  # Node.js and others: "<time>\t<request>\tERROR\tmessage"
    if len(parts) >= 4 and _TIMESTAMP_RE.match(parts[0].strip()) and parts[2].strip() in ("ERROR", "FATAL"):
        body = "\t".join(parts[3:]).strip()
        start = body.find("{")
        if start >= 0:
            found = _json_error(body[start:])
            if found:
                return found
        first = (body.splitlines() or [""])[0].strip()
        return _typed(first) or ("Logged error", first[:500])
    if text.startswith("Traceback (most recent call last)"):
        last = text.splitlines()[-1].strip()
        return _typed(last) or ("Logged error", last[:500])
    if re.match(r"^(ERROR|FATAL|CRITICAL)\b", text) or text.startswith(("panic: ", "Exception in thread")):
        first = text.splitlines()[0].strip()
        return ("panic" if first.startswith("panic: ") else "Logged error"), first[:500]
    return None


def _phrase(search: str | None) -> str | None:
    """
    The exact text a plain search= looks for (None for a filter pattern with its own syntax), so lines can be
    checked again here.
    """
    text = str(search or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"' and '"' not in text[1:-1]:
        return text[1:-1]
    return text if text and not (text.startswith(("{", "[", "?", "-")) or " ?" in text) else None


def _filter_pattern(search: str | None) -> str | None:
    """
    search= as a CloudWatch Logs filter pattern: plain text becomes an exact, case-sensitive phrase; a pattern
    ('?ERROR ?WARN', '{ $.level = "ERROR" }') passes through as it is.
    """
    if not search:
        return None
    text = str(search).strip()
    if text.startswith(("{", "[", "?", "-", '"')) or " ?" in text:
        return text
    return '"' + text.replace('"', '\\"') + '"'


def _error_key(text: str) -> str:
    """
    A message with what changes between occurrences blanked out (IDs, numbers), so repeats group together.
    """
    text = _REQUEST_ID_RE.sub("<id>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", text)
    return re.sub(r"\d+(?:\.\d+)?", "N", text)[:300]


def group_errors(events: Iterable[LogEvent]) -> list[ErrorGroup]:
    """
    Log events that report errors, grouped by cause (kind and message, numbers and IDs aside), most frequent
    first. Lines that aren't errors are skipped.
    """
    groups: dict[tuple[str, str], ErrorGroup] = {}
    seen: dict[tuple[str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for event in events:
        found = classify_error(event.message)
        if not found:
            continue
        kind, summary = found
        key = (kind, _error_key(summary))
        group = groups.get(key)
        if group is None:
            group = groups[key] = ErrorGroup(kind, summary, 0, event.time, event.time, event.message.strip())
        group.count += 1
        group.first = min(group.first, event.time)
        if event.time >= group.last:
            group.last, group.message, group.example = event.time, summary, event.message.strip()
        if event.request_id:
            seen[key].append((event.time, event.request_id))
    for key, group in groups.items():
        ids = [rid for _, rid in sorted(seen[key], reverse=True)]
        group.request_ids = list(dict.fromkeys(ids))[:5]
    return sorted(groups.values(), key=lambda g: (-g.count, -g.last.timestamp()))


def error_findings(report: ErrorReport) -> list[tuple[str, str]]:
    """
    What the errors in a window mean and what to do: timeouts, out of memory, code that can't load, missing
    permissions, throttled or unreachable services, and the most common error otherwise.
    """
    fn = report.function
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    covered: set[int] = set()
    for i, g in enumerate(report.groups[:8]):  # the most frequent causes; the table has the rest
        see = f" logs('{name}', request_id='{g.request_ids[0]}') shows that whole run." if g.request_ids else ""
        when = f"the last {human_age(g.last, report.until)}"
        them = "it" if g.count == 1 else "them"
        if g.kind == "Timeout":
            found.append(("warn", (
                f"{_plural(g.count, 'run')} timed out at the {fn.timeout} s limit ({when}): Lambda stopped {them} part "
                f"way. If runs need longer: {_longer(fn)}; if they shouldn't take that long, performance('{name}') "
                f"shows the slowest runs.{see}"
            )))
        elif g.kind == "Out of memory":
            found.append(("warn", (
                f"{_plural(g.count, 'run')} ran out of memory at {fn.memory:,} MB ({when}): Lambda killed {them}. Give "
                f"it more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(fn.memory)}; "
                f"performance('{name}') shows what the other runs use.{see}"
            )))
        elif g.kind in _LOAD_ERRORS:
            found.append(("warn", (
                f"Its code can't be loaded ({g.message}): calls fail before any of it runs. Usually a package "
                f"missing from the deployment package or a layer, or a handler setting ({fn.handler or '-'}) that "
                f"doesn't match a file and function. code('{name}') shows what's in the package."
            )))
        elif _ACCESS_DENIED_RE.search(g.example):
            denied = _DENIED_ACTION_RE.search(g.example)
            role = (fn.role or "").split("/")[-1] or "its execution role"
            if denied:
                on = f" on {denied.group(2).rstrip('.')}" if denied.group(2) else ""
                what = f"its role {role} isn't allowed {denied.group(1)}{on}"
            else:
                what = f"{_clip(g.message, 160)}; its role {role} needs the permission the message names"
            found.append(("warn", (
                f"Its code was refused access {_times(g.count)} ({when}): {what}. Add the permission to the role's "
                f"policy in IAM.{see}"
            )))
        elif _THROTTLED_RE.search(g.example):
            found.append(("info", (
                f"Calls it makes to another service were throttled {_times(g.count)} ({when}): "
                f"{_clip(g.message, 160)}. Retry with backoff, or ask that service for a higher limit.{see}"
            )))
        elif fn.vpc_id and _CONNECT_RE.search(g.example):
            found.append(("info", (
                f"It couldn't reach a service {_times(g.count)} ({when}): {_clip(g.message, 160)}. It runs in "
                f"VPC {fn.vpc_id}, which reaches the internet and AWS APIs only through a NAT gateway or VPC "
                f"endpoints.{see}"
            )))
        else:
            continue
        covered.add(i)
    uncovered = [g for i, g in enumerate(report.groups) if i not in covered]
    if uncovered:
        g = uncovered[0]
        see = f" logs('{name}', request_id='{g.request_ids[0]}') shows the whole run." if g.request_ids else ""
        found.append(("warn" if g.kind != "Logged error" else "info", (
            f"The most common {'error' if not covered else 'other error'} happened {_times(g.count)} (the last "
            f"{human_age(g.last, report.until)}): {_clip(g.message, 160).rstrip('.')}.{see}"
        )))
    m = report.metrics
    window = _window((report.until - report.since).total_seconds())
    if m is not None and m.errors >= 1 and not report.groups and "logs" not in report.errors:
        found.append(("info", (
            f"CloudWatch counted {_count(round(m.errors))} failed calls in the last {window}, but no log line looked "
            f"like an error: the code may fail without logging it, or its logging is off. logs('{name}') shows what "
            "it did log."
        )))
    if m is not None and m.throttles >= 1:
        found.append(("warn", (
            f"{_count(round(m.throttles))} calls were throttled in the last {window} (throttled calls never run, so "
            f"they log nothing): more arrived than it may run at once. function_info('{name}') shows its concurrency."
        )))
    return found
