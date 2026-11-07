"""
What the messaging explorer window is drawn with: its tabs, stylesheet and colours (the OpenSearch explorer's console
look, on the windows' terminal theme), the sentences from reports reworded for the window, and the faces of its
lines (a queue, topic or rule, a hop of the map, a dead-letter queue, a queue Live watches).
"""

from __future__ import annotations

import re

from ..._kit.fmt import _count
from ..._kit.render import _CALL_RE
from ..._kit.text import _esc, _marked
from ..._kit.window import _icon_url, _terminal_theme

from ..models import Hop


_EXPLORER_TABS = (
    ("everything", "Everything", "Every queue, topic and EventBridge rule in the region: click one to open it",
     "<path d='M4 7h16M4 12h16M4 17h10'/>"),
    ("overview", "Overview", "The queue, topic or rule open: its settings, numbers and what needs attention",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("map", "Map", "Where a message goes from it, hop by hop: click a hop to open it",
     "<circle cx='5.5' cy='6' r='2'/><circle cx='18.5' cy='6' r='2'/><circle cx='12' cy='18' r='2'/>"
     "<path d='M7.5 6h9M6.7 7.7l4.2 8.6M17.3 7.7l-4.2 8.6'/>"),
    ("dead", "Dead letters", "Every dead-letter queue, the fullest first, and how to move its messages back",
     "<path d='M4 7.5 12 13l8-5.5'/><rect x='4' y='5.5' width='16' height='13' rx='2'/>"),
    ("live", "Live", "How many messages wait in a queue, looked at every few seconds while this tab is open",
     "<circle cx='12' cy='12' r='2.5'/><path d='M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4'/>"
     "<path d='M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14'/>"),
)

_ITEM_TABS = ("overview", "map")

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M4 7.5 12 13l8-5.5"/>'
         '<rect x="4" y="5.5" width="16" height="13" rx="2"/></svg>')


def _explorer_rules() -> str:
    """
    The explorer's tab icons: .mgx-i-<key> sets --mx-icon, which a tab's ::before draws.
    """
    return "\n".join(f".mgx-app .mgx-i-{key}{{--mx-icon:{_icon_url(paths)}}}" for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--mx-accent:#60a5fa;--mx-accent-2:#f472b6;--mx-soft:rgba(96,165,250,.15);--mx-ring:rgba(96,165,250,"
                  ".38);--mx-raised:rgba(255,255,255,.11);--mx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--mx-ink-bad:#f87171;--mx-ink-warn:#fbbf24;--mx-ink-ok:#34d399")

_TERMINAL_TOKENS = ("--mx-accent:var(--mgt-accent);--mx-button:var(--mgt-accent);--mx-accent-2:var(--mgt-prompt);"
                    "--mx-soft:var(--mgt-hover);--mx-ring:var(--mgt-accent);--mx-line:var(--mgt-line);"
                    "--mx-line-2:var(--mgt-line);--mx-tint:transparent;--mx-tint-2:var(--mgt-hover);--mx-bg:var(--mgt-bg);"
                    "--mx-surface:transparent;--mx-raised:var(--mgt-hover);--mx-shadow:none;--mx-ok:var(--mgt-ok);"
                    "--mx-warn:var(--mgt-warn);--mx-bad:var(--mgt-bad);--mx-ink-ok:var(--mgt-ok);"
                    "--mx-ink-warn:var(--mgt-warn);--mx-ink-bad:var(--mgt-bad)")

_MESSAGING_PINK = "#e7157b"

