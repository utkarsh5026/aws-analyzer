"""
runs()'s report: a function's runs as a table you choose the columns of, filtered by a query, sorted, and summed up
per hour, error or any field, with the fields there are to query folded at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone
from ..._kit.fmt import _count, _fmt_dt, _plural, _share, _tz_label

from ..helpers import human_ms
from ..analysis.performance import percentile
from ..analysis.runs import DEFAULT_COLUMNS, RunGroup, RunTable, format_value, runs_findings

_STATUS_TONES = {"failed": "bad", "timeout": "bad", "logged": "warn"}

_HEADERS = {
    "status": "Status", "failed": "Failed", "day": "Day", "hour": "Hour", "duration": "Run time", "billed": "Billed",
    "memory": "Memory used", "memory_size": "Memory", "memory_share": "Memory share", "timeout_share": "Of timeout",
    "cold": "Cold start", "init": "Start-up", "version": "Version", "error_type": "Error type", "error": "Error",
    "summary": "What it logged", "lines": "Lines", "request_id": "Request ID", "stream": "Log stream",
}

_ORDER_WORDS = {"duration": ("longest", "shortest"), "started": ("newest", "oldest"), "memory": ("most memory",
                                                                                                 "least memory")}


def _header(name: str) -> str:
    if name in ("started", "hour"):
        return f"{_HEADERS.get(name, 'Started')} ({_tz_label()})"
    return _HEADERS.get(name, name)


def _cell(name: str, value: Any) -> Any:
    if name == "status" and value:
        return _Tone(str(value), _STATUS_TONES.get(str(value), ""))
    if name == "started":
        return _fmt_dt(value, seconds=True)
    return format_value(name, value)


def _order_words(table: RunTable) -> str:
    if not table.sort:
        return "newest first"
    name, descending = table.sort[0]
    words = _ORDER_WORDS.get(name)
    if words is not None:
        return f"{words[0] if descending else words[1]} first"
    return f"by {name}" + (", largest first" if descending else "")


def _run_cards(table: RunTable, findings: list[tuple[str, str]]) -> _Cards:
    everything = table.unfiltered or table
    runs = table.runs
    durations = [run.duration for run in runs if run.duration is not None]
    failed = sum(1 for run in runs if run.status in ("failed", "timeout"))
    warned = " ".join(message for level, message in findings if level == "warn")
    longest = max(durations, default=None)
    matched = f"{_count(len(runs))} of {_count(everything.read)}" if table.query else _count(len(runs))
    return _Cards([
        ("Runs" + (" that match" if table.query else ""), matched),
        ("Failed", _count(failed), "bad" if failed and "timeout" in warned else ""),
        ("Median run time", human_ms(percentile(durations, 50))),
        ("Longest", f"{human_ms(longest)} of {table.function.timeout} s" if longest is not None else "-",
         "warn" if "timeout" in warned else ""),
        ("Cold starts", _count(sum(1 for run in runs if run.cold))),
    ])


def _group_table(table: RunTable, by: str) -> _Table:
    groups = table.group(by)
    total = len(table.rows)

    def row(group: RunGroup) -> list[Any]:
        label = "(none)" if group.value is None else _group_label(by, group.value)
        failed = _Tone(_count(group.failed), "bad") if group.failed else "0"
        return [label, _count(group.runs), failed, _count(group.cold), human_ms(group.median),
                human_ms(group.slowest)]

    return _Table([_header(by), "Runs", "Failed", "Cold starts", "Median", "Longest"], [row(g) for g in groups],
                  bars=[_share(g.runs, total) for g in groups], bar_label="Share of runs", max_rows=0,
                  title=f"The {_plural(len(table.rows), 'run')} by {_HEADERS.get(by, by).lower()}")


def _group_label(by: str, value: Any) -> str:
    if by == "hour":
        return f"{int(value):02d}:00"
    return format_value(by, value) or str(value)


def _runs_table(table: RunTable, columns: Sequence[str], n: int) -> _Table:
    rows = [[_cell(name, row.values.get(name)) for name in columns] for row in table.rows]
    return _Table([_header(name) for name in columns], rows, max_rows=n, title=f"The runs, {_order_words(table)}")


def _fields_table(table: RunTable) -> _Table:
    everything = table.unfiltered or table
    infos = [info for info in everything.fields() if info.runs or info.name in DEFAULT_COLUMNS]
    return _Table(["Field", "In runs", "What it is"],
                  [[info.name, _count(info.runs), info.about] for info in infos],
                  title="Fields to query, sort or group by", collapsed=True, max_rows=0)


def _runs_next(table: RunTable, call: Callable[..., str], *, since_arg: dict[str, Any],
                group_by: str | None) -> _Next:
    steps: list[tuple[str, str]] = []
    first = table.rows[0].run if table.rows else None
    if first is not None and first.request_id:
        steps.append((call("logs", request_id=first.request_id, **since_arg), "every line of the first run here"))
    query = {"where": table.query} if table.query else {}
    if group_by is None:
        steps.append((call("runs", **since_arg, **query, group_by="hour"), "the same runs summed up by hour"))
    if not table.sort:
        steps.append((call("runs", **since_arg, **query, sort="-duration"), "the longest first"))
    steps.append((call("explore", tab="logs"), "click through them, each one's lines a click away"))
    return _Next(steps[:3])


@dataclass
class _RunsLayout:
    """
    How runs() lays the runs out: the columns of the table (DEFAULT_COLUMNS when None), the field to sum them up
    by, and how many rows to show.
    """

    columns: Sequence[str] | None = None
    group_by: str | None = None
    n: int = 50


def _runs_blocks(table: RunTable, *, call: Callable[..., str], since_arg: dict[str, Any],
                 layout: _RunsLayout) -> list[Any]:
    """
    runs()'s report, as blocks: the cards, what the runs call for, the runs summed up by group_by, the runs
    themselves in the columns asked for, and the fields there are to query, folded.
    """
    columns, group_by, n = layout.columns, layout.group_by, layout.n
    everything = table.unfiltered or table
    fn = table.function
    covered = (f"{_fmt_dt(everything.covered_from, zone=True)} (where reading stopped)" if everything.truncated
               else _fmt_dt(table.since, zone=True))
    sub = f"{_plural(everything.read, 'run')} read, from {covered} to {_fmt_dt(table.until)}"
    blocks: list[Any] = [_Title(f"Runs of {fn.name}" + (f" where {table.query}" if table.query else ""), sub)]
    if not everything.rows:
        if not everything.errors:
            blocks.append(_Note(f"No runs from {_fmt_dt(table.since, zone=True)}: it wasn't called, or its logs go "
                                f"somewhere else. {call('function_info')} shows when it was last called."))
        return blocks
    shown = list(columns or DEFAULT_COLUMNS)
    findings = runs_findings(table, ref=fn.name, named=[*shown, *([group_by] if group_by else [])])
    blocks.append(_run_cards(table, findings))
    blocks.append(_Findings(findings, empty=(
        "None of these runs reached the timeout or came close to running out of memory.")))
    if not table.rows:
        blocks.append(_Note(f"No run of the {_count(everything.read)} read matches {table.query!r}. The fields at the "
                            "end say what each run has."))
    else:
        if group_by:
            blocks.append(_group_table(table, group_by))
        blocks.append(_runs_table(table, shown, n))
    blocks.append(_fields_table(table))
    blocks.append(_runs_next(table, call, since_arg=since_arg, group_by=group_by))
    return blocks
