"""
What several analysis modules share: a day in words, a sync's failure reasons as text, and the
metadata file example findings show.
"""

from __future__ import annotations

from datetime import datetime

from ..._kit.fmt import _in_zone
from ..._kit.text import _clip


def _fmt_day(moment: datetime | None) -> str:
    return (
        "-" if moment is None else _in_zone(moment).strftime("%Y-%m-%d")
    )


def _reasons_text(reasons: list[str], limit: int = 2) -> str:
    return (
        "; ".join(_clip(" ".join(r.split()).rstrip("."), 200) for r in reasons[:limit])
        or "no reason given"
    )


_METADATA_EXAMPLE = '{"metadataAttributes": {"team": "billing", "year": 2024}}'
