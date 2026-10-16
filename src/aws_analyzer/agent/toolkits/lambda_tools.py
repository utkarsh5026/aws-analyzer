"""The Lambda toolkit: seven tools over LambdaAnalyzer, building blocks rather than one per question. Which functions
there are (find_functions), how every function did in a window, from CloudWatch (function_health), and one function's
errors, runs, run times, settings and code (function_errors, function_logs, function_performance, function_details,
function_code). The summarize_* functions turn what LambdaAnalyzer returns into the compact dicts the model reads;
they're pure, so they run on any Function, ErrorReport, LogPage, Performance, FunctionDetail or CodeMap."""

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

if TYPE_CHECKING:  # for the type hints only: the Lambda analyzer loads when the first tool runs
    from ...lambda_functions import (
        CodeMap, ErrorReport, Function, FunctionDetail, FunctionMetrics, LambdaAnalyzer, LogPage, Performance,
    )

# What each tool reads, for tools() and README. A tool's own reads are what its analyzer method makes.
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
}


def _lambda_module() -> Any:
    """aws_analyzer.lambda_functions, which the tools are built on. Loaded when a tool first needs it, so importing
    the agent loads no analyzer."""
    from ... import lambda_functions

    return lambda_functions


# =============================================================================
# What the tools return: LambdaAnalyzer's results as compact JSON (pure)
# =============================================================================


