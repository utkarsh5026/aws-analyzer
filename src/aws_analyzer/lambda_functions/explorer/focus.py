"""
What LambdaExplorer is showing, as a Focus (_kit/focus.py): the window's `focus` property.
"""

from __future__ import annotations

from datetime import datetime
from ..._kit.fmt import _plural
from ..._kit.focus import Focus, FocusPart
from ..._kit.text import _clip

from ..analysis.logs import LogRun
from ..models import CodeFrame, ErrorGroup, ErrorReport, Function
from .frame import _EXPLORER_TABS
from .functions import _FUNCTION_CHIPS
from .logs import _RUN_CHIPS

_FOCUS_NAMES = 20
_FOCUS_CAUSES = 5
_CHIP_CHARS = 80
_SHORT_ID = 8


def _tab_part(key: str) -> FocusPart:
    title = next(title for tab, title, *_ in _EXPLORER_TABS if tab == key)
    return FocusPart("tab", key, f"{title} tab")


def _chip_part(key: str, chips: tuple[tuple[str, str, str], ...]) -> list[FocusPart]:
    """
    The part for a list's chip, read as its label ('failed' -> 'Failed'), or none when no chip is picked.
    """
    if not key:
        return []
    label = next((label for chip, label, _ in chips if chip == key), key)
    return [FocusPart("filter", key, label)]


def _range_part(start: datetime, end: datetime, text: str) -> FocusPart:
    return FocusPart("range", f"{start.isoformat()}/{end.isoformat()}", text)


def _search_part(query: str) -> list[FocusPart]:
    return [FocusPart("search", query, f"search: {_clip(query, _CHIP_CHARS)}")] if query else []


def _run_parts(runs: list[LogRun]) -> list[FocusPart]:
    """
    The parts for the runs opened in the Logs tab: one run is its request ID and the error it failed on (if it
    did); several are their request IDs, comma-separated; none is no part. Lines outside any run don't count.
    """
    opened = [run for run in runs if run.request_id]
    if not opened:
        return []
    if len(opened) > 1:
        ids = ",".join(run.request_id or "" for run in opened)
        return [FocusPart("runs", ids, f"{len(opened)} runs open")]
    run = opened[0]
    request_id = run.request_id or ""
    parts = [FocusPart("request_id", request_id, f"run {request_id[:_SHORT_ID]}")]
    if run.error is not None:
        summary = run.error[1]
        parts.append(FocusPart("error", summary, _clip(summary, _CHIP_CHARS)))
    return parts


def _cause_text(group: ErrorGroup) -> str:
    """
    A cause as its line reads: "KeyError: 'x' (×8)", its kind written once when its message starts with it.
    """
    message = group.message if group.message.startswith(group.kind) else f"{group.kind}: {group.message}"
    return f"{message} (×{group.count})"


def _cause_parts(report: ErrorReport | None) -> list[FocusPart]:
    """
    The Errors tab's causes as one part: the most frequent few as "KeyError: 'x' (×8); ...", read as '3 causes'.
    """
    if report is None or not report.groups:
        return []
    listed = "; ".join(_cause_text(group) for group in report.groups[:_FOCUS_CAUSES])
    return [FocusPart("causes", listed, _plural(len(report.groups), "cause"))]


def _code_parts(file: str | None, target: CodeFrame | None, *, how: bool) -> list[FocusPart]:
    """
    The Code tab's parts: How it works, or the file shown and, when it was opened at a line (a traceback's), the
    line.
    """
    if how:
        return [FocusPart("view", "how_it_works", "How it works")]
    if not file:
        return []
    parts = [FocusPart("file", file, file)]
    if target is not None and target.path == file:
        parts.append(FocusPart("line", str(target.line), f"line {target.line}"))
    return parts


class _FocusReads:
    """
    LambdaExplorer's `focus`: what the window is showing, as data.
    """

    @property
    def focus(self) -> Focus | None:
        """
        What the window is showing, as a Focus (aws_analyzer._kit.focus): its kind is 'functions' (the list, with the
        functions the search and chip let through), 'live' (the Live tab, and the function it follows), 'function'
        (a function's Overview or Settings tab), 'logs' (its Logs tab, with the range and search), 'run' (one run
        opened there), 'errors' (with its causes), 'performance' or 'code' (with the file shown). x.focus.chips()
        reads it as chips; None when there's no window (text mode).
        """
        if self._w is None:
            return None
        if self._tab == "live":
            return self._live_focus()
        if self.function is None or self._tab == "functions":
            return self._list_focus()
        return self._function_focus(self.function)

    def _region_part(self) -> FocusPart:
        region = self.region or self.core.region or ""
        return FocusPart("region", region, "all regions" if region == "all" else region)

    def _one_region(self) -> str:
        """
        The region the list or the Live tab reads, or '' when it reads every region.
        """
        region = self._region_part().value
        return "" if region == "all" else region

    def _list_focus(self) -> Focus:
        region = self._region_part()
        names = [facts.fn.name for facts in self._visible]
        parts = [region, *_search_part(self._query), *_chip_part(self._chip, _FUNCTION_CHIPS)]
        if names:
            parts.append(FocusPart("functions", ", ".join(names[:_FOCUS_NAMES]), _plural(len(names), "function")))
        return Focus("lambda", "functions", region=self._one_region(), parts=tuple(parts))

    def _live_focus(self) -> Focus:
        followed = self._followed()
        parts = [self._region_part(), _tab_part("live")]
        if followed is not None:
            fn = followed.function
            parts.insert(0, FocusPart("function", fn.arn or fn.name, fn.name))
        return Focus("lambda", "live", region=self._one_region(), parts=tuple(parts))

    def _function_focus(self, fn: Function) -> Focus:
        """
        A function's tab: the function, its region and the tab, then what that tab adds.
        """
        start: datetime | None = None
        end: datetime | None = None
        kind, extra = "function", []
        if self._tab == "logs":
            start, end = self._range_window()
            extra = [_range_part(start, end, self._range_text()), *self._logs_parts()]
            kind = "run" if any(part.key == "request_id" for part in extra) else "logs"
        elif self._tab in ("errors", "performance"):
            start, end = self._tab_window()
            extra = [_range_part(start, end, self._tab_range_text())]
            if self._tab == "errors":
                extra += _cause_parts(self.error_report)
            kind = self._tab
        elif self._tab == "code":
            kind, extra = "code", _code_parts(self._code_file, self._code_target, how=self._how)
        base = [FocusPart("function", fn.arn or fn.name, fn.name), FocusPart("region", fn.region, fn.region),
                _tab_part(self._tab)]
        return Focus("lambda", kind, region=fn.region, since=start, until=end, parts=(*base, *extra))

    def _logs_parts(self) -> list[FocusPart]:
        """
        The Logs tab's search (unless it only names the run opened), chip and opened runs.
        """
        runs = self._found_runs if self.found is not None else self._runs
        opened = _run_parts([run for run in runs if run.key in self._open_runs])
        opened_id = next((part.value for part in opened if part.key == "request_id"), "")
        query = self._log_query if self._log_query != opened_id else ""
        return [*_search_part(query), *_chip_part(self._run_chip, _RUN_CHIPS), *opened]
