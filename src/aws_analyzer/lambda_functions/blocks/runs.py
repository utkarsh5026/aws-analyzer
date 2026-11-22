"""
runs()'s report: a function's runs, answer first (how many failed and why, in a sentence), a dot per run by when it
started and how long it took, a card per cause of the failed runs, then the runs as a table you choose the columns
of, filtered by a query, sorted, and summed up per hour, error or any field, with the fields there are to query folded
at the end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Title, _Tone
from ..._kit.charts import _Dots, _Point
from ..._kit.fmt import _count, _fmt_dt, _plural, _share, _tz_label

from ..helpers import human_ms
from ..models import ErrorGroup
from ..analysis.causes import STOPPED, CauseState, cause_advice, cause_state, runs_verdict
from ..analysis.logs import LogRun
from ..analysis.performance import percentile
from ..analysis.runs import DEFAULT_COLUMNS, RunGroup, RunTable, failure_groups, format_value, runs_findings
from .causes import _CaseFacts, _Causes, _Verdict, _cause

_STATUS_TONES = {"failed": "bad", "timeout": "bad", "logged": "warn"}

_HEADERS = {
    "status": "Status", "failed": "Failed", "day": "Day", "hour": "Hour", "duration": "Run time", "billed": "Billed",
    "memory": "Memory used", "memory_size": "Memory", "memory_share": "Memory share", "timeout_share": "Of timeout",
    "cold": "Cold start", "init": "Start-up", "version": "Version", "error_type": "Error type", "error": "Error",
    "summary": "What it logged", "lines": "Lines", "request_id": "Request ID", "stream": "Log stream",
}

_ZOOM_BELOW = 0.4

_NEAR_TIMEOUT = 0.9

_ZOOM_ROOM = 1.1

_ROUND_STEPS = (1, 2, 5, 10)

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


def _run_cards(table: RunTable, findings: list[tuple[str, str]], *, failing: bool) -> _Cards:
    """
    The numbers that matter: runs, how many failed (red while a cause is still failing), run times, memory and cold
    starts.
    """
    everything = table.unfiltered or table
    runs = table.runs
    durations = [run.duration for run in runs if run.duration is not None]
    failed = sum(1 for run in runs if run.status in ("failed", "timeout"))
    warned = " ".join(message for level, message in findings if level == "warn")
    longest = max(durations, default=None)
    matched = f"{_count(len(runs))} of {_count(everything.read)}" if table.query else _count(len(runs))
    failed_text = f"{_count(failed)} · {failed / len(runs):.0%}" if failed and runs else _count(failed)
    cards: list[tuple[str, ...]] = [
        ("Runs" + (" that match" if table.query else ""), matched),
        ("Failed", failed_text, "bad" if failing else ""),
        ("Median run time", human_ms(percentile(durations, 50))),
        ("Longest", f"{human_ms(longest)} of {table.function.timeout} s" if longest is not None else "-",
         "warn" if "timeout" in warned else ""),
    ]
    used = max((run.report.max_memory or 0 for run in runs if run.report is not None), default=0)
    if used and table.function.memory:
        cards.append(("Memory used", f"{used:,} of {table.function.memory:,} MB",
                      "warn" if "out of memory" in warned else ""))
    cards.append(("Cold starts", _count(sum(1 for run in runs if run.cold))))
    return _Cards(cards)


def _round_up(value: float) -> float:
    """
    The next round amount at or above `value`: 1, 2 or 5 times a power of ten (8,700 -> 10,000).
    """
    power = 10 ** math.floor(math.log10(value))
    return next(step * power for step in _ROUND_STEPS if step * power >= value)


def _dot_tone(run: LogRun) -> str:
    if run.status in ("failed", "timeout"):
        return "bad"
    return "note" if run.cold else ""


def _dot_label(run: LogRun) -> str:
    """
    What pointing at a run's dot says: when, how long, how it ended and what failed.
    """
    parts = [_fmt_dt(run.start, seconds=True), human_ms(run.duration), run.status]
    if run.cold:
        parts.append("cold start")
    if run.error is not None:
        parts.append(run.error[1].splitlines()[0][:80])
    return " · ".join(parts)


def _zoom_for(durations: list[float], timeout_ms: float) -> float | None:
    """
    How far up to draw the scale when every run not at the timeout took a small share of it, so their run times
    stay apart; None to draw it to the timeout.
    """
    under = [duration for duration in durations if duration < timeout_ms * _NEAR_TIMEOUT]
    if not under or max(under) <= 0 or max(under) >= timeout_ms * _ZOOM_BELOW:
        return None
    return _round_up(max(under) * _ZOOM_ROOM)


def _timeline(table: RunTable) -> _Dots | None:
    """
    Every run as a dot by when it started and how long it took, failed ones red and cold starts marked, under the
    timeout; zoomed in when the runs take seconds of a long timeout, the runs at the timeout on a strip above.
    """
    timed = sorted((run for run in table.runs if run.duration is not None), key=lambda run: run.start)
    if len(timed) < 2:
        return None
    timeout_ms = table.function.timeout * 1000.0
    points = [_Point(run.start.timestamp(), run.duration or 0.0, _dot_tone(run), _dot_label(run)) for run in timed]
    zoom = _zoom_for([point.value for point in points], timeout_ms)
    limit_text = f"at the {table.function.timeout} s timeout" if zoom else f"timeout {table.function.timeout} s"
    return _Dots(points, max(timeout_ms, max(point.value for point in points)), human_ms(timeout_ms),
                 _fmt_dt(timed[0].start), _fmt_dt(timed[-1].start), limit=timeout_ms, limit_text=limit_text,
                 tones={"bad": "failed or timed out", "note": "worked after a cold start"}, noun="run",
                 title=f"Every run, by when it started and how long it took ({_tz_label()})",
                 zoom=zoom, zoom_text=human_ms(zoom) if zoom else "")


def _cause_cards(table: RunTable, facts: list[_CaseFacts], run_call: Callable[[str], str]) -> _Causes:
    """
    A card per cause of the failed runs, still failing first: what it means, where it fails and what to do.
    """
    failed = sum(fact.group.count for fact in facts)
    causes = [replace(_cause(number, fact, until=table.until, lines=failed, run_call=run_call), share_of="failed runs")
              for number, fact in enumerate(facts, start=1)]
    return _Causes(causes, "Why runs failed: a card per cause")


def _failure_facts(table: RunTable) -> list[_CaseFacts]:
    """
    The failed runs' causes with their state and advice, the ones still failing first, then by how many runs.
    """
    groups: list[ErrorGroup] = failure_groups(table)
    facts = [_CaseFacts(group, cause_state(group, since=table.since, until=table.until),
                        cause_advice(group, table.function)) for group in groups]
    return sorted(facts, key=lambda fact: fact.state.state == STOPPED)


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


def _newest_failed(table: RunTable) -> LogRun | None:
    failed = [run for run in table.runs if run.status in ("failed", "timeout") and run.request_id]
    return max(failed, key=lambda run: run.start, default=None)


def _runs_next(table: RunTable, call: Callable[..., str], *, since_arg: dict[str, Any],
               group_by: str | None) -> _Next:
    steps: list[tuple[str, str]] = []
    failed = _newest_failed(table)
    first = table.rows[0].run if table.rows else None
    if failed is not None:
        steps.append((call("logs", request_id=failed.request_id, **since_arg), "the newest failed run, line by line"))
    elif first is not None and first.request_id:
        steps.append((call("logs", request_id=first.request_id, **since_arg), "every line of the first run here"))
    query = {"where": table.query} if table.query else {}
    if group_by is None and failed is not None and not table.query:
        steps.append((call("runs", **since_arg, where="failed", group_by="error_type"),
                      "the failed runs counted by error"))
    elif group_by is None:
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
    runs()'s report, as blocks: the verdict, the cards, what the runs call for, a dot per run, a card per cause of the
    failed ones, the runs summed up by group_by, the runs themselves in the columns asked for, and the fields there
    are to query, folded.
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
    facts = _failure_facts(table)
    states: list[CauseState] = [fact.state for fact in facts]
    if table.rows:
        verdict = runs_verdict(table.runs, fn, [(fact.group, fact.state) for fact in facts], until=table.until,
                               matching=bool(table.query))
        blocks.append(_Verdict(verdict.headline, verdict.detail, verdict.tone))
    blocks.append(_run_cards(table, findings, failing=any(state.state != STOPPED for state in states)))
    blocks.append(_Findings(findings, empty=(
        "None of these runs reached the timeout or came close to running out of memory.")))
    if not table.rows:
        blocks.append(_Note(f"No run of the {_count(everything.read)} read matches {table.query!r}. The fields at the "
                            "end say what each run has."))
    else:
        timeline = _timeline(table)
        if timeline is not None:
            blocks.append(timeline)
        if facts:
            blocks.append(_cause_cards(table, facts, lambda request_id: call("logs", request_id=request_id,
                                                                             **since_arg)))
        if group_by:
            blocks.append(_group_table(table, group_by))
        blocks.append(_runs_table(table, shown, n))
    blocks.append(_fields_table(table))
    blocks.append(_runs_next(table, call, since_arg=since_arg, group_by=group_by))
    return blocks
