"""
_Fates (what happened to a function's failed events, a card for each way they went), with its drawings, and the
blocks errors() shows for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..._kit.blocks import _Note, _Report, _draws
from ..._kit.errors import _why
from ..._kit.render import _prose
from ..._kit.text import _esc

from ..models import ErrorReport, EventFate
from ..analysis.fates import AT_RISK, FATE_TONES, KEPT, LOST, SAFE


_TITLE = "What happened to the failed events"

_SECTION_PERMISSIONS = {
    "triggers": ("what reads queues for it", "lambda:ListEventSourceMappings"),
    "policy": ("who may call it", "lambda:GetPolicy"),
    "async": ("its asynchronous settings", "lambda:GetFunctionEventInvokeConfig"),
    "async_metrics": ("CloudWatch's count of dropped events", "cloudwatch:GetMetricData"),
}

_WORRYING = {LOST, AT_RISK, KEPT}


@dataclass
class _Fates:
    """
    Where a function's failed events went: a card per fate (lost, kept, waiting, at risk, unknown), its number big,
    what it means, and where the number came from. A line per fate in text.
    """

    fates: list[EventFate] = field(default_factory=list)
    intro: str = ""
    title: str = _TITLE


def _count_text(fate: EventFate) -> str:
    return "-" if fate.count is None else f"{fate.count:,}"


@_draws(_Fates, "html")
def _fates_html(block: _Fates, report: _Report) -> str:
    cards = "".join(
        f'<div class="ef {FATE_TONES.get(fate.outcome, "")}"><span class="ef-o">{_esc(fate.outcome)}</span>'
        f'<b class="ef-n">{_esc(_count_text(fate))}</b><span class="ef-h">{_esc(fate.headline)}</span>'
        f'<span class="ef-s">{_prose(fate.says)}</span>'
        + (f'<span class="ef-b">{_esc(fate.basis)}</span>' if fate.basis else "") + "</div>"
        for fate in block.fates
    )
    intro = f'<p class="ef-in">{_prose(block.intro)}</p>' if block.intro else ""
    return f'<h4>{_prose(block.title)}</h4>{intro}<div class="efs">{cards}</div>'


@_draws(_Fates, "text")
def _fates_lines(block: _Fates, report: _Report) -> list[str]:
    lines = ["", f"-- {block.title} --"]
    if block.intro:
        lines.append(block.intro)
    for fate in block.fates:
        mark = " (!)" if fate.outcome in (LOST, AT_RISK) else ""
        basis = f" [{fate.basis}]" if fate.basis else ""
        lines.append(f"  {fate.outcome.upper():<8} {fate.headline}: {fate.says}{basis}{mark}")
    return lines


def _intro(report: ErrorReport) -> str:
    """
    The sentence over the cards: how many calls failed, and that the last few minutes' may still be tried again.
    """
    failed = report.metrics.errors if report.metrics is not None else 0
    lead = f"{round(failed):,} calls failed. " if failed else ""
    return (f"{lead}Where their events went depends on what called the function; events that failed in the last "
            "few minutes may still be tried again.")


def lost_events(report: ErrorReport) -> int:
    """
    How many failed events nothing kept, as far as the report can count.
    """
    return sum(fate.count or 0 for fate in report.fates if fate.outcome == LOST)


def _unread_notes(report: ErrorReport) -> list[Any]:
    """
    A note for each part of the fates that couldn't be read, with the permission it needs.
    """
    notes = []
    for section, code in report.fate_errors.items():
        what, permission = _SECTION_PERMISSIONS.get(section, (section, ""))
        reason = _why(code, permission) if permission else code
        notes.append(_Note(f"Couldn't read {what} ({reason}), so what happened to some failed events isn't known.",
                           "warn"))
    return notes


def _fates_blocks(report: ErrorReport) -> list[Any]:
    """
    errors()' panel on what happened to the failed events, shown when calls failed or a queue holds failed events;
    nothing when every fate is safe and nothing failed.
    """
    failed = report.metrics is not None and report.metrics.errors > 0
    worrying = any(fate.outcome in _WORRYING for fate in report.fates)
    if not (failed or worrying):
        return []
    shown = [fate for fate in report.fates if fate.outcome != SAFE or failed]
    blocks: list[Any] = []
    if shown:
        blocks.append(_Fates(shown, _intro(report)))
    return blocks + _unread_notes(report)
