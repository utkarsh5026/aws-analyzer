"""What the notebook windows share: the S3 explorer, the chat, and the knowledge base and Lambda explorers.

The windows are ipywidgets laid out and styled by CSS, with no JavaScript. ipywidgets and IPython load only inside the
functions that need them (the window itself loads ipywidgets with `_require`), so this module imports with boto3 alone.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from typing import Any, Callable

from .blocks import _Findings, _Next, _Note, _Table, _Title
from .text import _esc


def _css_height(height: int | str | None) -> str | None:
    """height= as CSS: a number is pixels (720, '720'), other text is CSS as written ('80vh'); None is None."""
    text = "" if height is None else str(height).strip()
    return f"{text}px" if re.fullmatch(r"\d+(\.\d+)?", text) else text or None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The kernel's event loop, which runs the window's clicks in a notebook; None elsewhere (a script, the tests),
    where the window's work runs right away while the click waits."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _cell_number() -> Any:
    """The running cell's execution count in IPython (None elsewhere): a cell that ends with a window (chat(),
    explore()) shows it once, not twice."""
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return None
    shell = get_ipython()
    return getattr(shell, "execution_count", None) if shell is not None else None


def _class_if(widget: Any, name: str, on: bool) -> None:
    """Adds or removes a widget's CSS class, sending nothing when it's already as wanted."""
    if on and name not in widget._dom_classes:
        widget.add_class(name)
    elif not on and name in widget._dom_classes:
        widget.remove_class(name)


def _skeleton(text: str) -> str:
    """A page still loading: what's being read, with a spinner, over grey bars where the report will be."""
    return (f'<div class="skw"><div class="skl"><span class="spin"></span>{_esc(text)}</div><div class="sk t"></div>'
            '<div class="skc"><div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div>'
            '</div><div class="sk"></div><div class="sk" style="width:86%"></div><div class="sk" style="width:64%">'
            "</div></div>")


def _for_window(blocks: list[Any], words: Callable[[str], str]) -> list[Any]:
    """A report's blocks for an explorer window: no Next block (its calls are for a cell), and the commands its
    sentences name changed into the tabs that show the same (words: the window's _window_text)."""
    out = []
    for block in blocks:
        if isinstance(block, _Next):
            continue
        if isinstance(block, _Findings):
            block = dataclasses.replace(block, items=[(level, words(m)) for level, m in block.items],
                                        empty=words(block.empty))
        elif isinstance(block, _Note):
            block = dataclasses.replace(block, text=words(block.text))
        elif isinstance(block, _Title):
            block = dataclasses.replace(block, sub=words(block.sub))
        elif isinstance(block, _Table):
            rows = [[words(cell) if j in block.prose_cols and isinstance(cell, str) else cell
                     for j, cell in enumerate(row)] for row in block.rows]
            block = dataclasses.replace(block, rows=rows, title=words(block.title))
        out.append(block)
    return out
