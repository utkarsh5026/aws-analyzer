"""
A function's runs as rows of fields, to filter, sort and group: what Lambda says about each run (status, run time,
memory, cold start...), every field its JSON log lines carry, the event when it logs it, and fields read out of text
lines by patterns the user gives. RunTable holds them; where(), sorted_by() and group() answer questions about
them without another read from AWS.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Iterable

from ..._kit.deps import _require
from ..._kit.fmt import _in_zone, _plural

from ..helpers import human_ms, stream_version
from ..models import Function, LogEvent
from .logs import LogPage, LogRun, _marker
from .performance import percentile
from .query import FIELD_KINDS, QueryError, RunQuery, closest_field, parse_query, short_name_of


BUILTIN_FIELDS = {
    "status": "ok, failed, timeout, logged (an error it may have handled) or running",
    "failed": "whether it failed or timed out",
    "started": "when it started (compare with 14:00, 2h, today or 2026-10-31T09:00)",
    "day": "the day it started, 2026-10-31",
    "hour": "the hour it started, 0 to 23",
    "duration": "how long it ran (15s, 500ms, 2m)",
    "billed": "the run time Lambda billed",
    "memory": "memory it used, in MB",
    "memory_size": "memory it had, in MB",
    "memory_share": "memory it used, as a share of what it had (80%)",
    "timeout_share": "its run time as a share of the timeout (50%)",
    "cold": "whether it was a cold start",
    "init": "start-up time of a cold start",
    "version": "the version that ran it ($LATEST, 3)",
    "error_type": "what kind of error it hit (KeyError, Timeout...)",
    "error": "the error it hit, as it logged it",
    "summary": "what failed, else the first thing it logged",
    "text": "every line it logged, for ~ (contains)",
    "lines": "how many lines it logged",
    "request_id": "its request ID",
    "stream": "the log stream it ran in",
}

DEFAULT_COLUMNS = ("status", "started", "duration", "memory", "cold", "summary", "request_id")

EVENT_PREFIX = "event."

_LINE_KEYS = ("message", "msg", "level", "levelname", "severity", "timestamp", "time", "requestId", "AWSRequestId",
              "type", "record")

_MAX_DEPTH = 6

_MAX_FIELDS_PER_RUN = 200

_SUMMARY_CHARS = 160

_FAILED = ("failed", "timeout")

_TIME_GROUPS = ("hour", "day")


@dataclass
class LoggedEvent:
    """
    The event a run was called with, from the line that logged it (a JSON line with an "event" object, or
    Powertools' log_event=True, which logs the event as the line's message).
    """

    event: dict[str, Any]
    line: LogEvent


def _json_of(message: str) -> dict[str, Any] | None:
    text = message.strip()
    if not text.startswith("{"):
        brace = text.find("\t{")
        text = text[brace + 1:] if brace >= 0 else ""
    if not text.startswith("{"):
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _is_error_doc(doc: dict[str, Any]) -> bool:
    return bool(doc.get("errorType") or doc.get("errorMessage"))


def logged_event(run: LogRun) -> LoggedEvent | None:
    """
    The event the run was called with, when it logged it; None when it didn't (Lambda itself never logs it).
    """
    for line in run.events:
        doc = _json_of(line.message)
        if doc is None or str(doc.get("type", "")).startswith("platform."):
            continue
        if isinstance(doc.get("event"), dict):
            return LoggedEvent(doc["event"], line)
        message = doc.get("message")
        if isinstance(message, dict) and not _is_error_doc(message):
            return LoggedEvent(message, line)
    return None


def flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """
    Nested JSON as dotted paths: {"s3": {"object": {"size": 5}}} -> {"s3.object.size": 5}. A list of objects is
    read through its first one (Records.s3.object.size), as an event of one record usually is; other lists stay
    whole.
    """
    if isinstance(value, list) and value and isinstance(value[0], dict) and depth < _MAX_DEPTH:
        return flatten(value[0], prefix, depth + 1)
    if not isinstance(value, dict) or depth >= _MAX_DEPTH:
        return {prefix: value} if prefix else {}
    flat: dict[str, Any] = {}
    for key, inner in value.items():
        flat.update(flatten(inner, f"{prefix}.{key}" if prefix else str(key), depth + 1))
    return flat


def _logged_fields(run: LogRun) -> dict[str, Any]:
    """
    Every field the run's JSON log lines carry, by dotted path, the first value of each.
    """
    found: dict[str, Any] = {}
    for line in run.events:
        doc = _json_of(line.message)
        if doc is None or str(doc.get("type", "")).startswith("platform."):
            continue
        rest = {key: value for key, value in doc.items() if key not in _LINE_KEYS and key != "event"}
        for name, value in flatten(rest).items():
            found.setdefault(name, value)
        if len(found) >= _MAX_FIELDS_PER_RUN:
            break
    return found


def compile_patterns(patterns: dict[str, str] | None) -> dict[str, re.Pattern[str]]:
    """
    {"written": r"wrote (\\d+) orders"} -> the compiled patterns; a pattern that isn't a valid regular expression
    raises a ValueError that names it.
    """
    compiled = {}
    for name, pattern in (patterns or {}).items():
        try:
            compiled[str(name)] = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"fields={{{name!r}: {pattern!r}}} isn't a regular expression Python can read: "
                             f"{exc}") from None
    return compiled


def _pattern_value(text: str) -> Any:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return text


def _pattern_fields(run: LogRun, patterns: dict[str, re.Pattern[str]]) -> dict[str, Any]:
    """
    What each pattern finds in the run's lines: its first group in the first line it matches (the whole match when
    it has no group), as a number when it reads as one.
    """
    found: dict[str, Any] = {}
    for name, pattern in patterns.items():
        for line in run.events:
            match = pattern.search(line.message)
            if match is not None:
                found[name] = _pattern_value(match.group(1) if match.groups() else match.group(0))
                break
    return found


def _own_lines(run: LogRun) -> list[LogEvent]:
    return [line for line in run.events if _marker(line.message) is None]


def _line_text(message: str) -> str:
    doc = _json_of(message)
    if doc is not None:
        inner = doc.get("message", doc.get("msg"))
        return inner if isinstance(inner, str) else ""
    return re.sub(r"^\S+\t\S+\t(?:\S+\t)?", "", message.strip())


def run_summary(run: LogRun) -> str:
    """
    What failed in the run, else the first thing it logged, on one line.
    """
    if run.error is not None:
        return run.error[1].splitlines()[0][:_SUMMARY_CHARS]
    for line in _own_lines(run):
        text = _line_text(line.message).strip()
        if text:
            return text.splitlines()[0][:_SUMMARY_CHARS]
    return ""


def _percent_of(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100 * part / whole, 1)


def _lambda_fields(run: LogRun, timeout_seconds: int) -> dict[str, Any]:
    """
    What Lambda's own lines say about the run.
    """
    report = run.report
    started = _in_zone(run.start)
    error = run.error
    return {
        "status": run.status,
        "failed": run.status in _FAILED,
        "started": run.start,
        "day": f"{started:%Y-%m-%d}",
        "hour": started.hour,
        "duration": run.duration,
        "billed": report.billed if report is not None else None,
        "memory": report.max_memory if report is not None and report.memory else None,
        "memory_size": report.memory if report is not None and report.memory else None,
        "memory_share": _percent_of(report.max_memory, report.memory) if report is not None else None,
        "timeout_share": _percent_of(run.duration, timeout_seconds * 1000),
        "cold": run.cold,
        "init": report.init if report is not None else None,
        "version": stream_version(run.stream),
        "error_type": error[0] if error is not None else None,
        "error": error[1] if error is not None else None,
        "summary": run_summary(run),
        "text": "\n".join(line.message for line in run.events),
        "lines": len(_own_lines(run)),
        "request_id": run.request_id,
        "stream": run.stream,
    }


def run_fields(run: LogRun, *, timeout_seconds: int = 0,
               patterns: dict[str, re.Pattern[str]] | None = None) -> dict[str, Any]:
    """
    One run as {field: value}: Lambda's fields (BUILTIN_FIELDS), then the fields its JSON lines carry, the event as
    event.* when it logged it, and what `patterns` find in its lines. A logged field never replaces a built-in one.
    """
    values = _logged_fields(run)
    event = logged_event(run)
    if event is not None:
        values.update({EVENT_PREFIX + name: value for name, value in flatten(event.event).items()})
    values.update(_pattern_fields(run, patterns or {}))
    values.update(_lambda_fields(run, timeout_seconds))
    return values


@dataclass
class RunRow:
    """
    One run and its fields.
    """

    run: LogRun
    values: dict[str, Any]


@dataclass
class FieldInfo:
    """
    A field the runs have: in how many of them, and what it is (BUILTIN_FIELDS' words, or where it came from).
    """

    name: str
    runs: int
    about: str


@dataclass
class RunGroup:
    """
    The runs that share a value of the field they were grouped by, summed up.
    """

    value: Any
    runs: int
    failed: int
    cold: int
    median: float | None
    slowest: float | None
    rows: list[RunRow] = field(default_factory=list, repr=False)


def _group_key(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _summed(value: Any, rows: list[RunRow]) -> RunGroup:
    durations = [row.run.duration for row in rows if row.run.duration is not None]
    return RunGroup(
        value=value,
        runs=len(rows),
        failed=sum(1 for row in rows if row.run.status in _FAILED),
        cold=sum(1 for row in rows if row.run.cold),
        median=percentile(durations, 50),
        slowest=max(durations) if durations else None,
        rows=rows,
    )


def _sort_value(value: Any) -> tuple[int, Any]:
    if value is None or value == "":
        return (2, 0)
    if isinstance(value, bool):
        return (0, float(value))
    if isinstance(value, (int, float)):
        return (0, float(value))
    if isinstance(value, datetime):
        return (0, value.timestamp())
    return (1, str(value).lower())


def parse_sort(spec: str | Iterable[str] | None) -> list[tuple[str, bool]]:
    """
    '-duration' -> [('duration', True)] (largest first); 'started' -> oldest first; a list sorts by each in turn.
    """
    if spec is None:
        return []
    parts = [spec] if isinstance(spec, str) else list(spec)
    keys = []
    for part in parts:
        for piece in str(part).split(","):
            name = piece.strip()
            if name:
                keys.append((name.lstrip("-+").strip(), name.startswith("-")))
    return keys


@dataclass
class RunTable:
    """
    A function's runs from `since` to `until` as rows of fields (RunRow), newest first, or as where() and
    sorted_by() left them. `query` is the filter applied, `truncated` says reading stopped at the limit before
    `since` (the rows reach back to `covered_from`), `read` is how many runs were read before any filter, and
    `unfiltered` the table before where() (None for a table no query has filtered).
    """

    function: Function
    since: datetime
    until: datetime
    rows: list[RunRow] = field(default_factory=list)
    query: str = ""
    sort: list[tuple[str, bool]] = field(default_factory=list)
    read: int = 0
    truncated: bool = False
    covered_from: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)
    unfiltered: RunTable | None = field(default=None, repr=False)

    @property
    def runs(self) -> list[LogRun]:
        return [row.run for row in self.rows]

    def fields(self) -> list[FieldInfo]:
        """
        Every field the rows have: Lambda's first, in BUILTIN_FIELDS' order, then the ones the function logs, the
        most common first.
        """
        counts: Counter[str] = Counter()
        for row in self.rows:
            counts.update(name for name, value in row.values.items() if value is not None)
        known = [FieldInfo(name, counts[name], about) for name, about in BUILTIN_FIELDS.items()]
        logged = sorted((name for name in counts if name not in BUILTIN_FIELDS), key=lambda n: (-counts[n], n))
        return known + [FieldInfo(name, counts[name], _logged_about(name)) for name in logged]

    def field_names(self) -> list[str]:
        return [info.name for info in self.fields()]

    def resolve(self, query: str | RunQuery) -> RunQuery:
        """
        The query parsed, with each short field name that stands for one field the rows have spelled out ('size' ->
        'event.Records.s3.object.size').
        """
        parsed = parse_query(query) if isinstance(query, str) else query
        names = self.field_names()
        known = set(names)
        spelled = {}
        by_lower = {name.lower(): name for name in names}
        for name in parsed.fields:
            if name in known:
                continue
            full = by_lower.get(name.lower()) or short_name_of(name, names)
            if full is not None:
                spelled[name] = full
        return parsed.renamed(spelled) if spelled else parsed

    def unknown_fields(self, query: str | RunQuery) -> list[tuple[str, str | None]]:
        """
        (field, the field it most likely meant) for each field a query names that no row has.
        """
        parsed = self.resolve(query)
        names = set(self.field_names())
        return [(name, closest_field(name, names)) for name in parsed.fields if name not in names]

    def where(self, query: str | RunQuery | None) -> RunTable:
        """
        The rows a query keeps ('duration > 15s and cold'): a new RunTable, this one unchanged. Raises QueryError
        when the query can't be read.
        """
        if query is None or (isinstance(query, str) and not query.strip()):
            return self
        parsed = self.resolve(query)
        kept = [row for row in self.rows if parsed.matches(row.values)]
        joined = f"({self.query}) and ({parsed.text})" if self.query else parsed.text
        return replace(self, rows=kept, query=joined, unfiltered=self.unfiltered or self)

    def sorted_by(self, spec: str | Iterable[str] | None) -> RunTable:
        """
        The rows in another order: '-duration' (longest first), 'memory', ['-status', '-duration']. Runs without
        the field go last.
        """
        keys = parse_sort(spec)
        if not keys:
            return self
        rows = list(self.rows)
        for name, descending in reversed(keys):
            present = [row for row in rows if _sort_value(row.values.get(name))[0] < 2]
            missing = [row for row in rows if _sort_value(row.values.get(name))[0] == 2]
            present.sort(key=lambda row, n=name: _sort_value(row.values.get(n)), reverse=descending)
            rows = present + missing
        return replace(self, rows=rows, sort=keys)

    def group(self, by: str) -> list[RunGroup]:
        """
        The rows summed up per value of a field: runs, failed, cold starts, median and longest run time. Hours and
        days come in time order, anything else the most runs first; runs without the field are one group, None.
        """
        buckets: dict[Any, list[RunRow]] = {}
        for row in self.rows:
            buckets.setdefault(_group_key(row.values.get(by)), []).append(row)
        groups = [_summed(value, rows) for value, rows in buckets.items()]
        if by in _TIME_GROUPS:
            return sorted(groups, key=lambda g: _sort_value(g.value))
        return sorted(groups, key=lambda g: (g.value is None, -g.runs, _sort_value(g.value)))

    def to_df(self, columns: Iterable[str] | None = None):
        """
        The rows as a pandas DataFrame: every field, or the columns named.
        """
        pd = _require("pandas", "RunTable.to_df()")
        names = list(columns) if columns is not None else [n for n in self.field_names() if n != "text"]
        return pd.DataFrame([{name: row.values.get(name) for name in names} for row in self.rows], columns=names)


def _logged_about(name: str) -> str:
    if name.startswith(EVENT_PREFIX):
        return "from the event it logged"
    return "a field it logs"


def run_table(page: LogPage, *, fields: dict[str, str] | None = None) -> RunTable:
    """
    The runs a LogPage read (LambdaAnalyzer.log_runs()) as a RunTable, newest first. fields= reads more fields out
    of text lines: {"written": r"wrote (\\d+) orders"}.
    """
    patterns = compile_patterns(fields)
    timeout = page.function.timeout or 0
    rows = [RunRow(run, run_fields(run, timeout_seconds=timeout, patterns=patterns))
            for run in page.runs if run.request_id is not None]
    return RunTable(page.function, page.since, page.until, rows, read=len(rows), truncated=page.truncated,
                    covered_from=page.covered_from, errors=dict(page.errors))


def format_value(name: str, value: Any) -> str:
    """
    A field's value as a report shows it: run times as '15.2 s', memory as '388 MB', shares as '62%', a cold start
    as 'cold', times in the reports' zone.
    """
    if value is None or value == "":
        return ""
    kind = FIELD_KINDS.get(name, "")
    if kind == "ms":
        return human_ms(value)
    if kind == "mb":
        return f"{value:,.0f} MB"
    if kind == "pct":
        return f"{value:.0f}%"
    if name == "cold":
        return "cold" if value else ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return f"{_in_zone(value):%Y-%m-%d %H:%M:%S}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)[:_SUMMARY_CHARS]
    return str(value)


def _timeout_finding(table: RunTable, ref: str) -> tuple[str, str] | None:
    timeouts = [row for row in table.rows if row.run.status == "timeout"]
    if not timeouts:
        return None
    first = timeouts[0].run
    one = len(timeouts) == 1
    stopped = "it, so what it was" if one else "them, so what they were"
    return ("warn", f"{_plural(len(timeouts), 'run')} reached the {table.function.timeout}s timeout: Lambda stopped "
                    f"{stopped} doing didn't finish. Read {'it' if one else 'one'} from start to end "
                    f"(logs('{ref}', request_id='{first.request_id}')), then make it faster or raise the timeout "
                    f"(aws lambda update-function-configuration --function-name {ref} --timeout "
                    f"{min(900, table.function.timeout * 2)}).")


def _memory_finding(table: RunTable) -> tuple[str, str] | None:
    tight = [row for row in table.rows if (row.values.get("memory_share") or 0) >= 90]
    if not tight:
        return None
    return ("warn", f"{_plural(len(tight), 'run')} used 90% or more of {table.function.memory:,} MB: one bigger input "
                    "and it runs out of memory. Look at what they read (sort='-memory'), or give it more memory.")


def _unknown_finding(name: str, meant: str | None, everything: RunTable) -> tuple[str, str]:
    if name.startswith(EVENT_PREFIX) and not any(n.startswith(EVENT_PREFIX) for n in everything.field_names()):
        return ("info", f"No run logged its event, so {name} matches nothing: Lambda never logs the event itself. Log it at "
                        "the start of the handler (Powertools: @logger.inject_lambda_context(log_event=True)).")
    if meant:
        return ("warn", f"No run has a field {name!r}: did you mean {meant!r}?")
    return ("warn", f"No run has a field {name!r}, so it matches and shows nothing. The fields table at the end "
                    "lists the ones the runs have.")


def _named_fields(table: RunTable, named: Iterable[str]) -> list[str]:
    names: list[str] = []
    if table.query:
        try:
            names += (table.unfiltered or table).resolve(table.query).fields
        except QueryError:
            pass
    names += [name for name, _ in table.sort] + list(named)
    return list(dict.fromkeys(names))


def runs_findings(table: RunTable, *, ref: str = "", named: Iterable[str] = ()) -> list[tuple[str, str]]:
    """
    What the runs a query kept call for: fields the query, the sort or `named` (columns, group_by) name that no run
    has (with the likely one), runs that reached the timeout, runs close to running out of memory, and reading that
    stopped before `since`.
    """
    everything = table.unfiltered or table
    ref = ref or table.function.name
    known = set(everything.field_names())
    found = [_unknown_finding(name, closest_field(name, known), everything)
             for name in _named_fields(table, named) if name not in known]
    for finding in (_timeout_finding(table, ref), _memory_finding(table)):
        if finding is not None:
            found.append(finding)
    if everything.truncated and everything.covered_from is not None:
        found.append(("info", f"Read the newest {_plural(everything.read, 'run')} only: reading stopped at the "
                              f"limit, back to {_in_zone(everything.covered_from):%Y-%m-%d %H:%M}. A shorter since= "
                              "or a bigger limit= covers the rest."))
    return found