_TERMINAL_CSS = _terminal_theme((".mgx-app",), "mgt") + """
.mgx-app.mgx-app.mgx-app.mgx-app{""" + _TERMINAL_TOKENS + """}
.mgx-app.mgx-app{font-family:var(--mgt-mono);font-size:12.5px;border-radius:10px;box-shadow:none}
.mgx-app.mgx-app .widget-html-content,.mgx-app.mgx-app .widget-label,.mgx-app.mgx-app input,.mgx-app.mgx-app select{font-family:var(--mgt-mono)}
.mgx-app.mgx-app button.jupyter-button{font-family:var(--mgt-mono);font-size:12px;border-radius:6px;background:transparent;font-weight:400}
.mgx-app.mgx-app button.jupyter-button:hover:enabled{background:transparent;border-color:var(--mgt-accent);color:var(--mgt-accent)}
.mgx-app.mgx-app button.jupyter-button.mod-primary,.mgx-app.mgx-app button.jupyter-button.mod-primary:hover:enabled{background:transparent;border-color:var(--mgt-accent);color:var(--mgt-accent);font-weight:700;filter:none}
.mgx-app.mgx-app .widget-text input,.mgx-app.mgx-app .jupyter-widget-text input,.mgx-app.mgx-app .widget-dropdown>select,.mgx-app.mgx-app .jupyter-widget-dropdown>select{height:28px;border-radius:6px;font-size:12.5px}
.mgx-app.mgx-app .widget-text input:focus,.mgx-app.mgx-app .jupyter-widget-text input:focus,.mgx-app.mgx-app .widget-dropdown>select:focus,.mgx-app.mgx-app .jupyter-widget-dropdown>select:focus{box-shadow:none}
.mgx-app.mgx-app .widget-toggle-buttons,.mgx-app.mgx-app .jupyter-widget-toggle-buttons{padding:0;gap:4px;border-radius:0;background:transparent}
.mgx-app.mgx-app .widget-toggle-buttons .widget-toggle-button,.mgx-app.mgx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{border:1px solid var(--mgt-line);border-radius:6px;opacity:1;color:var(--mgt-dim)}
.mgx-app.mgx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.mgx-app.mgx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:transparent;border-color:var(--mgt-accent);color:var(--mgt-accent);font-weight:700;box-shadow:none}
.mgx-app .mgx-logo{width:30px;height:30px;border-radius:7px;color:""" + _MESSAGING_PINK + """;background:transparent;box-shadow:inset 0 0 0 1px var(--mgt-line)}
.mgx-app .mgx-name{font-size:14px;font-weight:700}
.mgx-app .mgx-name::before{content:"\\276F  ";color:var(--mgt-prompt)}
.mgx-app .mgx-name span{color:var(--mgt-comment);opacity:1;font-size:12px}
.mgx-app .mgx-sub{color:var(--mgt-comment);opacity:1;font-size:11.5px}
.mgx-app .mgx-stat{border-radius:6px;background:transparent;padding:4px 10px}
.mgx-app .mgx-stat .l{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--mgt-comment);opacity:1}
.mgx-app .mgx-stat .l::before{content:"# "}
.mgx-app .mgx-stat b{font-size:14px}
.mgx-app .mgx-stat.warn{border-color:var(--mgt-warn);background:transparent}
.mgx-app .mgx-stat.warn b{color:var(--mgt-warn)}
.mgx-app .mgx-stat.bad{border-color:var(--mgt-bad);background:transparent}
.mgx-app .mgx-stat.bad b{color:var(--mgt-bad)}
.mgx-app .mgx-stat.ok{border-color:var(--mgt-line)}
.mgx-app .mgx-stat.ok b{color:var(--mgt-ok)}
.mgx-app.mgx-app .mgx-trig>.mgx-trig-b{border-radius:6px;box-shadow:none}
.mgx-app.mgx-app .mgx-open .mgx-trig>.mgx-trig-b{box-shadow:none}
.mgx-app .fxl{font-size:10.5px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--mgt-comment);opacity:1}
.mgx-app .fxl::before{content:"# "}
.mgx-app .fxi,.mgx-app .opi{font-family:var(--mgt-mono);color:var(--mgt-comment);opacity:1}
.mgx-app.mgx-app .mgx-pop{border-radius:8px;box-shadow:0 10px 28px rgba(0,0,0,.18)}
.mgx-app.mgx-app .mgx-opt>.mgx-opt-b,.mgx-app.mgx-app .mgx-row>.mgx-row-b{border-radius:5px}
.mgx-app.mgx-app .mgx-opt.mgx-on>.mgx-opt-b{border-color:transparent;box-shadow:inset 2px 0 0 var(--mgt-accent)}
.mgx-app.mgx-app .mgx-tabs{gap:4px;padding:0;border-radius:0;background:transparent;border-bottom:1px solid var(--mgt-line)}
.mgx-app.mgx-app button.mgx-tab{height:30px;border-radius:0;border:0;opacity:.7;color:var(--mgt-dim)}
.mgx-app.mgx-app button.mgx-tab:hover:enabled{background:transparent;color:var(--mgt-accent);opacity:1}
.mgx-app.mgx-app button.mgx-tab.mgx-on,.mgx-app.mgx-app button.mgx-tab.mgx-on:hover:enabled{background:transparent;box-shadow:inset 0 -2px 0 var(--mgt-accent);color:var(--mgt-accent);font-weight:700}
.mgx-app.mgx-app button.mgx-small,.mgx-app.mgx-app button.mgx-chip,.mgx-app.mgx-app button.mgx-x{border-radius:6px}
.mgx-app.mgx-app button.mgx-chip.mgx-on,.mgx-app.mgx-app button.mgx-chip.mgx-on:hover:enabled{background:transparent;border-color:var(--mgt-accent);font-weight:700}
.mgx-app.mgx-app .mgx-left,.mgx-app.mgx-app .mgx-machines{border-radius:8px}
.mgx-app.mgx-app .mgx-machines{border:1px solid var(--mgt-line);padding:0 4px 4px 0;overflow:visible}
.mgx-app.mgx-app .mgx-lhead{border-bottom:1px solid var(--mgt-line);padding:6px 10px 6px 8px;margin:0 0 4px}
.mgx-app.mgx-app button.mgx-col{font-size:11px;font-weight:400;letter-spacing:0;text-transform:none;color:var(--mgt-comment);opacity:1}
.mgx-app.mgx-app button.mgx-col.mgx-c-name::before{content:"# "}
.mgx-app.mgx-app button.mgx-col:hover:enabled{background:transparent;color:var(--mgt-accent)}
.mgx-app.mgx-app button.mgx-col.mgx-on{color:var(--mgt-accent);font-weight:700}
.mgx-app.mgx-app .mgx-row.mgx-on>.mgx-row-b,.mgx-app.mgx-app .mgx-row.mgx-on>.mgx-row-b:hover:enabled{background:transparent;border-color:transparent;box-shadow:inset 2px 0 0 var(--mgt-accent)}
.mgx-app .tag,.mgx-app .ei{border-radius:4px}
.mgx-app .tag{font-weight:400;letter-spacing:0;color:var(--mgt-comment)}
.mgx-app .tag.x{background:rgba(231,21,123,.12);color:""" + _MESSAGING_PINK + """}
.mgx-app .ef,.mgx-app .ff,.mgx-app .ez,.mgx-app .fz{color:var(--mgt-comment);opacity:1}
.mgx-app .mgx-pager .widget-html-content,.mgx-app .mgx-pager .jupyter-widget-html-content{color:var(--mgt-dim);opacity:1}
.mgx-app.mgx-app .mgx-pager{border-top:0}
.mgx-app .mgx-label{font-weight:400;letter-spacing:0;text-transform:none;color:var(--mgt-comment);opacity:1}
.mgx-app .mgx-label::before{content:"# "}
.mgx-app .msg .card{border-radius:6px}
.mgx-app .msg .note,.mgx-app .msg .fi{border-radius:3px 6px 6px 3px}
.mgx-app .msg pre{border-radius:6px}
.mgx-app .mgx-status{color:var(--mgt-dim)}
.mgx-app .mgx-status .st{opacity:1}
.mgx-app .mgx-status .st:not(.warn):not(.ok):not(:has(.spin))::before{content:"\\25CF  ";color:var(--mgt-ok)}
.mgx-app .mgx-status code{border-radius:4px;color:var(--mgt-s)}
.mgx-app .mgx-hint{border-radius:6px}
.mgx-app .spin{border-top-color:var(--mgt-accent)}"""

