"""
The Lambda toolkit: ten tools over LambdaAnalyzer, building blocks rather than one per question. Which functions
there are (find_functions), how every function did in a window, from CloudWatch (function_health), one function's
errors, runs, run times, settings and code (function_errors, function_logs, function_performance, function_details,
function_code), and its source read the way coding tools read code: a file's lines like sed -n (function_source),
every file searched like grep -rn (search_code) and a Python name's definition and uses like a language server
(find_symbol). The summarize_* functions turn what LambdaAnalyzer returns into the compact dicts the model reads;
they're pure, so they run on any Function, ErrorReport, LogPage, Performance, FunctionDetail, CodeMap, CodePackage,
CodeSearch or SymbolReport. code_locations() finds where in the package a logged error
happened (its traceback's frames), which is how the model goes from the logs to the lines of code that failed.

TOOL_READS is what each tool reads, for tools() and README: what its LambdaAnalyzer method calls. The analyzer's
types are imported for the type hints only, since the analyzer loads when the first tool runs (_lambda_module).
_PERMISSIONS maps a result's error sections to the permission each needs, and _OVERVIEW_PERMISSIONS the sections of
overview()'s 'region:section' errors.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, human_money, human_size
from ..._kit.text import _clip
from ..budget import Budget, _Refused
from ..results import _iso, _window, _with_permissions, hide_secrets
from .base import Toolkit

if TYPE_CHECKING:
    from ...lambda_functions import (
        CodeMap, CodePackage, CodeSearch, ErrorGroup, ErrorReport, Function, FunctionDetail, FunctionMetrics,
        LambdaAnalyzer, LogPage, LogRun, Performance, SymbolReport, VersionDiff,
    )

TOOL_READS: dict[str, tuple[str, ...]] = {
    "find_functions": ("lambda:ListFunctions", "lambda:ListEventSourceMappings", "lambda:GetAccountSettings",
                       "logs:DescribeLogGroups"),
    "function_health": ("lambda:ListFunctions", "lambda:ListEventSourceMappings", "lambda:GetAccountSettings",
                        "logs:DescribeLogGroups", "cloudwatch:GetMetricData"),
    "function_errors": ("lambda:GetFunction", "logs:DescribeLogGroups", "logs:FilterLogEvents",
                        "cloudwatch:GetMetricData"),
    "function_logs": ("lambda:GetFunction", "logs:DescribeLogGroups", "logs:FilterLogEvents",
                      "logs:DescribeLogStreams"),
    "function_performance": ("lambda:GetFunction", "logs:DescribeLogGroups", "logs:FilterLogEvents"),
    "function_details": ("lambda:GetFunction", "lambda:ListEventSourceMappings", "lambda:GetPolicy",
                         "lambda:GetFunctionUrlConfig", "lambda:GetFunctionEventInvokeConfig", "lambda:ListAliases",
                         "lambda:ListVersionsByFunction", "lambda:ListProvisionedConcurrencyConfigs",
                         "lambda:GetRuntimeManagementConfig", "logs:DescribeLogGroups", "cloudwatch:GetMetricData"),
    "function_code": ("lambda:GetFunction", "the package's download link (HTTPS, from S3)"),
    "function_source": ("lambda:GetFunction", "the package's download link (HTTPS, from S3)"),
    "search_code": ("lambda:GetFunction", "the package's download link (HTTPS, from S3)"),
    "find_symbol": ("lambda:GetFunction", "the package's download link (HTTPS, from S3)"),
    "diff_versions": ("lambda:ListVersionsByFunction", "lambda:GetFunction",
                      "the packages' download links (HTTPS, from S3)"),
    "save_function_code": ("lambda:GetFunction", "the package's download link (HTTPS, from S3)"),
}


def _lambda_module() -> Any:
    """
    aws_analyzer.lambda_functions, which the tools are built on. Loaded when a tool first needs it, so importing
    the agent loads no analyzer.
    """
    from ... import lambda_functions

    return lambda_functions


_NEAR_TIMEOUT = 0.9

_PYTHON_FRAME_RE = re.compile(r'File \\?"/var/task/(?P<file>[^"\\]+)\\?", line (?P<line>\d+)(?:, in (?P<name>[\w<>.]+))?')

_STACK_FRAME_RE = re.compile(r"(?:\bat (?P<name>[\w$.<>]+) \()?(?:file://)?/var/task/(?P<file>[^\s:()\"\\]+):(?P<line>\d+)")

_IN_CODE_FRAMES = 6


def code_locations(text: str) -> list[dict[str, Any]]:
    """
    Where in a function's own code a logged error happened, from the traceback or stack trace in the log line (as
    text, or inside a JSON line's stackTrace): the frames in its deployment package, which Lambda unpacks in
    /var/task/, in the order they were called, so the handler's frame comes first and the line that raised last,
    as {'file', 'line', 'function'} (Python prints its frames in that order; JavaScript and Ruby print the newest
    first, so theirs are turned round). Frames in the runtime, layers or Python itself aren't the package's and
    are left out; a trace longer than _IN_CODE_FRAMES keeps its first and last frames, where the handler called in
    and where it broke. [] when the line has no trace.
    """
    frames = list(_PYTHON_FRAME_RE.finditer(text))
    if not frames:
        frames = list(reversed(list(_STACK_FRAME_RE.finditer(text))))
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in frames:
        place = (match["file"], int(match["line"]))
        if place not in seen:
            seen.add(place)
            found.append({"file": place[0], "line": place[1], "function": match["name"]})
    if len(found) <= _IN_CODE_FRAMES:
        return found
    half = _IN_CODE_FRAMES // 2
    return found[:half] + found[-half:]


def _metrics_dict(m: FunctionMetrics | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {"calls": int(m.invocations), "errors": int(m.errors), "throttles": int(m.throttles),
            "error_rate_pct": round(100 * m.error_rate, 2) if m.error_rate is not None else None,
            "avg_ms": round(m.avg_duration) if m.avg_duration is not None else None,
            "longest_ms": round(m.duration_max) if m.duration_max is not None else None}


def function_row(fn: Function, *, today: date | None = None) -> dict[str, Any]:
    """
    One function's settings as the agent sees them: runtime and where it stands in AWS's support schedule,
    memory, timeout, package and description. Environment variable values are never read; their names are.
    """
    lf = _lambda_module()
    status = lf.runtime_status(fn.runtime, package_type=fn.package_type, today=today)
    return {
        "name": fn.name, "region": fn.region, "runtime": status.label, "runtime_support": status.state,
        "support_ends": status.deprecated.isoformat() if status.deprecated else None, "upgrade_to": status.upgrade,
        "memory_mb": fn.memory, "timeout_s": fn.timeout, "architecture": fn.architecture,
        "package": fn.package_type, "code_size": human_size(fn.code_size), "last_modified": fn.last_modified,
        "description": _clip(fn.description, 120) if fn.description else "", "handler": fn.handler,
        "state": fn.state, "env_var_names": fn.env_names,
    }


def summarize_functions(functions: list[Function], regions: list[str], errors: dict[str, str] | None = None,
                        *, limit: int = 60) -> dict[str, Any]:
    """
    find_functions()'s answer: every function found, the newest-changed first (up to `limit`), by region.
    """
    rows = sorted(functions, key=lambda f: f.last_modified or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    out = {
        "regions": regions, "count": len(functions),
        "functions": [{k: v for k, v in function_row(f).items() if k in (
            "name", "region", "runtime", "runtime_support", "memory_mb", "timeout_s", "last_modified", "description")}
            for f in rows[:limit]],
    }
    if len(rows) > limit:
        out["more"] = f"{len(rows) - limit:,} more not listed: narrow with match='prefix-*'"
    if errors:
        out["errors"] = _with_permissions(errors, _OVERVIEW_PERMISSIONS)
    return out


def _health_groups(functions: list[Function],
                   metrics: dict[str, FunctionMetrics]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """
    (failed, throttled, near_timeout, not_called): each function with CloudWatch numbers, as a row of them, in
    every group it belongs to. Near its timeout is a longest run of _NEAR_TIMEOUT of the timeout or more.
    """
    failed, throttled, near, idle = [], [], [], []
    for fn in functions:
        m = metrics.get(fn.arn)
        if m is None:
            continue
        row = {"name": fn.name, "region": fn.region, **(_metrics_dict(m) or {}), "timeout_ms": fn.timeout * 1000}
        if m.last_error is not None:
            row["last_error_period"] = m.last_error
        if m.errors:
            failed.append(row)
        if m.throttles:
            throttled.append(row)
        if m.duration_max is not None and m.duration_max >= _NEAR_TIMEOUT * fn.timeout * 1000:
            near.append(row)
        if not m.invocations:
            idle.append(fn.name)
    return failed, throttled, near, idle


def summarize_health(functions: list[Function], metrics: dict[str, FunctionMetrics],  # noqa: PLR0913
                     start: datetime, end: datetime, regions: list[str], errors: dict[str, str] | None = None,
                     *, limit: int = 25) -> dict[str, Any]:
    """
    function_health()'s answer: CloudWatch's counts for every function in a window, grouped into the ones that
    failed (most failed calls first), were throttled, ran within 10% of their timeout, and weren't called.
    """
    failed, throttled, near, idle = _health_groups(functions, metrics)
    failed.sort(key=lambda r: (-r["errors"], r["name"]))
    throttled.sort(key=lambda r: (-r["throttles"], r["name"]))
    near.sort(key=lambda r: -(r["longest_ms"] or 0) / max(r["timeout_ms"], 1))
    out: dict[str, Any] = {
        "window": _window(start, end), "regions": regions, "functions_checked": len(functions),
        "with_numbers": sum(1 for fn in functions if fn.arn in metrics),
        "failed": failed[:limit], "throttled": throttled[:limit], "near_timeout": near[:limit],
        "not_called": sorted(idle)[:limit],
        "note": "From CloudWatch's Errors metric: calls that ended in an error. Errors a function logged but "
                "handled don't count here; function_errors reads the logs.",
    }
    for key, rows in (("failed", failed), ("throttled", throttled), ("near_timeout", near), ("not_called", idle)):
        if len(rows) > limit:
            out[f"{key}_more"] = len(rows) - limit
    if errors:
        out["errors"] = _with_permissions(errors, _OVERVIEW_PERMISSIONS)
    return out


def _cause(group: ErrorGroup) -> dict[str, Any]:
    """
    One cause of errors as the model reads it, with where in the code it happened when its example has a trace.
    """
    cause = {"kind": group.kind, "message": hide_secrets(_clip(group.message, 200)), "count": group.count,
             "first": group.first, "last": group.last, "example": hide_secrets(_clip(group.example, 300)),
             "request_ids": group.request_ids[:3]}
    in_code = code_locations(group.example)
    if in_code:
        cause["in_code"] = in_code
    if group.versions:
        cause["ran_on"] = group.versions
    return cause


def summarize_errors(report: ErrorReport, *, causes: int = 8) -> dict[str, Any]:
    """
    function_errors()'s answer: a function's errors in a window, grouped by cause, most frequent first, with an
    example line (secrets hidden), a few request IDs and, when the example has a traceback, the file and line in
    the code each happened at (in_code), CloudWatch's counts and the analyzer's findings.
    """
    lf = _lambda_module()
    out: dict[str, Any] = {
        "function": report.function.name, "region": report.function.region,
        "window": _window(report.since, report.until),
        "cloudwatch": _metrics_dict(report.metrics), "error_lines_found": report.errors_found,
        "causes": [_cause(group) for group in report.groups[:causes]],
        "cut_short": report.truncated,
        "findings": [{"level": level, "message": hide_secrets(text)} for level, text in lf.error_findings(report)],
    }
    if len(report.groups) > causes:
        out["more_causes"] = len(report.groups) - causes
    if report.truncated and report.covered_from:
        out["covered_from"] = report.covered_from
    if report.errors:
        out["errors"] = _with_permissions(report.errors, _PERMISSIONS)
    return out


def _run_entry(run: LogRun, lines: int) -> dict[str, Any]:
    """
    One run as the model reads it: its status, run time, memory, its last lines (secrets hidden) and, when one of
    its lines has a traceback, where in the code it failed.
    """
    report = run.report
    entry: dict[str, Any] = {
        "request_id": run.request_id, "start": run.start, "status": run.status,
        "duration_ms": round(report.duration) if report else None,
        "memory_used_mb": report.max_memory if report else None,
        "memory_mb": report.memory if report else None, "cold_start": run.cold,
        "error": hide_secrets(run.error[1]) if run.error else None,
        "lines": [f"{e.time.astimezone(timezone.utc):%H:%M:%SZ} {hide_secrets(_clip(e.message.strip(), 400))}"
                  for e in run.events[-lines:]],
    }
    in_code = code_locations("\n".join(event.message for event in run.events))
    if in_code:
        entry["in_code"] = in_code
    if len(run.events) > lines:
        entry["lines_left_out"] = len(run.events) - lines
    return entry


def summarize_runs(page: LogPage, *, runs: int = 6, lines: int = 30) -> dict[str, Any]:
    """
    function_logs()'s answer: the newest runs in a window (those matching the search, when there is one), each
    with its status, run time, memory, its last lines (secrets hidden) and, when it logged a traceback, the file
    and line in the code it failed at (in_code).
    """
    found = page.runs
    out: dict[str, Any] = {
        "function": page.function.name, "region": page.function.region, "window": _window(page.since, page.until),
        "search": page.pattern, "runs_found": len(found),
        "statuses": dict(Counter(run.status for run in found)),
        "runs": [],
        "cut_short": page.truncated,
    }
    for run in found[:runs]:
        out["runs"].append(_run_entry(run, lines))
    if len(found) > runs:
        out["more_runs"] = len(found) - runs
    if page.truncated and page.covered_from:
        out["covered_from"] = page.covered_from
    if not page.events and page.latest:
        out["newest_line_in_log_group"] = page.latest
    if page.errors:
        out["errors"] = _with_permissions(page.errors, _PERMISSIONS)
    return out


def summarize_performance(perf: Performance, prices: dict[str, float] | None = None) -> dict[str, Any]:
    """
    function_performance()'s answer: run times (median, p95, p99, longest) against the timeout, timeouts, cold
    starts, memory used against what it has (and the size it needs), and the analyzer's findings.
    """
    lf = _lambda_module()
    fn, runs = perf.function, perf.invocations
    times = [r.duration for r in runs]
    used = max((r.max_memory for r in runs), default=None)
    colds = perf.cold_starts
    out: dict[str, Any] = {
        "function": fn.name, "region": fn.region, "window": _window(perf.since, perf.until), "runs": len(runs),
        "median_ms": lf.percentile(times, 50), "p95_ms": lf.percentile(times, 95), "p99_ms": lf.percentile(times, 99),
        "longest_ms": max(times, default=None), "timeout_ms": fn.timeout * 1000,
        "timeouts": sum(1 for r in runs if r.status == "timeout" or r.duration >= fn.timeout * 1000 * 0.99),
        "cold_starts": len(colds),
        "avg_cold_start_ms": round(sum(r.init or 0 for r in colds) / len(colds)) if colds else None,
        "memory_mb": fn.memory, "most_memory_used_mb": used,
        "suggested_memory_mb": lf.suggest_memory(used, fn.memory) if used else None,
        "cut_short": perf.truncated,
        "findings": [{"level": level, "message": text} for level, text in lf.performance_findings(perf, prices)],
    }
    if perf.truncated and perf.covered_from:
        out["covered_from"] = perf.covered_from
    if perf.errors:
        out["errors"] = _with_permissions(perf.errors, _PERMISSIONS)
    return out


def summarize_detail(detail: FunctionDetail, prices: dict[str, float] | None = None) -> dict[str, Any]:
    """
    function_details()'s answer: settings, what triggers it, its last 30 days from CloudWatch, the estimated
    monthly cost by part, and every finding about it, each with its next step.
    """
    lf = _lambda_module()
    fn = detail.function
    cost = lf.function_monthly_cost(fn, detail.metrics, detail.provisioned, detail.log_group, prices=prices)
    known = [v for v in cost.values() if v is not None]
    out: dict[str, Any] = {
        "function": function_row(fn),
        "reserved_concurrency": fn.reserved_concurrency,
        "triggers": [{"kind": t.kind, "source": t.source, "state": t.state, "public": t.public or None}
                     for t in detail.triggers],
        "function_url": {"auth": detail.url.auth_type} if detail.url else None,
        "on_failure": detail.async_config.on_failure if detail.async_config else None,
        "aliases": [a.name for a in detail.aliases], "versions": len(detail.versions),
        "provisioned_concurrency": [{"qualifier": p.qualifier, "copies": p.billed} for p in detail.provisioned],
        "log_group": ({"retention_days": detail.log_group.retention_days,
                       "stored": human_size(detail.log_group.stored_bytes or 0)} if detail.log_group else None),
        "last_30_days": _metrics_dict(detail.metrics),
        "monthly_cost_usd": {**{k: round(v, 2) for k, v in cost.items() if v is not None},
                             "total": round(sum(known), 2) if known else None,
                             "basis": "estimate: the last 30 days scaled to a month, us-east-1 list prices, "
                                      "before the free tier"},
        "findings": [{"level": level, "message": text} for level, text in lf.function_findings(
            fn, detail.metrics, detail, prices=prices)],
    }
    if detail.errors:
        out["errors"] = dict(detail.errors)
    return out


def summarize_code(cmap: CodeMap) -> dict[str, Any]:
    """
    function_code()'s answer: what a Python function's code does, read with Python's ast module (nothing is
    run): the AWS calls it makes, the environment variables it reads (names only), the event fields it uses, the
    libraries it imports and the findings about it.
    """
    lf = _lambda_module()
    fn = cmap.function
    if cmap.note:
        return {"function": fn.name if fn else None, "note": cmap.note}
    return {
        "function": fn.name if fn else None, "handler": cmap.handler, "files_read": cmap.files, "lines": cmap.lines,
        "aws_services": cmap.services,
        "aws_calls": [{"call": c.label, "access": c.access, "where": f"{c.file}:{c.line}", "in_loop": bool(c.loop)}
                      for c in cmap.aws[:30]],
        "env_vars_read": sorted({e.name for e in cmap.env}),
        "event_fields": [e.label for e in cmap.event[:30]],
        "libraries": sorted({i.top for i in cmap.imports if i.source in ("package", "layer", "missing")}),
        "findings": [{"level": level, "message": hide_secrets(text)} for level, text in lf.code_map_findings(cmap)],
        "cut_short": bool(cmap.limited),
    }


_LISTED_FILES = 40

_LINES_AROUND = 12

_MOST_LINES = 300

_LINE_CHARACTERS = 300

_SEARCH_MATCHES = 40

_SYMBOL_USES = 40

_LINES_RE = re.compile(r"^\s*(\d+)\s*(?:[-,:]\s*(\d*)\s*)?$")


def _numbered(lines: list[str], numbers: list[int], marked: set[int]) -> list[str]:
    """
    Lines by number (1-based) as '42: text', secrets hidden, '>> ' before the marked ones and '...' where numbers
    skip.
    """
    numbered = []
    previous = None
    for number in numbers:
        if previous is not None and number > previous + 1:
            numbered.append("...")
        text = hide_secrets(_clip(lines[number - 1].rstrip(), _LINE_CHARACTERS))
        numbered.append(f"{'>> ' if number in marked else ''}{number}: {text}")
        previous = number
    return numbered


def _asked_lines(spec: str, total: int) -> tuple[int, int, set[int]]:
    """
    The lines a function_source(lines=...) call asks for, as (first, last, marked): '' the file's start, '42' the
    lines around line 42 (marked), '150-300' (or sed's '150,300') that range and '150-' from line 150 on, each at
    most _MOST_LINES long. A ValueError says what it takes.
    """
    if not spec.strip():
        return 1, min(total, _MOST_LINES), set()
    match = _LINES_RE.match(spec)
    if not match:
        raise ValueError(f"lines={spec!r} isn't a line or a range: give one line ('42') or a range ('150-300')")
    first = int(match[1])
    if match[2] is None:
        return max(1, first - _LINES_AROUND), min(total, first + _LINES_AROUND), {first}
    last = int(match[2]) if match[2] else first + _MOST_LINES - 1
    if last < first:
        raise ValueError(f"lines={spec!r} ends before it starts: give a range like '150-300'")
    return first, min(total, last, first + _MOST_LINES - 1), set()


def summarize_source(pkg: CodePackage, *, lines: str = "") -> dict[str, Any]:
    """
    function_source()'s answer: lines of the file a CodePackage holds (code() read it; nothing is run), numbered,
    with anything that looks like a secret hidden, like sed -n: lines='42' gives the lines around line 42, marked;
    '150-300' that range, '150-' from 150 on; '' the file's start; at most _MOST_LINES at a time, and `next` says the
    range after it. A container image, a binary or a secrets file gives a note instead.
    """
    fn = pkg.function
    out: dict[str, Any] = {"function": fn.name, "region": fn.region, "handler": fn.handler, "file": pkg.shown_file}
    if pkg.note or pkg.source is None:
        out["note"] = pkg.note or "The package holds no file with the handler's code."
        return out
    text_lines = pkg.source.splitlines()
    total = len(text_lines)
    first, last, marked = _asked_lines(lines, total)
    out["total_lines"] = total
    if first > total or any(number > total for number in marked):
        out["note"] = (f"The file has {total:,} lines, so there is no line {max(marked or {first})}: the code may have "
                       "changed since that line was logged.")
        return out
    out["showing"] = f"lines {first}-{last}" if last else "an empty file"
    out["lines"] = _numbered(text_lines, list(range(first, last + 1)), marked)
    if last < total:
        out["next"] = f"{last + 1}-{min(total, last + _MOST_LINES)}"
    if pkg.source_truncated:
        out["cut_short"] = "only the start of the file was read (200 KB)"
    return out


def _excerpts(matches: list[Any]) -> list[dict[str, Any]]:
    """
    A search's matches as the model reads them: one entry per file, in order, its matching lines marked and the
    lines around them, overlapping context shown once.
    """
    by_file: dict[str, tuple[dict[int, str], set[int]]] = {}
    for match in matches:
        texts, marked = by_file.setdefault(match.file, ({}, set()))
        texts.update(dict(match.context))
        marked.add(match.line)
    excerpts = []
    for path, (texts, marked) in by_file.items():
        numbers = sorted(texts)
        lines = [""] * numbers[-1]
        for number, text in texts.items():
            lines[number - 1] = text
        excerpts.append({"file": path, "matches": len(marked), "lines": _numbered(lines, numbers, marked)})
    return excerpts


def summarize_search(search: CodeSearch) -> dict[str, Any]:
    """
    search_code()'s answer, like grep -rn: how many lines matched in how many of the files it read, then each file's
    matching lines (marked, secrets hidden) with the lines around them, and the files it couldn't read.
    """
    fn = search.function
    out: dict[str, Any] = {"function": fn.name, "region": fn.region, "search": search.pattern, "regex": search.regex,
                           "files": search.files or "its own source files"}
    if search.note:
        out["note"] = search.note
        return out
    out.update(files_searched=search.files_searched, matching_lines=search.total, files_matched=search.files_matched,
               matches=_excerpts(search.matches))
    if search.truncated:
        out["more_matches"] = search.total - len(search.matches)
        out["cut_short"] = True
    if search.skipped:
        out["not_read"] = search.skipped[:10]
    if not search.total and not search.files:
        out["hint"] = ("Nothing in its own files: files='*' searches the libraries packed with it too, and "
                       "regex=True with '(?i)' ignores case.")
    return out


def summarize_symbol(report: SymbolReport) -> dict[str, Any]:
    """
    find_symbol()'s answer, like a language server: where a Python name is defined (file, lines, signature,
    decorators and the first line of its docstring) and the lines that call, import or refer to it, each with the
    function it's in (secrets hidden).
    """
    fn = report.function
    out: dict[str, Any] = {"function": fn.name, "region": fn.region, "name": report.name}
    if report.note:
        out["note"] = report.note
        return out
    out["definitions"] = []
    for symbol in report.definitions:
        entry: dict[str, Any] = {"name": symbol.name, "kind": symbol.kind, "file": symbol.file, "line": symbol.line,
                                 "end_line": symbol.end_line, "signature": hide_secrets(symbol.signature)}
        if symbol.decorators:
            entry["decorators"] = symbol.decorators
        if symbol.doc:
            entry["doc"] = hide_secrets(_clip(symbol.doc, 200))
        out["definitions"].append(entry)
    out["uses"] = [{"file": use.file, "line": use.line, "kind": use.kind, "within": use.within,
                    "text": hide_secrets(_clip(use.text, _LINE_CHARACTERS))} for use in report.uses]
    out["total_uses"] = report.total_uses
    if report.total_uses > len(report.uses):
        out["more_uses"] = report.total_uses - len(report.uses)
    out["python_files_read"] = report.files_read
    if report.skipped:
        out["not_read"] = report.skipped[:10]
    if not report.definitions:
        out["hint"] = ("No definition in its own Python files: it may come from a library or Python itself, or be "
                       "spelled differently. search_code finds every line that mentions it.")
    return out


_DIFF_LINES = 300

_DIFF_FILES = 30


def _diff_excerpts(diff: VersionDiff) -> tuple[list[dict[str, Any]], bool]:
    """
    The changed files' diff lines as the model reads them, file by file, at most _DIFF_LINES in all (secrets hidden,
    long lines clipped), and whether any were left out.
    """
    room, cut, excerpts = _DIFF_LINES, False, []
    for change in diff.files[:_DIFF_FILES]:
        if not change.lines:
            continue
        kept = change.lines[:max(0, room)]
        cut = cut or len(kept) < len(change.lines) or change.truncated
        room -= len(kept)
        if kept:
            excerpts.append({"file": change.path,
                             "lines": [hide_secrets(_clip(line, _LINE_CHARACTERS)) for line in kept]})
    return excerpts, cut or len(diff.files) > _DIFF_FILES


def _diff_file(change: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"file": change.path, "status": change.status, "lines_added": change.added,
                             "lines_removed": change.removed}
    if change.definitions:
        entry["changed_in"] = change.definitions[:10]
    if change.note:
        entry["note"] = change.note
    return entry


def summarize_diff(diff: VersionDiff) -> dict[str, Any]:
    """
    diff_versions()'s answer: the two versions compared and when each was made, whether the code is the same, the
    settings, environment variable names (never values) and libraries that changed, the AWS calls the new code
    makes that the old didn't (with the permission each needs), each own file that changed
    with the Python functions its changes are in, the diff lines themselves (secrets hidden) and the findings.
    """
    lf = _lambda_module()
    fn, old = diff.function, diff.old
    out: dict[str, Any] = {"function": fn.name, "region": fn.region, "new": fn.version,
                           "new_changed": fn.last_modified}
    if diff.versions:
        out["published_versions"] = [v.version for v in diff.versions][-20:]
    if old is None:
        out["note"] = diff.note
        return out
    out.update(old=old.version, old_changed=old.last_modified, same_code=diff.same_code)
    if diff.skipped:
        out["skipped_same_code"] = diff.skipped
    if diff.note:
        out["note"] = diff.note
    out["settings_changed"] = [{"setting": c.setting, "old": c.old, "new": c.new} for c in diff.settings]
    if diff.env_added or diff.env_removed:
        out["env_vars"] = {"added": diff.env_added, "removed": diff.env_removed, "still_read": diff.still_read}
    out["libraries_changed"] = [{"name": c.name, "old": c.old, "new": c.new} for c in diff.libraries]
    if diff.new_calls:
        out["aws_calls_added"] = [{"call": c.label, "permission": c.permission, "where": f"{c.file}:{c.line}"}
                                  for c in diff.new_calls]
    out["files_changed"] = [_diff_file(change) for change in diff.files[:_DIFF_FILES]]
    out["files_unchanged"] = diff.unchanged_files
    out["diff"], out["cut_short"] = _diff_excerpts(diff)
    out["findings"] = [{"level": level, "message": hide_secrets(text)} for level, text in lf.diff_findings(diff)]
    return out


_SKIPPED_SHOWN = 20
_PERMISSIONS = {"logs": "logs:FilterLogEvents", "metrics": "cloudwatch:GetMetricData"}
_OVERVIEW_PERMISSIONS = {"list": "lambda:ListFunctions", "triggers": "lambda:ListEventSourceMappings",
                         "account": "lambda:GetAccountSettings", "logs": "logs:DescribeLogGroups",
                         "metrics": "cloudwatch:GetMetricData"}


class LambdaToolkit(Toolkit):
    """
    The agent's tools for AWS Lambda, reading through LambdaAnalyzer: `analyzer` is one to use (else one is made
    on the agent's session), `clients` its clients (for tests) and `prices` its prices. LambdaAnalyzer makes a
    client per service and region through its _service, which the guard hooks.

    Each tool is a method: its name, type hints and docstring are what the model reads.
    """

    name = "lambda"
    title = "Lambda"
    covers = ("AWS Lambda: which functions exist, how often they ran, failed, were throttled or ran close to their "
              "timeout, their errors and logs run by run, their run times and memory, their settings, triggers and "
              "estimated cost, what their code calls, and their source code, read line by line, searched across "
              "files and followed from a call to its definition, so an error in the logs can be traced to the line "
              "that raised it, and what changed between two versions of its code and settings.")
    privacy = ("Environment variable values are never read. Lines of code the agent reads (function_source, "
               "search_code, find_symbol) go to the model with anything that looks like a secret hidden; secrets "
               "files (.env, keys) are never read.")
    reads = TOOL_READS
    service = "lambda"
    services = {"FilterLogEvents": "logs", "DescribeLogGroups": "logs", "DescribeLogStreams": "logs",
                "GetMetricData": "cloudwatch", "DescribeRegions": "ec2"}
    view = "LambdaView"
    client_factory = "_service"

    def __init__(self, analyzer: LambdaAnalyzer | None = None, *, clients: dict[str, Any] | None = None,
                 prices: dict[str, float] | None = None):
        super().__init__(analyzer)
        self._clients = clients
        self._prices = prices

    def make_analyzer(self, session: Any) -> LambdaAnalyzer:
        return _lambda_module().LambdaAnalyzer(session, clients=self._clients, prices=self._prices)

    @property
    def analyzer(self) -> LambdaAnalyzer:
        """
        The LambdaAnalyzer the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        return f"""\
AWS Lambda
- Start broad and cheap, then narrow down: function_health counts calls and failures for every function at once
  (CloudWatch, a few seconds), then function_errors, function_logs or function_performance read the logs of the few
  functions that matter. Don't read logs function by function to find which ones failed.
- Log reads (function_errors, function_logs, function_performance) cover at most {budget.log_days:g} days; CloudWatch's
  counts (function_health) cover longer windows. If a tool refuses a window, explain why and what the user can do.
- To find out why a function fails, follow its logs into its code: function_errors and function_logs give each
  error's in_code (the file and line its traceback names, the line that raised last), and function_source(function,
  file=..., lines='42') shows the lines around it; lines='150-300' reads any range. find_symbol says where a Python
  function, class or name is defined and every line that uses it, so you can follow a call into the function it
  calls; search_code finds a text or a regular expression in every file (an error message, a table, a variable).
  Read the code before saying what caused an error, quote the line, and say what to change. function_code says what
  the code calls and where.
- When errors started recently, ask what changed: each cause in function_errors says which versions it ran on
  (ran_on), and diff_versions shows what changed in the code, libraries and settings between two versions (by
  default $LATEST against the newest published version with other code). Read a version's code with
  function_source('name:3', ...). Lambda keeps only published versions' code: if diff_versions has nothing to
  compare, say so and use when the code last changed instead.
- Name functions exactly as the tools return them.{self._python_rule()}"""

    def _python_rule(self) -> str:
        """
        The Lambda section's line on the Python workspace, when the agent has one.
        """
        if self.peer("python") is None:
            return ""
        return """
- For work across a function's whole code (every call to a client, every handler's shape, what two functions share),
  save_function_code puts its files in the Python workspace and run_python reads them there."""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        find_functions, function_health, function_errors, function_logs, function_performance, function_details,
        function_code, function_source, search_code, find_symbol and diff_versions, and save_function_code when the
        agent has a Python workspace: methods whose type hints and docstrings tell the model how to call them,
        each reading through LambdaAnalyzer and returning a compact, JSON-ready summary with what it covered.
        """
        tools = [self.find_functions, self.function_health, self.function_errors, self.function_logs,
                 self.function_performance, self.function_details, self.function_code, self.function_source,
                 self.search_code, self.find_symbol, self.diff_versions]
        if self.peer("python") is not None:
            tools.append(self.save_function_code)
        return tools

    def find_functions(self, match: str = "", regions: str = "") -> dict[str, Any]:
        """
        Lists the Lambda functions in the account: name, region, runtime (and whether AWS still supports
        it), memory, timeout, when each was last changed and its description. match: a name glob like 'etl-*'.
        regions: 'us-east-1,eu-west-1' or 'all'; empty for the default regions.
        """
        core = self.analyzer
        ov = core.overview(match or None, regions=self._regions(regions), metrics=False, details=False)
        out = summarize_functions(ov.functions, ov.regions, ov.errors)
        out["_summary"] = f"{_plural(len(ov.functions), 'function')} in {', '.join(ov.regions)}"
        out["_next"] = [(self._view_call("functions", match or None, regions=regions or None),
                         "every function with its calls, errors, cost and warnings")]
        return out

    def function_health(self, since: str = "24h", until: str = "", match: str = "",
                        regions: str = "") -> dict[str, Any]:
        """
        How every Lambda function did in a time window, from CloudWatch: calls, failed calls, throttles and
        the longest run against its timeout, sorted into failed, throttled, near_timeout and not_called. Fast and
        cheap: start here for "what failed", "what's slow" or "what's unused". since/until: ISO UTC times
        ('2026-10-15T18:00Z') or relative ('24h', '7d'); until defaults to now. match: a name glob like 'etl-*'.
        regions: 'us-east-1,eu-west-1' or 'all'; empty for the default regions.
        """
        core = self.analyzer
        start, end = core.time_range(since or "24h", until or None)
        ov = core.overview(match or None, regions=self._regions(regions), metrics=False, details=False)
        found, read, _, errors = core.overview_numbers(ov.functions, since=start, until=end)
        self._count_metrics(read, core.prices["metric_request"])
        out = summarize_health(ov.functions, found, start, end, ov.regions, {**ov.errors, **errors})
        failed = out["failed"]
        out["_summary"] = (f"{len(failed) + out.get('failed_more', 0)} of {_plural(len(ov.functions), 'function')} "
                           "had failed calls" + (": " + ", ".join(f"{r['name']} ({r['errors']:,})" for r in failed[:3])
                                                 if failed else ""))
        out["_next"] = [(self._next_for(fn, "errors", since=_since(start)), "its errors, grouped by cause")
                        for fn in ov.functions if fn.name in {r["name"] for r in failed[:2]}]
        return out

    def function_errors(self, function: str, since: str = "24h", until: str = "",
                        region: str = "") -> dict[str, Any]:
        """
        One function's errors in a time window, read from its logs and grouped by cause (Timeout, KeyError,
        AccessDenied, ...), with CloudWatch's count of failed and throttled calls over the same window. Each
        cause has a count, when it first and last happened, an example line and request IDs to look up with
        function_logs. since/until: ISO UTC times or relative ('24h'); the window can't be longer than the log
        limit. region: empty for the default region.
        """
        core = self.analyzer
        start, end = self._log_window(function, since, until, region or None)
        report = core.errors(function, since=start, until=end, region=region or None)
        out = summarize_errors(report)
        causes = out["causes"]
        kinds = Counter(c["kind"] for c in causes[:3])
        out["_summary"] = (f"{_plural(sum(c['count'] for c in causes), 'error')}, {_plural(len(causes), 'cause')}"
                           + (": " + ", ".join(_cause_label(c, kinds) for c in causes[:3]) if causes else ""))
        out["_next"] = [(self._next_for(report.function, "errors", since=_since(start)),
                         "the same errors as a report, with each cause's findings")]
        if causes and causes[0]["request_ids"]:
            out["_next"].insert(0, (self._next_for(report.function, "logs", request_id=causes[0]["request_ids"][0]),
                                    f"one {causes[0]['kind']} run, start to end"))
        return out

    def function_logs(self, function: str, since: str = "1h", until: str = "", search: str = "",
                      region: str = "") -> dict[str, Any]:
        """
        One function's runs in a time window, newest first, from its logs: each run's status (ok, failed,
        timeout, logged an error), run time, memory, whether it was a cold start, and its last lines. search:
        text the runs' lines must contain, such as a request ID, an order ID or 'KeyError' (case-sensitive).
        since/until: ISO UTC times or relative ('1h'); the window can't be longer than the log limit. region:
        empty for the default region.
        """
        core = self.analyzer
        start, end = self._log_window(function, since, until, region or None)
        page = core.log_runs(function, since=start, until=end, search=search or None, region=region or None)
        out = summarize_runs(page)
        statuses = out["statuses"]
        out["_summary"] = (f"{_plural(out['runs_found'], 'run')}"
                           + (f" ({', '.join(f'{n} {s}' for s, n in statuses.items())})" if statuses else ""))
        out["_next"] = [(self._next_for(page.function, "logs", since=_since(start), search=search or None),
                         "the same lines, as a report")]
        return out

    def function_performance(self, function: str, since: str = "24h", until: str = "",
                             region: str = "") -> dict[str, Any]:
        """
        One function's run times and memory in a time window, from the REPORT line Lambda logs after every
        run: median, p95, p99 and longest run against its timeout, timeouts, cold starts, the most memory a run
        used and the memory size it needs. since/until: ISO UTC times or relative ('24h'); the window can't be
        longer than the log limit. region: empty for the default region.
        """
        core = self.analyzer
        start, end = self._log_window(function, since, until, region or None)
        perf = core.performance(function, since=start, until=end, region=region or None)
        out = summarize_performance(perf, core.prices)
        p95 = out["p95_ms"]
        out["_summary"] = (f"{_plural(out['runs'], 'run')}"
                           + (f", p95 {p95 / 1000:.1f}s of a {perf.function.timeout}s timeout" if p95 else ""))
        out["_next"] = [(self._next_for(perf.function, "performance", since=_since(start)),
                         "run times, memory and cold starts as a report")]
        return out

    def function_details(self, function: str, region: str = "") -> dict[str, Any]:
        """
        Everything about one function: its settings (runtime and its support dates, memory, timeout,
        reserved concurrency), what triggers it, its aliases and versions, its last 30 days from CloudWatch, its
        log group's retention and size, the estimated monthly cost by part, and every finding about it with the
        next step. region: empty for the default region.
        """
        core = self.analyzer
        detail = core.describe(function, region=region or None, days=30)
        out = summarize_detail(detail, core.prices)
        total = out["monthly_cost_usd"].get("total")
        out["_summary"] = (f"{detail.function.runtime or 'container image'}, {detail.function.memory:,} MB, "
                           f"{detail.function.timeout}s; {_plural(len(out['findings']), 'finding')}"
                           + (f"; about {human_money(total)}/month" if total is not None else ""))
        out["_next"] = [(self._next_for(detail.function, "function_info"), "the function in plain English")]
        return out

    def function_code(self, function: str, region: str = "") -> dict[str, Any]:
        """
        What a Python function's code does, read from its deployment package with Python's ast module
        (nothing is run): the AWS calls it makes, the environment variables it reads (names only), the event
        fields it uses, the libraries it imports and findings such as HTTP calls without a timeout. Other
        runtimes and container images give a note. region: empty for the default region.
        """
        core = self.analyzer
        cmap = core.code_map(function, region=region or None)
        out = summarize_code(cmap)
        if "note" in out:
            out["_summary"] = _clip(str(out["note"]), 80)
        else:
            out["_summary"] = (f"{_plural(len(out['aws_calls']), 'AWS call')}"
                               + (f" ({', '.join(out['aws_services'][:4])})" if out["aws_services"] else "")
                               + f", {_plural(len(out['env_vars_read']), 'environment variable')}")
        if cmap.function is not None:
            out["_next"] = [(self._next_for(cmap.function, "how_it_works"), "how the code works, step by step")]
        return out

    def function_source(self, function: str, file: str = "", lines: str = "", region: str = "") -> dict[str, Any]:
        """
        One function's source code, numbered line by line, from its deployment package, like sed -n (nothing is
        run, and anything that looks like a secret is hidden). file: a path in the package, such as an error's
        in_code file, a definition's file from find_symbol, a match's file from search_code or one from this tool's
        files list; empty for the file that holds the handler. lines: '42' for the lines around line 42 (the line an
        error's in_code names), '150-300' for a range, '150-' from line 150 on, empty for the file's start; at most
        300 lines a call, and next says the range after it. Use it after function_errors or function_logs to read
        the code that failed. region: empty for the default region.
        """
        core = self.analyzer
        try:
            pkg = core.code(function, file=file or None, region=region or None)
        except ValueError as exc:
            if not file or not str(exc).startswith("No file"):
                raise
            return self._no_such_file(core.code(function, region=region or None), file)
        out = summarize_source(pkg, lines=lines)
        if not file:
            self._add_files(out, pkg)
        out["_summary"] = _source_summary(out)
        out["_next"] = [(self._next_for(pkg.function, "code", pkg.shown_file), "the whole file, in colour")]
        return out

    def search_code(self, function: str, text: str, regex: bool = False, files: str = "",
                    region: str = "") -> dict[str, Any]:
        """
        Every line of one function's code that contains a text, like grep -rn across its files: each match with
        its file, line number and the lines around it (secrets hidden; nothing is run). text: what to find, such as
        a variable, an error message, a table name or an environment variable (case-sensitive). regex: true to
        read text as a Python regular expression ('(?i)' first ignores case). files: empty for the function's own
        source files, or globs, folders or paths separated by commas ('*.py', 'app/', 'requests/*') to search
        those, the libraries packed with it too. Works for every runtime. region: empty for the default region.
        """
        core = self.analyzer
        search = core.search_code(function, text, regex=regex, files=files or None, limit=_SEARCH_MATCHES,
                                  region=region or None)
        out = summarize_search(search)
        if search.note:
            out["_summary"] = _clip(search.note, 80)
        else:
            out["_summary"] = (f"{_plural(search.total, 'line')} in {_plural(search.files_matched, 'file')} "
                               f"of {search.files_searched:,}")
        if search.matches:
            out["_next"] = [(self._next_for(search.function, "code", search.matches[0].file),
                             "the first file it was found in, in colour")]
        return out

    def find_symbol(self, function: str, name: str, region: str = "") -> dict[str, Any]:
        """
        Where a name is defined in one Python function's code and every line that uses it, like a language
        server's go to definition and find references: each definition's file, lines, signature and docstring, and
        each call, import or reference with the function it's in (read with Python's ast module; nothing is run).
        name: a function, class, method or module-level name ('load_orders'), or a dotted one ('Orders.load',
        'store.load_orders') to pick one. Then read a definition with function_source(file=..., lines='17-60').
        For other runtimes, use search_code. region: empty for the default region.
        """
        core = self.analyzer
        report = core.find_symbol(function, name, limit=_SYMBOL_USES, region=region or None)
        out = summarize_symbol(report)
        if report.note:
            out["_summary"] = _clip(report.note, 80)
            return out
        places = ", ".join(f"{d.file}:{d.line}" for d in report.definitions[:2]) or "not defined in its own files"
        out["_summary"] = f"{report.name}: {places}; {_plural(report.total_uses, 'use')}"
        if report.definitions:
            out["_next"] = [(self._next_for(report.function, "code", report.definitions[0].file),
                             "the file it's defined in, in colour")]
        return out

    def diff_versions(self, function: str, old: str = "", new: str = "", region: str = "") -> dict[str, Any]:
        """
        What changed between two versions of one function, like git diff: the settings and environment variable
        names that changed, the libraries packed with it that were added, removed or upgraded, each of its own
        files that changed with the Python functions the changes are in, and the changed lines themselves (secrets
        hidden; nothing is run). new: a version number, an alias or '$LATEST' (empty: $LATEST, or the version the
        function name's ':alias' points to); old: the version to compare it with (empty: the newest published
        version before new whose code differs). Use it when errors started after a deploy: function_errors says
        which versions each cause ran on. Lambda keeps only published versions' code, so a function that was never
        published has nothing to compare, and the note says so. region: empty for the default region.
        """
        core = self.analyzer
        diff = core.diff(function, old or None, new or None, region=region or None)
        out = summarize_diff(diff)
        if diff.old is None:
            out["_summary"] = _clip(diff.note or "nothing to compare", 80)
            return out
        out["_summary"] = (f"{diff.old.version} → {diff.function.version}: "
                           + ("same code" if diff.same_code else
                              f"{_plural(len(diff.files), 'file')}, +{diff.added:,} −{diff.removed:,} lines")
                           + (f", {_plural(len(diff.libraries), 'library change')}" if diff.libraries else "")
                           + (f", {_plural(len(diff.settings), 'setting')}" if diff.settings else ""))
        out["_next"] = [(self._next_for(diff.function, "diff", diff.old.version, diff.function.version),
                         "the same change as a report, line by line")]
        return out

    def _add_files(self, out: dict[str, Any], pkg: CodePackage) -> None:
        """
        Adds the package's own source files to a function_source() answer, the most the model can pick from.
        """
        files = _lambda_module().source_files(pkg)
        out["files"] = files[:_LISTED_FILES]
        if len(files) > _LISTED_FILES:
            out["more_files"] = len(files) - _LISTED_FILES
        others = len(pkg.files) - len(files)
        if others:
            out["other_files"] = f"{others:,} more: libraries, data and binaries packed with it"

    def _no_such_file(self, pkg: CodePackage, file: str) -> dict[str, Any]:
        """
        function_source()'s answer when the package has no such file: the closest names, and its files.
        """
        files = _lambda_module().source_files(pkg)
        close = difflib.get_close_matches(file, files, n=3)
        out: dict[str, Any] = {
            "function": pkg.function.name, "error": f"No file {file!r} in {pkg.function.name}'s package.",
            "close_files": close, "hint": "Use a path from files; the code may have changed since the error.",
        }
        self._add_files(out, pkg)
        out["_summary"] = f"no file {file}"
        return out

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """
        A tool's answer when a function isn't there: the closest names in that region.
        """
        function = arguments.get("function")
        if _error_code(exc) != "ResourceNotFoundException" or not function:
            return None
        core = self.analyzer
        name, _, where = core.locate(function, arguments.get("region") or None)
        try:
            names = [f.name for f in core.list_functions(where)]
        except (ClientError, BotoCoreError):
            names = []
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
        return {"error": f"No function {name!r} in {where}. Names are case-sensitive.",
                "close_names": close, "region": where,
                "hint": "Use one of close_names, or find_functions to list them; the function may be in another "
                        "region."}

    def _log_window(self, function: str, since: str, until: str, region: str | None) -> tuple[datetime, datetime]:
        """
        (start, end) for a log read, or _Refused when it's longer than the budget's log_days.
        """
        core = self.analyzer
        log_days = self.budget.log_days
        start, end = core.time_range(since or "24h", until or None)
        days = (end - start).total_seconds() / 86400
        if days > log_days + 1e-9:
            stored = None
            try:
                fn = core.function(function, region=region)
                group = core.log_group(fn.log_group, fn.region)
                stored = group.stored_bytes if group else None
            except (ClientError, BotoCoreError, ValueError):
                pass
            size = f" Its log group holds {human_size(stored)}." if stored else ""
            raise _Refused(
                "log_window",
                f"Not read: a log read over {days:.1f} days is longer than the {log_days:g}-day limit "
                f"(long log searches are slow).{size} Use function_health for counts over long windows, or read the "
                f"logs of a shorter window. The user can raise the limit with AgentView(budget={{'log_days': "
                f"{max(7, round(days))}}}).",
                window=_window(start, end), stored=human_size(stored) if stored else None,
            )
        return start, end

    def save_function_code(self, function: str, files: str = "", region: str = "") -> dict[str, Any]:
        """
        Saves one function's source files from its deployment package into the Python workspace, under
        lambda/<function>/ (lambda/<function>@<version>/ for a published version), so run_python can read them: parse
        them with ast, search them, count things across files. files: globs, folders or paths separated by commas to
        save those instead, the libraries packed with it too ('*.json', 'vendor/'); empty for its own source files.
        Secrets files (.env, keys), binary files and files over 2 MB are never saved. region: empty for the default
        region.
        """
        python = self.peer("python")
        if python is None:
            raise ValueError("The agent has no Python workspace to save code into")
        sources = self.analyzer.sources(function, files=files or None, region=region or None)
        fn = sources.function
        out: dict[str, Any] = {"function": fn.name, "region": fn.region, "version": fn.version}
        if sources.note:
            out["note"] = sources.note
            out["_summary"] = _clip(sources.note, 80)
            return out
        folder = f"lambda/{fn.name}" if fn.version == "$LATEST" else f"lambda/{fn.name}@{fn.version}"
        out.update(python.save(folder, {path: text.encode() for path, text in sources.texts.items()}))
        if sources.skipped:
            out["skipped"] = sources.skipped[:_SKIPPED_SHOWN]
        if not sources.texts:
            out["note"] = (f"No file in the package matched {files!r}." if files else
                           "The package holds no source files of its own: files='*' saves what's in it.")
        out["_summary"] = f"saved {_plural(out['files_saved'], 'file')} ({out['size']}) to {out['folder']}"
        out["_next"] = [(self._next_for(fn, "code"), "its files, and the handler's code")]
        return out

    def _next_for(self, fn: Function, command: str, *args: Any, **kwargs: Any) -> str:
        """
        A LambdaView call about a function, as it runs in a new cell (region= added when it's elsewhere).
        """
        return self._view_call(command, fn.name, *args, region=fn.region, **kwargs)


def _cause_label(cause: dict[str, Any], kinds: Counter) -> str:
    """
    A cause of errors as the report's one line says it: its kind and count ('KeyError ×12'), with what tells it
    apart when another of the causes shown has the same kind ('KeyError (order_id) ×12').
    """
    kind = cause["kind"]
    rest = re.sub(rf"^\W*{re.escape(kind)}\W*", "", cause["message"] or "").strip()
    if kinds[kind] > 1 and rest:
        return f"{kind} ({_clip(rest, 40)}) ×{cause['count']}"
    return f"{kind} ×{cause['count']}"


def _source_summary(out: dict[str, Any]) -> str:
    """
    A function_source() call as the report's one line says it: the file and what of it was shown.
    """
    file = out.get("file") or "no file"
    if "note" in out:
        return f"{file}: {_clip(str(out['note']), 60)}"
    return f"{file}, {out.get('showing', 'nothing shown')} of {out['total_lines']:,}"


def _since(start: datetime) -> str:
    """
    A window's start as a View's since= takes it.
    """
    return _iso(start) or "24h"
