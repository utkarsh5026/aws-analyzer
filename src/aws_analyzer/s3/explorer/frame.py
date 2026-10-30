"""
What the window's panes share: its line drawings, its stylesheet, its buttons and the list's rows.
"""

from __future__ import annotations

from typing import Any, Callable

from ..._kit.window import _icon_url, _terminal_theme

from .navigator import Entry


# The toolbar's icons (24 x 24, drawn with lines), shown as masks in the text's colour so they follow the theme.
_ICON_PATHS = {
    "back": "<path d='M19 12H5M11 18l-6-6 6-6'/>",
    "forward": "<path d='M5 12h14M13 6l6 6-6 6'/>",
    "up": "<path d='M12 19V5M6 11l6-6 6 6'/>",
    "refresh": "<path d='M20 12a8 8 0 1 1-2.34-5.66L20 8.5M20 4v4.5h-4.5'/>",
    "edit": "<path d='M15.5 4.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4zM13.5 6.5l3 3'/>",
    "settings": "<path d='M9.1 5.9L9 2.9h6l-.1 3 1 .5 2.5-1.5 3 5.1-2.6 1.4v1.2l2.6 1.4-3 5.1-2.5-1.5-1 .5.1 3H9l.1-3-1-.5"
                "-2.5 1.5-3-5.1 2.6-1.4v-1.2L2.6 10l3-5.1 2.5 1.5z'/><circle cx='12' cy='12' r='2.8'/>",
    "close": "<path d='M6 6l12 12M18 6L6 18'/>",
    "search": "<circle cx='11' cy='11' r='6.5'/><path d='M20 20l-4.2-4.2'/>",
    "chevron": "<path d='M9 6l6 6-6 6'/>",
    "previous": "<path d='M15 6l-6 6 6 6'/>",
    "next": "<path d='M9 6l6 6-6 6'/>",
    "first": "<path d='M17 6l-6 6 6 6M7 6v12'/>",
    "last": "<path d='M7 6l6 6-6 6M17 6v12'/>",
    "below": "<path d='M6 4v9a3 3 0 0 0 3 3h10M15 12l4 4-4 4'/>",
    "check": "<path stroke-width='3.2' d='M5 12.5l4.5 4.5L19 7.5'/>",
    "minus": "<path stroke-width='3.2' d='M6.5 12h11'/>",
}


def _icon_rules() -> str:
    """
    A CSS rule per icon: .s3x-i-<name> sets --s3x-icon, which .s3x-ic::before (and the search box) draw.
    """
    return "\n".join(f".s3x .s3x-i-{name}{{--s3x-icon:{_icon_url(paths)}}}" for name, paths in _ICON_PATHS.items())