_BASE_CSS = """.mgx-app{--mx-accent:#2563eb;--mx-button:#2563eb;--mx-accent-2:#db2777;--mx-soft:rgba(37,99,235,.10);--mx-ring:rgba(37,99,235,.28);--mx-line:rgba(127,127,127,.22);--mx-line-2:rgba(127,127,127,.36);--mx-tint:rgba(127,127,127,.06);--mx-tint-2:rgba(127,127,127,.11);--mx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--mx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--mx-raised:var(--mx-surface);--mx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--mx-ok:#10b981;--mx-warn:#f59e0b;--mx-bad:#ef4444;--mx-ink-bad:#dc2626;--mx-ink-warn:#b45309;--mx-ink-ok:#047857}
body[data-jp-theme-light="false"] .mgx-app,body.vscode-dark .mgx-app,body.vscode-high-contrast .mgx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .mgx-app{""" + _EXPLORER_DARK + """}}
.mgx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--mx-line);border-radius:18px;padding:14px 16px 10px;background:var(--mx-bg);box-shadow:var(--mx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.mgx-app *{box-sizing:border-box}
.mgx-app .widget-html-content,.mgx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.mgx-app.mgx-app .mgx-flat>*{margin:0}
.mgx-app.mgx-app button.jupyter-button{color:inherit;background:var(--mx-tint);border:1px solid var(--mx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.mgx-app.mgx-app button.jupyter-button:hover:enabled{background:var(--mx-tint-2);border-color:var(--mx-line-2);box-shadow:none}
.mgx-app.mgx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.mgx-app.mgx-app button.jupyter-button:focus-visible{outline:2px solid var(--mx-ring);outline-offset:-1px}
.mgx-app.mgx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.mgx-app.mgx-app button.jupyter-button.mod-primary{background:var(--mx-button);border-color:transparent;color:#fff;font-weight:600}
.mgx-app.mgx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--mx-button);filter:brightness(1.08)}
.mgx-app.mgx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.mgx-app.mgx-app .widget-text input,.mgx-app.mgx-app .jupyter-widget-text input,.mgx-app.mgx-app .widget-dropdown>select,.mgx-app.mgx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--mx-line-2);border-radius:9px;background-color:var(--mx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.mgx-app.mgx-app .widget-dropdown>select,.mgx-app.mgx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.mgx-app.mgx-app .widget-text input:focus,.mgx-app.mgx-app .jupyter-widget-text input:focus,.mgx-app.mgx-app .widget-dropdown>select:focus,.mgx-app.mgx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--mx-accent);box-shadow:0 0 0 3px var(--mx-soft)}
.mgx-app.mgx-app .widget-text,.mgx-app.mgx-app .widget-dropdown,.mgx-app.mgx-app .jupyter-widget-text,.mgx-app.mgx-app .jupyter-widget-dropdown{margin:0;height:auto}
.mgx-app.mgx-app .widget-toggle-buttons,.mgx-app.mgx-app .jupyter-widget-toggle-buttons{display:inline-flex;padding:3px;border-radius:11px;background:var(--mx-tint-2);gap:2px;flex:0 0 auto;margin:0}
.mgx-app.mgx-app .widget-toggle-buttons .widget-toggle-button,.mgx-app.mgx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button{margin:0;height:26px;line-height:26px;padding:0 11px;border:0;border-radius:8px;background:transparent;opacity:.72;font-size:12px;box-shadow:none;transform:none}
.mgx-app.mgx-app .widget-toggle-buttons .widget-toggle-button.mod-active,.mgx-app.mgx-app .jupyter-widget-toggle-buttons .jupyter-widget-toggle-button.mod-active{background:var(--mx-surface);opacity:1;font-weight:600;box-shadow:0 1px 3px rgba(15,23,42,.15)}
.mgx-app.mgx-app .widget-checkbox input[type=checkbox],.mgx-app.mgx-app .jupyter-widget-checkbox input[type=checkbox]{accent-color:var(--mx-accent)}
.mgx-app.mgx-app .mgx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--mx-line);overflow:visible}
.mgx-app.mgx-app .mgx-top{align-items:center;gap:12px;overflow:visible}
.mgx-app.mgx-app .mgx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.mgx-app .mgx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.mgx-app .mgx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,var(--mx-accent),var(--mx-accent-2));box-shadow:0 2px 8px var(--mx-ring);flex:0 0 auto}
.mgx-app .mgx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .mgx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.mgx-app .mgx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .mgx-stats{display:flex;flex-wrap:wrap;gap:8px}
.mgx-app .mgx-stat{border:1px solid var(--mx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--mx-tint)}
.mgx-app .mgx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.mgx-app .mgx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.mgx-app .mgx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.mgx-app .mgx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.mgx-app .mgx-stat.ok{border-color:rgba(16,185,129,.55)}
.mgx-app .mgx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--mx-tint-2) 30%,var(--mx-line) 50%,var(--mx-tint-2) 70%);animation:mgx-glow 1.3s ease-in-out infinite}
.mgx-app.mgx-app .mgx-field{position:relative;overflow:visible;flex:0 0 300px;max-width:100%}
.mgx-app.mgx-app .mgx-field.mgx-open{z-index:41}
.mgx-app.mgx-app .mgx-trig{position:relative;min-height:52px;overflow:visible}
.mgx-app.mgx-app .mgx-trig>.mgx-trig-b,.mgx-app.mgx-app .mgx-opt>.mgx-opt-b,.mgx-app.mgx-app .mgx-row>.mgx-row-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.mgx-app.mgx-app .mgx-trig>.mgx-trig-b{border-color:var(--mx-line-2);border-radius:12px;background:var(--mx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.mgx-app.mgx-app .mgx-trig>.mgx-trig-b:hover:enabled{border-color:var(--mx-accent);background:var(--mx-surface)}
.mgx-app.mgx-app .mgx-open .mgx-trig>.mgx-trig-b{border-color:var(--mx-accent);box-shadow:0 0 0 3px var(--mx-soft)}
.mgx-app.mgx-app .mgx-trig>.mgx-trig-b:active:enabled,.mgx-app.mgx-app .mgx-opt>.mgx-opt-b:active:enabled,.mgx-app.mgx-app .mgx-row>.mgx-row-b:active:enabled{transform:none}
.mgx-app.mgx-app .mgx-trig>.mgx-face,.mgx-app.mgx-app .mgx-opt>.mgx-opt-t,.mgx-app.mgx-app .mgx-row>.mgx-row-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.mgx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.mgx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.mgx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.mgx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.mgx-app .fxi,.mgx-app .opi{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.mgx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.mgx-app .mgx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--mx-accent)}
.mgx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.5)}
.mgx-app .dot.ok{background:var(--mx-ok)}
.mgx-app .dot.warn{background:var(--mx-warn)}
.mgx-app .dot.bad{background:var(--mx-bad)}
.mgx-app.mgx-app .mgx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(470px,calc(100vw - 48px));padding:8px;border:1px solid var(--mx-line-2);border-radius:14px;background:var(--mx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.mgx-app.mgx-app .mgx-pop>*{margin:0}
.mgx-app.mgx-app .mgx-pop .mgx-x{margin-left:6px}
.mgx-app.mgx-app .mgx-opts{max-height:340px;overflow:hidden auto;margin:6px 0 0}
.mgx-app.mgx-app .mgx-opts>*{flex:0 0 auto}
.mgx-app.mgx-app .mgx-opt{position:relative;margin:0 0 2px;overflow:visible}
.mgx-app.mgx-app .mgx-opt>.mgx-opt-b{border-radius:10px}
.mgx-app.mgx-app .mgx-opt>.mgx-opt-b:hover:enabled{background:var(--mx-tint-2)}
.mgx-app.mgx-app .mgx-opt.mgx-on>.mgx-opt-b{background:var(--mx-soft);border-color:var(--mx-ring)}
.mgx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.mgx-app .op .dot{margin-top:6px}
.mgx-app .opb{flex:1 1 auto;min-width:0}
.mgx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.mgx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 0 auto;max-width:100%}
.mgx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--mx-line);line-height:1.4}
.mgx-app .opf.warn{opacity:1;color:var(--mx-ink-warn)}
.mgx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.mgx-app.mgx-app .mgx-backdrop,.mgx-app.mgx-app .mgx-backdrop:hover:enabled,.mgx-app.mgx-app .mgx-backdrop:active:enabled,.mgx-app.mgx-app .mgx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.mgx-app.mgx-app .mgx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--mx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.mgx-app.mgx-app button.mgx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.mgx-app.mgx-app button.mgx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--mx-icon) center/contain no-repeat;mask:var(--mx-icon) center/contain no-repeat}
.mgx-app.mgx-app button.mgx-tab:hover:enabled{background:var(--mx-tint);opacity:.95}
.mgx-app.mgx-app button.mgx-tab.mgx-on,.mgx-app.mgx-app button.mgx-tab.mgx-on:hover:enabled{background:var(--mx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.mgx-app.mgx-app button.mgx-tab.mgx-on::before{background:var(--mx-accent)}
.mgx-app.mgx-app button.mgx-tab.mgx-alert::after,.mgx-app.mgx-app button.mgx-tab.mgx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--mx-warn);flex:0 0 auto}
.mgx-app.mgx-app button.mgx-tab.mgx-alarm::after{background:var(--mx-bad)}
@container (max-width:520px){.mgx-app.mgx-app button.mgx-tab::before{display:none}}
.mgx-app.mgx-app .mgx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden auto;padding:2px 6px 2px 2px}
body[class*=vscode-] .mgx-app.mgx-app .mgx-page{height:620px}
.mgx-app.mgx-app .mgx-page>*{flex:0 0 auto}
.mgx-app.mgx-app .mgx-page.mgx-split{overflow:hidden;gap:14px;align-items:stretch;padding:0}
.mgx-app.mgx-app .mgx-left{flex:0 0 390px;min-width:300px;max-width:46%;border:1px solid var(--mx-line);border-radius:14px;padding:10px 10px 6px;background:var(--mx-surface);overflow:hidden}
.mgx-app.mgx-app .mgx-left>*{margin:0;flex:0 0 auto}
.mgx-app.mgx-app .mgx-left,.mgx-app.mgx-app .mgx-right,.mgx-app.mgx-app .mgx-rows{min-height:0}
@media (max-width:900px){.mgx-app.mgx-app .mgx-page.mgx-split{flex-wrap:wrap;overflow:hidden auto}.mgx-app.mgx-app .mgx-left{flex:1 1 100%;max-width:100%;height:440px}.mgx-app.mgx-app .mgx-right{flex:1 1 100%;overflow:visible}}
.mgx-app.mgx-app .mgx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.mgx-app.mgx-app .mgx-right>*{flex:0 0 auto;margin:0}
.mgx-app.mgx-app .mgx-find{position:relative;align-items:center}
.mgx-app.mgx-app .mgx-find input,.mgx-app.mgx-app .mgx-find input:focus{padding-left:32px;background:var(--mx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.mgx-app.mgx-app .mgx-chips{flex-wrap:wrap;gap:6px;margin:8px 0 0}
.mgx-app.mgx-app .mgx-chips>*{margin:0}
.mgx-app.mgx-app button.mgx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--mx-line);display:inline-flex;align-items:center;gap:6px}
.mgx-app.mgx-app button.mgx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.mgx-app.mgx-app button.mgx-chip.mgx-t-all::before{display:none}
.mgx-app.mgx-app button.mgx-chip.mgx-t-bad::before{background:var(--mx-bad)}
.mgx-app.mgx-app button.mgx-chip.mgx-t-warn::before{background:var(--mx-warn)}
.mgx-app.mgx-app button.mgx-chip.mgx-t-ok::before{background:var(--mx-ok)}
.mgx-app.mgx-app button.mgx-chip.mgx-on,.mgx-app.mgx-app button.mgx-chip.mgx-on:hover:enabled{background:var(--mx-soft);border-color:var(--mx-ring);color:var(--mx-accent);font-weight:650}
.mgx-app.mgx-app .mgx-filters{gap:6px;margin:8px 0 0;align-items:center}
.mgx-app.mgx-app .mgx-filters>*{margin:0}
.mgx-app.mgx-app .mgx-rows{flex:1 1 auto;overflow:hidden auto;margin:8px -4px 0;padding:0 4px}
.mgx-app.mgx-app .mgx-rows>*{flex:0 0 auto}
.mgx-app.mgx-app .mgx-row{position:relative;height:50px;margin:0 0 2px;overflow:visible}
.mgx-app.mgx-app .mgx-row>.mgx-row-b{border-radius:10px}
.mgx-app.mgx-app .mgx-row>.mgx-row-b:hover:enabled{background:var(--mx-tint-2)}
.mgx-app.mgx-app .mgx-row.mgx-on>.mgx-row-b,.mgx-app.mgx-app .mgx-row.mgx-on>.mgx-row-b:hover:enabled{background:var(--mx-soft);border-color:var(--mx-ring)}
.mgx-app .fr{display:flex;align-items:center;gap:11px;height:50px;padding:0 10px 0 8px;min-width:0}
.mgx-app .fm{flex:1 1 auto;min-width:0;line-height:1.3}
.mgx-app .fn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .ff{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.mgx-app .sl{font-weight:600}
.mgx-app .sl.bad{color:var(--mx-ink-bad)}
.mgx-app .sl.warn{color:var(--mx-ink-warn)}
.mgx-app .sl.ok{color:var(--mx-ink-ok)}
.mgx-app .fz{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.6;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.mgx-app .mgx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.mgx-app.mgx-app .mgx-pager{align-items:center;gap:2px;padding:6px 2px 2px;border-top:1px solid var(--mx-line);margin-top:4px}
.mgx-app.mgx-app .mgx-pager>*{margin:0}
.mgx-app.mgx-app button.mgx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.mgx-app .mgx-pager .widget-html-content,.mgx-app .mgx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app.mgx-app .mgx-bar{position:sticky;top:0;z-index:3;gap:8px;align-items:center;padding:0 0 10px;margin:0 0 4px;background:var(--mx-bg);border-bottom:1px solid var(--mx-line);flex-wrap:wrap}
.mgx-app.mgx-app .mgx-bar>*{margin:0}
.mgx-app.mgx-app button.mgx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px}
.mgx-app.mgx-app button.mgx-ghost{background:transparent;border-color:transparent}
.mgx-app.mgx-app button.mgx-ghost:hover:enabled{background:var(--mx-tint-2)}
.mgx-app.mgx-app button.mgx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6}
.mgx-app.mgx-app button.mgx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--mx-ink-bad)}
.mgx-app .mgx-label{font-size:11px;font-weight:600;opacity:.6;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}
.mgx-app .mgx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.mgx-app .mgx-status .st{opacity:.72}
.mgx-app .mgx-status .st.warn{opacity:1;color:var(--mx-ink-warn)}
.mgx-app .mgx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.mgx-app .mgx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--mx-ok)}
.mgx-app .mgx-status code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;padding:1px 5px;border-radius:5px;background:var(--mx-tint-2);user-select:all;-webkit-user-select:all}
.mgx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--mx-accent);border-radius:50%;animation:mgx-spin .8s linear infinite}
@keyframes mgx-spin{to{transform:rotate(360deg)}}
.mgx-app .skw{padding:6px 2px}
.mgx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.mgx-app .sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--mx-tint) 30%,var(--mx-tint-2) 50%,var(--mx-tint) 70%);animation:mgx-glow 1.3s ease-in-out infinite}
.mgx-app .sk.t{height:18px;width:42%;margin-bottom:16px}
.mgx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.mgx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes mgx-glow{from{background-position:100% 0}to{background-position:0 0}}
.mgx-app .mgx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--mx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.mgx-app .mgx-hint b{font-weight:650}
@media (prefers-reduced-motion:reduce){.mgx-app *,.mgx-app *::before{transition:none!important;animation-duration:2.5s!important}}
.mgx-app.mgx-app .mgx-row{height:52px}
.mgx-app .er{display:flex;align-items:center;gap:11px;height:52px;padding:0 10px 0 8px;min-width:0}
.mgx-app .ei{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:var(--mx-tint-2)}
.mgx-app .ei.ok{background:rgba(16,185,129,.15);color:var(--mx-ink-ok)}
.mgx-app .ei.bad{background:rgba(239,68,68,.14);color:var(--mx-ink-bad)}
.mgx-app .ei.warn{background:rgba(245,158,11,.16);color:var(--mx-ink-warn)}
.mgx-app .ei.run{background:rgba(59,130,246,.15);color:var(--mx-accent)}
.mgx-app .em{flex:1 1 auto;min-width:0;line-height:1.3}
.mgx-app .en{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .ef{font-size:11.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.mgx-app .ez{flex:0 0 auto;text-align:right;font-size:11.5px;opacity:.65;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.mgx-app .tag{display:inline-block;font-size:10px;font-weight:650;letter-spacing:.03em;padding:0 6px;border-radius:5px;background:var(--mx-tint-2);margin-left:6px;vertical-align:1px}
.mgx-app .tag.x{background:rgba(219,39,119,.12);color:var(--mx-accent-2)}
.mgx-app.mgx-app .mgx-lhead{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;padding:4px 10px 4px 8px;border-bottom:1px solid var(--mx-line)}
.mgx-app.mgx-app .mgx-lhead>*{margin:0;min-width:0}
.mgx-app.mgx-app button.mgx-col{width:auto;height:24px;padding:0;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app.mgx-app button.mgx-col.mgx-c-name{text-align:left;justify-content:flex-start}
.mgx-app.mgx-app button.mgx-col:hover:enabled{background:transparent;opacity:1;color:var(--mx-accent)}
.mgx-app.mgx-app button.mgx-col.mgx-on{opacity:1;color:var(--mx-accent)}
.mgx-app .mr{display:grid;grid-template-columns:22px minmax(220px,1.8fr) repeat(4,minmax(80px,.6fr));gap:10px;align-items:center;height:52px;padding:0 10px 0 8px;min-width:0}
.mgx-app .mr .num{text-align:right;font-variant-numeric:tabular-nums}
.mgx-app .mr .num.bad{color:var(--mx-ink-bad);font-weight:650}
.mgx-app .mr .num.warn{color:var(--mx-ink-warn);font-weight:650}
.mgx-app.mgx-app .mgx-machines{overflow:hidden auto}
.mgx-app.mgx-app .mgx-machines>*{flex:0 0 auto}
.mgx-app .msg h3 .badge{display:none}
.mgx-app .msg h3{font-size:17px;margin:6px 0 2px}
.mgx-app .msg .card{border-radius:12px;background:var(--mx-tint);border-color:var(--mx-line)}
.mgx-app .msg .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.mgx-app .msg .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.mgx-app .msg .card.ok{border-color:rgba(16,185,129,.55)}
.mgx-app .msg .note,.mgx-app .msg .fi{border-radius:4px 10px 10px 4px}
.mgx-app .msg pre{border-radius:10px}
"""

