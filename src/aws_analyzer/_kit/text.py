"""Escaping text for HTML, clipping and padding it for the text tables, and finding search words in it."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Iterable


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _clip(text: str, width: int = 90) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _width(text: str) -> int:
    """How many columns a terminal gives `text`, so text tables line up when a cell holds an emoji (📁) or CJK:
    2 for a wide character, 1 more for a symbol U+FE0F turns into an emoji (⚙️), 0 for combining marks and joiners."""
    width, wide = 0, False
    for ch in text:
        if ch == "️":
            width, wide = width + (not wide), True
        elif not unicodedata.combining(ch) and unicodedata.category(ch) not in ("Mn", "Me", "Cf"):
            wide = unicodedata.east_asian_width(ch) in ("W", "F")
            width += 2 if wide else 1
    return width


def _pad(text: str, width: int, right: bool = False) -> str:
    """ljust / rjust by the columns the text takes on screen (_width), not its length."""
    fill = " " * max(0, width - _width(text))
    return fill + text if right else text + fill


def _text_bar(fraction: float, width: int = 20) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled) + f" {fraction * 100:5.1f}%"


def search_rank(text: str, fields: Iterable[Any]) -> int | None:
    """Where something goes in a search for `text` among its fields (a name, an ID, a description...), best first:
    0 when a field is the text (an ID pasted whole), 1 when one starts with it, 2 when one holds it, 3 when every
    word of it is in some field; None when a word is in none. Case is ignored, so 'k7qj' finds 'K7QJ2M4XNA'; an
    empty search finds everything, at 3."""
    wanted = " ".join(str(text or "").lower().split())
    if not wanted:
        return 3
    values = [" ".join(str(f).lower().split()) for f in fields if f]
    if wanted in values:
        return 0
    if any(v.startswith(wanted) for v in values):
        return 1
    if any(wanted in v for v in values):
        return 2
    if all(any(word in v for v in values) for word in wanted.split()):
        return 3
    return None


def _marked(text: str, words: Iterable[str]) -> str:
    """HTML for `text` with every place a search word is found, in any case and inside longer words ('k7qj' in
    'K7QJ2M4XNA'), in <mark>. Each piece is escaped before it's wrapped."""
    unique = sorted({w for w in words if w}, key=len, reverse=True)
    if not unique:
        return _esc(text)
    regex = re.compile("(" + "|".join(re.escape(w) for w in unique) + ")", re.IGNORECASE)
    return "".join(f"<mark>{_esc(piece)}</mark>" if i % 2 else _esc(piece) for i, piece in enumerate(regex.split(text)))
