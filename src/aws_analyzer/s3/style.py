"""
The look of S3 reports: their CSS root class, stylesheet (with the service's own blocks' rules) and
badge.
"""

from __future__ import annotations

from typing import Any

from .._kit.jsontree import _json_tree_css
from .._kit.render import _base_css, _report_html


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
.s3a .flow .pm{margin:8px 0;padding:5px 10px;border:1px dashed rgba(127,127,127,.5);border-radius:4px;font-size:12px;opacity:.75}"""  # the rules for S3's own blocks: pictures, PDF pages, documents, and the JSON tree's (_kit's)

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _json_tree_css(_ROOT))

_BADGE = "🪣 S3"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws.
    """
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)