_OWN_CSS = """
.mgx-app .fz b{display:block;font-weight:600;opacity:1}
.mgx-app .fz.warn b{color:var(--mx-ink-warn)}
.mgx-app.mgx-app .mgx-items .mgx-row,.mgx-app.mgx-app .mgx-hops .mgx-row,.mgx-app.mgx-app .mgx-dead .mgx-row{height:52px}
.mgx-app.mgx-app .mgx-hops .mgx-row{height:44px}
.mgx-app .hr{display:flex;align-items:center;gap:10px;height:44px;padding:0 10px 0 8px;min-width:0}
.mgx-app .hb{flex:0 0 auto;white-space:pre;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;opacity:.5}
.mgx-app .hr .em{flex:1 1 auto}
.mgx-app .hp{flex:0 0 auto;max-width:42%;text-align:right;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mgx-app .hp.bad{color:var(--mx-ink-bad);font-weight:650}
.mgx-app .hp.warn{color:var(--mx-ink-warn);font-weight:650}
.mgx-app .hr.up .hb{opacity:.8;color:var(--mx-accent)}
.mgx-app .mgx-sep{font-size:11px;opacity:.6;padding:10px 8px 4px;text-transform:uppercase;letter-spacing:.04em}
.mgx-app .lv{padding:10px 12px;border:1px solid var(--mx-line);border-radius:8px;margin:0 0 8px}
.mgx-app .lv .lh{display:flex;align-items:baseline;gap:10px}
.mgx-app .lv .lh b{font-size:14px}
.mgx-app .lv .lt{font-size:12px;opacity:.75}
.mgx-app .lv .lt.ok{color:var(--mx-ink-ok);opacity:1}
.mgx-app .lv .lt.warn{color:var(--mx-ink-warn);opacity:1}
.mgx-app .lv .spark{display:flex;align-items:flex-end;gap:2px;height:38px;margin:8px 0 4px}
.mgx-app .lv .spark i{display:block;flex:1 1 0;min-width:3px;max-width:14px;background:var(--mx-accent);opacity:.75;border-radius:2px 2px 0 0}
.mgx-app .lv .ln{font-size:11.5px;opacity:.7;font-variant-numeric:tabular-nums}
.mgx-app.mgx-app button.mgx-small.mgx-on{border-color:var(--mx-accent);color:var(--mx-accent);font-weight:700}
.mgx-app.mgx-app button.mgx-tab.mgx-onair::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--mx-bad);flex:0 0 auto}
"""

