"""
How progress shows while a long command runs: a tqdm bar, or a line with the count, rate and time left.

_BaseView._progress (view.py) shows them. tqdm, and ipywidgets for its notebook bar, load only when a bar is needed;
without them (or with a tqdm too old to import cleanly) the plain line takes over without a word.
"""

from __future__ import annotations

import importlib
from typing import Any

from .fmt import _duration, human_size


def _progress_bar_class(notebook: bool) -> Any:
    """
    tqdm's widget bar in a notebook (it needs ipywidgets) or its text bar elsewhere; None without tqdm.
    """
    try:
        if notebook:
            importlib.import_module("ipywidgets")
            return importlib.import_module("tqdm.notebook").tqdm
        return importlib.import_module("tqdm").tqdm
    except Exception:  # not installed, or too old to import cleanly: the plain progress line takes over
        return None


def _progress_bar(bar_class: Any, label: str, unit: str, total: int | None) -> Any:
    """
    A tqdm bar that shows up after half a second and disappears when closed. unit='B' counts bytes.
    """
    options: dict[str, Any] = {"desc": label, "total": total, "leave": False, "delay": 0.5, "mininterval": 0.25,
                               "dynamic_ncols": True, "disable": False, "unit_scale": True}
    if unit == "B":
        options.update(unit="B", unit_divisor=1024)
    else:
        known = total is not None
        counts = "{percentage:3.0f}%|{bar}| {n:,}/{total:,}" if known else "{n:,}"
        timing = "{elapsed}<{remaining}, {rate_fmt}" if known else "{elapsed}, {rate_fmt}"
        options.update(unit=f" {unit}", bar_format=f"{{desc}}: {counts} {unit} [{timing}]")
    return bar_class(**options)


def _progress_text(label: str, unit: str, count: int, total: int | None, elapsed: float) -> str:
    """
    The progress line shown without tqdm: 'Reading... 1.2 GB of 3.0 GB (40%) · 12s · 98.0 MB/s · about 18s left'.
    """
    amount = human_size if unit == "B" else (lambda n: f"{n:,}")
    text = f"{label}... {amount(count)}"
    if total:
        text += f" of {amount(total)}"
    text += "" if unit == "B" else f" {unit}"
    if total:
        text += f" ({min(count / total, 1):.0%})"
    text += f" · {_duration(elapsed)}"
    if elapsed >= 1 and count:
        rate = count / elapsed
        if unit == "B":
            text += f" · {human_size(rate)}/s"
        else:
            text += f" · {rate:,.0f}/s" if rate >= 10 else f" · {rate:.1f}/s"
        if total and total > count:
            text += f" · about {_duration((total - count) / rate)} left"
    return text
