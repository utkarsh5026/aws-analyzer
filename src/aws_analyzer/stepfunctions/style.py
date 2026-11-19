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
.sfn{--fg-node:var(--jp-layout-color0,#fff);--fg-ink:#16191f;--fg-sub:#5f6b7a;--fg-edge:#8d99a8;--fg-hot:#0972d3;--fg-ok:#037f0c;--fg-ok-bg:#effaf0;--fg-bad:#d91515;--fg-bad-bg:#fff2f2;--fg-run:#0972d3;--fg-run-bg:#eef6ff;--fg-pill:#232f3e;--fg-pill-ink:#fff;--fg-box:#c6ccd5;--fg-shell:rgba(9,114,211,.035);--fg-shell-line:#9ba7b6;--fg-label:#f4f4f6;--fg-heat1:#fff7e0;--fg-heat1-line:#e0a100;--fg-heat2:#ffe9d6;--fg-heat2-line:#ec7211;--fg-heat3:#ffe1e1;--fg-heat3-line:#d91515;--fg-time1:#f2f7ff;--fg-time1-line:#7eb4ea;--fg-time2:#dcebff;--fg-time2-line:#2f84d8;--fg-time3:#b9d7fb;--fg-time3-line:#0a4a90}
.sfn .fg{max-width:100%;margin:4px 0 12px}
body[data-jp-theme-light="false"] .sfn,body.vscode-dark .sfn,body.vscode-high-contrast .sfn{--fg-node:#1b2129;--fg-ink:#e9ebed;--fg-sub:#a4aeb9;--fg-edge:#6b7684;--fg-hot:#5cb2ff;--fg-ok:#2bb534;--fg-ok-bg:#132a17;--fg-bad:#ff6b6b;--fg-bad-bg:#3a1718;--fg-run:#5cb2ff;--fg-run-bg:#10263e;--fg-pill:#e9ebed;--fg-pill-ink:#16191f;--fg-box:#414d5c;--fg-shell:rgba(92,178,255,.05);--fg-shell-line:#56616f;--fg-label:#262d36;--fg-heat1:#3a2f12;--fg-heat1-line:#ffd34d;--fg-heat2:#3d2410;--fg-heat2-line:#ff9a3c;--fg-heat3:#3a1718;--fg-heat3-line:#ff6b6b;--fg-time1:#14243a;--fg-time1-line:#4f8fd0;--fg-time2:#173659;--fg-time2-line:#5cb2ff;--fg-time3:#1d4f87;--fg-time3-line:#a9d4ff}
.sfn .fgv{overflow:auto;border:1px solid rgba(127,127,127,.18);border-radius:10px;background:radial-gradient(rgba(127,127,127,.16) 1px,transparent 1px) 0 0/18px 18px}
.sfn .fg.bare{margin:0}
.sfn .fg svg{display:block;font-family:inherit;color:var(--fg-ink)}
.sfn .fgv svg{max-width:100%;height:auto;margin:0 auto}
.sfn .fg:has(.z15:checked) .fgv svg{max-width:none;width:calc(var(--w) * 1.5)}
.sfn .fg:has(.z2:checked) .fgv svg{max-width:none;width:calc(var(--w) * 2)}
.sfn .fgk{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font-size:11.5px;margin:0 0 6px}
.sfn .fgk b{font-weight:650}
.sfn .fgk span{display:inline-flex;align-items:center;gap:5px;opacity:.85}
.sfn .fgk i{display:inline-block;width:13px;height:10px;border-radius:3px;border:1.5px solid var(--fg-box,#c6ccd5)}
.sfn .fgz{display:inline-flex!important;margin-left:auto;gap:2px;padding:2px;border-radius:7px;background:rgba(127,127,127,.12)}
.sfn .fgz label{cursor:pointer;padding:1px 9px;border-radius:5px;opacity:.7;font-variant-numeric:tabular-nums}
.sfn .fgz label:has(input:checked){background:var(--fg-node);opacity:1;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
.sfn .fe .fp{fill:none;stroke:var(--fg-edge);stroke-width:1.5}
.sfn .fe .fh{fill:var(--fg-edge);stroke:none}
.sfn .fe.catch .fp{stroke-dasharray:6 4}
.sfn .fe.loop .fp{stroke-dasharray:2 4;stroke-linecap:round}
.sfn .fe.on .fp{stroke:var(--fg-ok);stroke-width:2.6}
.sfn .fe.on .fh{fill:var(--fg-ok)}
.sfn .fe.on .fa{fill:none;stroke:var(--fg-node);stroke-width:1.4;stroke-dasharray:3 11;stroke-linecap:round;animation:sfnflow .9s linear infinite}
.sfn .fe.catch.on .fp{stroke:var(--fg-bad)}
.sfn .fe.catch.on .fh{fill:var(--fg-bad)}
.sfn .fq rect{fill:var(--fg-label);stroke:var(--fg-box);stroke-width:1}
.sfn .fq text{font-size:10.5px;fill:var(--fg-sub)}
.sfn .fq.catch rect{stroke:rgba(217,21,21,.45)}
.sfn .fq.catch text{fill:var(--fg-bad)}
.sfn .fq.on rect{stroke:var(--fg-ok)}
.sfn .fq.on text{fill:var(--fg-ok);font-weight:600}
.sfn .fq.catch.on rect{stroke:var(--fg-bad)}
.sfn .fq.catch.on text{fill:var(--fg-bad);font-weight:600}
.sfn .fn .fb{fill:var(--fg-node);stroke:var(--fg-box);stroke-width:1.2;filter:drop-shadow(0 1px 2px rgba(0,16,40,.10))}
.sfn .fn.fc .fb{fill:var(--fg-shell);stroke:var(--fg-shell-line);stroke-dasharray:6 4;filter:none}
.sfn .fn text{fill:currentColor}
.sfn .fnm{font-size:12.5px;font-weight:650}
.sfn .fns{font-size:10.5px;fill:var(--fg-sub)!important}
.sfn .fnt{font-size:10.5px;font-weight:650;fill:var(--fg-sub)!important;font-variant-numeric:tabular-nums}
.sfn .fdv{stroke:var(--fg-shell-line);stroke-dasharray:3 4;stroke-width:1}
.sfn .fi{fill:#5f6b7a}
.sfn .fi.i-lambda,.sfn .fi.i-compute{fill:#ed7100}
.sfn .fi.i-database{fill:#c925d1}
.sfn .fi.i-messaging,.sfn .fi.i-management{fill:#e7157b}
.sfn .fi.i-storage{fill:#7aa116}
.sfn .fi.i-ml{fill:#01a88d}
.sfn .fi.i-analytics{fill:#8c4fff}
.sfn .fi.i-security{fill:#dd344c}
.sfn .fi.i-choice,.sfn .fi.i-wait,.sfn .fi.i-pass{fill:#545b64}
.sfn .fi.i-map,.sfn .fi.i-parallel{fill:#0972d3}
.sfn .fi.i-succeed{fill:#037f0c}
.sfn .fi.i-fail{fill:#d91515}
.sfn .fgl{fill:none;stroke:#fff;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.sfn text.fgl{fill:#fff!important;stroke:none;font-weight:700}
.sfn .fpl rect{fill:var(--fg-pill)}
.sfn .fpl text{fill:var(--fg-pill-ink);font-size:11.5px;font-weight:650;letter-spacing:.02em}
.sfn .fst circle{stroke:var(--fg-node);stroke-width:2}
.sfn .fst path{fill:none;stroke:#fff;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.sfn .fst.s-ok circle{fill:var(--fg-ok)}
.sfn .fst.s-failed circle{fill:var(--fg-bad)}
.sfn .fst.s-running circle{fill:var(--fg-run)}
.sfn .fst.s-running path{transform-box:fill-box;transform-origin:100% 100%;animation:sfnspin 1s linear infinite}
.sfn .fn.m-ok .fb{fill:var(--fg-ok-bg);stroke:var(--fg-ok);stroke-width:1.6}
.sfn .fn.m-failed .fb{fill:var(--fg-bad-bg);stroke:var(--fg-bad);stroke-width:1.8}
.sfn .fn.m-running .fb{fill:var(--fg-run-bg);stroke:var(--fg-run);stroke-width:1.8}
.sfn .fn.fc.m-ok .fb,.sfn .fn.fc.m-failed .fb,.sfn .fn.fc.m-running .fb{fill-opacity:.55}
.sfn .fn.m-ok .fnt{fill:var(--fg-ok)!important}
.sfn .fn.m-failed .fnt{fill:var(--fg-bad)!important}
.sfn .fn.m-running .fnt{fill:var(--fg-run)!important}
.sfn .fn.m-skip{opacity:.5}
.sfn .fn.m-skip .fb{stroke-dasharray:4 3;filter:none}
.sfn .fhalo{fill:none;stroke-width:2;transform-box:fill-box;transform-origin:center;animation:sfnhalo 1.8s ease-out infinite}
.sfn .fn.m-failed .fhalo{stroke:var(--fg-bad);animation-iteration-count:3}
.sfn .fn.m-running .fhalo{stroke:var(--fg-run)}
.sfn .fn.m-h1 .fb{fill:var(--fg-heat1);stroke:var(--fg-heat1-line);stroke-width:1.6}
.sfn .fn.m-h2 .fb{fill:var(--fg-heat2);stroke:var(--fg-heat2-line);stroke-width:1.8}
.sfn .fn.m-h3 .fb{fill:var(--fg-heat3);stroke:var(--fg-heat3-line);stroke-width:2.2}
.sfn .fn.m-h2 .fnt,.sfn .fn.m-h3 .fnt{fill:var(--fg-heat3-line)!important}
.sfn .fn.m-t1 .fb{fill:var(--fg-time1);stroke:var(--fg-time1-line);stroke-width:1.4}
.sfn .fn.m-t2 .fb{fill:var(--fg-time2);stroke:var(--fg-time2-line);stroke-width:1.8}
.sfn .fn.m-t3 .fb{fill:var(--fg-time3);stroke:var(--fg-time3-line);stroke-width:2.2}
.sfn .fn.m-t2 .fnt,.sfn .fn.m-t3 .fnt{fill:var(--fg-time3-line)!important}
.sfn .fn.sel .fb{stroke:var(--fg-hot);stroke-width:3}
.sfn .fgk i.m-ok{background:var(--fg-ok-bg);border-color:var(--fg-ok)}
.sfn .fgk i.m-failed{background:var(--fg-bad-bg);border-color:var(--fg-bad)}
.sfn .fgk i.m-running{background:var(--fg-run-bg);border-color:var(--fg-run)}
.sfn .fgk i.m-skip{border-style:dashed;opacity:.6}
.sfn .fgk i.m-h1{background:var(--fg-heat1);border-color:var(--fg-heat1-line)}
.sfn .fgk i.m-h2{background:var(--fg-heat2);border-color:var(--fg-heat2-line)}
.sfn .fgk i.m-h3{background:var(--fg-heat3);border-color:var(--fg-heat3-line)}
.sfn .fgk i.m-t1{background:var(--fg-time1);border-color:var(--fg-time1-line)}
.sfn .fgk i.m-t2{background:var(--fg-time2);border-color:var(--fg-time2-line)}
.sfn .fgk i.m-t3{background:var(--fg-time3);border-color:var(--fg-time3-line)}
.sfn .fgk i.m-flow{height:0;border:0;border-top:2.6px dashed var(--fg-ok);border-radius:0;width:18px}
.sfn .fn,.sfn .fpl,.sfn .fe,.sfn .fq{transition:opacity .18s ease;animation:sfnrise .5s cubic-bezier(.2,.7,.3,1) both;animation-delay:calc(var(--l,0) * 60ms)}
.sfn .fn .fb{transition:stroke .15s ease,stroke-width .15s ease}
.sfn .fn:hover,.sfn .fpl:hover{cursor:default}
@keyframes sfnrise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes sfnflow{to{stroke-dashoffset:-14}}
@keyframes sfnspin{to{transform:rotate(360deg)}}
@keyframes sfnhalo{0%{opacity:.75;transform:scale(1)}100%{opacity:0;transform:scale(1.08,1.3)}}
@media (prefers-reduced-motion:reduce){.sfn .fg *{animation:none!important;transition:none!important}}
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
