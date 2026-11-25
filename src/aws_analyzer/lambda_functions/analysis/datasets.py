"""
Lambda's reports as data: the tables behind functions(), function_info(), errors(), performance(), runs() and logs(),
each column said in plain words with how to read it, and the charts the workbook draws from them. Pure: every
function takes what a command already read and returns a ReportData (_kit/datasets.py), so ui.data() and
ui.to_excel() read nothing again.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.datasets import (
    DataChart,
    DataColumn,
    DataTable,
    Recipe,
    ReportData,
    by_hour,
    median_of,
    nearest_rank,
)
from ..models import ErrorReport, FunctionDetail, Invocation, Performance
from .causes import cause_state
from .logs import LogPage
from .overview import Overview
from .performance import timed_out
from .runs import RunTable

_P95 = 0.95
_STATUS_TONES = (("timeout", "bad"), ("failed", "bad"), ("logged", "warn"))
_STATE_TONES = (("still failing", "bad"), ("new", "bad"), ("stopped", "ok"))
_RUNTIME_TONES = (("deprecated", "bad"), ("blocked", "bad"), ("ending", "warn"))
_SKIPPED_RUN_FIELDS = frozenset({"text"})
_LOGGED_KIND = {bool: "flag", int: "number", float: "number", datetime: "time"}


def _fraction(part: float | None, whole: float | None) -> float | None:
    """
    part / whole as a share from 0 to 1, or None without a whole.
    """
    if part is None or not whole:
        return None
    return part / whole


def _stem(command: str, name: str) -> str:
    return f"lambda-{command}-{name}"



_FUNCTION_COLUMNS = [
    DataColumn("function", "the function's name", "id"),
    DataColumn("region", "the AWS region it's in", "id"),
    DataColumn("runtime", "the language version it runs, or 'container image'"),
    DataColumn("runtime_status", "whether AWS still supports that runtime", tones=_RUNTIME_TONES,
               read="'deprecated' gets no security patches; 'ending' loses support within months."),
    DataColumn("memory_mb", "memory it has; it also sets how much CPU it gets", "mb"),
    DataColumn("timeout_s", "the longest a run may take before Lambda stops it", "seconds"),
    DataColumn("architecture", "x86_64, or arm64 (about 20% cheaper per second)"),
    DataColumn("package", "Zip or Image: how its code is deployed"),
    DataColumn("code_bytes", "the size of its zipped code", "bytes"),
    DataColumn("last_modified", "when its code or settings last changed", "time"),
    DataColumn("triggers", "what calls it: queues, buckets, schedules, APIs"),
    DataColumn("invocations", "calls CloudWatch counted in the window", "count", bar=True),
    DataColumn("errors", "calls that failed (an exception, a timeout or running out of memory)", "count", bar=True),
    DataColumn("error_rate", "errors / invocations", "share", read="Over 0.01 (1%) is worth a look."),
    DataColumn("throttles", "calls refused because too many ran at once", "count",
               read="Any is a sign it needs reserved concurrency or the region's limit raised."),
    DataColumn("avg_duration_ms", "the average run time", "ms"),
    DataColumn("max_duration_ms", "the longest run", "ms",
               read="Close to timeout_s x 1000 means runs come near being stopped."),
    DataColumn("last_invoked", "the start of the last day it was called", "time"),
    DataColumn("est_monthly_usd", "an estimate of its monthly bill: calls, run time, provisioned concurrency, logs",
               "usd", bar=True, read="Scaled from the window to a month, at list prices."),
    DataColumn("description", "the description it was given"),
]


def overview_rows(ov: Overview) -> list[dict[str, Any]]:
    """
    One dict per function, the costliest first: its settings, CloudWatch's counts and its estimated monthly cost.
    """
    rows = ov.rows()
    for row in rows:
        row["error_rate"] = _fraction(row.get("errors"), row.get("invocations"))
    return sorted(rows, key=lambda row: -(row.get("est_monthly_usd") or 0.0))


def functions_data(ov: Overview) -> ReportData:
    """
    functions()' report as data: a row per function, the costliest first, and charts of the bill and the calls.
    """
    table = DataTable(
        "functions", "Every function in the report", "a function", _FUNCTION_COLUMNS, overview_rows(ov),
        notes=[
            f"Counts cover the last {ov.days} days; est_monthly_usd scales them to a month.",
            "Rows are sorted by est_monthly_usd, the costliest first, so the top rows are where the bill goes.",
            "A function with invocations of 0 or empty wasn't called in the window: a candidate to remove.",
        ],
        recipes=[
            Recipe("df.nlargest(10, 'est_monthly_usd')[['function', 'est_monthly_usd']]", "where the bill goes"),
            Recipe("df[df.error_rate > 0.01].sort_values('errors', ascending=False)", "functions failing often"),
            Recipe("df.groupby('runtime').est_monthly_usd.sum()", "the bill by runtime"),
            Recipe("df[df.invocations.fillna(0) == 0].function", "functions nobody called"),
        ],
    )
    charts = [
        DataChart("Estimated monthly cost by function", "functions", "function", ("est_monthly_usd",), "bar",
                  "The costliest functions first: shrinking the top bars is what moves the bill.", "USD a month"),
        DataChart("Calls and failed calls", "functions", "function", ("invocations", "errors"), "bar",
                  f"Over the last {ov.days} days, in the same order as the cost chart.", "calls"),
    ]
    name = "all" if len(ov.regions) != 1 else ov.regions[0]
    return ReportData("Lambda functions", [table], charts, stem=_stem("functions", name))



_DAY_COLUMNS = [
    DataColumn("day", "the start of the day (or period)", "time"),
    DataColumn("invocations", "calls in the day", "count", bar=True),
    DataColumn("errors", "failed calls in the day", "count"),
    DataColumn("error_rate", "errors / invocations", "share"),
    DataColumn("throttles", "calls refused because too many ran at once", "count"),
    DataColumn("avg_duration_ms", "the average run time that day", "ms"),
    DataColumn("max_duration_ms", "the longest run that day", "ms"),
    DataColumn("concurrency", "the most runs at once that day", "count"),
]


def function_data(detail: FunctionDetail) -> ReportData:
    """
    function_info()'s report as data: its days, one row each, from CloudWatch.
    """
    fn, metrics = detail.function, detail.metrics
    rows = []
    for day in metrics.daily if metrics else []:
        rows.append({
            "day": day.start, "invocations": day.invocations, "errors": day.errors,
            "error_rate": _fraction(day.errors, day.invocations), "throttles": day.throttles,
            "avg_duration_ms": _fraction(day.duration_sum, day.invocations), "max_duration_ms": day.duration_max,
            "concurrency": day.concurrency,
        })
    table = DataTable(
        "days", f"{fn.name}, day by day", "a day CloudWatch has numbers for", _DAY_COLUMNS, rows,
        notes=["Days with no calls are left out, so a gap in the dates means it wasn't called.",
               "CloudWatch's days are UTC days."],
        recipes=[Recipe("df.set_index('day').invocations.plot()", "calls over time"),
                 Recipe("df[df.errors > 0]", "the days with failed calls")],
    )
    charts = [
        DataChart("Calls a day", "days", "day", ("invocations", "errors"), "column", "Calls, and the failed ones.",
                  "calls"),
        DataChart("Run time a day", "days", "day", ("avg_duration_ms", "max_duration_ms"), "line",
                  f"The average and the longest run, against a timeout of {fn.timeout * 1000:,} ms.", "ms"),
    ]
    return ReportData(f"{fn.name}", [table], charts, stem=_stem("function", fn.name))



_CAUSE_COLUMNS = [
    DataColumn("kind", "the kind of error: Timeout, Out of memory, KeyError, ..."),
    DataColumn("message", "the error's message, with numbers and IDs blanked so alike errors group together"),
    DataColumn("count", "error lines with this cause in the window", "count", bar=True),
    DataColumn("share", "its part of all the error lines", "share",
               read="The top rows are what fixing would remove the most errors."),
    DataColumn("state", "whether it's still failing, new or has stopped", tones=_STATE_TONES),
    DataColumn("first_seen", "its first error line in the window", "time"),
    DataColumn("last_seen", "its last error line", "time"),
    DataColumn("versions", "the function versions it happened in"),
    DataColumn("example", "one whole message, as logged"),
    DataColumn("request_id", "the newest run with this error: logs(name, request_id=...) shows it whole", "id"),
]
_ERROR_LINE_COLUMNS = [
    DataColumn("time", "when it was logged", "time"),
    DataColumn("request_id", "the run that logged it", "id"),
    DataColumn("message", "the line as logged"),
    DataColumn("stream", "the log stream: one container, running one call at a time", "id"),
]
_PERIOD_COLUMNS = [
    DataColumn("start", "the start of the period", "time"),
    DataColumn("invocations", "calls CloudWatch counted", "count"),
    DataColumn("errors", "failed calls", "count", bar=True),
    DataColumn("error_rate", "errors / invocations", "share"),
    DataColumn("throttles", "calls refused because too many ran at once", "count"),
]


def _cause_rows(report: ErrorReport) -> list[dict[str, Any]]:
    total = sum(group.count for group in report.groups)
    rows = []
    for group in report.groups:
        state = cause_state(group, since=report.since, until=report.until, metrics=report.metrics)
        rows.append({
            "kind": group.kind, "message": group.message, "count": group.count, "share": _fraction(group.count, total),
            "state": state.state, "first_seen": group.first, "last_seen": group.last,
            "versions": group.versions, "example": group.example,
            "request_id": group.request_ids[0] if group.request_ids else None,
        })
    return rows


def _period_rows(report: ErrorReport) -> list[dict[str, Any]]:
    metrics = report.metrics
    return [{"start": day.start, "invocations": day.invocations, "errors": day.errors,
             "error_rate": _fraction(day.errors, day.invocations), "throttles": day.throttles}
            for day in (metrics.daily if metrics else [])]


def errors_data(report: ErrorReport) -> ReportData:
    """
    errors()' report as data: its causes, the newest error lines and CloudWatch's failed calls per period.
    """
    fn = report.function
    causes = DataTable(
        "causes", f"Why {fn.name} failed", "a cause: errors alike enough to share one fix", _CAUSE_COLUMNS,
        _cause_rows(report),
        notes=["The most frequent cause is first; share adds up to 1 across the table.",
               "A cause that's 'stopped' had no line for a fifth of the window: likely fixed or no longer hit."],
        recipes=[Recipe("df[df.state != 'stopped'][['kind', 'count', 'last_seen']]", "what's still failing"),
                 Recipe("df.groupby('kind')['count'].sum()", "errors by kind")],
    )
    lines = DataTable(
        "error_lines", "The newest error lines", "an error line", _ERROR_LINE_COLUMNS,
        [{"time": e.time, "request_id": e.request_id, "message": e.message, "stream": e.stream}
         for e in report.newest],
        notes=["Newest first. A run's other lines are in logs(name, request_id=...)."],
        recipes=[Recipe("df.request_id.nunique()", "how many runs these lines come from")],
    )
    periods = DataTable(
        "calls", "Calls and failed calls, from CloudWatch", "a period CloudWatch counted (an hour, in a day's window)", _PERIOD_COLUMNS,
        _period_rows(report),
        notes=["CloudWatch counts failed calls even when nothing was logged, so errors can exceed the error lines."],
        recipes=[Recipe("df.set_index('start')[['invocations', 'errors']].plot()", "calls and failures over time")],
    )
    charts = [
        DataChart("Errors by cause", "causes", "kind", ("count",), "bar",
                  "Each cause's error lines: the longest bar is the fix that helps most.", "error lines"),
        DataChart("Failed calls over time", "calls", "start", ("errors",), "column",
                  "Failed calls per period, from CloudWatch: a tall bar is a burst of failures.", "failed calls"),
    ]
    tables = [causes, lines] + ([periods] if periods.rows else [])
    charts = [chart for chart in charts if chart.table in {table.name for table in tables}]
    return ReportData(f"Errors in {fn.name}", tables, charts, stem=_stem("errors", fn.name))



_RUN_COLUMNS = [
    DataColumn("time", "when the run ended (its REPORT line)", "time"),
    DataColumn("request_id", "the run's request ID", "id"),
    DataColumn("duration_ms", "how long the code ran", "ms", bar=True),
    DataColumn("billed_ms", "the run time Lambda billed", "ms"),
    DataColumn("timeout_share", "duration as a share of the timeout", "share",
               read="Over 0.8 runs close to being stopped; 1 means it timed out."),
    DataColumn("memory_mb", "the memory it had", "mb"),
    DataColumn("max_memory_mb", "the most memory it used", "mb"),
    DataColumn("memory_share", "max_memory_mb / memory_mb", "share",
               read="Over 0.9 risks running out; under 0.3 everywhere means memory could come down."),
    DataColumn("cold_start", "whether a new container started for it", "flag"),
    DataColumn("init_ms", "start-up time of a cold start", "ms"),
    DataColumn("timed_out", "whether it hit the timeout", "flag"),
    DataColumn("status", "'timeout' or 'error' when Lambda said it didn't succeed", tones=(("timeout", "bad"),
                                                                                        ("error", "bad"))),
    DataColumn("error_type", "the kind of error Lambda reported: Runtime.OutOfMemory, ..."),
    DataColumn("version", "the version that ran it, from the log stream", "id"),
    DataColumn("stream", "the log stream it ran in", "id"),
]
_HOUR_COLUMNS = [
    DataColumn("hour", "the start of the hour", "time"),
    DataColumn("runs", "runs that ended in the hour", "count", bar=True),
    DataColumn("median_ms", "the typical run time of the runs that finished", "ms"),
    DataColumn("p95_ms", "the run time 95 in 100 finished runs stayed under", "ms",
               read="A p95 far above the median means a slow tail worth looking at."),
    DataColumn("longest_ms", "the longest finished run", "ms"),
    DataColumn("timeouts", "runs that hit the timeout", "count"),
    DataColumn("cold_starts", "runs that started a new container", "count"),
    DataColumn("max_memory_mb", "the most memory a run used", "mb"),
]


def _invocation_row(run: Invocation, timeout_s: int) -> dict[str, Any]:
    return {
        "time": run.time, "request_id": run.request_id, "duration_ms": run.duration, "billed_ms": run.billed,
        "timeout_share": _fraction(run.duration, timeout_s * 1000), "memory_mb": run.memory,
        "max_memory_mb": run.max_memory, "memory_share": _fraction(run.max_memory, run.memory),
        "cold_start": run.init is not None, "init_ms": run.init, "timed_out": timed_out(run, timeout_s),
        "status": run.status, "error_type": run.error_type, "version": run.version, "stream": run.stream,
    }


def _hour_row(hour: datetime, runs: list[dict[str, Any]]) -> dict[str, Any]:
    finished = [row["duration_ms"] for row in runs if not row["timed_out"]]
    return {
        "hour": hour, "runs": len(runs), "median_ms": median_of(finished), "p95_ms": nearest_rank(finished, _P95),
        "longest_ms": max(finished, default=None), "timeouts": sum(1 for row in runs if row["timed_out"]),
        "cold_starts": sum(1 for row in runs if row["cold_start"]),
        "max_memory_mb": max((row["max_memory_mb"] for row in runs), default=None),
    }


def performance_data(perf: Performance) -> ReportData:
    """
    performance()'s report as data: every run, oldest first, and the runs summed up hour by hour.
    """
    fn = perf.function
    rows = sorted((_invocation_row(run, fn.timeout) for run in perf.invocations if run.time),
                  key=lambda row: row["time"])
    hours = [_hour_row(hour, runs) for hour, runs in by_hour(rows, time="time").items()]
    runs = DataTable(
        "runs", f"Every run of {fn.name}", "a run, from the REPORT line Lambda logs after it", _RUN_COLUMNS, rows,
        notes=[f"Its timeout is {fn.timeout} s and its memory {fn.memory:,} MB.",
               "A run that timed out only tells you the timeout, so typical run times leave timeouts out."],
        recipes=[Recipe("df[~df.timed_out].duration_ms.describe(percentiles=[.5, .9, .99])", "run times"),
                 Recipe("df.groupby('cold_start')[['duration_ms', 'init_ms']].median()",
                        "run time and start-up, warm against cold: a cold start adds its init_ms"),
                 Recipe("df.groupby('version').duration_ms.median()", "whether a version got slower"),
                 Recipe("df.nlargest(10, 'duration_ms')[['time', 'request_id', 'duration_ms']]",
                        "the slowest runs, to open with logs(..., request_id=...)")],
    )
    by_hours = DataTable(
        "hours", "Runs hour by hour", "an hour with runs", _HOUR_COLUMNS, hours,
        notes=["Hours without runs are left out."],
        recipes=[Recipe("df.set_index('hour')[['median_ms', 'p95_ms']].plot()", "run times over the day")],
    )
    charts = [
        DataChart("Every run's time", "runs", "time", ("duration_ms",), "scatter",
                  f"A dot per run. The timeout is {fn.timeout * 1000:,} ms: dots near it are close to being stopped.",
                  "ms"),
        DataChart("Typical and slow run time by hour", "hours", "hour", ("median_ms", "p95_ms"), "line",
                  "The median and the 95th percentile of finished runs; a widening gap is a growing slow tail.", "ms"),
        DataChart("Runs, timeouts and cold starts by hour", "hours", "hour", ("runs", "timeouts", "cold_starts"),
                  "column", "How busy each hour was, and how many runs timed out or started cold.", "runs"),
    ]
    return ReportData(f"How {fn.name} runs", [runs, by_hours], charts, stem=_stem("performance", fn.name))



_BUILTIN_COLUMNS = {
    "status": DataColumn("status", "ok, failed, timeout, logged (an error it may have handled) or running",
                         tones=_STATUS_TONES),
    "failed": DataColumn("failed", "whether it failed or timed out", "flag"),
    "started": DataColumn("started", "when it started", "time"),
    "day": DataColumn("day", "the day it started, in the reports' zone"),
    "hour": DataColumn("hour", "the hour it started, 0 to 23, in the reports' zone", "number"),
    "duration": DataColumn("duration", "how long it ran", "ms", bar=True),
    "billed": DataColumn("billed", "the run time Lambda billed", "ms"),
    "memory": DataColumn("memory", "memory it used", "mb"),
    "memory_size": DataColumn("memory_size", "memory it had", "mb"),
    "memory_share": DataColumn("memory_share", "memory used / memory it had", "share",
                               read="Over 0.9 risks running out of memory."),
    "timeout_share": DataColumn("timeout_share", "run time / the timeout", "share",
                                read="Over 0.8 is close to being stopped."),
    "cold": DataColumn("cold", "whether it was a cold start", "flag"),
    "init": DataColumn("init", "start-up time of a cold start", "ms"),
    "version": DataColumn("version", "the version that ran it", "id"),
    "error_type": DataColumn("error_type", "what kind of error it hit"),
    "error": DataColumn("error", "the error it hit, as logged"),
    "summary": DataColumn("summary", "what failed, else the first thing it logged"),
    "lines": DataColumn("lines", "how many lines it logged", "count"),
    "request_id": DataColumn("request_id", "its request ID", "id"),
    "stream": DataColumn("stream", "the log stream it ran in", "id"),
}
_PERCENT_FIELDS = frozenset({"memory_share", "timeout_share"})


def _logged_column(name: str, about: str, sample: Any) -> DataColumn:
    """
    A column for a field the function logs, its kind guessed from a value.
    """
    return DataColumn(name, about, _LOGGED_KIND.get(type(sample), "text"))


def _run_columns(table: RunTable) -> list[DataColumn]:
    """
    Lambda's own fields first, then what the function logs, in the order RunTable lists them.
    """
    columns = [column for name, column in _BUILTIN_COLUMNS.items() if name in table.field_names()]
    for info in table.fields():
        if info.name in _BUILTIN_COLUMNS or info.name in _SKIPPED_RUN_FIELDS:
            continue
        sample = next((row.values.get(info.name) for row in table.rows if row.values.get(info.name) is not None),
                      None)
        columns.append(_logged_column(info.name, f"{info.about} (in {info.runs:,} runs)", sample))
    return columns


def _run_row(values: dict[str, Any], columns: list[DataColumn]) -> dict[str, Any]:
    row = {column.name: values.get(column.name) for column in columns}
    for name in _PERCENT_FIELDS & row.keys():
        row[name] = row[name] / 100 if row[name] is not None else None
    return row


def _run_hours(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hours = []
    for hour, runs in by_hour(rows, time="started").items():
        durations = [row["duration"] for row in runs if row.get("duration") is not None]
        hours.append({"hour": hour, "runs": len(runs), "failed": sum(1 for row in runs if row.get("failed")),
                      "cold_starts": sum(1 for row in runs if row.get("cold")),
                      "median_ms": median_of(durations), "p95_ms": nearest_rank(durations, _P95)})
    return hours


_RUN_HOUR_COLUMNS = [
    DataColumn("hour", "the start of the hour", "time"),
    DataColumn("runs", "runs that started in the hour", "count", bar=True),
    DataColumn("failed", "runs that failed or timed out", "count"),
    DataColumn("cold_starts", "runs that started a new container", "count"),
    DataColumn("median_ms", "the typical run time", "ms"),
    DataColumn("p95_ms", "the run time 95 in 100 runs stayed under", "ms"),
]


def runs_data(table: RunTable) -> ReportData:
    """
    runs()' report as data: every run with Lambda's fields and the ones it logs, and the runs per hour.
    """
    fn = table.function
    columns = _run_columns(table)
    rows = [_run_row(row.values, columns) for row in table.rows]
    runs = DataTable(
        "runs", f"Runs of {fn.name}", "a run: one call, from START to REPORT", columns, rows,
        notes=["Newest first. Fields after stream are the ones its JSON logs or logged event carry.",
               "where= and sort= in runs() work on the same fields: runs(name, where='duration > 15s')."],
        recipes=[Recipe("df.groupby('status').size()", "how the runs ended"),
                 Recipe("df[df.failed].groupby('error_type').size().sort_values()", "what the failures were"),
                 Recipe("df.groupby('hour').duration.median()", "run time by hour of day")],
    )
    hours = DataTable("hours", "Runs hour by hour", "an hour with runs", _RUN_HOUR_COLUMNS, _run_hours(rows),
                      notes=["Hours without runs are left out."])
    charts = [DataChart("Runs and failures by hour", "hours", "hour", ("runs", "failed"), "column",
                        "How many runs started each hour, and how many failed.", "runs"),
              DataChart("Every run's time", "runs", "started", ("duration",), "scatter",
                        f"A dot per run. Its timeout is {fn.timeout * 1000:,} ms.", "ms")]
    return ReportData(f"Runs of {fn.name}", [runs, hours], charts, stem=_stem("runs", fn.name))



_LINE_COLUMNS = [
    DataColumn("time", "when it was logged", "time"),
    DataColumn("request_id", "the run it belongs to (empty for start-up lines)", "id"),
    DataColumn("message", "the line, as logged"),
    DataColumn("matched", "whether it matched the search", "flag"),
    DataColumn("stream", "the log stream: one container", "id"),
]
_LINE_HOUR_COLUMNS = [
    DataColumn("hour", "the start of the hour", "time"),
    DataColumn("lines", "lines logged in the hour", "count", bar=True),
    DataColumn("runs", "runs those lines belong to", "count"),
]


def logs_data(page: LogPage) -> ReportData:
    """
    logs()' report as data: every line read, oldest first, and the lines per hour.
    """
    fn = page.function
    rows = [{"time": e.time, "request_id": e.request_id, "message": e.message, "matched": e.matched,
             "stream": e.stream} for e in page.events]
    hours = [{"hour": hour, "lines": len(lines), "runs": len({row["request_id"] for row in lines if row["request_id"]})}
             for hour, lines in by_hour(rows, time="time").items()]
    lines = DataTable(
        "lines", f"Log lines of {fn.name}", "a line it logged", _LINE_COLUMNS, rows,
        notes=["Oldest first. A run's lines share its request_id; START, END and REPORT are Lambda's own."],
        recipes=[Recipe("df.groupby('request_id').message.apply('\\n'.join)", "each run's lines together"),
                 Recipe("df[df.message.str.contains('ERROR')]", "lines with ERROR in them")],
    )
    per_hour = DataTable("hours", "Lines hour by hour", "an hour with lines", _LINE_HOUR_COLUMNS, hours)
    charts = [DataChart("Lines by hour", "hours", "hour", ("lines",), "column", "How much it logged each hour.",
                        "lines")]
    return ReportData(f"Logs of {fn.name}", [lines, per_hour], charts, stem=_stem("logs", fn.name))
