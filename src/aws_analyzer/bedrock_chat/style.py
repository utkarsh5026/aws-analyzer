"""
The look of Bedrock chat reports and its window: the side tabs' rules, the theme, the CSS root class,
stylesheet and badge.
"""

from __future__ import annotations

from typing import Any

from .._bedrock.blocks import _download_css
from .._kit.render import _base_css, _report_html
from .._kit.window import _icon_url


# The window's side tabs, in order: each title, and the line drawing (24 x 24) shown before it as a mask in the text's
# colour, so it follows the theme like the S3 explorer's icons. Under 480px of tab bar the titles stand alone.
_TABS = (
    ("Settings", "<path d='M4 7.5h9M17.5 7.5H20M4 16.5h2.5M11 16.5h9'/><circle cx='15' cy='7.5' r='2.5'/>"
                 "<circle cx='8.5' cy='16.5' r='2.5'/>"),
    ("Test", "<path d='M9 3h6M10 3v6L4.6 18.4A1.8 1.8 0 0 0 6.2 21h11.6a1.8 1.8 0 0 0 1.6-2.6L14 9V3'/>"
             "<path d='M7.2 15h9.6'/>"),
    ("Runs", "<path d='M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5'/><path d='M3.5 3.5v5h5'/><path d='M12 7.5V12l3 2'/>"),
    ("Code", "<path d='M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14'/>"),
    ("Request", "<path d='M7 17L17 7M8.5 7H17v8.5'/>"),
    ("Response", "<path d='M17 7L7 17M15.5 17H7V8.5'/>"),
)

_TAB_TITLES = [title for title, _ in _TABS]


def _tab_rules() -> str:
    """
    The side tabs as one row that never wraps: equal-weight titles in a bar, the picked one raised, each with its
    icon while the bar is 480px wide or more (a container query), tighter under 360px. For both ipywidgets 8's (lm-)
    and 7's (p-) class names.
    """
    rules, narrow, tight = [], [], []
    for x in ("lm", "p"):
        bar = f".kbc-app.kbc-app .kbc-side>.{x}-TabBar"
        tab = f"{bar} .{x}-TabBar-tab"
        rules += [
            f"{bar}{{container-type:inline-size;padding:3px;border-radius:12px;background:var(--kc-tint-2);"
            "min-height:0;border:0;overflow:visible;margin:0 0 10px}",
            f"{bar}>.{x}-TabBar-content{{gap:2px;border:0;align-items:stretch;flex-wrap:nowrap}}",
            f"{tab}{{flex:1 1 auto;min-width:0;min-height:30px;line-height:30px;margin:0;padding:0 8px;border:0;"
            "border-radius:9px;font-size:12px;background:transparent;color:inherit;opacity:.72;font-weight:500;"
            "transform:none;justify-content:center;align-items:center;gap:6px;cursor:pointer;"
            "transition:background-color .15s,opacity .15s}",
            f"{tab}:hover:not(.{x}-mod-current){{background:var(--kc-tint);opacity:.95}}",
            f"{tab}.{x}-mod-current{{background:var(--kc-raised);opacity:1;font-weight:600;min-height:30px;"
            "transform:none;box-shadow:0 1px 3px rgba(15,23,42,.16)}",
            f"{tab}.{x}-mod-current::before{{display:none}}",
            f"{tab}:focus{{outline:none}}",
            f"{tab}:focus-visible{{outline:2px solid var(--kc-ring);outline-offset:-2px}}",
            f"{tab} .{x}-TabBar-tabLabel{{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;"
            "white-space:nowrap}",
            f"{tab} .{x}-TabBar-tabIcon{{flex:0 0 auto;width:14px;height:14px;background:currentColor;"
            "-webkit-mask:var(--kc-icon) center/contain no-repeat;mask:var(--kc-icon) center/contain no-repeat}",
            f"{tab}.{x}-mod-current .{x}-TabBar-tabIcon{{background:var(--kc-accent)}}",
        ]
        rules += [f"{tab}:nth-child({k}) .{x}-TabBar-tabIcon{{--kc-icon:{_icon_url(paths)}}}"
                  for k, (_, paths) in enumerate(_TABS, 1)]
        narrow.append(f"{tab} .{x}-TabBar-tabIcon{{display:none}}")
        tight.append(f"{tab}{{padding:0 5px}}")
    return "\n".join([*rules, f"@container (max-width:479px){{{''.join(narrow)}}}",
                      f"@container (max-width:359px){{{''.join(tight)}}}"])


_ROOT = "kbc"  # the class around each report's HTML: its stylesheet's rules are scoped to it

