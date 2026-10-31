"""
What changed in a function since your last look, from what the explorer already read (no AWS call): its code and
settings against the snapshot noted then, its failed calls since against the days before, and the error causes its
Errors tab hadn't seen. A snapshot keeps the code's hash, the settings and a hash of each cause, never a log line.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..._kit.fmt import _fmt_dt, _plural, human_age

from ..helpers import _midnight
from ..models import ErrorGroup, Function, FunctionMetrics

_ERRORS_RISE = 2.0
_ERRORS_WORTH_SAYING = 3
_CAUSE_KEY_CHARS = 16


@dataclass(frozen=True)
class FunctionLook:
    """
    A function as you last saw it: its code (CodeSha256) and when it was deployed, the settings that change how it
    runs, and the error causes its Errors tab showed (hashes; None when the tab wasn't opened).
    """

    code_sha: str
    deployed: str
    runtime: str
    handler: str
    memory: int
    timeout: int
    error_causes: list[str] | None = None

    def to_data(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if self.error_causes is None:
            del data["error_causes"]
        return data

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> FunctionLook | None:
        """
        A look read back from a snapshot, or None when the snapshot isn't one (another version wrote it).
        """
        try:
            causes = data.get("error_causes")
            return cls(str(data["code_sha"]), str(data["deployed"]), str(data["runtime"]), str(data["handler"]),
                       int(data["memory"]), int(data["timeout"]), list(causes) if causes is not None else None)
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class FunctionChanges:
    """
    A function since your last look (`since`): whether its code changed and when it was deployed, its settings that
    changed ('memory 512 → 1,024 MB'), and its failed calls a day from `counted_from` (the UTC day after your last
    look, since CloudWatch counts whole UTC days) against the days before your last look's (None without its numbers,
    or before a whole day has passed).
    """

    since: datetime
    code_changed: bool = False
    deployed: datetime | None = None
    settings: list[str] = field(default_factory=list)
    counted_from: datetime | None = None
    errors_since: float | None = None
    errors_a_day: float | None = None
    errors_a_day_before: float | None = None

    @property
    def errors_rose(self) -> bool:
        if self.errors_since is None or self.errors_since < _ERRORS_WORTH_SAYING:
            return False
        before = self.errors_a_day_before or 0.0
        return (self.errors_a_day or 0.0) >= _ERRORS_RISE * before

    @property
    def has_news(self) -> bool:
        return self.code_changed or bool(self.settings) or self.errors_rose


def function_look(fn: Function, *, error_causes: list[str] | None = None) -> FunctionLook:
    """
    The look of a function from its configuration (and its Errors tab's causes, when they were read).
    """
    deployed = fn.last_modified.isoformat() if fn.last_modified else ""
    return FunctionLook(fn.raw.get("CodeSha256") or "", deployed, fn.runtime or "container image", fn.handler or "",
                        fn.memory, fn.timeout, error_causes)


def cause_key(group: ErrorGroup) -> str:
    """
    A short hash of an error's cause (its kind and blanked message), so a snapshot can tell causes apart without
    keeping what the log said.
    """
    text = f"{group.kind}\n{group.message}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_CAUSE_KEY_CHARS]


def _changed_settings(before: FunctionLook, now: FunctionLook) -> list[str]:
    settings = []
    if before.runtime != now.runtime:
        settings.append(f"runtime {before.runtime} → {now.runtime}")
    if before.handler != now.handler:
        settings.append(f"handler {before.handler} → {now.handler}")
    if before.memory != now.memory:
        settings.append(f"memory {before.memory:,} → {now.memory:,} MB")
    if before.timeout != now.timeout:
        settings.append(f"timeout {before.timeout:,} → {now.timeout:,} s")
    return settings


def _errors_around(metrics: FunctionMetrics, since: datetime,
                   now: datetime) -> tuple[datetime, float, float, float | None] | None:
    """
    The failed calls from the UTC day after `since` on (that day, their count, a day on average), and a day on
    average in the window's days before the day of `since`; None until that day has begun.
    """
    visit_day = _midnight(since)
    first_day = visit_day + timedelta(days=1)
    if _midnight(now) < first_day:
        return None
    days_since = (_midnight(now) - first_day).days + 1
    errors_since = sum(day.errors for day in metrics.daily if day.start >= first_day)
    days_before = max(0, round(metrics.days) - days_since - 1)
    errors_before = sum(day.errors for day in metrics.daily if day.start < visit_day)
    a_day_before = errors_before / days_before if days_before else None
    return first_day, errors_since, errors_since / days_since, a_day_before


def function_changes(fn: Function, *, before: FunctionLook, since: datetime, now: datetime,
                     metrics: FunctionMetrics | None = None) -> FunctionChanges:
    """
    What changed in `fn` since your last look at `since`: code, settings, and failed calls when metrics are there.
    """
    look = function_look(fn)
    code_changed = bool(before.code_sha and look.code_sha) and before.code_sha != look.code_sha
    changes = FunctionChanges(since, code_changed, fn.last_modified if code_changed else None,
                              _changed_settings(before, look))
    around = _errors_around(metrics, since, now) if metrics is not None else None
    if around is None:
        return changes
    counted_from, errors_since, a_day, a_day_before = around
    return dataclasses.replace(changes, counted_from=counted_from, errors_since=errors_since, errors_a_day=a_day,
                               errors_a_day_before=a_day_before)


def _errors_finding(changes: FunctionChanges, when: str) -> tuple[str, str]:
    count = _plural(round(changes.errors_since or 0), "failed call")
    day = changes.counted_from or changes.since
    since_day = f"since {day:%d %b} (UTC days), about {changes.errors_a_day or 0:,.0f} a day"
    if not changes.errors_a_day_before:
        return ("warn", f"It started failing since your last look {when}: {count} {since_day}, none in the days "
                        "before. The Errors tab groups them by cause.")
    return ("warn", f"Errors went up since your last look {when}: {count} {since_day}, against about "
                    f"{changes.errors_a_day_before:,.1f} a day before. The Errors tab groups them by cause.")


def change_findings(changes: FunctionChanges) -> list[tuple[str, str]]:
    """
    What changed since your last look, as findings: failed calls going up first, then the code and settings; one
    'nothing changed' line when nothing did.
    """
    when = human_age(changes.since)
    findings = []
    if changes.errors_rose:
        findings.append(_errors_finding(changes, when))
    if changes.code_changed:
        deployed = f": deployed {_fmt_dt(changes.deployed, zone=True)}" if changes.deployed else ""
        findings.append(("info", f"The code changed since your last look {when}{deployed}. The Code tab shows what's "
                                 "in it now."))
    if changes.settings:
        findings.append(("info", f"Its settings changed since your last look {when}: {'; '.join(changes.settings)}."))
    if not findings:
        errors = " and no more failed calls than before" if changes.errors_since is not None else ""
        findings.append(("info", f"Nothing changed since your last look {when}: the same code and settings{errors}."))
    return findings


def new_error_causes(groups: list[ErrorGroup], seen: list[str]) -> list[ErrorGroup]:
    """
    The error causes not among those seen on your last look (cause_key), most frequent first.
    """
    known = set(seen)
    return [group for group in groups if cause_key(group) not in known]


def new_causes_finding(new: list[ErrorGroup], since: datetime) -> tuple[str, str]:
    """
    The Errors tab's line about causes it hadn't seen on your last look.
    """
    shown = "; ".join(f"{group.kind} ({_plural(group.count, 'time')}, first {human_age(group.first)})"
                      for group in new[:3])
    more = f", and {len(new) - 3:,} more" if len(new) > 3 else ""
    what = "A new error" if len(new) == 1 else f"{len(new):,} new errors"
    return ("warn", f"{what} since your last look {human_age(since)}: {shown}{more}. They're in the table below.")
