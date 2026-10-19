"""
What a lifecycle rule would do to a prefix's objects, and what it would save or cost.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from ..._kit.fmt import Stat, _utcnow

from ..helpers import parse_s3_uri, s3_uri
from ..prices import MIN_STORAGE_DAYS, MIN_TRANSITION_SIZE, S3_PRICES, S3_TRANSITION_PRICES, TRANSITION_ORDER
from ..models import LifecycleImpact, ObjectInfo
from .cost import object_monthly_cost


def _lifecycle_steps(
    move_after: int | dict[int, str] | None, to: str | None
) -> list[tuple[int, str]]:
    """
    Normalize move_after / to into [(days, class)] and apply the checks S3 does on a rule.
    """
    if move_after is None:
        if to is not None:
            raise ValueError("to= needs move_after= (days)")
        return []
    if isinstance(move_after, dict):
        if to is not None:
            raise ValueError(
                "Pass either move_after=days with to=class, or move_after={days: class}"
            )
        steps = sorted(
            (int(days), str(cls).upper()) for days, cls in move_after.items()
        )
    elif to is None:
        raise ValueError("move_after= needs to=, e.g. to='STANDARD_IA'")
    else:
        steps = [(int(move_after), to.upper())]
    targets = ", ".join(cls for cls, rank in TRANSITION_ORDER.items() if rank)
    last_days, last_rank, last_cls = 0, 0, ""
    for days, cls in steps:
        rank = TRANSITION_ORDER.get(cls)
        if not rank:
            raise ValueError(
                f"Lifecycle rules can't move objects to {cls!r}; use one of {targets}"
            )
        if days < 0:
            raise ValueError("Days can't be negative")
        if cls in ("STANDARD_IA", "ONEZONE_IA") and days < 30:
            raise ValueError(
                f"S3 only moves objects to {cls} once they are at least 30 days old"
            )
        if rank <= last_rank:
            raise ValueError(
                f"Each move must go to a colder class: {cls} comes after {last_cls}"
            )
        if last_cls in ("STANDARD_IA", "ONEZONE_IA") and days < last_days + 30:
            raise ValueError(
                f"S3 keeps objects in {last_cls} for at least 30 days before moving them again"
            )
        last_days, last_rank, last_cls = days, rank, cls
    return steps


def simulate_lifecycle_objects(
    objects: Iterable[ObjectInfo],
    uri: str = "",
    *,
    move_after: int | dict[int, str] | None = None,
    to: str | None = None,
    delete_after: int | None = None,
    prices: dict[str, float] | None = None,
    transition_prices: dict[str, float] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> LifecycleImpact:
    """
    What a lifecycle rule would do to `objects` if it ran today, and what it would save.

    move_after    days since last modified, with to='STANDARD_IA' / 'GLACIER_IR' / 'GLACIER' / ...,
                  or several moves at once: {30: 'STANDARD_IA', 180: 'GLACIER'}
    delete_after  days since last modified before the object is deleted (expired)

    Follows S3's rules: objects under 128 KB are not moved, objects only move to colder classes,
    and removing an object before its class's minimum storage duration is billed for the rest of it.
    """
    steps = _lifecycle_steps(move_after, to)
    if delete_after is None and not steps:
        raise ValueError(
            "Nothing to simulate: pass move_after= with to=, and/or delete_after="
        )
    if delete_after is not None and (
        delete_after < 1 or (steps and delete_after <= steps[-1][0])
    ):
        raise ValueError(
            "delete_after must be at least 1 day and later than the last move"
        )
    prices = S3_PRICES if prices is None else prices
    transition_prices = (
        S3_TRANSITION_PRICES if transition_prices is None else transition_prices
    )
    now = now or _utcnow()
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    impact = LifecycleImpact(
        uri=s3_uri(bucket, prefix) if bucket else uri,
        transitions=steps,
        expire_days=delete_after,
    )
    moves: dict[str, Stat] = defaultdict(Stat)

    for i, obj in enumerate(objects):
        if limit is not None and i >= limit:
            impact.truncated = True
            break
        if obj.is_folder_marker:
            continue
        size, cls = obj.size, obj.storage_class
        age_days = (now - obj.last_modified).total_seconds() / 86400
        before = after = object_monthly_cost(size, cls, prices) or 0.0
        target = next(
            (step_cls for days, step_cls in reversed(steps) if age_days >= days), None
        )
        removed = False
        impact.scanned.add(size)
        if delete_after is not None and age_days >= delete_after:
            impact.expired.add(size)
            after, removed = 0.0, True
        elif (
            target and TRANSITION_ORDER.get(cls, 99) < TRANSITION_ORDER[target]
        ):  # unknown classes never move
            if size < MIN_TRANSITION_SIZE:
                impact.too_small.add(size)
            else:
                moves[target].add(size)
                after, removed = object_monthly_cost(size, target, prices) or 0.0, True
                impact.one_time_cost += transition_prices.get(target, 0.0) / 1000
        min_days = MIN_STORAGE_DAYS.get(cls, 0)
        if removed and age_days < min_days:
            impact.early_removals.add(size)
            impact.one_time_cost += before * (min_days - age_days) / 30
        impact.cost_before += before
        impact.cost_after += after

    impact.moves = dict(moves)
    return impact