_EXPLORER_CSS = "<style>\n" + _BASE_CSS + _OWN_CSS + _TERMINAL_CSS + "\n" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {
    "overview": "the Everything tab",
    "queue_info": "the Overview tab",
    "topic_info": "the Overview tab",
    "rule_info": "the Overview tab",
    "follow": "the Map tab",
    "dead_letters": "the Dead letters tab",
}

_FIRST_TEXT_RE = re.compile(r"\(\s*['\"]([^'\"]+)['\"]")

_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(t)(?=he [\w ]+ tab)")

_SAME_TAB_RE = re.compile(r"(the [\w ]+? tab)(?:, \1)* and \1\b")


def _window_text(text: str) -> str:
    """
    A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab,
    with the queue, topic or rule it names (queue_info('orders-dlq') -> "the Overview tab of orders-dlq"), the
    same tab said once where a sentence lists it again, capitalised where it starts a sentence. Calls on other Views and AWS CLI commands stay.
    """

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        if call.startswith(".") or name not in _IN_WINDOW:
            return call
        named = _FIRST_TEXT_RE.search(call)
        return f"{_IN_WINDOW[name]} of {named.group(1)}" if named else _IN_WINDOW[name]

    swapped = _SAME_TAB_RE.sub(r"\1", _CALL_RE.sub(swap, str(text or "")))
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), swapped)


