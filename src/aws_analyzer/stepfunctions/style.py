"""
The look of Step Functions reports: their CSS root class, stylesheet (the shared rules, then the waterfall's and the
execution log's) and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.render import _base_css, _Look, _report_html


_ROOT = "sfn"

_EXTRA_CSS = """\
.sfn .wf{max-width:1100px;margin:4px 0 12px;font-size:12px}
.sfn .wfr{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:10px;align-items:center;padding:2px 0;border-top:1px solid rgba(127,127,127,.14)}
.sfn .wfr.wfh{border-top:0;opacity:.6;font-size:11px}
.sfn .wfn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.sfn .wfn span{font-weight:400;opacity:.6;margin-left:6px;font-size:11px}
.sfn .wfr.d1 .wfn{padding-left:14px;font-weight:500}
.sfn .wfr.d2 .wfn,.sfn .wfr.d3 .wfn{padding-left:28px;font-weight:500}
.sfn .wft{display:flex;justify-content:space-between}
.sfn .wfl{position:relative;height:18px}
.sfn .wfl::before{content:"";position:absolute;left:0;right:0;top:9px;border-top:1px dotted rgba(127,127,127,.35)}
.sfn .wfs{position:absolute;top:2px;height:14px;border-radius:3px;font-style:normal;font-size:10px;line-height:14px;color:#fff;padding-left:4px;white-space:nowrap;overflow:hidden;box-sizing:border-box}
.sfn .wfs.ok{background:#10b981}
.sfn .wfs.failed{background:#ef4444}
.sfn .wfs.retry{background:#f59e0b}
.sfn .wfs.wait{background:repeating-linear-gradient(45deg,rgba(245,158,11,.55) 0 3px,transparent 3px 6px)}
.sfn .wfs.running{background:repeating-linear-gradient(90deg,rgba(59,130,246,.75) 0 6px,rgba(59,130,246,.35) 6px 12px)}
.sfn .wfs.sdk{background:#8b5cf6}
.sfn .wfs.choice{background:rgba(127,127,127,.55)}
.sfn .wfk{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-size:11px;opacity:.75}
.sfn .wfk span{display:inline-flex;align-items:center;gap:5px}
.sfn .wfk .wfs{position:static;display:inline-block;width:14px;height:9px;padding:0}
.sfn .story{max-width:1100px;margin:4px 0 12px;border:1px solid rgba(127,127,127,.25);border-radius:8px;overflow:hidden;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px}
.sfn .story .sh{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:6px 10px;border-bottom:1px solid rgba(127,127,127,.2)}
.sfn .story .sz{margin-left:auto;opacity:.6}
.sfn .story .sl{display:grid;grid-template-columns:92px minmax(90px,140px) 1fr;gap:10px;padding:2px 10px;align-items:baseline}
.sfn .story .sl.error{background:rgba(239,68,68,.09);color:#dc2626}
.sfn .story .sl.report{opacity:.7}
.sfn .story .sc{opacity:.65;white-space:nowrap}
.sfn .story .sx{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
.sfn .story .sn{padding:6px 10px;border-top:1px dashed rgba(127,127,127,.25);font-family:inherit;opacity:.75}
.sfn .src{display:inline-block;justify-self:start;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-radius:4px;padding:0 6px;font-size:10.5px;background:rgba(127,127,127,.14)}
.sfn .src.sf{background:rgba(219,39,119,.12);color:#be185d}
.sfn .src.l0{background:rgba(234,88,12,.13);color:#c2410c}
.sfn .src.l1{background:rgba(37,99,235,.12);color:#1d4ed8}
.sfn .src.l2{background:rgba(5,150,105,.13);color:#047857}
.sfn .src.l3{background:rgba(124,58,237,.12);color:#6d28d9}
.sfn .src.l4{background:rgba(2,132,199,.13);color:#0369a1}
.sfn .src.l5{background:rgba(161,98,7,.13);color:#a16207}
body[data-jp-theme-light="false"] .sfn .story .sl.error{color:#f87171}
"""

_CSS = _base_css(_ROOT, _EXTRA_CSS)

_BADGE = "🔀 Step Functions"

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look.
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