_TERMINAL_CSS = _terminal_theme((".s3x",), "s3t") + """
.s3x.s3x.s3x{--s3x-accent:var(--s3t-accent);--s3x-accent-fg:var(--s3t-accent);--s3x-tint:var(--s3t-hover);
 --s3x-tint-line:var(--s3t-line);--s3x-line:var(--s3t-line);--s3x-fill:transparent;--s3x-hover:var(--s3t-hover);
 --s3x-bg:var(--s3t-bg)}
.s3x.s3x{font-family:var(--s3t-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.s3x .jupyter-button,.s3x .widget-label,.s3x input{font-family:var(--s3t-mono);font-size:12.5px}
.s3x .s3x-bar{padding:7px 12px}
.s3x .s3x-navs{gap:0;padding:0;background:none}
.s3x button.s3x-nav{width:26px;min-width:26px;height:26px;border-radius:6px;opacity:.65}
.s3x .s3x-crumbs::before{content:"s3://";flex:0 0 auto;margin:0 0 0 6px;color:var(--s3t-prompt);font-weight:700}
.s3x button.s3x-crumb{height:26px;padding:0 3px;border-radius:5px;opacity:.7;font-weight:400}
.s3x button.s3x-crumb.s3x-here{font-weight:700;color:var(--s3t-accent)}
.s3x .widget-label.s3x-sep{width:auto;min-width:0;opacity:.45;font-size:0}
.s3x .s3x-sep::before{content:"/";width:auto;height:auto;background:none;-webkit-mask:none;mask:none;font-size:12.5px;
 line-height:26px;padding:0 1px}
.s3x .s3x-search input,.s3x .s3x-path input,.s3x .s3x-setting input{height:28px;border-radius:6px;border:1px solid var(--s3t-line);background:transparent}
.s3x .s3x-search input:focus,.s3x .s3x-path input:focus,.s3x .s3x-setting input:focus{border-color:var(--s3t-accent);box-shadow:none}
.s3x .s3x-search::before{content:"\\203A";-webkit-mask:none;mask:none;background:none;width:auto;height:auto;margin-top:-10px;
 left:11px;font-size:15px;line-height:20px;font-weight:700;color:var(--s3t-prompt);opacity:1}
.s3x .s3x-search input{padding:0 30px 0 26px}
.s3x .s3x-segs{gap:6px;padding:1px;background:none}
.s3x button.s3x-seg,.s3x button.s3x-chip,.s3x button.s3x-act,.s3x a.s3x-act,.s3x button.s3x-quiet{height:24px;line-height:22px;
 padding:0 9px;border-radius:6px;border:1px solid var(--s3t-line);background:transparent;font-size:12px;font-weight:400;opacity:1}
.s3x button.s3x-seg{padding:0 7px;color:var(--s3t-dim)}
.s3x .s3x-kinds{gap:6px}
.s3x button.s3x-deep{padding:0 7px}
.s3x button.s3x-seg:hover,.s3x button.s3x-chip:hover,.s3x button.s3x-act:hover,.s3x a.s3x-act:hover,
.s3x button.s3x-quiet:hover:enabled{background:transparent;border-color:var(--s3t-accent);color:var(--s3t-accent)}
.s3x button.s3x-seg.s3x-on,.s3x button.s3x-chip.s3x-on,.s3x button.s3x-act.s3x-on{background:transparent;box-shadow:none;
 border-color:var(--s3t-accent);color:var(--s3t-accent);font-weight:700}
.s3x button.s3x-close{border-color:transparent;font-size:0}
.s3x .s3x-head{padding:0 10px}
.s3x button.s3x-col{height:26px;line-height:26px;font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;
 color:var(--s3t-comment);opacity:1}
.s3x button.s3x-col::before{content:"# "}
.s3x button.s3x-col.s3x-num::before{content:none}
.s3x button.s3x-col.s3x-on{color:inherit;font-weight:700}
.s3x .s3x-rows{padding:4px 6px}
.s3x .s3x-r,.s3x button.s3x-row{height:28px}
.s3x .s3x-r2,.s3x .s3x-r2 button.s3x-row{height:44px}
.s3x .s3x-r2 button.s3x-check{top:14px}
.s3x button.s3x-row{border-radius:5px}
.s3x button.s3x-row.s3x-on{background:var(--s3t-hover);font-weight:700;box-shadow:inset 2px 0 0 var(--s3t-accent);
 color:var(--s3t-accent)}
.s3x button.s3x-row.s3x-picked{background:var(--s3t-hover)}
.s3x .s3x-r .widget-label{color:var(--s3t-dim);opacity:.85;font-size:11.5px}
.s3x button.s3x-check{border-radius:3px}
.s3x button.s3x-primary{height:26px;padding:0 12px;border-radius:6px;border:1px solid var(--s3t-accent);background:transparent;
 color:var(--s3t-accent);font-weight:700;box-shadow:none}
.s3x button.s3x-primary:hover:enabled{background:var(--s3t-hover)}
.s3x .s3x-actions{padding:9px 0}
.s3x .s3x-status{background:transparent;font-size:11.5px;color:var(--s3t-dim)}
.s3x .s3x-status .s3x-at::before{content:"\\25CF  ";color:var(--s3t-ok)}
.s3x .s3x-status code{border:0;background:var(--s3t-hover);border-radius:4px;color:var(--s3t-s)}
.s3x .s3x-pages .widget-html-content{color:var(--s3t-dim)}
.s3x .s3x-busy::before,.s3x .s3x-spin{border-top-color:var(--s3t-accent)}"""


