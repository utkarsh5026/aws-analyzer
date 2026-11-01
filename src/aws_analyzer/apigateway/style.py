"""
The look of API Gateway reports: their CSS root class, stylesheet (the shared rules, then the route path's and the
time split's) and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.render import _base_css, _Look, _report_html


_ROOT = "apg"

_EXTRA_CSS = """\
.apg .pp{display:flex;flex-wrap:wrap;align-items:stretch;gap:0;margin:4px 0 12px;max-width:1100px}
.apg .pp em{font-style:normal;align-self:center;padding:0 8px;opacity:.55;font-size:16px}
.apg .pb{display:flex;flex-direction:column;min-width:120px;max-width:260px;padding:7px 11px;border:1px solid rgba(127,127,127,.32);border-radius:8px;background:rgba(127,127,127,.05)}
.apg .pb i{font-style:normal;font-size:10px;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.apg .pb b{font-weight:650;overflow-wrap:anywhere}
.apg .pb span{font-size:11.5px;opacity:.75;overflow-wrap:anywhere}
.apg .pb.bad{border-color:#ef4444;background:rgba(239,68,68,.08)}
.apg .pb.bad span{color:#dc2626;opacity:1}
.apg .pb.warn{border-color:#f59e0b;background:rgba(245,158,11,.08)}
.apg .ts{max-width:900px;margin:4px 0 12px;font-size:12px}
.apg .tsr{display:grid;grid-template-columns:110px 1fr 80px;gap:10px;align-items:center;padding:2px 0}
.apg .tsl{position:relative;height:14px}
.apg .tsl::before{content:"";position:absolute;left:0;right:0;top:7px;border-top:1px dotted rgba(127,127,127,.35)}
.apg .tsb{position:absolute;top:1px;height:12px;border-radius:3px;background:#60a5fa}
.apg .tsb.bad{background:#ef4444}
.apg .tsb.warn{background:#f59e0b}
.apg .tsm{text-align:right;font-variant-numeric:tabular-nums}
.apg .tsm.bad{color:#dc2626;font-weight:650}
.apg .tsn{margin-top:4px;opacity:.7}
body[data-jp-theme-light="false"] .apg .pb.bad span,body[data-jp-theme-light="false"] .apg .tsm.bad{color:#f87171}
"""

_CSS = _base_css(_ROOT, _EXTRA_CSS)

_BADGE = "🚪 API Gateway"

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look.
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
