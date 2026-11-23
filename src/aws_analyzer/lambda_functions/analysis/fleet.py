"""
The functions() report's reading of a whole region (or several): each function put in a group by what it needs
(failing, paying for nothing, needs a fix, not called, healthy), its findings sorted into kinds so one problem shared
by many functions reads as one job, the sentence the report opens with, and where the monthly bill goes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..._kit.fmt import _plural, _total, human_money

from ..models import Function, FunctionMetrics, RuntimeStatus
from .causes import Verdict
from .cost import function_monthly_cost
from .findings import _recent, function_findings
from .functions import runtime_status
from .overview import Overview


@dataclass(frozen=True)
class FindingKind:
    """
    A kind of finding, found by its wording: the heading the report groups it under, why it matters, the few
    words a function's line shows for it, and the group a warning of this kind puts its function in.
    """

    key: str
    pattern: str
    title: str
    why: str
    short: str
    group: str = "risk"


FINDING_KINDS: tuple[FindingKind, ...] = (
    FindingKind("broken", r"in the Failed state|last update failed|reserved concurrency is 0|can't decrypt",
                "Not running as it should", "Every call fails, or it can't be changed, until this is fixed.",
                "not running", "failing"),
    FindingKind("changed", r"began to fail after it was last changed", "Started failing after a change",
                "Its calls began to fail right after its code or settings changed: look at what changed first.",
                "failing since a change", "failing"),
    FindingKind("errors", r"calls failed \(", "Calls failing",
                "Failed calls are lost work, or retries you pay for.", "failed calls", "failing"),
    FindingKind("trigger", r"reports '|isn't being processed|is disabled: nothing from it", "Triggers not delivering",
                "Events from its trigger wait, or expire, instead of reaching it.", "trigger stuck", "failing"),
    FindingKind("public", r"needs no sign-in|lets anyone invoke", "Open to anyone",
                "Anyone who finds it can run it, and you pay for every call.", "open to anyone"),
    FindingKind("provisioned", r"provisioned concurrency", "Paying for capacity nobody uses",
                "Provisioned concurrency is billed every hour, whether it's used or not.", "idle capacity", "waste"),
    FindingKind("runtime", r"end of support|lost support", "Runtimes AWS no longer patches",
                "No security fixes, and later Lambda refuses to update these functions at all.", "unsupported runtime"),
    FindingKind("throttles", r"were throttled", "Calls turned away",
                "More calls arrived than it may run at once.", "throttled"),
    FindingKind("timeout", r"Its longest run took", "Runs close to their time limit",
                "A run that needs a little longer is cut off and fails.", "near its timeout"),
    FindingKind("dropped", r"are dropped: nothing keeps them", "Failed events thrown away",
                "Events that still fail after their retries are lost for good.", "drops failed events"),
    FindingKind("logs", r"^It logs ", "Logs cost more than the code",
                "CloudWatch charges for every GB a function logs.", "costly logs"),
    FindingKind("retention", r"keeps logs forever", "Logs kept forever",
                "Stored logs are billed every month and never go away.", "logs kept forever"),
    FindingKind("secret", r"looks? like (a )?secrets?", "Secrets in environment variables",
                "Anyone allowed lambda:GetFunction can read them.", "secret in settings"),
    FindingKind("unused", r"wasn't called", "Possibly unused",
                "Not called lately: it may be safe to remove.", "not called"),
    FindingKind("other", r"", "Other things worth knowing", "", ""),
)

KINDS_BY_KEY = {kind.key: kind for kind in FINDING_KINDS}

FLEET_GROUPS: tuple[tuple[str, str], ...] = (
    ("failing", "Failing"),
    ("waste", "Paying for nothing"),
    ("risk", "Needs a fix"),
    ("unused", "Not called"),
    ("healthy", "Healthy"),
)

FIRST_ISSUES = 3
RISING_FACTOR = 2
RISING_RATE = 0.01
_MOST_NAMED = 3
_BIGGEST_SHARE = 0.5

BILL_PARTS: tuple[tuple[str, str], ...] = (
    ("provisioned", "Provisioned concurrency (kept ready)"),
    ("logs", "Logs written"),
    ("log_storage", "Logs stored"),
    ("compute", "Run time (compute)"),
    ("requests", "Requests"),
    ("storage", "/tmp storage"),
)


def finding_kind(message: str) -> FindingKind:
    """
    The kind of a finding function_findings() wrote, by its wording ('other' when no kind matches).
    """
    for kind in FINDING_KINDS:
        if kind.pattern and re.search(kind.pattern, message):
            return kind
    return KINDS_BY_KEY["other"]


@dataclass
class FleetFinding:
    """
    One finding about a function, with its kind.
    """

    level: str
    message: str
    kind: FindingKind


@dataclass
class FleetRow:
    """
    One function as the functions() report shows it: its findings by kind, the group they put it in, what it costs
    by part, and its runtime's place in AWS's support schedule.
    """

    function: Function
    metrics: FunctionMetrics | None
    triggers: list[str]
    runtime: RuntimeStatus
    findings: list[FleetFinding]
    cost: dict[str, float]
    group: str = "healthy"

    @property
    def total_cost(self) -> float | None:
        """
        Its estimated monthly cost, None when no part of it is known.
        """
        return _total(self.cost) if self.cost else None

    @property
    def warnings(self) -> list[FleetFinding]:
        """
        Its findings at the warn level.
        """
        return [found for found in self.findings if found.level == "warn"]


@dataclass
class FailureTrend:
    """
    A function's share of failed calls over the whole window and over the last RECENT_DAYS days, and whether the
    recent share is high enough, and far enough above the whole, to call it rising.
    """

    rate: float
    recent_rate: float | None
    is_rising: bool


def failure_trend(metrics: FunctionMetrics | None, now: datetime) -> FailureTrend | None:
    """
    Its failed calls overall and lately, None when it wasn't called.
    """
    if metrics is None or not metrics.invocations:
        return None
    rate = metrics.errors / metrics.invocations
    recent = _recent(metrics, now)
    if not recent or not recent[0]:
        return FailureTrend(rate, None, False)
    recent_rate = recent[1] / recent[0]
    is_rising = recent_rate >= RISING_RATE and recent_rate >= RISING_FACTOR * rate
    return FailureTrend(rate, recent_rate, is_rising)


def fleet_group(findings: list[FleetFinding], metrics: FunctionMetrics | None) -> str:
    """
    What a function needs, from its warnings: 'failing' (a warning about failing calls), 'waste' (paying for
    capacity it doesn't use), 'risk' (any other warning), 'unused' (no warnings and no calls) or 'healthy'.
    """
    warned = {found.kind.group for found in findings if found.level == "warn"}
    for group in ("failing", "waste", "risk"):
        if group in warned:
            return group
    if metrics is not None and not metrics.invocations:
        return "unused"
    return "healthy"


def _trigger_names(triggers: list) -> list[str]:
    """
    Each kind of trigger once, in the order they were found ('SQS', 'S3').
    """
    return list(dict.fromkeys(trigger.short for trigger in triggers))


def fleet_rows(overview: Overview, *, prices: dict[str, float], now: datetime) -> list[FleetRow]:
    """
    Every function in an overview as a FleetRow, the ones that need you first: by group, then the most warnings,
    then the highest cost, then by name.
    """
    rows = []
    for fn in overview.functions:
        detail = overview.detail(fn)
        found = function_findings(fn, detail.metrics, detail, prices=prices, now=now)
        findings = [FleetFinding(level, message, finding_kind(message)) for level, message in found]
        cost = function_monthly_cost(fn, detail.metrics, detail.provisioned, detail.log_group, prices=prices)
        known_cost = {part: amount for part, amount in cost.items() if amount is not None}
        rows.append(FleetRow(
            function=fn,
            metrics=detail.metrics,
            triggers=_trigger_names(detail.triggers),
            runtime=runtime_status(fn.runtime, package_type=fn.package_type, today=now.date()),
            findings=findings,
            cost=known_cost,
            group=fleet_group(findings, detail.metrics),
        ))
    return sorted(rows, key=_row_order)


_GROUP_ORDER = {key: place for place, (key, _) in enumerate(FLEET_GROUPS)}


def _row_order(row: FleetRow) -> tuple:
    return (_GROUP_ORDER[row.group], -len(row.warnings), -(row.total_cost or 0.0), row.function.name.lower(),
            row.function.region)


def row_label(row: FleetRow, *, with_region: bool) -> str:
    """
    A function's name, with its region when the report covers several.
    """
    fn = row.function
    return f"{fn.name} ({fn.region})" if with_region else fn.name


@dataclass
class FleetIssue:
    """
    One kind of problem across the functions that have it: `hits` pairs who has it (a function's label, or a
    region for the account's own findings) with its finding.
    """

    kind: FindingKind
    level: str
    hits: list[tuple[str, str]] = field(default_factory=list)
    cost: float = 0.0

    @property
    def who(self) -> list[str]:
        """
        The labels of what has it, once each.
        """
        return list(dict.fromkeys(label for label, _ in self.hits))


_KIND_ORDER = {kind.key: place for place, kind in enumerate(FINDING_KINDS)}
_KIND_COST = {"provisioned": "provisioned", "logs": "logs", "retention": "log_storage"}


def fleet_issues(rows: list[FleetRow], *, with_region: bool,
                 account: list[tuple[str, str, str]] | None = None) -> list[FleetIssue]:
    """
    Every finding grouped by kind and level, the most urgent first: warnings before notes, then by FINDING_KINDS'
    order. `account` adds the regions' own findings as (region, level, message). An issue carries the monthly cost
    of the part it's about (provisioned concurrency, logs) when it has one.
    """
    issues: dict[tuple[str, str], FleetIssue] = {}
    for row in rows:
        label = row_label(row, with_region=with_region)
        for found in row.findings:
            issue = issues.setdefault((found.kind.key, found.level), FleetIssue(found.kind, found.level))
            issue.hits.append((label, found.message))
            issue.cost += row.cost.get(_KIND_COST.get(found.kind.key, ""), 0.0)
    for region, level, message in account or []:
        issue = issues.setdefault(("account", level), FleetIssue(_ACCOUNT_KIND, level))
        issue.hits.append((region, message))
    return sorted(issues.values(), key=lambda issue: (issue.level != "warn", _KIND_ORDER.get(issue.kind.key, -1)))


_ACCOUNT_KIND = FindingKind("account", "", "The account's limits", "Limits shared by every function in the region.",
                            "")


def first_issues(issues: list[FleetIssue], *, count: int = FIRST_ISSUES) -> tuple[list[FleetIssue], list[FleetIssue]]:
    """
    The warnings to act on first and everything else. A warning whose functions an earlier one of the same group
    already named is left for later ('calls failing' after 'started failing after a change' for the same function),
    so the first few are different jobs.
    """
    first: list[FleetIssue] = []
    named: dict[str, set[str]] = {}
    for issue in issues:
        if len(first) == count or issue.level != "warn":
            break
        seen = named.setdefault(issue.kind.group, set())
        if set(issue.who) <= seen:
            continue
        first.append(issue)
        seen.update(issue.who)
    rest = [issue for issue in issues if issue not in first]
    return first, rest


def _names(labels: list[str]) -> str:
    """
    'a, b and c', or the first few and 'N more'.
    """
    shown = labels[:_MOST_NAMED]
    rest = len(labels) - len(shown)
    if rest:
        return f"{', '.join(shown)} and {rest} more"
    if len(shown) == 1:
        return shown[0]
    return f"{', '.join(shown[:-1])} and {shown[-1]}"


def _issue_clause(issue: FleetIssue) -> str:
    """
    One issue as part of the opening sentence: 'started failing after a change: orders-etl'.
    """
    money = f" ({human_money(issue.cost)}/month)" if issue.cost >= 0.01 else ""
    title = issue.kind.title
    return f"{title[:1].lower()}{title[1:]}: {_names(issue.who)}{money}"


def fleet_verdict(rows: list[FleetRow], issues: list[FleetIssue]) -> Verdict:
    """
    The report's opening: how many functions need you, then the first few warnings in a sentence; 'bad' when a
    function is failing, 'warn' for other warnings, 'ok' when none.
    """
    needing = [row for row in rows if row.group in ("failing", "waste", "risk")]
    if not needing:
        unused = sum(row.group == "unused" for row in rows)
        rest = f", and {_plural(unused, 'function')} wasn't called" if unused else ""
        headline = f"All {_plural(len(rows), 'function')} look healthy{rest}." if len(rows) > 1 else (
            "It looks healthy." if not unused else "It wasn't called lately.")
        return Verdict(headline, "No warnings: nothing here needs you now.", "ok")
    warned, _ = first_issues(issues)
    detail = "; ".join(_issue_clause(issue) for issue in warned)
    tone = "bad" if any(row.group == "failing" for row in rows) else "warn"
    verb = "needs" if len(needing) == 1 else "need"
    headline = f"{len(needing):,} of {_plural(len(rows), 'function')} {verb} you."
    return Verdict(headline, f"{detail[:1].upper()}{detail[1:]}.", tone)


@dataclass
class BillPart:
    """
    One part of the monthly bill across every function: what it is, how much, and the functions that pay most of it.
    """

    key: str
    label: str
    amount: float
    payers: list[str]


def bill_parts(rows: list[FleetRow], *, with_region: bool) -> list[BillPart]:
    """
    The estimated monthly bill by part (BILL_PARTS), the largest first, each with who pays it, the most first.
    Parts that cost nothing are left out.
    """
    parts = []
    labels = dict(BILL_PARTS)
    for key, label in labels.items():
        paying = [(row.cost.get(key, 0.0), row_label(row, with_region=with_region)) for row in rows]
        paying = sorted((amount, name) for amount, name in paying if amount > 0)
        amount = sum(amount for amount, _ in paying)
        if amount >= 0.005:
            parts.append(BillPart(key, label, amount, [name for _, name in reversed(paying)]))
    return sorted(parts, key=lambda part: part.amount, reverse=True)


def bill_note(parts: list[BillPart]) -> str:
    """
    What the bill's split says: the part that's most of it and who pays it, or that it's spread out.
    """
    total = sum(part.amount for part in parts)
    if not parts or total <= 0:
        return ""
    biggest = parts[0]
    share = biggest.amount / total
    if share < _BIGGEST_SHARE:
        return f"{biggest.label} is the largest part, {share:.0%} of it."
    payers = biggest.payers
    who = f"all from {payers[0]}" if len(payers) == 1 else f"most from {payers[0]}"
    return f"{biggest.label} is {share:.0%} of it, {who}."