# Custom properties the chat's rules use (its window's too), light and dark.
_THEME_CSS = """\
.kbc,.kbc-app{--kc-solid:#2563eb;--kc-accent:#2563eb;--kc-accent-2:#7c3aed;--kc-soft:rgba(37,99,235,.11);--kc-ring:rgba(37,99,235,.28);--kc-line:rgba(127,127,127,.22);--kc-line-2:rgba(127,127,127,.36);--kc-tint:rgba(127,127,127,.06);--kc-tint-2:rgba(127,127,127,.11);--kc-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--kc-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--kc-shadow:0 1px 2px rgba(15,23,42,.06),0 4px 14px rgba(15,23,42,.06);--kc-cite:rgba(59,130,246,.11);--kc-raised:var(--kc-surface)}
.kbc{--kk:#7c3aed;--ks:#15803d;--kn:#b45309;--kl:#1d4ed8;--kf:#0e7490;--ka:#c2410c;--kw:#be185d}
body[data-jp-theme-light="false"] .kbc,body[data-jp-theme-light="false"] .kbc-app,body.vscode-dark .kbc,body.vscode-dark .kbc-app,body.vscode-high-contrast .kbc,body.vscode-high-contrast .kbc-app,.kbc-dark .kbc,.kbc-dark .kbc-app{--kc-solid:#2563eb;--kc-accent:#60a5fa;--kc-accent-2:#a78bfa;--kc-soft:rgba(96,165,250,.15);--kc-ring:rgba(96,165,250,.35);--kc-shadow:0 1px 2px rgba(0,0,0,.35),0 4px 14px rgba(0,0,0,.25);--kc-cite:rgba(96,165,250,.16);--kc-raised:rgba(255,255,255,.12);--kk:#c4b5fd;--ks:#86efac;--kn:#fcd34d;--kl:#93c5fd;--kf:#67e8f9;--ka:#fdba74;--kw:#f9a8d4}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .kbc,body:not([data-jp-theme-light]):not(.vscode-light) .kbc-app{--kc-solid:#2563eb;--kc-accent:#60a5fa;--kc-accent-2:#a78bfa;--kc-soft:rgba(96,165,250,.15);--kc-ring:rgba(96,165,250,.35);--kc-shadow:0 1px 2px rgba(0,0,0,.35),0 4px 14px rgba(0,0,0,.25);--kc-cite:rgba(96,165,250,.16);--kc-raised:rgba(255,255,255,.12);--kk:#c4b5fd;--ks:#86efac;--kn:#fcd34d;--kl:#93c5fd;--kf:#67e8f9;--ka:#fdba74;--kw:#f9a8d4}}"""

# The chat's reports match its window: rounder cards, badges and pills, and colours from the theme.
_RESTYLE = {
    "h3 .badge{": '.kbc h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:var(--kc-soft);color:var(--kc-accent)}',
    ".card{": '.kbc .card{border:1px solid var(--kc-line);border-radius:12px;padding:7px 13px;min-width:96px;background:var(--kc-tint)}',
    ".track{display:": '.kbc .track{display:inline-block;width:110px;height:8px;border-radius:4px;background:rgba(127,127,127,.18)}',
    ".fill{": '.kbc .fill{display:block;height:100%;border-radius:4px;background:var(--kc-accent)}',
    ".pill{": '.kbc .pill{display:inline-block;padding:0 8px;border-radius:999px;font-weight:600;font-size:12px}',
    ".note{": '.kbc .note{padding:7px 12px;margin:5px 0;border-left:3px solid var(--kc-accent);border-radius:4px 10px 10px 4px;background:var(--kc-soft)}',
    ".note.warn{": '.kbc .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.11)}',
    ".note.ok{": '.kbc .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.11)}',
    ".chip{": '.kbc .chip{display:inline-block;padding:0 8px;border-radius:999px;font-size:11px;font-weight:600;line-height:18px}',
    ".chip.info{": '.kbc .chip.info{background:var(--kc-soft);box-shadow:inset 0 0 0 1px var(--kc-ring)}',
    ".fi{border-left:": '.kbc .fi{border-left:3px solid var(--kc-accent);background:var(--kc-soft);border-radius:4px 10px 10px 4px}',
    "code{font-family:": '.kbc code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;padding:1px 5px;border-radius:5px}',
    "code{background:": '.kbc code{background:var(--kc-tint-2);user-select:all;-webkit-user-select:all;cursor:text}',
    "pre{": '.kbc pre{max-height:420px;overflow:auto;padding:9px 12px;border:1px solid var(--kc-line);border-radius:10px;font-size:12px;background:var(--kc-tint)}',
}