def _tag(kind: str) -> str:
    label = {"queue": "QUEUE", "dlq": "DEAD-LETTER", "topic": "TOPIC", "rule": "RULE"}.get(kind, kind.upper())
    return f'<span class="tag{" x" if kind == "dlq" else ""}">{_esc(label)}</span>'


def _number_cell(value: int | None, tone: str = "") -> str:
    return f'<span class="num {tone}">{_esc(_count(value))}</span>'


def _item_face(*, kind: str, name: str, note: str, tone: str, traffic: int | None, failed: int | None,  # noqa: PLR0913
               waiting: int | None, problems: int, words: list[str]) -> str:
    """
    A queue's, topic's or rule's line in the Everything tab: a dot for how it is, its name and kind, what it is in a
    few words, then what came in over the window (sent, published or matched), what failed, how many messages wait,
    and its problems.
    """
    failed_tone = "bad" if failed else ""
    waiting_tone = "bad" if kind == "dlq" and waiting else ""
    return (f'<div class="mr"><span class="dot {tone}"></span><div class="em"><div class="en">'
            f'{_marked(name, words)}{_tag(kind)}</div><div class="ef">{_esc(note)}</div></div>'
            f'{_number_cell(traffic)}{_number_cell(failed, failed_tone)}{_number_cell(waiting, waiting_tone)}'
            f'<span class="num {"warn" if problems else ""}">{problems:,}</span></div>')


