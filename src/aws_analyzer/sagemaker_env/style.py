"""
The look of SageMaker reports: their CSS root class, stylesheet (the shared rules, then the pipeline graph's and
waterfall's) and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.render import _base_css, _Look, _report_html


_ROOT = "smk"  # the class around each report's HTML: its stylesheet's rules are scoped to it

_EXTRA_CSS = """\
.smk .pg{max-width:100%;overflow-x:auto;margin:4px 0 12px}
.smk .pg svg{display:block;max-width:100%;height:auto;font-family:inherit}
.smk .pgk{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:11.5px;margin:0 0 6px}
.smk .pgk span{display:inline-flex;align-items:center;gap:5px;opacity:.8}
.smk .pgk i{display:inline-block;width:12px;height:9px;border-radius:3px;border:1.5px solid rgba(127,127,127,.55)}
.smk .pe path{fill:none;stroke:rgba(127,127,127,.55);stroke-width:1.6}
.smk .pe path.ph{fill:rgba(127,127,127,.65);stroke:none}
.smk .pl{font-size:10px;fill:currentColor;opacity:.6}
.smk .pn rect{fill:var(--jp-layout-color0,#fff);stroke:rgba(127,127,127,.5);stroke-width:1.3}
.smk .pn text{fill:currentColor}
.smk .pnm{font-size:12.5px;font-weight:650}
.smk .pns{font-size:10.5px;opacity:.65}
.smk .pn.s-succeeded rect{fill:rgba(16,185,129,.12);stroke:#10b981}
.smk .pn.s-failed rect{fill:rgba(239,68,68,.12);stroke:#ef4444;stroke-width:2.2}
.smk .pn.s-executing rect,.smk .pn.s-starting rect{fill:rgba(59,130,246,.12);stroke:#3b82f6}
.smk .pn.s-stopped rect,.smk .pn.s-stopping rect{fill:rgba(245,158,11,.12);stroke:#f59e0b}
.smk .pn.s-cached rect{fill:rgba(16,185,129,.06);stroke:#10b981;stroke-dasharray:5 3}
.smk .pn.s-none rect{stroke-dasharray:4 3;fill:transparent}
.smk .pn.s-none text{opacity:.5}
.smk .pgk i.s-succeeded{background:rgba(16,185,129,.12);border-color:#10b981}
.smk .pgk i.s-failed{background:rgba(239,68,68,.12);border-color:#ef4444}
.smk .pgk i.s-executing,.smk .pgk i.s-starting{background:rgba(59,130,246,.12);border-color:#3b82f6}
.smk .pgk i.s-stopped,.smk .pgk i.s-stopping{background:rgba(245,158,11,.12);border-color:#f59e0b}
.smk .pgk i.s-cached{border-color:#10b981;border-style:dashed}
.smk .pgk i.s-none{border-style:dashed}
.smk .pw{max-width:1100px;margin:4px 0 12px;font-size:12px}
.smk .pwr{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:10px;align-items:center;padding:2px 0;border-top:1px solid rgba(127,127,127,.14)}
.smk .pwr.pwh{border-top:0;opacity:.6;font-size:11px}
.smk .pwn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.smk .pwn span{font-weight:400;opacity:.6;margin-left:6px;font-size:11px}
.smk .pwt{display:flex;justify-content:space-between}
.smk .pwl{position:relative;height:18px}
.smk .pwl::before{content:"";position:absolute;left:0;right:0;top:9px;border-top:1px dotted rgba(127,127,127,.35)}
.smk .pws{position:absolute;top:2px;height:14px;border-radius:3px;font-style:normal;font-size:10px;line-height:14px;color:#fff;padding-left:4px;white-space:nowrap;overflow:hidden;box-sizing:border-box;background:rgba(127,127,127,.55)}
.smk .pws.s-succeeded{background:#10b981}
.smk .pws.s-failed{background:#ef4444}
.smk .pws.s-executing,.smk .pws.s-starting{background:repeating-linear-gradient(90deg,rgba(59,130,246,.75) 0 6px,rgba(59,130,246,.35) 6px 12px)}
.smk .pws.s-stopped,.smk .pws.s-stopping{background:#f59e0b}
.smk .pws.s-cached{background:rgba(16,185,129,.35);color:inherit}
"""

_CSS = _base_css(_ROOT, _EXTRA_CSS)

_BADGE = "🧪 SageMaker"  # the chip before each report's title, so reports from different analyzers are easy to tell apart

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look (the windows' reports).
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
