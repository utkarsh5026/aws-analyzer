"""
The look of S3 reports: their CSS root class, stylesheet (with the service's own blocks' rules) and
badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.jsontree import _json_tree_css
from .._kit.render import _base_css, _Look, _report_html


_ROOT = "s3a"  # the class around each report's HTML: its stylesheet's rules are scoped to it

_EXTRA_CSS = """\
.s3a img{max-width:100%;max-height:480px;border:1px solid rgba(127,127,127,.3)}
.s3a .pages{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;margin:6px 0 10px}
.s3a .pages figure{margin:0;flex:0 1 300px;min-width:0}
.s3a .pages.one figure{flex-basis:640px}
.s3a .pages img{display:block;width:100%;height:auto;max-height:none;border:0;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.3)}
.s3a .pages figcaption{font-size:11px;opacity:.65;text-align:center;margin-top:4px}
.s3a .zr{display:none}
.s3a .zo{display:block;cursor:zoom-in}
.s3a .zx,.s3a .zc,.s3a .zn,.s3a .zh{display:none}
.s3a .zr:not(:checked)+.zp .zo:hover img{box-shadow:0 0 0 2px rgba(59,130,246,.7),0 1px 4px rgba(0,0,0,.3)}
.s3a .zr:checked+.zp{position:fixed;inset:0;z-index:10000;display:flex;flex-direction:column;align-items:center;gap:8px;padding:16px 68px;background:rgba(17,17,17,.9)}
.s3a .zr:checked+.zp .zo{flex:1 1 auto;min-height:0;width:100%}
.s3a .zr:checked+.zp img{width:100%;height:100%;object-fit:contain;background:transparent;box-shadow:none}
.s3a .zr:checked+.zp .zx{display:block;position:absolute;inset:0;z-index:1;cursor:zoom-out}
.s3a .zr:checked+.zp .zh{display:block;flex:0 0 auto;color:#eee;font-size:12px}
.s3a .zr:checked+.zp .zn,.s3a .zr:checked+.zp .zc{display:flex;align-items:center;justify-content:center;position:absolute;z-index:2;width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.14);color:#fff;font-size:30px;line-height:1;cursor:pointer;user-select:none}
.s3a .zr:checked+.zp .zn:hover,.s3a .zr:checked+.zp .zc:hover{background:rgba(255,255,255,.3)}
.s3a .zr:checked+.zp .zn{top:50%;transform:translateY(-50%)}
.s3a .zr:checked+.zp .zprev{left:12px}
.s3a .zr:checked+.zp .znext{right:12px}
.s3a .zr:checked+.zp .zc{top:12px;right:12px;width:36px;height:36px;font-size:18px}
.s3a .flow{max-width:760px;margin:6px 0 10px;padding:14px 22px;border:1px solid rgba(127,127,127,.3);border-radius:6px}
.s3a .flow{font-size:14px;line-height:1.55}
.s3a .flow p{margin:0 0 8px;white-space:pre-wrap;overflow-wrap:anywhere}
.s3a .flow .dh{font-weight:600;line-height:1.3;margin:14px 0 6px}
.s3a .flow .d0{font-size:22px;margin-top:2px}
.s3a .flow .d1{font-size:18px}
.s3a .flow .d2{font-size:15px}
.s3a .flow ul{margin:0 0 8px;padding-left:22px}
.s3a .flow li.n{list-style:none}
.s3a .flow .pg{display:flex;align-items:center;gap:10px;margin:22px 0 12px;font-size:11px;opacity:.55;letter-spacing:.04em}
.s3a .flow .pg::before,.s3a .flow .pg::after{content:"";flex:1;border-top:1px solid rgba(127,127,127,.45)}
.s3a .flow .pg:first-child{margin-top:0}
.s3a .flow figure{margin:10px 0}
.s3a .flow img{display:block;height:auto;max-height:none}
.s3a .flow .pm{margin:8px 0;padding:5px 10px;border:1px dashed rgba(127,127,127,.5);border-radius:4px;font-size:12px;opacity:.75}
.s3a .vd.bad b{color:var(--bad-fg,#b91c1c)}
.s3a .fx{margin:6px 0 14px;max-width:980px}
.s3a .fx ol{list-style:none;margin:0;padding:0;border:1px solid rgba(127,127,127,.3);border-radius:8px;overflow:hidden}
.s3a .fx li{display:grid;grid-template-columns:24px minmax(0,1fr) minmax(130px,200px);gap:12px;align-items:start;padding:10px 14px;border-top:1px solid rgba(127,127,127,.18)}
.s3a .fx li:first-child{border-top:0}
.s3a .fx .fx-n{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;font-size:12px;font-weight:700;background:rgba(127,127,127,.15)}
.s3a .fx li.bad .fx-n{background:#dc2626;color:#fff}
.s3a .fx li.warn .fx-n{background:color-mix(in srgb,var(--warn,#d97706) 26%,transparent)}
.s3a .fx .fx-t{display:block;font-size:13.5px;font-weight:600}
.s3a .fx li.bad .fx-t{color:var(--bad-fg,#b91c1c)}
.s3a .fx .fx-w{margin:2px 0 6px;font-size:12.5px;opacity:.8}
.s3a .fx .fx-c code{overflow-wrap:anywhere}
.s3a .fx .fx-s{text-align:right;font-size:12px;opacity:.75}
.s3a .fx .fx-k{display:inline-block;margin:4px 0 0 4px;padding:0 6px;border-radius:4px;background:rgba(127,127,127,.12);font-size:11px}
.s3a .cg{display:grid;gap:4px;margin:4px 0 8px;max-width:980px;font-size:12.5px}
.s3a .cg .cgh{align-self:end;padding:0 2px 2px;font-size:10.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.s3a .cg .cgb{display:flex;flex-direction:column;justify-content:center;min-width:0}
.s3a .cg .cgb b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s3a .cg .cgb span{font-size:11px;opacity:.6}
.s3a .cg .cgc{display:flex;align-items:center;min-height:20px;padding:6px 9px;border-radius:6px;font-weight:600}
.s3a .cg .cgc.bad{background:#dc2626;color:#fff}
.s3a .cg .cgc.warn{background:color-mix(in srgb,var(--warn,#d97706) 24%,transparent)}
.s3a .cg .cgc.ok{background:rgba(127,127,127,.07);font-weight:400;opacity:.8}
.s3a .cg .cgc.unknown{border:1px dashed rgba(127,127,127,.55);font-weight:400;opacity:.8}
.s3a .cg .cgs{display:flex;flex-direction:column;justify-content:center;gap:3px;font-variant-numeric:tabular-nums;white-space:nowrap}
.s3a .cg .cgs .bar{display:block;height:6px;border-radius:3px;background:rgba(127,127,127,.18)}
.s3a .cg .cgs .bar b{display:block;height:100%;border-radius:3px;background:#e8890c}
.s3a .cgp{margin:2px 0 10px;max-width:980px}
.s3a .cgp>summary{cursor:pointer;padding:7px 10px;border:1px dashed rgba(127,127,127,.45);border-radius:6px;font-size:12.5px}
.s3a .cgp[open]>summary{margin-bottom:6px}"""  # the rules for S3's own blocks: pictures, PDF pages, documents, and the JSON tree's (_kit's)

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _json_tree_css(_ROOT))

_BADGE = "🪣 S3"  # the chip before each report's title, so reports from different analyzers are easy to tell apart

_LOOK = _Look(_CSS, _ROOT, _BADGE)


def _render_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws, in
    `theme` when one is named (set_theme's), else in the stylesheet's own look (the windows' reports).
    """
    return _report_html(blocks, max_rows, _LOOK, theme=theme)
