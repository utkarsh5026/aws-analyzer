"""
The look of Step Functions reports: their CSS root class, stylesheet (the shared rules, then the waterfall's, the
execution log's, the graph's and the JSON trees') and badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.jsontree import _json_tree_css
from .._kit.render import _base_css, _Look, _report_html


_ROOT = "sfn"

_EXTRA_CSS = """\
.sfn .chg{max-width:900px;margin:4px 0 12px;font-size:11px}
.sfn .cht{position:relative;height:74px;border-bottom:1px solid rgba(127,127,127,.35);margin-top:4px}
.sfn .chl{position:absolute;top:0;bottom:0;border-left:2px dashed #8b5cf6}
.sfn .chl.sus{border-left-style:solid;border-left-color:#7c3aed}
.sfn .chl em{position:absolute;top:0;left:6px;white-space:nowrap;font-style:normal;color:#7c3aed;font-weight:600}
.sfn .chl.r em{left:auto;right:6px}
.sfn .chd{position:absolute;bottom:6px;width:9px;height:9px;margin-left:-4.5px;border-radius:50%;background:#ef4444;box-shadow:0 0 0 1.5px var(--jp-layout-color0,#fff)}
.sfn .cha{display:flex;justify-content:space-between;margin-top:3px;opacity:.65;font-variant-numeric:tabular-nums}
.sfn .chk{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px;opacity:.75}
.sfn .chk i{display:inline-block;width:9px;height:9px;margin-right:5px;vertical-align:-1px}
.sfn .chk i.l{width:0;border-left:2px dashed #8b5cf6;height:11px}
.sfn .chk i.d{border-radius:50%;background:#ef4444}
.sfn .dzs{max-width:720px;margin:4px 0 12px;font-size:11px}
.sfn .dzb{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:max(1px,min(6px,calc(240px / var(--n))));align-items:end;height:64px;border-bottom:1px solid rgba(127,127,127,.35)}
.sfn .dz{height:100%;display:flex;align-items:flex-end;border-radius:3px 3px 0 0}
.sfn .dz i{display:flex;flex-direction:column;justify-content:flex-end;width:100%;min-height:1px;background:rgba(127,127,127,.32);border-radius:2px 2px 0 0;overflow:hidden}
.sfn .dz b{display:block;min-height:2px;background:#ef4444}
.sfn .dz.jz{box-shadow:0 0 0 1.5px #f59e0b;background:rgba(245,158,11,.08)}
.sfn .dzl{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:max(1px,min(6px,calc(240px / var(--n))));margin-top:3px;opacity:.7;text-align:center;white-space:nowrap}
.sfn .dzl span{overflow:visible;font-variant-numeric:tabular-nums}
.sfn .dzl b{display:block;color:#dc2626;font-weight:600}
.sfn .dzk{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px;opacity:.75}
.sfn .dzk i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px;background:rgba(127,127,127,.32)}
.sfn .dzk i.f{background:#ef4444}
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
.sfn .story{container-type:inline-size;max-width:1100px;margin:4px 0 12px;border:1px solid rgba(127,127,127,.25);border-radius:8px;overflow:hidden;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px}
.sfn .story .sh{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:6px 10px;border-bottom:1px solid rgba(127,127,127,.2)}
.sfn .story .sz{margin-left:auto;opacity:.6}
.sfn .story .sl{display:grid;grid-template-columns:92px minmax(90px,140px) 1fr;gap:10px;padding:2px 10px;align-items:baseline}
.sfn .story .sl.error{background:rgba(239,68,68,.09);color:#dc2626}
.sfn .story .sl.report{opacity:.7}
.sfn .story .sc{opacity:.65;white-space:nowrap}
.sfn .story .sx{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
@container (max-width:480px){.sfn .story .sl{grid-template-columns:92px minmax(0,1fr);row-gap:0}.sfn .story .sx{grid-column:1/-1;padding:0 0 4px 12px}}
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
.sfn .fg{max-width:100%;overflow-x:auto;margin:4px 0 12px}
.sfn .fg svg{display:block;max-width:100%;height:auto;font-family:inherit}
.sfn .fgk{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:11.5px;margin:0 0 6px}
.sfn .fgk b{font-weight:650}
.sfn .fgk span{display:inline-flex;align-items:center;gap:5px;opacity:.8}
.sfn .fgk i{display:inline-block;width:12px;height:9px;border-radius:3px;border:1.5px solid rgba(127,127,127,.55)}
.sfn .fe path{fill:none;stroke:rgba(127,127,127,.55);stroke-width:1.6}
.sfn path.fe{fill:none;stroke:rgba(127,127,127,.55);stroke-width:1.6}
.sfn .fe path.fh,.sfn path.fh{fill:rgba(127,127,127,.65);stroke:none}
.sfn .fe.catch path:first-child{stroke-dasharray:5 4}
.sfn .fe.loop path:first-child{stroke-dasharray:2 3}
.sfn .fe.on path:first-child,.sfn path.fe.on{stroke:#10b981;stroke-width:2.4}
.sfn .fe.on path.fh{fill:#10b981}
.sfn .fe.catch.on path:first-child{stroke:#ef4444}
.sfn .fe.catch.on path.fh{fill:#ef4444}
.sfn .fl{font-size:10px;fill:currentColor;opacity:.6}
.sfn .fn rect{fill:var(--jp-layout-color0,#fff);stroke:rgba(127,127,127,.5);stroke-width:1.3}
.sfn .fn text{fill:currentColor}
.sfn .fnm{font-size:12.5px;font-weight:650}
.sfn .fns{font-size:10.5px;opacity:.65}
.sfn .fni{font-size:10.5px;opacity:.75}
.sfn .fn.t-choice rect{stroke-dasharray:0}
.sfn .fn.t-fail rect{stroke:rgba(239,68,68,.55)}
.sfn .fn.m-ok rect{fill:rgba(16,185,129,.12);stroke:#10b981}
.sfn .fn.m-failed rect{fill:rgba(239,68,68,.12);stroke:#ef4444}
.sfn .fn.m-running rect{fill:rgba(59,130,246,.12);stroke:#3b82f6}
.sfn .fn.m-skip rect{stroke-dasharray:4 3;fill:transparent}
.sfn .fn.m-skip text{opacity:.5}
.sfn .fn.m-h1 rect{fill:rgba(245,158,11,.10);stroke:#f59e0b}
.sfn .fn.m-h2 rect{fill:rgba(249,115,22,.16);stroke:#f97316}
.sfn .fn.m-h3 rect{fill:rgba(239,68,68,.18);stroke:#ef4444}
.sfn .fgk i.m-ok{background:rgba(16,185,129,.12);border-color:#10b981}
.sfn .fgk i.m-failed{background:rgba(239,68,68,.12);border-color:#ef4444}
.sfn .fgk i.m-running{background:rgba(59,130,246,.12);border-color:#3b82f6}
.sfn .fgk i.m-skip{border-style:dashed}
.sfn .fgk i.m-h1{background:rgba(245,158,11,.10);border-color:#f59e0b}
.sfn .fgk i.m-h2{background:rgba(249,115,22,.16);border-color:#f97316}
.sfn .fgk i.m-h3{background:rgba(239,68,68,.18);border-color:#ef4444}
.sfn .fn.m-t1 rect{fill:rgba(217,119,6,.06);stroke:rgba(217,119,6,.55)}
.sfn .fn.m-t2 rect{fill:rgba(217,119,6,.18);stroke:#d97706}
.sfn .fn.m-t3 rect{fill:rgba(217,119,6,.34);stroke:#b45309;stroke-width:2}
.sfn .fgk i.m-t1{background:rgba(217,119,6,.06);border-color:rgba(217,119,6,.55)}
.sfn .fgk i.m-t2{background:rgba(217,119,6,.18);border-color:#d97706}
.sfn .fgk i.m-t3{background:rgba(217,119,6,.34);border-color:#b45309}
.sfn .fn.sel rect{stroke:#2563eb;stroke-width:3}
.sfn .fstart{fill:currentColor;opacity:.75}
.sfn .fend{fill:none;stroke:currentColor;stroke-width:2;opacity:.75}
"""

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _json_tree_css(_ROOT))

_BADGE = "🔀 Step Functions"

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look.
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
