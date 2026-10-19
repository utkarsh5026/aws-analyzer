"""
The look of Bedrock knowledge base reports: their CSS root class, stylesheet and badge.
"""

from __future__ import annotations

from typing import Any

from .._bedrock.blocks import _download_css
from .._kit.render import _base_css, _report_html


_ROOT = "kba"  # the class around each report's HTML: its stylesheet's rules are scoped to it

_EXTRA_CSS = """\
.kba .psg{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 10px;margin:6px 0;max-width:900px}
.kba .psg .ph{font-weight:600;font-size:12px}
.kba .psg .pm{opacity:.65;font-size:12px}
.kba .psg .pt{margin-top:3px;white-space:pre-wrap;overflow-wrap:anywhere}
.kba a.fl{color:#3b82f6;text-decoration:none;border-bottom:1px solid rgba(59,130,246,.35)}
.kba a.fl:hover{border-bottom-color:currentColor}
.kba a.fl::after{content:"\\2197";font-size:.8em;margin-left:2px;opacity:.7}
.kba .lnk{margin:6px 0 8px;font-weight:600}
.kba mark{background:rgba(250,204,21,.4);color:inherit;border-radius:2px;padding:0 1px}
.kba .ans{font-size:14px;line-height:1.6;margin:8px 0 10px;max-width:900px;overflow-wrap:anywhere}
.kba .ans>:first-child{margin-top:0}
.kba .ans>:last-child{margin-bottom:0}
.kba .ans p{margin:0 0 .65em}
.kba .ans h1,.kba .ans h2,.kba .ans h3,.kba .ans h4,.kba .ans h5,.kba .ans h6{margin:.95em 0 .4em;line-height:1.3;font-weight:650}
.kba .ans h1{font-size:1.32em}
.kba .ans h2{font-size:1.2em}
.kba .ans h3{font-size:1.08em}
.kba .ans h4,.kba .ans h5,.kba .ans h6{font-size:1em}
.kba .ans ul,.kba .ans ol{margin:.25em 0 .65em;padding-left:1.45em}
.kba .ans li{margin:.18em 0}
.kba .ans li>p{margin:0 0 .35em}
.kba .ans li>ul,.kba .ans li>ol{margin:.15em 0 .2em}
.kba .ans blockquote{margin:.4em 0 .75em;padding:.15em 0 .15em .9em;border-left:3px solid rgba(127,127,127,.36);opacity:.88}
.kba .ans hr{border:0;border-top:1px solid rgba(127,127,127,.22);margin:1em 0}
.kba .ans code{font-size:.86em;user-select:text;-webkit-user-select:text}
.kba .ans a{color:#3b82f6;text-decoration:none;border-bottom:1px solid rgba(59,130,246,.35)}
.kba .ans a:hover{border-bottom-color:currentColor}
.kba .ans .pre{position:relative;margin:.45em 0 .8em}
.kba .ans .pre pre{margin:0;padding:11px 14px;white-space:pre;overflow:auto;max-height:420px;font-size:12.5px;line-height:1.55;user-select:all;-webkit-user-select:all;cursor:text}
.kba .ans .pre code{background:none;padding:0;font-size:inherit;user-select:inherit;-webkit-user-select:inherit}
.kba .ans .pre .lang{position:absolute;top:6px;right:12px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;opacity:.5}
.kba .ans .mdt{overflow-x:auto;margin:.45em 0 .8em;border:1px solid rgba(127,127,127,.22);border-radius:10px}
.kba .ans table{border-collapse:collapse;font-size:13px;line-height:1.45;width:100%}
.kba .ans th,.kba .ans td{border-bottom:1px solid rgba(127,127,127,.22);padding:6px 11px;text-align:left;vertical-align:top}
.kba .ans th{background:rgba(127,127,127,.06);font-weight:600}
.kba .ans tbody tr:last-child td{border-bottom:0}
.kba .ans th.c,.kba .ans td.c{text-align:center}
.kba .ans th.r,.kba .ans td.r{text-align:right;font-variant-numeric:tabular-nums}
.kba .ans .cite{background:rgba(59,130,246,.10);border-radius:3px;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.kba .ans sup{font-size:10px;font-weight:650;color:#3b82f6;margin-left:1px;line-height:0}
.kba .ans .none{font-style:italic;opacity:.6}
.kba .steps{margin:8px 0 12px;max-width:900px}
.kba .st{position:relative;display:flex;gap:12px;padding:0 0 13px}
.kba .st::before{content:"";position:absolute;left:6px;top:17px;bottom:1px;width:2px;border-radius:1px;background:rgba(127,127,127,.22)}
.kba .st:last-child{padding-bottom:2px}
.kba .st:last-child::before{display:none}
.kba .st .sd{position:relative;z-index:1;flex:0 0 auto;width:14px;height:14px;margin-top:2px;border-radius:50%;background:rgba(127,127,127,.38);box-shadow:0 0 0 3px rgba(127,127,127,.13)}
.kba .st.ok .sd{background:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.18)}
.kba .st.warn .sd{background:#f59e0b;box-shadow:0 0 0 3px rgba(245,158,11,.2)}
.kba .st.bad .sd{background:#ef4444;box-shadow:0 0 0 3px rgba(239,68,68,.18)}
.kba .st .sb{min-width:0;line-height:1.45}
.kba .st .sk{font-weight:600;margin-right:8px}
.kba .st .sv{overflow-wrap:anywhere}
.kba .st.bad .sv{color:#dc2626;font-weight:600}
.kba .st .sx{font-size:12px;opacity:.6;margin-top:1px;overflow-wrap:anywhere}
.kba .pipe{margin:6px 0 12px}
.kba .pipe .pl{font-size:12px;font-weight:600;margin:10px 0 5px}
.kba .pipe .pr{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px}
.kba .pipe .ps{flex:1 1 150px;max-width:280px;min-width:0;border:1px solid rgba(127,127,127,.3);border-radius:8px;padding:6px 10px}
.kba .pipe .ps.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.07)}
.kba .pipe .ps.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.07)}
.kba .pipe .ps.ok{border-color:rgba(16,185,129,.55)}
.kba .pipe .pk{font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.55}
.kba .pipe .pv{font-weight:600;overflow-wrap:anywhere;margin-top:1px}
.kba .pipe .pd{font-size:12px;opacity:.65;overflow-wrap:anywhere;margin-top:1px}
.kba .pipe .pa{align-self:center;opacity:.35;font-size:15px}
.kba .ckbar{display:flex;gap:2px;height:16px;margin:6px 0 2px;max-width:900px;border-radius:4px;overflow:hidden}
.kba .ckbar i{display:block;min-width:3px;background:rgba(59,130,246,.62)}
.kba .ckbar i:nth-child(even){background:rgba(59,130,246,.36)}
.kba .ckbar i.tiny{background:rgba(245,158,11,.7)}
.kba .ckpos{position:relative;height:10px;margin:8px 0 2px;max-width:900px;border-radius:4px;background:rgba(239,68,68,.16)}
.kba .ckpos i{position:absolute;top:0;bottom:0;border-radius:2px;background:rgba(16,185,129,.62)}
.kba .cklg{font-size:11px;opacity:.6;margin:2px 0 8px}
.kba details.ck{border:1px solid rgba(127,127,127,.25);border-radius:8px;margin:4px 0;max-width:900px}
.kba details.ck>summary{cursor:pointer;display:flex;align-items:center;gap:8px;padding:5px 10px;list-style:none;white-space:nowrap;overflow:hidden;border-radius:8px}
.kba details.ck>summary::-webkit-details-marker{display:none}
.kba details.ck>summary:hover{background:rgba(127,127,127,.07)}
.kba details.ck[open]>summary{border-bottom:1px solid rgba(127,127,127,.18);border-radius:8px 8px 0 0}
.kba .ck .ckn{flex:0 0 auto;min-width:22px;height:20px;padding:0 6px;box-sizing:border-box;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:650;color:#3b82f6;background:rgba(59,130,246,.12)}
.kba .ck .ckp,.kba .ck .ckz{flex:0 0 auto;font-size:12px;opacity:.7;font-variant-numeric:tabular-nums}
.kba .ck .cko{flex:0 0 auto;font-size:11px;padding:0 7px;border-radius:9px;background:rgba(16,185,129,.13)}
.kba .ck .ckw{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;opacity:.85}
.kba .ck .cks{flex:0 0 auto;font-size:11px;opacity:.65;font-variant-numeric:tabular-nums}
.kba .ck .ckt{white-space:pre-wrap;overflow-wrap:anywhere;padding:8px 12px;line-height:1.5;max-height:420px;overflow:auto}
.kba .ck .ckt mark.ov{background:rgba(16,185,129,.22)}
.kba .ck .ckm{padding:0 12px 8px;font-size:12px;opacity:.6;overflow-wrap:anywhere}
.kba .shr{display:flex;gap:2px;height:12px;margin:8px 0 6px;max-width:900px;border-radius:6px;overflow:hidden}
.kba .shr i{display:block;min-width:4px;background:rgba(127,127,127,.4)}
.kba .shl{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;margin:0 0 10px;opacity:.85}
.kba .shl i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:-1px;background:rgba(127,127,127,.4)}
.kba .shl b{font-weight:650;font-variant-numeric:tabular-nums}
.kba .s-failed{background:#ef4444!important}
.kba .s-changed{background:#f59e0b!important}
.kba .s-new{background:#f97316!important}
.kba .s-skipped{background:#a855f7!important}
.kba .s-deleted{background:#ec4899!important}
.kba .s-partial{background:#eab308!important}
.kba .s-ignored{background:#94a3b8!important}
.kba .s-indexing{background:#3b82f6!important}
.kba .s-unchecked{background:#cbd5e1!important}
.kba .s-indexed{background:#10b981!important}
.kba .json{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.55;padding:9px 12px 9px 26px;border:1px solid rgba(127,127,127,.3);border-radius:8px;overflow:auto;max-height:560px;white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 8px}
.kba .json details>summary{cursor:pointer;list-style:none;position:relative;display:block}
.kba .json details>summary::-webkit-details-marker{display:none}
.kba .json details>summary::before{content:"\\25B8";position:absolute;left:-14px;top:0;opacity:.5;font-size:11px}
.kba .json details[open]>summary::before{content:"\\25BE"}
.kba .json details[open]>summary .jx{display:none}
.kba .json .ji{padding-left:18px;margin-left:1px;border-left:1px dotted rgba(127,127,127,.35)}
.kba .json .jx{opacity:.55}
.kba .json .jm{background:rgba(250,204,21,.30);border-radius:4px;box-shadow:0 0 0 2px rgba(250,204,21,.30)}
.kba .json .jc{margin-left:10px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:11px;font-style:italic;opacity:.55;white-space:nowrap}
.kba .jk{color:#7c3aed}
.kba .js{color:#15803d}
.kba .jn{color:#b45309}
.kba .jl{color:#1d4ed8;font-weight:600}
body[data-jp-theme-light="false"] .kba .jk,body.vscode-dark .kba .jk{color:#c4b5fd}
body[data-jp-theme-light="false"] .kba .js,body.vscode-dark .kba .js{color:#86efac}
body[data-jp-theme-light="false"] .kba .jn,body.vscode-dark .kba .jn{color:#fcd34d}
body[data-jp-theme-light="false"] .kba .jl,body.vscode-dark .kba .jl{color:#93c5fd}"""

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _download_css(_ROOT))

_BADGE = "📚 Bedrock KB"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws.
    """
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)
