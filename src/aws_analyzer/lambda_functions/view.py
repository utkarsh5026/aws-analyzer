"""
LambdaView: the notebook UI, which renders what LambdaAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import difflib
import importlib.util
import math
import os
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generator

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Tabs, _Text, _Title, _Tone, _Verdict
from .._kit.deps import _in_notebook, _require
from .._kit.errors import _Hint, _error_code, _why
from .._kit.fmt import (
    _as_count, _as_int, _count, _fmt_dt, _plural, _share, _total, _utcnow, human_age, human_money,
    human_size,
)
from .._kit.charts import _Part, _Split
from .._kit.jsontree import _JsonTree
from .._kit.render import _call
from .._kit.text import _clip
from .._kit.themes import _theme_name
from .._kit.view import _BaseView, _friendly_errors

from .helpers import (
    MAX_TIMEOUT, MEMORY_PER_VCPU, UNZIPPED_LIMIT, _in_days, _mb, _ms, _pct, human_ms, parse_function_ref,
    runtime_family,
)
from .models import (
    CodePackage, ErrorReport, Function, FunctionDetail, LogEvent, LogGroup, PackageDownload, Performance,
    RuntimeStatus,
    SavedPage,
)
from .analysis.text import _reason, _window
from .analysis.functions import runtime_status
from .analysis.cost import cost_workings, function_monthly_cost, provisioned_monthly_cost
from .analysis.findings import _errors_level, _times, account_findings, function_findings
from .analysis.errors import (
    _LIFECYCLE_RE, _filter_pattern, _phrase, classify_error, error_findings, window_findings,
)
from .analysis.fates import fate_findings
from .analysis.fleet import (
    FleetIssue, FleetRow, bill_note, bill_parts, first_issues, fleet_issues, fleet_rows, fleet_verdict,
)
from .analysis.word_search import WordSearch, word_search
from .analysis.package import (
    _HANDLER_EXTENSIONS, _JUNK_RE, _SOURCE_LIMIT, _local_path, _top_folder, package_file_name, package_findings,
)
from .analysis.codemap.terms import _LAMBDA_ENV
from .analysis.codemap.aws_model import _event_kind
from .analysis.codemap.model import CodeMap
from .analysis.codemap.findings import _code_summary, _short_name, code_map_findings
from .analysis.codemap.graph import CodeGraph, code_graph
from .analyzer import LambdaAnalyzer
from .style import _render_html, _render_page
from .blocks.walk import _Walk
from .blocks.info import cost_split, cost_workings_table, day_charts, wiring_block
from .blocks.graph import _Graph
from .blocks.page import _PageLink
from .blocks.diff import _diff_blocks
from .blocks.runs import _RunsLayout, _runs_blocks
from .blocks.performance import _PerformanceSetup, performance_blocks
from .blocks.causes import _cause_blocks
from .blocks.fleet import _FunctionList, _Todo
from .blocks.fates import _fates_blocks, lost_events
from .blocks.logs import _LogRuns, _marked_words, _opened_runs, _runs_holding
from .blocks.invoke import invoke_blocks

if TYPE_CHECKING:
    from .explorer.window import LambdaExplorer
    from .explorer.tester import LambdaTester


def _stamp(moment: datetime | None, *, zone: bool = False) -> str:
    """
    A log line's time, to the second, in the zone reports show times in (UTC unless set_timezone() chose another);
    zone=True adds the zone's name.
    """
    return _fmt_dt(moment, seconds=True, zone=zone)


_SMALLEST_BILL = 0.01

_SECTIONS = {  # what a section of a report is, and the permission that reads it
    "list": ("functions", "lambda:ListFunctions"),
    "policy": ("resource policy", "lambda:GetPolicy"),
    "triggers": ("event source mappings", "lambda:ListEventSourceMappings"),
    "url": ("function URL", "lambda:GetFunctionUrlConfig"),
    "async": ("settings for failed asynchronous calls", "lambda:GetFunctionEventInvokeConfig"),
    "versions": ("versions", "lambda:ListVersionsByFunction"),
    "aliases": ("aliases", "lambda:ListAliases"),
    "provisioned": ("provisioned concurrency", "lambda:ListProvisionedConcurrencyConfigs"),
    "runtime_updates": ("runtime update setting", "lambda:GetRuntimeManagementConfig"),
    "account": ("limits", "lambda:GetAccountSettings"),
    "log_group": ("log group", "logs:DescribeLogGroups"),
    "logs": ("log groups", "logs:DescribeLogGroups"),
    "metrics": ("numbers from CloudWatch", "cloudwatch:GetMetricData"),
}

_PARTS = {  # function_monthly_cost's parts, in plain English
    "requests": "Requests",
    "compute": "Compute (memory x run time)",
    "storage": "/tmp above 512 MB",
    "logs": "Logs sent to CloudWatch",
    "log_storage": "Logs kept in CloudWatch",
    "provisioned": "Provisioned concurrency",
}

_VIA = {
    "event source mapping": "Lambda reads it",
    "resource policy": "allowed to invoke it",
    "function URL": "its HTTPS address",
}

_INFO_ORDER = ("head", "charts", "wiring", "cost", "runs", "access", "triggers", "async", "versions", "days", "tags",
               "raw", "next")  # function_info()'s parts, the answer and its pictures first, then the detail


def _unread(errors: dict[str, str], owner: str) -> _Note | None:
    """
    One note naming the sections that couldn't be read ('its versions', "us-east-1's limits"), each with the
    permission it needs.
    """
    parts = []
    for section, code in errors.items():
        what, permission = _SECTIONS.get(section, (section, "the permission"))
        parts.append(f"{owner}{what} ({_why(code, permission)})")
    return _Note(f"Couldn't read {'; '.join(parts)}.", "warn") if parts else None


def _runtime_meaning(status: RuntimeStatus) -> str:
    if status.state == "image":
        return "A container image: what's inside is yours to patch, so rebuild it on a current base image now and then"
    newest = f" The newest is {status.upgrade}." if status.upgrade else ""
    if status.state == "unknown":
        return "This file doesn't know its support dates (RUNTIMES): the AWS Lambda runtimes page has them." + newest
    blocked = status.block_update is not None and status.block_update <= _utcnow().date()
    updates = (f" Updates {'blocked since' if blocked else 'blocked from'} {status.block_update}."
               if status.block_update else "")
    if status.state in ("deprecated", "blocked"):
        return f"Support ended on {status.deprecated}: no more security patches.{updates}{newest}"
    if status.state == "ending":
        return f"Supported until {status.deprecated} ({_in_days(status.days_left or 0)}).{updates}{newest}"
    return (f"Supported until {status.deprecated}." if status.deprecated else "Supported.") + newest


def _handler_meaning(fn: Function) -> str:
    if fn.package_type == "Image":
        return "The image's own entry point runs it"
    if not fn.handler:
        return ""
    module, _, function = fn.handler.rpartition(".")
    family = runtime_family(fn.runtime)
    if family == "python" and module:
        return f"Lambda calls {function}() in {module.replace('.', '/')}.py"
    if family == "nodejs" and module:
        return f"Lambda calls the {function} export of {module}.js (or .mjs)"
    if family == "ruby" and module:
        return f"Lambda calls {function} in {module}.rb"
    if family in ("java", "dotnet"):
        return "The class and method Lambda calls"
    return "What Lambda starts"


def _cpu(fn: Function) -> str:
    """
    The CPU a function's memory buys: 'about 0.58 vCPU', 'about 2.0 vCPUs' (6 at most).
    """
    return f"about {fn.vcpus:.2f} vCPU" if fn.vcpus < 1 else f"about {min(fn.vcpus, 6):.1f} vCPUs"


def _log_meaning(fn: Function, group: LogGroup | None, unread: bool) -> str:
    kept = ""
    if group is not None:
        kept = "kept forever" if group.retention_days is None else f"kept {_plural(group.retention_days, 'day')}"
        kept += f", {human_size(group.stored_bytes)} stored" if group.stored_bytes is not None else ""
    elif not unread:
        kept = "no log group yet: it hasn't logged anything"
    return ", ".join(filter(None, [f"{fn.log_format or 'Text'} lines", kept]))


def _concurrency_cell(fn: Function) -> tuple[Any, str]:
    if fn.reserved_concurrency is None:
        return "shared", "No reservation: it shares the region's concurrent runs with every other function"
    if fn.reserved_concurrency == 0:
        return _Tone("0: switched off", "bad"), "Every call is throttled"
    return (f"{fn.reserved_concurrency:,} reserved",
            f"At most {fn.reserved_concurrency:,} run at once (more are throttled), and that many are kept for it")


def _lifecycle(message: str) -> bool:
    """
    START / END / INIT_START lines, and their JSON platform records: there's one of each per run.
    """
    text = message.lstrip()
    if _LIFECYCLE_RE.match(text) or text.startswith("INIT_START"):
        return True
    return text.startswith('{"time"') and any(
        f'"type":"platform.{kind}"' in text.replace(" ", "") for kind in ("start", "runtimeDone", "initStart"))


def _lines_found(events: list[LogEvent], phrase: str | None, words: WordSearch | None) -> list[LogEvent]:
    """
    The lines logs(search=...) keeps: each line that holds the words joined with AND, OR and NOT, else the plain
    search's phrase, checked again here for log stores that only roughly apply filter patterns.
    """
    if words is not None:
        return [event for event in events if words.matches([event.message])]
    return [event for event in events if phrase in event.message] if phrase else events


_SEARCH_RUN_LINES = 3_000


def _can_search_here(draws_html: bool) -> bool:
    """
    Whether logs() can put a search box over its report: in Jupyter, with ipywidgets installed.
    """
    return draws_html and _in_notebook() and importlib.util.find_spec("ipywidgets") is not None


def _search_said(search: str, phrase: str | None, words: WordSearch | None) -> str:
    """
    What logs(search=...) looked for, for the report's subtitle.
    """
    if words is not None:
        return f"lines matching {words.written()!r}"
    return f"lines containing {phrase!r}" if phrase else f"filter pattern {_filter_pattern(search)}"


def _since_arg(since: Any, default: str) -> dict[str, Any]:
    """
    since= for a next step: carried over when the user chose one (as text), left out when it's the default.
    """
    return {"since": since} if isinstance(since, str) and since != default else {}


class LambdaView(_BaseView):
    """
    Notebook UI over LambdaAnalyzer. Each method renders a report and returns nothing; for the underlying data
    call the matching method on `view.core` (a LambdaAnalyzer): functions() -> overview(), function_info() ->
    describe(), logs() -> log_events(), and errors(), performance() and code() by the same name.

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🧭 Explore by clicking": ("explore",),
        "λ Functions": ("functions", "function_info"),
        "🩺 When something goes wrong": ("errors", "logs", "runs", "performance"),
        "▶ Run it": ("test", "invoke"),
        "📦 Code": ("code", "diff", "how_it_works", "how_it_works_page", "download"),
        "❓ Help": ("help", "next"),
    }
    _CHANGES = frozenset({"download", "how_it_works_page", "invoke", "test"})
    _START = (
        ("explore()", "the explorer window: every function, its logs run by run, errors and code, by clicking"),
        ("functions()", "every function: runtime, triggers, calls, errors, cost and warnings"),
        ("functions(regions='all')", "the same in every region your account has turned on"),
        ("function_info('name')", "one function in plain English, and its last 30 days"),
    )

    def __init__(
        self,
        core: LambdaAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: LambdaAnalyzer = core or LambdaAnalyzer()
        self.explorer: LambdaExplorer | None = None  # the window explore() opened last
        self.tester: LambdaTester | None = None

    # ------------------------------------------------------------------ plumbing

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        lowered = message.lower()
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code == "ResourceNotFoundException":
            return (f"{text} Names are case-sensitive, and functions are regional (this is {self.core.region}): "
                    "functions() lists them, and functions(regions='all') looks in every region.")
        denied = code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in lowered
        if denied and "invokefunction" in lowered:
            return (f"{text} invoke() needs lambda:InvokeFunction on the function, on top of the read-only ones "
                    "README lists for the other commands.")
        if denied:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    def _missing(self, ref: str, region: str | None) -> _Hint:
        """
        What to say when a function isn't there: the closest names, and where else functions() saw it.
        """
        name, qualifier, where = self.core.locate(ref, region)
        try:
            names = [f.name for f in self.core.list_functions(where)]
        except (ClientError, BotoCoreError):
            names = []
        if qualifier and name in names:
            return _Hint(f"{name} has no alias or version {qualifier!r}. function_info({name!r}) lists its aliases "
                         "and versions.")
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        others = sorted(self.core._seen.get(name, set()) - {where})
        elsewhere = f" functions() found it in {', '.join(others)}: pass region='{others[0]}'." if others else ""
        return _Hint(
            f"No function {name!r} in {where}.{hint}{elsewhere} Names are case-sensitive: functions() lists the ones "
            f"in {where}, and functions(regions='all') looks in every region."
        )

    @contextmanager
    def _named(self, ref: str, region: str | None) -> Generator[None, None, None]:
        """
        Turns AWS's bare 'function not found' into a hint with the closest names.
        """
        try:
            yield
        except ClientError as exc:
            if _error_code(exc) != "ResourceNotFoundException":
                raise
            raise self._missing(ref, region) from None

    def _call_for(self, command: str, fn: Function, *args: Any, **kwargs: Any) -> str:
        """
        A next step about a function, with region= when it isn't in the view's region.
        """
        if fn.region and fn.region != self.core.region:
            kwargs = {**kwargs, "region": fn.region}
        return _call(command, fn.name, *args, **kwargs)

    # ------------------------------------------------------------------ functions

    @_friendly_errors
    def functions(
        self,
        match: str | None = None,
        *,
        regions: Any = None,
        days: int = 30,
        metrics: bool = True,
        details: bool = True,
    ) -> None:
        """
        Every Lambda function in the region, the ones that need you first: a sentence saying what's wrong, where the
        monthly bill goes, the few problems to fix first (each with the command), then each function under Failing,
        Paying for nothing, Needs a fix, Not called or Healthy, with its calls a day, failed calls, longest run
        against its timeout and cost; click a function for its findings. regions='all' covers every region your
        account has turned on (or pass a list); match='etl-*' keeps matching names; metrics=False skips CloudWatch
        and details=False the reads made per function (resource policy, provisioned concurrency), for speed.
        """
        with self._progress("Reading functions", unit="functions") as tick:
            ov = self.core.overview(match=match, regions=regions, days=days, metrics=metrics, details=details,
                                    progress=tick)
        now = _utcnow()
        multi = len(ov.regions) > 1
        rows = fleet_rows(ov, prices=self.core.prices, now=now)
        account = [(region, level, message) for region, limits in sorted(ov.accounts.items())
                   for level, message in account_findings(limits, ov.days)]
        issues = fleet_issues(rows, with_region=multi, account=account)
        blocks: list[Any] = [_Title(self._functions_title(ov, multi), self._functions_subtitle(ov, match, metrics))]
        if rows:
            verdict = fleet_verdict(rows, issues)
            blocks += [_Verdict(verdict.headline, verdict.detail, verdict.tone),
                       _Cards(self._functions_cards(ov, rows, multi=multi, metrics=metrics, issues=issues))]
        blocks += self._functions_notes(ov, match=match, multi=multi, details=details)
        if not rows:
            self._show(blocks)
            return
        blocks += self._bill_blocks(rows, multi=multi)
        first, rest = first_issues(issues)
        blocks.append(_Todo(first, rest))
        total = sum(row.total_cost or 0.0 for row in rows)
        next_calls = {row.function.arn: self._row_calls(row) for row in rows}
        blocks.append(_FunctionList(rows, now, with_region=multi, with_metrics=metrics, total_cost=total,
                                    next_calls=next_calls))
        if multi:
            blocks.append(self._regions_table(ov, rows, metrics=metrics))
        if ov.metrics_read:
            cost = ov.metrics_read / 1000 * self.core.prices["metric_request"]
            blocks.append(_Note(
                f"Cost estimates scale CloudWatch's last {ov.days} days to a month, and include provisioned concurrency "
                f"and the logs kept. This report read {ov.metrics_read:,} CloudWatch metrics (about "
                f"{human_money(cost)})."
            ))
        blocks.append(_Next(self._functions_next(rows, multi=multi, regions=regions)))
        self._show(blocks)

    @staticmethod
    def _functions_title(ov: Any, multi: bool) -> str:
        if not multi:
            return f"Lambda functions in {ov.regions[0]}"
        return f"Lambda functions in {len(ov.regions)} regions"

    def _functions_subtitle(self, ov: Any, match: str | None, metrics: bool) -> str:
        parts = [f"names matching {match!r}" if match else ""]
        if metrics:
            parts.append(f"calls, errors and run time from CloudWatch over the last {ov.days} days")
        parts.append(f"cost estimated at {self._price_basis()}, before the free tier")
        return " · ".join(filter(None, parts))

    @staticmethod
    def _functions_cards(ov: Any, rows: list[FleetRow], *, multi: bool, metrics: bool,
                         issues: list[FleetIssue]) -> list[tuple[str, ...]]:
        """
        functions()' cards: how many functions, how many are failing (only CloudWatch can tell) and need a fix, the
        calls and failed share, the monthly cost, and the region's code storage.
        """
        failing = sum(row.group == "failing" for row in rows)
        needing = sum(row.group in ("waste", "risk") for row in rows)
        cards: list[tuple[str, ...]] = [("Functions", f"{len(rows):,}")]
        if multi:
            with_functions = len({row.function.region for row in rows})
            cards.append(("Regions with functions", f"{with_functions:,} of {len(ov.regions):,}"))
        if metrics:
            cards.append(("Failing", f"{failing:,}", "bad" if failing else ""))
        cards.append(("Need a fix", f"{needing:,}", "warn" if needing else ""))
        if metrics:
            calls = sum(row.metrics.invocations for row in rows if row.metrics)
            failed = sum(row.metrics.errors for row in rows if row.metrics)
            errors_warned = any(issue.kind.key == "errors" and issue.level == "warn" for issue in issues)
            cards.append((f"Calls ({ov.days}d)", _count(round(calls))))
            cards.append(("Failed calls", _pct(failed / calls) if calls else "-", "warn" if errors_warned else ""))
        costs = [row.total_cost for row in rows if row.total_cost is not None]
        cards.append(("Est. cost / month", human_money(sum(costs)) if costs else "-"))
        if not multi and ov.regions[0] in ov.accounts:
            limits = ov.accounts[ov.regions[0]]
            if limits.code_storage is not None and limits.code_storage_limit:
                full = limits.code_storage >= 0.8 * limits.code_storage_limit
                cards.append(("Code storage", f"{human_size(limits.code_storage)} of "
                              f"{human_size(limits.code_storage_limit)}", "warn" if full else ""))
        return cards

    @staticmethod
    def _functions_notes(ov: Any, *, match: str | None, multi: bool, details: bool) -> list[Any]:
        """
        What functions() couldn't read, and what it says when there's no function.
        """
        notes: list[Any] = []
        listed = {key.split(":")[0] for key in ov.errors if key.endswith(":list")}
        for region in sorted(listed):
            code = ov.errors[f"{region}:list"]
            notes.append(_Note(f"Couldn't list the functions in {region} ({_why(code, 'lambda:ListFunctions')}).",
                               "warn"))
        by_region: dict[str, dict[str, str]] = defaultdict(dict)
        for key, code in ov.errors.items():
            region, _, section = key.partition(":")
            if section != "list":
                by_region[region][section] = code
        for region, errors in sorted(by_region.items()):
            note = _unread(errors, f"{region}'s ")
            if note:
                notes.append(note)
        for section in ("policy", "provisioned"):
            codes = Counter(fn.errors[section] for fn in ov.functions if section in fn.errors)
            if codes:
                code, count = codes.most_common(1)[0]
                what, permission = _SECTIONS[section]
                notes.append(_Note(
                    f"Couldn't read the {what} of {_plural(count, 'function')} ({_why(code, permission)}), so "
                    + ("triggers from other services and public access aren't shown for them."
                       if section == "policy" else "the cost of provisioned concurrency is missing for them."),
                    "warn"))
        if ov.skipped:
            notes.append(_Note(
                f"Left out {_plural(len(ov.skipped), 'region')} not turned on for this account: "
                f"{', '.join(sorted(ov.skipped))}."
            ))
        if not ov.functions and not listed:
            where = f"matching {match!r} " if match else ""
            place = ov.regions[0] if not multi else f"these {len(ov.regions)} regions"
            more = "" if multi else " Functions are regional: functions(regions='all') looks in every region."
            notes.append(_Note(f"No Lambda functions {where}in {place}.{more}"))
        if ov.functions and not details:
            notes.append(_Note(
                "details=False: resource policies and provisioned concurrency weren't read, so triggers from other "
                "services, public access and the cost of provisioned concurrency are missing."
            ))
        return notes

    @staticmethod
    def _bill_blocks(rows: list[FleetRow], *, multi: bool) -> list[Any]:
        """
        The monthly bill as one bar of its parts, with who pays the largest; nothing when it's all but free.
        """
        parts = bill_parts(rows, with_region=multi)
        total = sum(part.amount for part in parts)
        if total < _SMALLEST_BILL:
            return []
        split = _Split([_Part(part.label, part.amount, human_money(part.amount)) for part in parts],
                       human_money(total), title=f"Where the {human_money(total)} a month goes",
                       note=bill_note(parts))
        return [split]

    def _row_calls(self, row: FleetRow) -> list[str]:
        """
        The calls a function's line offers when it's opened.
        """
        fn, metrics = row.function, row.metrics
        calls = [self._call_for("function_info", fn)]
        if metrics is not None and metrics.errors:
            calls.append(self._call_for("errors", fn))
        if metrics is not None and metrics.invocations:
            calls.append(self._call_for("performance", fn))
        return calls

    @staticmethod
    def _regions_table(ov: Any, rows: list[FleetRow], *, metrics: bool) -> _Table:
        """
        Each region with functions: how many, their calls and cost, its code storage and the most run at once.
        """
        regional: dict[str, dict[str, float]] = defaultdict(lambda: {"functions": 0, "calls": 0.0, "cost": 0.0})
        for row in rows:
            numbers = regional[row.function.region]
            numbers["functions"] += 1
            numbers["calls"] += row.metrics.invocations if row.metrics else 0.0
            numbers["cost"] += row.total_cost or 0.0
        region_rows = []
        for region in sorted(regional):
            limits = ov.accounts.get(region)
            storage = "-"
            peak = "-"
            if limits is not None and limits.code_storage is not None:
                storage = human_size(limits.code_storage) + (
                    f" of {human_size(limits.code_storage_limit)}" if limits.code_storage_limit else "")
            if limits is not None and limits.peak_concurrency is not None:
                peak = f"{limits.peak_concurrency:,.0f}" + (f" of {limits.concurrency:,}" if limits.concurrency else "")
            numbers = regional[region]
            region_rows.append([region, f"{numbers['functions']:,.0f}",
                                _count(round(numbers["calls"])) if metrics else "-",
                                human_money(numbers["cost"]), storage, peak])
        return _Table(["Region", "Functions", f"Calls ({ov.days}d)", "Est. $/month", "Code storage", "Most at once"],
                      region_rows, title="By region", max_rows=0)

    def _functions_next(self, rows: list[FleetRow], *, multi: bool, regions: Any) -> list[tuple[str, str]]:
        """
        functions()' next steps: the function that needs you most, the errors of the worst failing one, and every
        region when only one was read.
        """
        first = rows[0].function
        steps = [(self._call_for("function_info", first), "everything about it, and its last 30 days")]
        failing = [row for row in rows if row.metrics is not None and row.metrics.errors]
        if failing:
            worst = max(failing, key=lambda row: (row.group == "failing", row.metrics.errors))
            steps.append((self._call_for("errors", worst.function), "its errors, grouped by cause"))
        if not multi and regions is None:
            steps.append(("functions(regions='all')", "every region your account has turned on"))
        return steps

    @_friendly_errors
    def function_info(self, name: str, *, region: str | None = None, days: int = 30) -> None:
        """
        One function in plain English: what it runs (the runtime and when it loses support, memory and the CPU
        it buys, timeout), what triggers it and who may call it, what happens to failed events, what it can reach
        (role, network, environment variable names: values stay hidden), versions, aliases and provisioned
        concurrency, its last 30 days day by day, the estimated monthly cost, and findings with the command for
        each. name can also be 'name:alias', an ARN or a console link.
        """
        with self._progress("Reading the function", unit="parts") as tick, self._named(name, region):
            detail = self.core.describe(name, region=region, days=days, progress=tick)
        sections = self._function_sections(detail, name=name, days=days)
        self._show([block for part in _INFO_ORDER for block in sections[part]])

    def _function_sections(self, detail: FunctionDetail, *, name: str | None = None, days: int = 30,
                           ) -> dict[str, list[Any]]:
        """
        function_info()'s blocks by part (_INFO_ORDER), so the explorer window can show its health and its setup
        on different tabs: 'head' (title, cards, findings), 'charts' (calls and run time a day), 'wiring' (what calls
        it and where its results go), 'cost', 'runs', 'access', 'triggers', 'async', 'versions', 'days', 'tags', 'raw'
        and 'next'.
        """
        fn, m = detail.function, detail.metrics
        now = _utcnow()
        prices = self.core.prices
        found = function_findings(fn, m, detail, prices=prices, now=now)
        status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
        cost = function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices)
        level = _errors_level(m, now)
        near_timeout = bool(m and m.duration_max and fn.timeout and m.duration_max >= 0.9 * fn.timeout * 1000)
        public = any(t.public for t in detail.triggers)
        _, qualifier, _ = parse_function_ref(name or fn.name)
        sections: dict[str, list[Any]] = {part: [] for part in _INFO_ORDER}
        cards: list[tuple[str, ...]] = [
            ("Runtime", status.label, status.tone),
            ("Memory", _mb(fn.memory)),
            ("Timeout", f"{fn.timeout} s", "warn" if near_timeout else ""),
            ("Architecture", fn.architecture),
        ]
        if m is not None:
            cards += [
                (f"Calls ({days}d)", _count(round(m.invocations))),
                ("Error rate", _pct(m.error_rate) if m.invocations else "-", "warn" if level == "warn" else ""),
                ("Avg / longest run",
                 f"{human_ms(m.avg_duration)} / {human_ms(m.duration_max)}" if m.invocations else "-",
                 "warn" if near_timeout else ""),
            ]
            if m.throttles:
                cards.append(("Throttled", _count(round(m.throttles))))
        cards.append(("Est. cost / month", human_money(_total(cost)) if cost else "-"))
        cards.append(("Triggers", f"{len(detail.triggers):,}", "warn" if public else ""))
        sub = " · ".join(filter(None, [
            fn.arn + (f":{qualifier}" if qualifier else ""),
            _clip(fn.description, 120),
            f"changed {human_age(fn.last_modified, now)}" if fn.last_modified else "",
        ]))
        head = sections["head"]
        head += [_Title(f"Function {fn.name}" + (f" ({qualifier})" if qualifier else ""), sub), _Cards(cards)]
        note = _unread(detail.errors, "its ")
        if note:
            head.append(note)
        head.append(_Findings(found, empty=f"No problems found in its settings or its last {days} days."))
        sections["charts"] += day_charts(detail, days=days, now=now)
        sections["wiring"].append(wiring_block(detail))

        package = (f"{fn.package_type}, {human_size(fn.code_size)}" if fn.package_type == "Zip"
                   else f"container image {fn.image_uri or ''}".strip())
        if fn.layers:
            package += f", plus {_plural(len(fn.layers), 'layer')} ({human_size(fn.layer_size)})"
        concurrency, concurrency_meaning = _concurrency_cell(fn)
        state = fn.state or "-"
        runs: list[list[Any]] = [
            ["Runtime", _Tone(status.label, status.tone) if status.tone else status.label, _runtime_meaning(status)],
            ["Handler", fn.handler or "-", _handler_meaning(fn)],
            ["Package", package, self._call_for("code", fn) + " lists its files and shows the handler's source"
             if fn.package_type == "Zip" else "The code is inside the image"],
            ["Memory", f"{fn.memory:,} MB ({_cpu(fn)})", f"Memory also sets the CPU: a full vCPU at "
             f"{MEMORY_PER_VCPU:,} MB"],
            ["Timeout", f"{fn.timeout:,} seconds", f"A run is stopped after this (the most Lambda allows is "
             f"{MAX_TIMEOUT} s)"],
            ["/tmp storage", f"{fn.ephemeral_storage:,} MB" + (" (512 MB free)" if fn.ephemeral_storage > 512 else
                                                                " (free)"),
             "Above 512 MB is billed by the GB-second" if fn.ephemeral_storage > 512 else "Scratch space for a run"],
            ["Architecture", fn.architecture, "Graviton: 20% less per GB-second than x86_64" if fn.arm
             else "arm64 (Graviton) costs 20% less per GB-second"],
            ["Concurrency", concurrency, concurrency_meaning],
            ["Tracing", "X-Ray on" if fn.tracing == "Active" else "off",
             "Each call's path through AWS is traced in X-Ray" if fn.tracing == "Active"
             else "No X-Ray traces of its calls"],
            ["Logs", fn.log_group, _log_meaning(fn, detail.log_group, "log_group" in detail.errors)],
            ["State", _Tone(state, "warn") if state not in ("Active", "-") else state,
             fn.state_reason or ("Ready to run" if state == "Active" else "")],
            ["Last change", f"{_fmt_dt(fn.last_modified, zone=True)} ({human_age(fn.last_modified, now)})",
             f"Last update: {fn.last_update or '-'}" + _reason(fn.last_update_reason)],
        ]
        if detail.runtime_updates:
            runs.append(["Runtime updates", detail.runtime_updates,
                         "Pinned to one runtime version: no security patches" if detail.runtime_updates == "Manual"
                         else "Lambda applies runtime patches itself"])
        if fn.snapstart or runtime_family(fn.runtime) in ("java", "python", "dotnet"):
            runs.append(["SnapStart", "on" if fn.snapstart else "off",
                         "New copies start from a snapshot, for faster cold starts (published versions)"])
        sections["runs"].append(_Table(["Setting", "Value", "What it means"], runs, title="What it runs", max_rows=0,
                                       prose_cols=(2,)))

        trigger_rows = []
        for t in detail.triggers:
            disabled = (t.state or "").lower() == "disabled"
            state_cell: Any = (_Tone("public", "warn") if t.public else _Tone(t.state or "", "warn") if disabled
                               else t.state or "-")
            trigger_rows.append([t.kind, t.source or "-", _VIA.get(t.via, t.via), state_cell,
                                 "; ".join(filter(None, [t.detail, t.last_result])) or "-"])
        if trigger_rows:
            sections["triggers"].append(_Table(
                ["Trigger", "Source", "How", "State", "Details"], trigger_rows, max_rows=0,
                title="What triggers it (event source mappings, its resource policy and URL)"))
        elif not {"policy", "triggers"} & set(detail.errors):
            sections["triggers"].append(_Note(
                "Nothing in its resource policy or event source mappings calls it: it's called directly (an SDK, "
                "aws lambda invoke, Step Functions, or a service that uses its own role), or not at all."
            ))
        async_config = detail.async_config
        asynchronous = [t for t in detail.triggers if t.asynchronous]
        if async_config is not None and (asynchronous or async_config.on_failure or async_config.on_success
                                         or fn.dead_letter):
            sections["async"].append(_Table(["Setting", "Value"], [
                ["Retries after a failure", _times(async_config.retries) if async_config.retries else "none"],
                ["Oldest event kept", _window(async_config.max_age)],
                ["When it succeeds, the result goes to", async_config.on_success or "-"],
                ["When it fails for good, the event goes to", async_config.on_failure or fn.dead_letter
                 or _Tone("nowhere: dropped", "warn" if asynchronous else "")],
            ], title="When an asynchronous call fails (events from S3, SNS, EventBridge...)", max_rows=0))

        if fn.vpc_id:
            network = (f"VPC {fn.vpc_id} ({_plural(len(fn.subnets), 'subnet')}, "
                       f"{_plural(len(fn.security_groups), 'security group')})")
            network_meaning = ("It reaches your VPC's resources, and the internet and AWS APIs only through a NAT "
                               "gateway or VPC endpoints")
        else:
            network = "Lambda's own network"
            network_meaning = "It reaches the internet and AWS APIs, but nothing private in your VPCs"
        access: list[list[Any]] = [
            ["Execution role", (fn.role or "-").split("/")[-1],
             f"What its code may do in AWS: the role's policies in IAM ({fn.role})" if fn.role else ""],
            ["Network", network, network_meaning],
            ["Environment variables", ", ".join(fn.env_names) if fn.env_names else "none",
             "Values hidden here: they often hold secrets" if fn.env_names else ""],
        ]
        if fn.file_systems:
            access.append(["File systems", "; ".join(fn.file_systems), "EFS file systems mounted into it"])
        if fn.layers:
            access.append(["Layers", ", ".join(f"{layer.name} ({human_size(layer.code_size)})" for layer in fn.layers),
                           "Shared code added next to its own"])
        if fn.env_names:
            access.append(["Encryption", fn.kms_key.split("/")[-1] if fn.kms_key else "AWS-managed key",
                           "The key that encrypts its environment variables"])
        sections["access"].append(_Table(["Setting", "Value", "What it means"], access, title="What it can reach",
                                         max_rows=0, prose_cols=(2,)))

        version_rows: list[list[Any]] = []
        for alias in detail.aliases:
            weights = ", ".join(f"{share:.0%} to version {v}" for v, share in alias.weights.items())
            version_rows.append([f"alias {alias.name}", f"version {alias.version}" + (f" ({weights})" if weights else ""),
                                 alias.description or "-"])
        if detail.versions:
            ordered = sorted(detail.versions, key=lambda v: int(v.version) if v.version.isdigit() else 0)
            newest = ordered[-1]
            version_rows.append([_plural(len(ordered), "published version"),
                                 f"newest {newest.version} ({human_age(newest.last_modified, now)})",
                                 f"{human_size(sum(v.code_size for v in ordered))} of code kept"])
        for pc in detail.provisioned:
            copies = pc.billed or pc.requested
            version_rows.append([f"provisioned concurrency on {pc.qualifier}",
                                 f"{pc.allocated:,} of {pc.requested:,} ready ({pc.status or '-'})",
                                 f"{human_money(provisioned_monthly_cost(fn, copies, prices))}/month, used or not"])
        if version_rows:
            sections["versions"].append(_Table(["What", "Value", "Notes"], version_rows, max_rows=0,
                                               title="Versions, aliases and provisioned concurrency"))

        if m is not None and m.invocations:
            day_rows, bars = [], []
            busiest = max((d.invocations for d in m.daily), default=0.0)
            for d in sorted(m.daily, key=lambda d: d.start, reverse=True):
                day_rows.append([d.start.strftime("%Y-%m-%d %a"), _count(round(d.invocations)), _count(round(d.errors)),
                                 _count(round(d.throttles)), _ms(d.avg_duration), _ms(d.duration_max)])
                bars.append(_share(d.invocations, busiest))
            sections["days"].append(_Table(
                ["Day (UTC)", "Calls", "Errors", "Throttles", "Avg run time", "Longest"], day_rows, bars=bars,
                bar_label="Calls vs. the busiest day", max_rows=0,
                title=f"The last {days} days (CloudWatch; days without data are left out)",
            ))
        if cost:
            basis = (f"{self._price_basis()}; usage over the last {days} days scaled to a month" if m is not None
                     else self._price_basis())
            sections["cost"].append(cost_split(cost, _PARTS, basis=basis))
            workings = cost_workings(fn, m, detail.provisioned, detail.log_group, prices=prices)
            sections["cost"].append(cost_workings_table(workings, _PARTS))
        if fn.tags:
            sections["tags"].append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(fn.tags.items())],
                                           title="Tags", collapsed=True, max_rows=0))
        sections["raw"].append(_JsonTree(fn.raw, items=100, name="raw", collapsed=True,
                                         title="Configuration, as Lambda returns it (environment values hidden)"))
        steps = []
        if m is not None and m.errors:
            steps.append((self._call_for("errors", fn), "its errors, grouped by cause"))
        steps.append((self._call_for("performance", fn), "run times, memory used and cold starts"))
        if fn.package_type == "Zip":
            steps.append((self._call_for("code", fn), "the files in its package, and the handler's source"))
        if len(steps) < 3:
            steps.append((self._call_for("logs", fn), "the newest lines it logged"))
        sections["next"].append(_Next(steps))
        return sections

    # ------------------------------------------------------------------ when something goes wrong

    @_friendly_errors
    def errors(self, name: str, *, since: Any = "24h", region: str | None = None, limit: Any = 10_000) -> None:
        """
        A function's errors in the last 24 hours, grouped by cause: timeouts, running out of memory, code that
        can't load, missing permissions and exceptions, each with how often, when, an example and the run to look
        at, next to CloudWatch's count of failed and throttled calls. since='7d' or '2026-10-01' looks further
        back; reading stops at the newest `limit` error lines.
        """
        limit = _as_count(limit, "limit")
        with self._progress("Reading error lines", unit="lines") as tick, self._named(name, region):
            report = self.core.errors(name, since=since, region=region, limit=limit, progress=tick)
        self._show(self._errors_blocks(report, since=since, limit=limit))

    def _errors_blocks(self, report: ErrorReport, *, since: Any = "24h", limit: int | None = 10_000,
                       for_window: bool = False) -> list[Any]:
        """
        errors()'s report, as blocks; for_window=True leaves out the errors by cause and the newest error lines (the
        explorer lists them as lines to click).
        """
        fn, m = report.function, report.metrics
        window = _window((report.until - report.since).total_seconds())
        has_cases = bool(report.groups) and not for_window
        found = fate_findings(fn, report.fates) + (window_findings(report) if has_cases else error_findings(report))
        last = max((g.last for g in report.groups), default=None)
        cards: list[tuple[str, ...]] = [
            ("Failed calls (CloudWatch)", _count(round(m.errors)) if m is not None else "-"),
            ("Calls", _count(round(m.invocations)) if m is not None else "-"),
            ("Error rate", _pct(m.error_rate) if m is not None and m.invocations else "-"),
            ("Causes", _count(len(report.groups))),
            ("Last error", human_age(last, report.until) if last else "-"),
        ]
        if m is not None and m.throttles:
            cards.insert(3, ("Throttled calls", _count(round(m.throttles)), "warn"))
        if lost_events(report):
            cards.insert(3, ("Events lost", _count(lost_events(report)), "bad"))
        sub = " · ".join(filter(None, [
            f"log group {report.log_group}",
            f"the last {window} (since {_fmt_dt(report.since, zone=True)})",
            fn.region if fn.region != self.core.region else "",
        ]))
        cases = _cause_blocks(report, run_call=lambda rid: self._call_for(
            "logs", fn, request_id=rid, **_since_arg(since, "24h"))) if has_cases else []
        blocks: list[Any] = [_Title(f"Errors in {fn.name}", sub), *cases[:1], _Cards(cards)]
        blocks += self._log_notes(report.errors, fn, report.log_group)
        if report.truncated:
            blocks.append(_Note(
                f"Reading stopped at the newest {_count(limit)} error lines, which reach back to "
                f"{_fmt_dt(report.covered_from, zone=True)}: pass limit= to read more, or a shorter since=."
            ))
        empty = (f"No errors in the last {window}: no error lines in its logs"
                 + (", and CloudWatch counted no failed calls." if m is not None else "."))
        blocks += cases[1:2] + _fates_blocks(report)
        if found or not has_cases:
            blocks.append(_Findings(found, empty=empty if "logs" not in report.errors else ""))
        blocks += cases[2:]
        timeouts = any(g.kind in ("Timeout", "Out of memory") for g in report.groups)
        steps = []
        newest = next((e for e in report.newest if e.request_id), None)
        if newest is not None:
            steps.append((self._call_for("logs", fn, request_id=newest.request_id, **_since_arg(since, "24h")),
                          "the whole run behind the newest error"))
        if timeouts:
            steps.append((self._call_for("performance", fn, **_since_arg(since, "24h")),
                          "run times and memory: how close runs come to the limits"))
        steps.append((self._call_for("function_info", fn), "its settings, triggers and last 30 days"))
        blocks.append(_Next(steps))
        return blocks

    def _log_notes(self, errors: dict[str, str], fn: Function, group: str) -> list[Any]:
        """
        Notes for what couldn't be read: no log group yet, logs that can't be read, or CloudWatch's numbers.
        """
        notes: list[Any] = []
        code = errors.get("logs")
        if code == "ResourceNotFoundException":
            notes.append(_Note(
                f"{fn.name} has no log group yet ({group}): it hasn't logged anything. It creates one on its first "
                "run, if its role may write logs (logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents).",
                "warn"))
        elif code:
            notes.append(_Note(f"Couldn't read its logs ({_why(code, 'logs:FilterLogEvents')}).", "warn"))
        if errors.get("metrics"):
            notes.append(_Note(
                f"Couldn't read CloudWatch's numbers ({_why(errors['metrics'], 'cloudwatch:GetMetricData')}).", "warn"))
        return notes

    def _search_hint(self, fn: Function, search: str | None, request_id: str | None) -> str:
        """
        The line over logs()' runs saying how to search every line in CloudWatch, and where to filter as you type.
        """
        if request_id:
            return ""
        example = search or "KeyError OR Timeout"
        return (f"Search every line in CloudWatch: {self._call_for('logs', fn, search=example)}. With ipywidgets "
                "installed, a search box over this report filters as you type.")

    @_friendly_errors
    def logs(
        self,
        name: str,
        *,
        since: Any = None,
        search: str | None = None,
        request_id: str | None = None,
        n: Any = 50,
        region: str | None = None,
    ) -> None:
        """
        A function's newest runs, like a log viewer: a line each (status, start, run time, memory used, cold start,
        what failed) that opens to its log lines, for the runs holding its newest 50 lines of the last hour, with
        chips that keep the failed runs, errors or cold starts. search='KeyError' keeps the runs with that text,
        whole, the words marked (case-sensitive; join words with AND, OR and NOT, 'KeyError OR Timeout', or pass a
        CloudWatch Logs filter pattern), request_id= shows one run from start to end (from the last 24 hours), and
        since='24h' or '2026-10-01' looks further back. In Jupyter with ipywidgets, a search box sits over the
        report: typing keeps the runs read that hold the text, and Enter searches every line in CloudWatch.
        """
        n = _as_int(n, "n")
        if n < 1:
            raise ValueError("n= is how many lines to show: 1 or more")
        blocks = self._logs_blocks(name, since=since, search=search, request_id=request_id, n=n, region=region)
        if request_id or not _can_search_here(self.use_html):
            self._show(blocks)
            return
        from .explorer.log_search import _LogsAsked, _LogSearch

        _LogSearch(self, blocks, _LogsAsked(name, since, n, region), search or "").show()

    def _logs_blocks(self, name: str, *, since: Any, search: str | None, request_id: str | None, n: int,
                     region: str | None) -> list[Any]:
        """
        logs()' report, as blocks: the search box above it in a notebook draws it again for each search.
        """
        chosen = since  # next steps carry over a since= the user picked, not this command's default
        since = since if since is not None else ("24h" if request_id else "1h")
        words = word_search(search)
        pattern = words.pattern() if words is not None else _filter_pattern(search)
        limit = 10_000 if request_id else max(3 * n, 300)
        searching = bool(search) and not request_id
        with self._progress("Reading log lines", unit="lines") as tick, self._named(name, region):
            if searching:
                page = self.core.log_runs(name, since=since, search=search, limit=max(limit, _SEARCH_RUN_LINES),
                                          region=region, progress=tick)
            else:
                page = self.core.log_events(name, since=since, pattern=pattern, request_id=request_id, limit=limit,
                                            region=region, progress=tick)
        fn = page.function
        window = _window((page.until - page.since).total_seconds())
        events = page.events
        phrase = _phrase(search)
        if searching:
            events = [e for e in events if e.matched] or _lines_found(events, phrase, words)
        if not request_id:
            events = [e for e in events if not _lifecycle(e.message)]
        shown = events[-n:]
        log_runs = _runs_holding(page.events, shown, request_id)
        failed_runs = [run for run in log_runs if run.status in ("failed", "timeout")]
        error_lines = [e for e in shown if classify_error(e.message)]
        cards: list[tuple[str, ...]] = [
            ("Runs", _count(sum(1 for run in log_runs if run.request_id))),
            ("Failed", _count(len(failed_runs)), "bad" if failed_runs else ""),
            ("Lines shown", _count(len(shown))),
            ("Error lines", _count(len(error_lines))),
            ("Newest line", human_age(shown[-1].time, page.until) if shown else "-"),
        ]
        what = (f"run {request_id}" if request_id
                else f"the newest {_plural(len(shown), 'line')} of the last {window}" if shown
                else f"the last {window}")
        sub = " · ".join(filter(None, [
            f"log group {page.log_group}",
            _search_said(search, phrase, words) if search and not request_id else "",
            what,
            "grouped into runs" if not request_id else "",
        ]))
        title = f"Run {request_id} of {fn.name}" if request_id else f"Logs of {fn.name}"
        blocks: list[Any] = [_Title(title, sub), _Cards(cards)]
        blocks += self._log_notes(page.errors, fn, page.log_group)
        if not shown and "logs" not in page.errors:
            if request_id:
                blocks.append(_Note(
                    f"No run {request_id} in the last {window}. errors() and performance() name runs by their "
                    "request IDs; pass since= to look further back."
                ))
            elif page.latest is not None:
                back = max(1, math.ceil((page.until - page.latest).total_seconds() / 86400) + 1)
                blocks.append(_Note(
                    f"No lines{' matching ' + repr(search) if search else ''} in the last {window}. The newest line in "
                    f"its log group is from {human_age(page.latest, page.until)}: "
                    f"{self._call_for('logs', fn, since=f'{back}d', **({'search': search} if search else {}))}."
                ))
            else:
                blocks.append(_Note(
                    f"No lines in the last {window}"
                    + (f" matching {search!r}" if search else ", and none at all in its log group: it hasn't run, "
                       "or it logs somewhere else") + "."
                ))
        if page.truncated:
            blocks.append(_Note(
                f"Reading stopped after {_count(len(page.events))} lines, back to "
                f"{_fmt_dt(page.covered_from, zone=True)}: these are the newest."
            ))
        if shown:
            blocks.append(_LogRuns(
                log_runs, timeout=fn.timeout, words=_marked_words(phrase, words), searched=bool(search),
                opened=_opened_runs(log_runs, request_id=request_id, search=search),
                title="Its lines, oldest first" if request_id else "Runs, newest first: click one for its lines",
                search_hint=self._search_hint(fn, search, request_id),
            ))
        steps = []
        if error_lines:
            steps.append((self._call_for("errors", fn, **_since_arg(chosen, "24h")), "every error, grouped by cause"))
            rid = next((run.request_id for run in log_runs if run.failures and run.request_id), None)
            if rid and not request_id:
                steps.append((self._call_for("logs", fn, request_id=rid), "the whole run behind the newest error"))
        if not request_id and since == "1h":
            steps.append((self._call_for("logs", fn, since="24h"), "the last 24 hours"))
        steps.append((self._call_for("performance", fn), "run times, memory used and cold starts"))
        blocks.append(_Next(steps[:3]))
        return blocks

    @_friendly_errors
    def invoke(
        self,
        name: str,
        event: Any = None,
        *,
        region: str | None = None,
        wait_logs: float = 10,
        dry_run: bool = False,
    ) -> None:
        """
        Runs a function once with an event and shows what happened in one report, like the console's Test button
        and its log tab together: whether it worked, what it returned (the error and traceback when it failed), its
        run time, memory used, cold start and cost, and every line it logged during that run. event= is a dict, a
        list, JSON text or nothing ({}); 'name:alias' runs that alias. The last 4 KB of its log come back with the
        answer at once; a longer log is read from CloudWatch Logs, waiting up to wait_logs= seconds for it.
        dry_run=True only checks that you may invoke it. This really runs the function, with whatever its code
        writes or sends, so use it on functions that are safe to call.
        """
        with self._progress("Running", unit="calls") as tick, self._named(name, region):
            invoked = self.core.invoke(name, event, region=region, wait_logs=wait_logs, dry_run=dry_run)
            tick(1)
        fn = invoked.function
        qualified = f"{fn.name}:{invoked.qualifier}" if invoked.qualifier else fn.name
        region_args = {"region": fn.region} if fn.region and fn.region != self.core.region else {}

        def call(command: str, *args: Any, **kwargs: Any) -> str:
            target = qualified if command == "invoke" else fn.name
            return _call(command, target, *args, **kwargs, **region_args)

        self._show(invoke_blocks(invoked, call=call))

    @_friendly_errors
    def runs(
        self,
        name: str,
        *,
        since: Any = "24h",
        where: str | None = None,
        sort: Any = None,
        columns: Any = None,
        group_by: str | None = None,
        fields: dict[str, str] | None = None,
        n: Any = 50,
        region: str | None = None,
        limit: Any = 10_000,
    ) -> None:
        """
        A function's runs in the last 24 hours, one row each: status, start, run time, memory, cold start and what it
        logged. where='duration > 15s and cold' keeps the runs that match (also status, memory, error ~ KeyError, or
        any field its JSON logs or logged event carry: event.Records.s3.object.size > 30MB), sort='-duration' puts
        the longest first, columns=[...] picks what each row shows, group_by='hour' sums them up per hour (or per
        error_type, version or any field), and since='today' or '7d' reads another range.
        """
        n = _as_int(n, "n")
        limit = _as_count(limit, "limit")
        with self._progress("Reading runs", unit="lines") as tick, self._named(name, region):
            table = self.core.runs(name, since=since, where=where, sort=sort, fields=fields, limit=limit,
                                   region=region, progress=tick)
        fn = table.function
        chosen = [columns] if isinstance(columns, str) else list(columns) if columns else None
        blocks = _runs_blocks(
            table, call=lambda command, **kwargs: self._call_for(command, fn, **kwargs),
            since_arg=_since_arg(since, "24h"), layout=_RunsLayout(chosen, group_by, n))
        blocks[1:1] = self._log_notes(table.errors, fn, fn.log_group)
        self._show(blocks)

    @_friendly_errors
    def performance(self, name: str, *, since: Any = "24h", region: str | None = None, limit: Any = 5_000,
                    compare: bool = True) -> None:
        """
        How a function's runs went in the last 24 hours, from the REPORT line Lambda logs after each one: a verdict,
        the typical and slow run time against the timeout, every run over time with the timeouts in a lane of their
        own, how it compares with the 24 hours before and between versions, cold starts against warm runs, memory
        used against what it has and per container, and the slowest runs. since='7d' looks further back; reading
        stops at the newest `limit` runs; compare=False skips reading the window before.
        """
        limit = _as_count(limit, "limit")
        with self._progress("Reading REPORT lines", unit="lines") as tick, self._named(name, region):
            perf = self.core.performance(name, since=since, region=region, limit=limit, compare=compare,
                                         progress=tick)
        self._show(self._performance_blocks(perf, since=since, limit=limit))

    def _performance_blocks(self, perf: Performance, *, since: Any = "24h", limit: int | None = 5_000,
                            for_window: bool = False) -> list[Any]:
        """
        performance()'s report, as blocks; for_window=True leaves out the slowest runs (the explorer lists them as runs
        to click).
        """
        fn = perf.function
        setup = _PerformanceSetup(
            window=_window((perf.until - perf.since).total_seconds()),
            call=lambda command, *args, **kwargs: self._call_for(command, fn, *args, **kwargs),
            limit=limit, since=_since_arg(since, "24h"), prices=self.core.prices, price_basis=self._price_basis(),
            for_window=for_window,
        )
        return performance_blocks(perf, setup, self._log_notes(perf.errors, fn, perf.log_group))

    # ------------------------------------------------------------------ code

    @_friendly_errors
    def code(self, name: str, file: str | None = None, *, region: str | None = None, max_size: Any = "50MB") -> None:
        """
        What's in a function's deployment package: its files and folders by size, findings (secrets packed in it,
        size near the limit, files that never run), and the source of the file that holds the handler, or of
        file='utils.py' (a path, a name or a glob). Downloads the .zip, up to max_size, from the link Lambda gives;
        nothing in it is run. A secrets file's text (.env, keys) isn't shown, and a container image is named, not
        pulled.
        """
        with self._named(name, region):
            package = self.core.code(name, file=file, max_size=max_size, region=region)
        self._show(self._code_blocks(package))

    def _code_blocks(self, package: CodePackage, *, source: bool = True) -> list[Any]:
        """
        code()'s report, as blocks; source=False leaves out the shown file's text (the explorer shows it on its
        own).
        """
        fn = package.function
        found = package_findings(package)
        blocks: list[Any] = [_Title(
            f"Code of {fn.name}",
            " · ".join(filter(None, [
                f"{fn.package_type} package" if fn.package_type == "Zip" else "container image",
                f"handler {fn.handler}" if fn.handler else "",
                fn.runtime or "",
            ])),
        )]
        if package.note and not package.files:
            blocks += [_Note(package.note), _Next([(self._call_for("function_info", fn), "its settings")])]
            return blocks
        # the same checks package_findings() warns about, so a card is amber only when a warning is about it
        handler_missing = bool(fn.handler) and runtime_family(fn.runtime) in _HANDLER_EXTENSIONS and not (
            package.handler_file)
        too_big = package.unzipped + fn.layer_size >= 0.8 * UNZIPPED_LIMIT
        blocks.append(_Cards([
            ("Package (zipped)", human_size(package.size)),
            ("Unzipped", human_size(package.unzipped), "warn" if too_big else ""),
            ("Files", _count(len(package.files))),
            ("Layers", f"{len(fn.layers):,}" + (f" · {human_size(fn.layer_size)}" if fn.layers else "")),
            ("Handler file", package.handler_file or "-", "warn" if handler_missing else ""),
        ]))
        blocks.append(_Findings(found, empty="Nothing in the package stands out: the handler's file is there, no "
                                             "secrets files, and it's well inside Lambda's size limit."))
        folders: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for f in package.files:
            folders[_top_folder(f.path)][0] += 1
            folders[_top_folder(f.path)][1] += f.size
        top = sorted(folders.items(), key=lambda item: -item[1][1])[:12]
        blocks.append(_Table(
            ["Folder", "Files", "Size"], [[folder, _count(n), human_size(size)] for folder, (n, size) in top],
            bars=[_share(size, package.unzipped) for _, (_, size) in top], bar_label="Share",
            title="What takes the space (unzipped)", max_rows=0,
        ))
        blocks.append(_Table(
            ["File", "Size"], [[f.path, human_size(f.size)] for f in sorted(package.files, key=lambda f: f.path)],
            title="Every file", collapsed=True,
        ))
        if package.note and source:
            blocks.append(_Note(package.note))
        if package.source is not None and source:
            label = "the handler's file" if package.shown_file == package.handler_file else "the file you asked for"
            blocks.append(_Text(package.source, title=f"{package.shown_file} ({label})"))
            if package.source_truncated:
                blocks.append(_Note(f"Only the first {human_size(_SOURCE_LIMIT)} of {package.shown_file} is shown."))
        others = [f.path for f in package.files if f.path != package.shown_file and not _JUNK_RE.search(f.path)
                  and f.path.endswith((".py", ".js", ".mjs", ".ts", ".rb")) and "/" not in f.path]
        steps = []
        if others:
            steps.append((self._call_for("code", fn, file=others[0]), "another of its own files"))
        steps.append((self._call_for("function_info", fn), "its settings, triggers and last 30 days"))
        blocks.append(_Next(steps))
        return blocks

    @_friendly_errors
    def diff(self, name: str, old: Any = None, new: Any = None, *, region: str | None = None,
             max_size: Any = "50MB") -> None:
        """
        What changed between two versions of a function: its own files that differ, line by line, with the Python
        functions each change is in, the libraries packed with it that were added, removed or upgraded, and its
        settings and environment variable names (never their values). diff('etl') compares $LATEST with the newest
        published version whose code is different, diff('etl', 3) with version 3, diff('etl', 3, 5) two versions,
        and diff('etl:prod') what an alias runs with the version before it. Lambda keeps the code of published
        versions only: code deployed over $LATEST without publishing a version is gone, and the report says so.
        Downloads both packages, up to max_size, like code(); nothing in them is run.
        """
        with self._named(name, region):
            diff = self.core.diff(name, old, new, region=region, max_size=max_size)
        elsewhere = diff.function.region if diff.function.region != self.core.region else None
        self._show(_diff_blocks(diff, region=elsewhere))

    @_friendly_errors
    def how_it_works(self, name: str, *, region: str | None = None, max_size: Any = "50MB") -> None:
        """
        How a Python function's code works, read from its package with Python's ast module (nothing in it is run):
        two tabs, a graph of what comes in, who calls whom and what it reaches, and its steps to click through like
        a debugger, from the handler into each function it calls, with what each line does (AWS calls, environment
        variables, event fields, HTTP requests, logs); then a few sentences and findings (a variable that isn't set,
        an import that isn't there, an HTTP call without a timeout, a list call that reads one page, errors it drops).
        Downloads the .zip, up to max_size, like code(). core.code_map() returns the same as data: its AWS calls,
        environment variables, event fields and imports.
        """
        with self._named(name, region):
            cmap = self.core.code_map(name, region=region, max_size=max_size)
        self._show(self._how_blocks(cmap))

    @_friendly_errors
    def how_it_works_page(self, name: str, path: str | None = None, *, region: str | None = None,
                          max_size: Any = "50MB") -> None:
        """
        how_it_works() as a page of its own, to read in a tab with room for it: saved as <name>-how-it-works.html in
        lambda-downloads next to the notebook (or at path=), with a link that opens it. In JupyterLab a click opens
        it in a JupyterLab tab; middle-click, or right-click and Open link in new tab, for a browser tab.
        """
        with self._named(name, region):
            cmap = self.core.code_map(name, region=region, max_size=max_size)
        saved = self._save_how_page(cmap, path)
        self._show(self._page_blocks(saved))

    def _save_how_page(self, cmap: CodeMap, path: str | None = None) -> SavedPage:
        """
        how_it_works()'s report drawn as a page and saved where download() saves (the explorer's ↗ Open in a tab too).
        """
        fn = cmap.function or Function(cmap.handler or "code", handler=cmap.handler)
        whole_graph = code_graph(cmap, max_functions=None, max_depth=None, max_lines=None)
        page = _render_page(self._how_blocks(cmap, whole_graph), f"How {fn.name} works", theme=_theme_name())
        return self.core._save_page(fn, page, path)

    def _page_blocks(self, saved: SavedPage) -> list[Any]:
        """
        how_it_works_page()'s report: the link, and how to open it where.
        """
        fn = saved.function
        said = ["In JupyterLab, a click opens it in a JupyterLab tab you can drag beside the notebook; middle-click "
                "it, or right-click it and pick Open link in new tab, for a browser tab of its own"
                + (", where it replaced the page saved before." if saved.replaced else ".")]
        if saved.marked:
            said.append(f"{_local_path(os.path.dirname(saved.path))} was made for downloads, with a .gitignore that "
                        "keeps it out of git.")
        said.append("It's a snapshot of the code as it is now: run it again after a deploy.")
        return [_Title(f"Saved how {fn.name} works as a page", f"{_local_path(saved.path)} · {human_size(saved.size)}"),
                _PageLink(saved.path), _Note(" ".join(said)),
                _Next([(self._call_for("how_it_works", fn), "the same report here, in the notebook")])]

    def _how_blocks(self, cmap: CodeMap, graph: CodeGraph | None = None) -> list[Any]:
        """
        how_it_works()'s report, as blocks (the explorer's How it works shows them too). graph is the one to draw:
        by default code_graph()'s, cut to fit a notebook; the saved page draws all of it.
        """
        fn = cmap.function or Function(cmap.handler or "code", handler=cmap.handler)
        read = (f"read from {_plural(cmap.files, 'Python file')} ({cmap.lines:,} lines) with Python's ast, nothing run"
                if cmap.files else "")
        blocks: list[Any] = [_Title(f"How {fn.name} works", " · ".join(filter(None, [
            f"handler {cmap.handler}" if cmap.handler else "", fn.runtime or "", read])))]
        if cmap.note:
            return blocks + [_Note(cmap.note), _Next([
                (self._call_for("code", fn), "the files in its package, and the handler's source"),
                (self._call_for("function_info", fn), "its settings, triggers and last 30 days")])]
        found = code_map_findings(cmap)
        handler_name = (cmap.handler or "").rsplit(".", 1)[-1]
        entry = _short_name(cmap.entry) if cmap.entry else None
        names = list(dict.fromkeys(e.name for e in cmap.env))
        unset = {e.name for e in cmap.env if e.required and fn.env_error is None and cmap.function is not None
                 and e.name not in fn.env_names and e.name not in _LAMBDA_ENV}
        ops = list(dict.fromkeys((c.service, c.operation) for c in cmap.aws))
        services = cmap.services
        kind = _event_kind(cmap.event)
        files = len({f.file for f in cmap.functions})
        overview: list[Any] = [_Cards([
            ("Starts at", f"{entry}() in {cmap.handler_file}" if entry else f"no {handler_name}() in {cmap.handler_file}",
             "" if entry else "warn"),
            ("Expects", kind or ("an event" if cmap.event else "-")),
            ("AWS calls", f"{len(ops)} · {', '.join(services[:3])}{', …' if len(services) > 3 else ''}" if ops
             else "none it can see"),
            ("Environment", (f"{_plural(len(names), 'variable')} read" + (f" · {len(unset)} not set" if unset else ""))
             if names else "none read", "warn" if unset else ""),
            ("Functions run", f"{len(cmap.functions):,}" + (f" in {files:,} files" if files > 1 else "")),
        ])]
        summary = _code_summary(cmap)
        if summary:
            overview.append(_Note(" ".join(summary)))
        overview.append(_Findings(found, empty=(
            "Nothing in the code stands out: the variables it needs are set, what it imports is there, and its HTTP "
            "and list calls look complete.")))
        graph = graph or code_graph(cmap)
        graph_blocks: list[Any] = []
        if graph.nodes:
            graph_blocks.append(_Graph(graph, "How it fits together: what comes in, who calls whom, and what it reaches"))
            if graph.what_was_cut():
                graph_blocks.append(_Note(f"The graph is cut to fit here: it {graph.what_was_cut()}. "
                                          f"{self._call_for('how_it_works_page', fn)} saves a page with the whole graph."))
            graph_blocks.append(_Text(graph.mermaid(), code=True, collapsed=True, title=(
                "The same graph in Mermaid, to paste in a Markdown file, a wiki or a pull request")))
        walk = _Walk(cmap.startup, cmap.steps, "When Lambda starts it (once per cold start, before the first call)",
                     f"What each call does, step by step from {entry}()" if entry else "What each call does",
                     empty=f"Lambda calls {handler_name}(), which isn't in {cmap.handler_file}." if not entry else
                     "(nothing it can follow)")
        blocks.append(_Tabs([("Graph", graph_blocks), ("Step by step", [walk])]) if graph_blocks else walk)
        blocks.extend(overview)
        said = ["It reads its own code with Python's ast module and runs none of it. It follows the boto3 clients the "
                "code makes and calls into its own functions; a client handed in from elsewhere or made inside a "
                "library isn't followed, so there may be AWS calls this doesn't list."]
        if cmap.other_files:
            said.append(f"{_plural(cmap.other_files, 'other Python file')} in the package "
                        f"{'is' if cmap.other_files == 1 else 'are'}n't imported from the handler (scripts, other "
                        "handlers), so they're left out.")
        if cmap.unreached:
            said.append(f"{_plural(cmap.unreached, 'function')} in the files it read never "
                        f"run{'s' if cmap.unreached == 1 else ''} from the handler.")
        if cmap.limited:
            said.append(f"To keep it short, it {cmap.limited}.")
        blocks.append(_Note(" ".join(said)))
        steps = [(self._call_for("code", fn, file=cmap.handler_file), "the handler's file, line by line")]
        other = next((f.file for f in cmap.functions if f.file != cmap.handler_file), None)
        if other:
            steps.append((self._call_for("code", fn, file=other), "another of its files that runs"))
        steps.append((self._call_for("function_info", fn), "its settings: environment variables, role and triggers"))
        steps.append((self._call_for("errors", fn), "the errors it hit in the last 24 hours, grouped by cause"))
        blocks.append(_Next(steps[:3]))
        return blocks

    @_friendly_errors
    def download(self, name: str, path: str | None = None, *, region: str | None = None) -> None:
        """
        Saves a function's deployment package (the .zip) on the notebook's disk, in lambda-downloads next to the
        notebook, or at path= (a file, or a folder), and says where, how big, and whether it's the code Lambda runs
        (its SHA-256 against Lambda's). In JupyterLab, right-click it in the file browser and pick Download to get it
        on your computer. Nothing in it is unzipped or run; a container image is named, not pulled.
        """
        with self._named(name, region):
            saved = self.core.download(name, path, region=region)
        self._show(self._download_blocks(saved))

    def _download_blocks(self, saved: PackageDownload) -> list[Any]:
        """
        download()'s report, as blocks.
        """
        fn = saved.function
        where = _local_path(saved.path)
        ref = fn.name if fn.version in ("", "$LATEST") else f"{fn.name}:{fn.version}"  # the version it saved
        elsewhere = {"region": fn.region} if fn.region and fn.region != self.core.region else {}
        own_path = saved.path != os.path.join(self.core.downloads_folder(), package_file_name(fn))
        blocks: list[Any] = [
            _Title(f"Downloaded {fn.name}'s code", " · ".join(filter(None, [
                where, f"version {fn.version}" if fn.version not in ("", "$LATEST") else "",
                f"handler {fn.handler}" if fn.handler else "", fn.runtime or ""]))),
            _Cards([
                ("Saved as", where),
                ("Size (zipped)", human_size(saved.size)),
                ("Files", _count(saved.files) if saved.files is not None else "not a .zip",
                 "" if saved.files is not None else "warn"),
                ("SHA-256", {True: "matches Lambda's", False: "differs from Lambda's", None: "not reported"}[
                    saved.sha256_matches], "warn" if saved.sha256_matches is False else ""),
            ]),
        ]
        if saved.sha256_matches is False:
            blocks.append(_Note(
                f"Its SHA-256 differs from the CodeSha256 Lambda reports, so it may not be the code {fn.name} runs: "
                "the code was probably deployed again while it downloaded. "
                f"{_call('download', ref, *([saved.path] if own_path else []), **elsewhere)} downloads it again.",
                "warn"))
        if saved.files is None:
            blocks.append(_Note("Lambda's link gave a file that isn't a readable .zip, so code() can't list it.",
                                "warn"))
        said = [f"It's on the notebook's disk{', where it replaced the one downloaded before' if saved.replaced else ''}"
                ": in JupyterLab, right-click it in the file browser and pick Download to save it on your computer."]
        if saved.marked:
            said.append(f"{_local_path(os.path.dirname(saved.path))} was made for downloads, with a .gitignore that "
                        "keeps it out of git, since a package can hold secrets.")
        said.append("Nothing in it was unzipped or run.")
        blocks.append(_Note(" ".join(said)))
        folder = saved.path[:-4] if saved.path.endswith(".zip") else saved.path + "-files"
        blocks.append(_Next([
            (_call("code", ref, **elsewhere), "what's in it, file by file, and the handler's source"),
            ("import shutil; " + _call("shutil.unpack_archive", _local_path(saved.path), _local_path(folder)),
             "unzip it next to the notebook"),
        ]))
        return blocks

    # ------------------------------------------------------------------ the window

    @_friendly_errors
    def test(self, name: str, event: Any = None, *, region: str | None = None,
             height: int | str | None = None) -> None:
        """
        A window to test one function with an event, like the Lambda console's Test page: paste the event (or start
        from one shaped like what S3, SQS, SNS, API Gateway or EventBridge send) and see it as tidy JSON in colour,
        with the line where it breaks when it isn't valid; ▶ Run runs it once and shows what it returned, every line
        it logged, its run time, memory and cost, and everything else Lambda returned (request ID, version, X-Ray
        trace, headers). event= is what the box starts with. Nothing runs until you click ▶ Run, and then it really
        runs (needs lambda:InvokeFunction). Needs Jupyter and ipywidgets; view.tester is the window.
        """
        if not self.use_html:
            raise _Hint("The test window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, "
                        f"invoke({name!r}, event) runs it once and shows what came back.")
        _require("ipywidgets", "The test window")
        from .explorer.tester import LambdaTester

        self.tester = LambdaTester(name, event, region=region, view=self, height=height, mode="widgets")

    @_friendly_errors
    def explore(self, name: str | None = None, *, tab: str | None = None, region: str | None = None,
                height: int | str | None = None, history: bool | str = True, assistant: Any = None) -> None:
        """
        The explorer window: every function in the region to click through, and for the one you pick, how it's
        doing, its logs run by run (failed runs in red, a search box, a time range, and Live to watch new runs come
        in), its errors grouped by cause, its run times, the code in its package and every setting. Needs Jupyter and
        ipywidgets; view.explorer is the window. name opens one function, on tab= 'logs', 'errors', 'performance',
        'code' or 'settings'; region='all' lists every region. history=False: don't remember the functions you open
        (the Recent chip). assistant=AgentView(...) adds ✦ Ask: a drawer where you ask the agent about what the
        window shows.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, functions() lists every "
                "function, logs('name') shows what one logged, and errors('name') why it fails."
            )
        _require("ipywidgets", "The explorer window")
        from .explorer.window import LambdaExplorer

        self.explorer = LambdaExplorer(name, tab=tab, region=region, view=self, height=height, mode="widgets",
                                      history=history, assistant=assistant)
