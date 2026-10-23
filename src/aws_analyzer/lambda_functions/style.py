"""
The look of Lambda reports: their CSS root class, stylesheet (with the service's own blocks' rules) and
badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.jsontree import _json_tree_css
from .._kit.render import _base_css, _Look, _page_document, _report_html


_ROOT = "lmb"  # the class around each report's HTML: its stylesheet's rules are scoped to it

_CODE_FONT = '"Cascadia Mono","Cascadia Code",ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace'

_EXTRA_CSS = """\
.lmb{--lmb-mono:%CODE_FONT%}
.lmb.lmb.lmb :is(code,pre,kbd,samp,.jt,td.tree){font-family:var(--lmb-mono)}
.lmb .wire{display:grid;grid-template-columns:minmax(0,1fr) 26px minmax(0,.9fr) 26px minmax(0,1fr);align-items:center;margin:6px 0 10px;max-width:980px}
.lmb .wire .wc{display:flex;flex-direction:column;gap:6px;min-width:0}
.lmb .wire .wa{text-align:center;font-size:18px;opacity:.45}
.lmb .wire .wn{border:1px solid rgba(127,127,127,.3);border-radius:8px;padding:5px 10px;line-height:1.35;min-width:0}
.lmb .wire .wn.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.lmb .wire .wn.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.lmb .wire .wn.none{border-style:dashed;opacity:.7}
.lmb .wire .wk{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.6}
.lmb .wire .wn b{display:block;font-weight:600;overflow-wrap:anywhere}
.lmb .wire .wd{display:block;font-size:11.5px;opacity:.7;overflow-wrap:anywhere}
.lmb .wire .wf{border:1px solid rgba(234,88,12,.55);border-radius:10px;padding:10px 12px;background:rgba(234,88,12,.06);text-align:center}
.lmb .wire .wf .wl{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;margin-bottom:4px;color:#fff;font-weight:700;background:linear-gradient(135deg,#f97316,#ea580c)}
.lmb .wire .wf b{display:block;font-size:14px;overflow-wrap:anywhere}
.lmb .colbars{display:flex;align-items:flex-end;gap:2px;height:120px;padding:0 0 0 2px;position:relative;border-bottom:1px solid rgba(127,127,127,.35);max-width:980px}
.lmb .colbars i{flex:1 1 0;min-width:2px;max-width:34px;border-radius:2px 2px 0 0;background:rgba(59,130,246,.55);position:relative;display:flex;flex-direction:column;justify-content:flex-start}
.lmb .colbars i:hover{background:rgba(59,130,246,.8)}
.lmb .colbars i b{display:block;width:100%;border-radius:2px 2px 0 0;background:#ef4444}
.lmb .colbars.dim i{background:rgba(59,130,246,.22);justify-content:flex-end}
.lmb .colbars.dim i b{background:rgba(59,130,246,.75);border-radius:0}
.lmb .colbars .lim{position:absolute;left:0;right:0;border-top:1.5px dashed rgba(239,68,68,.7);pointer-events:none}
.lmb .colbars .lim span{position:absolute;right:0;top:-16px;font-size:10.5px;color:#ef4444;opacity:.85}
.lmb .colbars .top{position:absolute;left:4px;top:-2px;font-size:10.5px;opacity:.55;pointer-events:none}
.lmb .colx{display:flex;justify-content:space-between;font-size:10.5px;opacity:.55;margin:3px 0 10px;max-width:980px}
.lmb .pgw{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;margin:8px 0 10px}
.lmb a.pgo{display:inline-block;padding:5px 16px;border:1px solid rgba(59,130,246,.5);border-radius:999px;color:#3b82f6;font-weight:600;font-size:13.5px;text-decoration:none}
.lmb a.pgo:hover{background:rgba(59,130,246,.1)}
.lmb .pgp{font-size:12px;opacity:.65;overflow-wrap:anywhere}
.lmb .flow{border:1px solid rgba(127,127,127,.25);border-radius:8px;padding:6px 10px 6px 4px;margin:4px 0 10px;max-width:1180px;max-height:760px;overflow:auto;font-size:12px}
.lmb .flow .ol{display:flex;align-items:baseline;flex-wrap:wrap;column-gap:8px;row-gap:2px;padding:1px 4px;border-radius:5px;line-height:1.55;min-width:0}
.lmb .flow .ol:hover{background:rgba(127,127,127,.08)}
.lmb :is(.flow,.wk) .ln{flex:0 0 30px;text-align:right;font:11px var(--lmb-mono);opacity:.45;user-select:none;-webkit-user-select:none}
.lmb .flow code.ocd{font:12px var(--lmb-mono);white-space:pre-wrap;overflow-wrap:anywhere;background:none;padding:0;min-width:0}
.lmb .flow .k-def>code.ocd{font-weight:600}
.lmb .flow .k-return>code.ocd,.lmb .flow .k-raise>code.ocd{font-weight:600}
.lmb .flow .ox{margin-left:38px;padding-left:10px;border-left:1px solid rgba(127,127,127,.28)}
.lmb :is(.flow,.wk) .nt{display:inline-block;font-size:10.5px;font-weight:600;line-height:1.6;padding:0 7px;border-radius:9px;white-space:nowrap;background:rgba(127,127,127,.14)}
.lmb :is(.flow,.wk) .nt i{font-style:normal;font-weight:500;opacity:.7;margin-left:4px}
.lmb :is(.flow,.wk) .n-aws-reads{background:rgba(59,130,246,.16);color:#2f6fdb}
.lmb :is(.flow,.wk) .n-aws-writes{background:rgba(234,88,12,.16);color:#d0570c}
.lmb :is(.flow,.wk) .n-aws-sends{background:rgba(219,39,119,.14);color:#c42a73}
.lmb :is(.flow,.wk) .n-aws-runs{background:rgba(147,51,234,.15);color:#8b3fd9}
.lmb :is(.flow,.wk) .n-aws-signs{background:rgba(100,116,139,.18)}
.lmb :is(.flow,.wk) .n-env{background:rgba(13,148,136,.16);color:#0f8a7e}
.lmb :is(.flow,.wk) .n-event{background:rgba(22,163,74,.15);color:#178a45}
.lmb :is(.flow,.wk) .n-http{background:rgba(219,39,119,.14);color:#c42a73}
.lmb :is(.flow,.wk) .n-client{background:rgba(59,130,246,.08);color:#2f6fdb}
.lmb :is(.flow,.wk) .n-warn,.lmb :is(.flow,.wk) .n-sleep{background:rgba(245,158,11,.2);color:#b86e04}
.lmb :is(.flow,.wk) .n-route{background:rgba(147,51,234,.15);color:#8b3fd9}
.lmb :is(.flow,.wk) .n-log,.lmb :is(.flow,.wk) .n-file,.lmb :is(.flow,.wk) .n-command,.lmb :is(.flow,.wk) .n-more{font-weight:500;opacity:.85}
.lmb .flow details.od{margin:0}
.lmb .flow details.od>summary{list-style:none;cursor:pointer}
.lmb .flow details.od>summary::-webkit-details-marker{display:none}
.lmb .flow .on{white-space:nowrap}
.lmb .flow .on b{font:600 12px var(--lmb-mono)}
.lmb .flow .och{display:inline-block;width:6px;height:6px;margin:0 7px 2px 1px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(-45deg);opacity:.6;transition:transform .15s}
.lmb .flow details.od[open]>summary .och{transform:rotate(45deg);margin-bottom:4px}
.lmb .flow details.od[open]>summary .os{display:none}
.lmb .flow .oseen .on{padding-left:15px}
.lmb :is(.flow,.wk) :is(.of,.oq){font-size:11px;opacity:.6}
.lmb :is(.flow,.wk) .oq{font-style:italic}
.lmb .flow .os{display:inline-flex;flex-wrap:wrap;gap:3px 5px}
.lmb .flow .ob{margin-left:22px}
.lmb :is(.flow,.wk) .pk{color:var(--jp-mirror-editor-keyword-color,light-dark(#7c3aed,#c792ea))}
.lmb :is(.flow,.wk) .pf{color:var(--jp-mirror-editor-def-color,light-dark(#2563eb,#82aaff))}
.lmb :is(.flow,.wk) .js,.lmb :is(.flow,.wk) .jk{color:var(--jp-mirror-editor-string-color,light-dark(#047857,#a5d6a7))}
.lmb :is(.flow,.wk) .jn,.lmb :is(.flow,.wk) .jl{color:var(--jp-mirror-editor-number-color,light-dark(#c2410c,#f78c6c))}
.lmb :is(.flow,.wk) .pa{font-style:italic}
.lmb :is(.flow,.wk) .pc{opacity:.6;font-style:italic}
.lmb .wk{container-type:inline-size;max-width:1180px;margin:2px 0 10px}
.lmb .wk .wtip{font-size:12px;opacity:.62;margin:0 0 8px}
.lmb .wk .wg{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,1fr);gap:12px;align-items:start}
@container (max-width:720px){.lmb .wk .wg{grid-template-columns:minmax(0,1fr)}.lmb .wk .wps{order:-1}}
.lmb .wk .wtree{border:1px solid rgba(127,127,127,.25);border-radius:10px;padding:4px 0;max-height:560px;overflow:auto;font-size:12px;scroll-padding:48px 0}
.lmb .wk .wsec{position:sticky;top:-4px;z-index:1;padding:6px 12px 4px;font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.75;background:var(--jp-layout-color0,var(--vscode-editor-background,Canvas))}
.lmb .wk .wl{position:relative;display:flex;align-items:baseline;gap:8px;padding:1px 10px 1px 0;line-height:1.65;white-space:nowrap;cursor:pointer;border-left:3px solid transparent;transition:background-color .1s}
.lmb .wk .wl:hover{background:rgba(127,127,127,.09)}
.lmb .wk .wl:has(>.wr:checked){background:rgba(59,130,246,.15);border-left-color:#3b82f6}
.lmb .wk .wl:has(>.wr:focus-visible){outline:1.5px solid rgba(59,130,246,.7);outline-offset:-1.5px}
.lmb .wk .wr{display:block;position:absolute;left:0;top:0;width:1px;height:100%;margin:0;opacity:0;pointer-events:none}
.lmb .wk .wl .ln{flex:0 0 30px}
.lmb .wk .wt{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;padding-left:calc(var(--d,0) * 16px);background-image:repeating-linear-gradient(90deg,rgba(127,127,127,.2) 0 1px,transparent 1px 16px);background-size:calc(var(--d,0) * 16px) 100%;background-repeat:no-repeat;background-position:4px 0}
.lmb .wk .wt code.ocd{white-space:pre}
.lmb .wk .wt b{font:600 12px var(--lmb-mono)}
.lmb .wk .k-def>.wt code.ocd,.lmb .wk .k-return>.wt code.ocd,.lmb .wk .k-raise>.wt code.ocd{font-weight:600}
.lmb .wk .wx{flex:0 1 auto;display:inline-flex;gap:4px;min-width:0;max-width:45%;overflow:hidden}
.lmb .wk .wx .nt{font-size:10px;line-height:1.5;padding:0 6px}
.lmb .wk .wps{position:sticky;top:8px;min-width:0}
.lmb .wk .wp{display:none;border:1px solid rgba(127,127,127,.28);border-radius:12px;padding:12px 14px 14px;background:rgba(127,127,127,.04);box-shadow:0 1px 2px rgba(0,0,0,.05),0 8px 22px rgba(15,23,42,.06)}
.lmb .wk .wh{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.lmb .wk .wn{font-size:11px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.7}
.lmb .wk .wf{font-size:11.5px;opacity:.6;overflow-wrap:anywhere}
.lmb .wk .wbar{height:3px;border-radius:2px;background:rgba(127,127,127,.18);margin:6px 0 10px;overflow:hidden}
.lmb .wk .wbar i{display:block;height:100%;background:#3b82f6;border-radius:2px}
.lmb .wk .wcr{font-size:12px;margin:0 0 4px;overflow-wrap:anywhere}
.lmb .wk .wcr b{font:600 12px var(--lmb-mono)}
.lmb .wk .wc0{opacity:.6;font-style:italic}
.lmb .wk .wa{font-size:12px;opacity:.85;margin:0 0 4px;overflow-wrap:anywhere}
.lmb .wk label.wc{cursor:pointer;color:#3b82f6;font:500 12px var(--lmb-mono);border-radius:4px}
.lmb .wk label.wc:hover{text-decoration:underline}
.lmb .wk .wa label.wc code.ocd{color:inherit}
.lmb .wk .wcode{margin:8px 0 10px;padding:8px 10px;border-radius:8px;background:rgba(127,127,127,.09);border-left:3px solid #3b82f6}
.lmb .wk .wcode code.ocd{font:12.5px var(--lmb-mono);white-space:pre-wrap;overflow-wrap:anywhere;background:none;padding:0}
.lmb .wk code.ocd{background:none;padding:0;user-select:text;-webkit-user-select:text;cursor:inherit}
.lmb .wk ul.wd{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px;font-size:12.5px}
.lmb .wk ul.wd li{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;line-height:1.45}
.lmb .wk ul.wd li>span:not(.nt){opacity:.72}
.lmb .wk ul.wd li.wm{opacity:.9}
.lmb .wk .wb{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.lmb .wk .wbt{display:inline-block;padding:5px 12px;border:1px solid rgba(127,127,127,.35);border-radius:8px;font-size:12.5px;font-weight:600;cursor:pointer;user-select:none;-webkit-user-select:none;transition:background-color .1s,border-color .1s}
.lmb .wk label.wbt:hover{background:rgba(127,127,127,.1);border-color:rgba(127,127,127,.6)}
.lmb .wk .wbt.go{background:#3b82f6;border-color:#3b82f6;color:#fff}
.lmb .wk label.wbt.go:hover{background:#2563eb;border-color:#2563eb}
.lmb .wk .wbt.off{opacity:.32;cursor:default}
.lmb .cgb{width:fit-content;max-width:min(100%,1180px)}
.lmb .cg{border:1px solid rgba(127,127,127,.2);border-radius:14px;margin:6px 0 8px;overflow:auto;background-color:rgba(127,127,127,.035);background-attachment:local;background-size:18px 18px;
 background-image:radial-gradient(circle at 1px 1px,rgba(127,127,127,.3) 1px,transparent 0);background-position:9px 9px}
.lmb .cg svg{display:block;max-width:100%;height:auto;overflow:visible;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.lmb .cg .cap{font-size:12px;font-weight:650;letter-spacing:1.2px;fill:currentColor;fill-opacity:.6}
.lmb .cg .capl{stroke:currentColor;stroke-opacity:.14;stroke-width:1;fill:none}
.lmb .cg .cn,.lmb .cg .ce{transition:opacity .15s}
.lmb .cg .cn .bg{fill:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke:none;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.07)) drop-shadow(0 6px 16px rgba(15,23,42,.07))}
.lmb .cg .cn .fg{fill:rgba(255,255,255,.05);stroke:rgba(127,127,127,.32);stroke-width:1}
.lmb .cg .cn:hover .fg{stroke:rgba(127,127,127,.6)}
.lmb .cg .cn text{fill:currentColor}
.lmb .cg .cn .lb{font-size:14px;font-weight:650;letter-spacing:-.1px}
.lmb .cg .k-handler .lb,.lmb .cg .k-function .lb{font-family:var(--lmb-mono);font-size:13.5px;font-weight:650;letter-spacing:0}
.lmb .cg .k-handler .lb{font-size:14px;font-weight:700}
.lmb .cg .cn .sb{font-size:12.5px;fill-opacity:.62}
.lmb .cg .cn .sb .dir{fill-opacity:.4}
.lmb .cg .cn .sb .ac{font-weight:600;fill-opacity:.95}
.lmb .cg .cn .sb .sep{fill-opacity:.4}
.lmb .cg .cn .dv{stroke:rgba(127,127,127,.2);stroke-width:1;fill:none}
.lmb .cg .cn .rw{font:12.5px var(--lmb-mono);fill-opacity:.88}
.lmb .cg .cn .rw.mo{font-family:inherit;font-style:italic;fill-opacity:.55}
.lmb .cg .cn .pr{font-size:12.5px;fill-opacity:.7}
.lmb .cg .cn .ns{font-size:12px;font-weight:650;fill:light-dark(#b45309,#fbbf24);letter-spacing:.2px}
.lmb .cg .cn .bu{fill:var(--c,rgba(127,127,127,.6))}
.lmb .cg .cn .bdg{fill:var(--t,rgba(127,127,127,.12));stroke:var(--c,rgba(127,127,127,.5));stroke-opacity:.22;stroke-width:1}
.lmb .cg .cn .ico{fill:none;stroke:var(--c,currentColor);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.lmb .cg .k-event{--c:#16a34a;--t:rgba(22,163,74,.12)}
.lmb .cg .k-env{--c:#0d9488;--t:rgba(13,148,136,.13)}
.lmb .cg .k-aws{--c:#3b82f6;--t:rgba(59,130,246,.12)}
.lmb .cg .k-http{--c:#db2777;--t:rgba(219,39,119,.11)}
.lmb .cg .k-start{--c:#64748b;--t:rgba(100,116,139,.13)}
.lmb .cg .k-function{--c:currentColor;--t:rgba(127,127,127,.12)}
.lmb .cg .k-function .ico{stroke-opacity:.72}
.lmb .cg .k-aws .bu,.lmb .cg .k-http .bu{fill:rgba(127,127,127,.55)}
.lmb .cg .k-start .fg{stroke-dasharray:5 4;stroke:rgba(127,127,127,.55);fill:rgba(127,127,127,.04)}
.lmb .cg .k-start .bg{filter:none}
.lmb .cg .k-handler .bg{filter:drop-shadow(0 1px 2px rgba(0,0,0,.1)) drop-shadow(0 10px 24px rgba(15,23,42,.12))}
.lmb .cg .k-handler .fg{stroke:currentColor;stroke-opacity:.6;stroke-width:1.5}
.lmb .cg .k-handler .bdg{fill:currentColor;stroke:none}
.lmb .cg .k-handler .ico{stroke:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke-width:1.7}
.lmb .cg .cn.warn .fg{stroke:#f59e0b;stroke-opacity:1;stroke-width:1.5;fill:rgba(245,158,11,.06)}
.lmb .cg .wb circle,.lmb .cg .wm circle{fill:#f59e0b;stroke:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke-width:1.5}
.lmb .cg .wb path,.lmb .cg .wm path{stroke:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke-width:2;stroke-linecap:round;fill:none}
.lmb .cg .rt rect{fill:rgba(127,127,127,.1);stroke:rgba(127,127,127,.22)}
.lmb .cg .rt text{font:12.5px var(--lmb-mono);fill-opacity:.85}
.lmb .cg .rt .mt{font-weight:700;fill-opacity:1}
.lmb .cg .ce path{fill:none}
.lmb .cg .ce .ln{stroke:var(--c);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.lmb .cg .ce .ah{fill:var(--c);stroke:var(--c);stroke-width:.6;stroke-linejoin:round}
.lmb .cg .ce .hit{stroke:transparent;stroke-width:12}
.lmb .cg .ce .halo{stroke:#f59e0b;stroke-opacity:.75;stroke-width:7;stroke-linecap:round;stroke-linejoin:round}
.lmb .cg .ce .cs{stroke:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round}
.lmb .cg .ce:hover .ln{stroke-width:2.6}
.lmb .cg .ce.pt circle{fill:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke:var(--c);stroke-width:1.6}
.lmb .cg .ce .lpb circle{fill:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke:var(--c);stroke-width:1.3}
.lmb .cg .ce .lpb path{fill:none;stroke:var(--c);stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round}
.lmb .cg .ek-call,.lmb .cgk .ek-call,.lmb .cgk .ek-guess,.lmb .cgk .ek-via{--c:rgba(127,127,127,.95)}
.lmb .cg .ek-event{--c:#16a34a}
.lmb .cg .ek-event .ln{stroke-width:2.2}
.lmb .cg .ek-env,.lmb .cgk .ek-env{--c:#0d9488}
.lmb .cg .ek-env .ln,.lmb .cgk .ek-env .ln{stroke-dasharray:5 4}
.lmb .cg .ek-client,.lmb .cgk .ek-client{--c:rgba(100,116,139,.85)}
.lmb .cg .ek-client .ln,.lmb .cgk .ek-client .ln{stroke-dasharray:.5 4.5;stroke-width:2}
.lmb .cg .ek-reads,.lmb .cgk .ek-reads{--c:#3b82f6}
.lmb .cg .ek-writes,.lmb .cgk .ek-writes{--c:#ea580c}
.lmb .cg .ek-sends,.lmb .cg .ek-http,.lmb .cgk .ek-sends,.lmb .cgk .ek-http{--c:#db2777}
.lmb .cg .ek-runs,.lmb .cgk .ek-runs{--c:#9333ea}
.lmb .cg .ek-signs,.lmb .cgk .ek-signs{--c:rgba(100,116,139,.9)}
.lmb .cg .ek-signs .ln,.lmb .cgk .ek-signs .ln{stroke-dasharray:6 3}
.lmb .cg .ek-http .ln,.lmb .cgk .ek-http .ln{stroke-dasharray:8 3}
.lmb .cg .ce.guess .ln,.lmb .cgk .ek-guess .ln{stroke-dasharray:3 2.5;stroke-linecap:butt}
.lmb .cg .ce.via .ln,.lmb .cgk .ek-via .ln{stroke-dasharray:9 3 2 3}
.lmb .cg .cn .ac{fill:var(--c)}
.lmb .cg .cn .sb .ek-reads{fill:light-dark(#2563eb,#60a5fa)}.lmb .cg .cn .sb .ek-writes{fill:light-dark(#c2410c,#fb923c)}
.lmb .cg .cn .sb .ek-sends{fill:light-dark(#be185d,#f472b6)}.lmb .cg .cn .sb .ek-runs{fill:light-dark(#7e22ce,#c084fc)}
.lmb .cg .cn .sb .ek-signs{fill:light-dark(#475569,#94a3b8)}
.lmb .cg .cn .bu.flagged{fill:#f59e0b}
.lmb .cgk{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 12px;width:0;min-width:100%;font-size:12px}
.lmb .cgk .kp{display:inline-flex;align-items:center;gap:6px;padding:3px 10px 3px 8px;border:1px solid rgba(127,127,127,.25);border-radius:999px;background:rgba(127,127,127,.05);line-height:16px;white-space:nowrap}
.lmb .cgk .kp b{font-weight:650;margin-right:3px}
.lmb .cgk .kh{border-style:dashed;white-space:normal}
.lmb .cgk .kt{opacity:.55;margin-left:auto;padding:3px 2px}
.lmb .cgk .sw{display:block;overflow:visible;flex:none}
.lmb .cgk .sw .ln{fill:none;stroke:var(--c);stroke-width:1.8;stroke-linecap:round}
.lmb .cgk .sw .ah{fill:var(--c);stroke:var(--c);stroke-width:.6;stroke-linejoin:round}
.lmb .cgk .kw circle{fill:#f59e0b}
.lmb .cgk .kw path{stroke:var(--jp-layout-color0,var(--vscode-editor-background,Canvas));stroke-width:1.8;stroke-linecap:round;fill:none}
.lmb .cgk .kl circle{fill:none;stroke:currentColor;stroke-opacity:.55;stroke-width:1.2}
.lmb .cgk .kl path{fill:none;stroke:currentColor;stroke-opacity:.8;stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round}
.lmb details.dif{border:1px solid rgba(127,127,127,.25);border-radius:8px;margin:6px 0;max-width:1180px}
.lmb details.dif>summary{cursor:pointer;padding:5px 10px;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}
.lmb details.dif>summary code{font-weight:600}
.lmb .dif .dfs{font-size:10.5px;font-weight:600;padding:0 7px;border-radius:9px;background:rgba(127,127,127,.14)}
.lmb .dif .dfs-added{background:rgba(22,163,74,.15);color:#178a45}
.lmb .dif .dfs-removed{background:rgba(239,68,68,.14);color:#c53030}
.lmb .dif .dfs-changed{background:rgba(245,158,11,.18);color:#b86e04}
.lmb .dif .dfn{font:12px var(--lmb-mono);opacity:.75}
.lmb .dif .dfd{font-size:12px;opacity:.65}
.lmb .dif pre.dfp{margin:0;padding:6px 0;border:0;border-top:1px solid rgba(127,127,127,.2);border-radius:0;max-height:520px;overflow:auto;font-size:12px;line-height:1.5;white-space:pre}
.lmb .dif pre.dfp>span{display:inline-block;min-width:100%;box-sizing:border-box;padding:0 10px}
.lmb .dif .dfa{background:rgba(22,163,74,.13)}
.lmb .dif .dfr{background:rgba(239,68,68,.12)}
.lmb .dif .dfh{color:#3b82f6;opacity:.8;margin-top:4px}
.lmb .dif .dfx{padding:4px 10px 6px;font-size:12px;opacity:.7}""".replace("%CODE_FONT%", _CODE_FONT)

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _json_tree_css(_ROOT))

_BADGE = "⚡ Lambda"  # the chip before each report's title, so reports from different analyzers are easy to tell apart

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look (the windows' reports).
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)


_WIDE_CSS = """\
<style>
.lmb .flow,.lmb .wk{max-width:none;max-height:none}
.lmb .wk .wtree{max-height:calc(100vh - 140px)}
.lmb .cgb{max-width:100%}
</style>
"""


def _render_page(blocks: list[Any], title: str, theme: str = "") -> str:
    """
    A report as a page of its own (a whole HTML document) to open in a browser tab, with no row cap: the page has
    the tab's whole height and width, so the outline and the graph are as wide and tall as they are (_WIDE_CSS)
    instead of scrolling in a box. It's drawn in `theme`, like the View's reports.
    """
    look = _Look(_CSS + _WIDE_CSS, _ROOT, _BADGE)
    return _page_document(_report_html(blocks, 0, look, theme=theme), title)
