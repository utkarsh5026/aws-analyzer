"""
What needs attention in one service: the page runs the service's own overview (Lambda's functions(), S3's
overview(), DynamoDB's tables(), ...) when its page opens, the same report a notebook draws, and reads its answer
out of the blocks it drew: the verdict, the cards, and every finding as a `Concern` (what's wrong, the rest of the
sentence, the resources it names, how bad), worst first. Only the service opened is read; the runner keeps what it
found for ATTENTION_KEPT, so Home's "Checked so far" and the quick look reuse it without asking AWS again.

The blocks are the Views' own (_Findings, a "Warnings" _Table, Lambda's _Todo, S3's _Fixes), so a finding a
service adds shows here with no change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from .._kit.blocks import _Cards, _Findings, _Note, _Table, _Tone, _Verdict
from .._kit.render import _split_lead

ATTENTION_KEPT = 900
_LEVEL_ORDER = {"bad": 0, "warn": 1, "info": 2}
_WARNINGS_TITLE = "warnings"
_SHORTEST_NAME = 3
_ACCOUNT_KIND = "account"


@dataclass(frozen=True)
class Check:
    """
    How a service is checked: the command whose report says what needs attention, its arguments, and the command
    and parameter that open one of the resources a finding names (a bucket's bucket_info(bucket=...)).
    """

    command: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    opens: tuple[str, str] = ("", "")


CHECKS: dict[str, Check] = {
    "s3": Check("overview", opens=("bucket_info", "bucket")),
    "lambda_functions": Check("functions", opens=("function_info", "name")),
    "dynamodb": Check("tables", opens=("table_info", "table")),
    "stepfunctions": Check("overview", opens=("state_machine", "name")),
    "apigateway": Check("apis", opens=("api_info", "name")),
    "messaging": Check("overview"),
    "athena": Check("overview"),
    "bedrock_kb": Check("kbs", opens=("kb_info", "name")),
    "bedrock_chat": Check("kbs"),
    "opensearch": Check("overview", opens=("indexes", "name")),
    "sagemaker_env": Check("running"),
}


@dataclass(frozen=True)
class Target:
    """
    A resource a concern names, and the command (with its arguments, as form texts) that opens it.
    """

    name: str
    command: str = ""
    arguments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Concern:
    """
    One thing that needs attention: how bad ('bad', 'warn' or 'info'), what's wrong (the sentence's lead), the
    rest (why it matters, what to do), and the resources it's about.
    """

    level: str
    headline: str
    detail: str
    targets: list[Target] = field(default_factory=list)


@dataclass(frozen=True)
class Attention:
    """
    What one check of a service found: the call it ran, the report's verdict and cards, the concerns worst first,
    a note when the report couldn't be read (a missing permission), when, how long it took, and whether it came
    from what the server kept.
    """

    service: str
    call: str
    command: str
    verdict: str
    tone: str
    cards: list[list[str]]
    concerns: list[Concern]
    note: str
    checked_at: datetime
    seconds: float
    from_memory: bool = False


def check_for(key: str) -> Check | None:
    """
    How a service is checked, or None when it has no overview to check yet.
    """
    return CHECKS.get(key)


def _concern(level: str, message: str, names: Iterable[str]) -> Concern:
    lead, _, rest = _split_lead(message)
    targets = [Target(name) for name in dict.fromkeys(names) if name]
    if not lead:
        return Concern(level, message, "", targets)
    return Concern(level, lead, rest, targets)


def _cell_text(cell: Any) -> str:
    return cell.text if isinstance(cell, _Tone) else str(cell)


def _is_warnings_table(block: _Table) -> bool:
    return block.title.lower().startswith(_WARNINGS_TITLE) and len(block.headers) >= 2


def _table_concerns(block: _Table) -> list[Concern]:
    """
    A "Warnings" table's rows, (resource, warning): DynamoDB's, OpenSearch's and the knowledge bases' overviews.
    """
    return [_concern("warn", _cell_text(row[-1]), [_cell_text(row[0])]) for row in block.rows if len(row) >= 2]


def _without_region(label: str) -> str:
    """
    'orders-etl (eu-west-1)' -> 'orders-etl': a function's label in a report that covers several regions.
    """
    return re.sub(r"\s+\([a-z]{2}(-[a-z]+)+-\d\)$", "", label)


def _todo_concerns(block: Any) -> list[Concern]:
    """
    Lambda's problems to fix: one concern per kind, its heading and why, with each function that has it.
    """
    found = []
    for issue in [*block.first, *block.rest]:
        is_about_the_account = issue.kind.key == _ACCOUNT_KIND
        names = [] if is_about_the_account else [_without_region(label) for label in issue.who]
        detail = issue.kind.why or (issue.hits[0][1] if issue.hits else "")
        found.append(Concern(issue.level, issue.kind.title, detail, [Target(name) for name in names]))
    return found


def _fixes_concerns(block: Any) -> list[Concern]:
    """
    S3's fixes: each change that clears warnings, why, and the buckets it covers.
    """
    return [Concern(fix.severity if fix.severity in _LEVEL_ORDER else "warn", fix.title, fix.why,
                    [Target(bucket) for bucket in fix.buckets]) for fix in block.fixes]


def _service_readers() -> dict[type, Callable[[Any], list[Concern]]]:
    """
    The blocks a service draws its findings with, other than the shared ones. Imported here, when a check runs,
    so the server loads a service only once its page is opened.
    """
    from ..lambda_functions.blocks.fleet import _Todo
    from ..s3.blocks import _Fixes

    return {_Todo: _todo_concerns, _Fixes: _fixes_concerns}


def _block_concerns(block: Any, readers: dict[type, Callable[[Any], list[Concern]]]) -> list[Concern]:
    if isinstance(block, _Findings):
        return [_concern(level, message, []) for level, message in block.items]
    if isinstance(block, _Table) and _is_warnings_table(block):
        return _table_concerns(block)
    reader = readers.get(type(block))
    return reader(block) if reader else []


def _mentioned(message: str, names: Iterable[str]) -> list[str]:
    """
    The names a sentence mentions, as whole words ('orders' isn't found in 'orders-etl').
    """
    found = []
    for name in names:
        if len(name) < _SHORTEST_NAME:
            continue
        if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", message):
            found.append(name)
    return found


def _with_names(concern: Concern, names: list[str]) -> Concern:
    """
    A concern that named no resource, with the ones its sentence mentions out of the service's names.
    """
    if concern.targets or not names:
        return concern
    mentioned = _mentioned(f"{concern.headline} {concern.detail}", names)
    return Concern(concern.level, concern.headline, concern.detail, [Target(name) for name in mentioned])


def _same_merged(concerns: list[Concern]) -> list[Concern]:
    """
    The same finding for several resources as one concern naming them all, worst first, in the report's order.
    """
    merged: dict[tuple[str, str, str], Concern] = {}
    for concern in concerns:
        key = (concern.level, concern.headline, concern.detail)
        if key in merged:
            earlier = merged[key]
            merged[key] = Concern(earlier.level, earlier.headline, earlier.detail, [*earlier.targets, *concern.targets])
        else:
            merged[key] = concern
    return sorted(merged.values(), key=lambda concern: _LEVEL_ORDER.get(concern.level, len(_LEVEL_ORDER)))


def concerns_of(reports: list[list[Any]], names: list[str] | None = None) -> list[Concern]:
    """
    Every finding in a check's reports as Concerns, worst first, the same finding for several resources merged.
    `names` (the service's resources, when the search has read them) lets a sentence that names one point at it.
    """
    readers = _service_readers()
    found = [concern for blocks in reports for block in blocks for concern in _block_concerns(block, readers)]
    return _same_merged([_with_names(concern, names or []) for concern in found])


def verdict_of(reports: list[list[Any]]) -> tuple[str, str]:
    """
    The report's answer in a sentence and its tone, when it has one.
    """
    for blocks in reports:
        for block in blocks:
            if isinstance(block, _Verdict):
                return block.headline, block.tone
    return "", ""


def cards_of(reports: list[list[Any]]) -> list[list[str]]:
    """
    The report's cards as [label, value, tone]: the few numbers it leads with.
    """
    cards = []
    for blocks in reports:
        for block in blocks:
            if isinstance(block, _Cards):
                cards += [[str(part) for part in (*card, "")[:3]] for card in block.items]
    return cards


def note_of(reports: list[list[Any]]) -> str:
    """
    When the check couldn't read the service (only notes came back: a missing permission, a wrong region), what
    the first warning note says; else ''.
    """
    blocks = [block for report in reports for block in report]
    if any(not isinstance(block, _Note) for block in blocks):
        return ""
    warnings = [block.text for block in blocks if isinstance(block, _Note) and block.level == "warn"]
    notes = warnings or [block.text for block in blocks if isinstance(block, _Note)]
    return notes[0] if notes else ""


def opened(concerns: list[Concern], check: Check, kept: dict[str, Any]) -> list[Concern]:
    """
    The concerns with each resource's opening command: the one its search entry names when the search has read it
    (a queue's queue_info, a topic's topic_info), else the service's own (`check.opens`), else none.
    """
    command, parameter = check.opens
    result = []
    for concern in concerns:
        targets = []
        for target in concern.targets:
            resource = kept.get(target.name)
            if resource is not None:
                targets.append(Target(target.name, resource.command, dict(resource.arguments)))
            elif command:
                targets.append(Target(target.name, command, {parameter: target.name}))
            else:
                targets.append(target)
        result.append(Concern(concern.level, concern.headline, concern.detail, targets))
    return result