_EXTRA_CSS = """\
.kbc mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.kbc .ans{font-size:14px;line-height:1.6;margin:8px 0 10px;max-width:900px;overflow-wrap:anywhere}
.kbc .ans>:first-child{margin-top:0}
.kbc .ans>:last-child{margin-bottom:0}
.kbc .ans p{margin:0 0 .65em}
.kbc .ans h1,.kbc .ans h2,.kbc .ans h3,.kbc .ans h4,.kbc .ans h5,.kbc .ans h6{margin:.95em 0 .4em;line-height:1.3;font-weight:650}
.kbc .ans h1{font-size:1.32em}
.kbc .ans h2{font-size:1.2em}
.kbc .ans h3{font-size:1.08em}
.kbc .ans h4,.kbc .ans h5,.kbc .ans h6{font-size:1em}
.kbc .ans ul,.kbc .ans ol{margin:.25em 0 .65em;padding-left:1.45em}
.kbc .ans li{margin:.18em 0}
.kbc .ans li>p{margin:0 0 .35em}
.kbc .ans li>ul,.kbc .ans li>ol{margin:.15em 0 .2em}
.kbc .ans blockquote{margin:.4em 0 .75em;padding:.15em 0 .15em .9em;border-left:3px solid var(--kc-line-2);opacity:.88}
.kbc .ans hr{border:0;border-top:1px solid var(--kc-line);margin:1em 0}
.kbc .ans code{font-size:.86em;user-select:text;-webkit-user-select:text}
.kbc .ans a{color:var(--kc-accent);text-decoration:none;border-bottom:1px solid var(--kc-ring)}
.kbc .ans a:hover{border-bottom-color:currentColor}
.kbc .ans .pre{position:relative;margin:.45em 0 .8em}
.kbc .ans .pre pre{margin:0;padding:11px 14px;white-space:pre;overflow:auto;max-height:420px;font-size:12.5px;line-height:1.55;user-select:all;-webkit-user-select:all;cursor:text}
.kbc .ans .pre code{background:none;padding:0;font-size:inherit;user-select:inherit;-webkit-user-select:inherit}
.kbc .ans .pre .lang{position:absolute;top:6px;right:12px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;opacity:.5}
.kbc .ans .mdt{overflow-x:auto;margin:.45em 0 .8em;border:1px solid var(--kc-line);border-radius:10px}
.kbc .ans table{border-collapse:collapse;font-size:13px;line-height:1.45;width:100%}
.kbc .ans th,.kbc .ans td{border-bottom:1px solid var(--kc-line);padding:6px 11px;text-align:left;vertical-align:top}
.kbc .ans th{background:var(--kc-tint);font-weight:600}
.kbc .ans tbody tr:last-child td{border-bottom:0}
.kbc .ans th.c,.kbc .ans td.c{text-align:center}
.kbc .ans th.r,.kbc .ans td.r{text-align:right;font-variant-numeric:tabular-nums}
.kbc .ans .cite{background:var(--kc-cite);border-radius:3px;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.kbc .ans sup{font-size:10px;font-weight:650;color:var(--kc-accent);margin-left:1px;line-height:0}
.kbc .ans .none{font-style:italic;opacity:.6}
.kbc .json{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.55;padding:9px 12px 9px 26px;border:1px solid var(--kc-line);border-radius:12px;overflow:auto;max-height:560px;white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 8px;background:var(--kc-tint)}
.kbc .json details>summary{cursor:pointer;list-style:none;position:relative;display:block}
.kbc .json details>summary::-webkit-details-marker{display:none}
.kbc .json details>summary::before{content:"\\25B8";position:absolute;left:-14px;top:0;opacity:.5;font-size:11px}
.kbc .json details[open]>summary::before{content:"\\25BE"}
.kbc .json details>summary:hover::before{opacity:1;color:var(--kc-accent)}
.kbc .json details[open]>summary .jx{display:none}
.kbc .json .ji{padding-left:18px;margin-left:1px;border-left:1px dotted rgba(127,127,127,.35)}
.kbc .jk{color:var(--kk)}
.kbc .js{color:var(--ks)}
.kbc .jn{color:var(--kn)}
.kbc .jl{color:var(--kl);font-weight:600}
.kbc .pk{color:var(--kw);font-weight:600}
.kbc .pf{color:var(--kf)}
.kbc .pa{color:var(--ka)}
.kbc .pc{opacity:.55;font-style:italic}
.kbc .json .jx{opacity:.55}
.kbc .json .jm{background:rgba(250,204,21,.30);border-radius:4px;box-shadow:0 0 0 2px rgba(250,204,21,.30)}
.kbc .json .jc{margin-left:10px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:11px;font-style:italic;opacity:.55;white-space:nowrap}
.kbc .msg{max-width:min(820px,92%);padding:10px 14px;border-radius:18px;margin:4px 0;box-sizing:border-box}
.kbc .msg.you{margin-left:auto;width:fit-content;background:var(--kc-solid);color:#fff;border-bottom-right-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13.5px;line-height:1.5;box-shadow:0 1px 2px rgba(15,23,42,.12)}
.kbc .ask{display:flex;justify-content:flex-end;align-items:flex-end;gap:8px}
.kbc .ask .msg.you{margin-left:0}
.kbc .av.me{width:26px;height:26px;border-radius:9px;font-size:14px;margin:0 0 4px;background:var(--kc-tint-2)}
.kbc .msg.bot{margin-right:auto;background:var(--kc-surface);border:1px solid var(--kc-line);border-bottom-left-radius:6px;box-shadow:var(--kc-shadow)}
.kbc .msg.err{border-color:rgba(239,68,68,.55);background:rgba(239,68,68,.07)}
.kbc .msg.sys{margin:8px auto;width:fit-content;max-width:90%;text-align:center;font-size:12px;opacity:.75;padding:4px 12px;border-radius:999px;background:var(--kc-tint-2);border:0;box-shadow:none}
.kbc .msg .ans{margin:4px 0 2px;font-size:13.5px}
.kbc .msg .note{margin:8px 0 2px;font-size:12px}
.kbc .who{display:flex;align-items:center;font-size:11.5px;line-height:1.35;margin-bottom:6px}
.kbc .who b{font-weight:650;font-size:12.5px}
.kbc .who .wm{opacity:.6;font-size:11px}
.kbc .who .av{width:26px;height:26px;border-radius:9px;font-size:13px;margin-right:9px}
.kbc .av{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;margin-right:7px;border-radius:7px;font-size:11px;color:#fff;background:linear-gradient(135deg,var(--kc-accent),var(--kc-accent-2));flex:0 0 auto}
.kbc .wait{display:flex;align-items:center;opacity:.85;width:fit-content}
.kbc .dots{display:inline-flex;gap:4px;margin-right:10px}
.kbc .dots i{width:6px;height:6px;border-radius:50%;background:var(--kc-accent);opacity:.3;animation:kbc-dot 1.2s infinite ease-in-out}
.kbc .dots i:nth-child(2){animation-delay:.15s}
.kbc .dots i:nth-child(3){animation-delay:.3s}
@keyframes kbc-dot{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
.kbc .srcs{margin-top:10px;padding-top:8px;border-top:1px solid var(--kc-line);font-size:12px}
.kbc .srcs .sh{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55;margin:0 0 4px}
.kbc details.src{border-radius:9px;margin:2px 0}
.kbc details.src>summary{cursor:pointer;display:flex;align-items:center;gap:7px;white-space:nowrap;overflow:hidden;padding:3px 6px;border-radius:9px;list-style:none}
.kbc details.src>summary::-webkit-details-marker{display:none}
.kbc details.src>summary:hover{background:var(--kc-tint-2)}
.kbc details.src[open]{background:var(--kc-tint)}
.kbc details.src .sx{flex:0 0 auto;min-width:18px;height:18px;padding:0 4px;box-sizing:border-box;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:650;color:var(--kc-accent);background:var(--kc-soft)}
.kbc details.src .sf{flex:0 0 auto;font-weight:600}
.kbc details.src .sn{opacity:.6;overflow:hidden;text-overflow:ellipsis;min-width:0}
.kbc details.src[open]>summary .sn{display:none}
.kbc details.src .pt{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 8px 6px 31px;padding:8px 12px;border-left:3px solid var(--kc-ring);border-radius:3px 9px 9px 3px;background:var(--kc-surface);max-height:280px;overflow:auto;line-height:1.5}
.kbc details.src .pm{margin:0 8px 6px 31px;opacity:.6;overflow-wrap:anywhere}
.kbc details.src .pmeta{display:grid;grid-template-columns:minmax(0,max-content) minmax(0,1fr);gap:2px 12px;margin:0 8px 8px 31px;padding:6px 10px;border-radius:9px;background:var(--kc-surface);font-size:11.5px}
.kbc details.src .pmeta .pmh{grid-column:1/-1;font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.kbc details.src .pmeta .mk{font-weight:600;overflow-wrap:anywhere}
.kbc details.src .pmeta .mv{overflow-wrap:anywhere;white-space:pre-wrap}
.kbc details.src .pmeta .mb{opacity:.6}
.kbc details.src .pmeta .mn{grid-column:1/-1;opacity:.6}
.kbc .srcs.hits{margin-top:2px;padding-top:0;border-top:0}
.kbc .hits details.src>summary{flex-wrap:wrap;row-gap:2px;white-space:normal;padding:4px 6px}
.kbc .hits details.src .sf{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.kbc .hits details.src .sn{flex:1 1 100%;margin-left:25px;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;line-height:1.45}
.kbc .sc{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-variant-numeric:tabular-nums;opacity:.7}
.kbc .sc .sb{display:inline-block;width:34px;height:5px;border-radius:3px;background:var(--kc-tint-2);overflow:hidden}
.kbc .sc .sb i{display:block;height:100%;border-radius:3px;background:var(--kc-accent)}
.kbc .ct{flex:0 0 auto;font-size:10.5px;font-weight:650;padding:0 7px;border-radius:999px;color:var(--kc-accent);background:var(--kc-soft)}
.kbc details.raw{margin-top:8px;font-size:12px}
.kbc details.raw>summary{cursor:pointer;opacity:.65;width:fit-content;padding:2px 8px 2px 4px;border-radius:7px}
.kbc details.raw>summary:hover{opacity:1;background:var(--kc-tint-2)}
.kbc .jh{font-size:11px;font-weight:600;opacity:.6;margin:8px 0 0}
.kbc .caret::after{content:"\\258D";opacity:.6;color:var(--kc-accent);animation:kbc-blink 1s steps(1) infinite}
@keyframes kbc-blink{50%{opacity:0}}
.kbc .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--kc-accent);border-radius:50%;animation:kbc-spin .8s linear infinite}
@keyframes kbc-spin{to{transform:rotate(360deg)}}
.kbc .hello{display:flex;gap:10px;padding:10px 14px;border:1px solid var(--kc-line);border-radius:14px;margin:4px 0;line-height:1.5;background:var(--kc-surface);box-shadow:var(--kc-shadow)}
.kbc .hello .av{width:26px;height:26px;border-radius:9px;font-size:13px;margin:0}
.kbc details.tour{margin-top:4px;font-size:12px}
.kbc details.tour>summary{cursor:pointer;opacity:.65;width:fit-content;padding:2px 8px 2px 4px;border-radius:7px}
.kbc details.tour>summary:hover{opacity:1;background:var(--kc-tint-2)}
.kbc details.tour ul{margin:4px 0 2px;padding-left:18px}
.kbc .hello li{margin:2px 0}
.kbc .st{font-size:12px;opacity:.7;padding:6px 6px 0;line-height:1.4}
.kbc .st.warn{opacity:1;color:#d97706}
.kbc .st.ok{opacity:.85}
.kbc .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.kbc .st.ok::before{content:"\\2713";margin-right:6px;color:#10b981}
.kbc .gh{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;opacity:.5;margin:10px 2px 4px;line-height:1.4}
.kbc .ph{font-weight:650;font-size:12px;margin:2px 0 0;line-height:1.4}
.kbc .ph .hint{margin-left:6px}
.kbc .pd{font-size:11px;opacity:.6;margin:1px 0 4px;line-height:1.45}
.kbc .rh{display:flex;align-items:baseline;gap:7px;min-width:0;line-height:1.3;cursor:help}
.kbc .rh b{font-weight:600;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbc .rk{font-size:10px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbc .rp{font-size:11px;line-height:1.4;opacity:.7;margin:4px 0 0;overflow-wrap:anywhere}
.kbc .rp code{font-size:10px}
.kbc .rp.bad{opacity:1;color:#dc2626}
.kbc .rp.pending{opacity:.9;color:#d97706}
.kbc .rp.off{font-style:italic}
.kbc .fc{min-width:0;line-height:1.35}
.kbc .fc .fl{display:flex;align-items:baseline;gap:7px;min-width:0}
.kbc .fc .fl b{font-weight:600;font-size:12px;white-space:nowrap}
.kbc .fc .fd{font-size:11px;opacity:.65;margin:2px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.kbc .fc .fw{font-size:10px;opacity:.4;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.kbc details.setup{margin-top:6px;font-size:11px;line-height:1.4}
.kbc details.setup>summary{cursor:pointer;width:fit-content;font-weight:600;font-size:12px;padding:2px 8px 2px 4px;border-radius:7px}
.kbc details.setup>summary:hover{background:var(--kc-tint-2)}
.kbc pre{word-break:normal}
.kbc details.setup pre{margin:6px 0 0}
.kbc .whd{display:flex;align-items:center;gap:12px;min-width:0}
.kbc .whd .av{width:36px;height:36px;border-radius:12px;font-size:18px;margin:0;box-shadow:0 2px 8px var(--kc-ring)}
.kbc .whd h3{margin:0;font-size:17px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbc .whd h3 .badge{margin:0 0 0 8px}
.kbc .whd .sub{margin:2px 0 0}
.kbc .fx{position:relative;padding:6px 34px 6px 12px;min-width:0;line-height:1.3}
.kbc .fbl{font-size:12px;opacity:.65;margin-right:2px;white-space:nowrap}
.kbc .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55;white-space:nowrap}
.kbc .fxv{display:flex;align-items:center;gap:7px;min-width:0;margin-top:2px;font-size:13px;white-space:nowrap}
.kbc .fxv b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.kbc .fxv b.fxe{font-weight:400;opacity:.5}
.kbc .fxb{font-size:11.5px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.kbc .fxb.id,.kbc .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px}
.kbc .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.kbc-open .kbc .chev{transform:rotate(225deg);margin-top:-2px;opacity:.85;color:var(--kc-accent)}
.kbc .dot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.55)}
.kbc .dot.ok{background:#10b981}
.kbc .dot.warn{background:#f59e0b}
.kbc .dot.bad{background:#ef4444}
.kbc .dot.none{background:transparent}
.kbc .op{display:flex;align-items:center;gap:10px;padding:7px 10px;min-width:0;line-height:1.35}
.kbc .op .dot{align-self:flex-start;margin-top:6px}
.kbc .opb{flex:1 1 auto;min-width:0}
.kbc .opt{display:flex;align-items:baseline;gap:8px;min-width:0;white-space:nowrap}
.kbc .opt b{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.kbc .opi{opacity:.6;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.kbc .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kbc .op mark{padding:0}
.kbc .opk{flex:0 0 auto;width:16px;height:16px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;line-height:1;color:var(--kc-accent)}
.kbc .op.on .opk::after{content:"\\2713"}
.kbc .op.multi .opk{border:1.5px solid var(--kc-line-2);border-radius:5px}
.kbc .op.multi.on .opk{background:var(--kc-accent);border-color:var(--kc-accent);color:#fff}
.kbc .opf{font-size:11px;opacity:.6;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--kc-line);line-height:1.4}
.kbc .opf.warn{opacity:1;color:#d97706}
.kbc .opf.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.kbc .bqs{margin:6px 0 10px}
.kbc details.bq,.kbc .bq.wait{border:1px solid var(--kc-line);border-radius:12px;margin:0 0 6px;background:var(--kc-surface)}
.kbc details.bq>summary,.kbc .bq.wait{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;padding:7px 10px;line-height:1.35}
.kbc details.bq>summary{cursor:pointer;list-style:none;border-radius:11px}
.kbc details.bq>summary::-webkit-details-marker{display:none}
.kbc details.bq>summary:hover{background:var(--kc-tint)}
.kbc details.bq[open]>summary{border-bottom:1px solid var(--kc-line);border-radius:11px 11px 0 0}
.kbc details.bq.ok{border-left:3px solid rgba(16,185,129,.7)}
.kbc details.bq.warn{border-left:3px solid rgba(245,158,11,.85)}
.kbc details.bq.bad{border-left:3px solid rgba(239,68,68,.8)}
.kbc .bq .bn{flex:0 0 auto;min-width:20px;height:20px;padding:0 5px;box-sizing:border-box;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:650;color:var(--kc-accent);background:var(--kc-soft)}
.kbc .bq .bt{flex:1 1 260px;min-width:0;font-weight:600;overflow-wrap:anywhere}
.kbc .bq .bx{display:inline-flex;align-items:center;flex-wrap:wrap;gap:4px 8px;margin-left:auto}
.kbc .bqs.narrow .bq .bx{flex:1 1 100%;margin-left:28px}
.kbc .bq .pill{font-size:11px;white-space:nowrap}
.kbc .bq .pill:not(.ok):not(.warn):not(.bad){background:var(--kc-tint-2)}
.kbc .bq .bm{font-size:11px;opacity:.62;white-space:nowrap;font-variant-numeric:tabular-nums}
.kbc .bq .bc{font-size:10.5px;font-weight:650;white-space:nowrap;padding:0 7px;border-radius:999px}
.kbc .bq .bc.up{color:#059669;background:rgba(16,185,129,.13)}
.kbc .bq .bc.down{color:#dc2626;background:rgba(239,68,68,.11)}
.kbc .bq .bb{padding:8px 12px 10px}
.kbc .bq .bb>.msg{max-width:none;margin:0;padding:0;border:0;border-radius:0;box-shadow:none;background:transparent}
.kbc .bq .be{font-size:12px;opacity:.75;margin:0 0 6px}
.kbc .bq.wait{opacity:.72}
.kbc .bq.wait .spin{margin:0}
.kbc .tests .cards{gap:6px;margin:8px 0 6px}
.kbc .tests .card{padding:5px 10px;min-width:64px;border-radius:10px}
.kbc .tests .card .v{font-size:13.5px}
.kbc .tests table.t td:first-child:not(.s):not(.n){min-width:170px}
.kbc-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--kc-line);border-radius:20px;padding:14px 16px 12px;background:var(--kc-bg);box-shadow:var(--kc-shadow);gap:0}
.kbc-app *{box-sizing:border-box}
.kbc-app .widget-html-content,.kbc-app .jupyter-widget-html-content{min-width:0}
.kbc-app.kbc-app .kbc-head{padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--kc-line);gap:10px 14px}
.kbc-app.kbc-app .kbc-pickers{gap:8px 10px;align-items:flex-start}
.kbc-app.kbc-app .widget-text input,.kbc-app.kbc-app .widget-textarea textarea,.kbc-app.kbc-app .widget-dropdown>select,.kbc-app.kbc-app .jupyter-widget-text input,.kbc-app.kbc-app .jupyter-widget-textarea textarea,.kbc-app.kbc-app .jupyter-widget-dropdown>select{border:1px solid var(--kc-line-2);border-radius:10px;background-color:var(--kc-surface);color:inherit;transition:border-color .15s,box-shadow .15s}
.kbc-app.kbc-app .widget-text input,.kbc-app.kbc-app .jupyter-widget-text input{padding:4px 11px}
.kbc-app.kbc-app .widget-dropdown>select,.kbc-app.kbc-app .jupyter-widget-dropdown>select{padding:0 26px 0 11px;cursor:pointer}
.kbc-app.kbc-app .widget-textarea textarea,.kbc-app.kbc-app .jupyter-widget-textarea textarea{padding:7px 11px;line-height:1.45;resize:vertical}
.kbc-app.kbc-app .widget-text input:focus,.kbc-app.kbc-app .widget-textarea textarea:focus,.kbc-app.kbc-app .widget-dropdown>select:focus,.kbc-app.kbc-app .jupyter-widget-text input:focus,.kbc-app.kbc-app .jupyter-widget-textarea textarea:focus,.kbc-app.kbc-app .jupyter-widget-dropdown>select:focus{border-color:var(--kc-accent);box-shadow:0 0 0 3px var(--kc-soft)}
.kbc-app.kbc-app .widget-text,.kbc-app.kbc-app .widget-dropdown,.kbc-app.kbc-app .widget-textarea{margin:2px 0}
.kbc-app.kbc-app .kbc-mono textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.5}
.kbc-app.kbc-app .kbc-side{--jp-widgets-font-size:12px;--jp-widgets-inline-height:26px}
.kbc-app.kbc-app .kbc-side .kbc-mono textarea,.kbc-app.kbc-app .kbc-side .kbc .json,.kbc-app.kbc-app .kbc-side .kbc pre{font-size:11.5px}
.kbc-app.kbc-app .jupyter-button{border-radius:10px;border:1px solid var(--kc-line);background:var(--kc-tint);color:inherit;font-weight:500;box-shadow:none;outline:none;transition:background-color .15s,border-color .15s,box-shadow .15s,transform .05s}
.kbc-app.kbc-app .jupyter-button:hover:enabled{background:var(--kc-tint-2);border-color:var(--kc-line-2);box-shadow:none}
.kbc-app.kbc-app .jupyter-button:focus-visible{box-shadow:0 0 0 3px var(--kc-soft);outline:none}
.kbc-app.kbc-app .jupyter-button:active:enabled{transform:translateY(1px)}
.kbc-app.kbc-app .jupyter-button.mod-primary{background:var(--kc-solid);border-color:transparent;color:#fff;font-weight:600}
.kbc-app.kbc-app .jupyter-button.mod-primary:hover:enabled{background:var(--kc-solid);filter:brightness(1.08);border-color:transparent}
.kbc-app.kbc-app .jupyter-button:disabled{opacity:.45;cursor:default}
.kbc-app.kbc-app .kbc-ghost{background:transparent;border-color:transparent}
.kbc-app.kbc-app .kbc-ghost:hover:enabled{background:var(--kc-tint-2)}
.kbc-app.kbc-app .kbc-new-chat{border-radius:999px;padding:0 14px}
.kbc-app.kbc-app .kbc-chip{height:24px;line-height:22px;font-size:11.5px;padding:0 10px;margin:0 5px 5px 0;border-radius:999px;background:var(--kc-surface);border:1px dashed var(--kc-line-2)}
.kbc-app.kbc-app .kbc-chip:hover:enabled{border-style:solid;border-color:var(--kc-accent);color:var(--kc-accent);background:var(--kc-soft)}
.kbc-app.kbc-app .kbc-x{padding:0;min-width:22px;height:22px;line-height:20px;font-size:11px;border-radius:999px;background:transparent;border-color:transparent;opacity:.55}
.kbc-app.kbc-app .kbc-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:#dc2626}
.kbc-app.kbc-app .kbc-small{height:24px;line-height:22px;font-size:11.5px;padding:0 11px;border-radius:999px}
.kbc-app.kbc-app .kbc-files{gap:6px}
.kbc-app.kbc-app .kbc-file{height:26px;line-height:24px;font-size:12px;padding:0 10px;margin:0;border-radius:999px;background:var(--kc-soft);border:1px solid var(--kc-accent);color:var(--kc-accent)}
.kbc-app.kbc-app .kbc-file:hover:enabled{border-color:#dc2626;color:#dc2626;background:rgba(239,68,68,.08)}
.kbc-app.kbc-app .kbc-log{height:clamp(540px,calc(100vh - 400px),1400px);border:1px solid var(--kc-line);border-radius:18px;padding:12px 14px;background:var(--kc-tint)}
body[class*=vscode-] .kbc-app.kbc-app .kbc-log{height:540px}
.kbc-app.kbc-app .kbc-composer{margin-top:10px;padding:5px 5px 5px 8px;border:1px solid var(--kc-line-2);border-radius:999px;background:var(--kc-surface);box-shadow:var(--kc-shadow);align-items:center;transition:border-color .15s,box-shadow .15s}
.kbc-app.kbc-app .kbc-composer:focus-within{border-color:var(--kc-accent);box-shadow:0 0 0 3px var(--kc-soft)}
.kbc-app.kbc-app .kbc-composer .widget-text input,.kbc-app.kbc-app .kbc-composer .jupyter-widget-text input{border:0;box-shadow:none;background:transparent;font-size:14px;padding:4px 8px}
.kbc-app.kbc-app .kbc-composer .jupyter-button{border-radius:999px;height:34px;line-height:34px;padding:0 18px}
.kbc-app.kbc-app .kbc-top{gap:8px}
.kbc-app.kbc-app .kbc-mode{border-radius:999px;margin:0}
.kbc-app.kbc-app .kbc-mode .widget-toggle-button,.kbc-app.kbc-app .kbc-mode .jupyter-widget-toggle-button{height:28px;line-height:28px;padding:0 12px;border-radius:999px}
.kbc-app.kbc-app .kbc-toggle{background:transparent;opacity:.6}
.kbc-app.kbc-app .kbc-toggle.kbc-on{background:var(--kc-soft);border-color:var(--kc-accent);color:var(--kc-accent);opacity:1}
.kbc-app.kbc-app .kbc-row{border:1px solid transparent;border-top-color:var(--kc-line);border-radius:0;padding:6px 2px 7px 6px;margin:0;background:transparent;transition:background-color .2s,border-color .2s,box-shadow .2s}
.kbc-app.kbc-app .kbc-row:hover{background:var(--kc-tint)}
.kbc-app.kbc-app .kbc-row .rp{margin-top:1px}
.kbc-app.kbc-app .kbc-row.kbc-fresh{border-color:var(--kc-accent);border-radius:10px;box-shadow:0 0 0 2px var(--kc-soft)}
.kbc-app.kbc-app .kbc-row.kbc-broken{border-color:rgba(220,38,38,.6);border-radius:10px}
.kbc-app.kbc-app .kbc-row.kbc-pending{border-style:dashed;border-color:rgba(217,119,6,.7);border-radius:10px}
.kbc-app.kbc-app .kbc-row.kbc-off{opacity:.55}
.kbc-app.kbc-app .kbc-add{height:30px;border:1px dashed var(--kc-line-2);border-radius:10px;background:transparent;font-size:12px;opacity:.8}
.kbc-app.kbc-app .kbc-add:hover:enabled{opacity:1;border-style:solid;border-color:var(--kc-accent);color:var(--kc-accent);background:var(--kc-soft)}
.kbc-app.kbc-app .kbc-foot{margin-top:14px;padding:10px 0 0 2px;border-top:1px solid var(--kc-line)}
.kbc-app.kbc-app .kbc-search{margin-top:4px}
.kbc-app.kbc-app .kbc-pick{align-items:center;gap:10px;padding:6px 8px 6px 10px;border-radius:10px;border:1px solid transparent;margin:0 0 2px}
.kbc-app.kbc-app .kbc-pick:hover{background:var(--kc-tint);border-color:var(--kc-line)}
.kbc-app.kbc-app .kbc-pick .jupyter-button{flex:0 0 auto}
.kbc-app.kbc-app .kbc-results{margin:4px 0 2px}
.kbc-app.kbc-app .kbc-card{border:1px solid var(--kc-line);border-radius:12px;padding:9px 11px;margin:8px 0 0;background:var(--kc-tint)}
.kbc-app.kbc-app .widget-checkbox input[type=checkbox],.kbc-app.kbc-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--kc-accent);width:15px;height:15px}
.kbc-app.kbc-app .widget-toggle-buttons,.kbc-app.kbc-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:12px;background:var(--kc-tint-2);gap:2px;flex:0 0 auto}
.kbc-app.kbc-app .widget-toggle-buttons .widget-toggle-button,.kbc-app.kbc-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:24px;line-height:24px;border:0;border-radius:9px;background:transparent;opacity:.72;font-size:11.5px;box-shadow:none;transform:none}
.kbc-app.kbc-app .widget-toggle-buttons .widget-toggle-button.mod-active,.kbc-app.kbc-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--kc-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.kbc-app.kbc-app .widget-toggle-buttons .widget-toggle-button:hover:enabled{opacity:1;background:var(--kc-tint)}
.kbc-app.kbc-app .widget-toggle-buttons .widget-toggle-button.mod-active:hover:enabled{background:var(--kc-surface)}
.kbc-app.kbc-app .widget-toggle-buttons .widget-toggle-button:disabled{opacity:.4}
.kbc-app.kbc-app .kbc-side>.widget-tab-contents,.kbc-app.kbc-app .kbc-side>.jupyter-widget-tab-contents{border:1px solid var(--kc-line);border-radius:16px;padding:10px 4px 10px 12px;background:var(--kc-surface);overflow:hidden}
.kbc-app.kbc-app .kbc-side>.widget-tab-contents>.widget-box,.kbc-app.kbc-app .kbc-side>.jupyter-widget-tab-contents>.jupyter-widget-box{max-height:clamp(620px,calc(100vh - 320px),1480px);overflow:hidden auto;padding-right:8px}
body[class*=vscode-] .kbc-app.kbc-app .kbc-side>.widget-tab-contents>.widget-box,body[class*=vscode-] .kbc-app.kbc-app .kbc-side>.jupyter-widget-tab-contents>.jupyter-widget-box{max-height:620px}
.kbc-app.kbc-app .kbc-side>.widget-tab-contents>.widget-box>*,.kbc-app.kbc-app .kbc-side>.jupyter-widget-tab-contents>.jupyter-widget-box>*{flex-shrink:0}
.kbc-app.kbc-app .kbc-head,.kbc-app.kbc-app .kbc-pickers,.kbc-app.kbc-app .kbc-field{overflow:visible}
.kbc-app.kbc-app .kbc-field{position:relative;margin:0}
.kbc-app.kbc-app .kbc-field.kbc-open{z-index:41}
.kbc-app.kbc-app .kbc-backdrop,.kbc-app.kbc-app .kbc-backdrop:hover:enabled,.kbc-app.kbc-app .kbc-backdrop:active:enabled,.kbc-app.kbc-app .kbc-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.kbc-app.kbc-app .kbc-file-bar{gap:6px;margin:10px 0 0}
.kbc-app.kbc-app .kbc-trig{position:relative;min-height:46px;margin:0;overflow:visible}
.kbc-app.kbc-app .kbc-trig>.kbc-trig-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid var(--kc-line-2);border-radius:12px;background:var(--kc-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.kbc-app.kbc-app .kbc-trig>.kbc-trig-b:hover:enabled{border-color:var(--kc-accent);background:var(--kc-surface)}
.kbc-app.kbc-app .kbc-open .kbc-trig>.kbc-trig-b{border-color:var(--kc-accent);box-shadow:0 0 0 3px var(--kc-soft)}
.kbc-app.kbc-app .kbc-trig>.kbc-trig-b:disabled{opacity:1}
.kbc-app.kbc-app .kbc-trig>.kbc-face,.kbc-app.kbc-app .kbc-opt>.kbc-opt-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;height:auto}
.kbc-app.kbc-app .kbc-field.kbc-off{opacity:.5}
.kbc-app.kbc-app .kbc-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:100%;min-width:340px;max-width:min(560px,calc(100vw - 48px));padding:8px;border:1px solid var(--kc-line-2);border-radius:14px;background:var(--kc-bg);box-shadow:0 14px 36px rgba(15,23,42,.2),0 3px 8px rgba(15,23,42,.08);overflow:visible;--jp-widgets-inline-height:34px}
.kbc-app.kbc-app .kbc-pop .kbc-x{margin-left:6px}
.kbc-app.kbc-app .kbc-pop .kbc-list{margin-left:6px;height:30px;flex:0 0 auto}
.kbc-app .kbc-saved .kbc .dlw{margin:6px 2px 0;font-size:12px}
.kbc-app .kbc a.dl{color:var(--kc-accent);border-color:var(--kc-ring)}
.kbc-app .kbc a.dl:hover{background:var(--kc-soft)}
.kbc-app.kbc-app .kbc-find input,.kbc-app.kbc-app .kbc-find input:focus{padding:4px 11px 4px 32px;border-radius:10px;background:var(--kc-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.kbc-app.kbc-app .kbc-opts{max-height:336px;overflow:hidden auto;margin:6px 0 0;padding:0 2px 0 0}
.kbc-app.kbc-app .kbc-opts>*{flex:0 0 auto}
.kbc-app.kbc-app .kbc-opt{position:relative;margin:0 0 2px;overflow:visible}
.kbc-app.kbc-app .kbc-opt>.kbc-opt-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;border-radius:10px;background:transparent;box-shadow:none}
.kbc-app.kbc-app .kbc-opt>.kbc-opt-b:hover:enabled{background:var(--kc-tint-2);border-color:transparent}
.kbc-app.kbc-app .kbc-opt.kbc-on>.kbc-opt-b,.kbc-app.kbc-app .kbc-opt.kbc-on>.kbc-opt-b:hover:enabled{background:var(--kc-soft);border-color:var(--kc-ring)}
.kbc-app.kbc-app .kbc-trig>.kbc-trig-b:active:enabled,.kbc-app.kbc-app .kbc-opt>.kbc-opt-b:active:enabled{transform:none}
.kbc-app.kbc-app .noUi-connect{background:var(--kc-accent)}
.kbc-app.kbc-app .noUi-handle{border-radius:50%;border-color:var(--kc-accent)}
""" + _tab_rules()

# Highlighted code to copy (_Code), next to the shared rules for <pre>.
_PRE_HL = '.kbc pre.hl{white-space:pre;overflow-wrap:normal;word-break:normal;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;line-height:1.55;tab-size:4}'

_CSS = _base_css(_ROOT, _EXTRA_CSS + "\n" + _download_css(_ROOT), theme=_THEME_CSS, restyle=_RESTYLE,
                 after={"pre.code{": _PRE_HL})

_BADGE = "💬 Bedrock chat"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """
    A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws.
    """
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)