def _metrics_dict(m: FunctionMetrics | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {"calls": int(m.invocations), "errors": int(m.errors), "throttles": int(m.throttles),
            "error_rate_pct": round(100 * m.error_rate, 2) if m.error_rate is not None else None,
            "avg_ms": round(m.avg_duration) if m.avg_duration is not None else None,
            "longest_ms": round(m.duration_max) if m.duration_max is not None else None}


def function_row(fn: Function, *, today: date | None = None) -> dict[str, Any]:
    """One function's settings as the agent sees them: runtime and where it stands in AWS's support schedule,
    memory, timeout, package and description. Environment variable values are never read; their names are."""
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
    """find_functions()'s answer: every function found, the newest-changed first (up to `limit`), by region."""
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


def summarize_health(functions: list[Function], metrics: dict[str, FunctionMetrics], start: datetime,
                     end: datetime, regions: list[str], errors: dict[str, str] | None = None,
                     *, limit: int = 25) -> dict[str, Any]:
    """function_health()'s answer: CloudWatch's counts for every function in a window, grouped into the ones that
    failed (most failed calls first), were throttled, ran within 10% of their timeout, and weren't called."""
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
        if m.duration_max is not None and m.duration_max >= 0.9 * fn.timeout * 1000:
            near.append(row)
        if not m.invocations:
            idle.append(fn.name)
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


def summarize_errors(report: ErrorReport, *, causes: int = 8) -> dict[str, Any]:
    """function_errors()'s answer: a function's errors in a window, grouped by cause, most frequent first, with an
    example line (secrets hidden) and a few request IDs each, CloudWatch's counts and the analyzer's findings."""
    lf = _lambda_module()
    out: dict[str, Any] = {
        "function": report.function.name, "region": report.function.region,
        "window": _window(report.since, report.until),
        "cloudwatch": _metrics_dict(report.metrics), "error_lines_found": report.errors_found,
        "causes": [{"kind": g.kind, "message": hide_secrets(_clip(g.message, 200)), "count": g.count,
                    "first": g.first, "last": g.last, "example": hide_secrets(_clip(g.example, 300)),
                    "request_ids": g.request_ids[:3]} for g in report.groups[:causes]],
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


def summarize_runs(page: LogPage, *, runs: int = 6, lines: int = 30) -> dict[str, Any]:
    """function_logs()'s answer: the newest runs in a window (those matching the search, when there is one), each
    with its status, run time, memory and its last lines (secrets hidden)."""
    found = page.runs
    out: dict[str, Any] = {
        "function": page.function.name, "region": page.function.region, "window": _window(page.since, page.until),
        "search": page.pattern, "runs_found": len(found),
        "statuses": dict(Counter(run.status for run in found)),
        "runs": [],
        "cut_short": page.truncated,
    }
    for run in found[:runs]:
        report = run.report
        entry = {"request_id": run.request_id, "start": run.start, "status": run.status,
                 "duration_ms": round(report.duration) if report else None,
                 "memory_used_mb": report.max_memory if report else None,
                 "memory_mb": report.memory if report else None, "cold_start": run.cold,
                 "error": hide_secrets(run.error[1]) if run.error else None,
                 "lines": [f"{e.time.astimezone(timezone.utc):%H:%M:%SZ} {hide_secrets(_clip(e.message.strip(), 400))}"
                           for e in run.events[-lines:]]}
        if len(run.events) > lines:
            entry["lines_left_out"] = len(run.events) - lines
        out["runs"].append(entry)
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
    """function_performance()'s answer: run times (median, p95, p99, longest) against the timeout, timeouts, cold
    starts, memory used against what it has (and the size it needs), and the analyzer's findings."""
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
    """function_details()'s answer: settings, what triggers it, its last 30 days from CloudWatch, the estimated
    monthly cost by part, and every finding about it, each with its next step."""
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
    """function_code()'s answer: what a Python function's code does, read with Python's ast module (nothing is
    run): the AWS calls it makes, the environment variables it reads (names only), the event fields it uses, the
    libraries it imports and the findings about it."""
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


_PERMISSIONS = {"logs": "logs:FilterLogEvents", "metrics": "cloudwatch:GetMetricData"}  # error section -> permission
_OVERVIEW_PERMISSIONS = {"list": "lambda:ListFunctions", "triggers": "lambda:ListEventSourceMappings",
                         "account": "lambda:GetAccountSettings", "logs": "logs:DescribeLogGroups",
                         "metrics": "cloudwatch:GetMetricData"}  # overview()'s 'region:section' errors



# =============================================================================
# LambdaToolkit
# =============================================================================


class LambdaToolkit(Toolkit):
    """The agent's tools for AWS Lambda, reading through LambdaAnalyzer: `analyzer` is one to use (else one is made
    on the agent's session), `clients` its clients (for tests) and `prices` its prices."""

    name = "lambda"
    title = "Lambda"
    covers = ("AWS Lambda: which functions exist, how often they ran, failed, were throttled or ran close to their "
              "timeout, their errors and logs run by run, their run times and memory, their settings, triggers and "
              "estimated cost, and what their code calls.")
    privacy = "Environment variable values are never read."
    reads = TOOL_READS
    service = "lambda"
    services = {"FilterLogEvents": "logs", "DescribeLogGroups": "logs", "DescribeLogStreams": "logs",
                "GetMetricData": "cloudwatch", "DescribeRegions": "ec2"}
    view = "LambdaView"
    client_factory = "_service"  # LambdaAnalyzer makes a client per service and region through it

    def __init__(self, analyzer: LambdaAnalyzer | None = None, *, clients: dict[str, Any] | None = None,
                 prices: dict[str, float] | None = None):
        super().__init__(analyzer)
        self._clients = clients
        self._prices = prices

    def make_analyzer(self, session: Any) -> LambdaAnalyzer:
        return _lambda_module().LambdaAnalyzer(session, clients=self._clients, prices=self._prices)

    @property
    def analyzer(self) -> LambdaAnalyzer:
        """The LambdaAnalyzer the tools read through, made on first use and hooked to the read-only guard."""
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        return f"""\
AWS Lambda
- Start broad and cheap, then narrow down: function_health counts calls and failures for every function at once
  (CloudWatch, a few seconds), then function_errors, function_logs or function_performance read the logs of the few
  functions that matter. Don't read logs function by function to find which ones failed.
- Log reads (function_errors, function_logs, function_performance) cover at most {budget.log_days:g} days; CloudWatch's
  counts (function_health) cover longer windows. If a tool refuses a window, explain why and what the user can do.
- Name functions exactly as the tools return them."""

    # ------------------------------------------------------------------ the tools

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """find_functions, function_health, function_errors, function_logs, function_performance, function_details
        and function_code: plain functions (their type hints and docstrings tell the model how to call them), each
        reading through LambdaAnalyzer and returning a compact, JSON-ready summary with what it covered."""

        def find_functions(match: str = "", regions: str = "") -> dict[str, Any]:
            """Lists the Lambda functions in the account: name, region, runtime (and whether AWS still supports
            it), memory, timeout, when each was last changed and its description. match: a name glob like 'etl-*'.
            regions: 'us-east-1,eu-west-1' or 'all'; empty for the default regions."""
            core = self.analyzer
            ov = core.overview(match or None, regions=self._regions(regions), metrics=False, details=False)
            out = summarize_functions(ov.functions, ov.regions, ov.errors)
            out["_summary"] = f"{_plural(len(ov.functions), 'function')} in {', '.join(ov.regions)}"
            out["_next"] = [(self._view_call("functions", match or None, regions=regions or None),
                             "every function with its calls, errors, cost and warnings")]
            return out

        def function_health(since: str = "24h", until: str = "", match: str = "", regions: str = "") -> dict[str, Any]:
            """How every Lambda function did in a time window, from CloudWatch: calls, failed calls, throttles and
            the longest run against its timeout, sorted into failed, throttled, near_timeout and not_called. Fast and
            cheap: start here for "what failed", "what's slow" or "what's unused". since/until: ISO UTC times
            ('2026-10-15T18:00Z') or relative ('24h', '7d'); until defaults to now. match: a name glob like 'etl-*'.
            regions: 'us-east-1,eu-west-1' or 'all'; empty for the default regions."""
            core = self.analyzer
            start, end = core._window(since or "24h", until or None)
            ov = core.overview(match or None, regions=self._regions(regions), metrics=False, details=False)
            found, read, _, errors = core._numbers(ov.functions, since=start, until=end)
            self._count_metrics(read, core.prices["metric_request"])
            out = summarize_health(ov.functions, found, start, end, ov.regions, {**ov.errors, **errors})
            failed = out["failed"]
            out["_summary"] = (f"{len(failed) + out.get('failed_more', 0)} of {_plural(len(ov.functions), 'function')} "
                               "had failed calls" + (": " + ", ".join(f"{r['name']} ({r['errors']:,})" for r in failed[:3])
                                                     if failed else ""))
            out["_next"] = [(self._next_for(fn, "errors", since=_since(start)), "its errors, grouped by cause")
                            for fn in ov.functions if fn.name in {r["name"] for r in failed[:2]}]
            return out

        def function_errors(function: str, since: str = "24h", until: str = "", region: str = "") -> dict[str, Any]:
            """One function's errors in a time window, read from its logs and grouped by cause (Timeout, KeyError,
            AccessDenied, ...), with CloudWatch's count of failed and throttled calls over the same window. Each
            cause has a count, when it first and last happened, an example line and request IDs to look up with
            function_logs. since/until: ISO UTC times or relative ('24h'); the window can't be longer than the log
            limit. region: empty for the default region."""
            core = self.analyzer
            start, end = self._log_window(function, since, until, region or None)
            report = core.errors(function, since=start, until=end, region=region or None)
            out = summarize_errors(report)
            causes = out["causes"]
            kinds = Counter(c["kind"] for c in causes[:3])

            def label(cause: dict[str, Any]) -> str:  # the kind, and what tells it apart when two share a kind
                kind = cause["kind"]
                rest = re.sub(rf"^\W*{re.escape(kind)}\W*", "", cause["message"] or "").strip()
                if kinds[kind] > 1 and rest:
                    return f"{kind} ({_clip(rest, 40)}) ×{cause['count']}"
                return f"{kind} ×{cause['count']}"

            out["_summary"] = (f"{_plural(sum(c['count'] for c in causes), 'error')}, {_plural(len(causes), 'cause')}"
                               + (": " + ", ".join(label(c) for c in causes[:3]) if causes else ""))
            out["_next"] = [(self._next_for(report.function, "errors", since=_since(start)),
                             "the same errors as a report, with each cause's findings")]
            if causes and causes[0]["request_ids"]:
                out["_next"].insert(0, (self._next_for(report.function, "logs", request_id=causes[0]["request_ids"][0]),
                                        f"one {causes[0]['kind']} run, start to end"))
            return out

        def function_logs(function: str, since: str = "1h", until: str = "", search: str = "",
                          region: str = "") -> dict[str, Any]:
            """One function's runs in a time window, newest first, from its logs: each run's status (ok, failed,
            timeout, logged an error), run time, memory, whether it was a cold start, and its last lines. search:
            text the runs' lines must contain, such as a request ID, an order ID or 'KeyError' (case-sensitive).
            since/until: ISO UTC times or relative ('1h'); the window can't be longer than the log limit. region:
            empty for the default region."""
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

        def function_performance(function: str, since: str = "24h", until: str = "",
                                 region: str = "") -> dict[str, Any]:
            """One function's run times and memory in a time window, from the REPORT line Lambda logs after every
            run: median, p95, p99 and longest run against its timeout, timeouts, cold starts, the most memory a run
            used and the memory size it needs. since/until: ISO UTC times or relative ('24h'); the window can't be
            longer than the log limit. region: empty for the default region."""
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

        def function_details(function: str, region: str = "") -> dict[str, Any]:
            """Everything about one function: its settings (runtime and its support dates, memory, timeout,
            reserved concurrency), what triggers it, its aliases and versions, its last 30 days from CloudWatch, its
            log group's retention and size, the estimated monthly cost by part, and every finding about it with the
            next step. region: empty for the default region."""
            core = self.analyzer
            detail = core.describe(function, region=region or None, days=30)
            out = summarize_detail(detail, core.prices)
            total = out["monthly_cost_usd"].get("total")
            out["_summary"] = (f"{detail.function.runtime or 'container image'}, {detail.function.memory:,} MB, "
                               f"{detail.function.timeout}s; {_plural(len(out['findings']), 'finding')}"
                               + (f"; about {human_money(total)}/month" if total is not None else ""))
            out["_next"] = [(self._next_for(detail.function, "function_info"), "the function in plain English")]
            return out

        def function_code(function: str, region: str = "") -> dict[str, Any]:
            """What a Python function's code does, read from its deployment package with Python's ast module
            (nothing is run): the AWS calls it makes, the environment variables it reads (names only), the event
            fields it uses, the libraries it imports and findings such as HTTP calls without a timeout. Other
            runtimes and container images give a note. region: empty for the default region."""
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

        return [find_functions, function_health, function_errors, function_logs, function_performance,
                function_details, function_code]

    # ------------------------------------------------------------------ what the tools share

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """A tool's answer when a function isn't there: the closest names in that region."""
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
        """(start, end) for a log read, or _Refused when it's longer than the budget's log_days."""
        core = self.analyzer
        log_days = self.budget.log_days
        start, end = core._window(since or "24h", until or None)
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

    def _next_for(self, fn: Function, command: str, *args: Any, **kwargs: Any) -> str:
        """A LambdaView call about a function, as it runs in a new cell (region= added when it's elsewhere)."""
        return self._view_call(command, fn.name, *args, region=fn.region, **kwargs)


def _since(start: datetime) -> str:
    """A window's start as a View's since= takes it."""
    return _iso(start) or "24h"
