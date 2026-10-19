"""
The look of DynamoDB reports: their CSS root class, stylesheet and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.render import _base_css, _report_html


_ROOT = "ddb"  # the class around each report's HTML: its stylesheet's rules are scoped to it

_CSS = _base_css(_ROOT)

_BADGE = "🗄️ DynamoDB"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws.
    """
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)