def _hop_face(hop: Hop) -> str:
    """
    A hop of the Map tab: its place in the tree (↑ for what sends to where the map starts), a dot, its name, its
    kind, how a message gets there and its numbers, and what's wrong there.
    """
    lead = "↑ " if hop.upstream else hop.branch
    under = " · ".join(filter(None, [hop.kind, hop.via, hop.numbers]))
    return (f'<div class="hr{" up" if hop.upstream else ""}"><span class="hb">{_esc(lead)}</span>'
            f'<span class="dot {hop.tone or "ok"}"></span><div class="em"><div class="en">{_esc(hop.name)}</div>'
            f'<div class="ef">{_esc(under)}</div></div><span class="hp {hop.tone}">{_esc(hop.problem)}</span></div>')


def _dead_face(*, name: str, messages: int, oldest: str, catches: str, words: list[str]) -> str:
    """
    A dead-letter queue's line in the Dead letters tab: red while it holds messages, what it catches failures from,
    and how many it holds and how old the oldest is.
    """
    tone = "bad" if messages else "ok"
    return (f'<div class="fr"><span class="dot {tone}"></span><div class="fm"><div class="fn">'
            f'{_marked(name, words)}</div><div class="ff">{_esc(catches)}</div></div>'
            f'<div class="fz {"warn" if messages else ""}"><b>{messages:,} waiting</b>{_esc(oldest)}</div></div>')