_CSS = """<style>
.s3x{--s3x-accent:#3b82f6;--s3x-accent-fg:#1d64d8;--s3x-tint:rgba(59,130,246,.12);--s3x-tint-line:rgba(59,130,246,.45);
 --s3x-line:rgba(127,127,127,.22);--s3x-fill:rgba(127,127,127,.08);--s3x-hover:rgba(127,127,127,.13);
 --s3x-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--s3x-raised:var(--s3x-bg);
 font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;
 border:1px solid var(--s3x-line);border-radius:12px;overflow:hidden;margin:6px 0;background:var(--s3x-bg);
 box-shadow:0 1px 2px rgba(0,0,0,.04),0 12px 32px -18px rgba(0,0,0,.25)}
body[data-jp-theme-light=false] .s3x,body.vscode-dark .s3x{--s3x-accent-fg:#8ab4ff;--s3x-tint:rgba(96,165,250,.16);
 --s3x-tint-line:rgba(96,165,250,.5);--s3x-raised:rgba(255,255,255,.11)}
.s3x .widget-html-content{line-height:1.45}
.s3x .jupyter-button,.s3x .widget-label,.s3x input{font-size:13px}
.s3x button.jupyter-button{color:inherit;background:transparent;box-shadow:none;outline:none;margin:0;font-family:inherit;
 transition:background-color .12s ease,color .12s ease,opacity .12s ease,border-color .12s ease,box-shadow .12s ease}
.s3x button.jupyter-button:hover:enabled,.s3x button.jupyter-button:focus:enabled,.s3x button.jupyter-button:active,
.s3x button.jupyter-button.mod-active{box-shadow:none;outline:none}
.s3x button.jupyter-button:focus-visible:enabled{outline:2px solid var(--s3x-tint-line);outline-offset:-2px}
.s3x button.jupyter-button:active:enabled{background-color:var(--s3x-hover)}
.s3x .s3x-ic::before{content:"";display:block;flex:0 0 auto;width:16px;height:16px;background:currentColor;
 -webkit-mask:var(--s3x-icon) center/contain no-repeat;mask:var(--s3x-icon) center/contain no-repeat}
.s3x .s3x-body{height:clamp(560px,calc(100vh - 220px),1400px)}
body[class*=vscode-] .s3x .s3x-body{height:560px}
.s3x .s3x-bar{align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-bar>*,.s3x .s3x-navs>*{margin:0}
.s3x .s3x-navs{flex:0 0 auto;gap:2px;padding:2px;border-radius:10px;background:var(--s3x-fill)}
.s3x button.s3x-nav{display:inline-flex;align-items:center;justify-content:center;width:30px;min-width:30px;height:28px;
 padding:0;border-radius:8px;font-size:0;line-height:0;opacity:.8}
.s3x button.s3x-nav:hover:enabled{background:var(--s3x-hover);opacity:1}
.s3x button.s3x-nav:disabled{opacity:.25;cursor:default}
.s3x .s3x-crumbs{flex:1 1 auto;min-width:0;overflow:hidden;align-items:center;flex-wrap:nowrap}
.s3x .s3x-crumbs>*{margin:0;flex:0 1 auto;min-width:0}
.s3x button.s3x-crumb{width:auto;max-width:240px;height:28px;padding:0 8px;border-radius:7px;opacity:.62;font-weight:500}
.s3x button.s3x-crumb:hover{background:var(--s3x-hover);opacity:1}
.s3x button.s3x-crumb.s3x-here{opacity:1;font-weight:650}
.s3x .s3x-sep{display:flex;align-items:center;justify-content:center;width:14px;min-width:14px;flex:0 0 auto;font-size:0;
 opacity:.35}
.s3x .s3x-sep::before{width:12px;height:12px}
.s3x .s3x-search input,.s3x .s3x-path input,.s3x .s3x-setting input{height:32px;padding:0 10px;border-radius:8px;
 border:1px solid transparent;background:var(--s3x-fill);color:inherit;box-shadow:none}
.s3x .s3x-search input:focus,.s3x .s3x-path input:focus,.s3x .s3x-setting input:focus{outline:none;
 border-color:var(--s3x-accent);background:var(--s3x-bg);box-shadow:0 0 0 3px var(--s3x-tint)}
.s3x .s3x-path input{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.s3x .s3x-side{border-right:1px solid var(--s3x-line)}
.s3x .s3x-side>*{margin:0}
.s3x .s3x-find{gap:8px;padding:10px 10px 10px;border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-find>*,.s3x .s3x-searchrow>*{margin:0}
.s3x .s3x-searchrow{position:relative;align-items:center}
.s3x .s3x-search{position:relative}
.s3x .s3x-search::before{content:"";position:absolute;z-index:1;left:11px;top:50%;width:15px;height:15px;margin-top:-7.5px;
 background:currentColor;opacity:.45;pointer-events:none;-webkit-mask:var(--s3x-icon) center/contain no-repeat;
 mask:var(--s3x-icon) center/contain no-repeat}
.s3x .s3x-search input{padding:0 34px}
.s3x button.s3x-clear{position:absolute;z-index:1;right:4px;top:50%;margin-top:-12px;width:24px;min-width:24px;height:24px;
 border-radius:6px}
.s3x button.s3x-clear::before{width:12px;height:12px}
.s3x .s3x-kinds{align-items:center;gap:8px;flex-wrap:wrap}
.s3x .s3x-kinds>*,.s3x .s3x-segs>*,.s3x .s3x-types>*{margin:0}
.s3x .s3x-segs{flex:0 0 auto;gap:2px;padding:2px;border-radius:9px;background:var(--s3x-fill)}
.s3x button.s3x-seg{width:auto;height:26px;padding:0 10px;border-radius:7px;font-size:12px;font-weight:500;opacity:.68}
.s3x button.s3x-seg:hover{opacity:1}
.s3x button.s3x-seg.s3x-on{opacity:1;background:var(--s3x-raised);
 box-shadow:0 1px 2px rgba(0,0,0,.12),0 0 0 1px rgba(127,127,127,.14)}
.s3x .s3x-types{flex-wrap:wrap;gap:6px}
.s3x button.s3x-chip{width:auto;height:26px;padding:0 10px;border-radius:13px;border:1px solid var(--s3x-line);font-size:12px}
.s3x button.s3x-chip:hover{background:var(--s3x-hover)}
.s3x button.s3x-chip.s3x-on{background:var(--s3x-tint);border-color:var(--s3x-tint-line);color:var(--s3x-accent-fg);
 font-weight:600}
.s3x button.s3x-more{border-style:dashed;opacity:.75}
.s3x button.s3x-deep{display:inline-flex;align-items:center;gap:6px;margin-left:auto}
.s3x button.s3x-deep::before{width:13px;height:13px}
.s3x .s3x-list>*{margin:0}
.s3x .s3x-head{position:sticky;top:0;z-index:4;padding:0 12px;border-bottom:1px solid var(--s3x-line);
 background:var(--s3x-bg)}
.s3x .s3x-head>*{margin:0}
.s3x button.s3x-col{height:30px;line-height:30px;padding:0 8px;text-align:left;font-size:11px;font-weight:600;
 letter-spacing:.06em;text-transform:uppercase;opacity:.48}
.s3x button.s3x-col:hover{opacity:.85}
.s3x button.s3x-col.s3x-on{opacity:.9}
.s3x button.s3x-col.s3x-num{text-align:right}
.s3x button.s3x-namecol{padding-left:28px}
.s3x .s3x-rows{padding:6px}
.s3x .s3x-rows>*{margin:0}
.s3x .s3x-r{position:relative;height:32px;align-items:center;justify-content:flex-end;flex:0 0 auto}
.s3x .s3x-r>*{margin:0}
.s3x button.s3x-row{position:absolute;top:0;left:0;width:100%;height:32px;text-align:left;padding:0 164px 0 34px;
 border-radius:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s3x button.s3x-row:hover{background:var(--s3x-hover)}
.s3x button.s3x-row.s3x-picked{background:rgba(59,130,246,.07)}
.s3x button.s3x-row.s3x-on{background:var(--s3x-tint);font-weight:600;box-shadow:inset 0 0 0 1px var(--s3x-tint-line)}
.s3x button.s3x-check{position:absolute;z-index:2;top:50%;left:9px;display:inline-flex;align-items:center;
 justify-content:center;width:16px;min-width:16px;height:16px;margin:-8px 0 0;padding:0;font-size:0;border-radius:5px;
 border:1.5px solid rgba(127,127,127,.55);background:var(--s3x-bg);color:#fff;opacity:0}
.s3x button.s3x-check::before{width:11px;height:11px;opacity:0}
.s3x .s3x-r:hover button.s3x-check,.s3x .s3x-picking button.s3x-check,.s3x .s3x-list:hover .s3x-head button.s3x-check{
 opacity:1}
.s3x button.s3x-check:hover{border-color:var(--s3x-accent)}
.s3x button.s3x-check.s3x-on{opacity:1;background:var(--s3x-accent);border-color:var(--s3x-accent)}
.s3x button.s3x-check.s3x-on::before{opacity:1}
.s3x .s3x-head button.s3x-check{left:15px}
.s3x .s3x-r2 button.s3x-check{top:15px}
.s3x .s3x-r .widget-label{position:relative;z-index:1;pointer-events:none;text-align:right;opacity:.58;
 font-size:12px;font-variant-numeric:tabular-nums;padding-right:8px}
.s3x .s3x-buckets button.s3x-row{padding:0 88px 0 10px}
.s3x .s3x-buckets .s3x-size,.s3x .s3x-buckets button.s3x-check{display:none}
.s3x .s3x-buckets button.s3x-namecol{padding-left:8px}
.s3x .s3x-r2,.s3x .s3x-r2 button.s3x-row{height:46px}
.s3x .s3x-r2 button.s3x-row{padding-bottom:16px}
.s3x .s3x-r .widget-label.s3x-where{position:absolute;display:block;left:61px;right:164px;bottom:6px;height:16px;
 line-height:16px;padding:0;text-align:left;font-size:11.5px;opacity:.5;direction:rtl;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.s3x .s3x-where::before,.s3x .s3x-where::after{content:"\\200E"}
.s3x .s3x-foot{flex-wrap:wrap;justify-content:center;gap:6px;padding:4px 12px 14px;align-items:center}
.s3x .s3x-foot>*{margin:0}
.s3x button.s3x-link{width:auto;height:28px;padding:0 10px;border-radius:8px;color:var(--s3x-accent-fg);font-weight:500}
.s3x button.s3x-link:hover{background:var(--s3x-tint)}
.s3x .s3x-empty{padding:22px 8px 4px;text-align:center;opacity:.62;font-size:12.5px}
.s3x .s3x-pages{align-items:center;gap:2px;padding:3px 8px 3px 14px;border-top:1px solid var(--s3x-line);
 background:var(--s3x-bg)}
.s3x .s3x-pages>*{margin:0}
.s3x .s3x-pages .widget-html-content{font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis;opacity:.72}
.s3x .s3x-pages b{font-weight:600}
.s3x .s3x-picks{align-items:center;gap:6px;padding:8px 10px 8px 14px;border-top:1px solid var(--s3x-line);
 background:var(--s3x-bg);box-shadow:0 -8px 18px -14px rgba(0,0,0,.3)}
.s3x .s3x-picks>*{margin:0}
.s3x .s3x-picks .widget-html-content{display:flex;flex-direction:column;line-height:1.3;min-width:0}
.s3x .s3x-picks-n{font-weight:600}
.s3x .s3x-picks-s{font-size:11.5px;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.s3x button.s3x-primary{width:auto;height:30px;padding:0 14px;border-radius:8px;background:var(--s3x-accent);
 color:#fff;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
.s3x button.s3x-primary:hover:enabled{background:#2f6fde}
.s3x button.s3x-primary:disabled{opacity:.4;cursor:default;box-shadow:none}
.s3x button.s3x-quiet{width:auto;height:30px;padding:0 10px;border-radius:8px;opacity:.72}
.s3x button.s3x-quiet:hover:enabled{opacity:1;background:var(--s3x-hover)}
.s3x .s3x-right{padding:0 18px 14px}
.s3x .s3x-right>*{margin:0;min-width:0}
.s3x .s3x-right .widget-html-content{min-width:0}
.s3x .s3x-actions{position:sticky;top:0;z-index:3;flex-wrap:wrap;gap:6px;padding:10px 0;background:var(--s3x-bg);
 border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-actions>*{margin:0}
.s3x button.s3x-act{width:auto;height:28px;padding:0 12px;border-radius:8px;border:1px solid var(--s3x-line);
 font-weight:500}
.s3x button.s3x-act:hover{background:var(--s3x-hover)}
.s3x button.s3x-act.s3x-on{background:var(--s3x-tint);border-color:var(--s3x-tint-line);color:var(--s3x-accent-fg)}
.s3x a.s3x-act{display:flex;align-items:center;box-sizing:border-box;height:28px;padding:0 12px;border-radius:8px;
 border:1px solid var(--s3x-line);font-weight:500;color:inherit;text-decoration:none;white-space:nowrap;
 transition:background-color .12s ease}
.s3x a.s3x-act:hover{background:var(--s3x-hover)}
.s3x a.s3x-act:focus-visible{outline:2px solid var(--s3x-tint-line);outline-offset:-2px}
.s3x button.s3x-close{display:inline-flex;align-items:center;justify-content:center;margin-left:auto;width:28px;
 min-width:28px;padding:0;border-color:transparent;font-size:0;opacity:.7}
.s3x button.s3x-close:hover{opacity:1}
.s3x button.s3x-expand{margin-left:auto}
.s3x button.s3x-expand+button.s3x-close{margin-left:0}
.s3x .s3x-pager{flex-wrap:wrap;gap:6px;align-items:center;margin-top:10px;padding:10px 0 4px;
 border-top:1px solid var(--s3x-line)}
.s3x .s3x-pager>*{margin:0}
.s3x .s3x-pager .widget-label{opacity:.6;margin-right:6px}
.s3x .s3x-settings{gap:10px;padding:6px 0 8px}
.s3x .s3x-settings>*{margin:0}
.s3x .s3x-setting{gap:10px;align-items:center;flex-wrap:wrap}
.s3x .s3x-setting>*{margin:0}
.s3x .s3x-setting .widget-label{font-size:12px;font-weight:500}
.s3x .s3x-hint{opacity:.6;font-size:12px}
.s3x .s3x-status{padding:0 12px;border-top:1px solid var(--s3x-line);background:var(--s3x-fill);font-size:12px}
.s3x .s3x-status>.widget-html-content{display:flex;gap:12px;justify-content:space-between;align-items:center;
 line-height:30px;min-width:0}
.s3x .s3x-status .s3x-at{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;opacity:.78}
.s3x .s3x-status code{display:inline-block;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;line-height:18px;padding:0 7px;
 border-radius:6px;border:1px solid var(--s3x-line);background:var(--s3x-bg);user-select:all;-webkit-user-select:all;
 cursor:text}
@keyframes s3x-spin{to{transform:rotate(360deg)}}
@keyframes s3x-glow{from{background-position:100% 0}to{background-position:0 0}}
.s3x .s3x-busy::before,.s3x .s3x-spin{content:"";display:inline-block;flex:0 0 auto;width:10px;height:10px;
 margin-right:8px;vertical-align:-2px;border:2px solid rgba(127,127,127,.28);border-top-color:var(--s3x-accent);
 border-radius:50%;animation:s3x-spin .75s linear infinite}
.s3x .s3x-wait{padding:16px 0}
.s3x .s3x-wait-t{display:flex;align-items:center;opacity:.75;margin-bottom:16px}
.s3x .s3x-sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;
 background-image:linear-gradient(90deg,var(--s3x-fill) 30%,var(--s3x-hover) 50%,var(--s3x-fill) 70%);
 animation:s3x-glow 1.3s ease-in-out infinite}
.s3x .s3x-sk.s3x-t{height:18px;width:45%;margin-bottom:16px}
.s3x .s3x-sk-cards{display:flex;gap:8px;margin:0 0 18px}
.s3x .s3x-sk-cards .s3x-sk{flex:1;height:50px;margin:0;border-radius:8px}
@media (prefers-reduced-motion:reduce){.s3x *,.s3x *::before{transition:none!important;animation-duration:2.5s!important}}
""" + _TERMINAL_CSS + "\n" + _icon_rules() + "\n</style>"


