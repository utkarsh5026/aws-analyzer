"""
The look of SQS, SNS and EventBridge reports: their CSS root class, stylesheet (the shared rules: these reports use
only the shared blocks) and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.render import _base_css, _Look, _report_html


_ROOT = "msg"

_CSS = _base_css(_ROOT)

_BADGE = "📨 Messaging"

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look.
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