_PAGE_BUTTONS = (("first", "«", "The first page"), ("previous", "‹", "The page before"), ("next", "›", "The next page"),
                 ("last", "»", "The last page"))

_CHECK = ("s3x-check", "s3x-ic", "s3x-i-check")  # a row's checkbox; it gets s3x-on when ticked


class _Row:
    """
    One reusable row of the list: a full-width button (the name) under a checkbox and two labels (size, modified),
    and under the name, when the list holds everything below a folder, the folder the entry is in.
    """

    def __init__(self, widgets: Any, on_click: Callable[[_Row], None], on_check: Callable[[_Row], None]):
        self.entry: Entry | None = None
        self.button = widgets.Button(layout=widgets.Layout(width="100%"))
        self.button.add_class("s3x-row")
        self.button.on_click(lambda _: on_click(self))
        self.check = widgets.Button(description="✓", tooltip="Select it, to download several files in one .zip")
        for name in _CHECK:
            self.check.add_class(name)
        self.check.on_click(lambda _: on_check(self))
        self.where = widgets.Label(layout=widgets.Layout(display="none"))
        self.where.add_class("s3x-where")
        self.size = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.size.add_class("s3x-size")
        self.age = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.box = widgets.HBox([self.button, self.check, self.where, self.size, self.age],
                                layout=widgets.Layout(width="100%"))
        self.box.add_class("s3x-r")
